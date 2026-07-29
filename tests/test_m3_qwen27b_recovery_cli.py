"""Fixture-only tests for Qwen3.5-27B no-GPU and local-evaluation CLIs."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime import qwen27b_predeploy as predeploy


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location("_" + path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREDEPLOY_CLI = _load_script("prepare_stage3_qwen27b_predeploy.py")
EVALUATOR_CLI = _load_script("evaluate_stage3_qwen27b_recovery.py")
RUNNER_CLI = _load_script("run_stage3_qwen27b_recovery.py")


class _Artifact:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value

    def to_dict(self) -> dict[str, object]:
        return dict(self.value)


class Qwen27BRecoveryCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / f"tmp-qwen27b-recovery-cli-{uuid4().hex}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _predeploy_args(self, output_root: Path, *, validate_only: bool = False, source_tree: str = "2" * 40) -> list[str]:
        arguments = [
            "--runtime-root", str(self.runtime_root),
            "--model-root", str(self.model_root),
            "--repository-archive", str(self.archive),
            "--repository-archive-manifest", str(self.archive_manifest),
            "--case-bundle-root", str(self.case_root),
            "--source-commit", "1" * 40,
            "--source-tree", source_tree,
            "--selected-copy", "none",
            "--output-root", str(output_root),
        ]
        if validate_only:
            arguments.append("--validate-only")
        return arguments

    def _predeploy_fixture(self) -> dict[str, object]:
        self.runtime_root = self.root / "runtime"
        for relative in ("bin/python", *predeploy._RUNTIME_STATIC_FILES):
            path = self.runtime_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("runtime:" + relative).encode("utf-8"))
        self.model_root = self.root / "model"; self.model_root.mkdir()
        for name in predeploy._MODEL_FILES:
            (self.model_root / name).write_bytes(("fixture:" + name).encode("utf-8"))
        self.archive = self.root / "req2web.zip"; self.archive.write_bytes(b"fixture-archive")
        self.archive_manifest = self.root / "req2web.manifest.json"
        self.archive_manifest.write_bytes(b"fixture-manifest")
        self.case_root = self.root / "case-bundle"; self.case_root.mkdir()
        (self.case_root / "case.json").write_bytes(b"fixture-cases")
        runtime = predeploy.create_runtime_inventory(self.runtime_root)
        self.runtime_inventory_path = self.root / "runtime-inventory.json"
        self.runtime_inventory_path.write_bytes(predeploy._dump(runtime))
        archive_sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive_authority = {
            "manifest_id": "a" * 64,
            "manifest_sha256": "b" * 64,
            "commit_sha": "1" * 40,
            "tree_sha": "2" * 40,
            "archive_byte_length": self.archive.stat().st_size,
            "archive_sha256": archive_sha,
        }
        self.model_inventory = predeploy.Qwen27BPredeployInventory.from_dict(
            predeploy._official_model_inventory_authority()["inventory"]
        )
        self.bundle = SimpleNamespace(
            manifest={
                "source": {
                    "repository_archive_authority": self.archive_authority
                }
            }
        )
        return predeploy._create_no_gpu_runtime_probe(self.runtime_inventory_path.read_bytes())

    def test_predeploy_cli_calls_live_probe_and_replays_without_model_or_network(self) -> None:
        fixture_probe = self._predeploy_fixture()
        output_root = self.root / "predeploy-output"
        with (
            patch.object(
                PREDEPLOY_CLI.predeploy,
                "probe_no_gpu_runtime",
                return_value=fixture_probe,
            ) as probe,
            patch.object(
                PREDEPLOY_CLI.predeploy,
                "validate_repository_archive_authority",
                return_value=self.archive_authority,
            ),
            patch.object(
                PREDEPLOY_CLI.predeploy,
                "collect_model_root_inventory",
                return_value=self.model_inventory,
            ),
            patch.object(
                PREDEPLOY_CLI,
                "validate_qwen27b_case_bundle",
                return_value=self.bundle,
            ),
            patch(
                "req2web_runtime.qwen27b_case_bundle."
                "validate_qwen27b_case_bundle",
                return_value=self.bundle,
            ),
        ):
            self.assertEqual(PREDEPLOY_CLI.main(self._predeploy_args(output_root)), 0)
            self.assertEqual(PREDEPLOY_CLI.main(self._predeploy_args(output_root, validate_only=True)), 0)
        self.assertEqual(probe.call_count, 2)
        manifest = predeploy.Qwen27BPredeployManifest.from_bytes((output_root / PREDEPLOY_CLI.MANIFEST_FILE).read_bytes())
        ready = predeploy.Qwen27BPredeployReadyNoGpu.from_bytes((output_root / PREDEPLOY_CLI.READY_FILE).read_bytes())
        self.assertEqual(manifest.to_dict()["status"], "predeploy_manifest_no_gpu_not_action")
        self.assertEqual(ready.to_dict()["next_gate"], "fresh_gpu_action_gate_required")
        with (
            patch.object(
                PREDEPLOY_CLI.predeploy,
                "probe_no_gpu_runtime",
                return_value=fixture_probe,
            ),
            patch.object(
                PREDEPLOY_CLI.predeploy,
                "validate_repository_archive_authority",
                return_value=self.archive_authority,
            ),
            patch.object(
                PREDEPLOY_CLI.predeploy,
                "collect_model_root_inventory",
                return_value=self.model_inventory,
            ),
            patch.object(
                PREDEPLOY_CLI,
                "validate_qwen27b_case_bundle",
                return_value=self.bundle,
            ),
            patch(
                "req2web_runtime.qwen27b_case_bundle."
                "validate_qwen27b_case_bundle",
                return_value=self.bundle,
            ),
        ):
            with self.assertRaisesRegex(SystemExit, "source commit/tree"):
                PREDEPLOY_CLI.main(self._predeploy_args(output_root, validate_only=True, source_tree="3" * 40))

    def test_evaluator_cli_preserves_raw_and_returns_fail_closed_sidecars(self) -> None:
        run_root = self.root / "run-root"; run_root.mkdir()
        raw_by_case = {
            "path3-commerce-checkout": b"not-json-commerce",
            "path3-media-analysis": b"not-json-media",
        }
        record = {
            "schema_version": "req2web.runtime.qwen35_27b_final_route.v1",
            "record_id": "qwen35-27b-final-route-v1-" + "a" * 64,
            "source_run": {"run_id": "b" * 64},
            "case_ids": list(EVALUATOR_CLI.CASE_IDS),
            "cases": [
                {
                    "case_id": case_id,
                    "source_raw": {
                        "byte_length": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    },
                    "parser_status": "failed",
                    "assembler_status": "failed",
                    "generic_route": {
                        "status": "fallback_delivery",
                        "delivery_source": "g0_frozen_fallback",
                    },
                    "semantic_coverage": None,
                    "decision": "fail_closed",
                }
                for case_id, raw in raw_by_case.items()
            ],
            "overall_decision": "fail_closed",
            "claims": {"development_recovery_pilot_only": True},
        }
        output_root = self.root / "evaluation-output"
        argv = [
            "--predeploy-manifest", str(self.root / "manifest"),
            "--predeploy-ready", str(self.root / "ready"),
            "--repository-archive", str(self.root / "archive"),
            "--repository-archive-manifest", str(self.root / "archive-manifest"),
            "--case-bundle-root", str(self.root / "bundle"),
            "--selected-copy", "none",
            "--run-root", str(run_root),
            "--output-root", str(output_root),
        ]
        with patch.object(EVALUATOR_CLI, "_evaluate", return_value=record) as evaluate:
            self.assertEqual(EVALUATOR_CLI.main(argv), 0)
            self.assertEqual(EVALUATOR_CLI.main([*argv, "--validate-only"]), 0)
        self.assertEqual(evaluate.call_count, 2)
        run = json.loads((output_root / EVALUATOR_CLI.RUN_FILE).read_text(encoding="utf-8"))
        self.assertEqual(run["overall_decision"], "fail_closed")
        for case_id, raw in raw_by_case.items():
            sidecar = json.loads((output_root / "cases" / case_id / "evaluation.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["source_raw"]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(sidecar["parser_status"], "failed")
            self.assertEqual(sidecar["assembler_status"], "failed")
            self.assertEqual(sidecar["decision"], "fail_closed")

    def test_parent_interrupt_signal_and_exception_always_terminate_worker_group(
        self,
    ) -> None:
        argv = [
            "--predeploy-manifest", "manifest",
            "--predeploy-ready", "ready",
            "--model-root", "model",
            "--runtime-root", "runtime",
            "--repository-archive", "archive",
            "--repository-archive-manifest", "archive-manifest",
            "--case-bundle-root", "bundle",
            "--result-root", "result",
            "--selected-copy", "none",
            "--expected-gpu-name", "NVIDIA RTX PRO 6000 Blackwell",
            "--expected-gpu-uuid", "GPU-fixture",
        ]
        args = RUNNER_CLI._parser().parse_args(argv)

        for label, raised in (
            ("keyboard", KeyboardInterrupt()),
            ("parent_exception", RuntimeError("parent failed")),
        ):
            with self.subTest(label=label):
                process = Mock()
                process.pid = 7123
                with (
                    patch.object(RUNNER_CLI.sys, "platform", "linux"),
                    patch.object(RUNNER_CLI.subprocess, "Popen", return_value=process),
                    patch.object(
                        RUNNER_CLI, "wait_for_worker", side_effect=raised
                    ) as wait,
                    patch.object(RUNNER_CLI, "terminate_process_group") as terminate,
                ):
                    with self.assertRaises(type(raised)):
                        RUNNER_CLI._parent(args)
                wait.assert_called_once_with(
                    process,
                    args.timeout_seconds,
                    None,
                    process_group_id=7123,
                )
                terminate.assert_called_once_with(
                    process, process_group_id=7123
                )

        process = Mock()
        process.pid = 7123
        handlers = {}

        def install(signum, handler):
            if callable(handler):
                handlers[signum] = handler

        def signal_wait(*_args, **_kwargs):
            handlers[RUNNER_CLI.signal.SIGTERM](
                RUNNER_CLI.signal.SIGTERM,
                None,
            )

        with (
            patch.object(RUNNER_CLI.sys, "platform", "linux"),
            patch.object(RUNNER_CLI.subprocess, "Popen", return_value=process),
            patch.object(RUNNER_CLI.signal, "getsignal", return_value=RUNNER_CLI.signal.SIG_DFL),
            patch.object(RUNNER_CLI.signal, "signal", side_effect=install),
            patch.object(
                RUNNER_CLI, "wait_for_worker", side_effect=signal_wait
            ) as wait,
            patch.object(RUNNER_CLI, "terminate_process_group") as terminate,
        ):
            with self.assertRaisesRegex(
                RUNNER_CLI.Qwen27BRecoveryRunnerError,
                "parent_sigterm",
            ):
                RUNNER_CLI._parent(args)
        wait.assert_called_once_with(
            process,
            args.timeout_seconds,
            None,
            process_group_id=7123,
        )
        terminate.assert_called_once_with(process, process_group_id=7123)


if __name__ == "__main__":
    unittest.main()
