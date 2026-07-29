"""Focused fake-backend tests for the Qwen3.5-27B recovery runner."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import unittest
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_runtime import qwen27b_predeploy as predeploy  # noqa: E402
from req2web_runtime.qwen27b_case_bundle import (  # noqa: E402
    CASE_IDS,
    build_qwen27b_case_bundle,
)
from req2web_runtime.qwen27b_recovery_runner import (  # noqa: E402
    BackendGeneration,
    EXPECTED_DEVICE,
    EXPECTED_GPU_NAMES,
    MIN_TOTAL_VRAM_BYTES,
    Qwen27BRecoveryRunnerError,
    TransformersRecoveryBackend,
    execute_qwen27b_recovery,
    validate_qwen27b_recovery_result,
    validate_qwen27b_recovery_result_against,
    validate_qwen27b_recovery_return_against,
    terminate_process_group,
    wait_for_worker,
)
from test_m3_qwen27b_case_bundle import Qwen27BCaseBundleTests  # noqa: E402


class FakeBackend:
    def __init__(self, *, fail_case: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_case = fail_case
        self.loaded = False

    def runtime_facts(self, expected_versions):
        return {
            **dict(expected_versions),
            "gpu_index": 0,
            "gpu_uuid": "GPU-fixture",
            "gpu_name": EXPECTED_GPU_NAMES[0],
            "total_vram_bytes": MIN_TOTAL_VRAM_BYTES,
            "free_vram_bytes_before_load": MIN_TOTAL_VRAM_BYTES,
            "torch_gpu_name": EXPECTED_GPU_NAMES[0],
            "torch_gpu_uuid": "GPU-fixture",
            "torch_total_vram_bytes": MIN_TOTAL_VRAM_BYTES,
            "device": EXPECTED_DEVICE,
            "dtype": "bf16",
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
        }

    def load(self, model_root: Path) -> None:
        self.loaded = True

    def generate(self, model_text, case_id, *, stream_output, stream):
        self.calls.append(case_id)
        if case_id == self.fail_case:
            raise RuntimeError("fixture generation failure")
        text = f'{{"case_id":"{case_id}","fixture":true}}'
        if stream_output == "console":
            stream.write(text)
            stream.flush()
        return BackendGeneration(
            text=text,
            input_tokens=100 + len(self.calls),
            generated_tokens=20 + len(self.calls),
            elapsed_ms=1000,
            stream_event_count=3 if stream_output != "off" else 0,
            stream_text=text if stream_output != "off" else "",
        )

    def loaded_facts(self):
        if not self.loaded:
            raise AssertionError("load was not called")
        return {
            "device": EXPECTED_DEVICE,
            "dtype": "bf16",
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
            "allocated_vram_bytes": 55 * 1024**3,
            "reserved_vram_bytes": 56 * 1024**3,
        }


class Qwen27BRecoveryRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Qwen27BCaseBundleTests.setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        Qwen27BCaseBundleTests.tearDownClass()

    def setUp(self) -> None:
        self.work = ROOT / ("tmp-qwen27b-recovery-" + uuid4().hex)
        self.work.mkdir()
        self.model = self.work / "model"
        self.model.mkdir()
        for name in predeploy._MODEL_FILES:
            (self.model / name).write_bytes(("fixture:" + name).encode("utf-8"))
        self.archive = self.work / "req2web.zip"
        shutil.copyfile(
            Qwen27BCaseBundleTests.first_archive_path,
            self.archive,
        )
        self.archive_manifest_path = self.work / "req2web.manifest.json"
        shutil.copyfile(
            Qwen27BCaseBundleTests.first_manifest_path,
            self.archive_manifest_path,
        )
        self.bundle = build_qwen27b_case_bundle(
            self.archive,
            self.archive_manifest_path,
            self.work / "case-bundle",
        )
        self.runtime = self.work / "runtime"
        for relative in ("bin/python", *predeploy._RUNTIME_STATIC_FILES):
            path = self.runtime / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("runtime:" + relative).encode("utf-8"))
        runtime = predeploy.create_runtime_inventory(self.runtime)
        runtime_bytes = predeploy._dump(runtime)
        probe_bytes = predeploy._dump(
            predeploy._create_no_gpu_runtime_probe(runtime_bytes)
        )
        self.model_inventory = predeploy.Qwen27BPredeployInventory.from_dict(
            predeploy._official_model_inventory_authority()["inventory"]
        )
        archive_authority = self.bundle.manifest["source"][
            "repository_archive_authority"
        ]
        self.manifest = predeploy.create_predeploy_manifest(
            self.model_inventory,
            runtime_bytes,
            probe_bytes,
            predeploy.collect_file_inventory(self.archive, "repository_archive"),
            predeploy.collect_directory_inventory(self.bundle.root, "case_bundle"),
            archive_authority,
            archive_authority,
            source_commit=archive_authority["commit_sha"],
            source_tree=archive_authority["tree_sha"],
        )
        self.model_fixture_original = (
            self.model / predeploy._MODEL_FILES[0]
        ).read_bytes()
        with mock.patch.object(
            predeploy,
            "collect_model_root_inventory",
            side_effect=self._collect_fixture_model,
        ):
            self.ready = predeploy.create_predeploy_ready_no_gpu(
                self.manifest,
                self.model,
                self.archive,
                self.bundle.root,
                "none",
            )
        self.manifest_path = self.work / "predeploy-manifest.json"
        self.manifest_path.write_bytes(self.manifest.canonical_bytes())
        self.ready_path = self.work / "predeploy-ready.json"
        self.ready_path.write_bytes(self.ready.canonical_bytes())

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def _collect_fixture_model(self, _root):
        if (
            self.model / predeploy._MODEL_FILES[0]
        ).read_bytes() != self.model_fixture_original:
            raise predeploy.Qwen27BPredeployError(
                "model_official_inventory_mismatch"
            )
        return self.model_inventory

    def execute(self, backend, result_name="result", stream_output="off"):
        with (
            mock.patch(
                "req2web_runtime.qwen27b_recovery_runner.sys.platform", "linux"
            ),
            mock.patch("req2web_runtime.qwen27b_recovery_runner._progress"),
            mock.patch(
                "req2web_runtime.qwen27b_recovery_runner.sys.executable",
                str(self.runtime / "bin" / "python"),
            ),
            mock.patch(
                "req2web_runtime.qwen27b_predeploy.collect_model_root_inventory",
                side_effect=self._collect_fixture_model,
            ),
        ):
            return execute_qwen27b_recovery(
                self.manifest_path,
                self.ready_path,
                self.model,
                self.runtime,
                self.archive,
                self.archive_manifest_path,
                self.bundle.root,
                self.work / result_name,
                selected_copy="none",
                expected_gpu_name=EXPECTED_GPU_NAMES[0],
                expected_gpu_uuid="GPU-fixture",
                stream_output=stream_output,
                backend=backend,
                stream=io.StringIO(),
            )

    def test_two_case_success_is_raw_first_bounded_and_replayable(self) -> None:
        backend = FakeBackend()
        result = self.execute(backend, stream_output="console")
        self.assertEqual(backend.calls, list(CASE_IDS))
        self.assertEqual(result["execution"]["provider_call_count"], 2)
        self.assertEqual(result["execution"]["retry_count"], 0)
        self.assertEqual(result["model"]["local_files_only"], True)
        self.assertEqual(result["profile"]["quantization"], "none")
        self.assertEqual(result["claims"]["semantic_success"], False)
        for row in result["cases"]:
            self.assertEqual(row["provider_call_count"], 1)
            self.assertEqual(row["retry_count"], 0)
            self.assertTrue(row["stream_matches_final_raw"])
            self.assertGreater(row["tokens_per_second"], 0)
        replay = validate_qwen27b_recovery_result(self.work / "result")
        self.assertEqual(replay, result)

    def test_predeploy_or_case_bundle_tamper_fails_before_backend(self) -> None:
        prompt = (
            self.bundle.root
            / "cases"
            / CASE_IDS[0]
            / "prompt.json"
        )
        prompt.write_bytes(prompt.read_bytes() + b" ")
        backend = FakeBackend()
        with self.assertRaises(Qwen27BRecoveryRunnerError):
            self.execute(backend)
        self.assertEqual(backend.calls, [])
        self.assertFalse(backend.loaded)

    def test_failure_is_fail_closed_without_retry_or_success_manifest(self) -> None:
        backend = FakeBackend(fail_case=CASE_IDS[1])
        with self.assertRaisesRegex(
            Qwen27BRecoveryRunnerError, "recovery_worker_failed"
        ):
            self.execute(backend)
        self.assertEqual(backend.calls, list(CASE_IDS))
        result = self.work / "result"
        self.assertFalse((result / "recovery_run_manifest.json").exists())
        self.assertTrue((result / "recovery_run_failure.json").is_file())
        self.assertTrue(
            (result / "cases" / CASE_IDS[0] / "raw_response.bin").is_file()
        )

    def test_linux_and_stream_mode_are_hard_gates(self) -> None:
        with self.assertRaisesRegex(
            Qwen27BRecoveryRunnerError, "linux_worker_required"
        ):
            execute_qwen27b_recovery(
                self.manifest_path,
                self.ready_path,
                self.model,
                self.runtime,
                self.archive,
                self.archive_manifest_path,
                self.bundle.root,
                self.work / "result",
                selected_copy="none",
                expected_gpu_name=EXPECTED_GPU_NAMES[0],
                expected_gpu_uuid="GPU-fixture",
                backend=FakeBackend(),
            )
        with mock.patch(
            "req2web_runtime.qwen27b_recovery_runner.sys.platform", "linux"
        ):
            with self.assertRaisesRegex(
                Qwen27BRecoveryRunnerError, "stream_output_mode_invalid"
            ):
                execute_qwen27b_recovery(
                    self.manifest_path,
                    self.ready_path,
                    self.model,
                    self.runtime,
                    self.archive,
                    self.archive_manifest_path,
                    self.bundle.root,
                    self.work / "result",
                    selected_copy="none",
                    expected_gpu_name=EXPECTED_GPU_NAMES[0],
                    expected_gpu_uuid="GPU-fixture",
                    stream_output="invalid",
                    backend=FakeBackend(),
                )

    def test_backend_cannot_promote_a_wrong_gpu_profile(self) -> None:
        class WrongGpuBackend(FakeBackend):
            def runtime_facts(self, expected_versions):
                facts = super().runtime_facts(expected_versions)
                facts["gpu_name"] = "NVIDIA RTX 5090"
                return facts

        backend = WrongGpuBackend()
        with self.assertRaisesRegex(
            Qwen27BRecoveryRunnerError, "gpu_profile_mismatch"
        ):
            self.execute(backend)
        self.assertFalse(backend.loaded)
        self.assertEqual(backend.calls, [])

    def test_result_validator_rejects_raw_tamper_and_extra_file(self) -> None:
        self.execute(FakeBackend())
        raw = self.work / "result" / "cases" / CASE_IDS[0] / "raw_response.bin"
        raw.write_bytes(b"tampered")
        with self.assertRaisesRegex(
            Qwen27BRecoveryRunnerError, "raw_binding"
        ):
            validate_qwen27b_recovery_result(self.work / "result")
        shutil.rmtree(self.work / "result")
        self.execute(FakeBackend())
        (self.work / "result" / "extra.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(
            Qwen27BRecoveryRunnerError, "inventory_paths"
        ):
            validate_qwen27b_recovery_result(self.work / "result")

    def test_against_validator_rejects_rehashed_binding_and_live_artifact_drift(
        self,
    ) -> None:
        self.execute(FakeBackend())
        result_root = self.work / "result"
        run_path = result_root / "recovery_run_manifest.json"
        original_run = run_path.read_bytes()
        mutators = {
            "predeploy": lambda data: data["predeploy"].__setitem__(
                "manifest_sha256", "f" * 64
            ),
            "case_bundle": lambda data: data["predeploy"].__setitem__(
                "case_bundle_sha256", "f" * 64
            ),
            "archive": lambda data: data["predeploy"][
                "repository_archive_authority"
            ].__setitem__("archive_sha256", "f" * 64),
            "model": lambda data: data["predeploy"][
                "model_inventory"
            ].__setitem__("tree_sha256", "f" * 64),
            "runtime": lambda data: (
                data["predeploy"]["runtime_inventory"].__setitem__(
                    "tree_sha256", "f" * 64
                ),
                data["worker_runtime"].__setitem__(
                    "runtime_tree_sha256", "f" * 64
                ),
            ),
        }
        for label, mutate in mutators.items():
            with self.subTest(rehashed_binding=label):
                data = __import__("json").loads(
                    original_run.decode("utf-8")
                )
                mutate(data)
                data["run_id"] = "0" * 64
                data["run_id"] = hashlib.sha256(
                    predeploy._dump(data)
                ).hexdigest()
                run_path.write_bytes(predeploy._dump(data))
                validate_qwen27b_recovery_result(result_root)
                with (
                    mock.patch(
                        "req2web_runtime.qwen27b_recovery_runner.validate_repository_archive_authority",
                        return_value=self.manifest.to_dict()[
                            "repository_archive_authority"
                        ],
                    ),
                    mock.patch.object(
                        predeploy,
                        "collect_model_root_inventory",
                        side_effect=self._collect_fixture_model,
                    ),
                    self.assertRaisesRegex(
                        Qwen27BRecoveryRunnerError,
                        "against_predeploy_binding_mismatch",
                    ),
                ):
                    validate_qwen27b_recovery_result_against(
                        result_root,
                        self.manifest_path,
                        self.ready_path,
                        self.model,
                        self.runtime,
                        self.archive,
                        self.archive_manifest_path,
                        self.bundle.root,
                        "none",
                    )
        run_path.write_bytes(original_run)

        for label, path in (
            ("model", self.model / predeploy._MODEL_FILES[0]),
            ("runtime", self.runtime / "bin" / "python"),
            ("archive", self.archive),
            (
                "case",
                self.bundle.root
                / "cases"
                / CASE_IDS[0]
                / "prompt.json",
            ),
        ):
            with self.subTest(label=label):
                original = path.read_bytes()
                path.write_bytes(original + b"x")
                try:
                    with (
                        mock.patch(
                            "req2web_runtime.qwen27b_recovery_runner.validate_repository_archive_authority",
                            return_value=self.manifest.to_dict()[
                                "repository_archive_authority"
                            ],
                        ),
                        mock.patch.object(
                            predeploy,
                            "collect_model_root_inventory",
                            side_effect=self._collect_fixture_model,
                        ),
                        self.assertRaises(Qwen27BRecoveryRunnerError),
                    ):
                        validate_qwen27b_recovery_result_against(
                            result_root,
                            self.manifest_path,
                            self.ready_path,
                            self.model,
                            self.runtime,
                            self.archive,
                            self.archive_manifest_path,
                            self.bundle.root,
                            "none",
                        )
                finally:
                    path.write_bytes(original)

    def test_local_return_validator_needs_no_local_model_or_runtime(self) -> None:
        expected = self.execute(FakeBackend())
        shutil.rmtree(self.model)
        shutil.rmtree(self.runtime)

        actual = validate_qwen27b_recovery_return_against(
            self.work / "result",
            self.manifest_path,
            self.ready_path,
            self.archive,
            self.archive_manifest_path,
            self.bundle.root,
            "none",
        )
        self.assertEqual(actual, expected)

    def test_local_return_rejects_manifest_model_and_ready_runtime_drift(
        self,
    ) -> None:
        self.execute(FakeBackend())
        original_manifest = self.manifest_path.read_bytes()
        original_ready = self.ready_path.read_bytes()
        try:
            manifest = json.loads(original_manifest)
            rows = manifest["expected_model_inventory"]["files"]
            rows[0]["sha256"] = "f" * 64
            manifest["expected_model_inventory"] = predeploy._inventory_data(
                "model_root", rows
            )
            manifest = predeploy._identified(manifest, "manifest_id")
            self.manifest_path.write_bytes(predeploy._dump(manifest))
            with self.assertRaises(Qwen27BRecoveryRunnerError):
                validate_qwen27b_recovery_return_against(
                    self.work / "result",
                    self.manifest_path,
                    self.ready_path,
                    self.archive,
                    self.archive_manifest_path,
                    self.bundle.root,
                    "none",
                )

            self.manifest_path.write_bytes(original_manifest)
            ready = json.loads(original_ready)
            runtime = ready["actual_runtime_inventory"]
            runtime["runtime_root"] = "E:/detached-drift-runtime"
            runtime["runtime_root_sha256"] = hashlib.sha256(
                runtime["runtime_root"].encode("utf-8")
            ).hexdigest()
            runtime = predeploy._identified(runtime, "inventory_id")
            ready["actual_runtime_inventory"] = runtime
            ready = predeploy._identified(ready, "ready_id")
            self.ready_path.write_bytes(predeploy._dump(ready))
            with self.assertRaisesRegex(
                Qwen27BRecoveryRunnerError,
                "return_ready_inventory_mismatch",
            ):
                validate_qwen27b_recovery_return_against(
                    self.work / "result",
                    self.manifest_path,
                    self.ready_path,
                    self.archive,
                    self.archive_manifest_path,
                    self.bundle.root,
                    "none",
                )
        finally:
            self.manifest_path.write_bytes(original_manifest)
            self.ready_path.write_bytes(original_ready)

    def test_wait_timeout_terminates_process_group(self) -> None:
        process = mock.Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired(
            cmd=["fixture"], timeout=0.01
        )
        process.poll.return_value = None
        with (
            mock.patch(
                "req2web_runtime.qwen27b_recovery_runner.time.monotonic",
                side_effect=[0.0, 2.0],
            ),
            mock.patch(
                "req2web_runtime.qwen27b_recovery_runner.terminate_process_group"
            ) as terminate,
        ):
            with self.assertRaisesRegex(
                Qwen27BRecoveryRunnerError, "recovery_timeout"
            ):
                wait_for_worker(process, 1, process_group_id=7123)
        terminate.assert_called_once_with(process, process_group_id=7123)

    def test_terminate_group_after_leader_exit_still_sends_term_and_kill(
        self,
    ) -> None:
        process = mock.Mock(pid=7123)
        process.poll.return_value = 1
        with (
            mock.patch(
                "req2web_runtime.qwen27b_recovery_runner.sys.platform",
                "linux",
            ),
            mock.patch(
                "req2web_runtime.qwen27b_recovery_runner.os.killpg",
                create=True,
            ) as killpg,
            mock.patch(
                "req2web_runtime.qwen27b_recovery_runner.signal.SIGKILL",
                9,
                create=True,
            ),
        ):
            terminate_process_group(
                process, grace_seconds=0.0, process_group_id=7123
            )
        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(7123, signal.SIGTERM),
                mock.call(7123, 9),
            ],
        )
        process.wait.assert_not_called()

    def test_real_backend_contract_uses_offline_bf16_greedy_flags(self) -> None:
        calls = {}

        class Tensor:
            def __init__(self, shape):
                self.shape = shape

            def to(self, device):
                calls.setdefault("tensor_devices", []).append(device)
                return self

        class Generated:
            shape = (1, 2)

        class Output:
            def __getitem__(self, key):
                calls["slice"] = key
                return Generated()

        class Parameter:
            device = EXPECTED_DEVICE
            dtype = "torch.bfloat16"

            @staticmethod
            def is_floating_point():
                return True

        class Model:
            def to(self, device):
                calls["model_device"] = device
                return self

            def eval(self):
                calls["eval"] = True

            @staticmethod
            def parameters():
                return iter((Parameter(),))

            @staticmethod
            def generate(**kwargs):
                calls["generate"] = kwargs
                return Output()

        class ModelType:
            @staticmethod
            def from_pretrained(root, **kwargs):
                calls["model_load"] = (root, kwargs)
                return Model()

        class Processor:
            @staticmethod
            def apply_chat_template(messages, **kwargs):
                calls["template"] = kwargs
                return "rendered"

            @staticmethod
            def __call__(**kwargs):
                calls["processor_call"] = kwargs
                return {
                    "input_ids": Tensor((1, 100)),
                    "attention_mask": Tensor((1, 100)),
                }

            @staticmethod
            def batch_decode(generated, **kwargs):
                calls["decode"] = kwargs
                return ['{"fixture":true}']

        class ProcessorType:
            @staticmethod
            def from_pretrained(root, **kwargs):
                calls["processor_load"] = (root, kwargs)
                return Processor()

        class InferenceMode:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, traceback):
                return False

        class Cuda:
            @staticmethod
            def memory_allocated(index):
                return 10

            @staticmethod
            def memory_reserved(index):
                return 20

        class Torch:
            bfloat16 = "torch.bfloat16"
            cuda = Cuda()

            @staticmethod
            def inference_mode():
                return InferenceMode()

        backend = TransformersRecoveryBackend()
        backend._runtime = {"validated": True}
        backend._torch = Torch()
        backend._processor_type = ProcessorType
        backend._model_type = ModelType
        backend.load(self.model)
        generated = backend.generate(
            "model text",
            CASE_IDS[0],
            stream_output="off",
            stream=io.StringIO(),
        )
        self.assertEqual(
            calls["processor_load"][1],
            {"local_files_only": True, "trust_remote_code": False},
        )
        self.assertEqual(
            calls["model_load"][1],
            {
                "local_files_only": True,
                "trust_remote_code": False,
                "dtype": "torch.bfloat16",
                "device_map": None,
                "low_cpu_mem_usage": True,
            },
        )
        self.assertEqual(calls["model_device"], EXPECTED_DEVICE)
        self.assertEqual(calls["template"]["enable_thinking"], False)
        self.assertEqual(calls["generate"]["do_sample"], False)
        self.assertEqual(calls["generate"]["max_new_tokens"], 4096)
        self.assertNotIn("streamer", calls["generate"])
        self.assertEqual(generated.input_tokens, 100)
        self.assertEqual(generated.generated_tokens, 2)
        self.assertEqual(generated.stream_event_count, 0)


if __name__ == "__main__":
    unittest.main()
