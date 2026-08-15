"""Build or validate the deterministic Req2Web Inspector candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.phase6_release_candidate import (  # noqa: E402
    Phase6ReleaseCandidateError,
    build_phase6_release_candidate,
    validate_phase6_release_candidate,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build or validate a deterministic local reviewer ZIP. This command "
            "does not publish, call a model, use a GPU, or read H1/gold."
        )
    )
    parser.add_argument(
        "--reviewer-root",
        type=Path,
        default=ROOT / "release" / "phase6_reviewer_v16",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "release" / "phase6_release_candidate_v16",
    )
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = (
            validate_phase6_release_candidate(args.output_root)
            if args.validate_only
            else build_phase6_release_candidate(
                reviewer_bundle_root=args.reviewer_root,
                output_root=args.output_root,
            )
        )
    except (OSError, ValueError, Phase6ReleaseCandidateError) as exc:
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
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "archive": manifest["archive"],
                "public_release_ready": manifest["public_release_ready"],
                "blocking_gate": manifest["blocking_gate"],
                "output_root": str(args.output_root.resolve()),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
