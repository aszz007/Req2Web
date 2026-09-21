from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from scripts import phase7_e2_integrated_trace_acceptance as acceptance


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATH = ROOT / "outputs/phase7_experiment2_comparison_v1/local-baselines-final/scored_observations.json"
EXTERNAL_PATH = ROOT / "outputs/phase7_experiment2_comparison_v1/external-epsilon/run-1/observations.json"
STRATEGY_PATH = ROOT / "fixtures/phase7_e2_integrated_trace_acceptance_v1.json"


def _load():
    local = json.loads(LOCAL_PATH.read_text(encoding="utf-8"))
    external = json.loads(EXTERNAL_PATH.read_text(encoding="utf-8"))
    strategy = json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))
    return local, external, strategy


def _analyze(local=None, external=None):
    source_local, source_external, strategy = _load()
    return acceptance.analyze_payloads(
        source_local if local is None else local,
        source_external if external is None else external,
        local_sha256=acceptance.LOCAL_OBSERVATIONS_SHA256,
        external_sha256=acceptance.EXTERNAL_OBSERVATIONS_SHA256,
        strategy=strategy,
    )


class IntegratedTraceAcceptanceTests(unittest.TestCase):
    def test_strategy_matches_frozen_sources_and_complete_family(self) -> None:
        _, _, strategy = _load()
        acceptance._validate_strategy(strategy)
        self.assertEqual(strategy["case_ids"], list(acceptance.CASE_IDS))
        self.assertEqual(strategy["criteria"], list(acceptance.CRITERIA))

    def test_frozen_secondary_result_is_18_vs_6_with_external_cross_tie(self) -> None:
        result = acceptance.analyze_files(LOCAL_PATH, EXTERNAL_PATH, STRATEGY_PATH)
        self.assertEqual(result["conditions"]["C2"]["integrated_trace_family"]["pass"], 18)
        self.assertEqual(result["conditions"]["C1"]["integrated_trace_family"]["pass"], 6)
        self.assertEqual(result["conditions"]["EVL-local"]["integrated_trace_family"]["pass"], 6)
        self.assertEqual(result["conditions"]["EVL-cross"]["integrated_trace_family"]["pass"], 18)
        self.assertTrue(result["external_equivalence_disclosed"])
        self.assertEqual(
            result["exploratory_interpretation"],
            "bounded_integrated_trace_checking_advantage_observed_not_unique",
        )

    def test_all_six_cases_are_retained_and_req2web_wins_each_local_pair(self) -> None:
        result = _analyze()
        contrast = next(
            row for row in result["contrasts"] if row["contrast"] == "C2-EVL-local"
        )
        self.assertEqual(len(contrast["case_differences"]), 6)
        self.assertEqual(
            contrast["case_win_tie_loss"],
            {"left_wins": 6, "ties": 0, "right_wins": 0},
        )
        self.assertTrue(all(row["difference"] == 2 for row in contrast["case_differences"]))
        self.assertEqual(contrast["exact_paired_sign_flip_two_sided_p"], 0.03125)

    def test_broad_results_remain_visible(self) -> None:
        result = _analyze()
        self.assertEqual(
            result["conditions"]["C2"]["broad_context_unchanged"],
            {
                "mutants_detected": 24,
                "mutant_total": 24,
                "exact_targets_present": 24,
                "clean_alarms": 0,
                "clean_total": 6,
            },
        )
        self.assertEqual(
            result["conditions"]["EVL-cross"]["broad_context_unchanged"]["mutants_detected"],
            24,
        )

    def test_missing_observation_fails_closed(self) -> None:
        local, external, _ = _load()
        local["observations"].pop()
        with self.assertRaisesRegex(acceptance.IntegratedTraceAcceptanceError, "retain all"):
            _analyze(local=local, external=external)

    def test_duplicate_observation_fails_closed(self) -> None:
        local, external, _ = _load()
        local["observations"][-1] = copy.deepcopy(local["observations"][0])
        with self.assertRaisesRegex(acceptance.IntegratedTraceAcceptanceError, "inventory"):
            _analyze(local=local, external=external)

    def test_target_hit_without_detection_is_rejected(self) -> None:
        local, external, _ = _load()
        row = next(
            row
            for row in external
            if row["condition"] == "EVL-cross"
            and row["mutation_kind"] == acceptance.TRACE_FAMILY
        )
        row["detected"] = False
        with self.assertRaisesRegex(acceptance.IntegratedTraceAcceptanceError, "requires"):
            _analyze(local=local, external=external)

    def test_identity_drift_fails_closed(self) -> None:
        local, external, strategy = _load()
        with self.assertRaisesRegex(acceptance.IntegratedTraceAcceptanceError, "identity"):
            acceptance.analyze_payloads(
                local,
                external,
                local_sha256="sha256:" + "0" * 64,
                external_sha256=acceptance.EXTERNAL_OBSERVATIONS_SHA256,
                strategy=strategy,
            )


if __name__ == "__main__":
    unittest.main()
