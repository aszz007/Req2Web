from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_inspector.canonical_run import (  # noqa: E402
    CanonicalInspectorRunStore,
    InspectorCanonicalRunError,
)
from req2web_orchestration.phase4_graph import (  # noqa: E402
    phase4_synthetic_fixture_output,
    synthetic_commerce_b_input,
)


class CanonicalInspectorRunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / f".inspector-canonical-test-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.model_root = self.root / "model"
        self.model_root.mkdir()
        self.evidence = self.root / "integrity.json"
        self.evidence.write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _raw_node_generator(node_id, authority_state):
        return json.dumps(
            phase4_synthetic_fixture_output(node_id, authority_state),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _assist(value):
        return {
            "status": "advisory_available",
            "input": value,
            "sidecar": {
                "schema_version": "req2web.semantic_requirement_assist.sidecar.v1",
                "sidecar_status": "advisory_available",
                "advisory": {"ambiguities": [], "suggestions": []},
            },
        }

    @staticmethod
    def _browser(**kwargs):
        output_root = Path(kwargs["output_root"])
        output_root.mkdir()
        (output_root / "browser_screenshot.png").write_bytes(b"test-screenshot")
        (output_root / "browser_execution_report.json").write_text(
            "{}", encoding="utf-8"
        )
        (output_root / "case_browser_audit.json").write_text(
            "{}", encoding="utf-8"
        )
        return {
            "browser_status": "pass",
            "browser_execution_status": "pass",
            "page_spec_conformance_status": "pass",
            "real_browser_executed": True,
        }

    @staticmethod
    def _semantic(**kwargs):
        result_root = Path(kwargs["result_root"])
        result_root.mkdir()
        (result_root / "run_summary.json").write_text("{}", encoding="utf-8")
        return {
            "status": "semantic_alignment_complete_pending_owner_review",
            "semantic_alignment_executed": True,
            "automatic_retry_count": 0,
        }

    def _store(self) -> CanonicalInspectorRunStore:
        return CanonicalInspectorRunStore(
            root=self.root / "runs",
            index_dir=ROOT / "data/processed/rag",
            model_root=self.model_root,
            integrity_evidence=self.evidence,
            requirement_assist_runner=self._assist,
            browser_runner=self._browser,
            semantic_runner=self._semantic,
            _raw_node_generator=self._raw_node_generator,
        )

    def test_capability_exposes_one_canonical_single_flight_route(self) -> None:
        capability = self._store().capability()
        self.assertTrue(capability["single_flight"])
        self.assertTrue(capability["requirement_assist_available"])
        self.assertTrue(capability["real_browser_acceptance_available"])
        self.assertTrue(capability["semantic_acceptance_available"])
        self.assertFalse(capability["b_aux_consumed_by_f1_f4"])
        self.assertEqual(capability["profile"]["profile_name"], "local_low_gpu_nf4")

    def test_explicit_model_confirmation_is_required(self) -> None:
        case = synthetic_commerce_b_input()
        with self.assertRaisesRegex(
            InspectorCanonicalRunError,
            "explicit local model action confirmation",
        ):
            self._store().create(
                {
                    "requirement": case["requirement"],
                    "target_device": case["target_device"],
                    "task_type": case["task_type"],
                    "constraints": case["constraints"],
                }
            )

    def test_async_full_route_reaches_package_browser_and_semantic_stages(self) -> None:
        case = synthetic_commerce_b_input()
        store = self._store()
        inventory = {
            "inventory_identity": {
                "identity_kind": "canonical_json",
                "sha256": "sha256:" + ("c" * 64),
                "byte_length": 1,
                "revision": "test.inventory.v1",
            }
        }
        with patch(
            "req2web_runtime.phase4_local_qwen_langgraph_integrated.validate_model_inventory_metadata",
            return_value=inventory,
        ):
            created = store.create(
                {
                    "requirement": case["requirement"],
                    "target_device": case["target_device"],
                    "task_type": case["task_type"],
                    "constraints": case["constraints"],
                    "confirm_local_model_action": True,
                    "run_requirement_assist": True,
                    "run_browser_acceptance": True,
                    "run_semantic_acceptance": True,
                }
            )
            record = store.wait(created["run_id"], timeout=30)

        self.assertIn(
            record["status"],
            {
                "completed_model_delivery",
                "completed_delivery_via_same_case_g0_fallback",
            },
            msg=json.dumps(record, ensure_ascii=False, indent=2),
        )
        statuses = {
            item["stage_id"]: item["status"] for item in record["stages"]
        }
        self.assertEqual(statuses["b_aux"], "completed_advisory_only")
        self.assertEqual(statuses["model"], "not_executed_test_generator")
        self.assertEqual(
            list(record["live_attempts"]),
            ["F1", "F2", "F3", "F4"],
        )
        self.assertTrue(
            all(
                item["generate_call_count"] == 1
                and item["automatic_retry_count"] == 0
                for item in record["live_attempts"].values()
            )
        )
        self.assertEqual(statuses["browser"], "pass")
        self.assertEqual(
            statuses["semantic"],
            "semantic_alignment_complete_pending_owner_review",
        )
        self.assertEqual(record["authority_boundary"]["automatic_retry_count"], 0)
        self.assertFalse(record["authority_boundary"]["b_aux_writeback"])
        self.assertTrue(
            store.artifact_path(
                record["run_id"],
                "package/page/index.html",
            ).is_file()
        )
        self.assertTrue(
            store.artifact_path(record["run_id"], "result-package.zip").is_file()
        )
        self.assertTrue(
            store.artifact_path(
                record["run_id"], "evidence/browser-screenshot.png"
            ).is_file()
        )
        self.assertTrue(
            store.artifact_path(
                record["run_id"], "evidence/browser-audit.json"
            ).is_file()
        )
        self.assertTrue(
            store.artifact_path(
                record["run_id"], "evidence/node-F1-output.json"
            ).is_file()
        )
        self.assertTrue(
            store.artifact_path(
                record["run_id"], "evidence/semantic-summary.json"
            ).is_file()
        )
        with self.assertRaisesRegex(
            InspectorCanonicalRunError,
            "unsupported",
        ):
            store.artifact_path(record["run_id"], "evidence/other.json")

    def test_objective_browser_failure_blocks_semantic_agent_without_calling_it(self) -> None:
        case = synthetic_commerce_b_input()
        semantic_called = False

        def browser_objective_fail(**kwargs):
            output_root = Path(kwargs["output_root"])
            output_root.mkdir()
            (output_root / "browser_execution_report.json").write_text(
                "{}", encoding="utf-8"
            )
            (output_root / "case_browser_audit.json").write_text(
                "{}", encoding="utf-8"
            )
            return {
                "browser_status": "fail",
                "browser_execution_status": "pass",
                "page_spec_conformance_status": "fail",
                "real_browser_executed": True,
            }

        def semantic_must_not_run(**_kwargs):
            nonlocal semantic_called
            semantic_called = True
            raise AssertionError("semantic Agent crossed the objective browser gate")

        store = CanonicalInspectorRunStore(
            root=self.root / "runs-objective-fail",
            index_dir=ROOT / "data/processed/rag",
            model_root=self.model_root,
            integrity_evidence=self.evidence,
            requirement_assist_runner=self._assist,
            browser_runner=browser_objective_fail,
            semantic_runner=semantic_must_not_run,
            _raw_node_generator=self._raw_node_generator,
        )
        inventory = {
            "inventory_identity": {
                "identity_kind": "canonical_json",
                "sha256": "sha256:" + ("d" * 64),
                "byte_length": 1,
                "revision": "test.inventory.v1",
            }
        }
        with patch(
            "req2web_runtime.phase4_local_qwen_langgraph_integrated.validate_model_inventory_metadata",
            return_value=inventory,
        ):
            created = store.create(
                {
                    "requirement": case["requirement"],
                    "target_device": case["target_device"],
                    "task_type": case["task_type"],
                    "constraints": case["constraints"],
                    "confirm_local_model_action": True,
                    "run_requirement_assist": True,
                    "run_browser_acceptance": True,
                    "run_semantic_acceptance": True,
                }
            )
            record = store.wait(created["run_id"], timeout=30)

        statuses = {
            item["stage_id"]: item["status"] for item in record["stages"]
        }
        self.assertEqual(statuses["browser"], "fail")
        self.assertEqual(
            statuses["semantic"],
            "not_executed_objective_browser_gate",
        )
        self.assertFalse(semantic_called)
        self.assertFalse(record["semantic"]["semantic_alignment_executed"])
        self.assertEqual(
            record["semantic"]["page_spec_conformance_status"],
            "fail",
        )


if __name__ == "__main__":
    unittest.main()
