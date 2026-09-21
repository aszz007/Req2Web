from __future__ import annotations

import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import phase7_e2_diagnostic_v3_recovery_runtime as recovery
import phase7_e2_supervise as supervisor


WORKSPACE_TEMP = (
    ROOT / "outputs" / "phase7_e2_diagnostic_v3_recovery" / "runtime_tests"
)
WORKSPACE_TEMP.mkdir(parents=True, exist_ok=True)


class WorkspaceDirectory:
    def __enter__(self):
        self.path = WORKSPACE_TEMP / ("case_" + uuid.uuid4().hex)
        self.path.mkdir()
        return self.path

    def __exit__(self, exc_type, exc, tb):
        target = self.path.resolve(strict=True)
        root = WORKSPACE_TEMP.resolve(strict=True)
        if target.parent != root or not target.name.startswith("case_"):
            raise RuntimeError("refusing unsafe recovery-test cleanup target")
        shutil.rmtree(target)


def canonical(value: object) -> bytes:
    return recovery.legacy.canonical_bytes(value)


def make_prior_failure(root: Path) -> tuple[dict[str, object], list[dict[str, str]]]:
    public = root / "e2-v3" / "public"
    public.mkdir(parents=True)
    (public / "manifest.json").write_bytes(b"{}")
    integrity = root / "model_integrity.json"
    integrity.write_bytes(b"{}")
    model = root / "model"
    model.mkdir()
    manifest_sha = recovery.legacy.sha256_bytes(b"{}")
    caps = {
        "reads": 5,
        "input_tokens": 12000,
        "output_tokens": 2048,
        "provider_turns": 6,
        "session_seconds": 120,
    }
    prior_action = {
        "schema_version": recovery.v3.ACTION_SCHEMA,
        "approved": True,
        "model_root": str(model),
        "model_identity": recovery.legacy.QWEN_MODEL_ID,
        "model_revision": recovery.legacy.QWEN_MODEL_REVISION,
        "integrity_evidence": str(integrity),
        "public_root": str(public),
        "public_manifest_sha256": manifest_sha,
        "output_root": str(root / "failed-output"),
        "state_root": str(public.parent / "runtime_state_v3"),
        "split": "development",
        "caps": caps,
        "accepted_development": None,
    }
    action_path = root / "prior-action.json"
    action_path.write_bytes(canonical(prior_action))
    prior_freeze = recovery.v3.freeze_identity(prior_action, manifest_sha)
    claim_path = (
        public.parent
        / "runtime_state_v3"
        / "claims"
        / manifest_sha
        / "development"
        / "claim.json"
    )
    claim_path.parent.mkdir(parents=True)
    claim_path.write_bytes(
        canonical(
            {
                "public_manifest_sha256": manifest_sha,
                "freeze_identity": prior_freeze,
                "split": "development",
                "output_root": str(Path(prior_action["output_root"]).resolve()),
            }
        )
    )
    schedule = [
        {"session_id": f"d{index:03d}", "packet_id": f"p{packet:03d}", "arm": arm}
        for index, (packet, arm) in enumerate(
            ((1, "B"), (1, "A"), (2, "B"), (2, "A")), start=1
        )
    ]
    sessions = [
        {
            **row,
            "status": "not_started_after_interruption",
            "answer": None,
            "reads": None,
            "provider_turns": 0,
            "input_tokens": 0,
            "output_tokens": None,
            "raw_refs": [],
            "reason": "worker_exit_2_without_summary",
        }
        for row in schedule
    ]
    interrupted_path = root / "interrupted_summary.json"
    interrupted_path.write_bytes(
        canonical(
            {
                "schema_version": recovery.v3.RESULT_SCHEMA,
                "split": "development",
                "public_manifest_sha256": manifest_sha,
                "fatal": "worker_exit_2_without_summary",
                "sessions": sessions,
                "scope": "watchdog reconstruction; unknown output token counts are not zero",
            }
        )
    )
    worker_log = root / "worker.log"
    worker_log.write_text(recovery.EXPECTED_PRIOR_ERROR + "\n", encoding="utf-8")
    current = {
        **prior_action,
        "schema_version": recovery.ACTION_SCHEMA,
        "output_root": str(root / "recovery-output"),
        "state_root": str(public.parent / "runtime_state_v3_recovery1"),
        "runtime_environment": {
            "python_executable": sys.executable,
            "torch_version": recovery.EXPECTED_TORCH_VERSION,
            "transformers_version": recovery.EXPECTED_TRANSFORMERS_VERSION,
            "cuda_required": True,
            "gpu_name_substring": "RTX 5090",
        },
        "prior_failed_attempt": {
            "action_config": str(action_path),
            "claim": str(claim_path),
            "interrupted_summary": str(interrupted_path),
            "worker_log": str(worker_log),
        },
    }
    return current, schedule


class Phase7E2DiagnosticV3RecoveryTests(unittest.TestCase):
    def test_prior_failure_requires_zero_started_model_sessions(self) -> None:
        with WorkspaceDirectory() as root:
            config, schedule = make_prior_failure(root)
            with patch.object(
                recovery.candidate,
                "validate_public",
                return_value={
                    "schema_version": recovery.candidate.PUBLIC_SCHEMA,
                    "budgets": recovery.candidate.BUDGETS,
                    "schedule": {"development": schedule},
                },
            ):
                receipt = recovery.validate_prior_failed_attempt(config)
                self.assertRegex(receipt["identity"], r"^[0-9a-f]{64}$")

                interrupted_path = Path(
                    config["prior_failed_attempt"]["interrupted_summary"]
                )
                interrupted = json.loads(interrupted_path.read_bytes())
                interrupted["sessions"][0]["provider_turns"] = 1
                interrupted_path.write_bytes(canonical(interrupted))
                with self.assertRaisesRegex(
                    recovery.legacy.RuntimeErrorClosed, "started or ambiguous"
                ):
                    recovery.validate_prior_failed_attempt(config)

    def test_recovery_config_uses_a_new_single_claim_namespace(self) -> None:
        with WorkspaceDirectory() as root:
            config, _ = make_prior_failure(root)
            path = root / "recovery-action.json"
            path.write_bytes(canonical(config))
            loaded = recovery.load_config(path)
            self.assertEqual(recovery.ACTION_SCHEMA, loaded["schema_version"])
            self.assertTrue(loaded["state_root"].endswith("runtime_state_v3_recovery1"))

            config["state_root"] = str(Path(config["public_root"]).parent / "runtime_state_v3")
            path.write_bytes(canonical(config))
            with self.assertRaisesRegex(
                recovery.legacy.RuntimeErrorClosed, "runtime_state_v3_recovery1"
            ):
                recovery.load_config(path)

    def test_environment_check_precedes_claim_and_binds_cuda_stack(self) -> None:
        config = {
            "runtime_environment": {
                "python_executable": sys.executable,
                "torch_version": recovery.EXPECTED_TORCH_VERSION,
                "transformers_version": recovery.EXPECTED_TRANSFORMERS_VERSION,
                "cuda_required": True,
                "gpu_name_substring": "RTX 5090",
            }
        }
        fake_cuda = SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_name=lambda _: "NVIDIA GeForce RTX 5090",
        )
        fake_torch = SimpleNamespace(
            __version__=recovery.EXPECTED_TORCH_VERSION, cuda=fake_cuda
        )
        fake_transformers = SimpleNamespace(
            __version__=recovery.EXPECTED_TRANSFORMERS_VERSION
        )
        with patch.dict(
            sys.modules,
            {"torch": fake_torch, "transformers": fake_transformers},
        ):
            receipt = recovery.validate_runtime_environment(config)
        self.assertTrue(receipt["cuda_available"])
        self.assertEqual("NVIDIA GeForce RTX 5090", receipt["cuda_device_name"])

        config["runtime_environment"]["python_executable"] = str(
            Path(sys.executable).with_name("wrong-python")
        )
        with self.assertRaises((FileNotFoundError, recovery.legacy.RuntimeErrorClosed)):
            recovery.validate_runtime_environment(config)

    def test_supervisor_uses_bound_worker_interpreter_for_recovery(self) -> None:
        config_path = ROOT / "fixtures" / "phase7_e2_action_v3_recovery1.template.json"
        command = supervisor.worker_command(
            {
                "schema_version": recovery.ACTION_SCHEMA,
                "runtime_environment": {"python_executable": sys.executable},
            },
            config_path,
        )
        self.assertEqual(str(Path(sys.executable).resolve()), command[0])
        self.assertTrue(command[1].endswith("phase7_e2_diagnostic_v3_recovery_runtime.py"))
        self.assertEqual("run", command[2])


if __name__ == "__main__":
    unittest.main()
