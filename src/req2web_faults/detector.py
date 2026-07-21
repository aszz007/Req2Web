"""Read-only deterministic fault detection over parity-validated M2 bundles.

The production entry accepts only an on-disk blinded bundle plus its aggregate
inventory-parity report. It never accepts injection-side or evaluator-side
artifacts and it does not perform repair or delivery orchestration.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from req2web_acceptance import (
    ACCEPTANCE_BINDING_SCHEMA_VERSION,
    ACCEPTANCE_PLAN_SCHEMA_VERSION,
    EXECUTABLE_STEP_PLAN_SCHEMA_VERSION,
    INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION,
)
from req2web_generation import (
    PAGE_SPEC_SCHEMA_VERSION,
    RENDER_MANIFEST_SCHEMA_VERSION,
    RESULT_PACKAGE_V2_SCHEMA_VERSION,
    AffectedPageSpecField,
    GuidanceDecision,
    GuidanceItem,
    GuidanceSource,
    GuidedPageSpecBuildResult,
    InfluenceCheck,
    RetrievalGuidance,
    RetrievalInfluenceReport,
    RoleAblation,
    StructuralDifference,
    UseCaseGuidanceTrace,
)
from req2web_generation.result_package_v2 import (
    RetrievalEnhancedResultPackage,
    RetrievalEnhancedResultPackageError,
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
from req2web_inspector.facts import (
    INSPECTOR_FACT_SET_SCHEMA_VERSION,
    InspectorFact,
    InspectorFactError,
    InspectorFactSet,
    InspectorInfluenceCheckSummary,
    InspectorSourceRef,
    InspectorTraceLink,
    project_g0_inspector_facts,
)

from .bundle import (
    BLINDED_FAULT_BUNDLE_SCHEMA_VERSION,
    BlindedBundleInventoryParityReport,
    BlindedFaultBundleRecord,
    FaultBundleError,
    load_blinded_fault_bundle,
)


FAULT_DETECTION_REPORT_SCHEMA_VERSION = "req2web.fault_detection_report.v1"
FAULT_DETECTION_REPORT_MANIFEST_SCHEMA_VERSION = "req2web.fault_detection_report_manifest.v1"

NO_FAULT_DETECTED = "no_fault_detected"
FAULT_DETECTED = "fault_detected"
UNCLASSIFIED_FAILURE = "unclassified_failure"
AMBIGUOUS_MULTIPLE_FAULTS = "ambiguous_multiple_faults"
FAULT_DETECTION_STATUSES = (
    NO_FAULT_DETECTED,
    FAULT_DETECTED,
    UNCLASSIFIED_FAILURE,
    AMBIGUOUS_MULTIPLE_FAULTS,
)

PAGE_SPEC_DANGLING_COMPONENT_REFERENCE = "page_spec_dangling_component_reference"
INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH = "inspector_trace_relation_integrity_mismatch"
DOM_COMPONENT_STABLE_ID_MISMATCH = "dom_component_stable_id_mismatch"
PACKAGE_MANIFEST_PATH_MISMATCH = "package_manifest_path_mismatch"
PACKAGE_MANIFEST_SHA256_MISMATCH = "package_manifest_sha256_mismatch"

_CLASSIFIED_ERROR_STAGES = {
    PAGE_SPEC_DANGLING_COMPONENT_REFERENCE: "page_spec",
    INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH: "guidance/adoption",
    DOM_COMPONENT_STABLE_ID_MISMATCH: "render_binding",
    PACKAGE_MANIFEST_PATH_MISMATCH: "package",
    PACKAGE_MANIFEST_SHA256_MISMATCH: "package",
}
_D03_STAGES = frozenset({
    "input_context",
    "retrieval",
    "guidance/adoption",
    "provider_generation",
    "provider_runtime",
    "page_spec",
    "render_binding",
    "browser_runtime",
    "acceptance",
    "package",
})
_ADMINISTRATIVE_STAGES = frozenset({"none", "unclassified", "multiple"})
_REPORT_FILE = "fault_detection_report.json"
_REPORT_MANIFEST_FILE = "fault_detection_report_manifest.json"

_V2_ROLES = {
    "page/index.html": "browser_entrypoint",
    "page/styles.css": "page_styles",
    "page/app.js": "page_script",
    "page/render_manifest.json": "render_manifest",
    "internal/agent_context.json": "agent_context",
    "internal/retrieval_guidance.json": "retrieval_guidance",
    "internal/guided_page_spec_build_result.json": "guided_page_spec_build_result",
    "internal/page_spec.json": "guided_page_spec",
    "internal/consistency_report.json": "consistency_report",
    "internal/retrieval_influence_report.json": "retrieval_influence_report",
    "result_summary.json": "result_summary",
}


class FaultDetectionError(ValueError):
    """The detector input, parity gate, report, or writer failed closed."""


@dataclass(frozen=True)
class FaultDetectionFinding:
    """One mechanically classified root finding from detector-visible bytes."""

    finding_id: str
    stage: str
    error_code: str
    related_identifiers: tuple[str, ...]
    related_paths: tuple[str, ...]
    expected: tuple[tuple[str, str], ...]
    actual: tuple[tuple[str, str], ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "actual": _detail_dicts(self.actual),
            "error_code": self.error_code,
            "expected": _detail_dicts(self.expected),
            "related_identifiers": list(self.related_identifiers),
            "related_paths": list(self.related_paths),
            "stage": self.stage,
        }

    def validate(self) -> None:
        _require_text(self.finding_id, "finding_id")
        if self.error_code not in _CLASSIFIED_ERROR_STAGES:
            raise FaultDetectionError("finding error_code is not mechanically classified")
        if self.stage != _CLASSIFIED_ERROR_STAGES[self.error_code]:
            raise FaultDetectionError("finding stage does not match error_code")
        _validate_sorted_text_tuple(self.related_identifiers, "related_identifiers")
        _validate_path_tuple(self.related_paths, "related_paths")
        _validate_details(self.expected, "expected")
        _validate_details(self.actual, "actual")
        if not self.related_identifiers and not self.related_paths:
            raise FaultDetectionError("finding must identify a related artifact or path")
        expected_id = "fault-finding-" + _canonical_sha256(self.to_payload())[:20]
        if self.finding_id != expected_id:
            raise FaultDetectionError("finding_id does not match canonical payload")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"finding_id": self.finding_id, **self.to_payload()}


@dataclass(frozen=True)
class FaultDetectionDiagnostic:
    """One structurally visible failure outside the approved classifier."""

    diagnostic_id: str
    code: str
    related_identifiers: tuple[str, ...]
    related_paths: tuple[str, ...]
    expected: tuple[tuple[str, str], ...]
    actual: tuple[tuple[str, str], ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "actual": _detail_dicts(self.actual),
            "code": self.code,
            "expected": _detail_dicts(self.expected),
            "related_identifiers": list(self.related_identifiers),
            "related_paths": list(self.related_paths),
        }

    def validate(self) -> None:
        _require_text(self.diagnostic_id, "diagnostic_id")
        _require_text(self.code, "code")
        _validate_sorted_text_tuple(self.related_identifiers, "related_identifiers")
        _validate_path_tuple(self.related_paths, "related_paths")
        _validate_details(self.expected, "expected")
        _validate_details(self.actual, "actual")
        if not self.related_identifiers and not self.related_paths:
            raise FaultDetectionError("diagnostic must identify a related artifact or path")
        expected_id = "fault-diagnostic-" + _canonical_sha256(self.to_payload())[:20]
        if self.diagnostic_id != expected_id:
            raise FaultDetectionError("diagnostic_id does not match canonical payload")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"diagnostic_id": self.diagnostic_id, **self.to_payload()}


@dataclass(frozen=True)
class FaultDetectionReport:
    """Canonical, read-only D03 prediction over one blinded bundle."""

    report_id: str
    bundle_id: str
    bundle_record_sha256: str
    bundle_schema_version: str
    bundle_inventory_sha256: str
    parity_schema_version: str
    parity_path_set_sha256: str
    case_id: str
    page_id: str
    status: str
    predicted_stage: str
    predicted_error_code: str
    related_identifiers: tuple[str, ...]
    related_paths: tuple[str, ...]
    expected: tuple[tuple[str, str], ...]
    actual: tuple[tuple[str, str], ...]
    predicted_repairable: bool
    predicted_repair_scope: tuple[str, ...]
    fallback_recommendation: str
    findings: tuple[FaultDetectionFinding, ...]
    diagnostics: tuple[FaultDetectionDiagnostic, ...]
    schema_version: str = FAULT_DETECTION_REPORT_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "actual": _detail_dicts(self.actual),
            "bundle_id": self.bundle_id,
            "bundle_inventory_sha256": self.bundle_inventory_sha256,
            "bundle_record_sha256": self.bundle_record_sha256,
            "bundle_schema_version": self.bundle_schema_version,
            "case_id": self.case_id,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "expected": _detail_dicts(self.expected),
            "fallback_recommendation": self.fallback_recommendation,
            "findings": [item.to_dict() for item in self.findings],
            "page_id": self.page_id,
            "parity_path_set_sha256": self.parity_path_set_sha256,
            "parity_schema_version": self.parity_schema_version,
            "predicted_error_code": self.predicted_error_code,
            "predicted_repair_scope": list(self.predicted_repair_scope),
            "predicted_repairable": self.predicted_repairable,
            "predicted_stage": self.predicted_stage,
            "related_identifiers": list(self.related_identifiers),
            "related_paths": list(self.related_paths),
            "schema_version": self.schema_version,
            "status": self.status,
        }

    def validate(self) -> None:
        for name in (
            "report_id", "bundle_id", "bundle_schema_version",
            "parity_schema_version", "case_id", "page_id", "status",
            "predicted_stage", "predicted_error_code", "fallback_recommendation",
        ):
            _require_text(getattr(self, name), name)
        if self.schema_version != FAULT_DETECTION_REPORT_SCHEMA_VERSION:
            raise FaultDetectionError("unsupported fault-detection report schema")
        if self.bundle_schema_version != BLINDED_FAULT_BUNDLE_SCHEMA_VERSION:
            raise FaultDetectionError("report binds an unsupported bundle schema")
        if self.status not in FAULT_DETECTION_STATUSES:
            raise FaultDetectionError("unsupported fault-detection status")
        if self.predicted_stage not in _D03_STAGES | _ADMINISTRATIVE_STAGES:
            raise FaultDetectionError("unsupported predicted_stage")
        for name in ("bundle_record_sha256", "bundle_inventory_sha256", "parity_path_set_sha256"):
            _require_sha256(getattr(self, name), name)
        if not isinstance(self.predicted_repairable, bool):
            raise FaultDetectionError("predicted_repairable must be a boolean")
        _validate_path_tuple(self.predicted_repair_scope, "predicted_repair_scope")
        _validate_sorted_text_tuple(self.related_identifiers, "related_identifiers")
        _validate_path_tuple(self.related_paths, "related_paths")
        _validate_details(self.expected, "expected")
        _validate_details(self.actual, "actual")
        if tuple(sorted(self.findings, key=_finding_order_key)) != self.findings:
            raise FaultDetectionError("findings must use canonical order")
        if tuple(sorted(self.diagnostics, key=_diagnostic_order_key)) != self.diagnostics:
            raise FaultDetectionError("diagnostics must use canonical order")
        for item in self.findings:
            item.validate()
        for item in self.diagnostics:
            item.validate()
        summary = _derive_report_summary(self.findings, self.diagnostics)
        for name, expected_value in summary.items():
            if getattr(self, name) != expected_value:
                raise FaultDetectionError(f"report {name} does not match findings and diagnostics")
        expected_id = "fault-detection-report-" + _canonical_sha256(self.to_payload())[:20]
        if self.report_id != expected_id:
            raise FaultDetectionError("report_id does not match canonical payload")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"report_id": self.report_id, **self.to_payload()}

    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())

def detect_blinded_fault_bundle(
    bundle_dir: Path,
    parity_report: BlindedBundleInventoryParityReport,
) -> FaultDetectionReport:
    """Detect approved mechanical failures after mandatory bundle/parity gates."""
    if not isinstance(parity_report, BlindedBundleInventoryParityReport):
        raise FaultDetectionError("parity_report must be BlindedBundleInventoryParityReport")
    try:
        bundle_record = load_blinded_fault_bundle(Path(bundle_dir))
        parity_report.validate()
    except (FaultBundleError, ValueError, TypeError) as error:
        raise FaultDetectionError("bundle or parity validation failed") from error
    _validate_parity_binding(bundle_record, parity_report)
    files = _read_bundle_artifacts(Path(bundle_dir), bundle_record)
    findings, diagnostics = _inspect_bundle(bundle_record, files, Path(bundle_dir))
    return _make_report(bundle_record, parity_report, findings, diagnostics)


def write_fault_detection_report(
    report: FaultDetectionReport,
    output_dir: Path,
) -> FaultDetectionReport:
    """Write canonical report and integrity manifest into one empty directory."""
    if not isinstance(report, FaultDetectionReport):
        raise FaultDetectionError("report must be FaultDetectionReport")
    report.validate()
    destination = _prepare_empty_output_directory(Path(output_dir))
    report_bytes = _canonical_json_bytes(report.to_dict())
    _write_exact_bytes(destination / _REPORT_FILE, report_bytes)
    manifest = {
        "files": [{
            "path": _REPORT_FILE,
            "sha256": sha256(report_bytes).hexdigest(),
            "size": len(report_bytes),
        }],
        "report_id": report.report_id,
        "report_sha256": report.sha256(),
        "schema_version": FAULT_DETECTION_REPORT_MANIFEST_SCHEMA_VERSION,
    }
    manifest_bytes = _canonical_json_bytes(manifest)
    _write_exact_bytes(destination / _REPORT_MANIFEST_FILE, manifest_bytes)
    actual = {
        path.name: path.read_bytes()
        for path in sorted(destination.iterdir())
        if path.is_file()
    }
    if actual != {_REPORT_FILE: report_bytes, _REPORT_MANIFEST_FILE: manifest_bytes}:
        raise FaultDetectionError("fault-detection writer did not round-trip exact bytes")
    if _load_json_object(actual[_REPORT_MANIFEST_FILE], _REPORT_MANIFEST_FILE) != manifest:
        raise FaultDetectionError("fault-detection manifest did not round-trip")
    if sha256(actual[_REPORT_FILE]).hexdigest() != manifest["files"][0]["sha256"]:
        raise FaultDetectionError("fault-detection report hash did not round-trip")
    return report


def _inspect_bundle(
    bundle_record: BlindedFaultBundleRecord,
    files: Mapping[str, bytes],
    bundle_dir: Path,
) -> tuple[tuple[FaultDetectionFinding, ...], tuple[FaultDetectionDiagnostic, ...]]:
    findings: list[FaultDetectionFinding] = []
    diagnostics: list[FaultDetectionDiagnostic] = []

    page_path = "artifact/page_spec.json"
    page_payload = _decode_json_object(files, page_path, diagnostics)
    page_object, page_finding, missing_component_ids = _inspect_page_spec(
        page_payload, bundle_record, page_path, diagnostics
    )
    if page_finding is not None:
        findings.append(page_finding)

    inspector_path = "artifact/inspector_fact_set.json"
    inspector_payload = _decode_json_object(files, inspector_path, diagnostics)
    page_reference_root_detected = bool(missing_component_ids)
    inspector_finding = _inspect_inspector(
        inspector_payload,
        bundle_record,
        files,
        page_payload,
        page_reference_root_detected,
        inspector_path,
        diagnostics,
    )
    if inspector_finding is not None:
        findings.append(inspector_finding)

    render_finding = _inspect_render(
        files,
        bundle_record,
        page_payload,
        missing_component_ids,
        page_reference_root_detected,
        diagnostics,
    )
    if render_finding is not None:
        findings.append(render_finding)

    package_findings, package_payload = _inspect_result_package(
        files, bundle_record, bundle_dir, diagnostics
    )
    findings.extend(package_findings)

    _inspect_acceptance_chain(
        files,
        bundle_record,
        page_payload,
        page_reference_root_detected,
        render_finding is not None,
        diagnostics,
    )
    _inspect_package_mirrors(
        files,
        page_payload,
        page_reference_root_detected,
        render_finding is not None,
        package_payload,
        diagnostics,
    )

    if (
        page_payload is not None
        and page_object is None
        and page_finding is None
        and not missing_component_ids
    ):
        diagnostics.append(_make_diagnostic(
            "page_spec_object_unavailable",
            related_paths=(page_path,),
            expected={"page_spec_object": "validated"},
            actual={"page_spec_object": "unavailable"},
        ))

    unique_findings = {item.finding_id: item for item in findings}
    unique_diagnostics = {item.diagnostic_id: item for item in diagnostics}
    return (
        tuple(sorted(unique_findings.values(), key=_finding_order_key)),
        tuple(sorted(unique_diagnostics.values(), key=_diagnostic_order_key)),
    )


def _inspect_page_spec(
    payload: dict[str, Any] | None,
    bundle_record: BlindedFaultBundleRecord,
    path: str,
    diagnostics: list[FaultDetectionDiagnostic],
) -> tuple[PageSpec | None, FaultDetectionFinding | None, tuple[str, ...]]:
    if payload is None:
        return None, None, ()
    if payload.get("schema_version") != PAGE_SPEC_SCHEMA_VERSION:
        diagnostics.append(_make_diagnostic(
            "page_spec_schema_failure",
            related_paths=(path,),
            expected={"schema_version": PAGE_SPEC_SCHEMA_VERSION},
            actual={"schema_version": payload.get("schema_version")},
        ))
        return None, None, ()
    if payload.get("page_id") != bundle_record.page_id:
        diagnostics.append(_make_diagnostic(
            "page_spec_identity_failure",
            related_paths=(path,),
            expected={"page_id": bundle_record.page_id},
            actual={"page_id": payload.get("page_id")},
        ))

    missing_map: dict[str, list[str]] = {}
    component_ids = _unique_object_ids(payload.get("components"), "component_id")
    if component_ids is None:
        diagnostics.append(_make_diagnostic(
            "page_spec_component_inventory_failure",
            related_paths=(path,),
            expected={"components": "unique component_id objects"},
            actual={"components": "malformed"},
        ))
    else:
        valid_components = set(component_ids)
        reference_specs = (
            ("sections", "section_id", "component_ids"),
            ("states", "state_id", "visible_component_ids"),
            ("interactions", "interaction_id", "trigger_component_id"),
        )
        for collection, identity_key, reference_key in reference_specs:
            values = payload.get(collection)
            if not isinstance(values, list):
                diagnostics.append(_make_diagnostic(
                    "page_spec_reference_inventory_failure",
                    related_paths=(path,),
                    expected={collection: "list"},
                    actual={collection: type(values).__name__},
                ))
                continue
            for index, item in enumerate(values):
                if not isinstance(item, dict):
                    diagnostics.append(_make_diagnostic(
                        "page_spec_reference_inventory_failure",
                        related_paths=(path,),
                        expected={f"{collection}[{index}]": "object"},
                        actual={f"{collection}[{index}]": type(item).__name__},
                    ))
                    continue
                owner = item.get(identity_key)
                raw_refs = item.get(reference_key)
                refs = raw_refs if isinstance(raw_refs, list) else [raw_refs]
                if not isinstance(owner, str) or any(not isinstance(value, str) for value in refs):
                    diagnostics.append(_make_diagnostic(
                        "page_spec_reference_inventory_failure",
                        related_paths=(path,),
                        expected={f"{collection}[{index}].{reference_key}": "component ID text"},
                        actual={f"{collection}[{index}].{reference_key}": "malformed"},
                    ))
                    continue
                for value in refs:
                    if value not in valid_components:
                        missing_map.setdefault(value, []).append(
                            f"{collection}[{identity_key}={owner}].{reference_key}"
                        )
        traceability = payload.get("traceability")
        trace_use_cases = traceability.get("use_cases") if isinstance(traceability, dict) else None
        if isinstance(trace_use_cases, list):
            for index, item in enumerate(trace_use_cases):
                if not isinstance(item, dict) or not isinstance(item.get("component_ids"), list):
                    diagnostics.append(_make_diagnostic(
                        "page_spec_traceability_inventory_failure",
                        related_paths=(path,),
                        expected={f"traceability.use_cases[{index}]": "component_ids list"},
                        actual={f"traceability.use_cases[{index}]": "malformed"},
                    ))
                    continue
                owner = item.get("use_case_id")
                for value in item["component_ids"]:
                    if not isinstance(value, str):
                        diagnostics.append(_make_diagnostic(
                            "page_spec_traceability_inventory_failure",
                            related_paths=(path,),
                            expected={"component_id": "text"},
                            actual={"component_id": type(value).__name__},
                        ))
                    elif value not in valid_components:
                        missing_map.setdefault(value, []).append(
                            f"traceability.use_cases[use_case_id={owner}].component_ids"
                        )
        else:
            diagnostics.append(_make_diagnostic(
                "page_spec_traceability_inventory_failure",
                related_paths=(path,),
                expected={"traceability.use_cases": "list"},
                actual={"traceability.use_cases": type(trace_use_cases).__name__},
            ))

    finding = None
    missing_ids = tuple(sorted(missing_map))
    if len(missing_ids) == 1:
        reference_paths = sorted({item for values in missing_map.values() for item in values})
        finding = _make_finding(
            "page_spec",
            PAGE_SPEC_DANGLING_COMPONENT_REFERENCE,
            related_identifiers=tuple(sorted({bundle_record.page_id, *missing_ids})),
            related_paths=(path,),
            expected={"missing_component_id_count": 1},
            actual={"missing_component_ids": list(missing_ids), "reference_paths": reference_paths},
        )
    elif len(missing_ids) > 1:
        diagnostics.append(_make_diagnostic(
            "page_spec_multiple_missing_component_references",
            related_identifiers=tuple(sorted({bundle_record.page_id, *missing_ids})),
            related_paths=(path,),
            expected={"missing_component_id_count": 1},
            actual={
                "missing_component_ids": list(missing_ids),
                "reference_paths": sorted({
                    item for values in missing_map.values() for item in values
                }),
            },
        ))

    page_object = None
    try:
        page_object = _page_spec_from_payload(payload)
        page_object.validate()
    except (KeyError, TypeError, ValueError) as error:
        message = str(error).lower()
        approved_reference_failure = (
            bool(missing_ids)
            and "component" in message
            and any(token in message for token in ("unknown", "missing", "reference"))
        )
        if not approved_reference_failure:
            diagnostics.append(_make_diagnostic(
                "page_spec_validation_failure",
                related_paths=(path,),
                expected={"page_spec_gate": "pass"},
                actual={"page_spec_gate": "rejected:" + error.__class__.__name__},
            ))
        page_object = None
    return page_object, finding, missing_ids

def _inspect_inspector(
    payload: dict[str, Any] | None,
    bundle_record: BlindedFaultBundleRecord,
    files: Mapping[str, bytes],
    page_payload: dict[str, Any] | None,
    page_root_detected: bool,
    path: str,
    diagnostics: list[FaultDetectionDiagnostic],
) -> FaultDetectionFinding | None:
    if payload is None:
        return None
    if payload.get("schema_version") != INSPECTOR_FACT_SET_SCHEMA_VERSION:
        diagnostics.append(_make_diagnostic(
            "inspector_schema_failure",
            related_paths=(path,),
            expected={"schema_version": INSPECTOR_FACT_SET_SCHEMA_VERSION},
            actual={"schema_version": payload.get("schema_version")},
        ))
        return None
    if payload.get("page_id") != bundle_record.page_id:
        diagnostics.append(_make_diagnostic(
            "inspector_identity_failure",
            related_paths=(path,),
            expected={"page_id": bundle_record.page_id},
            actual={"page_id": payload.get("page_id")},
        ))
    if page_payload is not None:
        expected_page_hash = _canonical_sha256(page_payload)
        if payload.get("page_spec_sha256") != expected_page_hash and not page_root_detected:
            diagnostics.append(_make_diagnostic(
                "inspector_page_spec_binding_failure",
                related_paths=(path, "artifact/page_spec.json"),
                expected={"page_spec_sha256": expected_page_hash},
                actual={"page_spec_sha256": payload.get("page_spec_sha256")},
            ))

    try:
        fact_set = _inspector_fact_set_from_payload(payload)
    except (KeyError, TypeError, ValueError) as error:
        diagnostics.append(_make_diagnostic(
            "inspector_payload_failure",
            related_paths=(path,),
            expected={"inspector_payload": "canonical object graph"},
            actual={"inspector_payload": "rejected:" + error.__class__.__name__},
        ))
        return None

    expected_fact_set = _expected_g0_fact_set_from_v2_package(
        files,
        bundle_record,
        diagnostics,
    )
    finding = None
    if expected_fact_set is not None and fact_set != expected_fact_set:
        missing_link = _exact_single_missing_inspector_relation(
            fact_set,
            expected_fact_set,
        )
        if missing_link is not None:
            expected_fact = next(
                item
                for item in expected_fact_set.facts
                if missing_link.trace_link_id in item.trace_link_ids
            )
            source = next(
                (
                    item
                    for item in expected_fact_set.source_refs
                    if item.source_ref_id == missing_link.source_ref_id
                ),
                None,
            )
            related = {
                fact_set.fact_set_id,
                expected_fact.fact_id,
                expected_fact.decision_id,
                missing_link.trace_link_id,
                missing_link.entity_id,
                missing_link.source_ref_id,
            }
            if source is not None and source.doc_id:
                related.add(source.doc_id)
            finding = _make_finding(
                "guidance/adoption",
                INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH,
                related_identifiers=tuple(sorted(related)),
                related_paths=(
                    path,
                    "artifact/result_package/internal/guided_page_spec_build_result.json",
                    "artifact/result_package/internal/retrieval_guidance.json",
                    "artifact/result_package/internal/retrieval_influence_report.json",
                ),
                expected={
                    "decision_id": expected_fact.decision_id,
                    "entity_id": missing_link.entity_id,
                    "field_path": missing_link.field_path,
                    "trace_link_id": missing_link.trace_link_id,
                },
                actual={"trace_relation": "missing_from_fact_and_trace_links"},
            )
        else:
            diagnostics.append(_make_diagnostic(
                "inspector_expected_projection_mismatch",
                related_identifiers=(fact_set.fact_set_id,),
                related_paths=(
                    path,
                    "artifact/result_package/internal/guided_page_spec_build_result.json",
                ),
                expected={
                    "inspector_fact_set": "exact G0 projection or one missing expected relation"
                },
                actual={
                    "mismatched_fields": _inspector_mismatched_fields(
                        fact_set,
                        expected_fact_set,
                    )
                },
            ))

    try:
        fact_set.validate()
    except InspectorFactError as error:
        if finding is None:
            diagnostics.append(_make_diagnostic(
                "inspector_validation_failure",
                related_identifiers=(fact_set.fact_set_id,),
                related_paths=(path,),
                expected={"inspector_gate": "pass"},
                actual={"inspector_gate": "rejected:" + error.__class__.__name__},
            ))
    if not fact_set.retrieval_influence_report_passed:
        diagnostics.append(_make_diagnostic(
            "inspector_influence_gate_failure",
            related_identifiers=(fact_set.retrieval_influence_report_id,),
            related_paths=(path,),
            expected={"retrieval_influence_report_passed": True},
            actual={"retrieval_influence_report_passed": False},
        ))
    return finding


def _expected_g0_fact_set_from_v2_package(
    files: Mapping[str, bytes],
    bundle_record: BlindedFaultBundleRecord,
    diagnostics: list[FaultDetectionDiagnostic],
) -> InspectorFactSet | None:
    manifest_path = "artifact/result_package/package_manifest.json"
    internal_paths = {
        "guidance": "artifact/result_package/internal/retrieval_guidance.json",
        "guided": "artifact/result_package/internal/guided_page_spec_build_result.json",
        "page_spec": "artifact/result_package/internal/page_spec.json",
        "influence": "artifact/result_package/internal/retrieval_influence_report.json",
    }
    try:
        manifest = _load_json_object(files[manifest_path], manifest_path)
        if (
            manifest.get("schema_version") != RESULT_PACKAGE_V2_SCHEMA_VERSION
            or manifest.get("page_id") != bundle_record.page_id
        ):
            raise ValueError("result package is not the current G0 v2 route")
        payloads = {
            name: _load_json_object(files[path], path)
            for name, path in internal_paths.items()
        }
        guidance = _retrieval_guidance_from_payload(payloads["guidance"])
        guided = _guided_build_result_from_payload(payloads["guided"])
        page_spec = _page_spec_from_payload(payloads["page_spec"])
        influence = _retrieval_influence_report_from_payload(payloads["influence"])
        if guidance.to_dict() != payloads["guidance"]:
            raise ValueError("retrieval guidance did not round-trip exactly")
        if guided.to_dict() != payloads["guided"]:
            raise ValueError("guided build result did not round-trip exactly")
        if page_spec.to_dict() != payloads["page_spec"]:
            raise ValueError("PageSpec did not round-trip exactly")
        if influence.to_dict() != payloads["influence"]:
            raise ValueError("retrieval influence did not round-trip exactly")
        return project_g0_inspector_facts(guidance, guided, page_spec, influence)
    except (KeyError, TypeError, ValueError, InspectorFactError) as error:
        diagnostics.append(_make_diagnostic(
            "inspector_projection_source_failure",
            related_paths=(manifest_path, *internal_paths.values()),
            expected={"projection_source": "validated G0 ResultPackage v2 internals"},
            actual={"projection_source": "rejected:" + error.__class__.__name__},
        ))
        return None


def _exact_single_missing_inspector_relation(
    actual: InspectorFactSet,
    expected: InspectorFactSet,
) -> InspectorTraceLink | None:
    expected_links = {item.trace_link_id: item for item in expected.trace_links}
    actual_links = {item.trace_link_id: item for item in actual.trace_links}
    missing_ids = set(expected_links) - set(actual_links)
    if len(missing_ids) != 1 or set(actual_links) - set(expected_links):
        return None
    if any(actual_links[key] != expected_links[key] for key in actual_links):
        return None
    missing_id = next(iter(missing_ids))
    expected_facts = {item.fact_id: item for item in expected.facts}
    actual_facts = {item.fact_id: item for item in actual.facts}
    if set(actual_facts) != set(expected_facts):
        return None
    differing = [
        key for key in expected_facts if actual_facts[key] != expected_facts[key]
    ]
    if len(differing) != 1:
        return None
    fact_id = differing[0]
    actual_fact = actual_facts[fact_id]
    expected_fact = expected_facts[fact_id]
    if replace(actual_fact, trace_link_ids=expected_fact.trace_link_ids) != expected_fact:
        return None
    if set(expected_fact.trace_link_ids) - set(actual_fact.trace_link_ids) != {missing_id}:
        return None
    if set(actual_fact.trace_link_ids) - set(expected_fact.trace_link_ids):
        return None
    candidate = replace(
        actual,
        trace_links=expected.trace_links,
        facts=expected.facts,
    )
    if candidate != expected:
        return None
    try:
        candidate.validate()
    except InspectorFactError:
        return None
    return expected_links[missing_id]


def _inspector_mismatched_fields(
    actual: InspectorFactSet,
    expected: InspectorFactSet,
) -> list[str]:
    fields = (
        "fact_set_id",
        "run_group",
        "page_id",
        "guidance_bundle_id",
        "guided_build_result_id",
        "retrieval_influence_report_id",
        "guidance_sha256",
        "guided_build_result_sha256",
        "page_spec_sha256",
        "retrieval_influence_report_sha256",
        "retrieval_influence_report_passed",
        "influence_checks",
        "source_refs",
        "trace_links",
        "facts",
        "schema_version",
    )
    return [name for name in fields if getattr(actual, name) != getattr(expected, name)]

def _inspect_render(
    files: Mapping[str, bytes],
    bundle_record: BlindedFaultBundleRecord,
    page_payload: dict[str, Any] | None,
    page_missing_component_ids: tuple[str, ...],
    page_root_detected: bool,
    diagnostics: list[FaultDetectionDiagnostic],
) -> FaultDetectionFinding | None:
    html_path = "artifact/render/index.html"
    manifest_path = "artifact/render/render_manifest.json"
    manifest = _decode_json_object(files, manifest_path, diagnostics)
    if manifest is not None:
        expected_fields = {"files", "page_id", "page_spec_schema_version", "schema_version"}
        if set(manifest) != expected_fields or manifest.get("schema_version") != RENDER_MANIFEST_SCHEMA_VERSION:
            diagnostics.append(_make_diagnostic(
                "render_manifest_schema_failure",
                related_paths=(manifest_path,),
                expected={"schema_version": RENDER_MANIFEST_SCHEMA_VERSION, "fields": sorted(expected_fields)},
                actual={"schema_version": manifest.get("schema_version"), "fields": sorted(manifest)},
            ))
        if manifest.get("page_id") != bundle_record.page_id:
            diagnostics.append(_make_diagnostic(
                "render_identity_failure",
                related_paths=(manifest_path,),
                expected={"page_id": bundle_record.page_id},
                actual={"page_id": manifest.get("page_id")},
            ))
        entries = manifest.get("files")
        declared = {}
        if isinstance(entries, list):
            for item in entries:
                if (
                    isinstance(item, dict)
                    and set(item) == {"name", "sha256"}
                    and isinstance(item.get("name"), str)
                ):
                    declared[item["name"]] = item.get("sha256")
        if set(declared) != {"index.html", "styles.css", "app.js"}:
            diagnostics.append(_make_diagnostic(
                "render_manifest_inventory_failure",
                related_paths=(manifest_path,),
                expected={"files": ["app.js", "index.html", "styles.css"]},
                actual={"files": sorted(declared)},
            ))
        else:
            for name, digest in sorted(declared.items()):
                path = "artifact/render/" + name
                actual_digest = sha256(files[path]).hexdigest()
                if digest != actual_digest:
                    diagnostics.append(_make_diagnostic(
                        "render_manifest_hash_failure",
                        related_paths=(manifest_path, path),
                        expected={"sha256": actual_digest},
                        actual={"sha256": digest},
                    ))

    if page_payload is None:
        diagnostics.append(_make_diagnostic(
            "render_binding_input_unavailable",
            related_paths=(html_path, "artifact/page_spec.json"),
            expected={"page_spec": "available"},
            actual={"page_spec": "unavailable"},
        ))
        return None
    expected_ids = _unique_object_ids(page_payload.get("components"), "component_id")
    if expected_ids is None:
        return None
    try:
        html = files[html_path].decode("utf-8")
        parser = _ComponentIdParser()
        parser.feed(html)
        parser.close()
    except (UnicodeDecodeError, ValueError) as error:
        diagnostics.append(_make_diagnostic(
            "render_html_failure",
            related_paths=(html_path,),
            expected={"html": "valid UTF-8 component-ID markup"},
            actual={"html": "rejected:" + error.__class__.__name__},
        ))
        return None
    actual_ids = parser.component_ids
    if len(actual_ids) != len(set(actual_ids)):
        diagnostics.append(_make_diagnostic(
            "render_component_identity_failure",
            related_paths=(html_path,),
            expected={"data_component_ids": "unique"},
            actual={"data_component_ids": actual_ids},
        ))
        return None
    missing = tuple(sorted(set(expected_ids) - set(actual_ids)))
    unexpected = tuple(sorted(set(actual_ids) - set(expected_ids)))
    if len(missing) == 1 and len(unexpected) == 1:
        return _make_finding(
            "render_binding",
            DOM_COMPONENT_STABLE_ID_MISMATCH,
            related_identifiers=tuple(sorted({bundle_record.page_id, *missing, *unexpected})),
            related_paths=(html_path, manifest_path),
            expected={"data_component_id": missing[0]},
            actual={"data_component_id": unexpected[0]},
        )
    if (
        not missing
        and unexpected
        and page_root_detected
        and set(unexpected).issubset(page_missing_component_ids)
    ):
        return None
    if missing or unexpected:
        diagnostics.append(_make_diagnostic(
            "render_component_binding_failure",
            related_identifiers=tuple(sorted({*missing, *unexpected})),
            related_paths=(html_path,),
            expected={"component_ids": sorted(expected_ids)},
            actual={"component_ids": sorted(actual_ids)},
        ))
    return None

def _inspect_result_package(
    files: Mapping[str, bytes],
    bundle_record: BlindedFaultBundleRecord,
    bundle_dir: Path,
    diagnostics: list[FaultDetectionDiagnostic],
) -> tuple[tuple[FaultDetectionFinding, ...], dict[str, Any] | None]:
    manifest_path = "artifact/result_package/package_manifest.json"
    manifest = _decode_json_object(files, manifest_path, diagnostics)
    if manifest is None:
        return (), None
    expected_fields = {
        "schema_version", "package_id", "page_id", "entrypoint", "result_summary", "files"
    }
    if set(manifest) != expected_fields:
        diagnostics.append(_make_diagnostic(
            "package_manifest_schema_failure",
            related_paths=(manifest_path,),
            expected={"fields": sorted(expected_fields)},
            actual={"fields": sorted(manifest)},
        ))
        return (), manifest
    schema = manifest.get("schema_version")
    roles = _V2_ROLES if schema == RESULT_PACKAGE_V2_SCHEMA_VERSION else None
    if roles is None:
        diagnostics.append(_make_diagnostic(
            "package_manifest_schema_failure",
            related_paths=(manifest_path,),
            expected={"schema_version": RESULT_PACKAGE_V2_SCHEMA_VERSION},
            actual={"schema_version": schema},
        ))
        return (), manifest
    package_id = manifest.get("package_id")
    if not isinstance(package_id, str) or not package_id:
        diagnostics.append(_make_diagnostic(
            "package_identity_failure",
            related_paths=(manifest_path,),
            expected={"package_id": "non-empty text"},
            actual={"package_id": package_id},
        ))
    if manifest.get("page_id") != bundle_record.page_id:
        diagnostics.append(_make_diagnostic(
            "package_identity_failure",
            related_paths=(manifest_path,),
            expected={"page_id": bundle_record.page_id},
            actual={"page_id": manifest.get("page_id")},
        ))

    entries = manifest.get("files")
    if not isinstance(entries, list):
        diagnostics.append(_make_diagnostic(
            "package_manifest_inventory_failure",
            related_paths=(manifest_path,),
            expected={"files": "list"},
            actual={"files": type(entries).__name__},
        ))
        return (), manifest
    declared: dict[str, dict[str, Any]] = {}
    malformed = False
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "role", "sha256", "size"}:
            malformed = True
            continue
        path = item.get("path")
        if not _is_safe_relative_posix(path) or path in declared:
            malformed = True
            continue
        declared[path] = item
    if malformed:
        diagnostics.append(_make_diagnostic(
            "package_manifest_inventory_failure",
            related_paths=(manifest_path,),
            expected={"entries": "unique safe path/role/sha256/size objects"},
            actual={"entries": "malformed"},
        ))
        return (), manifest

    prefix = "artifact/result_package/"
    actual_paths = {
        path.removeprefix(prefix)
        for path in files
        if path.startswith(prefix) and path != manifest_path
    }
    declared_paths = set(declared)
    missing_declarations = tuple(sorted(actual_paths - declared_paths))
    unexpected_declarations = tuple(sorted(declared_paths - actual_paths))
    findings: list[FaultDetectionFinding] = []
    if len(missing_declarations) == 1 and len(unexpected_declarations) == 1:
        findings.append(_make_finding(
            "package",
            PACKAGE_MANIFEST_PATH_MISMATCH,
            related_identifiers=(package_id,) if isinstance(package_id, str) and package_id else (),
            related_paths=tuple(sorted({
                manifest_path,
                prefix + missing_declarations[0],
                prefix + unexpected_declarations[0],
            })),
            expected={"manifest_path": missing_declarations[0]},
            actual={"manifest_path": unexpected_declarations[0]},
        ))
    elif missing_declarations or unexpected_declarations:
        diagnostics.append(_make_diagnostic(
            "package_manifest_inventory_failure",
            related_paths=(manifest_path,),
            expected={"declared_paths": sorted(actual_paths)},
            actual={"declared_paths": sorted(declared_paths)},
        ))
    else:
        role_failures = []
        size_failures = []
        hash_failures = []
        for path, entry in sorted(declared.items()):
            content = files[prefix + path]
            if entry.get("role") != roles.get(path):
                role_failures.append(path)
            if entry.get("size") != len(content):
                size_failures.append(path)
            if entry.get("sha256") != sha256(content).hexdigest():
                hash_failures.append(path)
        if role_failures or size_failures:
            diagnostics.append(_make_diagnostic(
                "package_manifest_entry_failure",
                related_paths=tuple(sorted({
                    manifest_path,
                    *(prefix + item for item in role_failures + size_failures),
                })),
                expected={"roles_and_sizes": "match package schema and bytes"},
                actual={"role_failures": role_failures, "size_failures": size_failures},
            ))
        if len(hash_failures) == 1 and not role_failures and not size_failures:
            target = hash_failures[0]
            findings.append(_make_finding(
                "package",
                PACKAGE_MANIFEST_SHA256_MISMATCH,
                related_identifiers=(package_id,) if isinstance(package_id, str) and package_id else (),
                related_paths=(manifest_path, prefix + target),
                expected={"sha256": sha256(files[prefix + target]).hexdigest()},
                actual={"sha256": declared[target].get("sha256")},
            ))
        elif hash_failures:
            diagnostics.append(_make_diagnostic(
                "package_manifest_hash_failure",
                related_paths=tuple(sorted({
                    manifest_path,
                    *(prefix + item for item in hash_failures),
                })),
                expected={"sha256": "one declared digest per exact file"},
                actual={"mismatched_paths": hash_failures},
            ))

    package_root = bundle_dir / "artifact" / "result_package"
    if isinstance(package_id, str) and isinstance(manifest.get("page_id"), str):
        try:
            package = RetrievalEnhancedResultPackage(
                package_id=package_id,
                page_id=manifest["page_id"],
                package_dir=package_root,
                entrypoint=manifest.get("entrypoint"),
                result_summary=manifest.get("result_summary"),
                package_manifest="package_manifest.json",
            )
            package.validate()
        except (RetrievalEnhancedResultPackageError, TypeError, ValueError) as error:
            if not findings:
                diagnostics.append(_make_diagnostic(
                    "package_validation_failure",
                    related_identifiers=(package_id,),
                    related_paths=(manifest_path,),
                    expected={"package_gate": "pass"},
                    actual={"package_gate": "rejected:" + error.__class__.__name__},
                ))
    return tuple(sorted(findings, key=_finding_order_key)), manifest


def _inspect_acceptance_chain(
    files: Mapping[str, bytes],
    bundle_record: BlindedFaultBundleRecord,
    page_payload: dict[str, Any] | None,
    page_root_detected: bool,
    render_root_detected: bool,
    diagnostics: list[FaultDetectionDiagnostic],
) -> None:
    view_path = "artifact/acceptance/requirement_view.json"
    plan_path = "artifact/acceptance/acceptance_plan.json"
    binding_path = "artifact/acceptance/acceptance_binding.json"
    view = _decode_json_object(files, view_path, diagnostics)
    plan = _decode_json_object(files, plan_path, diagnostics)
    binding = _decode_json_object(files, binding_path, diagnostics)
    if view is None or plan is None or binding is None:
        return
    expected_view_fields = {
        "constraints", "requirement", "schema_version", "source_context_schema_version",
        "use_cases", "validation_evidence",
    }
    if set(view) != expected_view_fields or view.get("schema_version") != INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION:
        diagnostics.append(_make_diagnostic(
            "acceptance_requirement_view_failure",
            related_paths=(view_path,),
            expected={"schema_version": INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION, "fields": sorted(expected_view_fields)},
            actual={"schema_version": view.get("schema_version"), "fields": sorted(view)},
        ))
    expected_plan_fields = {
        "criteria", "requirement_metadata", "schema_version",
        "source_requirement_view_schema_version", "source_requirement_view_sha256",
    }
    if set(plan) != expected_plan_fields or plan.get("schema_version") != ACCEPTANCE_PLAN_SCHEMA_VERSION:
        diagnostics.append(_make_diagnostic(
            "acceptance_plan_failure",
            related_paths=(plan_path,),
            expected={"schema_version": ACCEPTANCE_PLAN_SCHEMA_VERSION, "fields": sorted(expected_plan_fields)},
            actual={"schema_version": plan.get("schema_version"), "fields": sorted(plan)},
        ))
    expected_binding_fields = {
        "bindings", "observed_render_manifest_sha256", "page_id", "schema_version",
        "source_acceptance_plan_schema_version", "source_acceptance_plan_sha256",
        "source_page_spec_schema_version", "source_page_spec_sha256",
        "source_requirement_view_schema_version", "source_requirement_view_sha256",
        "step_plan_schema_version", "steps",
    }
    if set(binding) != expected_binding_fields or binding.get("schema_version") != ACCEPTANCE_BINDING_SCHEMA_VERSION:
        diagnostics.append(_make_diagnostic(
            "acceptance_binding_failure",
            related_paths=(binding_path,),
            expected={"schema_version": ACCEPTANCE_BINDING_SCHEMA_VERSION, "fields": sorted(expected_binding_fields)},
            actual={"schema_version": binding.get("schema_version"), "fields": sorted(binding)},
        ))
        return
    view_hash = _canonical_sha256(view)
    plan_hash = _canonical_sha256(plan)
    checks = (
        ("plan_requirement_view_hash", plan.get("source_requirement_view_sha256"), view_hash, False),
        ("binding_requirement_view_hash", binding.get("source_requirement_view_sha256"), view_hash, False),
        ("binding_acceptance_plan_hash", binding.get("source_acceptance_plan_sha256"), plan_hash, False),
        (
            "binding_page_spec_hash",
            binding.get("source_page_spec_sha256"),
            _canonical_sha256(page_payload) if page_payload is not None else None,
            page_root_detected,
        ),
        (
            "binding_render_manifest_hash",
            binding.get("observed_render_manifest_sha256"),
            sha256(files["artifact/render/render_manifest.json"]).hexdigest(),
            render_root_detected,
        ),
    )
    for code, actual, expected, suppressed in checks:
        if actual != expected and not suppressed:
            diagnostics.append(_make_diagnostic(
                "acceptance_chain_identity_failure",
                related_paths=(binding_path, plan_path, view_path),
                expected={code: expected},
                actual={code: actual},
            ))
    schema_checks = {
        "source_requirement_view_schema_version": INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION,
        "source_acceptance_plan_schema_version": ACCEPTANCE_PLAN_SCHEMA_VERSION,
        "source_page_spec_schema_version": PAGE_SPEC_SCHEMA_VERSION,
        "step_plan_schema_version": EXECUTABLE_STEP_PLAN_SCHEMA_VERSION,
    }
    for field, expected in schema_checks.items():
        if binding.get(field) != expected:
            diagnostics.append(_make_diagnostic(
                "acceptance_chain_schema_failure",
                related_paths=(binding_path,),
                expected={field: expected},
                actual={field: binding.get(field)},
            ))
    if binding.get("page_id") != bundle_record.page_id:
        diagnostics.append(_make_diagnostic(
            "acceptance_chain_identity_failure",
            related_paths=(binding_path,),
            expected={"page_id": bundle_record.page_id},
            actual={"page_id": binding.get("page_id")},
        ))


def _inspect_package_mirrors(
    files: Mapping[str, bytes],
    page_payload: dict[str, Any] | None,
    page_root_detected: bool,
    render_root_detected: bool,
    package_manifest: dict[str, Any] | None,
    diagnostics: list[FaultDetectionDiagnostic],
) -> None:
    if package_manifest is None:
        return
    internal_path = "artifact/result_package/internal/page_spec.json"
    embedded = _decode_json_object(files, internal_path, diagnostics) if internal_path in files else None
    if embedded is None and internal_path not in files:
        diagnostics.append(_make_diagnostic(
            "package_source_mirror_failure",
            related_paths=(internal_path,),
            expected={"internal_page_spec": "present"},
            actual={"internal_page_spec": "missing"},
        ))
    elif page_payload is not None and embedded != page_payload and not page_root_detected:
        diagnostics.append(_make_diagnostic(
            "package_source_mirror_failure",
            related_paths=(internal_path, "artifact/page_spec.json"),
            expected={"internal_page_spec": "equals top-level PageSpec"},
            actual={"internal_page_spec": "different"},
        ))
    mismatches = []
    for name in ("index.html", "styles.css", "app.js", "render_manifest.json"):
        package_path = "artifact/result_package/page/" + name
        render_path = "artifact/render/" + name
        if package_path not in files or files[package_path] != files[render_path]:
            mismatches.append(name)
    if mismatches and not render_root_detected:
        diagnostics.append(_make_diagnostic(
            "package_render_mirror_failure",
            related_paths=tuple(sorted({
                *("artifact/result_package/page/" + item for item in mismatches),
                *("artifact/render/" + item for item in mismatches),
            })),
            expected={"page_files": "byte-equal to top-level RenderResult"},
            actual={"mismatched_files": mismatches},
        ))

def _make_report(
    bundle_record: BlindedFaultBundleRecord,
    parity_report: BlindedBundleInventoryParityReport,
    findings: tuple[FaultDetectionFinding, ...],
    diagnostics: tuple[FaultDetectionDiagnostic, ...],
) -> FaultDetectionReport:
    summary = _derive_report_summary(findings, diagnostics)
    unsigned = FaultDetectionReport(
        report_id="",
        bundle_id=bundle_record.bundle_id,
        bundle_record_sha256=bundle_record.sha256(),
        bundle_schema_version=bundle_record.schema_version,
        bundle_inventory_sha256=bundle_record.inventory_sha256,
        parity_schema_version=parity_report.schema_version,
        parity_path_set_sha256=parity_report.path_set_sha256,
        case_id=bundle_record.case_id,
        page_id=bundle_record.page_id,
        findings=findings,
        diagnostics=diagnostics,
        **summary,
    )
    report = replace(
        unsigned,
        report_id="fault-detection-report-" + _canonical_sha256(unsigned.to_payload())[:20],
    )
    report.validate()
    return report


def _derive_report_summary(
    findings: tuple[FaultDetectionFinding, ...],
    diagnostics: tuple[FaultDetectionDiagnostic, ...],
) -> dict[str, object]:
    all_identifiers = tuple(sorted({
        value for item in (*findings, *diagnostics) for value in item.related_identifiers
    }))
    all_paths = tuple(sorted({
        value for item in (*findings, *diagnostics) for value in item.related_paths
    }))
    if not findings and not diagnostics:
        return {
            "status": NO_FAULT_DETECTED,
            "predicted_stage": "none",
            "predicted_error_code": "none",
            "related_identifiers": (),
            "related_paths": (),
            "expected": _details({"inspected_artifacts": "structurally_clean"}),
            "actual": _details({"inspected_artifacts": "structurally_clean"}),
            "predicted_repairable": False,
            "predicted_repair_scope": (),
            "fallback_recommendation": "not_applicable",
        }
    if len(findings) == 1 and not diagnostics:
        finding = findings[0]
        repairable, scope, fallback = _repair_prediction(finding.error_code)
        return {
            "status": FAULT_DETECTED,
            "predicted_stage": finding.stage,
            "predicted_error_code": finding.error_code,
            "related_identifiers": finding.related_identifiers,
            "related_paths": finding.related_paths,
            "expected": finding.expected,
            "actual": finding.actual,
            "predicted_repairable": repairable,
            "predicted_repair_scope": scope,
            "fallback_recommendation": fallback,
        }
    if not findings:
        return {
            "status": UNCLASSIFIED_FAILURE,
            "predicted_stage": "unclassified",
            "predicted_error_code": "unclassified_artifact_failure",
            "related_identifiers": all_identifiers,
            "related_paths": all_paths,
            "expected": _details({"classified_root_count": 1}),
            "actual": _details({"diagnostic_codes": [item.code for item in diagnostics]}),
            "predicted_repairable": False,
            "predicted_repair_scope": (),
            "fallback_recommendation": "deterministic_fallback",
        }
    return {
        "status": AMBIGUOUS_MULTIPLE_FAULTS,
        "predicted_stage": "multiple",
        "predicted_error_code": "multiple_faults",
        "related_identifiers": all_identifiers,
        "related_paths": all_paths,
        "expected": _details({"classified_root_count": 1, "unclassified_count": 0}),
        "actual": _details({
            "classified_error_codes": [item.error_code for item in findings],
            "unclassified_codes": [item.code for item in diagnostics],
        }),
        "predicted_repairable": False,
        "predicted_repair_scope": (),
        "fallback_recommendation": "deterministic_fallback",
    }


def _repair_prediction(error_code: str) -> tuple[bool, tuple[str, ...], str]:
    if error_code == DOM_COMPONENT_STABLE_ID_MISMATCH:
        return True, (
            "artifact/render/index.html",
            "artifact/render/render_manifest.json",
        ), "deterministic_fallback_if_local_repair_not_applied"
    if error_code in {PACKAGE_MANIFEST_PATH_MISMATCH, PACKAGE_MANIFEST_SHA256_MISMATCH}:
        return True, (
            "artifact/result_package/package_manifest.json",
        ), "deterministic_fallback_if_local_repair_not_applied"
    return False, (), "deterministic_fallback"


def _validate_parity_binding(
    bundle_record: BlindedFaultBundleRecord,
    parity_report: BlindedBundleInventoryParityReport,
) -> None:
    paths = tuple(item.path for item in bundle_record.files)
    slots = tuple(item.slot for item in bundle_record.files)
    if paths != parity_report.paths:
        raise FaultDetectionError("bundle path inventory does not match parity report")
    if tuple(sorted(set(slots))) != parity_report.slots:
        raise FaultDetectionError("bundle slot inventory does not match parity report")
    for entry in bundle_record.files:
        if entry.slot != _slot_for_detector_path(entry.path):
            raise FaultDetectionError("bundle path-to-slot mapping is not canonical")


def _read_bundle_artifacts(
    bundle_dir: Path,
    bundle_record: BlindedFaultBundleRecord,
) -> dict[str, bytes]:
    root = bundle_dir.absolute()
    if root.is_symlink() or not root.is_dir():
        raise FaultDetectionError("bundle_dir must be a real directory")
    files: dict[str, bytes] = {}
    for entry in bundle_record.files:
        path = root.joinpath(*PurePosixPath(entry.path).parts)
        if path.is_symlink() or not path.is_file():
            raise FaultDetectionError("bundle artifact path is not a real file")
        content = path.read_bytes()
        if len(content) != entry.size or sha256(content).hexdigest() != entry.sha256:
            raise FaultDetectionError("bundle artifact changed after bundle validation")
        files[entry.path] = content
    return files


def _retrieval_guidance_from_payload(payload: dict[str, Any]) -> RetrievalGuidance:
    def item_from_payload(item: dict[str, Any]) -> GuidanceItem:
        source = item["source"]
        return GuidanceItem(
            guidance_id=item["guidance_id"],
            category=item["category"],
            value=item["value"],
            source=GuidanceSource(
                role=source["role"],
                doc_id=source["doc_id"],
                source_fields=list(source["source_fields"]),
                extraction_rule=source["extraction_rule"],
                reference_uris=list(source["reference_uris"]),
                adapter_evidence=[dict(value) for value in source.get("adapter_evidence", [])],
            ),
        )

    return RetrievalGuidance(
        guidance_bundle_id=payload["guidance_bundle_id"],
        target_device=payload["target_device"],
        task_type=payload["task_type"],
        requirement_guidance=[item_from_payload(item) for item in payload["requirement_guidance"]],
        ui_guidance=[item_from_payload(item) for item in payload["ui_guidance"]],
        interaction_guidance=[item_from_payload(item) for item in payload["interaction_guidance"]],
        implementation_guidance=[item_from_payload(item) for item in payload["implementation_guidance"]],
        validation_guidance=[item_from_payload(item) for item in payload["validation_guidance"]],
        use_case_traces=[
            UseCaseGuidanceTrace(
                use_case_id=item["use_case_id"],
                guidance_ids=list(item["guidance_ids"]),
                source_doc_ids=list(item["source_doc_ids"]),
            )
            for item in payload["use_case_traces"]
        ],
        source_context_schema_version=payload["source_context_schema_version"],
        schema_version=payload["schema_version"],
    )


def _guided_build_result_from_payload(
    payload: dict[str, Any],
) -> GuidedPageSpecBuildResult:
    def decision_from_payload(item: dict[str, Any]) -> GuidanceDecision:
        return GuidanceDecision(
            decision_id=item["decision_id"],
            source_kind=item["source_kind"],
            role=item["role"],
            guidance_id=item["guidance_id"],
            doc_id=item["doc_id"],
            rule=item["rule"],
            reason=item["reason"],
            affected_fields=[
                AffectedPageSpecField(**value) for value in item["affected_fields"]
            ],
        )

    return GuidedPageSpecBuildResult(
        build_result_id=payload["build_result_id"],
        guidance_bundle_id=payload["guidance_bundle_id"],
        page_spec=_page_spec_from_payload(payload["page_spec"]),
        adopted=[decision_from_payload(item) for item in payload["adopted"]],
        ignored=[decision_from_payload(item) for item in payload["ignored"]],
        fallback=[decision_from_payload(item) for item in payload["fallback"]],
        source_context_schema_version=payload["source_context_schema_version"],
        source_guidance_schema_version=payload["source_guidance_schema_version"],
        schema_version=payload["schema_version"],
    )


def _retrieval_influence_report_from_payload(
    payload: dict[str, Any],
) -> RetrievalInfluenceReport:
    def difference_from_payload(item: dict[str, Any]) -> StructuralDifference:
        return StructuralDifference(**item)

    return RetrievalInfluenceReport(
        report_id=payload["report_id"],
        page_id=payload["page_id"],
        guidance_bundle_id=payload["guidance_bundle_id"],
        passed=payload["passed"],
        checks=[
            InfluenceCheck(
                check_id=item["check_id"],
                category=item["category"],
                status=item["status"],
                message=item["message"],
                related_ids=list(item["related_ids"]),
            )
            for item in payload["checks"]
        ],
        structural_differences=[
            difference_from_payload(item) for item in payload["structural_differences"]
        ],
        decision_traceability=[dict(item) for item in payload["decision_traceability"]],
        role_summaries=[dict(item) for item in payload["role_summaries"]],
        field_category_counts=dict(payload["field_category_counts"]),
        decision_status_counts=dict(payload["decision_status_counts"]),
        ablations=[
            RoleAblation(
                role=item["role"],
                outcome=item["outcome"],
                difference_count=item["difference_count"],
                differences=[
                    difference_from_payload(value) for value in item["differences"]
                ],
            )
            for item in payload["ablations"]
        ],
        renderer_consistency=dict(payload["renderer_consistency"]),
        schema_version=payload["schema_version"],
    )


def _page_spec_from_payload(payload: dict[str, Any]) -> PageSpec:
    traceability = payload["traceability"]
    if not isinstance(traceability, dict):
        raise TypeError("traceability must be an object")
    return PageSpec(
        page_id=payload["page_id"],
        title=payload["title"],
        summary=payload["summary"],
        target_device=payload["target_device"],
        page_type=payload["page_type"],
        layout=LayoutSpec(**payload["layout"]),
        use_cases=[PageUseCase(**item) for item in payload["use_cases"]],
        sections=[SectionSpec(**item) for item in payload["sections"]],
        components=[ComponentSpec(**item) for item in payload["components"]],
        states=[PageState(**item) for item in payload["states"]],
        interactions=[InteractionSpec(**item) for item in payload["interactions"]],
        constraints=[ConstraintSpec(**item) for item in payload["constraints"]],
        acceptance_checks=[AcceptanceCheck(**item) for item in payload["acceptance_checks"]],
        traceability=TraceabilitySpec(
            source_context_schema_version=traceability["source_context_schema_version"],
            evidence=[EvidenceReference(**item) for item in traceability["evidence"]],
            use_cases=[UseCaseTrace(**item) for item in traceability["use_cases"]],
        ),
        schema_version=payload["schema_version"],
    )


def _inspector_fact_set_from_payload(payload: dict[str, Any]) -> InspectorFactSet:
    return InspectorFactSet(
        fact_set_id=payload["fact_set_id"],
        run_group=payload["run_group"],
        page_id=payload["page_id"],
        guidance_bundle_id=payload["guidance_bundle_id"],
        guided_build_result_id=payload["guided_build_result_id"],
        retrieval_influence_report_id=payload["retrieval_influence_report_id"],
        guidance_sha256=payload["guidance_sha256"],
        guided_build_result_sha256=payload["guided_build_result_sha256"],
        page_spec_sha256=payload["page_spec_sha256"],
        retrieval_influence_report_sha256=payload["retrieval_influence_report_sha256"],
        retrieval_influence_report_passed=payload["retrieval_influence_report_passed"],
        influence_checks=tuple(
            InspectorInfluenceCheckSummary(**item) for item in payload["influence_checks"]
        ),
        source_refs=tuple(InspectorSourceRef(**item) for item in payload["source_refs"]),
        trace_links=tuple(InspectorTraceLink(**item) for item in payload["trace_links"]),
        facts=tuple(
            InspectorFact(**{**item, "trace_link_ids": tuple(item["trace_link_ids"])})
            for item in payload["facts"]
        ),
        schema_version=payload["schema_version"],
    )


class _ComponentIdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.component_ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "data-component-id" and value is not None:
                self.component_ids.append(value)


def _decode_json_object(
    files: Mapping[str, bytes],
    path: str,
    diagnostics: list[FaultDetectionDiagnostic],
) -> dict[str, Any] | None:
    if path not in files:
        diagnostics.append(_make_diagnostic(
            "artifact_missing",
            related_paths=(path,),
            expected={"artifact": "present"},
            actual={"artifact": "missing"},
        ))
        return None
    try:
        return _load_json_object(files[path], path)
    except FaultDetectionError as error:
        diagnostics.append(_make_diagnostic(
            "artifact_json_failure",
            related_paths=(path,),
            expected={"artifact": "UTF-8 JSON object"},
            actual={"artifact": "rejected:" + error.__class__.__name__},
        ))
        return None


def _make_finding(
    stage: str,
    error_code: str,
    *,
    related_identifiers: Sequence[str] = (),
    related_paths: Sequence[str] = (),
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> FaultDetectionFinding:
    unsigned = FaultDetectionFinding(
        finding_id="",
        stage=stage,
        error_code=error_code,
        related_identifiers=tuple(sorted(set(related_identifiers))),
        related_paths=tuple(sorted(set(related_paths))),
        expected=_details(expected),
        actual=_details(actual),
    )
    finding = replace(
        unsigned,
        finding_id="fault-finding-" + _canonical_sha256(unsigned.to_payload())[:20],
    )
    finding.validate()
    return finding


def _make_diagnostic(
    code: str,
    *,
    related_identifiers: Sequence[str] = (),
    related_paths: Sequence[str] = (),
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> FaultDetectionDiagnostic:
    unsigned = FaultDetectionDiagnostic(
        diagnostic_id="",
        code=code,
        related_identifiers=tuple(sorted(set(related_identifiers))),
        related_paths=tuple(sorted(set(related_paths))),
        expected=_details(expected),
        actual=_details(actual),
    )
    diagnostic = replace(
        unsigned,
        diagnostic_id="fault-diagnostic-" + _canonical_sha256(unsigned.to_payload())[:20],
    )
    diagnostic.validate()
    return diagnostic


def _finding_order_key(item: FaultDetectionFinding) -> tuple[str, str, str]:
    return item.stage, item.error_code, item.finding_id


def _diagnostic_order_key(item: FaultDetectionDiagnostic) -> tuple[str, str]:
    return item.code, item.diagnostic_id

def _details(values: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), _detail_value(value)) for key, value in values.items()))


def _detail_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _detail_dicts(values: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"key": key, "value": value} for key, value in values]


def _validate_details(values: tuple[tuple[str, str], ...], field_name: str) -> None:
    if not isinstance(values, tuple) or not values:
        raise FaultDetectionError(field_name + " must be a non-empty tuple")
    keys = []
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise FaultDetectionError(field_name + " entries must be key/value tuples")
        key, value = item
        _require_text(key, field_name + " key")
        _require_text(value, field_name + " value")
        keys.append(key)
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise FaultDetectionError(field_name + " keys must be sorted and unique")


def _validate_sorted_text_tuple(values: tuple[str, ...], field_name: str) -> None:
    if not isinstance(values, tuple):
        raise FaultDetectionError(field_name + " must be a tuple")
    for value in values:
        _require_text(value, field_name)
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise FaultDetectionError(field_name + " must be sorted and unique")


def _validate_path_tuple(values: tuple[str, ...], field_name: str) -> None:
    _validate_sorted_text_tuple(values, field_name)
    for value in values:
        if not _is_safe_relative_posix(value):
            raise FaultDetectionError(field_name + " contains an unsafe path")


def _slot_for_detector_path(path: str) -> str:
    if path.startswith("artifact/acceptance/"):
        return "acceptance"
    if path == "artifact/page_spec.json":
        return "page_spec"
    if path == "artifact/inspector_fact_set.json":
        return "inspector_fact_set"
    if path.startswith("artifact/render/"):
        return "render_artifact"
    if path.startswith("artifact/result_package/"):
        return "result_package"
    raise FaultDetectionError("bundle path is outside detector inventory")


def _unique_object_ids(value: object, field: str) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    result = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get(field), str) or not item[field]:
            return None
        result.append(item[field])
    if len(result) != len(set(result)):
        return None
    return tuple(result)


def _stable_id(prefix: str, payload: object) -> str:
    return prefix + "-" + _canonical_sha256(payload)[:20]


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _load_json_object(content: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FaultDetectionError(name + " must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise FaultDetectionError(name + " must be a JSON object")
    return value


def _is_safe_relative_posix(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(
        part not in {"", ".", ".."} for part in path.parts
    )


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise FaultDetectionError(field_name + " must be non-empty text")


def _require_sha256(value: object, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(item not in "0123456789abcdef" for item in value)
    ):
        raise FaultDetectionError(field_name + " must be a lowercase SHA-256")


def _prepare_empty_output_directory(output_dir: Path) -> Path:
    destination = output_dir.absolute()
    _reject_symlink_ancestors(destination)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise FaultDetectionError("output_dir must be a real directory path")
        if any(destination.iterdir()):
            raise FaultDetectionError("output_dir must be empty; refusing to overwrite")
    else:
        destination.mkdir(parents=True, exist_ok=False)
    return destination


def _reject_symlink_ancestors(path: Path) -> None:
    for candidate in (path.absolute(), *path.absolute().parents):
        if candidate.exists() and candidate.is_symlink():
            raise FaultDetectionError("symlinked output paths are not allowed")


def _write_exact_bytes(path: Path, content: bytes) -> None:
    _reject_symlink_ancestors(path.parent)
    if path.exists():
        raise FaultDetectionError("writer refuses to overwrite a file")
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise FaultDetectionError("written bytes did not round-trip")