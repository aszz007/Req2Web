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


from req2web_runtime.phase5_publication_policy_revalidation import (  # noqa: E402
    Phase5PublicationPolicyRevalidationError,
    _validate_row_summary,
    run_phase5_publication_policy_revalidation,
)


EXTERNAL_RETURN = Path(
    r"D:\Req2WebPhase5Returns\phase5-publication-path2-v16-full-20260809-a"
)
RESULT_TAR = EXTERNAL_RETURN / "result_return.tar"
RESULT_MANIFEST = EXTERNAL_RETURN / "result_return_manifest.json"
TEST_OUTPUT = ROOT / ".phase5-publication-policy-revalidation-test"


class Phase5PublicationPolicyRevalidationTest(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_OUTPUT.is_dir():
            shutil.rmtree(TEST_OUTPUT)

    def test_explicit_confirmation_is_required(self) -> None:
        with self.assertRaisesRegex(
            Phase5PublicationPolicyRevalidationError,
            "confirmation",
        ):
            run_phase5_publication_policy_revalidation(
                result_tar_path=RESULT_TAR,
                result_manifest_path=RESULT_MANIFEST,
                output_root=TEST_OUTPUT,
                confirm_zero_model_revalidation=False,
            )

    @unittest.skipUnless(
        RESULT_TAR.is_file() and RESULT_MANIFEST.is_file(),
        "the owner-custody v16 result return is not present",
    )
    def test_exact_v16_return_revalidates_without_model_calls(self) -> None:
        summary = run_phase5_publication_policy_revalidation(
            result_tar_path=RESULT_TAR,
            result_manifest_path=RESULT_MANIFEST,
            output_root=TEST_OUTPUT,
            confirm_zero_model_revalidation=True,
        )
        self.assertEqual(
            summary["status"],
            "zero_model_policy_revalidation_complete_pending_browser",
        )
        self.assertEqual(
            summary["counts"][
                "historical_downstream_first_pass_success_count"
            ],
            9,
        )
        self.assertEqual(
            summary["counts"]["f4_policy_revalidated_row_count"],
            3,
        )
        self.assertEqual(
            summary["counts"]["delivery_evidence_available_count"],
            12,
        )
        self.assertEqual(summary["counts"]["model_generate_calls_added"], 0)
        self.assertEqual(summary["counts"]["automatic_retry_count_added"], 0)
        self.assertEqual(
            [
                row["execution_index"]
                for row in summary["row_results"]
                if row["disposition"]
                == "validated_after_f4_policy_revalidation"
            ],
            [2, 5, 9],
        )
        for index in (2, 5, 9):
            receipt = json.loads(
                (TEST_OUTPUT / "row_receipts" / f"{index:02d}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(receipt["historical_artifact_overwritten"])
            self.assertEqual(receipt["model_generate_calls_added"], 0)
            self.assertTrue(receipt["amended_policy_raw_bytes_accepted"])

    def test_row_summary_identity_and_historical_prompt_are_required(self) -> None:
        root = {
            "schema_version": "req2web.phase5.publication_action.v1.row_summary",
            "execution_index": 1,
            "row_id": "row-1",
            "shared_flow_summary": {
                "prompt_authority_identity": {
                    "sha256": (
                        "sha256:68de2486f92ccf20aa3c2b6ba3826c3f201ba2ba14e9355e1a86ae40aca4c66f"
                    )
                },
                "per_node_generate_started": {
                    "F1": 1,
                    "F2": 1,
                    "F3": 1,
                    "F4": 1,
                },
            },
        }
        from req2web_runtime.phase5_publication_policy_revalidation import _identity

        row = {
            **root,
            "row_summary_identity": _identity(
                root,
                revision="req2web.phase5.publication_action.v1.row_summary",
            ),
        }
        self.assertEqual(_validate_row_summary(row, index=1)["row_id"], "row-1")
        row["shared_flow_summary"]["prompt_authority_identity"]["sha256"] = (
            "sha256:" + "0" * 64
        )
        with self.assertRaisesRegex(
            Phase5PublicationPolicyRevalidationError,
            "identity drifted",
        ):
            _validate_row_summary(row, index=1)


if __name__ == "__main__":
    unittest.main()
