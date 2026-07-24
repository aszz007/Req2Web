"""Tier B local metadata declarations with no external-action capability.

The records in this module are canonical, payload-free syntactic declarations.
They are not live-artifact authorities and cannot make an external-action gate
eligible. A later typed/live validator must rebind every declared identity to
its authoritative object before any external action can be considered.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Mapping


TIER_B_MANAGER_RUN_PLAN_SCHEMA_VERSION = "req2web.runtime.tier_b_manager_run_plan.v1"
TIER_B_EXTERNAL_ACTION_BINDING_DECLARATION_SCHEMA_VERSION = (
    "req2web.runtime.tier_b_external_action_binding_declaration.v1"
)
TIER_B_MANAGER_RUN_PLAN_ID_PREFIX = "tier-b-manager-run-plan-"
TIER_B_EXTERNAL_ACTION_BINDING_DECLARATION_ID_PREFIX = "tier-b-binding-declaration-"

_CANDIDATE_MODEL = "Qwen/Qwen3.5-9B"
_MODEL_LICENSE = "Apache-2.0"
_TIER_A_CODE_COMMIT_SHA = "a366ca22bc6b7c35d1818483c4d0f21148fd5735"
_D17_DECISION_SET_SHA256 = "6a9eb554d77d3e872e194545459c48a951d257718f71c037dda434312bba7669"
_D17_FIELD_POLICY_SHA256 = "fb12efc6a0bd0d61b7ab4fea020c5e8053283b58406da37326d7cc02a7e47438"
_PLAN_STATUS = "manager_recorded_recommendation_not_authorization"
_DECLARATION_STATUS = "syntactic_binding_declaration_complete_not_live_validated"
_SOURCE_CLASSES = ("project_authored", "synthetic")
_NETWORK_PHASES = (
    "provision_source_transfer",
    "controlled_acquisition",
    "offline_execution",
    "controlled_result_return_closeout",
)
_APPROVED_ACQUISITION_HOSTS = (
    "pypi.org",
    "files.pythonhosted.org",
    "download.pytorch.org",
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "cas-bridge.xethub.hf.co",
    "transfer.xethub.hf.co",
)
_GIB = 1024 * 1024 * 1024
_MIB = 1024 * 1024

_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{2,95}$")
_CASE_ID = re.compile(r"^[a-z][a-z0-9_-]{2,95}$")
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_METADATA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}$")
_PYTHON_VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
_PACKAGE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:\+[A-Za-z0-9.]+)?$")
_LEAK_MARKERS = (
    "secret", "token", "cookie", "password", "bearer",
    "payload", "raw-output", "raw_output", "gold", "reference-only",
    "reference_only", "api.openai.com", "http:", "https:", "file:", "\\", "/",
)

_PLAN_KEYS = (
    "schema_version", "plan_id", "plan_status", "candidate_model", "model_revision",
    "model_license", "execution_path", "h1_allowed", "formal_quality_allowed",
    "profile_name", "service_mode", "endpoint_mode", "runtime_architecture",
    "runtime_python", "runtime_pytorch", "runtime_transformers", "base_image_identity",
    "artifact_integrity_manifest_required", "precision", "quantization",
    "text_only", "thinking_enabled", "do_sample", "max_total_tokens",
    "max_new_tokens", "seed", "gpu_class", "min_vram_mib", "min_host_ram_gib",
    "disk_free_gib", "max_managed_disk_usage_gib", "max_parallel_generations",
    "setup_timeout_seconds", "model_load_timeout_seconds", "call_timeout_seconds",
    "case_timeout_seconds", "cancel_grace_seconds", "provider_retry_per_case",
    "provider_retry_total", "model_repair_call_budget", "network_phases",
    "acquisition_hosts", "model_process_offline", "source_result_transfer_mode",
    "max_unit_price_cny_fen", "max_billable_minutes", "max_total_spend_cny_fen",
    "max_fixtures", "max_initial_calls", "max_total_generation_calls",
    "max_model_download_bytes", "max_project_ingress_bytes", "max_result_egress_bytes",
    "credential_mode", "ordinary_log_mode", "debug_logging_enabled",
    "redaction_policy_version", "remote_retention_deadline_minutes",
    "local_archive_retention_days", "remote_cleanup_mode", "authorization_term_days",
    "renewal_by_silence_allowed", "tier_a_code_commit_sha", "d17_decision_set_sha256",
    "d17_field_policy_sha256", "external_action_gate_eligible",
    "external_action_authorized", "external_egress_allowed", "model_downloaded",
    "model_loaded", "provider_invoked", "project_data_transferred",
    "real_credentials_used", "paid_resource_created", "run_occurred",
)
_PLAN_WITHOUT_ID = tuple(key for key in _PLAN_KEYS if key != "plan_id")

_ARTIFACT_PREFIXES = (
    "d17_manifest", "provider_visible_payload", "selection_record",
    "input_view_artifact", "prompt_artifact", "config_artifact", "local_request",
    "pre_invocation_audit", "preparation_record", "tier_a_08_bundle",
    "same_case_frozen_g0_reference", "model_artifact_integrity_manifest",
    "runtime_environment_image_record",
)
_DERIVED_POLICY_PREFIXES = (
    "network_policy", "budget_policy", "credential_log_redaction_policy",
    "retention_cleanup_policy", "authorization_expiry_policy",
)
_DECLARATION_KEYS = (
    "schema_version", "declaration_id", "declaration_status", "manager_run_plan_id",
    "manager_run_plan_sha256", "manager_run_plan_byte_length", "case_id", "source_class",
    "tier_a_code_commit_sha", "d17_decision_set_sha256", "d17_field_policy_sha256",
    *(field for prefix in _ARTIFACT_PREFIXES for field in (
        f"{prefix}_id", f"{prefix}_sha256", f"{prefix}_byte_length"
    )),
    *(field for prefix in _DERIVED_POLICY_PREFIXES for field in (
        f"{prefix}_id", f"{prefix}_sha256", f"{prefix}_byte_length"
    )),
    "external_action_gate_eligible", "external_action_authorized",
    "external_egress_allowed", "model_downloaded", "model_loaded", "provider_invoked",
    "project_data_transferred", "real_credentials_used", "paid_resource_created",
    "run_occurred", "h1_accessed", "formal_quality_claimed",
)
_DECLARATION_WITHOUT_ID = tuple(key for key in _DECLARATION_KEYS if key != "declaration_id")

_REPLACEABLE_PLAN_FIELDS = frozenset({
    "model_revision", "runtime_python", "runtime_pytorch", "runtime_transformers",
    "base_image_identity", "max_total_tokens", "max_new_tokens", "seed",
    "min_vram_mib", "min_host_ram_gib", "disk_free_gib",
    "max_managed_disk_usage_gib", "setup_timeout_seconds", "model_load_timeout_seconds",
    "call_timeout_seconds", "case_timeout_seconds", "cancel_grace_seconds",
    "provider_retry_per_case", "provider_retry_total", "max_unit_price_cny_fen",
    "max_billable_minutes", "max_total_spend_cny_fen", "max_fixtures",
    "max_initial_calls", "max_total_generation_calls", "max_model_download_bytes",
    "max_project_ingress_bytes", "max_result_egress_bytes",
    "remote_retention_deadline_minutes", "local_archive_retention_days",
    "authorization_term_days", "acquisition_hosts",
})
_NUMERIC_LIMITS = {
    "max_total_tokens": (1, 16384), "max_new_tokens": (1, 4096),
    "seed": (0, 2**31 - 1), "min_vram_mib": (30000, 32768),
    "min_host_ram_gib": (60, 1024), "disk_free_gib": (1, 4096),
    "max_managed_disk_usage_gib": (1, 65), "setup_timeout_seconds": (1, 2700),
    "model_load_timeout_seconds": (1, 720), "call_timeout_seconds": (1, 120),
    "case_timeout_seconds": (1, 600), "cancel_grace_seconds": (1, 30),
    "provider_retry_per_case": (0, 1), "provider_retry_total": (0, 2),
    "max_unit_price_cny_fen": (1, 288), "max_billable_minutes": (1, 360),
    "max_total_spend_cny_fen": (1, 2500), "max_fixtures": (1, 2),
    "max_initial_calls": (1, 2), "max_total_generation_calls": (1, 4),
    "max_model_download_bytes": (1, 21 * _GIB),
    "max_project_ingress_bytes": (1, 200 * _MIB),
    "max_result_egress_bytes": (1, 2 * _GIB),
    "remote_retention_deadline_minutes": (1, 120),
    "local_archive_retention_days": (1, 30), "authorization_term_days": (1, 7),
}


class TierBReadinessError(ValueError):
    """A declaration is noncanonical or outside the approved no-action boundary."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_constant(value: str) -> object:
    raise TierBReadinessError("tier_b_non_finite_json_rejected")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TierBReadinessError("tier_b_duplicate_json_key")
        result[key] = value
    return result


def _load_canonical_json(raw: object) -> object:
    if type(raw) is not bytes:
        raise TierBReadinessError("tier_b_bytes_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TierBReadinessError) as exc:
        raise TierBReadinessError("tier_b_json_invalid") from exc
    if _canonical_bytes(value) != raw:
        raise TierBReadinessError("tier_b_bytes_noncanonical")
    return value


def _exact_mapping(value: object, keys: tuple[str, ...], code: str) -> Mapping[str, object]:
    if type(value) is not dict or set(value) != set(keys):
        raise TierBReadinessError(code)
    return value


def _strict_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise TierBReadinessError(f"tier_b_{name}_invalid")
    return value


def _strict_int(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise TierBReadinessError(f"tier_b_{name}_invalid")
    return value


def _fixed_string(value: object, name: str, expected: str) -> str:
    if type(value) is not str or value != expected:
        raise TierBReadinessError(f"tier_b_{name}_invalid")
    return value


def _safe_metadata(value: object, name: str) -> str:
    if type(value) is not str or _METADATA.fullmatch(value) is None:
        raise TierBReadinessError(f"tier_b_{name}_invalid")
    if any(marker in value.lower() for marker in _LEAK_MARKERS):
        raise TierBReadinessError(f"tier_b_{name}_invalid")
    return value


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise TierBReadinessError(f"tier_b_{name}_invalid")
    if any(marker in value.lower() for marker in _LEAK_MARKERS):
        raise TierBReadinessError(f"tier_b_{name}_invalid")
    return value


def _case_id(value: object) -> str:
    if type(value) is not str or _CASE_ID.fullmatch(value) is None:
        raise TierBReadinessError("tier_b_case_id_invalid")
    if any(marker in value.lower() for marker in _LEAK_MARKERS):
        raise TierBReadinessError("tier_b_case_id_invalid")
    return value


def _sha(value: object, name: str) -> str:
    if type(value) is not str or _HEX_64.fullmatch(value) is None:
        raise TierBReadinessError(f"tier_b_{name}_invalid")
    return value


def _commit_sha(value: object, name: str) -> str:
    if type(value) is not str or _HEX_40.fullmatch(value) is None:
        raise TierBReadinessError(f"tier_b_{name}_invalid")
    return value


def _acquisition_hosts(value: object) -> tuple[str, ...]:
    if type(value) is not list or not value:
        raise TierBReadinessError("tier_b_acquisition_hosts_invalid")
    if any(type(item) is not str or _HOSTNAME.fullmatch(item) is None for item in value):
        raise TierBReadinessError("tier_b_acquisition_hosts_invalid")
    hosts = tuple(value)
    expected_order = tuple(host for host in _APPROVED_ACQUISITION_HOSTS if host in hosts)
    if len(set(hosts)) != len(hosts) or hosts != expected_order:
        raise TierBReadinessError("tier_b_acquisition_hosts_invalid")
    return hosts


def _network_phases(value: object) -> tuple[str, ...]:
    if type(value) is not list or tuple(value) != _NETWORK_PHASES:
        raise TierBReadinessError("tier_b_network_phases_invalid")
    return tuple(value)


def _plan_default_root() -> dict[str, object]:
    return {
        "schema_version": TIER_B_MANAGER_RUN_PLAN_SCHEMA_VERSION,
        "plan_status": _PLAN_STATUS, "candidate_model": _CANDIDATE_MODEL,
        "model_revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
        "model_license": _MODEL_LICENSE, "execution_path": "path_3",
        "h1_allowed": False, "formal_quality_allowed": False,
        "profile_name": "quality_experiment", "service_mode": "in_process_local_adapter",
        "endpoint_mode": "no_http_endpoint", "runtime_architecture": "direct_transformers",
        "runtime_python": "3.11", "runtime_pytorch": "2.7.1+cu128",
        "runtime_transformers": "5.14.1", "base_image_identity": "immutable_image_identity_required",
        "artifact_integrity_manifest_required": True, "precision": "bf16",
        "quantization": "none", "text_only": True, "thinking_enabled": False,
        "do_sample": False, "max_total_tokens": 16384, "max_new_tokens": 4096,
        "seed": 20260724, "gpu_class": "RTX_5090_32GB_x1", "min_vram_mib": 30000,
        "min_host_ram_gib": 60, "disk_free_gib": 80, "max_managed_disk_usage_gib": 65,
        "max_parallel_generations": 1, "setup_timeout_seconds": 2700,
        "model_load_timeout_seconds": 720, "call_timeout_seconds": 120,
        "case_timeout_seconds": 600, "cancel_grace_seconds": 30,
        "provider_retry_per_case": 1, "provider_retry_total": 2,
        "model_repair_call_budget": 0, "network_phases": list(_NETWORK_PHASES),
        "acquisition_hosts": list(_APPROVED_ACQUISITION_HOSTS), "model_process_offline": True,
        "source_result_transfer_mode": "provider_assigned_ssh_only",
        "max_unit_price_cny_fen": 288, "max_billable_minutes": 360,
        "max_total_spend_cny_fen": 2500, "max_fixtures": 2, "max_initial_calls": 2,
        "max_total_generation_calls": 4, "max_model_download_bytes": 21 * _GIB,
        "max_project_ingress_bytes": 200 * _MIB, "max_result_egress_bytes": 2 * _GIB,
        "credential_mode": "no_provider_api_or_model_token",
        "ordinary_log_mode": "identity_hash_length_status_timing_resource_only",
        "debug_logging_enabled": False, "redaction_policy_version": "req2web.tier_b.redaction.v1",
        "remote_retention_deadline_minutes": 120, "local_archive_retention_days": 30,
        "remote_cleanup_mode": "delete_work_cache_release_instance_revoke_access",
        "authorization_term_days": 7, "renewal_by_silence_allowed": False,
        "tier_a_code_commit_sha": _TIER_A_CODE_COMMIT_SHA,
        "d17_decision_set_sha256": _D17_DECISION_SET_SHA256,
        "d17_field_policy_sha256": _D17_FIELD_POLICY_SHA256,
        "external_action_gate_eligible": False, "external_action_authorized": False,
        "external_egress_allowed": False, "model_downloaded": False, "model_loaded": False,
        "provider_invoked": False, "project_data_transferred": False,
        "real_credentials_used": False, "paid_resource_created": False, "run_occurred": False,
    }


def _plan_root(plan: "TierBManagerRunPlan") -> dict[str, object]:
    return {
        key: list(getattr(plan, key)) if key in {"network_phases", "acquisition_hosts"}
        else getattr(plan, key) for key in _PLAN_WITHOUT_ID
    }


def _validate_plan(plan: "TierBManagerRunPlan") -> None:
    root = _plan_root(plan)
    _exact_mapping({"plan_id": plan.plan_id, **root}, _PLAN_KEYS, "tier_b_plan_exact_keys_invalid")
    fixed_strings = {
        "schema_version": TIER_B_MANAGER_RUN_PLAN_SCHEMA_VERSION, "plan_status": _PLAN_STATUS,
        "candidate_model": _CANDIDATE_MODEL, "model_license": _MODEL_LICENSE,
        "execution_path": "path_3", "profile_name": "quality_experiment",
        "service_mode": "in_process_local_adapter", "endpoint_mode": "no_http_endpoint",
        "runtime_architecture": "direct_transformers", "precision": "bf16",
        "quantization": "none", "gpu_class": "RTX_5090_32GB_x1",
        "source_result_transfer_mode": "provider_assigned_ssh_only",
        "credential_mode": "no_provider_api_or_model_token",
        "ordinary_log_mode": "identity_hash_length_status_timing_resource_only",
        "redaction_policy_version": "req2web.tier_b.redaction.v1",
        "remote_cleanup_mode": "delete_work_cache_release_instance_revoke_access",
    }
    for name, expected in fixed_strings.items():
        _fixed_string(getattr(plan, name), name, expected)
    _commit_sha(plan.model_revision, "model_revision")
    _commit_sha(plan.tier_a_code_commit_sha, "tier_a_code_commit_sha")
    if plan.tier_a_code_commit_sha != _TIER_A_CODE_COMMIT_SHA:
        raise TierBReadinessError("tier_b_tier_a_code_commit_sha_invalid")
    for name in ("d17_decision_set_sha256", "d17_field_policy_sha256"):
        _sha(getattr(plan, name), name)
    if plan.d17_decision_set_sha256 != _D17_DECISION_SET_SHA256 or plan.d17_field_policy_sha256 != _D17_FIELD_POLICY_SHA256:
        raise TierBReadinessError("tier_b_d17_policy_binding_invalid")
    _safe_metadata(plan.base_image_identity, "base_image_identity")
    if type(plan.runtime_python) is not str or _PYTHON_VERSION.fullmatch(plan.runtime_python) is None:
        raise TierBReadinessError("tier_b_runtime_python_invalid")
    for name in ("runtime_pytorch", "runtime_transformers"):
        value = getattr(plan, name)
        if type(value) is not str or _PACKAGE_VERSION.fullmatch(value) is None:
            raise TierBReadinessError(f"tier_b_{name}_invalid")
    _network_phases(list(plan.network_phases)); _acquisition_hosts(list(plan.acquisition_hosts))
    fixed_bools = {
        "h1_allowed": False, "formal_quality_allowed": False,
        "artifact_integrity_manifest_required": True, "text_only": True,
        "thinking_enabled": False, "do_sample": False, "model_process_offline": True,
        "debug_logging_enabled": False, "renewal_by_silence_allowed": False,
        "external_action_gate_eligible": False, "external_action_authorized": False,
        "external_egress_allowed": False, "model_downloaded": False, "model_loaded": False,
        "provider_invoked": False, "project_data_transferred": False,
        "real_credentials_used": False, "paid_resource_created": False, "run_occurred": False,
    }
    for name, expected in fixed_bools.items():
        if _strict_bool(getattr(plan, name), name) is not expected:
            raise TierBReadinessError("tier_b_plan_no_action_state_invalid")
    if type(plan.max_parallel_generations) is not int or plan.max_parallel_generations != 1:
        raise TierBReadinessError("tier_b_max_parallel_generations_invalid")
    if type(plan.model_repair_call_budget) is not int or plan.model_repair_call_budget != 0:
        raise TierBReadinessError("tier_b_model_repair_call_budget_invalid")
    for name, (minimum, maximum) in _NUMERIC_LIMITS.items():
        _strict_int(getattr(plan, name), name, minimum, maximum)
    if plan.max_new_tokens > plan.max_total_tokens:
        raise TierBReadinessError("tier_b_context_relation_invalid")
    if plan.max_initial_calls != plan.max_fixtures:
        raise TierBReadinessError("tier_b_initial_fixture_relation_invalid")
    if plan.provider_retry_total > plan.provider_retry_per_case * plan.max_fixtures:
        raise TierBReadinessError("tier_b_retry_relation_invalid")
    if plan.max_total_generation_calls != plan.max_initial_calls + plan.provider_retry_total:
        raise TierBReadinessError("tier_b_total_call_relation_invalid")
    if not (plan.cancel_grace_seconds < plan.call_timeout_seconds and plan.call_timeout_seconds + plan.cancel_grace_seconds <= plan.case_timeout_seconds):
        raise TierBReadinessError("tier_b_timeout_relation_invalid")
    if plan.max_unit_price_cny_fen * plan.max_billable_minutes > plan.max_total_spend_cny_fen * 60:
        raise TierBReadinessError("tier_b_compute_cost_relation_invalid")
    managed_bytes = plan.max_managed_disk_usage_gib * _GIB
    if plan.max_model_download_bytes + plan.max_project_ingress_bytes + plan.max_result_egress_bytes > managed_bytes:
        raise TierBReadinessError("tier_b_managed_disk_budget_relation_invalid")
    if plan.max_managed_disk_usage_gib > plan.disk_free_gib:
        raise TierBReadinessError("tier_b_managed_disk_free_relation_invalid")
    expected_id = TIER_B_MANAGER_RUN_PLAN_ID_PREFIX + _sha256(_canonical_bytes(root))[:20]
    if type(plan.plan_id) is not str or plan.plan_id != expected_id:
        raise TierBReadinessError("tier_b_plan_identity_invalid")


@dataclass(frozen=True)
class TierBManagerRunPlan:
    """A canonical manager-recorded metadata plan, never an authorization."""

    schema_version: str
    plan_id: str
    plan_status: str
    candidate_model: str
    model_revision: str
    model_license: str
    execution_path: str
    h1_allowed: bool
    formal_quality_allowed: bool
    profile_name: str
    service_mode: str
    endpoint_mode: str
    runtime_architecture: str
    runtime_python: str
    runtime_pytorch: str
    runtime_transformers: str
    base_image_identity: str
    artifact_integrity_manifest_required: bool
    precision: str
    quantization: str
    text_only: bool
    thinking_enabled: bool
    do_sample: bool
    max_total_tokens: int
    max_new_tokens: int
    seed: int
    gpu_class: str
    min_vram_mib: int
    min_host_ram_gib: int
    disk_free_gib: int
    max_managed_disk_usage_gib: int
    max_parallel_generations: int
    setup_timeout_seconds: int
    model_load_timeout_seconds: int
    call_timeout_seconds: int
    case_timeout_seconds: int
    cancel_grace_seconds: int
    provider_retry_per_case: int
    provider_retry_total: int
    model_repair_call_budget: int
    network_phases: tuple[str, ...]
    acquisition_hosts: tuple[str, ...]
    model_process_offline: bool
    source_result_transfer_mode: str
    max_unit_price_cny_fen: int
    max_billable_minutes: int
    max_total_spend_cny_fen: int
    max_fixtures: int
    max_initial_calls: int
    max_total_generation_calls: int
    max_model_download_bytes: int
    max_project_ingress_bytes: int
    max_result_egress_bytes: int
    credential_mode: str
    ordinary_log_mode: str
    debug_logging_enabled: bool
    redaction_policy_version: str
    remote_retention_deadline_minutes: int
    local_archive_retention_days: int
    remote_cleanup_mode: str
    authorization_term_days: int
    renewal_by_silence_allowed: bool
    tier_a_code_commit_sha: str
    d17_decision_set_sha256: str
    d17_field_policy_sha256: str
    external_action_gate_eligible: bool
    external_action_authorized: bool
    external_egress_allowed: bool
    model_downloaded: bool
    model_loaded: bool
    provider_invoked: bool
    project_data_transferred: bool
    real_credentials_used: bool
    paid_resource_created: bool
    run_occurred: bool

    @classmethod
    def create(cls, *, overrides: Mapping[str, object] | None = None) -> "TierBManagerRunPlan":
        if cls is not _CAPTURED_PLAN_TYPE:
            raise TierBReadinessError("tier_b_plan_type_invalid")
        root = _plan_default_root()
        if overrides is not None:
            if type(overrides) is not dict or not set(overrides).issubset(_REPLACEABLE_PLAN_FIELDS):
                raise TierBReadinessError("tier_b_plan_overrides_invalid")
            if "acquisition_hosts" in overrides and type(overrides["acquisition_hosts"]) is not list:
                raise TierBReadinessError("tier_b_acquisition_hosts_invalid")
            root.update(overrides)
        plan = cls(
            plan_id=TIER_B_MANAGER_RUN_PLAN_ID_PREFIX + _sha256(_canonical_bytes(root))[:20],
            **{key: tuple(value) if key in {"network_phases", "acquisition_hosts"} else value for key, value in root.items()},
        )
        if type(plan) is not _CAPTURED_PLAN_TYPE:
            raise TierBReadinessError("tier_b_plan_type_invalid")
        _validate_plan(plan)
        return plan

    @classmethod
    def from_dict(cls, payload: object) -> "TierBManagerRunPlan":
        if cls is not _CAPTURED_PLAN_TYPE:
            raise TierBReadinessError("tier_b_plan_type_invalid")
        data = _exact_mapping(payload, _PLAN_KEYS, "tier_b_plan_exact_keys_invalid")
        if type(data["network_phases"]) is not list:
            raise TierBReadinessError("tier_b_network_phases_invalid")
        if type(data["acquisition_hosts"]) is not list:
            raise TierBReadinessError("tier_b_acquisition_hosts_invalid")
        plan = cls(**{key: tuple(data[key]) if key in {"network_phases", "acquisition_hosts"} else data[key] for key in _PLAN_KEYS})
        if type(plan) is not _CAPTURED_PLAN_TYPE:
            raise TierBReadinessError("tier_b_plan_type_invalid")
        _validate_plan(plan)
        return plan

    @classmethod
    def from_bytes(cls, raw: object) -> "TierBManagerRunPlan":
        if cls is not _CAPTURED_PLAN_TYPE:
            raise TierBReadinessError("tier_b_plan_type_invalid")
        return cls.from_dict(_load_canonical_json(raw))

    def validate(self) -> None:
        if type(self) is not _CAPTURED_PLAN_TYPE:
            raise TierBReadinessError("tier_b_plan_type_invalid")
        _validate_plan(self)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {key: list(getattr(self, key)) if key in {"network_phases", "acquisition_hosts"} else getattr(self, key) for key in _PLAN_KEYS}

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256(self.canonical_bytes())


def _policy_binding(prefix: str, payload: Mapping[str, object]) -> tuple[str, str, int]:
    raw = _canonical_bytes(dict(payload)); digest = _sha256(raw)
    return f"tier-b-{prefix}-{digest[:20]}", digest, len(raw)


def _derived_policy_bindings(plan: TierBManagerRunPlan) -> dict[str, object]:
    sections = {
        "network_policy": {
            "service_mode": plan.service_mode, "endpoint_mode": plan.endpoint_mode,
            "runtime_architecture": plan.runtime_architecture,
            "network_phases": list(plan.network_phases), "acquisition_hosts": list(plan.acquisition_hosts),
            "model_process_offline": plan.model_process_offline,
            "source_result_transfer_mode": plan.source_result_transfer_mode,
        },
        "budget_policy": {name: getattr(plan, name) for name in (
            "max_unit_price_cny_fen", "max_billable_minutes", "max_total_spend_cny_fen",
            "max_fixtures", "max_initial_calls", "provider_retry_per_case", "provider_retry_total",
            "model_repair_call_budget", "max_total_generation_calls", "max_model_download_bytes",
            "max_project_ingress_bytes", "max_result_egress_bytes", "max_managed_disk_usage_gib",
        )},
        "credential_log_redaction_policy": {
            "credential_mode": plan.credential_mode, "ordinary_log_mode": plan.ordinary_log_mode,
            "debug_logging_enabled": plan.debug_logging_enabled,
            "redaction_policy_version": plan.redaction_policy_version,
        },
        "retention_cleanup_policy": {
            "remote_retention_deadline_minutes": plan.remote_retention_deadline_minutes,
            "local_archive_retention_days": plan.local_archive_retention_days,
            "remote_cleanup_mode": plan.remote_cleanup_mode,
        },
        "authorization_expiry_policy": {
            "authorization_term_days": plan.authorization_term_days,
            "renewal_by_silence_allowed": plan.renewal_by_silence_allowed,
            "external_action_gate_eligible": plan.external_action_gate_eligible,
        },
    }
    result: dict[str, object] = {}
    for prefix, payload in sections.items():
        identity, digest, length = _policy_binding(prefix.replace("_", "-"), payload)
        result[f"{prefix}_id"] = identity; result[f"{prefix}_sha256"] = digest
        result[f"{prefix}_byte_length"] = length
    return result


def _declaration_root(declaration: "TierBExternalActionBindingDeclaration") -> dict[str, object]:
    return {key: getattr(declaration, key) for key in _DECLARATION_WITHOUT_ID}


def _validate_artifact_triple(declaration: "TierBExternalActionBindingDeclaration", prefix: str) -> None:
    _identifier(getattr(declaration, f"{prefix}_id"), f"{prefix}_id")
    _sha(getattr(declaration, f"{prefix}_sha256"), f"{prefix}_sha256")
    _strict_int(getattr(declaration, f"{prefix}_byte_length"), f"{prefix}_byte_length", 1, 1 << 62)


def _validate_declaration(declaration: "TierBExternalActionBindingDeclaration") -> None:
    root = _declaration_root(declaration)
    _exact_mapping(
        {"declaration_id": declaration.declaration_id, **root},
        _DECLARATION_KEYS,
        "tier_b_declaration_exact_keys_invalid",
    )
    _fixed_string(
        declaration.schema_version,
        "declaration_schema_version",
        TIER_B_EXTERNAL_ACTION_BINDING_DECLARATION_SCHEMA_VERSION,
    )
    _fixed_string(declaration.declaration_status, "declaration_status", _DECLARATION_STATUS)
    _identifier(declaration.manager_run_plan_id, "manager_run_plan_id")
    _sha(declaration.manager_run_plan_sha256, "manager_run_plan_sha256")
    _strict_int(declaration.manager_run_plan_byte_length, "manager_run_plan_byte_length", 1, 1 << 62)
    _case_id(declaration.case_id)
    if type(declaration.source_class) is not str or declaration.source_class not in _SOURCE_CLASSES:
        raise TierBReadinessError("tier_b_source_class_invalid")
    _commit_sha(declaration.tier_a_code_commit_sha, "tier_a_code_commit_sha")
    if declaration.tier_a_code_commit_sha != _TIER_A_CODE_COMMIT_SHA:
        raise TierBReadinessError("tier_b_tier_a_code_commit_sha_invalid")
    for name in ("d17_decision_set_sha256", "d17_field_policy_sha256"):
        _sha(getattr(declaration, name), name)
    if declaration.d17_decision_set_sha256 != _D17_DECISION_SET_SHA256 or declaration.d17_field_policy_sha256 != _D17_FIELD_POLICY_SHA256:
        raise TierBReadinessError("tier_b_d17_policy_binding_invalid")
    for prefix in (*_ARTIFACT_PREFIXES, *_DERIVED_POLICY_PREFIXES):
        _validate_artifact_triple(declaration, prefix)
    for name in (
        "external_action_gate_eligible", "external_action_authorized", "external_egress_allowed",
        "model_downloaded", "model_loaded", "provider_invoked", "project_data_transferred",
        "real_credentials_used", "paid_resource_created", "run_occurred", "h1_accessed",
        "formal_quality_claimed",
    ):
        if _strict_bool(getattr(declaration, name), name) is not False:
            raise TierBReadinessError("tier_b_declaration_no_action_state_invalid")
    expected_id = TIER_B_EXTERNAL_ACTION_BINDING_DECLARATION_ID_PREFIX + _sha256(_canonical_bytes(root))[:20]
    if type(declaration.declaration_id) is not str or declaration.declaration_id != expected_id:
        raise TierBReadinessError("tier_b_declaration_identity_invalid")


@dataclass(frozen=True)
class TierBExternalActionBindingDeclaration:
    """A non-authoritative hash declaration; it is not a live-readiness record."""

    schema_version: str
    declaration_id: str
    declaration_status: str
    manager_run_plan_id: str
    manager_run_plan_sha256: str
    manager_run_plan_byte_length: int
    case_id: str
    source_class: str
    tier_a_code_commit_sha: str
    d17_decision_set_sha256: str
    d17_field_policy_sha256: str
    d17_manifest_id: str
    d17_manifest_sha256: str
    d17_manifest_byte_length: int
    provider_visible_payload_id: str
    provider_visible_payload_sha256: str
    provider_visible_payload_byte_length: int
    selection_record_id: str
    selection_record_sha256: str
    selection_record_byte_length: int
    input_view_artifact_id: str
    input_view_artifact_sha256: str
    input_view_artifact_byte_length: int
    prompt_artifact_id: str
    prompt_artifact_sha256: str
    prompt_artifact_byte_length: int
    config_artifact_id: str
    config_artifact_sha256: str
    config_artifact_byte_length: int
    local_request_id: str
    local_request_sha256: str
    local_request_byte_length: int
    pre_invocation_audit_id: str
    pre_invocation_audit_sha256: str
    pre_invocation_audit_byte_length: int
    preparation_record_id: str
    preparation_record_sha256: str
    preparation_record_byte_length: int
    tier_a_08_bundle_id: str
    tier_a_08_bundle_sha256: str
    tier_a_08_bundle_byte_length: int
    same_case_frozen_g0_reference_id: str
    same_case_frozen_g0_reference_sha256: str
    same_case_frozen_g0_reference_byte_length: int
    model_artifact_integrity_manifest_id: str
    model_artifact_integrity_manifest_sha256: str
    model_artifact_integrity_manifest_byte_length: int
    runtime_environment_image_record_id: str
    runtime_environment_image_record_sha256: str
    runtime_environment_image_record_byte_length: int
    network_policy_id: str
    network_policy_sha256: str
    network_policy_byte_length: int
    budget_policy_id: str
    budget_policy_sha256: str
    budget_policy_byte_length: int
    credential_log_redaction_policy_id: str
    credential_log_redaction_policy_sha256: str
    credential_log_redaction_policy_byte_length: int
    retention_cleanup_policy_id: str
    retention_cleanup_policy_sha256: str
    retention_cleanup_policy_byte_length: int
    authorization_expiry_policy_id: str
    authorization_expiry_policy_sha256: str
    authorization_expiry_policy_byte_length: int
    external_action_gate_eligible: bool
    external_action_authorized: bool
    external_egress_allowed: bool
    model_downloaded: bool
    model_loaded: bool
    provider_invoked: bool
    project_data_transferred: bool
    real_credentials_used: bool
    paid_resource_created: bool
    run_occurred: bool
    h1_accessed: bool
    formal_quality_claimed: bool

    @classmethod
    def create(
        cls,
        plan: TierBManagerRunPlan,
        *,
        case_id: str,
        source_class: str,
        d17_manifest_id: str,
        d17_manifest_sha256: str,
        d17_manifest_byte_length: int,
        provider_visible_payload_id: str,
        provider_visible_payload_sha256: str,
        provider_visible_payload_byte_length: int,
        selection_record_id: str,
        selection_record_sha256: str,
        selection_record_byte_length: int,
        input_view_artifact_id: str,
        input_view_artifact_sha256: str,
        input_view_artifact_byte_length: int,
        prompt_artifact_id: str,
        prompt_artifact_sha256: str,
        prompt_artifact_byte_length: int,
        config_artifact_id: str,
        config_artifact_sha256: str,
        config_artifact_byte_length: int,
        local_request_id: str,
        local_request_sha256: str,
        local_request_byte_length: int,
        pre_invocation_audit_id: str,
        pre_invocation_audit_sha256: str,
        pre_invocation_audit_byte_length: int,
        preparation_record_id: str,
        preparation_record_sha256: str,
        preparation_record_byte_length: int,
        tier_a_08_bundle_id: str,
        tier_a_08_bundle_sha256: str,
        tier_a_08_bundle_byte_length: int,
        same_case_frozen_g0_reference_id: str,
        same_case_frozen_g0_reference_sha256: str,
        same_case_frozen_g0_reference_byte_length: int,
        model_artifact_integrity_manifest_id: str,
        model_artifact_integrity_manifest_sha256: str,
        model_artifact_integrity_manifest_byte_length: int,
        runtime_environment_image_record_id: str,
        runtime_environment_image_record_sha256: str,
        runtime_environment_image_record_byte_length: int,
    ) -> "TierBExternalActionBindingDeclaration":
        if cls is not _CAPTURED_DECLARATION_TYPE or type(plan) is not _CAPTURED_PLAN_TYPE:
            raise TierBReadinessError("tier_b_declaration_type_invalid")
        plan.validate()
        local_values = locals()
        artifact_values = {
            field: local_values[field]
            for prefix in _ARTIFACT_PREFIXES
            for field in (f"{prefix}_id", f"{prefix}_sha256", f"{prefix}_byte_length")
        }
        root: dict[str, object] = {
            "schema_version": TIER_B_EXTERNAL_ACTION_BINDING_DECLARATION_SCHEMA_VERSION,
            "declaration_status": _DECLARATION_STATUS,
            "manager_run_plan_id": plan.plan_id,
            "manager_run_plan_sha256": plan.sha256(),
            "manager_run_plan_byte_length": len(plan.canonical_bytes()),
            "case_id": case_id, "source_class": source_class,
            "tier_a_code_commit_sha": plan.tier_a_code_commit_sha,
            "d17_decision_set_sha256": plan.d17_decision_set_sha256,
            "d17_field_policy_sha256": plan.d17_field_policy_sha256,
            **artifact_values, **_derived_policy_bindings(plan),
            "external_action_gate_eligible": False, "external_action_authorized": False,
            "external_egress_allowed": False, "model_downloaded": False,
            "model_loaded": False, "provider_invoked": False,
            "project_data_transferred": False, "real_credentials_used": False,
            "paid_resource_created": False, "run_occurred": False,
            "h1_accessed": False, "formal_quality_claimed": False,
        }
        declaration = cls(
            declaration_id=TIER_B_EXTERNAL_ACTION_BINDING_DECLARATION_ID_PREFIX + _sha256(_canonical_bytes(root))[:20],
            **root,
        )
        if type(declaration) is not _CAPTURED_DECLARATION_TYPE:
            raise TierBReadinessError("tier_b_declaration_type_invalid")
        _validate_declaration(declaration)
        return declaration

    @classmethod
    def from_dict(cls, payload: object) -> "TierBExternalActionBindingDeclaration":
        if cls is not _CAPTURED_DECLARATION_TYPE:
            raise TierBReadinessError("tier_b_declaration_type_invalid")
        data = _exact_mapping(payload, _DECLARATION_KEYS, "tier_b_declaration_exact_keys_invalid")
        declaration = cls(**{key: data[key] for key in _DECLARATION_KEYS})
        if type(declaration) is not _CAPTURED_DECLARATION_TYPE:
            raise TierBReadinessError("tier_b_declaration_type_invalid")
        _validate_declaration(declaration)
        return declaration

    @classmethod
    def from_bytes(cls, raw: object) -> "TierBExternalActionBindingDeclaration":
        if cls is not _CAPTURED_DECLARATION_TYPE:
            raise TierBReadinessError("tier_b_declaration_type_invalid")
        return cls.from_dict(_load_canonical_json(raw))

    def validate(self) -> None:
        if type(self) is not _CAPTURED_DECLARATION_TYPE:
            raise TierBReadinessError("tier_b_declaration_type_invalid")
        _validate_declaration(self)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {key: getattr(self, key) for key in _DECLARATION_KEYS}

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256(self.canonical_bytes())


_CAPTURED_PLAN_TYPE = TierBManagerRunPlan
_CAPTURED_DECLARATION_TYPE = TierBExternalActionBindingDeclaration


def create_tier_b_manager_run_plan(*, overrides: Mapping[str, object] | None = None) -> TierBManagerRunPlan:
    """Create a no-action manager-recorded metadata plan."""

    plan = _CAPTURED_PLAN_TYPE.create(overrides=overrides)
    if type(plan) is not _CAPTURED_PLAN_TYPE:
        raise TierBReadinessError("tier_b_plan_type_invalid")
    return plan


def create_tier_b_external_action_binding_declaration(
    plan: TierBManagerRunPlan,
    *,
    case_id: str,
    source_class: str,
    d17_manifest_id: str,
    d17_manifest_sha256: str,
    d17_manifest_byte_length: int,
    provider_visible_payload_id: str,
    provider_visible_payload_sha256: str,
    provider_visible_payload_byte_length: int,
    selection_record_id: str,
    selection_record_sha256: str,
    selection_record_byte_length: int,
    input_view_artifact_id: str,
    input_view_artifact_sha256: str,
    input_view_artifact_byte_length: int,
    prompt_artifact_id: str,
    prompt_artifact_sha256: str,
    prompt_artifact_byte_length: int,
    config_artifact_id: str,
    config_artifact_sha256: str,
    config_artifact_byte_length: int,
    local_request_id: str,
    local_request_sha256: str,
    local_request_byte_length: int,
    pre_invocation_audit_id: str,
    pre_invocation_audit_sha256: str,
    pre_invocation_audit_byte_length: int,
    preparation_record_id: str,
    preparation_record_sha256: str,
    preparation_record_byte_length: int,
    tier_a_08_bundle_id: str,
    tier_a_08_bundle_sha256: str,
    tier_a_08_bundle_byte_length: int,
    same_case_frozen_g0_reference_id: str,
    same_case_frozen_g0_reference_sha256: str,
    same_case_frozen_g0_reference_byte_length: int,
    model_artifact_integrity_manifest_id: str,
    model_artifact_integrity_manifest_sha256: str,
    model_artifact_integrity_manifest_byte_length: int,
    runtime_environment_image_record_id: str,
    runtime_environment_image_record_sha256: str,
    runtime_environment_image_record_byte_length: int,
) -> TierBExternalActionBindingDeclaration:
    """Create only a syntactic hash declaration, never a live-ready record."""

    if type(plan) is not _CAPTURED_PLAN_TYPE:
        raise TierBReadinessError("tier_b_plan_type_invalid")
    declaration = _CAPTURED_DECLARATION_TYPE.create(
        plan,
        case_id=case_id, source_class=source_class,
        d17_manifest_id=d17_manifest_id, d17_manifest_sha256=d17_manifest_sha256,
        d17_manifest_byte_length=d17_manifest_byte_length,
        provider_visible_payload_id=provider_visible_payload_id,
        provider_visible_payload_sha256=provider_visible_payload_sha256,
        provider_visible_payload_byte_length=provider_visible_payload_byte_length,
        selection_record_id=selection_record_id, selection_record_sha256=selection_record_sha256,
        selection_record_byte_length=selection_record_byte_length,
        input_view_artifact_id=input_view_artifact_id,
        input_view_artifact_sha256=input_view_artifact_sha256,
        input_view_artifact_byte_length=input_view_artifact_byte_length,
        prompt_artifact_id=prompt_artifact_id, prompt_artifact_sha256=prompt_artifact_sha256,
        prompt_artifact_byte_length=prompt_artifact_byte_length,
        config_artifact_id=config_artifact_id, config_artifact_sha256=config_artifact_sha256,
        config_artifact_byte_length=config_artifact_byte_length,
        local_request_id=local_request_id, local_request_sha256=local_request_sha256,
        local_request_byte_length=local_request_byte_length,
        pre_invocation_audit_id=pre_invocation_audit_id,
        pre_invocation_audit_sha256=pre_invocation_audit_sha256,
        pre_invocation_audit_byte_length=pre_invocation_audit_byte_length,
        preparation_record_id=preparation_record_id,
        preparation_record_sha256=preparation_record_sha256,
        preparation_record_byte_length=preparation_record_byte_length,
        tier_a_08_bundle_id=tier_a_08_bundle_id, tier_a_08_bundle_sha256=tier_a_08_bundle_sha256,
        tier_a_08_bundle_byte_length=tier_a_08_bundle_byte_length,
        same_case_frozen_g0_reference_id=same_case_frozen_g0_reference_id,
        same_case_frozen_g0_reference_sha256=same_case_frozen_g0_reference_sha256,
        same_case_frozen_g0_reference_byte_length=same_case_frozen_g0_reference_byte_length,
        model_artifact_integrity_manifest_id=model_artifact_integrity_manifest_id,
        model_artifact_integrity_manifest_sha256=model_artifact_integrity_manifest_sha256,
        model_artifact_integrity_manifest_byte_length=model_artifact_integrity_manifest_byte_length,
        runtime_environment_image_record_id=runtime_environment_image_record_id,
        runtime_environment_image_record_sha256=runtime_environment_image_record_sha256,
        runtime_environment_image_record_byte_length=runtime_environment_image_record_byte_length,
    )
    if type(declaration) is not _CAPTURED_DECLARATION_TYPE:
        raise TierBReadinessError("tier_b_declaration_type_invalid")
    return declaration


def validate_tier_b_manager_run_plan_bytes(raw: object) -> TierBManagerRunPlan:
    """Validate canonical plan bytes intrinsically; this performs no live binding."""

    plan = _CAPTURED_PLAN_TYPE.from_bytes(raw)
    if type(plan) is not _CAPTURED_PLAN_TYPE:
        raise TierBReadinessError("tier_b_plan_type_invalid")
    return plan


def validate_tier_b_external_action_binding_declaration_bytes(
    raw: object,
) -> TierBExternalActionBindingDeclaration:
    """Validate declaration syntax only; this is not a live-artifact validator."""

    declaration = _CAPTURED_DECLARATION_TYPE.from_bytes(raw)
    if type(declaration) is not _CAPTURED_DECLARATION_TYPE:
        raise TierBReadinessError("tier_b_declaration_type_invalid")
    return declaration
