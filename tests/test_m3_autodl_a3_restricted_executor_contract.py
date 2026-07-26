from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a3_action_time_coordinator as coordinator
import req2web_runtime.autodl_a3_action_time_issuer as issuer
import req2web_runtime.autodl_a3_restricted_executor_contract as restricted
from tests.test_m3_autodl_action_time_authority import make_fixture


class RestrictedExecutorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner, cls.gate_result, cls.base_bindings = make_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.owner.tearDown()

    def bindings(self):
        value = deepcopy(self.base_bindings)
        value["signature_profile"]["backend"] = "openssh_keygen_y_sign_verify"
        snapshot = self.gate_result and __import__("req2web_runtime.autodl_a1_production_gate", fromlist=["_"])._read_a1_production_gate_result_owning_snapshot(self.gate_result)
        value["action_time_git_sha"] = snapshot["action_time_git_sha"]
        value["deployment_manifest"]["source_commit_sha"] = snapshot["action_time_git_sha"]
        value["profile"] = {"profile_name": "quality_experiment", "dtype": "bf16", "quantization": "none", "selected_device": "unresolved_no_action", "selection_basis": "local_protocol_preparation_only", "override": False}
        value["nonce"]["issued_at_utc"] = "2030-01-01T00:00:00Z"
        value["nonce"]["expires_at_utc"] = "2030-01-01T01:00:00Z"
        return value

    def intent(self, value=None):
        return issuer.prepare_action_time_issuance_intent_no_action(self.gate_result, "a1_failure_cleanup", value or self.bindings())

    def handoff(self, value=None):
        coordinator_values = deepcopy(value or self.bindings())
        coordinator_values.pop("profile", None)
        coordinator_values["signature_profile"] = deepcopy(self.base_bindings["signature_profile"])
        return coordinator.prepare_a3_action_time_handoff_no_action(self.gate_result, "a1_failure_cleanup", coordinator_values)

    def executor_binding(self):
        digest = restricted._digest_for_tests
        identity = lambda value: {"identity": value, "identity_sha256": digest(value.encode("utf-8"))}
        return {"bootstrap_identity": identity("bootstrap-no-action"), "restricted_executor_identity": identity("restricted-executor-no-action"), "worker_id_sha256": digest(b"worker-v1"), "gpu_id_sha256": digest(b"gpu-unresolved-no-action"), "fixed_command_sha256": digest(b"fixed-command-v1"), "capsule_command_sha256": digest(b"capsule-command-v1")}

    def create(self):
        values = self.bindings()
        return restricted.create_a3_restricted_executor_contract(self.intent(values), self.handoff(values), self.executor_binding())

    def test_happy_structure_is_canonical_and_all_transport_and_action_flags_are_fixed(self):
        contract = self.create()
        self.assertIs(type(contract), restricted.A3RestrictedExecutorContract)
        data = contract.to_dict()
        self.assertEqual(data["status"], "restricted_executor_transport_declared_no_action")
        self.assertEqual(set(data["transport_restrictions"]), set(restricted.A3_RESTRICTED_EXECUTOR_TRANSPORT_RESTRICTIONS))
        self.assertTrue(all(value is False for value in data["transport_restrictions"].values()))
        for key in ("permit_issued", "signature_verified", "destructive_action_authorized", "external_action_executed", "cleanup_complete", "manager_consumable", "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed"):
            self.assertFalse(data[key])
        replay = restricted.validate_a3_restricted_executor_contract_bytes(contract.canonical_bytes())
        self.assertEqual(replay.to_dict(), data)
        self.assertEqual(replay.sha256(), contract.sha256())

    def test_forged_bytes_constructor_and_subclass_cannot_upgrade_or_bypass_registry(self):
        contract = self.create()
        forged = contract.to_dict(); forged["transport_restrictions"]["pty"] = True
        with self.assertRaises(restricted.A3RestrictedExecutorContractError):
            restricted.validate_a3_restricted_executor_contract_bytes(restricted._canonical_for_tests(forged))
        forged = contract.to_dict(); forged["permit_issued"] = True
        with self.assertRaises(restricted.A3RestrictedExecutorContractError):
            restricted.validate_a3_restricted_executor_contract_bytes(restricted._canonical_for_tests(forged))
        with self.assertRaisesRegex(TypeError, "factory_required"):
            restricted.A3RestrictedExecutorContract()
        with self.assertRaisesRegex(TypeError, "not_registered"):
            object.__new__(restricted.A3RestrictedExecutorContract).canonical_bytes()
        class EqualContract(restricted.A3RestrictedExecutorContract):
            __slots__ = ()
        with self.assertRaisesRegex(TypeError, "exact_type_required"):
            object.__new__(EqualContract).to_dict()

    def test_binding_cross_bundle_and_identity_drift_fail_closed(self):
        values = self.bindings()
        intent = self.intent(values)
        other = self.bindings(); other["nonce"]["expires_at_utc"] = "2030-01-01T02:00:00Z"
        with self.assertRaisesRegex(restricted.A3RestrictedExecutorContractError, "cross_bundle_invalid"):
            restricted.create_a3_restricted_executor_contract(intent, self.handoff(other), self.executor_binding())
        bad = self.executor_binding(); bad["bootstrap_identity"] = bad["restricted_executor_identity"]
        with self.assertRaisesRegex(restricted.A3RestrictedExecutorContractError, "identity_separation_invalid"):
            restricted.create_a3_restricted_executor_contract(self.intent(values), self.handoff(values), bad)
        replay = issuer.validate_action_time_issuance_intent_bytes(self.intent(values).canonical_bytes())
        with self.assertRaisesRegex(issuer.ActionTimeIssuanceError, "replay_bytes_not_ledger_eligible"):
            restricted.create_a3_restricted_executor_contract(replay, self.handoff(values), self.executor_binding())

    def test_definition_time_captures_survive_module_and_descriptor_rebinding(self):
        values = self.bindings()
        saved_issuer, saved_coordinator = restricted.issuer_module, restricted.coordinator_module
        original = coordinator.A3ActionTimeCoordinatorResult.request
        try:
            restricted.issuer_module = object(); restricted.coordinator_module = object()
            coordinator.A3ActionTimeCoordinatorResult.request = property(lambda self: object())
            contract = restricted.create_a3_restricted_executor_contract(self.intent(values), self.handoff(values), self.executor_binding())
            self.assertFalse(contract.to_dict()["external_action_executed"])
        finally:
            restricted.issuer_module = saved_issuer; restricted.coordinator_module = saved_coordinator
            coordinator.A3ActionTimeCoordinatorResult.request = original

    def test_no_runtime_action_surface_is_present(self):
        source = Path(restricted.__file__).read_text(encoding="utf-8").lower()
        for token in ("subprocess", "socket", "requests", "urllib", "paramiko", "ssh-keygen", "autodl.com", "transformers"):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
