"""Prepare a deterministic three-case Phase 5 semantic canary manifest."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_evaluation.phase5_semantic_canary import (  # noqa: E402
    Phase5SemanticCanaryError,
    write_phase5_semantic_canary_manifest,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare and replay-validate a three-case Phase 5 semantic "
            "evaluation no-action manifest. No model or browser action is run."
        )
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        required=True,
        help="New manifest path. Existing files are never overwritten.",
    )
    parser.add_argument(
        "--case",
        action="append",
        nargs=4,
        metavar=("ORDER", "CASE_ID", "AUDIT_ROOT", "RESULT_PACKAGE_ROOT"),
        help=(
            "One case input. Repeat exactly three times with order 1, 2, 3."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.case is None or len(args.case) != 3:
        parser.error("exactly three --case arguments are required")
    cases: list[dict[str, object]] = []
    for values in args.case:
        order, case_id, audit_root, result_package_root = values
        try:
            case_order = int(order)
        except ValueError:
            parser.error("case order must be an integer")
        cases.append(
            {
                "case_order": case_order,
                "case_id": case_id,
                "audit_root": Path(audit_root),
                "result_package_root": Path(result_package_root),
            }
        )
    try:
        manifest = write_phase5_semantic_canary_manifest(
            output_path=args.manifest_output,
            cases=cases,
        )
    except Phase5SemanticCanaryError as exc:
        parser.error(str(exc))
    print(
        "Prepared no-action semantic canary manifest "
        f"with {len(manifest['cases'])} cases."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
