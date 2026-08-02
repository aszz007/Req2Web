"""Focused no-model tests for the Phase 4 local Qwen pre-call foundation.

The focused collection currently contains 18 tests; none loads or calls a
model.
"""

from __future__ import annotations

import json
import hashlib
import copy
import queue
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import req2web_runtime.phase4_local_qwen as p4q

from req2web_orchestration.phase4_graph import (
    Phase4GraphRuntime,
    create_initial_state,
    make_identity,
    phase4_create_authority_state,
    phase4_project_node_input_authority,
    phase4_register_node_output,
    phase4_synthetic_fixture_output,
    synthetic_commerce_b_input,
)
from req2web_runtime.phase4_local_qwen import (
    CHANGE_REASONS,
    AttemptResult,
    LocalQwenLoadReceipt,
    LocalQwenProfile,
    NodeProjectionPolicy,
    Phase4LocalQwenContractError,
    Phase4LocalQwenPilotRunner,
    PilotBinding,
    PilotExecutionLease,
    PilotSupervisorReceipt,
    PreCallManifest,
    SupervisedLocalQwenBackend,
    SupervisedWorkerFailure,
    acquire_pilot_execution_lease,
    build_prompt_v1,
    build_prompt_v2,
    build_synthetic_case_binding,
    create_scripted_fixture_backend,
    derive_node_input,
    prepare_local_qwen_pilot,
    validate_model_inventory_metadata,
)


def _policies() -> tuple[NodeProjectionPolicy, ...]:
    caps = {
        "input_bytes": 131072,
        "output_bytes": 65536,
        "prompt_bytes": 16384,
        "config_bytes": 8192,
        "request_bytes": 8192,
        "ref_count": 32,
    }
    categories = {
        "F1": ["canonical_b_input", "case_identity"],
        "F2": ["canonical_b_input", "validated_F1_output", "deterministic_registry"],
        "F3": ["canonical_b_input", "validated_F1_output", "validated_F2_output", "deterministic_registry"],
        "F4": ["canonical_b_input", "validated_F1_output", "validated_F2_output", "validated_F3_output", "deterministic_registry", "deterministic_mapping"],
    }
    upstream = {"F1": [], "F2": ["F1"], "F3": ["F1", "F2"], "F4": ["F1", "F2", "F3"]}
    prohibited = ["b_aux_sidecar", "retrieval_evidence", "h1_gold", "browser_evidence", "hidden_reasoning"]
    return tuple(
        NodeProjectionPolicy.create(
            node_id=node_id,
            allowed_categories=categories[node_id],
            prohibited_categories=prohibited,
            field_caps=caps,
            upstream_required_node_ids=upstream[node_id],
        )
        for node_id in ("F1", "F2", "F3", "F4")
    )


def _binding_profile_manifest(root_marker: str = "test-result-root"):
    b_input = synthetic_commerce_b_input()
    policies = _policies()
    model_root_identity = make_identity({"fake_model_root": "metadata-only"}, revision="test.model-root.v1")
    inventory_identity = make_identity({"fake_inventory": ["config.json"]}, revision="test.inventory.v1")
    profile = LocalQwenProfile.create(
        model_root_identity=model_root_identity,
        model_inventory_identity=inventory_identity,
        model_file_count=1,
    )
    case_binding = {
        "case_id": b_input["case_id"],
        "request_id": b_input["request_id"],
        "b_identity": make_identity(b_input, revision="canonical_b.p4.v1"),
        "requirement_sha256": make_identity({"requirement": b_input["requirement"]}, revision="test.requirement.v1")["sha256"],
        "use_case_sha256": make_identity(b_input["use_cases"], revision="test.use-cases.v1")["sha256"],
        "constraint_sha256": make_identity(b_input["constraints"], revision="test.constraints.v1")["sha256"],
    }
    binding = PilotBinding.create(
        pilot_id="p4-03-test",
        policy_id="pending",
        case_binding=case_binding,
        node_policy_identities={policy.node_id: policy.sha256() for policy in policies},
        profile_id=profile.profile_id,
        result_root_marker=root_marker,
    )
    manifest = PreCallManifest.create(
        pilot_binding=binding,
        policies=policies,
        profile=profile,
        model_root_identity=model_root_identity,
        model_inventory_identity=inventory_identity,
        inventory_file_count=1,
        offline_environment={"local_files_only": True, "network": False, "telemetry": False, "tracing": False},
    )
    return b_input, policies, profile, binding, manifest


def _fixture_bytes(b_input: dict[str, object]) -> list[bytes]:
    state = phase4_create_authority_state(b_input)
    result: list[bytes] = []
    for node_id in ("F1", "F2", "F3", "F4"):
        output = phase4_synthetic_fixture_output(node_id, state)
        result.append(json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        state = phase4_register_node_output(state, node_id, output)
    return result


def _write_fake_integrity(parent: Path) -> tuple[Path, Path]:
    model_root = parent / "fake-model"
    model_root.mkdir(parents=True)
    files = {
        ".gitattributes": b"*.safetensors filter=lfs diff=lfs merge=lfs -text\n",
        "config.json": b'{"model_type":"qwen3_5"}',
        "weights.bin": b"small-test-weight-bytes",
    }
    rows = []
    for relative_path, raw in files.items():
        path = model_root / relative_path
        path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        rows.append({
            "rfilename": relative_path,
            "exists": True,
            "expected_size": len(raw),
            "expected_lfs_sha256": digest if relative_path == "weights.bin" else None,
            "expected_blob_id": "0" * 40,
            "actual_size": len(raw),
            "actual_sha256": digest,
            "actual_git_blob_sha1": "0" * 40,
            "size_match": True,
            "identity_match": True,
        })
    evidence = {
        "repo_id": "Qwen/Qwen3.5-9B",
        "requested_revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
        "resolved_sha": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
        "root": str(model_root.resolve()),
        "expected_file_count": len(rows),
        "actual_repo_file_count": len(rows),
        "missing": [],
        "extra": [],
        "total_actual_bytes": sum(len(raw) for raw in files.values()),
        "all_sizes_match": True,
        "all_identities_match": True,
        "elapsed_seconds": 0.01,
        "files": rows,
    }
    evidence_path = parent / "local_integrity.json"
    evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return model_root, evidence_path


def _fake_gpu_facts() -> dict[str, object]:
    return {
        "executed": True,
        "device_index": 0,
        "device_uuid": "GPU-test-placeholder",
        "device_name": "NVIDIA GeForce RTX 4060 Laptop GPU",
        "total_vram_bytes": 8_000_000_000,
        "free_vram_bytes": 6_000_000_000,
        "driver_version": "test-driver",
        "cuda_version": "12.8",
    }


def _prepare_tiny_artifact(parent: Path):
    """Create a complete tiny prepare artifact without loading a model."""

    model_root, integrity_evidence = _write_fake_integrity(parent)
    b_input = synthetic_commerce_b_input()
    result_root = parent / "prepared"
    prepared = prepare_local_qwen_pilot(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        result_root=result_root,
        case_binding=build_synthetic_case_binding(b_input),
        policies=_policies(),
        runtime_versions={
            "transformers": p4q.TRANSFORMERS_VERSION,
            "torch": p4q.TORCH_VERSION,
            "bitsandbytes": p4q.BITSANDBYTES_VERSION,
            "accelerate": p4q.ACCELERATE_VERSION,
        },
        gpu_facts=_fake_gpu_facts(),
        environment={
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "LANGSMITH_TRACING": "0",
            "LANGCHAIN_TRACING_V2": "0",
        },
    )
    return model_root, integrity_evidence, result_root, b_input, *prepared


def FakeBackend(
    outputs: list[bytes], *, result_root: Path | None = None
):
    return create_scripted_fixture_backend(outputs, result_root=result_root)


def FailingBackend(outputs: list[bytes]):
    return create_scripted_fixture_backend(outputs)


class Phase4LocalQwenTests(unittest.TestCase):
    def setUp(self):
        self._test_roots: list[Path] = []

    def tearDown(self):
        for root in self._test_roots:
            shutil.rmtree(root, ignore_errors=True)
        parent = Path.cwd() / ".p4-03-test-results"
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()

    def _runner(self, backend, *, context=None, guidance=None):
        b_input, policies, profile, binding, manifest = _binding_profile_manifest()
        root = Path.cwd() / ".p4-03-test-results" / f"case-{len(self._test_roots)}"
        root.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(root)
        (root / ".req2web-phase4-p4-03-result-root").write_text("test-result-root\n", encoding="ascii")
        runner = Phase4LocalQwenPilotRunner(
            pilot=binding,
            policies=policies,
            profile=profile,
            manifest=manifest,
            result_root=root,
            b_input=b_input,
            backend=backend,
            assembler_context=context,
            assembler_guidance=guidance,
        )
        return runner, root, b_input

    def test_exact_records_reject_bool_and_direct_constructor(self):
        _, _, _, binding, _ = _binding_profile_manifest()
        self.assertEqual(binding, type(binding).from_bytes(binding.canonical_bytes()))
        payload = binding.to_dict()
        payload["node_total_call_cap"] = True
        with self.assertRaises(Phase4LocalQwenContractError):
            PilotBinding.from_dict(payload)
        with self.assertRaises(TypeError):
            PilotBinding()  # type: ignore[call-arg]

    def test_prepare_live_hashes_inventory_and_keeps_actions_false(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"inventory-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)
        model_root, evidence_path = _write_fake_integrity(parent)
        b_input = synthetic_commerce_b_input()
        binding, policies, profile, manifest = prepare_local_qwen_pilot(
            model_root=model_root,
            integrity_evidence=evidence_path,
            result_root=parent / "result",
            case_binding=build_synthetic_case_binding(b_input),
            policies=_policies(),
            runtime_versions={
                "transformers": "5.14.1",
                "torch": "2.7.1+cu128",
                "bitsandbytes": "0.50.0",
                "accelerate": "1.14.0",
            },
            gpu_facts=_fake_gpu_facts(),
            environment={
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "LANGSMITH_TRACING": "0",
                "LANGCHAIN_TRACING_V2": "0",
            },
        )
        self.assertEqual([policy.node_id for policy in policies], ["F1", "F2", "F3", "F4"])
        self.assertEqual(binding.profile_id, profile.profile_id)
        self.assertTrue(manifest.weight_bytes_hashed)
        self.assertTrue(manifest.authorization_state["model_action_authorized"])
        self.assertFalse(manifest.action_state["model_action"])
        self.assertFalse(manifest.action_state["graph_runtime_execution"])
        payload = manifest.to_dict()
        payload["model_root_identity"] = make_identity(
            {"drift": True}, revision="test.drift.v1"
        )
        with self.assertRaises(Phase4LocalQwenContractError):
            PreCallManifest.from_dict(payload)
        (model_root / "weights.bin").write_bytes(b"drift")
        with self.assertRaises(Phase4LocalQwenContractError):
            validate_model_inventory_metadata(
                model_root=model_root,
                integrity_evidence=evidence_path,
            )

    def test_model_inventory_excludes_only_huggingface_local_cache(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"cache-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)
        model_root, evidence_path = _write_fake_integrity(parent)
        cache_file = model_root / ".cache" / "huggingface" / "CACHEDIR.TAG"
        cache_file.parent.mkdir(parents=True)
        cache_file.write_bytes(b"Signature: 8a477f597d28d172789f06886806bc55\n")
        inventory = validate_model_inventory_metadata(
            model_root=model_root,
            integrity_evidence=evidence_path,
        )
        self.assertEqual(inventory["file_count"], 3)
        self.assertEqual(inventory["excluded_local_cache_file_count"], 1)
        (model_root / "unexpected.txt").write_bytes(b"not model evidence")
        with self.assertRaises(Phase4LocalQwenContractError):
            validate_model_inventory_metadata(
                model_root=model_root,
                integrity_evidence=evidence_path,
            )

    def test_prompt_and_backend_include_exact_actual_input(self):
        _, policies, profile, _, _ = _binding_profile_manifest()
        actual_input = b'{"actual":"node-input"}'
        prompt = build_prompt_v1(
            node_id="F1",
            input_bytes=actual_input,
            policy=policies[0],
            profile=profile,
        )
        prompt_data = json.loads(prompt.decode("utf-8"))
        self.assertIn("output_contract", prompt_data)
        self.assertEqual(
            prompt_data["output_contract"]["exact_top_level_keys"],
            ["page_title", "layout_pattern", "sections", "components"],
        )

        class FakeTensor:
            shape = (1, 3)

            def to(self, _: str):
                return self

            def __getitem__(self, _: object):
                return self

        class FakeProcessor:
            def __init__(self):
                self.model_text = ""

            def apply_chat_template(self, messages, **kwargs):
                self.model_text = messages[0]["content"][0]["text"]
                self.kwargs = kwargs
                return self.model_text

            def __call__(self, **_: object):
                return {"input_ids": FakeTensor(), "attention_mask": FakeTensor(), "mm_token_type_ids": FakeTensor()}

            def batch_decode(self, *_: object, **__: object):
                return ["{}"]

        class FakeModel:
            def generate(self, **_: object):
                return FakeTensor()

        class FakeCuda:
            @staticmethod
            def manual_seed_all(_: int) -> None:
                return None

        class FakeTorch:
            cuda = FakeCuda()

            @staticmethod
            def manual_seed(_: int) -> None:
                return None

        backend = p4q._LazyTransformersQwenBackend(
            model_root=Path("C:/not-loaded"),
            profile=profile,
            runtime_capability=p4q._REAL_RUNTIME_CAPABILITY,
        )
        processor = FakeProcessor()
        backend._processor = processor
        backend._model = FakeModel()
        backend._torch = FakeTorch()
        raw = backend.generate(
            node_id="F1",
            input_bytes=actual_input,
            prompt_bytes=prompt,
            config_bytes=b'{"config":1}',
            request_bytes=b'{"request":1}',
        )
        self.assertEqual(raw, b"{}")
        self.assertIn('ACTUAL_NODE_INPUT_JSON\n{"actual":"node-input"}', processor.model_text)
        self.assertFalse(processor.kwargs["enable_thinking"])

    def test_load_receipt_is_separate_from_pre_call_action_state(self):
        _, _, profile, binding, manifest = _binding_profile_manifest()
        receipt = LocalQwenLoadReceipt.create(
            manifest=manifest,
            pilot=binding,
            profile=profile,
            loaded_facts={
                "model_class": "Qwen3_5ForConditionalGeneration",
                "processor_class": "Qwen3VLProcessor",
                "is_loaded_in_4bit": True,
                "hf_device_map": {"": "0"},
                "parameter_devices": ["cuda:0"],
                "compute_dtypes": ["torch.bfloat16"],
                "cpu_offload": False,
                "device": "cuda:0",
            },
        )
        receipt.validate_against(manifest=manifest, pilot=binding, profile=profile)
        self.assertTrue(receipt.action_state["model_action"])
        self.assertFalse(receipt.action_state["graph_runtime_execution"])
        self.assertFalse(receipt.generation_occurred)

    def test_f4_input_binds_registry_mapping_and_exact_refs(self):
        b_input = synthetic_commerce_b_input()
        policies = _policies()
        state = phase4_create_authority_state(b_input)
        upstream: dict[str, bytes] = {}
        for node_id in ("F1", "F2", "F3"):
            output = phase4_synthetic_fixture_output(node_id, state)
            upstream[node_id] = json.dumps(output, separators=(",", ":")).encode("utf-8")
            state = phase4_register_node_output(state, node_id, output)
        projection = phase4_project_node_input_authority(state, "F4")
        actual = derive_node_input(
            node_id="F4",
            b_input_bytes=json.dumps(b_input, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            upstream_outputs=upstream,
            authority_state=state,
            policy=policies[3],
        )
        payload = json.loads(actual.decode("utf-8"))
        self.assertEqual(payload["authority_bindings"], projection)
        self.assertTrue(projection["mapping_identity"]["sha256"].startswith("sha256:"))
        self.assertTrue(projection["ordered_mappings"])
        self.assertTrue(projection["f2_state_refs"])
        self.assertEqual(
            [row["ref_id"] for row in projection["canonical_b_use_case_refs"]],
            [row["use_case_id"] for row in b_input["use_cases"]],
        )
        tampered = copy.deepcopy(state)
        tampered["registry_inventory"][0]["stable_id"] = "p4-f1-section-tampered0000000"
        with self.assertRaises(ValueError):
            phase4_project_node_input_authority(tampered, "F4")

    def test_prompt_v2_requires_immutable_prior_failure(self):
        b_input, policies, profile, binding, manifest = _binding_profile_manifest()
        runner, _, _ = self._runner(FailingBackend([b"{}"]))
        result = runner.run_node_local(node_id="F1")
        self.assertIsNotNone(result.failure_code)
        prompt = build_prompt_v2(
            node_id="F1",
            input_bytes=b'{"input":"same"}',
            policy=policies[0],
            profile=profile,
            prior_failure=result,
            change_reason=CHANGE_REASONS[0],
        )
        envelope = json.loads(prompt.decode("utf-8"))
        self.assertEqual(envelope["prompt_schema_version"], "req2web.phase4.p4_03.prompt.v2")
        self.assertEqual(envelope["prior_failure_identity"]["sha256"], make_identity(result.to_dict(), revision="req2web.phase4.p4_03.attempt_result.v1")["sha256"])
        with self.assertRaises(Phase4LocalQwenContractError):
            build_prompt_v2(node_id="F1", input_bytes=b'{"input":"same"}', policy=policies[0], profile=profile, prior_failure=result, change_reason="free_form_tuning")

    def test_node_local_success_then_fresh_integrated_success_and_raw_first(self):
        b_input = synthetic_commerce_b_input()
        fixture = _fixture_bytes(b_input)
        backend = FakeBackend(fixture + fixture)
        runner, root, _ = self._runner(backend)
        for node_id in ("F1", "F2", "F3", "F4"):
            result = runner.run_node_local(node_id=node_id)
            self.assertIsNone(result.failure_code)
        self.assertEqual(runner.ledger.node_local_counts, {node_id: 1 for node_id in ("F1", "F2", "F3", "F4")})
        outcome = runner.run_integrated()
        self.assertEqual(outcome.status, "integrated_success")
        self.assertEqual(outcome.composition_status, "validated")
        self.assertEqual(outcome.assembler_status, "assembled")
        self.assertEqual(outcome.node_total_counts, {node_id: 2 for node_id in ("F1", "F2", "F3", "F4")})
        self.assertEqual(outcome.integrated_node_raw_contract_pass_count, 4)
        self.assertEqual(outcome.source_kind, "scripted_test_fixture")
        self.assertFalse(outcome.model_action_occurred)
        self.assertEqual(outcome.model_calls_performed, 0)
        self.assertEqual(
            runner.integrated_graph_events,
            (
                "F1:started", "F1:passed", "F2:started", "F2:passed",
                "F3:started", "F3:passed",
                "map_use_cases:started", "map_use_cases:passed",
                "F4:started", "F4:passed",
                "compose_candidate:started", "compose_candidate:passed",
                "assemble_page_spec:started", "assemble_page_spec:passed",
            ),
        )
        self.assertEqual(outcome.downstream_execution["acceptance"], "not_executed_p4_03")
        self.assertTrue(backend.raw_seen_before_return)
        self.assertEqual(len(list(root.rglob("raw_response.bin"))), 8)

    def test_empty_and_invalid_raw_consume_generate_start_and_stop_after_two(self):
        backend = FailingBackend([b"", b"not-json"])
        runner, root, _ = self._runner(backend)
        first = runner.run_node_local(node_id="F1")
        self.assertEqual(first.failure_code, "empty_raw_response")
        action_payload = json.loads(
            next(root.rglob("node_d17_action.json")).read_text(encoding="utf-8")
        )
        self.assertTrue(action_payload["authorization_state"]["model_action_authorized"])
        self.assertFalse(action_payload["action_state"]["model_action"])
        self.assertIsNone(action_payload["runtime_load_ref"])
        result_payload = json.loads(
            next(root.rglob("attempt_result.json")).read_text(encoding="utf-8")
        )
        self.assertFalse(result_payload["action_state"]["model_action"])
        self.assertEqual(result_payload["source_kind"], "scripted_test_fixture")
        raw_paths = list(root.rglob("raw_response.bin"))
        self.assertEqual(len(raw_paths), 1)
        self.assertEqual(raw_paths[0].read_bytes(), b"")
        second = runner.run_node_local(node_id="F1", prompt_version=2, change_reason="output_schema_clarification")
        self.assertEqual(second.failure_code, "node_contract_invalid")
        actions = sorted(root.rglob("node_d17_action.json"))
        second_action = json.loads(actions[-1].read_text(encoding="utf-8"))
        self.assertEqual(second_action["prompt_revision"], "p4-03-prompt-v2")
        self.assertEqual(
            second_action["prompt_change"]["prior_result_id"], first.result_id
        )
        self.assertEqual(
            second_action["prompt_change"]["change_reason"],
            "output_schema_clarification",
        )
        self.assertTrue(runner.stopped)
        self.assertEqual(runner.ledger.node_total_counts["F1"], 2)
        self.assertEqual(runner.outcome(stop_reason="node_budget_exhausted").status, "stopped_node_exhausted")

    def test_cross_binding_and_assembler_failure_are_fail_closed(self):
        b_input = synthetic_commerce_b_input()
        fixture = _fixture_bytes(b_input)
        backend = FakeBackend(fixture + fixture)
        runner, _, _ = self._runner(backend, context=object(), guidance=object())
        for node_id in ("F1", "F2", "F3", "F4"):
            runner.run_node_local(node_id=node_id)
        outcome = runner.run_integrated()
        self.assertEqual(outcome.status, "integrated_failed_closed")
        self.assertEqual(outcome.composition_status, "validated")
        self.assertEqual(outcome.assembler_status, "failed_closed")
        self.assertTrue(runner.stopped)

    def test_composition_failure_does_not_claim_assembler_execution(self):
        b_input = synthetic_commerce_b_input()
        fixture = _fixture_bytes(b_input)
        runner, _, _ = self._runner(FakeBackend(fixture + fixture))
        for node_id in ("F1", "F2", "F3", "F4"):
            runner.run_node_local(node_id=node_id)
        with patch(
            "req2web_orchestration.phase4_graph.phase4_compose_candidate",
            side_effect=ValueError("forced composition failure"),
        ):
            outcome = runner.run_integrated()
        self.assertEqual(outcome.status, "integrated_failed_closed")
        self.assertEqual(outcome.composition_status, "failed_closed")
        self.assertEqual(outcome.assembler_status, "not_executed")

    def test_reference_cap_is_enforced_on_actual_projection(self):
        b_input = synthetic_commerce_b_input()
        policies = _policies()
        state = phase4_create_authority_state(b_input)
        upstream: dict[str, bytes] = {}
        for node_id in ("F1", "F2", "F3"):
            output = phase4_synthetic_fixture_output(node_id, state)
            upstream[node_id] = json.dumps(
                output, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            state = phase4_register_node_output(state, node_id, output)
        restrictive_caps = dict(policies[3].field_caps)
        restrictive_caps["ref_count"] = 1
        restrictive = NodeProjectionPolicy.create(
            node_id="F4",
            allowed_categories=policies[3].allowed_categories,
            prohibited_categories=policies[3].prohibited_categories,
            field_caps=restrictive_caps,
            upstream_required_node_ids=policies[3].upstream_required_node_ids,
        )
        with self.assertRaises(Phase4LocalQwenContractError):
            derive_node_input(
                node_id="F4",
                b_input_bytes=json.dumps(
                    b_input, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                upstream_outputs=upstream,
                authority_state=state,
                policy=restrictive,
            )

    def test_execution_lease_is_one_shot_and_repeat_is_rejected(self):
        root = Path.cwd() / ".p4-03-test-results" / f"lease-{len(self._test_roots)}"
        root.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(root)
        (
            model_root,
            integrity_evidence,
            result_root,
            _b_input,
            binding,
            _policies,
            _profile,
            manifest,
        ) = _prepare_tiny_artifact(root)
        lease = acquire_pilot_execution_lease(
            result_root=result_root,
            pilot=binding,
            manifest=manifest,
        )
        self.assertIsInstance(lease, PilotExecutionLease)
        self.assertEqual(
            (result_root / "execution_lease.json").read_bytes(),
            lease.canonical_bytes(),
        )
        claim = p4q._acquire_runtime_start_claim(
            result_root=result_root, lease=lease
        )
        self.assertEqual(
            (result_root / "runtime_start_claim.json").read_bytes(),
            claim.canonical_bytes(),
        )
        with self.assertRaises(Phase4LocalQwenContractError):
            p4q._acquire_runtime_start_claim(result_root=result_root, lease=lease)
        with patch(
            "req2web_runtime.phase4_local_qwen.subprocess.Popen"
        ) as popen:
            with self.assertRaises(Phase4LocalQwenContractError):
                p4q.start_supervised_local_qwen_runtime(
                    model_root=model_root,
                    integrity_evidence=integrity_evidence,
                    result_root=result_root,
                    load_timeout_seconds=1,
                )
            popen.assert_not_called()
        with self.assertRaises(Phase4LocalQwenContractError):
            acquire_pilot_execution_lease(
                result_root=result_root,
                pilot=binding,
                manifest=manifest,
            )

    def test_missing_or_drifted_prepare_artifact_rejects_before_popen(self):
        for case_name in ("missing", "drifted"):
            root = Path.cwd() / ".p4-03-test-results" / f"prepare-{case_name}-{len(self._test_roots)}"
            root.mkdir(parents=True, exist_ok=False)
            self._test_roots.append(root)
            (
                model_root,
                integrity_evidence,
                result_root,
                _b_input,
                binding,
                _policies,
                _profile,
                manifest,
            ) = _prepare_tiny_artifact(root)
            acquire_pilot_execution_lease(
                result_root=result_root,
                pilot=binding,
                manifest=manifest,
            )
            if case_name == "missing":
                (result_root / "pre_call_manifest.json").unlink()
            else:
                (model_root / "weights.bin").write_bytes(b"drifted-test-weight-bytes")
            with patch(
                "req2web_runtime.phase4_local_qwen.subprocess.Popen"
            ) as popen:
                with self.assertRaises(Phase4LocalQwenContractError):
                    p4q.start_supervised_local_qwen_runtime(
                        model_root=model_root,
                        integrity_evidence=integrity_evidence,
                        result_root=result_root,
                        load_timeout_seconds=1,
                    )
                popen.assert_not_called()

    def test_load_receipt_write_failure_terminates_started_worker(self):
        root = Path.cwd() / ".p4-03-test-results" / f"receipt-{len(self._test_roots)}"
        root.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(root)
        (
            model_root,
            integrity_evidence,
            result_root,
            _b_input,
            binding,
            _policies,
            profile,
            manifest,
        ) = _prepare_tiny_artifact(root)
        lease = acquire_pilot_execution_lease(
            result_root=result_root, pilot=binding, manifest=manifest
        )
        loaded_facts = {
            "model_class": "Qwen3_5ForConditionalGeneration",
            "processor_class": "Qwen3VLProcessor",
            "is_loaded_in_4bit": True,
            "hf_device_map": {"": "0"},
            "parameter_devices": ["cuda:0"],
            "compute_dtypes": ["torch.bfloat16"],
            "cpu_offload": False,
            "device": "cuda:0",
        }
        loaded_line = p4q._canonical_bytes(
            {
                "protocol": "req2web.phase4.p4_03.worker-ipc.v1",
                "kind": "loaded",
                "worker_id": "worker-receipt-failure",
                "worker_pid": 4242,
                "loaded_facts": loaded_facts,
            }
        ).decode("utf-8") + "\n"

        class FakeStdin:
            def write(self, _: str) -> None:
                return None

            def flush(self) -> None:
                return None

        class FakeStdout:
            def __iter__(self):
                yield loaded_line

        class FakeStderr:
            buffer = None

            def __init__(self):
                self.buffer = self

            def read(self, _: int) -> bytes:
                return b""

        class FakeProcess:
            pid = 4242
            stdin = FakeStdin()
            stdout = FakeStdout()
            stderr = FakeStderr()

            def __init__(self):
                self.exit_code = None
                self.terminated = False

            def poll(self):
                return self.exit_code

            def terminate(self):
                self.terminated = True
                self.exit_code = 1

            def kill(self):
                self.exit_code = 2

            def wait(self, timeout=None):
                del timeout
                if self.exit_code is None:
                    self.exit_code = 0
                return self.exit_code

        fake_process = FakeProcess()
        with patch(
            "req2web_runtime.phase4_local_qwen.subprocess.Popen",
            return_value=fake_process,
        ) as popen, patch(
            "req2web_runtime.phase4_local_qwen.persist_local_qwen_load_receipt",
            side_effect=OSError("synthetic receipt write failure"),
        ):
            with self.assertRaises(p4q.SupervisedWorkerStartFailure) as captured:
                p4q.start_supervised_local_qwen_runtime(
                    model_root=model_root,
                    integrity_evidence=integrity_evidence,
                    result_root=result_root,
                    load_timeout_seconds=1,
                )
            self.assertEqual(popen.call_count, 1)
        self.assertTrue(fake_process.terminated)
        self.assertEqual(
            captured.exception.failure_code, "evidence_persistence_failed"
        )
        self.assertTrue(captured.exception.teardown_facts["worker_exit_verified"])
        self.assertTrue((result_root / "runtime_start_claim.json").is_file())
        self.assertFalse((result_root / "local_qwen_load_receipt.json").exists())

    def test_real_source_rejects_arbitrary_backend_and_forged_receipt(self):
        b_input, policies, profile, binding, manifest = _binding_profile_manifest()
        root = Path.cwd() / ".p4-03-test-results" / f"source-{len(self._test_roots)}"
        root.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(root)
        (root / ".req2web-phase4-p4-03-result-root").write_text(
            "test-result-root\n", encoding="ascii"
        )
        lease = PilotExecutionLease.create(
            pilot=binding, manifest=manifest, parent_pid=1
        )
        (root / "execution_lease.json").write_bytes(lease.canonical_bytes())
        forged_receipt = LocalQwenLoadReceipt.create(
            manifest=manifest,
            pilot=binding,
            profile=profile,
            loaded_facts={
                "model_class": "Qwen3_5ForConditionalGeneration",
                "processor_class": "Qwen3VLProcessor",
                "is_loaded_in_4bit": True,
                "hf_device_map": {"": "0"},
                "parameter_devices": ["cuda:0"],
                "compute_dtypes": ["torch.bfloat16"],
                "cpu_offload": False,
                "device": "cuda:0",
            },
        )
        with self.assertRaises(Phase4LocalQwenContractError):
            Phase4LocalQwenPilotRunner(
                pilot=binding,
                policies=policies,
                profile=profile,
                manifest=manifest,
                result_root=root,
                b_input=b_input,
                backend=FakeBackend([]),
                source_kind="real_local_qwen",
                load_receipt=forged_receipt,
                execution_lease=lease,
            )

    def test_synthetic_timeout_terminates_worker_and_yields_terminal_receipt(self):
        _, _, profile, binding, manifest = _binding_profile_manifest()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        backend = SupervisedLocalQwenBackend(
            process=process,
            messages=queue.Queue(),
            stderr_chunks=[],
            profile=profile,
            worker_id="worker-synthetic-timeout",
            loaded_facts={"synthetic": True},
            capability=p4q._REAL_RUNTIME_CAPABILITY,
        )
        backend._generation_started = True
        with self.assertRaises(SupervisedWorkerFailure) as captured:
            backend._receive(timeout=1)
        self.assertEqual(captured.exception.failure_code, "generation_timeout")
        facts = backend.teardown_facts
        self.assertTrue(facts["worker_exit_verified"])
        self.assertTrue(facts["terminate_sent"] or facts["kill_sent"])
        self.assertIsNotNone(process.poll())
        lease = PilotExecutionLease.create(
            pilot=binding, manifest=manifest, parent_pid=1
        )
        receipt = PilotSupervisorReceipt.create(
            lease=lease,
            terminal_status="generation_timeout",
            worker_id=facts["worker_id"],
            worker_pid=facts["worker_pid"],
            worker_exit_code=facts["worker_exit_code"],
            worker_exit_verified=True,
            graceful_shutdown_requested=False,
            terminate_sent=bool(facts["terminate_sent"]),
            kill_sent=bool(facts["kill_sent"]),
            generation_started=True,
            raw_status="not_captured",
            latest_attempt_result_identity=None,
            pilot_outcome_identity=None,
            stderr_identity=facts["stderr_identity"],
            model_action=True,
        )
        receipt.validate()
        self.assertEqual(receipt.terminal_status, "generation_timeout")

    def test_windows_teardown_oserror_is_recorded_as_unverified(self):
        _, _, profile, binding, manifest = _binding_profile_manifest()

        class BrokenStream:
            def write(self, _: str) -> None:
                return None

            def flush(self) -> None:
                return None

        class BrokenProcess:
            pid = 4343
            stdin = BrokenStream()

            @staticmethod
            def poll():
                return None

            @staticmethod
            def terminate():
                raise OSError("synthetic Windows terminate failure")

            @staticmethod
            def kill():
                raise OSError("synthetic Windows kill failure")

            @staticmethod
            def wait(timeout=None):
                del timeout
                raise OSError("synthetic Windows wait failure")

        backend = SupervisedLocalQwenBackend(
            process=BrokenProcess(),
            messages=queue.Queue(),
            stderr_chunks=[],
            profile=profile,
            worker_id="worker-unverified-exit",
            loaded_facts={"synthetic": True},
            capability=p4q._REAL_RUNTIME_CAPABILITY,
        )
        backend._force_teardown("generation_timeout")
        facts = backend.teardown_facts
        self.assertFalse(facts["worker_exit_verified"])
        self.assertEqual(facts["worker_pid"], 4343)
        self.assertEqual(facts["terminal_status"], "worker_teardown_unverified")
        lease = PilotExecutionLease.create(
            pilot=binding, manifest=manifest, parent_pid=1
        )
        receipt = PilotSupervisorReceipt.create(
            lease=lease,
            terminal_status="worker_teardown_unverified",
            worker_id=facts["worker_id"],
            worker_pid=facts["worker_pid"],
            worker_exit_code=None,
            worker_exit_verified=False,
            graceful_shutdown_requested=False,
            terminate_sent=True,
            kill_sent=True,
            generation_started=True,
            raw_status="not_captured",
            latest_attempt_result_identity=None,
            pilot_outcome_identity=None,
            stderr_identity=facts["stderr_identity"],
            model_action=True,
        )
        receipt.validate()

    def test_p4_02a_state_and_lazy_backend_remain_separate(self):
        b_input = synthetic_commerce_b_input()
        state = create_initial_state(b_input)
        self.assertEqual(state["source_kind"], "deterministic_synthetic_fixture")
        self.assertEqual(state["action_state"]["model_action"], False)
        _, _, profile, _, _ = _binding_profile_manifest()
        backend = p4q._LazyTransformersQwenBackend(
            model_root=Path("C:/nonexistent-model-root"), profile=profile
        )
        self.assertFalse(backend.loaded)
        with self.assertRaises(Phase4LocalQwenContractError):
            backend.load()
        self.assertEqual(state["execution_counts"], {node_id: 0 for node_id in ("F1", "F2", "F3", "F4")})


if __name__ == "__main__":
    unittest.main()
