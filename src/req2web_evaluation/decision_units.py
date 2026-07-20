"""Immutable M1 decision units; no metrics or model attribution."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Iterable, Mapping
import unicodedata

from req2web_acceptance.acceptance_plan import ACCEPTANCE_PLAN_SCHEMA_VERSION, AcceptancePlan
from req2web_generation.schema import PAGE_SPEC_SCHEMA_VERSION, PageSpec

DECISION_UNIT_SCHEMA_VERSION = "req2web.evaluation.decision_unit.v1"
_UNIT_TYPES = frozenset({"component_presence", "state_obligation", "interaction_edge", "constraint_obligation", "acceptance_binding"})
_HARDNESS = frozenset({"hard", "soft"})
_EXPECTED_CHANGE = frozenset({"not_applicable", "expected_change", "expected_stable"})
IDENTITY_VALIDATION_STATUSES = frozenset({"no_claim", "validated", "rejected", "mixed"})
_NO_CLAIM_STATUS = "no_claim"
_FROZEN_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    value = _require_text(value, field_name)
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _normalized_text(value: object, field_name: str) -> str:
    value = _require_text(value, field_name)
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if not normalized:
        raise ValueError(f"{field_name} normalizes to empty text")
    return normalized


def _semantic_key(unit_type: str, values: Mapping[str, object]) -> str:
    if unit_type not in _UNIT_TYPES:
        raise ValueError(f"unsupported unit_type: {unit_type}")
    return _canonical_json_bytes({"unit_type": unit_type, **dict(values)}).decode("utf-8")


def _sorted_unique(values: object, field_name: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    if not allow_empty and not values:
        raise ValueError(f"{field_name} must not be empty")
    prior: str | None = None
    result: list[str] = []
    for index, value in enumerate(values):
        value = _require_text(value, f"{field_name}[{index}]")
        if prior is not None and value <= prior:
            raise ValueError(f"{field_name} must be sorted and unique")
        result.append(value)
        prior = value
    return tuple(result)


def _record_id(prefix: str, material: Mapping[str, object]) -> str:
    return prefix + "-" + sha256(_canonical_json_bytes(material)).hexdigest()


def _gold_id(*, case_id: str, unit_type: str, semantic_key: str, source_requirement_ids: tuple[str, ...], hardness: str, expected_change: str, evaluation_rule: str) -> str:
    return _record_id("gold-unit", {"case_id": case_id, "evaluation_rule": evaluation_rule, "expected_change": expected_change, "hardness": hardness, "schema_version": DECISION_UNIT_SCHEMA_VERSION, "semantic_key": semantic_key, "source_requirement_ids": list(source_requirement_ids), "unit_type": unit_type})


def _decision_id(*, case_id: str, unit_type: str, semantic_key: str) -> str:
    return _record_id("candidate-decision", {"case_id": case_id, "schema_version": DECISION_UNIT_SCHEMA_VERSION, "semantic_key": semantic_key, "unit_type": unit_type})


def _alignment_id(*, gold_unit_id: str, decision_ids: tuple[str, ...], match_type: str, evaluation_result: str) -> str:
    return _record_id("decision-alignment", {"decision_ids": list(decision_ids), "evaluation_result": evaluation_result, "gold_unit_id": gold_unit_id, "match_type": match_type, "schema_version": DECISION_UNIT_SCHEMA_VERSION})


def _page_spec_sha256(page_spec: PageSpec) -> str:
    page_spec.validate()
    return sha256(_canonical_json_bytes(page_spec.to_dict())).hexdigest()


@dataclass(frozen=True)
class GoldObligation:
    """Pre-candidate requirement-side record: no PageSpec, runtime, or match fields."""

    schema_version: str
    gold_unit_id: str
    case_id: str
    unit_type: str
    semantic_key: str
    source_requirement_ids: tuple[str, ...]
    hardness: str
    expected_change: str
    evaluation_rule: str

    def validate(self) -> None:
        if self.schema_version != DECISION_UNIT_SCHEMA_VERSION:
            raise ValueError("unsupported gold obligation schema")
        _require_text(self.case_id, "gold.case_id")
        if self.unit_type not in _UNIT_TYPES:
            raise ValueError("gold.unit_type is unsupported")
        _require_text(self.semantic_key, "gold.semantic_key")
        source_ids = _sorted_unique(self.source_requirement_ids, "gold.source_requirement_ids", allow_empty=False)
        if self.hardness not in _HARDNESS or self.expected_change not in _EXPECTED_CHANGE:
            raise ValueError("gold hardness or expected_change is unsupported")
        _require_text(self.evaluation_rule, "gold.evaluation_rule")
        if self.gold_unit_id != _gold_id(case_id=self.case_id, unit_type=self.unit_type, semantic_key=self.semantic_key, source_requirement_ids=source_ids, hardness=self.hardness, expected_change=self.expected_change, evaluation_rule=self.evaluation_rule):
            raise ValueError("gold_unit_id does not match immutable content")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"case_id": self.case_id, "evaluation_rule": self.evaluation_rule, "expected_change": self.expected_change, "gold_unit_id": self.gold_unit_id, "hardness": self.hardness, "schema_version": self.schema_version, "semantic_key": self.semantic_key, "source_requirement_ids": list(self.source_requirement_ids), "unit_type": self.unit_type}


@dataclass(frozen=True)
class CandidateDecision:
    """PageSpec-side record: no gold, match, evaluation, or runtime fields."""

    schema_version: str
    decision_id: str
    case_id: str
    unit_type: str
    semantic_key: str
    candidate_entity_ids: tuple[str, ...]
    attribution_labels: tuple[str, ...]
    attribution_edge_ids: tuple[str, ...]
    identity_validation_status: str

    def validate(self) -> None:
        if self.schema_version != DECISION_UNIT_SCHEMA_VERSION:
            raise ValueError("unsupported candidate decision schema")
        _require_text(self.case_id, "candidate.case_id")
        if self.unit_type not in _UNIT_TYPES:
            raise ValueError("candidate.unit_type is unsupported")
        _require_text(self.semantic_key, "candidate.semantic_key")
        _sorted_unique(self.candidate_entity_ids, "candidate.candidate_entity_ids", allow_empty=False)
        labels = _sorted_unique(self.attribution_labels, "candidate.attribution_labels", allow_empty=True)
        edges = _sorted_unique(self.attribution_edge_ids, "candidate.attribution_edge_ids", allow_empty=True)
        if self.identity_validation_status not in IDENTITY_VALIDATION_STATUSES:
            raise ValueError("candidate.identity_validation_status is unsupported")
        if any(not _FROZEN_LABEL.fullmatch(label) for label in labels):
            raise ValueError("candidate.attribution_labels must be lower_snake_case")
        if self.identity_validation_status == _NO_CLAIM_STATUS:
            if labels or edges:
                raise ValueError("no_claim candidate decisions must not contain attribution fields")
        elif not labels or not edges:
            raise ValueError("audited candidate decisions require labels and attribution edge IDs")
        if self.decision_id != _decision_id(case_id=self.case_id, unit_type=self.unit_type, semantic_key=self.semantic_key):
            raise ValueError("decision_id does not match semantic content")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"attribution_edge_ids": list(self.attribution_edge_ids), "attribution_labels": list(self.attribution_labels), "candidate_entity_ids": list(self.candidate_entity_ids), "case_id": self.case_id, "decision_id": self.decision_id, "identity_validation_status": self.identity_validation_status, "schema_version": self.schema_version, "semantic_key": self.semantic_key, "unit_type": self.unit_type}


@dataclass(frozen=True)
class DecisionAlignment:
    """Explicit future mapping. M1-04a does not infer its labels or results."""

    schema_version: str
    alignment_id: str
    gold_unit_id: str
    decision_ids: tuple[str, ...]
    match_type: str
    evaluation_result: str

    def validate(self) -> None:
        if self.schema_version != DECISION_UNIT_SCHEMA_VERSION:
            raise ValueError("unsupported alignment schema")
        _require_text(self.gold_unit_id, "alignment.gold_unit_id")
        decision_ids = _sorted_unique(self.decision_ids, "alignment.decision_ids", allow_empty=True)
        for field_name, value in (("alignment.match_type", self.match_type), ("alignment.evaluation_result", self.evaluation_result)):
            if not _FROZEN_LABEL.fullmatch(_require_text(value, field_name)):
                raise ValueError(f"{field_name} must be lower_snake_case")
        if self.alignment_id != _alignment_id(gold_unit_id=self.gold_unit_id, decision_ids=decision_ids, match_type=self.match_type, evaluation_result=self.evaluation_result):
            raise ValueError("alignment_id does not match frozen content")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"alignment_id": self.alignment_id, "decision_ids": list(self.decision_ids), "evaluation_result": self.evaluation_result, "gold_unit_id": self.gold_unit_id, "match_type": self.match_type, "schema_version": self.schema_version}

def create_decision_alignment(
    gold_unit_id: str,
    decision_ids: Iterable[str],
    match_type: str,
    evaluation_result: str,
) -> DecisionAlignment:
    """Construct a canonical explicit alignment without inferring its semantics."""
    gold_unit_id = _require_text(gold_unit_id, "alignment.gold_unit_id")
    canonical_ids = tuple(sorted(_require_text(item, "alignment.decision_ids item") for item in decision_ids))
    if len(canonical_ids) != len(set(canonical_ids)):
        raise ValueError("alignment.decision_ids must not contain duplicates")
    match_type = _require_text(match_type, "alignment.match_type")
    evaluation_result = _require_text(evaluation_result, "alignment.evaluation_result")
    record = DecisionAlignment(
        schema_version=DECISION_UNIT_SCHEMA_VERSION,
        alignment_id=_alignment_id(
            gold_unit_id=gold_unit_id,
            decision_ids=canonical_ids,
            match_type=match_type,
            evaluation_result=evaluation_result,
        ),
        gold_unit_id=gold_unit_id,
        decision_ids=canonical_ids,
        match_type=match_type,
        evaluation_result=evaluation_result,
    )
    record.validate()
    return record
@dataclass(frozen=True)
class GoldObligationSet:
    """Case gold with its own source hash; it cannot contain candidate content."""

    schema_version: str
    case_id: str
    source_acceptance_plan_schema_version: str
    source_acceptance_plan_sha256: str
    obligations: tuple[GoldObligation, ...]

    def validate(self) -> None:
        if self.schema_version != DECISION_UNIT_SCHEMA_VERSION:
            raise ValueError("unsupported gold set schema")
        _require_text(self.case_id, "gold_set.case_id")
        if self.source_acceptance_plan_schema_version != ACCEPTANCE_PLAN_SCHEMA_VERSION:
            raise ValueError("gold set has an unsupported AcceptancePlan schema")
        _require_sha256(self.source_acceptance_plan_sha256, "gold_set.source_acceptance_plan_sha256")
        if not isinstance(self.obligations, tuple) or not self.obligations:
            raise ValueError("gold set must contain a non-empty tuple")
        ids: set[str] = set()
        prior: tuple[str, str] | None = None
        for record in self.obligations:
            if not isinstance(record, GoldObligation):
                raise ValueError("gold set contains an invalid record")
            record.validate()
            key = (record.unit_type, record.gold_unit_id)
            if record.case_id != self.case_id or record.gold_unit_id in ids or (prior is not None and key <= prior):
                raise ValueError("gold set identifiers or canonical order are invalid")
            ids.add(record.gold_unit_id)
            prior = key

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"case_id": self.case_id, "obligations": [record.to_dict() for record in self.obligations], "schema_version": self.schema_version, "source_acceptance_plan_schema_version": self.source_acceptance_plan_schema_version, "source_acceptance_plan_sha256": self.source_acceptance_plan_sha256}

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


@dataclass(frozen=True)
class CandidateDecisionSet:
    """Case PageSpec decisions with an independent source and collection hash."""

    schema_version: str
    case_id: str
    source_page_spec_schema_version: str
    source_page_spec_sha256: str
    decisions: tuple[CandidateDecision, ...]

    def validate(self) -> None:
        if self.schema_version != DECISION_UNIT_SCHEMA_VERSION:
            raise ValueError("unsupported candidate set schema")
        _require_text(self.case_id, "candidate_set.case_id")
        if self.source_page_spec_schema_version != PAGE_SPEC_SCHEMA_VERSION:
            raise ValueError("candidate set has an unsupported PageSpec schema")
        _require_sha256(self.source_page_spec_sha256, "candidate_set.source_page_spec_sha256")
        if not isinstance(self.decisions, tuple) or not self.decisions:
            raise ValueError("candidate set must contain a non-empty tuple")
        ids: set[str] = set()
        prior: tuple[str, str] | None = None
        for record in self.decisions:
            if not isinstance(record, CandidateDecision):
                raise ValueError("candidate set contains an invalid record")
            record.validate()
            key = (record.unit_type, record.decision_id)
            if record.case_id != self.case_id or record.decision_id in ids or (prior is not None and key <= prior):
                raise ValueError("candidate set identifiers or canonical order are invalid")
            ids.add(record.decision_id)
            prior = key

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"case_id": self.case_id, "decisions": [record.to_dict() for record in self.decisions], "schema_version": self.schema_version, "source_page_spec_schema_version": self.source_page_spec_schema_version, "source_page_spec_sha256": self.source_page_spec_sha256}

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


@dataclass(frozen=True)
class DecisionAlignmentSet:
    """Explicit mappings with source set hashes, never an independent denominator."""

    schema_version: str
    case_id: str
    source_gold_obligation_set_sha256: str
    source_candidate_decision_set_sha256: str
    alignments: tuple[DecisionAlignment, ...]

    def validate(self) -> None:
        if self.schema_version != DECISION_UNIT_SCHEMA_VERSION:
            raise ValueError("unsupported alignment set schema")
        _require_text(self.case_id, "alignment_set.case_id")
        _require_sha256(self.source_gold_obligation_set_sha256, "alignment_set.source_gold_obligation_set_sha256")
        _require_sha256(self.source_candidate_decision_set_sha256, "alignment_set.source_candidate_decision_set_sha256")
        if not isinstance(self.alignments, tuple):
            raise ValueError("alignment set alignments must be a tuple")
        prior: str | None = None
        for record in self.alignments:
            if not isinstance(record, DecisionAlignment):
                raise ValueError("alignment set contains an invalid record")
            record.validate()
            if prior is not None and record.gold_unit_id <= prior:
                raise ValueError("alignment set must use canonical gold-unit order")
            prior = record.gold_unit_id

    def validate_against(self, gold_obligations: GoldObligationSet, candidate_decisions: CandidateDecisionSet) -> None:
        if not isinstance(gold_obligations, GoldObligationSet) or not isinstance(candidate_decisions, CandidateDecisionSet):
            raise TypeError("alignment validation requires GoldObligationSet and CandidateDecisionSet")
        self.validate()
        gold_obligations.validate()
        candidate_decisions.validate()
        if self.case_id != gold_obligations.case_id or self.case_id != candidate_decisions.case_id or self.source_gold_obligation_set_sha256 != gold_obligations.sha256() or self.source_candidate_decision_set_sha256 != candidate_decisions.sha256():
            raise ValueError("alignment set source identity does not match gold/candidate sets")
        gold_ids = {record.gold_unit_id for record in gold_obligations.obligations}
        candidate_ids = {record.decision_id for record in candidate_decisions.decisions}
        if {record.gold_unit_id for record in self.alignments} != gold_ids:
            raise ValueError("alignment set must retain exactly one record per gold obligation")
        if any(decision_id not in candidate_ids for record in self.alignments for decision_id in record.decision_ids):
            raise ValueError("alignment references an unknown candidate decision")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"alignments": [record.to_dict() for record in self.alignments], "case_id": self.case_id, "schema_version": self.schema_version, "source_candidate_decision_set_sha256": self.source_candidate_decision_set_sha256, "source_gold_obligation_set_sha256": self.source_gold_obligation_set_sha256}

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


@dataclass(frozen=True)
class DecisionUnitBundle:
    """Read-only bundle exposing separate hashes for gold, candidate, alignment."""

    schema_version: str
    gold_obligations: GoldObligationSet
    candidate_decisions: CandidateDecisionSet
    alignments: DecisionAlignmentSet

    def validate(self) -> None:
        if self.schema_version != DECISION_UNIT_SCHEMA_VERSION:
            raise ValueError("unsupported decision-unit bundle schema")
        self.gold_obligations.validate()
        self.candidate_decisions.validate()
        self.alignments.validate_against(self.gold_obligations, self.candidate_decisions)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"alignment_set": self.alignments.to_dict(), "alignment_set_sha256": self.alignments.sha256(), "candidate_decision_set": self.candidate_decisions.to_dict(), "candidate_decision_set_sha256": self.candidate_decisions.sha256(), "gold_obligation_set": self.gold_obligations.to_dict(), "gold_obligation_set_sha256": self.gold_obligations.sha256(), "schema_version": self.schema_version}

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()

def _gold_projection(criterion: object) -> tuple[str, str, str]:
    """Project only pre-candidate AcceptancePlan material into a gold rule."""
    source_kind = getattr(criterion, "source_kind")
    payload = dict(getattr(criterion, "expected_payload"))
    if source_kind == "requirement":
        return "constraint_obligation", _semantic_key("constraint_obligation", {"kind": "requirement_semantics", "requirement": _normalized_text(payload["original_requirement"], "criterion.original_requirement"), "requirement_summary": _normalized_text(payload["requirement_summary"], "criterion.requirement_summary")}), "gold_only_coverage"
    if source_kind == "target_device":
        return "constraint_obligation", _semantic_key("constraint_obligation", {"kind": "target_device", "target_device": _normalized_text(payload["target_device"], "criterion.target_device")}), "gold_only_coverage"
    if source_kind == "constraint":
        return "constraint_obligation", _semantic_key("constraint_obligation", {"kind": "constraint", "constraint": _normalized_text(payload["description"], "criterion.constraint.description")}), "gold_only_coverage"
    if source_kind == "use_case":
        return "acceptance_binding", _semantic_key("acceptance_binding", {"kind": "use_case_outcome", "use_case": {"actor": _normalized_text(payload["actor"], "criterion.use_case.actor"), "expected_outcome": _normalized_text(payload["expected_outcome"], "criterion.use_case.expected_outcome"), "goal": _normalized_text(payload["goal"], "criterion.use_case.goal")}}), "independent_acceptance_binding"
    if source_kind == "validation_signal":
        return "acceptance_binding", _semantic_key("acceptance_binding", {"kind": "validation_signal", "semantic_kind": _normalized_text(getattr(criterion, "semantic_kind"), "criterion.semantic_kind"), "value": _normalized_text(payload["value"], "criterion.validation.value")}), "independent_acceptance_binding"
    raise ValueError(f"unsupported AcceptancePlan source_kind: {source_kind}")


def normalize_gold_obligations(case_id: str, acceptance_plan: AcceptancePlan) -> GoldObligationSet:
    """Create development-only gold from AcceptancePlan only; never read PageSpec/runtime."""
    case_id = _require_text(case_id, "case_id")
    if not isinstance(acceptance_plan, AcceptancePlan):
        raise TypeError("acceptance_plan must be an AcceptancePlan")
    acceptance_plan.validate()
    requirement_id = acceptance_plan.requirement_metadata.requirement_id
    records_by_semantic_key: dict[tuple[str, str], GoldObligation] = {}
    for criterion in acceptance_plan.criteria:
        unit_type, semantic_key, rule = _gold_projection(criterion)
        source_ids = (requirement_id,)
        record = GoldObligation(
            schema_version=DECISION_UNIT_SCHEMA_VERSION,
            gold_unit_id=_gold_id(case_id=case_id, unit_type=unit_type, semantic_key=semantic_key, source_requirement_ids=source_ids, hardness="hard", expected_change="not_applicable", evaluation_rule=rule),
            case_id=case_id,
            unit_type=unit_type,
            semantic_key=semantic_key,
            source_requirement_ids=source_ids,
            hardness="hard",
            expected_change="not_applicable",
            evaluation_rule=rule,
        )
        record.validate()
        key = (unit_type, semantic_key)
        existing = records_by_semantic_key.get(key)
        if existing is not None and existing != record:
            raise ValueError("equivalent gold semantics have inconsistent frozen material")
        records_by_semantic_key[key] = record
    records = sorted(records_by_semantic_key.values(), key=lambda record: (record.unit_type, record.gold_unit_id))
    result = GoldObligationSet(
        schema_version=DECISION_UNIT_SCHEMA_VERSION,
        case_id=case_id,
        source_acceptance_plan_schema_version=acceptance_plan.schema_version,
        source_acceptance_plan_sha256=acceptance_plan.sha256(),
        obligations=tuple(records),
    )
    result.validate()
    return result


def _semantic_use_case(use_case: object) -> dict[str, str]:
    return {"actor": _normalized_text(getattr(use_case, "actor"), "use_case.actor"), "expected_outcome": _normalized_text(getattr(use_case, "expected_outcome"), "use_case.expected_outcome"), "goal": _normalized_text(getattr(use_case, "goal"), "use_case.goal")}


def _semantic_component(component: object) -> dict[str, str]:
    return {"component_type": _normalized_text(getattr(component, "component_type"), "component.component_type"), "purpose": _normalized_text(getattr(component, "purpose"), "component.purpose")}


def _semantic_state(state: object, components: Mapping[str, object]) -> dict[str, object]:
    visible = tuple(sorted(_canonical_json_bytes(_semantic_component(components[component_id])).decode("utf-8") for component_id in getattr(state, "visible_component_ids")))
    return {"description": _normalized_text(getattr(state, "description"), "state.description"), "name": _normalized_text(getattr(state, "name"), "state.name"), "visible_components": visible}


def _add_group(groups: dict[tuple[str, str], set[str]], unit_type: str, semantic_key: str, entity_ids: Iterable[str]) -> None:
    groups.setdefault((unit_type, semantic_key), set()).update(entity_ids)


def normalize_candidate_decisions(case_id: str, page_spec: PageSpec) -> CandidateDecisionSet:
    """Create no-claim candidate decisions from validated PageSpec only.

    Traceability/local scaffolds are validated only as part of PageSpec integrity and
    are never read into attribution labels or edge IDs.
    """
    case_id = _require_text(case_id, "case_id")
    if not isinstance(page_spec, PageSpec):
        raise TypeError("page_spec must be a PageSpec")
    page_spec.validate()
    use_cases = {item.use_case_id: item for item in page_spec.use_cases}
    sections = {item.section_id: item for item in page_spec.sections}
    components = {item.component_id: item for item in page_spec.components}
    states = {item.state_id: item for item in page_spec.states}
    groups: dict[tuple[str, str], set[str]] = {}

    for component in page_spec.components:
        for use_case_id in sections[component.section_id].use_case_ids:
            key = _semantic_key("component_presence", {"component": _semantic_component(component), "use_case": _semantic_use_case(use_cases[use_case_id])})
            _add_group(groups, "component_presence", key, (component.component_id,))
    for state in page_spec.states:
        key = _semantic_key("state_obligation", _semantic_state(state, components))
        _add_group(groups, "state_obligation", key, (state.state_id, *state.visible_component_ids))
    for interaction in page_spec.interactions:
        for use_case_id in interaction.use_case_ids:
            key = _semantic_key("interaction_edge", {"action": _normalized_text(interaction.action, "interaction.action"), "feedback": _normalized_text(interaction.user_feedback, "interaction.user_feedback"), "source_state": _semantic_state(states[interaction.source_state_id], components), "target_state": _semantic_state(states[interaction.target_state_id], components), "trigger_component": _semantic_component(components[interaction.trigger_component_id]), "use_case": _semantic_use_case(use_cases[use_case_id])})
            _add_group(groups, "interaction_edge", key, (interaction.interaction_id, interaction.trigger_component_id, interaction.source_state_id, interaction.target_state_id))
    for constraint in page_spec.constraints:
        key = _semantic_key("constraint_obligation", {"kind": "constraint", "constraint": _normalized_text(constraint.description, "constraint.description")})
        _add_group(groups, "constraint_obligation", key, (constraint.constraint_id,))
    for check in page_spec.acceptance_checks:
        for use_case_id in check.use_case_ids:
            key = _semantic_key("acceptance_binding", {"description": _normalized_text(check.description, "acceptance_check.description"), "kind": "pagespec_acceptance_declaration", "state": _semantic_state(states[check.state_id], components), "use_case": _semantic_use_case(use_cases[use_case_id])})
            _add_group(groups, "acceptance_binding", key, (check.check_id, check.state_id))

    records: list[CandidateDecision] = []
    for (unit_type, key), entity_ids in groups.items():
        entities = tuple(sorted(entity_ids))
        record = CandidateDecision(
            schema_version=DECISION_UNIT_SCHEMA_VERSION,
            decision_id=_decision_id(case_id=case_id, unit_type=unit_type, semantic_key=key),
            case_id=case_id,
            unit_type=unit_type,
            semantic_key=key,
            candidate_entity_ids=entities,
            attribution_labels=(),
            attribution_edge_ids=(),
            identity_validation_status=_NO_CLAIM_STATUS,
        )
        record.validate()
        records.append(record)
    records.sort(key=lambda record: (record.unit_type, record.decision_id))
    result = CandidateDecisionSet(
        schema_version=DECISION_UNIT_SCHEMA_VERSION,
        case_id=case_id,
        source_page_spec_schema_version=page_spec.schema_version,
        source_page_spec_sha256=_page_spec_sha256(page_spec),
        decisions=tuple(records),
    )
    result.validate()
    return result

def freeze_decision_alignments(gold_obligations: GoldObligationSet, candidate_decisions: CandidateDecisionSet, alignments: Iterable[DecisionAlignment]) -> DecisionAlignmentSet:
    """Freeze supplied records without guessing alignment, evaluation, or metrics.

    One explicit record is required for each gold unit. Empty decision_ids preserve
    unmatched gold; unused candidate decisions remain in their own collection.
    """
    if not isinstance(gold_obligations, GoldObligationSet) or not isinstance(candidate_decisions, CandidateDecisionSet):
        raise TypeError("alignment freezing requires GoldObligationSet and CandidateDecisionSet")
    gold_obligations.validate()
    candidate_decisions.validate()
    if gold_obligations.case_id != candidate_decisions.case_id:
        raise ValueError("gold and candidate sets must use the same case_id")
    supplied = tuple(alignments)
    if any(not isinstance(record, DecisionAlignment) for record in supplied):
        raise TypeError("alignments must contain DecisionAlignment records")
    result = DecisionAlignmentSet(
        schema_version=DECISION_UNIT_SCHEMA_VERSION,
        case_id=gold_obligations.case_id,
        source_gold_obligation_set_sha256=gold_obligations.sha256(),
        source_candidate_decision_set_sha256=candidate_decisions.sha256(),
        alignments=tuple(sorted(supplied, key=lambda record: record.gold_unit_id)),
    )
    result.validate_against(gold_obligations, candidate_decisions)
    return result