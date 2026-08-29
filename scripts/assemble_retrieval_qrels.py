"""Assemble assisted qrels from completed Phase 6 human review packets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_rag.retrieval_qrels import (  # noqa: E402
    RetrievalQrelsError,
    assemble_retrieval_qrels,
)
from req2web_inspector.local_data import framework_evidence_root  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate two completed split-review packets and one completed joint "
            "resolution packet, then assemble immutable exploratory assisted qrels."
        )
    )
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--reviewer-1-packet", type=Path, required=True)
    parser.add_argument("--reviewer-2-packet", type=Path, required=True)
    parser.add_argument("--joint-resolution-packet", type=Path, required=True)
    parser.add_argument("--reviewer-1-id", required=True)
    parser.add_argument("--reviewer-2-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
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
        default=framework_evidence_root(ROOT) / "retrieval_comparison",
    )
    parser.add_argument(
        "--experiment-root",
        type=Path,
        default=framework_evidence_root(ROOT) / "retrieval_experiment",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = assemble_retrieval_qrels(
            fixture_path=args.fixture,
            index_dir=args.index_dir,
            comparison_root=args.comparison_root,
            experiment_root=args.experiment_root,
            review_root=args.review_root,
            reviewer_1_packet=args.reviewer_1_packet,
            reviewer_2_packet=args.reviewer_2_packet,
            joint_resolution_packet=args.joint_resolution_packet,
            reviewer_1_id=args.reviewer_1_id,
            reviewer_2_id=args.reviewer_2_id,
            output_path=args.output,
        )
    except (OSError, ValueError, RetrievalQrelsError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
