from __future__ import annotations

import base64
import copy
from contextlib import ExitStack
import hashlib
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
sys.path.insert(0, str(ROOT / "tests"))

from req2web_orchestration import model_route as v1_route  # noqa: E402
from req2web_orchestration import trusted_remote_model_route as route  # noqa: E402
from req2web_provider import semantic_candidate as semantic  # noqa: E402
from req2web_runtime import autodl_repository_archive as archive  # noqa: E402
from req2web_runtime import autodl_trusted_remote_qwen_raw as raw_module  # noqa: E402
from req2web_runtime import autodl_trusted_remote_records as records  # noqa: E402
from test_m3_model_route import assembly_invalid_bytes, build_chain, build_g0  # noqa: E402
from test_m3_semantic_candidate_assembly import build_context, candidate_payload, raw_bytes  # noqa: E402


TEST_ROOT = ROOT / "outputs" / "_m3_trusted_remote_model_route_tests"


def _reference_sha(reference) -> str:
    return hashlib.sha256(
        json.dumps(reference.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _closure_cell(function, name):
    names = function.__code__.co_freevars
    if name not in names or function.__closure__ is None:
        raise AssertionError(f"missing closure cell: {function.__name__}.{name}")
    return function.__closure__[names.index(name)]


def _plan_kwargs(manifest):
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


class TrustedRemoteModelRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = TEST_ROOT / uuid4().hex
        self.work.mkdir(parents=True)
        self.context = build_context()
        self.package, self.context, self.guidance, self.reference = build_g0(self.work / "g0", context=self.context)
        self.manifest, self.selected, self.local_request, self.audit, self.preparation = build_chain(self.context)
        self.plan = self._plan_with_reference_sha(_reference_sha(self.reference))
        self.profile = raw_module.create_trusted_remote_qwen_runtime_profile(self.plan)
        self.loader = raw_module.create_trusted_remote_qwen_loader_contract(self.profile)
        self.valid_raw = raw_bytes(candidate_payload())
        self.record = self._record(self.valid_raw)
        self.envelope = route.create_untrusted_trusted_remote_real_run_envelope(self.plan, self.record, self.reference)

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def _record(self, payload: bytes):
        return raw_module.capture_local_contract_candidate_raw_response(
            self.plan, self.profile, self.loader, "path3-commerce-checkout", payload
        )

    def _plan_with_reference_sha(self, reference_sha: str):
        body = archive._manifest_body("f" * 40, "e" * 40, [], [], b"", authority=archive._AUTHORITY)
        manifest = archive.RepositoryArchiveManifest.from_dict(body)
        kwargs = _plan_kwargs(manifest)
        selection = self.selected.selection_record
        kwargs["cases"][0]["provider_input"] = {
            "input_id": selection.input_view_id,
            "sha256": selection.provider_visible_input_sha256,
            "byte_length": selection.provider_visible_input_byte_length,
        }
        kwargs["cases"][0]["frozen_g0"] = {
            "package_id": self.reference.package_id,
            "sha256": reference_sha,
        }
        plan, _, _ = records.create_local_r0_record_bundle(**kwargs)
        return plan

    def _vector(self, envelope=None):
        return route.TestOnlyNonAttestedReplayVector(
            envelope or self.envelope,
            self.plan,
            "path3-commerce-checkout",
            self.reference,
            self.package,
            self.context,
            self.guidance,
            self.manifest,
            self.selected,
            self.local_request,
            self.audit,
            self.preparation,
        )

    def _scripted_v1_success(self):
        fixture = v1_route.ScriptedLocalFixture.create(
            v1_route.SCRIPTED_FIXTURE_VALID_CANDIDATE_KEY,
            semantic.ProviderRawResponse.from_bytes(self.valid_raw),
        )
        return v1_route.TierAModelRouteOrchestrator().run(
            frozen_g0_reference=self.reference,
            package=self.package,
            context=self.context,
            guidance=self.guidance,
            manifest=self.manifest,
            selected=self.selected,
            local_request=self.local_request,
            pre_invocation_audit=self.audit,
            local_qwen_preparation=self.preparation,
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=fixture,
        )

    def test_envelope_round_trip_and_public_real_path_fail_closed(self):
        replay = route.TrustedRemoteRealRunEnvelope.from_bytes(self.envelope.canonical_bytes())
        self.assertEqual(replay.canonical_bytes(), self.envelope.canonical_bytes())
        data = self.envelope.to_dict()
        self.assertEqual(data["source_state"], "untrusted_slice_1_local_candidate")
        self.assertEqual(data["attestation_state"], "not_verified_real_provider")
        self.assertEqual(data["execution_state"]["provider_calls_after_first_complete_raw"], 0)
        outcome = route.route_untrusted_trusted_remote_real_run_envelope(
            replay, self.plan, "path3-commerce-checkout", self.reference
        )
        outcome_data = outcome.to_dict()
        self.assertEqual((outcome_data["source_branch"], outcome_data["disposition"]), ("failure", "fail_closed"))
        self.assertEqual(outcome_data["failure"]["code"], "real_provider_authority_unavailable")
        self.assertEqual(route.validate_trusted_remote_model_route_outcome_binding(outcome, self.plan, "path3-commerce-checkout", self.reference), outcome)

    def test_local_candidate_and_public_bytes_cannot_create_real_attestation(self):
        forged = self.envelope.to_dict()
        forged["source_state"] = "trusted_remote_local_qwen"
        forged = route._identified(forged, "envelope_id", route._ENVELOPE_PREFIX)
        with self.assertRaisesRegex(route.TrustedRemoteModelRouteError, "state_or_issuer"):
            route.TrustedRemoteRealRunEnvelope.from_dict(forged)
        failure = route.route_untrusted_trusted_remote_real_run_envelope(self.envelope, self.plan, "path3-commerce-checkout", self.reference).to_dict()
        failure["source_branch"] = "trusted_remote_local_qwen"
        failure["disposition"] = "real_provider_assembled"
        failure["attestation_state"] = "real_provider_attested"
        failure["source_binding"] = {"kind": "verified_real_provider"}
        failure["failure"] = None
        failure = route._identified(failure, "outcome_id", route._OUTCOME_PREFIX)
        with self.assertRaisesRegex(route.TrustedRemoteModelRouteError, "real_provider_authority_unavailable"):
            route.TrustedRemoteModelRouteOutcome.from_dict(failure)
        self.assertFalse(hasattr(route, "VerifiedTrustedRemoteProviderRecord"))

    def test_scripted_wrapper_uses_existing_authority_and_rejects_laundering(self):
        v1_outcome = self._scripted_v1_success()
        wrapped = route.wrap_serialized_scripted_local_fixture_outcome(
            v1_outcome.canonical_bytes(), self.plan, "path3-commerce-checkout", self.reference
        )
        self.assertEqual(wrapped.to_dict()["source_branch"], "scripted_local_fixture")
        self.assertEqual(wrapped.to_dict()["disposition"], "scripted_fixture_assembled")
        self.assertEqual(route.validate_trusted_remote_model_route_outcome_binding(wrapped, self.plan, "path3-commerce-checkout", self.reference), wrapped)
        forged = wrapped.to_dict()
        forged["source_branch"] = "failure"
        forged["disposition"] = "fail_closed"
        forged["attestation_state"] = "not_verified_real_provider"
        forged["source_binding"] = {"kind": "untrusted_envelope", "envelope_id": self.envelope.identity(), "envelope_sha256": self.envelope.sha256()}
        forged["failure"] = {"code": "real_provider_authority_unavailable", "stage": "source_exclusive_route", "phase": "verified_real_provider_authority", "retryable": False, "payload_disclosure": "none"}
        forged = route._identified(forged, "outcome_id", route._OUTCOME_PREFIX)
        replay = route.TrustedRemoteModelRouteOutcome.from_dict(forged)
        self.assertEqual(replay.to_dict()["source_branch"], "failure")
        # A valid failure is not allowed to be relabeled as a scripted success.
        forged["source_branch"] = "scripted_local_fixture"
        forged["disposition"] = "scripted_fixture_assembled"
        forged["attestation_state"] = "scripted_fixture_authoritative_v1"
        forged["failure"] = None
        forged = route._identified(forged, "outcome_id", route._OUTCOME_PREFIX)
        with self.assertRaises(route.TrustedRemoteModelRouteError):
            route.TrustedRemoteModelRouteOutcome.from_dict(forged)

    def test_canonical_direct_storage_and_cross_binding_fail_closed(self):
        with self.assertRaises(route.TrustedRemoteModelRouteError):
            route.TrustedRemoteRealRunEnvelope.from_bytes(self.envelope.canonical_bytes() + b"\n")
        duplicate = self.envelope.canonical_bytes().replace(b'{"action_time_plan"', b'{"action_time_plan":null,"action_time_plan"', 1)
        with self.assertRaisesRegex(route.TrustedRemoteModelRouteError, "duplicate_json_key|not_canonical"):
            route.TrustedRemoteRealRunEnvelope.from_bytes(duplicate)
        original = object.__getattribute__(self.envelope, "_canonical")
        try:
            object.__setattr__(self.envelope, "_canonical", b"{}")
            with self.assertRaises(route.TrustedRemoteModelRouteError):
                route.route_untrusted_trusted_remote_real_run_envelope(self.envelope, self.plan, "path3-commerce-checkout", self.reference)
        finally:
            object.__setattr__(self.envelope, "_canonical", original)
        with self.assertRaises(route.TrustedRemoteModelRouteError):
            route.route_untrusted_trusted_remote_real_run_envelope(self.envelope, self.plan, "path3-media-analysis", self.reference)

    def test_test_only_replay_is_structural_only_with_bounded_failures(self):
        valid = route.test_only_replay_trusted_remote_envelope(self._vector())
        self.assertEqual(valid.state, "semantic_replay_structurally_valid_test_only")
        self.assertIsNotNone(valid.assembled_page_spec)
        self.assertFalse(valid.real_provider_assembled)
        self.assertFalse(valid.result_package_created)
        self.assertFalse(valid.a07_input_created)
        malformed_record = self._record(b"not-json")
        malformed_envelope = route.create_untrusted_trusted_remote_real_run_envelope(self.plan, malformed_record, self.reference)
        malformed = route.test_only_replay_trusted_remote_envelope(self._vector(malformed_envelope))
        self.assertEqual(malformed.state, "semantic_replay_parse_failed_test_only")
        assembly_record = self._record(assembly_invalid_bytes())
        assembly_envelope = route.create_untrusted_trusted_remote_real_run_envelope(self.plan, assembly_record, self.reference)
        assembly = route.test_only_replay_trusted_remote_envelope(self._vector(assembly_envelope))
        self.assertEqual(assembly.state, "semantic_replay_assembly_failed_test_only")

    def test_d17_g0_request_and_case_drift_fail_closed(self):
        wrong_case = route.TestOnlyNonAttestedReplayVector(
            self.envelope, self.plan, "path3-media-analysis", self.reference, self.package,
            self.context, self.guidance, self.manifest, self.selected, self.local_request,
            self.audit, self.preparation,
        )
        result = route.test_only_replay_trusted_remote_envelope(wrong_case)
        self.assertEqual(result.state, "test_only_binding_failed")
        with self.assertRaises(route.TrustedRemoteModelRouteError):
            route.validate_trusted_remote_model_route_outcome_binding(
                route.route_untrusted_trusted_remote_real_run_envelope(self.envelope, self.plan, "path3-commerce-checkout", self.reference),
                self.plan,
                "path3-media-analysis",
                self.reference,
            )

    def test_action_request_runtime_raw_and_g0_tamper_fail_closed(self):
        cases = []
        action = self.envelope.to_dict()
        action["action_time_plan"]["model_inventory"]["files"][0]["sha256"] = "0" * 64
        cases.append(action)
        request = self.envelope.to_dict()
        request["case_binding"]["provider_input"]["sha256"] = "0" * 64
        cases.append(request)
        runtime = self.envelope.to_dict()
        runtime["runtime_profile"]["runtime_inventory"]["runtime_id"] = "forged-runtime"
        cases.append(runtime)
        raw = self.envelope.to_dict()
        raw["raw_record"]["raw_response"]["base64"] = base64.b64encode(b"forged").decode("ascii")
        cases.append(raw)
        g0 = self.envelope.to_dict()
        g0["frozen_g0_binding"]["reference"]["package_id"] = "forged-g0"
        cases.append(g0)
        for payload in cases:
            with self.subTest(payload=payload["envelope_id"]):
                payload = route._identified(payload, "envelope_id", route._ENVELOPE_PREFIX)
                with self.assertRaises(route.TrustedRemoteModelRouteError):
                    route.TrustedRemoteRealRunEnvelope.from_dict(payload)

    def test_grouped_global_rebinding_cannot_launder_case_source_or_no_action_state(self):
        failure = route.route_untrusted_trusted_remote_real_run_envelope(
            self.envelope, self.plan, "path3-commerce-checkout", self.reference
        )
        original_extract = route._OUTCOME_EXTRACT
        original_loads = route._loads
        original_replay_plan = route._replay_action_plan
        original_case = route._case_from_plan
        original_reference_map = route._reference_mapping_from_instance
        original_reference_binding = route._reference_binding
        plan_data, _plan_bytes = original_replay_plan(self.plan)
        commerce_case = original_case(plan_data, "path3-commerce-checkout")
        commerce_reference = original_reference_map(self.reference)
        commerce_binding = original_reference_binding(commerce_reference, commerce_case)
        with ExitStack() as stack:
            # This is the manager's decisive negative control: an old dynamic
            # validator would read these values and accept a commerce outcome as media.
            stack.enter_context(patch.object(route, "_OUTCOME_EXTRACT", lambda value: original_extract(value)))
            stack.enter_context(patch.object(route, "_loads", lambda value: original_loads(value)))
            stack.enter_context(patch.object(route, "_replay_action_plan", lambda _value: (plan_data, _plan_bytes)))
            stack.enter_context(patch.object(route, "_case_from_plan", lambda *_args: commerce_case))
            stack.enter_context(patch.object(route, "_reference_mapping_from_instance", lambda _value: commerce_reference))
            stack.enter_context(patch.object(route, "_reference_binding", lambda *_args: commerce_binding))
            with self.assertRaises(route.TrustedRemoteModelRouteError):
                route.validate_trusted_remote_model_route_outcome_binding(
                    failure, self.plan, "path3-media-analysis", self.reference
                )

        baseline = self.envelope.canonical_bytes()
        with ExitStack() as stack:
            stack.enter_context(patch.object(route, "_UNTRUSTED_SOURCE_STATE", "forged_real_provider"))
            stack.enter_context(patch.object(route, "_UNTRUSTED_ATTESTATION_STATE", "real_provider_attested"))
            stack.enter_context(patch.object(route, "_FUTURE_ISSUER", {"issuer": "forged"}))
            stack.enter_context(patch.object(route, "_ENVELOPE_EXECUTION_STATE", {"run_occurred": True}))
            stack.enter_context(patch.object(route, "_validate_execution_state", lambda _value: {"run_occurred": True}))
            recreated = route.create_untrusted_trusted_remote_real_run_envelope(
                self.plan, self.record, self.reference
            )
            self.assertEqual(recreated.canonical_bytes(), baseline)
            self.assertEqual(recreated.to_dict()["attestation_state"], "not_verified_real_provider")
            self.assertEqual(recreated.to_dict()["execution_state"]["run_occurred"], False)

        wrong_vector = route.TestOnlyNonAttestedReplayVector(
            self.envelope, self.plan, "path3-media-analysis", self.reference, self.package,
            self.context, self.guidance, self.manifest, self.selected, self.local_request,
            self.audit, self.preparation,
        )
        with patch.object(route, "_live_validate_test_vector", return_value=semantic.ProviderRawResponse.from_bytes(self.valid_raw)):
            replay = route.test_only_replay_trusted_remote_envelope(wrong_vector)
        self.assertEqual(replay.state, "test_only_binding_failed")

    def test_manager_gate_rejects_real_signals_after_record_validator_fault_injection(self):
        failure = route.route_untrusted_trusted_remote_real_run_envelope(
            self.envelope, self.plan, "path3-commerce-checkout", self.reference
        )
        forged = failure.to_dict()
        forged["source_branch"] = "trusted_remote_local_qwen"
        forged["disposition"] = "real_provider_assembled"
        forged["attestation_state"] = "real_provider_attested"
        forged["source_binding"] = {
            "kind": "verified_real_provider",
            "record_id": "forged-real-provider-record",
        }
        forged["failure"] = None
        forged = route._identified(forged, "outcome_id", route._OUTCOME_PREFIX)
        canonical = route._dumps(forged)

        validator_cells = [
            _closure_cell(route._OUTCOME_FROM_DATA, "validator"),
            _closure_cell(route._OUTCOME_FROM_BYTES, "validator"),
            _closure_cell(route._OUTCOME_EXTRACT, "validator"),
            _closure_cell(route.TrustedRemoteModelRouteOutcome.__init__, "validator"),
        ]
        original_validators = [cell.cell_contents for cell in validator_cells]
        permissive = lambda data: dict(data)
        try:
            for cell in validator_cells:
                cell.cell_contents = permissive
            forged_object = route.TrustedRemoteModelRouteOutcome(forged)
            self.assertEqual(forged_object.canonical_bytes(), canonical)
            self.assertEqual(
                route.TrustedRemoteModelRouteOutcome.from_bytes(canonical).canonical_bytes(),
                canonical,
            )
            for value in (forged_object, canonical):
                with self.subTest(entry_type=type(value).__name__):
                    with self.assertRaisesRegex(
                        route.TrustedRemoteModelRouteError,
                        "real_provider_authority_unavailable",
                    ):
                        route.validate_trusted_remote_model_route_outcome_binding(
                            value, self.plan, "path3-commerce-checkout", self.reference
                        )

            scripted = route.wrap_serialized_scripted_local_fixture_outcome(
                self._scripted_v1_success().canonical_bytes(),
                self.plan,
                "path3-commerce-checkout",
                self.reference,
            )
            for legal in (failure, failure.canonical_bytes(), scripted, scripted.canonical_bytes()):
                replay = route.validate_trusted_remote_model_route_outcome_binding(
                    legal, self.plan, "path3-commerce-checkout", self.reference
                )
                self.assertIn(replay.to_dict()["source_branch"], ("failure", "scripted_local_fixture"))
        finally:
            for cell, original in zip(validator_cells, original_validators):
                cell.cell_contents = original

    def test_mutable_defaults_and_global_data_cannot_launder_public_authority(self):
        baseline = self.envelope.canonical_bytes()
        default_snapshots = []
        targets = (
            route._validate_envelope,
            route._create_envelope_impl,
            route._validate_execution_state,
            route._reference_mapping_from_instance,
        )
        global_issuer = copy.deepcopy(route._FUTURE_ISSUER)
        global_execution = copy.deepcopy(route._ENVELOPE_EXECUTION_STATE)
        global_declarations = copy.deepcopy(route._FROZEN_G0_DECLARATIONS)
        try:
            for function in targets:
                for default in function.__defaults__ or ():
                    if type(default) is not dict:
                        continue
                    snapshot = copy.deepcopy(default)
                    default_snapshots.append((default, snapshot))
                    default.clear()
                    if "issuer_schema" in snapshot:
                        default.update({
                            "issuer_schema": "req2web.orchestration.trusted_remote_real_provider_writer.v1",
                            "issuer_identity": "forged_runtime_writer",
                            "issuer_state": "production_authority_available",
                        })
                    elif "provider_execution" in snapshot:
                        default.update({
                            "provider_execution": "executed",
                            "model_loaded": True,
                            "network_invoked": True,
                            "run_occurred": True,
                            "provider_calls_after_first_complete_raw": 1,
                            "model_repair_call_count": 1,
                        })
                    else:
                        default["forged-declaration"] = "forged-declaration"
            route._FUTURE_ISSUER.update({
                "issuer_identity": "forged_runtime_writer",
                "issuer_state": "production_authority_available",
            })
            route._ENVELOPE_EXECUTION_STATE.update({"run_occurred": True, "model_loaded": True})
            route._FROZEN_G0_DECLARATIONS["forged-declaration"] = "forged-declaration"

            recreated = route.create_untrusted_trusted_remote_real_run_envelope(
                self.plan, self.record, self.reference
            )
            self.assertEqual(recreated.canonical_bytes(), baseline)
            self.assertEqual(
                recreated.to_dict()["future_issuer"]["issuer_identity"],
                "unavailable_slice_2_future_writer_only",
            )
            self.assertEqual(
                recreated.to_dict()["future_issuer"]["issuer_state"],
                "production_authority_unavailable",
            )
            self.assertFalse(recreated.to_dict()["execution_state"]["run_occurred"])

            forged_reference = route._reference_mapping_from_instance(self.reference)
            forged_reference["declarations"] = copy.deepcopy(route._FROZEN_G0_DECLARATIONS)
            forged_reference_sha = hashlib.sha256(
                json.dumps(forged_reference, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            forged_plan = self._plan_with_reference_sha(forged_reference_sha)
            scripted = self._scripted_v1_success()
            with self.assertRaises(route.TrustedRemoteModelRouteError):
                route.wrap_serialized_scripted_local_fixture_outcome(
                    scripted.canonical_bytes(), forged_plan, "path3-commerce-checkout", self.reference
                )
            baseline_outcome = route.wrap_serialized_scripted_local_fixture_outcome(
                scripted.canonical_bytes(), self.plan, "path3-commerce-checkout", self.reference
            )
            self.assertEqual(baseline_outcome.to_dict()["disposition"], "scripted_fixture_assembled")
            self.assertNotEqual(
                baseline_outcome.to_dict()["frozen_g0_binding"]["reference"]["declarations"],
                forged_reference["declarations"],
            )
        finally:
            for default, snapshot in reversed(default_snapshots):
                default.clear()
                default.update(snapshot)
            route._FUTURE_ISSUER.clear()
            route._FUTURE_ISSUER.update(global_issuer)
            route._ENVELOPE_EXECUTION_STATE.clear()
            route._ENVELOPE_EXECUTION_STATE.update(global_execution)
            route._FROZEN_G0_DECLARATIONS.clear()
            route._FROZEN_G0_DECLARATIONS.update(global_declarations)

    def test_definition_time_capture_rebinding_and_no_public_execution_surface(self):
        baseline = self.envelope.canonical_bytes()
        def rebound(*_args, **_kwargs):
            raise RuntimeError("rebound")
        with ExitStack() as stack:
            stack.enter_context(patch.object(route, "_FUTURE_ISSUER", {"issuer": "forged"}))
            stack.enter_context(patch.object(route, "_dumps", rebound))
            stack.enter_context(patch.object(route, "_sha256", lambda _value: "0" * 64))
            stack.enter_context(patch.object(route.TrustedRemoteRealRunEnvelope, "_bytes", side_effect=rebound))
            stack.enter_context(patch.object(route.TrustedRemoteRealRunEnvelope, "canonical_bytes", side_effect=rebound))
            recreated = route.create_untrusted_trusted_remote_real_run_envelope(self.plan, self.record, self.reference)
            self.assertEqual(object.__getattribute__(recreated, "_canonical"), baseline)
            outcome = route.route_untrusted_trusted_remote_real_run_envelope(recreated, self.plan, "path3-commerce-checkout", self.reference)
            self.assertEqual(object.__getattribute__(outcome, "_canonical"), route._OUTCOME_EXTRACT(outcome))
        expected = {
            route.create_untrusted_trusted_remote_real_run_envelope: ("action_time_plan", "raw_record", "frozen_g0_reference"),
            route.wrap_serialized_scripted_local_fixture_outcome: ("value", "action_time_plan", "expected_case_id", "frozen_g0_reference"),
            route.route_untrusted_trusted_remote_real_run_envelope: ("value", "action_time_plan", "expected_case_id", "frozen_g0_reference"),
            route.validate_trusted_remote_model_route_outcome_binding: ("value", "action_time_plan", "expected_case_id", "frozen_g0_reference"),
            route.test_only_replay_trusted_remote_envelope: ("test_only_non_attested_replay_vector",),
        }
        for function, names in expected.items():
            self.assertEqual(tuple(inspect.signature(function).parameters), names)
        self.assertFalse(any(name.startswith("_SEAL") or name.startswith("_SEALED") for name in vars(route)))
        self.assertFalse(hasattr(route, "_install_sealed_public_api"))
        source = (ROOT / "src" / "req2web_orchestration" / "trusted_remote_model_route.py").read_text(encoding="utf-8")
        for token in ("import socket", "import subprocess", "import requests", "import transformers", "invoke_local_qwen_provider", "TierA07"):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()