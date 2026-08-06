from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.phase5_formal_runner import (  # noqa: E402
    run_phase5_formal_runner,
    synthetic_phase5_worker_factory,
)
from req2web_runtime.phase5_result_return import (  # noqa: E402
    Phase5ResultReturnManifest,
    create_phase5_result_return,
    validate_phase5_result_return,
)
from req2web_runtime.phase5_sealed_action_package import (  # noqa: E402
    create_phase5_sealed_action_package,
)


FIXTURE = ROOT / "fixtures" / "phase5_sealed_action_package_synthetic_v1.json"


class Phase5ResultReturnTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = ROOT / f".phase5-result-return-test-{uuid.uuid4().hex}"
        self.temp.mkdir()
        package = create_phase5_sealed_action_package(
            json.loads(FIXTURE.read_text(encoding="utf-8"))
        )
        self.result_root = (self.temp / "result").resolve()
        run_phase5_formal_runner(
            package=package,
            result_root=self.result_root,
            worker_factory=synthetic_phase5_worker_factory,
            allow_synthetic_validation_only=True,
        )
        self.return_root = self.temp / "return"
        self.return_root.mkdir()
        self.tar_path = (self.return_root / "phase5-synthetic-return.tar").resolve()
        self.manifest_path = (
            self.return_root / "phase5-synthetic-return.manifest.json"
        ).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_deterministic_return_round_trip_and_exact_inventory(self) -> None:
        manifest = create_phase5_result_return(
            result_root=self.result_root,
            tar_path=self.tar_path,
            manifest_path=self.manifest_path,
        )
        payload = manifest.to_dict()
        self.assertGreater(payload["file_count"], 0)
        self.assertEqual(
            payload["file_count"],
            len(payload["inventory"]),
        )
        self.assertFalse(payload["action_state"]["local_return_validated"])
        self.assertFalse(payload["action_state"]["instance_released"])
        self.assertEqual(
            validate_phase5_result_return(
                tar_path=self.tar_path,
                manifest_path=self.manifest_path,
            ),
            manifest,
        )
        self.assertEqual(
            Phase5ResultReturnManifest.from_json_bytes(
                manifest.canonical_json_bytes()
            ),
            manifest,
        )

    def test_same_source_produces_same_tar_bytes(self) -> None:
        first = create_phase5_result_return(
            result_root=self.result_root,
            tar_path=self.tar_path,
            manifest_path=self.manifest_path,
        )
        second_tar = (self.return_root / "second.tar").resolve()
        second_manifest = (self.return_root / "second.manifest.json").resolve()
        second = create_phase5_result_return(
            result_root=self.result_root,
            tar_path=second_tar,
            manifest_path=second_manifest,
        )
        self.assertEqual(first.to_dict()["tar_sha256"], second.to_dict()["tar_sha256"])
        self.assertEqual(self.tar_path.read_bytes(), second_tar.read_bytes())

    def test_tar_or_manifest_tamper_fails_closed(self) -> None:
        create_phase5_result_return(
            result_root=self.result_root,
            tar_path=self.tar_path,
            manifest_path=self.manifest_path,
        )
        self.tar_path.write_bytes(self.tar_path.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "tar bytes drifted"):
            validate_phase5_result_return(
                tar_path=self.tar_path,
                manifest_path=self.manifest_path,
            )


if __name__ == "__main__":
    unittest.main()
