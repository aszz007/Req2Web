from __future__ import annotations

"""Verified, path-free references to materialized deterministic G0 v2 packages.

This module never builds, copies, routes, or falls back to a package.  It
records only a byte/hash snapshot of an already materialized package after the
existing v2 validator and explicit AgentContext/RetrievalGuidance rebinding
have succeeded.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Iterable

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle
from req2web_generation import (
    RESULT_PACKAGE_V2_SCHEMA_VERSION,
    RETRIEVAL_GUIDANCE_SCHEMA_VERSION,
    RetrievalEnhancedResultPackage,
    RetrievalGuidance,
)


FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION = "req2web.evaluation.frozen_g0_package_reference.v1"

_STAGE = "package"
_PHASE = "g0_freeze_reference"

_CONTENT_ROLES = {
    "internal/agent_context.json": "agent_context",
    "internal/retrieval_guidance.json": "retrieval_guidance",
    "internal/guided_page_spec_build_result.json": "guided_page_spec_build_result",
    "internal/page_spec.json": "guided_page_spec",
    "internal/consistency_report.json": "consistency_report",
    "internal/retrieval_influence_report.json": "retrieval_influence_report",
    "page/index.html": "browser_entrypoint",
    "page/styles.css": "page_styles",
    "page/app.js": "page_script",
    "page/render_manifest.json": "render_manifest",
    "result_summary.json": "result_summary",
}
_INVENTORY_ROLES = {"package_manifest.json": "package_manifest", **_CONTENT_ROLES}
_INVENTORY_PATHS = tuple(sorted(_INVENTORY_ROLES))
_FIXED_DECLARATION_ITEMS: tuple[tuple[str, str | bool], ...] = (
    ("baseline_kind", "deterministic_guided_g0"),
    ("retrieval_influence", "v1_only"),
    ("result_package", "v2_only"),
    ("package_copied", False),
    ("fallback_materialized", False),
    ("delivery_route", "not_executed"),
    ("provider_invocation_status", "not_executed"),
    ("unguided_builder_used", False),
)

_ERROR_MESSAGES = {
    "context_identity_invalid": "frozen G0 reference context identity is invalid",
    "package_identity_invalid": "frozen G0 reference package identity is invalid",
    "page_identity_invalid": "frozen G0 reference page identity is invalid",
    "guidance_identity_invalid": "frozen G0 reference guidance identity is invalid",
    "context_sha256_invalid": "frozen G0 reference context hash is invalid",
    "guidance_sha256_invalid": "frozen G0 reference guidance hash is invalid",
    "manifest_sha256_invalid": "frozen G0 reference manifest hash is invalid",
    "context_inventory_binding_invalid": "frozen G0 reference context binding is invalid",
    "context_package_binding_mismatch": "frozen G0 reference context binding is invalid",
    "context_schema_invalid": "frozen G0 reference schema is invalid",
    "guidance_inventory_binding_invalid": "frozen G0 reference guidance binding is invalid",
    "guidance_package_binding_mismatch": "frozen G0 reference guidance binding is invalid",
    "guidance_schema_invalid": "frozen G0 reference schema is invalid",
    "inventory_invalid": "frozen G0 reference inventory is invalid",
    "inventory_order_invalid": "frozen G0 reference inventory is invalid",
    "inventory_path_invalid": "frozen G0 reference inventory is invalid",
    "inventory_role_invalid": "frozen G0 reference inventory is invalid",
    "inventory_sha256_invalid": "frozen G0 reference inventory is invalid",
    "inventory_size_invalid": "frozen G0 reference inventory is invalid",
    "live_artifact_type_invalid": "frozen G0 reference input is invalid",
    "live_package_reference_mismatch": "frozen G0 reference live binding is invalid",
    "package_inventory_binding_mismatch": "frozen G0 reference package binding is invalid",
    "package_manifest_inventory_binding_invalid": "frozen G0 reference manifest binding is invalid",
    "package_or_live_artifact_validation_failed": "frozen G0 reference input cannot be verified",
    "package_schema_invalid": "frozen G0 reference schema is invalid",
    "package_tree_invalid": "frozen G0 reference package tree is invalid",
    "package_tree_inventory_mismatch": "frozen G0 reference package tree is invalid",
    "package_tree_link_forbidden": "frozen G0 reference package tree is invalid",
    "package_tree_unreadable": "frozen G0 reference package tree cannot be verified",
    "package_type_invalid": "frozen G0 reference input is invalid",
    "reference_identity_invalid": "frozen G0 reference identity is invalid",
    "reference_schema_invalid": "frozen G0 reference schema is invalid",
    "tree_sha256_invalid": "frozen G0 reference inventory is invalid",
}


def _declarations_dict() -> dict[str, str | bool]:
    return dict(_FIXED_DECLARATION_ITEMS)


class FrozenG0PackageReferenceError(ValueError):
    """A fixed safe error envelope with no package paths or content."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_MESSAGES:
            raise ValueError("unsupported frozen G0 package reference error code")
        self.code = code
        self.stage = _STAGE
        self.phase = _PHASE
        super().__init__(_ERROR_MESSAGES[code])


def _error(code: str) -> FrozenG0PackageReferenceError:
    return FrozenG0PackageReferenceError(code)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _package_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(code)
    return value


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise _error("inventory_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix() or value == ".":
        raise _error("inventory_path_invalid")
    return value


def _is_reparse_or_link(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise _error("package_tree_unreadable") from exc
    if stat.S_ISLNK(mode):
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _safe_package_path(root: Path, relative_path: str) -> Path:
    path = root.joinpath(*PurePosixPath(relative_path).parts)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise _error("inventory_path_invalid") from exc
    return path


def _walk_package_paths(root: Path) -> list[str]:
    if _is_reparse_or_link(root) or not root.is_dir():
        raise _error("package_tree_invalid")
    found: list[str] = []
    expected_directories = {"internal", "page"}
    pending: list[Path] = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if _is_reparse_or_link(path):
                        raise _error("package_tree_link_forbidden")
                    relative = path.relative_to(root).as_posix()
                    _safe_relative_path(relative)
                    if entry.is_dir(follow_symlinks=False):
                        if relative not in expected_directories:
                            raise _error("package_tree_invalid")
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        found.append(relative)
                    else:
                        raise _error("package_tree_invalid")
        except FrozenG0PackageReferenceError:
            raise
        except OSError as exc:
            raise _error("package_tree_unreadable") from exc
    if tuple(sorted(found)) != _INVENTORY_PATHS:
        raise _error("package_tree_inventory_mismatch")
    return sorted(found)


def _inventory_from_package(root: Path) -> tuple["FrozenG0InventoryEntry", ...]:
    paths = _walk_package_paths(root)
    entries: list[FrozenG0InventoryEntry] = []
    for relative_path in paths:
        path = _safe_package_path(root, relative_path)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise _error("package_tree_unreadable") from exc
        entries.append(
            FrozenG0InventoryEntry(
                relative_path=relative_path,
                role=_INVENTORY_ROLES[relative_path],
                size=len(content),
                sha256=_sha256(content),
            )
        )
    return tuple(entries)


def _inventory_tree_sha256(inventory: Iterable["FrozenG0InventoryEntry"]) -> str:
    return _sha256(_canonical_json_bytes([item.to_dict() for item in inventory]))


def _context_identity(context_bytes: bytes) -> str:
    return "agent-context-" + _sha256(context_bytes)[:20]


def _reference_payload(
    *,
    package_id: str,
    page_id: str,
    context_id: str,
    context_sha256: str,
    guidance_bundle_id: str,
    guidance_sha256: str,
    package_manifest_sha256: str,
    inventory_tree_sha256: str,
    inventory: tuple["FrozenG0InventoryEntry", ...],
) -> dict[str, Any]:
    return {
        "schema_version": FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION,
        "package_schema_version": RESULT_PACKAGE_V2_SCHEMA_VERSION,
        "package_id": package_id,
        "page_id": page_id,
        "context_schema_version": AGENT_BUNDLE_SCHEMA_VERSION,
        "context_id": context_id,
        "context_sha256": context_sha256,
        "guidance_schema_version": RETRIEVAL_GUIDANCE_SCHEMA_VERSION,
        "guidance_bundle_id": guidance_bundle_id,
        "guidance_sha256": guidance_sha256,
        "package_manifest_sha256": package_manifest_sha256,
        "inventory_tree_sha256": inventory_tree_sha256,
        "inventory": [item.to_dict() for item in inventory],
        "declarations": _declarations_dict(),
    }


def _reference_id(**kwargs: Any) -> str:
    return "frozen-g0-" + _sha256(_canonical_json_bytes(_reference_payload(**kwargs)))


@dataclass(frozen=True)
class FrozenG0InventoryEntry:
    relative_path: str
    role: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "role": self.role,
            "size": self.size,
            "sha256": self.sha256,
        }

    def validate(self, expected_path: str) -> None:
        if self.relative_path != expected_path or _safe_relative_path(self.relative_path) != expected_path:
            raise _error("inventory_path_invalid")
        if self.role != _INVENTORY_ROLES[expected_path]:
            raise _error("inventory_role_invalid")
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0:
            raise _error("inventory_size_invalid")
        if not _is_sha256(self.sha256):
            raise _error("inventory_sha256_invalid")


@dataclass(frozen=True)
class FrozenG0PackageReference:
    package_id: str
    page_id: str
    context_id: str
    context_sha256: str
    guidance_bundle_id: str
    guidance_sha256: str
    package_manifest_sha256: str
    inventory_tree_sha256: str
    inventory: tuple[FrozenG0InventoryEntry, ...]
    reference_id: str
    schema_version: str = FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION
    package_schema_version: str = RESULT_PACKAGE_V2_SCHEMA_VERSION
    context_schema_version: str = AGENT_BUNDLE_SCHEMA_VERSION
    guidance_schema_version: str = RETRIEVAL_GUIDANCE_SCHEMA_VERSION

    @classmethod
    def from_verified_package(
        cls,
        package: RetrievalEnhancedResultPackage,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
    ) -> "FrozenG0PackageReference":
        if not isinstance(package, RetrievalEnhancedResultPackage):
            raise _error("package_type_invalid")
        if not isinstance(context, AgentContextBundle) or not isinstance(guidance, RetrievalGuidance):
            raise _error("live_artifact_type_invalid")
        try:
            package.validate()
            context_bytes = _package_json_bytes(context.to_dict())
            guidance_bytes = _package_json_bytes(guidance.to_dict())
        except Exception as exc:
            raise _error("package_or_live_artifact_validation_failed") from exc
        root = Path(package.package_dir)
        try:
            context_on_disk = _safe_package_path(root, "internal/agent_context.json").read_bytes()
            guidance_on_disk = _safe_package_path(root, "internal/retrieval_guidance.json").read_bytes()
            manifest_bytes = _safe_package_path(root, "package_manifest.json").read_bytes()
        except OSError as exc:
            raise _error("package_tree_unreadable") from exc
        if context_on_disk != context_bytes:
            raise _error("context_package_binding_mismatch")
        if guidance_on_disk != guidance_bytes:
            raise _error("guidance_package_binding_mismatch")
        if guidance.guidance_bundle_id != json.loads(guidance_on_disk.decode("utf-8")).get("guidance_bundle_id"):
            raise _error("guidance_package_binding_mismatch")
        inventory = _inventory_from_package(root)
        inventory_map = {item.relative_path: item for item in inventory}
        context_sha256 = _sha256(context_bytes)
        guidance_sha256 = _sha256(guidance_bytes)
        manifest_sha256 = _sha256(manifest_bytes)
        if (
            inventory_map["internal/agent_context.json"].sha256 != context_sha256
            or inventory_map["internal/retrieval_guidance.json"].sha256 != guidance_sha256
            or inventory_map["package_manifest.json"].sha256 != manifest_sha256
        ):
            raise _error("package_inventory_binding_mismatch")
        tree_sha256 = _inventory_tree_sha256(inventory)
        context_id = _context_identity(context_bytes)
        reference_id = _reference_id(
            package_id=package.package_id,
            page_id=package.page_id,
            context_id=context_id,
            context_sha256=context_sha256,
            guidance_bundle_id=guidance.guidance_bundle_id,
            guidance_sha256=guidance_sha256,
            package_manifest_sha256=manifest_sha256,
            inventory_tree_sha256=tree_sha256,
            inventory=inventory,
        )
        result = cls(
            package_id=package.package_id,
            page_id=package.page_id,
            context_id=context_id,
            context_sha256=context_sha256,
            guidance_bundle_id=guidance.guidance_bundle_id,
            guidance_sha256=guidance_sha256,
            package_manifest_sha256=manifest_sha256,
            inventory_tree_sha256=tree_sha256,
            inventory=inventory,
            reference_id=reference_id,
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION:
            raise _error("reference_schema_invalid")
        if self.package_schema_version != RESULT_PACKAGE_V2_SCHEMA_VERSION:
            raise _error("package_schema_invalid")
        if self.context_schema_version != AGENT_BUNDLE_SCHEMA_VERSION:
            raise _error("context_schema_invalid")
        if self.guidance_schema_version != RETRIEVAL_GUIDANCE_SCHEMA_VERSION:
            raise _error("guidance_schema_invalid")
        for value, code in (
            (self.package_id, "package_identity_invalid"),
            (self.page_id, "page_identity_invalid"),
            (self.context_id, "context_identity_invalid"),
            (self.guidance_bundle_id, "guidance_identity_invalid"),
        ):
            _require_text(value, code)
        for value, code in (
            (self.context_sha256, "context_sha256_invalid"),
            (self.guidance_sha256, "guidance_sha256_invalid"),
            (self.package_manifest_sha256, "manifest_sha256_invalid"),
            (self.inventory_tree_sha256, "tree_sha256_invalid"),
        ):
            if not _is_sha256(value):
                raise _error(code)
        if not isinstance(self.inventory, tuple) or len(self.inventory) != len(_INVENTORY_PATHS):
            raise _error("inventory_invalid")
        if tuple(item.relative_path for item in self.inventory) != _INVENTORY_PATHS:
            raise _error("inventory_order_invalid")
        for item, expected_path in zip(self.inventory, _INVENTORY_PATHS, strict=True):
            if not isinstance(item, FrozenG0InventoryEntry):
                raise _error("inventory_invalid")
            item.validate(expected_path)
        inventory_by_path = {item.relative_path: item for item in self.inventory}
        if self.context_sha256 != inventory_by_path["internal/agent_context.json"].sha256:
            raise _error("context_inventory_binding_invalid")
        if self.guidance_sha256 != inventory_by_path["internal/retrieval_guidance.json"].sha256:
            raise _error("guidance_inventory_binding_invalid")
        if self.package_manifest_sha256 != inventory_by_path["package_manifest.json"].sha256:
            raise _error("package_manifest_inventory_binding_invalid")
        if _inventory_tree_sha256(self.inventory) != self.inventory_tree_sha256:
            raise _error("tree_sha256_invalid")
        expected_context_id = "agent-context-" + self.context_sha256[:20]
        if self.context_id != expected_context_id:
            raise _error("context_identity_invalid")
        expected_reference_id = _reference_id(
            package_id=self.package_id,
            page_id=self.page_id,
            context_id=self.context_id,
            context_sha256=self.context_sha256,
            guidance_bundle_id=self.guidance_bundle_id,
            guidance_sha256=self.guidance_sha256,
            package_manifest_sha256=self.package_manifest_sha256,
            inventory_tree_sha256=self.inventory_tree_sha256,
            inventory=self.inventory,
        )
        if self.reference_id != expected_reference_id:
            raise _error("reference_identity_invalid")

    def validate_against(
        self,
        package: RetrievalEnhancedResultPackage,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
    ) -> None:
        self.validate()
        current = type(self).from_verified_package(package, context, guidance)
        if current != self:
            raise _error("live_package_reference_mismatch")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "reference_id": self.reference_id,
            "package_schema_version": self.package_schema_version,
            "package_id": self.package_id,
            "page_id": self.page_id,
            "context_schema_version": self.context_schema_version,
            "context_id": self.context_id,
            "context_sha256": self.context_sha256,
            "guidance_schema_version": self.guidance_schema_version,
            "guidance_bundle_id": self.guidance_bundle_id,
            "guidance_sha256": self.guidance_sha256,
            "package_manifest_sha256": self.package_manifest_sha256,
            "inventory_tree_sha256": self.inventory_tree_sha256,
            "inventory": [item.to_dict() for item in self.inventory],
            "declarations": _declarations_dict(),
        }


def freeze_verified_g0_package_reference(
    package: RetrievalEnhancedResultPackage,
    context: AgentContextBundle,
    guidance: RetrievalGuidance,
) -> FrozenG0PackageReference:
    """Create a path-free reference to an existing, validated G0 v2 package."""
    return FrozenG0PackageReference.from_verified_package(package, context, guidance)
