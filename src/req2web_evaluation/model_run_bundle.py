from __future__ import annotations

"""Synthetic/mock-only evaluation-bundle foundation for the M3 local path.

This module writes and validates an internal provenance bundle. It never builds
a D17 payload, loads a model, performs external egress, imports semantic gold,
or routes a result package.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import uuid
from typing import Any, Mapping

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle, UseCase
from req2web_generation import RetrievalGuidanceBuilder
from req2web_generation.retrieval_guidance import RETRIEVAL_GUIDANCE_SCHEMA_VERSION, RetrievalGuidance
from req2web_generation.schema import (
    PAGE_SPEC_SCHEMA_VERSION,
    AcceptanceCheck,
    ComponentSpec,
    ConstraintSpec,
    EvidenceReference,
    InteractionSpec,
    LayoutSpec,
    PageSpec,
    PageState,
    PageUseCase,
    SectionSpec,
    TraceabilitySpec,
    UseCaseTrace,
)
from req2web_rag.corpus import ROLE_ORDER
from req2web_provider.candidate_audit import (
    MODEL_CANDIDATE_AUDIT_RECORD_SCHEMA_VERSION,
    SYNTHETIC_MOCK_VISIBILITY_RECEIPT_SCHEMA_VERSION,
    CandidateAuditDecision,
    MockEvidenceVisibilitySource,
    MockPolicyVisibilitySource,
    MockRequirementVisibilitySource,
    ModelCandidateAuditRecord,
    ResolvedVisibilitySource,
    SyntheticMockVisibilityReceipt,
    SyntheticMockVisibilitySourceRegistry,
)
from req2web_provider.semantic_candidate import (
    MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION,
    PAGE_SPEC_ASSEMBLY_REPORT_SCHEMA_VERSION,
    PROVIDER_RAW_RESPONSE_SCHEMA_VERSION,
    ArtifactVisibilityRecord,
    AssembledPageSpec,
    ClaimedAttributionEdge,
    FieldOrigin,
    ModelSemanticCandidate,
    PageSpecAssemblyReport,
    ProviderRawResponse,
    parse_provider_raw_response,
)


MODEL_RUN_BUNDLE_SCHEMA_VERSION = "req2web.evaluation.model_run_bundle.v1"
_SYNTHETIC_INPUT_SCHEMA_VERSION = "req2web.evaluation.synthetic_mock_input_bytes.v1"
_MOCK_SERIALIZER_CONFIG_SCHEMA_VERSION = "req2web.evaluation.mock_serializer_config_bytes.v1"
_DECLARATION = "synthetic_mock_only_evaluator_free_not_a_d17_or_model_execution_record"
_BUNDLE_ID_PREFIX = "synthetic-mock-bundle-"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_FIXED_INVENTORY_SPECS = (
    ("artifacts/agent_context.json", "agent_context", AGENT_BUNDLE_SCHEMA_VERSION, "local_only"),
    ("artifacts/retrieval_guidance.json", "retrieval_guidance", RETRIEVAL_GUIDANCE_SCHEMA_VERSION, "local_only"),
    ("artifacts/provider_raw_response.bin", "provider_raw_response", PROVIDER_RAW_RESPONSE_SCHEMA_VERSION, "provider_output_only"),
    ("artifacts/model_semantic_candidate.json", "model_semantic_candidate", MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION, "provider_output_only"),
    ("artifacts/page_spec_assembly_report.json", "page_spec_assembly_report", PAGE_SPEC_ASSEMBLY_REPORT_SCHEMA_VERSION, "local_only"),
    ("artifacts/assembled_page_spec.json", "assembled_page_spec", PAGE_SPEC_SCHEMA_VERSION, "local_only"),
    ("artifacts/synthetic_mock_visibility_receipt.json", "synthetic_mock_visibility_receipt", SYNTHETIC_MOCK_VISIBILITY_RECEIPT_SCHEMA_VERSION, "synthetic_mock_only"),
    ("artifacts/model_candidate_audit_record.json", "model_candidate_audit_record", MODEL_CANDIDATE_AUDIT_RECORD_SCHEMA_VERSION, "local_only"),
    ("inputs/synthetic_input.bin", "synthetic_input", _SYNTHETIC_INPUT_SCHEMA_VERSION, "synthetic_mock_only"),
    ("inputs/mock_serializer_config.bin", "mock_serializer_config", _MOCK_SERIALIZER_CONFIG_SCHEMA_VERSION, "synthetic_mock_only"),
)
_FIXED_PATHS = tuple(item[0] for item in _FIXED_INVENTORY_SPECS)
_FIXED_SPECS_BY_PATH = {item[0]: item[1:] for item in _FIXED_INVENTORY_SPECS}
_FIXED_ARTIFACT_KINDS = frozenset(item[1] for item in _FIXED_INVENTORY_SPECS)
_REQUIRED_FILES = frozenset({"bundle_manifest.json", *_FIXED_PATHS})
_SENSITIVE_KEY_TOKENS = ("token", "cookie", "authorization", "secret", "apikey", "password", "credential")
_ABSOLUTE_PATH_RE = re.compile(r"^[a-zA-Z]:[\\/]")

_ERROR_MESSAGES = {
    "output_destination_invalid": "output destination is invalid",
    "output_destination_exists": "output destination already exists",
    "output_publish_failed": "bundle publish failed",
    "live_context_invalid": "live AgentContext artifact is invalid",
    "live_guidance_invalid": "live RetrievalGuidance artifact is invalid",
    "guidance_context_binding_invalid": "guidance does not bind to the live context",
    "live_assembly_invalid": "live assembled PageSpec artifact is invalid",
    "assembly_context_guidance_binding_invalid": "assembly does not bind to the live context and guidance",
    "assembly_raw_candidate_binding_invalid": "assembly does not bind to the live raw response and candidate",
    "visibility_receipt_invalid": "synthetic/mock visibility receipt is invalid",
    "synthetic_input_receipt_binding_mismatch": "synthetic input bytes do not match the visibility receipt",
    "mock_serializer_config_receipt_binding_mismatch": "mock serializer/config bytes do not match the visibility receipt",
    "candidate_audit_invalid": "model candidate audit record is invalid",
    "candidate_audit_binding_invalid": "model candidate audit does not bind to the live artifact root",
    "bundle_directory_invalid": "bundle directory is invalid",
    "bundle_symlink_detected": "bundle contains a symbolic link",
    "bundle_inventory_invalid": "bundle inventory is invalid",
    "bundle_file_missing": "bundle is missing a required file",
    "bundle_file_unexpected": "bundle contains an unexpected file",
    "bundle_json_invalid": "bundle JSON artifact is invalid",
    "bundle_json_noncanonical": "bundle JSON artifact is not canonical",
    "bundle_file_integrity_invalid": "bundle file integrity is invalid",
    "bundle_manifest_invalid": "bundle manifest is invalid",
    "bundle_schema_invalid": "bundle artifact schema is invalid",
    "bundle_provenance_invalid": "bundle provenance binding is invalid",
    "bundle_status_invalid": "bundle downstream status is invalid",
    "bundle_storage_json_invalid": "bundle storage bytes are not an eligible canonical JSON fixture",
    "bundle_storage_sensitive_data": "bundle storage bytes contain a sensitive key or absolute path",
    "bundle_page_spec_invalid": "stored PageSpec artifact is invalid",
}


class ModelRunBundleError(ValueError):
    """Safe package-stage error with no bytes, secrets, or paths in its text."""

    stage = "package"
    phase = "evaluation_bundle"

    def __init__(self, code: str) -> None:
        if code not in _ERROR_MESSAGES:
            raise ValueError("unsupported evaluation bundle error code")
        super().__init__(_ERROR_MESSAGES[code])
        self.code = code
        self.message = _ERROR_MESSAGES[code]


def _error(code: str) -> ModelRunBundleError:
    return ModelRunBundleError(code)


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(payload: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(payload))


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError("invalid SHA-256")
    return value


def _require_nonempty_bytes(value: object) -> bytes:
    if not isinstance(value, bytes) or not value:
        raise ValueError("invalid bytes")
    return value


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or "" in path.parts:
        raise ValueError("unsafe relative path")
    if path.as_posix() != value or "\\" in value:
        raise ValueError("unsafe relative path")
    return value


def _strict_json_loads(value: bytes) -> dict[str, Any]:
    if value.startswith(b"\xef\xbb\xbf"):
        raise _error("bundle_json_noncanonical")
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error("bundle_json_invalid") from exc

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result

    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite")),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _error("bundle_json_invalid") from exc
    if not isinstance(payload, dict):
        raise _error("bundle_json_invalid")
    try:
        canonical = _canonical_json_bytes(payload)
    except (TypeError, ValueError) as exc:
        raise _error("bundle_json_invalid") from exc
    if canonical != value:
        raise _error("bundle_json_noncanonical")
    return payload


def _assert_exact_keys(payload: Mapping[str, Any], expected: set[str], error_code: str) -> None:
    if set(payload) != expected:
        raise _error(error_code)


def _json_artifact(value: object) -> bytes:
    return _canonical_json_bytes(value)


def _validate_bundle_storage_json_bytes(value: object) -> bytes:
    try:
        raw_bytes = _require_nonempty_bytes(value)
        payload = _strict_json_loads(raw_bytes)
    except (ModelRunBundleError, ValueError) as exc:
        raise _error("bundle_storage_json_invalid") from exc
    _reject_sensitive_fixture_content(payload)
    return raw_bytes


def _reject_sensitive_fixture_content(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _error("bundle_storage_json_invalid")
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if any(token in normalized for token in _SENSITIVE_KEY_TOKENS):
                raise _error("bundle_storage_sensitive_data")
            _reject_sensitive_fixture_content(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_sensitive_fixture_content(item)
        return
    if isinstance(value, str):
        if value.startswith(("/", "\\")) or _ABSOLUTE_PATH_RE.match(value) or value.lower().startswith("file://"):
            raise _error("bundle_storage_sensitive_data")
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    raise _error("bundle_storage_json_invalid")


def _require_object_list(value: object, keys: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("expected object list")
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("expected object")
        if set(item) != keys:
            raise ValueError("unexpected object keys")
        items.append(item)
    return items


def _page_spec_from_dict(payload: Mapping[str, Any]) -> PageSpec:
    expected = {
        "page_id", "title", "summary", "target_device", "page_type", "layout", "use_cases", "sections",
        "components", "states", "interactions", "constraints", "acceptance_checks", "traceability", "schema_version",
    }
    try:
        _assert_exact_keys(payload, expected, "bundle_page_spec_invalid")
        layout_payload = payload["layout"]
        traceability_payload = payload["traceability"]
        if not isinstance(layout_payload, dict) or not isinstance(traceability_payload, dict):
            raise ValueError("nested PageSpec object")
        if set(layout_payload) != {"pattern", "section_order"}:
            raise ValueError("layout keys")
        if set(traceability_payload) != {"source_context_schema_version", "evidence", "use_cases"}:
            raise ValueError("traceability keys")
        page_spec = PageSpec(
            page_id=payload["page_id"],
            title=payload["title"],
            summary=payload["summary"],
            target_device=payload["target_device"],
            page_type=payload["page_type"],
            layout=LayoutSpec(**layout_payload),
            use_cases=[PageUseCase(**item) for item in _require_object_list(payload["use_cases"], {"use_case_id", "title", "actor", "goal", "expected_outcome"})],
            sections=[SectionSpec(**item) for item in _require_object_list(payload["sections"], {"section_id", "title", "purpose", "component_ids", "use_case_ids"})],
            components=[ComponentSpec(**item) for item in _require_object_list(payload["components"], {"component_id", "section_id", "component_type", "label", "purpose"})],
            states=[PageState(**item) for item in _require_object_list(payload["states"], {"state_id", "name", "description", "visible_component_ids"})],
            interactions=[InteractionSpec(**item) for item in _require_object_list(payload["interactions"], {"interaction_id", "trigger_component_id", "source_state_id", "action", "target_state_id", "user_feedback", "use_case_ids"})],
            constraints=[ConstraintSpec(**item) for item in _require_object_list(payload["constraints"], {"constraint_id", "description", "source"})],
            acceptance_checks=[AcceptanceCheck(**item) for item in _require_object_list(payload["acceptance_checks"], {"check_id", "description", "use_case_ids", "state_id"})],
            traceability=TraceabilitySpec(
                source_context_schema_version=traceability_payload["source_context_schema_version"],
                evidence=[EvidenceReference(**item) for item in _require_object_list(traceability_payload["evidence"], {"role", "doc_id", "title", "reference_uris"})],
                use_cases=[UseCaseTrace(**item) for item in _require_object_list(traceability_payload["use_cases"], {"use_case_id", "section_ids", "component_ids", "interaction_ids", "evidence_doc_ids"})],
            ),
            schema_version=payload["schema_version"],
        )
        page_spec.validate()
        return page_spec
    except (KeyError, TypeError, ValueError, ModelRunBundleError) as exc:
        raise _error("bundle_page_spec_invalid") from exc


def _receipt_from_dict(payload: Mapping[str, Any]) -> SyntheticMockVisibilityReceipt:
    try:
        registry_payload = payload["source_registry"]
        if not isinstance(registry_payload, dict):
            raise ValueError("registry")
        registry = SyntheticMockVisibilitySourceRegistry(
            requirements=tuple(MockRequirementVisibilitySource(**item) for item in registry_payload["requirements"]),
            evidences=tuple(MockEvidenceVisibilitySource(**item) for item in registry_payload["evidences"]),
            policies=tuple(MockPolicyVisibilitySource(**item) for item in registry_payload["policies"]),
        )
        receipt = SyntheticMockVisibilityReceipt(
            receipt_id=payload["receipt_id"],
            synthetic_input_sha256=payload["synthetic_input_sha256"],
            synthetic_input_byte_length=payload["synthetic_input_byte_length"],
            mock_serializer_config_sha256=payload["mock_serializer_config_sha256"],
            source_registry=registry,
            source_registry_sha256=payload["source_registry_sha256"],
            mock_only=payload["mock_only"],
            external_egress_allowed=payload["external_egress_allowed"],
            is_d17_manifest=payload["is_d17_manifest"],
            is_d17_serializer_output=payload["is_d17_serializer_output"],
            declaration=payload["declaration"],
            schema_version=payload["schema_version"],
        )
        receipt.validate()
        return receipt
    except (KeyError, TypeError, ValueError) as exc:
        raise _error("visibility_receipt_invalid") from exc


def _report_from_dict(payload: Mapping[str, Any]) -> PageSpecAssemblyReport:
    try:
        report = PageSpecAssemblyReport(
            report_id=payload["report_id"],
            raw_response_sha256=payload["raw_response_sha256"],
            candidate_sha256=payload["candidate_sha256"],
            context_sha256=payload["context_sha256"],
            guidance_sha256=payload["guidance_sha256"],
            assembled_page_spec_sha256=payload["assembled_page_spec_sha256"],
            field_origins=tuple(FieldOrigin(**item) for item in payload["field_origins"]),
            input_visibility=tuple(ArtifactVisibilityRecord(**item) for item in payload["input_visibility"]),
            claimed_attribution_edges=tuple(ClaimedAttributionEdge(**item) for item in payload["claimed_attribution_edges"]),
            local_identity_scaffold=payload["local_identity_scaffold"],
            schema_version=payload["schema_version"],
        )
        report.validate()
        return report
    except (KeyError, TypeError, ValueError) as exc:
        raise _error("bundle_provenance_invalid") from exc


def _audit_from_dict(payload: Mapping[str, Any]) -> ModelCandidateAuditRecord:
    try:
        decisions = tuple(
            CandidateAuditDecision(
                candidate_entity_stable_id=item["candidate_entity_stable_id"],
                source_kind=item["source_kind"],
                source_id=item["source_id"],
                status=item["status"],
                reason_code=item["reason_code"],
                resolved_source=ResolvedVisibilitySource(**item["resolved_source"]),
            )
            for item in payload["edge_decisions"]
        )
        record = ModelCandidateAuditRecord(
            record_id=payload["record_id"],
            raw_response_sha256=payload["raw_response_sha256"],
            candidate_sha256=payload["candidate_sha256"],
            assembly_report_id=payload["assembly_report_id"],
            assembly_report_sha256=payload["assembly_report_sha256"],
            assembled_page_spec_sha256=payload["assembled_page_spec_sha256"],
            visibility_receipt_id=payload["visibility_receipt_id"],
            visibility_receipt_sha256=payload["visibility_receipt_sha256"],
            synthetic_input_sha256=payload["synthetic_input_sha256"],
            mock_serializer_config_sha256=payload["mock_serializer_config_sha256"],
            status=payload["status"],
            accepted_count=payload["accepted_count"],
            rejected_count=payload["rejected_count"],
            edge_decisions=decisions,
            semantic_correctness_not_evaluated=payload["semantic_correctness_not_evaluated"],
            schema_version=payload["schema_version"],
        )
        record.validate()
        return record
    except (KeyError, TypeError, ValueError) as exc:
        raise _error("candidate_audit_invalid") from exc


def _context_from_dict(payload: Mapping[str, Any]) -> AgentContextBundle:
    try:
        context = AgentContextBundle(
            original_requirement=payload["original_requirement"],
            requirement_summary=payload["requirement_summary"],
            target_device=payload["target_device"],
            task_type=payload["task_type"],
            constraints=payload["constraints"],
            use_cases=[UseCase(**item) for item in payload["use_cases"]],
            retrieval_queries={role: payload["retrieval_queries"][role] for role in ROLE_ORDER},
            retrieval_results={role: payload["retrieval_results"][role] for role in ROLE_ORDER},
            schema_version=payload["schema_version"],
        )
        context.validate()
        return context
    except (KeyError, TypeError, ValueError) as exc:
        raise _error("live_context_invalid") from exc

@dataclass(frozen=True)
class BundleInventoryEntry:
    relative_path: str
    artifact_kind: str
    schema_version: str
    sha256: str
    byte_length: int
    input_visibility: str

    def validate(self) -> None:
        _safe_relative_path(self.relative_path)
        if not isinstance(self.artifact_kind, str) or not self.artifact_kind:
            raise ValueError("artifact kind")
        if not isinstance(self.schema_version, str) or not self.schema_version:
            raise ValueError("artifact schema")
        _require_sha256(self.sha256)
        if not isinstance(self.byte_length, int) or isinstance(self.byte_length, bool) or self.byte_length <= 0:
            raise ValueError("artifact byte length")
        if self.input_visibility not in {"local_only", "provider_output_only", "synthetic_mock_only"}:
            raise ValueError("artifact input visibility")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "relative_path": self.relative_path,
            "artifact_kind": self.artifact_kind,
            "schema_version": self.schema_version,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "input_visibility": self.input_visibility,
        }


@dataclass(frozen=True)
class ModelRunBundleManifest:
    bundle_id: str
    artifact_inventory: tuple[BundleInventoryEntry, ...]
    artifact_roots: Mapping[str, Mapping[str, str]]
    assembly: Mapping[str, Any]
    candidate_audit: Mapping[str, Any]
    downstream_status: Mapping[str, Mapping[str, Any]]
    closed_run_metadata: Mapping[str, Any]
    declaration: str = _DECLARATION
    synthetic_mock_only: bool = True
    external_egress_allowed: bool = False
    real_d17_path_status: str = "not_selected_or_executed"
    real_serializer_status: str = "not_selected_or_executed"
    provider_status: str = "not_selected_or_executed"
    model_status: str = "not_selected_or_executed"
    parity_status: str = "not_evaluated"
    formal_experiment_status: str = "not_executed"
    schema_version: str = MODEL_RUN_BUNDLE_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        artifact_inventory: tuple[BundleInventoryEntry, ...],
        artifact_roots: Mapping[str, Mapping[str, str]],
        assembly: Mapping[str, Any],
        candidate_audit: Mapping[str, Any],
        downstream_status: Mapping[str, Mapping[str, Any]],
    ) -> "ModelRunBundleManifest":
        closed_run_metadata = _closed_run_metadata()
        root = cls._root_dict_for(
            artifact_inventory=artifact_inventory,
            artifact_roots=artifact_roots,
            assembly=assembly,
            candidate_audit=candidate_audit,
            downstream_status=downstream_status,
            closed_run_metadata=closed_run_metadata,
        )
        return cls(
            bundle_id=f"{_BUNDLE_ID_PREFIX}{_sha256_json(root)}",
            artifact_inventory=artifact_inventory,
            artifact_roots=artifact_roots,
            assembly=assembly,
            candidate_audit=candidate_audit,
            downstream_status=downstream_status,
            closed_run_metadata=closed_run_metadata,
        )

    @staticmethod
    def _root_dict_for(
        *,
        artifact_inventory: tuple[BundleInventoryEntry, ...],
        artifact_roots: Mapping[str, Mapping[str, str]],
        assembly: Mapping[str, Any],
        candidate_audit: Mapping[str, Any],
        downstream_status: Mapping[str, Mapping[str, Any]],
        closed_run_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": MODEL_RUN_BUNDLE_SCHEMA_VERSION,
            "declaration": _DECLARATION,
            "synthetic_mock_only": True,
            "external_egress_allowed": False,
            "real_d17_path_status": "not_selected_or_executed",
            "real_serializer_status": "not_selected_or_executed",
            "provider_status": "not_selected_or_executed",
            "model_status": "not_selected_or_executed",
            "parity_status": "not_evaluated",
            "formal_experiment_status": "not_executed",
            "artifact_inventory": [item.to_dict() for item in artifact_inventory],
            "artifact_roots": dict(artifact_roots),
            "assembly": dict(assembly),
            "candidate_audit": dict(candidate_audit),
            "downstream_status": dict(downstream_status),
            "closed_run_metadata": dict(closed_run_metadata),
        }

    def _root_dict(self) -> dict[str, Any]:
        return self._root_dict_for(
            artifact_inventory=self.artifact_inventory,
            artifact_roots=self.artifact_roots,
            assembly=self.assembly,
            candidate_audit=self.candidate_audit,
            downstream_status=self.downstream_status,
            closed_run_metadata=self.closed_run_metadata,
        )

    def validate(self) -> None:
        if self.schema_version != MODEL_RUN_BUNDLE_SCHEMA_VERSION:
            raise ValueError("bundle manifest schema")
        if self.declaration != _DECLARATION:
            raise ValueError("bundle declaration")
        if self.synthetic_mock_only is not True or self.external_egress_allowed is not False:
            raise ValueError("bundle mock declaration")
        if any(value != "not_selected_or_executed" for value in (
            self.real_d17_path_status, self.real_serializer_status, self.provider_status, self.model_status,
        )):
            raise ValueError("bundle execution declaration")
        if self.parity_status != "not_evaluated" or self.formal_experiment_status != "not_executed":
            raise ValueError("bundle evaluation declaration")
        if not isinstance(self.bundle_id, str) or not self.bundle_id.startswith(_BUNDLE_ID_PREFIX):
            raise ValueError("bundle ID")
        digest = self.bundle_id.removeprefix(_BUNDLE_ID_PREFIX)
        _require_sha256(digest)
        if digest != _sha256_json(self._root_dict()):
            raise ValueError("bundle ID")
        if len(self.artifact_inventory) != len(_FIXED_INVENTORY_SPECS):
            raise ValueError("bundle inventory")
        for item, expected in zip(self.artifact_inventory, _FIXED_INVENTORY_SPECS, strict=True):
            item.validate()
            if (item.relative_path, item.artifact_kind, item.schema_version, item.input_visibility) != expected:
                raise ValueError("bundle inventory")
        if not isinstance(self.artifact_roots, Mapping) or set(self.artifact_roots) != _FIXED_ARTIFACT_KINDS:
            raise ValueError("bundle roots")
        for value in self.artifact_roots.values():
            if not isinstance(value, Mapping) or set(value) != {"schema_version", "sha256"}:
                raise ValueError("bundle root")
            if not isinstance(value["schema_version"], str) or not value["schema_version"]:
                raise ValueError("bundle root schema")
            _require_sha256(value["sha256"])
        _validate_assembly_summary(self.assembly)
        _validate_audit_summary(self.candidate_audit)
        _validate_downstream_status(self.downstream_status)
        _validate_closed_run_metadata(self.closed_run_metadata)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"bundle_id": self.bundle_id, **self._root_dict()}

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ModelRunBundleManifest":
        _assert_exact_keys(payload, {
            "bundle_id", "schema_version", "declaration", "synthetic_mock_only", "external_egress_allowed",
            "real_d17_path_status", "real_serializer_status", "provider_status", "model_status", "parity_status",
            "formal_experiment_status", "artifact_inventory", "artifact_roots", "assembly", "candidate_audit",
            "downstream_status", "closed_run_metadata",
        }, "bundle_manifest_invalid")
        try:
            manifest = cls(
                bundle_id=payload["bundle_id"],
                artifact_inventory=tuple(BundleInventoryEntry(**item) for item in payload["artifact_inventory"]),
                artifact_roots=payload["artifact_roots"],
                assembly=payload["assembly"],
                candidate_audit=payload["candidate_audit"],
                downstream_status=payload["downstream_status"],
                closed_run_metadata=payload["closed_run_metadata"],
                declaration=payload["declaration"],
                synthetic_mock_only=payload["synthetic_mock_only"],
                external_egress_allowed=payload["external_egress_allowed"],
                real_d17_path_status=payload["real_d17_path_status"],
                real_serializer_status=payload["real_serializer_status"],
                provider_status=payload["provider_status"],
                model_status=payload["model_status"],
                parity_status=payload["parity_status"],
                formal_experiment_status=payload["formal_experiment_status"],
                schema_version=payload["schema_version"],
            )
            manifest.validate()
            return manifest
        except (KeyError, TypeError, ValueError, ModelRunBundleError) as exc:
            raise _error("bundle_manifest_invalid") from exc


@dataclass(frozen=True)
class WrittenModelRunBundle:
    bundle_directory: Path
    manifest: ModelRunBundleManifest


def _validate_assembly_summary(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "assembly_report_id", "field_origins", "input_visibility", "claimed_attribution_edges", "local_identity_scaffold",
    }:
        raise ValueError("assembly summary")
    if not isinstance(value["assembly_report_id"], str) or not value["assembly_report_id"].startswith("assembly-"):
        raise ValueError("assembly report ID")
    if value["local_identity_scaffold"] != "provenance_only_not_model_attribution":
        raise ValueError("assembly local scaffold")
    if not isinstance(value["field_origins"], list) or not isinstance(value["input_visibility"], list):
        raise ValueError("assembly summaries")
    if not isinstance(value["claimed_attribution_edges"], list):
        raise ValueError("assembly claims")


def _validate_audit_summary(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "record_id", "status", "accepted_count", "rejected_count", "edge_decisions", "semantic_correctness_not_evaluated",
    }:
        raise ValueError("audit summary")
    if not isinstance(value["record_id"], str) or not value["record_id"].startswith("candidate-audit-"):
        raise ValueError("audit record ID")
    if value["status"] not in {"no_claims", "clean", "with_rejections"}:
        raise ValueError("audit status")
    if (
        not isinstance(value["accepted_count"], int)
        or isinstance(value["accepted_count"], bool)
        or value["accepted_count"] < 0
        or not isinstance(value["rejected_count"], int)
        or isinstance(value["rejected_count"], bool)
        or value["rejected_count"] < 0
    ):
        raise ValueError("audit count")
    if not isinstance(value["edge_decisions"], list) or value["semantic_correctness_not_evaluated"] is not True:
        raise ValueError("audit decisions")


def _validate_downstream_status(value: object) -> None:
    expected = {
        "gate", "acceptance", "repair", "package_route", "fallback", "final_package",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("downstream status")
    exact = {
        "gate": {"status": "not_executed", "report_sha256": None},
        "acceptance": {"status": "not_executed", "report_sha256": None},
        "repair": {"status": "not_applicable_no_gate_result", "delta_sha256": None},
        "package_route": {"status": "not_executed", "route": None, "package_id": None, "package_sha256": None},
        "fallback": {"status": "not_applicable_no_failure", "source_package_id": None, "source_package_sha256": None},
        "final_package": {"status": "not_executed", "package_id": None, "package_sha256": None},
    }
    if dict(value) != exact:
        raise ValueError("downstream status")


def _closed_run_metadata() -> dict[str, Any]:
    return {
        "group": "synthetic_mock_not_experiment",
        "seed": None,
        "start_time": None,
        "end_time": None,
        "duration_ms": None,
        "unexpected_authoritative_field_findings": [],
    }


def _validate_closed_run_metadata(value: object) -> None:
    if not isinstance(value, Mapping) or dict(value) != _closed_run_metadata():
        raise ValueError("closed run metadata")


def _downstream_status() -> dict[str, dict[str, Any]]:
    return {
        "gate": {"status": "not_executed", "report_sha256": None},
        "acceptance": {"status": "not_executed", "report_sha256": None},
        "repair": {"status": "not_applicable_no_gate_result", "delta_sha256": None},
        "package_route": {"status": "not_executed", "route": None, "package_id": None, "package_sha256": None},
        "fallback": {"status": "not_applicable_no_failure", "source_package_id": None, "source_package_sha256": None},
        "final_package": {"status": "not_executed", "package_id": None, "package_sha256": None},
    }

class ModelRunBundleWriter:
    """Write a deterministic internal bundle from already-live M3-01/M3-02 artifacts."""

    def write(
        self,
        output_directory: Path | str,
        *,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        assembled_page_spec: AssembledPageSpec,
        visibility_receipt: SyntheticMockVisibilityReceipt,
        synthetic_input_bytes: bytes,
        mock_serializer_config_bytes: bytes,
        candidate_audit_record: ModelCandidateAuditRecord,
    ) -> WrittenModelRunBundle:
        live = self._validate_live_inputs(
            context=context,
            guidance=guidance,
            assembled_page_spec=assembled_page_spec,
            visibility_receipt=visibility_receipt,
            synthetic_input_bytes=synthetic_input_bytes,
            mock_serializer_config_bytes=mock_serializer_config_bytes,
            candidate_audit_record=candidate_audit_record,
        )
        destination = self._prepare_destination(output_directory)
        files, manifest = self._build_files(**live)
        staging = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
        try:
            staging.mkdir()
            for relative_path, value in files.items():
                target = staging.joinpath(*PurePosixPath(relative_path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(value)
            (staging / "bundle_manifest.json").write_bytes(manifest.canonical_json_bytes())
            os.replace(staging, destination)
        except (OSError, ValueError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise _error("output_publish_failed") from exc
        try:
            validated = validate_model_run_bundle(destination)
        except ModelRunBundleError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return WrittenModelRunBundle(bundle_directory=destination, manifest=validated)

    def _prepare_destination(self, output_directory: Path | str) -> Path:
        try:
            destination = Path(output_directory)
        except TypeError as exc:
            raise _error("output_destination_invalid") from exc
        if not destination.name or destination.exists() or destination.is_symlink():
            raise _error("output_destination_exists" if destination.exists() else "output_destination_invalid")
        if not destination.parent.is_dir() or destination.parent.is_symlink():
            raise _error("output_destination_invalid")
        return destination

    def _validate_live_inputs(
        self,
        *,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        assembled_page_spec: AssembledPageSpec,
        visibility_receipt: SyntheticMockVisibilityReceipt,
        synthetic_input_bytes: bytes,
        mock_serializer_config_bytes: bytes,
        candidate_audit_record: ModelCandidateAuditRecord,
    ) -> dict[str, Any]:
        if not isinstance(context, AgentContextBundle):
            raise _error("live_context_invalid")
        try:
            context.validate()
            context_bytes = _json_artifact(context.to_dict())
        except (TypeError, ValueError) as exc:
            raise _error("live_context_invalid") from exc
        if not isinstance(guidance, RetrievalGuidance):
            raise _error("live_guidance_invalid")
        try:
            guidance.validate()
            guidance_bytes = _json_artifact(guidance.to_dict())
            rebuilt_guidance_bytes = _json_artifact(RetrievalGuidanceBuilder().build(context).to_dict())
        except (TypeError, ValueError) as exc:
            raise _error("live_guidance_invalid") from exc
        if guidance_bytes != rebuilt_guidance_bytes:
            raise _error("guidance_context_binding_invalid")
        if not isinstance(assembled_page_spec, AssembledPageSpec):
            raise _error("live_assembly_invalid")
        try:
            assembled_page_spec.validate()
            reparsed_candidate = parse_provider_raw_response(assembled_page_spec.raw_response)
        except (TypeError, ValueError) as exc:
            raise _error("live_assembly_invalid") from exc
        if reparsed_candidate != assembled_page_spec.candidate:
            raise _error("assembly_raw_candidate_binding_invalid")
        raw = assembled_page_spec.raw_response
        candidate = assembled_page_spec.candidate
        page_spec = assembled_page_spec.page_spec
        report = assembled_page_spec.report
        candidate_bytes = _json_artifact(candidate.to_dict())
        page_spec_bytes = _json_artifact(page_spec.to_dict())
        report_bytes = report.canonical_json_bytes()
        if (
            report.context_sha256 != _sha256_bytes(context_bytes)
            or report.guidance_sha256 != _sha256_bytes(guidance_bytes)
        ):
            raise _error("assembly_context_guidance_binding_invalid")
        if (
            report.raw_response_sha256 != raw.sha256
            or report.candidate_sha256 != _sha256_bytes(candidate_bytes)
            or report.assembled_page_spec_sha256 != _sha256_bytes(page_spec_bytes)
        ):
            raise _error("assembly_raw_candidate_binding_invalid")
        synthetic_input_bytes = _validate_bundle_storage_json_bytes(synthetic_input_bytes)
        mock_serializer_config_bytes = _validate_bundle_storage_json_bytes(mock_serializer_config_bytes)
        if not isinstance(visibility_receipt, SyntheticMockVisibilityReceipt):
            raise _error("visibility_receipt_invalid")
        try:
            visibility_receipt.validate()
        except (TypeError, ValueError) as exc:
            raise _error("visibility_receipt_invalid") from exc
        if (
            visibility_receipt.mock_only is not True
            or visibility_receipt.external_egress_allowed is not False
            or visibility_receipt.is_d17_manifest is not False
            or visibility_receipt.is_d17_serializer_output is not False
        ):
            raise _error("visibility_receipt_invalid")
        if (
            visibility_receipt.synthetic_input_sha256 != _sha256_bytes(synthetic_input_bytes)
            or visibility_receipt.synthetic_input_byte_length != len(synthetic_input_bytes)
        ):
            raise _error("synthetic_input_receipt_binding_mismatch")
        if visibility_receipt.mock_serializer_config_sha256 != _sha256_bytes(mock_serializer_config_bytes):
            raise _error("mock_serializer_config_receipt_binding_mismatch")
        if not isinstance(candidate_audit_record, ModelCandidateAuditRecord):
            raise _error("candidate_audit_invalid")
        try:
            candidate_audit_record.validate()
        except (TypeError, ValueError) as exc:
            raise _error("candidate_audit_invalid") from exc
        receipt_bytes = visibility_receipt.canonical_json_bytes()
        audit_bytes = candidate_audit_record.canonical_json_bytes()
        if (
            candidate_audit_record.raw_response_sha256 != raw.sha256
            or candidate_audit_record.candidate_sha256 != candidate.sha256()
            or candidate_audit_record.assembly_report_id != report.report_id
            or candidate_audit_record.assembly_report_sha256 != _sha256_bytes(report_bytes)
            or candidate_audit_record.assembled_page_spec_sha256 != _sha256_bytes(page_spec_bytes)
            or candidate_audit_record.visibility_receipt_id != visibility_receipt.receipt_id
            or candidate_audit_record.visibility_receipt_sha256 != _sha256_bytes(receipt_bytes)
            or candidate_audit_record.synthetic_input_sha256 != _sha256_bytes(synthetic_input_bytes)
            or candidate_audit_record.mock_serializer_config_sha256 != _sha256_bytes(mock_serializer_config_bytes)
        ):
            raise _error("candidate_audit_binding_invalid")
        claims = tuple((item.candidate_entity_stable_id, item.source_kind, item.source_id) for item in candidate.claimed_attribution_edges)
        decisions = tuple((item.candidate_entity_stable_id, item.source_kind, item.source_id) for item in candidate_audit_record.edge_decisions)
        if decisions != claims:
            raise _error("candidate_audit_binding_invalid")
        return {
            "context": context,
            "guidance": guidance,
            "assembled_page_spec": assembled_page_spec,
            "visibility_receipt": visibility_receipt,
            "synthetic_input_bytes": synthetic_input_bytes,
            "mock_serializer_config_bytes": mock_serializer_config_bytes,
            "candidate_audit_record": candidate_audit_record,
            "context_bytes": context_bytes,
            "guidance_bytes": guidance_bytes,
            "candidate_bytes": candidate_bytes,
            "page_spec_bytes": page_spec_bytes,
            "report_bytes": report_bytes,
            "receipt_bytes": receipt_bytes,
            "audit_bytes": audit_bytes,
        }

    def _build_files(self, **live: Any) -> tuple[dict[str, bytes], ModelRunBundleManifest]:
        assembled: AssembledPageSpec = live["assembled_page_spec"]
        audit: ModelCandidateAuditRecord = live["candidate_audit_record"]
        files = {
            "artifacts/agent_context.json": live["context_bytes"],
            "artifacts/retrieval_guidance.json": live["guidance_bytes"],
            "artifacts/provider_raw_response.bin": assembled.raw_response.raw_bytes,
            "artifacts/model_semantic_candidate.json": live["candidate_bytes"],
            "artifacts/page_spec_assembly_report.json": live["report_bytes"],
            "artifacts/assembled_page_spec.json": live["page_spec_bytes"],
            "artifacts/synthetic_mock_visibility_receipt.json": live["receipt_bytes"],
            "artifacts/model_candidate_audit_record.json": live["audit_bytes"],
            "inputs/synthetic_input.bin": live["synthetic_input_bytes"],
            "inputs/mock_serializer_config.bin": live["mock_serializer_config_bytes"],
        }
        inventory = tuple(
            BundleInventoryEntry(
                relative_path=path,
                artifact_kind=kind,
                schema_version=schema,
                sha256=_sha256_bytes(files[path]),
                byte_length=len(files[path]),
                input_visibility=visibility,
            )
            for path, kind, schema, visibility in _FIXED_INVENTORY_SPECS
        )
        roots = {
            kind: {"schema_version": schema, "sha256": _sha256_bytes(files[path])}
            for path, kind, schema, _visibility in _FIXED_INVENTORY_SPECS
        }
        assembly = {
            "assembly_report_id": assembled.report.report_id,
            "field_origins": [item.__dict__ for item in assembled.report.field_origins],
            "input_visibility": [item.__dict__ for item in assembled.report.input_visibility],
            "claimed_attribution_edges": [{"candidate_entity_stable_id": item.candidate_entity_stable_id, "source_kind": item.source_kind, "source_id": item.source_id} for item in assembled.candidate.claimed_attribution_edges],
            "local_identity_scaffold": assembled.report.local_identity_scaffold,
        }
        candidate_audit = {
            "record_id": audit.record_id,
            "status": audit.status,
            "accepted_count": audit.accepted_count,
            "rejected_count": audit.rejected_count,
            "edge_decisions": [item.to_dict() for item in audit.edge_decisions],
            "semantic_correctness_not_evaluated": True,
        }
        return files, ModelRunBundleManifest.create(
            artifact_inventory=inventory,
            artifact_roots=roots,
            assembly=assembly,
            candidate_audit=candidate_audit,
            downstream_status=_downstream_status(),
        )

def validate_model_run_bundle(bundle_directory: Path | str) -> ModelRunBundleManifest:
    """Fail closed while reloading one fixed-layout synthetic/mock-only bundle."""
    try:
        root = Path(bundle_directory)
    except TypeError as exc:
        raise _error("bundle_directory_invalid") from exc
    if not root.is_dir() or root.is_symlink():
        raise _error("bundle_directory_invalid")
    try:
        entries = list(root.rglob("*"))
    except OSError as exc:
        raise _error("bundle_directory_invalid") from exc
    if any(item.is_symlink() for item in entries):
        raise _error("bundle_symlink_detected")
    actual_files = {item.relative_to(root).as_posix() for item in entries if item.is_file()}
    actual_directories = {item.relative_to(root).as_posix() for item in entries if item.is_dir()}
    missing = _REQUIRED_FILES - actual_files
    unexpected = actual_files - _REQUIRED_FILES
    if missing:
        raise _error("bundle_file_missing")
    if unexpected or actual_directories != {"artifacts", "inputs"}:
        raise _error("bundle_file_unexpected")
    if any(not item.is_file() and not item.is_dir() for item in entries):
        raise _error("bundle_inventory_invalid")
    try:
        manifest_bytes = (root / "bundle_manifest.json").read_bytes()
    except OSError as exc:
        raise _error("bundle_file_missing") from exc
    manifest = ModelRunBundleManifest.from_dict(_strict_json_loads(manifest_bytes))
    if len(manifest.artifact_inventory) != len(_FIXED_INVENTORY_SPECS):
        raise _error("bundle_inventory_invalid")
    for entry, expected in zip(manifest.artifact_inventory, _FIXED_INVENTORY_SPECS, strict=True):
        if (entry.relative_path, entry.artifact_kind, entry.schema_version, entry.input_visibility) != expected:
            raise _error("bundle_inventory_invalid")
    file_bytes: dict[str, bytes] = {}
    for entry in manifest.artifact_inventory:
        try:
            relative_path = _safe_relative_path(entry.relative_path)
            candidate = root.joinpath(*PurePosixPath(relative_path).parts)
            if not candidate.is_file() or candidate.is_symlink():
                raise _error("bundle_file_missing")
            value = candidate.read_bytes()
        except OSError as exc:
            raise _error("bundle_file_missing") from exc
        if len(value) != entry.byte_length or _sha256_bytes(value) != entry.sha256:
            raise _error("bundle_file_integrity_invalid")
        file_bytes[relative_path] = value
    _validate_disk_cross_bindings(manifest, file_bytes)
    return manifest


def _validate_disk_cross_bindings(manifest: ModelRunBundleManifest, files: Mapping[str, bytes]) -> None:
    context_payload = _strict_json_loads(files["artifacts/agent_context.json"])
    context = _context_from_dict(context_payload)
    context_bytes = files["artifacts/agent_context.json"]
    if context.schema_version != AGENT_BUNDLE_SCHEMA_VERSION or _json_artifact(context.to_dict()) != context_bytes:
        raise _error("live_context_invalid")
    _validate_bundle_storage_json_bytes(files["inputs/synthetic_input.bin"])
    _validate_bundle_storage_json_bytes(files["inputs/mock_serializer_config.bin"])
    guidance_payload = _strict_json_loads(files["artifacts/retrieval_guidance.json"])
    if guidance_payload.get("schema_version") != RETRIEVAL_GUIDANCE_SCHEMA_VERSION:
        raise _error("bundle_schema_invalid")
    try:
        rebuilt_guidance = RetrievalGuidanceBuilder().build(context)
        rebuilt_guidance_bytes = _json_artifact(rebuilt_guidance.to_dict())
    except (TypeError, ValueError) as exc:
        raise _error("guidance_context_binding_invalid") from exc
    if rebuilt_guidance_bytes != files["artifacts/retrieval_guidance.json"]:
        raise _error("guidance_context_binding_invalid")
    raw_bytes = files["artifacts/provider_raw_response.bin"]
    try:
        raw = ProviderRawResponse.from_bytes(raw_bytes)
        candidate = parse_provider_raw_response(raw)
    except (TypeError, ValueError) as exc:
        raise _error("assembly_raw_candidate_binding_invalid") from exc
    candidate_payload = _strict_json_loads(files["artifacts/model_semantic_candidate.json"])
    try:
        saved_candidate = ModelSemanticCandidate.from_dict(candidate_payload)
    except (TypeError, ValueError) as exc:
        raise _error("assembly_raw_candidate_binding_invalid") from exc
    if candidate != saved_candidate or candidate.canonical_json_bytes() != files["artifacts/model_semantic_candidate.json"]:
        raise _error("assembly_raw_candidate_binding_invalid")
    report_bytes = files["artifacts/page_spec_assembly_report.json"]
    report_payload = _strict_json_loads(report_bytes)
    report = _report_from_dict(report_payload)
    if report.canonical_json_bytes() != report_bytes:
        raise _error("bundle_provenance_invalid")
    page_spec_bytes = files["artifacts/assembled_page_spec.json"]
    page_spec_payload = _strict_json_loads(page_spec_bytes)
    page_spec = _page_spec_from_dict(page_spec_payload)
    if page_spec.to_dict() != page_spec_payload or _json_artifact(page_spec.to_dict()) != page_spec_bytes:
        raise _error("bundle_page_spec_invalid")
    receipt_bytes = files["artifacts/synthetic_mock_visibility_receipt.json"]
    receipt_payload = _strict_json_loads(receipt_bytes)
    receipt = _receipt_from_dict(receipt_payload)
    if receipt.canonical_json_bytes() != receipt_bytes:
        raise _error("visibility_receipt_invalid")
    audit_bytes = files["artifacts/model_candidate_audit_record.json"]
    audit_payload = _strict_json_loads(audit_bytes)
    audit = _audit_from_dict(audit_payload)
    if audit.canonical_json_bytes() != audit_bytes:
        raise _error("candidate_audit_invalid")
    if (
        receipt.mock_only is not True
        or receipt.external_egress_allowed is not False
        or receipt.is_d17_manifest is not False
        or receipt.is_d17_serializer_output is not False
    ):
        raise _error("visibility_receipt_invalid")
    if (
        report.context_sha256 != _sha256_bytes(context_bytes)
        or report.guidance_sha256 != _sha256_bytes(files["artifacts/retrieval_guidance.json"])
        or report.raw_response_sha256 != raw.sha256
        or report.candidate_sha256 != candidate.sha256()
        or report.assembled_page_spec_sha256 != _sha256_bytes(page_spec_bytes)
    ):
        raise _error("bundle_provenance_invalid")
    if (
        receipt.synthetic_input_sha256 != _sha256_bytes(files["inputs/synthetic_input.bin"])
        or receipt.synthetic_input_byte_length != len(files["inputs/synthetic_input.bin"])
        or receipt.mock_serializer_config_sha256 != _sha256_bytes(files["inputs/mock_serializer_config.bin"])
    ):
        raise _error("bundle_provenance_invalid")
    if (
        audit.raw_response_sha256 != raw.sha256
        or audit.candidate_sha256 != candidate.sha256()
        or audit.assembly_report_id != report.report_id
        or audit.assembly_report_sha256 != report.sha256()
        or audit.assembled_page_spec_sha256 != _sha256_bytes(page_spec_bytes)
        or audit.visibility_receipt_id != receipt.receipt_id
        or audit.visibility_receipt_sha256 != receipt.sha256()
        or audit.synthetic_input_sha256 != _sha256_bytes(files["inputs/synthetic_input.bin"])
        or audit.mock_serializer_config_sha256 != _sha256_bytes(files["inputs/mock_serializer_config.bin"])
    ):
        raise _error("candidate_audit_binding_invalid")
    claims = [{"candidate_entity_stable_id": item.candidate_entity_stable_id, "source_kind": item.source_kind, "source_id": item.source_id} for item in candidate.claimed_attribution_edges]
    decisions = [
        {
            "candidate_entity_stable_id": item.candidate_entity_stable_id,
            "source_kind": item.source_kind,
            "source_id": item.source_id,
        }
        for item in audit.edge_decisions
    ]
    if decisions != claims:
        raise _error("candidate_audit_binding_invalid")
    expected_assembly = {
        "assembly_report_id": report.report_id,
        "field_origins": [item.__dict__ for item in report.field_origins],
        "input_visibility": [item.__dict__ for item in report.input_visibility],
        "claimed_attribution_edges": claims,
        "local_identity_scaffold": report.local_identity_scaffold,
    }
    expected_audit = {
        "record_id": audit.record_id,
        "status": audit.status,
        "accepted_count": audit.accepted_count,
        "rejected_count": audit.rejected_count,
        "edge_decisions": [item.to_dict() for item in audit.edge_decisions],
        "semantic_correctness_not_evaluated": True,
    }
    if dict(manifest.assembly) != expected_assembly or dict(manifest.candidate_audit) != expected_audit:
        raise _error("bundle_provenance_invalid")
    inventory = {item.relative_path: item for item in manifest.artifact_inventory}
    for path, artifact_kind, schema_version, input_visibility in _FIXED_INVENTORY_SPECS:
        entry = inventory[path]
        if (entry.artifact_kind, entry.schema_version, entry.input_visibility) != (artifact_kind, schema_version, input_visibility):
            raise _error("bundle_inventory_invalid")
        root_value = manifest.artifact_roots[artifact_kind]
        if root_value["schema_version"] != schema_version or root_value["sha256"] != entry.sha256:
            raise _error("bundle_provenance_invalid")
    try:
        _validate_downstream_status(manifest.downstream_status)
    except ValueError as exc:
        raise _error("bundle_status_invalid") from exc
