from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
import zipfile

from scripts import phase7_e1_v3_cases as v3_cases
from scripts import phase7_e1_v4_analyze as analysis
from scripts import phase7_e1_v4_cases as case_module
from scripts import phase7_e1_v4_observe as observer
from scripts import phase7_e1_v4_runtime as runtime
from scripts import prepare_phase7_e1_v4_bundle as bundle


ROOT = Path(__file__).resolve().parents[1]


class Phase7E1V4LocalTests(unittest.TestCase):
    def test_public_case_content_is_unchanged_from_v3(self) -> None:
        old_rows = v3_cases.cases()
        new_rows = case_module.cases()
        self.assertEqual(len(new_rows), 16)
        self.assertEqual(case_module.SOURCE_INPUT_IDENTITY, v3_cases.frozen_input_identity())
        self.assertTrue(case_module.frozen_input_identity().startswith("sha256:"))
        for old, new in zip(old_rows, new_rows, strict=True):
            old_copy = copy.deepcopy(old)
            new_copy = copy.deepcopy(new)
            self.assertEqual(
                new_copy.pop("case_id"),
                "e1v4-" + old_copy.pop("case_id").removeprefix("e1v3-"),
            )
            self.assertEqual(new_copy, old_copy)

    def test_holdout_balance_and_private_parameters_remain_frozen(self) -> None:
        rows = case_module.cases()
        development = [row for row in rows if row["split"] == "development"]
        measured = [row for row in rows if row["split"] == "measured"]
        self.assertEqual((len(development), len(measured)), (4, 12))
        self.assertEqual(
            {
                family: sum(row["family"] == family for row in measured)
                for family in observer._base._CRITERIA
            },
            {
                "ordered_decision": 3,
                "failure_recovery": 3,
                "cancel_restart": 3,
                "conditional_branch": 3,
            },
        )
        by_family = {
            family: {row["case_id"] for row in rows if row["family"] == family}
            for family in observer._base._CRITERIA
        }
        self.assertEqual(set(observer._ORDER_CHECKPOINT), by_family["ordered_decision"])
        self.assertEqual(set(observer._RECOVERY_PREPARATION), by_family["failure_recovery"])
        self.assertEqual(set(observer._WIZARD_STEP_COUNT), by_family["cancel_restart"])
        self.assertEqual(set(observer._BRANCH_CHECKPOINT), by_family["conditional_branch"])

    def test_runtime_schedule_and_payload_keep_evaluator_local(self) -> None:
        schedule = runtime._schedule(case_module.cases())
        self.assertEqual(len(schedule), 48)
        self.assertEqual(sum(row["call_cap"] for row in schedule), 96)
        self.assertEqual(
            sum(row["call_cap"] for row in schedule if row["split"] == "measured"),
            72,
        )
        self.assertIn("scripts/phase7_e1_v4_runtime.py", bundle.OVERLAY)
        self.assertIn("scripts/phase7_e1_v4_cases.py", bundle.OVERLAY)
        self.assertNotIn("scripts/phase7_e1_v4_observe.py", bundle.OVERLAY)
        with analysis._configured_base():
            self.assertEqual(analysis._base.SCHEMA, "req2web.phase7.e1_v4.analysis.v1")

    def test_current_payload_excludes_private_evaluator_material(self) -> None:
        archive = (
            ROOT
            / "outputs/phase7_experiment1_v4/payload-v2-20260918"
            / "phase7-autodl-payload.zip"
        )
        if not archive.is_file():
            self.skipTest("E1 v4 payload has not been built")
        with zipfile.ZipFile(archive) as source:
            names = set(source.namelist())
            manifest = json.loads(source.read("payload_manifest.json"))
        self.assertNotIn("scripts/phase7_e1_v4_observe.py", names)
        self.assertFalse(any("evaluator_gold" in name for name in names))
        self.assertEqual(manifest["metadata"]["automatic_retry_count"], 0)
        self.assertEqual(
            manifest["metadata"]["input_identity"],
            case_module.frozen_input_identity(),
        )


if __name__ == "__main__":
    unittest.main()
