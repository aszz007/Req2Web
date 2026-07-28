from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_runtime import autodl_repository_archive as archive  # noqa: E402
from req2web_runtime import autodl_trusted_remote_case_loader as loader  # noqa: E402
from req2web_runtime import autodl_trusted_remote_executor as executor  # noqa: E402
from req2web_runtime import autodl_trusted_remote_records as records  # noqa: E402
import test_m3_autodl_trusted_remote_executor as executor_test_module  # noqa: E402
from test_m3_trusted_remote_model_route import _plan_kwargs  # noqa: E402


class TrustedRemoteCaseLoaderTests(unittest.TestCase):
    def setUp(self):
        self.base = executor_test_module.TrustedRemoteExecutorV2Tests(
            "test_pre_run_has_unknown_result_and_internal_clock_only"
        )
        self.base.setUp()

    def tearDown(self):
        self.base.tearDown()

    def _plan_for_materials(self, materials):
        manifest = archive.RepositoryArchiveManifest.from_dict(
            self.base.plan.to_dict()["archive_manifest"]
        )
        kwargs = _plan_kwargs(manifest)
        kwargs["target_instance"]["instance_id"] = "recorded-instance"
        kwargs["ssh"]["temporary_public_key_sha256"] = self.base.signer.key_sha256
        kwargs["model"] = {
            "exact_revision": "f" * 40,
            "files": self.base._rows(self.base.model_files),
        }
        kwargs["runtime"] = {
            "runtime_id": "recorded-runtime",
            "image_id": "recorded-image",
            "artifacts": self.base._rows(self.base.runtime_files),
        }
        manifest_rows = {row["case_id"]: row for row in materials.manifest["cases"]}
        for row in kwargs["cases"]:
            case_id = row["case_id"]
            provider_raw = materials.case_payloads[case_id]["provider_input"]
            reference = materials.case_inputs[case_id]["frozen_g0_reference"]
            row["provider_input"] = {
                "input_id": manifest_rows[case_id]["provider_input_id"],
                "sha256": hashlib.sha256(provider_raw).hexdigest(),
                "byte_length": len(provider_raw),
            }
            row["frozen_g0"] = {
                "package_id": reference.package_id,
                "sha256": hashlib.sha256(executor_test_module.canonical(reference.to_dict())).hexdigest(),
            }
        return records.create_local_r0_record_bundle(**kwargs)[0]

    def test_fixed_loader_rebuilds_and_binds_exact_two_case_materials(self):
        project_temp = self.base.work / "loader-temp"
        project_temp.mkdir()
        (project_temp / ".req2web_project_temp_root.json").write_bytes(b"{}")
        materials = loader.build_fixed_trusted_remote_case_materials_v2(
            ROOT, project_temp
        )
        self.assertEqual(
            tuple(materials.case_inputs), records.REQUIRED_CASE_IDS
        )
        self.assertEqual(
            materials.manifest["boundary"],
            {
                "non_h1": True,
                "reference_only": False,
                "model_repository_enumeration": False,
            },
        )
        plan = self._plan_for_materials(materials)
        package = executor.prepare_trusted_remote_execution_package_v2(
            plan,
            materials.case_payloads,
            self.base.instance,
            self.base.runtime_execution,
        )
        self.assertIs(
            loader.bind_fixed_trusted_remote_case_inputs_v2(materials, package),
            materials,
        )
        materials.case_payloads["path3-media-analysis"]["provider_input"] = b"{}"
        with self.assertRaisesRegex(
            loader.TrustedRemoteCaseLoaderError,
            "provider_input_binding",
        ):
            loader.bind_fixed_trusted_remote_case_inputs_v2(materials, package)


if __name__ == "__main__":
    unittest.main()