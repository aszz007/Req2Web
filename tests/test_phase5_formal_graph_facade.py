from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_orchestration.phase4_graph import (  # noqa: E402
    Phase4ContractError,
    phase4_compose_sealed_formal_candidate,
    phase4_create_authority_state,
    phase4_create_sealed_formal_authority_state,
    phase4_create_sealed_formal_mapping,
    phase4_project_sealed_formal_node_input_authority,
    phase4_register_sealed_formal_node_output,
    phase4_synthetic_fixture_output,
    synthetic_commerce_b_input,
)


class Phase5FormalGraphFacadeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b_input = synthetic_commerce_b_input(
            case_id="synthetic-phase5-formal-facade",
            request_id="synthetic-phase5-formal-facade-request",
        )

    def test_formal_source_kind_is_not_synthetic(self) -> None:
        historical = phase4_create_authority_state(self.b_input)
        formal = phase4_create_sealed_formal_authority_state(self.b_input)
        self.assertEqual(
            historical["source_kind"],
            "deterministic_synthetic_fixture",
        )
        self.assertEqual(formal["source_kind"], "sealed_formal_holdout")
        self.assertEqual(historical["b_identity"], formal["b_identity"])
        self.assertEqual(
            historical["constraint_identity"],
            formal["constraint_identity"],
        )

    def test_formal_facade_reuses_validator_registry_mapping_and_composition(self) -> None:
        state = phase4_create_sealed_formal_authority_state(self.b_input)
        for node_id in ("F1", "F2", "F3"):
            output = phase4_synthetic_fixture_output(node_id, state)
            state = phase4_register_sealed_formal_node_output(
                state,
                node_id,
                output,
            )
        mapping = phase4_create_sealed_formal_mapping(state)
        self.assertEqual(mapping["status"], "mapped")
        self.assertEqual(
            mapping["input_identities"]["b_identity"],
            state["b_identity"],
        )
        f4_authority = phase4_project_sealed_formal_node_input_authority(
            state,
            "F4",
        )
        self.assertIsNotNone(f4_authority["mapping_identity"])
        self.assertTrue(f4_authority["ordered_mappings"])
        f4 = phase4_synthetic_fixture_output("F4", state)
        state = phase4_register_sealed_formal_node_output(state, "F4", f4)
        candidate = phase4_compose_sealed_formal_candidate(state)
        self.assertEqual(candidate["b_identity"], state["b_identity"])
        self.assertEqual(
            candidate["candidate_projection_status"],
            "candidate_composition_validated_only",
        )
        self.assertGreater(candidate["model_semantic_candidate_byte_length"], 0)
        self.assertEqual(state["source_kind"], "sealed_formal_holdout")

    def test_formal_wrappers_reject_synthetic_or_tampered_source_kind(self) -> None:
        synthetic = phase4_create_authority_state(self.b_input)
        f1 = phase4_synthetic_fixture_output("F1", synthetic)
        with self.assertRaisesRegex(Phase4ContractError, "root drifted"):
            phase4_register_sealed_formal_node_output(synthetic, "F1", f1)

        formal = phase4_create_sealed_formal_authority_state(self.b_input)
        tampered = copy.deepcopy(formal)
        tampered["source_kind"] = "other"
        f1 = phase4_synthetic_fixture_output("F1", formal)
        with self.assertRaisesRegex(Phase4ContractError, "root drifted"):
            phase4_register_sealed_formal_node_output(tampered, "F1", f1)


if __name__ == "__main__":
    unittest.main()
