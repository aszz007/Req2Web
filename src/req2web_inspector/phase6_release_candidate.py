"""Build and validate a deterministic, license-gated Phase 6 release candidate."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
import zipfile
from typing import Any, Mapping

from .phase6_replay import validate_phase6_reviewer_bundle


PHASE6_RELEASE_CANDIDATE_SCHEMA_VERSION = (
    "req2web.phase6.release_candidate.v1"
)
PHASE6_RELEASE_STATUS_SCHEMA_VERSION = "req2web.phase6.release_status.v1"
ARCHIVE_NAME = "Req2Web-Phase6-reviewer.zip"
ARCHIVE_ROOT = "Req2Web-Phase6-reviewer"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class Phase6ReleaseCandidateError(ValueError):
    """Raised when a release candidate is incomplete or inconsistent."""


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase6ReleaseCandidateError("value is not canonical JSON") from exc


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Phase6ReleaseCandidateError(f"{name} must be an object")
    return dict(value)


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or ".." in path.parts
        or str(path) != value
    ):
        raise Phase6ReleaseCandidateError("candidate path is not safely relative")
    return value


def _read_json(path: Path, name: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase6ReleaseCandidateError(f"{name} is unavailable")
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase6ReleaseCandidateError(f"{name} is not UTF-8 JSON") from exc
    return _mapping(value, name)


def _write_bytes(path: Path, raw: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise Phase6ReleaseCandidateError(f"candidate output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _regular_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise Phase6ReleaseCandidateError("release input must not contain symlinks")
        if path.is_file():
            files.append(path)
    files.sort(key=lambda path: path.relative_to(root).as_posix())
    if not files:
        raise Phase6ReleaseCandidateError("release input is empty")
    return files


def write_deterministic_reviewer_zip(bundle_root: Path, zip_path: Path) -> str:
    """Write a byte-stable ZIP using stored entries and fixed metadata."""

    bundle = Path(bundle_root).resolve(strict=True)
    target = Path(zip_path).resolve()
    if bundle.is_symlink() or not bundle.is_dir():
        raise Phase6ReleaseCandidateError("reviewer bundle root is invalid")
    if target.exists() or target.is_symlink():
        raise Phase6ReleaseCandidateError("release ZIP already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, mode="x", compression=zipfile.ZIP_STORED) as archive:
        for path in _regular_files(bundle):
            relative = path.relative_to(bundle).as_posix()
            _safe_relative(relative)
            info = zipfile.ZipInfo(
                filename=f"{ARCHIVE_ROOT}/{relative}",
                date_time=FIXED_ZIP_TIMESTAMP,
            )
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return sha256(target.read_bytes()).hexdigest()


def _validate_embedded_bundle(entries: Mapping[str, bytes]) -> None:
    prefix = f"{ARCHIVE_ROOT}/"
    manifest_name = prefix + "replay_manifest.json"
    materials_name = prefix + "MATERIALS.json"
    if manifest_name not in entries or materials_name not in entries:
        raise Phase6ReleaseCandidateError("ZIP omits reviewer control files")
    try:
        manifest = _mapping(
            json.loads(entries[manifest_name].decode("utf-8")),
            "embedded replay manifest",
        )
        materials = _mapping(
            json.loads(entries[materials_name].decode("utf-8")),
            "embedded materials inventory",
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase6ReleaseCandidateError("embedded controls are invalid JSON") from exc
    expected = {manifest_name}
    for raw in manifest.get("files", []):
        row = _mapping(raw, "embedded replay file")
        relative = _safe_relative(str(row.get("path")))
        name = prefix + relative
        if name not in entries:
            raise Phase6ReleaseCandidateError(f"ZIP omits reviewer file: {relative}")
        payload = entries[name]
        if (
            row.get("sha256") != sha256(payload).hexdigest()
            or row.get("byte_length") != len(payload)
        ):
            raise Phase6ReleaseCandidateError(f"ZIP reviewer file drifted: {relative}")
        expected.add(name)
    if set(entries) != expected:
        raise Phase6ReleaseCandidateError("ZIP reviewer inventory is not exact")
    if (
        materials.get("public_release_ready") is not False
        or materials.get("blocking_gate") != "owner_license_decision_required"
    ):
        raise Phase6ReleaseCandidateError("embedded license gate drifted")


def validate_deterministic_reviewer_zip(
    zip_path: Path,
    *,
    expected_bundle_root: Path | None = None,
) -> dict[str, Any]:
    """Validate ZIP path safety, fixed metadata, and embedded replay hashes."""

    path = Path(zip_path).resolve(strict=True)
    if path.is_symlink() or not path.is_file():
        raise Phase6ReleaseCandidateError("release ZIP is invalid")
    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(path, mode="r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if names != sorted(set(names)):
            raise Phase6ReleaseCandidateError("ZIP entries are not sorted and unique")
        for info in infos:
            _safe_relative(info.filename)
            if (
                info.is_dir()
                or info.date_time != FIXED_ZIP_TIMESTAMP
                or info.compress_type != zipfile.ZIP_STORED
                or (info.external_attr >> 16) != 0o100644
            ):
                raise Phase6ReleaseCandidateError("ZIP entry metadata drifted")
            entries[info.filename] = archive.read(info)
    _validate_embedded_bundle(entries)
    if expected_bundle_root is not None:
        bundle = Path(expected_bundle_root).resolve(strict=True)
        expected = {
            f"{ARCHIVE_ROOT}/{path.relative_to(bundle).as_posix()}": path.read_bytes()
            for path in _regular_files(bundle)
        }
        if entries != expected:
            raise Phase6ReleaseCandidateError("ZIP bytes disagree with reviewer bundle")
    return {
        "archive_name": path.name,
        "archive_sha256": sha256(path.read_bytes()).hexdigest(),
        "file_count": len(entries),
        "archive_byte_length": path.stat().st_size,
    }


_REPRODUCE_MD = f"""# Req2Web Phase 6 Reproduction

This candidate is a precomputed, local-only reviewer replay. It requires only
Python 3 and does not require a GPU, model, network connection, hidden material,
H1/gold data, or paid service.

1. Verify `{ARCHIVE_NAME}` against `SHA256SUMS.txt`.
2. Extract the ZIP with a standard ZIP tool.
3. Enter `{ARCHIVE_ROOT}`.
4. Run `python validate_bundle.py`.
5. Run `python serve_bundle.py --port 8765`.
6. Open `http://127.0.0.1:8765/`.

The validator fails closed on missing, extra, modified, or symlinked files.
The server binds to loopback only. The archive remains blocked from public
redistribution until the project owner makes an explicit license decision.
""".encode("utf-8")


def _candidate_inventory(root: Path) -> list[dict[str, Any]]:
    rows = []
    for relative in sorted(
        (ARCHIVE_NAME, "RELEASE_STATUS.json", "REPRODUCE.md", "SHA256SUMS.txt")
    ):
        path = root / relative
        raw = path.read_bytes()
        rows.append(
            {
                "path": relative,
                "sha256": sha256(raw).hexdigest(),
                "byte_length": len(raw),
            }
        )
    return rows


def build_phase6_release_candidate(
    *,
    reviewer_bundle_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Create one deterministic release candidate after reviewer validation."""

    bundle = Path(reviewer_bundle_root).resolve(strict=True)
    target = Path(output_root).resolve()
    if target.exists() or target.is_symlink():
        raise Phase6ReleaseCandidateError("release candidate root already exists")
    reviewer_manifest = validate_phase6_reviewer_bundle(bundle)
    build_root = target.with_name(f".{target.name}.building")
    if build_root.exists() or build_root.is_symlink():
        raise Phase6ReleaseCandidateError("release candidate build root already exists")
    build_root.mkdir(parents=True)
    try:
        archive_path = build_root / ARCHIVE_NAME
        archive_sha256 = write_deterministic_reviewer_zip(bundle, archive_path)
        archive_summary = validate_deterministic_reviewer_zip(
            archive_path,
            expected_bundle_root=bundle,
        )
        status = {
            "schema_version": PHASE6_RELEASE_STATUS_SCHEMA_VERSION,
            "status": "technically_reproducible_license_blocked",
            "non_human_phase6_engineering_status": "complete",
            "public_release_ready": False,
            "blocking_gate": "owner_license_decision_required",
            "human_work_remaining": [
                "owner license selection and legal review",
                (
                    "approved model prelabels, split two-human review, and "
                    "joint blind-overlap resolution"
                ),
                "video recording and paper writing",
            ],
            "claim_boundary": (
                "precomputed engineering replay only; not H1/gold, formal quality, "
                "broad generalization, production, or user-study evidence"
            ),
        }
        _write_bytes(build_root / "RELEASE_STATUS.json", _canonical_json_bytes(status))
        _write_bytes(build_root / "REPRODUCE.md", _REPRODUCE_MD)
        _write_bytes(
            build_root / "SHA256SUMS.txt",
            f"{archive_sha256}  {ARCHIVE_NAME}\n".encode("ascii"),
        )
        manifest = {
            "schema_version": PHASE6_RELEASE_CANDIDATE_SCHEMA_VERSION,
            "status": status["status"],
            "reviewer_replay_schema_version": reviewer_manifest["schema_version"],
            "archive": archive_summary,
            "public_release_ready": False,
            "blocking_gate": "owner_license_decision_required",
            "files": _candidate_inventory(build_root),
        }
        _write_bytes(
            build_root / "release_candidate_manifest.json",
            _canonical_json_bytes(manifest),
        )
        validate_phase6_release_candidate(build_root)
        build_root.rename(target)
        return validate_phase6_release_candidate(target)
    except Exception:
        shutil.rmtree(build_root, ignore_errors=True)
        raise


def validate_phase6_release_candidate(root: Path) -> dict[str, Any]:
    """Pure-read validation of the release candidate directory and ZIP."""

    candidate = Path(root).resolve(strict=True)
    if candidate.is_symlink() or not candidate.is_dir():
        raise Phase6ReleaseCandidateError("release candidate root is invalid")
    manifest = _read_json(
        candidate / "release_candidate_manifest.json",
        "release candidate manifest",
    )
    status = _read_json(candidate / "RELEASE_STATUS.json", "release status")
    if (
        manifest.get("schema_version") != PHASE6_RELEASE_CANDIDATE_SCHEMA_VERSION
        or manifest.get("status") != "technically_reproducible_license_blocked"
        or manifest.get("public_release_ready") is not False
        or manifest.get("blocking_gate") != "owner_license_decision_required"
        or status.get("schema_version") != PHASE6_RELEASE_STATUS_SCHEMA_VERSION
        or status.get("non_human_phase6_engineering_status") != "complete"
        or status.get("public_release_ready") is not False
        or status.get("blocking_gate") != "owner_license_decision_required"
    ):
        raise Phase6ReleaseCandidateError("release status boundary drifted")
    if manifest.get("files") != _candidate_inventory(candidate):
        raise Phase6ReleaseCandidateError("release candidate inventory drifted")
    actual_paths = sorted(
        path.name
        for path in candidate.iterdir()
        if path.is_file() and not path.is_symlink()
    )
    expected_paths = sorted(
        [
            ARCHIVE_NAME,
            "RELEASE_STATUS.json",
            "REPRODUCE.md",
            "SHA256SUMS.txt",
            "release_candidate_manifest.json",
        ]
    )
    if actual_paths != expected_paths or any(path.is_symlink() for path in candidate.iterdir()):
        raise Phase6ReleaseCandidateError("release candidate file set is not exact")
    archive = validate_deterministic_reviewer_zip(candidate / ARCHIVE_NAME)
    if archive != manifest.get("archive"):
        raise Phase6ReleaseCandidateError("release archive summary drifted")
    checksum = (candidate / "SHA256SUMS.txt").read_text(encoding="ascii")
    if checksum != f"{archive['archive_sha256']}  {ARCHIVE_NAME}\n":
        raise Phase6ReleaseCandidateError("release checksum drifted")
    return manifest


def extract_release_candidate_for_rehearsal(
    *,
    candidate_root: Path,
    extraction_root: Path,
) -> Path:
    """Safely extract one validated candidate into a new exact directory."""

    candidate = Path(candidate_root).resolve(strict=True)
    target = Path(extraction_root).resolve()
    if target.exists() or target.is_symlink():
        raise Phase6ReleaseCandidateError("rehearsal extraction root already exists")
    validate_phase6_release_candidate(candidate)
    target.mkdir(parents=True)
    try:
        with zipfile.ZipFile(candidate / ARCHIVE_NAME, mode="r") as archive:
            for info in archive.infolist():
                relative = _safe_relative(info.filename)
                destination = target / Path(relative)
                resolved_parent = destination.parent.resolve()
                if target != resolved_parent and target not in resolved_parent.parents:
                    raise Phase6ReleaseCandidateError("ZIP extraction escaped the root")
                destination.parent.mkdir(parents=True, exist_ok=True)
                _write_bytes(destination, archive.read(info))
        extracted_bundle = target / ARCHIVE_ROOT
        validate_phase6_reviewer_bundle(extracted_bundle)
        return extracted_bundle
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


__all__ = [
    "ARCHIVE_NAME",
    "ARCHIVE_ROOT",
    "Phase6ReleaseCandidateError",
    "build_phase6_release_candidate",
    "extract_release_candidate_for_rehearsal",
    "validate_deterministic_reviewer_zip",
    "validate_phase6_release_candidate",
    "write_deterministic_reviewer_zip",
]
