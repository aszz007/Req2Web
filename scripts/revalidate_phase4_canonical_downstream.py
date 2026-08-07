from __future__ import annotations

import argparse
import json
from pathlib import Path

from req2web_runtime.phase4_downstream_revalidation import (
    run_phase4_downstream_status_revalidation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create zero-model Phase 4 downstream status-revalidation "
            "receipts without changing historical case summaries."
        )
    )
    parser.add_argument("--flow-summary", type=Path, required=True)
    parser.add_argument("--final-browser-summary", type=Path, required=True)
    parser.add_argument("--legacy-canary-root", type=Path, required=True)
    parser.add_argument("--browser-audit-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--confirm-zero-model-revalidation",
        action="store_true",
        help=(
            "Confirm that this run performs deterministic evidence "
            "revalidation only and adds no model call or retry."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_phase4_downstream_status_revalidation(
        flow_summary_path=args.flow_summary,
        final_browser_summary_path=args.final_browser_summary,
        legacy_canary_root=args.legacy_canary_root,
        browser_audit_root=args.browser_audit_root,
        output_root=args.output_root,
        confirm_zero_model_revalidation=(
            args.confirm_zero_model_revalidation
        ),
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "run_id": result["run_id"],
                "counts": result["counts"],
                "output_root": str(args.output_root.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
