"""Focused end-to-end returned-result/route test for Qwen3.6."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_runtime import qwen27b_predeploy as runtime_authority  # noqa: E402
from req2web_runtime import qwen36_diagnostic as diagnostic  # noqa: E402
from req2web_runtime import qwen36_diagnostic_route as route  # noqa: E402
from req2web_runtime.autodl_trusted_remote_case_loader import (  # noqa: E402
    build_fixed_trusted_remote_case_materials_v2,
)
from req2web_runtime.qwen27b_case_bundle import (  # noqa: E402
    build_qwen27b_case_bundle,
)
from test_m3_gate_delivery import gate_delivery_candidate_bytes  # noqa: E402
from test_m3_qwen27b_case_bundle import Qwen27BCaseBundleTests  # noqa: E402
from test_m3_semantic_candidate_assembly import raw_bytes  # noqa: E402


class Qwen36DiagnosticRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Qwen27BCaseBundleTests.setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        Qwen27BCaseBundleTests.tearDownClass()

    def setUp(self) -> None:
        self.work = ROOT / ("tmp-qwen36-route-" + uuid4().hex)
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
        self.runtime_raw = runtime_authority._dump(
            runtime_authority.collect_runtime_root_inventory(
                self.runtime_root
            )
        )
        self.plan = diagnostic.create_qwen36_diagnostic_plan(
            Qwen27BCaseBundleTests.first_archive_path,
            Qwen27BCaseBundleTests.first_manifest_path,
            self.bundle.root,
            self.runtime_raw,
        )
        self.inventory = self._inventory()
        self.action = diagnostic.create_qwen36_diagnostic_action_binding(
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

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    @staticmethod
    def _inventory() -> diagnostic.Qwen36ModelInventory:
        authority = diagnostic._official_model_inventory_authority()
        rows = copy.deepcopy(authority["files"])
        body = {
            "schema_version": diagnostic.MODEL_INVENTORY_SCHEMA,
            "inventory_id": "0" * 64,
            "repository": diagnostic.MODEL_REPOSITORY,
            "revision": diagnostic.MODEL_REVISION,
            "official_inventory_authority_id": authority["authority_id"],
            "official_inventory_manifest_sha256": authority[
                "manifest_sha256"
            ],
            "files": rows,
            "file_count": len(rows),
            "total_bytes": sum(row["bytes"] for row in rows),
            "weight_shard_count": 15,
            "weight_shard_total_bytes": (
                diagnostic.MODEL_WEIGHT_SHARD_TOTAL_BYTES
            ),
            "weight_tensor_total_bytes": (
                diagnostic.MODEL_WEIGHT_TENSOR_TOTAL_BYTES
            ),
            "tree_sha256": diagnostic._sha(
                diagnostic._canonical(rows)
            ),
        }
        return diagnostic.Qwen36ModelInventory(
            diagnostic._identified(body, "inventory_id")
        )

    def _media_raw(self) -> bytes:
        material_root = route._accepted_route._prepare_work_root(
            self.work / ("material-" + uuid4().hex)
        )
        try:
            materials = build_fixed_trusted_remote_case_materials_v2(
                ROOT, material_root
            )
            context = materials.case_inputs["path3-media-analysis"][
                "context"
            ]
            value = json.loads(gate_delivery_candidate_bytes(context))
            concepts = {
                "UC-01": (
                    "upload file photo image media select input browse "
                    "permission denied error retry fallback recover"
                ),
                "UC-02": "analyze analysis result output insight",
            }
            sections = {
                row["stable_id"]: row for row in value["sections"]
            }
            for mapping in value["use_case_mappings"]:
                suffix = concepts[mapping["use_case_id"]]
                for stable_id in mapping["section_stable_ids"]:
                    sections[stable_id]["purpose"] += " " + suffix
            return raw_bytes(value)
        finally:
            shutil.rmtree(material_root, ignore_errors=True)

    def _run_root(self, raw_by_case: dict[str, bytes]) -> Path:
        root = self.work / ("run-" + uuid4().hex)
        cases = []
        rows = []
        for case_id in diagnostic.CASE_IDS:
            raw = raw_by_case[case_id]
            relative = f"cases/{case_id}/raw_response.bin"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            binding = {
                "relative_path": relative,
                "byte_length": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            rows.append(binding)
            cases.append(
                {
                    "case_id": case_id,
                    "provider_call_count": 1,
                    "retry_count": 0,
                    "input_tokens": 100,
                    "generated_tokens": 20,
                    "generation_elapsed_ms": 1000,
                    "tokens_per_second": 20.0,
                    "stream_output": "off",
                    "stream_event_count": 0,
                    "stream_matches_final_raw": None,
                    "raw_response": binding,
                    "downstream_state": "not_started_raw_authoritative",
                }
            )
        runtime = {
            "python": "3.11.15",
            "torch": "2.7.1+cu128",
            "transformers": "5.14.1",
            "cuda": "12.8",
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
            "dtype": "bf16",
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
        }
        body = {
            "schema_version": diagnostic.RUN_SCHEMA,
            "run_id": "0" * 64,
            "state": "completed_raw_only",
            "plan": {
                "plan_id": self.plan.to_dict()["plan_id"],
                "sha256": self.plan.sha256(),
            },
            "action_binding": {
                "binding_id": self.action.to_dict()["binding_id"],
                "sha256": self.action.sha256(),
            },
            "model": {
                "repository": diagnostic.MODEL_REPOSITORY,
                "revision": diagnostic.MODEL_REVISION,
                "local_files_only": True,
                "trust_remote_code": False,
            },
            "model_inventory": {
                "inventory_id": self.inventory.to_dict()["inventory_id"],
                "sha256": self.inventory.sha256(),
                "tree_sha256": self.inventory.to_dict()["tree_sha256"],
            },
            "runtime_inventory": self.plan.to_dict()["runtime"],
            "backend": {
                "kind": "fixed_transformers_backend",
                "class": "TransformersRecoveryBackend",
                "module": "req2web_runtime.qwen27b_recovery_runner",
                "caller_backend_injection_allowed": False,
            },
            "actual_runtime": runtime,
            "loaded_model": {
                "device": "cuda:0",
                "dtype": "bf16",
                "quantization": "none",
                "cpu_offload": False,
                "device_map": "none",
                "allocated_vram_bytes": 1,
                "reserved_vram_bytes": 1,
            },
            "model_load_duration_ms": 1,
            "execution": {
                "case_ids": list(diagnostic.CASE_IDS),
                "provider_call_count": 2,
                "retry_count": 0,
                "elapsed_ms": 2000,
            },
            "cases": cases,
            "inventory": {
                "files": rows,
                "file_count": len(rows),
                "total_bytes": sum(row["byte_length"] for row in rows),
                "inventory_sha256": diagnostic._sha(
                    diagnostic._canonical(rows)
                ),
            },
            "claims": {
                "diagnostic_only": True,
                "h1": False,
                "formal_quality": False,
                "browser_quality": False,
                "evidence_use": False,
                "semantic_success": False,
                "lora_evidence": False,
            },
        }
        body = diagnostic._identified(body, "run_id")
        (root / diagnostic.RUN_MANIFEST_FILENAME).write_bytes(
            diagnostic._canonical(body)
        )
        return root

    def test_real_return_validator_parser_gate_and_fallback_are_connected(self) -> None:
        run_root = self._run_root(
            {
                "path3-commerce-checkout": b"not-json-commerce",
                "path3-media-analysis": self._media_raw(),
            }
        )
        record = route.evaluate_qwen36_diagnostic_route(
            run_root=run_root,
            plan=self.plan,
            action_binding=self.action,
            model_inventory=self.inventory,
            runtime_inventory=self.runtime_raw,
            repository_archive=Qwen27BCaseBundleTests.first_archive_path,
            repository_archive_manifest=(
                Qwen27BCaseBundleTests.first_manifest_path
            ),
            case_bundle_root=self.bundle.root,
            work_root=self.work / ("route-work-" + uuid4().hex),
        )
        by_case = {case["case_id"]: case for case in record["cases"]}
        self.assertEqual(record["overall_decision"], "fail_closed")
        self.assertEqual(
            by_case["path3-commerce-checkout"]["decision"],
            "fail_closed",
        )
        self.assertEqual(
            by_case["path3-media-analysis"]["decision"],
            "recovery_pass",
        )
        for case in record["cases"]:
            self.assertEqual(
                case["schema_version"], route.FINAL_ROUTE_CASE_SCHEMA
            )
            self.assertTrue(
                case["claims"]["stronger_checkpoint_diagnostic_only"]
            )
            self.assertFalse(case["claims"]["lora_evidence"])


if __name__ == "__main__":
    unittest.main()
