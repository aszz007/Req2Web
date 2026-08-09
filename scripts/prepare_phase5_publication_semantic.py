"""Prepare or replay the twelve-row publication semantic manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_evaluation.phase5_publication_semantic import (  # noqa: E402
    Phase5PublicationSemanticError,
    load_phase5_publication_semantic_manifest,
    write_phase5_publication_semantic_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare or replay-validate the path-free twelve-row Phase 5 "
            "publication semantic manifest. No model, GPU, browser, H1, or "
            "gold action is performed."
        )
    )
    parser.add_argument("--revalidation-root", required=True, type=Path)
    parser.add_argument("--browser-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Replay an existing manifest without writing any artifact.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.validate_only:
            manifest = load_phase5_publication_semantic_manifest(
                path=args.manifest.resolve(),
                revalidation_root=args.revalidation_root.resolve(),
                browser_root=args.browser_root.resolve(),
            )
        else:
            manifest = write_phase5_publication_semantic_manifest(
                output_path=args.manifest.resolve(),
                revalidation_root=args.revalidation_root.resolve(),
                browser_root=args.browser_root.resolve(),
            )
    except (Phase5PublicationSemanticError, OSError) as exc:
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
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "validated" if args.validate_only else "prepared",
                "row_count": manifest["row_count"],
                "criterion_count": manifest["criterion_count"],
                "semantic_alignment_executed": manifest[
                    "semantic_alignment_executed"
                ],
                "manifest_identity": manifest["manifest_identity"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
