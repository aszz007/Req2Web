"""Revalidate one captured F4 result without loading or calling a model."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from req2web_runtime.phase4_local_qwen_f4_revalidation import (
    Phase4LocalQwenF4RevalidationError,
    revalidate_phase4_local_qwen_f4_result,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize only F4 generic audit-ref ordering, then revalidate "
            "registry, candidate composition, and the existing assembler. "
            "This command loads no model and performs no generation."
        )
    )
    parser.add_argument("--source-result-root", required=True, type=Path)
    parser.add_argument("--f3-result-root", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument(
        "--confirm-no-model-revalidation",
        action="store_true",
        help="Confirm deterministic revalidation with zero model calls.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.confirm_no_model_revalidation is not True:
        parser.error("--confirm-no-model-revalidation is required")
    try:
        result = revalidate_phase4_local_qwen_f4_result(
            source_result_root=args.source_result_root.resolve(strict=True),
            f3_result_root=args.f3_result_root.resolve(strict=True),
            result_root=args.result_root.resolve(strict=False),
            confirm_no_model_revalidation=True,
        )
    except (OSError, Phase4LocalQwenF4RevalidationError) as exc:
        print(f"[P4-03D4-F4-REVALIDATION] failed closed: {exc}", file=sys.stderr)
        return 2
    print(
        "[P4-03D4-F4-REVALIDATION] "
        f"status={result['status']} "
        f"raw_model_contract_success={result['raw_model_contract_success']} "
        f"normalized_node_contract_success="
        f"{result['normalized_node_contract_success']} "
        f"model_generate_calls={result['model_generate_calls']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
