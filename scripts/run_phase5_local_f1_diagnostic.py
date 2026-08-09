"""Run the component-only local NF4 Core 2 F1 diagnostic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from req2web_runtime.phase5_local_f1_diagnostic import (
    run_phase5_local_f1_diagnostic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one component-only local NF4 F1 call from the exact preserved "
            "Phase 5 Core 2 baseline input. This is not a publication rerun, "
            "full-chain run, or formal evaluation."
        )
    )
    parser.add_argument("--source-tar", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--integrity-evidence", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--diagnostic-run-id", required=True)
    parser.add_argument(
        "--confirm-one-local-nf4-f1-call",
        action="store_true",
        help="Required confirmation for the one-call component diagnostic.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.confirm_one_local_nf4_f1_call:
        raise SystemExit("explicit one-call diagnostic confirmation is required")
    summary = run_phase5_local_f1_diagnostic(
        tar_path=args.source_tar.resolve(),
        manifest_path=args.source_manifest.resolve(),
        model_root=args.model_root.resolve(),
        integrity_evidence=args.integrity_evidence.resolve(),
        result_root=args.result_root.resolve(),
        diagnostic_run_id=args.diagnostic_run_id,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary["raw_contract_pass"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
