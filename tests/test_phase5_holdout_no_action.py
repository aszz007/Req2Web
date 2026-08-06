from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_evaluation.phase5_holdout_no_action import (  # noqa: E402
    PHASE4_EXIT_COMMIT,
    PHASE4_EXIT_HANDOFF_SHA256,
    PHASE4_STABILITY_RUN_ID,
    PHASE4_STABILITY_SUMMARY_CANONICAL_SHA256,
    PHASE4_STABILITY_SUMMARY_FILE_SHA256,
    PHASE5_NO_ACTION_BUNDLE_SCHEMA_VERSION,
    Phase5NoActionBundle,
    build_synthetic_phase5_no_action_bundle,
    build_synthetic_phase5_no_action_bundle_from_json_bytes,
)


FIXTURE_PATH = ROOT / "fixtures" / "phase5_holdout_no_action_synthetic_v1.json"
HANDOFF_PATH = ROOT / "docs" / "phase4_exit_phase5_entry_handoff.md"
CONTRACT_PATH = ROOT / "docs" / "phase5_formal_holdout_no_action_contract.md"
MODULE_PATH = ROOT / "src" / "req2web_evaluation" / "phase5_holdout_no_action.py"
GITATTRIBUTES_PATH = ROOT / ".gitattributes"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _record_id(prefix: str, value: object) -> str:
    return f"{prefix}-{hashlib.sha256(_canonical_json_bytes(value)).hexdigest()}"


def _fixture_dict() -> dict[str, object]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("fixture root must be an object")
    return value


def _rebind_root_id(value: dict[str, object]) -> None:
    body = {key: item for key, item in value.items() if key != "bundle_id"}
    value["bundle_id"] = _record_id("phase5-no-action-bundle", body)


def _rebind_record_id(
    value: dict[str, object],
    *,
    id_key: str,
    prefix: str,
) -> None:
    body = {key: item for key, item in value.items() if key != id_key}
    value[id_key] = _record_id(prefix, body)


class Phase5HoldoutNoActionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = build_synthetic_phase5_no_action_bundle_from_json_bytes(
            FIXTURE_PATH.read_bytes()
        )
        self.payload = self.bundle.to_dict()

    def test_authoritative_phase4_exit_is_bound_without_opening_action(self) -> None:
        declaration = self.payload["declaration"]
        self.assertEqual(declaration["status"], "entered_no_action_foundation")
        self.assertEqual(declaration["phase4_exit_commit"], PHASE4_EXIT_COMMIT)
        self.assertTrue(declaration["phase4_exit_handoff_present"])
        self.assertEqual(
            declaration["phase4_exit_handoff_sha256"],
            PHASE4_EXIT_HANDOFF_SHA256,
        )
        self.assertEqual(
            declaration["phase4_stability_summary_file_sha256"],
            PHASE4_STABILITY_SUMMARY_FILE_SHA256,
        )
        self.assertEqual(
            declaration["phase4_stability_summary_canonical_sha256"],
            PHASE4_STABILITY_SUMMARY_CANONICAL_SHA256,
        )
        self.assertEqual(
            declaration["phase4_stability_run_id"],
            PHASE4_STABILITY_RUN_ID,
        )
        for key in (
            "real_h1_read_authorized",
            "real_gold_read_authorized",
            "holdout_selection_authorized",
            "holdout_annotation_authorized",
            "holdout_execution_authorized",
            "model_action_authorized",
            "external_action_authorized",
            "training_authorized",
            "h1_driven_system_change_authorized",
        ):
            self.assertFalse(declaration[key])

    def test_live_handoff_hash_matches_the_bound_authority(self) -> None:
        self.assertTrue(HANDOFF_PATH.is_file())
        self.assertEqual(
            hashlib.sha256(HANDOFF_PATH.read_bytes()).hexdigest(),
            PHASE4_EXIT_HANDOFF_SHA256,
        )
        attribute_lines = {
            line.strip()
            for line in GITATTRIBUTES_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn(
            "docs/phase4_exit_phase5_entry_handoff.md -text",
            attribute_lines,
        )

    def test_bundle_is_canonical_replayable_and_hash_stable(self) -> None:
        self.assertEqual(
            self.payload["schema_version"],
            PHASE5_NO_ACTION_BUNDLE_SCHEMA_VERSION,
        )
        replayed = Phase5NoActionBundle.from_json_bytes(
            self.bundle.canonical_json_bytes()
        )
        rebuilt = build_synthetic_phase5_no_action_bundle(_fixture_dict())
        self.assertEqual(replayed.to_dict(), self.payload)
        self.assertEqual(replayed.sha256(), self.bundle.sha256())
        self.assertEqual(rebuilt.sha256(), self.bundle.sha256())

    def test_noncanonical_bundle_bytes_are_rejected(self) -> None:
        noncanonical = json.dumps(self.payload, ensure_ascii=False, indent=2).encode(
            "utf-8"
        )
        with self.assertRaisesRegex(ValueError, "not canonical"):
            Phase5NoActionBundle.from_json_bytes(noncanonical)

    def test_exact_duplicate_audit_keeps_raw_text_out_of_bundle(self) -> None:
        audit = self.payload["duplicate_audit"]
        exact_pairs = [
            pair for pair in audit["pairs"] if pair["verdict"] == "exact_duplicate"
        ]
        self.assertEqual(
            [
                (
                    pair["left_subject_id"],
                    pair["right_subject_id"],
                )
                for pair in exact_pairs
            ],
            [("synthetic-core-01", "synthetic-core-01-duplicate")],
        )
        self.assertIsNone(audit["near_duplicate_threshold"])
        self.assertEqual(
            audit["threshold_status"],
            "development_calibration_required_before_real_holdout",
        )
        canonical_text = self.bundle.canonical_json
        self.assertNotIn("requirement_text", canonical_text)
        self.assertNotIn("Create a mobile checkout form", canonical_text)
        self.assertNotIn("Create a desktop media upload page", canonical_text)
        self.assertTrue(
            all(subject["raw_text_retained"] is False for subject in audit["subjects"])
        )
        adjudication = audit["manual_adjudication_interface"]
        self.assertEqual(
            adjudication["status"],
            "interface_only_no_real_subject_access",
        )
        self.assertFalse(adjudication["subject_content_visible"])
        self.assertFalse(adjudication["candidate_outputs_visible"])
        self.assertEqual(adjudication["entries"], [])

    def test_selected_manifest_excludes_duplicate_and_all_real_content(self) -> None:
        manifest = self.payload["holdout_manifest"]
        self.assertEqual(manifest["real_h1_case_count"], 0)
        self.assertEqual(manifest["real_gold_item_count"], 0)
        self.assertFalse(manifest["sealed"])
        self.assertFalse(manifest["h1_opened"])
        self.assertFalse(manifest["used_test_set"])
        self.assertEqual(
            [case["case_slot_id"] for case in manifest["cases"]],
            ["synthetic-core-01", "synthetic-core-02"],
        )
        for case in manifest["cases"]:
            self.assertFalse(case["real_content_present"])
            self.assertFalse(case["gold_content_present"])
            self.assertFalse(case["eligible_for_real_h1"])

    def test_matrix_freezes_indivisible_g1_g2_blocks_and_same_case_g0_dependencies(
        self,
    ) -> None:
        matrix = self.payload["evaluation_matrix"]
        rows = matrix["rows"]
        self.assertEqual(matrix["model_nodes"], ["F1", "F2", "F3", "F4"])
        self.assertFalse(matrix["real_h1_rows_present"])
        self.assertFalse(matrix["candidate_outputs_present"])
        self.assertEqual(len(rows), 6)
        for case_id in ("synthetic-core-01", "synthetic-core-02"):
            case_rows = [row for row in rows if row["case_slot_id"] == case_id]
            self.assertEqual({row["group"] for row in case_rows}, {"G0", "G1", "G2"})
            g0 = next(row for row in case_rows if row["group"] == "G0")
            g1 = next(row for row in case_rows if row["group"] == "G1")
            g2 = next(row for row in case_rows if row["group"] == "G2")
            self.assertEqual(g1["block_id"], g2["block_id"])
            self.assertEqual(g1["depends_on_block_ids"], [g0["block_id"]])
            self.assertEqual(g2["depends_on_block_ids"], [g0["block_id"]])
            self.assertEqual(g1["planned_initial_model_calls"], 4)
            self.assertEqual(g2["planned_initial_model_calls"], 0)
            self.assertEqual(g0["planned_initial_model_calls"], 0)
            self.assertTrue(
                matrix["block_order"].index(g0["block_id"])
                < matrix["block_order"].index(g1["block_id"])
            )
            self.assertTrue(all(row["actual_run_count"] == 0 for row in case_rows))
            self.assertTrue(
                all(row["status"] == "not_executed_no_action" for row in case_rows)
            )

    def test_budget_is_structural_no_retry_no_reset_and_not_approved_for_h1(
        self,
    ) -> None:
        policy = self.payload["execution_policy"]
        budget = policy["budget"]
        self.assertEqual(budget["initial_node_call_cap"], 8)
        self.assertEqual(budget["total_model_call_cap"], 8)
        self.assertEqual(budget["candidate_model_repair_call_cap"], 0)
        self.assertEqual(budget["fault_copy_model_repair_call_cap"], 0)
        self.assertEqual(budget["provider_retry_call_cap"], 0)
        self.assertEqual(budget["calls_consumed"], 0)
        self.assertTrue(budget["generate_start_consumes_call"])
        self.assertFalse(budget["automatic_retry_allowed"])
        self.assertFalse(budget["budget_reset_allowed"])
        self.assertFalse(budget["owner_approved_for_h1"])
        self.assertFalse(budget["model_action_authorized"])
        self.assertFalse(policy["result_driven_changes_allowed"])
        self.assertFalse(policy["real_execution_authorized"])
        self.assertEqual(
            policy["on_incomplete_main_matrix"],
            "incomplete_experiment_descriptive_only",
        )

    def test_artifact_inventory_is_raw_first_same_case_and_empty(self) -> None:
        inventory = self.payload["artifact_inventory"]
        self.assertEqual(
            inventory["status"],
            "template_only_no_runtime_artifacts",
        )
        self.assertTrue(inventory["g0_prefreeze_reference_required"])
        self.assertTrue(inventory["same_case_g0_only"])
        self.assertTrue(inventory["raw_first_required"])
        self.assertTrue(inventory["generate_start_consumes_attempt"])
        for key in (
            "automatic_retry_allowed",
            "budget_reset_allowed",
            "source_result_rewrite_allowed",
            "normalization_counts_as_raw_success",
            "repair_counts_as_raw_success",
            "replay_counts_as_raw_success",
            "composition_counts_as_raw_success",
            "fallback_counts_as_raw_success",
        ):
            self.assertFalse(inventory[key])
        self.assertEqual(
            [item["artifact_slot"] for item in inventory["artifacts"]],
            [
                "g0_prefreeze_reference",
                "provider_raw_response",
                "node_attempt_ledger",
                "candidate_chain",
                "gate_and_acceptance_reports",
                "result_package",
                "evaluation_result_row",
                "replay_receipt",
            ],
        )
        for artifact in inventory["artifacts"]:
            self.assertEqual(artifact["availability"], "not_created_no_action")
            self.assertIsNone(artifact["artifact_id"])
            self.assertIsNone(artifact["sha256"])
            self.assertIsNone(artifact["byte_length"])

    def test_h1_opening_gate_is_closed_except_completed_phase4_intake(self) -> None:
        gate = self.payload["opening_gate"]
        self.assertEqual(gate["status"], "closed_no_action")
        self.assertTrue(gate["conditions"]["phase4_exit_intake_complete"])
        self.assertTrue(
            all(
                value is False
                for key, value in gate["conditions"].items()
                if key != "phase4_exit_intake_complete"
            )
        )
        for key in (
            "h1_open_allowed",
            "real_h1_read_allowed",
            "real_gold_read_allowed",
            "holdout_execution_allowed",
            "model_action_allowed",
            "external_action_allowed",
            "training_allowed",
            "formal_quality_claim_allowed",
        ):
            self.assertFalse(gate[key])

    def test_custody_requires_dual_annotation_but_exposes_no_payload(self) -> None:
        custody = self.payload["custody"]
        self.assertTrue(custody["dual_annotation_required"])
        self.assertTrue(custody["adjudication_required"])
        self.assertFalse(custody["real_dual_annotation_complete"])
        self.assertFalse(custody["annotation_payload_exposed"])
        self.assertFalse(custody["read_authorized"])
        self.assertFalse(custody["selection_authorized"])
        self.assertFalse(custody["annotation_authorized"])
        self.assertFalse(custody["execution_authorized"])

    def test_metric_contract_preserves_missing_and_path_boundaries(self) -> None:
        metrics = self.payload["metric_contract"]
        self.assertEqual(metrics["status"], "definitions_only_not_executed")
        self.assertEqual(
            metrics["missing_outcomes"],
            ["fail", "not_supported", "unknown"],
        )
        self.assertTrue(metrics["not_supported_retained_in_applicable_denominator"])
        self.assertTrue(metrics["not_applicable_excluded_only_with_reason"])
        self.assertTrue(metrics["no_valid_candidate_attribution_coverage_zero"])
        self.assertTrue(metrics["case_macro_first"])
        self.assertTrue(metrics["candidate_output_cannot_define_gold_or_denominator"])
        self.assertFalse(metrics["path3_formal_h1_eligible"])
        self.assertFalse(metrics["path3_evidence_influence_metrics_applicable"])
        self.assertFalse(metrics["formal_metrics_executed"])
        self.assertFalse(metrics["real_gold_consumed"])

    def test_replay_skeleton_contains_no_results_or_quality_claim(self) -> None:
        replay = self.payload["replay_skeleton"]
        self.assertEqual(replay["status"], "not_executed_no_action")
        self.assertEqual(replay["result_rows"], [])
        self.assertTrue(replay["structural_preparation_only"])
        for key in (
            "h1_opened",
            "h1_used_test_set",
            "model_invoked",
            "gpu_used",
            "external_service_used",
            "training_executed",
            "formal_quality_claimed",
        ):
            self.assertFalse(replay[key])
        for key in (
            "raw_result_count",
            "normalization_count",
            "repair_count",
            "fallback_count",
            "failed_closed_count",
        ):
            self.assertEqual(replay[key], 0)

    def test_selected_exact_duplicate_is_rejected_before_manifest_creation(self) -> None:
        fixture = _fixture_dict()
        fixture["selected_case_ids"] = [
            "synthetic-core-01",
            "synthetic-core-01-duplicate",
        ]
        with self.assertRaisesRegex(ValueError, "exact duplicate"):
            build_synthetic_phase5_no_action_bundle(fixture)

    def test_fixture_rejects_non_synthetic_scope_and_unsorted_candidates(self) -> None:
        wrong_scope = _fixture_dict()
        wrong_scope["fixture_kind"] = "sealed_h1"
        with self.assertRaises(ValueError):
            build_synthetic_phase5_no_action_bundle(wrong_scope)

        unsorted = _fixture_dict()
        unsorted["candidates"] = list(reversed(unsorted["candidates"]))
        with self.assertRaisesRegex(ValueError, "sorted"):
            build_synthetic_phase5_no_action_bundle(unsorted)

    def test_action_or_real_content_injection_is_rejected_even_after_root_rebind(
        self,
    ) -> None:
        action = copy.deepcopy(self.payload)
        action["declaration"]["holdout_execution_authorized"] = True
        _rebind_root_id(action)
        with self.assertRaisesRegex(ValueError, "must remain false"):
            Phase5NoActionBundle.from_dict(action)

        exposure = copy.deepcopy(self.payload)
        exposure["custody"]["content_exposed"] = True
        _rebind_root_id(exposure)
        with self.assertRaisesRegex(ValueError, "must remain false"):
            Phase5NoActionBundle.from_dict(exposure)

        injected = copy.deepcopy(self.payload)
        injected["holdout_manifest"]["cases"][0]["requirement_text"] = "forbidden"
        _rebind_root_id(injected)
        with self.assertRaises(ValueError):
            Phase5NoActionBundle.from_dict(injected)

    def test_duplicate_pair_flags_are_recomputed_from_subject_hashes(self) -> None:
        tampered = copy.deepcopy(self.payload)
        pair = next(
            pair
            for pair in tampered["duplicate_audit"]["pairs"]
            if pair["left_subject_id"] == "synthetic-core-01"
            and pair["right_subject_id"] == "synthetic-core-01-duplicate"
        )
        pair["exact_text_match"] = False
        pair["verdict"] = "unadjudicated_no_threshold"
        _rebind_record_id(
            tampered["duplicate_audit"],
            id_key="audit_id",
            prefix="phase5-duplicate-audit",
        )
        _rebind_root_id(tampered)
        with self.assertRaisesRegex(ValueError, "pair flags"):
            Phase5NoActionBundle.from_dict(tampered)

    def test_manifest_commitment_is_recomputed_from_audit_and_coverage(self) -> None:
        tampered = copy.deepcopy(self.payload)
        tampered["holdout_manifest"]["cases"][0]["case_commitment_sha256"] = "0" * 64
        _rebind_record_id(
            tampered["holdout_manifest"],
            id_key="manifest_id",
            prefix="phase5-holdout-manifest",
        )
        _rebind_root_id(tampered)
        with self.assertRaisesRegex(ValueError, "commitment binding"):
            Phase5NoActionBundle.from_dict(tampered)

    def test_matrix_route_seed_and_block_are_recomputed(self) -> None:
        tampered_route = copy.deepcopy(self.payload)
        g1 = next(
            row
            for row in tampered_route["evaluation_matrix"]["rows"]
            if row["group"] == "G1"
        )
        g1["artifact_route"] = "forged_route"
        _rebind_record_id(
            tampered_route["evaluation_matrix"],
            id_key="matrix_id",
            prefix="phase5-evaluation-matrix",
        )
        _rebind_root_id(tampered_route)
        with self.assertRaisesRegex(ValueError, "artifact_route"):
            Phase5NoActionBundle.from_dict(tampered_route)

        tampered_seed = copy.deepcopy(self.payload)
        g0 = next(
            row
            for row in tampered_seed["evaluation_matrix"]["rows"]
            if row["group"] == "G0"
        )
        g0["seed"] += 1
        _rebind_record_id(
            tampered_seed["evaluation_matrix"],
            id_key="matrix_id",
            prefix="phase5-evaluation-matrix",
        )
        _rebind_root_id(tampered_seed)
        with self.assertRaisesRegex(ValueError, "seed"):
            Phase5NoActionBundle.from_dict(tampered_seed)

    def test_p5_00_sync_has_no_stale_blocked_markers_in_candidate_files(self) -> None:
        stale_status = "no_action_foundation_" + "awaiting_phase4_exit_handoff"
        stale_absence = "phase4_exit_handoff_present=" + "false"
        stale_sentence = "handoff" + " 不存在"
        for path in (CONTRACT_PATH, MODULE_PATH, FIXTURE_PATH, Path(__file__)):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(stale_status, text, str(path))
            self.assertNotIn(stale_absence, text, str(path))
            self.assertNotIn(stale_sentence, text, str(path))


if __name__ == "__main__":
    unittest.main()
