"""Internal M1 attribution evaluation; no Provider, Inspector, or remote execution."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
import re
from typing import Iterable

from req2web_evaluation.decision_units import CandidateDecisionSet, DecisionAlignmentSet, GoldObligationSet
from req2web_generation.schema import PageSpec

ATTRIBUTION_EVALUATION_SCHEMA_VERSION = "req2web.evaluation.attribution_evaluation.v1"
D17_PATHS = frozenset({"path_1", "path_2", "path_3"})
ATTRIBUTION_KINDS = frozenset({"requirement", "evidence", "pre_registered_policy", "local_identity_scaffold"})
EDGE_IDENTITY_STATUSES = frozenset({"validated", "rejected"})
CANDIDATE_CASE_STATUSES = frozenset({"eligible_candidate_present", "invalid_or_ineligible_claims_only", "valid_candidate_no_claims", "no_valid_candidate"})
SOURCE_MODES = frozenset({"valid_candidate", "no_valid_candidate"})
APPLICABILITY_STATUSES = frozenset({"applicable", "not_applicable"})
_RELATION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_LABEL_BY_KIND = {
    "requirement": "requirement_attributed",
    "evidence": "evidence_attributed",
    "pre_registered_policy": "pre_registered_policy_attributed",
    "local_identity_scaffold": "local_identity_scaffold",
}
_COVERAGE_SCOPES = ("all_eligible", "requirement", "evidence", "pre_registered_policy")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    value = _require_text(value, field_name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _require_relation(value: object, field_name: str) -> str:
    value = _require_text(value, field_name)
    if not _RELATION.fullmatch(value):
        raise ValueError(f"{field_name} must be lower_snake_case")
    return value


def _ratio_decimal(numerator: int, denominator: int) -> str:
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        raise ValueError("ratio counts are invalid")
    return str((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _macro_decimal(values: Iterable[str]) -> str:
    decimals = tuple(Decimal(value) for value in values)
    if not decimals:
        raise ValueError("macro average requires at least one defined value")
    return str((sum(decimals) / Decimal(len(decimals))).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _candidate_edge_id(*, case_id: str, decision_id: str, attribution_kind: str, source_id: str, relation: str, provider_visible: bool, pre_registered: bool, identity_validation_status: str) -> str:
    return "candidate-attribution-edge-" + sha256(_canonical_json_bytes({
        "attribution_kind": attribution_kind, "case_id": case_id, "decision_id": decision_id,
        "identity_validation_status": identity_validation_status, "pre_registered": pre_registered,
        "provider_visible": provider_visible, "relation": relation, "source_id": source_id,
    })).hexdigest()


def _gold_edge_id(*, case_id: str, gold_unit_id: str, source_id: str, relation: str) -> str:
    return "evaluator-gold-edge-" + sha256(_canonical_json_bytes({
        "case_id": case_id, "gold_unit_id": gold_unit_id, "relation": relation, "source_id": source_id,
    })).hexdigest()


def _page_spec_sha256(page_spec: PageSpec) -> str:
    page_spec.validate()
    return sha256(_canonical_json_bytes(page_spec.to_dict())).hexdigest()


@dataclass(frozen=True)
class CandidateAttributionEdge:
    """A locally audited candidate claim, not semantic evaluator gold."""

    schema_version: str
    edge_id: str
    case_id: str
    decision_id: str
    attribution_kind: str
    source_id: str
    relation: str
    provider_visible: bool
    pre_registered: bool
    identity_validation_status: str

    def validate(self) -> None:
        if self.schema_version != ATTRIBUTION_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported candidate attribution edge schema")
        _require_text(self.case_id, "candidate_edge.case_id")
        _require_text(self.decision_id, "candidate_edge.decision_id")
        if self.attribution_kind not in ATTRIBUTION_KINDS:
            raise ValueError("candidate_edge.attribution_kind is unsupported")
        _require_text(self.source_id, "candidate_edge.source_id")
        _require_relation(self.relation, "candidate_edge.relation")
        provider_visible = _require_bool(self.provider_visible, "candidate_edge.provider_visible")
        pre_registered = _require_bool(self.pre_registered, "candidate_edge.pre_registered")
        if self.identity_validation_status not in EDGE_IDENTITY_STATUSES:
            raise ValueError("candidate_edge.identity_validation_status is unsupported")
        if self.attribution_kind == "local_identity_scaffold":
            if provider_visible or pre_registered:
                raise ValueError("local_identity_scaffold must remain local-only and non-policy")
        elif self.attribution_kind != "pre_registered_policy" and pre_registered:
            raise ValueError("only pre_registered_policy edges may be pre-registered")
        if self.edge_id != _candidate_edge_id(
            case_id=self.case_id, decision_id=self.decision_id, attribution_kind=self.attribution_kind,
            source_id=self.source_id, relation=self.relation, provider_visible=provider_visible,
            pre_registered=pre_registered, identity_validation_status=self.identity_validation_status,
        ):
            raise ValueError("candidate attribution edge ID does not match immutable content")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "attribution_kind": self.attribution_kind, "case_id": self.case_id, "decision_id": self.decision_id,
            "edge_id": self.edge_id, "identity_validation_status": self.identity_validation_status,
            "pre_registered": self.pre_registered, "provider_visible": self.provider_visible,
            "relation": self.relation, "schema_version": self.schema_version, "source_id": self.source_id,
        }


def create_candidate_attribution_edge(*, case_id: str, decision_id: str, attribution_kind: str, source_id: str, relation: str, provider_visible: bool, pre_registered: bool = False, identity_validation_status: str = "validated") -> CandidateAttributionEdge:
    edge = CandidateAttributionEdge(
        schema_version=ATTRIBUTION_EVALUATION_SCHEMA_VERSION,
        edge_id=_candidate_edge_id(case_id=case_id, decision_id=decision_id, attribution_kind=attribution_kind,
            source_id=source_id, relation=relation, provider_visible=provider_visible,
            pre_registered=pre_registered, identity_validation_status=identity_validation_status),
        case_id=case_id, decision_id=decision_id, attribution_kind=attribution_kind, source_id=source_id,
        relation=relation, provider_visible=provider_visible, pre_registered=pre_registered,
        identity_validation_status=identity_validation_status,
    )
    edge.validate()
    return edge


@dataclass(frozen=True)
class CandidateAttributionEdgeSet:
    """Canonical claim collection bound to one CandidateDecisionSet and D17 path."""

    schema_version: str
    case_id: str
    d17_path: str
    source_candidate_decision_set_sha256: str
    source_page_spec_schema_version: str
    source_page_spec_sha256: str
    edges: tuple[CandidateAttributionEdge, ...]

    def validate(self) -> None:
        if self.schema_version != ATTRIBUTION_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported candidate attribution edge set schema")
        _require_text(self.case_id, "candidate_edge_set.case_id")
        if self.d17_path not in D17_PATHS:
            raise ValueError("candidate_edge_set.d17_path is unsupported")
        _require_sha256(self.source_candidate_decision_set_sha256, "candidate_edge_set.source_candidate_decision_set_sha256")
        _require_text(self.source_page_spec_schema_version, "candidate_edge_set.source_page_spec_schema_version")
        _require_sha256(self.source_page_spec_sha256, "candidate_edge_set.source_page_spec_sha256")
        if not isinstance(self.edges, tuple):
            raise ValueError("candidate_edge_set.edges must be a tuple")
        prior: str | None = None
        for edge in self.edges:
            if not isinstance(edge, CandidateAttributionEdge):
                raise ValueError("candidate_edge_set contains an invalid edge")
            edge.validate()
            if edge.case_id != self.case_id or (prior is not None and edge.edge_id <= prior):
                raise ValueError("candidate_edge_set case IDs or canonical edge order are invalid")
            prior = edge.edge_id

    def validate_against(self, candidate_decisions: CandidateDecisionSet) -> None:
        if not isinstance(candidate_decisions, CandidateDecisionSet):
            raise TypeError("candidate edge validation requires CandidateDecisionSet")
        self.validate()
        candidate_decisions.validate()
        if (self.case_id != candidate_decisions.case_id
                or self.source_candidate_decision_set_sha256 != candidate_decisions.sha256()
                or self.source_page_spec_schema_version != candidate_decisions.source_page_spec_schema_version
                or self.source_page_spec_sha256 != candidate_decisions.source_page_spec_sha256):
            raise ValueError("candidate edge set source identity does not match candidate decisions")
        decisions = {record.decision_id: record for record in candidate_decisions.decisions}
        by_decision: dict[str, list[CandidateAttributionEdge]] = {decision_id: [] for decision_id in decisions}
        for edge in self.edges:
            if edge.decision_id not in decisions:
                raise ValueError("candidate attribution edge references an unknown decision")
            by_decision[edge.decision_id].append(edge)
        for decision_id, decision in decisions.items():
            decision_edges = by_decision[decision_id]
            if tuple(sorted(edge.edge_id for edge in decision_edges)) != decision.attribution_edge_ids:
                raise ValueError("candidate attribution edge IDs do not match the decision record")
            if tuple(sorted({_LABEL_BY_KIND[edge.attribution_kind] for edge in decision_edges})) != decision.attribution_labels:
                raise ValueError("candidate attribution labels do not match the decision record")
            statuses = {edge.identity_validation_status for edge in decision_edges}
            if decision.identity_validation_status == "no_claim" and decision_edges:
                raise ValueError("no_claim decision must not have candidate attribution edges")
            if decision.identity_validation_status == "validated" and statuses != {"validated"}:
                raise ValueError("validated decision requires only validated candidate edges")
            if decision.identity_validation_status == "rejected" and statuses != {"rejected"}:
                raise ValueError("rejected decision requires only rejected candidate edges")
            if decision.identity_validation_status == "mixed" and statuses != {"validated", "rejected"}:
                raise ValueError("mixed decision requires both validated and rejected candidate edges")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "case_id": self.case_id, "d17_path": self.d17_path,
            "edges": [edge.to_dict() for edge in self.edges], "schema_version": self.schema_version,
            "source_candidate_decision_set_sha256": self.source_candidate_decision_set_sha256,
            "source_page_spec_schema_version": self.source_page_spec_schema_version,
            "source_page_spec_sha256": self.source_page_spec_sha256,
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


def freeze_candidate_attribution_edges(*, candidate_decisions: CandidateDecisionSet, d17_path: str, edges: Iterable[CandidateAttributionEdge]) -> CandidateAttributionEdgeSet:
    if not isinstance(candidate_decisions, CandidateDecisionSet):
        raise TypeError("candidate attribution edge freezing requires CandidateDecisionSet")
    candidate_decisions.validate()
    result = CandidateAttributionEdgeSet(
        schema_version=ATTRIBUTION_EVALUATION_SCHEMA_VERSION, case_id=candidate_decisions.case_id,
        d17_path=d17_path, source_candidate_decision_set_sha256=candidate_decisions.sha256(),
        source_page_spec_schema_version=candidate_decisions.source_page_spec_schema_version,
        source_page_spec_sha256=candidate_decisions.source_page_spec_sha256,
        edges=tuple(sorted(tuple(edges), key=lambda edge: edge.edge_id)),
    )
    result.validate_against(candidate_decisions)
    return result

@dataclass(frozen=True)
class EvaluatorOnlyGoldEdge:
    """Pre-candidate deep-label evidence edge; no Provider claim or runtime fields."""

    schema_version: str
    gold_edge_id: str
    case_id: str
    gold_unit_id: str
    source_id: str
    relation: str

    def validate(self) -> None:
        if self.schema_version != ATTRIBUTION_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported evaluator-only gold edge schema")
        _require_text(self.case_id, "gold_edge.case_id")
        _require_text(self.gold_unit_id, "gold_edge.gold_unit_id")
        _require_text(self.source_id, "gold_edge.source_id")
        _require_relation(self.relation, "gold_edge.relation")
        if self.gold_edge_id != _gold_edge_id(case_id=self.case_id, gold_unit_id=self.gold_unit_id,
                                               source_id=self.source_id, relation=self.relation):
            raise ValueError("evaluator-only gold edge ID does not match immutable content")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "case_id": self.case_id, "gold_edge_id": self.gold_edge_id, "gold_unit_id": self.gold_unit_id,
            "relation": self.relation, "schema_version": self.schema_version, "source_id": self.source_id,
        }


def create_evaluator_only_gold_edge(*, case_id: str, gold_unit_id: str, source_id: str, relation: str) -> EvaluatorOnlyGoldEdge:
    edge = EvaluatorOnlyGoldEdge(
        schema_version=ATTRIBUTION_EVALUATION_SCHEMA_VERSION,
        gold_edge_id=_gold_edge_id(case_id=case_id, gold_unit_id=gold_unit_id, source_id=source_id, relation=relation),
        case_id=case_id, gold_unit_id=gold_unit_id, source_id=source_id, relation=relation,
    )
    edge.validate()
    return edge


@dataclass(frozen=True)
class EvaluatorOnlyGoldEdgeSet:
    """Frozen deep-label fixture isolated from Provider claims and candidate content."""

    schema_version: str
    case_id: str
    source_gold_obligation_set_sha256: str
    edges: tuple[EvaluatorOnlyGoldEdge, ...]

    def validate(self) -> None:
        if self.schema_version != ATTRIBUTION_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported evaluator-only gold edge set schema")
        _require_text(self.case_id, "gold_edge_set.case_id")
        _require_sha256(self.source_gold_obligation_set_sha256, "gold_edge_set.source_gold_obligation_set_sha256")
        if not isinstance(self.edges, tuple) or not self.edges:
            raise ValueError("evaluator-only gold edge set must contain a non-empty tuple")
        prior: str | None = None
        for edge in self.edges:
            if not isinstance(edge, EvaluatorOnlyGoldEdge):
                raise ValueError("evaluator-only gold edge set contains an invalid edge")
            edge.validate()
            if edge.case_id != self.case_id or (prior is not None and edge.gold_edge_id <= prior):
                raise ValueError("evaluator-only gold edge set case IDs or canonical edge order are invalid")
            prior = edge.gold_edge_id

    def validate_against(self, gold_obligations: GoldObligationSet) -> None:
        if not isinstance(gold_obligations, GoldObligationSet):
            raise TypeError("gold edge validation requires GoldObligationSet")
        self.validate()
        gold_obligations.validate()
        if self.case_id != gold_obligations.case_id or self.source_gold_obligation_set_sha256 != gold_obligations.sha256():
            raise ValueError("evaluator-only gold edge set source identity does not match gold obligations")
        gold_ids = {record.gold_unit_id for record in gold_obligations.obligations}
        if any(edge.gold_unit_id not in gold_ids for edge in self.edges):
            raise ValueError("evaluator-only gold edge references an unknown gold obligation")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "case_id": self.case_id, "edges": [edge.to_dict() for edge in self.edges],
            "schema_version": self.schema_version,
            "source_gold_obligation_set_sha256": self.source_gold_obligation_set_sha256,
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


def freeze_evaluator_only_gold_edges(*, gold_obligations: GoldObligationSet, edges: Iterable[EvaluatorOnlyGoldEdge]) -> EvaluatorOnlyGoldEdgeSet:
    if not isinstance(gold_obligations, GoldObligationSet):
        raise TypeError("evaluator-only gold edge freezing requires GoldObligationSet")
    gold_obligations.validate()
    result = EvaluatorOnlyGoldEdgeSet(
        schema_version=ATTRIBUTION_EVALUATION_SCHEMA_VERSION, case_id=gold_obligations.case_id,
        source_gold_obligation_set_sha256=gold_obligations.sha256(),
        edges=tuple(sorted(tuple(edges), key=lambda edge: edge.gold_edge_id)),
    )
    result.validate_against(gold_obligations)
    return result


@dataclass(frozen=True)
class AttributionCoverageMetric:
    """Candidate-decision denominator metric, never an alignment denominator."""

    metric_name: str
    attribution_scope: str
    applicability: str
    not_applicable_reason: str | None
    numerator: int
    denominator: int
    decimal_value: str
    forced_zero_reason: str | None = None

    def validate(self) -> None:
        if self.metric_name != "candidate_attribution_coverage" or self.attribution_scope not in _COVERAGE_SCOPES:
            raise ValueError("unsupported attribution coverage metric")
        if self.applicability not in APPLICABILITY_STATUSES:
            raise ValueError("coverage applicability is unsupported")
        if not isinstance(self.numerator, int) or not isinstance(self.denominator, int):
            raise ValueError("coverage counts must be integers")
        if self.applicability == "not_applicable":
            if (self.numerator != 0 or self.denominator != 0 or self.decimal_value != "not_applicable"
                    or self.forced_zero_reason is not None):
                raise ValueError("not-applicable coverage metric must not contain counts, ratio, or forced-zero reason")
            _require_text(self.not_applicable_reason, "coverage.not_applicable_reason")
            return
        if self.not_applicable_reason is not None:
            raise ValueError("applicable coverage metric must not contain a not-applicable reason")
        if self.denominator == 0:
            if (self.numerator != 0 or self.decimal_value != "0.000000"
                    or not isinstance(self.forced_zero_reason, str) or not self.forced_zero_reason.strip()):
                raise ValueError("zero-denominator coverage metric requires an explicit forced-zero reason")
            return
        if self.denominator < 0 or self.forced_zero_reason is not None:
            raise ValueError("positive-denominator coverage metric has invalid forced-zero fields")
        if self.decimal_value != _ratio_decimal(self.numerator, self.denominator):
            raise ValueError("coverage ratio does not match its integer counts")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "applicability": self.applicability, "attribution_scope": self.attribution_scope,
            "decimal_value": self.decimal_value, "denominator": self.denominator,
            "forced_zero_reason": self.forced_zero_reason, "metric_name": self.metric_name,
            "not_applicable_reason": self.not_applicable_reason, "numerator": self.numerator,
        }

@dataclass(frozen=True)
class DeepLabelAttributionCorrectness:
    """Evaluator-only evidence correctness; no Provider or scaffold self-certification."""

    applicability: str
    not_applicable_reason: str | None
    true_positive_count: int
    false_positive_count: int
    false_negative_count: int
    precision: str
    recall: str

    def validate(self) -> None:
        if self.applicability not in APPLICABILITY_STATUSES:
            raise ValueError("deep-label applicability is unsupported")
        values = (self.true_positive_count, self.false_positive_count, self.false_negative_count)
        if any(not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("deep-label counts must be non-negative integers")
        if self.applicability == "not_applicable":
            if values != (0, 0, 0) or self.precision != "not_applicable" or self.recall != "not_applicable":
                raise ValueError("not-applicable deep-label result must not contain counts or ratios")
            _require_text(self.not_applicable_reason, "deep_label.not_applicable_reason")
            return
        if self.not_applicable_reason is not None:
            raise ValueError("applicable deep-label result must not contain a not-applicable reason")
        precision_denominator = self.true_positive_count + self.false_positive_count
        expected_precision = "undefined" if precision_denominator == 0 else _ratio_decimal(self.true_positive_count, precision_denominator)
        recall_denominator = self.true_positive_count + self.false_negative_count
        if recall_denominator <= 0:
            raise ValueError("applicable deep-label result requires evaluator-only gold")
        if self.precision != expected_precision or self.recall != _ratio_decimal(self.true_positive_count, recall_denominator):
            raise ValueError("deep-label ratios do not match their integer counts")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "applicability": self.applicability, "false_negative_count": self.false_negative_count,
            "false_positive_count": self.false_positive_count, "not_applicable_reason": self.not_applicable_reason,
            "precision": self.precision, "recall": self.recall, "true_positive_count": self.true_positive_count,
        }


def _edge_is_eligible(edge: CandidateAttributionEdge, d17_path: str) -> bool:
    if edge.identity_validation_status != "validated" or not edge.provider_visible:
        return False
    if edge.attribution_kind == "local_identity_scaffold":
        return False
    if edge.attribution_kind == "pre_registered_policy":
        return edge.pre_registered
    if edge.attribution_kind == "evidence":
        return d17_path in {"path_1", "path_2"}
    return edge.attribution_kind == "requirement"


def _scope_is_applicable(scope: str, d17_path: str) -> tuple[str, str | None]:
    if scope == "evidence" and d17_path == "path_3":
        return "not_applicable", "d17_path_3_excludes_evidence_attribution"
    return "applicable", None


def _coverage_metrics(candidate_decisions: CandidateDecisionSet, candidate_edges: CandidateAttributionEdgeSet) -> tuple[AttributionCoverageMetric, ...]:
    denominator = len(candidate_decisions.decisions)
    decision_status = {record.decision_id: record.identity_validation_status for record in candidate_decisions.decisions}
    metrics: list[AttributionCoverageMetric] = []
    for scope in _COVERAGE_SCOPES:
        applicability, reason = _scope_is_applicable(scope, candidate_edges.d17_path)
        if applicability == "not_applicable":
            metrics.append(AttributionCoverageMetric("candidate_attribution_coverage", scope, applicability, reason, 0, 0, "not_applicable"))
            continue
        qualifying = {
            edge.decision_id for edge in candidate_edges.edges
            if decision_status[edge.decision_id] in {"validated", "mixed"}
            and _edge_is_eligible(edge, candidate_edges.d17_path)
            and (scope == "all_eligible" or edge.attribution_kind == scope)
        }
        metrics.append(AttributionCoverageMetric(
            "candidate_attribution_coverage", scope, "applicable", None, len(qualifying), denominator,
            _ratio_decimal(len(qualifying), denominator),
        ))
    return tuple(metrics)


def _no_valid_coverage_metrics(d17_path: str) -> tuple[AttributionCoverageMetric, ...]:
    metrics: list[AttributionCoverageMetric] = []
    for scope in _COVERAGE_SCOPES:
        applicability, reason = _scope_is_applicable(scope, d17_path)
        if applicability == "not_applicable":
            metrics.append(AttributionCoverageMetric("candidate_attribution_coverage", scope, applicability, reason, 0, 0, "not_applicable"))
        else:
            metrics.append(AttributionCoverageMetric(
                "candidate_attribution_coverage", scope, "applicable", None, 0, 0, "0.000000",
                "model_did_not_produce_legal_candidate_pagespec",
            ))
    return tuple(metrics)


def _candidate_case_status(candidate_edges: CandidateAttributionEdgeSet, metrics: tuple[AttributionCoverageMetric, ...]) -> str:
    if not candidate_edges.edges:
        return "valid_candidate_no_claims"
    all_eligible = next(metric for metric in metrics if metric.attribution_scope == "all_eligible")
    return "eligible_candidate_present" if all_eligible.numerator > 0 else "invalid_or_ineligible_claims_only"


def _deep_label_result(*, d17_path: str, candidate_edges: CandidateAttributionEdgeSet, alignments: DecisionAlignmentSet, evaluator_only_gold_edges: EvaluatorOnlyGoldEdgeSet | None) -> DeepLabelAttributionCorrectness:
    if d17_path == "path_3":
        return DeepLabelAttributionCorrectness("not_applicable", "d17_path_3_excludes_deep_label_evidence", 0, 0, 0, "not_applicable", "not_applicable")
    if evaluator_only_gold_edges is None:
        return DeepLabelAttributionCorrectness("not_applicable", "evaluator_only_gold_fixture_not_supplied", 0, 0, 0, "not_applicable", "not_applicable")
    aligned_gold_by_decision: dict[str, set[str]] = {}
    for alignment in alignments.alignments:
        for decision_id in alignment.decision_ids:
            aligned_gold_by_decision.setdefault(decision_id, set()).add(alignment.gold_unit_id)
    remaining = {edge.gold_edge_id: edge for edge in evaluator_only_gold_edges.edges}
    true_positive_count = 0
    false_positive_count = 0
    for candidate_edge in candidate_edges.edges:
        if candidate_edge.attribution_kind != "evidence":
            continue
        candidates = [
            gold_edge for gold_edge in remaining.values()
            if gold_edge.gold_unit_id in aligned_gold_by_decision.get(candidate_edge.decision_id, set())
            and gold_edge.source_id == candidate_edge.source_id and gold_edge.relation == candidate_edge.relation
        ]
        if _edge_is_eligible(candidate_edge, d17_path) and candidates:
            matched = min(candidates, key=lambda edge: edge.gold_edge_id)
            del remaining[matched.gold_edge_id]
            true_positive_count += 1
        else:
            # Rejected, invisible, unaligned, duplicate, and non-gold evidence claims stay FP.
            false_positive_count += 1
    false_negative_count = len(remaining)
    precision = "undefined" if true_positive_count + false_positive_count == 0 else _ratio_decimal(true_positive_count, true_positive_count + false_positive_count)
    recall = _ratio_decimal(true_positive_count, true_positive_count + false_negative_count)
    return DeepLabelAttributionCorrectness("applicable", None, true_positive_count, false_positive_count, false_negative_count, precision, recall)


def _no_valid_deep_label_result(d17_path: str, evaluator_only_gold_edges: EvaluatorOnlyGoldEdgeSet | None) -> DeepLabelAttributionCorrectness:
    if d17_path == "path_3":
        return DeepLabelAttributionCorrectness("not_applicable", "d17_path_3_excludes_deep_label_evidence", 0, 0, 0, "not_applicable", "not_applicable")
    if evaluator_only_gold_edges is None:
        return DeepLabelAttributionCorrectness("not_applicable", "evaluator_only_gold_fixture_not_supplied", 0, 0, 0, "not_applicable", "not_applicable")
    return DeepLabelAttributionCorrectness(
        "applicable", None, 0, 0, len(evaluator_only_gold_edges.edges), "undefined", "0.000000"
    )


def _validate_sources(*, case_id: str, page_spec: PageSpec, gold_obligations: GoldObligationSet, candidate_decisions: CandidateDecisionSet, alignments: DecisionAlignmentSet, candidate_edges: CandidateAttributionEdgeSet, evaluator_only_gold_edges: EvaluatorOnlyGoldEdgeSet | None) -> None:
    _require_text(case_id, "case_id")
    if not isinstance(page_spec, PageSpec):
        raise TypeError("page_spec must be a PageSpec")
    if not isinstance(gold_obligations, GoldObligationSet) or not isinstance(candidate_decisions, CandidateDecisionSet):
        raise TypeError("attribution evaluation requires immutable gold and candidate decisions")
    if not isinstance(alignments, DecisionAlignmentSet) or not isinstance(candidate_edges, CandidateAttributionEdgeSet):
        raise TypeError("attribution evaluation requires alignments and candidate attribution edges")
    page_spec.validate()
    gold_obligations.validate()
    candidate_decisions.validate_against(page_spec)
    alignments.validate_against(gold_obligations, candidate_decisions)
    candidate_edges.validate_against(candidate_decisions)
    if {gold_obligations.case_id, candidate_decisions.case_id, alignments.case_id, candidate_edges.case_id} != {case_id}:
        raise ValueError("attribution evaluation inputs must use one identical case_id")
    if evaluator_only_gold_edges is not None:
        if not isinstance(evaluator_only_gold_edges, EvaluatorOnlyGoldEdgeSet):
            raise TypeError("evaluator_only_gold_edges must be an EvaluatorOnlyGoldEdgeSet")
        evaluator_only_gold_edges.validate_against(gold_obligations)
        if evaluator_only_gold_edges.case_id != case_id:
            raise ValueError("evaluator-only gold edge fixture must use the evaluation case_id")
    if candidate_edges.d17_path == "path_3" and evaluator_only_gold_edges is not None:
        raise ValueError("D17 path 3 must not consume a deep-label evidence fixture")


def _validate_no_valid_sources(*, case_id: str, d17_path: str, gold_obligations: GoldObligationSet, evaluator_only_gold_edges: EvaluatorOnlyGoldEdgeSet | None) -> None:
    _require_text(case_id, "case_id")
    if d17_path not in D17_PATHS:
        raise ValueError("no-valid candidate evaluation has an unsupported D17 path")
    if not isinstance(gold_obligations, GoldObligationSet):
        raise TypeError("no-valid candidate evaluation requires GoldObligationSet")
    gold_obligations.validate()
    if gold_obligations.case_id != case_id:
        raise ValueError("no-valid candidate evaluation gold must use the evaluation case_id")
    if evaluator_only_gold_edges is not None:
        if not isinstance(evaluator_only_gold_edges, EvaluatorOnlyGoldEdgeSet):
            raise TypeError("evaluator_only_gold_edges must be an EvaluatorOnlyGoldEdgeSet")
        evaluator_only_gold_edges.validate_against(gold_obligations)
        if evaluator_only_gold_edges.case_id != case_id:
            raise ValueError("evaluator-only gold edge fixture must use the evaluation case_id")
    if d17_path == "path_3" and evaluator_only_gold_edges is not None:
        raise ValueError("D17 path 3 must not consume a deep-label evidence fixture")


@dataclass(frozen=True)
class AttributionEvaluationReport:
    """Hashed case result with strictly disjoint valid- and no-valid-candidate source modes."""

    schema_version: str
    case_id: str
    source_mode: str
    d17_path: str
    candidate_case_status: str
    source_page_spec_schema_version: str | None
    source_page_spec_sha256: str | None
    source_gold_obligation_set_sha256: str
    source_candidate_decision_set_sha256: str | None
    source_alignment_set_sha256: str | None
    source_candidate_attribution_edge_set_sha256: str | None
    source_evaluator_only_gold_edge_set_sha256: str | None
    coverage_metrics: tuple[AttributionCoverageMetric, ...]
    deep_label_correctness: DeepLabelAttributionCorrectness
    excluded_local_identity_scaffold_edge_count: int

    def validate(self) -> None:
        if self.schema_version != ATTRIBUTION_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported attribution evaluation report schema")
        _require_text(self.case_id, "attribution_report.case_id")
        if self.source_mode not in SOURCE_MODES or self.d17_path not in D17_PATHS or self.candidate_case_status not in CANDIDATE_CASE_STATUSES:
            raise ValueError("attribution report source mode, path, or candidate status is unsupported")
        _require_sha256(self.source_gold_obligation_set_sha256, "attribution_report.source_gold_obligation_set_sha256")
        candidate_fields = (
            self.source_page_spec_schema_version, self.source_page_spec_sha256,
            self.source_candidate_decision_set_sha256, self.source_alignment_set_sha256,
            self.source_candidate_attribution_edge_set_sha256,
        )
        if self.source_mode == "valid_candidate":
            if self.candidate_case_status == "no_valid_candidate" or any(value is None for value in candidate_fields):
                raise ValueError("valid-candidate report requires every candidate-side source identity")
            _require_text(self.source_page_spec_schema_version, "attribution_report.source_page_spec_schema_version")
            for field_name in (
                "source_page_spec_sha256", "source_candidate_decision_set_sha256",
                "source_alignment_set_sha256", "source_candidate_attribution_edge_set_sha256",
            ):
                _require_sha256(getattr(self, field_name), f"attribution_report.{field_name}")
        else:
            if self.candidate_case_status != "no_valid_candidate" or any(value is not None for value in candidate_fields):
                raise ValueError("no-valid-candidate report must omit every candidate-side source identity")
        if self.source_evaluator_only_gold_edge_set_sha256 is not None:
            _require_sha256(self.source_evaluator_only_gold_edge_set_sha256, "attribution_report.source_evaluator_only_gold_edge_set_sha256")
        if not isinstance(self.coverage_metrics, tuple) or tuple(metric.attribution_scope for metric in self.coverage_metrics) != _COVERAGE_SCOPES:
            raise ValueError("attribution report must retain every coverage scope in canonical order")
        for metric in self.coverage_metrics:
            metric.validate()
        self.deep_label_correctness.validate()
        if not isinstance(self.excluded_local_identity_scaffold_edge_count, int) or self.excluded_local_identity_scaffold_edge_count < 0:
            raise ValueError("scaffold exclusion count must be a non-negative integer")
        evidence = next(metric for metric in self.coverage_metrics if metric.attribution_scope == "evidence")
        if self.d17_path == "path_3":
            if self.source_evaluator_only_gold_edge_set_sha256 is not None:
                raise ValueError("D17 path 3 must not retain a deep-label gold fixture hash")
            if evidence.applicability != "not_applicable" or self.deep_label_correctness.applicability != "not_applicable":
                raise ValueError("D17 path 3 evidence metrics must be not applicable")
        elif self.deep_label_correctness.applicability == "applicable" and self.source_evaluator_only_gold_edge_set_sha256 is None:
            raise ValueError("applicable deep-label result requires evaluator-only gold identity")
        if self.source_mode == "no_valid_candidate":
            if self.excluded_local_identity_scaffold_edge_count != 0:
                raise ValueError("no-valid-candidate report cannot retain local scaffold edges")
            for metric in self.coverage_metrics:
                if metric.applicability == "applicable" and metric.forced_zero_reason != "model_did_not_produce_legal_candidate_pagespec":
                    raise ValueError("no-valid-candidate coverage must retain the frozen forced-zero reason")

    def validate_against(self, *, page_spec: PageSpec, gold_obligations: GoldObligationSet, candidate_decisions: CandidateDecisionSet, alignments: DecisionAlignmentSet, candidate_edges: CandidateAttributionEdgeSet, evaluator_only_gold_edges: EvaluatorOnlyGoldEdgeSet | None = None) -> None:
        self.validate()
        if self.source_mode != "valid_candidate":
            raise ValueError("no-valid-candidate report must be validated with validate_no_valid_candidate_against")
        _validate_sources(case_id=self.case_id, page_spec=page_spec, gold_obligations=gold_obligations,
            candidate_decisions=candidate_decisions, alignments=alignments, candidate_edges=candidate_edges,
            evaluator_only_gold_edges=evaluator_only_gold_edges)
        expected = _build_report(case_id=self.case_id, page_spec=page_spec, gold_obligations=gold_obligations,
            candidate_decisions=candidate_decisions, alignments=alignments, candidate_edges=candidate_edges,
            evaluator_only_gold_edges=evaluator_only_gold_edges)
        if self.to_dict() != expected.to_dict():
            raise ValueError("attribution evaluation report does not match the frozen source artifacts")

    def validate_no_valid_candidate_against(self, *, gold_obligations: GoldObligationSet, evaluator_only_gold_edges: EvaluatorOnlyGoldEdgeSet | None = None) -> None:
        self.validate()
        if self.source_mode != "no_valid_candidate":
            raise ValueError("valid-candidate report must be validated with validate_against")
        _validate_no_valid_sources(case_id=self.case_id, d17_path=self.d17_path, gold_obligations=gold_obligations,
            evaluator_only_gold_edges=evaluator_only_gold_edges)
        expected = _build_no_valid_report(case_id=self.case_id, d17_path=self.d17_path, gold_obligations=gold_obligations,
            evaluator_only_gold_edges=evaluator_only_gold_edges)
        if self.to_dict() != expected.to_dict():
            raise ValueError("no-valid-candidate report does not match the frozen gold source artifacts")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "candidate_case_status": self.candidate_case_status, "case_id": self.case_id,
            "coverage_metrics": [metric.to_dict() for metric in self.coverage_metrics], "d17_path": self.d17_path,
            "deep_label_correctness": self.deep_label_correctness.to_dict(),
            "excluded_local_identity_scaffold_edge_count": self.excluded_local_identity_scaffold_edge_count,
            "schema_version": self.schema_version, "source_alignment_set_sha256": self.source_alignment_set_sha256,
            "source_candidate_attribution_edge_set_sha256": self.source_candidate_attribution_edge_set_sha256,
            "source_candidate_decision_set_sha256": self.source_candidate_decision_set_sha256,
            "source_evaluator_only_gold_edge_set_sha256": self.source_evaluator_only_gold_edge_set_sha256,
            "source_gold_obligation_set_sha256": self.source_gold_obligation_set_sha256,
            "source_mode": self.source_mode, "source_page_spec_schema_version": self.source_page_spec_schema_version,
            "source_page_spec_sha256": self.source_page_spec_sha256,
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


def _build_report(*, case_id: str, page_spec: PageSpec, gold_obligations: GoldObligationSet, candidate_decisions: CandidateDecisionSet, alignments: DecisionAlignmentSet, candidate_edges: CandidateAttributionEdgeSet, evaluator_only_gold_edges: EvaluatorOnlyGoldEdgeSet | None) -> AttributionEvaluationReport:
    metrics = _coverage_metrics(candidate_decisions, candidate_edges)
    deep = _deep_label_result(d17_path=candidate_edges.d17_path, candidate_edges=candidate_edges,
                              alignments=alignments, evaluator_only_gold_edges=evaluator_only_gold_edges)
    result = AttributionEvaluationReport(
        schema_version=ATTRIBUTION_EVALUATION_SCHEMA_VERSION, case_id=case_id, source_mode="valid_candidate",
        d17_path=candidate_edges.d17_path, candidate_case_status=_candidate_case_status(candidate_edges, metrics),
        source_page_spec_schema_version=page_spec.schema_version, source_page_spec_sha256=_page_spec_sha256(page_spec),
        source_gold_obligation_set_sha256=gold_obligations.sha256(),
        source_candidate_decision_set_sha256=candidate_decisions.sha256(), source_alignment_set_sha256=alignments.sha256(),
        source_candidate_attribution_edge_set_sha256=candidate_edges.sha256(),
        source_evaluator_only_gold_edge_set_sha256=None if evaluator_only_gold_edges is None else evaluator_only_gold_edges.sha256(),
        coverage_metrics=metrics, deep_label_correctness=deep,
        excluded_local_identity_scaffold_edge_count=sum(edge.attribution_kind == "local_identity_scaffold" for edge in candidate_edges.edges),
    )
    result.validate()
    return result


def _build_no_valid_report(*, case_id: str, d17_path: str, gold_obligations: GoldObligationSet, evaluator_only_gold_edges: EvaluatorOnlyGoldEdgeSet | None) -> AttributionEvaluationReport:
    result = AttributionEvaluationReport(
        schema_version=ATTRIBUTION_EVALUATION_SCHEMA_VERSION, case_id=case_id, source_mode="no_valid_candidate",
        d17_path=d17_path, candidate_case_status="no_valid_candidate",
        source_page_spec_schema_version=None, source_page_spec_sha256=None,
        source_gold_obligation_set_sha256=gold_obligations.sha256(),
        source_candidate_decision_set_sha256=None, source_alignment_set_sha256=None,
        source_candidate_attribution_edge_set_sha256=None,
        source_evaluator_only_gold_edge_set_sha256=None if evaluator_only_gold_edges is None else evaluator_only_gold_edges.sha256(),
        coverage_metrics=_no_valid_coverage_metrics(d17_path),
        deep_label_correctness=_no_valid_deep_label_result(d17_path, evaluator_only_gold_edges),
        excluded_local_identity_scaffold_edge_count=0,
    )
    result.validate()
    return result


def evaluate_attribution(case_id: str, page_spec: PageSpec, gold_obligations: GoldObligationSet, candidate_decisions: CandidateDecisionSet, alignments: DecisionAlignmentSet, candidate_edges: CandidateAttributionEdgeSet, evaluator_only_gold_edges: EvaluatorOnlyGoldEdgeSet | None = None) -> AttributionEvaluationReport:
    """Evaluate a real legal PageSpec candidate and its audited attribution edges."""
    _validate_sources(case_id=case_id, page_spec=page_spec, gold_obligations=gold_obligations,
        candidate_decisions=candidate_decisions, alignments=alignments, candidate_edges=candidate_edges,
        evaluator_only_gold_edges=evaluator_only_gold_edges)
    return _build_report(case_id=case_id, page_spec=page_spec, gold_obligations=gold_obligations,
        candidate_decisions=candidate_decisions, alignments=alignments, candidate_edges=candidate_edges,
        evaluator_only_gold_edges=evaluator_only_gold_edges)


def evaluate_no_valid_candidate(case_id: str, d17_path: str, gold_obligations: GoldObligationSet, evaluator_only_gold_edges: EvaluatorOnlyGoldEdgeSet | None = None) -> AttributionEvaluationReport:
    """Record only a model failure to produce a legal candidate PageSpec; no candidate artifact is accepted."""
    _validate_no_valid_sources(case_id=case_id, d17_path=d17_path, gold_obligations=gold_obligations,
        evaluator_only_gold_edges=evaluator_only_gold_edges)
    return _build_no_valid_report(case_id=case_id, d17_path=d17_path, gold_obligations=gold_obligations,
        evaluator_only_gold_edges=evaluator_only_gold_edges)

@dataclass(frozen=True)
class AttributionCaseReference:
    case_id: str
    report_sha256: str

    def validate(self) -> None:
        _require_text(self.case_id, "attribution_case_reference.case_id")
        _require_sha256(self.report_sha256, "attribution_case_reference.report_sha256")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"case_id": self.case_id, "report_sha256": self.report_sha256}


@dataclass(frozen=True)
class AttributionMacroMetric:
    metric_name: str
    total_case_count: int
    applicable_case_count: int
    defined_case_count: int
    undefined_case_count: int
    decimal_value: str

    def validate(self) -> None:
        allowed = {*(f"candidate_attribution_coverage:{scope}" for scope in _COVERAGE_SCOPES), "deep_label_precision", "deep_label_recall"}
        if self.metric_name not in allowed:
            raise ValueError("unsupported attribution macro metric")
        counts = (self.total_case_count, self.applicable_case_count, self.defined_case_count, self.undefined_case_count)
        if any(not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("macro counts must be non-negative integers")
        if self.applicable_case_count > self.total_case_count or self.defined_case_count + self.undefined_case_count != self.applicable_case_count:
            raise ValueError("macro metric case counts are inconsistent")
        if self.applicable_case_count == 0:
            if self.decimal_value != "not_applicable":
                raise ValueError("non-applicable macro metric must report not_applicable")
        elif self.defined_case_count == 0:
            if self.decimal_value != "undefined":
                raise ValueError("macro metric without defined ratios must report undefined")
        else:
            try:
                parsed = Decimal(self.decimal_value)
            except Exception as exc:  # pragma: no cover - defensive parsing branch
                raise ValueError("macro decimal value is invalid") from exc
            if parsed < Decimal("0") or parsed > Decimal("1") or str(parsed.quantize(Decimal("0.000001"))) != self.decimal_value:
                raise ValueError("macro decimal value must be a six-place ratio")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "applicable_case_count": self.applicable_case_count, "decimal_value": self.decimal_value,
            "defined_case_count": self.defined_case_count, "metric_name": self.metric_name,
            "total_case_count": self.total_case_count, "undefined_case_count": self.undefined_case_count,
        }


def _macro_metric(metric_name: str, values: Iterable[tuple[str, str]], total_case_count: int) -> AttributionMacroMetric:
    supplied = tuple(values)
    applicable = [value for applicability, value in supplied if applicability == "applicable"]
    defined = [value for value in applicable if value not in {"undefined", "not_applicable"}]
    undefined_count = sum(value == "undefined" for value in applicable)
    decimal_value = "not_applicable" if not applicable else "undefined" if not defined else _macro_decimal(defined)
    metric = AttributionMacroMetric(metric_name, total_case_count, len(applicable), len(defined), undefined_count, decimal_value)
    metric.validate()
    return metric


@dataclass(frozen=True)
class AttributionMacroSummary:
    """Case-macro summary; it deliberately never recomputes a micro denominator."""

    schema_version: str
    d17_path: str
    case_references: tuple[AttributionCaseReference, ...]
    metrics: tuple[AttributionMacroMetric, ...]

    def validate(self) -> None:
        if self.schema_version != ATTRIBUTION_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported attribution macro summary schema")
        if self.d17_path not in D17_PATHS:
            raise ValueError("attribution macro summary d17_path is unsupported")
        if not isinstance(self.case_references, tuple) or not self.case_references:
            raise ValueError("attribution macro summary requires one or more case references")
        prior: str | None = None
        for reference in self.case_references:
            if not isinstance(reference, AttributionCaseReference):
                raise ValueError("attribution macro summary has an invalid case reference")
            reference.validate()
            if prior is not None and reference.case_id <= prior:
                raise ValueError("attribution macro summary case references must be sorted and unique")
            prior = reference.case_id
        expected_names = tuple([f"candidate_attribution_coverage:{scope}" for scope in _COVERAGE_SCOPES] + ["deep_label_precision", "deep_label_recall"])
        if not isinstance(self.metrics, tuple) or tuple(metric.metric_name for metric in self.metrics) != expected_names:
            raise ValueError("attribution macro summary metrics must retain canonical order")
        for metric in self.metrics:
            metric.validate()
            if metric.total_case_count != len(self.case_references):
                raise ValueError("attribution macro summary metric total case count is inconsistent")

    def validate_against(self, reports: Iterable[AttributionEvaluationReport]) -> None:
        expected = aggregate_attribution_evaluations(tuple(reports))
        self.validate()
        if self.to_dict() != expected.to_dict():
            raise ValueError("attribution macro summary does not match supplied case reports")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "case_references": [reference.to_dict() for reference in self.case_references], "d17_path": self.d17_path,
            "metrics": [metric.to_dict() for metric in self.metrics], "schema_version": self.schema_version,
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


def aggregate_attribution_evaluations(reports: Iterable[AttributionEvaluationReport]) -> AttributionMacroSummary:
    """Aggregate frozen case reports by case, preserving no-valid-candidate zero coverage."""
    supplied = tuple(reports)
    if not supplied or any(not isinstance(report, AttributionEvaluationReport) for report in supplied):
        raise ValueError("attribution macro aggregation requires one or more case reports")
    for report in supplied:
        report.validate()
    paths = {report.d17_path for report in supplied}
    if len(paths) != 1:
        raise ValueError("attribution macro aggregation must not mix D17 paths")
    ordered = tuple(sorted(supplied, key=lambda report: report.case_id))
    if len({report.case_id for report in ordered}) != len(ordered):
        raise ValueError("attribution macro aggregation must not duplicate case IDs")
    metrics: list[AttributionMacroMetric] = []
    for scope in _COVERAGE_SCOPES:
        values = []
        for report in ordered:
            metric = next(item for item in report.coverage_metrics if item.attribution_scope == scope)
            values.append((metric.applicability, metric.decimal_value))
        metrics.append(_macro_metric(f"candidate_attribution_coverage:{scope}", values, len(ordered)))
    metrics.append(_macro_metric("deep_label_precision", ((report.deep_label_correctness.applicability, report.deep_label_correctness.precision) for report in ordered), len(ordered)))
    metrics.append(_macro_metric("deep_label_recall", ((report.deep_label_correctness.applicability, report.deep_label_correctness.recall) for report in ordered), len(ordered)))
    result = AttributionMacroSummary(
        schema_version=ATTRIBUTION_EVALUATION_SCHEMA_VERSION, d17_path=next(iter(paths)),
        case_references=tuple(AttributionCaseReference(report.case_id, report.sha256()) for report in ordered),
        metrics=tuple(metrics),
    )
    result.validate()
    return result
