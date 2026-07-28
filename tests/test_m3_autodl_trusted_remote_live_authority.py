from __future__ import annotations

import subprocess
import shutil
import sys
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_runtime import autodl_repository_archive as archive  # noqa: E402
from req2web_runtime import autodl_trusted_remote_live_authority as authority  # noqa: E402
from req2web_runtime import autodl_trusted_remote_qwen_raw as raw_module  # noqa: E402
from req2web_runtime import autodl_trusted_remote_records as records  # noqa: E402
from req2web_runtime import autodl_trusted_remote_slice3 as slice3  # noqa: E402
from req2web_runtime import autodl_trusted_remote_two_case_runner as runner  # noqa: E402
from test_m3_model_route import build_chain, build_g0  # noqa: E402
from test_m3_semantic_candidate_assembly import build_context, candidate_payload, raw_bytes  # noqa: E402
from test_m3_trusted_remote_model_route import _plan_kwargs, _reference_sha  # noqa: E402

TEST_ROOT = ROOT / "outputs" / "_m3_trusted_remote_live_authority_tests"


class TrustedRemoteLiveAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.work = TEST_ROOT / uuid4().hex
        self.work.mkdir(parents=True)
        self.private_key = self.work / "authority_key"
        result = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.private_key)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
        if not self.private_key.is_file():
            self.skipTest("ssh-keygen_ed25519_unavailable")
        public = subprocess.run(["ssh-keygen", "-y", "-f", str(self.private_key)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
        if public.returncode != 0 or not public.stdout.strip():
            self.skipTest("ssh-keygen_public_identity_unavailable")
        self.signer = authority.ExpectedLiveAuthoritySigner(authority.LIVE_AUTHORITY_PRINCIPAL, b" ".join(public.stdout.split()[:2]).decode("ascii"))
        self.nonce = uuid4().hex * 2
        context = build_context()
        _, self.context, self.guidance, self.reference = build_g0(self.work / "g0", context=context)
        _, self.selected, _, _, _ = build_chain(self.context)
        self.plan = self._plan()
        self.profile = raw_module.create_trusted_remote_qwen_runtime_profile(self.plan)
        self.loader = raw_module.create_trusted_remote_qwen_loader_contract(self.profile)
        self.candidates = {case_id: self._candidate(case_id) for case_id in records.REQUIRED_CASE_IDS}
        self.instance = {
            "provider": "AutoDL", "instance_id": "recorded-instance", "gpu_model": "NVIDIA RTX 5090", "gpu_index": 0,
            "gpu_uuid": "GPU-test-uuid", "ssh_host_key_sha256": "8" * 64, "ssh_public_key_sha256": self.signer.key_sha256,
        }

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _plan(self):
        body = archive._manifest_body("f" * 40, "e" * 40, [], [], b"", authority=archive._AUTHORITY)
        manifest = archive.RepositoryArchiveManifest.from_dict(body)
        kwargs = _plan_kwargs(manifest)
        kwargs["ssh"]["temporary_public_key_sha256"] = self.signer.key_sha256
        selection = self.selected.selection_record
        kwargs["cases"][0]["provider_input"] = {"input_id": selection.input_view_id, "sha256": selection.provider_visible_input_sha256, "byte_length": selection.provider_visible_input_byte_length}
        kwargs["cases"][0]["frozen_g0"] = {"package_id": self.reference.package_id, "sha256": _reference_sha(self.reference)}
        return records.create_local_r0_record_bundle(**kwargs)[0]

    def _observation(self):
        return {"actual_profile": "quality_experiment", "actual_device": "autodl_rtx_5090_or_stronger", "actual_dtype": "bf16", "actual_quantization": "none", "elapsed_seconds": 10, "cost_milli": 10, "storage_bytes": 10, "timeout_state": "completed", "cancel_state": "not_requested"}

    def _candidate(self, case_id):
        return slice3.write_trusted_remote_production_run_record(self.plan, self.profile, self.loader, case_id, raw_bytes(candidate_payload()), self._observation())

    def _receipt(self, *, nonce=None, expires="2026-07-29T00:00:00Z"):
        nonce = nonce or self.nonce
        payload = authority.build_trusted_remote_live_authority_payload(self.plan, self.candidates, self.instance, nonce, "2026-07-28T00:00:00Z", expires)
        payload_path = self.work / ("payload-" + nonce[:8] + ".json")
        payload_path.write_bytes(__import__("json").dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        sign = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(self.private_key), "-n", authority.LIVE_AUTHORITY_NAMESPACE, str(payload_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
        self.assertEqual(sign.returncode, 0, sign.stderr.decode("utf-8", errors="replace"))
        return authority.create_trusted_remote_live_authority_receipt(payload, payload_path.with_suffix(".json.sig").read_bytes(), self.signer)

    def test_signed_receipt_replays_and_verifies_expected_signer(self):
        receipt = self._receipt()
        verified = authority.verify_trusted_remote_live_authority_receipt(receipt.canonical_bytes(), self.plan, self.candidates, self.instance, self.signer, "2026-07-28T12:00:00Z")
        self.assertEqual(verified.to_dict()["signer_key_sha256"], self.signer.key_sha256)
        self.assertNotIn(str(self.private_key), receipt.canonical_bytes().decode("utf-8"))

    def test_wrong_signer_signature_namespace_nonce_window_and_binding_fail_closed(self):
        receipt = self._receipt()
        other = self.work / "other"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
        public = subprocess.run(["ssh-keygen", "-y", "-f", str(other)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
        self.assertEqual(public.returncode, 0)
        wrong = authority.ExpectedLiveAuthoritySigner(authority.LIVE_AUTHORITY_PRINCIPAL, b" ".join(public.stdout.split()[:2]).decode("ascii"))
        with self.assertRaises(authority.TrustedRemoteLiveAuthorityError): authority.verify_trusted_remote_live_authority_receipt(receipt, self.plan, self.candidates, self.instance, wrong, "2026-07-28T12:00:00Z")
        altered = receipt.to_dict(); altered["namespace"] = "wrong"
        with self.assertRaises(authority.TrustedRemoteLiveAuthorityError): authority.TrustedRemoteLiveAuthorityReceipt.from_dict(altered)
        with self.assertRaises(authority.TrustedRemoteLiveAuthorityError): authority.verify_trusted_remote_live_authority_receipt(receipt, self.plan, self.candidates, {**self.instance, "gpu_uuid": "GPU-other"}, self.signer, "2026-07-28T12:00:00Z")
        with self.assertRaises(authority.TrustedRemoteLiveAuthorityError): authority.verify_trusted_remote_live_authority_receipt(receipt, self.plan, self.candidates, self.instance, self.signer, "2026-07-29T00:00:00Z")
        with self.assertRaises(authority.TrustedRemoteLiveAuthorityError): self._receipt(expires="2026-07-28T00:00:00Z")
        with self.assertRaises(authority.TrustedRemoteLiveAuthorityError): self._receipt(expires="2026-08-05T00:00:00Z")

    def test_runner_package_dry_run_is_bounded_and_live_preconditions_are_not_execution(self):
        package = runner.prepare_trusted_remote_two_case_runner_package(self.plan, self.candidates)
        result = runner.run_trusted_remote_two_case_dry_run(package, self.plan, self.candidates, dry_run=True)
        self.assertEqual(result["state"], "prepared_dry_run_not_executed")
        self.assertFalse(result["model_loaded"]); self.assertFalse(result["run_occurred"]); self.assertEqual(result["provider_call_count"], 0)
        with self.assertRaises(runner.TrustedRemoteTwoCaseRunnerError): runner.run_trusted_remote_two_case_dry_run(package, self.plan, self.candidates, dry_run=False)
        receipt = self._receipt()
        pre = runner.validate_trusted_remote_two_case_live_preconditions(package, self.plan, self.candidates, receipt, self.signer, self.instance, "2026-07-28T12:00:00Z", dry_run=False)
        self.assertEqual(pre["state"], "preconditions_verified_execution_not_started")
        with self.assertRaisesRegex(runner.TrustedRemoteTwoCaseRunnerError, "live_authority_precondition_invalid"):
            runner.validate_trusted_remote_two_case_live_preconditions(package, self.plan, self.candidates, receipt, self.signer, self.instance, "2026-07-28T12:00:00Z", dry_run=False)
        import inspect
        self.assertEqual(
            tuple(inspect.signature(runner.validate_trusted_remote_two_case_live_preconditions).parameters),
            ("value", "action_time_plan", "candidate_records", "live_authority_receipt", "expected_signer", "expected_instance_facts", "now_utc", "dry_run"),
        )
        self.assertFalse(hasattr(authority, "TrustedRemoteLiveAuthorityNonceLedger"))
        with self.assertRaises(runner.TrustedRemoteTwoCaseRunnerError): runner.validate_trusted_remote_two_case_live_preconditions(package, self.plan, self.candidates, receipt, self.signer, self.instance, "2026-07-28T12:00:00Z", dry_run=True)

    def test_arbitrary_bytes_and_candidate_records_do_not_become_live_authority(self):
        with self.assertRaises(authority.TrustedRemoteLiveAuthorityError): authority.create_trusted_remote_live_authority_receipt({"x": 1}, b"not-an-openssh-signature", self.signer)
        package = runner.prepare_trusted_remote_two_case_runner_package(self.plan, self.candidates)
        with self.assertRaises(runner.TrustedRemoteTwoCaseRunnerError): runner.validate_trusted_remote_two_case_live_preconditions(package, self.plan, self.candidates, b"{}", self.signer, self.instance, "2026-07-28T12:00:00Z", dry_run=False)


if __name__ == "__main__":
    unittest.main()
