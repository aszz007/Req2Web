from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "phase7_e2_diagnostic_runtime.py"
WORKSPACE_TEMP = Path(__file__).resolve().parents[1] / "outputs" / "phase7_e2_diagnostic_v2" / "runtime_tests"
WORKSPACE_TEMP.mkdir(parents=True, exist_ok=True)


class WorkspaceDirectory:
    """Avoid the managed-host TemporaryDirectory ACL defect."""
    def __enter__(self):
        self.path = WORKSPACE_TEMP / ("case_" + uuid.uuid4().hex)
        self.path.mkdir()
        return str(self.path)

    def __exit__(self, exc_type, exc, tb):
        target = self.path.resolve(strict=True)
        root = WORKSPACE_TEMP.resolve(strict=True)
        if target.parent != root or not target.name.startswith("case_"):
            raise RuntimeError("refusing unsafe runtime-test cleanup target")
        shutil.rmtree(target)
SPEC = importlib.util.spec_from_file_location("phase7_e2_diagnostic_runtime", MODULE_PATH)
runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runtime)


class ScriptedBackend:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def generate(self, messages, *, max_new_tokens, remaining_input_tokens, deadline, started, chunk):
        self.calls += 1
        started({"mock": True, "input_tokens": 100, "formatted_prompt": json.dumps(messages, sort_keys=True)})
        raw = runtime.canonical_bytes(next(self.replies))
        chunk(raw[: max(1, len(raw) // 2)])
        return {"raw": raw, "input_tokens": 100, "output_tokens": 20}


def build_public(root: Path) -> tuple[Path, str]:
    public = root / "public"
    packets = []
    development = []
    measured = []
    counters = {"development": 0, "measured": 0}
    for absolute in range(1, 23):
            split, prefix = ("development", "d") if absolute <= 2 else ("measured", "m")
            pid = f"p{absolute:03d}"
            files = {"readme.json": {"overview": "neutral"}, "facts.json": {"entities": [{"id": "component-1"}], "use_cases": ["UC-01"]}, "trace_index.json": {"edges": []}}
            rows = []
            packet_dir = public / "packets" / pid
            packet_dir.mkdir(parents=True)
            for name, value in files.items():
                raw = runtime.canonical_bytes(value)
                (packet_dir / name).write_bytes(raw)
                rows.append({"path": name, "sha256": runtime.sha256_bytes(raw), "bytes": len(raw)})
            packets.append({"packet_id": pid, "files": rows})
            target = development if split == "development" else measured
            for arm in ("A", "B"):
                counters[split] += 1
                target.append({"session_id": f"{prefix}{counters[split]:03d}", "packet_id": pid, "arm": arm})
    manifest = {"schema_version": runtime.PUBLIC_SCHEMA, "budgets": runtime.EXPECTED_BUDGETS, "packets": packets, "schedule": {"development": development, "measured": measured}}
    raw = runtime.canonical_bytes(manifest)
    (public / "manifest.json").write_bytes(raw)
    return public, runtime.sha256_bytes(raw)


def write_config(root: Path, public: Path, digest: str, *, split="development", accepted=None) -> Path:
    model = root / "model"; model.mkdir()
    integrity = root / "integrity.json"; integrity.write_text("{}", encoding="utf-8")
    config = {
        "schema_version": "req2web.phase7.e2_action.v1", "approved": True,
        "model_root": str(model), "model_identity": runtime.QWEN_MODEL_ID, "model_revision": runtime.QWEN_MODEL_REVISION,
        "integrity_evidence": str(integrity), "public_root": str(public),
        "public_manifest_sha256": digest, "output_root": str(root / "output"),
        "state_root": str(public.parent / "runtime_state"), "split": split,
        "caps": {key: runtime.EXPECTED_BUDGETS[key] for key in ("reads", "input_tokens", "output_tokens", "provider_turns", "session_seconds")},
        "accepted_development": accepted,
    }
    path = root / "action.json"; path.write_bytes(runtime.canonical_bytes(config)); return path


class E2RuntimeTests(unittest.TestCase):
    def test_chat_eos_is_added_to_model_end_of_text_without_replacing_it(self):
        tokenizer = SimpleNamespace(eos_token_id=248046,
            convert_tokens_to_ids=lambda token: 248046 if token == "<|im_end|>" else None,
            convert_ids_to_tokens=lambda token: "<|im_end|>" if token == 248046 else None)
        self.assertEqual([248044, 248046], runtime.generation_stop_ids(tokenizer, 248044))
        self.assertEqual([248044, 248046], runtime.generation_stop_ids(tokenizer, [248044, 248046]))
        tokenizer.eos_token_id = 0
        with self.assertRaises(runtime.RuntimeErrorClosed):
            runtime.generation_stop_ids(tokenizer, 248044)

    def test_multi_message_output_is_preserved_and_rejected_without_trimming(self):
        with WorkspaceDirectory() as td:
            root = Path(td); public, digest = build_public(root)
            manifest = runtime.validate_public_root(public, digest)
            raw = b'{"action":"read","artifact":"facts.json","pointer":""}\nuser\n{"invented":true}'
            class Backend:
                def generate(self, messages, **kw):
                    kw["started"]({"input_tokens": 100, "formatted_prompt": json.dumps(messages)})
                    kw["chunk"](raw)
                    return {"raw": raw, "input_tokens": 100, "output_tokens": 25}
            result = runtime._run_session(row=manifest["schedule"]["development"][0], packet=manifest["packets"][0],
                root=public, out=root/"output", backend=Backend(), caps=runtime.EXPECTED_BUDGETS,
                freeze="a"*64, active_path=root/"active.json")
            self.assertEqual("invalid_response", result["status"])
            self.assertIn("Extra data", result["protocol_error"])
            self.assertEqual(raw, (root/"output/d001/turn_01.raw").read_bytes())
            self.assertEqual(0, result["reads"])

    def test_six_reads_reserve_the_seventh_turn_for_final_for_both_arms(self):
        with WorkspaceDirectory() as td:
            root = Path(td); public, digest = build_public(root)
            manifest = runtime.validate_public_root(public, digest)
            final = {"action":"final", "answer":{"status":"unknown", "origin_candidates":[],
                     "affected_use_cases":[], "evidence_edges":[], "uncertainty":"insufficient evidence"}}
            class Backend(ScriptedBackend):
                def generate(self, messages, **kw):
                    if self.calls == 6:
                        control = json.loads(messages[-1]["content"])["host_control"]
                        assert control["final_required"] is True and control["remaining_reads"] == 0
                        assert control["remaining_provider_turns"] == 1
                    return super().generate(messages, **kw)
            for row in manifest["schedule"]["development"][:2]:
                replies = [{"action":"read", "artifact":"facts.json", "pointer":""}]*6 + [final]
                result = runtime._run_session(row=row, packet=manifest["packets"][0], root=public,
                    out=root/"output", backend=Backend(replies), caps=runtime.EXPECTED_BUDGETS,
                    freeze="a"*64, active_path=root/"active.json")
                self.assertEqual("unknown", result["status"])
                self.assertEqual((6,7), (result["reads"],result["provider_turns"]))

    def test_public_manifest_and_packet_bytes_are_bound(self):
        with WorkspaceDirectory() as td:
            root = Path(td); public, digest = build_public(root)
            manifest = runtime.validate_public_root(public, digest)
            self.assertEqual(22, len(manifest["packets"]))
            (public / "packets" / "p001" / "facts.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(runtime.RuntimeErrorClosed, "identity mismatch"):
                runtime.validate_public_root(public, digest)

    def test_duplicate_json_keys_and_nan_fail_closed(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(runtime.RuntimeErrorClosed):
                runtime._strict_json(raw)

    def test_development_run_reads_then_finishes_and_claim_prevents_resume(self):
        with WorkspaceDirectory() as td:
            root = Path(td); public, digest = build_public(root); config = write_config(root, public, digest)
            replies = []
            for row in runtime._strict_json((public / "manifest.json").read_bytes())["schedule"]["development"]:
                replies.extend([
                    {"action": "read", "artifact": "facts.json", "pointer": "/entities/0/id"},
                    {"action": "final", "answer": {"status": "fault", "origin_candidates": [{"artifact": "facts.json", "entity": "component-1", "field": "id"}], "affected_use_cases": ["UC-01"], "evidence_edges": [], "uncertainty": "bounded mock"}},
                ])
            backend = ScriptedBackend(replies)
            with patch.object(runtime, "preflight_run") as preflight:
                manifest = runtime.validate_public_root(public, digest)
                freeze = runtime._freeze_identity(runtime._load_config(config), digest)
                preflight.return_value = {"freeze_identity": freeze, "manifest": manifest}
                summary = runtime.run_experiment(config, backend=backend)
                self.assertEqual(4, len(summary["sessions"]))
                self.assertTrue(all(s["status"] == "fault" and s["reads"] == 1 for s in summary["sessions"]))
                self.assertTrue(all(s["provider_turns"] == 2 and s["input_tokens"] == 200 for s in summary["sessions"]))
                self.assertEqual(digest, summary["public_manifest_sha256"])
                with self.assertRaisesRegex(runtime.RuntimeErrorClosed, "already claimed"):
                    runtime.run_experiment(config, backend=backend)

    def test_unlisted_read_ends_session_without_guessing(self):
        with WorkspaceDirectory() as td:
            root = Path(td); public, digest = build_public(root); config = write_config(root, public, digest)
            backend = ScriptedBackend([{"action": "read", "artifact": "secret.json", "pointer": ""}] * 4)
            with patch.object(runtime, "preflight_run") as preflight:
                manifest = runtime.validate_public_root(public, digest)
                preflight.return_value = {"freeze_identity": runtime._freeze_identity(runtime._load_config(config), digest), "manifest": manifest}
                summary = runtime.run_experiment(config, backend=backend)
            self.assertTrue(all(s["status"] == "invalid_response" for s in summary["sessions"]))

    def test_arm_a_cannot_read_list_hidden_trace_index(self):
        with WorkspaceDirectory() as td:
            root = Path(td); public, digest = build_public(root); config = write_config(root, public, digest)
            final = {"action": "final", "answer": {"status": "unknown", "origin_candidates": [], "affected_use_cases": [], "evidence_edges": [], "uncertainty": "mock"}}
            backend = ScriptedBackend([{"action": "read", "artifact": "trace_index.json", "pointer": ""}, final, final, final])
            with patch.object(runtime, "preflight_run") as preflight:
                manifest = runtime.validate_public_root(public, digest)
                preflight.return_value = {"freeze_identity": runtime._freeze_identity(runtime._load_config(config), digest), "manifest": manifest}
                summary = runtime.run_experiment(config, backend=backend)
            self.assertEqual("invalid_response", summary["sessions"][0]["status"])
            self.assertEqual("unknown", summary["sessions"][1]["status"])

    def test_measured_requires_explicit_accepted_development(self):
        with WorkspaceDirectory() as td:
            root = Path(td); public, digest = build_public(root); config = write_config(root, public, digest, split="measured")
            with self.assertRaisesRegex(runtime.RuntimeErrorClosed, "accepted-development"):
                runtime._load_config(config)


if __name__ == "__main__":
    unittest.main()
