from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain  # noqa: E402
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    DeterministicResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
)
from req2web_rag import RetrieverConfig, create_retriever  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a deterministic req2web.result.package.v1 directory."
    )
    parser.add_argument("requirement", help="One vague software or webpage requirement")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Destination directory for the complete result package",
    )
    parser.add_argument(
        "--index-dir", type=Path, default=ROOT / "data/processed/rag"
    )
    parser.add_argument("--retriever-backend", default="tfidf")
    parser.add_argument("--top-k", type=int, default=2, help="Results per RAG role")
    parser.add_argument("--target-device")
    parser.add_argument("--task-type")
    parser.add_argument(
        "--constraint",
        action="append",
        default=[],
        help="Optional explicit constraint; repeat for multiple values",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        retriever = create_retriever(
            RetrieverConfig(
                index_dir=args.index_dir.resolve(),
                backend=args.retriever_backend,
            )
        )
        context = MinimalAgentChain(
            DeterministicRequirementProvider(),
            retriever,
            top_k_per_role=args.top_k,
        ).run(
            args.requirement,
            target_device=args.target_device,
            task_type=args.task_type,
            constraints=args.constraint,
        )
        page_spec = PageSpecBuilder().build(context)
        output_dir = args.output_dir.expanduser().resolve(strict=False)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_dir.parent / (
            f".{output_dir.name}.render-{uuid.uuid4().hex}"
        )
        temporary.mkdir()
        try:
            render_result = DeterministicPageRenderer().render(
                page_spec=page_spec,
                output_dir=temporary,
            )
            report = MinimalConsistencyChecker().check(page_spec, render_result)
            result = DeterministicResultPackager().package(
                context=context,
                page_spec=page_spec,
                render_result=render_result,
                consistency_report=report,
                package_dir=output_dir,
            )
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        result.validate()
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
