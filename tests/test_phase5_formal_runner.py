from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import time
import unittest
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.phase5_formal_runner import (  # noqa: E402
    NODE_ORDER,
    Phase5FormalRunnerError,
    SyntheticPhase5CaseWorker,
    _record_id,
    build_phase5_local_assembly_bindings,
    run_phase5_formal_runner,
    synthetic_phase5_worker_factory,
    validate_phase5_formal_result_root,
)
from req2web_runtime.phase5_sealed_action_package import (  # noqa: E402
    create_phase5_sealed_action_package,
)


FIXTURE = ROOT / "fixtures" / "phase5_sealed_action_package_synthetic_v1.json"


class _InvalidF2Worker(SyntheticPhase5CaseWorker):
    def generate(self, **kwargs: object) -> bytes:
        raw = super().generate(**kwargs)  # type: ignore[arg-type]
        if kwargs["node_id"] == "F2":
            return b'{"states":[]}'
        return raw


def _invalid_f2_factory(row: dict[str, object]) -> _InvalidF2Worker:
    return _InvalidF2Worker(
        worker_id=f"synthetic-invalid-f2-{int(row['row_order']):04d}"
    )


class Phase5FormalRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.package = create_phase5_sealed_action_package(
            json.loads(FIXTURE.read_text(encoding="utf-8"))
        )
        self.payload = self.package.to_dict()
        self.temp = ROOT / f".phase5-formal-runner-test-{uuid.uuid4().hex}"
        self.temp.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_synthetic_runner_executes_all_rows_raw_first_without_retry(self) -> None:
        result_root = (self.temp / "result").resolve()
        summary = run_phase5_formal_runner(
            package=self.package,
            result_root=result_root,
            worker_factory=synthetic_phase5_worker_factory,
            allow_synthetic_validation_only=True,
        )
        self.assertEqual(summary["runtime_row_count"], 3)
        self.assertEqual(summary["terminal_row_count"], 3)
        self.assertEqual(summary["actual_generate_started_count"], 12)
        self.assertEqual(
            summary["status_counts"],
            {
                "assembled_candidate_pending_owner_evaluation": 3,
                "failed_closed": 0,
                "incomplete_experiment": 0,
            },
        )
        for key in (
            "automatic_retry_count",
            "normalization_count",
            "repair_count",
            "fallback_count",
            "post_generation_semantic_adjustment_count",
        ):
            self.assertEqual(summary[key], 0, key)
        self.assertFalse(summary["owner_evaluation_executed"])
        self.assertFalse(summary["formal_quality_claimed"])
        self.assertEqual(
            validate_phase5_formal_result_root(
                package=self.package,
                result_root=result_root,
            ),
            summary,
        )

        case_roots = sorted((result_root / "cases").iterdir())
        self.assertEqual(len(case_roots), 3)
        for case_root in case_roots:
            case = json.loads(
                (case_root / "case_result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                case["status"],
                "assembled_candidate_pending_owner_evaluation",
            )
            self.assertEqual(case["generate_started_count"], 4)
            self.assertEqual(
                case["supervisor_receipt"]["generate_calls"],
                {node: 1 for node in NODE_ORDER},
            )
            for node in NODE_ORDER:
                attempt = case_root / "attempts" / node
                for filename in (
                    "config.json",
                    "input.json",
                    "pre_call_record.json",
                    "prompt.json",
                    "request.json",
                    "generation_started.json",
                    "raw_response.bin",
                    "registered_graph_state.json",
                    "attempt_result.json",
                ):
                    self.assertTrue((attempt / filename).is_file(), filename)
                receipt = json.loads(
                    (attempt / "attempt_result.json").read_text(encoding="utf-8")
                )
                self.assertEqual(receipt["status"], "raw_contract_pass")
                self.assertTrue(receipt["generate_started"])
                self.assertFalse(receipt["automatic_retry"])

    def test_provider_artifacts_exclude_local_row_intervention_and_gold(self) -> None:
        result_root = (self.temp / "visibility").resolve()
        run_phase5_formal_runner(
            package=self.package,
            result_root=result_root,
            worker_factory=synthetic_phase5_worker_factory,
            allow_synthetic_validation_only=True,
        )
        for path in (result_root / "cases").glob("*/attempts/*/input.json"):
            encoded = path.read_text(encoding="utf-8")
            for prohibited in (
                "matrix_row_id",
                "runtime_case_id",
                "opaque_case_ref",
                "remove_critical_role",
                "irrelevant_evidence",
                '"gold"',
                "owner_score",
            ):
                self.assertNotIn(prohibited, encoded)
            value = json.loads(encoded)
            self.assertTrue(value["provider_case_ref"].startswith(
                "phase5-provider-case-"
            ))
            self.assertTrue(value["provider_request_ref"].startswith(
                "phase5-provider-request-"
            ))

    def test_f3_and_f4_plans_are_pre_call_prompt_material(self) -> None:
        result_root = (self.temp / "plans").resolve()
        run_phase5_formal_runner(
            package=self.package,
            result_root=result_root,
            worker_factory=synthetic_phase5_worker_factory,
            allow_synthetic_validation_only=True,
        )
        first_case = sorted((result_root / "cases").iterdir())[0]
        f3 = json.loads(
            (first_case / "attempts" / "F3" / "prompt.json").read_text(
                encoding="utf-8"
            )
        )
        f4 = json.loads(
            (first_case / "attempts" / "F4" / "prompt.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(f3["required_interaction_plan"])
        self.assertTrue(f4["required_acceptance_target_plan"])
        self.assertEqual(
            [row["position"] for row in f3["required_interaction_plan"]],
            list(range(len(f3["required_interaction_plan"]))),
        )
        self.assertEqual(
            [row["position"] for row in f4["required_acceptance_target_plan"]],
            list(range(len(f4["required_acceptance_target_plan"]))),
        )

    def test_invalid_raw_is_preserved_and_does_not_retry(self) -> None:
        result_root = (self.temp / "invalid").resolve()
        summary = run_phase5_formal_runner(
            package=self.package,
            result_root=result_root,
            worker_factory=_invalid_f2_factory,
            allow_synthetic_validation_only=True,
        )
        self.assertEqual(summary["actual_generate_started_count"], 6)
        self.assertEqual(summary["automatic_retry_count"], 0)
        self.assertEqual(summary["status_counts"]["failed_closed"], 3)
        for case_root in (result_root / "cases").iterdir():
            raw_path = case_root / "attempts" / "F2" / "raw_response.bin"
            self.assertEqual(raw_path.read_bytes(), b'{"states":[]}')
            result = json.loads(
                (
                    case_root / "attempts" / "F2" / "attempt_result.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(result["status"], "failed_closed")
            self.assertTrue(result["generate_started"])
            self.assertEqual(result["generate_call_count"], 1)
            self.assertFalse((case_root / "attempts" / "F3").exists())

    def test_terminal_resume_returns_same_summary_without_new_calls(self) -> None:
        result_root = (self.temp / "resume").resolve()
        first = run_phase5_formal_runner(
            package=self.package,
            result_root=result_root,
            worker_factory=synthetic_phase5_worker_factory,
            allow_synthetic_validation_only=True,
        )
        second = run_phase5_formal_runner(
            package=self.package,
            result_root=result_root,
            worker_factory=lambda row: self.fail("worker must not be created"),
            allow_synthetic_validation_only=True,
            resume_existing=True,
        )
        self.assertEqual(second, first)
        self.assertEqual(
            len(list((result_root / "call-ledger").glob("*.json"))),
            12,
        )

    def test_partial_case_is_never_resumed(self) -> None:
        result_root = (self.temp / "partial").resolve()
        run_phase5_formal_runner(
            package=self.package,
            result_root=result_root,
            worker_factory=synthetic_phase5_worker_factory,
            allow_synthetic_validation_only=True,
        )
        (result_root / "run_summary.json").unlink()
        (result_root / "aggregate_call_ledger.json").unlink()
        second_case = sorted((result_root / "cases").iterdir())[1]
        (second_case / "case_result.json").unlink()
        with self.assertRaisesRegex(
            Phase5FormalRunnerError,
            "partial formal case",
        ):
            run_phase5_formal_runner(
                package=self.package,
                result_root=result_root,
                worker_factory=synthetic_phase5_worker_factory,
                allow_synthetic_validation_only=True,
                resume_existing=True,
            )

    def test_case_boundary_resume_does_not_reset_time_or_cost_clock(self) -> None:
        result_root = (self.temp / "clock-resume").resolve()
        run_phase5_formal_runner(
            package=self.package,
            result_root=result_root,
            worker_factory=synthetic_phase5_worker_factory,
            allow_synthetic_validation_only=True,
        )
        (result_root / "run_summary.json").unlink()
        (result_root / "aggregate_call_ledger.json").unlink()
        third_case = sorted((result_root / "cases").iterdir())[2]
        shutil.rmtree(third_case)
        for index in range(9, 13):
            (
                result_root
                / "call-ledger"
                / f"generate-start-{index:06d}.json"
            ).unlink()

        clock_path = result_root / "run_clock.json"
        clock = json.loads(clock_path.read_text(encoding="utf-8"))
        clock["started_epoch_seconds"] = time.time() - 8_000
        body = {key: clock[key] for key in clock if key != "run_clock_id"}
        clock["run_clock_id"] = _record_id("phase5-formal-run-clock", body)
        clock_path.write_text(
            json.dumps(
                clock,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        summary = run_phase5_formal_runner(
            package=self.package,
            result_root=result_root,
            worker_factory=lambda row: self.fail("expired run must not load worker"),
            allow_synthetic_validation_only=True,
            resume_existing=True,
        )
        self.assertEqual(summary["actual_generate_started_count"], 8)
        self.assertEqual(summary["status_counts"]["failed_closed"], 1)
        self.assertEqual(
            len(list((result_root / "call-ledger").glob("*.json"))),
            8,
        )

    def test_local_assembly_binding_tracks_intervention_without_gold(self) -> None:
        rows = self.payload["runtime_rows"]
        removed = build_phase5_local_assembly_bindings(
            self.package,
            matrix_row_id=rows[1]["matrix_row_id"],
        )[0]
        irrelevant = build_phase5_local_assembly_bindings(
            self.package,
            matrix_row_id=rows[2]["matrix_row_id"],
        )[0]
        self.assertTrue(
            removed.retrieval_results["interaction_flow"][0]["doc_id"].startswith(
                "phase5-local-absence:"
            )
        )
        irrelevant_signals = {
            item["summary"]
            for item in irrelevant.retrieval_results["interaction_flow"]
        }
        self.assertTrue(
            any("media carousel" in summary for summary in irrelevant_signals)
        )
        encoded = json.dumps(
            irrelevant.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        )
        self.assertNotIn('"gold"', encoded)
        self.assertNotIn("owner_score", encoded)

    def test_synthetic_execution_requires_explicit_confirmation(self) -> None:
        with self.assertRaisesRegex(
            Phase5FormalRunnerError,
            "explicit validation-only confirmation",
        ):
            run_phase5_formal_runner(
                package=self.package,
                result_root=(self.temp / "not-confirmed").resolve(),
                worker_factory=synthetic_phase5_worker_factory,
            )

    def test_result_replay_rejects_summary_or_case_inventory_tamper(self) -> None:
        result_root = (self.temp / "replay-tamper").resolve()
        run_phase5_formal_runner(
            package=self.package,
            result_root=result_root,
            worker_factory=synthetic_phase5_worker_factory,
            allow_synthetic_validation_only=True,
        )
        summary_path = result_root / "run_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["status_counts"]["failed_closed"] = 1
        summary_path.write_text(
            json.dumps(
                summary,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            Phase5FormalRunnerError,
            "summary replay drifted",
        ):
            validate_phase5_formal_result_root(
                package=self.package,
                result_root=result_root,
            )


if __name__ == "__main__":
    unittest.main()
