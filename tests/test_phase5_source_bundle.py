from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import req2web_runtime.phase5_source_bundle as _source_bundle  # noqa: E402
from req2web_runtime.phase5_source_bundle import (  # noqa: E402
    Phase5SourceBundleManifest,
    create_phase5_source_bundle,
    validate_phase5_source_bundle,
)


class Phase5SourceBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = ROOT / f".phase5-source-bundle-test-{uuid.uuid4().hex}"
        self.temp.mkdir()
        self.repo = self.temp / "repo"
        self.handoff = self.temp / "handoff"
        self.repo.mkdir()
        self.handoff.mkdir()
        self.commit = "1" * 40
        self.tree = "2" * 40
        self.bundle = (self.handoff / "phase5-test.bundle").resolve()
        self.manifest_path = (
            self.handoff / "phase5-test.bundle.manifest.json"
        ).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _git(self, root: Path, *args: str) -> str:
        self.assertEqual(root, self.repo.resolve(strict=True))
        if args == ("rev-parse", "--git-dir"):
            return ".phase5-test-git"
        if args in (
            ("rev-parse", "HEAD"),
            ("rev-parse", "refs/heads/main"),
        ):
            return self.commit
        if args == ("rev-parse", f"{self.commit}^{{tree}}"):
            return self.tree
        if args[:2] == ("bundle", "create"):
            Path(args[2]).write_bytes(b"synthetic verified git bundle")
            self.assertEqual(args[3], "refs/heads/main")
            return ""
        if args[:2] == ("bundle", "verify"):
            return "verified"
        self.fail(f"unexpected Git call: {args}")

    def _create(self) -> Phase5SourceBundleManifest:
        with patch.object(_source_bundle, "_run_git", side_effect=self._git):
            return create_phase5_source_bundle(
                repo_root=self.repo,
                source_commit=self.commit,
                bundle_path=self.bundle,
                manifest_path=self.manifest_path,
            )

    def test_commit_bound_bundle_invokes_create_and_verify_seam(self) -> None:
        manifest = self._create()
        payload = manifest.to_dict()
        self.assertEqual(payload["source_commit"], self.commit)
        self.assertEqual(payload["source_tree"], self.tree)
        self.assertTrue(payload["bundle_verify_completed"])
        self.assertEqual(payload["transport"], "git_bundle_then_local_clone")
        self.assertFalse(payload["action_state"]["remote_uploaded"])
        self.assertFalse(payload["action_state"]["ssh_connected"])
        with patch.object(_source_bundle, "_run_git", side_effect=self._git):
            self.assertEqual(
                validate_phase5_source_bundle(
                    repo_root=self.repo,
                    bundle_path=self.bundle,
                    manifest_path=self.manifest_path,
                ),
                manifest,
            )

    def test_manifest_is_canonical_and_bundle_tamper_fails(self) -> None:
        manifest = self._create()
        self.assertEqual(
            Phase5SourceBundleManifest.from_json_bytes(
                manifest.canonical_json_bytes()
            ),
            manifest,
        )
        self.bundle.write_bytes(self.bundle.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "bytes drifted"):
            with patch.object(_source_bundle, "_run_git", side_effect=self._git):
                validate_phase5_source_bundle(
                    repo_root=self.repo,
                    bundle_path=self.bundle,
                    manifest_path=self.manifest_path,
                )

    def test_source_commit_must_equal_head_and_local_main(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "must equal HEAD and local main",
        ):
            with patch.object(
                _source_bundle,
                "_run_git",
                side_effect=lambda root, *args: (
                    ".phase5-test-git"
                    if args == ("rev-parse", "--git-dir")
                    else "3" * 40
                ),
            ):
                create_phase5_source_bundle(
                    repo_root=self.repo,
                    source_commit="1" * 40,
                    bundle_path=self.bundle,
                    manifest_path=self.manifest_path,
                )

    def test_output_must_remain_outside_source_repository(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            with patch.object(_source_bundle, "_run_git", side_effect=self._git):
                create_phase5_source_bundle(
                    repo_root=self.repo,
                    source_commit=self.commit,
                    bundle_path=(self.repo / "phase5.bundle").resolve(),
                    manifest_path=(self.repo / "phase5.manifest.json").resolve(),
                )


if __name__ == "__main__":
    unittest.main()
