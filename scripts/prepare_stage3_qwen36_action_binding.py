#!/usr/bin/env python3
"""Create or replay the locally retained Qwen3.6 action-time GPU binding."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime import qwen36_diagnostic as diagnostic  # noqa: E402


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bind the manager-retained no-model plan/inventories to the exact "
            "action-time GPU name and UUID. The record is not external-action authority."
        )
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--model-inventory", type=Path, required=True)
    parser.add_argument("--runtime-inventory", type=Path, required=True)
    parser.add_argument("--expected-gpu-name", required=True)
    parser.add_argument("--expected-gpu-uuid", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        binding = diagnostic.create_qwen36_diagnostic_action_binding(
            args.plan,
            args.model_inventory,
            args.runtime_inventory,
            expected_gpu_name=args.expected_gpu_name,
            expected_gpu_uuid=args.expected_gpu_uuid,
        )
        raw = binding.canonical_bytes()
        if args.validate_only:
            existing = diagnostic.Qwen36DiagnosticActionBinding.from_bytes(
                args.output.read_bytes()
            )
            if existing.canonical_bytes() != raw:
                raise diagnostic.Qwen36DiagnosticError(
                    "action_binding_replay_mismatch"
                )
            mode = "validate_only"
        else:
            if args.output.exists() or args.output.is_symlink():
                raise diagnostic.Qwen36DiagnosticError(
                    "action_binding_output_exists"
                )
            if not args.output.parent.is_dir() or args.output.parent.is_symlink():
                raise diagnostic.Qwen36DiagnosticError(
                    "action_binding_output_parent_invalid"
                )
            args.output.write_bytes(raw)
            mode = "create"
        result = {
            "schema_version": (
                "req2web.script.qwen36_27b_action_binding_result.v1"
            ),
            "mode": mode,
            "binding_id": binding.to_dict()["binding_id"],
            "binding_sha256": binding.sha256(),
            "selected_gpu": binding.to_dict()["selected_gpu"],
            "artifact_is_not_external_action_authorization": True,
        }
        sys.stdout.buffer.write(_canonical(result) + b"\n")
        return 0
    except (OSError, diagnostic.Qwen36DiagnosticError) as exc:
        raise SystemExit(
            f"Qwen3.6 action binding failed closed: {exc}"
        ) from exc


if __name__ == "__main__":
    raise SystemExit(main())
