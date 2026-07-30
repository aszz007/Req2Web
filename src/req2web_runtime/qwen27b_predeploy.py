"""Strict no-GPU predeploy contract for the Qwen3.5-27B recovery pilot.

This module only validates local artifact identities before a fresh GPU action
gate.  It never downloads, loads, or invokes a model, opens a network
connection, or authorizes a remote action.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from req2web_runtime import autodl_repository_archive as repository_archive


PROFILE_SCHEMA = "req2web.runtime.qwen35_27b_recovery_profile.v1"
INVENTORY_SCHEMA = "req2web.runtime.qwen35_27b_predeploy_inventory.v1"
RUNTIME_INVENTORY_SCHEMA = "req2web.runtime.qwen35_27b_runtime_inventory.v2"
RUNTIME_PROBE_SCHEMA = "req2web.runtime.qwen35_27b_no_gpu_runtime_probe.v1"
MANIFEST_SCHEMA = "req2web.runtime.qwen35_27b_predeploy_manifest.v1"
READY_SCHEMA = "req2web.runtime.qwen35_27b_predeploy_ready_no_gpu.v1"

MODEL_ID = "Qwen3.5-27B"
MODEL_REPOSITORY = "Qwen/Qwen3.5-27B"
MODEL_REVISION = "fc05daec18b0a78c049392ed2e771dde82bdf654"
PROFILE_ID = "quality_recovery_pilot"
REQUIRED_CASE_IDS = ("path3-commerce-checkout", "path3-media-analysis")
PRIMARY_TRANSPORT = "autodl_same_region_data_disk_clone"
TRANSFER_COPIES = ("none", "aliyundrive_autopanel_backup")
OFFICIAL_MODEL_INVENTORY_MANIFEST = Path(__file__).with_name(
    "qwen35_27b_fc05daec_official_inventory.v1.json"
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_MODEL_FILES = (
    ".gitattributes", "LICENSE", "README.md", "chat_template.jinja",
    "config.json", "generation_config.json", "merges.txt",
    *(f"model.safetensors-{index:05d}-of-00011.safetensors" for index in range(1, 12)),
    "model.safetensors.index.json", "preprocessor_config.json", "tokenizer.json",
    "tokenizer_config.json", "video_preprocessor_config.json", "vocab.json",
)
_RUNTIME_STATIC_FILES = (
    "lib/python3.11/site-packages/torch/__init__.py",
    "lib/python3.11/site-packages/torch/version.py",
    "lib/python3.11/site-packages/torch-2.7.1+cu128.dist-info/METADATA",
    "lib/python3.11/site-packages/torch-2.7.1+cu128.dist-info/RECORD",
    "lib/python3.11/site-packages/transformers/__init__.py",
    "lib/python3.11/site-packages/transformers/dependency_versions_table.py",
    "lib/python3.11/site-packages/transformers-5.14.1.dist-info/METADATA",
    "lib/python3.11/site-packages/transformers-5.14.1.dist-info/RECORD",
)
RUNTIME_MAX_FILE_COUNT = 250_000
RUNTIME_MAX_ENTRY_COUNT = 250_000
RUNTIME_MAX_TOTAL_BYTES = 64 * 1024 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024



class Qwen27BPredeployError(ValueError):
    """The local no-GPU predeploy contract failed closed."""


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Qwen27BPredeployError("duplicate_json_key")
        result[key] = value
    return result


def _dump(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Qwen27BPredeployError("canonical_json_invalid") from exc


def _load(raw: bytes) -> Any:
    if type(raw) is not bytes:
        raise Qwen27BPredeployError("canonical_bytes_required")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=lambda _v: (_ for _ in ()).throw(Qwen27BPredeployError("non_finite_json_forbidden")))
    except UnicodeDecodeError as exc:
        raise Qwen27BPredeployError("canonical_bytes_not_utf8") from exc
    except Qwen27BPredeployError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise Qwen27BPredeployError("canonical_bytes_not_json") from exc


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _keys(value: Any, expected: Sequence[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or len(value) != len(expected) or set(value) != set(expected):
        raise Qwen27BPredeployError(f"{label}_exact_keys_invalid")
    return value


def _text(value: Any, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise Qwen27BPredeployError(f"{label}_not_nonempty_text")
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise Qwen27BPredeployError(f"{label}_invalid_integer")
    return value


def _hex(value: Any, label: str, pattern: re.Pattern[str] = _HEX64) -> str:
    value = _text(value, label)
    if not pattern.fullmatch(value):
        raise Qwen27BPredeployError(f"{label}_invalid_hex")
    return value


def _false(value: Any, label: str) -> bool:
    if type(value) is not bool or value:
        raise Qwen27BPredeployError(f"{label}_must_be_false")
    return False


def _identified(data: dict[str, Any], key: str) -> dict[str, Any]:
    result = copy.deepcopy(data)
    result[key] = "0" * 64
    result[key] = _sha(_dump(result))
    return result


def _validate_id(data: Mapping[str, Any], key: str) -> None:
    actual = _hex(data[key], key)
    body = copy.deepcopy(dict(data)); body[key] = "0" * 64
    if _sha(_dump(body)) != actual:
        raise Qwen27BPredeployError(f"{key}_invalid")


def _row(value: Any) -> dict[str, Any]:
    value = _keys(value, ("relative_path", "bytes", "sha256"), "inventory_row")
    path = _text(value["relative_path"], "inventory_relative_path")
    if path.startswith("/") or "\\" in path or "//" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise Qwen27BPredeployError("inventory_relative_path_invalid")
    return {"relative_path": path, "bytes": _integer(value["bytes"], "inventory_bytes"), "sha256": _hex(value["sha256"], "inventory_sha256")}


def _inventory_rows(rows: Any, expected_paths: Sequence[str] | None = None) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise Qwen27BPredeployError("inventory_rows_invalid")
    parsed = [_row(row) for row in rows]
    paths = tuple(row["relative_path"] for row in parsed)
    if paths != tuple(sorted(paths)) or len(set(paths)) != len(paths):
        raise Qwen27BPredeployError("inventory_order_or_uniqueness_invalid")
    if expected_paths is not None and set(paths) != set(expected_paths):
        raise Qwen27BPredeployError("model_loader_file_set_invalid")
    return parsed


def _inventory_data(kind: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    data = {
        "schema_version": INVENTORY_SCHEMA,
        "inventory_id": "0" * 64,
        "kind": kind,
        "files": rows,
        "tree_sha256": _sha(_dump(rows)),
        "total_bytes": sum(row["bytes"] for row in rows),
    }
    return _identified(data, "inventory_id")


def _official_model_inventory_authority() -> dict[str, Any]:
    try:
        raw = OFFICIAL_MODEL_INVENTORY_MANIFEST.read_bytes()
    except OSError as exc:
        raise Qwen27BPredeployError(
            "official_model_inventory_manifest_unavailable"
        ) from exc
    value = _keys(
        _load(raw),
        (
            "schema_version",
            "authority_id",
            "repository",
            "revision",
            "source",
            "files",
            "file_count",
            "total_bytes",
            "tree_sha256",
        ),
        "official_model_inventory",
    )
    if raw != _dump(value):
        raise Qwen27BPredeployError(
            "official_model_inventory_manifest_not_canonical"
        )
    if (
        value["schema_version"]
        != "req2web.runtime.qwen35_27b_official_inventory.v1"
        or value["repository"] != MODEL_REPOSITORY
        or value["revision"] != MODEL_REVISION
    ):
        raise Qwen27BPredeployError("official_model_inventory_identity_invalid")
    source = _keys(
        value["source"],
        ("metadata_api", "small_file_resolve_template", "method"),
        "official_model_inventory_source",
    )
    expected_metadata_api = (
        "https://huggingface.co/api/models/Qwen/Qwen3.5-27B/revision/"
        + MODEL_REVISION
        + "?blobs=true"
    )
    expected_resolve = (
        "https://huggingface.co/Qwen/Qwen3.5-27B/resolve/"
        + MODEL_REVISION
        + "/{relative_path}?download=true"
    )
    if (
        source["metadata_api"] != expected_metadata_api
        or source["small_file_resolve_template"] != expected_resolve
        or source["method"]
        != (
            "LFS files use official lfs.sha256 and lfs.size; non-LFS files "
            "use exact-revision resolve bytes with local SHA-256."
        )
    ):
        raise Qwen27BPredeployError("official_model_inventory_source_invalid")
    files = value["files"]
    if not isinstance(files, list):
        raise Qwen27BPredeployError("official_model_inventory_files_invalid")
    rows: list[dict[str, Any]] = []
    for file_value in files:
        file_value = _keys(
            file_value,
            ("relative_path", "bytes", "sha256", "sha256_source"),
            "official_model_inventory_file",
        )
        row = _row(
            {
                "relative_path": file_value["relative_path"],
                "bytes": file_value["bytes"],
                "sha256": file_value["sha256"],
            }
        )
        if file_value["sha256_source"] not in {
            "huggingface_lfs_oid_sha256",
            "exact_revision_resolve_bytes_sha256",
        }:
            raise Qwen27BPredeployError(
                "official_model_inventory_sha256_source_invalid"
            )
        rows.append(row)
    rows = _inventory_rows(rows, _MODEL_FILES)
    if (
        _integer(value["file_count"], "official_model_file_count")
        != len(rows)
        or _integer(value["total_bytes"], "official_model_total_bytes")
        != sum(row["bytes"] for row in rows)
        or _hex(value["tree_sha256"], "official_model_tree_sha256")
        != _sha(_dump(files))
    ):
        raise Qwen27BPredeployError("official_model_inventory_summary_invalid")
    body = copy.deepcopy(dict(value))
    body["authority_id"] = "0" * 64
    if _hex(value["authority_id"], "official_model_authority_id") != _sha(
        _dump(body)
    ):
        raise Qwen27BPredeployError("official_model_authority_id_invalid")
    inventory = _inventory_data("model_root", rows)
    return {
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "authority_id": value["authority_id"],
        "manifest_sha256": _sha(raw),
        "file_count": len(rows),
        "total_bytes": inventory["total_bytes"],
        "tree_sha256": inventory["tree_sha256"],
        "inventory": inventory,
    }


def _validate_inventory(data: Any, expected_kind: str | None = None) -> dict[str, Any]:
    value = _keys(data, ("schema_version", "inventory_id", "kind", "files", "tree_sha256", "total_bytes"), "inventory")
    if value["schema_version"] != INVENTORY_SCHEMA:
        raise Qwen27BPredeployError("inventory_schema_invalid")
    kind = _text(value["kind"], "inventory_kind")
    if expected_kind is not None and kind != expected_kind:
        raise Qwen27BPredeployError("inventory_kind_invalid")
    rows = _inventory_rows(value["files"], _MODEL_FILES if kind == "model_root" else None)
    if _hex(value["tree_sha256"], "inventory_tree_sha256") != _sha(_dump(rows)):
        raise Qwen27BPredeployError("inventory_tree_sha256_invalid")
    if _integer(value["total_bytes"], "inventory_total_bytes") != sum(row["bytes"] for row in rows):
        raise Qwen27BPredeployError("inventory_total_bytes_invalid")
    _validate_id(value, "inventory_id")
    if kind == "model_root":
        official = _official_model_inventory_authority()["inventory"]
        if dict(value) != official:
            raise Qwen27BPredeployError(
                "model_official_inventory_mismatch"
            )
    return copy.deepcopy(dict(value))


def _repository_archive_authority(value: Any) -> dict[str, Any]:
    value = _keys(
        value,
        (
            "manifest_id",
            "manifest_sha256",
            "commit_sha",
            "tree_sha",
            "archive_byte_length",
            "archive_sha256",
        ),
        "repository_archive_authority",
    )
    return {
        "manifest_id": _hex(value["manifest_id"], "archive_manifest_id"),
        "manifest_sha256": _hex(
            value["manifest_sha256"], "archive_manifest_sha256"
        ),
        "commit_sha": _hex(value["commit_sha"], "archive_commit_sha", _HEX40),
        "tree_sha": _hex(value["tree_sha"], "archive_tree_sha", _HEX40),
        "archive_byte_length": _integer(
            value["archive_byte_length"], "archive_byte_length", minimum=1
        ),
        "archive_sha256": _hex(
            value["archive_sha256"], "archive_sha256"
        ),
    }


def validate_repository_archive_authority(
    repository_archive_path: str | os.PathLike[str],
    repository_archive_manifest_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Replay the accepted filtered-archive authority and return its binding."""

    try:
        manifest = repository_archive.validate_archive_file(
            repository_archive_path,
            repository_archive_manifest_path,
        )
    except repository_archive.RepositoryArchiveError as exc:
        raise Qwen27BPredeployError(
            "repository_archive_authority_invalid"
        ) from exc
    data = manifest.to_dict()
    return _repository_archive_authority(
        {
            "manifest_id": data["manifest_id"],
            "manifest_sha256": manifest.sha256(),
            "commit_sha": data["commit_sha"],
            "tree_sha": data["tree_sha"],
            "archive_byte_length": data["archive"]["byte_length"],
            "archive_sha256": data["archive"]["sha256"],
        }
    )


def _path_root(path_value: str | os.PathLike[str]) -> Path:
    path = Path(path_value)
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise Qwen27BPredeployError("artifact_root_unavailable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise Qwen27BPredeployError("artifact_root_not_real_directory")
    return path


def _file_row(path: Path, relative: str) -> dict[str, Any]:
    try:
        file_stat = os.lstat(path)
    except OSError as exc:
        raise Qwen27BPredeployError("artifact_file_unavailable") from exc
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise Qwen27BPredeployError("artifact_file_not_regular")
    digest = hashlib.sha256()
    observed_bytes = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
                observed_bytes += len(chunk)
    except OSError as exc:
        raise Qwen27BPredeployError("artifact_file_unavailable") from exc
    if observed_bytes != file_stat.st_size:
        raise Qwen27BPredeployError("artifact_file_changed_during_inventory")
    return {
        "relative_path": relative,
        "bytes": file_stat.st_size,
        "sha256": digest.hexdigest(),
    }


def collect_model_root_inventory(model_root: str | os.PathLike[str]) -> "Qwen27BPredeployInventory":
    root = _path_root(model_root)
    entries = list(root.iterdir())
    if {entry.name for entry in entries} != set(_MODEL_FILES):
        raise Qwen27BPredeployError("model_loader_file_set_invalid")
    rows = sorted((_file_row(root / name, name) for name in _MODEL_FILES), key=lambda row: row["relative_path"])
    actual = _inventory_data("model_root", rows)
    official = _official_model_inventory_authority()["inventory"]
    if actual != official:
        raise Qwen27BPredeployError("model_official_inventory_mismatch")
    return Qwen27BPredeployInventory(actual)


def collect_directory_inventory(root_value: str | os.PathLike[str], kind: str) -> "Qwen27BPredeployInventory":
    root = _path_root(root_value)
    rows: list[dict[str, Any]] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            if stat.S_ISLNK(os.lstat(current_path / directory).st_mode):
                raise Qwen27BPredeployError("artifact_directory_symlink_forbidden")
        for filename in filenames:
            path = current_path / filename
            rows.append(_file_row(path, path.relative_to(root).as_posix()))
    if not rows:
        raise Qwen27BPredeployError("artifact_directory_empty")
    rows.sort(key=lambda row: row["relative_path"])
    return Qwen27BPredeployInventory(_inventory_data(_text(kind, "inventory_kind"), rows))


def collect_file_inventory(path_value: str | os.PathLike[str], kind: str) -> "Qwen27BPredeployInventory":
    path = Path(path_value)
    return Qwen27BPredeployInventory(_inventory_data(_text(kind, "inventory_kind"), [_file_row(path, path.name)]))


@dataclass(frozen=True)
class _Record:
    data: Mapping[str, Any]

    def validate(self) -> None:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return copy.deepcopy(dict(self.data))

    def canonical_bytes(self) -> bytes:
        self.validate()
        return _dump(self.data)

    def sha256(self) -> str:
        self.validate()
        return _sha(self.canonical_bytes())


@dataclass(frozen=True)
class Qwen27BPredeployInventory(_Record):
    def validate(self) -> None:
        _validate_inventory(self.data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Qwen27BPredeployInventory":
        parsed = _validate_inventory(copy.deepcopy(dict(data)))
        return cls(parsed)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Qwen27BPredeployInventory":
        parsed = cls.from_dict(_load(raw))
        if raw != parsed.canonical_bytes():
            raise Qwen27BPredeployError("inventory_not_canonical")
        return parsed


def create_profile() -> "Qwen27BRecoveryProfile":
    data = {
        "schema_version": PROFILE_SCHEMA, "profile_id": "0" * 64,
        "model": {"id": MODEL_ID, "repository": MODEL_REPOSITORY, "revision": MODEL_REVISION},
        "execution": {"gpu_name": "NVIDIA RTX PRO 6000 Blackwell", "gpu_vram_gb": 96, "device": "cuda:0", "device_index": 0, "dtype": "bf16", "quantization": "none", "cpu_offload": False, "device_map": "none", "thinking": False, "decode": "greedy", "max_input_tokens": 8192, "max_new_tokens": 4096, "provider_calls_per_case": 1, "retry_count": 0},
        "cases": list(REQUIRED_CASE_IDS),
        "boundary": {"d17_path": "path_3", "h1_eligible": False, "formal_quality_claim": False, "browser_evidence": False, "evidence_use_claim": False},
        "transport": {
            "primary_transport": PRIMARY_TRANSPORT,
            "optional_copy_values": list(TRANSFER_COPIES),
        },
    }
    return Qwen27BRecoveryProfile(_identified(data, "profile_id"))


@dataclass(frozen=True)
class Qwen27BRecoveryProfile(_Record):
    def validate(self) -> None:
        value = _keys(self.data, ("schema_version", "profile_id", "model", "execution", "cases", "boundary", "transport"), "profile")
        if value["schema_version"] != PROFILE_SCHEMA:
            raise Qwen27BPredeployError("profile_schema_invalid")
        _keys(value["model"], ("id", "repository", "revision"), "profile_model")
        if value["model"] != {"id": MODEL_ID, "repository": MODEL_REPOSITORY, "revision": MODEL_REVISION}:
            raise Qwen27BPredeployError("profile_model_invalid")
        expected_execution = create_profile().data["execution"] if value.get("profile_id") != "0" * 64 else None
        if expected_execution is not None and value["execution"] != expected_execution:
            raise Qwen27BPredeployError("profile_execution_invalid")
        if value["cases"] != list(REQUIRED_CASE_IDS):
            raise Qwen27BPredeployError("profile_cases_invalid")
        if value["boundary"] != {"d17_path": "path_3", "h1_eligible": False, "formal_quality_claim": False, "browser_evidence": False, "evidence_use_claim": False}:
            raise Qwen27BPredeployError("profile_boundary_invalid")
        if value["transport"] != {
            "primary_transport": PRIMARY_TRANSPORT,
            "optional_copy_values": list(TRANSFER_COPIES),
        }:
            raise Qwen27BPredeployError("profile_transport_invalid")
        _validate_id(value, "profile_id")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Qwen27BRecoveryProfile":
        result = cls(copy.deepcopy(_load(raw))); result.validate()
        if raw != result.canonical_bytes(): raise Qwen27BPredeployError("profile_not_canonical")
        return result


def _runtime_interpreter(root: Path) -> tuple[Path, str]:
    interpreter = root / "bin" / "python"
    try:
        mode = os.lstat(interpreter).st_mode
    except OSError as exc:
        raise Qwen27BPredeployError("runtime_interpreter_unavailable") from exc
    if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
        raise Qwen27BPredeployError("runtime_interpreter_not_regular")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_interpreter = interpreter.resolve(strict=True)
        relative = resolved_interpreter.relative_to(resolved_root).as_posix()
    except (OSError, ValueError) as exc:
        raise Qwen27BPredeployError(
            "runtime_interpreter_symlink_escapes_root"
        ) from exc
    if not resolved_interpreter.is_file() or relative not in {
        "bin/python",
        "bin/python3.11",
    }:
        raise Qwen27BPredeployError("runtime_interpreter_identity_invalid")
    return interpreter, relative


def _runtime_symlink_row(path: Path, root: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    try:
        raw_target = os.readlink(path)
        resolved = path.resolve(strict=True)
        resolved_relative = resolved.relative_to(root).as_posix()
        resolved_mode = os.lstat(resolved).st_mode
    except (OSError, RuntimeError, ValueError) as exc:
        raise Qwen27BPredeployError("runtime_symlink_target_invalid") from exc
    target_text = os.fsdecode(raw_target).replace("\\", "/")
    if not target_text or "\x00" in target_text:
        raise Qwen27BPredeployError("runtime_symlink_target_text_invalid")
    if stat.S_ISREG(resolved_mode):
        target_type = "file"
    elif stat.S_ISDIR(resolved_mode):
        target_type = "directory"
        try:
            resolved_parent = path.parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise Qwen27BPredeployError("runtime_symlink_target_invalid") from exc
        if resolved_parent == resolved or resolved_parent.is_relative_to(resolved):
            raise Qwen27BPredeployError("runtime_symlink_cycle_forbidden")
    else:
        raise Qwen27BPredeployError("runtime_symlink_special_target_forbidden")
    return {
        "relative_path": relative,
        "readlink_target": target_text,
        "resolved_relative_path": resolved_relative,
        "target_type": target_type,
    }


def _runtime_symlink_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise Qwen27BPredeployError("runtime_symlink_rows_invalid")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        value = _keys(
            row,
            ("relative_path", "readlink_target", "resolved_relative_path", "target_type"),
            "runtime_symlink_row",
        )
        relative_path = _text(value["relative_path"], "runtime_symlink_relative_path")
        resolved_relative_path = _text(
            value["resolved_relative_path"], "runtime_symlink_resolved_relative_path"
        )
        for candidate, label in (
            (relative_path, "runtime_symlink_relative_path"),
            (resolved_relative_path, "runtime_symlink_resolved_relative_path"),
        ):
            if (
                candidate.startswith("/")
                or "\\" in candidate
                or "//" in candidate
                or any(part in {"", ".", ".."} for part in candidate.split("/"))
            ):
                raise Qwen27BPredeployError(f"{label}_invalid")
        readlink_target = _text(value["readlink_target"], "runtime_symlink_readlink_target")
        if "\\" in readlink_target or "\x00" in readlink_target:
            raise Qwen27BPredeployError("runtime_symlink_readlink_target_invalid")
        target_type = value["target_type"]
        if target_type not in {"file", "directory"}:
            raise Qwen27BPredeployError("runtime_symlink_target_type_invalid")
        parsed.append(
            {
                "relative_path": relative_path,
                "readlink_target": readlink_target,
                "resolved_relative_path": resolved_relative_path,
                "target_type": target_type,
            }
        )
    paths = tuple(row["relative_path"] for row in parsed)
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise Qwen27BPredeployError("runtime_symlink_order_or_uniqueness_invalid")
    return parsed


def collect_runtime_root_inventory(
    runtime_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Collect the complete regular-file tree and bounded internal symlinks."""

    if not isinstance(runtime_root, (str, os.PathLike)):
        raise Qwen27BPredeployError("runtime_root_path_required")
    root = _path_root(runtime_root).resolve(strict=True)
    _, interpreter_relative = _runtime_interpreter(root)
    rows: list[dict[str, Any]] = []
    symlink_rows: list[dict[str, Any]] = []
    ordinary_directories: set[str] = set()
    total_bytes = 0
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        ordinary_directories.add(current_path.relative_to(root).as_posix())
        directories.sort()
        filenames.sort()
        retained_directories: list[str] = []
        for directory in directories:
            directory_path = current_path / directory
            try:
                mode = os.lstat(directory_path).st_mode
            except OSError as exc:
                raise Qwen27BPredeployError(
                    "runtime_tree_entry_unavailable"
                ) from exc
            if stat.S_ISLNK(mode):
                symlink_rows.append(_runtime_symlink_row(directory_path, root))
                continue
            if not stat.S_ISDIR(mode):
                raise Qwen27BPredeployError(
                    "runtime_non_directory_entry_forbidden"
                )
            retained_directories.append(directory)
        directories[:] = retained_directories
        for filename in filenames:
            path = current_path / filename
            relative = path.relative_to(root).as_posix()
            try:
                file_stat = os.lstat(path)
            except OSError as exc:
                raise Qwen27BPredeployError(
                    "runtime_tree_entry_unavailable"
                ) from exc
            if stat.S_ISLNK(file_stat.st_mode):
                symlink_rows.append(_runtime_symlink_row(path, root))
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                raise Qwen27BPredeployError(
                    "runtime_non_regular_file_forbidden"
                )
            if len(rows) + 1 > RUNTIME_MAX_FILE_COUNT:
                raise Qwen27BPredeployError(
                    "runtime_inventory_file_count_cap_exceeded"
                )
            total_bytes += file_stat.st_size
            if total_bytes > RUNTIME_MAX_TOTAL_BYTES:
                raise Qwen27BPredeployError(
                    "runtime_inventory_total_bytes_cap_exceeded"
                )
            digest = hashlib.sha256()
            observed_bytes = 0
            try:
                with path.open("rb") as handle:
                    while chunk := handle.read(_HASH_CHUNK_BYTES):
                        digest.update(chunk)
                        observed_bytes += len(chunk)
            except OSError as exc:
                raise Qwen27BPredeployError("runtime_file_unavailable") from exc
            if observed_bytes != file_stat.st_size:
                raise Qwen27BPredeployError(
                    "runtime_file_changed_during_inventory"
                )
            rows.append(
                {
                    "relative_path": relative,
                    "bytes": file_stat.st_size,
                    "sha256": digest.hexdigest(),
                }
            )
    rows.sort(key=lambda row: row["relative_path"])
    symlink_rows.sort(key=lambda row: row["relative_path"])
    if len(rows) + len(symlink_rows) > RUNTIME_MAX_ENTRY_COUNT:
        raise Qwen27BPredeployError("runtime_inventory_entry_count_cap_exceeded")
    file_paths = {row["relative_path"] for row in rows}
    symlink_paths = {row["relative_path"] for row in symlink_rows}
    if file_paths & symlink_paths:
        raise Qwen27BPredeployError("runtime_inventory_entry_path_overlap")
    for row in symlink_rows:
        target = row["resolved_relative_path"]
        if row["target_type"] == "file" and target not in file_paths:
            raise Qwen27BPredeployError("runtime_symlink_file_target_not_in_inventory")
        if row["target_type"] == "directory" and (
            target not in ordinary_directories
            or not any(path.startswith(target + "/") for path in file_paths)
        ):
            raise Qwen27BPredeployError(
                "runtime_symlink_directory_target_not_in_inventory"
            )
    if not rows or interpreter_relative not in file_paths:
        raise Qwen27BPredeployError(
            "runtime_interpreter_inventory_binding_invalid"
        )
    interpreter_link = next(
        (row for row in symlink_rows if row["relative_path"] == "bin/python"),
        None,
    )
    if interpreter_relative == "bin/python3.11":
        if (
            interpreter_link is None
            or interpreter_link["target_type"] != "file"
            or interpreter_link["resolved_relative_path"] != interpreter_relative
        ):
            raise Qwen27BPredeployError(
                "runtime_interpreter_symlink_binding_invalid"
            )
    elif interpreter_link is not None:
        raise Qwen27BPredeployError("runtime_interpreter_symlink_binding_invalid")
    normalized_root = root.as_posix()
    entry_tree = {"files": rows, "symlinks": symlink_rows}
    data = {
        "schema_version": RUNTIME_INVENTORY_SCHEMA,
        "inventory_id": "0" * 64,
        "runtime_root": normalized_root,
        "runtime_root_sha256": _sha(normalized_root.encode("utf-8")),
        "interpreter_relative_path": interpreter_relative,
        "python_version": "3.11.15",
        "torch_version": "2.7.1+cu128",
        "transformers_version": "5.14.1",
        "cuda_version": "12.8",
        "files": rows,
        "symlinks": symlink_rows,
        "file_count": len(rows),
        "symlink_count": len(symlink_rows),
        "entry_count": len(rows) + len(symlink_rows),
        "total_bytes": total_bytes,
        "max_file_count": RUNTIME_MAX_FILE_COUNT,
        "max_entry_count": RUNTIME_MAX_ENTRY_COUNT,
        "max_total_bytes": RUNTIME_MAX_TOTAL_BYTES,
        "tree_sha256": _sha(_dump(entry_tree)),
    }
    return _identified(data, "inventory_id")
def create_runtime_inventory(
    runtime_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Compatibility name for the live, root-owned runtime collector."""

    return collect_runtime_root_inventory(runtime_root)


def _runtime_inventory(raw: bytes) -> dict[str, Any]:
    value = _keys(
        _load(raw),
        (
            "schema_version",
            "inventory_id",
            "runtime_root",
            "runtime_root_sha256",
            "interpreter_relative_path",
            "python_version",
            "torch_version",
            "transformers_version",
            "cuda_version",
            "files",
            "symlinks",
            "file_count",
            "symlink_count",
            "entry_count",
            "total_bytes",
            "max_file_count",
            "max_entry_count",
            "max_total_bytes",
            "tree_sha256",
        ),
        "runtime_inventory",
    )
    if value["schema_version"] != RUNTIME_INVENTORY_SCHEMA or tuple(
        value[name]
        for name in (
            "python_version",
            "torch_version",
            "transformers_version",
            "cuda_version",
        )
    ) != ("3.11.15", "2.7.1+cu128", "5.14.1", "12.8"):
        raise Qwen27BPredeployError("runtime_inventory_version_invalid")
    runtime_root = _text(value["runtime_root"], "runtime_root")
    if not Path(runtime_root).is_absolute():
        raise Qwen27BPredeployError("runtime_root_not_absolute")
    if _hex(value["runtime_root_sha256"], "runtime_root_sha256") != _sha(
        runtime_root.encode("utf-8")
    ):
        raise Qwen27BPredeployError("runtime_root_binding_invalid")
    interpreter_relative = _text(
        value["interpreter_relative_path"], "runtime_interpreter_relative_path"
    )
    if interpreter_relative not in {"bin/python", "bin/python3.11"}:
        raise Qwen27BPredeployError("runtime_interpreter_identity_invalid")
    rows = _inventory_rows(value["files"])
    symlink_rows = _runtime_symlink_rows(value["symlinks"])
    file_paths = {row["relative_path"] for row in rows}
    symlink_paths = {row["relative_path"] for row in symlink_rows}
    if file_paths & symlink_paths:
        raise Qwen27BPredeployError("runtime_inventory_entry_path_overlap")
    for row in symlink_rows:
        target = row["resolved_relative_path"]
        if row["target_type"] == "file" and target not in file_paths:
            raise Qwen27BPredeployError("runtime_symlink_file_target_not_in_inventory")
        if row["target_type"] == "directory" and not any(
            path.startswith(target + "/") for path in file_paths
        ):
            raise Qwen27BPredeployError(
                "runtime_symlink_directory_target_not_in_inventory"
            )
    if interpreter_relative not in file_paths:
        raise Qwen27BPredeployError(
            "runtime_interpreter_inventory_binding_invalid"
        )
    interpreter_link = next(
        (row for row in symlink_rows if row["relative_path"] == "bin/python"),
        None,
    )
    if interpreter_relative == "bin/python3.11":
        if (
            interpreter_link is None
            or interpreter_link["target_type"] != "file"
            or interpreter_link["resolved_relative_path"] != interpreter_relative
        ):
            raise Qwen27BPredeployError(
                "runtime_interpreter_symlink_binding_invalid"
            )
    elif interpreter_link is not None:
        raise Qwen27BPredeployError("runtime_interpreter_symlink_binding_invalid")
    file_count = _integer(value["file_count"], "runtime_file_count", 1)
    symlink_count = _integer(value["symlink_count"], "runtime_symlink_count")
    entry_count = _integer(value["entry_count"], "runtime_entry_count", 1)
    total_bytes = _integer(value["total_bytes"], "runtime_total_bytes")
    if (
        file_count != len(rows)
        or symlink_count != len(symlink_rows)
        or entry_count != file_count + symlink_count
        or total_bytes != sum(row["bytes"] for row in rows)
        or file_count > RUNTIME_MAX_FILE_COUNT
        or entry_count > RUNTIME_MAX_ENTRY_COUNT
        or total_bytes > RUNTIME_MAX_TOTAL_BYTES
        or value["max_file_count"] != RUNTIME_MAX_FILE_COUNT
        or value["max_entry_count"] != RUNTIME_MAX_ENTRY_COUNT
        or value["max_total_bytes"] != RUNTIME_MAX_TOTAL_BYTES
    ):
        raise Qwen27BPredeployError(
            "runtime_inventory_bounds_or_summary_invalid"
        )
    entry_tree = {"files": rows, "symlinks": symlink_rows}
    if _hex(
        value["tree_sha256"], "runtime_inventory_tree_sha256"
    ) != _sha(_dump(entry_tree)):
        raise Qwen27BPredeployError("runtime_inventory_tree_invalid")
    _validate_id(value, "inventory_id")
    if raw != _dump(value):
        raise Qwen27BPredeployError("runtime_inventory_not_canonical")
    return copy.deepcopy(dict(value))

def _create_no_gpu_runtime_probe(
    runtime_inventory_bytes: bytes,
    *,
    observed_python: str = "3.11.15",
    observed_torch: str = "2.7.1+cu128",
    observed_transformers: str = "5.14.1",
    observed_cuda: str = "12.8",
    observed_cuda_device_count: int = 0,
    observed_sys_executable_relative_path: str | None = None,
) -> dict[str, Any]:
    runtime = _runtime_inventory(runtime_inventory_bytes)
    if observed_sys_executable_relative_path is None:
        observed_sys_executable_relative_path = runtime[
            "interpreter_relative_path"
        ]
    observed = {
        "python": observed_python,
        "torch": observed_torch,
        "transformers": observed_transformers,
        "cuda": observed_cuda,
        "cuda_device_count": observed_cuda_device_count,
        "sys_executable_relative_path": observed_sys_executable_relative_path,
    }
    expected = {
        "python": runtime["python_version"],
        "torch": runtime["torch_version"],
        "transformers": runtime["transformers_version"],
        "cuda": runtime["cuda_version"],
        "cuda_device_count": 0,
        "sys_executable_relative_path": runtime[
            "interpreter_relative_path"
        ],
    }
    if observed != expected:
        raise Qwen27BPredeployError("runtime_probe_observation_invalid")
    data = {
        "schema_version": RUNTIME_PROBE_SCHEMA,
        "probe_id": "0" * 64,
        "runtime_inventory_sha256": _sha(runtime_inventory_bytes),
        "probe_origin": "live_subprocess",
        "probe_state": "no_gpu_runtime_probe",
        "observed": observed,
        "gpu_required": False,
        "gpu_observed": False,
        "model_loaded": False,
        "run_occurred": False,
        "provider_call_count": 0,
    }
    return _identified(data, "probe_id")


def _runtime_probe(raw: bytes, runtime_bytes: bytes) -> dict[str, Any]:
    runtime = _runtime_inventory(runtime_bytes)
    value = _keys(_load(raw), ("schema_version", "probe_id", "runtime_inventory_sha256", "probe_origin", "probe_state", "observed", "gpu_required", "gpu_observed", "model_loaded", "run_occurred", "provider_call_count"), "runtime_probe")
    observed = _keys(value["observed"], ("python", "torch", "transformers", "cuda", "cuda_device_count", "sys_executable_relative_path"), "runtime_probe_observed")
    expected_observed = {
        "python": runtime["python_version"],
        "torch": runtime["torch_version"],
        "transformers": runtime["transformers_version"],
        "cuda": runtime["cuda_version"],
        "cuda_device_count": 0,
        "sys_executable_relative_path": runtime[
            "interpreter_relative_path"
        ],
    }
    if observed != expected_observed:
        raise Qwen27BPredeployError("runtime_probe_observation_invalid")
    expected = {"schema_version": RUNTIME_PROBE_SCHEMA, "runtime_inventory_sha256": _sha(_dump(runtime)), "probe_origin": "live_subprocess", "probe_state": "no_gpu_runtime_probe", "observed": expected_observed, "gpu_required": False, "gpu_observed": False, "model_loaded": False, "run_occurred": False, "provider_call_count": 0}
    if any(value[name] != item for name, item in expected.items()): raise Qwen27BPredeployError("runtime_probe_invalid")
    _validate_id(value, "probe_id")
    if raw != _dump(value): raise Qwen27BPredeployError("runtime_probe_not_canonical")
    return copy.deepcopy(dict(value))


def probe_no_gpu_runtime(
    runtime_root: str | os.PathLike[str],
    runtime_inventory_bytes: bytes,
) -> dict[str, Any]:
    """Run the pinned interpreter once and record an actual no-GPU version probe."""

    runtime = _runtime_inventory(runtime_inventory_bytes)
    root = _path_root(runtime_root).resolve(strict=True)
    actual_inventory = collect_runtime_root_inventory(root)
    if _dump(actual_inventory) != runtime_inventory_bytes:
        raise Qwen27BPredeployError("runtime_inventory_live_mismatch")
    interpreter, interpreter_relative = _runtime_interpreter(root)
    script = (
        "import json,sys,torch,transformers;"
        "print(json.dumps({'python':f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}',"
        "'torch':torch.__version__,'transformers':transformers.__version__,"
        "'cuda':str(torch.version.cuda),'cuda_device_count':torch.cuda.device_count(),"
        "'sys_executable':sys.executable},"
        "sort_keys=True,separators=(',',':')))"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "CUDA_VISIBLE_DEVICES"}
    }
    try:
        completed = subprocess.run(
            [str(interpreter), "-I", "-s", "-E", "-c", script],
            cwd=str(root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Qwen27BPredeployError("runtime_probe_execution_failed") from exc
    if completed.returncode != 0:
        raise Qwen27BPredeployError("runtime_probe_execution_failed")
    try:
        observed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise Qwen27BPredeployError("runtime_probe_output_invalid") from exc
    if type(observed) is not dict:
        raise Qwen27BPredeployError("runtime_probe_output_invalid")
    try:
        observed_executable = Path(observed.get("sys_executable")).resolve(
            strict=True
        )
        observed_relative = observed_executable.relative_to(root).as_posix()
    except (OSError, TypeError, ValueError) as exc:
        raise Qwen27BPredeployError(
            "runtime_probe_sys_executable_outside_root"
        ) from exc
    if observed_relative != interpreter_relative:
        raise Qwen27BPredeployError(
            "runtime_probe_sys_executable_mismatch"
        )
    return _create_no_gpu_runtime_probe(
        runtime_inventory_bytes,
        observed_python=observed.get("python"),
        observed_torch=observed.get("torch"),
        observed_transformers=observed.get("transformers"),
        observed_cuda=observed.get("cuda"),
        observed_cuda_device_count=observed.get("cuda_device_count"),
        observed_sys_executable_relative_path=observed_relative,
    )


def _record_bytes(value: Any, type_: type[_Record], label: str) -> tuple[dict[str, Any], bytes]:
    if type(value) is type_:
        raw = value.canonical_bytes()
    elif type(value) is bytes:
        raw = value
    else:
        raise Qwen27BPredeployError(f"{label}_exact_type_or_bytes_required")
    parsed = type_.from_bytes(raw)
    return parsed.to_dict(), raw


def create_predeploy_manifest(
    expected_model_inventory: Any,
    runtime_inventory_bytes: bytes,
    runtime_probe_bytes: bytes,
    repository_archive_inventory: Any,
    case_bundle_inventory: Any,
    repository_archive_authority: Mapping[str, Any],
    case_bundle_archive_authority: Mapping[str, Any],
    *,
    source_commit: str,
    source_tree: str,
    transfer_copy: str = "none",
) -> "Qwen27BPredeployManifest":
    profile = create_profile(); model, _ = _record_bytes(expected_model_inventory, Qwen27BPredeployInventory, "model_inventory")
    archive, _ = _record_bytes(repository_archive_inventory, Qwen27BPredeployInventory, "repository_archive_inventory")
    cases, _ = _record_bytes(case_bundle_inventory, Qwen27BPredeployInventory, "case_bundle_inventory")
    if model["kind"] != "model_root" or archive["kind"] != "repository_archive" or cases["kind"] != "case_bundle": raise Qwen27BPredeployError("manifest_inventory_kind_invalid")
    runtime = _runtime_inventory(runtime_inventory_bytes); probe = _runtime_probe(runtime_probe_bytes, runtime_inventory_bytes)
    archive_authority = _repository_archive_authority(
        repository_archive_authority
    )
    case_archive_authority = _repository_archive_authority(
        case_bundle_archive_authority
    )
    if case_archive_authority != archive_authority:
        raise Qwen27BPredeployError(
            "case_bundle_archive_authority_mismatch"
        )
    if transfer_copy not in TRANSFER_COPIES: raise Qwen27BPredeployError("transfer_copy_invalid")
    source = {
        "commit": _hex(source_commit, "source_commit", _HEX40),
        "tree": _hex(source_tree, "source_tree", _HEX40),
    }
    archive_row = archive["files"][0]
    if (
        archive_authority["commit_sha"] != source["commit"]
        or archive_authority["tree_sha"] != source["tree"]
        or archive_authority["archive_byte_length"] != archive_row["bytes"]
        or archive_authority["archive_sha256"] != archive_row["sha256"]
    ):
        raise Qwen27BPredeployError(
            "repository_archive_authority_binding_invalid"
        )
    official_model_authority = _official_model_inventory_authority()
    official_model_authority.pop("inventory")
    data = {
        "schema_version": MANIFEST_SCHEMA,
        "manifest_id": "0" * 64,
        "status": "predeploy_manifest_no_gpu_not_action",
        "source": source,
        "repository_archive_authority": archive_authority,
        "case_bundle_archive_authority": case_archive_authority,
        "official_model_inventory_authority": official_model_authority,
        "profile": profile.to_dict(),
        "profile_sha256": profile.sha256(),
        "expected_model_inventory": model,
        "runtime_inventory": runtime,
        "runtime_inventory_sha256": _sha(runtime_inventory_bytes),
        "runtime_probe": probe,
        "runtime_probe_sha256": _sha(runtime_probe_bytes),
        "repository_archive_inventory": archive,
        "case_bundle_inventory": cases,
        "transport": {
            "primary_transport": PRIMARY_TRANSPORT,
            "selected_copy": transfer_copy,
        },
    }
    return Qwen27BPredeployManifest(_identified(data, "manifest_id"))


@dataclass(frozen=True)
class Qwen27BPredeployManifest(_Record):
    def validate(self) -> None:
        value = _keys(self.data, ("schema_version", "manifest_id", "status", "source", "repository_archive_authority", "case_bundle_archive_authority", "official_model_inventory_authority", "profile", "profile_sha256", "expected_model_inventory", "runtime_inventory", "runtime_inventory_sha256", "runtime_probe", "runtime_probe_sha256", "repository_archive_inventory", "case_bundle_inventory", "transport"), "manifest")
        if value["schema_version"] != MANIFEST_SCHEMA or value["status"] != "predeploy_manifest_no_gpu_not_action": raise Qwen27BPredeployError("manifest_header_invalid")
        source = _keys(value["source"], ("commit", "tree"), "manifest_source")
        _hex(source["commit"], "source_commit", _HEX40)
        _hex(source["tree"], "source_tree", _HEX40)
        archive_authority = _repository_archive_authority(
            value["repository_archive_authority"]
        )
        case_archive_authority = _repository_archive_authority(
            value["case_bundle_archive_authority"]
        )
        if case_archive_authority != archive_authority:
            raise Qwen27BPredeployError(
                "case_bundle_archive_authority_mismatch"
            )
        official_model_authority = _official_model_inventory_authority()
        official_model_authority.pop("inventory")
        if value["official_model_inventory_authority"] != official_model_authority:
            raise Qwen27BPredeployError(
                "official_model_inventory_authority_mismatch"
            )
        profile = Qwen27BRecoveryProfile(copy.deepcopy(value["profile"])); profile.validate()
        if _hex(value["profile_sha256"], "profile_sha256") != profile.sha256(): raise Qwen27BPredeployError("manifest_profile_binding_invalid")
        model = _validate_inventory(value["expected_model_inventory"], "model_root")
        archive = _validate_inventory(value["repository_archive_inventory"], "repository_archive")
        cases = _validate_inventory(value["case_bundle_inventory"], "case_bundle")
        archive_row = archive["files"][0]
        if (
            archive_authority["commit_sha"] != source["commit"]
            or archive_authority["tree_sha"] != source["tree"]
            or archive_authority["archive_byte_length"] != archive_row["bytes"]
            or archive_authority["archive_sha256"] != archive_row["sha256"]
        ):
            raise Qwen27BPredeployError(
                "repository_archive_authority_binding_invalid"
            )
        runtime_bytes = _dump(value["runtime_inventory"]); runtime = _runtime_inventory(runtime_bytes)
        if _hex(value["runtime_inventory_sha256"], "runtime_inventory_sha256") != _sha(runtime_bytes): raise Qwen27BPredeployError("manifest_runtime_binding_invalid")
        probe_bytes = _dump(value["runtime_probe"]); _runtime_probe(probe_bytes, runtime_bytes)
        if _hex(value["runtime_probe_sha256"], "runtime_probe_sha256") != _sha(probe_bytes): raise Qwen27BPredeployError("manifest_probe_binding_invalid")
        transport = _keys(value["transport"], ("primary_transport", "selected_copy"), "manifest_transport")
        if transport["primary_transport"] != PRIMARY_TRANSPORT or transport["selected_copy"] not in TRANSFER_COPIES: raise Qwen27BPredeployError("manifest_transport_invalid")
        _validate_id(value, "manifest_id")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Qwen27BPredeployManifest":
        result = cls(copy.deepcopy(_load(raw))); result.validate()
        if raw != result.canonical_bytes(): raise Qwen27BPredeployError("manifest_not_canonical")
        return result


def create_predeploy_ready_no_gpu(manifest: Any, model_root: str | os.PathLike[str], repository_archive: str | os.PathLike[str], case_bundle_root: str | os.PathLike[str], selected_copy: str) -> "Qwen27BPredeployReadyNoGpu":
    manifest_data, manifest_bytes = _record_bytes(manifest, Qwen27BPredeployManifest, "manifest")
    if selected_copy not in TRANSFER_COPIES or selected_copy != manifest_data["transport"]["selected_copy"]: raise Qwen27BPredeployError("ready_copy_binding_invalid")
    actual_model = collect_model_root_inventory(model_root).to_dict()
    actual_archive = collect_file_inventory(repository_archive, "repository_archive").to_dict()
    actual_cases = collect_directory_inventory(case_bundle_root, "case_bundle").to_dict()
    for actual, expected, label in ((actual_model, manifest_data["expected_model_inventory"], "model"), (actual_archive, manifest_data["repository_archive_inventory"], "archive"), (actual_cases, manifest_data["case_bundle_inventory"], "case_bundle")):
        if actual != expected: raise Qwen27BPredeployError(f"ready_{label}_inventory_mismatch")
    actual_runtime = collect_runtime_root_inventory(
        manifest_data["runtime_inventory"]["runtime_root"]
    )
    if actual_runtime != manifest_data["runtime_inventory"]:
        raise Qwen27BPredeployError("ready_runtime_inventory_mismatch")
    from req2web_runtime.qwen27b_case_bundle import validate_qwen27b_case_bundle

    bundle = validate_qwen27b_case_bundle(case_bundle_root)
    if (
        bundle.manifest["source"]["repository_archive_authority"]
        != manifest_data["repository_archive_authority"]
    ):
        raise Qwen27BPredeployError(
            "ready_case_bundle_archive_authority_mismatch"
        )
    claims = {"gpu_required": False, "gpu_observed": False, "model_loaded": False, "run_occurred": False, "provider_call_count": 0, "h1_eligible": False, "formal_quality_claim": False, "browser_evidence": False, "evidence_use_claim": False}
    data = {"schema_version": READY_SCHEMA, "ready_id": "0" * 64, "status": "predeploy_ready_no_gpu", "manifest": {"manifest_id": manifest_data["manifest_id"], "sha256": _sha(manifest_bytes)}, "transport": {"primary_transport": PRIMARY_TRANSPORT, "selected_copy": selected_copy}, "actual_model_inventory": actual_model, "actual_runtime_inventory": actual_runtime, "actual_repository_archive_inventory": actual_archive, "actual_case_bundle_inventory": actual_cases, "claims": claims, "next_gate": "fresh_gpu_action_gate_required"}
    return Qwen27BPredeployReadyNoGpu(_identified(data, "ready_id"))


@dataclass(frozen=True)
class Qwen27BPredeployReadyNoGpu(_Record):
    def validate(self) -> None:
        value = _keys(self.data, ("schema_version", "ready_id", "status", "manifest", "transport", "actual_model_inventory", "actual_runtime_inventory", "actual_repository_archive_inventory", "actual_case_bundle_inventory", "claims", "next_gate"), "ready")
        if value["schema_version"] != READY_SCHEMA or value["status"] != "predeploy_ready_no_gpu" or value["next_gate"] != "fresh_gpu_action_gate_required": raise Qwen27BPredeployError("ready_header_invalid")
        _keys(value["manifest"], ("manifest_id", "sha256"), "ready_manifest")
        _hex(value["manifest"]["manifest_id"], "ready_manifest_id"); _hex(value["manifest"]["sha256"], "ready_manifest_sha256")
        transport = _keys(value["transport"], ("primary_transport", "selected_copy"), "ready_transport")
        if transport["primary_transport"] != PRIMARY_TRANSPORT or transport["selected_copy"] not in TRANSFER_COPIES: raise Qwen27BPredeployError("ready_transport_invalid")
        _validate_inventory(value["actual_model_inventory"], "model_root")
        _runtime_inventory(_dump(value["actual_runtime_inventory"]))
        _validate_inventory(value["actual_repository_archive_inventory"], "repository_archive")
        _validate_inventory(value["actual_case_bundle_inventory"], "case_bundle")
        expected = {"gpu_required": False, "gpu_observed": False, "model_loaded": False, "run_occurred": False, "provider_call_count": 0, "h1_eligible": False, "formal_quality_claim": False, "browser_evidence": False, "evidence_use_claim": False}
        if value["claims"] != expected: raise Qwen27BPredeployError("ready_claims_invalid")
        _validate_id(value, "ready_id")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Qwen27BPredeployReadyNoGpu":
        result = cls(copy.deepcopy(_load(raw))); result.validate()
        if raw != result.canonical_bytes(): raise Qwen27BPredeployError("ready_not_canonical")
        return result


def validate_predeploy_ready_no_gpu_bytes(raw: bytes) -> Qwen27BPredeployReadyNoGpu:
    """Validate a canonical ready marker; it confers no GPU/run authorization."""
    return Qwen27BPredeployReadyNoGpu.from_bytes(raw)


def validate_predeploy_ready_no_gpu_against(
    ready: Any,
    manifest: Any,
    model_root: str | os.PathLike[str],
    repository_archive: str | os.PathLike[str],
    case_bundle_root: str | os.PathLike[str],
    selected_copy: str,
) -> Qwen27BPredeployReadyNoGpu:
    """Recompute every artifact and bind a ready marker to its owning manifest."""

    ready_data, ready_bytes = _record_bytes(
        ready, Qwen27BPredeployReadyNoGpu, "ready"
    )
    expected = create_predeploy_ready_no_gpu(
        manifest,
        model_root,
        repository_archive,
        case_bundle_root,
        selected_copy,
    )
    if ready_bytes != expected.canonical_bytes() or ready_data != expected.to_dict():
        raise Qwen27BPredeployError("ready_live_replay_mismatch")
    return expected


__all__ = (
    "MODEL_ID",
    "MODEL_REPOSITORY",
    "MODEL_REVISION",
    "PRIMARY_TRANSPORT",
    "PROFILE_ID",
    "REQUIRED_CASE_IDS",
    "TRANSFER_COPIES",
    "Qwen27BPredeployError",
    "Qwen27BPredeployInventory",
    "Qwen27BPredeployManifest",
    "Qwen27BPredeployReadyNoGpu",
    "Qwen27BRecoveryProfile",
    "collect_directory_inventory",
    "collect_file_inventory",
    "collect_model_root_inventory",
    "create_predeploy_manifest",
    "create_predeploy_ready_no_gpu",
    "create_profile",
    "create_runtime_inventory",
    "probe_no_gpu_runtime",
    "validate_predeploy_ready_no_gpu_against",
    "validate_predeploy_ready_no_gpu_bytes",
    "validate_repository_archive_authority",
)
