from __future__ import annotations

"""Synthetic/mock-only pre-route orchestration for the Req2Web M3 boundary.

This module establishes control-flow provenance only. It validates an already
frozen deterministic G0 v2 reference before a scripted mock Provider can be
invoked, then composes existing parser, canonical assembly, candidate-audit,
and model-run-bundle APIs. It never selects delivery, fallback, repair, D17,
a real Provider, model load, or formal quality route.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping

from req2web_agent import AgentContextBundle
from req2web_evaluation.frozen_g0_reference import (
    FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION,
    FrozenG0InventoryEntry,
    FrozenG0PackageReference,
    FrozenG0PackageReferenceError,
)
from req2web_evaluation.model_run_bundle import (
    ModelRunBundleError,
    ModelRunBundleManifest,
    ModelRunBundleWriter,
    validate_model_run_bundle,
)
from req2web_generation import RetrievalEnhancedResultPackage, RetrievalGuidance
from req2web_provider.candidate_audit import (
    CandidateAuditConstructionError,
    ModelCandidateAuditAdapter,
    ModelCandidateAuditRecord,
)
from req2web_provider.mock_provider import (
    MockProviderRequestError,
    MockSmokePageSpecProvider,
    SyntheticMockProviderInvocationRecord,
    SyntheticMockProviderRequest,
)
from req2web_provider.semantic_candidate import (
    AssembledPageSpec,
    CanonicalPageSpecAssembler,
    PageSpecAssemblyReport,
    ProviderError,
    ProviderProtocolError,
    parse_provider_raw_response,
)


PRE_ROUTE_OUTCOME_SCHEMA_VERSION = "req2web.orchestration.synthetic_mock_pre_route_outcome.v1"
_PRE_ROUTE_ID_PREFIX = "synthetic-mock-pre-route-"

_STEP_G0 = "g0_reference_validation"
_STEP_PROVIDER = "mock_provider_invocation"
_STEP_PARSE = "semantic_candidate_parse"
_STEP_ASSEMBLY = "canonical_page_spec_assembly"
_STEP_AUDIT = "candidate_audit"
_STEP_BUNDLE = "model_run_bundle"
_ALL_STEPS = (_STEP_G0, _STEP_PROVIDER, _STEP_PARSE, _STEP_ASSEMBLY, _STEP_AUDIT, _STEP_BUNDLE)

_FIXED_DECLARATIONS = {
    "synthetic_mock_only": True,
    "external_egress_allowed": False,
    "model_loaded": False,
    "remote_called": False,
    "provider_kind": "mock_smoke_only",
    "real_d17_path_status": "not_selected_or_executed",
    "g1_g2_status": "not_assigned",
    "delivery_route": "not_executed",
    "fallback_materialized": False,
    "repair_status": "not_executed",
    "formal_quality_status": "not_executed",
}

_FIXED_FAILURES = {
    "g0_reference_validation_failed": ("package", "g0_reference_validation", False, ()),
    "mock_provider_request_invalid": ("input_context", "mock_provider_request", False, (_STEP_G0,)),
    "mock_provider_invocation_invalid": ("provider_runtime", "mock_provider_invocation", False, (_STEP_G0,)),
    "semantic_candidate_invalid": ("provider_generation", "semantic_candidate", False, (_STEP_G0, _STEP_PROVIDER)),
    "page_spec_assembly_invalid": ("page_spec", "assembly", False, (_STEP_G0, _STEP_PROVIDER, _STEP_PARSE)),
    "candidate_audit_invalid": ("audit", "candidate_audit", False, (_STEP_G0, _STEP_PROVIDER, _STEP_PARSE, _STEP_ASSEMBLY)),
    "model_run_bundle_invalid": ("package", "evaluation_bundle", False, (_STEP_G0, _STEP_PROVIDER, _STEP_PARSE, _STEP_ASSEMBLY, _STEP_AUDIT)),
}
_RUNTIME_FAILURES = {
    "timeout": ("provider_runtime", "transport", True),
    "cancelled": ("provider_runtime", "transport", False),
    "resource_exhausted": ("provider_runtime", "transport", True),
    "service_unavailable": ("provider_runtime", "transport", True),
}
_ARTIFACT_KEYS = (
    "raw_response_sha256",
    "model_semantic_candidate_sha256",
    "assembled_page_id",
    "assembly_report_id",
    "assembly_report_sha256",
    "assembled_page_spec_sha256",
    "candidate_audit_record_id",
    "candidate_audit_record_sha256",
    "model_run_bundle_id",
    "model_run_bundle_manifest_sha256",
)


_ERROR_MESSAGES = {
    "artifact_key_invalid": "outcome artifact key is invalid",
    "assembled_artifact_binding_invalid": "assembled PageSpec does not bind to the recorded outcome",
    "assembled_artifact_invalid": "assembled PageSpec artifact is invalid",
    "assembled_artifact_missing": "assembled PageSpec artifact is missing",
    "assembled_page_reference_binding_invalid": "assembled page identity does not bind to the verified G0 case",
    "assembly_artifact_binding_invalid": "assembly report does not bind to the recorded artifacts",
    "assembly_artifact_invalid": "assembly report artifact is invalid",
    "assembly_artifact_missing": "assembly report artifact is missing",
    "audit_artifact_binding_invalid": "candidate audit does not bind to the recorded artifacts",
    "audit_artifact_invalid": "candidate audit artifact is invalid",
    "audit_artifact_missing": "candidate audit artifact is missing",
    "audit_provider_binding_invalid": "candidate audit does not bind to the current mock invocation",
    "audit_without_assembly": "candidate audit exists without an assembly artifact",
    "bundle_artifact_binding_invalid": "model-run bundle does not bind to the recorded artifacts",
    "bundle_artifact_invalid": "model-run bundle artifact is invalid",
    "bundle_artifact_missing": "model-run bundle artifact is missing",
    "bundle_without_predecessor": "model-run bundle exists without required predecessor artifacts",
    "failure_artifact_boundary_invalid": "failure artifact boundary is invalid",
    "failure_code_invalid": "failure code is invalid",
    "failure_envelope_invalid": "failure envelope is invalid",
    "failure_envelope_missing": "failure envelope is missing",
    "failure_step_order_invalid": "failure step order is invalid",
    "frozen_reference_invalid": "frozen G0 reference is invalid",
    "frozen_reference_missing": "frozen G0 reference is missing",
    "g0_failure_boundary_invalid": "G0 validation failure boundary is invalid",
    "outcome_artifacts_invalid": "outcome artifact identities are invalid",
    "outcome_disposition_invalid": "outcome disposition is invalid",
    "outcome_identity_invalid": "outcome identity is invalid",
    "outcome_schema_invalid": "outcome schema is invalid",
    "outcome_steps_invalid": "outcome step sequence is invalid",
    "provider_record_invalid": "mock Provider invocation record is invalid",
    "provider_record_missing": "mock Provider invocation record is missing",
    "provider_request_failure_boundary_invalid": "mock Provider request failure boundary is invalid",
    "provider_runtime_error_invalid": "mock Provider runtime error is invalid",
    "provider_success_binding_invalid": "mock Provider success does not bind to the outcome",
    "raw_response_binding_invalid": "raw response does not bind to the current invocation",
    "runtime_failure_binding_invalid": "runtime failure does not bind to the current invocation",
    "serialized_artifacts_invalid": "serialized artifact identities are invalid",
    "serialized_declarations_invalid": "serialized execution declarations are invalid",
    "serialized_failure_invalid": "serialized failure envelope is invalid",
    "serialized_outcome_invalid": "serialized outcome is invalid",
    "serialized_reference_hash_invalid": "serialized frozen G0 reference hash is invalid",
    "serialized_steps_invalid": "serialized step sequence is invalid",
    "success_artifact_missing": "success artifact identity is missing",
    "success_dependency_missing": "success dependency is missing",
    "success_provider_binding_invalid": "success does not bind to a mock Provider invocation",
    "success_raw_binding_invalid": "success raw response does not bind to the current invocation",
    "success_step_order_invalid": "success step order is invalid",
    "verified_reference_missing": "verified frozen G0 reference is missing",
}

class PreRouteOutcomeError(ValueError):
    """Fixed-code validation failure for one safe outcome projection."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str):
            raise ValueError("unsupported pre-route outcome error code")
        message = _ERROR_MESSAGES.get(code)
        if message is None:
            raise ValueError("unsupported pre-route outcome error code")
        self.code = code
        self.stage = "orchestration"
        self.phase = "pre_route_outcome"
        self.message = message
        super().__init__(message)


def _error(code: str) -> PreRouteOutcomeError:
    return PreRouteOutcomeError(code)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _optional_sha256(value: object) -> str | None:
    if value is None:
        return None
    if not _is_sha256(value):
        raise _error("outcome_artifacts_invalid")
    return value


def _declarations_dict() -> dict[str, str | bool]:
    return dict(_FIXED_DECLARATIONS)


def _reference_sha256(reference: FrozenG0PackageReference) -> str:
    return _sha256(_canonical_json_bytes(reference.to_dict()))


@dataclass(frozen=True)
class PreRouteFailure:
    """Payload-free fixed stage/phase/code envelope."""

    code: str
    stage: str
    phase: str
    retryable: bool

    @classmethod
    def fixed(cls, code: str) -> "PreRouteFailure":
        try:
            stage, phase, retryable, _ = _FIXED_FAILURES[code]
        except KeyError as exc:
            raise _error("failure_code_invalid") from exc
        return cls(code, stage, phase, retryable)

    @classmethod
    def from_provider_error(cls, provider_error: ProviderError) -> "PreRouteFailure":
        try:
            stage, phase, retryable = _RUNTIME_FAILURES[provider_error.code]
        except (AttributeError, KeyError) as exc:
            raise _error("provider_runtime_error_invalid") from exc
        if (provider_error.stage, provider_error.phase, provider_error.retryable) != (stage, phase, retryable):
            raise _error("provider_runtime_error_invalid")
        return cls(provider_error.code, stage, phase, retryable)

    def validate(self) -> None:
        if self.code in _FIXED_FAILURES:
            stage, phase, retryable, _ = _FIXED_FAILURES[self.code]
            if (self.stage, self.phase, self.retryable) != (stage, phase, retryable):
                raise _error("failure_envelope_invalid")
            return
        if _RUNTIME_FAILURES.get(self.code) != (self.stage, self.phase, self.retryable):
            raise _error("failure_envelope_invalid")

    def to_dict(self) -> dict[str, str | bool]:
        self.validate()
        return {"code": self.code, "stage": self.stage, "phase": self.phase, "retryable": self.retryable}


def _empty_artifacts() -> dict[str, str | None]:
    return {key: None for key in _ARTIFACT_KEYS}


def _expected_steps_for_failure(failure: PreRouteFailure) -> tuple[str, ...]:
    if failure.code in _FIXED_FAILURES:
        return _FIXED_FAILURES[failure.code][3]
    return (_STEP_G0, _STEP_PROVIDER)
def _reference_from_dict(payload: object) -> FrozenG0PackageReference:
    if not isinstance(payload, Mapping):
        raise _error("frozen_reference_invalid")
    expected = {
        "schema_version", "reference_id", "package_schema_version", "package_id", "page_id",
        "context_schema_version", "context_id", "context_sha256", "guidance_schema_version",
        "guidance_bundle_id", "guidance_sha256", "package_manifest_sha256",
        "inventory_tree_sha256", "inventory", "declarations",
    }
    expected_declarations = {
        "baseline_kind": "deterministic_guided_g0",
        "retrieval_influence": "v1_only",
        "result_package": "v2_only",
        "package_copied": False,
        "fallback_materialized": False,
        "delivery_route": "not_executed",
        "provider_invocation_status": "not_executed",
        "unguided_builder_used": False,
    }
    if (
        set(payload) != expected
        or payload.get("schema_version") != FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION
        or payload.get("declarations") != expected_declarations
        or not isinstance(payload.get("inventory"), list)
    ):
        raise _error("frozen_reference_invalid")
    try:
        reference = FrozenG0PackageReference(
            reference_id=payload["reference_id"],
            package_id=payload["package_id"],
            page_id=payload["page_id"],
            context_id=payload["context_id"],
            context_sha256=payload["context_sha256"],
            guidance_bundle_id=payload["guidance_bundle_id"],
            guidance_sha256=payload["guidance_sha256"],
            package_manifest_sha256=payload["package_manifest_sha256"],
            inventory_tree_sha256=payload["inventory_tree_sha256"],
            inventory=tuple(FrozenG0InventoryEntry(**item) for item in payload["inventory"]),
            package_schema_version=payload["package_schema_version"],
            context_schema_version=payload["context_schema_version"],
            guidance_schema_version=payload["guidance_schema_version"],
            schema_version=payload["schema_version"],
        )
        reference.validate()
        return reference
    except (KeyError, TypeError, ValueError, FrozenG0PackageReferenceError) as exc:
        raise _error("frozen_reference_invalid") from exc


def _record_from_dict(payload: object) -> SyntheticMockProviderInvocationRecord:
    if not isinstance(payload, Mapping):
        raise _error("provider_record_invalid")
    values = dict(payload)
    provider_error = values.get("provider_error")
    if provider_error is not None:
        if not isinstance(provider_error, Mapping):
            raise _error("provider_record_invalid")
        try:
            values["provider_error"] = ProviderError(**dict(provider_error))
        except (TypeError, ValueError) as exc:
            raise _error("provider_record_invalid") from exc
    try:
        record = SyntheticMockProviderInvocationRecord(**values)
        record.validate()
        return record
    except (TypeError, ValueError) as exc:
        raise _error("provider_record_invalid") from exc


def _outcome_root(
    *,
    disposition: str,
    completed_steps: tuple[str, ...],
    failure: PreRouteFailure | None,
    frozen_g0_reference: FrozenG0PackageReference | None,
    provider_invocation_record: SyntheticMockProviderInvocationRecord | None,
    artifacts: Mapping[str, str | None],
) -> dict[str, object]:
    return {
        "schema_version": PRE_ROUTE_OUTCOME_SCHEMA_VERSION,
        "disposition": disposition,
        "completed_steps": list(completed_steps),
        "failure": None if failure is None else failure.to_dict(),
        "frozen_g0_reference": None if frozen_g0_reference is None else frozen_g0_reference.to_dict(),
        "frozen_g0_reference_sha256": None if frozen_g0_reference is None else _reference_sha256(frozen_g0_reference),
        "provider_invocation_record": None if provider_invocation_record is None else provider_invocation_record.to_dict(),
        "artifacts": dict(artifacts),
        "execution_declarations": _declarations_dict(),
    }


@dataclass(frozen=True)
class PreRouteOutcome:
    """Path-free record of a synthetic/mock pre-route attempt.

    `to_dict()` deliberately excludes package directories, raw response bytes,
    request/config bytes, requirement text, credentials, assembled PageSpec
    content, and all fallback-package bytes.
    """

    outcome_id: str
    disposition: str
    completed_steps: tuple[str, ...]
    failure: PreRouteFailure | None
    frozen_g0_reference: FrozenG0PackageReference | None = None
    provider_invocation_record: SyntheticMockProviderInvocationRecord | None = None
    assembled_page_spec: AssembledPageSpec | None = None
    assembly_report: PageSpecAssemblyReport | None = None
    candidate_audit_record: ModelCandidateAuditRecord | None = None
    model_run_bundle_manifest: ModelRunBundleManifest | None = None
    raw_response_sha256: str | None = None
    model_semantic_candidate_sha256: str | None = None
    assembled_page_id: str | None = None
    assembly_report_id: str | None = None
    assembly_report_sha256: str | None = None
    assembled_page_spec_sha256: str | None = None
    candidate_audit_record_id: str | None = None
    candidate_audit_record_sha256: str | None = None
    model_run_bundle_id: str | None = None
    model_run_bundle_manifest_sha256: str | None = None
    schema_version: str = PRE_ROUTE_OUTCOME_SCHEMA_VERSION

    @property
    def frozen_g0_reference_sha256(self) -> str | None:
        return None if self.frozen_g0_reference is None else _reference_sha256(self.frozen_g0_reference)

    @property
    def artifacts(self) -> dict[str, str | None]:
        return {key: getattr(self, key) for key in _ARTIFACT_KEYS}

    @classmethod
    def create(
        cls,
        *,
        disposition: str,
        completed_steps: tuple[str, ...],
        failure: PreRouteFailure | None,
        frozen_g0_reference: FrozenG0PackageReference | None = None,
        provider_invocation_record: SyntheticMockProviderInvocationRecord | None = None,
        assembled_page_spec: AssembledPageSpec | None = None,
        assembly_report: PageSpecAssemblyReport | None = None,
        candidate_audit_record: ModelCandidateAuditRecord | None = None,
        model_run_bundle_manifest: ModelRunBundleManifest | None = None,
        **artifacts: str | None,
    ) -> "PreRouteOutcome":
        unknown = set(artifacts) - set(_ARTIFACT_KEYS)
        if unknown:
            raise _error("artifact_key_invalid")
        artifact_values = {**_empty_artifacts(), **artifacts}
        root = _outcome_root(
            disposition=disposition,
            completed_steps=completed_steps,
            failure=failure,
            frozen_g0_reference=frozen_g0_reference,
            provider_invocation_record=provider_invocation_record,
            artifacts=artifact_values,
        )
        result = cls(
            outcome_id=_PRE_ROUTE_ID_PREFIX + _sha256(_canonical_json_bytes(root)),
            disposition=disposition,
            completed_steps=completed_steps,
            failure=failure,
            frozen_g0_reference=frozen_g0_reference,
            provider_invocation_record=provider_invocation_record,
            assembled_page_spec=assembled_page_spec,
            assembly_report=assembly_report,
            candidate_audit_record=candidate_audit_record,
            model_run_bundle_manifest=model_run_bundle_manifest,
            **artifact_values,
        )
        result.validate()
        return result

    def _root(self) -> dict[str, object]:
        return _outcome_root(
            disposition=self.disposition,
            completed_steps=self.completed_steps,
            failure=self.failure,
            frozen_g0_reference=self.frozen_g0_reference,
            provider_invocation_record=self.provider_invocation_record,
            artifacts=self.artifacts,
        )
    def validate(self) -> None:
        if self.schema_version != PRE_ROUTE_OUTCOME_SCHEMA_VERSION:
            raise _error("outcome_schema_invalid")
        if self.disposition not in {"success", "fail_closed"}:
            raise _error("outcome_disposition_invalid")
        if (
            not isinstance(self.completed_steps, tuple)
            or any(step not in _ALL_STEPS for step in self.completed_steps)
            or self.completed_steps != _ALL_STEPS[:len(self.completed_steps)]
        ):
            raise _error("outcome_steps_invalid")
        for key, value in self.artifacts.items():
            if value is not None and (not isinstance(value, str) or not value):
                raise _error("outcome_artifacts_invalid")
        for key in (
            "raw_response_sha256", "model_semantic_candidate_sha256", "assembly_report_sha256",
            "assembled_page_spec_sha256", "candidate_audit_record_sha256", "model_run_bundle_manifest_sha256",
        ):
            _optional_sha256(self.artifacts[key])
        if self.frozen_g0_reference is None:
            if self.disposition != "fail_closed" or not isinstance(self.failure, PreRouteFailure) or self.failure.code != "g0_reference_validation_failed":
                raise _error("frozen_reference_missing")
        else:
            try:
                self.frozen_g0_reference.validate()
            except (TypeError, ValueError, FrozenG0PackageReferenceError) as exc:
                raise _error("frozen_reference_invalid") from exc
        if self.provider_invocation_record is not None:
            try:
                self.provider_invocation_record.validate()
            except (TypeError, ValueError) as exc:
                raise _error("provider_record_invalid") from exc
        self._validate_private_artifact_bindings()
        if self.disposition == "success":
            self._validate_success()
        else:
            self._validate_failure()
        if not isinstance(self.outcome_id, str) or not self.outcome_id.startswith(_PRE_ROUTE_ID_PREFIX):
            raise _error("outcome_identity_invalid")
        if self.outcome_id != _PRE_ROUTE_ID_PREFIX + _sha256(_canonical_json_bytes(self._root())):
            raise _error("outcome_identity_invalid")

    def _validate_private_artifact_bindings(self) -> None:
        assembled = self.assembled_page_spec
        report = self.assembly_report
        audit = self.candidate_audit_record
        manifest = self.model_run_bundle_manifest
        record = self.provider_invocation_record

        if assembled is None:
            if self.assembled_page_id is not None:
                raise _error("assembled_artifact_missing")
            if report is not None:
                raise _error("assembled_artifact_missing")
        else:
            try:
                assembled.validate()
            except (TypeError, ValueError) as exc:
                raise _error("assembled_artifact_invalid") from exc
            if report is None or assembled.report != report:
                raise _error("assembled_artifact_binding_invalid")
            if assembled.page_spec.page_id != self.assembled_page_id:
                raise _error("assembled_artifact_binding_invalid")
            if self.frozen_g0_reference is None or self.assembled_page_id != self.frozen_g0_reference.page_id:
                raise _error("assembled_page_reference_binding_invalid")

        if report is None:
            if any(value is not None for value in (
                self.assembly_report_id, self.assembly_report_sha256, self.assembled_page_spec_sha256
            )):
                raise _error("assembly_artifact_missing")
        else:
            try:
                report.validate()
            except (TypeError, ValueError) as exc:
                raise _error("assembly_artifact_invalid") from exc
            if (
                self.assembly_report_id != report.report_id
                or self.assembly_report_sha256 != report.sha256()
                or self.assembled_page_spec_sha256 != report.assembled_page_spec_sha256
                or self.raw_response_sha256 != report.raw_response_sha256
                or self.model_semantic_candidate_sha256 != report.candidate_sha256
            ):
                raise _error("assembly_artifact_binding_invalid")

        if audit is None:
            if any(value is not None for value in (
                self.candidate_audit_record_id, self.candidate_audit_record_sha256
            )):
                raise _error("audit_artifact_missing")
        else:
            if report is None:
                raise _error("audit_without_assembly")
            if record is None or record.outcome != "success":
                raise _error("audit_provider_binding_invalid")
            try:
                audit.validate()
            except (TypeError, ValueError) as exc:
                raise _error("audit_artifact_invalid") from exc
            if (
                self.candidate_audit_record_id != audit.record_id
                or self.candidate_audit_record_sha256 != _sha256(audit.canonical_json_bytes())
                or audit.raw_response_sha256 != report.raw_response_sha256
                or audit.candidate_sha256 != report.candidate_sha256
                or audit.assembly_report_id != report.report_id
                or audit.assembly_report_sha256 != report.sha256()
                or audit.assembled_page_spec_sha256 != report.assembled_page_spec_sha256
            ):
                raise _error("audit_artifact_binding_invalid")
            if (
                audit.visibility_receipt_id != record.visibility_receipt_id
                or audit.visibility_receipt_sha256 != record.visibility_receipt_sha256
                or audit.synthetic_input_sha256 != record.synthetic_input_sha256
                or audit.mock_serializer_config_sha256 != record.mock_serializer_config_sha256
            ):
                raise _error("audit_provider_binding_invalid")

        if manifest is None:
            if any(value is not None for value in (
                self.model_run_bundle_id, self.model_run_bundle_manifest_sha256
            )):
                raise _error("bundle_artifact_missing")
            return
        if assembled is None or report is None or audit is None or record is None:
            raise _error("bundle_without_predecessor")
        try:
            manifest.validate()
        except (TypeError, ValueError) as exc:
            raise _error("bundle_artifact_invalid") from exc
        roots = manifest.artifact_roots
        if (
            self.model_run_bundle_id != manifest.bundle_id
            or self.model_run_bundle_manifest_sha256 != _sha256(manifest.canonical_json_bytes())
            or manifest.assembly["assembly_report_id"] != report.report_id
            or manifest.candidate_audit["record_id"] != audit.record_id
            or roots["provider_raw_response"]["sha256"] != report.raw_response_sha256
            or roots["model_semantic_candidate"]["sha256"] != report.candidate_sha256
            or roots["page_spec_assembly_report"]["sha256"] != report.sha256()
            or roots["assembled_page_spec"]["sha256"] != report.assembled_page_spec_sha256
            or roots["synthetic_mock_visibility_receipt"]["sha256"] != audit.visibility_receipt_sha256
            or roots["model_candidate_audit_record"]["sha256"] != _sha256(audit.canonical_json_bytes())
            or roots["synthetic_input"]["sha256"] != record.synthetic_input_sha256
            or roots["mock_serializer_config"]["sha256"] != record.mock_serializer_config_sha256
        ):
            raise _error("bundle_artifact_binding_invalid")
    def _validate_failure(self) -> None:
        if not isinstance(self.failure, PreRouteFailure):
            raise _error("failure_envelope_missing")
        self.failure.validate()
        if self.completed_steps != _expected_steps_for_failure(self.failure):
            raise _error("failure_step_order_invalid")
        if self.failure.code == "g0_reference_validation_failed":
            if self.frozen_g0_reference is not None or self.provider_invocation_record is not None or any(value is not None for value in self.artifacts.values()):
                raise _error("g0_failure_boundary_invalid")
            return
        if self.frozen_g0_reference is None:
            raise _error("verified_reference_missing")
        if self.failure.code in {"mock_provider_request_invalid", "mock_provider_invocation_invalid"}:
            if self.provider_invocation_record is not None or any(value is not None for value in self.artifacts.values()):
                raise _error("provider_request_failure_boundary_invalid")
            return
        if self.provider_invocation_record is None:
            raise _error("provider_record_missing")
        record = self.provider_invocation_record
        if self.failure.code in _RUNTIME_FAILURES:
            if record.outcome != "provider_runtime_error" or not isinstance(record.provider_error, ProviderError):
                raise _error("runtime_failure_binding_invalid")
            if self.failure != PreRouteFailure.from_provider_error(record.provider_error) or any(value is not None for value in self.artifacts.values()):
                raise _error("runtime_failure_binding_invalid")
            return
        if record.outcome != "success" or record.raw_response_sha256 is None:
            raise _error("provider_success_binding_invalid")
        if self.raw_response_sha256 != record.raw_response_sha256:
            raise _error("raw_response_binding_invalid")
        expected_presence = {
            "semantic_candidate_invalid": {"raw_response_sha256"},
            "page_spec_assembly_invalid": {"raw_response_sha256", "model_semantic_candidate_sha256"},
            "candidate_audit_invalid": {
                "raw_response_sha256", "model_semantic_candidate_sha256", "assembled_page_id",
                "assembly_report_id", "assembly_report_sha256", "assembled_page_spec_sha256",
            },
            "model_run_bundle_invalid": {
                "raw_response_sha256", "model_semantic_candidate_sha256", "assembled_page_id",
                "assembly_report_id", "assembly_report_sha256", "assembled_page_spec_sha256", "candidate_audit_record_id",
                "candidate_audit_record_sha256",
            },
        }.get(self.failure.code)
        if expected_presence is None:
            raise _error("failure_envelope_invalid")
        if {key for key, value in self.artifacts.items() if value is not None} != expected_presence:
            raise _error("failure_artifact_boundary_invalid")

    def _validate_success(self) -> None:
        if self.failure is not None or self.completed_steps != _ALL_STEPS:
            raise _error("success_step_order_invalid")
        if self.frozen_g0_reference is None or self.provider_invocation_record is None:
            raise _error("success_dependency_missing")
        record = self.provider_invocation_record
        if record.outcome != "success" or record.raw_response_sha256 is None:
            raise _error("success_provider_binding_invalid")
        if any(value is None for value in self.artifacts.values()):
            raise _error("success_artifact_missing")
        if self.raw_response_sha256 != record.raw_response_sha256:
            raise _error("success_raw_binding_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"outcome_id": self.outcome_id, **self._root()}


def validate_serialized_pre_route_outcome(payload: object) -> None:
    """Replay safe serialized IDs without reading disk, raw bytes, or a package."""
    if not isinstance(payload, Mapping):
        raise _error("serialized_outcome_invalid")
    expected = {
        "outcome_id", "schema_version", "disposition", "completed_steps", "failure",
        "frozen_g0_reference", "frozen_g0_reference_sha256", "provider_invocation_record", "artifacts", "execution_declarations",
    }
    if set(payload) != expected or payload.get("schema_version") != PRE_ROUTE_OUTCOME_SCHEMA_VERSION:
        raise _error("serialized_outcome_invalid")
    if payload.get("execution_declarations") != _declarations_dict():
        raise _error("serialized_declarations_invalid")
    steps = payload.get("completed_steps")
    if not isinstance(steps, list) or tuple(steps) != _ALL_STEPS[:len(steps)]:
        raise _error("serialized_steps_invalid")
    failure_value = payload.get("failure")
    if failure_value is None:
        failure = None
    elif isinstance(failure_value, Mapping):
        try:
            failure = PreRouteFailure(**dict(failure_value))
            failure.validate()
        except (TypeError, ValueError, PreRouteOutcomeError) as exc:
            raise _error("serialized_failure_invalid") from exc
    else:
        raise _error("serialized_failure_invalid")
    reference_value = payload.get("frozen_g0_reference")
    reference = None if reference_value is None else _reference_from_dict(reference_value)
    expected_reference_sha256 = None if reference is None else _reference_sha256(reference)
    if payload.get("frozen_g0_reference_sha256") != expected_reference_sha256:
        raise _error("serialized_reference_hash_invalid")
    record_value = payload.get("provider_invocation_record")
    record = None if record_value is None else _record_from_dict(record_value)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(_ARTIFACT_KEYS):
        raise _error("serialized_artifacts_invalid")
    outcome = PreRouteOutcome(
        outcome_id=payload.get("outcome_id"),
        disposition=payload.get("disposition"),
        completed_steps=tuple(steps),
        failure=failure,
        frozen_g0_reference=reference,
        provider_invocation_record=record,
        **dict(artifacts),
    )
    if outcome.disposition not in {"success", "fail_closed"}:
        raise _error("outcome_disposition_invalid")
    for key, value in outcome.artifacts.items():
        if value is not None and (not isinstance(value, str) or not value):
            raise _error("outcome_artifacts_invalid")
    for key in (
        "raw_response_sha256", "model_semantic_candidate_sha256", "assembly_report_sha256",
        "assembled_page_spec_sha256", "candidate_audit_record_sha256", "model_run_bundle_manifest_sha256",
    ):
        _optional_sha256(outcome.artifacts[key])
    if reference is None:
        if outcome.disposition != "fail_closed" or failure is None or failure.code != "g0_reference_validation_failed":
            raise _error("frozen_reference_missing")
    if record is not None:
        record.validate()
    if outcome.assembled_page_id is not None:
        if reference is None or outcome.assembled_page_id != reference.page_id:
            raise _error("assembled_page_reference_binding_invalid")
    if outcome.disposition == "success":
        outcome._validate_success()
    else:
        outcome._validate_failure()
    if outcome.outcome_id != _PRE_ROUTE_ID_PREFIX + _sha256(_canonical_json_bytes(outcome._root())):
        raise _error("outcome_identity_invalid")


@dataclass(frozen=True)
class SyntheticMockPreRouteOrchestrator:
    """One fail-closed synthetic/mock attempt; retry and routing stay unexecuted."""

    assembler: CanonicalPageSpecAssembler = CanonicalPageSpecAssembler()
    candidate_audit_adapter: ModelCandidateAuditAdapter = ModelCandidateAuditAdapter()
    bundle_writer: ModelRunBundleWriter = ModelRunBundleWriter()

    def run(
        self,
        *,
        frozen_g0_reference: FrozenG0PackageReference,
        package: RetrievalEnhancedResultPackage,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        visibility_receipt: object,
        synthetic_input_bytes: bytes,
        mock_serializer_config_bytes: bytes,
        mock_provider: object,
        bundle_output_directory: Path | str,
    ) -> PreRouteOutcome:
        """Execute only the approved pre-route ordering and produce no delivery route."""
        try:
            if not isinstance(frozen_g0_reference, FrozenG0PackageReference):
                raise TypeError("frozen reference required")
            frozen_g0_reference.validate_against(package, context, guidance)
        except (FrozenG0PackageReferenceError, TypeError, ValueError, OSError):
            return PreRouteOutcome.create(
                disposition="fail_closed",
                completed_steps=(),
                failure=PreRouteFailure.fixed("g0_reference_validation_failed"),
            )
        try:
            request = SyntheticMockProviderRequest.from_mock_bytes(
                visibility_receipt,
                synthetic_input_bytes=synthetic_input_bytes,
                mock_serializer_config_bytes=mock_serializer_config_bytes,
            )
            if not isinstance(mock_provider, MockSmokePageSpecProvider):
                raise TypeError("mock Provider required")
        except (MockProviderRequestError, TypeError, ValueError):
            return self._failure(
                frozen_g0_reference,
                PreRouteFailure.fixed("mock_provider_request_invalid"),
                completed_steps=(_STEP_G0,),
            )
        try:
            provider_result, provider_record = mock_provider.invoke(request)
            provider_result.validate()
            provider_record.validate()
            expected_record = SyntheticMockProviderInvocationRecord.from_request_result(request, provider_result)
            if provider_result.request_id != request.request_id or provider_record != expected_record:
                raise ValueError("mock Provider result is not bound to the current request")
        except (AttributeError, TypeError, ValueError):
            return self._failure(
                frozen_g0_reference,
                PreRouteFailure.fixed("mock_provider_invocation_invalid"),
                completed_steps=(_STEP_G0,),
            )
        if provider_result.outcome == "provider_runtime_error":
            return self._failure(
                frozen_g0_reference,
                PreRouteFailure.from_provider_error(provider_result.provider_error),
                completed_steps=(_STEP_G0, _STEP_PROVIDER),
                provider_record=provider_record,
            )
        raw_response = provider_result.raw_response
        if raw_response is None or provider_record.outcome != "success":
            return self._failure(
                frozen_g0_reference,
                PreRouteFailure.fixed("mock_provider_invocation_invalid"),
                completed_steps=(_STEP_G0,),
            )
        raw_hash = raw_response.sha256
        try:
            candidate = parse_provider_raw_response(raw_response)
        except (ProviderProtocolError, TypeError, ValueError):
            return self._failure(
                frozen_g0_reference,
                PreRouteFailure.fixed("semantic_candidate_invalid"),
                completed_steps=(_STEP_G0, _STEP_PROVIDER),
                provider_record=provider_record,
                raw_response_sha256=raw_hash,
            )
        candidate_hash = candidate.sha256()
        try:
            assembled = self.assembler.assemble(raw_response, context, guidance)
            assembled.validate()
            if assembled.candidate.sha256() != candidate_hash:
                raise ValueError("parser assembler identity mismatch")
        except (ProviderProtocolError, TypeError, ValueError):
            return self._failure(
                frozen_g0_reference,
                PreRouteFailure.fixed("page_spec_assembly_invalid"),
                completed_steps=(_STEP_G0, _STEP_PROVIDER, _STEP_PARSE),
                provider_record=provider_record,
                raw_response_sha256=raw_hash,
                model_semantic_candidate_sha256=candidate_hash,
            )
        report = assembled.report
        assembled_page_id = assembled.page_spec.page_id
        try:
            audit = self.candidate_audit_adapter.build(
                assembled,
                visibility_receipt,
                synthetic_input_bytes=synthetic_input_bytes,
                mock_serializer_config_bytes=mock_serializer_config_bytes,
            )
            audit.validate()
        except (CandidateAuditConstructionError, TypeError, ValueError):
            return self._failure(
                frozen_g0_reference,
                PreRouteFailure.fixed("candidate_audit_invalid"),
                completed_steps=(_STEP_G0, _STEP_PROVIDER, _STEP_PARSE, _STEP_ASSEMBLY),
                provider_record=provider_record,
                raw_response_sha256=raw_hash,
                model_semantic_candidate_sha256=candidate_hash,
                assembled_page_id=assembled_page_id,
                assembly_report_id=report.report_id,
                assembly_report_sha256=report.sha256(),
                assembled_page_spec_sha256=report.assembled_page_spec_sha256,
                assembled_page_spec=assembled,
                assembly_report=report,
            )
        try:
            written = self.bundle_writer.write(
                bundle_output_directory,
                context=context,
                guidance=guidance,
                assembled_page_spec=assembled,
                visibility_receipt=visibility_receipt,
                synthetic_input_bytes=synthetic_input_bytes,
                mock_serializer_config_bytes=mock_serializer_config_bytes,
                candidate_audit_record=audit,
            )
            manifest = validate_model_run_bundle(written.bundle_directory)
            manifest.validate()
        except (ModelRunBundleError, OSError, TypeError, ValueError):
            return self._failure(
                frozen_g0_reference,
                PreRouteFailure.fixed("model_run_bundle_invalid"),
                completed_steps=(_STEP_G0, _STEP_PROVIDER, _STEP_PARSE, _STEP_ASSEMBLY, _STEP_AUDIT),
                provider_record=provider_record,
                raw_response_sha256=raw_hash,
                model_semantic_candidate_sha256=candidate_hash,
                assembled_page_id=assembled_page_id,
                assembly_report_id=report.report_id,
                assembly_report_sha256=report.sha256(),
                assembled_page_spec_sha256=report.assembled_page_spec_sha256,
                candidate_audit_record_id=audit.record_id,
                candidate_audit_record_sha256=_sha256(audit.canonical_json_bytes()),
                assembled_page_spec=assembled,
                assembly_report=report,
                candidate_audit_record=audit,
            )
        return PreRouteOutcome.create(
            disposition="success",
            completed_steps=_ALL_STEPS,
            failure=None,
            frozen_g0_reference=frozen_g0_reference,
            provider_invocation_record=provider_record,
            raw_response_sha256=raw_hash,
            model_semantic_candidate_sha256=candidate_hash,
            assembled_page_id=assembled_page_id,
            assembly_report_id=report.report_id,
            assembly_report_sha256=report.sha256(),
            assembled_page_spec_sha256=report.assembled_page_spec_sha256,
            candidate_audit_record_id=audit.record_id,
            candidate_audit_record_sha256=_sha256(audit.canonical_json_bytes()),
            model_run_bundle_id=manifest.bundle_id,
            model_run_bundle_manifest_sha256=_sha256(manifest.canonical_json_bytes()),
            assembled_page_spec=assembled,
            assembly_report=report,
            candidate_audit_record=audit,
            model_run_bundle_manifest=manifest,
        )

    @staticmethod
    def _failure(
        frozen_g0_reference: FrozenG0PackageReference,
        failure: PreRouteFailure,
        *,
        completed_steps: tuple[str, ...],
        provider_record: SyntheticMockProviderInvocationRecord | None = None,
        assembled_page_spec: AssembledPageSpec | None = None,
        assembly_report: PageSpecAssemblyReport | None = None,
        candidate_audit_record: ModelCandidateAuditRecord | None = None,
        model_run_bundle_manifest: ModelRunBundleManifest | None = None,
        **artifacts: str | None,
    ) -> PreRouteOutcome:
        return PreRouteOutcome.create(
            disposition="fail_closed",
            completed_steps=completed_steps,
            failure=failure,
            frozen_g0_reference=frozen_g0_reference,
            provider_invocation_record=provider_record,
            assembled_page_spec=assembled_page_spec,
            assembly_report=assembly_report,
            candidate_audit_record=candidate_audit_record,
            model_run_bundle_manifest=model_run_bundle_manifest,
            **artifacts,
        )