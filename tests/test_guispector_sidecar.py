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
    GUISPECTOR_DECISION_IMPORT_SCHEMA_VERSION,
    GUISpectorSidecarError,
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
        self.assertIn("openai_api_key_not_configured", result["blocking_reasons"])


if __name__ == "__main__":
    unittest.main()
