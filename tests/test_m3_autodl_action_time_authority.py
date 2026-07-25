from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a1_production_gate as gate
import req2web_runtime.autodl_action_time_authority as authority
import req2web_runtime.autodl_a3_action_time_coordinator as coordinator
from tests import test_m3_autodl_a1_production_gate as gate_fixtures


H = "a" * 64
G = "b" * 40


def make_fixture():
    owner = gate_fixtures.A1ProductionGateTests("test_exact_committed_evidence_bundle_is_authority_neutral")
    owner.setUp()
    decision = owner.validate_saved_bundle()
    result = gate._make_synthetic_gate_result_for_tests(
        owner.report,
        owner.gate_receipt,
        owner.bundle,
        decision,
    )
    report = owner.report.to_dict()
    bindings = {
        "action_time_git_sha": G,
        "approved_disposition": {"id": "autodl-disposition-test", "sha256": H},
        "d17": {
            "manifest_id": "d17-manifest-test",
            "manifest_sha256": H,
            "manager_plan_id": "manager-plan-test",
            "manager_plan_sha256": H,
            "field_policy_sha256": H,
        },
        "deployment_manifest": {
            "manifest_id": "deployment-manifest-test",
            "manifest_sha256": H,
            "source_commit_sha": G,
            "package_tree_sha256": H,
        },
        "no_action_plan": {"id": "no-action-plan-test", "sha256": H},
        "operational_plan": {"id": report["plan_id"], "sha256": report["plan_sha256"]},
        "controls": {"id": report["control_id"], "sha256": report["control_sha256"]},
        "model_inventory": {
            "repository": "Qwen/Qwen3.5-9B",
            "revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
            "file_count": 16,
            "total_bytes": 19329393661,
            "tree_sha256": H,
        },
        "endpoint": {
            "instance_id_hash": H,
            "ssh_host_fingerprint_sha256": H,
            "executor_fingerprint_sha256": H,
            "credential_fingerprint_sha256": H,
        },
        "root_contracts": [
            {
                "role": role,
                "normalized_path_sha256": f"{index + 1:064x}",
                "marker_id": f"a3-{role}-marker-test",
                "marker_sha256": H,
                "inventory_tree_sha256": H,
            }
            for index, role in enumerate(("work", "cache", "log", "transfer", "evidence"))
        ],
        "a3": {
            "policy_id": "a3-policy-test",
            "policy_sha256": H,
            "trigger_id": "a3-trigger-test",
            "trigger_sha256": H,
            "cleanup_mode": "delete_work_cache_release_instance_revoke_access",
        },
        "executor_package": {
            "package_id": "a3-executor-package-test",
            "package_sha256": H,
            "tree_sha256": H,
        },
        "signature_profile": {
            "algorithm": "ed25519_openssh_detached",
            "format": "openssh_detached_signature_capsule_v1",
            "issuer_public_key_fingerprint_sha256": H,
            "capsule_version": "v1",
            "real_key_loaded": False,
            "signature_bytes_present": False,
        },
        "nonce": {
            "nonce_id": "action-time-nonce-test",
            "nonce_sha256": H,
            "issued_at_utc": "2026-07-25T10:00:00Z",
            "expires_at_utc": "2026-07-25T12:00:00Z",
            "single_use": True,
            "no_retry": True,
            "no_second_provision": True,
        },
    }
    return owner, result, bindings


class ActionTimeAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner, cls.gate_result, cls.bindings = make_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.owner.tearDown()

    def prepare(self, bindings=None):
        return coordinator.prepare_a3_action_time_handoff_no_action(
            self.gate_result,
            "a1_failure_cleanup",
            deepcopy(bindings or self.bindings),
        )

    def test_complete_gate_result_is_required_and_failure_never_unlocks_a2(self):
        result = self.prepare()
        request = result.request
        self.assertIs(type(request), authority.ActionTimeAuthorityRequest)
        self.assertEqual(request.data["trigger_kind"], "a1_failure_cleanup")
        self.assertFalse(request.data["a1"]["source_manager_a1_passed"])
        self.assertFalse(request.data["a1"]["source_a2_unlocked"])
        for key in (
            "permit_issued", "signature_verified", "destructive_action_authorized",
            "external_action_executed", "cleanup_complete", "manager_consumable",
            "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed",
        ):
            self.assertFalse(request.data[key])
        self.assertEqual(
            authority.ActionTimeAuthorityRequest.from_bytes(request.canonical_bytes()).to_dict(),
            request.to_dict(),
        )
        for substitute in (
            self.gate_result.decision,
            self.gate_result.report,
            self.gate_result.receipt,
            self.gate_result.bundle.canonical_bytes(),
        ):
            with self.assertRaisesRegex(
                coordinator.A3ActionTimeCoordinatorError,
                "gate_owner_generation_or_type_drift",
            ):
                coordinator.prepare_a3_action_time_handoff_no_action(
                    substitute,
                    "a1_failure_cleanup",
                    deepcopy(self.bindings),
                )

    def test_pass_trigger_rejects_non_manager_result(self):
        with self.assertRaisesRegex(
            TypeError,
            "a1_passed_trigger_requires_registered_manager_result",
        ):
            coordinator.prepare_a3_action_time_handoff_no_action(
                self.gate_result,
                "a1_passed_closeout",
                deepcopy(self.bindings),
            )

    def test_exact_manager_descriptor_rebinding_exploit_is_rejected(self):
        forged = object.__new__(gate.ValidatedA1ManagerDecision)
        targets = (
            (gate.ValidatedA1ManagerDecision, "status"),
            (gate.ValidatedA1ManagerDecision, "manager_a1_passed"),
            (gate.ValidatedA1ManagerDecision, "a2_unlocked"),
            (gate.A1ProductionGateResult, "decision"),
        )
        original = {(cls, name): vars(cls)[name] for cls, name in targets}
        try:
            gate.ValidatedA1ManagerDecision.status = property(
                lambda self: "validated_manager_a1_passed"
            )
            gate.ValidatedA1ManagerDecision.manager_a1_passed = property(
                lambda self: True
            )
            gate.ValidatedA1ManagerDecision.a2_unlocked = property(
                lambda self: True
            )
            gate.A1ProductionGateResult.decision = property(lambda self: forged)
            with self.assertRaisesRegex(
                TypeError,
                "a1_passed_trigger_requires_registered_manager_result",
            ):
                coordinator.prepare_a3_action_time_handoff_no_action(
                    self.gate_result,
                    "a1_passed_closeout",
                    deepcopy(self.bindings),
                )
        finally:
            for (cls, name), descriptor in original.items():
                setattr(cls, name, descriptor)
    def test_direct_constructor_mutation_and_root_drift_fail_closed(self):
        result = self.prepare()
        frozen = result.request.canonical_bytes()
        mutable = deepcopy(self.bindings)
        prepared = self.prepare(mutable)
        mutable["endpoint"]["instance_id_hash"] = "f" * 64
        mutable["root_contracts"][0]["marker_id"] = "mutated-marker-test"
        self.assertEqual(prepared.request.data["endpoint"]["instance_id_hash"], H)
        self.assertEqual(result.request.canonical_bytes(), frozen)

        forged = result.request.to_dict()
        forged["permit_issued"] = True
        with self.assertRaisesRegex(authority.ActionTimeAuthorityError, "action_time_public_authority_forbidden"):
            authority.validate_action_time_authority_request_bytes(authority._canonical_for_tests(forged))
        forged_identity = result.request.to_dict()
        forged_identity["request_id"] = "action-time-request-forged"
        with self.assertRaisesRegex(authority.ActionTimeAuthorityError, "action_time_request_identity_invalid"):
            authority.validate_action_time_authority_request_bytes(authority._canonical_for_tests(forged_identity))

        wrong_order = deepcopy(self.bindings)
        wrong_order["root_contracts"][0], wrong_order["root_contracts"][1] = (
            wrong_order["root_contracts"][1], wrong_order["root_contracts"][0]
        )
        with self.assertRaisesRegex(authority.ActionTimeAuthorityError, "action_time_root_role_order_invalid"):
            self.prepare(wrong_order)

        duplicate = deepcopy(self.bindings)
        duplicate["root_contracts"][1]["normalized_path_sha256"] = duplicate["root_contracts"][0]["normalized_path_sha256"]
        with self.assertRaisesRegex(authority.ActionTimeAuthorityError, "action_time_root_path_identity_duplicate"):
            self.prepare(duplicate)

        class NestedDict(dict):
            pass

        nested_subclass = deepcopy(self.bindings)
        nested_subclass["d17"] = NestedDict(nested_subclass["d17"])
        with self.assertRaisesRegex(coordinator.A3ActionTimeCoordinatorError, "a3_coordinator_nested_mapping_exact_type_required"):
            self.prepare(nested_subclass)

        extra = deepcopy(self.bindings)
        extra["endpoint"]["unexpected"] = H
        with self.assertRaises(authority.ActionTimeAuthorityError):
            self.prepare(extra)

    def test_scripted_nonce_ledger_states_never_create_live_authority(self):
        ledger = authority._create_scripted_nonce_ledger_for_tests()
        issued = ledger.transition("action-time-nonce-ledger", H, "issued")
        consumed = ledger.transition("action-time-nonce-ledger", H, "consumed")
        replay = ledger.transition("action-time-nonce-ledger", H, "replay")
        self.assertEqual([row["state"] for row in replay.to_dict()["entries"]], ["issued", "consumed", "replay"])
        for snapshot in (issued, consumed, replay):
            parsed = authority.NonceLedgerSnapshot.from_bytes(snapshot.canonical_bytes())
            self.assertFalse(parsed.to_dict()["live_authority"])
            self.assertFalse(parsed.to_dict()["permit_issued"])
            self.assertFalse(parsed.to_dict()["destructive_action_authorized"])
            self.assertFalse(hasattr(parsed, "transition"))

        ambiguous = authority._create_scripted_nonce_ledger_for_tests()
        ambiguous.transition("action-time-nonce-ambiguous", H, "issued")
        ambiguous.transition("action-time-nonce-ambiguous", H, "ambiguous")
        expired = authority._create_scripted_nonce_ledger_for_tests()
        expired.transition("action-time-nonce-expired", H, "issued")
        expired.transition("action-time-nonce-expired", H, "expired")
        with self.assertRaises(authority.ActionTimeAuthorityError):
            expired.transition("action-time-nonce-expired", H, "consumed")
        forged_snapshot = replay.to_dict()
        forged_snapshot["ledger_id"] = "action-time-ledger-forged"
        with self.assertRaisesRegex(authority.ActionTimeAuthorityError, "nonce_ledger_identity_invalid"):
            authority.NonceLedgerSnapshot(forged_snapshot)

    def test_equal_subclass_and_module_global_rebinding_do_not_replace_authority(self):
        class EqualResult(gate.A1ProductionGateResult):
            __slots__ = ()
            def __eq__(self, other):
                return True
            def __hash__(self):
                return hash(self.gate_result) if hasattr(self, "gate_result") else 1

        collision = object.__new__(EqualResult)
        with self.assertRaisesRegex(
            coordinator.A3ActionTimeCoordinatorError,
            "gate_owner_generation_or_type_drift",
        ):
            coordinator.prepare_a3_action_time_handoff_no_action(
                collision,
                "a1_failure_cleanup",
                deepcopy(self.bindings),
            )

        saved_gate_module = coordinator.a1_gate
        saved_json = authority.json
        saved_hashlib = authority.hashlib
        rebound_names = ("dict", "list", "tuple", "bytes", "len", "set", "id", "enumerate", "TypeError", "ValueError", "UnicodeDecodeError")
        try:
            coordinator.a1_gate = object()
            authority.json = object()
            authority.hashlib = object()
            for name in rebound_names:
                setattr(authority, name, object())
                setattr(coordinator, name, object())
            result = self.prepare()
            self.assertFalse(result.permit_issued)
            self.assertFalse(result.destructive_action_authorized)
            self.assertEqual(
                authority.ActionTimeAuthorityRequest.from_bytes(
                    result.request.canonical_bytes()
                ).sha256(),
                result.request.sha256(),
            )
        finally:
            coordinator.a1_gate = saved_gate_module
            authority.json = saved_json
            authority.hashlib = saved_hashlib
            for name in rebound_names:
                if hasattr(authority, name):
                    delattr(authority, name)
                if hasattr(coordinator, name):
                    delattr(coordinator, name)


if __name__ == "__main__":
    unittest.main()
