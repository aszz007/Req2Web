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
