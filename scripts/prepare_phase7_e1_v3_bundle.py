"""Build the E1 v3 server payload without local observer material."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import phase7_e1_v3_runtime as runtime
import prepare_phase7_e1_v2_bundle as _base


OVERLAY = (
    "scripts/phase7_direct_html_worker.py",
    "scripts/phase7_e1_v2_runtime.py",
    "scripts/phase7_e1_v2_structured.py",
    "scripts/phase7_e1_v3_cases.py",
    "scripts/phase7_e1_v3_runtime.py",
    "src/req2web_capabilities.py",
    "src/req2web_agent/prompt_authority.py",
    "src/req2web_agent/understanding.py",
    "src/req2web_generation/consistency.py",
    "src/req2web_generation/renderer.py",
    "src/req2web_orchestration/phase4_graph.py",
)


@contextmanager
def _configured_base() -> Iterator[None]:
    saved = {
        "runtime": _base.runtime,
        "PREPARATION_PREFIX": _base.PREPARATION_PREFIX,
        "PURPOSE": _base.PURPOSE,
        "OVERLAY": _base.OVERLAY,
    }
    _base.runtime = runtime
    _base.PREPARATION_PREFIX = "e1-v3"
    _base.PURPOSE = (
        "Phase 7 E1 v3 repair-informed holdout three-arm workflow comparison"
    )
    _base.OVERLAY = OVERLAY
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(_base, name, value)

BundleError = _base.BundleError


def build(preparation: Path, base_payload: Path, destination: Path) -> dict[str, object]:
    with _configured_base():
        return _base.build(preparation, base_payload, destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument(
        "--base-payload",
        type=Path,
        default=ROOT / "outputs/phase7_autodl_prep_v1/payload-corrected-v2-20260916",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.preparation, args.base_payload, args.output), indent=2))
