"""Frozen Phase 5 RTX 5090 execution profile derived from Phase 4 evidence."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Sequence


SCHEMA_VERSION = "req2web.phase5.rtx5090.execution_profile.v1"
PHASE4_EXIT_COMMIT = "9cd02e9040c2aaa41b4ddc44a3b0146770370b2b"
PHASE4_SUMMARY_FILE_SHA256 = (
    "6683ab9bd44d09e7382ae130a8020850d0b0487050d03c9e7b817af98d0ba411"
)
PHASE4_SUMMARY_CANONICAL_SHA256 = (
    "e0e166bc9a2a537d1adcf3e49293b3281bf051ac59ecb87200450e8452b5676c"
)
MODEL_INVENTORY_SHA256 = (
    "5cbe7e2948a034efb6df80e4bada474646409273d4cd733baea8ea12fc910894"
)
MODEL_ROOT_SHA256 = "50a7bac9dbb2cf9b5f75338467e7f4130c6560d979d3191ded2f6ce84efd1024"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_id(prefix: str, value: object) -> str:
    return f"{prefix}-{sha256(_canonical(value)).hexdigest()}"


def _exact(value: object, keys: Sequence[str], name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError(f"{name} has invalid keys")
    return dict(value)


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _expected_body() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "selected_for_future_phase5_execution_no_action",
        "source_evidence": {
            "phase4_exit_commit": PHASE4_EXIT_COMMIT,
            "phase4_final_result_root": "p4-05-stability-full-direct-20260805-g",
            "phase4_summary_file_sha256": PHASE4_SUMMARY_FILE_SHA256,
            "phase4_summary_canonical_sha256": PHASE4_SUMMARY_CANONICAL_SHA256,
            "phase4_run_id": "p4-05-remote-qwen-stability-full-direct-94bc6fd05f644c13",
            "phase4_evidence_scope": "synthetic_path3_non_h1",
            "phase4_profile_reuse_kind": "configuration_only_not_result_reuse",
        },
        "platform": {
            "os_family": "linux_posix_container",
            "distribution_and_kernel_frozen": False,
            "user": "root",
            "gpu_class": "nvidia_geforce_rtx_5090",
            "device_index": 0,
            "cuda_visible_devices": "0",
            "phase4_observed_gpu_uuid": "GPU-9df7500b-8530-eaa4-053e-a81e912714a9",
            "future_instance_gpu_uuid": None,
            "phase4_observed_total_vram_bytes": 34_190_917_632,
            "phase4_representative_free_vram_bytes": 33_669_775_360,
        },
        "paths": {
            "phase4_repository_reference": (
                "/root/autodl-tmp/req2web-phase4-qwen9b/"
                "repository-bbac57d28652"
            ),
            "model_root": "/root/autodl-tmp/req2web-phase4-qwen9b/model",
            "model_integrity_evidence": (
                "/root/autodl-tmp/req2web-phase4-qwen9b/evidence/"
                "local_integrity_c202236235762e1c871ad0ccb60c8ee5ba337b9a.json"
            ),
            "python_executable": (
                "/root/autodl-tmp/req2web-qwen35-27b/runtime/bin/python"
            ),
            "phase5_repository_root_template": (
                "/root/autodl-tmp/req2web-phase5-qwen9b/"
                "repository-<action-commit-prefix>"
            ),
            "phase5_result_root_template": (
                "/root/autodl-tmp/req2web-phase5-qwen9b/results/<run-id>"
            ),
            "local_return_root_template": (
                "D:\\VSCodeProjects\\CrowdMEP\\<phase5-result-root>"
            ),
            "phase4_result_reuse_allowed": False,
        },
        "runtime": {
            "python_version": "3.11.15",
            "torch_version": "2.7.1+cu128",
            "transformers_version": "5.14.1",
            "accelerate_version": "1.14.0",
            "cuda_version": "12.8",
            "nvidia_driver_version": "595.71.05",
            "p4_02a_pinned_dependencies": {
                "langgraph": "1.2.9",
                "langchain_core": "1.5.3",
                "pydantic": "2.13.4",
                "pydantic_core": "2.46.4",
            },
            "p4_05_live_profile_directly_attested_dependencies": [
                "python",
                "torch",
                "transformers",
                "accelerate",
                "cuda",
                "nvidia_driver",
            ],
            "local_files_only": True,
            "network_allowed_during_runtime": False,
            "telemetry_allowed": False,
            "tracing_allowed": False,
            "python_unbuffered": True,
        },
        "model": {
            "model_id": "Qwen/Qwen3.5-9B",
            "revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
            "inventory_file_count": 16,
            "inventory_total_bytes": 19_329_393_661,
            "inventory_sha256": MODEL_INVENTORY_SHA256,
            "model_root_sha256": MODEL_ROOT_SHA256,
            "model_class": "Qwen3_5ForConditionalGeneration",
            "processor_class": "Qwen3VLProcessor",
            "dtype": "bfloat16",
            "compute_dtype": "bfloat16",
            "quantization": "none",
            "cpu_offload": False,
            "parameter_devices": ["cuda:0"],
            "parameter_dtypes": ["torch.bfloat16"],
            "is_loaded_in_4bit": False,
            "is_loaded_in_8bit": False,
            "optional_fast_path_install_allowed_at_action_time": False,
            "validated_optional_fast_path_behavior": "transformers_torch_fallback",
        },
        "generation": {
            "native_context_tokens": 262_144,
            "input_truncation": False,
            "output_truncation": False,
            "fixed_max_new_tokens": None,
            "max_new_tokens_policy": "remaining_native_context_only",
            "complete_json_stopping_required": True,
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 0,
            "per_generate_wall_timeout_seconds": 1_200,
            "generate_start_consumes_call": True,
            "per_node_generate_call_cap": 1,
            "automatic_retry": False,
            "retry_count": 0,
            "model_repair_call_cap": 0,
        },
        "worker_supervisor": {
            "worker_isolation": "one_worker_per_case",
            "model_loads_per_case": 1,
            "node_order": ["F1", "F2", "F3", "F4"],
            "cross_case_worker_reuse_allowed": False,
            "supervisor_must_remain_alive": True,
            "worker_process_group_required": True,
            "start_new_session_required": True,
            "normal_shutdown_ack_required": True,
            "normal_worker_wait_seconds": 20,
            "terminate_signal": "SIGTERM",
            "terminate_grace_seconds": 2,
            "kill_signal": "SIGKILL",
            "kill_wait_seconds": 10,
            "stdout_reader_join_required": True,
            "stderr_reader_join_required": True,
            "worker_exit_verification_required": True,
        },
        "streaming": {
            "worker_stdout_role": "strict_json_ipc_only",
            "worker_stderr_role": "versioned_stream_events",
            "event_types": [
                "load_started",
                "load_completed",
                "generation_started",
                "token_delta",
                "generation_completed",
                "worker_failed",
            ],
            "token_delta_encoding": "base64_bytes",
            "console_mirror_and_flush_required": True,
            "worker_stderr_identity_required": True,
            "partial_stream_counts_as_raw_capture": False,
        },
        "raw_first": {
            "pre_call_write_once_files": [
                "input.json",
                "prompt.json",
                "config.json",
                "request.json",
                "pre_call_record.json",
            ],
            "pre_call_flush_and_fsync_required": True,
            "raw_response_filename": "raw_response.bin",
            "raw_response_write_once_and_fsync_before_parse": True,
            "parse_before_raw_capture_allowed": False,
            "timeout_cancel_parse_or_schema_failure_consumes_started_call": True,
            "raw_not_formed_if_complete_bytes_not_fsynced": True,
            "post_generation_semantic_rewrite_allowed": False,
        },
        "resume": {
            "development_checkpoint_reuse_in_formal_h1_allowed": False,
            "case_boundary_recovery_allowed": True,
            "completed_case_requires_live_identity_revalidation": True,
            "partial_case_generate_resume_allowed": False,
            "new_directory_or_process_resets_budget": False,
            "prompt_revision_resets_budget": False,
            "worker_reload_resets_budget": False,
        },
        "transfer_and_closeout": {
            "source_identity": "phase5_action_commit_required",
            "uncommitted_remote_worktree_as_source_allowed": False,
            "upload_exclusions": [
                "credentials",
                "docx",
                "evaluator_only_plaintext",
                "h1_gold_plaintext",
                "historical_tmp",
                "rico_reference_only",
                "unrelated_outputs",
            ],
            "return_tar_required": True,
            "return_tar_sha256_required": True,
            "return_inventory_required": True,
            "local_validation_before_instance_release_required": True,
            "shutdown_equals_release": False,
            "no_card_equals_release": False,
            "release_and_credential_revocation_separately_recorded": True,
            "model_runtime_evidence_delete_by_default": False,
        },
        "known_failure_controls": {
            "windows_source_path_is_remote_model_identity": False,
            "pip_record_bytes_must_match_across_hosts": False,
            "site_packages_record_mutation_allowed": False,
            "slow_first_token_is_automatically_stalled": False,
            "ctrl_c_is_sufficient_worker_teardown": False,
            "truncation_to_force_contract_pass_allowed": False,
            "optional_kernel_install_after_freeze_allowed": False,
            "generic_ref_normalization_extends_to_semantic_arrays": False,
            "new_result_directory_creates_new_budget": False,
        },
        "action_state": {
            "profile_selected_by_owner": True,
            "instance_provided": False,
            "instance_identity_frozen": False,
            "price_time_storage_caps_frozen": False,
            "ssh_fingerprint_frozen": False,
            "source_action_commit_frozen": False,
            "phase5_sealed_runner_ready": False,
            "external_action_authorized": False,
            "ssh_connected": False,
            "model_loaded": False,
            "holdout_executed": False,
        },
    }


def _validate_payload(value: object) -> dict[str, object]:
    record = _exact(value, ("profile_id", *_expected_body().keys()), "execution profile")
    body = {key: record[key] for key in record if key != "profile_id"}
    if body != _expected_body():
        raise ValueError("execution profile content drifted")
    expected_id = _record_id("phase5-rtx5090-profile", body)
    if record["profile_id"] != expected_id:
        raise ValueError("execution profile id drifted")
    _digest(body["source_evidence"]["phase4_summary_file_sha256"], "summary file hash")
    _digest(
        body["source_evidence"]["phase4_summary_canonical_sha256"],
        "summary canonical hash",
    )
    _digest(body["model"]["inventory_sha256"], "model inventory hash")
    _digest(body["model"]["model_root_sha256"], "model root hash")
    return record


@dataclass(frozen=True)
class Phase5RTX5090ExecutionProfile:
    canonical_json: str
    digest_sha256: str

    @classmethod
    def from_dict(cls, value: object) -> "Phase5RTX5090ExecutionProfile":
        payload = _validate_payload(value)
        canonical = _canonical(payload)
        result = cls(canonical.decode("utf-8"), sha256(canonical).hexdigest())
        result.validate()
        return result

    @classmethod
    def from_json_bytes(cls, value: bytes) -> "Phase5RTX5090ExecutionProfile":
        if not isinstance(value, bytes) or not value:
            raise ValueError("execution profile JSON must be non-empty bytes")
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("execution profile JSON is invalid") from exc
        if _canonical(parsed) != value:
            raise ValueError("execution profile JSON is not canonical")
        return cls.from_dict(parsed)

    def validate(self) -> None:
        if not isinstance(self.canonical_json, str) or not self.canonical_json:
            raise ValueError("stored execution profile JSON must be non-empty text")
        try:
            parsed = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise ValueError("stored execution profile JSON is invalid") from exc
        canonical = _canonical(parsed)
        if canonical.decode("utf-8") != self.canonical_json:
            raise ValueError("stored execution profile JSON is not canonical")
        if sha256(canonical).hexdigest() != _digest(
            self.digest_sha256,
            "execution profile digest",
        ):
            raise ValueError("stored execution profile digest drifted")
        _validate_payload(parsed)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):
            raise ValueError("stored execution profile root is not an object")
        return value

    def canonical_json_bytes(self) -> bytes:
        self.validate()
        return self.canonical_json.encode("utf-8")

    def sha256(self) -> str:
        self.validate()
        return self.digest_sha256


def build_phase5_rtx5090_execution_profile() -> Phase5RTX5090ExecutionProfile:
    """Build the frozen no-action profile; this performs no runtime action."""

    body = _expected_body()
    return Phase5RTX5090ExecutionProfile.from_dict(
        {"profile_id": _record_id("phase5-rtx5090-profile", body), **body}
    )
