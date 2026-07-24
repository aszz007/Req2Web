from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from contextlib import ExitStack
import inspect
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import req2web_runtime.cleanup as cleanup_module  # noqa: E402
from req2web_runtime.cleanup import (  # noqa: E402
    LocalCleanupError,
    LocalCleanupPolicy,
    LocalCleanupReceipt,
    LocalTemporaryArtifact,
    create_local_cleanup_policy,
    create_local_temporary_root,
    execute_local_cleanup,
)


TEST_ROOT = ROOT / "outputs" / "_m3_retention_cleanup_tests"


class TierA09RetentionCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = create_local_temporary_root(TEST_ROOT)
        self.work = self.temp.path
        (self.work / "nested").mkdir()
        (self.work / "alpha.txt").write_bytes(b"synthetic alpha")
        (self.work / "nested" / "beta.json").write_bytes(b'{"fixture":"beta"}')
        self.artifacts = (
            LocalTemporaryArtifact("fixture-alpha", "synthetic_fixture", "alpha.txt"),
            LocalTemporaryArtifact("fixture-beta", "deterministic_test_artifact", "nested/beta.json"),
        )
        self.policy = create_local_cleanup_policy(self.temp, self.artifacts)

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)
        if TEST_ROOT.exists() and not any(TEST_ROOT.iterdir()):
            TEST_ROOT.rmdir()

    def _restore(self) -> None:
        (self.work / "nested").mkdir(exist_ok=True)
        (self.work / "alpha.txt").write_bytes(b"synthetic alpha")
        (self.work / "nested" / "beta.json").write_bytes(b'{"fixture":"beta"}')

    @staticmethod
    def _reidentify(payload: dict[str, object], key: str, prefix: str) -> None:
        root = {name: value for name, value in payload.items() if name != key}
        payload[key] = prefix + cleanup_module._sha256(cleanup_module._canonical_bytes(root))[:20]

    def test_public_signatures_have_no_authority_override(self) -> None:
        targets = (
            execute_local_cleanup, create_local_cleanup_policy, LocalCleanupPolicy.create,
            LocalCleanupPolicy.from_dict, LocalCleanupPolicy.from_bytes, LocalCleanupPolicy.validate,
            LocalCleanupPolicy.to_dict, LocalCleanupReceipt.from_dict, LocalCleanupReceipt.from_bytes,
            LocalCleanupReceipt.validate, LocalCleanupReceipt.to_dict, LocalCleanupReceipt.validate_against,
        )
        for target in targets:
            with self.subTest(target=target):
                self.assertFalse(any(parameter.name.startswith("_") for parameter in inspect.signature(target).parameters.values()))
        with self.assertRaises(TypeError):
            execute_local_cleanup(self.policy, self.temp, self.artifacts, _artifact_path_fn=lambda path: path)  # type: ignore[call-arg]

    def test_dedicated_root_policy_and_normal_cleanup_round_trip(self) -> None:
        self.assertFalse(self.policy.external_egress_allowed)
        self.assertEqual(LocalCleanupPolicy.from_bytes(self.policy.canonical_bytes()), self.policy)
        receipt = execute_local_cleanup(self.policy, self.temp, self.artifacts)
        self.assertEqual(receipt.action_status, "completed")
        self.assertEqual(receipt.failure_code, "none")
        self.assertFalse((self.work / "alpha.txt").exists())
        self.assertFalse((self.work / "nested" / "beta.json").exists())
        self.assertEqual(LocalCleanupReceipt.from_bytes(receipt.canonical_bytes()), receipt)
        receipt.validate_against(self.policy, self.temp, self.artifacts)

    def test_root_is_not_an_arbitrary_directory_and_lexical_symlink_is_rejected(self) -> None:
        with self.assertRaises(LocalCleanupError):
            create_local_cleanup_policy(self.work, self.artifacts)  # type: ignore[arg-type]
        linked = TEST_ROOT / ("linked-" + uuid4().hex)
        try:
            linked.symlink_to(self.work, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Windows account does not permit symlink creation")
        try:
            forged = cleanup_module.LocalTemporaryRoot(linked, self.temp.root_id)
            with self.assertRaises(LocalCleanupError):
                create_local_cleanup_policy(forged, self.artifacts)
        finally:
            linked.unlink(missing_ok=True)

    def test_duplicate_relative_path_policy_is_rejected(self) -> None:
        duplicate = (
            self.artifacts[0],
            LocalTemporaryArtifact("second-id", "synthetic_fixture", "alpha.txt"),
        )
        with self.assertRaises(LocalCleanupError):
            create_local_cleanup_policy(self.temp, duplicate)

    def test_precheck_hash_length_and_scope_drift_fail_closed(self) -> None:
        (self.work / "alpha.txt").write_bytes(b"changed length and hash")
        receipt = execute_local_cleanup(self.policy, self.temp, self.artifacts)
        self.assertEqual((receipt.action_status, receipt.failure_code), ("failed", "cleanup_precheck_failed"))
        self.assertTrue((self.work / "alpha.txt").exists())
        self.assertTrue((self.work / "nested" / "beta.json").exists())
        self.assertTrue(all(item["decision"] == "rejected_precheck" for item in receipt.artifact_results))
        outside = TEST_ROOT / ("outside-" + uuid4().hex + ".txt")
        outside.write_bytes(b"do not remove")
        for first in (
            LocalTemporaryArtifact("fixture-alpha", "synthetic_fixture", "../outside.txt"),
            LocalTemporaryArtifact("fixture-alpha", "synthetic_fixture", str(outside)),
            LocalTemporaryArtifact("unlisted", "synthetic_fixture", "alpha.txt"),
        ):
            with self.subTest(first=first):
                failed = execute_local_cleanup(self.policy, self.temp, (first, self.artifacts[1]))
                self.assertEqual(failed.failure_code, "cleanup_precheck_failed")
                self.assertTrue(outside.exists())
        outside.unlink()

    def test_completed_inventory_and_failed_result_forgery_are_rejected(self) -> None:
        receipt = execute_local_cleanup(self.policy, self.temp, self.artifacts)
        payload = receipt.to_dict()
        payload["pre_inventory_sha256"] = "0" * 64
        payload["post_inventory_sha256"] = "1" * 64
        self._reidentify(payload, "receipt_id", cleanup_module._RECEIPT_ID_PREFIX)
        forged = LocalCleanupReceipt.from_dict(payload)
        with self.assertRaises(LocalCleanupError):
            forged.validate_against(self.policy, self.temp, self.artifacts)

        self._restore()
        failed = execute_local_cleanup(self.policy, self.temp, self.artifacts[:-1])
        self.assertEqual(failed.failure_code, "cleanup_precheck_failed")
        payload = failed.to_dict()
        payload["artifact_results"].pop()
        self._reidentify(payload, "receipt_id", cleanup_module._RECEIPT_ID_PREFIX)
        forged_failed = LocalCleanupReceipt.from_dict(payload)
        with self.assertRaises(LocalCleanupError):
            forged_failed.validate_against(self.policy, self.temp, self.artifacts[:-1])

    def test_private_scripted_delete_failure_has_exact_prefix_failure_suffix_matrix(self) -> None:
        original = Path.unlink
        calls = {"count": 0}

        def fail_second(path: Path) -> None:
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("scripted")
            original(path)

        receipt = cleanup_module._execute_local_cleanup_scripted(self.policy, self.temp, self.artifacts, fail_second)
        self.assertEqual((receipt.action_status, receipt.failure_code), ("failed", "cleanup_not_completed"))
        self.assertEqual([item["decision"] for item in receipt.artifact_results], ["absent_after_action", "not_completed"])
        receipt.validate_against(self.policy, self.temp, self.artifacts)

    def test_receipt_is_payload_free_and_is_not_an_a08_bundle_receipt(self) -> None:
        receipt = execute_local_cleanup(self.policy, self.temp, self.artifacts)
        text = receipt.canonical_bytes().decode("utf-8")
        self.assertNotIn("synthetic alpha", text)
        self.assertNotIn("nested/beta.json", text)
        self.assertNotIn(str(self.work), text)
        self.assertEqual(receipt.local_fixture_scope["receipt_scope"], "local_temporary_project_authored_or_synthetic_fixture")
        self.assertEqual(receipt.local_fixture_scope["real_run_cleanup_receipt"], "not_a_real_run_receipt")
        self.assertTrue(all(value == "not_applicable_tier_a_not_executed" for value in receipt.remote_actions.values()))

    def test_unknown_keys_noncanonical_bytes_and_fixed_remote_actions_fail_closed(self) -> None:
        policy_payload = self.policy.to_dict()
        policy_payload["unknown"] = True
        with self.assertRaises(LocalCleanupError):
            LocalCleanupPolicy.from_dict(policy_payload)
        with self.assertRaises(LocalCleanupError):
            LocalCleanupPolicy.from_bytes(self.policy.canonical_bytes() + b"\n")
        receipt = execute_local_cleanup(self.policy, self.temp, self.artifacts)
        payload = receipt.to_dict()
        payload["remote_actions"]["remote_deletion"] = "completed"
        self._reidentify(payload, "receipt_id", cleanup_module._RECEIPT_ID_PREFIX)
        with self.assertRaises(LocalCleanupError):
            LocalCleanupReceipt.from_dict(payload)

    def test_definition_time_capture_survives_module_rebinding(self) -> None:
        policy_bytes = self.policy.canonical_bytes()
        failed = execute_local_cleanup(self.policy, self.temp, self.artifacts[:-1])
        failed_bytes = failed.canonical_bytes()
        with ExitStack() as stack:
            for target, name, replacement in (
                (cleanup_module, "CLEANUP_POLICY_SCHEMA_VERSION", "forged"),
                (cleanup_module, "_canonical_bytes", lambda value: b"forged"),
                (cleanup_module, "LocalCleanupPolicy", object),
                (cleanup_module, "_FIXED_EXECUTE", lambda *args: (_ for _ in ()).throw(AssertionError("rebound"))),
                (cleanup_module, "_observe_policy", lambda *args: (_ for _ in ()).throw(AssertionError("rebound"))),
                (cleanup_module, "_expected_result_template", lambda *args: (_ for _ in ()).throw(AssertionError("rebound"))),
                (cleanup_module, "_POLICY_KEYS", ("forged",)),
                (cleanup_module, "_RECEIPT_KEYS", ("forged",)),
                (cleanup_module, "LocalCleanupReceipt", object),
                (cleanup_module, "_policy_root", lambda value: {"forged": True}),
                (cleanup_module, "_receipt_root", lambda value: {"forged": True}),
                (cleanup_module, "_exact_mapping", lambda *args: (_ for _ in ()).throw(AssertionError("rebound"))),
                (cleanup_module, "_artifact_id", lambda value: "forged"),
                (cleanup_module, "_relative_path", lambda value: (_ for _ in ()).throw(AssertionError("rebound"))),
                (cleanup_module, "_is_reparse_or_symlink", lambda value: True),
                (cleanup_module, "_inventory_hash", lambda value: "0" * 64),
                (cleanup_module, "_FAILURE_CODES", ("forged",)),
                (cleanup_module, "_RESULT_KEYS", ("forged",)),
                (cleanup_module, "LocalTemporaryArtifact", object),
                (cleanup_module, "CleanupArtifactDeclaration", object),
                (cleanup_module, "LocalCleanupError", RuntimeError),
            ):
                stack.enter_context(patch.object(target, name, replacement))
            stack.enter_context(patch.object(cleanup_module.json, "loads", lambda value: (_ for _ in ()).throw(AssertionError("rebound"))))
            self.assertEqual(LocalCleanupPolicy.from_bytes(policy_bytes).canonical_bytes(), policy_bytes)
            self.assertEqual(LocalCleanupReceipt.from_bytes(failed_bytes).canonical_bytes(), failed_bytes)
            with self.assertRaises(LocalCleanupError):
                LocalCleanupPolicy.from_dict({"forged": True})
            with self.assertRaises(LocalCleanupError):
                LocalCleanupReceipt.from_dict({"forged": True})
            with self.assertRaises(LocalCleanupError):
                create_local_cleanup_policy(self.work, self.artifacts)  # type: ignore[arg-type]
            unsafe = (LocalTemporaryArtifact("fixture-alpha", "synthetic_fixture", "../outside.txt"), self.artifacts[1])
            precheck = execute_local_cleanup(self.policy, self.temp, unsafe)
            self.assertEqual((precheck.action_status, precheck.failure_code), ("failed", "cleanup_precheck_failed"))
            fresh = create_local_cleanup_policy(self.temp, self.artifacts)
            self.assertEqual(fresh.canonical_bytes(), policy_bytes)
            receipt = execute_local_cleanup(fresh, self.temp, self.artifacts)
            self.assertEqual(receipt.action_status, "completed")
            self.assertEqual(LocalCleanupPolicy.from_bytes(policy_bytes).canonical_bytes(), policy_bytes)
            self.assertEqual(LocalCleanupReceipt.from_bytes(receipt.canonical_bytes()), receipt)
            receipt.validate_against(fresh, self.temp, self.artifacts)
        self.assertEqual(LocalCleanupPolicy.from_bytes(policy_bytes).canonical_bytes(), policy_bytes)
        self.assertEqual(LocalCleanupReceipt.from_bytes(failed_bytes).canonical_bytes(), failed_bytes)


    def test_rebound_policy_methods_cannot_bypass_pre_action_validation(self) -> None:
        forged = replace(self.policy, authority_id="forged-authority")
        with ExitStack() as stack:
            stack.enter_context(patch.object(LocalCleanupPolicy, "validate", lambda value: None))
            stack.enter_context(patch.object(LocalCleanupPolicy, "sha256", lambda value: "0" * 64))
            stack.enter_context(patch.object(LocalCleanupPolicy, "canonical_bytes", lambda value: b"forged"))
            stack.enter_context(patch.object(cleanup_module.CleanupArtifactDeclaration, "to_dict", lambda value: {"forged": True}))
            stack.enter_context(patch.object(cleanup_module, "_CAPTURED_ERROR_TYPE", RuntimeError))
            with self.assertRaises(LocalCleanupError):
                execute_local_cleanup(forged, self.temp, self.artifacts)
        self.assertTrue((self.work / "alpha.txt").exists())
        self.assertTrue((self.work / "nested" / "beta.json").exists())

    def test_not_completed_receipt_is_not_resignable_across_failure_identity_or_code(self) -> None:
        original = Path.unlink
        calls = {"count": 0}
        def fail_second(path: Path) -> None:
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("scripted")
            original(path)
        receipt = cleanup_module._execute_local_cleanup_scripted(self.policy, self.temp, self.artifacts, fail_second)
        payload = receipt.to_dict()
        payload["failure_artifact_id"] = "fixture-alpha"
        self._reidentify(payload, "receipt_id", cleanup_module._RECEIPT_ID_PREFIX)
        with self.assertRaises(LocalCleanupError):
            LocalCleanupReceipt.from_dict(payload)
        with self.assertRaises(LocalCleanupError):
            LocalCleanupReceipt.from_bytes(cleanup_module._canonical_bytes(payload))
        payload = receipt.to_dict()
        payload["failure_code"] = "cleanup_postcheck_failed"
        self._reidentify(payload, "receipt_id", cleanup_module._RECEIPT_ID_PREFIX)
        with self.assertRaises(LocalCleanupError):
            LocalCleanupReceipt.from_dict(payload)
        with self.assertRaises(LocalCleanupError):
            LocalCleanupReceipt.from_bytes(cleanup_module._canonical_bytes(payload))

    def test_receipt_policy_id_is_strict_and_payload_free(self) -> None:
        receipt = execute_local_cleanup(self.policy, self.temp, self.artifacts)
        for value in ("D:/private/example-secret-location.txt", "https://example.test/private", "free text", 42):
            with self.subTest(value=value):
                payload = receipt.to_dict()
                payload["policy_id"] = value
                self._reidentify(payload, "receipt_id", cleanup_module._RECEIPT_ID_PREFIX)
                with self.assertRaises(LocalCleanupError):
                    LocalCleanupReceipt.from_dict(payload)
                with self.assertRaises(LocalCleanupError):
                    LocalCleanupReceipt.from_bytes(cleanup_module._canonical_bytes(payload))


    def test_filesystem_and_canonical_primitives_are_captured_before_drift_check(self) -> None:
        policy_bytes = self.policy.canonical_bytes()
        (self.work / "alpha.txt").write_bytes(b"mutated-alpha-content")
        path_type = type(self.work)
        old_read = path_type.read_bytes
        old_resolve = path_type.resolve
        old_lstat = path_type.lstat
        old_exists = path_type.exists
        old_is_file = path_type.is_file
        old_is_dir = path_type.is_dir
        old_is_symlink = path_type.is_symlink
        def stale_read(path: Path) -> bytes:
            if path == self.work / "alpha.txt":
                return b"synthetic alpha"
            return old_read(path)
        with ExitStack() as stack:
            stack.enter_context(patch.object(path_type, "read_bytes", stale_read))
            stack.enter_context(patch.object(path_type, "resolve", old_resolve))
            stack.enter_context(patch.object(path_type, "lstat", old_lstat))
            stack.enter_context(patch.object(path_type, "exists", old_exists))
            stack.enter_context(patch.object(path_type, "is_file", old_is_file))
            stack.enter_context(patch.object(path_type, "is_dir", old_is_dir))
            stack.enter_context(patch.object(path_type, "is_symlink", old_is_symlink))
            stack.enter_context(patch.object(cleanup_module.json, "dumps", lambda *args, **kwargs: "forged"))
            stack.enter_context(patch.object(cleanup_module.hashlib, "sha256", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("rebound"))))
            stack.enter_context(patch.object(cleanup_module, "Mapping", object))
            stack.enter_context(patch.object(cleanup_module, "Path", object))
            stack.enter_context(patch.object(cleanup_module, "_CAPTURED_ERROR_TYPE", RuntimeError))
            receipt = execute_local_cleanup(self.policy, self.temp, self.artifacts)
            self.assertEqual((receipt.action_status, receipt.failure_code), ("failed", "cleanup_precheck_failed"))
            self.assertEqual(LocalCleanupPolicy.from_bytes(policy_bytes).canonical_bytes(), policy_bytes)
            (self.work / "alpha.txt").write_bytes(b"synthetic alpha")
            self.assertEqual(create_local_cleanup_policy(self.temp, self.artifacts).canonical_bytes(), policy_bytes)
        self.assertTrue((self.work / "alpha.txt").exists())
        self.assertTrue((self.work / "nested" / "beta.json").exists())

    def test_receipt_rows_and_first_non_absent_id_are_canonical_standalone_and_live(self) -> None:
        def fail_first(path: Path) -> None:
            raise OSError("scripted")
        receipt = cleanup_module._execute_local_cleanup_scripted(self.policy, self.temp, self.artifacts, fail_first)
        self.assertEqual((receipt.action_status, receipt.failure_code, receipt.failure_artifact_id), ("failed", "cleanup_not_completed", "fixture-alpha"))
        self.assertTrue((self.work / "alpha.txt").exists())
        self.assertTrue((self.work / "nested" / "beta.json").exists())
        for transform in ("reverse_rows", "wrong_first_id"):
            with self.subTest(transform=transform):
                payload = receipt.to_dict()
                if transform == "reverse_rows":
                    payload["artifact_results"].reverse()
                else:
                    payload["failure_artifact_id"] = "fixture-beta"
                self._reidentify(payload, "receipt_id", cleanup_module._RECEIPT_ID_PREFIX)
                with self.assertRaises(LocalCleanupError):
                    LocalCleanupReceipt.from_dict(payload)
                with self.assertRaises(LocalCleanupError):
                    LocalCleanupReceipt.from_bytes(cleanup_module._canonical_bytes(payload))
                forged = replace(
                    receipt,
                    artifact_results=tuple(payload["artifact_results"]),
                    failure_artifact_id=payload["failure_artifact_id"],
                    receipt_id=payload["receipt_id"],
                )
                with self.assertRaises(LocalCleanupError):
                    forged.validate_against(self.policy, self.temp, self.artifacts)
        receipt.validate_against(self.policy, self.temp, self.artifacts)


if __name__ == "__main__":
    unittest.main()
