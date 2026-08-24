"""Build and validate the read-only Req2Web Inspector replay bundle.

The builder consumes only frozen Phase 5 Path 2 engineering artifacts. It
does not invoke a model, launch a browser, rebuild F1-F4, or change any source
evidence. The generated bundle is a curated, self-contained projection for
review and demonstration.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from typing import Any, Mapping

from req2web_evaluation.phase5_publication_semantic import (
    load_phase5_publication_semantic_manifest,
    validate_phase5_publication_semantic_summary,
)
from req2web_runtime.phase5_publication_browser import (
    replay_phase5_publication_browser_result,
)
from req2web_runtime.phase5_result_return import validate_phase5_result_return
from req2web_rag.retrieval_comparison import (
    RETRIEVAL_COMPARISON_SCHEMA_VERSION,
    validate_retrieval_comparison,
)
from req2web_rag.retrieval_experiment import (
    RETRIEVAL_EXPERIMENT_SCHEMA_VERSION,
    validate_retrieval_experiment,
)
from req2web_inspector.guispector_sidecar import (
    GUISPECTOR_EVALUATION_SCHEMA_VERSION,
    GUISpectorSidecarError,
    build_guispector_evaluation,
    validate_guispector_evaluation,
)


PHASE6_REPLAY_MANIFEST_SCHEMA_VERSION = "req2web.phase6.reviewer_replay.v2"
PHASE6_REVIEWER_CASE_SCHEMA_VERSION = "req2web.phase6.reviewer_case.v1"
PHASE6_MATERIALS_SCHEMA_VERSION = "req2web.phase6.material_inventory.v2"

ROW_COUNT = 12
HISTORICAL_FIRST_PASS_COUNT = 9
HISTORICAL_FAILED_CLOSED_INDICES = (2, 5, 9)
GENERATION_CALL_COUNT = 48
SEMANTIC_CALL_COUNT = 12
SEMANTIC_CRITERION_COUNT = 24
BROWSER_INTERACTION_COUNT = 36

_NODE_IDS = ("F1", "F2", "F3", "F4")
_PACKAGE_FILES = (
    "internal/page_spec.json",
    "page/app.js",
    "page/index.html",
    "page/render_manifest.json",
    "page/styles.css",
)
_BROWSER_FILES = (
    "browser_execution_report.json",
    "browser_screenshot.png",
    "case_browser_audit.json",
)


class Phase6ReplayError(ValueError):
    """Raised when a replay source or generated bundle fails closed."""


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase6ReplayError("value is not canonical JSON") from exc


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Phase6ReplayError(f"{name} must be an object")
    return dict(value)


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise Phase6ReplayError(f"{name} must be a list")
    return value


def _read_json(path: Path, name: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase6ReplayError(f"{name} is unavailable")
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase6ReplayError(f"{name} is not UTF-8 JSON") from exc
    return _mapping(value, name)


def _write_bytes(path: Path, raw: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise Phase6ReplayError(f"bundle target already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _write_json(path: Path, value: object) -> None:
    _write_bytes(path, _canonical_json_bytes(value))


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or ".." in path.parts
        or str(path) != value
    ):
        raise Phase6ReplayError("bundle path is not a safe POSIX relative path")
    return value


def _copy_regular_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise Phase6ReplayError(f"source file is unavailable: {source}")
    _write_bytes(destination, source.read_bytes())


def _tar_member_bytes(archive: tarfile.TarFile, member_name: str) -> bytes:
    _safe_relative_path(member_name)
    try:
        member = archive.getmember(member_name)
    except KeyError as exc:
        raise Phase6ReplayError(
            f"required publication return member is missing: {member_name}"
        ) from exc
    if not member.isfile() or member.issym() or member.islnk():
        raise Phase6ReplayError(
            f"publication return member is not a regular file: {member_name}"
        )
    stream = archive.extractfile(member)
    if stream is None:
        raise Phase6ReplayError(
            f"publication return member is unreadable: {member_name}"
        )
    return stream.read()


def _json_from_bytes(raw: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase6ReplayError(f"{name} is not UTF-8 JSON") from exc
    return _mapping(value, name)


def _validate_exact_counts(
    *,
    amended: Mapping[str, Any],
    browser: Mapping[str, Any],
    semantic: Mapping[str, Any],
) -> None:
    amended_counts = _mapping(amended.get("counts"), "amended summary counts")
    browser_counts = _mapping(browser.get("counts"), "browser summary counts")
    semantic_counts = _mapping(semantic.get("counts"), "semantic summary counts")
    expected_amended = {
        "delivery_evidence_available_count": ROW_COUNT,
        "historical_downstream_first_pass_success_count": HISTORICAL_FIRST_PASS_COUNT,
        "f4_policy_revalidated_row_count": len(HISTORICAL_FAILED_CLOSED_INDICES),
        "model_generate_calls_added": 0,
        "automatic_retry_count_added": 0,
    }
    expected_browser = {
        "real_browser_executed_count": ROW_COUNT,
        "browser_execution_pass_count": ROW_COUNT,
        "page_spec_conformance_pass_count": ROW_COUNT,
    }
    expected_semantic = {
        "row_count": ROW_COUNT,
        "accepted_criterion_count": SEMANTIC_CRITERION_COUNT,
        "generate_started_count": SEMANTIC_CALL_COUNT,
        "accepted_semantic_result_count": ROW_COUNT,
        "supported_count": SEMANTIC_CRITERION_COUNT,
        "automatic_retry_count": 0,
    }
    for name, counts, expected in (
        ("amended summary", amended_counts, expected_amended),
        ("browser summary", browser_counts, expected_browser),
        ("semantic summary", semantic_counts, expected_semantic),
    ):
        for key, expected_value in expected.items():
            if counts.get(key) != expected_value:
                raise Phase6ReplayError(
                    f"{name} {key} drifted: expected {expected_value!r}"
                )
    if amended.get("historical_result_overwritten") is not False:
        raise Phase6ReplayError("historical Phase 5 result must remain immutable")
    if browser.get("all_objective_browser_checks_pass") is not True:
        raise Phase6ReplayError("objective browser evidence is not fully passed")
    if semantic.get("formal_evaluation_executed") is not False:
        raise Phase6ReplayError("semantic summary opened formal evaluation")
    if semantic.get("formal_quality_claimed") is not False:
        raise Phase6ReplayError("semantic summary claims formal quality")
    if semantic.get("h1_or_gold_access") is not False:
        raise Phase6ReplayError("semantic summary accessed H1 or gold")


def _source_bundle(
    *,
    result_tar_path: Path,
    result_manifest_path: Path,
    revalidation_root: Path,
    browser_root: Path,
    semantic_manifest_path: Path,
    semantic_summary_path: Path,
    semantic_results_root: Path,
) -> dict[str, Any]:
    return_manifest = validate_phase5_result_return(
        tar_path=result_tar_path,
        manifest_path=result_manifest_path,
    ).to_dict()
    browser = replay_phase5_publication_browser_result(
        revalidation_root=revalidation_root,
        browser_root=browser_root,
    )
    semantic_manifest = load_phase5_publication_semantic_manifest(
        path=semantic_manifest_path,
        revalidation_root=revalidation_root,
        browser_root=browser_root,
    )
    semantic_value = _read_json(semantic_summary_path, "semantic summary")
    semantic = validate_phase5_publication_semantic_summary(
        value=semantic_value,
        manifest=semantic_manifest,
        revalidation_root=revalidation_root,
        browser_root=browser_root,
        results_root=semantic_results_root,
    )
    amended = _read_json(
        revalidation_root / "amended_summary.json",
        "amended policy summary",
    )
    _validate_exact_counts(amended=amended, browser=browser, semantic=semantic)
    return {
        "return_manifest": return_manifest,
        "amended": amended,
        "browser": browser,
        "semantic_manifest": semantic_manifest,
        "semantic": semantic,
    }


def _rows_by_index(
    value: Mapping[str, Any],
    name: str,
) -> dict[int, dict[str, Any]]:
    rows = _list(value.get("row_results"), f"{name} rows")
    if len(rows) != ROW_COUNT:
        raise Phase6ReplayError(f"{name} must contain twelve rows")
    indexed: dict[int, dict[str, Any]] = {}
    for raw in rows:
        row = _mapping(raw, f"{name} row")
        index = row.get("execution_index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise Phase6ReplayError(f"{name} row has invalid execution index")
        if index in indexed:
            raise Phase6ReplayError(f"{name} row execution index is duplicated")
        indexed[index] = row
    if sorted(indexed) != list(range(1, ROW_COUNT + 1)):
        raise Phase6ReplayError(f"{name} execution order drifted")
    return indexed


def _require_same_row(
    index: int,
    *rows: Mapping[str, Any],
) -> tuple[str, str, str]:
    identities = {
        (
            row.get("row_id"),
            row.get("case_id"),
            row.get("condition_id"),
        )
        for row in rows
    }
    if len(identities) != 1:
        raise Phase6ReplayError(f"row {index} source bindings disagree")
    row_id, case_id, condition_id = identities.pop()
    if not all(isinstance(item, str) and item for item in (row_id, case_id, condition_id)):
        raise Phase6ReplayError(f"row {index} has an invalid identity")
    return row_id, case_id, condition_id


def _case_record(
    *,
    index: int,
    amended_row: Mapping[str, Any],
    browser_row: Mapping[str, Any],
    semantic_row: Mapping[str, Any],
    historical_result: Mapping[str, Any],
    historical_result_sha256: str,
) -> dict[str, Any]:
    row_id, case_id, condition_id = _require_same_row(
        index,
        amended_row,
        browser_row,
        semantic_row,
    )
    historical_pass = amended_row.get("historical_downstream_first_pass_success")
    if not isinstance(historical_pass, bool):
        raise Phase6ReplayError(f"row {index} historical status is invalid")
    expected_failure = index in HISTORICAL_FAILED_CLOSED_INDICES
    if historical_pass is expected_failure:
        raise Phase6ReplayError(f"row {index} historical first-pass status drifted")
    failure = historical_result.get("failure")
    if expected_failure:
        failure_map = _mapping(failure, f"row {index} historical failure")
        if (
            historical_result.get("status") != "failed_closed"
            or failure_map.get("failure_stage") != "F4"
            or failure_map.get("failure_code") != "node_failed_closed"
        ):
            raise Phase6ReplayError(f"row {index} historical failure location drifted")
        failure_view: dict[str, Any] | None = {
            "historical_status": "failed_closed",
            "failure_stage": "F4",
            "failure_code": "node_failed_closed",
            "preserved": True,
            "zero_model_policy_revalidated": True,
        }
    else:
        if historical_result.get("status") != "delivery_terminal_success":
            raise Phase6ReplayError(f"row {index} historical success status drifted")
        failure_view = None
    verdict_counts = _mapping(
        semantic_row.get("verdict_counts"),
        f"row {index} semantic verdict counts",
    )
    if (
        browser_row.get("real_browser_executed") is not True
        or browser_row.get("browser_execution_status") != "pass"
        or browser_row.get("page_spec_conformance_status") != "pass"
        or semantic_row.get("semantic_result_accepted") is not True
        or verdict_counts.get("supported") != 2
    ):
        raise Phase6ReplayError(f"row {index} terminal evidence drifted")
    case_root = f"cases/{index:02d}"
    artifacts: dict[str, Any] = {
        "requirement": f"{case_root}/requirement.json",
        "evidence_projection": f"{case_root}/evidence_projection.json",
        "node_outputs": {
            node_id: f"{case_root}/nodes/{node_id.lower()}_output.json"
            for node_id in _NODE_IDS
        },
        "node_attempts": {
            node_id: f"{case_root}/nodes/{node_id.lower()}_attempt.json"
            for node_id in _NODE_IDS
        },
        "page_spec": f"{case_root}/package/internal/page_spec.json",
        "browser_audit": f"{case_root}/browser/case_browser_audit.json",
        "browser_execution": f"{case_root}/browser/browser_execution_report.json",
        "browser_screenshot": f"{case_root}/browser/browser_screenshot.png",
        "semantic_result": f"{case_root}/semantic_alignment_result.json",
        "final_page": f"{case_root}/package/page/index.html",
    }
    if expected_failure:
        artifacts["policy_revalidation"] = f"{case_root}/policy_revalidation.json"
    return {
        "schema_version": PHASE6_REVIEWER_CASE_SCHEMA_VERSION,
        "execution_index": index,
        "row_id": row_id,
        "case_id": case_id,
        "condition_id": condition_id,
        "historical_first_pass": {
            "passed": historical_pass,
            "disposition": amended_row.get("disposition"),
            "immutable": True,
        },
        "historical_source": {
            "source_result_sha256": historical_result_sha256,
            "status": historical_result.get("status"),
            "source_kind": historical_result.get("source_kind"),
            "workflow_runtime": historical_result.get("workflow_runtime"),
            "prompt_revision": historical_result.get("prompt_revision"),
            "model_generate_calls": historical_result.get("model_generate_calls"),
            "manual_f1_f4_loop_used": historical_result.get("manual_f1_f4_loop_used"),
            "failure": failure,
        },
        "delivery": {
            "available": amended_row.get("delivery_evidence_available") is True,
            "source": amended_row.get("disposition"),
            "historical_count_rewritten": False,
        },
        "browser": {
            "executed": True,
            "execution_status": "pass",
            "page_spec_conformance_status": "pass",
            "automation_reliable": browser_row.get("automation_reliable") is True,
        },
        "semantic": {
            "accepted": True,
            "criterion_count": semantic_row.get("criterion_count"),
            "verdict_counts": verdict_counts,
            "automatic_retry_count": semantic_row.get("automatic_retry_count"),
        },
        "historical_failure_location": failure_view,
        "node_output_kinds": {
            node_id: (
                "preserved_strict_json_raw"
                if expected_failure and node_id == "F4"
                else "validated_node_output"
            )
            for node_id in _NODE_IDS
        },
        "artifacts": artifacts,
    }


def _material_inventory() -> dict[str, Any]:
    return {
        "schema_version": PHASE6_MATERIALS_SCHEMA_VERSION,
        "public_release_ready": False,
        "blocking_gate": "owner_license_decision_required",
        "license_statement": (
            "This bundle does not grant a software or data license. The repository "
            "has no top-level license file at bundle construction time. Public "
            "redistribution requires an explicit owner license decision."
        ),
        "included_materials": [
            {
                "material": "Inspector HTML, CSS, JavaScript, and replay metadata",
                "origin": "project-authored",
                "redistribution_status": "pending_owner_license_decision",
            },
            {
                "material": "Four English Path 2 cases across three frozen conditions",
                "origin": "project-authored synthetic evaluation material",
                "redistribution_status": "pending_owner_license_decision",
            },
            {
                "material": (
                    "Validated structured node outputs, plus the three preserved "
                    "strict-JSON F4 answers at the historical policy-failure boundary, "
                    "and deterministic pages"
                ),
                "origin": "precomputed engineering evidence",
                "redistribution_status": "pending_owner_license_decision",
            },
            {
                "material": "Local Chrome screenshots and interaction reports",
                "origin": "derived from project-authored generated pages",
                "redistribution_status": "pending_owner_license_decision",
            },
            {
                "material": "Accepted semantic-alignment result JSON",
                "origin": "precomputed descriptive engineering sidecar",
                "redistribution_status": "pending_owner_license_decision",
            },
            {
                "material": "Deterministic BM25, RRF, and TF-IDF comparison report",
                "origin": "project-authored local retrieval diagnostics",
                "redistribution_status": "pending_owner_license_decision",
            },
            {
                "material": (
                    "GUISpector-compatible requirement packet, internal-reference "
                    "boundary, and same-formula metric protocol"
                ),
                "origin": "project-authored read-only interoperability sidecar",
                "redistribution_status": "pending_owner_license_decision",
            },
        ],
        "excluded_materials": [
            "H1 or gold data",
            "RICO or reference-only assets",
            "raw datasets and hidden evidence",
            "model weights and runtime caches",
            (
            "complete AgentContext, prompts, and raw model response bytes other than the three preserved "
                "strict-JSON F4 answers required to explain the fail-closed examples"
            ),
            "credentials, SSH material, server logs, and host inventories",
            "third-party hosted JavaScript, fonts, images, or analytics",
        ],
        "external_runtime_dependencies": [],
        "network_required_after_bundle_creation": False,
        "gpu_required_after_bundle_creation": False,
    }


_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Req2Web Inspector</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="site-header">
    <a class="brand" href="#top" aria-label="Req2Web Inspector home">
      <span class="brand-mark">R2W</span>
      <span><strong>Req2Web</strong><small>Inspector</small></span>
    </a>
    <nav aria-label="Inspector sections">
      <a href="#overview">Overview</a>
      <a href="#intake">Create</a>
      <a href="#run-history">Runs</a>
      <a href="#case-inspector">Evidence</a>
      <a href="#external-verification">Optional check</a>
      <a href="#final-result">Result</a>
    </nav>
    <span class="local-status"><i></i> Local replay</span>
  </header>

  <div class="app-shell" id="top">
    <section class="hero" id="overview">
      <div class="hero-copy">
        <p class="eyebrow">REQ2WEB INSPECTION CONSOLE</p>
        <h1>Trace a requirement into a runnable page.</h1>
        <p class="lede">Use one console to check an irregular requirement, create a model-free draft, run the canonical local model flow when explicitly enabled, inspect every stage, or replay twelve frozen evaluation rows.</p>
        <div class="hero-actions">
          <a class="button" href="#intake">Create a local draft</a>
          <a class="button secondary" href="#case-inspector">Replay frozen evidence</a>
        </div>
      </div>
      <aside class="release-card" aria-label="Current capability">
        <span class="section-kicker">Current capability</span>
        <strong>Replay, drafts, optional canonical generation</strong>
        <p>Deterministic controls call no model. Optional local-Qwen actions are single-flight, zero-retry, and keep requirement advice, F1-F4 generation, browser facts, and semantic acceptance separate.</p>
        <div class="status-line"><i></i> User actions are explicit and local-first</div>
      </aside>
    </section>

    <main>
    <section class="panel intake-panel" id="intake">
      <div class="section-heading">
        <div>
          <span class="section-kicker">New requirement</span>
          <h2 class="module-title">Turn an irregular request into an inspectable result <button class="info-tip" type="button" aria-label="About requirement processing" data-tooltip="Checks the raw request and shows what the system understood. You can create a fast model-free draft or, when the local model route is enabled, run canonical B, F1-F4, composition, delivery, real-browser checks, semantic acceptance, and export.">!</button></h2>
          <p class="muted">Write naturally. The Inspector keeps the raw text, flags uncertainty, and shows its interpretation before or during generation.</p>
        </div>
        <div id="live-capability" class="capability-pill pending">Checking local service</div>
      </div>
      <div class="intake-grid">
        <form id="requirement-form" class="requirement-form">
          <label class="field-label" for="requirement-input">Requirement</label>
          <textarea id="requirement-input" maxlength="12000" rows="7" placeholder="Example: I need a page where field technicians can find an asset, report a problem, see validation errors, and retry after a failed submission."></textarea>
          <div class="form-grid">
            <label><span>Target device</span><select id="target-device-input"><option value="">Let Req2Web infer</option><option value="responsive_web">Responsive web</option><option value="desktop">Desktop</option><option value="mobile">Mobile</option><option value="tablet">Tablet</option><option value="web">Web</option></select></label>
            <label><span>Task type</span><select id="task-type-input"><option value="">Let Req2Web infer</option><option value="web_application">General web application</option><option value="ecommerce">E-commerce</option><option value="location_service">Location service</option><option value="content_platform">Content platform</option><option value="social_communication">Social communication</option><option value="dashboard">Dashboard</option><option value="recognition_tool">Recognition tool</option></select></label>
          </div>
          <label class="field-label" for="constraints-input">Optional constraints <small>one per line</small></label>
          <textarea id="constraints-input" maxlength="6000" rows="3" placeholder="Responsive on phones and desktop&#10;Show clear validation and recovery guidance"></textarea>
          <div class="form-actions">
            <button id="analyze-requirement" class="button secondary" type="button">Check understanding</button>
            <button id="semantic-assist" class="button secondary" type="button" disabled>Run semantic assistant</button>
            <button id="generate-draft" class="button" type="submit">Generate deterministic draft</button>
            <button id="fill-example" class="text-button" type="button">Use an example</button>
          </div>
          <div class="canonical-controls">
            <div><strong class="module-title">Complete local flow <button class="info-tip" type="button" aria-label="About the complete local flow" data-tooltip="Runs the sole canonical requirement adapter and LangGraph F1-F4 route, then the existing renderer, gates, ResultPackage export, real Chrome audit, and semantic acceptance. The low-GPU profile is integration evidence, not formal quality evidence.">!</button></strong><small>One local model job at a time. A 4060 run can take many minutes.</small></div>
            <label><input id="canonical-b-aux" type="checkbox" checked> Requirement advice</label>
            <label><input id="canonical-browser" type="checkbox" checked> Real browser check</label>
            <label><input id="canonical-semantic" type="checkbox" checked> Semantic acceptance</label>
            <button id="generate-canonical" class="button" type="button" disabled>Run complete local flow</button>
          </div>
        </form>
        <aside class="intake-side">
          <article class="compact-module">
            <h3 class="module-title">Requirement diagnostics <button class="info-tip" type="button" aria-label="About requirement diagnostics" data-tooltip="Detects missing actions, extremely short input, device ambiguity, multilingual publication limits, and absent constraints. Findings are advice except for unsafe or empty input.">!</button></h3>
            <p>Fast deterministic checks help messy input fail clearly instead of failing later.</p>
            <span class="module-status available">Available now</span>
          </article>
          <article class="compact-module">
            <h3 class="module-title">Semantic requirement assistant <button class="info-tip" type="button" aria-label="About the semantic requirement assistant" data-tooltip="An optional advisory-only Qwen Agent points out ambiguity, conflicts, missing information, risks, and suggestions after deterministic requirement processing. It saves the raw response first, calls the model at most once, never rewrites canonical B, and never enters F1-F4.">!</button></h3>
            <p>Available only when the repository service is explicitly started with a local Qwen profile. The portable replay and deterministic draft remain model-free.</p>
            <span id="semantic-assist-status" class="module-status unavailable">Not connected</span>
          </article>
          <article class="compact-module">
            <h3 class="module-title">Canonical model flow <button class="info-tip" type="button" aria-label="About full model generation" data-tooltip="Uses canonical B, the shared F1-F4 prompt authority, Phase4RealModelGraphRuntime, registries, deterministic composition, downstream gates, same-input G0 fallback, browser evidence, and a separately accounted semantic sidecar.">!</button></h3>
            <p>The server exposes it only when an exact local model and integrity record are configured. Every run requires an explicit click.</p>
            <span id="canonical-flow-status" class="module-status unavailable">Not connected</span>
          </article>
        </aside>
      </div>
      <div id="intake-result" class="intake-result" aria-live="polite"></div>
    </section>

    <section class="panel history-panel" id="run-history">
      <div class="section-heading">
        <div><span class="section-kicker">Local runs</span><h2 class="module-title">Open any previous run or import a package <button class="info-tip" type="button" aria-label="About local run history" data-tooltip="Lists deterministic drafts, asynchronous canonical runs, and validated package imports. Each record keeps its source facts, stage statuses, failure location, result kind, runnable page, and ZIP download when available.">!</button></h2></div>
        <button id="refresh-runs" class="text-button" type="button">Refresh</button>
      </div>
      <div class="entry-strip" aria-label="Inspector entry points">
        <span><strong>Create</strong> available</span>
        <span><strong>Complete model flow</strong> optional</span>
        <span><strong>Previous runs</strong> available</span>
        <span><strong>Import a package</strong> available</span>
        <span><strong>Frozen replay</strong> available below</span>
      </div>
      <form id="import-form" class="import-form">
        <div class="import-copy"><strong class="module-title">Import a ResultPackage ZIP <button class="info-tip" type="button" aria-label="About package import" data-tooltip="Accepts only a bounded local ZIP whose result-package root passes the existing ResultPackage v1 or v2 validator. Unsafe paths, links, special files, duplicate paths, extra files, hash drift, and oversized archives fail closed.">!</button></strong><small id="import-help">The package is validated locally. Import does not rerun generation, browser checks, or semantic evaluation.</small></div>
        <input id="import-package" class="file-input-hidden" type="file" accept=".zip,application/zip" aria-describedby="import-help import-file-name">
        <div class="import-picker"><label class="button secondary" for="import-package">Choose ZIP</label><span id="import-file-name">No file selected</span></div>
        <button id="import-package-button" class="button secondary" type="submit">Validate and import</button>
      </form>
      <div id="run-list" class="run-list"><p class="muted">Local run history is loading.</p></div>
    </section>

    <section class="metrics" id="metrics" aria-label="Evidence summary"></section>
    <section class="separation-note">
      <span class="notice-label">Accounting boundary</span>
      <div><strong>Three ledgers stay separate.</strong><p>Historical first-pass delivery, objective browser checks, and semantic alignment answer different questions and are never merged into one score.</p></div>
    </section>

    <section class="case-toolbar" id="case-inspector">
      <div class="toolbar-title"><span class="section-kicker">Evidence explorer</span><strong class="module-title">Select one frozen row <button class="info-tip" type="button" aria-label="About the evidence explorer" data-tooltip="Replays one of twelve immutable engineering rows. It does not regenerate, repair, or relabel the historical evidence.">!</button></strong></div>
      <label for="case-select"><span class="sr-only">Evidence row</span><select id="case-select"></select></label>
      <div id="case-badges" class="badges"></div>
    </section>

    <section class="overview-grid">
      <article class="panel">
        <h2 class="module-title">Requirement <button class="info-tip" type="button" aria-label="About requirement evidence" data-tooltip="Shows the exact structured understanding used by the frozen row: the request summary, target device, task type, constraints, and user goals.">!</button></h2>
        <div id="requirement-summary" class="prose"></div>
        <details><summary>Exact canonical input</summary><pre id="requirement-json"></pre></details>
      </article>
      <article class="panel">
        <h2 class="module-title">Evidence projection <button class="info-tip" type="button" aria-label="About evidence projection" data-tooltip="Shows the small, project-authored evidence summaries that each generation step could see. It does not expose hidden reasoning or copied dataset content.">!</button></h2>
        <div id="evidence-cards" class="evidence-cards"></div>
        <details><summary>Exact projection JSON</summary><pre id="evidence-json"></pre></details>
      </article>
    </section>

    <section class="panel">
      <div class="section-heading">
        <div><h2 class="module-title">F1-F4 structured outputs <button class="info-tip" type="button" aria-label="About F1 through F4" data-tooltip="F1 defines static page structure, F2 defines states and visibility, F3 defines interactions, and F4 defines candidate acceptance checks. These are exact frozen outputs and are never regenerated here.">!</button></h2><p class="muted">Exact frozen node outputs. Details remain collapsed into scrollable evidence blocks.</p></div>
        <div id="failure-location"></div>
      </div>
      <div id="node-grid" class="node-grid"></div>
    </section>

    <section class="overview-grid">
      <article class="panel">
        <h2 class="module-title">Final PageSpec <button class="info-tip" type="button" aria-label="About the final PageSpec" data-tooltip="The machine-checkable page contract consumed by the Renderer. It binds sections, components, states, interactions, constraints, evidence references, and acceptance checks.">!</button></h2>
        <pre id="page-spec-json" class="tall"></pre>
      </article>
      <article class="panel">
        <h2 class="module-title">Objective browser evidence <button class="info-tip" type="button" aria-label="About browser evidence" data-tooltip="Records real Chrome loading, element operations, state changes, PageSpec conformance, console messages, page errors, viewport, screenshot, and interaction trace. It does not decide abstract semantic quality.">!</button></h2>
        <div id="browser-summary" class="prose"></div>
        <div class="media-heading">
          <div><strong>Captured Chrome evidence</strong><small id="browser-viewport">Loading viewport</small></div>
          <span>Immutable audit artifact</span>
        </div>
        <p class="media-note">This screenshot was captured at the audited viewport. It is evidence, not a responsive thumbnail of the wider live preview below.</p>
        <div class="browser-capture"><img id="browser-screenshot" alt="Captured Chrome result for the selected row"></div>
        <ol id="interaction-list" class="interaction-list"></ol>
        <details><summary>Interaction trace and browser audit</summary><pre id="browser-json"></pre></details>
      </article>
    </section>

    <section class="overview-grid">
      <article class="panel">
        <h2 class="module-title">Semantic sidecar <button class="info-tip" type="button" aria-label="About semantic evaluation" data-tooltip="A separately accounted evaluator checked whether concrete page evidence supported each abstract acceptance goal. Its verdicts cannot overwrite browser facts or historical first-pass accounting.">!</button></h2>
        <pre id="semantic-json"></pre>
      </article>
      <article class="panel">
        <h2 class="module-title">Historical outcome <button class="info-tip" type="button" aria-label="About historical outcome" data-tooltip="Preserves what happened on the original first pass. A later zero-model policy replay may make a package deliverable, but cannot rewrite an original failure as success.">!</button></h2>
        <pre id="historical-json"></pre>
        <div id="revalidation-block"></div>
      </article>
    </section>

    <section class="panel external-panel" id="external-verification">
      <div class="section-heading">
        <div>
          <span class="section-kicker">Optional external verification</span>
          <h2 class="module-title">Use GUISpector as a separate acceptance strategy <button class="info-tip" type="button" aria-label="About the optional GUISpector check" data-tooltip="Sends one selected package to a separately operated GUISpector verifier. The module is off by default, requires an explicit model-provider action, and never changes Req2Web generation, browser evidence, semantic evidence, or the canonical result.">!</button></h2>
          <p class="muted">This optional module is disabled until you turn it on. It is not a benchmark comparison and its decisions remain a separate acceptance view.</p>
        </div>
        <div id="guispector-runtime-status" class="capability-pill unavailable">Off by default</div>
      </div>
      <label class="optional-switch" for="guispector-enable">
        <input id="guispector-enable" type="checkbox">
        <span><strong>Enable optional GUISpector controls</strong><small>No model or external service is called merely by enabling the panel.</small></span>
      </label>
      <div id="guispector-controls" hidden>
        <div id="guispector-summary" class="external-summary"></div>
        <div class="external-grid">
          <article class="external-card">
            <span class="section-kicker">Provider connection</span>
            <h3>Select one supported provider profile</h3>
            <p>The API key is used for one explicit connection test, is never written to the repository or browser storage, and is cleared after the request.</p>
            <label for="guispector-provider"><span>Provider</span><select id="guispector-provider"><option value="zhipu_bigmodel">Zhipu BigModel</option><option value="alibaba_gui_plus">Alibaba GUI Plus</option></select></label>
            <label for="guispector-model"><span>Compatible model</span><input id="guispector-model" type="text" value="glm-4.6v" readonly></label>
            <label for="guispector-api-key"><span>API key</span><input id="guispector-api-key" type="password" autocomplete="off" spellcheck="false" placeholder="Used once and not saved"></label>
            <label id="guispector-workspace-field" for="guispector-workspace" hidden><span>Alibaba workspace ID or Beijing endpoint</span><input id="guispector-workspace" type="text" autocomplete="off" spellcheck="false"></label>
            <label class="explicit-confirmation" for="guispector-confirm"><input id="guispector-confirm" type="checkbox"><span>I understand that the connection test makes one external model request and may incur provider cost.</span></label>
            <button id="guispector-test-connection" class="button secondary" type="button">Test selected provider</button>
            <div id="guispector-connection-result" class="media-note" aria-live="polite">No provider request has been made.</div>
          </article>
          <article class="external-card">
            <span class="section-kicker">Prepared input</span>
            <h3>One exact package at a time</h3>
            <p>GUISpector receives the frozen requirement, two fixed acceptance conditions, and the exact packaged page address. It does not receive hidden data or rewrite the package.</p>
            <label for="guispector-case-select"><span>Prepared package</span><select id="guispector-case-select"></select></label>
            <div id="guispector-case" class="external-case"></div>
          </article>
        </div>
        <div class="external-actions">
          <a class="button secondary" href="guispector_evaluation.json" download>Download evaluation packet</a>
          <a id="guispector-open-runtime" class="button secondary" href="http://127.0.0.1:8000/" target="_blank" rel="noopener">Open local GUISpector</a>
          <details><summary>Exact packet and claim boundary</summary><pre id="guispector-json"></pre></details>
        </div>
      </div>
    </section>

    <section class="panel page-panel" id="final-result">
      <div class="section-heading">
        <div><h2 class="module-title">Final runnable page <button class="info-tip" type="button" aria-label="About the runnable page" data-tooltip="Loads the exact packaged HTML, CSS, and JavaScript. Prototype controls change the PageSpec state in place; they are not expected to navigate to a production backend or another application route.">!</button></h2><p class="muted">The exact packaged page used by the objective browser audit.</p></div>
        <a id="open-page" class="button" target="_blank" rel="noopener">Open exact package</a>
      </div>
      <div class="preview-controls">
        <div class="segmented-control" role="group" aria-label="Live preview viewport">
          <button id="preview-fit" type="button" aria-pressed="true">Fit to panel</button>
          <button id="preview-audit" type="button" aria-pressed="false">Audited viewport</button>
        </div>
        <span id="preview-viewport">Fit to panel</span>
      </div>
      <div class="preview-state" aria-live="polite">
        <span>Live prototype state</span>
        <strong id="preview-state-name">Loading</strong>
        <p id="preview-state-message">The selected page has not finished loading.</p>
      </div>
      <div id="preview-stage" class="preview-stage">
        <div id="preview-canvas" class="preview-canvas">
          <iframe id="final-page" title="Selected Req2Web result page"></iframe>
        </div>
      </div>
      <p id="preview-note" class="media-note">Fit-to-panel mode uses the available console width. Switch to the audited viewport for a like-for-like comparison with the captured Chrome evidence.</p>
    </section>
  </main>
  </div>

  <footer>
    <div><strong>Req2Web Inspector</strong><p>Frozen replay evidence plus bounded local tooling. No H1/gold, formal evaluation, broad generalization, training, LoRA, production, or user-study claim.</p></div>
    <a href="#top">Back to top</a>
  </footer>
  <script src="app.js"></script>
</body>
</html>
""".encode("utf-8")


_STYLES_CSS = """:root {
  color-scheme: light;
  --ink: #172033;
  --muted: #667085;
  --canvas: #f7f8fa;
  --card: #ffffff;
  --soft: #f2f4f7;
  --line: #e4e7ec;
  --line-strong: #d0d5dd;
  --accent: #2563eb;
  --accent-dark: #1d4ed8;
  --accent-soft: #eff6ff;
  --success: #0f766e;
  --success-soft: #ecfdf5;
  --warning: #b45309;
  --warning-soft: #fffbeb;
  --danger: #b42318;
  --danger-soft: #fef3f2;
  --radius: 14px;
  --shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 10px 30px rgba(16, 24, 40, .05);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: 92px; }
body { max-width: 100%; margin: 0; overflow-x: clip; color: var(--ink); background: var(--canvas); font-size: 15px; }
a { color: var(--accent); }
.site-header { position: sticky; top: 0; z-index: 30; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 2rem; min-height: 68px; padding: 0 clamp(1rem, 4vw, 3rem); border-bottom: 1px solid var(--line); background: rgba(255, 255, 255, .94); backdrop-filter: blur(18px); }
.brand { display: flex; align-items: center; gap: .7rem; color: var(--ink); text-decoration: none; }
.brand-mark { display: grid; width: 36px; height: 36px; place-items: center; border-radius: 10px; background: var(--ink); color: white; font-size: .72rem; font-weight: 800; letter-spacing: .04em; }
.brand > span:last-child { display: grid; line-height: 1.15; }
.brand small { color: var(--muted); font-size: .7rem; font-weight: 600; }
nav { display: flex; min-width: 0; justify-content: center; gap: clamp(.7rem, 1.5vw, 1.55rem); }
nav a { flex: 0 0 auto; color: #475467; text-decoration: none; font-size: .86rem; font-weight: 650; white-space: nowrap; }
nav a:hover { color: var(--accent); }
.local-status { display: inline-flex; align-items: center; gap: .5rem; padding: .42rem .7rem; border: 1px solid var(--line); border-radius: 999px; color: #344054; background: white; font-size: .78rem; font-weight: 700; }
.local-status i, .status-line i { width: 7px; height: 7px; border-radius: 50%; background: #12b76a; box-shadow: 0 0 0 3px #d1fadf; }
.app-shell { width: min(1480px, calc(100% - 2rem)); margin: 0 auto; }
.hero { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: clamp(2rem, 6vw, 7rem); align-items: center; min-height: 430px; padding: clamp(3.4rem, 8vw, 7rem) clamp(.2rem, 2vw, 2rem) 4.2rem; border-bottom: 1px solid var(--line); }
.hero-copy { max-width: 840px; }
.hero h1 { max-width: 820px; margin: .65rem 0 1.15rem; font-size: clamp(2.55rem, 5vw, 4.7rem); line-height: .99; letter-spacing: -.058em; }
.eyebrow, .section-kicker { color: var(--accent); font-size: .71rem; letter-spacing: .14em; font-weight: 800; text-transform: uppercase; }
.lede { max-width: 790px; margin: 0; color: var(--muted); font-size: 1.08rem; line-height: 1.72; }
.hero-actions { display: flex; gap: .7rem; margin-top: 1.7rem; }
.release-card { padding: 1.45rem; border: 1px solid var(--line); border-radius: 18px; background: var(--card); box-shadow: var(--shadow); }
.release-card > strong { display: block; margin: .75rem 0 .35rem; font-size: 1.35rem; letter-spacing: -.025em; }
.release-card p { margin: 0 0 1.15rem; color: var(--muted); line-height: 1.55; }
.status-line { display: flex; align-items: center; gap: .55rem; padding-top: 1rem; border-top: 1px solid var(--line); color: #475467; font-size: .8rem; font-weight: 700; }
.status-line.warning i { background: #f79009; box-shadow: 0 0 0 3px #fef0c7; }
main { padding: 2rem 0 5rem; }
.metrics { display: grid; grid-template-columns: repeat(5, minmax(140px, 1fr)); overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card); }
.metric { min-height: 116px; padding: 1.25rem; border-right: 1px solid var(--line); }
.metric:last-child { border-right: 0; }
.metric strong { display: block; margin-bottom: .35rem; color: var(--ink); font-size: 1.72rem; letter-spacing: -.04em; }
.metric span { color: var(--muted); font-size: .8rem; line-height: 1.45; }
.separation-note { display: grid; grid-template-columns: 150px 1fr; gap: 1.1rem; align-items: start; margin: 1rem 0 4rem; padding: 1rem 1.15rem; border: 1px solid #bfdbfe; border-radius: 12px; background: var(--accent-soft); }
.notice-label { color: var(--accent-dark); font-size: .72rem; font-weight: 800; text-transform: uppercase; letter-spacing: .08em; }
.separation-note p { margin: .2rem 0 0; color: #475467; line-height: 1.5; }
.panel { min-width: 0; padding: 1.4rem; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card); box-shadow: var(--shadow); }
.panel h2 { margin: .35rem 0 .7rem; font-size: 1.3rem; letter-spacing: -.025em; }
.module-title { display: flex; min-width: 0; align-items: center; gap: .45rem; }
.info-tip { position: relative; display: inline-grid; flex: 0 0 auto; width: 19px; height: 19px; padding: 0; place-items: center; border: 1px solid #98a2b3; border-radius: 50%; background: white; color: #475467; font: 800 11px/1 ui-sans-serif, system-ui, sans-serif; cursor: help; }
.info-tip::after { position: absolute; top: calc(100% + 9px); left: -8px; z-index: 80; width: min(330px, 75vw); padding: .7rem .78rem; border: 1px solid var(--line-strong); border-radius: 9px; background: #101828; color: white; box-shadow: 0 10px 30px rgba(16, 24, 40, .18); content: attr(data-tooltip); font-size: .75rem; font-weight: 500; letter-spacing: 0; line-height: 1.48; opacity: 0; pointer-events: none; text-align: left; text-transform: none; transform: translateY(-3px); transition: opacity .14s ease, transform .14s ease; }
.intake-side .info-tip::after, .overview-grid > :nth-child(2n) .info-tip::after { right: -8px; left: auto; }
.info-tip:hover::after, .info-tip:focus-visible::after { opacity: 1; transform: translateY(0); }
.info-tip:focus-visible { outline: 3px solid #bfdbfe; outline-offset: 2px; }
.section-heading { display: flex; justify-content: space-between; gap: 2rem; align-items: flex-start; }
.section-heading > div:first-child { max-width: 840px; }
.muted { color: var(--muted); line-height: 1.55; }
.prose { line-height: 1.68; }
.prose p { margin: .35rem 0; }
.intake-panel { margin-bottom: 1rem; padding: 1.6rem; }
.capability-pill { flex: 0 0 auto; padding: .45rem .65rem; border-radius: 999px; font-size: .73rem; font-weight: 800; }
.capability-pill.pending { background: var(--soft); color: #475467; }
.capability-pill.available { background: var(--success-soft); color: var(--success); }
.capability-pill.unavailable { background: var(--warning-soft); color: var(--warning); }
.intake-grid { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(280px, .75fr); gap: 1rem; margin-top: 1.25rem; }
.intake-grid > *, .canonical-controls > *, .compact-module { min-width: 0; }
.requirement-form { min-width: 0; padding: 1rem; border: 1px solid var(--line); border-radius: 12px; background: #fcfcfd; }
.field-label, .requirement-form label { display: grid; gap: .4rem; color: #344054; font-size: .78rem; font-weight: 750; }
.field-label small { color: var(--muted); font-weight: 500; }
textarea, input { width: 100%; padding: .72rem .8rem; border: 1px solid var(--line-strong); border-radius: 9px; background: white; color: var(--ink); font: inherit; line-height: 1.5; resize: vertical; }
input[readonly] { background: var(--soft); color: #475467; }
textarea:focus, input:focus, select:focus { outline: 3px solid #bfdbfe; outline-offset: 1px; border-color: #84adff; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .75rem; margin: .8rem 0; }
.form-actions { display: flex; flex-wrap: wrap; align-items: center; gap: .55rem; margin-top: .9rem; }
.canonical-controls { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: center; gap: .65rem; margin-top: 1rem; padding: .8rem; border: 1px solid #c7d7fe; border-radius: 10px; background: var(--accent-soft); }
.canonical-controls > div { display: grid; grid-column: 1 / -1; gap: .2rem; }
.canonical-controls small { color: var(--muted); font-weight: 500; line-height: 1.4; }
.canonical-controls label { display: flex; align-items: flex-start; gap: .35rem; white-space: normal; }
.canonical-controls input { width: auto; margin: 0; }
.canonical-controls .button { grid-column: 1 / -1; justify-self: start; }
.text-button { padding: .52rem .65rem; border: 0; border-radius: 8px; background: transparent; color: var(--accent-dark); font: inherit; font-size: .78rem; font-weight: 750; cursor: pointer; }
.text-button:hover { background: var(--accent-soft); }
.button:disabled, .text-button:disabled { cursor: not-allowed; opacity: .5; }
.intake-side { display: grid; gap: .65rem; }
.compact-module { padding: .85rem; overflow-wrap: anywhere; border: 1px solid var(--line); border-radius: 10px; background: white; }
.compact-module h3 { margin: 0; font-size: .9rem; }
.compact-module p { margin: .45rem 0 .7rem; color: var(--muted); font-size: .78rem; line-height: 1.5; }
.module-status { display: inline-flex; padding: .27rem .45rem; border-radius: 999px; font-size: .68rem; font-weight: 800; }
.module-status.available { background: var(--success-soft); color: var(--success); }
.module-status.unavailable { background: var(--soft); color: #667085; }
.intake-result { margin-top: 1rem; }
.intake-result:empty { display: none; }
.result-box { padding: 1rem; border: 1px solid var(--line); border-radius: 10px; background: white; }
.result-box h3 { margin: 0 0 .5rem; font-size: 1rem; }
.finding-list { display: grid; gap: .45rem; margin: .75rem 0; padding: 0; list-style: none; }
.finding { padding: .65rem .75rem; border-left: 3px solid #98a2b3; border-radius: 6px; background: var(--soft); color: #475467; font-size: .78rem; line-height: 1.48; }
.finding.warning, .finding.blocking { border-left-color: #f79009; background: var(--warning-soft); }
.finding strong { color: var(--ink); }
.stage-list { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .45rem; margin: .8rem 0; padding: 0; list-style: none; }
.stage-list li { min-width: 0; padding: .55rem; border: 1px solid var(--line); border-radius: 8px; color: #475467; font-size: .72rem; line-height: 1.35; }
.stage-list strong { display: block; margin-bottom: .2rem; color: var(--ink); overflow-wrap: anywhere; }
.stage-list li[class*="completed"], .stage-list .validated, .stage-list .pass, .stage-list li[class*="result_package"] { border-color: #a6f4c5; background: var(--success-soft); }
.stage-list li[class*="failed"], .stage-list li[class*="not_completed"] { border-color: #fedf89; background: var(--warning-soft); }
.stage-list .not_executed { background: var(--soft); }
.outcome-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .55rem; margin: .85rem 0; }
.outcome-card { min-width: 0; padding: .7rem; border: 1px solid var(--line); border-radius: 9px; background: #fcfcfd; }
.outcome-card > span { display: block; margin-bottom: .35rem; color: var(--muted); font-size: .65rem; font-weight: 850; letter-spacing: .06em; text-transform: uppercase; }
.outcome-card strong { display: block; color: var(--ink); font-size: .78rem; line-height: 1.35; overflow-wrap: anywhere; }
.outcome-card small { display: block; margin-top: .35rem; color: var(--muted); font-size: .68rem; line-height: 1.42; }
.outcome-card.success { border-color: #a6f4c5; background: var(--success-soft); }
.outcome-card.warning { border-color: #fedf89; background: var(--warning-soft); }
.outcome-card.neutral { background: var(--soft); }
.history-panel { margin: 1rem 0 3rem; }
.entry-strip { display: flex; flex-wrap: wrap; gap: .5rem; margin: .8rem 0 1rem; }
.entry-strip span { padding: .4rem .55rem; border: 1px solid var(--line); border-radius: 8px; color: var(--muted); font-size: .72rem; }
.entry-strip strong { color: var(--ink); }
.import-form { display: grid; grid-template-columns: minmax(0, 1fr) minmax(240px, .7fr) auto; align-items: end; gap: .75rem; margin: 0 0 1rem; padding: .8rem; border: 1px solid var(--line); border-radius: 10px; background: #fcfcfd; }
.import-copy { display: grid; gap: .3rem; color: #344054; font-size: .78rem; }
.import-copy small { color: var(--muted); line-height: 1.45; }
.file-input-hidden { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; opacity: 0; pointer-events: none; }
.import-picker { display: flex; min-width: 0; align-items: center; gap: .6rem; }
.import-picker label { flex: 0 0 auto; margin: 0; }
.import-picker span { min-width: 0; color: var(--muted); font-size: .74rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .65rem; }
.run-card { min-width: 0; padding: .85rem; border: 1px solid var(--line); border-radius: 10px; background: #fcfcfd; }
.run-card h3 { margin: .45rem 0; font-size: .9rem; overflow-wrap: anywhere; }
.run-card p { margin: .35rem 0; color: var(--muted); font-size: .75rem; line-height: 1.45; }
.run-card .badges { margin-bottom: .45rem; }
.run-actions { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .65rem; }
.run-actions a, .run-actions button { padding: .4rem .52rem; border: 1px solid var(--line-strong); border-radius: 7px; background: white; color: var(--accent-dark); font: inherit; font-size: .7rem; font-weight: 750; text-decoration: none; cursor: pointer; }
select { width: 100%; min-width: 0; padding: .68rem .8rem; border: 1px solid var(--line-strong); border-radius: 9px; background: white; color: var(--ink); font: inherit; }
select:focus, a:focus-visible, summary:focus-visible { outline: 3px solid #bfdbfe; outline-offset: 2px; }
.case-toolbar { position: sticky; top: 78px; z-index: 20; display: grid; grid-template-columns: 190px minmax(300px, 1fr) auto; align-items: center; gap: 1rem; margin-bottom: 1rem; padding: .8rem 1rem; border: 1px solid var(--line); border-radius: 12px; background: rgba(255, 255, 255, .96); box-shadow: var(--shadow); backdrop-filter: blur(14px); }
.toolbar-title { display: grid; gap: .15rem; }
.toolbar-title strong { font-size: .86rem; }
.badges { display: flex; flex-wrap: wrap; gap: .4rem; }
.badge { padding: .32rem .55rem; border-radius: 999px; background: var(--success-soft); color: var(--success); font-size: .72rem; font-weight: 800; }
.badge.warn { background: var(--warning-soft); color: var(--warning); }
.overview-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 1rem 0; }
.evidence-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .65rem; }
.evidence-card { padding: .8rem; border: 1px solid var(--line); border-radius: 10px; background: #fcfcfd; }
.evidence-card h3 { margin: 0 0 .35rem; color: var(--accent); font-size: .78rem; }
.evidence-card p { margin: .25rem 0; font-size: .8rem; line-height: 1.45; }
.node-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .75rem; }
.node { min-width: 0; padding: .8rem; border: 1px solid var(--line); border-radius: 10px; background: #fcfcfd; }
.node h3 { margin: 0 0 .55rem; color: var(--accent); }
pre { max-height: 430px; overflow: auto; margin: .7rem 0 0; padding: .9rem; border: 1px solid #26324a; border-radius: 9px; background: #101828; color: #e4e7ec; font: 12px/1.48 ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
pre.tall { max-height: 720px; }
details { margin-top: .7rem; }
summary { cursor: pointer; color: #344054; font-size: .82rem; font-weight: 750; }
.media-heading { display: flex; justify-content: space-between; align-items: center; gap: 1rem; margin-top: 1rem; }
.media-heading > div { display: grid; gap: .2rem; }
.media-heading small { color: var(--muted); font-size: .74rem; }
.media-heading > span { padding: .28rem .5rem; border-radius: 999px; background: var(--soft); color: #475467; font-size: .68rem; font-weight: 750; white-space: nowrap; }
.media-note { margin: .55rem 0 0; color: var(--muted); font-size: .77rem; line-height: 1.5; }
.browser-capture { display: grid; place-items: start center; width: 100%; margin: 1rem 0; padding: 1rem; overflow: auto; border: 1px solid var(--line); border-radius: 10px; background: var(--soft); }
#browser-screenshot { display: block; width: auto; max-width: 100%; height: auto; margin: 0; border: 1px solid var(--line); border-radius: 8px; background: white; }
.failure { max-width: 470px; padding: .75rem .85rem; border: 1px solid #fedf89; border-radius: 10px; background: var(--warning-soft); color: #7a2e0e; font-size: .8rem; line-height: 1.48; }
.success { padding: .58rem .75rem; border: 1px solid #a6f4c5; border-radius: 10px; background: var(--success-soft); color: var(--success); font-size: .8rem; font-weight: 750; }
.button { display: inline-block; padding: .66rem .9rem; border: 1px solid var(--accent); border-radius: 9px; background: var(--accent); color: white; text-decoration: none; font: inherit; font-size: .84rem; font-weight: 750; cursor: pointer; }
.button:hover { background: var(--accent-dark); }
.button.secondary { border-color: var(--line-strong); background: white; color: #344054; }
.interaction-list { margin: .8rem 0; padding-left: 1.5rem; color: var(--muted); font-size: .82rem; line-height: 1.5; }
.interaction-list small { display: block; overflow-wrap: anywhere; }
.external-panel { margin-top: 1rem; }
#external-verification, #final-result { scroll-margin-top: 190px; }
.optional-switch { display: flex; align-items: flex-start; gap: .75rem; margin: 1rem 0; padding: .9rem; border: 1px solid var(--line); border-radius: 11px; background: #fcfcfd; cursor: pointer; }
.optional-switch input, .explicit-confirmation input { width: auto; flex: 0 0 auto; margin-top: .15rem; }
.optional-switch span { display: grid; gap: .18rem; }
.optional-switch small { color: var(--muted); font-weight: 500; line-height: 1.45; }
[hidden] { display: none !important; }
.external-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .65rem; margin: 1.1rem 0; }
.external-stat { min-width: 0; padding: .8rem; border: 1px solid var(--line); border-radius: 10px; background: #fcfcfd; }
.external-stat strong { display: block; color: var(--ink); font-size: 1.25rem; letter-spacing: -.03em; }
.external-stat span { display: block; margin-top: .25rem; color: var(--muted); font-size: .72rem; line-height: 1.4; }
.external-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(300px, .9fr); gap: .8rem; }
.external-card { min-width: 0; padding: 1rem; border: 1px solid var(--line); border-radius: 11px; background: #fcfcfd; }
.external-card h3 { margin: .4rem 0 .45rem; font-size: 1rem; }
.external-card > p { color: var(--muted); font-size: .8rem; line-height: 1.55; }
.external-card label { display: grid; gap: .4rem; margin-top: .8rem; color: #344054; font-size: .76rem; font-weight: 750; }
.external-card .explicit-confirmation { display: flex; align-items: flex-start; gap: .6rem; font-weight: 600; line-height: 1.45; }
.external-case { min-width: 0; margin-top: .75rem; padding: .75rem; border: 1px solid #bfdbfe; border-radius: 9px; background: var(--accent-soft); }
.external-case strong, .external-case span { overflow-wrap: anywhere; }
.external-case p { margin: .35rem 0; color: #475467; font-size: .77rem; line-height: 1.5; }
.external-case ul { margin: .45rem 0 0; padding-left: 1.2rem; color: #475467; font-size: .75rem; line-height: 1.5; }
.external-actions { display: flex; flex-wrap: wrap; align-items: flex-start; gap: .75rem; margin-top: .85rem; }
.external-actions details { flex: 1 1 520px; margin: 0; }
.page-panel { margin-top: 1rem; }
.preview-controls { display: flex; justify-content: space-between; align-items: center; gap: 1rem; margin-top: 1rem; }
.preview-controls > span { color: var(--muted); font-size: .76rem; font-weight: 700; }
.segmented-control { display: inline-flex; padding: 3px; border: 1px solid var(--line); border-radius: 10px; background: var(--soft); }
.segmented-control button { min-height: 34px; padding: .4rem .72rem; border: 0; border-radius: 7px; background: transparent; color: #475467; font: inherit; font-size: .76rem; font-weight: 750; cursor: pointer; }
.segmented-control button[aria-pressed="true"] { background: white; color: var(--accent-dark); box-shadow: 0 1px 3px rgba(16, 24, 40, .12); }
.segmented-control button:focus-visible { outline: 3px solid #bfdbfe; outline-offset: 2px; }
.preview-state { display: grid; grid-template-columns: auto auto minmax(0, 1fr); align-items: center; gap: .55rem 1rem; margin-top: .75rem; padding: .7rem .85rem; border: 1px solid #bfdbfe; border-radius: 10px; background: var(--accent-soft); }
.preview-state > span { color: var(--accent-dark); font-size: .7rem; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; }
.preview-state > strong { font-size: .82rem; }
.preview-state p { min-width: 0; margin: 0; color: #475467; font-size: .78rem; line-height: 1.45; overflow-wrap: anywhere; }
.preview-stage { display: grid; place-items: start center; width: 100%; margin-top: .75rem; padding: 1rem; overflow: auto; border: 1px solid var(--line); border-radius: 12px; background: var(--soft); }
.preview-canvas { position: relative; width: 100%; height: 760px; margin: 0 auto; }
#final-page { display: block; width: 100%; height: 100%; margin: 0; border: 1px solid var(--line); border-radius: 10px; background: white; transform-origin: top left; }
footer { display: flex; justify-content: space-between; gap: 2rem; align-items: center; padding: 2rem max(1rem, calc((100vw - 1480px) / 2)); border-top: 1px solid var(--line); background: white; color: var(--muted); font-size: .8rem; }
footer strong { color: var(--ink); }
footer p { margin: .25rem 0 0; }
footer a { white-space: nowrap; text-decoration: none; font-weight: 750; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@media (max-width: 1180px) {
  nav { display: none; }
  .site-header { grid-template-columns: 1fr auto; }
  .hero { grid-template-columns: 1fr 300px; }
  .intake-grid { grid-template-columns: 1fr; }
  .intake-side { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .metrics { grid-template-columns: repeat(3, 1fr); }
  .metric { border-bottom: 1px solid var(--line); }
  .node-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 820px) {
  .app-shell { width: min(100% - 1rem, 1480px); }
  .hero { grid-template-columns: 1fr; min-height: 0; padding: 3.5rem .2rem; }
  .release-card { max-width: 520px; }
  .section-heading { flex-direction: column; align-items: stretch; }
  .intake-side { grid-template-columns: 1fr; }
  .canonical-controls > div, .canonical-controls .button { grid-column: 1 / -1; }
  .import-form { grid-template-columns: 1fr; }
  .stage-list, .outcome-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .run-list { grid-template-columns: 1fr; }
  .metrics { grid-template-columns: 1fr 1fr; }
  .metric { border-right: 1px solid var(--line); }
  .separation-note { grid-template-columns: 1fr; }
  .overview-grid, .node-grid, .evidence-cards, .external-grid { grid-template-columns: 1fr; }
  .external-summary { grid-template-columns: 1fr 1fr; }
  .case-toolbar { top: 74px; grid-template-columns: 1fr; }
  .badges { display: none; }
  .preview-canvas { height: 680px; }
  .preview-state { grid-template-columns: 1fr auto; }
  .preview-state p { grid-column: 1 / -1; }
}
@media (max-width: 520px) {
  .site-header { padding: 0 .75rem; }
  .local-status { display: none; }
  .hero h1 { font-size: 2.45rem; }
  .hero-actions { flex-direction: column; }
  .form-grid, .stage-list, .outcome-summary { grid-template-columns: 1fr; }
  .canonical-controls { grid-template-columns: 1fr; }
  .external-summary { grid-template-columns: 1fr; }
  .canonical-controls > div, .canonical-controls label, .canonical-controls .button { grid-column: 1; }
  .button { text-align: center; }
  .metrics { grid-template-columns: 1fr; }
  .metric { min-height: 0; border-right: 0; }
  .panel { padding: 1rem; }
  .media-heading, .preview-controls { align-items: flex-start; flex-direction: column; }
  .browser-capture, .preview-stage { padding: .5rem; }
  .segmented-control { width: 100%; }
  .segmented-control button { flex: 1; }
  footer { align-items: flex-start; flex-direction: column; }
}
""".encode("utf-8")


_APP_JS = """const cache = new Map();

async function getJson(path) {
  if (!cache.has(path)) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`Could not load ${path}: ${response.status}`);
    cache.set(path, await response.json());
  }
  return cache.get(path);
}

function pretty(value) { return JSON.stringify(value, null, 2); }
function byId(id) { return document.getElementById(id); }
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}
function badge(text, warning = false) {
  return `<span class="badge${warning ? ' warn' : ''}">${text}</span>`;
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, options);
  let value;
  try {
    value = await response.json();
  } catch (error) {
    throw new Error(`The local Inspector service did not return JSON for ${path}.`);
  }
  if (!response.ok) {
    throw new Error(value.error_message || value.failure?.message || `Request failed with status ${response.status}.`);
  }
  return value;
}

function intakePayload() {
  return {
    requirement: byId('requirement-input').value,
    target_device: byId('target-device-input').value || null,
    task_type: byId('task-type-input').value || null,
    constraints: byId('constraints-input').value.split(/\\r?\\n/).map(value => value.trim()).filter(Boolean),
    retriever_backend: 'tfidf',
    top_k: 2,
  };
}

function findingMarkup(item) {
  return `<li class="finding ${escapeHtml(item.severity)}"><strong>${escapeHtml(item.message)}</strong><br>${escapeHtml(item.suggestion)}</li>`;
}

function renderDiagnostics(value) {
  const findings = value.findings.length
    ? `<ul class="finding-list">${value.findings.map(findingMarkup).join('')}</ul>`
    : '<p class="success">No deterministic preflight concern was found.</p>';
  const preview = value.canonical_b_preview;
  const previewMarkup = preview
    ? `<p><strong>What Req2Web understood:</strong> ${escapeHtml(preview.requirement_summary)}</p><p class="muted">${preview.use_cases.length} user goals, ${preview.constraints.length} constraints, ${escapeHtml(preview.target_device)} target.</p><details><summary>Exact deterministic understanding preview</summary><pre>${escapeHtml(pretty(preview))}</pre></details>`
    : '';
  byId('intake-result').innerHTML = `<div class="result-box"><h3>${value.accepted_for_deterministic_draft ? 'Requirement check completed' : 'Requirement needs attention'}</h3>${findings}${previewMarkup}<p class="media-note">Semantic requirement assistant: ${escapeHtml(value.semantic_assist.status)}. Model calls: ${value.semantic_assist.call_count}.</p></div>`;
}

function renderSemanticAssist(value) {
  const sidecar = value.sidecar || {};
  const items = sidecar.advisory_items || [];
  const advisory = items.length
    ? `<ul class="finding-list">${items.map(item => `<li class="finding notice"><strong>${escapeHtml(item.advisory_kind.replaceAll('_', ' '))}</strong><br>${escapeHtml(item.statement)}</li>`).join('')}</ul>`
    : '<p class="failure">No advisory sidecar was accepted. The deterministic requirement and draft paths are unchanged.</p>';
  const worker = value.worker?.worker_result || {};
  const memory = worker.cuda_peak_reserved_bytes
    ? `<p class="muted">${escapeHtml(value.profile.profile_name)} - ${worker.input_token_length} input tokens - ${Math.round(worker.cuda_peak_reserved_bytes / 1048576)} MiB peak CUDA reservation.</p>`
    : '';
  byId('intake-result').innerHTML = `<div class="result-box"><h3>${value.status === 'advisory_available' ? 'Semantic advice is available' : 'Semantic assistant failed closed'}</h3>${advisory}${memory}<details><summary>Exact advisory evidence</summary><pre>${escapeHtml(pretty(value))}</pre></details><p class="media-note">Canonical requirement writeback: disabled. F1-F4 consumption: disabled. Model calls: ${sidecar.call_count || 0}. Automatic retries: 0.</p></div>`;
}

function stageMarkup(item) {
  return `<li class="${escapeHtml(item.status)}"><strong>${escapeHtml(item.label)}</strong>${escapeHtml(item.status.replaceAll('_', ' '))}${item.detail ? `<br>${escapeHtml(item.detail)}` : ''}</li>`;
}

function stageStatus(record, stageId) {
  return (record.stages || []).find(item => item.stage_id === stageId)?.status || 'not_recorded';
}

function canonicalOutcomeItems(record) {
  const summary = record.canonical_summary || {};
  const result = record.result || {};
  const graphFailure = summary.graph_failure || {};
  const modelAvailable = result.model_result_available === true || summary.model_result_available === true;
  const deliveryReady = Boolean(result.entrypoint);
  const selectedDelivery = result.selected_delivery_kind || summary.selected_delivery_kind || 'not available';
  const browserStatus = record.browser?.browser_execution_status || stageStatus(record, 'browser');
  const pageSpecStatus = record.browser?.page_spec_conformance_status;
  const semanticStatus = record.semantic?.status || stageStatus(record, 'semantic');

  const model = modelAvailable
    ? {tone: 'success', title: 'Model result accepted', detail: 'F1-F4 and composition produced the selected model package.'}
    : graphFailure.failure_stage
      ? {tone: 'warning', title: `Failed closed at ${graphFailure.failure_stage}`, detail: graphFailure.message_code || 'The raw model result was rejected and is not counted as model success.'}
      : {tone: 'neutral', title: stageStatus(record, 'model').replaceAll('_', ' '), detail: 'No accepted model-generated package is recorded.'};
  const delivery = deliveryReady
    ? {
        tone: 'success',
        title: selectedDelivery === 'deterministic_g0_result_package_v2' ? 'Deterministic G0 ready' : 'ResultPackage ready',
        detail: selectedDelivery === 'deterministic_g0_result_package_v2'
          ? 'A same-input deterministic package is available; this does not change the model failure.'
          : selectedDelivery.replaceAll('_', ' '),
      }
    : {tone: 'warning', title: 'No package available', detail: stageStatus(record, 'package').replaceAll('_', ' ')};
  const browser = browserStatus === 'pass' && pageSpecStatus === 'pass'
    ? {tone: 'success', title: 'Real Chrome passed', detail: 'Browser execution and PageSpec conformance both passed.'}
    : browserStatus === 'not_requested' || browserStatus.startsWith('not_executed') || browserStatus === 'not_recorded'
      ? {tone: 'neutral', title: 'Not run', detail: browserStatus.replaceAll('_', ' ')}
      : {tone: 'warning', title: 'Did not pass', detail: `${browserStatus.replaceAll('_', ' ')}${pageSpecStatus ? `; PageSpec ${pageSpecStatus}` : ''}`};
  const semantic = semanticStatus === 'semantic_alignment_complete_pending_owner_review'
    ? {tone: 'success', title: 'Completed; review pending', detail: 'The semantic sidecar completed. Its verdict remains separate from browser and delivery facts.'}
    : semanticStatus === 'not_requested' || semanticStatus.startsWith('not_executed') || semanticStatus === 'not_recorded'
      ? {tone: 'neutral', title: 'Not run', detail: semanticStatus.replaceAll('_', ' ')}
      : semanticStatus === 'failed_closed'
        ? {tone: 'warning', title: 'Failed closed', detail: 'No semantic success is inferred.'}
        : {tone: 'neutral', title: semanticStatus.replaceAll('_', ' '), detail: 'Open the semantic evidence for the exact verdict.'};
  return [
    {label: 'Model generation', ...model},
    {label: 'Deliverable package', ...delivery},
    {label: 'Objective browser', ...browser},
    {label: 'Semantic sidecar', ...semantic},
  ];
}

function nonCanonicalOutcomeItems(record, imported) {
  const deliveryReady = Boolean(record.result?.entrypoint);
  return [
    {label: 'Model generation', tone: 'neutral', title: 'Not run', detail: imported ? 'This package was imported.' : 'This draft uses deterministic components only.'},
    {label: 'Deliverable package', tone: deliveryReady ? 'success' : 'warning', title: deliveryReady ? 'ResultPackage ready' : 'No package available', detail: imported ? 'The imported package passed local validation.' : 'The deterministic draft is available locally.'},
    {label: 'Objective browser', tone: 'neutral', title: 'Not rerun', detail: 'No real-browser result is inferred for this entry.'},
    {label: 'Semantic sidecar', tone: 'neutral', title: 'Not run', detail: 'No semantic verdict is inferred for this entry.'},
  ];
}

function outcomeSummaryMarkup(record, imported, canonical) {
  const items = canonical ? canonicalOutcomeItems(record) : nonCanonicalOutcomeItems(record, imported);
  return `<div class="outcome-summary" role="list" aria-label="Run outcome summary">${items.map(item => `<article class="outcome-card ${escapeHtml(item.tone)}" role="listitem"><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail)}</small></article>`).join('')}</div>`;
}

function canonicalHistoryStatus(record) {
  if (record.status === 'queued' || record.status === 'running') return record.status;
  if (record.result?.entrypoint && record.result?.model_result_available === false) return 'model failed; deterministic package ready';
  if (record.result?.entrypoint) return 'package ready';
  return 'failed closed; no package';
}

function renderRunTrace(record, scroll = true) {
  const imported = record.status === 'completed_imported_result_package';
  const canonical = record.mode === 'canonical_local_qwen_full_flow';
  const result = record.result || {};
  const actions = result.entrypoint
    ? `<div class="form-actions"><a class="button" href="${escapeHtml(result.entrypoint)}" target="_blank" rel="noopener">Open page</a><a class="button secondary" href="${escapeHtml(result.download)}">Download ResultPackage ZIP</a></div>`
    : '';
  const evidenceLinks = [
    result.page_spec ? `<a href="${escapeHtml(result.page_spec)}" target="_blank" rel="noopener">PageSpec</a>` : '',
    ...Object.entries(record.node_evidence || {}).flatMap(([nodeId, evidence]) => {
      const raw = evidence.raw || evidence.attempt?.replace(/-attempt\\.json$/, '-raw.bin');
      return [
        evidence.output ? `<a href="${escapeHtml(evidence.output)}" target="_blank" rel="noopener">${escapeHtml(nodeId)} output</a>` : '',
        evidence.attempt ? `<a href="${escapeHtml(evidence.attempt)}" target="_blank" rel="noopener">${escapeHtml(nodeId)} attempt</a>` : '',
        raw ? `<a href="${escapeHtml(raw)}" target="_blank" rel="noopener">${escapeHtml(nodeId)} raw response</a>` : '',
      ];
    }),
    record.browser?.screenshot ? `<a href="${escapeHtml(record.browser.screenshot)}" target="_blank" rel="noopener">Browser screenshot</a>` : '',
    record.browser?.audit ? `<a href="${escapeHtml(record.browser.audit)}" target="_blank" rel="noopener">Browser audit</a>` : '',
    record.browser?.execution || record.browser?.audit ? `<a href="${escapeHtml(record.browser?.execution || record.browser.audit.replace(/browser-audit\\.json$/, 'browser-execution.json'))}" target="_blank" rel="noopener">Browser execution</a>` : '',
    record.semantic?.result ? `<a href="${escapeHtml(record.semantic.result)}" target="_blank" rel="noopener">Semantic result</a>` : '',
    record.semantic?.summary ? `<a href="${escapeHtml(record.semantic.summary)}" target="_blank" rel="noopener">Semantic summary</a>` : '',
  ].filter(Boolean).join('');
  const evidenceActions = evidenceLinks ? `<div class="run-actions evidence-actions">${evidenceLinks}</div>` : '';
  const failure = record.failure
    ? `<div class="failure"><strong>Failed closed at ${escapeHtml(record.failure.stage_id)}.</strong> ${escapeHtml(record.failure.message)}</div>`
    : '';
  const note = imported
    ? 'This is a validated existing ResultPackage import. Generation, model, semantic Agent, and real browser stages were not rerun or inferred.'
    : canonical
      ? 'This canonical run keeps model-node calls, deterministic fallback, browser facts, and semantic acceptance separate. The low-GPU profile proves integration only and is not formal-quality evidence.'
      : 'This is a component-level deterministic guided/G0 draft. F1-F4 model generation, semantic Agent calls, and real browser acceptance were not executed.';
  const title = result.title || record.input?.requirement || 'Local run trace';
  const detail = result.summary || result.selected_delivery_kind || record.status.replaceAll('_', ' ');
  const outcomeSummary = outcomeSummaryMarkup(record, imported, canonical);
  byId('intake-result').innerHTML = `<div class="result-box"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(detail)}</p>${outcomeSummary}${failure}<details><summary>Detailed stage trace</summary><ul class="stage-list">${(record.stages || []).map(stageMarkup).join('')}</ul></details>${actions}${evidenceActions}<details><summary>Exact local run record</summary><pre>${escapeHtml(pretty(record))}</pre></details><p class="media-note">${escapeHtml(note)}</p></div>`;
  if (scroll) byId('intake-result').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

function renderRuns(records) {
  if (!records.length) {
    byId('run-list').innerHTML = '<p class="muted">No local draft or package import has been created yet.</p>';
    return;
  }
  byId('run-list').innerHTML = records.map(record => {
    const imported = record.status === 'completed_imported_result_package';
    const canonical = record.mode === 'canonical_local_qwen_full_flow';
    const complete = record.status === 'completed_deterministic_draft' || imported || Boolean(record.result?.entrypoint);
    const title = record.result?.title || record.input?.requirement || record.input?.source_filename || record.run_id;
    const detail = record.result?.summary
      || record.result?.selected_delivery_kind
      || record.failure?.message
      || record.status.replaceAll('_', ' ');
    const actions = record.result?.entrypoint
      ? `<a href="${escapeHtml(record.result.entrypoint)}" target="_blank" rel="noopener">Open page</a><a href="${escapeHtml(record.result.download)}">Download</a>`
      : '';
    const statusLabel = imported ? 'imported package ready' : canonical ? canonicalHistoryStatus(record) : (complete ? 'deterministic draft ready' : 'failed closed');
    const recordUrl = canonical ? `/api/canonical-runs/${record.run_id}` : `/api/runs/${record.run_id}`;
    return `<article class="run-card"><div class="badges">${badge(statusLabel, !complete && record.status !== 'running' && record.status !== 'queued')}</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(detail)}</p><p>${escapeHtml(record.created_at || 'time unavailable')} - ${escapeHtml(record.run_id)}</p><div class="run-actions"><button type="button" data-record-url="${escapeHtml(recordUrl)}">Inspect trace</button>${actions}</div></article>`;
  }).join('');
  byId('run-list').querySelectorAll('[data-record-url]').forEach(button => {
    button.addEventListener('click', async () => {
      try {
        renderRunTrace(await requestJson(button.dataset.recordUrl));
      } catch (error) {
        byId('intake-result').innerHTML = `<div class="failure">${escapeHtml(error.message)}</div>`;
      }
    });
  });
}

async function loadRuns() {
  const [drafts, canonical] = await Promise.all([
    requestJson('/api/runs'),
    requestJson('/api/canonical-runs'),
  ]);
  const records = [...(drafts.runs || []), ...(canonical.runs || [])]
    .sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')));
  renderRuns(records);
}

function setIntakeBusy(busy, label = '') {
  byId('analyze-requirement').disabled = busy;
  byId('semantic-assist').disabled = busy || byId('semantic-assist').dataset.available !== 'true';
  byId('generate-draft').disabled = busy;
  byId('generate-canonical').disabled = busy || byId('generate-canonical').dataset.available !== 'true';
  byId('import-package-button').disabled = busy;
  if (busy) {
    const modelAction = label.toLowerCase().includes('semantic') || label.toLowerCase().includes('complete local');
    const detail = modelAction
      ? 'The isolated local Qwen route may take many minutes. Every model node is single-call with no retry, raw output is saved before parsing, and same-input deterministic fallback remains separately identified.'
      : 'The local deterministic pipeline is running. No model or external service is being called.';
    byId('intake-result').innerHTML = `<div class="result-box"><h3>${escapeHtml(label)}</h3><p class="muted">${escapeHtml(detail)}</p></div>`;
  }
}

async function initializeLiveInspector() {
  const capability = byId('live-capability');
  const guispectorStatus = byId('guispector-runtime-status');
  try {
    const value = await requestJson('/api/capabilities');
    const available = value.deterministic_guided_draft === 'available';
    const importAvailable = value.result_package_import === 'available';
    const semanticAvailable = value.semantic_requirement_assist === 'available_local_qwen';
    const canonicalAvailable = value.model_f1_f4_generation === 'available_explicit_local_qwen';
    const guispector = value.optional_guispector_verification || {};
    capability.textContent = canonicalAvailable ? 'Complete local flow available' : (available ? 'Local drafts available' : 'Portable replay only');
    capability.className = `capability-pill ${available ? 'available' : 'unavailable'}`;
    byId('analyze-requirement').disabled = !available;
    byId('generate-draft').disabled = !available;
    byId('semantic-assist').dataset.available = String(semanticAvailable);
    byId('semantic-assist').disabled = !semanticAvailable;
    byId('semantic-assist-status').textContent = semanticAvailable
      ? `Available: ${value.semantic_requirement_assist_detail.profile.profile_name}`
      : 'Not connected';
    byId('semantic-assist-status').className = `module-status ${semanticAvailable ? 'available' : 'unavailable'}`;
    byId('generate-canonical').dataset.available = String(canonicalAvailable);
    byId('generate-canonical').disabled = !canonicalAvailable;
    byId('canonical-flow-status').textContent = canonicalAvailable
      ? `Available: ${value.canonical_model_flow.profile.profile_name}`
      : 'Not connected';
    byId('canonical-flow-status').className = `module-status ${canonicalAvailable ? 'available' : 'unavailable'}`;
    guispectorStatus.dataset.apiAvailable = 'true';
    guispectorStatus.dataset.runtimeReady = String(guispector.status === 'ready_for_operator_started_external_run');
    byId('canonical-b-aux').disabled = !value.canonical_model_flow.requirement_assist_available;
    if (!value.canonical_model_flow.requirement_assist_available) byId('canonical-b-aux').checked = false;
    byId('import-package-button').disabled = !importAvailable;
    byId('import-package').disabled = !importAvailable;
    if (available) await loadRuns();
    else byId('run-list').innerHTML = '<p class="muted">Run history is disabled in read-only replay mode.</p>';
  } catch (error) {
    capability.textContent = 'Portable replay only';
    capability.className = 'capability-pill unavailable';
    byId('analyze-requirement').disabled = true;
    byId('generate-draft').disabled = true;
    byId('semantic-assist').dataset.available = 'false';
    byId('semantic-assist').disabled = true;
    byId('generate-canonical').dataset.available = 'false';
    byId('generate-canonical').disabled = true;
    byId('canonical-flow-status').textContent = 'Not connected';
    guispectorStatus.dataset.apiAvailable = 'false';
    guispectorStatus.dataset.runtimeReady = 'false';
    byId('canonical-flow-status').className = 'module-status unavailable';
    byId('semantic-assist-status').textContent = 'Not connected';
    byId('semantic-assist-status').className = 'module-status unavailable';
    byId('import-package-button').disabled = true;
    byId('import-package').disabled = true;
    byId('run-list').innerHTML = '<p class="muted">Start the repository Inspector server to create and revisit local drafts. The standalone reviewer bundle remains read-only.</p>';
  }
  updateOptionalGuispectorStatus();

  byId('fill-example').addEventListener('click', () => {
    byId('requirement-input').value = 'I need a field-service page where a technician can search for equipment, report a problem, see clear validation errors, retry a failed submission, and confirm the final status.';
    byId('target-device-input').value = 'responsive_web';
    byId('constraints-input').value = 'Support keyboard operation\\nKeep recovery guidance visible after a failed submission';
  });
  byId('import-package').addEventListener('change', event => {
    byId('import-file-name').textContent = event.target.files[0]?.name || 'No file selected';
  });
  byId('analyze-requirement').addEventListener('click', async () => {
    setIntakeBusy(true, 'Checking the requirement');
    try {
      renderDiagnostics(await requestJson('/api/intake/analyze', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(intakePayload()),
      }));
    } catch (error) {
      byId('intake-result').innerHTML = `<div class="failure"><strong>Requirement check failed closed.</strong> ${escapeHtml(error.message)}</div>`;
    } finally {
      setIntakeBusy(false);
    }
  });
  byId('semantic-assist').addEventListener('click', async () => {
    setIntakeBusy(true, 'Running the semantic requirement assistant');
    try {
      renderSemanticAssist(await requestJson('/api/intake/semantic-assist', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(intakePayload()),
      }));
    } catch (error) {
      byId('intake-result').innerHTML = `<div class="failure"><strong>Semantic assistant failed closed.</strong> ${escapeHtml(error.message)}<br>The deterministic requirement and draft paths remain available.</div>`;
    } finally {
      setIntakeBusy(false);
    }
  });
  byId('requirement-form').addEventListener('submit', async event => {
    event.preventDefault();
    setIntakeBusy(true, 'Creating a deterministic draft');
    try {
      const record = await requestJson('/api/runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(intakePayload()),
      });
      renderRunTrace(record);
      await loadRuns();
    } catch (error) {
      byId('intake-result').innerHTML = `<div class="failure"><strong>Draft generation failed closed.</strong> ${escapeHtml(error.message)}</div>`;
      try { await loadRuns(); } catch (ignored) { /* Preserve the original failure. */ }
    } finally {
      setIntakeBusy(false);
    }
  });
  byId('canonical-browser').addEventListener('change', event => {
    if (!event.target.checked) byId('canonical-semantic').checked = false;
    byId('canonical-semantic').disabled = !event.target.checked;
  });
  byId('generate-canonical').addEventListener('click', async () => {
    setIntakeBusy(true, 'Starting the complete local flow');
    try {
      const record = await requestJson('/api/canonical-runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          ...intakePayload(),
          confirm_local_model_action: true,
          run_requirement_assist: byId('canonical-b-aux').checked,
          run_browser_acceptance: byId('canonical-browser').checked,
          run_semantic_acceptance: byId('canonical-semantic').checked,
        }),
      });
      renderRunTrace(record);
      setIntakeBusy(false);
      while (true) {
        await new Promise(resolve => setTimeout(resolve, 3000));
        const current = await requestJson(`/api/canonical-runs/${record.run_id}`);
        renderRunTrace(current, false);
        await loadRuns();
        if (!['queued', 'running'].includes(current.status)) break;
      }
    } catch (error) {
      byId('intake-result').innerHTML = `<div class="failure"><strong>Complete local flow failed closed.</strong> ${escapeHtml(error.message)}</div>`;
      try { await loadRuns(); } catch (ignored) { /* Preserve the original failure. */ }
    } finally {
      setIntakeBusy(false);
    }
  });
  byId('import-form').addEventListener('submit', async event => {
    event.preventDefault();
    const file = byId('import-package').files[0];
    if (!file) {
      byId('intake-result').innerHTML = '<div class="failure"><strong>Package import needs a ZIP file.</strong> Select one ResultPackage ZIP and try again.</div>';
      return;
    }
    setIntakeBusy(true, 'Validating an existing ResultPackage');
    try {
      const record = await requestJson('/api/imports', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/zip',
          'X-Req2Web-Filename': encodeURIComponent(file.name),
        },
        body: file,
      });
      renderRunTrace(record);
      byId('import-form').reset();
      byId('import-file-name').textContent = 'No file selected';
      await loadRuns();
    } catch (error) {
      byId('intake-result').innerHTML = `<div class="failure"><strong>Package import failed closed.</strong> ${escapeHtml(error.message)}</div>`;
    } finally {
      setIntakeBusy(false);
    }
  });
  byId('refresh-runs').addEventListener('click', async () => {
    try { await loadRuns(); } catch (error) {
      byId('run-list').innerHTML = `<div class="failure">${escapeHtml(error.message)}</div>`;
    }
  });
}

let previewMode = 'fit';
let auditedViewport = {width: 0, height: 0};

function viewportLabel(viewport) {
  return viewport.width && viewport.height
    ? `${viewport.width} × ${viewport.height}`
    : 'Viewport unavailable';
}

function renderPreviewLayout() {
  const stage = byId('preview-stage');
  const canvas = byId('preview-canvas');
  const frame = byId('final-page');
  const fitButton = byId('preview-fit');
  const auditButton = byId('preview-audit');
  fitButton.setAttribute('aria-pressed', String(previewMode === 'fit'));
  auditButton.setAttribute('aria-pressed', String(previewMode === 'audit'));

  if (previewMode === 'audit' && auditedViewport.width && auditedViewport.height) {
    const availableWidth = Math.max(280, stage.clientWidth - 34);
    const scale = Math.min(1, availableWidth / auditedViewport.width);
    canvas.style.width = `${Math.round(auditedViewport.width * scale)}px`;
    canvas.style.height = `${Math.round(auditedViewport.height * scale)}px`;
    frame.style.width = `${auditedViewport.width}px`;
    frame.style.height = `${auditedViewport.height}px`;
    frame.style.transform = `scale(${scale})`;
    byId('preview-viewport').textContent = `${viewportLabel(auditedViewport)} audited viewport · ${Math.round(scale * 100)}% display scale`;
    byId('preview-note').textContent = 'Audited-viewport mode uses the same responsive breakpoint as the captured Chrome evidence. Display scaling changes only its size inside the console.';
    return;
  }

  canvas.style.width = '100%';
  canvas.style.height = window.innerWidth <= 820 ? '680px' : '760px';
  frame.style.width = '100%';
  frame.style.height = '100%';
  frame.style.transform = 'none';
  byId('preview-viewport').textContent = 'Fit to panel';
  byId('preview-note').textContent = 'Fit-to-panel mode uses the available console width. Switch to the audited viewport for a like-for-like comparison with the captured Chrome evidence.';
}

function setPreviewMode(mode) {
  previewMode = mode;
  renderPreviewLayout();
}

function refreshPreviewState() {
  const frame = byId('final-page');
  try {
    const page = frame.contentDocument;
    const state = page?.getElementById('page-state');
    const stateName = state?.dataset.stateName || page?.body?.dataset.stateName || 'Ready';
    const message = state?.querySelector('.state-message')?.textContent || 'The packaged page is loaded. Actions update this state in place.';
    byId('preview-state-name').textContent = stateName;
    byId('preview-state-message').textContent = message;
  } catch (error) {
    byId('preview-state-name').textContent = 'Unavailable';
    byId('preview-state-message').textContent = 'The live state cannot be read from this page context.';
  }
}

function applyPreviewSafetyStyles() {
  const frame = byId('final-page');
  const page = frame.contentDocument;
  if (!page?.head || page.getElementById('req2web-inspector-preview-safety')) return;
  const style = page.createElement('style');
  style.id = 'req2web-inspector-preview-safety';
  style.textContent = `
    html, body { max-width: 100%; overflow-x: hidden; }
    .page-shell, .hero, .hero-copy, .page-meta, .page-meta div, .page-state,
    .section-list, .page-section, .section-heading, .component-list,
    .component, .component-copy, .page-footer, .page-footer span { min-width: 0; }
    .summary, .page-meta dd, .state-message, .section-kicker,
    .section-heading h2, .component-copy h3, .component-copy p,
    .fallback-note, .page-footer span { overflow-wrap: anywhere; word-break: break-word; }
  `;
  page.head.appendChild(style);
}

function connectPreviewStateMonitor() {
  const frame = byId('final-page');
  applyPreviewSafetyStyles();
  refreshPreviewState();
  const page = frame.contentDocument;
  if (!page || page.documentElement.dataset.inspectorMonitorAttached === 'true') return;
  page.documentElement.dataset.inspectorMonitorAttached = 'true';
  ['click', 'change', 'submit'].forEach(eventName => {
    page.addEventListener(eventName, () => window.setTimeout(refreshPreviewState, 0));
  });
}

function renderMetrics(counts) {
  const items = [
    ['9/12', 'historical first-pass delivery'],
    ['12/12', 'deliverable packages after zero-model policy replay'],
    ['12/12', 'objective Chrome and PageSpec pass'],
    ['24/24', 'supported semantic criteria'],
    ['48 + 12', 'generation and semantic calls, separate ledgers'],
  ];
  byId('metrics').innerHTML = items.map(([value, label]) =>
    `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`
  ).join('');
}

function renderGuispectorCase(report, index) {
  const item = report.cases[index];
  const requirement = item.guispector_requirement;
  byId('guispector-case').innerHTML = `<strong>${escapeHtml(requirement.title)}</strong><p>${escapeHtml(requirement.description.split('\\n')[0])}</p><ul>${requirement.acceptance_criteria.map(row => `<li><strong>${escapeHtml(row.criterion_name)}</strong> · ${escapeHtml(row.description)}</li>`).join('')}</ul><p><a href="${escapeHtml(item.bundle_relative_start_url)}" target="_blank" rel="noopener">Open exact package</a></p>`;
}

function renderGuispector(report) {
  const scope = report.scope;
  const current = report.current_result;
  const summary = [
    [scope.result_package_count, 'prepared result packages'],
    [scope.acceptance_criterion_count, 'fixed acceptance criteria'],
    [current.decision_count, 'external decisions imported'],
    ['Separate', 'acceptance result ledger'],
  ];
  byId('guispector-summary').innerHTML = summary.map(([value, label]) => `<div class="external-stat"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`).join('');
  const select = byId('guispector-case-select');
  select.innerHTML = report.cases.map((item, index) => `<option value="${index}">${String(item.execution_index).padStart(2, '0')} · ${escapeHtml(item.case_id)} · ${escapeHtml(item.condition_id)}</option>`).join('');
  select.addEventListener('change', () => renderGuispectorCase(report, Number(select.value)));
  byId('guispector-json').textContent = pretty({
    status: report.status,
    execution_performed: report.execution_performed,
    evaluation_identity: report.evaluation_identity,
    upstream: report.upstream,
    scope,
    input_policy: report.input_policy,
    current_result: current,
  });
  renderGuispectorCase(report, 0);
}

function selectedGuispectorModel() {
  return byId('guispector-provider').value === 'alibaba_gui_plus'
    ? 'gui-plus-2026-02-26'
    : 'glm-4.6v';
}

function updateGuispectorProvider() {
  const guiPlus = byId('guispector-provider').value === 'alibaba_gui_plus';
  byId('guispector-model').value = selectedGuispectorModel();
  byId('guispector-workspace-field').hidden = !guiPlus;
}

function updateOptionalGuispectorStatus() {
  const enabled = byId('guispector-enable').checked;
  const status = byId('guispector-runtime-status');
  byId('guispector-controls').hidden = !enabled;
  if (!enabled) {
    status.textContent = 'Off by default';
    status.className = 'capability-pill unavailable';
    return;
  }
  const runtimeReady = status.dataset.runtimeReady === 'true';
  status.textContent = runtimeReady
    ? 'Enabled · local runtime ready'
    : 'Enabled · local runtime setup may be required';
  status.className = `capability-pill ${runtimeReady ? 'available' : 'pending'}`;
  byId('guispector-test-connection').disabled = status.dataset.apiAvailable !== 'true';
  if (status.dataset.apiAvailable !== 'true') {
    byId('guispector-connection-result').textContent = 'Start the repository Inspector service to test a provider. Portable replay remains available.';
  }
}

async function testGuispectorConnection() {
  const apiKey = byId('guispector-api-key');
  const result = byId('guispector-connection-result');
  if (!byId('guispector-confirm').checked) {
    result.className = 'failure';
    result.textContent = 'Confirm the one-call external model action before continuing.';
    return;
  }
  byId('guispector-test-connection').disabled = true;
  result.className = 'media-note';
  result.textContent = 'Testing the selected provider with one request. No automatic retry is allowed.';
  try {
    const value = await requestJson('/api/guispector/test-connection', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        provider_id: byId('guispector-provider').value,
        model: selectedGuispectorModel(),
        api_key: apiKey.value,
        workspace_id: byId('guispector-workspace').value,
        confirm_external_model_action: true,
      }),
    });
    result.className = 'success';
    result.textContent = `${value.provider_model} connection passed. The key was not saved and no GUISpector verification was run.`;
  } catch (error) {
    result.className = 'failure';
    result.textContent = error.message;
  } finally {
    apiKey.value = '';
    byId('guispector-confirm').checked = false;
    byId('guispector-test-connection').disabled = byId('guispector-runtime-status').dataset.apiAvailable !== 'true';
  }
}

function renderRequirement(value) {
  const requirement = value.requirement || value.requirement_summary || 'Unavailable';
  const useCases = Array.isArray(value.use_cases) ? value.use_cases.length : 0;
  const constraints = Array.isArray(value.constraints) ? value.constraints.length : 0;
  byId('requirement-summary').innerHTML =
    `<p>${requirement}</p><p><strong>${useCases}</strong> use cases · <strong>${constraints}</strong> constraints · ${value.target_device || 'unspecified'} target</p>`;
  byId('requirement-json').textContent = pretty(value);
}

function renderEvidence(value) {
  byId('evidence-cards').innerHTML = ['F1', 'F2', 'F3', 'F4'].map(node => {
    const items = value[node]?.items || [];
    return `<div class="evidence-card"><h3>${node}</h3>${items.map(item =>
      `<p><strong>${item.role}</strong> — ${item.project_authored_nonverbatim_short_summary}</p>`
    ).join('')}</div>`;
  }).join('');
  byId('evidence-json').textContent = pretty(value);
}

async function showCase(item) {
  const record = await getJson(item.case_record);
  const a = record.artifacts;
  const [requirement, evidence, pageSpec, browserAudit, semantic] = await Promise.all([
    getJson(a.requirement), getJson(a.evidence_projection), getJson(a.page_spec),
    getJson(a.browser_audit), getJson(a.semantic_result),
  ]);
  renderRequirement(requirement);
  renderEvidence(evidence);
  byId('page-spec-json').textContent = pretty(pageSpec);
  byId('browser-json').textContent = pretty(browserAudit);
  byId('semantic-json').textContent = pretty(semantic);
  byId('historical-json').textContent = pretty(record.historical_source);
  byId('browser-screenshot').src = a.browser_screenshot;
  auditedViewport = {
    width: Number(browserAudit.viewport?.width || 0),
    height: Number(browserAudit.viewport?.height || 0),
  };
  byId('browser-viewport').textContent = viewportLabel(auditedViewport);
  byId('preview-state-name').textContent = 'Loading';
  byId('preview-state-message').textContent = 'The selected page has not finished loading.';
  byId('final-page').src = a.final_page;
  byId('open-page').href = a.final_page;
  renderPreviewLayout();
  byId('case-badges').innerHTML = [
    badge(record.historical_first_pass.passed ? 'historical first-pass' : 'historical fail-closed', !record.historical_first_pass.passed),
    badge('delivery available'), badge('browser pass'), badge('semantic 2/2 supported'),
  ].join('');

  const failure = record.historical_failure_location;
  byId('failure-location').innerHTML = failure
    ? `<div class="failure"><strong>Preserved historical failure:</strong> ${failure.failure_stage} / ${failure.failure_code}. The exact bytes later passed a zero-model policy replay; the 9/12 historical count is unchanged.</div>`
    : '<div class="success">Historical downstream first-pass success</div>';

  const nodes = await Promise.all(['F1', 'F2', 'F3', 'F4'].map(async node => ({
    node,
    output: await getJson(a.node_outputs[node]),
    attempt: await getJson(a.node_attempts[node]),
  })));
  byId('node-grid').innerHTML = nodes.map(({node, output, attempt}) =>
    `<article class="node"><h3>${node}</h3><div class="badges">${badge(attempt.status, attempt.status !== 'validated')}${badge(record.node_output_kinds[node].replaceAll('_', ' '), record.node_output_kinds[node] !== 'validated_node_output')}${badge(`retry ${attempt.retry_count}`)}</div><pre>${pretty(output)}</pre></article>`
  ).join('');

  byId('browser-summary').innerHTML = `<p><strong>${browserAudit.browser_execution_status}</strong> browser execution · <strong>${browserAudit.page_spec_conformance_status}</strong> PageSpec conformance · <strong>${browserAudit.interactions.length}</strong> recorded interactions · ${browserAudit.console_messages.length} console and ${browserAudit.page_errors.length} page errors.</p>`;
  byId('interaction-list').innerHTML = browserAudit.interactions.map(interaction =>
    `<li><strong>${escapeHtml(interaction.kind || interaction.action || interaction.interaction_id || 'interaction')}</strong> · ${escapeHtml(interaction.mechanism || interaction.status || interaction.outcome || 'recorded')}<small>${escapeHtml(interaction.selector || '')}</small></li>`
  ).join('');
  if (a.policy_revalidation) {
    const replay = await getJson(a.policy_revalidation);
    byId('revalidation-block').innerHTML = `<details open><summary>Zero-model policy replay</summary><pre>${pretty(replay)}</pre></details>`;
  } else {
    byId('revalidation-block').innerHTML = '';
  }
}

async function main() {
  const catalog = await getJson('catalog.json');
  const guispector = await getJson(catalog.guispector_evaluation);
  renderMetrics(catalog.counts);
  renderGuispector(guispector);
  const select = byId('case-select');
  select.innerHTML = catalog.cases.map(item =>
    `<option value="${item.execution_index - 1}">${String(item.execution_index).padStart(2, '0')} · ${item.case_id} · ${item.condition_id}</option>`
  ).join('');
  select.addEventListener('change', () => showCase(catalog.cases[Number(select.value)]));
  byId('preview-fit').addEventListener('click', () => setPreviewMode('fit'));
  byId('preview-audit').addEventListener('click', () => setPreviewMode('audit'));
  byId('final-page').addEventListener('load', connectPreviewStateMonitor);
  byId('guispector-enable').addEventListener('change', updateOptionalGuispectorStatus);
  byId('guispector-provider').addEventListener('change', updateGuispectorProvider);
  byId('guispector-test-connection').addEventListener('click', testGuispectorConnection);
  window.addEventListener('resize', renderPreviewLayout);
  updateGuispectorProvider();
  await initializeLiveInspector();
  await showCase(catalog.cases[0]);
}

main().catch(error => {
  document.body.innerHTML = `<main><section class="panel"><h1>Replay failed closed</h1><pre>${error.stack || error}</pre></section></main>`;
});
""".encode("utf-8")


_README_MD = """# Req2Web Inspector

This directory is the self-contained, read-only reviewer replay for the single
Req2Web Inspector. It replays twelve frozen Phase 5 engineering rows and lets
a reviewer inspect the requirement, evidence
projection, validated F1-F4 outputs, preserved failure location, final
PageSpec, objective Chrome evidence, semantic sidecar, screenshot, interaction
trace, and runnable final page. A default-off GUISpector option maps the same
twelve exact pages to their frozen requirements and twenty-four acceptance
criteria. Its result remains separate from Req2Web's canonical evidence.

When served from the repository with `scripts/run_req2web_inspector.py`, the
same interface also accepts irregular requirements, runs deterministic input
diagnostics, creates component-level guided/G0 drafts, keeps immutable local
run history, imports strictly validated existing ResultPackage v1/v2 ZIP files,
and exports validated ResultPackage v2 ZIP files. Imported packages are not
rerun or relabeled. Deterministic diagnostics, drafts, and imports call no
model or external service. A repository operator may explicitly enable the
isolated advisory-only local Qwen requirement assistant; it writes raw output
first, makes at most one call, never writes back to canonical B, and never
enters F1-F4. The extracted reviewer bundle keeps that control visibly
disconnected. F1-F4 model generation, semantic acceptance, and real browser
acceptance for new drafts remain explicitly not executed.

No GPU, model, hidden material, network connection, or browser automation is
required. The replay does not regenerate or repair any evidence.

The optional GUISpector packet is `guispector_evaluation.json`. The module is
off by default. A user may explicitly select a supported provider profile and
run one connection test; the API key is not persisted and there is no automatic
retry. Full verification remains an operator-started action in the local
GUISpector runtime. It is an optional acceptance strategy, not a paper
comparison, independent gold, or formal evaluation.

## Run

From the repository root:

```powershell
python scripts/run_req2web_inspector.py --validate-only
python scripts/run_req2web_inspector.py
```

Then open `http://127.0.0.1:8765/`.

With only this directory and Python available:

```powershell
python validate_bundle.py
python serve_bundle.py --port 8765
```

The extracted reviewer directory is intentionally read-only. Its Create and
Runs panels explain that repository service capabilities are unavailable while
all frozen replay functions continue to work.

## Interpretation

- Historical downstream first-pass delivery remains 9/12.
- A zero-model policy replay makes twelve ResultPackages available but does
  not rewrite the historical count.
- Objective Chrome execution and PageSpec conformance passed 12/12 with 36
  interactions and zero console or page errors.
- The separate semantic sidecar accepted 12/12 results and marked 24/24
  criteria supported with zero retry.
- The 48 F1-F4 generation calls and 12 semantic calls remain separate ledgers.
- Development annotation, experiment-comparison, release-management, and
  submission-work panels are intentionally absent from the user-facing
  Inspector.

This is bounded engineering evidence, not H1/gold, formal evaluation, broad
generalization, training, LoRA, production, or user-study evidence.

## License gate

The bundle is technically runnable, but public redistribution is not yet
authorized by a repository license. See `MATERIALS.json`. The project owner
must make an explicit license decision before publication.
""".encode("utf-8")


_STANDALONE_VALIDATE_PY = r'''"""Validate this reviewer bundle with the Python standard library."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import sys


EXPECTED_SCHEMA = "req2web.phase6.reviewer_replay.v2"


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or ".." in path.parts
        or str(path) != value
    ):
        raise ValueError("unsafe bundle-relative path")
    return value


def _load_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing regular file: {path.name}")
    value = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path.name}")
    return value


def validate(root: Path) -> dict:
    bundle = root.resolve(strict=True)
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("bundle root must be a regular directory")
    manifest = _load_json(bundle / "replay_manifest.json")
    if manifest.get("schema_version") != EXPECTED_SCHEMA:
        raise ValueError("reviewer replay schema drifted")
    if manifest.get("status") != "precomputed_replay_only":
        raise ValueError("reviewer replay status drifted")
    if any(
        manifest.get(key) is not False
        for key in (
            "historical_first_pass_count_rewritten",
            "generation_and_semantic_call_ledgers_merged",
            "model_or_gpu_required",
            "hidden_material_required",
        )
    ):
        raise ValueError("reviewer replay boundary flags drifted")
    expected_paths = []
    for row in manifest.get("files", []):
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "byte_length"}:
            raise ValueError("manifest file row drifted")
        relative = _safe_relative(str(row["path"]))
        path = bundle / Path(relative)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"bundle file missing: {relative}")
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != row["sha256"] or len(raw) != row["byte_length"]:
            raise ValueError(f"bundle file drifted: {relative}")
        expected_paths.append(relative)
    if expected_paths != sorted(set(expected_paths)):
        raise ValueError("manifest paths are not sorted and unique")
    actual_paths = []
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            raise ValueError("bundle contains a symlink")
        if path.is_file() and path.name != "replay_manifest.json":
            actual_paths.append(path.relative_to(bundle).as_posix())
    actual_paths.sort()
    if actual_paths != expected_paths:
        raise ValueError("manifest inventory is incomplete")
    counts = manifest.get("counts", {})
    if not isinstance(counts, dict) or counts.get("row_count") != 12:
        raise ValueError("reviewer row count drifted")
    if counts.get("historical_first_pass_count") != 9:
        raise ValueError("historical first-pass count drifted")
    materials = _load_json(bundle / "MATERIALS.json")
    if (
        materials.get("public_release_ready") is not False
        or materials.get("blocking_gate") != "owner_license_decision_required"
    ):
        raise ValueError("license gate drifted")
    return manifest


if __name__ == "__main__":
    try:
        result = validate(Path(__file__).resolve().parent)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed_closed", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({"status": "validated_precomputed_replay", "counts": result["counts"]}, sort_keys=True))
'''.encode("utf-8")


_STANDALONE_SERVE_PY = r'''"""Validate and serve this reviewer bundle on loopback only."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from validate_bundle import validate


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and serve the local Req2Web reviewer bundle.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = validate(root)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(root)))
    print(f"Validated {manifest['counts']['row_count']} rows; open http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
'''.encode("utf-8")


def _copy_case_sources(
    *,
    archive: tarfile.TarFile,
    bundle_root: Path,
    revalidation_root: Path,
    browser_root: Path,
    semantic_results_root: Path,
    index: int,
    record: Mapping[str, Any],
) -> None:
    row_id = str(record["row_id"])
    source_root = f"rows/{index:02d}-{row_id}"
    case_root = bundle_root / "cases" / f"{index:02d}"
    tar_targets = {
        "b_input.json": case_root / "requirement.json",
        "provider_evidence_projection.json": case_root / "evidence_projection.json",
    }
    for node_id in _NODE_IDS:
        node_leaf = node_id.lower()
        tar_targets[f"attempts/{node_id}/attempt_result.json"] = (
            case_root / "nodes" / f"{node_leaf}_attempt.json"
        )
        output_member = (
            f"attempts/{node_id}/raw_response.bin"
            if index in HISTORICAL_FAILED_CLOSED_INDICES and node_id == "F4"
            else f"attempts/{node_id}/validated_node_output.json"
        )
        tar_targets[output_member] = (
            case_root / "nodes" / f"{node_leaf}_output.json"
        )
    for source_relative, destination in tar_targets.items():
        raw = _tar_member_bytes(archive, f"{source_root}/{source_relative}")
        if source_relative.endswith("/raw_response.bin"):
            parsed = _json_from_bytes(raw, f"row {index} preserved F4 response")
            if not isinstance(parsed.get("acceptance_checks"), list):
                raise Phase6ReplayError(
                    f"row {index} preserved F4 response has no acceptance checks"
                )
        _write_bytes(destination, raw)
    package_source = revalidation_root / "packages" / f"{index:02d}-{row_id}"
    for relative in _PACKAGE_FILES:
        _copy_regular_file(
            package_source / Path(relative),
            case_root / "package" / Path(relative),
        )
    browser_source = browser_root / "cases" / f"{index:02d}"
    for relative in _BROWSER_FILES:
        _copy_regular_file(
            browser_source / relative,
            case_root / "browser" / relative,
        )
    semantic_source = semantic_results_root / f"{index:02d}-{row_id}"
    _copy_regular_file(
        semantic_source / "semantic_alignment_result.json",
        case_root / "semantic_alignment_result.json",
    )
    if index in HISTORICAL_FAILED_CLOSED_INDICES:
        _copy_regular_file(
            revalidation_root / "row_receipts" / f"{index:02d}.json",
            case_root / "policy_revalidation.json",
        )
    _write_json(case_root / "case.json", record)


def _file_inventory(bundle_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(bundle_root.rglob("*")):
        if path.is_symlink():
            raise Phase6ReplayError("reviewer bundle must not contain symlinks")
        if not path.is_file() or path.name == "replay_manifest.json":
            continue
        relative = path.relative_to(bundle_root).as_posix()
        _safe_relative_path(relative)
        raw = path.read_bytes()
        rows.append(
            {
                "path": relative,
                "sha256": sha256(raw).hexdigest(),
                "byte_length": len(raw),
            }
        )
    if not rows:
        raise Phase6ReplayError("reviewer bundle inventory is empty")
    return sorted(rows, key=lambda row: str(row["path"]))


def build_phase6_reviewer_bundle(
    *,
    result_tar_path: Path,
    result_manifest_path: Path,
    revalidation_root: Path,
    browser_root: Path,
    semantic_manifest_path: Path,
    semantic_summary_path: Path,
    semantic_results_root: Path,
    retrieval_fixture_path: Path,
    retrieval_index_dir: Path,
    retrieval_comparison_root: Path,
    retrieval_experiment_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Create one immutable, precomputed twelve-row reviewer bundle."""

    paths = {
        "result_tar_path": Path(result_tar_path).resolve(strict=True),
        "result_manifest_path": Path(result_manifest_path).resolve(strict=True),
        "revalidation_root": Path(revalidation_root).resolve(strict=True),
        "browser_root": Path(browser_root).resolve(strict=True),
        "semantic_manifest_path": Path(semantic_manifest_path).resolve(strict=True),
        "semantic_summary_path": Path(semantic_summary_path).resolve(strict=True),
        "semantic_results_root": Path(semantic_results_root).resolve(strict=True),
    }
    retrieval_fixture = Path(retrieval_fixture_path).resolve(strict=True)
    retrieval_index = Path(retrieval_index_dir).resolve(strict=True)
    retrieval_root = Path(retrieval_comparison_root).resolve(strict=True)
    experiment_root = Path(retrieval_experiment_root).resolve(strict=True)
    target = Path(output_root).resolve()
    if target.exists() or target.is_symlink():
        raise Phase6ReplayError("output root already exists; replay bundles are immutable")
    target.parent.mkdir(parents=True, exist_ok=True)
    sources = _source_bundle(**paths)
    retrieval_comparison = validate_retrieval_comparison(
        fixture_path=retrieval_fixture,
        index_dir=retrieval_index,
        output_root=retrieval_root,
    )
    retrieval_experiment_summary = validate_retrieval_experiment(
        fixture_path=retrieval_fixture,
        index_dir=retrieval_index,
        comparison_root=retrieval_root,
        output_root=experiment_root,
    )
    retrieval_experiment = {
        "schema_version": RETRIEVAL_EXPERIMENT_SCHEMA_VERSION,
        "status": retrieval_experiment_summary["status"],
        "llm_prelabel_template_count": retrieval_experiment_summary[
            "llm_prelabel_template_count"
        ],
        "human_reviewer_count": retrieval_experiment_summary[
            "human_reviewer_count"
        ],
        "blind_overlap_audit_count": retrieval_experiment_summary[
            "blind_overlap_audit_count"
        ],
        "judgments_per_human_reviewer": retrieval_experiment_summary[
            "judgments_per_human_reviewer"
        ],
        "ranking_quality_computed": retrieval_experiment_summary[
            "ranking_quality_computed"
        ],
        "winner_declared": retrieval_experiment_summary["winner_declared"],
        "protocol": _read_json(
            experiment_root / "experiment_protocol.json",
            "retrieval experiment protocol",
        ),
        "efficiency": retrieval_experiment_summary["efficiency"],
    }
    amended_rows = _rows_by_index(sources["amended"], "amended summary")
    browser_rows = _rows_by_index(sources["browser"], "browser summary")
    semantic_rows = _rows_by_index(sources["semantic"], "semantic summary")
    temp_root = target.with_name(f".{target.name}.building")
    if temp_root.exists() or temp_root.is_symlink():
        raise Phase6ReplayError("reviewer bundle build root already exists")
    # tempfile.mkdtemp(mode=0o700) produces inaccessible directories under
    # some managed Windows hosts. A normal, exact sibling directory preserves
    # the same atomic-publish behavior without that host-specific ACL issue.
    temp_root.mkdir()
    try:
        case_records: list[dict[str, Any]] = []
        with tarfile.open(paths["result_tar_path"], mode="r:") as archive:
            for index in range(1, ROW_COUNT + 1):
                amended_row = amended_rows[index]
                browser_row = browser_rows[index]
                semantic_row = semantic_rows[index]
                row_id, _, _ = _require_same_row(
                    index,
                    amended_row,
                    browser_row,
                    semantic_row,
                )
                historical_raw = _tar_member_bytes(
                    archive,
                    f"rows/{index:02d}-{row_id}/p4_05_final_result.json",
                )
                historical_result = _json_from_bytes(
                    historical_raw,
                    f"row {index} historical result",
                )
                record = _case_record(
                    index=index,
                    amended_row=amended_row,
                    browser_row=browser_row,
                    semantic_row=semantic_row,
                    historical_result=historical_result,
                    historical_result_sha256=sha256(historical_raw).hexdigest(),
                )
                _copy_case_sources(
                    archive=archive,
                    bundle_root=temp_root,
                    revalidation_root=paths["revalidation_root"],
                    browser_root=paths["browser_root"],
                    semantic_results_root=paths["semantic_results_root"],
                    index=index,
                    record=record,
                )
                case_records.append(record)
        counts = {
            "case_count": 4,
            "condition_count": 3,
            "row_count": ROW_COUNT,
            "generation_call_count": GENERATION_CALL_COUNT,
            "generation_automatic_retry_count": 0,
            "historical_first_pass_count": HISTORICAL_FIRST_PASS_COUNT,
            "historical_failed_closed_count": len(HISTORICAL_FAILED_CLOSED_INDICES),
            "delivery_evidence_available_count": ROW_COUNT,
            "browser_execution_pass_count": ROW_COUNT,
            "page_spec_conformance_pass_count": ROW_COUNT,
            "browser_interaction_count": BROWSER_INTERACTION_COUNT,
            "browser_console_error_count": 0,
            "browser_page_error_count": 0,
            "semantic_call_count": SEMANTIC_CALL_COUNT,
            "semantic_automatic_retry_count": 0,
            "semantic_accepted_count": ROW_COUNT,
            "semantic_supported_criterion_count": SEMANTIC_CRITERION_COUNT,
        }
        catalog = {
            "schema_version": PHASE6_REPLAY_MANIFEST_SCHEMA_VERSION,
            "status": "precomputed_replay_only",
            "counts": counts,
            "claim_boundary": (
                "bounded project-authored engineering evidence only; H1/gold, "
                "formal evaluation, formal quality, broad generalization, training, "
                "LoRA, production, and user-study claims remain closed"
            ),
            "retrieval_comparison": "retrieval_comparison.json",
            "retrieval_experiment": "retrieval_experiment.json",
            "guispector_evaluation": "guispector_evaluation.json",
            "cases": [
                {
                    "execution_index": record["execution_index"],
                    "case_id": record["case_id"],
                    "condition_id": record["condition_id"],
                    "case_record": f"cases/{record['execution_index']:02d}/case.json",
                }
                for record in case_records
            ],
        }
        try:
            guispector_evaluation = build_guispector_evaluation(temp_root, catalog)
        except GUISpectorSidecarError as exc:
            raise Phase6ReplayError(
                f"GUISpector evaluation packet failed closed: {exc}"
            ) from exc
        _write_json(temp_root / "retrieval_comparison.json", retrieval_comparison)
        _write_json(temp_root / "retrieval_experiment.json", retrieval_experiment)
        _write_json(temp_root / "guispector_evaluation.json", guispector_evaluation)
        _write_json(temp_root / "catalog.json", catalog)
        _write_json(temp_root / "MATERIALS.json", _material_inventory())
        _write_bytes(temp_root / "README.md", _README_MD)
        _write_bytes(temp_root / "index.html", _INDEX_HTML)
        _write_bytes(temp_root / "styles.css", _STYLES_CSS)
        _write_bytes(temp_root / "app.js", _APP_JS)
        _write_bytes(temp_root / "validate_bundle.py", _STANDALONE_VALIDATE_PY)
        _write_bytes(temp_root / "serve_bundle.py", _STANDALONE_SERVE_PY)
        return_manifest = sources["return_manifest"]
        replay_manifest = {
            "schema_version": PHASE6_REPLAY_MANIFEST_SCHEMA_VERSION,
            "status": "precomputed_replay_only",
            "source_bindings": {
                "publication_return_manifest_id": return_manifest["manifest_id"],
                "publication_return_tar_sha256": return_manifest["tar_sha256"],
                "amended_summary_identity": sources["amended"]["summary_identity"],
                "browser_summary_identity": sources["browser"]["summary_identity"],
                "semantic_manifest_identity": sources["semantic_manifest"]["manifest_identity"],
                "semantic_summary_identity": sources["semantic"]["summary_identity"],
                "retrieval_comparison_report_sha256": sha256(
                    (retrieval_root / "comparison_report.json").read_bytes()
                ).hexdigest(),
                "retrieval_experiment_manifest_sha256": sha256(
                    (experiment_root / "experiment_manifest.json").read_bytes()
                ).hexdigest(),
            },
            "counts": counts,
            "historical_first_pass_count_rewritten": False,
            "generation_and_semantic_call_ledgers_merged": False,
            "model_or_gpu_required": False,
            "hidden_material_required": False,
            "files": _file_inventory(temp_root),
        }
        _write_json(temp_root / "replay_manifest.json", replay_manifest)
        validate_phase6_reviewer_bundle(temp_root)
        temp_root.rename(target)
        return validate_phase6_reviewer_bundle(target)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def validate_phase6_reviewer_bundle(root: Path) -> dict[str, Any]:
    """Validate a reviewer bundle using only files inside that bundle."""

    bundle_root = Path(root).resolve(strict=True)
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise Phase6ReplayError("reviewer bundle root is invalid")
    manifest = _read_json(
        bundle_root / "replay_manifest.json",
        "reviewer replay manifest",
    )
    if manifest.get("schema_version") != PHASE6_REPLAY_MANIFEST_SCHEMA_VERSION:
        raise Phase6ReplayError("reviewer replay schema drifted")
    if manifest.get("status") != "precomputed_replay_only":
        raise Phase6ReplayError("reviewer replay status drifted")
    if manifest.get("historical_first_pass_count_rewritten") is not False:
        raise Phase6ReplayError("reviewer replay rewrites historical accounting")
    if manifest.get("generation_and_semantic_call_ledgers_merged") is not False:
        raise Phase6ReplayError("reviewer replay merges separate call ledgers")
    if manifest.get("model_or_gpu_required") is not False:
        raise Phase6ReplayError("reviewer replay requires a model or GPU")
    if manifest.get("hidden_material_required") is not False:
        raise Phase6ReplayError("reviewer replay requires hidden material")
    counts = _mapping(manifest.get("counts"), "reviewer replay counts")
    expected_counts = {
        "row_count": ROW_COUNT,
        "generation_call_count": GENERATION_CALL_COUNT,
        "historical_first_pass_count": HISTORICAL_FIRST_PASS_COUNT,
        "historical_failed_closed_count": len(HISTORICAL_FAILED_CLOSED_INDICES),
        "delivery_evidence_available_count": ROW_COUNT,
        "browser_execution_pass_count": ROW_COUNT,
        "page_spec_conformance_pass_count": ROW_COUNT,
        "browser_interaction_count": BROWSER_INTERACTION_COUNT,
        "semantic_call_count": SEMANTIC_CALL_COUNT,
        "semantic_accepted_count": ROW_COUNT,
        "semantic_supported_criterion_count": SEMANTIC_CRITERION_COUNT,
    }
    for key, expected in expected_counts.items():
        if counts.get(key) != expected:
            raise Phase6ReplayError(f"reviewer replay {key} drifted")
    file_rows = _list(manifest.get("files"), "reviewer replay files")
    observed_paths: list[str] = []
    for raw in file_rows:
        row = _mapping(raw, "reviewer replay file")
        if set(row) != {"path", "sha256", "byte_length"}:
            raise Phase6ReplayError("reviewer replay file keys drifted")
        relative = _safe_relative_path(str(row["path"]))
        path = bundle_root / Path(relative)
        if path.is_symlink() or not path.is_file():
            raise Phase6ReplayError(f"reviewer replay file is missing: {relative}")
        raw_bytes = path.read_bytes()
        if (
            row["sha256"] != sha256(raw_bytes).hexdigest()
            or row["byte_length"] != len(raw_bytes)
        ):
            raise Phase6ReplayError(f"reviewer replay file drifted: {relative}")
        observed_paths.append(relative)
    if observed_paths != sorted(set(observed_paths)):
        raise Phase6ReplayError("reviewer replay files are not sorted and unique")
    actual_paths = [row["path"] for row in _file_inventory(bundle_root)]
    if observed_paths != actual_paths:
        raise Phase6ReplayError("reviewer replay file inventory is incomplete")
    catalog = _read_json(bundle_root / "catalog.json", "reviewer catalog")
    catalog_cases = _list(catalog.get("cases"), "reviewer catalog cases")
    if catalog.get("counts") != counts or len(catalog_cases) != ROW_COUNT:
        raise Phase6ReplayError("reviewer catalog aggregate drifted")
    guispector_path = _safe_relative_path(str(catalog.get("guispector_evaluation")))
    if guispector_path != "guispector_evaluation.json":
        raise Phase6ReplayError("GUISpector evaluation path drifted")
    try:
        guispector = validate_guispector_evaluation(bundle_root)
    except GUISpectorSidecarError as exc:
        raise Phase6ReplayError(
            f"GUISpector evaluation packet failed closed: {exc}"
        ) from exc
    guispector_scope = _mapping(guispector.get("scope"), "GUISpector scope")
    guispector_reference = _mapping(
        guispector.get("reference_profile"),
        "GUISpector reference profile",
    )
    if (
        guispector.get("schema_version") != GUISPECTOR_EVALUATION_SCHEMA_VERSION
        or guispector.get("status") != "prepared_not_executed"
        or guispector.get("execution_performed") is not False
        or guispector_scope.get("result_package_count") != ROW_COUNT
        or guispector_scope.get("acceptance_criterion_count")
        != SEMANTIC_CRITERION_COUNT
        or guispector_reference.get("independent_human_gold") is not False
        or guispector_reference.get("paper_comparison_eligible") is not False
    ):
        raise Phase6ReplayError("GUISpector evaluation claim boundary drifted")
    retrieval_path = _safe_relative_path(str(catalog.get("retrieval_comparison")))
    retrieval = _read_json(
        bundle_root / Path(retrieval_path),
        "retrieval comparison",
    )
    ranking = _mapping(
        retrieval.get("ranking_evaluation"),
        "retrieval ranking evaluation",
    )
    pairwise = _mapping(
        retrieval.get("pairwise_summary"),
        "retrieval pairwise summary",
    )
    if (
        retrieval.get("schema_version") != RETRIEVAL_COMPARISON_SCHEMA_VERSION
        or retrieval.get("methods") != ["bm25", "rrf", "tfidf"]
        or retrieval.get("query_role_unit_count") != 60
        or len(_list(retrieval.get("units"), "retrieval comparison units")) != 60
        or ranking.get("status") != "not_computed_no_independent_qrels"
        or ranking.get("winner_declared") is not False
        or pairwise.get("quality_winner_declared") is not False
        or pairwise.get("comparison_count") != 3
        or len(_list(pairwise.get("comparisons"), "retrieval pair comparisons"))
        != 3
    ):
        raise Phase6ReplayError("retrieval comparison claim boundary drifted")
    experiment_path = _safe_relative_path(str(catalog.get("retrieval_experiment")))
    experiment = _read_json(
        bundle_root / Path(experiment_path),
        "retrieval experiment",
    )
    experiment_protocol = _mapping(
        experiment.get("protocol"),
        "retrieval experiment protocol",
    )
    experiment_efficiency = _mapping(
        experiment.get("efficiency"),
        "retrieval experiment efficiency",
    )
    efficiency_methods = [
        _mapping(raw, "retrieval experiment efficiency method")
        for raw in _list(
            experiment_efficiency.get("methods"),
            "retrieval experiment efficiency methods",
        )
    ]
    if (
        experiment.get("schema_version") != RETRIEVAL_EXPERIMENT_SCHEMA_VERSION
        or experiment.get("status")
        != "prepared_awaiting_llm_prelabels"
        or experiment.get("llm_prelabel_template_count") != 1
        or experiment.get("human_reviewer_count") != 2
        or experiment.get("blind_overlap_audit_count") != 84
        or experiment.get("judgments_per_human_reviewer") != 252
        or experiment.get("ranking_quality_computed") is not False
        or experiment.get("winner_declared") is not False
        or experiment_protocol.get("primary_metrics")
        != ["pooled_ndcg_at_5", "pooled_recall_at_5"]
        or experiment_protocol.get("statistical_unit") != "case"
        or [row.get("backend") for row in efficiency_methods]
        != ["bm25", "rrf", "tfidf"]
        or any(
            row.get("all_repetitions_exactly_match_comparison") is not True
            for row in efficiency_methods
        )
    ):
        raise Phase6ReplayError("retrieval experiment claim boundary drifted")
    failed_indices: list[int] = []
    for expected_index, item_raw in enumerate(catalog_cases, start=1):
        item = _mapping(item_raw, f"catalog case {expected_index}")
        if item.get("execution_index") != expected_index:
            raise Phase6ReplayError("reviewer catalog execution order drifted")
        case_path = _safe_relative_path(str(item.get("case_record")))
        case = _read_json(bundle_root / Path(case_path), f"case {expected_index}")
        if (
            case.get("schema_version") != PHASE6_REVIEWER_CASE_SCHEMA_VERSION
            or case.get("execution_index") != expected_index
            or case.get("case_id") != item.get("case_id")
            or case.get("condition_id") != item.get("condition_id")
        ):
            raise Phase6ReplayError(f"case {expected_index} identity drifted")
        historical = _mapping(
            case.get("historical_first_pass"),
            f"case {expected_index} historical status",
        )
        if historical.get("passed") is False:
            failed_indices.append(expected_index)
            failure = _mapping(
                case.get("historical_failure_location"),
                f"case {expected_index} failure location",
            )
            if (
                failure.get("failure_stage") != "F4"
                or failure.get("zero_model_policy_revalidated") is not True
            ):
                raise Phase6ReplayError(
                    f"case {expected_index} fail-closed evidence drifted"
                )
        elif historical.get("passed") is not True:
            raise Phase6ReplayError(
                f"case {expected_index} historical status is invalid"
            )
    if tuple(failed_indices) != HISTORICAL_FAILED_CLOSED_INDICES:
        raise Phase6ReplayError("historical failed-closed row set drifted")
    materials = _read_json(bundle_root / "MATERIALS.json", "material inventory")
    if (
        materials.get("schema_version") != PHASE6_MATERIALS_SCHEMA_VERSION
        or materials.get("public_release_ready") is not False
        or materials.get("blocking_gate") != "owner_license_decision_required"
    ):
        raise Phase6ReplayError("material and license gate drifted")
    return manifest


__all__ = [
    "PHASE6_MATERIALS_SCHEMA_VERSION",
    "PHASE6_REPLAY_MANIFEST_SCHEMA_VERSION",
    "PHASE6_REVIEWER_CASE_SCHEMA_VERSION",
    "Phase6ReplayError",
    "build_phase6_reviewer_bundle",
    "validate_phase6_reviewer_bundle",
]
