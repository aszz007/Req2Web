"""Focused no-model tests for the F4 savepoint preparation foundation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import req2web_runtime.phase4_remote_qwen as f3
import req2web_runtime.phase4_remote_qwen_f4 as f4


class F4PolicyTests(unittest.TestCase):
    def test_policy_is_canonical_and_no_action(self):
        policy, raw = f4.load_f4_preflight_policy()
        self.assertEqual(raw, f4._canonical_bytes(policy.to_dict()))
        self.assertEqual(policy.node["node_id"], "F4")
        self.assertEqual(policy.execution["generate_calls"], 0)
        self.assertFalse(policy.action_state["model_action"])
        self.assertFalse(policy.action_state["remote_action"])

    def test_policy_rejects_final_acceptance_or_model_action(self):
        policy, _ = f4.load_f4_preflight_policy()
        tampered = policy.to_dict()
        tampered["execution"]["model_action"] = True
        tampered["policy_id"] = f4._identity(
            {key: value for key, value in tampered.items() if key != "policy_id"},
            revision=f4.F4_POLICY_SCHEMA_VERSION,
        )["sha256"]
        with self.assertRaises(f4.Phase4RemoteQwenF4ContractError):
            f4.F4PreflightPolicy.from_dict(tampered)


class F4ProjectionTests(unittest.TestCase):
    def test_projection_uses_exact_f4_owning_categories(self):
        policy = f4._f4_projection_policy()
        self.assertEqual(list(policy.allowed_categories), f4.F4_ALLOWED_CATEGORIES)
        self.assertEqual(
            list(policy.upstream_required_node_ids), ["F1", "F2", "F3"]
        )
        self.assertEqual(policy.b_aux_disposition, "absent/not_requested")

    def test_prompt_is_candidate_semantics_only(self):
        policy = f4._f4_projection_policy()
        raw = f4._build_f4_prompt(input_bytes=b"{}", policy=policy)
        prompt = json.loads(raw)
        self.assertEqual(prompt["node_id"], "F4")
        self.assertEqual(
            prompt["output_contract"]["exact_top_level_keys"],
            ["acceptance_checks"],
        )
        self.assertTrue(
            any("Do not claim final Acceptance" in row for row in prompt["instructions"])
        )

    def test_config_and_request_are_no_generate_preparation(self):
        profile = SimpleNamespace(
            to_dict=lambda: {"profile": "bf16"},
            dtype="bfloat16",
            quantization="none",
            compute_dtype="bfloat16",
            device_map={"": 0},
            cpu_offload=False,
            model_context_tokens=f3.REMOTE_MODEL_CONTEXT_TOKENS,
        )
        config = json.loads(f4._build_f4_config(profile=profile))
        request = json.loads(f4._build_f4_request())
        self.assertFalse(config["model_action"])
        self.assertEqual(config["generate_calls"], 0)
        self.assertEqual(request["generate_call_cap"], 0)
        self.assertEqual(request["node_id"], "F4")


class F4SavepointTests(unittest.TestCase):
    def test_hash_drift_fails_before_replay(self):
        root = Path(tempfile.gettempdir()) / f".p4-f4-test-{uuid.uuid4().hex}"
        root.mkdir()
        try:
            for name in (
                f3.REMOTE_REVALIDATION_NAME,
                f3.REMOTE_REVALIDATED_OUTPUT_NAME,
                f3.REMOTE_REVALIDATED_STATE_NAME,
                f3.REMOTE_RAW_NAME,
            ):
                (root / name).write_bytes(b"drift")
            with self.assertRaises(f4.Phase4RemoteQwenF4ContractError):
                f4._replay_f3_savepoint(f3_result_root=root)
        finally:
            for child in root.iterdir():
                child.unlink()
            root.rmdir()


class F4CliTests(unittest.TestCase):
    def test_confirmation_is_required(self):
        from scripts import prepare_phase4_remote_qwen_f4 as script

        args = [
            "--f3-result-root",
            "f3",
            "--result-root",
            "f4",
        ]
        with patch.object(script, "prepare_phase4_remote_qwen_f4") as prepare:
            with self.assertRaises(SystemExit) as captured:
                script.main(args)
        self.assertEqual(captured.exception.code, 2)
        prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
