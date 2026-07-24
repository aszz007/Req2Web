"""Typed local-only validation of the existing Tier B readiness artifact chain."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable, Mapping

from req2web_agent.schema import AgentContextBundle as _AgentContextBundle
from req2web_evaluation.frozen_g0_reference import FrozenG0PackageReference as _FrozenG0PackageReference
from req2web_generation import RetrievalEnhancedResultPackage as _RetrievalEnhancedResultPackage
from req2web_generation import RetrievalGuidance as _RetrievalGuidance
from req2web_orchestration.model_route import ModelRouteOutcome as _ModelRouteOutcome
from req2web_orchestration.model_route import ScriptedAcceptanceFixture as _ScriptedAcceptanceFixture
from req2web_orchestration.model_route import ScriptedLocalFixture as _ScriptedLocalFixture
from req2web_orchestration.model_route import TierA07bFieldGateReport as _TierA07bFieldGateReport
from req2web_orchestration.model_route import TierA07bGateDeliveryOutcome as _TierA07bGateDeliveryOutcome
from req2web_orchestration.model_route import TierA07bRepairPatch as _TierA07bRepairPatch
from req2web_orchestration.model_route import TierA08RealRunBundlePlaceholder as _TierA08RealRunBundlePlaceholder
from req2web_orchestration.model_route import verify_tier_a_07b_gate_delivery_read_only as _OWNING_GATE_READ_ONLY_VERIFIER
from req2web_faults.fallback_delivery import FrozenG0FallbackRecord as _FrozenG0FallbackRecord
from req2web_provider.d17_audit import D17Path3TierAPreInvocationAuditRecord as _D17Path3TierAPreInvocationAuditRecord
from req2web_provider.d17_input_view import D17Path3InputSelectionRecord as _D17Path3InputSelectionRecord
from req2web_provider.d17_input_view import D17Path3ProviderVisibleInputView as _D17Path3ProviderVisibleInputView
from req2web_provider.d17_input_view import D17Path3SelectedInput as _D17Path3SelectedInput
from req2web_provider.d17_manifest import D17Path3TierAManifest as _D17Path3TierAManifest
from req2web_provider.d17_serializer import D17Path3ConfigArtifact as _D17Path3ConfigArtifact
from req2web_provider.d17_serializer import D17Path3InputViewArtifact as _D17Path3InputViewArtifact
from req2web_provider.d17_serializer import D17Path3LocalRequestArtifact as _D17Path3LocalRequestArtifact
from req2web_provider.d17_serializer import D17Path3PromptArtifact as _D17Path3PromptArtifact
from req2web_provider.local_qwen_provider import LocalQwenProviderPreparationRecord as _LocalQwenProviderPreparationRecord
from req2web_runtime.qwen_profile import QwenProfilePlaceholder as _QwenProfilePlaceholder
from req2web_runtime.tier_b_readiness import TierBExternalActionBindingDeclaration as _TierBExternalActionBindingDeclaration
from req2web_runtime.tier_b_readiness import TierBManagerRunPlan as _TierBManagerRunPlan

TIER_B_LOCAL_READINESS_VALIDATION_RECORD_SCHEMA_VERSION = "req2web.orchestration.tier_b_local_readiness_validation_record.v1"
TIER_B_LOCAL_READINESS_VALIDATION_RECORD_ID_PREFIX = "tier-b-local-readiness-validation-"
_RECORD_STATUS = "local_artifact_chain_validated_no_action"
_SUBSECTION_SCHEMA_VERSION = "req2web.orchestration.tier_b_plan_subsection_binding.v1"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{2,127}$")
_CASE_ID = re.compile(r"^[a-z][a-z0-9_-]{2,95}$")
_ARTIFACT_NAMES = (
    "d17_manifest", "provider_visible_payload", "selection_record", "input_view_artifact",
    "prompt_artifact", "config_artifact", "local_request", "pre_invocation_audit",
    "preparation_record", "tier_a_08_bundle", "same_case_frozen_g0_reference",
    "model_artifact_integrity_manifest", "runtime_environment_image_record",
)
_POLICY_NAMES = (
    "network_policy", "budget_policy", "credential_log_redaction_policy",
    "retention_cleanup_policy", "authorization_expiry_policy",
)
_LIVE_ONLY_ARTIFACT_NAMES = (
    "selected_input", "tier_a_08_profile", "route_outcome",
    "gate_delivery_outcome", "live_verified_g0_package",
)
_BINDING_NAMES = (
    "agent_context", "retrieval_guidance", "manager_run_plan", "binding_declaration",
    *_ARTIFACT_NAMES, *_LIVE_ONLY_ARTIFACT_NAMES, *_POLICY_NAMES,
)
_NO_ACTION_FLAGS = (
    "external_action_gate_eligible", "external_action_authorized", "external_egress_allowed",
    "model_downloaded", "model_loaded", "provider_invoked", "project_data_transferred",
    "real_credentials_used", "paid_resource_created", "run_occurred", "h1_accessed",
    "formal_quality_claimed",
)

class TierBLocalReadinessValidationError(ValueError):
    """Payload-free failure code for local readiness validation."""


def _fail(code: str) -> None:
    raise TierBLocalReadinessValidationError(code)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TierBLocalReadinessValidationError("tier_b_live_readiness_canonical_invalid") from exc


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _package_bytes(value: object) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TierBLocalReadinessValidationError("tier_b_live_readiness_package_bytes_invalid") from exc


def _reject_constant(value: str) -> object:
    raise ValueError(value)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _load(raw: object) -> object:
    if type(raw) is not bytes:
        _fail("tier_b_live_readiness_record_bytes_invalid")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise TierBLocalReadinessValidationError("tier_b_live_readiness_record_bytes_invalid") from exc
    if _canonical(value) != raw:
        _fail("tier_b_live_readiness_record_not_canonical")
    return value


def _mapping(value: object, keys: tuple[str, ...], code: str) -> Mapping[str, object]:
    if type(value) is not dict or set(value) != set(keys):
        _fail(code)
    return value


def _identity(value: object, code: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        _fail(code)
    return value


def _digest(value: object, code: str) -> str:
    if type(value) is not str or _HEX_64.fullmatch(value) is None:
        _fail(code)
    return value


def _length(value: object, code: str) -> int:
    if type(value) is not int or value < 1:
        _fail(code)
    return value


def _exact(value: object, expected: type[object], code: str) -> object:
    if type(value) is not expected:
        _fail(code)
    return value


_NONE_TYPE = type(None)


def _owning(code: str, action: Callable[[], object]) -> None:
    try:
        result = action()
    except Exception as exc:
        raise TierBLocalReadinessValidationError(code) from exc
    if type(result) is not _NONE_TYPE:
        _fail(code)


@dataclass(frozen=True)
class TierBLocalReadinessArtifactChain:
    """Exact existing artifacts; none is a model, provider, or action handle."""
    context: object
    guidance: object
    manifest: object
    provider_visible_input: object
    selection_record: object
    selected_input: object
    input_view_artifact: object
    prompt_artifact: object
    config_artifact: object
    local_request: object
    pre_invocation_audit: object
    preparation_record: object
    tier_a_08_profile: object
    route_outcome: object
    gate_delivery_outcome: object
    tier_a_08_bundle: object
    frozen_g0_reference: object
    frozen_g0_package: object
    scripted_local_fixture: object
    fallback_record: object
    fallback_snapshot_dir: object
    render_output_dir: object
    model_package_output_dir: object
    fallback_output_dir: object
    scripted_acceptance_fixture: object
    field_gate_report: object
    repair_patch: object
    manager_run_plan: object
    declaration: object


_CAPTURED_CHAIN_TYPE = TierBLocalReadinessArtifactChain


@dataclass(frozen=True)
class TierBLocalReadinessBinding:
    name: str
    identity: str
    sha256: str
    byte_length: int

    def validate(self) -> None:
        if type(self) is not _CAPTURED_BINDING_TYPE:
            _fail("tier_b_live_readiness_binding_type_invalid")
        if self.name not in _BINDING_NAMES:
            _fail("tier_b_live_readiness_binding_name_invalid")
        _identity(self.identity, "tier_b_live_readiness_binding_identity_invalid")
        _digest(self.sha256, "tier_b_live_readiness_binding_sha256_invalid")
        _length(self.byte_length, "tier_b_live_readiness_binding_length_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"name": self.name, "identity": self.identity, "sha256": self.sha256, "byte_length": self.byte_length}

    @classmethod
    def from_dict(cls, payload: object) -> "TierBLocalReadinessBinding":
        if cls is not _CAPTURED_BINDING_TYPE:
            _fail("tier_b_live_readiness_binding_type_invalid")
        value = _mapping(payload, ("name", "identity", "sha256", "byte_length"), "tier_b_live_readiness_binding_keys_invalid")
        result = _CAPTURED_BINDING_TYPE(value["name"], value["identity"], value["sha256"], value["byte_length"])
        if type(result) is not _CAPTURED_BINDING_TYPE:
            _fail("tier_b_live_readiness_binding_type_invalid")
        result.validate()
        return result


_CAPTURED_BINDING_TYPE = TierBLocalReadinessBinding


@dataclass(frozen=True)
class TierBLocalReadinessValidationRecord:
    """Canonical record of validation only; it cannot authorize any action."""
    schema_version: str
    record_id: str
    validation_status: str
    case_id: str
    source_class: str
    bindings: tuple[TierBLocalReadinessBinding, ...]
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

    def _root(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "validation_status": self.validation_status,
            "case_id": self.case_id, "source_class": self.source_class,
            "bindings": [item.to_dict() for item in self.bindings],
            **{name: getattr(self, name) for name in _NO_ACTION_FLAGS},
        }

    def validate(self) -> None:
        if type(self) is not _CAPTURED_RECORD_TYPE:
            _fail("tier_b_live_readiness_record_type_invalid")
        if self.schema_version != TIER_B_LOCAL_READINESS_VALIDATION_RECORD_SCHEMA_VERSION:
            _fail("tier_b_live_readiness_record_schema_invalid")
        if self.validation_status != _RECORD_STATUS:
            _fail("tier_b_live_readiness_record_status_invalid")
        if type(self.case_id) is not str or _CASE_ID.fullmatch(self.case_id) is None:
            _fail("tier_b_live_readiness_record_case_invalid")
        if self.source_class not in {"project_authored", "synthetic"}:
            _fail("tier_b_live_readiness_record_source_invalid")
        if type(self.bindings) is not tuple or tuple(item.name for item in self.bindings) != _BINDING_NAMES:
            _fail("tier_b_live_readiness_record_bindings_invalid")
        for item in self.bindings:
            if type(item) is not _CAPTURED_BINDING_TYPE:
                _fail("tier_b_live_readiness_record_binding_type_invalid")
            item.validate()
        for name in _NO_ACTION_FLAGS:
            if type(getattr(self, name)) is not bool or getattr(self, name):
                _fail("tier_b_live_readiness_record_no_action_invalid")
        expected = TIER_B_LOCAL_READINESS_VALIDATION_RECORD_ID_PREFIX + _sha(_canonical(self._root()))[:20]
        if self.record_id != expected:
            _fail("tier_b_live_readiness_record_identity_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"schema_version": self.schema_version, "record_id": self.record_id, **{key: value for key, value in self._root().items() if key != "schema_version"}}

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict())

    def sha256(self) -> str:
        return _sha(self.canonical_bytes())

    @classmethod
    def from_dict(cls, payload: object) -> "TierBLocalReadinessValidationRecord":
        if cls is not _CAPTURED_RECORD_TYPE:
            _fail("tier_b_live_readiness_record_type_invalid")
        keys = ("schema_version", "record_id", "validation_status", "case_id", "source_class", "bindings", *_NO_ACTION_FLAGS)
        value = _mapping(payload, keys, "tier_b_live_readiness_record_keys_invalid")
        if type(value["bindings"]) is not list:
            _fail("tier_b_live_readiness_record_bindings_invalid")
        result = _CAPTURED_RECORD_TYPE(
            value["schema_version"], value["record_id"], value["validation_status"], value["case_id"], value["source_class"],
            tuple(_CAPTURED_BINDING_TYPE.from_dict(item) for item in value["bindings"]),
            **{name: value[name] for name in _NO_ACTION_FLAGS},
        )
        if type(result) is not _CAPTURED_RECORD_TYPE:
            _fail("tier_b_live_readiness_record_type_invalid")
        result.validate()
        return result

    @classmethod
    def from_bytes(cls, raw: object) -> "TierBLocalReadinessValidationRecord":
        if cls is not _CAPTURED_RECORD_TYPE:
            _fail("tier_b_live_readiness_record_type_invalid")
        result = _CAPTURED_RECORD_TYPE.from_dict(_load(raw))
        if type(result) is not _CAPTURED_RECORD_TYPE:
            _fail("tier_b_live_readiness_record_type_invalid")
        return result


_CAPTURED_RECORD_TYPE = TierBLocalReadinessValidationRecord
_CAPTURED_ROUTE_LIVE_VERIFIER = _ModelRouteOutcome.validate_against
_CAPTURED_GATE_READ_ONLY_VERIFIER = _OWNING_GATE_READ_ONLY_VERIFIER


def _binding(name: str, identity: object, raw: object) -> TierBLocalReadinessBinding:
    if type(raw) is not bytes:
        _fail("tier_b_live_readiness_artifact_bytes_invalid")
    result = _CAPTURED_BINDING_TYPE(name, _identity(identity, "tier_b_live_readiness_artifact_identity_invalid"), _sha(raw), len(raw))
    if type(result) is not _CAPTURED_BINDING_TYPE:
        _fail("tier_b_live_readiness_binding_type_invalid")
    return result


def _object_binding(name: str, identity: object, artifact: object) -> TierBLocalReadinessBinding:
    try:
        raw = artifact.canonical_bytes()
    except Exception as exc:
        raise TierBLocalReadinessValidationError("tier_b_live_readiness_artifact_canonical_invalid") from exc
    return _binding(name, identity, raw)


def _plan_binding(name: str, values: Mapping[str, object]) -> TierBLocalReadinessBinding:
    raw = _canonical(dict(values)); digest = _sha(raw)
    result = _CAPTURED_BINDING_TYPE(name, f"tier-b-{name.replace('_', '-')}-{digest[:20]}", digest, len(raw))
    if type(result) is not _CAPTURED_BINDING_TYPE:
        _fail("tier_b_live_readiness_binding_type_invalid")
    return result


def _policy_bindings(plan: _TierBManagerRunPlan) -> tuple[TierBLocalReadinessBinding, ...]:
    values = {
        "network_policy": {"service_mode": plan.service_mode, "endpoint_mode": plan.endpoint_mode, "runtime_architecture": plan.runtime_architecture, "network_phases": list(plan.network_phases), "acquisition_hosts": list(plan.acquisition_hosts), "model_process_offline": plan.model_process_offline, "source_result_transfer_mode": plan.source_result_transfer_mode},
        "budget_policy": {name: getattr(plan, name) for name in ("max_unit_price_cny_fen", "max_billable_minutes", "max_total_spend_cny_fen", "max_fixtures", "max_initial_calls", "provider_retry_per_case", "provider_retry_total", "model_repair_call_budget", "max_total_generation_calls", "max_model_download_bytes", "max_project_ingress_bytes", "max_result_egress_bytes", "max_managed_disk_usage_gib")},
        "credential_log_redaction_policy": {"credential_mode": plan.credential_mode, "ordinary_log_mode": plan.ordinary_log_mode, "debug_logging_enabled": plan.debug_logging_enabled, "redaction_policy_version": plan.redaction_policy_version},
        "retention_cleanup_policy": {"remote_retention_deadline_minutes": plan.remote_retention_deadline_minutes, "local_archive_retention_days": plan.local_archive_retention_days, "remote_cleanup_mode": plan.remote_cleanup_mode},
        "authorization_expiry_policy": {"authorization_term_days": plan.authorization_term_days, "renewal_by_silence_allowed": plan.renewal_by_silence_allowed, "external_action_gate_eligible": plan.external_action_gate_eligible},
    }
    return tuple(_plan_binding(name, values[name]) for name in _POLICY_NAMES)


def _planned_bindings(plan: _TierBManagerRunPlan) -> tuple[TierBLocalReadinessBinding, ...]:
    common = {"binding_schema_version": _SUBSECTION_SCHEMA_VERSION, "binding_status": "not_observed_no_action", "candidate_model": plan.candidate_model, "model_revision": plan.model_revision, "tier_a_code_commit_sha": plan.tier_a_code_commit_sha, "d17_decision_set_sha256": plan.d17_decision_set_sha256, "d17_field_policy_sha256": plan.d17_field_policy_sha256}
    integrity = {**common, "model_license": plan.model_license, "artifact_integrity_manifest_required": plan.artifact_integrity_manifest_required, "observed_model_artifact": False}
    runtime = {**common, "profile_name": plan.profile_name, "service_mode": plan.service_mode, "endpoint_mode": plan.endpoint_mode, "runtime_architecture": plan.runtime_architecture, "runtime_python": plan.runtime_python, "runtime_pytorch": plan.runtime_pytorch, "runtime_transformers": plan.runtime_transformers, "base_image_identity": plan.base_image_identity, "gpu_class": plan.gpu_class, "min_vram_mib": plan.min_vram_mib, "min_host_ram_gib": plan.min_host_ram_gib, "disk_free_gib": plan.disk_free_gib, "observed_runtime_environment": False, "observed_image": False}
    return (_plan_binding("model_artifact_integrity_manifest", integrity), _plan_binding("runtime_environment_image_record", runtime))


def _same(left: object, right: object, code: str) -> None:
    if left != right:
        _fail(code)


def _actual(chain: TierBLocalReadinessArtifactChain) -> tuple[object, ...]:
    _exact(chain, _CAPTURED_CHAIN_TYPE, "tier_b_live_readiness_chain_type_invalid")
    specs = (
        (chain.context, _AgentContextBundle, "context"), (chain.guidance, _RetrievalGuidance, "guidance"),
        (chain.manifest, _D17Path3TierAManifest, "manifest"), (chain.provider_visible_input, _D17Path3ProviderVisibleInputView, "visible_input"),
        (chain.selection_record, _D17Path3InputSelectionRecord, "selection"), (chain.selected_input, _D17Path3SelectedInput, "selected"),
        (chain.input_view_artifact, _D17Path3InputViewArtifact, "input_artifact"), (chain.prompt_artifact, _D17Path3PromptArtifact, "prompt"),
        (chain.config_artifact, _D17Path3ConfigArtifact, "config"), (chain.local_request, _D17Path3LocalRequestArtifact, "request"),
        (chain.pre_invocation_audit, _D17Path3TierAPreInvocationAuditRecord, "audit"), (chain.preparation_record, _LocalQwenProviderPreparationRecord, "preparation"),
        (chain.tier_a_08_profile, _QwenProfilePlaceholder, "profile"), (chain.route_outcome, _ModelRouteOutcome, "route"),
        (chain.gate_delivery_outcome, _TierA07bGateDeliveryOutcome, "gate"), (chain.tier_a_08_bundle, _TierA08RealRunBundlePlaceholder, "bundle"),
        (chain.frozen_g0_reference, _FrozenG0PackageReference, "g0_reference"), (chain.frozen_g0_package, _RetrievalEnhancedResultPackage, "g0_package"),
        (chain.scripted_local_fixture, _ScriptedLocalFixture, "scripted_fixture"), (chain.fallback_record, _FrozenG0FallbackRecord, "fallback_record"),
        (chain.scripted_acceptance_fixture, _ScriptedAcceptanceFixture, "acceptance_fixture"), (chain.field_gate_report, _TierA07bFieldGateReport, "field_gate_report"),
        (chain.repair_patch, _TierA07bRepairPatch, "repair_patch"), (chain.manager_run_plan, _TierBManagerRunPlan, "plan"),
        (chain.declaration, _TierBExternalActionBindingDeclaration, "declaration"),
    )
    for value, expected, label in specs:
        _exact(value, expected, f"tier_b_live_readiness_{label}_type_invalid")
    context, guidance, manifest, view, selection, selected, input_artifact, prompt, config, request, audit, preparation, profile, route, gate, bundle, reference, package, scripted_fixture, fallback_record, acceptance_fixture, field_gate_report, repair_patch, plan, declaration = tuple(value for value, _, _ in specs)
    checks = (
        ("context_validation_failed", context.validate), ("guidance_validation_failed", guidance.validate), ("manifest_validation_failed", manifest.validate),
        ("visible_input_validation_failed", view.validate), ("selection_validation_failed", selection.validate), ("selected_validation_failed", lambda: selected.validate(manifest)),
        ("selected_live_binding_failed", lambda: selected.validate_against(context, manifest)), ("input_artifact_validation_failed", lambda: input_artifact.validate(manifest)),
        ("input_artifact_live_binding_failed", lambda: input_artifact.validate_against(context, manifest)), ("prompt_validation_failed", lambda: prompt.validate(input_artifact=input_artifact)),
        ("config_validation_failed", lambda: config.validate(input_artifact=input_artifact)), ("request_validation_failed", lambda: request.validate(manifest)),
        ("request_live_binding_failed", lambda: request.validate_against(context, manifest)), ("audit_validation_failed", audit.validate),
        ("audit_live_binding_failed", lambda: audit.validate_against(context, manifest, selected, request)), ("preparation_validation_failed", preparation.validate),
        ("preparation_live_binding_failed", lambda: preparation.validate_against(context, manifest, selected, request, audit)), ("profile_validation_failed", profile.validate),
        ("route_validation_failed", route.validate),
        ("route_live_replay_failed", lambda: _CAPTURED_ROUTE_LIVE_VERIFIER(
            route, frozen_g0_reference=reference, package=package, context=context, guidance=guidance,
            manifest=manifest, selected=selected, local_request=request, pre_invocation_audit=audit,
            local_qwen_preparation=preparation, execution_branch="scripted_local_fixture",
            scripted_local_fixture=scripted_fixture,
        )),
        ("gate_validation_failed", gate.validate),
        ("gate_read_only_replay_failed", lambda: _CAPTURED_GATE_READ_ONLY_VERIFIER(
            gate, model_route_outcome=route, frozen_g0_reference=reference, package=package,
            context=context, guidance=guidance, manifest=manifest, selected=selected,
            local_request=request, pre_invocation_audit=audit, local_qwen_preparation=preparation,
            scripted_local_fixture=scripted_fixture, fallback_record=fallback_record,
            fallback_snapshot_dir=chain.fallback_snapshot_dir, render_output_dir=chain.render_output_dir,
            model_package_output_dir=chain.model_package_output_dir, fallback_output_dir=chain.fallback_output_dir,
            scripted_acceptance_fixture=acceptance_fixture, field_gate_report=field_gate_report,
            repair_patch=repair_patch,
        )),
        ("bundle_validation_failed", bundle.validate),
        ("bundle_live_binding_failed", lambda: bundle.validate_against(context, manifest, selected, request, audit, preparation, profile, route, gate)),
        ("g0_reference_validation_failed", reference.validate), ("g0_live_binding_failed", lambda: reference.validate_against(package, context, guidance)),
        ("plan_validation_failed", plan.validate), ("declaration_validation_failed", declaration.validate),
    )
    for code, action in checks:
        _owning(f"tier_b_live_readiness_{code}", action)
    _same(selected.provider_visible_input.canonical_bytes(), view.canonical_bytes(), "tier_b_live_readiness_visible_input_rebinding_invalid")
    _same(selected.selection_record.canonical_bytes(), selection.canonical_bytes(), "tier_b_live_readiness_selection_rebinding_invalid")
    _same(request.input_view_artifact.canonical_bytes(), input_artifact.canonical_bytes(), "tier_b_live_readiness_input_rebinding_invalid")
    _same(request.prompt_artifact.canonical_bytes(), prompt.canonical_bytes(), "tier_b_live_readiness_prompt_rebinding_invalid")
    _same(request.config_artifact.canonical_bytes(), config.canonical_bytes(), "tier_b_live_readiness_config_rebinding_invalid")
    _same(gate.case_id, declaration.case_id, "tier_b_live_readiness_case_binding_invalid")
    _same(selection.original_requirement_source_class, declaration.source_class, "tier_b_live_readiness_source_binding_invalid")
    return context, guidance, manifest, view, selection, selected, input_artifact, prompt, config, request, audit, preparation, profile, route, gate, bundle, reference, package, scripted_fixture, fallback_record, acceptance_fixture, field_gate_report, repair_patch, plan, declaration


def _selected_input_binding(selected: _D17Path3SelectedInput) -> TierBLocalReadinessBinding:
    raw = _canonical({
        "provider_visible_input": selected.provider_visible_input.to_dict(),
        "selection_record": selected.selection_record.to_dict(),
    })
    return _binding("selected_input", "d17-path3-selected-input-" + _sha(raw)[:20], raw)


def _live_verified_g0_package_binding(
    package: _RetrievalEnhancedResultPackage,
    reference: _FrozenG0PackageReference,
) -> TierBLocalReadinessBinding:
    try:
        manifest_raw = (package.package_dir / package.package_manifest).read_bytes()
        manifest = json.loads(
            manifest_raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant
        )
        manifest_canonical = _canonical(manifest)
        inventory = [item.to_dict() for item in reference.inventory]
        tree_sha256 = _sha(_canonical(inventory))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise TierBLocalReadinessValidationError("tier_b_live_readiness_g0_package_evidence_invalid") from exc
    _same(_sha(manifest_raw), reference.package_manifest_sha256, "tier_b_live_readiness_g0_package_manifest_invalid")
    _same(tree_sha256, reference.inventory_tree_sha256, "tier_b_live_readiness_g0_package_tree_invalid")
    _same(type(manifest), dict, "tier_b_live_readiness_g0_package_manifest_invalid")
    _same(manifest.get("package_id"), package.package_id, "tier_b_live_readiness_g0_package_id_invalid")
    _same(manifest.get("page_id"), package.page_id, "tier_b_live_readiness_g0_package_page_invalid")
    evidence = {
        "package_schema_version": reference.package_schema_version,
        "package_id": package.package_id,
        "page_id": package.page_id,
        "package_manifest_sha256": _sha(manifest_raw),
        "package_manifest_canonical_sha256": _sha(manifest_canonical),
        "package_manifest_canonical_byte_length": len(manifest_canonical),
        "inventory_tree_sha256": tree_sha256,
        "inventory_file_count": len(inventory),
        "inventory": inventory,
    }
    return _binding("live_verified_g0_package", package.package_id, _canonical(evidence))


def _bindings(chain: TierBLocalReadinessArtifactChain) -> tuple[tuple[TierBLocalReadinessBinding, ...], _TierBExternalActionBindingDeclaration]:
    context, guidance, manifest, view, selection, selected, input_artifact, prompt, config, request, audit, preparation, profile, route, gate, bundle, reference, package, _scripted_fixture, _fallback_record, _acceptance_fixture, _field_gate_report, _repair_patch, plan, declaration = _actual(chain)
    context_raw = _package_bytes(context.to_dict()); guidance_raw = _package_bytes(guidance.to_dict())
    context_binding = _binding("agent_context", "agent-context-" + _sha(context_raw)[:20], context_raw)
    guidance_binding = _binding("retrieval_guidance", guidance.guidance_bundle_id, guidance_raw)
    _same(reference.context_id, context_binding.identity, "tier_b_live_readiness_g0_context_binding_invalid")
    _same(reference.context_sha256, context_binding.sha256, "tier_b_live_readiness_g0_context_sha256_invalid")
    _same(reference.guidance_bundle_id, guidance_binding.identity, "tier_b_live_readiness_g0_guidance_binding_invalid")
    _same(reference.guidance_sha256, guidance_binding.sha256, "tier_b_live_readiness_g0_guidance_sha256_invalid")
    _same(selection.local_context_sha256, _sha(_canonical(context.to_dict())), "tier_b_live_readiness_d17_context_binding_invalid")
    artifact = (
        _object_binding("d17_manifest", manifest.manifest_id, manifest), _binding("provider_visible_payload", selection.input_view_id, view.canonical_bytes()),
        _object_binding("selection_record", selection.selection_record_id, selection), _object_binding("input_view_artifact", input_artifact.artifact_id, input_artifact),
        _object_binding("prompt_artifact", prompt.artifact_id, prompt), _object_binding("config_artifact", config.artifact_id, config),
        _object_binding("local_request", request.artifact_id, request), _object_binding("pre_invocation_audit", audit.audit_record_id, audit),
        _object_binding("preparation_record", preparation.preparation_record_id, preparation), _object_binding("tier_a_08_bundle", bundle.bundle_id, bundle),
        _binding("same_case_frozen_g0_reference", reference.reference_id, _canonical(reference.to_dict())), *_planned_bindings(plan),
    )
    _same(type(route.frozen_g0_reference), _FrozenG0PackageReference, "tier_b_live_readiness_route_g0_type_invalid")
    _same(route.frozen_g0_reference.to_dict(), reference.to_dict(), "tier_b_live_readiness_route_g0_binding_invalid")
    _same(route.frozen_g0_reference_sha256, artifact[10].sha256, "tier_b_live_readiness_route_g0_sha256_invalid")
    _same(gate.model_route_binding["outcome_id"], route.outcome_id, "tier_b_live_readiness_gate_route_binding_invalid")
    _same(gate.g0_binding["reference_id"], reference.reference_id, "tier_b_live_readiness_gate_g0_binding_invalid")
    _same(gate.g0_binding["package_id"], reference.package_id, "tier_b_live_readiness_gate_package_binding_invalid")
    _same(gate.g0_binding["package_manifest_sha256"], reference.package_manifest_sha256, "tier_b_live_readiness_gate_manifest_binding_invalid")
    live_only = (
        _selected_input_binding(selected),
        _object_binding("tier_a_08_profile", profile.profile_id, profile),
        _object_binding("route_outcome", route.outcome_id, route),
        _object_binding("gate_delivery_outcome", gate.outcome_id, gate),
        _live_verified_g0_package_binding(package, reference),
    )
    result = (
        context_binding, guidance_binding, _object_binding("manager_run_plan", plan.plan_id, plan),
        _object_binding("binding_declaration", declaration.declaration_id, declaration),
        *artifact, *live_only, *_policy_bindings(plan),
    )
    return result, declaration


def _verify_declaration(declaration: _TierBExternalActionBindingDeclaration, bindings: tuple[TierBLocalReadinessBinding, ...]) -> None:
    values = {item.name: item for item in bindings}
    for name in _ARTIFACT_NAMES + _POLICY_NAMES:
        item = values[name]
        _same(getattr(declaration, f"{name}_id"), item.identity, "tier_b_live_readiness_declaration_identity_mismatch")
        _same(getattr(declaration, f"{name}_sha256"), item.sha256, "tier_b_live_readiness_declaration_sha256_mismatch")
        _same(getattr(declaration, f"{name}_byte_length"), item.byte_length, "tier_b_live_readiness_declaration_length_mismatch")
    plan = values["manager_run_plan"]
    _same(declaration.manager_run_plan_id, plan.identity, "tier_b_live_readiness_declaration_plan_identity_mismatch")
    _same(declaration.manager_run_plan_sha256, plan.sha256, "tier_b_live_readiness_declaration_plan_sha256_mismatch")
    _same(declaration.manager_run_plan_byte_length, plan.byte_length, "tier_b_live_readiness_declaration_plan_length_mismatch")
    for name in _NO_ACTION_FLAGS:
        if getattr(declaration, name) is not False:
            _fail("tier_b_live_readiness_declaration_no_action_invalid")


def validate_tier_b_local_artifact_chain(artifacts: TierBLocalReadinessArtifactChain) -> TierBLocalReadinessValidationRecord:
    """Validate actual artifacts without granting external-action authority."""
    bindings, declaration = _bindings(artifacts)
    _verify_declaration(declaration, bindings)
    base = _CAPTURED_RECORD_TYPE(
        TIER_B_LOCAL_READINESS_VALIDATION_RECORD_SCHEMA_VERSION, "", _RECORD_STATUS,
        declaration.case_id, declaration.source_class, bindings, **{name: False for name in _NO_ACTION_FLAGS},
    )
    result = _CAPTURED_RECORD_TYPE(**{**base.__dict__, "record_id": TIER_B_LOCAL_READINESS_VALIDATION_RECORD_ID_PREFIX + _sha(_canonical(base._root()))[:20]})
    result.validate()
    return result


def validate_tier_b_local_readiness_validation_record_bytes(raw: object) -> TierBLocalReadinessValidationRecord:
    """Replay a canonical non-authorizing record; it does not revalidate live artifacts."""
    result = _CAPTURED_RECORD_TYPE.from_bytes(raw)
    if type(result) is not _CAPTURED_RECORD_TYPE:
        _fail("tier_b_live_readiness_record_type_invalid")
    return result


__all__ = (
    "TIER_B_LOCAL_READINESS_VALIDATION_RECORD_SCHEMA_VERSION",
    "TierBLocalReadinessArtifactChain", "TierBLocalReadinessBinding",
    "TierBLocalReadinessValidationError", "TierBLocalReadinessValidationRecord",
    "validate_tier_b_local_artifact_chain", "validate_tier_b_local_readiness_validation_record_bytes",
)
