from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import phase7_e2_diagnostic_v4_runtime as runtime
import phase7_e2_diagnostic_v4_score as scorer
import phase7_e2_supervise as supervisor
import phase7_e2_trace_navigation_acceptance_v4 as acceptance


WORKSPACE_TEMP = ROOT / "outputs" / "phase7_e2_diagnostic_v4" / "runtime_tests"
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
            raise RuntimeError("refusing unsafe v4-test cleanup target")
        shutil.rmtree(target)


class SequenceBackend:
    def __init__(self, actions: list[dict[str, object]]):
        self.actions = list(actions)

    def generate(self, messages, *, max_new_tokens, remaining_input_tokens, deadline, started, chunk):
        action = self.actions.pop(0)
        raw = runtime.legacy.canonical_bytes(action)
        started(
            {
                "input_tokens": 1,
                "formatted_prompt": json.dumps(messages, sort_keys=True),
                "protocol_revision": runtime.PROTOCOL_REVISION,
            }
        )
        chunk(raw)
        return {"raw": raw, "input_tokens": 1, "output_tokens": 1}


def make_packet(root: Path) -> dict[str, object]:
    packet_root = root / "packets" / "p002"
    packet_root.mkdir(parents=True)
    values = {
        "readme.json": {"task": "diagnose"},
        "handoff.json": {"links": [{"use_case_id": "UC-01"}]},
        "specification.json": {"sections": [{"use_case_ids": ["UC-01"]}]},
        "trace_index.json": {
            "kind": runtime.protocol.TRACE_INDEX_KIND,
            "access": "lookup_only_by_exact_entity_pointer",
            "entities": {
                "UC-01": [
                    {"artifact": "handoff.json", "pointer": "/links/0/use_case_id"},
                    {"artifact": "specification.json", "pointer": "/sections/0/use_case_ids/0"},
                ]
            },
            "meaning": "source occurrences only",
        },
    }
    files = []
    for name, value in values.items():
        raw = runtime.legacy.canonical_bytes(value)
        (packet_root / name).write_bytes(raw)
        files.append({"path": name, "sha256": runtime.legacy.sha256_bytes(raw), "bytes": len(raw)})
    return {"packet_id": "p002", "files": files}


def caps() -> dict[str, int]:
    return {key: runtime.candidate.BUDGETS[key] for key in runtime.RUNTIME_CAP_KEYS}


class Phase7E2DiagnosticV4RuntimeTests(unittest.TestCase):
    def test_b_session_preserves_grounded_model_action_and_canonical_final(self) -> None:
        with WorkspaceDirectory() as work:
            public = work / "public"
            packet = make_packet(public)
            out = work / "result"
            backend = SequenceBackend(
                [
                    {"action": "read", "artifact": "readme.json", "pointer": ""},
                    {"action": "read", "artifact": "handoff.json", "pointer": ""},
                    {"action": "trace_lookup", "entity_id": "UC-01"},
                    {
                        "action": "final",
                        "status": "fault",
                        "origins": ["handoff.json::/links/0/use_case_id"],
                        "use_cases": ["UC-01"],
                        "edges": [
                            "handoff.json::/links/0/use_case_id=>specification.json::/sections/0/use_case_ids"
                        ],
                        "uncertainty": "low",
                    },
                ]
            )
            row = {"session_id": "d001", "packet_id": "p002", "arm": "B"}
            result = runtime._run_session_v4(
                row=row,
                packet=packet,
                root=public,
                out=out,
                backend=backend,
                caps=caps(),
                freeze="0" * 64,
                active_path=out / "active.json",
            )
            self.assertEqual(result["status"], "fault")
            self.assertEqual(result["reads"], 3)
            self.assertEqual(result["provider_turns"], 4)
            receipt = json.loads((out / "d001" / "turn_03.tool_read.json").read_bytes())
            self.assertEqual(receipt["model_action"], {"action": "trace_lookup", "entity_id": "UC-01"})
            self.assertEqual(receipt["request"]["pointer"], "/entities/UC-01")
            audit = acceptance.audit_tool_reads(
                [
                    {**json.loads((out / "d001" / "turn_01.tool_read.json").read_bytes()), "turn": 1},
                    {**json.loads((out / "d001" / "turn_02.tool_read.json").read_bytes()), "turn": 2},
                    {**receipt, "turn": 3},
                ]
            )
            self.assertEqual(audit["grounded_exact_index_reads"], 1)
            inventory = {item["path"] for item in packet["files"]}
            self.assertEqual(
                scorer.verify_raw_answer(result, out, inventory=inventory),
                result["answer"],
            )

    def test_direct_trace_index_read_fails_without_a_tool_receipt(self) -> None:
        with WorkspaceDirectory() as work:
            public = work / "public"
            packet = make_packet(public)
            out = work / "result"
            result = runtime._run_session_v4(
                row={"session_id": "d001", "packet_id": "p002", "arm": "B"},
                packet=packet,
                root=public,
                out=out,
                backend=SequenceBackend(
                    [{"action": "read", "artifact": "trace_index.json", "pointer": ""}]
                ),
                caps=caps(),
                freeze="0" * 64,
                active_path=out / "active.json",
            )
            self.assertEqual(result["status"], "invalid_response")
            self.assertEqual(result["reads"], 0)
            self.assertIn("direct trace-index reads", result["protocol_error"])
            self.assertFalse((out / "d001" / "turn_01.tool_read.json").exists())

    def test_v4_acceptance_rejects_an_index_receipt_without_model_action(self) -> None:
        records = [
            {
                "turn": 1,
                "request": {"action": "read", "artifact": "handoff.json", "pointer": ""},
                "response": {"artifact": "handoff.json", "pointer": "", "value": {"id": "UC-01"}},
            },
            {
                "turn": 2,
                "request": {"action": "read", "artifact": "trace_index.json", "pointer": "/entities/UC-01"},
                "response": {"artifact": "trace_index.json", "pointer": "/entities/UC-01", "value": []},
            },
        ]
        with self.assertRaisesRegex(acceptance.base.AcceptanceError, "model trace_lookup"):
            acceptance.audit_tool_reads(records)

    def test_runtime_environment_is_checked_before_a_claim(self) -> None:
        config = {
            "runtime_environment": {
                "python_executable": sys.executable,
                "torch_version": runtime.EXPECTED_TORCH_VERSION,
                "transformers_version": runtime.EXPECTED_TRANSFORMERS_VERSION,
                "cuda_required": True,
                "gpu_name_substring": "RTX 5090",
            }
        }
        fake_cuda = SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_name=lambda _: "NVIDIA GeForce RTX 5090",
        )
        fake_torch = SimpleNamespace(__version__=runtime.EXPECTED_TORCH_VERSION, cuda=fake_cuda)
        fake_transformers = SimpleNamespace(__version__=runtime.EXPECTED_TRANSFORMERS_VERSION)
        with patch.dict(sys.modules, {"torch": fake_torch, "transformers": fake_transformers}):
            receipt = runtime.validate_runtime_environment(config)
        self.assertTrue(receipt["cuda_available"])
        self.assertEqual(receipt["cuda_device_count"], 1)

    def test_measured_config_requires_a_hash_bound_passing_v4_assessment(self) -> None:
        with WorkspaceDirectory() as work:
            public = work / "e2-v3" / "public"
            public.mkdir(parents=True)
            summary_sha = "1" * 64
            assessment = {
                "schema_version": acceptance.ASSESSMENT_SCHEMA,
                "split": "development",
                "development_gate_passed": True,
                "sources": {"runtime_result_sha256": f"sha256:{summary_sha}"},
            }
            assessment_path = work / "assessment.json"
            assessment_raw = runtime.legacy.canonical_bytes(assessment)
            assessment_path.write_bytes(assessment_raw)
            config = {
                "schema_version": runtime.ACTION_SCHEMA,
                "approved": False,
                "model_root": str(work / "model"),
                "model_identity": runtime.legacy.QWEN_MODEL_ID,
                "model_revision": runtime.legacy.QWEN_MODEL_REVISION,
                "integrity_evidence": str(work / "integrity.json"),
                "public_root": str(public),
                "public_manifest_sha256": "2" * 64,
                "output_root": str(work / "measured"),
                "state_root": str(public.parent / "runtime_state_v4"),
                "split": "measured",
                "caps": caps(),
                "accepted_development": {
                    "accepted": True,
                    "freeze_identity": "3" * 64,
                    "summary_sha256": summary_sha,
                    "assessment_path": str(assessment_path),
                    "assessment_sha256": runtime.legacy.sha256_bytes(assessment_raw),
                },
                "runtime_environment": {
                    "python_executable": sys.executable,
                    "torch_version": runtime.EXPECTED_TORCH_VERSION,
                    "transformers_version": runtime.EXPECTED_TRANSFORMERS_VERSION,
                    "cuda_required": True,
                    "gpu_name_substring": "RTX 5090",
                },
            }
            config_path = work / "action.json"
            config_path.write_bytes(runtime.legacy.canonical_bytes(config))
            self.assertEqual(runtime.load_config(config_path)["split"], "measured")

            assessment["development_gate_passed"] = False
            assessment_raw = runtime.legacy.canonical_bytes(assessment)
            assessment_path.write_bytes(assessment_raw)
            config["accepted_development"]["assessment_sha256"] = runtime.legacy.sha256_bytes(assessment_raw)
            config_path.write_bytes(runtime.legacy.canonical_bytes(config))
            with self.assertRaisesRegex(runtime.legacy.RuntimeErrorClosed, "does not pass"):
                runtime.load_config(config_path)

    def test_v4_supervisor_uses_the_bound_interpreter(self) -> None:
        command = supervisor.worker_command(
            {
                "schema_version": runtime.ACTION_SCHEMA,
                "runtime_environment": {"python_executable": sys.executable},
            },
            ROOT / "fixtures" / "phase7_e2_action_v4.template.json",
        )
        self.assertEqual(str(Path(sys.executable).resolve()), command[0])
        self.assertTrue(command[1].endswith("phase7_e2_diagnostic_v4_runtime.py"))
        self.assertEqual("run", command[2])


if __name__ == "__main__":
    unittest.main()
