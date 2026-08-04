"""Run one bounded local Qwen F4 node-contract generation with visible stream output."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from req2web_runtime.phase4_local_qwen_f4 import (
    F4_LOCAL_DIAGNOSTIC_ID,
    Phase4LocalQwenF4ContractError,
    run_phase4_local_qwen_f4,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run exactly one local Qwen F4 generate from the validated F3 savepoint. "
            "The result stops at node_model_pass and never executes composition, "
            "assembler, Acceptance, repair, G0, package, or production routing."
        )
    )
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--integrity-evidence", required=True, type=Path)
    parser.add_argument("--f3-result-root", required=True, type=Path)
    parser.add_argument("--prior-f4-failure-root", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument(
        "--confirm-one-local-generate",
        action="store_true",
        help="Confirm the single bounded F4 model call; automatic retry is forbidden.",
    )
    return parser


def _offline_process() -> None:
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "LANGSMITH_TRACING": "0",
            "LANGCHAIN_TRACING_V2": "0",
            "PYTHONUNBUFFERED": "1",
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.confirm_one_local_generate is not True:
        parser.error("--confirm-one-local-generate is required before the F4 worker may start")
    _offline_process()
    try:
        result = run_phase4_local_qwen_f4(
            f3_result_root=args.f3_result_root.resolve(strict=True),
            model_root=args.model_root.resolve(strict=True),
            integrity_evidence=args.integrity_evidence.resolve(strict=True),
            prior_f4_failure_root=args.prior_f4_failure_root.resolve(strict=True),
            result_root=args.result_root.resolve(strict=False),
            confirm_one_local_generate=True,
            console=sys.stderr,
        )
    except (OSError, Phase4LocalQwenF4ContractError) as exc:
        print(f"[{F4_LOCAL_DIAGNOSTIC_ID}] failed closed: {exc}", file=sys.stderr, flush=True)
        return 2
    print(
        f"[{F4_LOCAL_DIAGNOSTIC_ID}] status={result['status']} "
        f"generate_calls={result['generate_calls']} result_root={args.result_root}",
        file=sys.stderr,
        flush=True,
    )
    return (
        0
        if result["status"] in {"node_model_pass", "normalized_node_pass"}
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
