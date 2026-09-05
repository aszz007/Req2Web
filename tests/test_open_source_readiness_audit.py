from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_open_source_readiness import audit_repository, main  # noqa: E402


class OpenSourceReadinessAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = Path(tempfile.mkdtemp(prefix="req2web-readiness-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def _write(self, relative_path: str, content: str) -> None:
        target = self.temp_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def test_reports_prepublication_blockers_without_exposing_values(self) -> None:
        token = "ghp_" + "A" * 24
        self._write("README.md", "# Demo\n")
        self._write("CONTRIBUTING.md", "# Contributing\n")
        self._write("AGENTS.md", "private maintenance\n")
        self._write(
            "docs/internal.md",
            f"token={token}\nroot=D:\\VSCodeProjects\\Req2Web\n",
        )
        inventory = (
            "README.md",
            "CONTRIBUTING.md",
            "AGENTS.md",
            "docs/internal.md",
        )

        report = audit_repository(self.temp_root, inventory=inventory)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["missing_public_files"], ["LICENSE", "SECURITY.md"])
        self.assertEqual(report["cleanup_candidates"], ["AGENTS.md"])
        self.assertEqual(
            report["secret_findings"],
            [{"path": "docs/internal.md", "line": 1, "kind": "github_token"}],
        )
        self.assertEqual(
            report["absolute_path_findings"],
            [
                {
                    "path": "docs/internal.md",
                    "line": 2,
                    "kind": "windows_absolute_path",
                }
            ],
        )
        self.assertNotIn(token, json.dumps(report, sort_keys=True))

    def test_clean_minimal_inventory_reaches_owner_review(self) -> None:
        inventory = ("README.md", "CONTRIBUTING.md", "LICENSE", "SECURITY.md")
        for relative_path in inventory:
            self._write(relative_path, f"# {relative_path}\n")

        report = audit_repository(self.temp_root, inventory=inventory)

        self.assertEqual(report["status"], "ready_for_owner_review")
        self.assertFalse(any(report["blockers"].values()))

    def test_cli_rejects_nonpositive_large_file_threshold(self) -> None:
        self.assertEqual(main(["--large-file-bytes", "0"]), 2)

    def test_reads_exact_git_tracked_inventory(self) -> None:
        inventory = ("README.md", "CONTRIBUTING.md", "LICENSE", "SECURITY.md")
        for relative_path in inventory:
            self._write(relative_path, f"# {relative_path}\n")
        self._write("untracked.txt", "must not enter the report\n")
        subprocess.run(
            ["git", "init", "--quiet", str(self.temp_root)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.temp_root), "add", *inventory],
            check=True,
        )

        report = audit_repository(self.temp_root)

        self.assertEqual(report["tracked_file_count"], 4)
        self.assertEqual(report["status"], "ready_for_owner_review")


if __name__ == "__main__":
    unittest.main()
