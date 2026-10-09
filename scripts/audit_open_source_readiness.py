"""Audit the tracked tree for a future Req2Web public release.

The audit is read-only. It reports release blockers without printing suspected
secret values and makes no changes to the repository or Git history.
"""

from __future__ import annotations

import argparse
from fnmatch import fnmatch
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable

from repository_secret_rules import SECRET_PATTERNS, find_secret_locations


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "req2web.open_source_readiness_audit.v1"
DEFAULT_LARGE_FILE_BYTES = 1_000_000

REQUIRED_PUBLIC_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "SECURITY.md",
)

CLEANUP_EXACT_PATHS = (
    "AGENTS.md",
    "docs/delegated_task_acceptance_log.md",
    "docs/project_memory.md",
    "docs/repository_hygiene_checklist.md",
    "docs/repository_output_reference_manifest.md",
    "docs/sig_crowdEMP.docx",
    "docs/work_maintenance.md",
)

CLEANUP_PATTERNS = (
    "docs/stage3_*autodl*",
    "docs/stage3_*trusted_remote*",
    "docs/stage3_*approval*",
    "docs/stage3_*handoff*",
    "docs/phase4_*policy.json",
    "docs/phase4_*result.json",
    "docs/phase4_*timeout*",
    "docs/phase5_*owner*",
    "docs/phase5_*sealed*",
    "docs/phase5_*closeout*",
    "docs/phase5_*handoff*",
    "docs/phase5_*rerun*",
    "docs/phase5_*preopen*",
    "docs/phase5_rtx5090_*",
)

REVIEW_SUFFIXES = (
    ".bin",
    ".docx",
    ".jpg",
    ".jpeg",
    ".png",
    ".zip",
    ".tar",
    ".gz",
)

ABSOLUTE_PATH_PATTERNS = (
    (
        "windows_absolute_path",
        re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:Users|VSCodeProjects|ProgramData|Temp)[\\/]", re.IGNORECASE),
    ),
    ("unix_home_path", re.compile(r"(?<![A-Za-z0-9])/(?:home|Users)/[^/\s]+/")),
)


class ReadinessAuditError(RuntimeError):
    """Raised when the tracked tree cannot be audited safely."""


def _normalized_relative_path(value: str) -> str:
    path = value.replace("\\", "/").strip("/")
    if not path or path == "." or ".." in Path(path).parts:
        raise ReadinessAuditError(f"invalid tracked path: {value!r}")
    return path


def tracked_paths(repository_root: Path) -> list[str]:
    """Return the exact tracked file set from Git without following symlinks."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(repository_root), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReadinessAuditError("unable to read the Git tracked-file inventory") from exc
    try:
        decoded = completed.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReadinessAuditError("Git returned a non-UTF-8 tracked path") from exc
    return sorted(
        _normalized_relative_path(path)
        for path in decoded.split("\0")
        if path
    )


def _is_cleanup_candidate(path: str) -> bool:
    return path in CLEANUP_EXACT_PATHS or any(
        fnmatch(path, pattern) for pattern in CLEANUP_PATTERNS
    )


def _read_scannable_text(path: Path, size: int) -> str | None:
    if size > 25_000_000 or path.is_symlink() or not path.is_file():
        return None
    raw = path.read_bytes()
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _pattern_findings(
    relative_path: str,
    text: str,
    patterns: Iterable[tuple[str, re.Pattern[str]]],
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in patterns:
            if pattern.search(line):
                findings.append(
                    {"path": relative_path, "line": line_number, "kind": kind}
                )
    return findings


def audit_repository(
    repository_root: Path,
    *,
    inventory: Iterable[str] | None = None,
    large_file_bytes: int = DEFAULT_LARGE_FILE_BYTES,
) -> dict[str, object]:
    """Build a deterministic, value-redacted readiness report."""

    root = repository_root.resolve()
    source_inventory = tracked_paths(root) if inventory is None else inventory
    paths = sorted({_normalized_relative_path(path) for path in source_inventory})
    missing = sorted(path for path in REQUIRED_PUBLIC_FILES if path not in paths)
    cleanup = sorted(path for path in paths if _is_cleanup_candidate(path))
    binary_review: list[dict[str, object]] = []
    large_files: list[dict[str, object]] = []
    secret_findings: list[dict[str, object]] = []
    absolute_path_findings: list[dict[str, object]] = []

    for relative_path in paths:
        file_path = root / relative_path
        if not file_path.exists() or file_path.is_symlink() or not file_path.is_file():
            continue
        size = file_path.stat().st_size
        if size >= large_file_bytes:
            large_files.append({"path": relative_path, "byte_length": size})
        if file_path.suffix.lower() in REVIEW_SUFFIXES:
            binary_review.append({"path": relative_path, "byte_length": size})
        text = _read_scannable_text(file_path, size)
        if text is None:
            continue
        secret_findings.extend(
            {"path": relative_path, **finding}
            for finding in find_secret_locations(text)
        )
        absolute_path_findings.extend(
            _pattern_findings(relative_path, text, ABSOLUTE_PATH_PATTERNS)
        )

    blockers = {
        "missing_public_files": bool(missing),
        "tracked_cleanup_candidates": bool(cleanup),
        "suspected_secrets": bool(secret_findings),
        "absolute_machine_paths": bool(absolute_path_findings),
    }
    status = "blocked" if any(blockers.values()) else "ready_for_owner_review"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "tracked_file_count": len(paths),
        "blockers": blockers,
        "missing_public_files": missing,
        "cleanup_candidates": cleanup,
        "secret_findings": secret_findings,
        "absolute_path_findings": absolute_path_findings,
        "binary_review": binary_review,
        "large_files": large_files,
    }


def _text_summary(report: dict[str, object]) -> str:
    return "\n".join(
        (
            f"status: {report['status']}",
            f"tracked files: {report['tracked_file_count']}",
            f"missing public files: {len(report['missing_public_files'])}",
            f"cleanup candidates: {len(report['cleanup_candidates'])}",
            f"suspected secret locations: {len(report['secret_findings'])}",
            f"absolute path locations: {len(report['absolute_path_findings'])}",
            f"binary review files: {len(report['binary_review'])}",
            f"large files: {len(report['large_files'])}",
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read the tracked Req2Web tree and report public-release blockers. "
            "Suspected secret values are never printed."
        )
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--fail-on-blockers", action="store_true")
    parser.add_argument(
        "--large-file-bytes",
        type=int,
        default=DEFAULT_LARGE_FILE_BYTES,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.large_file_bytes <= 0:
        print("large-file threshold must be positive", file=sys.stderr)
        return 2
    try:
        report = audit_repository(
            args.root,
            large_file_bytes=args.large_file_bytes,
        )
    except (OSError, ReadinessAuditError) as exc:
        print(f"audit failed: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_text_summary(report))
    if args.fail_on_blockers and report["status"] == "blocked":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
