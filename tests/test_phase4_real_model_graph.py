from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_orchestration.phase4_graph import (  # noqa: E402
    NODE_ORDER,
    REAL_MODEL_GRAPH_NODE_ORDER,
    REAL_MODEL_GRAPH_REVISION,
    REAL_MODEL_SOURCE_KIND,
    Phase4RealModelGraphRuntime,
    create_real_model_graph_state,
    make_identity,
    make_real_model_raw_capture,
    phase4_create_portable_authority_state,
    phase4_synthetic_assembler_bindings,
    phase4_synthetic_fixture_output,
    synthetic_commerce_b_input,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _execution(
    node_id: str,
    output: dict[str, object],
) -> dict[str, object]:
    raw = _canonical(output)
    return {
        "schema_version": "req2web.phase4.real_model_node_execution.v1",
        "node_id": node_id,
        "status": "validated",
        "source_kind": REAL_MODEL_SOURCE_KIND,
        "generate_call_count": 1,
        "raw_capture": make_real_model_raw_capture(raw),
        "attempt_identity": make_identity(
            {
                "node_id": node_id,
                "raw_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            },
            revision="test.real_model_attempt.v1",
        ),
        "output": output,
        "raw_model_contract_success": True,
        "normalized_node_contract_success": True,
        "failure": None,
    }


class Phase4RealModelGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b_input = synthetic_commerce_b_input(
            case_id="p4-real-graph-test",
            request_id="p4-real-graph-request",
        )
        fixture_state = phase4_create_portable_authority_state(self.b_input)
        self.context, self.guidance = phase4_synthetic_assembler_bindings(
            fixture_state
        )

    def test_real_executor_uses_one_fixed_langgraph_topology(self) -> None:
        def execute(
            node_id: str,
            authority_state: dict[str, object],
            _authority_projection: dict[str, object],
        ) -> dict[str, object]:
            return _execution(
                node_id,
                phase4_synthetic_fixture_output(node_id, authority_state),
            )

        runtime = Phase4RealModelGraphRuntime(
            node_executor=execute,
            context=self.context,
            guidance=self.guidance,
            delivery_executor=lambda *_: {
                "graph_delivery_success": True,
                "status": "first_pass_success",
            },
        )
        result = runtime.invoke(
            create_real_model_graph_state(
                run_id="p4-real-graph-run",
                b_input=self.b_input,
                upstream_binding={"test_fixture": True},
            ),
            thread_id="p4-real-graph-thread",
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            tuple(result["completed_graph_nodes"]),
            REAL_MODEL_GRAPH_NODE_ORDER,
        )
        self.assertEqual(
            result["graph_revision"],
            REAL_MODEL_GRAPH_REVISION,
        )
        self.assertEqual(
            tuple(result["node_execution_records"]),
            NODE_ORDER,
        )
        for node_id in NODE_ORDER:
            node_result = result["authority_state"]["node_results"][node_id]
            self.assertEqual(
                node_result["raw_capture"]["source_kind"],
                REAL_MODEL_SOURCE_KIND,
            )
            self.assertEqual(node_result["payload"]["call_count"], 1)
            self.assertEqual(
                node_result["payload"]["execution_binding"][
                    "generate_call_count"
                ],
                1,
            )

    def test_failed_node_stops_before_downstream_nodes(self) -> None:
        def execute(
            node_id: str,
            authority_state: dict[str, object],
            _authority_projection: dict[str, object],
        ) -> dict[str, object]:
            if node_id != "F2":
                return _execution(
                    node_id,
                    phase4_synthetic_fixture_output(
                        node_id,
                        authority_state,
                    ),
                )
            return {
                "schema_version": (
                    "req2web.phase4.real_model_node_execution.v1"
                ),
                "node_id": node_id,
                "status": "failed_closed",
                "source_kind": REAL_MODEL_SOURCE_KIND,
                "generate_call_count": 1,
                "raw_capture": {
                    "state": "not_formed",
                    "byte_length": 0,
                    "sha256": "sha256:" + ("0" * 64),
                    "source_kind": REAL_MODEL_SOURCE_KIND,
                },
                "attempt_identity": make_identity(
                    {"node_id": node_id, "failed": True},
                    revision="test.real_model_attempt.v1",
                ),
                "output": None,
                "raw_model_contract_success": False,
                "normalized_node_contract_success": False,
                "failure": {
                    "failure_code": "test_failure",
                    "failure_stage": node_id,
                    "retry_allowed": False,
                    "fallback_allowed": False,
                    "message_code": "test_failure",
                },
            }

        runtime = Phase4RealModelGraphRuntime(
            node_executor=execute,
            context=self.context,
            guidance=self.guidance,
            delivery_executor=lambda *_: self.fail(
                "delivery must not run after F2 failure"
            ),
        )
        result = runtime.invoke(
            create_real_model_graph_state(
                run_id="p4-real-graph-failure-run",
                b_input=self.b_input,
                upstream_binding={"test_fixture": True},
            ),
            thread_id="p4-real-graph-failure-thread",
        )

        self.assertEqual(result["status"], "failed_closed")
        self.assertEqual(
            tuple(result["node_execution_records"]),
            ("F1", "F2"),
        )
        self.assertNotIn("F3", result["node_input_authorities"])
        self.assertIsNone(result["delivery_result"])


if __name__ == "__main__":
    unittest.main()
