"""Run one advisory-only local Qwen semantic requirement review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.semantic_assist import (  # noqa: E402
    HIGH_GPU_PROFILE,
    LOCAL_LOW_GPU_PROFILE,
    SemanticRequirementAssistError,
    SemanticRequirementAssistStore,
    provider_capabilities,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one advisory-only Qwen3.5-9B semantic requirement review. "
            "The result cannot rewrite canonical B or enter F1-F4."
        )
    )
    parser.add_argument("--show-providers", action="store_true")
    parser.add_argument("--requirement")
    parser.add_argument("--target-device")
    parser.add_argument("--task-type")
    parser.add_argument("--constraint", action="append", default=[])
    parser.add_argument(
        "--profile",
        choices=(LOCAL_LOW_GPU_PROFILE, HIGH_GPU_PROFILE),
        default=LOCAL_LOW_GPU_PROFILE,
    )
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--integrity-evidence", type=Path)
    parser.add_argument(
        "--result-root",
        type=Path,
        default=ROOT / "outputs" / "req2web_semantic_assist_runs",
    )
    parser.add_argument(
        "--confirm-local-model-action",
        action="store_true",
        help="Required before the one-call local Qwen worker may start.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.show_providers:
        print(
            json.dumps(
                provider_capabilities(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    if not args.requirement:
        print("--requirement is required for a semantic-assist run", file=sys.stderr)
        return 2
    if not args.confirm_local_model_action:
        print("--confirm-local-model-action is required", file=sys.stderr)
        return 2
    if args.model_root is None or args.integrity_evidence is None:
        print(
            "--model-root and --integrity-evidence are required for local Qwen",
            file=sys.stderr,
        )
        return 2
    try:
        store = SemanticRequirementAssistStore(
            root=args.result_root,
            model_root=args.model_root,
            integrity_evidence=args.integrity_evidence,
            profile_name=args.profile,
        )
        result = store.run(
            {
                "requirement": args.requirement,
                "target_device": args.target_device,
                "task_type": args.task_type,
                "constraints": args.constraint,
                "retriever_backend": "tfidf",
                "top_k": 2,
            }
        )
    except (OSError, ValueError, SemanticRequirementAssistError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if result["status"] == "advisory_available" else 1


if __name__ == "__main__":
    raise SystemExit(main())
