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


from req2web_runtime import phase4_remote_qwen_fresh_integrated as fresh
from req2web_runtime import phase4_remote_qwen_full_direct_stability as full
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
        "per_node_generate_calls": {"F1": 1, "F2": 1, "F3": 1, "F4": 1},
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
        "total_model_generate_calls": 4,
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
        "experiment_mode": full.FULL_DIRECT_MODE,
        "prompt_revision": full.FULL_DIRECT_PROMPT_REVISION,
        "baseline_binding": None,
        "historical_per_node_generate_calls": dict(
            full.FULL_DIRECT_ZERO_HISTORY
        ),
        "aggregate_per_node_generate_calls": {
            "F1": 1,
            "F2": 1,
            "F3": 1,
            "F4": 1,
        },
    }
    return {
        **root,
        "case_result_identity": fresh._identity(
            root,
            revision=stability.STABILITY_CASE_RESULT_SCHEMA_VERSION,
        ),
    }


class Phase4RemoteQwenFullDirectStabilityTests(unittest.TestCase):
    def test_policy_forbids_checkpoint_reuse_and_freezes_full_budget(self) -> None:
        policy = full._create_policy(
            run_id="p4-05-full-direct-test",
            case_set=get_stability_case_set(),
            profile_identity=_identity("profile"),
            model_inventory_identity=_identity("inventory"),
        )

        self.assertIs(policy["checkpoint_reuse"], False)
        self.assertEqual(policy["history_result_roots"], [])
        self.assertIsNone(policy["resume_prefix"])
        self.assertEqual(policy["new_call_nodes"], ["F1", "F2", "F3", "F4"])
        self.assertEqual(policy["total_new_call_cap"], 40)
        self.assertEqual(policy["prompt_nodes"], ["F3", "F4"])
        self.assertEqual(policy["a07b_status"], "not_executed_by_policy")

    def test_run_starts_every_node_once_and_requires_raw_first_pass(self) -> None:
        case_set = get_stability_case_set()
        run_id = "p4-05-full-direct-run-test"
        profile_identity = _identity("profile")
        inventory_identity = _identity("inventory")
        policy = full._create_policy(
            run_id=run_id,
            case_set=case_set,
            profile_identity=profile_identity,
            model_inventory_identity=inventory_identity,
        )
        result_root = ROOT / "virtual-full-direct-result"
        prepared = {
            "result_root": result_root,
            "run_id": run_id,
            "case_set": case_set,
            "policy": policy,
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
            full,
            "prepare_phase4_remote_qwen_full_direct_stability",
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
            summary = full.run_phase4_remote_qwen_full_direct_stability(
                model_root=ROOT,
                integrity_evidence=ROOT,
                result_root=result_root,
                confirm_full_direct_stability=True,
            )

        self.assertEqual(run_child.call_count, 10)
        self.assertEqual(len(generated_results), 10)
        self.assertTrue(summary["quality_target_met"])
        self.assertTrue(summary["all_nodes_raw_contract_pass"])
        self.assertEqual(summary["new_model_generate_calls"], 40)
        self.assertEqual(
            load_progress.call_args.kwargs["prompt_nodes"],
                ("F3", "F4"),
        )
        for call in run_child.call_args_list:
            kwargs = call.kwargs
            self.assertEqual(kwargs["history_result_roots"], ())
            self.assertIsNone(kwargs["resume_from_result_root"])
            self.assertIsNone(kwargs["resume_prefix"])
            self.assertEqual(
                kwargs["prompt_revision"],
                full.FULL_DIRECT_PROMPT_REVISION,
            )


if __name__ == "__main__":
    unittest.main()
