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
from .publication_language import contains_cjk_text
from .recovery import ErrorRecoveryScenario, match_error_recovery_constraint


_TITLE_PREFIX = re.compile(
    r"^(?:\u6211\u60f3|\u8bf7|\u5e2e\u6211)?"
    r"(?:\u505a|\u521b\u5efa|\u6784\u5efa|\u8bbe\u8ba1)"
    r"(?:\u4e00\u4e2a|\u4e00\u6b3e)?"
)
_EMPTY_STATE_KEYWORDS = (
    "\u641c\u7d22",
    "\u7b5b\u9009",
    "\u5217\u8868",
    "\u76ee\u5f55",
    "\u6d4f\u89c8",
    "\u7ed3\u679c",
    "search",
    "filter",
    "list",
    "result",
)
_COMPONENT_TYPE_RULES = (
    ("media_input", ("\u62cd\u6444", "\u4e0a\u4f20", "\u56fe\u7247", "camera", "upload")),
    ("search_input", ("\u641c\u7d22", "\u7b5b\u9009", "\u67e5\u8be2", "search", "filter")),
    ("form", ("\u586b\u5199", "\u8868\u5355", "\u767b\u5f55", "\u6ce8\u518c", "form", "login")),
    ("data_view", ("\u6570\u636e", "\u7edf\u8ba1", "\u56fe\u8868", "dashboard", "chart")),
    ("location_picker", ("\u5730\u56fe", "\u4f4d\u7f6e", "\u5730\u5740", "map", "location")),
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
    first_clause = re.split(
        r"[\u3002\uff01\uff1f!?\n]",
        requirement.strip(),
        maxsplit=1,
    )[0]
    title = _TITLE_PREFIX.sub("", first_clause).strip(
        " \u3000\uff0c,\uff1a:"
    )
    if not title or contains_cjk_text(title):
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


def _select_recovery_component(
    scenario: ErrorRecoveryScenario,
    components: list[ComponentSpec],
) -> ComponentSpec:
    for component_type in scenario.preferred_component_types:
        for component in components:
            if component.component_type == component_type:
                return component
    for component in components:
        if component.component_type != "status_panel":
            return component
    raise ValueError("PageSpec cannot attach error recovery to a status-only page")


def _uses_english_publication_copy(context: AgentContextBundle) -> bool:
    visible_values = [
        context.original_requirement,
        context.requirement_summary,
        *context.constraints,
        *(
            value
            for item in context.use_cases
            for value in (
                item.title,
                item.actor,
                item.goal,
                item.expected_outcome,
            )
        ),
    ]
    return not any(contains_cjk_text(value) for value in visible_values)


def _english_recovery_scenario(
    scenario: ErrorRecoveryScenario,
) -> ErrorRecoveryScenario:
    copy_by_kind = {
        "permission": ErrorRecoveryScenario(
            kind="permission",
            preferred_component_types=("media_input",),
            trigger_label="Simulate camera permission denial",
            trigger_purpose=(
                "Simulate denied camera permission offline without requesting "
                "real browser permission."
            ),
            trigger_action="Simulate camera permission denial",
            error_feedback=(
                "Camera permission was denied. Return to use sample input, or "
                "allow camera access and try again."
            ),
            recovery_label="Return and use sample input",
            recovery_purpose=(
                "Return to an actionable state and continue with offline "
                "sample input."
            ),
            recovery_action="Recover by returning to sample input",
            recovery_feedback=(
                "The page is actionable again. Continue with sample input."
            ),
        ),
        "input": ErrorRecoveryScenario(
            kind="input",
            preferred_component_types=("search_input", "form"),
            trigger_label="Simulate invalid input",
            trigger_purpose=(
                "Simulate invalid input offline to verify the error reason "
                "and recovery action."
            ),
            trigger_action="Simulate invalid input",
            error_feedback=(
                "The input is invalid. Return, update the value, and try again."
            ),
            recovery_label="Update the input and try again",
            recovery_purpose=(
                "Return to an actionable state, correct the input, and rerun "
                "the normal flow."
            ),
            recovery_action="Recover by updating the input",
            recovery_feedback=(
                "The input state is available again. Update the value and "
                "try again."
            ),
        ),
        "generic": ErrorRecoveryScenario(
            kind="generic",
            preferred_component_types=(
                "form",
                "search_input",
                "media_input",
                "primary_action",
                "data_view",
                "location_picker",
            ),
            trigger_label="Simulate task failure",
            trigger_purpose=(
                "Simulate task failure offline to verify a clear reason and "
                "recovery action."
            ),
            trigger_action="Simulate task failure",
            error_feedback=(
                "The task could not be completed. Return, check the input, "
                "and try again."
            ),
            recovery_label="Return and try again",
            recovery_purpose=(
                "Return to an actionable state and rerun the normal flow "
                "after correcting the issue."
            ),
            recovery_action="Recover by returning and trying again",
            recovery_feedback=(
                "The page is actionable again. You can retry the task."
            ),
        ),
    }
    return copy_by_kind[scenario.kind]


class PageSpecBuilder:
    """Build ``req2web.page_spec.v1`` from an existing Agent context bundle."""

    def build(self, context: AgentContextBundle) -> PageSpec:
        if not isinstance(context, AgentContextBundle):
            raise TypeError("PageSpecBuilder input must be an AgentContextBundle")
        if context.schema_version != AGENT_BUNDLE_SCHEMA_VERSION:
            raise ValueError(f"unsupported Agent context schema: {context.schema_version}")
        context.validate()
        english_publication = _uses_english_publication_copy(context)
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
                        label=(
                            f"{use_case.title} feedback"
                            if english_publication
                            else f"{use_case.title} feedback"
                        ),
                        purpose=use_case.expected_outcome,
                    ),
                )
            )
            interactions.append(
                InteractionSpec(
                    interaction_id=interaction_id,
                    trigger_component_id=action_id,
                    source_state_id="state-initial",
                    action=(
                        f"Perform: {use_case.goal}"
                        if english_publication
                        else f"Perform: {use_case.goal}"
                    ),
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

        recovery_matches: list[ErrorRecoveryScenario] = []
        seen_recovery_kinds: set[str] = set()
        for description in context.constraints:
            scenario = match_error_recovery_constraint(description)
            if scenario is not None and scenario.kind not in seen_recovery_kinds:
                if english_publication:
                    scenario = _english_recovery_scenario(scenario)
                recovery_matches.append(scenario)
                seen_recovery_kinds.add(scenario.kind)

        initial_error_trigger_ids: list[str] = []
        error_recovery_component_ids: list[str] = []
        recovery_acceptance_parts: list[tuple[str, str]] = []
        base_components = components.copy()
        sections_by_id = {item.section_id: item for item in sections}
        for scenario_index, scenario in enumerate(recovery_matches, 1):
            target = _select_recovery_component(scenario, base_components)
            section = sections_by_id[target.section_id]
            use_case_id = section.use_case_ids[0]
            token = _stable_token(use_case_id, f"uc-{scenario_index:02d}")
            scenario_token = scenario.kind

            error_component_id = f"component-{token}-error-{scenario_token}"
            recovery_component_id = f"component-{token}-recovery-{scenario_token}"
            error_interaction_id = f"interaction-{token}-error-{scenario_token}"
            recovery_interaction_id = (
                f"interaction-{token}-recovery-{scenario_token}"
            )

            components.extend(
                (
                    ComponentSpec(
                        component_id=error_component_id,
                        section_id=section.section_id,
                        component_type="primary_action",
                        label=scenario.trigger_label,
                        purpose=scenario.trigger_purpose,
                    ),
                    ComponentSpec(
                        component_id=recovery_component_id,
                        section_id=section.section_id,
                        component_type="primary_action",
                        label=scenario.recovery_label,
                        purpose=scenario.recovery_purpose,
                    ),
                )
            )
            section.component_ids.insert(1, error_component_id)
            section.component_ids.append(recovery_component_id)
            interactions.extend(
                (
                    InteractionSpec(
                        interaction_id=error_interaction_id,
                        trigger_component_id=error_component_id,
                        source_state_id="state-initial",
                        action=scenario.trigger_action,
                        target_state_id="state-error",
                        user_feedback=scenario.error_feedback,
                        use_case_ids=[use_case_id],
                    ),
                    InteractionSpec(
                        interaction_id=recovery_interaction_id,
                        trigger_component_id=recovery_component_id,
                        source_state_id="state-error",
                        action=scenario.recovery_action,
                        target_state_id="state-initial",
                        user_feedback=scenario.recovery_feedback,
                        use_case_ids=[use_case_id],
                    ),
                )
            )
            initial_error_trigger_ids.append(error_component_id)
            error_recovery_component_ids.append(recovery_component_id)
            trace_parts[use_case_id]["component_ids"].extend(
                (error_component_id, recovery_component_id)
            )
            trace_parts[use_case_id]["interaction_ids"].extend(
                (error_interaction_id, recovery_interaction_id)
            )
            recovery_acceptance_parts.append((scenario_token, use_case_id))

        states = [
            PageState(
                state_id="state-initial",
                name="initial",
                description=(
                    "The page is ready and the primary workflow is actionable."
                    if english_publication
                    else "The page is ready and the primary workflow is actionable."
                ),
                visible_component_ids=[
                    *action_component_ids,
                    *output_component_ids,
                    *initial_error_trigger_ids,
                ],
            ),
            PageState(
                state_id="state-loading",
                name="loading",
                description=(
                    "The system is processing the requested task."
                    if english_publication
                    else "The system is processing the requested task."
                ),
                visible_component_ids=output_component_ids.copy(),
            ),
            PageState(
                state_id="state-error",
                name="error",
                description=(
                    "The page shows the failure reason and a recovery action."
                    if english_publication
                    else "The page shows the failure reason and a recovery action."
                ),
                visible_component_ids=[
                    *output_component_ids,
                    *error_recovery_component_ids,
                ],
            ),
            PageState(
                state_id="state-success",
                name="success",
                description=(
                    "The primary task is complete with a clear result and "
                    "next action."
                    if english_publication
                    else (
                        "The primary task is complete with a clear result and "
                        "next action."
                    )
                ),
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
                    description=(
                        "No matching content is available. The page explains "
                        "the empty result and provides a next action."
                        if english_publication
                        else (
                            "No matching content is available. The page explains "
                            "the empty result and provides a next action."
                        )
                    ),
                    visible_component_ids=[*action_component_ids, *output_component_ids],
                ),
            )

        constraint_descriptions = _deduplicate(
            [
                *context.constraints,
                (
                    f"The page must support the target device: "
                    f"{context.target_device}"
                    if english_publication
                    else (
                        f"The page must support the target device: "
                        f"{context.target_device}"
                    )
                ),
                (
                    "The page structure must cover every core use case and "
                    "provide clear state feedback"
                    if english_publication
                    else (
                        "The page structure must cover every core use case and "
                        "provide clear state feedback"
                    )
                ),
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
                    (
                        f'After the user completes "{use_case.title}", the '
                        f"page must {use_case.expected_outcome}."
                    )
                    if english_publication
                    else (
                        f'After the user completes "{use_case.title}", the '
                        f"page must {use_case.expected_outcome}."
                    )
                ),
                use_case_ids=[use_case.use_case_id],
                state_id="state-success",
            )
            for index, use_case in enumerate(context.use_cases, 1)
        ]
        for scenario_token, use_case_id in recovery_acceptance_parts:
            acceptance_checks.extend(
                (
                    AcceptanceCheck(
                        check_id=f"check-error-{scenario_token}",
                        description=(
                            (
                                "An explicit recovery constraint must "
                                "deterministically enter the error state and "
                                "show the reason plus a recovery action."
                            )
                            if english_publication
                            else (
                                "An explicit recovery constraint must "
                                "deterministically enter the error state and "
                                "show the reason plus a recovery action."
                            )
                        ),
                        use_case_ids=[use_case_id],
                        state_id="state-error",
                    ),
                    AcceptanceCheck(
                        check_id=f"check-recovery-{scenario_token}",
                        description=(
                            (
                                "The recovery action must return from error "
                                "to initial and allow the normal flow to run "
                                "again."
                            )
                            if english_publication
                            else (
                                "The recovery action must return from error to "
                                "initial and allow the normal flow to run again."
                            )
                        ),
                        use_case_ids=[use_case_id],
                        state_id="state-initial",
                    ),
                )
            )

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
