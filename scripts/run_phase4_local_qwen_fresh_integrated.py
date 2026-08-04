"""Prepare or run the bounded Phase 4 fresh integrated local-Qwen pilot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from req2web_runtime.phase4_local_qwen_fresh_integrated import (
    FRESH_INTEGRATED_PILOT_ID,
    Phase4LocalQwenFreshIntegratedError,
    prepare_phase4_local_qwen_fresh_integrated,
    run_phase4_local_qwen_fresh_integrated,
    run_phase4_local_qwen_fresh_integrated_scripted,
)


def _offline_process() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["LANGSMITH_TRACING"] = "0"
    os.environ["LANGCHAIN_TRACING_V2"] = "0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare or run the bounded Phase 4 fresh integrated local-Qwen "
            "pilot. The scripted fake-backend path is explicit and test-only."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare",
        help="Write the versioned policy, qualification receipt, and no-run seam.",
    )
    prepare.add_argument("--qualification-savepoint-root", type=Path, required=True)
    prepare.add_argument("--result-root", type=Path, required=True)
    prepare.add_argument("--pilot-id", default=FRESH_INTEGRATED_PILOT_ID)
    prepare.add_argument("--run-id", default=None)

    run = commands.add_parser(
        "run",
        help="Run one fresh integrated local-Qwen pilot through one persistent worker.",
    )
    run.add_argument("--qualification-savepoint-root", type=Path, required=True)
    run.add_argument("--model-root", type=Path, required=True)
    run.add_argument("--integrity-evidence", type=Path, required=True)
    run.add_argument("--result-root", type=Path, required=True)
    run.add_argument(
        "--predecessor-result-root",
        type=Path,
        default=None,
        help=(
            "Existing immutable fresh-integrated result root to link as a "
            "pre-worker failure; it never resets the new pilot budget."
        ),
    )
    run.add_argument(
        "--confirm-one-local-fresh-integrated-run",
        action="store_true",
        help="Required confirmation for the single fresh integrated model run.",
    )
    run.add_argument("--run-id", default=None)

    scripted = commands.add_parser(
        "scripted-run",
        help="Run only the explicit deterministic fake-backend foundation.",
    )
    scripted.add_argument("--prepared-root", type=Path, required=True)
    scripted.add_argument(
        "--confirm-no-model-run",
        action="store_true",
        help="Required confirmation that this is the fake-backend path.",
    )
    scripted.add_argument(
        "--f4-order-drift",
        action="store_true",
        help="Exercise the approved F4 generic audit-ref normalization path.",
    )
    return parser


def _console(event: dict[str, object]) -> None:
    if event.get("event") == "token_delta":
        sys.stdout.write(str(event.get("delta", "")))
        sys.stdout.flush()
        return
    sys.stderr.write(
        f"[P4-03I] {event.get('event')} "
        f"{event.get('stage', '')} {event.get('status', '')}\n"
    )
    sys.stderr.flush()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (
        args.command == "run"
        and args.confirm_one_local_fresh_integrated_run is not True
    ):
        parser.error(
            "--confirm-one-local-fresh-integrated-run is required before "
            "the fresh integrated worker may start"
        )
    try:
        if args.command == "prepare":
            result = prepare_phase4_local_qwen_fresh_integrated(
                qualification_savepoint_root=args.qualification_savepoint_root,
                result_root=args.result_root,
                run_id=args.run_id,
                pilot_id=args.pilot_id,
            )
        elif args.command == "run":
            _offline_process()
            result = run_phase4_local_qwen_fresh_integrated(
                qualification_savepoint_root=args.qualification_savepoint_root.resolve(
                    strict=True
                ),
                model_root=args.model_root.resolve(strict=True),
                integrity_evidence=args.integrity_evidence.resolve(strict=True),
                result_root=args.result_root.resolve(strict=False),
                predecessor_result_root=(
                    None
                    if args.predecessor_result_root is None
                    else args.predecessor_result_root.resolve(strict=True)
                ),
                confirm_one_local_fresh_integrated_run=(
                    args.confirm_one_local_fresh_integrated_run
                ),
                run_id=args.run_id,
                console=_console,
            )
        else:
            result = run_phase4_local_qwen_fresh_integrated_scripted(
                prepared_root=args.prepared_root,
                confirm_fresh_integrated_run=args.confirm_no_model_run,
                f4_order_drift=args.f4_order_drift,
                console=_console,
            )
    except Phase4LocalQwenFreshIntegratedError as exc:
        print(f"[P4-03I] failed closed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
