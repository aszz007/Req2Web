from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION
from req2web_rag.corpus import ROLE_ORDER


PAGE_SPEC_SCHEMA_VERSION = "req2web.page_spec.v1"


@dataclass
class PageUseCase:
    use_case_id: str
    title: str
    actor: str
    goal: str
    expected_outcome: str


@dataclass
class LayoutSpec:
    pattern: str
    section_order: list[str]


@dataclass
class SectionSpec:
    section_id: str
    title: str
    purpose: str
    component_ids: list[str]
    use_case_ids: list[str]


@dataclass
class ComponentSpec:
    component_id: str
    section_id: str
    component_type: str
    label: str
    purpose: str


@dataclass
class PageState:
    state_id: str
    name: str
    description: str
    visible_component_ids: list[str]


@dataclass
class InteractionSpec:
    interaction_id: str
    trigger_component_id: str
    source_state_id: str
    action: str
    target_state_id: str
    user_feedback: str
    use_case_ids: list[str]


@dataclass
class ConstraintSpec:
    constraint_id: str
    description: str
    source: str


@dataclass
class AcceptanceCheck:
    check_id: str
    description: str
    use_case_ids: list[str]
    state_id: str


@dataclass
class EvidenceReference:
    role: str
    doc_id: str
    title: str
    reference_uris: list[str]


@dataclass
class UseCaseTrace:
    use_case_id: str
    section_ids: list[str]
    component_ids: list[str]
    interaction_ids: list[str]
    evidence_doc_ids: list[str]


@dataclass
class TraceabilitySpec:
    source_context_schema_version: str
    evidence: list[EvidenceReference]
    use_cases: list[UseCaseTrace]


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_unique(values: Iterable[str], field_name: str) -> list[str]:
    items = list(values)
    if len(items) != len(set(items)):
        raise ValueError(f"{field_name} must be unique")
    return items


def _validate_references(
    values: Iterable[str], valid: set[str], field_name: str, *, required: bool = True
) -> list[str]:
    items = _require_unique(values, field_name)
    if required and not items:
        raise ValueError(f"{field_name} must not be empty")
    missing = [value for value in items if value not in valid]
    if missing:
        raise ValueError(f"{field_name} contains unknown references: {missing}")
    return items


@dataclass
class PageSpec:
    page_id: str
    title: str
    summary: str
    target_device: str
    page_type: str
    layout: LayoutSpec
    use_cases: list[PageUseCase]
    sections: list[SectionSpec]
    components: list[ComponentSpec]
    states: list[PageState]
    interactions: list[InteractionSpec]
    constraints: list[ConstraintSpec]
    acceptance_checks: list[AcceptanceCheck]
    traceability: TraceabilitySpec
    schema_version: str = PAGE_SPEC_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != PAGE_SPEC_SCHEMA_VERSION:
            raise ValueError(f"unsupported PageSpec schema: {self.schema_version}")
        for field_name in (
            "page_id",
            "title",
            "summary",
            "target_device",
            "page_type",
        ):
            _require_text(getattr(self, field_name), field_name)

        if not 2 <= len(self.use_cases) <= 4:
            raise ValueError("PageSpec must preserve 2-4 core use cases")
        use_case_ids = _require_unique(
            (item.use_case_id for item in self.use_cases), "use_case IDs"
        )
        valid_use_cases = set(use_case_ids)
        for item in self.use_cases:
            for field_name in (
                "use_case_id",
                "title",
                "actor",
                "goal",
                "expected_outcome",
            ):
                _require_text(getattr(item, field_name), f"use_case.{field_name}")

        section_ids = _require_unique(
            (item.section_id for item in self.sections), "section IDs"
        )
        component_ids = _require_unique(
            (item.component_id for item in self.components), "component IDs"
        )
        state_ids = _require_unique(
            (item.state_id for item in self.states), "state IDs"
        )
        interaction_ids = _require_unique(
            (item.interaction_id for item in self.interactions), "interaction IDs"
        )
        constraint_ids = _require_unique(
            (item.constraint_id for item in self.constraints), "constraint IDs"
        )
        check_ids = _require_unique(
            (item.check_id for item in self.acceptance_checks), "acceptance check IDs"
        )
        all_entity_ids = [
            *section_ids,
            *component_ids,
            *state_ids,
            *interaction_ids,
            *constraint_ids,
            *check_ids,
        ]
        if len(all_entity_ids) != len(set(all_entity_ids)):
            raise ValueError("PageSpec entity IDs must be globally unique")
        if not section_ids or not component_ids or not state_ids or not interaction_ids:
            raise ValueError(
                "PageSpec sections, components, states, and interactions must not be empty"
            )

        _require_text(self.layout.pattern, "layout.pattern")
        layout_sections = _validate_references(
            self.layout.section_order,
            set(section_ids),
            "layout.section_order",
        )
        if set(layout_sections) != set(section_ids):
            raise ValueError("layout.section_order must include every section exactly once")

        components_by_id = {item.component_id: item for item in self.components}
        sections_by_id = {item.section_id: item for item in self.sections}
        interactions_by_id = {
            item.interaction_id: item for item in self.interactions
        }
        evidence_by_id = {
            item.doc_id: item for item in self.traceability.evidence
        }

        for section in self.sections:
            for field_name in ("section_id", "title", "purpose"):
                _require_text(getattr(section, field_name), f"section.{field_name}")
            referenced_components = _validate_references(
                section.component_ids,
                set(component_ids),
                f"section {section.section_id} component_ids",
            )
            _validate_references(
                section.use_case_ids,
                valid_use_cases,
                f"section {section.section_id} use_case_ids",
            )
            if any(
                components_by_id[component_id].section_id != section.section_id
                for component_id in referenced_components
            ):
                raise ValueError(
                    f"section {section.section_id} contains a component owned by another section"
                )

        for component in self.components:
            for field_name in (
                "component_id",
                "section_id",
                "component_type",
                "label",
                "purpose",
            ):
                _require_text(getattr(component, field_name), f"component.{field_name}")
            if component.section_id not in sections_by_id:
                raise ValueError(
                    f"component {component.component_id} references unknown section: "
                    f"{component.section_id}"
                )
            if (
                component.component_id
                not in sections_by_id[component.section_id].component_ids
            ):
                raise ValueError(
                    f"component {component.component_id} is not listed in its owning "
                    f"section {component.section_id} component_ids"
                )

        for state in self.states:
            for field_name in ("state_id", "name", "description"):
                _require_text(getattr(state, field_name), f"state.{field_name}")
            _validate_references(
                state.visible_component_ids,
                set(component_ids),
                f"state {state.state_id} visible_component_ids",
                required=False,
            )

        for interaction in self.interactions:
            for field_name in (
                "interaction_id",
                "trigger_component_id",
                "source_state_id",
                "action",
                "target_state_id",
                "user_feedback",
            ):
                _require_text(
                    getattr(interaction, field_name), f"interaction.{field_name}"
                )
            if interaction.trigger_component_id not in components_by_id:
                raise ValueError(
                    f"interaction {interaction.interaction_id} references unknown component: "
                    f"{interaction.trigger_component_id}"
                )
            missing_states = [
                state_id
                for state_id in (
                    interaction.source_state_id,
                    interaction.target_state_id,
                )
                if state_id not in set(state_ids)
            ]
            if missing_states:
                raise ValueError(
                    f"interaction {interaction.interaction_id} state references contain "
                    f"unknown references: {missing_states}"
                )
            _validate_references(
                interaction.use_case_ids,
                valid_use_cases,
                f"interaction {interaction.interaction_id} use_case_ids",
            )

        if not self.constraints:
            raise ValueError("PageSpec constraints must not be empty")
        for constraint in self.constraints:
            for field_name in ("constraint_id", "description", "source"):
                _require_text(
                    getattr(constraint, field_name), f"constraint.{field_name}"
                )

        if not self.acceptance_checks:
            raise ValueError("PageSpec acceptance_checks must not be empty")
        covered_use_cases: set[str] = set()
        for check in self.acceptance_checks:
            _require_text(check.check_id, "acceptance_check.check_id")
            _require_text(check.description, "acceptance_check.description")
            referenced_use_cases = _validate_references(
                check.use_case_ids,
                valid_use_cases,
                f"acceptance check {check.check_id} use_case_ids",
            )
            covered_use_cases.update(referenced_use_cases)
            if check.state_id not in set(state_ids):
                raise ValueError(
                    f"acceptance check {check.check_id} references unknown state: "
                    f"{check.state_id}"
                )
        if covered_use_cases != valid_use_cases:
            raise ValueError("acceptance_checks must cover every use case")

        if (
            self.traceability.source_context_schema_version
            != AGENT_BUNDLE_SCHEMA_VERSION
        ):
            raise ValueError(
                "traceability must identify req2web.agent.context.v1 as its source"
            )
        evidence_doc_ids = _require_unique(
            (item.doc_id for item in self.traceability.evidence),
            "evidence doc_ids",
        )
        if set(evidence_by_id) != set(evidence_doc_ids):
            raise ValueError("evidence doc_ids must be unique")
        evidence_roles: set[str] = set()
        for evidence in self.traceability.evidence:
            for field_name in ("role", "doc_id", "title"):
                _require_text(getattr(evidence, field_name), f"evidence.{field_name}")
            if evidence.role not in ROLE_ORDER:
                raise ValueError(f"unsupported evidence role: {evidence.role}")
            evidence_roles.add(evidence.role)
            if any(
                not isinstance(uri, str) or not uri.strip()
                for uri in evidence.reference_uris
            ):
                raise ValueError(
                    f"evidence {evidence.doc_id} contains an invalid reference URI"
                )
            _require_unique(
                evidence.reference_uris,
                f"evidence {evidence.doc_id} reference_uris",
            )
        if evidence_roles != set(ROLE_ORDER):
            raise ValueError("PageSpec evidence must cover all five RAG roles")

        trace_use_case_ids = _require_unique(
            (item.use_case_id for item in self.traceability.use_cases),
            "traceability use_case IDs",
        )
        if set(trace_use_case_ids) != valid_use_cases:
            raise ValueError("traceability must contain exactly one trace per use case")
        covered_evidence: set[str] = set()
        for trace in self.traceability.use_cases:
            sections = _validate_references(
                trace.section_ids,
                set(section_ids),
                f"trace {trace.use_case_id} section_ids",
            )
            components = _validate_references(
                trace.component_ids,
                set(component_ids),
                f"trace {trace.use_case_id} component_ids",
            )
            interactions = _validate_references(
                trace.interaction_ids,
                set(interaction_ids),
                f"trace {trace.use_case_id} interaction_ids",
            )
            evidence_ids = _validate_references(
                trace.evidence_doc_ids,
                set(evidence_doc_ids),
                f"trace {trace.use_case_id} evidence_doc_ids",
            )
            if any(
                trace.use_case_id not in sections_by_id[section_id].use_case_ids
                for section_id in sections
            ):
                raise ValueError(
                    f"trace {trace.use_case_id} references an unrelated section"
                )
            if any(
                components_by_id[component_id].section_id not in sections
                for component_id in components
            ):
                raise ValueError(
                    f"trace {trace.use_case_id} references an unrelated component"
                )
            if any(
                trace.use_case_id
                not in interactions_by_id[interaction_id].use_case_ids
                for interaction_id in interactions
            ):
                raise ValueError(
                    f"trace {trace.use_case_id} references an unrelated interaction"
                )
            trace_roles = {evidence_by_id[doc_id].role for doc_id in evidence_ids}
            if trace_roles != set(ROLE_ORDER):
                raise ValueError(
                    f"trace {trace.use_case_id} must reference all five RAG roles"
                )
            covered_evidence.update(evidence_ids)
        if covered_evidence != set(evidence_doc_ids):
            raise ValueError("every evidence doc_id must be linked to at least one use case")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
