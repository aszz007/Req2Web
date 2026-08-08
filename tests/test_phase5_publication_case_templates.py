from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_evaluation.phase5_publication_case_templates import (  # noqa: E402
    INTERVENTION_FREEZE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    TEMPLATE_MATRIX_SCHEMA_VERSION,
    build_phase5_publication_case_templates_from_json_bytes,
    build_phase5_publication_intervention_freeze,
    build_phase5_publication_template_matrix,
    validate_phase5_publication_case_templates,
    validate_phase5_publication_intervention_freeze,
    validate_phase5_publication_template_matrix,
)


FIXTURE = ROOT / "fixtures" / "phase5_publication_case_templates_v1.json"
FREEZE_FIXTURE = (
    ROOT / "fixtures" / "phase5_publication_intervention_freeze_v1.json"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _rebind_fixture_id(value: dict[str, object]) -> None:
    body = {key: item for key, item in value.items() if key != "fixture_id"}
    value["fixture_id"] = (
        "phase5-publication-templates-"
        + hashlib.sha256(_canonical(body)).hexdigest()
    )


class Phase5PublicationCaseTemplatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = build_phase5_publication_case_templates_from_json_bytes(
            FIXTURE.read_bytes()
        )
        self.matrix = build_phase5_publication_template_matrix(self.fixture)
        self.freeze = build_phase5_publication_intervention_freeze(self.fixture)
        self.stored_freeze = validate_phase5_publication_intervention_freeze(
            json.loads(FREEZE_FIXTURE.read_text(encoding="utf-8")),
            self.fixture,
        )

    def test_fixture_identity_scope_and_language(self) -> None:
        self.assertEqual(self.fixture["schema_version"], SCHEMA_VERSION)
        self.assertEqual(self.fixture["language"], "en")
        self.assertEqual(self.fixture["core_template_count"], 4)
        self.assertEqual(self.fixture["reserve_template_count"], 2)
        self.assertEqual(
            self.fixture["fixture_id"],
            "phase5-publication-templates-"
            "253d78d347b2f3e0ecc3669664a0d839969b803ff452d3c4e4d5f980299f43fc",
        )

    def test_core_and_reserve_slots_match_the_approved_recommendation(self) -> None:
        templates = self.fixture["templates"]
        self.assertEqual(
            [item["slot_id"] for item in templates if item["slot_kind"] == "core"],
            [
                "information_retrieval",
                "structured_form_transaction",
                "media_async_processing",
                "stateful_recovery_responsive",
            ],
        )
        self.assertEqual(
            [item["slot_id"] for item in templates if item["slot_kind"] == "reserve"],
            ["appointment_scheduling", "settings_administration"],
        )
        self.assertTrue(
            all(len(item["use_case_outlines"]) >= 2 for item in templates)
        )
        self.assertTrue(
            all(len(item["acceptance_observables"]) >= 2 for item in templates)
        )
        self.assertTrue(
            all(len(item["constraint_outlines"]) >= 2 for item in templates)
        )
        self.assertTrue(
            all(
                set(item["evidence_role_outlines"])
                == {
                    "requirement",
                    "ui_reference",
                    "interaction_flow",
                    "implementation",
                    "validation",
                }
                for item in templates
            )
        )

    def test_condition_payloads_and_real_actions_remain_unfrozen(self) -> None:
        policy = self.fixture["condition_template_policy"]
        self.assertEqual(
            policy["condition_ids"],
            ["none", "irrelevant_evidence", "remove_critical_role"],
        )
        self.assertEqual(policy["irrelevant_item_count_per_case"], 1)
        self.assertFalse(policy["irrelevant_payloads_frozen"])
        self.assertFalse(policy["critical_role_ids_frozen"])
        self.assertFalse(policy["reserve_replacement_after_opening_allowed"])
        self.assertFalse(policy["result_driven_template_change_allowed"])
        self.assertTrue(
            all(value is False for value in self.fixture["action_state"].values())
        )

    def test_draft_matrix_has_twelve_rows_and_excludes_reserves(self) -> None:
        self.assertEqual(
            self.matrix["schema_version"],
            TEMPLATE_MATRIX_SCHEMA_VERSION,
        )
        self.assertEqual(self.matrix["runtime_row_count"], 12)
        self.assertEqual(self.matrix["node_generate_call_cap"], 48)
        self.assertEqual(len(self.matrix["rows"]), 12)
        self.assertEqual(
            [row["condition_id"] for row in self.matrix["rows"][:3]],
            ["none", "irrelevant_evidence", "remove_critical_role"],
        )
        self.assertTrue(
            all(
                row["template_id"].startswith("publication-core-template-")
                for row in self.matrix["rows"]
            )
        )
        self.assertTrue(
            all(
                row["status"] == "not_executed_draft_template_only"
                and row["terminal_status"] is None
                and row["generate_call_cap"] == 4
                for row in self.matrix["rows"]
            )
        )
        self.assertTrue(
            all(value is False for value in self.matrix["action_state"].values())
        )

    def test_intervention_freeze_binds_exact_texts_and_roles_by_hash(self) -> None:
        self.assertEqual(self.stored_freeze, self.freeze)
        self.assertEqual(
            self.freeze["schema_version"],
            INTERVENTION_FREEZE_SCHEMA_VERSION,
        )
        self.assertEqual(
            self.freeze["manager_consensus"],
            "three_prior_managers_accepted_after_core1_correction",
        )
        self.assertEqual(
            self.freeze["frozen_condition_ids"],
            ["irrelevant_evidence", "remove_critical_role"],
        )
        self.assertEqual(self.freeze["template_count"], 6)
        self.assertEqual(
            [row["critical_role_id"] for row in self.freeze["rows"]],
            [
                "ui_reference",
                "validation",
                "interaction_flow",
                "interaction_flow",
                "interaction_flow",
                "implementation",
            ],
        )
        self.assertTrue(
            all(len(row["irrelevant_evidence_sha256"]) == 64 for row in self.freeze["rows"])
        )
        self.assertTrue(
            all(value is False for value in self.freeze["action_state"].values())
        )

    def test_fixture_contains_no_gold_or_model_output_fields(self) -> None:
        text = _canonical(self.fixture).decode("utf-8")
        self.assertNotIn("gold_label", text)
        self.assertNotIn("expected_page_spec", text)
        self.assertNotIn("provider_raw_response", text)
        self.assertNotIn("real_h1_content", text)

    def test_cjk_template_text_fails_closed(self) -> None:
        tampered = copy.deepcopy(self.fixture)
        tampered["templates"][0]["title"] = "\u4e2d\u6587\u6807\u9898"
        _rebind_fixture_id(tampered)
        with self.assertRaisesRegex(ValueError, "English-only"):
            validate_phase5_publication_case_templates(tampered)

    def test_slot_order_role_and_action_tampering_fail_closed(self) -> None:
        wrong_order = copy.deepcopy(self.fixture)
        wrong_order["templates"][0], wrong_order["templates"][1] = (
            wrong_order["templates"][1],
            wrong_order["templates"][0],
        )
        _rebind_fixture_id(wrong_order)
        with self.assertRaisesRegex(ValueError, "core slot order drifted"):
            validate_phase5_publication_case_templates(wrong_order)

        wrong_role = copy.deepcopy(self.fixture)
        wrong_role["templates"][0]["critical_role_recommendation"] = "unknown"
        _rebind_fixture_id(wrong_role)
        with self.assertRaisesRegex(ValueError, "critical role is invalid"):
            validate_phase5_publication_case_templates(wrong_role)

        missing_evidence_role = copy.deepcopy(self.fixture)
        del missing_evidence_role["templates"][0]["evidence_role_outlines"][
            "validation"
        ]
        _rebind_fixture_id(missing_evidence_role)
        with self.assertRaisesRegex(ValueError, "invalid keys"):
            validate_phase5_publication_case_templates(missing_evidence_role)

        source_path = copy.deepcopy(self.fixture)
        source_path["templates"][0]["evidence_role_outlines"][
            "implementation"
        ] = "Read the implementation from https://example.invalid/reference."
        _rebind_fixture_id(source_path)
        with self.assertRaisesRegex(ValueError, "URI or source path"):
            validate_phase5_publication_case_templates(source_path)

        opened = copy.deepcopy(self.fixture)
        opened["action_state"]["model_action_authorized"] = True
        _rebind_fixture_id(opened)
        with self.assertRaisesRegex(ValueError, "must remain false"):
            validate_phase5_publication_case_templates(opened)

        matrix_drift = copy.deepcopy(self.matrix)
        matrix_drift["rows"][0]["generate_call_cap"] = 5
        body = {
            key: value
            for key, value in matrix_drift.items()
            if key != "matrix_id"
        }
        matrix_drift["matrix_id"] = (
            "phase5-publication-template-matrix-"
            + hashlib.sha256(_canonical(body)).hexdigest()
        )
        with self.assertRaisesRegex(ValueError, "matrix content drifted"):
            validate_phase5_publication_template_matrix(
                matrix_drift,
                self.fixture,
            )

        freeze_drift = copy.deepcopy(self.freeze)
        freeze_drift["rows"][0]["critical_role_id"] = "implementation"
        body = {
            key: value
            for key, value in freeze_drift.items()
            if key != "manifest_id"
        }
        freeze_drift["manifest_id"] = (
            "phase5-publication-intervention-freeze-"
            + hashlib.sha256(_canonical(body)).hexdigest()
        )
        with self.assertRaisesRegex(ValueError, "freeze content drifted"):
            validate_phase5_publication_intervention_freeze(
                freeze_drift,
                self.fixture,
            )


if __name__ == "__main__":
    unittest.main()
