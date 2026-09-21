from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import prepare_phase7_autodl_bundle as archive_api
import prepare_phase7_e2_v4_bundle as builder


WORKSPACE_TEMP = ROOT / "outputs" / "phase7_e2_diagnostic_v4" / "bundle_tests"
WORKSPACE_TEMP.mkdir(parents=True, exist_ok=True)


class WorkspaceDirectory:
    def __enter__(self):
        self.path = WORKSPACE_TEMP / ("case_" + uuid.uuid4().hex)
        self.path.mkdir()
        return self.path

    def __exit__(self, exc_type, exc, tb):
        target = self.path.resolve(strict=True)
        root = WORKSPACE_TEMP.resolve(strict=True)
        if target.parent != root or not target.name.startswith("case_"):
            raise RuntimeError("refusing unsafe v4-bundle cleanup target")
        shutil.rmtree(target)


class Phase7E2V4BundleTests(unittest.TestCase):
    def test_bundle_preserves_public_packets_and_adds_only_v4_runtime_slice(self) -> None:
        source = ROOT / "outputs/phase7_e2_diagnostic_v3_recovery/payload-20260919-final"
        source_handoff = json.loads((source / "handoff_manifest.json").read_bytes())
        source_archive = source / source_handoff["archive_filename"]
        with WorkspaceDirectory() as work:
            destination = work / "payload"
            result = builder.build(source, destination)
            archive = destination / result["archive_filename"]
            archive_api.validate_archive(archive, result["archive_sha256"])
            with zipfile.ZipFile(source_archive) as old_zip, zipfile.ZipFile(archive) as new_zip:
                old_manifest = json.loads(old_zip.read("payload_manifest.json"))
                new_manifest = json.loads(new_zip.read("payload_manifest.json"))
                old_public = {
                    row["path"]: row["sha256"]
                    for row in old_manifest["files"]
                    if row["path"].startswith("e2-v3/public/")
                }
                new_public = {
                    row["path"]: row["sha256"]
                    for row in new_manifest["files"]
                    if row["path"].startswith("e2-v3/public/")
                }
                self.assertEqual(old_public, new_public)
                names = {row["path"] for row in new_manifest["files"]}
                self.assertTrue(builder.ADDITIONS <= names)
                metadata = new_manifest["metadata"]
                self.assertFalse(metadata["public_packets_changed"])
                self.assertTrue(metadata["model_visible_protocol_changed"])
                self.assertFalse(metadata["runtime_selected_or_approved"])
                self.assertEqual(metadata["automatic_retry_cap"], 0)


if __name__ == "__main__":
    unittest.main()
