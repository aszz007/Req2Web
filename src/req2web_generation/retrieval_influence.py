from __future__ import annotations

"""Deterministic retrieval-influence checks and controlled role ablations.

This module only consumes already materialized context, guidance and PageSpec
objects.  It intentionally has no Retriever, corpus, network, or raw-data
dependency.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
from collections import Counter
from typing import Any, Iterable, Literal

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle
from req2web_rag.corpus import ROLE_ORDER

from .builder import PageSpecBuilder
from .consistency import ConsistencyReport, MinimalConsistencyChecker
from .guided_builder import (
    GUIDED_PAGE_SPEC_BUILD_RESULT_SCHEMA_VERSION,
    GuidanceDecision,
    GuidedPageSpecBuildResult,
    RetrievalGuidedPageSpecBuilder,
)
from .renderer import RenderResult
from .retrieval_guidance import RETRIEVAL_GUIDANCE_SCHEMA_VERSION, RetrievalGuidance
from .schema import PAGE_SPEC_SCHEMA_VERSION, PageSpec


RETRIEVAL_INFLUENCE_REPORT_SCHEMA_VERSION = "req2web.retrieval.influence.report.v1"
_STATUSES = ("pass", "fail")
_ENTITY_COLLECTIONS = (
    ("sections", "section_id", "section"),
    ("components", "component_id", "component"),
    ("states", "state_id", "state"),
    ("interactions", "interaction_id", "interaction"),
    ("constraints", "constraint_id", "constraint"),
    ("acceptance_checks", "check_id", "acceptance_check"),
)
_NON_IMPORTANT_FIELDS = {"section_id", "component_id", "state_id", "interaction_id", "constraint_id", "check_id", "use_case_ids", "component_ids", "visible_component_ids"}


@dataclass(frozen=True)
class InfluenceCheck:
    check_id: str
    category: str
    status: Literal["pass", "fail"]
    message: str
    related_ids: list[str]


@dataclass(frozen=True)
class StructuralDifference:
    entity_id: str
    field_name: str
    field_category: str


@dataclass(frozen=True)
class RoleAblation:
    role: str
    outcome: Literal[
        "role_has_influence",
        "guidance_not_applicable_or_ignored",
        "verification_failed",
    ]
    difference_count: int
    differences: list[StructuralDifference]


@dataclass
class RetrievalInfluenceReport:
    report_id: str
    page_id: str
    guidance_bundle_id: str
    passed: bool
    checks: list[InfluenceCheck]
    structural_differences: list[StructuralDifference]
    decision_traceability: list[dict[str, Any]]
    role_summaries: list[dict[str, Any]]
    field_category_counts: dict[str, int]
    decision_status_counts: dict[str, int]
    ablations: list[RoleAblation]
    renderer_consistency: dict[str, Any]
    schema_version: str = RETRIEVAL_INFLUENCE_REPORT_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != RETRIEVAL_INFLUENCE_REPORT_SCHEMA_VERSION:
            raise ValueError("unsupported retrieval influence report schema")
        if not self.report_id or not self.page_id or not self.guidance_bundle_id:
            raise ValueError("retrieval influence report identities must not be empty")
        if not self.checks:
            raise ValueError("retrieval influence report must contain checks")
        ids = [item.check_id for item in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("retrieval influence check IDs must be unique")
        if any(item.status not in _STATUSES for item in self.checks):
            raise ValueError("retrieval influence check has an unsupported status")
        if self.passed != all(item.status == "pass" for item in self.checks):
            raise ValueError("retrieval influence passed must reflect checks")
        if [item.role for item in self.ablations] != list(ROLE_ORDER):
            raise ValueError("retrieval influence ablations must cover roles in stable order")
        if any(item.difference_count != len(item.differences) for item in self.ablations):
            raise ValueError("ablation difference counts must match details")
        if set(self.decision_status_counts) != {"adopted", "ignored", "fallback"}:
            raise ValueError("retrieval influence decision status counts are incomplete")
        trace_ids = [item.get("decision_id") for item in self.decision_traceability]
        if not trace_ids or any(not isinstance(value, str) or not value for value in trace_ids):
            raise ValueError("retrieval influence decision traceability must contain decision IDs")
        if len(trace_ids) != len(set(trace_ids)):
            raise ValueError("retrieval influence decision traceability IDs must be unique")
        if set(self.field_category_counts) != {"layout", *(kind for _, _, kind in _ENTITY_COLLECTIONS)}:
            raise ValueError("retrieval influence field category counts are incomplete")
        if _report_id(self) != self.report_id:
            raise ValueError("retrieval influence report_id does not match deterministic payload")
        if _contains_absolute_path(self.to_payload()):
            raise ValueError("retrieval influence report must not contain absolute paths")

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("report_id", None)
        return payload

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def _report_id(report: RetrievalInfluenceReport) -> str:
    encoded = json.dumps(report.to_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "retrieval-influence-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def _contains_absolute_path(value: object) -> bool:
    if isinstance(value, dict):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_path(item) for item in value)
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/")
    return normalized.startswith("/") or normalized.startswith("//") or (len(normalized) > 2 and normalized[1:3] == ":/")


def _page_entities(page_spec: PageSpec) -> dict[str, dict[str, Any]]:
    payload = page_spec.to_dict()
    entities = {page_spec.page_id: {"layout": payload["layout"]}}
    for collection, identity, _ in _ENTITY_COLLECTIONS:
        for item in payload[collection]:
            entities[str(item[identity])] = item
    return entities


def structural_differences(before: PageSpec, after: PageSpec) -> list[StructuralDifference]:
    """Return a stable, normalized field-level PageSpec difference list."""
    before.validate()
    after.validate()
    old_entities = _page_entities(before)
    new_entities = _page_entities(after)
    categories = {before.page_id: "layout", after.page_id: "layout"}
    for page_spec in (before, after):
        for collection, identity, category in _ENTITY_COLLECTIONS:
            categories.update({str(item[identity]): category for item in page_spec.to_dict()[collection]})
    result: list[StructuralDifference] = []
    for entity_id in sorted(set(old_entities) | set(new_entities)):
        old = old_entities.get(entity_id, {})
        new = new_entities.get(entity_id, {})
        for field_name in sorted(set(old) | set(new)):
            if old.get(field_name) != new.get(field_name):
                if entity_id in {before.page_id, after.page_id} and field_name == "layout":
                    for layout_field in sorted(set(old.get("layout", {})) | set(new.get("layout", {}))):
                        if old.get("layout", {}).get(layout_field) != new.get("layout", {}).get(layout_field):
                            result.append(StructuralDifference(entity_id, f"layout.{layout_field}", "layout"))
                else:
                    result.append(StructuralDifference(entity_id, field_name, categories[entity_id]))
    return result


def _field_changed(
    differences: Iterable[StructuralDifference], entity_id: str, field_name: str
) -> bool:
    return any(item.entity_id == entity_id and item.field_name == field_name for item in differences)


def _all_decisions(result: GuidedPageSpecBuildResult) -> list[tuple[str, GuidanceDecision]]:
    return [
        *(("adopted", item) for item in result.adopted),
        *(("ignored", item) for item in result.ignored),
        *(("fallback", item) for item in result.fallback),
    ]


class RetrievalInfluenceChecker:
    """Verify claimed retrieval effects and deterministic role ablations."""

    def check(
        self,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        baseline_page_spec: PageSpec,
        guided_result: GuidedPageSpecBuildResult,
        ablations: dict[str, GuidedPageSpecBuildResult],
        render_result: RenderResult,
    ) -> RetrievalInfluenceReport:
        if not isinstance(context, AgentContextBundle):
            raise TypeError("retrieval influence context must be an AgentContextBundle")
        if not isinstance(guidance, RetrievalGuidance):
            raise TypeError("retrieval influence guidance must be RetrievalGuidance")
        if not isinstance(baseline_page_spec, PageSpec):
            raise TypeError("retrieval influence baseline must be a PageSpec")
        if not isinstance(guided_result, GuidedPageSpecBuildResult):
            raise TypeError("retrieval influence guided result must be GuidedPageSpecBuildResult")
        if not isinstance(render_result, RenderResult):
            raise TypeError("retrieval influence render_result must be RenderResult")
        if context.schema_version != AGENT_BUNDLE_SCHEMA_VERSION or guidance.schema_version != RETRIEVAL_GUIDANCE_SCHEMA_VERSION:
            raise ValueError("retrieval influence requires compatible context and guidance schemas")
        context.validate()
        guidance.validate()
        baseline_page_spec.validate()
        guided_result.validate(guidance)
        if set(ablations) != set(ROLE_ORDER):
            raise ValueError("retrieval influence requires exactly one controlled ablation per role")

        expected_baseline = PageSpecBuilder().build(context)
        if expected_baseline.to_dict() != baseline_page_spec.to_dict():
            raise ValueError("baseline PageSpec is not the deterministic unguided result for context")
        expected_full = RetrievalGuidedPageSpecBuilder().build(context, guidance)
        if expected_full.to_dict() != guided_result.to_dict():
            raise ValueError("guided result is not the deterministic full-guidance result for context")
        for role in ROLE_ORDER:
            candidate = ablations[role]
            if not isinstance(candidate, GuidedPageSpecBuildResult):
                raise TypeError("retrieval influence ablation must be GuidedPageSpecBuildResult")
            candidate.validate(guidance)
            expected = RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,))
            if candidate.to_dict() != expected.to_dict():
                raise ValueError(f"{role} ablation is not the deterministic controlled result")

        differences = structural_differences(baseline_page_spec, guided_result.page_spec)
        checks: list[InfluenceCheck] = []
        def add(check_id: str, category: str, passed: bool, message: str, related: Iterable[str] = ()) -> None:
            checks.append(InfluenceCheck(check_id, category, "pass" if passed else "fail", message, list(dict.fromkeys(related))))

        add("input.identity", "input_identity", True, "Context, guidance, baseline and full guided identities are deterministic and compatible.", [guided_result.page_spec.page_id, guidance.guidance_bundle_id])
        add("input.ablation-identity", "input_identity", True, "All five role ablations match their controlled deterministic builds.", ROLE_ORDER)
        for disposition, decision in _all_decisions(guided_result):
            if disposition == "adopted":
                valid = all(_field_changed(differences, item.entity_id, item.field_name) for item in decision.affected_fields)
                add(f"decision.{decision.decision_id}", "decision_effect", valid, "Adopted guidance fields correspond to actual normalized PageSpec differences." if valid else "Adopted guidance claims a PageSpec field that did not change.", [decision.decision_id, decision.guidance_id or decision.role])
            elif decision.source_kind == "retrieval_guidance":
                valid = not decision.affected_fields
                add(f"decision.{decision.decision_id}", "decision_boundary", valid, "Ignored or evidence-only retrieval guidance claims no structural fields." if valid else "Ignored or evidence-only guidance must not claim structural effects.", [decision.decision_id, decision.guidance_id or decision.role])

        decision_entities = {field.entity_id for _, item in _all_decisions(guided_result) for field in item.affected_fields}
        important = [item for item in differences if item.field_name not in _NON_IMPORTANT_FIELDS]
        unclaimed = [item for item in important if item.entity_id not in decision_entities]
        add("difference.attribution", "attribution", not unclaimed, "Every important structural difference has a decision-level entity attribution." if not unclaimed else "Important structural differences lack decision attribution.", [item.entity_id for item in unclaimed])
        add("boundary.explicit-requirement", "priority_boundary", True, "Full guided output is rebuilt from the same context, preserving explicit requirement precedence.", [context.schema_version])
        add("boundary.reference-safety", "safety_boundary", True, "Guidance and full guided output passed existing safe-reference validation.", [guidance.guidance_bundle_id])

        consistency = MinimalConsistencyChecker().check(guided_result.page_spec, render_result)
        add("gate.renderer-consistency", "render_gate", consistency.passed, "Full guided PageSpec passed renderer and structural consistency gates." if consistency.passed else "Full guided PageSpec failed renderer or structural consistency gates.", [guided_result.page_spec.page_id])

        ablation_reports: list[RoleAblation] = []
        role_summaries: list[dict[str, Any]] = []
        for role in ROLE_ORDER:
            role_differences = structural_differences(ablations[role].page_spec, guided_result.page_spec)
            role_decisions = [item for disposition, item in _all_decisions(guided_result) if item.role == role and disposition == "adopted"]
            if role_differences:
                outcome = "role_has_influence"
            elif role_decisions:
                outcome = "verification_failed"
                add(f"ablation.{role}", "ablation", False, "Adopted role guidance produced no structural delta when removed.", [role])
            else:
                outcome = "guidance_not_applicable_or_ignored"
            ablation_reports.append(RoleAblation(role, outcome, len(role_differences), role_differences))
            counts = Counter(disposition for disposition, item in _all_decisions(guided_result) if item.role == role)
            role_summaries.append({"role": role, "adopted": counts["adopted"], "ignored": counts["ignored"], "fallback": counts["fallback"], "ablation_outcome": outcome})

        field_counts = Counter(item.field_category for item in differences)
        decision_counts = Counter(disposition for disposition, _ in _all_decisions(guided_result))
        decision_traceability = [
            {
                "disposition": disposition,
                "decision_id": decision.decision_id,
                "source_kind": decision.source_kind,
                "role": decision.role,
                "guidance_id": decision.guidance_id,
                "doc_id": decision.doc_id,
                "affected_fields": [
                    {"entity_id": field.entity_id, "field_name": field.field_name}
                    for field in decision.affected_fields
                ],
            }
            for disposition, decision in _all_decisions(guided_result)
        ]
        report = RetrievalInfluenceReport(
            report_id="",
            page_id=guided_result.page_spec.page_id,
            guidance_bundle_id=guidance.guidance_bundle_id,
            passed=all(item.status == "pass" for item in checks),
            checks=checks,
            structural_differences=differences,
            decision_traceability=decision_traceability,
            role_summaries=role_summaries,
            field_category_counts={"layout": field_counts["layout"], **{kind: field_counts[kind] for _, _, kind in _ENTITY_COLLECTIONS}},
            decision_status_counts={name: decision_counts[name] for name in ("adopted", "ignored", "fallback")},
            ablations=ablation_reports,
            renderer_consistency={"schema_version": consistency.schema_version, "passed": consistency.passed, "summary": consistency.summary},
        )
        report.report_id = _report_id(report)
        report.validate()
        return report
