from __future__ import annotations

import inspect
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime.phase4_canonical_full_flow import (  # noqa: E402
    ACTIVE_DEFAULT_ENTRY,
    FLOW_AUTHORITY_ROLE,
    Phase4CanonicalFullFlowError,
    _build_upstream,
    _parent_experiment_binding,
    _progress_checkpoint_path,
    run_phase4_canonical_full_flow,
)
from req2web_runtime import (  # noqa: E402
    phase4_remote_qwen_fresh_integrated as historical_manual,
    phase4_remote_qwen_full_direct_stability as historical_stability,
)
from req2web_runtime.phase4_canonical_full_flow_cases import (  # noqa: E402
    CANARY_COUNT,
    CASE_COUNT,
    get_case_set,
    validate_supported_requirement_scope,
)
from req2web_orchestration.phase4_graph import (  # noqa: E402
    REAL_MODEL_SOURCE_KIND,
    Phase4RealModelGraphRuntime,
    create_real_model_graph_state,
    make_identity,
    make_real_model_raw_capture,
    phase4_synthetic_fixture_output,
)


class Phase4CanonicalFullFlowTest(unittest.TestCase):
    def test_progress_checkpoints_are_write_once_per_case(self) -> None:
        root = Path("result-root")
        self.assertEqual(
            _progress_checkpoint_path(root, 1),
            root / "progress" / "01.json",
        )
        self.assertEqual(
            _progress_checkpoint_path(root, 2),
            root / "progress" / "02.json",
        )
        with self.assertRaisesRegex(
            Phase4CanonicalFullFlowError,
            "progress checkpoint index is invalid",
        ):
            _progress_checkpoint_path(root, 0)

    def test_only_canonical_full_flow_is_the_active_default(self) -> None:
        self.assertTrue(ACTIVE_DEFAULT_ENTRY)
        self.assertEqual(
            FLOW_AUTHORITY_ROLE,
            "active_canonical_full_flow",
        )
        self.assertFalse(historical_manual.ACTIVE_DEFAULT_ENTRY)
        self.assertTrue(historical_manual.HISTORICAL_MANUAL_ENTRYPOINT)
        self.assertEqual(
            historical_manual.FLOW_AUTHORITY_ROLE,
            (
                "shared_qwen_runtime_component_library_with_historical_"
                "manual_entrypoint"
            ),
        )
        self.assertFalse(historical_stability.ACTIVE_DEFAULT_ENTRY)
        source = inspect.getsource(run_phase4_canonical_full_flow)
        self.assertNotIn("phase4_full_architecture_cases", source)
        self.assertNotIn(
            "run_phase4_remote_qwen_fresh_integrated(",
            source,
        )
        self.assertNotIn("phase4_remote_qwen_stability", source)

    def test_parent_binding_is_owned_by_the_canonical_flow(self) -> None:
        case = get_case_set()["cases"][0]
        policy_identity = {
            "identity_kind": "canonical_json",
            "sha256": "sha256:" + "a" * 64,
            "byte_length": 100,
            "revision": "test.policy.v1",
        }
        binding = _parent_experiment_binding(
            experiment_run_id="canonical-flow-run",
            experiment_policy_identity=policy_identity,
            index=1,
            case=case,
        )
        self.assertEqual(binding["case_id"], case["case_id"])
        self.assertEqual(binding["request_id"], case["request_id"])
        self.assertEqual(
            binding["experiment_policy_identity"],
            policy_identity,
        )

    def test_case_set_is_ten_raw_requirements_only(self) -> None:
        case_set = get_case_set()
        self.assertEqual(case_set["case_count"], CASE_COUNT)
        self.assertEqual(case_set["canary_count"], CANARY_COUNT)
        self.assertFalse(case_set["prewritten_upstream_fields_present"])
        forbidden = {
            "requirement_summary",
            "use_cases",
            "retrieval_queries",
            "retrieval_results",
            "agent_context",
            "retrieval_guidance",
            "b_input",
        }
        for case in case_set["cases"]:
            self.assertFalse(forbidden & set(case))

    def test_scope_check_rejects_obviously_broad_system_before_model(self) -> None:
        receipt = validate_supported_requirement_scope(
            "Build a complete enterprise system with all business modules."
        )
        self.assertFalse(receipt["supported"])
        self.assertFalse(receipt["model_call_allowed"])
        self.assertEqual(
            receipt["rejection_reason"],
            "multi_system_or_multi_module_scope",
        )

    def test_actual_upstream_builds_context_guidance_and_canonical_b(self) -> None:
        case = get_case_set()["cases"][0]
        saved: dict[str, object] = {}
        with patch(
            "req2web_runtime.phase4_canonical_full_flow._write_json",
            side_effect=lambda path, value: saved.__setitem__(
                path.name,
                value,
            ),
        ):
            upstream = _build_upstream(
                case=case,
                index_dir=ROOT / "data/processed/rag",
                output_root=Path("unused"),
            )
        receipt = upstream["receipt"]
        self.assertEqual(receipt["requirement_provider_call_count"], 1)
        self.assertEqual(receipt["retriever_call_count"], 5)
        self.assertFalse(receipt["prewritten_upstream_fields_consumed"])
        self.assertTrue(receipt["same_context_guidance_used_for_downstream"])
        self.assertFalse(receipt["synthetic_downstream_context_rebuilt"])
        self.assertEqual(
            saved["canonical_b_adapter_receipt.json"][
                "explicit_user_input_identity"
            ],
            upstream["adaptation"].receipt["explicit_user_input_identity"],
        )
        b_input = upstream["adaptation"].b_input
        self.assertEqual(
            b_input["requirement"],
            case["requirement"],
        )
        self.assertEqual(
            b_input["requirement_summary"],
            upstream["context"].requirement_summary,
        )
        saved_receipt = saved["upstream_receipt.json"]
        self.assertEqual(
            saved_receipt["receipt_identity"],
            receipt["receipt_identity"],
        )

    def test_raw_requirement_reaches_the_same_real_graph_topology(self) -> None:
        case = get_case_set()["cases"][0]
        with patch(
            "req2web_runtime.phase4_canonical_full_flow._write_json",
        ):
            upstream = _build_upstream(
                case=case,
                index_dir=ROOT / "data/processed/rag",
                output_root=Path("unused"),
            )
        observed: dict[str, object] = {}

        def node_executor(node_id, authority_state, authority_projection):
            output = phase4_synthetic_fixture_output(
                node_id,
                authority_state,
            )
            raw = json.dumps(
                output,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return {
                "schema_version": (
                    "req2web.phase4.real_model_node_execution.v1"
                ),
                "node_id": node_id,
                "status": "validated",
                "source_kind": REAL_MODEL_SOURCE_KIND,
                "generate_call_count": 1,
                "raw_capture": make_real_model_raw_capture(raw),
                "attempt_identity": make_identity(
                    {
                        "node_id": node_id,
                        "raw_sha256": make_real_model_raw_capture(raw)[
                            "sha256"
                        ],
                    },
                    revision="test.canonical_full_flow_attempt.v1",
                ),
                "output": output,
                "raw_model_contract_success": True,
                "normalized_node_contract_success": True,
                "failure": None,
            }

        def delivery_executor(graph_state, page_spec, assembly_report):
            observed["context"] = upstream["context"].to_dict()
            observed["guidance"] = upstream["guidance"].to_dict()
            observed["page_spec_summary"] = page_spec["summary"]
            observed["upstream_binding"] = graph_state["upstream_binding"]
            return {
                "graph_delivery_success": True,
                "terminal_status": "synthetic_continuity_pass",
                "delivery": {
                    "scripted_acceptance": "synthetic_test_executor",
                },
                "final_result_identity": make_identity(
                    assembly_report,
                    revision="test.synthetic_delivery.v1",
                ),
            }

        runtime = Phase4RealModelGraphRuntime(
            node_executor=node_executor,
            context=upstream["context"],
            guidance=upstream["guidance"],
            delivery_executor=delivery_executor,
        )
        state = create_real_model_graph_state(
            run_id="canonical-full-flow-continuity-test",
            b_input=upstream["adaptation"].b_input,
            upstream_binding=upstream["receipt"],
        )
        result = runtime.invoke(
            state,
            thread_id="canonical-full-flow-continuity-test",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            observed["page_spec_summary"],
            upstream["context"].requirement_summary,
        )
        self.assertEqual(
            observed["upstream_binding"]["receipt_identity"],
            upstream["receipt"]["receipt_identity"],
        )
        self.assertEqual(
            observed["guidance"],
            upstream["guidance"].to_dict(),
        )

    def test_parent_rejects_unapproved_case_count_before_path_access(self) -> None:
        with self.assertRaisesRegex(
            Phase4CanonicalFullFlowError,
            "max_cases",
        ):
            run_phase4_canonical_full_flow(
                model_root=Path("missing-model"),
                integrity_evidence=Path("missing-integrity"),
                index_dir=Path("missing-index"),
                result_root=Path("missing-result"),
                confirm_canonical_full_flow=True,
                max_cases=4,
            )


if __name__ == "__main__":
    unittest.main()
