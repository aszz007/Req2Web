"""Aggregate or replay twelve terminal publication semantic results."""

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
    validate_phase5_publication_semantic_summary,
    write_phase5_publication_semantic_summary,
)


def _load_json(path: Path, name: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise Phase5PublicationSemanticError(f"{name} is unavailable")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5PublicationSemanticError(f"{name} is not JSON") from exc
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase5PublicationSemanticError(
            f"{name} is not canonical JSON"
        ) from exc
    if not isinstance(value, dict) or raw != canonical:
        raise Phase5PublicationSemanticError(
            f"{name} is not a canonical JSON object"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate or replay the twelve terminal Phase 5 publication "
            "semantic results. This command never calls a model or changes "
            "browser, package, or F1-F4 evidence."
        )
    )
    parser.add_argument("--revalidation-root", required=True, type=Path)
    parser.add_argument("--browser-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Replay an existing aggregate summary without writing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        revalidation_root = args.revalidation_root.resolve()
        browser_root = args.browser_root.resolve()
        manifest = load_phase5_publication_semantic_manifest(
            path=args.manifest.resolve(),
            revalidation_root=revalidation_root,
            browser_root=browser_root,
        )
        if args.validate_only:
            summary = validate_phase5_publication_semantic_summary(
                value=_load_json(args.summary.resolve(), "semantic summary"),
                manifest=manifest,
                revalidation_root=revalidation_root,
                browser_root=browser_root,
                results_root=args.results_root.resolve(),
            )
        else:
            summary = write_phase5_publication_semantic_summary(
                output_path=args.summary.resolve(),
                manifest=manifest,
                revalidation_root=revalidation_root,
                browser_root=browser_root,
                results_root=args.results_root.resolve(),
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
                "status": summary["status"],
                "counts": summary["counts"],
                "summary_identity": summary["summary_identity"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
