"""Run the no-model Phase 4 integrated savepoint foundation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from req2web_runtime.phase4_local_qwen_integrated import (
    Phase4LocalQwenIntegratedFoundationError,
    run_phase4_local_qwen_integrated_savepoint_foundation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Restore and live-revalidate the Phase 4 F1-F4 savepoints, "
            "then run deterministic composition and assembly without a "
            "model call."
        )
    )
    parser.add_argument("--f3-savepoint-root", type=Path, required=True)
    parser.add_argument("--f4-savepoint-root", type=Path, required=True)
    parser.add_argument(
        "--f4-source-result-root",
        type=Path,
        default=None,
        help=(
            "Optional live F4 raw-result root. If omitted, use the absolute "
            "root recorded by the F4 source binding."
        ),
    )
    parser.add_argument(
        "--f4-raw-artifact-root",
        type=Path,
        default=None,
        help=(
            "Optional self-contained integrated-savepoint root containing "
            "f4_source_raw_response.bin. Use this for replay when the "
            "original model-result directory is unavailable."
        ),
    )
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument(
        "--confirm-no-model-integrated-foundation",
        action="store_true",
        help="Required confirmation that this command must not call a model.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print("[P4-03I] preflight started", flush=True)
    try:
        result = run_phase4_local_qwen_integrated_savepoint_foundation(
            f3_savepoint_root=args.f3_savepoint_root,
            f4_savepoint_root=args.f4_savepoint_root,
            f4_source_result_root=args.f4_source_result_root,
            f4_raw_artifact_root=args.f4_raw_artifact_root,
            result_root=args.result_root,
            confirm_no_model_integrated_foundation=(
                args.confirm_no_model_integrated_foundation
            ),
        )
    except Phase4LocalQwenIntegratedFoundationError as exc:
        print(f"[P4-03I] failed closed: {exc}", file=sys.stderr, flush=True)
        return 2
    print("[P4-03I] preflight completed", flush=True)
    print("[P4-03I] savepoint replay started", flush=True)
    print("[P4-03I] savepoint replay completed", flush=True)
    print("[P4-03I] composition and assembly started", flush=True)
    print("[P4-03I] composition and assembly completed", flush=True)
    print(
        "[P4-03I] "
        f"status={result['status']} "
        f"model_generate_calls={result['model_generate_calls']} "
        f"raw_model_contract_success={result['raw_model_contract_success']} "
        f"agent_chain_system_output_usable="
        f"{result['agent_chain_system_output_usable']} "
        f"result_root={args.result_root}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
