from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_evaluation.phase5_publication_case_drafts import (  # noqa: E402
    SCHEMA_VERSION,
    build_phase5_publication_case_drafts_from_json_bytes,
    validate_phase5_publication_case_drafts,
)
from req2web_evaluation.phase5_publication_case_templates import (  # noqa: E402
    build_phase5_publication_case_templates_from_json_bytes,
    validate_phase5_publication_intervention_freeze,
)


TEMPLATES = ROOT / "fixtures" / "phase5_publication_case_templates_v1.json"
FREEZE = ROOT / "fixtures" / "phase5_publication_intervention_freeze_v1.json"
DRAFTS = ROOT / "fixtures" / "phase5_publication_case_drafts_v1.json"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _rebind_draft_id(value: dict[str, object]) -> None:
    body = {key: item for key, item in value.items() if key != "draft_id"}
    value["draft_id"] = (
        "phase5-publication-case-drafts-"
        + hashlib.sha256(_canonical(body)).hexdigest()
    )


class Phase5PublicationCaseDraftsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.templates = build_phase5_publication_case_templates_from_json_bytes(
            TEMPLATES.read_bytes()
        )
        self.freeze = validate_phase5_publication_intervention_freeze(
            json.loads(FREEZE.read_text(encoding="utf-8")),
            self.templates,
        )
        self.drafts = build_phase5_publication_case_drafts_from_json_bytes(
            DRAFTS.read_bytes(),
            self.templates,
            self.freeze,
        )

    def test_identity_counts_and_action_boundary(self) -> None:
        self.assertEqual(self.drafts["schema_version"], SCHEMA_VERSION)
        self.assertEqual(
            self.drafts["draft_id"],
            "phase5-publication-case-drafts-"
            "54b6d18b568f64683648eb3031692b160ce44952b35b1f19a468de04285c65a0",
        )
        self.assertEqual(self.drafts["core_case_count"], 4)
        self.assertEqual(self.drafts["reserve_case_count"], 0)
        self.assertTrue(
            all(value is False for value in self.drafts["action_state"].values())
        )

    def test_cases_are_complete_english_drafts_in_template_order(self) -> None:
        cases = self.drafts["cases"]
        self.assertEqual(
            [case["template_id"] for case in cases],
            [
                template["template_id"]
                for template in self.templates["templates"]
                if template["slot_kind"] == "core"
            ],
        )
        self.assertTrue(all(len(case["use_cases"]) >= 2 for case in cases))
        self.assertTrue(all(len(case["constraints"]) >= 2 for case in cases))
        self.assertTrue(
            all(case["content_status"] == "complete_draft_not_sealed" for case in cases)
        )
        self.assertTrue(all(case["real_h1_or_gold"] is False for case in cases))

    def test_intervention_bindings_match_the_consensus_freeze(self) -> None:
        core_rows = [
            row for row in self.freeze["rows"] if row["slot_kind"] == "core"
        ]
        for case, row in zip(self.drafts["cases"], core_rows, strict=True):
            self.assertEqual(
                case["intervention_binding"]["critical_role_id"],
                row["critical_role_id"],
            )
            self.assertEqual(
                case["intervention_binding"]["irrelevant_evidence_sha256"],
                row["irrelevant_evidence_sha256"],
            )

    def test_no_gold_or_model_output_fields_are_present(self) -> None:
        text = _canonical(self.drafts).decode("utf-8")
        self.assertNotIn("gold_label", text)
        self.assertNotIn("expected_page_spec", text)
        self.assertNotIn("provider_raw_response", text)
        self.assertNotIn("real_h1_content", text)

    def test_text_binding_and_action_tampering_fail_closed(self) -> None:
        cjk = copy.deepcopy(self.drafts)
        cjk["cases"][0]["requirement_text"] = "\u4e2d\u6587"
        _rebind_draft_id(cjk)
        with self.assertRaisesRegex(ValueError, "English-only"):
            validate_phase5_publication_case_drafts(
                cjk,
                self.templates,
                self.freeze,
            )

        path = copy.deepcopy(self.drafts)
        path["cases"][0]["constraints"][0] = "Read D:\\private\\case.txt."
        _rebind_draft_id(path)
        with self.assertRaisesRegex(ValueError, "URI or source path"):
            validate_phase5_publication_case_drafts(
                path,
                self.templates,
                self.freeze,
            )

        binding = copy.deepcopy(self.drafts)
        binding["cases"][0]["intervention_binding"][
            "critical_role_id"
        ] = "implementation"
        _rebind_draft_id(binding)
        with self.assertRaisesRegex(ValueError, "critical role drifted"):
            validate_phase5_publication_case_drafts(
                binding,
                self.templates,
                self.freeze,
            )

        opened = copy.deepcopy(self.drafts)
        opened["action_state"]["model_action_authorized"] = True
        _rebind_draft_id(opened)
        with self.assertRaisesRegex(ValueError, "must remain false"):
            validate_phase5_publication_case_drafts(
                opened,
                self.templates,
                self.freeze,
            )


if __name__ == "__main__":
    unittest.main()
