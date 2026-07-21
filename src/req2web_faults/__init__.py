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

__all__ = [
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
]
