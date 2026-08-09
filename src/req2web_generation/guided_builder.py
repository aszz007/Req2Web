from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Iterable

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle
from req2web_rag.corpus import ROLE_ORDER

from .builder import PageSpecBuilder, _stable_token
from .publication_language import contains_cjk_text
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
    "search_input": ("\u641c\u7d22", "\u67e5\u8be2", "search"),
    "filter_control": ("\u7b5b\u9009", "\u8fc7\u6ee4", "filter"),
    "result_list": ("\u5217\u8868", "list"),
    "cart_summary": ("\u8d2d\u7269\u8f66", "cart", "basket"),
    "checkout_action": ("\u7ed3\u7b97", "\u652f\u4ed8", "checkout", "payment"),
    "media_input": ("\u62cd\u6444", "\u62cd\u7167", "\u4e0a\u4f20", "\u7167\u7247", "\u56fe\u7247", "camera", "upload"),
    "analysis_result": ("\u8bc6\u522b", "\u5206\u6790", "recognition", "analysis"),
    "location_picker": ("\u5730\u56fe", "\u5730\u70b9", "\u4f4d\u7f6e", "\u5730\u5740", "\u5b9a\u4f4d", "map", "location"),
    "metric_summary": ("\u770b\u677f", "\u6307\u6807", "\u7edf\u8ba1", "\u6570\u636e", "dashboard", "metric"),
    "detail_view": ("\u8be6\u60c5", "detail"),
    "empty_state": ("\u7a7a\u72b6\u6001", "\u65e0\u5339\u914d", "\u6ca1\u6709\u5339\u914d", "\u65e0\u6570\u636e", "\u6ca1\u6709\u6570\u636e", "empty", "no data"),
    "permission_recovery": ("\u6743\u9650", "\u62d2\u7edd", "permission"),
    "retry_recovery": ("\u8f93\u5165\u9519\u8bef", "\u9519\u8bef", "\u5931\u8d25", "\u91cd\u8bd5", "retry", "error"),
    "form_structure": ("\u586b\u5199", "\u8868\u5355", "\u767b\u5f55", "\u6ce8\u518c", "login", "register", "sign in"),
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
    "search_input": ("search_input", "Search input", "Enter keywords and start a search"),
    "filter_control": ("search_input", "Filter controls", "Adjust filters and update results"),
    "result_list": ("data_view", "Result list", "Show results that match the current query"),
    "cart_summary": ("primary_action", "Cart summary", "Review selected items and quantities"),
    "checkout_action": ("primary_action", "Checkout", "Confirm the order and complete checkout"),
    "media_input": ("media_input", "Capture or upload", "Provide the local media requested by the user"),
    "analysis_result": ("data_view", "Analysis result", "Show the analysis result requested by the user"),
    "location_picker": ("location_picker", "Location picker", "Search, select, and confirm a location"),
    "metric_summary": ("data_view", "Key metrics", "Show the requested metric summary"),
    "detail_view": ("data_view", "Detail view", "Show key information about the selected item"),
    "empty_state": ("status_panel", "Empty result", "Explain that no content matches and provide a recovery action"),
    "form_structure": ("form", "Form", "Provide the requested data-entry or authentication form"),
}

_COMPONENT_PRESENTATION_EN: dict[str, tuple[str, str, str]] = {
    "search_input": ("search_input", "Search input", "Enter keywords and start a search"),
    "filter_control": ("search_input", "Filter controls", "Adjust filters and update results"),
    "result_list": ("data_view", "Result list", "Show results that match the current query"),
    "cart_summary": ("primary_action", "Cart summary", "Review selected items and quantities"),
    "checkout_action": ("primary_action", "Checkout", "Confirm the order and complete checkout"),
    "media_input": ("media_input", "Capture or upload", "Provide the local media requested by the user"),
    "analysis_result": ("data_view", "Analysis result", "Show the analysis result requested by the user"),
    "location_picker": ("location_picker", "Location picker", "Search, select, and confirm a location"),
    "metric_summary": ("data_view", "Key metrics", "Show the requested metric summary"),
    "detail_view": ("data_view", "Detail view", "Show key information about the selected item"),
    "empty_state": ("status_panel", "Empty result", "Explain that no content matches and provide a recovery action"),
    "form_structure": ("form", "Form", "Provide the requested data-entry or authentication form"),
}

_TAP_WORDS = (
    "\u70b9\u51fb",
    "\u9009\u62e9",
    "\u786e\u8ba4",
    "\u67e5\u770b",
    "\u641c\u7d22",
    "\u7b5b\u9009",
    "\u4e0a\u4f20",
    "\u62cd\u6444",
    "\u7ed3\u7b97",
    "\u63d0\u4ea4",
    "tap",
    "select",
    "confirm",
    "view",
    "search",
    "filter",
    "upload",
    "capture",
    "checkout",
    "submit",
)
_SWIPE_WORDS = (
    "\u6ed1\u52a8",
    "\u6eda\u52a8",
    "\u6d4f\u89c8",
    "\u5217\u8868",
    "\u641c\u7d22",
    "\u7b5b\u9009",
    "swipe",
    "scroll",
    "browse",
    "list",
    "search",
    "filter",
)
_ACTION_WORDS = tuple(
    dict.fromkeys(
        (
            *_TAP_WORDS,
            *_SWIPE_WORDS,
            "\u5206\u6790",
            "\u8bc6\u522b",
            "analyze",
            "recognize",
        )
    )
)


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


def _is_english_page_spec(page_spec: PageSpec) -> bool:
    values = [
        page_spec.title,
        page_spec.summary,
        *(
            value
            for item in page_spec.use_cases
            for value in (
                item.title,
                item.actor,
                item.goal,
                item.expected_outcome,
            )
        ),
    ]
    return not any(contains_cjk_text(value) for value in values)


def _component_presentation(
    page_spec: PageSpec,
    concept: str,
) -> tuple[str, str, str]:
    source = (
        _COMPONENT_PRESENTATION_EN
        if _is_english_page_spec(page_spec)
        else _COMPONENT_PRESENTATION
    )
    return source[concept]


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
        if concept == "metric_summary" and _contains_any(
            text,
            (
                "\u6838\u5fc3",
                "\u72b6\u6001",
                "\u6d4f\u89c8",
                "core",
                "status",
                "browse",
            ),
        ):
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
    component_type, label, purpose = _component_presentation(
        page_spec,
        concept,
    )
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
    component_type, label, _ = _component_presentation(
        page_spec,
        concept,
    )
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
        description=(
            "No matching content is available. The page explains the empty "
            "result and provides a next action."
            if _is_english_page_spec(page_spec)
            else (
                "No matching content is available. The page explains the "
                "empty result and provides a next action."
            )
        ),
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
                reason=(
                    "The controlled ablation removed retrieval guidance for "
                    "this role; no PageSpec structural effect is claimed."
                ),
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
                    "The component is retained by the original requirement or "
                    "an explicit constraint; retrieval is not claimed as the "
                    "source of this capability."
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
                    reason=(
                        "The dashboard layout is guaranteed by the original "
                        "requirement; weak UI retrieval is not required as proof."
                    ),
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
        english_publication = _is_english_page_spec(page_spec)
        cases: list[tuple[str, str, str, str, str, str, str]] = []
        if (
            _contains_any(
                constraints_text,
                (
                    "\u5b9a\u4f4d\u4e0d\u53ef\u7528",
                    "\u4f4d\u7f6e\u4e0d\u53ef\u7528",
                    "location unavailable",
                    "location is unavailable",
                    "location services are unavailable",
                ),
            )
            and _contains_any(
                constraints_text,
                ("\u624b\u52a8", "\u5730\u5740", "manual"),
            )
        ):
            cases.append(
                (
                    "location-manual",
                    "location_picker",
                    "state-error",
                    (
                        "Simulate unavailable location"
                        if english_publication
                        else "Simulate unavailable location"
                    ),
                    (
                        "Choose an address manually"
                        if english_publication
                        else "Choose an address manually"
                    ),
                    (
                        "Location is unavailable. Choose an address manually."
                        if english_publication
                        else "Location is unavailable. Choose an address manually."
                    ),
                    (
                        "The page is actionable again. Select and confirm an "
                        "address manually."
                        if english_publication
                        else (
                            "The page is actionable again. Select and confirm an "
                            "address manually."
                        )
                    ),
                )
            )
        if _context_has(context, "empty_state") and _contains_any(
            constraints_text,
            (
                "\u6e05\u9664\u7b5b\u9009",
                "\u91cd\u7f6e\u7b5b\u9009",
                "clear filter",
            ),
        ):
            cases.append(
                (
                    "empty-clear-filter",
                    "filter_control",
                    "state-empty",
                    (
                        "Review an empty result"
                        if english_publication
                        else "Review an empty result"
                    ),
                    (
                        "Clear filters"
                        if english_publication
                        else "Clear filters"
                    ),
                    (
                        "No data matches. Clear the filters and try again."
                        if english_publication
                        else "No data matches. Clear the filters and try again."
                    ),
                    (
                        "The filters are cleared. Review the data again."
                        if english_publication
                        else "The filters are cleared. Review the data again."
                    ),
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
                (
                    "Validate the explicitly requested recovery boundary "
                    "offline."
                    if english_publication
                    else "Validate the explicitly requested recovery boundary offline."
                ),
            )
            recovery = ComponentSpec(
                recovery_id,
                section.section_id,
                "primary_action",
                recovery_label,
                (
                    "Run the explicitly requested conservative recovery action."
                    if english_publication
                    else "Run the explicitly requested conservative recovery action."
                ),
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
                (
                    f"An explicit constraint must enter {target_state.name} "
                    "and show the reason plus a recovery action."
                    if english_publication
                    else (
                        f"An explicit constraint must enter {target_state.name} "
                        "and show the reason plus a recovery action."
                    )
                ),
                use_case_id,
                target_state.state_id,
            )
            recover_check = _append_acceptance(
                page_spec,
                f"context-{case_token}-recover",
                (
                    "The explicit recovery action must return to initial and "
                    "preserve the normal flow."
                    if english_publication
                    else (
                        "The explicit recovery action must return to initial and "
                        "preserve the normal flow."
                    )
                ),
                use_case_id,
                "state-initial",
            )
            fallback.append(
                _decision(
                    "fallback",
                    source_kind="agent_context",
                    role="context",
                    rule=f"context_explicit_recovery:v1:{case_token}",
                    reason=(
                        "The recovery behavior comes directly from an explicit "
                        "constraint; retrieval is not claimed as the source."
                    ),
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
        english_publication = _is_english_page_spec(page_spec)
        for item in guidance.requirement_guidance:
            if item.category == "business_boundary_hint" and not adopted_boundary:
                constraint = _append_constraint(
                    page_spec,
                    "requirement-evidence-boundary",
                    (
                        "Retrieved requirement examples only provide a "
                        "structural evidence boundary; they do not override "
                        "the original requirement, device, use cases, or "
                        "explicit constraints."
                        if english_publication
                        else (
                            "Retrieved requirement examples only provide a "
                            "structural evidence boundary; they do not override "
                            "the original requirement, device, use cases, or "
                            "explicit constraints."
                        )
                    ),
                )
                adopted.append(
                    _decision(
                        "adopted",
                        source_kind="retrieval_guidance",
                        role="requirement",
                        item=item,
                        rule="requirement_boundary:v1:reference_only",
                        reason=(
                            "The source is an allowed requirement reference and "
                            "only supplements the evidence boundary."
                        ),
                        affected_fields=(AffectedPageSpecField(constraint.constraint_id, "description"),),
                    )
                )
                adopted_boundary = True
            elif item.category == "reusable_constraint" and _context_has(context, item.value) and item.value not in adopted_values:
                constraint = _append_constraint(
                    page_spec,
                    f"requirement-{item.value}",
                    (
                        f"Retrieved requirement examples only add structural "
                        f"evidence for {item.value}; the current context "
                        "remains authoritative."
                        if english_publication
                        else (
                            f"Retrieved requirement examples only add structural "
                            f"evidence for {item.value}; the current context "
                            "remains authoritative."
                        )
                    ),
                )
                adopted.append(
                    _decision(
                        "adopted",
                        source_kind="retrieval_guidance",
                        role="requirement",
                        item=item,
                        rule=f"requirement_boundary:v1:context_confirmed:{item.value}",
                        reason=(
                            "The controlled value is explicitly supported by "
                            "the current context; retrieval only supplements "
                            "the boundary."
                        ),
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
                        reason=(
                            "The current context does not explicitly support "
                            "this controlled capability; similar requirements "
                            "cannot add it."
                        ),
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
                        reason=(
                            "A similar task type or duplicate reference is not "
                            "enough to change the PageSpec; retain the context "
                            "and existing rule."
                        ),
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
        english_publication = _is_english_page_spec(page_spec)
        for item in guidance.ui_guidance:
            if item.category == "ui_reference_uri":
                fallback.append(
                    _decision(
                        "fallback",
                        source_kind="retrieval_guidance",
                        role="ui_reference",
                        item=item,
                        rule="ui_guidance:v1:reference_only",
                        reason=(
                            "The UI URI is retained as evidence only; it does "
                            "not copy an asset or change business behavior."
                        ),
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
                        reason=(
                            "A generic or unsupported UI hint is insufficient "
                            "to change the PageSpec."
                        ),
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
                        reason=(
                            "This controlled UI value is unrelated to the "
                            "original requirement, use cases, and explicit "
                            "constraints."
                        ),
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
                        reason=(
                            "The same controlled UI value already has an earlier "
                            "stable adopted source."
                        ),
                    )
                )
                continue

            component = _ensure_component(page_spec, context, item.value, "guided-ui")
            purpose_note = (
                f"Retrieved UI guidance informs the structure for "
                f"{item.value}; "
                if english_publication
                else f"Retrieved UI guidance informs the structure for {item.value}; "
            )
            if not component.purpose.startswith(purpose_note):
                component.purpose = purpose_note + component.purpose
            affected = [AffectedPageSpecField(component.component_id, "purpose")]
            if item.value in {"search_input", "location_picker", "metric_summary"}:
                page_spec.layout.pattern = self._layout_pattern(context)
                affected.append(AffectedPageSpecField(page_spec.page_id, "layout.pattern"))
            if item.value == "empty_state":
                empty_state = _ensure_empty_state(page_spec)
                empty_state.description = (
                    "Retrieved UI guidance supports a clear empty-result "
                    "presentation; the current requirement or constraint "
                    "still determines recovery behavior."
                    if english_publication
                    else (
                        "Retrieved UI guidance supports a clear empty-result "
                        "presentation; the current requirement or constraint "
                        "still determines recovery behavior."
                    )
                )
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
                    reason=(
                        "The controlled UI value is semantically consistent "
                        "with the current context and changes the component or "
                        "layout expression."
                    ),
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
                score = (
                    action_score
                    if action_score >= 2
                    or re.search(r"[\u3001\u4e0e\u548c]| and |, ", text)
                    else 0
                )
            elif value == "single_step_transition":
                score = (
                    action_score
                    if action_score == 1
                    and not re.search(r"[\u3001\u4e0e\u548c]| and |, ", text)
                    else 0
                )
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
        english_publication = _is_english_page_spec(page_spec)
        labels = (
            {
                "tap": "tap action",
                "swipe": "swipe navigation",
                "tap_and_swipe": "tap and swipe",
                "single_step_transition": "single-step transition",
                "multi_step_transition": "multi-step transition",
            }
            if english_publication
            else {
                "tap": "tap action",
                "swipe": "swipe navigation",
                "tap_and_swipe": "tap and swipe",
                "single_step_transition": "single-step transition",
                "multi_step_transition": "multi-step transition",
            }
        )
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
                        reason=(
                            "The use-case trace alone is insufficient; the "
                            "title, goal, and expected outcome do not support "
                            "this flow pattern."
                        ),
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
                        reason=(
                            "This use case already has an earlier guidance item "
                            "of the same kind; avoid stacking conflicting changes."
                        ),
                    )
                )
                continue
            interaction = self._primary_interaction(page_spec, use_case_id)
            phrase = (
                f"; interaction pattern: {labels[item.value]}"
                if english_publication
                else f"; interaction pattern: {labels[item.value]}"
            )
            if phrase not in interaction.action:
                interaction.action += phrase
            adopted.append(
                _decision(
                    "adopted",
                    source_kind="retrieval_guidance",
                    role="interaction_flow",
                    item=item,
                    rule=f"interaction_guidance:v1:semantic_use_case_match:{item.value}",
                    reason=(
                        "The controlled flow value deterministically matches the "
                        "use-case semantics and only adjusts interaction wording; "
                        "it does not create new business behavior."
                    ),
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
        english_publication = _is_english_page_spec(page_spec)
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
                        reason=(
                            "The implementation value is unrelated to the "
                            "current context and cannot change a builder "
                            "constraint."
                        ),
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
                        reason=(
                            "The same implementation structure or resource "
                            "constraint is already adopted; avoid duplication."
                        ),
                    )
                )
                continue
            if item.value == "reference_structure":
                description = (
                    "Retrieved implementation guidance only organizes a "
                    "stable single-page structure; it does not copy business "
                    "content."
                    if english_publication
                    else (
                        "Retrieved implementation guidance only organizes a "
                        "stable single-page structure; it does not copy "
                        "business content."
                    )
                )
            else:
                description = (
                    f"Retrieved implementation guidance may organize "
                    f"structure or resources using {item.value}; references "
                    "must remain project-relative paths or HTTPS."
                    if english_publication
                    else (
                        f"Retrieved implementation guidance may organize "
                        f"structure or resources using {item.value}; references "
                        "must remain project-relative paths or HTTPS."
                    )
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
                    reason=(
                        "Implementation guidance only creates a sourced "
                        "builder constraint and does not expand business scope."
                    ),
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
        english_publication = _is_english_page_spec(page_spec)
        constraints_text = " ".join(context.constraints).casefold()
        recovery_declared = _contains_any(
            constraints_text,
            (
                "\u6062\u590d",
                "\u91cd\u8bd5",
                "\u4fee\u6539",
                "\u624b\u52a8",
                "recover",
                "retry",
            ),
        )
        gates = {
            "empty_state": _context_has(context, "empty_state"),
            "retry_recovery": recovery_declared
            and _contains_any(
                constraints_text,
                (
                    "\u9519\u8bef",
                    "\u5931\u8d25",
                    "\u8f93\u5165",
                    "retry",
                    "error",
                ),
            ),
            "permission_recovery": recovery_declared
            and _contains_any(
                constraints_text,
                ("\u6743\u9650", "\u62d2\u7edd", "permission"),
            ),
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
                        reason=(
                            "Generic regression cases and external URIs are "
                            "evidence only; they cannot create acceptance "
                            "behavior by themselves."
                        ),
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
                        reason=(
                            "Only empty_state, retry_recovery, and "
                            "permission_recovery are gated values supported in "
                            "this version."
                        ),
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
                        reason=(
                            "The original requirement or an explicit constraint "
                            "does not establish this error or recovery boundary."
                        ),
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
                        reason=(
                            "An earlier validation source of the same kind is "
                            "already adopted."
                        ),
                    )
                )
                continue

            affected: list[AffectedPageSpecField] = []
            if item.value == "empty_state":
                use_case_id = _best_use_case(context, "filter_control")
                empty_state_preexisting = any(
                    state.name == "empty" for state in page_spec.states
                )
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
                        (
                            "Review an empty result"
                            if english_publication
                            else "Review an empty result"
                        ),
                        (
                            "Validate the explicitly requested empty-result "
                            "boundary offline."
                            if english_publication
                            else (
                                "Validate the explicitly requested empty-result "
                                "boundary offline."
                            )
                        ),
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
                        (
                            "Show the empty result for the current filters"
                            if english_publication
                            else "Show the empty result for the current filters"
                        ),
                        empty_state.state_id,
                        (
                            "No data matches. Clear the filters and try again."
                            if english_publication
                            else "No data matches. Clear the filters and try again."
                        ),
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
                    (
                        "When the requirement declares a no-match scenario, "
                        "the page must show an empty state and retain a "
                        "recovery action."
                        if english_publication
                        else (
                            "When the requirement declares a no-match scenario, "
                            "the page must show an empty state and retain a "
                            "recovery action."
                        )
                    ),
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
                if not empty_state_preexisting:
                    affected.insert(
                        0,
                        AffectedPageSpecField(empty_state.state_id, "description"),
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
                            reason=(
                                "The context did not produce a traceable recovery "
                                "path; validation cannot create one independently."
                            ),
                        )
                    )
                    continue
                label = (
                    (
                        "permission-denial recovery"
                        if item.value == "permission_recovery"
                        else "invalid-input retry"
                    )
                    if english_publication
                    else (
                        "permission-denial recovery"
                        if item.value == "permission_recovery"
                        else "invalid-input retry"
                    )
                )
                check = _append_acceptance(
                    page_spec,
                    f"{item.value}-{use_case_id}",
                    (
                        f"At the explicitly requested {label} boundary, "
                        "recovery must allow the original primary flow to run "
                        "again; retrieved examples only supplement the "
                        "acceptance boundary."
                        if english_publication
                        else (
                            f"At the explicitly requested {label} boundary, "
                            "recovery must allow the original primary flow to "
                            "run again; retrieved examples only supplement "
                            "the acceptance boundary."
                        )
                    ),
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
                    reason=(
                        "Validation is consistent with the original requirement "
                        "or explicit constraint and only supplements state and "
                        "recovery acceptance."
                    ),
                    affected_fields=affected,
                )
            )
            adopted_values.add(item.value)
