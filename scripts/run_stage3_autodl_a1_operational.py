"""Print, but never execute, the exact real-A1 worker argv."""
from __future__ import annotations
import argparse
import json
from req2web_runtime.autodl_a1_operational import build_real_a1_worker_command


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Print the reviewed AutoDL A1 worker command without execution")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--controls", required=True)
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    args = parser.parse_args(argv)
    print(json.dumps({"action": "not_executed", "command": build_real_a1_worker_command(args.plan, args.controls, args.package_root, args.model_root, args.evidence_root)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
