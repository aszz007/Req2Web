from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.phase5_result_return import (  # noqa: E402
    Phase5ResultReturnManifest,
    create_phase5_result_return,
    validate_phase5_result_return,
)


class Phase5ResultReturnTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = ROOT / f".phase5-result-return-test-{uuid.uuid4().hex}"
        self.temp.mkdir()
        self.result_root = (self.temp / "result").resolve()
        self.result_root.mkdir()
        (self.result_root / "artifact.json").write_bytes(
            self._canonical({"status": "synthetic_component_result"})
        )
        (self.result_root / "run_summary.json").write_bytes(
            self._canonical(
                {
                    "schema_version": "synthetic.phase5.run_summary.v1",
                    "run_id": "synthetic-phase5-result-return-run",
                    "package_sha256": "a" * 64,
                    "status": "terminal_synthetic_component_result",
                }
            )
        )
        self.return_root = self.temp / "return"
        self.return_root.mkdir()
        self.tar_path = (self.return_root / "phase5-synthetic-return.tar").resolve()
        self.manifest_path = (
            self.return_root / "phase5-synthetic-return.manifest.json"
        ).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _semantic_result_root(self) -> Path:
        result_root = (self.temp / "semantic-result").resolve()
        result_root.mkdir()
        (result_root / "raw_response.bin").write_bytes(b"semantic raw response")
        summary = {
            "schema_version": "req2web.phase5.semantic_run_summary.v2",
            "runtime_schema_version": "req2web.phase5.semantic_qwen_runtime.v3",
            "status": "failed_closed",
            "case_id": "p4-full-flow-02-apparel",
            "formal_quality_claimed": False,
        }
        (result_root / "run_summary.json").write_bytes(
            self._canonical(summary)
        )
        return result_root

    def _publication_result_root(self) -> Path:
        result_root = (self.temp / "publication-result").resolve()
        result_root.mkdir()
        (result_root / "raw_response.bin").write_bytes(b"publication raw response")
        summary = {
            "schema_version": "req2web.phase5.publication_action.v1.summary",
            "run_id": "phase5-publication-result-return-test",
            "package_sha256": "b" * 64,
            "source_action_commit": "c" * 40,
            "candidate_matrix_id": "phase5-publication-matrix-test",
            "formal_evaluation": False,
            "formal_quality_claimed": False,
            "h1_opened": False,
            "gold_content_present": False,
        }
        (result_root / "run_summary.json").write_bytes(
            self._canonical(summary)
        )
        return result_root

    def test_publication_action_uses_non_formal_v3_source_binding(self) -> None:
        result_root = self._publication_result_root()
        tar_path = (self.return_root / "publication-return.tar").resolve()
        manifest_path = (
            self.return_root / "publication-return.manifest.json"
        ).resolve()
        manifest = create_phase5_result_return(
            result_root=result_root,
            tar_path=tar_path,
            manifest_path=manifest_path,
        )
        payload = manifest.to_dict()
        self.assertEqual(
            payload["schema_version"],
            "req2web.phase5.result_return_manifest.v3",
        )
        self.assertEqual(
            payload["source_binding"],
            {
                "candidate_matrix_id": "phase5-publication-matrix-test",
                "package_sha256": "b" * 64,
                "publication_summary_schema_version": (
                    "req2web.phase5.publication_action.v1.summary"
                ),
                "run_id": "phase5-publication-result-return-test",
                "schema_version": (
                    "req2web.phase5.result_return.source_binding.v2"
                ),
                "source_action_commit": "c" * 40,
                "source_kind": "publication_action",
            },
        )
        self.assertEqual(
            validate_phase5_result_return(
                tar_path=tar_path,
                manifest_path=manifest_path,
            ),
            manifest,
        )

    def test_publication_action_rejects_formal_claim_drift(self) -> None:
        result_root = self._publication_result_root()
        summary_path = result_root / "run_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["formal_evaluation"] = True
        summary_path.write_bytes(self._canonical(summary))
        with self.assertRaisesRegex(
            ValueError,
            "formal_evaluation must be false",
        ):
            create_phase5_result_return(
                result_root=result_root,
                tar_path=(self.return_root / "invalid-publication.tar").resolve(),
                manifest_path=(
                    self.return_root / "invalid-publication.manifest.json"
                ).resolve(),
            )

    def test_semantic_case_summary_uses_typed_source_binding(self) -> None:
        result_root = self._semantic_result_root()
        tar_path = (self.return_root / "semantic-return.tar").resolve()
        manifest_path = (
            self.return_root / "semantic-return.manifest.json"
        ).resolve()
        manifest = create_phase5_result_return(
            result_root=result_root,
            tar_path=tar_path,
            manifest_path=manifest_path,
        )
        payload = manifest.to_dict()
        self.assertEqual(
            payload["source_binding"],
            {
                "case_id": "p4-full-flow-02-apparel",
                "schema_version": (
                    "req2web.phase5.result_return.source_binding.v1"
                ),
                "semantic_summary_schema_version": (
                    "req2web.phase5.semantic_run_summary.v2"
                ),
                "source_kind": "semantic_evaluator_case",
            },
        )
        self.assertNotIn("package_sha256", payload)
        self.assertNotIn("package_sha256", payload["source_binding"])
        self.assertEqual(
            validate_phase5_result_return(
                tar_path=tar_path,
                manifest_path=manifest_path,
            ),
            manifest,
        )

    def test_existing_v1_formal_manifest_replays(self) -> None:
        manifest = create_phase5_result_return(
            result_root=self.result_root,
            tar_path=self.tar_path,
            manifest_path=self.manifest_path,
        )
        current = manifest.to_dict()
        source = current["source_binding"]
        legacy_body = {
            "schema_version": "req2web.phase5.result_return_manifest.v1",
            "status": current["status"],
            "run_id": source["run_id"],
            "package_sha256": source["package_sha256"],
            "run_summary_sha256": current["run_summary_sha256"],
            "tar_filename": current["tar_filename"],
            "tar_sha256": current["tar_sha256"],
            "tar_byte_length": current["tar_byte_length"],
            "file_count": current["file_count"],
            "total_file_bytes": current["total_file_bytes"],
            "inventory": current["inventory"],
            "archive_policy": current["archive_policy"],
            "action_state": current["action_state"],
        }
        legacy_payload = {
            "manifest_id": (
                "phase5-result-return-"
                + sha256(self._canonical(legacy_body)).hexdigest()
            ),
            **legacy_body,
        }
        legacy_path = (self.return_root / "legacy.manifest.json").resolve()
        legacy_path.write_bytes(self._canonical(legacy_payload))
        replayed = validate_phase5_result_return(
            tar_path=self.tar_path,
            manifest_path=legacy_path,
        )
        self.assertEqual(
            replayed.to_dict()["schema_version"],
            "req2web.phase5.result_return_manifest.v1",
        )
        self.assertEqual(replayed.to_dict()["run_id"], source["run_id"])

    def test_existing_v2_formal_manifest_replays(self) -> None:
        manifest = create_phase5_result_return(
            result_root=self.result_root,
            tar_path=self.tar_path,
            manifest_path=self.manifest_path,
        )
        current = manifest.to_dict()
        previous_body = {
            **{
                key: value
                for key, value in current.items()
                if key != "manifest_id"
            },
            "schema_version": "req2web.phase5.result_return_manifest.v2",
        }
        previous_payload = {
            "manifest_id": (
                "phase5-result-return-"
                + sha256(self._canonical(previous_body)).hexdigest()
            ),
            **previous_body,
        }
        previous_path = (self.return_root / "previous.manifest.json").resolve()
        previous_path.write_bytes(self._canonical(previous_payload))
        replayed = validate_phase5_result_return(
            tar_path=self.tar_path,
            manifest_path=previous_path,
        )
        self.assertEqual(
            replayed.to_dict()["schema_version"],
            "req2web.phase5.result_return_manifest.v2",
        )

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
        self.assertEqual(
            payload["source_binding"]["source_kind"],
            "formal_run",
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
