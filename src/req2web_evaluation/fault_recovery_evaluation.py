"""Evaluator-only M2-07b fault localization, recovery, and fallback scoring.

This module intentionally sits outside `req2web_faults`. It rebinds every
caller-supplied development fault artifact through public runtime gates, then
records only evaluator-side scores. Runtime fault modules must not import this
module or receive its gold/report artifacts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
from uuid import uuid4
from typing import Any, Iterable, Mapping

from req2web_faults.bundle import (
    BlindedBundleInventoryParityReport,
    BlindedFaultBundleRecord,
    BlindedFaultBundleSource,
    FaultBundleError,
    assemble_blinded_fault_bundle,
    load_blinded_fault_bundle,
    validate_blinded_bundle_inventory_parity,
)
from req2web_faults.detector import (
    FaultDetectionError,
    FaultDetectionReport,
    detect_blinded_fault_bundle,
)
from req2web_faults.evaluator_gold import FaultGoldManifest
from req2web_faults.fallback_delivery import FrozenG0FallbackRecord
from req2web_faults.injector_audit import InjectorMutationAudit
from req2web_faults.mutation import FaultCopyRecord, FaultMutationError
from req2web_faults.recovery_outcome import (
    FALLBACK_DELIVERY,
    FAILED_DELIVERY,
    FIRST_PASS_SUCCESS,
    RECOVERED_SUCCESS,
    DeterministicRecoveryOutcome,
    DeterministicRecoveryOutcomeError,
)
from req2web_faults.repair_executor import (
    REPAIR_EXECUTION_REPORT_MANIFEST_SCHEMA_VERSION,
    RepairExecutionError,
    RepairExecutionFileDelta,
    RepairExecutionReport,
)
from req2web_faults.repair_policy import RepairAuthorization, RepairPolicyError


FAULT_RECOVERY_EVALUATION_CASE_SCHEMA_VERSION = "req2web.evaluation.fault_recovery_case.v1"
FAULT_RECOVERY_EVALUATION_SUITE_SCHEMA_VERSION = "req2web.evaluation.fault_recovery_suite.v1"
FAULT_RECOVERY_EVALUATION_MANIFEST_SCHEMA_VERSION = "req2web.evaluation.fault_recovery_manifest.v1"

SCOPE_RELATIONS = frozenset({
    "exact", "under_scope", "over_scope", "overlap", "disjoint", "empty_both",
})
ACTUAL_SCOPE_RELATIONS = SCOPE_RELATIONS | frozenset({"not_attempted", "attempted_without_execution"})
FINAL_OUTCOMES = frozenset({
    FIRST_PASS_SUCCESS, RECOVERED_SUCCESS, FALLBACK_DELIVERY, FAILED_DELIVERY,
})
METRIC_NAMES = (
    "fault_localization_accuracy",
    "predicted_repairability_accuracy",
    "predicted_scope_exact",
    "deterministic_one_repair_success",
    "actual_repair_locality",
    "fallback_delivery",
)

_CASE_REPORT_FILE = "fault_recovery_evaluation_case.json"
_SUITE_REPORT_FILE = "fault_recovery_evaluation_suite.json"
_MANIFEST_FILE = "fault_recovery_evaluation_manifest.json"


class FaultRecoveryEvaluationError(ValueError):
    """A local evaluator report or externally rebound artifact is unsafe."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FaultRecoveryEvaluationError(field_name + " must be non-empty text")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise FaultRecoveryEvaluationError(field_name + " must be a lowercase SHA-256")
    return value


def _safe_relative_posix(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise FaultRecoveryEvaluationError(field_name + " must be a safe POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise FaultRecoveryEvaluationError(field_name + " must not traverse or escape its root")
    return value


def _sorted_unique_text(values: object, field_name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise FaultRecoveryEvaluationError(field_name + " must be an immutable tuple")
    if not allow_empty and not values:
        raise FaultRecoveryEvaluationError(field_name + " must not be empty")
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise FaultRecoveryEvaluationError(field_name + " must be sorted and unique")
    for value in values:
        _require_text(value, field_name + " entry")
    return values


def _safe_path_tuple(values: object, field_name: str) -> tuple[str, ...]:
    result = _sorted_unique_text(values, field_name)
    for value in result:
        _safe_relative_posix(value, field_name + " entry")
    return result


def classify_scope_relation(predicted_scope: tuple[str, ...], gold_scope: tuple[str, ...]) -> str:
    """Classify canonical scopes without treating two empty scopes as exact."""
    predicted = set(_safe_path_tuple(predicted_scope, "predicted_scope"))
    gold = set(_safe_path_tuple(gold_scope, "gold_scope"))
    if not predicted and not gold:
        return "empty_both"
    if predicted == gold:
        return "exact"
    if predicted < gold:
        return "under_scope"
    if gold < predicted:
        return "over_scope"
    if predicted.intersection(gold):
        return "overlap"
    return "disjoint"


@dataclass(frozen=True)
class FaultRecoveryMetric:
    """D08 case-macro artifact with transparent supporting row counts."""

    metric_name: str
    total_case_count: int
    applicable_case_count: int
    defined_case_count: int
    undefined_case_count: int
    decimal_value: str
    row_numerator: int
    row_denominator: int

    def validate(self) -> None:
        if self.metric_name not in METRIC_NAMES:
            raise FaultRecoveryEvaluationError("metric_name is unsupported")
        counts = (
            self.total_case_count, self.applicable_case_count,
            self.defined_case_count, self.undefined_case_count,
            self.row_numerator, self.row_denominator,
        )
        if any(not isinstance(value, int) or value < 0 for value in counts):
            raise FaultRecoveryEvaluationError("macro metric counts must be non-negative integers")
        if self.applicable_case_count > self.total_case_count:
            raise FaultRecoveryEvaluationError("applicable case count exceeds total case count")
        if self.defined_case_count + self.undefined_case_count != self.applicable_case_count:
            raise FaultRecoveryEvaluationError("defined/undefined counts do not cover applicable cases")
        if self.row_numerator > self.row_denominator:
            raise FaultRecoveryEvaluationError("row numerator exceeds transparent row denominator")
        if self.applicable_case_count == 0:
            if self.decimal_value != "not_applicable" or self.defined_case_count or self.undefined_case_count:
                raise FaultRecoveryEvaluationError("zero-applicable macro metric must be explicit N/A")
            if self.row_numerator or self.row_denominator:
                raise FaultRecoveryEvaluationError("non-applicable metric must not carry applicable row support")
            return
        if self.defined_case_count == 0:
            if self.decimal_value != "undefined":
                raise FaultRecoveryEvaluationError("macro metric without defined cases must be explicit undefined")
            return
        try:
            decimal_value = Decimal(self.decimal_value)
        except Exception as error:  # pragma: no cover - defensive parsing branch
            raise FaultRecoveryEvaluationError("macro decimal value is invalid") from error
        if decimal_value < Decimal("0") or decimal_value > Decimal("1"):
            raise FaultRecoveryEvaluationError("macro decimal value must be in [0, 1]")
        if str(decimal_value.quantize(Decimal("0.000001"))) != self.decimal_value:
            raise FaultRecoveryEvaluationError("macro decimal value must use six decimal places")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class FaultRecoveryEvaluationCaseReport:
    """Evaluator-only scoring result for one development/synthetic fault copy."""

    report_id: str
    case_id: str
    fault_copy_id: str
    fault_copy_sha256: str
    injector_audit_id: str
    injector_audit_sha256: str
    gold_manifest_id: str
    gold_manifest_sha256: str
    bundle_id: str
    bundle_record_sha256: str
    detector_report_id: str
    detector_report_sha256: str
    authorization_id: str
    authorization_sha256: str
    outcome_id: str
    outcome_sha256: str
    frozen_fallback_record_id: str | None
    frozen_fallback_record_sha256: str | None
    expected_fallback: str
    gold_stage: str
    gold_error: str
    gold_repairable: bool
    gold_allowed_scope: tuple[str, ...]
    predicted_stage: str
    predicted_error_code: str
    predicted_repairable: bool
    predicted_repair_scope: tuple[str, ...]
    stage_match: bool
    error_match: bool
    fault_localization_exact: bool
    repairability_match: bool
    predicted_scope_relation: str
    repair_applicability: str
    repair_attempt_status: str
    one_repair_success: str
    actual_repair_scope_relation: str
    actual_repair_locality: str
    fallback_delivery_applicability: str
    fallback_delivery_status: str
    final_outcome_status: str
    delivery_source: str
    schema_version: str = FAULT_RECOVERY_EVALUATION_CASE_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("report_id", None)
        return payload

    def validate(self) -> None:
        for field_name in (
            "report_id", "case_id", "fault_copy_id", "fault_copy_sha256", "injector_audit_id",
            "injector_audit_sha256", "gold_manifest_id", "gold_manifest_sha256", "bundle_id",
            "bundle_record_sha256", "detector_report_id", "detector_report_sha256", "authorization_id",
            "authorization_sha256", "outcome_id", "outcome_sha256", "expected_fallback", "gold_stage",
            "gold_error", "predicted_stage", "predicted_error_code", "repair_applicability",
            "repair_attempt_status", "one_repair_success", "actual_repair_scope_relation",
            "actual_repair_locality", "fallback_delivery_applicability", "fallback_delivery_status",
            "final_outcome_status", "delivery_source",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.schema_version != FAULT_RECOVERY_EVALUATION_CASE_SCHEMA_VERSION:
            raise FaultRecoveryEvaluationError("unsupported fault-recovery case schema")
        for field_name in (
            "fault_copy_sha256", "injector_audit_sha256", "gold_manifest_sha256", "bundle_record_sha256",
            "detector_report_sha256", "authorization_sha256", "outcome_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        for field_name in ("frozen_fallback_record_id", "frozen_fallback_record_sha256"):
            value = getattr(self, field_name)
            if value is not None:
                _require_text(value, field_name)
                if field_name.endswith("sha256"):
                    _require_sha256(value, field_name)
        if not all(isinstance(getattr(self, name), bool) for name in (
            "gold_repairable", "predicted_repairable", "stage_match", "error_match",
            "fault_localization_exact", "repairability_match",
        )):
            raise FaultRecoveryEvaluationError("evaluation matches and repairability must be booleans")
        _safe_path_tuple(self.gold_allowed_scope, "gold_allowed_scope")
        _safe_path_tuple(self.predicted_repair_scope, "predicted_repair_scope")
        if self.predicted_scope_relation not in SCOPE_RELATIONS:
            raise FaultRecoveryEvaluationError("predicted_scope_relation is unsupported")
        if self.predicted_scope_relation != classify_scope_relation(self.predicted_repair_scope, self.gold_allowed_scope):
            raise FaultRecoveryEvaluationError("predicted_scope_relation does not match canonical scopes")
        if self.stage_match != (self.predicted_stage == self.gold_stage):
            raise FaultRecoveryEvaluationError("stage_match does not match stage values")
        if self.error_match != (self.predicted_error_code == self.gold_error):
            raise FaultRecoveryEvaluationError("error_match does not match error values")
        if self.fault_localization_exact != (self.stage_match and self.error_match):
            raise FaultRecoveryEvaluationError("fault_localization_exact must require both stage and error")
        if self.repairability_match != (self.predicted_repairable == self.gold_repairable):
            raise FaultRecoveryEvaluationError("repairability_match does not match evaluator/runtime values")
        if self.repair_applicability not in {"applicable", "not_applicable"}:
            raise FaultRecoveryEvaluationError("repair_applicability is unsupported")
        if self.repair_applicability != ("applicable" if self.gold_repairable else "not_applicable"):
            raise FaultRecoveryEvaluationError("repair applicability must use gold_repairable only")
        if self.repair_attempt_status not in {"attempted", "not_attempted", "not_applicable"}:
            raise FaultRecoveryEvaluationError("repair_attempt_status is unsupported")
        if self.repair_applicability == "not_applicable" and self.one_repair_success != "not_applicable":
            raise FaultRecoveryEvaluationError("non-repairable gold case must be excluded from One-repair Success")
        if self.repair_applicability == "applicable" and self.one_repair_success not in {"success", "failure"}:
            raise FaultRecoveryEvaluationError("repairable gold case needs one-repair success/failure")
        if self.one_repair_success == "success" and self.final_outcome_status != RECOVERED_SUCCESS:
            raise FaultRecoveryEvaluationError("only recovered_success may score as repair success")
        if self.actual_repair_scope_relation not in ACTUAL_SCOPE_RELATIONS:
            raise FaultRecoveryEvaluationError("actual_repair_scope_relation is unsupported")
        if self.actual_repair_locality not in {"within_gold_allowed_scope", "outside_gold_allowed_scope", "not_attempted", "attempted_without_execution"}:
            raise FaultRecoveryEvaluationError("actual_repair_locality is unsupported")
        if self.actual_repair_scope_relation in SCOPE_RELATIONS:
            expected_locality = "within_gold_allowed_scope" if self.actual_repair_scope_relation in {"exact", "under_scope", "empty_both"} else "outside_gold_allowed_scope"
            if self.actual_repair_locality != expected_locality:
                raise FaultRecoveryEvaluationError("actual locality does not match actual scope relation")
        elif self.actual_repair_scope_relation != self.actual_repair_locality:
            raise FaultRecoveryEvaluationError("missing execution scope must keep its explicit status")
        if self.fallback_delivery_applicability not in {"applicable", "not_applicable"}:
            raise FaultRecoveryEvaluationError("fallback delivery applicability is unsupported")
        if self.fallback_delivery_status not in {"success", "failure", "not_applicable"}:
            raise FaultRecoveryEvaluationError("fallback delivery status is unsupported")
        requires_fallback = (not self.gold_repairable) or self.final_outcome_status != RECOVERED_SUCCESS
        if self.fallback_delivery_applicability != ("applicable" if requires_fallback else "not_applicable"):
            raise FaultRecoveryEvaluationError("fallback applicability must follow gold repairability and recovered success")
        if self.fallback_delivery_applicability == "not_applicable":
            if self.fallback_delivery_status != "not_applicable":
                raise FaultRecoveryEvaluationError("recovered success must make fallback delivery N/A")
        elif self.fallback_delivery_status not in {"success", "failure"}:
            raise FaultRecoveryEvaluationError("applicable fallback delivery must be success or failure")
        if self.final_outcome_status not in FINAL_OUTCOMES:
            raise FaultRecoveryEvaluationError("final outcome status is unsupported")
        expected_id = "fault-recovery-evaluation-case-" + _canonical_sha256(self.to_payload())[:20]
        if self.report_id != expected_id:
            raise FaultRecoveryEvaluationError("case report ID does not match canonical payload")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"report_id": self.report_id, **self.to_payload()}

    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())


    def validate_against(
        self,
        *,
        fault_copy_record: FaultCopyRecord,
        injector_audit: InjectorMutationAudit,
        gold_manifest: FaultGoldManifest,
        clean_source: BlindedFaultBundleSource,
        fragment_dir: Path,
        bundle_dir: Path,
        parity_report: BlindedBundleInventoryParityReport,
        detector_report: FaultDetectionReport,
        authorization: RepairAuthorization,
        recovery_outcome: DeterministicRecoveryOutcome,
        recovery_outcome_dir: Path,
        fallback_snapshot_dir: Path | None,
        fallback_record: FrozenG0FallbackRecord | None,
    ) -> None:
        self.validate()
        expected = _build_case_report(
            fault_copy_record=fault_copy_record,
            injector_audit=injector_audit,
            gold_manifest=gold_manifest,
            clean_source=clean_source,
            fragment_dir=fragment_dir,
            bundle_dir=bundle_dir,
            parity_report=parity_report,
            detector_report=detector_report,
            authorization=authorization,
            recovery_outcome=recovery_outcome,
            recovery_outcome_dir=recovery_outcome_dir,
            fallback_snapshot_dir=fallback_snapshot_dir,
            fallback_record=fallback_record,
        )
        if self.to_dict() != expected.to_dict():
            raise FaultRecoveryEvaluationError("case report does not match externally rebound fault/recovery evidence")


@dataclass(frozen=True)
class FaultRecoveryEvaluationSuiteReport:
    """Aggregate, manual-mutation-only D08 metrics plus excluded-observation tables."""

    suite_id: str
    case_reports: tuple[FaultRecoveryEvaluationCaseReport, ...]
    metrics: tuple[FaultRecoveryMetric, ...]
    predicted_scope_relation_counts: tuple[tuple[str, int], ...]
    actual_scope_relation_counts: tuple[tuple[str, int], ...]
    final_outcome_counts: tuple[tuple[str, int], ...]
    negative_control_case_ids: tuple[str, ...]
    qwen_natural_error_case_ids: tuple[str, ...]
    schema_version: str = FAULT_RECOVERY_EVALUATION_SUITE_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "actual_scope_relation_counts": _count_dicts(self.actual_scope_relation_counts),
            "case_reports": [item.to_dict() for item in self.case_reports],
            "final_outcome_counts": _count_dicts(self.final_outcome_counts),
            "metrics": [item.to_dict() for item in self.metrics],
            "negative_control_case_ids": list(self.negative_control_case_ids),
            "predicted_scope_relation_counts": _count_dicts(self.predicted_scope_relation_counts),
            "qwen_natural_error_case_ids": list(self.qwen_natural_error_case_ids),
            "schema_version": self.schema_version,
        }

    def validate(self) -> None:
        _require_text(self.suite_id, "suite_id")
        if self.schema_version != FAULT_RECOVERY_EVALUATION_SUITE_SCHEMA_VERSION:
            raise FaultRecoveryEvaluationError("unsupported fault-recovery suite schema")
        if not isinstance(self.case_reports, tuple) or not self.case_reports:
            raise FaultRecoveryEvaluationError("suite requires one or more manual-mutation case reports")
        for report in self.case_reports:
            if not isinstance(report, FaultRecoveryEvaluationCaseReport):
                raise FaultRecoveryEvaluationError("suite case report has an invalid type")
            report.validate()
        case_ids = tuple(item.case_id + "|" + item.gold_manifest_id for item in self.case_reports)
        if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
            raise FaultRecoveryEvaluationError("manual case reports must be sorted and unique by case/gold binding")
        if tuple(metric.metric_name for metric in self.metrics) != METRIC_NAMES:
            raise FaultRecoveryEvaluationError("suite metrics must use the frozen D08 order")
        for metric in self.metrics:
            metric.validate()
        _validate_count_pairs(self.predicted_scope_relation_counts, tuple(sorted(SCOPE_RELATIONS)), "predicted scope")
        _validate_count_pairs(self.actual_scope_relation_counts, tuple(sorted(ACTUAL_SCOPE_RELATIONS)), "actual scope")
        _validate_count_pairs(self.final_outcome_counts, tuple(sorted(FINAL_OUTCOMES)), "final outcome")
        _sorted_unique_text(self.negative_control_case_ids, "negative_control_case_ids")
        _sorted_unique_text(self.qwen_natural_error_case_ids, "qwen_natural_error_case_ids")
        expected = _build_suite_report(self.case_reports, self.negative_control_case_ids, self.qwen_natural_error_case_ids)
        if self.to_payload() != expected.to_payload():
            raise FaultRecoveryEvaluationError("suite metrics/counts do not match the frozen case-macro rules")
        expected_id = "fault-recovery-evaluation-suite-" + _canonical_sha256(self.to_payload())[:20]
        if self.suite_id != expected_id:
            raise FaultRecoveryEvaluationError("suite ID does not match canonical payload")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"suite_id": self.suite_id, **self.to_payload()}

    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())

    def validate_against(self, reports: Iterable[FaultRecoveryEvaluationCaseReport]) -> None:
        self.validate()
        actual = tuple(reports)
        for report in actual:
            report.validate()
        if tuple(item.to_dict() for item in actual) != tuple(item.to_dict() for item in self.case_reports):
            raise FaultRecoveryEvaluationError("suite does not bind the supplied ordered manual case reports")


def evaluate_fault_recovery_case(
    *,
    fault_copy_record: FaultCopyRecord,
    injector_audit: InjectorMutationAudit,
    gold_manifest: FaultGoldManifest,
    clean_source: BlindedFaultBundleSource,
    fragment_dir: Path,
    bundle_dir: Path,
    parity_report: BlindedBundleInventoryParityReport,
    detector_report: FaultDetectionReport,
    authorization: RepairAuthorization,
    recovery_outcome: DeterministicRecoveryOutcome,
    recovery_outcome_dir: Path,
    fallback_snapshot_dir: Path | None,
    fallback_record: FrozenG0FallbackRecord | None,
) -> FaultRecoveryEvaluationCaseReport:
    """Externally rebind a synthetic fault copy and emit one evaluator-only scorecard."""
    return _build_case_report(
        fault_copy_record=fault_copy_record,
        injector_audit=injector_audit,
        gold_manifest=gold_manifest,
        clean_source=clean_source,
        fragment_dir=fragment_dir,
        bundle_dir=bundle_dir,
        parity_report=parity_report,
        detector_report=detector_report,
        authorization=authorization,
        recovery_outcome=recovery_outcome,
        recovery_outcome_dir=recovery_outcome_dir,
        fallback_snapshot_dir=fallback_snapshot_dir,
        fallback_record=fallback_record,
    )


def aggregate_fault_recovery_evaluations(
    reports: Iterable[FaultRecoveryEvaluationCaseReport],
    *,
    negative_control_case_ids: Iterable[str] = (),
    qwen_natural_error_case_ids: Iterable[str] = (),
) -> FaultRecoveryEvaluationSuiteReport:
    """Aggregate only manual-mutation reports; controls/natural errors stay excluded."""
    reports_tuple = tuple(sorted(tuple(reports), key=lambda item: (item.case_id, item.gold_manifest_id)))
    for report in reports_tuple:
        report.validate()
    negative_ids = tuple(sorted(set(negative_control_case_ids)))
    natural_ids = tuple(sorted(set(qwen_natural_error_case_ids)))
    return _build_suite_report(reports_tuple, negative_ids, natural_ids)


def write_fault_recovery_evaluation_case(
    report: FaultRecoveryEvaluationCaseReport,
    output_dir: Path,
    **bindings: object,
) -> FaultRecoveryEvaluationCaseReport:
    """Write a case report only after full external rebinding."""
    if not isinstance(report, FaultRecoveryEvaluationCaseReport):
        raise FaultRecoveryEvaluationError("report must be FaultRecoveryEvaluationCaseReport")
    report.validate_against(**bindings)
    _write_artifact_directory(output_dir, {_CASE_REPORT_FILE: _canonical_json_bytes(report.to_dict())}, report.report_id, report.sha256())
    return report


def write_fault_recovery_evaluation_suite(
    report: FaultRecoveryEvaluationSuiteReport,
    output_dir: Path,
) -> FaultRecoveryEvaluationSuiteReport:
    """Write a canonical aggregate only; suite validation is intentionally report-local."""
    if not isinstance(report, FaultRecoveryEvaluationSuiteReport):
        raise FaultRecoveryEvaluationError("report must be FaultRecoveryEvaluationSuiteReport")
    report.validate()
    _write_artifact_directory(output_dir, {_SUITE_REPORT_FILE: _canonical_json_bytes(report.to_dict())}, report.suite_id, report.sha256())
    return report


def validate_fault_recovery_evaluation_case_artifact(
    output_dir: Path,
    **bindings: object,
) -> FaultRecoveryEvaluationCaseReport:
    """Load a canonical case artifact and rerun its complete external binding chain."""
    files = _read_exact_artifact_directory(Path(output_dir), _CASE_REPORT_FILE)
    report = _case_report_from_dict(_load_canonical_json(files[_CASE_REPORT_FILE], _CASE_REPORT_FILE))
    report.validate_against(**bindings)
    return report


def validate_fault_recovery_evaluation_suite_artifact(output_dir: Path) -> FaultRecoveryEvaluationSuiteReport:
    """Load an aggregate artifact and verify local canonical metric/count integrity."""
    files = _read_exact_artifact_directory(Path(output_dir), _SUITE_REPORT_FILE)
    report = _suite_report_from_dict(_load_canonical_json(files[_SUITE_REPORT_FILE], _SUITE_REPORT_FILE))
    report.validate()
    return report



def _build_case_report(**values: object) -> FaultRecoveryEvaluationCaseReport:
    fault_copy_record = _typed(values, "fault_copy_record", FaultCopyRecord)
    injector_audit = _typed(values, "injector_audit", InjectorMutationAudit)
    gold_manifest = _typed(values, "gold_manifest", FaultGoldManifest)
    clean_source = _typed(values, "clean_source", BlindedFaultBundleSource)
    parity_report = _typed(values, "parity_report", BlindedBundleInventoryParityReport)
    detector_report = _typed(values, "detector_report", FaultDetectionReport)
    authorization = _typed(values, "authorization", RepairAuthorization)
    recovery_outcome = _typed(values, "recovery_outcome", DeterministicRecoveryOutcome)
    fragment_dir = Path(_typed_path(values, "fragment_dir"))
    bundle_dir = Path(_typed_path(values, "bundle_dir"))
    outcome_dir = Path(_typed_path(values, "recovery_outcome_dir"))
    snapshot_dir = _optional_path(values.get("fallback_snapshot_dir"), "fallback_snapshot_dir")
    fallback_record = values.get("fallback_record")
    if fallback_record is not None and not isinstance(fallback_record, FrozenG0FallbackRecord):
        raise FaultRecoveryEvaluationError("fallback_record must be FrozenG0FallbackRecord or None")

    try:
        fault_copy_record.validate()
        injector_audit.validate_against(fault_copy_record)
        gold_manifest.validate_against(fault_copy_record, injector_audit)
        _validate_fragment_record_binding(fragment_dir, fault_copy_record)
        clean_source.validate()
        if clean_source.case_id != gold_manifest.case_id:
            raise FaultRecoveryEvaluationError("clean source case_id does not match evaluator gold")
        bundle_record = _rebind_bundle_from_source(clean_source, fragment_dir, bundle_dir, parity_report)
        fresh_report = detect_blinded_fault_bundle(bundle_dir, parity_report)
        detector_report.validate()
        if detector_report.to_dict() != fresh_report.to_dict():
            raise FaultRecoveryEvaluationError("caller detector report does not exactly match fresh canonical detection")
        authorization.validate_against(detector_report)
        authorization.validate_against(fresh_report)
        recovery_outcome.validate_against(
            bundle_dir, parity_report, fresh_report, authorization,
            snapshot_dir, fallback_record, outcome_dir,
        )
    except (FaultMutationError, FaultBundleError, FaultDetectionError, RepairPolicyError, DeterministicRecoveryOutcomeError, ValueError, TypeError) as error:
        raise FaultRecoveryEvaluationError("external fault/recovery binding failed") from error

    execution: RepairExecutionReport | None = None
    if recovery_outcome.status == RECOVERED_SUCCESS:
        execution = _load_canonical_repair_execution_report(
            outcome_dir / "repair" / "repair_execution_report" / "repair_execution_report.json"
        )
        if (execution.execution_id, execution.sha256()) != (
            recovery_outcome.repair_execution_id, recovery_outcome.repair_execution_sha256,
        ):
            raise FaultRecoveryEvaluationError("recovered outcome does not bind its canonical repair execution report")
        post_report = detect_blinded_fault_bundle(outcome_dir / "repair" / "repaired_bundle", parity_report)
        try:
            execution.validate_against(
                bundle_dir,
                outcome_dir / "repair" / "repaired_bundle",
                parity_report,
                fresh_report,
                authorization,
                post_report,
            )
        except (RepairExecutionError, ValueError, TypeError) as error:
            raise FaultRecoveryEvaluationError("recovered repair execution report failed external validation") from error

    stage_match = fresh_report.predicted_stage == gold_manifest.gold_stage
    error_match = fresh_report.predicted_error_code == gold_manifest.gold_error
    predicted_relation = classify_scope_relation(fresh_report.predicted_repair_scope, gold_manifest.gold_allowed_scope)
    if recovery_outcome.repair_attempted:
        repair_attempt_status = "attempted"
    elif gold_manifest.gold_repairable:
        repair_attempt_status = "not_attempted"
    else:
        repair_attempt_status = "not_applicable"
    if gold_manifest.gold_repairable:
        repair_success = (
            recovery_outcome.status == RECOVERED_SUCCESS
            and recovery_outcome.repair_attempted
            and recovery_outcome.repair_succeeded
            and execution is not None
        )
        one_repair_success = "success" if repair_success else "failure"
    else:
        one_repair_success = "not_applicable"
    if execution is not None:
        actual_relation = classify_scope_relation(execution.actual_repair_scope, gold_manifest.gold_allowed_scope)
        actual_locality = "within_gold_allowed_scope" if actual_relation in {"exact", "under_scope", "empty_both"} else "outside_gold_allowed_scope"
    elif recovery_outcome.repair_attempted:
        actual_relation = "attempted_without_execution"
        actual_locality = "attempted_without_execution"
    else:
        actual_relation = "not_attempted"
        actual_locality = "not_attempted"
    requires_fallback = (not gold_manifest.gold_repairable) or recovery_outcome.status != RECOVERED_SUCCESS
    fallback_applicability = "applicable" if requires_fallback else "not_applicable"
    fallback_status = "not_applicable" if not requires_fallback else ("success" if recovery_outcome.status == FALLBACK_DELIVERY else "failure")
    unsigned = FaultRecoveryEvaluationCaseReport(
        report_id="",
        case_id=gold_manifest.case_id,
        fault_copy_id=fault_copy_record.fault_copy_id,
        fault_copy_sha256=fault_copy_record.sha256(),
        injector_audit_id=injector_audit.injector_audit_id,
        injector_audit_sha256=injector_audit.sha256(),
        gold_manifest_id=gold_manifest.gold_manifest_id,
        gold_manifest_sha256=gold_manifest.sha256(),
        bundle_id=bundle_record.bundle_id,
        bundle_record_sha256=bundle_record.sha256(),
        detector_report_id=fresh_report.report_id,
        detector_report_sha256=fresh_report.sha256(),
        authorization_id=authorization.authorization_id,
        authorization_sha256=authorization.sha256(),
        outcome_id=recovery_outcome.outcome_id,
        outcome_sha256=recovery_outcome.outcome_sha256,
        frozen_fallback_record_id=(fallback_record.record_id if fallback_record is not None else None),
        frozen_fallback_record_sha256=(fallback_record.record_sha256 if fallback_record is not None else None),
        expected_fallback=gold_manifest.expected_fallback,
        gold_stage=gold_manifest.gold_stage,
        gold_error=gold_manifest.gold_error,
        gold_repairable=gold_manifest.gold_repairable,
        gold_allowed_scope=gold_manifest.gold_allowed_scope,
        predicted_stage=fresh_report.predicted_stage,
        predicted_error_code=fresh_report.predicted_error_code,
        predicted_repairable=fresh_report.predicted_repairable,
        predicted_repair_scope=fresh_report.predicted_repair_scope,
        stage_match=stage_match,
        error_match=error_match,
        fault_localization_exact=stage_match and error_match,
        repairability_match=fresh_report.predicted_repairable == gold_manifest.gold_repairable,
        predicted_scope_relation=predicted_relation,
        repair_applicability="applicable" if gold_manifest.gold_repairable else "not_applicable",
        repair_attempt_status=repair_attempt_status,
        one_repair_success=one_repair_success,
        actual_repair_scope_relation=actual_relation,
        actual_repair_locality=actual_locality,
        fallback_delivery_applicability=fallback_applicability,
        fallback_delivery_status=fallback_status,
        final_outcome_status=recovery_outcome.status,
        delivery_source=recovery_outcome.delivery_source,
    )
    report = replace(unsigned, report_id="fault-recovery-evaluation-case-" + _canonical_sha256(unsigned.to_payload())[:20])
    report.validate()
    return report


def _rebind_bundle_from_source(
    clean_source: BlindedFaultBundleSource,
    fragment_dir: Path,
    actual_bundle_dir: Path,
    parity_report: BlindedBundleInventoryParityReport,
) -> BlindedFaultBundleRecord:
    parity_report.validate()
    actual_root = _require_real_directory(actual_bundle_dir, "bundle_dir")
    _reject_symlink_ancestors(actual_root)
    actual_record = load_blinded_fault_bundle(actual_root)
    stage = _create_owned_temporary_directory(actual_root.parent, ".fault-recovery-evaluation-")
    try:
        _reject_symlink_ancestors(stage)
        expected_root = stage / "expected_bundle"
        expected_record = assemble_blinded_fault_bundle(clean_source, expected_root, fragment_dir=fragment_dir)
        expected_files = _read_regular_tree(expected_root, "expected blinded bundle")
        actual_files = _read_regular_tree(actual_root, "actual blinded bundle")
        if expected_record.to_dict() != actual_record.to_dict() or expected_files != actual_files:
            raise FaultRecoveryEvaluationError("actual detector bundle is not the exact evaluator-reassembled fragment bundle")
        pair_parity = validate_blinded_bundle_inventory_parity((expected_record, actual_record))
    finally:
        if stage.exists() and stage.is_dir() and not stage.is_symlink():
            _remove_owned_directory(stage)
    for field_name in ("schema_version", "paths", "slots", "path_set_sha256"):
        if getattr(parity_report, field_name) != getattr(pair_parity, field_name):
            raise FaultRecoveryEvaluationError("supplied parity report does not match evaluator-revalidated bundle inventory")
    return actual_record


def _validate_fragment_record_binding(fragment_dir: Path, expected_record: FaultCopyRecord) -> None:
    root = _require_real_directory(fragment_dir, "fragment_dir")
    _reject_symlink_ancestors(root)
    record_path = root / "fault_copy_record.json"
    manifest_path = root / "fault_copy_manifest.json"
    record_payload = _load_canonical_json_file(record_path, "fault_copy_record.json")
    fields = {
        "fault_copy_id", "case_id", "target_artifact_kind", "target_artifact_id", "source_sha256",
        "mutated_sha256", "copy_location", "copy_manifest_identity", "detector_input_ready", "schema_version",
    }
    if set(record_payload) != fields:
        raise FaultRecoveryEvaluationError("fragment fault-copy record fields are invalid")
    try:
        actual_record = FaultCopyRecord(**record_payload)
        actual_record.validate()
    except (FaultMutationError, TypeError, ValueError) as error:
        raise FaultRecoveryEvaluationError("fragment fault-copy record is invalid") from error
    if record_path.read_bytes() != _canonical_json_bytes(actual_record.to_dict()):
        raise FaultRecoveryEvaluationError("fragment fault-copy record bytes are not canonical")
    if actual_record.to_dict() != expected_record.to_dict():
        raise FaultRecoveryEvaluationError("caller FaultCopyRecord does not match the supplied fragment")
    manifest = _load_canonical_json_file(manifest_path, "fault_copy_manifest.json")
    expected_manifest = {
        "schema_version": "req2web.fault_copy_manifest.v3",
        "fault_copy_id": actual_record.fault_copy_id,
        "fault_copy_record_sha256": actual_record.sha256(),
        "copy_manifest_identity": actual_record.copy_manifest_identity,
        "artifact_file_count": len(_read_regular_tree(root / "artifact", "fault fragment artifacts")),
    }
    if manifest != expected_manifest or manifest_path.read_bytes() != _canonical_json_bytes(expected_manifest):
        raise FaultRecoveryEvaluationError("fragment fault-copy manifest does not bind its canonical record/tree")


def _load_canonical_repair_execution_report(path: Path) -> RepairExecutionReport:
    report_path = _require_regular_file(path, "repair execution report")
    raw = _load_canonical_json_file(report_path, "repair execution report")
    expected_fields = {
        "execution_id", "source_bundle_id", "source_bundle_record_sha256", "source_bundle_inventory_sha256",
        "source_bundle_schema_version", "repaired_bundle_id", "repaired_bundle_record_sha256",
        "repaired_bundle_inventory_sha256", "repaired_bundle_schema_version", "detector_report_id",
        "detector_report_sha256", "authorization_id", "authorization_sha256", "error_code",
        "policy_allowed_scope", "predicted_repair_scope", "effective_repair_scope", "actual_repair_scope",
        "administrative_reseal_scope", "file_deltas", "post_detector_report_id", "post_detector_report_sha256",
        "post_detector_status", "schema_version",
    }
    if set(raw) != expected_fields or not isinstance(raw.get("file_deltas"), list):
        raise FaultRecoveryEvaluationError("repair execution report fields are invalid")
    try:
        deltas = tuple(
            RepairExecutionFileDelta(
                path=item["path"], before_size=item["before_size"], before_sha256=item["before_sha256"],
                after_size=item["after_size"], after_sha256=item["after_sha256"],
            )
            for item in raw["file_deltas"] if isinstance(item, dict)
        )
        if len(deltas) != len(raw["file_deltas"]):
            raise ValueError("file delta is not an object")
        report = RepairExecutionReport(
            execution_id=raw["execution_id"], source_bundle_id=raw["source_bundle_id"],
            source_bundle_record_sha256=raw["source_bundle_record_sha256"],
            source_bundle_inventory_sha256=raw["source_bundle_inventory_sha256"],
            source_bundle_schema_version=raw["source_bundle_schema_version"], repaired_bundle_id=raw["repaired_bundle_id"],
            repaired_bundle_record_sha256=raw["repaired_bundle_record_sha256"],
            repaired_bundle_inventory_sha256=raw["repaired_bundle_inventory_sha256"],
            repaired_bundle_schema_version=raw["repaired_bundle_schema_version"], detector_report_id=raw["detector_report_id"],
            detector_report_sha256=raw["detector_report_sha256"], authorization_id=raw["authorization_id"],
            authorization_sha256=raw["authorization_sha256"], error_code=raw["error_code"],
            policy_allowed_scope=tuple(raw["policy_allowed_scope"]), predicted_repair_scope=tuple(raw["predicted_repair_scope"]),
            effective_repair_scope=tuple(raw["effective_repair_scope"]), actual_repair_scope=tuple(raw["actual_repair_scope"]),
            administrative_reseal_scope=tuple(raw["administrative_reseal_scope"]), file_deltas=deltas,
            post_detector_report_id=raw["post_detector_report_id"], post_detector_report_sha256=raw["post_detector_report_sha256"],
            post_detector_status=raw["post_detector_status"], schema_version=raw["schema_version"],
        )
        report.validate()
    except (RepairExecutionError, TypeError, ValueError, KeyError) as error:
        raise FaultRecoveryEvaluationError("repair execution report is invalid") from error
    if report_path.read_bytes() != _canonical_json_bytes(report.to_dict()):
        raise FaultRecoveryEvaluationError("repair execution report bytes are not canonical")
    manifest_path = report_path.parent / "repair_execution_report_manifest.json"
    manifest = _load_canonical_json_file(manifest_path, "repair execution report manifest")
    expected_manifest = {
        "execution_id": report.execution_id,
        "execution_sha256": report.sha256(),
        "files": [{
            "path": "repair_execution_report.json",
            "sha256": _sha256_bytes(report_path.read_bytes()),
            "size": len(report_path.read_bytes()),
        }],
        "schema_version": REPAIR_EXECUTION_REPORT_MANIFEST_SCHEMA_VERSION,
    }
    if manifest != expected_manifest or manifest_path.read_bytes() != _canonical_json_bytes(expected_manifest):
        raise FaultRecoveryEvaluationError("repair execution manifest is not canonical or does not bind report bytes")
    return report



def _build_suite_report(
    reports: tuple[FaultRecoveryEvaluationCaseReport, ...],
    negative_ids: tuple[str, ...],
    natural_ids: tuple[str, ...],
) -> FaultRecoveryEvaluationSuiteReport:
    if not reports:
        raise FaultRecoveryEvaluationError("aggregate requires at least one manual-mutation case report")
    grouped = _group_reports_by_case(reports)
    localization = _case_macro_metric(
        "fault_localization_accuracy", grouped,
        applicable=lambda _: True,
        success=lambda item: item.fault_localization_exact,
    )
    repairability = _case_macro_metric(
        "predicted_repairability_accuracy", grouped,
        applicable=lambda _: True,
        success=lambda item: item.repairability_match,
    )
    scope_exact = _case_macro_metric(
        "predicted_scope_exact", grouped,
        applicable=lambda _: True,
        success=lambda item: item.predicted_scope_relation in {"exact", "empty_both"},
    )
    one_repair = _case_macro_metric(
        "deterministic_one_repair_success", grouped,
        applicable=lambda item: item.gold_repairable,
        success=lambda item: item.one_repair_success == "success",
    )
    locality = _case_macro_metric(
        "actual_repair_locality", grouped,
        applicable=lambda item: item.actual_repair_scope_relation in SCOPE_RELATIONS,
        success=lambda item: item.actual_repair_locality == "within_gold_allowed_scope",
    )
    fallback = _case_macro_metric(
        "fallback_delivery", grouped,
        applicable=lambda item: item.fallback_delivery_applicability == "applicable",
        success=lambda item: item.fallback_delivery_status == "success",
    )
    predicted_counts = _relation_counts((item.predicted_scope_relation for item in reports), tuple(sorted(SCOPE_RELATIONS)))
    actual_counts = _relation_counts((item.actual_repair_scope_relation for item in reports), tuple(sorted(ACTUAL_SCOPE_RELATIONS)))
    outcome_counts = _relation_counts((item.final_outcome_status for item in reports), tuple(sorted(FINAL_OUTCOMES)))
    unsigned = FaultRecoveryEvaluationSuiteReport(
        suite_id="", case_reports=reports,
        metrics=(localization, repairability, scope_exact, one_repair, locality, fallback),
        predicted_scope_relation_counts=predicted_counts,
        actual_scope_relation_counts=actual_counts,
        final_outcome_counts=outcome_counts,
        negative_control_case_ids=negative_ids,
        qwen_natural_error_case_ids=natural_ids,
    )
    return replace(unsigned, suite_id="fault-recovery-evaluation-suite-" + _canonical_sha256(unsigned.to_payload())[:20])


def _group_reports_by_case(
    reports: tuple[FaultRecoveryEvaluationCaseReport, ...],
) -> tuple[tuple[str, tuple[FaultRecoveryEvaluationCaseReport, ...]], ...]:
    groups: dict[str, list[FaultRecoveryEvaluationCaseReport]] = {}
    for report in reports:
        groups.setdefault(report.case_id, []).append(report)
    return tuple(
        (case_id, tuple(sorted(case_reports, key=lambda item: item.gold_manifest_id)))
        for case_id, case_reports in sorted(groups.items())
    )


def _case_macro_metric(
    metric_name: str,
    groups: tuple[tuple[str, tuple[FaultRecoveryEvaluationCaseReport, ...]], ...],
    *,
    applicable,
    success,
) -> FaultRecoveryMetric:
    per_case_ratios: list[Decimal] = []
    applicable_case_count = 0
    undefined_case_count = 0
    row_numerator = 0
    row_denominator = 0
    for _, reports in groups:
        rows = tuple(item for item in reports if applicable(item))
        if not rows:
            continue
        applicable_case_count += 1
        row_denominator += len(rows)
        successes = sum(bool(success(item)) for item in rows)
        row_numerator += successes
        if not rows:  # pragma: no cover - retained for explicit undefined protocol shape
            undefined_case_count += 1
            continue
        per_case_ratios.append(Decimal(successes) / Decimal(len(rows)))
    defined_case_count = len(per_case_ratios)
    if applicable_case_count == 0:
        decimal_value = "not_applicable"
    elif defined_case_count == 0:
        decimal_value = "undefined"
    else:
        decimal_value = _macro_decimal(per_case_ratios)
    metric = FaultRecoveryMetric(
        metric_name=metric_name,
        total_case_count=len(groups),
        applicable_case_count=applicable_case_count,
        defined_case_count=defined_case_count,
        undefined_case_count=undefined_case_count,
        decimal_value=decimal_value,
        row_numerator=row_numerator,
        row_denominator=row_denominator,
    )
    metric.validate()
    return metric


def _macro_decimal(values: Iterable[Decimal]) -> str:
    supplied = tuple(values)
    if not supplied:
        raise FaultRecoveryEvaluationError("case macro requires one or more defined ratios")
    return str((sum(supplied) / Decimal(len(supplied))).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _relation_counts(values: Iterable[str], vocabulary: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    observed = tuple(values)
    return tuple((name, observed.count(name)) for name in vocabulary)


def _count_dicts(values: tuple[tuple[str, int], ...]) -> list[dict[str, object]]:
    return [{"name": name, "count": count} for name, count in values]


def _validate_count_pairs(values: object, vocabulary: tuple[str, ...], label: str) -> None:
    if not isinstance(values, tuple) or tuple(name for name, _ in values) != vocabulary:
        raise FaultRecoveryEvaluationError(label + " counts must use frozen sorted vocabulary")
    for name, count in values:
        _require_text(name, label + " count name")
        if not isinstance(count, int) or count < 0:
            raise FaultRecoveryEvaluationError(label + " count must be non-negative")


def _case_report_from_dict(payload: dict[str, object]) -> FaultRecoveryEvaluationCaseReport:
    fields = set(FaultRecoveryEvaluationCaseReport.__dataclass_fields__)
    if set(payload) != fields:
        raise FaultRecoveryEvaluationError("case report fields are invalid")
    tuple_fields = {"gold_allowed_scope", "predicted_repair_scope"}
    converted = {name: (tuple(value) if name in tuple_fields and isinstance(value, list) else value) for name, value in payload.items()}
    try:
        report = FaultRecoveryEvaluationCaseReport(**converted)
        report.validate()
    except (TypeError, ValueError) as error:
        raise FaultRecoveryEvaluationError("case report payload is invalid") from error
    return report


def _suite_report_from_dict(payload: dict[str, object]) -> FaultRecoveryEvaluationSuiteReport:
    fields = set(FaultRecoveryEvaluationSuiteReport.__dataclass_fields__)
    if set(payload) != fields or not isinstance(payload.get("case_reports"), list) or not isinstance(payload.get("metrics"), list):
        raise FaultRecoveryEvaluationError("suite report fields are invalid")
    try:
        reports = tuple(_case_report_from_dict(item) for item in payload["case_reports"] if isinstance(item, dict))
        metrics = tuple(FaultRecoveryMetric(**item) for item in payload["metrics"] if isinstance(item, dict))
        if len(reports) != len(payload["case_reports"]) or len(metrics) != len(payload["metrics"]):
            raise ValueError("report/metric entry is invalid")
        def pairs(name: str) -> tuple[tuple[str, int], ...]:
            items = payload[name]
            if not isinstance(items, list):
                raise ValueError(name + " must be a list")
            result = tuple((item["name"], item["count"]) for item in items if isinstance(item, dict) and set(item) == {"name", "count"})
            if len(result) != len(items):
                raise ValueError(name + " entry is invalid")
            return result
        report = FaultRecoveryEvaluationSuiteReport(
            suite_id=payload["suite_id"], case_reports=reports, metrics=metrics,
            predicted_scope_relation_counts=pairs("predicted_scope_relation_counts"),
            actual_scope_relation_counts=pairs("actual_scope_relation_counts"),
            final_outcome_counts=pairs("final_outcome_counts"),
            negative_control_case_ids=tuple(payload["negative_control_case_ids"]),
            qwen_natural_error_case_ids=tuple(payload["qwen_natural_error_case_ids"]),
            schema_version=payload["schema_version"],
        )
        report.validate()
    except (TypeError, ValueError, KeyError) as error:
        raise FaultRecoveryEvaluationError("suite report payload is invalid") from error
    return report


def _typed(values: Mapping[str, object], name: str, expected_type: type[Any]) -> Any:
    value = values.get(name)
    if not isinstance(value, expected_type):
        raise FaultRecoveryEvaluationError(name + " has an invalid type")
    return value


def _typed_path(values: Mapping[str, object], name: str) -> Path:
    value = values.get(name)
    if not isinstance(value, (str, Path)):
        raise FaultRecoveryEvaluationError(name + " must be a filesystem path")
    return Path(value)


def _optional_path(value: object, field_name: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, (str, Path)):
        raise FaultRecoveryEvaluationError(field_name + " must be a filesystem path or None")
    return Path(value)


def _require_real_directory(path: Path, field_name: str) -> Path:
    value = Path(path).absolute()
    _reject_symlink_ancestors(value)
    if value.is_symlink() or not value.is_dir():
        raise FaultRecoveryEvaluationError(field_name + " must be a real directory")
    return value


def _require_regular_file(path: Path, field_name: str) -> Path:
    value = Path(path).absolute()
    _reject_symlink_ancestors(value.parent)
    if value.is_symlink() or not value.is_file():
        raise FaultRecoveryEvaluationError(field_name + " must be a regular file")
    return value


def _read_regular_tree(root: Path, field_name: str) -> dict[str, bytes]:
    directory = _require_real_directory(root, field_name)
    result: dict[str, bytes] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise FaultRecoveryEvaluationError(field_name + " must not contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise FaultRecoveryEvaluationError(field_name + " contains an unsupported path type")
        relative = path.relative_to(directory).as_posix()
        _safe_relative_posix(relative, field_name + " relative path")
        result[relative] = path.read_bytes()
    return result


def _load_canonical_json_file(path: Path, name: str) -> dict[str, object]:
    file_path = _require_regular_file(path, name)
    return _load_canonical_json(file_path.read_bytes(), name)


def _load_canonical_json(content: bytes, name: str) -> dict[str, object]:
    if content.startswith(b"\xef\xbb\xbf"):
        raise FaultRecoveryEvaluationError(name + " must not include a UTF-8 BOM")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FaultRecoveryEvaluationError(name + " must be UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise FaultRecoveryEvaluationError(name + " must be a JSON object")
    if content != _canonical_json_bytes(value):
        raise FaultRecoveryEvaluationError(name + " must use canonical UTF-8 JSON bytes")
    return value


def _write_artifact_directory(output_dir: Path, files: Mapping[str, bytes], report_id: str, report_sha256: str) -> None:
    destination = Path(output_dir).absolute()
    _reject_symlink_ancestors(destination)
    if destination.exists():
        raise FaultRecoveryEvaluationError("writer refuses an existing output target")
    parent = destination.parent
    _reject_symlink_ancestors(parent)
    if not parent.exists() or parent.is_symlink() or not parent.is_dir():
        raise FaultRecoveryEvaluationError("writer parent must be an existing real directory")
    for name, content in files.items():
        _safe_relative_posix(name, "report file path")
        if "/" in name or not isinstance(content, bytes):
            raise FaultRecoveryEvaluationError("report writer accepts only top-level exact byte files")
    stage = _create_owned_temporary_directory(parent, "." + destination.name + ".stage-")
    try:
        staged_files = dict(files)
        manifest = {
            "files": [
                {"path": name, "sha256": _sha256_bytes(content), "size": len(content)}
                for name, content in sorted(staged_files.items())
            ],
            "report_id": report_id,
            "report_sha256": report_sha256,
            "schema_version": FAULT_RECOVERY_EVALUATION_MANIFEST_SCHEMA_VERSION,
        }
        staged_files[_MANIFEST_FILE] = _canonical_json_bytes(manifest)
        for name, content in staged_files.items():
            _write_exact_bytes(stage / name, content)
        actual = _read_regular_tree(stage, "staged evaluator artifact")
        if actual != staged_files:
            raise FaultRecoveryEvaluationError("writer bytes did not round-trip")
        if _load_canonical_json(actual[_MANIFEST_FILE], _MANIFEST_FILE) != manifest:
            raise FaultRecoveryEvaluationError("writer manifest did not round-trip")
        stage.rename(destination)
        stage = None
        _read_exact_artifact_directory(destination, next(iter(files)))
    except Exception:
        if destination.exists() and destination.is_dir() and not destination.is_symlink():
            _remove_owned_directory(destination)
        raise
    finally:
        if stage is not None and stage.exists():
            _remove_owned_directory(stage)


def _read_exact_artifact_directory(output_dir: Path, report_name: str) -> dict[str, bytes]:
    root = _require_real_directory(output_dir, "evaluation artifact output_dir")
    files = _read_regular_tree(root, "evaluation artifact output_dir")
    expected_names = {report_name, _MANIFEST_FILE}
    if set(files) != expected_names:
        raise FaultRecoveryEvaluationError("evaluation artifact has an unexpected file inventory")
    manifest = _load_canonical_json(files[_MANIFEST_FILE], _MANIFEST_FILE)
    expected_keys = {"files", "report_id", "report_sha256", "schema_version"}
    if set(manifest) != expected_keys or manifest["schema_version"] != FAULT_RECOVERY_EVALUATION_MANIFEST_SCHEMA_VERSION:
        raise FaultRecoveryEvaluationError("evaluation artifact manifest fields are invalid")
    entries = manifest["files"]
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise FaultRecoveryEvaluationError("evaluation artifact manifest file inventory is invalid")
    entry = entries[0]
    if set(entry) != {"path", "sha256", "size"} or entry["path"] != report_name:
        raise FaultRecoveryEvaluationError("evaluation artifact manifest report entry is invalid")
    if entry["size"] != len(files[report_name]) or entry["sha256"] != _sha256_bytes(files[report_name]):
        raise FaultRecoveryEvaluationError("evaluation artifact manifest does not bind report bytes")
    _require_text(manifest["report_id"], "manifest report_id")
    _require_sha256(manifest["report_sha256"], "manifest report_sha256")
    payload = _load_canonical_json(files[report_name], report_name)
    if report_name == _CASE_REPORT_FILE:
        report = _case_report_from_dict(payload)
        report_id, report_sha256 = report.report_id, report.sha256()
    elif report_name == _SUITE_REPORT_FILE:
        report = _suite_report_from_dict(payload)
        report_id, report_sha256 = report.suite_id, report.sha256()
    else:
        raise FaultRecoveryEvaluationError("evaluation artifact has an unsupported report path")
    if (manifest["report_id"], manifest["report_sha256"]) != (report_id, report_sha256):
        raise FaultRecoveryEvaluationError("evaluation artifact manifest does not bind report identity/hash")
    return files


def _write_exact_bytes(path: Path, content: bytes) -> None:
    _reject_symlink_ancestors(path.parent)
    if path.exists():
        raise FaultRecoveryEvaluationError("writer refuses to overwrite a file")
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise FaultRecoveryEvaluationError("written bytes did not round-trip")


def _reject_symlink_ancestors(path: Path) -> None:
    for candidate in (Path(path).absolute(), *Path(path).absolute().parents):
        if candidate.exists() and candidate.is_symlink():
            raise FaultRecoveryEvaluationError("symlinked paths are not allowed")



def _create_owned_temporary_directory(parent: Path, prefix: str) -> Path:
    root = _require_real_directory(parent, "temporary directory parent")
    for _ in range(32):
        candidate = root / (prefix + uuid4().hex)
        _reject_symlink_ancestors(candidate)
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        if candidate.is_symlink() or not candidate.is_dir():
            raise FaultRecoveryEvaluationError("temporary directory is unsafe")
        return candidate
    raise FaultRecoveryEvaluationError("unable to allocate a unique evaluator temporary directory")

def _remove_owned_directory(path: Path) -> None:
    root = Path(path).absolute()
    if root.is_symlink() or not root.is_dir():
        raise FaultRecoveryEvaluationError("cleanup target must be a real directory")
    _reject_symlink_ancestors(root.parent)
    for item in root.rglob("*"):
        if item.is_symlink():
            raise FaultRecoveryEvaluationError("cleanup refuses symlinked tree")
    shutil.rmtree(root)
