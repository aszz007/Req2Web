from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from req2web_runtime import phase4_remote_qwen_stability as runtime
from req2web_runtime.phase4_stability_cases import (
    get_stability_case_set,
    validate_stability_case_set,
)
from scripts import run_phase4_remote_qwen_stability as cli


class _FakeProfile:
    def validate(self) -> None:
        return None

    def to_dict(self) -> dict[str, object]:
        return {"profile": "fake-rtx5090-bf16"}

    def canonical_bytes(self) -> bytes:
        return b'{"profile":"fake-rtx5090-bf16"}'


class Phase4RemoteQwenStabilityCliTests(unittest.TestCase):
    def _common_args(self, result_root: Path) -> list[str]:
        return [
            "--model-root",
            str(_ROOT),
            "--integrity-evidence",
            str(_ROOT / "docs" / "project_memory.md"),
            "--result-root",
            str(result_root),
        ]

    def test_parser_exposes_required_baseline_controls(self) -> None:
        help_text = cli.build_parser().format_help()
        for option in (
            "--model-root",
            "--integrity-evidence",
            "--result-root",
            "--run-id",
            "--preflight-only",
            "--confirm-ten-case-baseline",
        ):
            self.assertIn(option, help_text)

    def test_non_preflight_requires_explicit_confirmation(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            cli.main(
                self._common_args(
                    Path("C:/p4-05-test-only/stability-result")
                )
            )
        self.assertEqual(raised.exception.code, 2)

    def test_preflight_dispatches_without_running_model_baseline(self) -> None:
        result_root = Path("C:/p4-05-test-only/stability-preflight")
        prepared = {"run_id": "p4-05-test-preflight"}
        stdout = io.StringIO()
        with patch.dict(os.environ, {}, clear=False):
            with patch.object(
                runtime,
                "prepare_phase4_remote_qwen_stability",
                return_value=prepared,
            ) as prepare, patch.object(
                runtime,
                "run_phase4_remote_qwen_stability",
            ) as run, patch("sys.stdout", stdout):
                code = cli.main(
                    self._common_args(result_root)
                    + [
                        "--run-id",
                        "p4-05-test-preflight",
                        "--preflight-only",
                    ]
                )

        self.assertEqual(code, 0)
        prepare.assert_called_once_with(
            model_root=_ROOT.resolve(strict=True),
            integrity_evidence=(
                _ROOT / "docs" / "project_memory.md"
            ).resolve(strict=True),
            result_root=result_root.resolve(strict=False),
            run_id="p4-05-test-preflight",
        )
        run.assert_not_called()
        self.assertIn("status=prepared_no_model", stdout.getvalue())

    def test_run_dispatches_console_and_prints_stable_summary(self) -> None:
        result_root = Path("C:/p4-05-test-only/stability-run")
        summary = {
            "baseline_complete": True,
            "aggregate": {
                "case_count": 10,
                "total_model_generate_calls": 37,
                "f4_raw_direct_pass_count": 6,
                "f4_normalized_case_count": 2,
                "deterministic_repair_count": 4,
                "g0_fallback_count": 1,
                "delivery_success_count": 9,
                "failed_closed_count": 1,
            },
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.dict(os.environ, {}, clear=False):
            with patch.object(
                runtime,
                "run_phase4_remote_qwen_stability",
                return_value=summary,
            ) as run, patch("sys.stdout", stdout), patch(
                "sys.stderr", stderr
            ):
                code = cli.main(
                    self._common_args(result_root)
                    + [
                        "--run-id",
                        "p4-05-test-run",
                        "--confirm-ten-case-baseline",
                    ]
                )

        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertEqual(
            run.call_args.kwargs,
            {
                "model_root": _ROOT.resolve(strict=True),
                "integrity_evidence": (
                    _ROOT / "docs" / "project_memory.md"
                ).resolve(strict=True),
                "result_root": result_root.resolve(strict=False),
                "confirm_ten_case_baseline": True,
                "run_id": "p4-05-test-run",
                "console": stderr,
            },
        )
        output = stdout.getvalue()
        for fragment in (
            "status=baseline_complete",
            "completed_cases=10",
            "total_model_generate_calls=37",
            "f4_raw_direct_pass=6",
            "normalization=2",
            "repair=4",
            "fallback=1",
            "delivery=9",
            "failed_closed=1",
        ):
            self.assertIn(fragment, output)

    def test_runtime_exception_is_reported_fail_closed(self) -> None:
        stderr = io.StringIO()
        with patch.object(
            runtime,
            "run_phase4_remote_qwen_stability",
            side_effect=runtime.Phase4RemoteQwenStabilityError(
                "synthetic stability failure"
            ),
        ):
            with patch("sys.stderr", stderr):
                code = cli.main(
                    self._common_args(
                        Path("C:/p4-05-test-only/stability-failure")
                    )
                    + ["--confirm-ten-case-baseline"]
                )

        self.assertEqual(code, 2)
        self.assertIn(
            "failed closed: synthetic stability failure",
            stderr.getvalue(),
        )

    def test_offline_process_sets_bounded_runtime_environment(self) -> None:
        expected = {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "LANGSMITH_TRACING": "0",
            "LANGCHAIN_TRACING_V2": "0",
            "CUDA_VISIBLE_DEVICES": "0",
            "PYTHONUNBUFFERED": "1",
        }
        with patch.dict(os.environ, {}, clear=True):
            cli._offline_process()
            self.assertEqual(
                {key: os.environ[key] for key in expected},
                expected,
            )


class Phase4RemoteQwenStabilityRuntimeTests(unittest.TestCase):
    def test_prepare_freezes_exact_case_set_and_no_model_policy(self) -> None:
        result_root = Path("C:/p4-05-test-only/stability-preflight-runtime")
        inventory = {"inventory_identity": {"sha256": "sha256:" + ("1" * 64)}}
        gpu = {
            "device_name": runtime._fresh._remote.REMOTE_DEVICE_NAME,
            "total_vram_bytes": runtime._fresh._remote.REMOTE_MIN_VRAM_BYTES,
        }
        with patch.object(
            runtime._fresh._remote,
            "validate_remote_model_inventory",
            return_value=inventory,
        ), patch.object(
            runtime._fresh._remote,
            "_collect_remote_runtime_facts",
            return_value={"runtime": "fake"},
        ), patch.object(
            runtime._fresh._remote,
            "_probe_remote_gpu_facts",
            return_value=gpu,
        ), patch.object(
            runtime._fresh.RemoteFreshIntegratedProfile,
            "create",
            return_value=_FakeProfile(),
        ), patch.object(
            runtime._fresh,
            "_write_fsync",
        ) as write_artifact, patch.object(
            Path,
            "mkdir",
            return_value=None,
        ):
            prepared = runtime.prepare_phase4_remote_qwen_stability(
                model_root=_ROOT,
                integrity_evidence=_ROOT / "docs" / "project_memory.md",
                result_root=result_root,
                run_id="p4-05-stability-test-preflight",
            )

        self.assertEqual(prepared["run_id"], "p4-05-stability-test-preflight")
        written = {
            call.args[0].name: call.args[1]
            for call in write_artifact.call_args_list
        }
        case_set = json.loads(written["stability_case_set.json"].decode("utf-8"))
        self.assertEqual(validate_stability_case_set(case_set), case_set)
        policy = json.loads(written["stability_policy.json"].decode("utf-8"))
        self.assertEqual(policy["baseline_total_call_cap"], 40)
        self.assertIs(policy["training"], False)
        self.assertIs(policy["action_state"]["model_action"], False)

    def test_aggregate_separates_raw_and_system_adjustment_rates(self) -> None:
        case_result = {
            "status": "delivery_terminal_success",
            "per_node_generate_calls": {
                "F1": 1,
                "F2": 1,
                "F3": 1,
                "F4": 1,
            },
            "per_node_raw_contract_pass": {
                "F1": True,
                "F2": True,
                "F3": True,
                "F4": True,
            },
            "per_node_failure_codes": {
                "F1": None,
                "F2": None,
                "F3": None,
                "F4": None,
            },
            "f4_called": True,
            "f4_raw_direct_pass": True,
            "f4_normalization_used": False,
            "composition_pass": True,
            "assembler_pass": True,
            "downstream_first_pass_success": False,
            "deterministic_repair_success": True,
            "g0_fallback_success": False,
            "delivery_success": True,
            "system_adjustment_used": True,
            "total_model_generate_calls": 4,
            "failure_code": None,
        }

        aggregate = runtime._aggregate([case_result] * 10)

        self.assertEqual(aggregate["total_model_generate_calls"], 40)
        self.assertEqual(aggregate["f4_raw_direct_pass_rate"], 1.0)
        self.assertEqual(aggregate["f4_normalization_rate"], 0.0)
        self.assertEqual(aggregate["deterministic_repair_rate"], 1.0)
        self.assertEqual(aggregate["delivery_success_rate"], 1.0)
        self.assertEqual(aggregate["system_adjustment_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
