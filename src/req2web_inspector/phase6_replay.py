"""Build and validate the read-only Phase 6 reviewer replay bundle.

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
                    "Provider-neutral LLM prelabel template, deterministic two-human "
                    "review assignment with an 84-candidate blind overlap audit, "
                    "exploratory metric protocol, and descriptive local efficiency report"
                ),
                "origin": "project-authored local experiment preparation",
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
  <title>Req2Web Phase 6 Inspector</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="site-header">
    <a class="brand" href="#top" aria-label="Req2Web Inspector home">
      <span class="brand-mark">R2W</span>
      <span><strong>Req2Web</strong><small>Phase 6 Inspector</small></span>
    </a>
    <nav aria-label="Inspector sections">
      <a href="#overview">Overview</a>
      <a href="#human-work">Human handoff</a>
      <a href="#retrieval">Retrieval</a>
      <a href="#case-inspector">Evidence</a>
      <a href="#final-result">Result</a>
    </nav>
    <span class="local-status"><i></i> Local replay</span>
  </header>

  <div class="app-shell" id="top">
    <section class="hero" id="overview">
      <div class="hero-copy">
        <p class="eyebrow">REVIEWER-READY ENGINEERING REPLAY</p>
        <h1>Inspect every step from requirement to runnable page.</h1>
        <p class="lede">A focused, read-only workspace for twelve precomputed Req2Web evaluation rows. Review requirements, evidence, F1-F4 outputs, browser traces, retrieval candidates, and final pages without a GPU or model runtime.</p>
        <div class="hero-actions">
          <a class="button" href="#case-inspector">Inspect evidence</a>
          <a class="button secondary" href="#human-work">View human tasks</a>
        </div>
      </div>
      <aside class="release-card" aria-label="Release status">
        <span class="section-kicker">Release state</span>
        <strong>Technically reproducible</strong>
        <p>Standalone validation and local replay are ready.</p>
        <div class="status-line warning"><i></i> Public license decision pending</div>
      </aside>
    </section>

    <main>
    <section class="metrics" id="metrics" aria-label="Evidence summary"></section>
    <section class="separation-note">
      <span class="notice-label">Accounting boundary</span>
      <div><strong>Three ledgers stay separate.</strong><p>Historical first-pass delivery, objective browser checks, and semantic alignment answer different questions and are never merged into one score.</p></div>
    </section>

    <section class="human-section" id="human-work">
      <div class="section-intro">
        <div><span class="section-kicker">Human handoff</span><h2>Three decisions remain outside automation.</h2></div>
        <p>All authorized non-human engineering is complete. These tasks require independent judgment, legal ownership, or authored submission material.</p>
      </div>
      <div class="human-grid">
        <article class="task-card">
          <div class="task-meta"><span>01</span><em>LLM prelabel + two reviewers</em></div>
          <h3>Judge retrieval relevance</h3>
          <p>A provider-neutral model packet covers all 420 method-blind candidates. Two people then review 252 items each: 168 assisted primary items plus the same 84-item blind overlap audit. They jointly resolve only that overlap after both individual packets are frozen.</p>
          <div class="task-links">
            <a href="human_tasks/retrieval/llm_prelabel_template.json" download>LLM prelabel packet</a>
            <a href="human_tasks/retrieval/human_review_assignment.json" download>Human assignment</a>
            <a href="human_tasks/README.md">Exact instructions</a>
          </div>
          <small>No API is called by the bundle. The labels remain exploratory and are never presented as independent human gold or H1.</small>
        </article>
        <article class="task-card">
          <div class="task-meta"><span>02</span><em>Project owner + legal review</em></div>
          <h3>Approve license and materials</h3>
          <p>Select the software and content license, confirm screenshot and project-authored case redistribution, and approve the exact candidate archive identity.</p>
          <a class="task-link" href="MATERIALS.json">Open material inventory</a>
          <small>Done when license text, notices, material approval, and archive SHA-256 are recorded together.</small>
        </article>
        <article class="task-card">
          <div class="task-meta"><span>03</span><em>Authors</em></div>
          <h3>Prepare submission material</h3>
          <p>Record the short demonstration, write the paper, select screenshots, and assemble the final submission without expanding the evaluation claims.</p>
          <small>Done when the video, paper, limitations, artifact link, and submission metadata are reviewed by the authors.</small>
        </article>
      </div>
    </section>

    <section class="panel retrieval-panel" id="retrieval">
      <div class="section-heading">
        <div><span class="section-kicker">Retrieval laboratory</span><h2>Compare candidates without overstating quality.</h2><p class="muted">BM25, RRF, and TF-IDF use the same corpus, deterministic case-role queries, and result projection. Candidate differences, downstream utility, and efficiency stay separate; exploratory relevance metrics require complete model prelabels and the frozen two-human review.</p></div>
        <div id="retrieval-gate"></div>
      </div>
      <div id="retrieval-summary" class="retrieval-summary"></div>
      <div class="control-row">
        <label for="retrieval-unit-select"><span>Case-role candidate view</span><select id="retrieval-unit-select"></select></label>
        <p>Raw scores are backend-specific and must not be compared across methods.</p>
      </div>
      <div id="retrieval-candidates" class="retrieval-candidates"></div>
      <div class="details-grid">
        <details><summary>Metric policy and exact comparison report</summary><pre id="retrieval-json"></pre></details>
        <details><summary>Assisted review protocol and descriptive efficiency</summary><pre id="retrieval-experiment-json"></pre></details>
      </div>
    </section>

    <section class="case-toolbar" id="case-inspector">
      <div class="toolbar-title"><span class="section-kicker">Evidence explorer</span><strong>Select one frozen row</strong></div>
      <label for="case-select"><span class="sr-only">Evidence row</span><select id="case-select"></select></label>
      <div id="case-badges" class="badges"></div>
    </section>

    <section class="overview-grid">
      <article class="panel">
        <h2>Requirement</h2>
        <div id="requirement-summary" class="prose"></div>
        <details><summary>Exact canonical input</summary><pre id="requirement-json"></pre></details>
      </article>
      <article class="panel">
        <h2>Evidence projection</h2>
        <p class="muted">Project-authored, non-verbatim summaries visible to each generation node.</p>
        <div id="evidence-cards" class="evidence-cards"></div>
        <details><summary>Exact projection JSON</summary><pre id="evidence-json"></pre></details>
      </article>
    </section>

    <section class="panel">
      <div class="section-heading">
        <div><h2>F1-F4 structured outputs</h2><p class="muted">Exact validated outputs, or preserved strict JSON at the historical F4 policy boundary. The Inspector does not regenerate them.</p></div>
        <div id="failure-location"></div>
      </div>
      <div id="node-grid" class="node-grid"></div>
    </section>

    <section class="overview-grid">
      <article class="panel">
        <h2>Final PageSpec</h2>
        <pre id="page-spec-json" class="tall"></pre>
      </article>
      <article class="panel">
        <h2>Objective browser evidence</h2>
        <div id="browser-summary" class="prose"></div>
        <img id="browser-screenshot" alt="Captured Chrome result for the selected row">
        <ol id="interaction-list" class="interaction-list"></ol>
        <details><summary>Interaction trace and browser audit</summary><pre id="browser-json"></pre></details>
      </article>
    </section>

    <section class="overview-grid">
      <article class="panel">
        <h2>Semantic sidecar</h2>
        <p class="muted">This separate evaluator record cannot overwrite browser facts or historical first-pass accounting.</p>
        <pre id="semantic-json"></pre>
      </article>
      <article class="panel">
        <h2>Historical outcome</h2>
        <pre id="historical-json"></pre>
        <div id="revalidation-block"></div>
      </article>
    </section>

    <section class="panel page-panel" id="final-result">
      <div class="section-heading">
        <div><h2>Final runnable page</h2><p class="muted">The exact packaged page used by the objective browser audit.</p></div>
        <a id="open-page" class="button" target="_blank" rel="noopener">Open page</a>
      </div>
      <iframe id="final-page" title="Selected Req2Web result page"></iframe>
    </section>
  </main>
  </div>

  <footer>
    <div><strong>Req2Web Phase 6 Inspector</strong><p>Bounded engineering replay only. No H1/gold, formal evaluation, broad generalization, training, LoRA, production, or user-study claim.</p></div>
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
body { margin: 0; color: var(--ink); background: var(--canvas); font-size: 15px; }
a { color: var(--accent); }
.site-header { position: sticky; top: 0; z-index: 30; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 2rem; min-height: 68px; padding: 0 clamp(1rem, 4vw, 3rem); border-bottom: 1px solid var(--line); background: rgba(255, 255, 255, .94); backdrop-filter: blur(18px); }
.brand { display: flex; align-items: center; gap: .7rem; color: var(--ink); text-decoration: none; }
.brand-mark { display: grid; width: 36px; height: 36px; place-items: center; border-radius: 10px; background: var(--ink); color: white; font-size: .72rem; font-weight: 800; letter-spacing: .04em; }
.brand > span:last-child { display: grid; line-height: 1.15; }
.brand small { color: var(--muted); font-size: .7rem; font-weight: 600; }
nav { display: flex; justify-content: center; gap: 1.55rem; }
nav a { color: #475467; text-decoration: none; font-size: .86rem; font-weight: 650; }
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
.human-section { margin-bottom: 4rem; }
.section-intro { display: flex; justify-content: space-between; gap: 3rem; align-items: end; margin-bottom: 1.25rem; }
.section-intro h2 { margin: .45rem 0 0; font-size: clamp(1.65rem, 3vw, 2.35rem); letter-spacing: -.035em; }
.section-intro > p { max-width: 590px; margin: 0; color: var(--muted); line-height: 1.6; }
.human-grid { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--line); border-radius: var(--radius); background: var(--card); overflow: hidden; }
.task-card { display: flex; min-width: 0; min-height: 310px; flex-direction: column; padding: 1.4rem; border-right: 1px solid var(--line); }
.task-card:last-child { border-right: 0; }
.task-meta { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.task-meta span { color: var(--accent); font-size: .8rem; font-weight: 850; }
.task-meta em { color: var(--muted); font-size: .72rem; font-style: normal; font-weight: 650; text-align: right; }
.task-card h3 { margin: 2rem 0 .55rem; font-size: 1.2rem; letter-spacing: -.02em; }
.task-card p { margin: 0; color: var(--muted); line-height: 1.62; }
.task-card small { display: block; margin-top: auto; padding-top: 1rem; border-top: 1px solid var(--line); color: #667085; line-height: 1.5; }
.task-links { display: flex; flex-wrap: wrap; gap: .55rem; margin: 1rem 0; }
.task-links a, .task-link { width: fit-content; margin: 1rem 0; padding: .46rem .65rem; border-radius: 8px; background: var(--accent-soft); color: var(--accent-dark); text-decoration: none; font-size: .76rem; font-weight: 750; }
.panel { min-width: 0; padding: 1.4rem; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card); box-shadow: var(--shadow); }
.panel h2 { margin: .35rem 0 .7rem; font-size: 1.3rem; letter-spacing: -.025em; }
.section-heading { display: flex; justify-content: space-between; gap: 2rem; align-items: flex-start; }
.section-heading > div:first-child { max-width: 840px; }
.muted { color: var(--muted); line-height: 1.55; }
.prose { line-height: 1.68; }
.prose p { margin: .35rem 0; }
.retrieval-panel { margin: 0 0 3.2rem; padding: 1.6rem; }
.retrieval-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .7rem; margin: 1.25rem 0; }
.retrieval-card { min-width: 0; padding: .9rem; border: 1px solid var(--line); border-radius: 10px; background: #fcfcfd; color: #475467; line-height: 1.52; font-size: .82rem; }
.retrieval-card strong { display: block; margin-bottom: .25rem; color: var(--ink); font-size: .96rem; }
.control-row { display: flex; align-items: end; justify-content: space-between; gap: 2rem; margin: 1.7rem 0 .9rem; padding-top: 1.3rem; border-top: 1px solid var(--line); }
.control-row label { display: grid; gap: .45rem; width: min(650px, 100%); color: #344054; font-size: .8rem; font-weight: 750; }
.control-row p { max-width: 440px; margin: 0; color: var(--muted); font-size: .78rem; line-height: 1.5; }
select { width: 100%; min-width: 280px; padding: .68rem .8rem; border: 1px solid var(--line-strong); border-radius: 9px; background: white; color: var(--ink); font: inherit; }
select:focus, a:focus-visible, summary:focus-visible { outline: 3px solid #bfdbfe; outline-offset: 2px; }
.retrieval-candidates { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .75rem; }
.retrieval-candidates .retrieval-card { background: white; }
.candidate-list { margin: .65rem 0 0; padding-left: 1.25rem; }
.candidate-list li { margin: .65rem 0; color: var(--ink); line-height: 1.35; }
.candidate-list small { display: block; margin-top: .16rem; color: var(--muted); overflow-wrap: anywhere; }
.details-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .75rem; margin-top: 1rem; }
.details-grid details { padding: .8rem .9rem; border: 1px solid var(--line); border-radius: 10px; }
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
#browser-screenshot { display: block; width: min(100%, 430px); max-height: 520px; object-fit: contain; margin: 1rem auto; border: 1px solid var(--line); border-radius: 10px; background: white; }
.failure { max-width: 470px; padding: .75rem .85rem; border: 1px solid #fedf89; border-radius: 10px; background: var(--warning-soft); color: #7a2e0e; font-size: .8rem; line-height: 1.48; }
.success { padding: .58rem .75rem; border: 1px solid #a6f4c5; border-radius: 10px; background: var(--success-soft); color: var(--success); font-size: .8rem; font-weight: 750; }
.button { display: inline-block; padding: .66rem .9rem; border: 1px solid var(--accent); border-radius: 9px; background: var(--accent); color: white; text-decoration: none; font-size: .84rem; font-weight: 750; }
.button:hover { background: var(--accent-dark); }
.button.secondary { border-color: var(--line-strong); background: white; color: #344054; }
.interaction-list { margin: .8rem 0; padding-left: 1.5rem; color: var(--muted); font-size: .82rem; line-height: 1.5; }
.interaction-list small { display: block; overflow-wrap: anywhere; }
.page-panel { margin-top: 1rem; }
iframe { width: 100%; height: 760px; margin-top: 1rem; border: 1px solid var(--line); border-radius: 10px; background: white; }
footer { display: flex; justify-content: space-between; gap: 2rem; align-items: center; padding: 2rem max(1rem, calc((100vw - 1480px) / 2)); border-top: 1px solid var(--line); background: white; color: var(--muted); font-size: .8rem; }
footer strong { color: var(--ink); }
footer p { margin: .25rem 0 0; }
footer a { white-space: nowrap; text-decoration: none; font-weight: 750; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@media (max-width: 1180px) {
  nav { display: none; }
  .site-header { grid-template-columns: 1fr auto; }
  .hero { grid-template-columns: 1fr 300px; }
  .metrics { grid-template-columns: repeat(3, 1fr); }
  .metric { border-bottom: 1px solid var(--line); }
  .node-grid { grid-template-columns: repeat(2, 1fr); }
  .retrieval-candidates { grid-template-columns: 1fr; }
}
@media (max-width: 820px) {
  .app-shell { width: min(100% - 1rem, 1480px); }
  .hero { grid-template-columns: 1fr; min-height: 0; padding: 3.5rem .2rem; }
  .release-card { max-width: 520px; }
  .section-intro, .section-heading, .control-row { flex-direction: column; align-items: stretch; }
  .human-grid { grid-template-columns: 1fr; }
  .task-card { min-height: 0; border-right: 0; border-bottom: 1px solid var(--line); }
  .task-card:last-child { border-bottom: 0; }
  .task-card small { margin-top: 1rem; }
  .metrics { grid-template-columns: 1fr 1fr; }
  .metric { border-right: 1px solid var(--line); }
  .separation-note { grid-template-columns: 1fr; }
  .retrieval-summary, .details-grid, .overview-grid, .node-grid, .evidence-cards { grid-template-columns: 1fr; }
  .case-toolbar { top: 74px; grid-template-columns: 1fr; }
  .badges { display: none; }
  iframe { height: 680px; }
}
@media (max-width: 520px) {
  .site-header { padding: 0 .75rem; }
  .local-status { display: none; }
  .hero h1 { font-size: 2.45rem; }
  .hero-actions { flex-direction: column; }
  .button { text-align: center; }
  .metrics { grid-template-columns: 1fr; }
  .metric { min-height: 0; border-right: 0; }
  .panel, .retrieval-panel { padding: 1rem; }
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

function renderRetrievalCandidates(report, unitIndex) {
  const unit = report.units[unitIndex];
  byId('retrieval-candidates').innerHTML = report.methods.map(method => {
    const rows = unit.candidates[method].map(item =>
      `<li><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.summary)}</small><small>${escapeHtml(item.doc_id)} · score ${item.score}</small></li>`
    ).join('');
    return `<article class="retrieval-card"><strong>${method.toUpperCase()}</strong><ol class="candidate-list">${rows}</ol></article>`;
  }).join('');
}

function renderRetrievalComparison(report, experiment) {
  const pairwise = report.pairwise_summary;
  const ranking = report.ranking_evaluation;
  byId('retrieval-gate').innerHTML = ranking.status === 'not_computed_no_independent_qrels'
    ? '<div class="failure"><strong>No relevance winner.</strong> The model prelabel template and two-human assignment are prepared; no labels have been collected.</div>'
    : '<div class="success">Exploratory assisted-review metrics available</div>';
  const utilityCards = report.method_runs.map(run =>
    `<div class="retrieval-card"><strong>${run.backend.toUpperCase()}</strong>${run.utility.guidance_count} guidance items · ${run.utility.adopted} adopted · ${run.utility.role_has_influence_count}/60 role units with influence · consistency ${run.utility.all_consistency_passed ? 'pass' : 'fail'}</div>`
  );
  pairwise.comparisons.forEach(comparison => {
    utilityCards.push(`<div class="retrieval-card"><strong>${comparison.methods.map(method => method.toUpperCase()).join(' vs ')}</strong>${comparison.top1_changed_count}/60 Top-1 changes · mean overlap ${comparison.mean_overlap_count_at_k}/5 · mean Jaccard ${comparison.mean_jaccard_at_k}</div>`);
  });
  experiment.efficiency.methods.forEach(method => {
    utilityCards.push(`<div class="retrieval-card"><strong>${method.backend.toUpperCase()} efficiency</strong>${method.load_plus_first_suite_elapsed_ms} ms load + first suite · ${method.warm_suite_median_ms} ms warm median · ${(method.python_traced_peak_bytes_load_plus_first_suite / 1048576).toFixed(2)} MiB Python traced peak<br><small>Descriptive only; not part of the winner rule.</small></div>`);
  });
  byId('retrieval-summary').innerHTML = utilityCards.join('');
  const select = byId('retrieval-unit-select');
  select.innerHTML = report.units.map((unit, index) =>
    `<option value="${index}">${escapeHtml(unit.case_id)} · ${escapeHtml(unit.role)}</option>`
  ).join('');
  select.addEventListener('change', () => renderRetrievalCandidates(report, Number(select.value)));
  byId('retrieval-json').textContent = pretty({
    status: report.status,
    ranking_evaluation: report.ranking_evaluation,
    metric_policy: report.metric_policy,
    claim_boundary: report.claim_boundary,
  });
  byId('retrieval-experiment-json').textContent = pretty(experiment);
  renderRetrievalCandidates(report, 0);
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
  byId('final-page').src = a.final_page;
  byId('open-page').href = a.final_page;
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
  const [retrieval, retrievalExperiment] = await Promise.all([
    getJson(catalog.retrieval_comparison),
    getJson(catalog.retrieval_experiment),
  ]);
  renderMetrics(catalog.counts);
  renderRetrievalComparison(retrieval, retrievalExperiment);
  const select = byId('case-select');
  select.innerHTML = catalog.cases.map(item =>
    `<option value="${item.execution_index - 1}">${String(item.execution_index).padStart(2, '0')} · ${item.case_id} · ${item.condition_id}</option>`
  ).join('');
  select.addEventListener('change', () => showCase(catalog.cases[Number(select.value)]));
  await showCase(catalog.cases[0]);
}

main().catch(error => {
  document.body.innerHTML = `<main><section class="panel"><h1>Replay failed closed</h1><pre>${error.stack || error}</pre></section></main>`;
});
""".encode("utf-8")


_README_MD = """# Req2Web Phase 6 Reviewer Replay

This directory is a self-contained, read-only replay of twelve frozen Phase 5
engineering rows. It lets a reviewer inspect the requirement, evidence
projection, validated F1-F4 outputs, preserved failure location, final
PageSpec, objective Chrome evidence, semantic sidecar, screenshot, interaction
trace, runnable final page, and a deterministic BM25/RRF/TF-IDF retrieval
comparison with a blinded metric protocol and descriptive local efficiency
evidence.

No GPU, model, hidden material, network connection, or browser automation is
required. The replay does not regenerate or repair any evidence.

## Run

From the repository root:

```powershell
python scripts/run_phase6_inspector.py --validate-only
python scripts/run_phase6_inspector.py
```

Then open `http://127.0.0.1:8765/`.

With only this directory and Python available:

```powershell
python validate_bundle.py
python serve_bundle.py --port 8765
```

## Interpretation

- Historical downstream first-pass delivery remains 9/12.
- A zero-model policy replay makes twelve ResultPackages available but does
  not rewrite the historical count.
- Objective Chrome execution and PageSpec conformance passed 12/12 with 36
  interactions and zero console or page errors.
- The separate semantic sidecar accepted 12/12 results and marked 24/24
  criteria supported with zero retry.
- The 48 F1-F4 generation calls and 12 semantic calls remain separate ledgers.
- The retrieval panel compares BM25, RRF, and TF-IDF over sixty frozen
  case-role query units. One provider-neutral model-prelabel template and a
  deterministic two-human assignment are prepared. Without completed model and
  human labels it shows candidate, downstream, and efficiency diagnostics only,
  and declares no relevance-quality winner.
- The Human handoff section links directly to the prelabel template, the review
  assignment, detailed instructions, and the exact license and submission tasks
  that still require people.

This is bounded engineering evidence, not H1/gold, formal evaluation, broad
generalization, training, LoRA, production, or user-study evidence.

## License gate

The bundle is technically runnable, but public redistribution is not yet
authorized by a repository license. See `MATERIALS.json`. The project owner
must make an explicit license decision before publication.
""".encode("utf-8")


_HUMAN_TASKS_MD = """# Req2Web Phase 6 Human Tasks

Only human judgment, ownership decisions, and authored submission material
remain. Do not calculate metrics manually and do not edit generated evidence.

## 1. Retrieval relevance

1. Give `llm_prelabel_template.json` to an approved model operator. The operator
   may use a closed provider only after the project owner separately approves
   the provider, model, price cap, credential path, and exact action. No script
   in this bundle calls an API.
2. The model operator changes only the declared completion fields: packet
   `status`, model identity, raw-response hash, paid-API flag, and each
   candidate's 0-3 suggestion, short rationale, and confidence. All 420
   candidates must be completed without changing the method-blind input.
3. A maintainer prepares deterministic human packets from the completed model
   packet and the frozen assignment:

```powershell
python scripts/prepare_retrieval_human_review.py `
  --completed-llm-packet path/to/llm_prelabels_completed.json
```

4. Give the two human packets to two different people. Each person reviews 252
   items: 168 primary items with the model suggestion visible and the same 84
   blind-audit items with it hidden. They complete their own packets before
   seeing each other's blind ratings.
5. After both packets are frozen, the same two people jointly resolve only the
   84 blind-overlap items in `joint_resolution_template.json`. Do not add a third
   reviewer or relabel an insufficient pool to create a winner.
6. A maintainer runs the repository assembler. It rejects packet drift,
   calculates linearly weighted Cohen kappa over the blind overlap, preserves
   model and human judgment ledgers, and creates immutable exploratory qrels:

```powershell
python scripts/assemble_retrieval_qrels.py `
  --review-root outputs/phase6_retrieval_human_review_v1 `
  --reviewer-1-packet path/to/human_reviewer_1_completed.json `
  --reviewer-2-packet path/to/human_reviewer_2_completed.json `
  --joint-resolution-packet path/to/joint_resolution_completed.json `
  --reviewer-1-id reviewer-a --reviewer-2-id reviewer-b `
  --output outputs/completed_retrieval_qrels_v3.json
```

Done means the model packet, both human packets, and the joint packet validate;
all raw judgments are retained; weighted kappa is reported; and the result is
labeled `LLM-assisted, split-human-reviewed exploratory qrels`, never
independent human gold or H1.

## 2. License and material approval

The project owner selects the software and content/data licenses, confirms the
redistribution status of project-authored cases, outputs, screenshots, pages,
and semantic records, and records the decision owner, date, notice files, and
exact release archive SHA-256. `MATERIALS.json` is the review inventory.

## 3. Submission material

When the authors are ready, they record the 3-5 minute demonstration, write the
four-page paper, select screenshots, add the artifact link, and retain the
published limitations. These materials must not expand the bounded engineering
claim into H1/gold, formal quality, broad generalization, training, production,
or user-study claims.
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
        _write_json(temp_root / "retrieval_comparison.json", retrieval_comparison)
        _write_json(temp_root / "retrieval_experiment.json", retrieval_experiment)
        _copy_regular_file(
            experiment_root / "annotation" / "llm_prelabel_template.json",
            temp_root
            / "human_tasks"
            / "retrieval"
            / "llm_prelabel_template.json",
        )
        _copy_regular_file(
            experiment_root / "annotation" / "human_review_assignment.json",
            temp_root
            / "human_tasks"
            / "retrieval"
            / "human_review_assignment.json",
        )
        _write_json(temp_root / "catalog.json", catalog)
        _write_json(temp_root / "MATERIALS.json", _material_inventory())
        _write_bytes(temp_root / "README.md", _README_MD)
        _write_bytes(temp_root / "human_tasks" / "README.md", _HUMAN_TASKS_MD)
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
