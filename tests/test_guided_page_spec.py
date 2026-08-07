from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
from unittest import TestCase, mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402


def result(
    role: str,
    doc_id: str,
    title: str,
    summary: str,
    references: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "score": 1.0,
        "doc_id": doc_id,
        "role": role,
        "dataset": "fixture",
        "subset": "guided",
        "sample_id": doc_id.rsplit(":", 1)[-1],
        "title": title,
        "summary": summary,
        "references": references or [],
    }


def build_context(
    *,
    requirement: str = (
        "Create a mobile page for product search, filtering, and results."
    ),
    summary: str = "Mobile product search and results.",
    device: str = "mobile",
    task_type: str = "catalog",
    constraints: list[str] | None = None,
    use_cases: list[UseCase] | None = None,
    ui_title: str = "Search and filtered results",
    ui_summary: str = "Search controls, filters, and a result list",
    flow_title: str = "mixed flow",
    flow_summary: str = "tap and swipe multi step",
    validation_title: str = "Plain regression",
    validation_summary: str = "regression case",
) -> AgentContextBundle:
    retrieval_results = {
        "requirement": [
            result(
                "requirement",
                "requirement:fixture:guided:req",
                "Reference requirement",
                "bounded frontend requirement",
                [{"kind": "requirement", "uri": "fixtures/requirement.txt"}],
            )
        ],
        "ui_reference": [
            result(
                "ui_reference",
                "ui_reference:fixture:guided:ui",
                ui_title,
                ui_summary,
                [{"kind": "screenshot", "uri": "fixtures/ui.png"}],
            )
        ],
        "interaction_flow": [
            result(
                "interaction_flow",
                "interaction_flow:fixture:guided:flow",
                flow_title,
                flow_summary,
                [
                    {"kind": "step_screenshot", "uri": "fixtures/flow-1.png"},
                    {"kind": "step_screenshot", "uri": "fixtures/flow-2.png"},
                ],
            )
        ],
        "implementation": [
            result(
                "implementation",
                "implementation:fixture:guided:impl",
                "Reference structure",
                "stable reference structure",
                [{"kind": "html", "uri": "fixtures/page.html"}],
            )
        ],
        "validation": [
            result(
                "validation",
                "validation:fixture:guided:case",
                validation_title,
                validation_summary,
                [{"kind": "issue", "uri": "https://example.test/issues/1"}],
            )
        ],
    }
    return AgentContextBundle(
        original_requirement=requirement,
        requirement_summary=summary,
        target_device=device,
        task_type=task_type,
        constraints=constraints or [],
        use_cases=use_cases
        or [
            UseCase(
                "UC-01",
                "Search and filter",
                "User",
                "Search and filter products",
                "Review the result list",
            ),
            UseCase(
                "UC-02",
                "Review details",
                "User",
                "Select and review product details",
                "Understand the product details",
            ),
        ],
        retrieval_queries={role: f"fixture {role}" for role in ROLE_ORDER},
        retrieval_results=retrieval_results,
    )


class GuidedPageSpecTest(TestCase):
    def setUp(self) -> None:
        self.context = build_context()
        self.guidance = RetrievalGuidanceBuilder().build(self.context)
        self.builder = RetrievalGuidedPageSpecBuilder()

    def test_legacy_builder_output_remains_unchanged_without_guidance(self) -> None:
        before = PageSpecBuilder().build(self.context).to_dict()
        self.builder.build(self.context, self.guidance)
        after = PageSpecBuilder().build(self.context).to_dict()
        self.assertEqual(before, after)
        self.assertEqual(before["schema_version"], "req2web.page_spec.v1")

    def test_same_context_and_guidance_are_byte_deterministic(self) -> None:
        first = self.builder.build(self.context, self.guidance)
        second = self.builder.build(self.context, self.guidance)
        encoded = lambda value: json.dumps(
            value.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(encoded(first), encoded(second))

    def test_schema_device_task_and_doc_mismatches_are_rejected(self) -> None:
        invalid_schema = deepcopy(self.guidance)
        invalid_schema.schema_version = "req2web.retrieval.guidance.invalid"
        with self.assertRaisesRegex(ValueError, "schema"):
            self.builder.build(self.context, invalid_schema)

        invalid_source_schema = deepcopy(self.guidance)
        invalid_source_schema.source_context_schema_version = "req2web.agent.context.invalid"
        with self.assertRaisesRegex(ValueError, "context"):
            self.builder.build(self.context, invalid_source_schema)

        invalid_device = deepcopy(self.guidance)
        invalid_device.target_device = "desktop"
        with self.assertRaisesRegex(ValueError, "target_device"):
            self.builder.build(self.context, invalid_device)

        invalid_task = deepcopy(self.guidance)
        invalid_task.task_type = "dashboard"
        with self.assertRaisesRegex(ValueError, "task_type"):
            self.builder.build(self.context, invalid_task)

        changed_context = deepcopy(self.context)
        changed_context.retrieval_results["ui_reference"][0]["doc_id"] = (
            "ui_reference:fixture:guided:other"
        )
        changed_guidance = RetrievalGuidanceBuilder().build(changed_context)
        with self.assertRaisesRegex(ValueError, "doc_id is absent"):
            self.builder.build(self.context, changed_guidance)

    def test_replacing_ui_guidance_changes_component_expression(self) -> None:
        context = build_context(
            requirement=(
                "Create a mobile location-search page where users search for "
                "an address and select a location."
            ),
            summary="Location search and selection.",
            task_type="location_service",
            use_cases=[
                UseCase(
                    "UC-01",
                    "Search for an address",
                    "User",
                    "Search for an address",
                    "Review location results",
                ),
                UseCase(
                    "UC-02",
                    "Select a location",
                    "User",
                    "Select and confirm a location",
                    "Receive the confirmed location",
                ),
            ],
        )
        search_result = self.builder.build(context, RetrievalGuidanceBuilder().build(context))
        changed = deepcopy(context)
        changed.retrieval_results["ui_reference"] = [
            result(
                "ui_reference",
                "ui_reference:fixture:guided:map",
                "Map address location",
                "Select a location and confirm the address",
                [{"kind": "screenshot", "uri": "fixtures/map.png"}],
            )
        ]
        location_result = self.builder.build(
            changed, RetrievalGuidanceBuilder().build(changed)
        )
        search_purposes = {
            item.component_id: item.purpose for item in search_result.page_spec.components
        }
        location_purposes = {
            item.component_id: item.purpose for item in location_result.page_spec.components
        }
        self.assertNotEqual(search_purposes, location_purposes)
        self.assertTrue(any("location_picker" in value for value in location_purposes.values()))
        self.assertTrue(
            any(
                item.role == "ui_reference" and item.affected_fields
                for item in location_result.adopted
            )
        )

    def test_replacing_interaction_guidance_changes_interaction_action(self) -> None:
        mixed = self.builder.build(self.context, self.guidance)
        changed = deepcopy(self.context)
        changed.retrieval_results["interaction_flow"] = [
            result(
                "interaction_flow",
                "interaction_flow:fixture:guided:swipe",
                "swipe flow",
                "swipe list",
                [{"kind": "step_screenshot", "uri": "fixtures/flow.png"}],
            )
        ]
        swipe = self.builder.build(changed, RetrievalGuidanceBuilder().build(changed))
        self.assertNotEqual(
            mixed.page_spec.interactions[0].action,
            swipe.page_spec.interactions[0].action,
        )
        self.assertIn(
            "swipe navigation",
            swipe.page_spec.interactions[0].action,
        )

    def test_tap_and_single_step_guidance_change_interaction_expression(self) -> None:
        context = build_context(
            requirement=(
                "Create a details page where users review information and "
                "save the configuration."
            ),
            summary="Details and configuration.",
            use_cases=[
                UseCase(
                    "UC-01",
                    "Review details",
                    "User",
                    "Review the details",
                    "Understand the details",
                ),
                UseCase(
                    "UC-02",
                    "Save configuration",
                    "User",
                    "Save the configuration",
                    "The configuration is saved",
                ),
            ],
            flow_title="tap flow",
            flow_summary="tap transition",
        )
        context.retrieval_results["interaction_flow"][0]["references"] = [
            {"kind": "step_screenshot", "uri": "fixtures/flow.png"}
        ]
        result_value = self.builder.build(
            context, RetrievalGuidanceBuilder().build(context)
        )
        actions = " ".join(item.action for item in result_value.page_spec.interactions)
        self.assertIn("tap action", actions)
        self.assertIn("single-step transition", actions)

    def test_metric_and_empty_ui_guidance_change_components_and_layout(self) -> None:
        context = build_context(
            requirement=(
                "Create a desktop dashboard with key metrics and an empty "
                "state when no data is available."
            ),
            summary="Metric dashboard and empty state.",
            device="desktop",
            task_type="dashboard",
            constraints=["Show an empty state when no data is available."],
            use_cases=[
                UseCase(
                    "UC-01",
                    "Review key metrics",
                    "User",
                    "Review key metrics",
                    "Understand the metrics",
                ),
                UseCase(
                    "UC-02",
                    "Review status",
                    "User",
                    "Review the status",
                    "Understand the current state",
                ),
            ],
            ui_title="Metric dashboard empty state",
            ui_summary="dashboard metric empty no data",
        )
        result_value = self.builder.build(
            context, RetrievalGuidanceBuilder().build(context)
        )
        self.assertEqual(result_value.page_spec.layout.pattern, "guided_dashboard_flow")
        purposes = " ".join(item.purpose for item in result_value.page_spec.components)
        self.assertIn("metric_summary", purposes)
        self.assertIn("empty_state", purposes)
        adopted_ui_values = {
            item.rule.rsplit(":", 1)[-1]
            for item in result_value.adopted
            if item.role == "ui_reference"
        }
        self.assertTrue({"metric_summary", "empty_state"}.issubset(adopted_ui_values))

    def test_relevant_empty_and_retry_validation_add_gated_acceptance(self) -> None:
        empty_context = build_context(
            requirement=(
                "Create a desktop dashboard with list filters and an empty "
                "state when no records match."
            ),
            summary="Desktop metrics, list filtering, and an empty state.",
            device="desktop",
            task_type="dashboard",
            constraints=[
                "Show an empty state and allow filters to be cleared when "
                "no records match."
            ],
            validation_title="Empty state regression",
            validation_summary="empty no data recovery",
        )
        empty_result = self.builder.build(
            empty_context, RetrievalGuidanceBuilder().build(empty_context)
        )
        self.assertTrue(
            any(item.state_id == "state-empty" for item in empty_result.page_spec.acceptance_checks)
        )

        retry_context = build_context(
            constraints=[
                "Allow users to correct invalid input and retry."
            ],
            validation_title="Input error retry",
            validation_summary="input error permits retry",
        )
        retry_result = self.builder.build(
            retry_context, RetrievalGuidanceBuilder().build(retry_context)
        )
        self.assertTrue(
            any(
                "invalid-input retry" in item.description
                for item in retry_result.page_spec.acceptance_checks
            )
        )

    def test_explicit_location_and_empty_recovery_stay_context_sourced(self) -> None:
        map_context = build_context(
            requirement=(
                "Create a mobile address-search page where users search for "
                "a place, select a result, and confirm the location."
            ),
            summary="Address search and location confirmation.",
            task_type="location_service",
            constraints=[
                "When location is unavailable, allow manual address "
                "selection."
            ],
            use_cases=[
                UseCase(
                    "UC-01",
                    "Search for an address",
                    "User",
                    "Search for an address",
                    "Review candidate locations",
                ),
                UseCase(
                    "UC-02",
                    "Select a location",
                    "User",
                    "Select and confirm a location",
                    "The location is confirmed",
                ),
            ],
            ui_title="Map address location",
            ui_summary="location picker",
        )
        map_result = self.builder.build(
            map_context, RetrievalGuidanceBuilder().build(map_context)
        )
        self.assertTrue(
            any(
                item.label == "Choose an address manually"
                for item in map_result.page_spec.components
            )
        )
        self.assertTrue(
            any(
                item.source_kind == "agent_context" and "location-manual" in item.rule
                for item in map_result.fallback
            )
        )

        dashboard_context = build_context(
            requirement=(
                "Create a desktop dashboard with key metrics, list filters, "
                "and detail views."
            ),
            summary="Desktop data dashboard.",
            device="desktop",
            task_type="dashboard",
            constraints=[
                "When no records match, show an empty state and provide a "
                "clear filter action."
            ],
            ui_title="Empty-state list filters",
            ui_summary="empty list filter",
        )
        dashboard_result = self.builder.build(
            dashboard_context, RetrievalGuidanceBuilder().build(dashboard_context)
        )
        self.assertEqual(dashboard_result.page_spec.layout.pattern, "guided_dashboard_flow")
        self.assertTrue(
            any(
                item.label == "Clear filters"
                for item in dashboard_result.page_spec.components
            )
        )
        self.assertTrue(
            any(
                item.source_kind == "agent_context" and "empty-clear-filter" in item.rule
                for item in dashboard_result.fallback
            )
        )

    def test_implementation_guidance_forms_sourced_builder_constraint(self) -> None:
        result_value = self.builder.build(self.context, self.guidance)
        implementation_decisions = [
            item for item in result_value.adopted if item.role == "implementation"
        ]
        self.assertTrue(implementation_decisions)
        constraints = {item.constraint_id: item for item in result_value.page_spec.constraints}
        for decision in implementation_decisions:
            for affected in decision.affected_fields:
                self.assertEqual(constraints[affected.entity_id].source, "builder")
                self.assertIn(
                    "Retrieved implementation guidance",
                    constraints[affected.entity_id].description,
                )

    def test_unrelated_permission_and_retry_do_not_pollute_page(self) -> None:
        context = build_context(
            constraints=[],
            validation_title="Permission failure retry",
            validation_summary="permission denied and retry recovery",
        )
        result_value = self.builder.build(context, RetrievalGuidanceBuilder().build(context))
        descriptions = " ".join(
            item.description for item in result_value.page_spec.acceptance_checks
        )
        self.assertNotIn("permission-denial recovery", descriptions)
        self.assertNotIn("invalid-input retry", descriptions)
        self.assertFalse(
            any(item.target_state_id == "state-error" for item in result_value.page_spec.interactions)
        )
        ignored_values = {
            item.guidance_id
            for item in result_value.ignored
            if item.role == "validation"
        }
        self.assertTrue(ignored_values)

    def test_rotating_trace_alone_is_not_semantic_relevance(self) -> None:
        context = build_context(
            requirement=(
                "Create a settings page where users enter a name and save "
                "the configuration."
            ),
            summary="Enter and save settings.",
            use_cases=[
                UseCase(
                    "UC-01",
                    "Enter a name",
                    "User",
                    "Enter a name",
                    "The name is recorded",
                ),
                UseCase(
                    "UC-02",
                    "Save configuration",
                    "User",
                    "Save the configuration",
                    "The configuration is saved",
                ),
            ],
            ui_title="Settings form",
            ui_summary="form controls",
            flow_title="swipe flow",
            flow_summary="swipe transition",
        )
        guidance = RetrievalGuidanceBuilder().build(context)
        result_value = self.builder.build(context, guidance)
        flow_ids = {item.guidance_id for item in guidance.interaction_guidance}
        self.assertTrue(
            flow_ids.issubset(
                {
                    item.guidance_id
                    for item in result_value.ignored
                    if item.role == "interaction_flow"
                }
            )
        )
        self.assertFalse(
            any(
                "swipe navigation" in item.action
                for item in result_value.page_spec.interactions
            )
        )

    def test_weak_pet_ui_keeps_media_input_sourced_from_context(self) -> None:
        context = build_context(
            requirement=(
                "Create a pet-emotion recognition app where users capture or "
                "upload a photo and review the analysis."
            ),
            summary="Pet-image input and emotion analysis.",
            task_type="recognition_tool",
            constraints=[
                "Provide a recovery path when camera permission is denied."
            ],
            use_cases=[
                UseCase(
                    "UC-01",
                    "Capture or upload media",
                    "User",
                    "Capture or upload media",
                    "The media is submitted",
                ),
                UseCase(
                    "UC-02",
                    "Analyze a photo",
                    "User",
                    "Analyze the pet photo",
                    "Review the emotion result",
                ),
            ],
            ui_title="Search results",
            ui_summary="search result list",
            validation_title="Permission recovery",
            validation_summary="permission denied recovery",
        )
        result_value = self.builder.build(context, RetrievalGuidanceBuilder().build(context))
        media = next(
            item for item in result_value.page_spec.components if item.component_type == "media_input"
        )
        context_decisions = [
            item
            for item in result_value.fallback
            if item.source_kind == "agent_context" and "media_input" in item.rule
        ]
        self.assertTrue(context_decisions)
        self.assertIn(media.component_id, {field.entity_id for field in context_decisions[0].affected_fields})
        self.assertFalse(
            any(item.role == "ui_reference" and "media_input" in item.rule for item in result_value.adopted)
        )

    def test_decisions_are_complete_and_auditable(self) -> None:
        context = build_context(
            ui_title="Map location",
            ui_summary="location picker",
            validation_title="Permission retry",
            validation_summary="permission denied retry",
        )
        guidance = RetrievalGuidanceBuilder().build(context)
        result_value = self.builder.build(context, guidance)
        self.assertTrue(result_value.adopted)
        self.assertTrue(result_value.ignored)
        self.assertTrue(result_value.fallback)
        for item in result_value.adopted:
            self.assertTrue(item.guidance_id)
            self.assertTrue(item.doc_id)
            self.assertTrue(item.rule)
            self.assertTrue(item.affected_fields)
        self.assertTrue(all(item.reason for item in result_value.ignored))
        result_value.validate(guidance)

    def test_builder_does_not_read_files_or_call_retriever(self) -> None:
        with mock.patch("builtins.open", side_effect=AssertionError("file access forbidden")):
            result_value = self.builder.build(self.context, self.guidance)
        self.assertTrue(result_value.page_spec.components)

    def test_existing_renderer_consumes_guided_page_spec_unchanged(self) -> None:
        result_value = self.builder.build(self.context, self.guidance)
        root = ROOT / "tests" / ".tmp_guided_renderer" / self._testMethodName
        if root.exists():
            shutil.rmtree(root)
        try:
            rendered = DeterministicPageRenderer().render(result_value.page_spec, root / "page")
            self.assertTrue(rendered.index_html.exists())
            self.assertTrue(rendered.app_js.exists())
        finally:
            if root.exists():
                shutil.rmtree(root)
            if root.parent.exists():
                root.parent.rmdir()


if __name__ == "__main__":
    import unittest

    unittest.main()
