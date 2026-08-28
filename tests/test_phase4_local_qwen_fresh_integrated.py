from __future__ import annotations

import base64
import json
import queue
import shutil
import unittest
from pathlib import Path

import req2web_runtime.phase4_local_qwen_fresh_integrated as fresh
from req2web_orchestration.phase4_graph import (
    phase4_normalize_and_validate_f4_output,
)
from req2web_runtime.phase4_local_qwen import Phase4LocalQwenPilotRunner


class Phase4LocalQwenFreshIntegratedTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]
    QUALIFICATION_ROOT = ROOT / "fixtures" / "phase4" / "integrated_raw"
    TEST_ROOT = ROOT / ".p4-03i-fresh-integrated-test-results"

    @classmethod
    def setUpClass(cls) -> None:
        if not cls.QUALIFICATION_ROOT.is_dir():
            raise unittest.SkipTest(
                "self-contained integrated qualification savepoint is unavailable"
            )
        if cls.TEST_ROOT.exists():
            shutil.rmtree(cls.TEST_ROOT)
        cls.TEST_ROOT.mkdir()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.TEST_ROOT.exists():
            shutil.rmtree(cls.TEST_ROOT)

    def _prepare(self, name: str) -> Path:
        result_root = self.TEST_ROOT / name
        fresh.prepare_phase4_local_qwen_fresh_integrated(
            qualification_savepoint_root=self.QUALIFICATION_ROOT,
            result_root=result_root,
            run_id=(
                f"{fresh.FRESH_INTEGRATED_RUN_PREFIX}{name}"
            ),
        )
        return result_root

    def _make_pilot(
        self,
        prepared_root: Path,
        *,
        f4_order_drift: bool,
        backend: object | None = None,
    ) -> fresh.FreshIntegratedPilot:
        (
            policy,
            qualification,
            run_binding,
            pilot,
            policies,
            profile,
            manifest,
            b_input,
        ) = fresh._load_prepared(prepared_root)
        backend_impl = (
            fresh.ScriptedStreamingBackend(
                fresh._fixture_outputs(
                    b_input,
                    f4_order_drift=f4_order_drift,
                )
            )
            if backend is None
            else backend
        )
        adapter = fresh._FreshBackendAdapter(
            backend_impl,
            event_sink=lambda _: None,
            allow_scripted_fallback=backend is None,
        )
        runner = Phase4LocalQwenPilotRunner(
            pilot=pilot,
            policies=policies,
            profile=profile,
            manifest=manifest,
            result_root=prepared_root,
            b_input=b_input,
            backend=adapter,
            source_kind="scripted_test_fixture",
            fresh_integrated_only=True,
            integrated_event_observer=lambda _: None,
            f4_normalizer=lambda raw, output, state: (
                phase4_normalize_and_validate_f4_output(
                    raw_bytes=raw,
                    output=output,
                    state=state,
                )
            ),
            integrated_run_id=str(policy["run_id"]),
        )
        result = fresh.FreshIntegratedPilot(
            policy=policy,
            qualification=qualification,
            run_binding=run_binding,
            runner=runner,
            result_root=prepared_root,
        )
        adapter._event_sink = result._emit
        runner._integrated_event_observer = result._observe_stage
        return result

    def test_prepare_binds_savepoint_as_qualification_only(self):
        result_root = self._prepare("qualification")
        receipt = json.loads(
            (
                result_root / fresh.FRESH_INTEGRATED_QUALIFICATION_NAME
            ).read_text(encoding="utf-8")
        )
        validated = fresh.validate_fresh_integrated_qualification(
            receipt,
            savepoint_root=self.QUALIFICATION_ROOT,
        )
        self.assertEqual(
            validated["qualification_status"],
            "validated_node_contract_qualification_only",
        )
        self.assertTrue(validated["source_savepoint_is_not_integrated_input"])
        self.assertEqual(validated["model_generate_calls"], 0)
        self.assertFalse(validated["raw_model_contract_success"])
        self.assertTrue(validated["normalized_node_contract_success"])

    def test_node_local_is_forbidden_and_fresh_run_starts_empty(self):
        result_root = self._prepare("node-local")
        pilot = self._make_pilot(result_root, f4_order_drift=False)
        with self.assertRaises(fresh.Phase4LocalQwenFreshIntegratedError):
            pilot.run_node_local(node_id="F1")
        result = pilot.run_integrated(confirm_fresh_integrated_run=True)
        self.assertEqual(result["status"], "fresh_integrated_assembled")
        self.assertEqual(
            result["fresh_empty_authority_state_identity"]["revision"],
            fresh.FRESH_INTEGRATED_EMPTY_AUTHORITY_REVISION,
        )
        self.assertEqual(result["node_total_counts"], {
            "F1": 1,
            "F2": 1,
            "F3": 1,
            "F4": 1,
        })

    def test_f4_order_drift_preserves_raw_and_uses_normalized_path(self):
        result_root = self._prepare("f4-normalization")
        pilot = self._make_pilot(result_root, f4_order_drift=True)
        result = pilot.run_integrated(confirm_fresh_integrated_run=True)
        normalization = result["f4_normalization"]
        self.assertFalse(normalization["raw_model_contract_success"])
        self.assertTrue(normalization["normalized_node_contract_success"])
        self.assertEqual(normalization["normalization_count"], 2)
        self.assertEqual(result["composition_status"], "validated")
        self.assertEqual(result["assembler_status"], "assembled")
        self.assertEqual(result["backend_generate_calls"], 4)
        self.assertEqual(result["model_generate_calls"], 0)
        self.assertEqual(result["retry_count"], 0)
        for value in result["downstream"].values():
            self.assertEqual(value, "not_executed")
        event_payload = json.loads(
            (
                result_root / fresh.FRESH_INTEGRATED_EVENT_STREAM_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(
            any(event["event"] == "token_delta" for event in event_payload["events"])
        )
        self.assertTrue(
            any(
                event["event"] == "stage"
                and event["stage"] == "F1"
                for event in event_payload["events"]
            )
        )
        run_id = result["run_id"]
        attempt_root = (
            result_root
            / "runs"
            / run_id
            / "F4"
            / "attempt-01"
        )
        raw = (attempt_root / "raw_response.bin").read_bytes()
        normalized = (
            attempt_root / fresh.FRESH_INTEGRATED_NORMALIZED_OUTPUT_NAME
        ).read_bytes()
        receipt = json.loads(
            (
                attempt_root
                / fresh.FRESH_INTEGRATED_NORMALIZATION_RECEIPT_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertNotEqual(raw, normalized)
        self.assertEqual(receipt["raw_model_contract_success"], False)
        self.assertEqual(receipt["normalized_node_contract_success"], True)

    def test_integrated_run_cap_is_one(self):
        result_root = self._prepare("run-cap")
        pilot = self._make_pilot(result_root, f4_order_drift=False)
        pilot.run_integrated(confirm_fresh_integrated_run=True)
        with self.assertRaises(fresh.Phase4LocalQwenFreshIntegratedError):
            pilot.run_integrated(confirm_fresh_integrated_run=True)

    def test_persistent_worker_protocol_streams_four_nodes_without_reload(self):
        messages: "queue.Queue[dict[str, object]]" = queue.Queue()
        writes: list[dict[str, object]] = []
        worker_id = "worker-fresh-test"

        class ReactiveStdin:
            def write(self, raw: str) -> int:
                payload = json.loads(raw)
                writes.append(payload)
                if payload.get("kind") == "generate_fresh_integrated":
                    call_id = payload["call_id"]
                    node_id = payload["node_id"]
                    messages.put(
                        {
                            "protocol": fresh._local.FRESH_INTEGRATED_WORKER_PROTOCOL,
                            "kind": "token_delta",
                            "call_id": call_id,
                            "worker_id": worker_id,
                            "node_id": node_id,
                            "delta_b64": base64.b64encode(
                                f"<{node_id}>".encode("utf-8")
                            ).decode("ascii"),
                        }
                    )
                    messages.put(
                        {
                            "protocol": fresh._local.FRESH_INTEGRATED_WORKER_PROTOCOL,
                            "kind": "generation_result",
                            "call_id": call_id,
                            "worker_id": worker_id,
                            "raw_b64": base64.b64encode(
                                f'{{"node":"{node_id}"}}'.encode("utf-8")
                            ).decode("ascii"),
                            "generation_facts": {
                                "schema_version": (
                                    fresh._local.FRESH_INTEGRATED_RUNTIME_FACTS_SCHEMA_VERSION
                                ),
                                "input_token_length": 9000,
                                "native_context_tokens": 32768,
                                "generation_budget": 23768,
                                "output_limit_kind": "context_remaining",
                                "input_truncation": False,
                                "output_truncation": False,
                                "complete_single_json_required": True,
                            },
                        }
                    )
                return len(raw)

            def flush(self) -> None:
                return None

        class FakeProcess:
            pid = 32123

            def __init__(self) -> None:
                self.stdin = ReactiveStdin()
                self._exit_code: int | None = None

            def poll(self) -> int | None:
                return self._exit_code

            def terminate(self) -> None:
                self._exit_code = 0

            def kill(self) -> None:
                self._exit_code = -9

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                self._exit_code = 0
                return 0

        process = FakeProcess()
        backend = fresh._local.SupervisedLocalQwenBackend(
            process=process,  # type: ignore[arg-type]
            messages=messages,
            stderr_capture=fresh._local._WorkerStderrCapture(),
            profile=self._make_pilot(
                self._prepare("protocol-profile"),
                f4_order_drift=False,
            ).runner._profile,
            worker_id=worker_id,
            loaded_facts={},
            capability=fresh._local._REAL_RUNTIME_CAPABILITY,
            protocol=fresh._local.FRESH_INTEGRATED_WORKER_PROTOCOL,
            generate_call_cap=4,
            fresh_runtime_facts={
                "schema_version": (
                    fresh._local.FRESH_INTEGRATED_RUNTIME_FACTS_SCHEMA_VERSION
                ),
                "worker_protocol": fresh._local.FRESH_INTEGRATED_WORKER_PROTOCOL,
            },
        )
        streamed: list[bytes] = []
        for node_id in fresh.NODE_ORDER:
            raw = backend.generate_fresh_integrated(
                node_id=node_id,
                input_bytes=b"{}",
                prompt_bytes=b"{}",
                config_bytes=b"{}",
                request_bytes=json.dumps(
                    {
                        "call_kind": "integrated",
                        "b_aux_disposition": "absent/not_requested",
                        "node_id": node_id,
                    }
                ).encode("utf-8"),
                emit_delta=streamed.append,
            )
            self.assertEqual(raw, f'{{"node":"{node_id}"}}'.encode("utf-8"))
        self.assertEqual(
            [item["kind"] for item in writes],
            ["generate_fresh_integrated"] * 4,
        )
        self.assertEqual(streamed, [b"<F1>", b"<F2>", b"<F3>", b"<F4>"])
        self.assertIsNone(process.poll())
        self.assertEqual(backend.last_generation_facts["input_token_length"], 9000)
        self.assertEqual(backend.last_generation_facts["generation_budget"], 23768)

    def test_fresh_worker_rejects_truncation_fact(self):
        messages: "queue.Queue[dict[str, object]]" = queue.Queue()
        worker_id = "worker-truncation-test"

        class ReactiveStdin:
            def write(self, raw: str) -> int:
                payload = json.loads(raw)
                if payload.get("kind") == "generate_fresh_integrated":
                    messages.put(
                        {
                            "protocol": fresh._local.FRESH_INTEGRATED_WORKER_PROTOCOL,
                            "kind": "generation_result",
                            "call_id": payload["call_id"],
                            "worker_id": worker_id,
                            "raw_b64": base64.b64encode(b"{}").decode("ascii"),
                            "generation_facts": {
                                "schema_version": (
                                    fresh._local.FRESH_INTEGRATED_RUNTIME_FACTS_SCHEMA_VERSION
                                ),
                                "input_truncation": True,
                                "output_truncation": False,
                                "complete_single_json_required": True,
                            },
                        }
                    )
                return len(raw)

            def flush(self) -> None:
                return None

        class FakeProcess:
            pid = 32124

            def __init__(self) -> None:
                self.stdin = ReactiveStdin()
                self._exit_code: int | None = None

            def poll(self) -> int | None:
                return self._exit_code

            def terminate(self) -> None:
                self._exit_code = 0

            def kill(self) -> None:
                self._exit_code = -9

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                self._exit_code = 0
                return 0

        prepared = self._prepare("truncation-facts")
        pilot = self._make_pilot(prepared, f4_order_drift=False)
        backend = fresh._local.SupervisedLocalQwenBackend(
            process=FakeProcess(),  # type: ignore[arg-type]
            messages=messages,
            stderr_capture=fresh._local._WorkerStderrCapture(),
            profile=pilot.runner._profile,
            worker_id=worker_id,
            loaded_facts={},
            capability=fresh._local._REAL_RUNTIME_CAPABILITY,
            protocol=fresh._local.FRESH_INTEGRATED_WORKER_PROTOCOL,
            generate_call_cap=1,
            fresh_runtime_facts={
                "schema_version": (
                    fresh._local.FRESH_INTEGRATED_RUNTIME_FACTS_SCHEMA_VERSION
                ),
                "worker_protocol": fresh._local.FRESH_INTEGRATED_WORKER_PROTOCOL,
            },
        )
        with self.assertRaises(fresh._local.SupervisedWorkerFailure):
            backend.generate_fresh_integrated(
                node_id="F1",
                input_bytes=b"{}",
                prompt_bytes=b"{}",
                config_bytes=b"{}",
                request_bytes=b'{"call_kind":"integrated","b_aux_disposition":"absent/not_requested","node_id":"F1"}',
                emit_delta=lambda _: None,
            )

    def test_real_adapter_does_not_fallback_to_scripted_generate(self):
        class GenerateOnly:
            def generate(self, **_: object) -> bytes:
                return b"{}"

        adapter = fresh._FreshBackendAdapter(
            GenerateOnly(),
            event_sink=lambda _: None,
            allow_scripted_fallback=False,
        )
        with self.assertRaises(fresh.Phase4LocalQwenFreshIntegratedError):
            adapter.generate(
                node_id="F1",
                input_bytes=b"{}",
                prompt_bytes=b"{}",
                config_bytes=b"{}",
                 request_bytes=b"{}",
            )

    def test_pre_worker_failure_does_not_consume_model_call_budget(self):
        class ParentSeamFailure:
            generation_started = False

            def generate_fresh_integrated(self, **_: object) -> bytes:
                raise RuntimeError("fake parent adapter failure before worker send")

        prepared = self._prepare("pre-worker-failure")
        pilot = self._make_pilot(
            prepared,
            f4_order_drift=False,
            backend=ParentSeamFailure(),
        )
        result = pilot.run_integrated(confirm_fresh_integrated_run=True)

        latest = pilot.runner.latest_result
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.failure_code, "pre_worker_failure")
        self.assertFalse(latest.generate_started)
        self.assertEqual(pilot.runner.model_calls, 0)
        self.assertEqual(
            pilot.runner.ledger.node_total_counts,
            {"F1": 0, "F2": 0, "F3": 0, "F4": 0},
        )
        self.assertEqual(result["backend_generate_calls"], 0)
        self.assertEqual(result["model_generate_calls"], 0)
        self.assertFalse(result["model_action"])
        self.assertEqual(result["integrated_outcome"], "failed_closed")
        self.assertFalse(
            any(
                event.get("event") == "generation_started"
                for event in pilot.events
            )
        )

    def test_new_policy_links_pre_worker_failure_without_budget_reset(self):
        predecessor_root = self.TEST_ROOT / "portable-predecessor-source"
        attempt_root = (
            predecessor_root
            / "runs"
            / "p4-03i-local-qwen-fresh-integrated-run-old"
            / "F1"
            / "attempt-01"
        )
        attempt_root.mkdir(parents=True)

        def write_json(path: Path, value: object) -> None:
            path.write_bytes(fresh._canonical_bytes(value))

        write_json(
            attempt_root / "attempt_result.json",
            {
                "call_kind": "integrated",
                "failure_code": "backend_exception",
                "generate_started": True,
                "raw_status": "not_captured",
                "pilot_id": "p4-03i-local-qwen-fresh-integrated-v1",
                "run_id": "p4-03i-local-qwen-fresh-integrated-run-old",
                "node_id": "F1",
            },
        )
        write_json(
            predecessor_root / "supervisor_receipt.json",
            {
                "generation_started": False,
                "worker_exit_verified": True,
                "raw_status": "not_captured",
            },
        )
        write_json(
            predecessor_root / "ledger.json",
            {
                "integrated_run_count": 1,
                "node_total_counts": {"F1": 1},
            },
        )
        write_json(
            predecessor_root / "pilot_outcome.json",
            {
                "status": "integrated_failed_closed",
                "model_calls_performed": 1,
            },
        )

        predecessor = fresh._validate_predecessor_result_root(
            predecessor_root
        )
        policy = fresh.create_fresh_integrated_policy(
            run_id=(
                "p4-03i-local-qwen-fresh-integrated-run-new"
            ),
            predecessor_receipt=predecessor,
        )
        validated = fresh.validate_fresh_integrated_policy(policy)
        self.assertEqual(
            validated["predecessor_link"]["mode"],
            "pre_worker_failure",
        )
        self.assertEqual(
            validated["predecessor_link"]["receipt_id"],
            predecessor["predecessor_id"],
        )
        self.assertEqual(
            validated["predecessor_link"]["effective_model_generate_calls"],
            0,
        )
        self.assertFalse(
            validated["predecessor_link"]["aggregate_budget_reset"]
        )

    def test_fresh_profile_uses_native_context_and_complete_json_contract(self):
        prepared = self._prepare("profile-contract")
        (
            policy,
            _qualification,
            _run_binding,
            _pilot,
            _policies,
            profile,
            _manifest,
            _b_input,
        ) = fresh._load_prepared(prepared)
        self.assertEqual(profile.context_tokens, 32768)
        self.assertEqual(profile.max_input_tokens, profile.context_tokens)
        self.assertEqual(profile.max_new_tokens, profile.context_tokens)
        self.assertIsNone(policy["input_context"]["artificial_input_token_cap"])
        self.assertFalse(policy["output_contract"]["artificial_output_truncation"])
        self.assertEqual(
            policy["worker_contract"]["stopping_criteria"]["class_name"],
            "F4CompleteSingleJSONStoppingCriteria",
        )


if __name__ == "__main__":
    unittest.main()
