"""Focused no-model tests for the Phase 4 local Qwen pre-call foundation.

The focused collection currently contains 18 tests; none loads or calls a
model.
"""

from __future__ import annotations

import json
import hashlib
import copy
import io
import os
import queue
import shutil
import subprocess
import sys
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

import req2web_runtime.phase4_local_qwen as p4q
from scripts import run_phase4_local_qwen_pilot as pilot_script

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

    def _run_mocked_integrated_success(
        self,
        *,
        teardown,
        stderr_bytes,
        stderr_persist_side_effect=None,
    ):
        b_input, policies, profile, binding, manifest = _binding_profile_manifest()
        root = Path.cwd() / ".p4-03-test-results" / f"supervisor-{len(self._test_roots)}"
        root.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(root)
        lease = PilotExecutionLease.create(
            pilot=binding, manifest=manifest, parent_pid=1
        )

        class FakeBackend:
            generation_started = False

            def close(self):
                return copy.deepcopy(teardown)

            @property
            def stderr_bytes(self):
                return stderr_bytes

        backend = FakeBackend()

        class FakeRuntime:
            pass

        runtime = FakeRuntime()
        runtime.backend = backend
        runtime.pilot = binding
        runtime.policies = policies
        runtime.profile = profile
        runtime.manifest = manifest
        runtime.b_input = b_input
        runtime.load_receipt = object()
        runtime.execution_lease = lease
        runtime.runtime_start_claim = object()

        class SuccessfulAttempt:
            failure_code = None

        class IntegratedSuccess:
            status = "integrated_success"

            @staticmethod
            def to_dict():
                return {"status": "integrated_success"}

        class FakeRunner:
            stopped = False
            latest_result = None

            @staticmethod
            def run_node_local(*, node_id):
                del node_id
                return SuccessfulAttempt()

            @staticmethod
            def run_integrated():
                return IntegratedSuccess()

        captured_receipts = []
        with patch.object(
            pilot_script,
            "load_prepared_local_qwen_pilot",
            return_value=(binding, policies, profile, manifest),
        ), patch.object(
            pilot_script,
            "acquire_pilot_execution_lease",
            return_value=lease,
        ), patch.object(
            pilot_script,
            "start_supervised_local_qwen_runtime",
            return_value=runtime,
        ), patch.object(
            pilot_script,
            "Phase4LocalQwenPilotRunner",
            return_value=FakeRunner(),
        ), patch.object(
            pilot_script,
            "persist_pilot_outcome",
        ), patch.object(
            pilot_script,
            "persist_worker_stderr_artifact",
            side_effect=stderr_persist_side_effect,
        ) as stderr_persist, patch.object(
            pilot_script,
            "persist_supervisor_receipt",
            side_effect=lambda **kwargs: captured_receipts.append(kwargs["receipt"]),
        ):
            return_code = pilot_script.main(
                [
                    "run",
                    "--model-root",
                    str(root / "model"),
                    "--integrity-evidence",
                    str(root / "integrity.json"),
                    "--result-root",
                    str(root),
                ]
            )
        return return_code, captured_receipts[0], stderr_persist

    def test_worker_stderr_artifact_round_trip_binds_exact_file_bytes(self):
        root = Path.cwd() / ".p4-03-test-results" / "stderr-round-trip"
        root.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(root)
        raw = b"load traceback\x00\xff\n"
        artifact = p4q.persist_worker_stderr_artifact(
            result_root=root, stderr_bytes=raw
        )
        persisted = p4q.WorkerStderrArtifact.from_bytes(
            (root / "worker_stderr.json").read_bytes()
        )
        self.assertEqual(persisted.stderr_bytes, raw)
        self.assertEqual(
            persisted.canonical_bytes(), (root / "worker_stderr.json").read_bytes()
        )
        self.assertEqual(artifact.identity(), persisted.identity())
        self.assertEqual(
            artifact.identity(),
            p4q.make_canonical_identity(
                persisted.to_dict(), revision=p4q.WORKER_STDERR_SCHEMA_VERSION
            ),
        )

    def test_start_failure_carries_immutable_stderr_bytes(self):
        class FakeProcess:
            pid = 5252

            def __init__(self):
                self.exit_code = None

            def poll(self):
                return self.exit_code

            def terminate(self):
                self.exit_code = 17

            def kill(self):
                self.exit_code = 18

            def wait(self, timeout=None):
                del timeout
                if self.exit_code is None:
                    self.exit_code = 0
                return self.exit_code

        raw = b"Traceback\nload failed\n"
        capture_state = p4q._WorkerStderrCapture()
        capture_state.append(raw[:10])
        capture_state.append(raw[10:])
        capture_state.complete()

        class JoinedThread:
            def join(self, timeout=None):
                del timeout

            @staticmethod
            def is_alive():
                return False

        with self.assertRaises(p4q.SupervisedWorkerStartFailure) as captured:
            p4q._raise_worker_start_failure(
                process=FakeProcess(),
                stderr_capture=capture_state,
                stderr_thread=JoinedThread(),
                message="synthetic load failure",
            )
        self.assertEqual(captured.exception.stderr_bytes, raw)
        self.assertEqual(
            captured.exception.teardown_facts["stderr_identity"],
            p4q.WorkerStderrArtifact.create(stderr_bytes=raw).identity(),
        )

    def test_incomplete_stderr_capture_never_claims_an_artifact_identity(self):
        class ExitedProcess:
            pid = 5262

            @staticmethod
            def poll():
                return 2

        class AliveThread:
            def join(self, timeout=None):
                del timeout

            @staticmethod
            def is_alive():
                return True

        delayed = p4q._WorkerStderrCapture()
        delayed.append(b"partial delayed stderr")
        with self.assertRaises(p4q.SupervisedWorkerStartFailure) as alive_failure:
            p4q._raise_worker_start_failure(
                process=ExitedProcess(),
                stderr_capture=delayed,
                stderr_thread=AliveThread(),
                message="synthetic delayed stderr",
            )
        self.assertIsNone(alive_failure.exception.stderr_bytes)
        self.assertFalse(
            alive_failure.exception.teardown_facts["stderr_capture_completed"]
        )
        self.assertIsNone(alive_failure.exception.teardown_facts["stderr_identity"])

        class FailingBuffer:
            def __init__(self):
                self.calls = 0

            def read(self, _):
                self.calls += 1
                if self.calls == 1:
                    return b"partial before read failure"
                raise OSError("synthetic stderr read failure")

        class FailingStream:
            def __init__(self):
                self.buffer = FailingBuffer()

        failed = p4q._WorkerStderrCapture()
        p4q._worker_stderr_reader(FailingStream(), failed)
        snapshot = failed.snapshot(
            stderr_thread=None,
            worker_exit_verified=True,
        )
        self.assertFalse(snapshot["completed"])
        self.assertIn("synthetic stderr read failure", snapshot["error"])
        self.assertIsNone(snapshot["stderr_bytes"])

    def test_worker_load_exception_writes_traceback_without_changing_stdout_schema(self):
        class FakeStream:
            def __init__(self, raw=b""):
                self.buffer = io.BytesIO(raw)

        class FakeBackend:
            def __init__(self, **_):
                pass

            def load(self):
                raise RuntimeError("synthetic load traceback")

        old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
        try:
            sys.stdin = FakeStream(
                p4q._canonical_bytes(
                    {
                        "protocol": "req2web.phase4.p4_03.worker-ipc.v1",
                        "kind": "load",
                        "profile_b64": "e30=",
                    }
                )
                + b"\n"
            )
            stdout = FakeStream()
            stderr = io.StringIO()
            sys.stdout = stdout
            sys.stderr = stderr
            with patch(
                "req2web_runtime.phase4_local_qwen._LazyTransformersQwenBackend",
                FakeBackend,
            ), patch.object(
                p4q.LocalQwenProfile,
                "from_bytes",
                return_value=object(),
            ):
                self.assertEqual(
                    p4q._run_local_qwen_worker_protocol(model_root=Path("C:/model")),
                    2,
                )
            response = p4q._strict_json(stdout.buffer.getvalue().rstrip(b"\r\n"))
            self.assertEqual(set(response), {"protocol", "kind"})
            self.assertEqual(response["kind"], "load_error")
            self.assertIn("Traceback", stderr.getvalue())
            self.assertIn("synthetic load traceback", stderr.getvalue())
        finally:
            sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr

    def test_stderr_artifact_persistence_failure_keeps_receipt_identity_absent(self):
        b_input, policies, profile, binding, manifest = _binding_profile_manifest()
        root = Path.cwd() / ".p4-03-test-results" / "stderr-persist-failure"
        root.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(root)
        lease = PilotExecutionLease.create(
            pilot=binding, manifest=manifest, parent_pid=1
        )
        teardown = {
            "worker_id": "worker-stderr-failure",
            "worker_pid": 5353,
            "worker_exit_code": 2,
            "worker_exit_verified": True,
            "graceful_shutdown_requested": False,
            "terminate_sent": True,
            "kill_sent": False,
            "terminal_status": "load_failed",
            "stderr_capture_completed": True,
            "stderr_capture_error": None,
            "stderr_thread_joined": True,
            "stderr_identity": p4q.WorkerStderrArtifact.create(
                stderr_bytes=b"traceback"
            ).identity(),
        }
        captured_receipts = []
        with patch.object(
            pilot_script,
            "load_prepared_local_qwen_pilot",
            return_value=(binding, policies, profile, manifest),
        ), patch.object(
            pilot_script,
            "acquire_pilot_execution_lease",
            return_value=lease,
        ), patch.object(
            pilot_script,
            "start_supervised_local_qwen_runtime",
            side_effect=p4q.SupervisedWorkerStartFailure(
                "synthetic startup failure",
                teardown,
                stderr_bytes=b"traceback",
            ),
        ), patch.object(
            pilot_script,
            "persist_worker_stderr_artifact",
            side_effect=OSError("synthetic stderr artifact failure"),
        ), patch.object(
            pilot_script,
            "persist_supervisor_receipt",
            side_effect=lambda **kwargs: captured_receipts.append(kwargs["receipt"]),
        ):
            self.assertEqual(
                pilot_script.main(
                    [
                        "run",
                        "--model-root",
                        str(root / "model"),
                        "--integrity-evidence",
                        str(root / "integrity.json"),
                        "--result-root",
                        str(root),
                    ]
                ),
                2,
            )
        self.assertEqual(len(captured_receipts), 1)
        receipt = captured_receipts[0]
        self.assertEqual(receipt.terminal_status, "evidence_persistence_failed")
        self.assertIsNone(receipt.stderr_identity)

    def test_integrated_success_is_not_returned_when_stderr_persistence_fails(self):
        raw = b"complete stderr"
        teardown = {
            "worker_id": "worker-normal-close",
            "worker_pid": 5454,
            "worker_exit_code": 0,
            "worker_exit_verified": True,
            "graceful_shutdown_requested": True,
            "terminate_sent": False,
            "kill_sent": False,
            "terminal_status": "normal_completed",
            "stderr_capture_completed": True,
            "stderr_capture_error": None,
            "stderr_thread_joined": True,
            "stderr_identity": p4q.WorkerStderrArtifact.create(
                stderr_bytes=raw
            ).identity(),
        }
        code, receipt, stderr_persist = self._run_mocked_integrated_success(
            teardown=teardown,
            stderr_bytes=raw,
            stderr_persist_side_effect=OSError("synthetic stderr persistence failure"),
        )
        self.assertEqual(code, 2)
        self.assertEqual(receipt.terminal_status, "evidence_persistence_failed")
        self.assertIsNone(receipt.stderr_identity)
        stderr_persist.assert_called_once()

    def test_unverified_worker_exit_outranks_incomplete_stderr_failure(self):
        teardown = {
            "worker_id": "worker-unverified-close",
            "worker_pid": 5555,
            "worker_exit_code": None,
            "worker_exit_verified": False,
            "graceful_shutdown_requested": True,
            "terminate_sent": True,
            "kill_sent": True,
            "terminal_status": "worker_teardown_unverified",
            "stderr_capture_completed": False,
            "stderr_capture_error": "worker_exit_unverified",
            "stderr_thread_joined": False,
            "stderr_identity": None,
        }
        code, receipt, stderr_persist = self._run_mocked_integrated_success(
            teardown=teardown,
            stderr_bytes=None,
        )
        self.assertEqual(code, 2)
        self.assertEqual(receipt.terminal_status, "worker_teardown_unverified")
        self.assertIsNone(receipt.stderr_identity)
        stderr_persist.assert_not_called()

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

    def test_node_specific_prompts_are_canonical_replayable_and_capped(self):
        _, policies, profile, _, _ = _binding_profile_manifest()
        input_bytes = b'{"same":"canonical-input"}'
        expected_guidance = {
            "F1": (
                'literal "section"',
                'literal "component"',
                "Avoid redundant sections or components",
                "without fixing an exact entity count",
            ),
            "F2": (
                "new F2-owned local_id",
                "does not reuse any F1 section or component local_id",
                "exact local_id from upstream F1 components only",
                "preserve F1 component order as a subsequence",
            ),
            "F3": (
                'literal entity_type value "interaction"',
                "trigger_component_local_id",
                "upstream F2 state local IDs",
                "distinct use-case and state transitions",
                "component-by-state Cartesian product",
                "automatically create an interaction for every visible component",
                "do not repeat equivalent actions",
                "without fixing an exact interaction count or weakening reference validation",
            ),
            "F4": (
                'literal entity_type value "candidate_acceptance_check"',
                "authority_bindings.canonical_b_use_case_refs",
                "authority_bindings.f2_state_refs",
                "complete coverage of every canonical use case",
                "state-by-use-case Cartesian product",
                "do not fix an exact check count or weaken use-case or state-ref validation",
            ),
        }
        expected_keys = {
            "prompt_schema_version",
            "node_id",
            "template_revision",
            "output_format",
            "input_sha256",
            "input_byte_length",
            "instructions",
            "output_contract",
            "model_id",
            "model_revision",
        }
        for policy in policies:
            with self.subTest(node_id=policy.node_id):
                first = build_prompt_v1(
                    node_id=policy.node_id,
                    input_bytes=input_bytes,
                    policy=policy,
                    profile=profile,
                )
                replay = build_prompt_v1(
                    node_id=policy.node_id,
                    input_bytes=input_bytes,
                    policy=policy,
                    profile=profile,
                )
                self.assertEqual(first, replay)
                envelope = json.loads(first.decode("utf-8"))
                self.assertEqual(set(envelope), expected_keys)
                self.assertEqual(p4q._canonical_bytes(envelope), first)
                self.assertLessEqual(len(first), policy.field_caps["prompt_bytes"])
                guidance = "\n".join(envelope["instructions"])
                for fragment in (
                    "minified compact JSON",
                    "free-text field concise and non-redundant",
                    "smallest complete entity collection",
                    "close every string, array, and object",
                ):
                    self.assertIn(fragment, guidance)
                for fragment in expected_guidance[policy.node_id]:
                    self.assertIn(fragment, guidance)
                if policy.node_id == "F1":
                    self.assertNotRegex(
                        guidance,
                        r"\b\d+\s+(?:sections|components)\b|\bexactly\s+\d+\b",
                    )
                if policy.node_id in {"F3", "F4"}:
                    self.assertNotRegex(
                        guidance,
                        r"\b\d+\s+(?:interactions|acceptance_checks|checks)\b|\bexactly\s+\d+\b",
                    )
                if policy.node_id == "F4":
                    self.assertIn(
                        "collectively cover every use case",
                        envelope["output_contract"]["use_case_ref_rule"],
                    )

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

    def test_live_placement_allows_missing_or_empty_optional_device_map(self):
        class Parameter:
            def __init__(self, device):
                self.device = device

        class MissingMapModel:
            def parameters(self):
                return [Parameter("cuda:0")]

        class Model:
            def __init__(self, device_map):
                self.hf_device_map = device_map

            def parameters(self):
                return [Parameter("cuda:0")]

        for model in (MissingMapModel(), Model({})):
            normalized_map, parameter_devices = p4q._validate_live_model_placement(model)
            self.assertEqual(normalized_map, {})
            self.assertEqual(parameter_devices, {"cuda:0"})

    def test_live_placement_rejects_non_mapping_device_map(self):
        class Model:
            hf_device_map = ["cuda:0"]

            @staticmethod
            def parameters():
                return [type("Parameter", (), {"device": "cuda:0"})()]

        with self.assertRaises(Phase4LocalQwenContractError):
            p4q._validate_live_model_placement(Model())

    def test_device_map_rejects_non_string_and_duplicate_mapping_keys(self):
        for device_map in ({0: 0}, {object(): 0}):
            with self.subTest(device_map=device_map):
                with self.assertRaises(Phase4LocalQwenContractError):
                    p4q._normalize_hf_device_map(device_map)

        class DuplicateItemsMapping(Mapping):
            def __getitem__(self, key):
                if key == "":
                    return 0
                raise KeyError(key)

            def __iter__(self):
                return iter(("",))

            def __len__(self):
                return 1

            def items(self):
                return (("", 0), ("", 0))

        with self.assertRaises(Phase4LocalQwenContractError):
            p4q._normalize_hf_device_map(DuplicateItemsMapping())

    def test_device_map_rejects_boolean_and_float_devices(self):
        for device in (False, True, 0.0, 1.0):
            with self.subTest(device=device):
                with self.assertRaises(Phase4LocalQwenContractError):
                    p4q._normalize_hf_device_map({"": device})

    def test_live_placement_rejects_cpu_or_disk_device_map(self):
        class Model:
            def __init__(self, device_map):
                self.hf_device_map = device_map

            @staticmethod
            def parameters():
                return [type("Parameter", (), {"device": "cuda:0"})()]

        for device_map in ({"": "cpu"}, {"": "disk"}):
            with self.subTest(device_map=device_map):
                with self.assertRaises(Phase4LocalQwenContractError):
                    p4q._validate_live_model_placement(Model(device_map))

    def test_live_placement_rejects_parameters_mixed_with_cpu(self):
        class Parameter:
            def __init__(self, device):
                self.device = device

        class Model:
            hf_device_map = {}

            @staticmethod
            def parameters():
                return [Parameter("cuda:0"), Parameter("cpu")]

        with self.assertRaises(Phase4LocalQwenContractError):
            p4q._validate_live_model_placement(Model())

    def test_load_receipt_allows_empty_map_but_rejects_forged_placement(self):
        _, _, profile, binding, manifest = _binding_profile_manifest()
        loaded_facts = {
            "model_class": "Qwen3_5ForConditionalGeneration",
            "processor_class": "Qwen3VLProcessor",
            "is_loaded_in_4bit": True,
            "hf_device_map": {},
            "parameter_devices": ["cuda:0"],
            "compute_dtypes": ["torch.bfloat16"],
            "cpu_offload": False,
            "device": "cuda:0",
        }
        receipt = LocalQwenLoadReceipt.create(
            manifest=manifest,
            pilot=binding,
            profile=profile,
            loaded_facts=loaded_facts,
        )
        roundtrip = LocalQwenLoadReceipt.from_bytes(receipt.canonical_bytes())
        roundtrip.validate_against(manifest=manifest, pilot=binding, profile=profile)
        self.assertEqual(roundtrip.loaded_facts["hf_device_map"], {})

        for forged in (
            {**loaded_facts, "hf_device_map": {"": "cpu"}},
            {**loaded_facts, "parameter_devices": ["cpu"]},
        ):
            with self.subTest(forged=forged):
                with self.assertRaises(Phase4LocalQwenContractError):
                    LocalQwenLoadReceipt.create(
                        manifest=manifest,
                        pilot=binding,
                        profile=profile,
                        loaded_facts=forged,
                    )

    def test_v1_load_receipt_preserves_integer_zero_device_map(self):
        _, _, profile, binding, manifest = _binding_profile_manifest()
        receipt = LocalQwenLoadReceipt.create(
            manifest=manifest,
            pilot=binding,
            profile=profile,
            loaded_facts={
                "model_class": "Qwen3_5ForConditionalGeneration",
                "processor_class": "Qwen3VLProcessor",
                "is_loaded_in_4bit": True,
                "hf_device_map": {"": 0},
                "parameter_devices": ["cuda:0"],
                "compute_dtypes": ["torch.bfloat16"],
                "cpu_offload": False,
                "device": "cuda:0",
            },
        )
        roundtrip = LocalQwenLoadReceipt.from_bytes(receipt.canonical_bytes())
        roundtrip.validate_against(manifest=manifest, pilot=binding, profile=profile)
        self.assertEqual(roundtrip.loaded_facts["hf_device_map"], {"": 0})
        self.assertIs(type(roundtrip.loaded_facts["hf_device_map"][""]), int)

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
        fixture = _fixture_bytes(b_input)
        private_prior_raw = b'{"private_marker":"F2_PRIOR_RAW_MUST_NOT_APPEAR"}'
        runner, _, _ = self._runner(FailingBackend([fixture[0], private_prior_raw]))
        self.assertIsNone(runner.run_node_local(node_id="F1").failure_code)
        result = runner.run_node_local(node_id="F2")
        self.assertEqual(result.failure_code, "node_contract_invalid")
        prompt = build_prompt_v2(
            node_id="F2",
            input_bytes=b'{"input":"same"}',
            policy=policies[1],
            profile=profile,
            prior_failure=result,
            change_reason=CHANGE_REASONS[0],
        )
        envelope = json.loads(prompt.decode("utf-8"))
        self.assertEqual(envelope["prompt_schema_version"], "req2web.phase4.p4_03.prompt.v2")
        self.assertEqual(envelope["prior_failure_identity"]["sha256"], make_identity(result.to_dict(), revision="req2web.phase4.p4_03.attempt_result.v1")["sha256"])
        self.assertEqual(p4q._canonical_bytes(envelope), prompt)
        self.assertLessEqual(len(prompt), policies[1].field_caps["prompt_bytes"])
        guidance = "\n".join(envelope["instructions"])
        self.assertIn("failure_code=node_contract_invalid", guidance)
        self.assertIn("new F2-owned local_id", guidance)
        self.assertIn("rebuild one complete minified JSON object", guidance)
        self.assertIn("close every string, array, and object", guidance)
        self.assertNotIn("truncat", guidance.lower())
        self.assertNotIn("F2_PRIOR_RAW_MUST_NOT_APPEAR", prompt.decode("utf-8"))
        with self.assertRaises(Phase4LocalQwenContractError):
            build_prompt_v2(node_id="F2", input_bytes=b'{"input":"same"}', policy=policies[1], profile=profile, prior_failure=result, change_reason="free_form_tuning")

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

    def test_supervised_worker_python_runtime_is_exact_and_rejects_injection(self):
        executable, pythonpath = p4q._supervised_worker_python_runtime()
        expected_executable = (
            str(Path(sys._base_executable).resolve(strict=True))
            if os.name == "nt"
            else sys.executable
        )
        self.assertEqual(executable, expected_executable)
        self.assertTrue(Path(executable).is_absolute())
        self.assertTrue(Path(executable).is_file())

        expected_paths = [Path(p4q.__file__).resolve(strict=True).parent.parent]
        for key in ("purelib", "platlib"):
            path = Path(p4q.sysconfig.get_paths()[key]).resolve(strict=True)
            if path not in expected_paths:
                expected_paths.append(path)
        actual_paths = tuple(Path(item) for item in pythonpath.split(os.pathsep))
        self.assertEqual(actual_paths, tuple(expected_paths))
        self.assertTrue(all(path.is_absolute() and path.is_dir() for path in actual_paths))

        injected_paths = dict(p4q.sysconfig.get_paths())
        injected_paths["purelib"] = (
            injected_paths["purelib"]
            + os.pathsep
            + injected_paths["platlib"]
        )
        with patch.object(p4q.sysconfig, "get_paths", return_value=injected_paths):
            with self.assertRaises(Phase4LocalQwenContractError):
                p4q._supervised_worker_python_runtime()

        executable_attr = "_base_executable" if os.name == "nt" else "executable"
        with patch.object(p4q.sys, executable_attr, "relative-python"):
            with self.assertRaises(Phase4LocalQwenContractError):
                p4q._supervised_worker_python_runtime()

    @unittest.skipUnless(os.name == "nt", "Windows redirector probe")
    def test_windows_base_worker_probe_preserves_pid_and_imports_runtime(self):
        executable, pythonpath = p4q._supervised_worker_python_runtime()
        environment = dict(os.environ)
        environment.update(
            {
                "PYTHONPATH": pythonpath,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1",
                "LANGSMITH_TRACING": "0",
                "LANGCHAIN_TRACING_V2": "0",
            }
        )
        probe = (
            "import json,os; "
            "import accelerate,bitsandbytes,torch,transformers; "
            "import req2web_runtime.phase4_local_qwen as module; "
            "print(json.dumps({'pid':os.getpid(),'schema':module.P4_03_SCHEMA_PREFIX,"
            "'transformers':transformers.__version__,'torch':torch.__version__,"
            "'bitsandbytes':bitsandbytes.__version__,'accelerate':accelerate.__version__},"
            "sort_keys=True))"
        )
        process = subprocess.Popen(
            [executable, "-c", probe],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        parent_observed_pid = process.pid
        stdout, stderr = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 0, stderr)
        payload = json.loads(stdout.strip())
        self.assertEqual(payload["pid"], parent_observed_pid)
        self.assertEqual(payload["schema"], p4q.P4_03_SCHEMA_PREFIX)
        self.assertEqual(payload["transformers"], p4q.TRANSFORMERS_VERSION)
        self.assertEqual(payload["torch"], p4q.TORCH_VERSION)
        self.assertEqual(payload["bitsandbytes"], p4q.BITSANDBYTES_VERSION)
        self.assertEqual(payload["accelerate"], p4q.ACCELERATE_VERSION)

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
        ), patch.dict(
            p4q.os.environ,
            {"PYTHONPATH": "caller-injected"},
            clear=False,
        ):
            with self.assertRaises(p4q.SupervisedWorkerStartFailure) as captured:
                p4q.start_supervised_local_qwen_runtime(
                    model_root=model_root,
                    integrity_evidence=integrity_evidence,
                    result_root=result_root,
                    load_timeout_seconds=1,
                )
            self.assertEqual(popen.call_count, 1)
            expected_executable, expected_pythonpath = (
                p4q._supervised_worker_python_runtime()
            )
            self.assertEqual(popen.call_args.args[0][0], expected_executable)
            self.assertEqual(
                popen.call_args.kwargs["env"]["PYTHONPATH"], expected_pythonpath
            )
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
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                self.addCleanup(stream.close)
        backend = SupervisedLocalQwenBackend(
            process=process,
            messages=queue.Queue(),
            stderr_capture=p4q._WorkerStderrCapture(),
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
            stderr_capture=p4q._WorkerStderrCapture(),
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
