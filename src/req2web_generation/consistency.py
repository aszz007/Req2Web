from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

from .renderer import (
    RENDER_MANIFEST_SCHEMA_VERSION,
    SUPPORTED_COMPONENT_TYPES,
    RenderResult,
)
from .recovery import match_error_recovery_constraint
from .schema import PAGE_SPEC_SCHEMA_VERSION, PageSpec


CONSISTENCY_REPORT_SCHEMA_VERSION = "req2web.consistency.report.v1"
CHECK_STATUSES = ("pass", "fail", "warning")
_REQUIRED_FILES = ("index.html", "styles.css", "app.js", "render_manifest.json")
_MANIFEST_FILES = ("index.html", "styles.css", "app.js")
_CHECKER_SIDECARS = {"consistency_report.json"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NETWORK_URL_RE = re.compile(r"(?i)(?:https?:)?//[a-z0-9]")
_CSS_NETWORK_RE = re.compile(
    r"(?is)(?:@import\s+(?:url\()?\s*['\"]?(?:https?:)?//|"
    r"url\(\s*['\"]?(?:https?:)?//)"
)
_JS_NETWORK_API_RE = re.compile(
    r"(?i)\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|importScripts)\s*\("
)
_JS_INNER_HTML_RE = re.compile(r"\binnerHTML\b")
_PAGE_DATA_PREFIX = "const PAGE_DATA = Object.freeze("


@dataclass(frozen=True)
class ConsistencyCheck:
    check_id: str
    category: str
    status: Literal["pass", "fail", "warning"]
    message: str
    related_ids: list[str]


@dataclass
class ConsistencyReport:
    page_id: str
    passed: bool
    summary: dict[str, int]
    checks: list[ConsistencyCheck]
    warnings: list[str]
    schema_version: str = CONSISTENCY_REPORT_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != CONSISTENCY_REPORT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported consistency report schema: {self.schema_version}"
            )
        if not isinstance(self.page_id, str) or not self.page_id.strip():
            raise ValueError("consistency report page_id must not be empty")
        if not self.checks:
            raise ValueError("consistency report checks must not be empty")

        check_ids = [item.check_id for item in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("consistency report check_ids must be unique")
        for item in self.checks:
            if not item.check_id.strip() or not item.category.strip():
                raise ValueError("consistency check identity must not be empty")
            if item.status not in CHECK_STATUSES:
                raise ValueError(f"unsupported consistency status: {item.status}")
            if not item.message.strip():
                raise ValueError("consistency check message must not be empty")
            if len(item.related_ids) != len(set(item.related_ids)):
                raise ValueError(
                    f"consistency check {item.check_id} related_ids must be unique"
                )
            if any(
                not isinstance(value, str) or not value.strip()
                for value in item.related_ids
            ):
                raise ValueError(
                    f"consistency check {item.check_id} has an invalid related_id"
                )

        counts = Counter(item.status for item in self.checks)
        expected_summary = {
            "total": len(self.checks),
            "pass": counts["pass"],
            "fail": counts["fail"],
            "warning": counts["warning"],
        }
        if self.summary != expected_summary:
            raise ValueError("consistency report summary does not match checks")
        if self.passed != (counts["fail"] == 0):
            raise ValueError("consistency report passed must mean no fail checks")
        expected_warnings = [
            item.message for item in self.checks if item.status == "warning"
        ]
        if self.warnings != expected_warnings:
            raise ValueError("consistency report warnings do not match warning checks")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


class _RenderedHTMLParser(HTMLParser):
    _URL_ATTRIBUTES = {
        "action",
        "formaction",
        "href",
        "poster",
        "src",
        "srcset",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_schema_values: list[str] = []
        self.body_page_ids: list[str] = []
        self.section_ids: list[str] = []
        self.components: dict[str, list[dict[str, str | None]]] = defaultdict(list)
        self.external_references: list[str] = []
        self.texts: dict[str, list[list[str]]] = {
            "title": [],
            "h1": [],
            "summary": [],
        }
        self._active_texts: list[tuple[str, int]] = []
        self._section_stack: list[str | None] = []
        self._stack: list[tuple[str, bool, list[tuple[str, int]]]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {name: value for name, value in attrs}
        entered_section = False
        if tag == "html" and attributes.get("data-page-spec-schema") is not None:
            self.html_schema_values.append(attributes["data-page-spec-schema"] or "")
        if tag == "body" and attributes.get("data-page-id") is not None:
            self.body_page_ids.append(attributes["data-page-id"] or "")
        if tag == "section":
            section_id = attributes.get("data-section-id")
            self._section_stack.append(section_id)
            entered_section = True
            if section_id is not None:
                self.section_ids.append(section_id)

        component_id = attributes.get("data-component-id")
        if component_id is not None:
            self.components[component_id].append(
                {
                    "section_id": (
                        self._section_stack[-1] if self._section_stack else None
                    ),
                    "component_type": attributes.get("data-component-type"),
                    "renderer_kind": attributes.get("data-renderer-kind"),
                    "hidden": "true" if "hidden" in attributes else None,
                }
            )

        captures: list[tuple[str, int]] = []
        classes = set((attributes.get("class") or "").split())
        capture_name = None
        if tag == "title":
            capture_name = "title"
        elif tag == "h1":
            capture_name = "h1"
        elif "summary" in classes:
            capture_name = "summary"
        if capture_name is not None:
            self.texts[capture_name].append([])
            capture = (capture_name, len(self.texts[capture_name]) - 1)
            self._active_texts.append(capture)
            captures.append(capture)

        for name, value in attrs:
            if name in self._URL_ATTRIBUTES and value:
                candidates = value.split(",") if name == "srcset" else [value]
                for candidate in candidates:
                    reference = candidate.strip().split()[0]
                    if _NETWORK_URL_RE.search(reference):
                        self.external_references.append(reference)

        self._stack.append((tag, entered_section, captures))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        while self._stack:
            opened_tag, entered_section, captures = self._stack.pop()
            for capture in captures:
                if capture in self._active_texts:
                    self._active_texts.remove(capture)
            if entered_section and self._section_stack:
                self._section_stack.pop()
            if opened_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        for name, index in self._active_texts:
            self.texts[name][index].append(data)

    def rendered_texts(self, name: str) -> list[str]:
        return ["".join(parts) for parts in self.texts[name]]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _extract_page_data(script: str) -> tuple[dict[str, Any] | None, int, int, str | None]:
    if script.count(_PAGE_DATA_PREFIX) != 1:
        return None, -1, -1, "PAGE_DATA declaration must appear exactly once"
    marker = script.find(_PAGE_DATA_PREFIX)
    if marker < 0:
        return None, -1, -1, "PAGE_DATA declaration was not found"
    payload_start = marker + len(_PAGE_DATA_PREFIX)
    try:
        payload, consumed = json.JSONDecoder().raw_decode(script[payload_start:])
    except json.JSONDecodeError:
        return None, -1, -1, "PAGE_DATA is not valid deterministic JSON"
    payload_end = payload_start + consumed
    if not script[payload_end:].lstrip().startswith(");"):
        return None, -1, -1, "PAGE_DATA declaration has an invalid closing boundary"
    if not isinstance(payload, dict):
        return None, -1, -1, "PAGE_DATA must be a JSON object"
    return payload, payload_start, payload_end, None


class MinimalConsistencyChecker:
    """Check deterministic structural fidelity between PageSpec and static output."""

    def check(
        self,
        page_spec: PageSpec,
        render_result: RenderResult,
    ) -> ConsistencyReport:
        if not isinstance(page_spec, PageSpec):
            raise TypeError("MinimalConsistencyChecker page_spec must be a PageSpec")
        if not isinstance(render_result, RenderResult):
            raise TypeError("MinimalConsistencyChecker render_result must be a RenderResult")
        page_spec.validate()

        output_value = render_result.output_dir
        if not isinstance(output_value, (str, os.PathLike)) or not str(output_value):
            raise ValueError("MinimalConsistencyChecker cannot determine output_dir")
        try:
            output_dir = Path(output_value).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ValueError(
                "MinimalConsistencyChecker cannot determine output_dir"
            ) from exc

        checks: list[ConsistencyCheck] = []

        def add(
            check_id: str,
            category: str,
            status: Literal["pass", "fail", "warning"],
            message: str,
            related_ids: list[str] | tuple[str, ...] = (),
        ) -> None:
            checks.append(
                ConsistencyCheck(
                    check_id=check_id,
                    category=category,
                    status=status,
                    message=message,
                    related_ids=list(
                        dict.fromkeys(
                            value
                            for value in related_ids
                            if isinstance(value, str) and value.strip()
                        )
                    ),
                )
            )

        add(
            "input.page-spec",
            "input_identity",
            "pass",
            "PageSpec validates against req2web.page_spec.v1.",
            [page_spec.page_id],
        )
        page_id_matches = render_result.page_id == page_spec.page_id
        add(
            "input.render-result-page-id",
            "input_identity",
            "pass" if page_id_matches else "fail",
            (
                "RenderResult page_id matches PageSpec."
                if page_id_matches
                else "RenderResult page_id does not match PageSpec."
            ),
            [page_spec.page_id, render_result.page_id],
        )

        result_paths: dict[str, Path | None] = {}
        path_fields = {
            "index.html": render_result.index_html,
            "styles.css": render_result.styles_css,
            "app.js": render_result.app_js,
            "render_manifest.json": render_result.render_manifest,
        }
        path_contract_ok = True
        for filename, value in path_fields.items():
            try:
                path = Path(value).expanduser().resolve(strict=False)
            except (OSError, RuntimeError, TypeError, ValueError):
                path = None
            result_paths[filename] = path
            if (
                path is None
                or not _is_within(path, output_dir)
                or path != output_dir / filename
            ):
                path_contract_ok = False
        add(
            "input.output-paths",
            "input_identity",
            "pass" if path_contract_ok else "fail",
            (
                "All RenderResult file paths are canonical files under output_dir."
                if path_contract_ok
                else "One or more RenderResult file paths are outside output_dir or non-canonical."
            ),
            list(_REQUIRED_FILES),
        )

        file_bytes: dict[str, bytes] = {}
        file_text: dict[str, str] = {}
        for filename in _REQUIRED_FILES:
            path = output_dir / filename
            exists = path.is_file()
            add(
                f"file.required:{filename}",
                "file_integrity",
                "pass" if exists else "fail",
                (
                    f"Required file {filename} exists."
                    if exists
                    else f"Required file {filename} is missing."
                ),
                [filename],
            )
            if not exists:
                continue
            try:
                content = path.read_bytes()
                text = content.decode("utf-8")
            except (OSError, UnicodeError):
                add(
                    f"file.utf8-readable:{filename}",
                    "file_integrity",
                    "fail",
                    f"Required file {filename} could not be read as UTF-8.",
                    [filename],
                )
                continue
            file_bytes[filename] = content
            file_text[filename] = text
            add(
                f"file.utf8-readable:{filename}",
                "file_integrity",
                "pass",
                f"Required file {filename} is readable UTF-8.",
                [filename],
            )

        manifest: dict[str, Any] | None = None
        manifest_text = file_text.get("render_manifest.json")
        if manifest_text is not None:
            try:
                parsed_manifest = json.loads(manifest_text)
                if isinstance(parsed_manifest, dict):
                    manifest = parsed_manifest
            except json.JSONDecodeError:
                manifest = None
        add(
            "manifest.json-object",
            "input_identity",
            "pass" if manifest is not None else "fail",
            (
                "Render manifest is a valid JSON object."
                if manifest is not None
                else "Render manifest is missing or is not a valid JSON object."
            ),
            ["render_manifest.json"],
        )

        manifest_entries: dict[str, str] = {}
        if manifest is not None:
            manifest_keys_ok = set(manifest) == {
                "files",
                "page_id",
                "page_spec_schema_version",
                "schema_version",
            }
            add(
                "manifest.fields",
                "input_identity",
                "pass" if manifest_keys_ok else "fail",
                (
                    "Render manifest fields match the v1 contract."
                    if manifest_keys_ok
                    else "Render manifest fields do not match the v1 contract."
                ),
                ["render_manifest.json"],
            )
            schema_ok = (
                manifest.get("schema_version") == RENDER_MANIFEST_SCHEMA_VERSION
            )
            add(
                "manifest.schema-version",
                "input_identity",
                "pass" if schema_ok else "fail",
                (
                    "Render manifest schema version is correct."
                    if schema_ok
                    else "Render manifest schema version is incorrect."
                ),
                [RENDER_MANIFEST_SCHEMA_VERSION],
            )
            page_schema_ok = (
                manifest.get("page_spec_schema_version")
                == PAGE_SPEC_SCHEMA_VERSION
            )
            add(
                "manifest.page-spec-schema-version",
                "input_identity",
                "pass" if page_schema_ok else "fail",
                (
                    "Render manifest identifies req2web.page_spec.v1."
                    if page_schema_ok
                    else "Render manifest PageSpec schema version is incorrect."
                ),
                [PAGE_SPEC_SCHEMA_VERSION],
            )
            manifest_page_ok = manifest.get("page_id") == page_spec.page_id
            add(
                "manifest.page-id",
                "input_identity",
                "pass" if manifest_page_ok else "fail",
                (
                    "Render manifest page_id matches PageSpec."
                    if manifest_page_ok
                    else "Render manifest page_id does not match PageSpec."
                ),
                [page_spec.page_id],
            )

            entries = manifest.get("files")
            entries_ok = isinstance(entries, list)
            names: list[str] = []
            if entries_ok:
                for entry in entries:
                    if not isinstance(entry, dict) or set(entry) != {"name", "sha256"}:
                        entries_ok = False
                        continue
                    name = entry.get("name")
                    digest = entry.get("sha256")
                    if (
                        not isinstance(name, str)
                        or name not in _MANIFEST_FILES
                        or Path(name).name != name
                        or not isinstance(digest, str)
                        or not _SHA256_RE.fullmatch(digest)
                    ):
                        entries_ok = False
                        continue
                    names.append(name)
                    if name not in manifest_entries:
                        manifest_entries[name] = digest
            if names != list(_MANIFEST_FILES):
                entries_ok = False
            add(
                "manifest.files-contract",
                "file_integrity",
                "pass" if entries_ok else "fail",
                (
                    "Render manifest declares the three page files once in stable order."
                    if entries_ok
                    else "Render manifest file declarations do not match the v1 contract."
                ),
                list(_MANIFEST_FILES),
            )
        else:
            for check_id, message in (
                ("manifest.fields", "Render manifest fields could not be checked."),
                ("manifest.schema-version", "Render manifest schema version could not be checked."),
                (
                    "manifest.page-spec-schema-version",
                    "Render manifest PageSpec schema version could not be checked.",
                ),
                ("manifest.page-id", "Render manifest page_id could not be checked."),
                (
                    "manifest.files-contract",
                    "Render manifest file declarations could not be checked.",
                ),
            ):
                add(
                    check_id,
                    "file_integrity" if check_id.endswith("files-contract") else "input_identity",
                    "fail",
                    message,
                    ["render_manifest.json"],
                )

        for filename in _MANIFEST_FILES:
            declared_digest = manifest_entries.get(filename)
            actual = file_bytes.get(filename)
            hash_ok = (
                declared_digest is not None
                and actual is not None
                and declared_digest == _sha256(actual)
            )
            add(
                f"file.sha256:{filename}",
                "file_integrity",
                "pass" if hash_ok else "fail",
                (
                    f"SHA-256 for {filename} matches the render manifest."
                    if hash_ok
                    else f"SHA-256 for {filename} is missing or does not match the render manifest."
                ),
                [filename],
            )

        parser: _RenderedHTMLParser | None = None
        html_text = file_text.get("index.html")
        if html_text is not None:
            try:
                parser = _RenderedHTMLParser()
                parser.feed(html_text)
                parser.close()
            except Exception:
                parser = None
        add(
            "html.parse",
            "html_structure",
            "pass" if parser is not None else "fail",
            (
                "index.html was parsed deterministically."
                if parser is not None
                else "index.html could not be parsed deterministically."
            ),
            ["index.html"],
        )

        actual_section_ids: set[str] = set()
        actual_component_ids: set[str] = set()
        if parser is not None:
            body_ok = parser.body_page_ids == [page_spec.page_id]
            add(
                "html.body-page-id",
                "html_structure",
                "pass" if body_ok else "fail",
                (
                    "body[data-page-id] matches PageSpec exactly once."
                    if body_ok
                    else "body[data-page-id] is missing, duplicated, or mismatched."
                ),
                [page_spec.page_id],
            )
            html_schema_ok = parser.html_schema_values == [PAGE_SPEC_SCHEMA_VERSION]
            add(
                "html.page-spec-schema",
                "html_structure",
                "pass" if html_schema_ok else "fail",
                (
                    "HTML identifies req2web.page_spec.v1 exactly once."
                    if html_schema_ok
                    else "HTML PageSpec schema marker is missing, duplicated, or incorrect."
                ),
                [PAGE_SPEC_SCHEMA_VERSION],
            )
            title_ok = (
                parser.rendered_texts("title") == [page_spec.title]
                and parser.rendered_texts("h1") == [page_spec.title]
            )
            add(
                "html.title",
                "html_structure",
                "pass" if title_ok else "fail",
                (
                    "PageSpec title is safely represented in title and h1 text."
                    if title_ok
                    else "PageSpec title is missing, duplicated, or not represented as text."
                ),
                [page_spec.page_id],
            )
            summary_ok = parser.rendered_texts("summary") == [page_spec.summary]
            add(
                "html.summary",
                "html_structure",
                "pass" if summary_ok else "fail",
                (
                    "PageSpec summary is safely represented as text."
                    if summary_ok
                    else "PageSpec summary is missing, duplicated, or not represented as text."
                ),
                [page_spec.page_id],
            )

            section_counts = Counter(parser.section_ids)
            actual_section_ids = set(parser.section_ids)
            for section in page_spec.sections:
                occurrence_ok = section_counts[section.section_id] == 1
                add(
                    f"html.section-occurrence:{section.section_id}",
                    "html_structure",
                    "pass" if occurrence_ok else "fail",
                    (
                        f"Section {section.section_id} appears exactly once."
                        if occurrence_ok
                        else f"Section {section.section_id} does not appear exactly once."
                    ),
                    [section.section_id],
                )
            order_ok = parser.section_ids == page_spec.layout.section_order
            add(
                "html.section-order",
                "html_structure",
                "pass" if order_ok else "fail",
                (
                    "Sections follow layout.section_order."
                    if order_ok
                    else "Sections do not follow layout.section_order exactly."
                ),
                page_spec.layout.section_order,
            )
            extra_sections = sorted(
                actual_section_ids - {item.section_id for item in page_spec.sections}
            )
            add(
                "html.extra-sections",
                "html_structure",
                "pass" if not extra_sections else "fail",
                (
                    "HTML contains no untracked sections."
                    if not extra_sections
                    else "HTML contains sections that are not in PageSpec."
                ),
                extra_sections or [page_spec.page_id],
            )

            actual_component_ids = set(parser.components)
            for component in page_spec.components:
                occurrences = parser.components.get(component.component_id, [])
                occurrence_ok = len(occurrences) == 1
                add(
                    f"html.component-occurrence:{component.component_id}",
                    "html_structure",
                    "pass" if occurrence_ok else "fail",
                    (
                        f"Component {component.component_id} appears exactly once."
                        if occurrence_ok
                        else f"Component {component.component_id} does not appear exactly once."
                    ),
                    [component.component_id],
                )
                actual = occurrences[0] if occurrence_ok else {}
                owner_ok = actual.get("section_id") == component.section_id
                add(
                    f"html.component-section:{component.component_id}",
                    "html_structure",
                    "pass" if owner_ok else "fail",
                    (
                        f"Component {component.component_id} is inside its owning section."
                        if owner_ok
                        else f"Component {component.component_id} is not inside its owning section."
                    ),
                    [component.component_id, component.section_id],
                )
                type_ok = actual.get("component_type") == component.component_type
                add(
                    f"html.component-type:{component.component_id}",
                    "html_structure",
                    "pass" if type_ok else "fail",
                    (
                        f"Component {component.component_id} preserves component_type."
                        if type_ok
                        else f"Component {component.component_id} does not preserve component_type."
                    ),
                    [component.component_id, component.component_type],
                )
                expected_renderer = (
                    component.component_type
                    if component.component_type in SUPPORTED_COMPONENT_TYPES
                    else "fallback"
                )
                renderer_ok = (
                    actual.get("renderer_kind") == expected_renderer
                    and (expected_renderer != "fallback" or actual.get("hidden") is None)
                )
                add(
                    f"html.renderer-kind:{component.component_id}",
                    "html_structure",
                    "pass" if renderer_ok else "fail",
                    (
                        f"Component {component.component_id} has the expected visible renderer kind."
                        if renderer_ok
                        else f"Component {component.component_id} renderer kind is missing or incorrect."
                    ),
                    [component.component_id, expected_renderer],
                )
            extra_components = sorted(
                actual_component_ids
                - {item.component_id for item in page_spec.components}
            )
            add(
                "html.extra-components",
                "html_structure",
                "pass" if not extra_components else "fail",
                (
                    "HTML contains no untracked components."
                    if not extra_components
                    else "HTML contains components that are not in PageSpec."
                ),
                extra_components or [page_spec.page_id],
            )
        else:
            for check_id, message in (
                ("html.body-page-id", "body[data-page-id] could not be checked."),
                ("html.page-spec-schema", "HTML PageSpec schema marker could not be checked."),
                ("html.title", "PageSpec title rendering could not be checked."),
                ("html.summary", "PageSpec summary rendering could not be checked."),
                ("html.section-order", "Section order could not be checked."),
                ("html.extra-sections", "Untracked sections could not be checked."),
                ("html.extra-components", "Untracked components could not be checked."),
            ):
                add(check_id, "html_structure", "fail", message, ["index.html"])
            for section in page_spec.sections:
                add(
                    f"html.section-occurrence:{section.section_id}",
                    "html_structure",
                    "fail",
                    f"Section {section.section_id} could not be checked.",
                    [section.section_id],
                )
            for component in page_spec.components:
                for suffix, message in (
                    ("occurrence", "occurrence"),
                    ("section", "owning section"),
                    ("type", "component_type"),
                    ("renderer-kind", "renderer kind"),
                ):
                    add(
                        f"html.component-{suffix}:{component.component_id}",
                        "html_structure",
                        "fail",
                        f"Component {component.component_id} {message} could not be checked.",
                        [component.component_id],
                    )

        script_text = file_text.get("app.js")
        runtime: dict[str, Any] | None = None
        runtime_start = runtime_end = -1
        runtime_error = "app.js is missing or unreadable"
        if script_text is not None:
            runtime, runtime_start, runtime_end, runtime_error = _extract_page_data(
                script_text
            )
        add(
            "runtime.page-data",
            "runtime_coverage",
            "pass" if runtime is not None else "fail",
            (
                "app.js contains a valid deterministic PAGE_DATA object."
                if runtime is not None
                else f"app.js PAGE_DATA could not be checked: {runtime_error}."
            ),
            ["app.js"],
        )

        runtime_states_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        runtime_interactions_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        runtime_state_ids: set[str] = set()
        runtime_interaction_ids: set[str] = set()
        if runtime is not None:
            runtime_fields_ok = set(runtime) == {
                "component_sections",
                "initial_state_id",
                "interactions",
                "page_id",
                "states",
            }
            add(
                "runtime.fields",
                "runtime_coverage",
                "pass" if runtime_fields_ok else "fail",
                (
                    "PAGE_DATA fields match the deterministic runtime contract."
                    if runtime_fields_ok
                    else "PAGE_DATA fields do not match the deterministic runtime contract."
                ),
                ["app.js"],
            )
            runtime_page_ok = runtime.get("page_id") == page_spec.page_id
            add(
                "runtime.page-id",
                "runtime_coverage",
                "pass" if runtime_page_ok else "fail",
                (
                    "PAGE_DATA page_id matches PageSpec."
                    if runtime_page_ok
                    else "PAGE_DATA page_id does not match PageSpec."
                ),
                [page_spec.page_id],
            )
            initial_state_id = next(
                (item.state_id for item in page_spec.states if item.name == "initial"),
                page_spec.states[0].state_id,
            )
            initial_ok = runtime.get("initial_state_id") == initial_state_id
            add(
                "runtime.initial-state",
                "runtime_coverage",
                "pass" if initial_ok else "fail",
                (
                    "PAGE_DATA initial state matches PageSpec."
                    if initial_ok
                    else "PAGE_DATA initial state does not match PageSpec."
                ),
                [initial_state_id],
            )
            expected_component_sections = {
                item.component_id: item.section_id for item in page_spec.components
            }
            component_sections_ok = (
                runtime.get("component_sections") == expected_component_sections
            )
            add(
                "runtime.component-sections",
                "runtime_coverage",
                "pass" if component_sections_ok else "fail",
                (
                    "PAGE_DATA component ownership matches PageSpec."
                    if component_sections_ok
                    else "PAGE_DATA component ownership does not match PageSpec."
                ),
                list(expected_component_sections),
            )

            runtime_states = runtime.get("states")
            if isinstance(runtime_states, list):
                for item in runtime_states:
                    if isinstance(item, dict) and isinstance(item.get("state_id"), str):
                        runtime_states_by_id[item["state_id"]].append(item)
            runtime_state_ids = set(runtime_states_by_id)
            for state in page_spec.states:
                expected_state = {
                    "description": state.description,
                    "name": state.name,
                    "state_id": state.state_id,
                    "visible_component_ids": state.visible_component_ids,
                }
                occurrences = runtime_states_by_id.get(state.state_id, [])
                state_ok = occurrences == [expected_state]
                add(
                    f"runtime.state:{state.state_id}",
                    "runtime_coverage",
                    "pass" if state_ok else "fail",
                    (
                        f"PageState {state.state_id} is present exactly once with matching data."
                        if state_ok
                        else f"PageState {state.state_id} is missing, duplicated, or mismatched."
                    ),
                    [state.state_id],
                )
            extra_states = sorted(
                runtime_state_ids - {item.state_id for item in page_spec.states}
            )
            add(
                "runtime.extra-states",
                "runtime_coverage",
                "pass" if not extra_states else "fail",
                (
                    "PAGE_DATA contains no untracked states."
                    if not extra_states
                    else "PAGE_DATA contains states that are not in PageSpec."
                ),
                extra_states or [page_spec.page_id],
            )

            runtime_interactions = runtime.get("interactions")
            if isinstance(runtime_interactions, list):
                for item in runtime_interactions:
                    if isinstance(item, dict) and isinstance(
                        item.get("interaction_id"), str
                    ):
                        runtime_interactions_by_id[item["interaction_id"]].append(item)
            runtime_interaction_ids = set(runtime_interactions_by_id)
            for interaction in page_spec.interactions:
                expected_interaction = {
                    "action": interaction.action,
                    "interaction_id": interaction.interaction_id,
                    "source_state_id": interaction.source_state_id,
                    "target_state_id": interaction.target_state_id,
                    "trigger_component_id": interaction.trigger_component_id,
                    "user_feedback": interaction.user_feedback,
                }
                occurrences = runtime_interactions_by_id.get(
                    interaction.interaction_id, []
                )
                interaction_ok = occurrences == [expected_interaction]
                add(
                    f"runtime.interaction:{interaction.interaction_id}",
                    "runtime_coverage",
                    "pass" if interaction_ok else "fail",
                    (
                        f"Interaction {interaction.interaction_id} is present exactly once with matching data."
                        if interaction_ok
                        else f"Interaction {interaction.interaction_id} is missing, duplicated, or mismatched."
                    ),
                    [interaction.interaction_id],
                )
                trigger_ok = interaction.trigger_component_id in actual_component_ids
                add(
                    f"runtime.trigger-component:{interaction.interaction_id}",
                    "runtime_coverage",
                    "pass" if trigger_ok else "fail",
                    (
                        f"Interaction {interaction.interaction_id} trigger component exists in HTML."
                        if trigger_ok
                        else f"Interaction {interaction.interaction_id} trigger component is missing from HTML."
                    ),
                    [interaction.interaction_id, interaction.trigger_component_id],
                )
                target_ok = interaction.target_state_id in runtime_state_ids
                add(
                    f"runtime.target-state:{interaction.interaction_id}",
                    "runtime_coverage",
                    "pass" if target_ok else "fail",
                    (
                        f"Interaction {interaction.interaction_id} target state exists at runtime."
                        if target_ok
                        else f"Interaction {interaction.interaction_id} target state is missing at runtime."
                    ),
                    [interaction.interaction_id, interaction.target_state_id],
                )
            extra_interactions = sorted(
                runtime_interaction_ids
                - {item.interaction_id for item in page_spec.interactions}
            )
            add(
                "runtime.extra-interactions",
                "runtime_coverage",
                "pass" if not extra_interactions else "fail",
                (
                    "PAGE_DATA contains no untracked interactions."
                    if not extra_interactions
                    else "PAGE_DATA contains interactions that are not in PageSpec."
                ),
                extra_interactions or [page_spec.page_id],
            )
        else:
            for check_id, message in (
                ("runtime.fields", "PAGE_DATA fields could not be checked."),
                ("runtime.page-id", "PAGE_DATA page_id could not be checked."),
                ("runtime.initial-state", "PAGE_DATA initial state could not be checked."),
                (
                    "runtime.component-sections",
                    "PAGE_DATA component ownership could not be checked.",
                ),
                ("runtime.extra-states", "Untracked runtime states could not be checked."),
                (
                    "runtime.extra-interactions",
                    "Untracked runtime interactions could not be checked.",
                ),
            ):
                add(check_id, "runtime_coverage", "fail", message, ["app.js"])
            for state in page_spec.states:
                add(
                    f"runtime.state:{state.state_id}",
                    "runtime_coverage",
                    "fail",
                    f"PageState {state.state_id} could not be checked.",
                    [state.state_id],
                )
            for interaction in page_spec.interactions:
                for prefix, message in (
                    ("interaction", "runtime data"),
                    ("trigger-component", "HTML trigger component"),
                    ("target-state", "runtime target state"),
                ):
                    add(
                        f"runtime.{prefix}:{interaction.interaction_id}",
                        "runtime_coverage",
                        "fail",
                        f"Interaction {interaction.interaction_id} {message} could not be checked.",
                        [interaction.interaction_id],
                    )

        if script_text is not None and runtime_start >= 0:
            executable_script = (
                script_text[:runtime_start] + "{}" + script_text[runtime_end:]
            )
            no_inner_html = _JS_INNER_HTML_RE.search(executable_script) is None
        else:
            no_inner_html = False
        add(
            "safety.no-inner-html",
            "runtime_coverage",
            "pass" if no_inner_html else "fail",
            (
                "app.js executable logic does not use innerHTML."
                if no_inner_html
                else "app.js executable logic could not be verified free of innerHTML."
            ),
            ["app.js"],
        )

        network_scan_ready = all(
            filename in file_text for filename in ("index.html", "styles.css", "app.js")
        )
        network_free = False
        if network_scan_ready and parser is not None and script_text is not None:
            executable_script = (
                script_text[:runtime_start] + "{}" + script_text[runtime_end:]
                if runtime_start >= 0
                else script_text
            )
            network_free = (
                not parser.external_references
                and _CSS_NETWORK_RE.search(file_text["styles.css"]) is None
                and _JS_NETWORK_API_RE.search(executable_script) is None
                and _NETWORK_URL_RE.search(executable_script) is None
            )
        add(
            "safety.offline-only",
            "runtime_coverage",
            "pass" if network_free else "fail",
            (
                "Static output contains no external network dependency."
                if network_free
                else "Static output contains or could not be checked for an external network dependency."
            ),
            ["index.html", "styles.css", "app.js"],
        )

        use_case_ids = {item.use_case_id for item in page_spec.use_cases}
        for acceptance in page_spec.acceptance_checks:
            use_cases_ok = bool(acceptance.use_case_ids) and set(
                acceptance.use_case_ids
            ).issubset(use_case_ids)
            add(
                f"acceptance.use-cases:{acceptance.check_id}",
                "acceptance_coverage",
                "pass" if use_cases_ok else "fail",
                (
                    f"Acceptance check {acceptance.check_id} references existing use cases."
                    if use_cases_ok
                    else f"Acceptance check {acceptance.check_id} references a missing use case."
                ),
                [acceptance.check_id, *acceptance.use_case_ids],
            )
            state_ok = acceptance.state_id in runtime_state_ids
            add(
                f"acceptance.runtime-state:{acceptance.check_id}",
                "acceptance_coverage",
                "pass" if state_ok else "fail",
                (
                    f"Acceptance check {acceptance.check_id} target state exists at runtime."
                    if state_ok
                    else f"Acceptance check {acceptance.check_id} target state is missing at runtime."
                ),
                [acceptance.check_id, acceptance.state_id],
            )
            related_interactions = [
                item
                for item in page_spec.interactions
                if item.target_state_id == acceptance.state_id
                and set(item.use_case_ids).intersection(acceptance.use_case_ids)
            ]
            reachable_ok = any(
                any(
                    runtime_item.get("target_state_id") == acceptance.state_id
                    for runtime_item in runtime_interactions_by_id.get(
                        item.interaction_id, []
                    )
                )
                for item in related_interactions
            )
            add(
                f"acceptance.reachable:{acceptance.check_id}",
                "acceptance_coverage",
                "pass" if reachable_ok else "fail",
                (
                    f"Acceptance check {acceptance.check_id} has a related interaction reaching its target state."
                    if reachable_ok
                    else f"Acceptance check {acceptance.check_id} has no rendered related interaction reaching its target state."
                ),
                [
                    acceptance.check_id,
                    acceptance.state_id,
                    *(item.interaction_id for item in related_interactions),
                ],
            )

        states_by_id = {item.state_id: item for item in page_spec.states}
        runtime_initial_state_id = next(
            (item.state_id for item in page_spec.states if item.name == "initial"),
            page_spec.states[0].state_id,
        )

        def runtime_has_interaction(interaction) -> bool:
            expected = {
                "action": interaction.action,
                "interaction_id": interaction.interaction_id,
                "source_state_id": interaction.source_state_id,
                "target_state_id": interaction.target_state_id,
                "trigger_component_id": interaction.trigger_component_id,
                "user_feedback": interaction.user_feedback,
            }
            return runtime_interactions_by_id.get(interaction.interaction_id) == [
                expected
            ]

        rendered_interactions = [
            item for item in page_spec.interactions if runtime_has_interaction(item)
        ]
        runtime_reachable_state_ids: set[str] = set()
        if (
            runtime is not None
            and runtime.get("initial_state_id") == runtime_initial_state_id
            and runtime_initial_state_id in runtime_state_ids
        ):
            runtime_reachable_state_ids.add(runtime_initial_state_id)
            pending_state_ids = deque((runtime_initial_state_id,))
            while pending_state_ids:
                source_state_id = pending_state_ids.popleft()
                for interaction in rendered_interactions:
                    if interaction.source_state_id != source_state_id:
                        continue
                    if interaction.target_state_id in runtime_reachable_state_ids:
                        continue
                    runtime_reachable_state_ids.add(interaction.target_state_id)
                    pending_state_ids.append(interaction.target_state_id)

        error_role_markers = (
            "error",
            "invalid",
            "validation",
            "failure",
            "failed",
            "denied",
            "reject",
            "exception",
            "blocked",
            "unavailable",
            "\u9519\u8bef",
            "\u65e0\u6548",
            "\u6821\u9a8c",
            "\u9a8c\u8bc1\u5931\u8d25",
            "\u5931\u8d25",
            "\u62d2\u7edd",
            "\u5f02\u5e38",
            "\u65e0\u6cd5",
        )
        scenario_error_markers = {
            "input": (
                "invalid",
                "validation",
                "\u65e0\u6548",
                "\u8f93\u5165",
                "\u6821\u9a8c",
            ),
            "permission": (
                "permission",
                "denied",
                "camera",
                "\u6743\u9650",
                "\u62d2\u7edd",
                "\u76f8\u673a",
            ),
            "generic": error_role_markers,
        }

        def has_role_marker(markers: tuple[str, ...], *values: str) -> bool:
            normalized = " ".join(values).casefold()
            return any(marker in normalized for marker in markers)

        def has_error_role_marker(*values: str) -> bool:
            return has_role_marker(error_role_markers, *values)

        for constraint in page_spec.constraints:
            if constraint.source != "agent_context":
                continue
            scenario = match_error_recovery_constraint(constraint.description)
            if scenario is None:
                continue

            entry_candidates: list[tuple[Any, list[Any], set[str]]] = []
            for interaction in page_spec.interactions:
                source_state = states_by_id.get(interaction.source_state_id)
                target_state = states_by_id.get(interaction.target_state_id)
                if source_state is None or target_state is None:
                    continue
                if interaction.source_state_id not in runtime_reachable_state_ids:
                    continue
                if interaction.trigger_component_id not in set(
                    source_state.visible_component_ids
                ):
                    continue
                if not runtime_has_interaction(interaction):
                    continue
                if interaction.target_state_id not in runtime_state_ids:
                    continue
                if not has_error_role_marker(target_state.name, target_state.description):
                    continue
                error_acceptances = [
                    item
                    for item in page_spec.acceptance_checks
                    if item.state_id == target_state.state_id
                    and bool(
                        set(item.use_case_ids).intersection(interaction.use_case_ids)
                    )
                    and has_error_role_marker(item.description)
                ]
                scenario_markers = scenario_error_markers.get(
                    scenario.kind, error_role_markers
                )
                if not has_role_marker(
                    scenario_markers,
                    target_state.name,
                    target_state.description,
                    interaction.action,
                    interaction.user_feedback,
                    *(item.description for item in error_acceptances),
                ):
                    continue
                accepted_use_case_ids = set().union(
                    *(set(item.use_case_ids) for item in error_acceptances)
                )
                relevant_use_case_ids = set(interaction.use_case_ids).intersection(
                    accepted_use_case_ids
                )
                if not relevant_use_case_ids:
                    continue
                entry_candidates.append(
                    (interaction, error_acceptances, relevant_use_case_ids)
                )

            entry_ok = len(entry_candidates) == 1
            entry_interaction = entry_candidates[0][0] if entry_ok else None
            entry_acceptances = entry_candidates[0][1] if entry_ok else []
            relevant_use_case_ids = entry_candidates[0][2] if entry_ok else set()
            add(
                f"error-recovery.entry:{constraint.constraint_id}",
                "acceptance_coverage",
                "pass" if entry_ok else "fail",
                (
                    f"Constraint {constraint.constraint_id} has one rendered reachable error-entry interaction."
                    if entry_ok
                    else f"Constraint {constraint.constraint_id} does not have exactly one rendered reachable error-entry interaction."
                ),
                [
                    constraint.constraint_id,
                    *(item[0].interaction_id for item in entry_candidates),
                ],
            )

            index_markup = file_text.get("index.html", "")
            global_page_state_feedback = (
                'id="page-state"' in index_markup
                and 'class="state-message"' in index_markup
            )
            feedback_ok = bool(
                entry_interaction is not None
                and entry_interaction.target_state_id in runtime_state_ids
                and entry_interaction.user_feedback.strip()
                and runtime_has_interaction(entry_interaction)
                and global_page_state_feedback
            )
            add(
                f"error-recovery.feedback:{constraint.constraint_id}",
                "acceptance_coverage",
                "pass" if feedback_ok else "fail",
                (
                    f"Constraint {constraint.constraint_id} exposes non-empty error feedback through the rendered global page-state message."
                    if feedback_ok
                    else f"Constraint {constraint.constraint_id} lacks reachable visible error feedback."
                ),
                [
                    constraint.constraint_id,
                    *(
                        (entry_interaction.interaction_id,)
                        if feedback_ok and entry_interaction is not None
                        else ()
                    ),
                ],
            )

            recovery_candidates = []
            if entry_interaction is not None:
                error_state = states_by_id[entry_interaction.target_state_id]
                non_error_acceptance_state_ids = {
                    item.state_id
                    for item in page_spec.acceptance_checks
                    if bool(
                        set(item.use_case_ids).intersection(relevant_use_case_ids)
                    )
                    and item.state_id in states_by_id
                    and not has_error_role_marker(
                        states_by_id[item.state_id].name,
                        states_by_id[item.state_id].description,
                    )
                }
                alternative_target_ids = non_error_acceptance_state_ids - {
                    entry_interaction.source_state_id
                }
                unique_alternative_target_id = (
                    next(iter(alternative_target_ids))
                    if len(alternative_target_ids) == 1
                    else None
                )
                for interaction in page_spec.interactions:
                    target_allowed = (
                        interaction.target_state_id
                        == entry_interaction.source_state_id
                        or (
                            unique_alternative_target_id is not None
                            and interaction.target_state_id
                            == unique_alternative_target_id
                        )
                    )
                    if (
                        interaction.source_state_id == error_state.state_id
                        and target_allowed
                        and interaction.target_state_id in runtime_state_ids
                        and bool(
                            set(interaction.use_case_ids).intersection(
                                relevant_use_case_ids
                            )
                        )
                        and interaction.trigger_component_id
                        in set(error_state.visible_component_ids)
                        and interaction.user_feedback.strip()
                        and runtime_has_interaction(interaction)
                    ):
                        recovery_candidates.append(interaction)

            recovery_ok = len(recovery_candidates) == 1
            recovery_interaction = recovery_candidates[0] if recovery_ok else None
            add(
                f"error-recovery.return:{constraint.constraint_id}",
                "acceptance_coverage",
                "pass" if recovery_ok else "fail",
                (
                    f"Constraint {constraint.constraint_id} has one rendered recovery path from the resolved error state."
                    if recovery_ok
                    else f"Constraint {constraint.constraint_id} does not have exactly one rendered recovery path from the resolved error state."
                ),
                [
                    constraint.constraint_id,
                    *(item.interaction_id for item in recovery_candidates),
                ],
            )

            recovery_acceptances = [
                item
                for item in page_spec.acceptance_checks
                if recovery_interaction is not None
                and item.state_id == recovery_interaction.target_state_id
                and bool(set(item.use_case_ids).intersection(relevant_use_case_ids))
            ]
            acceptance_ok = (
                entry_ok
                and recovery_ok
                and bool(entry_acceptances)
                and bool(recovery_acceptances)
            )
            add(
                f"error-recovery.acceptance:{constraint.constraint_id}",
                "acceptance_coverage",
                "pass" if acceptance_ok else "fail",
                (
                    f"Constraint {constraint.constraint_id} has acceptance coverage for error entry and recovery."
                    if acceptance_ok
                    else f"Constraint {constraint.constraint_id} lacks acceptance coverage for error entry or recovery."
                ),
                [
                    constraint.constraint_id,
                    *(item.check_id for item in entry_acceptances),
                    *(item.check_id for item in recovery_acceptances),
                ],
            )

        for use_case in page_spec.use_cases:
            sections = [
                item
                for item in page_spec.sections
                if use_case.use_case_id in item.use_case_ids
            ]
            section_ids = {item.section_id for item in sections}
            components = [
                item for item in page_spec.components if item.section_id in section_ids
            ]
            interactions = [
                item
                for item in page_spec.interactions
                if use_case.use_case_id in item.use_case_ids
            ]
            acceptances = [
                item
                for item in page_spec.acceptance_checks
                if use_case.use_case_id in item.use_case_ids
            ]
            coverage_ok = (
                bool(sections)
                and all(item.section_id in actual_section_ids for item in sections)
                and bool(components)
                and all(
                    item.component_id in actual_component_ids for item in components
                )
                and bool(interactions)
                and all(
                    item.interaction_id in runtime_interaction_ids
                    for item in interactions
                )
                and bool(acceptances)
            )
            add(
                f"use-case.coverage:{use_case.use_case_id}",
                "acceptance_coverage",
                "pass" if coverage_ok else "fail",
                (
                    f"Use case {use_case.use_case_id} has rendered section, component, interaction, and acceptance coverage."
                    if coverage_ok
                    else f"Use case {use_case.use_case_id} lacks rendered section, component, interaction, or acceptance coverage."
                ),
                [use_case.use_case_id],
            )

        if runtime is not None:
            initial_state = runtime.get("initial_state_id")
            graph: dict[str, set[str]] = defaultdict(set)
            for occurrences in runtime_interactions_by_id.values():
                for item in occurrences:
                    source = item.get("source_state_id")
                    target = item.get("target_state_id")
                    if isinstance(source, str) and isinstance(target, str):
                        graph[source].add(target)
            reachable_states: set[str] = set()
            if isinstance(initial_state, str):
                queue: deque[str] = deque([initial_state])
                while queue:
                    state_id = queue.popleft()
                    if state_id in reachable_states:
                        continue
                    reachable_states.add(state_id)
                    queue.extend(sorted(graph.get(state_id, set()) - reachable_states))
            acceptance_state_ids = {
                item.state_id for item in page_spec.acceptance_checks
            }
            for state in page_spec.states:
                if (
                    state.state_id != initial_state
                    and state.state_id not in acceptance_state_ids
                    and state.state_id not in reachable_states
                ):
                    add(
                        f"warning.unreachable-state:{state.state_id}",
                        "capability_boundary",
                        "warning",
                        f"Non-acceptance state {state.state_id} has no reachable interaction.",
                        [state.state_id],
                    )

        for component in page_spec.components:
            if component.component_type not in SUPPORTED_COMPONENT_TYPES:
                add(
                    f"warning.fallback-component:{component.component_id}",
                    "capability_boundary",
                    "warning",
                    f"Component {component.component_id} uses the visible generic fallback renderer.",
                    [component.component_id, component.component_type],
                )

        extra_files: list[str] = []
        if output_dir.is_dir():
            try:
                for path in output_dir.rglob("*"):
                    if path.is_file():
                        relative = path.relative_to(output_dir).as_posix()
                        if relative not in set(_REQUIRED_FILES) | _CHECKER_SIDECARS:
                            extra_files.append(relative)
            except OSError:
                extra_files = ["<unreadable-extra-files>"]
        extra_files = sorted(set(extra_files))
        if extra_files:
            add(
                "warning.extra-local-files",
                "capability_boundary",
                "warning",
                "Output directory contains extra local files not declared by the render manifest.",
                extra_files,
            )

        counts = Counter(item.status for item in checks)
        report = ConsistencyReport(
            page_id=page_spec.page_id,
            passed=counts["fail"] == 0,
            summary={
                "total": len(checks),
                "pass": counts["pass"],
                "fail": counts["fail"],
                "warning": counts["warning"],
            },
            checks=checks,
            warnings=[item.message for item in checks if item.status == "warning"],
        )
        report.validate()
        return report
