#!/usr/bin/env python3
"""Replay the final local route for captured Qwen3.5-27B pilot raw responses."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping
from uuid import uuid4

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.qwen27b_case_bundle import (  # noqa: E402
    Qwen27BCaseBundleError,
)
from req2web_runtime.qwen27b_final_route import (  # noqa: E402
    Qwen27BFinalRouteError,
    evaluate_qwen27b_final_route,
)
from req2web_runtime.qwen27b_recovery_runner import (  # noqa: E402
    Qwen27BRecoveryRunnerError,
)

RUN_FILE = "qwen27b_recovery_evaluation.json"
CASE_IDS = ("path3-commerce-checkout", "path3-media-analysis")
ENTRYPOINT_STATUS = "historical"
ENTRYPOINT_MODE = "replay_only"
ACTIVE_DEFAULT_ENTRY = False
_ENTRYPOINT_HELP = (
    "HISTORICAL STAGE 3 ENTRYPOINT; NOT THE ACTIVE DEFAULT FULL-FLOW ENTRY. "
    "entrypoint_status=historical active_default_entry=false "
    "entrypoint_mode=replay_only."
)


class RecoveryEvaluationError(ValueError):
    """The fixed recovery-pilot evaluation could not be replayed exactly."""


def _emit_entrypoint_notice() -> None:
    print(
        json.dumps(
            {
                "active_default_entry": ACTIVE_DEFAULT_ENTRY,
                "entrypoint_mode": ENTRYPOINT_MODE,
                "entrypoint_status": ENTRYPOINT_STATUS,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _new_work_root(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / (".qwen27b-final-route-" + uuid4().hex)
    root.mkdir()
    return root


def _evaluate(args: argparse.Namespace) -> dict[str, Any]:
    work_root = _new_work_root(args.output_root.parent)
    try:
        return evaluate_qwen27b_final_route(
            run_root=args.run_root.resolve(strict=True),
            predeploy_manifest=args.predeploy_manifest.resolve(strict=True),
            predeploy_ready=args.predeploy_ready.resolve(strict=True),
            repository_archive=args.repository_archive.resolve(strict=True),
            repository_archive_manifest=(
                args.repository_archive_manifest.resolve(strict=True)
            ),
            case_bundle_root=args.case_bundle_root.resolve(strict=True),
            selected_copy=args.selected_copy,
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


def _write_new(root: Path, relative: str, raw: bytes) -> None:
    path = root / relative
    if path.exists():
        raise RecoveryEvaluationError("evaluation_output_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _write_output(output_root: Path, record: Mapping[str, Any]) -> None:
    if output_root.exists():
        if (
            output_root.is_symlink()
            or not output_root.is_dir()
            or any(output_root.iterdir())
        ):
            raise RecoveryEvaluationError("evaluation_output_root_not_empty")
    else:
        output_root.mkdir(parents=True, exist_ok=False)
    for case in record["cases"]:
        _write_new(
            output_root,
            f"cases/{case['case_id']}/evaluation.json",
            _canonical(case),
        )
    _write_new(output_root, RUN_FILE, _canonical(record))


def _validate_existing(
    output_root: Path,
    expected: Mapping[str, Any],
) -> None:
    actual_paths = {
        path.relative_to(output_root).as_posix()
        for path in output_root.rglob("*")
        if path.is_file()
    }
    if actual_paths != _expected_paths(expected):
        raise RecoveryEvaluationError("evaluation_output_inventory_invalid")
    if (output_root / RUN_FILE).read_bytes() != _canonical(expected):
        raise RecoveryEvaluationError("evaluation_run_replay_mismatch")
    for case in expected["cases"]:
        path = (
            output_root
            / "cases"
            / case["case_id"]
            / "evaluation.json"
        )
        if path.read_bytes() != _canonical(case):
            raise RecoveryEvaluationError("evaluation_case_replay_mismatch")


def _result(mode: str, record: Mapping[str, Any], output_root: Path) -> None:
    value = {
        "schema_version": (
            "req2web.script.qwen35_27b_recovery_evaluation_cli_result.v2"
        ),
        "mode": mode,
        "record_id": record["record_id"],
        "record_sha256": _sha(_canonical(record)),
        "overall_decision": record["overall_decision"],
        "output_root": str(output_root),
        "model_loaded": False,
        "provider_call_performed": False,
        "retry_performed": False,
        "real_browser_executed": False,
        "formal_browser_success": False,
    }
    sys.stdout.buffer.write(_canonical(value) + b"\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            _ENTRYPOINT_HELP
            + " Validate captured Qwen3.5-27B raw results against the frozen "
            "pre-run artifacts, replay accepted A-07a/A-07b delivery semantics, "
            "and recompute the pre-registered semantic coverage. This command "
            "does not load a model, call a Provider, retry, or open a network."
        )
    )
    parser.add_argument("--predeploy-manifest", type=Path, required=True)
    parser.add_argument("--predeploy-ready", type=Path, required=True)
    parser.add_argument("--repository-archive", type=Path, required=True)
    parser.add_argument(
        "--repository-archive-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument("--case-bundle-root", type=Path, required=True)
    parser.add_argument("--selected-copy", required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Replay the full local route and compare stored sidecars exactly.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _emit_entrypoint_notice()
    try:
        record = _evaluate(args)
        if args.validate_only:
            _validate_existing(args.output_root, record)
            mode = "validate_only"
        else:
            _write_output(args.output_root, record)
            mode = "create"
        _result(mode, record, args.output_root)
        return 0
    except (
        OSError,
        RecoveryEvaluationError,
        Qwen27BCaseBundleError,
        Qwen27BFinalRouteError,
        Qwen27BRecoveryRunnerError,
    ) as exc:
        raise SystemExit(
            f"recovery evaluation failed closed: {exc}"
        ) from exc


if __name__ == "__main__":
    raise SystemExit(main())
