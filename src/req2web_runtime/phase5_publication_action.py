"""Execute the bounded project-authored Phase 5 publication experiment.

The parent owns case, condition, budget, and result accounting. Every model row
still enters through the canonical raw-requirement upstream and delegates F1-F4
to the shared Phase 4 LangGraph executor and prompt authority.
"""

from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping

from req2web_agent import PROMPT_AUTHORITY_IDENTITY
from req2web_evaluation.phase5_publication_case_drafts import (
    build_phase5_publication_candidate_matrix_from_json_bytes,
    build_phase5_publication_case_drafts_from_json_bytes,
)
from req2web_evaluation.phase5_publication_case_templates import (
    build_phase5_publication_case_templates_from_json_bytes,
    validate_phase5_publication_intervention_freeze,
)
from req2web_orchestration.phase4_graph import NODE_ORDER, REAL_MODEL_GRAPH_REVISION
from req2web_rag import DOCUMENT_SCHEMA_VERSION
from req2web_rag.index import build_tfidf_index, write_index
from req2web_runtime import phase4_remote_qwen_fresh_integrated as _fresh
from req2web_runtime.phase5_5090_execution_profile import (
    MODEL_INVENTORY_SHA256,
    MODEL_ROOT_SHA256,
)
from req2web_runtime.phase5_sealed_action_package import RTX5090_PROFILE_SHA256
from req2web_runtime.phase4_canonical_full_flow import (
    _aggregate,
    _build_upstream,
    _summarize_case,
)
from req2web_runtime.phase4_remote_qwen_langgraph_integrated import (
    ACTIVE_RUNNER_REVISION,
    run_phase4_remote_qwen_langgraph_integrated,
)


SCHEMA_VERSION = "req2web.phase5.publication_action.v1"
ACTION_PACKAGE_SCHEMA_VERSION = f"{SCHEMA_VERSION}.package"
OWNER_ACTION_RECEIPT_SCHEMA_VERSION = f"{SCHEMA_VERSION}.owner_action_receipt"
RUN_SUMMARY_SCHEMA_VERSION = f"{SCHEMA_VERSION}.summary"
ROW_SUMMARY_SCHEMA_VERSION = f"{SCHEMA_VERSION}.row_summary"
PROJECTION_RECEIPT_SCHEMA_VERSION = f"{SCHEMA_VERSION}.projection_receipt"
BASELINE_CORPUS_SCHEMA_VERSION = f"{SCHEMA_VERSION}.baseline_corpus"
ROOT_MARKER = ".req2web-phase5-publication-action-root"
EXPERIMENT_ID = "phase5-publication-project-authored-path2-v1"
TOTAL_GENERATE_CALL_CAP = 48
IRRELEVANT_EVIDENCE_ROLE = "implementation"
NODE_EVIDENCE_ROLES = copy.deepcopy(_fresh.P4_05_PROVIDER_EVIDENCE_NODE_ROLES)
FIXTURE_NAMES = {
    "templates": "phase5_publication_case_templates_v1.json",
    "intervention": "phase5_publication_intervention_freeze_v1.json",
    "drafts": "phase5_publication_case_drafts_v1.json",
    "matrix": "phase5_publication_candidate_matrix_v1.json",
}
_POSIX_ABSOLUTE = re.compile(r"^/(?:[^/\x00]+/)*[^/\x00]*$")


class Phase5PublicationActionError(ValueError):
    """Raised when the publication action cannot continue safely."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5PublicationActionError(f"invalid JSON artifact: {path.name}") from exc


def _repository_head(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    head = completed.stdout.strip().lower()
    if completed.returncode != 0 or len(head) != 40:
        raise Phase5PublicationActionError("repository HEAD is unavailable")
    return head


def phase5_publication_fixture_blob_ids(
    repository_root: Path,
    source_action_commit: str,
) -> dict[str, str]:
    """Bind each fixture to its exact commit blob and reject worktree drift."""

    _validate_commit(source_action_commit, "fixture source commit")
    result: dict[str, str] = {}
    for name, filename in FIXTURE_NAMES.items():
        relative = f"fixtures/{filename}"
        committed = subprocess.run(
            ["git", "rev-parse", f"{source_action_commit}:{relative}"],
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        worktree = subprocess.run(
            ["git", "hash-object", f"--path={relative}", relative],
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        committed_blob = committed.stdout.strip().lower()
        worktree_blob = worktree.stdout.strip().lower()
        if (
            committed.returncode != 0
            or worktree.returncode != 0
            or len(committed_blob) != 40
            or worktree_blob != committed_blob
        ):
            raise Phase5PublicationActionError(
                f"tracked publication fixture drifted from source commit: {relative}"
            )
        result[name] = committed_blob
    return result


def _committed_fixture_bytes(
    repository_root: Path,
    source_action_commit: str,
    name: str,
) -> bytes:
    relative = f"fixtures/{FIXTURE_NAMES[name]}"
    completed = subprocess.run(
        ["git", "show", f"{source_action_commit}:{relative}"],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise Phase5PublicationActionError(
            f"committed publication fixture is unavailable: {relative}"
        )
    return completed.stdout


def _validate_commit(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise Phase5PublicationActionError(f"{name} must be a lowercase commit")
    return value


def _validate_digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise Phase5PublicationActionError(f"{name} must be a lowercase SHA-256")
    return value


def _validate_posix_path(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not _POSIX_ABSOLUTE.fullmatch(value)
        or "\\" in value
        or "/../" in f"{value}/"
        or value.endswith("/..")
    ):
        raise Phase5PublicationActionError(f"{name} must be an absolute POSIX path")
    return value


def load_phase5_publication_fixtures(
    repository_root: Path,
    source_action_commit: str | None = None,
) -> dict[str, dict[str, object]]:
    """Load and cross-validate the four tracked publication fixtures."""

    selected_commit = source_action_commit or _repository_head(repository_root)
    phase5_publication_fixture_blob_ids(repository_root, selected_commit)
    templates = build_phase5_publication_case_templates_from_json_bytes(
        _committed_fixture_bytes(repository_root, selected_commit, "templates")
    )
    intervention = validate_phase5_publication_intervention_freeze(
        json.loads(
            _committed_fixture_bytes(
                repository_root,
                selected_commit,
                "intervention",
            ).decode("utf-8")
        ),
        templates,
    )
    drafts = build_phase5_publication_case_drafts_from_json_bytes(
        _committed_fixture_bytes(repository_root, selected_commit, "drafts"),
        templates,
        intervention,
    )
    matrix = build_phase5_publication_candidate_matrix_from_json_bytes(
        _committed_fixture_bytes(repository_root, selected_commit, "matrix"),
        drafts,
        templates,
        intervention,
    )
    return {
        "templates": templates,
        "intervention": intervention,
        "drafts": drafts,
        "matrix": matrix,
    }


def _opaque_doc_id(*parts: str) -> str:
    return f"doc-{sha256(':'.join(parts).encode('utf-8')).hexdigest()[:24]}"


def _case_input(case: Mapping[str, object], case_index: int) -> dict[str, object]:
    return {
        "case_id": case["case_ref"],
        "request_id": f"phase5-publication-request-{case_index:02d}",
        "requirement": case["requirement_text"],
        "target_device": case["target_device"],
        "task_type": case["task_type"],
        "constraints": copy.deepcopy(case["constraints"]),
    }


def build_phase5_publication_baseline_documents(
    case: Mapping[str, object],
    template: Mapping[str, object],
) -> list[dict[str, object]]:
    """Build one deterministic five-role project-authored corpus for a case."""

    outlines = template.get("evidence_role_outlines")
    if not isinstance(outlines, Mapping) or tuple(outlines) != (
        "requirement",
        "ui_reference",
        "interaction_flow",
        "implementation",
        "validation",
    ):
        raise Phase5PublicationActionError("baseline evidence role order drifted")
    requirement = str(case["requirement_text"])
    case_ref = str(case["case_ref"])
    documents: list[dict[str, object]] = []
    for role, outline_value in outlines.items():
        outline = str(outline_value)
        doc_id = _opaque_doc_id(case_ref, role, outline)
        documents.append(
            {
                "schema_version": DOCUMENT_SCHEMA_VERSION,
                "doc_id": doc_id,
                "role": role,
                "dataset": "req2web_phase5_project_authored_publication",
                "subset": "four_core_cases_v1",
                "sample_id": case_ref,
                "title": f"Project-authored {role.replace('_', ' ')} evidence",
                "summary": outline,
                "content": f"{requirement}\n\nRole-specific evidence: {outline}",
                "tags": ["project-authored", "phase5-publication", role],
                "references": [],
                "source": {
                    "kind": "project_authored",
                    "license": "project_owned",
                },
                "metadata": {
                    "language": "en",
                    "third_party_content": False,
                    "h1_or_gold": False,
                },
            }
        )
    build_tfidf_index(documents)
    return documents


def write_phase5_publication_baseline_index(
    *,
    index_root: Path,
    documents: list[dict[str, object]],
) -> dict[str, object]:
    """Write a deterministic corpus/index and return its byte identities."""

    if index_root.exists():
        raise Phase5PublicationActionError("baseline index root already exists")
    index_root.mkdir(parents=True, exist_ok=False)
    document_bytes = b"".join(_canonical(item) + b"\n" for item in documents)
    _fresh._write_fsync(index_root / "documents.jsonl", document_bytes)
    write_index(index_root / "tfidf_index.json.gz", build_tfidf_index(documents))
    index_bytes = (index_root / "tfidf_index.json.gz").read_bytes()
    body = {
        "schema_version": BASELINE_CORPUS_SCHEMA_VERSION,
        "document_count": len(documents),
        "role_order": [str(item["role"]) for item in documents],
        "documents_sha256": _digest(document_bytes),
        "documents_byte_length": len(document_bytes),
        "index_sha256": _digest(index_bytes),
        "index_byte_length": len(index_bytes),
        "project_authored": True,
        "third_party_content": False,
        "h1_or_gold": False,
    }
    receipt = {
        **body,
        "corpus_identity": _fresh._identity(
            body,
            revision=BASELINE_CORPUS_SCHEMA_VERSION,
        ),
    }
    _fresh._write_fsync(
        index_root / "baseline_corpus_receipt.json",
        _canonical(receipt),
    )
    return receipt


def build_phase5_provider_evidence_projection(
    *,
    context_value: Mapping[str, object],
    irrelevant_evidence: str,
    condition_id: str,
    critical_role_id: str,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    """Apply one frozen condition only to the model-visible typed evidence view."""

    if condition_id not in {"none", "irrelevant_evidence", "remove_critical_role"}:
        raise Phase5PublicationActionError("publication condition is invalid")
    retrieval_results = context_value.get("retrieval_results")
    if not isinstance(retrieval_results, Mapping):
        raise Phase5PublicationActionError("AgentContext retrieval results are missing")
    baseline_by_role: dict[str, dict[str, str]] = {}
    for role in ("ui_reference", "interaction_flow", "implementation", "validation"):
        rows = retrieval_results.get(role)
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
            raise Phase5PublicationActionError(
                f"baseline retrieval must produce exactly one {role} item"
            )
        row = rows[0]
        baseline_by_role[role] = {
            "role": role,
            "opaque_doc_id": str(row["doc_id"]),
            "project_authored_nonverbatim_short_summary": str(row["summary"]),
        }
    irrelevant_item = {
        "role": IRRELEVANT_EVIDENCE_ROLE,
        "opaque_doc_id": _opaque_doc_id("fixed-irrelevant", irrelevant_evidence),
        "project_authored_nonverbatim_short_summary": irrelevant_evidence,
    }
    role_rank = {
        role: index
        for index, role in enumerate(_fresh.P4_05_PROVIDER_EVIDENCE_ROLE_ORDER)
    }
    views: dict[str, dict[str, object]] = {}
    for node_id in NODE_ORDER:
        items = [
            copy.deepcopy(baseline_by_role[role])
            for role in NODE_EVIDENCE_ROLES[node_id]
            if not (
                condition_id == "remove_critical_role"
                and role == critical_role_id
            )
        ]
        if (
            condition_id == "irrelevant_evidence"
            and IRRELEVANT_EVIDENCE_ROLE in NODE_EVIDENCE_ROLES[node_id]
        ):
            items.append(copy.deepcopy(irrelevant_item))
        items.sort(key=lambda item: (role_rank[str(item["role"])], str(item["opaque_doc_id"])))
        views[node_id] = _fresh._validate_provider_evidence_view(
            {
                "schema_version": _fresh.P4_05_PROVIDER_EVIDENCE_VIEW_SCHEMA_VERSION,
                "items": items,
            },
            node_id=node_id,
        )
    baseline_identity = _fresh._identity(
        baseline_by_role,
        revision=BASELINE_CORPUS_SCHEMA_VERSION,
    )
    projection_identity = _fresh._identity(
        views,
        revision=_fresh.P4_05_PROVIDER_EVIDENCE_VIEW_SCHEMA_VERSION,
    )
    receipt = {
        "schema_version": PROJECTION_RECEIPT_SCHEMA_VERSION,
        "condition_id": condition_id,
        "critical_role_id": critical_role_id,
        "irrelevant_evidence_sha256": _digest(irrelevant_evidence.encode("utf-8")),
        "irrelevant_evidence_role": IRRELEVANT_EVIDENCE_ROLE,
        "full_local_baseline_identity": baseline_identity,
        "provider_visible_projection_identity": projection_identity,
        "full_local_agent_context_unchanged": True,
        "condition_label_provider_visible": False,
        "automatic_retry": False,
    }
    return views, receipt


def create_phase5_publication_owner_action_receipt(
    *,
    source_action_commit: str,
    run_id: str,
    fixtures: Mapping[str, Mapping[str, object]],
    fixture_blob_ids: Mapping[str, str],
    owner_confirmation_sha256: str,
    instance_id: str,
    gpu_uuid: str,
    ssh_fingerprint_sha256: str,
    repository_root: str,
    model_root: str,
    integrity_evidence: str,
    python_executable: str,
    result_root: str,
    hourly_rate_minor_units: int,
    time_cap_seconds: int,
    cost_cap_minor_units: int,
    storage_cap_bytes: int,
) -> dict[str, object]:
    """Create a typed external receipt from an explicit owner authorization."""

    _validate_commit(source_action_commit, "owner receipt source commit")
    _validate_digest(owner_confirmation_sha256, "owner confirmation")
    _validate_digest(ssh_fingerprint_sha256, "SSH fingerprint")
    for name, value in (
        ("repository root", repository_root),
        ("model root", model_root),
        ("integrity evidence", integrity_evidence),
        ("Python executable", python_executable),
        ("result root", result_root),
    ):
        _validate_posix_path(value, name)
    if set(fixture_blob_ids) != set(FIXTURE_NAMES):
        raise Phase5PublicationActionError("owner receipt fixture blob keys drifted")
    for name, blob_id in fixture_blob_ids.items():
        if (
            not isinstance(blob_id, str)
            or len(blob_id) != 40
            or any(character not in "0123456789abcdef" for character in blob_id)
        ):
            raise Phase5PublicationActionError(
                f"owner receipt fixture blob is invalid: {name}"
            )
    if (
        not isinstance(run_id, str)
        or not run_id
        or not isinstance(instance_id, str)
        or not instance_id
        or not isinstance(gpu_uuid, str)
        or not gpu_uuid.startswith("GPU-")
    ):
        raise Phase5PublicationActionError("owner receipt run or instance identity is invalid")
    for name, value, minimum in (
        ("hourly rate", hourly_rate_minor_units, 0),
        ("time cap", time_cap_seconds, 1),
        ("cost cap", cost_cap_minor_units, 1),
        ("storage cap", storage_cap_bytes, 1),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise Phase5PublicationActionError(f"owner receipt {name} is invalid")
    body = {
        "schema_version": OWNER_ACTION_RECEIPT_SCHEMA_VERSION,
        "status": "owner_approved_ready_for_exact_publication_action",
        "owner_confirmation_sha256": owner_confirmation_sha256,
        "source_action_commit": source_action_commit,
        "run_id": run_id,
        "authority_bindings": {
            "candidate_matrix_id": fixtures["matrix"]["matrix_id"],
            "case_draft_id": fixtures["drafts"]["draft_id"],
            "template_fixture_id": fixtures["templates"]["fixture_id"],
            "intervention_freeze_manifest_id": fixtures["intervention"]["manifest_id"],
            "execution_order_sha256": fixtures["matrix"]["execution_order_sha256"],
            "fixture_blob_ids": copy.deepcopy(dict(fixture_blob_ids)),
            "rtx5090_profile_sha256": RTX5090_PROFILE_SHA256,
            "model_inventory_sha256": MODEL_INVENTORY_SHA256,
            "model_root_sha256": MODEL_ROOT_SHA256,
            "prompt_authority_identity": PROMPT_AUTHORITY_IDENTITY,
            "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
            "active_runner_revision": ACTIVE_RUNNER_REVISION,
        },
        "instance": {
            "instance_id": instance_id,
            "gpu_uuid": gpu_uuid,
            "gpu_class": "nvidia_geforce_rtx_5090",
            "device_index": 0,
            "ssh_fingerprint_sha256": ssh_fingerprint_sha256,
        },
        "paths": {
            "repository_root": repository_root,
            "model_root": model_root,
            "integrity_evidence": integrity_evidence,
            "python_executable": python_executable,
            "result_root": result_root,
        },
        "limits": {
            "hourly_rate_minor_units": hourly_rate_minor_units,
            "time_cap_seconds": time_cap_seconds,
            "cost_cap_minor_units": cost_cap_minor_units,
            "storage_cap_bytes": storage_cap_bytes,
            "runtime_row_count": 12,
            "node_generate_call_cap": TOTAL_GENERATE_CALL_CAP,
            "automatic_retry": False,
        },
        "authorization": {
            "owner_approved_exact_run": True,
            "project_authored_path2_allowed": True,
            "model_action_allowed": True,
            "gpu_remote_paid_action_allowed": True,
            "h1_open_allowed": False,
            "gold_access_allowed": False,
            "formal_evaluation_allowed": False,
            "formal_quality_claim_allowed": False,
            "training_allowed": False,
            "lora_allowed": False,
            "result_driven_change_allowed": False,
        },
        "action_state": {
            "receipt_created": True,
            "model_loaded": False,
            "experiment_executed": False,
            "formal_quality_claimed": False,
        },
    }
    return {
        "receipt_id": f"phase5-publication-owner-action-{_digest(_canonical(body))}",
        **body,
    }


def validate_phase5_publication_owner_action_receipt(
    value: object,
    *,
    fixtures: Mapping[str, Mapping[str, object]],
    fixture_blob_ids: Mapping[str, str],
) -> dict[str, object]:
    """Validate an external owner receipt before any runtime resource probe."""

    if not isinstance(value, Mapping):
        raise Phase5PublicationActionError("owner action receipt is not an object")
    receipt = copy.deepcopy(dict(value))
    expected_keys = {
        "receipt_id",
        "schema_version",
        "status",
        "owner_confirmation_sha256",
        "source_action_commit",
        "run_id",
        "authority_bindings",
        "instance",
        "paths",
        "limits",
        "authorization",
        "action_state",
    }
    if set(receipt) != expected_keys:
        raise Phase5PublicationActionError("owner action receipt keys drifted")
    recreated = create_phase5_publication_owner_action_receipt(
        source_action_commit=str(receipt["source_action_commit"]),
        run_id=str(receipt["run_id"]),
        fixtures=fixtures,
        fixture_blob_ids=fixture_blob_ids,
        owner_confirmation_sha256=str(receipt["owner_confirmation_sha256"]),
        instance_id=str(receipt["instance"]["instance_id"]),
        gpu_uuid=str(receipt["instance"]["gpu_uuid"]),
        ssh_fingerprint_sha256=str(receipt["instance"]["ssh_fingerprint_sha256"]),
        repository_root=str(receipt["paths"]["repository_root"]),
        model_root=str(receipt["paths"]["model_root"]),
        integrity_evidence=str(receipt["paths"]["integrity_evidence"]),
        python_executable=str(receipt["paths"]["python_executable"]),
        result_root=str(receipt["paths"]["result_root"]),
        hourly_rate_minor_units=int(receipt["limits"]["hourly_rate_minor_units"]),
        time_cap_seconds=int(receipt["limits"]["time_cap_seconds"]),
        cost_cap_minor_units=int(receipt["limits"]["cost_cap_minor_units"]),
        storage_cap_bytes=int(receipt["limits"]["storage_cap_bytes"]),
    )
    if _canonical(receipt) != _canonical(recreated):
        raise Phase5PublicationActionError("owner action receipt content drifted")
    return receipt


def _action_package(
    *,
    source_action_commit: str,
    run_id: str,
    fixtures: Mapping[str, Mapping[str, object]],
    profile_identity: Mapping[str, object],
    model_inventory_identity: Mapping[str, object],
    instance_id: str,
    gpu_uuid: str,
    ssh_fingerprint_sha256: str,
    repository_root: Path,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    time_cap_seconds: int,
    cost_cap_minor_units: int,
    storage_cap_bytes: int,
    owner_action_receipt_sha256: str,
) -> dict[str, object]:
    if len(source_action_commit) != 40 or len(ssh_fingerprint_sha256) != 64:
        raise Phase5PublicationActionError("action identity is invalid")
    body = {
        "schema_version": ACTION_PACKAGE_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "run_id": run_id,
        "source_action_commit": source_action_commit,
        "candidate_matrix_id": fixtures["matrix"]["matrix_id"],
        "case_draft_id": fixtures["drafts"]["draft_id"],
        "template_fixture_id": fixtures["templates"]["fixture_id"],
        "intervention_freeze_manifest_id": fixtures["intervention"]["manifest_id"],
        "execution_order_sha256": fixtures["matrix"]["execution_order_sha256"],
        "runtime_row_count": 12,
        "node_generate_call_cap": TOTAL_GENERATE_CALL_CAP,
        "node_order": list(NODE_ORDER),
        "prompt_authority_identity": PROMPT_AUTHORITY_IDENTITY,
        "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
        "active_runner_revision": ACTIVE_RUNNER_REVISION,
        "profile_identity": copy.deepcopy(dict(profile_identity)),
        "model_inventory_identity": copy.deepcopy(dict(model_inventory_identity)),
        "instance_id": instance_id,
        "gpu_uuid": gpu_uuid,
        "ssh_fingerprint_sha256": ssh_fingerprint_sha256,
        "repository_root": str(repository_root),
        "model_root": str(model_root),
        "integrity_evidence": str(integrity_evidence),
        "result_root": str(result_root),
        "time_cap_seconds": time_cap_seconds,
        "cost_cap_minor_units": cost_cap_minor_units,
        "storage_cap_bytes": storage_cap_bytes,
        "owner_action_receipt_sha256": owner_action_receipt_sha256,
        "owner_action_receipt_validated": True,
        "project_authored_path2_only": True,
        "raw_first": True,
        "one_call_per_node": True,
        "automatic_retry": False,
        "h1_opened": False,
        "gold_content_present": False,
        "formal_evaluation": False,
        "formal_quality_claimed": False,
        "training": False,
        "lora": False,
    }
    return {**body, "package_sha256": _digest(_canonical(body))}


def _parent_binding(
    *,
    run_id: str,
    policy_identity: Mapping[str, object],
    case_index: int,
    case: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": _fresh.P4_05_PARENT_BINDING_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "experiment_run_id": run_id,
        "experiment_policy_identity": copy.deepcopy(dict(policy_identity)),
        "case_index": case_index,
        "case_id": case["case_id"],
        "request_id": case["request_id"],
    }


def run_phase5_publication_action(
    *,
    repository_root: Path,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    owner_action_receipt: Path,
    source_action_commit: str,
    run_id: str,
    instance_id: str,
    gpu_uuid: str,
    ssh_fingerprint_sha256: str,
    time_cap_seconds: int,
    cost_cap_minor_units: int,
    storage_cap_bytes: int,
    python_executable: str,
    confirm_publication_action: bool,
    console: object | None = None,
) -> dict[str, object]:
    """Run all 12 frozen rows without opening H1 or changing shared semantics."""

    if confirm_publication_action is not True:
        raise Phase5PublicationActionError("explicit publication action confirmation is required")
    repository_root = repository_root.resolve(strict=True)
    model_root = model_root.resolve(strict=True)
    integrity_evidence = integrity_evidence.resolve(strict=True)
    result_root = result_root.resolve(strict=False)
    owner_action_receipt = owner_action_receipt.resolve(strict=True)
    if _repository_head(repository_root) != source_action_commit:
        raise Phase5PublicationActionError("source action commit does not match repository HEAD")
    if min(time_cap_seconds, storage_cap_bytes) < 1 or cost_cap_minor_units < 0:
        raise Phase5PublicationActionError("publication resource caps are invalid")

    fixture_blob_ids = phase5_publication_fixture_blob_ids(
        repository_root,
        source_action_commit,
    )
    fixtures = load_phase5_publication_fixtures(
        repository_root,
        source_action_commit,
    )
    matrix = fixtures["matrix"]
    owner_receipt_raw = owner_action_receipt.read_bytes()
    owner_receipt_value = _read_json(owner_action_receipt)
    if _canonical(owner_receipt_value) != owner_receipt_raw:
        raise Phase5PublicationActionError("owner action receipt must be canonical JSON")
    owner_receipt = validate_phase5_publication_owner_action_receipt(
        owner_receipt_value,
        fixtures=fixtures,
        fixture_blob_ids=fixture_blob_ids,
    )
    expected_owner_bindings = {
        "source_action_commit": source_action_commit,
        "run_id": run_id,
        "instance_id": instance_id,
        "gpu_uuid": gpu_uuid,
        "ssh_fingerprint_sha256": ssh_fingerprint_sha256,
        "repository_root": str(repository_root),
        "model_root": str(model_root),
        "integrity_evidence": str(integrity_evidence),
        "python_executable": python_executable,
        "result_root": str(result_root),
        "time_cap_seconds": time_cap_seconds,
        "cost_cap_minor_units": cost_cap_minor_units,
        "storage_cap_bytes": storage_cap_bytes,
    }
    actual_owner_bindings = {
        "source_action_commit": owner_receipt["source_action_commit"],
        "run_id": owner_receipt["run_id"],
        "instance_id": owner_receipt["instance"]["instance_id"],
        "gpu_uuid": owner_receipt["instance"]["gpu_uuid"],
        "ssh_fingerprint_sha256": owner_receipt["instance"]["ssh_fingerprint_sha256"],
        "repository_root": owner_receipt["paths"]["repository_root"],
        "model_root": owner_receipt["paths"]["model_root"],
        "integrity_evidence": owner_receipt["paths"]["integrity_evidence"],
        "python_executable": owner_receipt["paths"]["python_executable"],
        "result_root": owner_receipt["paths"]["result_root"],
        "time_cap_seconds": owner_receipt["limits"]["time_cap_seconds"],
        "cost_cap_minor_units": owner_receipt["limits"]["cost_cap_minor_units"],
        "storage_cap_bytes": owner_receipt["limits"]["storage_cap_bytes"],
    }
    if actual_owner_bindings != expected_owner_bindings:
        raise Phase5PublicationActionError("owner action receipt does not bind this exact run")
    if result_root.exists():
        raise Phase5PublicationActionError("publication result root must be new")
    result_root.mkdir(parents=True, exist_ok=False)
    _fresh._write_fsync(result_root / ROOT_MARKER, ROOT_MARKER.encode("ascii"))
    _fresh._write_fsync(
        result_root / "owner_action_receipt.json",
        owner_receipt_raw,
    )
    for name, fixture in fixtures.items():
        _fresh._write_fsync(result_root / "frozen" / f"{name}.json", _canonical(fixture))

    inventory = _fresh._remote.validate_remote_model_inventory(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    runtime_facts = _fresh._remote._collect_remote_runtime_facts()
    gpu_facts = _fresh._remote._probe_remote_gpu_facts()
    profile = _fresh.RemoteFreshIntegratedProfile.create(
        inventory=inventory,
        runtime_facts=runtime_facts,
        gpu_facts=gpu_facts,
    )
    if profile.device_uuid != gpu_uuid:
        raise Phase5PublicationActionError("live GPU UUID does not match the action package")
    profile_identity = _fresh.make_stable_profile_binding_identity(profile)
    inventory_identity = copy.deepcopy(dict(inventory["inventory_identity"]))
    package = _action_package(
        source_action_commit=source_action_commit,
        run_id=run_id,
        fixtures=fixtures,
        profile_identity=profile_identity,
        model_inventory_identity=inventory_identity,
        instance_id=instance_id,
        gpu_uuid=gpu_uuid,
        ssh_fingerprint_sha256=ssh_fingerprint_sha256,
        repository_root=repository_root,
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        result_root=result_root,
        time_cap_seconds=time_cap_seconds,
        cost_cap_minor_units=cost_cap_minor_units,
        storage_cap_bytes=storage_cap_bytes,
        owner_action_receipt_sha256=_digest(owner_receipt_raw),
    )
    _fresh._write_fsync(result_root / "action_package.json", _canonical(package))
    policy_identity = _fresh._identity(package, revision=ACTION_PACKAGE_SCHEMA_VERSION)

    template_by_id = {
        str(item["template_id"]): item for item in fixtures["templates"]["templates"]
    }
    case_by_ref = {
        str(item["case_ref"]): item for item in fixtures["drafts"]["cases"]
    }
    case_inputs: dict[str, dict[str, object]] = {}
    upstream_by_case: dict[str, dict[str, object]] = {}
    template_for_case: dict[str, Mapping[str, object]] = {}
    for case_index, case_value in enumerate(fixtures["drafts"]["cases"], start=1):
        case_ref = str(case_value["case_ref"])
        case = _case_input(case_value, case_index)
        template = template_by_id[str(case_value["template_id"])]
        documents = build_phase5_publication_baseline_documents(case_value, template)
        index_root = result_root / "baseline" / f"{case_index:02d}" / "index"
        corpus_receipt = write_phase5_publication_baseline_index(
            index_root=index_root,
            documents=documents,
        )
        upstream_root = result_root / "baseline" / f"{case_index:02d}" / "upstream"
        upstream_root.mkdir(parents=True, exist_ok=False)
        upstream = _build_upstream(
            case=case,
            index_dir=index_root,
            output_root=upstream_root,
        )
        _fresh._write_fsync(
            upstream_root / "baseline_binding.json",
            _canonical(
                {
                    "case_ref": case_ref,
                    "corpus_identity": corpus_receipt["corpus_identity"],
                    "agent_context_identity": upstream["receipt"]["agent_context_identity"],
                    "canonical_b_identity": upstream["receipt"]["canonical_b_identity"],
                    "shared_across_all_three_conditions": True,
                }
            ),
        )
        case_inputs[case_ref] = case
        upstream_by_case[case_ref] = upstream
        template_for_case[case_ref] = template

    rows_by_id = {str(item["row_id"]): item for item in matrix["rows"]}
    case_index_by_ref = {
        str(item["case_ref"]): index
        for index, item in enumerate(fixtures["drafts"]["cases"], start=1)
    }
    completed: list[dict[str, object]] = []
    infrastructure_failure: dict[str, object] | None = None
    for execution_index, row_id in enumerate(matrix["execution_order"], start=1):
        row = rows_by_id[str(row_id)]
        case_ref = str(row["case_ref"])
        case = case_inputs[case_ref]
        upstream = upstream_by_case[case_ref]
        template = template_for_case[case_ref]
        condition_id = str(row["condition_id"])
        projection, projection_receipt = build_phase5_provider_evidence_projection(
            context_value=upstream["context"].to_dict(),
            irrelevant_evidence=str(template["irrelevant_evidence_outline"]),
            condition_id=condition_id,
            critical_role_id=str(case_by_ref[case_ref]["intervention_binding"]["critical_role_id"]),
        )
        row_root = result_root / "rows" / f"{execution_index:02d}-{row_id}"
        _fresh._write_fsync(
            result_root / "projection-receipts" / f"{execution_index:02d}.json",
            _canonical({"row_id": row_id, **projection_receipt}),
        )
        if console is not None:
            print(
                f"[PHASE5-PUBLICATION] row {execution_index:02d}/12 started",
                file=console,
                flush=True,
            )
        try:
            final_result = run_phase4_remote_qwen_langgraph_integrated(
                model_root=model_root,
                integrity_evidence=integrity_evidence,
                result_root=row_root,
                b_input=upstream["adaptation"].b_input,
                upstream_context=upstream["context"],
                upstream_guidance=upstream["guidance"],
                upstream_binding=upstream["receipt"],
                parent_experiment_binding=_parent_binding(
                    run_id=run_id,
                    policy_identity=policy_identity,
                    case_index=case_index_by_ref[case_ref],
                    case=case,
                ),
                expected_profile_identity=profile_identity,
                expected_model_inventory_identity=inventory_identity,
                confirm_one_remote_langgraph_run=True,
                run_id=f"{run_id}-row-{execution_index:02d}",
                console=console,
                provider_evidence_projection_by_node=projection,
            )
            shared_summary = _summarize_case(
                index=case_index_by_ref[case_ref],
                case=case,
                upstream_receipt=upstream["receipt"],
                child_root=row_root,
                final_result=final_result,
            )
            row_body = {
                "schema_version": ROW_SUMMARY_SCHEMA_VERSION,
                "execution_index": execution_index,
                "row_id": row_id,
                "case_ref": case_ref,
                "condition_id": condition_id,
                "provider_visible_projection_identity": projection_receipt[
                    "provider_visible_projection_identity"
                ],
                "shared_case_upstream_identity": upstream["receipt"]["receipt_identity"],
                "shared_flow_summary": shared_summary,
            }
            summary = {
                **row_body,
                "row_summary_identity": _fresh._identity(
                    row_body,
                    revision=ROW_SUMMARY_SCHEMA_VERSION,
                ),
            }
            _fresh._write_fsync(
                result_root / "row-summaries" / f"{execution_index:02d}.json",
                _canonical(summary),
            )
            completed.append(summary)
        except Exception as exc:
            infrastructure_failure = {
                "execution_index": execution_index,
                "row_id": row_id,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "automatic_retry": False,
                "row_regeneration_allowed": False,
            }
            break

    shared_summaries = [item["shared_flow_summary"] for item in completed]
    aggregate = _aggregate(shared_summaries)
    if aggregate["total_generate_started_count"] > TOTAL_GENERATE_CALL_CAP:
        raise Phase5PublicationActionError("aggregate generate-call cap was exceeded")
    terminal_status = (
        "completed_descriptive_results"
        if len(completed) == 12 and infrastructure_failure is None
        else "incomplete_experiment"
    )
    summary_body = {
        "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
        "run_id": run_id,
        "package_sha256": package["package_sha256"],
        "status": terminal_status,
        "source_action_commit": source_action_commit,
        "candidate_matrix_id": matrix["matrix_id"],
        "execution_order_sha256": matrix["execution_order_sha256"],
        "planned_runtime_row_count": 12,
        "completed_runtime_row_count": len(completed),
        "node_generate_call_cap": TOTAL_GENERATE_CALL_CAP,
        "aggregate": aggregate,
        "rows": completed,
        "infrastructure_failure": infrastructure_failure,
        "raw_first": True,
        "automatic_retry": False,
        "numeric_gain_thresholds_used": False,
        "aggregate_pass_score_computed": False,
        "reporting_scope": "descriptive_case_level_only",
        "h1_opened": False,
        "gold_content_present": False,
        "formal_evaluation": False,
        "formal_quality_claimed": False,
        "training": False,
        "lora": False,
    }
    summary = {
        **summary_body,
        "summary_identity": _fresh._identity(
            summary_body,
            revision=RUN_SUMMARY_SCHEMA_VERSION,
        ),
    }
    _fresh._write_fsync(result_root / "run_summary.json", _canonical(summary))
    return summary


__all__ = [
    "ACTION_PACKAGE_SCHEMA_VERSION",
    "EXPERIMENT_ID",
    "IRRELEVANT_EVIDENCE_ROLE",
    "NODE_EVIDENCE_ROLES",
    "OWNER_ACTION_RECEIPT_SCHEMA_VERSION",
    "Phase5PublicationActionError",
    "RUN_SUMMARY_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "TOTAL_GENERATE_CALL_CAP",
    "build_phase5_provider_evidence_projection",
    "build_phase5_publication_baseline_documents",
    "create_phase5_publication_owner_action_receipt",
    "load_phase5_publication_fixtures",
    "phase5_publication_fixture_blob_ids",
    "run_phase5_publication_action",
    "validate_phase5_publication_owner_action_receipt",
    "write_phase5_publication_baseline_index",
]
