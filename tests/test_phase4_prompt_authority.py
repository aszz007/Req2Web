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
    build_canonical_f1_f4_prompt,
    prompt_authority_manifest,
    validate_canonical_prompt,
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
        f2_instructions = "\n".join(
            manifest["node_specific_instructions"]["F2"]
        )
        f1_instructions = "\n".join(
            manifest["node_specific_instructions"]["F1"]
        )
        self.assertIn(
            "explicit button or action component",
            f1_instructions,
        )
        self.assertIn(
            "dedicated advancement control",
            f1_instructions,
        )
        self.assertIn("required_f1_component_order", f2_instructions)
        self.assertIn("exact subsequence", f2_instructions)

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
        instructions = "\n".join(value["instructions"])
        self.assertIn(
            "scanning required_f1_component_order from left to right",
            instructions,
        )
        self.assertIn(
            "Never regroup components by section",
            instructions,
        )
        self.assertIn(
            "emit both a separate error state",
            instructions,
        )
        self.assertIn("error state must not be the final state", instructions)
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
        self.assertIn(
            "successful recovery must target the later non-error state",
            instructions,
        )
        self.assertIn(
            "never represent successful recovery as a self-loop",
            instructions,
        )
        self.assertIn(
            "choose distinct visible trigger components",
            instructions,
        )
        self.assertIn(
            "absent from that exact source-state list",
            instructions,
        )
        self.assertIn(
            "required_trigger_component_local_id",
            instructions,
        )

    def test_shared_f3_plan_assigns_distinct_exact_triggers(self) -> None:
        plan = build_canonical_f3_interaction_plan(
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
                "state_ref": {
                    "ref_type": "registry_stable",
                    "ref_id": "p4-f2-state-example",
                    "ref_revision": "req2web.phase4.registry.p4_02a.v1",
                },
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
        instructions = "\n".join(value["instructions"])
        self.assertIn(
            "contract key order ref_type, ref_id, ref_revision",
            instructions,
        )
        self.assertIn(
            "never copy that display order into the output",
            instructions,
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
