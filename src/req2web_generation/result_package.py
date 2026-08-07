from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle
from req2web_rag.corpus import ROLE_ORDER

from .consistency import (
    CONSISTENCY_REPORT_SCHEMA_VERSION,
    ConsistencyReport,
)
from .renderer import RENDER_MANIFEST_SCHEMA_VERSION, RenderResult
from .schema import PAGE_SPEC_SCHEMA_VERSION, PageSpec


RESULT_SUMMARY_SCHEMA_VERSION = "req2web.result.summary.v1"
RESULT_PACKAGE_SCHEMA_VERSION = "req2web.result.package.v1"

_PAGE_FILES = (
    "page/index.html",
    "page/styles.css",
    "page/app.js",
    "page/render_manifest.json",
)
_INTERNAL_FILES = (
    "internal/agent_context.json",
    "internal/page_spec.json",
    "internal/consistency_report.json",
)
_PACKAGE_CONTENT_FILES = (*_PAGE_FILES, *_INTERNAL_FILES, "result_summary.json")
_PACKAGE_MANIFEST = "package_manifest.json"
_RENDER_FILES = ("index.html", "styles.css", "app.js")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?:(?<![a-z0-9+.-])[a-z]:[\\/]|file://)"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_FILE_ROLES = {
    "page/index.html": "browser_entrypoint",
    "page/styles.css": "page_styles",
    "page/app.js": "page_script",
    "page/render_manifest.json": "render_manifest",
    "internal/agent_context.json": "agent_context",
    "internal/page_spec.json": "page_spec",
    "internal/consistency_report.json": "consistency_report",
    "result_summary.json": "result_summary",
}

_REQUIRED_REPORT_CHECKS = (
    "input.page-spec",
    "input.render-result-page-id",
    "input.output-paths",
    "manifest.schema-version",
    "manifest.page-spec-schema-version",
    "manifest.page-id",
    "manifest.files-contract",
    "file.required:index.html",
    "file.required:styles.css",
    "file.required:app.js",
    "file.required:render_manifest.json",
    "file.sha256:index.html",
    "file.sha256:styles.css",
    "file.sha256:app.js",
)


class ResultPackageError(ValueError):
    """Raised when a result package gate or package integrity check fails."""


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_relative_path(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResultPackageError(f"{field_name} must be a non-empty relative path")
    if "\\" in value:
        raise ResultPackageError(f"{field_name} must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ResultPackageError(f"{field_name} must be a normalized relative path")
    return value


def _ensure_no_absolute_paths(files: dict[str, bytes]) -> None:
    for relative_path, content in files.items():
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ResultPackageError(
                f"package file must be UTF-8: {relative_path}"
            ) from exc
        if _WINDOWS_ABSOLUTE_PATH_RE.search(text):
            raise ResultPackageError(
                f"package file contains an absolute local path: {relative_path}"
            )


def _result_package_from_dir(
    package_dir: Path,
    *,
    package_id: str,
    page_id: str,
) -> ResultPackage:
    return ResultPackage(
        package_id=package_id,
        page_id=page_id,
        package_dir=package_dir,
        entrypoint="page/index.html",
        result_summary="result_summary.json",
        package_manifest=_PACKAGE_MANIFEST,
    )


@dataclass(frozen=True)
class ResultPackage:
    package_id: str
    page_id: str
    package_dir: Path
    entrypoint: str
    result_summary: str
    package_manifest: str
    schema_version: str = RESULT_PACKAGE_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != RESULT_PACKAGE_SCHEMA_VERSION:
            raise ResultPackageError(
                f"unsupported result package schema: {self.schema_version}"
            )
        if not self.package_id.strip() or not self.page_id.strip():
            raise ResultPackageError("result package identity must not be empty")
        for field_name in ("entrypoint", "result_summary", "package_manifest"):
            _validate_relative_path(getattr(self, field_name), field_name)

        root = Path(self.package_dir)
        manifest_path = root / self.package_manifest
        if not manifest_path.is_file():
            raise ResultPackageError("package_manifest.json is missing")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ResultPackageError(
                "package_manifest.json is not a readable JSON object"
            ) from exc
        if not isinstance(manifest, dict):
            raise ResultPackageError("package_manifest.json must be a JSON object")
        if set(manifest) != {
            "entrypoint",
            "files",
            "package_id",
            "page_id",
            "result_summary",
            "schema_version",
        }:
            raise ResultPackageError("package manifest fields do not match v1")
        if manifest.get("schema_version") != RESULT_PACKAGE_SCHEMA_VERSION:
            raise ResultPackageError("package manifest schema version is incorrect")
        if manifest.get("package_id") != self.package_id:
            raise ResultPackageError("package manifest package_id is inconsistent")
        if manifest.get("page_id") != self.page_id:
            raise ResultPackageError("package manifest page_id is inconsistent")
        if manifest.get("entrypoint") != self.entrypoint:
            raise ResultPackageError("package manifest entrypoint is inconsistent")
        if manifest.get("result_summary") != self.result_summary:
            raise ResultPackageError("package manifest result_summary is inconsistent")

        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ResultPackageError("package manifest files must be a list")
        paths: list[str] = []
        file_bytes: dict[str, bytes] = {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "path",
                "role",
                "sha256",
                "size",
            }:
                raise ResultPackageError("package manifest file entry is invalid")
            relative_path = _validate_relative_path(
                entry.get("path"), "package manifest file path"
            )
            if relative_path == _PACKAGE_MANIFEST:
                raise ResultPackageError("package manifest must not hash itself")
            if entry.get("role") != _FILE_ROLES.get(relative_path):
                raise ResultPackageError(
                    f"package manifest role is incorrect: {relative_path}"
                )
            digest = entry.get("sha256")
            size = entry.get("size")
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise ResultPackageError(
                    f"package manifest SHA-256 is invalid: {relative_path}"
                )
            if not isinstance(size, int) or size < 0:
                raise ResultPackageError(
                    f"package manifest size is invalid: {relative_path}"
                )
            path = root.joinpath(*PurePosixPath(relative_path).parts)
            if not path.is_file():
                raise ResultPackageError(f"declared package file is missing: {relative_path}")
            content = path.read_bytes()
            if len(content) != size or _sha256(content) != digest:
                raise ResultPackageError(
                    f"declared package file hash or size is incorrect: {relative_path}"
                )
            paths.append(relative_path)
            file_bytes[relative_path] = content

        if paths != sorted(_PACKAGE_CONTENT_FILES):
            raise ResultPackageError(
                "package manifest must declare every non-manifest file in stable order"
            )
        actual_paths = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )
        if actual_paths != sorted((*_PACKAGE_CONTENT_FILES, _PACKAGE_MANIFEST)):
            raise ResultPackageError("package contains undeclared or missing files")

        try:
            summary = json.loads(file_bytes[self.result_summary].decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ResultPackageError("result_summary.json is invalid") from exc
        if not isinstance(summary, dict):
            raise ResultPackageError("result_summary.json must be a JSON object")
        if summary.get("schema_version") != RESULT_SUMMARY_SCHEMA_VERSION:
            raise ResultPackageError("result summary schema version is incorrect")
        if summary.get("package_id") != self.package_id:
            raise ResultPackageError("result summary package_id is inconsistent")
        if summary.get("page_id") != self.page_id:
            raise ResultPackageError("result summary page_id is inconsistent")
        if summary.get("entrypoint") != self.entrypoint:
            raise ResultPackageError("result summary entrypoint is inconsistent")
        artifacts = summary.get("artifacts")
        if not isinstance(artifacts, list):
            raise ResultPackageError("result summary artifacts must be a list")
        for artifact in artifacts:
            if not isinstance(artifact, dict) or set(artifact) != {"path", "role"}:
                raise ResultPackageError("result summary artifact is invalid")
            _validate_relative_path(artifact.get("path"), "artifact path")
        _ensure_no_absolute_paths(file_bytes)

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "page_id": self.page_id,
            "package_dir": str(self.package_dir),
            "entrypoint": self.entrypoint,
            "result_summary": self.result_summary,
            "package_manifest": self.package_manifest,
        }


class DeterministicResultPackager:
    """Assemble validated upstream artifacts into a deterministic result package."""

    def package(
        self,
        context: AgentContextBundle,
        page_spec: PageSpec,
        render_result: RenderResult,
        consistency_report: ConsistencyReport,
        package_dir: Path,
    ) -> ResultPackage:
        destination = Path(package_dir).expanduser().resolve(strict=False)
        self._validate_destination(destination)
        source_files, internal_files = self._gate(
            context,
            page_spec,
            render_result,
            consistency_report,
        )

        package_id = self._derive_package_id(source_files, internal_files)
        summary = self._build_summary(
            package_id,
            page_spec,
            consistency_report,
        )
        package_files = {
            **source_files,
            **internal_files,
            "result_summary.json": _json_bytes(summary),
        }
        _ensure_no_absolute_paths(package_files)

        destination.parent.mkdir(parents=True, exist_ok=True)
        # Python 3.12's tempfile mode can create a Windows directory that the
        # managed workspace cannot extend.  A same-parent UUID path is still
        # private staging state; it never enters package bytes or identifiers.
        staging = destination.parent / (
            f".{destination.name}.staging-{uuid.uuid4().hex}"
        )
        staging.mkdir()
        try:
            for relative_path in sorted(package_files):
                path = staging.joinpath(*PurePosixPath(relative_path).parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(package_files[relative_path])

            manifest = self._build_manifest(
                package_id,
                page_spec.page_id,
                package_files,
            )
            (staging / _PACKAGE_MANIFEST).write_bytes(_json_bytes(manifest))
            staged_result = _result_package_from_dir(
                staging,
                package_id=package_id,
                page_id=page_spec.page_id,
            )
            staged_result.validate()

            if destination.exists():
                if (
                    destination.is_symlink()
                    or not destination.is_dir()
                    or any(destination.iterdir())
                ):
                    raise ResultPackageError(
                        "package_dir already exists and is not an empty directory"
                    )
                destination.rmdir()
            os.replace(staging, destination)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

        return _result_package_from_dir(
            destination,
            package_id=package_id,
            page_id=page_spec.page_id,
        )

    @staticmethod
    def _validate_destination(destination: Path) -> None:
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ResultPackageError("package_dir must be a directory path")
            if any(destination.iterdir()):
                raise ResultPackageError(
                    "package_dir already exists and is not empty; refusing to overwrite"
                )

    def _gate(
        self,
        context: AgentContextBundle,
        page_spec: PageSpec,
        render_result: RenderResult,
        consistency_report: ConsistencyReport,
    ) -> tuple[dict[str, bytes], dict[str, bytes]]:
        if not isinstance(context, AgentContextBundle):
            raise TypeError("DeterministicResultPackager context must be an AgentContextBundle")
        if not isinstance(page_spec, PageSpec):
            raise TypeError("DeterministicResultPackager page_spec must be a PageSpec")
        if not isinstance(render_result, RenderResult):
            raise TypeError("DeterministicResultPackager render_result must be a RenderResult")
        if not isinstance(consistency_report, ConsistencyReport):
            raise TypeError(
                "DeterministicResultPackager consistency_report must be a ConsistencyReport"
            )

        context.validate()
        page_spec.validate()
        consistency_report.validate()
        if context.schema_version != AGENT_BUNDLE_SCHEMA_VERSION:
            raise ResultPackageError(
                f"unsupported Agent context schema: {context.schema_version}"
            )
        if page_spec.schema_version != PAGE_SPEC_SCHEMA_VERSION:
            raise ResultPackageError(
                f"unsupported PageSpec schema: {page_spec.schema_version}"
            )
        if consistency_report.schema_version != CONSISTENCY_REPORT_SCHEMA_VERSION:
            raise ResultPackageError(
                "unsupported consistency report schema: "
                f"{consistency_report.schema_version}"
            )
        self._validate_context_source(context, page_spec)

        page_ids = {
            page_spec.page_id,
            render_result.page_id,
            consistency_report.page_id,
        }
        if len(page_ids) != 1:
            raise ResultPackageError(
                "PageSpec, RenderResult, and ConsistencyReport page_id values must match"
            )
        if not consistency_report.passed:
            raise ResultPackageError("consistency report did not pass")
        if consistency_report.summary.get("fail") != 0:
            raise ResultPackageError("consistency report contains failed checks")
        checks_by_id = {item.check_id: item for item in consistency_report.checks}
        missing_or_failed = [
            check_id
            for check_id in _REQUIRED_REPORT_CHECKS
            if check_id not in checks_by_id or checks_by_id[check_id].status != "pass"
        ]
        if missing_or_failed:
            raise ResultPackageError(
                "consistency report lacks required passing package gates: "
                + ", ".join(missing_or_failed)
            )

        output_dir = Path(render_result.output_dir).expanduser().resolve(strict=False)
        render_paths = {
            "index.html": render_result.index_html,
            "styles.css": render_result.styles_css,
            "app.js": render_result.app_js,
            "render_manifest.json": render_result.render_manifest,
        }
        render_bytes: dict[str, bytes] = {}
        for filename, value in render_paths.items():
            try:
                path = Path(value).expanduser().resolve(strict=True)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                raise ResultPackageError(f"required render file is missing: {filename}") from exc
            if path != output_dir / filename or not path.is_file():
                raise ResultPackageError(
                    f"RenderResult path is not the canonical output file: {filename}"
                )
            render_bytes[filename] = path.read_bytes()
            try:
                render_bytes[filename].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ResultPackageError(
                    f"required render file is not UTF-8: {filename}"
                ) from exc

        self._validate_render_manifest(render_bytes, page_spec.page_id)
        source_files = {
            f"page/{filename}": render_bytes[filename]
            for filename in (*_RENDER_FILES, "render_manifest.json")
        }
        internal_files = {
            "internal/agent_context.json": _json_bytes(context.to_dict()),
            "internal/page_spec.json": _json_bytes(page_spec.to_dict()),
            "internal/consistency_report.json": _json_bytes(
                consistency_report.to_dict()
            ),
        }
        return source_files, internal_files

    @staticmethod
    def _validate_context_source(
        context: AgentContextBundle,
        page_spec: PageSpec,
    ) -> None:
        if page_spec.traceability.source_context_schema_version != context.schema_version:
            raise ResultPackageError(
                "PageSpec traceability does not identify the supplied Agent context"
            )
        if (
            page_spec.summary != context.requirement_summary
            or page_spec.target_device != context.target_device
            or page_spec.page_type != context.task_type
        ):
            raise ResultPackageError(
                "PageSpec identity fields do not match the supplied Agent context"
            )
        if [asdict(item) for item in page_spec.use_cases] != [
            asdict(item) for item in context.use_cases
        ]:
            raise ResultPackageError(
                "PageSpec use cases do not match the supplied Agent context"
            )
        context_constraints = list(context.constraints)
        spec_context_constraints = [
            item.description
            for item in page_spec.constraints
            if item.source == "agent_context"
        ]
        if spec_context_constraints != context_constraints:
            raise ResultPackageError(
                "PageSpec constraints do not match the supplied Agent context"
            )

        expected_evidence: list[tuple[str, str, str, list[str]]] = []
        for role in ROLE_ORDER:
            for result in context.retrieval_results[role]:
                references = result.get("references", [])
                if not isinstance(references, list):
                    raise ResultPackageError(
                        f"Agent context references must be a list: {role}"
                    )
                uris: list[str] = []
                for reference in references:
                    if not isinstance(reference, dict):
                        raise ResultPackageError(
                            f"Agent context reference must be an object: {role}"
                        )
                    uri = reference.get("uri")
                    if isinstance(uri, str) and uri.strip() and uri.strip() not in uris:
                        uris.append(uri.strip())
                expected_evidence.append(
                    (
                        role,
                        str(result.get("doc_id", "")),
                        str(result.get("title", "")).strip(),
                        uris[:3],
                    )
                )
        actual_evidence = [
            (item.role, item.doc_id, item.title, item.reference_uris)
            for item in page_spec.traceability.evidence
        ]
        if actual_evidence != expected_evidence:
            raise ResultPackageError(
                "PageSpec evidence does not match the supplied Agent context"
            )

    @staticmethod
    def _validate_render_manifest(
        render_bytes: dict[str, bytes],
        page_id: str,
    ) -> None:
        try:
            manifest = json.loads(render_bytes["render_manifest.json"].decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ResultPackageError("render_manifest.json is not valid JSON") from exc
        if not isinstance(manifest, dict):
            raise ResultPackageError("render_manifest.json must be a JSON object")
        if set(manifest) != {
            "files",
            "page_id",
            "page_spec_schema_version",
            "schema_version",
        }:
            raise ResultPackageError("render manifest fields do not match v1")
        if manifest.get("schema_version") != RENDER_MANIFEST_SCHEMA_VERSION:
            raise ResultPackageError("render manifest schema version is incorrect")
        if manifest.get("page_spec_schema_version") != PAGE_SPEC_SCHEMA_VERSION:
            raise ResultPackageError("render manifest PageSpec schema is incorrect")
        if manifest.get("page_id") != page_id:
            raise ResultPackageError("render manifest page_id is inconsistent")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ResultPackageError("render manifest files must be a list")
        if [entry.get("name") for entry in entries if isinstance(entry, dict)] != list(
            _RENDER_FILES
        ) or len(entries) != len(_RENDER_FILES):
            raise ResultPackageError(
                "render manifest must declare the three static files in stable order"
            )
        for filename, entry in zip(_RENDER_FILES, entries):
            if not isinstance(entry, dict) or set(entry) != {"name", "sha256"}:
                raise ResultPackageError(
                    f"render manifest entry is invalid: {filename}"
                )
            digest = entry.get("sha256")
            if digest != _sha256(render_bytes[filename]):
                raise ResultPackageError(
                    f"render manifest SHA-256 does not match static file: {filename}"
                )

    @staticmethod
    def _derive_package_id(
        source_files: dict[str, bytes],
        internal_files: dict[str, bytes],
    ) -> str:
        digest = hashlib.sha256()
        for relative_path, content in sorted({**source_files, **internal_files}.items()):
            path_bytes = relative_path.encode("utf-8")
            digest.update(len(path_bytes).to_bytes(4, "big"))
            digest.update(path_bytes)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return f"result-{digest.hexdigest()[:20]}"

    @staticmethod
    def _build_summary(
        package_id: str,
        page_spec: PageSpec,
        consistency_report: ConsistencyReport,
    ) -> dict[str, Any]:
        use_case_text = "; ".join(
            f"{item.title}: {item.expected_outcome}"
            for item in page_spec.use_cases
        )
        constraint_text = "; ".join(
            item.description for item in page_spec.constraints
        )
        text_description = (
            f"{page_spec.title}. {page_spec.summary}. "
            f"Core use cases: {use_case_text}. Constraints: {constraint_text}."
        )
        ui_references = [
            {
                "doc_id": item.doc_id,
                "title": item.title,
                "reference_uris": item.reference_uris,
            }
            for item in page_spec.traceability.evidence
            if item.role == "ui_reference"
        ]
        use_case_order = {
            item.use_case_id: index for index, item in enumerate(page_spec.use_cases)
        }
        interaction_order = {id(item): index for index, item in enumerate(page_spec.interactions)}
        interactions = sorted(
            page_spec.interactions,
            key=lambda item: (
                min(use_case_order[value] for value in item.use_case_ids),
                interaction_order[id(item)],
                item.interaction_id,
            ),
        )
        interaction_flow = [
            {
                "step": index,
                "use_case_ids": item.use_case_ids,
                "trigger_component_id": item.trigger_component_id,
                "action": item.action,
                "user_feedback": item.user_feedback,
                "target_state_id": item.target_state_id,
            }
            for index, item in enumerate(interactions, 1)
        ]
        artifacts = [
            {"path": path, "role": _FILE_ROLES[path]}
            for path in (
                "page/index.html",
                "page/styles.css",
                "page/app.js",
                "page/render_manifest.json",
                "internal/page_spec.json",
                "internal/consistency_report.json",
                "internal/agent_context.json",
            )
        ]
        return {
            "schema_version": RESULT_SUMMARY_SCHEMA_VERSION,
            "package_id": package_id,
            "page_id": page_spec.page_id,
            "title": page_spec.title,
            "summary": page_spec.summary,
            "target_device": page_spec.target_device,
            "page_type": page_spec.page_type,
            "entrypoint": "page/index.html",
            "text_description": text_description,
            "ui_references": ui_references,
            "interaction_flow": interaction_flow,
            "quality_gate": {
                "passed": consistency_report.passed,
                "total": consistency_report.summary["total"],
                "pass": consistency_report.summary["pass"],
                "fail": consistency_report.summary["fail"],
                "warning": consistency_report.summary["warning"],
                "warnings": consistency_report.warnings,
                "consistency_report": "internal/consistency_report.json",
            },
            "artifacts": artifacts,
        }

    @staticmethod
    def _build_manifest(
        package_id: str,
        page_id: str,
        package_files: dict[str, bytes],
    ) -> dict[str, Any]:
        if set(package_files) != set(_PACKAGE_CONTENT_FILES):
            raise ResultPackageError("package file set is incomplete before manifest")
        return {
            "schema_version": RESULT_PACKAGE_SCHEMA_VERSION,
            "package_id": package_id,
            "page_id": page_id,
            "entrypoint": "page/index.html",
            "result_summary": "result_summary.json",
            "files": [
                {
                    "path": relative_path,
                    "size": len(package_files[relative_path]),
                    "sha256": _sha256(package_files[relative_path]),
                    "role": _FILE_ROLES[relative_path],
                }
                for relative_path in sorted(package_files)
            ],
        }
