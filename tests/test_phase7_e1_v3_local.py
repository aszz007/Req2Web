from __future__ import annotations

import unittest

from scripts import phase7_e1_v3_analyze as analysis
from scripts import phase7_e1_v3_cases as case_module
from scripts import phase7_e1_v3_observe as observer
from scripts import phase7_e1_v3_runtime as runtime
from scripts import prepare_phase7_e1_v3_bundle as bundle


class Phase7E1V3LocalTests(unittest.TestCase):
    def test_frozen_holdout_is_balanced_and_new(self) -> None:
        rows = case_module.cases()
        self.assertEqual(len(rows), 16)
        self.assertTrue(case_module.frozen_input_identity().startswith("sha256:"))
        development = [row for row in rows if row["split"] == "development"]
        measured = [row for row in rows if row["split"] == "measured"]
        self.assertEqual(len(development), 4)
        self.assertEqual(len(measured), 12)
        self.assertEqual(
            {family: sum(row["family"] == family for row in measured) for family in observer._base._CRITERIA},
            {
                "ordered_decision": 3,
                "failure_recovery": 3,
                "cancel_restart": 3,
                "conditional_branch": 3,
            },
        )
        self.assertTrue(all(row["case_id"].startswith("e1v3-") for row in rows))

    def test_private_observer_parameters_cover_every_specialized_case(self) -> None:
        rows = case_module.cases()
        by_family = {
            family: {row["case_id"] for row in rows if row["family"] == family}
            for family in observer._base._CRITERIA
        }
        self.assertEqual(set(observer._ORDER_CHECKPOINT), by_family["ordered_decision"])
        self.assertEqual(set(observer._RECOVERY_PREPARATION), by_family["failure_recovery"])
        self.assertEqual(set(observer._WIZARD_STEP_COUNT), by_family["cancel_restart"])
        self.assertEqual(set(observer._BRANCH_CHECKPOINT), by_family["conditional_branch"])
        self.assertEqual(len(observer.evaluator_checks()), 16)
        self.assertTrue(observer.observer_identity().startswith("sha256:"))

    def test_runtime_schedule_and_payload_keep_evaluator_local(self) -> None:
        schedule = runtime._schedule(case_module.cases())
        self.assertEqual(len(schedule), 48)
        self.assertEqual(sum(row["call_cap"] for row in schedule), 96)
        self.assertEqual(sum(row["call_cap"] for row in schedule if row["split"] == "measured"), 72)
        self.assertIn("scripts/phase7_e1_v3_runtime.py", bundle.OVERLAY)
        self.assertIn("scripts/phase7_e1_v3_cases.py", bundle.OVERLAY)
        self.assertNotIn("scripts/phase7_e1_v3_observe.py", bundle.OVERLAY)
        with analysis._configured_base():
            self.assertEqual(analysis._base.SCHEMA, "req2web.phase7.e1_v3.analysis.v1")


if __name__ == "__main__":
    unittest.main()
