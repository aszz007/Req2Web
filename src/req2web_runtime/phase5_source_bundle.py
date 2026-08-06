"""Commit-bound Git bundle and manifest for the future no-card handoff.

The utility creates only a local Git bundle from the exact committed `main`
ref and a canonical hash manifest.  It does not stage, commit, push, connect
to SSH, upload, clone remotely, inspect H1/gold, or execute a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from typing import Mapping, Sequence


SCHEMA_VERSION = "req2web.phase5.source_bundle_manifest.v1"
SOURCE_REF = "refs/heads/main"


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


def _commit(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase 40-character commit")
    return value


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _run_git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"Git command failed closed: {detail}")
    return completed.stdout.strip()


def _validate_payload(value: object) -> dict[str, object]:
    manifest = _exact(
        value,
        (
            "manifest_id",
            "schema_version",
            "status",
            "source_ref",
            "source_commit",
            "source_tree",
            "bundle_filename",
            "bundle_sha256",
            "bundle_byte_length",
            "bundle_verify_completed",
            "clone_target_template",
            "transport",
            "action_state",
        ),
        "Phase 5 source bundle manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError("source bundle manifest schema drifted")
    if manifest["status"] != "local_commit_bound_bundle_ready_no_upload":
        raise ValueError("source bundle manifest status drifted")
    if manifest["source_ref"] != SOURCE_REF:
        raise ValueError("source bundle manifest ref drifted")
    _commit(manifest["source_commit"], "source bundle commit")
    _commit(manifest["source_tree"], "source bundle tree")
    filename = manifest["bundle_filename"]
    if (
        not isinstance(filename, str)
        or not filename.endswith(".bundle")
        or "/" in filename
        or "\\" in filename
    ):
        raise ValueError("source bundle filename is invalid")
    _digest(manifest["bundle_sha256"], "source bundle hash")
    if (
        isinstance(manifest["bundle_byte_length"], bool)
        or not isinstance(manifest["bundle_byte_length"], int)
        or manifest["bundle_byte_length"] < 1
    ):
        raise ValueError("source bundle byte length is invalid")
    if manifest["bundle_verify_completed"] is not True:
        raise ValueError("source bundle must be verified")
    if (
        manifest["clone_target_template"]
        != "/root/autodl-tmp/req2web-phase5-qwen9b/repository-<commit-prefix>"
        or manifest["transport"] != "git_bundle_then_local_clone"
    ):
        raise ValueError("source bundle transport binding drifted")
    state = _exact(
        manifest["action_state"],
        (
            "git_staged",
            "git_committed",
            "git_pushed",
            "remote_uploaded",
            "remote_cloned",
            "ssh_connected",
            "model_loaded",
            "holdout_executed",
        ),
        "source bundle action state",
    )
    if any(value is not False for value in state.values()):
        raise ValueError("source bundle action state must remain no-action")
    body = {key: manifest[key] for key in manifest if key != "manifest_id"}
    if manifest["manifest_id"] != _record_id("phase5-source-bundle", body):
        raise ValueError("source bundle manifest ID drifted")
    return manifest


@dataclass(frozen=True)
class Phase5SourceBundleManifest:
    canonical_json: str
    digest_sha256: str

    @classmethod
    def from_dict(cls, value: object) -> "Phase5SourceBundleManifest":
        payload = _validate_payload(value)
        canonical = _canonical(payload)
        result = cls(canonical.decode("utf-8"), sha256(canonical).hexdigest())
        result.validate()
        return result

    @classmethod
    def from_json_bytes(cls, value: bytes) -> "Phase5SourceBundleManifest":
        if not isinstance(value, bytes) or not value:
            raise ValueError("source bundle manifest JSON must be non-empty")
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("source bundle manifest JSON is invalid") from exc
        if _canonical(parsed) != value:
            raise ValueError("source bundle manifest JSON is not canonical")
        return cls.from_dict(parsed)

    def validate(self) -> None:
        try:
            parsed = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise ValueError("stored source bundle manifest is invalid") from exc
        canonical = _canonical(parsed)
        if canonical.decode("utf-8") != self.canonical_json:
            raise ValueError("stored source bundle manifest is not canonical")
        if sha256(canonical).hexdigest() != self.digest_sha256:
            raise ValueError("stored source bundle manifest digest drifted")
        _validate_payload(parsed)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):
            raise ValueError("stored source bundle manifest root is invalid")
        return value

    def canonical_json_bytes(self) -> bytes:
        self.validate()
        return self.canonical_json.encode("utf-8")

    def sha256(self) -> str:
        self.validate()
        return self.digest_sha256


def create_phase5_source_bundle(
    *,
    repo_root: Path,
    source_commit: str,
    bundle_path: Path,
    manifest_path: Path,
) -> Phase5SourceBundleManifest:
    """Create and verify the exact local bundle without any remote action."""

    source_commit = _commit(source_commit, "source action commit")
    if (
        not isinstance(repo_root, Path)
        or not repo_root.is_dir()
        or repo_root.is_symlink()
    ):
        raise ValueError("source repository root is invalid")
    repo_root = repo_root.resolve(strict=True)
    _run_git(repo_root, "rev-parse", "--git-dir")
    for path, name in (
        (bundle_path, "bundle output"),
        (manifest_path, "bundle manifest output"),
    ):
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError(f"{name} must be absolute")
        if path.exists() or not path.parent.is_dir() or path.parent.is_symlink():
            raise ValueError(f"{name} path is not a new regular-file target")
        try:
            path.resolve(strict=False).relative_to(repo_root)
        except ValueError:
            pass
        else:
            raise ValueError(f"{name} must remain outside the repository")
    if bundle_path.parent != manifest_path.parent:
        raise ValueError("bundle and manifest must share one handoff directory")
    head = _run_git(repo_root, "rev-parse", "HEAD")
    main = _run_git(repo_root, "rev-parse", SOURCE_REF)
    if head != source_commit or main != source_commit:
        raise ValueError("source action commit must equal HEAD and local main")
    tree = _commit(
        _run_git(repo_root, "rev-parse", f"{source_commit}^{{tree}}"),
        "source action tree",
    )
    try:
        _run_git(
            repo_root,
            "bundle",
            "create",
            str(bundle_path),
            SOURCE_REF,
        )
        _run_git(repo_root, "bundle", "verify", str(bundle_path))
        raw = bundle_path.read_bytes()
        if not raw:
            raise ValueError("created source bundle is empty")
        body = {
            "schema_version": SCHEMA_VERSION,
            "status": "local_commit_bound_bundle_ready_no_upload",
            "source_ref": SOURCE_REF,
            "source_commit": source_commit,
            "source_tree": tree,
            "bundle_filename": bundle_path.name,
            "bundle_sha256": sha256(raw).hexdigest(),
            "bundle_byte_length": len(raw),
            "bundle_verify_completed": True,
            "clone_target_template": (
                "/root/autodl-tmp/req2web-phase5-qwen9b/"
                "repository-<commit-prefix>"
            ),
            "transport": "git_bundle_then_local_clone",
            "action_state": {
                "git_staged": False,
                "git_committed": False,
                "git_pushed": False,
                "remote_uploaded": False,
                "remote_cloned": False,
                "ssh_connected": False,
                "model_loaded": False,
                "holdout_executed": False,
            },
        }
        manifest = Phase5SourceBundleManifest.from_dict(
            {
                "manifest_id": _record_id("phase5-source-bundle", body),
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
        for path in (manifest_path, bundle_path):
            try:
                path.unlink()
            except OSError:
                pass
        raise


def validate_phase5_source_bundle(
    *,
    repo_root: Path,
    bundle_path: Path,
    manifest_path: Path,
) -> Phase5SourceBundleManifest:
    manifest = Phase5SourceBundleManifest.from_json_bytes(
        manifest_path.read_bytes()
    )
    payload = manifest.to_dict()
    if bundle_path.name != payload["bundle_filename"]:
        raise ValueError("source bundle filename drifted")
    raw = bundle_path.read_bytes()
    if (
        sha256(raw).hexdigest() != payload["bundle_sha256"]
        or len(raw) != payload["bundle_byte_length"]
    ):
        raise ValueError("source bundle bytes drifted")
    _run_git(repo_root.resolve(strict=True), "bundle", "verify", str(bundle_path))
    return manifest


__all__ = [
    "Phase5SourceBundleManifest",
    "SOURCE_REF",
    "create_phase5_source_bundle",
    "validate_phase5_source_bundle",
]
