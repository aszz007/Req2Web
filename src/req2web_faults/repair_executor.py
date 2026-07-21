"""Fail-closed copy-on-write execution for authorized M2 mechanical repairs.

The executor consumes only a parity-validated detector-visible bundle, an
independently recomputed detector report, and a report-bound frozen-policy
authorization. It performs no fallback delivery, semantic repair, model call,
or evaluation-only lookup.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from html.parser import HTMLParser
import copy
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any, Mapping

from .bundle import (
    BLINDED_FAULT_BUNDLE_SCHEMA_VERSION,
    BlindedBundleInventoryParityReport,
    BlindedFaultBundleRecord,
    FaultBundleError,
    _copy_and_reseal_blinded_fault_bundle,
    load_blinded_fault_bundle,
)
from .detector import (
    DOM_COMPONENT_STABLE_ID_MISMATCH,
    FAULT_DETECTED,
    NO_FAULT_DETECTED,
    PACKAGE_MANIFEST_PATH_MISMATCH,
    PACKAGE_MANIFEST_SHA256_MISMATCH,
    FaultDetectionError,
    FaultDetectionReport,
    detect_blinded_fault_bundle,
)
from .repair_policy import (
    DETERMINISTIC_REPAIR_AUTHORIZED,
    RepairAuthorization,
    RepairPolicyError,
)


REPAIR_EXECUTION_REPORT_SCHEMA_VERSION = "req2web.repair_execution_report.v1"
REPAIR_EXECUTION_REPORT_MANIFEST_SCHEMA_VERSION = "req2web.repair_execution_report_manifest.v1"

_REPAIRED_BUNDLE_DIRECTORY = "repaired_bundle"
_EXECUTION_REPORT_DIRECTORY = "repair_execution_report"
_EXECUTION_REPORT_FILE = "repair_execution_report.json"
_EXECUTION_REPORT_MANIFEST_FILE = "repair_execution_report_manifest.json"
_BUNDLE_MANIFEST_FILE = "fault_case_bundle_manifest.json"

_MECHANICAL_POLICY_SCOPES = {
    DOM_COMPONENT_STABLE_ID_MISMATCH: (
        "artifact/render/index.html",
        "artifact/render/render_manifest.json",
    ),
    PACKAGE_MANIFEST_PATH_MISMATCH: (
        "artifact/result_package/package_manifest.json",
    ),
    PACKAGE_MANIFEST_SHA256_MISMATCH: (
        "artifact/result_package/package_manifest.json",
    ),
}
_COMPONENT_ATTRIBUTE_PATTERN = re.compile(
    r"\bdata-component-id\s*=\s*(?P<quote>[\"'])(?P<value>[^\"']*)(?P=quote)",
    flags=re.IGNORECASE,
)


class RepairExecutionError(ValueError):
    """An authorized repair input, local edit, or post-gate failed closed."""


@dataclass(frozen=True)
class RepairExecutionFileDelta:
    """One changed source/repaired bundle file, recorded by exact bytes."""

    path: str
    before_size: int
    before_sha256: str
    after_size: int
    after_sha256: str

    def validate(self) -> None:
        _validate_bundle_delta_path(self.path)
        for field_name in ("before_size", "after_size"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or value < 0:
                raise RepairExecutionError(field_name + " must be a non-negative integer")
        _require_sha256(self.before_sha256, "before_sha256")
        _require_sha256(self.after_sha256, "after_sha256")
        if self.before_size == self.after_size and self.before_sha256 == self.after_sha256:
            raise RepairExecutionError("file delta must represent changed bytes")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class RepairExecutionReport:
    """Immutable evidence for one successful authorized mechanical repair."""

    execution_id: str
    source_bundle_id: str
    source_bundle_record_sha256: str
    source_bundle_inventory_sha256: str
    source_bundle_schema_version: str
    repaired_bundle_id: str
    repaired_bundle_record_sha256: str
    repaired_bundle_inventory_sha256: str
    repaired_bundle_schema_version: str
    detector_report_id: str
    detector_report_sha256: str
    authorization_id: str
    authorization_sha256: str
    error_code: str
    policy_allowed_scope: tuple[str, ...]
    predicted_repair_scope: tuple[str, ...]
    effective_repair_scope: tuple[str, ...]
    actual_repair_scope: tuple[str, ...]
    administrative_reseal_scope: tuple[str, ...]
    file_deltas: tuple[RepairExecutionFileDelta, ...]
    post_detector_report_id: str
    post_detector_report_sha256: str
    post_detector_status: str
    schema_version: str = REPAIR_EXECUTION_REPORT_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "actual_repair_scope": list(self.actual_repair_scope),
            "administrative_reseal_scope": list(self.administrative_reseal_scope),
            "authorization_id": self.authorization_id,
            "authorization_sha256": self.authorization_sha256,
            "detector_report_id": self.detector_report_id,
            "detector_report_sha256": self.detector_report_sha256,
            "effective_repair_scope": list(self.effective_repair_scope),
            "error_code": self.error_code,
            "file_deltas": [item.to_dict() for item in self.file_deltas],
            "policy_allowed_scope": list(self.policy_allowed_scope),
            "post_detector_report_id": self.post_detector_report_id,
            "post_detector_report_sha256": self.post_detector_report_sha256,
            "post_detector_status": self.post_detector_status,
            "predicted_repair_scope": list(self.predicted_repair_scope),
            "repaired_bundle_id": self.repaired_bundle_id,
            "repaired_bundle_inventory_sha256": self.repaired_bundle_inventory_sha256,
            "repaired_bundle_record_sha256": self.repaired_bundle_record_sha256,
            "repaired_bundle_schema_version": self.repaired_bundle_schema_version,
            "schema_version": self.schema_version,
            "source_bundle_id": self.source_bundle_id,
            "source_bundle_inventory_sha256": self.source_bundle_inventory_sha256,
            "source_bundle_record_sha256": self.source_bundle_record_sha256,
            "source_bundle_schema_version": self.source_bundle_schema_version,
        }

    def validate(self) -> None:
        for field_name in (
            "execution_id", "source_bundle_id", "source_bundle_schema_version",
            "repaired_bundle_id", "repaired_bundle_schema_version", "detector_report_id",
            "authorization_id", "error_code", "post_detector_report_id", "post_detector_status",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.schema_version != REPAIR_EXECUTION_REPORT_SCHEMA_VERSION:
            raise RepairExecutionError("unsupported repair execution report schema")
        if (
            self.source_bundle_schema_version != BLINDED_FAULT_BUNDLE_SCHEMA_VERSION
            or self.repaired_bundle_schema_version != BLINDED_FAULT_BUNDLE_SCHEMA_VERSION
        ):
            raise RepairExecutionError("execution report binds an unsupported bundle schema")
        for field_name in (
            "source_bundle_record_sha256", "source_bundle_inventory_sha256",
            "repaired_bundle_record_sha256", "repaired_bundle_inventory_sha256",
            "detector_report_sha256", "authorization_sha256", "post_detector_report_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        if self.source_bundle_id == self.repaired_bundle_id:
            raise RepairExecutionError("successful repair must bind a distinct repaired bundle")
        if self.post_detector_status != NO_FAULT_DETECTED:
            raise RepairExecutionError("successful repair must have a no-fault post-detector status")
        for field_name in (
            "policy_allowed_scope", "predicted_repair_scope", "effective_repair_scope",
            "actual_repair_scope",
        ):
            _validate_artifact_scope(getattr(self, field_name), field_name)
        if self.error_code not in _MECHANICAL_POLICY_SCOPES:
            raise RepairExecutionError("execution report error code is not mechanically executable")
        expected_scope = _MECHANICAL_POLICY_SCOPES[self.error_code]
        if not self.actual_repair_scope:
            raise RepairExecutionError("successful repair must record an actual domain scope")
        if not (
            self.policy_allowed_scope == self.predicted_repair_scope
            == self.effective_repair_scope == self.actual_repair_scope
            == expected_scope
        ):
            raise RepairExecutionError("successful repair scopes must be exact and complete")
        if self.administrative_reseal_scope != (_BUNDLE_MANIFEST_FILE,):
            raise RepairExecutionError("administrative reseal scope must contain only the bundle manifest")
        if tuple(sorted(self.file_deltas, key=lambda item: item.path)) != self.file_deltas:
            raise RepairExecutionError("file deltas must use canonical path order")
        expected_delta_paths = set(self.actual_repair_scope) | set(self.administrative_reseal_scope)
        if len(self.file_deltas) != len(expected_delta_paths):
            raise RepairExecutionError("file deltas must exactly cover repair and reseal paths")
        if {item.path for item in self.file_deltas} != expected_delta_paths:
            raise RepairExecutionError("file deltas do not match repair and reseal scopes")
        for item in self.file_deltas:
            if not isinstance(item, RepairExecutionFileDelta):
                raise RepairExecutionError("file_deltas must contain RepairExecutionFileDelta values")
            item.validate()
        expected_id = "repair-execution-" + _canonical_sha256(self.to_payload())[:20]
        if self.execution_id != expected_id:
            raise RepairExecutionError("execution_id does not match canonical report payload")

    def validate_against(
        self,
        source_bundle_dir: Path,
        repaired_bundle_dir: Path,
        parity_report: BlindedBundleInventoryParityReport,
        pre_report: FaultDetectionReport,
        authorization: RepairAuthorization,
        post_report: FaultDetectionReport,
    ) -> None:
        """Prove this report is bound to real bundles, reports, and authorization."""
        if not isinstance(parity_report, BlindedBundleInventoryParityReport):
            raise RepairExecutionError("parity_report must be BlindedBundleInventoryParityReport")
        if not isinstance(pre_report, FaultDetectionReport):
            raise RepairExecutionError("pre_report must be FaultDetectionReport")
        if not isinstance(authorization, RepairAuthorization):
            raise RepairExecutionError("authorization must be RepairAuthorization")
        if not isinstance(post_report, FaultDetectionReport):
            raise RepairExecutionError("post_report must be FaultDetectionReport")
        self.validate()
        source_root = Path(source_bundle_dir).absolute()
        repaired_root = Path(repaired_bundle_dir).absolute()
        _require_disjoint_directory_paths(
            source_root,
            repaired_root,
            "source_bundle_dir and repaired_bundle_dir must be disjoint",
        )
        try:
            source_record = load_blinded_fault_bundle(source_root)
            repaired_record = load_blinded_fault_bundle(repaired_root)
            _validate_parity_binding(source_record, parity_report)
            _validate_parity_binding(repaired_record, parity_report)
            pre_report.validate()
            post_report.validate()
            fresh_pre_report = detect_blinded_fault_bundle(source_root, parity_report)
            fresh_post_report = detect_blinded_fault_bundle(repaired_root, parity_report)
            authorization.validate_against(pre_report)
        except (FaultBundleError, FaultDetectionError, RepairPolicyError, ValueError, TypeError) as error:
            raise RepairExecutionError("external execution-report binding validation failed") from error
        if fresh_pre_report.to_dict() != pre_report.to_dict():
            raise RepairExecutionError("pre_report does not exactly match fresh source detection")
        if fresh_post_report.to_dict() != post_report.to_dict():
            raise RepairExecutionError("post_report does not exactly match fresh repaired detection")
        if post_report.status != NO_FAULT_DETECTED or post_report.findings or post_report.diagnostics:
            raise RepairExecutionError("post_report is not a clean no-fault detector result")
        expected_scope = _require_authorized_mechanical_repair(pre_report, authorization)
        source_tree = _read_bundle_tree(source_root, source_record)
        repaired_tree = _read_bundle_tree(repaired_root, repaired_record)
        expected_deltas = _verified_bundle_deltas(
            source_tree,
            repaired_tree,
            expected_scope,
        )
        expected_bindings = {
            "source_bundle_id": source_record.bundle_id,
            "source_bundle_record_sha256": source_record.sha256(),
            "source_bundle_inventory_sha256": source_record.inventory_sha256,
            "source_bundle_schema_version": source_record.schema_version,
            "repaired_bundle_id": repaired_record.bundle_id,
            "repaired_bundle_record_sha256": repaired_record.sha256(),
            "repaired_bundle_inventory_sha256": repaired_record.inventory_sha256,
            "repaired_bundle_schema_version": repaired_record.schema_version,
            "detector_report_id": pre_report.report_id,
            "detector_report_sha256": pre_report.sha256(),
            "authorization_id": authorization.authorization_id,
            "authorization_sha256": authorization.sha256(),
            "error_code": pre_report.predicted_error_code,
            "policy_allowed_scope": authorization.policy_allowed_scope,
            "predicted_repair_scope": authorization.predicted_repair_scope,
            "effective_repair_scope": authorization.effective_repair_scope,
            "actual_repair_scope": expected_scope,
            "post_detector_report_id": post_report.report_id,
            "post_detector_report_sha256": post_report.sha256(),
            "post_detector_status": post_report.status,
        }
        for field_name, expected_value in expected_bindings.items():
            if getattr(self, field_name) != expected_value:
                raise RepairExecutionError(
                    "execution report " + field_name + " does not match external evidence"
                )
        if self.file_deltas != expected_deltas:
            raise RepairExecutionError("execution report file_deltas do not match real bundle deltas")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"execution_id": self.execution_id, **self.to_payload()}

    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())


class _ComponentIdParser(HTMLParser):
    """Capture start-tag offsets and data-component-id values without editing text."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=False)
        self._line_starts = _line_starts(html)
        self.component_ids: list[str] = []
        self.target_tags: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._capture(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._capture(attrs)

    def _capture(self, attrs: list[tuple[str, str | None]]) -> None:
        component_values = [
            value for name, value in attrs
            if name.lower() == "data-component-id" and isinstance(value, str)
        ]
        self.component_ids.extend(component_values)
        if component_values:
            line, column = self.getpos()
            if line < 1 or line > len(self._line_starts):
                raise RepairExecutionError("HTML parser returned an invalid start-tag location")
            raw = self.get_starttag_text()
            if raw is None:
                raise RepairExecutionError("HTML parser did not expose source start-tag text")
            self.target_tags.append((self._line_starts[line - 1] + column, raw))


def execute_authorized_deterministic_repair(
    bundle_dir: Path,
    parity_report: BlindedBundleInventoryParityReport,
    report: FaultDetectionReport,
    authorization: RepairAuthorization,
    output_dir: Path,
) -> RepairExecutionReport:
    """Perform exactly one pre-authorized local M2 repair into a fresh output tree."""
    if not isinstance(report, FaultDetectionReport):
        raise RepairExecutionError("report must be FaultDetectionReport")
    if not isinstance(authorization, RepairAuthorization):
        raise RepairExecutionError("authorization must be RepairAuthorization")
    if not isinstance(parity_report, BlindedBundleInventoryParityReport):
        raise RepairExecutionError("parity_report must be BlindedBundleInventoryParityReport")

    source_root = Path(bundle_dir).absolute()
    destination_candidate = Path(output_dir).absolute()
    _require_disjoint_directory_paths(
        source_root,
        destination_candidate,
        "source bundle and output_dir must be disjoint",
    )
    try:
        source_record = load_blinded_fault_bundle(source_root)
        _validate_parity_binding(source_record, parity_report)
    except (FaultBundleError, ValueError, TypeError) as error:
        raise RepairExecutionError("source bundle or parity validation failed") from error

    try:
        recomputed_report = detect_blinded_fault_bundle(source_root, parity_report)
        report.validate()
    except (FaultDetectionError, ValueError, TypeError) as error:
        raise RepairExecutionError("detector report validation failed") from error
    if recomputed_report.to_dict() != report.to_dict():
        raise RepairExecutionError("caller report does not exactly match fresh detector output")

    try:
        authorization.validate_against(report)
        authorization.validate_against(recomputed_report)
    except (RepairPolicyError, ValueError, TypeError) as error:
        raise RepairExecutionError("authorization does not bind the validated detector report") from error
    expected_scope = _require_authorized_mechanical_repair(report, authorization)
    destination = _validate_empty_output_destination(destination_candidate)
    if destination.exists():
        raise RepairExecutionError("executor output_dir must be a new path")
    source_files = _read_bundle_artifact_bytes(source_root, source_record)
    updates = _repair_artifact_updates(report, source_files)
    if tuple(sorted(updates)) != expected_scope:
        raise RepairExecutionError("mechanical repair updates do not exactly match authorized scope")

    staged_root = _prepare_staging_directory(destination)
    repaired_bundle_dir = staged_root / _REPAIRED_BUNDLE_DIRECTORY
    try:
            repaired_record = _copy_and_reseal_blinded_fault_bundle(
                source_root, repaired_bundle_dir, updates
            )
            post_report = detect_blinded_fault_bundle(repaired_bundle_dir, parity_report)
            if post_report.status != NO_FAULT_DETECTED or post_report.findings or post_report.diagnostics:
                raise RepairExecutionError("post-repair detector did not return no_fault_detected")
            source_tree = _read_bundle_tree(source_root, source_record)
            repaired_tree = _read_bundle_tree(repaired_bundle_dir, repaired_record)
            file_deltas = _verified_bundle_deltas(source_tree, repaired_tree, expected_scope)
            execution = _make_execution_report(
                source_record, repaired_record, report, authorization,
                expected_scope, file_deltas, post_report,
            )
            execution.validate_against(
                source_root,
                repaired_bundle_dir,
                parity_report,
                report,
                authorization,
                post_report,
            )
            write_repair_execution_report(
                execution, staged_root / _EXECUTION_REPORT_DIRECTORY
            )
            _commit_staged_output(staged_root, destination, source_root)
            try:
                execution.validate_against(
                    source_root,
                    destination / _REPAIRED_BUNDLE_DIRECTORY,
                    parity_report,
                    report,
                    authorization,
                    post_report,
                )
            except Exception:
                _remove_generated_destination(destination, source_root)
                raise
    except RepairExecutionError:
        raise
    except (FaultBundleError, FaultDetectionError, RepairPolicyError, ValueError, TypeError) as error:
        raise RepairExecutionError("authorized repair execution failed closed") from error
    finally:
        _remove_generated_staging_directory(staged_root)
    return execution


def write_repair_execution_report(
    report: RepairExecutionReport,
    output_dir: Path,
) -> RepairExecutionReport:
    """Write one canonical execution report and integrity manifest to an empty directory."""
    if not isinstance(report, RepairExecutionReport):
        raise RepairExecutionError("report must be RepairExecutionReport")
    report.validate()
    destination = _prepare_empty_output_directory(Path(output_dir))
    report_bytes = _canonical_json_bytes(report.to_dict())
    _write_exact_bytes(destination / _EXECUTION_REPORT_FILE, report_bytes)
    manifest = {
        "execution_id": report.execution_id,
        "execution_sha256": report.sha256(),
        "files": [{
            "path": _EXECUTION_REPORT_FILE,
            "sha256": sha256(report_bytes).hexdigest(),
            "size": len(report_bytes),
        }],
        "schema_version": REPAIR_EXECUTION_REPORT_MANIFEST_SCHEMA_VERSION,
    }
    manifest_bytes = _canonical_json_bytes(manifest)
    _write_exact_bytes(destination / _EXECUTION_REPORT_MANIFEST_FILE, manifest_bytes)
    actual = _read_regular_tree(destination)
    expected = {
        _EXECUTION_REPORT_FILE: report_bytes,
        _EXECUTION_REPORT_MANIFEST_FILE: manifest_bytes,
    }
    if actual != expected:
        raise RepairExecutionError("repair execution writer did not round-trip exact bytes")
    loaded_manifest = _load_json_object(actual[_EXECUTION_REPORT_MANIFEST_FILE], _EXECUTION_REPORT_MANIFEST_FILE)
    if loaded_manifest != manifest:
        raise RepairExecutionError("repair execution manifest did not round-trip")
    if sha256(actual[_EXECUTION_REPORT_FILE]).hexdigest() != manifest["files"][0]["sha256"]:
        raise RepairExecutionError("repair execution report hash did not round-trip")
    return report


def _require_authorized_mechanical_repair(
    report: FaultDetectionReport,
    authorization: RepairAuthorization,
) -> tuple[str, ...]:
    if report.status != FAULT_DETECTED or len(report.findings) != 1 or report.diagnostics:
        raise RepairExecutionError("only one classified detector finding without diagnostics is repairable")
    if report.predicted_error_code not in _MECHANICAL_POLICY_SCOPES:
        raise RepairExecutionError("detector error code is not mechanically executable")
    if authorization.status != DETERMINISTIC_REPAIR_AUTHORIZED:
        raise RepairExecutionError("authorization status does not permit deterministic repair")
    if authorization.action != "deterministic_repair":
        raise RepairExecutionError("authorization action does not permit deterministic repair")
    if authorization.error_code != report.predicted_error_code:
        raise RepairExecutionError("authorization error code does not match detector report")
    finding = report.findings[0]
    if finding.error_code != report.predicted_error_code:
        raise RepairExecutionError("detector finding does not match report error code")
    expected_scope = _MECHANICAL_POLICY_SCOPES[report.predicted_error_code]
    if not report.predicted_repairable:
        raise RepairExecutionError("detector report did not predict mechanical repairability")
    for field_name, value in (
        ("report.predicted_repair_scope", report.predicted_repair_scope),
        ("authorization.predicted_repair_scope", authorization.predicted_repair_scope),
        ("authorization.policy_allowed_scope", authorization.policy_allowed_scope),
        ("authorization.effective_repair_scope", authorization.effective_repair_scope),
    ):
        if value != expected_scope:
            raise RepairExecutionError(field_name + " does not exactly match frozen mechanical scope")
    return expected_scope


def _repair_artifact_updates(
    report: FaultDetectionReport,
    source_files: Mapping[str, bytes],
) -> dict[str, bytes]:
    code = report.predicted_error_code
    if code == DOM_COMPONENT_STABLE_ID_MISMATCH:
        return _repair_render_stable_id(report, source_files)
    if code == PACKAGE_MANIFEST_PATH_MISMATCH:
        return _repair_package_manifest_path(report, source_files)
    if code == PACKAGE_MANIFEST_SHA256_MISMATCH:
        return _repair_package_manifest_sha256(report, source_files)
    raise RepairExecutionError("error code has no authorized mechanical repair")


def _repair_render_stable_id(
    report: FaultDetectionReport,
    source_files: Mapping[str, bytes],
) -> dict[str, bytes]:
    html_path = "artifact/render/index.html"
    manifest_path = "artifact/render/render_manifest.json"
    expected = _single_detail_value(report.expected, "data_component_id")
    actual = _single_detail_value(report.actual, "data_component_id")
    if expected == actual:
        raise RepairExecutionError("render repair requires distinct expected and actual component IDs")
    original_html = _require_file_bytes(source_files, html_path)
    original_manifest = _require_file_bytes(source_files, manifest_path)
    mirror_manifest = _require_file_bytes(
        source_files, "artifact/result_package/page/render_manifest.json"
    )
    expected_component_ids = _page_spec_component_ids(
        _require_file_bytes(source_files, "artifact/page_spec.json")
    )
    html = _decode_utf8(original_html, html_path)
    repaired_html = _replace_exact_component_id_attribute(html, expected, actual)
    repaired_component_ids = _component_ids_from_html(repaired_html)
    if tuple(sorted(repaired_component_ids)) != tuple(sorted(expected_component_ids)):
        raise RepairExecutionError("render repair did not restore the exact PageSpec component-ID inventory")
    repaired_html_bytes = repaired_html.encode("utf-8")
    repaired_manifest = _update_render_manifest_index_hash(
        original_manifest, mirror_manifest, original_html, repaired_html_bytes
    )
    return {html_path: repaired_html_bytes, manifest_path: repaired_manifest}


def _repair_package_manifest_path(
    report: FaultDetectionReport,
    source_files: Mapping[str, bytes],
) -> dict[str, bytes]:
    manifest_path = "artifact/result_package/package_manifest.json"
    expected_path = _single_detail_value(report.expected, "manifest_path")
    actual_path = _single_detail_value(report.actual, "manifest_path")
    package_files = _package_artifact_files(source_files)
    if expected_path not in package_files or actual_path in package_files:
        raise RepairExecutionError("package path report does not identify one real and one substituted path")
    manifest = _load_package_manifest(_require_file_bytes(source_files, manifest_path))
    entries = manifest["files"]
    matching = [(index, entry) for index, entry in enumerate(entries) if entry["path"] == actual_path]
    if len(matching) != 1 or any(entry["path"] == expected_path for entry in entries):
        raise RepairExecutionError("package path repair target is not unique")
    index, entry = matching[0]
    expected_bytes = package_files[expected_path]
    if entry["size"] != len(expected_bytes) or entry["sha256"] != sha256(expected_bytes).hexdigest():
        raise RepairExecutionError("package path repair entry does not bind the expected package bytes")
    repaired = copy.deepcopy(manifest)
    repaired["files"][index]["path"] = expected_path
    _assert_single_entry_field_change(manifest, repaired, index, "path")
    return {manifest_path: _canonical_json_bytes(repaired)}


def _repair_package_manifest_sha256(
    report: FaultDetectionReport,
    source_files: Mapping[str, bytes],
) -> dict[str, bytes]:
    manifest_path = "artifact/result_package/package_manifest.json"
    target_path = _single_related_package_path(report, manifest_path)
    expected_sha256 = _single_detail_value(report.expected, "sha256")
    actual_sha256 = _single_detail_value(report.actual, "sha256")
    package_files = _package_artifact_files(source_files)
    if target_path not in package_files:
        raise RepairExecutionError("package hash report target is not a real package file")
    target_bytes = package_files[target_path]
    if sha256(target_bytes).hexdigest() != expected_sha256:
        raise RepairExecutionError("package hash report expected digest does not match real bytes")
    manifest = _load_package_manifest(_require_file_bytes(source_files, manifest_path))
    matching = [(index, entry) for index, entry in enumerate(manifest["files"]) if entry["path"] == target_path]
    if len(matching) != 1:
        raise RepairExecutionError("package hash repair target is not unique")
    index, entry = matching[0]
    if entry["sha256"] != actual_sha256 or entry["size"] != len(target_bytes):
        raise RepairExecutionError("package hash repair entry does not match the detector report")
    repaired = copy.deepcopy(manifest)
    repaired["files"][index]["sha256"] = expected_sha256
    _assert_single_entry_field_change(manifest, repaired, index, "sha256")
    return {manifest_path: _canonical_json_bytes(repaired)}


def _replace_exact_component_id_attribute(html: str, expected: str, actual: str) -> str:
    parser = _ComponentIdParser(html)
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, AssertionError) as error:
        raise RepairExecutionError("render HTML cannot be parsed for stable-ID repair") from error
    ids = parser.component_ids
    if len(ids) != len(set(ids)):
        raise RepairExecutionError("render HTML has duplicate data-component-id values")
    if ids.count(actual) != 1 or expected in ids:
        raise RepairExecutionError("render HTML does not contain exactly one unexpected ID and no expected ID")
    spans: list[tuple[int, int]] = []
    for start_offset, raw_tag in parser.target_tags:
        for match in _COMPONENT_ATTRIBUTE_PATTERN.finditer(raw_tag):
            if match.group("value") == actual:
                spans.append((start_offset + match.start("value"), start_offset + match.end("value")))
    if len(spans) != 1:
        raise RepairExecutionError("render HTML target attribute is not uniquely addressable")
    start, end = spans[0]
    if html[start:end] != actual:
        raise RepairExecutionError("render HTML target attribute does not match detector actual value")
    repaired = html[:start] + expected + html[end:]
    repaired_ids = _component_ids_from_html(repaired)
    if repaired_ids.count(expected) != 1 or actual in repaired_ids:
        raise RepairExecutionError("render HTML stable-ID replacement did not round-trip")
    return repaired


def _update_render_manifest_index_hash(
    content: bytes,
    packaged_mirror: bytes,
    original_html: bytes,
    repaired_html: bytes,
) -> bytes:
    manifest = _load_json_object(content, "render_manifest.json")
    expected_fields = {"files", "page_id", "page_spec_schema_version", "schema_version"}
    if set(manifest) != expected_fields or not isinstance(manifest.get("files"), list):
        raise RepairExecutionError("render manifest has an unsupported schema")
    entries = manifest["files"]
    index_entries = []
    seen_names: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {"name", "sha256"}:
            raise RepairExecutionError("render manifest file entry is invalid")
        name = entry.get("name")
        digest = entry.get("sha256")
        if not isinstance(name, str) or name in seen_names:
            raise RepairExecutionError("render manifest file names must be unique")
        _require_sha256(digest, "render manifest sha256")
        seen_names.add(name)
        if name == "index.html":
            index_entries.append((index, entry))
    if set(seen_names) != {"index.html", "styles.css", "app.js"} or len(index_entries) != 1:
        raise RepairExecutionError("render manifest index.html entry is not unique")
    index, entry = index_entries[0]
    if entry["sha256"] != sha256(original_html).hexdigest():
        raise RepairExecutionError("render manifest index.html hash does not bind source HTML")
    repaired = copy.deepcopy(manifest)
    repaired["files"][index]["sha256"] = sha256(repaired_html).hexdigest()
    _assert_single_entry_field_change(manifest, repaired, index, "sha256")
    mirror = _load_json_object(packaged_mirror, "package render_manifest mirror")
    if mirror != repaired:
        raise RepairExecutionError(
            "packaged render manifest mirror does not exactly match the mechanical repair"
        )
    return packaged_mirror


def _page_spec_component_ids(content: bytes) -> tuple[str, ...]:
    payload = _load_json_object(content, "page_spec.json")
    components = payload.get("components")
    if not isinstance(components, list) or not components:
        raise RepairExecutionError("PageSpec components are unavailable for render repair")
    values = []
    for component in components:
        if not isinstance(component, dict) or not isinstance(component.get("component_id"), str):
            raise RepairExecutionError("PageSpec component IDs are malformed")
        values.append(component["component_id"])
    if len(values) != len(set(values)):
        raise RepairExecutionError("PageSpec component IDs are not unique")
    return tuple(values)


def _component_ids_from_html(html: str) -> list[str]:
    parser = _ComponentIdParser(html)
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, AssertionError) as error:
        raise RepairExecutionError("render HTML cannot be parsed") from error
    return parser.component_ids


def _load_package_manifest(content: bytes) -> dict[str, Any]:
    manifest = _load_json_object(content, "package_manifest.json")
    expected_fields = {"schema_version", "package_id", "page_id", "entrypoint", "result_summary", "files"}
    if set(manifest) != expected_fields or not isinstance(manifest.get("files"), list):
        raise RepairExecutionError("package manifest has an unsupported schema")
    seen_paths: set[str] = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "role", "sha256", "size"}:
            raise RepairExecutionError("package manifest file entry is invalid")
        path = entry.get("path")
        if not _is_safe_relative_posix(path) or path in seen_paths:
            raise RepairExecutionError("package manifest paths must be unique safe relative paths")
        if not isinstance(entry.get("role"), str) or not entry["role"]:
            raise RepairExecutionError("package manifest entry role is invalid")
        if not isinstance(entry.get("size"), int) or entry["size"] < 0:
            raise RepairExecutionError("package manifest entry size is invalid")
        _require_sha256(entry.get("sha256"), "package manifest sha256")
        seen_paths.add(path)
    return manifest


def _package_artifact_files(source_files: Mapping[str, bytes]) -> dict[str, bytes]:
    prefix = "artifact/result_package/"
    manifest_path = prefix + "package_manifest.json"
    result = {
        path.removeprefix(prefix): content
        for path, content in source_files.items()
        if path.startswith(prefix) and path != manifest_path
    }
    if not result:
        raise RepairExecutionError("bundle does not expose package artifact bytes")
    return result


def _single_related_package_path(report: FaultDetectionReport, manifest_path: str) -> str:
    prefix = "artifact/result_package/"
    candidates = tuple(sorted(
        path.removeprefix(prefix)
        for path in report.related_paths
        if path.startswith(prefix) and path != manifest_path
    ))
    if len(candidates) != 1:
        raise RepairExecutionError("package report does not identify exactly one target file")
    return candidates[0]


def _assert_single_entry_field_change(before: dict[str, Any], after: dict[str, Any], entry_index: int, field_name: str) -> None:
    before_entries = before.get("files")
    after_entries = after.get("files")
    if not isinstance(before_entries, list) or not isinstance(after_entries, list):
        raise RepairExecutionError("manifest files are unavailable for locality validation")
    if len(before_entries) != len(after_entries):
        raise RepairExecutionError("manifest repair changed the entry count")
    for key in set(before) | set(after):
        if key != "files" and before.get(key) != after.get(key):
            raise RepairExecutionError("manifest repair changed a non-file field")
    for index, (before_entry, after_entry) in enumerate(zip(before_entries, after_entries)):
        if not isinstance(before_entry, dict) or not isinstance(after_entry, dict):
            raise RepairExecutionError("manifest repair encountered a non-object entry")
        if index == entry_index:
            expected = dict(before_entry)
            expected[field_name] = after_entry.get(field_name)
            if after_entry != expected or before_entry.get(field_name) == after_entry.get(field_name):
                raise RepairExecutionError("manifest repair changed fields beyond the authorized target")
        elif before_entry != after_entry:
            raise RepairExecutionError("manifest repair changed a non-target entry")


def _make_execution_report(
    source_record: BlindedFaultBundleRecord,
    repaired_record: BlindedFaultBundleRecord,
    report: FaultDetectionReport,
    authorization: RepairAuthorization,
    expected_scope: tuple[str, ...],
    file_deltas: tuple[RepairExecutionFileDelta, ...],
    post_report: FaultDetectionReport,
) -> RepairExecutionReport:
    payload = {
        "actual_repair_scope": list(expected_scope),
        "administrative_reseal_scope": [_BUNDLE_MANIFEST_FILE],
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization.sha256(),
        "detector_report_id": report.report_id,
        "detector_report_sha256": report.sha256(),
        "effective_repair_scope": list(authorization.effective_repair_scope),
        "error_code": report.predicted_error_code,
        "file_deltas": [item.to_dict() for item in file_deltas],
        "policy_allowed_scope": list(authorization.policy_allowed_scope),
        "post_detector_report_id": post_report.report_id,
        "post_detector_report_sha256": post_report.sha256(),
        "post_detector_status": post_report.status,
        "predicted_repair_scope": list(authorization.predicted_repair_scope),
        "repaired_bundle_id": repaired_record.bundle_id,
        "repaired_bundle_inventory_sha256": repaired_record.inventory_sha256,
        "repaired_bundle_record_sha256": repaired_record.sha256(),
        "repaired_bundle_schema_version": repaired_record.schema_version,
        "schema_version": REPAIR_EXECUTION_REPORT_SCHEMA_VERSION,
        "source_bundle_id": source_record.bundle_id,
        "source_bundle_inventory_sha256": source_record.inventory_sha256,
        "source_bundle_record_sha256": source_record.sha256(),
        "source_bundle_schema_version": source_record.schema_version,
    }
    execution = RepairExecutionReport(
        execution_id="repair-execution-" + _canonical_sha256(payload)[:20],
        source_bundle_id=source_record.bundle_id,
        source_bundle_record_sha256=source_record.sha256(),
        source_bundle_inventory_sha256=source_record.inventory_sha256,
        source_bundle_schema_version=source_record.schema_version,
        repaired_bundle_id=repaired_record.bundle_id,
        repaired_bundle_record_sha256=repaired_record.sha256(),
        repaired_bundle_inventory_sha256=repaired_record.inventory_sha256,
        repaired_bundle_schema_version=repaired_record.schema_version,
        detector_report_id=report.report_id,
        detector_report_sha256=report.sha256(),
        authorization_id=authorization.authorization_id,
        authorization_sha256=authorization.sha256(),
        error_code=report.predicted_error_code,
        policy_allowed_scope=authorization.policy_allowed_scope,
        predicted_repair_scope=authorization.predicted_repair_scope,
        effective_repair_scope=authorization.effective_repair_scope,
        actual_repair_scope=expected_scope,
        administrative_reseal_scope=(_BUNDLE_MANIFEST_FILE,),
        file_deltas=file_deltas,
        post_detector_report_id=post_report.report_id,
        post_detector_report_sha256=post_report.sha256(),
        post_detector_status=post_report.status,
    )
    execution.validate()
    return execution


def _verified_bundle_deltas(
    source_tree: Mapping[str, bytes], repaired_tree: Mapping[str, bytes], expected_scope: tuple[str, ...]
) -> tuple[RepairExecutionFileDelta, ...]:
    if set(source_tree) != set(repaired_tree):
        raise RepairExecutionError("repaired bundle changed the file inventory")
    changed_paths = tuple(sorted(path for path in source_tree if source_tree[path] != repaired_tree[path]))
    expected_paths = tuple(sorted({*expected_scope, _BUNDLE_MANIFEST_FILE}))
    if changed_paths != expected_paths:
        raise RepairExecutionError("repaired bundle changed paths outside authorized scope and reseal")
    deltas = tuple(
        RepairExecutionFileDelta(
            path=path,
            before_size=len(source_tree[path]),
            before_sha256=sha256(source_tree[path]).hexdigest(),
            after_size=len(repaired_tree[path]),
            after_sha256=sha256(repaired_tree[path]).hexdigest(),
        )
        for path in changed_paths
    )
    for item in deltas:
        item.validate()
    return deltas


def _read_bundle_artifact_bytes(bundle_dir: Path, record: BlindedFaultBundleRecord) -> dict[str, bytes]:
    root = Path(bundle_dir).absolute()
    if root.is_symlink() or not root.is_dir():
        raise RepairExecutionError("bundle_dir must be a real directory")
    result: dict[str, bytes] = {}
    for entry in record.files:
        path = root.joinpath(*PurePosixPath(entry.path).parts)
        if path.is_symlink() or not path.is_file():
            raise RepairExecutionError("bundle artifact path must be a real file")
        content = path.read_bytes()
        if len(content) != entry.size or sha256(content).hexdigest() != entry.sha256:
            raise RepairExecutionError("bundle artifact changed after validation")
        result[entry.path] = content
    return result


def _read_bundle_tree(bundle_dir: Path, record: BlindedFaultBundleRecord) -> dict[str, bytes]:
    artifacts = _read_bundle_artifact_bytes(bundle_dir, record)
    manifest_path = Path(bundle_dir).absolute() / _BUNDLE_MANIFEST_FILE
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RepairExecutionError("bundle manifest must be a real file")
    manifest = manifest_path.read_bytes()
    expected = _canonical_json_bytes(record.to_dict())
    if manifest != expected:
        raise RepairExecutionError("bundle manifest bytes do not match canonical record")
    return {_BUNDLE_MANIFEST_FILE: manifest, **artifacts}


def _validate_parity_binding(record: BlindedFaultBundleRecord, parity_report: BlindedBundleInventoryParityReport) -> None:
    parity_report.validate()
    paths = tuple(entry.path for entry in record.files)
    slots = tuple(entry.slot for entry in record.files)
    if paths != parity_report.paths:
        raise RepairExecutionError("bundle path inventory does not match parity report")
    if tuple(sorted(set(slots))) != parity_report.slots:
        raise RepairExecutionError("bundle slot inventory does not match parity report")
    for entry in record.files:
        if entry.slot != _slot_for_bundle_path(entry.path):
            raise RepairExecutionError("bundle path-to-slot mapping is not canonical")


def _require_disjoint_directory_paths(
    first: Path,
    second: Path,
    message: str,
) -> None:
    first_path = Path(first).absolute()
    second_path = Path(second).absolute()
    if (
        first_path == second_path
        or first_path in second_path.parents
        or second_path in first_path.parents
    ):
        raise RepairExecutionError(message)


def _slot_for_bundle_path(path: str) -> str:
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
    raise RepairExecutionError("bundle path is outside the canonical artifact inventory")


def _validate_empty_output_destination(output_dir: Path) -> Path:
    if str(output_dir) in {"", "."}:
        raise RepairExecutionError("output_dir must be a non-empty dedicated path")
    destination = output_dir.absolute()
    _reject_symlink_ancestors(destination)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise RepairExecutionError("output_dir must be a real directory path")
        if any(destination.iterdir()):
            raise RepairExecutionError("output_dir must be empty; refusing to overwrite")
    return destination


def _prepare_staging_directory(destination: Path) -> Path:
    parent = destination.parent
    _reject_symlink_ancestors(parent)
    if parent.exists():
        if parent.is_symlink() or not parent.is_dir():
            raise RepairExecutionError("output parent must be a real directory")
    else:
        parent.mkdir(parents=True, exist_ok=False)
    stage = parent / ("." + destination.name + ".repair-stage")
    _reject_symlink_ancestors(stage)
    if stage.exists():
        raise RepairExecutionError("repair staging directory already exists")
    stage.mkdir(parents=False, exist_ok=False)
    return stage


def _remove_generated_destination(destination: Path, source_root: Path) -> None:
    """Remove only this executor's new, validated, disjoint destination tree."""
    target = Path(destination).absolute()
    source = Path(source_root).absolute()
    _require_disjoint_directory_paths(
        source,
        target,
        "source bundle and generated destination must be disjoint",
    )
    if not target.exists():
        return
    if target.is_symlink() or not target.is_dir():
        raise RepairExecutionError("generated destination became unsafe")
    _reject_symlink_ancestors(target.parent)
    _read_regular_tree(target)
    shutil.rmtree(target)
    if target.exists():
        raise RepairExecutionError("generated destination cleanup did not complete")


def _remove_generated_staging_directory(stage: Path) -> None:
    if not stage.exists():
        return
    if stage.is_symlink() or not stage.is_dir():
        raise RepairExecutionError("repair staging directory became unsafe")
    shutil.rmtree(stage)


def _commit_staged_output(
    staged_root: Path,
    destination: Path,
    source_root: Path,
) -> None:
    staged = _read_regular_tree(staged_root)
    _reject_symlink_ancestors(destination)
    if destination.exists():
        raise RepairExecutionError("executor output_dir must not exist at commit time")
    staged_root.replace(destination)
    try:
        if _read_regular_tree(destination) != staged:
            raise RepairExecutionError("final repair output did not round-trip staged bytes")
    except Exception:
        _remove_generated_destination(destination, source_root)
        raise


def _prepare_empty_output_directory(output_dir: Path) -> Path:
    destination = _validate_empty_output_destination(output_dir)
    if not destination.exists():
        destination.mkdir(parents=True, exist_ok=False)
    return destination


def _read_regular_tree(root: Path) -> dict[str, bytes]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise RepairExecutionError("output root must be a real directory")
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RepairExecutionError("symlinked output paths are not allowed")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RepairExecutionError("output tree contains an unsupported path type")
        relative = path.relative_to(root).as_posix()
        _safe_relative_posix(relative, "output relative path")
        result[relative] = path.read_bytes()
    return result


def _write_exact_bytes(path: Path, content: bytes) -> None:
    _reject_symlink_ancestors(path.parent)
    if path.exists():
        raise RepairExecutionError("writer refuses to overwrite a file")
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise RepairExecutionError("writer bytes did not round-trip")


def _reject_symlink_ancestors(path: Path) -> None:
    for candidate in (path.absolute(), *path.absolute().parents):
        if candidate.exists() and candidate.is_symlink():
            raise RepairExecutionError("symlinked paths are not allowed")


def _line_starts(value: str) -> list[int]:
    starts = [0]
    for index, character in enumerate(value):
        if character == "\n":
            starts.append(index + 1)
    return starts


def _single_detail_value(details: tuple[tuple[str, str], ...], key: str) -> str:
    values = [value for name, value in details if name == key]
    if len(values) != 1 or len(details) != 1:
        raise RepairExecutionError("detector details do not contain one exact " + key + " value")
    _require_text(values[0], key)
    return values[0]


def _require_file_bytes(files: Mapping[str, bytes], path: str) -> bytes:
    value = files.get(path)
    if not isinstance(value, bytes):
        raise RepairExecutionError("bundle is missing required artifact " + path)
    return value


def _decode_utf8(content: bytes, name: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepairExecutionError(name + " must be valid UTF-8") from error


def _load_json_object(content: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RepairExecutionError(name + " must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise RepairExecutionError(name + " must be a JSON object")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _validate_bundle_delta_path(path: str) -> None:
    if path == _BUNDLE_MANIFEST_FILE:
        return
    if not path.startswith("artifact/"):
        raise RepairExecutionError("bundle delta path must be the manifest or an artifact path")
    _safe_relative_posix(path, "bundle delta path")


def _validate_artifact_scope(values: tuple[str, ...], field_name: str) -> None:
    if not isinstance(values, tuple) or values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise RepairExecutionError(field_name + " must use sorted unique paths")
    for path in values:
        _safe_relative_posix(path, field_name)
        if not path.startswith("artifact/"):
            raise RepairExecutionError(field_name + " must contain artifact paths only")


def _is_safe_relative_posix(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and not any(part in {"", ".", ".."} for part in path.parts)


def _safe_relative_posix(value: object, field_name: str) -> None:
    if not _is_safe_relative_posix(value):
        raise RepairExecutionError(field_name + " must be a safe POSIX relative path")


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise RepairExecutionError(field_name + " must be non-empty text")


def _require_sha256(value: object, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RepairExecutionError(field_name + " must be a lowercase SHA-256 hex digest")
