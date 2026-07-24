from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.prepare_stage3_path3_external_action_gate import (  # noqa: E402
    DATASET_MEMBERSHIP,
    PREPARATION_STATUS,
    PURPOSE,
    _load_frozen_regression_cases,
    _load_json,
    _validate_captured_case_set_identity,
    _validated_cases,
    prepare,
    validate_output_against_tracked_record,
)


class Stage3Path3ExternalActionGatePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = ROOT / "fixtures" / "stage3_path3_compatibility_cases_v1.json"
        cls.tracked_record = (
            ROOT / "fixtures" / "stage3_path3_external_action_gate_preparation_v1.json"
        )
        cls.outputs_root = (ROOT / "outputs").resolve()
        cls.work = cls.outputs_root / (
            f"_m3_external_action_gate_preparation_tests_{uuid.uuid4().hex}"
        )
        cls.result = prepare(
            cls.fixture,
            cls.work,
            ROOT / "data" / "processed" / "rag",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if not cls.work.exists():
            return
        resolved = cls.work.resolve()
        if resolved.parent != cls.outputs_root:
            raise AssertionError("test cleanup target escaped the repository outputs directory")
        validate_output_against_tracked_record(resolved)
        shutil.rmtree(resolved)

    def test_preparation_is_exact_two_case_non_h1_no_action_chain(self) -> None:
        self.assertEqual(self.result["status"], PREPARATION_STATUS)
        self.assertEqual(self.result["purpose"], PURPOSE)
        self.assertEqual(self.result["dataset_membership"], DATASET_MEMBERSHIP)
        self.assertEqual(
            [item["case_id"] for item in self.result["cases"]],
            ["path3-commerce-checkout", "path3-media-analysis"],
        )
        self.assertEqual(
            [item["source_class"] for item in self.result["cases"]],
            ["synthetic", "synthetic"],
        )
        self.assertEqual(self.result["inherited_boundary"], {
            "execution_path": "path_3",
            "non_h1": True,
            "frozen_regression_cases_used": False,
            "h1_or_gold_accessed": False,
            "reference_only_assets_used": False,
            "third_party_payload_used": False,
        })
        self.assertTrue(self.result["gate_state"])
        self.assertTrue(all(value is False for value in self.result["gate_state"].values()))
        for case in self.result["cases"]:
            self.assertTrue(all(value is False for value in case["gate_state"].values()))
            self.assertEqual(case["exclusion_scan"]["status"], "not_included_in_audit_bytes")
            self.assertEqual(case["exclusion_scan"]["prohibited_category_count"], 18)
            self.assertEqual(case["same_case_frozen_g0_reference"]["inventory_file_count"], 12)
        self.assertTrue((self.work / "completion_marker.json").is_file())

    def test_generated_payload_free_record_matches_tracked_identity_record(self) -> None:
        validate_output_against_tracked_record(self.work)
        generated = (
            self.work / "external_action_gate_preparation_record.json"
        ).read_bytes()
        fixture_payload = _load_json(self.fixture)
        for case in fixture_payload["cases"]:
            self.assertNotIn(case["requirement"].encode("utf-8"), generated)
            for constraint in case["constraints"]:
                self.assertNotIn(constraint.encode("utf-8"), generated)

    def test_case_set_is_captured_and_disjoint_from_frozen_regression(self) -> None:
        frozen = _load_frozen_regression_cases(
            ROOT / "fixtures" / "demo_v2_regression_cases_v1.json"
        )
        payload = _load_json(self.fixture)
        _validate_captured_case_set_identity(payload)
        cases = _validated_cases(payload, frozen)
        frozen_ids = {item["case_id"] for item in frozen}
        self.assertTrue({item["case_id"] for item in cases}.isdisjoint(frozen_ids))

        renamed_copy = deepcopy(payload)
        source = next(item for item in frozen if item["case_id"] == "desktop-dashboard")
        renamed_copy["cases"][0]["case_id"] = "synthetic-labeled-copy"
        renamed_copy["cases"][0]["requirement"] = source["requirement"]
        renamed_copy["cases"][0]["constraints"] = list(source["constraints"])
        with self.assertRaisesRegex(ValueError, "copies the frozen regression set"):
            _validated_cases(renamed_copy, frozen)

    def test_captured_identity_and_semantic_text_fail_closed(self) -> None:
        payload = _load_json(self.fixture)
        drifted = deepcopy(payload)
        drifted["cases"][0]["requirement"] += " drift"
        with self.assertRaisesRegex(ValueError, "captured compatibility case set identity"):
            _validate_captured_case_set_identity(drifted)

        placeholder = deepcopy(payload)
        placeholder["cases"][0]["requirement"] = "????????"
        frozen = _load_frozen_regression_cases(
            ROOT / "fixtures" / "demo_v2_regression_cases_v1.json"
        )
        with self.assertRaisesRegex(ValueError, "semantic alphanumeric text"):
            _validated_cases(placeholder, frozen)


    def test_completion_marker_tamper_fails_closed(self) -> None:
        work = self.outputs_root / (
            f"_m3_external_action_gate_marker_tamper_{uuid.uuid4().hex}"
        )
        prepare(
            self.fixture,
            work,
            ROOT / "data" / "processed" / "rag",
        )
        marker_path = work / "completion_marker.json"
        original = marker_path.read_bytes()
        marker_path.write_bytes(original.replace(b"complete_no_action", b"complete_tampered", 1))
        with self.assertRaisesRegex(ValueError, "completion marker"):
            validate_output_against_tracked_record(work)
        marker_path.write_bytes(original)
        validate_output_against_tracked_record(work)
        if work.resolve().parent != self.outputs_root:
            raise AssertionError("tamper-test cleanup target escaped repository outputs")
        shutil.rmtree(work)

    def test_existing_output_root_is_never_overwritten(self) -> None:
        with self.assertRaisesRegex(ValueError, "already exists"):
            prepare(
                self.fixture,
                self.work,
                ROOT / "data" / "processed" / "rag",
            )


if __name__ == "__main__":
    unittest.main()