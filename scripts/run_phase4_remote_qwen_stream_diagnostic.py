"""Run the owner-confirmed one-call P4-03D3 remote Qwen BF16 F3 pilot."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Keep the repository CLI usable from a fresh checkout without importing any
# model/runtime package merely to display --help.
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from req2web_runtime.phase4_remote_qwen import (
    REMOTE_DIAGNOSTIC_ID,
    Phase4RemoteQwenContractError,
    run_remote_qwen_bf16_f3,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run exactly one supervised remote Qwen3.5-9B BF16 F3 generation. "
            "The immutable D2 truncation failure is required as predecessor evidence. "
            "The result is a node-local, non-H1 experiment and never runs F4, "
            "composition, assembly, or downstream gates."
        )
    )
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--integrity-evidence", required=True, type=Path)
    parser.add_argument("--checkpoint-packet", required=True, type=Path)
    parser.add_argument("--checkpoint-receipt", required=True, type=Path)
    parser.add_argument("--prior-f3-failure", required=True, type=Path)
    parser.add_argument("--predecessor-d2-result", required=True, type=Path)
    parser.add_argument("--predecessor-d2-raw", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument(
        "--confirm-one-remote-generate",
        action="store_true",
        help=(
            "Explicitly authorize this invocation's single remote generate call; "
            "retry remains zero, the output has no fixed short token cap, and "
            "the 20-minute parent deadline remains fixed."
        ),
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
            "CUDA_VISIBLE_DEVICES": "0",
            "PYTHONUNBUFFERED": "1",
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.confirm_one_remote_generate is not True:
        parser.error(
            "--confirm-one-remote-generate is required before the remote worker may start"
        )
    _offline_process()
    try:
        result = run_remote_qwen_bf16_f3(
            model_root=args.model_root.resolve(strict=True),
            integrity_evidence=args.integrity_evidence.resolve(strict=True),
            checkpoint_packet=args.checkpoint_packet.resolve(strict=True),
            checkpoint_receipt=args.checkpoint_receipt.resolve(strict=True),
            prior_f3_failure=args.prior_f3_failure.resolve(strict=True),
            predecessor_d2_result=args.predecessor_d2_result.resolve(strict=True),
            predecessor_d2_raw=args.predecessor_d2_raw.resolve(strict=True),
            result_root=args.result_root.resolve(strict=False),
            confirm_one_remote_generate=True,
            console=sys.stderr,
        )
    except (OSError, Phase4RemoteQwenContractError) as exc:
        print(f"[{REMOTE_DIAGNOSTIC_ID}] failed closed: {exc}", file=sys.stderr)
        return 2
    print(
        f"[{REMOTE_DIAGNOSTIC_ID}] generation={result.generation_terminal} "
        f"node_model_pass={result.node_model_pass}",
        file=sys.stderr,
        flush=True,
    )
    return 0 if result.node_model_pass is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
