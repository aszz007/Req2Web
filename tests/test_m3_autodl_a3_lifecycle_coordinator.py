from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a1_executor as executor
import req2web_runtime.autodl_a3_action_time_coordinator as action_coordinator
import req2web_runtime.autodl_a3_action_time_issuer as issuer
import req2web_runtime.autodl_a3_closeout as closeout
import req2web_runtime.autodl_a3_lifecycle_coordinator as lifecycle
import req2web_runtime.autodl_a3_restricted_executor_contract as restricted
from tests.test_m3_autodl_action_time_authority import make_fixture


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def reidentify(value, key, prefix):
    root = {name: item for name, item in value.items() if name != key}
    value[key] = prefix + hashlib.sha256(canonical(root)).hexdigest()[:20]


class A3LifecycleCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner, cls.gate_result, cls.base_bindings = make_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.owner.tearDown()

    def bindings(self, *, issued="2029-12-31T23:00:00Z", expires="2030-01-01T01:00:00Z"):
        value = deepcopy(self.base_bindings)
        value["signature_profile"]["backend"] = "openssh_keygen_y_sign_verify"
        gate = __import__(
            "req2web_runtime.autodl_a1_production_gate", fromlist=["_"]
        )
        snapshot = gate._read_a1_production_gate_result_owning_snapshot(
            self.gate_result
        )
        value["action_time_git_sha"] = snapshot["action_time_git_sha"]
        value["deployment_manifest"]["source_commit_sha"] = snapshot[
            "action_time_git_sha"
        ]
        value["profile"] = {
            "profile_name": "quality_experiment",
            "dtype": "bf16",
            "quantization": "none",
            "selected_device": "unresolved_no_action",
            "selection_basis": "local_protocol_preparation_only",
            "override": False,
        }
        value["nonce"]["issued_at_utc"] = issued
        value["nonce"]["expires_at_utc"] = expires
        return value

    def intent(self, values):
        return issuer.prepare_action_time_issuance_intent_no_action(
            self.gate_result, "a1_failure_cleanup", values
        )

    def handoff(self, values):
        coordinator_values = deepcopy(values)
        coordinator_values.pop("profile", None)
        coordinator_values["signature_profile"] = deepcopy(
            self.base_bindings["signature_profile"]
        )
        return action_coordinator.prepare_a3_action_time_handoff_no_action(
            self.gate_result, "a1_failure_cleanup", coordinator_values
        )

    def executor_binding(self):
        digest = lifecycle._digest_for_tests
        identity = lambda value: {
            "identity": value,
            "identity_sha256": digest(value.encode("utf-8")),
        }
        return {
            "bootstrap_identity": identity("bootstrap-no-action"),
            "restricted_executor_identity": identity(
                "restricted-executor-no-action"
            ),
            "worker_id_sha256": digest(b"worker-v1"),
            "gpu_id_sha256": digest(b"gpu-unresolved-no-action"),
            "fixed_command_sha256": digest(b"fixed-command-v1"),
            "capsule_command_sha256": digest(b"capsule-command-v1"),
        }

    def source_chain(self, values=None):
        selected = values or self.bindings()
        intent = self.intent(selected)
        handoff = self.handoff(selected)
        contract = lifecycle.prepare_lifecycle_bound_restricted_executor_contract_no_action(
            intent, handoff, self.executor_binding()
        )
        return intent, handoff, contract

    def closeout_artifacts(self, *, incomplete=False):
        report = self.gate_result.report.to_dict()
        receipt = self.owner.executor_receipt
        policy = closeout.create_a3_closeout_policy()
        trigger = closeout.create_a3_closeout_trigger(
            policy,
            report["plan_id"],
            report["plan_sha256"],
            receipt.to_dict()["receipt_id"],
            receipt.sha256(),
            "2026-07-25T00:00:00Z",
        )
        process = closeout.create_a3_process_termination_evidence(
            policy, trigger,
            source_artifact_id="observer-process",
            source_artifact_sha256="3" * 64,
            observed_at_utc="2026-07-25T00:10:00Z",
            term_sent=True, grace_seconds=30, kill_sent=False,
            pid_sha256="4" * 64, pgid_sha256="5" * 64,
            cgroup_sha256="6" * 64, post_process_count=0,
            post_gpu_process_count=0, post_listener_count=0,
        )
        deletion = closeout.create_a3_project_side_deletion_evidence(
            policy, trigger,
            source_artifact_id="observer-deletion",
            source_artifact_sha256="7" * 64,
            observed_at_utc="2026-07-25T00:20:00Z",
            pre_inventory_sha256="8" * 64, pre_file_count=16,
            pre_total_bytes=19329393661, post_residual_file_count=0,
            post_residual_total_bytes=0,
        )
        release = closeout.create_a3_instance_release_evidence(
            policy, trigger,
            source_artifact_id="observer-release",
            source_artifact_sha256="9" * 64,
            observed_at_utc="2026-07-25T00:30:00Z",
            instance_id_sha256="a" * 64,
            release_request_sha256="b" * 64,
            control_plane_state="stopped" if incomplete else "released",
            storage_state="retained" if incomplete else "released",
            billing_state="stopped" if incomplete else "not_running",
        )
        revocation = closeout.create_a3_access_revocation_evidence(
            policy, trigger,
            source_artifact_id="observer-revocation",
            source_artifact_sha256="c" * 64,
            observed_at_utc="2026-07-25T00:40:00Z",
            credential_fingerprint_sha256="d" * 64,
            remote_access_state="removed",
            local_ephemeral_key_state="removed",
            instance_release_state="released",
            instance_access_state="inaccessible",
        )
        bundle = closeout.create_a3_closeout_evidence_bundle(
            policy, trigger, [process, deletion, release, revocation]
        )
        return bundle, closeout.evaluate_a3_closeout_readiness(bundle)

    def coordinate(self, *, values=None, incomplete=False, include_closeout=True):
        intent, handoff, contract = self.source_chain(values)
        bundle = receipt = None
        if include_closeout:
            bundle, receipt = self.closeout_artifacts(incomplete=incomplete)
        record = lifecycle.coordinate_a1_a2_lifecycle_no_action(
            intent,
            handoff,
            contract,
            self.gate_result,
            self.owner.executor_receipt,
            bundle,
            receipt,
        )
        return record, intent, handoff, contract

    def test_failure_and_incomplete_or_missing_closeout_require_a3(self):
        for incomplete, include in ((True, True), (False, False)):
            record, _, _, _ = self.coordinate(
                incomplete=incomplete, include_closeout=include
            )
            data = record.to_dict()
            self.assertEqual(data["status"], "a3_closeout_required_no_action")
            self.assertTrue(data["a3_closeout_required"])
            self.assertFalse(data["separate_a2_gate_required"])
            self.assertIn("a1_failure_or_non_manager_result", data["reason_codes"])
            expected = (
                "a3_closeout_evidence_incomplete"
                if include else "a3_closeout_artifacts_missing"
            )
            self.assertIn(expected, data["reason_codes"])
            for key in (
                "manager_consumable", "cleanup_complete", "next_run_allowed",
                "a2_unlocked", "external_action_authorized",
                "external_action_executed", "provider_invoked",
                "project_data_transferred", "h1_allowed",
                "formal_quality_allowed",
            ):
                self.assertFalse(data[key])
            replay = lifecycle.validate_a3_lifecycle_coordination_bytes(
                record.canonical_bytes()
            )
            self.assertEqual(replay.to_dict(), data)

    def test_complete_structural_closeout_cannot_upgrade_non_manager_a1(self):
        record, _, _, _ = self.coordinate()
        data = record.to_dict()
        self.assertEqual(
            data["source_closeout_status"],
            "a3_structural_evidence_ready_not_manager_consumable",
        )
        self.assertEqual(data["status"], "a3_closeout_required_no_action")
        self.assertEqual(data["reason_codes"], ["a1_failure_or_non_manager_result"])
        self.assertFalse(data["a2_unlocked"])
        self.assertFalse(data["manager_consumable"])

    def test_expired_live_intent_is_forced_to_closeout(self):
        values = self.bindings(
            issued="2019-12-31T23:00:00Z",
            expires="2020-01-01T01:00:00Z",
        )
        record, _, _, _ = self.coordinate(values=values)
        data = record.to_dict()
        self.assertEqual(data["status"], "a3_closeout_required_no_action")
        self.assertIn("action_time_intent_expired", data["reason_codes"])
        self.assertFalse(data["a2_unlocked"])

    def test_manager_pass_classification_is_separate_gate_and_never_a2(self):
        status, reasons = lifecycle._classify_lifecycle_state_for_tests(
            True, True, True, "a1_passed_closeout",
            "a1_passed_awaiting_independent_gate",
            "a3_structural_evidence_ready_not_manager_consumable",
            False, [],
        )
        self.assertEqual(
            status, "a2_candidate_requires_separate_gate_no_action"
        )
        self.assertEqual(
            reasons,
            [
                "a3_structural_evidence_not_manager_authority",
                "separate_a2_gate_required",
            ],
        )
        expired_status, expired_reasons = lifecycle._classify_lifecycle_state_for_tests(
            True, True, True, "a1_passed_closeout",
            "a1_passed_awaiting_independent_gate",
            "a3_structural_evidence_ready_not_manager_consumable",
            True, [],
        )
        self.assertEqual(expired_status, "a3_closeout_required_no_action")
        self.assertIn("action_time_intent_expired", expired_reasons)

    def test_replayed_intent_or_restricted_contract_is_rejected(self):
        values = self.bindings()
        intent, handoff, contract = self.source_chain(values)
        replayed_intent = issuer.validate_action_time_issuance_intent_bytes(
            intent.canonical_bytes()
        )
        with self.assertRaisesRegex(
            issuer.ActionTimeIssuanceError, "replay_bytes_not_ledger_eligible"
        ):
            lifecycle.prepare_lifecycle_bound_restricted_executor_contract_no_action(
                replayed_intent, handoff, self.executor_binding()
            )
        replayed_contract = restricted.validate_a3_restricted_executor_contract_bytes(
            contract.canonical_bytes()
        )
        bundle, receipt = self.closeout_artifacts()
        with self.assertRaisesRegex(
            TypeError, "restricted_contract_not_lifecycle_registered"
        ):
            lifecycle.coordinate_a1_a2_lifecycle_no_action(
                intent, handoff, replayed_contract, self.gate_result,
                self.owner.executor_receipt, bundle, receipt,
            )

    def test_cross_bundle_and_execution_receipt_mismatch_fail_closed(self):
        first = self.bindings()
        second = self.bindings(expires="2030-01-01T02:00:00Z")
        first_intent, _, _ = self.source_chain(first)
        _, second_handoff, second_contract = self.source_chain(second)
        bundle, receipt = self.closeout_artifacts()
        with self.assertRaisesRegex(
            lifecycle.A3LifecycleCoordinationError,
            "restricted_source_cross_binding_invalid",
        ):
            lifecycle.coordinate_a1_a2_lifecycle_no_action(
                first_intent, second_handoff, second_contract, self.gate_result,
                self.owner.executor_receipt, bundle, receipt,
            )

        valid = self.owner.executor_receipt.to_dict()
        valid["control_sha256"] = "e" * 64
        reidentify(valid, "receipt_id", "real-a1-execution-receipt-")
        mismatch = executor.A1ExecutionReceipt.from_dict(valid)
        intent, handoff, contract = self.source_chain(first)
        record = lifecycle.coordinate_a1_a2_lifecycle_no_action(
            intent, handoff, contract, self.gate_result, mismatch, bundle, receipt
        )
        self.assertEqual(record.to_dict()["status"], "a3_closeout_required_no_action")
        self.assertIn(
            "a1_execution_receipt_mismatch", record.to_dict()["reason_codes"]
        )

    def test_bytes_only_route_rejects_any_separate_gate_candidate(self):
        record, intent, handoff, contract = self.coordinate()
        bundle, receipt = self.closeout_artifacts()
        replay = lifecycle.validate_a3_lifecycle_coordination_bytes(
            record.canonical_bytes()
        )
        self.assertEqual(
            replay.to_dict()["status"], "a3_closeout_required_no_action"
        )
        self.assertTrue(replay.to_dict()["a3_closeout_required"])
        self.assertTrue(lifecycle.validate_a3_lifecycle_coordination_against(
            replay, intent, handoff, contract, self.gate_result,
            self.owner.executor_receipt, bundle, receipt,
        ))

        separate = replay.to_dict()
        separate["status"] = "a2_candidate_requires_separate_gate_no_action"
        separate["reason_codes"] = [
            "a3_structural_evidence_not_manager_authority",
            "separate_a2_gate_required",
        ]
        separate["trigger_kind"] = "a1_passed_closeout"
        separate["source_manager_authority"] = True
        separate["source_manager_a1_passed"] = True
        separate["source_a2_unlocked"] = True
        separate["source_execution_status"] = (
            "a1_passed_awaiting_independent_gate"
        )
        separate["source_closeout_status"] = (
            "a3_structural_evidence_ready_not_manager_consumable"
        )
        separate["a3_closeout_required"] = False
        separate["separate_a2_gate_required"] = True
        reidentify(separate, "record_id", "a3-lifecycle-")
        with self.assertRaisesRegex(
            lifecycle.A3LifecycleCoordinationError,
            "separate_gate_bytes_replay_forbidden",
        ):
            lifecycle.validate_a3_lifecycle_coordination_bytes(
                canonical(separate)
            )

    def test_genuine_separate_shape_cannot_be_rehydrated_from_bytes(self):
        record, _, _, _ = self.coordinate()
        separate = record.to_dict()
        separate.update({
            "status": "a2_candidate_requires_separate_gate_no_action",
            "reason_codes": [
                "a3_structural_evidence_not_manager_authority",
                "separate_a2_gate_required",
            ],
            "trigger_kind": "a1_passed_closeout",
            "source_manager_authority": True,
            "source_manager_a1_passed": True,
            "source_a2_unlocked": True,
            "source_execution_status": "a1_passed_awaiting_independent_gate",
            "source_closeout_status": (
                "a3_structural_evidence_ready_not_manager_consumable"
            ),
            "a3_closeout_required": False,
            "separate_a2_gate_required": True,
        })
        reidentify(separate, "record_id", "a3-lifecycle-")
        raw = canonical(separate)
        with self.assertRaisesRegex(
            lifecycle.A3LifecycleCoordinationError,
            "separate_gate_bytes_replay_forbidden",
        ):
            lifecycle.A3LifecycleCoordinationRecord.from_bytes(raw)

    def test_forged_record_constructor_subclass_and_authority_flags_reject(self):
        record, _, _, _ = self.coordinate()
        forged = record.to_dict()
        forged["a2_unlocked"] = True
        reidentify(forged, "record_id", "a3-lifecycle-")
        with self.assertRaises(lifecycle.A3LifecycleCoordinationError):
            lifecycle.validate_a3_lifecycle_coordination_bytes(canonical(forged))
        with self.assertRaisesRegex(TypeError, "factory_required"):
            lifecycle.A3LifecycleCoordinationRecord()
        class EqualRecord(lifecycle.A3LifecycleCoordinationRecord):
            __slots__ = ()
        with self.assertRaisesRegex(TypeError, "exact_type_required"):
            EqualRecord.from_bytes(record.canonical_bytes())
        parsed = lifecycle.validate_a3_lifecycle_coordination_bytes(
            record.canonical_bytes()
        )
        self.assertFalse(parsed.to_dict()["a2_unlocked"])
        self.assertFalse(parsed.to_dict()["manager_consumable"])

    def test_definition_time_module_and_descriptor_captures_are_stable(self):
        values = self.bindings()
        intent, handoff, contract = self.source_chain(values)
        bundle, receipt = self.closeout_artifacts()
        saved_issuer = lifecycle.issuer_module
        saved_restricted = lifecycle.restricted_module
        original_request = action_coordinator.A3ActionTimeCoordinatorResult.request
        original_to_dict = restricted.A3RestrictedExecutorContract.to_dict
        try:
            lifecycle.issuer_module = object()
            lifecycle.restricted_module = object()
            action_coordinator.A3ActionTimeCoordinatorResult.request = property(
                lambda self: object()
            )
            restricted.A3RestrictedExecutorContract.to_dict = lambda self: {
                "a2_unlocked": True
            }
            record = lifecycle.coordinate_a1_a2_lifecycle_no_action(
                intent, handoff, contract, self.gate_result,
                self.owner.executor_receipt, bundle, receipt,
            )
            self.assertFalse(record.to_dict()["a2_unlocked"])
        finally:
            lifecycle.issuer_module = saved_issuer
            lifecycle.restricted_module = saved_restricted
            action_coordinator.A3ActionTimeCoordinatorResult.request = original_request
            restricted.A3RestrictedExecutorContract.to_dict = original_to_dict

    def test_public_api_has_no_authority_or_expected_overrides(self):
        self.assertEqual(
            tuple(inspect.signature(
                lifecycle.coordinate_a1_a2_lifecycle_no_action
            ).parameters),
            (
                "intent", "coordinator_result", "restricted_contract",
                "gate_result", "execution_receipt", "closeout_bundle",
                "closeout_receipt",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(
                lifecycle.validate_a3_lifecycle_coordination_against
            ).parameters),
            (
                "record", "intent", "coordinator_result",
                "restricted_contract", "gate_result", "execution_receipt",
                "closeout_bundle", "closeout_receipt",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(
                lifecycle.prepare_lifecycle_bound_restricted_executor_contract_no_action
            ).parameters),
            ("intent", "coordinator_result", "executor_binding"),
        )
        values = self.bindings()
        intent = self.intent(values)
        handoff = self.handoff(values)
        ordinary = restricted.create_a3_restricted_executor_contract(
            intent, handoff, self.executor_binding()
        )
        bundle, receipt = self.closeout_artifacts()
        with self.assertRaisesRegex(
            TypeError, "restricted_contract_not_lifecycle_registered"
        ):
            lifecycle.coordinate_a1_a2_lifecycle_no_action(
                intent, handoff, ordinary, self.gate_result,
                self.owner.executor_receipt, bundle, receipt,
            )

    def test_no_runtime_action_surface_is_present(self):
        source = Path(lifecycle.__file__).read_text(encoding="utf-8").lower()
        for token in (
            "subprocess", "socket", "requests", "urllib", "paramiko",
            "ssh-keygen", "autodl.com", "transformers",
        ):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
