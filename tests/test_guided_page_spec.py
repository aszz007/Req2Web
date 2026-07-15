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
    requirement: str = "做一个移动端搜索、筛选商品并查看结果的页面。",
    summary: str = "移动端商品搜索与结果。",
    device: str = "mobile",
    task_type: str = "catalog",
    constraints: list[str] | None = None,
    use_cases: list[UseCase] | None = None,
    ui_title: str = "搜索筛选结果",
    ui_summary: str = "搜索筛选和结果列表",
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
            UseCase("UC-01", "搜索与筛选", "用户", "搜索并筛选商品", "查看结果列表"),
            UseCase("UC-02", "查看详情", "用户", "选择并查看详情", "理解详情信息"),
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
            requirement="做一个移动端地点搜索页面，可搜索地址并选择位置。",
            summary="地点搜索和位置选择。",
            task_type="location_service",
            use_cases=[
                UseCase("UC-01", "搜索地址", "用户", "搜索地址", "查看地点结果"),
                UseCase("UC-02", "选择位置", "用户", "选择并确认位置", "得到确认位置"),
            ],
        )
        search_result = self.builder.build(context, RetrievalGuidanceBuilder().build(context))
        changed = deepcopy(context)
        changed.retrieval_results["ui_reference"] = [
            result(
                "ui_reference",
                "ui_reference:fixture:guided:map",
                "地图地址位置",
                "选择位置并确认地址",
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
        self.assertIn("滑动浏览", swipe.page_spec.interactions[0].action)

    def test_tap_and_single_step_guidance_change_interaction_expression(self) -> None:
        context = build_context(
            requirement="做一个详情页，用户查看详情后保存配置。",
            summary="详情与配置。",
            use_cases=[
                UseCase("UC-01", "查看详情", "用户", "查看详情", "理解详情"),
                UseCase("UC-02", "保存配置", "用户", "保存配置", "配置已保存"),
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
        self.assertIn("点击操作", actions)
        self.assertIn("单步状态转换", actions)

    def test_metric_and_empty_ui_guidance_change_components_and_layout(self) -> None:
        context = build_context(
            requirement="做一个桌面数据看板，展示关键指标；没有数据时显示空状态。",
            summary="指标看板和空状态。",
            device="desktop",
            task_type="dashboard",
            constraints=["没有数据时显示空状态"],
            use_cases=[
                UseCase("UC-01", "查看关键指标", "用户", "查看关键指标", "理解指标"),
                UseCase("UC-02", "查看状态", "用户", "查看状态", "理解当前状态"),
            ],
            ui_title="指标看板空状态",
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
            requirement="做一个桌面数据看板，支持列表筛选；没有匹配数据时显示空状态。",
            summary="桌面指标、列表筛选和空状态。",
            device="desktop",
            task_type="dashboard",
            constraints=["没有匹配数据时显示空状态并允许清除筛选"],
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
            constraints=["输入错误时允许修改并重试"],
            validation_title="Input error retry",
            validation_summary="input error permits retry",
        )
        retry_result = self.builder.build(
            retry_context, RetrievalGuidanceBuilder().build(retry_context)
        )
        self.assertTrue(
            any("输入错误重试边界" in item.description for item in retry_result.page_spec.acceptance_checks)
        )

    def test_explicit_location_and_empty_recovery_stay_context_sourced(self) -> None:
        map_context = build_context(
            requirement="做一个移动端地址搜索页面，用户可以搜索地点、选择结果并确认位置。",
            summary="地址搜索和位置确认。",
            task_type="location_service",
            constraints=["定位不可用时应允许手动选择地址"],
            use_cases=[
                UseCase("UC-01", "搜索地址", "用户", "搜索地址", "查看候选地点"),
                UseCase("UC-02", "选择位置", "用户", "选择并确认位置", "位置已确认"),
            ],
            ui_title="地图地址位置",
            ui_summary="location picker",
        )
        map_result = self.builder.build(
            map_context, RetrievalGuidanceBuilder().build(map_context)
        )
        self.assertTrue(
            any(item.label == "手动选择地址" for item in map_result.page_spec.components)
        )
        self.assertTrue(
            any(
                item.source_kind == "agent_context" and "location-manual" in item.rule
                for item in map_result.fallback
            )
        )

        dashboard_context = build_context(
            requirement="做一个桌面数据看板，展示关键指标、列表筛选和详情。",
            summary="桌面数据看板。",
            device="desktop",
            task_type="dashboard",
            constraints=["没有匹配数据时应显示空状态并允许清除筛选"],
            ui_title="空状态列表筛选",
            ui_summary="empty list filter",
        )
        dashboard_result = self.builder.build(
            dashboard_context, RetrievalGuidanceBuilder().build(dashboard_context)
        )
        self.assertEqual(dashboard_result.page_spec.layout.pattern, "guided_dashboard_flow")
        self.assertTrue(
            any(item.label == "清除筛选" for item in dashboard_result.page_spec.components)
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
                self.assertIn("检索实现参考", constraints[affected.entity_id].description)

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
        self.assertNotIn("权限拒绝恢复边界", descriptions)
        self.assertNotIn("输入错误重试边界", descriptions)
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
            requirement="做一个设置页，用户填写名称并保存配置。",
            summary="填写并保存设置。",
            use_cases=[
                UseCase("UC-01", "填写名称", "用户", "填写名称", "名称已记录"),
                UseCase("UC-02", "保存配置", "用户", "保存配置", "配置已保存"),
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
        self.assertFalse(any("滑动浏览" in item.action for item in result_value.page_spec.interactions))

    def test_weak_pet_ui_keeps_media_input_sourced_from_context(self) -> None:
        context = build_context(
            requirement="做一个宠物情绪识别 App，用户拍照或上传照片后查看分析结果。",
            summary="宠物图片输入和情绪分析。",
            task_type="recognition_tool",
            constraints=["相机权限被拒绝时应提供恢复方式"],
            use_cases=[
                UseCase("UC-01", "拍摄或上传素材", "用户", "拍摄或上传素材", "素材已提交"),
                UseCase("UC-02", "分析照片", "用户", "分析宠物照片", "查看情绪结果"),
            ],
            ui_title="搜索结果",
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
            ui_title="地图位置",
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
