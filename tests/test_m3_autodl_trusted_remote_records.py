from __future__ import annotations

import copy
from contextlib import ExitStack
import inspect
import re
import unittest
from types import MappingProxyType
from unittest.mock import patch

from src.req2web_runtime import autodl_repository_archive as archive_module
from src.req2web_runtime import autodl_trusted_remote_records as records


class TrustedRemoteRecordsTests(unittest.TestCase):
    def setUp(self) -> None:
        body = archive_module._manifest_body(
            "f" * 40, "e" * 40, [], [], b"", authority=archive_module._AUTHORITY
        )
        self.archive_manifest = archive_module.RepositoryArchiveManifest.from_dict(body)
        self.kwargs = self._kwargs(self.archive_manifest)
        self.plan, self.run, self.closeout = records.create_local_r0_record_bundle(**self.kwargs)

    def _kwargs(self, manifest):
        return {
            "amendment_sha256": "a" * 64,
            "archive_manifest": manifest,
            "cases": [
                {"case_id": "path3-commerce-checkout", "d17_path": "path_3", "metadata_state": "recorded_not_verified_no_action", "provider_input": {"input_id": "commerce-input", "sha256": "1" * 64, "byte_length": 301}, "frozen_g0": {"package_id": "commerce-g0", "sha256": "2" * 64}},
                {"case_id": "path3-media-analysis", "d17_path": "path_3", "metadata_state": "recorded_not_verified_no_action", "provider_input": {"input_id": "media-input", "sha256": "3" * 64, "byte_length": 302}, "frozen_g0": {"package_id": "media-g0", "sha256": "4" * 64}},
            ],
            "model": {"exact_revision": "f" * 40, "files": [{"relative_path": ".gitattributes", "byte_length": 10, "sha256": "4" * 64}, {"relative_path": "config.json", "byte_length": 20, "sha256": "5" * 64}, {"relative_path": "model.safetensors", "byte_length": 30, "sha256": "6" * 64}]},
            "runtime": {"runtime_id": "recorded-runtime", "image_id": "recorded-image", "artifacts": [{"relative_path": "packages/torch.whl", "byte_length": 40, "sha256": "7" * 64}]},
            "action_time": {"issued_at_utc": "2026-07-26T00:00:00Z", "expires_at_utc": "2026-08-01T00:00:00Z", "single_use_state": "recorded_single_use_not_verified_no_action"},
            "target_instance": {"provider": "AutoDL", "instance_id": "recorded-instance", "instance_class": "autodl-gpu", "state": "recorded_not_verified_no_action"},
            "gpu": {"model": "NVIDIA RTX 5090", "selected_index": 0, "uuid_state": "platform_uuid_absent", "uuid": "absent_by_platform_no_action", "vram_bytes": 34359738368, "state": "recorded_not_verified_no_action"},
            "quoted_price": {"currency": "CNY", "quoted_price_milli": 50000, "state": "recorded_not_verified_no_action"},
            "ssh": {"host": "recorded-host", "port": 22, "host_key_sha256": "8" * 64, "temporary_public_key_sha256": "9" * 64, "state": "recorded_not_verified_no_action"},
            "old_instance": {"instance_id": "old-failed-instance", "release_evidence_id": "release-record", "release_evidence_sha256": "b" * 64, "access_revocation_evidence_id": "revoke-record", "access_revocation_evidence_sha256": "c" * 64, "state": "recorded_released_and_access_revoked_not_verified_no_action"},
            "caps": {"gpu_count": 1, "price_cap_milli": 100000, "time_cap_seconds": 7200, "storage_cap_bytes": 50000000000, "transfer_cap_bytes": 20000000000},
            "result_return": {"location_id": "recorded-result-return", "location_sha256": "d" * 64, "max_file_count": len(records.RESULT_RETURN_PATHS), "max_total_bytes": 10000000, "allowed_paths": list(records.RESULT_RETURN_PATHS), "state": "recorded_not_verified_no_action"},
            "cleanup_plan": {"plan_id": "recorded-cleanup-plan", "plan_sha256": "e" * 64, "state": "recorded_not_verified_no_action"},
        }

    def _identified(self, data):
        return records._identified(data, records._AUTHORITY)

    def test_exact_local_r0_chain_contains_required_operational_records(self):
        plan = self.plan.to_dict()
        operational = plan["operational"]
        self.assertEqual(plan["archive_manifest"]["manifest_id"], self.archive_manifest.to_dict()["manifest_id"])
        self.assertEqual(plan["policy"]["implementation_commit_sha"], self.archive_manifest.to_dict()["commit_sha"])
        self.assertEqual(plan["model_inventory"]["file_count"], 3)
        self.assertEqual(plan["runtime_inventory"]["artifact_count"], 1)
        self.assertEqual(operational["gpu"]["uuid_state"], "platform_uuid_absent")
        self.assertEqual(operational["result_return"]["allowed_paths"], list(records.RESULT_RETURN_PATHS))
        self.assertIn("run/model_inventory.json", operational["result_return"]["allowed_paths"])
        self.assertIn("cases/path3-media-analysis/gate_delivery_outcome.json", operational["result_return"]["allowed_paths"])
        self.assertEqual(plan["profile_selection"]["selection_basis"], "owner_approved_quality_experiment_no_action_record")
        self.assertFalse(plan["profile_selection"]["human_override_applied"])
        self.assertEqual(operational["target_instance"]["provider"], "AutoDL")
        self.assertEqual([row["step"] for row in self.closeout.to_dict()["expectations"]], list(records.CLOSEOUT_STEP_ORDER))
        for item in (self.plan, self.run, self.closeout):
            self.assertEqual(item.validate(), item)
            self.assertEqual(type(item.from_bytes(item.canonical_bytes())), type(item))
            self.assertTrue(all(value is False for value in item.to_dict()["no_action_flags"].values()))

    def test_archive_must_be_current_owning_manifest_type_or_canonical_bytes(self):
        mapping = self._kwargs(self.archive_manifest)
        mapping["archive_manifest"] = self.archive_manifest.to_dict()
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "exact_type_or_bytes_required"):
            records.create_local_r0_record_bundle(**mapping)
        bytes_input = self._kwargs(self.archive_manifest)
        bytes_input["archive_manifest"] = self.archive_manifest.canonical_bytes()
        self.assertEqual(records.create_local_r0_record_bundle(**bytes_input)[0], self.plan)
        malformed = self._kwargs(self.archive_manifest)
        malformed["archive_manifest"] = self.archive_manifest.canonical_bytes() + b"\n"
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "archive_manifest_live_validation_failed"):
            records.create_local_r0_record_bundle(**malformed)
        forged = object.__new__(archive_module.RepositoryArchiveManifest)
        object.__setattr__(forged, "_data", MappingProxyType({}))
        direct = self._kwargs(self.archive_manifest)
        direct["archive_manifest"] = forged
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "archive_manifest_live_validation_failed"):
            records.create_local_r0_record_bundle(**direct)
        drift = self.plan.to_dict()
        drift["archive_manifest"]["commit_sha"] = "0" * 40
        self._identified(drift)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "archive_manifest_replay_invalid"):
            records.TrustedRemoteActionTimePlan.from_dict(drift)

    def test_full_manifest_replay_rejects_forged_summary_policy_and_manifest_tampering(self):
        forged_summary = self.plan.to_dict()
        forged_summary["archive_binding"] = {
            "schema_version": "req2web.runtime.autodl_policy_filtered_repository_archive_manifest.v1",
            "manifest_id": "0" * 64, "manifest_sha256": "1" * 64, "commit_sha": "2" * 40,
            "tree_sha": "3" * 40, "archive_sha256": "4" * 64,
            "tracked_inventory_sha256": "5" * 64, "coverage_sha256": "6" * 64,
        }
        forged_summary["policy"]["implementation_commit_sha"] = "2" * 40
        forged_summary["policy"]["implementation_tree_sha"] = "3" * 40
        self._identified(forged_summary)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "archive_binding_replay_mismatch"):
            records.TrustedRemoteActionTimePlan.from_dict(forged_summary)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "archive_binding_replay_mismatch"):
            records.TrustedRemoteActionTimePlan.from_bytes(records._AUTHORITY.dumps(forged_summary))
        full_tamper = self.plan.to_dict()
        full_tamper["archive_manifest"]["commit_sha"] = "0" * 40
        full_tamper["archive_manifest"]["tree_sha"] = "1" * 40
        full_tamper["archive_manifest"]["archive"]["sha256"] = "2" * 64
        self._identified(full_tamper)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "archive_manifest_replay_invalid"):
            records.TrustedRemoteActionTimePlan.from_dict(full_tamper)
        nested_run_tamper = self.run.to_dict()
        nested_run_tamper["action_time_plan"] = full_tamper
        self._identified(nested_run_tamper)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "archive_manifest_replay_invalid"):
            records.TrustedRemoteRunEvidence.from_dict(nested_run_tamper)
        nested_closeout_tamper = self.closeout.to_dict()
        nested_closeout_tamper["action_time_plan"] = full_tamper
        self._identified(nested_closeout_tamper)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "archive_manifest_replay_invalid"):
            records.TrustedRemoteCloseoutExpectation.from_dict(nested_closeout_tamper)
        binding_tamper = self.plan.to_dict()
        binding_tamper["archive_binding"]["coverage_sha256"] = "0" * 64
        self._identified(binding_tamper)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "archive_binding_replay_mismatch"):
            records.TrustedRemoteActionTimePlan.from_dict(binding_tamper)
        self.assertEqual(
            records.TrustedRemoteActionTimePlan.from_bytes(self.plan.canonical_bytes()), self.plan
        )

    def test_constructor_bytes_and_inventory_forgery_fail_closed(self):
        forged = object.__new__(records.TrustedRemoteActionTimePlan)
        object.__setattr__(forged, "_canonical", b"{}")
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "exact_keys|private_storage"):
            forged.canonical_bytes()
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "record_not_canonical"):
            records.TrustedRemoteActionTimePlan.from_bytes(self.plan.canonical_bytes() + b"\n")
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "duplicate_json_key"):
            records.TrustedRemoteActionTimePlan.from_bytes(b'{"x":1,"x":2}')
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "non_finite_json_forbidden"):
            records.TrustedRemoteActionTimePlan.from_bytes(b'{"x":NaN}')
        model = self.plan.to_dict()
        model["model_inventory"]["files"].reverse()
        self._identified(model)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "rows_order_or_duplicate"):
            records.TrustedRemoteActionTimePlan.from_dict(model)
        runtime = self.plan.to_dict()
        runtime["runtime_inventory"]["total_bytes"] += 1
        self._identified(runtime)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "count_or_total_drift"):
            records.TrustedRemoteActionTimePlan.from_dict(runtime)

    def test_runtime_inventory_accepts_real_conda_names_and_rejects_noncanonical_edges(self):
        mapping = self._kwargs(self.archive_manifest)
        accepted_paths = (
            "lib/libstdc++.so.6",
            "lib/python3.11/site-packages/setuptools/launcher manifest.xml",
            "lib/python3.11/site-packages/setuptools/script (dev).tmpl",
            "lib/python3.11/site-packages/torch-2.7.1+cu128.dist-info/METADATA",
        )
        mapping["runtime"]["artifacts"] = [
            {
                "relative_path": path,
                "byte_length": index,
                "sha256": f"{index + 1:x}" * 64,
            }
            for index, path in enumerate(accepted_paths)
        ]
        plan = records.create_local_r0_record_bundle(**mapping)[0]
        self.assertEqual(
            tuple(
                row["relative_path"]
                for row in plan.to_dict()["runtime_inventory"]["artifacts"]
            ),
            accepted_paths,
        )

        for invalid_path in (
            "/absolute",
            "../traversal",
            "runtime//file",
            "runtime/./file",
            "runtime/../file",
            "runtime/ leading-space",
            "runtime/trailing-space ",
            "runtime/tab\tname",
            "runtime/back\\slash",
        ):
            invalid = self._kwargs(self.archive_manifest)
            invalid["runtime"]["artifacts"][0]["relative_path"] = invalid_path
            with self.subTest(invalid_path=invalid_path):
                with self.assertRaisesRegex(
                    records.TrustedRemoteRecordsError,
                    "runtime_artifact_path_not_canonical_posix_path",
                ):
                    records.create_local_r0_record_bundle(**invalid)

        for invalid_length in (-1, 1.5, True, "0"):
            invalid = self._kwargs(self.archive_manifest)
            invalid["runtime"]["artifacts"][0]["byte_length"] = invalid_length
            with self.subTest(invalid_length=invalid_length):
                with self.assertRaisesRegex(
                    records.TrustedRemoteRecordsError,
                    "runtime_artifact_byte_length_not_nonnegative_integer",
                ):
                    records.create_local_r0_record_bundle(**invalid)

    def test_action_time_instance_gpu_ssh_price_result_and_cleanup_fields_fail_closed(self):
        overdue = self.plan.to_dict()
        overdue["operational"]["action_time"]["expires_at_utc"] = "2026-08-03T00:00:00Z"
        self._identified(overdue)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "action_time_window"):
            records.TrustedRemoteActionTimePlan.from_dict(overdue)
        price = self.plan.to_dict()
        price["operational"]["quoted_price"]["quoted_price_milli"] = 100001
        self._identified(price)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "quoted_price_exceeds_cap"):
            records.TrustedRemoteActionTimePlan.from_dict(price)
        gpu = self.plan.to_dict()
        gpu["operational"]["gpu"]["uuid"] = "not-absent"
        self._identified(gpu)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "gpu_uuid_absent_state_invalid"):
            records.TrustedRemoteActionTimePlan.from_dict(gpu)
        provider = self.plan.to_dict()
        provider["operational"]["target_instance"]["provider"] = "other"
        self._identified(provider)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "target_instance_state_invalid"):
            records.TrustedRemoteActionTimePlan.from_dict(provider)
        port = self.plan.to_dict()
        port["operational"]["ssh"]["port"] = 65536
        self._identified(port)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "ssh_port_out_of_range"):
            records.TrustedRemoteActionTimePlan.from_dict(port)
        cleanup = self.plan.to_dict()
        cleanup["operational"]["cleanup_plan"]["state"] = "completed"
        self._identified(cleanup)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "cleanup_plan_state_invalid"):
            records.TrustedRemoteActionTimePlan.from_dict(cleanup)

    def test_case_profile_precision_flags_result_paths_and_closeout_state_promotions_fail(self):
        case = self.plan.to_dict(); case["cases"].reverse(); self._identified(case)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "case_inventory_order_path_or_state_invalid"):
            records.TrustedRemoteActionTimePlan.from_dict(case)
        profile = self.plan.to_dict(); profile["profile_selection"]["selected_device"] = "other"; self._identified(profile)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "profile_or_device_selection_invalid"):
            records.TrustedRemoteActionTimePlan.from_dict(profile)
        override = self.plan.to_dict(); override["profile_selection"]["human_override_applied"] = True; self._identified(override)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "profile_or_device_selection_invalid"):
            records.TrustedRemoteActionTimePlan.from_dict(override)
        precision = self.plan.to_dict(); precision["precision"]["selected_quantization"] = "int4"; self._identified(precision)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "dtype_or_quantization_invalid"):
            records.TrustedRemoteActionTimePlan.from_dict(precision)
        flags = self.plan.to_dict(); flags["no_action_flags"]["ssh_authorized"] = True; self._identified(flags)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "no_action_flag_state_promotion"):
            records.TrustedRemoteActionTimePlan.from_dict(flags)
        paths = self.run.to_dict(); paths["result_inventory"]["allowed_paths"][0] = "../collision"; self._identified(paths)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "canonical_posix|path_traversal"):
            records.TrustedRemoteRunEvidence.from_dict(paths)
        duplicate = self.run.to_dict(); duplicate["result_inventory"]["allowed_paths"][1] = duplicate["result_inventory"]["allowed_paths"][0]; self._identified(duplicate)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "path_traversal_or_duplicate"):
            records.TrustedRemoteRunEvidence.from_dict(duplicate)
        closeout = self.closeout.to_dict(); closeout["expectations"][2]["observed"] = True; self._identified(closeout)
        with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "closeout_expectation_state_promotion"):
            records.TrustedRemoteCloseoutExpectation.from_dict(closeout)

    def test_definition_time_regex_constants_helpers_schema_and_archive_authority_are_captured(self):
        plan_bytes, run_bytes, closeout_bytes = self.plan.canonical_bytes(), self.run.canonical_bytes(), self.closeout.canonical_bytes()
        bad_commit = self.plan.to_dict(); bad_commit["policy"]["implementation_commit_sha"] = "NOT_A_GIT_SHA"; self._identified(bad_commit)
        bad_hash = self.plan.to_dict(); bad_hash["archive_manifest"]["coverage_sha256"] = "NOT_A_SHA256"; self._identified(bad_hash)
        bad_schema = self.plan.to_dict(); bad_schema["schema_version"] = "rebound"; self._identified(bad_schema)
        bad_state = self.plan.to_dict(); bad_state["record_state"] = "rebound"; self._identified(bad_state)
        bad_case_path = self.plan.to_dict(); bad_case_path["cases"][0]["d17_path"] = "rebound"; self._identified(bad_case_path)
        bad_profile = self.plan.to_dict(); bad_profile["profile_selection"]["selected_device"] = "rebound"; self._identified(bad_profile)
        bad_precision = self.plan.to_dict(); bad_precision["precision"]["selected_dtype"] = "rebound"; self._identified(bad_precision)
        bad_flags = self.plan.to_dict(); bad_flags["no_action_flags"]["ssh_authorized"] = True; self._identified(bad_flags)
        bad_port = self.plan.to_dict(); bad_port["operational"]["ssh"]["port"] = 0; self._identified(bad_port)
        bad_path = self.run.to_dict(); bad_path["result_inventory"]["allowed_paths"][0] = "../rebound"; self._identified(bad_path)
        bad_closeout = self.closeout.to_dict(); bad_closeout["expectations"].reverse(); self._identified(bad_closeout)
        replacements = {
            "_HEX40": re.compile(".*"), "_HEX64": re.compile(".*"), "_POSIX_PATH": re.compile(".*"),
            "ACTION_TIME_PLAN_SCHEMA": "rebound", "RUN_EVIDENCE_SCHEMA": "rebound", "CLOSEOUT_EXPECTATION_SCHEMA": "rebound",
            "RECORD_STATE": "rebound", "REQUIRED_CASE_IDS": ("rebound",), "PATH_3": "rebound", "PROFILE": "rebound",
            "REQUIRED_DEVICE": "rebound", "DTYPE": "rebound", "QUANTIZATION": "rebound", "NO_ACTION_FLAGS": ("rebound",),
            "RESULT_RETURN_PATHS": ("rebound",), "CLOSEOUT_STEP_ORDER": ("rebound",), "_ARCHIVE_MANIFEST_TYPE": object,
        }
        with ExitStack() as stack:
            stack.enter_context(patch.multiple(records, **replacements))
            for name in (
                "_validate_plan", "_archive_binding", "_archive_full_binding", "_replay_archive_manifest", "_resolve_archive", "_text", "_integer", "_path", "_keys", "_rows",
                "_inventory_identity", "_model_inventory", "_runtime_inventory", "_operational", "_historical", "_false_flags",
            ):
                stack.enter_context(patch.object(records, name, side_effect=RuntimeError("rebound")))
            for name in ("_ARCHIVE_FROM_BYTES", "_ARCHIVE_CANONICAL_BYTES", "_ARCHIVE_TO_DICT", "_ARCHIVE_SHA256", "_ARCHIVE_VALIDATED_DATA", "_ARCHIVE_JSON_DUMPS", "_ARCHIVE_JSON_SHA256"):
                stack.enter_context(patch.object(records, name, side_effect=RuntimeError("rebound")))
            for name in ("from_bytes", "to_dict", "sha256", "canonical_bytes", "_validated_data"):
                stack.enter_context(patch.object(archive_module.RepositoryArchiveManifest, name, side_effect=RuntimeError("rebound")))
            stack.enter_context(patch.object(archive_module, "RepositoryArchiveManifest", object))
            self.assertEqual(self.plan.canonical_bytes(), plan_bytes)
            self.assertEqual(self.run.canonical_bytes(), run_bytes)
            self.assertEqual(self.closeout.canonical_bytes(), closeout_bytes)
            plan, run, closeout = records.create_local_r0_record_bundle(**self.kwargs)
            self.assertEqual((plan.canonical_bytes(), run.canonical_bytes(), closeout.canonical_bytes()), (plan_bytes, run_bytes, closeout_bytes))
            empty_runtime = self._kwargs(self.archive_manifest)
            empty_runtime["runtime"] = {"runtime_id": "", "image_id": "", "artifacts": [{"relative_path": "runtime.bin", "byte_length": 1, "sha256": "f" * 64}]}
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "runtime_id_not_nonempty_string"):
                records.create_local_r0_record_bundle(**empty_runtime)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "implementation_commit_sha_invalid"):
                records.TrustedRemoteActionTimePlan.from_dict(bad_commit)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "archive_manifest_replay_invalid"):
                records.TrustedRemoteActionTimePlan.from_dict(bad_hash)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "action_time_plan_schema_or_state_invalid"):
                records.TrustedRemoteActionTimePlan.from_dict(bad_schema)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "action_time_plan_schema_or_state_invalid"):
                records.TrustedRemoteActionTimePlan.from_dict(bad_state)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "case_inventory_order_path_or_state_invalid"):
                records.TrustedRemoteActionTimePlan.from_dict(bad_case_path)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "profile_or_device_selection_invalid"):
                records.TrustedRemoteActionTimePlan.from_dict(bad_profile)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "dtype_or_quantization_invalid"):
                records.TrustedRemoteActionTimePlan.from_dict(bad_precision)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "no_action_flag_state_promotion"):
                records.TrustedRemoteActionTimePlan.from_dict(bad_flags)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "ssh_port_not_positive_integer"):
                records.TrustedRemoteActionTimePlan.from_dict(bad_port)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "canonical_posix|path_traversal"):
                records.TrustedRemoteRunEvidence.from_dict(bad_path)
            with self.assertRaisesRegex(records.TrustedRemoteRecordsError, "closeout_expectation_state_promotion"):
                records.TrustedRemoteCloseoutExpectation.from_dict(bad_closeout)

    def test_public_factory_has_no_trust_or_execution_override(self):
        names = set(inspect.signature(records.create_local_r0_record_bundle).parameters)
        self.assertTrue({"authority", "callback", "parser", "hash", "validator", "executor", "transport", "override"}.isdisjoint(names))
        self.assertIn("archive_manifest", names)
        self.assertNotIn("implementation_commit_sha", names)


if __name__ == "__main__":
    unittest.main()
