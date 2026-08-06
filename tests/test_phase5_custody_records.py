from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_evaluation.phase5_custody_records import (  # noqa: E402
    SyntheticCalibrationReceipt,
    build_synthetic_duplicate_calibration,
    create_opaque_duplicate_adjudication,
    create_synthetic_annotation_adjudication,
    create_synthetic_annotation_commitment,
    validate_opaque_duplicate_adjudication,
    validate_synthetic_annotation_adjudication,
    validate_synthetic_annotation_commitment,
)


FIXTURE = ROOT / "fixtures" / "phase5_duplicate_calibration_synthetic_v1.json"


class Phase5CustodyRecordsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.receipt = build_synthetic_duplicate_calibration(self.fixture)
        self.payload = self.receipt.to_dict()

    def test_calibration_is_synthetic_only_and_formal_threshold_absent(self) -> None:
        self.assertEqual(
            self.payload["status"],
            "synthetic_calibration_example_not_formal",
        )
        self.assertIsNone(self.payload["formal_threshold_ppm"])
        self.assertFalse(self.payload["formal_threshold_approved"])
        self.assertFalse(self.payload["real_h1_audited"])
        self.assertFalse(self.payload["raw_text_retained"])

    def test_all_pairs_are_scored_and_raw_text_is_not_retained(self) -> None:
        self.assertEqual(self.payload["subject_count"], 5)
        self.assertEqual(self.payload["pair_count"], 10)
        self.assertEqual(len(self.payload["pairs"]), 10)
        self.assertNotIn("requirement_text", self.receipt.canonical_json)
        for pair in self.payload["pairs"]:
            self.assertGreaterEqual(pair["combined_score_ppm"], 0)
            self.assertLessEqual(pair["combined_score_ppm"], 1_000_000)

    def test_exact_duplicate_scores_above_near_and_unrelated(self) -> None:
        scores = {
            (row["left_subject_id"], row["right_subject_id"]): row["combined_score_ppm"]
            for row in self.payload["pairs"]
        }
        exact = scores[("synthetic-dup-a", "synthetic-dup-a-exact")]
        near = scores[("synthetic-dup-a", "synthetic-dup-b-near")]
        unrelated = scores[("synthetic-dup-a", "synthetic-dup-d-unrelated")]
        self.assertGreater(exact, near)
        self.assertGreater(near, unrelated)

    def test_synthetic_threshold_replays_confusion_counts(self) -> None:
        threshold = self.payload["synthetic_threshold_ppm"]
        counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for row in self.payload["pairs"]:
            positive = row["label"] in {"exact_duplicate", "near_duplicate"}
            predicted = row["combined_score_ppm"] >= threshold
            key = "tp" if positive and predicted else "fn" if positive else "fp" if predicted else "tn"
            counts[key] += 1
        self.assertEqual(counts, self.payload["confusion"])

    def test_calibration_is_deterministic(self) -> None:
        second = build_synthetic_duplicate_calibration(self.fixture)
        self.assertEqual(second.canonical_json_bytes(), self.receipt.canonical_json_bytes())
        self.assertEqual(second.sha256_digest, self.receipt.sha256_digest)
        self.assertEqual(
            self.receipt.sha256_digest,
            "a94adbd22dfb995e06aad241252595860e790fe46c53902ddf3fa20ddd3535f7",
        )

    def test_calibration_receipt_replays_from_canonical_json(self) -> None:
        replayed = SyntheticCalibrationReceipt.from_json_bytes(
            self.receipt.canonical_json_bytes()
        )
        self.assertEqual(replayed, self.receipt)
        replayed.validate()
        replayed.validate_against_fixture(self.fixture)

        drifted_fixture = json.loads(json.dumps(self.fixture))
        drifted_fixture["subjects"][0]["requirement_text"] += " drift"
        with self.assertRaisesRegex(ValueError, "does not match"):
            replayed.validate_against_fixture(drifted_fixture)

    def test_calibration_receipt_rejects_noncanonical_json_bytes(self) -> None:
        noncanonical = json.dumps(self.payload, indent=2).encode()
        with self.assertRaisesRegex(ValueError, "not canonical"):
            SyntheticCalibrationReceipt.from_json_bytes(noncanonical)

    def test_calibration_receipt_rejects_authority_and_replay_tampering(self) -> None:
        mutations = {
            "schema": ("schema_version", "drifted"),
            "status": ("status", "formal"),
            "method": ("method", "drifted"),
            "fixture_hash": ("fixture_sha256", "0" * 64),
            "subject_count": ("subject_count", 6),
            "pair_count": ("pair_count", 9),
            "threshold": (
                "synthetic_threshold_ppm",
                self.payload["synthetic_threshold_ppm"] + 1,
            ),
            "formal_threshold": ("formal_threshold_ppm", 500_000),
            "formal_approval": ("formal_threshold_approved", True),
            "receipt_id": ("receipt_id", "phase5-synthetic-calibration-drifted"),
        }
        for name, (key, replacement) in mutations.items():
            with self.subTest(name=name):
                tampered = json.loads(json.dumps(self.payload))
                tampered[key] = replacement
                with self.assertRaises(ValueError):
                    SyntheticCalibrationReceipt.from_dict(tampered)

        pair_tampered = json.loads(json.dumps(self.payload))
        pair_tampered["pairs"][0]["combined_score_ppm"] -= 1
        with self.assertRaisesRegex(ValueError, "combined score replay"):
            SyntheticCalibrationReceipt.from_dict(pair_tampered)

        order_tampered = json.loads(json.dumps(self.payload))
        order_tampered["pairs"][0], order_tampered["pairs"][1] = (
            order_tampered["pairs"][1],
            order_tampered["pairs"][0],
        )
        with self.assertRaisesRegex(ValueError, "canonically ordered"):
            SyntheticCalibrationReceipt.from_dict(order_tampered)

        confusion_tampered = json.loads(json.dumps(self.payload))
        confusion_tampered["confusion"]["tp"] += 1
        with self.assertRaisesRegex(ValueError, "confusion replay"):
            SyntheticCalibrationReceipt.from_dict(confusion_tampered)

    def test_fixture_scope_and_pair_coverage_fail_closed(self) -> None:
        wrong = json.loads(json.dumps(self.fixture))
        wrong["fixture_kind"] = "real_h1"
        with self.assertRaises(ValueError):
            build_synthetic_duplicate_calibration(wrong)
        incomplete = json.loads(json.dumps(self.fixture))
        incomplete["pair_labels"] = incomplete["pair_labels"][:-1]
        with self.assertRaisesRegex(ValueError, "every canonical pair"):
            build_synthetic_duplicate_calibration(incomplete)

    def test_opaque_duplicate_adjudication_retains_only_hashes(self) -> None:
        left = "1" * 64
        right = "2" * 64
        record = create_opaque_duplicate_adjudication(
            left_case_commitment_sha256=left,
            right_case_commitment_sha256=right,
            verdict="near_duplicate",
            reason_code="synthetic_manual_review",
            adjudicator_commitment_sha256="3" * 64,
        )
        self.assertFalse(record["case_content_visible"])
        self.assertFalse(record["candidate_output_visible"])
        self.assertEqual(record["left_case_commitment_sha256"], left)
        self.assertNotIn("requirement", json.dumps(record))
        self.assertEqual(validate_opaque_duplicate_adjudication(record), record)

    def test_opaque_duplicate_adjudication_tampering_fails_closed(self) -> None:
        record = create_opaque_duplicate_adjudication(
            left_case_commitment_sha256="1" * 64,
            right_case_commitment_sha256="2" * 64,
            verdict="near_duplicate",
            reason_code="synthetic_manual_review",
            adjudicator_commitment_sha256="3" * 64,
        )
        for key, replacement in (
            ("schema_version", "drifted"),
            ("reason_code", "raw requirement text"),
            ("case_content_visible", True),
            ("decision_id", "phase5-opaque-adjudication-drifted"),
        ):
            with self.subTest(key=key):
                tampered = dict(record)
                tampered[key] = replacement
                with self.assertRaises(ValueError):
                    validate_opaque_duplicate_adjudication(tampered)

    def test_annotation_commitment_does_not_retain_bytes(self) -> None:
        raw = b'{"synthetic":"annotation"}'
        record = create_synthetic_annotation_commitment(
            case_commitment_sha256="4" * 64,
            annotator_ref="synthetic-annotator-a",
            annotation_bytes=raw,
        )
        self.assertEqual(record["annotation_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(record["annotation_byte_length"], len(raw))
        self.assertFalse(record["annotation_content_retained"])
        self.assertFalse(record["real_h1_or_gold"])
        self.assertNotIn(raw.decode(), json.dumps(record))
        self.assertEqual(validate_synthetic_annotation_commitment(record), record)

    def test_annotation_commitment_tampering_fails_closed(self) -> None:
        record = create_synthetic_annotation_commitment(
            case_commitment_sha256="4" * 64,
            annotator_ref="synthetic-annotator-a",
            annotation_bytes=b"annotation",
        )
        for key, replacement in (
            ("material_class", "real_h1"),
            ("annotator_ref", "real-annotator"),
            ("annotation_byte_length", 0),
            ("annotation_content_retained", True),
            ("commitment_id", "phase5-annotation-commitment-drifted"),
        ):
            with self.subTest(key=key):
                tampered = dict(record)
                tampered[key] = replacement
                with self.assertRaises(ValueError):
                    validate_synthetic_annotation_commitment(tampered)

    def test_two_annotation_commitments_can_form_hash_only_adjudication(self) -> None:
        first = create_synthetic_annotation_commitment(
            case_commitment_sha256="5" * 64,
            annotator_ref="synthetic-annotator-a",
            annotation_bytes=b"a",
        )
        second = create_synthetic_annotation_commitment(
            case_commitment_sha256="5" * 64,
            annotator_ref="synthetic-annotator-b",
            annotation_bytes=b"b",
        )
        ordered = sorted((first, second), key=lambda item: item["commitment_id"])
        receipt = create_synthetic_annotation_adjudication(
            first_commitment=ordered[0],
            second_commitment=ordered[1],
            adjudicator_commitment_sha256="6" * 64,
            agreement_status="disagree_adjudication_required",
        )
        self.assertFalse(receipt["annotation_content_visible"])
        self.assertFalse(receipt["real_h1_or_gold"])
        self.assertEqual(receipt["annotation_commitment_ids"], [item["commitment_id"] for item in ordered])
        self.assertEqual(validate_synthetic_annotation_adjudication(receipt), receipt)

    def test_annotation_adjudication_tampering_fails_closed(self) -> None:
        first = create_synthetic_annotation_commitment(
            case_commitment_sha256="5" * 64,
            annotator_ref="synthetic-annotator-a",
            annotation_bytes=b"a",
        )
        second = create_synthetic_annotation_commitment(
            case_commitment_sha256="5" * 64,
            annotator_ref="synthetic-annotator-b",
            annotation_bytes=b"b",
        )
        ordered = sorted((first, second), key=lambda item: item["commitment_id"])
        receipt = create_synthetic_annotation_adjudication(
            first_commitment=ordered[0],
            second_commitment=ordered[1],
            adjudicator_commitment_sha256="6" * 64,
            agreement_status="agree",
        )
        for key, replacement in (
            (
                "annotation_commitment_ids",
                list(reversed(receipt["annotation_commitment_ids"])),
            ),
            ("agreement_status", "unknown"),
            ("annotation_content_visible", True),
            ("receipt_id", "phase5-annotation-adjudication-drifted"),
        ):
            with self.subTest(key=key):
                tampered = dict(receipt)
                tampered[key] = replacement
                with self.assertRaises(ValueError):
                    validate_synthetic_annotation_adjudication(tampered)

    def test_annotation_adjudication_requires_valid_distinct_annotators(self) -> None:
        first = create_synthetic_annotation_commitment(
            case_commitment_sha256="7" * 64,
            annotator_ref="synthetic-annotator-a",
            annotation_bytes=b"a",
        )
        second = create_synthetic_annotation_commitment(
            case_commitment_sha256="7" * 64,
            annotator_ref="synthetic-annotator-a",
            annotation_bytes=b"b",
        )
        ordered = sorted((first, second), key=lambda item: item["commitment_id"])
        with self.assertRaisesRegex(ValueError, "distinct annotators"):
            create_synthetic_annotation_adjudication(
                first_commitment=ordered[0],
                second_commitment=ordered[1],
                adjudicator_commitment_sha256="8" * 64,
                agreement_status="disagree_adjudication_required",
            )

        forged = dict(first)
        forged["annotation_byte_length"] = 0
        with self.assertRaisesRegex(ValueError, "annotation byte length"):
            create_synthetic_annotation_adjudication(
                first_commitment=forged,
                second_commitment=second,
                adjudicator_commitment_sha256="8" * 64,
                agreement_status="disagree_adjudication_required",
            )


if __name__ == "__main__":
    unittest.main()
