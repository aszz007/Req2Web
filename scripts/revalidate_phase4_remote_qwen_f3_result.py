"""Revalidate one immutable P4-03D3 F3 result without a model call."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from req2web_runtime.phase4_remote_qwen import (
    REMOTE_DIAGNOSTIC_ID,
    Phase4RemoteQwenContractError,
    revalidate_remote_qwen_bf16_f3_result,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the immutable D3 raw response through the existing F3 "
            "validator and stable-ID registry. No model is loaded or called."
        )
    )
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument(
        "--confirm-no-model-revalidation",
        action="store_true",
        help=(
            "Confirm a zero-generate replay that preserves historical result.json "
            "and writes separate revalidation sidecars."
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
            "PYTHONUNBUFFERED": "1",
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.confirm_no_model_revalidation is not True:
        parser.error(
            "--confirm-no-model-revalidation is required before sidecars may be written"
        )
    _offline_process()
    try:
        result = revalidate_remote_qwen_bf16_f3_result(
            result_root=args.result_root.resolve(strict=True)
        )
    except (OSError, Phase4RemoteQwenContractError) as exc:
        print(
            f"[{REMOTE_DIAGNOSTIC_ID}] revalidation failed closed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    print(
        f"[{REMOTE_DIAGNOSTIC_ID}] revalidation "
        f"node_model_pass={result.node_model_pass} "
        f"model_generate_calls={result.revalidation_generate_calls}",
        file=sys.stderr,
        flush=True,
    )
    return 0 if result.node_model_pass is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
