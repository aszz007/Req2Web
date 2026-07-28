from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_runtime import autodl_repository_archive as archive  # noqa: E402
from req2web_runtime import autodl_trusted_remote_qwen_raw as raw_module  # noqa: E402
from req2web_runtime import autodl_trusted_remote_records as records  # noqa: E402
from req2web_runtime import autodl_trusted_remote_slice3 as slice3  # noqa: E402
from req2web_runtime import autodl_trusted_remote_two_case_runner as runner  # noqa: E402
from test_m3_model_route import build_chain, build_g0  # noqa: E402
from test_m3_semantic_candidate_assembly import build_context, candidate_payload, raw_bytes  # noqa: E402
from test_m3_trusted_remote_model_route import _plan_kwargs, _reference_sha  # noqa: E402

TEST_ROOT = ROOT / "outputs" / "_m3_trusted_remote_two_case_runner_tests"


class TrustedRemoteTwoCaseRunnerTests(unittest.TestCase):
    def setUp(self):
        self.work = TEST_ROOT / uuid4().hex
        self.work.mkdir(parents=True)
        context = build_context()
        _, self.context, _, self.reference = build_g0(self.work / "g0", context=context)
        _, self.selected, _, _, _ = build_chain(self.context)
        body = archive._manifest_body("f" * 40, "e" * 40, [], [], b"", authority=archive._AUTHORITY)
        kwargs = _plan_kwargs(archive.RepositoryArchiveManifest.from_dict(body))
        selection = self.selected.selection_record
        kwargs["cases"][0]["provider_input"] = {"input_id": selection.input_view_id, "sha256": selection.provider_visible_input_sha256, "byte_length": selection.provider_visible_input_byte_length}
        kwargs["cases"][0]["frozen_g0"] = {"package_id": self.reference.package_id, "sha256": _reference_sha(self.reference)}
        self.plan = records.create_local_r0_record_bundle(**kwargs)[0]
        profile = raw_module.create_trusted_remote_qwen_runtime_profile(self.plan)
        loader = raw_module.create_trusted_remote_qwen_loader_contract(profile)
        observation = {"actual_profile": "quality_experiment", "actual_device": "autodl_rtx_5090_or_stronger", "actual_dtype": "bf16", "actual_quantization": "none", "elapsed_seconds": 10, "cost_milli": 10, "storage_bytes": 10, "timeout_state": "completed", "cancel_state": "not_requested"}
        self.candidates = {case: slice3.write_trusted_remote_production_run_record(self.plan, profile, loader, case, raw_bytes(candidate_payload()), observation) for case in records.REQUIRED_CASE_IDS}

    def tearDown(self): shutil.rmtree(self.work, ignore_errors=True)

    def test_exact_two_case_dry_run_package_and_no_model_execution(self):
        package = runner.prepare_trusted_remote_two_case_runner_package(self.plan, self.candidates)
        replay = runner.validate_trusted_remote_two_case_runner_package(package.canonical_bytes(), self.plan, self.candidates)
        self.assertEqual(replay.to_dict()["quality_profile"], {"profile": "quality_experiment", "dtype": "bf16", "quantization": "none", "provider_call_limit_per_case": 1, "retry_allowed": False})
        result = runner.run_trusted_remote_two_case_dry_run(package, self.plan, self.candidates, dry_run=True)
        self.assertEqual(result["state"], "prepared_dry_run_not_executed")
        self.assertFalse(result["model_loaded"]); self.assertFalse(result["run_occurred"]); self.assertEqual(result["provider_call_count"], 0)

    def test_cross_case_candidate_and_live_request_fail_closed(self):
        package = runner.prepare_trusted_remote_two_case_runner_package(self.plan, self.candidates)
        swapped = dict(self.candidates); swapped["path3-commerce-checkout"] = self.candidates["path3-media-analysis"]
        with self.assertRaises(runner.TrustedRemoteTwoCaseRunnerError): runner.prepare_trusted_remote_two_case_runner_package(self.plan, swapped)
        with self.assertRaises(runner.TrustedRemoteTwoCaseRunnerError): runner.run_trusted_remote_two_case_dry_run(package, self.plan, self.candidates, dry_run=False)
        with self.assertRaises(runner.TrustedRemoteTwoCaseRunnerError): runner.validate_trusted_remote_two_case_live_preconditions(package, self.plan, self.candidates, b"{}", object(), {}, "2026-07-28T12:00:00Z", dry_run=False)


if __name__ == "__main__": unittest.main()
