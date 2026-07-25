"""Explicit Real-A1 executor CLI. No action occurs without --execute-real-a1."""
from __future__ import annotations

import argparse
import json

from req2web_runtime.autodl_a1_executor import RealA1ExecutorError, execute_real_a1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the reviewed Linux-only Real-A1 executor core")
    parser.add_argument("--execute-real-a1", action="store_true")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--controls", required=True)
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    args = parser.parse_args(argv)
    if not args.execute_real_a1:
        raise RealA1ExecutorError("explicit_execute_real_a1_required")
    result = execute_real_a1(
        args.plan,
        args.controls,
        args.package_root,
        args.model_root,
        args.evidence_root,
        True,
    )
    print(
        json.dumps(
            {
                "return_code": result.return_code,
                "cleanup_required": result.cleanup_required,
                "receipt_id": None if result.receipt is None else result.receipt.data["receipt_id"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    raise SystemExit(result.return_code)


if __name__ == "__main__":
    main()
