from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from req2web_runtime.autodl_a3_closeout import validate_a3_closeout_bundle_bytes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a local canonical AutoDL A3 structural evidence bundle and readiness receipt."
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()

    bundle, receipt = validate_a3_closeout_bundle_bytes(
        args.bundle.read_bytes(), args.receipt.read_bytes()
    )
    envelope = {
        "bundle": bundle.to_dict(),
        "readiness_receipt": receipt.to_dict(),
        "validation": "local_structural_only_no_action",
    }
    print(json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
