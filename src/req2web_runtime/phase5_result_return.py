"""Deterministic Phase 5 result tar and exact inventory validation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tarfile
from typing import Mapping, Sequence


SCHEMA_VERSION = "req2web.phase5.result_return_manifest.v1"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_id(prefix: str, value: object) -> str:
    return f"{prefix}-{sha256(_canonical(value)).hexdigest()}"


def _exact(value: object, keys: Sequence[str], name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError(f"{name} has invalid keys")
    return dict(value)


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _validate_inventory(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError("result return inventory must be a non-empty list")
    rows: list[dict[str, object]] = []
    paths: list[str] = []
    for raw in value:
        row = _exact(
            raw,
            ("relative_path", "sha256", "byte_length"),
            "result return inventory row",
        )
        relative = row["relative_path"]
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or "\\" in relative
            or ".." in Path(relative).parts
        ):
            raise ValueError("result return relative path is invalid")
        _digest(row["sha256"], "result return file hash")
        if (
            isinstance(row["byte_length"], bool)
            or not isinstance(row["byte_length"], int)
            or row["byte_length"] < 1
        ):
            raise ValueError("result return file length is invalid")
        paths.append(relative)
        rows.append(row)
    if paths != sorted(set(paths)):
        raise ValueError("result return inventory must be sorted and unique")
    return rows


def _validate_payload(value: object) -> dict[str, object]:
    manifest = _exact(
        value,
        (
            "manifest_id",
            "schema_version",
            "status",
            "run_id",
            "package_sha256",
            "run_summary_sha256",
            "tar_filename",
            "tar_sha256",
            "tar_byte_length",
            "file_count",
            "total_file_bytes",
            "inventory",
            "archive_policy",
            "action_state",
        ),
        "result return manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError("result return manifest schema drifted")
    if manifest["status"] != "deterministic_owner_custody_return_ready":
        raise ValueError("result return manifest status drifted")
    if not isinstance(manifest["run_id"], str) or not manifest["run_id"]:
        raise ValueError("result return run ID is invalid")
    for key in ("package_sha256", "run_summary_sha256", "tar_sha256"):
        _digest(manifest[key], f"result return {key}")
    if (
        not isinstance(manifest["tar_filename"], str)
        or not manifest["tar_filename"].endswith(".tar")
        or "/" in manifest["tar_filename"]
        or "\\" in manifest["tar_filename"]
    ):
        raise ValueError("result return tar filename is invalid")
    inventory = _validate_inventory(manifest["inventory"])
    if (
        manifest["file_count"] != len(inventory)
        or manifest["total_file_bytes"]
        != sum(int(row["byte_length"]) for row in inventory)
        or isinstance(manifest["tar_byte_length"], bool)
        or not isinstance(manifest["tar_byte_length"], int)
        or manifest["tar_byte_length"] < 1
    ):
        raise ValueError("result return aggregate counts drifted")
    policy = _exact(
        manifest["archive_policy"],
        (
            "format",
            "sorted_paths",
            "regular_files_only",
            "symlinks_allowed",
            "fixed_mtime",
            "fixed_uid_gid",
            "compression",
        ),
        "result return archive policy",
    )
    if policy != {
        "format": "ustar",
        "sorted_paths": True,
        "regular_files_only": True,
        "symlinks_allowed": False,
        "fixed_mtime": 0,
        "fixed_uid_gid": 0,
        "compression": "none",
    }:
        raise ValueError("result return archive policy drifted")
    state = _exact(
        manifest["action_state"],
        (
            "archive_created",
            "local_return_validated",
            "instance_released",
            "ssh_access_revoked",
            "formal_quality_claimed",
        ),
        "result return action state",
    )
    if state["archive_created"] is not True:
        raise ValueError("result return archive must be created")
    for key in (
        "local_return_validated",
        "instance_released",
        "ssh_access_revoked",
        "formal_quality_claimed",
    ):
        if state[key] is not False:
            raise ValueError(f"result return state {key} must remain false")
    normalized = {**manifest, "inventory": inventory}
    body = {key: normalized[key] for key in normalized if key != "manifest_id"}
    if normalized["manifest_id"] != _record_id("phase5-result-return", body):
        raise ValueError("result return manifest ID drifted")
    return normalized


@dataclass(frozen=True)
class Phase5ResultReturnManifest:
    canonical_json: str
    digest_sha256: str

    @classmethod
    def from_dict(cls, value: object) -> "Phase5ResultReturnManifest":
        payload = _validate_payload(value)
        canonical = _canonical(payload)
        result = cls(canonical.decode("utf-8"), sha256(canonical).hexdigest())
        result.validate()
        return result

    @classmethod
    def from_json_bytes(cls, value: bytes) -> "Phase5ResultReturnManifest":
        if not isinstance(value, bytes) or not value:
            raise ValueError("result return manifest JSON must be non-empty")
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("result return manifest JSON is invalid") from exc
        if _canonical(parsed) != value:
            raise ValueError("result return manifest JSON is not canonical")
        return cls.from_dict(parsed)

    def validate(self) -> None:
        try:
            parsed = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise ValueError("stored result return manifest is invalid") from exc
        canonical = _canonical(parsed)
        if canonical.decode("utf-8") != self.canonical_json:
            raise ValueError("stored result return manifest is not canonical")
        if sha256(canonical).hexdigest() != self.digest_sha256:
            raise ValueError("stored result return manifest digest drifted")
        _validate_payload(parsed)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):
            raise ValueError("stored result return manifest root is invalid")
        return value

    def canonical_json_bytes(self) -> bytes:
        self.validate()
        return self.canonical_json.encode("utf-8")

    def sha256(self) -> str:
        self.validate()
        return self.digest_sha256


def _source_inventory(result_root: Path) -> list[tuple[str, bytes]]:
    if (
        not isinstance(result_root, Path)
        or not result_root.is_dir()
        or result_root.is_symlink()
    ):
        raise ValueError("result return source root is invalid")
    rows: list[tuple[str, bytes]] = []
    for path in sorted(
        result_root.rglob("*"),
        key=lambda item: item.relative_to(result_root).as_posix(),
    ):
        if path.is_symlink():
            raise ValueError("result return source contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("result return source contains a non-regular file")
        relative = path.relative_to(result_root).as_posix()
        rows.append((relative, path.read_bytes()))
    if not rows:
        raise ValueError("result return source is empty")
    return rows


def create_phase5_result_return(
    *,
    result_root: Path,
    tar_path: Path,
    manifest_path: Path,
) -> Phase5ResultReturnManifest:
    if not (result_root / "run_summary.json").is_file():
        raise ValueError("result return source has no terminal run summary")
    for path, name in (
        (tar_path, "result tar"),
        (manifest_path, "result return manifest"),
    ):
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError(f"{name} path must be absolute")
        if path.exists() or not path.parent.is_dir() or path.parent.is_symlink():
            raise ValueError(f"{name} path must be a new file")
    if tar_path.parent != manifest_path.parent:
        raise ValueError("result tar and manifest must share one directory")
    rows = _source_inventory(result_root)
    summary_raw = (result_root / "run_summary.json").read_bytes()
    summary = json.loads(summary_raw.decode("utf-8"))
    if not isinstance(summary, Mapping):
        raise ValueError("result return summary is invalid")
    try:
        with tar_path.open("xb") as raw_stream:
            with tarfile.open(
                fileobj=raw_stream,
                mode="w",
                format=tarfile.USTAR_FORMAT,
            ) as archive:
                for relative, raw in rows:
                    info = tarfile.TarInfo(name=relative)
                    info.size = len(raw)
                    info.mode = 0o600
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    archive.addfile(info, io.BytesIO(raw))
            raw_stream.flush()
            os.fsync(raw_stream.fileno())
        tar_raw = tar_path.read_bytes()
        inventory = [
            {
                "relative_path": relative,
                "sha256": sha256(raw).hexdigest(),
                "byte_length": len(raw),
            }
            for relative, raw in rows
        ]
        body = {
            "schema_version": SCHEMA_VERSION,
            "status": "deterministic_owner_custody_return_ready",
            "run_id": summary["run_id"],
            "package_sha256": summary["package_sha256"],
            "run_summary_sha256": sha256(summary_raw).hexdigest(),
            "tar_filename": tar_path.name,
            "tar_sha256": sha256(tar_raw).hexdigest(),
            "tar_byte_length": len(tar_raw),
            "file_count": len(inventory),
            "total_file_bytes": sum(len(raw) for _, raw in rows),
            "inventory": inventory,
            "archive_policy": {
                "format": "ustar",
                "sorted_paths": True,
                "regular_files_only": True,
                "symlinks_allowed": False,
                "fixed_mtime": 0,
                "fixed_uid_gid": 0,
                "compression": "none",
            },
            "action_state": {
                "archive_created": True,
                "local_return_validated": False,
                "instance_released": False,
                "ssh_access_revoked": False,
                "formal_quality_claimed": False,
            },
        }
        manifest = Phase5ResultReturnManifest.from_dict(
            {
                "manifest_id": _record_id("phase5-result-return", body),
                **body,
            }
        )
        descriptor = os.open(
            manifest_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(manifest.canonical_json_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        return manifest
    except BaseException:
        for path in (manifest_path, tar_path):
            try:
                path.unlink()
            except OSError:
                pass
        raise


def validate_phase5_result_return(
    *,
    tar_path: Path,
    manifest_path: Path,
) -> Phase5ResultReturnManifest:
    manifest = Phase5ResultReturnManifest.from_json_bytes(
        manifest_path.read_bytes()
    )
    payload = manifest.to_dict()
    if tar_path.name != payload["tar_filename"]:
        raise ValueError("result return tar filename drifted")
    tar_raw = tar_path.read_bytes()
    if (
        sha256(tar_raw).hexdigest() != payload["tar_sha256"]
        or len(tar_raw) != payload["tar_byte_length"]
    ):
        raise ValueError("result return tar bytes drifted")
    inventory = payload["inventory"]
    expected = {row["relative_path"]: row for row in inventory}
    observed: dict[str, dict[str, object]] = {}
    with tarfile.open(tar_path, mode="r:") as archive:
        for member in archive.getmembers():
            if not member.isfile() or member.issym() or member.islnk():
                raise ValueError("result return tar contains a non-regular member")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("result return tar member is unreadable")
            raw = extracted.read()
            observed[member.name] = {
                "relative_path": member.name,
                "sha256": sha256(raw).hexdigest(),
                "byte_length": len(raw),
            }
    if sorted(observed) != sorted(expected):
        raise ValueError("result return tar inventory paths drifted")
    for path, row in expected.items():
        if observed[path] != row:
            raise ValueError("result return tar inventory bytes drifted")
    return manifest


__all__ = [
    "Phase5ResultReturnManifest",
    "create_phase5_result_return",
    "validate_phase5_result_return",
]
