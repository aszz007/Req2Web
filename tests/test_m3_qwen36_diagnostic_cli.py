"""Focused no-model CLI tests for the Qwen3.6 diagnostic."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location("_" + path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREPARE = _load_script("prepare_stage3_qwen36_diagnostic.py")
ACTION = _load_script("prepare_stage3_qwen36_action_binding.py")
RUNNER = _load_script("run_stage3_qwen36_diagnostic.py")
EVALUATE = _load_script("evaluate_stage3_qwen36_diagnostic.py")


class _Record:
    def __init__(self, value: dict) -> None:
        self.value = value

    def to_dict(self) -> dict:
        return dict(self.value)

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def sha256(self) -> str:
        import hashlib

        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class Qwen36DiagnosticCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = ROOT / ("tmp-qwen36-cli-" + uuid4().hex)
        self.work.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def test_prepare_cli_create_then_validate_without_live_rescan(self) -> None:
        runtime_binding = {
            "inventory_id": "d" * 64,
            "sha256": "e" * 64,
            "tree_sha256": "f" * 64,
            "runtime_root_sha256": "1" * 64,
            "interpreter_relative_path": "bin/python",
            "versions": {
                "python": "3.11.15",
                "torch": "2.7.1+cu128",
                "transformers": "5.14.1",
                "cuda": "12.8",
            },
        }
        plan = _Record({"plan_id": "a" * 64, "runtime": runtime_binding})
        inventory = _Record(
            {"inventory_id": "b" * 64, "tree_sha256": "c" * 64}
        )
        runtime_data = {
            "inventory_id": "d" * 64,
            "tree_sha256": "f" * 64,
        }
        output = self.work / "prepared"
        args = [
            "--runtime-root",
            str(self.work / "runtime"),
            "--model-root",
            str(self.work / "model"),
            "--repository-archive",
            str(self.work / "archive"),
            "--repository-archive-manifest",
            str(self.work / "manifest"),
            "--case-bundle-root",
            str(self.work / "bundle"),
            "--output-root",
            str(output),
        ]
        with (
            patch.object(
                PREPARE.runtime_authority,
                "collect_runtime_root_inventory",
                return_value=runtime_data,
            ) as runtime_collect,
            patch.object(
                PREPARE.diagnostic,
                "collect_qwen36_model_inventory",
                return_value=inventory,
            ) as model_collect,
            patch.object(
                PREPARE.diagnostic,
                "create_qwen36_diagnostic_plan",
                return_value=plan,
            ),
        ):
            self.assertEqual(PREPARE.main(args), 0)
        runtime_collect.assert_called_once()
        model_collect.assert_called_once()
        runtime_raw = PREPARE.runtime_authority._dump(runtime_data)
        with (
            patch.object(
                PREPARE.diagnostic,
                "_read_runtime_inventory",
                return_value=(runtime_data, runtime_raw),
            ),
            patch.object(
                PREPARE.diagnostic.Qwen36ModelInventory,
                "from_bytes",
                return_value=inventory,
            ),
            patch.object(
                PREPARE.diagnostic,
                "create_qwen36_diagnostic_plan",
                return_value=plan,
            ),
            patch.object(
                PREPARE.runtime_authority,
                "collect_runtime_root_inventory",
            ) as runtime_collect,
            patch.object(
                PREPARE.diagnostic,
                "collect_qwen36_model_inventory",
            ) as model_collect,
        ):
            self.assertEqual(
                PREPARE.main([*args, "--validate-only"]), 0
            )
        runtime_collect.assert_not_called()
        model_collect.assert_not_called()
        self.assertEqual(
            (output / PREPARE.PLAN_FILE).read_bytes(),
            plan.canonical_bytes(),
        )
        self.assertEqual(
            (output / PREPARE.MODEL_INVENTORY_FILE).read_bytes(),
            inventory.canonical_bytes(),
        )
        self.assertEqual(
            (output / PREPARE.RUNTIME_INVENTORY_FILE).read_bytes(),
            runtime_raw,
        )

    def test_action_binding_cli_create_and_validate_only_are_exact(self) -> None:
        binding = _Record(
            {
                "binding_id": "9" * 64,
                "selected_gpu": {
                    "index": 0,
                    "name": (
                        "NVIDIA RTX PRO 6000 Blackwell Server Edition"
                    ),
                    "uuid": (
                        "GPU-12345678-1234-1234-1234-123456789abc"
                    ),
                },
            }
        )
        output = self.work / "action-binding.json"
        args = [
            "--plan",
            "plan",
            "--model-inventory",
            "inventory",
            "--runtime-inventory",
            "runtime-inventory",
            "--expected-gpu-name",
            "NVIDIA RTX PRO 6000 Blackwell Server Edition",
            "--expected-gpu-uuid",
            "GPU-12345678-1234-1234-1234-123456789abc",
            "--output",
            str(output),
        ]
        with (
            patch.object(
                ACTION.diagnostic,
                "create_qwen36_diagnostic_action_binding",
                return_value=binding,
            ),
            patch.object(
                ACTION.diagnostic.Qwen36DiagnosticActionBinding,
                "from_bytes",
                return_value=binding,
            ),
        ):
            self.assertEqual(ACTION.main(args), 0)
            self.assertEqual(
                ACTION.main([*args, "--validate-only"]), 0
            )
        self.assertEqual(output.read_bytes(), binding.canonical_bytes())

    def test_evaluator_cli_create_and_validate_only_are_exact(self) -> None:
        record = {
            "record_id": "qwen36-27b-diagnostic-route-v1-" + "a" * 64,
            "cases": [
                {"case_id": "path3-commerce-checkout", "decision": "fail_closed"},
                {"case_id": "path3-media-analysis", "decision": "recovery_pass"},
            ],
            "overall_decision": "fail_closed",
        }
        output = self.work / "evaluation"
        args = [
            "--plan",
            "plan",
            "--action-binding",
            "action",
            "--model-inventory",
            "inventory",
            "--runtime-inventory",
            "runtime-inventory",
            "--repository-archive",
            "archive",
            "--repository-archive-manifest",
            "manifest",
            "--case-bundle-root",
            "bundle",
            "--run-root",
            "run",
            "--output-root",
            str(output),
        ]
        with patch.object(EVALUATE, "_evaluate", return_value=record):
            self.assertEqual(EVALUATE.main(args), 0)
            self.assertEqual(EVALUATE.main([*args, "--validate-only"]), 0)
        self.assertEqual(
            json.loads((output / EVALUATE.RUN_FILE).read_text("utf-8"))[
                "overall_decision"
            ],
            "fail_closed",
        )

    def _runner_args(self):
        return RUNNER.build_parser().parse_args(
            [
                "--plan",
                "plan",
                "--action-binding",
                str(self.work / "action.json"),
                "--model-inventory",
                "inventory",
                "--runtime-inventory",
                "runtime-inventory",
                "--model-root",
                "model",
                "--runtime-root",
                "runtime",
                "--repository-archive",
                "archive",
                "--repository-archive-manifest",
                "manifest",
                "--case-bundle-root",
                "bundle",
                "--result-root",
                "result",
            ]
        )

    def test_parent_failure_and_sigterm_terminate_process_group(self) -> None:
        args = self._runner_args()
        action = self.work / "action.json"
        action.write_bytes(b"action")
        for raised in (KeyboardInterrupt(), RuntimeError("parent failed")):
            process = Mock()
            process.pid = 7123
            with (
                patch.object(RUNNER.sys, "platform", "linux"),
                patch.object(
                    RUNNER.diagnostic.Qwen36DiagnosticActionBinding,
                    "from_bytes",
                    return_value=Mock(),
                ),
                patch.object(RUNNER.subprocess, "Popen", return_value=process),
                patch.object(RUNNER, "wait_for_worker", side_effect=raised),
                patch.object(RUNNER, "terminate_process_group") as terminate,
            ):
                with self.assertRaises(type(raised)):
                    RUNNER._parent(args)
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
            handlers[RUNNER.signal.SIGTERM](RUNNER.signal.SIGTERM, None)

        with (
            patch.object(RUNNER.sys, "platform", "linux"),
            patch.object(
                RUNNER.diagnostic.Qwen36DiagnosticActionBinding,
                "from_bytes",
                return_value=Mock(),
            ),
            patch.object(RUNNER.subprocess, "Popen", return_value=process),
            patch.object(
                RUNNER.signal, "getsignal", return_value=RUNNER.signal.SIG_DFL
            ),
            patch.object(RUNNER.signal, "signal", side_effect=install),
            patch.object(RUNNER, "wait_for_worker", side_effect=signal_wait),
            patch.object(RUNNER, "terminate_process_group") as terminate,
        ):
            with self.assertRaisesRegex(
                RUNNER.diagnostic.Qwen36DiagnosticError,
                "diagnostic_parent_sigterm",
            ):
                RUNNER._parent(args)
        terminate.assert_called_once_with(process, process_group_id=7123)

    def test_reused_waiter_cancel_file_terminates_process_group(self) -> None:
        cancel = self.work / "cancel"
        cancel.write_text("cancel", encoding="utf-8")
        process = Mock()
        with (
            patch(
                "req2web_runtime.qwen27b_recovery_runner."
                "terminate_process_group"
            ) as terminate,
            self.assertRaisesRegex(
                RUNNER.Qwen27BRecoveryRunnerError, "recovery_cancelled"
            ),
        ):
            RUNNER.wait_for_worker(
                process,
                timeout_seconds=10,
                cancel_path=cancel,
                process_group_id=7123,
            )
        terminate.assert_called_once_with(
            process, process_group_id=7123
        )


if __name__ == "__main__":
    unittest.main()
