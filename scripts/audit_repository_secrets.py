"""Read-only, local-only credential audit of Git index, HEAD, or history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from repository_secret_rules import find_secret_locations


ROOT = Path(__file__).resolve().parents[1]
MAX_BLOB_BYTES = 25_000_000


class SecretAuditError(RuntimeError):
    """A Git inventory could not be checked completely."""


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], input=input_bytes,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SecretAuditError("Git read unavailable or timed out; raw diagnostic withheld") from exc
    if result.returncode:
        raise SecretAuditError("Git read failed; raw diagnostic withheld")
    return result.stdout


def _inventory(root: Path, source: str) -> dict[str, set[str]]:
    objects: dict[str, set[str]] = {}
    if source == "index":
        entries = _git(root, "ls-files", "--stage", "-z").decode("utf-8").split("\0")
        for entry in filter(None, entries):
            metadata, path = entry.split("\t", 1)
            mode, oid, stage = metadata.split()
            if stage != "0":
                raise SecretAuditError("Unmerged index; resolve it before scanning")
            if mode == "160000":
                raise SecretAuditError("Submodule inventory requires separate review")
            objects.setdefault(oid, set()).add(path)
    elif source == "head":
        entries = _git(root, "ls-tree", "-r", "-z", "HEAD").decode("utf-8").split("\0")
        for entry in filter(None, entries):
            metadata, path = entry.split("\t", 1)
            _, kind, oid = metadata.split()
            if kind != "blob":
                raise SecretAuditError("Submodule inventory requires separate review")
            objects.setdefault(oid, set()).add(path)
    else:
        for entry in _git(root, "rev-list", "--objects", "--all").decode("utf-8").splitlines():
            if " " in entry:
                oid, path = entry.split(" ", 1)
                objects.setdefault(oid, set()).add(path)
    return objects


def audit_git(root: Path, source: str = "index") -> dict[str, object]:
    """Scan exact Git blobs; do not trust a different working-tree copy."""
    if source not in {"index", "head", "history"}:
        raise SecretAuditError("Unsupported inventory source")
    objects = _inventory(root, source)
    metadata = _git(root, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                    input_bytes="".join(oid + "\n" for oid in objects).encode()).decode().splitlines()
    findings = []
    unchecked = []
    binary_count = 0
    text_count = 0
    blob_count = 0
    selected = []
    for entry in metadata:
        parts = entry.split()
        if len(parts) != 3:
            raise SecretAuditError("Incomplete Git object metadata")
        oid, kind, size = parts
        if kind != "blob":
            continue
        blob_count += 1
        if int(size) > MAX_BLOB_BYTES:
            unchecked.extend({"path": path, "reason": "size_limit", "blob": oid} for path in sorted(objects[oid]))
            continue
        selected.append((oid, int(size)))
    # One persistent Git reader avoids thousands of process launches. Request
    # and consume each object before sending the next, bounding memory and
    # avoiding pipe backpressure. Never forward payloads or raw stderr.
    process = subprocess.Popen(
        ["git", "-C", str(root), "cat-file", "--batch"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        for oid, size in selected:
            process.stdin.write((oid + "\n").encode())
            process.stdin.flush()
            header = process.stdout.readline().decode().split()
            if header != [oid, "blob", str(size)]:
                raise SecretAuditError("Git blob identity or length mismatch")
            raw = process.stdout.read(size)
            if len(raw) != size or process.stdout.read(1) != b"\n":
                raise SecretAuditError("Incomplete Git object stream")
            if b"\0" in raw:
                binary_count += 1
                continue
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                unchecked.extend({"path": path, "reason": "non_utf8", "blob": oid} for path in sorted(objects[oid]))
                continue
            text_count += 1
            for location in find_secret_locations(text):
                findings.extend({"path": path, "blob": oid, **location} for path in sorted(objects[oid]))
        process.stdin.close()
        if process.wait(timeout=60):
            raise SecretAuditError("Git object reader failed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
    return {
        "schema_version": "req2web.repository_secret_audit.v1", "source": source,
        "status": "review_required" if findings or unchecked else "no_candidates_found",
        "blob_count": blob_count, "text_blob_count": text_count,
        "binary_blob_count": binary_count, "unchecked": unchecked,
        "findings": findings, "credential_validity_tested": False,
        "network_used": False, "binary_payloads_scanned": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source", choices=("index", "head", "history"), default="index")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = audit_git(args.root.resolve(), args.source)
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired, SecretAuditError):
        print("Secret audit could not complete; raw diagnostic withheld", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    else:
        print(f"source: {report['source']}; status: {report['status']}")
        print(f"text blobs: {report['text_blob_count']}; binary blobs not scanned: {report['binary_blob_count']}")
        for finding in report["findings"]:
            print(f"{finding['path']}:{finding['line']}: {finding['kind']}")
        print(f"unchecked locations: {len(report['unchecked'])}")
    return 1 if report["findings"] or report["unchecked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
