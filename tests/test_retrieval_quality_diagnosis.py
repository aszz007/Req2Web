from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest import TestCase


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
SPEC = importlib.util.spec_from_file_location("diagnose_retrieval_quality", ROOT / "scripts" / "diagnose_retrieval_quality.py")
assert SPEC and SPEC.loader
diagnosis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnosis)


@unittest.skipUnless(
    (diagnosis.framework_evidence_root(ROOT) / "demo_regression").is_dir(),
    "local deterministic regression evidence is unavailable",
)
class RetrievalQualityDiagnosisTest(TestCase):
    def setUp(self) -> None:
        self.fixture = ROOT / "fixtures" / "demo_v2_regression_cases_v1.json"
        self.packages = diagnosis.framework_evidence_root(ROOT) / "demo_regression"
        self.index = ROOT / "data" / "processed" / "rag"
        self.temp = ROOT / "tests" / ".tmp_retrieval_quality_diagnosis"
        shutil.rmtree(self.temp, ignore_errors=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _report(self) -> dict:
        return diagnosis.generate_diagnosis(fixture=self.fixture, package_root=self.packages, index_dir=self.index)

    def test_schema_and_exact_target_units(self) -> None:
        report = self._report()
        self.assertEqual(report["schema_version"], diagnosis.SCHEMA_VERSION)
        self.assertEqual(len(report["units"]), 15)
        self.assertEqual(sum(item["role"] == "validation" for item in report["units"]), 11)
        self.assertEqual(sum(item["role"] == "ui_reference" for item in report["units"]), 4)
        self.assertTrue(all(item["ablation"]["difference_count"] == 0 for item in report["units"]))

    def test_repeated_machine_reports_are_byte_identical(self) -> None:
        report = self._report()
        first = self.temp / "first"; second = self.temp / "second"
        diagnosis.write_diagnosis(report, first)
        diagnosis.write_diagnosis(self._report(), second)
        for name in ("diagnosis.json", "target_units.csv"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
        self.assertEqual(hashlib.sha256((first / "diagnosis.json").read_bytes()).hexdigest(), hashlib.sha256((second / "diagnosis.json").read_bytes()).hexdigest())

    def test_invalid_package_root_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            diagnosis.generate_diagnosis(fixture=self.fixture, package_root=self.temp / "missing", index_dir=self.index)

    def test_output_is_valid_json_and_csv(self) -> None:
        output = self.temp / "report"
        diagnosis.write_diagnosis(self._report(), output)
        payload = json.loads((output / "diagnosis.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["scope"]["target_unit_count"], 15)
        self.assertEqual((output / "target_units.csv").read_text(encoding="utf-8").count("\n"), 16)


if __name__ == "__main__":
    import unittest
    unittest.main()
