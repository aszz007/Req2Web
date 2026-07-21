"""Evaluator-only gold manifest support for pre-created fault copies.

This module is separate from both the non-detector-ready mutation-fragment
metadata layer and the injector-only mutation audit layer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from .injector_audit import InjectorMutationAudit
from .mutation import (
    FaultCopyRecord,
    FaultMutationError,
    _prepare_empty_output_directory,
    _require_text,
    _write_exact_bytes,
    canonical_json_bytes,
    canonical_sha256,
)


FAULT_GOLD_MANIFEST_SCHEMA_VERSION = "req2web.fault_gold_manifest.v2"
APPROVED_GOLD_STAGES = (
    "acceptance",
    "browser_runtime",
    "guidance/adoption",
    "input_context",
    "package",
    "page_spec",
    "provider_generation",
    "provider_runtime",
    "render_binding",
    "retrieval",
)


@dataclass(frozen=True)
class FaultGoldManifest:
    """Immutable evaluator-only labels bound to runtime and injector identities."""

    gold_manifest_id: str
    fault_copy_id: str
    fault_copy_sha256: str
    injector_audit_id: str
    injector_audit_sha256: str
    case_id: str
    gold_stage: str
    gold_error: str
    gold_repairable: bool
    gold_allowed_scope: tuple[str, ...]
    expected_fallback: str
    evaluator_only: bool = True
    schema_version: str = FAULT_GOLD_MANIFEST_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("gold_manifest_id", None)
        return payload

    def validate(self) -> None:
        for name in (
            "gold_manifest_id", "fault_copy_id", "fault_copy_sha256", "injector_audit_id",
            "injector_audit_sha256", "case_id", "gold_stage", "gold_error",
            "expected_fallback",
        ):
            _require_text(getattr(self, name), name)
        if self.schema_version != FAULT_GOLD_MANIFEST_SCHEMA_VERSION:
            raise FaultMutationError("unsupported evaluator-only gold manifest schema")
        if self.evaluator_only is not True:
            raise FaultMutationError("fault gold manifests must remain evaluator_only")
        if self.gold_stage not in APPROVED_GOLD_STAGES:
            raise FaultMutationError("gold_stage is not an approved D03 stage")
        if not isinstance(self.gold_repairable, bool):
            raise FaultMutationError("gold_repairable must be an explicit boolean")
        if not isinstance(self.gold_allowed_scope, tuple):
            raise FaultMutationError("gold_allowed_scope must be an immutable tuple")
        if tuple(sorted(self.gold_allowed_scope)) != self.gold_allowed_scope or len(self.gold_allowed_scope) != len(set(self.gold_allowed_scope)):
            raise FaultMutationError("gold_allowed_scope must be sorted and unique")
        for item in self.gold_allowed_scope:
            _require_text(item, "gold_allowed_scope entry")
        for name in ("fault_copy_sha256", "injector_audit_sha256"):
            value = getattr(self, name)
            if len(value) != 64 or any(item not in "0123456789abcdef" for item in value):
                raise FaultMutationError(name + " must be a lowercase SHA-256")
        expected = "fault-gold-" + canonical_sha256(self.to_payload())[:20]
        if self.gold_manifest_id != expected:
            raise FaultMutationError("gold_manifest_id does not match canonical payload")

    def validate_against(self, runtime_record: FaultCopyRecord, injector_audit: InjectorMutationAudit) -> None:
        self.validate()
        runtime_record.validate()
        injector_audit.validate_against(runtime_record)
        if self.fault_copy_id != runtime_record.fault_copy_id or self.fault_copy_sha256 != runtime_record.sha256():
            raise FaultMutationError("gold runtime binding does not match FaultCopyRecord")
        if self.injector_audit_id != injector_audit.injector_audit_id or self.injector_audit_sha256 != injector_audit.sha256():
            raise FaultMutationError("gold injector binding does not match InjectorMutationAudit")
        if self.case_id != runtime_record.case_id or self.case_id != injector_audit.case_id:
            raise FaultMutationError("gold case_id does not match runtime and injector records")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def sha256(self) -> str:
        self.validate()
        return canonical_sha256(self.to_dict())


def build_fault_gold_manifest(
    *,
    fault_copy_record: FaultCopyRecord,
    injector_audit: InjectorMutationAudit,
    gold_stage: str,
    gold_error: str,
    gold_repairable: bool,
    gold_allowed_scope: tuple[str, ...],
    expected_fallback: str,
) -> FaultGoldManifest:
    """Build gold only from explicit labels and matching runtime/audit identities."""
    fault_copy_record.validate()
    injector_audit.validate_against(fault_copy_record)
    unsigned = FaultGoldManifest(
        "", fault_copy_record.fault_copy_id, fault_copy_record.sha256(),
        injector_audit.injector_audit_id, injector_audit.sha256(),
        fault_copy_record.case_id, gold_stage, gold_error, gold_repairable,
        gold_allowed_scope, expected_fallback,
    )
    manifest = replace(unsigned, gold_manifest_id="fault-gold-" + canonical_sha256(unsigned.to_payload())[:20])
    manifest.validate_against(fault_copy_record, injector_audit)
    return manifest


def write_fault_gold_manifest(manifest: FaultGoldManifest, output_dir) -> FaultGoldManifest:
    """Write exactly one canonical evaluator-only manifest into an empty directory."""
    manifest.validate()
    destination = _prepare_empty_output_directory(output_dir)
    target = destination / "fault_gold_manifest.json"
    content = canonical_json_bytes(manifest.to_dict())
    _write_exact_bytes(target, content)
    if target.read_bytes() != content:
        raise FaultMutationError("evaluator-only gold manifest bytes did not round-trip")
    if [item.name for item in destination.iterdir()] != ["fault_gold_manifest.json"]:
        raise FaultMutationError("evaluator-only gold writer must not copy runtime artifacts")
    return manifest
