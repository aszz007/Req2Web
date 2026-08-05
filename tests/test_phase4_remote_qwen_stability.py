from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid


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


_PROFILE_RECORD = {"profile": "fake-rtx5090-bf16"}
_PROFILE_IDENTITY = runtime._fresh._identity(
    _PROFILE_RECORD,
    revision=runtime.STABILITY_PROFILE_BINDING_SCHEMA_VERSION,
)
_INVENTORY_IDENTITY = {
    "identity_kind": "canonical_json",
    "sha256": "sha256:" + ("2" * 64),
    "byte_length": 202,
    "revision": "test.model.inventory.v1",
}
_PARENT_BINDING = {
    "schema_version": runtime._fresh.P4_05_PARENT_BINDING_SCHEMA_VERSION,
    "experiment_id": "p4-05-commerce-stability-10",
    "experiment_run_id": "stability-test-run",
    "experiment_policy_identity": {
        "identity_kind": "canonical_json",
        "sha256": "sha256:" + ("3" * 64),
        "byte_length": 303,
        "revision": runtime.STABILITY_POLICY_SCHEMA_VERSION,
    },
    "case_index": 1,
    "case_id": "placeholder",
    "request_id": "placeholder",
}


class _FakeProfile:
    def validate(self) -> None:
        return None

    def to_dict(self) -> dict[str, object]:
        return dict(_PROFILE_RECORD)

    def canonical_bytes(self) -> bytes:
        return b'{"profile":"fake-rtx5090-bf16"}'


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(runtime._fresh._canonical_bytes(value))


class _WorkspaceTempDirectory:
    def __init__(self) -> None:
        self.path = _ROOT / f".tmp-phase4-stability-{uuid.uuid4().hex}"

    def __enter__(self) -> str:
        self.path.mkdir(parents=False, exist_ok=False)
        return str(self.path)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def _child_run_id(experiment_run_id: str, index: int) -> str:
    return f"{experiment_run_id}-case-{index:02d}"


def _case_parent_binding(
    experiment_run_id: str,
    index: int,
    case: dict[str, object],
) -> dict[str, object]:
    binding = dict(_PARENT_BINDING)
    binding["experiment_run_id"] = experiment_run_id
    binding["case_index"] = index
    binding["case_id"] = case["case_id"]
    binding["request_id"] = case["request_id"]
    return binding


def _create_child_root(
    child_root: Path,
    *,
    child_run_id: str,
    case: dict[str, object],
    calls: dict[str, int] | None = None,
    with_terminal_success: bool = False,
    downstream_status: str = "first_pass_success",
    parent_binding: dict[str, object] | None = None,
) -> None:
    calls = calls or {node_id: 0 for node_id in runtime._fresh.NODE_ORDER}
    if parent_binding is None:
        case_suffix = child_run_id.rsplit("-case-", 1)
        experiment_run_id = case_suffix[0]
        case_index = int(case_suffix[1]) if len(case_suffix) == 2 else 1
        parent_binding = _case_parent_binding(
            experiment_run_id,
            case_index,
            case,
        )
    child_root.mkdir(parents=True, exist_ok=True)
    _write_json(child_root / "b_input.json", case)
    _write_json(child_root / "remote_profile.json", _PROFILE_RECORD)
    _write_json(
        child_root / "model_inventory.json",
        {"inventory_identity": _INVENTORY_IDENTITY},
    )
    _write_json(
        child_root / "preflight_manifest.json",
        {
            "run_id": child_run_id,
            "case_id": case["case_id"],
            "request_id": case["request_id"],
            "profile_identity": runtime._fresh._identity(
                _PROFILE_RECORD,
                revision=runtime._fresh.P4_05_PROFILE_SCHEMA_VERSION,
            ),
            "model_inventory_identity": _INVENTORY_IDENTITY,
            "parent_experiment_binding": parent_binding,
        },
    )
    for node_id, count in calls.items():
        if count == 0:
            continue
        attempt_root = child_root / "attempts" / node_id
        input_bytes = runtime._fresh._canonical_bytes(
            {"node_id": node_id, "kind": "input"}
        )
        prompt_bytes = runtime._fresh._canonical_bytes(
            {"node_id": node_id, "kind": "prompt"}
        )
        config_bytes = runtime._fresh._canonical_bytes(
            {"node_id": node_id, "kind": "config"}
        )
        request_bytes = runtime._fresh._canonical_bytes(
            {"node_id": node_id, "kind": "request"}
        )
        raw = runtime._fresh._canonical_bytes(
            {"node_id": node_id, "kind": "raw"}
        )
        pre_call = {
            "run_id": child_run_id,
            "case_id": case["case_id"],
            "request_id": case["request_id"],
            "parent_experiment_binding": parent_binding,
            "node_id": node_id,
            "profile_identity": runtime._fresh._identity(
                _PROFILE_RECORD,
                revision=runtime._fresh.P4_05_PROFILE_SCHEMA_VERSION,
            ),
            "input_identity": runtime._fresh._identity(
                input_bytes,
                revision=runtime._fresh.P4_05_INPUT_SCHEMA_VERSION,
                identity_kind="raw_bytes",
            ),
            "prompt_identity": runtime._fresh._identity(
                prompt_bytes,
                revision=runtime._fresh.P4_05_PROMPT_SCHEMA_VERSION,
                identity_kind="raw_bytes",
            ),
            "config_identity": runtime._fresh._identity(
                config_bytes,
                revision=f"{runtime._fresh.P4_05_SCHEMA_PREFIX}.config.v1",
                identity_kind="raw_bytes",
            ),
            "request_identity": runtime._fresh._identity(
                request_bytes,
                revision=f"{runtime._fresh.P4_05_SCHEMA_PREFIX}.request.v1",
                identity_kind="raw_bytes",
            ),
        }
        attempt = {
            "run_id": child_run_id,
            "case_id": case["case_id"],
            "request_id": case["request_id"],
            "parent_experiment_binding": parent_binding,
            "node_id": node_id,
            "call_count": count,
            "generate_started": True,
            "status": "validated",
            "failure_code": None,
            "retry_count": 0,
            "automatic_retry": False,
            "pre_call_identity": runtime._fresh._identity(
                pre_call,
                revision=runtime._fresh.P4_05_PRE_CALL_SCHEMA_VERSION,
            ),
            "input_identity": pre_call["input_identity"],
            "prompt_identity": pre_call["prompt_identity"],
            "config_identity": pre_call["config_identity"],
            "request_identity": pre_call["request_identity"],
            "raw_identity": runtime._fresh._identity(
                raw,
                revision=f"{runtime._fresh.P4_05_SCHEMA_PREFIX}.raw.v1",
                identity_kind="raw_bytes",
            ),
        }
        attempt_root.mkdir(parents=True, exist_ok=True)
        (attempt_root / "input.json").write_bytes(input_bytes)
        (attempt_root / "prompt.json").write_bytes(prompt_bytes)
        (attempt_root / "config.json").write_bytes(config_bytes)
        (attempt_root / "request.json").write_bytes(request_bytes)
        _write_json(attempt_root / "pre_call_record.json", pre_call)
        (attempt_root / "raw_response.bin").write_bytes(raw)
        _write_json(
            attempt_root / "validated_node_output.json",
            {"node_id": node_id, "validated": True},
        )
        _write_json(
            attempt_root / "attempt_result.json",
            attempt,
        )
    ledger = {
        "schema_version": runtime._fresh.P4_05_LEDGER_SCHEMA_VERSION,
        "pilot_id": runtime._fresh.P4_05_PILOT_ID,
        "run_id": child_run_id,
        "node_order": list(runtime._fresh.NODE_ORDER),
        "per_node": {
            node_id: {
                "attempt_count": int(calls[node_id] > 0),
                "generate_started_count": calls[node_id],
                "generate_call_cap": 1,
                "retry_count": 0,
            }
            for node_id in runtime._fresh.NODE_ORDER
        },
        "total_generate_calls": sum(calls.values()),
        "automatic_retry": False,
        "budget_reset": False,
    }
    _write_json(child_root / "model_call_ledger.json", ledger)
    aggregate_ledger = {
        "schema_version": runtime._fresh.P4_05_AGGREGATE_LEDGER_SCHEMA_VERSION,
        "pilot_id": runtime._fresh.P4_05_PILOT_ID,
        "run_id": child_run_id,
        "history_receipt_identity": {
            "identity_kind": "canonical_json",
            "sha256": "sha256:" + ("4" * 64),
            "byte_length": 404,
            "revision": runtime._fresh.P4_05_HISTORY_SCHEMA_VERSION,
        },
        "current_ledger_identity": runtime._fresh._identity(
            ledger,
            revision=runtime._fresh.P4_05_LEDGER_SCHEMA_VERSION,
        ),
        "per_node": {
            node_id: {
                "historical_generate_started_count": 0,
                "current_generate_started_count": calls[node_id],
                "aggregate_generate_started_count": calls[node_id],
                "total_real_model_call_cap": (
                    runtime._fresh.P4_05_TOTAL_REAL_MODEL_CALL_CAP_PER_NODE
                ),
            }
            for node_id in runtime._fresh.NODE_ORDER
        },
        "aggregate_total_generate_calls": sum(calls.values()),
        "automatic_retry": False,
        "budget_reset": False,
    }
    _write_json(
        child_root / "aggregate_model_call_ledger.json",
        aggregate_ledger,
    )
    any_calls = any(calls.values())
    supervisor = {
        "schema_version": runtime._fresh.P4_05_SUPERVISOR_SCHEMA_VERSION,
        "pilot_id": runtime._fresh.P4_05_PILOT_ID,
        "run_id": child_run_id,
        "parent_experiment_binding": parent_binding,
        "terminal_status": "normal_completed" if any_calls else "worker_not_started",
        "worker_id": "worker-test" if any_calls else None,
        "worker_pid": 1234 if any_calls else None,
        "worker_exit_code": 0 if any_calls else None,
        "worker_exit_verified": any_calls,
        "generation_started": any_calls,
        "generate_calls": dict(calls),
        "attempt_envelopes": {
            node_id: int(calls[node_id] > 0)
            for node_id in runtime._fresh.NODE_ORDER
        },
        "model_call_ledger_identity": runtime._fresh._identity(
            ledger,
            revision=runtime._fresh.P4_05_LEDGER_SCHEMA_VERSION,
        ),
        "aggregate_model_call_ledger_identity": runtime._fresh._identity(
            aggregate_ledger,
            revision=runtime._fresh.P4_05_AGGREGATE_LEDGER_SCHEMA_VERSION,
        ),
        "stdout_thread_joined": any_calls,
        "stderr_thread_joined": any_calls,
        "stderr_capture_completed": any_calls,
    }
    _write_json(child_root / "supervisor_receipt.json", supervisor)
    if not with_terminal_success:
        return

    normalization = {
        "raw_model_contract_success": True,
        "normalized_node_contract_success": True,
        "normalization_count": 0,
        "raw_failure_code": None,
    }
    _write_json(
        child_root / "attempts" / "F4" / "normalization_receipt.json",
        normalization,
    )
    _write_json(
        child_root / "core_f4_normalization_receipt.json",
        normalization,
    )
    candidate_bytes = runtime._fresh._canonical_bytes(
        {
            "title": f"Candidate for {case['case_id']}",
            "sections": [],
        }
    )
    candidate = {
        "candidate_projection_status": "candidate_composition_validated_only",
        "failure": None,
        "model_semantic_candidate_canonical_b64": (
            base64.b64encode(candidate_bytes).decode("ascii")
        ),
        "model_semantic_candidate_byte_length": len(candidate_bytes),
        "model_semantic_candidate_sha256": (
            "sha256:" + runtime._bare_sha256(candidate_bytes)
        ),
    }
    page_spec = {"page_id": f"page-{case['case_id']}"}
    assembly_report = {
        "report_id": f"report-{case['case_id']}",
        "candidate_sha256": runtime._bare_sha256(candidate_bytes),
        "assembled_page_spec_sha256": runtime._bare_sha256(
            runtime._fresh._canonical_bytes(page_spec)
        ),
    }
    source_result = {
        "run_id": child_run_id,
        "case_id": case["case_id"],
        "request_id": case["request_id"],
        "parent_experiment_binding": parent_binding,
        "status": "assembled",
        "raw_model_contract_success": True,
        "normalized_node_contract_success": True,
        "composition_status": "composed",
        "assembler_status": "assembled",
        "model_semantic_candidate_sha256": (
            "sha256:" + runtime._bare_sha256(candidate_bytes)
        ),
        "assembled_page_spec_sha256": assembly_report[
            "assembled_page_spec_sha256"
        ],
    }
    final_result = {
        **source_result,
        "status": "delivery_terminal_success",
    }
    repair_success = downstream_status == "recovered_success"
    fallback_success = downstream_status == "fallback_delivery"
    repair_attempted = downstream_status in {
        "recovered_success",
        "fallback_delivery",
    }
    downstream = {
        "status": downstream_status,
        "one_repair": (
            "recovered_success"
            if repair_success
            else (
                "repair_failed"
                if repair_attempted
                else "not_executed_first_pass_pass"
            )
        ),
        "same_case_g0_fallback": (
            "delivered"
            if fallback_success
            else (
                "not_executed_repair_success"
                if repair_success
                else "not_executed_first_pass_pass"
            )
        ),
    }
    delivery_receipt = {
        "case_id": case["case_id"],
        "request_id": case["request_id"],
        "downstream_outcome": {
            "status": downstream_status,
            "repair_attempted": int(repair_attempted),
            "repair_status": (
                "recovered_success"
                if repair_success
                else "repair_failed"
                if repair_attempted
                else "not_attempted"
            ),
            "fallback_attempted": fallback_success,
            "fallback_succeeded": fallback_success,
        },
        "downstream": downstream,
        "success_accounting": {
            "model_success": False,
            "repair_success": repair_success,
            "fallback_success": fallback_success,
            "delivery_success": True,
        },
    }
    final_result["delivery_receipt"] = delivery_receipt
    _write_json(child_root / "candidate_composition_record.json", candidate)
    _write_json(child_root / "assembled_page_spec.json", page_spec)
    _write_json(child_root / "assembly_report.json", assembly_report)
    _write_json(child_root / "revalidation_result.json", source_result)
    _write_json(child_root / "p4_05_final_result.json", final_result)
    _write_json(
        child_root / "delivery" / "phase4_fresh_delivery_receipt.json",
        delivery_receipt,
    )


def _prepared(
    result_root: Path,
    *,
    run_id: str,
    case_set: dict[str, object],
    parent_binding: dict[str, object] | None,
) -> dict[str, object]:
    policy_identity = dict(_PARENT_BINDING["experiment_policy_identity"])
    return {
        "result_root": result_root,
        "run_id": run_id,
        "case_set": case_set,
        "inventory": {"inventory_identity": _INVENTORY_IDENTITY},
        "profile": None,
        "policy": {
            "run_id": run_id,
            "profile_identity": _PROFILE_IDENTITY,
            "model_inventory_identity": _INVENTORY_IDENTITY,
            "parent_experiment_binding": parent_binding,
            "policy_identity": policy_identity,
        },
        "preflight": {},
        "summary": None,
    }


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
            "--resume-existing",
            "--f3-f4-revision",
            "--baseline-root",
            "--confirm-f3-f4-revision",
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

    def test_preflight_dispatches_resume_flag_without_model_run(self) -> None:
        result_root = Path("C:/p4-05-test-only/stability-preflight")
        prepared = {"run_id": "p4-05-test-preflight", "summary": None}
        stdout = io.StringIO()
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
                    "--resume-existing",
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
            resume_existing=True,
        )
        run.assert_not_called()
        self.assertIn("status=prepared_no_model", stdout.getvalue())

    def test_run_dispatches_and_prints_expanded_summary(self) -> None:
        result_root = Path("C:/p4-05-test-only/stability-run")
        summary = {
            "baseline_complete": True,
            "aggregate": {
                "case_count": 10,
                "total_model_generate_calls": 37,
                "f4_raw_direct_pass_count": 6,
                "f4_normalized_case_count": 2,
                "repair_attempted_count": 4,
                "repair_success_count": 3,
                "repair_failed_count": 1,
                "fallback_attempted_count": 2,
                "g0_fallback_count": 1,
                "delivery_success_count": 9,
                "failed_closed_count": 1,
            },
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(
            runtime,
            "run_phase4_remote_qwen_stability",
            return_value=summary,
        ) as run, patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = cli.main(
                self._common_args(result_root)
                + [
                    "--run-id",
                    "p4-05-test-run",
                    "--confirm-ten-case-baseline",
                    "--resume-existing",
                ]
            )
        self.assertEqual(code, 0)
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
                "resume_existing": True,
            },
        )
        output = stdout.getvalue()
        for fragment in (
            "status=baseline_complete",
            "completed_cases=10",
            "total_model_generate_calls=37",
            "f4_raw_direct_pass=6",
            "normalization=2",
            "repair_attempted=4",
            "repair_success=3",
            "repair_failed=1",
            "fallback_attempted=2",
            "fallback=1",
            "delivery=9",
            "failed_closed=1",
        ):
            self.assertIn(fragment, output)

    def test_revision_dispatches_with_baseline_binding_and_new_call_summary(self) -> None:
        result_root = Path("C:/p4-05-test-only/stability-revision")
        baseline_root = _ROOT
        summary = {
            "revision_complete": True,
            "aggregate": {
                "case_count": 10,
                "total_model_generate_calls": 20,
                "f4_raw_direct_pass_count": 8,
                "f4_normalized_case_count": 0,
                "repair_attempted_count": 2,
                "repair_success_count": 1,
                "repair_failed_count": 1,
                "fallback_attempted_count": 1,
                "g0_fallback_count": 1,
                "delivery_success_count": 10,
                "failed_closed_count": 0,
            },
            "new_model_generate_calls": 20,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(
            runtime,
            "run_phase4_remote_qwen_f3_f4_prompt_revision",
            return_value=summary,
        ) as run, patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = cli.main(
                self._common_args(result_root)
                + [
                    "--run-id",
                    "p4-05-test-revision",
                    "--f3-f4-revision",
                    "--baseline-root",
                    str(baseline_root),
                    "--confirm-f3-f4-revision",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            run.call_args.kwargs,
            {
                "model_root": _ROOT.resolve(strict=True),
                "integrity_evidence": (
                    _ROOT / "docs" / "project_memory.md"
                ).resolve(strict=True),
                "baseline_root": baseline_root.resolve(strict=True),
                "result_root": result_root.resolve(strict=False),
                "confirm_f3_f4_prompt_revision": True,
                "run_id": "p4-05-test-revision",
                "console": stderr,
                "resume_existing": False,
            },
        )
        output = stdout.getvalue()
        self.assertIn("status=revision_complete", output)
        self.assertIn("new_model_generate_calls=20", output)

    def test_fresh_runner_exception_is_reported_fail_closed(self) -> None:
        stderr = io.StringIO()
        with patch.object(
            runtime,
            "run_phase4_remote_qwen_stability",
            side_effect=runtime._fresh.Phase4RemoteFreshIntegratedError(
                "synthetic fresh-run failure"
            ),
        ), patch("sys.stderr", stderr):
            code = cli.main(
                self._common_args(
                    Path("C:/p4-05-test-only/stability-failure")
                )
                + ["--confirm-ten-case-baseline"]
            )
        self.assertEqual(code, 2)
        self.assertIn(
            "failed closed: synthetic fresh-run failure",
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
    def test_revision_baseline_binding_is_hash_bound_and_rejects_drift(self) -> None:
        baseline_summary = {
            "baseline_complete": True,
            "experiment_mode": "baseline",
            "case_set_identity": {"sha256": "case-set"},
            "policy_identity": {"sha256": "policy"},
            "summary_identity": {"sha256": "summary"},
            "profile_identity": _PROFILE_IDENTITY,
            "model_inventory_identity": _INVENTORY_IDENTITY,
            "aggregate": {
                "per_node_called_count": {
                    node_id: runtime.CASE_COUNT
                    for node_id in runtime._fresh.NODE_ORDER
                },
                "total_model_generate_calls": runtime.BASELINE_TOTAL_CALL_CAP,
            },
        }
        prepared = {
            "result_root": Path("C:/p4-05-test-only/baseline"),
            "run_id": "baseline-run",
            "case_set": {},
            "profile": _FakeProfile(),
            "summary": baseline_summary,
        }
        binding = runtime._build_revision_baseline_binding(
            baseline_prepared=prepared,
        )
        tampered = dict(binding)
        tampered["baseline_run_id"] = "other-run"
        with self.assertRaises(runtime.Phase4RemoteQwenStabilityError):
            runtime._validate_revision_baseline_binding(
                tampered,
                baseline_prepared=prepared,
            )

    def test_revision_profile_compatibility_ignores_only_volatile_gpu_binding(
        self,
    ) -> None:
        class Profile:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def validate(self) -> None:
                return None

            def to_dict(self) -> dict[str, object]:
                return dict(self.payload)

        baseline = {
            "profile_id": "baseline-profile-id",
            "device_uuid": "GPU-baseline",
            "free_vram_bytes_at_preflight": 30_000,
            "device_name": "NVIDIA GeForce RTX 5090",
            "total_vram_bytes": 34_190_917_632,
            "driver_version": "595.58.03",
            "dtype": "bfloat16",
            "quantization": "none",
            "model_revision": "model-revision",
        }
        restarted = {
            **baseline,
            "profile_id": "restarted-profile-id",
            "device_uuid": "GPU-reassigned-after-restart",
            "free_vram_bytes_at_preflight": 31_000,
        }
        self.assertEqual(
            runtime._revision_profile_compatibility_identity(
                Profile(baseline)
            ),
            runtime._revision_profile_compatibility_identity(
                Profile(restarted)
            ),
        )
        incompatible = {**restarted, "device_name": "Different GPU"}
        self.assertNotEqual(
            runtime._revision_profile_compatibility_identity(
                Profile(baseline)
            ),
            runtime._revision_profile_compatibility_identity(
                Profile(incompatible)
            ),
        )

    def test_revision_prepare_records_current_compatible_profile(self) -> None:
        case_set = get_stability_case_set()
        baseline_summary = {
            "baseline_complete": True,
            "experiment_mode": "baseline",
            "case_set_identity": case_set["case_set_identity"],
            "policy_identity": {"sha256": "policy"},
            "summary_identity": {"sha256": "summary"},
            "profile_identity": _PROFILE_IDENTITY,
            "model_inventory_identity": _INVENTORY_IDENTITY,
            "aggregate": {
                "per_node_called_count": {
                    node_id: runtime.CASE_COUNT
                    for node_id in runtime._fresh.NODE_ORDER
                },
                "total_model_generate_calls": runtime.BASELINE_TOTAL_CALL_CAP,
            },
        }
        baseline_prepared = {
            "result_root": _ROOT,
            "run_id": "baseline-run",
            "case_set": case_set,
            "inventory": {"inventory_identity": _INVENTORY_IDENTITY},
            "profile": _FakeProfile(),
            "summary": baseline_summary,
        }
        inventory = {"inventory_identity": _INVENTORY_IDENTITY}
        gpu = {
            "device_name": runtime._fresh._remote.REMOTE_DEVICE_NAME,
            "total_vram_bytes": runtime._fresh._remote.REMOTE_MIN_VRAM_BYTES,
        }
        with _WorkspaceTempDirectory() as temp:
            result_root = Path(temp) / "revision"
            with patch.object(
                runtime,
                "_load_existing_prepared",
                return_value=baseline_prepared,
            ), patch.object(
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
            ):
                prepared = (
                    runtime.prepare_phase4_remote_qwen_f3_f4_prompt_revision(
                        model_root=_ROOT,
                        integrity_evidence=(
                            _ROOT / "docs" / "project_memory.md"
                        ),
                        baseline_root=_ROOT,
                        result_root=result_root,
                        run_id="revision-prepare-test",
                    )
                )
        expected_compatibility = (
            runtime._revision_profile_compatibility_identity(_FakeProfile())
        )
        self.assertEqual(
            prepared["preflight"]["profile_compatibility_identity"],
            expected_compatibility,
        )
        self.assertEqual(
            prepared["baseline_binding"][
                "baseline_profile_compatibility_identity"
            ],
            expected_compatibility,
        )

    def test_prepare_freezes_case_set_and_no_model_policy(self) -> None:
        result_root = (
            _ROOT
            / ".p4-05-test-only"
            / "stability-preflight-runtime"
        ).resolve(strict=False)
        inventory = {"inventory_identity": _INVENTORY_IDENTITY}
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
        anchor = json.loads(
            written[runtime.STABILITY_EXPERIMENT_ANCHOR_NAME].decode("utf-8")
        )
        self.assertEqual(anchor["case_set_id"], runtime.CASE_SET_ID)
        self.assertEqual(anchor["run_id"], "p4-05-stability-test-preflight")

    def test_experiment_anchor_allows_only_same_root_and_run(self) -> None:
        case_set = get_stability_case_set()
        run_id = "anchor-test-run"
        policy_identity = dict(_PARENT_BINDING["experiment_policy_identity"])
        with _WorkspaceTempDirectory() as temp:
            shared_parent = Path(temp).resolve()
            result_root = shared_parent / "baseline-a"
            anchor = runtime._create_experiment_anchor(
                result_root=result_root,
                run_id=run_id,
                case_set_identity=case_set["case_set_identity"],
                policy_identity=policy_identity,
                profile_identity=_PROFILE_IDENTITY,
                model_inventory_identity=_INVENTORY_IDENTITY,
            )
            loaded = runtime._read_json(
                shared_parent / runtime.STABILITY_EXPERIMENT_ANCHOR_NAME,
                required=True,
            )
            assert loaded is not None
            self.assertEqual(
                runtime._validate_experiment_anchor(
                    loaded,
                    result_root=result_root,
                    run_id=run_id,
                    case_set_identity=case_set["case_set_identity"],
                    policy_identity=policy_identity,
                    profile_identity=_PROFILE_IDENTITY,
                    model_inventory_identity=_INVENTORY_IDENTITY,
                ),
                anchor,
            )
            with self.assertRaises(runtime.Phase4RemoteQwenStabilityError):
                runtime._validate_experiment_anchor(
                    loaded,
                    result_root=shared_parent / "baseline-b",
                    run_id=run_id,
                )
            with self.assertRaises(runtime.Phase4RemoteQwenStabilityError):
                runtime._validate_experiment_anchor(
                    loaded,
                    result_root=result_root,
                    run_id="another-run",
                )

            drifted = dict(loaded)
            drifted["profile_identity"] = {
                **_PROFILE_IDENTITY,
                "sha256": "sha256:" + ("9" * 64),
            }
            experiment_root = {
                key: drifted[key]
                for key in (
                    "case_set_id",
                    "case_set_identity",
                    "run_id",
                    "policy_identity",
                    "profile_identity",
                    "model_inventory_identity",
                    "result_root",
                )
            }
            drifted["experiment_identity"] = runtime._fresh._identity(
                experiment_root,
                revision=(
                    f"{runtime.STABILITY_EXPERIMENT_ANCHOR_SCHEMA_VERSION}"
                    ".experiment"
                ),
            )
            drifted_root = {
                key: value
                for key, value in drifted.items()
                if key != "anchor_identity"
            }
            drifted["anchor_identity"] = runtime._fresh._identity(
                drifted_root,
                revision=runtime.STABILITY_EXPERIMENT_ANCHOR_SCHEMA_VERSION,
            )
            with self.assertRaises(runtime.Phase4RemoteQwenStabilityError):
                runtime._validate_experiment_anchor(
                    drifted,
                    result_root=result_root,
                    run_id=run_id,
                    profile_identity=_PROFILE_IDENTITY,
                )

    def test_case_summary_uses_delivery_status_and_independent_artifacts(self) -> None:
        case = dict(get_stability_case_set()["cases"][0])
        experiment_run_id = "stability-summary-test"
        with _WorkspaceTempDirectory() as temp:
            result_root = Path(temp).resolve()
            child_root = runtime._expected_child_root(result_root, 1, case)
            parent_binding = _case_parent_binding(
                experiment_run_id,
                1,
                case,
            )
            _create_child_root(
                child_root,
                child_run_id=_child_run_id(experiment_run_id, 1),
                case=case,
                calls={node_id: 1 for node_id in runtime._fresh.NODE_ORDER},
                with_terminal_success=True,
                parent_binding=parent_binding,
            )
            result = runtime._summarize_case(
                experiment_run_id=experiment_run_id,
                index=1,
                case=case,
                child_root=child_root,
                child_result=None,
                exception=None,
                expected_profile_identity=_PROFILE_IDENTITY,
                expected_model_inventory_identity=_INVENTORY_IDENTITY,
                parent_experiment_binding=parent_binding,
            )
        self.assertIs(result["model_success"], False)
        self.assertIs(result["downstream_first_pass_success"], True)
        self.assertIs(result["composition_pass"], True)
        self.assertIs(result["assembler_pass"], True)
        self.assertIs(result["repair_attempted"], False)
        self.assertIs(result["system_adjustment_used"], False)

    def test_failed_fallback_attempt_is_authoritatively_counted(self) -> None:
        case = dict(get_stability_case_set()["cases"][0])
        experiment_run_id = "failed-fallback-test"
        parent_binding = _case_parent_binding(experiment_run_id, 1, case)
        with _WorkspaceTempDirectory() as temp:
            result_root = Path(temp).resolve()
            child_root = runtime._expected_child_root(result_root, 1, case)
            _create_child_root(
                child_root,
                child_run_id=_child_run_id(experiment_run_id, 1),
                case=case,
                calls={node_id: 1 for node_id in runtime._fresh.NODE_ORDER},
                with_terminal_success=True,
                parent_binding=parent_binding,
            )
            receipt_path = (
                child_root
                / "delivery"
                / "phase4_fresh_delivery_receipt.json"
            )
            receipt = runtime._read_json(receipt_path, required=True)
            assert receipt is not None
            receipt["downstream_outcome"] = {
                **dict(receipt["downstream_outcome"]),
                "status": "failed_delivery",
                "repair_attempted": 1,
                "repair_status": "repair_failed",
                "fallback_attempted": True,
                "fallback_succeeded": False,
            }
            receipt["downstream"] = {
                **dict(receipt["downstream"]),
                "status": "failed_delivery",
                "one_repair": "repair_failed",
                "same_case_g0_fallback": "not_executed",
            }
            receipt["success_accounting"] = {
                **dict(receipt["success_accounting"]),
                "repair_success": False,
                "fallback_success": False,
                "delivery_success": False,
            }
            _write_json(receipt_path, receipt)
            final_result = runtime._read_json(
                child_root / "p4_05_final_result.json",
                required=True,
            )
            assert final_result is not None
            final_result["status"] = "failed_closed"
            final_result["delivery_receipt"] = receipt
            _write_json(
                child_root / "p4_05_final_result.json",
                final_result,
            )
            result = runtime._summarize_case(
                experiment_run_id=experiment_run_id,
                index=1,
                case=case,
                child_root=child_root,
                child_result=None,
                exception=None,
                expected_profile_identity=_PROFILE_IDENTITY,
                expected_model_inventory_identity=_INVENTORY_IDENTITY,
                parent_experiment_binding=parent_binding,
            )
        self.assertIs(result["repair_attempted"], True)
        self.assertIs(result["repair_success"], False)
        self.assertIs(result["fallback_attempted"], True)
        self.assertIs(result["g0_fallback_success"], False)
        self.assertIs(result["delivery_success"], False)

    def test_delivery_accounting_rejects_display_or_success_drift(self) -> None:
        case = dict(get_stability_case_set()["cases"][0])
        experiment_run_id = "delivery-accounting-drift-test"
        parent_binding = _case_parent_binding(experiment_run_id, 1, case)
        with _WorkspaceTempDirectory() as temp:
            result_root = Path(temp).resolve()
            child_root = runtime._expected_child_root(result_root, 1, case)
            _create_child_root(
                child_root,
                child_run_id=_child_run_id(experiment_run_id, 1),
                case=case,
                with_terminal_success=True,
                parent_binding=parent_binding,
            )
            receipt_path = (
                child_root
                / "delivery"
                / "phase4_fresh_delivery_receipt.json"
            )
            receipt = runtime._read_json(receipt_path, required=True)
            assert receipt is not None
            receipt["success_accounting"] = {
                **dict(receipt["success_accounting"]),
                "repair_success": True,
            }
            _write_json(receipt_path, receipt)
            with self.assertRaises(runtime.Phase4RemoteQwenStabilityError):
                runtime._summarize_case(
                    experiment_run_id=experiment_run_id,
                    index=1,
                    case=case,
                    child_root=child_root,
                    child_result=None,
                    exception=None,
                    expected_profile_identity=_PROFILE_IDENTITY,
                    expected_model_inventory_identity=_INVENTORY_IDENTITY,
                    parent_experiment_binding=parent_binding,
                )

    def test_aggregate_separates_attempt_success_failure_and_adjustment(self) -> None:
        case_result = {
            "status": "delivery_terminal_success",
            "per_node_generate_calls": {
                node_id: 1 for node_id in runtime._fresh.NODE_ORDER
            },
            "per_node_raw_contract_pass": {
                node_id: True for node_id in runtime._fresh.NODE_ORDER
            },
            "per_node_failure_codes": {
                node_id: None for node_id in runtime._fresh.NODE_ORDER
            },
            "f4_called": True,
            "f4_raw_direct_pass": True,
            "f4_normalization_used": False,
            "model_success": False,
            "composition_pass": True,
            "assembler_pass": True,
            "downstream_first_pass_success": False,
            "repair_attempted": True,
            "repair_success": False,
            "repair_failed": True,
            "fallback_attempted": True,
            "g0_fallback_success": True,
            "delivery_success": True,
            "system_adjustment_used": True,
            "total_model_generate_calls": 4,
            "failure_code": None,
        }
        aggregate = runtime._aggregate([case_result] * 10)
        self.assertEqual(aggregate["total_model_generate_calls"], 40)
        self.assertEqual(aggregate["model_success_count"], 0)
        self.assertEqual(aggregate["repair_attempted_count"], 10)
        self.assertEqual(aggregate["repair_success_count"], 0)
        self.assertEqual(aggregate["repair_failed_count"], 10)
        self.assertEqual(aggregate["fallback_attempted_count"], 10)
        self.assertEqual(aggregate["g0_fallback_count"], 10)
        self.assertEqual(aggregate["system_adjustment_rate"], 1.0)

    def test_revision_aggregate_keeps_new_and_historical_call_counts(self) -> None:
        case_result = {
            "status": "delivery_terminal_success",
            "per_node_generate_calls": {
                "F1": 0,
                "F2": 0,
                "F3": 1,
                "F4": 1,
            },
            "per_node_raw_contract_pass": {
                node_id: True for node_id in runtime._fresh.NODE_ORDER
            },
            "per_node_failure_codes": {
                node_id: None for node_id in runtime._fresh.NODE_ORDER
            },
            "historical_per_node_generate_calls": {
                node_id: 1 for node_id in runtime._fresh.NODE_ORDER
            },
            "aggregate_per_node_generate_calls": {
                "F1": 1,
                "F2": 1,
                "F3": 2,
                "F4": 2,
            },
            "f4_called": True,
            "f4_raw_direct_pass": True,
            "f4_normalization_used": False,
            "model_success": True,
            "composition_pass": True,
            "assembler_pass": True,
            "downstream_first_pass_success": True,
            "repair_attempted": False,
            "repair_success": False,
            "repair_failed": False,
            "deterministic_repair_success": False,
            "fallback_attempted": False,
            "g0_fallback_success": False,
            "delivery_success": True,
            "system_adjustment_used": False,
            "total_model_generate_calls": 2,
            "failure_code": None,
        }
        aggregate = runtime._aggregate([case_result] * 10)
        self.assertEqual(aggregate["total_model_generate_calls"], 20)
        self.assertEqual(
            aggregate["historical_per_node_generate_started_count"],
            {"F1": 10, "F2": 10, "F3": 10, "F4": 10},
        )
        self.assertEqual(
            aggregate["aggregate_per_node_generate_started_count"],
            {"F1": 10, "F2": 10, "F3": 20, "F4": 20},
        )
        self.assertEqual(aggregate["aggregate_total_model_generate_calls"], 60)

    def test_resume_skips_progress_and_seals_existing_child_without_progress(
        self,
    ) -> None:
        case_set = get_stability_case_set()
        run_id = "stability-test-run"
        with _WorkspaceTempDirectory() as temp:
            result_root = Path(temp).resolve()
            cases = case_set["cases"]
            case1 = dict(cases[0])
            case2 = dict(cases[1])
            parent1 = _case_parent_binding(run_id, 1, case1)
            child1 = runtime._expected_child_root(result_root, 1, case1)
            _create_child_root(
                child1,
                child_run_id=_child_run_id(run_id, 1),
                case=case1,
                parent_binding=parent1,
            )
            result1 = runtime._summarize_case(
                experiment_run_id=run_id,
                index=1,
                case=case1,
                child_root=child1,
                child_result=None,
                exception=None,
                expected_profile_identity=_PROFILE_IDENTITY,
                expected_model_inventory_identity=_INVENTORY_IDENTITY,
                parent_experiment_binding=parent1,
            )
            runtime._write_progress(
                result_root=result_root,
                experiment_run_id=run_id,
                index=1,
                case=case1,
                case_result=result1,
                aggregate_so_far=runtime._aggregate([result1]),
            )
            child2 = runtime._expected_child_root(result_root, 2, case2)
            parent2 = _case_parent_binding(run_id, 2, case2)
            _create_child_root(
                child2,
                child_run_id=_child_run_id(run_id, 2),
                case=case2,
                parent_binding=parent2,
            )
            prepared = _prepared(
                result_root,
                run_id=run_id,
                case_set=case_set,
                parent_binding=None,
            )

            def fake_single_case(**kwargs: object) -> dict[str, object]:
                child_case = dict(kwargs["b_input"])
                _create_child_root(
                    Path(kwargs["result_root"]),
                    child_run_id=str(kwargs["run_id"]),
                    case=child_case,
                    parent_binding=dict(kwargs["parent_experiment_binding"]),
                )
                return {
                    "run_id": kwargs["run_id"],
                    "case_id": child_case["case_id"],
                    "request_id": child_case["request_id"],
                    "status": "failed_closed",
                    "parent_experiment_binding": kwargs[
                        "parent_experiment_binding"
                    ],
                }

            with patch.object(
                runtime,
                "prepare_phase4_remote_qwen_stability",
                return_value=prepared,
            ), patch.object(
                runtime._fresh,
                "run_phase4_remote_qwen_fresh_integrated",
                side_effect=fake_single_case,
            ) as single_case:
                summary = runtime.run_phase4_remote_qwen_stability(
                    model_root=_ROOT,
                    integrity_evidence=_ROOT / "docs" / "project_memory.md",
                    result_root=result_root,
                    confirm_ten_case_baseline=True,
                    run_id=run_id,
                    resume_existing=True,
                    expected_profile_identity=_PROFILE_IDENTITY,
                    expected_model_inventory_identity=_INVENTORY_IDENTITY,
                )
            self.assertEqual(single_case.call_count, 8)
            self.assertEqual(summary["aggregate"]["case_count"], 10)
            self.assertEqual(summary["aggregate"]["total_model_generate_calls"], 0)
            self.assertEqual(summary["case_results"][1]["status"], "failed_closed")
            for call in single_case.call_args_list:
                self.assertEqual(
                    call.kwargs["expected_profile_identity"],
                    _PROFILE_IDENTITY,
                )
                self.assertEqual(
                    call.kwargs["expected_model_inventory_identity"],
                    _INVENTORY_IDENTITY,
                )
                self.assertIn("parent_experiment_binding", call.kwargs)

            completed = dict(prepared)
            completed["summary"] = summary
            with patch.object(
                runtime,
                "prepare_phase4_remote_qwen_stability",
                return_value=completed,
            ), patch.object(
                runtime._fresh,
                "run_phase4_remote_qwen_fresh_integrated",
            ) as zero_call:
                returned = runtime.run_phase4_remote_qwen_stability(
                    model_root=_ROOT,
                    integrity_evidence=_ROOT / "docs" / "project_memory.md",
                    result_root=result_root,
                    confirm_ten_case_baseline=True,
                    run_id=run_id,
                    resume_existing=True,
                )
            self.assertEqual(returned, summary)
            zero_call.assert_not_called()

    def test_existing_child_without_progress_requires_complete_terminal_evidence(
        self,
    ) -> None:
        case_set = get_stability_case_set()
        case = dict(case_set["cases"][0])
        run_id = "incomplete-existing-child-test"
        scenarios = (
            "missing_ledger",
            "missing_supervisor",
            "missing_attempt_result",
            "unexplained_raw_trace",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario), _WorkspaceTempDirectory() as temp:
                result_root = Path(temp).resolve()
                child_root = runtime._expected_child_root(result_root, 1, case)
                parent_binding = _case_parent_binding(run_id, 1, case)
                calls = {
                    node_id: int(
                        scenario == "missing_attempt_result"
                        and node_id == "F1"
                    )
                    for node_id in runtime._fresh.NODE_ORDER
                }
                _create_child_root(
                    child_root,
                    child_run_id=_child_run_id(run_id, 1),
                    case=case,
                    calls=calls,
                    parent_binding=parent_binding,
                )
                if scenario == "missing_ledger":
                    (child_root / "model_call_ledger.json").unlink()
                elif scenario == "missing_supervisor":
                    (child_root / "supervisor_receipt.json").unlink()
                elif scenario == "missing_attempt_result":
                    (
                        child_root
                        / "attempts"
                        / "F1"
                        / "attempt_result.json"
                    ).unlink()
                else:
                    trace_root = child_root / "attempts" / "F1"
                    trace_root.mkdir(parents=True, exist_ok=True)
                    (trace_root / "raw_response.bin").write_bytes(b"raw-trace")
                prepared = _prepared(
                    result_root,
                    run_id=run_id,
                    case_set=case_set,
                    parent_binding=None,
                )
                with patch.object(
                    runtime,
                    "prepare_phase4_remote_qwen_stability",
                    return_value=prepared,
                ), patch.object(
                    runtime._fresh,
                    "run_phase4_remote_qwen_fresh_integrated",
                ) as single_case:
                    with self.assertRaises(
                        runtime.Phase4RemoteQwenStabilityError
                    ):
                        runtime.run_phase4_remote_qwen_stability(
                            model_root=_ROOT,
                            integrity_evidence=(
                                _ROOT / "docs" / "project_memory.md"
                            ),
                            result_root=result_root,
                            confirm_ten_case_baseline=True,
                            run_id=run_id,
                            resume_existing=True,
                            expected_profile_identity=_PROFILE_IDENTITY,
                            expected_model_inventory_identity=(
                                _INVENTORY_IDENTITY
                            ),
                        )
                single_case.assert_not_called()
                self.assertFalse((result_root / "progress" / "01.json").exists())

    def test_child_rejects_b_input_preflight_and_attempt_binding_drift(self) -> None:
        case_set = get_stability_case_set()
        case1 = dict(case_set["cases"][0])
        case2 = dict(case_set["cases"][1])
        run_id = "child-binding-drift-test"
        scenarios = ("b_input", "preflight", "attempt")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), _WorkspaceTempDirectory() as temp:
                result_root = Path(temp).resolve()
                child_root = runtime._expected_child_root(result_root, 1, case1)
                parent_binding = _case_parent_binding(run_id, 1, case1)
                _create_child_root(
                    child_root,
                    child_run_id=_child_run_id(run_id, 1),
                    case=case1,
                    calls={
                        node_id: int(node_id == "F1")
                        for node_id in runtime._fresh.NODE_ORDER
                    },
                    parent_binding=parent_binding,
                )
                if scenario == "b_input":
                    _write_json(child_root / "b_input.json", case2)
                elif scenario == "preflight":
                    preflight = runtime._read_json(
                        child_root / "preflight_manifest.json",
                        required=True,
                    )
                    assert preflight is not None
                    preflight.pop("parent_experiment_binding")
                    _write_json(
                        child_root / "preflight_manifest.json",
                        preflight,
                    )
                else:
                    attempt_path = (
                        child_root
                        / "attempts"
                        / "F1"
                        / "attempt_result.json"
                    )
                    attempt = runtime._read_json(attempt_path, required=True)
                    assert attempt is not None
                    attempt.pop("request_id")
                    _write_json(attempt_path, attempt)
                with self.assertRaises(runtime.Phase4RemoteQwenStabilityError):
                    runtime._summarize_case(
                        experiment_run_id=run_id,
                        index=1,
                        case=case1,
                        child_root=child_root,
                        child_result=None,
                        exception=None,
                        expected_profile_identity=_PROFILE_IDENTITY,
                        expected_model_inventory_identity=_INVENTORY_IDENTITY,
                        parent_experiment_binding=parent_binding,
                    )

    def test_assembler_pass_is_independent_of_final_delivery(self) -> None:
        case = dict(get_stability_case_set()["cases"][0])
        run_id = "assembler-without-final-test"
        parent_binding = _case_parent_binding(run_id, 1, case)
        with _WorkspaceTempDirectory() as temp:
            result_root = Path(temp).resolve()
            child_root = runtime._expected_child_root(result_root, 1, case)
            _create_child_root(
                child_root,
                child_run_id=_child_run_id(run_id, 1),
                case=case,
                calls={node_id: 1 for node_id in runtime._fresh.NODE_ORDER},
                with_terminal_success=True,
                parent_binding=parent_binding,
            )
            (child_root / "p4_05_final_result.json").unlink()
            result = runtime._summarize_case(
                experiment_run_id=run_id,
                index=1,
                case=case,
                child_root=child_root,
                child_result=None,
                exception=None,
                expected_profile_identity=_PROFILE_IDENTITY,
                expected_model_inventory_identity=_INVENTORY_IDENTITY,
                parent_experiment_binding=parent_binding,
            )
        self.assertIs(result["composition_pass"], True)
        self.assertIs(result["assembler_pass"], True)
        self.assertIs(result["delivery_success"], True)

    def test_assembler_rejects_candidate_or_page_spec_hash_drift(self) -> None:
        case = dict(get_stability_case_set()["cases"][0])
        run_id = "assembler-hash-drift-test"
        parent_binding = _case_parent_binding(run_id, 1, case)
        with _WorkspaceTempDirectory() as temp:
            result_root = Path(temp).resolve()
            child_root = runtime._expected_child_root(result_root, 1, case)
            _create_child_root(
                child_root,
                child_run_id=_child_run_id(run_id, 1),
                case=case,
                with_terminal_success=True,
                parent_binding=parent_binding,
            )
            report_path = child_root / "assembly_report.json"
            report = runtime._read_json(report_path, required=True)
            assert report is not None
            report["assembled_page_spec_sha256"] = "0" * 64
            _write_json(report_path, report)
            with self.assertRaises(runtime.Phase4RemoteQwenStabilityError):
                runtime._summarize_case(
                    experiment_run_id=run_id,
                    index=1,
                    case=case,
                    child_root=child_root,
                    child_result=None,
                    exception=None,
                    expected_profile_identity=_PROFILE_IDENTITY,
                    expected_model_inventory_identity=_INVENTORY_IDENTITY,
                    parent_experiment_binding=parent_binding,
                )

    def test_case_result_rejects_cross_case_binding(self) -> None:
        case_set = get_stability_case_set()
        case1 = dict(case_set["cases"][0])
        case2 = dict(case_set["cases"][1])
        run_id = "cross-case-test"
        with _WorkspaceTempDirectory() as temp:
            result_root = Path(temp).resolve()
            child_root = runtime._expected_child_root(result_root, 1, case1)
            parent_binding = _case_parent_binding(run_id, 1, case1)
            _create_child_root(
                child_root,
                child_run_id=_child_run_id(run_id, 1),
                case=case1,
                parent_binding=parent_binding,
            )
            case_result = runtime._summarize_case(
                experiment_run_id=run_id,
                index=1,
                case=case1,
                child_root=child_root,
                child_result=None,
                exception=None,
                expected_profile_identity=_PROFILE_IDENTITY,
                expected_model_inventory_identity=_INVENTORY_IDENTITY,
                parent_experiment_binding=parent_binding,
            )
            identity_root = {
                key: value
                for key, value in case_result.items()
                if key != "case_result_identity"
            }
            identity_root["case_id"] = case2["case_id"]
            bad = {
                **identity_root,
                "case_result_identity": runtime._fresh._identity(
                    identity_root,
                    revision=runtime.STABILITY_CASE_RESULT_SCHEMA_VERSION,
                ),
            }
            with self.assertRaises(runtime.Phase4RemoteQwenStabilityError):
                runtime._validate_case_result(
                    bad,
                    experiment_run_id=run_id,
                    index=1,
                    case=case1,
                    result_root=result_root,
                    expected_profile_identity=_PROFILE_IDENTITY,
                    expected_model_inventory_identity=_INVENTORY_IDENTITY,
                    parent_experiment_binding=parent_binding,
                )

    def test_unknown_child_exception_is_not_swallowed(self) -> None:
        case_set = get_stability_case_set()
        run_id = "infrastructure-error-test"
        with _WorkspaceTempDirectory() as temp:
            result_root = Path(temp).resolve()
            prepared = _prepared(
                result_root,
                run_id=run_id,
                case_set=case_set,
                parent_binding=None,
            )
            with patch.object(
                runtime,
                "prepare_phase4_remote_qwen_stability",
                return_value=prepared,
            ), patch.object(
                runtime._fresh,
                "run_phase4_remote_qwen_fresh_integrated",
                side_effect=RuntimeError("synthetic infrastructure failure"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "synthetic infrastructure failure",
                ):
                    runtime.run_phase4_remote_qwen_stability(
                        model_root=_ROOT,
                        integrity_evidence=_ROOT / "docs" / "project_memory.md",
                        result_root=result_root,
                        confirm_ten_case_baseline=True,
                        run_id=run_id,
                    )
            self.assertFalse((result_root / "stability_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
