"""Run or validate the deterministic Phase 6 retrieval comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_rag.retrieval_comparison import (  # noqa: E402
    RetrievalComparisonError,
    run_retrieval_comparison,
    validate_retrieval_comparison,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the local BM25, RRF, and TF-IDF backends on frozen Demo v2 "
            "cases. No model, GPU, browser, remote service, H1, or gold is used."
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
        "--output-root",
        type=Path,
        default=ROOT / "outputs" / "phase6_retrieval_comparison_v3",
    )
    parser.add_argument(
        "--qrels",
        type=Path,
        help=(
            "Optional validated qrels. Assisted qrels produce exploratory pooled "
            "metrics only; without qrels, ranking quality remains unavailable."
        ),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Replay existing comparison evidence without writing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.validate_only:
            report = validate_retrieval_comparison(
                fixture_path=args.fixture,
                index_dir=args.index_dir,
                output_root=args.output_root,
                qrels_path=args.qrels,
            )
        else:
            report = run_retrieval_comparison(
                fixture_path=args.fixture,
                index_dir=args.index_dir,
                output_root=args.output_root,
                qrels_path=args.qrels,
            )
    except (OSError, ValueError, RetrievalComparisonError) as exc:
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
                "status": report["status"],
                "methods": report["methods"],
                "query_role_unit_count": report["query_role_unit_count"],
                "ranking_evaluation_status": report["ranking_evaluation"]["status"],
                "quality_winner_declared": report["pairwise_summary"][
                    "quality_winner_declared"
                ],
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
