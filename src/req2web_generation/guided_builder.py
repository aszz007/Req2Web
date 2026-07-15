from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Iterable

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle
from req2web_rag.corpus import ROLE_ORDER

from .builder import PageSpecBuilder, _stable_token
from .reference_safety import validate_reference_uri
from .retrieval_guidance import (
    RETRIEVAL_GUIDANCE_SCHEMA_VERSION,
    GuidanceItem,
    RetrievalGuidance,
    RetrievalGuidanceBuilder,
)
from .schema import (
    AcceptanceCheck,
    ComponentSpec,
    ConstraintSpec,
    InteractionSpec,
    PageSpec,
    PageState,
)


GUIDED_PAGE_SPEC_BUILD_RESULT_SCHEMA_VERSION = (
    "req2web.guided.page_spec.build_result.v1"
)

_CONCEPT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "search_input": ("搜索", "查询", "search"),
    "filter_control": ("筛选", "过滤", "filter"),
    "result_list": ("列表", "list"),
    "cart_summary": ("购物车", "cart", "basket"),
    "checkout_action": ("结算", "支付", "checkout", "payment"),
    "media_input": ("拍摄", "拍照", "上传", "照片", "图片", "camera", "upload"),
    "analysis_result": ("识别", "分析", "recognition", "analysis"),
    "location_picker": ("地图", "地点", "位置", "地址", "定位", "map", "location"),
    "metric_summary": ("看板", "指标", "统计", "数据", "dashboard", "metric"),
    "detail_view": ("详情", "detail"),
    "empty_state": ("空状态", "无匹配", "没有匹配", "无数据", "没有数据", "empty", "no data"),
    "permission_recovery": ("权限", "拒绝", "permission"),
    "retry_recovery": ("输入错误", "错误", "失败", "重试", "retry", "error"),
}

_CONTEXT_COMPONENTS: tuple[str, ...] = (
    "search_input",
    "filter_control",
    "cart_summary",
    "checkout_action",
    "media_input",
    "analysis_result",
    "location_picker",
    "metric_summary",
    "detail_view",
)

_COMPONENT_PRESENTATION: dict[str, tuple[str, str, str]] = {
    "search_input": ("search_input", "搜索输入", "输入关键词并发起搜索"),
    "filter_control": ("search_input", "筛选条件", "调整筛选条件并更新结果"),
    "result_list": ("data_view", "结果列表", "展示与当前查询匹配的结果列表"),
    "cart_summary": ("primary_action", "购物车摘要", "查看待购买项目和数量"),
    "checkout_action": ("primary_action", "结算", "确认订单并完成结算"),
    "media_input": ("media_input", "拍照或上传", "提交用户明确要求的本地素材"),
    "analysis_result": ("data_view", "分析结果", "展示用户明确要求的分析结果"),
    "location_picker": ("location_picker", "位置选择", "搜索、手动选择并确认地址"),
    "metric_summary": ("data_view", "关键指标", "展示用户明确要求的指标摘要"),
    "detail_view": ("data_view", "详情视图", "展示所选项目的关键信息"),
    "empty_state": ("status_panel", "空结果提示", "说明当前没有匹配内容并给出恢复入口"),
}

_TAP_WORDS = ("点击", "选择", "确认", "查看", "搜索", "筛选", "上传", "拍摄", "结算", "提交")
_SWIPE_WORDS = ("滑动", "滚动", "浏览", "列表", "搜索", "筛选")
_ACTION_WORDS = tuple(dict.fromkeys((*_TAP_WORDS, *_SWIPE_WORDS, "分析", "识别")))


@dataclass(frozen=True)
class AffectedPageSpecField:
    entity_id: str
    field_name: str


@dataclass(frozen=True)
class GuidanceDecision:
    decision_id: str
    source_kind: str
    role: str
    guidance_id: str | None
    doc_id: str | None
    rule: str
    reason: str
    affected_fields: list[AffectedPageSpecField]


@dataclass
class GuidedPageSpecBuildResult:
    build_result_id: str
    guidance_bundle_id: str
    page_spec: PageSpec
    adopted: list[GuidanceDecision]
    ignored: list[GuidanceDecision]
    fallback: list[GuidanceDecision]
    source_context_schema_version: str = AGENT_BUNDLE_SCHEMA_VERSION
    source_guidance_schema_version: str = RETRIEVAL_GUIDANCE_SCHEMA_VERSION
    schema_version: str = GUIDED_PAGE_SPEC_BUILD_RESULT_SCHEMA_VERSION

    def validate(self, guidance: RetrievalGuidance | None = None) -> None:
        if self.schema_version != GUIDED_PAGE_SPEC_BUILD_RESULT_SCHEMA_VERSION:
            raise ValueError(f"unsupported guided build-result schema: {self.schema_version}")
        if self.source_context_schema_version != AGENT_BUNDLE_SCHEMA_VERSION:
            raise ValueError("guided build result must identify req2web.agent.context.v1")
        if self.source_guidance_schema_version != RETRIEVAL_GUIDANCE_SCHEMA_VERSION:
            raise ValueError("guided build result must identify req2web.retrieval.guidance.v1")
        if not self.build_result_id.strip() or not self.guidance_bundle_id.strip():
            raise ValueError("guided build-result IDs must not be empty")
        self.page_spec.validate()

        groups = (("adopted", self.adopted), ("ignored", self.ignored), ("fallback", self.fallback))
        decisions = [item for _, group in groups for item in group]
        decision_ids = [item.decision_id for item in decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("guided decision IDs must be unique")

        entity_ids = _page_spec_entity_ids(self.page_spec)
        for disposition, group in groups:
            for decision in group:
                for value, name in (
                    (decision.decision_id, "decision_id"),
                    (decision.source_kind, "source_kind"),
                    (decision.role, "role"),
                    (decision.rule, "rule"),
                    (decision.reason, "reason"),
                ):
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(f"guided decision {name} must not be empty")
                if decision.source_kind == "retrieval_guidance":
                    if not decision.guidance_id or not decision.doc_id:
                        raise ValueError("retrieval decisions require guidance_id and doc_id")
                    if disposition == "adopted" and not decision.affected_fields:
                        raise ValueError("adopted guidance must identify affected PageSpec fields")
                    if disposition != "adopted" and decision.affected_fields:
                        raise ValueError("ignored/fallback guidance must not claim PageSpec effects")
                elif decision.source_kind == "agent_context":
                    if disposition != "fallback":
                        raise ValueError("Agent context decisions must be recorded as fallback")
                    if decision.guidance_id is not None or decision.doc_id is not None:
                        raise ValueError("Agent context decisions must not claim RAG source IDs")
                    if not decision.affected_fields:
                        raise ValueError("Agent context decisions must identify preserved fields")
                else:
                    raise ValueError(f"unsupported guided decision source: {decision.source_kind}")
                for affected in decision.affected_fields:
                    if affected.entity_id not in entity_ids:
                        raise ValueError(
                            f"guided decision references unknown PageSpec entity: {affected.entity_id}"
                        )
                    if not affected.field_name.strip():
                        raise ValueError("affected PageSpec field name must not be empty")

        if guidance is not None:
            guidance.validate()
            if self.guidance_bundle_id != guidance.guidance_bundle_id:
                raise ValueError("guided build result references the wrong guidance bundle")
            guidance_by_id = {item.guidance_id: item for item in _all_guidance_items(guidance)}
            retrieval_decisions = [
                item for item in decisions if item.source_kind == "retrieval_guidance"
            ]
            decided_ids = [str(item.guidance_id) for item in retrieval_decisions]
            if len(decided_ids) != len(set(decided_ids)):
                raise ValueError("every guidance item must have exactly one decision")
            if set(decided_ids) != set(guidance_by_id):
                raise ValueError("guided decisions must cover every guidance item")
            for decision in retrieval_decisions:
                item = guidance_by_id[str(decision.guidance_id)]
                if decision.doc_id != item.source.doc_id or decision.role != item.source.role:
                    raise ValueError("guided decision source does not match its guidance item")

        expected_id = _build_result_id(
            self.guidance_bundle_id,
            self.page_spec,
            self.adopted,
            self.ignored,
            self.fallback,
        )
        if self.build_result_id != expected_id:
            raise ValueError("guided build_result_id does not match its deterministic payload")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def _all_guidance_items(guidance: RetrievalGuidance) -> list[GuidanceItem]:
    return [
        *guidance.requirement_guidance,
        *guidance.ui_guidance,
        *guidance.interaction_guidance,
        *guidance.implementation_guidance,
        *guidance.validation_guidance,
    ]


def _page_spec_entity_ids(page_spec: PageSpec) -> set[str]:
    return {
        page_spec.page_id,
        *(item.section_id for item in page_spec.sections),
        *(item.component_id for item in page_spec.components),
        *(item.state_id for item in page_spec.states),
        *(item.interaction_id for item in page_spec.interactions),
        *(item.constraint_id for item in page_spec.constraints),
        *(item.check_id for item in page_spec.acceptance_checks),
    }


def _build_result_id(
    guidance_bundle_id: str,
    page_spec: PageSpec,
    adopted: list[GuidanceDecision],
    ignored: list[GuidanceDecision],
    fallback: list[GuidanceDecision],
) -> str:
    payload = {
        "guidance_bundle_id": guidance_bundle_id,
        "page_spec": page_spec.to_dict(),
        "adopted": [asdict(item) for item in adopted],
        "ignored": [asdict(item) for item in ignored],
        "fallback": [asdict(item) for item in fallback],
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return f"guided-page-spec-{digest}"


def _decision(
    disposition: str,
    *,
    source_kind: str,
    role: str,
    rule: str,
    reason: str,
    affected_fields: Iterable[AffectedPageSpecField] = (),
    item: GuidanceItem | None = None,
) -> GuidanceDecision:
    guidance_id = item.guidance_id if item else None
    doc_id = item.source.doc_id if item else None
    seed = "|".join(
        (
            disposition,
            source_kind,
            role,
            guidance_id or "context",
            doc_id or "context",
            rule,
        )
    )
    token = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return GuidanceDecision(
        decision_id=f"decision-{token}",
        source_kind=source_kind,
        role=role,
        guidance_id=guidance_id,
        doc_id=doc_id,
        rule=rule,
        reason=reason,
        affected_fields=list(affected_fields),
    )


def _contains_any(text: str, values: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(value.casefold() in lowered for value in values)


def _explicit_context_text(context: AgentContextBundle) -> str:
    return " ".join((context.original_requirement, *context.constraints)).casefold()


def _context_has(context: AgentContextBundle, concept: str) -> bool:
    return _contains_any(_explicit_context_text(context), _CONCEPT_KEYWORDS.get(concept, ()))


def _use_case_text(use_case: object) -> str:
    return " ".join(
        str(getattr(use_case, name))
        for name in ("title", "goal", "expected_outcome")
    ).casefold()


def _best_use_case(context: AgentContextBundle, concept: str) -> str:
    keywords = _CONCEPT_KEYWORDS.get(concept, ())
    scored = []
    for index, use_case in enumerate(context.use_cases):
        text = _use_case_text(use_case)
        score = sum(text.count(keyword.casefold()) for keyword in keywords)
        if concept == "metric_summary" and _contains_any(text, ("核心", "状态", "浏览")):
            score += 1
        scored.append((score, -index, use_case.use_case_id))
    return max(scored)[2]


def _trace_for_use_case(page_spec: PageSpec, use_case_id: str):
    return next(item for item in page_spec.traceability.use_cases if item.use_case_id == use_case_id)


def _section_for_use_case(page_spec: PageSpec, use_case_id: str):
    return next(item for item in page_spec.sections if use_case_id in item.use_case_ids)


def _add_component(
    page_spec: PageSpec,
    context: AgentContextBundle,
    concept: str,
    namespace: str,
) -> ComponentSpec:
    use_case_id = _best_use_case(context, concept)
    section = _section_for_use_case(page_spec, use_case_id)
    component_type, label, purpose = _COMPONENT_PRESENTATION[concept]
    token = _stable_token(use_case_id, "use-case")
    component_id = f"component-{token}-{namespace}-{concept.replace('_', '-')}"
    component = ComponentSpec(component_id, section.section_id, component_type, label, purpose)
    page_spec.components.append(component)
    section.component_ids.insert(max(len(section.component_ids) - 1, 0), component_id)
    _trace_for_use_case(page_spec, use_case_id).component_ids.append(component_id)
    for state in page_spec.states:
        if (
            concept != "empty_state"
            and state.name in {"initial", "success"}
            and component_id not in state.visible_component_ids
        ):
            state.visible_component_ids.append(component_id)
    return component


def _ensure_component(
    page_spec: PageSpec,
    context: AgentContextBundle,
    concept: str,
    namespace: str,
) -> ComponentSpec:
    use_case_id = _best_use_case(context, concept)
    section = _section_for_use_case(page_spec, use_case_id)
    component_type, label, _ = _COMPONENT_PRESENTATION[concept]
    candidates = [
        item
        for item in page_spec.components
        if item.section_id == section.section_id and item.component_type == component_type
    ]
    if concept in {"filter_control", "result_list", "cart_summary", "checkout_action", "empty_state"}:
        candidates = [item for item in candidates if item.label == label]
    if candidates:
        return candidates[0]
    return _add_component(page_spec, context, concept, namespace)


def _ensure_empty_state(page_spec: PageSpec) -> PageState:
    for state in page_spec.states:
        if state.name == "empty":
            return state
    state = PageState(
        state_id="state-empty",
        name="empty",
        description="暂无匹配内容时说明当前结果为空并提供下一步操作。",
        visible_component_ids=[],
    )
    success_index = next(
        (index for index, item in enumerate(page_spec.states) if item.name == "success"),
        len(page_spec.states),
    )
    page_spec.states.insert(success_index, state)
    return state


def _append_constraint(page_spec: PageSpec, token: str, description: str) -> ConstraintSpec:
    constraint_id = f"constraint-guided-{_stable_token(token, 'reference')}"
    existing = next(
        (item for item in page_spec.constraints if item.constraint_id == constraint_id),
        None,
    )
    if existing is not None:
        return existing
    constraint = ConstraintSpec(constraint_id, description, "builder")
    page_spec.constraints.append(constraint)
    return constraint


def _append_acceptance(
    page_spec: PageSpec,
    token: str,
    description: str,
    use_case_id: str,
    state_id: str,
) -> AcceptanceCheck:
    check_id = f"check-guided-{_stable_token(token, 'guidance')}"
    existing = next((item for item in page_spec.acceptance_checks if item.check_id == check_id), None)
    if existing is not None:
        return existing
    check = AcceptanceCheck(check_id, description, [use_case_id], state_id)
    page_spec.acceptance_checks.append(check)
    return check


def _validate_context_references(context: AgentContextBundle) -> None:
    for role in ROLE_ORDER:
        for result in context.retrieval_results[role]:
            references = result.get("references", [])
            if not isinstance(references, list):
                raise ValueError("retrieval result references must be a list")
            for reference in references:
                if not isinstance(reference, dict):
                    raise ValueError("retrieval result reference must be an object")
                uri = reference.get("uri")
                if uri is not None:
                    validate_reference_uri(uri)


class RetrievalGuidedPageSpecBuilder:
    """Conservatively apply RetrievalGuidance to a compatible PageSpec v1."""

    def build(
        self,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        *,
        disabled_roles: Iterable[str] = (),
    ) -> GuidedPageSpecBuildResult:
        if not isinstance(context, AgentContextBundle):
            raise TypeError("guided PageSpec context must be an AgentContextBundle")
        if not isinstance(guidance, RetrievalGuidance):
            raise TypeError("guided PageSpec guidance must be RetrievalGuidance")
        if context.schema_version != AGENT_BUNDLE_SCHEMA_VERSION:
            raise ValueError(f"unsupported Agent context schema: {context.schema_version}")
        if guidance.schema_version != RETRIEVAL_GUIDANCE_SCHEMA_VERSION:
            raise ValueError(f"unsupported RetrievalGuidance schema: {guidance.schema_version}")
        context.validate()
        guidance.validate()
        _validate_context_references(context)
        if guidance.source_context_schema_version != context.schema_version:
            raise ValueError("guidance source context schema does not match context")
        if guidance.target_device != context.target_device:
            raise ValueError("guidance target_device does not match context")
        if guidance.task_type != context.task_type:
            raise ValueError("guidance task_type does not match context")

        context_docs = {
            item["doc_id"]: role
            for role in ROLE_ORDER
            for item in context.retrieval_results[role]
        }
        for item in _all_guidance_items(guidance):
            if item.source.doc_id not in context_docs:
                raise ValueError(f"guidance doc_id is absent from context: {item.source.doc_id}")
            if context_docs[item.source.doc_id] != item.source.role:
                raise ValueError("guidance doc_id has the wrong context role")

        rebuilt = RetrievalGuidanceBuilder().build(context)
        if rebuilt.to_dict() != guidance.to_dict():
            raise ValueError("guidance is not the deterministic guidance derived from context")

        disabled = tuple(dict.fromkeys(disabled_roles))
        invalid_roles = [role for role in disabled if role not in ROLE_ORDER]
        if invalid_roles:
            raise ValueError(f"unsupported disabled guidance roles: {invalid_roles}")

        page_spec = PageSpecBuilder().build(context)
        adopted: list[GuidanceDecision] = []
        ignored: list[GuidanceDecision] = []
        fallback: list[GuidanceDecision] = []

        # The ablation path is deliberately implemented here, after the source
        # guidance has passed its normal identity checks.  It does not alter the
        # guidance object or re-run retrieval; it merely prevents one role from
        # reaching the existing deterministic transformation rules.
        if disabled:
            for item in _all_guidance_items(guidance):
                if item.source.role in disabled:
                    ignored.append(
                        _decision(
                            "ignored",
                            source_kind="retrieval_guidance",
                            role=item.source.role,
                            item=item,
                            rule=f"ablation_disabled_role:v1:{item.source.role}",
                            reason="受控消融移除了该角色的检索指导；不声明任何 PageSpec 结构影响。",
                        )
                    )
            effective_guidance = RetrievalGuidance(
                guidance_bundle_id=guidance.guidance_bundle_id,
                target_device=guidance.target_device,
                task_type=guidance.task_type,
                requirement_guidance=[] if "requirement" in disabled else guidance.requirement_guidance,
                ui_guidance=[] if "ui_reference" in disabled else guidance.ui_guidance,
                interaction_guidance=[] if "interaction_flow" in disabled else guidance.interaction_guidance,
                implementation_guidance=[] if "implementation" in disabled else guidance.implementation_guidance,
                validation_guidance=[] if "validation" in disabled else guidance.validation_guidance,
                use_case_traces=guidance.use_case_traces,
            )
        else:
            effective_guidance = guidance

        self._apply_context_components(context, page_spec, fallback)
        self._apply_context_recovery(context, page_spec, fallback)
        self._apply_requirement(context, effective_guidance, page_spec, adopted, ignored, fallback)
        self._apply_ui(context, effective_guidance, page_spec, adopted, ignored, fallback)
        self._apply_interaction(context, effective_guidance, page_spec, adopted, ignored, fallback)
        self._apply_implementation(context, effective_guidance, page_spec, adopted, ignored, fallback)
        self._apply_validation(context, effective_guidance, page_spec, adopted, ignored, fallback)

        page_spec.validate()
        result = GuidedPageSpecBuildResult(
            build_result_id=_build_result_id(
                guidance.guidance_bundle_id, page_spec, adopted, ignored, fallback
            ),
            guidance_bundle_id=guidance.guidance_bundle_id,
            page_spec=page_spec,
            adopted=adopted,
            ignored=ignored,
            fallback=fallback,
        )
        result.validate(guidance)
        return result

    @staticmethod
    def _apply_context_components(
        context: AgentContextBundle,
        page_spec: PageSpec,
        fallback: list[GuidanceDecision],
    ) -> None:
        for concept in _CONTEXT_COMPONENTS:
            if not _context_has(context, concept):
                continue
            component = _ensure_component(page_spec, context, concept, "context")
            fallback.append(
                _decision(
                    "fallback",
                    source_kind="agent_context",
                    role="context",
                    rule=f"context_explicit_component:v1:{concept}",
                    reason=(
                        "组件由原始需求或显式 constraint 保留；检索未被声明为该业务能力的来源。"
                    ),
                    affected_fields=(
                        AffectedPageSpecField(component.component_id, "component_type"),
                        AffectedPageSpecField(component.component_id, "label"),
                    ),
                )
            )
        if _context_has(context, "metric_summary"):
            page_spec.layout.pattern = "guided_dashboard_flow"
            fallback.append(
                _decision(
                    "fallback",
                    source_kind="agent_context",
                    role="context",
                    rule="context_explicit_layout:v1:metric_dashboard",
                    reason="桌面指标看板布局由原始需求保证，不等待弱 UI 召回证明。",
                    affected_fields=(
                        AffectedPageSpecField(page_spec.page_id, "layout.pattern"),
                    ),
                )
            )

    @staticmethod
    def _apply_context_recovery(
        context: AgentContextBundle,
        page_spec: PageSpec,
        fallback: list[GuidanceDecision],
    ) -> None:
        constraints_text = " ".join(context.constraints).casefold()
        cases: list[tuple[str, str, str, str, str, str, str]] = []
        if (
            _contains_any(constraints_text, ("定位不可用", "位置不可用", "location unavailable"))
            and _contains_any(constraints_text, ("手动", "地址", "manual"))
        ):
            cases.append(
                (
                    "location-manual",
                    "location_picker",
                    "state-error",
                    "模拟定位不可用",
                    "手动选择地址",
                    "定位不可用，请改用手动地址选择。",
                    "已返回可操作状态，可手动选择并确认地址。",
                )
            )
        if _context_has(context, "empty_state") and _contains_any(
            constraints_text, ("清除筛选", "重置筛选", "clear filter")
        ):
            cases.append(
                (
                    "empty-clear-filter",
                    "filter_control",
                    "state-empty",
                    "查看空结果示例",
                    "清除筛选",
                    "没有匹配数据，可清除筛选后重试。",
                    "筛选已清除，可以重新查看数据。",
                )
            )

        states_by_id = {item.state_id: item for item in page_spec.states}
        initial = states_by_id["state-initial"]
        for (
            case_token,
            concept,
            target_state_id,
            trigger_label,
            recovery_label,
            target_feedback,
            recovery_feedback,
        ) in cases:
            if target_state_id == "state-empty":
                target_state = _ensure_empty_state(page_spec)
            else:
                target_state = states_by_id[target_state_id]
            use_case_id = _best_use_case(context, concept)
            section = _section_for_use_case(page_spec, use_case_id)
            token = _stable_token(use_case_id, "use-case")
            trigger_id = f"component-{token}-context-{case_token}-trigger"
            recovery_id = f"component-{token}-context-{case_token}-recovery"
            trigger = ComponentSpec(
                trigger_id,
                section.section_id,
                "primary_action",
                trigger_label,
                "离线验证用户明确声明的恢复边界。",
            )
            recovery = ComponentSpec(
                recovery_id,
                section.section_id,
                "primary_action",
                recovery_label,
                "执行用户明确声明的保守恢复操作。",
            )
            page_spec.components.extend((trigger, recovery))
            section.component_ids.insert(max(len(section.component_ids) - 1, 0), trigger_id)
            section.component_ids.append(recovery_id)
            trace = _trace_for_use_case(page_spec, use_case_id)
            trace.component_ids.extend((trigger_id, recovery_id))
            initial.visible_component_ids.append(trigger_id)
            target_state.visible_component_ids.append(recovery_id)

            enter_id = f"interaction-{token}-context-{case_token}-enter"
            recover_id = f"interaction-{token}-context-{case_token}-recover"
            enter = InteractionSpec(
                enter_id,
                trigger_id,
                "state-initial",
                trigger_label,
                target_state.state_id,
                target_feedback,
                [use_case_id],
            )
            recover = InteractionSpec(
                recover_id,
                recovery_id,
                target_state.state_id,
                recovery_label,
                "state-initial",
                recovery_feedback,
                [use_case_id],
            )
            page_spec.interactions.extend((enter, recover))
            trace.interaction_ids.extend((enter_id, recover_id))
            enter_check = _append_acceptance(
                page_spec,
                f"context-{case_token}-enter",
                f"显式 constraint 必须可进入 {target_state.name} 并显示原因和恢复入口。",
                use_case_id,
                target_state.state_id,
            )
            recover_check = _append_acceptance(
                page_spec,
                f"context-{case_token}-recover",
                "显式 constraint 的恢复操作必须返回 initial，并保留原有正常流程。",
                use_case_id,
                "state-initial",
            )
            fallback.append(
                _decision(
                    "fallback",
                    source_kind="agent_context",
                    role="context",
                    rule=f"context_explicit_recovery:v1:{case_token}",
                    reason="恢复行为直接来自显式 constraint；检索未被声明为该业务行为的来源。",
                    affected_fields=(
                        AffectedPageSpecField(trigger_id, "label"),
                        AffectedPageSpecField(recovery_id, "label"),
                        AffectedPageSpecField(enter_id, "target_state_id"),
                        AffectedPageSpecField(recover_id, "target_state_id"),
                        AffectedPageSpecField(enter_check.check_id, "description"),
                        AffectedPageSpecField(recover_check.check_id, "description"),
                    ),
                )
            )

    @staticmethod
    def _apply_requirement(
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        page_spec: PageSpec,
        adopted: list[GuidanceDecision],
        ignored: list[GuidanceDecision],
        fallback: list[GuidanceDecision],
    ) -> None:
        adopted_boundary = False
        adopted_values: set[str] = set()
        for item in guidance.requirement_guidance:
            if item.category == "business_boundary_hint" and not adopted_boundary:
                constraint = _append_constraint(
                    page_spec,
                    "requirement-evidence-boundary",
                    "检索需求案例只补充页面结构的证据边界，不覆盖原始需求、设备、用例或显式约束。",
                )
                adopted.append(
                    _decision(
                        "adopted",
                        source_kind="retrieval_guidance",
                        role="requirement",
                        item=item,
                        rule="requirement_boundary:v1:reference_only",
                        reason="来源是允许的需求参考类型，仅用于补充证据边界。",
                        affected_fields=(AffectedPageSpecField(constraint.constraint_id, "description"),),
                    )
                )
                adopted_boundary = True
            elif item.category == "reusable_constraint" and _context_has(context, item.value) and item.value not in adopted_values:
                constraint = _append_constraint(
                    page_spec,
                    f"requirement-{item.value}",
                    f"检索需求案例仅补充 {item.value} 的结构证据；该能力仍以当前 context 明确内容为准。",
                )
                adopted.append(
                    _decision(
                        "adopted",
                        source_kind="retrieval_guidance",
                        role="requirement",
                        item=item,
                        rule=f"requirement_boundary:v1:context_confirmed:{item.value}",
                        reason="受控值已由当前 context 明确支持，检索仅补充边界。",
                        affected_fields=(AffectedPageSpecField(constraint.constraint_id, "description"),),
                    )
                )
                adopted_values.add(item.value)
            elif item.category == "reusable_constraint":
                ignored.append(
                    _decision(
                        "ignored",
                        source_kind="retrieval_guidance",
                        role="requirement",
                        item=item,
                        rule="requirement_boundary:v1:context_required",
                        reason="当前 context 未明确支持该受控能力，不能由相似需求案例补写。",
                    )
                )
            else:
                fallback.append(
                    _decision(
                        "fallback",
                        source_kind="retrieval_guidance",
                        role="requirement",
                        item=item,
                        rule="requirement_boundary:v1:weak_or_duplicate",
                        reason="相似任务类型或重复参考不足以改变 PageSpec，保留 context/旧规则。",
                    )
                )

    @staticmethod
    def _layout_pattern(context: AgentContextBundle) -> str:
        if _context_has(context, "metric_summary"):
            return "guided_dashboard_flow"
        if _context_has(context, "location_picker"):
            return "guided_location_search_flow"
        if _context_has(context, "cart_summary"):
            return "guided_commerce_search_flow"
        return "guided_search_results_flow"

    def _apply_ui(
        self,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        page_spec: PageSpec,
        adopted: list[GuidanceDecision],
        ignored: list[GuidanceDecision],
        fallback: list[GuidanceDecision],
    ) -> None:
        adopted_values: set[str] = set()
        supported = set(_COMPONENT_PRESENTATION)
        for item in guidance.ui_guidance:
            if item.category == "ui_reference_uri":
                fallback.append(
                    _decision(
                        "fallback",
                        source_kind="retrieval_guidance",
                        role="ui_reference",
                        item=item,
                        rule="ui_guidance:v1:reference_only",
                        reason="UI URI 只保留为证据，不直接复制资产或改变业务。",
                    )
                )
                continue
            if item.value not in supported or item.value == "reference_screen":
                fallback.append(
                    _decision(
                        "fallback",
                        source_kind="retrieval_guidance",
                        role="ui_reference",
                        item=item,
                        rule="ui_guidance:v1:controlled_value_required",
                        reason="通用或不受支持的 UI 提示不足以改变 PageSpec。",
                    )
                )
                continue
            if not _context_has(context, item.value):
                ignored.append(
                    _decision(
                        "ignored",
                        source_kind="retrieval_guidance",
                        role="ui_reference",
                        item=item,
                        rule=f"ui_guidance:v1:semantic_gate:{item.value}",
                        reason="该 UI 受控值与原始需求、用例和显式 constraint 不相关。",
                    )
                )
                continue
            if item.value in adopted_values:
                fallback.append(
                    _decision(
                        "fallback",
                        source_kind="retrieval_guidance",
                        role="ui_reference",
                        item=item,
                        rule=f"ui_guidance:v1:duplicate:{item.value}",
                        reason="同一受控 UI 值已有更早、稳定的来源被采用。",
                    )
                )
                continue

            component = _ensure_component(page_spec, context, item.value, "guided-ui")
            purpose_note = f"检索 UI 参考用于 {item.value} 的结构表达；"
            if not component.purpose.startswith(purpose_note):
                component.purpose = purpose_note + component.purpose
            affected = [AffectedPageSpecField(component.component_id, "purpose")]
            if item.value in {"search_input", "location_picker", "metric_summary"}:
                page_spec.layout.pattern = self._layout_pattern(context)
                affected.append(AffectedPageSpecField(page_spec.page_id, "layout.pattern"))
            if item.value == "empty_state":
                empty_state = _ensure_empty_state(page_spec)
                empty_state.description = "检索 UI 参考支持清楚的空结果表达；恢复行为仍由当前需求或 constraint 决定。"
                if component.component_id not in empty_state.visible_component_ids:
                    empty_state.visible_component_ids.append(component.component_id)
                affected.extend(
                    (
                        AffectedPageSpecField(empty_state.state_id, "description"),
                        AffectedPageSpecField(empty_state.state_id, "visible_component_ids"),
                    )
                )
            adopted.append(
                _decision(
                    "adopted",
                    source_kind="retrieval_guidance",
                    role="ui_reference",
                    item=item,
                    rule=f"ui_guidance:v1:context_confirmed:{item.value}",
                    reason="受控 UI 值与当前 context 明确语义一致，并改变组件或布局表达。",
                    affected_fields=affected,
                )
            )
            adopted_values.add(item.value)

    @staticmethod
    def _flow_use_case(context: AgentContextBundle, value: str) -> str | None:
        candidates: list[tuple[int, int, str]] = []
        for index, use_case in enumerate(context.use_cases):
            text = _use_case_text(use_case)
            tap_score = sum(1 for word in _TAP_WORDS if word in text)
            swipe_score = sum(1 for word in _SWIPE_WORDS if word in text)
            action_score = sum(1 for word in _ACTION_WORDS if word in text)
            if value == "tap":
                score = tap_score
            elif value == "swipe":
                score = swipe_score
            elif value == "tap_and_swipe":
                score = tap_score + swipe_score if tap_score and swipe_score else 0
            elif value == "multi_step_transition":
                score = action_score if action_score >= 2 or re.search(r"[、与和]", text) else 0
            elif value == "single_step_transition":
                score = action_score if action_score == 1 and not re.search(r"[、与和]", text) else 0
            else:
                score = 0
            if score:
                candidates.append((score, -index, use_case.use_case_id))
        return max(candidates)[2] if candidates else None

    @staticmethod
    def _primary_interaction(page_spec: PageSpec, use_case_id: str) -> InteractionSpec:
        return next(
            item
            for item in page_spec.interactions
            if use_case_id in item.use_case_ids
            and item.source_state_id == "state-initial"
            and item.target_state_id == "state-success"
        )

    def _apply_interaction(
        self,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        page_spec: PageSpec,
        adopted: list[GuidanceDecision],
        ignored: list[GuidanceDecision],
        fallback: list[GuidanceDecision],
    ) -> None:
        used: set[tuple[str, str]] = set()
        labels = {
            "tap": "点击操作",
            "swipe": "滑动浏览",
            "tap_and_swipe": "点击并滑动",
            "single_step_transition": "单步状态转换",
            "multi_step_transition": "多步骤状态转换",
        }
        for item in guidance.interaction_guidance:
            use_case_id = self._flow_use_case(context, item.value)
            if use_case_id is None:
                ignored.append(
                    _decision(
                        "ignored",
                        source_kind="retrieval_guidance",
                        role="interaction_flow",
                        item=item,
                        rule=f"interaction_guidance:v1:semantic_use_case_match:{item.value}",
                        reason="轮转 use-case trace 单独不足；用例标题、目标和预期结果未支持该流程模式。",
                    )
                )
                continue
            key = (item.category, use_case_id)
            if key in used:
                fallback.append(
                    _decision(
                        "fallback",
                        source_kind="retrieval_guidance",
                        role="interaction_flow",
                        item=item,
                        rule=f"interaction_guidance:v1:duplicate_for_use_case:{item.value}",
                        reason="该用例已有同类、更早的流程指导，避免冲突叠加。",
                    )
                )
                continue
            interaction = self._primary_interaction(page_spec, use_case_id)
            phrase = f"；流程表达：{labels[item.value]}"
            if phrase not in interaction.action:
                interaction.action += phrase
            adopted.append(
                _decision(
                    "adopted",
                    source_kind="retrieval_guidance",
                    role="interaction_flow",
                    item=item,
                    rule=f"interaction_guidance:v1:semantic_use_case_match:{item.value}",
                    reason="流程受控值与用例语义确定性匹配，仅调整交互表达，不制造新业务。",
                    affected_fields=(AffectedPageSpecField(interaction.interaction_id, "action"),),
                )
            )
            used.add(key)

    @staticmethod
    def _apply_implementation(
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        page_spec: PageSpec,
        adopted: list[GuidanceDecision],
        ignored: list[GuidanceDecision],
        fallback: list[GuidanceDecision],
    ) -> None:
        adopted_keys: set[tuple[str, str]] = set()
        for item in guidance.implementation_guidance:
            relevant = (
                item.category == "resource_constraint"
                or item.value == "reference_structure"
                or _context_has(context, item.value)
            )
            key = (item.category, item.value)
            if not relevant:
                ignored.append(
                    _decision(
                        "ignored",
                        source_kind="retrieval_guidance",
                        role="implementation",
                        item=item,
                        rule=f"implementation_guidance:v1:context_or_structure_gate:{item.value}",
                        reason="实现受控值与当前 context 无关，不能改变 builder constraint。",
                    )
                )
                continue
            if key in adopted_keys:
                fallback.append(
                    _decision(
                        "fallback",
                        source_kind="retrieval_guidance",
                        role="implementation",
                        item=item,
                        rule=f"implementation_guidance:v1:duplicate:{item.value}",
                        reason="相同实现结构或资源约束已采用，避免重复 constraint。",
                    )
                )
                continue
            description = (
                "检索实现参考仅用于组织稳定的单页结构，不复制其业务内容。"
                if item.value == "reference_structure"
                else f"检索实现参考允许按 {item.value} 组织结构或资源；引用必须保持项目相对路径或 HTTPS。"
            )
            constraint = _append_constraint(
                page_spec,
                f"implementation-{item.category}-{item.value}",
                description,
            )
            adopted.append(
                _decision(
                    "adopted",
                    source_kind="retrieval_guidance",
                    role="implementation",
                    item=item,
                    rule=f"implementation_guidance:v1:sourced_constraint:{item.value}",
                    reason="实现指导只形成有来源的 builder constraint，不扩展业务。",
                    affected_fields=(AffectedPageSpecField(constraint.constraint_id, "description"),),
                )
            )
            adopted_keys.add(key)

    @staticmethod
    def _recovery_use_case(page_spec: PageSpec, kind: str) -> str | None:
        if kind in {"retry_recovery", "permission_recovery"}:
            interaction = next(
                (
                    item
                    for item in page_spec.interactions
                    if item.source_state_id == "state-error" and item.target_state_id == "state-initial"
                ),
                None,
            )
            return interaction.use_case_ids[0] if interaction else None
        return None

    def _apply_validation(
        self,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        page_spec: PageSpec,
        adopted: list[GuidanceDecision],
        ignored: list[GuidanceDecision],
        fallback: list[GuidanceDecision],
    ) -> None:
        adopted_values: set[str] = set()
        constraints_text = " ".join(context.constraints).casefold()
        recovery_declared = _contains_any(constraints_text, ("恢复", "重试", "修改", "手动", "recover", "retry"))
        gates = {
            "empty_state": _context_has(context, "empty_state"),
            "retry_recovery": recovery_declared and _contains_any(constraints_text, ("错误", "失败", "输入", "retry", "error")),
            "permission_recovery": recovery_declared and _contains_any(constraints_text, ("权限", "拒绝", "permission")),
        }
        for item in guidance.validation_guidance:
            if item.value == "regression_case" or item.category == "validation_reference_uri":
                fallback.append(
                    _decision(
                        "fallback",
                        source_kind="retrieval_guidance",
                        role="validation",
                        item=item,
                        rule="validation_guidance:v1:evidence_only",
                        reason="通用回归案例或外部 URI 只作为证据，不能单独新增验收行为。",
                    )
                )
                continue
            if item.value not in gates:
                ignored.append(
                    _decision(
                        "ignored",
                        source_kind="retrieval_guidance",
                        role="validation",
                        item=item,
                        rule=f"validation_guidance:v1:supported_gate:{item.value}",
                        reason="只有 empty_state、retry_recovery、permission_recovery 可在本版本受门控采用。",
                    )
                )
                continue
            if not gates[item.value]:
                ignored.append(
                    _decision(
                        "ignored",
                        source_kind="retrieval_guidance",
                        role="validation",
                        item=item,
                        rule=f"validation_guidance:v1:explicit_context_gate:{item.value}",
                        reason="当前原始需求或显式 constraint 未建立该异常/恢复边界。",
                    )
                )
                continue
            if item.value in adopted_values:
                fallback.append(
                    _decision(
                        "fallback",
                        source_kind="retrieval_guidance",
                        role="validation",
                        item=item,
                        rule=f"validation_guidance:v1:duplicate:{item.value}",
                        reason="同类 validation 已有更早来源被采用。",
                    )
                )
                continue

            affected: list[AffectedPageSpecField] = []
            if item.value == "empty_state":
                use_case_id = _best_use_case(context, "filter_control")
                empty_state = _ensure_empty_state(page_spec)
                interaction = next(
                    (
                        value
                        for value in page_spec.interactions
                        if use_case_id in value.use_case_ids
                        and value.source_state_id == "state-initial"
                        and value.target_state_id == empty_state.state_id
                    ),
                    None,
                )
                if interaction is None:
                    section = _section_for_use_case(page_spec, use_case_id)
                    token = _stable_token(use_case_id, "use-case")
                    action = ComponentSpec(
                        f"component-{token}-guided-validation-empty-trigger",
                        section.section_id,
                        "primary_action",
                        "查看空结果示例",
                        "离线验证需求明确的空结果边界。",
                    )
                    page_spec.components.append(action)
                    section.component_ids.insert(max(len(section.component_ids) - 1, 0), action.component_id)
                    trace = _trace_for_use_case(page_spec, use_case_id)
                    trace.component_ids.append(action.component_id)
                    next(
                        state for state in page_spec.states if state.name == "initial"
                    ).visible_component_ids.append(action.component_id)
                    interaction_id = f"interaction-{token}-guided-empty"
                    interaction = InteractionSpec(
                        interaction_id,
                        action.component_id,
                        "state-initial",
                        "展示当前筛选条件下的空结果反馈",
                        empty_state.state_id,
                        "没有匹配数据，可清除筛选后重试。",
                        [use_case_id],
                    )
                    page_spec.interactions.append(interaction)
                    trace.interaction_ids.append(interaction_id)
                else:
                    action = next(
                        value
                        for value in page_spec.components
                        if value.component_id == interaction.trigger_component_id
                    )
                check = _append_acceptance(
                    page_spec,
                    f"empty-{use_case_id}",
                    "在需求明确的无匹配数据场景中，页面应显示空状态并保留恢复提示。",
                    use_case_id,
                    empty_state.state_id,
                )
                affected.extend(
                    (
                        AffectedPageSpecField(action.component_id, "label"),
                        AffectedPageSpecField(interaction.interaction_id, "target_state_id"),
                        AffectedPageSpecField(check.check_id, "description"),
                    )
                )
            else:
                use_case_id = self._recovery_use_case(page_spec, item.value)
                if use_case_id is None:
                    ignored.append(
                        _decision(
                            "ignored",
                            source_kind="retrieval_guidance",
                            role="validation",
                            item=item,
                            rule=f"validation_guidance:v1:existing_recovery_path:{item.value}",
                            reason="context 未生成可追溯恢复路径，validation 不能独立制造恢复行为。",
                        )
                    )
                    continue
                label = "权限拒绝恢复" if item.value == "permission_recovery" else "输入错误重试"
                check = _append_acceptance(
                    page_spec,
                    f"{item.value}-{use_case_id}",
                    f"在用户明确的{label}边界下，恢复后应允许重新执行原有核心流程；检索案例只补充验收边界。",
                    use_case_id,
                    "state-initial",
                )
                affected.append(AffectedPageSpecField(check.check_id, "description"))

            adopted.append(
                _decision(
                    "adopted",
                    source_kind="retrieval_guidance",
                    role="validation",
                    item=item,
                    rule=f"validation_guidance:v1:explicit_context_gate:{item.value}",
                    reason="validation 与原始需求或显式 constraint 一致，只补充状态/恢复验收。",
                    affected_fields=affected,
                )
            )
            adopted_values.add(item.value)
