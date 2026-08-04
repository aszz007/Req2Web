"""Fresh integrated Phase 4 local-Qwen pilot foundation.

The module adds a narrow policy wrapper around the existing P4-03 runner.  It
does not load a model.  A completed integrated savepoint is accepted only as
node-contract qualification evidence; the fresh run starts with an empty
authority state and derives every actual input from the same-run outputs.

The real worker seam is deliberately explicit.  The existing supervised
worker now exposes ``generate_fresh_integrated(..., emit_delta=...)`` and
implements the native-context, memory-efficient-attention, and
complete-single-JSON contract recorded here.  The scripted fixture path
remains separate and explicit for focused tests.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    phase4_create_authority_state,
    phase4_normalize_and_validate_f4_output,
    phase4_register_node_output,
    phase4_synthetic_fixture_output,
    synthetic_commerce_b_input,
)
from req2web_runtime import phase4_local_qwen as _local
from req2web_runtime import phase4_local_qwen_f4 as _f4
from req2web_runtime.phase4_local_qwen import (
    LocalQwenProfile,
    NodeProjectionPolicy,
    PilotExecutionLease,
    PilotBinding,
    PilotOutcome,
    PilotSupervisorReceipt,
    PreCallManifest,
    Phase4LocalQwenContractError,
    Phase4LocalQwenPilotRunner,
    acquire_pilot_execution_lease,
    persist_pilot_outcome,
    persist_supervisor_receipt,
    persist_worker_stderr_artifact,
    start_supervised_local_qwen_fresh_integrated_runtime,
)
from req2web_runtime.phase4_local_qwen_integrated import (
    INTEGRATED_ASSEMBLY_REPORT_NAME,
    INTEGRATED_CANDIDATE_NAME,
    INTEGRATED_F3_STATE_NAME,
    INTEGRATED_F4_RAW_NAME,
    INTEGRATED_F4_STATE_NAME,
    INTEGRATED_RESULT_NAME,
    INTEGRATED_FOUNDATION_ROOT_MARKER,
    INTEGRATED_SOURCE_BINDING_NAME,
    INTEGRATED_FOUNDATION_BINDING_SCHEMA_VERSION,
    INTEGRATED_FOUNDATION_SCHEMA_PREFIX,
    INTEGRATED_FOUNDATION_SCHEMA_VERSION,
    INTEGRATED_PAGE_SPEC_NAME,
    _identity,
    _read_regular,
    _strict_json,
)


FRESH_INTEGRATED_SCHEMA_PREFIX = (
    "req2web.phase4.local_qwen.fresh_integrated_pilot"
)
FRESH_INTEGRATED_POLICY_SCHEMA_VERSION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.policy.v1"
)
FRESH_INTEGRATED_QUALIFICATION_SCHEMA_VERSION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.qualification.v1"
)
FRESH_INTEGRATED_RUN_BINDING_SCHEMA_VERSION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.run_binding.v1"
)
FRESH_INTEGRATED_RESULT_SCHEMA_VERSION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.result.v1"
)
FRESH_INTEGRATED_EVENT_SCHEMA_VERSION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.event.v1"
)
FRESH_INTEGRATED_PILOT_ID = "p4-03i-local-qwen-fresh-integrated-v1"
FRESH_INTEGRATED_RUN_PREFIX = (
    "p4-03i-local-qwen-fresh-integrated-run-"
)
FRESH_INTEGRATED_ROOT_MARKER = (
    ".req2web-phase4-local-qwen-fresh-integrated-root"
)
FRESH_INTEGRATED_POLICY_NAME = "fresh_integrated_policy.json"
FRESH_INTEGRATED_QUALIFICATION_NAME = (
    "fresh_integrated_qualification_receipt.json"
)
FRESH_INTEGRATED_RUN_BINDING_NAME = "fresh_integrated_run_binding.json"
FRESH_INTEGRATED_MANIFEST_NAME = "pre_call_manifest.json"
FRESH_INTEGRATED_PREPARE_RESULT_NAME = (
    "fresh_integrated_prepare_result.json"
)
FRESH_INTEGRATED_RESULT_NAME = "fresh_integrated_result.json"
FRESH_INTEGRATED_EVENT_STREAM_NAME = "fresh_integrated_event_stream.json"
FRESH_INTEGRATED_PREFLIGHT_NAME = "fresh_integrated_preflight.json"
FRESH_INTEGRATED_NORMALIZED_OUTPUT_NAME = "normalized_node_output.json"
FRESH_INTEGRATED_NORMALIZATION_RECEIPT_NAME = (
    "node_normalization_receipt.json"
)
FRESH_INTEGRATED_PROJECTION_REVISION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.node_input.v1"
)
# NodeD17 owns the shared P4-03 prompt envelope.  The fresh pilot is
# versioned by its policy/run identities; it must not invent a new prompt
# revision without an owning-contract amendment.
FRESH_INTEGRATED_PROMPT_REVISION = "p4-03-prompt-v1"
FRESH_INTEGRATED_CONFIG_REVISION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.config.v1"
)
FRESH_INTEGRATED_EMPTY_AUTHORITY_REVISION = (
    f"{_local.P4_03_SCHEMA_PREFIX}.fresh_integrated.empty_authority.v1"
)
FRESH_INTEGRATED_MODEL_CONTEXT_REVISION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.native_model_context.v1"
)
FRESH_INTEGRATED_MODEL_ROOT_REVISION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.fixture_model_root.v1"
)
FRESH_INTEGRATED_MODEL_INVENTORY_REVISION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.fixture_model_inventory.v1"
)
FRESH_INTEGRATED_ARTIFACT_REVISION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.qualification_artifact.v1"
)
FRESH_INTEGRATED_RAW_REVISION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.qualification_raw.v1"
)
FRESH_INTEGRATED_PREDECESSOR_SCHEMA_VERSION = (
    f"{FRESH_INTEGRATED_SCHEMA_PREFIX}.predecessor.v1"
)
FRESH_INTEGRATED_PREDECESSOR_NAME = (
    "fresh_integrated_predecessor_receipt.json"
)

_canonical_bytes = _local._canonical_bytes
_write_once = _local._write_once


class Phase4LocalQwenFreshIntegratedError(Phase4LocalQwenContractError):
    """Raised when the fresh integrated boundary cannot be proven."""


class FreshIntegratedPreWorkerFailure(Phase4LocalQwenFreshIntegratedError):
    """A fresh-run failure raised before the worker accepted generate."""

    worker_generate_started = False


class FreshIntegratedBackend(Protocol):
    """Future supervised-worker seam for one fresh integrated generate."""

    def generate_fresh_integrated(
        self,
        *,
        node_id: str,
        input_bytes: bytes,
        prompt_bytes: bytes,
        config_bytes: bytes,
        request_bytes: bytes,
        emit_delta: Callable[[bytes], None],
    ) -> bytes:
        ...


def _safe_existing_root(path: Path, name: str) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or not path.is_dir()
        or path.is_symlink()
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            f"{name} must be an existing absolute non-symlink directory"
        )
    return path


def _prepare_result_root(path: Path) -> tuple[Path, str]:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.exists()
        or not path.parent.is_dir()
        or path.parent.is_symlink()
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated result root must be new and safe"
        )
    path.mkdir()
    marker = f"p4-03i-result-root-{uuid.uuid4().hex[:20]}"
    _write_once(
        path,
        FRESH_INTEGRATED_ROOT_MARKER,
        marker.encode("ascii"),
    )
    _write_once(
        path,
        _local.RESULT_ROOT_MARKER_NAME,
        marker.encode("ascii"),
    )
    return path, marker


def _read_json(root: Path, name: str) -> tuple[bytes, dict[str, object]]:
    try:
        raw = _read_regular(root, name)
        value = _strict_json(raw, require_canonical=True)
    except Exception as exc:
        raise Phase4LocalQwenFreshIntegratedError(
            f"required canonical JSON is unavailable: {name}"
        ) from exc
    if not isinstance(value, dict):
        raise Phase4LocalQwenFreshIntegratedError(
            f"required JSON root is not an object: {name}"
        )
    return raw, value


def _artifact_identity(
    root: Path,
    name: str,
    *,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    raw = _read_regular(root, name)
    return _identity(
        raw if identity_kind == "raw_bytes" else _strict_json(
            raw,
            require_canonical=True,
        ),
        revision=FRESH_INTEGRATED_ARTIFACT_REVISION,
        identity_kind=identity_kind,
    )


def _validate_predecessor_receipt(
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated predecessor receipt must be an object"
        )
    receipt = copy.deepcopy(dict(value))
    if receipt.get("schema_version") != FRESH_INTEGRATED_PREDECESSOR_SCHEMA_VERSION:
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated predecessor schema drifted"
        )
    expected_id = _identity(
        {
            key: item
            for key, item in receipt.items()
            if key != "predecessor_id"
        },
        revision=FRESH_INTEGRATED_PREDECESSOR_SCHEMA_VERSION,
    )["sha256"]
    if receipt.get("predecessor_id") != expected_id:
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated predecessor identity drifted"
        )
    if (
        receipt.get("predecessor_kind") != "pre_worker_failure"
        or receipt.get("worker_generate_started") is not False
        or receipt.get("effective_model_generate_calls") != 0
        or receipt.get("aggregate_budget_reset") is not False
        or receipt.get("source_result_immutable") is not True
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated predecessor is not a pre-worker failure"
        )
    for key in (
        "attempt_result_identity",
        "supervisor_receipt_identity",
        "ledger_identity",
        "pilot_outcome_identity",
    ):
        if not isinstance(receipt.get(key), Mapping):
            raise Phase4LocalQwenFreshIntegratedError(
                f"fresh integrated predecessor identity is missing: {key}"
            )
    return receipt


def _predecessor_link(
    predecessor: Mapping[str, object] | None,
) -> dict[str, object]:
    if predecessor is None:
        return {
            "mode": "none",
            "receipt_id": None,
            "effective_model_generate_calls": 0,
            "aggregate_budget_reset": False,
        }
    receipt = _validate_predecessor_receipt(predecessor)
    return {
        "mode": "pre_worker_failure",
        "receipt_id": receipt["predecessor_id"],
        "source_pilot_id": receipt["source_pilot_id"],
        "source_run_id": receipt["source_run_id"],
        "source_node_id": receipt["source_node_id"],
        "failure_code": receipt["failure_code"],
        "worker_generate_started": False,
        "effective_model_generate_calls": 0,
        "aggregate_budget_reset": False,
    }


def _validate_predecessor_link(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated predecessor link must be an object"
        )
    link = copy.deepcopy(dict(value))
    mode = link.get("mode")
    if mode == "none":
        if set(link) != {
            "mode",
            "receipt_id",
            "effective_model_generate_calls",
            "aggregate_budget_reset",
        }:
            raise Phase4LocalQwenFreshIntegratedError(
                "empty predecessor link keys drifted"
            )
        if (
            link.get("receipt_id") is not None
            or link.get("effective_model_generate_calls") != 0
            or link.get("aggregate_budget_reset") is not False
        ):
            raise Phase4LocalQwenFreshIntegratedError(
                "empty predecessor link is not zero-budget"
            )
        return link
    if mode != "pre_worker_failure":
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated predecessor link mode is invalid"
        )
    if set(link) != {
        "mode",
        "receipt_id",
        "source_pilot_id",
        "source_run_id",
        "source_node_id",
        "failure_code",
        "worker_generate_started",
        "effective_model_generate_calls",
        "aggregate_budget_reset",
    }:
        raise Phase4LocalQwenFreshIntegratedError(
            "pre-worker predecessor link keys drifted"
        )
    if (
        not isinstance(link.get("receipt_id"), str)
        or not link["receipt_id"].startswith("sha256:")
        or link.get("worker_generate_started") is not False
        or link.get("effective_model_generate_calls") != 0
        or link.get("aggregate_budget_reset") is not False
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated predecessor link is not zero-budget"
        )
    for key in (
        "source_pilot_id",
        "source_run_id",
        "source_node_id",
        "failure_code",
    ):
        if not isinstance(link.get(key), str) or not link[key]:
            raise Phase4LocalQwenFreshIntegratedError(
                f"fresh integrated predecessor link is missing {key}"
            )
    return link


def _validate_predecessor_result_root(
    result_root: Path,
) -> dict[str, object]:
    """Read, never modify, one immutable pre-worker failure result root."""

    root = _safe_existing_root(result_root, "predecessor result root")
    attempt_paths = tuple(
        path
        for path in root.glob("runs/*/F1/attempt-01/attempt_result.json")
        if path.is_file() and not path.is_symlink()
    )
    if len(attempt_paths) != 1:
        raise Phase4LocalQwenFreshIntegratedError(
            "predecessor result root must contain exactly one F1 attempt"
        )
    attempt_path = attempt_paths[0]
    attempt_raw = _read_regular(attempt_path.parent, attempt_path.name)
    attempt = _strict_json(attempt_raw, require_canonical=True)
    if not isinstance(attempt, Mapping):
        raise Phase4LocalQwenFreshIntegratedError(
            "predecessor attempt result is not an object"
        )
    supervisor_raw, supervisor = _read_json(root, "supervisor_receipt.json")
    ledger_raw, ledger = _read_json(root, "ledger.json")
    outcome_raw, outcome = _read_json(root, "pilot_outcome.json")
    if (
        attempt.get("call_kind") != "integrated"
        or attempt.get("node_id") != "F1"
        or attempt.get("failure_code") != "backend_exception"
        or attempt.get("generate_started") is not True
        or attempt.get("raw_status") != "not_captured"
        or supervisor.get("generation_started") is not False
        or supervisor.get("worker_exit_verified") is not True
        or supervisor.get("raw_status") != "not_captured"
        or outcome.get("status") != "integrated_failed_closed"
        or ledger.get("integrated_run_count") != 1
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            "predecessor result root is not the recorded pre-worker failure"
        )
    node_counts = ledger.get("node_total_counts")
    if not isinstance(node_counts, Mapping) or node_counts.get("F1", 0) < 1:
        raise Phase4LocalQwenFreshIntegratedError(
            "predecessor ledger does not contain the historical F1 entry"
        )
    receipt: dict[str, object] = {
        "schema_version": FRESH_INTEGRATED_PREDECESSOR_SCHEMA_VERSION,
        "predecessor_id": "pending",
        "predecessor_kind": "pre_worker_failure",
        "source_result_root": str(root),
        "source_pilot_id": attempt.get("pilot_id"),
        "source_run_id": attempt.get("run_id"),
        "source_node_id": attempt.get("node_id"),
        "failure_code": attempt.get("failure_code"),
        "parent_recorded_generate_started": attempt.get("generate_started"),
        "worker_generate_started": supervisor.get("generation_started"),
        "effective_model_generate_calls": 0,
        "historical_parent_model_generate_calls": outcome.get(
            "model_calls_performed"
        ),
        "aggregate_budget_reset": False,
        "source_result_immutable": True,
        "counter_semantics": (
            "worker_acceptance_boundary; parent pre-worker failure is linked "
            "and does not consume the new run budget"
        ),
        "attempt_result_identity": _identity(
            attempt,
            revision=_local.ATTEMPT_RESULT_SCHEMA_VERSION,
        ),
        "supervisor_receipt_identity": _identity(
            supervisor,
            revision=_local.SUPERVISOR_RECEIPT_SCHEMA_VERSION,
        ),
        "ledger_identity": _identity(
            ledger,
            revision=_local.LEDGER_SCHEMA_VERSION,
        ),
        "pilot_outcome_identity": _identity(
            outcome,
            revision=_local.OUTCOME_SCHEMA_VERSION,
        ),
        "attempt_relative_path": attempt_path.relative_to(root).as_posix(),
        "raw_status": attempt.get("raw_status"),
        "raw_sha256": attempt.get("raw_sha256"),
    }
    receipt["predecessor_id"] = _identity(
        {
            key: value
            for key, value in receipt.items()
            if key != "predecessor_id"
        },
        revision=FRESH_INTEGRATED_PREDECESSOR_SCHEMA_VERSION,
    )["sha256"]
    return _validate_predecessor_receipt(receipt)


def _require_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise Phase4LocalQwenFreshIntegratedError(message)


def _validate_integrated_savepoint(
    savepoint_root: Path,
) -> dict[str, object]:
    """Create a qualification receipt from the immutable self-contained root."""

    root = _safe_existing_root(savepoint_root, "qualification savepoint root")
    marker = _read_regular(root, INTEGRATED_FOUNDATION_ROOT_MARKER)
    if not marker:
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification savepoint root marker is empty"
        )
    result_raw, result = _read_json(root, INTEGRATED_RESULT_NAME)
    binding_raw, binding = _read_json(root, INTEGRATED_SOURCE_BINDING_NAME)
    _, f3_state = _read_json(root, INTEGRATED_F3_STATE_NAME)
    _, f4_state = _read_json(root, INTEGRATED_F4_STATE_NAME)
    _, candidate = _read_json(root, INTEGRATED_CANDIDATE_NAME)
    _, page_spec = _read_json(root, INTEGRATED_PAGE_SPEC_NAME)
    _, assembly_report = _read_json(root, INTEGRATED_ASSEMBLY_REPORT_NAME)
    raw_response = _read_regular(root, INTEGRATED_F4_RAW_NAME)

    if result.get("schema_version") != INTEGRATED_FOUNDATION_SCHEMA_VERSION:
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification result schema is not the accepted savepoint version"
        )
    expected_result_id = _identity(
        {key: value for key, value in result.items() if key != "result_id"},
        revision=INTEGRATED_FOUNDATION_SCHEMA_VERSION,
    )["sha256"]
    _require_equal(
        result.get("result_id"),
        expected_result_id,
        "qualification result identity drifted",
    )
    if (
        result.get("status") != "integrated_savepoint_foundation_ready"
        or result.get("model_action") is not False
        or result.get("model_generate_calls") != 0
        or result.get("checkpoint_reuse_is_not_raw_model_success") is not True
        or result.get("raw_model_contract_success") is not False
        or result.get("normalized_node_contract_success") is not True
        or result.get("composition_status")
        != "candidate_composition_validated_only"
        or result.get("assembler_status") != "assembled"
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification savepoint is outside the node-contract boundary"
        )

    if binding.get("schema_version") != INTEGRATED_FOUNDATION_BINDING_SCHEMA_VERSION:
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification source-binding schema drifted"
        )
    expected_binding_id = _identity(
        {key: value for key, value in binding.items() if key != "binding_id"},
        revision=INTEGRATED_FOUNDATION_BINDING_SCHEMA_VERSION,
    )["sha256"]
    _require_equal(
        binding.get("binding_id"),
        expected_binding_id,
        "qualification source-binding identity drifted",
    )
    source_raw_identity = binding.get("f4_source_raw_identity")
    if not isinstance(source_raw_identity, Mapping):
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification F4 raw identity is missing"
        )
    expected_raw_identity = _identity(
        raw_response,
        revision=FRESH_INTEGRATED_RAW_REVISION,
        identity_kind="raw_bytes",
    )
    if (
        source_raw_identity.get("sha256")
        != _identity(
            raw_response,
            revision=str(source_raw_identity.get("revision")),
            identity_kind="raw_bytes",
        ).get("sha256")
        or source_raw_identity.get("byte_length") != len(raw_response)
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification F4 raw bytes do not match the source binding"
        )
    if binding.get("f4_source_final_result_live_verified") is not True:
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification must come from the live-verified self-contained savepoint"
        )

    qualification: dict[str, object] = {
        "schema_version": FRESH_INTEGRATED_QUALIFICATION_SCHEMA_VERSION,
        "qualification_id": "pending",
        "qualification_status": "validated_node_contract_qualification_only",
        "savepoint_root": str(root.resolve(strict=True)),
        "savepoint_root_marker_identity": _identity(
            marker,
            revision=f"{INTEGRATED_FOUNDATION_SCHEMA_PREFIX}.root_marker.v1",
            identity_kind="raw_bytes",
        ),
        "integrated_foundation_result_identity": _identity(
            result,
            revision=INTEGRATED_FOUNDATION_SCHEMA_VERSION,
        ),
        "integrated_source_binding_identity": _identity(
            binding,
            revision=INTEGRATED_FOUNDATION_BINDING_SCHEMA_VERSION,
        ),
        "source_result_raw_identity": _identity(
            result_raw,
            revision=FRESH_INTEGRATED_RAW_REVISION,
            identity_kind="raw_bytes",
        ),
        "source_binding_raw_identity": _identity(
            binding_raw,
            revision=FRESH_INTEGRATED_RAW_REVISION,
            identity_kind="raw_bytes",
        ),
        "f3_authority_state_identity": _artifact_identity(
            root,
            INTEGRATED_F3_STATE_NAME,
        ),
        "f4_authority_state_identity": _artifact_identity(
            root,
            INTEGRATED_F4_STATE_NAME,
        ),
        "f4_source_raw_identity": copy.deepcopy(dict(source_raw_identity)),
        "f4_source_raw_artifact_identity": expected_raw_identity,
        "candidate_composition_identity": _artifact_identity(
            root,
            INTEGRATED_CANDIDATE_NAME,
        ),
        "assembled_page_spec_identity": _artifact_identity(
            root,
            INTEGRATED_PAGE_SPEC_NAME,
        ),
        "assembly_report_identity": _artifact_identity(
            root,
            INTEGRATED_ASSEMBLY_REPORT_NAME,
        ),
        "case_id": result.get("case_id"),
        "request_id": result.get("request_id"),
        "node_order": list(NODE_ORDER),
        "b_aux_disposition": "absent/not_requested",
        "model_generate_calls": 0,
        "raw_model_contract_success": False,
        "normalized_node_contract_success": True,
        "source_savepoint_is_not_integrated_input": True,
        "checkpoint_reuse_is_not_raw_model_success": True,
        "integrated_input_authority_state": "not_derived_from_savepoint",
        "downstream": {
            "consistency": "not_executed",
            "acceptance": "not_executed",
            "repair": "not_executed",
            "g0": "not_executed",
            "package": "not_executed",
            "production_route": "not_executed",
        },
        "claim_boundary": (
            "node_contract_qualification_only_not_current_pilot_model_success"
        ),
    }
    # Keep the raw identity from the accepted source binding byte-for-byte
    # authoritative while also recording the fresh qualification identity.
    if (
        qualification["f4_source_raw_identity"] != source_raw_identity
        or f3_state.get("case_id") != result.get("case_id")
        or f4_state.get("case_id") != result.get("case_id")
        or candidate.get("candidate_projection_status")
        != "candidate_composition_validated_only"
        or not isinstance(assembly_report, Mapping)
        or not isinstance(page_spec, Mapping)
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification artifact identities or statuses drifted"
        )
    qualification["qualification_id"] = _identity(
        {
            key: value
            for key, value in qualification.items()
            if key != "qualification_id"
        },
        revision=FRESH_INTEGRATED_QUALIFICATION_SCHEMA_VERSION,
    )["sha256"]
    return qualification


def validate_fresh_integrated_qualification(
    value: Mapping[str, object],
    *,
    savepoint_root: Path | None = None,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification receipt must be an object"
        )
    receipt = copy.deepcopy(dict(value))
    if receipt.get("schema_version") != FRESH_INTEGRATED_QUALIFICATION_SCHEMA_VERSION:
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification receipt schema drifted"
        )
    expected_id = _identity(
        {key: value for key, value in receipt.items() if key != "qualification_id"},
        revision=FRESH_INTEGRATED_QUALIFICATION_SCHEMA_VERSION,
    )["sha256"]
    if receipt.get("qualification_id") != expected_id:
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification receipt identity drifted"
        )
    if receipt.get("source_savepoint_is_not_integrated_input") is not True:
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification receipt can never authorize savepoint input reuse"
        )
    if receipt.get("model_generate_calls") != 0:
        raise Phase4LocalQwenFreshIntegratedError(
            "qualification receipt contains a model call"
        )
    if savepoint_root is not None:
        live = _validate_integrated_savepoint(savepoint_root)
        if live != receipt:
            raise Phase4LocalQwenFreshIntegratedError(
                "qualification receipt does not match the live savepoint"
            )
    return receipt


def create_fresh_integrated_policy(
    *,
    pilot_id: str = FRESH_INTEGRATED_PILOT_ID,
    run_id: str,
    predecessor_receipt: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if type(pilot_id) is not str or not pilot_id:
        raise Phase4LocalQwenFreshIntegratedError("fresh pilot id is invalid")
    if type(run_id) is not str or not run_id.startswith(FRESH_INTEGRATED_RUN_PREFIX):
        raise Phase4LocalQwenFreshIntegratedError("fresh run id is invalid")
    caps = {
        "input_bytes": 16 * 1024 * 1024,
        "output_bytes": 16 * 1024 * 1024,
        "prompt_bytes": 512 * 1024,
        "config_bytes": 128 * 1024,
        "request_bytes": 128 * 1024,
        "ref_count": 256,
    }
    policy: dict[str, object] = {
        "schema_version": FRESH_INTEGRATED_POLICY_SCHEMA_VERSION,
        "policy_id": "pending",
        "pilot_id": pilot_id,
        "run_id": run_id,
        "predecessor_link": _predecessor_link(predecessor_receipt),
        "node_order": list(NODE_ORDER),
        "node_local_generate_allowed": False,
        "integrated_run_cap": 1,
        "per_node_integrated_generate_cap": 1,
        "retry_count_cap": 0,
        "b_aux_disposition": "absent/not_requested",
        "field_caps": caps,
        "input_context": {
            "mode": "model_native_full_context",
            "native_context_identity_required": True,
            "artificial_input_token_cap": None,
            "input_truncation": False,
        },
        "output_contract": {
            "limit_kind": "context_remaining",
            "max_new_tokens_source": "native_context_minus_measured_input",
            "artificial_output_truncation": False,
            "incomplete_single_json_is_failure": True,
        },
        "worker_contract": {
            "worker_kind": "existing_persistent_supervised_worker",
            "parent_supervisor_required": True,
            "raw_first": True,
            "teardown_required": True,
            "memory_efficient_attention": {
                "implementation": _f4.F4_MEMORY_EFFICIENT_ATTENTION_NAME,
                "revision": _f4.F4_MEMORY_EFFICIENT_ATTENTION_REVISION,
                "math_fallback_allowed": False,
                "input_tokens_unchanged": True,
                "output_tokens_unchanged": True,
            },
            "stopping_criteria": {
                "class_name": _f4.F4CompleteSingleJSONStoppingCriteria.__name__,
                "revision": f"{_f4.F4_LOCAL_SCHEMA_PREFIX}.complete_single_json.v1",
                "one_top_level_json_object": True,
                "eos_allowed": True,
            },
            "token_streaming": True,
        },
        "f4_normalization": {
            "approved_failure": (
                "F4.acceptance_check.refs order or uniqueness is invalid"
            ),
            "raw_bytes_immutable": True,
            "normalized_artifact_immutable": True,
            "normalization_counts_as_retry": False,
            "raw_model_contract_success_separate": True,
            "normalized_node_contract_success_separate": True,
        },
        "downstream": {
            "assembler": "allowed",
            "consistency": "not_executed",
            "acceptance": "not_executed",
            "repair": "not_executed",
            "g0": "not_executed",
            "package": "not_executed",
            "production_route": "not_executed",
        },
        "action_state": {
            "model_action": False,
            "graph_runtime_execution": False,
            "dependency_installation": False,
            "training": False,
            "remote_action": False,
            "network": False,
            "telemetry": False,
            "tracing": False,
            "local_files_only": True,
        },
        "claim_boundary": (
            "fresh_integrated_pilot_foundation_not_model_quality_or_production"
        ),
    }
    policy["policy_id"] = _identity(
        {key: value for key, value in policy.items() if key != "policy_id"},
        revision=FRESH_INTEGRATED_POLICY_SCHEMA_VERSION,
    )["sha256"]
    return policy


def _build_policies() -> tuple[NodeProjectionPolicy, ...]:
    categories = {
        "F1": ["canonical_b_input", "case_identity"],
        "F2": ["canonical_b_input", "validated_F1_output", "deterministic_registry"],
        "F3": [
            "canonical_b_input",
            "validated_F1_output",
            "validated_F2_output",
            "deterministic_registry",
        ],
        "F4": [
            "canonical_b_input",
            "validated_F1_output",
            "validated_F2_output",
            "validated_F3_output",
            "deterministic_registry",
            "deterministic_mapping",
        ],
    }
    prohibited = [
        "b_aux_sidecar",
        "retrieval_evidence",
        "h1_gold",
        "browser_evidence",
        "hidden_reasoning",
    ]
    upstream = {"F1": [], "F2": ["F1"], "F3": ["F1", "F2"], "F4": ["F1", "F2", "F3"]}
    return tuple(
        NodeProjectionPolicy.create(
            node_id=node_id,
            allowed_categories=categories[node_id],
            prohibited_categories=prohibited,
            field_caps={
                "input_bytes": 16 * 1024 * 1024,
                "output_bytes": 16 * 1024 * 1024,
                "prompt_bytes": 512 * 1024,
                "config_bytes": 128 * 1024,
                "request_bytes": 128 * 1024,
                "ref_count": 256,
            },
            upstream_required_node_ids=upstream[node_id],
            projection_revision=FRESH_INTEGRATED_PROJECTION_REVISION,
            prompt_template_revision=FRESH_INTEGRATED_PROMPT_REVISION,
            config_revision=FRESH_INTEGRATED_CONFIG_REVISION,
        )
        for node_id in NODE_ORDER
    )


def _write_run_binding(
    *,
    policy: Mapping[str, object],
    qualification: Mapping[str, object],
    marker: str,
) -> dict[str, object]:
    binding: dict[str, object] = {
        "schema_version": FRESH_INTEGRATED_RUN_BINDING_SCHEMA_VERSION,
        "run_binding_id": "pending",
        "pilot_id": policy["pilot_id"],
        "run_id": policy["run_id"],
        "policy_id": policy["policy_id"],
        "qualification_id": qualification["qualification_id"],
        "predecessor_link": copy.deepcopy(policy["predecessor_link"]),
        "qualification_savepoint_identity": {
            "result": qualification["integrated_foundation_result_identity"],
            "source_binding": qualification["integrated_source_binding_identity"],
            "f4_raw": qualification["f4_source_raw_identity"],
        },
        "result_root_marker": marker,
        "source_savepoint_is_not_integrated_input": True,
        "fresh_empty_authority_state_required": True,
        "node_local_generate_allowed": False,
        "integrated_run_cap": 1,
        "per_node_integrated_generate_cap": 1,
        "retry_count_cap": 0,
        "b_aux_disposition": "absent/not_requested",
        "action_state": {
            "model_action": False,
            "graph_runtime_execution": False,
            "dependency_installation": False,
            "training": False,
            "remote_action": False,
            "network": False,
            "telemetry": False,
            "tracing": False,
            "local_files_only": True,
        },
    }
    binding["run_binding_id"] = _identity(
        {
            key: value
            for key, value in binding.items()
            if key != "run_binding_id"
        },
        revision=FRESH_INTEGRATED_RUN_BINDING_SCHEMA_VERSION,
    )["sha256"]
    return binding


def _fixture_runner_material(
    *,
    result_root: Path,
    marker: str,
    policy: Mapping[str, object],
    b_input: Mapping[str, object],
    native_context_tokens: int = 32768,
) -> tuple[PilotBinding, tuple[NodeProjectionPolicy, ...], LocalQwenProfile, PreCallManifest]:
    if type(native_context_tokens) is not int or native_context_tokens <= 8192:
        raise Phase4LocalQwenFreshIntegratedError(
            "fixture native context must be greater than the retired 8K limit"
        )
    policies = _build_policies()
    model_root_identity = _identity(
        {
            "model_id": _local.QWEN_MODEL_ID,
            "model_revision": _local.QWEN_MODEL_REVISION,
            "source": "scripted_fixture_placeholder",
            "pilot_id": policy["pilot_id"],
        },
        revision=FRESH_INTEGRATED_MODEL_ROOT_REVISION,
    )
    inventory_identity = _identity(
        {
            "source": "scripted_fixture_placeholder",
            "file_count": 1,
            "native_context_tokens": native_context_tokens,
        },
        revision=FRESH_INTEGRATED_MODEL_INVENTORY_REVISION,
    )
    profile = LocalQwenProfile.create(
        model_root_identity=model_root_identity,
        model_inventory_identity=inventory_identity,
        model_file_count=1,
        context_tokens=native_context_tokens,
        max_input_tokens=native_context_tokens,
        max_new_tokens=native_context_tokens,
        decode={
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 0,
            "output_limit_kind": "context_remaining",
            "complete_single_json_required": True,
        },
    )
    pilot = PilotBinding.create(
        pilot_id=str(policy["pilot_id"]),
        policy_id="pending",
        case_binding=_local.build_synthetic_case_binding(b_input),
        node_policy_identities={
            item.node_id: item.sha256() for item in policies
        },
        profile_id=profile.profile_id,
        result_root_marker=marker,
        decode_config={
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 0,
            "max_new_tokens": native_context_tokens,
            "output_limit_kind": "context_remaining",
        },
        integrated_run_cap=1,
    )
    manifest = PreCallManifest.create(
        pilot_binding=pilot,
        policies=policies,
        profile=profile,
        model_root_identity=model_root_identity,
        model_inventory_identity=inventory_identity,
        inventory_file_count=1,
        offline_environment={
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "LANGSMITH_TRACING": "0",
            "LANGCHAIN_TRACING_V2": "0",
        },
    )
    _write_once(result_root, FRESH_INTEGRATED_MANIFEST_NAME, manifest.canonical_bytes())
    return pilot, policies, profile, manifest


def _real_runner_material(
    *,
    result_root: Path,
    marker: str,
    policy: Mapping[str, object],
    b_input: Mapping[str, object],
    model_root: Path,
    integrity_evidence: Path,
) -> tuple[
    PilotBinding,
    tuple[NodeProjectionPolicy, ...],
    LocalQwenProfile,
    PreCallManifest,
    dict[str, object],
]:
    profile, native_context_identity = _f4._build_f4_profile(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    inventory_identity = dict(profile.model_inventory_identity)
    model_root_identity = _identity(
        {
            "model_id": _local.QWEN_MODEL_ID,
            "model_revision": _local.QWEN_MODEL_REVISION,
            "resolved_model_root": str(model_root.resolve(strict=True)),
            "inventory": inventory_identity,
        },
        revision=f"{_local.P4_03_SCHEMA_PREFIX}.model-root.v1",
    )
    policies = _build_policies()
    pilot = PilotBinding.create(
        pilot_id=str(policy["pilot_id"]),
        policy_id=str(policy["policy_id"]),
        case_binding=_local.build_synthetic_case_binding(b_input),
        node_policy_identities={
            item.node_id: item.sha256() for item in policies
        },
        profile_id=profile.profile_id,
        result_root_marker=marker,
        decode_config={
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": profile.seed,
            "max_new_tokens": profile.context_tokens,
            "output_limit_kind": "context_remaining",
            "complete_single_json_required": True,
        },
        integrated_run_cap=1,
    )
    manifest = PreCallManifest.create(
        pilot_binding=pilot,
        policies=policies,
        profile=profile,
        model_root_identity=model_root_identity,
        model_inventory_identity=inventory_identity,
        inventory_file_count=profile.model_file_count,
        offline_environment={
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "LANGSMITH_TRACING": "0",
            "LANGCHAIN_TRACING_V2": "0",
        },
    )
    preflight = {
        "schema_version": FRESH_INTEGRATED_MODEL_CONTEXT_REVISION,
        "preflight_status": "prepared_no_model",
        "pilot_id": pilot.pilot_id,
        "policy_id": policy["policy_id"],
        "profile_identity": _identity(
            profile.to_dict(),
            revision=_local.LOCAL_QWEN_PROFILE_SCHEMA_VERSION,
        ),
        "native_context_identity": dict(native_context_identity),
        "native_context_source": (
            "config.json:/text_config/max_position_embeddings"
        ),
        "model_root_identity": model_root_identity,
        "model_inventory_identity": inventory_identity,
        "manifest_identity": _identity(
            manifest.to_dict(),
            revision=_local.MANIFEST_SCHEMA_VERSION,
        ),
        "input_context": "model_native_full_context",
        "max_input_tokens": profile.context_tokens,
        "output_limit_kind": "context_remaining",
        "input_truncation": False,
        "output_truncation": False,
        "action_state": {
            "model_action": False,
            "graph_runtime_execution": False,
            "dependency_installation": False,
            "training": False,
            "remote_action": False,
            "network": False,
            "telemetry": False,
            "tracing": False,
            "local_files_only": True,
        },
    }
    _write_once(
        result_root,
        FRESH_INTEGRATED_MANIFEST_NAME,
        manifest.canonical_bytes(),
    )
    return pilot, policies, profile, manifest, preflight


def _fixture_outputs(
    b_input: Mapping[str, object],
    *,
    f4_order_drift: bool = False,
) -> list[bytes]:
    def raw_json(value: object) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    state = phase4_create_authority_state(b_input)
    outputs: list[bytes] = []
    for node_id in ("F1", "F2", "F3"):
        output = phase4_synthetic_fixture_output(node_id, state)
        outputs.append(raw_json(output))
        state = phase4_register_node_output(state, node_id, output)
    f4_output = phase4_synthetic_fixture_output("F4", state)
    if f4_order_drift:
        for check in f4_output["acceptance_checks"]:
            state_ref = check["state_ref"]
            check["refs"] = [
                {
                    "ref_type": "state",
                    "ref_id": state_ref["ref_id"],
                    "ref_revision": state_ref["ref_revision"],
                },
                *copy.deepcopy(check["use_case_refs"]),
            ]
    outputs.append(raw_json(f4_output))
    return outputs


class ScriptedStreamingBackend:
    """Test-only backend that emits deterministic byte deltas."""

    def __init__(
        self,
        outputs: Sequence[bytes],
        *,
        chunk_size: int = 19,
    ) -> None:
        if any(type(item) is not bytes for item in outputs):
            raise Phase4LocalQwenFreshIntegratedError(
                "scripted fresh outputs must be bytes"
            )
        if type(chunk_size) is not int or chunk_size <= 0:
            raise Phase4LocalQwenFreshIntegratedError(
                "scripted fresh chunk size is invalid"
            )
        self._outputs = list(outputs)
        self.calls: list[str] = []
        self._chunk_size = chunk_size

    def generate_stream(
        self,
        *,
        node_id: str,
        input_bytes: bytes,
        prompt_bytes: bytes,
        config_bytes: bytes,
        request_bytes: bytes,
        emit_delta: Callable[[bytes], None],
    ) -> bytes:
        del input_bytes, prompt_bytes, config_bytes, request_bytes
        if not self._outputs:
            raise Phase4LocalQwenFreshIntegratedError(
                "scripted fresh backend output budget exhausted"
            )
        self.calls.append(node_id)
        raw = self._outputs.pop(0)
        for offset in range(0, len(raw), self._chunk_size):
            emit_delta(raw[offset : offset + self._chunk_size])
        return raw

    def generate(self, **kwargs: object) -> bytes:
        return self.generate_stream(emit_delta=lambda _: None, **kwargs)  # type: ignore[arg-type]


class _FreshBackendAdapter:
    def __init__(
        self,
        backend: object,
        *,
        event_sink: Callable[[dict[str, object]], None],
        allow_scripted_fallback: bool,
    ) -> None:
        self._backend = backend
        self._event_sink = event_sink
        self._allow_scripted_fallback = allow_scripted_fallback
        self._event_sink_failures: list[str] = []
        object.__setattr__(
            self,
            "_fixture_capability",
            _local._FIXTURE_BACKEND_CAPABILITY,
        )
        if type(getattr(backend, "_real_runtime_capability", None)) is object:
            object.__setattr__(
                self,
                "_real_runtime_capability",
                getattr(backend, "_real_runtime_capability"),
            )
            object.__setattr__(self, "_real_backend", backend)

    @property
    def event_sink_failures(self) -> tuple[str, ...]:
        return tuple(self._event_sink_failures)

    def _emit_event(self, payload: Mapping[str, object]) -> None:
        try:
            self._event_sink(dict(payload))
        except Exception as exc:
            # Console/event observation is advisory.  It must not prevent the
            # supervised worker from receiving the already-authorized call.
            self._event_sink_failures.append(type(exc).__name__)

    @property
    def loaded_facts(self) -> dict[str, object]:
        value = getattr(self._backend, "loaded_facts", None)
        if not isinstance(value, Mapping):
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh real worker loaded facts are unavailable"
            )
        return copy.deepcopy(dict(value))

    @property
    def fresh_runtime_facts(self) -> dict[str, object] | None:
        value = getattr(self._backend, "fresh_runtime_facts", None)
        return None if value is None else copy.deepcopy(dict(value))

    def generate(
        self,
        *,
        node_id: str,
        input_bytes: bytes,
        prompt_bytes: bytes,
        config_bytes: bytes,
        request_bytes: bytes,
    ) -> bytes:
        generation_started_emitted = False

        def mark_generation_started() -> None:
            nonlocal generation_started_emitted
            if generation_started_emitted:
                return
            generation_started_emitted = True
            self._emit_event(
                {
                    "event": "generation_started",
                    "node_id": node_id,
                }
            )

        def emit_delta(delta: bytes) -> None:
            if type(delta) is not bytes:
                raise Phase4LocalQwenFreshIntegratedError(
                    "worker token delta must be bytes"
                )
            mark_generation_started()
            self._emit_event(
                {
                    "event": "token_delta",
                    "node_id": node_id,
                    "delta": delta.decode("utf-8", errors="replace"),
                    "byte_length": len(delta),
                }
            )

        fresh_generate = getattr(
            self._backend,
            "generate_fresh_integrated",
            None,
        )
        stream_generate = getattr(self._backend, "generate_stream", None)
        if callable(fresh_generate):
            try:
                raw = fresh_generate(
                    node_id=node_id,
                    input_bytes=input_bytes,
                    prompt_bytes=prompt_bytes,
                    config_bytes=config_bytes,
                    request_bytes=request_bytes,
                    emit_delta=emit_delta,
                )
            except _local.SupervisedWorkerFailure as exc:
                if (
                    not self._allow_scripted_fallback
                    and getattr(self._backend, "generation_started", False)
                    is not True
                ):
                    raise FreshIntegratedPreWorkerFailure(
                        "fresh integrated worker failed before generate start"
                    ) from exc
                raise
            except Exception as exc:
                if (
                    not self._allow_scripted_fallback
                    and getattr(self._backend, "generation_started", False)
                    is not True
                ):
                    raise FreshIntegratedPreWorkerFailure(
                        "fresh integrated worker failed before generate start"
                    ) from exc
                raise
            if not self._allow_scripted_fallback:
                if (
                    getattr(self._backend, "generation_started", False)
                    is not True
                ):
                    raise FreshIntegratedPreWorkerFailure(
                        "fresh integrated worker returned before generate start"
                    )
                mark_generation_started()
        elif callable(stream_generate):
            mark_generation_started()
            raw = stream_generate(
                node_id=node_id,
                input_bytes=input_bytes,
                prompt_bytes=prompt_bytes,
                config_bytes=config_bytes,
                request_bytes=request_bytes,
                emit_delta=emit_delta,
            )
        elif self._allow_scripted_fallback:
            mark_generation_started()
            raw = self._backend.generate(
                node_id=node_id,
                input_bytes=input_bytes,
                prompt_bytes=prompt_bytes,
                config_bytes=config_bytes,
                request_bytes=request_bytes,
            )
        else:
            raise FreshIntegratedPreWorkerFailure(
                "fresh integrated real worker must expose generate_fresh_integrated"
            )
        if type(raw) is not bytes:
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh integrated worker raw result must be bytes"
            )
        self._emit_event(
            {
                "event": "generation_completed",
                "node_id": node_id,
            }
        )
        return raw


class FreshIntegratedPilot:
    """Policy-checked wrapper around the existing P4-03 runner."""

    def __init__(
        self,
        *,
        policy: Mapping[str, object],
        qualification: Mapping[str, object],
        run_binding: Mapping[str, object],
        runner: Phase4LocalQwenPilotRunner,
        result_root: Path,
        console: object | None = None,
    ) -> None:
        self.policy = validate_fresh_integrated_policy(policy)
        self.qualification = validate_fresh_integrated_qualification(
            qualification
        )
        self.run_binding = validate_fresh_integrated_run_binding(run_binding)
        if runner.fresh_integrated_only is not True:
            raise Phase4LocalQwenFreshIntegratedError(
                "runner is not in fresh integrated-only mode"
            )
        if runner.ledger.integrated_run_count != 0:
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh integrated runner already consumed its run"
            )
        if self.run_binding["policy_id"] != self.policy["policy_id"]:
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh policy/run binding drifted"
            )
        if self.run_binding["qualification_id"] != self.qualification[
            "qualification_id"
        ]:
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh qualification/run binding drifted"
            )
        if not isinstance(result_root, Path) or not result_root.is_dir():
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh integrated result root is invalid"
            )
        self._runner = runner
        self._result_root = result_root
        self._console = console
        self._events: list[dict[str, object]] = []
        self._console_failures: list[str] = []
        self._ran = False
        self._last_runner_outcome: PilotOutcome | None = None

    @property
    def runner(self) -> Phase4LocalQwenPilotRunner:
        return self._runner

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        return tuple(copy.deepcopy(self._events))

    @property
    def last_runner_outcome(self) -> PilotOutcome | None:
        return (
            None
            if self._last_runner_outcome is None
            else PilotOutcome.from_dict(self._last_runner_outcome.to_dict())
        )

    def _emit(self, payload: Mapping[str, object]) -> None:
        event = {
            "schema_version": FRESH_INTEGRATED_EVENT_SCHEMA_VERSION,
            "sequence": len(self._events) + 1,
            "pilot_id": self.policy["pilot_id"],
            "run_id": self.policy["run_id"],
            **copy.deepcopy(dict(payload)),
        }
        self._events.append(event)
        if self._console is None:
            return
        try:
            if callable(self._console):
                self._console(event)
            elif callable(getattr(self._console, "write", None)):
                if event["event"] == "token_delta":
                    self._console.write(str(event["delta"]))
                    if callable(getattr(self._console, "flush", None)):
                        self._console.flush()
                else:
                    self._console.write(
                        f"[P4-03I] {event['event']} "
                        f"{event.get('node_id', '')}\n"
                    )
                    if callable(getattr(self._console, "flush", None)):
                        self._console.flush()
        except Exception as exc:
            # Console rendering is observational and cannot invalidate the
            # already persisted action or interrupt the worker call.
            self._console_failures.append(type(exc).__name__)

    def _observe_stage(self, event: str) -> None:
        if ":" in event:
            name, status = event.split(":", 1)
        else:
            name, status = event, "observed"
        self._emit(
            {
                "event": "stage",
                "stage": name,
                "status": status,
            }
        )

    def run_node_local(self, **_: object) -> None:
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated pilot forbids node-local generation"
        )

    def run_integrated(
        self,
        *,
        confirm_fresh_integrated_run: bool,
    ) -> dict[str, object]:
        if confirm_fresh_integrated_run is not True:
            raise Phase4LocalQwenFreshIntegratedError(
                "explicit fresh integrated run confirmation is required"
            )
        if self._ran:
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh integrated run cap is exhausted"
            )
        self._ran = True
        outcome = self._runner.run_integrated()
        if not isinstance(outcome, PilotOutcome):
            raise Phase4LocalQwenFreshIntegratedError(
                "runner returned an invalid pilot outcome"
            )
        self._last_runner_outcome = outcome
        node_counts = dict(self._runner.ledger.node_total_counts)
        if any(node_counts.get(node_id) not in {0, 1} for node_id in NODE_ORDER):
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh integrated per-node call cap drifted"
            )
        if len(self._runner.ledger.prior_failure_ids) > 1:
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh integrated failure ledger contains more than one failure"
            )
        if any(
            event.get("event") == "stage"
            and event.get("stage") == "run_node_local"
            for event in self._events
        ):
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh integrated event stream contains node-local execution"
            )
        normalization = self._runner.normalization_records.get("F4")
        result: dict[str, object] = {
            "schema_version": FRESH_INTEGRATED_RESULT_SCHEMA_VERSION,
            "result_id": "pending",
            "status": (
                "fresh_integrated_assembled"
                if outcome.integrated_outcome == "success"
                else "fresh_integrated_failed_closed"
            ),
            "pilot_id": self.policy["pilot_id"],
            "run_id": self.policy["run_id"],
            "policy_id": self.policy["policy_id"],
            "qualification_id": self.qualification["qualification_id"],
            "predecessor_link": copy.deepcopy(
                self.policy["predecessor_link"]
            ),
            "source_savepoint_is_not_integrated_input": True,
            "integrated_input_source": (
                "fresh_same_run_live_validated_upstream_outputs_only"
            ),
            "fresh_empty_authority_state_identity": self._runner.fresh_integrated_initial_authority_state_identity,
            "node_local_generate_allowed": False,
            "b_aux_disposition": "absent/not_requested",
            "retry_count": 0,
            "integrated_run_count": self._runner.ledger.integrated_run_count,
            "node_total_counts": node_counts,
            "backend_generate_calls": sum(node_counts.values()),
            "model_generate_calls": (
                self._runner.model_calls
                if self._runner.source_kind == "real_local_qwen"
                else 0
            ),
            "model_action": self._runner.source_kind == "real_local_qwen",
            "source_kind": outcome.source_kind,
            "raw_model_contract_success": (
                normalization.get("raw_model_contract_success")
                if normalization is not None
                else outcome.integrated_outcome == "success"
            ),
            "normalized_node_contract_success": (
                normalization.get("normalized_node_contract_success")
                if normalization is not None
                else outcome.integrated_outcome == "success"
            ),
            "f4_normalization": (
                copy.deepcopy(normalization)
                if normalization is not None
                else "not_observed"
            ),
            "integrated_outcome": outcome.integrated_outcome,
            "composition_status": outcome.composition_status,
            "assembler_status": outcome.assembler_status,
            "integrated_node_raw_contract_pass_count": (
                outcome.integrated_node_raw_contract_pass_count
            ),
            "downstream": {
                "consistency": "not_executed",
                "acceptance": "not_executed",
                "repair": "not_executed",
                "g0": "not_executed",
                "package": "not_executed",
                "production_route": "not_executed",
            },
            "event_count": len(self._events),
            "claim_boundary": (
                "assembler_only_no_acceptance_repair_g0_package_or_production"
            ),
        }
        result["result_id"] = _identity(
            {
                key: value
                for key, value in result.items()
                if key != "result_id"
            },
            revision=FRESH_INTEGRATED_RESULT_SCHEMA_VERSION,
        )["sha256"]
        _write_once(
            self._result_root,
            FRESH_INTEGRATED_EVENT_STREAM_NAME,
            _canonical_bytes({"events": self._events}),
        )
        _write_once(
            self._result_root,
            FRESH_INTEGRATED_RESULT_NAME,
            _canonical_bytes(result),
        )
        return copy.deepcopy(result)


def validate_fresh_integrated_policy(
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated policy must be an object"
        )
    policy = copy.deepcopy(dict(value))
    if policy.get("schema_version") != FRESH_INTEGRATED_POLICY_SCHEMA_VERSION:
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated policy schema drifted"
        )
    expected_id = _identity(
        {key: value for key, value in policy.items() if key != "policy_id"},
        revision=FRESH_INTEGRATED_POLICY_SCHEMA_VERSION,
    )["sha256"]
    if policy.get("policy_id") != expected_id:
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated policy identity drifted"
        )
    _validate_predecessor_link(policy.get("predecessor_link"))
    if (
        policy.get("node_order") != list(NODE_ORDER)
        or policy.get("node_local_generate_allowed") is not False
        or policy.get("integrated_run_cap") != 1
        or policy.get("per_node_integrated_generate_cap") != 1
        or policy.get("retry_count_cap") != 0
        or policy.get("b_aux_disposition") != "absent/not_requested"
        or policy.get("input_context", {}).get("artificial_input_token_cap")
        is not None
        or policy.get("output_contract", {}).get("artificial_output_truncation")
        is not False
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated policy budget/context boundary drifted"
        )
    return policy


def validate_fresh_integrated_run_binding(
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated run binding must be an object"
        )
    binding = copy.deepcopy(dict(value))
    if binding.get("schema_version") != FRESH_INTEGRATED_RUN_BINDING_SCHEMA_VERSION:
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated run binding schema drifted"
        )
    expected_id = _identity(
        {
            key: value
            for key, value in binding.items()
            if key != "run_binding_id"
        },
        revision=FRESH_INTEGRATED_RUN_BINDING_SCHEMA_VERSION,
    )["sha256"]
    if binding.get("run_binding_id") != expected_id:
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated run binding identity drifted"
        )
    _validate_predecessor_link(binding.get("predecessor_link"))
    for key, expected in (
        ("source_savepoint_is_not_integrated_input", True),
        ("fresh_empty_authority_state_required", True),
        ("node_local_generate_allowed", False),
        ("integrated_run_cap", 1),
        ("per_node_integrated_generate_cap", 1),
        ("retry_count_cap", 0),
        ("b_aux_disposition", "absent/not_requested"),
    ):
        if binding.get(key) != expected:
            raise Phase4LocalQwenFreshIntegratedError(
                f"fresh integrated run binding drifted at {key}"
            )
    return binding


def prepare_phase4_local_qwen_fresh_integrated(
    *,
    qualification_savepoint_root: Path,
    result_root: Path,
    run_id: str | None = None,
    pilot_id: str = FRESH_INTEGRATED_PILOT_ID,
) -> dict[str, object]:
    """Write a no-model fresh integrated policy, receipt, and run seam."""

    qualification = _validate_integrated_savepoint(qualification_savepoint_root)
    selected_run_id = run_id or (
        f"{FRESH_INTEGRATED_RUN_PREFIX}{uuid.uuid4().hex[:16]}"
    )
    policy = create_fresh_integrated_policy(
        pilot_id=pilot_id,
        run_id=selected_run_id,
    )
    result_root, marker = _prepare_result_root(result_root)
    run_binding = _write_run_binding(
        policy=policy,
        qualification=qualification,
        marker=marker,
    )
    b_input = synthetic_commerce_b_input()
    _fixture_runner_material(
        result_root=result_root,
        marker=marker,
        policy=policy,
        b_input=b_input,
    )
    for name, value in (
        (FRESH_INTEGRATED_POLICY_NAME, policy),
        (FRESH_INTEGRATED_QUALIFICATION_NAME, qualification),
        (FRESH_INTEGRATED_RUN_BINDING_NAME, run_binding),
    ):
        _write_once(result_root, name, _canonical_bytes(value))
    prepared = {
        "schema_version": FRESH_INTEGRATED_RESULT_SCHEMA_VERSION,
        "status": "prepared_no_model",
        "pilot_id": policy["pilot_id"],
        "run_id": policy["run_id"],
        "policy_id": policy["policy_id"],
        "qualification_id": qualification["qualification_id"],
        "result_root_marker": marker,
        "model_action": False,
        "graph_runtime_execution": False,
        "model_generate_calls": 0,
        "source_savepoint_is_not_integrated_input": True,
        "claim_boundary": "prepared_fresh_integrated_seam_not_model_run",
    }
    _write_once(
        result_root,
        FRESH_INTEGRATED_PREPARE_RESULT_NAME,
        _canonical_bytes(prepared),
    )
    return copy.deepcopy(prepared)


def _load_prepared(
    result_root: Path,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    PilotBinding,
    tuple[NodeProjectionPolicy, ...],
    LocalQwenProfile,
    PreCallManifest,
    dict[str, object],
]:
    root = _safe_existing_root(result_root, "fresh integrated prepared root")
    policy_raw, policy = _read_json(root, FRESH_INTEGRATED_POLICY_NAME)
    qualification_raw, qualification = _read_json(
        root,
        FRESH_INTEGRATED_QUALIFICATION_NAME,
    )
    run_binding_raw, run_binding = _read_json(
        root,
        FRESH_INTEGRATED_RUN_BINDING_NAME,
    )
    del policy_raw, qualification_raw, run_binding_raw
    validate_fresh_integrated_policy(policy)
    validate_fresh_integrated_qualification(qualification)
    validate_fresh_integrated_run_binding(run_binding)
    manifest_raw = _read_regular(root, FRESH_INTEGRATED_MANIFEST_NAME)
    manifest = PreCallManifest.from_bytes(manifest_raw)
    pilot = PilotBinding.from_dict(manifest.pilot_binding)
    policies = tuple(
        NodeProjectionPolicy.from_dict(item)
        for item in manifest.projection_policies
    )
    profile = LocalQwenProfile.from_dict(manifest.profile)
    marker = _read_regular(root, FRESH_INTEGRATED_ROOT_MARKER).decode("ascii")
    if marker != pilot.result_root_marker or marker != run_binding["result_root_marker"]:
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh integrated result-root marker drifted"
        )
    if (
        pilot.pilot_id != policy["pilot_id"]
        or run_binding["pilot_id"] != pilot.pilot_id
        or run_binding["run_id"] != policy["run_id"]
        or run_binding["predecessor_link"] != policy["predecessor_link"]
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            "fresh prepared runner identity drifted"
        )
    if policy["predecessor_link"]["mode"] == "pre_worker_failure":
        _, predecessor = _read_json(root, FRESH_INTEGRATED_PREDECESSOR_NAME)
        _validate_predecessor_receipt(predecessor)
        if _predecessor_link(predecessor) != policy["predecessor_link"]:
            raise Phase4LocalQwenFreshIntegratedError(
                "fresh prepared predecessor link drifted"
            )
    return (
        policy,
        qualification,
        run_binding,
        pilot,
        policies,
        profile,
        manifest,
        synthetic_commerce_b_input(),
    )


def run_phase4_local_qwen_fresh_integrated_scripted(
    *,
    prepared_root: Path,
    confirm_fresh_integrated_run: bool,
    f4_order_drift: bool = False,
    console: object | None = None,
) -> dict[str, object]:
    """Run only the explicit scripted fixture seam; never loads a model."""

    (
        policy,
        qualification,
        run_binding,
        pilot,
        policies,
        profile,
        manifest,
        b_input,
    ) = _load_prepared(prepared_root)
    backend = ScriptedStreamingBackend(
        _fixture_outputs(b_input, f4_order_drift=f4_order_drift)
    )
    adapter = _FreshBackendAdapter(
        backend,
        event_sink=lambda _: None,
        allow_scripted_fallback=True,
    )
    runner = Phase4LocalQwenPilotRunner(
        pilot=pilot,
        policies=policies,
        profile=profile,
        manifest=manifest,
        result_root=prepared_root,
        b_input=b_input,
        backend=adapter,
        source_kind="scripted_test_fixture",
        fresh_integrated_only=True,
        integrated_event_observer=lambda _: None,
        f4_normalizer=lambda raw, output, state: (
            phase4_normalize_and_validate_f4_output(
                raw_bytes=raw,
                output=output,
                state=state,
            )
        ),
        integrated_run_id=str(policy["run_id"]),
    )
    pilot_runner = FreshIntegratedPilot(
        policy=policy,
        qualification=qualification,
        run_binding=run_binding,
        runner=runner,
        result_root=prepared_root,
        console=console,
    )
    adapter._event_sink = pilot_runner._emit
    runner._integrated_event_observer = pilot_runner._observe_stage
    result = pilot_runner.run_integrated(
        confirm_fresh_integrated_run=confirm_fresh_integrated_run
    )
    return result


def run_phase4_local_qwen_fresh_integrated(
    *,
    qualification_savepoint_root: Path,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    confirm_one_local_fresh_integrated_run: bool,
    predecessor_result_root: Path | None = None,
    run_id: str | None = None,
    console: object | None = None,
) -> dict[str, object]:
    """Run one real fresh-integrated pilot through the persistent worker."""

    if confirm_one_local_fresh_integrated_run is not True:
        raise Phase4LocalQwenFreshIntegratedError(
            "explicit fresh integrated model-run confirmation is required"
        )
    model_root = _safe_existing_root(model_root, "model root")
    if (
        not isinstance(integrity_evidence, Path)
        or not integrity_evidence.is_absolute()
        or not integrity_evidence.is_file()
        or integrity_evidence.is_symlink()
    ):
        raise Phase4LocalQwenFreshIntegratedError(
            "integrity evidence must be an existing absolute regular file"
        )
    qualification = _validate_integrated_savepoint(
        qualification_savepoint_root
    )
    predecessor = (
        None
        if predecessor_result_root is None
        else _validate_predecessor_result_root(predecessor_result_root)
    )
    selected_run_id = run_id or (
        f"{FRESH_INTEGRATED_RUN_PREFIX}{uuid.uuid4().hex[:16]}"
    )
    policy = create_fresh_integrated_policy(
        pilot_id=FRESH_INTEGRATED_PILOT_ID,
        run_id=selected_run_id,
        predecessor_receipt=predecessor,
    )
    result_root, marker = _prepare_result_root(result_root)
    run_binding = _write_run_binding(
        policy=policy,
        qualification=qualification,
        marker=marker,
    )
    b_input = synthetic_commerce_b_input()
    (
        pilot,
        policies,
        profile,
        manifest,
        preflight,
    ) = _real_runner_material(
        result_root=result_root,
        marker=marker,
        policy=policy,
        b_input=b_input,
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    for name, value in (
        (FRESH_INTEGRATED_POLICY_NAME, policy),
        (FRESH_INTEGRATED_QUALIFICATION_NAME, qualification),
        (FRESH_INTEGRATED_RUN_BINDING_NAME, run_binding),
        (FRESH_INTEGRATED_PREFLIGHT_NAME, preflight),
    ):
        _write_once(result_root, name, _canonical_bytes(value))
    if predecessor is not None:
        _write_once(
            result_root,
            FRESH_INTEGRATED_PREDECESSOR_NAME,
            _canonical_bytes(predecessor),
        )
    lease = acquire_pilot_execution_lease(
        result_root=result_root,
        pilot=pilot,
        manifest=manifest,
    )
    runtime = None
    pilot_runner: FreshIntegratedPilot | None = None
    runner: Phase4LocalQwenPilotRunner | None = None
    runner_outcome: PilotOutcome | None = None
    result: dict[str, object] | None = None
    try:
        runtime = start_supervised_local_qwen_fresh_integrated_runtime(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
            result_root=result_root,
            pilot=pilot,
            policies=policies,
            profile=profile,
            manifest=manifest,
            b_input=b_input,
            execution_lease=lease,
        )
        adapter = _FreshBackendAdapter(
            runtime.backend,
            event_sink=lambda _: None,
            allow_scripted_fallback=False,
        )
        runner = Phase4LocalQwenPilotRunner(
            pilot=pilot,
            policies=policies,
            profile=profile,
            manifest=manifest,
            result_root=result_root,
            b_input=b_input,
            model_root=model_root,
            backend=adapter,
            source_kind="real_local_qwen",
            load_receipt=runtime.load_receipt,
            execution_lease=runtime.execution_lease,
            runtime_start_claim=runtime.runtime_start_claim,
            fresh_integrated_only=True,
            integrated_event_observer=lambda _: None,
            f4_normalizer=lambda raw, output, state: (
                phase4_normalize_and_validate_f4_output(
                    raw_bytes=raw,
                    output=output,
                    state=state,
                )
            ),
            integrated_run_id=str(policy["run_id"]),
        )
        pilot_runner = FreshIntegratedPilot(
            policy=policy,
            qualification=qualification,
            run_binding=run_binding,
            runner=runner,
            result_root=result_root,
            console=console,
        )
        adapter._event_sink = pilot_runner._emit
        runner._integrated_event_observer = pilot_runner._observe_stage
        result = pilot_runner.run_integrated(
            confirm_fresh_integrated_run=True
        )
        runner_outcome = pilot_runner.last_runner_outcome
        if runner_outcome is not None:
            persist_pilot_outcome(
                result_root=result_root,
                outcome=runner_outcome,
            )
        return result
    finally:
        if runtime is not None:
            teardown = runtime.backend.close()
            stderr_bytes = runtime.backend.stderr_bytes
            stderr_identity = None
            if type(stderr_bytes) is bytes:
                stderr_artifact = persist_worker_stderr_artifact(
                    result_root=result_root,
                    stderr_bytes=stderr_bytes,
                )
                stderr_identity = stderr_artifact.identity()
            if runner_outcome is None and runner is not None:
                runner_outcome = runner.outcome(
                    stop_reason="fresh_integrated_parent_finalized"
                )
                persist_pilot_outcome(
                    result_root=result_root,
                    outcome=runner_outcome,
                )
            terminal_status = str(teardown.get("terminal_status"))
            if terminal_status not in {
                "normal_completed",
                "generation_timeout",
                "generation_cancelled",
                "worker_failed",
                "worker_teardown_unverified",
            }:
                terminal_status = "worker_failed"
            latest = None if runner is None else runner.latest_result
            latest_identity = (
                None
                if latest is None
                else _identity(
                    latest.to_dict(),
                    revision=_local.ATTEMPT_RESULT_SCHEMA_VERSION,
                )
            )
            outcome_identity = (
                None
                if runner_outcome is None
                else _identity(
                    runner_outcome.to_dict(),
                    revision=_local.OUTCOME_SCHEMA_VERSION,
                )
            )
            raw_status = (
                "captured"
                if runtime.backend.last_raw_captured
                else "not_captured"
            )
            supervisor = PilotSupervisorReceipt.create(
                lease=lease,
                terminal_status=terminal_status,
                worker_id=teardown.get("worker_id"),
                worker_pid=teardown.get("worker_pid"),
                worker_exit_code=teardown.get("worker_exit_code"),
                worker_exit_verified=bool(
                    teardown.get("worker_exit_verified")
                ),
                graceful_shutdown_requested=bool(
                    teardown.get("graceful_shutdown_requested")
                ),
                terminate_sent=bool(teardown.get("terminate_sent")),
                kill_sent=bool(teardown.get("kill_sent")),
                generation_started=runtime.backend.generation_started,
                raw_status=raw_status,
                latest_attempt_result_identity=latest_identity,
                pilot_outcome_identity=outcome_identity,
                stderr_identity=stderr_identity,
                model_action=True,
            )
            persist_supervisor_receipt(
                result_root=result_root,
                receipt=supervisor,
            )


__all__ = [
    "FRESH_INTEGRATED_CONFIG_REVISION",
    "FRESH_INTEGRATED_EVENT_SCHEMA_VERSION",
    "FRESH_INTEGRATED_PILOT_ID",
    "FRESH_INTEGRATED_POLICY_NAME",
    "FRESH_INTEGRATED_POLICY_SCHEMA_VERSION",
    "FRESH_INTEGRATED_PREDECESSOR_NAME",
    "FRESH_INTEGRATED_PREDECESSOR_SCHEMA_VERSION",
    "FRESH_INTEGRATED_QUALIFICATION_NAME",
    "FRESH_INTEGRATED_QUALIFICATION_SCHEMA_VERSION",
    "FRESH_INTEGRATED_RESULT_NAME",
    "FRESH_INTEGRATED_PREFLIGHT_NAME",
    "FRESH_INTEGRATED_RESULT_SCHEMA_VERSION",
    "FRESH_INTEGRATED_RUN_BINDING_NAME",
    "FRESH_INTEGRATED_RUN_BINDING_SCHEMA_VERSION",
    "FRESH_INTEGRATED_RUN_PREFIX",
    "FreshIntegratedBackend",
    "FreshIntegratedPilot",
    "Phase4LocalQwenFreshIntegratedError",
    "FreshIntegratedPreWorkerFailure",
    "ScriptedStreamingBackend",
    "create_fresh_integrated_policy",
    "prepare_phase4_local_qwen_fresh_integrated",
    "run_phase4_local_qwen_fresh_integrated_scripted",
    "run_phase4_local_qwen_fresh_integrated",
    "validate_fresh_integrated_policy",
    "validate_fresh_integrated_qualification",
    "validate_fresh_integrated_run_binding",
]
