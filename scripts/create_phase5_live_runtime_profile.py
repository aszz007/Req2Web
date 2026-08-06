"""Create a Phase 5 runtime profile by rebinding only current GPU facts."""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path

from req2web_runtime.phase5_live_runtime_profile import (
    create_phase5_live_runtime_profile,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a write-once Phase 5 runtime profile from an accepted "
            "Phase 4 base profile and current GPU facts. No model is loaded."
        )
    )
    parser.add_argument("--base-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    profile = create_phase5_live_runtime_profile(
        base_profile_path=args.base_profile.resolve(strict=True),
        output_path=args.output.resolve(strict=False),
    )
    raw = profile.canonical_bytes()
    print(
        "[Phase 5] "
        f"profile_id={profile.profile_id} "
        f"device_uuid={profile.device_uuid} "
        f"profile_sha256={sha256(raw).hexdigest()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
