from __future__ import annotations

import copy
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

import req2web_runtime.phase4_local_qwen_fresh_integrated as fresh
import req2web_runtime.phase4_local_qwen_fresh_integrated_revalidation as reval
from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    REGISTRY_REVISION,
    phase4_create_authority_state,
    phase4_register_node_output,
    phase4_synthetic_fixture_output,
)
from req2web_runtime import phase4_local_qwen as local
from req2web_runtime.phase4_local_qwen import (
    AttemptLedger,
    AttemptResult,
    AttemptPreCall,
    LocalQwenLoadReceipt,
    NodeD17ActionRecord,
    PilotExecutionLease,
    PilotOutcome,
    PilotSupervisorReceipt,
    WorkerStderrArtifact,
)


class Phase4LocalQwenFreshIntegratedRevalidationTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]
    QUALIFICATION_ROOT = ROOT / "fixtures" / "phase4" / "integrated_raw"

    @classmethod
    def setUpClass(cls) -> None:
        if not cls.QUALIFICATION_ROOT.is_dir():
            raise unittest.SkipTest("qualification savepoint is unavailable")
        cls.TEST_ROOT = cls.ROOT / ".p4-03i-fresh-integrated-revalidation-test-results"
        if cls.TEST_ROOT.exists():
            shutil.rmtree(cls.TEST_ROOT)
        cls.TEST_ROOT.mkdir()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.TEST_ROOT.exists():
            shutil.rmtree(cls.TEST_ROOT)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(local._canonical_bytes(value))

    def _prepare_root(self, name: str) -> tuple[Path, dict[str, object]]:
        prepared = self.TEST_ROOT / f"prepared-{name}"
        fresh.prepare_phase4_local_qwen_fresh_integrated(
            qualification_savepoint_root=self.QUALIFICATION_ROOT,
            result_root=prepared,
            run_id=f"{fresh.FRESH_INTEGRATED_RUN_PREFIX}{name}",
        )
        (
            policy,
            qualification,
            run_binding,
            pilot,
            policies,
            profile,
            manifest,
            b_input,
        ) = fresh._load_prepared(prepared)
        return prepared, {
            "policy": policy,
            "qualification": qualification,
            "run_binding": run_binding,
            "pilot": pilot,
            "policies": {item.node_id: item for item in policies},
            "profile": profile,
            "manifest": manifest,
            "b_input": b_input,
        }

    def _make_source(
        self,
        name: str,
        *,
        f1_f3_pass: bool = True,
        teardown_verified: bool = True,
    ) -> Path:
        prepared, material = self._prepare_root(name)
        source = self.TEST_ROOT / f"source-{name}"
        source.mkdir()
        for filename in (
            fresh.FRESH_INTEGRATED_ROOT_MARKER,
            local.RESULT_ROOT_MARKER_NAME,
            fresh.FRESH_INTEGRATED_POLICY_NAME,
            fresh.FRESH_INTEGRATED_QUALIFICATION_NAME,
            fresh.FRESH_INTEGRATED_RUN_BINDING_NAME,
            fresh.FRESH_INTEGRATED_MANIFEST_NAME,
        ):
            shutil.copyfile(prepared / filename, source / filename)

        pilot = material["pilot"]
        policies = material["policies"]
        profile = material["profile"]
        manifest = material["manifest"]
        b_input = material["b_input"]
        run_id = str(material["policy"]["run_id"])
        case_id = str(pilot.case_binding["case_id"])
        request_id = str(pilot.case_binding["request_id"])

        load_receipt = LocalQwenLoadReceipt.create(
            manifest=manifest,
            pilot=pilot,
            profile=profile,
            loaded_facts={
                "model_class": "Qwen3_5ForConditionalGeneration",
                "processor_class": "Qwen3VLProcessor",
                "is_loaded_in_4bit": True,
                "hf_device_map": {},
                "parameter_devices": ["cuda:0"],
                "compute_dtypes": ["torch.bfloat16"],
                "cpu_offload": False,
                "device": "cuda:0",
            },
        )
        lease = PilotExecutionLease.create(
            pilot=pilot,
            manifest=manifest,
            parent_pid=max(1, os.getpid()),
        )
        stderr_artifact = WorkerStderrArtifact.create(
            stderr_bytes=b"fixture stderr"
        )

        state = phase4_create_authority_state(b_input)
        outputs: dict[str, dict[str, object]] = {}
        states: dict[str, dict[str, object]] = {}
        for node_id in ("F1", "F2", "F3"):
            outputs[node_id] = phase4_synthetic_fixture_output(node_id, state)
            state = phase4_register_node_output(state, node_id, outputs[node_id])
            states[node_id] = copy.deepcopy(state)
        f4 = phase4_synthetic_fixture_output("F4", state)
        for check in f4["acceptance_checks"]:
            check["use_case_refs"] = [
                item["ref_id"] for item in check["use_case_refs"]
            ]
            check["state_ref"] = check["state_ref"]["ref_id"]
            rows = [
                row
                for row in state["registry_inventory"]
                if row["node_id"] in {"F1", "F2", "F3"}
            ]
            refs = []
            for entity_type, suffix in (
                ("component", "f1"),
                ("state", "f2"),
                ("interaction", "f3"),
            ):
                row = next(
                    row
                    for row in rows
                    if row["entity_type"] == entity_type
                )
                refs.append(
                    {
                        "ref_type": entity_type,
                        "ref_id": row["stable_id"],
                        "ref_revision": f"{REGISTRY_REVISION}.{suffix}",
                    }
                )
            check["refs"] = list(reversed(refs))
        outputs["F4"] = f4

        action_results: dict[str, AttemptResult] = {}
        for node_id in NODE_ORDER:
            attempt_relative = f"runs/{run_id}/{node_id}/attempt-01"
            raw_relative = f"{attempt_relative}/raw_response.bin"
            actual_input = local._canonical_bytes(
                {
                    "case_id": case_id,
                    "node_id": node_id,
                    "b_identity": state["b_identity"],
                }
            )
            prompt = local._canonical_bytes(
                {
                    "prompt_schema_version": (
                        f"{local.P4_03_SCHEMA_PREFIX}.prompt.v1"
                    ),
                    "template_revision": "p4-03-prompt-v1",
                }
            )
            config = local._canonical_bytes({"node_id": node_id})
            request = local._canonical_bytes(
                {
                    "attempt_index": 1,
                    "call_kind": "integrated",
                    "case_id": case_id,
                    "node_id": node_id,
                    "pilot_id": pilot.pilot_id,
                    "request_id": request_id,
                    "run_id": run_id,
                }
            )
            upstream_refs = []
            for prior_node in NODE_ORDER[: NODE_ORDER.index(node_id)]:
                output_identity = local._identity(
                    outputs[prior_node],
                    revision=(
                        f"{local.P4_03_SCHEMA_PREFIX}.node_output."
                        f"{pilot.pilot_id}.{run_id}.{case_id}."
                        f"{request_id}.{prior_node}"
                    ),
                )
                upstream_refs.append(
                    {
                        "ref_type": "node_output",
                        "ref_id": output_identity["sha256"],
                        "ref_sha256": output_identity["sha256"],
                        "ref_revision": output_identity["revision"],
                    }
                )
            policy = policies[node_id]
            input_ref = {
                "ref_type": "d17_input_view",
                "ref_id": (
                    "input-" + actual_input.hex()[:20]
                ),
                "ref_sha256": local._sha256(actual_input),
                "ref_revision": (
                    f"{local.P4_03_SCHEMA_PREFIX}.input-view."
                    f"{policy.projection_revision}.{run_id}.{node_id}"
                ),
            }
            budget_identity = local._identity(
                {
                    "pilot_id": pilot.pilot_id,
                    "node_total_call_cap": pilot.node_total_call_cap,
                    "node_local_call_cap": pilot.node_local_call_cap,
                    "integrated_run_cap": pilot.integrated_run_cap,
                    "retry_count_cap": pilot.retry_count_cap,
                },
                revision=f"{local.P4_03_SCHEMA_PREFIX}.budget.v1",
            )
            action = NodeD17ActionRecord.create(
                pilot_id=pilot.pilot_id,
                run_id=run_id,
                case_id=case_id,
                request_id=request_id,
                node_id=node_id,
                attempt_index=1,
                call_kind="integrated",
                profile=profile,
                manifest_ref={
                    "ref_type": "d17_manifest",
                    "ref_id": manifest.manifest_id,
                    "ref_sha256": manifest.sha256(),
                    "ref_revision": manifest.SCHEMA_VERSION,
                },
                policy_ref={
                    "ref_type": "d17_policy",
                    "ref_id": policy.policy_id,
                    "ref_sha256": policy.sha256(),
                    "ref_revision": policy.projection_revision,
                },
                input_view_ref=input_ref,
                upstream_refs=upstream_refs,
                actual_input=actual_input,
                prompt_revision="p4-03-prompt-v1",
                prompt_change=None,
                prompt=prompt,
                config_revision=policy.config_revision,
                config=config,
                request=request,
                budget_identity=budget_identity,
                source_kind="real_local_qwen",
                runtime_load_ref={
                    "ref_type": "local_qwen_load_receipt",
                    "ref_id": load_receipt.receipt_id,
                    "ref_sha256": load_receipt.sha256(),
                    "ref_revision": load_receipt.SCHEMA_VERSION,
                },
            )
            pre_call = AttemptPreCall.create(
                action=action,
                attempt_relative_path=attempt_relative,
                raw_response_relative_path=raw_relative,
                upstream_identities=upstream_refs,
            )
            raw = json.dumps(
                outputs[node_id],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=False,
            ).encode("utf-8")
            pass_node = node_id != "F4" and (
                f1_f3_pass or node_id != "F2"
            )
            attempt_result = AttemptResult.create(
                pre_call=pre_call,
                generate_started=True,
                raw_status="captured_nonempty",
                raw_response_relative_path=raw_relative,
                raw_sha256=local._sha256(raw),
                raw_byte_length=len(raw),
                parse_status="passed" if pass_node else "failed",
                node_contract_status="passed" if pass_node else "failed",
                registry_status="passed" if pass_node else "not_run",
                composition_status="not_executed",
                assembler_status="not_executed",
                integrated_success=False,
                failure_code=None if pass_node else "node_contract_invalid",
                terminal=not pass_node or node_id == "F4",
            )
            action_results[node_id] = attempt_result
            attempt_dir = source / attempt_relative
            attempt_dir.mkdir(parents=True)
            (attempt_dir / "pre_call.json").write_bytes(pre_call.canonical_bytes())
            (attempt_dir / "node_d17_action.json").write_bytes(action.canonical_bytes())
            (attempt_dir / "attempt_result.json").write_bytes(
                attempt_result.canonical_bytes()
            )
            (attempt_dir / "raw_response.bin").write_bytes(raw)

        ledger = AttemptLedger.create(pilot=pilot).begin_integrated_run()
        for node_id in NODE_ORDER:
            ledger = ledger.record_generate_started(
                node_id=node_id,
                call_kind="integrated",
            )
            ledger = ledger.record_result(action_results[node_id])
        ledger = ledger.stop("integrated_terminal_failed_closed", terminal=True)
        outcome = PilotOutcome.create(
            pilot=pilot,
            status="integrated_failed_closed",
            stop_reason="integrated_terminal_failed_closed",
            node_local_statuses={
                node_id: "not_started" for node_id in NODE_ORDER
            },
            node_total_counts={node_id: 1 for node_id in NODE_ORDER},
            integrated_run_id=run_id,
            integrated_outcome="failed_closed",
            integrated_node_raw_contract_pass_count=3,
            composition_status="not_executed",
            assembler_status="not_executed",
            model_calls_performed=4,
            model_action_occurred=True,
            source_kind="real_local_qwen",
        )
        supervisor = PilotSupervisorReceipt.create(
            lease=lease,
            terminal_status=(
                "normal_completed"
                if teardown_verified
                else "worker_teardown_unverified"
            ),
            worker_id="fixture-worker",
            worker_pid=max(1, os.getpid()),
            worker_exit_code=0,
            worker_exit_verified=teardown_verified,
            graceful_shutdown_requested=True,
            terminate_sent=False,
            kill_sent=False,
            generation_started=True,
            raw_status="captured",
            latest_attempt_result_identity=local._identity(
                action_results["F4"].to_dict(),
                revision=AttemptResult.SCHEMA_VERSION,
            ),
            pilot_outcome_identity=local._identity(
                outcome.to_dict(),
                revision=PilotOutcome.SCHEMA_VERSION,
            ),
            stderr_identity=local._identity(
                stderr_artifact.to_dict(),
                revision=WorkerStderrArtifact.SCHEMA_VERSION,
            ),
            model_action=True,
        )
        preflight = {
            "schema_version": fresh.FRESH_INTEGRATED_MODEL_CONTEXT_REVISION,
            "pilot_id": pilot.pilot_id,
            "policy_id": material["policy"]["policy_id"],
            "preflight_status": "prepared_no_model",
            "input_context": "model_native_full_context",
            "input_truncation": False,
            "output_truncation": False,
            "output_limit_kind": "context_remaining",
            "max_input_tokens": profile.max_input_tokens,
            "native_context_source": "fixture",
            "native_context_identity": local._identity(
                {"fixture": True},
                revision=fresh.FRESH_INTEGRATED_MODEL_CONTEXT_REVISION,
            ),
            "manifest_identity": local._identity(
                manifest.to_dict(),
                revision=manifest.SCHEMA_VERSION,
            ),
            "profile_identity": local._identity(
                profile.to_dict(),
                revision=profile.SCHEMA_VERSION,
            ),
            "model_root_identity": profile.model_root_identity,
            "model_inventory_identity": profile.model_inventory_identity,
            "action_state": material["policy"]["action_state"],
        }
        source_result = {
            "schema_version": fresh.FRESH_INTEGRATED_RESULT_SCHEMA_VERSION,
            "result_id": "pending",
            "status": "fresh_integrated_failed_closed",
            "pilot_id": pilot.pilot_id,
            "run_id": run_id,
            "policy_id": material["policy"]["policy_id"],
            "qualification_id": material["qualification"]["qualification_id"],
            "predecessor_link": material["policy"]["predecessor_link"],
            "source_savepoint_is_not_integrated_input": True,
            "integrated_input_source": (
                "fresh_same_run_live_validated_upstream_outputs_only"
            ),
            "fresh_empty_authority_state_identity": local._identity(
                phase4_create_authority_state(b_input),
                revision=fresh.FRESH_INTEGRATED_EMPTY_AUTHORITY_REVISION,
            ),
            "node_local_generate_allowed": False,
            "b_aux_disposition": "absent/not_requested",
            "retry_count": 0,
            "integrated_run_count": 1,
            "node_total_counts": {node_id: 1 for node_id in NODE_ORDER},
            "backend_generate_calls": 4,
            "model_generate_calls": 4,
            "model_action": True,
            "source_kind": "real_local_qwen",
            "raw_model_contract_success": False,
            "normalized_node_contract_success": False,
            "f4_normalization": "not_observed",
            "integrated_outcome": "failed_closed",
            "composition_status": "not_executed",
            "assembler_status": "not_executed",
            "integrated_node_raw_contract_pass_count": 3,
            "downstream": {
                "consistency": "not_executed",
                "acceptance": "not_executed",
                "g0": "not_executed",
                "package": "not_executed",
                "production_route": "not_executed",
                "repair": "not_executed",
            },
            "event_count": 0,
            "claim_boundary": (
                "assembler_only_no_acceptance_repair_g0_package_or_production"
            ),
        }
        source_result["result_id"] = local._identity(
            {
                key: value
                for key, value in source_result.items()
                if key != "result_id"
            },
            revision=fresh.FRESH_INTEGRATED_RESULT_SCHEMA_VERSION,
        )["sha256"]
        for filename, value in (
            ("local_qwen_load_receipt.json", load_receipt.to_dict()),
            ("execution_lease.json", lease.to_dict()),
            ("ledger.json", ledger.to_dict()),
            ("pilot_outcome.json", outcome.to_dict()),
            ("supervisor_receipt.json", supervisor.to_dict()),
            ("worker_stderr.json", stderr_artifact.to_dict()),
            (fresh.FRESH_INTEGRATED_PREFLIGHT_NAME, preflight),
            (fresh.FRESH_INTEGRATED_RESULT_NAME, source_result),
        ):
            self._write_json(source / filename, value)
        return source

    def _run(self, source: Path, name: str) -> tuple[Path, dict[str, object]]:
        result_root = self.TEST_ROOT / f"result-{name}"
        result = reval.revalidate_phase4_local_qwen_fresh_integrated(
            source_result_root=source,
            qualification_savepoint_root=self.QUALIFICATION_ROOT,
            result_root=result_root,
            confirm_no_model_revalidation=True,
        )
        return result_root, result

    def test_happy_path_is_amended_normalized_and_assembled(self) -> None:
        source = self._make_source("happy")
        result_root, result = self._run(source, "happy")
        self.assertEqual(
            result["status"],
            "amended_normalized_fresh_integrated_revalidation_assembled",
        )
        self.assertTrue(result["agent_chain_system_output_usable"])
        self.assertFalse(result["raw_model_contract_success"])
        self.assertTrue(result["normalized_node_contract_success"])
        self.assertEqual(result["source_model_generate_calls"], 4)
        self.assertEqual(result["revalidation_model_generate_calls"], 0)
        self.assertEqual(result["model_generate_calls"], 0)
        self.assertFalse(result["aggregate_budget_reset"])
        self.assertFalse(result["normalization_counts_as_repair"])
        self.assertEqual(result["historical_strict_result"], "0/2_unchanged")
        self.assertEqual(
            reval.validate_phase4_local_qwen_fresh_integrated_revalidation_result(
                result_root
            ),
            result,
        )
        self.assertEqual(
            set(path.name for path in result_root.iterdir()),
            set(reval.REVALIDATION_OUTPUT_NAMES),
        )

    def test_source_raw_hash_drift_fails_closed(self) -> None:
        source = self._make_source("raw-drift")
        raw_path = next(
            source.glob("runs/*/F4/attempt-01/raw_response.bin")
        )
        raw_path.write_bytes(raw_path.read_bytes() + b" ")
        with self.assertRaises(reval.Phase4LocalQwenFreshIntegratedRevalidationError):
            self._run(source, "raw-drift-result")

    def test_cross_run_pre_call_substitution_fails_closed(self) -> None:
        source_a = self._make_source("cross-a")
        source_b = self._make_source("cross-b")
        pre_a = next(source_a.glob("runs/*/F2/attempt-01/pre_call.json"))
        pre_b = next(source_b.glob("runs/*/F2/attempt-01/pre_call.json"))
        pre_a.write_bytes(pre_b.read_bytes())
        with self.assertRaises(reval.Phase4LocalQwenFreshIntegratedRevalidationError):
            self._run(source_a, "cross-result")

    def test_cross_run_action_and_result_substitution_fails_closed(self) -> None:
        source_a = self._make_source("cross-action-a")
        source_b = self._make_source("cross-action-b")
        action_a = next(source_a.glob("runs/*/F2/attempt-01/node_d17_action.json"))
        action_b = next(source_b.glob("runs/*/F2/attempt-01/node_d17_action.json"))
        action_a.write_bytes(action_b.read_bytes())
        with self.assertRaises(
            reval.Phase4LocalQwenFreshIntegratedRevalidationError
        ):
            self._run(source_a, "cross-action-result")

        source_c = self._make_source("cross-attempt-a")
        source_d = self._make_source("cross-attempt-b")
        result_c = next(source_c.glob("runs/*/F3/attempt-01/attempt_result.json"))
        result_d = next(source_d.glob("runs/*/F3/attempt-01/attempt_result.json"))
        result_c.write_bytes(result_d.read_bytes())
        with self.assertRaises(
            reval.Phase4LocalQwenFreshIntegratedRevalidationError
        ):
            self._run(source_c, "cross-attempt-result")

    def test_f1_f3_contract_failure_fails_closed(self) -> None:
        source = self._make_source("f2-failure", f1_f3_pass=False)
        with self.assertRaises(reval.Phase4LocalQwenFreshIntegratedRevalidationError):
            self._run(source, "f2-failure-result")

    def test_supervisor_teardown_failure_fails_closed(self) -> None:
        source = self._make_source("teardown", teardown_verified=False)
        with self.assertRaises(reval.Phase4LocalQwenFreshIntegratedRevalidationError):
            self._run(source, "teardown-result")

    def test_output_and_receipt_tamper_are_detected(self) -> None:
        source = self._make_source("tamper")
        result_root, _ = self._run(source, "tamper")
        normalized = result_root / reval.REVALIDATION_NORMALIZED_F4_NAME
        value = json.loads(normalized.read_text(encoding="utf-8"))
        value["acceptance_checks"][0]["description"] += " drift"
        normalized.write_bytes(local._canonical_bytes(value))
        with self.assertRaises(reval.Phase4LocalQwenFreshIntegratedRevalidationError):
            reval.validate_phase4_local_qwen_fresh_integrated_revalidation_result(
                result_root
            )

        source = self._make_source("receipt-tamper")
        result_root, _ = self._run(source, "receipt-tamper")
        receipt = result_root / reval.REVALIDATION_CORE_RECEIPT_NAME
        value = json.loads(receipt.read_text(encoding="utf-8"))
        value["raw_failure_code"] = "tampered"
        receipt.write_bytes(local._canonical_bytes(value))
        with self.assertRaises(reval.Phase4LocalQwenFreshIntegratedRevalidationError):
            reval.validate_phase4_local_qwen_fresh_integrated_revalidation_result(
                result_root
            )

    def test_source_root_is_never_written(self) -> None:
        source = self._make_source("source-read-only")
        before = {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in source.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        original = reval._write_once

        def guarded_write(root: Path, relative: str, raw: bytes) -> None:
            self.assertNotEqual(root.resolve(), source.resolve())
            original(root, relative, raw)

        with mock.patch.object(reval, "_write_once", side_effect=guarded_write):
            self._run(source, "source-read-only-result")
        after = {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in source.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        self.assertEqual(before, after)

    def test_result_root_is_write_once(self) -> None:
        source = self._make_source("write-once")
        result_root = self.TEST_ROOT / "result-write-once"
        self._run(source, "write-once")
        with self.assertRaises(
            reval.Phase4LocalQwenFreshIntegratedRevalidationError
        ):
            reval.revalidate_phase4_local_qwen_fresh_integrated(
                source_result_root=source,
                qualification_savepoint_root=self.QUALIFICATION_ROOT,
                result_root=result_root,
                confirm_no_model_revalidation=True,
            )


if __name__ == "__main__":
    unittest.main()
