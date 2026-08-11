from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
import sys

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.semantic_assist import (  # noqa: E402
    CLOSED_API_PROVIDER,
    HIGH_GPU_PROFILE,
    LOCAL_LOW_GPU_PROFILE,
    SemanticRequirementAssistError,
    SemanticRequirementAssistStore,
    build_semantic_assist_prompt,
    build_sidecar,
    provider_capabilities,
    semantic_assist_profile,
    validate_semantic_assist_raw,
)


def _canonical_b() -> dict[str, object]:
    return {
        "requirement_summary": (
            "Build a responsive fault-reporting page with validation and retry."
        ),
        "target_device": "responsive web",
        "task_type": "multi-state form",
        "constraints": ["Support keyboard operation"],
        "use_cases": [
            {
                "use_case_id": "UC-01",
                "title": "Report a fault",
                "actor": "Technician",
                "goal": "Submit a fault report",
                "expected_outcome": "See validation and a final confirmation",
            }
        ],
    }


def _raw() -> bytes:
    return json.dumps(
        {
            "advisory_items": [
                {
                    "advisory_kind": "missing_information",
                    "statement": "The requirement does not identify which fields are mandatory.",
                    "target_b_refs": [
                        {
                            "ref_type": "b_requirement",
                            "ref_id": "REQ-01",
                            "ref_revision": "canonical_b.requirement.v1",
                        },
                        {
                            "ref_type": "b_use_case",
                            "ref_id": "UC-01",
                            "ref_revision": "canonical_b.use_case.v1",
                        },
                    ],
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")


class SemanticRequirementAssistTests(unittest.TestCase):
    def test_profiles_keep_precision_and_completeness_separate(self) -> None:
        low = semantic_assist_profile(LOCAL_LOW_GPU_PROFILE).to_dict()
        high = semantic_assist_profile(HIGH_GPU_PROFILE).to_dict()
        self.assertEqual(low["quantization"], "nf4_double_quant")
        self.assertEqual(high["quantization"], "none")
        self.assertEqual(low["max_new_tokens"], 1_280)
        self.assertEqual(high["max_new_tokens"], 2_048)
        self.assertEqual(low["compute_dtype"], "bfloat16")
        self.assertEqual(high["compute_dtype"], "bfloat16")
        self.assertFalse(low["formal_quality_eligible"])
        self.assertTrue(high["formal_quality_eligible"])
        for profile in (low, high):
            self.assertFalse(profile["input_truncation"])
            self.assertFalse(profile["output_truncation"])
            self.assertTrue(profile["complete_json_required"])
            self.assertEqual(profile["automatic_retry_limit"], 0)
            self.assertFalse(profile["cpu_offload"])

    def test_closed_api_interface_is_reserved_without_network(self) -> None:
        providers = provider_capabilities()
        self.assertEqual(
            providers[CLOSED_API_PROVIDER]["status"], "reserved_not_connected"
        )
        self.assertFalse(
            providers[CLOSED_API_PROVIDER]["network_calls_implemented"]
        )
        self.assertFalse(providers[CLOSED_API_PROVIDER]["credentials_accepted"])

    def test_prompt_is_advisory_only_and_binds_canonical_b(self) -> None:
        prompt = build_semantic_assist_prompt(_canonical_b())
        self.assertEqual(prompt["node_id"], "B-Aux")
        self.assertEqual(prompt["canonical_b"], _canonical_b())
        self.assertIn(
            "Do not rewrite, replace, expand, or annotate canonical B in place.",
            prompt["instructions"],
        )
        self.assertEqual(
            prompt["output_contract"]["exact_top_level_keys"],
            ["advisory_items"],
        )
        self.assertEqual(prompt["output_contract"]["maximum_items"], 6)
        self.assertEqual(
            prompt["output_contract"]["statement_maximum_characters"], 600
        )

    def test_valid_raw_response_builds_available_sidecar(self) -> None:
        items = validate_semantic_assist_raw(
            _raw(), canonical_b_value=_canonical_b()
        )
        self.assertEqual(len(items), 1)
        sidecar = build_sidecar(
            canonical_b_value=_canonical_b(), call_count=1, raw=_raw()
        )
        self.assertEqual(sidecar["sidecar_status"], "advisory_available")
        self.assertEqual(sidecar["call_count"], 1)
        self.assertIsNone(sidecar["failure"])

    def test_malformed_or_unsupported_raw_fails_closed_without_writeback(self) -> None:
        with self.assertRaisesRegex(
            SemanticRequirementAssistError, "unsupported top-level"
        ):
            validate_semantic_assist_raw(
                b'{"advisory_items":[],"rewrite":{"requirement":"changed"}}',
                canonical_b_value=_canonical_b(),
            )
        sidecar = build_sidecar(
            canonical_b_value=_canonical_b(),
            call_count=1,
            raw=b"not json",
        )
        self.assertEqual(sidecar["sidecar_status"], "advisory_unavailable")
        self.assertEqual(sidecar["advisory_items"], [])
        self.assertEqual(sidecar["failure"]["failure_code"], "advisory_unavailable")

    def test_refs_must_be_known_sorted_and_unique(self) -> None:
        value = json.loads(_raw().decode("utf-8"))
        value["advisory_items"][0]["target_b_refs"].reverse()
        with self.assertRaisesRegex(SemanticRequirementAssistError, "order"):
            validate_semantic_assist_raw(
                json.dumps(value).encode("utf-8"),
                canonical_b_value=_canonical_b(),
            )
        value = json.loads(_raw().decode("utf-8"))
        value["advisory_items"][0]["target_b_refs"] = [
            {
                "ref_type": "f1_local",
                "ref_id": "illegal",
                "ref_revision": "illegal.v1",
            }
        ]
        with self.assertRaisesRegex(SemanticRequirementAssistError, "outside"):
            validate_semantic_assist_raw(
                json.dumps(value).encode("utf-8"),
                canonical_b_value=_canonical_b(),
            )

    def test_store_requires_marker_and_reports_nonblocking_capability(self) -> None:
        root = ROOT / f".semantic-assist-test-{uuid.uuid4().hex}"
        try:
            root.mkdir()
            model = root / "model"
            model.mkdir()
            evidence = root / "integrity.json"
            evidence.write_text("{}", encoding="utf-8")
            store = SemanticRequirementAssistStore(
                root=root / "runs",
                model_root=model,
                integrity_evidence=evidence,
                profile_name=LOCAL_LOW_GPU_PROFILE,
            )
            capability = store.capability()
            self.assertTrue(capability["one_call_no_retry"])
            self.assertFalse(capability["canonical_b_writeback"])
            self.assertFalse(capability["f1_f4_input"])
            self.assertTrue((root / "runs" / ".req2web-semantic-assist-store.json").is_file())
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
