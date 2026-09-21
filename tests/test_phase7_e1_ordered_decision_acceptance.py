from __future__ import annotations

import unittest
import json
from pathlib import Path

from scripts import phase7_e1_ordered_decision_acceptance as acceptance


def _rows(scores: dict[str, dict[str, int]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for case_id in acceptance.ORDERED_CASE_IDS:
        for arm in acceptance.ARMS:
            passed = scores[case_id][arm]
            for index, criterion_id in enumerate(acceptance.ORDERED_CRITERIA):
                rows.append(
                    {
                        "case_id": case_id,
                        "family": "ordered_decision",
                        "arm": arm,
                        "criterion_id": criterion_id,
                        "label": "pass" if index < passed else "fail",
                    }
                )
    return rows


class OrderedDecisionAcceptanceTests(unittest.TestCase):
    def test_frozen_strategy_matches_scorer_constants(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "phase7_e1_ordered_decision_acceptance_v1.json"
        )
        strategy = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            strategy["historical_source"]["observation_sha256"],
            acceptance.SOURCE_OBSERVATION_SHA256,
        )
        self.assertEqual(
            tuple(strategy["historical_ordered_decision_cases"]),
            acceptance.ORDERED_CASE_IDS,
        )
        self.assertEqual(tuple(strategy["criteria"]), acceptance.ORDERED_CRITERIA)

    def test_scores_every_case_and_every_frozen_criterion(self) -> None:
        scores = {
            acceptance.ORDERED_CASE_IDS[0]: {"A": 6, "B": 2, "C": 5},
            acceptance.ORDERED_CASE_IDS[1]: {"A": 5, "B": 4, "C": 0},
            acceptance.ORDERED_CASE_IDS[2]: {"A": 5, "B": 2, "C": 0},
        }
        result = acceptance._score_ordered_rows(_rows(scores))
        self.assertEqual(result["arms"]["A"]["pass"], 16)
        self.assertEqual(result["arms"]["B"]["pass"], 8)
        contrast = result["contrasts"][0]
        self.assertEqual(contrast["case_win_tie_loss"], {"left_wins": 3, "ties": 0, "right_wins": 0})
        self.assertEqual(contrast["mean_passed_obligation_difference_per_case"], 8 / 3)
        self.assertEqual(contrast["exact_paired_sign_flip_two_sided_p"], 0.25)

    def test_missing_row_fails_closed_instead_of_improving_denominator(self) -> None:
        scores = {
            case_id: {arm: 6 for arm in acceptance.ARMS}
            for case_id in acceptance.ORDERED_CASE_IDS
        }
        rows = _rows(scores)
        rows.pop()
        with self.assertRaisesRegex(acceptance.AcceptanceError, "inventory"):
            acceptance._score_ordered_rows(rows)

    def test_unknown_is_retained_as_a_bound_not_a_pass(self) -> None:
        scores = {
            case_id: {arm: 0 for arm in acceptance.ARMS}
            for case_id in acceptance.ORDERED_CASE_IDS
        }
        rows = _rows(scores)
        rows[0]["label"] = "unknown"
        result = acceptance._score_ordered_rows(rows)
        self.assertEqual(result["arms"]["A"]["pass"], 0)
        self.assertEqual(result["arms"]["A"]["unknown"], 1)
        self.assertGreater(
            result["arms"]["A"]["coverage_upper_bound"],
            result["arms"]["A"]["coverage_lower_bound"],
        )


if __name__ == "__main__":
    unittest.main()
