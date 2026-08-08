from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_evaluation.phase5_single_owner_protocol import (  # noqa: E402
    Phase5SingleOwnerProtocol,
    build_phase5_single_owner_protocol,
)


class Phase5SingleOwnerProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = build_phase5_single_owner_protocol()
        self.payload = self.contract.to_dict()

    def test_identity_is_frozen(self) -> None:
        self.assertEqual(
            self.contract.sha256(),
            "75ba7c7f6922471431ce85bfdd1319e397de84239d1999294a2b900351c4ccf9",
        )
        self.assertEqual(
            self.payload["contract_id"],
            "phase5-single-owner-protocol-"
            "60392cb753750dbe2acfd5f4b2a20ff98c7b87c7820e74039af6cee77dcba814",
        )

    def test_single_evaluator_limitation_is_explicit(self) -> None:
        evaluator = self.payload["evaluator_contract"]
        self.assertEqual(evaluator["evaluator_mode"], "single_owner_evaluator")
        self.assertTrue(evaluator["owner_is_only_plaintext_evaluator"])
        self.assertFalse(evaluator["second_annotator_present"])
        self.assertFalse(evaluator["inter_rater_agreement_applicable"])
        self.assertFalse(evaluator["inter_rater_agreement_claim_allowed"])
        self.assertFalse(evaluator["independent_adjudication_claim_allowed"])
        self.assertTrue(evaluator["single_evaluator_limitation_required"])
        self.assertFalse(evaluator["manager_or_developer_plaintext_access_allowed"])
        self.assertFalse(evaluator["runtime_model_gold_access_allowed"])

    def test_metric_denominators_and_missing_rules_are_frozen(self) -> None:
        metrics = {metric["name"]: metric for metric in self.payload["metrics"]}
        self.assertEqual(len(metrics), 13)
        self.assertEqual(
            metrics["candidate_attribution_coverage"]["missing_rule"],
            "no_valid_candidate_case_value_zero",
        )
        self.assertEqual(
            metrics["independent_acceptance_coverage"]["denominator"],
            "all_applicable_owner_gold_criteria",
        )
        self.assertEqual(
            metrics["all_gold_criterion_success"]["missing_rule"],
            "fail_unknown_and_not_supported_remain_in_denominator",
        )
        self.assertTrue(
            self.payload["outcome_accounting"][
                "not_supported_retained_in_applicable_denominator"
            ]
        )
        self.assertTrue(
            self.payload["outcome_accounting"][
                "candidate_output_cannot_define_gold_or_denominator"
            ]
        )
        self.assertTrue(all(metric["executed"] is False for metric in metrics.values()))
        self.assertTrue(
            all(metric["formal_value"] is None for metric in metrics.values())
        )

    def test_claim_scope_is_bounded_and_thresholds_remain_pending(self) -> None:
        claims = self.payload["claim_matrix"]
        forbidden = set(claims["always_forbidden"])
        self.assertIn("broad_generalization", forbidden)
        self.assertIn("dual_annotator_or_inter_rater_reliability", forbidden)
        self.assertIn("production_quality_or_deployment_readiness", forbidden)
        self.assertFalse(claims["causal_language_allowed"])
        self.assertTrue(claims["controlled_influence_language_required"])
        threshold = self.payload["threshold_contract"]
        self.assertTrue(threshold["metric_definitions_frozen"])
        self.assertTrue(threshold["single_evaluator_claim_scope_frozen"])
        self.assertFalse(threshold["numeric_minimum_gain_thresholds_frozen"])
        self.assertFalse(threshold["formal_h1_may_set_or_tune_thresholds"])
        self.assertFalse(
            threshold["threshold_change_after_candidate_or_h1_result_allowed"]
        )

    def test_round_trip_and_tamper_fail_closed(self) -> None:
        replayed = Phase5SingleOwnerProtocol.from_json_bytes(
            self.contract.canonical_json_bytes()
        )
        self.assertEqual(replayed, self.contract)

        tampered = json.loads(json.dumps(self.payload))
        tampered["evaluator_contract"]["inter_rater_agreement_claim_allowed"] = True
        with self.assertRaisesRegex(ValueError, "content drifted"):
            Phase5SingleOwnerProtocol.from_dict(tampered)

        tampered = json.loads(json.dumps(self.payload))
        tampered["threshold_contract"]["numeric_minimum_gain_thresholds_frozen"] = True
        with self.assertRaisesRegex(ValueError, "content drifted"):
            Phase5SingleOwnerProtocol.from_dict(tampered)


if __name__ == "__main__":
    unittest.main()
