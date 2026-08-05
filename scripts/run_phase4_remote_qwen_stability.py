"""Run the bounded ten-case P4-05 remote Qwen stability baseline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from req2web_runtime import phase4_remote_qwen_stability as runtime
from req2web_runtime import phase4_remote_qwen_fresh_integrated as fresh


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the P4-05 ten-case synthetic commerce stability baseline "
            "with BF16, no quantization, GPU0, no retry, and no truncation."
        )
    )
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--integrity-evidence", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Write and validate the offline preflight without model calls.",
    )
    parser.add_argument(
        "--confirm-ten-case-baseline",
        action="store_true",
        help="Confirm all ten bounded baseline cases and their model calls.",
    )
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help=(
            "Resume the explicitly identified existing result root after "
            "validating its marker, policy, progress, and call ledger."
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


def _summary_line(result: dict[str, object]) -> str:
    aggregate = result.get("aggregate")
    if not isinstance(aggregate, dict):
        raise runtime.Phase4RemoteQwenStabilityError(
            "stability runner returned no aggregate summary"
        )
    status = (
        "baseline_complete"
        if result.get("baseline_complete") is True
        else "incomplete"
    )
    return (
        "[P4-05-STABILITY] "
        f"status={status} "
        f"completed_cases={aggregate.get('case_count', 0)} "
        f"total_model_generate_calls="
        f"{aggregate.get('total_model_generate_calls', 0)} "
        f"f4_raw_direct_pass={aggregate.get('f4_raw_direct_pass_count', 0)} "
        f"normalization={aggregate.get('f4_normalized_case_count', 0)} "
        f"repair_attempted={aggregate.get('repair_attempted_count', 0)} "
        f"repair_success={aggregate.get('repair_success_count', 0)} "
        f"repair_failed={aggregate.get('repair_failed_count', 0)} "
        f"fallback_attempted={aggregate.get('fallback_attempted_count', 0)} "
        f"fallback={aggregate.get('g0_fallback_count', 0)} "
        f"delivery={aggregate.get('delivery_success_count', 0)} "
        f"failed_closed={aggregate.get('failed_closed_count', 0)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (
        args.preflight_only is False
        and args.confirm_ten_case_baseline is not True
    ):
        parser.error(
            "--confirm-ten-case-baseline is required before the baseline "
            "may load or generate"
        )

    _offline_process()
    try:
        model_root = args.model_root.resolve(strict=True)
        integrity_evidence = args.integrity_evidence.resolve(strict=True)
        result_root = args.result_root.resolve(strict=False)
        if args.preflight_only:
            prepared = runtime.prepare_phase4_remote_qwen_stability(
                model_root=model_root,
                integrity_evidence=integrity_evidence,
                result_root=result_root,
                run_id=args.run_id,
                resume_existing=args.resume_existing,
            )
            print(
                "[P4-05-STABILITY] "
                f"status={('existing_summary_validated' if prepared.get('summary') is not None else 'prepared_no_model')} "
                f"run_id={prepared['run_id']} "
                f"result_root={result_root}",
                flush=True,
            )
            return 0

        print(
            f"[P4-05-STABILITY] run_id={args.run_id or 'generated'} "
            "baseline started",
            file=sys.stderr,
            flush=True,
        )
        result = runtime.run_phase4_remote_qwen_stability(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
            result_root=result_root,
            confirm_ten_case_baseline=True,
            run_id=args.run_id,
            console=sys.stderr,
            resume_existing=args.resume_existing,
        )
    except (
        OSError,
        runtime.Phase4RemoteQwenStabilityError,
        fresh.Phase4RemoteFreshIntegratedError,
    ) as exc:
        print(
            f"[P4-05-STABILITY] failed closed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2

    print(_summary_line(result), flush=True)
    return 0 if result.get("baseline_complete") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
