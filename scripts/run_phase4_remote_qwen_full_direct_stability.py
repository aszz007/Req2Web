"""Historical prebuilt-B ten-case runner; not the active full-flow entry."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from req2web_runtime import phase4_remote_qwen_fresh_integrated as fresh
from req2web_runtime import phase4_remote_qwen_full_direct_stability as runtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "HISTORICAL/REPLAY-ONLY: run the old prebuilt-canonical-B ten-case "
            "experiment. This is not the active raw-requirement full flow."
        )
    )
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--integrity-evidence", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help="Resume an existing owned result root after evidence validation.",
    )
    parser.add_argument(
        "--confirm-fresh-ten-case-full-chain",
        action="store_true",
        help="Confirm forty bounded model calls across ten fresh F1-F4 cases.",
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
    if args.confirm_fresh_ten_case_full_chain is not True:
        parser.error(
            "--confirm-fresh-ten-case-full-chain is required before generation"
        )
    _offline_process()
    try:
        result = runtime.run_phase4_remote_qwen_full_direct_stability(
            model_root=args.model_root.resolve(strict=True),
            integrity_evidence=args.integrity_evidence.resolve(strict=True),
            result_root=args.result_root.resolve(strict=False),
            confirm_full_direct_stability=True,
            run_id=args.run_id,
            console=sys.stderr,
            resume_existing=args.resume_existing,
        )
    except (
        OSError,
        runtime.Phase4RemoteQwenFullDirectStabilityError,
        fresh.Phase4RemoteFreshIntegratedError,
    ) as exc:
        print(
            f"[P4-05-FULL-DIRECT] failed closed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    aggregate = result["aggregate"]
    assert isinstance(aggregate, dict)
    print(
        "[P4-05-FULL-DIRECT] "
        f"status=revision_complete "
        f"quality_target_met={result['quality_target_met']} "
        f"completed_cases={aggregate['case_count']} "
        f"new_model_calls={result['new_model_generate_calls']} "
        f"all_nodes_raw_pass={result['all_nodes_raw_contract_pass']} "
        f"first_pass={aggregate['downstream_first_pass_success_count']} "
        f"normalization={aggregate['f4_normalized_case_count']} "
        f"repair={aggregate['repair_attempted_count']} "
        f"fallback={aggregate['g0_fallback_count']} "
        f"failed_closed={aggregate['failed_closed_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
