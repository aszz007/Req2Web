#!/usr/bin/env python3
"""Fixed child entrypoint for the Req2Web trusted-remote Qwen two-case worker."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.autodl_trusted_remote_executor import _worker_main_v2


if __name__ == "__main__":
    raise SystemExit(_worker_main_v2())
