from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a3_closeout as a3


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def reidentify(value, key, prefix):
    value[key] = prefix + hashlib.sha256(canonical({name: item for name, item in value.items() if name != key})).hexdigest()[:20]


def copied(value):
    return json.loads(canonical(value).decode("utf-8"))


class A3CloseoutFoundationTests(unittest.TestCase):
    def setUp(self):
        self.policy = a3.create_a3_closeout_policy()
        self.trigger = a3.create_a3_closeout_trigger(
            self.policy,
            "real-a1-plan-example",
            "1" * 64,
            "real-a1-execution-receipt-example",
            "2" * 64,
            "2026-07-25T00:00:00Z",
        )

    def process(self, status="process_termination_observed", at="2026-07-25T00:10:00Z"):
        not_run = status == "not_executed"
        return a3.create_a3_process_termination_evidence(
            self.policy, self.trigger,
            source_artifact_id="not-executed" if not_run else "ssh-observation-process",
            source_artifact_sha256=a3.A3_ABSENT_HASH if not_run else "3" * 64,
            observed_at_utc=at,
            term_sent=not not_run, grace_seconds=0 if not_run else 30,
            kill_sent=False, pid_sha256=a3.A3_ABSENT_HASH if not_run else "4" * 64,
            pgid_sha256=a3.A3_ABSENT_HASH if not_run else "5" * 64,
            cgroup_sha256=a3.A3_ABSENT_HASH if not_run else "6" * 64,
            post_process_count=0, post_gpu_process_count=0, post_listener_count=0,
        )

    def deletion(self, status="project_side_deletion_observed", at="2026-07-25T00:20:00Z"):
        not_run = status == "not_executed"
        return a3.create_a3_project_side_deletion_evidence(
            self.policy, self.trigger,
            source_artifact_id="not-executed" if not_run else "ssh-observation-deletion",
            source_artifact_sha256=a3.A3_ABSENT_HASH if not_run else "7" * 64,
            observed_at_utc=at,
            pre_inventory_sha256=a3.A3_ABSENT_HASH if not_run else "8" * 64,
            pre_file_count=0 if not_run else 16, pre_total_bytes=0 if not_run else 19329393661,
            post_residual_file_count=0, post_residual_total_bytes=0,
        )

    def release(self, status="released", at="2026-07-25T00:30:00Z"):
        not_run = status == "not_executed"
        release_states = {"released": ("released", "released", "not_running"), "stopped": ("stopped", "retained", "stopped"), "requested": ("requested", "unknown", "unknown"), "unknown": ("unknown", "unknown", "unknown")}
        states = ("not_executed",) * 3 if not_run else release_states[status]
        return a3.create_a3_instance_release_evidence(
            self.policy, self.trigger,
            source_artifact_id="not-executed" if not_run else "autodl-control-plane-observation",
            source_artifact_sha256=a3.A3_ABSENT_HASH if not_run else "9" * 64,
            observed_at_utc=at,
            instance_id_sha256=a3.A3_ABSENT_HASH if not_run else "a" * 64,
            release_request_sha256=a3.A3_ABSENT_HASH if not_run else "b" * 64,
            control_plane_state=states[0], storage_state=states[1], billing_state=states[2],
        )

    def revocation(self, status="access_revocation_observed", at="2026-07-25T00:40:00Z"):
        not_run = status == "not_executed"
        states = ("not_executed",) * 4 if not_run else (("removed", "removed", "released", "inaccessible") if status == "access_revocation_observed" else ("unknown", "unknown", "unknown", "unknown"))
        return a3.create_a3_access_revocation_evidence(
            self.policy, self.trigger,
            source_artifact_id="not-executed" if not_run else "local-operator-attestation",
            source_artifact_sha256=a3.A3_ABSENT_HASH if not_run else "c" * 64,
            observed_at_utc=at,
            credential_fingerprint_sha256=a3.A3_ABSENT_HASH if not_run else "d" * 64,
            remote_access_state=states[0], local_ephemeral_key_state=states[1],
            instance_release_state=states[2], instance_access_state=states[3],
        )

    def complete_bundle(self):
        return a3.create_a3_closeout_evidence_bundle(
            self.policy, self.trigger,
            [self.process(), self.deletion(), self.release(), self.revocation()],
        )

    def rejected(self, action, code=None):
        with self.assertRaises(a3.A3CloseoutError) as raised:
            action()
        if code is not None:
            self.assertEqual(str(raised.exception), code)

    def test_policy_and_trigger_freeze_d17_closeout_boundary(self):
        data = self.policy.to_dict()
        self.assertEqual(data["evidence_categories"], list(a3.A3_EVIDENCE_CATEGORIES))
        self.assertEqual(data["remote_deadline_minutes"], 120)
        self.assertEqual(data["cleanup_mode"], "delete_work_cache_release_instance_revoke_access")
        self.assertEqual(data["local_archive_retention_days"], 30)
        self.assertTrue(data["single_use"])
        for name in ("physical_erasure_claimed", "new_payload_allowed", "inference_allowed", "retry_allowed", "repair_allowed", "second_provision_allowed"):
            self.assertFalse(data[name])
        self.assertEqual(self.trigger.data["remote_deadline_utc"], "2026-07-25T02:00:00Z")

    def test_complete_bundle_is_structural_only_and_not_manager_consumable(self):
        bundle = self.complete_bundle()
        receipt = a3.evaluate_a3_closeout_readiness(bundle)
        self.assertEqual(receipt.data["status"], "a3_structural_evidence_ready_not_manager_consumable")
        self.assertTrue(receipt.data["structural_evidence_ready"])
        for name in ("manager_consumable", "external_action_authorized", "cleanup_complete", "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed", "physical_erasure_claimed"):
            self.assertFalse(receipt.data[name])
        parsed_bundle, parsed_receipt = a3.validate_a3_closeout_bundle_bytes(bundle.canonical_bytes(), receipt.canonical_bytes())
        self.assertEqual(parsed_bundle.sha256(), bundle.sha256())
        self.assertEqual(parsed_receipt.sha256(), receipt.sha256())

    def test_incomplete_and_not_executed_states_are_non_authorizing(self):
        incomplete = a3.create_a3_closeout_evidence_bundle(
            self.policy, self.trigger,
            [self.process(), self.deletion(), self.release("stopped"), self.revocation("access_revocation_incomplete")],
        )
        receipt = a3.evaluate_a3_closeout_readiness(incomplete)
        self.assertEqual(receipt.data["status"], "a3_evidence_incomplete")
        self.assertFalse(receipt.data["structural_evidence_ready"])
        not_run = a3.create_a3_closeout_evidence_bundle(
            self.policy, self.trigger,
            [self.process("not_executed", "2026-07-25T00:00:00Z"), self.deletion("not_executed", "2026-07-25T00:00:00Z"), self.release("not_executed", "2026-07-25T00:00:00Z"), self.revocation("not_executed", "2026-07-25T00:00:00Z")],
        )
        self.assertEqual(a3.evaluate_a3_closeout_readiness(not_run).data["status"], "a3_not_executed")

    def test_source_classes_and_release_state_are_strict(self):
        self.assertEqual(self.process().data["source_class"], "ssh_remote_observer")
        self.assertEqual(self.deletion().data["source_class"], "ssh_remote_observer")
        self.assertEqual(self.release().data["source_class"], "autodl_browser_control_plane_operator")
        self.assertEqual(self.revocation().data["source_class"], "local_operator_attestation")
        forged = copied(self.release().to_dict())
        forged["status"] = "released"
        forged["control_plane_state"] = "stopped"
        reidentify(forged, "evidence_id", "autodl-a3-release-")
        self.rejected(lambda: a3.A3InstanceReleaseEvidence.from_dict(forged), "release_state_tuple_invalid")

    def test_missing_category_wrong_source_and_late_evidence_fail_closed(self):
        bundle = self.complete_bundle().to_dict()
        bundle["evidence"].pop()
        bundle["inventory"].pop()
        bundle["tree_sha256"] = hashlib.sha256(canonical(bundle["inventory"])).hexdigest()
        reidentify(bundle, "bundle_id", "autodl-a3-bundle-")
        self.rejected(lambda: a3.A3CloseoutEvidenceBundle.from_dict(bundle), "bundle_evidence_count_invalid")

        wrong_source = copied(self.process().to_dict())
        wrong_source["source_class"] = "local_operator_attestation"
        reidentify(wrong_source, "evidence_id", "autodl-a3-process-")
        self.rejected(lambda: a3.A3ProcessTerminationEvidence.from_dict(wrong_source), "process_termination_source_invalid")

        late = self.revocation(at="2026-07-25T02:00:01Z")
        self.rejected(
            lambda: a3.create_a3_closeout_evidence_bundle(self.policy, self.trigger, [self.process(), self.deletion(), self.release(), late]),
            "bundle_evidence_after_deadline",
        )

    def test_forged_cleanup_complete_physical_erasure_and_direct_constructor_fail(self):
        bundle = self.complete_bundle().to_dict()
        bundle["cleanup_complete"] = True
        reidentify(bundle, "bundle_id", "autodl-a3-bundle-")
        self.rejected(
            lambda: a3.A3CloseoutEvidenceBundle(bundle),
            "bundle_cleanup_complete_invalid",
        )

        receipt = a3.evaluate_a3_closeout_readiness(self.complete_bundle()).to_dict()
        receipt["physical_erasure_claimed"] = True
        reidentify(receipt, "receipt_id", "autodl-a3-readiness-")
        self.rejected(
            lambda: a3.A3CloseoutReadinessReceipt(receipt),
            "readiness_receipt_physical_erasure_claimed_invalid",
        )

        receipt["physical_erasure_claimed"] = False
        receipt["status"] = "cleanup_complete"
        reidentify(receipt, "receipt_id", "autodl-a3-readiness-")
        self.rejected(lambda: a3.A3CloseoutReadinessReceipt.from_dict(receipt), "readiness_receipt_status_invalid")

    def test_release_free_text_payload_is_rejected_at_every_public_boundary(self):
        payloads = (
            ("unknown", r"C:\\Users\\operator\\.ssh\\id_ed25519", "cookie=session-secret"),
            ("unknown", "raw requirement text", "token=abc123"),
        )
        for states in payloads:
            self.rejected(
                lambda states=states: a3.create_a3_instance_release_evidence(
                    self.policy, self.trigger,
                    source_artifact_id="autodl-control-plane-observation",
                    source_artifact_sha256="9" * 64,
                    observed_at_utc="2026-07-25T00:30:00Z",
                    instance_id_sha256="a" * 64,
                    release_request_sha256="b" * 64,
                    control_plane_state=states[0],
                    storage_state=states[1],
                    billing_state=states[2],
                ),
                "release_state_tuple_invalid",
            )
        forged = copied(self.release("unknown").to_dict())
        forged["storage_state"] = r"C:\\secret\\path"
        forged["billing_state"] = "cookie=session-secret"
        reidentify(forged, "evidence_id", "autodl-a3-release-")
        self.rejected(
            lambda: a3.A3InstanceReleaseEvidence(forged),
            "release_state_tuple_invalid",
        )
        self.rejected(lambda: a3.A3InstanceReleaseEvidence.from_dict(forged), "release_state_tuple_invalid")
        self.rejected(lambda: a3.A3InstanceReleaseEvidence.from_bytes(canonical(forged)), "release_state_tuple_invalid")

    def test_receipt_failure_vocabulary_and_status_consistency_are_strict(self):
        valid = a3.evaluate_a3_closeout_readiness(self.complete_bundle()).to_dict()
        arbitrary = copied(valid)
        arbitrary["failure_codes"] = [r"C:\\secret\\path cookie=session-secret"]
        arbitrary["structural_evidence_ready"] = False
        arbitrary["status"] = "a3_evidence_incomplete"
        reidentify(arbitrary, "receipt_id", "autodl-a3-readiness-")
        self.rejected(
            lambda: a3.A3CloseoutReadinessReceipt(arbitrary),
            "readiness_receipt_failure_codes_invalid",
        )
        self.rejected(lambda: a3.A3CloseoutReadinessReceipt.from_dict(arbitrary), "readiness_receipt_failure_codes_invalid")
        self.rejected(lambda: a3.A3CloseoutReadinessReceipt.from_bytes(canonical(arbitrary)), "readiness_receipt_failure_codes_invalid")

        contradictory = copied(valid)
        contradictory["structural_evidence_ready"] = False
        reidentify(contradictory, "receipt_id", "autodl-a3-readiness-")
        self.rejected(
            lambda: a3.A3CloseoutReadinessReceipt.from_bytes(canonical(contradictory)),
            "readiness_receipt_status_consistency_invalid",
        )

        empty_incomplete = copied(valid)
        empty_incomplete["status"] = "a3_evidence_incomplete"
        empty_incomplete["structural_evidence_ready"] = False
        reidentify(empty_incomplete, "receipt_id", "autodl-a3-readiness-")
        self.rejected(
            lambda: a3.A3CloseoutReadinessReceipt.from_dict(empty_incomplete),
            "readiness_receipt_status_consistency_invalid",
        )

    def test_canonical_bytes_and_identity_replay_are_strict(self):
        raw = self.policy.canonical_bytes()
        self.rejected(lambda: a3.A3CloseoutPolicy.from_bytes(raw + b"\n"), "canonical_bytes_invalid")
        forged = self.policy.to_dict()
        forged["cleanup_mode"] = "other"
        reidentify(forged, "policy_id", "autodl-a3-policy-")
        self.rejected(lambda: a3.A3CloseoutPolicy.from_bytes(canonical(forged)), "policy_cleanup_invalid")

    def test_inventory_and_receipt_cross_binding_detect_drift(self):
        bundle = self.complete_bundle()
        receipt = a3.evaluate_a3_closeout_readiness(bundle)
        drift = bundle.to_dict()
        drift["inventory"][0]["bytes"] += 1
        reidentify(drift, "bundle_id", "autodl-a3-bundle-")
        self.rejected(lambda: a3.A3CloseoutEvidenceBundle.from_dict(drift), "bundle_inventory_binding_invalid")

        other_trigger = a3.create_a3_closeout_trigger(self.policy, "other-plan", "e" * 64, "other-receipt", "f" * 64, "2026-07-25T00:00:00Z")
        other_bundle = a3.create_a3_closeout_evidence_bundle(self.policy, other_trigger, [
            a3.create_a3_process_termination_evidence(self.policy, other_trigger, source_artifact_id="ssh-process", source_artifact_sha256="3" * 64, observed_at_utc="2026-07-25T00:10:00Z", term_sent=True, grace_seconds=30, kill_sent=False, pid_sha256="4" * 64, pgid_sha256="5" * 64, cgroup_sha256="6" * 64, post_process_count=0, post_gpu_process_count=0, post_listener_count=0),
            a3.create_a3_project_side_deletion_evidence(self.policy, other_trigger, source_artifact_id="ssh-delete", source_artifact_sha256="7" * 64, observed_at_utc="2026-07-25T00:20:00Z", pre_inventory_sha256="8" * 64, pre_file_count=1, pre_total_bytes=1, post_residual_file_count=0, post_residual_total_bytes=0),
            a3.create_a3_instance_release_evidence(self.policy, other_trigger, source_artifact_id="autodl-release", source_artifact_sha256="9" * 64, observed_at_utc="2026-07-25T00:30:00Z", instance_id_sha256="a" * 64, release_request_sha256="b" * 64, control_plane_state="released", storage_state="released", billing_state="not_running"),
            a3.create_a3_access_revocation_evidence(self.policy, other_trigger, source_artifact_id="local-revoke", source_artifact_sha256="c" * 64, observed_at_utc="2026-07-25T00:40:00Z", credential_fingerprint_sha256="d" * 64, remote_access_state="removed", local_ephemeral_key_state="removed", instance_release_state="released", instance_access_state="inaccessible"),
        ])
        self.rejected(lambda: a3.validate_a3_closeout_readiness_against(receipt, other_bundle), "readiness_receipt_bundle_binding_invalid")

    def test_public_api_has_no_action_or_authority_overrides(self):
        forbidden = {"backend", "callback", "observer", "expected", "status", "pass", "clock", "platform", "command", "delete", "release", "revoke"}
        functions = (
            a3.create_a3_closeout_policy, a3.create_a3_closeout_trigger,
            a3.create_a3_process_termination_evidence,
            a3.create_a3_project_side_deletion_evidence,
            a3.create_a3_instance_release_evidence,
            a3.create_a3_access_revocation_evidence,
            a3.create_a3_closeout_evidence_bundle,
            a3.evaluate_a3_closeout_readiness,
            a3.validate_a3_closeout_bundle_bytes,
        )
        for function in functions:
            signature = inspect.signature(function)
            self.assertFalse(any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()))
            self.assertTrue(forbidden.isdisjoint(signature.parameters))

    def test_definition_time_capture_resists_module_rebinding(self):
        original_bundle = self.complete_bundle()
        original_receipt = a3.evaluate_a3_closeout_readiness(original_bundle)
        with patch.multiple(
            a3,
            A3CloseoutPolicy=object,
            A3CloseoutTrigger=object,
            A3CloseoutEvidenceBundle=object,
            A3CloseoutReadinessReceipt=object,
            A3_EVIDENCE_CATEGORIES=("forged",),
            A3_CLEANUP_MODE="forged",
            _build_contract=lambda: (_ for _ in ()).throw(AssertionError("rebound")),
        ):
            policy = a3.create_a3_closeout_policy()
            self.assertEqual(policy.sha256(), self.policy.sha256())
            bundle, receipt = a3.validate_a3_closeout_bundle_bytes(original_bundle.canonical_bytes(), original_receipt.canonical_bytes())
            self.assertEqual(bundle.sha256(), original_bundle.sha256())
            self.assertEqual(receipt.sha256(), original_receipt.sha256())

    def test_cli_validates_only_local_canonical_bytes(self):
        bundle = self.complete_bundle()
        receipt = a3.evaluate_a3_closeout_readiness(bundle)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_path = root / "bundle.json"
            receipt_path = root / "receipt.json"
            bundle_path.write_bytes(bundle.canonical_bytes())
            receipt_path.write_bytes(receipt.canonical_bytes())
            completed = subprocess.run(
                [sys.executable, "-B", "scripts/validate_stage3_autodl_a3_closeout_bundle.py", "--bundle", str(bundle_path), "--receipt", str(receipt_path)],
                cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["validation"], "local_structural_only_no_action")
            self.assertFalse(output["readiness_receipt"]["manager_consumable"])


if __name__ == "__main__":
    unittest.main()
