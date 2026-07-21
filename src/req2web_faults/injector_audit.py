"""Injector-only provenance for deterministic fault-copy mutations.

This module is deliberately excluded from the mutation-fragment package API
and writes its audit into a directory separate from the runtime fragment.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from .mutation import (
    FaultCopyBuildResult,
    FaultCopyRecord,
    FaultMutationError,
    _prepare_empty_output_directory,
    _require_sha256,
    _require_text,
    _write_exact_bytes,
    canonical_json_bytes,
    canonical_sha256,
)


INJECTOR_MUTATION_AUDIT_SCHEMA_VERSION = "req2web.injector_mutation_audit.v1"


@dataclass(frozen=True)
class InjectorMutationAudit:
    """Exact mutation provenance available only to the injector and evaluator."""

    injector_audit_id: str
    runtime_fault_copy_id: str
    runtime_fault_copy_sha256: str
    case_id: str
    mutation_request_sha256: str
    mutation_kind: str
    exact_target_id: str
    manifest_field: str | None
    changed_path: str
    source_artifact_id: str
    source_sha256: str
    runtime_mutated_sha256: str
    injector_only: bool = True
    schema_version: str = INJECTOR_MUTATION_AUDIT_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("injector_audit_id", None)
        return payload

    def validate(self) -> None:
        for name in (
            "injector_audit_id", "runtime_fault_copy_id", "runtime_fault_copy_sha256",
            "case_id", "mutation_request_sha256", "mutation_kind", "exact_target_id",
            "changed_path", "source_artifact_id", "source_sha256", "runtime_mutated_sha256",
        ):
            _require_text(getattr(self, name), name)
        if self.schema_version != INJECTOR_MUTATION_AUDIT_SCHEMA_VERSION:
            raise FaultMutationError("unsupported injector mutation audit schema")
        if self.injector_only is not True:
            raise FaultMutationError("injector mutation audits must remain injector_only")
        if self.manifest_field is not None:
            _require_text(self.manifest_field, "manifest_field")
        for name in (
            "runtime_fault_copy_sha256", "mutation_request_sha256", "source_sha256",
            "runtime_mutated_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        expected = "injector-audit-" + canonical_sha256(self.to_payload())[:20]
        if self.injector_audit_id != expected:
            raise FaultMutationError("injector_audit_id does not match canonical payload")

    def validate_against(self, runtime_record: FaultCopyRecord) -> None:
        self.validate()
        runtime_record.validate()
        if self.runtime_fault_copy_id != runtime_record.fault_copy_id:
            raise FaultMutationError("injector audit fault-copy ID does not match runtime record")
        if self.runtime_fault_copy_sha256 != runtime_record.sha256():
            raise FaultMutationError("injector audit fault-copy hash does not match runtime record")
        if self.case_id != runtime_record.case_id:
            raise FaultMutationError("injector audit case_id does not match runtime record")
        if self.source_artifact_id != runtime_record.target_artifact_id:
            raise FaultMutationError("injector audit artifact identity does not match runtime record")
        if self.source_sha256 != runtime_record.source_sha256:
            raise FaultMutationError("injector audit source hash does not match runtime record")
        if self.runtime_mutated_sha256 != runtime_record.mutated_sha256:
            raise FaultMutationError("injector audit mutated hash does not match runtime record")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def sha256(self) -> str:
        self.validate()
        return canonical_sha256(self.to_dict())


def build_injector_mutation_audit(build_result: FaultCopyBuildResult) -> InjectorMutationAudit:
    """Bind exact mutation request provenance to one opaque runtime record."""
    build_result.validate()
    record = build_result.runtime_record
    request = build_result.mutation_request
    unsigned = InjectorMutationAudit(
        "", record.fault_copy_id, record.sha256(), record.case_id, request.sha256(),
        request.mutation_kind, request.target_id, request.manifest_field,
        build_result.changed_path, build_result.source_artifact_id,
        record.source_sha256, record.mutated_sha256,
    )
    audit = replace(unsigned, injector_audit_id="injector-audit-" + canonical_sha256(unsigned.to_payload())[:20])
    audit.validate_against(record)
    return audit


def write_injector_mutation_audit(audit: InjectorMutationAudit, output_dir) -> InjectorMutationAudit:
    """Write only a canonical injector-only audit into an empty directory."""
    audit.validate()
    destination = _prepare_empty_output_directory(output_dir)
    target = destination / "injector_mutation_audit.json"
    content = canonical_json_bytes(audit.to_dict())
    _write_exact_bytes(target, content)
    if target.read_bytes() != content:
        raise FaultMutationError("injector mutation audit bytes did not round-trip")
    if [item.name for item in destination.iterdir()] != ["injector_mutation_audit.json"]:
        raise FaultMutationError("injector audit writer must not copy runtime artifacts")
    return audit
