"""Build the precomputed, read-only Req2Web Inspector bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.phase6_replay import (  # noqa: E402
    Phase6ReplayError,
    build_phase6_reviewer_bundle,
)
from req2web_inspector.local_data import (  # noqa: E402
    framework_evidence_root,
    inspector_replay_bundle_root,
)


DEFAULT_RETURN_ROOT = Path(
    r"D:\Req2WebPhase5Returns\phase5-publication-path2-v16-full-20260809-a"
)
DEFAULT_SEMANTIC_ROOT = Path(
    r"D:\Req2WebOwnerCustody\phase5_publication_semantic_20260809_v1"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a self-contained Req2Web Inspector replay from validated "
            "Phase 5 Path 2 artifacts. This command never calls a model, "
            "launches a browser, or reads H1/gold."
        )
    )
    parser.add_argument(
        "--result-tar",
        type=Path,
        default=DEFAULT_RETURN_ROOT / "result_return.tar",
    )
    parser.add_argument(
        "--result-manifest",
        type=Path,
        default=DEFAULT_RETURN_ROOT / "result_return_manifest.json",
    )
    parser.add_argument(
        "--revalidation-root",
        type=Path,
        default=framework_evidence_root(ROOT) / "p5_policy_replay",
    )
    parser.add_argument(
        "--browser-root",
        type=Path,
        default=framework_evidence_root(ROOT) / "p5_browser_final",
    )
    parser.add_argument(
        "--semantic-manifest",
        type=Path,
        default=DEFAULT_SEMANTIC_ROOT / "action_evidence" / "publication_semantic_manifest.json",
    )
    parser.add_argument(
        "--semantic-summary",
        type=Path,
        default=DEFAULT_SEMANTIC_ROOT / "action_evidence" / "publication_semantic_summary.json",
    )
    parser.add_argument(
        "--semantic-results-root",
        type=Path,
        default=DEFAULT_SEMANTIC_ROOT / "terminal12",
    )
    parser.add_argument(
        "--retrieval-fixture",
        type=Path,
        default=ROOT / "fixtures" / "demo_v2_regression_cases_v1.json",
    )
    parser.add_argument(
        "--retrieval-index-dir",
        type=Path,
        default=ROOT / "data" / "processed" / "rag",
    )
    parser.add_argument(
        "--retrieval-comparison-root",
        type=Path,
        default=framework_evidence_root(ROOT) / "retrieval_comparison",
    )
    parser.add_argument(
        "--retrieval-experiment-root",
        type=Path,
        default=framework_evidence_root(ROOT) / "retrieval_experiment",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=inspector_replay_bundle_root(ROOT),
        help="Untracked stable local-data location for the Inspector replay bundle.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = build_phase6_reviewer_bundle(
            result_tar_path=args.result_tar,
            result_manifest_path=args.result_manifest,
            revalidation_root=args.revalidation_root,
            browser_root=args.browser_root,
            semantic_manifest_path=args.semantic_manifest,
            semantic_summary_path=args.semantic_summary,
            semantic_results_root=args.semantic_results_root,
            retrieval_fixture_path=args.retrieval_fixture,
            retrieval_index_dir=args.retrieval_index_dir,
            retrieval_comparison_root=args.retrieval_comparison_root,
            retrieval_experiment_root=args.retrieval_experiment_root,
            output_root=args.output_root,
        )
    except (OSError, ValueError, Phase6ReplayError) as exc:
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
                "status": manifest["status"],
                "counts": manifest["counts"],
                "output_root": str(args.output_root.resolve()),
                "public_release_ready": False,
                "blocking_gate": "owner_license_decision_required",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
