"""Run objective local-browser checks for twelve Phase 5 packages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime.phase5_publication_browser import (  # noqa: E402
    Phase5PublicationBrowserError,
    replay_phase5_publication_browser_result,
    run_phase5_publication_browser,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the shared objective Playwright acceptance authority over all "
            "twelve exact Phase 5 publication engineering ResultPackages."
        )
    )
    parser.add_argument("--revalidation-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--timeout-ms", type=int, default=5_000)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Replay an existing browser output without launching Chrome.",
    )
    parser.add_argument(
        "--confirm-real-browser",
        action="store_true",
        help=(
            "Confirm twelve local browser executions without semantic-model "
            "calls, retries, package repair, or historical-result relabeling."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.validate_only:
            summary = replay_phase5_publication_browser_result(
                revalidation_root=args.revalidation_root,
                browser_root=args.output_root,
            )
        else:
            summary = run_phase5_publication_browser(
                revalidation_root=args.revalidation_root,
                output_root=args.output_root,
                confirm_real_browser=args.confirm_real_browser,
                timeout_ms=args.timeout_ms,
            )
    except (OSError, ValueError, Phase5PublicationBrowserError) as exc:
        print(f"[P5-BROWSER] failed closed: {exc}", file=sys.stderr, flush=True)
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
        ),
        flush=True,
    )
    return 0 if summary["all_objective_browser_checks_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
