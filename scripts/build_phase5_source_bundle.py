"""Create the exact local Git bundle for the future Phase 5 no-card upload."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_runtime.phase5_source_bundle import (  # noqa: E402
    create_phase5_source_bundle,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create and verify a commit-bound local Git bundle plus canonical "
            "manifest. No staging, commit, push, SSH, upload, clone, model, or "
            "holdout action is performed."
        )
    )
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        manifest = create_phase5_source_bundle(
            repo_root=ROOT,
            source_commit=args.source_commit,
            bundle_path=args.bundle.resolve(strict=False),
            manifest_path=args.manifest.resolve(strict=False),
        )
    except (OSError, ValueError) as exc:
        print(f"[Phase 5] failed closed: {exc}", file=sys.stderr, flush=True)
        return 2
    payload = manifest.to_dict()
    print(
        "[Phase 5] "
        f"source_commit={payload['source_commit']} "
        f"bundle_sha256={payload['bundle_sha256']} "
        f"manifest_sha256={manifest.sha256()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
