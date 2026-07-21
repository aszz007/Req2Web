"""Detector-visible structural bundle API.

Injector mutation requests, fragment metadata, injector audits, and evaluator-only
gold remain internal.  Callers may only import the parity-validated bundle
assembler and its structural records from this package namespace.
"""
from .bundle import (
    BLINDED_BUNDLE_PARITY_SCHEMA_VERSION,
    BLINDED_FAULT_BUNDLE_SCHEMA_VERSION,
    BlindedBundleFile,
    BlindedBundleInventoryParityReport,
    BlindedFaultBundleRecord,
    BlindedFaultBundleSource,
    FaultBundleError,
    assemble_blinded_fault_bundle,
    load_blinded_fault_bundle,
    validate_blinded_bundle_inventory_parity,
)

from .detector import (
    AMBIGUOUS_MULTIPLE_FAULTS,
    DOM_COMPONENT_STABLE_ID_MISMATCH,
    FAULT_DETECTED,
    FAULT_DETECTION_REPORT_SCHEMA_VERSION,
    FAULT_DETECTION_STATUSES,
    INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH,
    NO_FAULT_DETECTED,
    PACKAGE_MANIFEST_PATH_MISMATCH,
    PACKAGE_MANIFEST_SHA256_MISMATCH,
    PAGE_SPEC_DANGLING_COMPONENT_REFERENCE,
    UNCLASSIFIED_FAILURE,
    FaultDetectionDiagnostic,
    FaultDetectionError,
    FaultDetectionFinding,
    FaultDetectionReport,
    detect_blinded_fault_bundle,
    write_fault_detection_report,
)

__all__ = [
    "AMBIGUOUS_MULTIPLE_FAULTS",
    "DOM_COMPONENT_STABLE_ID_MISMATCH",
    "FAULT_DETECTED",
    "FAULT_DETECTION_REPORT_SCHEMA_VERSION",
    "FAULT_DETECTION_STATUSES",
    "INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH",
    "NO_FAULT_DETECTED",
    "PACKAGE_MANIFEST_PATH_MISMATCH",
    "PACKAGE_MANIFEST_SHA256_MISMATCH",
    "PAGE_SPEC_DANGLING_COMPONENT_REFERENCE",
    "UNCLASSIFIED_FAILURE",
    "FaultDetectionDiagnostic",
    "FaultDetectionError",
    "FaultDetectionFinding",
    "FaultDetectionReport",
    "BLINDED_BUNDLE_PARITY_SCHEMA_VERSION",
    "BLINDED_FAULT_BUNDLE_SCHEMA_VERSION",
    "BlindedBundleFile",
    "BlindedBundleInventoryParityReport",
    "BlindedFaultBundleRecord",
    "BlindedFaultBundleSource",
    "FaultBundleError",
    "assemble_blinded_fault_bundle",
    "load_blinded_fault_bundle",
    "validate_blinded_bundle_inventory_parity",
    "detect_blinded_fault_bundle",
    "write_fault_detection_report",
]
