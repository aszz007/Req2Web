from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
from unittest import TestCase


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_generation import (  # noqa: E402
    DemoV2RegressionRunner,
    RegressionCaseSet,
    RegressionSuiteValidator,
)
from req2web_generation.result_package_v2 import RetrievalEnhancedResultPackage  # noqa: E402


class DemoV2RegressionTest(TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_demo_v2_regression"
        shutil.rmtree(self.root, ignore_errors=True)
        self.fixture = ROOT / "fixtures" / "demo_v2_regression_cases_v1.json"
        self.case_set = RegressionCaseSet.load(self.fixture)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _build(self, name: str) -> Path:
        output = self.root / name
        DemoV2RegressionRunner(index_dir=ROOT / "data" / "processed" / "rag").run(self.case_set, output)
        return output

    @staticmethod
    def _rewrite_declared(package_dir: Path, relative: str, payload: dict) -> None:
        path = package_dir / relative
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest_path = package_dir / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw = path.read_bytes()
        for item in manifest["files"]:
            if item["path"] == relative:
                item["size"] = len(raw)
                item["sha256"] = hashlib.sha256(raw).hexdigest()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_fixture_is_sorted_unique_and_reuses_the_legacy_inputs(self) -> None:
        self.assertEqual(len(self.case_set.cases), 12)
        self.assertEqual([item.case_id for item in self.case_set.cases], sorted(item.case_id for item in self.case_set.cases))
        legacy = {item.case_id: item for item in self.case_set.cases}
        self.assertEqual(legacy["ecommerce"].requirement, "做一个带搜索、筛选、购物车和结算的移动电商页面。")
        self.assertEqual(legacy["pet-recognition"].constraints, ("相机权限被拒绝时应提供恢复方式",))
        self.assertEqual(legacy["map-address-search"].constraints, ("定位不可用时应允许手动选择地址",))
        self.assertEqual(legacy["desktop-dashboard"].task_type, None)
        new_cases = [item for item in self.case_set.cases if "legacy-fixed" not in item.coverage_tags]
        self.assertGreaterEqual(sum(bool(item.constraints) for item in new_cases), 4)

    def test_batch_build_has_twelve_valid_packages_and_complete_matrix(self) -> None:
        output = self._build("batch")
        report = json.loads((output / "aggregate_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["case_count"], 12)
        self.assertTrue(report["aggregate"]["all_consistency_passed"])
        self.assertTrue(report["aggregate"]["all_retrieval_influence_passed"])
        self.assertEqual(len(list((output / "packages").iterdir())), 12)
        matrix = (output / "aggregate_matrix.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(matrix), 61)
        self.assertEqual(matrix[0].split(","), ["case_id", "role", "retrieval_count", "guidance_count", "adopted", "ignored", "fallback", "outcome", "difference_count", "influence_class"])
        for case in self.case_set.cases:
            package_dir = output / "packages" / case.case_id
            manifest = json.loads((package_dir / "package_manifest.json").read_text(encoding="utf-8"))
            RetrievalEnhancedResultPackage(manifest["package_id"], manifest["page_id"], package_dir).validate()
        RegressionSuiteValidator().validate(self.case_set, output)

    def test_aggregate_contains_real_influence_and_not_applicable_outcomes(self) -> None:
        report = json.loads((self._build("outcomes") / "aggregate_report.json").read_text(encoding="utf-8"))
        outcomes = report["aggregate"]["role_outcomes"]
        self.assertGreater(outcomes["role_has_influence"], 0)
        self.assertGreater(outcomes["guidance_not_applicable_or_ignored"], 0)
        self.assertEqual(outcomes["verification_failed"], 0)
        validation = next(item for item in report["aggregate"]["role_totals"] if item["role"] == "validation")
        self.assertGreater(validation["not_applicable_or_ignored"], validation["has_influence"])

    def test_missing_package_and_tampered_gate_or_statistics_are_rejected(self) -> None:
        output = self._build("tamper")
        package_dir = output / "packages" / "ecommerce"
        consistency = json.loads((package_dir / "internal" / "consistency_report.json").read_text(encoding="utf-8"))
        consistency["passed"] = False
        self._rewrite_declared(package_dir, "internal/consistency_report.json", consistency)
        with self.assertRaises(ValueError):
            RegressionSuiteValidator().validate(self.case_set, output)
        output = self._build("missing")
        shutil.rmtree(output / "packages" / "mobile-auth")
        with self.assertRaises(ValueError):
            RegressionSuiteValidator().validate(self.case_set, output)
        output = self._build("statistics")
        aggregate_manifest_path = output / "aggregate_manifest.json"
        aggregate_manifest = json.loads(aggregate_manifest_path.read_text(encoding="utf-8"))
        original_hash = aggregate_manifest["files"][0]["sha256"]
        aggregate_manifest["files"][0]["sha256"] = "0" * 64
        aggregate_manifest_path.write_text(json.dumps(aggregate_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest"):
            RegressionSuiteValidator().validate(self.case_set, output)
        aggregate_manifest["files"][0]["sha256"] = original_hash
        aggregate_manifest_path.write_text(json.dumps(aggregate_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report_path = output / "aggregate_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")); report["case_count"] = 11
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            RegressionSuiteValidator().validate(self.case_set, output)

    def test_absolute_path_leak_and_cross_directory_builds_are_rejected_or_identical(self) -> None:
        first = self._build("first")
        second = self._build("second")
        for path in sorted(item.relative_to(first) for item in first.rglob("*") if item.is_file()):
            self.assertEqual((first / path).read_bytes(), (second / path).read_bytes(), path.as_posix())
        package_dir = first / "packages" / "mobile-auth"
        summary = json.loads((package_dir / "result_summary.json").read_text(encoding="utf-8"))
        summary["text_description"] = "D:\\leaked\\workspace"
        self._rewrite_declared(package_dir, "result_summary.json", summary)
        manifest = json.loads((package_dir / "package_manifest.json").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            RetrievalEnhancedResultPackage(manifest["package_id"], manifest["page_id"], package_dir).validate()


if __name__ == "__main__":
    import unittest
    unittest.main()
