from __future__ import annotations

import copy
from contextlib import ExitStack
import hashlib
import inspect
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_runtime import autodl_repository_archive as archive_module  # noqa: E402
from req2web_runtime import autodl_trusted_remote_qwen_raw as raw_module  # noqa: E402
from req2web_runtime import autodl_trusted_remote_records as records  # noqa: E402
from req2web_provider import semantic_candidate as semantic_module  # noqa: E402
from test_m3_semantic_candidate_assembly import candidate_payload, raw_bytes  # noqa: E402


def _candidate_raw() -> bytes:
    return raw_bytes(candidate_payload())


class TrustedRemoteQwenRawTests(unittest.TestCase):
    def setUp(self) -> None:
        body = archive_module._manifest_body("f" * 40, "e" * 40, [], [], b"", authority=archive_module._AUTHORITY)
        manifest = archive_module.RepositoryArchiveManifest.from_dict(body)
        self.plan, _, _ = records.create_local_r0_record_bundle(**self._plan_kwargs(manifest))
        self.profile = raw_module.create_trusted_remote_qwen_runtime_profile(self.plan)
        self.loader = raw_module.create_trusted_remote_qwen_loader_contract(self.profile)
        self.raw_bytes = _candidate_raw()
        self.record = raw_module.capture_local_contract_candidate_raw_response(
            self.plan, self.profile, self.loader, "path3-commerce-checkout", self.raw_bytes
        )

    def _plan_kwargs(self, manifest):
        return {
            "amendment_sha256": "a" * 64,
            "archive_manifest": manifest,
            "cases": [
                {"case_id": "path3-commerce-checkout", "d17_path": "path_3", "metadata_state": "recorded_not_verified_no_action", "provider_input": {"input_id": "commerce-input", "sha256": "1" * 64, "byte_length": 301}, "frozen_g0": {"package_id": "commerce-g0", "sha256": "2" * 64}},
                {"case_id": "path3-media-analysis", "d17_path": "path_3", "metadata_state": "recorded_not_verified_no_action", "provider_input": {"input_id": "media-input", "sha256": "3" * 64, "byte_length": 302}, "frozen_g0": {"package_id": "media-g0", "sha256": "4" * 64}},
            ],
            "model": {"exact_revision": "f" * 40, "files": [{"relative_path": "config.json", "byte_length": 20, "sha256": "5" * 64}, {"relative_path": "model.safetensors", "byte_length": 30, "sha256": "6" * 64}]},
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

    def _identified(self, value):
        return raw_module._identified(value, raw_module._AUTHORITY)

    def test_round_trip_replays_exact_raw_bytes_without_semantic_parse(self):
        for item, item_type in ((self.profile, raw_module.TrustedRemoteQwenRuntimeProfile), (self.loader, raw_module.TrustedRemoteQwenLoaderContract), (self.record, raw_module.TrustedRemoteQwenRawResponseRecord)):
            self.assertEqual(item.validate(), item)
            self.assertEqual(item_type.from_bytes(item.canonical_bytes()).canonical_bytes(), item.canonical_bytes())
            self.assertEqual(hashlib.sha256(item.canonical_bytes()).hexdigest(), item.sha256())
        replay = raw_module.replay_trusted_remote_qwen_raw_response(self.record)
        self.assertEqual(replay.raw_bytes, self.raw_bytes)
        self.assertEqual(replay.to_dict(), self.record.to_dict()["raw_response"]["provider_raw_response"])
        data = self.record.to_dict()
        self.assertEqual(data["status"]["provider_calls_after_first_complete_raw"], 0)
        self.assertEqual(data["case_binding"]["provider_input"]["input_id"], "commerce-input")

    def test_capture_is_explicitly_non_attested_and_non_routable(self):
        data = self.record.to_dict()
        self.assertEqual(data["provider_identity"]["provenance_state"], raw_module.PROVENANCE_NOT_ATTESTED)
        self.assertEqual(data["provider_identity"]["provider_branch_state"], "not_routable_slice_1_local_capture")
        self.assertEqual(data["status"]["provider_invocation_state"], "not_invoked")
        self.assertFalse(data["status"]["model_loaded"])
        self.assertFalse(data["status"]["network_invoked"])
        self.assertFalse(data["status"]["run_occurred"])

    def test_direct_constructor_and_private_storage_forgery_fail_closed(self):
        with self.assertRaisesRegex(raw_module.TrustedRemoteQwenRawContractError, "exact_keys"):
            raw_module.TrustedRemoteQwenRawResponseRecord({"record_id": "0" * 64})
        object.__setattr__(self.record, "_canonical", b"{}")
        for method in (self.record.to_dict, self.record.canonical_bytes, self.record.sha256, self.record.validate):
            with self.assertRaises(raw_module.TrustedRemoteQwenRawContractError):
                method()

    def test_unknown_duplicate_and_noncanonical_bytes_fail_closed(self):
        data = self.record.to_dict()
        data["unexpected"] = "x"
        data = self._identified(data)
        with self.assertRaisesRegex(raw_module.TrustedRemoteQwenRawContractError, "exact_keys"):
            raw_module.TrustedRemoteQwenRawResponseRecord.from_dict(data)
        with self.assertRaisesRegex(raw_module.TrustedRemoteQwenRawContractError, "duplicate_json_key"):
            raw_module.TrustedRemoteQwenRawResponseRecord.from_bytes(b'{"record_id":"a","record_id":"b"}')
        with self.assertRaisesRegex(raw_module.TrustedRemoteQwenRawContractError, "not_canonical"):
            raw_module.TrustedRemoteQwenRawResponseRecord.from_bytes(self.record.canonical_bytes() + b" ")

    def test_equivalent_noncanonical_base64_pad_bits_fail_closed(self):
        pad_record = raw_module.capture_local_contract_candidate_raw_response(
            self.plan, self.profile, self.loader, "path3-commerce-checkout", b"X"
        )
        candidate = pad_record.to_dict()
        self.assertEqual(candidate["raw_response"]["base64"], "WA==")
        candidate["raw_response"]["base64"] = "WB=="
        with self.assertRaisesRegex(raw_module.TrustedRemoteQwenRawContractError, "base64_not_canonical"):
            raw_module.TrustedRemoteQwenRawResponseRecord.from_dict(self._identified(candidate))

    def test_cross_case_request_inventory_raw_and_status_tamper_fail_closed(self):
        media_record = raw_module.capture_local_contract_candidate_raw_response(
            self.plan, self.profile, self.loader, "path3-media-analysis", self.raw_bytes
        )
        with self.assertRaisesRegex(raw_module.TrustedRemoteQwenRawContractError, "case_request_or_d17_projection_cross_binding"):
            raw_module.validate_trusted_remote_qwen_raw_response_binding(
                media_record, self.plan, "path3-commerce-checkout"
            )
        self.assertEqual(
            raw_module.validate_trusted_remote_qwen_raw_response_binding(
                self.record, self.plan, "path3-commerce-checkout"
            ),
            self.record,
        )
        cases = []
        wrong_request = self.record.to_dict()
        wrong_request["case_binding"]["provider_input"]["sha256"] = "f" * 64
        cases.append((wrong_request, "case_request_or_d17_projection"))
        wrong_inventory = self.record.to_dict()
        wrong_inventory["runtime_profile"]["model_inventory"]["files"][0]["sha256"] = "f" * 64
        cases.append((wrong_inventory, "profile_id_invalid|runtime_profile"))
        wrong_raw = self.record.to_dict()
        wrong_raw["raw_response"]["base64"] = "eA=="
        cases.append((wrong_raw, "raw_response_identity"))
        wrong_status = self.record.to_dict()
        wrong_status["status"]["provider_calls_after_first_complete_raw"] = 1
        cases.append((wrong_status, "raw_status_invalid"))
        for candidate, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(raw_module.TrustedRemoteQwenRawContractError, expected):
                    raw_module.TrustedRemoteQwenRawResponseRecord.from_dict(self._identified(candidate))

    def test_laundering_and_invalid_raw_shape_fail_closed(self):
        for path, value, expected in (
            (("provider_identity", "provenance_state"), "real_provider_attested", "provider_identity_or_provenance"),
            (("provider_identity", "provider_branch_state"), "trusted_remote_local_qwen", "provider_identity_or_provenance"),
            (("status", "real_provider_provenance"), "real_provider_attested", "raw_status_invalid"),
        ):
            candidate = self.record.to_dict()
            candidate[path[0]][path[1]] = value
            with self.assertRaisesRegex(raw_module.TrustedRemoteQwenRawContractError, expected):
                raw_module.TrustedRemoteQwenRawResponseRecord.from_dict(self._identified(candidate))
        for raw in (
            b"not-json", b"\xff\xfe", b"\xef\xbb\xbf{\"not\":\"json\"}",
            b'{"schema_version":"x","schema_version":"x"}',
            b'{"schema_version":"req2web.provider.semantic_candidate.v1","unknown":"field"}',
        ):
            with self.subTest(raw=raw):
                preserved = raw_module.capture_local_contract_candidate_raw_response(
                    self.plan, self.profile, self.loader, "path3-commerce-checkout", raw
                )
                replay = raw_module.replay_trusted_remote_qwen_raw_response(preserved)
                self.assertEqual(replay.raw_bytes, raw)
                self.assertEqual(preserved.to_dict()["status"]["capture_state"], raw_module.LOCAL_CAPTURE_STATE)

    def test_rebinding_of_globals_classes_methods_and_helpers_cannot_change_captured_authority(self):
        profile_bytes, loader_bytes, record_bytes = self.profile.canonical_bytes(), self.loader.canonical_bytes(), self.record.canonical_bytes()
        with ExitStack() as stack:
            stack.enter_context(patch.object(raw_module, "_ACTION_PLAN_TYPE", object))
            stack.enter_context(patch.object(raw_module, "_ACTION_PLAN_FROM_BYTES", side_effect=RuntimeError("rebound")))
            stack.enter_context(patch.object(raw_module, "_RAW_TYPE", object))
            stack.enter_context(patch.object(raw_module, "_RAW_FROM_BYTES", side_effect=RuntimeError("rebound")))
            stack.enter_context(patch.object(raw_module, "_hex64", side_effect=RuntimeError("rebound")))
            stack.enter_context(patch.object(raw_module, "_validate_profile", side_effect=RuntimeError("rebound")))
            stack.enter_context(patch.object(raw_module, "_capture_local_contract_candidate_raw_response_impl", side_effect=RuntimeError("rebound")))
            stack.enter_context(patch.object(semantic_module.ProviderRawResponse, "from_bytes", side_effect=RuntimeError("rebound")))
            stack.enter_context(patch.object(semantic_module, "parse_provider_raw_response", side_effect=RuntimeError("rebound")))
            stack.enter_context(patch.object(semantic_module, "_reject_duplicate_pairs", side_effect=RuntimeError("rebound")))
            stack.enter_context(patch.object(semantic_module.ModelSemanticCandidate, "from_dict", side_effect=RuntimeError("rebound")))
            self.assertEqual(self.profile.canonical_bytes(), profile_bytes)
            self.assertEqual(self.loader.canonical_bytes(), loader_bytes)
            self.assertEqual(self.record.canonical_bytes(), record_bytes)
            self.assertEqual(raw_module.replay_trusted_remote_qwen_raw_response(self.record).raw_bytes, self.raw_bytes)
            new_profile = raw_module.create_trusted_remote_qwen_runtime_profile(self.plan)
            new_loader = raw_module.create_trusted_remote_qwen_loader_contract(new_profile)
            new_record = raw_module.capture_local_contract_candidate_raw_response(self.plan, new_profile, new_loader, "path3-commerce-checkout", self.raw_bytes)
            self.assertEqual(new_record.canonical_bytes(), record_bytes)

    def test_grouped_provenance_and_status_rebinding_cannot_promote_records(self):
        baseline = self.record.canonical_bytes()
        forged = self.record.to_dict()
        forged["provider_identity"]["provenance_state"] = "real_provider_attested"
        forged["status"]["real_provider_provenance"] = "real_provider_attested"
        with ExitStack() as stack:
            stack.enter_context(patch.multiple(
                raw_module,
                LOCAL_CONTRACT_STATE="promoted_contract_state",
                LOCAL_CAPTURE_STATE="real_provider_attested",
                PROVENANCE_NOT_ATTESTED="real_provider_attested",
                RUNTIME_PROFILE_SCHEMA="promoted_profile_schema",
                LOADER_CONTRACT_SCHEMA="promoted_loader_schema",
                RAW_RESPONSE_RECORD_SCHEMA="promoted_raw_schema",
            ))
            recreated_profile = raw_module.create_trusted_remote_qwen_runtime_profile(self.plan)
            recreated_loader = raw_module.create_trusted_remote_qwen_loader_contract(recreated_profile)
            recreated_record = raw_module.capture_local_contract_candidate_raw_response(
                self.plan, recreated_profile, recreated_loader, "path3-commerce-checkout", self.raw_bytes
            )
            self.assertEqual(recreated_record.canonical_bytes(), baseline)
            with self.assertRaisesRegex(raw_module.TrustedRemoteQwenRawContractError, "provider_identity_or_provenance|raw_status_invalid"):
                raw_module.TrustedRemoteQwenRawResponseRecord.from_dict(self._identified(forged))

    def test_mutating_authority_holder_has_no_effect_or_acceptance_path(self):
        profile_bytes = self.profile.canonical_bytes()
        loader_bytes = self.loader.canonical_bytes()
        record_bytes = self.record.canonical_bytes()
        forged = self.record.to_dict()
        forged["record_id"] = "0" * 64
        with ExitStack() as stack:
            stack.enter_context(patch.object(raw_module._AUTHORITY, "dumps", side_effect=RuntimeError("mutable-holder")))
            stack.enter_context(patch.object(raw_module._AUTHORITY, "loads", side_effect=RuntimeError("mutable-holder")))
            stack.enter_context(patch.object(raw_module._AUTHORITY, "sha", return_value="0" * 64))
            self.assertEqual(self.profile.canonical_bytes(), profile_bytes)
            self.assertEqual(self.loader.canonical_bytes(), loader_bytes)
            self.assertEqual(self.record.canonical_bytes(), record_bytes)
            profile = raw_module.create_trusted_remote_qwen_runtime_profile(self.plan)
            loader = raw_module.create_trusted_remote_qwen_loader_contract(profile)
            record = raw_module.capture_local_contract_candidate_raw_response(
                self.plan, profile, loader, "path3-commerce-checkout", self.raw_bytes
            )
            self.assertEqual(record.canonical_bytes(), record_bytes)
            with self.assertRaisesRegex(raw_module.TrustedRemoteQwenRawContractError, "record_id_invalid"):
                raw_module.TrustedRemoteQwenRawResponseRecord.from_dict(forged)

    def test_record_method_rebinding_cannot_substitute_object_storage_or_case(self):
        media = raw_module.capture_local_contract_candidate_raw_response(
            self.plan, self.profile, self.loader, "path3-media-analysis", self.raw_bytes
        )
        profile_bytes = self.profile.canonical_bytes()
        loader_bytes = self.loader.canonical_bytes()
        commerce_bytes = self.record.canonical_bytes()
        def rebound(*_args, **_kwargs):
            raise RuntimeError("rebound-record-method")
        with ExitStack() as stack:
            for record_type in (
                raw_module.TrustedRemoteQwenRuntimeProfile,
                raw_module.TrustedRemoteQwenLoaderContract,
                raw_module.TrustedRemoteQwenRawResponseRecord,
            ):
                stack.enter_context(patch.object(record_type, "_bytes", return_value=media.canonical_bytes()))
                stack.enter_context(patch.object(record_type, "canonical_bytes", side_effect=rebound))
                stack.enter_context(patch.object(record_type, "to_dict", side_effect=rebound))
                stack.enter_context(patch.object(record_type, "from_dict", side_effect=rebound))
                stack.enter_context(patch.object(record_type, "from_bytes", side_effect=rebound))
            validated = raw_module.validate_trusted_remote_qwen_raw_response_binding(
                self.record, self.plan, "path3-commerce-checkout"
            )
            self.assertEqual(object.__getattribute__(validated, "_canonical"), commerce_bytes)
            profile = raw_module.create_trusted_remote_qwen_runtime_profile(self.plan)
            loader = raw_module.create_trusted_remote_qwen_loader_contract(profile)
            record = raw_module.capture_local_contract_candidate_raw_response(
                self.plan, profile, loader, "path3-commerce-checkout", self.raw_bytes
            )
            self.assertEqual(object.__getattribute__(profile, "_canonical"), profile_bytes)
            self.assertEqual(object.__getattribute__(loader, "_canonical"), loader_bytes)
            self.assertEqual(object.__getattribute__(record, "_canonical"), commerce_bytes)
            replay = raw_module.replay_trusted_remote_qwen_raw_response(self.record)
            self.assertEqual(replay.raw_bytes, self.raw_bytes)

    def test_public_signatures_exclude_execution_or_authority_injection(self):
        expected = {
            raw_module.create_trusted_remote_qwen_runtime_profile: ("action_time_plan",),
            raw_module.create_trusted_remote_qwen_loader_contract: ("runtime_profile",),
            raw_module.capture_local_contract_candidate_raw_response: ("action_time_plan", "runtime_profile", "loader_contract", "case_id", "raw_bytes"),
            raw_module.validate_trusted_remote_qwen_raw_response_binding: ("record", "action_time_plan", "expected_case_id"),
            raw_module.replay_trusted_remote_qwen_raw_response: ("record",),
        }
        for function, names in expected.items():
            self.assertEqual(tuple(inspect.signature(function).parameters), names, function.__name__)

    def test_module_has_no_execution_import_or_route_surface(self):
        source = (ROOT / "src" / "req2web_runtime" / "autodl_trusted_remote_qwen_raw.py").read_text(encoding="utf-8")
        for token in ("import socket", "import subprocess", "import requests", "import urllib", "import transformers", "invoke_local_qwen_provider", "CanonicalPageSpecAssembler", "TierA07"):
            self.assertNotIn(token, source)
        self.assertNotIn("real_provider_attested", source)
        self.assertNotIn("parse_provider_raw_response", source)


if __name__ == "__main__":
    unittest.main()
