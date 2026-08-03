"""Run the owner-confirmed one-call P4-03D1 visible-stream diagnostic."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from req2web_runtime.phase4_local_qwen import (
    P4D1_DIAGNOSTIC_ID,
    Phase4LocalQwenContractError,
    run_p4d1_stream_diagnostic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run exactly one foreground local Qwen generation for the independent "
            "P4-03D1 visible-stream diagnostic. This is not an R6 continuation or "
            "a model-success/contract run."
        )
    )
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--integrity-evidence", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument(
        "--confirm-one-local-generate",
        action="store_true",
        help=(
            "Explicitly authorize this invocation's single local generate call; "
            "retry remains zero and the 20-minute deadline remains fixed."
        ),
    )
    return parser


def _offline_process() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["LANGSMITH_TRACING"] = "0"
    os.environ["LANGCHAIN_TRACING_V2"] = "0"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.confirm_one_local_generate is not True:
        parser.error(
            "--confirm-one-local-generate is required before the D1 worker may start"
        )
    _offline_process()
    try:
        receipt = run_p4d1_stream_diagnostic(
            model_root=args.model_root.resolve(strict=True),
            integrity_evidence=args.integrity_evidence.resolve(strict=True),
            result_root=args.result_root.resolve(strict=False),
            confirm_one_local_generate=True,
            console=sys.stderr,
        )
    except (OSError, Phase4LocalQwenContractError) as exc:
        print(f"[{P4D1_DIAGNOSTIC_ID}] failed closed: {exc}", file=sys.stderr)
        return 2
    print(
        f"[{P4D1_DIAGNOSTIC_ID}] terminal_status={receipt.terminal_status}",
        file=sys.stderr,
    )
    return 0 if receipt.terminal_status == "generation_completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
