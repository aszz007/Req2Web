from __future__ import annotations

import base64
import copy
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


try:
    import langgraph  # type: ignore[import-not-found]
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise

    class _StubInMemorySaver:
        pass

    class _StubStateGraph:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def add_node(self, *_: object, **__: object) -> None:
            pass

        def add_edge(self, *_: object, **__: object) -> None:
            pass

        def add_conditional_edges(self, *_: object, **__: object) -> None:
            pass

        def compile(self, *_: object, **__: object) -> object:
            return object()

    _langgraph = types.ModuleType("langgraph")
    _langgraph.__path__ = []  # type: ignore[attr-defined]
    _checkpoint = types.ModuleType("langgraph.checkpoint")
    _checkpoint.__path__ = []  # type: ignore[attr-defined]
    _memory = types.ModuleType("langgraph.checkpoint.memory")
    _memory.InMemorySaver = _StubInMemorySaver
    _graph = types.ModuleType("langgraph.graph")
    _graph.END = "__END__"
    _graph.START = "__START__"
    _graph.StateGraph = _StubStateGraph
    sys.modules.update(
        {
            "langgraph": _langgraph,
            "langgraph.checkpoint": _checkpoint,
            "langgraph.checkpoint.memory": _memory,
            "langgraph.graph": _graph,
        }
    )


import req2web_runtime.phase4_remote_qwen as remote_base
import req2web_runtime.phase4_remote_qwen_fresh_integrated as remote
from req2web_agent import prompt_authority_manifest
from req2web_orchestration.phase4_graph import (
    phase4_create_mapping,
    phase4_create_portable_authority_state,
    phase4_project_node_input_authority,
    phase4_register_node_output,
    phase4_synthetic_fixture_output,
    synthetic_commerce_b_input,
)


def _identity(value: object) -> dict[str, object]:
    return remote._identity(value, revision="test.v1")


def _fake_inventory() -> dict[str, object]:
    return {
        "schema_version": remote_base.REMOTE_INVENTORY_SCHEMA_VERSION,
        "model_root_identity": _identity({"root": "test"}),
        "inventory_identity": _identity({"files": 16}),
        "file_count": remote_base.REMOTE_FORMAL_FILE_COUNT,
    }


def _fake_runtime() -> dict[str, object]:
    return {
        "python_version": "3.12.0",
        "transformers_version": remote_base.REMOTE_TRANSFORMERS_VERSION,
        "torch_version": remote_base.REMOTE_TORCH_VERSION,
        "accelerate_version": remote_base.REMOTE_ACCELERATE_VERSION,
    }


def _fake_gpu() -> dict[str, object]:
    return {
        "device_name": remote_base.REMOTE_DEVICE_NAME,
        "device_uuid": "GPU-test-p4-05",
        "total_vram_bytes": remote_base.REMOTE_MIN_VRAM_BYTES,
        "free_vram_bytes": remote_base.REMOTE_MIN_VRAM_BYTES - 1,
        "driver_version": "test-driver",
        "cuda_version": "12.8",
    }


def _fake_graph_bound_delivery() -> dict[str, object]:
    return {
        "materials": object(),
        "live": {},
        "context": object(),
        "guidance": object(),
        "binding": {
            "schema_version": "req2web.phase4.graph_bound_delivery_materials.v1",
            "case_id": remote.P4_05_CASE_ID,
            "graph_assembler_bindings_exact": True,
            "model_loaded": False,
            "run_occurred": False,
        },
    }


class Phase4RemoteFreshIntegratedProfileTests(unittest.TestCase):
    def test_node_prompts_expose_exact_contracts_without_semantic_examples(self) -> None:
        input_bytes = remote._canonical_bytes(
            {
                "schema_version": remote.P4_05_INPUT_SCHEMA_VERSION,
                "node_id": "F1",
            }
        )

        f1 = json.loads(remote._node_prompt(node_id="F1", input_bytes=input_bytes))
        shared_manifest = prompt_authority_manifest()
        f4_contract = shared_manifest["output_contracts"]["F4"]
        with self.assertRaisesRegex(
            remote.Phase4RemoteFreshIntegratedError,
            "replay-only",
        ):
            remote._node_prompt(
                node_id="F3",
                input_bytes=input_bytes,
                prompt_revision=remote.P4_05_F3_F4_PROMPT_REVISION,
            )
        b_input = synthetic_commerce_b_input()
        state = phase4_create_portable_authority_state(b_input)
        for upstream_node_id in ("F1", "F2"):
            state = phase4_register_node_output(
                state,
                upstream_node_id,
                phase4_synthetic_fixture_output(upstream_node_id, state),
            )
        f3_full_input_bytes = remote._node_input(
            node_id="F3",
            b_input=b_input,
            state=state,
            authority_projection=phase4_project_node_input_authority(
                state,
                "F3",
            ),
        )
        f3_full_direct = json.loads(
            remote._node_prompt(
                node_id="F3",
                input_bytes=f3_full_input_bytes,
                prompt_revision=remote.P4_05_FULL_DIRECT_PROMPT_REVISION,
            )
        )
        state = phase4_register_node_output(
            state,
            "F3",
            phase4_synthetic_fixture_output("F3", state),
        )
        phase4_create_mapping(state)
        f4_full_input_bytes = remote._node_input(
            node_id="F4",
            b_input=b_input,
            state=state,
            authority_projection=phase4_project_node_input_authority(
                state,
                "F4",
            ),
        )
        f4_full_direct = json.loads(
            remote._node_prompt(
                node_id="F4",
                input_bytes=f4_full_input_bytes,
                prompt_revision=remote.P4_05_FULL_DIRECT_PROMPT_REVISION,
            )
        )

        self.assertEqual(
            f1["schema_version"],
            remote.P4_05_PROMPT_SCHEMA_VERSION,
        )
        self.assertEqual(
            f1["exact_output_contract"]["section_constants"],
            {"entity_type": "section", "refs": []},
        )
        self.assertIn(
            "components array order exactly equals the concatenation of sections[].component_local_ids",
            f1["exact_output_contract"]["invariants"],
        )
        self.assertEqual(
            f4_contract["acceptance_check_constants"],
            {"entity_type": "candidate_acceptance_check", "refs": []},
        )
        self.assertIn(
            "reference_exact_keys",
            f4_contract,
        )
        self.assertEqual(
            f4_contract["field_sources"]["use_case_refs"],
            {
                "source": "supplied canonical B use-case view",
                "required_ref_type": "canonical_b_use_case",
                "required_ref_revision": "canonical_b.use_case.v1",
                "forbidden_ref_type": "registry_stable",
            },
        )
        self.assertEqual(
            f4_contract["field_sources"]["state_ref"][
                "required_ref_revision"
            ],
            "req2web.phase4.registry.p4_02a.v1",
        )
        self.assertEqual(
            f3_full_direct["prompt_revision"],
            remote.P4_05_FULL_DIRECT_PROMPT_REVISION,
        )
        self.assertIn(
            "for every adjacent pair in supplied F2 state order, include at least one forward transition from the earlier state to the later state",
            f3_full_direct["exact_output_contract"]["invariants"],
        )
        self.assertIn(
            "let N be the number of supplied F2 states and emit exactly 2*N-1 interactions",
            f3_full_direct["exact_output_contract"]["invariants"],
        )
        self.assertIn(
            "use this exact interaction order: same-state work for state 0, forward transition state 0 to state 1, same-state work for state 1, then continue alternating until same-state work for the final state",
            f3_full_direct["exact_output_contract"]["invariants"],
        )
        f3_input = json.loads(f3_full_input_bytes)
        state_local_ids = [
            row["local_id"]
            for row in f3_input["projection"][
                "f2_registered_state_visibility_view"
            ]["states"]
        ]
        expected_plan: list[tuple[str, str, str]] = []
        for state_index, state_local_id in enumerate(state_local_ids):
            expected_plan.append(
                ("same_state_work", state_local_id, state_local_id)
            )
            if state_index + 1 < len(state_local_ids):
                expected_plan.append(
                    (
                        "forward_transition",
                        state_local_id,
                        state_local_ids[state_index + 1],
                    )
                )
        self.assertEqual(
            [
                (
                    row["transition_kind"],
                    row["source_state_local_id"],
                    row["target_state_local_id"],
                )
                for row in f3_full_direct["required_interaction_plan"]
            ],
            expected_plan,
        )
        self.assertEqual(
            f4_full_direct["prompt_revision"],
            remote.P4_05_FULL_DIRECT_PROMPT_REVISION,
        )
        self.assertIn(
            "derive every state_ref from the actual supplied F3 interaction plan; the selected stable state ID must be an actual validated target for that use case",
            f4_full_direct["exact_output_contract"]["invariants"],
        )
        f4_input = json.loads(f4_full_input_bytes)
        f4_use_cases = f4_input["projection"]["canonical_b_use_case_view"][
            "use_cases"
        ]
        f4_states = f4_input["projection"][
            "f2_registered_state_visibility_view"
        ]["states"]
        self.assertEqual(
            [
                (
                    row["use_case_ref"]["ref_id"],
                    row["state_ref"]["ref_id"],
                )
                for row in f4_full_direct[
                    "required_acceptance_target_plan"
                ]
            ],
            [
                (
                    use_case["use_case_id"],
                    f4_states[min(index, len(f4_states) - 1)]["stable_id"],
                )
                for index, use_case in enumerate(f4_use_cases)
            ],
        )

    def test_f4_direct_acceptance_receipt_is_replayable_and_non_rewriting(
        self,
    ) -> None:
        b_input = synthetic_commerce_b_input()
        state = phase4_create_portable_authority_state(b_input)
        for node_id in ("F1", "F2"):
            state = phase4_register_node_output(
                state,
                node_id,
                phase4_synthetic_fixture_output(node_id, state),
            )
        f3 = phase4_synthetic_fixture_output("F3", state)
        f3["interactions"][0]["target_state_local_id"] = "state-checkout"
        state = phase4_register_node_output(state, "F3", f3)
        phase4_create_mapping(state)
        input_bytes = remote._node_input(
            node_id="F4",
            b_input=b_input,
            state=state,
            authority_projection=phase4_project_node_input_authority(
                state,
                "F4",
            ),
        )
        output = phase4_synthetic_fixture_output("F4", state)
        before = copy.deepcopy(output)
        raw = remote._canonical_bytes(output)

        receipt = remote._f4_direct_acceptance_policy_receipt(
            input_bytes=input_bytes,
            output=output,
            raw_bytes=raw,
        )

        self.assertEqual(output, before)
        self.assertIs(receipt["automatic_rewrite"], False)
        self.assertIs(receipt["automatic_retry"], False)
        self.assertEqual(receipt["a07b_status"], "not_executed_by_policy")
        self.assertEqual(len(receipt["selected_targets"]), 2)

    def test_f4_direct_acceptance_receipt_rejects_unreachable_or_backward_targets(
        self,
    ) -> None:
        b_input = synthetic_commerce_b_input()
        state = phase4_create_portable_authority_state(b_input)
        for node_id in ("F1", "F2"):
            state = phase4_register_node_output(
                state,
                node_id,
                phase4_synthetic_fixture_output(node_id, state),
            )
        f3 = phase4_synthetic_fixture_output("F3", state)
        f3["interactions"][0]["target_state_local_id"] = "state-checkout"
        state = phase4_register_node_output(state, "F3", f3)
        phase4_create_mapping(state)
        input_bytes = remote._node_input(
            node_id="F4",
            b_input=b_input,
            state=state,
            authority_projection=phase4_project_node_input_authority(
                state,
                "F4",
            ),
        )
        output = phase4_synthetic_fixture_output("F4", state)
        state_rows = [
            row
            for row in state["registry_inventory"]
            if row["node_id"] == "F2"
        ]
        initial_state_id = state_rows[0]["stable_id"]
        checkout_state_id = state_rows[1]["stable_id"]
        final_state_id = state_rows[2]["stable_id"]

        unreachable = copy.deepcopy(output)
        unreachable["acceptance_checks"][0]["state_ref"]["ref_id"] = (
            initial_state_id
        )
        with self.assertRaisesRegex(
            remote.Phase4RemoteFreshIntegratedError,
            "actual reachable mapped interaction target",
        ):
            remote._f4_direct_acceptance_policy_receipt(
                input_bytes=input_bytes,
                output=unreachable,
                raw_bytes=remote._canonical_bytes(unreachable),
            )

        backward = copy.deepcopy(output)
        backward["acceptance_checks"][0]["state_ref"]["ref_id"] = final_state_id
        backward["acceptance_checks"][1]["state_ref"]["ref_id"] = (
            checkout_state_id
        )
        with self.assertRaisesRegex(
            remote.Phase4RemoteFreshIntegratedError,
            "target order is not monotonic",
        ):
            remote._f4_direct_acceptance_policy_receipt(
                input_bytes=input_bytes,
                output=backward,
                raw_bytes=remote._canonical_bytes(backward),
            )

    def test_profile_is_exact_bf16_gpu0_no_offload_profile(self) -> None:
        profile = remote.RemoteFreshIntegratedProfile.create(
            inventory=_fake_inventory(),
            runtime_facts=_fake_runtime(),
            gpu_facts=_fake_gpu(),
        )

        profile.validate()
        data = profile.to_dict()
        self.assertEqual(data["device_name"], remote_base.REMOTE_DEVICE_NAME)
        self.assertEqual(data["device_index"], 0)
        self.assertEqual(data["dtype"], "bfloat16")
        self.assertEqual(data["compute_dtype"], "bfloat16")
        self.assertEqual(data["quantization"], "none")
        self.assertIs(data["cpu_offload"], False)
        self.assertIs(data["local_files_only"], True)
        self.assertIs(data["offline"], True)
        self.assertIs(data["network"], False)
        self.assertIs(data["model_loaded"], False)
        self.assertIs(data["run_occurred"], False)

    def test_profile_rejects_runtime_or_placement_drift(self) -> None:
        profile = remote.RemoteFreshIntegratedProfile.create(
            inventory=_fake_inventory(),
            runtime_facts=_fake_runtime(),
            gpu_facts=_fake_gpu(),
        )
        tampered = profile.to_dict()
        tampered["quantization"] = "4bit_nf4"
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            remote.RemoteFreshIntegratedProfile.from_dict(tampered)

    def test_stability_profile_binding_excludes_only_dynamic_free_vram(self) -> None:
        profile = remote.RemoteFreshIntegratedProfile.create(
            inventory=_fake_inventory(),
            runtime_facts=_fake_runtime(),
            gpu_facts=_fake_gpu(),
        )
        stable = profile.to_dict()
        stable.pop("profile_id")
        stable.pop("free_vram_bytes_at_preflight")
        expected = remote._identity(
            stable,
            revision=remote.P4_05_STABILITY_PROFILE_BINDING_SCHEMA_VERSION,
        )

        self.assertTrue(
            remote._profile_matches_expected_identity(
                profile,
                {"full_profile_identity": "intentionally-not-compared"},
                expected,
            )
        )

        tampered = profile.to_dict()
        tampered["torch_version"] = "drifted"
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            remote.RemoteFreshIntegratedProfile.from_dict(tampered)


class Phase4RemoteFreshIntegratedPolicyTests(unittest.TestCase):
    def test_policy_reuses_fresh_graph_contract_and_freezes_budget(self) -> None:
        profile = remote.RemoteFreshIntegratedProfile.create(
            inventory=_fake_inventory(),
            runtime_facts=_fake_runtime(),
            gpu_facts=_fake_gpu(),
        )
        policy = remote.create_p4_05_policy(
            run_id=f"{remote.P4_05_RUN_PREFIX}test",
            result_root_marker=remote.P4_05_ROOT_MARKER,
            profile=profile,
        )

        self.assertEqual(policy["node_order"], ["F1", "F2", "F3", "F4"])
        self.assertEqual(policy["node_call_cap"], {"F1": 1, "F2": 1, "F3": 1, "F4": 1})
        self.assertEqual(policy["retry_count"], 0)
        self.assertEqual(
            policy["fresh_graph_contract"]["schema_version"],
            "req2web.phase4.local_qwen.fresh_integrated_pilot.policy.v1",
        )
        self.assertIs(policy["action_state"]["model_action"], False)
        self.assertEqual(
            policy["policy_id"],
            remote._identity(
                {key: value for key, value in policy.items() if key != "policy_id"},
                revision=remote.P4_05_POLICY_SCHEMA_VERSION,
            )["sha256"],
        )

    def test_policy_binds_an_explicit_parent_stability_experiment(self) -> None:
        profile = remote.RemoteFreshIntegratedProfile.create(
            inventory=_fake_inventory(),
            runtime_facts=_fake_runtime(),
            gpu_facts=_fake_gpu(),
        )
        parent = {
            "schema_version": remote.P4_05_PARENT_BINDING_SCHEMA_VERSION,
            "experiment_id": "p4-05-commerce-stability-10",
            "experiment_run_id": "p4-05-stability-run-test",
            "experiment_policy_identity": remote._identity(
                {"policy": "stability-test"},
                revision="req2web.phase4.p4_05.remote_qwen_stability.policy.v1",
            ),
            "case_index": 1,
            "case_id": "p4-05-stability-01-grocery",
            "request_id": "p4-05-stability-request-01",
        }

        roundtripped_parent = json.loads(
            remote._canonical_bytes(parent).decode("utf-8")
        )
        policy = remote.create_p4_05_policy(
            run_id=f"{remote.P4_05_RUN_PREFIX}stability-child",
            result_root_marker=remote.P4_05_ROOT_MARKER,
            profile=profile,
            case_id=str(parent["case_id"]),
            request_id=str(parent["request_id"]),
            parent_experiment_binding=roundtripped_parent,
        )

        self.assertEqual(
            policy["parent_experiment_binding"],
            roundtripped_parent,
        )
        invalid = copy.deepcopy(parent)
        invalid["case_id"] = "wrong-case"
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            remote.create_p4_05_policy(
                run_id=f"{remote.P4_05_RUN_PREFIX}stability-child-invalid",
                result_root_marker=remote.P4_05_ROOT_MARKER,
                profile=profile,
                case_id=str(parent["case_id"]),
                request_id=str(parent["request_id"]),
                parent_experiment_binding=invalid,
            )


class Phase4RemoteFreshIntegratedArtifactTests(unittest.TestCase):
    def test_preflight_writes_only_offline_no_model_artifacts(self) -> None:
        result_root = _ROOT / ".p4-05-test-only-preflight-result"
        with patch.object(
            remote._remote,
            "validate_remote_model_inventory",
            return_value=_fake_inventory(),
        ), patch.object(
            remote._remote,
            "_collect_remote_runtime_facts",
            return_value=_fake_runtime(),
        ), patch.object(
            remote._remote,
            "_probe_remote_gpu_facts",
            return_value=_fake_gpu(),
        ), patch.object(
            remote,
            "_prepare_graph_bound_delivery_materials",
            return_value=_fake_graph_bound_delivery(),
        ), patch.object(
            remote,
            "_write_fsync",
        ) as write_artifact, patch.object(
            Path,
            "mkdir",
            return_value=None,
        ):
            prepared = remote.prepare_phase4_remote_qwen_fresh_integrated(
                model_root=_ROOT,
                integrity_evidence=_ROOT / "AGENTS.md",
                result_root=result_root,
                run_id=f"{remote.P4_05_RUN_PREFIX}preflight",
            )

        self.assertEqual(prepared["run_id"], f"{remote.P4_05_RUN_PREFIX}preflight")
        self.assertIs(prepared["profile"].model_loaded, False)
        self.assertIs(prepared["profile"].run_occurred, False)
        written_names = {call.args[0].name for call in write_artifact.call_args_list}
        self.assertIn("preflight_manifest.json", written_names)
        self.assertIn("p4_05_policy.json", written_names)
        self.assertIn("b_input.json", written_names)
        self.assertNotIn("load_receipt.json", written_names)

    def test_active_preparation_rejects_missing_actual_upstream_before_io(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            remote.Phase4RemoteFreshIntegratedError,
            "actual upstream",
        ):
            remote.prepare_phase4_remote_qwen_fresh_integrated(
                model_root=Path("missing-model"),
                integrity_evidence=Path("missing-integrity"),
                result_root=Path("missing-result"),
                require_actual_upstream=True,
            )

    def test_model_json_accepts_complete_pretty_json_but_not_trailing_data(self) -> None:
        raw = b'{\n  "states": []\n}\n'
        self.assertEqual(remote._parse_model_json(raw, "test"), {"states": []})
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            remote._parse_model_json(b'{"states": []} trailing', "test")

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows managed host denies nested temp-directory access",
    )
    def test_history_and_resume_restore_validated_prefix_without_calls(self) -> None:
        b_input = synthetic_commerce_b_input(
            case_id=remote.P4_05_CASE_ID,
            request_id=remote.P4_05_REQUEST_ID,
        )
        source_state = phase4_create_portable_authority_state(b_input)
        attempts: dict[str, dict[str, object]] = {}
        with tempfile.TemporaryDirectory(
            dir=_ROOT,
            ignore_cleanup_errors=True,
        ) as temporary:
            temp_root = Path(temporary)
            source_root = temp_root / "source"
            child_root = temp_root / "child"
            result_root = temp_root / "result"
            source_root.mkdir()
            child_root.mkdir()
            result_root.mkdir()
            (source_root / "b_input.json").write_bytes(
                remote._canonical_bytes(b_input)
            )
            for node_id in ("F1", "F2"):
                output = phase4_synthetic_fixture_output(
                    node_id,
                    source_state,
                )
                source_state = phase4_register_node_output(
                    source_state,
                    node_id,
                    output,
                )
                raw = json.dumps(
                    output,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                attempt_root = source_root / "attempts" / node_id
                attempt_root.mkdir(parents=True)
                attempt = remote._attempt_record(
                    run_id=f"{remote.P4_05_RUN_PREFIX}resume-source",
                    case_id=remote.P4_05_CASE_ID,
                    request_id=remote.P4_05_REQUEST_ID,
                    node_id=node_id,
                    input_bytes=b"{}",
                    prompt_bytes=b"{}",
                    config_bytes=b"{}",
                    request_bytes=b"{}",
                    pre_call_record={},
                    worker_id="worker-source",
                    worker_pid=1,
                    raw=raw,
                    generate_started=True,
                    status="validated",
                    failure_code=None,
                )
                attempts[node_id] = attempt
                (attempt_root / "raw_response.bin").write_bytes(raw)
                (attempt_root / "validated_node_output.json").write_bytes(
                    remote._canonical_bytes(output)
                )
                (attempt_root / "attempt_result.json").write_bytes(
                    remote._canonical_bytes(attempt)
                )
                self.assertEqual(
                    set(
                        json.loads(
                            (
                                attempt_root
                                / "validated_node_output.json"
                            ).read_bytes()
                        )
                    ),
                    (
                        {
                            "page_title",
                            "layout_pattern",
                            "sections",
                            "components",
                        }
                        if node_id == "F1"
                        else {"states"}
                    ),
                )
            ledger = remote._call_ledger(
                run_id=f"{remote.P4_05_RUN_PREFIX}resume-source",
                node_results=attempts,
            )
            (source_root / "model_call_ledger.json").write_bytes(
                remote._canonical_bytes(ledger)
            )
            (child_root / "b_input.json").write_bytes(
                remote._canonical_bytes(b_input)
            )
            f3_output = phase4_synthetic_fixture_output(
                "F3",
                source_state,
            )
            f3_raw = json.dumps(
                f3_output,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            f3_attempt_root = child_root / "attempts" / "F3"
            f3_attempt_root.mkdir(parents=True)
            f3_attempt = remote._attempt_record(
                run_id=f"{remote.P4_05_RUN_PREFIX}resume-child",
                case_id=remote.P4_05_CASE_ID,
                request_id=remote.P4_05_REQUEST_ID,
                node_id="F3",
                input_bytes=b"{}",
                prompt_bytes=b"{}",
                config_bytes=b"{}",
                request_bytes=b"{}",
                pre_call_record={},
                worker_id="worker-child",
                worker_pid=2,
                raw=f3_raw,
                generate_started=True,
                status="validated",
                failure_code=None,
            )
            (f3_attempt_root / "raw_response.bin").write_bytes(f3_raw)
            (f3_attempt_root / "validated_node_output.json").write_bytes(
                remote._canonical_bytes(f3_output)
            )
            (f3_attempt_root / "attempt_result.json").write_bytes(
                remote._canonical_bytes(f3_attempt)
            )
            child_ledger = remote._call_ledger(
                run_id=f"{remote.P4_05_RUN_PREFIX}resume-child",
                node_results={"F3": f3_attempt},
            )
            (child_root / "model_call_ledger.json").write_bytes(
                remote._canonical_bytes(child_ledger)
            )
            (child_root / "resume_receipt.json").write_bytes(
                remote._canonical_bytes(
                    {
                        "schema_version": (
                            f"{remote.P4_05_SCHEMA_PREFIX}.resume.v1"
                        ),
                        "source_result_root": str(source_root.resolve()),
                        "model_generate_calls": 0,
                        "automatic_retry": False,
                        "budget_reset": False,
                    }
                )
            )
            ignored_f4_root = child_root / "attempts" / "F4"
            ignored_f4_root.mkdir(parents=True)
            (ignored_f4_root / "validated_node_output.json").write_bytes(
                b'{"intentionally":"ignored-by-f1-f2-prefix"}'
            )

            history = remote._build_call_history(
                history_result_roots=(source_root, child_root),
                result_root=result_root,
            )
            restored, receipt = remote._restore_validated_prefix(
                resume_from_result_root=child_root,
                result_root=result_root,
                b_input=b_input,
                state=phase4_create_portable_authority_state(b_input),
                history_receipt=history,
                resume_prefix=remote.P4_05_RESUME_PREFIX_F1_F2,
            )

        self.assertEqual(receipt["selected_prefix"], "F1-F2")
        self.assertEqual(receipt["resumed_nodes"], ["F1", "F2"])
        self.assertEqual(receipt["next_node"], "F3")
        self.assertEqual(receipt["ignored_source_nodes"], ["F3", "F4"])
        self.assertEqual(
            history["aggregate_per_node_generate_started_count"],
            {"F1": 1, "F2": 1, "F3": 1, "F4": 0},
        )
        self.assertEqual(
            list(restored["node_results"]),
            ["F1", "F2"],
        )


class Phase4RemoteFreshIntegratedStreamingTests(unittest.TestCase):
    def test_stream_mirror_prints_delta_and_keeps_bytes(self) -> None:
        console = io.StringIO()
        mirror = remote.FreshIntegratedStreamMirror(console)
        event = {
            "schema_version": remote.P4_05_STREAM_SCHEMA_VERSION,
            "event": "token_delta",
            "node_id": "F1",
            "delta_b64": base64.b64encode(b'{"page_title":').decode("ascii"),
        }
        mirror.feed(remote._canonical_bytes(event) + b"\n")

        self.assertEqual(bytes(mirror.token_bytes), b'{"page_title":')
        self.assertIn('{"page_title":', console.getvalue())
        self.assertIn(b"token_delta", bytes(mirror.stderr_bytes))

    def test_stream_mirror_rejects_delta_on_non_token_event(self) -> None:
        mirror = remote.FreshIntegratedStreamMirror(io.StringIO())
        event = {
            "schema_version": remote.P4_05_STREAM_SCHEMA_VERSION,
            "event": "load_started",
            "node_id": None,
            "delta_b64": base64.b64encode(b"unexpected").decode("ascii"),
        }
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            mirror.feed(remote._canonical_bytes(event) + b"\n")


class Phase4RemoteFreshIntegratedWorkerBoundaryTests(unittest.TestCase):
    def test_parent_receive_uses_queue_ipc_boundary(self) -> None:
        worker = object.__new__(remote.FreshIntegratedRemoteWorker)
        worker._messages = queue.Queue()
        worker._messages.put(
            {
                "protocol": remote.P4_05_WORKER_PROTOCOL,
                "kind": "loaded",
            }
        )
        worker._closed = False
        self.assertEqual(
            worker._receive(1)["kind"],
            "loaded",
        )

    def test_run_requires_explicit_confirmation_before_preflight(self) -> None:
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            remote.run_phase4_remote_qwen_fresh_integrated(
                model_root=Path("C:/not-used/model"),
                integrity_evidence=Path("C:/not-used/evidence"),
                result_root=Path("C:/not-used/result"),
                confirm_one_remote_fresh_integrated_run=False,
            )

    def test_graph_bound_material_failure_precedes_model_worker(self) -> None:
        result_root = _ROOT / ".p4-05-test-only-fixed-binding-failure"
        with patch.object(
            remote,
            "_prepare_graph_bound_delivery_materials",
            side_effect=remote.Phase4RemoteFreshIntegratedError(
                "fixed binding drift"
            ),
        ) as prepare_fixed, patch.object(
            remote,
            "FreshIntegratedRemoteWorker",
        ) as worker, patch.object(
            remote,
            "_write_fsync",
            return_value=None,
        ), patch.object(
            Path,
            "mkdir",
            return_value=None,
        ):
            with self.assertRaises(
                remote.Phase4RemoteFreshIntegratedError
            ):
                remote.run_phase4_remote_qwen_fresh_integrated(
                    model_root=_ROOT,
                    integrity_evidence=_ROOT / "AGENTS.md",
                    result_root=result_root,
                    confirm_one_remote_fresh_integrated_run=True,
                )

        prepare_fixed.assert_called_once()
        worker.assert_not_called()

    def test_parent_profile_drift_precedes_model_worker(self) -> None:
        profile = remote.RemoteFreshIntegratedProfile.create(
            inventory=_fake_inventory(),
            runtime_facts=_fake_runtime(),
            gpu_facts=_fake_gpu(),
        )
        b_input = synthetic_commerce_b_input(
            case_id=remote.P4_05_CASE_ID,
            request_id=remote.P4_05_REQUEST_ID,
        )
        prepared = {
            "result_root": Path("C:/p4-05-test-only/profile-drift"),
            "profile": profile,
            "run_id": f"{remote.P4_05_RUN_PREFIX}profile-drift",
            "b_input": b_input,
            "parent_experiment_binding": None,
            "preflight": {
                "profile_identity": remote._identity(
                    profile.to_dict(),
                    revision=remote.P4_05_PROFILE_SCHEMA_VERSION,
                ),
                "model_inventory_identity": _fake_inventory()[
                    "inventory_identity"
                ],
            },
        }
        with patch.object(
            remote,
            "prepare_phase4_remote_qwen_fresh_integrated",
            return_value=prepared,
        ), patch.object(
            remote,
            "FreshIntegratedRemoteWorker",
        ) as worker:
            with self.assertRaisesRegex(
                remote.Phase4RemoteFreshIntegratedError,
                "profile identity drifted",
            ):
                remote.run_phase4_remote_qwen_fresh_integrated(
                    model_root=_ROOT,
                    integrity_evidence=_ROOT / "AGENTS.md",
                    result_root=Path("C:/p4-05-test-only/profile-drift"),
                    confirm_one_remote_fresh_integrated_run=True,
                    expected_profile_identity=remote._identity(
                        {"profile": "different"},
                        revision=remote.P4_05_PROFILE_SCHEMA_VERSION,
                    ),
                )

        worker.assert_not_called()


class Phase4RemoteFreshIntegratedCliTests(unittest.TestCase):
    def test_cli_exposes_preflight_and_confirmation_gate(self) -> None:
        from scripts import run_phase4_remote_qwen_fresh_integrated as cli

        help_text = cli.build_parser().format_help()
        self.assertNotIn("--repository-root", help_text)
        self.assertIn("--preflight-only", help_text)
        self.assertIn("--confirm-one-remote-fresh-integrated-run", help_text)
        self.assertIn("--history-result-root", help_text)
        self.assertIn("--resume-from-result-root", help_text)

        with self.assertRaises(SystemExit) as raised:
            cli.main(
                [
                    "--model-root",
                    "C:/not-used/model",
                    "--integrity-evidence",
                    "C:/not-used/evidence",
                    "--result-root",
                    "C:/not-used/result",
                ]
            )
        self.assertEqual(raised.exception.code, 2)

    def test_cli_preflight_only_does_not_start_model_worker(self) -> None:
        from scripts import run_phase4_remote_qwen_fresh_integrated as cli

        with patch.object(
            remote,
            "prepare_phase4_remote_qwen_fresh_integrated",
            return_value={"run_id": f"{remote.P4_05_RUN_PREFIX}cli"},
        ) as prepare:
            result = cli.main(
                [
                    "--model-root",
                    str(_ROOT),
                    "--integrity-evidence",
                    str(_ROOT / "AGENTS.md"),
                    "--result-root",
                    "C:/p4-05-test-only/cli-result",
                    "--preflight-only",
                ]
            )

        self.assertEqual(result, 0)
        prepare.assert_called_once()

    def test_remote_runner_no_longer_references_early_delivery_bridge(self) -> None:
        source = (
            _ROOT
            / "src"
            / "req2web_runtime"
            / "phase4_remote_qwen_fresh_integrated.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("phase4_delivery_bridge", source)
        self.assertIn("run_phase4_fresh_delivery", source)
        self.assertIn("build_phase4_graph_bound_delivery_materials", source)
        self.assertNotIn("autodl_trusted_remote_case_loader", source)
        self.assertNotIn("build_fixed_trusted_remote_case_materials_v2", source)


if __name__ == "__main__":
    unittest.main()
