"""Replay the Phase 5 publication F4 policy failures without model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime.phase5_publication_policy_revalidation import (  # noqa: E402
    Phase5PublicationPolicyRevalidationError,
    run_phase5_publication_policy_revalidation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create separate zero-model receipts and ResultPackages for the "
            "three preserved Phase 5 F4 policy failures."
        )
    )
    parser.add_argument("--result-tar", required=True, type=Path)
    parser.add_argument("--result-manifest", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--confirm-zero-model-revalidation",
        action="store_true",
        help=(
            "Confirm that no model call, retry, normalization, repair, or "
            "fallback will be performed."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_phase5_publication_policy_revalidation(
            result_tar_path=args.result_tar,
            result_manifest_path=args.result_manifest,
            output_root=args.output_root,
            confirm_zero_model_revalidation=(
                args.confirm_zero_model_revalidation
            ),
        )
    except (OSError, ValueError, Phase5PublicationPolicyRevalidationError) as exc:
        print(f"[P5-F4-REVALIDATION] failed closed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": summary["status"],
                "run_id": summary["run_id"],
                "counts": summary["counts"],
                "output_root": str(args.output_root.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
