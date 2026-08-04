"""Prepare the F4 provider-visible contract packet without a model action."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from req2web_runtime.phase4_remote_qwen_f4 import (
    F4_DIAGNOSTIC_ID,
    Phase4RemoteQwenF4ContractError,
    prepare_phase4_remote_qwen_f4,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the validated F3 savepoint and prepare F4 input, prompt, "
            "runtime config, and request records without loading or calling a model."
        )
    )
    parser.add_argument("--f3-result-root", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument(
        "--confirm-no-model-preparation",
        action="store_true",
        help="Confirm that this command may write a new no-model F4 preparation root.",
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
    if args.confirm_no_model_preparation is not True:
        parser.error(
            "--confirm-no-model-preparation is required before writing the F4 packet"
        )
    _offline_process()
    try:
        prepared = prepare_phase4_remote_qwen_f4(
            f3_result_root=args.f3_result_root.resolve(strict=True),
            result_root=args.result_root.resolve(strict=False),
        )
    except (OSError, Phase4RemoteQwenF4ContractError) as exc:
        print(f"[{F4_DIAGNOSTIC_ID}] failed closed: {exc}", file=sys.stderr)
        return 2
    print(
        f"[{F4_DIAGNOSTIC_ID}] preflight=prepared "
        f"model_action=false result_root={prepared.result_root}",
        file=sys.stderr,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
