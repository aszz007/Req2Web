from __future__ import annotations

from copy import deepcopy
import inspect
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a3_action_time_coordinator as coordinator
import req2web_runtime.autodl_a3_authenticated_executor_contract as executor_contract
import req2web_runtime.autodl_a3_handoff_protocol as protocol
from tests.test_m3_autodl_action_time_authority import H, make_fixture


class A3HandoffProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner, cls.gate_result, cls.bindings = make_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.owner.tearDown()

    def prepared(self, bindings=None):
        return coordinator.prepare_a3_action_time_handoff_no_action(
            self.gate_result,
            "a1_failure_cleanup",
            deepcopy(bindings or self.bindings),
        )

    def test_capsule_and_executor_contract_are_structural_only(self):
        result = self.prepared()
        capsule = result.capsule
        contract = result.executor_contract
        self.assertEqual(capsule.data["status"], "detached_signature_capsule_template_no_action")
        self.assertEqual(tuple(capsule.data["signed_field_set"]), protocol.A3_HANDOFF_SIGNED_FIELD_SET)
        self.assertEqual(contract.data["channel_mode"], "pinned_ssh_channel")
        self.assertEqual(contract.data["verification_phase"], "future_remote_before_first_os_action")
        self.assertEqual(contract.data["capability_transport"], "future_authenticated_capsule_not_public_json_authority")
        for artifact in (capsule, contract):
            data = artifact.to_dict()
            for key in (
                "permit_issued", "signature_verified", "destructive_action_authorized",
                "external_action_executed", "cleanup_complete", "manager_consumable",
                "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed",
            ):
                self.assertFalse(data[key])
        self.assertFalse(capsule.data["signature_bytes_present"])
        self.assertFalse(capsule.data["signer_key_loaded"])
        self.assertNotIn("signature", capsule.to_dict())
        self.assertNotIn("private_key", capsule.to_dict())

    def test_remote_evidence_replay_never_self_certifies_release_or_revocation(self):
        result = self.prepared()
        replay = protocol.create_a3_remote_evidence_replay(
            result.request,
            result.capsule,
            evidence_bundle_id="a3-evidence-bundle-test",
            evidence_bundle_sha256=H,
            structurally_complete=True,
        )
        self.assertEqual(replay.to_dict()["status"], "remote_evidence_structural_replay_complete")
        self.assertEqual(replay.to_dict()["instance_release_state"], "deferred_external_browser_observation")
        self.assertEqual(replay.to_dict()["credential_revocation_state"], "deferred_external_manager_observation")
        self.assertFalse(replay.to_dict()["cleanup_complete"])
        self.assertFalse(replay.to_dict()["manager_consumable"])
        self.assertEqual(
            protocol.A3RemoteEvidenceReplay.from_bytes(replay.canonical_bytes()).to_dict(),
            replay.to_dict(),
        )

    def test_cross_bundle_and_equal_subclass_are_rejected(self):
        first = self.prepared()
        second_bindings = deepcopy(self.bindings)
        second_bindings["nonce"]["nonce_id"] = "action-time-nonce-second"
        second_bindings["nonce"]["nonce_sha256"] = "c" * 64
        second = self.prepared(second_bindings)
        with self.assertRaisesRegex(protocol.A3HandoffProtocolError, "a3_remote_replay_cross_bundle_invalid"):
            protocol.create_a3_remote_evidence_replay(
                second.request,
                first.capsule,
                evidence_bundle_id="a3-evidence-bundle-test",
                evidence_bundle_sha256=H,
                structurally_complete=False,
            )
        with self.assertRaisesRegex(executor_contract.A3AuthenticatedExecutorContractError, "a3_executor_contract_cross_bundle_invalid"):
            executor_contract.create_a3_authenticated_executor_contract(
                second.request,
                first.capsule,
            )

        class EqualCapsule(protocol.A3HandoffCapsuleTemplate):
            __slots__ = ()
            def __eq__(self, other):
                return True
            def __hash__(self):
                return 1

        forged = object.__new__(EqualCapsule)
        with self.assertRaisesRegex(TypeError, "a3_handoff_capsule_exact_type_required"):
            executor_contract.create_a3_authenticated_executor_contract(first.request, forged)

    def test_mutable_source_and_direct_constructor_cannot_add_signature_authority(self):
        result = self.prepared()
        capsule_data = result.capsule.to_dict()
        capsule_data["signed_field_set"].append("unapproved_field")
        self.assertNotIn("unapproved_field", result.capsule.data["signed_field_set"])

        forged = result.capsule.to_dict()
        forged["signature_verified"] = True
        with self.assertRaisesRegex(protocol.A3HandoffProtocolError, "a3_handoff_public_authority_forbidden"):
            protocol.validate_a3_handoff_capsule_template_bytes(protocol._canonical_for_tests(forged))
        forged_contract = result.executor_contract.to_dict()
        forged_contract["destructive_action_authorized"] = True
        with self.assertRaisesRegex(executor_contract.A3AuthenticatedExecutorContractError, "a3_executor_contract_authority_forbidden"):
            executor_contract.validate_a3_authenticated_executor_contract_bytes(executor_contract._canonical_for_tests(forged_contract))

    def test_public_source_has_no_signer_verifier_backend_or_action_surface(self):
        modules = (protocol, executor_contract, coordinator)
        source = "\n".join(inspect.getsource(module) for module in modules)
        forbidden_signatures = (
            "signer_callback", "verifier_callback", "backend_callback",
            "command_override", "clock_override", "platform_override",
            "approval_file", "permit_file", "private_key_path", "ssh-keygen",
            "subprocess.run", "subprocess.Popen", "paramiko", "requests.",
            "socket.", "os.kill", ".unlink(", ".rmdir(", "shutil.rmtree",
        )
        for token in forbidden_signatures:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
