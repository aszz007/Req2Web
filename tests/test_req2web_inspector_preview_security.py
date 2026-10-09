from __future__ import annotations

from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import shutil
import threading
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "req2web_preview_security_runner", ROOT / "scripts/run_req2web_inspector.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ArtifactStore:
    """Component fixture for HTTP routing; not package-validation evidence."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def artifact_path(self, run_id: str, relative: str) -> Path:
        if run_id != "fixture":
            raise ValueError("unknown component fixture")
        candidate = (self.root / relative).resolve()
        if self.root not in candidate.parents or not candidate.is_file():
            raise ValueError("artifact not found")
        return candidate


class InspectorPreviewSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = ROOT / f".req2web-preview-security-test-{uuid.uuid4().hex}"
        cls.root.mkdir()
        (cls.root / "package/page").mkdir(parents=True)
        (cls.root / "evidence").mkdir()
        cls.html = b"<!doctype html><p>Component preview</p><script>window.probe=1</script>"
        (cls.root / "package/page/index.html").write_bytes(cls.html)
        (cls.root / "package/page/app.js").write_bytes(b"window.scriptLoaded = true;")
        (cls.root / "evidence/browser.html").write_bytes(cls.html)
        (cls.root / "replay.html").write_bytes(cls.html)
        cls.archive = b"component-download-fixture"
        (cls.root / "result-package.zip").write_bytes(cls.archive)
        cls.runner = _load_runner()

        class Handler(cls.runner._InspectorHandler):
            run_store = _ArtifactStore(cls.root)
            canonical_run_store = _ArtifactStore(cls.root)
            live_drafts_enabled = True

            def log_message(self, format: str, *args: object) -> None:
                pass

        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(Handler, directory=str(cls.root))
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.root)

    def request(self, path: str, *, method: str = "GET", body=None, headers=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def assert_isolated(self, headers) -> None:
        policy = headers["Content-Security-Policy"]
        self.assertEqual(policy, self.runner._ARTIFACT_CSP)
        self.assertIn("sandbox allow-scripts allow-forms", policy)
        self.assertNotIn("allow-same-origin", policy)
        self.assertNotIn("allow-popups", policy)
        for directive in (
            "connect-src 'none'", "form-action 'none'", "frame-src 'none'",
            "worker-src 'none'", "base-uri 'none'",
        ):
            self.assertIn(directive, policy)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("microphone=()", headers["Permissions-Policy"])

    def test_inspector_controls_are_not_sandboxed(self) -> None:
        for path in ("/", "/index.html", "/app.js", "/styles.css"):
            with self.subTest(path=path):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 200)
                self.assertNotIn("Content-Security-Policy", headers)

    def test_direct_draft_and_import_preview_is_isolated(self) -> None:
        status, headers, body = self.request("/api/runs/fixture/package/page/index.html")
        self.assertEqual(status, 200)
        self.assertEqual(body, self.html)
        self.assert_isolated(headers)

    def test_canonical_preview_and_evidence_are_isolated(self) -> None:
        for path in (
            "/api/canonical-runs/fixture/package/page/index.html",
            "/api/canonical-runs/fixture/evidence/browser.html",
        ):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(body, self.html)
                self.assert_isolated(headers)

    def test_replay_file_is_isolated_for_get_and_head(self) -> None:
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                status, headers, body = self.request("/replay.html", method=method)
                self.assertEqual(status, 200)
                self.assert_isolated(headers)
                self.assertEqual(body, self.html if method == "GET" else b"")

    def test_package_head_uses_same_route_without_body(self) -> None:
        status, headers, body = self.request(
            "/api/runs/fixture/package/page/index.html", method="HEAD"
        )
        self.assertEqual(status, 200)
        self.assert_isolated(headers)
        self.assertEqual(int(headers["Content-Length"]), len(self.html))
        self.assertEqual(body, b"")

    def test_download_bytes_and_attachment_are_preserved(self) -> None:
        status, headers, body = self.request("/api/runs/fixture/download")
        self.assertEqual(status, 200)
        self.assertEqual(body, self.archive)
        self.assertEqual(
            headers["Content-Disposition"],
            'attachment; filename="fixture-result-package.zip"',
        )

    def test_encoded_preview_path_cannot_bypass_isolation(self) -> None:
        status, headers, body = self.request(
            "/api/runs/fixture/package/page%2findex.html?ignored=1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, self.html)
        self.assert_isolated(headers)

    def test_opaque_preview_origin_cannot_post_to_local_api(self) -> None:
        status, _, body = self.request(
            "/api/intake/analyze", method="POST",
            body=json.dumps({"requirement": "Build a search page"}),
            headers={"Content-Type": "application/json", "Origin": "null"},
        )
        self.assertEqual(status, 422)
        self.assertIn("cross-origin local API request was rejected", body.decode())


if __name__ == "__main__":
    unittest.main()
