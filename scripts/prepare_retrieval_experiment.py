"""Prepare or validate the Phase 6 retrieval experiment packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_rag.retrieval_experiment import (  # noqa: E402
    RetrievalExperimentError,
    prepare_retrieval_experiment,
    validate_retrieval_experiment,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare one provider-neutral method-blind LLM prelabel template, a "
            "deterministic two-human split-review plan, and descriptive local "
            "efficiency evidence. This command does not call a model or API."
        )
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "fixtures" / "demo_v2_regression_cases_v1.json",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=ROOT / "data" / "processed" / "rag",
    )
    parser.add_argument(
        "--comparison-root",
        type=Path,
        default=ROOT / "outputs" / "phase6_retrieval_comparison_v3",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs" / "phase6_retrieval_experiment_v5",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Replay the existing preparation packet without writing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.validate_only:
            result = validate_retrieval_experiment(
                fixture_path=args.fixture,
                index_dir=args.index_dir,
                comparison_root=args.comparison_root,
                output_root=args.output_root,
            )
        else:
            result = prepare_retrieval_experiment(
                fixture_path=args.fixture,
                index_dir=args.index_dir,
                comparison_root=args.comparison_root,
                output_root=args.output_root,
            )
    except (OSError, ValueError, RetrievalExperimentError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "case_count": result["case_count"],
                "query_role_unit_count": result["query_role_unit_count"],
                "llm_prelabel_template_count": result[
                    "llm_prelabel_template_count"
                ],
                "human_reviewer_count": result["human_reviewer_count"],
                "blind_overlap_audit_count": result[
                    "blind_overlap_audit_count"
                ],
                "ranking_quality_computed": result["ranking_quality_computed"],
                "winner_declared": result["winner_declared"],
                "output_root": str(args.output_root.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
