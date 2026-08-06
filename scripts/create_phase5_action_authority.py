"""Create one hash-only Phase 5 final action authority receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_runtime.phase5_action_authority import (  # noqa: E402
    create_phase5_final_action_authority,
    write_phase5_final_action_authority,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a canonical hash-only Phase 5 final action receipt. "
            "This does not connect to a server, load a model, or open H1."
        )
    )
    parser.add_argument("--source-json", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def _outside_repository(path: Path, *, must_exist: bool, name: str) -> Path:
    resolved = path.resolve(strict=must_exist)
    try:
        resolved.relative_to(ROOT.resolve(strict=True))
    except ValueError:
        return resolved
    raise ValueError(f"{name} must remain outside the repository")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        source = _outside_repository(
            args.source_json,
            must_exist=True,
            name="final action source",
        )
        output = _outside_repository(
            args.output,
            must_exist=False,
            name="final action output",
        )
        authority = create_phase5_final_action_authority(
            json.loads(source.read_text(encoding="utf-8"))
        )
        write_phase5_final_action_authority(output, authority)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[Phase 5] failed closed: {exc}", file=sys.stderr, flush=True)
        return 2
    print(
        f"[Phase 5] final_action_authority_sha256={authority.sha256()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
