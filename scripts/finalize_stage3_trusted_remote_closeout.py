#!/usr/bin/env python3
"""Finalize a downloaded trusted-remote return bundle after external closeout."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.autodl_trusted_remote_executor import (
    TrustedRemoteExecutionPackageV2,
    finalize_downloaded_trusted_remote_closeout_v2,
)
from req2web_runtime.autodl_trusted_remote_records import TrustedRemoteActionTimePlan


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Finalize a validated trusted-remote return bundle."
    )
    parser.add_argument("--action-time-plan", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--return-root", required=True)
    parser.add_argument("--instance-release-evidence", required=True)
    parser.add_argument("--temporary-access-revocation-evidence", required=True)
    args = parser.parse_args(argv)

    manifest = finalize_downloaded_trusted_remote_closeout_v2(
        TrustedRemoteActionTimePlan.from_bytes(Path(args.action_time_plan).read_bytes()),
        TrustedRemoteExecutionPackageV2.from_bytes(Path(args.package).read_bytes()),
        Path(args.return_root),
        Path(args.instance_release_evidence).read_bytes(),
        Path(args.temporary_access_revocation_evidence).read_bytes(),
    )
    sys.stdout.buffer.write(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())