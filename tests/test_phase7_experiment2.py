from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("phase7_experiment2", ROOT / "scripts" / "phase7_experiment2.py")
assert SPEC and SPEC.loader
experiment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = experiment
SPEC.loader.exec_module(experiment)


class Phase7Experiment2Test(unittest.TestCase):
    def test_fixture_has_six_distinct_requirements_and_exact_case_ids(self) -> None:
        cases = experiment.load_cases(ROOT / "fixtures" / "phase7_experiment2_cases_v1.json")
        self.assertEqual(tuple(case["case_id"] for case in cases), experiment.EXPECTED_CASE_IDS)
        self.assertEqual(len({case["requirement"] for case in cases}), 6)

    def test_matrix_contract_has_four_supported_single_delta_kinds(self) -> None:
        self.assertEqual(len(experiment.MUTATIONS), 4)
        self.assertEqual(len({item[0] for item in experiment.MUTATIONS}), 4)
        self.assertEqual({item[1] for item in experiment.MUTATIONS}, {"page_spec", "guidance/adoption", "render_binding", "package"})

    def test_summary_preserves_false_alarms_and_failed_localization(self) -> None:
        rows = self._rows()
        rows[0]["status"] = "fault_detected"
        rows[6]["detected"] = False
        rows[6]["exact_localization"] = False
        summary = experiment.summarize(rows)
        self.assertEqual(summary["clean_control_false_alarms"], 1)
        self.assertEqual(summary["mutant_detected"], 23)
        self.assertEqual(summary["mutant_exact_localization"], 23)

    def test_summary_uses_fixed_raw_denominators(self) -> None:
        summary = experiment.summarize(self._rows())
        self.assertEqual((summary["clean_control_total"], summary["mutant_total"], summary["observation_total"]), (6, 24, 30))
        self.assertTrue(all(value["total"] == 6 for value in summary["per_mutation"].values()))

    def test_summary_keeps_missing_reports_separate(self) -> None:
        rows = self._rows()
        rows[-1]["missing_report"] = True
        rows[-1]["status"] = "detector_invocation_error"
        summary = experiment.summarize(rows)
        self.assertEqual(summary["missing_reports"], 1)
        self.assertEqual(summary["rejected_or_unsupported_bundles"], 1)

    def test_evaluator_labels_are_not_detector_bundle_fields(self) -> None:
        forbidden = {"mutation_kind", "expected_stage", "expected_error_code", "target_id"}
        self.assertTrue(forbidden.isdisjoint({"bundle_dir", "case_id", "slot_id"}))

    def test_writer_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            path = Path(temporary) / "record.json"
            experiment._write_new(path, {"one": 1})
            with self.assertRaisesRegex(experiment.ExperimentError, "overwrite"):
                experiment._write_new(path, {"two": 2})

    @staticmethod
    def _rows() -> list[dict[str, object]]:
        rows = []
        slot = 0
        for case_id in experiment.EXPECTED_CASE_IDS:
            slot += 1
            rows.append({"case_id": case_id, "slot_id": f"slot-{slot:03d}", "mutation_kind": "clean_control", "status": "no_fault_detected", "detected": False, "exact_localization": False})
            for kind, _, _ in experiment.MUTATIONS:
                slot += 1
                rows.append({"case_id": case_id, "slot_id": f"slot-{slot:03d}", "mutation_kind": kind, "status": "fault_detected", "detected": True, "exact_localization": True})
        return rows


if __name__ == "__main__":
    unittest.main()
