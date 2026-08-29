"""Prepare or validate two-human worklists from completed LLM prelabels."""

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
    prepare_human_review_packets,
    validate_human_review_packets,
)
from req2web_inspector.local_data import framework_evidence_root  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one externally completed method-blind LLM prelabel packet "
            "and prepare deterministic split-review worklists for two humans. "
            "This command never calls a model or external API."
        )
    )
    parser.add_argument("--llm-prelabel-packet", type=Path)
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
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs" / "phase6_retrieval_human_review_v1",
    )
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.validate_only:
            result = validate_human_review_packets(
                fixture_path=args.fixture,
                index_dir=args.index_dir,
                comparison_root=args.comparison_root,
                experiment_root=args.experiment_root,
                output_root=args.output_root,
            )
        else:
            if args.llm_prelabel_packet is None:
                raise RetrievalQrelsError(
                    "--llm-prelabel-packet is required unless --validate-only is used"
                )
            result = prepare_human_review_packets(
                fixture_path=args.fixture,
                index_dir=args.index_dir,
                comparison_root=args.comparison_root,
                experiment_root=args.experiment_root,
                llm_prelabel_packet=args.llm_prelabel_packet,
                output_root=args.output_root,
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
