"""Explicit Linux-only Real-A1 production evidence gate CLI."""
from __future__ import annotations

import argparse
import json
import sys

from req2web_runtime.autodl_a1_production_gate import run_a1_production_gate


ENTRYPOINT_STATUS = "historical"
ENTRYPOINT_MODE = "replay_only"
ACTIVE_DEFAULT_ENTRY = False
_ENTRYPOINT_HELP = (
    "HISTORICAL STAGE 3 ENTRYPOINT; NOT THE ACTIVE DEFAULT FULL-FLOW ENTRY. "
    "entrypoint_status=historical active_default_entry=false "
    "entrypoint_mode=replay_only."
)


def _emit_entrypoint_notice() -> None:
    print(
        json.dumps(
            {
                "active_default_entry": ACTIVE_DEFAULT_ENTRY,
                "entrypoint_mode": ENTRYPOINT_MODE,
                "entrypoint_status": ENTRYPOINT_STATUS,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=_ENTRYPOINT_HELP
        + " Validate committed Real-A1 production evidence"
    )
    parser.add_argument("--evaluate-production-a1", action="store_true")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--controls", required=True)
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    args = parser.parse_args(argv)
    _emit_entrypoint_notice()
    if not args.evaluate_production_a1:
        raise ValueError("explicit_production_a1_gate_required")
    result = run_a1_production_gate(args.plan, args.controls, args.package_root, args.model_root, args.evidence_root)
    sys.stdout.buffer.write(result.bundle.canonical_bytes())
    raise SystemExit(result.return_code)


if __name__ == "__main__":
    main()
