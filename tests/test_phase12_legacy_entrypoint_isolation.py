from __future__ import annotations

import importlib.util
import inspect
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_CLASSIFICATIONS = {
    "scripts/run_agent_chain.py": "component_only",
    "scripts/run_page_spec.py": "component_only",
    "scripts/run_page_renderer.py": "component_only",
    "scripts/run_consistency_check.py": "component_only",
    "scripts/build_result_package.py": "component_only",
    "scripts/run_retrieval_guidance.py": "replay_only",
    "scripts/run_guided_page_spec.py": "replay_only",
    "scripts/run_retrieval_influence.py": "replay_only",
    "scripts/run_demo_v2_regression.py": "frozen_regression",
}


def _load_script(relative_path: str) -> ModuleType:
    path = ROOT / relative_path
    module_name = "phase12_legacy_" + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load script module: {relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Phase12LegacyEntrypointIsolationTests(unittest.TestCase):
    def test_all_legacy_entrypoints_expose_stable_non_default_metadata(self) -> None:
        for relative_path, classification in EXPECTED_CLASSIFICATIONS.items():
            with self.subTest(script=relative_path):
                module = _load_script(relative_path)
                self.assertEqual(
                    module.ENTRYPOINT_ISOLATION_SCHEMA,
                    "req2web.entrypoint.isolation.v1",
                )
                self.assertEqual(module.ENTRYPOINT_CLASSIFICATION, classification)
                self.assertIs(module.ACTIVE_DEFAULT_ENTRY, False)
                self.assertIn(
                    f"entrypoint_classification={classification}",
                    module.ENTRYPOINT_HELP,
                )
                self.assertIn("active_default_entry=false", module.ENTRYPOINT_HELP)
                self.assertIn("emit_entrypoint_isolation(", inspect.getsource(module.main))

    def test_normal_startup_summary_is_machine_readable_stderr_only(self) -> None:
        for relative_path, classification in EXPECTED_CLASSIFICATIONS.items():
            with self.subTest(script=relative_path):
                module = _load_script(relative_path)
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    if classification == "frozen_regression":
                        module.emit_entrypoint_isolation(execution_mode="build")
                    else:
                        module.emit_entrypoint_isolation()
                self.assertEqual(stdout.getvalue(), "")
                payload = json.loads(stderr.getvalue())
                self.assertEqual(
                    payload["schema"],
                    "req2web.entrypoint.isolation.v1",
                )
                self.assertEqual(payload["event"], "entrypoint_isolation")
                self.assertEqual(payload["entrypoint"], relative_path)
                self.assertEqual(
                    payload["entrypoint_classification"],
                    classification,
                )
                self.assertIs(payload["active_default_entry"], False)
                self.assertTrue(payload["scope_notice"])

    def test_frozen_regression_build_and_validate_only_remain_distinct(self) -> None:
        module = _load_script("scripts/run_demo_v2_regression.py")
        build = module.entrypoint_isolation_metadata(execution_mode="build")
        validate = module.entrypoint_isolation_metadata(
            execution_mode="validate_only"
        )

        self.assertEqual(build["entrypoint_classification"], "frozen_regression")
        self.assertEqual(validate["entrypoint_classification"], "frozen_regression")
        self.assertEqual(build["execution_mode"], "build")
        self.assertEqual(validate["execution_mode"], "validate_only")
        self.assertIn("Build", build["mode_notice"])
        self.assertIn("existing on-disk", validate["mode_notice"])
        self.assertNotEqual(build["mode_notice"], validate["mode_notice"])


if __name__ == "__main__":
    unittest.main()
