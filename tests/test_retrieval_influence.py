from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
from unittest import TestCase


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from test_guided_page_spec import build_context  # noqa: E402
from req2web_agent import UseCase  # noqa: E402


class RetrievalInfluenceTest(TestCase):
    def setUp(self) -> None:
        self.tmp_root = ROOT / "tests" / ".tmp_retrieval_influence"
        self.root = self.tmp_root / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)

    def tearDown(self) -> None:
        # The fixed-case subtests deliberately replace ``self.root``.  Clean
        # the shared test directory rather than only the final subtest path.
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _report(self, context):
        guidance = RetrievalGuidanceBuilder().build(context)
        builder = RetrievalGuidedPageSpecBuilder()
        full = builder.build(context, guidance)
        ablations = {role: builder.build(context, guidance, disabled_roles=(role,)) for role in ROLE_ORDER}
        rendered = DeterministicPageRenderer().render(full.page_spec, self.root / "rendered")
        report = RetrievalInfluenceChecker().check(
            context, guidance, PageSpecBuilder().build(context), full, ablations, rendered
        )
        return guidance, full, ablations, rendered, report

    def test_is_byte_deterministic_and_has_sha_safe_payload(self) -> None:
        first = self._report(build_context())[-1]
        shutil.rmtree(self.root)
        second = self._report(build_context())[-1]
        encode = lambda value: json.dumps(value.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(encode(first), encode(second))
        self.assertTrue(first.report_id.startswith("retrieval-influence-"))
        self.assertNotIn(str(ROOT).replace("\\", "/"), encode(first).decode("utf-8").replace("\\", "/"))

    def test_four_fixed_case_shapes_pass(self) -> None:
        cases = {
            "ecommerce": build_context(constraints=["输入错误时允许修改并重试"]),
            "pet-recognition": build_context(
                requirement="做一个宠物情绪识别 App，用户拍照或上传照片后查看分析结果。",
                summary="宠物图片输入和情绪分析。",
                task_type="recognition_tool",
                constraints=["相机权限被拒绝时应提供恢复方式"],
                ui_title="搜索结果", ui_summary="search result list",
            ),
            "map-address-search": build_context(
                requirement="做一个移动端地址搜索页面，用户搜索地点、选择并确认位置。",
                summary="地址搜索和位置确认。", task_type="location_service",
                constraints=["定位不可用时应允许手动选择地址"],
                ui_title="地图地址位置", ui_summary="location picker",
            ),
            "desktop-dashboard": build_context(
                requirement="做一个桌面数据看板，展示关键指标、列表筛选和详情；没有数据时显示空状态。",
                summary="桌面指标、列表和空状态。", device="desktop", task_type="dashboard",
                constraints=["没有匹配数据时应显示空状态并允许清除筛选"],
                ui_title="指标看板空状态", ui_summary="dashboard metric empty no data",
                use_cases=[
                    UseCase("UC-01", "查看关键指标", "用户", "查看关键指标", "理解指标"),
                    UseCase("UC-02", "筛选列表", "用户", "筛选列表并查看详情", "理解当前状态"),
                ],
            ),
        }
        for name, context in cases.items():
            with self.subTest(name=name):
                self.root = ROOT / "tests" / ".tmp_retrieval_influence" / name
                report = self._report(context)[-1]
                self.assertTrue(report.passed)
                self.assertEqual([item.role for item in report.ablations], list(ROLE_ORDER))

    def test_reports_real_and_not_applicable_role_outcomes(self) -> None:
        report = self._report(build_context())[-1]
        outcomes = {item.role: item.outcome for item in report.ablations}
        self.assertIn("role_has_influence", outcomes.values())
        self.assertEqual(outcomes["validation"], "guidance_not_applicable_or_ignored")
        self.assertGreater(report.decision_status_counts["ignored"], 0)

    def test_rejects_fabricated_affected_field_and_missing_decision(self) -> None:
        context = build_context()
        guidance, full, ablations, rendered, _ = self._report(context)
        fabricated = deepcopy(full)
        affected = fabricated.adopted[0].affected_fields[0]
        fabricated.adopted[0].affected_fields[0] = type(affected)(affected.entity_id, "not_a_real_change")
        with self.assertRaisesRegex(ValueError, "deterministic payload"):
            RetrievalInfluenceChecker().check(context, guidance, PageSpecBuilder().build(context), fabricated, ablations, rendered)
        omitted = deepcopy(full)
        omitted.adopted.pop()
        with self.assertRaises(ValueError):
            RetrievalInfluenceChecker().check(context, guidance, PageSpecBuilder().build(context), omitted, ablations, rendered)

    def test_rejects_tampered_guidance_identity_and_ablation(self) -> None:
        context = build_context()
        guidance, full, ablations, rendered, _ = self._report(context)
        bad_guidance = deepcopy(guidance)
        bad_guidance.target_device = "desktop"
        with self.assertRaisesRegex(ValueError, "target_device"):
            RetrievalInfluenceChecker().check(context, bad_guidance, PageSpecBuilder().build(context), full, ablations, rendered)
        bad_ablations = dict(ablations)
        bad_ablations["ui_reference"] = full
        with self.assertRaisesRegex(ValueError, "controlled result"):
            RetrievalInfluenceChecker().check(context, guidance, PageSpecBuilder().build(context), full, bad_ablations, rendered)

    def test_renderer_and_consistency_failure_are_a_report_gate(self) -> None:
        context = build_context()
        guidance, full, ablations, rendered, _ = self._report(context)
        rendered.app_js.write_text("const PAGE_DATA = Object.freeze({});", encoding="utf-8")
        report = RetrievalInfluenceChecker().check(context, guidance, PageSpecBuilder().build(context), full, ablations, rendered)
        self.assertFalse(report.passed)
        self.assertFalse(report.renderer_consistency["passed"])


if __name__ == "__main__":
    import unittest
    unittest.main()
