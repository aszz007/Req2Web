from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "review_ui_reference_signals.py"
SPEC = importlib.util.spec_from_file_location("ui_reference_signal_review", SCRIPT)
assert SPEC and SPEC.loader
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


@unittest.skipUnless(
    (review.framework_evidence_root(ROOT) / "demo_regression").is_dir(),
    "local deterministic regression evidence is unavailable",
)
class UiReferenceSignalReviewTest(TestCase):
    def test_frozen_inputs_cover_42_documents_four_targets_and_twelve_cases(self) -> None:
        report = review.generate_review(
            documents_path=ROOT / "data" / "processed" / "rag" / "documents.jsonl",
            package_root=review.framework_evidence_root(ROOT) / "demo_regression",
        )
        self.assertEqual(report["scope"]["document_count"], 42)
        self.assertEqual(report["scope"]["target_case_count"], 4)
        self.assertEqual(report["scope"]["frozen_package_count"], 12)
        self.assertEqual([item["case_id"] for item in report["target_units"]], list(review.TARGET_CASE_IDS))
        self.assertEqual(report["field_statistics"]["references"]["complete_four_kind_count"], 42)

    def test_target_verdicts_preserve_audit_only_and_semantic_rejection(self) -> None:
        report = review.generate_review(
            documents_path=ROOT / "data" / "processed" / "rag" / "documents.jsonl",
            package_root=review.framework_evidence_root(ROOT) / "demo_regression",
        )
        verdicts = {item["case_id"]: item["verdict"] for item in report["target_units"]}
        self.assertEqual(verdicts["mobile-appointment"], "safe_candidate_with_minimal_controlled_vocab_extension")
        self.assertEqual(verdicts["mobile-auth"], "safe_candidate_with_minimal_controlled_vocab_extension")
        self.assertEqual(verdicts["profile-settings"], "audit_only")
        self.assertEqual(verdicts["pet-recognition"], "continue_reject")

    def test_write_is_byte_deterministic(self) -> None:
        report = review.generate_review(
            documents_path=ROOT / "data" / "processed" / "rag" / "documents.jsonl",
            package_root=review.framework_evidence_root(ROOT) / "demo_regression",
        )
        self.assertEqual(review._json_bytes(report), review._json_bytes(report))
        target_rows = [{field: "" for field in review.TARGET_CSV_FIELDS}]
        self.assertEqual(
            review._csv_bytes(target_rows, review.TARGET_CSV_FIELDS),
            review._csv_bytes(target_rows, review.TARGET_CSV_FIELDS),
        )

    def test_rejects_missing_or_invalid_processed_ui_documents(self) -> None:
        documents = (ROOT / "data" / "processed" / "rag" / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        reduced = list(documents)
        del reduced[next(index for index, line in enumerate(reduced) if json.loads(line).get("role") == "ui_reference")]
        with patch.object(review.Path, "read_text", return_value="\n".join(reduced) + "\n"):
            with self.assertRaisesRegex(ValueError, "42 ui_reference"):
                review.load_ui_documents(ROOT / "missing.jsonl")
        with patch.object(review.Path, "read_text", return_value="{not-json}\n"):
            with self.assertRaisesRegex(ValueError, "invalid JSONL"):
                review.load_ui_documents(ROOT / "invalid.jsonl")

    def test_read_boundary_does_not_open_referenced_assets(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("data/raw", source)
        document = {
            "doc_id": "ui_reference:rico:combined:fixture", "sample_id": "fixture", "role": "ui_reference",
            "metadata": {"category": "form_input", "component_labels": {"Input": 1}, "icon_classes": {}},
            "references": [{"kind": "screenshot", "uri": "not-opened/asset.jpg"}],
        }
        signals = review.classify_signal_evidence(document)
        self.assertIn("form_structure", {item["value"] for item in signals})

    def test_compact_boundary_records_existing_loss(self) -> None:
        report = review.generate_review(
            documents_path=ROOT / "data" / "processed" / "rag" / "documents.jsonl",
            package_root=review.framework_evidence_root(ROOT) / "demo_regression",
        )
        boundary = report["field_boundary"]
        self.assertIn("metadata.component_labels", boundary["lost_from_compact_result"])
        self.assertEqual(boundary["currently_used_by_guidance"], ["title", "summary", "references"])
