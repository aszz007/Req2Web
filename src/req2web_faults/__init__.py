"""Mutation-fragment runtime metadata API; not detector-ready input.

Mutation requests, injector audits, and evaluator-only gold live in internal
modules and are intentionally not re-exported here. FaultCopyRecord metadata
must first be assembled into a separate parity-validated fault-case bundle
before any detector implementation is permitted.
"""
from .mutation import (
    FAULT_COPY_MANIFEST_SCHEMA_VERSION,
    FAULT_COPY_SCHEMA_VERSION,
    FaultCopyRecord,
    FaultMutationError,
    canonical_json_bytes,
    canonical_sha256,
)

__all__ = [
    "FAULT_COPY_MANIFEST_SCHEMA_VERSION",
    "FAULT_COPY_SCHEMA_VERSION",
    "FaultCopyRecord",
    "FaultMutationError",
    "canonical_json_bytes",
    "canonical_sha256",
]
