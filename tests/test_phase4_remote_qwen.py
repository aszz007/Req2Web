"""Focused no-model tests for the P4-03D2 remote Qwen adapter."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import uuid
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import req2web_runtime.phase4_remote_qwen as remote


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _inventory_evidence(source_root: Path, files: list[tuple[str, bytes]]) -> bytes:
    rows = []
    for relative, raw in files:
        digest = _sha(raw)
        rows.append(
            {
                "rfilename": relative,
                "exists": True,
                "expected_size": len(raw),
                "expected_lfs_sha256": digest,
                "expected_blob_id": f"blob-{relative}",
                "actual_size": len(raw),
                "actual_sha256": digest,
                "actual_git_blob_sha1": "0" * 40,
                "size_match": True,
                "identity_match": True,
            }
        )
    payload = {
        "repo_id": remote.REMOTE_MODEL_ID,
        "requested_revision": remote.REMOTE_MODEL_REVISION,
        "resolved_sha": remote.REMOTE_MODEL_REVISION,
        "root": str(source_root),
        "expected_file_count": len(files),
        "actual_repo_file_count": len(files),
        "missing": [],
        "extra": [],
        "total_actual_bytes": sum(len(raw) for _, raw in files),
        "all_sizes_match": True,
        "all_identities_match": True,
        "elapsed_seconds": 0.0,
        "files": rows,
    }
    return remote._canonical_bytes(payload) + b"\n"


@contextmanager
def _temporary_directory() -> Iterator[str]:
    # Use ordinary mkdir instead of TemporaryDirectory's Windows 0700 ACL.
    scratch_parent = Path(tempfile.gettempdir())
    directory = scratch_parent / f".p4-03d2-test-{uuid.uuid4().hex}"
    directory.mkdir()
    try:
        yield str(directory)
    finally:
        # Avoid shutil/tempfile's chmod-on-error path, which hangs on this
        # managed host. Every child here is created by this fixture.
        for child in sorted(
            directory.rglob("*"),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                if child.is_file() or child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass


class RemoteQwenPolicyTests(unittest.TestCase):
    def test_policy_is_canonical_and_exact(self):
        policy, raw = remote.load_remote_qwen_policy()
        self.assertEqual(raw, remote._canonical_bytes(policy.to_dict()))
        self.assertTrue(policy.authorization["remote_action"])
        self.assertEqual(policy.model["quantization"], "none")
        self.assertFalse(policy.model["bitsandbytes_required"])
        tampered = policy.to_dict()
        tampered["model"]["quantization"] = "4bit_nf4_double_quant"
        with self.assertRaises(remote.Phase4RemoteQwenContractError):
            remote.RemoteQwenPolicy.from_dict(tampered)

    def test_bf16_profile_rejects_local_quantized_profile_facts(self):
        root_identity = remote._identity(
            {"model_id": remote.REMOTE_MODEL_ID, "files": []},
            revision=remote.REMOTE_MODEL_ROOT_IDENTITY_REVISION,
            identity_kind="canonical_tree",
        )
        inventory_identity = remote._identity(
            [], revision=remote.REMOTE_INVENTORY_ROWS_REVISION, identity_kind="canonical_row_list"
        )
        profile = remote.RemoteQwenProfile.create(
            model_root_identity=root_identity,
            model_inventory_identity=inventory_identity,
            model_file_count=16,
            runtime_facts={
                "python_version": "3.12.0",
                "transformers_version": remote.REMOTE_TRANSFORMERS_VERSION,
                "torch_version": remote.REMOTE_TORCH_VERSION,
                "accelerate_version": remote.REMOTE_ACCELERATE_VERSION,
            },
            gpu_facts={
                "device_name": remote.REMOTE_DEVICE_NAME,
                "device_uuid": "GPU-test",
                "total_vram_bytes": remote.REMOTE_MIN_VRAM_BYTES,
                "free_vram_bytes": remote.REMOTE_MIN_VRAM_BYTES - 1,
                "driver_version": "test-driver",
                "cuda_version": "12.8",
            },
        )
        self.assertEqual(profile.quantization, "none")
        self.assertFalse(profile.bitsandbytes_required)
        tampered = profile.to_dict()
        tampered["quantization"] = "4bit_nf4_double_quant"
        tampered["profile_id"] = remote._identity(
            {key: value for key, value in tampered.items() if key != "profile_id"},
            revision=remote.REMOTE_PROFILE_SCHEMA_VERSION,
        )["sha256"]
        with self.assertRaises(remote.Phase4RemoteQwenContractError):
            remote.RemoteQwenProfile.from_dict(tampered)


class RemoteInventoryTests(unittest.TestCase):
    def test_relocation_live_hashes_exactly_sixteen_files(self):
        with _temporary_directory() as directory:
            root = Path(directory)
            source_root = root / "source-checkout"
            target_root = root / "remote-model-root"
            source_root.mkdir()
            target_root.mkdir()
            files = [(f"file-{index:02d}.bin", f"payload-{index}".encode()) for index in range(16)]
            for relative, raw in files:
                path = target_root / relative
                path.write_bytes(raw)
            evidence = root / "integrity.json"
            evidence.write_bytes(_inventory_evidence(source_root, files))
            inventory = remote.validate_remote_model_inventory(
                model_root=target_root,
                integrity_evidence=evidence,
            )
            self.assertEqual(inventory["file_count"], 16)
            relocation = inventory["relocation_binding"]
            self.assertTrue(relocation["source_path_is_not_remote_identity"])
            self.assertNotEqual(
                inventory["model_root_identity"]["identity_kind"], "canonical_json"
            )
            (target_root / "extra.bin").write_bytes(b"extra")
            with self.assertRaises(remote.Phase4RemoteQwenContractError):
                remote.validate_remote_model_inventory(
                    model_root=target_root,
                    integrity_evidence=evidence,
                )


class RemoteRawFirstTests(unittest.TestCase):
    def _prepared(self, root: Path, raw: bytes) -> tuple[remote.RemotePreparedExperiment, object, object]:
        # The production parser lazily imports the optional LangGraph runtime.
        # These tests patch that parser below so the no-model raw-first contract
        # can run in the repository's dependency-light test environment.
        state = {"authority": "captured-f1-f2"}
        fake_binding = SimpleNamespace(to_dict=lambda: {"binding": "checkpoint"})
        fake_prior = SimpleNamespace(
            result_id="sha256:" + "1" * 64,
            failure_code="generation_timeout",
            attempt_index=1,
            raw_status="not_captured",
            to_dict=lambda: {"prior": "failure"},
        )
        checkpoint = SimpleNamespace(
            binding=fake_binding,
            prior_failure=fake_prior,
            authority_state=state,
            outputs={"F1": b"f1", "F2": b"f2"},
        )
        fake_manifest = SimpleNamespace(to_dict=lambda: {"manifest": "preflight"})
        prepared = SimpleNamespace(
            result_root=root,
            policy_raw=b"policy",
            manifest=fake_manifest,
            checkpoint=checkpoint,
            input_bytes=b"{}",
            prompt_bytes=b"{}",
            config_bytes=b"{}",
            request_bytes=b"{}",
        )

        class FakeLoadReceipt:
            def to_dict(self):
                return {"load": "receipt"}

        class FakeClient:
            generation_started = False
            stderr_bytes = b"structured-stderr\n"

            def generate(self, **_):
                self.generation_started = True
                return raw

            def close(self):
                return {
                    "worker_id": "worker-remote-test",
                    "worker_pid": os.getpid(),
                    "worker_exit_code": 0,
                    "worker_exit_verified": True,
                    "graceful_shutdown_requested": True,
                    "terminate_sent": False,
                    "kill_sent": False,
                    "terminal_status": "normal_completed",
                }

        runtime = remote.RemoteRuntime(
            client=FakeClient(),
            loaded_facts={},
            load_receipt=FakeLoadReceipt(),
        )
        return prepared, runtime, checkpoint

    def test_raw_is_fsynced_before_parser_and_valid_f3_can_pass_node_only(self):
        with _temporary_directory() as directory:
            root = Path(directory)
            raw = remote._canonical_bytes({"interactions": [{"id": "synthetic-f3"}]})
            prepared, runtime, _ = self._prepared(root, raw)
            events: list[str] = []
            original_write = remote._write_remote_once

            def write(root_arg, relative, content):
                events.append(f"write:{relative}")
                return original_write(root_arg, relative, content)

            def parse(content, checkpoint):
                events.append("parse")
                self.assertEqual(content, raw)
                self.assertEqual(checkpoint.authority_state, {"authority": "captured-f1-f2"})
                return "parsed", "passed", "passed", {"registered": True}, None

            with patch.object(remote, "_write_remote_once", side_effect=write), patch.object(remote, "_parse_and_register_remote_f3", side_effect=parse):
                result = remote.execute_remote_qwen_bf16_f3(
                    prepared=prepared,
                    runtime=runtime,
                    mirror=remote.RemoteStreamMirror(io.StringIO()),
                )
            self.assertLess(events.index(f"write:{remote.REMOTE_RAW_NAME}"), events.index("parse"))
            self.assertTrue(result.node_model_pass)
            self.assertFalse(result.integrated)
            self.assertEqual(result.f4, "not_executed")
            self.assertEqual(result.composition, "not_executed")

    def test_contract_invalid_raw_is_not_repaired_or_marked_model_pass(self):
        with _temporary_directory() as directory:
            root = Path(directory)
            prepared, runtime, _ = self._prepared(root, b"{}")
            with patch.object(
                remote,
                "_parse_and_register_remote_f3",
                return_value=("parsed", "failed", "not_executed", None, "node_contract_invalid"),
            ):
                result = remote.execute_remote_qwen_bf16_f3(
                    prepared=prepared,
                    runtime=runtime,
                    mirror=remote.RemoteStreamMirror(io.StringIO()),
                )
            self.assertFalse(result.node_model_pass)
            self.assertEqual(result.failure_code, "node_contract_invalid")
            self.assertEqual(result.parse["status"], "parsed")
            self.assertEqual(result.node_contract["status"], "failed")
            self.assertFalse((root / "repaired_raw_response.bin").exists())


class RemoteCliTests(unittest.TestCase):
    def test_missing_confirmation_stops_before_action(self):
        from scripts import run_phase4_remote_qwen_stream_diagnostic as script

        args = [
            "--model-root", "model",
            "--integrity-evidence", "integrity.json",
            "--checkpoint-packet", "packet.json",
            "--checkpoint-receipt", "receipt.json",
            "--prior-f3-failure", "prior.json",
            "--result-root", "new-result",
        ]
        with patch.object(script, "run_remote_qwen_bf16_f3") as run:
            with self.assertRaises(SystemExit) as captured:
                script.main(args)
        self.assertEqual(captured.exception.code, 2)
        run.assert_not_called()

    def test_help_has_no_model_path_requirement(self):
        from scripts import run_phase4_remote_qwen_stream_diagnostic as script

        with self.assertRaises(SystemExit) as captured:
            script.main(["--help"])
        self.assertEqual(captured.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
