from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime import phase4_remote_qwen_f4_direct_stability as direct
from req2web_runtime import phase4_remote_qwen_fresh_integrated as fresh
from req2web_runtime import phase4_remote_qwen_stability as stability
from req2web_runtime.phase4_stability_cases import get_stability_case_set


def _identity(label: str) -> dict[str, object]:
    return fresh._identity({"label": label}, revision="test.v1")


def _case_result(
    *,
    run_id: str,
    index: int,
    case: dict[str, object],
    child_root: Path,
) -> dict[str, object]:
    root = {
        "schema_version": stability.STABILITY_CASE_RESULT_SCHEMA_VERSION,
        "experiment_run_id": run_id,
        "case_index": index,
        "case_id": case["case_id"],
        "request_id": case["request_id"],
        "child_run_id": stability._expected_child_run_id(run_id, index),
        "child_result_root": str(child_root.resolve(strict=False)),
        "parent_experiment_binding": None,
        "profile_identity": _identity("profile"),
        "model_inventory_identity": _identity("inventory"),
        "status": "delivery_terminal_success",
        "per_node_generate_calls": {"F1": 0, "F2": 0, "F3": 0, "F4": 1},
        "per_node_raw_contract_pass": {
            "F1": False,
            "F2": False,
            "F3": False,
            "F4": True,
        },
        "per_node_failure_codes": {
            "F1": None,
            "F2": None,
            "F3": None,
            "F4": None,
        },
        "total_model_generate_calls": 1,
        "f4_called": True,
        "f4_raw_direct_pass": True,
        "f4_normalization_count": 0,
        "f4_normalization_used": False,
        "normalized_node_contract_success": True,
        "model_success": True,
        "composition_pass": True,
        "assembler_pass": True,
        "downstream_status": "first_pass_success",
        "downstream_first_pass_success": True,
        "repair_attempted": False,
        "repair_success": False,
        "repair_failed": False,
        "deterministic_repair_success": False,
        "fallback_attempted": False,
        "g0_fallback_success": False,
        "delivery_success": True,
        "system_adjustment_used": False,
        "failure_code": None,
        "exception_type": None,
        "retry_count": 0,
        "automatic_retry": False,
        "experiment_mode": direct.F4_DIRECT_MODE,
        "prompt_revision": direct.F4_DIRECT_PROMPT_REVISION,
        "baseline_binding": {"kind": "history"},
        "historical_per_node_generate_calls": dict(
            direct.F4_DIRECT_HISTORICAL_PER_CASE
        ),
        "aggregate_per_node_generate_calls": {
            "F1": 1,
            "F2": 1,
            "F3": 2,
            "F4": 3,
        },
    }
    return {
        **root,
        "case_result_identity": fresh._identity(
            root,
            revision=stability.STABILITY_CASE_RESULT_SCHEMA_VERSION,
        ),
    }


class Phase4RemoteQwenF4DirectStabilityTests(unittest.TestCase):
    def test_policy_freezes_f4_only_resume_and_a07a_route(self) -> None:
        case_set = get_stability_case_set()
        policy = direct._create_policy(
            run_id="p4-05-f4-direct-test",
            case_set=case_set,
            history_binding={"history": "bound"},
            profile_identity=_identity("profile"),
            model_inventory_identity=_identity("inventory"),
        )

        self.assertEqual(policy["prompt_nodes"], ["F4"])
        self.assertEqual(policy["resume_prefix"], fresh.P4_05_RESUME_PREFIX_F1_F3)
        self.assertEqual(policy["new_call_nodes"], ["F4"])
        self.assertEqual(policy["total_new_call_cap"], 10)
        self.assertEqual(
            policy["downstream_policy"],
            "a07a_direct_first_pass_v1",
        )
        self.assertEqual(policy["a07b_status"], "not_executed_by_policy")
        self.assertIs(policy["automatic_retry"], False)

    def test_run_reuses_two_history_roots_and_generates_only_f4(self) -> None:
        case_set = get_stability_case_set()
        run_id = "p4-05-f4-direct-run-test"
        profile_identity = _identity("profile")
        inventory_identity = _identity("inventory")
        policy = direct._create_policy(
            run_id=run_id,
            case_set=case_set,
            history_binding={"history": "bound"},
            profile_identity=profile_identity,
            model_inventory_identity=inventory_identity,
        )
        result_root = ROOT / "virtual-f4-direct-result"
        baseline_root = ROOT
        predecessor_root = SRC
        history_binding = {
            "baseline_result_root": str(baseline_root),
            "predecessor_result_root": str(predecessor_root),
            "historical_aggregate_per_node_generate_started_count": {
                "F1": 10,
                "F2": 10,
                "F3": 20,
                "F4": 20,
            },
        }
        prepared = {
            "result_root": result_root,
            "run_id": run_id,
            "case_set": case_set,
            "policy": policy,
            "history_binding": history_binding,
            "summary": None,
        }
        generated_results: list[dict[str, object]] = []

        def summarize(**kwargs: object) -> dict[str, object]:
            value = _case_result(
                run_id=run_id,
                index=int(kwargs["index"]),
                case=dict(kwargs["case"]),
                child_root=Path(kwargs["child_root"]),
            )
            generated_results.append(value)
            return value

        with patch.object(
            direct,
            "prepare_phase4_remote_qwen_f4_direct_stability",
            return_value=prepared,
        ), patch.object(
            stability,
            "_validate_cases_layout",
            return_value=None,
        ), patch.object(
            stability,
            "_load_progress",
            return_value=[],
        ) as load_progress, patch.object(
            stability,
            "_assert_child_root",
            side_effect=lambda **kwargs: Path(
                kwargs["child_root"]
            ).resolve(strict=False),
        ), patch.object(
            stability,
            "_validate_child_b_input",
            return_value=None,
        ), patch.object(
            stability,
            "_summarize_case",
            side_effect=summarize,
        ), patch.object(
            stability,
            "_validate_case_result",
            side_effect=lambda value, **_: copy.deepcopy(value),
        ), patch.object(
            stability,
            "_write_progress",
            return_value=None,
        ), patch.object(
            fresh,
            "_write_fsync",
            return_value=None,
        ), patch.object(
            fresh,
            "run_phase4_remote_qwen_fresh_integrated",
            return_value={"status": "delivery_terminal_success"},
        ) as run_child:
            summary = direct.run_phase4_remote_qwen_f4_direct_stability(
                model_root=ROOT,
                integrity_evidence=ROOT,
                baseline_root=baseline_root,
                predecessor_root=predecessor_root,
                result_root=result_root,
                confirm_f4_direct_stability=True,
            )

        self.assertEqual(run_child.call_count, 10)
        self.assertEqual(len(generated_results), 10)
        self.assertTrue(summary["quality_target_met"])
        self.assertEqual(summary["new_model_generate_calls"], 10)
        self.assertEqual(summary["a07b_executed_count"], 0)
        self.assertEqual(
            load_progress.call_args.kwargs["prompt_nodes"],
            ("F4",),
        )
        for call in run_child.call_args_list:
            kwargs = call.kwargs
            self.assertEqual(
                kwargs["resume_prefix"],
                fresh.P4_05_RESUME_PREFIX_F1_F3,
            )
            self.assertEqual(
                kwargs["prompt_revision"],
                direct.F4_DIRECT_PROMPT_REVISION,
            )
            self.assertEqual(len(kwargs["history_result_roots"]), 2)


if __name__ == "__main__":
    unittest.main()
