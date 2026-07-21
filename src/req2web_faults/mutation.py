"""Deterministic development-only fault-copy injection.

The serialized runtime layer is mutation-fragment metadata and is not
detector-ready input. This module keeps registered mutation requests and exact
targets inside injector-only in-memory build results; it never writes that
provenance into fragment metadata.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from req2web_generation.renderer import RenderResult
from req2web_generation.result_package import ResultPackage
from req2web_generation.result_package_v2 import RetrievalEnhancedResultPackage
from req2web_generation.schema import PageSpec
from req2web_inspector.facts import InspectorFactSet


FAULT_COPY_SCHEMA_VERSION = "req2web.fault_copy.v3"
FAULT_COPY_MANIFEST_SCHEMA_VERSION = "req2web.fault_copy_manifest.v3"

PAGE_SPEC_COMPONENT_REMOVED = "page_spec_component_removed"
INSPECTOR_TRACE_RELATION_REMOVED = "inspector_trace_relation_removed"
RENDER_COMPONENT_STABLE_ID_TAMPERED = "render_component_stable_id_tampered"
PACKAGE_MANIFEST_PATH_TAMPERED = "package_manifest_path_tampered"
PACKAGE_MANIFEST_SHA256_TAMPERED = "package_manifest_sha256_tampered"

REGISTERED_MUTATION_KINDS = (
    PAGE_SPEC_COMPONENT_REMOVED,
    INSPECTOR_TRACE_RELATION_REMOVED,
    RENDER_COMPONENT_STABLE_ID_TAMPERED,
    PACKAGE_MANIFEST_PATH_TAMPERED,
    PACKAGE_MANIFEST_SHA256_TAMPERED,
)

_KIND_ARTIFACT_KIND = {
    PAGE_SPEC_COMPONENT_REMOVED: "page_spec",
    INSPECTOR_TRACE_RELATION_REMOVED: "inspector_fact_set",
    RENDER_COMPONENT_STABLE_ID_TAMPERED: "render_artifact",
    PACKAGE_MANIFEST_PATH_TAMPERED: "result_package",
    PACKAGE_MANIFEST_SHA256_TAMPERED: "result_package",
}
_KIND_COPY_LOCATION = {
    PAGE_SPEC_COMPONENT_REMOVED: "artifact/page_spec_fault.json",
    INSPECTOR_TRACE_RELATION_REMOVED: "artifact/inspector_fact_set_fault.json",
    RENDER_COMPONENT_STABLE_ID_TAMPERED: "artifact/render/index.html",
    PACKAGE_MANIFEST_PATH_TAMPERED: "artifact/result_package/package_manifest.json",
    PACKAGE_MANIFEST_SHA256_TAMPERED: "artifact/result_package/package_manifest.json",
}


class FaultMutationError(ValueError):
    """The requested development fault copy is not safe, exact, or isolated."""


def canonical_json_bytes(value: object) -> bytes:
    if hasattr(value, "to_payload"):
        value = value.to_payload()  # type: ignore[assignment]
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class FaultMutationRequest:
    """Injector-only pre-registered kind plus one exact source-artifact target."""

    case_id: str
    mutation_kind: str
    target_id: str
    manifest_field: str | None = None

    def validate(self) -> None:
        for name in ("case_id", "mutation_kind", "target_id"):
            _require_text(getattr(self, name), name)
        if self.mutation_kind not in REGISTERED_MUTATION_KINDS:
            raise FaultMutationError("unknown mutation_kind")
        package_kinds = {PACKAGE_MANIFEST_PATH_TAMPERED, PACKAGE_MANIFEST_SHA256_TAMPERED}
        if self.mutation_kind in package_kinds:
            expected = "path" if self.mutation_kind == PACKAGE_MANIFEST_PATH_TAMPERED else "sha256"
            if self.manifest_field != expected:
                raise FaultMutationError("package manifest mutation requires its registered manifest_field")
            _safe_relative_posix(self.target_id, "target_id")
        elif self.manifest_field is not None:
            raise FaultMutationError("manifest_field is reserved for package manifest mutations")

    def sha256(self) -> str:
        self.validate()
        return canonical_sha256(asdict(self))


@dataclass(frozen=True)
class FaultCopyRecord:
    """Opaque mutation-fragment identity and integrity metadata; not detector-ready."""

    fault_copy_id: str
    case_id: str
    target_artifact_kind: str
    target_artifact_id: str
    source_sha256: str
    mutated_sha256: str
    copy_location: str
    copy_manifest_identity: str
    detector_input_ready: bool = False
    schema_version: str = FAULT_COPY_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("fault_copy_id", None)
        return payload

    def validate(self) -> None:
        for name in (
            "fault_copy_id", "case_id", "target_artifact_kind", "target_artifact_id",
            "copy_location",
        ):
            _require_text(getattr(self, name), name)
        if self.schema_version != FAULT_COPY_SCHEMA_VERSION:
            raise FaultMutationError("unsupported fault-copy schema")
        _safe_relative_posix(self.copy_location, "copy_location")
        if self.detector_input_ready is not False:
            raise FaultMutationError("mutation fragments must remain not detector-ready")
        for name in ("source_sha256", "mutated_sha256", "copy_manifest_identity"):
            _require_sha256(getattr(self, name), name)
        if self.source_sha256 == self.mutated_sha256:
            raise FaultMutationError("fault copy must differ from its source artifact")
        expected = "fault-copy-" + canonical_sha256(self.to_payload())[:20]
        if self.fault_copy_id != expected:
            raise FaultMutationError("fault_copy_id does not match canonical payload")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def sha256(self) -> str:
        self.validate()
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True)
class FaultCopyBuildResult:
    """Injector-only bridge between a non-detector-ready fragment and provenance."""

    runtime_record: FaultCopyRecord
    mutation_request: FaultMutationRequest
    source_artifact_id: str
    changed_path: str

    def validate(self) -> None:
        self.runtime_record.validate()
        self.mutation_request.validate()
        for name in ("source_artifact_id", "changed_path"):
            _require_text(getattr(self, name), name)
        if self.runtime_record.case_id != self.mutation_request.case_id:
            raise FaultMutationError("injector build result has an inconsistent case_id")
        if self.runtime_record.target_artifact_id != self.source_artifact_id:
            raise FaultMutationError("injector build result has an inconsistent artifact identity")


def build_fault_copy(
    request: FaultMutationRequest,
    source: PageSpec | InspectorFactSet | RenderResult | ResultPackage | RetrievalEnhancedResultPackage,
    output_dir: Path,
) -> FaultCopyBuildResult:
    """Create a runtime copy and retain exact provenance only in injector memory."""
    request.validate()
    if request.mutation_kind == PAGE_SPEC_COMPONENT_REMOVED:
        plan = _page_spec_plan(request, source)
    elif request.mutation_kind == INSPECTOR_TRACE_RELATION_REMOVED:
        plan = _fact_set_plan(request, source)
    elif request.mutation_kind == RENDER_COMPONENT_STABLE_ID_TAMPERED:
        plan = _render_plan(request, source)
    elif request.mutation_kind in {PACKAGE_MANIFEST_PATH_TAMPERED, PACKAGE_MANIFEST_SHA256_TAMPERED}:
        plan = _package_plan(request, source)
    else:
        raise FaultMutationError("registered mutation_kind has no safe dispatcher")
    runtime_record = _write_runtime_output(output_dir=output_dir, **plan)
    result = FaultCopyBuildResult(
        runtime_record=runtime_record,
        mutation_request=request,
        source_artifact_id=plan["target_artifact_id"],
        changed_path=plan["changed_path"],
    )
    result.validate()
    return result


def write_fault_copy(
    request: FaultMutationRequest,
    source: PageSpec | InspectorFactSet | RenderResult | ResultPackage | RetrievalEnhancedResultPackage,
    output_dir: Path,
) -> FaultCopyRecord:
    """Injector convenience API returning only non-detector-ready fragment metadata."""
    return build_fault_copy(request, source, output_dir).runtime_record


def write_page_spec_component_removal(*, case_id: str, page_spec: PageSpec, component_id: str, output_dir: Path) -> FaultCopyRecord:
    return write_fault_copy(FaultMutationRequest(case_id, PAGE_SPEC_COMPONENT_REMOVED, component_id), page_spec, output_dir)


def write_inspector_trace_relation_removal(*, case_id: str, fact_set: InspectorFactSet, trace_link_id: str, output_dir: Path) -> FaultCopyRecord:
    return write_fault_copy(FaultMutationRequest(case_id, INSPECTOR_TRACE_RELATION_REMOVED, trace_link_id), fact_set, output_dir)


def write_render_stable_id_binding_tamper(*, case_id: str, render_result: RenderResult, component_id: str, output_dir: Path) -> FaultCopyRecord:
    return write_fault_copy(FaultMutationRequest(case_id, RENDER_COMPONENT_STABLE_ID_TAMPERED, component_id), render_result, output_dir)


def write_package_manifest_tamper(*, case_id: str, result_package: ResultPackage | RetrievalEnhancedResultPackage, manifest_path: str, field: str, output_dir: Path) -> FaultCopyRecord:
    kind = {"path": PACKAGE_MANIFEST_PATH_TAMPERED, "sha256": PACKAGE_MANIFEST_SHA256_TAMPERED}.get(field)
    if kind is None:
        raise FaultMutationError("package manifest field must be path or sha256")
    return write_fault_copy(FaultMutationRequest(case_id, kind, manifest_path, field), result_package, output_dir)


def _page_spec_plan(request: FaultMutationRequest, source: object) -> dict[str, Any]:
    if not isinstance(source, PageSpec):
        raise FaultMutationError("page_spec_component_removed requires a PageSpec source")
    payload = source.to_dict()
    if request.target_id not in {item["component_id"] for item in payload["components"]}:
        raise FaultMutationError("requested PageSpec component_id does not exist")
    source_files = {"page_spec_fault.json": canonical_json_bytes(payload)}
    mutated_payload = dict(payload)
    mutated_payload["components"] = [item for item in payload["components"] if item["component_id"] != request.target_id]
    mutated_files = {"page_spec_fault.json": canonical_json_bytes(mutated_payload)}
    return _plan(request, source.page_id, "components[component_id=" + request.target_id + "]", source_files, mutated_files)


def _fact_set_plan(request: FaultMutationRequest, source: object) -> dict[str, Any]:
    if not isinstance(source, InspectorFactSet):
        raise FaultMutationError("inspector_trace_relation_removed requires an InspectorFactSet source")
    payload = source.to_dict()
    if sum(item["trace_link_id"] == request.target_id for item in payload["trace_links"]) != 1:
        raise FaultMutationError("requested Inspector trace_link_id does not exist exactly once")
    linked = [item for item in payload["facts"] if request.target_id in item["trace_link_ids"]]
    if len(linked) != 1:
        raise FaultMutationError("requested Inspector trace relation is not uniquely attached")
    source_files = {"inspector_fact_set_fault.json": canonical_json_bytes(payload)}
    mutated_payload = dict(payload)
    mutated_payload["trace_links"] = [item for item in payload["trace_links"] if item["trace_link_id"] != request.target_id]
    mutated_payload["facts"] = [
        {**item, "trace_link_ids": [value for value in item["trace_link_ids"] if value != request.target_id]}
        if request.target_id in item["trace_link_ids"] else item
        for item in payload["facts"]
    ]
    mutated_files = {"inspector_fact_set_fault.json": canonical_json_bytes(mutated_payload)}
    changed = "trace_links[trace_link_id=" + request.target_id + "] and linked facts[].trace_link_ids"
    return _plan(request, source.fact_set_id, changed, source_files, mutated_files)


def _render_plan(request: FaultMutationRequest, source: object) -> dict[str, Any]:
    if not isinstance(source, RenderResult):
        raise FaultMutationError("render_component_stable_id_tampered requires a RenderResult source")
    source_files = _validated_render_files(source)
    token = 'data-component-id="' + request.target_id + '"'
    html = source_files["render/index.html"].decode("utf-8")
    if html.count(token) != 1:
        raise FaultMutationError("requested component stable-ID binding must occur exactly once")
    replacement = 'data-component-id="fault-' + sha256(request.target_id.encode("utf-8")).hexdigest()[:20] + '"'
    mutated_files = dict(source_files)
    mutated_files["render/index.html"] = html.replace(token, replacement, 1).encode("utf-8")
    mutated_files = _sync_render_manifest_hashes(mutated_files)
    return _plan(request, source.page_id, "index.html:data-component-id=" + request.target_id, source_files, mutated_files)


def _package_plan(request: FaultMutationRequest, source: object) -> dict[str, Any]:
    if not isinstance(source, (ResultPackage, RetrievalEnhancedResultPackage)):
        raise FaultMutationError("package manifest mutations require a ResultPackage source")
    source.validate()
    source_files = _read_tree_files(source.package_dir, prefix="result_package")
    manifest_key = "result_package/package_manifest.json"
    manifest = _load_json_object(source_files[manifest_key], manifest_key)
    files = manifest.get("files")
    if not isinstance(files, list):
        raise FaultMutationError("package manifest files must be a list")
    matches = [item for item in files if isinstance(item, dict) and item.get("path") == request.target_id]
    if len(matches) != 1:
        raise FaultMutationError("requested package manifest path must exist exactly once")
    mutated_files = dict(source_files)
    if request.mutation_kind == PACKAGE_MANIFEST_PATH_TAMPERED:
        matches[0]["path"] = "fault-missing/" + request.target_id
    else:
        matches[0]["sha256"] = "0" * 64
    mutated_files[manifest_key] = canonical_json_bytes(manifest)
    changed = "package_manifest.json:files[path=" + request.target_id + "]." + str(request.manifest_field)
    return _plan(request, source.package_id, changed, source_files, mutated_files)


def _plan(request: FaultMutationRequest, artifact_id: str, changed_path: str, source_files: Mapping[str, bytes], mutated_files: Mapping[str, bytes]) -> dict[str, Any]:
    return {
        "request": request,
        "target_artifact_kind": _KIND_ARTIFACT_KIND[request.mutation_kind],
        "target_artifact_id": artifact_id,
        "changed_path": changed_path,
        "source_sha256": _file_map_sha256(source_files),
        "mutated_sha256": _file_map_sha256(mutated_files),
        "copy_location": _KIND_COPY_LOCATION[request.mutation_kind],
        "artifact_files": mutated_files,
    }


def _write_runtime_output(*, request: FaultMutationRequest, target_artifact_kind: str, target_artifact_id: str, changed_path: str, source_sha256: str, mutated_sha256: str, copy_location: str, artifact_files: Mapping[str, bytes], output_dir: Path) -> FaultCopyRecord:
    destination = _prepare_empty_output_directory(output_dir)
    files = _validated_file_map(artifact_files)
    _write_file_map(destination / "artifact", files)
    actual = _read_tree_files(destination / "artifact")
    if actual != files or _file_map_sha256(actual) != mutated_sha256:
        raise FaultMutationError("written fault-copy artifact bytes/hash did not round-trip")
    record = _make_runtime_record(
        request.case_id, target_artifact_kind, target_artifact_id,
        source_sha256, mutated_sha256, copy_location, _file_map_sha256(actual),
    )
    record_bytes = canonical_json_bytes(record.to_dict())
    _write_exact_bytes(destination / "fault_copy_record.json", record_bytes)
    manifest = {
        "schema_version": FAULT_COPY_MANIFEST_SCHEMA_VERSION,
        "fault_copy_id": record.fault_copy_id,
        "fault_copy_record_sha256": record.sha256(),
        "copy_manifest_identity": record.copy_manifest_identity,
        "artifact_file_count": len(actual),
    }
    manifest_bytes = canonical_json_bytes(manifest)
    _write_exact_bytes(destination / "fault_copy_manifest.json", manifest_bytes)
    if (destination / "fault_copy_record.json").read_bytes() != record_bytes or (destination / "fault_copy_manifest.json").read_bytes() != manifest_bytes:
        raise FaultMutationError("runtime record or manifest bytes did not round-trip")
    if _file_map_sha256(_read_tree_files(destination / "artifact")) != mutated_sha256:
        raise FaultMutationError("fault-copy artifact changed after manifest write")
    return record


def _make_runtime_record(case_id: str, artifact_kind: str, artifact_id: str, source_sha256: str, mutated_sha256: str, copy_location: str, manifest_identity: str) -> FaultCopyRecord:
    unsigned = FaultCopyRecord(
        "", case_id, artifact_kind, artifact_id, source_sha256,
        mutated_sha256, copy_location, manifest_identity,
    )
    record = replace(unsigned, fault_copy_id="fault-copy-" + canonical_sha256(unsigned.to_payload())[:20])
    record.validate()
    return record


def _sync_render_manifest_hashes(files: Mapping[str, bytes]) -> dict[str, bytes]:
    result = dict(files)
    manifest_key = "render/render_manifest.json"
    manifest = _load_json_object(result[manifest_key], manifest_key)
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise FaultMutationError("render manifest files must be a list")
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise FaultMutationError("render manifest file entry is invalid")
        file_key = "render/" + item["name"]
        if file_key not in result:
            raise FaultMutationError("render manifest references a missing copied file")
        item["sha256"] = sha256(result[file_key]).hexdigest()
    result[manifest_key] = canonical_json_bytes(manifest)
    return dict(sorted(result.items()))


def _validated_render_files(result: RenderResult) -> dict[str, bytes]:
    root = result.output_dir
    expected = {
        "index.html": result.index_html, "styles.css": result.styles_css,
        "app.js": result.app_js, "render_manifest.json": result.render_manifest,
    }
    if root.is_symlink() or not root.is_dir() or any(item != root / name for name, item in expected.items()):
        raise FaultMutationError("RenderResult must point to a real direct-child render directory")
    actual = _read_tree_files(root)
    if set(actual) != set(expected):
        raise FaultMutationError("render artifact has an unexpected file set")
    manifest = _load_json_object(actual["render_manifest.json"], "render_manifest.json")
    if manifest.get("page_id") != result.page_id or not isinstance(manifest.get("files"), list):
        raise FaultMutationError("render manifest does not match RenderResult")
    entries = {item.get("name"): item.get("sha256") for item in manifest["files"] if isinstance(item, dict) and set(item) == {"name", "sha256"}}
    if set(entries) != {"index.html", "styles.css", "app.js"}:
        raise FaultMutationError("render manifest has an unexpected file declaration set")
    for name in ("index.html", "styles.css", "app.js"):
        if entries[name] != sha256(actual[name]).hexdigest():
            raise FaultMutationError("render source manifest hash does not match source bytes")
    return {"render/" + name: actual[name] for name in sorted(actual)}


def _read_tree_files(root: Path, *, prefix: str = "") -> dict[str, bytes]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise FaultMutationError("source artifact root must be a real directory")
    result: dict[str, bytes] = {}
    for current, directories, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        if any((current_path / name).is_symlink() for name in directories):
            raise FaultMutationError("symlinked directories are not allowed in source artifacts")
        for name in sorted(file_names):
            file_path = current_path / name
            if file_path.is_symlink() or not file_path.is_file():
                raise FaultMutationError("symlinked or non-regular source files are not allowed")
            relative = file_path.relative_to(root).as_posix()
            key = prefix + "/" + relative if prefix else relative
            _safe_relative_posix(key, "source artifact path")
            result[key] = file_path.read_bytes()
    if not result:
        raise FaultMutationError("source artifact directory must not be empty")
    return dict(sorted(result.items()))


def _validated_file_map(files: Mapping[str, bytes]) -> dict[str, bytes]:
    if not files:
        raise FaultMutationError("fault-copy artifact file map must not be empty")
    result: dict[str, bytes] = {}
    for name, content in files.items():
        _safe_relative_posix(name, "artifact path")
        if not isinstance(content, bytes):
            raise FaultMutationError("fault-copy artifact content must be bytes")
        result[name] = content
    return dict(sorted(result.items()))


def _write_file_map(root: Path, files: Mapping[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        target = root.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_exact_bytes(target, content)


def _write_exact_bytes(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise FaultMutationError("fixed fault-copy output files must not be overwritten")
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise FaultMutationError("written bytes did not round-trip: " + path.name)


def _prepare_empty_output_directory(output_dir: Path) -> Path:
    destination = Path(output_dir).expanduser()
    _reject_symlink_ancestors(destination)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise FaultMutationError("fault-copy output_dir must be a real directory path")
        if any(destination.iterdir()):
            raise FaultMutationError("fault-copy output_dir must be empty; refusing to overwrite")
    else:
        destination.mkdir(parents=True, exist_ok=False)
    return destination


def _reject_symlink_ancestors(path: Path) -> None:
    absolute = path.absolute()
    for candidate in (absolute, *absolute.parents):
        if candidate.exists() and candidate.is_symlink():
            raise FaultMutationError("symlinked output paths are not allowed")


def _file_entries(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    return [{"path": name, "size": len(content), "sha256": sha256(content).hexdigest()} for name, content in sorted(files.items())]


def _file_map_sha256(files: Mapping[str, bytes]) -> str:
    return canonical_sha256({"files": _file_entries(files)})


def _load_json_object(content: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FaultMutationError(name + " must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise FaultMutationError(name + " must be a JSON object")
    return value


def _safe_relative_posix(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise FaultMutationError(field_name + " must be a non-empty relative path")
    if "\\" in value:
        raise FaultMutationError(field_name + " must use safe POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise FaultMutationError(field_name + " must not traverse or escape its root")


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise FaultMutationError(field_name + " must be non-empty text")


def _require_sha256(value: object, field_name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(item not in "0123456789abcdef" for item in value):
        raise FaultMutationError(field_name + " must be a lowercase SHA-256")
