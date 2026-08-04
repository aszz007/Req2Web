"""Focused no-model tests for the Phase 4 integrated savepoint foundation."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from req2web_runtime.phase4_local_qwen_integrated import (
    INTEGRATED_F4_RAW_NAME,
    Phase4LocalQwenIntegratedFoundationError,
    run_phase4_local_qwen_integrated_savepoint_foundation,
)


class IntegratedSavepointFoundationTests(unittest.TestCase):
    F3_ROOT = _ROOT / "phase4_f3_savepoint_20260804"
    F4_ROOT = _ROOT / "phase4_f4_savepoint_20260804"
    TEST_ROOT = _ROOT / ".phase4-integrated-test-output"

    def setUp(self):
        if self.TEST_ROOT.exists():
            shutil.rmtree(self.TEST_ROOT)
        self.TEST_ROOT.mkdir()
        self.addCleanup(shutil.rmtree, self.TEST_ROOT, True)
        binding = json.loads(
            (
                self.F4_ROOT / "source_f4_failure_binding.json"
            ).read_text(encoding="utf-8")
        )
        source_root = Path(binding["resolved_source_root"])
        if not source_root.is_dir():
            self.skipTest(
                "the bound F4 raw source is unavailable; no model output is "
                "checked into the repository"
            )
        self.seed_root = self.TEST_ROOT / "seed"
        run_phase4_local_qwen_integrated_savepoint_foundation(
            f3_savepoint_root=self.F3_ROOT,
            f4_savepoint_root=self.F4_ROOT,
            f4_source_result_root=source_root,
            result_root=self.seed_root,
            confirm_no_model_integrated_foundation=True,
        )

    def test_existing_savepoints_restore_and_assemble_without_model(self):
        result = run_phase4_local_qwen_integrated_savepoint_foundation(
            f3_savepoint_root=self.F3_ROOT,
            f4_savepoint_root=self.F4_ROOT,
            f4_raw_artifact_root=self.seed_root,
            result_root=self.TEST_ROOT / "integrated",
            confirm_no_model_integrated_foundation=True,
        )

        self.assertEqual(
            result["status"],
            "integrated_savepoint_foundation_ready",
        )
        self.assertEqual(result["model_generate_calls"], 0)
        self.assertFalse(result["raw_model_contract_success"])
        self.assertTrue(result["restored_node_contracts_validated"])
        self.assertTrue(result["agent_chain_system_output_usable"])
        self.assertEqual(result["registry_status"], "validated_and_registered")
        self.assertEqual(result["assembler_status"], "assembled")
        self.assertEqual(result["completed_node_ids"], ["F1", "F2", "F3", "F4"])
        self.assertEqual(result["skipped_model_node_ids"], ["F1", "F2", "F3", "F4"])
        self.assertEqual(
            result["downstream"]["acceptance"],
            "not_executed",
        )
        self.assertEqual(
            (self.seed_root / INTEGRATED_F4_RAW_NAME).read_bytes(),
            (self.TEST_ROOT / "integrated" / INTEGRATED_F4_RAW_NAME).read_bytes(),
        )

    def test_checkpoint_reuse_is_not_reported_as_raw_model_success(self):
        result = run_phase4_local_qwen_integrated_savepoint_foundation(
            f3_savepoint_root=self.F3_ROOT,
            f4_savepoint_root=self.F4_ROOT,
            f4_raw_artifact_root=self.seed_root,
            result_root=self.TEST_ROOT / "integrated",
            confirm_no_model_integrated_foundation=True,
        )

        self.assertTrue(result["checkpoint_reuse_is_not_raw_model_success"])
        self.assertEqual(
            result["node_outcomes"][0]["raw_model_success_claim"],
            "not_claimed_from_checkpoint",
        )
        self.assertEqual(
            result["node_outcomes"][-1]["restored_contract_status"],
            "validated_after_generic_ref_normalization",
        )
        self.assertEqual(
            result["fresh_local_qwen_generate_seam"],
            "reserved_not_executed",
        )

    def test_source_raw_drift_fails_closed(self):
        source_copy = self.TEST_ROOT / "f4-raw-artifact"
        source_copy.mkdir()
        shutil.copy2(
            self.seed_root / INTEGRATED_F4_RAW_NAME,
            source_copy / INTEGRATED_F4_RAW_NAME,
        )
        raw_path = source_copy / INTEGRATED_F4_RAW_NAME
        raw_path.write_bytes(raw_path.read_bytes() + b"\n")

        with self.assertRaises(Phase4LocalQwenIntegratedFoundationError):
            run_phase4_local_qwen_integrated_savepoint_foundation(
                f3_savepoint_root=self.F3_ROOT,
                f4_savepoint_root=self.F4_ROOT,
                f4_raw_artifact_root=source_copy,
                result_root=self.TEST_ROOT / "integrated",
                confirm_no_model_integrated_foundation=True,
            )


if __name__ == "__main__":
    unittest.main()
