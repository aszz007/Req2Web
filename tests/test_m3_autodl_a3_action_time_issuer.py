from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a1_production_gate as gate
import req2web_runtime.autodl_a3_action_time_issuer as issuer
import req2web_runtime.autodl_a3_nonce_ledger as nonce_ledger
from tests.test_m3_autodl_action_time_authority import make_fixture


class ActionTimeIssuerAndNonceLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner, cls.gate_result, cls.base_bindings = make_fixture()
        cls.temp_parent = Path(__file__).resolve().parents[1] / ".tmp_nonce_ledger_tests"
        cls.temp_parent.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.owner.tearDown()

    def _root(self, label):
        case = self.temp_parent / (label + "-" + uuid.uuid4().hex)
        case.mkdir()
        root = case / "req2web-action-time-nonce-ledger"
        self.addCleanup(lambda: shutil.rmtree(case, ignore_errors=True))
        return root

    def bindings(self, *, issued="2030-01-01T00:00:00Z", expires="2030-01-01T01:00:00Z"):
        value = deepcopy(self.base_bindings)
        value["signature_profile"]["backend"] = "openssh_keygen_y_sign_verify"
        snapshot = gate._read_a1_production_gate_result_owning_snapshot(self.gate_result)
        value["action_time_git_sha"] = snapshot["action_time_git_sha"]
        value["deployment_manifest"]["source_commit_sha"] = snapshot["action_time_git_sha"]
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

    def intent(self, bindings=None):
        return issuer.prepare_action_time_issuance_intent_no_action(
            self.gate_result,
            "a1_failure_cleanup",
            bindings or self.bindings(),
        )

    def test_intent_requires_complete_owner_result_and_all_public_flags_stay_false(self):
        intent = self.intent()
        self.assertIs(type(intent), issuer.ActionTimeIssuanceIntent)
        data = intent.to_dict()
        self.assertEqual(data["protocol_status"], "local_issuance_intent_declared_no_action")
        self.assertEqual(data["a1_owner_snapshot"]["owner_snapshot_sha256"], gate._read_a1_production_gate_result_owning_snapshot(self.gate_result)["snapshot_sha256"])
        for key in (
            "permit_issued", "signature_verified", "destructive_action_authorized",
            "external_action_executed", "cleanup_complete", "manager_consumable",
            "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed",
        ):
            self.assertFalse(data[key])
        self.assertTrue(data["single_use"])
        self.assertTrue(data["no_retry"])
        self.assertTrue(data["no_second_provision"])
        with self.assertRaisesRegex(TypeError, "complete_registered_a1_result_required"):
            issuer.prepare_action_time_issuance_intent_no_action(b"not-a-result", "a1_failure_cleanup", self.bindings())
        with self.assertRaisesRegex(TypeError, "issuance_intent_not_registered"):
            object.__new__(issuer.ActionTimeIssuanceIntent).canonical_bytes()

    def test_naked_bytes_decision_and_cross_or_stale_owner_bindings_cannot_upgrade(self):
        intent = self.intent()
        replay = issuer.validate_action_time_issuance_intent_bytes(intent.canonical_bytes())
        self.assertEqual(replay.to_dict(), intent.to_dict())
        ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(self._root("replay"))
        with self.assertRaisesRegex(issuer.ActionTimeIssuanceError, "replay_bytes_not_ledger_eligible"):
            nonce_ledger.reserve_action_time_nonce_no_action(ledger, replay)
        forged = intent.to_dict()
        forged["permit_issued"] = True
        with self.assertRaises(issuer.ActionTimeIssuanceError):
            issuer.validate_action_time_issuance_intent_bytes(issuer._canonical_for_tests(forged))
        wrong_action = self.bindings()
        wrong_action["action_time_git_sha"] = "f" * 40
        with self.assertRaisesRegex(issuer.ActionTimeIssuanceError, "action_sha_owner_binding_invalid"):
            self.intent(wrong_action)
        wrong_plan = self.bindings()
        wrong_plan["operational_plan"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(issuer.ActionTimeIssuanceError, "operational_plan_owner_binding_invalid"):
            self.intent(wrong_plan)
        cross = self.bindings()
        cross["root_contracts"][1]["normalized_path_sha256"] = cross["root_contracts"][0]["normalized_path_sha256"]
        with self.assertRaisesRegex(issuer.ActionTimeIssuanceError, "root_containment_or_identity_invalid"):
            self.intent(cross)

    def test_definition_time_owner_capture_survives_module_and_descriptor_rebinding(self):
        bindings = self.bindings()
        original_module_class = gate.A1ProductionGateResult
        original_accessor = gate._read_a1_production_gate_result_owning_snapshot
        original_descriptor = vars(gate.A1ProductionGateResult)["decision"]
        try:
            gate.A1ProductionGateResult.decision = property(lambda self: object())
            gate.A1ProductionGateResult = object
            gate._read_a1_production_gate_result_owning_snapshot = lambda *args: {"forged": True}
            intent = self.intent(bindings)
            self.assertFalse(intent.to_dict()["permit_issued"])
        finally:
            gate.A1ProductionGateResult = original_module_class
            gate._read_a1_production_gate_result_owning_snapshot = original_accessor
            gate.A1ProductionGateResult.decision = original_descriptor

    def test_durable_ledger_is_atomic_single_use_and_stops_at_closeout(self):
        root = self._root("state-machine")
        ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(root)
        intent = self.intent()
        reserved = nonce_ledger.reserve_action_time_nonce_no_action(ledger, intent)
        self.assertEqual(reserved["entries"][0]["state"], "reserved")
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "transition_invalid"):
            nonce_ledger.transition_action_time_nonce_no_action(
                ledger, intent.to_dict()["nonce"]["nonce_id"], intent.to_dict()["nonce"]["nonce_sha256"], "terminal_success"
            )
        for state in ("issued", "delivered", "accepted_remote", "started", "terminal_failure", "closeout_required"):
            snapshot = nonce_ledger.transition_action_time_nonce_no_action(
                ledger, intent.to_dict()["nonce"]["nonce_id"], intent.to_dict()["nonce"]["nonce_sha256"], state
            )
        self.assertEqual(snapshot["entries"][0]["state"], "closeout_required")
        for key in (
            "permit_issued", "signature_verified", "destructive_action_authorized",
            "external_action_executed", "cleanup_complete", "manager_consumable",
            "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed",
        ):
            self.assertFalse(snapshot[key])
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "nonce_replay_or_duplicate"):
            nonce_ledger.reserve_action_time_nonce_no_action(ledger, intent)

    def test_expiry_lock_corruption_and_root_containment_fail_closed(self):
        root = self._root("fail-closed")
        ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(root)
        expired = self.intent(self.bindings(issued="2000-01-01T00:00:00Z", expires="2000-01-01T01:00:00Z"))
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "nonce_expired_closeout_required"):
            nonce_ledger.reserve_action_time_nonce_no_action(ledger, expired)
        lock = root / ".nonce-ledger.lock"
        lock.write_text("ambiguous", encoding="utf-8")
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "lock_or_ambiguous_state"):
            nonce_ledger.reserve_action_time_nonce_no_action(ledger, self.intent())
        lock.unlink()
        (root / "nonce-ledger.json").write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "file_drift_closeout_required|corrupt_closeout_required"):
            ledger.snapshot()
        bad_case = self.temp_parent / ("bad-" + uuid.uuid4().hex)
        bad_case.mkdir()
        self.addCleanup(lambda: shutil.rmtree(bad_case, ignore_errors=True))
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "root_name_invalid"):
            nonce_ledger._create_durable_nonce_ledger_for_tests(bad_case / "not-a-dedicated-root")

    def test_missing_ledger_after_marker_is_never_reinitialized(self):
        root = self._root("missing-ledger")
        ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(root)
        intent = self.intent()
        nonce_ledger.reserve_action_time_nonce_no_action(ledger, intent)
        (root / "nonce-ledger.json").unlink()
        for action in (
            lambda: ledger.snapshot(),
            lambda: nonce_ledger.reserve_action_time_nonce_no_action(ledger, intent),
            lambda: nonce_ledger.transition_action_time_nonce_no_action(
                ledger,
                intent.to_dict()["nonce"]["nonce_id"],
                intent.to_dict()["nonce"]["nonce_sha256"],
                "issued",
            ),
            lambda: nonce_ledger._create_durable_nonce_ledger_for_tests(root),
        ):
            with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "ledger_missing"):
                action()

    def test_persisted_history_replays_the_captured_transition_authority(self):
        cases = {
            "jump": ["reserved", "terminal_success"],
            "rollback": ["reserved", "issued", "reserved"],
            "repeat": ["reserved", "reserved"],
            "terminal-after-terminal": [
                "reserved", "issued", "delivered", "accepted_remote", "started",
                "terminal_failure", "terminal_success",
            ],
        }
        for label, states in cases.items():
            with self.subTest(label=label):
                root = self._root("history-" + label)
                ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(root)
                intent = self.intent()
                nonce_ledger.reserve_action_time_nonce_no_action(ledger, intent)
                ledger_path = root / "nonce-ledger.json"
                data = json.loads(ledger_path.read_text(encoding="utf-8"))
                row = data["entries"][0]
                row["history"] = [
                    {"state": state, "transition_index": index}
                    for index, state in enumerate(states)
                ]
                row["state"] = states[-1]
                row["transition_index"] = len(states) - 1
                ledger_path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
                with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "history_invalid"):
                    nonce_ledger._create_durable_nonce_ledger_for_tests(root)

    def test_live_marker_root_and_ledger_replacement_drift_fail_closed(self):
        marker_root = self._root("marker-drift")
        marker_ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(marker_root)
        marker_path = marker_root / ".req2web_nonce_ledger_root_v1.json"
        marker_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "marker_or_containment_drift"):
            marker_ledger.snapshot()

        file_root = self._root("file-replacement")
        file_ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(file_root)
        ledger_path = file_root / "nonce-ledger.json"
        replacement = file_root / "ledger-replacement.json"
        replacement.write_bytes(ledger_path.read_bytes())
        os.replace(replacement, ledger_path)
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "file_drift_closeout_required"):
            file_ledger.snapshot()

        root = self._root("root-replacement")
        ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(root)
        marker_raw = (root / ".req2web_nonce_ledger_root_v1.json").read_bytes()
        ledger_raw = (root / "nonce-ledger.json").read_bytes()
        moved = root.parent / "moved-root"
        root.rename(moved)
        root.mkdir()
        (root / ".req2web_nonce_ledger_root_v1.json").write_bytes(marker_raw)
        (root / "nonce-ledger.json").write_bytes(ledger_raw)
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "file_drift_closeout_required"):
            ledger.snapshot()

    def test_unmarked_nonempty_root_fails_closed(self):
        root = self._root("nonempty-root")
        root.mkdir()
        (root / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "unmarked_root_not_empty"):
            nonce_ledger._create_durable_nonce_ledger_for_tests(root)

    def test_ledger_symlink_fails_closed_when_supported(self):
        symlink_root = self._root("symlink")
        ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(symlink_root)
        ledger_path = symlink_root / "nonce-ledger.json"
        ledger_path.unlink()
        try:
            ledger_path.symlink_to("missing-target.json")
        except OSError:
            self.skipTest("symlink creation is unavailable on this Windows runner")
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "ledger_missing_or_not_regular"):
            ledger.snapshot()

    def test_preexisting_atomic_temporary_file_is_not_reused(self):
        root = self._root("temporary-file")
        ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(root)
        temporary = root / ".nonce-ledger.json.write"
        temporary.write_text("sentinel-temporary-bytes", encoding="utf-8")
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "root_contents_invalid"):
            nonce_ledger.reserve_action_time_nonce_no_action(ledger, self.intent())
        self.assertEqual(temporary.read_text(encoding="utf-8"), "sentinel-temporary-bytes")

    def test_preexisting_atomic_temporary_symlink_is_not_reused_when_supported(self):
        root = self._root("temporary-symlink")
        ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(root)
        target = root / "temporary-target.txt"
        target.write_text("sentinel-target-bytes", encoding="utf-8")
        temporary = root / ".nonce-ledger.json.write"
        try:
            temporary.symlink_to(target.name)
        except OSError:
            self.skipTest("symlink creation is unavailable on this Windows runner")
        with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "root_contents_invalid"):
            nonce_ledger.reserve_action_time_nonce_no_action(ledger, self.intent())
        self.assertEqual(target.read_text(encoding="utf-8"), "sentinel-target-bytes")

    def test_replaced_lock_binding_is_rejected_without_deleting_replacement(self):
        root = self._root("lock-replacement")
        ledger = nonce_ledger._create_durable_nonce_ledger_for_tests(root)
        lock = root / ".nonce-ledger.lock"
        lock_binding = nonce_ledger._acquire_durable_nonce_ledger_lock_for_tests(ledger)
        try:
            replacement = root / "replacement-lock"
            replacement.write_bytes(b"req2web-no-action-ledger-lock")
            os.replace(replacement, lock)
            with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "lock_ownership_drift_closeout_required"):
                nonce_ledger._validate_durable_nonce_ledger_lock_binding_for_tests(ledger, lock_binding)
            with self.assertRaisesRegex(nonce_ledger.DurableNonceLedgerError, "lock_ownership_drift_closeout_required"):
                nonce_ledger._release_durable_nonce_ledger_lock_for_tests(ledger, lock_binding)
            self.assertTrue(lock.exists())
        finally:
            if lock.exists() or lock.is_symlink():
                lock.unlink()

    def test_write_all_handles_partial_writes_and_fails_closed_on_zero_or_error(self):
        completed = nonce_ledger._exercise_durable_nonce_ledger_write_all_for_tests(
            b"abcdef",
            (1, 2, 3),
        )
        self.assertEqual(completed, 6)
        for label, script in (("zero", (0,)), ("failure", ("raise",))):
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    nonce_ledger.DurableNonceLedgerError,
                    "write_all_incomplete_closeout_required",
                ):
                    nonce_ledger._exercise_durable_nonce_ledger_write_all_for_tests(b"abc", script)

    def test_windows_production_ledger_requires_commit_barrier_before_root_creation(self):
        if os.name != "nt":
            self.skipTest("this assertion is specific to the current Windows no-capability runner")
        production_root = Path(nonce_ledger.__file__).absolute().parents[2] / "req2web-action-time-nonce-ledger"
        if production_root.exists():
            self.skipTest("pre-existing production root must not be modified by this test")
        with self.assertRaisesRegex(
            nonce_ledger.DurableNonceLedgerError,
            "commit_barrier_unsupported_closeout_required",
        ):
            nonce_ledger.open_durable_action_time_nonce_ledger_no_action()
        self.assertFalse(production_root.exists())

    def test_no_runtime_action_surface_is_present(self):
        files = (Path(issuer.__file__), Path(nonce_ledger.__file__))
        forbidden = ("subprocess", "socket", "requests", "urllib", "paramiko", "ssh-keygen", "autodl.com", "transformers")
        for path in files:
            source = path.read_text(encoding="utf-8").lower()
            for token in forbidden:
                self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
