from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_agent import UseCase  # noqa: E402
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalEnhancedResultPackageError,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from test_guided_page_spec import build_context  # noqa: E402


class ResultPackageV2Test(TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_result_package_v2" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.packager = DeterministicRetrievalEnhancedResultPackager()

    def tearDown(self) -> None:
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def _artifacts(self, context=None, name="build"):
        context = context or build_context()
        guidance = RetrievalGuidanceBuilder().build(context)
        baseline = PageSpecBuilder().build(context)
        guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
        ablations = {role: RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,)) for role in ROLE_ORDER}
        render = DeterministicPageRenderer().render(guided.page_spec, self.root / name / "render")
        consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
        influence = RetrievalInfluenceChecker().check(context, guidance, baseline, guided, ablations, render)
        return context, guidance, guided, render, consistency, influence

    def _package(self, context=None, name="package"):
        artifacts = self._artifacts(context, name)
        result = self.packager.package(*artifacts[:3], artifacts[2].page_spec, *artifacts[3:], self.root / name / "package")
        result.validate()
        return result, artifacts

    def _json(self, package, relative):
        return json.loads((package.package_dir / relative).read_text(encoding="utf-8"))

    def test_normal_v2_package_has_fixed_structure_and_double_gate(self) -> None:
        package, _ = self._package()
        paths = sorted(path.relative_to(package.package_dir).as_posix() for path in package.package_dir.rglob("*") if path.is_file())
        self.assertEqual(paths, sorted((
            "page/index.html", "page/styles.css", "page/app.js", "page/render_manifest.json",
            "internal/agent_context.json", "internal/retrieval_guidance.json", "internal/guided_page_spec_build_result.json", "internal/page_spec.json", "internal/consistency_report.json", "internal/retrieval_influence_report.json", "result_summary.json", "package_manifest.json",
        )))
        summary = self._json(package, "result_summary.json")
        self.assertTrue(summary["quality_gate"]["passed"])
        self.assertTrue(summary["quality_gate"]["consistency"]["passed"])
        self.assertTrue(summary["quality_gate"]["retrieval_influence"]["passed"])

    def test_four_fixed_case_shapes_pass(self) -> None:
        cases = {
            "ecommerce": build_context(constraints=["输入错误时允许修改并重试"]),
            "pet": build_context(requirement="做一个宠物情绪识别 App，用户拍照或上传照片后查看分析结果。", summary="宠物图片输入和情绪分析。", task_type="recognition_tool", constraints=["相机权限被拒绝时应提供恢复方式"], ui_title="搜索结果", ui_summary="search result list"),
            "map": build_context(requirement="做一个移动端地址搜索页面，用户搜索地点、选择并确认位置。", summary="地址搜索和位置确认。", task_type="location_service", constraints=["定位不可用时应允许手动选择地址"], ui_title="地图地址位置", ui_summary="location picker"),
            "dashboard": build_context(requirement="做一个桌面数据看板，展示关键指标、列表筛选和详情；没有数据时显示空状态。", summary="桌面指标、列表和空状态。", device="desktop", task_type="dashboard", constraints=["没有匹配数据时应显示空状态并允许清除筛选"], ui_title="指标看板空状态", ui_summary="dashboard metric empty no data", use_cases=[UseCase("UC-01", "查看关键指标", "用户", "查看关键指标", "理解指标"), UseCase("UC-02", "筛选列表", "用户", "筛选列表并查看详情", "理解当前状态")]),
        }
        for name, context in cases.items():
            with self.subTest(name=name):
                package, artifacts = self._package(context, name)
                self.assertTrue(artifacts[-1].passed)
                package.validate()

    def test_cross_directory_builds_are_byte_identical(self) -> None:
        first, _ = self._package(name="first")
        second, _ = self._package(name="second")
        self.assertEqual(first.package_id, second.package_id)
        files = sorted(path.relative_to(first.package_dir).as_posix() for path in first.package_dir.rglob("*") if path.is_file())
        for path in files:
            self.assertEqual((first.package_dir / path).read_bytes(), (second.package_dir / path).read_bytes(), path)
        manifest_hash = hashlib.sha256((first.package_dir / "package_manifest.json").read_bytes()).hexdigest()
        self.assertEqual(manifest_hash, hashlib.sha256((second.package_dir / "package_manifest.json").read_bytes()).hexdigest())

    def test_summary_marks_interactions_as_stable_list_not_strict_path(self) -> None:
        package, _ = self._package()
        summary = self._json(package, "result_summary.json")
        self.assertIn("stable_interaction_list", summary["interaction_flow_semantics"])
        self.assertIn("not a strict", summary["interaction_flow_semantics"])
        self.assertEqual([item["step"] for item in summary["interaction_flow"]], list(range(1, len(summary["interaction_flow"]) + 1)))

    def test_guidance_guided_page_and_report_mismatches_are_rejected(self) -> None:
        context, guidance, guided, render, consistency, influence = self._artifacts()
        bad_guidance = deepcopy(guidance); bad_guidance.target_device = "desktop"
        with self.assertRaises(ValueError):
            self.packager.package(context, bad_guidance, guided, guided.page_spec, render, consistency, influence, self.root / "bad-guidance")
        other = self._artifacts(build_context(requirement="另一个页面"), "other")[1]
        with self.assertRaisesRegex(ValueError, "wrong guidance bundle"):
            self.packager.package(context, other, guided, guided.page_spec, render, consistency, influence, self.root / "bad-guided")
        bad_influence = deepcopy(influence); bad_influence.guidance_bundle_id = "guidance-wrong"
        with self.assertRaises(ValueError):
            self.packager.package(context, guidance, guided, guided.page_spec, render, consistency, bad_influence, self.root / "bad-report")

    def test_failed_consistency_or_influence_is_rejected(self) -> None:
        context, guidance, guided, render, consistency, influence = self._artifacts()
        consistency.passed = False
        with self.assertRaises(ValueError):
            self.packager.package(context, guidance, guided, guided.page_spec, render, consistency, influence, self.root / "bad-consistency")
        _, _, guided, render, consistency, influence = self._artifacts(name="influence")
        influence.passed = False
        with self.assertRaises(ValueError):
            self.packager.package(context, guidance, guided, guided.page_spec, render, consistency, influence, self.root / "bad-influence")

    def test_static_and_internal_json_tampering_is_detected(self) -> None:
        package, _ = self._package()
        (package.package_dir / "page/app.js").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "size|SHA-256"):
            package.validate()
        package, _ = self._package(name="internal")
        path = package.package_dir / "internal/retrieval_guidance.json"
        payload = json.loads(path.read_text(encoding="utf-8")); payload["guidance_bundle_id"] = "tampered"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = self._json(package, "package_manifest.json")
        for item in manifest["files"]:
            if item["path"] == "internal/retrieval_guidance.json":
                raw = path.read_bytes(); item["size"] = len(raw); item["sha256"] = hashlib.sha256(raw).hexdigest()
        (package.package_dir / "package_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(RetrievalEnhancedResultPackageError, "guided build-result"):
            package.validate()

    def test_manifest_file_set_and_path_errors_are_detected(self) -> None:
        package, _ = self._package()
        manifest = self._json(package, "package_manifest.json")
        manifest["files"][0]["path"] = "../escape.html"
        (package.package_dir / "package_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            package.validate()

    def test_nonempty_destination_and_gate_failure_leave_no_manifest(self) -> None:
        context, guidance, guided, render, consistency, influence = self._artifacts()
        occupied = self.root / "occupied"; occupied.mkdir(); sentinel = occupied / "keep.txt"; sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(RetrievalEnhancedResultPackageError, "refusing to overwrite"):
            self.packager.package(context, guidance, guided, guided.page_spec, render, consistency, influence, occupied)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        consistency.passed = False
        failed = self.root / "failed"
        with self.assertRaises(ValueError):
            self.packager.package(context, guidance, guided, guided.page_spec, render, consistency, influence, failed)
        self.assertFalse((failed / "package_manifest.json").exists())
        self.assertFalse(list(self.root.glob(".failed.staging-*")))

    def test_atomic_publish_failure_leaves_no_complete_package(self) -> None:
        context, guidance, guided, render, consistency, influence = self._artifacts()
        destination = self.root / "replace-failed"
        with patch("req2web_generation.result_package_v2.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.packager.package(context, guidance, guided, guided.page_spec, render, consistency, influence, destination)
        self.assertFalse((destination / "package_manifest.json").exists())
        self.assertFalse(list(self.root.glob(".replace-failed.staging-*")))


if __name__ == "__main__":
    import unittest
    unittest.main()
