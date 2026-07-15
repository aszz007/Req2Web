from __future__ import annotations

from copy import deepcopy
import json
import sys
from pathlib import Path
from unittest import TestCase, mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import (
    RETRIEVAL_GUIDANCE_SCHEMA_VERSION,
    RetrievalGuidanceBuilder,
)  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402


def result(role: str, doc_id: str, title: str, summary: str, references: list[dict[str, str]]) -> dict[str, object]:
    return {
        "score": 1.0,
        "doc_id": doc_id,
        "role": role,
        "dataset": "fixture_dataset",
        "subset": "fixture_subset",
        "sample_id": doc_id.rsplit(":", 1)[-1],
        "title": title,
        "summary": summary,
        "references": references,
    }


def build_context() -> AgentContextBundle:
    results = {
        "requirement": [
            result("requirement", "requirement:fixture:ecommerce", "Ecommerce search", "filter cart checkout workflow", [{"kind": "workflow", "uri": "fixtures/requirement/workflow.json"}])
        ],
        "ui_reference": [
            result("ui_reference", "ui_reference:fixture:search", "搜索筛选结果", "商品搜索结果与筛选", [{"kind": "screenshot", "uri": "fixtures/ui/search.png"}])
        ],
        "interaction_flow": [
            result("interaction_flow", "interaction_flow:fixture:mixed", "mixed_short flow", "click and swipe flow", [{"kind": "step_screenshot", "uri": "fixtures/flow/01.png"}, {"kind": "step_screenshot", "uri": "fixtures/flow/02.png"}])
        ],
        "implementation": [
            result("implementation", "implementation:fixture:responsive", "Responsive cart checkout", "responsive cart structure", [{"kind": "html", "uri": "fixtures/implementation/cart.html"}, {"kind": "screenshot", "uri": "fixtures/implementation/cart.png"}])
        ],
        "validation": [
            result("validation", "validation:fixture:retry", "Input error retry", "input error permits retry", [{"kind": "issue", "uri": "https://example.test/issues/1"}])
        ],
    }
    return AgentContextBundle(
        original_requirement="做一个可搜索和结算的移动电商页面。",
        requirement_summary="移动电商搜索与结算。",
        target_device="mobile",
        task_type="ecommerce",
        constraints=["输入错误时允许修改并重试"],
        use_cases=[
            UseCase("UC-01", "搜索商品", "用户", "搜索商品", "查看结果"),
            UseCase("UC-02", "结算商品", "用户", "完成结算", "获得确认"),
        ],
        retrieval_queries={role: f"fixture {role}" for role in ROLE_ORDER},
        retrieval_results=results,
    )


class RetrievalGuidanceTest(TestCase):
    def setUp(self) -> None:
        self.context = build_context()
        self.builder = RetrievalGuidanceBuilder()
        self.guidance = self.builder.build(self.context)

    def test_schema_and_five_role_guidance_are_traceable(self) -> None:
        payload = self.guidance.to_dict()
        self.assertEqual(payload["schema_version"], RETRIEVAL_GUIDANCE_SCHEMA_VERSION)
        groups = {
            "requirement": self.guidance.requirement_guidance,
            "ui_reference": self.guidance.ui_guidance,
            "interaction_flow": self.guidance.interaction_guidance,
            "implementation": self.guidance.implementation_guidance,
            "validation": self.guidance.validation_guidance,
        }
        context_doc_ids = {
            item["doc_id"]
            for role in ROLE_ORDER
            for item in self.context.retrieval_results[role]
        }
        for role, items in groups.items():
            self.assertTrue(items)
            self.assertTrue(all(item.source.role == role for item in items))
            self.assertTrue(all(item.source.doc_id in context_doc_ids for item in items))
            self.assertTrue(all(item.source.source_fields for item in items))
            self.assertTrue(all(item.source.extraction_rule for item in items))
        self.assertEqual({trace.use_case_id for trace in self.guidance.use_case_traces}, {"UC-01", "UC-02"})
        self.assertIn("search_input", {item.value for item in self.guidance.ui_guidance})
        self.assertIn("tap_and_swipe", {item.value for item in self.guidance.interaction_guidance})

    def test_same_input_is_byte_for_byte_deterministic(self) -> None:
        first = json.dumps(self.builder.build(self.context).to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        second = json.dumps(self.builder.build(self.context).to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(first, second)

    def test_replacing_ui_evidence_changes_actual_layout_guidance(self) -> None:
        changed = deepcopy(self.context)
        changed.retrieval_results["ui_reference"] = [
            result("ui_reference", "ui_reference:fixture:map", "地图地址位置", "选择位置并确认地址", [{"kind": "screenshot", "uri": "fixtures/ui/map.png"}])
        ]
        altered = self.builder.build(changed)
        original_values = {(item.category, item.value) for item in self.guidance.ui_guidance}
        altered_values = {(item.category, item.value) for item in altered.ui_guidance}
        self.assertNotEqual(original_values, altered_values)
        self.assertIn(("layout_hint", "location_picker"), altered_values)
        self.assertNotIn(("layout_hint", "location_picker"), original_values)

    def test_unretrieved_text_does_not_become_guidance(self) -> None:
        changed = deepcopy(self.context)
        changed.retrieval_results["validation"] = [
            result("validation", "validation:fixture:plain", "Plain case", "unsupported secret field must not become guidance", [])
        ]
        guidance = self.builder.build(changed)
        values = {item.value for item in guidance.validation_guidance}
        self.assertEqual(values, {"regression_case"})
        self.assertFalse(any("secret" in item.value for item in guidance.validation_guidance))

    def test_missing_or_damaged_role_fails_clearly(self) -> None:
        for role in ROLE_ORDER:
            missing = deepcopy(self.context)
            missing.retrieval_results[role] = []
            with self.subTest(role=role):
                with self.assertRaisesRegex(ValueError, role):
                    self.builder.build(missing)

        damaged = deepcopy(self.context)
        damaged.retrieval_results["implementation"][0]["title"] = ""
        with self.assertRaisesRegex(ValueError, "title"):
            self.builder.build(damaged)

    def test_absolute_output_uri_is_rejected(self) -> None:
        changed = deepcopy(self.context)
        changed.retrieval_results["ui_reference"][0]["references"] = [
            {"kind": "screenshot", "uri": "C:/private/screen.png"}
        ]
        with self.assertRaisesRegex(ValueError, "absolute paths"):
            self.builder.build(changed)

    def test_builder_does_not_open_files_or_call_services(self) -> None:
        with mock.patch("builtins.open", side_effect=AssertionError("file access is forbidden")):
            guidance = self.builder.build(self.context)
        self.assertTrue(guidance.ui_guidance)

    def test_invalid_guidance_trace_is_rejected(self) -> None:
        self.guidance.use_case_traces[0].guidance_ids = ["guidance-missing"]
        with self.assertRaisesRegex(ValueError, "unknown guidance"):
            self.guidance.validate()


if __name__ == "__main__":
    import unittest

    unittest.main()
