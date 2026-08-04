"""Run zero-model revalidation for one terminal fresh-integrated result."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from req2web_runtime.phase4_local_qwen_fresh_integrated_revalidation import (
    Phase4LocalQwenFreshIntegratedRevalidationError,
    revalidate_phase4_local_qwen_fresh_integrated,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Revalidate one terminal fresh-integrated local-Qwen result with "
            "zero model calls. The source result root is read-only."
        )
    )
    parser.add_argument("--source-result-root", type=Path, required=True)
    parser.add_argument("--qualification-savepoint-root", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument(
        "--confirm-no-model-revalidation",
        action="store_true",
        help="Confirm that this command must perform zero model generations.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.confirm_no_model_revalidation is not True:
        parser.error("--confirm-no-model-revalidation is required")
    print("[P4-03I-REVALIDATION] preflight started", flush=True)
    try:
        result = revalidate_phase4_local_qwen_fresh_integrated(
            source_result_root=args.source_result_root.resolve(strict=True),
            qualification_savepoint_root=args.qualification_savepoint_root.resolve(
                strict=True
            ),
            result_root=args.result_root.resolve(strict=False),
            confirm_no_model_revalidation=True,
        )
    except (
        OSError,
        Phase4LocalQwenFreshIntegratedRevalidationError,
    ) as exc:
        print(
            f"[P4-03I-REVALIDATION] failed closed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    print("[P4-03I-REVALIDATION] preflight completed", flush=True)
    print(
        "[P4-03I-REVALIDATION] "
        f"status={result['status']} "
        f"agent_chain_system_output_usable="
        f"{result['agent_chain_system_output_usable']} "
        f"source_model_generate_calls={result['source_model_generate_calls']} "
        f"revalidation_model_generate_calls="
        f"{result['revalidation_model_generate_calls']} "
        f"result_root={args.result_root}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
