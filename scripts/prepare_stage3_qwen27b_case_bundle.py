#!/usr/bin/env python3
"""Build the fixed local two-case bundle for the Qwen3.5-27B recovery pilot."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.qwen27b_case_bundle import build_qwen27b_case_bundle


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the no-model Qwen3.5-27B recovery-pilot case bundle."
    )
    parser.add_argument(
        "--repository-archive",
        type=Path,
        required=True,
        help="Accepted policy-filtered repository archive.",
    )
    parser.add_argument(
        "--repository-archive-manifest",
        type=Path,
        required=True,
        help="Owning accepted repository archive manifest.",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    bundle = build_qwen27b_case_bundle(
        args.repository_archive,
        args.repository_archive_manifest,
        args.output_root,
    )
    result = {
        "state": bundle.manifest["state"],
        "bundle_id": bundle.manifest["bundle_id"],
        "sha256": bundle.sha256(),
        "case_ids": bundle.manifest["case_ids"],
        "output_root": str(bundle.root),
        "model_loaded": False,
        "run_occurred": False,
    }
    sys.stdout.buffer.write(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
