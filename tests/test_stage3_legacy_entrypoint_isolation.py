"""Focused checks that legacy Stage 3 CLIs cannot look like active defaults."""
from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    "run_stage3_trusted_remote_executor.py": "historical_execution",
    "run_stage3_trusted_remote_two_case.py": "replay_only",
    "run_stage3_qwen27b_recovery.py": "historical_execution",
    "evaluate_stage3_qwen27b_recovery.py": "replay_only",
    "run_stage3_qwen36_diagnostic.py": "diagnostic_only",
    "evaluate_stage3_qwen36_diagnostic.py": "diagnostic_only",
    "run_stage3_autodl_a1_executor.py": "historical_execution",
    "run_stage3_autodl_a1_operational.py": "replay_only",
    "run_stage3_autodl_a1_preflight.py": "historical_execution",
    "run_stage3_autodl_a1_production_gate.py": "replay_only",
}


class Stage3LegacyEntrypointIsolationTests(unittest.TestCase):
    def test_help_and_startup_notice_mark_every_entrypoint_inactive(self):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "src")
        for filename, expected_mode in SCRIPTS.items():
            with self.subTest(filename=filename):
                path = ROOT / "scripts" / filename
                completed = subprocess.run(
                    [sys.executable, str(path), "--help"],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    encoding="utf-8",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("HISTORICAL STAGE 3 ENTRYPOINT", completed.stdout)
                self.assertIn(
                    "active_default_entry=false", completed.stdout
                )
                self.assertIn(
                    f"entrypoint_mode={expected_mode}", completed.stdout
                )

                namespace = runpy.run_path(
                    str(path),
                    run_name="stage3_legacy_entrypoint_isolation_test",
                )
                captured = StringIO()
                with redirect_stderr(captured):
                    namespace["_emit_entrypoint_notice"]()
                notice = json.loads(captured.getvalue())
                self.assertEqual(
                    notice,
                    {
                        "active_default_entry": False,
                        "entrypoint_mode": expected_mode,
                        "entrypoint_status": "historical",
                    },
                )


if __name__ == "__main__":
    unittest.main()
