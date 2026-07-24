"""Tier A-09 exact local temporary-fixture cleanup controls.

There is deliberately no recursive or arbitrary-path deletion API here.  The
only public action removes exact, predeclared regular files from a dedicated
root created by this module.  Policies and receipts are payload-free.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePath
import stat
from typing import Callable, Mapping, Sequence
from uuid import uuid4


CLEANUP_POLICY_SCHEMA_VERSION = "req2web.runtime.local_cleanup_policy.v2"
CLEANUP_RECEIPT_SCHEMA_VERSION = "req2web.runtime.local_cleanup_receipt.v2"
CLEANUP_ROOT_SCHEMA_VERSION = "req2web.runtime.local_cleanup_root.v1"
CLEANUP_POLICY_AUTHORITY_ID = "req2web.runtime.local_cleanup_authority.v2"
_ROOT_MARKER_NAME = ".req2web-tier-a-local-cleanup-root.json"
_ROOT_PREFIX = "_req2web_tier_a_cleanup_"
_POLICY_ID_PREFIX = "local-cleanup-policy-"
_RECEIPT_ID_PREFIX = "local-cleanup-receipt-"
_ARTIFACT_CLASSES = (
    "deterministic_test_artifact",
    "project_authored_fixture",
    "synthetic_fixture",
)
_REMOTE_ACTIONS = {
    "credential_revocation": "not_applicable_tier_a_not_executed",
    "remote_deletion": "not_applicable_tier_a_not_executed",
    "remote_shutdown": "not_applicable_tier_a_not_executed",
}
_LOCAL_FIXTURE_SCOPE = {
    "receipt_scope": "local_temporary_project_authored_or_synthetic_fixture",
    "real_run_cleanup_receipt": "not_a_real_run_receipt",
}
_FAILURE_CODES = (
    "cleanup_delete_failed",
    "cleanup_postcheck_failed",
    "cleanup_precheck_failed",
)
_POLICY_KEYS = (
    "schema_version", "policy_id", "authority_id", "authority_sha256",
    "temporary_root_id_sha256", "temporary_root_marker_sha256", "artifacts",
    "expected_pre_inventory_sha256", "external_egress_allowed", "model_loaded",
    "run_occurred", "remote_actions",
)
_DECLARATION_KEYS = (
    "artifact_id", "artifact_class", "relative_path_sha256", "byte_length", "content_sha256",
)
_RECEIPT_KEYS = (
    "schema_version", "receipt_id", "policy_id", "policy_sha256", "authority_id",
    "authority_sha256", "temporary_root_id_sha256", "temporary_root_marker_sha256",
    "action_status", "failure_code", "failure_artifact_id", "pre_inventory_sha256", "post_inventory_sha256",
    "artifact_results", "external_egress_allowed", "model_loaded", "run_occurred",
    "remote_actions", "local_fixture_scope",
)
_RESULT_KEYS = (
    "artifact_id", "artifact_class", "relative_path_sha256", "expected_content_sha256",
    "decision", "post_state",
)


class LocalCleanupError(ValueError):
    """The Tier A local cleanup authority rejected the requested state."""


# Never consult the public module symbol after import.  Public test code may
# monkeypatch that symbol, but captured authorities must continue to raise and
# catch the original protocol error type.
_CAPTURED_ERROR_TYPE = LocalCleanupError


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()




def _canonical_captured(value: object, _dumps=json.dumps) -> bytes:
    return _dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_captured(value: bytes, _factory=hashlib.sha256) -> str:
    return _factory(value).hexdigest()


def _make_captured_filesystem_authority(
    _error=LocalCleanupError, _path_type=Path, _pure_path=PurePath, _pure_is_absolute=PurePath.is_absolute,
    _lstat=Path.lstat, _resolve=Path.resolve, _is_file=Path.is_file,
    _is_dir=Path.is_dir, _exists=Path.exists, _is_symlink=Path.is_symlink,
    _absolute=Path.absolute, _relative_to=Path.relative_to, _parent=Path.parent.fget,
    _name=Path.name.fget, _joinpath=Path.joinpath, _mkdir=Path.mkdir,
    _read_bytes=Path.read_bytes, _write_bytes=Path.write_bytes,
    _unlink=Path.unlink, _is_lnk=stat.S_ISLNK,
    _reparse_flag=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0),
    _loads=json.loads, _decode_error=json.JSONDecodeError, _classes=_ARTIFACT_CLASSES,
    _uuid=uuid4, _canonical=_canonical_captured, _hash=_sha256_captured,
    _marker_name=_ROOT_MARKER_NAME, _prefix=_ROOT_PREFIX,
    _schema=CLEANUP_ROOT_SCHEMA_VERSION, _root_type=None,
    _artifact_type=None,
):
    def reparse(path):
        try: info=_lstat(path)
        except OSError as exc: raise _error("cleanup_path_unreadable") from exc
        return _is_lnk(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _reparse_flag)
    def resolve_path(root, raw_relative, require_file):
        if type(raw_relative) is not str or not raw_relative or chr(92) in raw_relative: raise _error("cleanup_relative_path_invalid")
        relative=_pure_path(raw_relative)
        if _pure_is_absolute(relative) or any(part in ("", ".", "..") for part in relative.parts): raise _error("cleanup_relative_path_invalid")
        current=root
        for part in relative.parts:
            current=_joinpath(current,part)
            if _exists(current) or _is_symlink(current):
                if reparse(current): raise _error("cleanup_symlink_or_reparse_rejected")
        candidate=_joinpath(root,*relative.parts)
        if not require_file: return relative,candidate
        try: resolved=_resolve(candidate,strict=True)
        except OSError as exc: raise _error("cleanup_artifact_unreadable") from exc
        try: _relative_to(resolved,root)
        except ValueError as exc: raise _error("cleanup_scope_escape_rejected") from exc
        if not _is_file(resolved): raise _error("cleanup_artifact_not_regular_file")
        return relative,resolved
    def root_factory(parent):
        if not isinstance(parent,_path_type) or reparse(parent): raise _error("cleanup_root_parent_invalid")
        try: parent_resolved=_resolve(parent,strict=True)
        except OSError as exc: raise _error("cleanup_root_parent_invalid") from exc
        if not _is_dir(parent_resolved) or reparse(parent_resolved): raise _error("cleanup_root_parent_invalid")
        root_id=_uuid().hex; root=_joinpath(parent_resolved,_prefix+root_id)
        try:
            _mkdir(root); _write_bytes(_joinpath(root,_marker_name),_canonical({"root_id":root_id,"schema_version":_schema}))
        except OSError as exc: raise _error("cleanup_root_create_failed") from exc
        return _root_type(root,root_id)
    def root_validator(root):
        if type(root) is not _root_type or not isinstance(root.path,_path_type) or type(root.root_id) is not str or len(root.root_id) != 32 or any(c not in "0123456789abcdef" for c in root.root_id): raise _error("cleanup_root_invalid")
        lexical=root.path
        if reparse(lexical) or reparse(_parent(lexical)): raise _error("cleanup_symlink_or_reparse_rejected")
        try: resolved=_resolve(lexical,strict=True)
        except OSError as exc: raise _error("cleanup_root_invalid") from exc
        if resolved != _absolute(lexical) or not _is_dir(resolved) or reparse(resolved) or _name(resolved) != _prefix+root.root_id: raise _error("cleanup_root_invalid")
        marker_path=_joinpath(resolved,_marker_name)
        if reparse(marker_path): raise _error("cleanup_symlink_or_reparse_rejected")
        try: raw=_read_bytes(marker_path); marker=_loads(raw.decode("utf-8"))
        except (OSError,UnicodeDecodeError,_decode_error) as exc: raise _error("cleanup_root_marker_invalid") from exc
        if _canonical(marker) != raw or marker != {"root_id":root.root_id,"schema_version":_schema}: raise _error("cleanup_root_marker_invalid")
        return resolved,_hash(root.root_id.encode("utf-8")),_hash(raw)
    def metadata(root,artifact):
        if type(artifact) is not _artifact_type: raise _error("cleanup_artifact_descriptor_invalid")
        value=artifact.artifact_id
        if type(value) is not str or not value or len(value) > 96 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in value): raise _error("cleanup_artifact_id_invalid")
        identifier=value
        if artifact.artifact_class not in _classes: raise _error("cleanup_artifact_class_invalid")
        relative,path=resolve_path(root,artifact.relative_path,True)
        try: payload=_read_bytes(path)
        except OSError as exc: raise _error("cleanup_artifact_unreadable") from exc
        return {"artifact_id":identifier,"artifact_class":artifact.artifact_class,"relative_path_sha256":_hash(str(relative).encode("utf-8")),"byte_length":len(payload),"content_sha256":_hash(payload)}
    def absent(candidate):
        return not _exists(candidate) and not _is_symlink(candidate)
    return root_factory,root_validator,resolve_path,metadata,_unlink,absent


_CAPTURED_FILESYSTEM=_make_captured_filesystem_authority()

def _exact_mapping(value: object, keys: tuple[str, ...], _error=LocalCleanupError) -> Mapping[str, object]:
    if not isinstance(value, _mapping_type) or set(value) != set(keys):
        raise _error("cleanup_exact_keys_invalid")
    return value


def _sha256_text(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _strict_bool(value: object, _error=LocalCleanupError) -> bool:
    if type(value) is not bool:
        raise _error("cleanup_boolean_invalid")
    return value


def _artifact_id(value: object, _error=LocalCleanupError) -> str:
    if type(value) is not str or not value or len(value) > 96:
        raise _error("cleanup_artifact_id_invalid")
    if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for char in value):
        raise _error("cleanup_artifact_id_invalid")
    return value


def _policy_id(value: object, _prefix=_POLICY_ID_PREFIX, _error=LocalCleanupError) -> str:
    if type(value) is not str or not value.startswith(_prefix) or len(value) != len(_prefix) + 20:
        raise _error("cleanup_policy_id_invalid")
    suffix = value[len(_prefix):]
    if any(char not in "0123456789abcdef" for char in suffix):
        raise _error("cleanup_policy_id_invalid")
    return value


def _uuid_hex(value: object) -> bool:
    return type(value) is str and len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def _relative_path(value: object, _error=LocalCleanupError) -> PurePath:
    if type(value) is not str or not value or chr(92) in value:
        raise _error("cleanup_relative_path_invalid")
    result = PurePath(value)
    if result.is_absolute() or any(part in ("", ".", "..") for part in result.parts):
        raise _error("cleanup_relative_path_invalid")
    return result


def _is_reparse_or_symlink(path: Path, _error=LocalCleanupError) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise _error("cleanup_path_unreadable") from exc
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _authority_sha256(
    _canonical=_canonical_bytes, _hash=_sha256, _policy_schema=CLEANUP_POLICY_SCHEMA_VERSION,
    _receipt_schema=CLEANUP_RECEIPT_SCHEMA_VERSION, _root_schema=CLEANUP_ROOT_SCHEMA_VERSION,
    _authority=CLEANUP_POLICY_AUTHORITY_ID, _classes=_ARTIFACT_CLASSES,
) -> str:
    return _hash(_canonical({
        "artifact_classes": list(_classes), "authority_id": _authority,
        "policy_schema": _policy_schema, "receipt_schema": _receipt_schema,
        "root_schema": _root_schema,
    }))


@dataclass(frozen=True)
class LocalTemporaryRoot:
    """A module-created dedicated directory, not a general filesystem root."""

    path: Path
    root_id: str


@dataclass(frozen=True)
class LocalTemporaryArtifact:
    """Caller-facing descriptor for an exact local fixture file."""

    artifact_id: str
    artifact_class: str
    relative_path: str


@dataclass(frozen=True)
class CleanupArtifactDeclaration:
    artifact_id: str
    artifact_class: str
    relative_path_sha256: str
    byte_length: int
    content_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id, "artifact_class": self.artifact_class,
            "relative_path_sha256": self.relative_path_sha256, "byte_length": self.byte_length,
            "content_sha256": self.content_sha256,
        }


def _declaration_projection(
    declaration: object, _type=CleanupArtifactDeclaration, _error=LocalCleanupError,
) -> dict[str, object]:
    if type(declaration) is not _type:
        raise _error(cleanup_declaration_invalid)
    return {
        artifact_id: declaration.artifact_id,
        artifact_class: declaration.artifact_class,
        relative_path_sha256: declaration.relative_path_sha256,
        byte_length: declaration.byte_length,
        content_sha256: declaration.content_sha256,
    }


def _make_root_factory(
    root_type: type[LocalTemporaryRoot], _path_type=Path, _uuid=uuid4, _canonical=_canonical_bytes,
    _marker_name=_ROOT_MARKER_NAME, _prefix=_ROOT_PREFIX, _schema=CLEANUP_ROOT_SCHEMA_VERSION,
    _reparse=_is_reparse_or_symlink,
) -> Callable[[Path], LocalTemporaryRoot]:
    def create(parent: Path) -> LocalTemporaryRoot:
        if not isinstance(parent, _path_type):
            raise _CAPTURED_ERROR_TYPE("cleanup_root_parent_invalid")
        if _reparse(parent):
            raise _CAPTURED_ERROR_TYPE("cleanup_symlink_or_reparse_rejected")
        try:
            parent_resolved = parent.resolve(strict=True)
        except OSError as exc:
            raise _CAPTURED_ERROR_TYPE("cleanup_root_parent_invalid") from exc
        if not parent_resolved.is_dir() or _reparse(parent_resolved):
            raise _CAPTURED_ERROR_TYPE("cleanup_root_parent_invalid")
        root_id = _uuid().hex
        root = parent_resolved / (_prefix + root_id)
        try:
            root.mkdir()
            marker = {"root_id": root_id, "schema_version": _schema}
            (root / _marker_name).write_bytes(_canonical(marker))
        except OSError as exc:
            raise _CAPTURED_ERROR_TYPE("cleanup_root_create_failed") from exc
        return root_type(root, root_id)
    return create


def _make_root_validator(
    root_type: type[LocalTemporaryRoot], _path_type=Path, _canonical=_canonical_bytes,
    _hash=_sha256, _marker_name=_ROOT_MARKER_NAME, _prefix=_ROOT_PREFIX,
    _schema=CLEANUP_ROOT_SCHEMA_VERSION, _reparse=_is_reparse_or_symlink,
    _loads=json.loads, _decode_error=json.JSONDecodeError, _uuid_check=_uuid_hex,
) -> Callable[[LocalTemporaryRoot], tuple[Path, str, str]]:
    def validate(root: LocalTemporaryRoot) -> tuple[Path, str, str]:
        if type(root) is not root_type or not isinstance(root.path, _path_type) or not _uuid_check(root.root_id):
            raise _CAPTURED_ERROR_TYPE("cleanup_root_invalid")
        lexical = root.path
        # The lexical check must happen before resolve() so a root symlink cannot
        # be normalized away.
        if _reparse(lexical) or _reparse(lexical.parent):
            raise _CAPTURED_ERROR_TYPE("cleanup_symlink_or_reparse_rejected")
        try:
            resolved = lexical.resolve(strict=True)
        except OSError as exc:
            raise _CAPTURED_ERROR_TYPE("cleanup_root_invalid") from exc
        if (
            resolved != lexical.absolute() or not resolved.is_dir() or _reparse(resolved)
            or resolved.name != _prefix + root.root_id
        ):
            raise _CAPTURED_ERROR_TYPE("cleanup_root_invalid")
        marker_path = resolved / _marker_name
        if _reparse(marker_path):
            raise _CAPTURED_ERROR_TYPE("cleanup_symlink_or_reparse_rejected")
        try:
            raw = marker_path.read_bytes()
            marker = _loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, _decode_error) as exc:
            raise _CAPTURED_ERROR_TYPE("cleanup_root_marker_invalid") from exc
        if _canonical(marker) != raw or marker != {"root_id": root.root_id, "schema_version": _schema}:
            raise _CAPTURED_ERROR_TYPE("cleanup_root_marker_invalid")
        return resolved, _hash(root.root_id.encode("utf-8")), _hash(raw)
    return validate


def _make_path_resolver(
    _relative=_relative_path,
    _reparse=_is_reparse_or_symlink,
) -> Callable[[Path, str, bool], tuple[PurePath, Path]]:
    def resolve(root: Path, raw_relative: str, require_file: bool) -> tuple[PurePath, Path]:
        relative = _relative(raw_relative)
        current = root
        for part in relative.parts:
            current = current / part
            if current.exists() or current.is_symlink():
                if _reparse(current):
                    raise _CAPTURED_ERROR_TYPE("cleanup_symlink_or_reparse_rejected")
        candidate = root.joinpath(*relative.parts)
        if require_file:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise _CAPTURED_ERROR_TYPE("cleanup_artifact_unreadable") from exc
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise _CAPTURED_ERROR_TYPE("cleanup_scope_escape_rejected") from exc
            if not resolved.is_file():
                raise _CAPTURED_ERROR_TYPE("cleanup_artifact_not_regular_file")
            return relative, resolved
        return relative, candidate
    return resolve


def _make_metadata(
    resolver: Callable[[Path, str, bool], tuple[PurePath, Path]], _id=_artifact_id,
    _classes=_ARTIFACT_CLASSES, _hash=_sha256, _artifact_type=LocalTemporaryArtifact,
) -> Callable[[Path, LocalTemporaryArtifact], dict[str, object]]:
    def metadata(root: Path, artifact: LocalTemporaryArtifact) -> dict[str, object]:
        if type(artifact) is not _artifact_type:
            raise _CAPTURED_ERROR_TYPE("cleanup_artifact_descriptor_invalid")
        artifact_id = _id(artifact.artifact_id)
        if artifact.artifact_class not in _classes:
            raise _CAPTURED_ERROR_TYPE("cleanup_artifact_class_invalid")
        relative, path = resolver(root, artifact.relative_path, True)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise _CAPTURED_ERROR_TYPE("cleanup_artifact_unreadable") from exc
        return {
            "artifact_id": artifact_id, "artifact_class": artifact.artifact_class,
            "relative_path_sha256": _hash(str(relative).encode("utf-8")),
            "byte_length": len(payload), "content_sha256": _hash(payload),
        }
    return metadata


def _inventory_hash(items: Sequence[Mapping[str, object]], _canonical=_canonical_bytes, _hash=_sha256) -> str:
    return _hash(_canonical([dict(item) for item in items]))


def _declaration_from_dict(
    value: object, _classes=_ARTIFACT_CLASSES, _sha_check=_sha256_text,
    _mapping=_exact_mapping, _keys=_DECLARATION_KEYS, _id=_artifact_id,
    _declaration_type=CleanupArtifactDeclaration,
) -> CleanupArtifactDeclaration:
    data = _mapping(value, _keys)
    artifact_id = _id(data["artifact_id"])
    if data["artifact_class"] not in _classes or type(data["byte_length"]) is not int or data["byte_length"] < 0:
        raise _CAPTURED_ERROR_TYPE("cleanup_declaration_invalid")
    if not _sha_check(data["relative_path_sha256"]) or not _sha_check(data["content_sha256"]):
        raise _CAPTURED_ERROR_TYPE("cleanup_declaration_invalid")
    return _declaration_type(
        artifact_id, data["artifact_class"], data["relative_path_sha256"], data["byte_length"], data["content_sha256"]
    )


def _policy_root(policy: "LocalCleanupPolicy") -> dict[str, object]:
    return {
        "schema_version": policy.schema_version, "authority_id": policy.authority_id,
        "authority_sha256": policy.authority_sha256,
        "temporary_root_id_sha256": policy.temporary_root_id_sha256,
        "temporary_root_marker_sha256": policy.temporary_root_marker_sha256,
        "artifacts": [item.to_dict() for item in policy.artifacts],
        "expected_pre_inventory_sha256": policy.expected_pre_inventory_sha256,
        "external_egress_allowed": policy.external_egress_allowed, "model_loaded": policy.model_loaded,
        "run_occurred": policy.run_occurred, "remote_actions": dict(policy.remote_actions),
    }


def _validate_policy(
    policy: "LocalCleanupPolicy", _root=_policy_root, _canonical=_canonical_bytes, _hash=_sha256,
    _authority_hash=_authority_sha256, _schema=CLEANUP_POLICY_SCHEMA_VERSION,
    _authority=CLEANUP_POLICY_AUTHORITY_ID, _remote=_REMOTE_ACTIONS, _prefix=_POLICY_ID_PREFIX,
    _sha_check=_sha256_text, _declaration=_declaration_from_dict, _inventory=_inventory_hash,
) -> None:
    declarations = tuple(_declaration(item.to_dict()) for item in policy.artifacts)
    if (
        policy.schema_version != _schema or policy.authority_id != _authority
        or policy.authority_sha256 != _authority_hash() or not _sha_check(policy.temporary_root_id_sha256)
        or not _sha_check(policy.temporary_root_marker_sha256) or not _sha_check(policy.expected_pre_inventory_sha256)
        or not declarations or tuple(item.artifact_id for item in declarations) != tuple(sorted(item.artifact_id for item in declarations))
        or len({item.artifact_id for item in declarations}) != len(declarations)
        or len({item.relative_path_sha256 for item in declarations}) != len(declarations)
        or policy.external_egress_allowed is not False or policy.model_loaded is not False
        or policy.run_occurred is not False or dict(policy.remote_actions) != _remote
    ):
        raise _CAPTURED_ERROR_TYPE("cleanup_policy_state_invalid")
    expected_inventory = _inventory([item.to_dict() for item in declarations])
    if policy.expected_pre_inventory_sha256 != expected_inventory:
        raise _CAPTURED_ERROR_TYPE("cleanup_policy_inventory_invalid")
    if policy.policy_id != _prefix + _hash(_canonical(_root(policy)))[:20]:
        raise _CAPTURED_ERROR_TYPE("cleanup_policy_identity_invalid")


@dataclass(frozen=True)
class LocalCleanupPolicy:
    schema_version: str
    policy_id: str
    authority_id: str
    authority_sha256: str
    temporary_root_id_sha256: str
    temporary_root_marker_sha256: str
    artifacts: tuple[CleanupArtifactDeclaration, ...]
    expected_pre_inventory_sha256: str
    external_egress_allowed: bool
    model_loaded: bool
    run_occurred: bool
    remote_actions: Mapping[str, str]

    # These definitions are replaced below with closed public methods.  Keeping
    # no caller-controlled authority parameter in the final public signature is
    # intentional.
    @classmethod
    def create(cls, temporary_root: LocalTemporaryRoot, artifacts: Sequence[LocalTemporaryArtifact]) -> "LocalCleanupPolicy":
        raise AssertionError("captured method installation failed")

    @classmethod
    def from_dict(cls, payload: object) -> "LocalCleanupPolicy":
        raise AssertionError("captured method installation failed")

    @classmethod
    def from_bytes(cls, raw: object) -> "LocalCleanupPolicy":
        raise AssertionError("captured method installation failed")

    def validate(self) -> None:
        raise AssertionError("captured method installation failed")

    def to_dict(self) -> dict[str, object]:
        raise AssertionError("captured method installation failed")

    def canonical_bytes(self) -> bytes:
        raise AssertionError("captured method installation failed")

    def sha256(self) -> str:
        raise AssertionError("captured method installation failed")


def _result_from_declaration(declaration: CleanupArtifactDeclaration, decision: str, post_state: str) -> dict[str, object]:
    return {
        "artifact_id": declaration.artifact_id, "artifact_class": declaration.artifact_class,
        "relative_path_sha256": declaration.relative_path_sha256,
        "expected_content_sha256": declaration.content_sha256,
        "decision": decision, "post_state": post_state,
    }


def _receipt_root(receipt: "LocalCleanupReceipt") -> dict[str, object]:
    return {
        "schema_version": receipt.schema_version, "policy_id": receipt.policy_id,
        "policy_sha256": receipt.policy_sha256, "authority_id": receipt.authority_id,
        "authority_sha256": receipt.authority_sha256,
        "temporary_root_id_sha256": receipt.temporary_root_id_sha256,
        "temporary_root_marker_sha256": receipt.temporary_root_marker_sha256,
        "action_status": receipt.action_status, "failure_code": receipt.failure_code,
        "failure_artifact_id": receipt.failure_artifact_id,
        "pre_inventory_sha256": receipt.pre_inventory_sha256,
        "post_inventory_sha256": receipt.post_inventory_sha256,
        "artifact_results": [dict(value) for value in receipt.artifact_results],
        "external_egress_allowed": receipt.external_egress_allowed, "model_loaded": receipt.model_loaded,
        "run_occurred": receipt.run_occurred, "remote_actions": dict(receipt.remote_actions),
        "local_fixture_scope": dict(receipt.local_fixture_scope),
    }


def _validate_result(
    value: object, _classes=_ARTIFACT_CLASSES, _sha_check=_sha256_text,
    _mapping=_exact_mapping, _keys=_RESULT_KEYS, _id=_artifact_id,
) -> Mapping[str, object]:
    data = _mapping(value, _keys)
    _id(data["artifact_id"])
    if data["artifact_class"] not in _classes or not _sha_check(data["relative_path_sha256"]) or not _sha_check(data["expected_content_sha256"]):
        raise _CAPTURED_ERROR_TYPE("cleanup_receipt_result_invalid")
    if data["decision"] not in ("deleted", "delete_failed", "not_attempted", "rejected_precheck"):
        raise _CAPTURED_ERROR_TYPE("cleanup_receipt_result_invalid")
    if data["post_state"] not in ("absent", "not_checked", "present_or_unsafe"):
        raise _CAPTURED_ERROR_TYPE("cleanup_receipt_result_invalid")
    return data


def _validate_receipt(
    receipt: "LocalCleanupReceipt", _root=_receipt_root, _canonical=_canonical_bytes, _hash=_sha256,
    _authority_hash=_authority_sha256, _schema=CLEANUP_RECEIPT_SCHEMA_VERSION,
    _authority=CLEANUP_POLICY_AUTHORITY_ID, _remote=_REMOTE_ACTIONS, _scope=_LOCAL_FIXTURE_SCOPE,
    _prefix=_RECEIPT_ID_PREFIX, _sha_check=_sha256_text, _result=_validate_result,
    _failure_codes=_FAILURE_CODES,
) -> None:
    results = tuple(_result(item) for item in receipt.artifact_results)
    if (
        receipt.schema_version != _schema or receipt.authority_id != _authority
        or receipt.authority_sha256 != _authority_hash() or not _sha_check(receipt.policy_sha256)
        or not _sha_check(receipt.temporary_root_id_sha256) or not _sha_check(receipt.temporary_root_marker_sha256)
        or not _sha_check(receipt.pre_inventory_sha256) or not _sha_check(receipt.post_inventory_sha256)
        or receipt.action_status not in ("completed", "failed") or type(receipt.failure_code) is not str
        or (receipt.failure_artifact_id is not None and type(receipt.failure_artifact_id) is not str)
        or not results or len({item["artifact_id"] for item in results}) != len(results)
        or receipt.external_egress_allowed is not False or receipt.model_loaded is not False
        or receipt.run_occurred is not False or dict(receipt.remote_actions) != _remote
        or dict(receipt.local_fixture_scope) != _scope
    ):
        raise _CAPTURED_ERROR_TYPE("cleanup_receipt_state_invalid")
    if receipt.action_status == "completed":
        if receipt.failure_code != "none" or receipt.failure_artifact_id is not None or any(item["decision"] != "deleted" or item["post_state"] != "absent" for item in results):
            raise _CAPTURED_ERROR_TYPE("cleanup_receipt_state_invalid")
    elif receipt.failure_code not in _failure_codes:
        raise _CAPTURED_ERROR_TYPE("cleanup_receipt_state_invalid")
    elif receipt.failure_code == "cleanup_delete_failed":
        if receipt.failure_artifact_id not in {item["artifact_id"] for item in results}:
            raise _CAPTURED_ERROR_TYPE("cleanup_receipt_state_invalid")
    elif receipt.failure_artifact_id is not None:
        raise _CAPTURED_ERROR_TYPE("cleanup_receipt_state_invalid")
    if receipt.receipt_id != _prefix + _hash(_canonical(_root(receipt)))[:20]:
        raise _CAPTURED_ERROR_TYPE("cleanup_receipt_identity_invalid")


@dataclass(frozen=True)
class LocalCleanupReceipt:
    schema_version: str
    receipt_id: str
    policy_id: str
    policy_sha256: str
    authority_id: str
    authority_sha256: str
    temporary_root_id_sha256: str
    temporary_root_marker_sha256: str
    action_status: str
    failure_code: str
    failure_artifact_id: str | None
    pre_inventory_sha256: str
    post_inventory_sha256: str
    artifact_results: tuple[Mapping[str, object], ...]
    external_egress_allowed: bool
    model_loaded: bool
    run_occurred: bool
    remote_actions: Mapping[str, str]
    local_fixture_scope: Mapping[str, str]

    @classmethod
    def from_dict(cls, payload: object) -> "LocalCleanupReceipt":
        raise AssertionError("captured method installation failed")

    @classmethod
    def from_bytes(cls, raw: object) -> "LocalCleanupReceipt":
        raise AssertionError("captured method installation failed")

    def validate(self) -> None:
        raise AssertionError("captured method installation failed")

    def to_dict(self) -> dict[str, object]:
        raise AssertionError("captured method installation failed")

    def canonical_bytes(self) -> bytes:
        raise AssertionError("captured method installation failed")

    def sha256(self) -> str:
        raise AssertionError("captured method installation failed")

    def validate_against(
        self, policy: LocalCleanupPolicy, temporary_root: LocalTemporaryRoot,
        artifacts: Sequence[LocalTemporaryArtifact],
    ) -> None:
        raise AssertionError("captured method installation failed")


# Superseded mutable assembly helpers were intentionally removed.
# Compatibility names remain only so rebinding tests can prove the captured
# authority does not dispatch through them.
_observe_policy = None
_expected_result_template = None
_policy_root = None
_receipt_root = None
_inventory_hash = None

# Captured Tier A-09 authority.  This replaces legacy assembly helpers below
# with a closed implementation: no authority path calls policy/declaration
# instance methods or resolves mutable module helpers after import.
_CAPTURED_FILESYSTEM=_make_captured_filesystem_authority(
    _root_type=LocalTemporaryRoot, _artifact_type=LocalTemporaryArtifact,
)


def _build_captured_cleanup_authority(
    _error=LocalCleanupError, _policy_type=LocalCleanupPolicy,
    _receipt_type=LocalCleanupReceipt, _root_type=LocalTemporaryRoot,
    _artifact_type=LocalTemporaryArtifact, _decl_type=CleanupArtifactDeclaration,
    _root_validator=_CAPTURED_FILESYSTEM[1],
    _metadata=_CAPTURED_FILESYSTEM[3],
    _resolver=_CAPTURED_FILESYSTEM[2], _absent=_CAPTURED_FILESYSTEM[5],
    _canonical=_canonical_captured, _hash=_sha256_captured,
    _loads=json.loads, _decode_error=json.JSONDecodeError, _mapping_type=Mapping, _path_type=Path,
    _policy_schema=CLEANUP_POLICY_SCHEMA_VERSION, _receipt_schema=CLEANUP_RECEIPT_SCHEMA_VERSION,
    _root_schema=CLEANUP_ROOT_SCHEMA_VERSION, _authority=CLEANUP_POLICY_AUTHORITY_ID, _policy_prefix=_POLICY_ID_PREFIX,
    _receipt_prefix=_RECEIPT_ID_PREFIX, _classes=_ARTIFACT_CLASSES,
    _remote=_REMOTE_ACTIONS, _scope=_LOCAL_FIXTURE_SCOPE,
    _policy_keys=_POLICY_KEYS, _receipt_keys=_RECEIPT_KEYS,
    _declaration_keys=_DECLARATION_KEYS, _result_keys=_RESULT_KEYS,
):
    def exact(value, keys):
        if not isinstance(value, _mapping_type) or set(value) != set(keys):
            raise _error("cleanup_exact_keys_invalid")
        return value
    def sha(value):
        return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    def aid(value):
        if type(value) is not str or not value or len(value) > 96 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in value):
            raise _error("cleanup_artifact_id_invalid")
        return value
    def pid(value):
        if type(value) is not str or not value.startswith(_policy_prefix) or len(value) != len(_policy_prefix)+20 or any(c not in "0123456789abcdef" for c in value[len(_policy_prefix):]):
            raise _error("cleanup_policy_id_invalid")
        return value
    def authority_hash():
        return _hash(_canonical({"artifact_classes":list(_classes),"authority_id":_authority,"policy_schema":_policy_schema,"receipt_schema":_receipt_schema,"root_schema":_root_schema}))
    def decl(value):
        if type(value) is not _decl_type:
            raise _error("cleanup_declaration_invalid")
        result={"artifact_id":value.artifact_id,"artifact_class":value.artifact_class,"relative_path_sha256":value.relative_path_sha256,"byte_length":value.byte_length,"content_sha256":value.content_sha256}
        if result["artifact_class"] not in _classes or type(result["byte_length"]) is not int or result["byte_length"] < 0 or not sha(result["relative_path_sha256"]) or not sha(result["content_sha256"]):
            raise _error("cleanup_declaration_invalid")
        aid(result["artifact_id"])
        return result
    def decl_from(value):
        d=exact(value,_declaration_keys)
        return _decl_type(d["artifact_id"],d["artifact_class"],d["relative_path_sha256"],d["byte_length"],d["content_sha256"])
    def inv(items): return _hash(_canonical([dict(x) for x in items]))
    def policy_root(policy):
        return {"schema_version":policy.schema_version,"authority_id":policy.authority_id,"authority_sha256":policy.authority_sha256,"temporary_root_id_sha256":policy.temporary_root_id_sha256,"temporary_root_marker_sha256":policy.temporary_root_marker_sha256,"artifacts":[decl(x) for x in policy.artifacts],"expected_pre_inventory_sha256":policy.expected_pre_inventory_sha256,"external_egress_allowed":policy.external_egress_allowed,"model_loaded":policy.model_loaded,"run_occurred":policy.run_occurred,"remote_actions":dict(policy.remote_actions)}
    def validate_policy(policy):
        if type(policy) is not _policy_type: raise _error("cleanup_policy_type_invalid")
        ds=tuple(decl(x) for x in policy.artifacts)
        if policy.schema_version != _policy_schema or policy.authority_id != _authority or policy.authority_sha256 != authority_hash() or not pid(policy.policy_id) or not sha(policy.temporary_root_id_sha256) or not sha(policy.temporary_root_marker_sha256) or not sha(policy.expected_pre_inventory_sha256) or not ds or tuple(x["artifact_id"] for x in ds) != tuple(sorted(x["artifact_id"] for x in ds)) or len({x["artifact_id"] for x in ds}) != len(ds) or len({x["relative_path_sha256"] for x in ds}) != len(ds) or policy.external_egress_allowed is not False or policy.model_loaded is not False or policy.run_occurred is not False or dict(policy.remote_actions) != dict(_remote):
            raise _error("cleanup_policy_state_invalid")
        if policy.expected_pre_inventory_sha256 != inv(ds): raise _error("cleanup_policy_inventory_invalid")
        if policy.policy_id != _policy_prefix+_hash(_canonical(policy_root(policy)))[:20]: raise _error("cleanup_policy_identity_invalid")
    def policy_dict(policy):
        validate_policy(policy); return {"policy_id":policy.policy_id,**policy_root(policy)}
    def policy_bytes(policy): return _canonical(policy_dict(policy))
    def policy_hash(policy): return _hash(policy_bytes(policy))
    def policy_create(cls, root_obj, artifacts):
        if cls is not _policy_type or type(root_obj) is not _root_type or type(artifacts) not in (tuple,list) or not artifacts: raise _error("cleanup_policy_input_invalid")
        root,root_hash,marker_hash=_root_validator(root_obj)
        records=[_metadata(root,x) for x in artifacts]
        if len({x["artifact_id"] for x in records}) != len(records) or len({x["relative_path_sha256"] for x in records}) != len(records): raise _error("cleanup_duplicate_artifact_declaration")
        ds=tuple(_decl_type(x["artifact_id"],x["artifact_class"],x["relative_path_sha256"],x["byte_length"],x["content_sha256"]) for x in sorted(records,key=lambda x:x["artifact_id"]))
        root_data={"schema_version":_policy_schema,"authority_id":_authority,"authority_sha256":authority_hash(),"temporary_root_id_sha256":root_hash,"temporary_root_marker_sha256":marker_hash,"artifacts":[decl(x) for x in ds],"expected_pre_inventory_sha256":inv([decl(x) for x in ds]),"external_egress_allowed":False,"model_loaded":False,"run_occurred":False,"remote_actions":dict(_remote)}
        result=_policy_type(policy_id=_policy_prefix+_hash(_canonical(root_data))[:20],artifacts=ds,**{k:v for k,v in root_data.items() if k != "artifacts"})
        validate_policy(result); return result
    def policy_from_dict(cls,payload):
        if cls is not _policy_type: raise _error("cleanup_policy_type_invalid")
        d=exact(payload,_policy_keys)
        if type(d["artifacts"]) is not list or type(d["remote_actions"]) is not dict or type(d["external_egress_allowed"]) is not bool or type(d["model_loaded"]) is not bool or type(d["run_occurred"]) is not bool: raise _error("cleanup_policy_input_invalid")
        result=_policy_type(schema_version=d["schema_version"],policy_id=d["policy_id"],authority_id=d["authority_id"],authority_sha256=d["authority_sha256"],temporary_root_id_sha256=d["temporary_root_id_sha256"],temporary_root_marker_sha256=d["temporary_root_marker_sha256"],artifacts=tuple(decl_from(x) for x in d["artifacts"]),expected_pre_inventory_sha256=d["expected_pre_inventory_sha256"],external_egress_allowed=d["external_egress_allowed"],model_loaded=d["model_loaded"],run_occurred=d["run_occurred"],remote_actions=dict(d["remote_actions"]))
        validate_policy(result); return result
    def policy_from_bytes(cls,raw):
        if type(raw) is not bytes: raise _error("cleanup_policy_bytes_invalid")
        try: result=policy_from_dict(cls,_loads(raw.decode("utf-8")))
        except (UnicodeDecodeError,_decode_error) as exc: raise _error("cleanup_policy_bytes_invalid") from exc
        if policy_bytes(result) != raw: raise _error("cleanup_policy_bytes_noncanonical")
        return result
    def result(declaration,decision,state):
        d=decl(declaration); return {"artifact_id":d["artifact_id"],"artifact_class":d["artifact_class"],"relative_path_sha256":d["relative_path_sha256"],"expected_content_sha256":d["content_sha256"],"decision":decision,"post_state":state}
    def validate_result(value):
        d=exact(value,_result_keys); aid(d["artifact_id"])
        if d["artifact_class"] not in _classes or not sha(d["relative_path_sha256"]) or not sha(d["expected_content_sha256"]) or d["decision"] not in ("deleted","absent_after_action","not_completed","rejected_precheck") or d["post_state"] not in ("absent","present_or_unsafe","not_checked"): raise _error("cleanup_receipt_result_invalid")
        return d
    def receipt_root(receipt):
        return {"schema_version":receipt.schema_version,"policy_id":receipt.policy_id,"policy_sha256":receipt.policy_sha256,"authority_id":receipt.authority_id,"authority_sha256":receipt.authority_sha256,"temporary_root_id_sha256":receipt.temporary_root_id_sha256,"temporary_root_marker_sha256":receipt.temporary_root_marker_sha256,"action_status":receipt.action_status,"failure_code":receipt.failure_code,"failure_artifact_id":receipt.failure_artifact_id,"pre_inventory_sha256":receipt.pre_inventory_sha256,"post_inventory_sha256":receipt.post_inventory_sha256,"artifact_results":[dict(x) for x in receipt.artifact_results],"external_egress_allowed":receipt.external_egress_allowed,"model_loaded":receipt.model_loaded,"run_occurred":receipt.run_occurred,"remote_actions":dict(receipt.remote_actions),"local_fixture_scope":dict(receipt.local_fixture_scope)}
    def validate_receipt(receipt):
        if type(receipt) is not _receipt_type: raise _error("cleanup_receipt_type_invalid")
        rs=tuple(validate_result(x) for x in receipt.artifact_results)
        if tuple(x["artifact_id"] for x in rs) != tuple(sorted(x["artifact_id"] for x in rs)):
            raise _error("cleanup_receipt_result_order_invalid")
        if receipt.schema_version != _receipt_schema or receipt.authority_id != _authority or receipt.authority_sha256 != authority_hash() or not pid(receipt.policy_id) or not sha(receipt.policy_sha256) or not sha(receipt.temporary_root_id_sha256) or not sha(receipt.temporary_root_marker_sha256) or not sha(receipt.pre_inventory_sha256) or not sha(receipt.post_inventory_sha256) or receipt.action_status not in ("completed","failed") or type(receipt.failure_code) is not str or (receipt.failure_artifact_id is not None and type(receipt.failure_artifact_id) is not str) or not rs or len({x["artifact_id"] for x in rs}) != len(rs) or receipt.external_egress_allowed is not False or receipt.model_loaded is not False or receipt.run_occurred is not False or dict(receipt.remote_actions) != dict(_remote) or dict(receipt.local_fixture_scope) != dict(_scope): raise _error("cleanup_receipt_state_invalid")
        if receipt.action_status == "completed":
            good=receipt.failure_code == "none" and receipt.failure_artifact_id is None and all(x["decision"] == "deleted" and x["post_state"] == "absent" for x in rs)
        elif receipt.failure_code == "cleanup_precheck_failed":
            good=receipt.failure_artifact_id is None and all(x["decision"] == "rejected_precheck" and x["post_state"] == "not_checked" for x in rs)
        elif receipt.failure_code == "cleanup_not_completed":
            first=next((x for x in rs if x["post_state"] != "absent"),None)
            good=first is not None and receipt.failure_artifact_id == first["artifact_id"] and all(x["decision"] == ("absent_after_action" if x["post_state"] == "absent" else "not_completed") for x in rs)
        else: good=False
        if not good: raise _error("cleanup_receipt_state_invalid")
        if receipt.receipt_id != _receipt_prefix+_hash(_canonical(receipt_root(receipt)))[:20]: raise _error("cleanup_receipt_identity_invalid")
    def receipt_dict(receipt):
        validate_receipt(receipt); return {"receipt_id":receipt.receipt_id,**receipt_root(receipt)}
    def receipt_bytes(receipt): return _canonical(receipt_dict(receipt))
    def receipt_hash(receipt): return _hash(receipt_bytes(receipt))
    def receipt_from_dict(cls,payload):
        if cls is not _receipt_type: raise _error("cleanup_receipt_type_invalid")
        d=exact(payload,_receipt_keys)
        if type(d["artifact_results"]) is not list or type(d["remote_actions"]) is not dict or type(d["local_fixture_scope"]) is not dict or type(d["external_egress_allowed"]) is not bool or type(d["model_loaded"]) is not bool or type(d["run_occurred"]) is not bool: raise _error("cleanup_receipt_input_invalid")
        result=_receipt_type(schema_version=d["schema_version"],receipt_id=d["receipt_id"],policy_id=d["policy_id"],policy_sha256=d["policy_sha256"],authority_id=d["authority_id"],authority_sha256=d["authority_sha256"],temporary_root_id_sha256=d["temporary_root_id_sha256"],temporary_root_marker_sha256=d["temporary_root_marker_sha256"],action_status=d["action_status"],failure_code=d["failure_code"],failure_artifact_id=d["failure_artifact_id"],pre_inventory_sha256=d["pre_inventory_sha256"],post_inventory_sha256=d["post_inventory_sha256"],artifact_results=tuple(dict(validate_result(x)) for x in d["artifact_results"]),external_egress_allowed=d["external_egress_allowed"],model_loaded=d["model_loaded"],run_occurred=d["run_occurred"],remote_actions=dict(d["remote_actions"]),local_fixture_scope=dict(d["local_fixture_scope"]))
        validate_receipt(result); return result
    def receipt_from_bytes(cls,raw):
        if type(raw) is not bytes: raise _error("cleanup_receipt_bytes_invalid")
        try: result=receipt_from_dict(cls,_loads(raw.decode("utf-8")))
        except (UnicodeDecodeError,_decode_error) as exc: raise _error("cleanup_receipt_bytes_invalid") from exc
        if receipt_bytes(result) != raw: raise _error("cleanup_receipt_bytes_noncanonical")
        return result
    def observe_pre(policy,root,artifacts):
        supplied={}; ok=len(artifacts)==len(policy.artifacts)
        for x in artifacts:
            if type(x) is not _artifact_type or x.artifact_id in supplied: ok=False
            else: supplied[x.artifact_id]=x
        rows=[]
        for declaration in policy.artifacts:
            d=decl(declaration); base={"artifact_id":d["artifact_id"],"artifact_class":d["artifact_class"],"relative_path_sha256":d["relative_path_sha256"]}; x=supplied.get(d["artifact_id"])
            if x is None or x.artifact_class != d["artifact_class"]: rows.append({**base,"observation":"input_mismatch","byte_length":None,"content_sha256":None}); ok=False; continue
            try: live=_metadata(root,x)
            except _error: rows.append({**base,"observation":"present_or_unsafe","byte_length":None,"content_sha256":None}); ok=False; continue
            if live != d: rows.append({**base,"observation":"drifted","byte_length":live["byte_length"],"content_sha256":live["content_sha256"]}); ok=False
            else: rows.append({**base,"observation":"present","byte_length":live["byte_length"],"content_sha256":live["content_sha256"]})
        if set(supplied) != {decl(x)["artifact_id"] for x in policy.artifacts}: ok=False
        return rows,ok
    def input_matches(policy,artifacts):
        if len(artifacts) != len(policy.artifacts): return False
        supplied={}
        for x in artifacts:
            if type(x) is not _artifact_type or x.artifact_id in supplied: return False
            supplied[x.artifact_id]=x
        for declaration in policy.artifacts:
            d=decl(declaration); x=supplied.get(d["artifact_id"])
            if x is None or x.artifact_class != d["artifact_class"]: return False
            try: h=_hash(str(_resolver(_path_type('.'),x.relative_path,False)[0]).encode("utf-8"))
            except _error: return False
            if h != d["relative_path_sha256"]: return False
        return True
    def observe_post(policy,root,artifacts):
        supplied={x.artifact_id:x for x in artifacts if type(x) is _artifact_type}; rows=[]
        for declaration in policy.artifacts:
            d=decl(declaration); base={"artifact_id":d["artifact_id"],"artifact_class":d["artifact_class"],"relative_path_sha256":d["relative_path_sha256"]}; x=supplied.get(d["artifact_id"])
            try:
                _,candidate=_resolver(root,x.relative_path,False)
                if _absent(candidate): rows.append({**base,"observation":"absent","byte_length":None,"content_sha256":None})
                else:
                    live=_metadata(root,x); rows.append({**base,"observation":"present","byte_length":live["byte_length"],"content_sha256":live["content_sha256"]})
            except _error: rows.append({**base,"observation":"present_or_unsafe","byte_length":None,"content_sha256":None})
        return rows
    def template(policy,code,rows):
        observed={x["artifact_id"]:x["observation"] for x in rows}
        if code == "cleanup_precheck_failed": return tuple(result(x,"rejected_precheck","not_checked") for x in policy.artifacts)
        if code == "none": return tuple(result(x,"deleted","absent") for x in policy.artifacts)
        return tuple(result(x,"absent_after_action" if observed[decl(x)["artifact_id"]] == "absent" else "not_completed","absent" if observed[decl(x)["artifact_id"]] == "absent" else "present_or_unsafe") for x in policy.artifacts)
    def build(policy,status,code,failure_id,pre_hash,post_hash,rows):
        root={"schema_version":_receipt_schema,"policy_id":policy.policy_id,"policy_sha256":policy_hash(policy),"authority_id":policy.authority_id,"authority_sha256":policy.authority_sha256,"temporary_root_id_sha256":policy.temporary_root_id_sha256,"temporary_root_marker_sha256":policy.temporary_root_marker_sha256,"action_status":status,"failure_code":code,"failure_artifact_id":failure_id,"pre_inventory_sha256":pre_hash,"post_inventory_sha256":post_hash,"artifact_results":[dict(x) for x in rows],"external_egress_allowed":False,"model_loaded":False,"run_occurred":False,"remote_actions":dict(_remote),"local_fixture_scope":dict(_scope)}
        out=_receipt_type(receipt_id=_receipt_prefix+_hash(_canonical(root))[:20],artifact_results=tuple(root.pop("artifact_results")),**root); validate_receipt(out); return out
    def expected(policy,root,artifacts,check_pre):
        if check_pre:
            pre,ok=observe_pre(policy,root,artifacts)
            if not ok:
                h=inv(pre); return build(policy,"failed","cleanup_precheck_failed",None,h,h,template(policy,"cleanup_precheck_failed",pre))
        elif not input_matches(policy,artifacts):
            raise _error("cleanup_receipt_replay_invalid")
        post=observe_post(policy,root,artifacts); h=inv(post)
        if all(x["observation"] == "absent" for x in post): return build(policy,"completed","none",None,policy.expected_pre_inventory_sha256,h,template(policy,"none",post))
        first=next(x["artifact_id"] for x in post if x["observation"] != "absent")
        return build(policy,"failed","cleanup_not_completed",first,policy.expected_pre_inventory_sha256,h,template(policy,"cleanup_not_completed",post))
    def execute(policy,root_obj,artifacts,unlink):
        if type(policy) is not _policy_type or type(root_obj) is not _root_type or type(artifacts) not in (tuple,list): raise _error("cleanup_execution_input_invalid")
        validate_policy(policy)
        root,rh,mh=_root_validator(root_obj)
        if rh != policy.temporary_root_id_sha256 or mh != policy.temporary_root_marker_sha256: raise _error("cleanup_root_identity_drift")
        pre,ok=observe_pre(policy,root,artifacts)
        if not ok:
            h=inv(pre); return build(policy,"failed","cleanup_precheck_failed",None,h,h,template(policy,"cleanup_precheck_failed",pre))
        supplied={x.artifact_id:x for x in artifacts}
        for declaration in policy.artifacts:
            x=supplied[decl(declaration)["artifact_id"]]
            try:
                if _metadata(root,x) != decl(declaration): break
                _,path=_resolver(root,x.relative_path,True); unlink(path)
            except (_error,OSError): break
        return expected(policy,root,artifacts,False)
    def receipt_against(receipt,policy,root_obj,artifacts):
        validate_receipt(receipt)
        if type(policy) is not _policy_type or type(root_obj) is not _root_type or type(artifacts) not in (tuple,list): raise _error("cleanup_validation_input_invalid")
        validate_policy(policy); root,rh,mh=_root_validator(root_obj)
        if receipt.policy_id != policy.policy_id or receipt.policy_sha256 != policy_hash(policy) or receipt.authority_id != policy.authority_id or receipt.authority_sha256 != policy.authority_sha256 or receipt.temporary_root_id_sha256 != rh or receipt.temporary_root_marker_sha256 != mh: raise _error("cleanup_receipt_policy_binding_invalid")
        if receipt.failure_code == "cleanup_precheck_failed":
            candidate=expected(policy,root,artifacts,True)
        else:
            candidate=expected(policy,root,artifacts,False)
        if receipt_dict(receipt) != receipt_dict(candidate): raise _error("cleanup_receipt_replay_invalid")
    return locals()

def _captured_error_boundary(action, code, _error=LocalCleanupError):
    def wrapped(*args, **kwargs):
        try:
            return action(*args, **kwargs)
        except _error:
            raise
        except Exception as exc:
            raise _error(code) from exc
    return wrapped

_FIXED_ROOT_TYPE=LocalTemporaryRoot
_FIXED_POLICY_TYPE=LocalCleanupPolicy
_FIXED_RECEIPT_TYPE=LocalCleanupReceipt
_FIXED_AUTHORITY=_build_captured_cleanup_authority()
_FIXED_CREATE_ROOT=_CAPTURED_FILESYSTEM[0]
_FIXED_CREATE_POLICY=_FIXED_AUTHORITY["policy_create"]
_FIXED_EXECUTE=_FIXED_AUTHORITY["execute"]
_FIXED_POLICY_TYPE.create=classmethod(_FIXED_AUTHORITY["policy_create"])
_FIXED_POLICY_TYPE.from_dict=classmethod(_FIXED_AUTHORITY["policy_from_dict"])
_FIXED_POLICY_TYPE.from_bytes=classmethod(_FIXED_AUTHORITY["policy_from_bytes"])
_FIXED_POLICY_TYPE.validate=_FIXED_AUTHORITY["validate_policy"]
_FIXED_POLICY_TYPE.to_dict=_FIXED_AUTHORITY["policy_dict"]
_FIXED_POLICY_TYPE.canonical_bytes=_FIXED_AUTHORITY["policy_bytes"]
_FIXED_POLICY_TYPE.sha256=_FIXED_AUTHORITY["policy_hash"]
_FIXED_RECEIPT_TYPE.from_dict=classmethod(_FIXED_AUTHORITY["receipt_from_dict"])
_FIXED_RECEIPT_TYPE.from_bytes=classmethod(_FIXED_AUTHORITY["receipt_from_bytes"])
_FIXED_RECEIPT_TYPE.validate=_FIXED_AUTHORITY["validate_receipt"]
_FIXED_RECEIPT_TYPE.to_dict=_FIXED_AUTHORITY["receipt_dict"]
_FIXED_RECEIPT_TYPE.canonical_bytes=_FIXED_AUTHORITY["receipt_bytes"]
_FIXED_RECEIPT_TYPE.sha256=_FIXED_AUTHORITY["receipt_hash"]
_FIXED_RECEIPT_TYPE.validate_against=_FIXED_AUTHORITY["receipt_against"]

def _public_root_factory(factory):
    def create_local_temporary_root(parent: Path) -> LocalTemporaryRoot:
        """Create the only root type eligible for Tier A local cleanup."""
        return factory(parent)
    return create_local_temporary_root

def _public_policy_factory(factory, policy_type):
    def create_local_cleanup_policy(temporary_root: LocalTemporaryRoot, artifacts: Sequence[LocalTemporaryArtifact]) -> LocalCleanupPolicy:
        """Capture a policy for exact current files; no deletion is performed."""
        return factory(policy_type,temporary_root,artifacts)
    return create_local_cleanup_policy

def _public_executor(executor, unlink):
    def execute_local_cleanup(policy: LocalCleanupPolicy, temporary_root: LocalTemporaryRoot, artifacts: Sequence[LocalTemporaryArtifact]) -> LocalCleanupReceipt:
        """Remove only exact policy files using the captured local authority."""
        return executor(policy,temporary_root,artifacts,unlink)
    return execute_local_cleanup

create_local_temporary_root=_public_root_factory(_FIXED_CREATE_ROOT)
create_local_cleanup_policy=_public_policy_factory(_FIXED_CREATE_POLICY,_FIXED_POLICY_TYPE)
execute_local_cleanup=_public_executor(_FIXED_EXECUTE,_CAPTURED_FILESYSTEM[4])

def _private_scripted_executor(executor):
    def _execute_local_cleanup_scripted(policy: LocalCleanupPolicy, temporary_root: LocalTemporaryRoot, artifacts: Sequence[LocalTemporaryArtifact], delete_file: Callable[[Path],None]) -> LocalCleanupReceipt:
        """Private test seam for a bounded local filesystem fault simulation."""
        return executor(policy,temporary_root,artifacts,delete_file)
    return _execute_local_cleanup_scripted

_execute_local_cleanup_scripted=_private_scripted_executor(_FIXED_EXECUTE)
