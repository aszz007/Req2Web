#!/usr/bin/env python3
"""Replay the accepted local route for captured Qwen3.6 diagnostic raw bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.qwen36_diagnostic import (  # noqa: E402
    Qwen36DiagnosticError,
)
from req2web_runtime.qwen36_diagnostic_route import (  # noqa: E402
    Qwen36DiagnosticRouteError,
    evaluate_qwen36_diagnostic_route,
)

RUN_FILE = "qwen36_diagnostic_evaluation.json"


class Qwen36DiagnosticEvaluationError(ValueError):
    """Stored diagnostic evaluation sidecars do not replay exactly."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _evaluate(args: argparse.Namespace) -> dict[str, Any]:
    args.output_root.parent.mkdir(parents=True, exist_ok=True)
    work_root = args.output_root.parent / (
        ".qwen36-diagnostic-route-" + uuid4().hex
    )
    work_root.mkdir()
    try:
        return evaluate_qwen36_diagnostic_route(
            run_root=args.run_root,
            plan=args.plan,
            action_binding=args.action_binding,
            model_inventory=args.model_inventory,
            runtime_inventory=args.runtime_inventory,
            repository_archive=args.repository_archive,
            repository_archive_manifest=args.repository_archive_manifest,
            case_bundle_root=args.case_bundle_root,
            work_root=work_root,
        )
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def _expected_paths(record: Mapping[str, Any]) -> set[str]:
    return {
        RUN_FILE,
        *(
            f"cases/{case['case_id']}/evaluation.json"
            for case in record["cases"]
        ),
    }


def _write_output(root: Path, record: Mapping[str, Any]) -> None:
    if root.exists():
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise Qwen36DiagnosticEvaluationError(
                "evaluation_output_root_not_empty"
            )
    else:
        root.mkdir(parents=True, exist_ok=False)
    for case in record["cases"]:
        path = root / "cases" / case["case_id"] / "evaluation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_canonical(case))
    (root / RUN_FILE).write_bytes(_canonical(record))


def _validate_existing(root: Path, record: Mapping[str, Any]) -> None:
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != _expected_paths(record):
        raise Qwen36DiagnosticEvaluationError(
            "evaluation_output_inventory_invalid"
        )
    if (root / RUN_FILE).read_bytes() != _canonical(record):
        raise Qwen36DiagnosticEvaluationError(
            "evaluation_record_replay_mismatch"
        )
    for case in record["cases"]:
        path = root / "cases" / case["case_id"] / "evaluation.json"
        if path.read_bytes() != _canonical(case):
            raise Qwen36DiagnosticEvaluationError(
                "evaluation_case_replay_mismatch"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate captured Qwen3.6 raw results and replay the unchanged "
            "Parser/Assembler/gate/one-repair/G0 route without loading a model."
        )
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--action-binding", type=Path, required=True)
    parser.add_argument("--model-inventory", type=Path, required=True)
    parser.add_argument("--runtime-inventory", type=Path, required=True)
    parser.add_argument("--repository-archive", type=Path, required=True)
    parser.add_argument(
        "--repository-archive-manifest", type=Path, required=True
    )
    parser.add_argument("--case-bundle-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        record = _evaluate(args)
        if args.validate_only:
            _validate_existing(args.output_root, record)
            mode = "validate_only"
        else:
            _write_output(args.output_root, record)
            mode = "create"
        result = {
            "schema_version": (
                "req2web.script.qwen36_27b_diagnostic_evaluation_result.v1"
            ),
            "mode": mode,
            "record_id": record["record_id"],
            "record_sha256": _sha(_canonical(record)),
            "overall_decision": record["overall_decision"],
            "model_loaded": False,
            "provider_call_performed": False,
            "retry_performed": False,
        }
        sys.stdout.buffer.write(_canonical(result) + b"\n")
        return 0
    except (
        OSError,
        Qwen36DiagnosticError,
        Qwen36DiagnosticRouteError,
        Qwen36DiagnosticEvaluationError,
    ) as exc:
        raise SystemExit(
            f"Qwen3.6 diagnostic evaluation failed closed: {exc}"
        ) from exc


if __name__ == "__main__":
    raise SystemExit(main())
