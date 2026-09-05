from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_req2web_inspector.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("req2web_inspector_runner", RUNNER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load Inspector runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Req2WebInspectorStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = _load_runner()

    def test_portable_preflight_is_read_only_and_ready(self) -> None:
        args = self.runner.build_parser().parse_args(["--preflight-only"])
        result = self.runner._startup_preflight(args)

        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["model_loaded"])
        self.assertEqual(result["model_call_count"], 0)
        self.assertFalse(result["external_service_called"])

    def test_model_preflight_fails_closed_when_model_inputs_are_missing(self) -> None:
        args = self.runner.build_parser().parse_args(
            ["--preflight-only", "--enable-local-canonical-run"]
        )
        result = self.runner._startup_preflight(args)
        checks = {item["name"]: item for item in result["checks"]}

        self.assertFalse(result["ready"])
        self.assertFalse(checks["canonical_model_root"]["passed"])
        self.assertFalse(checks["canonical_model_integrity_evidence"]["passed"])
        self.assertFalse(result["model_loaded"])
        self.assertEqual(result["model_call_count"], 0)

    def test_read_only_replay_does_not_require_the_retrieval_index(self) -> None:
        args = self.runner.build_parser().parse_args(
            [
                "--preflight-only",
                "--read-only",
                "--index-dir",
                str(ROOT / "does-not-exist"),
            ]
        )
        result = self.runner._startup_preflight(args)
        checks = {item["name"]: item for item in result["checks"]}

        self.assertTrue(result["ready"])
        self.assertTrue(checks["retrieval_index"]["passed"])
        self.assertFalse(checks["retrieval_index"]["blocking"])

    def test_default_server_assets_use_current_final_inspector_source(self) -> None:
        page = self.runner._CURRENT_UI_ASSETS["/"][1].decode("utf-8")
        script = self.runner._CURRENT_UI_ASSETS["/app.js"][1].decode("utf-8")

        self.assertIn("Req2Web Inspector", page)
        self.assertNotIn("Human handoff", page)
        self.assertNotIn("Retrieval laboratory", page)
        self.assertIn("Copy selected advice into constraints", script)
        self.assertIn("Stop safely after the current stage", script)

    def test_repository_demo_does_not_require_historical_replay(self) -> None:
        args = self.runner.build_parser().parse_args(["--repository-demo"])

        self.assertTrue(args.repository_demo)
        self.assertFalse(args.read_only)
        self.assertFalse(args.validate_only)

        script = self.runner._CURRENT_UI_ASSETS["/app.js"][1].decode("utf-8")
        page = self.runner._CURRENT_UI_ASSETS["/"][1].decode("utf-8")
        previous = self.runner._InspectorHandler.precomputed_replay_available
        try:
            self.runner._InspectorHandler.precomputed_replay_available = False
            handler = self.runner._InspectorHandler.__new__(
                self.runner._InspectorHandler
            )
            self.assertEqual(
                handler._capabilities()["precomputed_replay"],
                "not_loaded_repository_demo",
            )
        finally:
            self.runner._InspectorHandler.precomputed_replay_available = previous
        self.assertIn("Repository demo", script)
        self.assertIn("data-replay-only", page)

    def test_hosted_dependency_lock_matches_runtime_receipt(self) -> None:
        receipt = json.loads(
            (ROOT / "docs" / "phase4_langgraph_dependency_acquisition_receipt.json")
            .read_text(encoding="utf-8")
        )
        expected = [
            f"{row['distribution']}=={row['version']}"
            for row in receipt["resolved_closure"]
        ]
        actual = [
            line
            for line in (
                ROOT / "requirements-phase4-agent-lock.txt"
            ).read_text(encoding="utf-8").splitlines()
            if line
        ]

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
