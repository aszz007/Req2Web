from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_agent import (  # noqa: E402
    PROMPT_AUTHORITY_IDENTITY,
    PROMPT_AUTHORITY_REVISION,
    PromptAuthorityError,
    build_canonical_f3_interaction_plan,
    build_canonical_f4_acceptance_target_plan,
    build_canonical_f1_f4_prompt,
    prompt_authority_manifest,
    validate_canonical_prompt,
)
from req2web_agent.prompt_authority import (  # noqa: E402
    _historical_v17_linear_interaction_plan,
)
from req2web_runtime import (  # noqa: E402
    phase4_remote_qwen_fresh_integrated as historical_runtime,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class Phase4PromptAuthorityTest(unittest.TestCase):
    def test_manifest_is_project_wide_and_self_identifying(self) -> None:
        manifest = prompt_authority_manifest()
        self.assertEqual(manifest["revision"], PROMPT_AUTHORITY_REVISION)
        self.assertEqual(
            manifest["authority_identity"],
            PROMPT_AUTHORITY_IDENTITY,
        )
        self.assertEqual(manifest["node_order"], ["F1", "F2", "F3", "F4"])
        self.assertFalse(
            manifest["stage_specific_prompt_semantics_allowed"]
        )
        self.assertFalse(
            manifest[
                "historical_phase_specific_revisions_are_runtime_sources"
            ]
        )
        instructions = "\n".join(manifest["instructions"])
        self.assertIn(
            "user-visible natural-language value in English only",
            instructions,
        )
        self.assertIn("Do not emit Han characters", instructions)
        f1_invariants = manifest["output_contracts"]["F1"]["invariants"]
        self.assertIn(
            "page_title and layout_pattern; every section title and purpose; "
            "and every component component_type, section_local_id, label, "
            "and purpose are non-empty canonical text",
            f1_invariants,
        )
        f2_instructions = "\n".join(
            manifest["node_specific_instructions"]["F2"]
        )
        f1_instructions = "\n".join(
            manifest["node_specific_instructions"]["F1"]
        )
        self.assertIn("Use primary_action", f1_instructions)
        self.assertEqual(
            manifest["output_contracts"]["F1"]["component_type_enum"],
            [
                "primary_action", "media_input", "search_input", "form",
                "data_view", "location_picker", "status_panel",
            ],
        )
        self.assertIn("required_f1_component_order", f2_instructions)
        self.assertIn("exact subsequence", f2_instructions)
        f4_invariants = manifest["output_contracts"]["F4"]["invariants"]
        self.assertIn(
            "canonical use-case order is output order, not workflow-time "
            "order; selected state positions need not be monotonic across "
            "different use cases",
            f4_invariants,
        )
        self.assertNotIn(
            "keep selected state positions monotonically non-decreasing "
            "across canonical use-case order",
            f4_invariants,
        )
        self.assertIn(
            "distinct internal microstate",
            "\n".join(manifest["node_specific_instructions"]["F2"]),
        )
        self.assertIn(
            "every explicitly required action",
            "\n".join(manifest["node_specific_instructions"]["F3"]),
        )
        self.assertEqual(
            manifest["revision"],
            "negative_action_full_context_section_visibility_english_v21",
        )
        self.assertIn(
            "filtering the complete components array from left to right",
            f1_instructions,
        )
        self.assertIn(
            "do not reinterpret that negative requirement as an enabled action",
            f1_instructions,
        )
        self.assertIn(
            "neutral workflow-actions or navigation section",
            f1_instructions,
        )
        self.assertIn(
            "emit no state-changing interaction",
            "\n".join(manifest["node_specific_instructions"]["F3"]),
        )

    def test_public_requirement_literals_are_projected_with_context(self) -> None:
        requirement = (
            "The initial state is 'Ready'. 'Prepare transfer' keeps Ready "
            "visible and enables 'Attempt'. 'Retry' is available only after "
            "'Attempt failed'."
        )
        input_bytes = _canonical(
            {
                "schema_version": "test.input.v1",
                "node_id": "F1",
                "projection": {
                    "canonical_b_requirement_view": {
                        "requirement": requirement,
                    }
                },
            }
        )
        value = validate_canonical_prompt(
            build_canonical_f1_f4_prompt(
                node_id="F1",
                input_bytes=input_bytes,
            ),
            node_id="F1",
            input_bytes=input_bytes,
        )
        self.assertEqual(
            [row["literal"] for row in value["public_requirement_literal_contexts"]],
            ["Ready", "Prepare transfer", "Attempt", "Retry", "Attempt failed"],
        )
        self.assertTrue(
            all(row["left_context"] or row["right_context"] for row in value["public_requirement_literal_contexts"])
        )
        instructions = "\n".join(value["instructions"])
        self.assertIn("one F1 component with one section owner", instructions)
        self.assertIn("loss-prevention checklist", instructions)
        self.assertIn("absent, disabled, unavailable", instructions)

    def test_negative_terminal_action_keeps_its_prohibiting_context(self) -> None:
        requirement = (
            "The terminal state is 'Completed'. In that terminal state, "
            "'Repeat' must be absent or disabled, or using it must leave one "
            "completion unchanged rather than restarting or duplicating it."
        )
        input_bytes = _canonical(
            {
                "schema_version": "test.input.v1",
                "node_id": "F1",
                "projection": {
                    "canonical_b_requirement_view": {
                        "requirement": requirement,
                    }
                },
            }
        )
        value = validate_canonical_prompt(
            build_canonical_f1_f4_prompt(
                node_id="F1",
                input_bytes=input_bytes,
            ),
            node_id="F1",
            input_bytes=input_bytes,
        )
        repeat = next(
            row
            for row in value["public_requirement_literal_contexts"]
            if row["literal"] == "Repeat"
        )
        context = f"{repeat['left_context']} {repeat['right_context']}"
        self.assertIn("absent or disabled", context)
        self.assertIn("unchanged rather than restarting", context)

    def test_f2_prompt_exposes_one_required_f1_component_order(self) -> None:
        input_bytes = _canonical(
            {
                "schema_version": "test.input.v1",
                "node_id": "F2",
                "projection": {
                    "f1_registered_structure_view": {
                        "components": [
                            {"local_id": "search_input"},
                            {"local_id": "cart_items"},
                            {"local_id": "cart_total"},
                            {"local_id": "delivery_form"},
                            {"local_id": "submit_order"},
                        ],
                    },
                },
            }
        )
        raw = build_canonical_f1_f4_prompt(
            node_id="F2",
            input_bytes=input_bytes,
        )
        value = validate_canonical_prompt(
            raw,
            node_id="F2",
            input_bytes=input_bytes,
        )
        self.assertEqual(
            value["required_f1_component_order"],
            [
                "search_input",
                "cart_items",
                "cart_total",
                "delivery_form",
                "submit_order",
            ],
        )
        self.assertEqual(
            value["required_f1_component_positions"],
            [
                {"position": 0, "component_local_id": "search_input"},
                {"position": 1, "component_local_id": "cart_items"},
                {"position": 2, "component_local_id": "cart_total"},
                {"position": 3, "component_local_id": "delivery_form"},
                {"position": 4, "component_local_id": "submit_order"},
            ],
        )
        self.assertEqual(
            value["required_f1_component_order_literal"],
            (
                "0:search_input < 1:cart_items < 2:cart_total < "
                "3:delivery_form < 4:submit_order"
            ),
        )
        instructions = "\n".join(value["instructions"])
        self.assertIn(
            "scanning required_f1_component_order from left to right",
            instructions,
        )
        self.assertIn(
            "Never regroup components by section",
            instructions,
        )
        self.assertIn("actual initial workflow state first", instructions)
        self.assertIn("including distinct branches, errors and recovery", instructions)
        self.assertIn(
            "position numbers in each emitted visible_component_local_ids "
            "array must be strictly increasing",
            instructions,
        )
        self.assertIn(
            "never move a feedback component before an earlier submit",
            instructions,
        )
        phase5_input_bytes = _canonical(
            {
                "schema_version": "test.phase5.input.v1",
                "node_id": "F2",
                "same_run_validated_upstream_projection": {
                    "f1_registered_structure_view": {
                        "components": [
                            {"local_id": "search_input"},
                            {"local_id": "cart_items"},
                            {"local_id": "cart_total"},
                            {"local_id": "delivery_form"},
                            {"local_id": "submit_order"},
                        ],
                    },
                },
            }
        )
        phase5_value = validate_canonical_prompt(
            build_canonical_f1_f4_prompt(
                node_id="F2",
                input_bytes=phase5_input_bytes,
            ),
            node_id="F2",
            input_bytes=phase5_input_bytes,
        )
        self.assertEqual(
            phase5_value["required_f1_component_order"],
            value["required_f1_component_order"],
        )

    def test_f3_prompt_requires_recovery_to_leave_error_state(self) -> None:
        input_bytes = _canonical(
            {
                "schema_version": "test.input.v1",
                "node_id": "F3",
                "projection": {"test": True},
            }
        )
        value = validate_canonical_prompt(
            build_canonical_f1_f4_prompt(
                node_id="F3",
                input_bytes=input_bytes,
                required_interaction_plan=[
                    {
                        "plan_index": 0,
                        "transition_kind": "same_state_work",
                        "required_trigger_component_local_id": "search_input",
                    }
                ],
            ),
            node_id="F3",
            input_bytes=input_bytes,
            required_interaction_plan=[
                {
                    "plan_index": 0,
                    "transition_kind": "same_state_work",
                    "required_trigger_component_local_id": "search_input",
                }
            ],
        )
        instructions = "\n".join(value["instructions"])
        self.assertIn("transitions from the public requirement", instructions)
        self.assertIn("capability boundary only", instructions)
        self.assertIn("source/trigger pair must be unique", instructions)

    def test_shared_f3_plan_assigns_distinct_exact_triggers(self) -> None:
        plan = _historical_v17_linear_interaction_plan(
            f1_registered_structure_view={
                "components": [
                    {
                        "local_id": "checkout_form",
                        "component_type": "form",
                        "label": "Shipping and Payment Details",
                        "purpose": "Collect and validate checkout details.",
                    },
                    {
                        "local_id": "order_confirmation",
                        "component_type": "feedback_message",
                        "label": "Order Confirmed",
                        "purpose": "Display final confirmation feedback.",
                    },
                ]
            },
            f2_registered_state_visibility_view={
                "states": [
                    {
                        "local_id": "state-checkout",
                        "name": "Checkout",
                        "description": "Enter shipping and payment details.",
                        "visible_component_local_ids": [
                            "checkout_form",
                            "order_confirmation",
                        ],
                    },
                    {
                        "local_id": "state-error",
                        "name": "Validation Error",
                        "description": "Correct invalid checkout fields.",
                        "visible_component_local_ids": [
                            "checkout_form",
                            "order_confirmation",
                        ],
                    },
                    {
                        "local_id": "state-success",
                        "name": "Order Confirmed",
                        "description": "The order is complete.",
                        "visible_component_local_ids": [
                            "checkout_form",
                            "order_confirmation",
                        ],
                    },
                ]
            },
        )
        self.assertEqual(
            [
                row["required_trigger_component_local_id"]
                for row in plan[:4]
            ],
            [
                "order_confirmation",
                "checkout_form",
                "order_confirmation",
                "checkout_form",
            ],
        )
        for index in (0, 2):
            self.assertNotEqual(
                plan[index]["required_trigger_component_local_id"],
                plan[index + 1]["required_trigger_component_local_id"],
            )
            self.assertEqual(
                plan[index]["allowed_trigger_component_local_ids"],
                [plan[index]["required_trigger_component_local_id"]],
            )
            self.assertEqual(
                plan[index + 1]["allowed_trigger_component_local_ids"],
                [plan[index + 1]["required_trigger_component_local_id"]],
            )

    def test_shared_f3_plan_uses_one_forward_row_for_one_visible_non_final_state(
        self,
    ) -> None:
        plan = _historical_v17_linear_interaction_plan(
            f1_registered_structure_view={
                "components": [
                    {
                        "local_id": "advance_action",
                        "component_type": "action_button",
                        "label": "Continue",
                        "purpose": "Advance to the result state.",
                    },
                    {
                        "local_id": "result_summary",
                        "component_type": "summary",
                        "label": "Result Summary",
                        "purpose": "Show the completed result.",
                    },
                ]
            },
            f2_registered_state_visibility_view={
                "states": [
                    {
                        "local_id": "state-working",
                        "name": "Working",
                        "description": "Continue the current work.",
                        "visible_component_local_ids": ["advance_action"],
                    },
                    {
                        "local_id": "state-result",
                        "name": "Result",
                        "description": "Review the completed result.",
                        "visible_component_local_ids": ["result_summary"],
                    },
                ]
            },
        )

        self.assertEqual(
            [
                (
                    row["transition_kind"],
                    row["source_state_local_id"],
                    row["target_state_local_id"],
                    row["required_trigger_component_local_id"],
                )
                for row in plan
            ],
            [
                (
                    "forward_transition",
                    "state-working",
                    "state-result",
                    "advance_action",
                ),
                (
                    "same_state_work",
                    "state-result",
                    "state-result",
                    "result_summary",
                ),
            ],
        )
        self.assertEqual(
            len(
                {
                    (
                        row["source_state_local_id"],
                        row["required_trigger_component_local_id"],
                    )
                    for row in plan
                }
            ),
            len(plan),
        )

    def test_shared_f3_plan_still_rejects_zero_visible_components(self) -> None:
        with self.assertRaisesRegex(
            PromptAuthorityError,
            "visibility is invalid",
        ):
            _historical_v17_linear_interaction_plan(
                f1_registered_structure_view={
                    "components": [
                        {
                            "local_id": "advance_action",
                            "component_type": "action_button",
                            "label": "Continue",
                            "purpose": "Advance the workflow.",
                        }
                    ]
                },
                f2_registered_state_visibility_view={
                    "states": [
                        {
                            "local_id": "state-empty",
                            "name": "Empty",
                            "description": "No visible controls are available.",
                            "visible_component_local_ids": [],
                        }
                    ]
                },
            )

    def test_shared_f3_plan_does_not_treat_active_validation_as_error(
        self,
    ) -> None:
        plan = _historical_v17_linear_interaction_plan(
            f1_registered_structure_view={
                "components": [
                    {
                        "local_id": "search_input",
                        "component_type": "input",
                        "label": "Search",
                        "purpose": "Search available items.",
                    },
                    {
                        "local_id": "proceed_button",
                        "component_type": "action_button",
                        "label": "Proceed to Checkout",
                        "purpose": "Advance from cart review to checkout.",
                    },
                    {
                        "local_id": "checkout_form",
                        "component_type": "form",
                        "label": "Checkout Details",
                        "purpose": "Collect shipping and payment details.",
                    },
                    {
                        "local_id": "submit_button",
                        "component_type": "action_button",
                        "label": "Submit Order",
                        "purpose": "Submit the final order.",
                    },
                ]
            },
            f2_registered_state_visibility_view={
                "states": [
                    {
                        "local_id": "state-cart",
                        "name": "Cart Review",
                        "description": "Review selected items.",
                        "visible_component_local_ids": [
                            "search_input",
                            "proceed_button",
                        ],
                    },
                    {
                        "local_id": "state-checkout",
                        "name": "Complete Order",
                        "description": (
                            "Enter shipping and payment details while inline "
                            "validation is active."
                        ),
                        "visible_component_local_ids": [
                            "checkout_form",
                            "submit_button",
                        ],
                    },
                ]
            },
        )
        self.assertEqual(
            plan[1]["required_trigger_component_local_id"],
            "proceed_button",
        )
        self.assertEqual(
            plan[2]["required_trigger_component_local_id"],
            "checkout_form",
        )

    def test_f4_prompt_reconstructs_reference_contract_key_order(self) -> None:
        input_bytes = _canonical(
            {
                "schema_version": "test.input.v1",
                "node_id": "F4",
                "projection": {"test": True},
            }
        )
        plan = [
            {
                "position": 0,
                "use_case_ref": {
                    "ref_type": "canonical_b_use_case",
                    "ref_id": "UC-01",
                    "ref_revision": "canonical_b.use_case.v1",
                },
                "ordered_eligible_state_refs": [
                    {
                        "ref_type": "registry_stable",
                        "ref_id": "p4-f2-state-example",
                        "ref_revision": "req2web.phase4.registry.p4_02a.v1",
                    },
                    {
                        "ref_type": "registry_stable",
                        "ref_id": "p4-f2-state-recovery",
                        "ref_revision": "req2web.phase4.registry.p4_02a.v1",
                    },
                ],
            }
        ]
        raw = build_canonical_f1_f4_prompt(
            node_id="F4",
            input_bytes=input_bytes,
            required_acceptance_target_plan=plan,
        )
        value = validate_canonical_prompt(
            raw,
            node_id="F4",
            input_bytes=input_bytes,
            required_acceptance_target_plan=plan,
        )
        self.assertEqual(
            list(value["required_acceptance_target_plan"][0]["use_case_ref"]),
            ["ref_id", "ref_revision", "ref_type"],
        )
        self.assertEqual(
            list(
                value["required_acceptance_target_plan"][0][
                    "ordered_eligible_state_refs"
                ][0]
            ),
            ["ref_id", "ref_revision", "ref_type"],
        )
        instructions = "\n".join(value["instructions"])
        self.assertIn(
            "contract key order ref_type, ref_id, ref_revision",
            instructions,
        )
        self.assertIn(
            "never copy that display order into the output",
            instructions,
        )
        self.assertIn(
            "Independently choose exactly one state_ref",
            instructions,
        )
        self.assertIn(
            "does not require selected state positions to be monotonic",
            instructions,
        )

    def test_f4_shared_plan_builds_independent_ordered_eligible_sets(self) -> None:
        plan = build_canonical_f4_acceptance_target_plan(
            canonical_b_use_case_view={
                "use_cases": [
                    {"use_case_id": "UC-01"},
                    {"use_case_id": "UC-02"},
                ]
            },
            f2_registered_state_visibility_view={
                "states": [
                    {"stable_id": "state-initial"},
                    {"stable_id": "state-validation-error"},
                    {"stable_id": "state-recovery-success"},
                ]
            },
            f3_registered_interaction_view={
                "interactions": [
                    {
                        "stable_id": "interaction-initial",
                        "source_state_stable_id": "state-initial",
                        "target_state_stable_id": "state-initial",
                    },
                    {
                        "stable_id": "interaction-error",
                        "source_state_stable_id": "state-initial",
                        "target_state_stable_id": "state-validation-error",
                    },
                    {
                        "stable_id": "interaction-recovery",
                        "source_state_stable_id": "state-validation-error",
                        "target_state_stable_id": "state-recovery-success",
                    },
                ]
            },
            deterministic_use_case_mapping_view={
                "ordered_mappings": [
                    {
                        "use_case_id": "UC-01",
                        "interaction_stable_ids": [
                            "interaction-error",
                            "interaction-recovery",
                        ],
                    },
                    {
                        "use_case_id": "UC-02",
                        "interaction_stable_ids": ["interaction-initial"],
                    },
                ]
            },
        )

        self.assertEqual(
            [
                [ref["ref_id"] for ref in row["ordered_eligible_state_refs"]]
                for row in plan
            ],
            [
                ["state-validation-error", "state-recovery-success"],
                ["state-initial"],
            ],
        )

    def test_prompt_replays_from_exact_input_and_plan(self) -> None:
        input_bytes = _canonical(
            {
                "schema_version": "test.input.v1",
                "node_id": "F3",
                "projection": {"test": True},
            }
        )
        plan = [
            {
                "plan_index": 0,
                "transition_kind": "same_state_work",
                "required_trigger_component_local_id": "search_input",
            }
        ]
        raw = build_canonical_f1_f4_prompt(
            node_id="F3",
            input_bytes=input_bytes,
            required_interaction_plan=plan,
        )
        value = validate_canonical_prompt(
            raw,
            node_id="F3",
            input_bytes=input_bytes,
            required_interaction_plan=plan,
        )
        self.assertEqual(value["prompt_revision"], PROMPT_AUTHORITY_REVISION)
        self.assertEqual(
            value["prompt_authority_identity"],
            PROMPT_AUTHORITY_IDENTITY,
        )
        forged = json.loads(raw.decode("utf-8"))
        forged["instructions"] = copy.deepcopy(forged["instructions"])
        forged["instructions"].append("stage-specific drift")
        with self.assertRaisesRegex(PromptAuthorityError, "drifted"):
            validate_canonical_prompt(
                _canonical(forged),
                node_id="F3",
                input_bytes=input_bytes,
                required_interaction_plan=plan,
            )

    def test_historical_manual_runner_cannot_select_old_prompt_semantics(
        self,
    ) -> None:
        input_bytes = _canonical(
            {
                "schema_version": (
                    historical_runtime.P4_05_INPUT_SCHEMA_VERSION
                ),
                "node_id": "F1",
            }
        )
        prompt = json.loads(
            historical_runtime._node_prompt(
                node_id="F1",
                input_bytes=input_bytes,
            )
        )
        self.assertEqual(
            prompt["prompt_authority_identity"],
            PROMPT_AUTHORITY_IDENTITY,
        )
        with self.assertRaisesRegex(
            historical_runtime.Phase4RemoteFreshIntegratedError,
            "replay-only",
        ):
            historical_runtime._node_prompt(
                node_id="F1",
                input_bytes=input_bytes,
                prompt_revision=(
                    historical_runtime.P4_05_F3_F4_PROMPT_REVISION
                ),
            )


if __name__ == "__main__":
    unittest.main()
