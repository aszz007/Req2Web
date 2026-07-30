"""Focused no-model and fake-backend tests for the Qwen3.6 diagnostic."""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_runtime import qwen27b_predeploy as runtime_authority  # noqa: E402
from req2web_runtime import qwen36_diagnostic as qwen36  # noqa: E402
from req2web_runtime.qwen27b_case_bundle import (  # noqa: E402
    build_qwen27b_case_bundle,
)
from req2web_runtime.qwen27b_recovery_runner import (  # noqa: E402
    BackendGeneration,
)
from test_m3_qwen27b_case_bundle import Qwen27BCaseBundleTests  # noqa: E402


class _Backend:
    def __init__(self, *, dtype: str = "bf16") -> None:
        self.dtype = dtype
        self.calls: list[str] = []

    def runtime_facts(self, expected_versions):
        return {
            **dict(expected_versions),
            "gpu_index": 0,
            "gpu_uuid": "GPU-12345678-1234-1234-1234-123456789abc",
            "gpu_name": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
            "total_vram_bytes": 96 * 1024**3,
            "free_vram_bytes_before_load": 90 * 1024**3,
            "torch_gpu_name": (
                "NVIDIA RTX PRO 6000 Blackwell Server Edition"
            ),
            "torch_gpu_uuid": (
                "GPU-12345678-1234-1234-1234-123456789abc"
            ),
            "torch_total_vram_bytes": 96 * 1024**3,
            "device": "cuda:0",
            "dtype": self.dtype,
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
        }

    def load(self, model_root: Path) -> None:
        if not model_root.is_dir():
            raise AssertionError("model root missing")

    def loaded_facts(self):
        return {
            "device": "cuda:0",
            "dtype": self.dtype,
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
            "allocated_vram_bytes": 1,
            "reserved_vram_bytes": 1,
        }

    def generate(self, model_text, case_id, *, stream_output, stream):
        self.calls.append(case_id)
        text = json.dumps(
            {"diagnostic_case": case_id},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return BackendGeneration(
            text=text,
            input_tokens=100,
            generated_tokens=20,
            elapsed_ms=1000,
            stream_event_count=0 if stream_output == "off" else 3,
            stream_text="" if stream_output == "off" else text,
        )


class Qwen36DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Qwen27BCaseBundleTests.setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        Qwen27BCaseBundleTests.tearDownClass()

    def setUp(self) -> None:
        self.work = ROOT / ("tmp-qwen36-diagnostic-" + uuid4().hex)
        self.work.mkdir()
        self.bundle = build_qwen27b_case_bundle(
            Qwen27BCaseBundleTests.first_archive_path,
            Qwen27BCaseBundleTests.first_manifest_path,
            self.work / "bundle",
        )
        self.runtime_root = self.work / "runtime"
        for relative in (
            "bin/python",
            *runtime_authority._RUNTIME_STATIC_FILES,
        ):
            path = self.runtime_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("runtime:" + relative).encode("utf-8"))
        self.runtime_data = (
            runtime_authority.collect_runtime_root_inventory(
                self.runtime_root
            )
        )
        self.runtime_raw = runtime_authority._dump(self.runtime_data)
        self.plan = qwen36.create_qwen36_diagnostic_plan(
            Qwen27BCaseBundleTests.first_archive_path,
            Qwen27BCaseBundleTests.first_manifest_path,
            self.bundle.root,
            self.runtime_raw,
        )
        self.inventory = self._inventory()
        self.action = qwen36.create_qwen36_diagnostic_action_binding(
            self.plan,
            self.inventory,
            self.runtime_raw,
            expected_gpu_name=(
                "NVIDIA RTX PRO 6000 Blackwell Server Edition"
            ),
            expected_gpu_uuid=(
                "GPU-12345678-1234-1234-1234-123456789abc"
            ),
        )
        self.model_root = self.work / "model"
        self.model_root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    @staticmethod
    def _inventory() -> qwen36.Qwen36ModelInventory:
        authority = qwen36._official_model_inventory_authority()
        rows = copy.deepcopy(authority["files"])
        body = {
            "schema_version": qwen36.MODEL_INVENTORY_SCHEMA,
            "inventory_id": "0" * 64,
            "repository": qwen36.MODEL_REPOSITORY,
            "revision": qwen36.MODEL_REVISION,
            "official_inventory_authority_id": authority["authority_id"],
            "official_inventory_manifest_sha256": authority[
                "manifest_sha256"
            ],
            "files": rows,
            "file_count": len(rows),
            "total_bytes": sum(row["bytes"] for row in rows),
            "weight_shard_count": 15,
            "weight_shard_total_bytes": (
                qwen36.MODEL_WEIGHT_SHARD_TOTAL_BYTES
            ),
            "weight_tensor_total_bytes": (
                qwen36.MODEL_WEIGHT_TENSOR_TOTAL_BYTES
            ),
            "tree_sha256": qwen36._sha(qwen36._canonical(rows)),
        }
        return qwen36.Qwen36ModelInventory(
            qwen36._identified(body, "inventory_id")
        )

    def _execute(
        self,
        backend: _Backend | None = None,
        *,
        stream_output: str = "off",
    ) -> tuple[Path, dict]:
        output = self.work / ("run-" + uuid4().hex)
        with (
            patch.object(
                qwen36,
                "validate_qwen36_model_root_against_inventory",
                return_value=self.inventory,
            ),
            patch.object(
                qwen36,
                "validate_qwen36_runtime_root_against_inventory",
                return_value=self.runtime_data,
            ),
            patch.object(
                qwen36,
                "TransformersRecoveryBackend",
                return_value=backend or _Backend(),
            ),
            patch.object(qwen36, "_progress"),
        ):
            result = qwen36.execute_qwen36_diagnostic(
                self.plan,
                self.action,
                self.inventory,
                self.runtime_raw,
                self.model_root,
                self.runtime_root,
                Qwen27BCaseBundleTests.first_archive_path,
                Qwen27BCaseBundleTests.first_manifest_path,
                self.bundle.root,
                output,
                stream_output=stream_output,
            )
        return output, result

    def _rewrite_manifest(self, output: Path, mutate) -> None:
        path = output / qwen36.RUN_MANIFEST_FILENAME
        value = json.loads(path.read_bytes())
        mutate(value)
        value = qwen36._identified(value, "run_id")
        path.write_bytes(qwen36._canonical(value))

    def test_official_inventory_and_plan_are_exact_and_canonical(self) -> None:
        authority = qwen36._official_model_inventory_authority()
        self.assertEqual(authority["file_count"], 29)
        self.assertEqual(
            authority["total_bytes"],
            self.plan.to_dict()["model"]["total_bytes"],
        )
        self.assertEqual(
            qwen36.Qwen36DiagnosticPlan.from_bytes(
                self.plan.canonical_bytes()
            ).sha256(),
            self.plan.sha256(),
        )
        self.assertEqual(
            qwen36.Qwen36ModelInventory.from_bytes(
                self.inventory.canonical_bytes()
            ).sha256(),
            self.inventory.sha256(),
        )

    def test_model_inventory_hash_size_revision_and_total_tamper_reject(self) -> None:
        mutations = (
            lambda value: value["files"][0].__setitem__("sha256", "f" * 64),
            lambda value: value["files"][0].__setitem__("bytes", 1),
            lambda value: value.__setitem__("revision", "f" * 40),
            lambda value: value.__setitem__("total_bytes", 1),
        )
        for mutate in mutations:
            value = self.inventory.to_dict()
            mutate(value)
            value = qwen36._identified(value, "inventory_id")
            with self.subTest(mutate=mutate), self.assertRaises(
                qwen36.Qwen36DiagnosticError
            ):
                qwen36.Qwen36ModelInventory(value)

    def test_fake_backend_two_case_raw_first_run_validates(self) -> None:
        backend = _Backend()
        output, result = self._execute(backend)
        self.assertEqual(backend.calls, list(qwen36.CASE_IDS))
        self.assertEqual(result["execution"]["provider_call_count"], 2)
        self.assertEqual(result["execution"]["retry_count"], 0)
        self.assertEqual(
            qwen36.validate_qwen36_diagnostic_result(output)["run_id"],
            result["run_id"],
        )
        for case in result["cases"]:
            raw = output / case["raw_response"]["relative_path"]
            self.assertTrue(raw.is_file())
            self.assertEqual(
                qwen36._sha(raw.read_bytes()),
                case["raw_response"]["sha256"],
            )

    def test_raw_bytes_and_manifest_contract_tamper_reject(self) -> None:
        output, _ = self._execute()
        raw = output / "cases/path3-commerce-checkout/raw_response.bin"
        raw.write_bytes(raw.read_bytes() + b" ")
        with self.assertRaisesRegex(
            qwen36.Qwen36DiagnosticError, "run_raw_live_mismatch"
        ):
            qwen36.validate_qwen36_diagnostic_result(output)

        for field, mutate, error in (
            (
                "model",
                lambda value: value["model"].__setitem__(
                    "revision", "f" * 40
                ),
                "run_model_invalid",
            ),
            (
                "retry",
                lambda value: value["execution"].__setitem__(
                    "retry_count", 1
                ),
                "run_execution_invalid",
            ),
            (
                "dtype",
                lambda value: value["actual_runtime"].__setitem__(
                    "dtype", "fp16"
                ),
                "runtime_profile_mismatch",
            ),
        ):
            clean, _ = self._execute()
            self._rewrite_manifest(clean, mutate)
            with self.subTest(field=field), self.assertRaisesRegex(
                qwen36.Qwen36DiagnosticError, error
            ):
                qwen36.validate_qwen36_diagnostic_result(clean)

    def test_public_executor_has_no_backend_injection_and_records_fixed_provenance(self) -> None:
        self.assertNotIn(
            "backend", inspect.signature(
                qwen36.execute_qwen36_diagnostic
            ).parameters
        )
        output, result = self._execute()
        self.assertEqual(
            result["backend"],
            {
                "kind": "fixed_transformers_backend",
                "class": "TransformersRecoveryBackend",
                "module": "req2web_runtime.qwen27b_recovery_runner",
                "caller_backend_injection_allowed": False,
            },
        )
        self._rewrite_manifest(
            output,
            lambda value: value["backend"].__setitem__(
                "kind", "caller_fixture_backend"
            ),
        )
        with self.assertRaisesRegex(
            qwen36.Qwen36DiagnosticError,
            "run_backend_provenance_invalid",
        ):
            qwen36.validate_qwen36_diagnostic_result(output)

    def test_stream_matches_raw_and_selected_gpu_tamper_fails_final_binding(self) -> None:
        output, result = self._execute(stream_output="console")
        for case in result["cases"]:
            self.assertTrue(case["stream_matches_final_raw"])
            self.assertEqual(case["stream_event_count"], 3)
        self._rewrite_manifest(
            output,
            lambda value: (
                value["actual_runtime"].__setitem__(
                    "gpu_name", "NVIDIA RTX PRO 6000 Blackwell"
                ),
                value["actual_runtime"].__setitem__(
                    "torch_gpu_name",
                    "NVIDIA RTX PRO 6000 Blackwell",
                ),
                value["actual_runtime"].__setitem__(
                    "gpu_uuid", "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
                ),
                value["actual_runtime"].__setitem__(
                    "torch_gpu_uuid",
                    "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                ),
            ),
        )
        qwen36.validate_qwen36_diagnostic_result(output)
        with self.assertRaisesRegex(
            qwen36.Qwen36DiagnosticError,
            "run_selected_gpu_binding_invalid",
        ):
            qwen36.validate_qwen36_diagnostic_result_against(
                output,
                self.plan,
                self.action,
                self.inventory,
                self.runtime_raw,
                Qwen27BCaseBundleTests.first_archive_path,
                Qwen27BCaseBundleTests.first_manifest_path,
                self.bundle.root,
            )

    def test_runtime_inventory_drift_fails_plan_replay(self) -> None:
        second_root = self.work / "runtime-second"
        shutil.copytree(self.runtime_root, second_root)
        second_raw = runtime_authority._dump(
            runtime_authority.collect_runtime_root_inventory(second_root)
        )
        output, _ = self._execute()
        with self.assertRaisesRegex(
            qwen36.Qwen36DiagnosticError, "plan_live_mismatch"
        ):
            qwen36.validate_qwen36_diagnostic_result_against(
                output,
                self.plan,
                self.action,
                self.inventory,
                second_raw,
                Qwen27BCaseBundleTests.first_archive_path,
                Qwen27BCaseBundleTests.first_manifest_path,
                self.bundle.root,
            )

    def test_runtime_drift_fails_before_model_load_and_writes_failure(self) -> None:
        output = self.work / "run-runtime-failure"
        with (
            patch.object(
                qwen36,
                "validate_qwen36_model_root_against_inventory",
                return_value=self.inventory,
            ),
            patch.object(
                qwen36,
                "validate_qwen36_runtime_root_against_inventory",
                return_value=self.runtime_data,
            ),
            patch.object(
                qwen36,
                "TransformersRecoveryBackend",
                return_value=_Backend(dtype="fp16"),
            ),
            patch.object(qwen36, "_progress"),
            self.assertRaisesRegex(
                qwen36.Qwen36DiagnosticError, "runtime_profile_mismatch"
            ),
        ):
            qwen36.execute_qwen36_diagnostic(
                self.plan,
                self.action,
                self.inventory,
                self.runtime_raw,
                self.model_root,
                self.runtime_root,
                Qwen27BCaseBundleTests.first_archive_path,
                Qwen27BCaseBundleTests.first_manifest_path,
                self.bundle.root,
                output,
            )
        self.assertTrue((output / qwen36.FAILURE_FILENAME).is_file())
        self.assertFalse((output / qwen36.RUN_MANIFEST_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
