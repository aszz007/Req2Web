from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts/phase7_e2_diagnostic_v3_candidate.py"
    spec = importlib.util.spec_from_file_location("phase7_e2_diagnostic_v3_candidate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


candidate = load_module()
runtime_path = ROOT / "scripts/phase7_e2_diagnostic_v3_runtime.py"
runtime_spec = importlib.util.spec_from_file_location("phase7_e2_diagnostic_v3_runtime", runtime_path)
assert runtime_spec and runtime_spec.loader
runtime = importlib.util.module_from_spec(runtime_spec)
runtime_spec.loader.exec_module(runtime)


class Phase7E2DiagnosticV3CandidateTests(unittest.TestCase):
    def test_budget_forces_final_after_fifth_read(self) -> None:
        self.assertEqual(candidate.BUDGETS["reads"], 5)
        self.assertEqual(candidate.BUDGETS["provider_turns"], 6)
        with self.assertRaisesRegex(candidate.CandidateError, "final answer required"):
            candidate.validate_action(
                {"action": "read", "artifact": "runtime.json", "pointer": ""},
                inventory={"runtime.json"}, arm="A", reads_used=5, turn=6,
            )

    def test_origins_use_unambiguous_artifact_pointer_schema(self) -> None:
        action = {
            "action": "final",
            "answer": {
                "status": "fault",
                "origin_candidates": [{"artifact": "handoff.json", "pointer": "/links/0/use_case_id"}],
                "affected_use_cases": ["UC-01"],
                "evidence_edges": [],
                "uncertainty": "low",
            },
        }
        self.assertEqual(candidate.validate_action(action, inventory={"handoff.json"}, arm="A", reads_used=2, turn=3), action)
        ambiguous = json.loads(json.dumps(action))
        ambiguous["answer"]["origin_candidates"][0] = {"artifact": "handoff.json", "entity": "link-1", "field": "use_case_id"}
        with self.assertRaisesRegex(candidate.CandidateError, "artifact/pointer"):
            candidate.validate_action(ambiguous, inventory={"handoff.json"}, arm="A", reads_used=2, turn=3)

    def test_trace_index_is_b_only_and_lookup_only(self) -> None:
        with self.assertRaisesRegex(candidate.CandidateError, "not visible"):
            candidate.validate_action(
                {"action": "read", "artifact": "trace_index.json", "pointer": "/entities/UC-01"},
                inventory={"trace_index.json"}, arm="A", reads_used=1, turn=2,
            )
        with self.assertRaisesRegex(candidate.CandidateError, "exact entity"):
            candidate.validate_action(
                {"action": "read", "artifact": "trace_index.json", "pointer": ""},
                inventory={"trace_index.json"}, arm="B", reads_used=1, turn=2,
            )
        accepted = candidate.validate_action(
            {"action": "read", "artifact": "trace_index.json", "pointer": "/entities/UC-01"},
            inventory={"trace_index.json"}, arm="B", reads_used=1, turn=2,
        )
        self.assertEqual(accepted["pointer"], "/entities/UC-01")

    def test_index_contains_only_exact_source_occurrences(self) -> None:
        files = {
            "specification.json": {"components": [{"component_id": "component-1"}]},
            "handoff.json": {"links": [{"component_id": "component-1"}]},
            "render_bindings.json": {"bindings": []},
            "runtime.json": {"events": []},
            "requirements.json": {},
            "versions.json": {},
        }
        index = candidate.make_index(files)
        self.assertEqual(set(index), {"kind", "access", "entities", "meaning"})
        self.assertEqual(len(index["entities"]["component-1"]), 2)
        self.assertNotIn("fault", json.dumps(index).lower())
        self.assertNotIn("expected", json.dumps(index).lower())

    def test_runtime_adapter_installs_v3_without_replacing_model_backend(self) -> None:
        original_backend = runtime.legacy.LocalQwenBackend
        names = ("PUBLIC_SCHEMA", "RESULT_SCHEMA", "EXPECTED_BUDGETS", "COMMON_PROMPT", "PROTOCOL_REVISION", "validate_public_root", "_load_config", "_freeze_identity", "_validate_final", "_pointer")
        original = {name: getattr(runtime.legacy, name) for name in names}
        try:
            runtime.install_contract()
            self.assertEqual(runtime.legacy.PUBLIC_SCHEMA, candidate.PUBLIC_SCHEMA)
            self.assertEqual(runtime.legacy.RESULT_SCHEMA, runtime.RESULT_SCHEMA)
            self.assertEqual(runtime.legacy.EXPECTED_BUDGETS, candidate.BUDGETS)
            self.assertIs(runtime.legacy.LocalQwenBackend, original_backend)
        finally:
            for name, value in original.items():
                setattr(runtime.legacy, name, value)

    def test_runtime_rejects_whole_trace_index_read(self) -> None:
        index = {"kind": "experiment_only_source_occurrence_index_v3", "entities": {"UC-01": []}}
        with self.assertRaisesRegex(runtime.legacy.RuntimeErrorClosed, "exact"):
            runtime.resolve_pointer(index, "")
        self.assertEqual(runtime.resolve_pointer(index, "/entities/UC-01"), [])

    def test_scorer_accepts_exact_pointer_and_rejects_ambiguous_field(self) -> None:
        files = {"handoff.json": {"links": [{"use_case_id": "UC-99"}]}}
        gold = {
            "status": "fault",
            "origins": [{"artifact": "handoff.json", "pointer": "/links/0/use_case_id"}],
            "affected_use_cases": ["UC-01"],
            "evidence_groups": [],
        }
        answer = {
            "status": "fault",
            "origin_candidates": [{"artifact": "handoff.json", "pointer": "/links/0/use_case_id"}],
            "affected_use_cases": ["UC-01"],
            "evidence_edges": [],
            "uncertainty": "low",
        }
        self.assertTrue(candidate.score_answer(answer, gold, files)["complete_supported_diagnosis"])
        answer["origin_candidates"] = [{"artifact": "handoff.json", "pointer": "use_case_id"}]
        with self.assertRaisesRegex(candidate.CandidateError, "artifact/pointer"):
            candidate.score_answer(answer, gold, files)


if __name__ == "__main__":
    unittest.main()
