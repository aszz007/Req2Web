"""Replay recorded A3 closeout evidence without performing any action."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.autodl_a3_finalizer import A3FinalizerError, finalizer_envelope_from_result, finalize_a3_closeout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate recorded A3 closeout evidence only; no external action is available.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--remote-envelope", required=True)
    parser.add_argument("--remote-events", required=True)
    parser.add_argument("--foundation-bundle", required=True)
    parser.add_argument("--foundation-receipt", required=True)
    args = parser.parse_args(argv)
    try:
        result = finalize_a3_closeout(
            Path(args.plan).read_bytes(),
            Path(args.remote_envelope).read_bytes(),
            Path(args.remote_events).read_bytes(),
            Path(args.foundation_bundle).read_bytes(),
            Path(args.foundation_receipt).read_bytes(),
        )
    except (A3FinalizerError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sys.stdout.buffer.write(finalizer_envelope_from_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
