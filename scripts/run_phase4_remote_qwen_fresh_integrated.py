"""Run the bounded P4-05 fresh F1-F4 BF16 integrated AutoDL pilot."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from req2web_runtime.phase4_remote_qwen_fresh_integrated import (
    P4_05_PILOT_ID,
    Phase4RemoteFreshIntegratedError,
    run_phase4_remote_qwen_fresh_integrated,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one P4-05 fresh F1-F4 Qwen3.5-9B integrated pilot on "
            "AutoDL RTX 5090 GPU0 using BF16, no quantization, one persistent "
            "worker, one call per node, no retry, and no truncation."
        )
    )
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--integrity-evidence", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument(
        "--confirm-one-remote-fresh-integrated-run",
        action="store_true",
        help="Confirm the four bounded remote model calls for this P4-05 pilot.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Write and validate the offline preflight without loading or generating.",
    )
    parser.add_argument("--run-id", default=None)
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
    if (
        args.preflight_only is False
        and args.confirm_one_remote_fresh_integrated_run is not True
    ):
        parser.error(
            "--confirm-one-remote-fresh-integrated-run is required before "
            "the worker may load or generate"
        )
    _offline_process()
    try:
        if args.preflight_only:
            from req2web_runtime.phase4_remote_qwen_fresh_integrated import (
                prepare_phase4_remote_qwen_fresh_integrated,
            )

            prepared = prepare_phase4_remote_qwen_fresh_integrated(
                model_root=args.model_root.resolve(strict=True),
                integrity_evidence=args.integrity_evidence.resolve(strict=True),
                result_root=args.result_root.resolve(strict=False),
                run_id=args.run_id,
            )
            print(
                "[P4-05] "
                f"status=prepared_no_model run_id={prepared['run_id']} "
                f"result_root={args.result_root}",
                flush=True,
            )
            return 0
        print(f"[P4-05] pilot={P4_05_PILOT_ID} preflight started", flush=True)
        result = run_phase4_remote_qwen_fresh_integrated(
            model_root=args.model_root.resolve(strict=True),
            integrity_evidence=args.integrity_evidence.resolve(strict=True),
            result_root=args.result_root.resolve(strict=False),
            confirm_one_remote_fresh_integrated_run=True,
            run_id=args.run_id,
            console=sys.stderr,
        )
    except (OSError, Phase4RemoteFreshIntegratedError) as exc:
        print(f"[P4-05] failed closed: {exc}", file=sys.stderr, flush=True)
        return 2
    print(
        "[P4-05] "
        f"status={result['status']} "
        f"model_generate_calls={result['model_generate_calls']} "
        f"normalized_node_contract_success="
        f"{result['normalized_node_contract_success']} "
        f"delivery_status={result.get('delivery_status')} "
        f"result_root={args.result_root}",
        flush=True,
    )
    return 0 if result["status"] == "delivery_terminal_success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
