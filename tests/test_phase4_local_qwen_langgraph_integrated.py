from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_orchestration.phase4_graph import (  # noqa: E402
    REAL_MODEL_GRAPH_REVISION,
    phase4_synthetic_fixture_output,
    synthetic_commerce_b_input,
)
from req2web_runtime.phase4_canonical_full_flow import _build_upstream  # noqa: E402
from req2web_runtime.phase4_local_qwen_langgraph_integrated import (  # noqa: E402
    HIGH_GPU_PROFILE,
    LOW_GPU_PROFILE,
    local_langgraph_profile,
    run_phase4_local_qwen_langgraph_integrated,
    runtime_capabilities,
)


class Phase4LocalQwenLangGraphIntegratedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / f".local-langgraph-test-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_profiles_separate_low_gpu_debug_from_high_gpu_quality(self) -> None:
        low = local_langgraph_profile(LOW_GPU_PROFILE).to_dict()
        high = local_langgraph_profile(HIGH_GPU_PROFILE).to_dict()
        self.assertEqual(low["quantization"], "nf4_double_quant")
        self.assertFalse(low["formal_quality_eligible"])
        self.assertEqual(low["max_input_tokens"], 8192)
        self.assertEqual(low["timeout_seconds"], 3600)
        self.assertEqual(high["quantization"], "none")
        self.assertTrue(high["formal_quality_eligible"])
        self.assertEqual(high["timeout_seconds"], 1200)
        for profile in (low, high):
            self.assertFalse(profile["input_truncation"])
            self.assertFalse(profile["output_truncation"])
            self.assertEqual(profile["automatic_retry_limit"], 0)

    def test_capability_reuses_formal_graph_and_prompt_authority(self) -> None:
        value = runtime_capabilities()
        self.assertEqual(value["workflow_runtime"], REAL_MODEL_GRAPH_REVISION)
        self.assertTrue(value["one_call_per_node"])
        self.assertFalse(value["automatic_retry"])
        self.assertFalse(value["b_aux_consumed_by_f1_f4"])

    def test_scripted_raw_probe_traverses_formal_graph_and_delivers_package(self) -> None:
        case = synthetic_commerce_b_input()
        upstream_root = self.root / "upstream"
        upstream = _build_upstream(
            case=case,
            index_dir=ROOT / "data/processed/rag",
            output_root=upstream_root,
        )
        model_root = self.root / "model"
        model_root.mkdir()
        evidence = self.root / "integrity.json"
        evidence.write_text("{}", encoding="utf-8")

        def raw_node_generator(node_id, authority_state):
            return json.dumps(
                phase4_synthetic_fixture_output(node_id, authority_state),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")

        inventory = {
            "inventory_identity": {
                "identity_kind": "canonical_json",
                "sha256": "sha256:" + ("a" * 64),
                "byte_length": 1,
                "revision": "test.inventory.v1",
            }
        }
        with patch(
            "req2web_runtime.phase4_local_qwen_langgraph_integrated.validate_model_inventory_metadata",
            return_value=inventory,
        ):
            summary = run_phase4_local_qwen_langgraph_integrated(
                model_root=model_root,
                integrity_evidence=evidence,
                result_root=self.root / "result",
                b_input=upstream["adaptation"].b_input,
                upstream_context=upstream["context"],
                upstream_guidance=upstream["guidance"],
                upstream_binding=upstream["receipt"],
                confirm_one_local_langgraph_run=True,
                b_aux_sidecar={"advisory_marker": "must-not-enter-f1-f4"},
                _raw_node_generator=raw_node_generator,
            )

        self.assertTrue(summary["agent_chain_completed"])
        self.assertIn(
            summary["status"],
            {
                "completed_model_delivery",
                "completed_delivery_via_same_case_g0_fallback",
            },
            msg=json.dumps(summary, ensure_ascii=False, indent=2),
        )
        self.assertEqual(summary["workflow_runtime"], REAL_MODEL_GRAPH_REVISION)
        self.assertFalse(summary["manual_f1_f4_loop_used"])
        self.assertEqual(
            summary["model_call_ledger"]["total_generate_started_count"],
            4,
        )
        self.assertTrue(Path(summary["selected_package_root"]).is_dir())
        self.assertTrue(
            (Path(summary["selected_package_root"]) / "package_manifest.json").is_file()
        )
        self.assertFalse(summary["b_aux_sidecar"]["f1_f4_input"])
        for node_id in ("F1", "F2", "F3", "F4"):
            node_input = (
                self.root / "result" / "attempts" / node_id / "input.json"
            ).read_text(encoding="utf-8")
            self.assertNotIn("must-not-enter-f1-f4", node_input)

    def test_node_failure_keeps_same_context_g0_without_retry(self) -> None:
        case = synthetic_commerce_b_input()
        upstream = _build_upstream(
            case=case,
            index_dir=ROOT / "data/processed/rag",
            output_root=self.root / "failure-upstream",
        )
        model_root = self.root / "failure-model"
        model_root.mkdir()
        evidence = self.root / "failure-integrity.json"
        evidence.write_text("{}", encoding="utf-8")

        def invalid_f2(node_id, authority_state):
            if node_id == "F2":
                return b"not-json"
            return json.dumps(
                phase4_synthetic_fixture_output(node_id, authority_state),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")

        inventory = {
            "inventory_identity": {
                "identity_kind": "canonical_json",
                "sha256": "sha256:" + ("b" * 64),
                "byte_length": 1,
                "revision": "test.inventory.v1",
            }
        }
        with patch(
            "req2web_runtime.phase4_local_qwen_langgraph_integrated.validate_model_inventory_metadata",
            return_value=inventory,
        ):
            summary = run_phase4_local_qwen_langgraph_integrated(
                model_root=model_root,
                integrity_evidence=evidence,
                result_root=self.root / "failure-result",
                b_input=upstream["adaptation"].b_input,
                upstream_context=upstream["context"],
                upstream_guidance=upstream["guidance"],
                upstream_binding=upstream["receipt"],
                confirm_one_local_langgraph_run=True,
                _raw_node_generator=invalid_f2,
            )

        self.assertEqual(
            summary["status"],
            "model_failed_closed_deterministic_g0_available",
        )
        self.assertFalse(summary["agent_chain_completed"])
        self.assertFalse(summary["model_result_available"])
        self.assertTrue(summary["deterministic_g0_available"])
        self.assertEqual(summary["selected_delivery_kind"], "deterministic_g0_result_package_v2")
        self.assertEqual(summary["model_call_ledger"]["automatic_retry_count"], 0)
        self.assertEqual(
            summary["model_call_ledger"]["total_generate_started_count"],
            2,
        )


if __name__ == "__main__":
    unittest.main()
