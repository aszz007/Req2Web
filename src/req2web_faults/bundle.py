"""Canonical structural-blindness bundle assembly for future fault detection.

This module assembles one already-constructed deterministic artifact set into a
fixed detector-visible inventory.  It deliberately does not classify a bundle
as clean, negative-control, or mutated.  Injector-only fragment metadata is
consumed only while assembling and is never copied into the output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from req2web_acceptance import AcceptanceBindingPlan, AcceptancePlan, RequirementView
from req2web_generation.renderer import RenderResult
from req2web_generation.result_package_v2 import RetrievalEnhancedResultPackage
from req2web_generation.schema import PageSpec
from req2web_inspector.facts import InspectorFactSet

from .mutation import (
    FAULT_COPY_MANIFEST_SCHEMA_VERSION,
    FaultCopyRecord,
    FaultMutationError,
    canonical_json_bytes,
    canonical_sha256,
)


BLINDED_FAULT_BUNDLE_SCHEMA_VERSION = "req2web.blinded_fault_bundle.v1"
BLINDED_BUNDLE_PARITY_SCHEMA_VERSION = "req2web.blinded_bundle_inventory_parity.v1"

_BUNDLE_MANIFEST_NAME = "fault_case_bundle_manifest.json"
_ARTIFACT_ROOT = "artifact"
_BUNDLE_SLOTS = frozenset({
    "acceptance", "page_spec", "inspector_fact_set", "render_artifact", "result_package",
})
_FORBIDDEN_OUTPUT_BASENAMES = frozenset({
    "fault_copy_record.json",
    "fault_copy_manifest.json",
    "injector_mutation_audit.json",
    "fault_gold_manifest.json",
})
_REQUIRED_PATH_SLOTS = {
    "artifact/acceptance/requirement_view.json": "acceptance",
    "artifact/acceptance/acceptance_plan.json": "acceptance",
    "artifact/acceptance/acceptance_binding.json": "acceptance",
    "artifact/page_spec.json": "page_spec",
    "artifact/inspector_fact_set.json": "inspector_fact_set",
    "artifact/render/index.html": "render_artifact",
    "artifact/render/styles.css": "render_artifact",
    "artifact/render/app.js": "render_artifact",
    "artifact/render/render_manifest.json": "render_artifact",
    "artifact/result_package/package_manifest.json": "result_package",
}


class FaultBundleError(ValueError):
    """A source set, fragment, or bundle cannot be structurally trusted."""


@dataclass(frozen=True)
class BlindedBundleFile:
    """One detector-visible artifact entry with no injection provenance."""

    slot: str
    path: str
    size: int
    sha256: str

    def validate(self) -> None:
        if self.slot not in _BUNDLE_SLOTS:
            raise FaultBundleError("bundle file has an unsupported slot")
        _safe_relative_posix(self.path, "bundle file path")
        if not self.path.startswith(_ARTIFACT_ROOT + "/"):
            raise FaultBundleError("bundle file path must stay below artifact/")
        if not isinstance(self.size, int) or self.size < 0:
            raise FaultBundleError("bundle file size must be a non-negative integer")
        _require_sha256(self.sha256, "bundle file sha256")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class BlindedFaultBundleRecord:
    """Canonical manifest for the detector-visible structural inventory."""

    bundle_id: str
    case_id: str
    page_id: str
    files: tuple[BlindedBundleFile, ...]
    inventory_sha256: str
    schema_version: str = BLINDED_FAULT_BUNDLE_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "files": [entry.to_dict() for entry in self.files],
            "inventory_sha256": self.inventory_sha256,
            "page_id": self.page_id,
            "schema_version": self.schema_version,
        }

    def validate(self) -> None:
        _require_text(self.bundle_id, "bundle_id")
        _require_text(self.case_id, "case_id")
        _require_text(self.page_id, "page_id")
        if self.schema_version != BLINDED_FAULT_BUNDLE_SCHEMA_VERSION:
            raise FaultBundleError("unsupported blinded bundle schema")
        if not isinstance(self.files, tuple) or not self.files:
            raise FaultBundleError("bundle files must be a non-empty tuple")
        for entry in self.files:
            if not isinstance(entry, BlindedBundleFile):
                raise FaultBundleError("bundle files must contain BlindedBundleFile values")
            entry.validate()
        paths = [entry.path for entry in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise FaultBundleError("bundle paths must be sorted and unique")
        slots = {entry.slot for entry in self.files}
        if not _BUNDLE_SLOTS.issubset(slots):
            raise FaultBundleError("bundle inventory is missing a required slot")
        path_slot = {entry.path: entry.slot for entry in self.files}
        for path, slot in _REQUIRED_PATH_SLOTS.items():
            if path_slot.get(path) != slot:
                raise FaultBundleError("bundle inventory has an invalid required path set")
        _require_sha256(self.inventory_sha256, "inventory_sha256")
        expected_inventory = _inventory_sha256(self.files)
        if self.inventory_sha256 != expected_inventory:
            raise FaultBundleError("bundle inventory_sha256 does not match canonical file entries")
        expected_id = "fault-bundle-" + canonical_sha256(self.to_payload())[:20]
        if self.bundle_id != expected_id:
            raise FaultBundleError("bundle_id does not match canonical payload")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"bundle_id": self.bundle_id, **self.to_payload()}

    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True)
class BlindedFaultBundleSource:
    """One already-constructed deterministic artifact set.

    The caller owns any negative-control semantics. This M2 source is limited
    to the G0 Inspector plus RetrievalEnhancedResultPackage v2 route; it carries
    no control/fault label and creates no semantic intervention.
    """

    case_id: str
    requirement_view: RequirementView
    acceptance_plan: AcceptancePlan
    acceptance_binding: AcceptanceBindingPlan
    page_spec: PageSpec
    inspector_fact_set: InspectorFactSet
    render_result: RenderResult
    result_package: RetrievalEnhancedResultPackage

    def validate(self) -> None:
        _require_text(self.case_id, "case_id")
        self.requirement_view.validate()
        self.acceptance_plan.validate_against(self.requirement_view)
        self.page_spec.validate()
        render_files = _validated_render_files(self.render_result)
        self.acceptance_binding.validate_against(
            self.requirement_view,
            self.acceptance_plan,
            self.page_spec,
            self.render_result,
        )
        self.inspector_fact_set.validate()
        package_files = _validated_result_package_files(self.result_package)
        _validate_result_package_alignment(
            self.page_spec,
            self.inspector_fact_set,
            render_files,
            package_files,
        )
        page_id = self.page_spec.page_id
        if any(value != page_id for value in (
            self.inspector_fact_set.page_id,
            self.render_result.page_id,
            self.acceptance_binding.page_id,
            self.result_package.page_id,
        )):
            raise FaultBundleError("source artifacts do not share one page_id")
        page_spec_sha256 = canonical_sha256(self.page_spec.to_dict())
        if self.inspector_fact_set.page_spec_sha256 != page_spec_sha256:
            raise FaultBundleError("Inspector fact set does not bind the supplied PageSpec")
        if not render_files:
            raise FaultBundleError("render artifact inventory must not be empty")

    def artifact_files(self) -> dict[str, bytes]:
        self.validate()
        render_files = _validated_render_files(self.render_result)
        package_files = _validated_result_package_files(self.result_package)
        _validate_result_package_alignment(
            self.page_spec,
            self.inspector_fact_set,
            render_files,
            package_files,
        )
        result = {
            "artifact/acceptance/requirement_view.json": canonical_json_bytes(self.requirement_view.to_dict()),
            "artifact/acceptance/acceptance_plan.json": canonical_json_bytes(self.acceptance_plan.to_dict()),
            "artifact/acceptance/acceptance_binding.json": canonical_json_bytes(self.acceptance_binding.to_dict()),
            "artifact/page_spec.json": canonical_json_bytes(self.page_spec.to_dict()),
            "artifact/inspector_fact_set.json": canonical_json_bytes(self.inspector_fact_set.to_dict()),
        }
        for name, content in render_files.items():
            result["artifact/" + name] = content
        for name, content in package_files.items():
            result["artifact/result_package/" + name] = content
        _validate_bundle_file_map(result)
        return dict(sorted(result.items()))


@dataclass(frozen=True)
class BlindedBundleInventoryParityReport:
    """Aggregate path/schema parity without per-bundle identities or labels."""

    bundle_count: int
    path_count: int
    paths: tuple[str, ...]
    slots: tuple[str, ...]
    path_set_sha256: str
    schema_version: str = BLINDED_BUNDLE_PARITY_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != BLINDED_BUNDLE_PARITY_SCHEMA_VERSION:
            raise FaultBundleError("unsupported bundle parity schema")
        if not isinstance(self.bundle_count, int) or self.bundle_count < 2:
            raise FaultBundleError("bundle parity requires at least two bundles")
        if not isinstance(self.path_count, int) or self.path_count != len(self.paths):
            raise FaultBundleError("bundle parity path_count does not match paths")
        if not self.paths or self.paths != tuple(sorted(self.paths)):
            raise FaultBundleError("bundle parity paths must be sorted and non-empty")
        if len(set(self.paths)) != len(self.paths):
            raise FaultBundleError("bundle parity paths must be unique")
        for path in self.paths:
            _safe_relative_posix(path, "bundle parity path")
            if not path.startswith(_ARTIFACT_ROOT + "/"):
                raise FaultBundleError("bundle parity path must stay below artifact/")
        if self.slots != tuple(sorted(_BUNDLE_SLOTS)):
            raise FaultBundleError("bundle parity must expose the canonical slot set")
        _require_sha256(self.path_set_sha256, "path_set_sha256")
        if self.path_set_sha256 != canonical_sha256({"paths": list(self.paths), "slots": list(self.slots)}):
            raise FaultBundleError("bundle parity path_set_sha256 does not match canonical paths")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)


def assemble_blinded_fault_bundle(
    source: BlindedFaultBundleSource,
    output_dir: Path,
    *,
    fragment_dir: Path | None = None,
) -> BlindedFaultBundleRecord:
    """Write one fixed artifact inventory without exporting injector provenance.

    ``fragment_dir`` is either absent or one verified M2-02 fragment directory.
    A caller-provided negative control uses the same ``source`` path and simply
    omits ``fragment_dir``; this function never creates semantic controls.
    """
    if not isinstance(source, BlindedFaultBundleSource):
        raise FaultBundleError("source must be BlindedFaultBundleSource")
    files = source.artifact_files()
    if fragment_dir is not None:
        updates = _validated_fragment_updates(source, files, Path(fragment_dir))
        if not updates:
            raise FaultBundleError("fragment must replace exactly one registered slot")
        files = {**files, **updates}
    _validate_bundle_file_map(files)
    destination = _prepare_empty_output_directory(Path(output_dir))
    _write_file_map(destination, files)
    actual = _read_tree_files(destination)
    if actual != files:
        raise FaultBundleError("bundle artifact bytes did not round-trip")
    record = _make_bundle_record(source.case_id, source.page_spec.page_id, actual)
    manifest_bytes = canonical_json_bytes(record.to_dict())
    _write_exact_bytes(destination / _BUNDLE_MANIFEST_NAME, manifest_bytes)
    reloaded = load_blinded_fault_bundle(destination)
    if reloaded.to_dict() != record.to_dict():
        raise FaultBundleError("bundle manifest did not round-trip")
    return record


def load_blinded_fault_bundle(bundle_dir: Path) -> BlindedFaultBundleRecord:
    """Load and structurally validate a written detector-visible bundle."""
    root = Path(bundle_dir)
    files = _read_tree_files(root)
    if _BUNDLE_MANIFEST_NAME not in files:
        raise FaultBundleError("bundle manifest is missing")
    artifact_files = {path: content for path, content in files.items() if path.startswith(_ARTIFACT_ROOT + "/")}
    if len(artifact_files) + 1 != len(files):
        raise FaultBundleError("bundle directory has an unexpected file inventory")
    payload = _load_json_object(files[_BUNDLE_MANIFEST_NAME], _BUNDLE_MANIFEST_NAME)
    expected_keys = {"bundle_id", "case_id", "page_id", "files", "inventory_sha256", "schema_version"}
    if set(payload) != expected_keys:
        raise FaultBundleError("bundle manifest has an unexpected field set")
    file_payloads = payload.get("files")
    if not isinstance(file_payloads, list):
        raise FaultBundleError("bundle manifest files must be a list")
    try:
        entries = tuple(BlindedBundleFile(**item) for item in file_payloads if isinstance(item, dict))
    except TypeError as error:
        raise FaultBundleError("bundle manifest file entry is invalid") from error
    if len(entries) != len(file_payloads):
        raise FaultBundleError("bundle manifest file entries must be objects")
    try:
        record = BlindedFaultBundleRecord(
            bundle_id=payload["bundle_id"],
            case_id=payload["case_id"],
            page_id=payload["page_id"],
            files=entries,
            inventory_sha256=payload["inventory_sha256"],
            schema_version=payload["schema_version"],
        )
    except TypeError as error:
        raise FaultBundleError("bundle manifest is invalid") from error
    record.validate()
    actual_entries = _bundle_file_entries(artifact_files)
    if tuple(entry.to_dict() for entry in record.files) != tuple(entry.to_dict() for entry in actual_entries):
        raise FaultBundleError("bundle manifest file entries do not match on-disk artifacts")
    return record


def validate_blinded_bundle_inventory_parity(
    bundles: tuple[BlindedFaultBundleRecord, ...] | list[BlindedFaultBundleRecord],
) -> BlindedBundleInventoryParityReport:
    """Prove identical detector-visible path/slot/schema inventory across rows."""
    records = tuple(bundles)
    if len(records) < 2:
        raise FaultBundleError("bundle parity requires at least two bundles")
    for record in records:
        if not isinstance(record, BlindedFaultBundleRecord):
            raise FaultBundleError("bundle parity requires BlindedFaultBundleRecord values")
        record.validate()
    first_paths = tuple(entry.path for entry in records[0].files)
    first_slots = tuple(entry.slot for entry in records[0].files)
    first_schema = records[0].schema_version
    for record in records[1:]:
        if record.schema_version != first_schema:
            raise FaultBundleError("bundle parity schema mismatch")
        if tuple(entry.path for entry in record.files) != first_paths:
            raise FaultBundleError("bundle parity path inventory mismatch")
        if tuple(entry.slot for entry in record.files) != first_slots:
            raise FaultBundleError("bundle parity slot inventory mismatch")
    report = BlindedBundleInventoryParityReport(
        bundle_count=len(records),
        path_count=len(first_paths),
        paths=first_paths,
        slots=tuple(sorted(_BUNDLE_SLOTS)),
        path_set_sha256=canonical_sha256({"paths": list(first_paths), "slots": sorted(_BUNDLE_SLOTS)}),
    )
    report.validate()
    return report


def _validated_fragment_updates(
    source: BlindedFaultBundleSource,
    clean_files: Mapping[str, bytes],
    fragment_dir: Path,
) -> dict[str, bytes]:
    record, fragment_files = _load_fragment(fragment_dir)
    if record.detector_input_ready is not False:
        raise FaultBundleError("M2-02 fragment must remain non-detector-ready")
    if record.case_id != source.case_id:
        raise FaultBundleError("fragment case_id does not match bundle source")
    route = _fragment_route(record.copy_location)
    expected_kind, expected_id = _expected_fragment_target(source, route)
    if record.target_artifact_kind != expected_kind:
        raise FaultBundleError("fragment target_artifact_kind does not match bundle source slot")
    if record.target_artifact_id != expected_id:
        raise FaultBundleError("fragment target_artifact_id does not match bundle source slot")
    source_slot = _source_files_for_fragment_slot(clean_files, route)
    if set(fragment_files) != set(source_slot):
        raise FaultBundleError("fragment artifact inventory does not match exactly one registered slot")
    if _file_map_sha256(source_slot) != record.source_sha256:
        raise FaultBundleError("fragment source hash does not match bundle source slot")
    if _file_map_sha256(fragment_files) != record.mutated_sha256:
        raise FaultBundleError("fragment bytes do not match the runtime record hash")
    if _file_map_sha256(fragment_files) != record.copy_manifest_identity:
        raise FaultBundleError("fragment bytes do not match the runtime manifest identity")
    updates = _bundle_updates_for_route(route, fragment_files)
    if set(updates) != _bundle_paths_for_fragment_slot(clean_files, route):
        raise FaultBundleError("fragment replacement does not cover exactly one registered slot")
    if all(clean_files[path] == content for path, content in updates.items()):
        raise FaultBundleError("fragment replacement must change its registered slot")
    return updates


def _load_fragment(fragment_dir: Path) -> tuple[FaultCopyRecord, dict[str, bytes]]:
    files = _read_tree_files(fragment_dir)
    required = {"fault_copy_record.json", "fault_copy_manifest.json"}
    if not required.issubset(files):
        raise FaultBundleError("fragment runtime metadata is incomplete")
    allowed = required | {path for path in files if path.startswith("artifact/")}
    if set(files) != allowed or not any(path.startswith("artifact/") for path in files):
        raise FaultBundleError("fragment has an unexpected file inventory")
    record_payload = _load_json_object(files["fault_copy_record.json"], "fault_copy_record.json")
    expected_record_keys = {
        "fault_copy_id", "case_id", "target_artifact_kind", "target_artifact_id", "source_sha256",
        "mutated_sha256", "copy_location", "copy_manifest_identity", "detector_input_ready", "schema_version",
    }
    if set(record_payload) != expected_record_keys:
        raise FaultBundleError("fragment runtime record has an unexpected field set")
    try:
        record = FaultCopyRecord(**record_payload)
    except TypeError as error:
        raise FaultBundleError("fragment runtime record is invalid") from error
    try:
        record.validate()
    except FaultMutationError as error:
        raise FaultBundleError("fragment runtime record failed validation") from error
    manifest = _load_json_object(files["fault_copy_manifest.json"], "fault_copy_manifest.json")
    expected_manifest_keys = {
        "schema_version", "fault_copy_id", "fault_copy_record_sha256", "copy_manifest_identity", "artifact_file_count",
    }
    if set(manifest) != expected_manifest_keys or manifest.get("schema_version") != FAULT_COPY_MANIFEST_SCHEMA_VERSION:
        raise FaultBundleError("fragment runtime manifest has an unexpected field set")
    if manifest.get("fault_copy_id") != record.fault_copy_id or manifest.get("fault_copy_record_sha256") != record.sha256():
        raise FaultBundleError("fragment runtime manifest does not bind the runtime record")
    if manifest.get("copy_manifest_identity") != record.copy_manifest_identity:
        raise FaultBundleError("fragment runtime manifest does not bind artifact identity")
    artifact_files = {path.removeprefix("artifact/"): content for path, content in files.items() if path.startswith("artifact/")}
    if manifest.get("artifact_file_count") != len(artifact_files):
        raise FaultBundleError("fragment runtime manifest has an invalid artifact_file_count")
    return record, artifact_files


def _fragment_route(copy_location: str) -> str:
    if copy_location == "artifact/page_spec_fault.json":
        return "page_spec"
    if copy_location == "artifact/inspector_fact_set_fault.json":
        return "inspector_fact_set"
    if copy_location == "artifact/render/index.html":
        return "render_artifact"
    if copy_location == "artifact/result_package/package_manifest.json":
        return "result_package"
    raise FaultBundleError("fragment copy_location is not a registered bundle slot")


def _expected_fragment_target(source: BlindedFaultBundleSource, route: str) -> tuple[str, str]:
    if route == "page_spec":
        return "page_spec", source.page_spec.page_id
    if route == "inspector_fact_set":
        return "inspector_fact_set", source.inspector_fact_set.fact_set_id
    if route == "render_artifact":
        return "render_artifact", source.render_result.page_id
    if route == "result_package":
        return "result_package", source.result_package.package_id
    raise FaultBundleError("unknown fragment route")


def _source_files_for_fragment_slot(clean_files: Mapping[str, bytes], route: str) -> dict[str, bytes]:
    if route == "page_spec":
        return {"page_spec_fault.json": clean_files["artifact/page_spec.json"]}
    if route == "inspector_fact_set":
        return {"inspector_fact_set_fault.json": clean_files["artifact/inspector_fact_set.json"]}
    if route == "render_artifact":
        return {path.removeprefix("artifact/"): content for path, content in clean_files.items() if path.startswith("artifact/render/")}
    if route == "result_package":
        return {path.removeprefix("artifact/"): content for path, content in clean_files.items() if path.startswith("artifact/result_package/")}
    raise FaultBundleError("unknown fragment route")


def _bundle_paths_for_fragment_slot(clean_files: Mapping[str, bytes], route: str) -> set[str]:
    if route == "page_spec":
        return {"artifact/page_spec.json"}
    if route == "inspector_fact_set":
        return {"artifact/inspector_fact_set.json"}
    if route == "render_artifact":
        return {path for path in clean_files if path.startswith("artifact/render/")}
    if route == "result_package":
        return {path for path in clean_files if path.startswith("artifact/result_package/")}
    raise FaultBundleError("unknown fragment route")


def _bundle_updates_for_route(route: str, fragment_files: Mapping[str, bytes]) -> dict[str, bytes]:
    if route == "page_spec":
        return {"artifact/page_spec.json": fragment_files["page_spec_fault.json"]}
    if route == "inspector_fact_set":
        return {"artifact/inspector_fact_set.json": fragment_files["inspector_fact_set_fault.json"]}
    if route in {"render_artifact", "result_package"}:
        return {"artifact/" + path: content for path, content in fragment_files.items()}
    raise FaultBundleError("unknown fragment route")


def _make_bundle_record(case_id: str, page_id: str, files: Mapping[str, bytes]) -> BlindedFaultBundleRecord:
    entries = tuple(_bundle_file_entries(files))
    unsigned = BlindedFaultBundleRecord(
        bundle_id="",
        case_id=case_id,
        page_id=page_id,
        files=entries,
        inventory_sha256=_inventory_sha256(entries),
    )
    record = replace(unsigned, bundle_id="fault-bundle-" + canonical_sha256(unsigned.to_payload())[:20])
    record.validate()
    return record


def _bundle_file_entries(files: Mapping[str, bytes]) -> tuple[BlindedBundleFile, ...]:
    entries = []
    for path, content in sorted(files.items()):
        entries.append(BlindedBundleFile(
            slot=_slot_for_bundle_path(path),
            path=path,
            size=len(content),
            sha256=sha256(content).hexdigest(),
        ))
    return tuple(entries)


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
    raise FaultBundleError("bundle path is outside the canonical artifact inventory")


def _inventory_sha256(entries: tuple[BlindedBundleFile, ...]) -> str:
    return canonical_sha256({"files": [entry.to_dict() for entry in entries]})


def _validated_render_files(render_result: RenderResult) -> dict[str, bytes]:
    root = render_result.output_dir
    if root.is_symlink() or not root.is_dir():
        raise FaultBundleError("render output directory must be a real directory")
    expected_paths = {
        "index.html": render_result.index_html,
        "styles.css": render_result.styles_css,
        "app.js": render_result.app_js,
        "render_manifest.json": render_result.render_manifest,
    }
    if any(path != root / name for name, path in expected_paths.items()):
        raise FaultBundleError("RenderResult must use direct-child render artifact paths")
    files = _read_tree_files(root, prefix="render")
    expected = {"render/" + name for name in expected_paths}
    if set(files) != expected:
        raise FaultBundleError("render artifact has an unexpected file set")
    manifest = _load_json_object(files["render/render_manifest.json"], "render_manifest.json")
    if manifest.get("page_id") != render_result.page_id or not isinstance(manifest.get("files"), list):
        raise FaultBundleError("render manifest does not match RenderResult")
    declared: dict[str, str] = {}
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != {"name", "sha256"}:
            raise FaultBundleError("render manifest file entry is invalid")
        name = item.get("name")
        digest = item.get("sha256")
        if not isinstance(name, str) or name in declared:
            raise FaultBundleError("render manifest file names must be unique")
        _require_sha256(digest, "render manifest sha256")
        declared[name] = digest
    if set(declared) != {"index.html", "styles.css", "app.js"}:
        raise FaultBundleError("render manifest has an unexpected declaration set")
    for name, digest in declared.items():
        if sha256(files["render/" + name]).hexdigest() != digest:
            raise FaultBundleError("render manifest hash does not match rendered bytes")
    return files


def _validated_result_package_files(
    result_package: RetrievalEnhancedResultPackage,
) -> dict[str, bytes]:
    if not isinstance(result_package, RetrievalEnhancedResultPackage):
        raise FaultBundleError(
            "M2 blinded bundles require a G0 RetrievalEnhancedResultPackage v2"
        )
    result_package.validate()
    root = result_package.package_dir
    if root.is_symlink() or not root.is_dir():
        raise FaultBundleError("result package directory must be a real directory")
    return _read_tree_files(root)


def _validate_result_package_alignment(
    page_spec: PageSpec,
    inspector_fact_set: InspectorFactSet,
    render_files: Mapping[str, bytes],
    package_files: Mapping[str, bytes],
) -> None:
    required = {
        "internal/retrieval_guidance.json",
        "internal/guided_page_spec_build_result.json",
        "internal/page_spec.json",
        "internal/retrieval_influence_report.json",
        "page/index.html",
        "page/styles.css",
        "page/app.js",
        "page/render_manifest.json",
    }
    if not required.issubset(package_files):
        raise FaultBundleError("result package is missing source-alignment artifacts")
    embedded_page_spec = _load_json_object(
        package_files["internal/page_spec.json"],
        "result package internal/page_spec.json",
    )
    if embedded_page_spec != page_spec.to_dict():
        raise FaultBundleError("result package embedded PageSpec does not match supplied PageSpec")
    if inspector_fact_set.run_group != "G0":
        raise FaultBundleError("M2 blinded bundles require a G0 InspectorFactSet")
    guidance = _load_json_object(
        package_files["internal/retrieval_guidance.json"],
        "result package internal/retrieval_guidance.json",
    )
    guided = _load_json_object(
        package_files["internal/guided_page_spec_build_result.json"],
        "result package internal/guided_page_spec_build_result.json",
    )
    influence = _load_json_object(
        package_files["internal/retrieval_influence_report.json"],
        "result package internal/retrieval_influence_report.json",
    )
    inspector_bindings = (
        (
            "guidance_bundle_id",
            inspector_fact_set.guidance_bundle_id,
            guidance.get("guidance_bundle_id"),
        ),
        (
            "guided_build_result_id",
            inspector_fact_set.guided_build_result_id,
            guided.get("build_result_id"),
        ),
        (
            "retrieval_influence_report_id",
            inspector_fact_set.retrieval_influence_report_id,
            influence.get("report_id"),
        ),
        (
            "guidance_sha256",
            inspector_fact_set.guidance_sha256,
            canonical_sha256(guidance),
        ),
        (
            "guided_build_result_sha256",
            inspector_fact_set.guided_build_result_sha256,
            canonical_sha256(guided),
        ),
        (
            "retrieval_influence_report_sha256",
            inspector_fact_set.retrieval_influence_report_sha256,
            canonical_sha256(influence),
        ),
        (
            "retrieval_influence_report_passed",
            inspector_fact_set.retrieval_influence_report_passed,
            influence.get("passed"),
        ),
    )
    for field_name, actual, expected in inspector_bindings:
        if actual != expected:
            raise FaultBundleError(
                "Inspector fact set " + field_name + " does not match the supplied G0 v2 package"
            )
    for name in ("index.html", "styles.css", "app.js", "render_manifest.json"):
        if package_files["page/" + name] != render_files["render/" + name]:
            raise FaultBundleError("result package page/" + name + " does not match supplied RenderResult")


def _validate_bundle_file_map(files: Mapping[str, bytes]) -> None:
    if not files:
        raise FaultBundleError("bundle artifact inventory must not be empty")
    for path, content in files.items():
        _safe_relative_posix(path, "bundle artifact path")
        if not path.startswith(_ARTIFACT_ROOT + "/"):
            raise FaultBundleError("bundle artifact path must stay below artifact/")
        if PurePosixPath(path).name in _FORBIDDEN_OUTPUT_BASENAMES:
            raise FaultBundleError("bundle artifact path may not contain injector or evaluator provenance")
        if not isinstance(content, bytes):
            raise FaultBundleError("bundle artifact content must be bytes")
    paths = set(files)
    if not set(_REQUIRED_PATH_SLOTS).issubset(paths):
        raise FaultBundleError("bundle artifact inventory is missing required files")
    for path in paths:
        _slot_for_bundle_path(path)


def _prepare_empty_output_directory(output_dir: Path) -> Path:
    destination = output_dir.absolute()
    _reject_symlink_ancestors(destination)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise FaultBundleError("bundle output_dir must be a real directory path")
        if any(destination.iterdir()):
            raise FaultBundleError("bundle output_dir must be empty; refusing to overwrite")
    else:
        destination.mkdir(parents=True, exist_ok=False)
    return destination


def _reject_symlink_ancestors(path: Path) -> None:
    for candidate in (path.absolute(), *path.absolute().parents):
        if candidate.exists() and candidate.is_symlink():
            raise FaultBundleError("symlinked paths are not allowed")


def _read_tree_files(root: Path, *, prefix: str = "") -> dict[str, bytes]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise FaultBundleError("artifact root must be a real directory")
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise FaultBundleError("symlinked artifact paths are not allowed")
        if path.is_dir():
            continue
        if not path.is_file():
            raise FaultBundleError("artifact tree contains an unsupported path type")
        relative = path.relative_to(root).as_posix()
        _safe_relative_posix(relative, "artifact relative path")
        name = (prefix.rstrip("/") + "/" if prefix else "") + relative
        _safe_relative_posix(name, "artifact relative path")
        if name in result:
            raise FaultBundleError("artifact tree contains duplicate paths")
        result[name] = path.read_bytes()
    return dict(sorted(result.items()))


def _write_file_map(root: Path, files: Mapping[str, bytes]) -> None:
    for relative, content in sorted(files.items()):
        _safe_relative_posix(relative, "bundle output path")
        path = root / PurePosixPath(relative)
        _reject_symlink_ancestors(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_exact_bytes(path, content)


def _write_exact_bytes(path: Path, content: bytes) -> None:
    _reject_symlink_ancestors(path.parent)
    if path.exists():
        raise FaultBundleError("bundle writer refuses to overwrite a file")
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise FaultBundleError("bundle output bytes did not round-trip")


def _file_map_sha256(files: Mapping[str, bytes]) -> str:
    entries = [
        {"path": path, "size": len(content), "sha256": sha256(content).hexdigest()}
        for path, content in sorted(files.items())
    ]
    return canonical_sha256({"files": entries})


def _load_json_object(content: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FaultBundleError(name + " must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise FaultBundleError(name + " must be a JSON object")
    return value


def _safe_relative_posix(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise FaultBundleError(field_name + " must be a non-empty relative path")
    if "\\" in value:
        raise FaultBundleError(field_name + " must use safe POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise FaultBundleError(field_name + " must not traverse or escape its root")


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise FaultBundleError(field_name + " must be non-empty text")


def _require_sha256(value: object, field_name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(item not in "0123456789abcdef" for item in value):
        raise FaultBundleError(field_name + " must be a lowercase SHA-256")
