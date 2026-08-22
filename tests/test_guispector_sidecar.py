from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.guispector_sidecar import (  # noqa: E402
    GUISPECTOR_BATCH_EXECUTION_SCHEMA_VERSION,
    GUISPECTOR_DECISION_IMPORT_SCHEMA_VERSION,
    GUISpectorSidecarError,
    build_guispector_batch_metrics,
    build_guispector_comparison,
    guispector_runtime_preflight,
    normalize_guispector_decision,
)


def _case(
    index: int,
    requirement_label: str,
    criterion_met: bool,
) -> dict[str, object]:
    return {
        "execution_index": index,
        "case_id": f"case-{index}",
        "guispector_requirement": {
            "acceptance_criteria": [
                {"criterion_name": "AC-1", "description": "A fixed criterion."}
            ]
        },
        "internal_reference": {
            "requirement_status": requirement_label,
            "acceptance_criteria": [
                {"criterion_name": "AC-1", "met": criterion_met}
            ],
        },
    }


def _raw_decision(status: str, met: bool) -> dict[str, object]:
    return {
        "status": status,
        "explanation": "Bounded test decision.",
        "detailed_summary": "Bounded test decision.",
        "acceptance_criteria_results": [
            {"criterion_name": "AC-1", "met": met, "evidence": "Observed."}
        ],
        "final_url": "http://127.0.0.1/example",
        "notes": "",
    }


class GUISpectorSidecarTests(unittest.TestCase):
    def test_batch_metrics_keep_errors_as_abstentions(self) -> None:
        evaluation = {
            "evaluation_identity": "batch-evaluation",
            "scope": {"acceptance_criterion_count": 2},
            "reference_profile": {"limitation": "Positive-only reference."},
            "published_reference_metrics": {},
            "cases": [_case(1, "met", True), _case(2, "met", True)],
        }
        execution = {
            "schema_version": GUISPECTOR_BATCH_EXECUTION_SCHEMA_VERSION,
            "evaluation_identity": "batch-evaluation",
            "rows": [
                {
                    "execution_index": 1,
                    "case_id": "case-1",
                    "condition_id": None,
                    "run_id": 10,
                    "status": "met",
                    "steps_taken": 5,
                    "elapsed_s": 20.0,
                    "usage": {"tokens_in": 1000, "tokens_out": 100},
                    "decision": _raw_decision("met", True),
                    "error": None,
                },
                {
                    "execution_index": 2,
                    "case_id": "case-2",
                    "condition_id": None,
                    "run_id": 11,
                    "status": "error",
                    "steps_taken": 5,
                    "elapsed_s": 30.0,
                    "usage": None,
                    "decision": None,
                    "error": "Terminal response was malformed.",
                },
            ],
        }
        for case in evaluation["cases"]:
            case["condition_id"] = None
        report = build_guispector_batch_metrics(evaluation, execution)
        self.assertEqual(report["decision_count"], 1)
        self.assertEqual(report["execution_error_count"], 1)
        self.assertEqual(report["decision_completion_rate"], 0.5)
        self.assertEqual(report["criterion_judgment_coverage"], 0.5)
        self.assertEqual(
            report["completed_decision_metrics"]["requirement_level"]["item_count"],
            1,
        )
        self.assertEqual(report["all_attempt_efficiency"]["steps"]["mean"], 5.0)
        conservative = report["conservative_end_to_end_metrics"]
        self.assertEqual(
            conservative["requirement_level"]["abstention_count"],
            1,
        )
        self.assertEqual(
            conservative["requirement_level"][
                "accuracy_with_abstention_as_incorrect"
            ],
            0.5,
        )
        self.assertEqual(
            conservative["requirement_level"]["met_positive_detection"][
                "precision"
            ],
            1.0,
        )
        self.assertEqual(
            conservative["requirement_level"]["met_positive_detection"][
                "recall"
            ],
            0.5,
        )
        self.assertAlmostEqual(
            conservative["requirement_level"]["met_positive_detection"]["f1"],
            2 / 3,
        )
        self.assertAlmostEqual(
            conservative["acceptance_criterion_level"][
                "met_positive_detection"
            ]["f1"],
            2 / 3,
        )
        self.assertFalse(report["paper_comparison_eligible"])

    def test_batch_metrics_reject_error_rows_with_decisions(self) -> None:
        evaluation = {
            "evaluation_identity": "batch-error",
            "scope": {"acceptance_criterion_count": 1},
            "reference_profile": {"limitation": "Positive-only reference."},
            "published_reference_metrics": {},
            "cases": [_case(1, "met", True)],
        }
        evaluation["cases"][0]["condition_id"] = None
        execution = {
            "schema_version": GUISPECTOR_BATCH_EXECUTION_SCHEMA_VERSION,
            "evaluation_identity": "batch-error",
            "rows": [
                {
                    "execution_index": 1,
                    "case_id": "case-1",
                    "condition_id": None,
                    "run_id": 12,
                    "status": "error",
                    "steps_taken": 0,
                    "elapsed_s": 1.0,
                    "decision": _raw_decision("met", True),
                    "error": "Malformed terminal response.",
                }
            ],
        }
        with self.assertRaisesRegex(GUISpectorSidecarError, "carries a decision"):
            build_guispector_batch_metrics(evaluation, execution)

    def test_documented_status_aliases_are_normalized(self) -> None:
        self.assertEqual(
            normalize_guispector_decision(
                _raw_decision("not_met", False),
                ["AC-1"],
            )["requirement_label"],
            "unmet",
        )
        self.assertEqual(
            normalize_guispector_decision(
                _raw_decision("partially_met", True),
                ["AC-1"],
            )["requirement_label"],
            "partial",
        )

    def test_criterion_order_drift_fails_closed(self) -> None:
        with self.assertRaisesRegex(GUISpectorSidecarError, "criterion set or order"):
            normalize_guispector_decision(
                _raw_decision("met", True),
                ["AC-2"],
            )

    def test_same_formula_metrics_cover_all_requirement_classes(self) -> None:
        evaluation = {
            "evaluation_identity": "evaluation-1",
            "reference_profile": {"limitation": "Synthetic metric test only."},
            "cases": [
                _case(1, "met", True),
                _case(2, "unmet", False),
                _case(3, "partial", True),
            ],
        }
        imported = {
            "schema_version": GUISPECTOR_DECISION_IMPORT_SCHEMA_VERSION,
            "evaluation_identity": "evaluation-1",
            "decisions": [
                {"execution_index": 1, "case_id": "case-1", "decision": _raw_decision("met", True)},
                {"execution_index": 2, "case_id": "case-2", "decision": _raw_decision("not_met", False)},
                {"execution_index": 3, "case_id": "case-3", "decision": _raw_decision("partially_met", True)},
            ],
        }
        report = build_guispector_comparison(evaluation, imported)
        for label in ("met", "unmet", "partial"):
            self.assertEqual(
                report["requirement_level"]["classes"][label]["f1"],
                1.0,
            )
        for label in ("met", "unmet"):
            self.assertEqual(
                report["acceptance_criterion_level"]["classes"][label]["f1"],
                1.0,
            )
        self.assertFalse(report["paper_comparison_eligible"])
        self.assertFalse(report["formal_evaluation"])

    def test_absent_reference_classes_are_undefined_not_zero(self) -> None:
        evaluation = {
            "evaluation_identity": "evaluation-positive-only",
            "reference_profile": {"limitation": "Positive-only reference."},
            "cases": [_case(1, "met", True)],
        }
        imported = {
            "schema_version": GUISPECTOR_DECISION_IMPORT_SCHEMA_VERSION,
            "evaluation_identity": "evaluation-positive-only",
            "decisions": [
                {"execution_index": 1, "case_id": "case-1", "decision": _raw_decision("met", True)}
            ],
        }
        report = build_guispector_comparison(evaluation, imported)
        self.assertEqual(report["requirement_level"]["classes"]["met"]["f1"], 1.0)
        self.assertIsNone(report["requirement_level"]["classes"]["unmet"]["f1"])
        self.assertIsNone(report["requirement_level"]["classes"]["partial"]["f1"])
        self.assertIsNone(report["acceptance_criterion_level"]["classes"]["unmet"]["f1"])

    def test_preflight_exposes_presence_only_and_runs_nothing(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "req2web_inspector.guispector_sidecar.shutil.which",
            return_value=None,
        ):
            result = guispector_runtime_preflight()
        self.assertEqual(result["status"], "not_ready")
        self.assertFalse(result["execution_performed"])
        self.assertFalse(result["secret_values_exposed"])
        self.assertIn("docker_cli_not_available", result["blocking_reasons"])
        self.assertIn("model_api_key_not_configured", result["blocking_reasons"])
        self.assertFalse(result["zhipu_api_key_configured"])
        self.assertFalse(result["openai_api_key_configured"])
        self.assertFalse(result["dashscope_api_key_configured"])
        self.assertFalse(result["dashscope_workspace_id_configured"])
        self.assertFalse(result["gui_plus_configured"])

    def test_preflight_accepts_req2web_compose_and_zhipu_presence(self) -> None:
        with patch.dict(
            os.environ,
            {
                "REQ2WEB_GUISPECTOR_ROOT": "D:/bounded-guispector-checkout",
                "ZHIPU_API_KEY": "presence-only-test-value",
            },
            clear=True,
        ), patch(
            "req2web_inspector.guispector_sidecar.shutil.which",
            return_value="docker",
        ), patch.object(
            Path,
            "is_dir",
            autospec=True,
            return_value=True,
        ), patch.object(
            Path,
            "is_file",
            autospec=True,
            side_effect=lambda path: path.name == "docker-compose.req2web.yml",
        ):
            result = guispector_runtime_preflight()
        self.assertEqual(result["status"], "ready_for_operator_started_external_run")
        self.assertTrue(result["guispector_checkout_configured"])
        self.assertTrue(result["model_api_key_configured"])
        self.assertTrue(result["zhipu_api_key_configured"])
        self.assertFalse(result["openai_api_key_configured"])
        self.assertFalse(result["gui_plus_configured"])
        self.assertEqual(result["blocking_reasons"], [])
        self.assertNotIn("presence-only-test-value", str(result))

    def test_preflight_accepts_only_a_complete_gui_plus_configuration(self) -> None:
        environment = {
            "REQ2WEB_GUISPECTOR_ROOT": "D:/bounded-guispector-checkout",
            "DASHSCOPE_API_KEY": "presence-only-dashscope-key",
            "DASHSCOPE_WORKSPACE_ID": "workspace-1",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "req2web_inspector.guispector_sidecar.shutil.which",
            return_value="docker",
        ), patch.object(
            Path,
            "is_dir",
            autospec=True,
            return_value=True,
        ), patch.object(
            Path,
            "is_file",
            autospec=True,
            side_effect=lambda path: path.name == "docker-compose.req2web.yml",
        ):
            result = guispector_runtime_preflight()
        self.assertEqual(result["status"], "ready_for_operator_started_external_run")
        self.assertTrue(result["model_api_key_configured"])
        self.assertTrue(result["dashscope_api_key_configured"])
        self.assertTrue(result["dashscope_workspace_id_configured"])
        self.assertTrue(result["gui_plus_configured"])
        self.assertNotIn("presence-only-dashscope-key", str(result))
        self.assertNotIn("workspace-1", str(result))


if __name__ == "__main__":
    unittest.main()
