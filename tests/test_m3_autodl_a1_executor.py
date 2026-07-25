from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime as runtime_package
import req2web_runtime.autodl_a1_executor as executor
from tests import test_m3_autodl_a1_operational as operational_fixtures


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def identify(prefix, value, key):
    value[key] = prefix + hashlib.sha256(canonical({name: item for name, item in value.items() if name != key})).hexdigest()[:20]


class RealA1ExecutorTests(unittest.TestCase):
    def setUp(self):
        fixture = operational_fixtures.RealA1OperationalTests("test_valid_artifact_chain_is_local_and_non_manager_consumable")
        fixture.setUp()
        try:
            _, self.plan, self.controls, *_ = fixture.parts()
        finally:
            fixture.tearDown()
        self.root = Path(__file__).resolve().parents[1] / f"req2web_a1_executor_{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root)

    def rejected(self, action, code=None):
        with self.assertRaises(executor.RealA1ExecutorError) as raised:
            action()
        if code is not None:
            self.assertEqual(str(raised.exception), code)

    def success_script(self, payload=None, delays=None):
        payload = deepcopy(payload or executor._success_payload_for_tests(self.plan))
        delays = delays or [1, 1, 1, 1, 1, 1]
        phases = (("setup", "started"), ("setup", "completed"), ("load", "started"), ("load", "completed"), ("probe", "started"), ("probe", "completed"))
        return [{"delay_ms": delay, "raw": executor._channel_bytes_for_tests(index, phase, transition, payload if index == 6 else {})} for index, ((phase, transition), delay) in enumerate(zip(phases, delays), 1)]

    def run_script(self, script, survive_terminate=False, utc_values=None, root_bindings=None):
        return executor._run_scripted_executor_for_tests(
            self.plan,
            self.controls,
            script,
            survive_terminate,
            utc_values=utc_values,
            root_bindings=root_bindings,
        )

    def test_public_api_is_typed_not_root_exported_and_has_no_overrides(self):
        self.assertFalse(hasattr(runtime_package, "execute_real_a1"))
        self.assertNotIn("execute_real_a1", runtime_package.__all__)
        self.assertEqual(tuple(inspect.signature(executor.execute_real_a1).parameters), ("plan_path", "controls_path", "package_root", "model_root", "evidence_root", "execute_real_a1"))
        for artifact_type in (executor.A1ProductionPhaseEvent, executor.A1ProductionObservation, executor.A1ExecutionReceipt, executor.A1EvidenceCommitMarker):
            for name in ("from_dict", "from_bytes", "validate", "to_dict", "canonical_bytes", "sha256"):
                self.assertFalse(any(parameter.startswith("_") for parameter in inspect.signature(getattr(artifact_type, name)).parameters))
        with self.assertRaises(TypeError):
            executor.execute_real_a1("p", "c", "package", "model", "evidence", True, callback=lambda: None)
        with self.assertRaises(TypeError):
            executor.execute_real_a1("p", "c", "package", "model", "evidence", True, clock=lambda: None)
        self.rejected(lambda: executor.execute_real_a1("p", "c", "package", "model", "evidence", False), "explicit_execute_real_a1_required")

    def test_scripted_success_only_awaits_independent_gate_and_logs_no_raw_text(self):
        observation, receipt, meta = self.run_script(self.success_script())
        self.assertEqual(observation.data["decision"], "pass")
        self.assertEqual(receipt.data["status"], "a1_passed_awaiting_independent_gate")
        self.assertEqual(receipt.data["next_state"], "awaiting_independent_a1_gate_validation")
        for key in ("a2_unlocked", "provider_invoked", "project_data_transferred", "compatibility_run_occurred", "h1_allowed", "formal_quality_allowed", "manager_a1_passed"):
            self.assertFalse(receipt.data[key])
        observation.validate_against(self.plan, self.controls)
        receipt.validate_against(self.plan, self.controls, observation)
        encoded = observation.canonical_bytes().decode("utf-8")
        self.assertNotIn(executor.FIXED_PROMPT, encoded)
        self.assertNotIn(executor.EXPECTED_SUFFIX, encoded)
        self.assertEqual(meta, {"terminate": 0, "kill": 0, "elapsed_ms": 6})
        self.assertEqual(observation.data["execution_started_utc"], "2026-07-25T00:00:00Z")
        self.assertEqual(observation.data["execution_completed_utc"], "2026-07-25T00:00:01Z")
        self.assertEqual([(event["phase"], event["transition"]) for event in observation.data["phase_events"]], [("setup", "started"), ("setup", "completed"), ("load", "started"), ("load", "completed"), ("probe", "started"), ("probe", "completed")])

    def test_execution_authorization_window_fails_closed_without_clock_override(self):
        cases = (
            (
                "not_yet_valid",
                ("2026-07-24T23:59:58Z", "2026-07-24T23:59:59Z"),
                1,
            ),
            (
                "expired_start",
                ("2026-08-01T00:00:01Z", "2026-08-01T00:00:02Z"),
                1,
            ),
            (
                "crosses_expiry",
                ("2026-07-31T23:59:59Z", "2026-08-01T00:00:01Z"),
                0,
            ),
        )
        for name, utc_values, expected_terminate in cases:
            with self.subTest(name=name):
                observation, receipt, meta = self.run_script(
                    self.success_script(),
                    utc_values=utc_values,
                )
                self.assertEqual(observation.data["decision"], "fail")
                self.assertEqual(
                    observation.data["failure_codes"],
                    ["authorization_window_invalid"],
                )
                self.assertEqual(
                    receipt.data["status"],
                    "a1_failed_cleanup_required",
                )
                self.assertFalse(receipt.data["a2_unlocked"])
                self.assertFalse(receipt.data["manager_a1_passed"])
                self.assertEqual(meta["terminate"], expected_terminate)

    def test_wrong_runtime_gpu_device_model_and_processor_fail_closed(self):
        mutations = (
            ("runtime", lambda payload: payload["runtime"].__setitem__("torch", "2.8.0"), "runtime_invalid"),
            ("gpu", lambda payload: payload["gpu"].__setitem__("name", "NVIDIA H100"), "gpu_invalid"),
            ("resource", lambda payload: payload["resources"].__setitem__("ram_gib", 32), "resource_invalid"),
            ("device", lambda payload: payload["gpu"].__setitem__("model_device", "cuda:1"), "device_invalid"),
            ("revision", lambda payload: payload["model"].__setitem__("revision", "0" * 40), "model_invalid"),
            ("quantization", lambda payload: payload["model"].__setitem__("quantization", "4bit"), "model_invalid"),
            ("offload", lambda payload: payload["model"].__setitem__("cpu_offload", True), "model_invalid"),
            ("image", lambda payload: payload["processor"].update({"keys": ["attention_mask", "input_ids", "pixel_values"], "multimedia_keys": ["pixel_values"]}), "processor_invalid"),
            ("video", lambda payload: payload["processor"].update({"keys": ["attention_mask", "input_ids", "video_grid_thw"], "multimedia_keys": ["video_grid_thw"]}), "processor_invalid"),
        )
        for label, mutation, code in mutations:
            with self.subTest(label=label):
                payload = executor._success_payload_for_tests(self.plan)
                mutation(payload)
                observation, receipt, _ = self.run_script(self.success_script(payload))
                self.assertEqual(observation.data["decision"], "fail")
                self.assertEqual(observation.data["failure_codes"], [code])
                self.assertEqual(receipt.data["status"], "a1_failed_cleanup_required")

    def test_suffix_prompt_false_positive_and_network_fail_closed(self):
        mutations = (
            ("suffix", lambda payload: payload["probe"].__setitem__("output_sha256", "0" * 64), "probe_invalid"),
            ("prompt_false_positive", lambda payload: payload["probe"].__setitem__("prompt_sentinel_absent_from_output", False), "probe_invalid"),
            ("same_netns", lambda payload: payload["network"].update({"child_netns_id": payload["network"]["parent_netns_id"], "netns_distinct": False}), "netns_invalid"),
            ("listener", lambda payload: payload["network"].__setitem__("listener_count", 1), "listener_invalid"),
            ("dns", lambda payload: payload["network"].__setitem__("dns_status", "reachable"), "dns_invalid"),
            ("egress", lambda payload: payload["network"].__setitem__("egress_status", "reachable"), "egress_invalid"),
            ("endpoint", lambda payload: payload["network"].__setitem__("http_endpoint_observed", True), "listener_invalid"),
        )
        for label, mutation, code in mutations:
            with self.subTest(label=label):
                payload = executor._success_payload_for_tests(self.plan)
                mutation(payload)
                observation, receipt, _ = self.run_script(self.success_script(payload))
                self.assertEqual(observation.data["failure_codes"], [code])
                self.assertEqual(receipt.data["next_state"], "a3_cleanup_release_revoke_required")

    def test_load_oom_and_forged_phase_fail_closed(self):
        script = self.success_script()[:3]
        script.append({"delay_ms": 1, "raw": executor._channel_bytes_for_tests(4, "load", "failed", {"failure_code": "load_oom"})})
        observation, receipt, meta = self.run_script(script)
        self.assertEqual(observation.data["failure_codes"], ["load_oom"])
        self.assertEqual(receipt.data["status"], "a1_failed_cleanup_required")
        self.assertEqual(meta["terminate"], 1)
        network = self.success_script()[:5]
        network.append({"delay_ms": 1, "raw": executor._channel_bytes_for_tests(6, "probe", "failed", {"failure_code": "network_invalid"})})
        observation, receipt, meta = self.run_script(network)
        self.assertEqual(observation.data["failure_codes"], ["network_invalid"])
        self.assertEqual(receipt.data["status"], "a1_failed_cleanup_required")
        self.assertEqual(meta["terminate"], 1)
        forged = self.success_script()
        forged[2]["raw"] = executor._channel_bytes_for_tests(99, "probe", "started", {})
        observation, receipt, meta = self.run_script(forged)
        self.assertEqual(observation.data["failure_codes"], ["phase_sequence_invalid"])
        self.assertEqual(meta["terminate"], 1)

    def test_parent_monotonic_setup_load_probe_timeouts_and_kill(self):
        setup = self.success_script(delays=[2700 * 1000 + 1, 1, 1, 1, 1, 1])
        observation, receipt, meta = self.run_script(setup, survive_terminate=True)
        self.assertEqual(observation.data["failure_codes"], ["setup_timeout"])
        self.assertEqual((meta["terminate"], meta["kill"]), (1, 1))
        load = self.success_script(delays=[1, 1, 1, 720 * 1000 + 1, 1, 1])
        observation, _, meta = self.run_script(load)
        self.assertEqual(observation.data["failure_codes"], ["load_timeout"])
        self.assertEqual(meta["terminate"], 1)
        probe = self.success_script(delays=[1, 1, 1, 1, 1, 120 * 1000 + 1])
        observation, _, meta = self.run_script(probe)
        self.assertEqual(observation.data["failure_codes"], ["probe_timeout"])
        self.assertEqual(meta["terminate"], 1)

    def test_forged_receipt_pass_a2_and_direct_constructor_reject(self):
        observation, receipt, _ = self.run_script(self.success_script())
        forged_observation = deepcopy(observation.data)
        forged_observation["runtime"]["torch"] = "forged"
        identify("real-a1-production-observation-", forged_observation, "observation_id")
        self.rejected(lambda: executor.A1ProductionObservation.from_bytes(canonical(forged_observation)), "runtime_invalid")
        self.rejected(executor.A1ProductionObservation(forged_observation).to_dict, "runtime_invalid")
        failed, _, _ = self.run_script(self.success_script()[:1])
        forged_failure = deepcopy(failed.data)
        forged_failure["runtime"]["torch"] = "forged-failure-runtime"
        identify("real-a1-production-observation-", forged_failure, "observation_id")
        self.rejected(lambda: executor.A1ProductionObservation.from_bytes(canonical(forged_failure)), "production_failure_measurements_invalid")
        self.rejected(executor.A1ProductionObservation(forged_failure).to_dict, "production_failure_measurements_invalid")
        forged_phases = deepcopy(observation.data)
        forged_phases["phase_events"][0]["sequence"] = 2
        identify("real-a1-phase-event-", forged_phases["phase_events"][0], "event_id")
        forged_phases["phase_inventory_sha256"] = hashlib.sha256(canonical(forged_phases["phase_events"])).hexdigest()
        identify("real-a1-production-observation-", forged_phases, "observation_id")
        self.rejected(lambda: executor.A1ProductionObservation.from_bytes(canonical(forged_phases)), "phase_event_inventory_invalid")
        forged_receipt = deepcopy(receipt.data)
        forged_receipt["manager_a1_passed"] = True
        forged_receipt["a2_unlocked"] = True
        identify("real-a1-execution-receipt-", forged_receipt, "receipt_id")
        self.rejected(lambda: executor.A1ExecutionReceipt.from_bytes(canonical(forged_receipt)), "execution_receipt_unlock_invalid")
        self.rejected(executor.A1ExecutionReceipt(forged_receipt).to_dict, "execution_receipt_unlock_invalid")
        self.assertEqual(executor.A1ProductionObservation.from_bytes(observation.canonical_bytes()).to_dict(), observation.data)
        self.assertEqual(executor.A1ExecutionReceipt.from_bytes(receipt.canonical_bytes()).to_dict(), receipt.data)

        payloads = {
            executor.PHASE_EVENT_FILENAME: canonical(observation.data["phase_events"]),
            executor.OBSERVATION_FILENAME: observation.canonical_bytes(),
            executor.RECEIPT_FILENAME: receipt.canonical_bytes(),
        }
        marker = executor.A1EvidenceCommitMarker(executor._build_evidence_commit_marker(observation, receipt, payloads))
        marker.validate_against(observation, receipt, payloads[executor.PHASE_EVENT_FILENAME], payloads[executor.OBSERVATION_FILENAME], payloads[executor.RECEIPT_FILENAME])
        self.assertEqual(executor.A1EvidenceCommitMarker.from_bytes(marker.canonical_bytes()).to_dict(), marker.data)
        forged_marker = deepcopy(marker.data)
        forged_marker["files"][0]["sha256"] = "0" * 64
        forged_marker["tree_sha256"] = hashlib.sha256(canonical(forged_marker["files"])).hexdigest()
        identify("real-a1-evidence-commit-", forged_marker, "commit_id")
        parsed_forged_marker = executor.A1EvidenceCommitMarker.from_bytes(canonical(forged_marker))
        self.rejected(lambda: parsed_forged_marker.validate_against(observation, receipt, payloads[executor.PHASE_EVENT_FILENAME], payloads[executor.OBSERVATION_FILENAME], payloads[executor.RECEIPT_FILENAME]), "evidence_commit_replay_invalid")
        self.rejected(lambda: executor.A1EvidenceCommitMarker(forged_marker).validate_against(observation, receipt, payloads[executor.PHASE_EVENT_FILENAME], payloads[executor.OBSERVATION_FILENAME], payloads[executor.RECEIPT_FILENAME]), "evidence_commit_replay_invalid")

    def test_definition_time_rebinding_does_not_change_saved_authorities(self):
        observation, receipt, _ = self.run_script(self.success_script())
        payloads = {executor.PHASE_EVENT_FILENAME: canonical(observation.data["phase_events"]), executor.OBSERVATION_FILENAME: observation.canonical_bytes(), executor.RECEIPT_FILENAME: receipt.canonical_bytes()}
        marker = executor.A1EvidenceCommitMarker(executor._build_evidence_commit_marker(observation, receipt, payloads))
        saved_types = (executor.A1ProductionPhaseEvent, executor.A1ProductionObservation, executor.A1ExecutionReceipt, executor.A1EvidenceCommitMarker)
        saved_run = executor._run_scripted_executor_for_tests
        snapshots = (observation.canonical_bytes(), receipt.canonical_bytes(), marker.canonical_bytes())
        calls = []

        def rebound(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("rebound authority used")

        class ReboundType:
            @classmethod
            def from_dict(cls, *args, **kwargs):
                return rebound(*args, **kwargs)

        scripted_channel = self.success_script()
        with patch.multiple(
            executor,
            _EXECUTOR_AUTHORITIES={"rebound": True},
            _captured_canon=rebound,
            _captured_sha=rebound,
            _captured_json=rebound,
            _validate_phase_event_data=rebound,
            _validate_production_observation_data=rebound,
            _validate_execution_receipt_data=rebound,
            A1OperationalPlan=ReboundType,
            A1OperationalControls=ReboundType,
            A1ModelInventory=ReboundType,
            A1ProductionPhaseEvent=ReboundType,
            A1ProductionObservation=ReboundType,
            A1ExecutionReceipt=ReboundType,
            A1EvidenceCommitMarker=ReboundType,
            PHASE_EVENT_SCHEMA="rebound",
            PRODUCTION_OBSERVATION_SCHEMA="rebound",
            EXECUTION_RECEIPT_SCHEMA="rebound",
            EVIDENCE_COMMIT_SCHEMA="rebound",
            CHANNEL_SCHEMA="rebound",
            WORKER_AUTHORITY="rebound",
            WORKER_VERSION="rebound",
            FIXED_PROMPT="rebound",
            EXPECTED_SUFFIX="rebound",
            _PHASE_KEYS=("rebound",),
            _OBSERVATION_KEYS=("rebound",),
            _RECEIPT_KEYS=("rebound",),
            _COMMIT_KEYS=("rebound",),
            _COMMIT_ROW_KEYS=("rebound",),
            _CHANNEL_KEYS=("rebound",),
            _PHASE_ORDER=(("rebound", "rebound"),),
            _FAILURE_CODES=("rebound",),
            json=rebound,
            hashlib=rebound,
            Mapping=rebound,
            datetime=rebound,
            timezone=rebound,
            _ROOT_INVARIANT_KEYS=("rebound",),
        ):
            self.assertEqual(saved_types[1].from_bytes(snapshots[0]).canonical_bytes(), snapshots[0])
            self.assertEqual(saved_types[2].from_bytes(snapshots[1]).canonical_bytes(), snapshots[1])
            self.assertEqual(saved_types[3].from_bytes(snapshots[2]).canonical_bytes(), snapshots[2])
            again, again_receipt, _ = saved_run(self.plan, self.controls, scripted_channel)
            self.assertEqual(again.data["decision"], "pass")
            self.assertEqual(again_receipt.data["status"], "a1_passed_awaiting_independent_gate")
        self.assertEqual(calls, [])

    def test_incremental_channel_reader_is_bounded_and_fail_closed(self):
        first = executor._channel_bytes_for_tests(1, "setup", "started", {})
        second = executor._channel_bytes_for_tests(2, "setup", "completed", {})
        adapter = executor._incremental_adapter_for_tests(
            [first[:11], first[11:] + b"\n" + second[:7], second[7:] + b"\n"],
            silent_after_chunks=True,
        )
        self.assertEqual(adapter.read_event(100), first)
        self.assertEqual(adapter.read_event(100), second)
        self.assertTrue(adapter.channel_clean())

        partial = executor._incremental_adapter_for_tests([b"partial-canonical-line"], silent_after_chunks=True)
        started = __import__("time").monotonic()
        self.assertIsNone(partial.read_event(50))
        elapsed = __import__("time").monotonic() - started
        self.assertGreaterEqual(elapsed, 0.03)
        self.assertLess(elapsed, 0.25)
        self.assertFalse(partial.channel_clean())

        oversize = executor._incremental_adapter_for_tests(
            [b"x" * (executor._MAX_CHANNEL_EVENT_LINE_BYTES + 1)],
            silent_after_chunks=True,
        )
        self.rejected(lambda: oversize.read_event(100), "channel_invalid")

        eof_partial = executor._incremental_adapter_for_tests([b"partial", b""], silent_after_chunks=False)
        self.rejected(lambda: eof_partial.read_event(100), "channel_invalid")

        invalid_utf8 = executor._incremental_adapter_for_tests([b"\xff\n"], silent_after_chunks=True)
        self.rejected(lambda: invalid_utf8.read_event(100), "channel_invalid")

        extra = executor._incremental_adapter_for_tests([first + b"\nextra\n"], silent_after_chunks=True)
        self.assertEqual(extra.read_event(100), first)
        self.assertFalse(extra.channel_clean())

        terminal_chunks = [item["raw"] + b"\n" for item in self.success_script()]
        terminal_chunks[-1] += b"extra-terminal-line\n"
        terminal_adapter = executor._incremental_adapter_for_tests(terminal_chunks, silent_after_chunks=False)
        contract = executor._EXECUTOR_AUTHORITIES["root_contract"](
            self.controls,
            ["private-incremental-test"],
            {"mode": "private-incremental-test"},
        )
        failed, failed_receipt = executor._EXECUTOR_AUTHORITIES["monitor_adapter"](
            self.plan,
            self.controls,
            terminal_adapter,
            contract,
        )
        self.assertEqual(failed.data["failure_codes"], ["channel_invalid"])
        self.assertEqual(failed_receipt.data["status"], "a1_failed_cleanup_required")

    def test_evidence_staging_is_exclusive_fixed_inventory_and_no_overwrite(self):
        evidence = self.root / "evidence"
        staging = executor._prepare_evidence_staging_root(evidence)
        self.assertTrue(staging.is_dir())
        observation, receipt, _ = self.run_script(self.success_script())
        executor._write_executor_artifacts(staging, observation, receipt)
        self.assertEqual(tuple(sorted(item.name for item in staging.iterdir())), tuple(sorted((executor.PHASE_EVENT_FILENAME, executor.OBSERVATION_FILENAME, executor.RECEIPT_FILENAME, executor.EVIDENCE_COMMIT_FILENAME))))
        self.rejected(lambda: executor._write_executor_artifacts(staging, observation, receipt), "executor_evidence_extra_or_existing")
        nonempty = self.root / "nonempty"
        nonempty.mkdir()
        (nonempty / "unexpected").write_text("x", encoding="utf-8")
        self.rejected(lambda: executor._prepare_evidence_staging_root(nonempty), "executor_evidence_role_not_empty")
        non_directory = self.root / "evidence-file"
        non_directory.write_text("not-a-directory", encoding="utf-8")
        self.rejected(lambda: executor._prepare_evidence_staging_root(non_directory), "executor_evidence_role_not_empty")

    def test_publish_failure_returns_no_in_memory_artifacts_and_keeps_partial_files(self):
        observation, receipt, _ = self.run_script(self.success_script())
        ordered_names = (executor.PHASE_EVENT_FILENAME, executor.OBSERVATION_FILENAME, executor.RECEIPT_FILENAME, executor.EVIDENCE_COMMIT_FILENAME)
        for failure_index in (2, 3, 4):
            with self.subTest(failure_index=failure_index):
                staging = executor._prepare_evidence_staging_root(self.root / f"publish-failure-{failure_index}")
                writes = 0
                attempted_names = []

                def fail_selected_write(path, content):
                    nonlocal writes
                    writes += 1
                    attempted_names.append(path.name)
                    if writes == failure_index:
                        raise OSError("injected exclusive write failure")
                    executor._write_exclusive(path, content)

                def write_with_failure(target, saved_observation, saved_receipt):
                    executor._write_executor_artifacts(
                        target,
                        saved_observation,
                        saved_receipt,
                        _write=fail_selected_write,
                    )

                result = executor._publish_executor_result(
                    staging,
                    observation,
                    receipt,
                    _write_artifacts=write_with_failure,
                )
                self.assertIsNone(result.observation)
                self.assertIsNone(result.receipt)
                self.assertNotEqual(result.return_code, 0)
                self.assertTrue(result.cleanup_required)
                self.assertEqual(tuple(attempted_names), ordered_names[:failure_index])
                self.assertEqual({item.name for item in staging.iterdir()}, set(ordered_names[: failure_index - 1]))
                self.assertFalse((staging / executor.EVIDENCE_COMMIT_FILENAME).exists())
                if failure_index < 4:
                    self.assertFalse((staging / executor.RECEIPT_FILENAME).exists())
                else:
                    self.assertTrue((staging / executor.RECEIPT_FILENAME).exists())

        success_staging = executor._prepare_evidence_staging_root(self.root / "publish-success")
        success = executor._publish_executor_result(success_staging, observation, receipt)
        self.assertIs(success.observation, observation)
        self.assertIs(success.receipt, receipt)
        self.assertEqual(success.return_code, 0)
        self.assertFalse(success.cleanup_required)
        self.assertEqual({item.name for item in success_staging.iterdir()}, set(ordered_names))

    def test_static_production_child_and_parent_contract(self):
        child = (Path(__file__).resolve().parents[1] / "src" / "req2web_runtime" / "autodl_a1_probe_worker.py").read_text(encoding="utf-8")
        parent = (Path(__file__).resolve().parents[1] / "src" / "req2web_runtime" / "autodl_a1_executor.py").read_text(encoding="utf-8")
        cli = (Path(__file__).resolve().parents[1] / "scripts" / "run_stage3_autodl_a1_executor.py").read_text(encoding="utf-8")
        doc = (Path(__file__).resolve().parents[1] / "docs" / "stage3_m3_autodl_a1_executor.md").read_text(encoding="utf-8")
        for required in ("output[:, input_len:]", "local_files_only=True", "trust_remote_code=False", "torch_dtype=torch.bfloat16", 'model.to("cuda:0")', "enable_thinking=False", "do_sample=False", "max_new_tokens=8", "AutoModelForImageTextToText", "AutoProcessor", 'torch_uuid != gpu_base["uuid"]'):
            self.assertIn(required, child)
        self.assertNotIn("\nimport torch\n", child.split("def main", 1)[0])
        self.assertNotIn("\nimport transformers\n", child.split("def main", 1)[0])
        for required in ('unshare = "/usr/bin/unshare"', '"--net"', '"--fork"', '"--mount-proc"', "subprocess.Popen", "shell=False", "terminate()", "kill()", "os.read", "os.set_blocking"):
            self.assertIn(required, parent)
        self.assertNotIn(".readline()", parent)
        self.assertIn("_MAX_CHANNEL_EVENT_LINE_BYTES", parent)
        self.assertIn("_MAX_CHANNEL_BUFFER_BYTES", parent)
        self.assertIn("return _result_type(None, None, 4, True", parent)
        self.assertIn('EVIDENCE_COMMIT_FILENAME = "evidence_commit.json"', parent)
        self.assertIn("commit_marker.canonical_bytes()", parent)
        self.assertIn("--execute-real-a1", cli)
        self.assertNotIn("--prompt", cli)
        self.assertNotIn("--backend", cli)
        self.assertNotIn("--command", cli)
        for forbidden in ("--expected", "--executable", "--module", "--platform", "--observer", "--callback"):
            self.assertNotIn(forbidden, cli)
        for required in ("executor_core_candidate_accepted", "review status: `accepted`", "real-A1 execution status: `not_run`", "`real_A1_executor_ready`: `false`", "manager-consumable `A1_passed`", "No real run occurred", "not a four-file atomic transaction", "observation=None", "completion marker is a commit boundary"):
            self.assertIn(required, doc)


if __name__ == "__main__":
    unittest.main()
