from __future__ import annotations

"""Evaluator-free synthetic/mock candidate-attribution audit for Req2Web M3.

This module validates only source identity, mock visibility, and fixture-permission
shape. It never decides semantic correctness, evidence adoption, Retrieval
Influence, model quality, or any D17 payload/property.
"""

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable

from req2web_rag.corpus import ROLE_ORDER

from .semantic_candidate import (
    AssembledPageSpec,
    ClaimedAttributionEdge,
    ModelSemanticCandidate,
    parse_provider_raw_response,
)


SYNTHETIC_MOCK_VISIBILITY_RECEIPT_SCHEMA_VERSION = (
    "req2web.provider.synthetic_mock_visibility_receipt.v1"
)
MODEL_CANDIDATE_AUDIT_RECORD_SCHEMA_VERSION = (
    "req2web.provider.model_candidate_audit_record.v1"
)

_RECEIPT_DECLARATION = "synthetic_mock_only_not_d17_manifest_or_serializer_output"
_SOURCE_KINDS = frozenset({"requirement", "evidence", "policy"})
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STABLE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,95}$")


class CandidateAuditConstructionError(ValueError):
    """Safe fail-closed audit-construction error aligned to D03 guidance/adoption."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = "guidance/adoption"
        self.phase = "candidate_audit"


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(payload: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(payload))


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty normalized string")
    return value


def _require_source_id(value: object, label: str) -> str:
    # M3-01 permits any normalized non-empty source ID. Keep that compatibility
    # so unseen Provider declarations are retained as rejected audit edges.
    return _require_text(value, label)


def _require_version(value: object, label: str) -> str:
    text = _require_text(value, label)
    if not _VERSION_RE.fullmatch(text):
        raise ValueError(f"{label} is invalid")
    return text


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a SHA-256 value")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _require_tuple(value: object, label: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{label} must be an immutable tuple")
    return value


def _require_nonempty_bytes(value: object, code: str, message: str) -> bytes:
    if not isinstance(value, bytes) or not value:
        raise CandidateAuditConstructionError(code, message)
    return value


@dataclass(frozen=True)
class MockRequirementVisibilitySource:
    """Synthetic registry entry for a visible requirement identity."""

    source_id: str
    requirement_id: str
    visible: bool
    eligible: bool

    def validate(self) -> None:
        _require_source_id(self.source_id, "requirement source ID")
        _require_source_id(self.requirement_id, "requirement ID")
        _require_bool(self.visible, "requirement visibility")
        _require_bool(self.eligible, "requirement eligibility")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "source_id": self.source_id,
            "requirement_id": self.requirement_id,
            "visible": self.visible,
            "eligible": self.eligible,
        }


@dataclass(frozen=True)
class MockEvidenceVisibilitySource:
    """Synthetic registry entry for one evidence role/document identity."""

    source_id: str
    role: str
    doc_id: str
    visible: bool
    fixture_permission: bool
    eligible: bool

    def validate(self) -> None:
        _require_source_id(self.source_id, "evidence source ID")
        # A normalized but unsupported role is representable so the adapter can
        # retain an explicit rejected edge instead of silently dropping it.
        _require_source_id(self.role, "evidence role")
        _require_source_id(self.doc_id, "evidence document ID")
        _require_bool(self.visible, "evidence visibility")
        _require_bool(self.fixture_permission, "evidence fixture permission")
        _require_bool(self.eligible, "evidence eligibility")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "source_id": self.source_id,
            "role": self.role,
            "doc_id": self.doc_id,
            "visible": self.visible,
            "fixture_permission": self.fixture_permission,
            "eligible": self.eligible,
        }


@dataclass(frozen=True)
class MockPolicyVisibilitySource:
    """Synthetic registry entry with auditable policy-version/hash binding."""

    source_id: str
    policy_version: str
    fixture_policy_version: str
    policy_sha256: str
    fixture_policy_sha256: str
    visible: bool
    eligible: bool

    def validate(self) -> None:
        _require_source_id(self.source_id, "policy source ID")
        _require_version(self.policy_version, "policy version")
        _require_version(self.fixture_policy_version, "fixture policy version")
        _require_sha256(self.policy_sha256, "policy SHA-256")
        _require_sha256(self.fixture_policy_sha256, "fixture policy SHA-256")
        _require_bool(self.visible, "policy visibility")
        _require_bool(self.eligible, "policy eligibility")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "source_id": self.source_id,
            "policy_version": self.policy_version,
            "fixture_policy_version": self.fixture_policy_version,
            "policy_sha256": self.policy_sha256,
            "fixture_policy_sha256": self.fixture_policy_sha256,
            "visible": self.visible,
            "eligible": self.eligible,
        }


@dataclass(frozen=True)
class SyntheticMockVisibilitySourceRegistry:
    """Local source registry; it is neither a D17 manifest nor a serializer view."""

    requirements: tuple[MockRequirementVisibilitySource, ...]
    evidences: tuple[MockEvidenceVisibilitySource, ...]
    policies: tuple[MockPolicyVisibilitySource, ...]

    def validate(self) -> None:
        _require_tuple(self.requirements, "requirement registry")
        _require_tuple(self.evidences, "evidence registry")
        _require_tuple(self.policies, "policy registry")
        collections: tuple[tuple[str, tuple[Any, ...]], ...] = (
            ("requirement", self.requirements),
            ("evidence", self.evidences),
            ("policy", self.policies),
        )
        expected_types = {
            "requirement": MockRequirementVisibilitySource,
            "evidence": MockEvidenceVisibilitySource,
            "policy": MockPolicyVisibilitySource,
        }
        seen: set[tuple[str, str]] = set()
        for source_kind, entries in collections:
            for entry in entries:
                if not isinstance(entry, expected_types[source_kind]):
                    raise ValueError("synthetic visibility registry contains an invalid source type")
                entry.validate()
                identity = (source_kind, entry.source_id)
                if identity in seen:
                    raise ValueError("synthetic visibility registry contains duplicate source identities")
                seen.add(identity)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "requirements": [entry.to_dict() for entry in self.requirements],
            "evidences": [entry.to_dict() for entry in self.evidences],
            "policies": [entry.to_dict() for entry in self.policies],
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256_bytes(self.canonical_json_bytes())

    def lookup(self, source_kind: str, source_id: str) -> (
        MockRequirementVisibilitySource | MockEvidenceVisibilitySource | MockPolicyVisibilitySource | None
    ):
        self.validate()
        entries: Iterable[Any]
        if source_kind == "requirement":
            entries = self.requirements
        elif source_kind == "evidence":
            entries = self.evidences
        elif source_kind == "policy":
            entries = self.policies
        else:
            raise ValueError("unsupported synthetic visibility source kind")
        return next((entry for entry in entries if entry.source_id == source_id), None)


@dataclass(frozen=True)
class SyntheticMockVisibilityReceipt:
    """Immutable receipt for synthetic/mock-only source visibility checks."""

    receipt_id: str
    synthetic_input_sha256: str
    synthetic_input_byte_length: int
    mock_serializer_config_sha256: str
    source_registry: SyntheticMockVisibilitySourceRegistry
    source_registry_sha256: str
    mock_only: bool = True
    external_egress_allowed: bool = False
    is_d17_manifest: bool = False
    is_d17_serializer_output: bool = False
    declaration: str = _RECEIPT_DECLARATION
    schema_version: str = SYNTHETIC_MOCK_VISIBILITY_RECEIPT_SCHEMA_VERSION

    @classmethod
    def from_mock_bytes(
        cls,
        synthetic_input_bytes: bytes,
        mock_serializer_config_bytes: bytes,
        source_registry: SyntheticMockVisibilitySourceRegistry,
    ) -> "SyntheticMockVisibilityReceipt":
        if not isinstance(synthetic_input_bytes, bytes) or not synthetic_input_bytes:
            raise ValueError("synthetic input bytes must be non-empty")
        if not isinstance(mock_serializer_config_bytes, bytes) or not mock_serializer_config_bytes:
            raise ValueError("mock serializer/config bytes must be non-empty")
        if not isinstance(source_registry, SyntheticMockVisibilitySourceRegistry):
            raise TypeError("source registry must be SyntheticMockVisibilitySourceRegistry")
        source_registry.validate()
        synthetic_input_sha256 = _sha256_bytes(synthetic_input_bytes)
        config_sha256 = _sha256_bytes(mock_serializer_config_bytes)
        registry_sha256 = source_registry.sha256()
        root = cls._root_dict_for(
            synthetic_input_sha256=synthetic_input_sha256,
            synthetic_input_byte_length=len(synthetic_input_bytes),
            mock_serializer_config_sha256=config_sha256,
            source_registry=source_registry,
            source_registry_sha256=registry_sha256,
            mock_only=True,
            external_egress_allowed=False,
            is_d17_manifest=False,
            is_d17_serializer_output=False,
            declaration=_RECEIPT_DECLARATION,
            schema_version=SYNTHETIC_MOCK_VISIBILITY_RECEIPT_SCHEMA_VERSION,
        )
        return cls(
            receipt_id=_receipt_id(root),
            synthetic_input_sha256=synthetic_input_sha256,
            synthetic_input_byte_length=len(synthetic_input_bytes),
            mock_serializer_config_sha256=config_sha256,
            source_registry=source_registry,
            source_registry_sha256=registry_sha256,
        )

    @staticmethod
    def _root_dict_for(
        *,
        synthetic_input_sha256: str,
        synthetic_input_byte_length: int,
        mock_serializer_config_sha256: str,
        source_registry: SyntheticMockVisibilitySourceRegistry,
        source_registry_sha256: str,
        mock_only: bool,
        external_egress_allowed: bool,
        is_d17_manifest: bool,
        is_d17_serializer_output: bool,
        declaration: str,
        schema_version: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": schema_version,
            "synthetic_input_sha256": synthetic_input_sha256,
            "synthetic_input_byte_length": synthetic_input_byte_length,
            "mock_serializer_config_sha256": mock_serializer_config_sha256,
            "source_registry": source_registry.to_dict(),
            "source_registry_sha256": source_registry_sha256,
            "mock_only": mock_only,
            "external_egress_allowed": external_egress_allowed,
            "is_d17_manifest": is_d17_manifest,
            "is_d17_serializer_output": is_d17_serializer_output,
            "declaration": declaration,
        }

    def _root_dict(self) -> dict[str, Any]:
        return self._root_dict_for(
            synthetic_input_sha256=self.synthetic_input_sha256,
            synthetic_input_byte_length=self.synthetic_input_byte_length,
            mock_serializer_config_sha256=self.mock_serializer_config_sha256,
            source_registry=self.source_registry,
            source_registry_sha256=self.source_registry_sha256,
            mock_only=self.mock_only,
            external_egress_allowed=self.external_egress_allowed,
            is_d17_manifest=self.is_d17_manifest,
            is_d17_serializer_output=self.is_d17_serializer_output,
            declaration=self.declaration,
            schema_version=self.schema_version,
        )

    def validate(self) -> None:
        if self.schema_version != SYNTHETIC_MOCK_VISIBILITY_RECEIPT_SCHEMA_VERSION:
            raise ValueError("unsupported synthetic/mock visibility receipt schema")
        _require_sha256(self.synthetic_input_sha256, "synthetic input hash")
        if not isinstance(self.synthetic_input_byte_length, int) or isinstance(self.synthetic_input_byte_length, bool) or self.synthetic_input_byte_length <= 0:
            raise ValueError("synthetic input byte length is invalid")
        _require_sha256(self.mock_serializer_config_sha256, "mock serializer/config hash")
        if not isinstance(self.source_registry, SyntheticMockVisibilitySourceRegistry):
            raise ValueError("synthetic/mock visibility receipt source registry is invalid")
        self.source_registry.validate()
        if self.source_registry_sha256 != self.source_registry.sha256():
            raise ValueError("synthetic/mock visibility receipt source registry hash is invalid")
        if self.mock_only is not True or self.external_egress_allowed is not False:
            raise ValueError("synthetic/mock visibility receipt cannot permit external egress")
        if self.is_d17_manifest is not False or self.is_d17_serializer_output is not False:
            raise ValueError("synthetic/mock visibility receipt must not claim D17 manifest or serializer output")
        if self.declaration != _RECEIPT_DECLARATION:
            raise ValueError("synthetic/mock visibility receipt declaration is invalid")
        if self.receipt_id != _receipt_id(self._root_dict()):
            raise ValueError("synthetic/mock visibility receipt ID does not bind its provenance root")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"receipt_id": self.receipt_id, **self._root_dict()}

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256_bytes(self.canonical_json_bytes())


@dataclass(frozen=True)
class ResolvedVisibilitySource:
    """All facts required to replay the fixed identity/visibility decision order."""

    source_kind: str
    source_id: str
    registry_found: bool
    requirement_id: str | None = None
    requirement_visible: bool | None = None
    requirement_eligible: bool | None = None
    evidence_role: str | None = None
    evidence_doc_id: str | None = None
    evidence_doc_bound_to_page_spec: bool | None = None
    evidence_role_matches_trace: bool | None = None
    evidence_visible: bool | None = None
    evidence_fixture_permission: bool | None = None
    evidence_eligible: bool | None = None
    policy_version: str | None = None
    fixture_policy_version: str | None = None
    policy_sha256: str | None = None
    fixture_policy_sha256: str | None = None
    policy_visible: bool | None = None
    policy_eligible: bool | None = None

    def validate(self) -> None:
        if self.source_kind not in _SOURCE_KINDS:
            raise ValueError("resolved source kind is invalid")
        _require_source_id(self.source_id, "resolved source ID")
        _require_bool(self.registry_found, "resolved source registry-found flag")
        detail_values = (
            self.requirement_id,
            self.requirement_visible,
            self.requirement_eligible,
            self.evidence_role,
            self.evidence_doc_id,
            self.evidence_doc_bound_to_page_spec,
            self.evidence_role_matches_trace,
            self.evidence_visible,
            self.evidence_fixture_permission,
            self.evidence_eligible,
            self.policy_version,
            self.fixture_policy_version,
            self.policy_sha256,
            self.fixture_policy_sha256,
            self.policy_visible,
            self.policy_eligible,
        )
        if not self.registry_found:
            if any(value is not None for value in detail_values):
                raise ValueError("unseen source resolution must not contain registry details")
            return
        if self.source_kind == "requirement":
            _require_source_id(self.requirement_id, "resolved requirement ID")
            _require_bool(self.requirement_visible, "resolved requirement visibility")
            _require_bool(self.requirement_eligible, "resolved requirement eligibility")
            if any(value is not None for value in detail_values[3:]):
                raise ValueError("resolved requirement source has incompatible fields")
        elif self.source_kind == "evidence":
            _require_source_id(self.evidence_role, "resolved evidence role")
            _require_source_id(self.evidence_doc_id, "resolved evidence document ID")
            _require_bool(self.evidence_doc_bound_to_page_spec, "resolved evidence document binding")
            _require_bool(self.evidence_role_matches_trace, "resolved evidence role trace match")
            _require_bool(self.evidence_visible, "resolved evidence visibility")
            _require_bool(self.evidence_fixture_permission, "resolved evidence fixture permission")
            _require_bool(self.evidence_eligible, "resolved evidence eligibility")
            if self.evidence_doc_bound_to_page_spec is False and self.evidence_role_matches_trace is True:
                raise ValueError("unbound evidence document cannot claim a trace-role match")
            if any(value is not None for value in (
                self.requirement_id,
                self.requirement_visible,
                self.requirement_eligible,
                self.policy_version,
                self.fixture_policy_version,
                self.policy_sha256,
                self.fixture_policy_sha256,
                self.policy_visible,
                self.policy_eligible,
            )):
                raise ValueError("resolved evidence source has incompatible fields")
        else:
            _require_version(self.policy_version, "resolved policy version")
            _require_version(self.fixture_policy_version, "resolved fixture policy version")
            _require_sha256(self.policy_sha256, "resolved policy SHA-256")
            _require_sha256(self.fixture_policy_sha256, "resolved fixture policy SHA-256")
            _require_bool(self.policy_visible, "resolved policy visibility")
            _require_bool(self.policy_eligible, "resolved policy eligibility")
            if any(value is not None for value in detail_values[:10]):
                raise ValueError("resolved policy source has incompatible fields")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "registry_found": self.registry_found,
            "requirement_id": self.requirement_id,
            "requirement_visible": self.requirement_visible,
            "requirement_eligible": self.requirement_eligible,
            "evidence_role": self.evidence_role,
            "evidence_doc_id": self.evidence_doc_id,
            "evidence_doc_bound_to_page_spec": self.evidence_doc_bound_to_page_spec,
            "evidence_role_matches_trace": self.evidence_role_matches_trace,
            "evidence_visible": self.evidence_visible,
            "evidence_fixture_permission": self.evidence_fixture_permission,
            "evidence_eligible": self.evidence_eligible,
            "policy_version": self.policy_version,
            "fixture_policy_version": self.fixture_policy_version,
            "policy_sha256": self.policy_sha256,
            "fixture_policy_sha256": self.fixture_policy_sha256,
            "policy_visible": self.policy_visible,
            "policy_eligible": self.policy_eligible,
        }


def expected_audit_decision_from_resolution(resolution: ResolvedVisibilitySource) -> tuple[str, str]:
    """Pure fixed-order identity/visibility decision replay for one resolution."""

    if not isinstance(resolution, ResolvedVisibilitySource):
        raise TypeError("resolution must be ResolvedVisibilitySource")
    resolution.validate()
    if not resolution.registry_found:
        return "rejected", "unseen_source"
    if resolution.source_kind == "requirement":
        if not resolution.requirement_visible:
            return "rejected", "requirement_not_visible"
        if not resolution.requirement_eligible:
            return "rejected", "requirement_not_eligible"
        return "accepted", "identity_visibility_license_shape_valid"
    if resolution.source_kind == "evidence":
        if resolution.evidence_role not in ROLE_ORDER:
            return "rejected", "evidence_role_invalid"
        if not resolution.evidence_doc_bound_to_page_spec:
            return "rejected", "evidence_doc_not_bound_to_assembled_page_spec"
        if not resolution.evidence_role_matches_trace:
            return "rejected", "evidence_role_trace_mismatch"
        if not resolution.evidence_visible:
            return "rejected", "evidence_not_visible"
        if not resolution.evidence_fixture_permission:
            return "rejected", "evidence_fixture_permission_denied"
        if not resolution.evidence_eligible:
            return "rejected", "evidence_not_eligible"
        return "accepted", "identity_visibility_license_shape_valid"
    if resolution.policy_version != resolution.fixture_policy_version:
        return "rejected", "policy_version_mismatch"
    if resolution.policy_sha256 != resolution.fixture_policy_sha256:
        return "rejected", "policy_sha256_mismatch"
    if not resolution.policy_visible:
        return "rejected", "policy_not_visible"
    if not resolution.policy_eligible:
        return "rejected", "policy_not_eligible"
    return "accepted", "identity_visibility_license_shape_valid"


@dataclass(frozen=True)
class CandidateAuditDecision:
    """One retained decision about an untrusted candidate-declared edge."""

    candidate_entity_stable_id: str
    source_kind: str
    source_id: str
    status: str
    reason_code: str
    resolved_source: ResolvedVisibilitySource
    semantic_correctness_not_evaluated: bool = True

    def validate(self) -> None:
        if not isinstance(self.candidate_entity_stable_id, str) or not _STABLE_ID_RE.fullmatch(self.candidate_entity_stable_id):
            raise ValueError("candidate audit decision entity stable ID is invalid")
        if self.source_kind not in _SOURCE_KINDS:
            raise ValueError("candidate audit decision source kind is invalid")
        _require_source_id(self.source_id, "candidate audit decision source ID")
        if self.status not in {"accepted", "rejected"}:
            raise ValueError("candidate audit decision status is invalid")
        if not isinstance(self.resolved_source, ResolvedVisibilitySource):
            raise ValueError("candidate audit decision source resolution is invalid")
        self.resolved_source.validate()
        if (self.resolved_source.source_kind, self.resolved_source.source_id) != (self.source_kind, self.source_id):
            raise ValueError("candidate audit decision source resolution does not match the claim")
        expected_status, expected_reason_code = expected_audit_decision_from_resolution(self.resolved_source)
        if (self.status, self.reason_code) != (expected_status, expected_reason_code):
            raise ValueError("candidate audit decision does not match resolved source facts")
        if self.semantic_correctness_not_evaluated is not True:
            raise ValueError("candidate audit must explicitly avoid semantic correctness evaluation")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "candidate_entity_stable_id": self.candidate_entity_stable_id,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "resolved_source": self.resolved_source.to_dict(),
            "semantic_correctness_not_evaluated": self.semantic_correctness_not_evaluated,
        }


@dataclass(frozen=True)
class VerifiedCandidateAttributionEdge:
    """Read-only projection of an accepted identity/visibility-only edge."""

    candidate_entity_stable_id: str
    source_kind: str
    source_id: str
    resolved_source: ResolvedVisibilitySource
    semantic_correctness_not_evaluated: bool = True

    def validate(self) -> None:
        CandidateAuditDecision(
            candidate_entity_stable_id=self.candidate_entity_stable_id,
            source_kind=self.source_kind,
            source_id=self.source_id,
            status="accepted",
            reason_code="identity_visibility_license_shape_valid",
            resolved_source=self.resolved_source,
            semantic_correctness_not_evaluated=self.semantic_correctness_not_evaluated,
        ).validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "candidate_entity_stable_id": self.candidate_entity_stable_id,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "resolved_source": self.resolved_source.to_dict(),
            "semantic_correctness_not_evaluated": self.semantic_correctness_not_evaluated,
        }


@dataclass(frozen=True)
class ModelCandidateAuditRecord:
    """Deterministic provenance-rooted audit result for one assembled PageSpec."""

    record_id: str
    raw_response_sha256: str
    candidate_sha256: str
    assembly_report_id: str
    assembly_report_sha256: str
    assembled_page_spec_sha256: str
    visibility_receipt_id: str
    visibility_receipt_sha256: str
    synthetic_input_sha256: str
    mock_serializer_config_sha256: str
    status: str
    accepted_count: int
    rejected_count: int
    edge_decisions: tuple[CandidateAuditDecision, ...]
    semantic_correctness_not_evaluated: bool = True
    schema_version: str = MODEL_CANDIDATE_AUDIT_RECORD_SCHEMA_VERSION

    def _root_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "raw_response_sha256": self.raw_response_sha256,
            "candidate_sha256": self.candidate_sha256,
            "assembly_report_id": self.assembly_report_id,
            "assembly_report_sha256": self.assembly_report_sha256,
            "assembled_page_spec_sha256": self.assembled_page_spec_sha256,
            "visibility_receipt_id": self.visibility_receipt_id,
            "visibility_receipt_sha256": self.visibility_receipt_sha256,
            "synthetic_input_sha256": self.synthetic_input_sha256,
            "mock_serializer_config_sha256": self.mock_serializer_config_sha256,
            "status": self.status,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "edge_decisions": [decision.to_dict() for decision in self.edge_decisions],
            "semantic_correctness_not_evaluated": self.semantic_correctness_not_evaluated,
        }

    def validate(self) -> None:
        if self.schema_version != MODEL_CANDIDATE_AUDIT_RECORD_SCHEMA_VERSION:
            raise ValueError("unsupported model candidate audit record schema")
        for value, label in (
            (self.raw_response_sha256, "raw response hash"),
            (self.candidate_sha256, "candidate hash"),
            (self.assembly_report_sha256, "assembly report hash"),
            (self.assembled_page_spec_sha256, "assembled PageSpec hash"),
            (self.visibility_receipt_sha256, "visibility receipt hash"),
            (self.synthetic_input_sha256, "synthetic input hash"),
            (self.mock_serializer_config_sha256, "mock serializer/config hash"),
        ):
            _require_sha256(value, label)
        if not isinstance(self.assembly_report_id, str) or not re.fullmatch(r"assembly-[0-9a-f]{64}", self.assembly_report_id):
            raise ValueError("assembly report ID is invalid")
        if not isinstance(self.visibility_receipt_id, str) or not re.fullmatch(r"mock-receipt-[0-9a-f]{64}", self.visibility_receipt_id):
            raise ValueError("visibility receipt ID is invalid")
        _require_tuple(self.edge_decisions, "candidate audit decision sequence")
        identities: set[tuple[str, str, str]] = set()
        for decision in self.edge_decisions:
            if not isinstance(decision, CandidateAuditDecision):
                raise ValueError("candidate audit record contains an invalid decision type")
            decision.validate()
            identity = (decision.candidate_entity_stable_id, decision.source_kind, decision.source_id)
            if identity in identities:
                raise ValueError("candidate audit record contains duplicate claimed edges")
            identities.add(identity)
        if not isinstance(self.accepted_count, int) or isinstance(self.accepted_count, bool) or self.accepted_count < 0:
            raise ValueError("candidate audit accepted count is invalid")
        if not isinstance(self.rejected_count, int) or isinstance(self.rejected_count, bool) or self.rejected_count < 0:
            raise ValueError("candidate audit rejected count is invalid")
        accepted_count = sum(decision.status == "accepted" for decision in self.edge_decisions)
        rejected_count = sum(decision.status == "rejected" for decision in self.edge_decisions)
        if (self.accepted_count, self.rejected_count) != (accepted_count, rejected_count):
            raise ValueError("candidate audit counts do not match its exact decision sequence")
        expected_status = "no_claims" if not self.edge_decisions else "clean" if rejected_count == 0 else "with_rejections"
        if self.status != expected_status:
            raise ValueError("candidate audit status does not match its decision sequence")
        if self.semantic_correctness_not_evaluated is not True:
            raise ValueError("candidate audit must explicitly avoid semantic correctness evaluation")
        if self.record_id != _audit_record_id(self._root_dict()):
            raise ValueError("candidate audit record ID does not bind its provenance root and ordered decisions")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"record_id": self.record_id, **self._root_dict()}

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256_bytes(self.canonical_json_bytes())

    def verified_edge_projection(self) -> tuple[VerifiedCandidateAttributionEdge, ...]:
        self.validate()
        return tuple(
            VerifiedCandidateAttributionEdge(
                candidate_entity_stable_id=decision.candidate_entity_stable_id,
                source_kind=decision.source_kind,
                source_id=decision.source_id,
                resolved_source=decision.resolved_source,
            )
            for decision in self.edge_decisions
            if decision.status == "accepted"
        )


class ModelCandidateAuditAdapter:
    """Build an audit record without expanding the M3 execution boundary."""

    def build(
        self,
        assembled_page_spec: AssembledPageSpec,
        visibility_receipt: SyntheticMockVisibilityReceipt,
        *,
        synthetic_input_bytes: bytes,
        mock_serializer_config_bytes: bytes,
    ) -> ModelCandidateAuditRecord:
        self._validate_inputs(
            assembled_page_spec,
            visibility_receipt,
            synthetic_input_bytes=synthetic_input_bytes,
            mock_serializer_config_bytes=mock_serializer_config_bytes,
        )
        decisions = tuple(
            self._audit_edge(edge, assembled_page_spec, visibility_receipt.source_registry)
            for edge in assembled_page_spec.candidate.claimed_attribution_edges
        )
        accepted_count = sum(decision.status == "accepted" for decision in decisions)
        rejected_count = sum(decision.status == "rejected" for decision in decisions)
        status = "no_claims" if not decisions else "clean" if rejected_count == 0 else "with_rejections"
        root = {
            "schema_version": MODEL_CANDIDATE_AUDIT_RECORD_SCHEMA_VERSION,
            "raw_response_sha256": assembled_page_spec.raw_response.sha256,
            "candidate_sha256": assembled_page_spec.candidate.sha256(),
            "assembly_report_id": assembled_page_spec.report.report_id,
            "assembly_report_sha256": assembled_page_spec.report.sha256(),
            "assembled_page_spec_sha256": assembled_page_spec.report.assembled_page_spec_sha256,
            "visibility_receipt_id": visibility_receipt.receipt_id,
            "visibility_receipt_sha256": visibility_receipt.sha256(),
            "synthetic_input_sha256": visibility_receipt.synthetic_input_sha256,
            "mock_serializer_config_sha256": visibility_receipt.mock_serializer_config_sha256,
            "status": status,
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "edge_decisions": [decision.to_dict() for decision in decisions],
            "semantic_correctness_not_evaluated": True,
        }
        record_fields = {**root, "edge_decisions": decisions}
        record = ModelCandidateAuditRecord(record_id=_audit_record_id(root), **record_fields)
        record.validate()
        return record

    def _validate_inputs(
        self,
        assembled_page_spec: AssembledPageSpec,
        visibility_receipt: SyntheticMockVisibilityReceipt,
        *,
        synthetic_input_bytes: bytes,
        mock_serializer_config_bytes: bytes,
    ) -> None:
        if not isinstance(assembled_page_spec, AssembledPageSpec):
            raise CandidateAuditConstructionError("assembled_artifact_invalid", "assembled PageSpec artifact is invalid")
        if not isinstance(visibility_receipt, SyntheticMockVisibilityReceipt):
            raise CandidateAuditConstructionError("visibility_receipt_invalid", "synthetic/mock visibility receipt is invalid")
        synthetic_input_bytes = _require_nonempty_bytes(
            synthetic_input_bytes,
            "synthetic_input_bytes_invalid",
            "synthetic input bytes are invalid",
        )
        mock_serializer_config_bytes = _require_nonempty_bytes(
            mock_serializer_config_bytes,
            "mock_serializer_config_bytes_invalid",
            "mock serializer/config bytes are invalid",
        )
        try:
            assembled_page_spec.validate()
        except (TypeError, ValueError) as exc:
            raise CandidateAuditConstructionError("assembled_artifact_invalid", "assembled PageSpec artifact is invalid") from exc
        try:
            visibility_receipt.validate()
        except (TypeError, ValueError) as exc:
            raise CandidateAuditConstructionError("visibility_receipt_invalid", "synthetic/mock visibility receipt is invalid") from exc
        if (
            len(synthetic_input_bytes) != visibility_receipt.synthetic_input_byte_length
            or _sha256_bytes(synthetic_input_bytes) != visibility_receipt.synthetic_input_sha256
        ):
            raise CandidateAuditConstructionError(
                "synthetic_input_receipt_binding_mismatch",
                "synthetic input bytes do not match the visibility receipt",
            )
        if _sha256_bytes(mock_serializer_config_bytes) != visibility_receipt.mock_serializer_config_sha256:
            raise CandidateAuditConstructionError(
                "mock_serializer_config_receipt_binding_mismatch",
                "mock serializer/config bytes do not match the visibility receipt",
            )
        try:
            reparsed_candidate = parse_provider_raw_response(assembled_page_spec.raw_response)
        except (TypeError, ValueError) as exc:
            raise CandidateAuditConstructionError("candidate_raw_binding_invalid", "raw response cannot be rebound to the assembled candidate") from exc
        candidate = assembled_page_spec.candidate
        report = assembled_page_spec.report
        if reparsed_candidate != candidate or reparsed_candidate.sha256() != candidate.sha256():
            raise CandidateAuditConstructionError("candidate_raw_binding_invalid", "raw response cannot be rebound to the assembled candidate")
        if report.raw_response_sha256 != assembled_page_spec.raw_response.sha256:
            raise CandidateAuditConstructionError("assembly_provenance_invalid", "assembly report raw-response binding is invalid")
        if report.candidate_sha256 != candidate.sha256():
            raise CandidateAuditConstructionError("assembly_provenance_invalid", "assembly report candidate binding is invalid")
        if report.assembled_page_spec_sha256 != _sha256_json(assembled_page_spec.page_spec.to_dict()):
            raise CandidateAuditConstructionError("assembly_provenance_invalid", "assembly report PageSpec binding is invalid")
        if report.claimed_attribution_edges != candidate.claimed_attribution_edges:
            raise CandidateAuditConstructionError("assembly_provenance_invalid", "assembly report claimed-edge binding is invalid")
        _candidate_entity_ids(candidate)

    def _audit_edge(
        self,
        edge: ClaimedAttributionEdge,
        assembled_page_spec: AssembledPageSpec,
        source_registry: SyntheticMockVisibilitySourceRegistry,
    ) -> CandidateAuditDecision:
        edge.validate()
        if edge.candidate_entity_stable_id not in _candidate_entity_ids(assembled_page_spec.candidate):
            raise CandidateAuditConstructionError("candidate_edge_binding_invalid", "candidate edge cannot be bound to a semantic entity")
        source = source_registry.lookup(edge.source_kind, edge.source_id)
        if source is None:
            return _decision_from_resolution(edge, ResolvedVisibilitySource(
                source_kind=edge.source_kind,
                source_id=edge.source_id,
                registry_found=False,
            ))
        if isinstance(source, MockRequirementVisibilitySource):
            return _decision_from_resolution(edge, ResolvedVisibilitySource(
                source_kind=edge.source_kind,
                source_id=edge.source_id,
                registry_found=True,
                requirement_id=source.requirement_id,
                requirement_visible=source.visible,
                requirement_eligible=source.eligible,
            ))
        if isinstance(source, MockEvidenceVisibilitySource):
            evidence_roles = {
                item.doc_id: item.role
                for item in assembled_page_spec.page_spec.traceability.evidence
            }
            doc_bound = source.doc_id in evidence_roles
            role_matches = doc_bound and evidence_roles[source.doc_id] == source.role
            return _decision_from_resolution(edge, ResolvedVisibilitySource(
                source_kind=edge.source_kind,
                source_id=edge.source_id,
                registry_found=True,
                evidence_role=source.role,
                evidence_doc_id=source.doc_id,
                evidence_doc_bound_to_page_spec=doc_bound,
                evidence_role_matches_trace=role_matches,
                evidence_visible=source.visible,
                evidence_fixture_permission=source.fixture_permission,
                evidence_eligible=source.eligible,
            ))
        if not isinstance(source, MockPolicyVisibilitySource):
            raise CandidateAuditConstructionError("visibility_receipt_invalid", "synthetic/mock visibility source has an invalid type")
        return _decision_from_resolution(edge, ResolvedVisibilitySource(
            source_kind=edge.source_kind,
            source_id=edge.source_id,
            registry_found=True,
            policy_version=source.policy_version,
            fixture_policy_version=source.fixture_policy_version,
            policy_sha256=source.policy_sha256,
            fixture_policy_sha256=source.fixture_policy_sha256,
            policy_visible=source.visible,
            policy_eligible=source.eligible,
        ))


def _candidate_entity_ids(candidate: ModelSemanticCandidate) -> set[str]:
    candidate.validate()
    entity_ids = {
        *(item.stable_id for item in candidate.sections),
        *(item.stable_id for item in candidate.components),
        *(item.stable_id for item in candidate.states),
        *(item.stable_id for item in candidate.interactions),
        *(item.stable_id for item in candidate.constraints),
        *(item.stable_id for item in candidate.acceptance_checks),
    }
    if not entity_ids:
        raise CandidateAuditConstructionError("candidate_edge_binding_invalid", "candidate does not contain semantic entities")
    return entity_ids


def _decision_from_resolution(edge: ClaimedAttributionEdge, resolution: ResolvedVisibilitySource) -> CandidateAuditDecision:
    status, reason_code = expected_audit_decision_from_resolution(resolution)
    decision = CandidateAuditDecision(
        candidate_entity_stable_id=edge.candidate_entity_stable_id,
        source_kind=edge.source_kind,
        source_id=edge.source_id,
        status=status,
        reason_code=reason_code,
        resolved_source=resolution,
    )
    decision.validate()
    return decision


def _receipt_id(root: dict[str, Any]) -> str:
    return f"mock-receipt-{_sha256_json(root)}"


def _audit_record_id(root: dict[str, Any]) -> str:
    return f"candidate-audit-{_sha256_json(root)}"
