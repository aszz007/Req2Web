from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_orchestration.phase4_graph import Phase4ContractError, validate_b_input  # noqa: E402
from req2web_runtime.phase4_stability_cases import (  # noqa: E402
    CASE_COUNT,
    CASE_SET_ID,
    CASE_SET_SCHEMA_VERSION,
    canonical_case_bytes,
    canonical_case_set_bytes,
    canonical_case_set_record_bytes,
    case_set_identity,
    get_stability_case,
    get_stability_case_set,
    get_stability_cases,
    validate_stability_case,
    validate_stability_case_set,
)


class Phase4StabilityCasesTest(unittest.TestCase):
    def test_case_set_has_exact_count_order_and_device_coverage(self) -> None:
        case_set = get_stability_case_set()

        self.assertEqual(case_set["schema_version"], CASE_SET_SCHEMA_VERSION)
        self.assertEqual(case_set["case_set_id"], CASE_SET_ID)
        self.assertEqual(len(case_set["cases"]), CASE_COUNT)
        self.assertEqual(case_set["case_order"], [item["case_id"] for item in case_set["cases"]])
        self.assertEqual(
            {item["target_device"] for item in case_set["cases"]},
            {"mobile", "desktop", "tablet"},
        )
        self.assertEqual(
            len({item["case_id"] for item in case_set["cases"]}),
            CASE_COUNT,
        )
        self.assertEqual(
            len({item["request_id"] for item in case_set["cases"]}),
            CASE_COUNT,
        )

    def test_every_case_passes_existing_b_validator_and_commerce_shape(self) -> None:
        for case in get_stability_cases():
            validated = validate_b_input(case)
            self.assertIs(validated, case)
            self.assertEqual(case["task_type"], "ecommerce")
            self.assertGreaterEqual(len(case["use_cases"]), 2)
            self.assertLessEqual(len(case["use_cases"]), 4)
            text = " ".join(
                str(value)
                for value in (
                    case["requirement"],
                    case["requirement_summary"],
                    *case["constraints"],
                    *[
                        field
                        for use_case in case["use_cases"]
                        for field in use_case.values()
                    ],
                )
            ).casefold()
            self.assertIn("search", text)
            self.assertIn("cart", text)
            self.assertIn("checkout", text)
            self.assertIn("inline", text)

    def test_forbidden_fields_and_word_categories_are_rejected(self) -> None:
        case = get_stability_cases()[0]
        forbidden_field = copy.deepcopy(case)
        forbidden_field["evidence"] = "not allowed"
        with self.assertRaisesRegex(Phase4ContractError, "exact keys"):
            validate_stability_case(forbidden_field)

        forbidden_word = copy.deepcopy(case)
        forbidden_word["requirement"] += " Do not include reference material."
        with self.assertRaisesRegex(Phase4ContractError, "forbidden term"):
            validate_stability_case(forbidden_word)

    def test_case_set_validator_rejects_order_or_identity_drift(self) -> None:
        case_set = get_stability_case_set()
        case_set["case_order"] = list(reversed(case_set["case_order"]))
        with self.assertRaisesRegex(Phase4ContractError, "order"):
            validate_stability_case_set(case_set)

        case_set = get_stability_case_set()
        case_set["case_set_identity"] = "sha256:" + ("0" * 64)
        with self.assertRaisesRegex(Phase4ContractError, "identity"):
            validate_stability_case_set(case_set)

    def test_identity_and_canonical_bytes_are_stable(self) -> None:
        first = get_stability_case_set()
        second = get_stability_case_set()

        first_bytes = canonical_case_set_bytes(first)
        second_bytes = canonical_case_set_bytes(second)
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(
            "sha256:" + hashlib.sha256(first_bytes).hexdigest(),
            case_set_identity(first),
        )
        self.assertEqual(first["case_set_identity"], second["case_set_identity"])
        self.assertEqual(
            [canonical_case_bytes(item) for item in first["cases"]],
            [canonical_case_bytes(item) for item in second["cases"]],
        )
        self.assertEqual(
            validate_stability_case(json.loads(canonical_case_bytes(first["cases"][0]))),
            first["cases"][0],
        )
        self.assertEqual(
            validate_stability_case_set(
                json.loads(canonical_case_set_record_bytes(first))
            ),
            first,
        )

    def test_returns_are_defensive_deep_copies(self) -> None:
        first = get_stability_case_set()
        original_identity = first["case_set_identity"]
        first["cases"][0]["constraints"].append("mutated")
        first["cases"][0]["use_cases"][0]["goal"] = "mutated"
        first["case_order"].clear()

        second = get_stability_case_set()
        self.assertEqual(second["case_set_identity"], original_identity)
        self.assertEqual(len(second["cases"]), CASE_COUNT)
        self.assertNotIn("mutated", second["cases"][0]["constraints"])
        self.assertNotEqual(second["cases"][0]["use_cases"][0]["goal"], "mutated")
        self.assertEqual(len(second["case_order"]), CASE_COUNT)

        cases = get_stability_cases()
        cases[0]["use_cases"][0]["title"] = "mutated"
        self.assertNotEqual(
            get_stability_cases()[0]["use_cases"][0]["title"],
            "mutated",
        )

    def test_single_case_lookup_is_stable_and_unknown_id_fails(self) -> None:
        cases = get_stability_cases()
        selected = validate_stability_case(cases[3])
        self.assertEqual(selected["case_id"], cases[3]["case_id"])

        with self.assertRaises(KeyError):
            get_stability_case("p4-05-stability-99-unknown")


if __name__ == "__main__":
    unittest.main()
