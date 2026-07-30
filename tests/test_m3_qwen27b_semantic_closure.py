"""Focused tests for the bounded Qwen27B deterministic semantic closure."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_provider.semantic_candidate import ModelSemanticCandidate  # noqa: E402
from req2web_runtime.qwen27b_semantic_closure import (  # noqa: E402
    Qwen27bSemanticClosureError,
    apply_qwen27b_deterministic_semantic_closure,
)
from test_m3_qwen27b_semantic_gate import payload  # noqa: E402


class Qwen27bSemanticClosureTests(unittest.TestCase):
    def candidate(self, case_id: str, value: dict | None = None):
        return ModelSemanticCandidate.from_dict(value or payload(case_id))

    def test_trigger_visibility_adds_existing_trigger_once_and_is_idempotent(self):
        data = payload("path3-media-analysis")
        interaction = data["interactions"][0]
        source = next(
            item
            for item in data["states"]
            if item["stable_id"] == interaction["source_state_stable_id"]
        )
        source["visible_component_stable_ids"].remove(
            interaction["trigger_component_stable_id"]
        )
        original = self.candidate("path3-media-analysis", data)

        first = apply_qwen27b_deterministic_semantic_closure(
            case_id="path3-media-analysis",
            candidate=original,
        )
        first.validate()
        self.assertEqual(first.report.decision, "repaired")
        self.assertEqual(first.report.repair_attempted, 1)
        self.assertEqual(
            [item.rule_id for item in first.report.operations],
            ["trigger_visibility_closure_v1"],
        )
        self.assertNotEqual(first.candidate.sha256(), original.sha256())
        repaired_source = next(
            item
            for item in first.candidate.states
            if item.stable_id == interaction["source_state_stable_id"]
        )
        self.assertEqual(
            repaired_source.visible_component_stable_ids.count(
                interaction["trigger_component_stable_id"]
            ),
            1,
        )

        second = apply_qwen27b_deterministic_semantic_closure(
            case_id="path3-media-analysis",
            candidate=first.candidate,
        )
        second.validate()
        self.assertEqual(second.report.decision, "unchanged")
        self.assertEqual(second.report.repair_attempted, 0)
        self.assertEqual(second.candidate.sha256(), first.candidate.sha256())

    def test_unique_acceptance_target_closes_interaction_and_mapping_trace(self):
        data = payload("path3-media-analysis")
        uc1_check = data["acceptance_checks"][0]
        uc2_interaction = next(
            item
            for item in data["interactions"]
            if item["use_case_ids"] == ["UC-02"]
        )
        uc1_check["state_stable_id"] = uc2_interaction[
            "target_state_stable_id"
        ]
        original = self.candidate("path3-media-analysis", data)

        result = apply_qwen27b_deterministic_semantic_closure(
            case_id="path3-media-analysis",
            candidate=original,
        )
        result.validate()
        self.assertEqual(result.report.decision, "repaired")
        repaired_interaction = next(
            item
            for item in result.candidate.interactions
            if item.stable_id == uc2_interaction["stable_id"]
        )
        self.assertEqual(repaired_interaction.use_case_ids, ("UC-02", "UC-01"))
        repaired_check = next(
            item
            for item in result.candidate.acceptance_checks
            if item.stable_id == uc1_check["stable_id"]
        )
        self.assertEqual(repaired_check.use_case_ids, ("UC-01",))
        uc1_mapping = next(
            item
            for item in result.candidate.use_case_mappings
            if item.use_case_id == "UC-01"
        )
        self.assertIn(
            uc2_interaction["stable_id"],
            uc1_mapping.interaction_stable_ids,
        )
        self.assertEqual(
            {item.rule_id for item in result.report.operations},
            {"unique_acceptance_reachability_trace_v1"},
        )

    def test_ambiguous_acceptance_target_fails_closed_atomically(self):
        data = payload("path3-media-analysis")
        uc2_mapping = data["use_case_mappings"][1]
        target = data["interactions"][1]["target_state_stable_id"]
        source = data["interactions"][1]["source_state_stable_id"]
        trigger = uc2_mapping["component_stable_ids"][0]
        extra = {
            "stable_id": "int-media-2-alternate",
            "trigger_component_stable_id": trigger,
            "source_state_stable_id": source,
            "action": "alternate analysis result",
            "target_state_stable_id": target,
            "user_feedback": "Alternate analysis result shown",
            "use_case_ids": ["UC-02"],
        }
        data["interactions"].append(extra)
        uc2_mapping["interaction_stable_ids"].append(extra["stable_id"])
        data["acceptance_checks"][0]["state_stable_id"] = target
        original = self.candidate("path3-media-analysis", data)

        result = apply_qwen27b_deterministic_semantic_closure(
            case_id="path3-media-analysis",
            candidate=original,
        )
        result.validate()
        self.assertEqual(result.report.decision, "fail_closed")
        self.assertEqual(
            result.report.failure_code,
            "acceptance_target_interaction_not_unique",
        )
        self.assertIsNone(result.candidate)
        self.assertEqual(original.sha256(), self.candidate("path3-media-analysis", data).sha256())

    def test_closure_never_creates_semantic_entities(self):
        data = payload("path3-media-analysis")
        interaction = data["interactions"][0]
        source = next(
            item
            for item in data["states"]
            if item["stable_id"] == interaction["source_state_stable_id"]
        )
        source["visible_component_stable_ids"].remove(
            interaction["trigger_component_stable_id"]
        )
        original = self.candidate("path3-media-analysis", data)
        counts = (
            len(original.sections),
            len(original.components),
            len(original.states),
            len(original.interactions),
            len(original.acceptance_checks),
        )
        result = apply_qwen27b_deterministic_semantic_closure(
            case_id="path3-media-analysis",
            candidate=original,
        )
        self.assertEqual(
            (
                len(result.candidate.sections),
                len(result.candidate.components),
                len(result.candidate.states),
                len(result.candidate.interactions),
                len(result.candidate.acceptance_checks),
            ),
            counts,
        )

    def test_unregistered_case_and_direct_invalid_type_fail_closed(self):
        candidate = self.candidate("path3-media-analysis")
        with self.assertRaisesRegex(
            Qwen27bSemanticClosureError,
            "semantic_closure_case_not_registered",
        ):
            apply_qwen27b_deterministic_semantic_closure(
                case_id="unregistered",
                candidate=candidate,
            )
        with self.assertRaisesRegex(
            Qwen27bSemanticClosureError,
            "semantic_closure_candidate_type_invalid",
        ):
            apply_qwen27b_deterministic_semantic_closure(
                case_id="path3-media-analysis",
                candidate=object(),
            )


if __name__ == "__main__":
    unittest.main()