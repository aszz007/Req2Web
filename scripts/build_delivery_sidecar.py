from __future__ import annotations

"""Build one deterministic delivery sidecar or a fixed package directory batch."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_generation import (  # noqa: E402
    DeterministicDeliverySidecarBuilder,
    DeliverySidecarError,
    build_delivery_sidecar_batch,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an offline deterministic UI-reference and storyboard delivery sidecar.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--package-dir", type=Path)
    mode.add_argument("--packages-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--reference-root", type=Path, help="Explicit workspace root permitted only for declared screenshot/semantic_image assets.")
    parser.add_argument("--expected-count", type=int, default=12, help="Batch only; use 0 to allow any positive package count.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.package_dir is not None:
            if args.output_dir is None or args.output_root is not None:
                raise DeliverySidecarError("single-package mode requires --output-dir and does not accept --output-root")
            payload = DeterministicDeliverySidecarBuilder().build(args.package_dir, args.output_dir, args.reference_root).to_dict()
        else:
            if args.output_root is None or args.output_dir is not None:
                raise DeliverySidecarError("batch mode requires --output-root and does not accept --output-dir")
            expected = None if args.expected_count == 0 else args.expected_count
            payload = build_delivery_sidecar_batch(args.packages_dir, args.output_root, args.reference_root, expected)
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
