"""Read-only exact G0 element-to-Acceptance trace protocol for Stage 3 M2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import re
from typing import Any

from req2web_acceptance import (
    AcceptanceBindingPlan,
    AcceptancePlan,
    BrowserExecutionReport,
    RequirementView,
)
from req2web_evaluation import (
    AcceptanceEvaluationReport,
    CandidateDecisionSet,
    GoldObligationSet,
    map_acceptance_criteria_to_gold_obligations,
)
from req2web_generation import (
    GuidedPageSpecBuildResult,
    PageSpec,
    RenderResult,
    RetrievalGuidance,
    RetrievalInfluenceReport,
)
from .facts import (
    INSPECTOR_FACT_UNAVAILABLE_SCHEMA_VERSION,
    InspectorFactSet,
    InspectorFactSourceIsolationError,
    InspectorFactUnavailable,
    canonical_sha256,
    project_g0_inspector_facts,
)

INSPECTOR_ELEMENT_ACCEPTANCE_TRACE_SCHEMA_VERSION = "req2web.inspector.element_acceptance_trace.v1"
INSPECTOR_TRACE_UNAVAILABLE_SCHEMA_VERSION = "req2web.inspector.trace_unavailable.v1"
_HOP_STATUSES = frozenset({"verified", "missing", "not_applicable", "ambiguous", "unavailable"})
_RUNTIME_STATUSES = frozenset({"pass", "fail", "unknown", "not_supported"})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class InspectorTraceError(ValueError):
    """Raised when an exact, read-only Inspector trace cannot be verified."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    return f"{prefix}-{sha256(_canonical_bytes(payload)).hexdigest()[:20]}"


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InspectorTraceError(f"{name} must be a non-empty string")


def _hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise InspectorTraceError(f"{name} must be a lowercase SHA-256 hex digest")


def _sorted_strings(values: object, name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise InspectorTraceError(f"{name} must be an immutable tuple")
    if any(not isinstance(item, str) or not item for item in values):
        raise InspectorTraceError(f"{name} must contain non-empty strings")
    if tuple(sorted(values)) != values or len(set(values)) != len(values):
        raise InspectorTraceError(f"{name} must be sorted and unique")
    if not allow_empty and not values:
        raise InspectorTraceError(f"{name} must not be empty")
    return values


def _pairs(values: object, name: str, *, allow_empty: bool = True) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, tuple):
        raise InspectorTraceError(f"{name} must be immutable")
    if any(not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(value, str) and value for value in item) for item in values):
        raise InspectorTraceError(f"{name} must contain non-empty string pairs")
    if tuple(sorted(values)) != values or len(set(values)) != len(values):
        raise InspectorTraceError(f"{name} must be sorted and unique")
    first_keys = tuple(item[0] for item in values)
    if len(first_keys) != len(set(first_keys)):
        raise InspectorTraceError(f"{name} must use unique first keys")
    if not allow_empty and not values:
        raise InspectorTraceError(f"{name} must not be empty")
    return values


def _influence_checks(values: object) -> tuple[tuple[str, str], ...]:
    pairs = _pairs(values, "influence checks", allow_empty=False)
    check_ids = tuple(item[0] for item in pairs)
    if len(check_ids) != len(set(check_ids)):
        raise InspectorTraceError("influence check IDs must be unique")
    if any(status not in {"pass", "fail"} for _, status in pairs):
        raise InspectorTraceError("influence check status must be pass/fail")
    return pairs


@dataclass(frozen=True)
class InspectorSourceUseCaseLink:
    """Exact source-to-canonical-use-case mapping, never inferred from text."""

    source_use_case_link_id: str
    source_ref_id: str
    source_kind: str
    status: str
    use_case_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("source_use_case_link_id", None)
        return value

    def validate(self) -> None:
        for name in ("source_use_case_link_id", "source_ref_id", "source_kind"):
            _text(getattr(self, name), name)
        if self.status not in _HOP_STATUSES:
            raise InspectorTraceError("source use-case link has an unsupported structural status")
        _sorted_strings(self.use_case_ids, "use_case_ids")
        if self.source_kind == "guided_agent_context_fallback":
            if self.status != "not_applicable" or self.use_case_ids:
                raise InspectorTraceError("agent-context fallback must remain canonical context only")
        elif self.source_kind == "guided_retrieval":
            if self.status == "verified" and len(self.use_case_ids) != 1:
                raise InspectorTraceError("verified source mapping requires exactly one use case")
            if self.status == "ambiguous" and len(self.use_case_ids) < 2:
                raise InspectorTraceError("ambiguous source mapping requires multiple use cases")
            if self.status in {"missing", "not_applicable", "unavailable"} and self.use_case_ids:
                raise InspectorTraceError("unverified source mapping must not claim a use case")
        else:
            raise InspectorTraceError("source use-case link has a forbidden source kind")
        if self.source_use_case_link_id != _stable_id("inspector-source-use-case", self.to_payload()):
            raise InspectorTraceError("source_use_case_link_id is not canonical")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class InspectorTraceSource:
    """Read-only G0 source display record retained from InspectorSourceRef."""

    source_ref_id: str
    source_kind: str
    role: str | None
    guidance_id: str | None
    doc_id: str | None

    def validate(self) -> None:
        _text(self.source_ref_id, "source_ref_id")
        if self.source_kind not in {"guided_retrieval", "guided_agent_context_fallback", "retrieval_influence_v1"}:
            raise InspectorFactSourceIsolationError("trace source has a forbidden kind")
        for name in ("role", "guidance_id", "doc_id"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        if self.source_kind == "guided_retrieval" and (not self.role or not self.guidance_id or not self.doc_id):
            raise InspectorTraceError("guided retrieval source requires role/guidance/doc identity")
        if self.source_kind == "guided_agent_context_fallback" and self.role != "context":
            raise InspectorTraceError("agent-context source must retain context role")
        if self.source_kind == "retrieval_influence_v1" and any(value is not None for value in (self.role, self.guidance_id, self.doc_id)):
            raise InspectorTraceError("influence source must not claim role/doc/guidance")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

@dataclass(frozen=True)
class InspectorDecisionObservation:
    """One retained G0 GuidanceDecision disposition, including no-link cases."""

    observation_id: str
    fact_id: str
    decision_id: str
    disposition: str
    source_ref_id: str
    source_use_case_link_id: str
    trace_link_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("observation_id", None)
        return value

    def validate(self) -> None:
        for name in ("observation_id", "fact_id", "decision_id", "disposition", "source_ref_id", "source_use_case_link_id"):
            _text(getattr(self, name), name)
        if self.disposition not in {"adopted", "ignored", "fallback"}:
            raise InspectorTraceError("decision observation has an unsupported disposition")
        _sorted_strings(self.trace_link_ids, "trace_link_ids")
        if self.observation_id != _stable_id("inspector-decision-observation", self.to_payload()):
            raise InspectorTraceError("observation_id is not canonical")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

@dataclass(frozen=True)
class InspectorElementTrace:
    """One exact G0 entity/field link with its exact downstream ID joins."""

    element_trace_id: str
    observation_id: str
    trace_link_id: str
    fact_id: str
    decision_id: str
    source_ref_id: str
    entity_id: str
    field_path: str
    candidate_decision_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("element_trace_id", None)
        return value

    def validate(self) -> None:
        for name in ("element_trace_id", "observation_id", "trace_link_id", "fact_id", "decision_id", "source_ref_id", "entity_id", "field_path"):
            _text(getattr(self, name), name)
        _sorted_strings(self.candidate_decision_ids, "candidate_decision_ids")
        if self.element_trace_id != _stable_id("inspector-element-trace", self.to_payload()):
            raise InspectorTraceError("element_trace_id is not canonical")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class InspectorAlignmentTrace:
    """One exact candidate-decision to alignment/gold-obligation join."""

    alignment_trace_id: str
    element_trace_id: str
    candidate_decision_id: str
    alignment_id: str
    gold_unit_id: str

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("alignment_trace_id", None)
        return value

    def validate(self) -> None:
        for name in ("alignment_trace_id", "element_trace_id", "candidate_decision_id", "alignment_id", "gold_unit_id"):
            _text(getattr(self, name), name)
        if self.alignment_trace_id != _stable_id("inspector-alignment-trace", self.to_payload()):
            raise InspectorTraceError("alignment_trace_id is not canonical")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class InspectorRuntimeStepTrace:
    """Unchanged runtime step status/evidence, joined by frozen step ID."""

    step_id: str
    binding_id: str
    criterion_id: str
    ordinal: int
    action_kind: str
    target_id: str
    selector: str
    source: str
    status: str
    evidence: tuple[tuple[str, str], ...]

    def validate(self) -> None:
        for name in ("step_id", "binding_id", "criterion_id", "action_kind", "target_id", "selector", "source"):
            _text(getattr(self, name), name)
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise InspectorTraceError("runtime ordinal must be a non-negative integer")
        if self.status not in {"pass", "fail", "unknown", "skipped"}:
            raise InspectorTraceError("runtime step status must be pass/fail/unknown/skipped")
        _pairs(self.evidence, "runtime step evidence")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "action_kind": self.action_kind,
            "binding_id": self.binding_id,
            "criterion_id": self.criterion_id,
            "evidence": dict(self.evidence),
            "ordinal": self.ordinal,
            "selector": self.selector,
            "source": self.source,
            "status": self.status,
            "step_id": self.step_id,
            "target_id": self.target_id,
        }


@dataclass(frozen=True)
class InspectorCriterionTrace:
    """Exact alignment/gold/criterion/binding/runtime/requirement chain."""

    criterion_trace_id: str
    alignment_trace_id: str
    element_trace_id: str
    gold_unit_id: str
    criterion_id: str
    binding_id: str
    binding_disposition: str
    binding_terminal_status: str | None
    runtime_status: str
    runtime_evidence: tuple[tuple[str, str], ...]
    runtime_steps: tuple[InspectorRuntimeStepTrace, ...]
    requirement_outcomes: tuple[tuple[str, str], ...]

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("criterion_trace_id", None)
        return value

    def validate(self) -> None:
        for name in ("criterion_trace_id", "alignment_trace_id", "element_trace_id", "gold_unit_id", "criterion_id", "binding_id", "binding_disposition"):
            _text(getattr(self, name), name)
        if self.binding_disposition not in {"bound", "terminal"}:
            raise InspectorTraceError("binding disposition is unsupported")
        if self.binding_disposition == "terminal":
            if self.binding_terminal_status not in {"fail", "not_supported"}:
                raise InspectorTraceError("terminal binding status must retain fail/not_supported")
        elif self.binding_terminal_status is not None:
            raise InspectorTraceError("bound binding cannot carry a terminal status")
        if self.runtime_status not in _RUNTIME_STATUSES:
            raise InspectorTraceError("runtime status must retain D04 values")
        _pairs(self.runtime_evidence, "runtime evidence")
        if not isinstance(self.runtime_steps, tuple):
            raise InspectorTraceError("runtime_steps must be immutable")
        prior = ""
        for step in self.runtime_steps:
            if not isinstance(step, InspectorRuntimeStepTrace):
                raise InspectorTraceError("runtime_steps contains an invalid record")
            step.validate()
            if step.binding_id != self.binding_id or step.criterion_id != self.criterion_id or step.step_id <= prior:
                raise InspectorTraceError("runtime steps are not exact sorted binding members")
            prior = step.step_id
        _pairs(self.requirement_outcomes, "requirement outcomes", allow_empty=False)
        if any(value not in {"met", "partial", "unmet", "unknown"} for _, value in self.requirement_outcomes):
            raise InspectorTraceError("requirement outcome is unsupported")
        if self.criterion_trace_id != _stable_id("inspector-criterion-trace", self.to_payload()):
            raise InspectorTraceError("criterion_trace_id is not canonical")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "alignment_trace_id": self.alignment_trace_id,
            "binding_disposition": self.binding_disposition,
            "binding_id": self.binding_id,
            "binding_terminal_status": self.binding_terminal_status,
            "criterion_id": self.criterion_id,
            "criterion_trace_id": self.criterion_trace_id,
            "element_trace_id": self.element_trace_id,
            "gold_unit_id": self.gold_unit_id,
            "requirement_outcomes": dict(self.requirement_outcomes),
            "runtime_evidence": dict(self.runtime_evidence),
            "runtime_status": self.runtime_status,
            "runtime_steps": [item.to_dict() for item in self.runtime_steps],
        }


@dataclass(frozen=True)
class InspectorAcceptanceObservation:
    """Complete M1 acceptance status retained even when no entity joins it."""

    acceptance_observation_id: str
    gold_unit_id: str
    criterion_id: str
    binding_id: str
    binding_disposition: str
    binding_terminal_status: str | None
    runtime_status: str
    step_ids: tuple[str, ...]
    requirement_outcomes: tuple[tuple[str, str], ...]

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("acceptance_observation_id", None)
        return value

    def validate(self) -> None:
        for name in ("acceptance_observation_id", "gold_unit_id", "criterion_id", "binding_id", "binding_disposition"):
            _text(getattr(self, name), name)
        if self.binding_disposition not in {"bound", "terminal"}:
            raise InspectorTraceError("acceptance observation binding disposition is unsupported")
        if self.binding_disposition == "terminal" and self.binding_terminal_status not in {"fail", "not_supported"}:
            raise InspectorTraceError("terminal acceptance observation must retain fail/not_supported")
        if self.binding_disposition == "bound" and self.binding_terminal_status is not None:
            raise InspectorTraceError("bound acceptance observation cannot carry terminal status")
        if self.runtime_status not in _RUNTIME_STATUSES:
            raise InspectorTraceError("acceptance observation runtime status must retain D04 values")
        _sorted_strings(self.step_ids, "acceptance observation step_ids")
        _pairs(self.requirement_outcomes, "acceptance observation requirement outcomes", allow_empty=False)
        if self.acceptance_observation_id != _stable_id("inspector-acceptance-observation", self.to_payload()):
            raise InspectorTraceError("acceptance_observation_id is not canonical")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "acceptance_observation_id": self.acceptance_observation_id,
            "binding_disposition": self.binding_disposition,
            "binding_id": self.binding_id,
            "binding_terminal_status": self.binding_terminal_status,
            "criterion_id": self.criterion_id,
            "gold_unit_id": self.gold_unit_id,
            "requirement_outcomes": dict(self.requirement_outcomes),
            "runtime_status": self.runtime_status,
            "step_ids": list(self.step_ids),
        }

@dataclass(frozen=True)
class TraceGap:
    """Non-semantic structural missing/ambiguous/unavailable observation."""

    gap_id: str
    owner_id: str
    owner_kind: str
    hop: str
    status: str
    related_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("gap_id", None)
        return value

    def validate(self) -> None:
        for name in ("gap_id", "owner_id", "owner_kind", "hop", "status"):
            _text(getattr(self, name), name)
        if self.owner_kind not in {"decision_observation", "element_trace", "unavailable_trace"}:
            raise InspectorTraceError("TraceGap owner_kind is unsupported")
        if self.hop not in {"source_use_case", "candidate_decision", "alignment", "gold_obligation", "criterion", "binding", "runtime", "requirement_outcome"}:
            raise InspectorTraceError("TraceGap hop is unsupported")
        if self.status not in {"missing", "ambiguous", "unavailable", "not_applicable"}:
            raise InspectorTraceError("TraceGap cannot express semantic or repair claims")
        _sorted_strings(self.related_ids, "TraceGap related_ids")
        if self.gap_id != _stable_id("inspector-trace-gap", self.to_payload()):
            raise InspectorTraceError("gap_id is not canonical")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

@dataclass(frozen=True)
class InspectorElementAcceptanceTraceReport:
    """Canonical G0-only trace report; verified is structural, not semantic."""

    trace_report_id: str
    run_group: str
    case_id: str
    fact_set_id: str
    fact_set_sha256: str
    guidance_bundle_id: str
    page_id: str
    retrieval_influence_report_id: str
    retrieval_influence_report_passed: bool
    influence_checks: tuple[tuple[str, str], ...]
    sources: tuple[InspectorTraceSource, ...]
    source_use_case_links: tuple[InspectorSourceUseCaseLink, ...]
    decision_observations: tuple[InspectorDecisionObservation, ...]
    element_traces: tuple[InspectorElementTrace, ...]
    alignment_traces: tuple[InspectorAlignmentTrace, ...]
    criterion_traces: tuple[InspectorCriterionTrace, ...]
    acceptance_observations: tuple[InspectorAcceptanceObservation, ...]
    gaps: tuple[TraceGap, ...]
    schema_version: str = INSPECTOR_ELEMENT_ACCEPTANCE_TRACE_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("trace_report_id", None)
        return value

    def validate(self) -> None:
        if self.schema_version != INSPECTOR_ELEMENT_ACCEPTANCE_TRACE_SCHEMA_VERSION:
            raise InspectorTraceError("unsupported Inspector element trace schema")
        if self.run_group != "G0":
            raise InspectorFactSourceIsolationError("element trace report is limited to G0")
        for name in ("trace_report_id", "case_id", "fact_set_id", "guidance_bundle_id", "page_id", "retrieval_influence_report_id"):
            _text(getattr(self, name), name)
        _hash(self.fact_set_sha256, "fact_set_sha256")
        if not isinstance(self.retrieval_influence_report_passed, bool):
            raise InspectorTraceError("retrieval_influence_report_passed must be boolean")
        _influence_checks(self.influence_checks)
        if self.retrieval_influence_report_passed != all(status == "pass" for _, status in self.influence_checks):
            raise InspectorTraceError("influence overall status does not match exact check statuses")
        _validate_records(self.sources, InspectorTraceSource, "source_ref_id")
        _validate_records(self.source_use_case_links, InspectorSourceUseCaseLink, "source_use_case_link_id")
        _validate_records(self.decision_observations, InspectorDecisionObservation, "observation_id")
        _validate_records(self.element_traces, InspectorElementTrace, "element_trace_id")
        _validate_records(self.alignment_traces, InspectorAlignmentTrace, "alignment_trace_id")
        _validate_records(self.criterion_traces, InspectorCriterionTrace, "criterion_trace_id")
        _validate_records(self.acceptance_observations, InspectorAcceptanceObservation, "acceptance_observation_id")
        _validate_records(self.gaps, TraceGap, "gap_id")

        sources_by_id = {item.source_ref_id: item for item in self.sources}
        links_by_id = {item.source_use_case_link_id: item for item in self.source_use_case_links}
        observations_by_id = {item.observation_id: item for item in self.decision_observations}
        elements_by_id = {item.element_trace_id: item for item in self.element_traces}
        alignments_by_id = {item.alignment_trace_id: item for item in self.alignment_traces}
        if len({item.fact_id for item in self.decision_observations}) != len(self.decision_observations):
            raise InspectorTraceError("decision observations must use unique fact_id values")
        if len({item.decision_id for item in self.decision_observations}) != len(self.decision_observations):
            raise InspectorTraceError("decision observations must use unique decision_id values")
        non_influence_sources = {
            source_id for source_id, source in sources_by_id.items()
            if source.source_kind != "retrieval_influence_v1"
        }
        if {item.source_ref_id for item in self.source_use_case_links} != non_influence_sources:
            raise InspectorTraceError("every non-influence source must have exactly one source-use-case link")
        for item in self.source_use_case_links:
            source = sources_by_id.get(item.source_ref_id)
            if source is None or source.source_kind != item.source_kind:
                raise InspectorTraceError("source-use-case link does not match its source identity")
        elements_by_observation: dict[str, list[InspectorElementTrace]] = {}
        trace_link_ids: set[str] = set()
        for item in self.element_traces:
            observation = observations_by_id.get(item.observation_id)
            if observation is None:
                raise InspectorTraceError("element trace has an unknown decision observation")
            if (item.fact_id, item.decision_id, item.source_ref_id) != (
                observation.fact_id, observation.decision_id, observation.source_ref_id
            ):
                raise InspectorTraceError("element trace does not match its observation fact/decision/source")
            if item.trace_link_id in trace_link_ids:
                raise InspectorTraceError("element traces must use unique trace_link_id values")
            trace_link_ids.add(item.trace_link_id)
            elements_by_observation.setdefault(item.observation_id, []).append(item)
        for observation in self.decision_observations:
            source = sources_by_id.get(observation.source_ref_id)
            link = links_by_id.get(observation.source_use_case_link_id)
            if source is None or source.source_kind == "retrieval_influence_v1" or link is None or link.source_ref_id != observation.source_ref_id:
                raise InspectorTraceError("decision observation has an inconsistent source/link chain")
            actual_trace_links = tuple(sorted(item.trace_link_id for item in elements_by_observation.get(observation.observation_id, ())))
            if actual_trace_links != observation.trace_link_ids:
                raise InspectorTraceError("observation trace_link_ids do not exactly match element traces")

        alignments_by_element_candidate: dict[tuple[str, str], list[InspectorAlignmentTrace]] = {}
        alignment_keys: set[tuple[str, str, str]] = set()
        for item in self.alignment_traces:
            element = elements_by_id.get(item.element_trace_id)
            if element is None or item.candidate_decision_id not in element.candidate_decision_ids:
                raise InspectorTraceError("alignment trace candidate is not an exact member of its element trace")
            key = (item.element_trace_id, item.candidate_decision_id, item.gold_unit_id)
            if key in alignment_keys:
                raise InspectorTraceError("alignment traces must not duplicate element/candidate/gold joins")
            alignment_keys.add(key)
            alignments_by_element_candidate.setdefault((item.element_trace_id, item.candidate_decision_id), []).append(item)

        gaps_by_owner: dict[tuple[str, str], list[TraceGap]] = {}
        for item in self.gaps:
            if item.owner_kind == "decision_observation" and item.owner_id not in observations_by_id:
                raise InspectorTraceError("decision gap has an unknown owner")
            if item.owner_kind == "element_trace" and item.owner_id not in elements_by_id:
                raise InspectorTraceError("element gap has an unknown owner")
            if item.owner_kind == "unavailable_trace":
                raise InspectorTraceError("unavailable gap is invalid in a G0 report")
            gaps_by_owner.setdefault((item.owner_kind, item.owner_id), []).append(item)
        for observation in self.decision_observations:
            observation_gaps = gaps_by_owner.get(("decision_observation", observation.observation_id), [])
            source_link = links_by_id[observation.source_use_case_link_id]
            expected_source_gap = source_link.status != "verified"
            source_gaps = [item for item in observation_gaps if item.hop == "source_use_case"]
            if expected_source_gap:
                expected_ids = tuple(sorted(set((observation.source_ref_id, *source_link.use_case_ids))))
                if len(source_gaps) != 1 or source_gaps[0].status != source_link.status or source_gaps[0].related_ids != expected_ids:
                    raise InspectorTraceError("source-use-case gap contradicts the exact source link")
            elif source_gaps:
                raise InspectorTraceError("verified source-use-case link cannot carry a gap")
            no_link_gaps = [item for item in observation_gaps if item.hop == "candidate_decision"]
            if not observation.trace_link_ids:
                if len(no_link_gaps) != 1 or no_link_gaps[0].status != "not_applicable" or no_link_gaps[0].related_ids != (observation.decision_id,):
                    raise InspectorTraceError("no-link observation must retain its exact not_applicable candidate gap")
            elif no_link_gaps:
                raise InspectorTraceError("observation with element links cannot carry a decision-level candidate gap")
            if any(item.hop not in {"source_use_case", "candidate_decision"} for item in observation_gaps):
                raise InspectorTraceError("decision observation has an unsupported structural gap")
        for element in self.element_traces:
            element_gaps = gaps_by_owner.get(("element_trace", element.element_trace_id), [])
            candidate_gaps = [item for item in element_gaps if item.hop == "candidate_decision"]
            if element.candidate_decision_ids:
                if candidate_gaps:
                    raise InspectorTraceError("element with candidates cannot claim a candidate-decision gap")
                verified_candidates = {
                    candidate_id
                    for candidate_id in element.candidate_decision_ids
                    if alignments_by_element_candidate.get((element.element_trace_id, candidate_id), [])
                }
                alignment_gaps = [item for item in element_gaps if item.hop == "alignment"]
                if any(item.status != "missing" or len(item.related_ids) != 1 for item in alignment_gaps):
                    raise InspectorTraceError("alignment gaps must be single-candidate missing records")
                gap_candidates = tuple(item.related_ids[0] for item in alignment_gaps)
                if len(gap_candidates) != len(set(gap_candidates)):
                    raise InspectorTraceError("alignment gaps must not duplicate a candidate")
                expected_missing_candidates = set(element.candidate_decision_ids) - verified_candidates
                if set(gap_candidates) != expected_missing_candidates:
                    raise InspectorTraceError("alignment-missing gaps must exactly equal candidates without verified alignments")
            else:
                expected_ids = tuple(sorted((element.entity_id, element.field_path)))
                if len(candidate_gaps) != 1 or candidate_gaps[0].status != "missing" or candidate_gaps[0].related_ids != expected_ids:
                    raise InspectorTraceError("candidate-free element must retain its exact missing-candidate gap")
                if any(item.hop == "alignment" for item in element_gaps):
                    raise InspectorTraceError("candidate-free element cannot carry an alignment gap")
            if any(item.hop not in {"candidate_decision", "alignment"} for item in element_gaps):
                raise InspectorTraceError("validated G0 trace cannot retain unverifiable downstream gaps")

        acceptance_by_key: dict[tuple[str, str, str], InspectorAcceptanceObservation] = {}
        criterion_binding_pairs: set[tuple[str, str]] = set()
        for item in self.acceptance_observations:
            key = (item.criterion_id, item.binding_id, item.gold_unit_id)
            pair = (item.criterion_id, item.binding_id)
            if key in acceptance_by_key or pair in criterion_binding_pairs:
                raise InspectorTraceError("acceptance observations must not duplicate criterion/binding identity")
            acceptance_by_key[key] = item
            criterion_binding_pairs.add(pair)
        criterion_keys: set[tuple[str, str]] = set()
        criteria_by_alignment: dict[str, list[InspectorCriterionTrace]] = {}
        for item in self.criterion_traces:
            alignment = alignments_by_id.get(item.alignment_trace_id)
            if alignment is None or (item.element_trace_id, item.gold_unit_id) != (alignment.element_trace_id, alignment.gold_unit_id):
                raise InspectorTraceError("criterion trace does not match prior alignment element/gold identity")
            key = (item.alignment_trace_id, item.criterion_id)
            if key in criterion_keys:
                raise InspectorTraceError("criterion traces must not duplicate alignment/criterion identity")
            criterion_keys.add(key)
            observed = acceptance_by_key.get((item.criterion_id, item.binding_id, item.gold_unit_id))
            step_ids = tuple(step.step_id for step in item.runtime_steps)
            if observed is None or (
                observed.binding_disposition,
                observed.binding_terminal_status,
                observed.runtime_status,
                observed.step_ids,
                observed.requirement_outcomes,
            ) != (
                item.binding_disposition,
                item.binding_terminal_status,
                item.runtime_status,
                step_ids,
                item.requirement_outcomes,
            ):
                raise InspectorTraceError("criterion trace does not match its exact acceptance observation")
            criteria_by_alignment.setdefault(item.alignment_trace_id, []).append(item)
        if set(criteria_by_alignment) != set(alignments_by_id):
            raise InspectorTraceError("every verified alignment must retain at least one exact criterion trace")
        if self.trace_report_id != _stable_id("inspector-element-acceptance-trace", self.to_payload()):
            raise InspectorTraceError("trace_report_id is not canonical")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "alignment_traces": [item.to_dict() for item in self.alignment_traces],
            "acceptance_observations": [item.to_dict() for item in self.acceptance_observations],
            "case_id": self.case_id,
            "criterion_traces": [item.to_dict() for item in self.criterion_traces],
            "decision_observations": [item.to_dict() for item in self.decision_observations],
            "element_traces": [item.to_dict() for item in self.element_traces],
            "fact_set_id": self.fact_set_id,
            "fact_set_sha256": self.fact_set_sha256,
            "gaps": [item.to_dict() for item in self.gaps],
            "guidance_bundle_id": self.guidance_bundle_id,
            "influence_checks": dict(self.influence_checks),
            "page_id": self.page_id,
            "retrieval_influence_report_id": self.retrieval_influence_report_id,
            "retrieval_influence_report_passed": self.retrieval_influence_report_passed,
            "run_group": self.run_group,
            "schema_version": self.schema_version,
            "source_use_case_links": [item.to_dict() for item in self.source_use_case_links],
            "sources": [item.to_dict() for item in self.sources],
            "trace_report_id": self.trace_report_id,
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


@dataclass(frozen=True)
class InspectorTraceUnavailable:
    """G1/G2 fail-closed trace result without model facts or M1 grafting."""

    trace_unavailable_id: str
    run_group: str
    status: str
    error_code: str
    fact_unavailable_schema_version: str
    fact_unavailable_sha256: str
    missing_artifacts: tuple[str, ...]
    schema_version: str = INSPECTOR_TRACE_UNAVAILABLE_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("trace_unavailable_id", None)
        return value

    def validate(self) -> None:
        if self.schema_version != INSPECTOR_TRACE_UNAVAILABLE_SCHEMA_VERSION:
            raise InspectorTraceError("unsupported Inspector unavailable trace schema")
        if self.run_group not in {"G1", "G2"} or self.status != "fail_closed":
            raise InspectorFactSourceIsolationError("unavailable trace must remain a G1/G2 fail-closed result")
        for name in ("trace_unavailable_id", "error_code", "fact_unavailable_schema_version"):
            _text(getattr(self, name), name)
        if self.fact_unavailable_schema_version != INSPECTOR_FACT_UNAVAILABLE_SCHEMA_VERSION:
            raise InspectorTraceError("unavailable trace has an incompatible fact-unavailable schema")
        _hash(self.fact_unavailable_sha256, "fact_unavailable_sha256")
        _sorted_strings(self.missing_artifacts, "missing_artifacts", allow_empty=False)
        if self.trace_unavailable_id != _stable_id("inspector-trace-unavailable", self.to_payload()):
            raise InspectorTraceError("trace_unavailable_id is not canonical")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def canonical_json_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


def candidate_decision_ids_for_entity(
    candidate_decisions: CandidateDecisionSet,
    page_spec: PageSpec,
    entity_id: str,
) -> tuple[str, ...]:
    """Return exact M1 candidate IDs for one real PageSpec entity ID only."""

    if not isinstance(candidate_decisions, CandidateDecisionSet):
        raise TypeError("candidate_decisions must be CandidateDecisionSet")
    if not isinstance(page_spec, PageSpec):
        raise TypeError("page_spec must be PageSpec")
    if not isinstance(entity_id, str) or not entity_id:
        raise InspectorTraceError("entity_id must be a non-empty string")
    page_spec.validate()
    candidate_decisions.validate_against(page_spec)
    entity_ids = {
        page_spec.page_id,
        *(item.section_id for item in page_spec.sections),
        *(item.component_id for item in page_spec.components),
        *(item.state_id for item in page_spec.states),
        *(item.interaction_id for item in page_spec.interactions),
        *(item.constraint_id for item in page_spec.constraints),
        *(item.check_id for item in page_spec.acceptance_checks),
    }
    if entity_id not in entity_ids:
        raise InspectorTraceError("entity_id does not exist in the supplied PageSpec")
    return tuple(sorted(item.decision_id for item in candidate_decisions.decisions if entity_id in item.candidate_entity_ids))


def build_inspector_element_acceptance_trace(
    inspector_facts: InspectorFactSet | InspectorFactUnavailable,
    *,
    guidance: RetrievalGuidance | None = None,
    guided_build_result: GuidedPageSpecBuildResult | None = None,
    page_spec: PageSpec | None = None,
    retrieval_influence_report: RetrievalInfluenceReport | None = None,
    requirement_view: RequirementView | None = None,
    acceptance_plan: AcceptancePlan | None = None,
    render_result: RenderResult | None = None,
    binding_plan: AcceptanceBindingPlan | None = None,
    browser_report: BrowserExecutionReport | None = None,
    gold_obligations: GoldObligationSet | None = None,
    candidate_decisions: CandidateDecisionSet | None = None,
    acceptance_evaluation_report: AcceptanceEvaluationReport | None = None,
) -> InspectorElementAcceptanceTraceReport | InspectorTraceUnavailable:
    """Build a verified G0 trace, or preserve a structured G1/G2 unavailable state."""

    if isinstance(inspector_facts, InspectorFactUnavailable):
        inspector_facts.validate()
        if any(value is not None for value in (guidance, guided_build_result, page_spec, retrieval_influence_report, requirement_view, acceptance_plan, render_result, binding_plan, browser_report, gold_obligations, candidate_decisions, acceptance_evaluation_report)):
            raise InspectorFactSourceIsolationError("G1/G2 unavailable facts cannot be mixed with G0/M1 artifacts")
        result = InspectorTraceUnavailable(
            trace_unavailable_id="",
            run_group=inspector_facts.run_group,
            status="fail_closed",
            error_code=inspector_facts.error_code,
            fact_unavailable_schema_version=inspector_facts.schema_version,
            fact_unavailable_sha256=canonical_sha256(inspector_facts.to_dict()),
            missing_artifacts=tuple(sorted(inspector_facts.missing_artifacts)),
        )
        result = replace(result, trace_unavailable_id=_stable_id("inspector-trace-unavailable", result.to_payload()))
        result.validate()
        return result
    if not isinstance(inspector_facts, InspectorFactSet):
        raise TypeError("inspector_facts must be InspectorFactSet or InspectorFactUnavailable")
    required = (guidance, guided_build_result, page_spec, retrieval_influence_report, requirement_view, acceptance_plan, render_result, binding_plan, browser_report, gold_obligations, candidate_decisions, acceptance_evaluation_report)
    if any(item is None for item in required):
        raise InspectorTraceError("G0 trace requires every deterministic/M1 identity artifact")
    return _build_g0_trace(inspector_facts, guidance, guided_build_result, page_spec, retrieval_influence_report, requirement_view, acceptance_plan, render_result, binding_plan, browser_report, gold_obligations, candidate_decisions, acceptance_evaluation_report)  # type: ignore[arg-type]


def _build_g0_trace(
    inspector_facts: InspectorFactSet,
    guidance: RetrievalGuidance,
    guided_build_result: GuidedPageSpecBuildResult,
    page_spec: PageSpec,
    retrieval_influence_report: RetrievalInfluenceReport,
    requirement_view: RequirementView,
    acceptance_plan: AcceptancePlan,
    render_result: RenderResult,
    binding_plan: AcceptanceBindingPlan,
    browser_report: BrowserExecutionReport,
    gold_obligations: GoldObligationSet,
    candidate_decisions: CandidateDecisionSet,
    acceptance_evaluation_report: AcceptanceEvaluationReport,
) -> InspectorElementAcceptanceTraceReport:
    inspector_facts.validate()
    if inspector_facts.run_group != "G0":
        raise InspectorFactSourceIsolationError("G0 trace cannot consume another run group")
    # Reprojection prevents accepting a self-reported fact-set ID/hash as proof.
    if inspector_facts != project_g0_inspector_facts(guidance, guided_build_result, page_spec, retrieval_influence_report):
        raise InspectorTraceError("InspectorFactSet does not match supplied G0 artifacts")
    if not all(isinstance(value, expected) for value, expected in ((requirement_view, RequirementView), (acceptance_plan, AcceptancePlan), (render_result, RenderResult), (binding_plan, AcceptanceBindingPlan), (browser_report, BrowserExecutionReport), (gold_obligations, GoldObligationSet), (candidate_decisions, CandidateDecisionSet), (acceptance_evaluation_report, AcceptanceEvaluationReport))):
        raise TypeError("G0 trace received an unsupported M1 artifact type")
    requirement_view.validate()
    acceptance_plan.validate_against(requirement_view)
    binding_plan.validate_against(requirement_view, acceptance_plan, page_spec, render_result)
    browser_report.validate_against(binding_plan)
    gold_obligations.validate()
    candidate_decisions.validate_against(page_spec)
    acceptance_evaluation_report.validate_against(acceptance_plan, binding_plan, browser_report, page_spec, gold_obligations, candidate_decisions)
    if not gold_obligations.case_id == candidate_decisions.case_id == acceptance_evaluation_report.case_id:
        raise InspectorTraceError("evaluation artifacts must use one exact case_id")
    if (inspector_facts.page_id != page_spec.page_id or inspector_facts.guidance_bundle_id != guidance.guidance_bundle_id or inspector_facts.guided_build_result_id != guided_build_result.build_result_id or inspector_facts.retrieval_influence_report_id != retrieval_influence_report.report_id):
        raise InspectorTraceError("InspectorFactSet identity does not match supplied G0 artifacts")

    source_refs = {item.source_ref_id: item for item in inspector_facts.source_refs}
    trace_sources = tuple(sorted((InspectorTraceSource(item.source_ref_id, item.source_kind, item.role, item.guidance_id, item.doc_id) for item in inspector_facts.source_refs), key=lambda item: item.source_ref_id))
    for item in trace_sources:
        item.validate()
    source_links = _source_links(inspector_facts, guidance, page_spec, requirement_view)
    source_link_by_ref = {item.source_ref_id: item for item in source_links}
    trace_links = {item.trace_link_id: item for item in inspector_facts.trace_links}
    alignments_by_decision: dict[str, list[Any]] = {}
    for alignment in acceptance_evaluation_report.alignment_set.alignments:
        for decision_id in alignment.decision_ids:
            alignments_by_decision.setdefault(decision_id, []).append(alignment)
    for value in alignments_by_decision.values():
        value.sort(key=lambda item: item.alignment_id)
    gold_by_id = {item.gold_unit_id: item for item in gold_obligations.obligations}
    criteria_by_gold: dict[str, list[str]] = {}
    for criterion_id, gold_unit_id in map_acceptance_criteria_to_gold_obligations(acceptance_plan, gold_obligations):
        criteria_by_gold.setdefault(gold_unit_id, []).append(criterion_id)
    for value in criteria_by_gold.values():
        value.sort()
    binding_by_criterion = {item.criterion_id: item for item in binding_plan.bindings}
    runtime_by_criterion = {item.criterion_id: item for item in browser_report.criteria}
    runtime_step_by_id = {item.step_id: item for item in browser_report.steps}
    outcome_by_requirement = {item.requirement_id: item for item in acceptance_evaluation_report.requirement_outcomes}

    observations: list[InspectorDecisionObservation] = []
    elements: list[InspectorElementTrace] = []
    alignment_traces: list[InspectorAlignmentTrace] = []
    criterion_traces: list[InspectorCriterionTrace] = []
    gaps: list[TraceGap] = []
    for fact in sorted(inspector_facts.facts, key=lambda item: item.fact_id):
        source_link = source_link_by_ref.get(fact.source_ref_id)
        if source_link is None or fact.source_ref_id not in source_refs:
            raise InspectorTraceError("fact source is missing an exact source mapping")
        observation = _observation(fact, source_link.source_use_case_link_id)
        observations.append(observation)
        if source_link.status != "verified":
            gaps.append(_gap(observation.observation_id, "decision_observation", "source_use_case", source_link.status, (fact.source_ref_id, *source_link.use_case_ids)))
        if not fact.trace_link_ids:
            gaps.append(_gap(observation.observation_id, "decision_observation", "candidate_decision", "not_applicable", (fact.decision_id,)))
        for trace_link_id in fact.trace_link_ids:
            source_trace = trace_links[trace_link_id]
            candidates = candidate_decision_ids_for_entity(candidate_decisions, page_spec, source_trace.entity_id)
            element = _element(observation, source_trace, candidates)
            elements.append(element)
            if not candidates:
                gaps.append(_gap(element.element_trace_id, "element_trace", "candidate_decision", "missing", (element.entity_id, element.field_path)))
                continue
            for candidate_id in candidates:
                alignments = alignments_by_decision.get(candidate_id, ())
                if not alignments:
                    gaps.append(_gap(element.element_trace_id, "element_trace", "alignment", "missing", (candidate_id,)))
                    continue
                for alignment in alignments:
                    gold = gold_by_id.get(alignment.gold_unit_id)
                    if gold is None:
                        gaps.append(_gap(element.element_trace_id, "element_trace", "gold_obligation", "missing", (candidate_id, alignment.alignment_id, alignment.gold_unit_id)))
                        continue
                    alignment_trace = _alignment(element.element_trace_id, candidate_id, alignment.alignment_id, gold.gold_unit_id)
                    alignment_traces.append(alignment_trace)
                    criterion_ids = criteria_by_gold.get(gold.gold_unit_id, ())
                    if not criterion_ids:
                        gaps.append(_gap(element.element_trace_id, "element_trace", "criterion", "missing", (gold.gold_unit_id,)))
                        continue
                    for criterion_id in criterion_ids:
                        binding = binding_by_criterion.get(criterion_id)
                        runtime = runtime_by_criterion.get(criterion_id)
                        if binding is None:
                            gaps.append(_gap(element.element_trace_id, "element_trace", "binding", "missing", (criterion_id,)))
                            continue
                        if runtime is None:
                            gaps.append(_gap(element.element_trace_id, "element_trace", "runtime", "missing", (criterion_id, binding.binding_id)))
                            continue
                        outcomes = tuple(sorted((requirement_id, outcome_by_requirement[requirement_id].outcome) for requirement_id in gold.source_requirement_ids if requirement_id in outcome_by_requirement))
                        if len(outcomes) != len(gold.source_requirement_ids):
                            missing = tuple(requirement_id for requirement_id in gold.source_requirement_ids if requirement_id not in outcome_by_requirement)
                            gaps.append(_gap(element.element_trace_id, "element_trace", "requirement_outcome", "missing", missing))
                            continue
                        steps: list[InspectorRuntimeStepTrace] = []
                        missing_step = False
                        for step_id in runtime.step_ids:
                            step = runtime_step_by_id.get(step_id)
                            if step is None:
                                gaps.append(_gap(element.element_trace_id, "element_trace", "runtime", "missing", (criterion_id, binding.binding_id, step_id)))
                                missing_step = True
                                break
                            steps.append(InspectorRuntimeStepTrace(step.step_id, step.binding_id, step.criterion_id, step.ordinal, step.action_kind, step.target_id, step.selector, step.source, step.status, tuple(step.evidence)))
                        if not missing_step:
                            criterion_traces.append(_criterion(alignment_trace, gold.gold_unit_id, criterion_id, binding, runtime, tuple(sorted(steps, key=lambda item: item.step_id)), outcomes))

    acceptance_observations = _acceptance_observations(gold_by_id, criteria_by_gold, binding_by_criterion, runtime_by_criterion, outcome_by_requirement)

    result = InspectorElementAcceptanceTraceReport(
        trace_report_id="",
        run_group="G0",
        case_id=gold_obligations.case_id,
        fact_set_id=inspector_facts.fact_set_id,
        fact_set_sha256=canonical_sha256(inspector_facts.to_dict()),
        guidance_bundle_id=guidance.guidance_bundle_id,
        page_id=page_spec.page_id,
        retrieval_influence_report_id=retrieval_influence_report.report_id,
        retrieval_influence_report_passed=inspector_facts.retrieval_influence_report_passed,
        influence_checks=tuple(sorted((item.check_id, item.status) for item in inspector_facts.influence_checks)),
        sources=trace_sources,
        source_use_case_links=tuple(sorted(source_links, key=lambda item: item.source_use_case_link_id)),
        decision_observations=tuple(sorted(observations, key=lambda item: item.observation_id)),
        element_traces=tuple(sorted(elements, key=lambda item: item.element_trace_id)),
        alignment_traces=tuple(sorted(alignment_traces, key=lambda item: item.alignment_trace_id)),
        criterion_traces=tuple(sorted(criterion_traces, key=lambda item: item.criterion_trace_id)),
        acceptance_observations=tuple(sorted(acceptance_observations, key=lambda item: item.acceptance_observation_id)),
        gaps=tuple(sorted(gaps, key=lambda item: item.gap_id)),
    )
    result = replace(result, trace_report_id=_stable_id("inspector-element-acceptance-trace", result.to_payload()))
    result.validate()
    return result


def _source_links(
    fact_set: InspectorFactSet,
    guidance: RetrievalGuidance,
    page_spec: PageSpec,
    requirement_view: RequirementView,
) -> tuple[InspectorSourceUseCaseLink, ...]:
    page_use_cases = {item.use_case_id for item in page_spec.use_cases}
    requirement_use_cases = {item.use_case_id for item in requirement_view.use_cases}
    links: list[InspectorSourceUseCaseLink] = []
    for source in sorted(fact_set.source_refs, key=lambda item: item.source_ref_id):
        if source.source_kind == "retrieval_influence_v1":
            continue
        if source.source_kind == "guided_agent_context_fallback":
            value = InspectorSourceUseCaseLink("", source.source_ref_id, source.source_kind, "not_applicable", ())
        elif source.source_kind == "guided_retrieval":
            use_cases = tuple(sorted(trace.use_case_id for trace in guidance.use_case_traces if source.guidance_id in trace.guidance_ids and source.doc_id in trace.source_doc_ids and trace.use_case_id in page_use_cases and trace.use_case_id in requirement_use_cases))
            status = "verified" if len(use_cases) == 1 else "missing" if not use_cases else "ambiguous"
            value = InspectorSourceUseCaseLink("", source.source_ref_id, source.source_kind, status, use_cases)
        else:
            raise InspectorFactSourceIsolationError("G0 trace found a forbidden source kind")
        value = replace(value, source_use_case_link_id=_stable_id("inspector-source-use-case", value.to_payload()))
        value.validate()
        links.append(value)
    return tuple(links)


def _observation(fact: Any, source_use_case_link_id: str) -> InspectorDecisionObservation:
    value = InspectorDecisionObservation("", fact.fact_id, fact.decision_id, fact.disposition, fact.source_ref_id, source_use_case_link_id, tuple(sorted(fact.trace_link_ids)))
    value = replace(value, observation_id=_stable_id("inspector-decision-observation", value.to_payload()))
    value.validate()
    return value


def _element(observation: InspectorDecisionObservation, trace_link: Any, candidate_decision_ids: tuple[str, ...]) -> InspectorElementTrace:
    value = InspectorElementTrace("", observation.observation_id, trace_link.trace_link_id, observation.fact_id, observation.decision_id, observation.source_ref_id, trace_link.entity_id, trace_link.field_path, candidate_decision_ids)
    value = replace(value, element_trace_id=_stable_id("inspector-element-trace", value.to_payload()))
    value.validate()
    return value


def _alignment(element_trace_id: str, candidate_decision_id: str, alignment_id: str, gold_unit_id: str) -> InspectorAlignmentTrace:
    value = InspectorAlignmentTrace("", element_trace_id, candidate_decision_id, alignment_id, gold_unit_id)
    value = replace(value, alignment_trace_id=_stable_id("inspector-alignment-trace", value.to_payload()))
    value.validate()
    return value


def _criterion(
    alignment: InspectorAlignmentTrace,
    gold_unit_id: str,
    criterion_id: str,
    binding: Any,
    runtime: Any,
    steps: tuple[InspectorRuntimeStepTrace, ...],
    outcomes: tuple[tuple[str, str], ...],
) -> InspectorCriterionTrace:
    value = InspectorCriterionTrace("", alignment.alignment_trace_id, alignment.element_trace_id, gold_unit_id, criterion_id, binding.binding_id, binding.disposition, binding.terminal_status, runtime.status, tuple(runtime.evidence), steps, outcomes)
    value = replace(value, criterion_trace_id=_stable_id("inspector-criterion-trace", value.to_payload()))
    value.validate()
    return value


def _gap(owner_id: str, owner_kind: str, hop: str, status: str, related_ids: tuple[str, ...]) -> TraceGap:
    value = TraceGap("", owner_id, owner_kind, hop, status, tuple(sorted(set(related_ids))))
    value = replace(value, gap_id=_stable_id("inspector-trace-gap", value.to_payload()))
    value.validate()
    return value


def _validate_records(values: object, expected_type: type[Any], identifier: str) -> None:
    if not isinstance(values, tuple):
        raise InspectorTraceError(f"{identifier} collection must be immutable")
    prior = ""
    for value in values:
        if not isinstance(value, expected_type):
            raise InspectorTraceError(f"{identifier} collection contains an invalid record")
        value.validate()
        current = getattr(value, identifier)
        if current <= prior:
            raise InspectorTraceError(f"{identifier} collection must be sorted and unique")
        prior = current












def _acceptance_observations(
    gold_by_id: dict[str, Any],
    criteria_by_gold: dict[str, list[str]],
    binding_by_criterion: dict[str, Any],
    runtime_by_criterion: dict[str, Any],
    outcome_by_requirement: dict[str, Any],
) -> tuple[InspectorAcceptanceObservation, ...]:
    records: list[InspectorAcceptanceObservation] = []
    for gold_unit_id, gold in sorted(gold_by_id.items()):
        outcomes = tuple(sorted((requirement_id, outcome_by_requirement[requirement_id].outcome) for requirement_id in gold.source_requirement_ids if requirement_id in outcome_by_requirement))
        if len(outcomes) != len(gold.source_requirement_ids):
            continue
        for criterion_id in criteria_by_gold.get(gold_unit_id, ()):
            binding = binding_by_criterion.get(criterion_id)
            runtime = runtime_by_criterion.get(criterion_id)
            if binding is None or runtime is None:
                continue
            record = InspectorAcceptanceObservation(
                "", gold_unit_id, criterion_id, binding.binding_id,
                binding.disposition, binding.terminal_status, runtime.status,
                tuple(sorted(runtime.step_ids)), outcomes,
            )
            record = replace(record, acceptance_observation_id=_stable_id("inspector-acceptance-observation", record.to_payload()))
            record.validate()
            records.append(record)
    return tuple(records)
