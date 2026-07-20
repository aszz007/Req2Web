"""Deterministic M1-04b acceptance alignment, metrics, and requirement outcomes."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
import re
from typing import Iterable

from req2web_acceptance.acceptance_plan import AcceptancePlan
from req2web_acceptance.binding import AcceptanceBindingPlan
from req2web_acceptance.browser_executor import BrowserExecutionReport
from req2web_generation.schema import PageSpec
from req2web_evaluation.decision_units import (
    CandidateDecisionSet,
    DecisionAlignmentSet,
    GoldObligationSet,
    create_decision_alignment,
    freeze_decision_alignments,
    map_acceptance_criteria_to_gold_obligations,
)

ACCEPTANCE_EVALUATION_SCHEMA_VERSION = "req2web.evaluation.acceptance_evaluation.v1"
ALIGNMENT_MATCH_TYPES = frozenset({"zero_decision", "one_decision", "many_decisions"})
EVALUATION_RESULTS = frozenset({"pass", "fail", "unknown", "not_supported"})
REQUIREMENT_OUTCOMES = frozenset({"met", "partial", "unmet", "unknown"})
_METRIC_NAMES = (
    "independent_acceptance_coverage",
    "executable_pass_rate",
    "all_gold_criterion_success",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_SCALE = Decimal("0.000001")
_STATUS_PRIORITY = {"pass": 0, "not_supported": 1, "unknown": 2, "fail": 3}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    value = _require_text(value, field_name)
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _sorted_unique(values: object, field_name: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    if not allow_empty and not values:
        raise ValueError(f"{field_name} must not be empty")
    result: list[str] = []
    prior: str | None = None
    for index, value in enumerate(values):
        value = _require_text(value, f"{field_name}[{index}]")
        if prior is not None and value <= prior:
            raise ValueError(f"{field_name} must be sorted and unique")
        result.append(value)
        prior = value
    return tuple(result)


def _ratio_decimal(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "not_applicable"
    value = (Decimal(numerator) / Decimal(denominator)).quantize(_DECIMAL_SCALE, rounding=ROUND_HALF_UP)
    return format(value, "f")


def classify_alignment_match(decision_ids: Iterable[str]) -> str:
    """Classify a canonical exact-ID alignment without inspecting candidate content."""
    canonical = tuple(sorted(_require_text(item, "decision_ids item") for item in decision_ids))
    if len(canonical) != len(set(canonical)):
        raise ValueError("decision_ids must not contain duplicates")
    return "zero_decision" if not canonical else "one_decision" if len(canonical) == 1 else "many_decisions"


def _merged_status(statuses: Iterable[str]) -> str:
    canonical = tuple(statuses)
    if not canonical:
        raise ValueError("a gold obligation must retain at least one browser criterion status")
    if any(status not in EVALUATION_RESULTS for status in canonical):
        raise ValueError("browser criterion status is unsupported by acceptance evaluation")
    return max(canonical, key=lambda status: _STATUS_PRIORITY[status])


@dataclass(frozen=True)
class GoldAcceptanceResult:
    """One D08 result per deduplicated gold obligation, never a candidate denominator."""

    gold_unit_id: str
    source_requirement_ids: tuple[str, ...]
    hardness: str
    evaluation_result: str
    source_criterion_ids: tuple[str, ...]
    matched_decision_ids: tuple[str, ...]
    has_executable_binding: bool

    def validate(self) -> None:
        _require_text(self.gold_unit_id, "gold_result.gold_unit_id")
        _sorted_unique(self.source_requirement_ids, "gold_result.source_requirement_ids", allow_empty=False)
        if self.hardness not in {"hard", "soft"}:
            raise ValueError("gold_result.hardness is unsupported")
        if self.evaluation_result not in EVALUATION_RESULTS:
            raise ValueError("gold_result.evaluation_result is unsupported")
        _sorted_unique(self.source_criterion_ids, "gold_result.source_criterion_ids", allow_empty=False)
        _sorted_unique(self.matched_decision_ids, "gold_result.matched_decision_ids", allow_empty=True)
        if not isinstance(self.has_executable_binding, bool):
            raise ValueError("gold_result.has_executable_binding must be boolean")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "evaluation_result": self.evaluation_result,
            "gold_unit_id": self.gold_unit_id,
            "hardness": self.hardness,
            "has_executable_binding": self.has_executable_binding,
            "matched_decision_ids": list(self.matched_decision_ids),
            "source_criterion_ids": list(self.source_criterion_ids),
            "source_requirement_ids": list(self.source_requirement_ids),
        }


@dataclass(frozen=True)
class RatioMetric:
    """Reproducible numerator/denominator metric with an explicit zero denominator."""

    metric_name: str
    numerator: int
    denominator: int
    decimal_value: str
    unknown_count: int
    not_supported_count: int
    not_applicable_reason: str | None

    def validate(self) -> None:
        if self.metric_name not in _METRIC_NAMES:
            raise ValueError("metric_name is unsupported")
        for field_name, value in (("metric.numerator", self.numerator), ("metric.denominator", self.denominator), ("metric.unknown_count", self.unknown_count), ("metric.not_supported_count", self.not_supported_count)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.numerator > self.denominator:
            raise ValueError("metric.numerator cannot exceed metric.denominator")
        if self.decimal_value != _ratio_decimal(self.numerator, self.denominator):
            raise ValueError("metric.decimal_value does not match the frozen ratio rule")
        if self.denominator == 0:
            if self.not_applicable_reason != "no_determinate_executed_gold_units":
                raise ValueError("zero denominator requires the documented non-applicable reason")
        elif self.not_applicable_reason is not None:
            raise ValueError("non-zero denominator must not carry a non-applicable reason")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "decimal_value": self.decimal_value,
            "denominator": self.denominator,
            "metric_name": self.metric_name,
            "not_applicable_reason": self.not_applicable_reason,
            "not_supported_count": self.not_supported_count,
            "numerator": self.numerator,
            "unknown_count": self.unknown_count,
        }


@dataclass(frozen=True)
class RequirementOutcome:
    """D08 requirement-level outcome projected only from immutable gold results."""

    requirement_id: str
    outcome: str
    hard_results: tuple[tuple[str, str], ...]
    soft_results: tuple[tuple[str, str], ...]

    def validate(self) -> None:
        _require_text(self.requirement_id, "requirement_outcome.requirement_id")
        if self.outcome not in REQUIREMENT_OUTCOMES:
            raise ValueError("requirement_outcome.outcome is unsupported")
        for field_name, values in (("requirement_outcome.hard_results", self.hard_results), ("requirement_outcome.soft_results", self.soft_results)):
            if not isinstance(values, tuple):
                raise ValueError(f"{field_name} must be a tuple")
            prior: str | None = None
            for index, value in enumerate(values):
                if not isinstance(value, tuple) or len(value) != 2:
                    raise ValueError(f"{field_name}[{index}] must be a two-item tuple")
                gold_unit_id = _require_text(value[0], f"{field_name}[{index}].gold_unit_id")
                if value[1] not in EVALUATION_RESULTS:
                    raise ValueError(f"{field_name}[{index}].result is unsupported")
                if prior is not None and gold_unit_id <= prior:
                    raise ValueError(f"{field_name} must be sorted and unique by gold unit")
                prior = gold_unit_id
        if not self.hard_results and not self.soft_results:
            raise ValueError("requirement_outcome needs at least one applicable gold result")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "hard_results": [{"evaluation_result": result, "gold_unit_id": gold_unit_id} for gold_unit_id, result in self.hard_results],
            "outcome": self.outcome,
            "requirement_id": self.requirement_id,
            "soft_results": [{"evaluation_result": result, "gold_unit_id": gold_unit_id} for gold_unit_id, result in self.soft_results],
        }


def aggregate_requirement_outcomes(results: Iterable[GoldAcceptanceResult]) -> tuple[RequirementOutcome, ...]:
    """Apply D08 exactly; all M1-04b gold results are applicable."""
    grouped: dict[str, list[GoldAcceptanceResult]] = {}
    for result in results:
        if not isinstance(result, GoldAcceptanceResult):
            raise TypeError("results must contain GoldAcceptanceResult records")
        result.validate()
        for requirement_id in result.source_requirement_ids:
            grouped.setdefault(requirement_id, []).append(result)
    if not grouped:
        raise ValueError("at least one applicable gold result is required")
    outcomes: list[RequirementOutcome] = []
    for requirement_id in sorted(grouped):
        records = grouped[requirement_id]
        hard = tuple(sorted((item.gold_unit_id, item.evaluation_result) for item in records if item.hardness == "hard"))
        soft = tuple(sorted((item.gold_unit_id, item.evaluation_result) for item in records if item.hardness == "soft"))
        hard_statuses = {status for _, status in hard}
        soft_statuses = {status for _, status in soft}
        if "fail" in hard_statuses:
            outcome = "unmet"
        elif hard_statuses.intersection({"unknown", "not_supported"}):
            outcome = "unknown"
        elif soft_statuses.intersection({"fail", "unknown", "not_supported"}):
            outcome = "partial"
        else:
            outcome = "met"
        record = RequirementOutcome(requirement_id, outcome, hard, soft)
        record.validate()
        outcomes.append(record)
    return tuple(outcomes)


def _metric_records(results: tuple[GoldAcceptanceResult, ...]) -> tuple[RatioMetric, ...]:
    total = len(results)
    coverage_numerator = sum(item.has_executable_binding for item in results)
    executable = tuple(item for item in results if item.has_executable_binding)
    determinate = tuple(item for item in executable if item.evaluation_result in {"pass", "fail"})
    pass_count = sum(item.evaluation_result == "pass" for item in determinate)
    records = (
        RatioMetric(
            "independent_acceptance_coverage", coverage_numerator, total,
            _ratio_decimal(coverage_numerator, total), 0, 0, None,
        ),
        RatioMetric(
            "executable_pass_rate", pass_count, len(determinate),
            _ratio_decimal(pass_count, len(determinate)),
            sum(item.evaluation_result == "unknown" for item in executable),
            sum(item.evaluation_result == "not_supported" for item in executable),
            None if determinate else "no_determinate_executed_gold_units",
        ),
        RatioMetric(
            "all_gold_criterion_success", sum(item.evaluation_result == "pass" for item in results), total,
            _ratio_decimal(sum(item.evaluation_result == "pass" for item in results), total),
            sum(item.evaluation_result == "unknown" for item in results),
            sum(item.evaluation_result == "not_supported" for item in results), None,
        ),
    )
    for item in records:
        item.validate()
    return records


def _validate_inputs(
    case_id: str,
    acceptance_plan: AcceptancePlan,
    binding_plan: AcceptanceBindingPlan,
    browser_report: BrowserExecutionReport,
    page_spec: PageSpec,
    gold_obligations: GoldObligationSet,
    candidate_decisions: CandidateDecisionSet,
) -> None:
    _require_text(case_id, "case_id")
    if not isinstance(acceptance_plan, AcceptancePlan):
        raise TypeError("acceptance_plan must be an AcceptancePlan")
    if not isinstance(binding_plan, AcceptanceBindingPlan):
        raise TypeError("binding_plan must be an AcceptanceBindingPlan")
    if not isinstance(browser_report, BrowserExecutionReport):
        raise TypeError("browser_report must be a BrowserExecutionReport")
    if not isinstance(page_spec, PageSpec):
        raise TypeError("page_spec must be a PageSpec")
    if not isinstance(gold_obligations, GoldObligationSet):
        raise TypeError("gold_obligations must be a GoldObligationSet")
    if not isinstance(candidate_decisions, CandidateDecisionSet):
        raise TypeError("candidate_decisions must be a CandidateDecisionSet")
    acceptance_plan.validate()
    binding_plan.validate()
    page_spec.validate()
    browser_report.validate_against(binding_plan)
    gold_obligations.validate()
    candidate_decisions.validate_against(page_spec)

    plan_sha256 = acceptance_plan.sha256()
    if binding_plan.source_acceptance_plan_schema_version != acceptance_plan.schema_version:
        raise ValueError("binding plan AcceptancePlan schema does not match")
    if binding_plan.source_acceptance_plan_sha256 != plan_sha256:
        raise ValueError("binding plan AcceptancePlan hash does not match")
    if (
        binding_plan.source_requirement_view_schema_version != acceptance_plan.source_requirement_view_schema_version
        or binding_plan.source_requirement_view_sha256 != acceptance_plan.source_requirement_view_sha256
    ):
        raise ValueError("binding plan RequirementView identity does not match")
    if gold_obligations.case_id != case_id or candidate_decisions.case_id != case_id:
        raise ValueError("case_id does not match every decision collection")
    if gold_obligations.source_acceptance_plan_schema_version != acceptance_plan.schema_version:
        raise ValueError("gold set AcceptancePlan schema does not match")
    if gold_obligations.source_acceptance_plan_sha256 != plan_sha256:
        raise ValueError("gold set AcceptancePlan hash does not match")
    if candidate_decisions.source_page_spec_schema_version != binding_plan.source_page_spec_schema_version:
        raise ValueError("candidate PageSpec schema does not match binding plan")
    if candidate_decisions.source_page_spec_sha256 != binding_plan.source_page_spec_sha256:
        raise ValueError("candidate PageSpec hash does not match binding plan")
    if browser_report.source_binding_plan_sha256 != binding_plan.sha256():
        raise ValueError("browser report binding hash does not match")
    if browser_report.source_page_spec_sha256 != candidate_decisions.source_page_spec_sha256:
        raise ValueError("browser report PageSpec hash does not match candidate set")


def _components(
    acceptance_plan: AcceptancePlan,
    binding_plan: AcceptanceBindingPlan,
    browser_report: BrowserExecutionReport,
    gold_obligations: GoldObligationSet,
    candidate_decisions: CandidateDecisionSet,
) -> tuple[DecisionAlignmentSet, tuple[GoldAcceptanceResult, ...], tuple[RatioMetric, ...], tuple[RequirementOutcome, ...]]:
    criterion_to_gold = dict(map_acceptance_criteria_to_gold_obligations(acceptance_plan, gold_obligations))
    bindings_by_criterion = {item.criterion_id: item for item in binding_plan.bindings}
    runtime_by_criterion = {item.criterion_id: item for item in browser_report.criteria}
    steps_by_id = {item.step_id: item for item in binding_plan.steps}
    criteria_by_gold: dict[str, list[str]] = {item.gold_unit_id: [] for item in gold_obligations.obligations}
    for criterion_id, gold_unit_id in criterion_to_gold.items():
        if criterion_id not in bindings_by_criterion or criterion_id not in runtime_by_criterion:
            raise ValueError("criterion-to-gold mapping references an unbound or unexecuted criterion")
        criteria_by_gold[gold_unit_id].append(criterion_id)

    alignments = []
    results = []
    for gold in gold_obligations.obligations:
        criterion_ids = tuple(sorted(criteria_by_gold[gold.gold_unit_id]))
        if not criterion_ids:
            raise ValueError("gold obligation lost its frozen AcceptancePlan criterion mapping")
        target_ids: set[str] = set()
        statuses: list[str] = []
        executable = False
        for criterion_id in criterion_ids:
            binding = bindings_by_criterion[criterion_id]
            runtime = runtime_by_criterion[criterion_id]
            statuses.append(runtime.status)
            target_ids.update(value for _, value in binding.target_refs)
            for step_id in binding.step_ids:
                target_ids.add(steps_by_id[step_id].target_id)
            if binding.disposition == "bound" and binding.step_ids:
                executable = True
        decision_ids = tuple(sorted(
            item.decision_id for item in candidate_decisions.decisions
            if target_ids.intersection(item.candidate_entity_ids)
        ))
        alignment = create_decision_alignment(
            gold.gold_unit_id, decision_ids, classify_alignment_match(decision_ids), _merged_status(statuses)
        )
        alignments.append(alignment)
        result = GoldAcceptanceResult(
            gold.gold_unit_id, gold.source_requirement_ids, gold.hardness,
            alignment.evaluation_result, criterion_ids, decision_ids, executable,
        )
        result.validate()
        results.append(result)

    alignment_set = freeze_decision_alignments(gold_obligations, candidate_decisions, tuple(alignments))
    result_records = tuple(sorted(results, key=lambda item: item.gold_unit_id))
    metrics = _metric_records(result_records)
    outcomes = aggregate_requirement_outcomes(result_records)
    return alignment_set, result_records, metrics, outcomes


@dataclass(frozen=True)
class AcceptanceEvaluationReport:
    """Canonical M1-04b report linked only to immutable deterministic inputs."""

    schema_version: str
    case_id: str
    source_requirement_view_schema_version: str
    source_requirement_view_sha256: str
    source_acceptance_plan_schema_version: str
    source_acceptance_plan_sha256: str
    source_binding_plan_schema_version: str
    source_binding_plan_sha256: str
    source_browser_execution_schema_version: str
    source_browser_execution_sha256: str
    source_page_spec_schema_version: str
    source_page_spec_sha256: str
    source_gold_obligation_set_schema_version: str
    source_gold_obligation_set_sha256: str
    source_candidate_decision_set_schema_version: str
    source_candidate_decision_set_sha256: str
    alignment_set: DecisionAlignmentSet
    alignment_set_sha256: str
    gold_results: tuple[GoldAcceptanceResult, ...]
    metrics: tuple[RatioMetric, ...]
    requirement_outcomes: tuple[RequirementOutcome, ...]

    def validate(self) -> None:
        if self.schema_version != ACCEPTANCE_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported acceptance evaluation schema")
        _require_text(self.case_id, "report.case_id")
        for field_name, value in (
            ("report.source_requirement_view_schema_version", self.source_requirement_view_schema_version),
            ("report.source_acceptance_plan_schema_version", self.source_acceptance_plan_schema_version),
            ("report.source_binding_plan_schema_version", self.source_binding_plan_schema_version),
            ("report.source_browser_execution_schema_version", self.source_browser_execution_schema_version),
            ("report.source_page_spec_schema_version", self.source_page_spec_schema_version),
            ("report.source_gold_obligation_set_schema_version", self.source_gold_obligation_set_schema_version),
            ("report.source_candidate_decision_set_schema_version", self.source_candidate_decision_set_schema_version),
        ):
            _require_text(value, field_name)
        for field_name, value in (
            ("report.source_requirement_view_sha256", self.source_requirement_view_sha256),
            ("report.source_acceptance_plan_sha256", self.source_acceptance_plan_sha256),
            ("report.source_binding_plan_sha256", self.source_binding_plan_sha256),
            ("report.source_browser_execution_sha256", self.source_browser_execution_sha256),
            ("report.source_page_spec_sha256", self.source_page_spec_sha256),
            ("report.source_gold_obligation_set_sha256", self.source_gold_obligation_set_sha256),
            ("report.source_candidate_decision_set_sha256", self.source_candidate_decision_set_sha256),
            ("report.alignment_set_sha256", self.alignment_set_sha256),
        ):
            _require_sha256(value, field_name)
        if not isinstance(self.alignment_set, DecisionAlignmentSet):
            raise ValueError("report.alignment_set must be a DecisionAlignmentSet")
        self.alignment_set.validate()
        if self.alignment_set_sha256 != self.alignment_set.sha256():
            raise ValueError("report alignment set hash does not match serialized alignment set")
        if not isinstance(self.gold_results, tuple) or not self.gold_results:
            raise ValueError("report.gold_results must be a non-empty tuple")
        prior_gold: str | None = None
        for record in self.gold_results:
            if not isinstance(record, GoldAcceptanceResult):
                raise ValueError("report.gold_results contains an invalid record")
            record.validate()
            if prior_gold is not None and record.gold_unit_id <= prior_gold:
                raise ValueError("report.gold_results must be sorted and unique")
            prior_gold = record.gold_unit_id
        if tuple(item.metric_name for item in self.metrics) != _METRIC_NAMES:
            raise ValueError("report.metrics must use the frozen D08 metric order")
        for metric in self.metrics:
            if not isinstance(metric, RatioMetric):
                raise ValueError("report.metrics contains an invalid record")
            metric.validate()
        if not isinstance(self.requirement_outcomes, tuple) or not self.requirement_outcomes:
            raise ValueError("report.requirement_outcomes must be a non-empty tuple")
        prior_requirement: str | None = None
        for outcome in self.requirement_outcomes:
            if not isinstance(outcome, RequirementOutcome):
                raise ValueError("report.requirement_outcomes contains an invalid record")
            outcome.validate()
            if prior_requirement is not None and outcome.requirement_id <= prior_requirement:
                raise ValueError("report.requirement_outcomes must be sorted and unique")
            prior_requirement = outcome.requirement_id

    def validate_against(
        self,
        acceptance_plan: AcceptancePlan,
        binding_plan: AcceptanceBindingPlan,
        browser_report: BrowserExecutionReport,
        page_spec: PageSpec,
        gold_obligations: GoldObligationSet,
        candidate_decisions: CandidateDecisionSet,
    ) -> None:
        _validate_inputs(self.case_id, acceptance_plan, binding_plan, browser_report, page_spec, gold_obligations, candidate_decisions)
        self.validate()
        if (
            self.source_requirement_view_schema_version != acceptance_plan.source_requirement_view_schema_version
            or self.source_requirement_view_sha256 != acceptance_plan.source_requirement_view_sha256
            or self.source_acceptance_plan_schema_version != acceptance_plan.schema_version
            or self.source_acceptance_plan_sha256 != acceptance_plan.sha256()
            or self.source_binding_plan_schema_version != binding_plan.schema_version
            or self.source_binding_plan_sha256 != binding_plan.sha256()
            or self.source_browser_execution_schema_version != browser_report.schema_version
            or self.source_browser_execution_sha256 != browser_report.sha256()
            or self.source_page_spec_schema_version != candidate_decisions.source_page_spec_schema_version
            or self.source_page_spec_sha256 != candidate_decisions.source_page_spec_sha256
            or self.source_gold_obligation_set_schema_version != gold_obligations.schema_version
            or self.source_gold_obligation_set_sha256 != gold_obligations.sha256()
            or self.source_candidate_decision_set_schema_version != candidate_decisions.schema_version
            or self.source_candidate_decision_set_sha256 != candidate_decisions.sha256()
            or self.alignment_set.case_id != self.case_id
            or self.alignment_set.source_gold_obligation_set_sha256 != gold_obligations.sha256()
            or self.alignment_set.source_candidate_decision_set_sha256 != candidate_decisions.sha256()
        ):
            raise ValueError("report source identity does not match immutable evaluation inputs")
        expected_alignment, expected_results, expected_metrics, expected_outcomes = _components(
            acceptance_plan, binding_plan, browser_report, gold_obligations, candidate_decisions
        )
        if (
            self.alignment_set != expected_alignment
            or self.gold_results != expected_results
            or self.metrics != expected_metrics
            or self.requirement_outcomes != expected_outcomes
        ):
            raise ValueError("report content does not match deterministic acceptance evaluation")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "alignment_set": self.alignment_set.to_dict(),
            "alignment_set_sha256": self.alignment_set_sha256,
            "case_id": self.case_id,
            "gold_results": [item.to_dict() for item in self.gold_results],
            "metrics": [item.to_dict() for item in self.metrics],
            "requirement_outcomes": [item.to_dict() for item in self.requirement_outcomes],
            "schema_version": self.schema_version,
            "source_acceptance_plan_schema_version": self.source_acceptance_plan_schema_version,
            "source_acceptance_plan_sha256": self.source_acceptance_plan_sha256,
            "source_binding_plan_schema_version": self.source_binding_plan_schema_version,
            "source_binding_plan_sha256": self.source_binding_plan_sha256,
            "source_browser_execution_schema_version": self.source_browser_execution_schema_version,
            "source_browser_execution_sha256": self.source_browser_execution_sha256,
            "source_candidate_decision_set_schema_version": self.source_candidate_decision_set_schema_version,
            "source_candidate_decision_set_sha256": self.source_candidate_decision_set_sha256,
            "source_gold_obligation_set_schema_version": self.source_gold_obligation_set_schema_version,
            "source_gold_obligation_set_sha256": self.source_gold_obligation_set_sha256,
            "source_page_spec_schema_version": self.source_page_spec_schema_version,
            "source_page_spec_sha256": self.source_page_spec_sha256,
            "source_requirement_view_schema_version": self.source_requirement_view_schema_version,
            "source_requirement_view_sha256": self.source_requirement_view_sha256,
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


def evaluate_acceptance(
    case_id: str,
    acceptance_plan: AcceptancePlan,
    binding_plan: AcceptanceBindingPlan,
    browser_report: BrowserExecutionReport,
    page_spec: PageSpec,
    gold_obligations: GoldObligationSet,
    candidate_decisions: CandidateDecisionSet,
) -> AcceptanceEvaluationReport:
    """Build the only M1-04b alignment/report path; no PageSpec text matching is used."""
    _validate_inputs(case_id, acceptance_plan, binding_plan, browser_report, page_spec, gold_obligations, candidate_decisions)
    alignment_set, results, metrics, outcomes = _components(
        acceptance_plan, binding_plan, browser_report, gold_obligations, candidate_decisions
    )
    report = AcceptanceEvaluationReport(
        ACCEPTANCE_EVALUATION_SCHEMA_VERSION, case_id,
        acceptance_plan.source_requirement_view_schema_version, acceptance_plan.source_requirement_view_sha256,
        acceptance_plan.schema_version, acceptance_plan.sha256(),
        binding_plan.schema_version, binding_plan.sha256(),
        browser_report.schema_version, browser_report.sha256(),
        candidate_decisions.source_page_spec_schema_version, candidate_decisions.source_page_spec_sha256,
        gold_obligations.schema_version, gold_obligations.sha256(),
        candidate_decisions.schema_version, candidate_decisions.sha256(),
        alignment_set, alignment_set.sha256(), results, metrics, outcomes,
    )
    report.validate_against(acceptance_plan, binding_plan, browser_report, page_spec, gold_obligations, candidate_decisions)
    return report
