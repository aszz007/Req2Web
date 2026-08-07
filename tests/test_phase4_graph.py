from __future__ import annotations

import base64
import copy
import importlib.metadata
import json
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from req2web_orchestration.phase4_graph import (  # noqa: E402
    GRAPH_NODE_ORDER,
    PauseHandle,
    Phase4ContractError,
    Phase4GraphRuntime,
    create_initial_state,
    synthetic_commerce_b_input,
    validate_dependency_acquisition_receipt,
    validate_graph_state,
    validate_runtime_environment,
)
from req2web_provider.semantic_candidate import ModelSemanticCandidate  # noqa: E402


class Phase4GraphTest(unittest.TestCase):
    def state(self, suffix: str = "base") -> dict[str, object]:
        return create_initial_state(
            synthetic_commerce_b_input(request_id=f"p4-02a-request-{suffix}")
        )

    def test_runtime_environment_and_in_memory_checkpoint_are_exact(self) -> None:
        validate_runtime_environment()
        receipt = validate_dependency_acquisition_receipt()
        self.assertTrue(receipt["action_state"]["dependency_installation"])
        self.assertFalse(receipt["action_state"]["graph_runtime_execution"])
        runtime = Phase4GraphRuntime()
        self.assertFalse(hasattr(runtime, "graph"))
        self.assertFalse(hasattr(runtime, "checkpointer"))
        self.assertIsInstance(runtime._checkpointer, InMemorySaver)

    def test_happy_path_composes_existing_candidate_contract(self) -> None:
        result = Phase4GraphRuntime().invoke(
            self.state("happy"), thread_id="p4-02a-thread-happy"
        )
        self.assertEqual(result["status"], "foundation_completed")
        self.assertEqual(result["completed_graph_nodes"], list(GRAPH_NODE_ORDER))
        self.assertEqual(result["execution_counts"], {node: 1 for node in ("F1", "F2", "F3", "F4")})
        self.assertTrue(all(item["disposition"] == "absent" for item in result["advisory_dispositions"]))
        self.assertEqual(
            result["candidate_composition_record"]["candidate_projection_status"],
            "candidate_composition_validated_only",
        )
        self.assertEqual(
            result["candidate_composition_record"]["page_spec_assembler_status_at_projection"],
            "not_executed_p4_02a",
        )
        self.assertFalse(result["candidate_composition_record"]["integrated_success"])
        self.assertEqual(result["assembly_record"]["assembly_status"], "assembled_synthetic")
        self.assertEqual(
            result["assembly_record"]["assembled_page_spec"]["schema_version"],
            "req2web.page_spec.v1",
        )
        self.assertFalse(result["assembly_record"]["integrated_success"])
        self.assertTrue(result["action_state"]["graph_runtime_execution"])
        self.assertFalse(result["action_state"]["dependency_installation"])
        raw = base64.b64decode(
            result["candidate_composition_record"]["model_semantic_candidate_canonical_b64"],
            validate=True,
        )
        candidate = ModelSemanticCandidate.from_dict(json.loads(raw.decode("utf-8")))
        self.assertEqual(candidate.constraints, ())
        self.assertEqual(candidate.claimed_attribution_edges, ())
        validate_graph_state(result, require_terminal=True)

    def test_exact_keys_and_live_b_identity_reject_tamper(self) -> None:
        extra = self.state("extra")
        extra["unexpected"] = True
        with self.assertRaisesRegex(Phase4ContractError, "exact keys"):
            validate_graph_state(extra)

        drift = self.state("drift")
        drift["b_input"]["requirement"] = "tampered"
        with self.assertRaisesRegex(Phase4ContractError, "identity drift"):
            Phase4GraphRuntime().invoke(drift, thread_id="p4-02a-thread-drift")

    def test_bool_cannot_impersonate_event_integer(self) -> None:
        result = Phase4GraphRuntime().invoke(
            self.state("bool"), thread_id="p4-02a-thread-bool"
        )
        forged = copy.deepcopy(result)
        forged["events"][0]["event_seq"] = True
        with self.assertRaisesRegex(Phase4ContractError, "non-boolean integer"):
            validate_graph_state(forged)

    def test_registry_conflict_fails_closed_without_downstream(self) -> None:
        result = Phase4GraphRuntime(
            fixture_scenario="f1_duplicate_local_id"
        ).invoke(self.state("registry"), thread_id="p4-02a-thread-registry")
        self.assertEqual(result["status"], "failed_closed")
        self.assertEqual(result["failure"]["failure_code"], "registry_conflict")
        self.assertEqual(result["execution_counts"], {"F1": 1, "F2": 0, "F3": 0, "F4": 0})
        self.assertIsNone(result["candidate_composition_record"])
        self.assertFalse(result["failure"]["retry_allowed"])
        self.assertFalse(result["failure"]["fallback_allowed"])

    def test_f1_cross_type_local_id_fails_before_registry(self) -> None:
        result = Phase4GraphRuntime(
            fixture_scenario="f1_cross_type_local_id"
        ).invoke(self.state("cross-type"), thread_id="p4-02a-thread-cross-type")
        self.assertEqual(result["status"], "failed_closed")
        self.assertEqual(result["execution_counts"], {"F1": 1, "F2": 0, "F3": 0, "F4": 0})
        self.assertEqual(result["completed_graph_nodes"], ["F1"])
        self.assertIsNone(result["assembly_record"])

    def test_node_failure_is_one_execution_and_freezes_downstream(self) -> None:
        result = Phase4GraphRuntime(
            fixture_scenario="f2_invalid_reference"
        ).invoke(self.state("f2-failure"), thread_id="p4-02a-thread-f2-failure")
        self.assertEqual(result["status"], "failed_closed")
        self.assertEqual(result["execution_counts"], {"F1": 1, "F2": 1, "F3": 0, "F4": 0})
        self.assertIn("F1", result["node_results"])
        self.assertEqual(result["node_results"]["F2"]["status"], "failed_closed")
        self.assertNotIn("F3", result["node_results"])
        self.assertIsNone(result["mapping_record"])
        self.assertIsNone(result["candidate_composition_record"])

    def test_update_stream_is_normalized_and_in_topology_order(self) -> None:
        chunks, result = Phase4GraphRuntime().stream(
            self.state("stream"), thread_id="p4-02a-thread-stream"
        )
        self.assertEqual([item["sequence"] for item in chunks], list(range(1, len(chunks) + 1)))
        self.assertEqual([item["graph_node"] for item in chunks], list(GRAPH_NODE_ORDER))
        self.assertEqual(result["status"], "foundation_completed")

    def test_interrupt_and_same_binding_resume_complete(self) -> None:
        runtime = Phase4GraphRuntime()
        handle = runtime.invoke_until_interrupt(
            self.state("interrupt"),
            thread_id="p4-02a-thread-interrupt",
            before_node="F3",
        )
        replay = PauseHandle.from_dict(handle.to_dict())
        self.assertEqual(replay.before_node, "F3")
        result = runtime.resume(replay)
        self.assertEqual(result["status"], "foundation_completed")
        self.assertEqual(result["execution_counts"], {"F1": 1, "F2": 1, "F3": 1, "F4": 1})

    def test_wrong_resume_binding_is_rejected_before_execution(self) -> None:
        runtime = Phase4GraphRuntime()
        handle = runtime.invoke_until_interrupt(
            self.state("wrong-resume"),
            thread_id="p4-02a-thread-wrong-resume",
            before_node="F3",
        )
        forged = handle.to_dict()
        forged["request_id"] = "different-request"
        with self.assertRaisesRegex(Phase4ContractError, "handle identity drift"):
            PauseHandle.from_dict(forged)

    def test_tracing_enablement_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, {"LANGSMITH_TRACING": "true"}, clear=False):
            with self.assertRaisesRegex(Phase4ContractError, "tracing"):
                Phase4GraphRuntime()

    def test_runtime_does_not_open_network_connection(self) -> None:
        runtime = Phase4GraphRuntime()
        with mock.patch("socket.create_connection", side_effect=AssertionError("network")) as create_connection:
            with mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")) as connect:
                result = runtime.invoke(
                    self.state("network"), thread_id="p4-02a-thread-network"
                )
        self.assertEqual(result["status"], "foundation_completed")
        create_connection.assert_not_called()
        connect.assert_not_called()

    def test_composition_hash_tamper_is_rejected(self) -> None:
        result = Phase4GraphRuntime().invoke(
            self.state("composition-tamper"),
            thread_id="p4-02a-thread-composition-tamper",
        )
        forged = copy.deepcopy(result)
        forged["candidate_composition_record"]["model_semantic_candidate_byte_length"] += 1
        with self.assertRaisesRegex(Phase4ContractError, "identity drift"):
            validate_graph_state(forged, require_terminal=True)

    def test_assembler_binding_failure_preserves_candidate_and_fails_closed(self) -> None:
        result = Phase4GraphRuntime(
            fixture_scenario="assembler_binding_mismatch"
        ).invoke(
            self.state("assembler-failure"),
            thread_id="p4-02a-thread-assembler-failure",
        )
        self.assertEqual(result["status"], "failed_closed")
        self.assertEqual(result["failure"]["failure_code"], "parser_assembler_invalid")
        self.assertIsNotNone(result["candidate_composition_record"])
        self.assertIsNone(result["assembly_record"])
        self.assertFalse(result["failure"]["retry_allowed"])

    def test_early_event_tamper_is_rejected(self) -> None:
        result = Phase4GraphRuntime().invoke(
            self.state("event-tamper"),
            thread_id="p4-02a-thread-event-tamper",
        )
        forged = copy.deepcopy(result)
        forged["events"][0]["event_type"] = "forged_success"
        with self.assertRaisesRegex(Phase4ContractError, "event type/topology drift"):
            validate_graph_state(forged, require_terminal=True)

    def test_action_state_boolean_tamper_is_rejected(self) -> None:
        state = self.state("action-state")
        state["action_state"]["model_action"] = 0
        with self.assertRaisesRegex(Phase4ContractError, "action-state"):
            validate_graph_state(state)

    def test_registry_receipt_tamper_is_recomputed_and_rejected(self) -> None:
        result = Phase4GraphRuntime().invoke(
            self.state("registry-replay"),
            thread_id="p4-02a-thread-registry-replay",
        )
        forged = copy.deepcopy(result)
        forged["registry_inventory"][0]["canonical_key_sha256"] = (
            "sha256:" + ("1" * 64)
        )
        with self.assertRaisesRegex(Phase4ContractError, "registry inventory authority drift"):
            validate_graph_state(forged, require_terminal=True)

    def test_requirements_file_drift_is_rejected(self) -> None:
        target = (ROOT / "requirements-phase4-agent.txt").resolve()
        original = Path.read_bytes

        def drift(path: Path) -> bytes:
            if path.resolve() == target:
                return b"langgraph==1.2.8\n"
            return original(path)

        with mock.patch.object(Path, "read_bytes", new=drift):
            with self.assertRaisesRegex(Phase4ContractError, "direct dependency pin drift"):
                validate_dependency_acquisition_receipt()

    def test_installed_dependency_file_drift_is_rejected(self) -> None:
        state = self.state("installed-drift")
        distribution = importlib.metadata.distribution("langgraph")
        target = Path(distribution.locate_file("langgraph/version.py")).resolve()
        original = Path.read_bytes

        def drift(path: Path) -> bytes:
            raw = original(path)
            return raw + b"\n" if path.resolve() == target else raw

        with mock.patch.object(Path, "read_bytes", new=drift):
            with self.assertRaisesRegex(Phase4ContractError, "installed dependency file drift"):
                validate_graph_state(state)

    def test_platform_specific_record_metadata_is_not_runtime_authority(self) -> None:
        import req2web_orchestration.phase4_graph as graph

        receipt = graph._validate_dependency_acquisition_receipt(
            verify_installed_files=False
        )
        installed = copy.deepcopy(receipt["resolved_closure"])
        for index, row in enumerate(installed):
            row["distribution"] = row["distribution"].replace("-", "_")
            row["record_sha256"] = "sha256:" + f"{index + 1:064x}"
        with mock.patch.object(
            graph,
            "_installed_langgraph_state",
            return_value=(
                installed,
                [
                    {
                        "distribution": "langgraph",
                        "relative_path": "linux/site-packages/langgraph/__init__.py",
                        "byte_length": 1,
                        "sha256": "sha256:" + ("f" * 64),
                    }
                ],
            ),
        ):
            self.assertEqual(
                validate_dependency_acquisition_receipt(),
                receipt,
            )

    def test_installed_dependency_version_drift_is_rejected(self) -> None:
        import req2web_orchestration.phase4_graph as graph

        receipt = graph._validate_dependency_acquisition_receipt(
            verify_installed_files=False
        )
        installed = copy.deepcopy(receipt["resolved_closure"])
        installed[0]["version"] = "0.0.0"
        with mock.patch.object(
            graph,
            "_installed_langgraph_state",
            return_value=(installed, []),
        ):
            with self.assertRaisesRegex(
                Phase4ContractError,
                "installed LangGraph dependency closure drift",
            ):
                validate_dependency_acquisition_receipt()

    def test_foundation_status_requires_complete_topology_without_terminal_flag(self) -> None:
        state = self.state("incomplete-foundation")
        state["status"] = "foundation_completed"
        with self.assertRaisesRegex(Phase4ContractError, "completed graph is incomplete"):
            validate_graph_state(state)


if __name__ == "__main__":
    unittest.main()
