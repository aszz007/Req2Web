"""Zero-model revalidation for one terminal fresh-integrated Phase 4 result.

This module is intentionally a read-only verifier for the source result root.
It replays the source F1/F2/F3 raw outputs, dispatches the source F4 raw bytes
through the accepted P4-01b ownership normalizer, and then reuses the existing
registry, mapping, composition, and CanonicalPageSpecAssembler authorities.
The new result root is immutable and contains only the revalidation artifacts;
the source root is never written.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
from typing import Mapping

from req2web_orchestration import phase4_graph as _graph
from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    phase4_assemble_candidate,
    phase4_compose_candidate,
    phase4_create_authority_state,
    phase4_create_mapping,
    phase4_normalize_and_validate_f4_output,
    phase4_register_node_output,
    phase4_synthetic_assembler_bindings,
    phase4_validate_f4_p4_01b_normalization_receipt,
)
from req2web_runtime import phase4_local_qwen as _local
from req2web_runtime import phase4_local_qwen_fresh_integrated as _fresh
from req2web_runtime import phase4_local_qwen_integrated as _integrated
from req2web_runtime.phase4_local_qwen import (
    AttemptLedger,
    AttemptPreCall,
    AttemptResult,
    LocalQwenLoadReceipt,
    LocalQwenProfile,
    NodeD17ActionRecord,
    NodeProjectionPolicy,
    PilotBinding,
    PilotExecutionLease,
    PilotOutcome,
    PilotSupervisorReceipt,
    PreCallManifest,
    WorkerStderrArtifact,
)


REVALIDATION_SCHEMA_PREFIX = (
    "req2web.phase4.local_qwen.fresh_integrated_revalidation"
)
REVALIDATION_ROOT_MARKER_SCHEMA_VERSION = (
    f"{REVALIDATION_SCHEMA_PREFIX}.root_marker.v1"
)
REVALIDATION_RUNTIME_BINDING_SCHEMA_VERSION = (
    f"{REVALIDATION_SCHEMA_PREFIX}.runtime_binding.v1"
)
REVALIDATION_RESULT_SCHEMA_VERSION = (
    f"{REVALIDATION_SCHEMA_PREFIX}.result.v1"
)
REVALIDATION_ARTIFACT_REVISION = (
    f"{REVALIDATION_SCHEMA_PREFIX}.artifact.v1"
)

REVALIDATION_ROOT_MARKER = (
    ".req2web-phase4-local-qwen-fresh-integrated-revalidation-root"
)
REVALIDATION_NORMALIZED_F4_NAME = "normalized_f4_node_output.json"
REVALIDATION_CORE_RECEIPT_NAME = "core_f4_normalization_receipt.json"
REVALIDATION_RUNTIME_BINDING_NAME = "runtime_binding_receipt.json"
REVALIDATION_CANDIDATE_NAME = "candidate_composition_record.json"
REVALIDATION_PAGE_SPEC_NAME = "assembled_page_spec.json"
REVALIDATION_ASSEMBLY_REPORT_NAME = "assembly_report.json"
REVALIDATION_RESULT_NAME = "revalidation_result.json"

REVALIDATION_OUTPUT_NAMES = (
    REVALIDATION_ROOT_MARKER,
    REVALIDATION_NORMALIZED_F4_NAME,
    REVALIDATION_CORE_RECEIPT_NAME,
    REVALIDATION_RUNTIME_BINDING_NAME,
    REVALIDATION_CANDIDATE_NAME,
    REVALIDATION_PAGE_SPEC_NAME,
    REVALIDATION_ASSEMBLY_REPORT_NAME,
    REVALIDATION_RESULT_NAME,
)

_SOURCE_RESULT_KEYS = (
    "schema_version",
    "result_id",
    "status",
    "pilot_id",
    "run_id",
    "policy_id",
    "qualification_id",
    "predecessor_link",
    "source_savepoint_is_not_integrated_input",
    "integrated_input_source",
    "fresh_empty_authority_state_identity",
    "node_local_generate_allowed",
    "b_aux_disposition",
    "retry_count",
    "integrated_run_count",
    "node_total_counts",
    "backend_generate_calls",
    "model_generate_calls",
    "model_action",
    "source_kind",
    "raw_model_contract_success",
    "normalized_node_contract_success",
    "f4_normalization",
    "integrated_outcome",
    "composition_status",
    "assembler_status",
    "integrated_node_raw_contract_pass_count",
    "downstream",
    "event_count",
    "claim_boundary",
)


class Phase4LocalQwenFreshIntegratedRevalidationError(
    _fresh.Phase4LocalQwenFreshIntegratedError
):
    """Raised when fresh-integrated zero-model revalidation fails closed."""


def _canonical(value: object) -> bytes:
    return _local._canonical_bytes(value)


def _identity(
    value: object,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    return _local._identity(value, revision=revision, identity_kind=identity_kind)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _error(message: str) -> Phase4LocalQwenFreshIntegratedRevalidationError:
    return Phase4LocalQwenFreshIntegratedRevalidationError(message)


def _safe_existing_root(path: Path, name: str) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or not path.is_dir()
        or path.is_symlink()
    ):
        raise _error(f"{name} must be an existing absolute non-symlink directory")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _error(f"{name} cannot be resolved") from exc
    if resolved.is_symlink():
        raise _error(f"{name} resolves through a symlink")
    return resolved


def _safe_new_root(path: Path, *, source_root: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or path.exists():
        raise _error("revalidation result root must be a new absolute directory")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise _error("revalidation result root parent is unsafe")
    source_resolved = source_root.resolve(strict=True)
    candidate = path.resolve(strict=False)
    try:
        candidate.relative_to(source_resolved)
    except ValueError:
        pass
    else:
        raise _error("revalidation result root must not be inside the source root")
    return candidate


def _read_regular(root: Path, relative: str) -> bytes:
    if type(relative) is not str or not relative or Path(relative).is_absolute():
        raise _error("artifact relative path is invalid")
    path = root / Path(relative)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _error(f"required artifact is unavailable: {relative}") from exc
    try:
        resolved.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise _error(f"artifact escapes its root: {relative}") from exc
    if not path.is_file() or path.is_symlink() or resolved.is_symlink():
        raise _error(f"required artifact is not a regular file: {relative}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise _error(f"required artifact cannot be read: {relative}") from exc


def _read_json(
    root: Path,
    relative: str,
    *,
    canonical: bool = True,
) -> tuple[bytes, dict[str, object]]:
    raw = _read_regular(root, relative)
    try:
        value = _local._strict_json(raw, require_canonical=canonical)
    except Exception as exc:
        raise _error(f"required JSON is invalid: {relative}") from exc
    return raw, value


def _write_once(root: Path, relative: str, raw: bytes) -> None:
    _local._write_once(root, relative, raw)


def _artifact_identity(
    raw: bytes,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    value: object = raw
    if identity_kind != "raw_bytes":
        value = _local._strict_json(raw, require_canonical=True)
    return _identity(value, revision=revision, identity_kind=identity_kind)


def _record_identity(record: object, schema_version: str) -> dict[str, object]:
    if not hasattr(record, "to_dict"):
        raise _error("canonical record is unavailable")
    return _identity(record.to_dict(), revision=schema_version)


def _require(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise _error(message)


def _require_keys(value: Mapping[str, object], keys: tuple[str, ...], name: str) -> None:
    if set(value) != set(keys):
        raise _error(f"{name} has non-exact keys")


def _read_record(
    root: Path,
    relative: str,
    record_type: type,
) -> tuple[bytes, object, dict[str, object]]:
    raw = _read_regular(root, relative)
    try:
        record = record_type.from_bytes(raw)
    except Exception as exc:
        raise _error(f"canonical record validation failed: {relative}") from exc
    return raw, record, _record_identity(record, record_type.SCHEMA_VERSION)


def _validate_preflight(
    raw: bytes,
    preflight: Mapping[str, object],
    *,
    policy: Mapping[str, object],
    manifest: PreCallManifest,
    profile: LocalQwenProfile,
    pilot: PilotBinding,
) -> dict[str, object]:
    _require_keys(preflight, (
        "action_state",
        "input_context",
        "input_truncation",
        "manifest_identity",
        "max_input_tokens",
        "model_inventory_identity",
        "model_root_identity",
        "native_context_identity",
        "native_context_source",
        "output_limit_kind",
        "output_truncation",
        "pilot_id",
        "policy_id",
        "preflight_status",
        "profile_identity",
        "schema_version",
    ), "fresh_integrated_preflight")
    _require(
        preflight["schema_version"],
        _fresh.FRESH_INTEGRATED_MODEL_CONTEXT_REVISION,
        "fresh preflight schema drifted",
    )
    _require(preflight["pilot_id"], pilot.pilot_id, "preflight pilot drifted")
    _require(preflight["policy_id"], policy["policy_id"], "preflight policy drifted")
    _require(
        preflight["preflight_status"],
        "prepared_no_model",
        "preflight status drifted",
    )
    _require(preflight["input_truncation"], False, "preflight input truncation drifted")
    _require(preflight["output_truncation"], False, "preflight output truncation drifted")
    _require(
        preflight["output_limit_kind"],
        "context_remaining",
        "preflight output limit drifted",
    )
    _require(
        preflight["max_input_tokens"],
        profile.max_input_tokens,
        "preflight context binding drifted",
    )
    _require(
        preflight["manifest_identity"],
        _identity(manifest.to_dict(), revision=PreCallManifest.SCHEMA_VERSION),
        "preflight manifest identity drifted",
    )
    _require(
        preflight["profile_identity"],
        _identity(profile.to_dict(), revision=LocalQwenProfile.SCHEMA_VERSION),
        "preflight profile identity drifted",
    )
    _require(
        preflight["model_root_identity"],
        profile.model_root_identity,
        "preflight model-root identity drifted",
    )
    _require(
        preflight["model_inventory_identity"],
        profile.model_inventory_identity,
        "preflight model inventory identity drifted",
    )
    if preflight["action_state"] != policy["action_state"]:
        raise _error("preflight action-state drifted")
    return copy.deepcopy(dict(preflight))


def _validate_source_result(
    raw: bytes,
    result: Mapping[str, object],
    *,
    policy: Mapping[str, object],
    qualification: Mapping[str, object],
    pilot: PilotBinding,
) -> dict[str, object]:
    _require_keys(result, _SOURCE_RESULT_KEYS, "fresh_integrated_result")
    _require(result["schema_version"], _fresh.FRESH_INTEGRATED_RESULT_SCHEMA_VERSION, "source result schema drifted")
    expected_id = _identity(
        {key: value for key, value in result.items() if key != "result_id"},
        revision=_fresh.FRESH_INTEGRATED_RESULT_SCHEMA_VERSION,
    )["sha256"]
    _require(result["result_id"], expected_id, "source result identity drifted")
    expected = {
        "status": "fresh_integrated_failed_closed",
        "pilot_id": pilot.pilot_id,
        "run_id": policy["run_id"],
        "policy_id": policy["policy_id"],
        "qualification_id": qualification["qualification_id"],
        "source_savepoint_is_not_integrated_input": True,
        "node_local_generate_allowed": False,
        "b_aux_disposition": "absent/not_requested",
        "retry_count": 0,
        "integrated_run_count": 1,
        "backend_generate_calls": 4,
        "model_generate_calls": 4,
        "model_action": True,
        "source_kind": "real_local_qwen",
        "raw_model_contract_success": False,
        "normalized_node_contract_success": False,
        "f4_normalization": "not_observed",
        "integrated_outcome": "failed_closed",
        "composition_status": "not_executed",
        "assembler_status": "not_executed",
        "integrated_node_raw_contract_pass_count": 3,
    }
    for key, value in expected.items():
        _require(result.get(key), value, f"source result drifted at {key}")
    _require(
        result["node_total_counts"],
        {node_id: 1 for node_id in NODE_ORDER},
        "source result node counts drifted",
    )
    _require(
        result["downstream"],
        {
            "consistency": "not_executed",
            "acceptance": "not_executed",
            "g0": "not_executed",
            "package": "not_executed",
            "production_route": "not_executed",
            "repair": "not_executed",
        },
        "source result downstream state drifted",
    )
    return copy.deepcopy(dict(result))


def _validate_attempt(
    source_root: Path,
    *,
    run_id: str,
    node_id: str,
    pilot: PilotBinding,
    manifest: PreCallManifest,
    policies: Mapping[str, NodeProjectionPolicy],
    profile: LocalQwenProfile,
    load_receipt: LocalQwenLoadReceipt,
) -> dict[str, object]:
    attempt_relative = f"runs/{run_id}/{node_id}/attempt-01"
    pre_relative = f"{attempt_relative}/pre_call.json"
    action_relative = f"{attempt_relative}/node_d17_action.json"
    result_relative = f"{attempt_relative}/attempt_result.json"
    raw_relative = f"{attempt_relative}/raw_response.bin"
    pre_raw, pre_call, pre_identity = _read_record(
        source_root,
        pre_relative,
        AttemptPreCall,
    )
    action_raw, action, action_identity = _read_record(
        source_root,
        action_relative,
        NodeD17ActionRecord,
    )
    result_raw, attempt_result, result_identity = _read_record(
        source_root,
        result_relative,
        AttemptResult,
    )
    raw_response = _read_regular(source_root, raw_relative)
    if not raw_response:
        raise _error(f"{node_id} raw response is empty")

    if not isinstance(pre_call, AttemptPreCall) or not isinstance(action, NodeD17ActionRecord):
        raise _error(f"{node_id} attempt record type drifted")
    if not isinstance(attempt_result, AttemptResult):
        raise _error(f"{node_id} attempt result type drifted")
    try:
        action.validate_against(
            pilot=pilot,
            policy=policies[node_id],
            profile=profile,
            load_receipt=load_receipt,
        )
    except Exception as exc:
        raise _error(f"{node_id} action-time binding validation failed") from exc
    expected_action_identity = _identity(
        action.to_dict(),
        revision=NodeD17ActionRecord.SCHEMA_VERSION,
    )
    _require(
        pre_call.action_record_identity,
        expected_action_identity,
        f"{node_id} pre-call/action identity drifted",
    )
    _require(
        pre_call.action_record_id,
        action.action_record_id,
        f"{node_id} pre-call/action ID drifted",
    )
    expected_raw_sha = _sha256(raw_response)
    for record in (pre_call, action, attempt_result):
        _require(record.pilot_id, pilot.pilot_id, f"{node_id} pilot binding drifted")
        _require(record.run_id, run_id, f"{node_id} run binding drifted")
        _require(record.case_id, pilot.case_binding["case_id"], f"{node_id} case binding drifted")
        _require(record.request_id, pilot.case_binding["request_id"], f"{node_id} request binding drifted")
        _require(record.node_id, node_id, f"{node_id} node binding drifted")
        _require(record.attempt_index, 1, f"{node_id} attempt index drifted")
        _require(record.call_kind, "integrated", f"{node_id} call kind drifted")
        _require(record.call_count, 1, f"{node_id} call count drifted")
        _require(record.retry_count, 0, f"{node_id} retry count drifted")
        _require(record.source_kind, "real_local_qwen", f"{node_id} source kind drifted")
    _require(
        pre_call.attempt_relative_path,
        attempt_relative,
        f"{node_id} pre-call path drifted",
    )
    _require(
        pre_call.raw_response_relative_path,
        raw_relative,
        f"{node_id} pre-call raw path drifted",
    )
    _require(
        attempt_result.pre_call_id,
        pre_call.pre_call_id,
        f"{node_id} result/pre-call binding drifted",
    )
    _require(
        attempt_result.action_record_id,
        action.action_record_id,
        f"{node_id} result/action binding drifted",
    )
    _require(
        attempt_result.raw_response_relative_path,
        raw_relative,
        f"{node_id} result/raw path drifted",
    )
    _require(
        attempt_result.raw_sha256,
        expected_raw_sha,
        f"{node_id} raw hash drifted",
    )
    _require(
        attempt_result.raw_byte_length,
        len(raw_response),
        f"{node_id} raw byte length drifted",
    )
    _require(
        action.generate_started,
        False,
        f"{node_id} action-time pre-call state drifted",
    )
    _require(
        attempt_result.generate_started,
        True,
        f"{node_id} attempt did not record generate start",
    )
    try:
        actual_input = base64.b64decode(action.actual_input_b64, validate=True)
        prompt = base64.b64decode(action.prompt_b64, validate=True)
        config = base64.b64decode(action.config_b64, validate=True)
        request = base64.b64decode(action.request_b64, validate=True)
    except Exception as exc:
        raise _error(f"{node_id} action byte fields are invalid") from exc
    for name, value, expected_hash, expected_length in (
        ("input", actual_input, action.actual_input_sha256, action.actual_input_byte_length),
        ("prompt", prompt, action.prompt_sha256, action.prompt_byte_length),
        ("config", config, action.config_sha256, action.config_byte_length),
        ("request", request, action.request_sha256, action.request_byte_length),
    ):
        _require(_sha256(value), expected_hash, f"{node_id} {name} hash drifted")
        _require(len(value), expected_length, f"{node_id} {name} length drifted")
    for name, value, expected in (
        ("input", actual_input, pre_call.input_identity),
        ("prompt", prompt, pre_call.prompt_identity),
        ("config", config, pre_call.config_identity),
        ("request", request, pre_call.request_identity),
    ):
        _require(
            expected,
            {"sha256": _sha256(value), "byte_length": len(value)},
            f"{node_id} pre-call {name} identity drifted",
        )
    if list(pre_call.upstream_identities) != list(action.upstream_refs):
        raise _error(f"{node_id} upstream identity drifted")
    return {
        "attempt_relative": attempt_relative,
        "pre_call": pre_call,
        "action": action,
        "attempt_result": attempt_result,
        "raw": raw_response,
        "pre_call_identity": pre_identity,
        "action_identity": action_identity,
        "attempt_result_identity": result_identity,
        "raw_identity": _identity(
            raw_response,
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.{node_id}.raw",
            identity_kind="raw_bytes",
        ),
        "pre_call_raw": pre_raw,
        "action_raw": action_raw,
        "attempt_result_raw": result_raw,
    }


def _validate_source_root(
    source_result_root: Path,
    *,
    qualification_savepoint_root: Path,
) -> dict[str, object]:
    root = _safe_existing_root(source_result_root, "source result root")
    marker_raw = _read_regular(root, _fresh.FRESH_INTEGRATED_ROOT_MARKER)
    try:
        marker = marker_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise _error("source result root marker is not ASCII") from exc
    if not marker or marker != marker.strip():
        raise _error("source result root marker is invalid")
    marker_identity = _identity(
        marker_raw,
        revision=f"{REVALIDATION_ARTIFACT_REVISION}.source_root_marker",
        identity_kind="raw_bytes",
    )

    _, policy_raw_value = _read_json(root, _fresh.FRESH_INTEGRATED_POLICY_NAME)
    _, source_qualification = _read_json(
        root,
        _fresh.FRESH_INTEGRATED_QUALIFICATION_NAME,
    )
    _, run_binding = _read_json(root, _fresh.FRESH_INTEGRATED_RUN_BINDING_NAME)
    preflight_raw, preflight = _read_json(root, _fresh.FRESH_INTEGRATED_PREFLIGHT_NAME)
    result_raw, source_result = _read_json(root, _fresh.FRESH_INTEGRATED_RESULT_NAME)
    policy = _fresh.validate_fresh_integrated_policy(policy_raw_value)
    live_qualification = _fresh.validate_fresh_integrated_qualification(
        source_qualification,
        savepoint_root=qualification_savepoint_root,
    )
    run_binding = _fresh.validate_fresh_integrated_run_binding(run_binding)

    manifest_raw, manifest, manifest_identity = _read_record(
        root,
        _fresh.FRESH_INTEGRATED_MANIFEST_NAME,
        PreCallManifest,
    )
    if not isinstance(manifest, PreCallManifest):
        raise _error("source pre-call manifest type drifted")
    pilot = PilotBinding.from_dict(manifest.pilot_binding)
    policies = {
        policy_value.node_id: policy_value
        for policy_value in (
            NodeProjectionPolicy.from_dict(item)
            for item in manifest.projection_policies
        )
    }
    profile = LocalQwenProfile.from_dict(manifest.profile)

    _require(marker, manifest.result_root_marker, "source marker/manifest drifted")
    _require(marker, pilot.result_root_marker, "source marker/pilot drifted")
    _require(marker, run_binding["result_root_marker"], "source marker/run binding drifted")
    _require(policy["policy_id"], run_binding["policy_id"], "source policy/run binding drifted")
    _require(policy["pilot_id"], pilot.pilot_id, "source pilot drifted")
    _require(policy["run_id"], run_binding["run_id"], "source run ID drifted")
    _require(
        run_binding["qualification_id"],
        live_qualification["qualification_id"],
        "source qualification drifted",
    )
    _require(
        policy["node_order"],
        list(NODE_ORDER),
        "source policy node order drifted",
    )
    _require(
        policy["action_state"]["model_action"],
        False,
        "source policy action state drifted",
    )
    if set(policies) != set(NODE_ORDER):
        raise _error("source manifest policy set drifted")
    try:
        load_raw, load_receipt, load_identity = _read_record(
            root,
            "local_qwen_load_receipt.json",
            LocalQwenLoadReceipt,
        )
        lease_raw, lease, lease_identity = _read_record(
            root,
            "execution_lease.json",
            PilotExecutionLease,
        )
        ledger_raw, ledger, ledger_identity = _read_record(
            root,
            "ledger.json",
            AttemptLedger,
        )
        outcome_raw, outcome, outcome_identity = _read_record(
            root,
            "pilot_outcome.json",
            PilotOutcome,
        )
        supervisor_raw, supervisor, supervisor_identity = _read_record(
            root,
            "supervisor_receipt.json",
            PilotSupervisorReceipt,
        )
        stderr_raw, stderr_artifact, stderr_identity = _read_record(
            root,
            "worker_stderr.json",
            WorkerStderrArtifact,
        )
    except Exception as exc:
        if isinstance(exc, Phase4LocalQwenFreshIntegratedRevalidationError):
            raise
        raise _error("source runtime record validation failed") from exc
    if not all(
        isinstance(value, record_type)
        for value, record_type in (
            (load_receipt, LocalQwenLoadReceipt),
            (lease, PilotExecutionLease),
            (ledger, AttemptLedger),
            (outcome, PilotOutcome),
            (supervisor, PilotSupervisorReceipt),
            (stderr_artifact, WorkerStderrArtifact),
        )
    ):
        raise _error("source runtime record types drifted")

    load_receipt.validate_against(
        manifest=manifest,
        pilot=pilot,
        profile=profile,
    )
    _require(
        lease.manifest_identity,
        _identity(manifest.to_dict(), revision=PreCallManifest.SCHEMA_VERSION),
        "source lease/manifest identity drifted",
    )
    _require(
        lease.pilot_identity,
        _identity(pilot.to_dict(), revision=PilotBinding.SCHEMA_VERSION),
        "source lease/pilot identity drifted",
    )
    _require(lease.pilot_id, pilot.pilot_id, "source lease pilot drifted")
    _require(lease.case_id, pilot.case_binding["case_id"], "source lease case drifted")
    _require(lease.request_id, pilot.case_binding["request_id"], "source lease request drifted")

    _validate_preflight(
        preflight_raw,
        preflight,
        policy=policy,
        manifest=manifest,
        profile=profile,
        pilot=pilot,
    )
    source_result = _validate_source_result(
        result_raw,
        source_result,
        policy=policy,
        qualification=live_qualification,
        pilot=pilot,
    )

    if not isinstance(ledger, AttemptLedger) or not isinstance(outcome, PilotOutcome):
        raise _error("source ledger/outcome type drifted")
    _require(ledger.pilot_id, pilot.pilot_id, "source ledger pilot drifted")
    _require(ledger.case_id, pilot.case_binding["case_id"], "source ledger case drifted")
    _require(ledger.request_id, pilot.case_binding["request_id"], "source ledger request drifted")
    _require(ledger.status, "terminal", "source ledger status drifted")
    _require(ledger.integrated_run_count, 1, "source ledger integrated count drifted")
    _require(
        ledger.node_total_counts,
        {node_id: 1 for node_id in NODE_ORDER},
        "source ledger node counts drifted",
    )
    _require(outcome.pilot_id, pilot.pilot_id, "source outcome pilot drifted")
    _require(outcome.case_id, pilot.case_binding["case_id"], "source outcome case drifted")
    _require(outcome.request_id, pilot.case_binding["request_id"], "source outcome request drifted")
    _require(outcome.status, "integrated_failed_closed", "source outcome status drifted")
    _require(outcome.model_calls_performed, 4, "source outcome call count drifted")
    _require(outcome.model_action_occurred, True, "source outcome action state drifted")
    _require(outcome.integrated_outcome, "failed_closed", "source outcome terminal state drifted")
    _require(outcome.composition_status, "not_executed", "source outcome composition drifted")
    _require(outcome.assembler_status, "not_executed", "source outcome assembler drifted")
    _require(
        outcome.integrated_node_raw_contract_pass_count,
        3,
        "source outcome node pass count drifted",
    )
    _require(
        outcome.integrated_run_id,
        policy["run_id"],
        "source outcome run binding drifted",
    )
    if not isinstance(supervisor, PilotSupervisorReceipt):
        raise _error("source supervisor receipt type drifted")
    _require(supervisor.pilot_id, pilot.pilot_id, "source supervisor pilot drifted")
    _require(supervisor.case_id, pilot.case_binding["case_id"], "source supervisor case drifted")
    _require(supervisor.request_id, pilot.case_binding["request_id"], "source supervisor request drifted")
    _require(supervisor.terminal_status, "normal_completed", "source supervisor terminal status drifted")
    _require(supervisor.generation_started, True, "source supervisor generation state drifted")
    _require(supervisor.worker_exit_verified, True, "source worker teardown is unverified")
    _require(supervisor.raw_status, "captured", "source supervisor raw status drifted")
    _require(supervisor.retry_performed, False, "source supervisor retry state drifted")
    _require(
        supervisor.lease_identity,
        lease_identity,
        "source supervisor lease identity drifted",
    )
    _require(
        supervisor.pilot_outcome_identity,
        outcome_identity,
        "source supervisor outcome identity drifted",
    )
    _require(
        supervisor.stderr_identity,
        stderr_identity,
        "source supervisor stderr identity drifted",
    )

    attempts: dict[str, dict[str, object]] = {}
    for node_id in NODE_ORDER:
        attempts[node_id] = _validate_attempt(
            root,
            run_id=str(policy["run_id"]),
            node_id=node_id,
            pilot=pilot,
            manifest=manifest,
            policies=policies,
            profile=profile,
            load_receipt=load_receipt,
        )
    result_ids = [
        str(attempts[node_id]["attempt_result"].result_id)
        for node_id in NODE_ORDER
    ]
    _require(
        list(ledger.attempt_result_ids),
        result_ids,
        "source ledger attempt-result order drifted",
    )
    _require(
        supervisor.latest_attempt_result_identity,
        attempts["F4"]["attempt_result_identity"],
        "source supervisor latest attempt drifted",
    )

    for node_id in ("F1", "F2", "F3"):
        attempt = attempts[node_id]["attempt_result"]
        _require(attempt.raw_status, "captured_nonempty", f"{node_id} raw status drifted")
        _require(attempt.parse_status, "passed", f"{node_id} parse status drifted")
        _require(attempt.node_contract_status, "passed", f"{node_id} contract status drifted")
        _require(attempt.registry_status, "passed", f"{node_id} registry status drifted")
        _require(attempt.composition_status, "not_executed", f"{node_id} composition drifted")
        _require(attempt.assembler_status, "not_executed", f"{node_id} assembler drifted")
        _require(attempt.failure_code, None, f"{node_id} failure state drifted")
        _require(attempt.terminal, False, f"{node_id} terminal state drifted")
    f4_result = attempts["F4"]["attempt_result"]
    _require(f4_result.raw_status, "captured_nonempty", "F4 raw status drifted")
    _require(f4_result.parse_status, "failed", "F4 parse status drifted")
    _require(f4_result.node_contract_status, "failed", "F4 contract status drifted")
    _require(f4_result.registry_status, "not_run", "F4 registry status drifted")
    _require(f4_result.composition_status, "not_executed", "F4 composition drifted")
    _require(f4_result.assembler_status, "not_executed", "F4 assembler drifted")
    _require(f4_result.failure_code, "node_contract_invalid", "F4 failure code drifted")
    _require(f4_result.terminal, True, "F4 terminal state drifted")

    return {
        "root": root,
        "marker_raw": marker_raw,
        "marker": marker,
        "marker_identity": marker_identity,
        "policy_raw": _read_regular(root, _fresh.FRESH_INTEGRATED_POLICY_NAME),
        "policy": policy,
        "qualification_raw": _read_regular(root, _fresh.FRESH_INTEGRATED_QUALIFICATION_NAME),
        "qualification": live_qualification,
        "run_binding_raw": _read_regular(root, _fresh.FRESH_INTEGRATED_RUN_BINDING_NAME),
        "run_binding": run_binding,
        "preflight_raw": preflight_raw,
        "preflight": preflight,
        "result_raw": result_raw,
        "result": source_result,
        "manifest_raw": manifest_raw,
        "manifest": manifest,
        "manifest_identity": manifest_identity,
        "pilot": pilot,
        "policies": policies,
        "profile": profile,
        "load_raw": load_raw,
        "load_receipt": load_receipt,
        "load_identity": load_identity,
        "lease_raw": lease_raw,
        "lease": lease,
        "lease_identity": lease_identity,
        "ledger_raw": ledger_raw,
        "ledger": ledger,
        "ledger_identity": ledger_identity,
        "outcome_raw": outcome_raw,
        "outcome": outcome,
        "outcome_identity": outcome_identity,
        "supervisor_raw": supervisor_raw,
        "supervisor": supervisor,
        "supervisor_identity": supervisor_identity,
        "stderr_raw": stderr_raw,
        "stderr_artifact": stderr_artifact,
        "stderr_identity": stderr_identity,
        "attempts": attempts,
    }


def _qualification_b_input(
    qualification_savepoint_root: Path,
    qualification: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    root = _safe_existing_root(
        qualification_savepoint_root,
        "qualification savepoint root",
    )
    _, f3_state = _read_json(root, _integrated.INTEGRATED_F3_STATE_NAME)
    _, f4_state = _read_json(root, _integrated.INTEGRATED_F4_STATE_NAME)
    if f3_state.get("b_input") != f4_state.get("b_input"):
        raise _error("qualification canonical B binding drifted")
    b_input = f3_state.get("b_input")
    if not isinstance(b_input, dict):
        raise _error("qualification canonical B input is unavailable")
    # The owning graph facade checks B's historical insertion order while
    # canonical JSON serialization sorts object keys.  Preserve the exact
    # canonical B values and restore only the owning input-key order locally;
    # no semantic field is added, removed, or rewritten.
    b_input_keys = (
        "case_id",
        "request_id",
        "requirement",
        "requirement_summary",
        "target_device",
        "task_type",
        "constraints",
        "use_cases",
    )
    if set(b_input) != set(b_input_keys):
        raise _error("qualification canonical B input keys drifted")
    use_case_keys = (
        "use_case_id",
        "title",
        "actor",
        "goal",
        "expected_outcome",
    )
    use_cases = b_input["use_cases"]
    if type(use_cases) is not list:
        raise _error("qualification canonical B use cases are invalid")
    ordered_use_cases = []
    for row in use_cases:
        if not isinstance(row, Mapping) or set(row) != set(use_case_keys):
            raise _error("qualification canonical B use-case keys drifted")
        ordered_use_cases.append(
            {key: copy.deepcopy(row[key]) for key in use_case_keys}
        )
    ordered_b_input = {
        key: copy.deepcopy(b_input[key])
        for key in b_input_keys
    }
    ordered_b_input["use_cases"] = ordered_use_cases
    if f3_state.get("case_id") != qualification.get("case_id"):
        raise _error("qualification case binding drifted")
    if f3_state.get("request_id") != qualification.get("request_id"):
        raise _error("qualification request binding drifted")
    return ordered_b_input, copy.deepcopy(dict(f3_state))


def _parse_raw_output(raw: bytes, name: str) -> dict[str, object]:
    try:
        return _local._strict_json(raw, require_canonical=False)
    except Exception as exc:
        raise _error(f"{name} raw JSON cannot be parsed") from exc


def _output_identity(value: object, suffix: str) -> dict[str, object]:
    return _identity(
        value,
        revision=f"{REVALIDATION_ARTIFACT_REVISION}.{suffix}",
    )


def _source_identity_map(source: Mapping[str, object]) -> dict[str, object]:
    return {
        "source_root_marker": copy.deepcopy(source["marker_identity"]),
        "policy": _artifact_identity(
            source["policy_raw"],
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.source.policy",
        ),
        "qualification": _artifact_identity(
            source["qualification_raw"],
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.source.qualification",
        ),
        "run_binding": _artifact_identity(
            source["run_binding_raw"],
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.source.run_binding",
        ),
        "preflight": _artifact_identity(
            source["preflight_raw"],
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.source.preflight",
        ),
        "result": _artifact_identity(
            source["result_raw"],
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.source.result",
        ),
        "manifest": copy.deepcopy(source["manifest_identity"]),
        "load_receipt": copy.deepcopy(source["load_identity"]),
        "execution_lease": copy.deepcopy(source["lease_identity"]),
        "ledger": copy.deepcopy(source["ledger_identity"]),
        "pilot_outcome": copy.deepcopy(source["outcome_identity"]),
        "supervisor_receipt": copy.deepcopy(source["supervisor_identity"]),
        "worker_stderr": copy.deepcopy(source["stderr_identity"]),
    }


def _attempt_identity_map(source: Mapping[str, object]) -> dict[str, object]:
    return {
        node_id: {
            "pre_call": copy.deepcopy(source["attempts"][node_id]["pre_call_identity"]),
            "node_d17_action": copy.deepcopy(
                source["attempts"][node_id]["action_identity"]
            ),
            "attempt_result": copy.deepcopy(
                source["attempts"][node_id]["attempt_result_identity"]
            ),
            "raw": copy.deepcopy(source["attempts"][node_id]["raw_identity"]),
        }
        for node_id in NODE_ORDER
    }


def _new_root_marker() -> dict[str, object]:
    marker: dict[str, object] = {
        "schema_version": REVALIDATION_ROOT_MARKER_SCHEMA_VERSION,
        "marker_id": "pending",
        "result_kind": "zero_model_fresh_integrated_revalidation",
        "source_model_generate_calls": 4,
        "revalidation_model_generate_calls": 0,
        "downstream": "not_executed",
    }
    marker["marker_id"] = _identity(
        {key: value for key, value in marker.items() if key != "marker_id"},
        revision=REVALIDATION_ROOT_MARKER_SCHEMA_VERSION,
    )["sha256"]
    return marker


def _prepare_result_root(
    path: Path,
    *,
    source_root: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    target = _safe_new_root(path, source_root=source_root)
    try:
        target.mkdir()
    except OSError as exc:
        raise _error("revalidation result root cannot be created") from exc
    marker = _new_root_marker()
    marker_raw = _canonical(marker)
    _write_once(target, REVALIDATION_ROOT_MARKER, marker_raw)
    return (
        target,
        marker,
        _artifact_identity(
            marker_raw,
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.output.root_marker",
        ),
    )


def _build_revalidation(
    *,
    source: Mapping[str, object],
    qualification_savepoint_root: Path,
) -> dict[str, object]:
    b_input, qualification_state = _qualification_b_input(
        qualification_savepoint_root,
        source["qualification"],
    )
    empty_state = phase4_create_authority_state(b_input)
    empty_state_identity = _identity(
        empty_state,
        revision=_fresh.FRESH_INTEGRATED_EMPTY_AUTHORITY_REVISION,
    )
    replayed_state = empty_state
    for node_id in ("F1", "F2", "F3"):
        raw = source["attempts"][node_id]["raw"]
        output = _parse_raw_output(raw, node_id)
        replayed_state = phase4_register_node_output(
            replayed_state,
            node_id,
            output,
        )
    f3_state = copy.deepcopy(replayed_state)
    f3_registry_identity = copy.deepcopy(f3_state["registry_identities"]["F3"])

    f4_raw = source["attempts"]["F4"]["raw"]
    f4_output = _parse_raw_output(f4_raw, "F4")
    normalized_f4, core_receipt = phase4_normalize_and_validate_f4_output(
        raw_bytes=f4_raw,
        output=f4_output,
        state=replayed_state,
    )
    if core_receipt.get("normalizer_revision") != (
        _graph.F4_AUDIT_REF_OWNERSHIP_NORMALIZATION_REVISION
    ):
        raise _error("source F4 did not produce a P4-01b normalization receipt")
    phase4_validate_f4_p4_01b_normalization_receipt(
        core_receipt,
        raw_bytes=f4_raw,
        normalized_output=normalized_f4,
        state=replayed_state,
    )
    replayed_state = phase4_register_node_output(
        replayed_state,
        "F4",
        normalized_f4,
    )
    mapping = phase4_create_mapping(replayed_state)
    mapping_identity = _identity(
        mapping,
        revision=_graph.MAPPING_REVISION,
    )
    candidate = phase4_compose_candidate(replayed_state)
    _require(
        candidate.get("mapping_identity"),
        mapping_identity,
        "replayed mapping identity drifted into candidate composition",
    )
    try:
        candidate_bytes = base64.b64decode(
            candidate["model_semantic_candidate_canonical_b64"],
            validate=True,
        )
    except Exception as exc:
        raise _error("candidate composition bytes are invalid") from exc
    context, guidance = phase4_synthetic_assembler_bindings(replayed_state)
    try:
        assembled = phase4_assemble_candidate(
            candidate_bytes,
            context,
            guidance,
        )
    except Exception as exc:
        raise _error("replayed candidate composition or assembler failed") from exc
    page_spec = assembled.page_spec.to_dict()
    assembly_report = assembled.report.to_dict()

    output_artifacts = {
        REVALIDATION_ROOT_MARKER: None,
        REVALIDATION_NORMALIZED_F4_NAME: _output_identity(
            normalized_f4,
            "normalized_f4",
        ),
        REVALIDATION_CORE_RECEIPT_NAME: _output_identity(
            core_receipt,
            "core_receipt",
        ),
        REVALIDATION_CANDIDATE_NAME: _output_identity(
            candidate,
            "candidate",
        ),
        REVALIDATION_PAGE_SPEC_NAME: _output_identity(
            page_spec,
            "page_spec",
        ),
        REVALIDATION_ASSEMBLY_REPORT_NAME: _output_identity(
            assembly_report,
            "assembly_report",
        ),
    }
    return {
        "b_input": b_input,
        "qualification_state": qualification_state,
        "empty_state_identity": empty_state_identity,
        "replayed_state": replayed_state,
        "f3_registry_identity": f3_registry_identity,
        "normalized_f4": normalized_f4,
        "core_receipt": core_receipt,
        "mapping": mapping,
        "mapping_identity": mapping_identity,
        "candidate": candidate,
        "page_spec": page_spec,
        "assembly_report": assembly_report,
        "output_artifacts": output_artifacts,
    }


def _build_runtime_binding(
    *,
    source: Mapping[str, object],
    replay: Mapping[str, object],
    source_root: Path,
    qualification_root: Path,
    result_marker_identity: Mapping[str, object],
) -> dict[str, object]:
    output_artifacts = copy.deepcopy(replay["output_artifacts"])
    output_artifacts[REVALIDATION_ROOT_MARKER] = copy.deepcopy(
        result_marker_identity
    )
    binding: dict[str, object] = {
        "schema_version": REVALIDATION_RUNTIME_BINDING_SCHEMA_VERSION,
        "binding_id": "pending",
        "source_result_root": str(source_root),
        "qualification_savepoint_root": str(qualification_root),
        "source_root_marker_identity": copy.deepcopy(source["marker_identity"]),
        "source_artifact_identities": _source_identity_map(source),
        "source_attempt_identities": _attempt_identity_map(source),
        "pilot_id": source["pilot"].pilot_id,
        "run_id": source["policy"]["run_id"],
        "case_id": source["pilot"].case_binding["case_id"],
        "request_id": source["pilot"].case_binding["request_id"],
        "source_policy_identity": _source_identity_map(source)["policy"],
        "source_qualification_identity": _source_identity_map(source)["qualification"],
        "source_run_binding_identity": _source_identity_map(source)["run_binding"],
        "source_result_identity": _source_identity_map(source)["result"],
        "source_ledger_identity": _source_identity_map(source)["ledger"],
        "source_pilot_outcome_identity": _source_identity_map(source)["pilot_outcome"],
        "source_supervisor_receipt_identity": _source_identity_map(source)["supervisor_receipt"],
        "source_worker_stderr_identity": _source_identity_map(source)["worker_stderr"],
        "source_load_receipt_identity": _source_identity_map(source)["load_receipt"],
        "source_execution_lease_identity": _source_identity_map(source)["execution_lease"],
        "f3_cumulative_registry_identity": copy.deepcopy(
            replay["f3_registry_identity"]
        ),
        "mapping_identity": copy.deepcopy(replay["mapping_identity"]),
        "contract_identity": copy.deepcopy(
            replay["qualification_state"]["contract_identity"]
        ),
        "core_p4_01b_receipt_identity": _output_identity(
            replay["core_receipt"],
            "core_receipt",
        ),
        "source_model_generate_calls": 4,
        "revalidation_model_generate_calls": 0,
        "aggregate_budget_reset": False,
        "retry_count": 0,
        "normalization_counts_as_repair": False,
        "raw_model_contract_success": False,
        "normalized_node_contract_success": True,
        "source_result_immutable": True,
        "source_write_attempts": 0,
        "output_artifact_identities": output_artifacts,
        "result_root_marker_identity": copy.deepcopy(result_marker_identity),
        "downstream": {
            "consistency": "not_executed",
            "acceptance": "not_executed",
            "repair": "not_executed",
            "g0": "not_executed",
            "package": "not_executed",
            "production_route": "not_executed",
        },
    }
    binding["binding_id"] = _identity(
        {key: value for key, value in binding.items() if key != "binding_id"},
        revision=REVALIDATION_RUNTIME_BINDING_SCHEMA_VERSION,
    )["sha256"]
    return binding


def _build_result(
    *,
    source: Mapping[str, object],
    replay: Mapping[str, object],
    runtime_binding: Mapping[str, object],
    output_artifacts: Mapping[str, object],
    runtime_binding_identity: Mapping[str, object],
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": REVALIDATION_RESULT_SCHEMA_VERSION,
        "result_id": "pending",
        "status": "amended_normalized_fresh_integrated_revalidation_assembled",
        "source_result_status": "fresh_integrated_failed_closed",
        "pilot_id": source["pilot"].pilot_id,
        "run_id": source["policy"]["run_id"],
        "case_id": source["pilot"].case_binding["case_id"],
        "request_id": source["pilot"].case_binding["request_id"],
        "source_root_marker_identity": copy.deepcopy(source["marker_identity"]),
        "runtime_binding_identity": copy.deepcopy(runtime_binding_identity),
        "source_model_generate_calls": 4,
        "revalidation_model_generate_calls": 0,
        "model_generate_calls": 0,
        "aggregate_budget_reset": False,
        "retry_count": 0,
        "normalization_counts_as_repair": False,
        "raw_model_contract_success": False,
        "source_f1_f3_raw_contract_success": True,
        "source_f4_raw_contract_success": False,
        "normalized_node_contract_success": True,
        "agent_chain_system_output_usable": True,
        "integrated_outcome": "amended_normalized_assembled",
        "composition_status": "composed",
        "assembler_status": "assembled",
        "normalized_f4_node_output_identity": copy.deepcopy(
            output_artifacts[REVALIDATION_NORMALIZED_F4_NAME]
        ),
        "core_f4_normalization_receipt_identity": copy.deepcopy(
            output_artifacts[REVALIDATION_CORE_RECEIPT_NAME]
        ),
        "candidate_composition_identity": copy.deepcopy(
            output_artifacts[REVALIDATION_CANDIDATE_NAME]
        ),
        "assembled_page_spec_identity": copy.deepcopy(
            output_artifacts[REVALIDATION_PAGE_SPEC_NAME]
        ),
        "assembly_report_identity": copy.deepcopy(
            output_artifacts[REVALIDATION_ASSEMBLY_REPORT_NAME]
        ),
        "output_artifact_identities": {
            **copy.deepcopy(dict(output_artifacts)),
            REVALIDATION_RUNTIME_BINDING_NAME: copy.deepcopy(
                runtime_binding_identity
            ),
        },
        "downstream": {
            "consistency": "not_executed",
            "acceptance": "not_executed",
            "repair": "not_executed",
            "g0": "not_executed",
            "package": "not_executed",
            "production_route": "not_executed",
        },
        "historical_strict_result": "0/2_unchanged",
        "source_savepoint_is_not_integrated_input": True,
        "claim_boundary": (
            "amended_normalized_agent_chain_revalidation_only; "
            "not_strict_raw_model_first_pass; downstream_not_executed"
        ),
    }
    result["result_id"] = _identity(
        {key: value for key, value in result.items() if key != "result_id"},
        revision=REVALIDATION_RESULT_SCHEMA_VERSION,
    )["sha256"]
    return result


def validate_phase4_local_qwen_fresh_integrated_revalidation_result(
    result_root: Path,
) -> dict[str, object]:
    """Validate only the immutable revalidation result-root artifacts."""

    root = _safe_existing_root(result_root, "revalidation result root")
    names = tuple(
        path.name
        for path in root.iterdir()
        if path.is_file() and not path.is_symlink()
    )
    if set(names) != set(REVALIDATION_OUTPUT_NAMES):
        raise _error("revalidation result root contains unexpected artifacts")
    marker_raw, marker = _read_json(root, REVALIDATION_ROOT_MARKER)
    normalized_raw, normalized = _read_json(root, REVALIDATION_NORMALIZED_F4_NAME)
    receipt_raw, receipt = _read_json(root, REVALIDATION_CORE_RECEIPT_NAME)
    binding_raw, binding = _read_json(root, REVALIDATION_RUNTIME_BINDING_NAME)
    candidate_raw, candidate = _read_json(root, REVALIDATION_CANDIDATE_NAME)
    page_raw, page_spec = _read_json(root, REVALIDATION_PAGE_SPEC_NAME)
    report_raw, assembly_report = _read_json(root, REVALIDATION_ASSEMBLY_REPORT_NAME)
    result_raw, result = _read_json(root, REVALIDATION_RESULT_NAME)

    _require(marker["schema_version"], REVALIDATION_ROOT_MARKER_SCHEMA_VERSION, "result marker schema drifted")
    _require(
        marker["marker_id"],
        _identity(
            {key: value for key, value in marker.items() if key != "marker_id"},
            revision=REVALIDATION_ROOT_MARKER_SCHEMA_VERSION,
        )["sha256"],
        "result marker identity drifted",
    )
    _require(binding["schema_version"], REVALIDATION_RUNTIME_BINDING_SCHEMA_VERSION, "runtime binding schema drifted")
    _require(
        binding["binding_id"],
        _identity(
            {key: value for key, value in binding.items() if key != "binding_id"},
            revision=REVALIDATION_RUNTIME_BINDING_SCHEMA_VERSION,
        )["sha256"],
        "runtime binding identity drifted",
    )
    _require(result["schema_version"], REVALIDATION_RESULT_SCHEMA_VERSION, "revalidation result schema drifted")
    _require(
        result["result_id"],
        _identity(
            {key: value for key, value in result.items() if key != "result_id"},
            revision=REVALIDATION_RESULT_SCHEMA_VERSION,
        )["sha256"],
        "revalidation result identity drifted",
    )
    actual_artifacts = {
        REVALIDATION_ROOT_MARKER: _artifact_identity(
            marker_raw,
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.output.root_marker",
        ),
        REVALIDATION_NORMALIZED_F4_NAME: _artifact_identity(
            normalized_raw,
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.normalized_f4",
        ),
        REVALIDATION_CORE_RECEIPT_NAME: _artifact_identity(
            receipt_raw,
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.core_receipt",
        ),
        REVALIDATION_CANDIDATE_NAME: _artifact_identity(
            candidate_raw,
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.candidate",
        ),
        REVALIDATION_PAGE_SPEC_NAME: _artifact_identity(
            page_raw,
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.page_spec",
        ),
        REVALIDATION_ASSEMBLY_REPORT_NAME: _artifact_identity(
            report_raw,
            revision=f"{REVALIDATION_ARTIFACT_REVISION}.assembly_report",
        ),
        REVALIDATION_RUNTIME_BINDING_NAME: _artifact_identity(
            binding_raw,
            revision=REVALIDATION_RUNTIME_BINDING_SCHEMA_VERSION,
        ),
    }
    _require(
        binding["output_artifact_identities"],
        {
            **{
                key: value
                for key, value in actual_artifacts.items()
                if key != REVALIDATION_RUNTIME_BINDING_NAME
            },
        },
        "runtime binding output identities drifted",
    )
    _require(
        result["output_artifact_identities"],
        actual_artifacts,
        "result output identities drifted",
    )
    _require(
        result["runtime_binding_identity"],
        actual_artifacts[REVALIDATION_RUNTIME_BINDING_NAME],
        "result/runtime binding identity drifted",
    )
    for key, expected in (
        ("source_model_generate_calls", 4),
        ("revalidation_model_generate_calls", 0),
        ("model_generate_calls", 0),
        ("aggregate_budget_reset", False),
        ("retry_count", 0),
        ("normalization_counts_as_repair", False),
        ("raw_model_contract_success", False),
        ("normalized_node_contract_success", True),
        ("agent_chain_system_output_usable", True),
        ("status", "amended_normalized_fresh_integrated_revalidation_assembled"),
        ("composition_status", "composed"),
        ("assembler_status", "assembled"),
        ("historical_strict_result", "0/2_unchanged"),
    ):
        _require(result.get(key), expected, f"revalidation result drifted at {key}")
    _require(
        binding["source_model_generate_calls"],
        4,
        "runtime binding source call count drifted",
    )
    _require(
        binding["revalidation_model_generate_calls"],
        0,
        "runtime binding revalidation call count drifted",
    )
    return copy.deepcopy(result)


def revalidate_phase4_local_qwen_fresh_integrated(
    *,
    source_result_root: Path,
    qualification_savepoint_root: Path,
    result_root: Path,
    confirm_no_model_revalidation: bool,
) -> dict[str, object]:
    """Revalidate a terminal fresh-integrated result with zero model calls."""

    if confirm_no_model_revalidation is not True:
        raise _error("confirm_no_model_revalidation is required")
    source_root = _safe_existing_root(source_result_root, "source result root")
    qualification_root = _safe_existing_root(
        qualification_savepoint_root,
        "qualification savepoint root",
    )
    _safe_new_root(result_root, source_root=source_root)
    source = _validate_source_root(
        source_root,
        qualification_savepoint_root=qualification_root,
    )
    replay = _build_revalidation(
        source=source,
        qualification_savepoint_root=qualification_root,
    )
    target, marker, marker_identity = _prepare_result_root(
        result_root,
        source_root=source_root,
    )
    output_artifacts = copy.deepcopy(replay["output_artifacts"])
    output_artifacts[REVALIDATION_ROOT_MARKER] = marker_identity
    _write_once(
        target,
        REVALIDATION_NORMALIZED_F4_NAME,
        _canonical(replay["normalized_f4"]),
    )
    _write_once(
        target,
        REVALIDATION_CORE_RECEIPT_NAME,
        _canonical(replay["core_receipt"]),
    )
    _write_once(target, REVALIDATION_CANDIDATE_NAME, _canonical(replay["candidate"]))
    _write_once(target, REVALIDATION_PAGE_SPEC_NAME, _canonical(replay["page_spec"]))
    _write_once(
        target,
        REVALIDATION_ASSEMBLY_REPORT_NAME,
        _canonical(replay["assembly_report"]),
    )
    runtime_binding = _build_runtime_binding(
        source=source,
        replay=replay,
        source_root=source_root,
        qualification_root=qualification_root,
        result_marker_identity=marker_identity,
    )
    runtime_binding_identity = _identity(
        runtime_binding,
        revision=REVALIDATION_RUNTIME_BINDING_SCHEMA_VERSION,
    )
    _write_once(
        target,
        REVALIDATION_RUNTIME_BINDING_NAME,
        _canonical(runtime_binding),
    )
    result = _build_result(
        source=source,
        replay=replay,
        runtime_binding=runtime_binding,
        output_artifacts=output_artifacts,
        runtime_binding_identity=runtime_binding_identity,
    )
    _write_once(target, REVALIDATION_RESULT_NAME, _canonical(result))
    validated = validate_phase4_local_qwen_fresh_integrated_revalidation_result(
        target
    )
    if validated != result:
        raise _error("revalidation result changed during final live validation")
    return copy.deepcopy(result)


__all__ = [
    "REVALIDATION_ROOT_MARKER",
    "REVALIDATION_NORMALIZED_F4_NAME",
    "REVALIDATION_CORE_RECEIPT_NAME",
    "REVALIDATION_RUNTIME_BINDING_NAME",
    "REVALIDATION_CANDIDATE_NAME",
    "REVALIDATION_PAGE_SPEC_NAME",
    "REVALIDATION_ASSEMBLY_REPORT_NAME",
    "REVALIDATION_RESULT_NAME",
    "REVALIDATION_RESULT_SCHEMA_VERSION",
    "REVALIDATION_RUNTIME_BINDING_SCHEMA_VERSION",
    "Phase4LocalQwenFreshIntegratedRevalidationError",
    "revalidate_phase4_local_qwen_fresh_integrated",
    "validate_phase4_local_qwen_fresh_integrated_revalidation_result",
]
