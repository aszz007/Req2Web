from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_guispector_bigmodel_overlay.py"


def _load_overlay_module():
    spec = importlib.util.spec_from_file_location("guispector_overlay", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load the GUISpector overlay installer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _MemoryTextFile:
    def __init__(self, content: str) -> None:
        self.content = content

    def read_text(self, *, encoding: str) -> str:
        self.asserted_encoding = encoding
        return self.content

    def write_text(self, content: str, *, encoding: str, newline: str) -> None:
        self.asserted_encoding = encoding
        self.asserted_newline = newline
        self.content = content


class GUISpectorOverlayPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.overlay = _load_overlay_module()

    def test_legacy_replay_mounts_are_replaced_and_idempotent(self) -> None:
        source = ROOT.parent / "Req2Web_LocalData" / "replay" / "bundle"
        compose = """services:
  req2web-pages:
    image: nginx:1.27-alpine
    volumes:
      - ../../../release/phase6_reviewer_v17:/usr/share/nginx/html:ro
  agent:
    volumes:
      - ./gui_spector:/app/gui_spector:ro
      - ../../../release/phase6_reviewer_v17:/app/req2web_reviewer_v17:ro
"""
        compose_path = _MemoryTextFile(compose)

        self.assertTrue(
            self.overlay._ensure_replay_mount(compose_path, source)
        )
        updated = compose_path.read_text(encoding="utf-8")
        self.assertIn("/app/req2web_inspector_replay_v1:ro", updated)
        self.assertIn("/usr/share/nginx/html:ro", updated)
        self.assertEqual(updated.count(source.as_posix()), 2)
        self.assertNotIn("phase6_reviewer_v17", updated)
        self.assertFalse(
            self.overlay._ensure_replay_mount(compose_path, source)
        )


if __name__ == "__main__":
    unittest.main()
