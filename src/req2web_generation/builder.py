from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle
from req2web_rag.corpus import ROLE_ORDER

from .schema import (
    AcceptanceCheck,
    ComponentSpec,
    ConstraintSpec,
    EvidenceReference,
    InteractionSpec,
    LayoutSpec,
    PageSpec,
    PageState,
    PageUseCase,
    SectionSpec,
    TraceabilitySpec,
    UseCaseTrace,
)


_TITLE_PREFIX = re.compile(r"^(?:我想|请|帮我)?(?:做|创建|构建|设计)(?:一个|一款)?")
_EMPTY_STATE_KEYWORDS = (
    "搜索",
    "筛选",
    "列表",
    "目录",
    "浏览",
    "结果",
    "search",
    "filter",
    "list",
    "result",
)
_COMPONENT_TYPE_RULES = (
    ("media_input", ("拍摄", "上传", "图片", "camera", "upload")),
    ("search_input", ("搜索", "筛选", "查询", "search", "filter")),
    ("form", ("填写", "表单", "登录", "注册", "form", "login")),
    ("data_view", ("数据", "统计", "图表", "dashboard", "chart")),
    ("location_picker", ("地图", "位置", "地址", "map", "location")),
)


def _stable_token(value: str, fallback: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return token or fallback


def _deduplicate(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def _derive_title(requirement: str, task_type: str) -> str:
    first_clause = re.split(r"[。！？!?\n]", requirement.strip(), maxsplit=1)[0]
    title = _TITLE_PREFIX.sub("", first_clause).strip(" ，,：:")
    if not title:
        title = task_type.replace("_", " ").strip()
    return title[:60]


def _component_type(text: str) -> str:
    lowered = text.casefold()
    for component_type, keywords in _COMPONENT_TYPE_RULES:
        if any(keyword in lowered for keyword in keywords):
            return component_type
    return "primary_action"


def _reference_uris(result: dict[str, Any]) -> list[str]:
    references = result.get("references", [])
    if not isinstance(references, list):
        raise ValueError("retrieval result references must be a list")
    uris: list[str] = []
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError("retrieval result reference must be an object")
        uri = reference.get("uri")
        if uri is not None:
            if not isinstance(uri, str) or not uri.strip():
                raise ValueError("retrieval result reference URI must be non-empty")
            uris.append(uri.strip())
    return _deduplicate(uris)[:3]


class PageSpecBuilder:
    """Build ``req2web.page_spec.v1`` from an existing Agent context bundle."""

    def build(self, context: AgentContextBundle) -> PageSpec:
        if not isinstance(context, AgentContextBundle):
            raise TypeError("PageSpecBuilder input must be an AgentContextBundle")
        if context.schema_version != AGENT_BUNDLE_SCHEMA_VERSION:
            raise ValueError(f"unsupported Agent context schema: {context.schema_version}")
        context.validate()
        for field_name in ("requirement_summary", "target_device", "task_type"):
            value = getattr(context, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Agent context {field_name} must not be empty")

        use_case_ids = [item.use_case_id for item in context.use_cases]
        if len(use_case_ids) != len(set(use_case_ids)):
            raise ValueError("Agent context use_case IDs must be unique")

        context_payload = context.to_dict()
        digest = hashlib.sha256(
            json.dumps(
                context_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]
        page_id = f"page-{_stable_token(context.task_type, 'application')}-{digest}"

        page_use_cases = [
            PageUseCase(
                use_case_id=item.use_case_id,
                title=item.title,
                actor=item.actor,
                goal=item.goal,
                expected_outcome=item.expected_outcome,
            )
            for item in context.use_cases
        ]

        sections: list[SectionSpec] = []
        components: list[ComponentSpec] = []
        interactions: list[InteractionSpec] = []
        action_component_ids: list[str] = []
        output_component_ids: list[str] = []
        trace_parts: dict[str, dict[str, list[str]]] = {}
        used_tokens: set[str] = set()
        for index, use_case in enumerate(context.use_cases, 1):
            token = _stable_token(use_case.use_case_id, f"uc-{index:02d}")
            if token in used_tokens:
                raise ValueError("Agent context use_case IDs do not form stable unique IDs")
            used_tokens.add(token)
            section_id = f"section-{token}"
            action_id = f"component-{token}-action"
            output_id = f"component-{token}-output"
            interaction_id = f"interaction-{token}-primary"
            action_component_ids.append(action_id)
            output_component_ids.append(output_id)
            sections.append(
                SectionSpec(
                    section_id=section_id,
                    title=use_case.title,
                    purpose=use_case.goal,
                    component_ids=[action_id, output_id],
                    use_case_ids=[use_case.use_case_id],
                )
            )
            components.extend(
                (
                    ComponentSpec(
                        component_id=action_id,
                        section_id=section_id,
                        component_type=_component_type(
                            f"{use_case.title} {use_case.goal}"
                        ),
                        label=use_case.title,
                        purpose=use_case.goal,
                    ),
                    ComponentSpec(
                        component_id=output_id,
                        section_id=section_id,
                        component_type="status_panel",
                        label=f"{use_case.title}反馈",
                        purpose=use_case.expected_outcome,
                    ),
                )
            )
            interactions.append(
                InteractionSpec(
                    interaction_id=interaction_id,
                    trigger_component_id=action_id,
                    source_state_id="state-initial",
                    action=f"执行：{use_case.goal}",
                    target_state_id="state-success",
                    user_feedback=use_case.expected_outcome,
                    use_case_ids=[use_case.use_case_id],
                )
            )
            trace_parts[use_case.use_case_id] = {
                "section_ids": [section_id],
                "component_ids": [action_id, output_id],
                "interaction_ids": [interaction_id],
                "evidence_doc_ids": [],
            }

        states = [
            PageState(
                state_id="state-initial",
                name="initial",
                description="页面已就绪，核心任务入口可操作。",
                visible_component_ids=[*action_component_ids, *output_component_ids],
            ),
            PageState(
                state_id="state-loading",
                name="loading",
                description="系统正在处理用户触发的核心任务。",
                visible_component_ids=output_component_ids.copy(),
            ),
            PageState(
                state_id="state-error",
                name="error",
                description="任务无法完成时显示原因和可恢复操作。",
                visible_component_ids=output_component_ids.copy(),
            ),
            PageState(
                state_id="state-success",
                name="success",
                description="核心任务完成并显示明确结果与后续入口。",
                visible_component_ids=[*action_component_ids, *output_component_ids],
            ),
        ]
        state_text = " ".join(
            [
                context.original_requirement,
                context.requirement_summary,
                *(item.title for item in context.use_cases),
            ]
        ).casefold()
        if any(keyword in state_text for keyword in _EMPTY_STATE_KEYWORDS):
            states.insert(
                2,
                PageState(
                    state_id="state-empty",
                    name="empty",
                    description="暂无匹配内容时说明当前结果为空并提供下一步操作。",
                    visible_component_ids=[*action_component_ids, *output_component_ids],
                ),
            )

        constraint_descriptions = _deduplicate(
            [
                *context.constraints,
                f"页面必须适配目标设备：{context.target_device}",
                "页面结构必须覆盖全部核心用例并提供明确状态反馈",
            ]
        )
        constraints = [
            ConstraintSpec(
                constraint_id=f"constraint-{index:02d}",
                description=description,
                source=("agent_context" if index <= len(context.constraints) else "builder"),
            )
            for index, description in enumerate(constraint_descriptions, 1)
        ]
        acceptance_checks = [
            AcceptanceCheck(
                check_id=f"check-{index:02d}",
                description=(
                    f"用户完成“{use_case.title}”后，页面应{use_case.expected_outcome}。"
                ),
                use_case_ids=[use_case.use_case_id],
                state_id="state-success",
            )
            for index, use_case in enumerate(context.use_cases, 1)
        ]

        evidence: list[EvidenceReference] = []
        evidence_ids_by_role: dict[str, list[str]] = {}
        seen_doc_ids: set[str] = set()
        for role in ROLE_ORDER:
            results = context.retrieval_results[role]
            if not results:
                raise ValueError(f"PageSpec requires retrieval evidence for role: {role}")
            role_doc_ids: list[str] = []
            for result in results:
                doc_id = result.get("doc_id")
                title = result.get("title")
                if not isinstance(doc_id, str) or not doc_id.strip():
                    raise ValueError(f"retrieval result for {role} has no doc_id")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError(f"retrieval result {doc_id} has no title")
                if doc_id in seen_doc_ids:
                    raise ValueError(f"duplicate retrieval evidence doc_id: {doc_id}")
                seen_doc_ids.add(doc_id)
                role_doc_ids.append(doc_id)
                evidence.append(
                    EvidenceReference(
                        role=role,
                        doc_id=doc_id,
                        title=title.strip(),
                        reference_uris=_reference_uris(result),
                    )
                )
            evidence_ids_by_role[role] = role_doc_ids

        assigned_evidence: dict[str, list[str]] = defaultdict(list)
        for role in ROLE_ORDER:
            role_doc_ids = evidence_ids_by_role[role]
            for use_case_index, use_case_id in enumerate(use_case_ids):
                assigned_evidence[use_case_id].append(
                    role_doc_ids[use_case_index % len(role_doc_ids)]
                )
            for evidence_index, doc_id in enumerate(role_doc_ids):
                assigned_evidence[use_case_ids[evidence_index % len(use_case_ids)]].append(
                    doc_id
                )

        traces: list[UseCaseTrace] = []
        for use_case_id in use_case_ids:
            parts = trace_parts[use_case_id]
            traces.append(
                UseCaseTrace(
                    use_case_id=use_case_id,
                    section_ids=parts["section_ids"],
                    component_ids=parts["component_ids"],
                    interaction_ids=parts["interaction_ids"],
                    evidence_doc_ids=_deduplicate(assigned_evidence[use_case_id]),
                )
            )

        spec = PageSpec(
            page_id=page_id,
            title=_derive_title(context.original_requirement, context.task_type),
            summary=context.requirement_summary,
            target_device=context.target_device,
            page_type=context.task_type,
            layout=LayoutSpec(
                pattern="single_page_task_flow",
                section_order=[item.section_id for item in sections],
            ),
            use_cases=page_use_cases,
            sections=sections,
            components=components,
            states=states,
            interactions=interactions,
            constraints=constraints,
            acceptance_checks=acceptance_checks,
            traceability=TraceabilitySpec(
                source_context_schema_version=context.schema_version,
                evidence=evidence,
                use_cases=traces,
            ),
        )
        spec.validate()
        return spec
