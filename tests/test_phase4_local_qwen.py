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
    P4R2ResultRootBinding,
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
    load_p4r2_policy_revision,
    validate_model_inventory_metadata,
    validate_p4r2_live_binding,
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


def _prepare_tiny_r2_artifact(parent: Path):
    model_root, integrity_evidence = _write_fake_integrity(parent)
    b_input = synthetic_commerce_b_input()
    result_root = parent / "prepared-r2"
    r2_policy, _ = load_p4r2_policy_revision()
    prepared = prepare_local_qwen_pilot(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        result_root=result_root,
        case_binding=build_synthetic_case_binding(b_input),
        policies=pilot_script._r2_historical_policies(),
        pilot_id=r2_policy.pilot_id,
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
        r2_policy=r2_policy,
    )
    return model_root, integrity_evidence, result_root, b_input, *prepared


def _prepare_tiny_r3_artifact(parent: Path):
    model_root, integrity_evidence = _write_fake_integrity(parent)
    b_input = synthetic_commerce_b_input()
    result_root = parent / "prepared-r3"
    r3_policy, _ = p4q.load_p4r3_policy_revision()
    with patch.object(
        p4q,
        "_load_p4r3_r2_action_time_evidence",
        return_value=p4q._p4r3_expected_action_evidence(),
    ):
        prepared = prepare_local_qwen_pilot(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
            result_root=result_root,
            case_binding=build_synthetic_case_binding(b_input),
            policies=pilot_script._default_policies(),
            pilot_id=r3_policy.pilot_id,
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
            r3_policy=r3_policy,
        )
    return model_root, integrity_evidence, result_root, b_input, *prepared


def _prepare_tiny_r4_artifact(parent: Path):
    model_root, integrity_evidence = _write_fake_integrity(parent)
    b_input = synthetic_commerce_b_input()
    result_root = parent / "prepared-r4"
    r4_policy, _ = p4q.load_p4r4_policy_revision()
    with patch.object(
        p4q,
        "_load_p4r4_r3_action_time_evidence",
        return_value=p4q._p4r4_expected_action_evidence(),
    ):
        prepared = prepare_local_qwen_pilot(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
            result_root=result_root,
            case_binding=build_synthetic_case_binding(b_input),
            policies=pilot_script._r4_policies(),
            pilot_id=r4_policy.pilot_id,
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
            r4_policy=r4_policy,
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

    def test_windows_unsigned_exit_codes_normalize_to_signed_receipt_values(self):
        self.assertEqual(p4q._normalize_process_exit_code(0xFFFFFFFF), -1)
        self.assertEqual(
            p4q._normalize_process_exit_code(0xC000013A),
            0xC000013A - 0x100000000,
        )
        for value in (0, 17, -1, -15, -2147483648, 2147483647):
            with self.subTest(value=value):
                self.assertEqual(p4q._normalize_process_exit_code(value), value)
        for invalid in (False, True, 0.0, -2147483649, 0x100000000):
            with self.subTest(invalid=invalid):
                with self.assertRaises(Phase4LocalQwenContractError):
                    p4q._normalize_process_exit_code(invalid)

        _, _, profile, binding, manifest = _binding_profile_manifest()

        class ExitedProcess:
            pid = 5656
            stdin = object()

            @staticmethod
            def poll():
                return 0xFFFFFFFF

        backend = SupervisedLocalQwenBackend(
            process=ExitedProcess(),
            messages=queue.Queue(),
            stderr_capture=p4q._WorkerStderrCapture(),
            profile=profile,
            worker_id="worker-unsigned-exit",
            loaded_facts={"synthetic": True},
            capability=p4q._REAL_RUNTIME_CAPABILITY,
        )
        backend._force_teardown("pilot_stopped")
        facts = backend.teardown_facts
        self.assertEqual(facts["worker_exit_code"], -1)
        self.assertTrue(facts["worker_exit_verified"])
        self.assertEqual(facts["terminal_status"], "pilot_stopped")

        lease = PilotExecutionLease.create(
            pilot=binding, manifest=manifest, parent_pid=1
        )
        receipt = PilotSupervisorReceipt.create(
            lease=lease,
            terminal_status="pilot_stopped",
            worker_id="worker-unsigned-exit",
            worker_pid=5656,
            worker_exit_code=0xFFFFFFFF,
            worker_exit_verified=True,
            graceful_shutdown_requested=False,
            terminate_sent=True,
            kill_sent=False,
            generation_started=True,
            raw_status="not_captured",
            latest_attempt_result_identity=None,
            pilot_outcome_identity=None,
            stderr_identity=None,
            model_action=True,
        )
        self.assertEqual(receipt.worker_exit_code, -1)
        roundtrip = PilotSupervisorReceipt.from_bytes(receipt.canonical_bytes())
        self.assertEqual(roundtrip, receipt)
        self.assertEqual(roundtrip.worker_exit_code, -1)

        tampered = receipt.to_dict()
        tampered["worker_exit_code"] = 0xFFFFFFFF
        tampered["receipt_id"] = p4q._identity(
            {key: value for key, value in tampered.items() if key != "receipt_id"},
            revision=p4q.SUPERVISOR_RECEIPT_SCHEMA_VERSION,
        )["sha256"]
        with self.assertRaises(Phase4LocalQwenContractError):
            PilotSupervisorReceipt.from_dict(tampered)

        for invalid in (False, True, -2147483649, 0x100000000):
            with self.subTest(receipt_invalid=invalid):
                with self.assertRaises(Phase4LocalQwenContractError):
                    PilotSupervisorReceipt.create(
                        lease=lease,
                        terminal_status="pilot_stopped",
                        worker_id="worker-invalid-exit",
                        worker_pid=5657,
                        worker_exit_code=invalid,
                        worker_exit_verified=True,
                        graceful_shutdown_requested=False,
                        terminate_sent=True,
                        kill_sent=False,
                        generation_started=True,
                        raw_status="not_captured",
                        latest_attempt_result_identity=None,
                        pilot_outcome_identity=None,
                        stderr_identity=None,
                        model_action=True,
                    )

    def test_worker_start_failure_normalizes_final_windows_poll_code(self):
        class Process:
            pid = 5757

            def __init__(self):
                self.poll_count = 0

            def poll(self):
                self.poll_count += 1
                return None if self.poll_count == 1 else 0xC000013A

            @staticmethod
            def terminate():
                return None

            @staticmethod
            def kill():
                return None

            @staticmethod
            def wait(timeout=None):
                del timeout
                return 0xC000013A

        with self.assertRaises(p4q.SupervisedWorkerStartFailure) as captured:
            p4q._raise_worker_start_failure(
                process=Process(),
                stderr_capture=None,
                message="synthetic unsigned startup exit",
            )
        facts = captured.exception.teardown_facts
        self.assertEqual(
            facts["worker_exit_code"], 0xC000013A - 0x100000000
        )
        self.assertTrue(facts["worker_exit_verified"])
        self.assertEqual(facts["terminal_status"], "load_failed")

    def test_normal_close_normalizes_windows_code_and_none_is_unverified(self):
        _, _, profile, _, _ = _binding_profile_manifest()

        class Stdin:
            @staticmethod
            def write(_):
                return None

            @staticmethod
            def flush():
                return None

        class HighExitProcess:
            pid = 5858
            stdin = Stdin()

            @staticmethod
            def wait(timeout=None):
                del timeout
                return 0xFFFFFFFF

            @staticmethod
            def poll():
                return 0xFFFFFFFF

        high_messages = queue.Queue()
        high_messages.put(
            {"kind": "shutdown_ack", "worker_id": "worker-high-close"}
        )
        high_stderr = p4q._WorkerStderrCapture()
        high_stderr.complete()
        high_backend = SupervisedLocalQwenBackend(
            process=HighExitProcess(),
            messages=high_messages,
            stderr_capture=high_stderr,
            profile=profile,
            worker_id="worker-high-close",
            loaded_facts={"synthetic": True},
            capability=p4q._REAL_RUNTIME_CAPABILITY,
        )
        high_facts = high_backend.close()
        self.assertEqual(high_facts["worker_exit_code"], -1)
        self.assertTrue(high_facts["worker_exit_verified"])
        self.assertEqual(high_facts["terminal_status"], "normal_completed")

        class NoneExitProcess:
            pid = 5959
            stdin = Stdin()

            def __init__(self):
                self.terminate_count = 0

            @staticmethod
            def wait(timeout=None):
                del timeout
                return None

            @staticmethod
            def poll():
                return None

            def terminate(self):
                self.terminate_count += 1

            @staticmethod
            def kill():
                return None

        none_process = NoneExitProcess()
        none_messages = queue.Queue()
        none_messages.put(
            {"kind": "shutdown_ack", "worker_id": "worker-none-close"}
        )
        none_stderr = p4q._WorkerStderrCapture()
        none_stderr.complete()
        none_backend = SupervisedLocalQwenBackend(
            process=none_process,
            messages=none_messages,
            stderr_capture=none_stderr,
            profile=profile,
            worker_id="worker-none-close",
            loaded_facts={"synthetic": True},
            capability=p4q._REAL_RUNTIME_CAPABILITY,
        )
        none_facts = none_backend.close()
        self.assertIsNone(none_facts["worker_exit_code"])
        self.assertFalse(none_facts["worker_exit_verified"])
        self.assertEqual(
            none_facts["terminal_status"], "worker_teardown_unverified"
        )
        self.assertEqual(none_process.terminate_count, 1)

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

    def test_r2_policy_is_canonical_and_rejects_extra_key_and_bool_injection(self):
        policy, raw = load_p4r2_policy_revision()
        self.assertEqual(policy.canonical_bytes(), raw)
        self.assertEqual(
            policy.authority_semantics,
            "frozen_owner_approval_record_not_live_self_authority",
        )
        self.assertEqual(
            policy.authorized_actions,
            {
                "existing_local_model_load_and_generate": True,
                "local_langgraph_runtime": True,
            },
        )
        payload = policy.to_dict()
        payload["unexpected"] = True
        with self.assertRaises(Phase4LocalQwenContractError):
            type(policy).from_dict(payload)
        payload = policy.to_dict()
        payload["prompt"]["f3_interaction_max"] = True
        with self.assertRaises(Phase4LocalQwenContractError):
            type(policy).from_dict(payload)

    def test_r2_terminal_summary_and_r3_policy_are_canonical_and_tamper_closed(self):
        r2_policy, _ = load_p4r2_policy_revision()
        summary, summary_raw = p4q.load_p4r2_result_summary()
        r3_policy, r3_raw = p4q.load_p4r3_policy_revision()
        self.assertEqual(r2_policy.pilot_id, p4q.P4R2_PILOT_ID)
        self.assertEqual(summary.canonical_bytes(), summary_raw)
        self.assertEqual(summary.source_commit, p4q.P4R3_R2_SOURCE_COMMIT)
        self.assertEqual(summary.pilot_outcome["node_total_calls"], {"F1": 2, "F2": 0, "F3": 0, "F4": 0})
        self.assertEqual(r3_policy.canonical_bytes(), r3_raw)
        self.assertEqual(r3_policy.pilot_id, p4q.P4R3_PILOT_ID)
        self.assertEqual(r3_policy.runtime, {"context_expansion": False, "max_new_tokens": 512})

        for mutator in (
            lambda payload: payload.update({"unexpected": True}),
            lambda payload: payload["projection"].update({"allowed_categories_unchanged": 1}),
            lambda payload: payload["predecessor_r2_result"].update({"summary_raw_byte_length": True}),
        ):
            payload = r3_policy.to_dict()
            mutator(payload)
            with self.assertRaises(Phase4LocalQwenContractError):
                p4q.P4R3Policy.from_dict(payload)

        summary_payload = summary.to_dict()
        summary_payload["pilot_outcome"]["node_total_calls"]["F2"] = False
        with self.assertRaises(Phase4LocalQwenContractError):
            p4q.P4R2ResultSummary.from_dict(summary_payload)

    def test_r3_prepare_binds_tracked_r2_summary_and_action_time_identities(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"r3-binding-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)
        model_root, _, result_root, _, binding, policies, _, _ = _prepare_tiny_r3_artifact(parent)
        self.assertEqual(binding.pilot_id, p4q.P4R3_PILOT_ID)
        self.assertTrue((result_root / p4q.P4R3_RESULT_POLICY_NAME).is_file())
        self.assertTrue((result_root / p4q.P4R3_RESULT_R2_SUMMARY_NAME).is_file())
        root_binding = p4q.P4R3ResultRootBinding.from_bytes(
            (result_root / p4q.P4R3_RESULT_BINDING_NAME).read_bytes()
        )
        self.assertEqual(root_binding.pilot_binding_policy_id, binding.policy_id)
        self.assertEqual(root_binding.r2_summary_identity["sha256"], p4q.P4R3_R2_SUMMARY_SHA256)
        self.assertEqual(root_binding.r2_outcome_identity["sha256"], p4q.P4R3_R2_OUTCOME_SHA256)
        self.assertEqual(root_binding.r2_supervisor_identity["sha256"], p4q.P4R3_R2_SUPERVISOR_SHA256)
        self.assertEqual(root_binding.r2_aggregate_identity["sha256"], p4q.P4R3_R2_AGGREGATE_SHA256)
        with patch.object(
            p4q,
            "_load_p4r3_r2_action_time_evidence",
            return_value=p4q._p4r3_expected_action_evidence(),
        ):
            p4q.validate_p4r3_live_binding(
                model_root=model_root,
                result_root=result_root,
                pilot=binding,
                policies=policies,
            )
            loaded = p4q.load_prepared_local_qwen_pilot(
                model_root=model_root,
                integrity_evidence=parent / "local_integrity.json",
                result_root=result_root,
                b_input=synthetic_commerce_b_input(),
                environment={
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "LANGSMITH_TRACING": "0",
                    "LANGCHAIN_TRACING_V2": "0",
                },
                r3_policy=p4q.load_p4r3_policy_revision()[0],
            )
        self.assertEqual(loaded[0].to_dict(), binding.to_dict())
        self.assertEqual(tuple(item.to_dict() for item in loaded[1]), tuple(item.to_dict() for item in policies))

        tampered = root_binding.to_dict()
        tampered["r2_outcome_identity"]["revision"] = "forged-revision"
        tampered["binding_id"] = p4q._sha256(
            p4q._canonical_bytes({key: value for key, value in tampered.items() if key != "binding_id"})
        )
        with self.assertRaises(Phase4LocalQwenContractError):
            p4q.P4R3ResultRootBinding.from_dict(tampered)

    def test_r3_f1_prompt_carries_every_pilot_local_static_structure_rule(self):
        _, _, profile, _, _ = _binding_profile_manifest()
        r3_policies = pilot_script._default_policies()
        r2_policies = pilot_script._r2_historical_policies()
        self.assertEqual({item.prompt_template_revision for item in r3_policies}, {p4q.P4R3_PROMPT_REVISION})
        self.assertEqual({item.prompt_template_revision for item in r2_policies}, {p4q.P4R2_PROMPT_REVISION})
        self.assertEqual(
            {item.projection_revision for item in r3_policies},
            {p4q.P4R2_PROJECTION_REVISION},
        )

        prompt = json.loads(
            build_prompt_v1(
                node_id="F1",
                input_bytes=b"{}",
                policy=r3_policies[0],
                profile=profile,
            ).decode("utf-8")
        )
        guidance = "\n".join(prompt["instructions"])
        self.assertEqual(prompt["template_revision"], p4q.P4R3_PROMPT_REVISION)
        for required in (
            "Generate static structure only",
            "error, loading, visibility, and recovery semantics belong to F2 or F3",
            "Every section must contain exactly local_id, entity_type, title, purpose, component_local_ids, and refs",
            "every component must contain exactly local_id, entity_type, component_type, section_local_id, label, purpose, and refs",
            "every refs value must be []",
            "component_local_ids must exactly partition the components array",
            "every component appears once",
            "each component.section_local_id equals its owning section.local_id",
            "preserve search, product or cart display, delivery input, and a submit trigger",
            "at most 4 sections and at most 6 components",
            "pilot-local size guards, not global F1 schema rules",
            "Do not add deterministic repair",
            "max_new_tokens=512",
        ):
            self.assertIn(required, guidance)
        config = json.loads(
            p4q.build_config_bytes(profile=profile, policy=r3_policies[0]).decode("utf-8")
        )
        self.assertEqual(config["max_new_tokens"], 512)

    def test_r4_summary_policy_prompt_and_profile_are_independent(self):
        summary, summary_raw = p4q.load_p4r3_result_summary()
        policy, policy_raw = p4q.load_p4r4_policy_revision()
        self.assertEqual(summary.canonical_bytes(), summary_raw)
        self.assertEqual(policy.canonical_bytes(), policy_raw)
        self.assertEqual(policy.pilot_id, p4q.P4R4_PILOT_ID)
        self.assertEqual(policy.runtime["max_new_tokens"], 640)
        self.assertNotEqual(policy.pilot_id, p4q.P4R3_PILOT_ID)
        profile = LocalQwenProfile.create(
            model_root_identity=make_identity({"r4": "model"}, revision="test.r4.model.v1"),
            model_inventory_identity=make_identity({"r4": "inventory"}, revision="test.r4.inventory.v1"),
            model_file_count=1,
            max_new_tokens=640,
        )
        r4_prompt = json.loads(
            build_prompt_v1(
                node_id="F1",
                input_bytes=b"{}",
                policy=pilot_script._r4_policies()[0],
                profile=profile,
            ).decode("utf-8")
        )
        guidance = "\n".join(r4_prompt["instructions"])
        self.assertEqual(r4_prompt["template_revision"], p4q.P4R4_PROMPT_REVISION)
        for required in (
            "no more than 5 necessary components and no more than 3 sections",
            "product or cart display (one compact display semantic is sufficient)",
            "extremely short and non-redundant",
            "pilot-local size guards for this fixed case, not global F1 schema rules",
            "max_new_tokens=640",
        ):
            self.assertIn(required, guidance)
        self.assertEqual(json.loads(p4q.build_config_bytes(profile=profile, policy=pilot_script._r4_policies()[0]))["max_new_tokens"], 640)

    def test_r4_prepare_binds_r3_action_evidence_and_uses_separate_shared_aggregate(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"r4-binding-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)
        model_root, _, result_root, _, binding, policies, profile, _ = _prepare_tiny_r4_artifact(parent)
        self.assertEqual(binding.pilot_id, p4q.P4R4_PILOT_ID)
        self.assertEqual(profile.max_new_tokens, 640)
        self.assertTrue((result_root / p4q.P4R4_RESULT_POLICY_NAME).is_file())
        self.assertTrue((result_root / p4q.P4R4_RESULT_R3_SUMMARY_NAME).is_file())
        root_binding = p4q.P4R4ResultRootBinding.from_bytes((result_root / p4q.P4R4_RESULT_BINDING_NAME).read_bytes())
        self.assertEqual(root_binding.r3_summary_identity["sha256"], p4q.P4R3_SUMMARY_SHA256)
        self.assertEqual(root_binding.r3_outcome_identity["sha256"], p4q.P4R3_OUTCOME_SHA256)
        r2_root, r2_name, _ = p4q._p4r2_aggregate_budget_paths(model_root)
        r3_root, r3_name, _ = p4q._p4r3_aggregate_budget_paths(model_root)
        r4_root, r4_name, _ = p4q._p4r4_aggregate_budget_paths(model_root)
        self.assertEqual(r2_root, r3_root)
        self.assertEqual(r3_root, r4_root)
        self.assertNotEqual(r2_name, r3_name)
        self.assertNotEqual(r3_name, r4_name)
        self.assertIsInstance(p4q.P4R4AggregateBudgetLedger.from_bytes((r4_root / r4_name).read_bytes()), p4q.P4R4AggregateBudgetLedger)
        with patch.object(p4q, "_load_p4r4_r3_action_time_evidence", return_value=p4q._p4r4_expected_action_evidence()):
            p4q.validate_p4r4_live_binding(model_root=model_root, result_root=result_root, pilot=binding, policies=policies)

    def test_r2_prepare_writes_direct_projection_and_cross_bound_root_evidence(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"r2-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)
        _, _, result_root, b_input, binding, policies, _, manifest = _prepare_tiny_r2_artifact(parent)
        self.assertEqual(binding.pilot_id, "p4-03r2-local-qwen-9b")
        self.assertTrue((result_root / "p4_03r2_policy.json").is_file())
        self.assertTrue((result_root / "p4_03r2_predecessor_aggregate_ledger.json").is_file())
        root_binding = P4R2ResultRootBinding.from_bytes(
            (result_root / "p4_03r2_result_root_binding.json").read_bytes()
        )
        self.assertEqual(root_binding.pilot_binding_policy_id, binding.policy_id)
        validate_p4r2_live_binding(result_root=result_root, pilot=binding, policies=policies)
        state = phase4_create_authority_state(b_input)
        upstream = {}
        for node_id in ("F1", "F2"):
            output = phase4_synthetic_fixture_output(node_id, state)
            upstream[node_id] = json.dumps(
                output,
                ensure_ascii=False,
                indent=2 if node_id == "F1" else None,
                separators=None if node_id == "F1" else (",", ":"),
            ).encode("utf-8")
            state = phase4_register_node_output(state, node_id, output)
        actual = derive_node_input(
            node_id="F3",
            b_input_bytes=p4q._canonical_bytes(b_input),
            upstream_outputs=upstream,
            authority_state=state,
            policy=policies[2],
        )
        projection = json.loads(actual.decode("utf-8"))
        self.assertEqual(projection["input_schema_version"], p4q.P4R2_NODE_INPUT_SCHEMA_VERSION)
        self.assertIsInstance(projection["canonical_b_input"], dict)
        self.assertIsInstance(projection["upstream"][0]["output"], dict)
        self.assertEqual(projection["canonical_b_input_sha256"], p4q._sha256(p4q._canonical_bytes(projection["canonical_b_input"])))
        self.assertEqual(projection["upstream"][0]["raw_sha256"], p4q._sha256(p4q._decode_b64(projection["upstream"][0]["raw_b64"], "test.raw_b64")))
        self.assertEqual(
            projection["canonical_b_input_identity"],
            {
                "sha256": p4q._sha256(p4q._canonical_bytes(projection["canonical_b_input"])),
                "byte_length": len(p4q._canonical_bytes(projection["canonical_b_input"])),
                "revision": p4q.P4R2_READABLE_JSON_IDENTITY_REVISION,
            },
        )
        self.assertEqual(
            projection["upstream"][0]["canonical_identity"],
            {
                "sha256": p4q._sha256(p4q._canonical_bytes(projection["upstream"][0]["output"])),
                "byte_length": len(p4q._canonical_bytes(projection["upstream"][0]["output"])),
                "revision": p4q.P4R2_READABLE_JSON_IDENTITY_REVISION,
            },
        )
        self.assertNotEqual(
            projection["upstream"][0]["raw_sha256"],
            projection["upstream"][0]["canonical_identity"]["sha256"],
        )
        f1_prompt = json.loads(build_prompt_v1(node_id="F1", input_bytes=actual, policy=policies[0], profile=manifest.profile and LocalQwenProfile.from_dict(manifest.profile)).decode("utf-8"))
        f3_prompt = json.loads(build_prompt_v1(node_id="F3", input_bytes=actual, policy=policies[2], profile=LocalQwenProfile.from_dict(manifest.profile)).decode("utf-8"))
        f1_guidance = "\n".join(f1_prompt["instructions"])
        f3_guidance = "\n".join(f3_prompt["instructions"])
        self.assertIn("input control and a submit/enter trigger", f1_guidance)
        self.assertIn("at most 4 interactions", f3_guidance)
        self.assertIn("not a global F3 schema rule", f3_guidance)
        for semantic in ("search/filter", "checkout open", "submit", "validation recovery"):
            self.assertIn(semantic, f3_guidance)
        self.assertEqual(f3_prompt["template_revision"], p4q.P4R2_PROMPT_REVISION)
        runner = Phase4LocalQwenPilotRunner(
            pilot=binding,
            policies=policies,
            profile=LocalQwenProfile.from_dict(manifest.profile),
            manifest=manifest,
            result_root=result_root,
            b_input=b_input,
            backend=create_scripted_fixture_backend(_fixture_bytes(b_input), result_root=result_root),
        )
        self.assertIsNone(runner.run_node_local(node_id="F1").failure_code)
        action = json.loads(next(result_root.rglob("node_d17_action.json")).read_text(encoding="utf-8"))
        self.assertEqual(action["prompt_revision"], p4q.P4R2_PROMPT_REVISION)
        self.assertIn(p4q.P4R2_PROJECTION_REVISION, action["input_view_ref"]["ref_revision"])

    def test_r2_shared_budget_cannot_reset_across_result_roots(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"r2-shared-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)
        model_root, integrity, _, b_input, binding_one, _, profile_one, _ = _prepare_tiny_r2_artifact(parent)
        policy, policy_raw = load_p4r2_policy_revision()
        result_two = parent / "prepared-r2-second"
        binding_two, _, profile_two, _ = prepare_local_qwen_pilot(
            model_root=model_root,
            integrity_evidence=integrity,
            result_root=result_two,
            case_binding=build_synthetic_case_binding(b_input),
            policies=pilot_script._r2_historical_policies(),
            pilot_id=policy.pilot_id,
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
            r2_policy=policy,
        )
        p4q._reserve_p4r2_aggregate_generate_entry(
            model_root=model_root,
            policy_raw=policy_raw,
            pilot=binding_one,
            profile=profile_one,
            node_id="F1",
            call_kind="node_local",
            run_id="run-one",
        )
        p4q._reserve_p4r2_aggregate_generate_entry(
            model_root=model_root,
            policy_raw=policy_raw,
            pilot=binding_two,
            profile=profile_two,
            node_id="F1",
            call_kind="node_local",
            run_id="run-two",
        )
        with self.assertRaisesRegex(
            Phase4LocalQwenContractError,
            "shared node-local budget is exhausted",
        ):
            p4q._reserve_p4r2_aggregate_generate_entry(
                model_root=model_root,
                policy_raw=policy_raw,
                pilot=binding_two,
                profile=profile_two,
                node_id="F1",
                call_kind="node_local",
                run_id="run-three",
            )
        shared = p4q._validate_p4r2_aggregate_budget(
            model_root=model_root,
            policy_raw=policy_raw,
            pilot=binding_two,
            profile=profile_two,
        )
        self.assertEqual(shared.node_local_generate_entry_reservations["F1"], 2)
        self.assertEqual(shared.node_total_generate_entry_reservations["F1"], 2)

    def test_r2_and_r3_aggregate_ledgers_are_independent_and_r3_is_shared_across_roots(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"r3-shared-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)
        model_root, integrity, _, b_input, r2_binding, _, r2_profile, _ = _prepare_tiny_r2_artifact(parent)
        r2_policy, r2_policy_raw = load_p4r2_policy_revision()
        r3_policy, r3_policy_raw = p4q.load_p4r3_policy_revision()
        r3_policies = pilot_script._default_policies()

        def prepare_r3(result_root):
            with patch.object(
                p4q,
                "_load_p4r3_r2_action_time_evidence",
                return_value=p4q._p4r3_expected_action_evidence(),
            ):
                return prepare_local_qwen_pilot(
                    model_root=model_root,
                    integrity_evidence=integrity,
                    result_root=result_root,
                    case_binding=build_synthetic_case_binding(b_input),
                    policies=r3_policies,
                    pilot_id=r3_policy.pilot_id,
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
                    r3_policy=r3_policy,
                )

        r3_binding_one, _, r3_profile_one, _ = prepare_r3(parent / "prepared-r3-one")
        r3_binding_two, _, r3_profile_two, _ = prepare_r3(parent / "prepared-r3-two")
        r2_run_root, r2_filename, _ = p4q._p4r2_aggregate_budget_paths(model_root)
        r3_run_root, r3_filename, _ = p4q._p4r3_aggregate_budget_paths(model_root)
        self.assertEqual(r2_run_root, r3_run_root)
        self.assertNotEqual(r2_filename, r3_filename)
        self.assertTrue((r2_run_root / r2_filename).is_file())
        self.assertTrue((r3_run_root / r3_filename).is_file())
        self.assertIsInstance(
            p4q.P4R2AggregateBudgetLedger.from_bytes((r2_run_root / r2_filename).read_bytes()),
            p4q.P4R2AggregateBudgetLedger,
        )
        self.assertIsInstance(
            p4q.P4R3AggregateBudgetLedger.from_bytes((r3_run_root / r3_filename).read_bytes()),
            p4q.P4R3AggregateBudgetLedger,
        )

        p4q._reserve_p4r2_aggregate_generate_entry(
            model_root=model_root,
            policy_raw=r2_policy_raw,
            pilot=r2_binding,
            profile=r2_profile,
            node_id="F1",
            call_kind="node_local",
            run_id="r2-run-one",
        )
        p4q._reserve_p4r3_aggregate_generate_entry(
            model_root=model_root,
            policy_raw=r3_policy_raw,
            pilot=r3_binding_one,
            profile=r3_profile_one,
            node_id="F1",
            call_kind="node_local",
            run_id="r3-run-one",
        )
        p4q._reserve_p4r3_aggregate_generate_entry(
            model_root=model_root,
            policy_raw=r3_policy_raw,
            pilot=r3_binding_two,
            profile=r3_profile_two,
            node_id="F1",
            call_kind="node_local",
            run_id="r3-run-two",
        )
        with self.assertRaisesRegex(Phase4LocalQwenContractError, "shared node-local budget is exhausted"):
            p4q._reserve_p4r3_aggregate_generate_entry(
                model_root=model_root,
                policy_raw=r3_policy_raw,
                pilot=r3_binding_two,
                profile=r3_profile_two,
                node_id="F1",
                call_kind="node_local",
                run_id="r3-run-three",
            )

        r2_ledger = p4q._validate_p4r2_aggregate_budget(
            model_root=model_root,
            policy_raw=r2_policy_raw,
            pilot=r2_binding,
            profile=r2_profile,
        )
        r3_ledger = p4q._validate_p4r3_aggregate_budget(
            model_root=model_root,
            policy_raw=r3_policy_raw,
            pilot=r3_binding_two,
            profile=r3_profile_two,
        )
        self.assertEqual(r2_ledger.node_local_generate_entry_reservations["F1"], 1)
        self.assertEqual(r3_ledger.node_local_generate_entry_reservations["F1"], 2)
        self.assertEqual(r2_ledger.status, "r2_aggregate_budget_live")
        self.assertEqual(r3_ledger.status, "r3_aggregate_budget_live")

    def test_r2_aggregate_rejects_resigned_invalid_integrated_decomposition(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"r2-ledger-invariant-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)
        model_root, _, _, _, binding, _, profile, _ = _prepare_tiny_r2_artifact(parent)
        _, policy_raw = load_p4r2_policy_revision()
        ledger = p4q._validate_p4r2_aggregate_budget(
            model_root=model_root,
            policy_raw=policy_raw,
            pilot=binding,
            profile=profile,
        )

        forged_payloads = []
        no_integrated_run = ledger.to_dict()
        no_integrated_run["node_total_generate_entry_reservations"]["F1"] = 1
        forged_payloads.append(no_integrated_run)

        non_prefix = ledger.to_dict()
        non_prefix["integrated_run_reservations"] = 1
        non_prefix["integrated_run_owner"] = {
            "run_id": "resigned-integrated-run",
            "result_root_marker": binding.result_root_marker,
        }
        non_prefix["node_total_generate_entry_reservations"]["F1"] = 1
        non_prefix["node_total_generate_entry_reservations"]["F3"] = 1
        forged_payloads.append(non_prefix)

        delta_two = ledger.to_dict()
        delta_two["integrated_run_reservations"] = 1
        delta_two["integrated_run_owner"] = {
            "run_id": "resigned-integrated-run",
            "result_root_marker": binding.result_root_marker,
        }
        delta_two["node_total_generate_entry_reservations"]["F1"] = 2
        forged_payloads.append(delta_two)

        for payload in forged_payloads:
            with self.subTest(total=payload["node_total_generate_entry_reservations"]):
                payload["generate_entry_reservation_total"] = sum(
                    payload["node_total_generate_entry_reservations"].values()
                )
                payload["ledger_id"] = p4q._sha256(
                    p4q._canonical_bytes(
                        {key: value for key, value in payload.items() if key != "ledger_id"}
                    )
                )
                with self.assertRaises(Phase4LocalQwenContractError):
                    p4q.P4R2AggregateBudgetLedger.from_dict(payload)

    def test_r2_fake_real_runner_reserves_after_pre_call_and_shares_one_integrated_slot(self):
        base = Path.cwd() / ".p4-03-test-results" / f"r2-real-order-{len(self._test_roots)}"
        base.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(base)

        class Sink:
            def write(self, value):
                return len(value)

            def flush(self):
                return None

        class ControlledProcess:
            stdin = Sink()

            def __init__(self, pid):
                self.pid = pid

            @staticmethod
            def poll():
                return None

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

        def make_controlled_runner(parent, worker_id, pid):
            parent.mkdir()
            model_root, _, result_root, b_input, binding, policies, profile, manifest = _prepare_tiny_r2_artifact(parent)
            lease = acquire_pilot_execution_lease(
                result_root=result_root, pilot=binding, manifest=manifest
            )
            claim = p4q._acquire_runtime_start_claim(result_root=result_root, lease=lease)
            receipt = LocalQwenLoadReceipt.create(
                manifest=manifest,
                pilot=binding,
                profile=profile,
                loaded_facts=loaded_facts,
            )
            object.__setattr__(receipt, "_real_runtime_capability", p4q._REAL_RUNTIME_CAPABILITY)
            backend = SupervisedLocalQwenBackend(
                process=ControlledProcess(pid),
                messages=queue.Queue(),
                stderr_capture=p4q._WorkerStderrCapture(),
                profile=profile,
                worker_id=worker_id,
                loaded_facts=loaded_facts,
                capability=p4q._REAL_RUNTIME_CAPABILITY,
            )
            runner = Phase4LocalQwenPilotRunner(
                pilot=binding,
                policies=policies,
                profile=profile,
                manifest=manifest,
                result_root=result_root,
                b_input=b_input,
                model_root=model_root,
                backend=backend,
                source_kind="real_local_qwen",
                load_receipt=receipt,
                execution_lease=lease,
                runtime_start_claim=claim,
            )
            return model_root, result_root, b_input, binding, profile, backend, runner

        _, rejected_root, _, _, _, rejected_backend, rejected_runner = make_controlled_runner(
            base / "rejected", "worker-r2-rejected", 6101
        )
        reserve_observed_pre_call = []

        def reject_reserve(**_):
            reserve_observed_pre_call.append(bool(list(rejected_root.rglob("pre_call.json"))))
            raise Phase4LocalQwenContractError("synthetic shared reserve rejection")

        with patch.object(rejected_backend, "generate", return_value=b"") as generate, patch.object(
            p4q, "_reserve_p4r2_aggregate_generate_entry", side_effect=reject_reserve
        ):
            with self.assertRaisesRegex(Phase4LocalQwenContractError, "synthetic shared reserve rejection"):
                rejected_runner.run_node_local(node_id="F1")
        self.assertEqual(reserve_observed_pre_call, [True])
        generate.assert_not_called()

        model_root, result_root, b_input, binding, profile, backend, runner = make_controlled_runner(
            base / "integrated", "worker-r2-integrated", 6102
        )
        fixture = _fixture_bytes(b_input)
        original_reserve = p4q._reserve_p4r2_aggregate_generate_entry
        pre_call_counts = []

        def observe_reserve(**kwargs):
            current_count = len(list(result_root.rglob("pre_call.json")))
            self.assertGreaterEqual(current_count, len(pre_call_counts) + 1)
            pre_call_counts.append(current_count)
            return original_reserve(**kwargs)

        with patch.object(backend, "generate", side_effect=fixture + fixture) as generate, patch.object(
            p4q, "_reserve_p4r2_aggregate_generate_entry", side_effect=observe_reserve
        ):
            for node_id in ("F1", "F2", "F3", "F4"):
                self.assertIsNone(runner.run_node_local(node_id=node_id).failure_code)
            runner.run_integrated()

        _, policy_raw = load_p4r2_policy_revision()
        shared = p4q._validate_p4r2_aggregate_budget(
            model_root=model_root,
            policy_raw=policy_raw,
            pilot=binding,
            profile=profile,
        )
        self.assertEqual(generate.call_count, 8)
        self.assertEqual(len(pre_call_counts), 8)
        self.assertEqual(shared.integrated_run_reservations, 1)
        self.assertEqual(
            shared.node_local_generate_entry_reservations,
            {node_id: 1 for node_id in ("F1", "F2", "F3", "F4")},
        )
        self.assertEqual(
            shared.node_total_generate_entry_reservations,
            {node_id: 2 for node_id in ("F1", "F2", "F3", "F4")},
        )

    def test_r3_fake_real_runner_must_reserve_r3_after_pre_call_before_generate(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"r3-real-order-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)

        class Sink:
            def write(self, value):
                return len(value)

            def flush(self):
                return None

        class ControlledProcess:
            stdin = Sink()
            pid = 6201

            @staticmethod
            def poll():
                return None

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
        model_root, _, result_root, b_input, binding, policies, profile, manifest = _prepare_tiny_r3_artifact(parent)
        lease = acquire_pilot_execution_lease(
            result_root=result_root,
            pilot=binding,
            manifest=manifest,
        )
        claim = p4q._acquire_runtime_start_claim(result_root=result_root, lease=lease)
        receipt = LocalQwenLoadReceipt.create(
            manifest=manifest,
            pilot=binding,
            profile=profile,
            loaded_facts=loaded_facts,
        )
        object.__setattr__(receipt, "_real_runtime_capability", p4q._REAL_RUNTIME_CAPABILITY)
        backend = SupervisedLocalQwenBackend(
            process=ControlledProcess(),
            messages=queue.Queue(),
            stderr_capture=p4q._WorkerStderrCapture(),
            profile=profile,
            worker_id="worker-r3-controlled",
            loaded_facts=loaded_facts,
            capability=p4q._REAL_RUNTIME_CAPABILITY,
        )
        observed_pre_call = []

        def reject_r3_reserve(**_):
            observed_pre_call.append(bool(list(result_root.rglob("pre_call.json"))))
            raise Phase4LocalQwenContractError("synthetic R3 shared reserve rejection")

        with patch.object(
            p4q,
            "_load_p4r3_r2_action_time_evidence",
            return_value=p4q._p4r3_expected_action_evidence(),
        ):
            runner = Phase4LocalQwenPilotRunner(
                pilot=binding,
                policies=policies,
                profile=profile,
                manifest=manifest,
                result_root=result_root,
                b_input=b_input,
                model_root=model_root,
                backend=backend,
                source_kind="real_local_qwen",
                load_receipt=receipt,
                execution_lease=lease,
                runtime_start_claim=claim,
            )
            with patch.object(backend, "generate", return_value=b"") as generate, patch.object(
                p4q,
                "_reserve_p4r3_aggregate_generate_entry",
                side_effect=reject_r3_reserve,
            ) as r3_reserve, patch.object(
                p4q,
                "_reserve_p4r2_aggregate_generate_entry",
                side_effect=AssertionError("R3 runner attempted to reserve the R2 ledger"),
            ) as r2_reserve:
                with self.assertRaisesRegex(
                    Phase4LocalQwenContractError,
                    "synthetic R3 shared reserve rejection",
                ):
                    runner.run_node_local(node_id="F1")
        self.assertEqual(observed_pre_call, [True])
        r3_reserve.assert_called_once()
        r2_reserve.assert_not_called()
        generate.assert_not_called()

    def test_r2_f3_limit_is_pilot_local_and_fail_closed(self):
        b_input = synthetic_commerce_b_input()
        state = phase4_create_authority_state(b_input)
        for node_id in ("F1", "F2"):
            output = phase4_synthetic_fixture_output(node_id, state)
            state = phase4_register_node_output(state, node_id, output)
        f3 = phase4_synthetic_fixture_output("F3", state)
        for suffix in ("extra-one", "extra-two"):
            extra = copy.deepcopy(f3["interactions"][0])
            extra["local_id"] = f"interaction-{suffix}"
            f3["interactions"].append(extra)
        raw = json.dumps(f3, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertEqual(
            len(p4q._validate_node_output_json(
                raw,
                node_id="F3",
                authority_state=state,
                policy=_policies()[2],
            )["interactions"]),
            5,
        )
        with self.assertRaisesRegex(
            Phase4LocalQwenContractError,
            "R2 pilot-local F3 interaction limit exceeded",
        ):
            p4q._validate_node_output_json(
                raw,
                node_id="F3",
                authority_state=state,
                policy=pilot_script._r2_historical_policies()[2],
            )

    def test_r2_live_binding_rejects_cross_pilot_and_old_ledger_drift(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"r2-drift-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)
        _, _, result_root, _, binding, policies, _, _ = _prepare_tiny_r2_artifact(parent)
        binding_payload = json.loads((result_root / "p4_03r2_result_root_binding.json").read_text(encoding="utf-8"))
        binding_payload["pilot_id"] = "p4-03r2-other-pilot"
        binding_payload["binding_id"] = p4q._sha256(p4q._canonical_bytes({key: value for key, value in binding_payload.items() if key != "binding_id"}))
        (result_root / "p4_03r2_result_root_binding.json").write_bytes(p4q._canonical_bytes(binding_payload))
        with self.assertRaises(Phase4LocalQwenContractError):
            validate_p4r2_live_binding(result_root=result_root, pilot=binding, policies=policies)
        original_binding = P4R2ResultRootBinding.create(
            policy_raw=(result_root / "p4_03r2_policy.json").read_bytes(),
            ledger_raw=(result_root / "p4_03r2_predecessor_aggregate_ledger.json").read_bytes(),
            pilot=binding,
            result_root_marker=binding.result_root_marker,
        )
        (result_root / "p4_03r2_result_root_binding.json").write_bytes(original_binding.canonical_bytes())
        drift = parent / "drifted-ledger.json"
        drift.write_bytes((result_root / "p4_03r2_predecessor_aggregate_ledger.json").read_bytes() + b" ")
        with patch.object(p4q, "P4R2_LEDGER_PATH", drift):
            with self.assertRaises(Phase4LocalQwenContractError):
                validate_p4r2_live_binding(result_root=result_root, pilot=binding, policies=policies)

    def test_real_start_requires_r2_binding_before_popen(self):
        parent = Path.cwd() / ".p4-03-test-results" / f"r2-start-{len(self._test_roots)}"
        parent.mkdir(parents=True, exist_ok=False)
        self._test_roots.append(parent)
        model_root, integrity, result_root, _, _, _, _, _ = _prepare_tiny_artifact(parent)
        with patch("req2web_runtime.phase4_local_qwen.subprocess.Popen") as popen:
            with self.assertRaisesRegex(
                Phase4LocalQwenContractError,
                "real runtime prepared pilot revision is unknown",
            ):
                p4q.start_supervised_local_qwen_runtime(
                    model_root=model_root,
                    integrity_evidence=integrity,
                    result_root=result_root,
                    load_timeout_seconds=1,
                )
            popen.assert_not_called()

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
        ) = _prepare_tiny_r2_artifact(root)
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
