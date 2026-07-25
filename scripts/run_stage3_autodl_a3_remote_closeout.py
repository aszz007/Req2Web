"""Validate A3 remote-closeout structural staging without any external action.

This command has no SSH, browser, AutoDL, credential, model, or network client.
The optional destructive flag is retained only to fail closed: no public CLI can
execute TERM, deletion, release, revocation, or evidence writes.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.autodl_a3_remote_closeout import A3RemoteCloseoutError, run_a3_remote_closeout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate A3 remote-closeout structural staging only; destructive execution is unavailable.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--log-root", required=True)
    parser.add_argument("--transfer-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--execute-a3-closeout", action="store_true")
    args = parser.parse_args(argv)
    try:
        envelope = run_a3_remote_closeout(
            args.plan, args.work_root, args.cache_root, args.log_root,
            args.transfer_root, args.evidence_root, args.execute_a3_closeout,
        )
    except A3RemoteCloseoutError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sys.stdout.buffer.write(envelope.canonical_bytes())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
