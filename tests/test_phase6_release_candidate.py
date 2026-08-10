from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.phase6_release_candidate import (  # noqa: E402
    ARCHIVE_ROOT,
    Phase6ReleaseCandidateError,
    validate_deterministic_reviewer_zip,
    write_deterministic_reviewer_zip,
)


class Phase6ReleaseCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = ROOT / f".phase6-release-test-{uuid4().hex}"
        self.bundle = self.temp_root / "bundle"
        self.bundle.mkdir(parents=True)
        data = b"deterministic reviewer data\n"
        materials = json.dumps(
            {
                "public_release_ready": False,
                "blocking_gate": "owner_license_decision_required",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        (self.bundle / "data.txt").write_bytes(data)
        (self.bundle / "MATERIALS.json").write_bytes(materials)
        files = []
        for relative, raw in (("MATERIALS.json", materials), ("data.txt", data)):
            files.append(
                {
                    "path": relative,
                    "sha256": sha256(raw).hexdigest(),
                    "byte_length": len(raw),
                }
            )
        (self.bundle / "replay_manifest.json").write_text(
            json.dumps({"files": files}, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_deterministic_zip_bytes_and_embedded_hashes(self) -> None:
        first = self.temp_root / "first.zip"
        second = self.temp_root / "second.zip"
        write_deterministic_reviewer_zip(self.bundle, first)
        write_deterministic_reviewer_zip(self.bundle, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        report = validate_deterministic_reviewer_zip(
            first,
            expected_bundle_root=self.bundle,
        )
        self.assertEqual(report["file_count"], 3)

    def test_zip_path_traversal_fails_closed(self) -> None:
        archive_path = self.temp_root / "unsafe.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(f"{ARCHIVE_ROOT}/../escape.txt", b"unsafe")
        with self.assertRaises(Phase6ReleaseCandidateError):
            validate_deterministic_reviewer_zip(archive_path)

    def test_embedded_file_tampering_fails_closed(self) -> None:
        original = self.temp_root / "original.zip"
        tampered = self.temp_root / "tampered.zip"
        write_deterministic_reviewer_zip(self.bundle, original)
        with zipfile.ZipFile(original, "r") as source, zipfile.ZipFile(
            tampered, "w", compression=zipfile.ZIP_STORED
        ) as target:
            for info in source.infolist():
                raw = source.read(info)
                if info.filename.endswith("data.txt"):
                    raw += b"tampered"
                target.writestr(info, raw)
        with self.assertRaisesRegex(
            Phase6ReleaseCandidateError,
            "reviewer file drifted",
        ):
            validate_deterministic_reviewer_zip(tampered)


if __name__ == "__main__":
    unittest.main()
