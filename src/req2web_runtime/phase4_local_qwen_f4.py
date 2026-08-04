"""Local Qwen F4 bounded runner."""

from __future__ import annotations

import copy
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple

from req2web_runtime import phase4_local_qwen as _local
from req2web_runtime import phase4_remote_qwen as _f3
from req2web_runtime import phase4_remote_qwen_f4 as _remote_f4


F4_LOCAL_SCHEMA_PREFIX = "req2web.phase4.p4_03d4.local_f4"
F4_LOCAL_POLICY_SCHEMA_VERSION = f"{F4_LOCAL_SCHEMA_PREFIX}.policy.v3"
F4_LOCAL_MANIFEST_SCHEMA_VERSION = f"{F4_LOCAL_SCHEMA_PREFIX}.manifest.v1"
F4_LOCAL_PRE_CALL_SCHEMA_VERSION = f"{F4_LOCAL_SCHEMA_PREFIX}.pre_call.v1"
F4_LOCAL_RESULT_SCHEMA_VERSION = f"{F4_LOCAL_SCHEMA_PREFIX}.result.v1"
F4_LOCAL_DIAGNOSTIC_ID = "p4-03d4-local-qwen-9b-f4"
F4_LOCAL_PILOT_ID = "p4-03d4-local-qwen-9b-recovery-20260804"
F4_LOCAL_NODE_ID = "F4"
F4_LOCAL_CASE_ID = _f3.REMOTE_CASE_ID
F4_LOCAL_REQUEST_ID = _f3.REMOTE_REQUEST_ID
F4_LOCAL_POLICY_PATH = Path(__file__).resolve().parents[2] / "docs/phase4_local_qwen_f4_policy.json"
F4_LOCAL_ROOT_MARKER = ".req2web-phase4-p4-03d4-local-f4-result-root"
F4_LOCAL_POLICY_COPY = "f4_local_qwen_policy.json"
F4_LOCAL_PROFILE_NAME = "f4_local_qwen_profile.json"
F4_LOCAL_PRIOR_FAILURE_BINDING_NAME = "prior_f4_failure_binding.json"
F4_LOCAL_MANIFEST_NAME = "f4_local_qwen_manifest.json"
F4_LOCAL_PRE_CALL_NAME = "f4_pre_call.json"
F4_LOCAL_RAW_NAME = "raw_response.bin"
F4_LOCAL_NORMALIZED_OUTPUT_NAME = "normalized_f4_output.json"
F4_LOCAL_NORMALIZATION_RECEIPT_NAME = "f4_generic_ref_normalization_receipt.json"
F4_LOCAL_NORMALIZED_STATE_NAME = "normalized_f4_authority_state.json"
F4_LOCAL_RESULT_NAME = "f4_local_qwen_result.json"
F4_LOCAL_FINAL_RESULT_NAME = "f4_local_qwen_final_result.json"
F4_LOCAL_STDERR_NAME = "worker_stderr.bin"
F4_LOCAL_FIELD_CAPS = dict(_remote_f4.F4_FIELD_CAPS)
F4_LOCAL_ALLOWED_CATEGORIES = list(_remote_f4.F4_ALLOWED_CATEGORIES)
F4_LOCAL_PROHIBITED_CATEGORIES = list(_remote_f4.F4_PROHIBITED_CATEGORIES)
F4_LOCAL_WORKER_PROTOCOL = f"{F4_LOCAL_SCHEMA_PREFIX}.worker-ipc.v1"
F4_NATIVE_CONTEXT_REVISION = f"{F4_LOCAL_SCHEMA_PREFIX}.native_model_context.v1"
F4_PRIOR_FAILURE_REVISION = f"{F4_LOCAL_SCHEMA_PREFIX}.prior_failure.v2"
F4_MEMORY_EFFICIENT_ATTENTION_NAME = "req2web_f4_memory_efficient_sdpa"
F4_MEMORY_EFFICIENT_ATTENTION_REVISION = (
    f"{F4_LOCAL_SCHEMA_PREFIX}.memory_efficient_attention.v1"
)
F4_PRIOR_FAILURE_ROOT = Path(
    r"D:\Models\Req2Web\phase4_runs\p4-03d4b-local-qwen-f4-recovery-20260804-c"
)

Phase4LocalQwenF4ContractError = _local.Phase4LocalQwenContractError
_CanonicalRecord = _local._CanonicalRecord
_canonical_bytes = _local._canonical_bytes
_strict_json = _local._strict_json
_exact = _local._exact
_identity = _local._identity
_sha256 = _local._sha256
_b64 = _local._b64
_decode_b64 = _local._decode_b64
_text = _local._text
_write_once = _local._write_once


F4_RUNTIME_KIND = "local_qwen_model_worker"
F4_FAILURE_CODES = frozenset({
    "confirmation_required",
    "preflight_failed",
    "worker_identity_failed",
    "worker_protocol_failed",
    "worker_load_failed",
    "worker_generation_failed",
    "generation_timeout",
    "generation_cancelled",
    "raw_capture_failed",
    "parse_failed",
    "contract_failed",
    "registry_failed",
    "prior_failure_missing",
    "prior_failure_drifted",
    "teardown_unverified",
    "worker_exit_unverified",
})


class F4WorkerFailure(Phase4LocalQwenF4ContractError):
    def __init__(self, failure_code: str, message: str) -> None:
        if failure_code not in F4_FAILURE_CODES:
            raise ValueError(f"unknown F4 failure code: {failure_code}")
        super().__init__(message)
        self.failure_code = failure_code


class F4WorkerStartFailure(F4WorkerFailure):
    def __init__(self, failure_code: str, message: str, teardown: Mapping[str, object]) -> None:
        super().__init__(failure_code, message)
        self.teardown = dict(teardown)


F4_NATIVE_CONTEXT_POLICY = {
    "source": "inventory_bound_model_config",
    "relative_path": "config.json",
    "json_pointer": "/text_config/max_position_embeddings",
    "required_type": "exact_positive_integer",
    "output_limit_kind": "context_remaining",
}
F4_RECOVERY_POLICY = {
    "previous_call_consumed": 2,
    "new_call_cap": 1,
    "aggregate_after_cap": 3,
    "prior_failure_status": "failed_closed",
    "prior_failure_generate_calls": 1,
    "prior_failure_raw_status": "not_captured",
    "prior_failure_code": "worker_generation_failed",
    "prior_failure_binding_required": True,
}


def _f4_action_state(*, model_action: bool) -> dict[str, object]:
    return {
        "action_state_version": f"{F4_LOCAL_SCHEMA_PREFIX}.action_state.v1",
        "runtime_kind": F4_RUNTIME_KIND,
        "model_action": model_action,
        "graph_runtime_execution": False,
        "dependency_installation": False,
        "training": False,
        "remote_action": False,
        "network": False,
        "telemetry": False,
        "tracing": False,
        "local_files_only": True,
    }


def _validate_f4_action_state(value: object, *, model_action: bool) -> dict[str, object]:
    data = _exact(
        value,
        (
            "action_state_version", "runtime_kind", "model_action",
            "graph_runtime_execution", "dependency_installation", "training",
            "remote_action", "network", "telemetry", "tracing", "local_files_only",
        ),
        "F4 action state",
    )
    expected = _f4_action_state(model_action=model_action)
    if data != expected:
        raise Phase4LocalQwenF4ContractError("F4 action state drifted")
    return data


def _local_action_state(*, model_action: bool) -> dict[str, object]:
    """Compatibility alias kept private; F4 uses its own non-LangGraph state."""

    return _f4_action_state(model_action=model_action)


class F4LocalQwenPolicy(_CanonicalRecord):
    """Tracked policy for one local F4 generate and no downstream route."""

    KEYS = (
        "schema_version", "policy_id", "diagnostic_id", "pilot_id", "case",
        "savepoint", "node", "native_context", "recovery", "execution",
        "claim_boundaries", "action_state",
    )
    SCHEMA_VERSION = F4_LOCAL_POLICY_SCHEMA_VERSION

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="F4LocalQwenPolicy")
        _local._sha(data["policy_id"], "F4LocalQwenPolicy.policy_id")
        if data["diagnostic_id"] != F4_LOCAL_DIAGNOSTIC_ID or data["pilot_id"] != F4_LOCAL_PILOT_ID:
            raise Phase4LocalQwenF4ContractError("F4 local policy identity drifted")
        case = _exact(data["case"], ("case_id", "request_id", "node_id", "model_id", "model_revision"), "F4LocalQwenPolicy.case")
        if case != {
            "case_id": F4_LOCAL_CASE_ID, "request_id": F4_LOCAL_REQUEST_ID,
            "node_id": F4_LOCAL_NODE_ID, "model_id": _local.QWEN_MODEL_ID,
            "model_revision": _local.QWEN_MODEL_REVISION,
        }:
            raise Phase4LocalQwenF4ContractError("F4 local policy case drifted")
        savepoint = _exact(data["savepoint"], ("requires_live_f3_registry_replay", "receipt_is_not_authority"), "F4LocalQwenPolicy.savepoint")
        if savepoint != {"requires_live_f3_registry_replay": True, "receipt_is_not_authority": True}:
            raise Phase4LocalQwenF4ContractError("F4 local savepoint policy drifted")
        node = _exact(data["node"], ("node_id", "allowed_categories", "prohibited_categories", "field_caps", "upstream_required_node_ids", "b_aux_disposition", "model_output_format"), "F4LocalQwenPolicy.node")
        if node != {
            "node_id": F4_LOCAL_NODE_ID,
            "allowed_categories": F4_LOCAL_ALLOWED_CATEGORIES,
            "prohibited_categories": F4_LOCAL_PROHIBITED_CATEGORIES,
            "field_caps": F4_LOCAL_FIELD_CAPS,
            "upstream_required_node_ids": ["F1", "F2", "F3"],
            "b_aux_disposition": "absent/not_requested",
            "model_output_format": "exact_json_object",
        }:
            raise Phase4LocalQwenF4ContractError("F4 local node policy drifted")
        if data["native_context"] != F4_NATIVE_CONTEXT_POLICY:
            raise Phase4LocalQwenF4ContractError("F4 native context policy drifted")
        if data["recovery"] != F4_RECOVERY_POLICY:
            raise Phase4LocalQwenF4ContractError("F4 recovery policy drifted")
        execution = _exact(data["execution"], ("model_action", "generate_call_cap", "generate_calls", "automatic_retry", "output_limit_kind", "output_truncation", "raw_first", "pending_confirmation", "f4_model_invocation", "composition", "assembler", "acceptance", "repair", "g0"), "F4LocalQwenPolicy.execution")
        if execution != {
            "model_action": False, "generate_call_cap": 1, "generate_calls": 0, "automatic_retry": False,
            "output_limit_kind": "context_remaining",
            "output_truncation": False, "raw_first": True,
            "pending_confirmation": True, "f4_model_invocation": "not_executed",
            "composition": "not_executed", "assembler": "not_executed",
            "acceptance": "not_executed", "repair": "not_executed", "g0": "not_executed",
        }:
            raise Phase4LocalQwenF4ContractError("F4 local execution policy drifted")
        claims = _exact(data["claim_boundaries"], ("formal_quality", "h1_or_gold", "training", "data_authoring"), "F4LocalQwenPolicy.claim_boundaries")
        if claims != {"formal_quality": False, "h1_or_gold": False, "training": False, "data_authoring": False}:
            raise Phase4LocalQwenF4ContractError("F4 local claim boundary drifted")
        _validate_f4_action_state(data["action_state"], model_action=False)
        expected = _identity({key: value for key, value in data.items() if key != "policy_id"}, revision=cls.SCHEMA_VERSION)["sha256"]
        if data["policy_id"] != expected:
            raise Phase4LocalQwenF4ContractError("F4 local policy id is invalid")


def build_f4_local_qwen_policy_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": F4_LOCAL_POLICY_SCHEMA_VERSION,
        "policy_id": "pending",
        "diagnostic_id": F4_LOCAL_DIAGNOSTIC_ID,
        "pilot_id": F4_LOCAL_PILOT_ID,
        "case": {
            "case_id": F4_LOCAL_CASE_ID,
            "request_id": F4_LOCAL_REQUEST_ID,
            "node_id": F4_LOCAL_NODE_ID,
            "model_id": _local.QWEN_MODEL_ID,
            "model_revision": _local.QWEN_MODEL_REVISION,
        },
        "savepoint": {"requires_live_f3_registry_replay": True, "receipt_is_not_authority": True},
        "node": {
            "node_id": F4_LOCAL_NODE_ID,
            "allowed_categories": F4_LOCAL_ALLOWED_CATEGORIES,
            "prohibited_categories": F4_LOCAL_PROHIBITED_CATEGORIES,
            "field_caps": F4_LOCAL_FIELD_CAPS,
            "upstream_required_node_ids": ["F1", "F2", "F3"],
            "b_aux_disposition": "absent/not_requested",
            "model_output_format": "exact_json_object",
        },
        "native_context": dict(F4_NATIVE_CONTEXT_POLICY),
        "recovery": dict(F4_RECOVERY_POLICY),
        "execution": {
            "model_action": False, "generate_call_cap": 1, "generate_calls": 0,
            "automatic_retry": False, "output_limit_kind": "context_remaining",
            "output_truncation": False, "raw_first": True, "pending_confirmation": True,
            "f4_model_invocation": "not_executed",
            "composition": "not_executed", "assembler": "not_executed",
            "acceptance": "not_executed", "repair": "not_executed", "g0": "not_executed",
        },
        "claim_boundaries": {"formal_quality": False, "h1_or_gold": False, "training": False, "data_authoring": False},
        "action_state": _f4_action_state(model_action=False),
    }
    payload["policy_id"] = _identity(
        {key: value for key, value in payload.items() if key != "policy_id"},
        revision=F4_LOCAL_POLICY_SCHEMA_VERSION,
    )["sha256"]
    return payload


def load_f4_local_qwen_policy() -> tuple[F4LocalQwenPolicy, bytes]:
    raw = _local._read_tracked_canonical_record(F4_LOCAL_POLICY_PATH, "F4 local Qwen policy")
    return F4LocalQwenPolicy.from_bytes(raw), raw


class F4PriorFailureBinding(_CanonicalRecord):
    """Immutable binding to the consumed failed F4 call."""

    KEYS = (
        "schema_version", "binding_id", "pilot_id",
        "prior_result_root_identity", "final_result_identity",
        "key_file_identities", "previous_call_consumed", "new_call_cap",
        "aggregate_after_cap", "prior_failure_facts", "action_state",
    )
    SCHEMA_VERSION = F4_PRIOR_FAILURE_REVISION

    @classmethod
    def create(cls, *, prior_result_root: Path) -> "F4PriorFailureBinding":
        if (
            not isinstance(prior_result_root, Path)
            or not prior_result_root.is_absolute()
            or not prior_result_root.is_dir()
            or prior_result_root.is_symlink()
        ):
            raise F4WorkerFailure("prior_failure_missing", "prior F4 failure root is unsafe")
        final_candidates = (
            F4_LOCAL_FINAL_RESULT_NAME,
            F4_LOCAL_RESULT_NAME,
        )
        final_name = next(
            (name for name in final_candidates if (prior_result_root / name).is_file()),
            None,
        )
        if final_name is None:
            raise F4WorkerFailure("prior_failure_missing", "prior F4 final result is missing")
        final_raw = (prior_result_root / final_name).read_bytes()
        final = _strict_json(final_raw, require_canonical=True)
        facts = {
            "status": final.get("status"),
            "node_model_pass": final.get("node_model_pass"),
            "generate_calls": final.get("generate_calls"),
            "previous_call_consumed": final.get("previous_call_consumed"),
            "current_call_consumed": final.get("current_call_consumed"),
            "aggregate_after_call": final.get("aggregate_after_call"),
            "raw_status": (
                final.get("raw", {}).get("status")
                if isinstance(final.get("raw"), Mapping)
                else None
            ),
            "failure_code": final.get("failure_code"),
        }
        if facts != {
            "status": "failed_closed",
            "node_model_pass": False,
            "generate_calls": 1,
            "previous_call_consumed": 1,
            "current_call_consumed": 1,
            "aggregate_after_call": 2,
            "raw_status": "not_captured",
            "failure_code": "worker_generation_failed",
        }:
            raise F4WorkerFailure("prior_failure_drifted", "prior F4 failure facts drifted")
        key_file_identities: dict[str, object] = {}
        for name in (
            final_name,
            F4_LOCAL_RESULT_NAME,
            F4_LOCAL_MANIFEST_NAME,
            F4_LOCAL_PRE_CALL_NAME,
            F4_LOCAL_PRIOR_FAILURE_BINDING_NAME,
            "local_qwen_load_receipt.json",
        ):
            path = prior_result_root / name
            if path.is_file() and not path.is_symlink():
                raw = path.read_bytes()
                key_file_identities[name] = _identity(
                    raw,
                    revision=f"{F4_PRIOR_FAILURE_REVISION}.file",
                    identity_kind="raw_bytes",
                )
        root_identity = _identity(
            {
                "resolved_root": str(prior_result_root.resolve(strict=True)),
                "key_file_identities": key_file_identities,
            },
            revision=f"{F4_PRIOR_FAILURE_REVISION}.root",
        )
        payload: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "binding_id": "pending",
            "pilot_id": F4_LOCAL_PILOT_ID,
            "prior_result_root_identity": root_identity,
            "final_result_identity": _identity(
                final_raw,
                revision=f"{F4_PRIOR_FAILURE_REVISION}.final_result",
                identity_kind="raw_bytes",
            ),
            "key_file_identities": key_file_identities,
            "previous_call_consumed": 2,
            "new_call_cap": 1,
            "aggregate_after_cap": 3,
            "prior_failure_facts": facts,
            "action_state": _f4_action_state(model_action=False),
        }
        payload["binding_id"] = _identity(
            {key: value for key, value in payload.items() if key != "binding_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(payload)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="F4PriorFailureBinding")
        _local._sha(data["binding_id"], "F4PriorFailureBinding.binding_id")
        if data["pilot_id"] != F4_LOCAL_PILOT_ID:
            raise Phase4LocalQwenF4ContractError("prior F4 pilot identity drifted")
        for key in ("prior_result_root_identity", "final_result_identity"):
            _local._validate_identity(data[key], f"F4PriorFailureBinding.{key}")
        identities = data["key_file_identities"]
        if not isinstance(identities, Mapping) or not identities:
            raise Phase4LocalQwenF4ContractError("prior F4 key file identities are missing")
        for name, identity in identities.items():
            _local._text(name, "F4PriorFailureBinding.key_file_name")
            _local._validate_identity(identity, f"F4PriorFailureBinding.{name}")
        if (
            data["previous_call_consumed"] != 2
            or data["new_call_cap"] != 1
            or data["aggregate_after_cap"] != 3
            or data["prior_failure_facts"]
            != {
                "status": "failed_closed",
                "node_model_pass": False,
                "generate_calls": 1,
                "previous_call_consumed": 1,
                "current_call_consumed": 1,
                "aggregate_after_call": 2,
                "raw_status": "not_captured",
                "failure_code": "worker_generation_failed",
            }
        ):
            raise Phase4LocalQwenF4ContractError("prior F4 failure facts drifted")
        _validate_f4_action_state(data["action_state"], model_action=False)
        expected = _identity(
            {key: value for key, value in data.items() if key != "binding_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["binding_id"] != expected:
            raise Phase4LocalQwenF4ContractError("prior F4 binding identity drifted")


def _read_native_model_context(
    *,
    model_root: Path,
) -> tuple[int, dict[str, object]]:
    config_path = model_root / "config.json"
    if not config_path.is_file() or config_path.is_symlink():
        raise Phase4LocalQwenF4ContractError("native model config.json is unavailable")
    raw = config_path.read_bytes()
    config = _strict_json(raw, require_canonical=False)
    text_config = config.get("text_config")
    if not isinstance(text_config, Mapping):
        raise Phase4LocalQwenF4ContractError("native model text_config is missing")
    context_tokens = text_config.get("max_position_embeddings")
    if type(context_tokens) is not int or context_tokens <= 0:
        raise Phase4LocalQwenF4ContractError(
            "native model context must be an exact positive integer"
        )
    identity = _identity(
        {
            "relative_path": "config.json",
            "json_pointer": "/text_config/max_position_embeddings",
            "context_tokens": context_tokens,
            "config_sha256": _sha256(raw),
        },
        revision=F4_NATIVE_CONTEXT_REVISION,
    )
    return context_tokens, identity


def _f4_projection_policy() -> _local.NodeProjectionPolicy:
    return _local.NodeProjectionPolicy.create(
        node_id=F4_LOCAL_NODE_ID,
        allowed_categories=F4_LOCAL_ALLOWED_CATEGORIES,
        prohibited_categories=F4_LOCAL_PROHIBITED_CATEGORIES,
        field_caps=F4_LOCAL_FIELD_CAPS,
        upstream_required_node_ids=["F1", "F2", "F3"],
        projection_revision=f"{F4_LOCAL_SCHEMA_PREFIX}.input.v1",
        prompt_template_revision=f"{F4_LOCAL_SCHEMA_PREFIX}.prompt.v1",
        config_revision=f"{F4_LOCAL_SCHEMA_PREFIX}.config.v1",
    )


def _build_f4_prompt(*, input_bytes: bytes, policy: _local.NodeProjectionPolicy) -> bytes:
    payload = {
        "prompt_schema_version": f"{F4_LOCAL_SCHEMA_PREFIX}.prompt.v1",
        "node_id": F4_LOCAL_NODE_ID,
        "template_revision": f"{F4_LOCAL_SCHEMA_PREFIX}.prompt.v1",
        "output_format": "exact_json_object",
        "input_sha256": _sha256(input_bytes),
        "input_byte_length": len(input_bytes),
        "instructions": list(_local._NODE_PROMPT_GUIDANCE[F4_LOCAL_NODE_ID]) + [
            "Return one complete JSON object only; do not emit markdown or commentary.",
            "Do not claim final Acceptance, browser evidence, repair, G0, or production success.",
        ],
        "output_contract": copy.deepcopy(_local._NODE_OUTPUT_CONTRACTS[F4_LOCAL_NODE_ID]),
        "model_id": _local.QWEN_MODEL_ID,
        "model_revision": _local.QWEN_MODEL_REVISION,
        "projection_revision": policy.projection_revision,
    }
    raw = _canonical_bytes(payload)
    if len(raw) > F4_LOCAL_FIELD_CAPS["prompt_bytes"]:
        raise Phase4LocalQwenF4ContractError("F4 local prompt exceeds cap")
    return raw


def _build_f4_config(
    *,
    profile: _local.LocalQwenProfile,
    native_context_identity: Mapping[str, object],
) -> bytes:
    return _canonical_bytes({
        "config_schema_version": f"{F4_LOCAL_SCHEMA_PREFIX}.config.v1",
        "node_id": F4_LOCAL_NODE_ID,
        "profile_identity": _identity(profile.to_dict(), revision=_local.LOCAL_QWEN_PROFILE_SCHEMA_VERSION),
        "dtype": profile.dtype,
        "quantization": profile.quantization,
        "compute_dtype": profile.compute_dtype,
        "device_map": profile.device_map,
        "cpu_offload": profile.cpu_offload,
        "context_tokens": profile.context_tokens,
        "max_new_tokens": None,
        "output_limit_kind": "context_remaining",
        "native_context_identity": dict(native_context_identity),
        "native_context_source": "config.json:/text_config/max_position_embeddings",
        "context_boundary": {
            "context_tokens": profile.context_tokens,
            "input_tokens_measured_at_runtime": True,
            "positive_remaining_required": True,
            "hard_boundary_incomplete_json_is_failure": True,
        },
        "generation_stop_policy": {"eos_allowed": True, "complete_json_required": True, "truncation_is_failure": True},
        "full_attention": {
            "implementation": F4_MEMORY_EFFICIENT_ATTENTION_NAME,
            "revision": F4_MEMORY_EFFICIENT_ATTENTION_REVISION,
            "input_tokens_unchanged": True,
            "output_tokens_unchanged": True,
            "math_fallback_allowed": False,
        },
        "generate_calls": 0,
        "retry_count": 0,
        "pending_confirmation": True,
        "model_action": False,
    })


def _build_f4_request(*, native_context_identity: Mapping[str, object]) -> bytes:
    return _canonical_bytes({
        "request_schema_version": f"{F4_LOCAL_SCHEMA_PREFIX}.request.v1",
        "diagnostic_id": F4_LOCAL_DIAGNOSTIC_ID,
        "pilot_id": F4_LOCAL_PILOT_ID,
        "case_id": F4_LOCAL_CASE_ID,
        "request_id": F4_LOCAL_REQUEST_ID,
        "node_id": F4_LOCAL_NODE_ID,
        "model_id": _local.QWEN_MODEL_ID,
        "model_revision": _local.QWEN_MODEL_REVISION,
        "call_kind": "f4_local_node_contract",
        "generate_call_index": 1,
        "generate_call_cap": 1,
        "generate_calls": 0,
        "retry_count": 0,
        "output_limit_kind": "context_remaining",
        "native_context_identity": dict(native_context_identity),
        "pending_confirmation": True,
        "output_policy": "complete_json_or_failed_closed",
    })


class F4LocalPreparedExperiment(NamedTuple):
    result_root: Path
    policy: F4LocalQwenPolicy
    policy_raw: bytes
    savepoint: _remote_f4.F4SavepointBinding
    profile: _local.LocalQwenProfile
    input_bytes: bytes
    prompt_bytes: bytes
    config_bytes: bytes
    request_bytes: bytes
    manifest_bytes: bytes
    b_input: dict[str, object]
    authority_state: Mapping[str, object]
    native_context_identity: Mapping[str, object]
    prior_failure_binding: F4PriorFailureBinding


def _safe_absolute_directory(path: Path, name: str, *, must_exist: bool) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
        raise Phase4LocalQwenF4ContractError(f"{name} must be an absolute non-symlink path")
    if must_exist and not path.is_dir():
        raise Phase4LocalQwenF4ContractError(f"{name} is not an existing directory")
    return path


def _build_f4_profile(
    *,
    model_root: Path,
    integrity_evidence: Path,
) -> tuple[_local.LocalQwenProfile, dict[str, object]]:
    native_context_tokens, native_context_identity = _read_native_model_context(
        model_root=model_root,
    )
    inventory = _local.validate_model_inventory_metadata(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    gpu = _local.probe_local_gpu_facts()
    facts = _local.collect_runtime_facts(gpu_facts=gpu)
    model_root_identity = _identity({
        "model_id": _local.QWEN_MODEL_ID,
        "model_revision": _local.QWEN_MODEL_REVISION,
        "resolved_model_root": str(model_root.resolve(strict=True)),
        "inventory": inventory["inventory_identity"],
    }, revision=f"{_local.P4_03_SCHEMA_PREFIX}.model-root.v1")
    profile = _local.LocalQwenProfile.create(
        model_root_identity=model_root_identity,
        model_inventory_identity=inventory["inventory_identity"],
        model_file_count=int(inventory["file_count"]),
        python_version=str(facts["python_version"]),
        device_name=str(gpu["device_name"]),
        device_uuid=str(gpu["device_uuid"]),
        total_vram_bytes=int(gpu["total_vram_bytes"]),
        free_vram_bytes_at_preflight=int(gpu["free_vram_bytes"]),
        driver_version=str(gpu["driver_version"]),
        cuda_version=str(gpu["cuda_version"]),
        max_input_tokens=native_context_tokens,
        context_tokens=native_context_tokens,
        max_new_tokens=native_context_tokens,
        timeout_seconds=3600,
    )
    return profile, native_context_identity


def _f4_profile_runtime_record(
    profile: _local.LocalQwenProfile,
    native_context_identity: Mapping[str, object],
) -> dict[str, object]:
    return {
        "profile_identity": _identity(
            profile.to_dict(),
            revision=_local.LOCAL_QWEN_PROFILE_SCHEMA_VERSION,
        ),
        "profile_name": profile.profile_name,
        "context_tokens": profile.context_tokens,
        "native_context_identity": dict(native_context_identity),
        "native_context_source": "config.json:/text_config/max_position_embeddings",
        "output_limit_kind": "context_remaining",
        "generation_budget_formula": "context_tokens - actual_tokenized_input_length",
        "positive_remaining_required": True,
        "hard_boundary_incomplete_json_is_failure": True,
        "artificial_short_output_cap": False,
        "full_attention_implementation": F4_MEMORY_EFFICIENT_ATTENTION_NAME,
        "full_attention_revision": F4_MEMORY_EFFICIENT_ATTENTION_REVISION,
        "full_attention_math_fallback_allowed": False,
        "input_tokens_unchanged": True,
        "output_tokens_unchanged": True,
        "cpu_offload": profile.cpu_offload,
        "device_map": profile.device_map,
        "dtype": profile.dtype,
        "quantization": profile.quantization,
        "compute_dtype": profile.compute_dtype,
    }


def _context_remaining_budget(profile: object, input_length: int) -> int:
    context_tokens = getattr(profile, "context_tokens", None)
    if type(context_tokens) is not int or context_tokens < 1:
        raise Phase4LocalQwenF4ContractError("F4 context boundary is invalid")
    if type(input_length) is not int or input_length < 0:
        raise Phase4LocalQwenF4ContractError("F4 tokenized input length is invalid")
    remaining = context_tokens - input_length
    if remaining <= 0:
        raise Phase4LocalQwenF4ContractError("F4 context has no positive remaining generation budget")
    return remaining


def _teardown_is_verified(
    teardown: Mapping[str, object],
    stderr_snapshot: Mapping[str, object],
) -> bool:
    return (
        teardown.get("worker_exit_verified") is True
        and stderr_snapshot.get("completed") is True
        and stderr_snapshot.get("error") is None
        and stderr_snapshot.get("thread_joined") is True
    )


def _finalize_success(
    candidate_success: bool,
    *,
    teardown: Mapping[str, object],
    stderr_snapshot: Mapping[str, object],
) -> tuple[bool, str | None]:
    if not candidate_success:
        return False, None
    if not _teardown_is_verified(teardown, stderr_snapshot):
        return False, "teardown_unverified"
    return True, None


def _replay_f3_savepoint_local(
    *,
    f3_result_root: Path,
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
    """Replay F3 from immutable raw bytes, then cross-check canonical sidecars."""

    result_raw = _remote_f4._read_input_file(
        f3_result_root / _f3.REMOTE_REVALIDATION_NAME,
        "F3 revalidation result",
    )
    output_raw = _remote_f4._read_input_file(
        f3_result_root / _f3.REMOTE_REVALIDATED_OUTPUT_NAME,
        "F3 revalidated output",
    )
    state_raw = _remote_f4._read_input_file(
        f3_result_root / _f3.REMOTE_REVALIDATED_STATE_NAME,
        "F3 revalidated state",
    )
    source_raw = _remote_f4._read_input_file(
        f3_result_root / _f3.REMOTE_RAW_NAME,
        "F3 source raw",
    )
    if (
        _sha256(result_raw) != _remote_f4.F3_REVALIDATION_RESULT_SHA256
        or _sha256(output_raw) != _remote_f4.F3_REVALIDATED_OUTPUT_SHA256
        or _sha256(state_raw) != _remote_f4.F3_REVALIDATED_STATE_SHA256
        or _sha256(source_raw) != _remote_f4.F3_SOURCE_RAW_SHA256
    ):
        raise Phase4LocalQwenF4ContractError("F3 savepoint file hash drifted")
    revalidation = _f3.RemoteRevalidationRecord.from_bytes(result_raw)
    if (
        revalidation.revalidation_id != _remote_f4.F3_REVALIDATION_ID
        or revalidation.node_model_pass is not True
        or revalidation.revalidation_generate_calls != 0
    ):
        raise Phase4LocalQwenF4ContractError("F3 savepoint status drifted")
    source_output = _strict_json(source_raw, require_canonical=False)
    _strict_json(output_raw, require_canonical=True)
    if _canonical_bytes(source_output) != output_raw:
        raise Phase4LocalQwenF4ContractError(
            "F3 source raw and canonical output sidecar drifted"
        )
    _strict_json(state_raw, require_canonical=True)
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
        "F3",
        source_output,
        checkpoint.authority_state,
    )
    replayed_state = phase4_register_node_output(
        checkpoint.authority_state,
        "F3",
        validated,
    )
    if _canonical_bytes(replayed_state) != state_raw:
        raise Phase4LocalQwenF4ContractError(
            "F3 replayed authority state drifted from canonical sidecar"
        )
    profile = _f3.RemoteQwenProfile.from_bytes(
        _remote_f4._read_input_file(
            f3_result_root / _f3.REMOTE_PROFILE_NAME,
            "F3 profile",
        )
    )
    return (
        result_raw,
        output_raw,
        state_raw,
        source_raw,
        revalidation,
        profile,
        replayed_state,
        checkpoint,
    )


def prepare_phase4_local_qwen_f4(
    *,
    f3_result_root: Path,
    model_root: Path,
    integrity_evidence: Path,
    prior_f4_failure_root: Path,
    result_root: Path,
) -> F4LocalPreparedExperiment:
    """Replay F3 and write an immutable local F4 pre-call packet."""

    _local.verify_offline_environment()
    _safe_absolute_directory(f3_result_root, "F3 result root", must_exist=True)
    _safe_absolute_directory(model_root, "model root", must_exist=True)
    _safe_absolute_directory(integrity_evidence.parent, "integrity evidence parent", must_exist=True)
    if not integrity_evidence.is_file() or integrity_evidence.is_symlink():
        raise Phase4LocalQwenF4ContractError("integrity evidence is not a regular file")
    if not isinstance(result_root, Path) or not result_root.is_absolute() or result_root.exists():
        raise Phase4LocalQwenF4ContractError("F4 result root must be new and absolute")
    if not result_root.parent.is_dir() or result_root.parent.is_symlink():
        raise Phase4LocalQwenF4ContractError("F4 result root parent is unsafe")
    policy, policy_raw = load_f4_local_qwen_policy()
    prior_failure_binding = F4PriorFailureBinding.create(
        prior_result_root=prior_f4_failure_root,
    )
    replay = _replay_f3_savepoint_local(f3_result_root=f3_result_root)
    result_raw, output_raw, state_raw, source_raw, revalidation, _, state, checkpoint = replay
    if revalidation.node_model_pass is not True or revalidation.revalidation_generate_calls != 0:
        raise Phase4LocalQwenF4ContractError("F3 savepoint is not a validated no-call source")
    from req2web_orchestration.phase4_graph import phase4_create_mapping, synthetic_commerce_b_input, validate_b_input
    b_input = validate_b_input(synthetic_commerce_b_input())
    mapping = phase4_create_mapping(state)
    projection = _f4_projection_policy()
    input_bytes = _local.derive_node_input(
        node_id=F4_LOCAL_NODE_ID,
        b_input_bytes=_canonical_bytes(b_input),
        upstream_outputs={**checkpoint.outputs, "F3": _canonical_bytes(_strict_json(output_raw))},
        authority_state=state,
        policy=projection,
    )
    profile, native_context_identity = _build_f4_profile(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    savepoint = _remote_f4.F4SavepointBinding.create(
        result_raw=result_raw,
        output_raw=output_raw,
        state_raw=state_raw,
        source_raw=source_raw,
        revalidation=revalidation,
        state_replayed=state,
    )
    prompt_bytes = _build_f4_prompt(input_bytes=input_bytes, policy=projection)
    config_bytes = _build_f4_config(
        profile=profile,
        native_context_identity=native_context_identity,
    )
    request_bytes = _build_f4_request(
        native_context_identity=native_context_identity,
    )
    marker = f"{F4_LOCAL_ROOT_MARKER}:{uuid.uuid4().hex}"
    manifest = _canonical_bytes({
        "schema_version": F4_LOCAL_MANIFEST_SCHEMA_VERSION,
        "diagnostic_id": F4_LOCAL_DIAGNOSTIC_ID,
        "pilot_id": F4_LOCAL_PILOT_ID,
        "case_id": F4_LOCAL_CASE_ID,
        "request_id": F4_LOCAL_REQUEST_ID,
        "node_id": F4_LOCAL_NODE_ID,
        "policy_identity": _identity(policy.to_dict(), revision=F4_LOCAL_POLICY_SCHEMA_VERSION),
        "savepoint_binding_identity": _identity(savepoint.to_dict(), revision=_remote_f4.F4_BINDING_SCHEMA_VERSION),
        "mapping_identity": _identity(mapping, revision="req2web.phase4.mapping.p4_02a.v1"),
        "profile_identity": _identity(profile.to_dict(), revision=_local.LOCAL_QWEN_PROFILE_SCHEMA_VERSION),
        "profile_runtime": _f4_profile_runtime_record(
            profile,
            native_context_identity,
        ),
        "native_context_identity": dict(native_context_identity),
        "native_context_source": "config.json:/text_config/max_position_embeddings",
        "prior_failure_binding_identity": _identity(
            prior_failure_binding.to_dict(),
            revision=F4_PRIOR_FAILURE_REVISION,
        ),
        "input_identity": _identity(input_bytes, revision=f"{F4_LOCAL_SCHEMA_PREFIX}.input.v1", identity_kind="raw_bytes"),
        "prompt_identity": _identity(prompt_bytes, revision=f"{F4_LOCAL_SCHEMA_PREFIX}.prompt.v1", identity_kind="raw_bytes"),
        "config_identity": _identity(config_bytes, revision=f"{F4_LOCAL_SCHEMA_PREFIX}.config.v1", identity_kind="raw_bytes"),
        "request_identity": _identity(request_bytes, revision=f"{F4_LOCAL_SCHEMA_PREFIX}.request.v1", identity_kind="raw_bytes"),
        "result_root_marker": marker,
        "execution": {
            "generate_call_cap": 1, "generate_calls": 0, "retry_count": 0,
            "raw_first": True, "output_limit_kind": "context_remaining",
            "context_boundary": {
                "context_tokens": profile.context_tokens,
                "native_context_identity": dict(native_context_identity),
                "input_tokens_measured_at_runtime": True,
                "hard_boundary_incomplete_json_is_failure": True,
            },
            "output_truncation": False,
        },
        "action_state": _local_action_state(model_action=False),
    })
    result = _canonical_bytes({
        "schema_version": F4_LOCAL_RESULT_SCHEMA_VERSION,
        "diagnostic_id": F4_LOCAL_DIAGNOSTIC_ID,
        "pilot_id": F4_LOCAL_PILOT_ID,
        "node_id": F4_LOCAL_NODE_ID,
        "status": "preflight_prepared_no_model",
        "model_action": False,
        "generate_calls": 0,
        "manifest_identity": _identity(manifest, revision=F4_LOCAL_MANIFEST_SCHEMA_VERSION, identity_kind="raw_bytes"),
        "action_state": _local_action_state(model_action=False),
    })
    result_root.mkdir(parents=False)
    _write_once(result_root, F4_LOCAL_ROOT_MARKER, (marker + "\n").encode("ascii"))
    for name, raw in (
        (F4_LOCAL_POLICY_COPY, policy_raw),
        (F4_LOCAL_PROFILE_NAME, _canonical_bytes(_f4_profile_runtime_record(profile, native_context_identity))),
        (F4_LOCAL_PRIOR_FAILURE_BINDING_NAME, prior_failure_binding.canonical_bytes()),
        ("f3_savepoint_binding.json", savepoint.canonical_bytes()),
        ("f4_input.json", input_bytes),
        ("f4_prompt.json", prompt_bytes),
        ("f4_runtime_config.json", config_bytes),
        ("f4_request.json", request_bytes),
        (F4_LOCAL_MANIFEST_NAME, manifest),
        (F4_LOCAL_RESULT_NAME, result),
    ):
        _write_once(result_root, name, raw)
    return F4LocalPreparedExperiment(
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
        b_input=dict(b_input),
        authority_state=copy.deepcopy(state),
        native_context_identity=native_context_identity,
        prior_failure_binding=prior_failure_binding,
    )


def _emit_worker_event(event: str, delta: bytes = b"") -> None:
    if not isinstance(event, str) or type(delta) is not bytes:
        raise Phase4LocalQwenF4ContractError("F4 worker stream event is invalid")
    raw = _canonical_bytes({
        "schema_version": _local.P4D1_STREAM_EVENT_SCHEMA_VERSION,
        "event": event,
        "delta_b64": _b64(delta, "F4 worker stream delta"),
    })
    sys.stderr.buffer.write(raw + b"\n")
    sys.stderr.buffer.flush()


def _f4_json_is_complete_single_object(text: str) -> bool:
    if not isinstance(text, str):
        return False
    candidate = text.lstrip()
    if not candidate:
        return False
    try:
        value, end = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        return False
    return isinstance(value, dict) and not candidate[end:].strip()


class F4CompleteSingleJSONStoppingCriteria:
    """Stop only after a complete generated-only top-level JSON object."""

    def __init__(self, *, tokenizer: object, prompt_length: int) -> None:
        self._tokenizer = tokenizer
        self._prompt_length = prompt_length

    def __call__(self, input_ids: object, scores: object, **_: object) -> bool:
        del scores
        shape = getattr(input_ids, "shape", None)
        if shape is None or len(shape) < 2 or int(shape[1]) <= self._prompt_length:
            return False
        generated = input_ids[:, self._prompt_length:]
        for row in generated:
            token_ids = row.tolist()
            text = self._tokenizer.decode(
                token_ids,
                skip_special_tokens=True,
            )
            if _f4_json_is_complete_single_object(text):
                return True
        return False


def _repeat_f4_key_value_heads(hidden_states: object, repeats: int) -> object:
    shape = getattr(hidden_states, "shape", None)
    if shape is None or len(shape) != 4 or type(repeats) is not int or repeats < 1:
        raise Phase4LocalQwenF4ContractError(
            "F4 memory-efficient attention received invalid key/value states"
        )
    if repeats == 1:
        return hidden_states
    batch, key_value_heads, sequence_length, head_dim = shape
    return (
        hidden_states[:, :, None, :, :]
        .expand(
            batch,
            key_value_heads,
            repeats,
            sequence_length,
            head_dim,
        )
        .reshape(
            batch,
            key_value_heads * repeats,
            sequence_length,
            head_dim,
        )
    )


def _f4_memory_efficient_attention_forward(
    module: object,
    query: object,
    key: object,
    value: object,
    attention_mask: object | None,
    *,
    dropout: float = 0.0,
    scaling: float | None = None,
    is_causal: bool | None = None,
    position_bias: object | None = None,
    **kwargs: object,
) -> tuple[object, None]:
    """Equivalent causal SDPA that forbids the quadratic-memory math fallback."""

    if attention_mask is not None or position_bias is not None:
        raise Phase4LocalQwenF4ContractError(
            "F4 memory-efficient attention requires the unpadded causal text path"
        )
    if kwargs.get("output_attentions", False):
        raise Phase4LocalQwenF4ContractError(
            "F4 memory-efficient attention does not expose attention weights"
        )
    repeats = getattr(module, "num_key_value_groups", None)
    if type(repeats) is not int or repeats < 1:
        raise Phase4LocalQwenF4ContractError(
            "F4 memory-efficient attention group count drifted"
        )
    repeated_key = _repeat_f4_key_value_heads(key, repeats)
    repeated_value = _repeat_f4_key_value_heads(value, repeats)
    query_shape = getattr(query, "shape", None)
    if query_shape is None or len(query_shape) != 4:
        raise Phase4LocalQwenF4ContractError(
            "F4 memory-efficient attention query shape drifted"
        )
    requested_causal = (
        bool(is_causal)
        if is_causal is not None
        else bool(getattr(module, "is_causal", True))
    )
    kernel_is_causal = bool(int(query_shape[2]) > 1 and requested_causal)

    torch = __import__("torch")
    from torch.nn.attention import SDPBackend, sdpa_kernel

    with sdpa_kernel(SDPBackend.EFFICIENT_ATTENTION):
        output = torch.nn.functional.scaled_dot_product_attention(
            query,
            repeated_key,
            repeated_value,
            attn_mask=None,
            dropout_p=dropout,
            scale=scaling,
            is_causal=kernel_is_causal,
        )
    return output.transpose(1, 2).contiguous(), None


def _install_f4_memory_efficient_attention(backend: object) -> dict[str, object]:
    model = getattr(backend, "_model", None)
    torch = getattr(backend, "_torch", None)
    if model is None or torch is None:
        raise Phase4LocalQwenF4ContractError(
            "F4 memory-efficient attention requires a loaded backend"
        )
    if torch.backends.cuda.mem_efficient_sdp_enabled() is not True:
        raise Phase4LocalQwenF4ContractError(
            "F4 memory-efficient SDPA is unavailable"
        )

    from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS

    ALL_ATTENTION_FUNCTIONS.register(
        F4_MEMORY_EFFICIENT_ATTENTION_NAME,
        _f4_memory_efficient_attention_forward,
    )
    full_attention_layers = 0
    for module in model.modules():
        if type(module).__name__ != "Qwen3_5Attention":
            continue
        config = getattr(module, "config", None)
        if config is None:
            raise Phase4LocalQwenF4ContractError(
                "F4 full-attention config is unavailable"
            )
        config._attn_implementation = F4_MEMORY_EFFICIENT_ATTENTION_NAME
        full_attention_layers += 1
    if full_attention_layers != 8:
        raise Phase4LocalQwenF4ContractError(
            "F4 full-attention layer inventory drifted"
        )
    return {
        "implementation": F4_MEMORY_EFFICIENT_ATTENTION_NAME,
        "revision": F4_MEMORY_EFFICIENT_ATTENTION_REVISION,
        "full_attention_layers": full_attention_layers,
        "math_fallback_allowed": False,
        "input_tokens_unchanged": True,
        "output_tokens_unchanged": True,
    }


def _generate_stream_backend(
    backend: Any,
    *,
    input_bytes: bytes,
    prompt_bytes: bytes,
    config_bytes: bytes,
    request_bytes: bytes,
    emit_delta: Callable[[bytes], None],
) -> tuple[bytes, dict[str, object]]:
    """Use the already-loaded local backend while allowing F4 stream events."""

    prompt_contract = _strict_json(prompt_bytes)
    actual_input = _strict_json(input_bytes)
    _strict_json(config_bytes)
    request = _strict_json(request_bytes)
    if request.get("diagnostic_id") != F4_LOCAL_DIAGNOSTIC_ID:
        raise Phase4LocalQwenF4ContractError("F4 request identity drifted")
    model_text = (
        "PROMPT_CONTRACT_JSON\n" + _canonical_bytes(prompt_contract).decode("utf-8")
        + "\nACTUAL_NODE_INPUT_JSON\n" + _canonical_bytes(actual_input).decode("utf-8")
    )
    processor = backend._processor
    model = backend._model
    torch = backend._torch
    profile = backend._profile
    rendered = processor.apply_chat_template(
        [{"role": "user", "content": [{"type": "text", "text": model_text}]}],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    encoded = processor(text=[rendered], return_tensors="pt")
    keys = backend._validated_text_inputs(encoded)
    encoded = {key: encoded[key].to("cuda:0") for key in keys}
    input_length = int(encoded["input_ids"].shape[1])
    if input_length > profile.max_input_tokens:
        raise Phase4LocalQwenF4ContractError("F4 model input token cap exceeded")
    generation_budget = _context_remaining_budget(profile, input_length)
    tokenizer = getattr(processor, "tokenizer", processor)
    streamer = _local._P4D1TokenDeltaStreamer(tokenizer=tokenizer, emit_delta=emit_delta)
    transformers = __import__("transformers")
    stopping_criteria = transformers.StoppingCriteriaList(
        [
            F4CompleteSingleJSONStoppingCriteria(
                tokenizer=tokenizer,
                prompt_length=input_length,
            )
        ]
    )
    torch.manual_seed(profile.seed)
    torch.cuda.manual_seed_all(profile.seed)
    generated = model.generate(
        **encoded,
        streamer=streamer,
        stopping_criteria=stopping_criteria,
        max_new_tokens=generation_budget,
        do_sample=False,
        num_return_sequences=1,
    )
    generated_only = generated[:, input_length:]
    text = processor.batch_decode(generated_only, skip_special_tokens=True)[0]
    return text.encode("utf-8"), {
        "output_limit_kind": "context_remaining",
        "context_tokens": int(profile.context_tokens),
        "input_token_length": input_length,
        "generation_budget": generation_budget,
        "hard_boundary_incomplete_json_is_failure": True,
        "full_attention_implementation": F4_MEMORY_EFFICIENT_ATTENTION_NAME,
        "full_attention_math_fallback_allowed": False,
        "input_tokens_unchanged": True,
        "output_tokens_unchanged": True,
    }


def _run_f4_worker_protocol(*, model_root: Path) -> int:
    """Child-side protocol; stdout is reserved for parent IPC JSON."""

    worker_id = f"worker-{uuid.uuid4().hex}"
    generate_seen = False
    try:
        first = sys.stdin.buffer.readline()
        load = _exact(_strict_json(first.rstrip(b"\r\n")), ("protocol", "kind", "profile_b64"), "F4 worker load")
        if load["protocol"] != F4_LOCAL_WORKER_PROTOCOL or load["kind"] != "load":
            raise Phase4LocalQwenF4ContractError("F4 worker load request drifted")
        profile = _local.LocalQwenProfile.from_bytes(_decode_b64(load["profile_b64"], "F4 worker profile"))
        _emit_worker_event("load_started")
        backend = _local._LazyTransformersQwenBackend(
            model_root=model_root,
            profile=profile,
            runtime_capability=_local._REAL_RUNTIME_CAPABILITY,
        )
        backend.load()
        loaded_facts = backend.loaded_facts
        if loaded_facts is None:
            raise Phase4LocalQwenF4ContractError("F4 worker loaded facts unavailable")
        loaded_facts["f4_full_attention"] = _install_f4_memory_efficient_attention(
            backend
        )
        _emit_worker_event("load_completed")
        sys.stdout.buffer.write(_canonical_bytes({
            "protocol": F4_LOCAL_WORKER_PROTOCOL, "kind": "loaded",
            "worker_id": worker_id, "worker_pid": os.getpid(),
            "loaded_facts": loaded_facts,
        }) + b"\n")
        sys.stdout.buffer.flush()
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return 0
            request = _strict_json(line.rstrip(b"\r\n"))
            if request.get("protocol") != F4_LOCAL_WORKER_PROTOCOL:
                raise Phase4LocalQwenF4ContractError("F4 worker protocol drifted")
            if request.get("kind") == "shutdown":
                sys.stdout.buffer.write(_canonical_bytes({
                    "protocol": F4_LOCAL_WORKER_PROTOCOL, "kind": "shutdown_ack",
                    "worker_id": worker_id,
                }) + b"\n")
                sys.stdout.buffer.flush()
                return 0
            data = _exact(request, ("protocol", "kind", "call_id", "node_id", "input_b64", "prompt_b64", "config_b64", "request_b64"), "F4 worker generate")
            if data["kind"] != "generate" or data["node_id"] != F4_LOCAL_NODE_ID or generate_seen:
                raise Phase4LocalQwenF4ContractError("F4 worker one-call scope drifted")
            generate_seen = True
            try:
                _emit_worker_event("generation_started")
                raw, generation_facts = _generate_stream_backend(
                    backend,
                    input_bytes=_decode_b64(data["input_b64"], "F4 worker input"),
                    prompt_bytes=_decode_b64(data["prompt_b64"], "F4 worker prompt"),
                    config_bytes=_decode_b64(data["config_b64"], "F4 worker config"),
                    request_bytes=_decode_b64(data["request_b64"], "F4 worker request"),
                    emit_delta=lambda delta: _emit_worker_event("token_delta", delta),
                )
                _emit_worker_event("generation_completed")
                response = {"protocol": F4_LOCAL_WORKER_PROTOCOL, "kind": "generation_result", "call_id": data["call_id"], "worker_id": worker_id, "raw_b64": _b64(raw, "F4 worker raw"), "generation_facts": generation_facts}
            except Exception:
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                _emit_worker_event("generation_failed")
                response = {"protocol": F4_LOCAL_WORKER_PROTOCOL, "kind": "generation_error", "call_id": data["call_id"], "worker_id": worker_id}
            sys.stdout.buffer.write(_canonical_bytes(response) + b"\n")
            sys.stdout.buffer.flush()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        try:
            sys.stdout.buffer.write(_canonical_bytes({"protocol": F4_LOCAL_WORKER_PROTOCOL, "kind": "load_error"}) + b"\n")
            sys.stdout.buffer.flush()
        except Exception:
            pass
        return 2


class F4StreamMirror:
    """Parent-side visible stream; transcript bytes never become model raw."""

    def __init__(self, target: object | None = None) -> None:
        self._target = target if target is not None else sys.stderr
        self._chunks: list[bytes] = []
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self._first_token = False

    def _write(self, text: str) -> None:
        with self._lock:
            self._target.write(text)  # type: ignore[union-attr]
            self._target.flush()  # type: ignore[union-attr]

    def feed(self, raw: bytes) -> None:
        try:
            event = _strict_json(raw.rstrip(b"\r\n"))
        except Phase4LocalQwenF4ContractError:
            self._write(raw.decode("utf-8", errors="replace"))
            return
        if event.get("schema_version") != _local.P4D1_STREAM_EVENT_SCHEMA_VERSION:
            self._write(raw.decode("utf-8", errors="replace"))
            return
        data = _exact(event, ("schema_version", "event", "delta_b64"), "F4 stream event")
        name = _text(data["event"], "F4 stream event name")
        delta = _decode_b64(data["delta_b64"], "F4 stream delta", allow_empty=True)
        if name == "token_delta":
            with self._lock:
                self._chunks.append(delta)
                self._first_token = self._first_token or bool(delta)
                self._target.write(delta.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
                self._target.flush()  # type: ignore[union-attr]
            return
        if delta:
            raise Phase4LocalQwenF4ContractError("F4 non-token event carried delta")
        if name == "generation_started":
            self._started_at = time.monotonic()
        self._write(f"\n[P4-03D4] {name.replace('_', ' ')}\n")

    def heartbeat(self) -> None:
        if self._started_at is None or self._first_token:
            return
        elapsed = int(max(0, time.monotonic() - self._started_at))
        self._write(f"\n[P4-03D4] waiting for first token ({elapsed}s)\n")

    @property
    def partial_bytes(self) -> bytes:
        with self._lock:
            return b"".join(self._chunks)


def _teardown_spawned_process(
    process: subprocess.Popen[str],
    *,
    terminal_status: str,
    worker_id: str = "unknown",
) -> dict[str, object]:
    terminate_sent = False
    kill_sent = False
    if process.poll() is None:
        terminate_sent = True
        try:
            process.terminate()
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            kill_sent = True
            try:
                process.kill()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
    exit_code = _local._normalize_process_exit_code(process.poll())
    return {
        "worker_id": worker_id,
        "worker_pid": process.pid,
        "worker_exit_code": exit_code,
        "worker_exit_verified": exit_code is not None,
        "terminate_sent": terminate_sent,
        "kill_sent": kill_sent,
        "terminal_status": terminal_status if exit_code is not None else "teardown_unverified",
    }


@dataclass
class F4WorkerHandle:
    process: subprocess.Popen[str]
    messages: "queue.Queue[dict[str, object]]"
    stderr_capture: Any
    stderr_thread: threading.Thread
    worker_id: str
    loaded_facts: dict[str, object]
    profile: _local.LocalQwenProfile
    mirror: F4StreamMirror
    generate_count: int = 0
    closed: bool = False
    generation_facts: dict[str, object] | None = None

    def _send(self, payload: Mapping[str, object]) -> None:
        if self.closed or self.process.stdin is None:
            raise F4WorkerFailure("worker_protocol_failed", "F4 worker is unavailable")
        try:
            self.process.stdin.write(_canonical_bytes(dict(payload)).decode("utf-8") + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._force_teardown("worker_failed")
            raise F4WorkerFailure("worker_protocol_failed", "F4 worker IPC failed") from exc

    def _force_teardown(self, status: str) -> dict[str, object]:
        terminate_sent = False
        kill_sent = False
        if self.process.poll() is None:
            terminate_sent = True
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                kill_sent = True
                try:
                    self.process.kill()
                    self.process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        exit_code = _local._normalize_process_exit_code(self.process.poll())
        self.closed = exit_code is not None
        return {
            "worker_id": self.worker_id,
            "worker_pid": self.process.pid,
            "worker_exit_code": exit_code,
            "worker_exit_verified": exit_code is not None,
            "terminate_sent": terminate_sent,
            "kill_sent": kill_sent,
            "terminal_status": status if exit_code is not None else "teardown_unverified",
        }

    def generate(self, *, input_bytes: bytes, prompt_bytes: bytes, config_bytes: bytes, request_bytes: bytes) -> bytes:
        if self.generate_count >= 1:
            raise F4WorkerFailure("worker_protocol_failed", "F4 worker generate cap exhausted")
        self.generate_count += 1
        call_id = f"call-{uuid.uuid4().hex}"
        self._send({
            "protocol": F4_LOCAL_WORKER_PROTOCOL,
            "kind": "generate",
            "call_id": call_id,
            "node_id": F4_LOCAL_NODE_ID,
            "input_b64": _b64(input_bytes, "F4 input"),
            "prompt_b64": _b64(prompt_bytes, "F4 prompt"),
            "config_b64": _b64(config_bytes, "F4 config"),
            "request_b64": _b64(request_bytes, "F4 request"),
        })
        deadline = time.monotonic() + self.profile.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._force_teardown("generation_timeout")
                raise F4WorkerFailure("generation_timeout", "F4 generation timed out")
            try:
                message = self.messages.get(timeout=min(1.0, remaining))
            except queue.Empty:
                self.mirror.heartbeat()
                continue
            if message.get("call_id") != call_id:
                self._force_teardown("worker_failed")
                raise F4WorkerFailure("worker_protocol_failed", "F4 worker call identity drifted")
            if message.get("kind") == "generation_error":
                self._force_teardown("generation_failed")
                raise F4WorkerFailure("worker_generation_failed", "F4 worker generation failed closed")
            data = _exact(message, ("protocol", "kind", "call_id", "worker_id", "raw_b64", "generation_facts"), "F4 generation result")
            if data["protocol"] != F4_LOCAL_WORKER_PROTOCOL or data["kind"] != "generation_result" or data["worker_id"] != self.worker_id:
                self._force_teardown("worker_failed")
                raise F4WorkerFailure("worker_protocol_failed", "F4 worker result drifted")
            if not isinstance(data["generation_facts"], Mapping):
                self._force_teardown("worker_failed")
                raise F4WorkerFailure("worker_protocol_failed", "F4 generation facts are invalid")
            self.generation_facts = dict(data["generation_facts"])
            return _decode_b64(data["raw_b64"], "F4 raw response")

    def close(self) -> dict[str, object]:
        if self.closed:
            return self._force_teardown("already_closed")
        try:
            self._send({"protocol": F4_LOCAL_WORKER_PROTOCOL, "kind": "shutdown"})
            message = self.messages.get(timeout=20)
            if message.get("kind") != "shutdown_ack" or message.get("worker_id") != self.worker_id:
                raise F4WorkerFailure("worker_protocol_failed", "F4 worker shutdown acknowledgement drifted")
            self.process.wait(timeout=20)
            self.closed = True
        except (OSError, subprocess.TimeoutExpired, queue.Empty, F4WorkerFailure):
            return self._force_teardown("worker_failed")
        return self._force_teardown("normal_completed")


def start_f4_local_worker(
    *,
    model_root: Path,
    profile: _local.LocalQwenProfile,
    mirror: F4StreamMirror,
    load_timeout_seconds: int = 600,
) -> F4WorkerHandle:
    """Start one independently terminable F4 model worker."""

    profile.validate()
    if not model_root.is_absolute() or not model_root.is_dir() or model_root.is_symlink():
        raise Phase4LocalQwenF4ContractError("F4 worker model root is unsafe")
    executable, pythonpath = _local._supervised_worker_python_runtime()
    environment = dict(os.environ)
    environment.update({
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
        "LANGSMITH_TRACING": "0", "LANGCHAIN_TRACING_V2": "0",
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
        "PYTHONUNBUFFERED": "1", "PYTHONPATH": pythonpath,
    })
    bootstrap = (
        "import sys; from pathlib import Path; "
        "from req2web_runtime.phase4_local_qwen_f4 import _run_f4_worker_protocol; "
        "raise SystemExit(_run_f4_worker_protocol(model_root=Path(sys.argv[1])))"
    )
    process = subprocess.Popen(
        [executable, "-c", bootstrap, str(model_root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    messages: "queue.Queue[dict[str, object]]" = queue.Queue()
    stderr_capture = _local._WorkerStderrCapture()
    if process.stdin is None or process.stdout is None or process.stderr is None:
        teardown = _teardown_spawned_process(process, terminal_status="worker_protocol_failed")
        raise F4WorkerStartFailure("worker_protocol_failed", "F4 worker IPC streams unavailable", teardown)
    threading.Thread(target=_local._worker_stdout_reader, args=(process.stdout, messages), daemon=True).start()
    stderr_thread = threading.Thread(
        target=_local._worker_stderr_reader,
        args=(process.stderr, stderr_capture, mirror.feed),
        daemon=True,
    )
    stderr_thread.start()
    try:
        process.stdin.write(_canonical_bytes({
            "protocol": F4_LOCAL_WORKER_PROTOCOL, "kind": "load",
            "profile_b64": _b64(profile.canonical_bytes(), "F4 worker profile"),
        }).decode("utf-8") + "\n")
        process.stdin.flush()
        loaded = messages.get(timeout=load_timeout_seconds)
    except queue.Empty as exc:
        teardown = _teardown_spawned_process(process, terminal_status="worker_load_failed")
        raise F4WorkerStartFailure("worker_load_failed", "F4 worker load timed out", teardown) from exc
    except (BrokenPipeError, OSError) as exc:
        teardown = _teardown_spawned_process(process, terminal_status="worker_protocol_failed")
        raise F4WorkerStartFailure("worker_protocol_failed", "F4 worker load IPC failed", teardown) from exc
    try:
        loaded = _exact(
            loaded,
            ("protocol", "kind", "worker_id", "worker_pid", "loaded_facts"),
            "F4 worker loaded response",
        )
        if loaded["protocol"] != F4_LOCAL_WORKER_PROTOCOL or loaded["kind"] != "loaded":
            raise ValueError("F4 worker loaded response identity drifted")
        worker_id = _text(loaded["worker_id"], "F4 worker id")
        if loaded["worker_pid"] != process.pid or not worker_id.startswith("worker-"):
            raise ValueError("F4 worker identity drifted")
        loaded_facts = loaded["loaded_facts"]
        if not isinstance(loaded_facts, Mapping):
            raise ValueError("F4 worker loaded facts are invalid")
    except Exception as exc:
        teardown = _teardown_spawned_process(
            process,
            terminal_status="worker_identity_failed",
            worker_id=str(loaded.get("worker_id", "unknown")),
        )
        raise F4WorkerStartFailure("worker_identity_failed", "F4 worker identity validation failed", teardown) from exc
    return F4WorkerHandle(
        process, messages, stderr_capture, stderr_thread, worker_id,
        dict(loaded_facts), profile, mirror,
    )


def _identity_record(root: Mapping[str, object], *, revision: str, key: str) -> bytes:
    payload = dict(root)
    payload[key] = "pending"
    payload[key] = _identity(
        {name: value for name, value in payload.items() if name != key},
        revision=revision,
    )["sha256"]
    return _canonical_bytes(payload)


def _write_execution_result(result_root: Path, payload: Mapping[str, object]) -> bytes:
    raw = _canonical_bytes(dict(payload))
    _write_once(result_root, F4_LOCAL_FINAL_RESULT_NAME, raw)
    return raw


def _make_pre_call(*, prepared: F4LocalPreparedExperiment, worker: F4WorkerHandle) -> bytes:
    root = {
        "schema_version": F4_LOCAL_PRE_CALL_SCHEMA_VERSION,
        "pre_call_id": "pending",
        "manifest_identity": _identity(prepared.manifest_bytes, revision=F4_LOCAL_MANIFEST_SCHEMA_VERSION, identity_kind="raw_bytes"),
        "worker": {"worker_id": worker.worker_id, "worker_pid": worker.process.pid, "loaded_facts_identity": _identity(worker.loaded_facts, revision=f"{F4_LOCAL_SCHEMA_PREFIX}.loaded_facts.v1")},
        "call": {"node_id": F4_LOCAL_NODE_ID, "generate_call_index": 1, "generate_call_cap": 1, "retry_count": 0},
        "input_identity": _identity(prepared.input_bytes, revision=f"{F4_LOCAL_SCHEMA_PREFIX}.input.v1", identity_kind="raw_bytes"),
        "prompt_identity": _identity(prepared.prompt_bytes, revision=f"{F4_LOCAL_SCHEMA_PREFIX}.prompt.v1", identity_kind="raw_bytes"),
        "config_identity": _identity(prepared.config_bytes, revision=f"{F4_LOCAL_SCHEMA_PREFIX}.config.v1", identity_kind="raw_bytes"),
        "request_identity": _identity(prepared.request_bytes, revision=f"{F4_LOCAL_SCHEMA_PREFIX}.request.v1", identity_kind="raw_bytes"),
        "raw_first": True,
        "output_limit_kind": "context_remaining",
        "output_truncation": False,
        "generate_call_authorized": True,
        "action_state": _f4_action_state(model_action=False),
    }
    return _identity_record(root, revision=F4_LOCAL_PRE_CALL_SCHEMA_VERSION, key="pre_call_id")


def run_phase4_local_qwen_f4(
    *,
    f3_result_root: Path,
    model_root: Path,
    integrity_evidence: Path,
    prior_f4_failure_root: Path,
    result_root: Path,
    confirm_one_local_generate: bool,
    console: object | None = None,
) -> dict[str, object]:
    """Run exactly one local F4 generate and stop at the node boundary."""

    if confirm_one_local_generate is not True:
        raise F4WorkerFailure(
            "confirmation_required",
            "one local F4 generate confirmation is required",
        )
    prepared = prepare_phase4_local_qwen_f4(
        f3_result_root=f3_result_root,
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        prior_f4_failure_root=prior_f4_failure_root,
        result_root=result_root,
    )
    mirror = F4StreamMirror(console)
    worker: F4WorkerHandle | None = None
    raw: bytes | None = None
    failure_code: str | None = None
    terminal_status = "failed_closed"
    parsed_status = "not_executed"
    registry_status = "not_executed"
    teardown: dict[str, object] = {}
    stderr_snapshot: dict[str, object] = {
        "completed": False,
        "error": "not_started",
        "thread_joined": False,
        "stderr_bytes": None,
    }
    generation_facts: Mapping[str, object] | None = None
    normalization_receipt: Mapping[str, object] | None = None
    normalized_output: Mapping[str, object] | None = None
    normalized_state: Mapping[str, object] | None = None
    candidate_success = False
    raw_model_contract_success = False
    try:
        mirror._write(f"\n[P4-03D4] load started\n")
        worker = start_f4_local_worker(model_root=model_root, profile=prepared.profile, mirror=mirror)
        load_receipt = _canonical_bytes({
            "schema_version": f"{F4_LOCAL_SCHEMA_PREFIX}.load_receipt.v1",
            "worker_id": worker.worker_id,
            "worker_pid": worker.process.pid,
            "profile_identity": _identity(prepared.profile.to_dict(), revision=_local.LOCAL_QWEN_PROFILE_SCHEMA_VERSION),
            "profile_runtime": _f4_profile_runtime_record(
                prepared.profile,
                prepared.native_context_identity,
            ),
            "loaded_facts": worker.loaded_facts,
            "model_loaded": True,
            "generation_occurred": False,
            "action_state": _f4_action_state(model_action=True),
        })
        _write_once(prepared.result_root, "local_qwen_load_receipt.json", load_receipt)
        mirror._write(f"\n[P4-03D4] load completed\n")
        pre_call = _make_pre_call(prepared=prepared, worker=worker)
        _write_once(prepared.result_root, F4_LOCAL_PRE_CALL_NAME, pre_call)
        mirror._write(f"\n[P4-03D4] generation started\n")
        raw = worker.generate(
            input_bytes=prepared.input_bytes,
            prompt_bytes=prepared.prompt_bytes,
            config_bytes=prepared.config_bytes,
            request_bytes=prepared.request_bytes,
        )
        generation_facts = worker.generation_facts
        try:
            _write_once(prepared.result_root, F4_LOCAL_RAW_NAME, raw)
        except OSError as exc:
            raise F4WorkerFailure("raw_capture_failed", "F4 raw capture failed") from exc
        try:
            parsed = _strict_json(raw, require_canonical=False)
            parsed_status = "parsed"
        except Exception as exc:
            failure_code = "parse_failed"
            raise F4WorkerFailure("parse_failed", "F4 output parsing failed") from exc
        from req2web_orchestration.phase4_graph import (
            phase4_normalize_and_validate_f4_output,
            phase4_register_node_output,
        )
        try:
            validated, normalization_receipt = (
                phase4_normalize_and_validate_f4_output(
                    raw_bytes=raw,
                    output=parsed,
                    state=prepared.authority_state,
                )
            )
            normalized_output = validated
            raw_model_contract_success = bool(
                normalization_receipt["raw_model_contract_success"]
            )
            _write_once(
                prepared.result_root,
                F4_LOCAL_NORMALIZED_OUTPUT_NAME,
                _canonical_bytes(validated),
            )
            _write_once(
                prepared.result_root,
                F4_LOCAL_NORMALIZATION_RECEIPT_NAME,
                _canonical_bytes(normalization_receipt),
            )
            registry_status = (
                "raw_contract_validated"
                if raw_model_contract_success
                else "normalized_contract_validated"
            )
        except Exception as exc:
            failure_code = "contract_failed"
            raise F4WorkerFailure("contract_failed", "F4 output contract validation failed") from exc
        try:
            normalized_state = phase4_register_node_output(
                prepared.authority_state,
                F4_LOCAL_NODE_ID,
                validated,
            )
            _write_once(
                prepared.result_root,
                F4_LOCAL_NORMALIZED_STATE_NAME,
                _canonical_bytes(normalized_state),
            )
            registry_status = "validated_and_registered"
            candidate_success = True
        except Exception as exc:
            failure_code = "registry_failed"
            raise F4WorkerFailure("registry_failed", "F4 registry replay failed") from exc
    except F4WorkerStartFailure as exc:
        failure_code = exc.failure_code
        teardown = dict(exc.teardown)
    except F4WorkerFailure as exc:
        failure_code = exc.failure_code
        if raw is not None and parsed_status == "not_executed":
            parsed_status = "failed"
    except (OSError, ValueError) as exc:
        failure_code = "worker_load_failed" if worker is None else "worker_generation_failed"
        if raw is not None and parsed_status == "not_executed":
            parsed_status = "failed"
    finally:
        if worker is not None:
            teardown = worker.close()
            stderr_snapshot = worker.stderr_capture.snapshot(
                stderr_thread=worker.stderr_thread,
                worker_exit_verified=bool(teardown.get("worker_exit_verified")),
            )
            stderr = stderr_snapshot.get("stderr_bytes")
            if isinstance(stderr, bytes):
                try:
                    _write_once(prepared.result_root, F4_LOCAL_STDERR_NAME, stderr)
                except OSError:
                    failure_code = "raw_capture_failed"
                    candidate_success = False
    candidate_success, teardown_failure = _finalize_success(
        candidate_success,
        teardown=teardown,
        stderr_snapshot=stderr_snapshot,
    )
    teardown_ok = _teardown_is_verified(teardown, stderr_snapshot)
    if teardown_failure is not None:
        failure_code = teardown_failure
    if candidate_success:
        terminal_status = (
            "node_model_pass"
            if raw_model_contract_success
            else "normalized_node_pass"
        )
    elif failure_code is None:
        failure_code = "worker_exit_unverified" if not teardown_ok else "contract_failed"
    action_occurred = worker is not None or bool(teardown)
    result = {
        "schema_version": F4_LOCAL_RESULT_SCHEMA_VERSION,
        "diagnostic_id": F4_LOCAL_DIAGNOSTIC_ID,
        "pilot_id": F4_LOCAL_PILOT_ID,
        "node_id": F4_LOCAL_NODE_ID,
        "status": terminal_status,
        "node_model_pass": candidate_success and raw_model_contract_success,
        "normalized_node_contract_pass": candidate_success,
        "agent_chain_output_usable": candidate_success,
        "normalization": {
            "applied": bool(
                normalization_receipt
                and normalization_receipt.get("status") == "normalized"
            ),
            "receipt_identity": (
                None
                if normalization_receipt is None
                else _identity(
                    normalization_receipt,
                    revision=(
                        "req2web.phase4.p4_03d4.local_f4."
                        "normalization_receipt.v1"
                    ),
                )
            ),
            "normalized_output_identity": (
                None
                if normalized_output is None
                else _identity(
                    normalized_output,
                    revision=(
                        "req2web.phase4.p4_03d4.local_f4."
                        "normalized_output.v1"
                    ),
                )
            ),
            "normalized_state_identity": (
                None
                if normalized_state is None
                else _identity(
                    normalized_state,
                    revision=(
                        "req2web.phase4.p4_03d4.local_f4."
                        "normalized_state.v1"
                    ),
                )
            ),
            "raw_model_contract_success": raw_model_contract_success,
            "normalization_counts_as_repair": False,
            "model_generate_calls": 0,
        },
        "generate_calls": 1 if worker is not None and worker.generate_count else 0,
        "retry_count": 0,
        "output_limit_kind": "context_remaining",
        "native_context_identity": dict(prepared.native_context_identity),
        "prior_failure_binding_identity": _identity(
            prepared.prior_failure_binding.to_dict(),
            revision=F4_PRIOR_FAILURE_REVISION,
        ),
        "previous_call_consumed": 2,
        "current_call_consumed": 1 if worker is not None and worker.generate_count else 0,
        "aggregate_after_call": 3 if worker is not None and worker.generate_count else 2,
        "generation_facts": None if generation_facts is None else dict(generation_facts),
        "raw": {"status": "captured" if raw is not None else "not_captured", "identity": None if raw is None else _identity(raw, revision=f"{F4_LOCAL_SCHEMA_PREFIX}.raw.v1", identity_kind="raw_bytes")},
        "parse": parsed_status,
        "registry": registry_status,
        "failure_code": failure_code,
        "worker_teardown": teardown,
        "stderr_evidence": {
            "completed": stderr_snapshot.get("completed"),
            "error": stderr_snapshot.get("error"),
            "thread_joined": stderr_snapshot.get("thread_joined"),
        },
        "downstream": {"composition": "not_executed", "assembler": "not_executed", "acceptance": "not_executed", "repair": "not_executed", "g0": "not_executed", "package": "not_executed", "production_route": "not_executed"},
        "action_state": _f4_action_state(model_action=action_occurred),
    }
    _write_execution_result(prepared.result_root, result)
    return result


__all__ = [
    "F4_LOCAL_DIAGNOSTIC_ID",
    "F4_LOCAL_PILOT_ID",
    "F4_LOCAL_NODE_ID",
    "F4_LOCAL_POLICY_PATH",
    "F4_LOCAL_RESULT_NAME",
    "F4_LOCAL_FINAL_RESULT_NAME",
    "F4_RUNTIME_KIND",
    "F4LocalQwenPolicy",
    "F4LocalPreparedExperiment",
    "F4StreamMirror",
    "F4WorkerHandle",
    "F4WorkerFailure",
    "F4WorkerStartFailure",
    "Phase4LocalQwenF4ContractError",
    "build_f4_local_qwen_policy_payload",
    "load_f4_local_qwen_policy",
    "_replay_f3_savepoint_local",
    "prepare_phase4_local_qwen_f4",
    "run_phase4_local_qwen_f4",
    "start_f4_local_worker",
]
