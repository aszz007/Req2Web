from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime.phase4_downstream_revalidation import (  # noqa: E402
    Phase4DownstreamRevalidationError,
    run_phase4_downstream_status_revalidation,
)


FLOW_SUMMARY = (
    ROOT
    / "outputs"
    / "phase4_browser_final_20260807"
    / "remote_result"
    / "flow_summary.json"
)
FINAL_BROWSER_SUMMARY = (
    ROOT
    / "outputs"
    / "phase4_browser_final_20260807"
    / "final_browser_summary.json"
)
LEGACY_CANARY_ROOT = (
    ROOT / "outputs" / "phase4_browser_canary_20260807"
)
BROWSER_AUDIT_ROOT = (
    ROOT / "outputs" / "phase4_browser_canary_20260807_v2"
)
TEST_OUTPUT = ROOT / ".phase4-downstream-revalidation-test"
FROZEN_EVIDENCE_PRESENT = all(
    path.exists()
    for path in (
        FLOW_SUMMARY,
        FINAL_BROWSER_SUMMARY,
        LEGACY_CANARY_ROOT,
        BROWSER_AUDIT_ROOT,
    )
)


class Phase4DownstreamRevalidationTest(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_OUTPUT.is_dir():
            shutil.rmtree(TEST_OUTPUT)

    @unittest.skipUnless(
        FROZEN_EVIDENCE_PRESENT,
        "frozen Phase 4 handoff evidence is not present in this checkout",
    )
    def test_three_runtime_status_cases_revalidate_without_model_calls(
        self,
    ) -> None:
        result = run_phase4_downstream_status_revalidation(
            flow_summary_path=FLOW_SUMMARY,
            final_browser_summary_path=FINAL_BROWSER_SUMMARY,
            legacy_canary_root=LEGACY_CANARY_ROOT,
            browser_audit_root=BROWSER_AUDIT_ROOT,
            output_root=TEST_OUTPUT,
            confirm_zero_model_revalidation=True,
        )
        self.assertEqual(
            result["status"],
            "phase4_terminal_evidence_ready_for_handoff",
        )
        self.assertEqual(
            result["counts"]["historical_downstream_first_pass_success_count"],
            5,
        )
        self.assertEqual(
            result["counts"]["runtime_status_revalidated_case_count"],
            3,
        )
        self.assertEqual(
            result["counts"]["delivery_evidence_available_count"],
            8,
        )
        self.assertEqual(result["counts"]["model_generate_calls_added"], 0)
        self.assertEqual(result["counts"]["automatic_retry_count_added"], 0)
        for index in (1, 2, 3):
            row = result["case_rows"][index - 1]
            self.assertEqual(
                row["disposition"],
                "validated_after_runtime_status_revalidation",
            )
            receipt = json.loads(
                (
                    TEST_OUTPUT
                    / "case_receipts"
                    / f"{index:02d}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertFalse(receipt["historical_case_summary_overwritten"])
            self.assertEqual(receipt["model_generate_calls_added"], 0)

    def test_explicit_confirmation_is_required(self) -> None:
        with self.assertRaisesRegex(
            Phase4DownstreamRevalidationError,
            "confirmation",
        ):
            run_phase4_downstream_status_revalidation(
                flow_summary_path=FLOW_SUMMARY,
                final_browser_summary_path=FINAL_BROWSER_SUMMARY,
                legacy_canary_root=LEGACY_CANARY_ROOT,
                browser_audit_root=BROWSER_AUDIT_ROOT,
                output_root=TEST_OUTPUT,
                confirm_zero_model_revalidation=False,
            )


if __name__ == "__main__":
    unittest.main()
