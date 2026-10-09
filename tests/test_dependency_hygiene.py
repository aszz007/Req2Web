"""Model-free guards for the repository's existing dependency layout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALL_LAYERS = (
    "requirements-data.txt",
    "requirements-phase4-agent-lock.txt",
)


def entries(name: str) -> list[str]:
    return [
        line.strip()
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


class DependencyHygieneTests(unittest.TestCase):
    def test_canonical_entry_point_only_includes_the_two_layers(self) -> None:
        self.assertEqual(
            entries("requirements.txt"),
            [f"-r {name}" for name in INSTALL_LAYERS],
        )

    def test_install_layers_have_exact_nonduplicated_pins(self) -> None:
        seen: set[str] = set()
        for name in INSTALL_LAYERS:
            pins = entries(name)
            self.assertTrue(pins, name)
            for pin in pins:
                with self.subTest(file=name, pin=pin):
                    self.assertRegex(pin, r"^[A-Za-z0-9_.-]+==[^\s=]+$")
                    distribution = re.sub(r"[-_.]+", "-", pin.split("==")[0]).lower()
                    self.assertNotIn(distribution, seen)
                    seen.add(distribution)

    def test_direct_declaration_preserves_the_receipt_identity(self) -> None:
        receipt = json.loads(
            (ROOT / "docs/phase4_langgraph_dependency_acquisition_receipt.json")
            .read_text(encoding="utf-8")
        )
        identity = receipt["requirements_file_identity"]
        self.assertEqual(identity["relative_path"], "requirements-phase4-agent.txt")
        raw = (ROOT / identity["relative_path"]).read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw.decode("utf-8").splitlines(), [receipt["direct_pin"]])
        canonical = (receipt["direct_pin"] + "\n").encode("utf-8")
        self.assertEqual(len(canonical), identity["canonical_byte_length"])
        self.assertEqual(
            "sha256:" + hashlib.sha256(canonical).hexdigest(),
            identity["canonical_sha256"],
        )

    def test_local_artifact_exclusions_do_not_hide_repository_inputs(self) -> None:
        ignored = [
            ".venv/pyvenv.cfg",
            ".venv-rebuild/pyvenv.cfg",
            ".venv-test/Scripts/python.exe",
            ".tox/log/run.log",
            ".nox/session/pyvenv.cfg",
            ".coverage",
            ".coverage.worker",
            "coverage.xml",
            "htmlcov/index.html",
            "build/lib/example.py",
            "dist/example.whl",
            "example.egg-info/PKG-INFO",
        ]
        visible = ["requirements.txt", *INSTALL_LAYERS, "requirements-phase4-agent.txt"]
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin", "-z"],
            cwd=ROOT,
            input=("\0".join([*ignored, *visible]) + "\0").encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout.decode("utf-8").split("\0")[:-1], ignored)


if __name__ == "__main__":
    unittest.main()
