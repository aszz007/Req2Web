"""Run the bounded project-authored Phase 5 publication experiment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime.phase5_publication_action import (  # noqa: E402
    Phase5PublicationActionError,
    run_phase5_publication_action,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run four project-authored English cases under three frozen evidence "
            "conditions through the canonical upstream and shared LangGraph."
        )
    )
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--integrity-evidence", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--owner-action-receipt", required=True, type=Path)
    parser.add_argument("--source-action-commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--ssh-fingerprint-sha256", required=True)
    parser.add_argument("--time-cap-seconds", type=int, default=57600)
    parser.add_argument("--cost-cap-minor-units", type=int, default=50000)
    parser.add_argument("--storage-cap-bytes", type=int, default=4294967296)
    parser.add_argument(
        "--confirm-publication-action",
        action="store_true",
        help=(
            "Confirm the fixed 12-row, 48-call-cap project-authored action. "
            "This does not open H1/gold or formal evaluation."
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
    if args.confirm_publication_action is not True:
        parser.error("--confirm-publication-action is required")
    _offline_process()
    try:
        summary = run_phase5_publication_action(
            repository_root=ROOT,
            model_root=args.model_root,
            integrity_evidence=args.integrity_evidence,
            result_root=args.result_root,
            owner_action_receipt=args.owner_action_receipt,
            source_action_commit=args.source_action_commit,
            run_id=args.run_id,
            instance_id=args.instance_id,
            gpu_uuid=args.gpu_uuid,
            ssh_fingerprint_sha256=args.ssh_fingerprint_sha256,
            time_cap_seconds=args.time_cap_seconds,
            cost_cap_minor_units=args.cost_cap_minor_units,
            storage_cap_bytes=args.storage_cap_bytes,
            python_executable=sys.executable,
            confirm_publication_action=True,
            console=sys.stderr,
        )
    except (OSError, Phase5PublicationActionError, ValueError) as exc:
        print(f"[PHASE5-PUBLICATION] failed closed: {exc}", file=sys.stderr, flush=True)
        return 2
    print(
        "[PHASE5-PUBLICATION] "
        f"status={summary['status']} "
        f"completed_rows={summary['completed_runtime_row_count']} "
        f"generate_started={summary['aggregate']['total_generate_started_count']}",
        flush=True,
    )
    return 0 if summary["status"] == "completed_descriptive_results" else 2


if __name__ == "__main__":
    raise SystemExit(main())
