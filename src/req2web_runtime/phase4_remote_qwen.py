"""P4-03D3 remote Qwen3.5-9B BF16 F3 completion pilot.

This module preserves the historical P4-03D2 result as an immutable failed
predecessor and creates a fresh one-call action-time identity.  The D3 worker
uses the full RTX 5090 BF16 profile with no quantization or CPU offload.  It
stops on a complete JSON object, model EOS, the model context boundary, or the
parent-owned wall-clock deadline; it has no fixed short output-token cap.
"""

from __future__ import annotations

import copy
import datetime as dt
import importlib
import importlib.metadata
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, ClassVar, Mapping, NamedTuple

from packaging.utils import canonicalize_name

from req2web_runtime import phase4_local_qwen as _local


Phase4RemoteQwenContractError = _local.Phase4LocalQwenContractError
_CanonicalRecord = _local._CanonicalRecord
_canonical_bytes = _local._canonical_bytes
_strict_json = _local._strict_json
_exact = _local._exact
_text = _local._text
_bool = _local._bool
_integer = _local._integer
_sha = _local._sha
_identity = _local._identity
_validate_identity = _local._validate_identity
_b64 = _local._b64
_decode_b64 = _local._decode_b64
_relative_path = _local._relative_path
_file_sha256 = _local._file_sha256
_normalize_hf_device_map = _local._normalize_hf_device_map
_validate_live_model_placement = _local._validate_live_model_placement
_NODE_OUTPUT_CONTRACTS = _local._NODE_OUTPUT_CONTRACTS
_NODE_PROMPT_GUIDANCE = _local._NODE_PROMPT_GUIDANCE
_worker_stdout_reader = _local._worker_stdout_reader
_worker_stderr_reader = _local._worker_stderr_reader
_WorkerStderrCapture = _local._WorkerStderrCapture
SupervisedWorkerFailure = _local.SupervisedWorkerFailure


REMOTE_SCHEMA_PREFIX = "req2web.phase4.p4_03d3"
REMOTE_DIAGNOSTIC_ID = "p4-03d3-remote-qwen-9b-bf16-f3-completion-pilot"
REMOTE_POLICY_SCHEMA_VERSION = (
    f"{REMOTE_SCHEMA_PREFIX}.remote_qwen_bf16_policy.v1"
)
REMOTE_POLICY_RELATIVE_PATH = "docs/phase4_remote_qwen_bf16_d3_policy.json"
REMOTE_POLICY_PATH = Path(__file__).resolve().parents[2] / REMOTE_POLICY_RELATIVE_PATH

REMOTE_PILOT_ID = "p4-03d3-remote-qwen-9b-bf16"
REMOTE_MODEL_ID = _local.QWEN_MODEL_ID
REMOTE_MODEL_REVISION = _local.QWEN_MODEL_REVISION
REMOTE_NODE_ID = "F3"
REMOTE_CASE_ID = "path3-commerce-checkout"
REMOTE_REQUEST_ID = "p4-02a-synthetic-request-001"
REMOTE_SOURCE_PILOT_ID = _local.P4R5_PILOT_ID
REMOTE_PRIOR_PILOT_ID = _local.P4R6_PILOT_ID

D2_SCHEMA_PREFIX = "req2web.phase4.p4_03d2"
D2_DIAGNOSTIC_ID = "p4-03d2-remote-qwen-9b-bf16-f3-pilot"
D2_PILOT_ID = "p4-03d2-remote-qwen-9b-bf16"
D2_RESULT_SCHEMA_VERSION = f"{D2_SCHEMA_PREFIX}.result.v1"
D2_RAW_IDENTITY_REVISION = f"{D2_SCHEMA_PREFIX}.complete_raw.v1"
D2_ACTION_STATE_VERSION = f"{D2_SCHEMA_PREFIX}.action_state.v1"
D2_RESULT_FILE_SHA256 = (
    "sha256:fbdb1b9a36f6893a9a572a2127f8c73b095820642acaa0231960a5cd33594e11"
)
D2_RAW_FILE_SHA256 = (
    "sha256:12ec0f8e0d19a20448bb25feb311f2165b9ea3eb3ec188ee51b5e98b7cefe198"
)
D2_RESULT_ID = (
    "sha256:cf94f6c4da4d7c9082ef6ac7e616d1c29c4efb60ef54d748f1673453c77e49cd"
)

REMOTE_PROFILE_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.profile.v1"
REMOTE_INVENTORY_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.inventory.v1"
REMOTE_RELOCATION_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.relocation.v1"
REMOTE_CHECKPOINT_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.checkpoint.v2"
REMOTE_DEPENDENCY_RUNTIME_SCHEMA_VERSION = (
    f"{REMOTE_SCHEMA_PREFIX}.langgraph_runtime.v1"
)
REMOTE_MANIFEST_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.manifest.v1"
REMOTE_INVOCATION_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.invocation.v1"
REMOTE_LOAD_RECEIPT_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.load_receipt.v1"
REMOTE_SUPERVISOR_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.supervisor.v1"
REMOTE_RESULT_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.result.v1"
REMOTE_REVALIDATION_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.revalidation.v1"
REMOTE_STDERR_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.stderr.v1"
REMOTE_RAW_IDENTITY_REVISION = f"{REMOTE_SCHEMA_PREFIX}.complete_raw.v1"
REMOTE_INPUT_REVISION = f"{REMOTE_SCHEMA_PREFIX}.f3_input.v1"
REMOTE_PROMPT_REVISION = f"{REMOTE_SCHEMA_PREFIX}.f3_prompt.v1"
REMOTE_CONFIG_REVISION = f"{REMOTE_SCHEMA_PREFIX}.runtime_config.v1"
REMOTE_REQUEST_REVISION = f"{REMOTE_SCHEMA_PREFIX}.request.v1"
REMOTE_WORKER_IPC_PROTOCOL = f"{REMOTE_SCHEMA_PREFIX}.worker-ipc.v1"
REMOTE_STREAM_EVENT_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.stream_event.v1"
REMOTE_MODEL_ROOT_IDENTITY_REVISION = f"{REMOTE_SCHEMA_PREFIX}.model_root.v1"
REMOTE_INVENTORY_ROWS_REVISION = f"{REMOTE_SCHEMA_PREFIX}.inventory.rows.v1"
REMOTE_PREDECESSOR_SCHEMA_VERSION = f"{REMOTE_SCHEMA_PREFIX}.d2_predecessor.v1"

REMOTE_POLICY_COPY_NAME = "remote_policy.json"
REMOTE_CHECKPOINT_PACKET_NAME = "checkpoint_packet.json"
REMOTE_CHECKPOINT_RECEIPT_NAME = "checkpoint_receipt.json"
REMOTE_PRIOR_FAILURE_NAME = "prior_f3_failure.json"
REMOTE_PREDECESSOR_RESULT_NAME = "predecessor_d2_result.json"
REMOTE_PREDECESSOR_RAW_NAME = "predecessor_d2_raw_response.bin"
REMOTE_PREDECESSOR_BINDING_NAME = "predecessor_d2_binding.json"
REMOTE_RELOCATION_NAME = "relocation_binding.json"
REMOTE_INVENTORY_NAME = "model_inventory.json"
REMOTE_PROFILE_NAME = "remote_profile.json"
REMOTE_MANIFEST_NAME = "remote_preflight_manifest.json"
REMOTE_INPUT_NAME = "f3_input.json"
REMOTE_PROMPT_NAME = "f3_prompt.json"
REMOTE_CONFIG_NAME = "runtime_config.json"
REMOTE_REQUEST_NAME = "remote_request.json"
REMOTE_RUNTIME_CLAIM_NAME = "runtime_start_claim.json"
REMOTE_LOAD_RECEIPT_NAME = "load_receipt.json"
REMOTE_INVOCATION_NAME = "generate_action.json"
REMOTE_RAW_NAME = "raw_response.bin"
REMOTE_STDERR_NAME = "worker_stderr.bin"
REMOTE_SUPERVISOR_NAME = "supervisor_receipt.json"
REMOTE_RESULT_NAME = "result.json"
REMOTE_REVALIDATION_NAME = "revalidation_result.json"
REMOTE_REVALIDATED_OUTPUT_NAME = "revalidated_f3_output.json"
REMOTE_REVALIDATED_STATE_NAME = "revalidated_f3_authority_state.json"
REMOTE_ROOT_MARKER_NAME = ".req2web-phase4-p4-03d3-result-root"

REMOTE_TIMEOUT_SECONDS = 1200
REMOTE_LOAD_TIMEOUT_SECONDS = 600
REMOTE_HEARTBEAT_INTERVAL_SECONDS = 10.0
REMOTE_RECEIVE_POLL_SECONDS = 0.5
REMOTE_GENERATE_CALL_CAP = 1
REMOTE_RETRY_COUNT = 0
REMOTE_FORMAL_FILE_COUNT = 16
REMOTE_MIN_VRAM_BYTES = 30_000_000_000
REMOTE_DEVICE_NAME = "NVIDIA GeForce RTX 5090"
REMOTE_MODEL_CONTEXT_TOKENS = 262_144
REMOTE_TRANSFORMERS_VERSION = "5.14.1"
REMOTE_TORCH_VERSION = "2.7.1+cu128"
REMOTE_ACCELERATE_VERSION = "1.14.0"
REMOTE_DTYPE = "bfloat16"
REMOTE_QUANTIZATION = "none"
REMOTE_DEVICE_MAP = {"": 0}
REMOTE_FIXED_MAX_NEW_TOKENS = None
REMOTE_GENERATION_STOP_POLICY = {
    "fixed_max_new_tokens": False,
    "stop_on_complete_json_object": True,
    "stop_on_model_eos": True,
    "model_context_limit_enforced": True,
    "parent_wall_clock_timeout_enforced": True,
}
REMOTE_PROJECTION_REVISION = _local.P4R6_PROJECTION_REVISION
REMOTE_OUTPUT_KEY_ORDER = [
    "local_id",
    "entity_type",
    "trigger_component_local_id",
    "source_state_local_id",
    "action",
    "target_state_local_id",
    "user_feedback",
    "refs",
]


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _remote_action_state(*, model_action: bool, remote_action: bool) -> dict[str, object]:
    return {
        "action_state_version": f"{REMOTE_SCHEMA_PREFIX}.action_state.v1",
        "runtime_kind": "remote_qwen_bf16_f3",
        "model_action": model_action,
        "graph_runtime_execution": False,
        "dependency_installation": False,
        "training": False,
        "data_authoring": False,
        "remote_action": remote_action,
        "network": False,
        "telemetry": False,
        "tracing": False,
        "local_files_only": True,
        "offline": True,
    }


def _validate_remote_action_state(
    value: object,
    name: str,
    *,
    model_action: bool,
    remote_action: bool,
) -> dict[str, object]:
    expected = _remote_action_state(
        model_action=model_action, remote_action=remote_action
    )
    data = _exact(value, tuple(expected), name)
    if data != expected:
        raise Phase4RemoteQwenContractError(f"{name} drifted")
    return data


class RemoteQwenPolicy(_CanonicalRecord):
    """Exact policy for the one approved remote BF16 F3 action."""

    KEYS = (
        "schema_version",
        "diagnostic_id",
        "current_date",
        "status",
        "authorization",
        "case",
        "model",
        "runtime",
        "checkpoint",
        "call_budget",
        "streaming",
        "result_boundaries",
        "claim_boundaries",
        "execution_boundaries",
    )
    SCHEMA_VERSION = REMOTE_POLICY_SCHEMA_VERSION

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="RemoteQwenPolicy")
        if (
            data["diagnostic_id"] != REMOTE_DIAGNOSTIC_ID
            or data["current_date"] != "2026-08-04"
            or data["status"] != "owner_authorized_single_remote_generate"
        ):
            raise Phase4RemoteQwenContractError("remote policy identity drifted")
        authorization = _exact(
            data["authorization"],
            (
                "authorized_generate_calls",
                "manual_foreground_observation",
                "owner_authorization_source",
                "requires_explicit_cli_confirmation",
                "remote_action",
                "model_action",
                "scope_note",
            ),
            "RemoteQwenPolicy.authorization",
        )
        if authorization != {
            "authorized_generate_calls": 1,
            "manual_foreground_observation": True,
            "owner_authorization_source": "current_project_owner_instruction_2026-08-04",
            "requires_explicit_cli_confirmation": True,
            "remote_action": True,
            "model_action": True,
            "scope_note": "remote_action=true means only this bounded remote execution; it is not a general remote or model authority",
        }:
            raise Phase4RemoteQwenContractError("remote policy authorization drifted")
        case = _exact(
            data["case"],
            ("case_id", "request_id", "node_id", "model_id", "model_revision"),
            "RemoteQwenPolicy.case",
        )
        if case != {
            "case_id": REMOTE_CASE_ID,
            "request_id": REMOTE_REQUEST_ID,
            "node_id": REMOTE_NODE_ID,
            "model_id": REMOTE_MODEL_ID,
            "model_revision": REMOTE_MODEL_REVISION,
        }:
            raise Phase4RemoteQwenContractError("remote policy case drifted")
        model = _exact(
            data["model"],
            (
                "model_id",
                "model_revision",
                "device_index",
                "device_name",
                "min_vram_bytes",
                "transformers_version",
                "torch_version",
                "accelerate_version",
                "bitsandbytes_required",
                "dtype",
                "quantization",
                "compute_dtype",
                "device_map",
                "cpu_offload",
            ),
            "RemoteQwenPolicy.model",
        )
        if model != {
            "model_id": REMOTE_MODEL_ID,
            "model_revision": REMOTE_MODEL_REVISION,
            "device_index": 0,
            "device_name": REMOTE_DEVICE_NAME,
            "min_vram_bytes": REMOTE_MIN_VRAM_BYTES,
            "transformers_version": REMOTE_TRANSFORMERS_VERSION,
            "torch_version": REMOTE_TORCH_VERSION,
            "accelerate_version": REMOTE_ACCELERATE_VERSION,
            "bitsandbytes_required": False,
            "dtype": REMOTE_DTYPE,
            "quantization": REMOTE_QUANTIZATION,
            "compute_dtype": REMOTE_DTYPE,
            "device_map": REMOTE_DEVICE_MAP,
            "cpu_offload": False,
        }:
            raise Phase4RemoteQwenContractError("remote policy model/runtime profile drifted")
        runtime = _exact(
            data["runtime"],
            (
                "local_files_only",
                "offline",
                "network",
                "telemetry",
                "tracing",
                "model_download",
                "model_load",
                "trust_remote_code",
            ),
            "RemoteQwenPolicy.runtime",
        )
        if runtime != {
            "local_files_only": True,
            "offline": True,
            "network": False,
            "telemetry": False,
            "tracing": False,
            "model_download": False,
            "model_load": True,
            "trust_remote_code": False,
        }:
            raise Phase4RemoteQwenContractError("remote policy runtime drifted")
        checkpoint = _exact(
            data["checkpoint"],
            (
                "requires_explicit_packet",
                "requires_explicit_receipt",
                "requires_prior_f3_failure",
                "requires_d2_predecessor_result",
                "requires_d2_predecessor_raw",
                "d2_predecessor_result_sha256",
                "d2_predecessor_raw_sha256",
                "d2_predecessor_result_id",
                "d2_predecessor_is_immutable_failure",
                "d3_is_not_retry",
                "d3_has_independent_call_budget",
                "source_pilot_id",
                "replay_authority",
                "replay_order",
                "receipt_is_not_authority",
                "relocation_binding_required",
                "source_path_is_not_remote_identity",
            ),
            "RemoteQwenPolicy.checkpoint",
        )
        if checkpoint != {
            "requires_explicit_packet": True,
            "requires_explicit_receipt": True,
            "requires_prior_f3_failure": True,
            "requires_d2_predecessor_result": True,
            "requires_d2_predecessor_raw": True,
            "d2_predecessor_result_sha256": D2_RESULT_FILE_SHA256,
            "d2_predecessor_raw_sha256": D2_RAW_FILE_SHA256,
            "d2_predecessor_result_id": D2_RESULT_ID,
            "d2_predecessor_is_immutable_failure": True,
            "d3_is_not_retry": True,
            "d3_has_independent_call_budget": True,
            "source_pilot_id": REMOTE_SOURCE_PILOT_ID,
            "replay_authority": "phase4_validate_node_output_then_phase4_register_node_output",
            "replay_order": ["F1", "F2"],
            "receipt_is_not_authority": True,
            "relocation_binding_required": True,
            "source_path_is_not_remote_identity": True,
        }:
            raise Phase4RemoteQwenContractError("remote policy checkpoint boundary drifted")
        call_budget = _exact(
            data["call_budget"],
            (
                "automatic_retry",
                "generate_call_cap",
                "retry_count",
                "timeout_seconds",
                "fixed_output_token_cap",
                "complete_json_stop",
                "model_eos_stop",
                "model_context_boundary_stop",
            ),
            "RemoteQwenPolicy.call_budget",
        )
        if call_budget != {
            "automatic_retry": False,
            "generate_call_cap": REMOTE_GENERATE_CALL_CAP,
            "retry_count": REMOTE_RETRY_COUNT,
            "timeout_seconds": REMOTE_TIMEOUT_SECONDS,
            "fixed_output_token_cap": False,
            "complete_json_stop": True,
            "model_eos_stop": True,
            "model_context_boundary_stop": True,
        }:
            raise Phase4RemoteQwenContractError("remote policy call budget drifted")
        streaming = _exact(
            data["streaming"],
            (
                "parent_low_latency_mirror",
                "worker_stdout",
                "worker_stderr",
                "heartbeat_seconds",
                "python_unbuffered",
                "tee_compatible",
            ),
            "RemoteQwenPolicy.streaming",
        )
        if streaming != {
            "parent_low_latency_mirror": True,
            "worker_stdout": "json_ipc_only",
            "worker_stderr": "structured_stage_and_token_delta_events",
            "heartbeat_seconds": 10,
            "python_unbuffered": True,
            "tee_compatible": True,
        }:
            raise Phase4RemoteQwenContractError("remote policy streaming boundary drifted")
        result = _exact(
            data["result_boundaries"],
            (
                "raw_first",
                "raw_fsync_before_parse",
                "node_model_pass_requires_parser_contract_registry",
                "integrated",
                "f4",
                "composition",
                "assembler",
                "downstream",
            ),
            "RemoteQwenPolicy.result_boundaries",
        )
        if result != {
            "raw_first": True,
            "raw_fsync_before_parse": True,
            "node_model_pass_requires_parser_contract_registry": True,
            "integrated": False,
            "f4": "not_executed",
            "composition": "not_executed",
            "assembler": "not_executed",
            "downstream": "not_executed",
        }:
            raise Phase4RemoteQwenContractError("remote policy result boundary drifted")
        claims = _exact(
            data["claim_boundaries"],
            ("formal_quality", "h1_or_gold", "training", "data_authoring", "studio", "server", "other_models"),
            "RemoteQwenPolicy.claim_boundaries",
        )
        if any(value is not False for value in claims.values()):
            raise Phase4RemoteQwenContractError("remote policy claim boundary drifted")
        execution = _exact(
            data["execution_boundaries"],
            ("remote_action", "model_download", "model_switch", "deterministic_repair", "automatic_retry"),
            "RemoteQwenPolicy.execution_boundaries",
        )
        if execution != {
            "remote_action": True,
            "model_download": False,
            "model_switch": False,
            "deterministic_repair": False,
            "automatic_retry": False,
        }:
            raise Phase4RemoteQwenContractError("remote policy execution boundary drifted")


def load_remote_qwen_policy() -> tuple[RemoteQwenPolicy, bytes]:
    """Read the tracked exact-key canonical policy without starting a worker."""

    try:
        raw = _local._read_tracked_canonical_record(
            REMOTE_POLICY_PATH, "remote Qwen BF16 policy"
        )
        payload = _strict_json(raw, require_canonical=True)
    except Phase4RemoteQwenContractError as exc:
        raise Phase4RemoteQwenContractError("remote policy is unavailable") from exc
    return RemoteQwenPolicy.from_dict(payload), raw  # type: ignore[return-value]


def _validate_d2_predecessor(
    *, result_raw: bytes, raw_response: bytes
) -> Mapping[str, object]:
    if _local._sha256(result_raw) != D2_RESULT_FILE_SHA256:
        raise Phase4RemoteQwenContractError(
            "D2 predecessor result file identity drifted"
        )
    if _local._sha256(raw_response) != D2_RAW_FILE_SHA256:
        raise Phase4RemoteQwenContractError(
            "D2 predecessor raw file identity drifted"
        )
    result = _exact(
        _strict_json(result_raw, require_canonical=True),
        (
            "schema_version",
            "result_id",
            "diagnostic_id",
            "pilot_id",
            "case_id",
            "request_id",
            "node_id",
            "generation_terminal",
            "supervisor_terminal_status",
            "call",
            "raw_capture",
            "parse",
            "node_contract",
            "registry",
            "node_model_pass",
            "integrated",
            "f4",
            "composition",
            "assembler",
            "downstream",
            "formal_quality",
            "h1_or_gold",
            "training",
            "data_authoring",
            "remote_action_occurred",
            "model_action_occurred",
            "checkpoint_binding_identity",
            "prior_failure_identity",
            "load_receipt_identity",
            "supervisor_identity",
            "failure_code",
            "source_kind",
            "action_state",
        ),
        "D2 predecessor result",
    )
    if (
        result["schema_version"] != D2_RESULT_SCHEMA_VERSION
        or result["result_id"] != D2_RESULT_ID
        or result["diagnostic_id"] != D2_DIAGNOSTIC_ID
        or result["pilot_id"] != D2_PILOT_ID
        or result["case_id"] != REMOTE_CASE_ID
        or result["request_id"] != REMOTE_REQUEST_ID
        or result["node_id"] != REMOTE_NODE_ID
        or result["generation_terminal"] != "generation_completed"
        or result["supervisor_terminal_status"] != "normal_completed"
        or result["failure_code"] != "node_contract_invalid"
        or result["source_kind"] != "remote_qwen_bf16"
    ):
        raise Phase4RemoteQwenContractError(
            "D2 predecessor scope/outcome drifted"
        )
    expected_result_id = _identity(
        {key: value for key, value in result.items() if key != "result_id"},
        revision=D2_RESULT_SCHEMA_VERSION,
    )["sha256"]
    if result["result_id"] != expected_result_id:
        raise Phase4RemoteQwenContractError(
            "D2 predecessor result identity is invalid"
        )
    call = _exact(
        result["call"],
        ("generate_calls", "generate_call_cap", "retry_count", "timeout_seconds"),
        "D2 predecessor call",
    )
    if call != {
        "generate_calls": 1,
        "generate_call_cap": 1,
        "retry_count": 0,
        "timeout_seconds": REMOTE_TIMEOUT_SECONDS,
    }:
        raise Phase4RemoteQwenContractError(
            "D2 predecessor call envelope drifted"
        )
    raw_capture = _exact(
        result["raw_capture"],
        ("status", "relative_path", "identity"),
        "D2 predecessor raw capture",
    )
    raw_identity = _validate_identity(
        raw_capture["identity"], "D2 predecessor raw identity"
    )
    if (
        raw_capture["status"] != "captured_authoritative_complete"
        or raw_capture["relative_path"] != "raw_response.bin"
        or raw_identity
        != _identity(
            raw_response,
            revision=D2_RAW_IDENTITY_REVISION,
            identity_kind="raw_bytes",
        )
    ):
        raise Phase4RemoteQwenContractError(
            "D2 predecessor raw binding drifted"
        )
    if (
        result["parse"] != {"status": "failed"}
        or result["node_contract"] != {"status": "not_executed"}
        or result["registry"] != {"status": "not_executed"}
        or result["node_model_pass"] is not False
        or result["integrated"] is not False
        or result["f4"] != "not_executed"
        or result["composition"] != "not_executed"
        or result["assembler"] != "not_executed"
        or result["downstream"] != "not_executed"
    ):
        raise Phase4RemoteQwenContractError(
            "D2 predecessor failure boundary drifted"
        )
    for key in (
        "formal_quality",
        "h1_or_gold",
        "training",
        "data_authoring",
    ):
        if result[key] is not False:
            raise Phase4RemoteQwenContractError(
                "D2 predecessor claim boundary drifted"
            )
    if (
        result["remote_action_occurred"] is not True
        or result["model_action_occurred"] is not True
    ):
        raise Phase4RemoteQwenContractError(
            "D2 predecessor action facts drifted"
        )
    action_state = _exact(
        result["action_state"],
        (
            "action_state_version",
            "runtime_kind",
            "model_action",
            "graph_runtime_execution",
            "dependency_installation",
            "training",
            "data_authoring",
            "remote_action",
            "network",
            "telemetry",
            "tracing",
            "offline",
            "local_files_only",
        ),
        "D2 predecessor action state",
    )
    if action_state != {
        "action_state_version": D2_ACTION_STATE_VERSION,
        "runtime_kind": "remote_qwen_bf16_f3",
        "model_action": True,
        "graph_runtime_execution": False,
        "dependency_installation": False,
        "training": False,
        "data_authoring": False,
        "remote_action": True,
        "network": False,
        "telemetry": False,
        "tracing": False,
        "offline": True,
        "local_files_only": True,
    }:
        raise Phase4RemoteQwenContractError(
            "D2 predecessor action state drifted"
        )
    return result


class RemoteD2PredecessorBinding(_CanonicalRecord):
    """Exact immutable binding to the completed-but-truncated D2 attempt."""

    KEYS = (
        "schema_version",
        "binding_id",
        "diagnostic_id",
        "pilot_id",
        "case_id",
        "request_id",
        "node_id",
        "predecessor_result_file_identity",
        "predecessor_raw_identity",
        "predecessor_result_id",
        "predecessor_generation_terminal",
        "predecessor_failure_code",
        "predecessor_node_model_pass",
        "immutable_predecessor_failure",
        "d3_is_not_retry",
        "d3_has_independent_call_budget",
        "action_state",
    )
    SCHEMA_VERSION = REMOTE_PREDECESSOR_SCHEMA_VERSION

    @classmethod
    def create(
        cls, *, result_raw: bytes, raw_response: bytes
    ) -> "RemoteD2PredecessorBinding":
        result = _validate_d2_predecessor(
            result_raw=result_raw, raw_response=raw_response
        )
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "binding_id": "pending",
            "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
            "pilot_id": REMOTE_PILOT_ID,
            "case_id": REMOTE_CASE_ID,
            "request_id": REMOTE_REQUEST_ID,
            "node_id": REMOTE_NODE_ID,
            "predecessor_result_file_identity": _identity(
                result_raw,
                revision=f"{D2_SCHEMA_PREFIX}.result_file.v1",
                identity_kind="raw_bytes",
            ),
            "predecessor_raw_identity": _identity(
                raw_response,
                revision=D2_RAW_IDENTITY_REVISION,
                identity_kind="raw_bytes",
            ),
            "predecessor_result_id": result["result_id"],
            "predecessor_generation_terminal": result[
                "generation_terminal"
            ],
            "predecessor_failure_code": result["failure_code"],
            "predecessor_node_model_pass": result["node_model_pass"],
            "immutable_predecessor_failure": True,
            "d3_is_not_retry": True,
            "d3_has_independent_call_budget": True,
            "action_state": _remote_action_state(
                model_action=False, remote_action=False
            ),
        }
        root["binding_id"] = _identity(
            {key: value for key, value in root.items() if key != "binding_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(
            data, schema=cls.SCHEMA_VERSION, name="RemoteD2PredecessorBinding"
        )
        _sha(data["binding_id"], "RemoteD2PredecessorBinding.binding_id")
        if (
            data["diagnostic_id"] != REMOTE_DIAGNOSTIC_ID
            or data["pilot_id"] != REMOTE_PILOT_ID
            or data["case_id"] != REMOTE_CASE_ID
            or data["request_id"] != REMOTE_REQUEST_ID
            or data["node_id"] != REMOTE_NODE_ID
            or data["predecessor_result_id"] != D2_RESULT_ID
            or data["predecessor_generation_terminal"]
            != "generation_completed"
            or data["predecessor_failure_code"] != "node_contract_invalid"
            or data["predecessor_node_model_pass"] is not False
            or data["immutable_predecessor_failure"] is not True
            or data["d3_is_not_retry"] is not True
            or data["d3_has_independent_call_budget"] is not True
        ):
            raise Phase4RemoteQwenContractError(
                "D2 predecessor binding drifted"
            )
        result_identity = _validate_identity(
            data["predecessor_result_file_identity"],
            "RemoteD2PredecessorBinding.predecessor_result_file_identity",
        )
        raw_identity = _validate_identity(
            data["predecessor_raw_identity"],
            "RemoteD2PredecessorBinding.predecessor_raw_identity",
        )
        if (
            result_identity["sha256"] != D2_RESULT_FILE_SHA256
            or raw_identity["sha256"] != D2_RAW_FILE_SHA256
            or result_identity["identity_kind"] != "raw_bytes"
            or raw_identity["identity_kind"] != "raw_bytes"
            or raw_identity["revision"] != D2_RAW_IDENTITY_REVISION
        ):
            raise Phase4RemoteQwenContractError(
                "D2 predecessor file identity drifted"
            )
        _validate_remote_action_state(
            data["action_state"],
            "RemoteD2PredecessorBinding.action_state",
            model_action=False,
            remote_action=False,
        )
        expected = _identity(
            {key: value for key, value in data.items() if key != "binding_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["binding_id"] != expected:
            raise Phase4RemoteQwenContractError(
                "D2 predecessor binding identity drifted"
            )

    def validate_against(
        self, *, result_raw: bytes, raw_response: bytes
    ) -> None:
        self.validate()
        expected = type(self).create(
            result_raw=result_raw, raw_response=raw_response
        )
        if self.to_dict() != expected.to_dict():
            raise Phase4RemoteQwenContractError(
                "D2 predecessor live binding drifted"
            )


class RemoteQwenProfile(_CanonicalRecord):
    """Action-time BF16 profile; model-root identity is content-only."""

    KEYS = (
        "schema_version",
        "profile_id",
        "profile_name",
        "model_id",
        "model_revision",
        "model_root_identity",
        "model_inventory_identity",
        "model_file_count",
        "python_version",
        "transformers_version",
        "torch_version",
        "accelerate_version",
        "bitsandbytes_required",
        "device_index",
        "device_name",
        "device_uuid",
        "total_vram_bytes",
        "free_vram_bytes_at_preflight",
        "driver_version",
        "cuda_version",
        "dtype",
        "quantization",
        "compute_dtype",
        "device_map",
        "cpu_offload",
        "local_files_only",
        "offline",
        "network",
        "telemetry",
        "tracing",
        "max_input_tokens",
        "model_context_tokens",
        "fixed_max_new_tokens",
        "generation_stop_policy",
        "timeout_seconds",
        "seed",
        "decode",
        "model_loaded",
        "run_occurred",
    )
    SCHEMA_VERSION = REMOTE_PROFILE_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        model_root_identity: Mapping[str, object],
        model_inventory_identity: Mapping[str, object],
        model_file_count: int,
        runtime_facts: Mapping[str, object],
        gpu_facts: Mapping[str, object],
        max_input_tokens: int = 8192,
        model_context_tokens: int = REMOTE_MODEL_CONTEXT_TOKENS,
        timeout_seconds: int = REMOTE_TIMEOUT_SECONDS,
        seed: int = 0,
    ) -> "RemoteQwenProfile":
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "profile_id": "pending",
            "profile_name": "remote_bf16_f3",
            "model_id": REMOTE_MODEL_ID,
            "model_revision": REMOTE_MODEL_REVISION,
            "model_root_identity": dict(model_root_identity),
            "model_inventory_identity": dict(model_inventory_identity),
            "model_file_count": model_file_count,
            "python_version": runtime_facts["python_version"],
            "transformers_version": runtime_facts["transformers_version"],
            "torch_version": runtime_facts["torch_version"],
            "accelerate_version": runtime_facts["accelerate_version"],
            "bitsandbytes_required": False,
            "device_index": 0,
            "device_name": gpu_facts["device_name"],
            "device_uuid": gpu_facts["device_uuid"],
            "total_vram_bytes": gpu_facts["total_vram_bytes"],
            "free_vram_bytes_at_preflight": gpu_facts["free_vram_bytes"],
            "driver_version": gpu_facts["driver_version"],
            "cuda_version": gpu_facts["cuda_version"],
            "dtype": REMOTE_DTYPE,
            "quantization": REMOTE_QUANTIZATION,
            "compute_dtype": REMOTE_DTYPE,
            "device_map": dict(REMOTE_DEVICE_MAP),
            "cpu_offload": False,
            "local_files_only": True,
            "offline": True,
            "network": False,
            "telemetry": False,
            "tracing": False,
            "max_input_tokens": max_input_tokens,
            "model_context_tokens": model_context_tokens,
            "fixed_max_new_tokens": REMOTE_FIXED_MAX_NEW_TOKENS,
            "generation_stop_policy": copy.deepcopy(
                REMOTE_GENERATION_STOP_POLICY
            ),
            "timeout_seconds": timeout_seconds,
            "seed": seed,
            "decode": {"do_sample": False, "temperature": 0.0, "top_p": 1.0},
            "model_loaded": False,
            "run_occurred": False,
        }
        root["profile_id"] = _identity(
            {key: value for key, value in root.items() if key != "profile_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="RemoteQwenProfile")
        _sha(data["profile_id"], "RemoteQwenProfile.profile_id")
        if data["profile_name"] != "remote_bf16_f3":
            raise Phase4RemoteQwenContractError("remote profile name drifted")
        if data["model_id"] != REMOTE_MODEL_ID or data["model_revision"] != REMOTE_MODEL_REVISION:
            raise Phase4RemoteQwenContractError("remote profile model identity drifted")
        root_identity = _validate_identity(
            data["model_root_identity"], "RemoteQwenProfile.model_root_identity"
        )
        inventory_identity = _validate_identity(
            data["model_inventory_identity"],
            "RemoteQwenProfile.model_inventory_identity",
        )
        if root_identity["identity_kind"] != "canonical_tree" or inventory_identity["identity_kind"] != "canonical_row_list":
            raise Phase4RemoteQwenContractError("remote profile root identity domain drifted")
        if (
            root_identity["revision"] != REMOTE_MODEL_ROOT_IDENTITY_REVISION
            or inventory_identity["revision"] != REMOTE_INVENTORY_ROWS_REVISION
        ):
            raise Phase4RemoteQwenContractError("remote profile identity revisions drifted")
        if _integer(data["model_file_count"], "RemoteQwenProfile.model_file_count", minimum=1) != REMOTE_FORMAL_FILE_COUNT:
            raise Phase4RemoteQwenContractError("remote profile formal file count drifted")
        for key in (
            "python_version",
            "transformers_version",
            "torch_version",
            "accelerate_version",
            "device_uuid",
            "driver_version",
            "cuda_version",
        ):
            _text(data[key], f"RemoteQwenProfile.{key}")
        if (
            data["transformers_version"] != REMOTE_TRANSFORMERS_VERSION
            or data["torch_version"] != REMOTE_TORCH_VERSION
            or data["accelerate_version"] != REMOTE_ACCELERATE_VERSION
            or data["bitsandbytes_required"] is not False
        ):
            raise Phase4RemoteQwenContractError("remote profile dependency facts drifted")
        if _integer(data["device_index"], "RemoteQwenProfile.device_index", minimum=0) != 0:
            raise Phase4RemoteQwenContractError("remote profile device index drifted")
        if "RTX 5090" not in _text(data["device_name"], "RemoteQwenProfile.device_name"):
            raise Phase4RemoteQwenContractError("remote profile requires RTX 5090")
        total = _integer(data["total_vram_bytes"], "RemoteQwenProfile.total_vram_bytes", minimum=REMOTE_MIN_VRAM_BYTES)
        free = _integer(data["free_vram_bytes_at_preflight"], "RemoteQwenProfile.free_vram_bytes_at_preflight", minimum=0)
        if free > total:
            raise Phase4RemoteQwenContractError("remote profile free VRAM exceeds total VRAM")
        for key, expected in (
            ("dtype", REMOTE_DTYPE),
            ("quantization", REMOTE_QUANTIZATION),
            ("compute_dtype", REMOTE_DTYPE),
        ):
            if data[key] != expected:
                raise Phase4RemoteQwenContractError(f"RemoteQwenProfile.{key} drifted")
        if data["device_map"] != REMOTE_DEVICE_MAP or data["cpu_offload"] is not False:
            raise Phase4RemoteQwenContractError("remote profile placement/quantization drifted")
        for key, expected in (
            ("local_files_only", True),
            ("offline", True),
            ("network", False),
            ("telemetry", False),
            ("tracing", False),
            ("model_loaded", False),
            ("run_occurred", False),
        ):
            if type(data[key]) is not type(expected) or data[key] != expected:
                raise Phase4RemoteQwenContractError(f"RemoteQwenProfile.{key} drifted")
        for key in ("max_input_tokens", "model_context_tokens", "timeout_seconds"):
            _integer(data[key], f"RemoteQwenProfile.{key}", minimum=1)
        if (
            data["model_context_tokens"] != REMOTE_MODEL_CONTEXT_TOKENS
            or data["fixed_max_new_tokens"] is not REMOTE_FIXED_MAX_NEW_TOKENS
            or data["generation_stop_policy"] != REMOTE_GENERATION_STOP_POLICY
            or data["timeout_seconds"] != REMOTE_TIMEOUT_SECONDS
        ):
            raise Phase4RemoteQwenContractError("remote profile generation caps drifted")
        _integer(data["seed"], "RemoteQwenProfile.seed", minimum=0)
        decode = _exact(data["decode"], ("do_sample", "temperature", "top_p"), "RemoteQwenProfile.decode")
        if decode != {"do_sample": False, "temperature": 0.0, "top_p": 1.0}:
            raise Phase4RemoteQwenContractError("remote profile decode drifted")
        expected_id = _identity(
            {key: data[key] for key in data if key != "profile_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["profile_id"] != expected_id:
            raise Phase4RemoteQwenContractError("RemoteQwenProfile.profile_id drifted")


class RemoteLoadReceipt(_CanonicalRecord):
    """Live facts proving that the worker loaded the exact BF16 placement."""

    KEYS = (
        "schema_version",
        "receipt_id",
        "diagnostic_id",
        "profile_identity",
        "loaded_facts",
        "model_loaded",
        "generation_occurred",
        "action_state",
    )
    SCHEMA_VERSION = REMOTE_LOAD_RECEIPT_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        profile: RemoteQwenProfile,
        loaded_facts: Mapping[str, object],
    ) -> "RemoteLoadReceipt":
        profile.validate()
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "receipt_id": "pending",
            "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
            "profile_identity": _identity(profile.to_dict(), revision=REMOTE_PROFILE_SCHEMA_VERSION),
            "loaded_facts": copy.deepcopy(dict(loaded_facts)),
            "model_loaded": True,
            "generation_occurred": False,
            "action_state": _remote_action_state(model_action=True, remote_action=True),
        }
        root["receipt_id"] = _identity(
            {key: value for key, value in root.items() if key != "receipt_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="RemoteLoadReceipt")
        _sha(data["receipt_id"], "RemoteLoadReceipt.receipt_id")
        if data["diagnostic_id"] != REMOTE_DIAGNOSTIC_ID:
            raise Phase4RemoteQwenContractError("remote load receipt diagnostic drifted")
        _validate_identity(data["profile_identity"], "RemoteLoadReceipt.profile_identity")
        facts = _exact(
            data["loaded_facts"],
            (
                "model_class",
                "processor_class",
                "is_loaded_in_4bit",
                "is_loaded_in_8bit",
                "quantization",
                "hf_device_map",
                "parameter_devices",
                "parameter_dtypes",
                "compute_dtypes",
                "cpu_offload",
                "device",
                "dtype",
                "model_context_tokens",
                "model_root_identity",
            ),
            "RemoteLoadReceipt.loaded_facts",
        )
        normalized = _normalize_hf_device_map(facts["hf_device_map"])
        if (
            facts["model_class"] != "Qwen3_5ForConditionalGeneration"
            or facts["processor_class"] != "Qwen3VLProcessor"
            or facts["is_loaded_in_4bit"] is not False
            or facts["is_loaded_in_8bit"] is not False
            or facts["quantization"] != REMOTE_QUANTIZATION
            or facts["hf_device_map"] != normalized
            or facts["parameter_devices"] != ["cuda:0"]
            or facts["parameter_dtypes"] != ["torch.bfloat16"]
            or facts["compute_dtypes"] not in ([], ["torch.bfloat16"])
            or facts["cpu_offload"] is not False
            or facts["device"] != "cuda:0"
            or facts["dtype"] != "torch.bfloat16"
            or facts["model_context_tokens"] != REMOTE_MODEL_CONTEXT_TOKENS
        ):
            raise Phase4RemoteQwenContractError("remote loaded BF16 facts drifted")
        _validate_identity(facts["model_root_identity"], "RemoteLoadReceipt.model_root_identity")
        if data["model_loaded"] is not True or data["generation_occurred"] is not False:
            raise Phase4RemoteQwenContractError("remote load receipt state drifted")
        _validate_remote_action_state(
            data["action_state"],
            "RemoteLoadReceipt.action_state",
            model_action=True,
            remote_action=True,
        )
        expected = _identity(
            {key: data[key] for key in data if key != "receipt_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["receipt_id"] != expected:
            raise Phase4RemoteQwenContractError("remote load receipt identity drifted")

    def validate_against(self, profile: RemoteQwenProfile) -> None:
        self.validate()
        if self.profile_identity != _identity(profile.to_dict(), revision=REMOTE_PROFILE_SCHEMA_VERSION):
            raise Phase4RemoteQwenContractError("remote load/profile binding drifted")
        if self.loaded_facts["model_root_identity"] != profile.model_root_identity:
            raise Phase4RemoteQwenContractError("remote load/model-root binding drifted")


class RemoteCheckpointBinding(_CanonicalRecord):
    """Source/relocation binding for imported F1/F2 checkpoint bytes."""

    KEYS = (
        "schema_version",
        "binding_id",
        "diagnostic_id",
        "target_pilot_id",
        "case",
        "source",
        "relocation",
        "dependency_runtime",
        "prior_failure",
        "authority_state_identity",
        "node_raw_identities",
        "action_state",
    )
    SCHEMA_VERSION = REMOTE_CHECKPOINT_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        packet: _local.P4R5CheckpointPacket,
        receipt: _local.P4R5CheckpointReceipt,
        packet_raw: bytes,
        receipt_raw: bytes,
        prior_failure: _local.AttemptResult,
        prior_raw: bytes,
        authority_state: Mapping[str, object],
        dependency_runtime: Mapping[str, object],
    ) -> "RemoteCheckpointBinding":
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "binding_id": "pending",
            "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
            "target_pilot_id": REMOTE_PILOT_ID,
            "case": {
                "case_id": packet.case_id,
                "request_id": packet.request_id,
                "node_id": REMOTE_NODE_ID,
                "model_id": packet.model_id,
                "model_revision": packet.model_revision,
            },
            "source": {
                "source_pilot_id": packet.pilot_id,
                "packet_identity": _identity(packet_raw, revision=_local.P4R5_CHECKPOINT_PACKET_SCHEMA_VERSION, identity_kind="raw_bytes"),
                "receipt_identity": _identity(receipt_raw, revision=_local.P4R5_CHECKPOINT_RECEIPT_SCHEMA_VERSION, identity_kind="raw_bytes"),
                "source_case_id": packet.case_id,
                "source_request_id": packet.request_id,
                "source_authority_state_identity": dict(packet.authority_state_identity),
                "receipt_is_not_authority": True,
            },
            "relocation": {
                "schema_version": REMOTE_RELOCATION_SCHEMA_VERSION,
                "source_pilot_id": packet.pilot_id,
                "target_pilot_id": REMOTE_PILOT_ID,
                "raw_bytes_preserved": True,
                "raw_bytes_rebound_to_target_scope": True,
                "source_path_is_not_remote_identity": True,
                "remote_identity_basis": "live_target_content_hashes_and_checkpoint_bytes",
            },
            "dependency_runtime": copy.deepcopy(dict(dependency_runtime)),
            "prior_failure": {
                "result_identity": _identity(prior_raw, revision=_local.ATTEMPT_RESULT_SCHEMA_VERSION, identity_kind="raw_bytes"),
                "result_id": prior_failure.result_id,
                "failure_code": prior_failure.failure_code,
                "attempt_index": prior_failure.attempt_index,
                "raw_status": prior_failure.raw_status,
            },
            "authority_state_identity": _identity(
                authority_state,
                revision=f"{_local.P4_03_SCHEMA_PREFIX}.r5.checkpoint_authority_state.v1",
            ),
            "node_raw_identities": {
                node_id: dict(row["raw_identity"])
                for node_id, row in zip(packet.seed_nodes, packet.nodes, strict=True)
            },
            "action_state": _remote_action_state(model_action=False, remote_action=False),
        }
        root["binding_id"] = _identity(
            {key: value for key, value in root.items() if key != "binding_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="RemoteCheckpointBinding")
        _sha(data["binding_id"], "RemoteCheckpointBinding.binding_id")
        if data["diagnostic_id"] != REMOTE_DIAGNOSTIC_ID or data["target_pilot_id"] != REMOTE_PILOT_ID:
            raise Phase4RemoteQwenContractError("remote checkpoint binding identity drifted")
        case = _exact(data["case"], ("case_id", "request_id", "node_id", "model_id", "model_revision"), "RemoteCheckpointBinding.case")
        if case != {
            "case_id": REMOTE_CASE_ID,
            "request_id": REMOTE_REQUEST_ID,
            "node_id": REMOTE_NODE_ID,
            "model_id": REMOTE_MODEL_ID,
            "model_revision": REMOTE_MODEL_REVISION,
        }:
            raise Phase4RemoteQwenContractError("remote checkpoint case drifted")
        source = _exact(
            data["source"],
            ("source_pilot_id", "packet_identity", "receipt_identity", "source_case_id", "source_request_id", "source_authority_state_identity", "receipt_is_not_authority"),
            "RemoteCheckpointBinding.source",
        )
        if source["source_pilot_id"] != REMOTE_SOURCE_PILOT_ID or source["source_case_id"] != REMOTE_CASE_ID or source["source_request_id"] != REMOTE_REQUEST_ID or source["receipt_is_not_authority"] is not True:
            raise Phase4RemoteQwenContractError("remote checkpoint source scope drifted")
        for key in ("packet_identity", "receipt_identity", "source_authority_state_identity"):
            _validate_identity(source[key], f"RemoteCheckpointBinding.source.{key}")
        relocation = _exact(
            data["relocation"],
            ("schema_version", "source_pilot_id", "target_pilot_id", "raw_bytes_preserved", "raw_bytes_rebound_to_target_scope", "source_path_is_not_remote_identity", "remote_identity_basis"),
            "RemoteCheckpointBinding.relocation",
        )
        if relocation != {
            "schema_version": REMOTE_RELOCATION_SCHEMA_VERSION,
            "source_pilot_id": REMOTE_SOURCE_PILOT_ID,
            "target_pilot_id": REMOTE_PILOT_ID,
            "raw_bytes_preserved": True,
            "raw_bytes_rebound_to_target_scope": True,
            "source_path_is_not_remote_identity": True,
            "remote_identity_basis": "live_target_content_hashes_and_checkpoint_bytes",
        }:
            raise Phase4RemoteQwenContractError("remote checkpoint relocation drifted")
        dependency_runtime = _exact(
            data["dependency_runtime"],
            (
                "schema_version",
                "receipt_identity",
                "expected_name_version_identity",
                "live_name_version_identity",
                "live_record_closure_identity",
                "live_file_inventory_identity",
                "platform_record_digests_are_not_receipt_authority",
            ),
            "RemoteCheckpointBinding.dependency_runtime",
        )
        if (
            dependency_runtime["schema_version"]
            != REMOTE_DEPENDENCY_RUNTIME_SCHEMA_VERSION
            or dependency_runtime[
                "platform_record_digests_are_not_receipt_authority"
            ]
            is not True
        ):
            raise Phase4RemoteQwenContractError(
                "remote dependency runtime scope drifted"
            )
        for key in (
            "receipt_identity",
            "expected_name_version_identity",
            "live_name_version_identity",
            "live_record_closure_identity",
            "live_file_inventory_identity",
        ):
            _validate_identity(
                dependency_runtime[key],
                f"RemoteCheckpointBinding.dependency_runtime.{key}",
            )
        if (
            dependency_runtime["expected_name_version_identity"]
            != dependency_runtime["live_name_version_identity"]
        ):
            raise Phase4RemoteQwenContractError(
                "remote dependency name/version identity drifted"
            )
        prior = _exact(data["prior_failure"], ("result_identity", "result_id", "failure_code", "attempt_index", "raw_status"), "RemoteCheckpointBinding.prior_failure")
        _validate_identity(prior["result_identity"], "RemoteCheckpointBinding.prior_failure.result_identity")
        _sha(prior["result_id"], "RemoteCheckpointBinding.prior_failure.result_id")
        if prior["failure_code"] != "generation_timeout" or prior["attempt_index"] != 1 or prior["raw_status"] != "not_captured":
            raise Phase4RemoteQwenContractError("remote prior F3 failure drifted")
        _validate_identity(data["authority_state_identity"], "RemoteCheckpointBinding.authority_state_identity")
        raws = _exact(data["node_raw_identities"], ("F1", "F2"), "RemoteCheckpointBinding.node_raw_identities")
        for node_id in ("F1", "F2"):
            _validate_identity(raws[node_id], f"RemoteCheckpointBinding.{node_id}.raw_identity")
        _validate_remote_action_state(
            data["action_state"],
            "RemoteCheckpointBinding.action_state",
            model_action=False,
            remote_action=False,
        )
        expected = _identity(
            {key: data[key] for key in data if key != "binding_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["binding_id"] != expected:
            raise Phase4RemoteQwenContractError("remote checkpoint binding identity drifted")


class RemoteCheckpointReplay(NamedTuple):
    packet: _local.P4R5CheckpointPacket
    receipt: _local.P4R5CheckpointReceipt
    prior_failure: _local.AttemptResult
    packet_raw: bytes
    receipt_raw: bytes
    prior_raw: bytes
    authority_state: Mapping[str, object]
    outputs: Mapping[str, bytes]
    binding: RemoteCheckpointBinding


def _read_input_file(path: Path, name: str) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Phase4RemoteQwenContractError(f"{name} must be an existing non-symlink file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Phase4RemoteQwenContractError(f"cannot read {name}") from exc


def _validate_remote_prior_failure(prior: _local.AttemptResult) -> _local.AttemptResult:
    if type(prior) is not _local.AttemptResult:
        raise Phase4RemoteQwenContractError("remote prior F3 failure type drifted")
    # This is an input to D2, never a mutable budget.  Reuse the owning R6
    # validator so an arbitrary failed file cannot reset the historical run.
    return _local._validate_p4r6_timeout_prior_attempt(prior)


def _validate_remote_langgraph_closure() -> dict[str, object]:
    """Validate remote LangGraph by portable package identity, not RECORD bytes."""

    from req2web_orchestration.phase4_graph import (
        DEPENDENCY_RECEIPT_REVISION,
        _installed_langgraph_state,
        _validate_dependency_acquisition_receipt,
    )

    receipt = _validate_dependency_acquisition_receipt(verify_installed_files=False)
    installed, file_inventory = _installed_langgraph_state()
    expected_pairs = sorted(
        [
            {
            "distribution": canonicalize_name(str(row["distribution"])),
            "version": str(row["version"]),
            }
            for row in receipt["resolved_closure"]
        ],
        key=lambda row: (row["distribution"], row["version"]),
    )
    actual_pairs = sorted(
        [
            {
                "distribution": canonicalize_name(str(row["distribution"])),
                "version": str(row["version"]),
            }
            for row in installed
        ],
        key=lambda row: (row["distribution"], row["version"]),
    )
    if actual_pairs != expected_pairs:
        raise Phase4RemoteQwenContractError(
            "remote LangGraph dependency names or versions drifted"
        )
    expected_identity = _identity(
        expected_pairs,
        revision=f"{REMOTE_SCHEMA_PREFIX}.langgraph-name-version.v1",
        identity_kind="canonical_row_list",
    )
    live_identity = _identity(
        actual_pairs,
        revision=f"{REMOTE_SCHEMA_PREFIX}.langgraph-name-version.v1",
        identity_kind="canonical_row_list",
    )
    return {
        "schema_version": REMOTE_DEPENDENCY_RUNTIME_SCHEMA_VERSION,
        "receipt_identity": _identity(
            receipt,
            revision=DEPENDENCY_RECEIPT_REVISION,
        ),
        "expected_name_version_identity": expected_identity,
        "live_name_version_identity": live_identity,
        "live_record_closure_identity": _identity(
            installed,
            revision=f"{REMOTE_SCHEMA_PREFIX}.langgraph-live-closure.v1",
            identity_kind="canonical_row_list",
        ),
        "live_file_inventory_identity": _identity(
            file_inventory,
            revision=f"{REMOTE_SCHEMA_PREFIX}.langgraph-live-files.v1",
            identity_kind="canonical_row_list",
        ),
        "platform_record_digests_are_not_receipt_authority": True,
    }


def replay_remote_checkpoint(
    *,
    checkpoint_packet: Path,
    checkpoint_receipt: Path,
    prior_f3_failure: Path,
) -> RemoteCheckpointReplay:
    """Replay F1/F2 through the existing validator and registry authority."""

    packet_raw = _read_input_file(checkpoint_packet, "checkpoint packet")
    receipt_raw = _read_input_file(checkpoint_receipt, "checkpoint receipt")
    prior_raw = _read_input_file(prior_f3_failure, "prior F3 failure")
    packet = _local.P4R5CheckpointPacket.from_bytes(packet_raw)
    receipt = _local.P4R5CheckpointReceipt.from_bytes(receipt_raw)
    if receipt.packet_identity != _identity(
        packet.to_dict(), revision=_local.P4R5_CHECKPOINT_PACKET_SCHEMA_VERSION
    ):
        raise Phase4RemoteQwenContractError("checkpoint receipt does not bind packet")
    if packet.case_id != REMOTE_CASE_ID or packet.request_id != REMOTE_REQUEST_ID or packet.model_id != REMOTE_MODEL_ID or packet.model_revision != REMOTE_MODEL_REVISION:
        raise Phase4RemoteQwenContractError("checkpoint case/model scope drifted")
    for node_id, row in zip(("F1", "F2"), packet.nodes, strict=True):
        receipt_raw_identity = receipt.node_raw_identities[node_id]
        if receipt_raw_identity != row["raw_identity"] or receipt.node_refs[node_id] != row["ref"]:
            raise Phase4RemoteQwenContractError("checkpoint receipt node binding drifted")
    prior = _validate_remote_prior_failure(_local.AttemptResult.from_bytes(prior_raw))
    if prior.case_id != REMOTE_CASE_ID or prior.request_id != REMOTE_REQUEST_ID or prior.node_id != REMOTE_NODE_ID:
        raise Phase4RemoteQwenContractError("prior F3 failure scope drifted")
    from req2web_orchestration.phase4_graph import (
        phase4_create_portable_authority_state,
        phase4_register_node_output,
        phase4_validate_node_output,
        synthetic_commerce_b_input,
        validate_b_input,
    )

    b_input = validate_b_input(synthetic_commerce_b_input())
    if packet.case_id != b_input["case_id"] or packet.request_id != b_input["request_id"]:
        raise Phase4RemoteQwenContractError("checkpoint does not bind the canonical case")
    dependency_runtime = _validate_remote_langgraph_closure()
    state: Mapping[str, object] = phase4_create_portable_authority_state(b_input)
    outputs: dict[str, bytes] = {}
    for node_id, row in zip(("F1", "F2"), packet.nodes, strict=True):
        raw = _decode_b64(row["raw_b64"], f"checkpoint.{node_id}.raw_b64")
        parsed = _strict_json(raw, require_canonical=False)
        validated = phase4_validate_node_output(node_id, parsed, state)
        state = phase4_register_node_output(state, node_id, validated)
        outputs[node_id] = raw
    state_identity = _identity(
        state, revision=f"{_local.P4_03_SCHEMA_PREFIX}.r5.checkpoint_authority_state.v1"
    )
    if state_identity != packet.authority_state_identity:
        raise Phase4RemoteQwenContractError("checkpoint authority replay drifted")
    binding = RemoteCheckpointBinding.create(
        packet=packet,
        receipt=receipt,
        packet_raw=packet_raw,
        receipt_raw=receipt_raw,
        prior_failure=prior,
        prior_raw=prior_raw,
        authority_state=state,
        dependency_runtime=dependency_runtime,
    )
    return RemoteCheckpointReplay(
        packet=packet,
        receipt=receipt,
        prior_failure=prior,
        packet_raw=packet_raw,
        receipt_raw=receipt_raw,
        prior_raw=prior_raw,
        authority_state=state,
        outputs=outputs,
        binding=binding,
    )


def validate_remote_model_inventory(
    *, model_root: Path, integrity_evidence: Path
) -> dict[str, object]:
    """Relocate source evidence, then live-hash exactly sixteen target files."""

    if not isinstance(model_root, Path) or not isinstance(integrity_evidence, Path):
        raise Phase4RemoteQwenContractError("remote model root/evidence must be Path objects")
    if not model_root.is_absolute() or model_root.is_symlink() or not model_root.is_dir():
        raise Phase4RemoteQwenContractError("remote model root is unavailable or symlinked")
    evidence_raw = _read_input_file(integrity_evidence, "integrity evidence")
    data = _exact(
        _strict_json(evidence_raw, require_canonical=False),
        (
            "repo_id", "requested_revision", "resolved_sha", "root",
            "expected_file_count", "actual_repo_file_count", "missing", "extra",
            "total_actual_bytes", "all_sizes_match", "all_identities_match",
            "elapsed_seconds", "files",
        ),
        "remote_integrity_evidence",
    )
    if data["repo_id"] != REMOTE_MODEL_ID or data["requested_revision"] != REMOTE_MODEL_REVISION or data["resolved_sha"] != REMOTE_MODEL_REVISION:
        raise Phase4RemoteQwenContractError("remote integrity model identity drifted")
    source_root_text = _text(data["root"], "remote_integrity_evidence.root")
    source_root = Path(source_root_text)
    source_root_is_absolute = (
        PurePosixPath(source_root_text).is_absolute()
        or PureWindowsPath(source_root_text).is_absolute()
    )
    if not source_root_is_absolute:
        raise Phase4RemoteQwenContractError("integrity source root must be absolute")
    if source_root.is_absolute() and source_root.resolve() == model_root.resolve():
        raise Phase4RemoteQwenContractError("remote relocation cannot reuse source path identity")
    if data["missing"] != [] or data["extra"] != [] or data["all_sizes_match"] is not True or data["all_identities_match"] is not True:
        raise Phase4RemoteQwenContractError("remote integrity evidence is incomplete")
    if _integer(data["expected_file_count"], "remote_integrity_evidence.expected_file_count", minimum=1) != REMOTE_FORMAL_FILE_COUNT or _integer(data["actual_repo_file_count"], "remote_integrity_evidence.actual_repo_file_count", minimum=1) != REMOTE_FORMAL_FILE_COUNT:
        raise Phase4RemoteQwenContractError("remote integrity formal file count must be sixteen")
    rows = data["files"]
    if type(rows) is not list or len(rows) != REMOTE_FORMAL_FILE_COUNT:
        raise Phase4RemoteQwenContractError("remote integrity file rows are incomplete")
    expected_rows: list[dict[str, object]] = []
    row_keys = (
        "rfilename", "exists", "expected_size", "expected_lfs_sha256",
        "expected_blob_id", "actual_size", "actual_sha256",
        "actual_git_blob_sha1", "size_match", "identity_match",
    )
    for item in rows:
        row = _exact(item, row_keys, "remote_integrity_evidence.file")
        relative = _relative_path(row["rfilename"], "remote_integrity_evidence.rfilename")
        actual_size = _integer(row["actual_size"], "remote_integrity_evidence.actual_size")
        actual_sha256 = _text(row["actual_sha256"], "remote_integrity_evidence.actual_sha256")
        if len(actual_sha256) != 64 or any(char not in "0123456789abcdef" for char in actual_sha256) or row["exists"] is not True or row["size_match"] is not True or row["identity_match"] is not True or _integer(row["expected_size"], "remote_integrity_evidence.expected_size") != actual_size:
            raise Phase4RemoteQwenContractError("remote integrity evidence row drifted")
        expected_rows.append({"relative_path": relative, "byte_length": actual_size, "sha256": actual_sha256})
    expected_rows.sort(key=lambda row: str(row["relative_path"]))
    if len({row["relative_path"] for row in expected_rows}) != REMOTE_FORMAL_FILE_COUNT:
        raise Phase4RemoteQwenContractError("remote integrity paths are not unique")
    live_paths: list[Path] = []
    for path in model_root.rglob("*"):
        if path.is_symlink():
            raise Phase4RemoteQwenContractError("remote model inventory forbids symlinks")
        if path.is_file():
            live_paths.append(path)
    live_paths.sort(key=lambda path: path.relative_to(model_root).as_posix())
    live_relative = [path.relative_to(model_root).as_posix() for path in live_paths]
    if live_relative != [str(row["relative_path"]) for row in expected_rows]:
        raise Phase4RemoteQwenContractError("remote model files are missing or extra")
    live_rows: list[dict[str, object]] = []
    for path, expected in zip(live_paths, expected_rows, strict=True):
        if path.is_symlink():
            raise Phase4RemoteQwenContractError("remote model file is symlinked")
        size = path.stat().st_size
        digest = _file_sha256(path)
        if size != expected["byte_length"] or digest != expected["sha256"]:
            raise Phase4RemoteQwenContractError("remote live model hash drifted")
        live_rows.append({"relative_path": expected["relative_path"], "byte_length": size, "sha256": digest})
    total = sum(int(row["byte_length"]) for row in live_rows)
    if _integer(data["total_actual_bytes"], "remote_integrity_evidence.total_actual_bytes") != total:
        raise Phase4RemoteQwenContractError("remote integrity total bytes drifted")
    inventory_identity = _identity(live_rows, revision=REMOTE_INVENTORY_ROWS_REVISION, identity_kind="canonical_row_list")
    root_identity = _identity(
        {"model_id": REMOTE_MODEL_ID, "model_revision": REMOTE_MODEL_REVISION, "files": live_rows},
        revision=REMOTE_MODEL_ROOT_IDENTITY_REVISION,
        identity_kind="canonical_tree",
    )
    return {
        "schema_version": REMOTE_INVENTORY_SCHEMA_VERSION,
        "model_id": REMOTE_MODEL_ID,
        "model_revision": REMOTE_MODEL_REVISION,
        "file_count": REMOTE_FORMAL_FILE_COUNT,
        "total_byte_length": total,
        "files": live_rows,
        "excluded_local_cache_file_count": 0,
        "excluded_local_cache_paths_identity": _identity([], revision=f"{REMOTE_SCHEMA_PREFIX}.local-cache.v1", identity_kind="canonical_row_list"),
        "weight_bytes_hashed": True,
        "inventory_identity": inventory_identity,
        "model_root_identity": root_identity,
        "evidence_identity": _identity(evidence_raw, revision=f"{REMOTE_SCHEMA_PREFIX}.integrity-evidence.v1", identity_kind="raw_bytes"),
        "relocation_binding": {
            "schema_version": REMOTE_RELOCATION_SCHEMA_VERSION,
            "source_evidence_root": source_root_text,
            "target_model_root": str(model_root.resolve()),
            "source_root_identity": _identity({"source_root": source_root_text}, revision=f"{REMOTE_SCHEMA_PREFIX}.source-root.v1"),
            "source_evidence_identity": _identity(evidence_raw, revision=f"{REMOTE_SCHEMA_PREFIX}.integrity-evidence.v1", identity_kind="raw_bytes"),
            "source_path_is_not_remote_identity": True,
            "target_identity_basis": "live_target_content_hashes",
            "relative_file_set_rebound": True,
            "formal_file_count": REMOTE_FORMAL_FILE_COUNT,
        },
    }


def _collect_remote_runtime_facts() -> dict[str, object]:
    versions: dict[str, str] = {}
    for distribution, expected in (
        ("transformers", REMOTE_TRANSFORMERS_VERSION),
        ("torch", REMOTE_TORCH_VERSION),
        ("accelerate", REMOTE_ACCELERATE_VERSION),
    ):
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise Phase4RemoteQwenContractError(f"required runtime distribution is missing: {distribution}") from exc
        if actual != expected:
            raise Phase4RemoteQwenContractError(f"runtime distribution drifted: {distribution}")
        versions[distribution] = actual
    return {"python_version": platform.python_version(), **{f"{key}_version": value for key, value in versions.items()}}


def _probe_remote_gpu_facts() -> dict[str, object]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Phase4RemoteQwenContractError("remote GPU probe failed closed") from exc
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    selected: list[list[str]] = []
    for line in rows:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 6 and parts[0] == "0":
            selected.append(parts)
    if len(selected) != 1:
        raise Phase4RemoteQwenContractError("remote GPU0 identity is ambiguous")
    parts = selected[0]
    if "RTX 5090" not in parts[2]:
        raise Phase4RemoteQwenContractError("remote GPU0 is not RTX 5090")
    try:
        total = int(parts[3]) * 1024 * 1024
        free = int(parts[4]) * 1024 * 1024
    except ValueError as exc:
        raise Phase4RemoteQwenContractError("remote GPU VRAM facts are invalid") from exc
    if total < REMOTE_MIN_VRAM_BYTES or free > total:
        raise Phase4RemoteQwenContractError("remote GPU0 VRAM is below the policy floor")
    try:
        torch = importlib.import_module("torch")
        if not torch.cuda.is_available() or torch.cuda.current_device() != 0 or "RTX 5090" not in torch.cuda.get_device_name(0):
            raise Phase4RemoteQwenContractError("torch GPU0 binding drifted")
        if str(torch.version.cuda) != "12.8":
            raise Phase4RemoteQwenContractError("torch CUDA runtime drifted")
    except Phase4RemoteQwenContractError:
        raise
    except Exception as exc:
        raise Phase4RemoteQwenContractError("remote torch GPU probe failed closed") from exc
    return {
        "device_index": 0,
        "device_uuid": parts[1],
        "device_name": parts[2],
        "total_vram_bytes": total,
        "free_vram_bytes": free,
        "driver_version": parts[5],
        "cuda_version": "12.8",
    }


def _offline_process() -> None:
    """Set all offline/telemetry guards before any model import or load."""

    values = {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "LANGSMITH_TRACING": "0",
        "LANGCHAIN_TRACING_V2": "0",
        "CUDA_VISIBLE_DEVICES": "0",
        "PYTHONUNBUFFERED": "1",
    }
    os.environ.update(values)


def _new_result_root(result_root: Path) -> str:
    if not isinstance(result_root, Path) or not result_root.is_absolute() or result_root.exists() or result_root.is_symlink():
        raise Phase4RemoteQwenContractError("remote result root must be a brand-new absolute path")
    if result_root.parent.is_symlink() or not result_root.parent.is_dir():
        raise Phase4RemoteQwenContractError("remote result root parent is invalid")
    result_root.mkdir(exist_ok=False)
    marker = f"{REMOTE_PILOT_ID}-{uuid.uuid4().hex}"
    marker_path = result_root / REMOTE_ROOT_MARKER_NAME
    with marker_path.open("xb") as handle:
        handle.write((marker + "\n").encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
    return marker


def _write_remote_once(root: Path, relative_path: str, raw: bytes) -> None:
    _local._p4d1_write_once(root, relative_path, raw)


def _build_remote_policy_ref(policy_raw: bytes) -> dict[str, object]:
    return _identity(policy_raw, revision=REMOTE_POLICY_SCHEMA_VERSION, identity_kind="raw_bytes")


class RemotePreflightManifest(_CanonicalRecord):
    KEYS = (
        "schema_version",
        "manifest_id",
        "diagnostic_id",
        "result_root_marker",
        "case",
        "policy_identity",
        "profile",
        "inventory_identity",
        "checkpoint_binding_identity",
        "prior_failure_identity",
        "d2_predecessor_binding_identity",
        "f3",
        "execution",
        "action_state",
    )
    SCHEMA_VERSION = REMOTE_MANIFEST_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        result_root_marker: str,
        policy_raw: bytes,
        profile: RemoteQwenProfile,
        inventory: Mapping[str, object],
        checkpoint: RemoteCheckpointBinding,
        prior_failure: _local.AttemptResult,
        d2_predecessor: RemoteD2PredecessorBinding,
        input_bytes: bytes,
        prompt_bytes: bytes,
        config_bytes: bytes,
        request_bytes: bytes,
    ) -> "RemotePreflightManifest":
        profile.validate()
        checkpoint.validate()
        prior_failure.validate()
        d2_predecessor.validate()
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "manifest_id": "pending",
            "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
            "result_root_marker": _text(result_root_marker, "remote result root marker"),
            "case": {"case_id": REMOTE_CASE_ID, "request_id": REMOTE_REQUEST_ID, "node_id": REMOTE_NODE_ID, "model_id": REMOTE_MODEL_ID, "model_revision": REMOTE_MODEL_REVISION},
            "policy_identity": _build_remote_policy_ref(policy_raw),
            "profile": profile.to_dict(),
            "inventory_identity": dict(inventory["inventory_identity"]),
            "checkpoint_binding_identity": _identity(checkpoint.to_dict(), revision=REMOTE_CHECKPOINT_SCHEMA_VERSION),
            "prior_failure_identity": _identity(prior_failure.to_dict(), revision=_local.ATTEMPT_RESULT_SCHEMA_VERSION),
            "d2_predecessor_binding_identity": _identity(
                d2_predecessor.to_dict(),
                revision=REMOTE_PREDECESSOR_SCHEMA_VERSION,
            ),
            "f3": {
                "node_id": REMOTE_NODE_ID,
                "projection_revision": REMOTE_PROJECTION_REVISION,
                "input_identity": _identity(input_bytes, revision=REMOTE_INPUT_REVISION, identity_kind="raw_bytes"),
                "prompt_identity": _identity(prompt_bytes, revision=REMOTE_PROMPT_REVISION, identity_kind="raw_bytes"),
                "config_identity": _identity(config_bytes, revision=REMOTE_CONFIG_REVISION, identity_kind="raw_bytes"),
                "request_identity": _identity(request_bytes, revision=REMOTE_REQUEST_REVISION, identity_kind="raw_bytes"),
            },
            "execution": {
                "generate_call_cap": REMOTE_GENERATE_CALL_CAP,
                "retry_count": REMOTE_RETRY_COUNT,
                "timeout_seconds": REMOTE_TIMEOUT_SECONDS,
                "parse": "not_executed",
                "node_contract": "not_executed",
                "registry": "not_executed",
                "f4": "not_executed",
                "composition": "not_executed",
                "assembler": "not_executed",
                "downstream": "not_executed",
            },
            "action_state": _remote_action_state(model_action=False, remote_action=False),
        }
        root["manifest_id"] = _identity(
            {key: value for key, value in root.items() if key != "manifest_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="RemotePreflightManifest")
        _sha(data["manifest_id"], "RemotePreflightManifest.manifest_id")
        if data["diagnostic_id"] != REMOTE_DIAGNOSTIC_ID:
            raise Phase4RemoteQwenContractError("remote manifest diagnostic drifted")
        _text(data["result_root_marker"], "RemotePreflightManifest.result_root_marker")
        if data["case"] != {"case_id": REMOTE_CASE_ID, "request_id": REMOTE_REQUEST_ID, "node_id": REMOTE_NODE_ID, "model_id": REMOTE_MODEL_ID, "model_revision": REMOTE_MODEL_REVISION}:
            raise Phase4RemoteQwenContractError("remote manifest case drifted")
        _validate_identity(data["policy_identity"], "RemotePreflightManifest.policy_identity")
        RemoteQwenProfile.from_dict(data["profile"])
        _validate_identity(data["inventory_identity"], "RemotePreflightManifest.inventory_identity")
        _validate_identity(data["checkpoint_binding_identity"], "RemotePreflightManifest.checkpoint_binding_identity")
        _validate_identity(data["prior_failure_identity"], "RemotePreflightManifest.prior_failure_identity")
        _validate_identity(
            data["d2_predecessor_binding_identity"],
            "RemotePreflightManifest.d2_predecessor_binding_identity",
        )
        f3 = _exact(data["f3"], ("node_id", "projection_revision", "input_identity", "prompt_identity", "config_identity", "request_identity"), "RemotePreflightManifest.f3")
        if f3["node_id"] != REMOTE_NODE_ID or f3["projection_revision"] != REMOTE_PROJECTION_REVISION:
            raise Phase4RemoteQwenContractError("remote manifest F3 scope drifted")
        for key in ("input_identity", "prompt_identity", "config_identity", "request_identity"):
            _validate_identity(f3[key], f"RemotePreflightManifest.f3.{key}")
        execution = _exact(data["execution"], ("generate_call_cap", "retry_count", "timeout_seconds", "parse", "node_contract", "registry", "f4", "composition", "assembler", "downstream"), "RemotePreflightManifest.execution")
        if execution != {"generate_call_cap": 1, "retry_count": 0, "timeout_seconds": REMOTE_TIMEOUT_SECONDS, "parse": "not_executed", "node_contract": "not_executed", "registry": "not_executed", "f4": "not_executed", "composition": "not_executed", "assembler": "not_executed", "downstream": "not_executed"}:
            raise Phase4RemoteQwenContractError("remote manifest execution drifted")
        _validate_remote_action_state(data["action_state"], "RemotePreflightManifest.action_state", model_action=False, remote_action=False)
        expected = _identity({key: data[key] for key in data if key != "manifest_id"}, revision=cls.SCHEMA_VERSION)["sha256"]
        if data["manifest_id"] != expected:
            raise Phase4RemoteQwenContractError("remote manifest identity drifted")


class RemotePreparedExperiment(NamedTuple):
    result_root: Path
    result_root_marker: str
    policy: RemoteQwenPolicy
    policy_raw: bytes
    profile: RemoteQwenProfile
    inventory: Mapping[str, object]
    checkpoint: RemoteCheckpointReplay
    d2_predecessor: RemoteD2PredecessorBinding
    d2_predecessor_result_raw: bytes
    d2_predecessor_raw_response: bytes
    manifest: RemotePreflightManifest
    input_bytes: bytes
    prompt_bytes: bytes
    config_bytes: bytes
    request_bytes: bytes


def _f3_policy() -> _local.NodeProjectionPolicy:
    return _local.NodeProjectionPolicy.create(
        node_id=REMOTE_NODE_ID,
        allowed_categories=[
            "canonical_b_input",
            "validated_F1_output",
            "validated_F2_output",
            "deterministic_registry",
        ],
        prohibited_categories=[
            "b_aux_sidecar",
            "retrieval_evidence",
            "h1_gold",
            "browser_evidence",
            "hidden_reasoning",
        ],
        field_caps={
            "input_bytes": 131072,
            "output_bytes": 65536,
            "prompt_bytes": 16384,
            "config_bytes": 8192,
            "request_bytes": 8192,
            "ref_count": 32,
        },
        upstream_required_node_ids=["F1", "F2"],
        projection_revision=REMOTE_PROJECTION_REVISION,
        prompt_template_revision=REMOTE_PROMPT_REVISION,
        config_revision=REMOTE_CONFIG_REVISION,
    )


def _build_remote_prompt(*, input_bytes: bytes, prior_failure: _local.AttemptResult, policy: _local.NodeProjectionPolicy) -> bytes:
    instructions = list(_NODE_PROMPT_GUIDANCE[REMOTE_NODE_ID]) + [
        "Return one canonical UTF-8 JSON object only; do not repair, retry, or emit a second object.",
    ]
    payload = {
        "prompt_schema_version": REMOTE_PROMPT_REVISION,
        "node_id": REMOTE_NODE_ID,
        "template_revision": REMOTE_PROMPT_REVISION,
        "output_format": "exact_json_object",
        "input_sha256": _local._sha256(input_bytes),
        "input_byte_length": len(input_bytes),
        "instructions": instructions,
        "output_key_order": list(REMOTE_OUTPUT_KEY_ORDER),
        "output_contract": copy.deepcopy(_NODE_OUTPUT_CONTRACTS[REMOTE_NODE_ID]),
        "model_id": REMOTE_MODEL_ID,
        "model_revision": REMOTE_MODEL_REVISION,
        "prior_failure_identity": _identity(prior_failure.to_dict(), revision=_local.ATTEMPT_RESULT_SCHEMA_VERSION),
        "prior_failure_code": prior_failure.failure_code,
        "projection_revision": policy.projection_revision,
    }
    raw = _canonical_bytes(payload)
    caps = _local._node_policy_caps(policy.field_caps, "remote F3 policy.field_caps")
    if len(raw) > caps["prompt_bytes"]:
        raise Phase4RemoteQwenContractError("remote F3 prompt exceeds cap")
    return raw


def _build_remote_config(*, profile: RemoteQwenProfile, policy: _local.NodeProjectionPolicy) -> bytes:
    payload = {
        "config_schema_version": REMOTE_CONFIG_REVISION,
        "config_revision": policy.config_revision,
        "profile_id": profile.profile_id,
        "profile_name": profile.profile_name,
        "dtype": profile.dtype,
        "quantization": profile.quantization,
        "compute_dtype": profile.compute_dtype,
        "device_map": profile.device_map,
        "cpu_offload": profile.cpu_offload,
        "local_files_only": profile.local_files_only,
        "offline": profile.offline,
        "network": profile.network,
        "telemetry": profile.telemetry,
        "tracing": profile.tracing,
        "decode": profile.decode,
        "max_input_tokens": profile.max_input_tokens,
        "model_context_tokens": profile.model_context_tokens,
        "fixed_max_new_tokens": profile.fixed_max_new_tokens,
        "generation_stop_policy": profile.generation_stop_policy,
        "timeout_seconds": profile.timeout_seconds,
        "seed": profile.seed,
    }
    raw = _canonical_bytes(payload)
    if len(raw) > _local._node_policy_caps(policy.field_caps, "remote F3 policy.field_caps")["config_bytes"]:
        raise Phase4RemoteQwenContractError("remote runtime config exceeds cap")
    return raw


def _build_remote_request(*, profile: RemoteQwenProfile) -> bytes:
    return _canonical_bytes({
        "request_schema_version": REMOTE_REQUEST_REVISION,
        "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
        "pilot_id": REMOTE_PILOT_ID,
        "case_id": REMOTE_CASE_ID,
        "request_id": REMOTE_REQUEST_ID,
        "node_id": REMOTE_NODE_ID,
        "model_id": profile.model_id,
        "model_revision": profile.model_revision,
        "call_kind": "remote_node_local",
        "source_kind": "remote_qwen_bf16",
        "generate_call_index": 1,
        "generate_call_cap": REMOTE_GENERATE_CALL_CAP,
        "retry_count": REMOTE_RETRY_COUNT,
    })


def prepare_remote_qwen_bf16_f3(
    *,
    model_root: Path,
    integrity_evidence: Path,
    checkpoint_packet: Path,
    checkpoint_receipt: Path,
    prior_f3_failure: Path,
    predecessor_d2_result: Path,
    predecessor_d2_raw: Path,
    result_root: Path,
) -> RemotePreparedExperiment:
    """Prepare and fsync all no-generation records for one fresh result root."""

    _offline_process()
    policy, policy_raw = load_remote_qwen_policy()
    marker = _new_result_root(result_root)
    _write_remote_once(result_root, REMOTE_POLICY_COPY_NAME, policy_raw)
    try:
        checkpoint = replay_remote_checkpoint(
            checkpoint_packet=checkpoint_packet,
            checkpoint_receipt=checkpoint_receipt,
            prior_f3_failure=prior_f3_failure,
        )
        predecessor_result_raw = _read_input_file(
            predecessor_d2_result, "D2 predecessor result"
        )
        predecessor_raw_response = _read_input_file(
            predecessor_d2_raw, "D2 predecessor raw response"
        )
        d2_predecessor = RemoteD2PredecessorBinding.create(
            result_raw=predecessor_result_raw,
            raw_response=predecessor_raw_response,
        )
        inventory = validate_remote_model_inventory(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
        )
        runtime_facts = _collect_remote_runtime_facts()
        gpu_facts = _probe_remote_gpu_facts()
        profile = RemoteQwenProfile.create(
            model_root_identity=inventory["model_root_identity"],
            model_inventory_identity=inventory["inventory_identity"],
            model_file_count=inventory["file_count"],
            runtime_facts=runtime_facts,
            gpu_facts=gpu_facts,
        )
        f3_policy = _f3_policy()
        from req2web_orchestration.phase4_graph import (
            phase4_project_node_input_authority,
            synthetic_commerce_b_input,
            validate_b_input,
        )

        canonical_b = validate_b_input(synthetic_commerce_b_input())
        b_input_bytes = _canonical_bytes(canonical_b)
        input_bytes = _local.derive_node_input(
            node_id=REMOTE_NODE_ID,
            b_input_bytes=b_input_bytes,
            upstream_outputs=checkpoint.outputs,
            authority_state=checkpoint.authority_state,
            policy=f3_policy,
        )
        # The projection call is deliberately repeated at preparation time so
        # the prompt/input identity is tied to the live replayed state.
        phase4_project_node_input_authority(checkpoint.authority_state, REMOTE_NODE_ID)
        prompt_bytes = _build_remote_prompt(
            input_bytes=input_bytes,
            prior_failure=checkpoint.prior_failure,
            policy=f3_policy,
        )
        config_bytes = _build_remote_config(profile=profile, policy=f3_policy)
        request_bytes = _build_remote_request(profile=profile)
        manifest = RemotePreflightManifest.create(
            result_root_marker=marker,
            policy_raw=policy_raw,
            profile=profile,
            inventory=inventory,
            checkpoint=checkpoint.binding,
            prior_failure=checkpoint.prior_failure,
            d2_predecessor=d2_predecessor,
            input_bytes=input_bytes,
            prompt_bytes=prompt_bytes,
            config_bytes=config_bytes,
            request_bytes=request_bytes,
        )
        _write_remote_once(result_root, REMOTE_CHECKPOINT_PACKET_NAME, checkpoint.packet_raw)
        _write_remote_once(result_root, REMOTE_CHECKPOINT_RECEIPT_NAME, checkpoint.receipt_raw)
        _write_remote_once(result_root, REMOTE_PRIOR_FAILURE_NAME, checkpoint.prior_raw)
        _write_remote_once(
            result_root,
            REMOTE_PREDECESSOR_RESULT_NAME,
            predecessor_result_raw,
        )
        _write_remote_once(
            result_root,
            REMOTE_PREDECESSOR_RAW_NAME,
            predecessor_raw_response,
        )
        _write_remote_once(
            result_root,
            REMOTE_PREDECESSOR_BINDING_NAME,
            d2_predecessor.canonical_bytes(),
        )
        _write_remote_once(result_root, REMOTE_RELOCATION_NAME, _canonical_bytes(inventory["relocation_binding"]))
        _write_remote_once(result_root, REMOTE_INVENTORY_NAME, _canonical_bytes(inventory))
        _write_remote_once(result_root, REMOTE_PROFILE_NAME, profile.canonical_bytes())
        _write_remote_once(result_root, REMOTE_INPUT_NAME, input_bytes)
        _write_remote_once(result_root, REMOTE_PROMPT_NAME, prompt_bytes)
        _write_remote_once(result_root, REMOTE_CONFIG_NAME, config_bytes)
        _write_remote_once(result_root, REMOTE_REQUEST_NAME, request_bytes)
        _write_remote_once(result_root, REMOTE_MANIFEST_NAME, manifest.canonical_bytes())
    except Exception as exc:
        try:
            _write_remote_once(
                result_root,
                "preflight_failure.json",
                _canonical_bytes({
                    "schema_version": f"{REMOTE_SCHEMA_PREFIX}.preflight_failure.v1",
                    "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
                    "failure_type": type(exc).__name__,
                    "failure_code": "preflight_failed",
                }),
            )
        except Exception:
            pass
        raise
    return RemotePreparedExperiment(
        result_root=result_root,
        result_root_marker=marker,
        policy=policy,
        policy_raw=policy_raw,
        profile=profile,
        inventory=inventory,
        checkpoint=checkpoint,
        d2_predecessor=d2_predecessor,
        d2_predecessor_result_raw=predecessor_result_raw,
        d2_predecessor_raw_response=predecessor_raw_response,
        manifest=manifest,
        input_bytes=input_bytes,
        prompt_bytes=prompt_bytes,
        config_bytes=config_bytes,
        request_bytes=request_bytes,
    )


class RemoteStreamMirror:
    """Parent-side low-latency mirror of structured worker stderr."""

    def __init__(self, target: object | None = None) -> None:
        self._target = target if target is not None else sys.stderr
        self._partial_chunks: list[bytes] = []
        self._lock = threading.Lock()
        self._generation_started_at: float | None = None
        self._first_token_seen = False

    def stage(self, label: str) -> None:
        label = _text(label, "remote stream stage")
        with self._lock:
            self._target.write(f"\n[P4-03D3] {label}\n")  # type: ignore[union-attr]
            self._target.flush()  # type: ignore[union-attr]

    def feed(self, raw: bytes) -> None:
        if type(raw) is not bytes:
            raise Phase4RemoteQwenContractError("remote mirrored stderr must be bytes")
        try:
            event = _strict_json(raw.rstrip(b"\r\n"))
        except Phase4RemoteQwenContractError:
            with self._lock:
                self._target.write(raw.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
                self._target.flush()  # type: ignore[union-attr]
            return
        try:
            data = _exact(event, ("schema_version", "diagnostic_id", "event", "delta_b64"), "remote stream event")
            if data["schema_version"] != REMOTE_STREAM_EVENT_SCHEMA_VERSION or data["diagnostic_id"] != REMOTE_DIAGNOSTIC_ID:
                raise Phase4RemoteQwenContractError("remote stream event identity drifted")
            event_name = _text(data["event"], "remote stream event name")
            delta = _decode_b64(data["delta_b64"], "remote stream delta", allow_empty=True)
        except Phase4RemoteQwenContractError:
            with self._lock:
                self._target.write(raw.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
                self._target.flush()  # type: ignore[union-attr]
            return
        if event_name == "token_delta":
            with self._lock:
                if delta:
                    self._first_token_seen = True
                    self._partial_chunks.append(delta)
                self._target.write(delta.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
                self._target.flush()  # type: ignore[union-attr]
            return
        if delta:
            raise Phase4RemoteQwenContractError("remote stage event cannot carry a delta")
        if event_name == "generation_started":
            with self._lock:
                self._generation_started_at = time.monotonic()
                self._first_token_seen = False
        self.stage(event_name.replace("_", " "))

    def heartbeat(self) -> None:
        with self._lock:
            started_at = self._generation_started_at
            first_token_seen = self._first_token_seen
        if started_at is None or first_token_seen:
            return
        elapsed = max(0, int(time.monotonic() - started_at))
        self.stage(f"waiting for first token (elapsed {elapsed}s)")

    @property
    def partial_bytes(self) -> bytes:
        with self._lock:
            return b"".join(self._partial_chunks)


def _remote_worker_stderr_write(raw: bytes) -> None:
    stream = getattr(sys.stderr, "buffer", None)
    if stream is not None:
        stream.write(raw)
        stream.flush()
    else:
        sys.stderr.write(raw.decode("utf-8"))
        sys.stderr.flush()


def _emit_remote_worker_event(event: str, delta: bytes = b"") -> None:
    _remote_worker_stderr_write(
        _canonical_bytes({
            "schema_version": REMOTE_STREAM_EVENT_SCHEMA_VERSION,
            "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
            "event": _text(event, "remote worker event"),
            "delta_b64": _b64(delta, "remote worker delta"),
        }) + b"\n"
    )


def _is_complete_json_object(text: str) -> bool:
    if type(text) is not str:
        return False
    candidate = text.lstrip()
    if not candidate.startswith("{"):
        return False
    try:
        value, end = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        return False
    return isinstance(value, Mapping) and not candidate[end:].strip()


class _RemoteCompleteJsonStoppingCriteria:
    """Stop immediately after one complete top-level JSON object."""

    def __init__(self, *, tokenizer: object, prompt_length: int) -> None:
        if not callable(getattr(tokenizer, "decode", None)):
            raise Phase4RemoteQwenContractError(
                "remote JSON stopping tokenizer is invalid"
            )
        self._tokenizer = tokenizer
        self._prompt_length = _integer(
            prompt_length, "remote JSON stopping prompt length", minimum=1
        )

    def __call__(
        self, input_ids: object, scores: object, **_: object
    ) -> bool:
        del scores
        if not callable(getattr(input_ids, "tolist", None)):
            raise Phase4RemoteQwenContractError(
                "remote JSON stopping token tensor is invalid"
            )
        rows = input_ids.tolist()  # type: ignore[union-attr]
        if (
            type(rows) is not list
            or len(rows) != 1
            or type(rows[0]) is not list
            or len(rows[0]) < self._prompt_length
        ):
            raise Phase4RemoteQwenContractError(
                "remote JSON stopping token batch drifted"
            )
        generated_ids = rows[0][self._prompt_length :]
        if not generated_ids:
            return False
        text = self._tokenizer.decode(  # type: ignore[union-attr]
            generated_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        return _is_complete_json_object(text)


class _RemoteTransformersBackend:
    """Lazy worker-only Transformers backend with no quantization path."""

    def __init__(self, *, model_root: Path, profile: RemoteQwenProfile) -> None:
        if not isinstance(model_root, Path) or not model_root.is_dir() or model_root.is_symlink():
            raise Phase4RemoteQwenContractError("remote backend model root is invalid")
        profile.validate()
        self._model_root = model_root
        self._profile = profile
        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._transformers: Any = None
        self._loaded_facts: dict[str, object] | None = None

    @property
    def loaded_facts(self) -> dict[str, object] | None:
        return None if self._loaded_facts is None else copy.deepcopy(self._loaded_facts)

    def load(self) -> None:
        if self._model is not None:
            return
        _offline_process()
        try:
            if importlib.metadata.version("transformers") != REMOTE_TRANSFORMERS_VERSION or importlib.metadata.version("torch") != REMOTE_TORCH_VERSION or importlib.metadata.version("accelerate") != REMOTE_ACCELERATE_VERSION:
                raise Phase4RemoteQwenContractError("remote runtime package versions drifted")
            transformers = importlib.import_module("transformers")
            torch = importlib.import_module("torch")
            gpu = _probe_remote_gpu_facts()
            if gpu["device_name"] != self._profile.device_name or gpu["device_uuid"] != self._profile.device_uuid or gpu["total_vram_bytes"] != self._profile.total_vram_bytes or gpu["cuda_version"] != self._profile.cuda_version:
                raise Phase4RemoteQwenContractError("remote action-time GPU/profile binding drifted")
            processor = transformers.AutoProcessor.from_pretrained(
                str(self._model_root),
                revision=REMOTE_MODEL_REVISION,
                local_files_only=True,
                trust_remote_code=False,
            )
            model = transformers.AutoModelForMultimodalLM.from_pretrained(
                str(self._model_root),
                revision=REMOTE_MODEL_REVISION,
                local_files_only=True,
                trust_remote_code=False,
                device_map={"": 0},
                dtype=torch.bfloat16,
                low_cpu_mem_usage=True,
            )
            if getattr(model, "is_loaded_in_4bit", False) is True or getattr(model, "is_loaded_in_8bit", False) is True or getattr(model, "quantization_config", None) is not None:
                raise Phase4RemoteQwenContractError("remote model was quantized")
            hf_device_map, parameter_devices = _validate_live_model_placement(model)
            parameter_dtypes = sorted({str(parameter.dtype) for parameter in model.parameters()})
            if parameter_dtypes != ["torch.bfloat16"]:
                raise Phase4RemoteQwenContractError("remote model parameters are not BF16")
            compute_dtypes = sorted({str(module.compute_dtype) for module in model.modules() if hasattr(module, "compute_dtype")})
            if compute_dtypes and compute_dtypes != ["torch.bfloat16"]:
                raise Phase4RemoteQwenContractError("remote model compute dtype drifted")
            if parameter_devices != {"cuda:0"}:
                raise Phase4RemoteQwenContractError("remote model escaped cuda:0")
            text_config = getattr(model.config, "text_config", model.config)
            model_context_tokens = getattr(
                text_config, "max_position_embeddings", None
            )
            if (
                type(model_context_tokens) is not int
                or model_context_tokens != self._profile.model_context_tokens
            ):
                raise Phase4RemoteQwenContractError(
                    "remote model context boundary drifted"
                )
            model.eval()
        except Phase4RemoteQwenContractError:
            raise
        except Exception as exc:
            raise Phase4RemoteQwenContractError("remote BF16 model load failed closed") from exc
        self._processor = processor
        self._model = model
        self._torch = torch
        self._transformers = transformers
        self._loaded_facts = {
            "model_class": type(model).__name__,
            "processor_class": type(processor).__name__,
            "is_loaded_in_4bit": False,
            "is_loaded_in_8bit": False,
            "quantization": REMOTE_QUANTIZATION,
            "hf_device_map": hf_device_map,
            "parameter_devices": sorted(parameter_devices),
            "parameter_dtypes": parameter_dtypes,
            "compute_dtypes": compute_dtypes,
            "cpu_offload": False,
            "device": "cuda:0",
            "dtype": "torch.bfloat16",
            "model_context_tokens": model_context_tokens,
            "model_root_identity": self._profile.model_root_identity,
        }

    @staticmethod
    def _validated_text_inputs(inputs: object) -> tuple[str, ...]:
        if not isinstance(inputs, Mapping):
            raise Phase4RemoteQwenContractError("remote processor output must be a mapping")
        keys = set(inputs)
        required = {"input_ids", "attention_mask"}
        allowed = required | {"mm_token_type_ids"}
        multimedia = [key for key in keys if str(key).startswith(("image", "video", "pixel"))]
        if multimedia or not required.issubset(keys) or not keys.issubset(allowed):
            raise Phase4RemoteQwenContractError("remote F3 text-only processor contract failed")
        return tuple(sorted(str(key) for key in keys))

    def generate_stream(self, *, input_bytes: bytes, prompt_bytes: bytes, config_bytes: bytes, request_bytes: bytes, emit_delta: Callable[[bytes], None]) -> bytes:
        if (
            self._model is None
            or self._processor is None
            or self._torch is None
            or self._transformers is None
        ):
            raise Phase4RemoteQwenContractError("remote backend was not loaded")
        if not callable(emit_delta):
            raise Phase4RemoteQwenContractError("remote token emitter is invalid")
        try:
            actual_input = _strict_json(input_bytes)
            prompt_contract = _strict_json(prompt_bytes)
            config = _strict_json(config_bytes)
            request = _strict_json(request_bytes)
            if request.get("diagnostic_id") != REMOTE_DIAGNOSTIC_ID or request.get("node_id") != REMOTE_NODE_ID:
                raise Phase4RemoteQwenContractError("remote request identity drifted")
            if (
                config.get("quantization") != REMOTE_QUANTIZATION
                or config.get("dtype") != REMOTE_DTYPE
                or config.get("device_map") != REMOTE_DEVICE_MAP
                or config.get("cpu_offload") is not False
                or config.get("model_context_tokens")
                != REMOTE_MODEL_CONTEXT_TOKENS
                or config.get("fixed_max_new_tokens") is not None
                or config.get("generation_stop_policy")
                != REMOTE_GENERATION_STOP_POLICY
            ):
                raise Phase4RemoteQwenContractError("remote runtime config drifted")
            model_text = "PROMPT_CONTRACT_JSON\n" + _canonical_bytes(prompt_contract).decode("utf-8") + "\nACTUAL_NODE_INPUT_JSON\n" + _canonical_bytes(actual_input).decode("utf-8")
            rendered = self._processor.apply_chat_template(
                [{"role": "user", "content": [{"type": "text", "text": model_text}]}],
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            encoded = self._processor(text=[rendered], return_tensors="pt")
            keys = self._validated_text_inputs(encoded)
            encoded = {key: encoded[key].to("cuda:0") for key in keys}
            input_length = int(encoded["input_ids"].shape[1])
            if input_length > self._profile.max_input_tokens:
                raise Phase4RemoteQwenContractError("remote model input token cap exceeded")
            tokenizer = getattr(self._processor, "tokenizer", self._processor)
            streamer = _local._P4D1TokenDeltaStreamer(tokenizer=tokenizer, emit_delta=emit_delta)
            remaining_context_tokens = (
                self._profile.model_context_tokens - input_length
            )
            if remaining_context_tokens < 1:
                raise Phase4RemoteQwenContractError(
                    "remote model context boundary was exhausted by input"
                )
            stopping_criteria = self._transformers.StoppingCriteriaList(
                [
                    _RemoteCompleteJsonStoppingCriteria(
                        tokenizer=tokenizer,
                        prompt_length=input_length,
                    )
                ]
            )
            self._torch.manual_seed(self._profile.seed)
            self._torch.cuda.manual_seed_all(self._profile.seed)
            generated = self._model.generate(
                **encoded,
                streamer=streamer,
                max_new_tokens=remaining_context_tokens,
                stopping_criteria=stopping_criteria,
                do_sample=False,
                num_return_sequences=1,
            )
            generated_only = generated[:, input_length:]
            text = self._processor.batch_decode(generated_only, skip_special_tokens=True)[0]
            if type(text) is not str or not text:
                raise Phase4RemoteQwenContractError("remote model returned empty text")
            return text.encode("utf-8")
        except Phase4RemoteQwenContractError:
            raise
        except Exception as exc:
            raise Phase4RemoteQwenContractError("remote BF16 generation failed closed") from exc


def _run_remote_worker_protocol(*, model_root: Path) -> int:
    """Child entrypoint.  stdout is JSON IPC; all visibility is structured stderr."""

    _offline_process()
    worker_id = f"worker-{uuid.uuid4().hex}"
    generate_seen = False
    try:
        line = sys.stdin.buffer.readline()
        load = _exact(_strict_json(line.rstrip(b"\r\n")), ("protocol", "kind", "profile_b64"), "remote worker load")
        if load["protocol"] != REMOTE_WORKER_IPC_PROTOCOL or load["kind"] != "load":
            raise Phase4RemoteQwenContractError("remote worker load request drifted")
        profile = RemoteQwenProfile.from_bytes(_decode_b64(load["profile_b64"], "remote worker profile"))
        _emit_remote_worker_event("load_started")
        backend = _RemoteTransformersBackend(model_root=model_root, profile=profile)
        backend.load()
        loaded_facts = backend.loaded_facts
        if loaded_facts is None:
            raise Phase4RemoteQwenContractError("remote worker loaded facts are unavailable")
        _emit_remote_worker_event("load_completed")
        sys.stdout.buffer.write(_canonical_bytes({"protocol": REMOTE_WORKER_IPC_PROTOCOL, "kind": "loaded", "worker_id": worker_id, "worker_pid": os.getpid(), "loaded_facts": loaded_facts}) + b"\n")
        sys.stdout.buffer.flush()
        while True:
            raw_line = sys.stdin.buffer.readline()
            if not raw_line:
                return 0
            request = _strict_json(raw_line.rstrip(b"\r\n"))
            if request.get("protocol") != REMOTE_WORKER_IPC_PROTOCOL:
                raise Phase4RemoteQwenContractError("remote worker protocol drifted")
            if request.get("kind") == "shutdown":
                sys.stdout.buffer.write(_canonical_bytes({"protocol": REMOTE_WORKER_IPC_PROTOCOL, "kind": "shutdown_ack", "worker_id": worker_id}) + b"\n")
                sys.stdout.buffer.flush()
                return 0
            data = _exact(request, ("protocol", "kind", "call_id", "node_id", "input_b64", "prompt_b64", "config_b64", "request_b64"), "remote worker generate")
            if data["kind"] != "generate" or data["node_id"] != REMOTE_NODE_ID or generate_seen:
                raise Phase4RemoteQwenContractError("remote worker one-call scope drifted")
            generate_seen = True
            try:
                _emit_remote_worker_event("generation_started")
                generated = backend.generate_stream(
                    input_bytes=_decode_b64(data["input_b64"], "remote worker input"),
                    prompt_bytes=_decode_b64(data["prompt_b64"], "remote worker prompt"),
                    config_bytes=_decode_b64(data["config_b64"], "remote worker config"),
                    request_bytes=_decode_b64(data["request_b64"], "remote worker request"),
                    emit_delta=lambda delta: _emit_remote_worker_event("token_delta", delta),
                )
                _emit_remote_worker_event("generation_completed")
                response = {"protocol": REMOTE_WORKER_IPC_PROTOCOL, "kind": "generation_result", "call_id": data["call_id"], "worker_id": worker_id, "raw_b64": _b64(generated, "remote worker raw")}
            except Exception:
                _emit_remote_worker_event("generation_failed")
                response = {"protocol": REMOTE_WORKER_IPC_PROTOCOL, "kind": "generation_error", "call_id": data["call_id"], "worker_id": worker_id}
            sys.stdout.buffer.write(_canonical_bytes(response) + b"\n")
            sys.stdout.buffer.flush()
    except Exception:
        _emit_remote_worker_event("load_failed" if not generate_seen else "worker_failed")
        try:
            sys.stdout.buffer.write(_canonical_bytes({"protocol": REMOTE_WORKER_IPC_PROTOCOL, "kind": "load_error", "worker_id": worker_id}) + b"\n")
            sys.stdout.buffer.flush()
        except Exception:
            pass
        return 2


class RemoteWorkerClient:
    """Parent IPC client with bounded polling, heartbeat, and hard teardown."""

    def __init__(
        self,
        *,
        process: subprocess.Popen[str],
        messages: queue.Queue[dict[str, object]],
        stderr_capture: _WorkerStderrCapture,
        stderr_thread: threading.Thread,
        profile: RemoteQwenProfile,
        worker_id: str = "worker-pending",
        loaded_facts: Mapping[str, object] | None = None,
        wait_observer: Callable[[], None] | None = None,
    ) -> None:
        profile.validate()
        if process.pid is None or process.stdin is None:
            raise Phase4RemoteQwenContractError("remote worker process is invalid")
        self._process = process
        self._messages = messages
        self._stderr_capture = stderr_capture
        self._stderr_thread = stderr_thread
        self._profile = profile
        self._worker_id = _text(worker_id, "remote worker id")
        self._loaded_facts = copy.deepcopy(dict(loaded_facts or {}))
        self._wait_observer = wait_observer
        self._closed = False
        self._generation_started = False
        self._generate_calls = 0
        self._teardown: dict[str, object] = {
            "worker_id": self._worker_id,
            "worker_pid": process.pid,
            "worker_exit_code": None,
            "worker_exit_verified": False,
            "graceful_shutdown_requested": False,
            "terminate_sent": False,
            "kill_sent": False,
            "terminal_status": "worker_running",
        }

    @property
    def loaded_facts(self) -> dict[str, object]:
        return copy.deepcopy(self._loaded_facts)

    @property
    def generation_started(self) -> bool:
        return self._generation_started

    @property
    def stderr_bytes(self) -> bytes | None:
        snapshot = self._stderr_capture.snapshot(stderr_thread=self._stderr_thread, worker_exit_verified=bool(self._teardown["worker_exit_verified"]))
        return None if snapshot["stderr_bytes"] is None else bytes(snapshot["stderr_bytes"])

    @property
    def teardown_facts(self) -> dict[str, object]:
        facts = copy.deepcopy(self._teardown)
        capture = self._stderr_capture.snapshot(stderr_thread=self._stderr_thread, worker_exit_verified=bool(facts["worker_exit_verified"]))
        facts["stderr_capture_completed"] = capture["completed"]
        facts["stderr_capture_error"] = capture["error"]
        facts["stderr_thread_joined"] = capture["thread_joined"]
        return facts

    def bind_loaded(self, *, worker_id: str, loaded_facts: Mapping[str, object]) -> None:
        self._worker_id = _text(worker_id, "remote worker id", pattern=_local._ID_RE)
        self._loaded_facts = copy.deepcopy(dict(loaded_facts))
        self._teardown["worker_id"] = self._worker_id

    def _send(self, payload: Mapping[str, object]) -> None:
        if self._closed or self._process.stdin is None:
            raise SupervisedWorkerFailure("worker_unavailable", "remote worker is unavailable")
        try:
            self._process.stdin.write(_canonical_bytes(dict(payload)).decode("utf-8") + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._force_teardown("worker_failed")
            raise SupervisedWorkerFailure("worker_ipc_failed", "remote worker IPC failed closed") from exc

    def _receive(self, *, timeout: int) -> dict[str, object]:
        _integer(timeout, "remote worker receive timeout", minimum=1)
        deadline = time.monotonic() + timeout
        next_heartbeat = time.monotonic() + REMOTE_HEARTBEAT_INTERVAL_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._force_teardown("generation_timeout")
                raise SupervisedWorkerFailure("generation_timeout", "remote generation exceeded wall-clock timeout")
            try:
                message = self._messages.get(timeout=min(REMOTE_RECEIVE_POLL_SECONDS, remaining))
            except queue.Empty:
                now = time.monotonic()
                if self._wait_observer is not None and now >= next_heartbeat:
                    self._wait_observer()
                    next_heartbeat = now + REMOTE_HEARTBEAT_INTERVAL_SECONDS
                continue
            if message.get("kind") == "protocol_error":
                self._force_teardown("worker_failed")
                raise SupervisedWorkerFailure("worker_protocol_failed", "remote worker protocol failed closed")
            return message

    def _force_teardown(self, terminal_status: str) -> None:
        if self._closed:
            return
        try:
            exit_code = self._process.poll()
        except OSError:
            exit_code = None
        if exit_code is None:
            self._teardown["terminate_sent"] = True
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self._teardown["kill_sent"] = True
                try:
                    self._process.kill()
                    self._process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        try:
            exit_code = self._process.poll()
        except OSError:
            exit_code = None
        self._closed = True
        self._teardown["worker_exit_code"] = _local._normalize_process_exit_code(exit_code)
        self._teardown["worker_exit_verified"] = exit_code is not None
        self._teardown["terminal_status"] = terminal_status if exit_code is not None else "worker_teardown_unverified"

    def generate(self, *, input_bytes: bytes, prompt_bytes: bytes, config_bytes: bytes, request_bytes: bytes) -> bytes:
        if self._closed or self._process.poll() is not None:
            raise SupervisedWorkerFailure("worker_unavailable", "remote worker exited before generation")
        if self._generate_calls >= REMOTE_GENERATE_CALL_CAP:
            raise SupervisedWorkerFailure("generate_call_cap_exhausted", "remote generate call cap is exhausted")
        call_id = f"call-{uuid.uuid4().hex}"
        self._generate_calls += 1
        self._generation_started = True
        self._send({"protocol": REMOTE_WORKER_IPC_PROTOCOL, "kind": "generate", "call_id": call_id, "node_id": REMOTE_NODE_ID, "input_b64": _b64(input_bytes, "remote worker input"), "prompt_b64": _b64(prompt_bytes, "remote worker prompt"), "config_b64": _b64(config_bytes, "remote worker config"), "request_b64": _b64(request_bytes, "remote worker request")})
        try:
            message = self._receive(timeout=self._profile.timeout_seconds)
        except KeyboardInterrupt as exc:
            self._force_teardown("generation_cancelled")
            raise SupervisedWorkerFailure("generation_cancelled", "remote generation was cancelled") from exc
        if message.get("call_id") != call_id or message.get("worker_id") != self._worker_id:
            self._force_teardown("worker_failed")
            raise SupervisedWorkerFailure("worker_protocol_failed", "remote worker call identity drifted")
        if message.get("kind") == "generation_error":
            self._force_teardown("worker_failed")
            raise SupervisedWorkerFailure("backend_exception", "remote worker generation failed closed")
        data = _exact(message, ("protocol", "kind", "call_id", "worker_id", "raw_b64"), "remote generation result")
        if data["protocol"] != REMOTE_WORKER_IPC_PROTOCOL or data["kind"] != "generation_result":
            self._force_teardown("worker_failed")
            raise SupervisedWorkerFailure("worker_protocol_failed", "remote generation response drifted")
        raw = _decode_b64(data["raw_b64"], "remote generation raw")
        if not raw:
            self._force_teardown("worker_failed")
            raise SupervisedWorkerFailure("backend_exception", "remote generation returned empty raw bytes")
        return raw

    def close(self) -> dict[str, object]:
        if not self._closed:
            self._teardown["graceful_shutdown_requested"] = True
            try:
                self._send({"protocol": REMOTE_WORKER_IPC_PROTOCOL, "kind": "shutdown"})
                message = self._receive(timeout=20)
                if message.get("kind") != "shutdown_ack" or message.get("worker_id") != self._worker_id:
                    raise SupervisedWorkerFailure("worker_protocol_failed", "remote shutdown acknowledgement drifted")
                exit_code = _local._normalize_process_exit_code(self._process.wait(timeout=20))
                self._closed = True
                self._teardown["worker_exit_code"] = exit_code
                self._teardown["worker_exit_verified"] = exit_code is not None
                self._teardown["terminal_status"] = "normal_completed" if exit_code is not None else "worker_teardown_unverified"
            except (OSError, subprocess.SubprocessError, SupervisedWorkerFailure):
                self._force_teardown("worker_failed")
        return self.teardown_facts


class RemoteRuntime(NamedTuple):
    client: RemoteWorkerClient
    loaded_facts: Mapping[str, object]
    load_receipt: RemoteLoadReceipt


class RemoteWorkerStartFailure(Phase4RemoteQwenContractError):
    def __init__(self, message: str, *, teardown_facts: Mapping[str, object], stderr_bytes: bytes | None) -> None:
        super().__init__(message)
        self.teardown_facts = copy.deepcopy(dict(teardown_facts))
        self.stderr_bytes = None if stderr_bytes is None else bytes(stderr_bytes)


def start_remote_qwen_runtime(*, prepared: RemotePreparedExperiment, mirror: RemoteStreamMirror) -> RemoteRuntime:
    prepared.profile.validate()
    prepared.manifest.validate()
    _offline_process()
    runtime_claim = {
        "schema_version": f"{REMOTE_SCHEMA_PREFIX}.runtime_start_claim.v1",
        "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
        "manifest_identity": _identity(prepared.manifest.to_dict(), revision=REMOTE_MANIFEST_SCHEMA_VERSION),
        "profile_identity": _identity(prepared.profile.to_dict(), revision=REMOTE_PROFILE_SCHEMA_VERSION),
        "explicit_confirmation_received": True,
        "generate_call_cap": REMOTE_GENERATE_CALL_CAP,
        "retry_count": REMOTE_RETRY_COUNT,
        "action_state": _remote_action_state(model_action=False, remote_action=False),
    }
    _write_remote_once(prepared.result_root, REMOTE_RUNTIME_CLAIM_NAME, _canonical_bytes(runtime_claim))
    mirror.stage("load started")
    worker_bootstrap = (
        "import sys; from pathlib import Path; "
        "from req2web_runtime.phase4_remote_qwen import "
        "_run_remote_worker_protocol; "
        "raise SystemExit(_run_remote_worker_protocol(model_root=Path(sys.argv[1])))"
    )
    worker_executable, worker_pythonpath = _local._supervised_worker_python_runtime()
    environment = dict(os.environ)
    environment.update({
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "LANGSMITH_TRACING": "0",
        "LANGCHAIN_TRACING_V2": "0",
        "CUDA_VISIBLE_DEVICES": "0",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": worker_pythonpath,
    })
    try:
        process = subprocess.Popen(
            [worker_executable, "-u", "-c", worker_bootstrap, str(prepared.inventory["relocation_binding"]["target_model_root"])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Phase4RemoteQwenContractError("remote worker could not start") from exc
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise Phase4RemoteQwenContractError("remote worker IPC streams are unavailable")
    messages: queue.Queue[dict[str, object]] = queue.Queue()
    capture = _WorkerStderrCapture()
    stdout_thread = threading.Thread(target=_worker_stdout_reader, args=(process.stdout, messages), daemon=True)
    stdout_thread.start()
    stderr_thread = threading.Thread(target=_worker_stderr_reader, args=(process.stderr, capture, mirror.feed), daemon=True)
    stderr_thread.start()
    client = RemoteWorkerClient(process=process, messages=messages, stderr_capture=capture, stderr_thread=stderr_thread, profile=prepared.profile, wait_observer=mirror.heartbeat)
    try:
        client._send({"protocol": REMOTE_WORKER_IPC_PROTOCOL, "kind": "load", "profile_b64": _b64(prepared.profile.canonical_bytes(), "remote worker profile")})
        loaded = client._receive(timeout=REMOTE_LOAD_TIMEOUT_SECONDS)
        if loaded.get("kind") != "loaded":
            raise Phase4RemoteQwenContractError("remote worker load failed closed")
        data = _exact(loaded, ("protocol", "kind", "worker_id", "worker_pid", "loaded_facts"), "remote worker loaded")
        if data["protocol"] != REMOTE_WORKER_IPC_PROTOCOL or data["kind"] != "loaded" or data["worker_pid"] != process.pid or not isinstance(data["loaded_facts"], Mapping):
            raise Phase4RemoteQwenContractError("remote worker loaded identity drifted")
        client.bind_loaded(worker_id=_text(data["worker_id"], "remote worker id", pattern=_local._ID_RE), loaded_facts=data["loaded_facts"])
        load_receipt = RemoteLoadReceipt.create(profile=prepared.profile, loaded_facts=data["loaded_facts"])
        load_receipt.validate_against(prepared.profile)
        _write_remote_once(prepared.result_root, REMOTE_LOAD_RECEIPT_NAME, load_receipt.canonical_bytes())
        mirror.stage("load completed")
        return RemoteRuntime(client=client, loaded_facts=data["loaded_facts"], load_receipt=load_receipt)
    except (OSError, subprocess.SubprocessError, Phase4RemoteQwenContractError, SupervisedWorkerFailure) as exc:
        client._force_teardown("load_failed")
        raise RemoteWorkerStartFailure("remote worker load failed closed", teardown_facts=client.teardown_facts, stderr_bytes=client.stderr_bytes) from exc


class RemoteInvocationRecord(_CanonicalRecord):
    KEYS = (
        "schema_version", "record_id", "diagnostic_id", "pilot_id", "case_id", "request_id", "node_id",
        "manifest_identity", "policy_identity", "checkpoint_binding_identity", "prior_failure_identity",
        "d2_predecessor_binding_identity",
        "input_identity", "prompt_identity", "config_identity", "request_identity", "generate_call_index",
        "generate_call_cap", "retry_count", "record_fsync_required", "source_kind", "action_state",
    )
    SCHEMA_VERSION = REMOTE_INVOCATION_SCHEMA_VERSION

    @classmethod
    def create(cls, *, prepared: RemotePreparedExperiment) -> "RemoteInvocationRecord":
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "record_id": "pending",
            "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
            "pilot_id": REMOTE_PILOT_ID,
            "case_id": REMOTE_CASE_ID,
            "request_id": REMOTE_REQUEST_ID,
            "node_id": REMOTE_NODE_ID,
            "manifest_identity": _identity(prepared.manifest.to_dict(), revision=REMOTE_MANIFEST_SCHEMA_VERSION),
            "policy_identity": _build_remote_policy_ref(prepared.policy_raw),
            "checkpoint_binding_identity": _identity(prepared.checkpoint.binding.to_dict(), revision=REMOTE_CHECKPOINT_SCHEMA_VERSION),
            "prior_failure_identity": _identity(prepared.checkpoint.prior_failure.to_dict(), revision=_local.ATTEMPT_RESULT_SCHEMA_VERSION),
            "d2_predecessor_binding_identity": _identity(
                prepared.d2_predecessor.to_dict(),
                revision=REMOTE_PREDECESSOR_SCHEMA_VERSION,
            ),
            "input_identity": _identity(prepared.input_bytes, revision=REMOTE_INPUT_REVISION, identity_kind="raw_bytes"),
            "prompt_identity": _identity(prepared.prompt_bytes, revision=REMOTE_PROMPT_REVISION, identity_kind="raw_bytes"),
            "config_identity": _identity(prepared.config_bytes, revision=REMOTE_CONFIG_REVISION, identity_kind="raw_bytes"),
            "request_identity": _identity(prepared.request_bytes, revision=REMOTE_REQUEST_REVISION, identity_kind="raw_bytes"),
            "generate_call_index": 1,
            "generate_call_cap": REMOTE_GENERATE_CALL_CAP,
            "retry_count": REMOTE_RETRY_COUNT,
            "record_fsync_required": True,
            "source_kind": "remote_qwen_bf16",
            "action_state": _remote_action_state(model_action=True, remote_action=True),
        }
        root["record_id"] = _identity({key: value for key, value in root.items() if key != "record_id"}, revision=cls.SCHEMA_VERSION)["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="RemoteInvocationRecord")
        _sha(data["record_id"], "RemoteInvocationRecord.record_id")
        if data["diagnostic_id"] != REMOTE_DIAGNOSTIC_ID or data["pilot_id"] != REMOTE_PILOT_ID or data["case_id"] != REMOTE_CASE_ID or data["request_id"] != REMOTE_REQUEST_ID or data["node_id"] != REMOTE_NODE_ID:
            raise Phase4RemoteQwenContractError("remote invocation scope drifted")
        for key in (
            "manifest_identity",
            "policy_identity",
            "checkpoint_binding_identity",
            "prior_failure_identity",
            "d2_predecessor_binding_identity",
            "input_identity",
            "prompt_identity",
            "config_identity",
            "request_identity",
        ):
            _validate_identity(data[key], f"RemoteInvocationRecord.{key}")
        if data["generate_call_index"] != 1 or data["generate_call_cap"] != 1 or data["retry_count"] != 0 or data["record_fsync_required"] is not True or data["source_kind"] != "remote_qwen_bf16":
            raise Phase4RemoteQwenContractError("remote invocation call envelope drifted")
        _validate_remote_action_state(data["action_state"], "RemoteInvocationRecord.action_state", model_action=True, remote_action=True)
        expected = _identity({key: data[key] for key, value in data.items() if key != "record_id"}, revision=cls.SCHEMA_VERSION)["sha256"]
        if data["record_id"] != expected:
            raise Phase4RemoteQwenContractError("remote invocation identity drifted")


class RemoteSupervisorReceipt(_CanonicalRecord):
    KEYS = (
        "schema_version", "receipt_id", "diagnostic_id", "pilot_id", "worker_id", "worker_pid", "worker_exit_code",
        "worker_exit_verified", "graceful_shutdown_requested", "terminate_sent", "kill_sent", "generation_started",
        "terminal_status", "raw_status", "stderr_identity", "retry_performed", "completed_at_utc", "action_state",
    )
    SCHEMA_VERSION = REMOTE_SUPERVISOR_SCHEMA_VERSION

    @classmethod
    def create(cls, *, pilot_id: str, teardown: Mapping[str, object], generation_started: bool, raw_status: str, stderr_identity: Mapping[str, object] | None, terminal_status: str) -> "RemoteSupervisorReceipt":
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "receipt_id": "pending",
            "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
            "pilot_id": pilot_id,
            "worker_id": teardown.get("worker_id"),
            "worker_pid": teardown.get("worker_pid"),
            "worker_exit_code": teardown.get("worker_exit_code"),
            "worker_exit_verified": teardown.get("worker_exit_verified", False),
            "graceful_shutdown_requested": teardown.get("graceful_shutdown_requested", False),
            "terminate_sent": teardown.get("terminate_sent", False),
            "kill_sent": teardown.get("kill_sent", False),
            "generation_started": generation_started,
            "terminal_status": terminal_status,
            "raw_status": raw_status,
            "stderr_identity": None if stderr_identity is None else dict(stderr_identity),
            "retry_performed": False,
            "completed_at_utc": _utc_now(),
            "action_state": _remote_action_state(model_action=generation_started, remote_action=generation_started),
        }
        root["receipt_id"] = _identity({key: value for key, value in root.items() if key != "receipt_id"}, revision=cls.SCHEMA_VERSION)["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="RemoteSupervisorReceipt")
        _sha(data["receipt_id"], "RemoteSupervisorReceipt.receipt_id")
        if data["diagnostic_id"] != REMOTE_DIAGNOSTIC_ID or data["pilot_id"] != REMOTE_PILOT_ID:
            raise Phase4RemoteQwenContractError("remote supervisor identity drifted")
        if data["worker_id"] is not None:
            _text(data["worker_id"], "RemoteSupervisorReceipt.worker_id", pattern=_local._ID_RE)
        if data["worker_pid"] is not None:
            _integer(data["worker_pid"], "RemoteSupervisorReceipt.worker_pid", minimum=1)
        if data["worker_exit_code"] is not None:
            _integer(data["worker_exit_code"], "RemoteSupervisorReceipt.worker_exit_code", minimum=-2147483648, maximum=2147483647)
        for key in ("worker_exit_verified", "graceful_shutdown_requested", "terminate_sent", "kill_sent", "generation_started", "retry_performed"):
            _bool(data[key], f"RemoteSupervisorReceipt.{key}")
        if data["retry_performed"] is not False or data["terminal_status"] not in {"normal_completed", "load_failed", "generation_completed", "generation_timeout", "generation_cancelled", "generation_failed", "worker_failed", "worker_teardown_unverified"}:
            raise Phase4RemoteQwenContractError("remote supervisor terminal/retry status drifted")
        if data["worker_exit_verified"] is not True or data["worker_exit_code"] is None:
            raise Phase4RemoteQwenContractError("remote worker exit was not verified")
        if data["raw_status"] not in {"captured", "not_captured"}:
            raise Phase4RemoteQwenContractError("remote supervisor raw status drifted")
        if data["stderr_identity"] is not None:
            _validate_identity(data["stderr_identity"], "RemoteSupervisorReceipt.stderr_identity")
        _text(data["completed_at_utc"], "RemoteSupervisorReceipt.completed_at_utc")
        _validate_remote_action_state(data["action_state"], "RemoteSupervisorReceipt.action_state", model_action=bool(data["generation_started"]), remote_action=bool(data["generation_started"]))
        expected = _identity({key: value for key, value in data.items() if key != "receipt_id"}, revision=cls.SCHEMA_VERSION)["sha256"]
        if data["receipt_id"] != expected:
            raise Phase4RemoteQwenContractError("remote supervisor identity drifted")


class RemoteResultRecord(_CanonicalRecord):
    KEYS = (
        "schema_version", "result_id", "diagnostic_id", "pilot_id", "case_id", "request_id", "node_id",
        "generation_terminal", "supervisor_terminal_status", "call", "raw_capture", "parse", "node_contract", "registry",
        "node_model_pass", "integrated", "f4", "composition", "assembler", "downstream", "formal_quality", "h1_or_gold",
        "training", "data_authoring", "remote_action_occurred", "model_action_occurred", "checkpoint_binding_identity",
        "prior_failure_identity", "d2_predecessor_binding_identity", "load_receipt_identity", "supervisor_identity",
        "failure_code", "source_kind", "action_state",
    )
    SCHEMA_VERSION = REMOTE_RESULT_SCHEMA_VERSION

    @classmethod
    def create(cls, **kwargs: object) -> "RemoteResultRecord":
        root = dict(kwargs)
        root["schema_version"] = cls.SCHEMA_VERSION
        root["result_id"] = "pending"
        root["diagnostic_id"] = REMOTE_DIAGNOSTIC_ID
        root["pilot_id"] = REMOTE_PILOT_ID
        root["case_id"] = REMOTE_CASE_ID
        root["request_id"] = REMOTE_REQUEST_ID
        root["node_id"] = REMOTE_NODE_ID
        root["result_id"] = _identity({key: value for key, value in root.items() if key != "result_id"}, revision=cls.SCHEMA_VERSION)["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(data, schema=cls.SCHEMA_VERSION, name="RemoteResultRecord")
        _sha(data["result_id"], "RemoteResultRecord.result_id")
        if data["diagnostic_id"] != REMOTE_DIAGNOSTIC_ID or data["pilot_id"] != REMOTE_PILOT_ID or data["case_id"] != REMOTE_CASE_ID or data["request_id"] != REMOTE_REQUEST_ID or data["node_id"] != REMOTE_NODE_ID:
            raise Phase4RemoteQwenContractError("remote result scope drifted")
        if data["generation_terminal"] not in {"not_started", "load_failed", "generation_completed", "generation_timeout", "generation_cancelled", "generation_failed", "worker_failed"}:
            raise Phase4RemoteQwenContractError("remote result generation terminal drifted")
        _text(data["supervisor_terminal_status"], "RemoteResultRecord.supervisor_terminal_status")
        call = _exact(data["call"], ("generate_calls", "generate_call_cap", "retry_count", "timeout_seconds"), "RemoteResultRecord.call")
        if call["generate_call_cap"] != 1 or call["retry_count"] != 0 or call["timeout_seconds"] != REMOTE_TIMEOUT_SECONDS or call["generate_calls"] not in {0, 1}:
            raise Phase4RemoteQwenContractError("remote result call budget drifted")
        raw = _exact(data["raw_capture"], ("status", "relative_path", "identity"), "RemoteResultRecord.raw_capture")
        if raw["status"] == "captured_authoritative_complete":
            if raw["relative_path"] != REMOTE_RAW_NAME or raw["identity"] is None:
                raise Phase4RemoteQwenContractError("remote raw capture binding drifted")
            _validate_identity(raw["identity"], "RemoteResultRecord.raw_capture.identity")
        elif raw != {"status": "not_captured", "relative_path": None, "identity": None}:
            raise Phase4RemoteQwenContractError("remote raw capture status drifted")
        parse = _exact(data["parse"], ("status",), "RemoteResultRecord.parse")
        contract = _exact(data["node_contract"], ("status",), "RemoteResultRecord.node_contract")
        registry = _exact(data["registry"], ("status",), "RemoteResultRecord.registry")
        if parse["status"] not in {"not_executed", "parsed", "failed"} or contract["status"] not in {"not_executed", "passed", "failed"} or registry["status"] not in {"not_executed", "passed", "failed"}:
            raise Phase4RemoteQwenContractError("remote result parse/contract/registry status drifted")
        if data["node_model_pass"] is not ((parse["status"] == "parsed") and (contract["status"] == "passed") and (registry["status"] == "passed") and (raw["status"] == "captured_authoritative_complete")):
            raise Phase4RemoteQwenContractError("remote node_model_pass is not source-exclusive")
        if data["integrated"] is not False or data["f4"] != "not_executed" or data["composition"] != "not_executed" or data["assembler"] != "not_executed" or data["downstream"] != "not_executed":
            raise Phase4RemoteQwenContractError("remote result downstream boundary drifted")
        for key in ("formal_quality", "h1_or_gold", "training", "data_authoring", "remote_action_occurred", "model_action_occurred"):
            _bool(data[key], f"RemoteResultRecord.{key}")
        for key in (
            "checkpoint_binding_identity",
            "prior_failure_identity",
            "d2_predecessor_binding_identity",
        ):
            _validate_identity(data[key], f"RemoteResultRecord.{key}")
        if data["load_receipt_identity"] is not None:
            _validate_identity(data["load_receipt_identity"], "RemoteResultRecord.load_receipt_identity")
        _validate_identity(data["supervisor_identity"], "RemoteResultRecord.supervisor_identity")
        if data["source_kind"] != "remote_qwen_bf16":
            raise Phase4RemoteQwenContractError("remote result source kind drifted")
        failure = data["failure_code"]
        if data["node_model_pass"]:
            if failure is not None:
                raise Phase4RemoteQwenContractError("passing remote result carries failure")
        elif type(failure) is not str or not failure:
            raise Phase4RemoteQwenContractError("failed remote result lacks failure code")
        _validate_remote_action_state(data["action_state"], "RemoteResultRecord.action_state", model_action=bool(data["model_action_occurred"]), remote_action=bool(data["remote_action_occurred"]))
        expected = _identity({key: value for key, value in data.items() if key != "result_id"}, revision=cls.SCHEMA_VERSION)["sha256"]
        if data["result_id"] != expected:
            raise Phase4RemoteQwenContractError("remote result identity drifted")


def _parse_and_register_remote_f3(raw: bytes, checkpoint: RemoteCheckpointReplay) -> tuple[str, str, str, Mapping[str, object] | None, str | None]:
    try:
        # Preserve the original bytes, then follow the existing P4-03 owning
        # parser path: ordinary JSON syntax is accepted before exact contract
        # and stable-ID validation.
        parsed = _strict_json(raw, require_canonical=False)
    except Exception:
        return "failed", "not_executed", "not_executed", None, "node_contract_invalid"
    try:
        from req2web_orchestration.phase4_graph import phase4_register_node_output, phase4_validate_node_output

        validated = phase4_validate_node_output(REMOTE_NODE_ID, parsed, checkpoint.authority_state)
    except Exception:
        return "parsed", "failed", "not_executed", None, "node_contract_invalid"
    try:
        registered = phase4_register_node_output(checkpoint.authority_state, REMOTE_NODE_ID, validated)
    except Exception:
        return "parsed", "passed", "failed", None, "node_contract_invalid"
    return "parsed", "passed", "passed", registered, None


class RemoteRevalidationRecord(_CanonicalRecord):
    """No-model replay of the immutable D3 raw bytes through owning authority."""

    KEYS = (
        "schema_version",
        "revalidation_id",
        "diagnostic_id",
        "pilot_id",
        "case_id",
        "request_id",
        "node_id",
        "source_result_file_identity",
        "source_result_id",
        "source_raw_identity",
        "checkpoint_binding_identity",
        "d2_predecessor_binding_identity",
        "parse",
        "node_contract",
        "registry",
        "node_model_pass",
        "revalidated_output_identity",
        "revalidated_state_identity",
        "source_generate_calls",
        "revalidation_generate_calls",
        "retry_count",
        "source_model_action_occurred",
        "revalidation_model_action_occurred",
        "remote_action_occurred",
        "historical_result_unchanged",
        "f4",
        "composition",
        "assembler",
        "downstream",
        "formal_quality",
        "h1_or_gold",
        "training",
        "data_authoring",
        "failure_code",
        "action_state",
    )
    SCHEMA_VERSION = REMOTE_REVALIDATION_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        source_result_raw: bytes,
        source_result: RemoteResultRecord,
        source_raw: bytes,
        checkpoint: RemoteCheckpointReplay,
        d2_predecessor: RemoteD2PredecessorBinding,
        parse_status: str,
        contract_status: str,
        registry_status: str,
        revalidated_output_identity: Mapping[str, object] | None,
        revalidated_state_identity: Mapping[str, object] | None,
        failure_code: str | None,
    ) -> "RemoteRevalidationRecord":
        passed = (
            parse_status == "parsed"
            and contract_status == "passed"
            and registry_status == "passed"
            and revalidated_output_identity is not None
            and revalidated_state_identity is not None
        )
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "revalidation_id": "pending",
            "diagnostic_id": REMOTE_DIAGNOSTIC_ID,
            "pilot_id": REMOTE_PILOT_ID,
            "case_id": REMOTE_CASE_ID,
            "request_id": REMOTE_REQUEST_ID,
            "node_id": REMOTE_NODE_ID,
            "source_result_file_identity": _identity(
                source_result_raw,
                revision=f"{REMOTE_SCHEMA_PREFIX}.result_file.v1",
                identity_kind="raw_bytes",
            ),
            "source_result_id": source_result.result_id,
            "source_raw_identity": _identity(
                source_raw,
                revision=REMOTE_RAW_IDENTITY_REVISION,
                identity_kind="raw_bytes",
            ),
            "checkpoint_binding_identity": _identity(
                checkpoint.binding.to_dict(),
                revision=REMOTE_CHECKPOINT_SCHEMA_VERSION,
            ),
            "d2_predecessor_binding_identity": _identity(
                d2_predecessor.to_dict(),
                revision=REMOTE_PREDECESSOR_SCHEMA_VERSION,
            ),
            "parse": {"status": parse_status},
            "node_contract": {"status": contract_status},
            "registry": {"status": registry_status},
            "node_model_pass": passed,
            "revalidated_output_identity": (
                None
                if revalidated_output_identity is None
                else dict(revalidated_output_identity)
            ),
            "revalidated_state_identity": (
                None
                if revalidated_state_identity is None
                else dict(revalidated_state_identity)
            ),
            "source_generate_calls": 1,
            "revalidation_generate_calls": 0,
            "retry_count": 0,
            "source_model_action_occurred": True,
            "revalidation_model_action_occurred": False,
            "remote_action_occurred": True,
            "historical_result_unchanged": True,
            "f4": "not_executed",
            "composition": "not_executed",
            "assembler": "not_executed",
            "downstream": "not_executed",
            "formal_quality": False,
            "h1_or_gold": False,
            "training": False,
            "data_authoring": False,
            "failure_code": failure_code,
            "action_state": _remote_action_state(
                model_action=False, remote_action=True
            ),
        }
        root["revalidation_id"] = _identity(
            {
                key: value
                for key, value in root.items()
                if key != "revalidation_id"
            },
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _local._common_record(
            data, schema=cls.SCHEMA_VERSION, name="RemoteRevalidationRecord"
        )
        _sha(data["revalidation_id"], "RemoteRevalidationRecord.revalidation_id")
        if (
            data["diagnostic_id"] != REMOTE_DIAGNOSTIC_ID
            or data["pilot_id"] != REMOTE_PILOT_ID
            or data["case_id"] != REMOTE_CASE_ID
            or data["request_id"] != REMOTE_REQUEST_ID
            or data["node_id"] != REMOTE_NODE_ID
        ):
            raise Phase4RemoteQwenContractError(
                "remote revalidation scope drifted"
            )
        for key in (
            "source_result_file_identity",
            "source_raw_identity",
            "checkpoint_binding_identity",
            "d2_predecessor_binding_identity",
        ):
            _validate_identity(data[key], f"RemoteRevalidationRecord.{key}")
        _sha(data["source_result_id"], "RemoteRevalidationRecord.source_result_id")
        parse = _exact(data["parse"], ("status",), "RemoteRevalidationRecord.parse")
        contract = _exact(
            data["node_contract"],
            ("status",),
            "RemoteRevalidationRecord.node_contract",
        )
        registry = _exact(
            data["registry"], ("status",), "RemoteRevalidationRecord.registry"
        )
        passed = (
            parse["status"] == "parsed"
            and contract["status"] == "passed"
            and registry["status"] == "passed"
        )
        if data["node_model_pass"] is not passed:
            raise Phase4RemoteQwenContractError(
                "remote revalidation pass state drifted"
            )
        if passed:
            _validate_identity(
                data["revalidated_output_identity"],
                "RemoteRevalidationRecord.revalidated_output_identity",
            )
            _validate_identity(
                data["revalidated_state_identity"],
                "RemoteRevalidationRecord.revalidated_state_identity",
            )
            if data["failure_code"] is not None:
                raise Phase4RemoteQwenContractError(
                    "passing remote revalidation carries failure"
                )
        elif (
            data["revalidated_output_identity"] is not None
            or data["revalidated_state_identity"] is not None
            or type(data["failure_code"]) is not str
            or not data["failure_code"]
        ):
            raise Phase4RemoteQwenContractError(
                "failed remote revalidation boundary drifted"
            )
        if (
            data["source_generate_calls"] != 1
            or data["revalidation_generate_calls"] != 0
            or data["retry_count"] != 0
            or data["source_model_action_occurred"] is not True
            or data["revalidation_model_action_occurred"] is not False
            or data["remote_action_occurred"] is not True
            or data["historical_result_unchanged"] is not True
            or data["f4"] != "not_executed"
            or data["composition"] != "not_executed"
            or data["assembler"] != "not_executed"
            or data["downstream"] != "not_executed"
        ):
            raise Phase4RemoteQwenContractError(
                "remote revalidation execution boundary drifted"
            )
        for key in (
            "formal_quality",
            "h1_or_gold",
            "training",
            "data_authoring",
        ):
            if data[key] is not False:
                raise Phase4RemoteQwenContractError(
                    "remote revalidation claim boundary drifted"
                )
        _validate_remote_action_state(
            data["action_state"],
            "RemoteRevalidationRecord.action_state",
            model_action=False,
            remote_action=True,
        )
        expected = _identity(
            {
                key: value
                for key, value in data.items()
                if key != "revalidation_id"
            },
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["revalidation_id"] != expected:
            raise Phase4RemoteQwenContractError(
                "remote revalidation identity drifted"
            )


def revalidate_remote_qwen_bf16_f3_result(
    *, result_root: Path
) -> RemoteRevalidationRecord:
    """Revalidate immutable D3 raw bytes without another model generate."""

    _offline_process()
    if (
        not isinstance(result_root, Path)
        or not result_root.is_absolute()
        or result_root.is_symlink()
        or not result_root.is_dir()
        or not (result_root / REMOTE_ROOT_MARKER_NAME).is_file()
    ):
        raise Phase4RemoteQwenContractError(
            "remote D3 result root is unavailable or unsafe"
        )
    source_result_raw = _read_input_file(result_root / REMOTE_RESULT_NAME, "D3 result")
    source_result = RemoteResultRecord.from_bytes(source_result_raw)
    if (
        source_result.generation_terminal != "generation_completed"
        or source_result.node_model_pass is not False
        or source_result.failure_code != "node_contract_invalid"
        or source_result.raw_capture["status"]
        != "captured_authoritative_complete"
    ):
        raise Phase4RemoteQwenContractError(
            "D3 source result is not the eligible parser-failure outcome"
        )
    source_raw = _read_input_file(result_root / REMOTE_RAW_NAME, "D3 raw response")
    if source_result.raw_capture["identity"] != _identity(
        source_raw,
        revision=REMOTE_RAW_IDENTITY_REVISION,
        identity_kind="raw_bytes",
    ):
        raise Phase4RemoteQwenContractError(
            "D3 source result does not bind live raw bytes"
        )
    checkpoint = replay_remote_checkpoint(
        checkpoint_packet=result_root / REMOTE_CHECKPOINT_PACKET_NAME,
        checkpoint_receipt=result_root / REMOTE_CHECKPOINT_RECEIPT_NAME,
        prior_f3_failure=result_root / REMOTE_PRIOR_FAILURE_NAME,
    )
    d2_result_raw = _read_input_file(
        result_root / REMOTE_PREDECESSOR_RESULT_NAME, "D2 predecessor result"
    )
    d2_raw = _read_input_file(
        result_root / REMOTE_PREDECESSOR_RAW_NAME, "D2 predecessor raw"
    )
    d2_predecessor = RemoteD2PredecessorBinding.from_bytes(
        _read_input_file(
            result_root / REMOTE_PREDECESSOR_BINDING_NAME,
            "D2 predecessor binding",
        )
    )
    d2_predecessor.validate_against(
        result_raw=d2_result_raw, raw_response=d2_raw
    )
    if (
        source_result.checkpoint_binding_identity
        != _identity(
            checkpoint.binding.to_dict(),
            revision=REMOTE_CHECKPOINT_SCHEMA_VERSION,
        )
        or source_result.d2_predecessor_binding_identity
        != _identity(
            d2_predecessor.to_dict(),
            revision=REMOTE_PREDECESSOR_SCHEMA_VERSION,
        )
    ):
        raise Phase4RemoteQwenContractError(
            "D3 source result replay binding drifted"
        )
    (
        parse_status,
        contract_status,
        registry_status,
        registered_state,
        failure_code,
    ) = _parse_and_register_remote_f3(source_raw, checkpoint)
    output_identity: Mapping[str, object] | None = None
    state_identity: Mapping[str, object] | None = None
    if registered_state is not None:
        revalidated_output = _canonical_bytes(
            _strict_json(source_raw, require_canonical=False)
        )
        revalidated_state = _canonical_bytes(registered_state)
        output_identity = _identity(
            revalidated_output,
            revision=f"{REMOTE_SCHEMA_PREFIX}.revalidated_f3_output.v1",
            identity_kind="raw_bytes",
        )
        state_identity = _identity(
            revalidated_state,
            revision=f"{REMOTE_SCHEMA_PREFIX}.revalidated_f3_state.v1",
            identity_kind="raw_bytes",
        )
        _write_remote_once(
            result_root, REMOTE_REVALIDATED_OUTPUT_NAME, revalidated_output
        )
        _write_remote_once(
            result_root, REMOTE_REVALIDATED_STATE_NAME, revalidated_state
        )
    record = RemoteRevalidationRecord.create(
        source_result_raw=source_result_raw,
        source_result=source_result,
        source_raw=source_raw,
        checkpoint=checkpoint,
        d2_predecessor=d2_predecessor,
        parse_status=parse_status,
        contract_status=contract_status,
        registry_status=registry_status,
        revalidated_output_identity=output_identity,
        revalidated_state_identity=state_identity,
        failure_code=failure_code,
    )
    _write_remote_once(
        result_root, REMOTE_REVALIDATION_NAME, record.canonical_bytes()
    )
    return record


def _stderr_identity(stderr_bytes: bytes | None) -> dict[str, object] | None:
    if stderr_bytes is None:
        return None
    return _identity(stderr_bytes, revision=REMOTE_STDERR_SCHEMA_VERSION, identity_kind="raw_bytes")


def execute_remote_qwen_bf16_f3(*, prepared: RemotePreparedExperiment, runtime: RemoteRuntime, mirror: RemoteStreamMirror) -> RemoteResultRecord:
    """Execute exactly one generate, preserve raw first, then validate F3."""

    invocation = RemoteInvocationRecord.create(prepared=prepared)
    _write_remote_once(prepared.result_root, REMOTE_INVOCATION_NAME, invocation.canonical_bytes())
    generation_terminal = "generation_completed"
    raw_status = "not_captured"
    raw_identity: dict[str, object] | None = None
    parse_status = "not_executed"
    contract_status = "not_executed"
    registry_status = "not_executed"
    failure_code: str | None = None
    registered_state: Mapping[str, object] | None = None
    try:
        raw = runtime.client.generate(
            input_bytes=prepared.input_bytes,
            prompt_bytes=prepared.prompt_bytes,
            config_bytes=prepared.config_bytes,
            request_bytes=prepared.request_bytes,
        )
        if type(raw) is not bytes or not raw:
            raise SupervisedWorkerFailure("backend_exception", "remote raw response is invalid")
        # This returns only after final-file fsync.  No parser or registry call
        # is reachable before this point.
        _write_remote_once(prepared.result_root, REMOTE_RAW_NAME, raw)
        raw_identity = _identity(raw, revision=REMOTE_RAW_IDENTITY_REVISION, identity_kind="raw_bytes")
        raw_status = "captured_authoritative_complete"
        parse_status, contract_status, registry_status, registered_state, failure_code = _parse_and_register_remote_f3(raw, prepared.checkpoint)
    except SupervisedWorkerFailure as exc:
        generation_terminal = {
            "generation_timeout": "generation_timeout",
            "generation_cancelled": "generation_cancelled",
            "backend_exception": "generation_failed",
            "worker_failed": "worker_failed",
        }.get(exc.failure_code, "generation_failed")
        failure_code = "generation_timeout" if exc.failure_code == "generation_timeout" else "generation_cancelled" if exc.failure_code == "generation_cancelled" else "generation_failed"
        mirror.stage(generation_terminal.replace("_", " "))
    except Exception:
        generation_terminal = "generation_failed"
        failure_code = "generation_failed"
        mirror.stage("generation failed")
    teardown = runtime.client.close()
    mirror.stage("worker exit")
    stderr_bytes = runtime.client.stderr_bytes
    if stderr_bytes is not None:
        _write_remote_once(prepared.result_root, REMOTE_STDERR_NAME, stderr_bytes)
    stderr_id = _stderr_identity(stderr_bytes)
    supervisor_terminal = str(teardown.get("terminal_status", "worker_teardown_unverified"))
    if not bool(teardown.get("worker_exit_verified")) and generation_terminal == "generation_completed":
        generation_terminal = "worker_failed"
        failure_code = "worker_teardown_unverified"
    supervisor = RemoteSupervisorReceipt.create(
        pilot_id=REMOTE_PILOT_ID,
        teardown=teardown,
        generation_started=runtime.client.generation_started,
        raw_status="captured" if raw_status == "captured_authoritative_complete" else "not_captured",
        stderr_identity=stderr_id,
        terminal_status=supervisor_terminal,
    )
    _write_remote_once(prepared.result_root, REMOTE_SUPERVISOR_NAME, supervisor.canonical_bytes())
    node_pass = (
        raw_status == "captured_authoritative_complete"
        and parse_status == "parsed"
        and contract_status == "passed"
        and registry_status == "passed"
        and bool(teardown.get("worker_exit_verified"))
    )
    if node_pass:
        failure_code = None
    elif failure_code is None:
        failure_code = "node_contract_invalid"
    result = RemoteResultRecord.create(
        generation_terminal=generation_terminal,
        supervisor_terminal_status=supervisor_terminal,
        call={"generate_calls": 1 if runtime.client.generation_started else 0, "generate_call_cap": 1, "retry_count": 0, "timeout_seconds": REMOTE_TIMEOUT_SECONDS},
        raw_capture={"status": raw_status, "relative_path": REMOTE_RAW_NAME if raw_identity is not None else None, "identity": raw_identity},
        parse={"status": parse_status},
        node_contract={"status": contract_status},
        registry={"status": registry_status},
        node_model_pass=node_pass,
        integrated=False,
        f4="not_executed",
        composition="not_executed",
        assembler="not_executed",
        downstream="not_executed",
        formal_quality=False,
        h1_or_gold=False,
        training=False,
        data_authoring=False,
        remote_action_occurred=True,
        model_action_occurred=runtime.client.generation_started,
        checkpoint_binding_identity=_identity(prepared.checkpoint.binding.to_dict(), revision=REMOTE_CHECKPOINT_SCHEMA_VERSION),
        prior_failure_identity=_identity(prepared.checkpoint.prior_failure.to_dict(), revision=_local.ATTEMPT_RESULT_SCHEMA_VERSION),
        d2_predecessor_binding_identity=_identity(
            prepared.d2_predecessor.to_dict(),
            revision=REMOTE_PREDECESSOR_SCHEMA_VERSION,
        ),
        load_receipt_identity=_identity(runtime.load_receipt.to_dict(), revision=REMOTE_LOAD_RECEIPT_SCHEMA_VERSION),
        supervisor_identity=_identity(supervisor.to_dict(), revision=REMOTE_SUPERVISOR_SCHEMA_VERSION),
        failure_code=failure_code,
        source_kind="remote_qwen_bf16",
        action_state=_remote_action_state(model_action=runtime.client.generation_started, remote_action=True),
    )
    _write_remote_once(prepared.result_root, REMOTE_RESULT_NAME, result.canonical_bytes())
    return result


def _result_after_start_failure(prepared: RemotePreparedExperiment, failure: RemoteWorkerStartFailure) -> RemoteResultRecord:
    stderr_id = _stderr_identity(failure.stderr_bytes)
    if failure.stderr_bytes is not None:
        _write_remote_once(prepared.result_root, REMOTE_STDERR_NAME, failure.stderr_bytes)
    supervisor_terminal = str(failure.teardown_facts.get("terminal_status", "load_failed"))
    supervisor = RemoteSupervisorReceipt.create(
        pilot_id=REMOTE_PILOT_ID,
        teardown=failure.teardown_facts,
        generation_started=False,
        raw_status="not_captured",
        stderr_identity=stderr_id,
        terminal_status=supervisor_terminal,
    )
    _write_remote_once(prepared.result_root, REMOTE_SUPERVISOR_NAME, supervisor.canonical_bytes())
    result = RemoteResultRecord.create(
        generation_terminal="load_failed",
        supervisor_terminal_status=supervisor_terminal,
        call={"generate_calls": 0, "generate_call_cap": 1, "retry_count": 0, "timeout_seconds": REMOTE_TIMEOUT_SECONDS},
        raw_capture={"status": "not_captured", "relative_path": None, "identity": None},
        parse={"status": "not_executed"},
        node_contract={"status": "not_executed"},
        registry={"status": "not_executed"},
        node_model_pass=False,
        integrated=False,
        f4="not_executed",
        composition="not_executed",
        assembler="not_executed",
        downstream="not_executed",
        formal_quality=False,
        h1_or_gold=False,
        training=False,
        data_authoring=False,
        remote_action_occurred=False,
        model_action_occurred=False,
        checkpoint_binding_identity=_identity(prepared.checkpoint.binding.to_dict(), revision=REMOTE_CHECKPOINT_SCHEMA_VERSION),
        prior_failure_identity=_identity(prepared.checkpoint.prior_failure.to_dict(), revision=_local.ATTEMPT_RESULT_SCHEMA_VERSION),
        d2_predecessor_binding_identity=_identity(
            prepared.d2_predecessor.to_dict(),
            revision=REMOTE_PREDECESSOR_SCHEMA_VERSION,
        ),
        load_receipt_identity=None,
        supervisor_identity=_identity(supervisor.to_dict(), revision=REMOTE_SUPERVISOR_SCHEMA_VERSION),
        failure_code="load_failed",
        source_kind="remote_qwen_bf16",
        action_state=_remote_action_state(model_action=False, remote_action=False),
    )
    _write_remote_once(prepared.result_root, REMOTE_RESULT_NAME, result.canonical_bytes())
    return result


def run_remote_qwen_bf16_f3(
    *,
    model_root: Path,
    integrity_evidence: Path,
    checkpoint_packet: Path,
    checkpoint_receipt: Path,
    prior_f3_failure: Path,
    predecessor_d2_result: Path,
    predecessor_d2_raw: Path,
    result_root: Path,
    confirm_one_remote_generate: bool,
    console: object | None = None,
) -> RemoteResultRecord:
    """Run the explicitly confirmed, single remote Qwen BF16 F3 attempt."""

    if confirm_one_remote_generate is not True:
        raise Phase4RemoteQwenContractError("--confirm-one-remote-generate is required before worker start")
    _offline_process()
    mirror = RemoteStreamMirror(console)
    mirror.stage("preflight started")
    prepared = prepare_remote_qwen_bf16_f3(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        checkpoint_packet=checkpoint_packet,
        checkpoint_receipt=checkpoint_receipt,
        prior_f3_failure=prior_f3_failure,
        predecessor_d2_result=predecessor_d2_result,
        predecessor_d2_raw=predecessor_d2_raw,
        result_root=result_root,
    )
    mirror.stage("preflight completed")
    try:
        runtime = start_remote_qwen_runtime(prepared=prepared, mirror=mirror)
    except RemoteWorkerStartFailure as exc:
        return _result_after_start_failure(prepared, exc)
    return execute_remote_qwen_bf16_f3(prepared=prepared, runtime=runtime, mirror=mirror)


__all__ = [
    "REMOTE_DIAGNOSTIC_ID",
    "REMOTE_POLICY_PATH",
    "REMOTE_POLICY_RELATIVE_PATH",
    "REMOTE_PILOT_ID",
    "REMOTE_MODEL_ID",
    "REMOTE_MODEL_REVISION",
    "REMOTE_RAW_NAME",
    "Phase4RemoteQwenContractError",
    "RemoteQwenPolicy",
    "load_remote_qwen_policy",
    "RemoteQwenProfile",
    "RemoteLoadReceipt",
    "RemoteCheckpointBinding",
    "RemoteCheckpointReplay",
    "replay_remote_checkpoint",
    "validate_remote_model_inventory",
    "RemotePreflightManifest",
    "RemotePreparedExperiment",
    "prepare_remote_qwen_bf16_f3",
    "RemoteStreamMirror",
    "RemoteWorkerClient",
    "RemoteRuntime",
    "RemoteInvocationRecord",
    "RemoteSupervisorReceipt",
    "RemoteResultRecord",
    "RemoteRevalidationRecord",
    "execute_remote_qwen_bf16_f3",
    "revalidate_remote_qwen_bf16_f3_result",
    "run_remote_qwen_bf16_f3",
    "_run_remote_worker_protocol",
]
