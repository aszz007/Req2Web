"""Build and validate the final internal Req2Web writer handoff ZIP.

The package preserves the stable Phase 6 reviewer materials from the earlier
handoff archive, replaces the framework snapshot with the current workspace,
and adds the frozen Phase 7 experiment code and compact result evidence.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = "Req2Web"
FIXED_ZIP_TIME = (2026, 9, 21, 0, 0, 0)

ROOT_FILES = (
    ".gitattributes",
    ".gitignore",
    "CONTRIBUTING.md",
    "README.md",
    "requirements-data.txt",
    "requirements-phase4-agent-lock.txt",
    "requirements-phase4-agent.txt",
)
SOURCE_DIRS = ("src", "scripts", "tests", "fixtures")
DOC_EXTENSIONS = {".md", ".json", ".svg"}
DATA_FILES = (
    "data/processed/selection_manifest.csv",
    "data/processed/rag/documents.jsonl",
    "data/processed/rag/index_manifest.json",
    "data/processed/rag/tfidf_index.json.gz",
)
REFERENCE_PREFIXES = (
    f"{PACKAGE_ROOT}/reviewer_bundle/",
    f"{PACKAGE_ROOT}/final_retrieval_metrics/",
    f"{PACKAGE_ROOT}/writer_reference/",
)
PHASE7_RESULT_FILES = (
    (
        "outputs/phase7_experiment1_v2/development-gate-v6-20260917-35786/"
        "measured-final/observation.json",
        "phase7_experiments/experiment1/source/observation.json",
    ),
    (
        "outputs/phase7_experiment1_v2/development-gate-v6-20260917-35786/"
        "measured-final/analysis.json",
        "phase7_experiments/experiment1/source/analysis.json",
    ),
    (
        "outputs/phase7_experiment1_v2/development-gate-v6-20260917-35786/"
        "measured-final/analysis.md",
        "phase7_experiments/experiment1/source/analysis.md",
    ),
    (
        "outputs/phase7_e1_ordered_decision_acceptance_v1/"
        "historical_secondary_analysis.json",
        "phase7_experiments/experiment1/frozen_acceptance/analysis.json",
    ),
    (
        "outputs/phase7_e1_ordered_decision_acceptance_v1/"
        "historical_secondary_analysis.md",
        "phase7_experiments/experiment1/frozen_acceptance/analysis.md",
    ),
    (
        "outputs/phase7_experiment2_comparison_v1/local-baselines-final/"
        "frozen_input_inventory.json",
        "phase7_experiments/experiment2/local/frozen_input_inventory.json",
    ),
    (
        "outputs/phase7_experiment2_comparison_v1/local-baselines-final/"
        "frozen_protocol.json",
        "phase7_experiments/experiment2/local/frozen_protocol.json",
    ),
    (
        "outputs/phase7_experiment2_comparison_v1/local-baselines-final/"
        "machine_summary.json",
        "phase7_experiments/experiment2/local/machine_summary.json",
    ),
    (
        "outputs/phase7_experiment2_comparison_v1/local-baselines-final/"
        "scored_observations.json",
        "phase7_experiments/experiment2/local/scored_observations.json",
    ),
    (
        "outputs/phase7_experiment2_comparison_v1/local-baselines-final/"
        "targeted_binding_checks.json",
        "phase7_experiments/experiment2/local/targeted_binding_checks.json",
    ),
    (
        "outputs/phase7_experiment2_comparison_v1/external-epsilon/run-1/"
        "frozen_protocol.json",
        "phase7_experiments/experiment2/external_epsilon/frozen_protocol.json",
    ),
    (
        "outputs/phase7_experiment2_comparison_v1/external-epsilon/run-1/"
        "observations.json",
        "phase7_experiments/experiment2/external_epsilon/observations.json",
    ),
    (
        "outputs/phase7_experiment2_comparison_v1/external-epsilon/run-1/"
        "summary.json",
        "phase7_experiments/experiment2/external_epsilon/summary.json",
    ),
    (
        "outputs/phase7_e2_integrated_trace_acceptance_v1/"
        "historical_secondary_analysis.json",
        "phase7_experiments/experiment2/frozen_acceptance/analysis.json",
    ),
    (
        "outputs/phase7_e2_integrated_trace_acceptance_v1/"
        "historical_secondary_analysis.md",
        "phase7_experiments/experiment2/frozen_acceptance/analysis.md",
    ),
)
REPORT_CANDIDATES = (
    "docs/Req2Web实验报告.docx",
    "docs/Req2Web实验结果简明报告.docx",
)


class PackageError(ValueError):
    """Raised when the handoff package cannot be built or validated."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def git_text(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def safe_workspace_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    relative = path.relative_to(ROOT)
    lowered = {part.lower() for part in relative.parts}
    if "__pycache__" in lowered or ".pytest_cache" in lowered:
        return False
    return path.suffix.lower() not in {".pyc", ".pyo", ".log", ".tmp"}


def framework_files() -> list[Path]:
    selected: set[Path] = set()
    for relative in ROOT_FILES:
        path = ROOT / relative
        if safe_workspace_file(path):
            selected.add(path)
    for directory in SOURCE_DIRS:
        root = ROOT / directory
        for path in root.rglob("*"):
            if safe_workspace_file(path):
                selected.add(path)
    docs_root = ROOT / "docs"
    for path in docs_root.rglob("*"):
        if safe_workspace_file(path) and path.suffix.lower() in DOC_EXTENSIONS:
            selected.add(path)
    for relative in DATA_FILES:
        path = ROOT / relative
        if not safe_workspace_file(path):
            raise PackageError(f"required framework file is missing: {relative}")
        selected.add(path)
    return sorted(selected, key=lambda item: item.relative_to(ROOT).as_posix())


def add_bytes(entries: dict[str, bytes], archive_path: str, raw: bytes) -> None:
    normalized = PurePosixPath(archive_path).as_posix()
    parts = PurePosixPath(normalized).parts
    if not parts or parts[0] != PACKAGE_ROOT or ".." in parts:
        raise PackageError(f"unsafe archive path: {archive_path}")
    entries[normalized] = raw


def copy_reference_materials(entries: dict[str, bytes], reference: Path) -> None:
    with zipfile.ZipFile(reference, "r") as source:
        if source.testzip() is not None:
            raise PackageError("the reference archive fails its CRC check")
        for info in source.infolist():
            if info.is_dir() or not info.filename.startswith(REFERENCE_PREFIXES):
                continue
            add_bytes(entries, info.filename, source.read(info))


def package_readme() -> bytes:
    text = """# Req2Web Final Internal Writer Package

This archive is the final internal code-and-evidence handoff for paper writing
and demonstration-video production after the Phase 7 experiment phase closed.
It is not a public release and does not grant redistribution rights.

## Contents

- `framework/`: the current source, scripts, tests, fixtures, technical
  documents, and compact processed retrieval inputs. The snapshot includes the
  current working-tree versions used for the final Phase 7 analysis.
- `phase7_experiments/`: compact frozen evidence for Experiments 1 and 2. Heavy
  GPU payloads, model weights, caches, and repeated screenshots are excluded.
- `reviewer_bundle/`: the preserved twelve-case read-only Inspector replay from
  the earlier handoff package.
- `final_retrieval_metrics/`: preserved final exploratory retrieval metrics.
- `writer_reference/`: owner-facing briefing documents, including the latest
  concise Chinese experiment report.
- `audit/`: source identity, package manifest, file inventory, and SHA-256
  checksums.

## Frozen Phase 7 results

- Experiment 1 ordered-decision family: Req2Web 16/18, direct HTML 8/18,
  structured one-call 5/18. The three-case paired value is p=0.25, so this is
  exploratory rather than confirmatory.
- Experiment 2 integrated trace family: Req2Web C2 18/18; C0, C1, and
  EVL-local 6/18; EVL-cross 18/18 after equivalent author-supplied cross-artifact
  rules were added. The C2 versus EVL-local paired value is post-hoc
  p=0.03125. This supports native integration, not unique rule expressiveness.

The active experiment phase is closed. E3 remains deferred. Remaining work is
small engineering polish, paper writing, and video production.

## Start points

- Overall status: `framework/docs/phase7_experiment_execution_status.md`
- E1 method and limits:
  `framework/docs/phase7_e1_ordered_decision_acceptance_strategy.md`
- E2 method and limits:
  `framework/docs/phase7_e2_integrated_trace_acceptance_strategy.md`
- Concise owner report: `writer_reference/Req2Web实验报告.docx`
- Video plan: `framework/docs/phase7_demo_video_production_plan.md`

For the preserved Phase 6 replay, run
`python reviewer_bundle/validate_bundle.py` after extraction. Source-level
tests and experiment scorers are under `framework/tests/` and
`framework/scripts/`.

## Boundary

This package does not establish H1/gold evaluation, formal quality,
generalization, training, production readiness, or a user study. It excludes
Git history, virtual environments, raw datasets, model weights, API keys,
machine caches, temporary roots, remote payloads, and superseded bulk outputs.
Public redistribution still requires the repository owner's license and
material decisions.
"""
    return text.encode("utf-8")


def experiment_readme() -> bytes:
    text = """# Phase 7 Frozen Experiment Evidence

This directory contains only the compact evidence needed to understand and
reproduce the frozen secondary analyses. It does not contain model weights or
the large remote/runtime payloads.

## Experiment 1

- `source/observation.json` is the exact frozen 216-row browser observation
  consumed by the ordered-decision scorer.
- `source/analysis.*` retains the broader measured analysis.
- `frozen_acceptance/analysis.*` is the final 16/18 versus 8/18 versus 5/18
  ordered-decision result.

## Experiment 2

- `local/` retains the exact local C0/C1/C2 observation and its frozen
  protocol.
- `external_epsilon/` retains the exact EVL-local/EVL-cross observation,
  protocol, and summary from the executed Eclipse Epsilon comparison.
- `frozen_acceptance/analysis.*` is the final integrated trace-family result.

The authoritative interpretation and limitations are in the corresponding
documents under `../framework/docs/`.
"""
    return text.encode("utf-8")


def build_entries(reference: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    entries: dict[str, bytes] = {}
    copy_reference_materials(entries, reference)

    source_files = framework_files()
    for path in source_files:
        relative = path.relative_to(ROOT).as_posix()
        add_bytes(entries, f"{PACKAGE_ROOT}/framework/{relative}", path.read_bytes())

    for source_relative, package_relative in PHASE7_RESULT_FILES:
        source = ROOT / source_relative
        if not safe_workspace_file(source):
            raise PackageError(f"required Phase 7 evidence is missing: {source_relative}")
        add_bytes(entries, f"{PACKAGE_ROOT}/{package_relative}", source.read_bytes())

    report = next(
        (
            ROOT / relative
            for relative in REPORT_CANDIDATES
            if safe_workspace_file(ROOT / relative)
        ),
        None,
    )
    if report is None:
        raise PackageError("the latest concise experiment report is missing")
    add_bytes(
        entries,
        f"{PACKAGE_ROOT}/writer_reference/Req2Web实验报告.docx",
        report.read_bytes(),
    )
    add_bytes(entries, f"{PACKAGE_ROOT}/README.md", package_readme())
    add_bytes(
        entries,
        f"{PACKAGE_ROOT}/phase7_experiments/README.md",
        experiment_readme(),
    )

    status_paths = [*ROOT_FILES, *SOURCE_DIRS, "docs", *DATA_FILES]
    status = git_text(
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        *status_paths,
    )
    source_snapshot = {
        "schema_version": "req2web.final_writer_source_snapshot.v1",
        "created_date": "2026-09-21",
        "branch": git_text("branch", "--show-current"),
        "head_commit": git_text("rev-parse", "HEAD"),
        "head_tree": git_text("rev-parse", "HEAD^{tree}"),
        "source_policy": (
            "current working-tree bytes for the included framework paths; "
            "tracked modifications and selected untracked Phase 7 files included"
        ),
        "included_framework_file_count": len(source_files),
        "included_status_lines": status.splitlines() if status else [],
        "reference_archive_sha256": sha256(reference.read_bytes()),
    }
    add_bytes(
        entries,
        f"{PACKAGE_ROOT}/audit/SOURCE_SNAPSHOT.json",
        (json.dumps(source_snapshot, indent=2, ensure_ascii=False) + "\n").encode(
            "utf-8"
        ),
    )
    return entries, source_snapshot


def category_for(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[1] if len(parts) > 1 else "root"


def add_audit_files(
    entries: dict[str, bytes], reference: Path, source_snapshot: dict[str, object]
) -> None:
    final_total = len(entries) + 3
    manifest = {
        "schema_version": "req2web.final_internal_writer_package.v2",
        "package_name": "Req2Web_final_20260921",
        "created_date": "2026-09-21",
        "source_commit": source_snapshot["head_commit"],
        "source_tree": source_snapshot["head_tree"],
        "source_policy": source_snapshot["source_policy"],
        "reference_archive": reference.name,
        "reference_archive_sha256": source_snapshot["reference_archive_sha256"],
        "experiment_phase": "closed_frozen_results_paper_and_video_next",
        "experiment_results": {
            "E1": {
                "Req2Web": "16/18",
                "direct_HTML": "8/18",
                "structured_one_call": "5/18",
                "paired_p": 0.25,
                "interpretation": "exploratory_ordered_decision_advantage",
            },
            "E2": {
                "C0": "6/18",
                "C1": "6/18",
                "Req2Web_C2": "18/18",
                "EVL_local": "6/18",
                "EVL_cross": "18/18",
                "C2_vs_EVL_local_p": 0.03125,
                "interpretation": "integrated_trace_checking_not_unique_expressiveness",
            },
        },
        "included": [
            "current framework source, scripts, tests, fixtures, and documentation",
            "compact processed retrieval corpus and index",
            "preserved Phase 6 reviewer replay and retrieval metrics",
            "frozen E1 and E2 source observations and secondary analyses",
            "latest concise Chinese experiment report",
            "source identity, inventory, and SHA-256 checksums",
        ],
        "excluded": [
            ".git and Git history",
            "virtual environments, caches, temporary roots, and logs",
            "model weights, remote GPU payloads, and bulk screenshots",
            "raw datasets, credentials, and local provider settings",
            "superseded bulk experiment outputs",
        ],
        "file_count": final_total,
        "checksum_scope": (
            "all package files except audit/FILE_INVENTORY.csv and "
            "audit/SHA256SUMS.txt"
        ),
        "claim_boundary": (
            "bounded engineering and exploratory experiment evidence only; "
            "not H1/gold, formal quality, generalization, training, production, "
            "or user-study evidence"
        ),
        "public_release_ready": False,
        "license_gate": "owner license and material decision required",
    }
    add_bytes(
        entries,
        f"{PACKAGE_ROOT}/audit/PACKAGE_MANIFEST.json",
        (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )

    rows = []
    for path in sorted(entries):
        raw = entries[path]
        rows.append((path, len(raw), sha256(raw), category_for(path)))
    csv_buffer = io.StringIO(newline="")
    writer = csv.writer(csv_buffer, lineterminator="\n")
    writer.writerow(("path", "bytes", "sha256", "category"))
    writer.writerows(rows)
    sums = "".join(f"{digest}  {path}\n" for path, _, digest, _ in rows)
    add_bytes(
        entries,
        f"{PACKAGE_ROOT}/audit/FILE_INVENTORY.csv",
        csv_buffer.getvalue().encode("utf-8"),
    )
    add_bytes(
        entries,
        f"{PACKAGE_ROOT}/audit/SHA256SUMS.txt",
        sums.encode("utf-8"),
    )


def write_zip(entries: dict[str, bytes], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise PackageError(f"refusing to overwrite existing output: {output}")
    with zipfile.ZipFile(
        output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(entries):
            info = zipfile.ZipInfo(path, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, entries[path], compresslevel=9)


def validate_zip(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path, "r") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise PackageError(f"CRC validation failed for {bad}")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise PackageError("archive contains duplicate paths")
        for name in names:
            parts = PurePosixPath(name).parts
            if not parts or parts[0] != PACKAGE_ROOT or ".." in parts:
                raise PackageError(f"archive contains unsafe path: {name}")
        required = {
            f"{PACKAGE_ROOT}/README.md",
            f"{PACKAGE_ROOT}/framework/README.md",
            f"{PACKAGE_ROOT}/framework/scripts/phase7_e1_ordered_decision_acceptance.py",
            f"{PACKAGE_ROOT}/framework/scripts/phase7_e2_integrated_trace_acceptance.py",
            f"{PACKAGE_ROOT}/phase7_experiments/experiment1/frozen_acceptance/analysis.json",
            f"{PACKAGE_ROOT}/phase7_experiments/experiment2/frozen_acceptance/analysis.json",
            f"{PACKAGE_ROOT}/reviewer_bundle/validate_bundle.py",
            f"{PACKAGE_ROOT}/writer_reference/Req2Web实验报告.docx",
            f"{PACKAGE_ROOT}/audit/PACKAGE_MANIFEST.json",
            f"{PACKAGE_ROOT}/audit/FILE_INVENTORY.csv",
            f"{PACKAGE_ROOT}/audit/SHA256SUMS.txt",
        }
        missing = sorted(required - set(names))
        if missing:
            raise PackageError(f"archive is missing required paths: {missing}")
        sums_text = archive.read(
            f"{PACKAGE_ROOT}/audit/SHA256SUMS.txt"
        ).decode("utf-8")
        checked = 0
        for line in sums_text.splitlines():
            digest, name = line.split("  ", 1)
            if sha256(archive.read(name)) != digest:
                raise PackageError(f"SHA-256 validation failed for {name}")
            checked += 1
        manifest = json.loads(
            archive.read(f"{PACKAGE_ROOT}/audit/PACKAGE_MANIFEST.json")
        )
        if manifest["file_count"] != len(names):
            raise PackageError("manifest file count does not match the archive")
        return {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256(path.read_bytes()),
            "entries": len(names),
            "checksums_verified": checked,
            "source_commit": manifest["source_commit"],
            "experiment_phase": manifest["experiment_phase"],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", type=Path)
    args = parser.parse_args(argv)

    if args.validate_only is not None:
        print(json.dumps(validate_zip(args.validate_only), indent=2))
        return 0
    if not args.reference_archive.is_file():
        raise PackageError("reference archive does not exist")
    entries, source_snapshot = build_entries(args.reference_archive)
    add_audit_files(entries, args.reference_archive, source_snapshot)
    write_zip(entries, args.output)
    print(json.dumps(validate_zip(args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
