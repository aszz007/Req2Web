from __future__ import annotations

"""Strict local protocol for the future vendor-neutral PageSpec Provider boundary.

This module is local-only.  It never loads a model, builds a Provider payload,
calls a service, or imports evaluator-only modules.
"""

from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Callable, Iterable, Mapping, TypeVar

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle
from req2web_generation.retrieval_guidance import (
    RETRIEVAL_GUIDANCE_SCHEMA_VERSION,
    GuidanceItem,
    RetrievalGuidance,
)
from req2web_generation.schema import (
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


PROVIDER_RAW_RESPONSE_SCHEMA_VERSION = "req2web.provider.raw_response.v1"
PROVIDER_ERROR_SCHEMA_VERSION = "req2web.provider.error.v1"
MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION = "req2web.provider.semantic_candidate.v1"
PAGE_SPEC_ASSEMBLY_REPORT_SCHEMA_VERSION = "req2web.provider.page_spec_assembly_report.v1"

_CANDIDATE_KEYS = frozenset({
    "schema_version", "title", "layout", "sections", "components", "states",
    "interactions", "constraints", "acceptance_checks", "use_case_mappings",
    "claimed_attribution_edges",
})
_FORBIDDEN_AUTHORITATIVE_FIELDS = frozenset({
    "page_id", "summary", "target_device", "page_type", "use_cases",
    "traceability", "evidence", "evidence_inventory", "source_context_schema_version",
    "agent_context", "retrieval_guidance", "retrieval_influence", "result_package",
})
_STABLE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,95}$")
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_D03_STAGES = frozenset({
    "input_context", "retrieval", "guidance/adoption", "provider_generation",
    "provider_runtime", "page_spec", "render_binding", "browser_runtime",
    "acceptance", "package",
})
_INTERNAL_PHASES = frozenset({"transport", "raw_response", "semantic_candidate", "assembly"})
_PHASE_TO_D03_STAGE = {
    "transport": "provider_runtime",
    "raw_response": "provider_generation",
    "semantic_candidate": "provider_generation",
    "assembly": "page_spec",
}
_CLAIMED_SOURCE_KINDS = frozenset({"requirement", "evidence", "policy"})


class ProviderProtocolError(ValueError):
    """Safe local protocol error with a D03 stage and an internal protocol phase."""

    def __init__(self, code: str, phase: str, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.stage = stage or _PHASE_TO_D03_STAGE[phase]
        self.message = message

    def to_provider_error(self) -> "ProviderError":
        return ProviderError(code=self.code, stage=self.stage, phase=self.phase, retryable=False)


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class ProviderError:
    """Vendor-neutral error envelope without payload, prompt, or secret fields."""

    code: str
    stage: str
    phase: str
    retryable: bool = False
    schema_version: str = PROVIDER_ERROR_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != PROVIDER_ERROR_SCHEMA_VERSION:
            raise ValueError("unsupported ProviderError schema")
        if not isinstance(self.code, str) or not _ERROR_CODE_RE.fullmatch(self.code):
            raise ValueError("ProviderError code is invalid")
        if self.stage not in _D03_STAGES:
            raise ValueError("ProviderError D03 stage is invalid")
        if self.phase not in _INTERNAL_PHASES:
            raise ValueError("ProviderError internal phase is invalid")
        if _PHASE_TO_D03_STAGE[self.phase] != self.stage:
            raise ValueError("ProviderError stage and phase are inconsistent")
        if not isinstance(self.retryable, bool):
            raise ValueError("ProviderError retryable must be boolean")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ProviderRawResponse:
    """Raw bytes are the parser input and the authoritative hash source."""

    raw_bytes: bytes
    schema_version: str = PROVIDER_RAW_RESPONSE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.raw_bytes, bytes) or not self.raw_bytes:
            raise TypeError("ProviderRawResponse requires non-empty bytes")
        if self.schema_version != PROVIDER_RAW_RESPONSE_SCHEMA_VERSION:
            raise ValueError("unsupported ProviderRawResponse schema")

    @classmethod
    def from_bytes(cls, raw_bytes: bytes) -> "ProviderRawResponse":
        return cls(raw_bytes=raw_bytes)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw_bytes).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "byte_sha256": self.sha256,
            "byte_length": len(self.raw_bytes),
        }


@dataclass(frozen=True)
class SemanticLayout:
    pattern: str
    section_stable_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticSection:
    stable_id: str
    title: str
    purpose: str
    component_stable_ids: tuple[str, ...]
    use_case_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticComponent:
    stable_id: str
    section_stable_id: str
    component_type: str
    label: str
    purpose: str


@dataclass(frozen=True)
class SemanticState:
    stable_id: str
    name: str
    description: str
    visible_component_stable_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticInteraction:
    stable_id: str
    trigger_component_stable_id: str
    source_state_stable_id: str
    action: str
    target_state_stable_id: str
    user_feedback: str
    use_case_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticConstraint:
    stable_id: str
    description: str


@dataclass(frozen=True)
class SemanticAcceptanceCheck:
    stable_id: str
    description: str
    use_case_ids: tuple[str, ...]
    state_stable_id: str


@dataclass(frozen=True)
class UseCaseSemanticMapping:
    use_case_id: str
    section_stable_ids: tuple[str, ...]
    component_stable_ids: tuple[str, ...]
    interaction_stable_ids: tuple[str, ...]


@dataclass(frozen=True)
class ClaimedAttributionEdge:
    candidate_entity_stable_id: str
    source_kind: str
    source_id: str

    def validate(self) -> None:
        if not isinstance(self.candidate_entity_stable_id, str) or not _STABLE_ID_RE.fullmatch(self.candidate_entity_stable_id):
            raise ValueError("claimed edge candidate entity stable ID is invalid")
        if self.source_kind not in _CLAIMED_SOURCE_KINDS:
            raise ValueError("claimed edge source kind is invalid")
        if not isinstance(self.source_id, str) or not self.source_id or self.source_id != self.source_id.strip():
            raise ValueError("claimed edge source ID is invalid")


@dataclass(frozen=True)
class ModelSemanticCandidate:
    """Provider-owned semantics with no canonical PageSpec identity/provenance."""

    title: str
    layout: SemanticLayout
    sections: tuple[SemanticSection, ...]
    components: tuple[SemanticComponent, ...]
    states: tuple[SemanticState, ...]
    interactions: tuple[SemanticInteraction, ...]
    constraints: tuple[SemanticConstraint, ...]
    acceptance_checks: tuple[SemanticAcceptanceCheck, ...]
    use_case_mappings: tuple[UseCaseSemanticMapping, ...]
    claimed_attribution_edges: tuple[ClaimedAttributionEdge, ...]
    schema_version: str = MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: object) -> "ModelSemanticCandidate":
        data = _require_object(payload)
        _require_exact_keys(data, _CANDIDATE_KEYS)
        if data["schema_version"] != MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION:
            raise _protocol("unsupported_candidate_schema", "semantic_candidate", "unsupported semantic candidate schema")
        candidate = cls(
            title=_require_text(data["title"]),
            layout=_parse_layout(data["layout"]),
            sections=tuple(_parse_sections(data["sections"])),
            components=tuple(_parse_components(data["components"])),
            states=tuple(_parse_states(data["states"])),
            interactions=tuple(_parse_interactions(data["interactions"])),
            constraints=tuple(_parse_constraints(data["constraints"])),
            acceptance_checks=tuple(_parse_checks(data["acceptance_checks"])),
            use_case_mappings=tuple(_parse_mappings(data["use_case_mappings"])),
            claimed_attribution_edges=tuple(_parse_edges(data["claimed_attribution_edges"])),
        )
        candidate.validate()
        return candidate

    def validate(self) -> None:
        if self.schema_version != MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION:
            raise _protocol("unsupported_candidate_schema", "semantic_candidate", "unsupported semantic candidate schema")
        _require_text(self.title)
        _require_text(self.layout.pattern)
        if not self.sections or not self.components or not self.states or not self.interactions:
            raise _protocol("missing_semantic_relation", "semantic_candidate", "candidate omits required page semantics")
        if not self.acceptance_checks or not 2 <= len(self.use_case_mappings) <= 4:
            raise _protocol("missing_semantic_relation", "semantic_candidate", "candidate omits required semantic relations")

        section_ids = _stable_ids(item.stable_id for item in self.sections)
        component_ids = _stable_ids(item.stable_id for item in self.components)
        state_ids = _stable_ids(item.stable_id for item in self.states)
        interaction_ids = _stable_ids(item.stable_id for item in self.interactions)
        constraint_ids = _stable_ids(item.stable_id for item in self.constraints)
        check_ids = _stable_ids(item.stable_id for item in self.acceptance_checks)
        all_ids = [*section_ids, *component_ids, *state_ids, *interaction_ids, *constraint_ids, *check_ids]
        if len(all_ids) != len(set(all_ids)):
            raise _protocol("stable_id_conflict", "semantic_candidate", "candidate stable IDs must be globally unique")
        _require_exact_reference_set(self.layout.section_stable_ids, set(section_ids))

        sections_by_id = {item.stable_id: item for item in self.sections}
        components_by_id = {item.stable_id: item for item in self.components}
        mapping_by_uc = {item.use_case_id: item for item in self.use_case_mappings}
        if len(mapping_by_uc) != len(self.use_case_mappings):
            raise _protocol("stable_id_conflict", "semantic_candidate", "candidate use-case mappings must be unique")
        for mapping in self.use_case_mappings:
            _require_text(mapping.use_case_id)
            _require_references(mapping.section_stable_ids, set(section_ids))
            _require_references(mapping.component_stable_ids, set(component_ids))
            _require_references(mapping.interaction_stable_ids, set(interaction_ids))

        for section in self.sections:
            _require_text(section.title); _require_text(section.purpose)
            _require_references(section.component_stable_ids, set(component_ids))
            _require_references(section.use_case_ids, set(mapping_by_uc))
            if any(components_by_id[item_id].section_stable_id != section.stable_id for item_id in section.component_stable_ids):
                raise _protocol("stable_id_conflict", "semantic_candidate", "candidate component ownership conflicts with its section")
        for component in self.components:
            _require_text(component.component_type); _require_text(component.label); _require_text(component.purpose)
            if component.section_stable_id not in sections_by_id or component.stable_id not in sections_by_id[component.section_stable_id].component_stable_ids:
                raise _protocol("stable_id_conflict", "semantic_candidate", "candidate component section relation is invalid")
        for state in self.states:
            _require_text(state.name); _require_text(state.description)
            _require_references(state.visible_component_stable_ids, set(component_ids), required=False)
        for interaction in self.interactions:
            _require_references((interaction.trigger_component_stable_id,), set(component_ids))
            # Source and target are independent role references.  A same-state
            # transition is valid for actions such as filtering or refreshing;
            # duplicate-identity checks still apply to actual identity lists.
            _require_references((interaction.source_state_stable_id,), set(state_ids))
            _require_references((interaction.target_state_stable_id,), set(state_ids))
            _require_text(interaction.action); _require_text(interaction.user_feedback)
            _require_references(interaction.use_case_ids, set(mapping_by_uc))
        constraint_descriptions = [_require_text(constraint.description) for constraint in self.constraints]
        if len(constraint_descriptions) != len(set(constraint_descriptions)):
            raise _protocol("stable_id_conflict", "semantic_candidate", "candidate constraint descriptions must be unique")
        for check in self.acceptance_checks:
            if len(_require_text(check.description)) < 8:
                raise _protocol("invalid_candidate_value", "semantic_candidate", "candidate acceptance checks must be self-descriptive")
            _require_references(check.use_case_ids, set(mapping_by_uc))
            _require_references((check.state_stable_id,), set(state_ids))

        _require_exact_reference_set((item.stable_id for item in self.sections), {item_id for mapping in self.use_case_mappings for item_id in mapping.section_stable_ids})
        _require_exact_reference_set((item.stable_id for item in self.components), {item_id for mapping in self.use_case_mappings for item_id in mapping.component_stable_ids})
        _require_exact_reference_set((item.stable_id for item in self.interactions), {item_id for mapping in self.use_case_mappings for item_id in mapping.interaction_stable_ids})
        for use_case_id, mapping in mapping_by_uc.items():
            expected_sections = {item.stable_id for item in self.sections if use_case_id in item.use_case_ids}
            expected_interactions = {item.stable_id for item in self.interactions if use_case_id in item.use_case_ids}
            expected_components = {component_id for section_id in expected_sections for component_id in sections_by_id[section_id].component_stable_ids}
            if set(mapping.section_stable_ids) != expected_sections or set(mapping.component_stable_ids) != expected_components or set(mapping.interaction_stable_ids) != expected_interactions:
                raise _protocol("missing_semantic_relation", "semantic_candidate", "candidate use-case mappings are incomplete")
        if {use_case_id for check in self.acceptance_checks for use_case_id in check.use_case_ids} != set(mapping_by_uc):
            raise _protocol("missing_semantic_relation", "semantic_candidate", "candidate acceptance checks do not cover every use case")
        entities = set(all_ids)
        seen_edges: set[tuple[str, str, str]] = set()
        for edge in self.claimed_attribution_edges:
            _require_references((edge.candidate_entity_stable_id,), entities)
            if edge.source_kind not in _CLAIMED_SOURCE_KINDS:
                raise _protocol("invalid_claimed_source_kind", "semantic_candidate", "candidate claimed edge source kind is invalid")
            _require_text(edge.source_id)
            try:
                edge.validate()
            except ValueError as exc:
                raise _protocol("invalid_candidate_value", "semantic_candidate", "candidate claimed edge is invalid") from exc
            identity = (edge.candidate_entity_stable_id, edge.source_kind, edge.source_id)
            if identity in seen_edges:
                raise _protocol("stable_id_conflict", "semantic_candidate", "candidate claimed edges must be unique")
            seen_edges.add(identity)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "layout": {"pattern": self.layout.pattern, "section_stable_ids": list(self.layout.section_stable_ids)},
            "sections": [{"stable_id": x.stable_id, "title": x.title, "purpose": x.purpose, "component_stable_ids": list(x.component_stable_ids), "use_case_ids": list(x.use_case_ids)} for x in self.sections],
            "components": [{"stable_id": x.stable_id, "section_stable_id": x.section_stable_id, "component_type": x.component_type, "label": x.label, "purpose": x.purpose} for x in self.components],
            "states": [{"stable_id": x.stable_id, "name": x.name, "description": x.description, "visible_component_stable_ids": list(x.visible_component_stable_ids)} for x in self.states],
            "interactions": [{"stable_id": x.stable_id, "trigger_component_stable_id": x.trigger_component_stable_id, "source_state_stable_id": x.source_state_stable_id, "action": x.action, "target_state_stable_id": x.target_state_stable_id, "user_feedback": x.user_feedback, "use_case_ids": list(x.use_case_ids)} for x in self.interactions],
            "constraints": [{"stable_id": x.stable_id, "description": x.description} for x in self.constraints],
            "acceptance_checks": [{"stable_id": x.stable_id, "description": x.description, "use_case_ids": list(x.use_case_ids), "state_stable_id": x.state_stable_id} for x in self.acceptance_checks],
            "use_case_mappings": [{"use_case_id": x.use_case_id, "section_stable_ids": list(x.section_stable_ids), "component_stable_ids": list(x.component_stable_ids), "interaction_stable_ids": list(x.interaction_stable_ids)} for x in self.use_case_mappings],
            "claimed_attribution_edges": [{"candidate_entity_stable_id": x.candidate_entity_stable_id, "source_kind": x.source_kind, "source_id": x.source_id} for x in self.claimed_attribution_edges],
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json_bytes()).hexdigest()


@dataclass(frozen=True)
class FieldOrigin:
    field_path: str
    origin: str
    input_visibility: str
    model_attribution_eligible: bool

    def validate(self) -> None:
        if self.origin not in {"model_semantic_candidate", "local_identity_scaffold"}:
            raise ValueError("unsupported field origin")
        if self.input_visibility not in {"provider_output_only", "local_only"}:
            raise ValueError("unsupported field-origin visibility")
        if not isinstance(self.model_attribution_eligible, bool):
            raise ValueError("field-origin attribution eligibility must be boolean")
        if self.origin == "local_identity_scaffold" and self.model_attribution_eligible:
            raise ValueError("local identity scaffold cannot be model attribution")


@dataclass(frozen=True)
class ArtifactVisibilityRecord:
    artifact_kind: str
    sha256: str
    visibility: str

    def validate(self) -> None:
        if self.artifact_kind not in {"provider_raw_response", "model_semantic_candidate", "agent_context", "retrieval_guidance", "assembled_page_spec"}:
            raise ValueError("unsupported audit artifact kind")
        if not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("audit artifact hash must be SHA-256")
        if self.visibility not in {"provider_output_only", "local_only"}:
            raise ValueError("unsupported artifact visibility")


@dataclass(frozen=True)
class PageSpecAssemblyReport:
    report_id: str
    raw_response_sha256: str
    candidate_sha256: str
    context_sha256: str
    guidance_sha256: str
    assembled_page_spec_sha256: str
    field_origins: tuple[FieldOrigin, ...]
    input_visibility: tuple[ArtifactVisibilityRecord, ...]
    claimed_attribution_edges: tuple[ClaimedAttributionEdge, ...]
    local_identity_scaffold: str = "provenance_only_not_model_attribution"
    schema_version: str = PAGE_SPEC_ASSEMBLY_REPORT_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != PAGE_SPEC_ASSEMBLY_REPORT_SCHEMA_VERSION:
            raise ValueError("unsupported PageSpecAssemblyReport schema")
        for value in (
            self.raw_response_sha256,
            self.candidate_sha256,
            self.context_sha256,
            self.guidance_sha256,
            self.assembled_page_spec_sha256,
        ):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("assembly report hash must be SHA-256")
        if self.local_identity_scaffold != "provenance_only_not_model_attribution":
            raise ValueError("assembly report local scaffold marker is invalid")
        if self.report_id != _assembly_report_id(
            self.raw_response_sha256,
            self.candidate_sha256,
            self.context_sha256,
            self.guidance_sha256,
            self.assembled_page_spec_sha256,
        ):
            raise ValueError("assembly report ID does not bind the artifact provenance root")
        expected_field_origins = {
            "title": ("model_semantic_candidate", "provider_output_only"),
            "layout": ("model_semantic_candidate", "provider_output_only"),
            "sections": ("model_semantic_candidate", "provider_output_only"),
            "components": ("model_semantic_candidate", "provider_output_only"),
            "states": ("model_semantic_candidate", "provider_output_only"),
            "interactions": ("model_semantic_candidate", "provider_output_only"),
            "constraints.model_semantic_candidate": ("model_semantic_candidate", "provider_output_only"),
            "acceptance_checks": ("model_semantic_candidate", "provider_output_only"),
            "page_id": ("local_identity_scaffold", "local_only"),
            "summary": ("local_identity_scaffold", "local_only"),
            "target_device": ("local_identity_scaffold", "local_only"),
            "page_type": ("local_identity_scaffold", "local_only"),
            "use_cases": ("local_identity_scaffold", "local_only"),
            "constraints.agent_context": ("local_identity_scaffold", "local_only"),
            "traceability": ("local_identity_scaffold", "local_only"),
        }
        if len(self.field_origins) != len(expected_field_origins):
            raise ValueError("assembly report field-origin inventory is incomplete or duplicated")
        actual_field_origins = {item.field_path: item for item in self.field_origins}
        if set(actual_field_origins) != set(expected_field_origins):
            raise ValueError("assembly report field-origin inventory is incomplete or duplicated")
        for path, (origin, visibility) in expected_field_origins.items():
            item = actual_field_origins[path]
            item.validate()
            if item.origin != origin or item.input_visibility != visibility:
                raise ValueError("assembly report field-origin binding is invalid")
        expected_artifacts = {
            "provider_raw_response": (self.raw_response_sha256, "provider_output_only"),
            "model_semantic_candidate": (self.candidate_sha256, "provider_output_only"),
            "agent_context": (self.context_sha256, "local_only"),
            "retrieval_guidance": (self.guidance_sha256, "local_only"),
            "assembled_page_spec": (self.assembled_page_spec_sha256, "local_only"),
        }
        if len(self.input_visibility) != len(expected_artifacts):
            raise ValueError("assembly report visibility inventory is incomplete or duplicated")
        actual_artifacts = {item.artifact_kind: item for item in self.input_visibility}
        if set(actual_artifacts) != set(expected_artifacts):
            raise ValueError("assembly report visibility inventory is incomplete or duplicated")
        for artifact_kind, (sha256, visibility) in expected_artifacts.items():
            item = actual_artifacts[artifact_kind]
            item.validate()
            if item.sha256 != sha256 or item.visibility != visibility:
                raise ValueError("assembly report visibility binding is invalid")
        for item in self.claimed_attribution_edges:
            item.validate()
        edge_identities = {
            (item.candidate_entity_stable_id, item.source_kind, item.source_id)
            for item in self.claimed_attribution_edges
        }
        if len(edge_identities) != len(self.claimed_attribution_edges):
            raise ValueError("assembly report claimed edge inventory contains duplicates")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json_bytes()).hexdigest()


@dataclass(frozen=True)
class AssembledPageSpec:
    raw_response: ProviderRawResponse
    candidate: ModelSemanticCandidate
    page_spec: PageSpec
    report: PageSpecAssemblyReport

    def validate(self) -> None:
        self.candidate.validate()
        self.page_spec.validate()
        if self.raw_response.sha256 != self.report.raw_response_sha256:
            raise ValueError("assembly report raw response hash does not match")
        if self.candidate.sha256() != self.report.candidate_sha256:
            raise ValueError("assembly report candidate hash does not match")
        if _sha256_json(self.page_spec.to_dict()) != self.report.assembled_page_spec_sha256:
            raise ValueError("assembly report assembled PageSpec hash does not match")
        if self.report.claimed_attribution_edges != self.candidate.claimed_attribution_edges:
            raise ValueError("assembly report claimed edges do not match the candidate")
        self.report.validate()

def parse_provider_raw_response(raw_response: ProviderRawResponse) -> ModelSemanticCandidate:
    """Parse one strict UTF-8 JSON semantic candidate while retaining raw bytes."""

    if not isinstance(raw_response, ProviderRawResponse):
        raise TypeError("raw response must be ProviderRawResponse")
    try:
        decoded = raw_response.raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _protocol("invalid_utf8", "raw_response", "raw response is not valid UTF-8") from exc
    if decoded.startswith("\ufeff"):
        raise _protocol("non_canonical_json", "raw_response", "raw response must not contain a UTF-8 BOM")
    try:
        payload = json.loads(decoded, object_pairs_hook=_reject_duplicate_pairs, parse_constant=_reject_json_constant)
    except _DuplicateJsonKey as exc:
        raise _protocol("duplicate_json_key", "raw_response", "raw response contains a duplicate JSON key") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise _protocol("invalid_json", "raw_response", "raw response is not a valid JSON object") from exc
    candidate = ModelSemanticCandidate.from_dict(payload)
    return candidate


class CanonicalPageSpecAssembler:
    """Locally bind verified identity/provenance without completing model semantics."""

    def assemble(self, raw_response: ProviderRawResponse, context: AgentContextBundle, guidance: RetrievalGuidance) -> AssembledPageSpec:
        candidate = parse_provider_raw_response(raw_response)
        context_hash, guidance_hash, evidence, evidence_by_role = _validate_local_bindings(context, guidance)
        _validate_candidate_against_context(candidate, context)
        page_spec = _assemble_page_spec(candidate, context, evidence, evidence_by_role)
        try:
            page_spec.validate()
        except ValueError as exc:
            raise _protocol("assembled_pagespec_invalid", "assembly", "assembled PageSpec failed local validation") from exc
        report = _build_report(raw_response, candidate, page_spec, context_hash, guidance_hash)
        result = AssembledPageSpec(raw_response=raw_response, candidate=candidate, page_spec=page_spec, report=report)
        result.validate()
        return result


def _assemble_page_spec(candidate: ModelSemanticCandidate, context: AgentContextBundle, evidence: list[EvidenceReference], evidence_by_role: Mapping[str, list[str]]) -> PageSpec:
    page_id = _canonical_page_id(context.task_type, context.to_dict())
    canonical_constraints = tuple(context.constraints)
    candidate_constraint_descriptions = tuple(item.description for item in candidate.constraints)
    if len(candidate_constraint_descriptions) != len(set(candidate_constraint_descriptions)):
        raise _protocol("stable_id_conflict", "assembly", "candidate constraints contain duplicate descriptions")
    if set(candidate_constraint_descriptions) & set(canonical_constraints):
        raise _protocol("canonical_identity_mismatch", "assembly", "candidate constraints duplicate canonical AgentContext constraints")
    local_constraints = [
        ConstraintSpec(
            constraint_id=(
                f"constraint-context-{index:02d}-"
                f"{hashlib.sha256(description.encode('utf-8')).hexdigest()[:16]}"
            ),
            description=description,
            source="agent_context",
        )
        for index, description in enumerate(canonical_constraints, 1)
    ]
    model_constraints = [
        ConstraintSpec(
            constraint_id=item.stable_id,
            description=item.description,
            source="model_semantic_candidate",
        )
        for item in candidate.constraints
    ]
    constraints = [*model_constraints, *local_constraints]
    if not constraints:
        raise _protocol("missing_semantic_relation", "assembly", "assembled PageSpec requires at least one source-owned constraint")
    candidate_output_ids = {
        *(item.stable_id for item in candidate.sections),
        *(item.stable_id for item in candidate.components),
        *(item.stable_id for item in candidate.states),
        *(item.stable_id for item in candidate.interactions),
        *(item.stable_id for item in candidate.constraints),
        *(item.stable_id for item in candidate.acceptance_checks),
    }
    local_constraint_ids = [item.constraint_id for item in local_constraints]
    if len(local_constraint_ids) != len(set(local_constraint_ids)):
        raise _protocol("canonical_identity_mismatch", "assembly", "canonical local constraint IDs are ambiguous")
    if page_id in candidate_output_ids or set(local_constraint_ids) & candidate_output_ids:
        raise _protocol("canonical_identity_mismatch", "assembly", "canonical local IDs conflict with candidate semantics")

    use_case_ids = [item.use_case_id for item in context.use_cases]
    evidence_assignment: dict[str, list[str]] = defaultdict(list)
    for role in ROLE_ORDER:
        role_doc_ids = evidence_by_role[role]
        for use_case_index, use_case_id in enumerate(use_case_ids):
            evidence_assignment[use_case_id].append(role_doc_ids[use_case_index % len(role_doc_ids)])
        for evidence_index, doc_id in enumerate(role_doc_ids):
            evidence_assignment[use_case_ids[evidence_index % len(use_case_ids)]].append(doc_id)
    mappings = {item.use_case_id: item for item in candidate.use_case_mappings}

    return PageSpec(
        page_id=page_id,
        title=candidate.title,
        summary=context.requirement_summary,
        target_device=context.target_device,
        page_type=context.task_type,
        layout=LayoutSpec(pattern=candidate.layout.pattern, section_order=list(candidate.layout.section_stable_ids)),
        use_cases=[PageUseCase(x.use_case_id, x.title, x.actor, x.goal, x.expected_outcome) for x in context.use_cases],
        sections=[SectionSpec(x.stable_id, x.title, x.purpose, list(x.component_stable_ids), list(x.use_case_ids)) for x in candidate.sections],
        components=[ComponentSpec(x.stable_id, x.section_stable_id, x.component_type, x.label, x.purpose) for x in candidate.components],
        states=[PageState(x.stable_id, x.name, x.description, list(x.visible_component_stable_ids)) for x in candidate.states],
        interactions=[InteractionSpec(x.stable_id, x.trigger_component_stable_id, x.source_state_stable_id, x.action, x.target_state_stable_id, x.user_feedback, list(x.use_case_ids)) for x in candidate.interactions],
        constraints=constraints,
        acceptance_checks=[AcceptanceCheck(x.stable_id, x.description, list(x.use_case_ids), x.state_stable_id) for x in candidate.acceptance_checks],
        traceability=TraceabilitySpec(
            source_context_schema_version=context.schema_version,
            evidence=evidence,
            use_cases=[
                UseCaseTrace(
                    use_case_id=use_case_id,
                    section_ids=list(mappings[use_case_id].section_stable_ids),
                    component_ids=list(mappings[use_case_id].component_stable_ids),
                    interaction_ids=list(mappings[use_case_id].interaction_stable_ids),
                    evidence_doc_ids=_deduplicate(evidence_assignment[use_case_id]),
                )
                for use_case_id in use_case_ids
            ],
        ),
    )


def _validate_local_bindings(context: AgentContextBundle, guidance: RetrievalGuidance) -> tuple[str, str, list[EvidenceReference], dict[str, list[str]]]:
    if not isinstance(context, AgentContextBundle):
        raise TypeError("assembly context must be AgentContextBundle")
    if not isinstance(guidance, RetrievalGuidance):
        raise TypeError("assembly guidance must be RetrievalGuidance")
    try:
        context.validate(); guidance.validate()
    except ValueError as exc:
        raise _protocol("local_binding_invalid", "assembly", "local context or guidance binding is invalid") from exc
    if context.schema_version != AGENT_BUNDLE_SCHEMA_VERSION or guidance.schema_version != RETRIEVAL_GUIDANCE_SCHEMA_VERSION:
        raise _protocol("local_binding_invalid", "assembly", "local context or guidance schema is unsupported")
    if guidance.target_device != context.target_device or guidance.task_type != context.task_type:
        raise _protocol("canonical_identity_mismatch", "assembly", "guidance does not bind to the same local context identity")
    if {item.use_case_id for item in guidance.use_case_traces} != {item.use_case_id for item in context.use_cases}:
        raise _protocol("canonical_identity_mismatch", "assembly", "guidance use-case traces do not match local context identity")

    doc_roles: dict[str, str] = {}
    evidence: list[EvidenceReference] = []
    evidence_by_role: dict[str, list[str]] = {}
    for role in ROLE_ORDER:
        results = context.retrieval_results[role]
        if not results:
            raise _protocol("local_binding_invalid", "assembly", "local context lacks required retrieval evidence")
        role_doc_ids: list[str] = []
        for result in results:
            if not isinstance(result, dict):
                raise _protocol("local_binding_invalid", "assembly", "local retrieval evidence has an invalid shape")
            doc_id, title = result.get("doc_id"), result.get("title")
            if not isinstance(doc_id, str) or not doc_id.strip() or not isinstance(title, str) or not title.strip() or doc_id in doc_roles:
                raise _protocol("local_binding_invalid", "assembly", "local retrieval evidence identity is invalid")
            references = result.get("references", [])
            if not isinstance(references, list):
                raise _protocol("local_binding_invalid", "assembly", "local retrieval reference inventory is invalid")
            uris: list[str] = []
            for reference in references:
                if not isinstance(reference, dict):
                    raise _protocol("local_binding_invalid", "assembly", "local retrieval reference inventory is invalid")
                uri = reference.get("uri")
                if uri is not None:
                    if not isinstance(uri, str) or not uri.strip():
                        raise _protocol("local_binding_invalid", "assembly", "local retrieval reference inventory is invalid")
                    uris.append(uri.strip())
            doc_roles[doc_id] = role
            role_doc_ids.append(doc_id)
            evidence.append(EvidenceReference(role=role, doc_id=doc_id, title=title.strip(), reference_uris=_deduplicate(uris)[:3]))
        evidence_by_role[role] = role_doc_ids
    for item in _all_guidance_items(guidance):
        if doc_roles.get(item.source.doc_id) != item.source.role:
            raise _protocol("canonical_identity_mismatch", "assembly", "guidance source identity does not bind to local evidence")
    for trace in guidance.use_case_traces:
        if any(doc_id not in doc_roles for doc_id in trace.source_doc_ids):
            raise _protocol("canonical_identity_mismatch", "assembly", "guidance trace identity does not bind to local evidence")
    return _sha256_json(context.to_dict()), _sha256_json(guidance.to_dict()), evidence, evidence_by_role


def _validate_candidate_against_context(candidate: ModelSemanticCandidate, context: AgentContextBundle) -> None:
    context_use_case_ids = {item.use_case_id for item in context.use_cases}
    if {item.use_case_id for item in candidate.use_case_mappings} != context_use_case_ids:
        raise _protocol("canonical_identity_mismatch", "assembly", "candidate use-case identity does not match local context")
    used_use_case_ids = {value for item in candidate.sections for value in item.use_case_ids}
    used_use_case_ids |= {value for item in candidate.interactions for value in item.use_case_ids}
    used_use_case_ids |= {value for item in candidate.acceptance_checks for value in item.use_case_ids}
    if used_use_case_ids != context_use_case_ids:
        raise _protocol("canonical_identity_mismatch", "assembly", "candidate semantic relations do not match local canonical use cases")
    # Claimed source IDs are deliberately not whitelisted here.  D02 assigns
    # requirement/evidence/policy allowlists and license/serializer checks to the
    # later model-candidate audit adapter, not to canonical assembly.


def _build_report(raw_response: ProviderRawResponse, candidate: ModelSemanticCandidate, page_spec: PageSpec, context_hash: str, guidance_hash: str) -> PageSpecAssemblyReport:
    page_spec_hash = _sha256_json(page_spec.to_dict())
    field_origins = tuple(
        [FieldOrigin(path, "model_semantic_candidate", "provider_output_only", False) for path in (
            "title", "layout", "sections", "components", "states", "interactions",
            "constraints.model_semantic_candidate", "acceptance_checks",
        )]
        + [FieldOrigin(path, "local_identity_scaffold", "local_only", False) for path in (
            "page_id", "summary", "target_device", "page_type", "use_cases",
            "constraints.agent_context", "traceability",
        )]
    )
    report = PageSpecAssemblyReport(
        report_id=_assembly_report_id(
            raw_response.sha256,
            candidate.sha256(),
            context_hash,
            guidance_hash,
            page_spec_hash,
        ),
        raw_response_sha256=raw_response.sha256,
        candidate_sha256=candidate.sha256(),
        context_sha256=context_hash,
        guidance_sha256=guidance_hash,
        assembled_page_spec_sha256=page_spec_hash,
        field_origins=field_origins,
        input_visibility=(
            ArtifactVisibilityRecord("provider_raw_response", raw_response.sha256, "provider_output_only"),
            ArtifactVisibilityRecord("model_semantic_candidate", candidate.sha256(), "provider_output_only"),
            ArtifactVisibilityRecord("agent_context", context_hash, "local_only"),
            ArtifactVisibilityRecord("retrieval_guidance", guidance_hash, "local_only"),
            ArtifactVisibilityRecord("assembled_page_spec", page_spec_hash, "local_only"),
        ),
        claimed_attribution_edges=candidate.claimed_attribution_edges,
    )
    report.validate()
    return report

def _parse_layout(value: object) -> SemanticLayout:
    data = _require_object(value); _require_exact_keys(data, {"pattern", "section_stable_ids"})
    return SemanticLayout(_require_text(data["pattern"]), tuple(_require_text_list(data["section_stable_ids"], required=True)))


def _parse_sections(value: object) -> list[SemanticSection]:
    return _parse_object_list(value, {"stable_id", "title", "purpose", "component_stable_ids", "use_case_ids"}, lambda x: SemanticSection(
        _require_stable_id(x["stable_id"]), _require_text(x["title"]), _require_text(x["purpose"]),
        tuple(_require_text_list(x["component_stable_ids"], required=True)), tuple(_require_text_list(x["use_case_ids"], required=True)),
    ))


def _parse_components(value: object) -> list[SemanticComponent]:
    return _parse_object_list(value, {"stable_id", "section_stable_id", "component_type", "label", "purpose"}, lambda x: SemanticComponent(
        _require_stable_id(x["stable_id"]), _require_stable_id(x["section_stable_id"]), _require_text(x["component_type"]), _require_text(x["label"]), _require_text(x["purpose"]),
    ))


def _parse_states(value: object) -> list[SemanticState]:
    return _parse_object_list(value, {"stable_id", "name", "description", "visible_component_stable_ids"}, lambda x: SemanticState(
        _require_stable_id(x["stable_id"]), _require_text(x["name"]), _require_text(x["description"]), tuple(_require_text_list(x["visible_component_stable_ids"], required=False)),
    ))


def _parse_interactions(value: object) -> list[SemanticInteraction]:
    return _parse_object_list(value, {"stable_id", "trigger_component_stable_id", "source_state_stable_id", "action", "target_state_stable_id", "user_feedback", "use_case_ids"}, lambda x: SemanticInteraction(
        _require_stable_id(x["stable_id"]), _require_stable_id(x["trigger_component_stable_id"]), _require_stable_id(x["source_state_stable_id"]), _require_text(x["action"]), _require_stable_id(x["target_state_stable_id"]), _require_text(x["user_feedback"]), tuple(_require_text_list(x["use_case_ids"], required=True)),
    ))


def _parse_constraints(value: object) -> list[SemanticConstraint]:
    return _parse_object_list(value, {"stable_id", "description"}, lambda x: SemanticConstraint(_require_stable_id(x["stable_id"]), _require_text(x["description"])))


def _parse_checks(value: object) -> list[SemanticAcceptanceCheck]:
    return _parse_object_list(value, {"stable_id", "description", "use_case_ids", "state_stable_id"}, lambda x: SemanticAcceptanceCheck(
        _require_stable_id(x["stable_id"]), _require_text(x["description"]), tuple(_require_text_list(x["use_case_ids"], required=True)), _require_stable_id(x["state_stable_id"]),
    ))


def _parse_mappings(value: object) -> list[UseCaseSemanticMapping]:
    return _parse_object_list(value, {"use_case_id", "section_stable_ids", "component_stable_ids", "interaction_stable_ids"}, lambda x: UseCaseSemanticMapping(
        _require_text(x["use_case_id"]), tuple(_require_text_list(x["section_stable_ids"], required=True)), tuple(_require_text_list(x["component_stable_ids"], required=True)), tuple(_require_text_list(x["interaction_stable_ids"], required=True)),
    ))


def _parse_edges(value: object) -> list[ClaimedAttributionEdge]:
    return _parse_object_list(value, {"candidate_entity_stable_id", "source_kind", "source_id"}, lambda x: ClaimedAttributionEdge(
        _require_stable_id(x["candidate_entity_stable_id"]), _require_text(x["source_kind"]), _require_text(x["source_id"])
    ))


_T = TypeVar("_T")
def _parse_object_list(value: object, expected_keys: set[str], constructor: Callable[[dict[str, Any]], _T]) -> list[_T]:
    if not isinstance(value, list):
        raise _protocol("candidate_shape_invalid", "semantic_candidate", "candidate collection must be a JSON array")
    result: list[_T] = []
    for item in value:
        data = _require_object(item); _require_exact_keys(data, expected_keys)
        result.append(constructor(data))
    return result


def _require_object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _protocol("candidate_shape_invalid", "semantic_candidate", "candidate contains an invalid object shape")
    return value


def _require_exact_keys(data: Mapping[str, Any], expected: Iterable[str]) -> None:
    expected_set, actual_set = set(expected), set(data)
    extras = actual_set - expected_set
    if extras & _FORBIDDEN_AUTHORITATIVE_FIELDS:
        raise _protocol("forbidden_authoritative_field", "semantic_candidate", "candidate contains a forbidden authoritative field")
    if extras:
        raise _protocol("unknown_candidate_field", "semantic_candidate", "candidate contains an unknown field")
    if actual_set != expected_set:
        raise _protocol("candidate_shape_invalid", "semantic_candidate", "candidate object does not have the required exact key set")


def _require_text(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _protocol("invalid_candidate_value", "semantic_candidate", "candidate contains an invalid string value")
    return value


def _require_stable_id(value: object) -> str:
    item = _require_text(value)
    if not _STABLE_ID_RE.fullmatch(item):
        raise _protocol("invalid_candidate_value", "semantic_candidate", "candidate contains an invalid stable ID")
    return item


def _require_text_list(value: object, *, required: bool) -> list[str]:
    if not isinstance(value, list):
        raise _protocol("candidate_shape_invalid", "semantic_candidate", "candidate relation must be a JSON array")
    items = [_require_text(item) for item in value]
    if required and not items:
        raise _protocol("missing_semantic_relation", "semantic_candidate", "candidate relation must not be empty")
    if len(items) != len(set(items)):
        raise _protocol("stable_id_conflict", "semantic_candidate", "candidate relation contains duplicate identities")
    return items


def _stable_ids(values: Iterable[str]) -> list[str]:
    items = [_require_stable_id(value) for value in values]
    if len(items) != len(set(items)):
        raise _protocol("stable_id_conflict", "semantic_candidate", "candidate stable IDs must be unique")
    return items


def _require_references(values: Iterable[str], valid: set[str], *, required: bool = True) -> tuple[str, ...]:
    items = tuple(values)
    if required and not items:
        raise _protocol("missing_semantic_relation", "semantic_candidate", "candidate relation must not be empty")
    if len(items) != len(set(items)):
        raise _protocol("stable_id_conflict", "semantic_candidate", "candidate relation contains duplicate identities")
    if any(item not in valid for item in items):
        raise _protocol("unknown_candidate_reference", "semantic_candidate", "candidate relation references an unknown semantic identity")
    return items


def _require_exact_reference_set(values: Iterable[str], expected: set[str]) -> None:
    items = tuple(values)
    if set(items) != expected or len(items) != len(set(items)):
        raise _protocol("missing_semantic_relation", "semantic_candidate", "candidate semantic relations are incomplete or inconsistent")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey()
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError("non-finite JSON constant")


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_json(payload: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _deduplicate(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            output.append(value); seen.add(value)
    return output


def _canonical_page_id(task_type: str, context_payload: object) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", task_type.casefold()).strip("-") or "application"
    return f"page-{token}-{_sha256_json(context_payload)[:12]}"


def _assembly_report_id(
    raw_response_sha256: str,
    candidate_sha256: str,
    context_sha256: str,
    guidance_sha256: str,
    assembled_page_spec_sha256: str,
) -> str:
    """Derive an audit identity from the complete D14 provenance root."""

    root_material = {
        "raw_response_sha256": raw_response_sha256,
        "candidate_sha256": candidate_sha256,
        "context_sha256": context_sha256,
        "guidance_sha256": guidance_sha256,
        "assembled_page_spec_sha256": assembled_page_spec_sha256,
    }
    return f"assembly-{_sha256_json(root_material)}"


def _all_guidance_items(guidance: RetrievalGuidance) -> tuple[GuidanceItem, ...]:
    return (
        *guidance.requirement_guidance,
        *guidance.ui_guidance,
        *guidance.interaction_guidance,
        *guidance.implementation_guidance,
        *guidance.validation_guidance,
    )


def _protocol(code: str, phase: str, message: str) -> ProviderProtocolError:
    return ProviderProtocolError(code, phase, message)
