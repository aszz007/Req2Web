from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import queue
import sys
import types
import unittest
from unittest.mock import patch


_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


try:
    import langgraph  # type: ignore[import-not-found]
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise

    class _StubInMemorySaver:
        pass

    class _StubStateGraph:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def add_node(self, *_: object, **__: object) -> None:
            pass

        def add_edge(self, *_: object, **__: object) -> None:
            pass

        def add_conditional_edges(self, *_: object, **__: object) -> None:
            pass

        def compile(self, *_: object, **__: object) -> object:
            return object()

    _langgraph = types.ModuleType("langgraph")
    _langgraph.__path__ = []  # type: ignore[attr-defined]
    _checkpoint = types.ModuleType("langgraph.checkpoint")
    _checkpoint.__path__ = []  # type: ignore[attr-defined]
    _memory = types.ModuleType("langgraph.checkpoint.memory")
    _memory.InMemorySaver = _StubInMemorySaver
    _graph = types.ModuleType("langgraph.graph")
    _graph.END = "__END__"
    _graph.START = "__START__"
    _graph.StateGraph = _StubStateGraph
    sys.modules.update(
        {
            "langgraph": _langgraph,
            "langgraph.checkpoint": _checkpoint,
            "langgraph.checkpoint.memory": _memory,
            "langgraph.graph": _graph,
        }
    )


import req2web_runtime.phase4_remote_qwen as remote_base
import req2web_runtime.phase4_remote_qwen_fresh_integrated as remote


def _identity(value: object) -> dict[str, object]:
    return remote._identity(value, revision="test.v1")


def _fake_inventory() -> dict[str, object]:
    return {
        "schema_version": remote_base.REMOTE_INVENTORY_SCHEMA_VERSION,
        "model_root_identity": _identity({"root": "test"}),
        "inventory_identity": _identity({"files": 16}),
        "file_count": remote_base.REMOTE_FORMAL_FILE_COUNT,
    }


def _fake_runtime() -> dict[str, object]:
    return {
        "python_version": "3.12.0",
        "transformers_version": remote_base.REMOTE_TRANSFORMERS_VERSION,
        "torch_version": remote_base.REMOTE_TORCH_VERSION,
        "accelerate_version": remote_base.REMOTE_ACCELERATE_VERSION,
    }


def _fake_gpu() -> dict[str, object]:
    return {
        "device_name": remote_base.REMOTE_DEVICE_NAME,
        "device_uuid": "GPU-test-p4-05",
        "total_vram_bytes": remote_base.REMOTE_MIN_VRAM_BYTES,
        "free_vram_bytes": remote_base.REMOTE_MIN_VRAM_BYTES - 1,
        "driver_version": "test-driver",
        "cuda_version": "12.8",
    }


def _fake_graph_bound_delivery() -> dict[str, object]:
    return {
        "materials": object(),
        "live": {},
        "context": object(),
        "guidance": object(),
        "binding": {
            "schema_version": "req2web.phase4.graph_bound_delivery_materials.v1",
            "case_id": remote.P4_05_CASE_ID,
            "graph_assembler_bindings_exact": True,
            "model_loaded": False,
            "run_occurred": False,
        },
    }


class Phase4RemoteFreshIntegratedProfileTests(unittest.TestCase):
    def test_profile_is_exact_bf16_gpu0_no_offload_profile(self) -> None:
        profile = remote.RemoteFreshIntegratedProfile.create(
            inventory=_fake_inventory(),
            runtime_facts=_fake_runtime(),
            gpu_facts=_fake_gpu(),
        )

        profile.validate()
        data = profile.to_dict()
        self.assertEqual(data["device_name"], remote_base.REMOTE_DEVICE_NAME)
        self.assertEqual(data["device_index"], 0)
        self.assertEqual(data["dtype"], "bfloat16")
        self.assertEqual(data["compute_dtype"], "bfloat16")
        self.assertEqual(data["quantization"], "none")
        self.assertIs(data["cpu_offload"], False)
        self.assertIs(data["local_files_only"], True)
        self.assertIs(data["offline"], True)
        self.assertIs(data["network"], False)
        self.assertIs(data["model_loaded"], False)
        self.assertIs(data["run_occurred"], False)

    def test_profile_rejects_runtime_or_placement_drift(self) -> None:
        profile = remote.RemoteFreshIntegratedProfile.create(
            inventory=_fake_inventory(),
            runtime_facts=_fake_runtime(),
            gpu_facts=_fake_gpu(),
        )
        tampered = profile.to_dict()
        tampered["quantization"] = "4bit_nf4"
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            remote.RemoteFreshIntegratedProfile.from_dict(tampered)

        tampered = profile.to_dict()
        tampered["torch_version"] = "drifted"
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            remote.RemoteFreshIntegratedProfile.from_dict(tampered)


class Phase4RemoteFreshIntegratedPolicyTests(unittest.TestCase):
    def test_policy_reuses_fresh_graph_contract_and_freezes_budget(self) -> None:
        profile = remote.RemoteFreshIntegratedProfile.create(
            inventory=_fake_inventory(),
            runtime_facts=_fake_runtime(),
            gpu_facts=_fake_gpu(),
        )
        policy = remote.create_p4_05_policy(
            run_id=f"{remote.P4_05_RUN_PREFIX}test",
            result_root_marker=remote.P4_05_ROOT_MARKER,
            profile=profile,
        )

        self.assertEqual(policy["node_order"], ["F1", "F2", "F3", "F4"])
        self.assertEqual(policy["node_call_cap"], {"F1": 1, "F2": 1, "F3": 1, "F4": 1})
        self.assertEqual(policy["retry_count"], 0)
        self.assertEqual(
            policy["fresh_graph_contract"]["schema_version"],
            "req2web.phase4.local_qwen.fresh_integrated_pilot.policy.v1",
        )
        self.assertIs(policy["action_state"]["model_action"], False)
        self.assertEqual(
            policy["policy_id"],
            remote._identity(
                {key: value for key, value in policy.items() if key != "policy_id"},
                revision=remote.P4_05_POLICY_SCHEMA_VERSION,
            )["sha256"],
        )


class Phase4RemoteFreshIntegratedArtifactTests(unittest.TestCase):
    def test_preflight_writes_only_offline_no_model_artifacts(self) -> None:
        result_root = Path("C:/p4-05-test-only/preflight-result")
        with patch.object(
            remote._remote,
            "validate_remote_model_inventory",
            return_value=_fake_inventory(),
        ), patch.object(
            remote._remote,
            "_collect_remote_runtime_facts",
            return_value=_fake_runtime(),
        ), patch.object(
            remote._remote,
            "_probe_remote_gpu_facts",
            return_value=_fake_gpu(),
        ), patch.object(
            remote,
            "_prepare_graph_bound_delivery_materials",
            return_value=_fake_graph_bound_delivery(),
        ), patch.object(
            remote,
            "_write_fsync",
        ) as write_artifact, patch.object(
            Path,
            "mkdir",
            return_value=None,
        ):
            prepared = remote.prepare_phase4_remote_qwen_fresh_integrated(
                model_root=_ROOT,
                integrity_evidence=_ROOT / "AGENTS.md",
                result_root=result_root,
                run_id=f"{remote.P4_05_RUN_PREFIX}preflight",
            )

        self.assertEqual(prepared["run_id"], f"{remote.P4_05_RUN_PREFIX}preflight")
        self.assertIs(prepared["profile"].model_loaded, False)
        self.assertIs(prepared["profile"].run_occurred, False)
        written_names = {call.args[0].name for call in write_artifact.call_args_list}
        self.assertIn("preflight_manifest.json", written_names)
        self.assertIn("p4_05_policy.json", written_names)
        self.assertIn("b_input.json", written_names)
        self.assertNotIn("load_receipt.json", written_names)

    def test_model_json_accepts_complete_pretty_json_but_not_trailing_data(self) -> None:
        raw = b'{\n  "states": []\n}\n'
        self.assertEqual(remote._parse_model_json(raw, "test"), {"states": []})
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            remote._parse_model_json(b'{"states": []} trailing', "test")


class Phase4RemoteFreshIntegratedStreamingTests(unittest.TestCase):
    def test_stream_mirror_prints_delta_and_keeps_bytes(self) -> None:
        console = io.StringIO()
        mirror = remote.FreshIntegratedStreamMirror(console)
        event = {
            "schema_version": remote.P4_05_STREAM_SCHEMA_VERSION,
            "event": "token_delta",
            "node_id": "F1",
            "delta_b64": base64.b64encode(b'{"page_title":').decode("ascii"),
        }
        mirror.feed(remote._canonical_bytes(event) + b"\n")

        self.assertEqual(bytes(mirror.token_bytes), b'{"page_title":')
        self.assertIn('{"page_title":', console.getvalue())
        self.assertIn(b"token_delta", bytes(mirror.stderr_bytes))

    def test_stream_mirror_rejects_delta_on_non_token_event(self) -> None:
        mirror = remote.FreshIntegratedStreamMirror(io.StringIO())
        event = {
            "schema_version": remote.P4_05_STREAM_SCHEMA_VERSION,
            "event": "load_started",
            "node_id": None,
            "delta_b64": base64.b64encode(b"unexpected").decode("ascii"),
        }
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            mirror.feed(remote._canonical_bytes(event) + b"\n")


class Phase4RemoteFreshIntegratedWorkerBoundaryTests(unittest.TestCase):
    def test_parent_receive_uses_queue_ipc_boundary(self) -> None:
        worker = object.__new__(remote.FreshIntegratedRemoteWorker)
        worker._messages = queue.Queue()
        worker._messages.put(
            {
                "protocol": remote.P4_05_WORKER_PROTOCOL,
                "kind": "loaded",
            }
        )
        worker._closed = False
        self.assertEqual(
            worker._receive(1)["kind"],
            "loaded",
        )

    def test_run_requires_explicit_confirmation_before_preflight(self) -> None:
        with self.assertRaises(remote.Phase4RemoteFreshIntegratedError):
            remote.run_phase4_remote_qwen_fresh_integrated(
                model_root=Path("C:/not-used/model"),
                integrity_evidence=Path("C:/not-used/evidence"),
                result_root=Path("C:/not-used/result"),
                confirm_one_remote_fresh_integrated_run=False,
            )

    def test_graph_bound_material_failure_precedes_model_worker(self) -> None:
        result_root = Path("C:/p4-05-test-only/fixed-binding-failure")
        with patch.object(
            remote,
            "_prepare_graph_bound_delivery_materials",
            side_effect=remote.Phase4RemoteFreshIntegratedError(
                "fixed binding drift"
            ),
        ) as prepare_fixed, patch.object(
            remote,
            "FreshIntegratedRemoteWorker",
        ) as worker, patch.object(
            remote,
            "_write_fsync",
            return_value=None,
        ), patch.object(
            Path,
            "mkdir",
            return_value=None,
        ):
            with self.assertRaises(
                remote.Phase4RemoteFreshIntegratedError
            ):
                remote.run_phase4_remote_qwen_fresh_integrated(
                    model_root=_ROOT,
                    integrity_evidence=_ROOT / "AGENTS.md",
                    result_root=result_root,
                    confirm_one_remote_fresh_integrated_run=True,
                )

        prepare_fixed.assert_called_once()
        worker.assert_not_called()


class Phase4RemoteFreshIntegratedCliTests(unittest.TestCase):
    def test_cli_exposes_preflight_and_confirmation_gate(self) -> None:
        from scripts import run_phase4_remote_qwen_fresh_integrated as cli

        help_text = cli.build_parser().format_help()
        self.assertNotIn("--repository-root", help_text)
        self.assertIn("--preflight-only", help_text)
        self.assertIn("--confirm-one-remote-fresh-integrated-run", help_text)

        with self.assertRaises(SystemExit) as raised:
            cli.main(
                [
                    "--model-root",
                    "C:/not-used/model",
                    "--integrity-evidence",
                    "C:/not-used/evidence",
                    "--result-root",
                    "C:/not-used/result",
                ]
            )
        self.assertEqual(raised.exception.code, 2)

    def test_cli_preflight_only_does_not_start_model_worker(self) -> None:
        from scripts import run_phase4_remote_qwen_fresh_integrated as cli

        with patch.object(
            remote,
            "prepare_phase4_remote_qwen_fresh_integrated",
            return_value={"run_id": f"{remote.P4_05_RUN_PREFIX}cli"},
        ) as prepare:
            result = cli.main(
                [
                    "--model-root",
                    str(_ROOT),
                    "--integrity-evidence",
                    str(_ROOT / "AGENTS.md"),
                    "--result-root",
                    "C:/p4-05-test-only/cli-result",
                    "--preflight-only",
                ]
            )

        self.assertEqual(result, 0)
        prepare.assert_called_once()

    def test_remote_runner_no_longer_references_early_delivery_bridge(self) -> None:
        source = (
            _ROOT
            / "src"
            / "req2web_runtime"
            / "phase4_remote_qwen_fresh_integrated.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("phase4_delivery_bridge", source)
        self.assertIn("run_phase4_fresh_delivery", source)
        self.assertIn("build_phase4_graph_bound_delivery_materials", source)
        self.assertNotIn("autodl_trusted_remote_case_loader", source)
        self.assertNotIn("build_fixed_trusted_remote_case_materials_v2", source)


if __name__ == "__main__":
    unittest.main()
