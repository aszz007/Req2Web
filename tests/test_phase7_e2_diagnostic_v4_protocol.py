from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/phase7_e2_diagnostic_v4_protocol.py"
SPEC = importlib.util.spec_from_file_location("phase7_e2_diagnostic_v4_protocol", MODULE_PATH)
assert SPEC and SPEC.loader
protocol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(protocol)


TRACE_INDEX = {
    "kind": protocol.TRACE_INDEX_KIND,
    "entities": {
        "UC-01": [{"artifact": "handoff.json", "pointer": "/links/0/use_case_id"}],
        "component/1": [{"artifact": "specification.json", "pointer": "/components/0/component_id"}],
    },
}
INVENTORY = {"readme.json", "handoff.json", "specification.json", "runtime.json", "trace_index.json"}


class Phase7E2DiagnosticV4ProtocolTests(unittest.TestCase):
    def test_self_check_preserves_v3_budgets_and_has_no_action_authority(self) -> None:
        result = protocol.self_check()
        self.assertEqual(result["status"], "validated_local_protocol_only")
        self.assertEqual(result["budgets"], protocol.v3.BUDGETS)
        self.assertFalse(result["model_loaded"])
        self.assertFalse(result["run_occurred"])
        self.assertFalse(result["measured_authorized"])

    def test_trace_lookup_is_b_only_grounded_and_exact(self) -> None:
        action = {"action": "trace_lookup", "entity_id": "component/1"}
        resolved = protocol.validate_action(
            action,
            arm="B",
            inventory=INVENTORY,
            observed_entities={"component/1"},
            trace_index=TRACE_INDEX,
            reads_used=3,
            turn=4,
        )
        self.assertEqual(
            resolved["read"],
            {"action": "read", "artifact": "trace_index.json", "pointer": "/entities/component~11"},
        )
        self.assertEqual(resolved["model_action"], action)
        with self.assertRaisesRegex(protocol.ProtocolV4Error, "only to arm B"):
            protocol.trace_lookup_to_read(
                action,
                arm="A",
                observed_entities={"component/1"},
                trace_index=TRACE_INDEX,
                reads_used=3,
                turn=4,
            )
        with self.assertRaisesRegex(protocol.ProtocolV4Error, "not grounded"):
            protocol.trace_lookup_to_read(
                action,
                arm="B",
                observed_entities=set(),
                trace_index=TRACE_INDEX,
                reads_used=3,
                turn=4,
            )

    def test_grounding_uses_values_seen_in_a_prior_raw_read(self) -> None:
        value = {"use_case_id": "UC-01", "nested": [{"component_id": "component/1"}], "noise": "UC-99"}
        self.assertEqual(protocol.grounded_entities(value, TRACE_INDEX), {"UC-01", "component/1"})

    def test_direct_trace_index_reads_are_forbidden(self) -> None:
        with self.assertRaisesRegex(protocol.ProtocolV4Error, "direct trace-index"):
            protocol.validate_action(
                {"action": "read", "artifact": "trace_index.json", "pointer": ""},
                arm="B",
                inventory=INVENTORY,
                observed_entities={"UC-01"},
                trace_index=TRACE_INDEX,
                reads_used=2,
                turn=3,
            )

    def test_flat_final_translates_to_the_unchanged_scoring_shape(self) -> None:
        action = {
            "action": "final",
            "status": "fault",
            "origins": ["handoff.json::/links/0/use_case_id"],
            "use_cases": ["UC-01"],
            "edges": [
                "handoff.json::/links/0/use_case_id=>specification.json::/sections/0/use_case_ids"
            ],
            "uncertainty": "low",
        }
        expected = {
            "status": "fault",
            "origin_candidates": [{"artifact": "handoff.json", "pointer": "/links/0/use_case_id"}],
            "affected_use_cases": ["UC-01"],
            "evidence_edges": [
                {
                    "from": {"artifact": "handoff.json", "pointer": "/links/0/use_case_id"},
                    "to": {"artifact": "specification.json", "pointer": "/sections/0/use_case_ids"},
                }
            ],
            "uncertainty": "low",
        }
        for arm in ("A", "B"):
            result = protocol.validate_action(
                action,
                arm=arm,
                inventory=INVENTORY,
                observed_entities=set(),
                trace_index=TRACE_INDEX,
                reads_used=5,
                turn=6,
            )
            self.assertEqual(result, {"kind": "final", "answer": expected})

    def test_flat_final_rejects_bad_delimiters_and_index_citations(self) -> None:
        base = {
            "action": "final",
            "status": "fault",
            "origins": ["handoff.json::/links/0/use_case_id"],
            "use_cases": ["UC-01"],
            "edges": [],
            "uncertainty": "low",
        }
        invalid = dict(base)
        invalid["origins"] = ["handoff.json#/links/0/use_case_id"]
        with self.assertRaisesRegex(protocol.ProtocolV4Error, "endpoint"):
            protocol.final_to_canonical(invalid, inventory=INVENTORY)
        invalid = dict(base)
        invalid["origins"] = ["trace_index.json::/entities/UC-01"]
        with self.assertRaisesRegex(protocol.ProtocolV4Error, "raw artifact"):
            protocol.final_to_canonical(invalid, inventory=INVENTORY)

    def test_malformed_or_duplicate_key_json_is_not_repaired(self) -> None:
        malformed = '{"action":"final","status":"fault","origins":[],"use_cases":[],"edges":[{"bad":1}],"uncertainty":"low"'
        with self.assertRaisesRegex(protocol.ProtocolV4Error, "strict JSON"):
            protocol.parse_and_validate(
                malformed,
                arm="A",
                inventory=INVENTORY,
                observed_entities=set(),
                trace_index=TRACE_INDEX,
                reads_used=5,
                turn=6,
            )
        duplicate = json.dumps({"action": "read", "artifact": "readme.json", "pointer": ""})
        duplicate = duplicate[:-1] + ',"artifact":"runtime.json"}'
        with self.assertRaisesRegex(protocol.ProtocolV4Error, "duplicate"):
            protocol.parse_and_validate(
                duplicate,
                arm="A",
                inventory=INVENTORY,
                observed_entities=set(),
                trace_index=TRACE_INDEX,
                reads_used=0,
                turn=1,
            )
        with self.assertRaisesRegex(protocol.ProtocolV4Error, "UTF-8"):
            protocol.parse_and_validate(
                b"\xff",
                arm="A",
                inventory=INVENTORY,
                observed_entities=set(),
                trace_index=TRACE_INDEX,
                reads_used=0,
                turn=1,
            )


if __name__ == "__main__":
    unittest.main()
