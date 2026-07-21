"""Deterministic development recovery-outcome routing for M2 fault copies."""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, Mapping
import uuid

from req2web_generation.result_package_v2 import (
    RESULT_PACKAGE_V2_SCHEMA_VERSION,
    RetrievalEnhancedResultPackage,
    RetrievalEnhancedResultPackageError,
)

from .bundle import (
    BLINDED_FAULT_BUNDLE_SCHEMA_VERSION,
    BlindedBundleInventoryParityReport,
    BlindedFaultBundleRecord,
    FaultBundleError,
    load_blinded_fault_bundle,
)
from .detector import (
    NO_FAULT_DETECTED,
    FaultDetectionError,
    FaultDetectionReport,
    detect_blinded_fault_bundle,
)
from .fallback_delivery import (
    FallbackDeliveryError,
    FallbackDeliveryReport,
    FrozenG0FallbackRecord,
    deliver_frozen_g0_fallback,
)
from .repair_executor import (
    REPAIR_EXECUTION_REPORT_MANIFEST_SCHEMA_VERSION,
    RepairExecutionError,
    RepairExecutionFileDelta,
    RepairExecutionReport,
    execute_authorized_deterministic_repair,
)
from .repair_policy import (
    DETERMINISTIC_REPAIR_AUTHORIZED,
    FALLBACK_REQUIRED,
    NO_ACTION,
    REPAIR_AUTHORIZATION_SCHEMA_VERSION,
    RepairAuthorization,
    RepairPolicyError,
)

DETERMINISTIC_RECOVERY_OUTCOME_SCHEMA_VERSION = "req2web.deterministic_recovery_outcome.v1"
DETERMINISTIC_RECOVERY_OUTCOME_MANIFEST_SCHEMA_VERSION = "req2web.deterministic_recovery_outcome_manifest.v1"
FIRST_PASS_SUCCESS = "first_pass_success"
RECOVERED_SUCCESS = "recovered_success"
FALLBACK_DELIVERY = "fallback_delivery"
FAILED_DELIVERY = "failed_delivery"
RECOVERY_OUTCOME_STATUSES = (FIRST_PASS_SUCCESS, RECOVERED_SUCCESS, FALLBACK_DELIVERY, FAILED_DELIVERY)
M2_DEVELOPMENT_FAULT_COPY_SCOPE = "m2_development_fault_copy"

_FAILURE_REASON_CODES = frozenset({
    "fallback_record_missing", "fallback_record_invalid", "fallback_case_mismatch",
    "fallback_page_mismatch", "fallback_snapshot_missing", "fallback_snapshot_invalid",
    "fallback_delivery_failed",
})
_REPAIR_FAILURE_REASON_CODE = "repair_execution_failed"
_OUTCOME_FILE = "deterministic_recovery_outcome.json"
_MANIFEST_FILE = "deterministic_recovery_outcome_manifest.json"
_REPAIR_DIRECTORY = "repair"
_FALLBACK_DIRECTORY = "fallback"
_SOURCE_PACKAGE_PATH = "artifact/result_package"
_REPAIRED_PACKAGE_PATH = "repair/repaired_bundle/artifact/result_package"
_FALLBACK_PACKAGE_PATH = "fallback/result_package"

class DeterministicRecoveryOutcomeError(ValueError):
    """A local recovery input or emitted outcome is unsafe."""

def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def _digest(value: object) -> str:
    return sha256(_json_bytes(value)).hexdigest()

def _sha(content: bytes) -> str:
    return sha256(content).hexdigest()

def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeterministicRecoveryOutcomeError(name + " must be non-empty text")
    return value

def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise DeterministicRecoveryOutcomeError(name + " must be a lowercase SHA-256 hex digest")
    return value

def _safe_path(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise DeterministicRecoveryOutcomeError(name + " must be a safe POSIX relative path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise DeterministicRecoveryOutcomeError(name + " must not traverse or escape its root")
    return value

def _object(content: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeterministicRecoveryOutcomeError(name + " must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise DeterministicRecoveryOutcomeError(name + " must be a JSON object")
    return value

@dataclass(frozen=True)
class RecoveryOutcomeFile:
    path: str
    size: int
    sha256: str

    def validate(self) -> None:
        _safe_path(self.path, "outcome file path")
        if not isinstance(self.size, int) or self.size < 0:
            raise DeterministicRecoveryOutcomeError("outcome file size must be a non-negative integer")
        _hash(self.sha256, "outcome file sha256")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"path": self.path, "size": self.size, "sha256": self.sha256}

@dataclass(frozen=True)
class _PackageEvidence:
    validation_status: str
    package_id: str | None
    page_id: str | None
    schema_version: str | None
    manifest_sha256: str | None
    tree_sha256: str

@dataclass(frozen=True)
class DeterministicRecoveryOutcome:
    outcome_id: str
    outcome_sha256: str
    scope: str
    case_id: str
    page_id: str
    source_bundle_id: str
    source_bundle_record_sha256: str
    source_bundle_inventory_sha256: str
    source_bundle_schema_version: str
    parity_schema_version: str
    parity_path_set_sha256: str
    detector_report_id: str
    detector_report_sha256: str
    authorization_id: str
    authorization_sha256: str
    authorization_schema_version: str
    source_package_validation_status: str
    source_package_id: str | None
    source_package_schema_version: str | None
    source_package_manifest_sha256: str | None
    source_package_tree_sha256: str
    status: str
    action: str
    delivery_source: str
    repair_attempted: bool
    repair_succeeded: bool
    repair_failure_reason_code: str | None
    fallback_attempted: bool
    fallback_succeeded: bool
    failure_reason_code: str | None
    repair_execution_id: str | None
    repair_execution_sha256: str | None
    repaired_bundle_id: str | None
    repaired_bundle_record_sha256: str | None
    repaired_package_id: str | None
    repaired_package_schema_version: str | None
    repaired_package_manifest_sha256: str | None
    repaired_package_tree_sha256: str | None
    frozen_fallback_record_id: str | None
    frozen_fallback_record_sha256: str | None
    fallback_delivery_report_id: str | None
    fallback_delivery_report_sha256: str | None
    fallback_package_id: str | None
    fallback_package_schema_version: str | None
    fallback_package_manifest_sha256: str | None
    fallback_package_tree_sha256: str | None
    final_artifact_root: str
    final_artifact_relative_path: str | None
    branch_files: tuple[RecoveryOutcomeFile, ...]
    schema_version: str = DETERMINISTIC_RECOVERY_OUTCOME_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "action": self.action, "authorization_id": self.authorization_id,
            "authorization_schema_version": self.authorization_schema_version,
            "authorization_sha256": self.authorization_sha256,
            "branch_files": [item.to_dict() for item in self.branch_files],
            "case_id": self.case_id, "delivery_source": self.delivery_source,
            "detector_report_id": self.detector_report_id,
            "detector_report_sha256": self.detector_report_sha256,
            "failure_reason_code": self.failure_reason_code,
            "fallback_attempted": self.fallback_attempted,
            "fallback_delivery_report_id": self.fallback_delivery_report_id,
            "fallback_delivery_report_sha256": self.fallback_delivery_report_sha256,
            "fallback_package_id": self.fallback_package_id,
            "fallback_package_manifest_sha256": self.fallback_package_manifest_sha256,
            "fallback_package_schema_version": self.fallback_package_schema_version,
            "fallback_package_tree_sha256": self.fallback_package_tree_sha256,
            "fallback_succeeded": self.fallback_succeeded,
            "final_artifact_relative_path": self.final_artifact_relative_path,
            "final_artifact_root": self.final_artifact_root,
            "frozen_fallback_record_id": self.frozen_fallback_record_id,
            "frozen_fallback_record_sha256": self.frozen_fallback_record_sha256,
            "page_id": self.page_id, "parity_path_set_sha256": self.parity_path_set_sha256,
            "parity_schema_version": self.parity_schema_version,
            "repair_attempted": self.repair_attempted,
            "repair_execution_id": self.repair_execution_id,
            "repair_execution_sha256": self.repair_execution_sha256,
            "repair_failure_reason_code": self.repair_failure_reason_code,
            "repair_succeeded": self.repair_succeeded,
            "repaired_bundle_id": self.repaired_bundle_id,
            "repaired_bundle_record_sha256": self.repaired_bundle_record_sha256,
            "repaired_package_id": self.repaired_package_id,
            "repaired_package_manifest_sha256": self.repaired_package_manifest_sha256,
            "repaired_package_schema_version": self.repaired_package_schema_version,
            "repaired_package_tree_sha256": self.repaired_package_tree_sha256,
            "schema_version": self.schema_version, "scope": self.scope,
            "source_bundle_id": self.source_bundle_id,
            "source_bundle_inventory_sha256": self.source_bundle_inventory_sha256,
            "source_bundle_record_sha256": self.source_bundle_record_sha256,
            "source_bundle_schema_version": self.source_bundle_schema_version,
            "source_package_id": self.source_package_id,
            "source_package_manifest_sha256": self.source_package_manifest_sha256,
            "source_package_validation_status": self.source_package_validation_status,
            "source_package_schema_version": self.source_package_schema_version,
            "source_package_tree_sha256": self.source_package_tree_sha256,
            "status": self.status,
        }

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"outcome_id": self.outcome_id, "outcome_sha256": self.outcome_sha256, **self.to_payload()}

    def validate(self) -> None:
        for name in (
            "outcome_id", "scope", "case_id", "page_id", "source_bundle_id",
            "source_bundle_schema_version", "parity_schema_version", "detector_report_id",
            "authorization_id", "authorization_schema_version", "source_package_validation_status",
            "status", "action", "delivery_source", "final_artifact_root",
        ):
            _text(getattr(self, name), name)
        if self.schema_version != DETERMINISTIC_RECOVERY_OUTCOME_SCHEMA_VERSION:
            raise DeterministicRecoveryOutcomeError("unsupported deterministic recovery outcome schema")
        if self.scope != M2_DEVELOPMENT_FAULT_COPY_SCOPE:
            raise DeterministicRecoveryOutcomeError("outcome scope is not restricted to M2 development fault copies")
        if self.source_bundle_schema_version != BLINDED_FAULT_BUNDLE_SCHEMA_VERSION:
            raise DeterministicRecoveryOutcomeError("outcome binds an unsupported blinded bundle schema")
        if self.parity_schema_version != "req2web.blinded_bundle_inventory_parity.v1":
            raise DeterministicRecoveryOutcomeError("outcome binds an unsupported parity schema")
        if self.authorization_schema_version != REPAIR_AUTHORIZATION_SCHEMA_VERSION:
            raise DeterministicRecoveryOutcomeError("outcome binds an unsupported authorization schema")
        if self.source_package_validation_status not in {"valid", "invalid_observed"}:
            raise DeterministicRecoveryOutcomeError("outcome source package validation status is unsupported")
        if self.source_package_validation_status == "valid":
            if not isinstance(self.source_package_id, str) or not self.source_package_id:
                raise DeterministicRecoveryOutcomeError("valid source package must bind package_id")
            if self.source_package_schema_version != RESULT_PACKAGE_V2_SCHEMA_VERSION:
                raise DeterministicRecoveryOutcomeError("valid source package must be ResultPackage v2")
            if self.source_package_manifest_sha256 is None:
                raise DeterministicRecoveryOutcomeError("valid source package must bind manifest bytes")
        elif self.source_package_id is not None or self.source_package_schema_version is not None:
            raise DeterministicRecoveryOutcomeError("invalid-observed source package must not claim identity or schema")
        for name in (
            "outcome_sha256", "source_bundle_record_sha256", "source_bundle_inventory_sha256",
            "parity_path_set_sha256", "detector_report_sha256", "authorization_sha256",
            "source_package_tree_sha256",
        ):
            _hash(getattr(self, name), name)
        if self.source_package_manifest_sha256 is not None:
            _hash(self.source_package_manifest_sha256, "source_package_manifest_sha256")
        if self.status not in RECOVERY_OUTCOME_STATUSES:
            raise DeterministicRecoveryOutcomeError("outcome status is unsupported")
        if not all(isinstance(value, bool) for value in (
            self.repair_attempted, self.repair_succeeded, self.fallback_attempted, self.fallback_succeeded,
        )):
            raise DeterministicRecoveryOutcomeError("route state must use booleans")
        if self.repair_succeeded and not self.repair_attempted:
            raise DeterministicRecoveryOutcomeError("repair cannot succeed without an attempt")
        if self.fallback_succeeded and not self.fallback_attempted:
            raise DeterministicRecoveryOutcomeError("fallback cannot succeed without an attempt")
        if self.repair_failure_reason_code not in {None, _REPAIR_FAILURE_REASON_CODE}:
            raise DeterministicRecoveryOutcomeError("repair failure reason code is unsupported")
        if self.failure_reason_code is not None and self.failure_reason_code not in _FAILURE_REASON_CODES:
            raise DeterministicRecoveryOutcomeError("delivery failure reason code is unsupported")
        if self.final_artifact_relative_path is not None:
            _safe_path(self.final_artifact_relative_path, "final artifact relative path")
        if tuple(sorted(self.branch_files, key=lambda item: item.path)) != self.branch_files:
            raise DeterministicRecoveryOutcomeError("outcome branch files must use canonical path order")
        if len({item.path for item in self.branch_files}) != len(self.branch_files):
            raise DeterministicRecoveryOutcomeError("outcome branch files must be unique")
        for item in self.branch_files:
            if not isinstance(item, RecoveryOutcomeFile):
                raise DeterministicRecoveryOutcomeError("outcome branch files have an unsupported value")
            item.validate()
        self._validate_route_state()
        digest = _digest(self.to_payload())
        if self.outcome_id != "deterministic-recovery-outcome-" + digest[:20] or self.outcome_sha256 != digest:
            raise DeterministicRecoveryOutcomeError("outcome identity does not match canonical payload")

    def _validate_route_state(self) -> None:
        repair_values = (
            self.repair_execution_id, self.repair_execution_sha256, self.repaired_bundle_id,
            self.repaired_bundle_record_sha256, self.repaired_package_id, self.repaired_package_schema_version,
            self.repaired_package_manifest_sha256, self.repaired_package_tree_sha256,
        )
        fallback_values = (
            self.frozen_fallback_record_id, self.frozen_fallback_record_sha256,
            self.fallback_delivery_report_id, self.fallback_delivery_report_sha256,
            self.fallback_package_id, self.fallback_package_schema_version,
            self.fallback_package_manifest_sha256, self.fallback_package_tree_sha256,
        )
        if self.status == FIRST_PASS_SUCCESS:
            good = (self.action == "no_change" and self.delivery_source == "g0_source_observation"
                and not self.repair_attempted and not self.repair_succeeded and not self.fallback_attempted
                and not self.fallback_succeeded and self.repair_failure_reason_code is None
                and self.failure_reason_code is None and not any(value is not None for value in repair_values)
                and not any(value is not None for value in fallback_values) and self.source_package_validation_status == "valid"
                and self.final_artifact_root == "source_bundle"
                and self.final_artifact_relative_path == _SOURCE_PACKAGE_PATH and not self.branch_files)
        elif self.status == RECOVERED_SUCCESS:
            good = (self.action == "deterministic_repair" and self.delivery_source == "repaired_g0_v2"
                and self.repair_attempted and self.repair_succeeded and not self.fallback_attempted
                and not self.fallback_succeeded and self.repair_failure_reason_code is None and self.failure_reason_code is None
                and all(isinstance(value, str) and value for value in repair_values)
                and not any(value is not None for value in fallback_values) and self.final_artifact_root == "outcome_output"
                and self.final_artifact_relative_path == _REPAIRED_PACKAGE_PATH and self.branch_files
                and all(item.path.startswith(_REPAIR_DIRECTORY + "/") for item in self.branch_files))
        elif self.status == FALLBACK_DELIVERY:
            repair_good = ((self.repair_attempted and not self.repair_succeeded and self.repair_failure_reason_code == _REPAIR_FAILURE_REASON_CODE)
                or (not self.repair_attempted and not self.repair_succeeded and self.repair_failure_reason_code is None))
            good = (self.action == "frozen_g0_fallback" and self.delivery_source == "g0_frozen_fallback"
                and repair_good and self.fallback_attempted and self.fallback_succeeded and self.failure_reason_code is None
                and not any(value is not None for value in repair_values)
                and all(isinstance(value, str) and value for value in fallback_values)
                and self.final_artifact_root == "outcome_output" and self.final_artifact_relative_path == _FALLBACK_PACKAGE_PATH
                and self.branch_files and all(item.path.startswith(_FALLBACK_DIRECTORY + "/") for item in self.branch_files))
        else:
            repair_good = ((self.repair_attempted and not self.repair_succeeded and self.repair_failure_reason_code == _REPAIR_FAILURE_REASON_CODE)
                or (not self.repair_attempted and not self.repair_succeeded and self.repair_failure_reason_code is None))
            good = (self.action == "no_delivery" and self.delivery_source == "unavailable" and repair_good
                and self.fallback_attempted and not self.fallback_succeeded and self.failure_reason_code is not None
                and not any(value is not None for value in repair_values) and not any(value is not None for value in fallback_values)
                and self.final_artifact_root == "none" and self.final_artifact_relative_path is None and not self.branch_files)
        if not good:
            raise DeterministicRecoveryOutcomeError("outcome route state is inconsistent")
        for value in (repair_values[1], repair_values[3], repair_values[6], repair_values[7], fallback_values[1], fallback_values[3], fallback_values[6], fallback_values[7]):
            if value is not None:
                _hash(value, "route binding sha256")


    def validate_against(
        self,
        bundle_dir: Path,
        parity_report: BlindedBundleInventoryParityReport,
        report: FaultDetectionReport,
        authorization: RepairAuthorization,
        fallback_snapshot_dir: Path | None,
        fallback_record: FrozenG0FallbackRecord | None,
        output_dir: Path,
    ) -> None:
        self.validate()
        source_root, snapshot, destination, source_record, fresh = _preflight(
            bundle_dir, parity_report, report, authorization, fallback_snapshot_dir, fallback_record, output_dir, new_output=False,
        )
        if destination.is_symlink() or not destination.is_dir():
            raise DeterministicRecoveryOutcomeError("outcome output_dir must be a real directory")
        source_package = _bundle_package(source_root, source_record, require_valid=self.status == FIRST_PASS_SUCCESS)
        _common_binding(self, source_record, parity_report, fresh, authorization, source_package)
        outcome_bytes = _exact_json(destination / _OUTCOME_FILE, self.to_dict(), _OUTCOME_FILE)
        manifest = _manifest(self, outcome_bytes)
        _exact_json(destination / _MANIFEST_FILE, manifest, _MANIFEST_FILE)
        if self.status == FIRST_PASS_SUCCESS:
            if fresh.status != NO_FAULT_DETECTED or authorization.status != NO_ACTION:
                raise DeterministicRecoveryOutcomeError("first-pass outcome does not bind a clean no-action route")
            _exact_tree(destination, (_OUTCOME_FILE, _MANIFEST_FILE, *(item.path for item in self.branch_files)), self.branch_files)
            return
        if self.status == RECOVERED_SUCCESS:
            if authorization.status != DETERMINISTIC_REPAIR_AUTHORIZED:
                raise DeterministicRecoveryOutcomeError("recovered outcome does not bind repair authorization")
            repair_root = destination / _REPAIR_DIRECTORY
            execution = _load_execution(repair_root / "repair_execution_report" / "repair_execution_report.json")
            if (execution.execution_id, execution.sha256()) != (self.repair_execution_id, self.repair_execution_sha256):
                raise DeterministicRecoveryOutcomeError("recovered outcome repair execution binding is incorrect")
            post = detect_blinded_fault_bundle(repair_root / "repaired_bundle", parity_report)
            execution.validate_against(source_root, repair_root / "repaired_bundle", parity_report, fresh, authorization, post)
            repaired_record = load_blinded_fault_bundle(repair_root / "repaired_bundle")
            repaired_package = _bundle_package(repair_root / "repaired_bundle", repaired_record, require_valid=True)
            if (
                self.repaired_bundle_id, self.repaired_bundle_record_sha256, self.repaired_package_id,
                self.repaired_package_schema_version, self.repaired_package_manifest_sha256, self.repaired_package_tree_sha256,
            ) != (
                repaired_record.bundle_id, repaired_record.sha256(), repaired_package.package_id,
                repaired_package.schema_version, repaired_package.manifest_sha256, repaired_package.tree_sha256,
            ):
                raise DeterministicRecoveryOutcomeError("recovered outcome repaired artifact binding is incorrect")
            _exact_tree(destination, (_OUTCOME_FILE, _MANIFEST_FILE, *(item.path for item in self.branch_files)), self.branch_files)
            return
        availability = _fallback_failure(snapshot, fallback_record, fresh)
        if self.status == FALLBACK_DELIVERY:
            if availability is not None or snapshot is None or fallback_record is None:
                raise DeterministicRecoveryOutcomeError("fallback outcome no longer has valid frozen evidence")
            fallback_root = destination / _FALLBACK_DIRECTORY
            delivery = _load_delivery(fallback_root / "fallback_delivery_report.json")
            if (delivery.report_id, delivery.report_sha256) != (self.fallback_delivery_report_id, self.fallback_delivery_report_sha256):
                raise DeterministicRecoveryOutcomeError("fallback outcome delivery report binding is incorrect")
            delivery.validate_against(snapshot, fallback_root / "result_package", fallback_record)
            if (
                self.frozen_fallback_record_id, self.frozen_fallback_record_sha256, self.fallback_package_id,
                self.fallback_package_schema_version, self.fallback_package_manifest_sha256, self.fallback_package_tree_sha256,
            ) != (
                fallback_record.record_id, fallback_record.record_sha256, delivery.package_id,
                delivery.package_schema_version, fallback_record.package_manifest_sha256, delivery.delivered_package_tree_sha256,
            ):
                raise DeterministicRecoveryOutcomeError("fallback outcome frozen package binding is incorrect")
            _exact_tree(destination, (_OUTCOME_FILE, _MANIFEST_FILE, *(item.path for item in self.branch_files)), self.branch_files)
            return
        if self.failure_reason_code == "fallback_delivery_failed":
            if availability is not None:
                raise DeterministicRecoveryOutcomeError("delivery failure cannot replace a prior availability failure")
        elif availability != self.failure_reason_code:
            raise DeterministicRecoveryOutcomeError("failed-delivery reason does not match frozen fallback availability")
        _exact_tree(destination, (_OUTCOME_FILE, _MANIFEST_FILE, *(item.path for item in self.branch_files)), self.branch_files)


def execute_deterministic_development_recovery(
    bundle_dir: Path,
    parity_report: BlindedBundleInventoryParityReport,
    report: FaultDetectionReport,
    authorization: RepairAuthorization,
    fallback_snapshot_dir: Path | None,
    fallback_record: FrozenG0FallbackRecord | None,
    output_dir: Path,
) -> DeterministicRecoveryOutcome:
    """Emit a deterministic development-only recovery outcome into a fresh directory."""
    source_root, snapshot, destination, source_record, fresh = _preflight(
        bundle_dir, parity_report, report, authorization, fallback_snapshot_dir, fallback_record, output_dir, new_output=True,
    )
    source_package = _bundle_package(source_root, source_record, require_valid=fresh.status == NO_FAULT_DETECTED)
    stage = _stage(destination)
    try:
        if fresh.status == NO_FAULT_DETECTED:
            if authorization.status != NO_ACTION:
                raise DeterministicRecoveryOutcomeError("clean detector report does not bind no_action")
            outcome = _make_outcome(
                source_record, parity_report, fresh, authorization, source_package,
                status=FIRST_PASS_SUCCESS, action="no_change", delivery_source="g0_source_observation",
                repair_attempted=False, repair_succeeded=False, repair_failure_reason_code=None,
                fallback_attempted=False, fallback_succeeded=False, failure_reason_code=None,
                final_artifact_root="source_bundle", final_artifact_relative_path=_SOURCE_PACKAGE_PATH, branch_files=(),
            )
        elif authorization.status == FALLBACK_REQUIRED:
            outcome = _fallback_or_failed(
                source_record, parity_report, fresh, authorization, source_package, stage, snapshot, fallback_record,
                repair_attempted=False, repair_failure_reason_code=None,
            )
        elif authorization.status == DETERMINISTIC_REPAIR_AUTHORIZED:
            outcome = _repair_or_fallback(
                source_root, source_record, parity_report, fresh, authorization, source_package, stage, snapshot, fallback_record,
            )
        else:
            raise DeterministicRecoveryOutcomeError("authorization status is unsupported for recovery routing")
        _write_exact(stage / _OUTCOME_FILE, _json_bytes(outcome.to_dict()))
        _write_exact(stage / _MANIFEST_FILE, _json_bytes(_manifest(outcome, (stage / _OUTCOME_FILE).read_bytes())))
        outcome.validate_against(source_root, parity_report, fresh, authorization, snapshot, fallback_record, stage)
        _commit(stage, destination)
        stage = None
        try:
            outcome.validate_against(source_root, parity_report, fresh, authorization, snapshot, fallback_record, destination)
        except Exception:
            _remove_destination(destination, source_root, snapshot)
            raise
        return outcome
    finally:
        if stage is not None and (stage.exists() or stage.is_symlink()):
            _remove_stage(stage)


def _repair_or_fallback(
    source_root: Path,
    source_record: BlindedFaultBundleRecord,
    parity: BlindedBundleInventoryParityReport,
    report: FaultDetectionReport,
    authorization: RepairAuthorization,
    source_package: _PackageEvidence,
    stage: Path,
    snapshot: Path | None,
    fallback_record: FrozenG0FallbackRecord | None,
) -> DeterministicRecoveryOutcome:
    repair_root = stage / _REPAIR_DIRECTORY
    try:
        execution = execute_authorized_deterministic_repair(source_root, parity, report, authorization, repair_root)
        repaired_record = load_blinded_fault_bundle(repair_root / "repaired_bundle")
        repaired_package = _bundle_package(repair_root / "repaired_bundle", repaired_record, require_valid=True)
        post = detect_blinded_fault_bundle(repair_root / "repaired_bundle", parity)
        execution.validate_against(source_root, repair_root / "repaired_bundle", parity, report, authorization, post)
        return _make_outcome(
            source_record, parity, report, authorization, source_package,
            status=RECOVERED_SUCCESS, action="deterministic_repair", delivery_source="repaired_g0_v2",
            repair_attempted=True, repair_succeeded=True, repair_failure_reason_code=None,
            fallback_attempted=False, fallback_succeeded=False, failure_reason_code=None,
            repair_execution=execution, repaired_record=repaired_record, repaired_package=repaired_package,
            final_artifact_root="outcome_output", final_artifact_relative_path=_REPAIRED_PACKAGE_PATH,
            branch_files=_branch_inventory(stage, _REPAIR_DIRECTORY),
        )
    except Exception:
        _remove_child(repair_root, stage)
        return _fallback_or_failed(
            source_record, parity, report, authorization, source_package, stage, snapshot, fallback_record,
            repair_attempted=True, repair_failure_reason_code=_REPAIR_FAILURE_REASON_CODE,
        )


def _fallback_or_failed(
    source_record: BlindedFaultBundleRecord,
    parity: BlindedBundleInventoryParityReport,
    report: FaultDetectionReport,
    authorization: RepairAuthorization,
    source_package: _PackageEvidence,
    stage: Path,
    snapshot: Path | None,
    fallback_record: FrozenG0FallbackRecord | None,
    *,
    repair_attempted: bool,
    repair_failure_reason_code: str | None,
) -> DeterministicRecoveryOutcome:
    failure = _fallback_failure(snapshot, fallback_record, report)
    if failure is not None:
        return _make_outcome(
            source_record, parity, report, authorization, source_package,
            status=FAILED_DELIVERY, action="no_delivery", delivery_source="unavailable",
            repair_attempted=repair_attempted, repair_succeeded=False, repair_failure_reason_code=repair_failure_reason_code,
            fallback_attempted=True, fallback_succeeded=False, failure_reason_code=failure,
            final_artifact_root="none", final_artifact_relative_path=None, branch_files=(),
        )
    assert snapshot is not None and fallback_record is not None
    fallback_root = stage / _FALLBACK_DIRECTORY
    try:
        delivery = deliver_frozen_g0_fallback(snapshot, fallback_record, fallback_root)
        delivery.validate_against(snapshot, fallback_root / "result_package", fallback_record)
        return _make_outcome(
            source_record, parity, report, authorization, source_package,
            status=FALLBACK_DELIVERY, action="frozen_g0_fallback", delivery_source="g0_frozen_fallback",
            repair_attempted=repair_attempted, repair_succeeded=False, repair_failure_reason_code=repair_failure_reason_code,
            fallback_attempted=True, fallback_succeeded=True, failure_reason_code=None,
            fallback_record=fallback_record, fallback_delivery=delivery,
            final_artifact_root="outcome_output", final_artifact_relative_path=_FALLBACK_PACKAGE_PATH,
            branch_files=_branch_inventory(stage, _FALLBACK_DIRECTORY),
        )
    except Exception:
        _remove_child(fallback_root, stage)
        return _make_outcome(
            source_record, parity, report, authorization, source_package,
            status=FAILED_DELIVERY, action="no_delivery", delivery_source="unavailable",
            repair_attempted=repair_attempted, repair_succeeded=False, repair_failure_reason_code=repair_failure_reason_code,
            fallback_attempted=True, fallback_succeeded=False, failure_reason_code="fallback_delivery_failed",
            final_artifact_root="none", final_artifact_relative_path=None, branch_files=(),
        )


def _validate_supplied_fallback_record(
    fallback_record: FrozenG0FallbackRecord | None,
    report: FaultDetectionReport,
) -> None:
    if fallback_record is None:
        return
    if not isinstance(fallback_record, FrozenG0FallbackRecord):
        raise DeterministicRecoveryOutcomeError("provided fallback_record must be FrozenG0FallbackRecord")
    try:
        fallback_record.validate()
    except (FallbackDeliveryError, ValueError, TypeError) as error:
        raise DeterministicRecoveryOutcomeError("provided fallback_record is invalid") from error
    if fallback_record.case_id != report.case_id:
        raise DeterministicRecoveryOutcomeError("provided fallback_record case_id does not match detector report")
    if fallback_record.page_id != report.page_id:
        raise DeterministicRecoveryOutcomeError("provided fallback_record page_id does not match detector report")


def _fallback_failure(
    snapshot: Path | None,
    fallback_record: FrozenG0FallbackRecord | None,
    report: FaultDetectionReport,
) -> str | None:
    if fallback_record is None:
        return "fallback_record_missing"
    if not isinstance(fallback_record, FrozenG0FallbackRecord):
        return "fallback_record_invalid"
    try:
        fallback_record.validate()
    except (FallbackDeliveryError, ValueError, TypeError):
        return "fallback_record_invalid"
    if fallback_record.case_id != report.case_id:
        return "fallback_case_mismatch"
    if fallback_record.page_id != report.page_id:
        return "fallback_page_mismatch"
    if snapshot is None or not snapshot.exists():
        return "fallback_snapshot_missing"
    try:
        fallback_record.validate_against(snapshot)
    except (FallbackDeliveryError, ValueError, TypeError):
        return "fallback_snapshot_invalid"
    return None


def _make_outcome(
    source_record: BlindedFaultBundleRecord,
    parity: BlindedBundleInventoryParityReport,
    report: FaultDetectionReport,
    authorization: RepairAuthorization,
    source_package: _PackageEvidence,
    *,
    status: str,
    action: str,
    delivery_source: str,
    repair_attempted: bool,
    repair_succeeded: bool,
    repair_failure_reason_code: str | None,
    fallback_attempted: bool,
    fallback_succeeded: bool,
    failure_reason_code: str | None,
    repair_execution: RepairExecutionReport | None = None,
    repaired_record: BlindedFaultBundleRecord | None = None,
    repaired_package: _PackageEvidence | None = None,
    fallback_record: FrozenG0FallbackRecord | None = None,
    fallback_delivery: FallbackDeliveryReport | None = None,
    final_artifact_root: str,
    final_artifact_relative_path: str | None,
    branch_files: tuple[RecoveryOutcomeFile, ...],
) -> DeterministicRecoveryOutcome:
    value = DeterministicRecoveryOutcome(
        outcome_id="", outcome_sha256="0" * 64, scope=M2_DEVELOPMENT_FAULT_COPY_SCOPE,
        case_id=report.case_id, page_id=report.page_id, source_bundle_id=source_record.bundle_id,
        source_bundle_record_sha256=source_record.sha256(), source_bundle_inventory_sha256=source_record.inventory_sha256,
        source_bundle_schema_version=source_record.schema_version, parity_schema_version=parity.schema_version,
        parity_path_set_sha256=parity.path_set_sha256, detector_report_id=report.report_id,
        detector_report_sha256=report.sha256(), authorization_id=authorization.authorization_id,
        authorization_sha256=authorization.sha256(), authorization_schema_version=authorization.schema_version,
        source_package_validation_status=source_package.validation_status, source_package_id=source_package.package_id,
        source_package_schema_version=source_package.schema_version, source_package_manifest_sha256=source_package.manifest_sha256,
        source_package_tree_sha256=source_package.tree_sha256,
        status=status, action=action, delivery_source=delivery_source, repair_attempted=repair_attempted,
        repair_succeeded=repair_succeeded, repair_failure_reason_code=repair_failure_reason_code,
        fallback_attempted=fallback_attempted, fallback_succeeded=fallback_succeeded, failure_reason_code=failure_reason_code,
        repair_execution_id=repair_execution.execution_id if repair_execution else None,
        repair_execution_sha256=repair_execution.sha256() if repair_execution else None,
        repaired_bundle_id=repaired_record.bundle_id if repaired_record else None,
        repaired_bundle_record_sha256=repaired_record.sha256() if repaired_record else None,
        repaired_package_id=repaired_package.package_id if repaired_package else None,
        repaired_package_schema_version=repaired_package.schema_version if repaired_package else None,
        repaired_package_manifest_sha256=repaired_package.manifest_sha256 if repaired_package else None,
        repaired_package_tree_sha256=repaired_package.tree_sha256 if repaired_package else None,
        frozen_fallback_record_id=fallback_record.record_id if fallback_record else None,
        frozen_fallback_record_sha256=fallback_record.record_sha256 if fallback_record else None,
        fallback_delivery_report_id=fallback_delivery.report_id if fallback_delivery else None,
        fallback_delivery_report_sha256=fallback_delivery.report_sha256 if fallback_delivery else None,
        fallback_package_id=fallback_delivery.package_id if fallback_delivery else None,
        fallback_package_schema_version=fallback_delivery.package_schema_version if fallback_delivery else None,
        fallback_package_manifest_sha256=fallback_record.package_manifest_sha256 if fallback_record else None,
        fallback_package_tree_sha256=fallback_delivery.delivered_package_tree_sha256 if fallback_delivery else None,
        final_artifact_root=final_artifact_root, final_artifact_relative_path=final_artifact_relative_path,
        branch_files=branch_files,
    )
    digest = _digest(value.to_payload())
    return replace(value, outcome_id="deterministic-recovery-outcome-" + digest[:20], outcome_sha256=digest)


def _preflight(
    bundle_dir: Path,
    parity: BlindedBundleInventoryParityReport,
    report: FaultDetectionReport,
    authorization: RepairAuthorization,
    fallback_snapshot_dir: Path | None,
    fallback_record: FrozenG0FallbackRecord | None,
    output_dir: Path,
    *,
    new_output: bool,
) -> tuple[Path, Path | None, Path, BlindedFaultBundleRecord, FaultDetectionReport]:
    if not isinstance(parity, BlindedBundleInventoryParityReport):
        raise DeterministicRecoveryOutcomeError("parity_report must be BlindedBundleInventoryParityReport")
    if not isinstance(report, FaultDetectionReport):
        raise DeterministicRecoveryOutcomeError("report must be FaultDetectionReport")
    if not isinstance(authorization, RepairAuthorization):
        raise DeterministicRecoveryOutcomeError("authorization must be RepairAuthorization")
    source = _real_dir(bundle_dir, "bundle_dir")
    snapshot = _candidate(fallback_snapshot_dir, "fallback_snapshot_dir") if fallback_snapshot_dir is not None else None
    destination = _candidate(output_dir, "output_dir")
    _disjoint(source, destination, "source bundle and output_dir must be disjoint")
    if snapshot is not None:
        _disjoint(source, snapshot, "source bundle and fallback snapshot must be disjoint")
        _disjoint(snapshot, destination, "fallback snapshot and output_dir must be disjoint")
    if new_output and destination.exists():
        raise DeterministicRecoveryOutcomeError("output_dir must be a new path; refusing to overwrite")
    if not new_output and destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise DeterministicRecoveryOutcomeError("outcome output_dir must be a real directory")
    try:
        parity.validate()
        record = load_blinded_fault_bundle(source)
        fresh = detect_blinded_fault_bundle(source, parity)
        report.validate()
    except (FaultBundleError, FaultDetectionError, ValueError, TypeError) as error:
        raise DeterministicRecoveryOutcomeError("source bundle, parity, or detector report validation failed") from error
    if fresh.to_dict() != report.to_dict():
        raise DeterministicRecoveryOutcomeError("caller report does not exactly match fresh detector output")
    try:
        authorization.validate_against(report)
        authorization.validate_against(fresh)
    except (RepairPolicyError, ValueError, TypeError) as error:
        raise DeterministicRecoveryOutcomeError("authorization does not bind the validated detector report") from error
    _validate_supplied_fallback_record(fallback_record, fresh)
    return source, snapshot, destination, record, fresh


def _bundle_package(root: Path, record: BlindedFaultBundleRecord, *, require_valid: bool) -> _PackageEvidence:
    """Observe source package bytes without trusting detector-classified invalid metadata."""
    package_root = _real_dir(Path(root) / "artifact" / "result_package", "bundle result package")
    tree_sha256 = _tree_digest(_regular_tree(package_root, "bundle result package"))
    manifest_path = package_root / "package_manifest.json"
    manifest_bytes: bytes | None = None
    if not manifest_path.is_symlink() and manifest_path.is_file():
        manifest_bytes = manifest_path.read_bytes()
    try:
        if manifest_bytes is None:
            raise DeterministicRecoveryOutcomeError("bundle result package manifest is missing or unsafe")
        manifest = _object(manifest_bytes, "bundle result package manifest")
        package_id, page_id = manifest.get("package_id"), manifest.get("page_id")
        if not isinstance(package_id, str) or not package_id or not isinstance(page_id, str) or page_id != record.page_id:
            raise DeterministicRecoveryOutcomeError("bundle result package identity is invalid")
        if manifest.get("schema_version") != RESULT_PACKAGE_V2_SCHEMA_VERSION:
            raise DeterministicRecoveryOutcomeError("bundle result package schema is invalid")
        RetrievalEnhancedResultPackage(package_id, page_id, package_root).validate()
    except (DeterministicRecoveryOutcomeError, RetrievalEnhancedResultPackageError, ValueError, TypeError) as error:
        if require_valid:
            raise DeterministicRecoveryOutcomeError("bundle result package v2 validation failed") from error
        return _PackageEvidence(
            validation_status="invalid_observed", package_id=None, page_id=None, schema_version=None,
            manifest_sha256=_sha(manifest_bytes) if manifest_bytes is not None else None,
            tree_sha256=tree_sha256,
        )
    return _PackageEvidence(
        validation_status="valid", package_id=package_id, page_id=page_id,
        schema_version=RESULT_PACKAGE_V2_SCHEMA_VERSION, manifest_sha256=_sha(manifest_bytes),
        tree_sha256=tree_sha256,
    )


def _common_binding(
    outcome: DeterministicRecoveryOutcome,
    record: BlindedFaultBundleRecord,
    parity: BlindedBundleInventoryParityReport,
    report: FaultDetectionReport,
    authorization: RepairAuthorization,
    source_package: _PackageEvidence,
) -> None:
    expected = {
        "case_id": report.case_id, "page_id": report.page_id, "source_bundle_id": record.bundle_id,
        "source_bundle_record_sha256": record.sha256(), "source_bundle_inventory_sha256": record.inventory_sha256,
        "source_bundle_schema_version": record.schema_version, "parity_schema_version": parity.schema_version,
        "parity_path_set_sha256": parity.path_set_sha256, "detector_report_id": report.report_id,
        "detector_report_sha256": report.sha256(), "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization.sha256(), "authorization_schema_version": authorization.schema_version,
        "source_package_validation_status": source_package.validation_status, "source_package_id": source_package.package_id,
        "source_package_schema_version": source_package.schema_version, "source_package_manifest_sha256": source_package.manifest_sha256,
        "source_package_tree_sha256": source_package.tree_sha256,
    }
    for name, value in expected.items():
        if getattr(outcome, name) != value:
            raise DeterministicRecoveryOutcomeError("outcome " + name + " does not match current source evidence")


def _branch_inventory(stage: Path, prefix: str) -> tuple[RecoveryOutcomeFile, ...]:
    files = _regular_tree(stage, "outcome staging tree")
    return tuple(
        RecoveryOutcomeFile(path, len(content), _sha(content))
        for path, content in files.items()
        if path.startswith(prefix + "/")
    )


def _manifest(outcome: DeterministicRecoveryOutcome, outcome_bytes: bytes) -> dict[str, object]:
    return {
        "branch_files": [item.to_dict() for item in outcome.branch_files],
        "outcome": {"path": _OUTCOME_FILE, "size": len(outcome_bytes), "sha256": _sha(outcome_bytes)},
        "root_file_paths": [_OUTCOME_FILE, _MANIFEST_FILE, *(item.path for item in outcome.branch_files)],
        "schema_version": DETERMINISTIC_RECOVERY_OUTCOME_MANIFEST_SCHEMA_VERSION,
    }


def _load_execution(path: Path) -> RepairExecutionReport:
    raw = _canonical_file_object(path, "repair execution report")
    expected = {
        "execution_id", "source_bundle_id", "source_bundle_record_sha256", "source_bundle_inventory_sha256",
        "source_bundle_schema_version", "repaired_bundle_id", "repaired_bundle_record_sha256",
        "repaired_bundle_inventory_sha256", "repaired_bundle_schema_version", "detector_report_id",
        "detector_report_sha256", "authorization_id", "authorization_sha256", "error_code",
        "policy_allowed_scope", "predicted_repair_scope", "effective_repair_scope", "actual_repair_scope",
        "administrative_reseal_scope", "file_deltas", "post_detector_report_id",
        "post_detector_report_sha256", "post_detector_status", "schema_version",
    }
    if set(raw) != expected or not isinstance(raw["file_deltas"], list):
        raise DeterministicRecoveryOutcomeError("repair execution report fields are invalid")
    try:
        deltas = tuple(
            RepairExecutionFileDelta(
                path=item["path"], before_size=item["before_size"], before_sha256=item["before_sha256"],
                after_size=item["after_size"], after_sha256=item["after_sha256"],
            )
            for item in raw["file_deltas"] if isinstance(item, dict)
        )
        if len(deltas) != len(raw["file_deltas"]):
            raise ValueError("invalid execution deltas")
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
        raise DeterministicRecoveryOutcomeError("repair execution report is invalid") from error
    report_bytes = path.read_bytes()
    if report_bytes != _json_bytes(report.to_dict()):
        raise DeterministicRecoveryOutcomeError("repair execution report bytes are not canonical")
    manifest_path = path.parent / "repair_execution_report_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise DeterministicRecoveryOutcomeError("repair execution report manifest is missing or unsafe")
    manifest_bytes = manifest_path.read_bytes()
    manifest = _object(manifest_bytes, "repair execution report manifest")
    expected_manifest = {
        "execution_id": report.execution_id,
        "execution_sha256": report.sha256(),
        "files": [{
            "path": "repair_execution_report.json",
            "sha256": _sha(report_bytes),
            "size": len(report_bytes),
        }],
        "schema_version": REPAIR_EXECUTION_REPORT_MANIFEST_SCHEMA_VERSION,
    }
    if manifest != expected_manifest or manifest_bytes != _json_bytes(expected_manifest):
        raise DeterministicRecoveryOutcomeError("repair execution report manifest is not canonical or does not bind report bytes")
    return report


def _load_delivery(path: Path) -> FallbackDeliveryReport:
    raw = _canonical_file_object(path, "fallback delivery report")
    try:
        report = FallbackDeliveryReport.from_dict(raw)
        report.validate()
    except (FallbackDeliveryError, TypeError, ValueError) as error:
        raise DeterministicRecoveryOutcomeError("fallback delivery report is invalid") from error
    if path.read_bytes() != _json_bytes(report.to_dict()):
        raise DeterministicRecoveryOutcomeError("fallback delivery report bytes are not canonical")
    return report


def _canonical_file_object(path: Path, name: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DeterministicRecoveryOutcomeError(name + " is missing or unsafe")
    return _object(path.read_bytes(), name)


def _exact_json(path: Path, expected: dict[str, object], name: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise DeterministicRecoveryOutcomeError(name + " is missing or unsafe")
    actual, wanted = path.read_bytes(), _json_bytes(expected)
    if actual != wanted or _object(actual, name) != expected:
        raise DeterministicRecoveryOutcomeError(name + " bytes are not canonical")
    return actual


def _exact_tree(root: Path, expected_paths: tuple[str, ...], branch_files: tuple[RecoveryOutcomeFile, ...]) -> None:
    actual = _regular_tree(root, "outcome root")
    expected = tuple(sorted(expected_paths))
    if len(set(expected_paths)) != len(expected_paths) or tuple(actual) != expected:
        raise DeterministicRecoveryOutcomeError("outcome root file inventory is not exact")
    entries = {item.path: item for item in branch_files}
    for path, content in actual.items():
        if path in {_OUTCOME_FILE, _MANIFEST_FILE}:
            continue
        entry = entries.get(path)
        if entry is None or entry.size != len(content) or entry.sha256 != _sha(content):
            raise DeterministicRecoveryOutcomeError("outcome branch file binding is incorrect")
    actual_dirs = {
        path.relative_to(root).as_posix() for path in root.rglob("*")
        if path.is_dir() and not path.is_symlink()
    }
    expected_dirs: set[str] = set()
    for path in expected_paths:
        parts = PurePosixPath(path).parts[:-1]
        expected_dirs.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    if actual_dirs != expected_dirs:
        raise DeterministicRecoveryOutcomeError("outcome root directory inventory is not exact")


def _regular_tree(root: Path, name: str) -> dict[str, bytes]:
    root = _real_dir(root, name)
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise DeterministicRecoveryOutcomeError(name + " must not contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise DeterministicRecoveryOutcomeError(name + " contains an unsupported path type")
        relative = path.relative_to(root).as_posix()
        _safe_path(relative, name + " path")
        files[relative] = path.read_bytes()
    return dict(sorted(files.items()))


def _tree_digest(files: Mapping[str, bytes]) -> str:
    return _digest({
        "files": [
            {"path": path, "size": len(content), "sha256": _sha(content)}
            for path, content in sorted(files.items())
        ]
    })


def _candidate(value: Path, name: str) -> Path:
    raw = Path(value)
    if ".." in raw.parts:
        raise DeterministicRecoveryOutcomeError(name + " must not traverse its parent path")
    candidate = raw.absolute()
    _reject_symlink_ancestors(candidate)
    return candidate


def _real_dir(value: Path, name: str) -> Path:
    root = _candidate(value, name)
    if root.is_symlink() or not root.is_dir():
        raise DeterministicRecoveryOutcomeError(name + " must be a real directory")
    return root


def _disjoint(left: Path, right: Path, message: str) -> None:
    left_key = Path(os.path.normcase(os.path.abspath(str(left))))
    right_key = Path(os.path.normcase(os.path.abspath(str(right))))
    if left_key == right_key or left_key in right_key.parents or right_key in left_key.parents:
        raise DeterministicRecoveryOutcomeError(message)


def _stage(destination: Path) -> Path:
    parent = destination.parent
    _reject_symlink_ancestors(parent)
    parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(parent)
    stage = parent / ("." + destination.name + ".staging-" + uuid.uuid4().hex)
    if stage.exists() or stage.is_symlink():
        raise DeterministicRecoveryOutcomeError("outcome staging directory already exists")
    stage.mkdir()
    return stage


def _commit(stage: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise DeterministicRecoveryOutcomeError("outcome output_dir appeared during staging")
    os.replace(stage, destination)


def _remove_destination(destination: Path, source: Path, snapshot: Path | None) -> None:
    _disjoint(destination, source, "generated outcome overlaps source bundle")
    if snapshot is not None:
        _disjoint(destination, snapshot, "generated outcome overlaps fallback snapshot")
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise DeterministicRecoveryOutcomeError("generated outcome destination is unsafe to remove")
        shutil.rmtree(destination)


def _remove_stage(stage: Path) -> None:
    if stage.is_symlink() or not stage.is_dir():
        raise DeterministicRecoveryOutcomeError("outcome staging directory is unsafe to remove")
    shutil.rmtree(stage)


def _remove_child(child: Path, stage: Path) -> None:
    if not child.exists() and not child.is_symlink():
        return
    if child.parent != stage or child.name not in {_REPAIR_DIRECTORY, _FALLBACK_DIRECTORY}:
        raise DeterministicRecoveryOutcomeError("stage child cleanup is unsafe")
    if child.is_symlink() or not child.is_dir():
        raise DeterministicRecoveryOutcomeError("stage child cleanup target is unsafe")
    shutil.rmtree(child)


def _reject_symlink_ancestors(path: Path) -> None:
    current = Path(path).absolute()
    for item in (current, *current.parents):
        if item.exists() and item.is_symlink():
            raise DeterministicRecoveryOutcomeError("path must not use a symlinked ancestor")


def _write_exact(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise DeterministicRecoveryOutcomeError("refusing to overwrite outcome artifact")
    _reject_symlink_ancestors(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(path.parent)
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise DeterministicRecoveryOutcomeError("outcome artifact write did not round trip exactly")
