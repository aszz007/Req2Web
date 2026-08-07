"""Run one bounded Phase 5 semantic-evaluator case."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_evaluation.phase5_semantic_qwen_runtime import (  # noqa: E402
    HIGH_GPU_PROFILE,
    LOW_GPU_PROFILE,
    Phase5SemanticQwenRuntimeError,
    finalize_existing_phase5_semantic_qwen_result,
    run_phase5_semantic_qwen,
    validate_existing_phase5_semantic_qwen_result,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one supervised Qwen3.5-9B semantic-alignment case. The local "
            "profile is quantized smoke evidence; the high-GPU profile is "
            "BF16 without quantization or CPU offload."
        )
    )
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--result-package-root", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument(
        "--profile",
        required=True,
        choices=(LOW_GPU_PROFILE, HIGH_GPU_PROFILE),
    )
    parser.add_argument(
        "--confirm-local-semantic-model-action",
        action="store_true",
    )
    parser.add_argument(
        "--finalize-existing-result",
        action="store_true",
        help=(
            "Parse and close an existing captured raw response without loading "
            "or calling the model."
        ),
    )
    parser.add_argument(
        "--validate-existing-result",
        action="store_true",
        help=(
            "Replay a terminal result without model, GPU, or writes."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.finalize_existing_result and args.validate_existing_result:
            raise Phase5SemanticQwenRuntimeError(
                "choose only one existing-result action"
            )
        if args.validate_existing_result:
            summary = validate_existing_phase5_semantic_qwen_result(
                audit_root=args.audit_root.resolve(),
                result_package_root=args.result_package_root.resolve(),
                result_root=args.result_root.resolve(),
                profile_name=args.profile,
            )
        elif args.finalize_existing_result:
            summary = finalize_existing_phase5_semantic_qwen_result(
                audit_root=args.audit_root.resolve(),
                result_package_root=args.result_package_root.resolve(),
                result_root=args.result_root.resolve(),
                profile_name=args.profile,
            )
        else:
            summary = run_phase5_semantic_qwen(
                audit_root=args.audit_root.resolve(),
                result_package_root=args.result_package_root.resolve(),
                model_root=args.model_root.resolve(),
                result_root=args.result_root.resolve(),
                profile_name=args.profile,
                confirm_model_action=(
                    args.confirm_local_semantic_model_action is True
                ),
            )
    except Phase5SemanticQwenRuntimeError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if summary["status"] != "failed_closed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
