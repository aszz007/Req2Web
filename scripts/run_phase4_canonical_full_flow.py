"""Run the canonical raw-requirement Phase 4 LangGraph flow."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime.phase4_canonical_full_flow import (  # noqa: E402
    CANARY_COUNT,
    CASE_COUNT,
    Phase4CanonicalFullFlowError,
    run_phase4_canonical_full_flow,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the sole active Phase 4 flow from raw requirements through "
            "the shared LangGraph topology and result packages. Start with "
            "three canaries; all ten require a real-browser canary receipt."
        )
    )
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--integrity-evidence", required=True, type=Path)
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=ROOT / "data/processed/rag",
    )
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--max-cases",
        type=int,
        choices=(CANARY_COUNT, CASE_COUNT),
        default=CANARY_COUNT,
        help="Run the three canaries or resume the same run to all ten cases.",
    )
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument(
        "--browser-canary-receipt",
        type=Path,
        help=(
            "Required with --max-cases 10; must bind three reliable real-"
            "browser canaries from this run."
        ),
    )
    parser.add_argument(
        "--confirm-canonical-full-flow",
        action="store_true",
        help=(
            "Confirm bounded real-model execution: one call per F node, no "
            "automatic retry, three canaries first, ten cases maximum."
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
    if args.confirm_canonical_full_flow is not True:
        parser.error("--confirm-canonical-full-flow is required")
    _offline_process()
    try:
        summary = run_phase4_canonical_full_flow(
            model_root=args.model_root,
            integrity_evidence=args.integrity_evidence,
            index_dir=args.index_dir,
            result_root=args.result_root,
            confirm_canonical_full_flow=True,
            max_cases=args.max_cases,
            resume_existing=args.resume_existing,
            browser_canary_receipt=args.browser_canary_receipt,
            run_id=args.run_id,
            console=sys.stderr,
        )
    except (OSError, Phase4CanonicalFullFlowError, ValueError) as exc:
        print(
            f"[P4-CANONICAL-FLOW] failed closed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    print(
        "[P4-CANONICAL-FLOW] "
        f"status={summary['status']} "
        f"completed_cases={summary['completed_case_count']} "
        f"generate_started="
        f"{summary['aggregate']['total_generate_started_count']} "
        f"first_pass="
        f"{summary['aggregate']['downstream_first_pass_success_count']} "
        "real_browser_executed=false",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
