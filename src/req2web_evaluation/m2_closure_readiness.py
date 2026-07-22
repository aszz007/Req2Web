"""Evaluator-only M2 development/synthetic closure-readiness matrix."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Iterable, Mapping
from uuid import uuid4

from req2web_faults.bundle import BlindedFaultBundleSource, assemble_blinded_fault_bundle, validate_blinded_bundle_inventory_parity
from req2web_faults.detector import NO_FAULT_DETECTED, detect_blinded_fault_bundle
from req2web_faults.mutation import FaultCopyRecord
from req2web_evaluation.fault_recovery_evaluation import FaultRecoveryEvaluationCaseReport, FaultRecoveryEvaluationError, validate_fault_recovery_evaluation_case_artifact, validate_fault_recovery_evaluation_suite_artifact
from req2web_evaluation.negative_control import PASSED, NegativeControlRun, build_passing_negative_control_ignored_evidence_misattribution_fault_copy, validate_negative_control_artifact

M2_CLOSURE_READINESS_SCHEMA_VERSION = "req2web.evaluation.m2_closure_readiness.v1"
M2_CLOSURE_READINESS_MANIFEST_SCHEMA_VERSION = "req2web.evaluation.m2_closure_readiness_manifest.v1"
READY_FOR_MANAGER_REVIEW = "ready_for_manager_review"
NOT_READY = "not_ready"
IMPLEMENTED_MANUAL_FAULT = "implemented_manual_fault"
DEPENDENCY_DEFERRED_SEMANTIC_EVIDENCE = "dependency_deferred_semantic_evidence"
DEPENDENCY_DEFERRED_M3_PROVIDER = "dependency_deferred_m3_provider"
DEPENDENCY_DEFERRED_BROWSER_RUNTIME_EVIDENCE = "dependency_deferred_browser_runtime_evidence"
PASSING_NEGATIVE_CONTROL = "passing_negative_control"

_MANUAL_RECORD_KIND = "fault_recovery_evaluation_case"
_NEGATIVE_RECORD_KIND = "negative_control_observation"
_GOLD_KIND = "evaluator_only_fault_gold"
_MATRIX_FILE = "m2_closure_readiness.json"
_MANIFEST_FILE = "m2_closure_readiness_manifest.json"


class M2ClosureReadinessError(ValueError):
    """The evaluator-only M2 closure evidence is incomplete or unsafe."""


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: bytes) -> str:
    return sha256(value).hexdigest()


def _canonical_sha(value: object) -> str:
    return _sha(_json_bytes(value))


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise M2ClosureReadinessError(name + " must be non-empty text")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(item not in "0123456789abcdef" for item in value):
        raise M2ClosureReadinessError(name + " must be a lowercase SHA-256")
    return value


@dataclass(frozen=True)
class _Expectation:
    variant_id: str
    coverage_status: str
    dependency: str | None
    stage: str | None = None
    error_code: str | None = None
    repairable: bool | None = None
    outcome: str | None = None


_IMPLEMENTED = (
    _Expectation("d03_1_required_component_removal", IMPLEMENTED_MANUAL_FAULT, None, "page_spec", "page_spec_dangling_component_reference", False, "fallback_delivery"),
    _Expectation("d03_4a_ignored_evidence_misattribution", IMPLEMENTED_MANUAL_FAULT, None, "guidance/adoption", "ignored_evidence_misattributed_to_page_spec", False, "fallback_delivery"),
    _Expectation("d03_5_missing_evidence_adoption_relation", IMPLEMENTED_MANUAL_FAULT, None, "guidance/adoption", "inspector_trace_relation_integrity_mismatch", False, "fallback_delivery"),
    _Expectation("d03_6_dom_component_stable_id_tamper", IMPLEMENTED_MANUAL_FAULT, None, "render_binding", "dom_component_stable_id_mismatch", True, "recovered_success"),
    _Expectation("d03_7a_manifest_path_tamper", IMPLEMENTED_MANUAL_FAULT, None, "package", "package_manifest_path_mismatch", True, "recovered_success"),
    _Expectation("d03_7b_manifest_sha256_tamper", IMPLEMENTED_MANUAL_FAULT, None, "package", "package_manifest_sha256_mismatch", True, "recovered_success"),
)
_DEFERRED = (
    _Expectation("d03_2_wrong_target_state", DEPENDENCY_DEFERRED_SEMANTIC_EVIDENCE, "semantic_evidence"),
    _Expectation("d03_3_missing_error_retry_recovery_edge", DEPENDENCY_DEFERRED_SEMANTIC_EVIDENCE, "semantic_evidence"),
    _Expectation("d03_4b_forced_incorrect_adoption", DEPENDENCY_DEFERRED_SEMANTIC_EVIDENCE, "semantic_evidence"),
    _Expectation("d03_8_provider_generation", DEPENDENCY_DEFERRED_M3_PROVIDER, "m3_provider"),
    _Expectation("d03_9_provider_runtime", DEPENDENCY_DEFERRED_M3_PROVIDER, "m3_provider"),
    _Expectation("d03_10_browser_runtime", DEPENDENCY_DEFERRED_BROWSER_RUNTIME_EVIDENCE, "browser_runtime_evidence"),
)
_NEGATIVE_ID = "d09_passing_irrelevant_evidence_negative_control"


def _implemented(variant_id: str) -> _Expectation:
    result = tuple(item for item in _IMPLEMENTED if item.variant_id == variant_id)
    if len(result) != 1:
        raise M2ClosureReadinessError("unsupported implemented M2 variant")
    return result[0]


@dataclass(frozen=True)
class ManualFaultEvidenceBinding:
    """One complete externally rebound M2-07b case-report binding."""

    variant_id: str
    case_artifact_dir: Path
    case_bindings: Mapping[str, object]

    def validate_shape(self) -> None:
        _implemented(self.variant_id)
        if not isinstance(self.case_artifact_dir, Path) or not isinstance(self.case_bindings, Mapping):
            raise M2ClosureReadinessError("manual evidence binding has invalid paths or bindings")
        required = {
            "fault_copy_record", "injector_audit", "gold_manifest", "clean_source", "fragment_dir", "bundle_dir",
            "parity_report", "detector_report", "authorization", "recovery_outcome", "recovery_outcome_dir",
            "fallback_snapshot_dir", "fallback_record",
        }
        if set(self.case_bindings) != required:
            raise M2ClosureReadinessError("manual evidence binding must contain the complete M2-07b external inputs")


@dataclass(frozen=True)
class NegativeControlEvidenceBinding:
    """Evaluator-only D09 control binding; no sidecar is detector-visible."""

    artifact_dir: Path
    run: NegativeControlRun
    baseline_source: BlindedFaultBundleSource
    controlled_source: BlindedFaultBundleSource

    def validate_shape(self) -> None:
        if not isinstance(self.artifact_dir, Path) or not isinstance(self.run, NegativeControlRun):
            raise M2ClosureReadinessError("negative-control binding has invalid artifact/run types")
        if not isinstance(self.baseline_source, BlindedFaultBundleSource) or not isinstance(self.controlled_source, BlindedFaultBundleSource):
            raise M2ClosureReadinessError("negative-control binding has invalid source types")


@dataclass(frozen=True)
class M2ManualFaultRow:
    variant_id: str
    coverage_status: str
    case_id: str
    record_kind: str
    case_report_id: str
    case_report_sha256: str
    detector_stage: str
    detector_error_code: str
    gold_kind: str
    gold_repairable: bool
    final_outcome_status: str
    delivery_source: str
    manual_metric_eligible: bool

    def validate(self) -> None:
        expected = _implemented(self.variant_id)
        for field in ("variant_id", "coverage_status", "case_id", "record_kind", "case_report_id", "case_report_sha256", "detector_stage", "detector_error_code", "gold_kind", "final_outcome_status", "delivery_source"):
            _text(getattr(self, field), field)
        _hash(self.case_report_sha256, "case_report_sha256")
        if self.coverage_status != IMPLEMENTED_MANUAL_FAULT or self.record_kind != _MANUAL_RECORD_KIND:
            raise M2ClosureReadinessError("manual row has invalid record semantics")
        if self.gold_kind != _GOLD_KIND or not isinstance(self.gold_repairable, bool) or self.manual_metric_eligible is not True:
            raise M2ClosureReadinessError("manual row has invalid evaluator-only metric semantics")
        if (self.detector_stage, self.detector_error_code, self.gold_repairable, self.final_outcome_status) != (expected.stage, expected.error_code, expected.repairable, expected.outcome):
            raise M2ClosureReadinessError("manual row does not match the frozen coverage map")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class M2DeferredCoverageRow:
    variant_id: str
    coverage_status: str
    dependency_milestone: str

    def validate(self) -> None:
        expected = tuple(item for item in _DEFERRED if item.variant_id == self.variant_id)
        if len(expected) != 1:
            raise M2ClosureReadinessError("unsupported deferred M2 variant")
        if (self.coverage_status, self.dependency_milestone) != (expected[0].coverage_status, expected[0].dependency):
            raise M2ClosureReadinessError("deferred row must preserve its fixed reason/dependency")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class M2NegativeControlRow:
    variant_id: str
    coverage_status: str
    case_id: str
    record_kind: str
    spec_id: str
    spec_sha256: str
    report_id: str
    report_sha256: str
    detector_status: str
    manual_metric_eligible: bool

    def validate(self) -> None:
        for field in ("variant_id", "coverage_status", "case_id", "record_kind", "spec_id", "spec_sha256", "report_id", "report_sha256", "detector_status"):
            _text(getattr(self, field), field)
        _hash(self.spec_sha256, "spec_sha256")
        _hash(self.report_sha256, "report_sha256")
        if (self.variant_id, self.coverage_status, self.record_kind, self.detector_status, self.manual_metric_eligible) != (_NEGATIVE_ID, PASSING_NEGATIVE_CONTROL, _NEGATIVE_RECORD_KIND, NO_FAULT_DETECTED, False):
            raise M2ClosureReadinessError("negative-control row must remain clean and excluded from manual metrics")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)

@dataclass(frozen=True)
class M2ClosureReadinessMatrix:
    """M2 coverage evidence that can request review but never close the milestone."""

    matrix_id: str
    readiness_status: str
    manual_fault_rows: tuple[M2ManualFaultRow, ...]
    deferred_rows: tuple[M2DeferredCoverageRow, ...]
    negative_control_row: M2NegativeControlRow | None
    suite_id: str | None
    suite_sha256: str | None
    missing_coverage_ids: tuple[str, ...]
    schema_version: str = M2_CLOSURE_READINESS_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "deferred_rows": [item.to_dict() for item in self.deferred_rows],
            "manual_fault_rows": [item.to_dict() for item in self.manual_fault_rows],
            "missing_coverage_ids": list(self.missing_coverage_ids),
            "negative_control_row": None if self.negative_control_row is None else self.negative_control_row.to_dict(),
            "readiness_status": self.readiness_status,
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "suite_sha256": self.suite_sha256,
        }

    def validate(self) -> None:
        _text(self.matrix_id, "matrix_id")
        if self.schema_version != M2_CLOSURE_READINESS_SCHEMA_VERSION or self.readiness_status not in {READY_FOR_MANAGER_REVIEW, NOT_READY}:
            raise M2ClosureReadinessError("matrix has an invalid schema or status")
        if not isinstance(self.manual_fault_rows, tuple) or not isinstance(self.deferred_rows, tuple):
            raise M2ClosureReadinessError("matrix rows must be immutable tuples")
        for row in self.manual_fault_rows:
            if not isinstance(row, M2ManualFaultRow):
                raise M2ClosureReadinessError("matrix manual row has an invalid type")
            row.validate()
        for row in self.deferred_rows:
            if not isinstance(row, M2DeferredCoverageRow):
                raise M2ClosureReadinessError("matrix deferred row has an invalid type")
            row.validate()
        manual_ids = tuple(item.variant_id for item in self.manual_fault_rows)
        if manual_ids != tuple(sorted(manual_ids)) or len(manual_ids) != len(set(manual_ids)):
            raise M2ClosureReadinessError("matrix manual rows must be sorted and unique")
        deferred_ids = tuple(item.variant_id for item in self.deferred_rows)
        if deferred_ids != tuple(item.variant_id for item in _DEFERRED):
            raise M2ClosureReadinessError("matrix deferred rows must exactly preserve the frozen D03 map")
        if self.negative_control_row is not None:
            self.negative_control_row.validate()
        missing = self.missing_coverage_ids
        if not isinstance(missing, tuple) or missing != tuple(sorted(missing)) or len(missing) != len(set(missing)):
            raise M2ClosureReadinessError("missing coverage identifiers must be sorted and unique")
        known = {item.variant_id for item in _IMPLEMENTED} | {_NEGATIVE_ID}
        if any(item not in known for item in missing):
            raise M2ClosureReadinessError("matrix has an unknown missing coverage identifier")
        actual_missing = {item.variant_id for item in _IMPLEMENTED} - set(manual_ids)
        if self.negative_control_row is None:
            actual_missing.add(_NEGATIVE_ID)
        if tuple(sorted(actual_missing)) != missing:
            raise M2ClosureReadinessError("matrix missing coverage does not match its present rows")
        if (self.suite_id is None) != (self.suite_sha256 is None):
            raise M2ClosureReadinessError("suite identity must be all-or-nothing")
        if self.suite_id is not None:
            _text(self.suite_id, "suite_id")
            _hash(self.suite_sha256, "suite_sha256")
        if not missing and self.suite_id is None:
            raise M2ClosureReadinessError("complete M2 coverage requires an exact suite binding")
        expected_status = READY_FOR_MANAGER_REVIEW if not missing and self.suite_id is not None else NOT_READY
        if self.readiness_status != expected_status:
            raise M2ClosureReadinessError("matrix readiness status does not match available evidence")
        expected_id = "m2-closure-readiness-" + _canonical_sha(self.to_payload())[:20]
        if self.matrix_id != expected_id:
            raise M2ClosureReadinessError("matrix_id does not match canonical payload")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"matrix_id": self.matrix_id, **self.to_payload()}

    def sha256(self) -> str:
        return _canonical_sha(self.to_dict())

    def validate_against(self, manual_bindings: Iterable[ManualFaultEvidenceBinding], *, suite_artifact_dir: Path | None, negative_control_binding: NegativeControlEvidenceBinding | None) -> None:
        self.validate()
        rebound = _rebind_manual_rows(manual_bindings, negative_control_binding)
        expected_rows = tuple(sorted((row for row, _ in rebound), key=lambda item: item.variant_id))
        if tuple(item.to_dict() for item in self.manual_fault_rows) != tuple(item.to_dict() for item in expected_rows):
            raise M2ClosureReadinessError("matrix rows do not match externally rebound manual evidence")
        expected_negative = None if negative_control_binding is None else _rebind_negative_control(negative_control_binding)
        actual_negative = None if self.negative_control_row is None else self.negative_control_row.to_dict()
        wanted_negative = None if expected_negative is None else expected_negative.to_dict()
        if actual_negative != wanted_negative:
            raise M2ClosureReadinessError("matrix negative-control row does not match external evidence")
        if self.suite_id is None:
            if suite_artifact_dir is not None:
                raise M2ClosureReadinessError("not-ready matrix must not bind a suite artifact")
            return
        if suite_artifact_dir is None:
            raise M2ClosureReadinessError("ready matrix requires a suite artifact")
        suite = validate_fault_recovery_evaluation_suite_artifact(Path(suite_artifact_dir))
        reports = tuple(sorted((report for _, report in rebound), key=lambda item: (item.case_id, item.gold_manifest_id)))
        suite.validate_against(reports)
        if suite.negative_control_case_ids or suite.qwen_natural_error_case_ids:
            raise M2ClosureReadinessError("manual suite must not mix control or natural-error tables")
        if suite.suite_id != self.suite_id or suite.sha256() != self.suite_sha256:
            raise M2ClosureReadinessError("matrix suite reference does not bind the canonical M2-07b suite")


def build_m2_closure_readiness_matrix(manual_bindings: Iterable[ManualFaultEvidenceBinding], *, suite_artifact_dir: Path | None = None, negative_control_binding: NegativeControlEvidenceBinding | None = None) -> M2ClosureReadinessMatrix:
    """Build only from pre-existing evaluator artifacts; never declare M2 closed."""
    rebound = _rebind_manual_rows(manual_bindings, negative_control_binding)
    rows = tuple(sorted((row for row, _ in rebound), key=lambda item: item.variant_id))
    negative = None if negative_control_binding is None else _rebind_negative_control(negative_control_binding)
    missing = {item.variant_id for item in _IMPLEMENTED} - {item.variant_id for item in rows}
    if negative is None:
        missing.add(_NEGATIVE_ID)
    suite_id = suite_sha256 = None
    complete = not missing
    if suite_artifact_dir is not None:
        suite = validate_fault_recovery_evaluation_suite_artifact(Path(suite_artifact_dir))
        reports = tuple(sorted((report for _, report in rebound), key=lambda item: (item.case_id, item.gold_manifest_id)))
        suite.validate_against(reports)
        if suite.negative_control_case_ids or suite.qwen_natural_error_case_ids:
            raise M2ClosureReadinessError("manual suite must exclude negative-control and natural-error observations")
        if not complete:
            raise M2ClosureReadinessError("partial M2 coverage cannot bind a complete suite")
        suite_id, suite_sha256 = suite.suite_id, suite.sha256()
    elif complete:
        raise M2ClosureReadinessError("complete M2 coverage requires a canonical suite artifact")
    deferred = tuple(M2DeferredCoverageRow(item.variant_id, item.coverage_status, str(item.dependency)) for item in _DEFERRED)
    unsigned = M2ClosureReadinessMatrix("", READY_FOR_MANAGER_REVIEW if complete else NOT_READY, rows, deferred, negative, suite_id, suite_sha256, tuple(sorted(missing)))
    result = replace(unsigned, matrix_id="m2-closure-readiness-" + _canonical_sha(unsigned.to_payload())[:20])
    result.validate()
    return result


def write_m2_closure_readiness_matrix(matrix: M2ClosureReadinessMatrix, output_dir: Path, manual_bindings: Iterable[ManualFaultEvidenceBinding], *, suite_artifact_dir: Path | None = None, negative_control_binding: NegativeControlEvidenceBinding | None = None) -> M2ClosureReadinessMatrix:
    if not isinstance(matrix, M2ClosureReadinessMatrix):
        raise M2ClosureReadinessError("matrix has an invalid type")
    matrix.validate_against(manual_bindings, suite_artifact_dir=suite_artifact_dir, negative_control_binding=negative_control_binding)
    _write_matrix_artifact(Path(output_dir), matrix)
    return matrix


def validate_m2_closure_readiness_artifact(output_dir: Path, manual_bindings: Iterable[ManualFaultEvidenceBinding], *, suite_artifact_dir: Path | None = None, negative_control_binding: NegativeControlEvidenceBinding | None = None) -> M2ClosureReadinessMatrix:
    files = _read_matrix_artifact(Path(output_dir))
    matrix = _matrix_from_dict(_load_canonical_object(files[_MATRIX_FILE], _MATRIX_FILE))
    matrix.validate_against(manual_bindings, suite_artifact_dir=suite_artifact_dir, negative_control_binding=negative_control_binding)
    return matrix


def _rebind_manual_rows(bindings: Iterable[ManualFaultEvidenceBinding], negative_binding: NegativeControlEvidenceBinding | None) -> tuple[tuple[M2ManualFaultRow, FaultRecoveryEvaluationCaseReport], ...]:
    supplied = tuple(bindings)
    if len({item.variant_id for item in supplied}) != len(supplied):
        raise M2ClosureReadinessError("manual evidence bindings must not repeat a variant")
    result = []
    for binding in supplied:
        binding.validate_shape()
        try:
            report = validate_fault_recovery_evaluation_case_artifact(binding.case_artifact_dir, **dict(binding.case_bindings))
        except (FaultRecoveryEvaluationError, TypeError, ValueError) as error:
            raise M2ClosureReadinessError("manual report failed complete external rebinding") from error
        expected = _implemented(binding.variant_id)
        _validate_report(report, expected)
        if binding.variant_id == "d03_4a_ignored_evidence_misattribution":
            if negative_binding is None:
                raise M2ClosureReadinessError("D03-4a requires an explicit passed-control wrapper binding")
            _validate_misattribution_wrapper(binding, negative_binding)
        row = M2ManualFaultRow(binding.variant_id, IMPLEMENTED_MANUAL_FAULT, report.case_id, _MANUAL_RECORD_KIND, report.report_id, report.sha256(), report.predicted_stage, report.predicted_error_code, _GOLD_KIND, report.gold_repairable, report.final_outcome_status, report.delivery_source, True)
        row.validate()
        result.append((row, report))
    return tuple(result)


def _validate_report(report: FaultRecoveryEvaluationCaseReport, expected: _Expectation) -> None:
    report.validate()
    values = (report.gold_stage, report.gold_error, report.gold_repairable, report.predicted_stage, report.predicted_error_code, report.final_outcome_status)
    if not report.stage_match or not report.error_match or not report.fault_localization_exact or not report.repairability_match or values != (expected.stage, expected.error_code, expected.repairable, expected.stage, expected.error_code, expected.outcome):
        raise M2ClosureReadinessError("externally rebound report does not satisfy frozen M2 semantics")
    if expected.repairable:
        if report.delivery_source != "repaired_g0_v2" or report.one_repair_success != "success":
            raise M2ClosureReadinessError("repairable row lacks one successful deterministic repair")
    elif report.delivery_source != "g0_frozen_fallback" or report.fallback_delivery_status != "success":
        raise M2ClosureReadinessError("non-repairable row lacks a verified frozen G0 fallback")


def _rebind_negative_control(binding: NegativeControlEvidenceBinding) -> M2NegativeControlRow:
    binding.validate_shape()
    try:
        report = validate_negative_control_artifact(binding.artifact_dir, binding.run.baseline_context)
        binding.run.spec.validate()
        binding.run.report.validate_against_spec(binding.run.spec)
        binding.baseline_source.validate()
        binding.controlled_source.validate()
    except (TypeError, ValueError) as error:
        raise M2ClosureReadinessError("negative-control spec/report failed canonical rebinding") from error
    if report.to_dict() != binding.run.report.to_dict() or report.status != PASSED:
        raise M2ClosureReadinessError("negative-control artifact is not the passed registered control")
    if binding.baseline_source.case_id != binding.run.spec.case_id or binding.controlled_source.case_id != binding.run.spec.case_id:
        raise M2ClosureReadinessError("negative-control source case does not match its spec")
    if binding.controlled_source.page_spec.to_dict() != binding.run.controlled_guided_build.page_spec.to_dict():
        raise M2ClosureReadinessError("controlled source does not bind the passed control PageSpec")
    _fresh_control_detector(binding)
    row = M2NegativeControlRow(_NEGATIVE_ID, PASSING_NEGATIVE_CONTROL, report.case_id, _NEGATIVE_RECORD_KIND, binding.run.spec.spec_id, binding.run.spec.sha256(), report.report_id, report.sha256(), NO_FAULT_DETECTED, False)
    row.validate()
    return row


def _fresh_control_detector(binding: NegativeControlEvidenceBinding) -> None:
    """Reassemble both G0 copies; sidecars never enter the detector-visible tree."""
    root = _create_rebind_root(binding.artifact_dir.parent, "control")
    try:
        baseline = assemble_blinded_fault_bundle(binding.baseline_source, root / "baseline")
        controlled = assemble_blinded_fault_bundle(binding.controlled_source, root / "controlled")
        parity = validate_blinded_bundle_inventory_parity((baseline, controlled))
        fresh = detect_blinded_fault_bundle(root / "controlled", parity)
        if fresh.status != NO_FAULT_DETECTED or fresh.findings:
            raise M2ClosureReadinessError("passed negative control was classified as a fault")
        names = {item.relative_to(root / "controlled").as_posix() for item in (root / "controlled").rglob("*") if item.is_file()}
        if any("negative_control" in item or "fault_gold" in item or "injector_audit" in item for item in names):
            raise M2ClosureReadinessError("evaluator-side control material leaked into detector bundle")
    finally:
        _remove_rebind_root(root, binding.artifact_dir.parent, ".m2-closure-rebind-control-")


def _validate_misattribution_wrapper(binding: ManualFaultEvidenceBinding, negative_binding: NegativeControlEvidenceBinding) -> None:
    _rebind_negative_control(negative_binding)
    record = binding.case_bindings.get("fault_copy_record")
    audit = binding.case_bindings.get("injector_audit")
    if not isinstance(record, FaultCopyRecord) or audit is None:
        raise M2ClosureReadinessError("D03-4a lacks a canonical fault-copy/audit binding")
    source_ref_id = getattr(audit, "exact_target_id", None)
    if record.case_id != negative_binding.run.spec.case_id or getattr(audit, "case_id", None) != record.case_id:
        raise M2ClosureReadinessError("D03-4a cannot use case identity as a substitute for wrapper provenance")
    if not isinstance(source_ref_id, str) or (not isinstance(getattr(audit, "changed_path", None), str) or "field_path=page.title" not in getattr(audit, "changed_path")):
        raise M2ClosureReadinessError("D03-4a is not the frozen title misattribution fixture")
    source = negative_binding.controlled_source
    root = _create_rebind_root(negative_binding.artifact_dir.parent, "misattribution")
    try:
        rebuilt = build_passing_negative_control_ignored_evidence_misattribution_fault_copy(
            run=negative_binding.run, inspector_fact_set=source.inspector_fact_set, page_spec=source.page_spec,
            source_ref_id=source_ref_id, page_spec_entity_id=source.page_spec.page_id,
            page_spec_field_name="title", output_dir=root / "fragment",
        )
        rebuilt.validate()
        if rebuilt.runtime_record.to_dict() != record.to_dict() or getattr(audit, "mutation_request_sha256", None) != rebuilt.mutation_request.sha256():
            raise M2ClosureReadinessError("D03-4a fault is not reproducibly bound to the passed-control wrapper")
    finally:
        _remove_rebind_root(root, negative_binding.artifact_dir.parent, ".m2-closure-rebind-misattribution-")


def _create_rebind_root(parent: Path, label: str) -> Path:
    root_parent = Path(parent).absolute()
    if not root_parent.exists() or root_parent.is_symlink() or not root_parent.is_dir():
        raise M2ClosureReadinessError("rebind parent must be an existing real directory")
    _reject_symlinks(root_parent)
    root = root_parent / (".m2-closure-rebind-" + label + "-" + uuid4().hex)
    root.mkdir(parents=False, exist_ok=False)
    return root


def _remove_rebind_root(root: Path, parent: Path, prefix: str) -> None:
    if not root.exists():
        return
    expected_parent = Path(parent).absolute()
    if root.parent != expected_parent or not root.name.startswith(prefix) or root.is_symlink() or not root.is_dir():
        raise M2ClosureReadinessError("refusing unsafe evaluator rebind cleanup")
    _reject_symlinks(root)
    if any(item.is_symlink() for item in root.rglob("*")):
        raise M2ClosureReadinessError("refusing evaluator rebind cleanup with symlink content")
    shutil.rmtree(root)


def _matrix_from_dict(payload: dict[str, object]) -> M2ClosureReadinessMatrix:
    expected = {"matrix_id", "readiness_status", "manual_fault_rows", "deferred_rows", "negative_control_row", "suite_id", "suite_sha256", "missing_coverage_ids", "schema_version"}
    if set(payload) != expected:
        raise M2ClosureReadinessError("matrix has an unexpected field set")
    manual, deferred, negative, missing = payload["manual_fault_rows"], payload["deferred_rows"], payload["negative_control_row"], payload["missing_coverage_ids"]
    if not isinstance(manual, list) or not all(isinstance(item, dict) for item in manual):
        raise M2ClosureReadinessError("matrix manual rows must be objects")
    if not isinstance(deferred, list) or not all(isinstance(item, dict) for item in deferred):
        raise M2ClosureReadinessError("matrix deferred rows must be objects")
    if negative is not None and not isinstance(negative, dict):
        raise M2ClosureReadinessError("matrix negative row must be an object or null")
    if not isinstance(missing, list) or not all(isinstance(item, str) for item in missing):
        raise M2ClosureReadinessError("matrix missing coverage must be text")
    try:
        result = M2ClosureReadinessMatrix(
            matrix_id=payload["matrix_id"], readiness_status=payload["readiness_status"],
            manual_fault_rows=tuple(M2ManualFaultRow(**item) for item in manual),
            deferred_rows=tuple(M2DeferredCoverageRow(**item) for item in deferred),
            negative_control_row=None if negative is None else M2NegativeControlRow(**negative),
            suite_id=payload["suite_id"], suite_sha256=payload["suite_sha256"],
            missing_coverage_ids=tuple(missing), schema_version=payload["schema_version"],
        )
    except TypeError as error:
        raise M2ClosureReadinessError("matrix payload has invalid row fields") from error
    result.validate()
    return result


def _load_canonical_object(content: bytes, name: str) -> dict[str, object]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise M2ClosureReadinessError(name + " must be UTF-8 JSON") from error
    if not isinstance(payload, dict) or _json_bytes(payload) != content:
        raise M2ClosureReadinessError(name + " must contain exact canonical JSON bytes")
    return payload


def _write_matrix_artifact(output_dir: Path, matrix: M2ClosureReadinessMatrix) -> None:
    destination, staging = _prepare_output(output_dir)
    matrix_bytes = _json_bytes(matrix.to_dict())
    manifest_bytes = _json_bytes({
        "schema_version": M2_CLOSURE_READINESS_MANIFEST_SCHEMA_VERSION,
        "matrix_id": matrix.matrix_id,
        "matrix_sha256": matrix.sha256(),
        "files": [{"path": _MATRIX_FILE, "size": len(matrix_bytes), "sha256": _sha(matrix_bytes)}],
    })
    expected_files = {_MATRIX_FILE: matrix_bytes, _MANIFEST_FILE: manifest_bytes}
    committed = False
    try:
        staging.mkdir(parents=False, exist_ok=False)
        _write_bytes(staging / _MATRIX_FILE, matrix_bytes)
        _write_bytes(staging / _MANIFEST_FILE, manifest_bytes)
        _validate_static_artifact(staging)
        staging.rename(destination)
        committed = True
        _validate_static_artifact(destination)
    except Exception as write_error:
        _remove_owned(staging, destination.parent, staging.name, complete=False)
        if committed:
            try:
                _remove_owned(
                    destination, destination.parent, destination.name, complete=True,
                    expected_files=expected_files,
                )
            except M2ClosureReadinessError as cleanup_error:
                raise M2ClosureReadinessError(
                    "post-commit matrix ownership could not be verified; destination retained"
                ) from cleanup_error
        raise write_error


def _read_matrix_artifact(output_dir: Path) -> dict[str, bytes]:
    root = Path(output_dir)
    _validate_static_artifact(root)
    return {name: (root / name).read_bytes() for name in (_MATRIX_FILE, _MANIFEST_FILE)}


def _validate_static_artifact(root: Path) -> M2ClosureReadinessMatrix:
    if not root.exists() or root.is_symlink() or not root.is_dir():
        raise M2ClosureReadinessError("matrix artifact directory must be a real directory")
    _reject_symlinks(root)
    children = tuple(sorted(root.iterdir(), key=lambda item: item.name))
    if any(item.is_symlink() or not item.is_file() for item in children) or tuple(item.name for item in children) != (_MATRIX_FILE, _MANIFEST_FILE):
        raise M2ClosureReadinessError("matrix artifact must contain exactly two regular files")
    matrix_bytes = (root / _MATRIX_FILE).read_bytes()
    matrix = _matrix_from_dict(_load_canonical_object(matrix_bytes, _MATRIX_FILE))
    manifest = _load_canonical_object((root / _MANIFEST_FILE).read_bytes(), _MANIFEST_FILE)
    if set(manifest) != {"schema_version", "matrix_id", "matrix_sha256", "files"} or manifest["schema_version"] != M2_CLOSURE_READINESS_MANIFEST_SCHEMA_VERSION:
        raise M2ClosureReadinessError("matrix manifest has invalid schema")
    if manifest["matrix_id"] != matrix.matrix_id or manifest["matrix_sha256"] != matrix.sha256():
        raise M2ClosureReadinessError("matrix manifest does not bind matrix identity")
    if manifest["files"] != [{"path": _MATRIX_FILE, "size": len(matrix_bytes), "sha256": _sha(matrix_bytes)}]:
        raise M2ClosureReadinessError("matrix manifest has invalid exact file inventory")
    return matrix


def _prepare_output(output_dir: Path) -> tuple[Path, Path]:
    raw = Path(output_dir)
    if any(part in {"", ".", ".."} for part in raw.parts):
        raise M2ClosureReadinessError("matrix output path must not contain traversal")
    destination = raw.absolute()
    parent = destination.parent
    if not parent.exists() or parent.is_symlink() or not parent.is_dir():
        raise M2ClosureReadinessError("matrix output parent must be an existing real directory")
    _reject_symlinks(parent)
    _reject_symlinks(destination)
    if destination.exists():
        raise M2ClosureReadinessError("matrix output directory already exists")
    staging = parent / ("." + destination.name + ".staging-" + uuid4().hex)
    if staging.exists():
        raise M2ClosureReadinessError("matrix staging directory already exists")
    return destination, staging


def _remove_owned(
    path: Path,
    parent: Path,
    name: str,
    complete: bool,
    *,
    expected_files: Mapping[str, bytes] | None = None,
) -> None:
    if not path.exists():
        return
    if path.parent != parent or path.name != name or path.is_symlink() or not path.is_dir():
        raise M2ClosureReadinessError("refusing unsafe matrix cleanup")
    _reject_symlinks(path)
    children = tuple(path.iterdir())
    if any(item.is_symlink() or item.is_dir() or not item.is_file() for item in children):
        raise M2ClosureReadinessError("refusing unexpected matrix cleanup")
    names = {item.name for item in children}
    allowed = {_MATRIX_FILE, _MANIFEST_FILE}
    if not names.issubset(allowed) or complete and names != allowed:
        raise M2ClosureReadinessError("refusing unrecognized matrix cleanup")
    if expected_files is not None:
        if names != set(expected_files):
            raise M2ClosureReadinessError("matrix cleanup ownership inventory does not match this write")
        for file_name, expected_bytes in expected_files.items():
            if (path / file_name).read_bytes() != expected_bytes:
                raise M2ClosureReadinessError("matrix cleanup ownership bytes do not match this write")
    shutil.rmtree(path)


def _write_bytes(path: Path, content: bytes) -> None:
    _reject_symlinks(path.parent)
    if path.exists() or path.is_symlink():
        raise M2ClosureReadinessError("matrix writer refuses overwrite")
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise M2ClosureReadinessError("matrix writer did not preserve exact bytes")


def _reject_symlinks(path: Path) -> None:
    for item in (path.absolute(), *path.absolute().parents):
        if item.exists() and item.is_symlink():
            raise M2ClosureReadinessError("symlinked matrix paths are not allowed")
