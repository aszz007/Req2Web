from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.phase5_5090_execution_profile import (  # noqa: E402
    Phase5RTX5090ExecutionProfile,
    build_phase5_rtx5090_execution_profile,
)


class Phase5RTX5090ExecutionProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = build_phase5_rtx5090_execution_profile()
        self.payload = self.profile.to_dict()

    def test_profile_identity_is_frozen(self) -> None:
        self.assertEqual(
            self.profile.sha256(),
            "db47a61e183cfa8e809c19c41f68857b5f7e45fd0083dea7ec2e9606631e5f03",
        )
        self.assertEqual(
            self.payload["profile_id"],
            "phase5-rtx5090-profile-"
            "a534126eae551e03eb85595d64ba63308d861947987d10988a109be9f7e3f426",
        )

    def test_model_gpu_and_runtime_match_phase4_verified_profile(self) -> None:
        self.assertEqual(self.payload["model"]["model_id"], "Qwen/Qwen3.5-9B")
        self.assertEqual(self.payload["model"]["dtype"], "bfloat16")
        self.assertEqual(self.payload["model"]["quantization"], "none")
        self.assertFalse(self.payload["model"]["cpu_offload"])
        self.assertEqual(self.payload["platform"]["device_index"], 0)
        self.assertEqual(self.payload["runtime"]["python_version"], "3.11.15")
        self.assertEqual(self.payload["runtime"]["torch_version"], "2.7.1+cu128")
        self.assertEqual(self.payload["runtime"]["cuda_version"], "12.8")

    def test_generation_is_complete_raw_first_and_no_retry(self) -> None:
        generation = self.payload["generation"]
        raw_first = self.payload["raw_first"]
        self.assertFalse(generation["input_truncation"])
        self.assertFalse(generation["output_truncation"])
        self.assertEqual(generation["per_node_generate_call_cap"], 1)
        self.assertFalse(generation["automatic_retry"])
        self.assertEqual(generation["retry_count"], 0)
        self.assertTrue(raw_first["raw_response_write_once_and_fsync_before_parse"])
        self.assertFalse(raw_first["parse_before_raw_capture_allowed"])

    def test_worker_is_isolated_per_case_with_process_group_teardown(self) -> None:
        worker = self.payload["worker_supervisor"]
        self.assertEqual(worker["worker_isolation"], "one_worker_per_case")
        self.assertFalse(worker["cross_case_worker_reuse_allowed"])
        self.assertTrue(worker["supervisor_must_remain_alive"])
        self.assertTrue(worker["worker_process_group_required"])
        self.assertEqual(worker["terminate_signal"], "SIGTERM")
        self.assertEqual(worker["kill_signal"], "SIGKILL")

    def test_streaming_is_visible_but_not_raw_capture(self) -> None:
        streaming = self.payload["streaming"]
        self.assertEqual(streaming["worker_stdout_role"], "strict_json_ipc_only")
        self.assertEqual(streaming["worker_stderr_role"], "versioned_stream_events")
        self.assertIn("token_delta", streaming["event_types"])
        self.assertTrue(streaming["console_mirror_and_flush_required"])
        self.assertFalse(streaming["partial_stream_counts_as_raw_capture"])

    def test_phase4_results_and_development_checkpoints_cannot_enter_h1(self) -> None:
        self.assertFalse(self.payload["paths"]["phase4_result_reuse_allowed"])
        self.assertFalse(
            self.payload["resume"]["development_checkpoint_reuse_in_formal_h1_allowed"]
        )
        self.assertFalse(self.payload["resume"]["partial_case_generate_resume_allowed"])
        self.assertTrue(self.payload["resume"]["case_boundary_recovery_allowed"])

    def test_profile_selection_does_not_authorize_runtime_action(self) -> None:
        state = self.payload["action_state"]
        self.assertTrue(state["profile_selected_by_owner"])
        for key in (
            "instance_provided",
            "instance_identity_frozen",
            "price_time_storage_caps_frozen",
            "ssh_fingerprint_frozen",
            "source_action_commit_frozen",
            "phase5_sealed_runner_ready",
            "external_action_authorized",
            "ssh_connected",
            "model_loaded",
            "holdout_executed",
        ):
            self.assertFalse(state[key], key)

    def test_profile_round_trip_and_tamper_rejection(self) -> None:
        replayed = Phase5RTX5090ExecutionProfile.from_json_bytes(
            self.profile.canonical_json_bytes()
        )
        self.assertEqual(replayed, self.profile)

        noncanonical = json.dumps(self.payload, indent=2).encode()
        with self.assertRaisesRegex(ValueError, "not canonical"):
            Phase5RTX5090ExecutionProfile.from_json_bytes(noncanonical)

        tampered = json.loads(json.dumps(self.payload))
        tampered["model"]["quantization"] = "4bit"
        with self.assertRaisesRegex(ValueError, "content drifted"):
            Phase5RTX5090ExecutionProfile.from_dict(tampered)


if __name__ == "__main__":
    unittest.main()
