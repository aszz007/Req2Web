from __future__ import annotations

import copy
import hashlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_runtime import autodl_repository_archive as archive  # noqa: E402
from req2web_runtime import autodl_trusted_remote_executor as executor  # noqa: E402
from req2web_runtime import autodl_trusted_remote_records as records  # noqa: E402
from test_m3_model_route import build_chain, build_g0  # noqa: E402
from test_m3_semantic_candidate_assembly import build_context  # noqa: E402
from test_m3_trusted_remote_model_route import _plan_kwargs, _reference_sha  # noqa: E402

TEST_ROOT = ROOT / "outputs" / "_m3_trusted_remote_executor_tests"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class _TimeoutProcess:
    pid = 424242
    returncode = None

    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.wait_calls = 0

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired(self.command, timeout)

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired(self.command, timeout)
        self.returncode = -9
        return self.returncode

    def kill(self):
        self.returncode = -9


class TrustedRemoteExecutorV2Tests(unittest.TestCase):
    def setUp(self):
        self.work = TEST_ROOT / uuid4().hex
        self.work.mkdir(parents=True)
        self.private_key = self.work / "owner_key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.private_key)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
        if not self.private_key.is_file():
            self.skipTest("ssh-keygen_ed25519_unavailable")
        public = subprocess.run(["ssh-keygen", "-y", "-f", str(self.private_key)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
        if public.returncode != 0:
            self.skipTest("ssh_key_public_identity_unavailable")
        self.public_key = b" ".join(public.stdout.split()[:2]).decode("ascii")
        self.signer = executor.ExpectedPreRunSigner(self.public_key)
        self.provider_inputs = {
            "path3-commerce-checkout": canonical({"case_id": "path3-commerce-checkout", "requirement": "Create a checkout page.", "use_cases": ["submit order"]}),
            "path3-media-analysis": canonical({"case_id": "path3-media-analysis", "requirement": "Create a media analysis page.", "use_cases": ["inspect media"]}),
        }
        self.prompt_artifacts = {
            case_id: canonical({"schema_version": executor.PROMPT_ARTIFACT_SCHEMA, "prompt_text": "Return only a PageSpec JSON object.", "provider_input_mode": "append_exact_provider_visible_input_utf8"})
            for case_id in records.REQUIRED_CASE_IDS
        }
        self.model_files = {"config.json": b'{"model_type":"qwen3_5"}', "model.safetensors": b"model-bytes"}
        self.runtime_files = {
            "bin/python": b"pinned-python",
            "lib/python3.11/site-packages/example/py.typed": b"",
            "lib/python3.11/site-packages/torch/__init__.py": b"torch-module",
            "lib/python3.11/site-packages/transformers/__init__.py": b"transformers-module",
        }
        self.runtime_execution = {
            "python_relative_path": "bin/python",
            "isolated_flags": ["-I", "-s", "-E"],
            "torch_module_relative_path": "lib/python3.11/site-packages/torch/__init__.py",
            "transformers_module_relative_path": "lib/python3.11/site-packages/transformers/__init__.py",
            "expected_versions": {"python": "3.11.9", "torch": "2.7.1+cu128", "transformers": "5.14.1", "cuda": "12.8"},
        }
        self.context = build_context()
        self.g0_package, self.context, self.guidance, self.reference = build_g0(self.work / "g0", context=self.context)
        self.manifest, self.selected, self.local_request, self.pre_invocation_audit, self.local_qwen_preparation = build_chain(self.context)
        selected_provider_raw = self.selected.provider_visible_input.canonical_bytes()
        self.provider_inputs = {case_id: selected_provider_raw for case_id in records.REQUIRED_CASE_IDS}
        self.prompt_artifacts = {case_id: canonical({"schema_version": executor.PROMPT_ARTIFACT_SCHEMA, "prompt_text": self.local_request.prompt_artifact.prompt_text, "provider_input_mode": "append_exact_provider_visible_input_utf8"}) for case_id in records.REQUIRED_CASE_IDS}
        self.plan = self._make_plan()
        self.instance = {
            "provider": "AutoDL", "instance_id": "recorded-instance", "gpu_model": "NVIDIA RTX 5090", "gpu_index": 0,
            "gpu_uuid": "GPU-test-uuid", "ssh_host_key_sha256": "8" * 64, "ssh_public_key_sha256": self.signer.key_sha256,
        }
        self.payloads = {case_id: {"provider_input": self.provider_inputs[case_id], "prompt": self.prompt_artifacts[case_id]} for case_id in records.REQUIRED_CASE_IDS}
        self.package = executor.prepare_trusted_remote_execution_package_v2(self.plan, self.payloads, self.instance, self.runtime_execution)
        self.clock = patch.object(executor, "_utc_now_text", return_value="2026-07-28T12:00:00Z")
        self.clock.start()
        self.pre_receipt = self._pre_receipt()
        self.actual_runtime = self._actual_runtime()

    def tearDown(self):
        self.clock.stop()
        shutil.rmtree(self.work, ignore_errors=True)

    def _rows(self, values):
        return [{"relative_path": name, "byte_length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in sorted(values.items())]

    def _make_plan(self, *, instance_id="recorded-instance", worker_raw=None):
        if worker_raw is None:
            worker_raw = (ROOT / "scripts" / "stage3_trusted_remote_qwen_worker.py").read_bytes()
        included = [{"path": "scripts/stage3_trusted_remote_qwen_worker.py", "git_mode": "100644", "blob_sha": "1" * 40, "byte_length": len(worker_raw), "sha256": hashlib.sha256(worker_raw).hexdigest()}]
        body = archive._manifest_body("f" * 40, "e" * 40, included, [], b"", authority=archive._AUTHORITY)
        manifest = archive.RepositoryArchiveManifest.from_dict(body)
        kwargs = _plan_kwargs(manifest)
        kwargs["target_instance"]["instance_id"] = instance_id
        kwargs["ssh"]["temporary_public_key_sha256"] = self.signer.key_sha256
        kwargs["model"] = {"exact_revision": "f" * 40, "files": self._rows(self.model_files)}
        kwargs["runtime"] = {"runtime_id": "recorded-runtime", "image_id": "recorded-image", "artifacts": self._rows(self.runtime_files)}
        for row in kwargs["cases"]:
            raw = self.provider_inputs[row["case_id"]]
            row["provider_input"] = {"input_id": self.selected.selection_record.input_view_id, "sha256": hashlib.sha256(raw).hexdigest(), "byte_length": len(raw)}
            row["frozen_g0"] = {"package_id": self.reference.package_id, "sha256": _reference_sha(self.reference)}
        return records.create_local_r0_record_bundle(**kwargs)[0]

    def _sign(self, payload, stem):
        payload_path = self.work / f"{stem}.json"
        payload_path.write_bytes(canonical(payload))
        result = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(self.private_key), "-n", executor.PRE_RUN_NAMESPACE, str(payload_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return payload_path.with_suffix(".json.sig").read_bytes()

    def _pre_receipt(self, *, plan=None, package=None, instance=None, nonce=None, issued="2026-07-28T00:00:00Z", expires="2026-07-29T00:00:00Z"):
        plan = plan or self.plan
        package = package or self.package
        instance = instance or self.instance
        nonce = nonce or (uuid4().hex * 2)
        payload = executor.build_trusted_remote_pre_run_authorization_payload_v2(plan, package, instance, nonce, issued, expires)
        return executor.create_trusted_remote_pre_run_authorization_receipt_v2(payload, self._sign(payload, "pre-" + nonce[:8]), self.signer)

    def _actual_runtime(self):
        data = self.package.to_dict()
        return {
            **self.runtime_execution["expected_versions"],
            "executable_path": self.runtime_execution["python_relative_path"],
            "torch_module_path": self.runtime_execution["torch_module_relative_path"],
            "transformers_module_path": self.runtime_execution["transformers_module_relative_path"],
            "device": "cuda:0", "gpu_index": 0, "gpu_uuid": self.instance["gpu_uuid"], "gpu_model": self.instance["gpu_model"],
            "dtype": "bf16", "quantization": "none", "model_repository": data["model_inventory"]["repository"],
            "model_revision": data["model_inventory"]["exact_revision"], "model_inventory_sha256": data["model_inventory"]["inventory_sha256"],
            "runtime_inventory_sha256": data["runtime_inventory"]["inventory_sha256"], "model_loaded": True,
        }

    def _manual_result(self, raw=None, test_only=False):
        raw = raw or {"path3-commerce-checkout": b'{"type":"page","case":"commerce"}', "path3-media-analysis": b'{"type":"page","case":"media"}'}
        return executor._create_execution_result(self.pre_receipt, self.package, self.actual_runtime, raw, "2026-07-28T12:00:00Z", "2026-07-28T12:01:00Z", 60000, test_only=test_only)

    def _private_verified_handle(self):
        result = self._manual_result()
        result_root = self.work / ("verified-result-" + uuid4().hex); result_root.mkdir()
        for row in result.to_dict()["cases"]:
            target = result_root / Path(row["raw_saved_relative_path"]); target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(__import__("base64").b64decode(row["raw_response_base64"]))
        (result_root / "execution_result.json").write_bytes(result.canonical_bytes())
        return executor._VerifiedExecutionHandle(result, self.package, self.plan, self.pre_receipt, result_root, executor._VERIFIED_EXECUTION_TOKEN)

    def _materialize_roots(self):
        repository_root = self.work / "repository"
        worker_target = repository_root / "scripts" / "stage3_trusted_remote_qwen_worker.py"; worker_target.parent.mkdir(parents=True); worker_target.write_bytes((ROOT / "scripts" / "stage3_trusted_remote_qwen_worker.py").read_bytes())
        package_root = self.work / "package"
        executor.materialize_trusted_remote_execution_inputs_v2(self.package, package_root, self.payloads)
        model_root = self.work / "model"; runtime_root = self.work / "runtime"
        for root, values in ((model_root, self.model_files), (runtime_root, self.runtime_files)):
            for relative, raw in values.items():
                target = root / Path(relative); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        (runtime_root / "bin" / "python").chmod(0o755)
        return repository_root, package_root, model_root, runtime_root

    def test_pre_run_has_unknown_result_and_internal_clock_only(self):
        payload = executor.build_trusted_remote_pre_run_authorization_payload_v2(self.plan, self.package, self.instance, "1" * 64, "2026-07-28T00:00:00Z", "2026-07-29T00:00:00Z")
        self.assertEqual(payload["unknown_post_run_values"]["raw_response_bytes"], "not_prebound")
        self.assertNotIn(b"raw_response_base64", canonical(payload))
        verified = executor.verify_trusted_remote_pre_run_authorization_receipt_v2(self.pre_receipt, self.plan, self.package, self.instance, self.signer)
        self.assertEqual(verified.sha256(), self.pre_receipt.sha256())
        self.assertNotIn("now_utc", inspect.signature(executor.verify_trusted_remote_pre_run_authorization_receipt_v2).parameters)
        self.assertNotIn("now_utc", inspect.signature(executor.run_trusted_remote_executor_v2).parameters)
        with patch.object(executor, "_utc_now_text", return_value="2026-07-30T00:00:00Z"):
            with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "expired"):
                executor.verify_trusted_remote_pre_run_authorization_receipt_v2(self.pre_receipt, self.plan, self.package, self.instance, self.signer)

    def test_package_accepts_empty_inventory_files_and_rejects_invalid_lengths(self):
        runtime_rows = self.package.to_dict()["runtime_inventory"]["artifacts"]
        self.assertIn(
            {
                "relative_path": "lib/python3.11/site-packages/example/py.typed",
                "byte_length": 0,
                "sha256": hashlib.sha256(b"").hexdigest(),
            },
            runtime_rows,
        )
        for invalid_length in (-1, 1.5, True, "0"):
            invalid = self.package.to_dict()
            invalid["runtime_inventory"]["artifacts"][0][
                "byte_length"
            ] = invalid_length
            with self.subTest(invalid_length=invalid_length):
                with self.assertRaisesRegex(
                    executor.TrustedRemoteExecutorError,
                    "runtime_inventory_byte_length_invalid",
                ):
                    executor.TrustedRemoteExecutionPackageV2.from_dict(invalid)

    def test_worker_identity_uses_archive_bytes_not_checkout_line_endings(self):
        archive_worker_raw = b"#!/usr/bin/env python3\n# canonical archive worker\n"
        self.assertNotEqual(
            archive_worker_raw,
            (ROOT / "scripts" / "stage3_trusted_remote_qwen_worker.py").read_bytes(),
        )
        plan = self._make_plan(worker_raw=archive_worker_raw)
        package = executor.prepare_trusted_remote_execution_package_v2(
            plan,
            self.payloads,
            self.instance,
            self.runtime_execution,
        )
        self.assertEqual(
            package.to_dict()["worker"],
            {
                "relative_path": executor.WORKER_RELATIVE_PATH,
                "sha256": hashlib.sha256(archive_worker_raw).hexdigest(),
                "byte_length": len(archive_worker_raw),
                "entrypoint": executor.WORKER_ENTRYPOINT,
                "command_prefix": list(executor.WORKER_COMMAND_PREFIX),
            },
        )
        repository_root = self.work / "archive-worker-repository"
        target = repository_root / executor.WORKER_RELATIVE_PATH
        target.parent.mkdir(parents=True)
        target.write_bytes(archive_worker_raw)
        self.assertEqual(
            executor._worker_script_at_repository_root(repository_root, package),
            target.resolve(),
        )

        forged = package.to_dict()
        forged["worker"]["sha256"] = "0" * 64
        forged["package_id"] = "trusted-remote-execution-package-v2-" + "0" * 64
        forged = executor._identified(
            forged,
            "package_id",
            "trusted-remote-execution-package-v2-",
        )
        with self.assertRaisesRegex(
            executor.TrustedRemoteExecutorError,
            "package_worker_plan_binding_invalid",
        ):
            executor._validate_package_against_plan(
                executor.TrustedRemoteExecutionPackageV2.from_dict(forged),
                plan,
            )

    def test_direct_worker_rejects_bad_authority_before_attempt_or_model_import(self):
        repository_root, package_root, model_root, runtime_root = self._materialize_roots()
        result_root = self.work / "direct-result"
        other_key = self.work / "other"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other_key)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
        other_public = subprocess.run(["ssh-keygen", "-y", "-f", str(other_key)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30).stdout
        wrong_signer = executor.ExpectedPreRunSigner(b" ".join(other_public.split()[:2]).decode("ascii"))
        with patch.object(executor.sys, "platform", "linux"):
            with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "signer"):
                executor.execute_worker_v2(self.package, self.plan, self.pre_receipt, wrong_signer, repository_root, package_root, model_root, runtime_root, result_root)
        self.assertFalse((package_root / executor._ATTEMPT_MARKER_RELATIVE_PATH).exists())
        self.assertNotIn("torch", executor.execute_worker_v2.__code__.co_names[:executor.execute_worker_v2.__code__.co_names.index("authorize_trusted_remote_execution_once_v2")])

    def test_attempt_replay_is_consumed_before_model_import(self):
        repository_root, package_root, model_root, runtime_root = self._materialize_roots()
        marker = package_root / executor._ATTEMPT_MARKER_RELATIVE_PATH
        marker.write_bytes(b"already")
        with patch.object(executor.sys, "platform", "linux"), patch.object(executor, "authorize_trusted_remote_execution_once_v2", return_value=self.pre_receipt):
            with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "already_consumed"):
                executor.execute_worker_v2(self.package, self.plan, self.pre_receipt, self.signer, repository_root, package_root, model_root, runtime_root, self.work / "result")
        self.assertFalse((self.work / "result").exists())

    def test_runtime_interpreter_module_versions_and_environment_are_authoritative(self):
        result = self._manual_result()
        self.assertEqual(executor.validate_execution_result_against_package_v2(result, self.package).sha256(), result.sha256())
        bad = copy.deepcopy(self.actual_runtime); bad["torch_module_path"] = "fake/torch.py"
        forged = executor._create_execution_result(self.pre_receipt, self.package, bad, {"path3-commerce-checkout": b"x", "path3-media-analysis": b"y"}, "2026-07-28T12:00:00Z", "2026-07-28T12:01:00Z", 1, test_only=False)
        with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "runtime_path"):
            executor.validate_execution_result_against_package_v2(forged, self.package)
        bad = copy.deepcopy(self.actual_runtime); bad["transformers"] = "fake"
        forged = executor._create_execution_result(self.pre_receipt, self.package, bad, {"path3-commerce-checkout": b"x", "path3-media-analysis": b"y"}, "2026-07-28T12:00:00Z", "2026-07-28T12:01:00Z", 1, test_only=False)
        with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "runtime_version"):
            executor.validate_execution_result_against_package_v2(forged, self.package)

    def test_provider_input_is_exactly_composed_with_fixed_prompt(self):
        _, package_root, _, _ = self._materialize_roots()
        row = self.package.to_dict()["cases"][0]
        text = executor._compose_exact_model_text(package_root, row)
        prompt_text = json.loads(self.prompt_artifacts[row["case_id"]])["prompt_text"]
        provider_text = self.provider_inputs[row["case_id"]].decode("utf-8")
        self.assertEqual(text, prompt_text + "\n\n<provider_visible_input>\n" + provider_text + "\n</provider_visible_input>")
        (package_root / Path(row["provider_input_file"]["relative_path"])).write_bytes(canonical({"omitted": True}))
        with self.assertRaises(executor.TrustedRemoteExecutorError):
            executor._compose_exact_model_text(package_root, row)
        bad_prompt = canonical({"schema_version": executor.PROMPT_ARTIFACT_SCHEMA, "prompt_text": "Static only", "provider_input_mode": "omit_input"})
        with self.assertRaises(executor.TrustedRemoteExecutorError):
            executor._prompt_artifact(bad_prompt, "prompt")


    def test_qwen_text_only_processor_allows_mm_token_type_ids_and_rejects_multimedia(self):
        class Tensor:
            def to(self, _device):
                return self

        approved = {"input_ids": Tensor(), "attention_mask": Tensor(), "mm_token_type_ids": Tensor()}
        self.assertEqual(
            executor._validated_text_only_processor_inputs(approved),
            ("attention_mask", "input_ids", "mm_token_type_ids"),
        )
        with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "multimedia"):
            executor._validated_text_only_processor_inputs({**approved, "pixel_values": Tensor()})
        with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "multimedia"):
            executor._validated_text_only_processor_inputs({**approved, "image_grid_thw": Tensor()})

    def test_raw_response_is_persisted_before_terminal_safe_live_report(self):
        result_root = self.work / "live-report-result"
        result_root.mkdir()
        raw = b'{"page_id":"main"}\n\x1b[31mnot-terminal-control\x1b[0m'
        stream = io.StringIO()
        with patch.object(executor.sys, "stderr", stream):
            executor._write_raw_first_and_report(
                result_root,
                "path3-commerce-checkout",
                raw,
                4096,
            )
        saved = result_root / "cases" / "path3-commerce-checkout" / "raw_response.bin"
        self.assertEqual(saved.read_bytes(), raw)
        output = stream.getvalue()
        self.assertIn('"event":"raw_response_persisted"', output)
        self.assertIn(f'"sha256":"{hashlib.sha256(raw).hexdigest()}"', output)
        self.assertIn("REQ2WEB_RAW_RESPONSE_BEGIN case_id=path3-commerce-checkout", output)
        self.assertIn("\\u001b[31mnot-terminal-control\\u001b[0m", output)
        self.assertNotIn("\x1b", output)
        self.assertLess(
            output.index("raw_response_persisted"),
            output.index("REQ2WEB_RAW_RESPONSE_BEGIN"),
        )

    def test_closed_binary_stderr_cannot_change_raw_first_execution(self):
        class ClosedBinaryStream:
            def write(self, _raw):
                raise BrokenPipeError("simulated closed stderr")

            def flush(self):
                raise AssertionError("flush must not follow failed write")

        class ClosedStderr:
            buffer = ClosedBinaryStream()

        result_root = self.work / "closed-stderr-result"
        result_root.mkdir()
        raw = b'{"page_id":"main"}'
        with patch.object(executor.sys, "stderr", ClosedStderr()):
            executor._emit_progress("preflight_complete")
            executor._write_raw_first_and_report(
                result_root,
                "path3-commerce-checkout",
                raw,
                4096,
            )
        saved = result_root / "cases" / "path3-commerce-checkout" / "raw_response.bin"
        self.assertEqual(saved.read_bytes(), raw)

    def test_manual_result_bytes_and_root_never_create_verified_execution(self):
        result = self._manual_result(test_only=False)
        with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "fixed_parent_worker"):
            executor._VerifiedExecutionHandle(result, self.package, self.plan, self.pre_receipt, self.work)
        forged = executor.TrustedRemoteExecutionResultV2(result.to_dict())
        self.assertEqual(forged.canonical_bytes(), result.canonical_bytes())
        self.assertFalse(hasattr(executor, "ExpectedPostRunSigner"))
        self.assertFalse(any("post_run" in name for name in executor.__all__))

    def test_result_root_is_bounded_and_canonical(self):
        result = self._manual_result()
        result_root = self.work / "result"; result_root.mkdir()
        for row in result.to_dict()["cases"]:
            target = result_root / Path(row["raw_saved_relative_path"]); target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(__import__("base64").b64decode(row["raw_response_base64"]))
        (result_root / "execution_result.json").write_bytes(result.canonical_bytes())
        self.assertEqual(executor.validate_trusted_remote_result_root_v2(result, result_root, self.package).sha256(), result.sha256())
        (result_root / "extra.log").write_bytes(b"x")
        with self.assertRaises(executor.TrustedRemoteExecutorError):
            executor.validate_trusted_remote_result_root_v2(result, result_root, self.package)

    def test_fixed_parent_uses_pinned_interpreter_isolated_env_timeout_and_cancel(self):
        parameters = tuple(inspect.signature(executor.run_trusted_remote_executor_v2).parameters)
        for forbidden in ("backend", "callback", "command", "authority", "now_utc"):
            self.assertNotIn(forbidden, parameters)
        repository_root, package_root, model_root, runtime_root = self._materialize_roots()
        result_root = self.work / "timeout-result"
        fake = None

        def make_process(command, **kwargs):
            nonlocal fake
            fake = _TimeoutProcess(command, **kwargs)
            return fake

        with patch.object(executor.sys, "platform", "linux"), patch.object(executor, "verify_trusted_remote_pre_run_authorization_receipt_v2", return_value=self.pre_receipt), patch.object(executor.signal, "SIGTERM", 15, create=True), patch.object(executor.signal, "SIGKILL", 9, create=True), patch.object(executor.time, "monotonic", side_effect=[0.0, 7201.0]), patch.object(executor.subprocess, "Popen", side_effect=make_process), patch.object(executor.os, "killpg", create=True) as killpg:
            with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "total_timeout"):
                executor.run_trusted_remote_executor_v2(self.package, self.plan, self.pre_receipt, self.signer, self.instance, repository_root, package_root, model_root, runtime_root, result_root)
        self.assertEqual(Path(fake.command[0]).resolve(), (runtime_root / "bin" / "python").resolve())
        self.assertEqual(fake.command[1:4], ["-I", "-s", "-E"])
        self.assertEqual(Path(fake.command[4]).resolve(), (repository_root / "scripts" / "stage3_trusted_remote_qwen_worker.py").resolve())
        self.assertTrue(fake.kwargs["start_new_session"])
        self.assertIsNone(fake.kwargs["stderr"])
        self.assertIs(fake.kwargs["stdout"], subprocess.PIPE)
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "CUDA_VISIBLE_DEVICES"):
            self.assertNotIn(key, fake.kwargs["env"])
        self.assertEqual(killpg.call_count, 2)

        cancel_path = self.work / "cancel.request"; cancel_path.write_text("cancel", encoding="utf-8")
        fake = None
        with patch.object(executor.sys, "platform", "linux"), patch.object(executor, "verify_trusted_remote_pre_run_authorization_receipt_v2", return_value=self.pre_receipt), patch.object(executor.signal, "SIGTERM", 15, create=True), patch.object(executor.signal, "SIGKILL", 9, create=True), patch.object(executor.time, "monotonic", return_value=0.0), patch.object(executor.subprocess, "Popen", side_effect=make_process), patch.object(executor.os, "killpg", create=True) as killpg:
            with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "cancelled"):
                executor.run_trusted_remote_executor_v2(self.package, self.plan, self.pre_receipt, self.signer, self.instance, repository_root, package_root, model_root, runtime_root, result_root, cancel_path)
        self.assertEqual(killpg.call_count, 2)

    def test_return_bundle_and_closeout_are_bounded_and_linked(self):
        handle = self._private_verified_handle()
        artifacts = {
            case_id: {
                "model_route_outcome": canonical({"case_id": case_id, "source": "trusted_remote_verified_execution"}),
                "gate_delivery_outcome": canonical({"case_id": case_id, "status": "fallback_delivery"}),
                "result_package_manifest": canonical({"case_id": case_id, "schema_version": "req2web.result.package.v2"}),
                "error": None,
            }
            for case_id in records.REQUIRED_CASE_IDS
        }
        project_temp = executor.create_trusted_remote_project_temp_root_v2(self.work / "project-temp", self.package)
        (project_temp / "work.bin").write_bytes(b"temporary")
        return_root = self.work / "return"
        manifest = executor.write_trusted_remote_return_bundle_v2(handle, artifacts, return_root, project_temp)
        self.assertEqual(manifest["state"], "pending_instance_release_and_access_revocation")
        self.assertFalse(project_temp.exists())
        actual = {path.relative_to(return_root).as_posix() for path in return_root.rglob("*") if path.is_file()}
        self.assertEqual(actual, set(records.RESULT_RETURN_PATHS))
        closeout = json.loads((return_root / "closeout" / "closeout.json").read_text(encoding="utf-8"))
        self.assertEqual(closeout["steps"]["process_completion"]["state"], "completed")
        self.assertEqual(closeout["steps"]["project_side_temporary_deletion"]["state"], "completed")
        self.assertEqual(closeout["steps"]["instance_release"]["state"], "pending_external_action")
        validated = executor.validate_downloaded_trusted_remote_return_bundle_v2(
            self.plan, self.package, return_root, expected_state="pending_instance_release_and_access_revocation"
        )
        self.assertEqual(validated["execution_result"].sha256(), handle._result.sha256())
        finalized = executor.finalize_downloaded_trusted_remote_closeout_v2(
            self.plan, self.package, return_root, b"instance-release-receipt", b"temporary-access-revocation-receipt"
        )
        self.assertEqual(finalized["state"], "completed")
        completed = json.loads((return_root / "closeout" / "closeout.json").read_text(encoding="utf-8"))
        self.assertEqual(completed["steps"]["instance_release"]["state"], "completed")
        self.assertEqual(completed["steps"]["temporary_access_revocation"]["state"], "completed")
        executor.validate_downloaded_trusted_remote_return_bundle_v2(
            self.plan, self.package, return_root, expected_state="completed"
        )


    def test_downloaded_closeout_rejects_extra_missing_and_tampered_return_files(self):
        artifacts = {
            case_id: {
                "model_route_outcome": canonical({"case_id": case_id, "source": "trusted_remote_verified_execution"}),
                "gate_delivery_outcome": canonical({"case_id": case_id, "status": "fallback_delivery"}),
                "result_package_manifest": canonical({"case_id": case_id, "schema_version": "req2web.result.package.v2"}),
                "error": None,
            }
            for case_id in records.REQUIRED_CASE_IDS
        }

        def make_bundle(stem):
            handle = self._private_verified_handle()
            project_temp = executor.create_trusted_remote_project_temp_root_v2(self.work / (stem + "-temp"), self.package)
            return_root = self.work / (stem + "-return")
            executor.write_trusted_remote_return_bundle_v2(handle, artifacts, return_root, project_temp)
            return return_root

        extra_root = make_bundle("extra")
        (extra_root / "unexpected.bin").write_bytes(b"unexpected")
        with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "path_inventory"):
            executor.finalize_downloaded_trusted_remote_closeout_v2(self.plan, self.package, extra_root, b"release", b"revoke")

        missing_root = make_bundle("missing")
        (missing_root / "cases" / "path3-commerce-checkout" / "raw_response.bin").unlink()
        with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "path_inventory"):
            executor.finalize_downloaded_trusted_remote_closeout_v2(self.plan, self.package, missing_root, b"release", b"revoke")

        tampered_root = make_bundle("tampered")
        (tampered_root / "cases" / "path3-media-analysis" / "gate_delivery_outcome.json").write_bytes(canonical({"tampered": True}))
        with self.assertRaisesRegex(executor.TrustedRemoteExecutorError, "index_or_manifest"):
            executor.finalize_downloaded_trusted_remote_closeout_v2(self.plan, self.package, tampered_root, b"release", b"revoke")

    def test_worker_entry_is_private_and_cli_has_no_clock_override(self):
        self.assertNotIn("_worker_main_v2", executor.__all__)
        self.assertFalse(hasattr(executor, "worker_main_v2"))
        script = (ROOT / "scripts" / "run_stage3_trusted_remote_executor.py").read_text(encoding="utf-8")
        self.assertNotIn("--now-utc", script)
        self.assertIn("run_trusted_remote_two_case_live_slice_v2", script)
        self.assertIn("load_fixed_trusted_remote_case_inputs_v2", script)
        self.assertIn("--return-root", script)
        worker_script = (
            ROOT / "scripts" / "stage3_trusted_remote_qwen_worker.py"
        ).read_text(encoding="utf-8")
        for source in (script, worker_script):
            self.assertLess(
                source.index("sys.dont_write_bytecode = True"),
                source.index("from req2web_runtime"),
            )
        finalizer = (ROOT / "scripts" / "finalize_stage3_trusted_remote_closeout.py").read_text(encoding="utf-8")
        self.assertIn("finalize_downloaded_trusted_remote_closeout_v2", finalizer)


if __name__ == "__main__":
    unittest.main()
