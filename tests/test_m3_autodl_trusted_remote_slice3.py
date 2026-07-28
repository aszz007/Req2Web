from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_orchestration import trusted_remote_model_route as route  # noqa: E402
from req2web_runtime import autodl_repository_archive as archive  # noqa: E402
from req2web_runtime import autodl_trusted_remote_qwen_raw as raw_module  # noqa: E402
from req2web_runtime import autodl_trusted_remote_records as records  # noqa: E402
from req2web_runtime import autodl_trusted_remote_slice3 as slice3  # noqa: E402
from test_m3_model_route import build_chain, build_g0  # noqa: E402
from test_m3_semantic_candidate_assembly import build_context, candidate_payload, raw_bytes  # noqa: E402
from test_m3_trusted_remote_model_route import _plan_kwargs, _reference_sha  # noqa: E402


TEST_ROOT = ROOT / "outputs" / "_m3_trusted_remote_slice3_tests"


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class TrustedRemoteSlice3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = TEST_ROOT / uuid4().hex
        self.work.mkdir(parents=True)
        self.context = build_context()
        self.package, self.context, self.guidance, self.reference = build_g0(self.work / "g0", context=self.context)
        self.manifest, self.selected, self.local_request, self.audit, self.preparation = build_chain(self.context)
        self.plan = self._plan()
        self.profile = raw_module.create_trusted_remote_qwen_runtime_profile(self.plan)
        self.loader = raw_module.create_trusted_remote_qwen_loader_contract(self.profile)
        self.raw_record = raw_module.capture_local_contract_candidate_raw_response(
            self.plan, self.profile, self.loader, "path3-commerce-checkout", raw_bytes(candidate_payload())
        )
        self.records = {
            "path3-commerce-checkout": self._record("path3-commerce-checkout", raw_bytes(candidate_payload())),
            "path3-media-analysis": self._record("path3-media-analysis", raw_bytes(candidate_payload())),
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def _plan(self):
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
            "sha256": _reference_sha(self.reference),
        }
        return records.create_local_r0_record_bundle(**kwargs)[0]

    def _observation(self):
        selected = self.plan.to_dict()["profile_selection"]
        precision = self.plan.to_dict()["precision"]
        return {
            "actual_profile": selected["selected_profile"],
            "actual_device": selected["selected_device"],
            "actual_dtype": precision["selected_dtype"],
            "actual_quantization": precision["selected_quantization"],
            "elapsed_seconds": 10,
            "cost_milli": 10,
            "storage_bytes": 10,
            "timeout_state": "completed",
            "cancel_state": "not_requested",
        }

    def _record(self, case_id, raw):
        return slice3.write_trusted_remote_production_run_record(
            self.plan, self.profile, self.loader, case_id, raw, self._observation()
        )

    def _bundle(self):
        delivery = {
            "path3-commerce-checkout": {
                "route_outcome": _canonical({"case_binding": {"case_id": "path3-commerce-checkout"}, "source_branch": "not_executed"}),
                "gate_delivery_outcome": _canonical({"case_id": "path3-commerce-checkout", "status": "failed_delivery"}),
                "result_package_manifest": None,
                "failure": {"code": "no_real_gate_execution", "stage": "slice3_contract", "retryable": False},
            },
            "path3-media-analysis": {
                "route_outcome": _canonical({"case_binding": {"case_id": "path3-media-analysis"}, "source_branch": "not_executed"}),
                "gate_delivery_outcome": _canonical({"case_id": "path3-media-analysis", "status": "failed_delivery"}),
                "result_package_manifest": None,
                "failure": {"code": "no_real_gate_execution", "stage": "slice3_contract", "retryable": False},
            },
        }
        artifacts = {
            path: (self.records["path3-commerce-checkout"].to_dict()["raw_response"]["base64"].encode("ascii") if path.endswith("raw_response.bin") else _canonical({"path": path, "state": "contract_candidate"}))
            for path in slice3.RESULT_RETURN_PATHS
        }
        closeout = {
            step: {"state": "completed", "evidence_id": f"{step}-receipt", "evidence_sha256": hashlib.sha256(step.encode("ascii")).hexdigest()}
            for step in slice3.CLOSEOUT_STEP_ORDER
        }
        return slice3.write_trusted_remote_production_run_bundle(self.plan, self.records, delivery, artifacts, closeout)

    def test_candidate_writer_record_and_source_binding_round_trip(self):
        record = self.records["path3-commerce-checkout"]
        replay = slice3.validate_trusted_remote_production_run_record_binding(
            record.canonical_bytes(), self.plan, "path3-commerce-checkout"
        )
        self.assertEqual(replay.canonical_bytes(), record.canonical_bytes())
        source = slice3.make_trusted_remote_production_source_binding(record, "path3-commerce-checkout")
        self.assertEqual(source["kind"], slice3.PRODUCTION_SOURCE_KIND)
        validated = slice3.validate_trusted_remote_production_source_binding(
            source, "path3-commerce-checkout", self.plan
        )
        self.assertEqual(validated, source)
        self.assertFalse(hasattr(route, "route_trusted_remote_production_run_record"))

    def test_public_candidate_cannot_authorize_real_provider_success(self):
        source = slice3.make_trusted_remote_production_source_binding(
            self.records["path3-commerce-checkout"], "path3-commerce-checkout"
        )
        envelope = route.create_untrusted_trusted_remote_real_run_envelope(
            self.plan, self.raw_record, self.reference
        )
        failure = route.route_untrusted_trusted_remote_real_run_envelope(
            envelope, self.plan, "path3-commerce-checkout", self.reference
        ).to_dict()
        failure["source_branch"] = "trusted_remote_local_qwen"
        failure["disposition"] = "real_provider_assembled"
        failure["attestation_state"] = "real_provider_attested"
        failure["source_binding"] = {
            "kind": "verified_real_provider",
            "run_record_base64": source["run_record_base64"],
            "run_record_sha256": source["run_record_sha256"],
            "run_record_id": source["run_record_id"],
        }
        failure["failure"] = None
        forged = route._identified(failure, "outcome_id", route._OUTCOME_PREFIX)
        with self.assertRaisesRegex(
            route.TrustedRemoteModelRouteError, "real_provider_authority_unavailable"
        ):
            route.validate_trusted_remote_model_route_outcome_binding(
                _canonical(forged), self.plan, "path3-commerce-checkout", self.reference
            )
        direct = object.__new__(slice3.TrustedRemoteProductionRunRecord)
        object.__setattr__(direct, "_data", forged)
        object.__setattr__(direct, "_canonical", _canonical(forged))
        with self.assertRaises(slice3.TrustedRemoteSlice3Error):
            direct.validate()

    def test_cross_case_cap_second_call_and_raw_tamper_fail_closed(self):
        with self.assertRaisesRegex(slice3.TrustedRemoteSlice3Error, "case_cross_binding"):
            slice3.validate_trusted_remote_production_run_record_binding(
                self.records["path3-media-analysis"], self.plan, "path3-commerce-checkout"
            )
        observation = self._observation()
        observation["elapsed_seconds"] = self.plan.to_dict()["operational"]["caps"]["time_cap_seconds"] + 1
        with self.assertRaisesRegex(slice3.TrustedRemoteSlice3Error, "runtime_cap_drift"):
            slice3.write_trusted_remote_production_run_record(
                self.plan, self.profile, self.loader, "path3-commerce-checkout", b"raw", observation
            )
        forged = self.records["path3-commerce-checkout"].to_dict()
        forged["execution"]["retry_count"] = 1
        forged["run_record_id"] = slice3.RECORD_PREFIX + "0" * 64
        with self.assertRaises(slice3.TrustedRemoteSlice3Error):
            slice3.TrustedRemoteProductionRunRecord.from_dict(forged)
        source = slice3.make_trusted_remote_production_source_binding(self.records["path3-commerce-checkout"], "path3-commerce-checkout")
        source["run_record_base64"] = source["run_record_base64"][:-4] + "AAAA"
        with self.assertRaises(slice3.TrustedRemoteSlice3Error):
            slice3.validate_trusted_remote_production_source_binding(source, "path3-commerce-checkout", self.plan)

    def test_two_case_bundle_inventory_return_and_closeout_round_trip(self):
        bundle = self._bundle()
        replay = slice3.validate_trusted_remote_production_run_bundle_binding(bundle.canonical_bytes(), self.plan)
        self.assertEqual(replay.canonical_bytes(), bundle.canonical_bytes())
        data = bundle.to_dict()
        self.assertEqual([row["case_id"] for row in data["case_run_records"]], list(slice3.REQUIRED_CASE_IDS))
        self.assertEqual([row["step"] for row in data["closeout"]], list(slice3.CLOSEOUT_STEP_ORDER))
        self.assertEqual(data["result_inventory"]["file_count"], len(slice3.RESULT_RETURN_PATHS))
        tampered = data
        tampered["return_receipt"]["inventory_sha256"] = "0" * 64
        tampered["bundle_id"] = slice3.BUNDLE_PREFIX + "0" * 64
        with self.assertRaises(slice3.TrustedRemoteSlice3Error):
            slice3.TrustedRemoteProductionRunBundle.from_dict(tampered)

    def test_public_signatures_exclude_authority_backend_or_validator_injection(self):
        import inspect
        self.assertEqual(tuple(inspect.signature(slice3.write_trusted_remote_production_run_record).parameters), ("action_time_plan", "runtime_profile", "loader_contract", "case_id", "raw_bytes", "runtime_observation"))
        self.assertEqual(tuple(inspect.signature(slice3.write_trusted_remote_production_run_bundle).parameters), ("action_time_plan", "case_run_records", "delivery_records", "result_artifacts", "closeout"))
        self.assertFalse(hasattr(route, "route_trusted_remote_production_run_record"))


if __name__ == "__main__":
    unittest.main()
