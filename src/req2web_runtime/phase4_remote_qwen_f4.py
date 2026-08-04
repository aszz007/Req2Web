"""P4-03 F4 no-model preparation from the validated F3 savepoint."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Mapping, NamedTuple

from req2web_runtime import phase4_local_qwen as _local
from req2web_runtime import phase4_remote_qwen as _f3


Phase4RemoteQwenF4ContractError = _f3.Phase4RemoteQwenContractError
_CanonicalRecord = _local._CanonicalRecord
_canonical_bytes = _local._canonical_bytes
_strict_json = _local._strict_json
_exact = _local._exact
_identity = _local._identity
_sha256 = _local._sha256
_read_input_file = _f3._read_input_file
_write_once = _f3._write_remote_once
_offline_process = _f3._offline_process


F4_SCHEMA_PREFIX = "req2web.phase4.p4_03d4.f4"
F4_DIAGNOSTIC_ID = "p4-03d4-remote-qwen-9b-bf16-f4-preparation"
F4_PILOT_ID = "p4-03d4-remote-qwen-9b-bf16"
F4_CASE_ID = _f3.REMOTE_CASE_ID
F4_REQUEST_ID = _f3.REMOTE_REQUEST_ID
F4_NODE_ID = "F4"
F4_POLICY_RELATIVE_PATH = "docs/phase4_remote_qwen_bf16_f4_preflight_policy.json"
F4_POLICY_PATH = Path(__file__).resolve().parents[2] / F4_POLICY_RELATIVE_PATH
F4_POLICY_SCHEMA_VERSION = f"{F4_SCHEMA_PREFIX}.policy.v1"
F4_BINDING_SCHEMA_VERSION = f"{F4_SCHEMA_PREFIX}.f3_savepoint_binding.v1"
F4_MANIFEST_SCHEMA_VERSION = f"{F4_SCHEMA_PREFIX}.manifest.v1"
F4_RESULT_SCHEMA_VERSION = f"{F4_SCHEMA_PREFIX}.result.v1"
F4_INPUT_REVISION = f"{F4_SCHEMA_PREFIX}.input.v1"
F4_PROMPT_REVISION = f"{F4_SCHEMA_PREFIX}.prompt.v1"
F4_CONFIG_REVISION = f"{F4_SCHEMA_PREFIX}.config.v1"
F4_REQUEST_REVISION = f"{F4_SCHEMA_PREFIX}.request.v1"

F4_ROOT_MARKER_NAME = ".req2web-phase4-p4-03d4-f4-preflight-result-root"
F4_POLICY_COPY_NAME = "f4_preflight_policy.json"
F4_SAVEPOINT_BINDING_NAME = "f3_savepoint_binding.json"
F4_INPUT_NAME = "f4_input.json"
F4_PROMPT_NAME = "f4_prompt.json"
F4_CONFIG_NAME = "f4_runtime_config.json"
F4_REQUEST_NAME = "f4_request.json"
F4_MANIFEST_NAME = "f4_preflight_manifest.json"
F4_RESULT_NAME = "f4_preflight_result.json"

F3_REVALIDATION_RESULT_SHA256 = (
    "sha256:5ced86718d5cb2b695586461af0d6cfb247a7bae9504d25307fe1094ff9f4085"
)
F3_REVALIDATED_OUTPUT_SHA256 = (
    "sha256:3cd72791d99a49dc541f23a425e80b781eb953dfe4a1b4816a0a18d7ff06ac21"
)
F3_REVALIDATED_STATE_SHA256 = (
    "sha256:8a6a48c67f7bafb7f3688352ab65badf7410eab0d7b3cfa2d8dc3843a8776870"
)
F3_REVALIDATION_ID = (
    "sha256:948a254520d3a8a5470e0c47b34e3034a21dfbdc5af01351cb49cdd92531b66f"
)
F3_SOURCE_RAW_SHA256 = (
    "sha256:079a40c5ccb572060b52b9dd703b33512a5d2da67aa4829a8813d4a181b6c2ac"
)

F4_ALLOWED_CATEGORIES = [
    "canonical_b_input",
    "validated_F1_output",
    "validated_F2_output",
    "validated_F3_output",
    "deterministic_registry",
    "deterministic_mapping",
]
F4_PROHIBITED_CATEGORIES = [
    "b_aux_sidecar",
    "retrieval_evidence",
    "h1_gold",
    "browser_evidence",
    "hidden_reasoning",
]
F4_FIELD_CAPS = {
    "input_bytes": 131072,
    "output_bytes": 65536,
    "prompt_bytes": 16384,
    "config_bytes": 8192,
    "request_bytes": 8192,
    "ref_count": 32,
}


def _remote_action_state() -> dict[str, object]:
    return {
        "model_action": False,
        "graph_runtime_execution": False,
        "dependency_installation": False,
        "training": False,
        "data_authoring": False,
        "remote_action": False,
        "network": False,
        "telemetry": False,
        "tracing": False,
    }


class F4PreflightPolicy(_CanonicalRecord):
    KEYS = (
        "schema_version",
        "policy_id",
        "diagnostic_id",
        "pilot_id",
        "case",
        "savepoint",
        "node",
        "execution",
        "claim_boundaries",
        "action_state",
    )
    SCHEMA_VERSION = F4_POLICY_SCHEMA_VERSION

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="F4PreflightPolicy")
        _local._sha(data["policy_id"], "F4PreflightPolicy.policy_id")
        if (
            data["diagnostic_id"] != F4_DIAGNOSTIC_ID
            or data["pilot_id"] != F4_PILOT_ID
            or data["case"]
            != {
                "case_id": F4_CASE_ID,
                "request_id": F4_REQUEST_ID,
                "node_id": F4_NODE_ID,
                "model_id": _f3.REMOTE_MODEL_ID,
                "model_revision": _f3.REMOTE_MODEL_REVISION,
            }
        ):
            raise Phase4RemoteQwenF4ContractError("F4 policy identity drifted")
        savepoint = _exact(
            data["savepoint"],
            (
                "revalidation_result_sha256",
                "revalidated_output_sha256",
                "revalidated_state_sha256",
                "revalidation_id",
                "source_raw_sha256",
                "requires_live_f3_registry_replay",
                "receipt_is_not_authority",
            ),
            "F4PreflightPolicy.savepoint",
        )
        if savepoint != {
            "revalidation_result_sha256": F3_REVALIDATION_RESULT_SHA256,
            "revalidated_output_sha256": F3_REVALIDATED_OUTPUT_SHA256,
            "revalidated_state_sha256": F3_REVALIDATED_STATE_SHA256,
            "revalidation_id": F3_REVALIDATION_ID,
            "source_raw_sha256": F3_SOURCE_RAW_SHA256,
            "requires_live_f3_registry_replay": True,
            "receipt_is_not_authority": True,
        }:
            raise Phase4RemoteQwenF4ContractError("F4 savepoint policy drifted")
        node = _exact(
            data["node"],
            (
                "node_id",
                "allowed_categories",
                "prohibited_categories",
                "field_caps",
                "upstream_required_node_ids",
                "b_aux_disposition",
                "model_output_format",
            ),
            "F4PreflightPolicy.node",
        )
        if node != {
            "node_id": F4_NODE_ID,
            "allowed_categories": F4_ALLOWED_CATEGORIES,
            "prohibited_categories": F4_PROHIBITED_CATEGORIES,
            "field_caps": F4_FIELD_CAPS,
            "upstream_required_node_ids": ["F1", "F2", "F3"],
            "b_aux_disposition": "absent/not_requested",
            "model_output_format": "exact_json_object",
        }:
            raise Phase4RemoteQwenF4ContractError("F4 node policy drifted")
        execution = _exact(
            data["execution"],
            (
                "model_action",
                "generate_calls",
                "automatic_retry",
                "prompt_or_config_revision",
                "f4_model_invocation",
                "composition",
                "assembler",
                "acceptance",
                "repair",
                "g0",
            ),
            "F4PreflightPolicy.execution",
        )
        if execution != {
            "model_action": False,
            "generate_calls": 0,
            "automatic_retry": False,
            "prompt_or_config_revision": "preparation_only",
            "f4_model_invocation": "not_executed",
            "composition": "not_executed",
            "assembler": "not_executed",
            "acceptance": "not_executed",
            "repair": "not_executed",
            "g0": "not_executed",
        }:
            raise Phase4RemoteQwenF4ContractError("F4 execution policy drifted")
        claims = _exact(
            data["claim_boundaries"],
            ("formal_quality", "h1_or_gold", "training", "data_authoring"),
            "F4PreflightPolicy.claim_boundaries",
        )
        if any(value is not False for value in claims.values()):
            raise Phase4RemoteQwenF4ContractError("F4 claim boundary drifted")
        if data["action_state"] != _remote_action_state():
            raise Phase4RemoteQwenF4ContractError("F4 action state drifted")
        expected = _identity(
            {key: value for key, value in data.items() if key != "policy_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["policy_id"] != expected:
            raise Phase4RemoteQwenF4ContractError("F4 policy identity invalid")


def load_f4_preflight_policy() -> tuple[F4PreflightPolicy, bytes]:
    raw = _local._read_tracked_canonical_record(F4_POLICY_PATH, "F4 preflight policy")
    return F4PreflightPolicy.from_bytes(raw), raw


class F4SavepointBinding(_CanonicalRecord):
    KEYS = (
        "schema_version",
        "binding_id",
        "diagnostic_id",
        "pilot_id",
        "case_id",
        "request_id",
        "node_id",
        "revalidation_result_identity",
        "revalidated_output_identity",
        "revalidated_state_identity",
        "source_raw_identity",
        "revalidation_id",
        "authority_state_replayed",
        "authority_state_matches_savepoint",
        "action_state",
    )
    SCHEMA_VERSION = F4_BINDING_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        result_raw: bytes,
        output_raw: bytes,
        state_raw: bytes,
        source_raw: bytes,
        revalidation: _f3.RemoteRevalidationRecord,
        state_replayed: Mapping[str, object],
    ) -> "F4SavepointBinding":
        result_identity = _identity(
            result_raw,
            revision=f"{_f3.REMOTE_SCHEMA_PREFIX}.result_file.v1",
            identity_kind="raw_bytes",
        )
        output_identity = _identity(
            output_raw,
            revision=f"{_f3.REMOTE_SCHEMA_PREFIX}.revalidated_f3_output.v1",
            identity_kind="raw_bytes",
        )
        state_identity = _identity(
            state_raw,
            revision=f"{_f3.REMOTE_SCHEMA_PREFIX}.revalidated_f3_state.v1",
            identity_kind="raw_bytes",
        )
        source_raw_identity = _identity(
            source_raw,
            revision=_f3.REMOTE_RAW_IDENTITY_REVISION,
            identity_kind="raw_bytes",
        )
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "binding_id": "pending",
            "diagnostic_id": F4_DIAGNOSTIC_ID,
            "pilot_id": F4_PILOT_ID,
            "case_id": F4_CASE_ID,
            "request_id": F4_REQUEST_ID,
            "node_id": F4_NODE_ID,
            "revalidation_result_identity": result_identity,
            "revalidated_output_identity": output_identity,
            "revalidated_state_identity": state_identity,
            "source_raw_identity": source_raw_identity,
            "revalidation_id": revalidation.revalidation_id,
            "authority_state_replayed": True,
            "authority_state_matches_savepoint": _canonical_bytes(state_replayed)
            == state_raw,
            "action_state": _remote_action_state(),
        }
        root["binding_id"] = _identity(
            {key: value for key, value in root.items() if key != "binding_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="F4SavepointBinding")
        _local._sha(data["binding_id"], "F4SavepointBinding.binding_id")
        if (
            data["diagnostic_id"] != F4_DIAGNOSTIC_ID
            or data["pilot_id"] != F4_PILOT_ID
            or data["case_id"] != F4_CASE_ID
            or data["request_id"] != F4_REQUEST_ID
            or data["node_id"] != F4_NODE_ID
            or data["authority_state_replayed"] is not True
            or data["authority_state_matches_savepoint"] is not True
            or data["revalidation_id"] != F3_REVALIDATION_ID
            or data["action_state"] != _remote_action_state()
        ):
            raise Phase4RemoteQwenF4ContractError("F4 savepoint binding drifted")
        for key in (
            "revalidation_result_identity",
            "revalidated_output_identity",
            "revalidated_state_identity",
            "source_raw_identity",
        ):
            _local._validate_identity(data[key], f"F4SavepointBinding.{key}")
        if (
            data["revalidation_result_identity"]["sha256"]
            != F3_REVALIDATION_RESULT_SHA256
            or data["revalidated_output_identity"]["sha256"]
            != F3_REVALIDATED_OUTPUT_SHA256
            or data["revalidated_state_identity"]["sha256"]
            != F3_REVALIDATED_STATE_SHA256
            or data["source_raw_identity"]["sha256"] != F3_SOURCE_RAW_SHA256
        ):
            raise Phase4RemoteQwenF4ContractError(
                "F4 savepoint identity values drifted"
            )
        expected = _identity(
            {key: value for key, value in data.items() if key != "binding_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["binding_id"] != expected:
            raise Phase4RemoteQwenF4ContractError("F4 savepoint binding identity drifted")


class F4PreparedExperiment(NamedTuple):
    result_root: Path
    policy: F4PreflightPolicy
    policy_raw: bytes
    savepoint: F4SavepointBinding
    profile: _f3.RemoteQwenProfile
    input_bytes: bytes
    prompt_bytes: bytes
    config_bytes: bytes
    request_bytes: bytes
    manifest_bytes: bytes


def _f4_projection_policy() -> _local.NodeProjectionPolicy:
    return _local.NodeProjectionPolicy.create(
        node_id=F4_NODE_ID,
        allowed_categories=F4_ALLOWED_CATEGORIES,
        prohibited_categories=F4_PROHIBITED_CATEGORIES,
        field_caps=F4_FIELD_CAPS,
        upstream_required_node_ids=["F1", "F2", "F3"],
        projection_revision=F4_INPUT_REVISION,
        prompt_template_revision=F4_PROMPT_REVISION,
        config_revision=F4_CONFIG_REVISION,
    )


def _build_f4_prompt(*, input_bytes: bytes, policy: _local.NodeProjectionPolicy) -> bytes:
    payload = {
        "prompt_schema_version": F4_PROMPT_REVISION,
        "node_id": F4_NODE_ID,
        "template_revision": F4_PROMPT_REVISION,
        "output_format": "exact_json_object",
        "input_sha256": _sha256(input_bytes),
        "input_byte_length": len(input_bytes),
        "instructions": list(_local._NODE_PROMPT_GUIDANCE[F4_NODE_ID])
        + [
            "Return one JSON object only; do not emit markdown, commentary, or a second object.",
            "Do not claim final Acceptance, browser evidence, repair, G0, or production success.",
        ],
        "output_contract": copy.deepcopy(_local._NODE_OUTPUT_CONTRACTS[F4_NODE_ID]),
        "model_id": _f3.REMOTE_MODEL_ID,
        "model_revision": _f3.REMOTE_MODEL_REVISION,
        "projection_revision": policy.projection_revision,
    }
    raw = _canonical_bytes(payload)
    if len(raw) > F4_FIELD_CAPS["prompt_bytes"]:
        raise Phase4RemoteQwenF4ContractError("F4 prompt exceeds cap")
    return raw


def _build_f4_config(*, profile: _f3.RemoteQwenProfile) -> bytes:
    return _canonical_bytes(
        {
            "config_schema_version": F4_CONFIG_REVISION,
            "node_id": F4_NODE_ID,
            "profile_identity": _identity(
                profile.to_dict(), revision=_f3.REMOTE_PROFILE_SCHEMA_VERSION
            ),
            "dtype": profile.dtype,
            "quantization": profile.quantization,
            "compute_dtype": profile.compute_dtype,
            "device_map": profile.device_map,
            "cpu_offload": profile.cpu_offload,
            "model_context_tokens": profile.model_context_tokens,
            "fixed_max_new_tokens": None,
            "generation_stop_policy": copy.deepcopy(
                _f3.REMOTE_GENERATION_STOP_POLICY
            ),
            "generate_calls": 0,
            "model_action": False,
        }
    )


def _build_f4_request() -> bytes:
    return _canonical_bytes(
        {
            "request_schema_version": F4_REQUEST_REVISION,
            "diagnostic_id": F4_DIAGNOSTIC_ID,
            "pilot_id": F4_PILOT_ID,
            "case_id": F4_CASE_ID,
            "request_id": F4_REQUEST_ID,
            "node_id": F4_NODE_ID,
            "model_id": _f3.REMOTE_MODEL_ID,
            "model_revision": _f3.REMOTE_MODEL_REVISION,
            "call_kind": "f4_preparation_only",
            "generate_call_index": 0,
            "generate_call_cap": 0,
            "retry_count": 0,
        }
    )


def _replay_f3_savepoint(
    *, f3_result_root: Path
) -> tuple[
    bytes,
    bytes,
    bytes,
    bytes,
    _f3.RemoteRevalidationRecord,
    _f3.RemoteQwenProfile,
    Mapping[str, object],
    _f3.RemoteCheckpointReplay,
]:
    result_raw = _read_input_file(
        f3_result_root / _f3.REMOTE_REVALIDATION_NAME, "F3 revalidation result"
    )
    output_raw = _read_input_file(
        f3_result_root / _f3.REMOTE_REVALIDATED_OUTPUT_NAME, "F3 revalidated output"
    )
    state_raw = _read_input_file(
        f3_result_root / _f3.REMOTE_REVALIDATED_STATE_NAME, "F3 revalidated state"
    )
    source_raw = _read_input_file(
        f3_result_root / _f3.REMOTE_RAW_NAME, "F3 source raw"
    )
    if (
        _sha256(result_raw) != F3_REVALIDATION_RESULT_SHA256
        or _sha256(output_raw) != F3_REVALIDATED_OUTPUT_SHA256
        or _sha256(state_raw) != F3_REVALIDATED_STATE_SHA256
        or _sha256(source_raw) != F3_SOURCE_RAW_SHA256
    ):
        raise Phase4RemoteQwenF4ContractError("F3 savepoint file hash drifted")
    revalidation = _f3.RemoteRevalidationRecord.from_bytes(result_raw)
    if (
        revalidation.revalidation_id != F3_REVALIDATION_ID
        or revalidation.node_model_pass is not True
        or revalidation.revalidation_generate_calls != 0
    ):
        raise Phase4RemoteQwenF4ContractError("F3 savepoint status drifted")
    output = _strict_json(output_raw, require_canonical=True)
    saved_state = _strict_json(state_raw, require_canonical=True)
    checkpoint = _f3.replay_remote_checkpoint(
        checkpoint_packet=f3_result_root / _f3.REMOTE_CHECKPOINT_PACKET_NAME,
        checkpoint_receipt=f3_result_root / _f3.REMOTE_CHECKPOINT_RECEIPT_NAME,
        prior_f3_failure=f3_result_root / _f3.REMOTE_PRIOR_FAILURE_NAME,
    )
    from req2web_orchestration.phase4_graph import (
        phase4_register_node_output,
        phase4_validate_node_output,
    )

    validated = phase4_validate_node_output(
        "F3", output, checkpoint.authority_state
    )
    state = phase4_register_node_output(
        checkpoint.authority_state, "F3", validated
    )
    if _canonical_bytes(state) != state_raw:
        raise Phase4RemoteQwenF4ContractError(
            "F3 live registry replay differs from saved authority state"
        )
    profile = _f3.RemoteQwenProfile.from_bytes(
        _read_input_file(f3_result_root / _f3.REMOTE_PROFILE_NAME, "F3 profile")
    )
    return (
        result_raw,
        output_raw,
        state_raw,
        source_raw,
        revalidation,
        profile,
        state,
        checkpoint,
    )


def prepare_phase4_remote_qwen_f4(
    *, f3_result_root: Path, result_root: Path
) -> F4PreparedExperiment:
    _offline_process()
    if (
        not isinstance(f3_result_root, Path)
        or not f3_result_root.is_absolute()
        or not f3_result_root.is_dir()
        or f3_result_root.is_symlink()
    ):
        raise Phase4RemoteQwenF4ContractError("F3 result root is unsafe")
    if not isinstance(result_root, Path) or not result_root.is_absolute():
        raise Phase4RemoteQwenF4ContractError("F4 result root must be absolute")
    if result_root.exists():
        raise Phase4RemoteQwenF4ContractError("F4 result root already exists")
    if (
        not result_root.parent.is_dir()
        or result_root.parent.is_symlink()
    ):
        raise Phase4RemoteQwenF4ContractError("F4 result parent is unsafe")
    policy, policy_raw = load_f4_preflight_policy()
    result_root.mkdir(parents=False)
    marker = f"{F4_ROOT_MARKER_NAME}:{_identity(str(result_root), revision=F4_SCHEMA_PREFIX)['sha256']}"
    _write_once(result_root, F4_ROOT_MARKER_NAME, marker.encode("utf-8"))
    (
        result_raw,
        output_raw,
        state_raw,
        source_raw,
        revalidation,
        profile,
        state,
        checkpoint,
    ) = _replay_f3_savepoint(f3_result_root=f3_result_root)
    savepoint = F4SavepointBinding.create(
        result_raw=result_raw,
        output_raw=output_raw,
        state_raw=state_raw,
        source_raw=source_raw,
        revalidation=revalidation,
        state_replayed=state,
    )
    from req2web_orchestration.phase4_graph import (
        phase4_create_mapping,
        synthetic_commerce_b_input,
        validate_b_input,
    )

    b_input = validate_b_input(synthetic_commerce_b_input())
    mapping = phase4_create_mapping(state)
    input_bytes = _local.derive_node_input(
        node_id=F4_NODE_ID,
        b_input_bytes=_canonical_bytes(b_input),
        upstream_outputs={
            **checkpoint.outputs,
            "F3": _canonical_bytes(_strict_json(output_raw, require_canonical=True)),
        },
        authority_state=state,
        policy=_f4_projection_policy(),
    )
    prompt_bytes = _build_f4_prompt(
        input_bytes=input_bytes, policy=_f4_projection_policy()
    )
    config_bytes = _build_f4_config(profile=profile)
    request_bytes = _build_f4_request()
    manifest = _canonical_bytes(
        {
            "schema_version": F4_MANIFEST_SCHEMA_VERSION,
            "diagnostic_id": F4_DIAGNOSTIC_ID,
            "pilot_id": F4_PILOT_ID,
            "case_id": F4_CASE_ID,
            "request_id": F4_REQUEST_ID,
            "node_id": F4_NODE_ID,
            "policy_identity": _identity(
                policy.to_dict(), revision=F4_POLICY_SCHEMA_VERSION
            ),
            "savepoint_binding_identity": _identity(
                savepoint.to_dict(), revision=F4_BINDING_SCHEMA_VERSION
            ),
            "mapping_identity": _identity(
                mapping, revision="req2web.phase4.mapping.p4_02a.v1"
            ),
            "input_identity": _identity(
                input_bytes, revision=F4_INPUT_REVISION, identity_kind="raw_bytes"
            ),
            "prompt_identity": _identity(
                prompt_bytes, revision=F4_PROMPT_REVISION, identity_kind="raw_bytes"
            ),
            "config_identity": _identity(
                config_bytes, revision=F4_CONFIG_REVISION, identity_kind="raw_bytes"
            ),
            "request_identity": _identity(
                request_bytes, revision=F4_REQUEST_REVISION, identity_kind="raw_bytes"
            ),
            "execution": {
                "model_action": False,
                "generate_calls": 0,
                "f4_model_invocation": "not_executed",
                "composition": "not_executed",
                "assembler": "not_executed",
                "acceptance": "not_executed",
                "repair": "not_executed",
                "g0": "not_executed",
            },
            "action_state": _remote_action_state(),
        }
    )
    result = _canonical_bytes(
        {
            "schema_version": F4_RESULT_SCHEMA_VERSION,
            "diagnostic_id": F4_DIAGNOSTIC_ID,
            "pilot_id": F4_PILOT_ID,
            "node_id": F4_NODE_ID,
            "status": "preflight_prepared_no_model",
            "model_action": False,
            "generate_calls": 0,
            "savepoint_binding_identity": _identity(
                savepoint.to_dict(), revision=F4_BINDING_SCHEMA_VERSION
            ),
            "manifest_identity": _identity(
                manifest,
                revision=F4_MANIFEST_SCHEMA_VERSION,
                identity_kind="raw_bytes",
            ),
            "action_state": _remote_action_state(),
        }
    )
    for name, raw in (
        (F4_POLICY_COPY_NAME, policy_raw),
        (F4_SAVEPOINT_BINDING_NAME, savepoint.canonical_bytes()),
        (F4_INPUT_NAME, input_bytes),
        (F4_PROMPT_NAME, prompt_bytes),
        (F4_CONFIG_NAME, config_bytes),
        (F4_REQUEST_NAME, request_bytes),
        (F4_MANIFEST_NAME, manifest),
        (F4_RESULT_NAME, result),
    ):
        _write_once(result_root, name, raw)
    return F4PreparedExperiment(
        result_root=result_root,
        policy=policy,
        policy_raw=policy_raw,
        savepoint=savepoint,
        profile=profile,
        input_bytes=input_bytes,
        prompt_bytes=prompt_bytes,
        config_bytes=config_bytes,
        request_bytes=request_bytes,
        manifest_bytes=manifest,
    )


__all__ = [
    "F4PreflightPolicy",
    "F4SavepointBinding",
    "F4PreparedExperiment",
    "Phase4RemoteQwenF4ContractError",
    "load_f4_preflight_policy",
    "prepare_phase4_remote_qwen_f4",
]
