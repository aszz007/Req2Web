"""Focused no-model tests for the local F4 bounded runner."""

from __future__ import annotations

import base64
import copy
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import req2web_runtime.phase4_local_qwen_f4 as f4
from req2web_orchestration.phase4_graph import (
    Phase4ContractError,
    REGISTRY_REVISION,
    phase4_assemble_candidate,
    phase4_compose_candidate,
    phase4_normalize_and_validate_f4_output,
    phase4_register_node_output,
    phase4_synthetic_assembler_bindings,
    phase4_validate_f4_normalization_receipt,
    phase4_validate_node_output,
)


class F4PolicyTests(unittest.TestCase):
    def test_builder_creates_a_self_bound_policy(self):
        payload = f4.build_f4_local_qwen_policy_payload()
        policy = f4.F4LocalQwenPolicy.from_dict(payload)
        self.assertEqual(policy.node["node_id"], "F4")
        self.assertEqual(policy.execution["generate_call_cap"], 1)
        self.assertEqual(policy.execution["generate_calls"], 0)
        self.assertTrue(policy.execution["pending_confirmation"])
        self.assertEqual(policy.execution["output_limit_kind"], "context_remaining")
        self.assertFalse(policy.execution["output_truncation"])
        self.assertTrue(policy.execution["raw_first"])
        self.assertFalse(policy.action_state["model_action"])
        self.assertEqual(policy.action_state["runtime_kind"], f4.F4_RUNTIME_KIND)
        self.assertFalse(policy.action_state["graph_runtime_execution"])
        self.assertFalse(policy.action_state["remote_action"])

    def test_tracked_policy_is_canonical(self):
        policy, raw = f4.load_f4_local_qwen_policy()
        self.assertEqual(raw, f4._canonical_bytes(policy.to_dict()))
        self.assertEqual(policy.pilot_id, f4.F4_LOCAL_PILOT_ID)


class F4ProjectionTests(unittest.TestCase):
    def test_f4_projection_has_only_f4_upstream_categories(self):
        policy = f4._f4_projection_policy()
        self.assertEqual(policy.node_id, "F4")
        self.assertEqual(list(policy.upstream_required_node_ids), ["F1", "F2", "F3"])
        self.assertEqual(policy.b_aux_disposition, "absent/not_requested")

    def test_prompt_is_candidate_only(self):
        policy = f4._f4_projection_policy()
        raw = f4._build_f4_prompt(input_bytes=b"{}", policy=policy)
        payload = json.loads(raw)
        self.assertEqual(payload["output_contract"]["exact_top_level_keys"], ["acceptance_checks"])
        self.assertTrue(any("Do not claim final Acceptance" in item for item in payload["instructions"]))

    def test_config_and_request_disable_truncation_and_retry(self):
        profile = SimpleNamespace(
            to_dict=lambda: {"profile": "local_smoke"},
            dtype="bfloat16",
            quantization="4bit_nf4_double_quant",
            compute_dtype="bfloat16",
            device_map={"0": "cuda:0"},
            cpu_offload=False,
            context_tokens=262144,
            max_new_tokens=262144,
        )
        native_context_identity = {
            "sha256": "sha256:" + "a" * 64,
            "byte_length": 1,
        }
        config = json.loads(
            f4._build_f4_config(
                profile=profile,
                native_context_identity=native_context_identity,
            )
        )
        request = json.loads(
            f4._build_f4_request(
                native_context_identity=native_context_identity,
            )
        )
        self.assertTrue(config["generation_stop_policy"]["truncation_is_failure"])
        self.assertIsNone(config["max_new_tokens"])
        self.assertEqual(config["output_limit_kind"], "context_remaining")
        self.assertEqual(
            config["full_attention"]["implementation"],
            f4.F4_MEMORY_EFFICIENT_ATTENTION_NAME,
        )
        self.assertFalse(config["full_attention"]["math_fallback_allowed"])
        self.assertTrue(config["full_attention"]["input_tokens_unchanged"])
        self.assertTrue(config["full_attention"]["output_tokens_unchanged"])
        self.assertEqual(config["generate_calls"], 0)
        self.assertEqual(config["retry_count"], 0)
        self.assertEqual(request["generate_call_cap"], 1)
        self.assertEqual(request["generate_calls"], 0)
        self.assertEqual(request["output_limit_kind"], "context_remaining")
        self.assertEqual(request["retry_count"], 0)

    def test_context_remaining_budget_uses_actual_input_length(self):
        profile = SimpleNamespace(context_tokens=262144)
        self.assertEqual(f4._context_remaining_budget(profile, 8280), 253864)
        with self.assertRaises(f4.Phase4LocalQwenF4ContractError):
            f4._context_remaining_budget(profile, 262144)

    def test_native_context_is_read_from_inventory_bound_config(self):
        config_raw = b'{"text_config":{"max_position_embeddings":262144}}'
        with patch.object(Path, "is_file", return_value=True), patch.object(
            Path, "is_symlink", return_value=False
        ), patch.object(Path, "read_bytes", return_value=config_raw):
            context_tokens, identity = f4._read_native_model_context(model_root=_ROOT)
        self.assertEqual(context_tokens, 262144)
        self.assertEqual(identity["identity_kind"], "canonical_json")
        self.assertEqual(identity["revision"], f4.F4_NATIVE_CONTEXT_REVISION)
        self.assertTrue(identity["sha256"].startswith("sha256:"))


class F4BoundaryFailureTests(unittest.TestCase):
    def test_native_context_boundary_without_remaining_budget_fails_closed(self):
        profile = SimpleNamespace(context_tokens=262144)
        with self.assertRaises(f4.Phase4LocalQwenF4ContractError):
            f4._context_remaining_budget(profile, 262144)

    def test_incomplete_json_is_not_a_successful_boundary_result(self):
        self.assertFalse(f4._f4_json_is_complete_single_object('{"acceptance_checks":['))
        success, failure = f4._finalize_success(
            False,
            teardown={"worker_exit_verified": True},
            stderr_snapshot={"completed": True, "error": None, "thread_joined": True},
        )
        self.assertFalse(success)
        self.assertIsNone(failure)

    def test_timeout_uses_stable_fail_closed_code(self):
        failure = f4.F4WorkerFailure("generation_timeout", "bounded timeout")
        self.assertEqual(failure.failure_code, "generation_timeout")
        self.assertIn(failure.failure_code, f4.F4_FAILURE_CODES)


class F4JSONStoppingTests(unittest.TestCase):
    class _Row:
        def __init__(self, values):
            self._values = values

        def tolist(self):
            return list(self._values)

    class _Tensor:
        def __init__(self, rows):
            self.rows = [list(row) for row in rows]
            self.shape = (len(self.rows), len(self.rows[0]) if self.rows else 0)

        def __getitem__(self, key):
            row_slice, column_slice = key
            rows = self.rows[row_slice]
            return F4JSONStoppingTests._Tensor(
                [row[column_slice] for row in rows]
            )

        def __iter__(self):
            return iter(F4JSONStoppingTests._Row(row) for row in self.rows)

    def test_stopper_ignores_prompt_and_stops_only_complete_object(self):
        tokenizer = SimpleNamespace(
            decode=lambda ids, skip_special_tokens=True: {
                (1,): '{"acceptance_checks":[]}',
                (2,): '{"acceptance_checks":',
            }[tuple(ids)]
        )
        stopper = f4.F4CompleteSingleJSONStoppingCriteria(
            tokenizer=tokenizer,
            prompt_length=1,
        )
        self.assertFalse(
            stopper(self._Tensor([[1, 2]]), None)
        )
        self.assertTrue(
            stopper(self._Tensor([[9, 1]]), None)
        )
        self.assertTrue(f4._f4_json_is_complete_single_object('{"a":1}'))
        self.assertFalse(f4._f4_json_is_complete_single_object('{"a":'))


class F4PriorFailureTests(unittest.TestCase):
    def test_prior_failure_binding_and_drift(self):
        final_raw = f4._canonical_bytes(
            {
                "status": "failed_closed",
                "node_model_pass": False,
                "generate_calls": 1,
                "previous_call_consumed": 1,
                "current_call_consumed": 1,
                "aggregate_after_call": 2,
                "raw": {"status": "not_captured"},
                "failure_code": "worker_generation_failed",
            }
        )
        key_names = {
            f4.F4_LOCAL_FINAL_RESULT_NAME,
            f4.F4_LOCAL_RESULT_NAME,
            f4.F4_LOCAL_MANIFEST_NAME,
            f4.F4_LOCAL_PRE_CALL_NAME,
            f4.F4_LOCAL_PRIOR_FAILURE_BINDING_NAME,
            "local_qwen_load_receipt.json",
        }

        def is_file(path):
            return path.name in key_names

        def read_bytes(path):
            return final_raw if path.name == f4.F4_LOCAL_FINAL_RESULT_NAME else b"{}"

        with patch.object(Path, "is_dir", new=lambda path: True), patch.object(
            Path, "is_symlink", new=lambda path: False
        ), patch.object(Path, "is_file", new=is_file), patch.object(
            Path, "read_bytes", new=read_bytes
        ), patch.object(
            Path, "resolve", new=lambda path, strict=False: path
        ):
            binding = f4.F4PriorFailureBinding.create(
                prior_result_root=_ROOT / "prior-failure-fixture"
            )
        self.assertEqual(binding.previous_call_consumed, 2)
        self.assertEqual(binding.aggregate_after_cap, 3)
        tampered = binding.to_dict()
        tampered["aggregate_after_cap"] = 2
        with self.assertRaises(f4.Phase4LocalQwenF4ContractError):
            f4.F4PriorFailureBinding.from_dict(tampered)


class F4SavepointReplayTests(unittest.TestCase):
    SAVEPOINT_ROOT = _ROOT / "fixtures" / "phase4" / "f3"

    def test_canonical_sidecar_needs_source_raw_live_replay(self):
        root = self.SAVEPOINT_ROOT
        self.assertTrue(root.is_dir())
        checkpoint = f4._f3.replay_remote_checkpoint(
            checkpoint_packet=root / f4._f3.REMOTE_CHECKPOINT_PACKET_NAME,
            checkpoint_receipt=root / f4._f3.REMOTE_CHECKPOINT_RECEIPT_NAME,
            prior_f3_failure=root / f4._f3.REMOTE_PRIOR_FAILURE_NAME,
        )
        sidecar_raw = (root / f4._f3.REMOTE_REVALIDATED_OUTPUT_NAME).read_bytes()
        sidecar = f4._strict_json(sidecar_raw, require_canonical=True)
        from req2web_orchestration.phase4_graph import (
            Phase4ContractError,
            phase4_validate_node_output,
        )

        with self.assertRaises(Phase4ContractError):
            phase4_validate_node_output("F3", sidecar, checkpoint.authority_state)
        replay = f4._replay_f3_savepoint_local(f3_result_root=root)
        self.assertEqual(replay[1], sidecar_raw)
        self.assertEqual(
            f4._canonical_bytes(f4._strict_json(replay[3], require_canonical=False)),
            replay[1],
        )

    def test_source_sidecar_and_state_drift_fail_closed(self):
        root = self.SAVEPOINT_ROOT
        original_reader = f4._remote_f4._read_input_file
        for drift_label in (
            "F3 source raw",
            "F3 revalidated output",
            "F3 revalidated state",
        ):
            def reader(path, label, *, _drift_label=drift_label):
                raw = original_reader(path, label)
                return raw + b" " if label == _drift_label else raw

            with self.subTest(drift_label=drift_label):
                with patch.object(f4._remote_f4, "_read_input_file", side_effect=reader):
                    with self.assertRaises(f4.Phase4LocalQwenF4ContractError):
                        f4._replay_f3_savepoint_local(f3_result_root=root)


class F4GenericAuditRefNormalizationTests(unittest.TestCase):
    SAVEPOINT_ROOT = _ROOT / "fixtures" / "phase4" / "f3"

    def state(self):
        return f4._replay_f3_savepoint_local(
            f3_result_root=self.SAVEPOINT_ROOT
        )[6]

    def unsorted_output(self, state):
        state_rows = [
            row
            for row in state["registry_inventory"]
            if row["node_id"] == "F2" and row["entity_type"] == "state"
        ]
        output = {
            "acceptance_checks": [
                {
                    "local_id": f"check-{index}",
                    "entity_type": "candidate_acceptance_check",
                    "description": (
                        "Verify the expected interface behavior for "
                        f"{use_case['title']}"
                    ),
                    "use_case_refs": [
                        {
                            "ref_type": "canonical_b_use_case",
                            "ref_id": use_case["use_case_id"],
                            "ref_revision": "canonical_b.use_case.v1",
                        }
                    ],
                    "state_ref": {
                        "ref_type": "registry_stable",
                        "ref_id": state_rows[
                            min(index - 1, len(state_rows) - 1)
                        ]["stable_id"],
                        "ref_revision": (
                            "req2web.phase4.registry.p4_02a.v1"
                        ),
                    },
                    "refs": [],
                }
                for index, use_case in enumerate(
                    state["b_input"]["use_cases"],
                    start=1,
                )
            ]
        }
        use_case = state["b_input"]["use_cases"][0]["use_case_id"]
        section = next(
            row
            for row in state["registry_inventory"]
            if row["node_id"] == "F1" and row["entity_type"] == "section"
        )
        component = next(
            row
            for row in state["registry_inventory"]
            if row["node_id"] == "F1" and row["entity_type"] == "component"
        )
        output["acceptance_checks"][0]["refs"] = [
            {
                "ref_type": "canonical_b_use_case",
                "ref_id": use_case,
                "ref_revision": "canonical_b.use_case.v1",
            },
            {
                "ref_type": "section",
                "ref_id": section["stable_id"],
                "ref_revision": "req2web.phase4.registry.p4_02a.v1",
            },
            {
                "ref_type": "component",
                "ref_id": component["stable_id"],
                "ref_revision": "req2web.phase4.registry.p4_02a.v1",
            },
        ]
        return output

    def legacy_shape_output(self, state):
        state_rows = [
            row
            for row in state["registry_inventory"]
            if row["node_id"] == "F2" and row["entity_type"] == "state"
        ]
        generic_rows = [
            row
            for row in state["registry_inventory"]
            if row["node_id"] == "F1"
            and row["entity_type"] == "component"
        ] + [
            row
            for row in state["registry_inventory"]
            if row["node_id"] == "F3"
            and row["entity_type"] == "interaction"
        ]
        self.assertTrue(state_rows)
        self.assertTrue(generic_rows)
        use_cases = state["b_input"]["use_cases"]
        output = {"acceptance_checks": []}
        for index in range(10):
            use_case = use_cases[index % len(use_cases)]
            target = generic_rows[index % len(generic_rows)]
            suffix = {
                "component": ".f1",
                "interaction": ".f3",
            }[target["entity_type"]]
            canonical_ref = {
                "ref_type": "canonical_b_use_case",
                "ref_id": use_case["use_case_id"],
                "ref_revision": "canonical_b.use_case.v1",
            }
            generic_ref = {
                "ref_type": target["entity_type"],
                "ref_id": target["stable_id"],
                "ref_revision": REGISTRY_REVISION + suffix,
            }
            state_ref = {
                "ref_type": "state",
                "ref_id": state_rows[0]["stable_id"],
                "ref_revision": REGISTRY_REVISION + ".f2",
            }
            refs = [canonical_ref, generic_ref, state_ref]
            if index < 5:
                refs = [canonical_ref, state_ref, generic_ref]
            output["acceptance_checks"].append(
                {
                    "local_id": f"ac_{index + 1:02d}",
                    "entity_type": "candidate_acceptance_check",
                    "description": (
                        "Verify the generated interface behavior for "
                        f"{use_case['use_case_id']} and check {index + 1}"
                    ),
                    "use_case_refs": [use_case["use_case_id"]],
                    "state_ref": state_rows[0]["stable_id"],
                    "refs": refs,
                }
            )
        return output

    def p4_01b_output_and_receipt(self):
        state = self.state()
        output = self.legacy_shape_output(state)
        raw = json.dumps(output, ensure_ascii=False).encode("utf-8")
        normalized, receipt = phase4_normalize_and_validate_f4_output(
            raw_bytes=raw,
            output=output,
            state=state,
        )
        return state, output, raw, normalized, receipt

    def test_normalized_refs_reach_composition_and_assembler(self):
        state = self.state()
        output = self.unsorted_output(state)
        raw = json.dumps(output, ensure_ascii=False, indent=2).encode("utf-8")
        with self.assertRaisesRegex(Phase4ContractError, "order or uniqueness"):
            phase4_validate_node_output("F4", output, state)
        normalized, receipt = phase4_normalize_and_validate_f4_output(
            raw_bytes=raw,
            output=output,
            state=state,
        )
        self.assertFalse(receipt["raw_model_contract_success"])
        self.assertTrue(receipt["normalized_node_contract_success"])
        self.assertEqual(receipt["normalization_count"], 1)
        self.assertFalse(receipt["normalization_counts_as_repair"])
        phase4_validate_f4_normalization_receipt(
            receipt,
            raw_bytes=raw,
            normalized_output=normalized,
            state=state,
        )
        registered = phase4_register_node_output(state, "F4", normalized)
        candidate = phase4_compose_candidate(registered)
        candidate_bytes = base64.b64decode(
            candidate["model_semantic_candidate_canonical_b64"],
            validate=True,
        )
        context, guidance = phase4_synthetic_assembler_bindings(registered)
        assembled = phase4_assemble_candidate(
            candidate_bytes,
            context,
            guidance,
        )
        self.assertEqual(
            assembled.page_spec.schema_version,
            "req2web.page_spec.v1",
        )

    def test_p4_01b_normalizes_real_shape_and_full_chain(self):
        state, output, raw, normalized, receipt = (
            self.p4_01b_output_and_receipt()
        )
        with self.assertRaises(Phase4ContractError):
            phase4_validate_node_output("F4", output, state)
        self.assertEqual(
            receipt["normalizer_revision"],
            "req2web.phase4.f4_generic_audit_ref_ownership_normalization.p4_01b.v1",
        )
        self.assertFalse(receipt["raw_model_contract_success"])
        self.assertTrue(receipt["normalized_node_contract_success"])
        self.assertEqual(receipt["ownership_operation_count"], 40)
        self.assertEqual(receipt["sort_operation_count"], 5)
        self.assertEqual(receipt["normalization_count"], 45)
        self.assertEqual(receipt["model_generate_calls"], 0)
        self.assertEqual(receipt["retry_count"], 0)
        self.assertFalse(receipt["repair_attempted"])
        self.assertFalse(receipt["normalization_counts_as_repair"])
        phase4_validate_f4_normalization_receipt(
            receipt,
            raw_bytes=raw,
            normalized_output=normalized,
            state=state,
        )
        for check in normalized["acceptance_checks"]:
            self.assertTrue(
                all(
                    isinstance(ref, dict)
                    for ref in check["use_case_refs"]
                )
            )
            self.assertIsInstance(check["state_ref"], dict)
            self.assertTrue(
                all(
                    ref["ref_revision"] == REGISTRY_REVISION
                    or ref["ref_revision"] == "canonical_b.use_case.v1"
                    for ref in check["refs"]
                )
            )
        registered = phase4_register_node_output(state, "F4", normalized)
        candidate = phase4_compose_candidate(registered)
        candidate_bytes = base64.b64decode(
            candidate["model_semantic_candidate_canonical_b64"],
            validate=True,
        )
        context, guidance = phase4_synthetic_assembler_bindings(registered)
        assembled = phase4_assemble_candidate(
            candidate_bytes,
            context,
            guidance,
        )
        self.assertEqual(
            assembled.page_spec.schema_version,
            "req2web.page_spec.v1",
        )

    def test_p4_01b_rejects_wrong_suffix_and_id_only_repair(self):
        state = self.state()
        for mutation, message in (
            (
                lambda ref: ref.update(
                    {"ref_revision": REGISTRY_REVISION + ".f9"}
                ),
                "legacy generic ref alias is invalid",
            ),
            (
                lambda ref: ref.update(
                    {
                        "ref_type": "state",
                        "ref_revision": REGISTRY_REVISION + ".f2",
                    }
                ),
                "legacy generic ref target is invalid",
            ),
        ):
            with self.subTest(message=message):
                output = self.legacy_shape_output(state)
                target = next(
                    ref
                    for ref in output["acceptance_checks"][0]["refs"]
                    if ref["ref_type"] in {"component", "interaction"}
                )
                mutation(target)
                raw = json.dumps(output, ensure_ascii=False).encode("utf-8")
                with self.assertRaisesRegex(
                    Phase4ContractError,
                    message,
                ):
                    phase4_normalize_and_validate_f4_output(
                        raw_bytes=raw,
                        output=output,
                        state=state,
                    )

    def test_p4_01b_rejects_mixed_shape_and_enforces_revisions(self):
        state = self.state()
        output = self.legacy_shape_output(state)
        output["acceptance_checks"][0]["use_case_refs"][0] = {
            "ref_type": "canonical_b_use_case",
            "ref_id": state["b_input"]["use_cases"][0]["use_case_id"],
            "ref_revision": "canonical_b.use_case.v1",
        }
        raw = json.dumps(output, ensure_ascii=False).encode("utf-8")
        with self.assertRaisesRegex(
            Phase4ContractError,
            "must not mix ref shapes",
        ):
            phase4_normalize_and_validate_f4_output(
                raw_bytes=raw,
                output=output,
                state=state,
            )

        _, _, _, normalized, _ = self.p4_01b_output_and_receipt()
        normalized["acceptance_checks"][0]["use_case_refs"][0][
            "ref_revision"
        ] = "canonical_b.use_case.v0"
        with self.assertRaisesRegex(
            Phase4ContractError,
            "F4 use-case ref type is invalid",
        ):
            phase4_validate_node_output("F4", normalized, state)
        _, _, _, normalized, _ = self.p4_01b_output_and_receipt()
        normalized["acceptance_checks"][0]["state_ref"][
            "ref_revision"
        ] = REGISTRY_REVISION + ".f2"
        with self.assertRaisesRegex(
            Phase4ContractError,
            "F4 state ref is invalid",
        ):
            phase4_validate_node_output("F4", normalized, state)

    def test_p4_01b_receipt_replay_tamper_and_non_allowlisted_drift_fail(self):
        state, _, raw, normalized, receipt = self.p4_01b_output_and_receipt()
        replay_output, replay_receipt = (
            phase4_normalize_and_validate_f4_output(
                raw_bytes=raw,
                output=json.loads(raw.decode("utf-8")),
                state=state,
            )
        )
        self.assertEqual(replay_output, normalized)
        self.assertEqual(replay_receipt, receipt)

        tampered_receipt = copy.deepcopy(receipt)
        tampered_receipt["operations"][0]["after"]["ref_id"] = "UC-99"
        with self.assertRaises(Phase4ContractError):
            phase4_validate_f4_normalization_receipt(
                tampered_receipt,
                raw_bytes=raw,
                normalized_output=normalized,
                state=state,
            )

        tampered_output = copy.deepcopy(normalized)
        tampered_output["acceptance_checks"][0]["description"] += " drift"
        with self.assertRaises(Phase4ContractError):
            phase4_validate_f4_normalization_receipt(
                receipt,
                raw_bytes=raw,
                normalized_output=tampered_output,
                state=state,
            )

    def test_duplicate_or_unknown_ref_still_fails_closed(self):
        state = self.state()
        duplicate = self.unsorted_output(state)
        duplicate["acceptance_checks"][0]["refs"].append(
            dict(duplicate["acceptance_checks"][0]["refs"][0])
        )
        raw = json.dumps(duplicate, ensure_ascii=False).encode("utf-8")
        with self.assertRaisesRegex(Phase4ContractError, "duplicates"):
            phase4_normalize_and_validate_f4_output(
                raw_bytes=raw,
                output=duplicate,
                state=state,
            )

        unknown = self.unsorted_output(state)
        unknown["acceptance_checks"][0]["refs"][1]["ref_id"] = (
            "p4-f1-section-0000000000000000"
        )
        raw = json.dumps(unknown, ensure_ascii=False).encode("utf-8")
        with self.assertRaisesRegex(Phase4ContractError, "target is invalid"):
            phase4_normalize_and_validate_f4_output(
                raw_bytes=raw,
                output=unknown,
                state=state,
            )

    def test_semantic_use_case_order_is_never_normalized(self):
        state = self.state()
        output = self.unsorted_output(state)
        output["acceptance_checks"][0]["use_case_refs"].append(
            {
                "ref_type": "canonical_b_use_case",
                "ref_id": state["b_input"]["use_cases"][1]["use_case_id"],
                "ref_revision": "canonical_b.use_case.v1",
            }
        )
        output["acceptance_checks"][0]["use_case_refs"].reverse()
        raw = json.dumps(output, ensure_ascii=False).encode("utf-8")
        with self.assertRaises(Phase4ContractError):
            phase4_normalize_and_validate_f4_output(
                raw_bytes=raw,
                output=output,
                state=state,
            )


class F4TeardownTests(unittest.TestCase):
    def test_success_downgrades_without_verified_teardown(self):
        success, failure = f4._finalize_success(
            True,
            teardown={"worker_exit_verified": False},
            stderr_snapshot={"completed": True, "error": None, "thread_joined": True},
        )
        self.assertFalse(success)
        self.assertEqual(failure, "teardown_unverified")

    def test_success_requires_complete_stderr_evidence(self):
        success, failure = f4._finalize_success(
            True,
            teardown={"worker_exit_verified": True},
            stderr_snapshot={"completed": False, "error": None, "thread_joined": False},
        )
        self.assertFalse(success)
        self.assertEqual(failure, "teardown_unverified")


class F4SpawnCleanupTests(unittest.TestCase):
    class _ErrorStream:
        def __init__(self):
            self.buffer = io.BytesIO()

    class _FakeProcess:
        def __init__(self, loaded_line):
            self.pid = 4567
            self.returncode = None
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(loaded_line)
            self.stderr = F4SpawnCleanupTests._ErrorStream()
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = 23

        def kill(self):
            self.killed = True
            self.returncode = 9

        def wait(self, timeout=None):
            if self.returncode is None:
                self.returncode = 23
            return self.returncode

    def _profile(self):
        return f4._local.LocalQwenProfile.create(
            model_root_identity=f4._identity({"root": "model"}, revision="test.root"),
            model_inventory_identity=f4._identity({"files": 1}, revision="test.inventory"),
            model_file_count=1,
        )

    def test_spawn_identity_failure_terminates_and_reaps_worker(self):
        loaded = f4._canonical_bytes(
            {
                "protocol": f4.F4_LOCAL_WORKER_PROTOCOL,
                "kind": "loaded",
                "worker_id": "worker-fake",
                "worker_pid": 9999,
                "loaded_facts": {},
            }
        ).decode("utf-8") + "\n"
        directory = _ROOT
        process = self._FakeProcess(loaded)
        with patch.object(f4._local, "_supervised_worker_python_runtime", return_value=("python", "path")):
            with patch.object(f4.subprocess, "Popen", return_value=process):
                with self.assertRaises(f4.F4WorkerStartFailure) as captured:
                    f4.start_f4_local_worker(
                        model_root=directory,
                        profile=self._profile(),
                        mirror=f4.F4StreamMirror(io.StringIO()),
                    )
        self.assertEqual(captured.exception.failure_code, "worker_identity_failed")
        self.assertTrue(process.terminated or process.killed)
        self.assertTrue(captured.exception.teardown["worker_exit_verified"])


class F4StreamTests(unittest.TestCase):
    def test_stream_is_visible_but_kept_separate_from_raw(self):
        output = io.StringIO()
        mirror = f4.F4StreamMirror(output)
        mirror.feed(
            f4._canonical_bytes(
                {
                    "schema_version": f4._local.P4D1_STREAM_EVENT_SCHEMA_VERSION,
                    "event": "token_delta",
                    "delta_b64": base64.b64encode(b'{"acceptance_checks":').decode("ascii"),
                }
            )
        )
        self.assertEqual(mirror.partial_bytes, b'{"acceptance_checks":')
        self.assertIn('{"acceptance_checks":', output.getvalue())

    def test_non_token_stream_event_cannot_carry_delta(self):
        mirror = f4.F4StreamMirror(io.StringIO())
        with self.assertRaises(f4.Phase4LocalQwenF4ContractError):
            mirror.feed(
                f4._canonical_bytes(
                    {
                        "schema_version": f4._local.P4D1_STREAM_EVENT_SCHEMA_VERSION,
                        "event": "generation_started",
                        "delta_b64": base64.b64encode(b"bad").decode("ascii"),
                    }
                )
            )


class F4CliTests(unittest.TestCase):
    def test_confirmation_is_required(self):
        from scripts import run_phase4_local_qwen_f4_stream_diagnostic as script

        with patch.object(script, "run_phase4_local_qwen_f4") as runner:
            with self.assertRaises(SystemExit) as captured:
                script.main([])
        self.assertEqual(captured.exception.code, 2)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
