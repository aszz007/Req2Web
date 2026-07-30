#!/usr/bin/env python3
"""Create or replay the no-GPU Qwen3.6-27B diagnostic artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime import qwen27b_predeploy as runtime_authority  # noqa: E402
from req2web_runtime import qwen36_diagnostic as diagnostic  # noqa: E402

PLAN_FILE = "qwen36_diagnostic_plan.json"
MODEL_INVENTORY_FILE = "qwen36_model_inventory.json"
RUNTIME_INVENTORY_FILE = "qwen36_runtime_inventory.json"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _progress(event: str, **fields: object) -> None:
    sys.stderr.write(
        _canonical({"event": event, **fields}).decode("utf-8") + "\n"
    )
    sys.stderr.flush()


def _prepare_root(path: Path, *, validate_only: bool) -> Path:
    if validate_only:
        if path.is_symlink() or not path.is_dir():
            raise diagnostic.Qwen36DiagnosticError(
                "prepare_output_root_invalid"
            )
        return path.resolve(strict=True)
    if path.exists():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise diagnostic.Qwen36DiagnosticError(
                "prepare_output_root_not_empty"
            )
    else:
        path.mkdir(parents=True, exist_ok=False)
    return path.resolve(strict=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create exact runtime/model inventories and the unchanged-contract "
            "Qwen3.6-27B diagnostic plan without loading a model or requiring a GPU."
        )
    )
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--repository-archive", type=Path, required=True)
    parser.add_argument(
        "--repository-archive-manifest", type=Path, required=True
    )
    parser.add_argument("--case-bundle-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "Replay stored canonical inventories and plan without re-reading "
            "the runtime root or the 55.6 GB model root."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = _prepare_root(
            args.output_root, validate_only=args.validate_only
        )
        if args.validate_only:
            runtime_raw = (output / RUNTIME_INVENTORY_FILE).read_bytes()
            diagnostic._read_runtime_inventory(runtime_raw)
            inventory = diagnostic.Qwen36ModelInventory.from_bytes(
                (output / MODEL_INVENTORY_FILE).read_bytes()
            )
            _progress("qwen36_prepare_canonical_replay_no_live_rescan")
        else:
            _progress("qwen36_runtime_inventory_validation_started")
            runtime_data = runtime_authority.collect_runtime_root_inventory(
                args.runtime_root
            )
            runtime_raw = runtime_authority._dump(runtime_data)
            _progress(
                "qwen36_runtime_inventory_validated",
                inventory_id=runtime_data["inventory_id"],
                tree_sha256=runtime_data["tree_sha256"],
            )
            _progress(
                "qwen36_model_inventory_validation_started",
                expected_file_count=29,
            )
            inventory = diagnostic.collect_qwen36_model_inventory(
                args.model_root
            )
            _progress(
                "qwen36_model_inventory_validated",
                inventory_id=inventory.to_dict()["inventory_id"],
                tree_sha256=inventory.to_dict()["tree_sha256"],
            )
        plan = diagnostic.create_qwen36_diagnostic_plan(
            args.repository_archive,
            args.repository_archive_manifest,
            args.case_bundle_root,
            runtime_raw,
        )
        expected = {
            PLAN_FILE: plan.canonical_bytes(),
            MODEL_INVENTORY_FILE: inventory.canonical_bytes(),
            RUNTIME_INVENTORY_FILE: runtime_raw,
        }
        if args.validate_only:
            actual_paths = {
                path.relative_to(output).as_posix()
                for path in output.rglob("*")
                if path.is_file() or path.is_symlink()
            }
            if actual_paths != set(expected):
                raise diagnostic.Qwen36DiagnosticError(
                    "prepare_output_inventory_invalid"
                )
            for relative, raw in expected.items():
                if (output / relative).read_bytes() != raw:
                    raise diagnostic.Qwen36DiagnosticError(
                        "prepare_output_replay_mismatch"
                    )
            mode = "validate_only_no_live_rescan"
        else:
            for relative, raw in expected.items():
                (output / relative).write_bytes(raw)
            mode = "create"
        runtime_binding = plan.to_dict()["runtime"]
        result = {
            "schema_version": (
                "req2web.script.qwen36_27b_diagnostic_prepare_result.v1"
            ),
            "mode": mode,
            "plan_id": plan.to_dict()["plan_id"],
            "plan_sha256": plan.sha256(),
            "model_inventory_id": inventory.to_dict()["inventory_id"],
            "model_inventory_sha256": inventory.sha256(),
            "model_inventory_tree_sha256": inventory.to_dict()[
                "tree_sha256"
            ],
            "runtime_inventory_id": runtime_binding["inventory_id"],
            "runtime_inventory_sha256": runtime_binding["sha256"],
            "runtime_inventory_tree_sha256": runtime_binding[
                "tree_sha256"
            ],
            "model_loaded": False,
            "provider_call_count": 0,
        }
        sys.stdout.buffer.write(_canonical(result) + b"\n")
        return 0
    except (
        OSError,
        diagnostic.Qwen36DiagnosticError,
        runtime_authority.Qwen27BPredeployError,
    ) as exc:
        raise SystemExit(
            f"Qwen3.6 diagnostic preparation failed closed: {exc}"
        ) from exc


if __name__ == "__main__":
    raise SystemExit(main())
