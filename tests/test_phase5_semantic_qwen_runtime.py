from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_evaluation.phase5_semantic_qwen_runtime import (  # noqa: E402
    HIGH_GPU_PROFILE,
    LOW_GPU_PROFILE,
    MODEL_ID,
    Phase5SemanticQwenRuntimeError,
    prepare_phase5_semantic_case,
    run_phase5_semantic_qwen,
    semantic_qwen_runtime_profile,
)
from req2web_evaluation.phase5_semantic_evaluator import (  # noqa: E402
    Phase5SemanticEvaluatorError,
)


AUDIT_ROOT = (
    ROOT
    / "outputs"
    / "phase4_browser_canary_20260807_v2"
    / "audits"
    / "case01"
)
PACKAGE_ROOT = (
    ROOT
    / "outputs"
    / "phase4_browser_canary_20260807"
    / "packages"
    / "case01"
    / "result_package_v1"
)


class Phase5SemanticQwenRuntimeTest(unittest.TestCase):
    def test_profiles_separate_low_smoke_and_high_quality_runtime(self) -> None:
        low = semantic_qwen_runtime_profile(LOW_GPU_PROFILE).to_dict()
        high = semantic_qwen_runtime_profile(HIGH_GPU_PROFILE).to_dict()

        self.assertEqual(low["model_id"], MODEL_ID)
        self.assertEqual(low["quantization"], "nf4")
        self.assertFalse(low["formal_quality_eligible"])
        self.assertEqual(
            low["evidence_projection"],
            "validated_identities_review_items_and_screenshot",
        )
        self.assertEqual(high["quantization"], "none")
        self.assertTrue(high["formal_quality_eligible"])
        self.assertFalse(high["cpu_offload"])
        self.assertEqual(
            high["evidence_projection"],
            "full_frozen_evidence",
        )

    def test_mixed_historical_phase4_case_is_rejected(self) -> None:
        if not AUDIT_ROOT.is_dir() or not PACKAGE_ROOT.is_dir():
            self.skipTest("tracked Phase 4 browser evidence is unavailable")
        with self.assertRaisesRegex(
            Phase5SemanticEvaluatorError,
            "request is not English-only",
        ):
            prepare_phase5_semantic_case(
                audit_root=AUDIT_ROOT,
                result_package_root=PACKAGE_ROOT,
                generator_model_identity="Qwen/Qwen3.5-9B@test",
                profile_name=LOW_GPU_PROFILE,
            )

    def test_model_action_confirmation_precedes_result_creation(self) -> None:
        result_root = (
            ROOT / ".phase5-semantic-confirmation-guard-result"
        )
        self.assertFalse(result_root.exists())
        with self.assertRaisesRegex(
            Phase5SemanticQwenRuntimeError,
            "explicit confirmation",
        ):
            run_phase5_semantic_qwen(
                audit_root=AUDIT_ROOT,
                result_package_root=PACKAGE_ROOT,
                model_root=ROOT,
                result_root=result_root,
                profile_name=LOW_GPU_PROFILE,
                confirm_model_action=False,
            )
        self.assertFalse(result_root.exists())


if __name__ == "__main__":
    unittest.main()
