"""Real-browser audit harness for Phase 4 result packages.

The harness reuses the accepted RequirementView, AcceptancePlan,
AcceptanceBindingPlan, and BrowserExecutionReport authorities.  It adds only
Phase 4 custody: exact ResultPackage identities, viewport/browser facts,
console/page errors, interaction observations, screenshots, and a canary
receipt that is bound to the active flow's case summaries.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from req2web_acceptance import (
    AcceptanceBindingPlan,
    BrowserBackend,
    LazyPlaywrightBrowserBackend,
    build_semantic_alignment_request,
    compile_acceptance_binding,
    compile_acceptance_plan,
    execute_acceptance_binding_plan,
    project_requirement_view,
)
from req2web_agent import AgentContextBundle, UseCase
from req2web_generation import (
    AcceptanceCheck,
    ComponentSpec,
    ConstraintSpec,
    EvidenceReference,
    InteractionSpec,
    LayoutSpec,
    PageSpec,
    PageState,
    PageUseCase,
    RenderResult,
    SectionSpec,
    TraceabilitySpec,
    UseCaseTrace,
)
from req2web_rag import ROLE_ORDER


CASE_AUDIT_SCHEMA_VERSION = "req2web.phase4.real_browser_case_audit.v2"
CANARY_RECEIPT_SCHEMA_VERSION = (
    "req2web.phase4.real_browser_canary_receipt.v2"
)
FINAL_BROWSER_SUMMARY_SCHEMA_VERSION = (
    "req2web.phase4.real_browser_final_summary.v1"
)
BROWSER_HARNESS_REVISION = "phase4_acceptance_plan_playwright_v2"
CANARY_CASE_COUNT = 3
FINAL_CASE_COUNT = 10
CASE_SUMMARY_SCHEMA_VERSION = (
    "req2web.phase4.canonical_full_flow.v1.case_summary"
)
EVIDENCE_SCOPES = {
    "historical_synthetic_canary",
    "phase4_canonical_canary",
    "phase4_canonical_full",
}
VIEWPORTS = {
    "mobile": {"width": 390, "height": 844},
    "tablet": {"width": 1024, "height": 768},
    "desktop": {"width": 1440, "height": 900},
}
_PACKAGE_MANIFEST_KEYS = {
    "entrypoint",
    "files",
    "package_id",
    "page_id",
    "result_summary",
    "schema_version",
}
_PACKAGE_FILE_KEYS = {"path", "role", "sha256", "size"}
_CASE_AUDIT_KEYS = {
    "schema_version",
    "harness_revision",
    "evidence_scope",
    "run_id",
    "case_index",
    "case_id",
    "source_case_summary_identity",
    "result_package",
    "page_spec_identity",
    "requirement_view_identity",
    "acceptance_plan_identity",
    "acceptance_binding_identity",
    "browser_execution_report_identity",
    "browser",
    "viewport",
    "interactions",
    "console_messages",
    "page_errors",
    "screenshot_identity",
    "criteria_counts",
    "browser_status",
    "browser_execution_status",
    "page_spec_conformance_status",
    "semantic_alignment",
    "real_browser_executed",
    "automation_reliable",
    "started_at_utc",
    "completed_at_utc",
    "claim_boundary",
    "audit_identity",
}


class Phase4BrowserAcceptanceError(ValueError):
    """Raised when browser evidence cannot be bound safely."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase4BrowserAcceptanceError(
            "browser evidence is not canonical JSON"
        ) from exc


def _identity(
    value: object,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    raw = value if type(value) is bytes else _canonical(value)
    return {
        "identity_kind": identity_kind,
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _write_once(path: Path, raw: bytes) -> None:
    if type(raw) is not bytes or not raw:
        raise Phase4BrowserAcceptanceError("browser artifact is empty")
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
            raise Phase4BrowserAcceptanceError(
                f"browser artifact drifted: {path.name}"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        import os

        os.fsync(handle.fileno())


def _load_json(path: Path, name: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise Phase4BrowserAcceptanceError(f"{name} is missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase4BrowserAcceptanceError(f"{name} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise Phase4BrowserAcceptanceError(f"{name} must be an object")
    return copy.deepcopy(dict(value))


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise Phase4BrowserAcceptanceError(f"{name} must be canonical text")
    return value


def _digest(value: object, name: str) -> str:
    text = _text(value, name)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise Phase4BrowserAcceptanceError(
            f"{name} must be a lowercase SHA-256 digest"
        )
    return text


def _identity_record(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "identity_kind",
        "sha256",
        "byte_length",
        "revision",
    }:
        raise Phase4BrowserAcceptanceError(f"{name} identity shape drifted")
    data = copy.deepcopy(dict(value))
    if (
        data["identity_kind"] not in {"canonical_json", "raw_bytes"}
        or not isinstance(data["sha256"], str)
        or not data["sha256"].startswith("sha256:")
        or len(data["sha256"]) != 71
        or not isinstance(data["byte_length"], int)
        or isinstance(data["byte_length"], bool)
        or data["byte_length"] <= 0
    ):
        raise Phase4BrowserAcceptanceError(f"{name} identity is invalid")
    _digest(data["sha256"][7:], f"{name} identity SHA-256")
    _text(data["revision"], f"{name} identity revision")
    return data


def _safe_relative(value: object, name: str) -> str:
    text = _text(value, name)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "\\" in text:
        raise Phase4BrowserAcceptanceError(f"{name} is not a safe relative path")
    return text


def _require_rows(
    value: object,
    keys: set[str],
    name: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise Phase4BrowserAcceptanceError(f"{name} must be an array")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != keys:
            raise Phase4BrowserAcceptanceError(
                f"{name}[{index}] exact keys drifted"
            )
        rows.append(copy.deepcopy(dict(item)))
    return rows


def _agent_context_from_dict(value: Mapping[str, object]) -> AgentContextBundle:
    expected = {
        "original_requirement",
        "requirement_summary",
        "target_device",
        "task_type",
        "constraints",
        "use_cases",
        "retrieval_queries",
        "retrieval_results",
        "schema_version",
    }
    if set(value) != expected:
        raise Phase4BrowserAcceptanceError("AgentContext exact keys drifted")
    retrieval_queries = value["retrieval_queries"]
    retrieval_results = value["retrieval_results"]
    if (
        not isinstance(retrieval_queries, Mapping)
        or set(retrieval_queries) != set(ROLE_ORDER)
        or not isinstance(retrieval_results, Mapping)
        or set(retrieval_results) != set(ROLE_ORDER)
    ):
        raise Phase4BrowserAcceptanceError(
            "AgentContext retrieval roles drifted"
        )
    context = AgentContextBundle(
        original_requirement=value["original_requirement"],  # type: ignore[arg-type]
        requirement_summary=value["requirement_summary"],  # type: ignore[arg-type]
        target_device=value["target_device"],  # type: ignore[arg-type]
        task_type=value["task_type"],  # type: ignore[arg-type]
        constraints=copy.deepcopy(value["constraints"]),  # type: ignore[arg-type]
        use_cases=[
            UseCase(**item)
            for item in _require_rows(
                value["use_cases"],
                {
                    "use_case_id",
                    "title",
                    "actor",
                    "goal",
                    "expected_outcome",
                },
                "AgentContext.use_cases",
            )
        ],
        retrieval_queries={
            role: copy.deepcopy(retrieval_queries[role])  # type: ignore[index]
            for role in ROLE_ORDER
        },
        retrieval_results={
            role: copy.deepcopy(retrieval_results[role])  # type: ignore[index]
            for role in ROLE_ORDER
        },
        schema_version=value["schema_version"],  # type: ignore[arg-type]
    )
    context.validate()
    return context


def _page_spec_from_dict(value: Mapping[str, object]) -> PageSpec:
    expected = {
        "page_id",
        "title",
        "summary",
        "target_device",
        "page_type",
        "layout",
        "use_cases",
        "sections",
        "components",
        "states",
        "interactions",
        "constraints",
        "acceptance_checks",
        "traceability",
        "schema_version",
    }
    if set(value) != expected:
        raise Phase4BrowserAcceptanceError("PageSpec exact keys drifted")
    layout = value["layout"]
    traceability = value["traceability"]
    if (
        not isinstance(layout, Mapping)
        or set(layout) != {"pattern", "section_order"}
        or not isinstance(traceability, Mapping)
        or set(traceability)
        != {"source_context_schema_version", "evidence", "use_cases"}
    ):
        raise Phase4BrowserAcceptanceError("PageSpec nested keys drifted")
    page_spec = PageSpec(
        page_id=value["page_id"],  # type: ignore[arg-type]
        title=value["title"],  # type: ignore[arg-type]
        summary=value["summary"],  # type: ignore[arg-type]
        target_device=value["target_device"],  # type: ignore[arg-type]
        page_type=value["page_type"],  # type: ignore[arg-type]
        layout=LayoutSpec(**dict(layout)),
        use_cases=[
            PageUseCase(**item)
            for item in _require_rows(
                value["use_cases"],
                {
                    "use_case_id",
                    "title",
                    "actor",
                    "goal",
                    "expected_outcome",
                },
                "PageSpec.use_cases",
            )
        ],
        sections=[
            SectionSpec(**item)
            for item in _require_rows(
                value["sections"],
                {
                    "section_id",
                    "title",
                    "purpose",
                    "component_ids",
                    "use_case_ids",
                },
                "PageSpec.sections",
            )
        ],
        components=[
            ComponentSpec(**item)
            for item in _require_rows(
                value["components"],
                {
                    "component_id",
                    "section_id",
                    "component_type",
                    "label",
                    "purpose",
                },
                "PageSpec.components",
            )
        ],
        states=[
            PageState(**item)
            for item in _require_rows(
                value["states"],
                {
                    "state_id",
                    "name",
                    "description",
                    "visible_component_ids",
                },
                "PageSpec.states",
            )
        ],
        interactions=[
            InteractionSpec(**item)
            for item in _require_rows(
                value["interactions"],
                {
                    "interaction_id",
                    "trigger_component_id",
                    "source_state_id",
                    "action",
                    "target_state_id",
                    "user_feedback",
                    "use_case_ids",
                },
                "PageSpec.interactions",
            )
        ],
        constraints=[
            ConstraintSpec(**item)
            for item in _require_rows(
                value["constraints"],
                {"constraint_id", "description", "source"},
                "PageSpec.constraints",
            )
        ],
        acceptance_checks=[
            AcceptanceCheck(**item)
            for item in _require_rows(
                value["acceptance_checks"],
                {"check_id", "description", "use_case_ids", "state_id"},
                "PageSpec.acceptance_checks",
            )
        ],
        traceability=TraceabilitySpec(
            source_context_schema_version=traceability[
                "source_context_schema_version"
            ],  # type: ignore[arg-type]
            evidence=[
                EvidenceReference(**item)
                for item in _require_rows(
                    traceability["evidence"],
                    {"role", "doc_id", "title", "reference_uris"},
                    "PageSpec.traceability.evidence",
                )
            ],
            use_cases=[
                UseCaseTrace(**item)
                for item in _require_rows(
                    traceability["use_cases"],
                    {
                        "use_case_id",
                        "section_ids",
                        "component_ids",
                        "interaction_ids",
                        "evidence_doc_ids",
                    },
                    "PageSpec.traceability.use_cases",
                )
            ],
        ),
        schema_version=value["schema_version"],  # type: ignore[arg-type]
    )
    page_spec.validate()
    return page_spec


def _package_binding(package_root: Path) -> dict[str, object]:
    root = Path(package_root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise Phase4BrowserAcceptanceError(
            "result package root must be a real directory"
        )
    manifest_path = root / "package_manifest.json"
    manifest_raw = manifest_path.read_bytes()
    manifest = _load_json(manifest_path, "result package manifest")
    if set(manifest) != _PACKAGE_MANIFEST_KEYS:
        raise Phase4BrowserAcceptanceError(
            "result package manifest exact keys drifted"
        )
    files = _require_rows(
        manifest["files"],
        _PACKAGE_FILE_KEYS,
        "result package files",
    )
    paths = [_safe_relative(row["path"], "result package file path") for row in files]
    if len(paths) != len(set(paths)) or paths != sorted(paths):
        raise Phase4BrowserAcceptanceError(
            "result package inventory order or uniqueness drifted"
        )
    verified: list[dict[str, object]] = []
    for row, relative in zip(files, paths, strict=True):
        artifact = root / PurePosixPath(relative)
        if artifact.is_symlink() or not artifact.is_file():
            raise Phase4BrowserAcceptanceError(
                f"result package file is missing: {relative}"
            )
        raw = artifact.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if (
            _digest(row["sha256"], "result package file SHA-256") != digest
            or row["size"] != len(raw)
        ):
            raise Phase4BrowserAcceptanceError(
                f"result package file identity drifted: {relative}"
            )
        verified.append(copy.deepcopy(row))
    entrypoint = _safe_relative(
        manifest["entrypoint"],
        "result package entrypoint",
    )
    if entrypoint != "page/index.html" or entrypoint not in paths:
        raise Phase4BrowserAcceptanceError(
            "result package browser entrypoint drifted"
        )
    return {
        "schema_version": manifest["schema_version"],
        "package_id": manifest["package_id"],
        "page_id": manifest["page_id"],
        "entrypoint": entrypoint,
        "manifest_identity": _identity(
            manifest_raw,
            revision=str(manifest["schema_version"]),
            identity_kind="raw_bytes",
        ),
        "inventory_identity": _identity(
            verified,
            revision=f"{manifest['schema_version']}.inventory",
        ),
    }


def validate_result_package_binding(
    package_root: Path,
) -> dict[str, object]:
    """Validate and identify one immutable ResultPackage v1 directory."""

    return _package_binding(package_root)


@dataclass
class Phase4PlaywrightBrowserBackend(LazyPlaywrightBrowserBackend):
    """Playwright backend with Phase 4 viewport and observable audit facts."""

    screenshot_path: Path
    viewport: Mapping[str, int]
    channel: str | None = "chrome"

    def __post_init__(self) -> None:
        super().__init__(channel=self.channel)
        self._instrumented = False
        self._browser_started = False
        self._screenshot_captured = False
        self._console_messages: list[dict[str, str]] = []
        self._page_errors: list[dict[str, str]] = []
        self._interactions: list[dict[str, str]] = []

    def _ensure_page(self, timeout_ms: int) -> Any:
        page = super()._ensure_page(timeout_ms)
        if not self._instrumented:
            page.set_viewport_size(dict(self.viewport))
            page.on(
                "console",
                lambda message: self._console_messages.append(
                    {
                        "type": str(message.type),
                        "text": str(message.text),
                    }
                ),
            )
            page.on(
                "pageerror",
                lambda error: self._page_errors.append(
                    {
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                ),
            )
            self._instrumented = True
            self._browser_started = True
        return page

    def navigate(self, page_url: str, timeout_ms: int) -> Mapping[str, str]:
        parsed = urlparse(page_url)
        if parsed.scheme not in {"file", "http", "https"}:
            raise Phase4BrowserAcceptanceError(
                "browser page URL scheme is unsupported"
            )
        return super().navigate(page_url, timeout_ms)

    def trigger(self, selector: str, timeout_ms: int) -> Mapping[str, str]:
        result = dict(super().trigger(selector, timeout_ms))
        mechanism = str(result.get("mechanism"))
        self._interactions.append(
            {
                "kind": "input" if mechanism == "file_change" else "click",
                "selector": selector,
                "mechanism": mechanism,
            }
        )
        return result

    def close(self) -> None:
        try:
            if self._page is not None:
                self.screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                self._page.screenshot(
                    path=str(self.screenshot_path),
                    full_page=True,
                )
                self._screenshot_captured = self.screenshot_path.is_file()
        finally:
            super().close()

    def facts(self) -> dict[str, object]:
        return {
            "browser_started": self._browser_started,
            "screenshot_captured": self._screenshot_captured,
            "console_messages": copy.deepcopy(self._console_messages),
            "page_errors": copy.deepcopy(self._page_errors),
            "interactions": copy.deepcopy(self._interactions),
            "browser": {
                "engine": "playwright.chromium",
                "channel": self.channel,
                "headless": True,
            },
        }


BackendFactory = Callable[
    [AcceptanceBindingPlan, Path, Mapping[str, int]],
    BrowserBackend,
]


def _default_backend_factory(
    _binding: AcceptanceBindingPlan,
    screenshot_path: Path,
    viewport: Mapping[str, int],
) -> BrowserBackend:
    return Phase4PlaywrightBrowserBackend(
        screenshot_path=screenshot_path,
        viewport=viewport,
    )


def _backend_facts(backend: BrowserBackend) -> dict[str, object]:
    facts = getattr(backend, "facts", None)
    if callable(facts):
        value = facts()
        if isinstance(value, Mapping):
            return copy.deepcopy(dict(value))
    return {
        "browser_started": True,
        "screenshot_captured": False,
        "console_messages": [],
        "page_errors": [],
        "interactions": [],
        "browser": {
            "engine": type(backend).__name__,
            "channel": None,
            "headless": None,
        },
    }


def _layered_objective_statuses(
    *,
    report: Any,
    real_browser_executed: bool,
    screenshot_captured: bool,
    page_errors: list[object],
    console_error_count: int,
) -> tuple[str, str, str]:
    unknown_steps = any(item.status == "unknown" for item in report.steps)
    unknown_criteria = any(item.status == "unknown" for item in report.criteria)
    browser_step_failed = any(
        item.status == "fail"
        and item.action_kind
        in {"load_page", "assert_element_exists", "trigger_interaction"}
        for item in report.steps
    )
    page_spec_step_failed = any(
        item.status == "fail"
        and item.action_kind in {"assert_state", "assert_feedback"}
        for item in report.steps
    )
    binding_failed = any(
        item.status == "fail" and item.terminal_stage is not None
        for item in report.criteria
    )

    if (
        not real_browser_executed
        or not screenshot_captured
        or unknown_steps
        or unknown_criteria
    ):
        browser_execution_status = "unknown"
    elif browser_step_failed or page_errors or console_error_count:
        browser_execution_status = "fail"
    else:
        browser_execution_status = "pass"

    if unknown_steps or unknown_criteria:
        page_spec_conformance_status = "unknown"
    elif page_spec_step_failed or binding_failed:
        page_spec_conformance_status = "fail"
    elif browser_execution_status != "pass":
        page_spec_conformance_status = "unknown"
    else:
        page_spec_conformance_status = "pass"

    if "unknown" in {
        browser_execution_status,
        page_spec_conformance_status,
    }:
        browser_status = "unknown"
    elif "fail" in {
        browser_execution_status,
        page_spec_conformance_status,
    }:
        browser_status = "fail"
    else:
        browser_status = "pass"
    return (
        browser_execution_status,
        page_spec_conformance_status,
        browser_status,
    )


def run_real_browser_case_audit(
    *,
    package_root: Path,
    output_root: Path,
    run_id: str,
    case_index: int,
    case_id: str,
    evidence_scope: str,
    source_case_summary_identity: Mapping[str, object] | None = None,
    timeout_ms: int = 5_000,
    backend_factory: BackendFactory | None = None,
) -> dict[str, object]:
    """Execute one exact ResultPackage through the accepted browser authorities."""

    if evidence_scope not in EVIDENCE_SCOPES:
        raise Phase4BrowserAcceptanceError("browser evidence scope is unsupported")
    if (
        evidence_scope != "historical_synthetic_canary"
        and source_case_summary_identity is None
    ):
        raise Phase4BrowserAcceptanceError(
            "canonical browser evidence requires a source case summary"
        )
    if (
        not isinstance(case_index, int)
        or isinstance(case_index, bool)
        or case_index <= 0
    ):
        raise Phase4BrowserAcceptanceError("case_index must be positive")
    selected_run_id = _text(run_id, "run_id")
    selected_case_id = _text(case_id, "case_id")
    destination = Path(output_root).resolve(strict=False)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir() or any(
            destination.iterdir()
        ):
            raise Phase4BrowserAcceptanceError(
                "browser output root must be new or empty"
            )
    else:
        destination.mkdir(parents=True, exist_ok=False)
    package = _package_binding(package_root)
    root = Path(package_root).resolve(strict=True)
    context_value = _load_json(
        root / "internal/agent_context.json",
        "result package AgentContext",
    )
    page_value = _load_json(
        root / "internal/page_spec.json",
        "result package PageSpec",
    )
    context = _agent_context_from_dict(context_value)
    page_spec = _page_spec_from_dict(page_value)
    if (
        package["page_id"] != page_spec.page_id
        or page_spec.summary != context.requirement_summary
        or page_spec.target_device != context.target_device
        or page_spec.page_type != context.task_type
    ):
        raise Phase4BrowserAcceptanceError(
            "result package context/PageSpec binding drifted"
        )
    render = RenderResult(
        page_id=page_spec.page_id,
        output_dir=root / "page",
        index_html=root / "page/index.html",
        styles_css=root / "page/styles.css",
        app_js=root / "page/app.js",
        render_manifest=root / "page/render_manifest.json",
    )
    requirement_view = project_requirement_view(context)
    acceptance_plan = compile_acceptance_plan(requirement_view)
    binding = compile_acceptance_binding(
        requirement_view,
        acceptance_plan,
        page_spec,
        render,
    )
    viewport = VIEWPORTS.get(
        page_spec.target_device,
        VIEWPORTS["desktop"],
    )
    screenshot_path = destination / "browser_screenshot.png"
    factory = backend_factory or _default_backend_factory
    backend = factory(binding, screenshot_path, viewport)
    started_at = datetime.now(timezone.utc).isoformat()
    report = execute_acceptance_binding_plan(
        binding,
        render.index_html.resolve(strict=True).as_uri(),
        backend=backend,
        timeout_ms=timeout_ms,
    )
    completed_at = datetime.now(timezone.utc).isoformat()
    report.validate_against(binding)
    facts = _backend_facts(backend)
    console_messages = facts.get("console_messages", [])
    page_errors = facts.get("page_errors", [])
    interactions = facts.get("interactions", [])
    if not isinstance(console_messages, list) or not isinstance(
        page_errors,
        list,
    ) or not isinstance(interactions, list):
        raise Phase4BrowserAcceptanceError("browser observation facts are invalid")
    counts = {
        status: sum(item.status == status for item in report.criteria)
        for status in ("pass", "fail", "unknown", "not_supported")
    }
    console_error_count = sum(
        isinstance(item, Mapping) and item.get("type") == "error"
        for item in console_messages
    )
    screenshot_identity = None
    if screenshot_path.is_file():
        screenshot_identity = _identity(
            screenshot_path.read_bytes(),
            revision="phase4_browser_screenshot.png.v1",
            identity_kind="raw_bytes",
        )
    real_browser_executed = facts.get("browser_started") is True
    (
        browser_execution_status,
        page_spec_conformance_status,
        browser_status,
    ) = _layered_objective_statuses(
        report=report,
        real_browser_executed=real_browser_executed,
        screenshot_captured=facts.get("screenshot_captured") is True,
        page_errors=page_errors,
        console_error_count=console_error_count,
    )
    automation_reliable = bool(
        real_browser_executed
        and facts.get("screenshot_captured") is True
        and counts["unknown"] == 0
    )
    page_spec_identity = _identity(
        page_value,
        revision=str(page_spec.schema_version),
    )
    acceptance_plan_identity = _identity(
        acceptance_plan.to_dict(),
        revision=acceptance_plan.schema_version,
    )
    acceptance_binding_identity = _identity(
        binding.to_dict(),
        revision=binding.schema_version,
    )
    browser_execution_report_identity = _identity(
        report.canonical_json_bytes(),
        revision=report.schema_version,
        identity_kind="raw_bytes",
    )
    _write_once(
        destination / "requirement_view.json",
        requirement_view.canonical_json_bytes(),
    )
    _write_once(
        destination / "acceptance_plan.json",
        acceptance_plan.canonical_json_bytes(),
    )
    _write_once(
        destination / "acceptance_binding.json",
        binding.canonical_json_bytes(),
    )
    _write_once(
        destination / "browser_execution_report.json",
        report.canonical_json_bytes(),
    )
    semantic_alignment: dict[str, object] = {
        "status": "not_executed",
        "disposition": "blocked_by_objective_failure",
        "review_item_count": 0,
        "request_identity": None,
        "model_generate_calls": 0,
        "automatic_retry_count": 0,
        "claim_boundary": (
            "Phase 4 does not invoke a semantic evaluator; objective browser "
            "or PageSpec-conformance failure cannot be overridden"
        ),
    }
    if browser_status == "pass":
        if screenshot_identity is None:
            raise Phase4BrowserAcceptanceError(
                "passing objective browser audit lacks screenshot evidence"
            )
        semantic_request = build_semantic_alignment_request(
            case_id=selected_case_id,
            acceptance_plan=acceptance_plan,
            binding_plan=binding,
            browser_report=report,
            page_spec=page_spec,
            evidence_identities={
                "acceptance_binding": (
                    "acceptance_binding",
                    acceptance_binding_identity,
                ),
                "acceptance_plan": (
                    "acceptance_plan",
                    acceptance_plan_identity,
                ),
                "browser_execution_report": (
                    "browser_execution_report",
                    browser_execution_report_identity,
                ),
                "browser_screenshot": (
                    "browser_screenshot",
                    screenshot_identity,
                ),
                "page_spec": ("page_spec", page_spec_identity),
                "result_package_manifest": (
                    "result_package_manifest",
                    package["manifest_identity"],
                ),
            },
        )
        _write_once(
            destination / "semantic_alignment_request.json",
            semantic_request.canonical_json_bytes(),
        )
        semantic_alignment = {
            "status": "not_executed",
            "disposition": "needs_semantic_review",
            "review_item_count": len(semantic_request.review_items),
            "request_identity": _identity(
                semantic_request.to_dict(),
                revision=semantic_request.schema_version,
            ),
            "model_generate_calls": 0,
            "automatic_retry_count": 0,
            "claim_boundary": (
                "objective browser execution and PageSpec conformance passed; "
                "abstract requirement alignment remains unexecuted and is not "
                "counted as semantic success"
            ),
        }
    root_value = {
        "schema_version": CASE_AUDIT_SCHEMA_VERSION,
        "harness_revision": BROWSER_HARNESS_REVISION,
        "evidence_scope": evidence_scope,
        "run_id": selected_run_id,
        "case_index": case_index,
        "case_id": selected_case_id,
        "source_case_summary_identity": (
            None
            if source_case_summary_identity is None
            else copy.deepcopy(dict(source_case_summary_identity))
        ),
        "result_package": package,
        "page_spec_identity": page_spec_identity,
        "requirement_view_identity": _identity(
            requirement_view.to_dict(),
            revision=requirement_view.schema_version,
        ),
        "acceptance_plan_identity": acceptance_plan_identity,
        "acceptance_binding_identity": acceptance_binding_identity,
        "browser_execution_report_identity": browser_execution_report_identity,
        "browser": copy.deepcopy(facts.get("browser")),
        "viewport": copy.deepcopy(viewport),
        "interactions": copy.deepcopy(interactions),
        "console_messages": copy.deepcopy(console_messages),
        "page_errors": copy.deepcopy(page_errors),
        "screenshot_identity": screenshot_identity,
        "criteria_counts": counts,
        "browser_status": browser_status,
        "browser_execution_status": browser_execution_status,
        "page_spec_conformance_status": page_spec_conformance_status,
        "semantic_alignment": semantic_alignment,
        "real_browser_executed": real_browser_executed,
        "automation_reliable": automation_reliable,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "claim_boundary": (
            "real browser execution bound to one exact ResultPackage, PageSpec, "
            "AcceptancePlan, and AcceptanceBindingPlan; synthetic/historical "
            "scope is not Phase 4 ten-case final browser-quality evidence"
            if evidence_scope == "historical_synthetic_canary"
            else (
                "real browser execution bound to one exact canonical Phase 4 "
                "case; browser execution and PageSpec conformance are objective "
                "evidence, while semantic alignment remains separately pending"
            )
        ),
    }
    audit = {
        **root_value,
        "audit_identity": _identity(
            root_value,
            revision=CASE_AUDIT_SCHEMA_VERSION,
        ),
    }
    validate_real_browser_case_audit(audit)
    _write_once(
        destination / "case_browser_audit.json",
        _canonical(audit),
    )
    return audit


def validate_real_browser_case_audit(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4BrowserAcceptanceError("browser case audit must be an object")
    data = copy.deepcopy(dict(value))
    if set(data) != _CASE_AUDIT_KEYS:
        raise Phase4BrowserAcceptanceError("browser case audit exact keys drifted")
    if (
        data["schema_version"] != CASE_AUDIT_SCHEMA_VERSION
        or data["harness_revision"] != BROWSER_HARNESS_REVISION
        or data["evidence_scope"] not in EVIDENCE_SCOPES
        or data["browser_status"] not in {"pass", "fail", "unknown"}
        or data["browser_execution_status"] not in {"pass", "fail", "unknown"}
        or data["page_spec_conformance_status"]
        not in {"pass", "fail", "unknown"}
        or type(data["real_browser_executed"]) is not bool
        or type(data["automation_reliable"]) is not bool
    ):
        raise Phase4BrowserAcceptanceError("browser case audit root drifted")
    _text(data["run_id"], "browser audit run_id")
    _text(data["case_id"], "browser audit case_id")
    if (
        not isinstance(data["case_index"], int)
        or isinstance(data["case_index"], bool)
        or data["case_index"] <= 0
    ):
        raise Phase4BrowserAcceptanceError("browser audit case_index is invalid")
    if data["evidence_scope"] != "historical_synthetic_canary" and not isinstance(
        data["source_case_summary_identity"],
        Mapping,
    ):
        raise Phase4BrowserAcceptanceError(
            "canonical browser audit is not case-summary bound"
        )
    if data["source_case_summary_identity"] is not None:
        _identity_record(
            data["source_case_summary_identity"],
            "source case summary",
        )
    if not isinstance(data["result_package"], Mapping):
        raise Phase4BrowserAcceptanceError(
            "browser audit result package binding is invalid"
        )
    package = data["result_package"]
    for key in ("manifest_identity", "inventory_identity"):
        _identity_record(package.get(key), f"result package {key}")
    for key in (
        "page_spec_identity",
        "requirement_view_identity",
        "acceptance_plan_identity",
        "acceptance_binding_identity",
        "browser_execution_report_identity",
    ):
        _identity_record(data[key], key)
    if data["automation_reliable"] is True:
        _identity_record(data["screenshot_identity"], "browser screenshot")
    counts = data["criteria_counts"]
    if (
        not isinstance(counts, Mapping)
        or set(counts) != {"pass", "fail", "unknown", "not_supported"}
        or any(
            not isinstance(item, int) or isinstance(item, bool) or item < 0
            for item in counts.values()
        )
    ):
        raise Phase4BrowserAcceptanceError(
            "browser audit criterion counts are invalid"
        )
    if data["browser_status"] == "pass" and (
        counts["fail"] or counts["unknown"]
    ):
        raise Phase4BrowserAcceptanceError(
            "passing browser audit contains fail or unknown criteria"
        )
    layer_statuses = {
        data["browser_execution_status"],
        data["page_spec_conformance_status"],
    }
    expected_browser_status = (
        "unknown"
        if "unknown" in layer_statuses
        else "fail"
        if "fail" in layer_statuses
        else "pass"
    )
    if data["browser_status"] != expected_browser_status:
        raise Phase4BrowserAcceptanceError(
            "browser aggregate status does not match objective layers"
        )
    semantic = data["semantic_alignment"]
    if (
        not isinstance(semantic, Mapping)
        or set(semantic)
        != {
            "status",
            "disposition",
            "review_item_count",
            "request_identity",
            "model_generate_calls",
            "automatic_retry_count",
            "claim_boundary",
        }
        or semantic.get("status") != "not_executed"
        or semantic.get("model_generate_calls") != 0
        or semantic.get("automatic_retry_count") != 0
        or not isinstance(semantic.get("review_item_count"), int)
        or isinstance(semantic.get("review_item_count"), bool)
        or semantic["review_item_count"] < 0
        or not isinstance(semantic.get("claim_boundary"), str)
        or not semantic["claim_boundary"].strip()
    ):
        raise Phase4BrowserAcceptanceError(
            "semantic alignment phase boundary drifted"
        )
    if data["browser_status"] == "pass":
        if (
            semantic.get("disposition") != "needs_semantic_review"
            or semantic["review_item_count"] <= 0
        ):
            raise Phase4BrowserAcceptanceError(
                "passing objective audit lacks pending semantic review"
            )
        _identity_record(
            semantic.get("request_identity"),
            "semantic alignment request",
        )
    elif (
        semantic.get("disposition") != "blocked_by_objective_failure"
        or semantic["review_item_count"] != 0
        or semantic.get("request_identity") is not None
    ):
        raise Phase4BrowserAcceptanceError(
            "failed objective audit must block semantic review"
        )
    root = {key: item for key, item in data.items() if key != "audit_identity"}
    if data["audit_identity"] != _identity(
        root,
        revision=CASE_AUDIT_SCHEMA_VERSION,
    ):
        raise Phase4BrowserAcceptanceError("browser case audit identity drifted")
    return data


def build_browser_final_summary(
    *,
    flow_summary: Mapping[str, object],
    case_audits: tuple[Mapping[str, object], ...],
) -> dict[str, object]:
    """Build a ten-case browser table without inventing missing executions."""

    if (
        flow_summary.get("schema_version")
        != "req2web.phase4.canonical_full_flow.v1.summary"
        or flow_summary.get("all_cases_complete") is not True
        or flow_summary.get("completed_case_count") != FINAL_CASE_COUNT
    ):
        raise Phase4BrowserAcceptanceError(
            "final browser summary requires a complete ten-case flow"
        )
    run_id = _text(flow_summary.get("run_id"), "final browser run_id")
    source_rows = flow_summary.get("case_summaries")
    if not isinstance(source_rows, list) or len(source_rows) != FINAL_CASE_COUNT:
        raise Phase4BrowserAcceptanceError(
            "final browser source case summaries are invalid"
        )

    audits_by_index: dict[int, dict[str, object]] = {}
    for value in case_audits:
        audit = validate_real_browser_case_audit(value)
        case_index = audit["case_index"]
        if (
            not isinstance(case_index, int)
            or isinstance(case_index, bool)
            or case_index in audits_by_index
            or audit["run_id"] != run_id
            or audit["evidence_scope"]
            not in {"phase4_canonical_canary", "phase4_canonical_full"}
        ):
            raise Phase4BrowserAcceptanceError(
                "final browser case audit binding is invalid"
            )
        audits_by_index[case_index] = audit

    rows: list[dict[str, object]] = []
    for expected_index, source_value in enumerate(source_rows, start=1):
        if not isinstance(source_value, Mapping):
            raise Phase4BrowserAcceptanceError(
                "final browser source case summary must be an object"
            )
        source = copy.deepcopy(dict(source_value))
        if (
            source.get("case_index") != expected_index
            or not isinstance(source.get("case_id"), str)
        ):
            raise Phase4BrowserAcceptanceError(
                "final browser source case order drifted"
            )
        source_identity = _identity_record(
            source.get("case_summary_identity"),
            "final browser source case summary",
        )
        audit = audits_by_index.pop(expected_index, None)
        if audit is None:
            disposition = (
                "not_executed_missing_required_audit"
                if source.get("downstream_delivery_success") is True
                else "not_executed_no_successful_result_package"
            )
            rows.append(
                {
                    "case_index": expected_index,
                    "case_id": source["case_id"],
                    "source_case_summary_identity": source_identity,
                    "model_case_status": source.get("status"),
                    "downstream_delivery_success": (
                        source.get("downstream_delivery_success") is True
                    ),
                    "browser_disposition": disposition,
                    "real_browser_executed": False,
                    "browser_execution_status": "not_executed",
                    "page_spec_conformance_status": "not_executed",
                    "semantic_alignment_status": "not_executed",
                    "automation_reliable": False,
                    "case_browser_audit_identity": None,
                    "failure": copy.deepcopy(source.get("failure")),
                }
            )
            continue
        if (
            audit["case_id"] != source["case_id"]
            or _canonical(audit["source_case_summary_identity"])
            != _canonical(source_identity)
        ):
            raise Phase4BrowserAcceptanceError(
                "final browser audit source binding drifted"
            )
        rows.append(
            {
                "case_index": expected_index,
                "case_id": source["case_id"],
                "source_case_summary_identity": source_identity,
                "model_case_status": source.get("status"),
                "downstream_delivery_success": (
                    source.get("downstream_delivery_success") is True
                ),
                "browser_disposition": "executed",
                "real_browser_executed": audit["real_browser_executed"],
                "browser_execution_status": audit[
                    "browser_execution_status"
                ],
                "page_spec_conformance_status": audit[
                    "page_spec_conformance_status"
                ],
                "semantic_alignment_status": audit[
                    "semantic_alignment"
                ]["status"],
                "automation_reliable": audit["automation_reliable"],
                "case_browser_audit_identity": copy.deepcopy(
                    audit["audit_identity"]
                ),
                "failure": None,
            }
        )
    if audits_by_index:
        raise Phase4BrowserAcceptanceError(
            "final browser audits include an unknown case"
        )

    executed = [
        row for row in rows if row["browser_disposition"] == "executed"
    ]
    missing_required = [
        row
        for row in rows
        if row["browser_disposition"]
        == "not_executed_missing_required_audit"
    ]
    browser_failures = [
        row
        for row in executed
        if row["browser_execution_status"] != "pass"
        or row["page_spec_conformance_status"] != "pass"
        or row["automation_reliable"] is not True
    ]
    if missing_required:
        status = "browser_audit_incomplete"
    elif browser_failures:
        status = "browser_audit_complete_with_failures"
    elif len(executed) < FINAL_CASE_COUNT:
        status = "browser_audit_complete_with_non_executable_cases"
    else:
        status = "browser_audit_complete_all_cases"
    counts = {
        "case_count": FINAL_CASE_COUNT,
        "real_browser_executed_count": len(executed),
        "browser_execution_pass_count": sum(
            row["browser_execution_status"] == "pass"
            for row in executed
        ),
        "page_spec_conformance_pass_count": sum(
            row["page_spec_conformance_status"] == "pass"
            for row in executed
        ),
        "automation_reliable_count": sum(
            row["automation_reliable"] is True for row in executed
        ),
        "not_executed_no_successful_result_package_count": sum(
            row["browser_disposition"]
            == "not_executed_no_successful_result_package"
            for row in rows
        ),
        "not_executed_missing_required_audit_count": len(missing_required),
        "semantic_alignment_executed_count": 0,
    }
    root = {
        "schema_version": FINAL_BROWSER_SUMMARY_SCHEMA_VERSION,
        "harness_revision": BROWSER_HARNESS_REVISION,
        "run_id": run_id,
        "flow_summary_identity": copy.deepcopy(
            flow_summary.get("summary_identity")
        ),
        "status": status,
        "counts": counts,
        "case_rows": rows,
        "all_executed_browser_checks_pass": not browser_failures,
        "all_ten_cases_browser_executed": (
            len(executed) == FINAL_CASE_COUNT
        ),
        "semantic_alignment_executed": False,
        "claim_boundary": (
            "objective real-browser execution and PageSpec conformance only; "
            "cases without a successful model ResultPackage remain explicitly "
            "not executed, and semantic alignment remains unexecuted"
        ),
    }
    return {
        **root,
        "summary_identity": _identity(
            root,
            revision=FINAL_BROWSER_SUMMARY_SCHEMA_VERSION,
        ),
    }


def write_browser_final_summary(
    *,
    flow_result_root: Path,
    case_audit_paths: tuple[Path, ...],
    output_path: Path,
) -> dict[str, object]:
    flow_summary = _load_json(
        Path(flow_result_root).resolve(strict=True) / "flow_summary.json",
        "canonical flow summary",
    )
    audits = tuple(
        _load_json(path.resolve(strict=True), "browser case audit")
        for path in case_audit_paths
    )
    summary = build_browser_final_summary(
        flow_summary=flow_summary,
        case_audits=audits,
    )
    _write_once(output_path.resolve(strict=False), _canonical(summary))
    return summary


def build_browser_canary_receipt(
    *,
    flow_result_root: Path,
    case_audit_paths: tuple[Path, ...],
    output_path: Path,
) -> dict[str, object]:
    """Bind three real-browser case audits to one canonical flow run."""

    if len(case_audit_paths) != CANARY_CASE_COUNT:
        raise Phase4BrowserAcceptanceError(
            "browser canary requires exactly three case audits"
        )
    flow_root = Path(flow_result_root).resolve(strict=True)
    policy = _load_json(flow_root / "flow_policy.json", "flow policy")
    run_id = _text(policy.get("run_id"), "flow policy run_id")
    policy_identity = policy.get("policy_identity")
    if not isinstance(policy_identity, Mapping):
        raise Phase4BrowserAcceptanceError("flow policy identity is absent")
    rows: list[dict[str, object]] = []
    for index, audit_path in enumerate(case_audit_paths, start=1):
        summary = _load_json(
            flow_root / "case-summaries" / f"{index:02d}.json",
            f"case {index} summary",
        )
        if (
            summary.get("schema_version") != CASE_SUMMARY_SCHEMA_VERSION
            or not isinstance(summary.get("case_summary_identity"), Mapping)
        ):
            raise Phase4BrowserAcceptanceError(
                f"case {index} summary is not canonical"
            )
        audit = validate_real_browser_case_audit(
            _load_json(audit_path, f"case {index} browser audit")
        )
        if (
            audit["evidence_scope"] != "phase4_canonical_canary"
            or audit["run_id"] != run_id
            or audit["case_index"] != index
            or audit["case_id"] != summary.get("case_id")
            or audit["source_case_summary_identity"]
            != summary["case_summary_identity"]
        ):
            raise Phase4BrowserAcceptanceError(
                f"case {index} browser audit binding drifted"
            )
        rows.append(
            {
                "case_index": index,
                "case_id": audit["case_id"],
                "case_summary_identity": summary["case_summary_identity"],
                "case_browser_audit_identity": audit["audit_identity"],
                "result_package": audit["result_package"],
                "page_spec_identity": audit["page_spec_identity"],
                "acceptance_plan_identity": audit["acceptance_plan_identity"],
                "acceptance_binding_identity": audit[
                    "acceptance_binding_identity"
                ],
                "browser_execution_report_identity": audit[
                    "browser_execution_report_identity"
                ],
                "screenshot_identity": audit["screenshot_identity"],
                "browser_status": audit["browser_status"],
                "browser_execution_status": audit[
                    "browser_execution_status"
                ],
                "page_spec_conformance_status": audit[
                    "page_spec_conformance_status"
                ],
                "semantic_alignment_status": audit[
                    "semantic_alignment"
                ]["status"],
                "semantic_alignment_disposition": audit[
                    "semantic_alignment"
                ]["disposition"],
                "semantic_alignment_request_identity": audit[
                    "semantic_alignment"
                ]["request_identity"],
                "real_browser_executed": audit["real_browser_executed"],
                "automation_reliable": audit["automation_reliable"],
            }
        )
    real_browser_executed = all(
        row["real_browser_executed"] is True for row in rows
    )
    automation_reliable = all(
        row["automation_reliable"] is True for row in rows
    )
    all_canary_browser_execution_pass = all(
        row["browser_execution_status"] == "pass" for row in rows
    )
    all_canary_page_spec_conformance_pass = all(
        row["page_spec_conformance_status"] == "pass" for row in rows
    )
    semantic_alignment_executed = any(
        row["semantic_alignment_status"] != "not_executed" for row in rows
    )
    root = {
        "schema_version": CANARY_RECEIPT_SCHEMA_VERSION,
        "harness_revision": BROWSER_HARNESS_REVISION,
        "run_id": run_id,
        "flow_policy_identity": copy.deepcopy(dict(policy_identity)),
        "canary_case_count": CANARY_CASE_COUNT,
        "case_audits": rows,
        "real_browser_executed": real_browser_executed,
        "automation_reliable": automation_reliable,
        "all_canary_browser_execution_pass": (
            all_canary_browser_execution_pass
        ),
        "all_canary_page_spec_conformance_pass": (
            all_canary_page_spec_conformance_pass
        ),
        "semantic_alignment_executed": semantic_alignment_executed,
        "continuation_allowed": bool(
            real_browser_executed
            and automation_reliable
            and all_canary_browser_execution_pass
            and all_canary_page_spec_conformance_pass
            and not semantic_alignment_executed
        ),
        "scripted_acceptance_is_browser_evidence": False,
        "claim_boundary": (
            "three-case objective real-browser and PageSpec-conformance "
            "canary; semantic alignment remains unexecuted and this is not "
            "the final ten-case browser evidence"
        ),
    }
    receipt = {
        **root,
        "receipt_identity": _identity(
            root,
            revision=CANARY_RECEIPT_SCHEMA_VERSION,
        ),
    }
    validate_browser_canary_receipt(
        flow_result_root=flow_root,
        receipt=receipt,
    )
    _write_once(Path(output_path).resolve(strict=False), _canonical(receipt))
    return receipt


def validate_browser_canary_receipt(
    *,
    flow_result_root: Path,
    receipt: object,
) -> dict[str, object]:
    if not isinstance(receipt, Mapping):
        raise Phase4BrowserAcceptanceError(
            "browser canary receipt must be an object"
        )
    data = copy.deepcopy(dict(receipt))
    expected = {
        "schema_version",
        "harness_revision",
        "run_id",
        "flow_policy_identity",
        "canary_case_count",
        "case_audits",
        "real_browser_executed",
        "automation_reliable",
        "all_canary_browser_execution_pass",
        "all_canary_page_spec_conformance_pass",
        "semantic_alignment_executed",
        "continuation_allowed",
        "scripted_acceptance_is_browser_evidence",
        "claim_boundary",
        "receipt_identity",
    }
    if set(data) != expected:
        raise Phase4BrowserAcceptanceError(
            "browser canary receipt exact keys drifted"
        )
    policy = _load_json(
        Path(flow_result_root).resolve(strict=True) / "flow_policy.json",
        "flow policy",
    )
    rows = data["case_audits"]
    if (
        data["schema_version"] != CANARY_RECEIPT_SCHEMA_VERSION
        or data["harness_revision"] != BROWSER_HARNESS_REVISION
        or data["run_id"] != policy.get("run_id")
        or data["flow_policy_identity"] != policy.get("policy_identity")
        or data["canary_case_count"] != CANARY_CASE_COUNT
        or not isinstance(rows, list)
        or len(rows) != CANARY_CASE_COUNT
        or data["scripted_acceptance_is_browser_evidence"] is not False
    ):
        raise Phase4BrowserAcceptanceError("browser canary receipt root drifted")
    for index, row in enumerate(rows, start=1):
        if (
            not isinstance(row, Mapping)
            or row.get("case_index") != index
            or row.get("real_browser_executed") is not True
            or row.get("automation_reliable") is not True
            or row.get("browser_status") != "pass"
            or row.get("browser_execution_status") != "pass"
            or row.get("page_spec_conformance_status") != "pass"
            or row.get("semantic_alignment_status") != "not_executed"
            or row.get("semantic_alignment_disposition")
            != "needs_semantic_review"
        ):
            raise Phase4BrowserAcceptanceError(
                "browser canary case did not pass reliably"
            )
        for key in (
            "case_summary_identity",
            "case_browser_audit_identity",
            "page_spec_identity",
            "acceptance_plan_identity",
            "acceptance_binding_identity",
            "browser_execution_report_identity",
            "screenshot_identity",
            "semantic_alignment_request_identity",
        ):
            _identity_record(row.get(key), f"browser canary {key}")
        summary = _load_json(
            Path(flow_result_root).resolve(strict=True)
            / "case-summaries"
            / f"{index:02d}.json",
            f"case {index} summary",
        )
        if (
            row.get("case_id") != summary.get("case_id")
            or row.get("case_summary_identity")
            != summary.get("case_summary_identity")
        ):
            raise Phase4BrowserAcceptanceError(
                "browser canary case summary binding drifted"
            )
    if (
        data["real_browser_executed"] is not True
        or data["automation_reliable"] is not True
        or data["all_canary_browser_execution_pass"] is not True
        or data["all_canary_page_spec_conformance_pass"] is not True
        or data["semantic_alignment_executed"] is not False
        or data["continuation_allowed"] is not True
    ):
        raise Phase4BrowserAcceptanceError(
            "browser canary receipt does not allow continuation"
        )
    root = {
        key: item for key, item in data.items() if key != "receipt_identity"
    }
    if data["receipt_identity"] != _identity(
        root,
        revision=CANARY_RECEIPT_SCHEMA_VERSION,
    ):
        raise Phase4BrowserAcceptanceError(
            "browser canary receipt identity drifted"
        )
    return data


__all__ = [
    "BROWSER_HARNESS_REVISION",
    "CANARY_RECEIPT_SCHEMA_VERSION",
    "CASE_AUDIT_SCHEMA_VERSION",
    "EVIDENCE_SCOPES",
    "FINAL_BROWSER_SUMMARY_SCHEMA_VERSION",
    "Phase4BrowserAcceptanceError",
    "Phase4PlaywrightBrowserBackend",
    "build_browser_final_summary",
    "build_browser_canary_receipt",
    "run_real_browser_case_audit",
    "validate_browser_canary_receipt",
    "validate_real_browser_case_audit",
    "validate_result_package_binding",
    "write_browser_final_summary",
]
