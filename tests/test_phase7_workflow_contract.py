"""Executable workflow contract checks; no model or historical result rewrite."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from req2web_agent.understanding import _build_use_cases, _keyword_present
from req2web_agent.prompt_authority import (
    build_canonical_f3_interaction_plan, build_canonical_f1_f4_prompt,
    prompt_authority_manifest,
)
from req2web_orchestration.phase4_graph import _validate_f1, _validate_f3, Phase4ContractError
from req2web_generation import DeterministicPageRenderer, MinimalConsistencyChecker
from req2web_generation.schema import ComponentSpec, PageState, InteractionSpec
from tests.test_page_renderer import build_spec


def state_fixture():
    components = [
        {"local_id": "go", "entity_type": "component", "component_type": "primary_action", "section_local_id": "section", "label": "Go", "purpose": "Advance the workflow", "refs": []},
        {"local_id": "back", "entity_type": "component", "component_type": "primary_action", "section_local_id": "section", "label": "Back", "purpose": "Return to the initial step", "refs": []},
        {"local_id": "status", "entity_type": "component", "component_type": "status_panel", "section_local_id": "section", "label": "Status", "purpose": "Display current workflow status", "refs": []},
    ]
    f1 = {"page_title": "Workflow", "layout_pattern": "single_column", "sections": [{"local_id": "section", "entity_type": "section", "title": "Workflow", "purpose": "Complete the workflow", "component_local_ids": [c["local_id"] for c in components], "refs": []}], "components": components}
    states = [{"local_id": "ready", "visible_component_local_ids": ["go", "status"]}, {"local_id": "review", "visible_component_local_ids": ["back", "status"]}, {"local_id": "done", "visible_component_local_ids": ["status"]}]
    authority = {"node_results": {"F1": {"payload": {"node_output": f1}}, "F2": {"payload": {"node_output": {"states": states}}}}}
    return f1, states, authority


class WorkflowContractTests(unittest.TestCase):
    def test_ordered_is_not_checkout_but_order_is(self):
        titles = [u.title for u in _build_use_cases("A parcel moves through an ordered workflow.")]
        self.assertNotIn("Manage the cart and complete checkout", titles)
        self.assertTrue(_keyword_present("submit an order", "order"))
        self.assertFalse(_keyword_present("border and ordered", "order"))

    def test_exact_component_capability_validation(self):
        f1, _, _ = state_fixture()
        _validate_f1(f1)
        f1["components"][0]["component_type"] = "button"
        with self.assertRaisesRegex(Phase4ContractError, "executable renderer"):
            _validate_f1(f1)

    def test_plan_allows_return_and_branch_without_display_triggers(self):
        f1, states, _ = state_fixture()
        plan = build_canonical_f3_interaction_plan(f1_registered_structure_view=f1, f2_registered_state_visibility_view={"states": states})
        self.assertEqual(plan[1]["allowed_target_state_local_ids"], ["ready", "review", "done"])
        self.assertEqual(plan[1]["allowed_trigger_component_local_ids"], ["back"])
        self.assertEqual(plan[2]["allowed_trigger_component_local_ids"], [])
        self.assertTrue(all(r["mandatory_interaction_count"] == 0 for r in plan))

    def test_node_accepts_backward_edge_and_rejects_invisible_or_display_trigger(self):
        _, _, state = state_fixture()
        edge = {"local_id": "return", "entity_type": "interaction", "trigger_component_local_id": "back", "source_state_local_id": "review", "action": "Return to ready", "target_state_local_id": "ready", "user_feedback": "Ready", "refs": []}
        _validate_f3({"interactions": [edge]}, state)
        for trigger in ("go", "status"):
            with self.subTest(trigger=trigger), self.assertRaises(Phase4ContractError):
                _validate_f3({"interactions": [{**edge, "trigger_component_local_id": trigger}]}, state)
        with self.assertRaisesRegex(Phase4ContractError, "ambiguous"):
            _validate_f3({"interactions": [edge, {**edge, "local_id": "duplicate"}]}, state)

    def test_prompt_has_no_linear_or_terminal_self_loop_requirement(self):
        manifest = prompt_authority_manifest()
        invariants = manifest["output_contracts"]["F3"]["invariants"]
        self.assertFalse(any("never skip a state" in item for item in invariants))
        self.assertIn("terminal state may have zero", " ".join(invariants))
        self.assertIn("component_type_enum", manifest["output_contracts"]["F1"])

    def test_unsupported_rendering_is_not_success(self):
        spec = build_spec("Create an offline approval workflow with review and reject actions.")
        spec.components[0].component_type = "unsupported_widget"
        output = ROOT / "outputs/phase7_workflow_contract" / uuid.uuid4().hex
        rendered = DeterministicPageRenderer().render(spec, output)
        report = MinimalConsistencyChecker().check(spec, rendered)
        failures = [c for c in report.checks if c.status == "fail"]
        self.assertTrue(any(c.check_id.startswith("error.unsupported-component:") for c in failures))


if __name__ == "__main__":
    unittest.main()
