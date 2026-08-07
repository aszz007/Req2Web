from __future__ import annotations

from typing import Iterable

from req2web_rag.corpus import ROLE_ORDER
from req2web_rag.retriever import Retriever

from .schema import AgentContextBundle, RequirementUnderstanding
from .understanding import RequirementUnderstandingProvider


ROLE_QUERY_FOCUS = {
    "requirement": "similar product requirements user stories core features scope page constraints",
    "ui_reference": "UI references visual layout control structure page type visible copy",
    "interaction_flow": "interaction flow operation steps page states tap swipe state changes",
    "implementation": "frontend implementation responsive HTML page layout component structure prototype",
    "validation": "acceptance criteria test points error flows invalid input permissions state consistency",
}


def build_retrieval_queries(
    understanding: RequirementUnderstanding,
) -> dict[str, str]:
    use_case_text = "; ".join(
        f"{item.title}: {item.expected_outcome}" for item in understanding.use_cases
    )
    constraint_text = "; ".join(understanding.constraints) or "no additional explicit constraints"
    shared = (
        f"{understanding.requirement_summary} "
        f"target device {understanding.target_device}; task type {understanding.task_type}; "
        f"core use cases {use_case_text}; constraints {constraint_text}"
    )
    return {
        role: f"{shared}. Retrieval focus: {ROLE_QUERY_FOCUS[role]}"
        for role in ROLE_ORDER
    }


class MinimalAgentChain:
    def __init__(
        self,
        requirement_provider: RequirementUnderstandingProvider,
        retriever: Retriever,
        *,
        top_k_per_role: int = 2,
    ) -> None:
        if top_k_per_role < 1:
            raise ValueError("top_k_per_role must be at least 1")
        self.requirement_provider = requirement_provider
        self.retriever = retriever
        self.top_k_per_role = top_k_per_role

    def run(
        self,
        original_requirement: str,
        *,
        target_device: str | None = None,
        task_type: str | None = None,
        constraints: Iterable[str] = (),
    ) -> AgentContextBundle:
        understanding = self.requirement_provider.understand(
            original_requirement,
            target_device=target_device,
            task_type=task_type,
            constraints=constraints,
        )
        queries = build_retrieval_queries(understanding)
        results = {
            role: self.retriever.search(
                queries[role], top_k=self.top_k_per_role, roles=(role,)
            )
            for role in ROLE_ORDER
        }
        bundle = AgentContextBundle(
            original_requirement=original_requirement,
            requirement_summary=understanding.requirement_summary,
            target_device=understanding.target_device,
            task_type=understanding.task_type,
            constraints=understanding.constraints,
            use_cases=understanding.use_cases,
            retrieval_queries=queries,
            retrieval_results=results,
        )
        bundle.validate()
        return bundle
