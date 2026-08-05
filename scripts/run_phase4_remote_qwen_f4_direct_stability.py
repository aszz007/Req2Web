"""Run the bounded ten-case P4-05 F4-only direct-acceptance closure."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from req2web_runtime import phase4_remote_qwen_f4_direct_stability as runtime
from req2web_runtime import phase4_remote_qwen_fresh_integrated as fresh


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reuse validated F1-F3 checkpoints and run exactly one new BF16 F4 "
            "generation per stability case through direct A-07a first-pass "
            "delivery."
        )
    )
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--integrity-evidence", required=True, type=Path)
    parser.add_argument("--baseline-root", required=True, type=Path)
    parser.add_argument("--predecessor-root", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Write and validate the offline preflight without model calls.",
    )
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help="Resume an existing owned result root after evidence validation.",
    )
    parser.add_argument(
        "--confirm-ten-f4-calls",
        action="store_true",
        help="Confirm the bounded ten-case F4-only model action.",
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
    if not args.preflight_only and not args.confirm_ten_f4_calls:
        parser.error(
            "--confirm-ten-f4-calls is required before model generation"
        )
    _offline_process()
    try:
        common = {
            "model_root": args.model_root.resolve(strict=True),
            "integrity_evidence": args.integrity_evidence.resolve(strict=True),
            "baseline_root": args.baseline_root.resolve(strict=True),
            "predecessor_root": args.predecessor_root.resolve(strict=True),
            "result_root": args.result_root.resolve(strict=False),
            "run_id": args.run_id,
            "resume_existing": args.resume_existing,
        }
        if args.preflight_only:
            prepared = (
                runtime.prepare_phase4_remote_qwen_f4_direct_stability(
                    **common
                )
            )
            print(
                "[P4-05-F4-DIRECT] "
                f"status={('existing_summary_validated' if prepared.get('summary') is not None else 'prepared_no_model')} "
                f"run_id={prepared['run_id']} "
                f"result_root={prepared['result_root']}",
                flush=True,
            )
            return 0
        result = runtime.run_phase4_remote_qwen_f4_direct_stability(
            **common,
            confirm_f4_direct_stability=True,
            console=sys.stderr,
        )
    except (
        OSError,
        runtime.Phase4RemoteQwenF4DirectStabilityError,
        fresh.Phase4RemoteFreshIntegratedError,
    ) as exc:
        print(
            f"[P4-05-F4-DIRECT] failed closed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2

    aggregate = result["aggregate"]
    assert isinstance(aggregate, dict)
    print(
        "[P4-05-F4-DIRECT] "
        f"status=revision_complete "
        f"quality_target_met={result['quality_target_met']} "
        f"completed_cases={aggregate['case_count']} "
        f"new_f4_calls={result['new_model_generate_calls']} "
        f"f4_raw_direct_pass={aggregate['f4_raw_direct_pass_count']} "
        f"first_pass={aggregate['downstream_first_pass_success_count']} "
        f"repair={aggregate['repair_attempted_count']} "
        f"fallback={aggregate['g0_fallback_count']} "
        f"failed_closed={aggregate['failed_closed_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
