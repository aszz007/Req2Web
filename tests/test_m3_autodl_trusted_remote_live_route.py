from __future__ import annotations

import base64
import json
import shutil
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_faults.fallback_delivery import freeze_g0_fallback_package  # noqa: E402
from req2web_orchestration.model_route import SCRIPTED_ACCEPTANCE_PASS_KEY, ScriptedAcceptanceFixture  # noqa: E402
from req2web_runtime import autodl_trusted_remote_executor as executor  # noqa: E402
from req2web_runtime import autodl_trusted_remote_live_route as live_route  # noqa: E402
from req2web_runtime import autodl_trusted_remote_records as records  # noqa: E402
import test_m3_autodl_trusted_remote_executor as executor_test_module  # noqa: E402
from test_m3_autodl_trusted_remote_executor import canonical  # noqa: E402
from test_m3_gate_delivery import gate_delivery_candidate_bytes  # noqa: E402
from test_m3_semantic_candidate_assembly import raw_bytes  # noqa: E402

TEST_ROOT = ROOT / "outputs" / "_m3_trusted_remote_live_route_tests"


class TrustedRemoteLiveRouteTests(unittest.TestCase):
    def setUp(self):
        self.base = executor_test_module.TrustedRemoteExecutorV2Tests("test_pre_run_has_unknown_result_and_internal_clock_only")
        self.base.setUp()
        self.work = TEST_ROOT / uuid4().hex
        self.work.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)
        self.base.tearDown()

    def _verified_handle(self, overrides=None):
        repair_raw = gate_delivery_candidate_bytes(self.base.context)
        pass_payload = json.loads(repair_raw.decode("utf-8"))
        for component in pass_payload["components"]:
            component["label"] = "component-label-" + component["stable_id"]
        pass_raw = raw_bytes(pass_payload)
        raw_map = {"path3-commerce-checkout": pass_raw, "path3-media-analysis": repair_raw}
        if overrides:
            raw_map.update(overrides)
        result = self.base._manual_result(raw_map)
        result_root = self.work / "result"; result_root.mkdir()
        for row in result.to_dict()["cases"]:
            target = result_root / Path(row["raw_saved_relative_path"]); target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(row["raw_response_base64"]))
        (result_root / "execution_result.json").write_bytes(result.canonical_bytes())
        return executor._VerifiedExecutionHandle(result, self.base.package, self.base.plan, self.base.pre_receipt, result_root, executor._VERIFIED_EXECUTION_TOKEN)

    def _case_inputs(self):
        values = {}
        for case_id in records.REQUIRED_CASE_IDS:
            snapshot = self.work / f"snapshot-{case_id}-{uuid4().hex}"
            fallback = freeze_g0_fallback_package(case_id, self.base.g0_package, snapshot)
            values[case_id] = {
                "frozen_g0_reference": self.base.reference,
                "package": self.base.g0_package,
                "context": self.base.context,
                "guidance": self.base.guidance,
                "manifest": self.base.manifest,
                "selected": self.base.selected,
                "local_request": self.base.local_request,
                "pre_invocation_audit": self.base.pre_invocation_audit,
                "local_qwen_preparation": self.base.local_qwen_preparation,
                "fallback_record": fallback,
                "fallback_snapshot_dir": snapshot,
                "render_output_dir": self.work / f"render-{case_id}",
                "model_package_output_dir": self.work / f"model-package-{case_id}",
                "fallback_output_dir": self.work / f"fallback-{case_id}",
                "scripted_acceptance_fixture": ScriptedAcceptanceFixture.create(SCRIPTED_ACCEPTANCE_PASS_KEY),
            }
        return values

    def test_verified_execution_routes_a07a_and_a07b_then_returns_and_closes_out(self):
        handle = self._verified_handle()
        with patch.object(executor, "_emit_progress") as progress:
            bundle = live_route.route_verified_trusted_remote_two_case_v2(handle, self._case_inputs())
        data = bundle.to_dict()
        self.assertEqual(data["state"], "live_route_completed_for_return")
        by_case = {row["case_id"]: row for row in data["cases"]}
        self.assertEqual(by_case["path3-commerce-checkout"]["a07_semantics"], "A-07a")
        self.assertEqual(by_case["path3-media-analysis"]["a07_semantics"], "A-07b")
        self.assertEqual(by_case["path3-commerce-checkout"]["status"], "first_pass_success")
        self.assertEqual(by_case["path3-media-analysis"]["status"], "recovered_success")
        progress_events = [call.args[0] for call in progress.call_args_list]
        self.assertEqual(
            progress_events,
            [
                "route_case_started",
                "route_case_complete",
                "route_case_started",
                "route_case_complete",
            ],
        )
        for artifacts in bundle.to_return_artifacts().values():
            self.assertNotIn(b"scripted_fixture_assembled", artifacts["model_route_outcome"])
            self.assertNotIn(b"scripted_fixture_assembled", artifacts["gate_delivery_outcome"])
        project_temp = executor.create_trusted_remote_project_temp_root_v2(self.work / "project-temp", self.base.package)
        (project_temp / "route-work.bin").write_bytes(b"temporary")
        return_root = self.work / "return"
        manifest = executor.write_trusted_remote_return_bundle_v2(handle, bundle.to_return_artifacts(), return_root, project_temp)
        self.assertEqual(manifest["state"], "pending_instance_release_and_access_revocation")
        self.assertEqual({p.relative_to(return_root).as_posix() for p in return_root.rglob("*") if p.is_file()}, set(records.RESULT_RETURN_PATHS))
        final = executor.finalize_trusted_remote_closeout_v2(handle, return_root, b"release", b"revoke")
        self.assertEqual(final["state"], "completed")

    def test_semantic_failure_uses_only_same_case_frozen_g0_fallback(self):
        handle = self._verified_handle({"path3-commerce-checkout": b"not-json"})
        bundle = live_route.route_verified_trusted_remote_two_case_v2(handle, self._case_inputs())
        by_case = {row["case_id"]: row for row in bundle.to_dict()["cases"]}
        self.assertEqual(by_case["path3-commerce-checkout"]["status"], "fallback_delivery")
        self.assertEqual(by_case["path3-commerce-checkout"]["delivery_source"], "g0_frozen_fallback")
        artifacts = bundle.to_return_artifacts()["path3-commerce-checkout"]
        manifest = json.loads(artifacts["result_package_manifest"].decode("utf-8"))
        self.assertEqual(manifest["schema_version"], "req2web.result.package.v2")

    def test_arbitrary_result_bytes_manual_root_and_cross_g0_cannot_enter_live_route(self):
        result = self.base._manual_result()
        with self.assertRaises(live_route.TrustedRemoteLiveRouteError):
            live_route.route_verified_trusted_remote_two_case_v2(result.canonical_bytes(), self._case_inputs())
        handle = self._verified_handle()
        inputs = self._case_inputs()
        forged = dict(inputs["path3-media-analysis"])
        forged["frozen_g0_reference"] = None
        inputs["path3-media-analysis"] = forged
        with self.assertRaises(live_route.TrustedRemoteLiveRouteError):
            live_route.route_verified_trusted_remote_two_case_v2(handle, inputs)


    def test_route_rejects_provider_input_and_local_request_cross_drift(self):
        handle = self._verified_handle()
        inputs = self._case_inputs()
        other_context = deepcopy(self.base.context)
        other_context.original_requirement = other_context.original_requirement + " changed"
        other_manifest, other_selected, other_request, other_audit, other_preparation = executor_test_module.build_chain(other_context)
        forged = dict(inputs["path3-media-analysis"])
        forged.update({
            "context": other_context,
            "manifest": other_manifest,
            "selected": other_selected,
            "local_request": other_request,
            "pre_invocation_audit": other_audit,
            "local_qwen_preparation": other_preparation,
        })
        inputs["path3-media-analysis"] = forged
        with self.assertRaisesRegex(live_route.TrustedRemoteLiveRouteError, "provider_input_cross_binding"):
            live_route.route_verified_trusted_remote_two_case_v2(handle, inputs)



if __name__ == "__main__":
    unittest.main()
