from __future__ import annotations

"""Frozen G0 fallback snapshots and byte-exact package delivery.

This module receives an already-validated ResultPackage v2. It never invokes a
builder, model provider, repair route, browser, or network service.
"""

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from req2web_generation.result_package_v2 import (
    RESULT_PACKAGE_V2_SCHEMA_VERSION,
    RetrievalEnhancedResultPackage,
)


FROZEN_G0_FALLBACK_RECORD_SCHEMA_VERSION = "req2web.frozen_g0_fallback_record.v1"
FROZEN_G0_FALLBACK_MANIFEST_SCHEMA_VERSION = "req2web.frozen_g0_fallback_manifest.v1"
FALLBACK_DELIVERY_REPORT_SCHEMA_VERSION = "req2web.fallback_delivery_report.v1"
FALLBACK_DELIVERY_MANIFEST_SCHEMA_VERSION = "req2web.fallback_delivery_manifest.v1"

_FROZEN_RECORD_FILE = "frozen_g0_fallback_record.json"
_FROZEN_MANIFEST_FILE = "frozen_g0_fallback_manifest.json"
_DELIVERY_REPORT_FILE = "fallback_delivery_report.json"
_DELIVERY_MANIFEST_FILE = "fallback_delivery_manifest.json"
_RESULT_PACKAGE_DIRECTORY = "result_package"
_PACKAGE_MANIFEST = "package_manifest.json"

_EXPECTED_PACKAGE_ROLES = {
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
    _PACKAGE_MANIFEST: "package_manifest",
}


class FallbackDeliveryError(ValueError):
    """Raised when a frozen fallback artifact is unsafe or unverifiable."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FallbackDeliveryError(field_name + " must be non-empty text")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise FallbackDeliveryError(field_name + " must be a lowercase SHA-256 hex digest")
    return value


def _safe_relative_posix(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise FallbackDeliveryError(field_name + " must be a safe POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise FallbackDeliveryError(field_name + " must not traverse or escape its root")
    return value


def _json_object(content: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FallbackDeliveryError(name + " must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise FallbackDeliveryError(name + " must be a JSON object")
    return value


@dataclass(frozen=True)
class FrozenPackageFile:
    path: str
    role: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "role": self.role, "size": self.size, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, value: object) -> "FrozenPackageFile":
        if not isinstance(value, dict) or set(value) != {"path", "role", "size", "sha256"}:
            raise FallbackDeliveryError("package inventory entry fields are invalid")
        return cls(path=value["path"], role=value["role"], size=value["size"], sha256=value["sha256"])

    def validate(self) -> None:
        path = _safe_relative_posix(self.path, "package inventory path")
        if path not in _EXPECTED_PACKAGE_ROLES or self.role != _EXPECTED_PACKAGE_ROLES[path]:
            raise FallbackDeliveryError("package inventory path or role is invalid")
        if not isinstance(self.size, int) or self.size < 0:
            raise FallbackDeliveryError("package inventory size is invalid")
        _require_sha256(self.sha256, "package inventory sha256")


@dataclass(frozen=True)
class FrozenG0Provenance:
    agent_context_bundle_id: str
    agent_context_bundle_sha256: str
    retrieval_guidance_bundle_id: str
    retrieval_guidance_bundle_sha256: str
    guided_page_spec_build_result_id: str
    guided_page_spec_build_result_sha256: str
    page_spec_id: str
    page_spec_sha256: str
    influence_report_id: str
    influence_report_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "agent_context_bundle_id": self.agent_context_bundle_id,
            "agent_context_bundle_sha256": self.agent_context_bundle_sha256,
            "retrieval_guidance_bundle_id": self.retrieval_guidance_bundle_id,
            "retrieval_guidance_bundle_sha256": self.retrieval_guidance_bundle_sha256,
            "guided_page_spec_build_result_id": self.guided_page_spec_build_result_id,
            "guided_page_spec_build_result_sha256": self.guided_page_spec_build_result_sha256,
            "page_spec_id": self.page_spec_id,
            "page_spec_sha256": self.page_spec_sha256,
            "influence_report_id": self.influence_report_id,
            "influence_report_sha256": self.influence_report_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FrozenG0Provenance":
        expected = {
            "agent_context_bundle_id", "agent_context_bundle_sha256",
            "retrieval_guidance_bundle_id", "retrieval_guidance_bundle_sha256",
            "guided_page_spec_build_result_id", "guided_page_spec_build_result_sha256",
            "page_spec_id", "page_spec_sha256", "influence_report_id", "influence_report_sha256",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise FallbackDeliveryError("frozen provenance fields are invalid")
        return cls(**value)

    def validate(self) -> None:
        for field_name in (
            "agent_context_bundle_id", "retrieval_guidance_bundle_id",
            "guided_page_spec_build_result_id", "page_spec_id", "influence_report_id",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "agent_context_bundle_sha256", "retrieval_guidance_bundle_sha256",
            "guided_page_spec_build_result_sha256", "page_spec_sha256", "influence_report_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)


@dataclass(frozen=True)
class FrozenG0FallbackRecord:
    record_id: str
    record_sha256: str
    case_id: str
    package_id: str
    page_id: str
    package_schema_version: str
    package_manifest_sha256: str
    package_tree_sha256: str
    package_inventory: tuple[FrozenPackageFile, ...]
    provenance: FrozenG0Provenance
    schema_version: str = FROZEN_G0_FALLBACK_RECORD_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "package_id": self.package_id,
            "page_id": self.page_id,
            "package_schema_version": self.package_schema_version,
            "package_manifest_sha256": self.package_manifest_sha256,
            "package_tree_sha256": self.package_tree_sha256,
            "package_inventory": [entry.to_dict() for entry in self.package_inventory],
            "provenance": self.provenance.to_dict(),
        }

    def to_dict(self) -> dict[str, object]:
        result = self.to_payload()
        result.update({"record_id": self.record_id, "record_sha256": self.record_sha256})
        return result

    @classmethod
    def from_dict(cls, value: object) -> "FrozenG0FallbackRecord":
        expected = {
            "schema_version", "record_id", "record_sha256", "case_id", "package_id", "page_id",
            "package_schema_version", "package_manifest_sha256", "package_tree_sha256",
            "package_inventory", "provenance",
        }
        if not isinstance(value, dict) or set(value) != expected or not isinstance(value["package_inventory"], list):
            raise FallbackDeliveryError("frozen fallback record fields are invalid")
        return cls(
            record_id=value["record_id"], record_sha256=value["record_sha256"], case_id=value["case_id"],
            package_id=value["package_id"], page_id=value["page_id"],
            package_schema_version=value["package_schema_version"],
            package_manifest_sha256=value["package_manifest_sha256"],
            package_tree_sha256=value["package_tree_sha256"],
            package_inventory=tuple(FrozenPackageFile.from_dict(item) for item in value["package_inventory"]),
            provenance=FrozenG0Provenance.from_dict(value["provenance"]), schema_version=value["schema_version"],
        )

    def validate(self) -> None:
        if self.schema_version != FROZEN_G0_FALLBACK_RECORD_SCHEMA_VERSION:
            raise FallbackDeliveryError("unsupported frozen fallback record schema")
        for field_name in ("record_id", "case_id", "package_id", "page_id"):
            _require_text(getattr(self, field_name), field_name)
        if self.package_schema_version != RESULT_PACKAGE_V2_SCHEMA_VERSION:
            raise FallbackDeliveryError("frozen fallback record must bind ResultPackage v2")
        for field_name in ("record_sha256", "package_manifest_sha256", "package_tree_sha256"):
            _require_sha256(getattr(self, field_name), field_name)
        _validate_fixed_inventory(self.package_inventory, "package_inventory")
        self.provenance.validate()
        expected = _canonical_sha256(self.to_payload())
        if self.record_id != "frozen-g0-fallback-" + expected[:20]:
            raise FallbackDeliveryError("frozen fallback record_id is not canonical")
        if self.record_sha256 != expected:
            raise FallbackDeliveryError("frozen fallback record_sha256 is not canonical")

    def validate_against(self, snapshot_dir: Path) -> None:
        self.validate()
        root = _require_real_directory(snapshot_dir, "snapshot_dir")
        _assert_exact_outer_root(
            root,
            (_FROZEN_RECORD_FILE, _FROZEN_MANIFEST_FILE, *_package_root_paths(self.package_inventory)),
            "snapshot root",
        )
        record_bytes = _assert_exact_json(root / _FROZEN_RECORD_FILE, self.to_dict(), _FROZEN_RECORD_FILE)
        manifest = _freeze_manifest(self)
        _assert_exact_json(root / _FROZEN_MANIFEST_FILE, manifest, _FROZEN_MANIFEST_FILE)
        _assert_manifest_file_binding(manifest, "record", record_bytes, _FROZEN_RECORD_FILE, _FROZEN_MANIFEST_FILE)
        _assert_record_matches_package(self, _inspect_package_dir(root / _RESULT_PACKAGE_DIRECTORY))


@dataclass(frozen=True)
class FallbackDeliveryReport:
    report_id: str
    report_sha256: str
    case_id: str
    package_id: str
    page_id: str
    package_schema_version: str
    frozen_record_id: str
    frozen_record_sha256: str
    snapshot_package_tree_sha256: str
    delivered_package_tree_sha256: str
    byte_identity: bool
    snapshot_inventory: tuple[FrozenPackageFile, ...]
    delivered_inventory: tuple[FrozenPackageFile, ...]
    status: str = "fallback_delivery"
    delivery_source: str = "g0_frozen_fallback"
    schema_version: str = FALLBACK_DELIVERY_REPORT_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "status": self.status,
            "delivery_source": self.delivery_source, "case_id": self.case_id,
            "package_id": self.package_id, "page_id": self.page_id,
            "package_schema_version": self.package_schema_version,
            "frozen_record_id": self.frozen_record_id, "frozen_record_sha256": self.frozen_record_sha256,
            "snapshot_package_tree_sha256": self.snapshot_package_tree_sha256,
            "delivered_package_tree_sha256": self.delivered_package_tree_sha256,
            "byte_identity": self.byte_identity,
            "snapshot_inventory": [entry.to_dict() for entry in self.snapshot_inventory],
            "delivered_inventory": [entry.to_dict() for entry in self.delivered_inventory],
        }

    def to_dict(self) -> dict[str, object]:
        result = self.to_payload()
        result.update({"report_id": self.report_id, "report_sha256": self.report_sha256})
        return result

    @classmethod
    def from_dict(cls, value: object) -> "FallbackDeliveryReport":
        expected = {
            "schema_version", "report_id", "report_sha256", "status", "delivery_source", "case_id",
            "package_id", "page_id", "package_schema_version", "frozen_record_id",
            "frozen_record_sha256", "snapshot_package_tree_sha256", "delivered_package_tree_sha256",
            "byte_identity", "snapshot_inventory", "delivered_inventory",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise FallbackDeliveryError("fallback delivery report fields are invalid")
        if not isinstance(value["snapshot_inventory"], list) or not isinstance(value["delivered_inventory"], list):
            raise FallbackDeliveryError("fallback delivery inventories must be lists")
        return cls(
            report_id=value["report_id"], report_sha256=value["report_sha256"], case_id=value["case_id"],
            package_id=value["package_id"], page_id=value["page_id"], package_schema_version=value["package_schema_version"],
            frozen_record_id=value["frozen_record_id"], frozen_record_sha256=value["frozen_record_sha256"],
            snapshot_package_tree_sha256=value["snapshot_package_tree_sha256"],
            delivered_package_tree_sha256=value["delivered_package_tree_sha256"], byte_identity=value["byte_identity"],
            snapshot_inventory=tuple(FrozenPackageFile.from_dict(item) for item in value["snapshot_inventory"]),
            delivered_inventory=tuple(FrozenPackageFile.from_dict(item) for item in value["delivered_inventory"]),
            status=value["status"], delivery_source=value["delivery_source"], schema_version=value["schema_version"],
        )

    def validate(self) -> None:
        if self.schema_version != FALLBACK_DELIVERY_REPORT_SCHEMA_VERSION:
            raise FallbackDeliveryError("unsupported fallback delivery report schema")
        if self.status != "fallback_delivery" or self.delivery_source != "g0_frozen_fallback":
            raise FallbackDeliveryError("fallback delivery status or source is invalid")
        for field_name in ("report_id", "case_id", "package_id", "page_id", "frozen_record_id"):
            _require_text(getattr(self, field_name), field_name)
        if self.package_schema_version != RESULT_PACKAGE_V2_SCHEMA_VERSION:
            raise FallbackDeliveryError("fallback delivery must bind ResultPackage v2")
        for field_name in (
            "report_sha256", "frozen_record_sha256", "snapshot_package_tree_sha256", "delivered_package_tree_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        if self.byte_identity is not True:
            raise FallbackDeliveryError("fallback delivery must explicitly assert byte identity")
        _validate_fixed_inventory(self.snapshot_inventory, "snapshot_inventory")
        _validate_fixed_inventory(self.delivered_inventory, "delivered_inventory")
        if self.snapshot_inventory != self.delivered_inventory:
            raise FallbackDeliveryError("byte-identical fallback delivery must have identical inventories")
        if self.snapshot_package_tree_sha256 != self.delivered_package_tree_sha256:
            raise FallbackDeliveryError("byte-identical fallback delivery must have identical tree hashes")
        expected = _canonical_sha256(self.to_payload())
        if self.report_id != "fallback-delivery-" + expected[:20]:
            raise FallbackDeliveryError("fallback delivery report_id is not canonical")
        if self.report_sha256 != expected:
            raise FallbackDeliveryError("fallback delivery report_sha256 is not canonical")

    def validate_against(self, snapshot_dir: Path, delivered_package_dir: Path, expected_record: FrozenG0FallbackRecord) -> None:
        self.validate()
        if not isinstance(expected_record, FrozenG0FallbackRecord):
            raise FallbackDeliveryError("expected_record must be a FrozenG0FallbackRecord")
        expected_record.validate_against(snapshot_dir)
        output_root = _require_real_directory(Path(delivered_package_dir).parent, "delivery output root")
        _assert_exact_outer_root(
            output_root,
            (_DELIVERY_REPORT_FILE, _DELIVERY_MANIFEST_FILE, *_package_root_paths(self.delivered_inventory)),
            "delivery root",
        )
        report_bytes = _assert_exact_json(output_root / _DELIVERY_REPORT_FILE, self.to_dict(), _DELIVERY_REPORT_FILE)
        manifest = _delivery_manifest(self)
        _assert_exact_json(output_root / _DELIVERY_MANIFEST_FILE, manifest, _DELIVERY_MANIFEST_FILE)
        _assert_manifest_file_binding(manifest, "report", report_bytes, _DELIVERY_REPORT_FILE, _DELIVERY_MANIFEST_FILE)
        snapshot = _inspect_package_dir(Path(snapshot_dir) / _RESULT_PACKAGE_DIRECTORY)
        delivered = _inspect_package_dir(delivered_package_dir)
        if snapshot.files != delivered.files:
            raise FallbackDeliveryError("snapshot and delivered package bytes differ")
        if self.case_id != expected_record.case_id:
            raise FallbackDeliveryError("delivery report case_id does not match frozen record")
        if (self.package_id, self.page_id, self.package_schema_version) != (
            expected_record.package_id, expected_record.page_id, expected_record.package_schema_version,
        ):
            raise FallbackDeliveryError("delivery report package identity does not match frozen record")
        if (self.frozen_record_id, self.frozen_record_sha256) != (expected_record.record_id, expected_record.record_sha256):
            raise FallbackDeliveryError("delivery report frozen record binding is incorrect")
        if (self.snapshot_package_tree_sha256, self.delivered_package_tree_sha256) != (snapshot.tree_sha256, delivered.tree_sha256):
            raise FallbackDeliveryError("delivery report package-tree binding is incorrect")
        if (self.snapshot_inventory, self.delivered_inventory) != (snapshot.inventory, delivered.inventory):
            raise FallbackDeliveryError("delivery report inventory does not match package bytes")
        _assert_record_matches_package(expected_record, snapshot)
        _assert_record_matches_package(expected_record, delivered)


@dataclass(frozen=True)
class _InspectedPackage:
    package_id: str
    page_id: str
    package_schema_version: str
    manifest_sha256: str
    tree_sha256: str
    inventory: tuple[FrozenPackageFile, ...]
    provenance: FrozenG0Provenance
    files: dict[str, bytes]


def freeze_g0_fallback_package(case_id: str, package: RetrievalEnhancedResultPackage, output_dir: Path) -> FrozenG0FallbackRecord:
    """Freeze one validated G0 ResultPackage v2 as an immutable byte snapshot."""
    _require_text(case_id, "case_id")
    if not isinstance(package, RetrievalEnhancedResultPackage):
        raise FallbackDeliveryError("package must be a real RetrievalEnhancedResultPackage v2")
    inspected = _inspect_package(package)
    destination = _validate_new_destination(output_dir, package.package_dir, "snapshot output_dir")
    stage: Path | None = None
    try:
        record = _make_frozen_record(case_id, inspected)
        stage = _prepare_staging_directory(destination)
        _write_file_map(stage / _RESULT_PACKAGE_DIRECTORY, inspected.files)
        _write_exact_bytes(stage / _FROZEN_RECORD_FILE, _canonical_json_bytes(record.to_dict()))
        _write_exact_bytes(stage / _FROZEN_MANIFEST_FILE, _canonical_json_bytes(_freeze_manifest(record)))
        record.validate_against(stage)
        _commit_staged_output(stage, destination)
        stage = None
        try:
            record.validate_against(destination)
        except Exception:
            _remove_generated_destination(destination, package.package_dir)
            raise
        return record
    finally:
        if stage is not None and (stage.exists() or stage.is_symlink()):
            _remove_generated_staging_directory(stage)


def deliver_frozen_g0_fallback(snapshot_dir: Path, expected_record: FrozenG0FallbackRecord, output_dir: Path) -> FallbackDeliveryReport:
    """Copy frozen package bytes to a new fallback delivery directory exactly."""
    if not isinstance(expected_record, FrozenG0FallbackRecord):
        raise FallbackDeliveryError("expected_record must be a FrozenG0FallbackRecord")
    expected_record.validate_against(snapshot_dir)
    snapshot = _inspect_package_dir(Path(snapshot_dir) / _RESULT_PACKAGE_DIRECTORY)
    destination = _validate_new_destination(output_dir, Path(snapshot_dir), "delivery output_dir")
    stage: Path | None = None
    try:
        stage = _prepare_staging_directory(destination)
        staged_package_dir = stage / _RESULT_PACKAGE_DIRECTORY
        _write_file_map(staged_package_dir, snapshot.files)
        delivered = _inspect_package_dir(staged_package_dir)
        if snapshot.files != delivered.files:
            raise FallbackDeliveryError("staged fallback package bytes are not identical to the snapshot")
        report = _make_delivery_report(expected_record, snapshot, delivered)
        _write_exact_bytes(stage / _DELIVERY_REPORT_FILE, _canonical_json_bytes(report.to_dict()))
        _write_exact_bytes(stage / _DELIVERY_MANIFEST_FILE, _canonical_json_bytes(_delivery_manifest(report)))
        report.validate_against(snapshot_dir, staged_package_dir, expected_record)
        _commit_staged_output(stage, destination)
        stage = None
        try:
            report.validate_against(snapshot_dir, destination / _RESULT_PACKAGE_DIRECTORY, expected_record)
        except Exception:
            _remove_generated_destination(destination, Path(snapshot_dir))
            raise
        return report
    finally:
        if stage is not None and (stage.exists() or stage.is_symlink()):
            _remove_generated_staging_directory(stage)


def _make_frozen_record(case_id: str, package: _InspectedPackage) -> FrozenG0FallbackRecord:
    seed = FrozenG0FallbackRecord(
        record_id="", record_sha256="0" * 64, case_id=case_id, package_id=package.package_id,
        page_id=package.page_id, package_schema_version=package.package_schema_version,
        package_manifest_sha256=package.manifest_sha256, package_tree_sha256=package.tree_sha256,
        package_inventory=package.inventory, provenance=package.provenance,
    )
    digest = _canonical_sha256(seed.to_payload())
    return FrozenG0FallbackRecord(
        record_id="frozen-g0-fallback-" + digest[:20], record_sha256=digest, case_id=case_id,
        package_id=package.package_id, page_id=package.page_id, package_schema_version=package.package_schema_version,
        package_manifest_sha256=package.manifest_sha256, package_tree_sha256=package.tree_sha256,
        package_inventory=package.inventory, provenance=package.provenance,
    )


def _make_delivery_report(record: FrozenG0FallbackRecord, snapshot: _InspectedPackage, delivered: _InspectedPackage) -> FallbackDeliveryReport:
    seed = FallbackDeliveryReport(
        report_id="", report_sha256="0" * 64, case_id=record.case_id, package_id=record.package_id,
        page_id=record.page_id, package_schema_version=record.package_schema_version,
        frozen_record_id=record.record_id, frozen_record_sha256=record.record_sha256,
        snapshot_package_tree_sha256=snapshot.tree_sha256, delivered_package_tree_sha256=delivered.tree_sha256,
        byte_identity=True, snapshot_inventory=snapshot.inventory, delivered_inventory=delivered.inventory,
    )
    digest = _canonical_sha256(seed.to_payload())
    return FallbackDeliveryReport(
        report_id="fallback-delivery-" + digest[:20], report_sha256=digest, case_id=record.case_id,
        package_id=record.package_id, page_id=record.page_id, package_schema_version=record.package_schema_version,
        frozen_record_id=record.record_id, frozen_record_sha256=record.record_sha256,
        snapshot_package_tree_sha256=snapshot.tree_sha256, delivered_package_tree_sha256=delivered.tree_sha256,
        byte_identity=True, snapshot_inventory=snapshot.inventory, delivered_inventory=delivered.inventory,
    )


def _inspect_package(package: RetrievalEnhancedResultPackage) -> _InspectedPackage:
    if package.schema_version != RESULT_PACKAGE_V2_SCHEMA_VERSION:
        raise FallbackDeliveryError("package must use ResultPackage v2")
    try:
        package.validate()
    except Exception as exc:
        raise FallbackDeliveryError("package must pass the ResultPackage v2 validator") from exc
    inspected = _inspect_package_dir(package.package_dir)
    if (inspected.package_id, inspected.page_id) != (package.package_id, package.page_id):
        raise FallbackDeliveryError("package object identity does not match on-disk ResultPackage v2")
    return inspected


def _inspect_package_dir(package_dir: Path) -> _InspectedPackage:
    root = _require_real_directory(package_dir, "result package directory")
    manifest_path = root / _PACKAGE_MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FallbackDeliveryError("ResultPackage v2 manifest is missing or unsafe")
    manifest = _json_object(manifest_path.read_bytes(), _PACKAGE_MANIFEST)
    if manifest.get("schema_version") != RESULT_PACKAGE_V2_SCHEMA_VERSION:
        raise FallbackDeliveryError("snapshot must contain ResultPackage v2")
    package_id, page_id = manifest.get("package_id"), manifest.get("page_id")
    entrypoint, result_summary = manifest.get("entrypoint"), manifest.get("result_summary")
    for value, name in ((package_id, "package_id"), (page_id, "page_id"), (entrypoint, "entrypoint"), (result_summary, "result_summary")):
        _require_text(value, name)
    package = RetrievalEnhancedResultPackage(
        package_id=package_id, page_id=page_id, package_dir=root, entrypoint=entrypoint,
        result_summary=result_summary, package_manifest=_PACKAGE_MANIFEST,
        schema_version=RESULT_PACKAGE_V2_SCHEMA_VERSION,
    )
    try:
        package.validate()
    except Exception as exc:
        raise FallbackDeliveryError("snapshot package fails the ResultPackage v2 validator") from exc
    files = _read_regular_tree(root)
    if set(files) != set(_EXPECTED_PACKAGE_ROLES):
        raise FallbackDeliveryError("ResultPackage v2 file tree is not fixed and complete")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise FallbackDeliveryError("ResultPackage v2 manifest file entries are invalid")
    roles: dict[str, str] = {_PACKAGE_MANIFEST: _EXPECTED_PACKAGE_ROLES[_PACKAGE_MANIFEST]}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("role"), str):
            raise FallbackDeliveryError("ResultPackage v2 manifest entry identity is invalid")
        roles[entry["path"]] = entry["role"]
    inventory = tuple(
        FrozenPackageFile(path=path, role=roles.get(path, ""), size=len(content), sha256=_sha256(content))
        for path, content in sorted(files.items())
    )
    _validate_fixed_inventory(inventory, "ResultPackage v2 inventory")
    provenance = _provenance_from_package_files(files, page_id)
    return _InspectedPackage(
        package_id=package_id, page_id=page_id, package_schema_version=RESULT_PACKAGE_V2_SCHEMA_VERSION,
        manifest_sha256=_sha256(files[_PACKAGE_MANIFEST]), tree_sha256=_inventory_tree_sha256(inventory),
        inventory=inventory, provenance=provenance, files=files,
    )



def _provenance_from_package_files(files: Mapping[str, bytes], page_id: str) -> FrozenG0Provenance:
    context = _json_object(files["internal/agent_context.json"], "agent_context.json")
    guidance = _json_object(files["internal/retrieval_guidance.json"], "retrieval_guidance.json")
    guided = _json_object(files["internal/guided_page_spec_build_result.json"], "guided_page_spec_build_result.json")
    page_spec = _json_object(files["internal/page_spec.json"], "page_spec.json")
    influence = _json_object(files["internal/retrieval_influence_report.json"], "retrieval_influence_report.json")
    if not context or page_spec.get("page_id") != page_id:
        raise FallbackDeliveryError("PageSpec or AgentContext provenance is inconsistent")
    context_sha256 = _sha256(files["internal/agent_context.json"])
    result = FrozenG0Provenance(
        # AgentContext v1 has no native bundle ID, so the ID is byte-derived and explicit.
        agent_context_bundle_id="agent-context-" + context_sha256[:20],
        agent_context_bundle_sha256=context_sha256,
        retrieval_guidance_bundle_id=_require_text(guidance.get("guidance_bundle_id"), "guidance_bundle_id"),
        retrieval_guidance_bundle_sha256=_sha256(files["internal/retrieval_guidance.json"]),
        guided_page_spec_build_result_id=_require_text(guided.get("build_result_id"), "build_result_id"),
        guided_page_spec_build_result_sha256=_sha256(files["internal/guided_page_spec_build_result.json"]),
        page_spec_id=_require_text(page_spec.get("page_id"), "page_spec_id"),
        page_spec_sha256=_sha256(files["internal/page_spec.json"]),
        influence_report_id=_require_text(influence.get("report_id"), "influence_report_id"),
        influence_report_sha256=_sha256(files["internal/retrieval_influence_report.json"]),
    )
    result.validate()
    return result


def _assert_record_matches_package(record: FrozenG0FallbackRecord, package: _InspectedPackage) -> None:
    bindings = (
        (record.package_id, package.package_id, "package_id"),
        (record.page_id, package.page_id, "page_id"),
        (record.package_schema_version, package.package_schema_version, "package_schema_version"),
        (record.package_manifest_sha256, package.manifest_sha256, "package_manifest_sha256"),
        (record.package_tree_sha256, package.tree_sha256, "package_tree_sha256"),
        (record.package_inventory, package.inventory, "package_inventory"),
        (record.provenance, package.provenance, "provenance"),
    )
    for expected, actual, field_name in bindings:
        if expected != actual:
            raise FallbackDeliveryError("frozen fallback record " + field_name + " does not match package bytes")


def _validate_fixed_inventory(inventory: tuple[FrozenPackageFile, ...], field_name: str) -> None:
    if not isinstance(inventory, tuple) or tuple(entry.path for entry in inventory) != tuple(sorted(_EXPECTED_PACKAGE_ROLES)):
        raise FallbackDeliveryError(field_name + " must use the fixed ResultPackage v2 inventory")
    for entry in inventory:
        entry.validate()


def _inventory_tree_sha256(inventory: tuple[FrozenPackageFile, ...]) -> str:
    return _canonical_sha256({"files": [entry.to_dict() for entry in inventory]})


def _freeze_manifest(record: FrozenG0FallbackRecord) -> dict[str, object]:
    record_bytes = _canonical_json_bytes(record.to_dict())
    return {
        "schema_version": FROZEN_G0_FALLBACK_MANIFEST_SCHEMA_VERSION,
        "record": {"path": _FROZEN_RECORD_FILE, "size": len(record_bytes), "sha256": _sha256(record_bytes)},
        "snapshot_package": {
            "directory": _RESULT_PACKAGE_DIRECTORY,
            "tree_sha256": record.package_tree_sha256,
            "inventory": [entry.to_dict() for entry in record.package_inventory],
        },
    }


def _delivery_manifest(report: FallbackDeliveryReport) -> dict[str, object]:
    report_bytes = _canonical_json_bytes(report.to_dict())
    return {
        "schema_version": FALLBACK_DELIVERY_MANIFEST_SCHEMA_VERSION,
        "report": {"path": _DELIVERY_REPORT_FILE, "size": len(report_bytes), "sha256": _sha256(report_bytes)},
        "delivered_package": {
            "directory": _RESULT_PACKAGE_DIRECTORY,
            "tree_sha256": report.delivered_package_tree_sha256,
            "inventory": [entry.to_dict() for entry in report.delivered_inventory],
        },
    }


def _package_root_paths(inventory: tuple[FrozenPackageFile, ...]) -> tuple[str, ...]:
    return tuple(_RESULT_PACKAGE_DIRECTORY + "/" + entry.path for entry in inventory)


def _assert_exact_outer_root(root: Path, expected_files: tuple[str, ...], field_name: str) -> None:
    root = _require_real_directory(root, field_name)
    actual_files: set[str] = set()
    actual_dirs: set[str] = set()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise FallbackDeliveryError(field_name + " must not contain symlinks")
        relative = path.relative_to(root).as_posix()
        _safe_relative_posix(relative, field_name + " path")
        if path.is_dir():
            actual_dirs.add(relative)
        elif path.is_file():
            actual_files.add(relative)
        else:
            raise FallbackDeliveryError(field_name + " contains an unsupported path type")
    expected_file_set = set(expected_files)
    if len(expected_file_set) != len(expected_files) or actual_files != expected_file_set:
        raise FallbackDeliveryError(field_name + " file inventory is not exact")
    expected_dirs: set[str] = set()
    for relative in expected_files:
        parts = PurePosixPath(relative).parts[:-1]
        expected_dirs.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    if actual_dirs != expected_dirs:
        raise FallbackDeliveryError(field_name + " directory inventory is not exact")


def _read_exact_json(path: Path, name: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise FallbackDeliveryError(name + " is missing or unsafe")
    return _json_object(path.read_bytes(), name)


def _assert_exact_json(path: Path, expected: dict[str, object], name: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise FallbackDeliveryError(name + " is missing or unsafe")
    actual = path.read_bytes()
    expected_bytes = _canonical_json_bytes(expected)
    if actual != expected_bytes:
        raise FallbackDeliveryError(name + " bytes are not canonical")
    if _json_object(actual, name) != expected:
        raise FallbackDeliveryError(name + " does not match the canonical artifact")
    return actual


def _assert_manifest_file_binding(
    manifest: Mapping[str, object],
    entry_name: str,
    actual_bytes: bytes,
    expected_path: str,
    manifest_name: str,
) -> None:
    entry = manifest.get(entry_name)
    if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
        raise FallbackDeliveryError(manifest_name + " " + entry_name + " entry is invalid")
    if entry.get("path") != expected_path or entry.get("size") != len(actual_bytes) or entry.get("sha256") != _sha256(actual_bytes):
        raise FallbackDeliveryError(manifest_name + " does not bind exact " + entry_name + " bytes")


def _require_real_directory(path: Path, field_name: str) -> Path:
    root = Path(path).absolute()
    _reject_symlink_ancestors(root)
    if root.is_symlink() or not root.is_dir():
        raise FallbackDeliveryError(field_name + " must be a real directory")
    return root


def _read_regular_tree(root: Path) -> dict[str, bytes]:
    root = _require_real_directory(root, "package tree")
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise FallbackDeliveryError("package tree must not contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise FallbackDeliveryError("package tree contains an unsupported path type")
        relative = path.relative_to(root).as_posix()
        _safe_relative_posix(relative, "package tree path")
        if relative in files:
            raise FallbackDeliveryError("package tree contains duplicate paths")
        files[relative] = path.read_bytes()
    return dict(sorted(files.items()))


def _validate_new_destination(output_dir: Path, source_root: Path, field_name: str) -> Path:
    raw_destination = Path(output_dir)
    if ".." in raw_destination.parts:
        raise FallbackDeliveryError(field_name + " must not traverse its parent path")
    destination = raw_destination.absolute()
    _reject_symlink_ancestors(destination)
    if _paths_overlap(destination, Path(source_root).absolute()):
        raise FallbackDeliveryError(field_name + " must be source-disjoint")
    if destination.exists():
        raise FallbackDeliveryError(field_name + " must be a new path; refusing to overwrite")
    return destination


def _paths_overlap(left: Path, right: Path) -> bool:
    left_key = Path(os.path.normcase(os.path.abspath(str(left))))
    right_key = Path(os.path.normcase(os.path.abspath(str(right))))
    return left_key == right_key or left_key in right_key.parents or right_key in left_key.parents


def _prepare_staging_directory(destination: Path) -> Path:
    parent = destination.parent
    _reject_symlink_ancestors(parent)
    parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(parent)
    stage = parent / ("." + destination.name + ".staging-" + uuid.uuid4().hex)
    _reject_symlink_ancestors(stage)
    stage.mkdir(parents=False, exist_ok=False)
    return stage


def _commit_staged_output(stage: Path, destination: Path) -> None:
    if destination.exists():
        raise FallbackDeliveryError("output destination appeared during staging")
    _reject_symlink_ancestors(stage)
    _reject_symlink_ancestors(destination.parent)
    os.replace(stage, destination)


def _remove_generated_destination(destination: Path, source_root: Path) -> None:
    if _paths_overlap(destination, Path(source_root).absolute()):
        raise FallbackDeliveryError("refusing to remove an overlapping generated destination")
    if not destination.exists() and not destination.is_symlink():
        return
    if destination.is_symlink():
        destination.unlink()
    else:
        shutil.rmtree(destination)


def _remove_generated_staging_directory(stage: Path) -> None:
    if not stage.exists() and not stage.is_symlink():
        return
    if stage.is_symlink():
        stage.unlink()
    else:
        shutil.rmtree(stage)


def _write_file_map(root: Path, files: Mapping[str, bytes]) -> None:
    if root.exists():
        raise FallbackDeliveryError("writer refuses to overwrite an existing package directory")
    root.mkdir(parents=True, exist_ok=False)
    for relative, content in sorted(files.items()):
        _safe_relative_posix(relative, "output package path")
        target = root.joinpath(*PurePosixPath(relative).parts)
        _reject_symlink_ancestors(target.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_exact_bytes(target, content)


def _write_exact_bytes(path: Path, content: bytes) -> None:
    _reject_symlink_ancestors(path.parent)
    if path.exists() or path.is_symlink():
        raise FallbackDeliveryError("writer refuses to overwrite an existing file")
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise FallbackDeliveryError("written bytes did not round-trip")


def _reject_symlink_ancestors(path: Path) -> None:
    for candidate in (Path(path).absolute(), *Path(path).absolute().parents):
        if candidate.exists() and candidate.is_symlink():
            raise FallbackDeliveryError("symlinked paths are not allowed")
