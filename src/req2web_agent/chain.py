from __future__ import annotations

from typing import Iterable

from req2web_rag.corpus import ROLE_ORDER
from req2web_rag.retriever import Retriever

from .schema import AgentContextBundle, RequirementUnderstanding
from .understanding import RequirementUnderstandingProvider


ROLE_QUERY_FOCUS = {
    "requirement": "相似产品需求 用户场景 核心功能 功能边界 页面约束",
    "ui_reference": "UI 界面参考 视觉布局 控件结构 页面类型 可见文字",
    "interaction_flow": "交互流程 操作步骤 页面状态 点击 滑动 状态变化",
    "implementation": "前端实现 响应式 HTML 页面布局 组件结构 原型实现",
    "validation": "验收标准 测试点 异常流程 输入错误 权限 状态一致性",
}


def build_retrieval_queries(
    understanding: RequirementUnderstanding,
) -> dict[str, str]:
    use_case_text = "；".join(
        f"{item.title}：{item.expected_outcome}" for item in understanding.use_cases
    )
    constraint_text = "；".join(understanding.constraints) or "无额外显式约束"
    shared = (
        f"{understanding.requirement_summary} "
        f"目标设备 {understanding.target_device}；任务类型 {understanding.task_type}；"
        f"核心用例 {use_case_text}；约束 {constraint_text}"
    )
    return {
        role: f"{shared}。检索重点：{ROLE_QUERY_FOCUS[role]}"
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
