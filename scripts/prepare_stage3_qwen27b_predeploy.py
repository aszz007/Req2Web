#!/usr/bin/env python3
"""Create or replay the Qwen3.5-27B no-GPU predeploy readiness artifacts.

The command is deliberately local-only.  It validates already-present files and
runs a pinned interpreter version probe; it never downloads, loads, or invokes
a model and never opens a network connection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime import qwen27b_predeploy as predeploy
from req2web_runtime.qwen27b_case_bundle import validate_qwen27b_case_bundle


MANIFEST_FILE = "predeploy_manifest.json"
READY_FILE = "predeploy_ready_no_gpu.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(path: Path, label: str) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"{label} is unavailable: {path}") from exc
    if not raw:
        raise SystemExit(f"{label} must not be empty: {path}")
    return raw


def _write_new(root: Path, name: str, raw: bytes) -> None:
    path = root / name
    if path.exists():
        raise SystemExit(f"output artifact already exists: {path}")
    path.write_bytes(raw)


def _output(
    *, mode: str, manifest: predeploy.Qwen27BPredeployManifest, ready: predeploy.Qwen27BPredeployReadyNoGpu, output_root: Path
) -> None:
    result = {
        "schema_version": "req2web.script.qwen35_27b_predeploy_cli_result.v1",
        "mode": mode,
        "manifest_id": manifest.to_dict()["manifest_id"],
        "manifest_sha256": manifest.sha256(),
        "ready_id": ready.to_dict()["ready_id"],
        "ready_sha256": ready.sha256(),
        "output_root": str(output_root),
        "model_loaded": False,
        "run_occurred": False,
        "network_opened": False,
        "next_gate": "fresh_gpu_action_gate_required",
    }
    sys.stdout.buffer.write(_canonical(result) + b"\n")


def _live_probe(runtime_root: Path, runtime_inventory: bytes) -> bytes:
    """Call the contract's live subprocess probe once and retain its bytes."""
    probe = predeploy.probe_no_gpu_runtime(runtime_root, runtime_inventory)
    return _canonical(probe)


def _create(args: argparse.Namespace) -> int:
    output_root = args.output_root
    if output_root.exists():
        if not output_root.is_dir() or any(output_root.iterdir()):
            raise SystemExit("output root must be a new or empty directory")
    else:
        output_root.mkdir(parents=True, exist_ok=False)

    runtime_inventory = predeploy._dump(
        predeploy.collect_runtime_root_inventory(args.runtime_root)
    )
    runtime_probe = _live_probe(args.runtime_root, runtime_inventory)
    model_inventory = predeploy.collect_model_root_inventory(args.model_root)
    archive_inventory = predeploy.collect_file_inventory(args.repository_archive, "repository_archive")
    archive_authority = predeploy.validate_repository_archive_authority(
        args.repository_archive,
        args.repository_archive_manifest,
    )
    bundle = validate_qwen27b_case_bundle(
        args.case_bundle_root,
        repository_archive_path=args.repository_archive,
        repository_archive_manifest_path=args.repository_archive_manifest,
    )
    case_archive_authority = bundle.manifest["source"][
        "repository_archive_authority"
    ]
    case_inventory = predeploy.collect_directory_inventory(args.case_bundle_root, "case_bundle")
    manifest = predeploy.create_predeploy_manifest(
        model_inventory,
        runtime_inventory,
        runtime_probe,
        archive_inventory,
        case_inventory,
        archive_authority,
        case_archive_authority,
        source_commit=args.source_commit,
        source_tree=args.source_tree,
        transfer_copy=args.selected_copy,
    )
    ready = predeploy.create_predeploy_ready_no_gpu(
        manifest,
        args.model_root,
        args.repository_archive,
        args.case_bundle_root,
        args.selected_copy,
    )
    _write_new(output_root, MANIFEST_FILE, manifest.canonical_bytes())
    _write_new(output_root, READY_FILE, ready.canonical_bytes())
    _output(mode="create", manifest=manifest, ready=ready, output_root=output_root)
    return 0


def _validate_only(args: argparse.Namespace) -> int:
    manifest_raw = _read(args.output_root / MANIFEST_FILE, "predeploy manifest")
    ready_raw = _read(args.output_root / READY_FILE, "predeploy ready marker")
    manifest = predeploy.Qwen27BPredeployManifest.from_bytes(manifest_raw)
    ready = predeploy.validate_predeploy_ready_no_gpu_bytes(ready_raw)
    manifest_data = manifest.to_dict()
    if manifest_data["source"] != {"commit": args.source_commit, "tree": args.source_tree}:
        raise SystemExit("source commit/tree does not bind the persisted manifest")
    if manifest_data["transport"] != {
        "primary_transport": predeploy.PRIMARY_TRANSPORT,
        "selected_copy": args.selected_copy,
    }:
        raise SystemExit("primary transport or selected copy does not bind the persisted manifest")
    archive_authority = predeploy.validate_repository_archive_authority(
        args.repository_archive,
        args.repository_archive_manifest,
    )
    if archive_authority != manifest_data["repository_archive_authority"]:
        raise SystemExit(
            "filtered repository archive authority does not bind the persisted manifest"
        )
    bundle = validate_qwen27b_case_bundle(
        args.case_bundle_root,
        repository_archive_path=args.repository_archive,
        repository_archive_manifest_path=args.repository_archive_manifest,
    )
    if (
        bundle.manifest["source"]["repository_archive_authority"]
        != manifest_data["case_bundle_archive_authority"]
    ):
        raise SystemExit(
            "case bundle archive authority does not bind the persisted manifest"
        )
    runtime_inventory = predeploy._dump(
        predeploy.collect_runtime_root_inventory(args.runtime_root)
    )
    expected_runtime = _canonical(manifest_data["runtime_inventory"])
    if runtime_inventory != expected_runtime:
        raise SystemExit("runtime inventory does not bind the persisted manifest")
    live_probe = _live_probe(args.runtime_root, runtime_inventory)
    if live_probe != _canonical(manifest_data["runtime_probe"]):
        raise SystemExit("live no-GPU runtime probe does not bind the persisted manifest")
    predeploy.validate_predeploy_ready_no_gpu_against(
        ready,
        manifest,
        args.model_root,
        args.repository_archive,
        args.case_bundle_root,
        args.selected_copy,
    )
    _output(mode="validate_only", manifest=manifest, ready=ready, output_root=args.output_root)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create or validate a local no-GPU Qwen3.5-27B predeploy marker; "
            "no download, model load, inference, or network action is performed."
        )
    )
    parser.add_argument("--runtime-root", type=Path, required=True, help="Pinned runtime root containing bin/python.")
    parser.add_argument("--model-root", type=Path, required=True, help="Already-present exact model directory.")
    parser.add_argument("--repository-archive", type=Path, required=True, help="Already-present filtered repository archive.")
    parser.add_argument(
        "--repository-archive-manifest",
        type=Path,
        required=True,
        help="Owning accepted filtered-repository archive manifest.",
    )
    parser.add_argument("--case-bundle-root", type=Path, required=True, help="Prepared fixed two-case bundle root.")
    parser.add_argument("--source-commit", required=True, help="Exact pushed 40-character source commit SHA-1.")
    parser.add_argument("--source-tree", required=True, help="Exact 40-character source tree SHA-1.")
    parser.add_argument(
        "--selected-copy",
        choices=predeploy.TRANSFER_COPIES,
        default="none",
        help=(
            "Optional copy path. The fixed primary transport remains "
            "autodl_same_region_data_disk_clone; this command performs no transfer."
        ),
    )
    parser.add_argument("--output-root", type=Path, required=True, help="New output directory, or existing directory for --validate-only.")
    parser.add_argument("--validate-only", action="store_true", help="Replay existing artifacts and run the live no-GPU version probe without writing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _validate_only(args) if args.validate_only else _create(args)
    except predeploy.Qwen27BPredeployError as exc:
        raise SystemExit(f"predeploy failed closed: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
