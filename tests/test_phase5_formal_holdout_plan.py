from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_evaluation.phase5_formal_holdout_plan import (  # noqa: E402
    P5_01_FOUNDATION_BUNDLE_SHA256,
    PUBLICATION_SCOPE_DESCRIPTOR_SCHEMA_VERSION,
    Phase5FormalNoActionPlan,
    build_phase5_formal_no_action_plan,
    build_phase5_formal_no_action_plan_from_json_bytes,
    build_phase5_publication_scope_descriptor,
    validate_phase5_publication_scope_descriptor,
)
from req2web_evaluation.phase5_holdout_no_action import (  # noqa: E402
    build_synthetic_phase5_no_action_bundle_from_json_bytes,
)


FOUNDATION_FIXTURE = ROOT / "fixtures" / "phase5_holdout_no_action_synthetic_v1.json"
OPAQUE_FIXTURE = ROOT / "fixtures" / "phase5_formal_holdout_opaque_slots_v1.json"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _record_id(prefix: str, value: object) -> str:
    return f"{prefix}-{hashlib.sha256(_canonical(value)).hexdigest()}"


def _fixture() -> dict[str, object]:
    value = json.loads(OPAQUE_FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("fixture root must be an object")
    return value


def _rebind_root(plan: dict[str, object]) -> None:
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    plan["plan_id"] = _record_id("phase5-formal-no-action-plan", body)


class Phase5FormalHoldoutPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.foundation = build_synthetic_phase5_no_action_bundle_from_json_bytes(
            FOUNDATION_FIXTURE.read_bytes()
        )

    def setUp(self) -> None:
        self.plan = build_phase5_formal_no_action_plan_from_json_bytes(
            self.foundation,
            OPAQUE_FIXTURE.read_bytes(),
        )
        self.payload = self.plan.to_dict()

    def test_foundation_binding_and_plan_status(self) -> None:
        self.assertEqual(self.foundation.sha256(), P5_01_FOUNDATION_BUNDLE_SHA256)
        self.assertEqual(self.payload["status"], "p5_02_p5_03_complete_no_action")
        self.assertEqual(
            self.payload["foundation_bundle_sha256"],
            P5_01_FOUNDATION_BUNDLE_SHA256,
        )

    def test_canonical_round_trip_and_hash_stability(self) -> None:
        replayed = Phase5FormalNoActionPlan.from_json_bytes(
            self.plan.canonical_json_bytes()
        )
        rebuilt = build_phase5_formal_no_action_plan(self.foundation, _fixture())
        self.assertEqual(replayed.to_dict(), self.payload)
        self.assertEqual(replayed.sha256(), self.plan.sha256())
        self.assertEqual(rebuilt.sha256(), self.plan.sha256())

    def test_opaque_custody_has_12_core_8_reserve_4_deep_and_no_content(self) -> None:
        custody = self.payload["custody_plan"]
        slots = custody["slots"]
        self.assertEqual(len(slots), 20)
        self.assertEqual(sum(slot["slot_kind"] == "core" for slot in slots), 12)
        self.assertEqual(sum(slot["slot_kind"] == "reserve" for slot in slots), 8)
        self.assertEqual(sum(slot["deep_label_slot"] for slot in slots), 4)
        self.assertEqual(len({slot["blind_case_ref"] for slot in slots}), 20)
        self.assertFalse(custody["real_h1_selected"])
        self.assertFalse(custody["reserve_promotion_performed"])
        self.assertFalse(custody["custodian_release_allowed"])
        for slot in slots:
            self.assertFalse(slot["real_case_content_present"])
            self.assertFalse(slot["real_gold_content_present"])
            self.assertIsNone(slot["gold_commitment_sha256"])

    def test_duplicate_annotation_and_gold_interfaces_are_empty(self) -> None:
        custody = self.payload["custody_plan"]
        self.assertEqual(custody["manual_duplicate_adjudication"]["entries"], [])
        self.assertFalse(
            custody["manual_duplicate_adjudication"]["real_subject_content_visible"]
        )
        self.assertEqual(custody["dual_annotation"]["required_annotator_count"], 2)
        self.assertEqual(custody["dual_annotation"]["raw_annotation_commitments"], [])
        self.assertEqual(custody["dual_annotation"]["adjudication_commitments"], [])
        self.assertFalse(custody["dual_annotation"]["complete"])
        self.assertEqual(custody["gold_custody"]["gold_commitments"], [])
        self.assertFalse(custody["gold_custody"]["runtime_gold_access_allowed"])
        self.assertFalse(custody["gold_custody"]["provider_gold_access_allowed"])

    def test_full_matrix_has_expected_rows_blocks_and_group_counts(self) -> None:
        matrix = self.payload["formal_matrix"]
        rows = matrix["rows"]
        self.assertEqual(len(rows), 140)
        self.assertEqual(len(matrix["block_order"]), 80)
        self.assertEqual(sum(row["group"] == "G0" for row in rows), 20)
        self.assertEqual(sum(row["group"] == "G1" for row in rows), 60)
        self.assertEqual(sum(row["group"] == "G2" for row in rows), 60)
        self.assertFalse(matrix["formal_h1_rows_present"])
        self.assertFalse(matrix["candidate_outputs_present"])
        self.assertEqual(matrix["fault_copy_template"]["rows"], [])

    def test_deep_slots_receive_both_interventions_and_non_deep_slots_do_not(self) -> None:
        rows = self.payload["formal_matrix"]["rows"]
        interventions_by_case: dict[str, set[str]] = {}
        deep_by_case: dict[str, bool] = {}
        for row in rows:
            interventions_by_case.setdefault(row["blind_case_ref"], set()).add(
                row["intervention"]
            )
            deep_by_case[row["blind_case_ref"]] = row["deep_label_slot"]
        for blind_case_ref, interventions in interventions_by_case.items():
            expected = (
                {"none", "irrelevant_evidence", "remove_critical_role"}
                if deep_by_case[blind_case_ref]
                else {"none"}
            )
            self.assertEqual(interventions, expected)

    def test_g1_g2_are_indivisible_and_candidate_blocks_depend_on_g0(self) -> None:
        matrix = self.payload["formal_matrix"]
        rows_by_block: dict[str, list[dict[str, object]]] = {}
        for row in matrix["rows"]:
            rows_by_block.setdefault(row["block_id"], []).append(row)
        for block_id, rows in rows_by_block.items():
            groups = {row["group"] for row in rows}
            if groups == {"G1", "G2"}:
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["seed"], rows[1]["seed"])
                self.assertTrue(rows[0]["depends_on_block_ids"])
                for dependency in rows[0]["depends_on_block_ids"]:
                    self.assertLess(
                        matrix["block_order"].index(dependency),
                        matrix["block_order"].index(block_id),
                    )
            else:
                self.assertEqual(groups, {"G0"})

    def test_budget_matches_four_node_phase4_chain_formula(self) -> None:
        budget = self.payload["budget_plan"]
        self.assertEqual(budget["deterministic_g0_run_cap"], 20)
        self.assertEqual(budget["candidate_generation_cap"], 60)
        self.assertEqual(budget["node_generate_call_cap"], 240)
        self.assertEqual(budget["total_model_call_cap"], 240)
        self.assertEqual(budget["candidate_model_repair_call_cap"], 0)
        self.assertEqual(budget["fault_copy_model_repair_call_cap"], 0)
        self.assertEqual(budget["provider_retry_call_cap"], 0)
        self.assertEqual(budget["calls_consumed"], 0)
        self.assertIsNone(budget["gpu_time_cap_seconds"])
        self.assertIsNone(budget["cost_cap_minor_units"])
        self.assertIsNone(budget["storage_cap_bytes"])
        self.assertTrue(budget["generate_start_consumes_call"])
        self.assertFalse(budget["automatic_retry_allowed"])
        self.assertFalse(budget["budget_reset_allowed"])
        self.assertFalse(budget["owner_approved"])
        self.assertFalse(budget["model_action_authorized"])

    def test_result_inventory_has_one_empty_skeleton_per_matrix_row(self) -> None:
        matrix = self.payload["formal_matrix"]
        inventory = self.payload["result_inventory"]
        self.assertEqual(inventory["expected_result_row_count"], 140)
        self.assertEqual(inventory["actual_terminal_result_count"], 0)
        self.assertEqual(
            [item["row_id"] for item in inventory["result_rows"]],
            [row["row_id"] for row in matrix["rows"]],
        )
        for item in inventory["result_rows"]:
            self.assertEqual(item["status"], "not_executed_no_action")
            self.assertIsNone(item["terminal_status"])
            self.assertFalse(item["normalization_applied"])
            self.assertFalse(item["repair_attempted"])
            self.assertFalse(item["fallback_attempted"])

    def test_opening_gate_marks_no_action_work_complete_but_real_gates_false(self) -> None:
        gate = self.payload["opening_gate"]
        self.assertEqual(gate["status"], "closed_all_no_action_work_complete")
        for key in (
            "p5_00_phase4_intake_complete",
            "p5_01_foundation_complete",
            "p5_02_opaque_custody_interface_complete",
            "p5_03_full_matrix_template_complete",
        ):
            self.assertTrue(gate["conditions"][key])
        for key, value in gate["conditions"].items():
            if not key.startswith("p5_"):
                self.assertFalse(value)
        for key, value in gate.items():
            if key.endswith("_allowed"):
                self.assertFalse(value)

    def test_replay_is_empty_and_incomplete_if_any_main_row_is_unterminal(self) -> None:
        replay = self.payload["replay"]
        self.assertEqual(replay["status"], "not_executed_no_action")
        self.assertEqual(replay["expected_main_matrix_rows"], 140)
        self.assertEqual(replay["terminal_main_matrix_rows"], 0)
        self.assertTrue(replay["incomplete_experiment_if_any_row_unterminal"])
        self.assertFalse(replay["h1_opened"])
        self.assertFalse(replay["h1_used_test_set"])
        self.assertFalse(replay["model_invoked"])
        self.assertFalse(replay["external_action_occurred"])
        self.assertFalse(replay["training_executed"])
        self.assertFalse(replay["formal_quality_claimed"])

    def test_fixture_rejects_count_coverage_and_scope_drift(self) -> None:
        wrong_scope = _fixture()
        wrong_scope["fixture_kind"] = "real_h1"
        with self.assertRaises(ValueError):
            build_phase5_formal_no_action_plan(self.foundation, wrong_scope)

        missing_slot = _fixture()
        missing_slot["slots"] = missing_slot["slots"][:-1]
        with self.assertRaisesRegex(ValueError, "12 core and 8 reserve"):
            build_phase5_formal_no_action_plan(self.foundation, missing_slot)

        missing_coverage = _fixture()
        for slot in missing_coverage["slots"]:
            slot["coverage_tags"] = [
                tag for tag in slot["coverage_tags"] if tag != "settings_admin"
            ]
        with self.assertRaisesRegex(ValueError, "coverage"):
            build_phase5_formal_no_action_plan(self.foundation, missing_coverage)

    def test_action_tampering_is_rejected_after_root_rebind(self) -> None:
        tampered = copy.deepcopy(self.payload)
        tampered["opening_gate"]["h1_open_allowed"] = True
        gate_body = {
            key: value
            for key, value in tampered["opening_gate"].items()
            if key != "opening_gate_id"
        }
        tampered["opening_gate"]["opening_gate_id"] = _record_id(
            "phase5-formal-opening-gate",
            gate_body,
        )
        _rebind_root(tampered)
        with self.assertRaisesRegex(ValueError, "must remain false"):
            Phase5FormalNoActionPlan.from_dict(tampered)

    def test_noncanonical_plan_json_is_rejected(self) -> None:
        pretty = json.dumps(self.payload, ensure_ascii=False, indent=2).encode()
        with self.assertRaisesRegex(ValueError, "not canonical"):
            Phase5FormalNoActionPlan.from_json_bytes(pretty)

    def test_no_requirement_or_gold_payload_is_serialized(self) -> None:
        text = self.plan.canonical_json
        self.assertNotIn("requirement_text", text)
        self.assertNotIn("gold_label", text)
        self.assertNotIn("provider_raw_response", text)
        self.assertNotIn("real_h1_content", text)


class Phase5PublicationScopeDescriptorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.descriptor = build_phase5_publication_scope_descriptor()

    def test_scope_records_four_cases_three_conditions_and_forty_eight_calls(self) -> None:
        self.assertEqual(
            self.descriptor["schema_version"],
            PUBLICATION_SCOPE_DESCRIPTOR_SCHEMA_VERSION,
        )
        scope = self.descriptor["publication_scope"]
        self.assertEqual(scope["core_case_count"], 4)
        self.assertEqual(
            scope["condition_ids"],
            ["none", "irrelevant_evidence", "remove_critical_role"],
        )
        self.assertEqual(scope["model_node_ids"], ["F1", "F2", "F3", "F4"])
        self.assertEqual(scope["runtime_row_count"], 12)
        self.assertEqual(scope["node_generate_call_cap"], 48)
        self.assertEqual(
            scope["condition_semantics_status"],
            "operator_semantics_frozen_payloads_pending",
        )

    def test_case_slots_are_structurally_complementary_and_selected_preoutput(self) -> None:
        contract = self.descriptor["case_selection_contract"]
        self.assertEqual(
            [slot["slot_id"] for slot in contract["slots"]],
            [
                "information_retrieval",
                "structured_form_transaction",
                "background_async_processing",
                "stateful_recovery_responsive",
            ],
        )
        self.assertIn(
            "selection_before_any_candidate_output",
            contract["selection_rules"],
        )
        self.assertIn(
            "no_selection_by_prior_model_success_or_expected_gain",
            contract["selection_rules"],
        )
        self.assertIn(
            "project_authored_english_only",
            contract["selection_rules"],
        )

    def test_condition_operators_freeze_operations_but_not_payloads(self) -> None:
        contract = self.descriptor["condition_operator_contract"]
        operators = {
            item["condition_id"]: item for item in contract["operators"]
        }
        self.assertEqual(
            operators["none"]["operation"],
            "no_evidence_intervention",
        )
        self.assertEqual(
            operators["irrelevant_evidence"]["operation"],
            "append_one_preregistered_schema_valid_irrelevant_evidence_item",
        )
        self.assertEqual(
            operators["remove_critical_role"]["operation"],
            "remove_one_preregistered_critical_evidence_role",
        )
        self.assertFalse(
            operators["irrelevant_evidence"]["actual_payload_frozen"]
        )
        self.assertFalse(
            operators["remove_critical_role"]["actual_role_frozen"]
        )
        self.assertIn(
            "no_result_driven_operator_change",
            contract["shared_rules"],
        )

    def test_accounting_inherits_definitions_but_not_numeric_thresholds(self) -> None:
        contract = self.descriptor["accounting_and_stop_contract"]
        self.assertTrue(contract["metric_definitions_frozen"])
        self.assertFalse(contract["numeric_minimum_gain_thresholds_frozen"])
        self.assertEqual(
            contract["numeric_threshold_status"],
            "pending_owner_decision",
        )
        self.assertTrue(contract["generate_start_consumes_call"])
        self.assertFalse(contract["automatic_retry_allowed"])
        self.assertFalse(contract["budget_reset_allowed"])
        self.assertIn(
            "result_driven_case_condition_prompt_schema_metric_or_threshold_change",
            contract["stop_rules"],
        )

    def test_historical_full_plan_remains_unchanged(self) -> None:
        historical = self.descriptor["historical_plan_binding"]
        self.assertEqual(historical["core_case_count"], 12)
        self.assertEqual(historical["reserve_case_count"], 8)
        self.assertEqual(historical["deep_label_case_count"], 4)
        self.assertEqual(historical["main_matrix_row_count"], 140)
        self.assertEqual(historical["node_generate_call_cap"], 240)
        self.assertTrue(historical["unchanged"])

    def test_reserve_replaces_only_preopening_invalid_cases(self) -> None:
        reserve = self.descriptor["reserve_policy"]
        self.assertEqual(reserve["reserve_case_cap"], 2)
        self.assertTrue(reserve["replacement_only"])
        self.assertTrue(reserve["invalidity_declared_before_opening"])
        self.assertFalse(reserve["adds_success_sample"])
        self.assertFalse(reserve["adds_runtime_rows"])
        self.assertFalse(reserve["adds_generate_calls"])

    def test_real_content_and_every_action_gate_remain_closed(self) -> None:
        sealed = self.descriptor["sealed_content_state"]
        self.assertTrue(
            all(
                sealed[key] is False
                for key in (
                    "real_case_content_present",
                    "gold_content_present",
                    "case_identities_frozen",
                    "condition_payloads_frozen",
                    "critical_role_ids_frozen",
                    "numeric_thresholds_frozen",
                    "execution_package_frozen",
                )
            )
        )
        self.assertTrue(sealed["metric_definitions_frozen"])
        self.assertTrue(
            all(value is False for value in self.descriptor["action_gates"].values())
        )
        canonical = _canonical(self.descriptor).decode("utf-8")
        self.assertNotIn("requirement_text", canonical)
        self.assertNotIn("gold_label", canonical)
        self.assertNotIn("provider_raw_response", canonical)

    def test_scope_or_authority_tampering_is_rejected(self) -> None:
        wrong_scope = copy.deepcopy(self.descriptor)
        wrong_scope["publication_scope"]["node_generate_call_cap"] = 49
        body = {
            key: value for key, value in wrong_scope.items() if key != "descriptor_id"
        }
        wrong_scope["descriptor_id"] = _record_id("phase5-publication-scope", body)
        with self.assertRaisesRegex(ValueError, "node_generate_call_cap drifted"):
            validate_phase5_publication_scope_descriptor(wrong_scope)

        opened = copy.deepcopy(self.descriptor)
        opened["action_gates"]["h1_opened"] = True
        body = {key: value for key, value in opened.items() if key != "descriptor_id"}
        opened["descriptor_id"] = _record_id("phase5-publication-scope", body)
        with self.assertRaisesRegex(ValueError, "must remain false"):
            validate_phase5_publication_scope_descriptor(opened)

        operator_drift = copy.deepcopy(self.descriptor)
        operator_drift["condition_operator_contract"]["operators"][1][
            "item_count"
        ] = 2
        body = {
            key: value
            for key, value in operator_drift.items()
            if key != "descriptor_id"
        }
        operator_drift["descriptor_id"] = _record_id(
            "phase5-publication-scope",
            body,
        )
        with self.assertRaisesRegex(ValueError, "condition-operator contract drifted"):
            validate_phase5_publication_scope_descriptor(operator_drift)


if __name__ == "__main__":
    unittest.main()
