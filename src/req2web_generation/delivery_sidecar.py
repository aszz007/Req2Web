from __future__ import annotations

"""Deterministic user-facing delivery sidecars for validated v2 packages.

The sidecar deliberately consumes already-published package files.  It does
not rerun generation, retrieval, rendering, or either quality gate.
"""

import hashlib
import html
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .result_package_v2 import RetrievalEnhancedResultPackage, RetrievalEnhancedResultPackageError


DELIVERY_REFERENCES_SCHEMA_VERSION = "req2web.delivery.references.v1"
DELIVERY_STORYBOARD_SCHEMA_VERSION = "req2web.delivery.storyboard.v1"
DELIVERY_SIDECAR_SCHEMA_VERSION = "req2web.delivery.sidecar.v1"

_MANIFEST_NAME = "delivery_manifest.json"
_BASE_FILES = ("index.html", "styles.css", "ui_references.json", "storyboard.json")
_IMAGE_EXTENSIONS = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"})
_PACKAGE_FILES = {
    "internal/agent_context.json",
    "internal/page_spec.json",
    "internal/consistency_report.json",
    "internal/retrieval_influence_report.json",
    "package_manifest.json",
}


class DeliverySidecarError(ValueError):
    """Raised when a sidecar input, output, or delivery contract is invalid."""


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliverySidecarError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DeliverySidecarError(f"{name} must be a JSON object")
    return value


def _safe_posix_path(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DeliverySidecarError(f"{name} must be a non-empty relative POSIX path")
    if "\\" in value or value.startswith("/") or ":" in value or "://" in value:
        raise DeliverySidecarError(f"{name} must not be absolute, external, or platform-specific")
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise DeliverySidecarError(f"{name} contains an unsafe path segment")
    return value


def _safe_output_path(value: object, name: str) -> str:
    return _safe_posix_path(value, name)


def _slug(value: str) -> str:
    return "".join(char if char.isascii() and char.isalnum() else "-" for char in value).strip("-") or "reference"


def _has_absolute_path(value: object) -> bool:
    if isinstance(value, str):
        return value.startswith("/") or value.startswith("\\") or (len(value) >= 3 and value[1] == ":" and value[2] in "\\/")
    if isinstance(value, list):
        return any(_has_absolute_path(item) for item in value)
    if isinstance(value, dict):
        return any(_has_absolute_path(item) for item in value.values())
    return False


def _state_scenario(name: str) -> str:
    lowered = name.lower()
    if lowered in {"initial", "success", "loading"}:
        return "normal"
    if "error" in lowered or "failure" in lowered or "denied" in lowered:
        return "error_entry"
    if "recover" in lowered or "retry" in lowered:
        return "recovery"
    return "other"


def _interaction_scenario(interaction: dict[str, Any], states: dict[str, dict[str, Any]]) -> str:
    action = str(interaction["action"]).lower()
    target = states[interaction["target_state_id"]]["name"].lower()
    if (
        "recover" in action
        or "retry" in action
        or "return" in action
        or "\u6062\u590d" in action
        or "\u91cd\u8bd5" in action
        or "\u8fd4\u56de" in action
    ):
        return "recovery"
    if "error" in target or "failure" in action or "simulate" in action and "error" in action:
        return "error_entry"
    return _state_scenario(target)


def _require_fields(value: object, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise DeliverySidecarError(f"{name} fields are invalid")
    return value


@dataclass(frozen=True)
class DeliverySidecarResult:
    sidecar_id: str
    package_id: str
    page_id: str
    output_dir: Path
    reference_count: int
    packaged_image_count: int
    reference_only_count: int
    use_case_count: int
    scenario_count: int
    step_count: int
    schema_version: str = DELIVERY_SIDECAR_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != DELIVERY_SIDECAR_SCHEMA_VERSION:
            raise DeliverySidecarError("unsupported delivery sidecar schema")
        root = Path(self.output_dir)
        manifest = _read_json(root / _MANIFEST_NAME, _MANIFEST_NAME)
        _require_fields(manifest, {"schema_version", "sidecar_id", "package_id", "page_id", "files"}, _MANIFEST_NAME)
        if manifest["schema_version"] != DELIVERY_SIDECAR_SCHEMA_VERSION:
            raise DeliverySidecarError("delivery manifest schema is invalid")
        if (manifest["sidecar_id"], manifest["package_id"], manifest["page_id"]) != (self.sidecar_id, self.package_id, self.page_id):
            raise DeliverySidecarError("delivery manifest identity is inconsistent")
        entries = manifest["files"]
        if not isinstance(entries, list):
            raise DeliverySidecarError("delivery manifest files must be a list")
        paths: list[str] = []
        for entry in entries:
            _require_fields(entry, {"path", "size", "sha256"}, "delivery manifest entry")
            path = _safe_output_path(entry["path"], "delivery manifest path")
            if path == _MANIFEST_NAME:
                raise DeliverySidecarError("delivery manifest must not self-reference")
            file_path = root.joinpath(*PurePosixPath(path).parts)
            if not file_path.is_file():
                raise DeliverySidecarError(f"declared delivery file is missing: {path}")
            content = file_path.read_bytes()
            if not isinstance(entry["size"], int) or entry["size"] != len(content) or entry["sha256"] != _sha256(content):
                raise DeliverySidecarError(f"delivery manifest hash is invalid: {path}")
            paths.append(path)
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise DeliverySidecarError("delivery manifest paths must be sorted and unique")
        actual = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
        if actual != sorted([*paths, _MANIFEST_NAME]):
            raise DeliverySidecarError("delivery sidecar contains undeclared or missing files")
        if not set(_BASE_FILES).issubset(paths):
            raise DeliverySidecarError("delivery sidecar fixed files are missing")
        references = _read_json(root / "ui_references.json", "ui_references.json")
        storyboard = _read_json(root / "storyboard.json", "storyboard.json")
        _require_fields(references, {"schema_version", "sidecar_id", "package_id", "page_id", "references", "source_statement"}, "ui references")
        _require_fields(storyboard, {"schema_version", "sidecar_id", "package_id", "page_id", "semantics", "use_cases"}, "storyboard")
        if references["schema_version"] != DELIVERY_REFERENCES_SCHEMA_VERSION or storyboard["schema_version"] != DELIVERY_STORYBOARD_SCHEMA_VERSION:
            raise DeliverySidecarError("delivery JSON schema is invalid")
        if any(value.get("sidecar_id") != self.sidecar_id or value.get("package_id") != self.package_id or value.get("page_id") != self.page_id for value in (references, storyboard)):
            raise DeliverySidecarError("delivery JSON identity is inconsistent")
        if references["source_statement"] != (
            "Reference images come from retrieval data and are design "
            "references only; they are not generated-page screenshots or "
            "visual understanding evidence."
        ):
            raise DeliverySidecarError("reference source statement is invalid")
        if storyboard["semantics"].get("scenario_boards_not_single_global_journey") is not True:
            raise DeliverySidecarError("storyboard semantics declaration is missing")
        if _has_absolute_path(references) or _has_absolute_path(storyboard):
            raise DeliverySidecarError("delivery JSON must not expose absolute paths")
        html_text = (root / "index.html").read_text(encoding="utf-8")
        if "innerHTML" in html_text or "http://" in html_text or "https://" in html_text or "<script" in html_text.lower():
            raise DeliverySidecarError("delivery HTML violates offline rendering boundary")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "sidecar_id": self.sidecar_id,
            "package_id": self.package_id,
            "page_id": self.page_id,
            "output_dir": ".",
            "reference_count": self.reference_count,
            "packaged_image_count": self.packaged_image_count,
            "reference_only_count": self.reference_only_count,
            "use_case_count": self.use_case_count,
            "scenario_count": self.scenario_count,
            "step_count": self.step_count,
        }


class DeterministicDeliverySidecarBuilder:
    """Build a user-facing sidecar from one already validated v2 package."""

    def build(self, package_dir: Path, output_dir: Path, reference_root: Path | None = None) -> DeliverySidecarResult:
        package_root = Path(package_dir).resolve(strict=False)
        output = Path(output_dir).resolve(strict=False)
        self._validate_destination(output)
        package_id, page_id, context, page_spec = self._load_validated_input(package_root)
        reference_base = self._resolve_reference_root(reference_root)
        references, assets = self._build_references(package_id, page_id, context, page_spec, reference_base)
        storyboard = self._build_storyboard(package_id, page_id, page_spec)
        sidecar_id = self._sidecar_id(package_id, page_id, references, storyboard)
        reference_payload = {
            "schema_version": DELIVERY_REFERENCES_SCHEMA_VERSION,
            "sidecar_id": sidecar_id,
            "package_id": package_id,
            "page_id": page_id,
            "source_statement": (
                "Reference images come from retrieval data and are design "
                "references only; they are not generated-page screenshots or "
                "visual understanding evidence."
            ),
            "references": references,
        }
        storyboard_payload = {"schema_version": DELIVERY_STORYBOARD_SCHEMA_VERSION, "sidecar_id": sidecar_id, "package_id": package_id, "page_id": page_id, **storyboard}
        files: dict[str, bytes] = {
            "ui_references.json": _json_bytes(reference_payload),
            "storyboard.json": _json_bytes(storyboard_payload),
            "styles.css": self._styles().encode("utf-8"),
        }
        files["index.html"] = self._html(reference_payload, storyboard_payload).encode("utf-8")
        files.update(assets)
        if _has_absolute_path(reference_payload) or _has_absolute_path(storyboard_payload):
            raise DeliverySidecarError("delivery output would expose an absolute path")
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = output.parent / f".{output.name}.staging-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            for path, content in sorted(files.items()):
                destination = staging.joinpath(*PurePosixPath(path).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            self._verify_asset_sources(assets, reference_base, references)
            manifest = {
                "schema_version": DELIVERY_SIDECAR_SCHEMA_VERSION,
                "sidecar_id": sidecar_id,
                "package_id": package_id,
                "page_id": page_id,
                "files": [{"path": path, "size": len(files[path]), "sha256": _sha256(files[path])} for path in sorted(files)],
            }
            (staging / _MANIFEST_NAME).write_bytes(_json_bytes(manifest))
            result = self._result_from_payload(sidecar_id, package_id, page_id, staging, references, storyboard_payload)
            result.validate()
            if output.exists():
                if output.is_symlink() or not output.is_dir() or any(output.iterdir()):
                    raise DeliverySidecarError("output_dir already exists and is not empty; refusing to overwrite")
                output.rmdir()
            os.replace(staging, output)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self._result_from_payload(sidecar_id, package_id, page_id, output, references, storyboard_payload)

    @staticmethod
    def _validate_destination(output: Path) -> None:
        if output.exists() and (output.is_symlink() or not output.is_dir() or any(output.iterdir())):
            raise DeliverySidecarError("output_dir already exists and is not empty; refusing to overwrite")

    @staticmethod
    def _resolve_reference_root(reference_root: Path | None) -> Path | None:
        if reference_root is None:
            return None
        root = Path(reference_root)
        if not root.is_dir() or root.is_symlink():
            raise DeliverySidecarError("reference_root must be an existing non-symlink directory")
        return root.resolve(strict=True)

    @staticmethod
    def _load_validated_input(root: Path) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        if not root.is_dir():
            raise DeliverySidecarError("package_dir must be an existing v2 package directory")
        manifest = _read_json(root / "package_manifest.json", "package_manifest.json")
        package_id, page_id = manifest.get("package_id"), manifest.get("page_id")
        if not isinstance(package_id, str) or not isinstance(page_id, str):
            raise DeliverySidecarError("package manifest identity is invalid")
        try:
            RetrievalEnhancedResultPackage(package_id, page_id, root).validate()
        except (RetrievalEnhancedResultPackageError, ValueError) as exc:
            raise DeliverySidecarError(f"v2 package did not pass its double-gate disk validation: {exc}") from exc
        if not _PACKAGE_FILES.issubset({path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}):
            raise DeliverySidecarError("v2 package does not contain required internal files")
        context = _read_json(root / "internal/agent_context.json", "agent_context.json")
        page_spec = _read_json(root / "internal/page_spec.json", "page_spec.json")
        if context.get("schema_version") != "req2web.agent.context.v1" or page_spec.get("schema_version") != "req2web.page_spec.v1":
            raise DeliverySidecarError("v2 package contains an unsupported upstream contract")
        return package_id, page_id, context, page_spec

    def _build_references(self, package_id: str, page_id: str, context: dict[str, Any], page_spec: dict[str, Any], reference_root: Path | None) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
        retrieval = context.get("retrieval_results")
        if not isinstance(retrieval, dict) or not isinstance(retrieval.get("ui_reference"), list):
            raise DeliverySidecarError("agent context has no UI reference retrieval results")
        evidence = page_spec.get("traceability", {}).get("evidence")
        if not isinstance(evidence, list):
            raise DeliverySidecarError("PageSpec traceability evidence is invalid")
        page_evidence = {item.get("doc_id"): item for item in evidence if isinstance(item, dict) and item.get("role") == "ui_reference"}
        records: list[dict[str, Any]] = []
        assets: dict[str, bytes] = {}
        copied_by_uri: dict[str, str] = {}
        for parent in retrieval["ui_reference"]:
            if not isinstance(parent, dict):
                raise DeliverySidecarError("UI reference retrieval entry is invalid")
            doc_id, title = parent.get("doc_id"), parent.get("title")
            if not isinstance(doc_id, str) or not doc_id or not isinstance(title, str) or not title:
                raise DeliverySidecarError("UI reference parent identity is invalid")
            trace = page_evidence.get(doc_id)
            if not isinstance(trace, dict) or trace.get("title") != title:
                raise DeliverySidecarError(f"PageSpec UI reference parent doc is missing or tampered: {doc_id}")
            references = parent.get("references")
            if not isinstance(references, list) or not references:
                raise DeliverySidecarError(f"UI reference {doc_id} has no declared references")
            trace_uris = trace.get("reference_uris")
            parent_uris = [item.get("uri") for item in references if isinstance(item, dict)]
            if not isinstance(trace_uris, list) or trace_uris != parent_uris[:len(trace_uris)]:
                raise DeliverySidecarError(f"PageSpec UI reference URI linkage is invalid: {doc_id}")
            for source in references:
                if not isinstance(source, dict) or set(source) != {"kind", "uri"}:
                    raise DeliverySidecarError(f"UI reference {doc_id} has malformed declared reference")
                kind, uri = source["kind"], source["uri"]
                if not isinstance(kind, str) or not kind:
                    raise DeliverySidecarError(f"UI reference {doc_id} kind is invalid")
                uri = _safe_posix_path(uri, f"UI reference URI for {doc_id}")
                record = {
                    "doc_id": doc_id,
                    "title": title,
                    "original_uri": uri,
                    "reference_kind": kind,
                    "delivery_mode": "reference_only",
                        "source_note": (
                            "Read from agent_context.json in the validated v2 "
                            "package; hierarchy and annotation content was not "
                            "read or interpreted."
                        ),
                    "local_file": None,
                }
                if kind in {"screenshot", "semantic_image"} and PurePosixPath(uri).suffix.lower() in _IMAGE_EXTENSIONS and reference_root is not None:
                    try:
                        source_path = self._safe_asset_path(reference_root, uri)
                    except DeliverySidecarError as exc:
                        if "is missing" in str(exc):
                            records.append(record)
                            continue
                        raise
                    first = source_path.read_bytes()
                    digest = _sha256(first)
                    local_path = copied_by_uri.get(uri)
                    if local_path is None:
                        local_path = f"assets/{_slug(doc_id)}-{_slug(kind)}-{digest[:16]}{PurePosixPath(uri).suffix.lower()}"
                        copied_by_uri[uri] = local_path
                        assets[local_path] = first
                    record["delivery_mode"] = "packaged_image"
                    record["local_file"] = local_path
                    record["source_note"] = (
                        "Read from a retrieval reference declared by the "
                        "validated v2 package; it is a design reference only "
                        "and is not generated-page screenshot or visual "
                        "understanding evidence."
                    )
                records.append(record)
        if not records:
            raise DeliverySidecarError("delivery sidecar requires at least one declared UI reference")
        return records, assets

    @staticmethod
    def _safe_asset_path(reference_root: Path, uri: str) -> Path:
        candidate = reference_root.joinpath(*PurePosixPath(uri).parts)
        current = reference_root
        for part in PurePosixPath(uri).parts:
            current = current / part
            if current.is_symlink():
                raise DeliverySidecarError("declared UI reference uses a symlink and is refused")
        if not candidate.is_file():
            raise DeliverySidecarError(f"declared UI reference image is missing: {uri}")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(reference_root)
        except (OSError, ValueError) as exc:
            raise DeliverySidecarError("declared UI reference escapes reference_root") from exc
        return candidate

    @staticmethod
    def _verify_asset_sources(assets: dict[str, bytes], reference_root: Path | None, references: list[dict[str, Any]]) -> None:
        if not assets or reference_root is None:
            return
        expected = {item["local_file"]: item for item in references if item["delivery_mode"] == "packaged_image"}
        for local_path, initial in assets.items():
            source = expected[local_path]
            candidate = DeterministicDeliverySidecarBuilder._safe_asset_path(reference_root, source["original_uri"])
            if candidate.read_bytes() != initial:
                raise DeliverySidecarError("declared UI reference changed while staging; refusing to publish")

    @staticmethod
    def _build_storyboard(package_id: str, page_id: str, page_spec: dict[str, Any]) -> dict[str, Any]:
        required = {"use_cases", "states", "components", "interactions"}
        if not required.issubset(page_spec):
            raise DeliverySidecarError("PageSpec lacks storyboard fields")
        use_cases = page_spec["use_cases"]
        states = {item.get("state_id"): item for item in page_spec["states"] if isinstance(item, dict)}
        components = {item.get("component_id"): item for item in page_spec["components"] if isinstance(item, dict)}
        interactions = page_spec["interactions"]
        if not isinstance(use_cases, list) or not states or not components or not isinstance(interactions, list):
            raise DeliverySidecarError("PageSpec storyboard fields are malformed")
        boards = []
        for use_case in use_cases:
            if not isinstance(use_case, dict) or not isinstance(use_case.get("use_case_id"), str):
                raise DeliverySidecarError("PageSpec use case is malformed")
            use_case_id = use_case["use_case_id"]
            panels = []
            for interaction in interactions:
                if not isinstance(interaction, dict) or use_case_id not in interaction.get("use_case_ids", []):
                    continue
                keys = {"interaction_id", "trigger_component_id", "source_state_id", "target_state_id", "action", "user_feedback", "use_case_ids"}
                if not keys.issubset(interaction):
                    raise DeliverySidecarError("PageSpec interaction is malformed")
                component = components.get(interaction["trigger_component_id"])
                source, target = states.get(interaction["source_state_id"]), states.get(interaction["target_state_id"])
                if not isinstance(component, dict) or not isinstance(source, dict) or not isinstance(target, dict):
                    raise DeliverySidecarError("PageSpec interaction references are broken")
                panels.append({
                    "interaction_id": interaction["interaction_id"],
                    "source_state": {"state_id": interaction["source_state_id"], "name": source.get("name"), "description": source.get("description")},
                    "target_state": {"state_id": interaction["target_state_id"], "name": target.get("name"), "description": target.get("description")},
                    "trigger_component": {"component_id": interaction["trigger_component_id"], "label": component.get("label"), "component_type": component.get("component_type")},
                    "action": interaction["action"],
                    "feedback": interaction["user_feedback"],
                    "use_case_ids": list(interaction["use_case_ids"]),
                    "scenario": _interaction_scenario(interaction, states),
                })
            boards.append({"use_case_id": use_case_id, "title": use_case.get("title"), "actor": use_case.get("actor"), "goal": use_case.get("goal"), "expected_outcome": use_case.get("expected_outcome"), "scenarios": DeterministicDeliverySidecarBuilder._scenario_boards(panels)})
        return {"semantics": {"scenario_boards_not_single_global_journey": True, "ordering_rule": "only interactions connected by source_state_id to target_state_id are sequenced; unconnected items are independent branches", "visual_boundary": "Panels are deterministic text/state descriptions from PageSpec, not generated screenshots or visual understanding."}, "use_cases": boards}

    @staticmethod
    def _scenario_boards(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for panel in panels:
            groups.setdefault(panel["scenario"], []).append(panel)
        boards = []
        for scenario in ("normal", "error_entry", "recovery", "other"):
            group = groups.get(scenario, [])
            if not group:
                continue
            remaining = {panel["interaction_id"]: panel for panel in group}
            ordered: list[dict[str, Any]] = []
            independent: list[dict[str, Any]] = []
            while remaining:
                starts = [item for item in remaining.values() if not any(other["target_state"]["state_id"] == item["source_state"]["state_id"] for other in remaining.values() if other is not item)]
                current = sorted(starts or remaining.values(), key=lambda item: item["interaction_id"])[0]
                chain = [current]; remaining.pop(current["interaction_id"])
                while True:
                    next_items = sorted((item for item in remaining.values() if item["source_state"]["state_id"] == chain[-1]["target_state"]["state_id"]), key=lambda item: item["interaction_id"])
                    if len(next_items) != 1:
                        break
                    current = next_items[0]; chain.append(current); remaining.pop(current["interaction_id"])
                if len(chain) == 1 and any(item["source_state"]["state_id"] == chain[0]["target_state"]["state_id"] or item["target_state"]["state_id"] == chain[0]["source_state"]["state_id"] for item in group if item is not chain[0]):
                    independent.extend(chain)
                else:
                    ordered.extend(chain)
            boards.append({"scenario": scenario, "sequence": ordered, "independent_branches": sorted(independent, key=lambda item: item["interaction_id"])})
        return boards

    @staticmethod
    def _sidecar_id(package_id: str, page_id: str, references: list[dict[str, Any]], storyboard: dict[str, Any]) -> str:
        encoded = json.dumps({"package_id": package_id, "page_id": page_id, "references": references, "storyboard": storyboard}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "delivery-sidecar-" + _sha256(encoded)[:20]

    @staticmethod
    def _result_from_payload(sidecar_id: str, package_id: str, page_id: str, output: Path, references: list[dict[str, Any]], storyboard: dict[str, Any]) -> DeliverySidecarResult:
        scenarios = [scenario for board in storyboard["use_cases"] for scenario in board["scenarios"]]
        steps = [item for scenario in scenarios for group in (scenario["sequence"], scenario["independent_branches"]) for item in group]
        return DeliverySidecarResult(sidecar_id, package_id, page_id, output, len(references), sum(item["delivery_mode"] == "packaged_image" for item in references), sum(item["delivery_mode"] == "reference_only" for item in references), len(storyboard["use_cases"]), len(scenarios), len(steps))

    @staticmethod
    def _styles() -> str:
        return """*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#172033;font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1120px;margin:0 auto;padding:24px}header,.card{background:#fff;border:1px solid #dce3f0;border-radius:14px;padding:20px;margin:0 0 18px}.card p{overflow-wrap:anywhere}.notice{border-left:4px solid #4068d8;background:#edf2ff;padding:12px}.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}.reference img{display:block;max-width:100%;height:auto;border:1px solid #dce3f0;border-radius:8px;margin-top:10px}.badge{display:inline-block;border-radius:999px;padding:2px 9px;background:#e8eefc;color:#27469f;font-size:.85rem}.reference-only{background:#fff8e8;border-left:4px solid #c88a12;padding:10px}.panel{border:1px solid #dce3f0;border-radius:10px;padding:14px;margin:10px 0}.state{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.88rem}@media(max-width:480px){main{padding:14px}header,.card{padding:15px}.grid{grid-template-columns:minmax(0,1fr)}}"""

    @staticmethod
    def _html(references: dict[str, Any], storyboard: dict[str, Any]) -> str:
        esc = lambda value: html.escape(str(value), quote=True)
        ref_cards = []
        for item in references["references"]:
            image = f'<img src="{esc(item["local_file"])}" alt="{esc(item["title"])}" loading="lazy">' if item["delivery_mode"] == "packaged_image" else '<p class="reference-only">reference_only: image not packaged; original reference metadata is retained.</p>'
            ref_cards.append(f'<article class="card reference"><h3>{esc(item["title"])}</h3><p><span class="badge">{esc(item["delivery_mode"])}</span> <span class="badge">{esc(item["reference_kind"])}</span></p><p>doc_id: {esc(item["doc_id"])}</p><p>Original URI: {esc(item["original_uri"])}</p><p>{esc(item["source_note"])}</p>{image}</article>')
        use_case_cards = []
        for board in storyboard["use_cases"]:
            scenario_blocks = []
            for scenario in board["scenarios"]:
                panels = []
                for label, entries in (
                    ("Connected sequence", scenario["sequence"]),
                    ("Independent branch", scenario["independent_branches"]),
                ):
                    for item in entries:
                        panels.append(f'<div class="panel"><p><span class="badge">{esc(scenario["scenario"])}</span> <span class="badge">{esc(label)}</span></p><p><strong>{esc(item["interaction_id"])}</strong></p><p class="state">{esc(item["source_state"]["state_id"])} -&gt; {esc(item["target_state"]["state_id"])}</p><p>Trigger component: {esc(item["trigger_component"]["label"])} ({esc(item["trigger_component"]["component_id"])})</p><p>Action: {esc(item["action"])}</p><p>Feedback: {esc(item["feedback"])}</p><p>Related use cases: {esc(", ".join(item["use_case_ids"]))}</p></div>')
                scenario_blocks.append(f'<section><h3>{esc(scenario["scenario"])}</h3>{"".join(panels) or "<p>No interaction panels.</p>"}</section>')
            use_case_cards.append(f'<article class="card"><h2>{esc(board["title"])}</h2><p>Use case: {esc(board["use_case_id"])}; Goal: {esc(board["goal"])}</p>{"".join(scenario_blocks)}</article>')
        return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Req2Web delivery sidecar</title><link rel="stylesheet" href="styles.css"></head><body><main><header><h1>UI references and interaction storyboard</h1><p>{esc(references["source_statement"])}</p><p>The storyboard is grouped by use case and scenario; it is not one complete global user journey.</p></header><section><h2>UI references</h2><div class="grid">{"".join(ref_cards)}</div></section><section><h2>Storyboard</h2>{"".join(use_case_cards)}</section></main></body></html>'


def build_delivery_sidecar_batch(packages_dir: Path, output_root: Path, reference_root: Path | None = None, expected_count: int | None = 12) -> dict[str, Any]:
    """Build independently versioned sidecars and one aggregate report/manifest."""
    source = Path(packages_dir)
    output = Path(output_root).resolve(strict=False)
    if not source.is_dir():
        raise DeliverySidecarError("packages_dir must be an existing directory")
    if output.exists() and (output.is_symlink() or not output.is_dir() or any(output.iterdir())):
        raise DeliverySidecarError("output_root already exists and is not empty; refusing to overwrite")
    packages = sorted((path for path in source.iterdir() if path.is_dir()), key=lambda path: path.name)
    if expected_count is not None and len(packages) != expected_count:
        raise DeliverySidecarError(f"packages_dir must contain exactly {expected_count} package directories")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        records = []
        for package in packages:
            result = DeterministicDeliverySidecarBuilder().build(package, staging / "sidecars" / package.name, reference_root)
            records.append({"case_id": package.name, **result.to_dict(), "delivery_manifest_sha256": _sha256((staging / "sidecars" / package.name / _MANIFEST_NAME).read_bytes())})
        report = {"schema_version": "req2web.delivery.sidecar.aggregate.report.v1", "package_count": len(records), "sidecars": records, "aggregate": {"packaged_image": sum(item["packaged_image_count"] for item in records), "reference_only": sum(item["reference_only_count"] for item in records), "use_cases": sum(item["use_case_count"] for item in records), "scenarios": sum(item["scenario_count"] for item in records), "steps": sum(item["step_count"] for item in records)}}
        report_bytes = _json_bytes(report)
        (staging / "aggregate_report.json").write_bytes(report_bytes)
        aggregate = {"schema_version": "req2web.delivery.sidecar.aggregate.manifest.v1", "package_count": len(records), "files": [{"path": f"sidecars/{item['case_id']}/{_MANIFEST_NAME}", "sha256": item["delivery_manifest_sha256"]} for item in records] + [{"path": "aggregate_report.json", "sha256": _sha256(report_bytes)}]}
        (staging / "aggregate_manifest.json").write_bytes(_json_bytes(aggregate))
        if output.exists():
            output.rmdir()
        os.replace(staging, output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return report
