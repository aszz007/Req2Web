from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from req2web_rag.corpus import ROLE_ORDER


AGENT_BUNDLE_SCHEMA_VERSION = "req2web.agent.context.v1"


@dataclass
class UseCase:
    use_case_id: str
    title: str
    actor: str
    goal: str
    expected_outcome: str


@dataclass
class RequirementUnderstanding:
    requirement_summary: str
    target_device: str
    task_type: str
    constraints: list[str]
    use_cases: list[UseCase]


@dataclass
class AgentContextBundle:
    original_requirement: str
    requirement_summary: str
    target_device: str
    task_type: str
    constraints: list[str]
    use_cases: list[UseCase]
    retrieval_queries: dict[str, str]
    retrieval_results: dict[str, list[dict[str, Any]]]
    schema_version: str = AGENT_BUNDLE_SCHEMA_VERSION

    def validate(self) -> None:
        if not self.original_requirement.strip():
            raise ValueError("original_requirement must not be empty")
        if not 2 <= len(self.use_cases) <= 4:
            raise ValueError("the minimal Agent chain must produce 2-4 use cases")
        if tuple(self.retrieval_queries) != ROLE_ORDER:
            raise ValueError("retrieval_queries must contain all five roles in stable order")
        if tuple(self.retrieval_results) != ROLE_ORDER:
            raise ValueError("retrieval_results must contain all five roles in stable order")
        for role in ROLE_ORDER:
            if not self.retrieval_queries[role].strip():
                raise ValueError(f"retrieval query must not be empty: {role}")
            if any(result.get("role") != role for result in self.retrieval_results[role]):
                raise ValueError(f"retrieval result has the wrong role: {role}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
