from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain  # noqa: E402
from req2web_generation import PageSpecBuilder  # noqa: E402
from req2web_rag import RetrieverConfig, create_retriever  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic req2web.page_spec.v1 JSON."
    )
    parser.add_argument("requirement", help="One vague software or webpage requirement")
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
    retriever = create_retriever(
        RetrieverConfig(
            index_dir=args.index_dir.resolve(),
            backend=args.retriever_backend,
        )
    )
    chain = MinimalAgentChain(
        DeterministicRequirementProvider(),
        retriever,
        top_k_per_role=args.top_k,
    )
    context = chain.run(
        args.requirement,
        target_device=args.target_device,
        task_type=args.task_type,
        constraints=args.constraint,
    )
    page_spec = PageSpecBuilder().build(context)
    print(json.dumps(page_spec.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
