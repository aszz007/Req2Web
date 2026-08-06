from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime import autodl_repository_archive as archive
from req2web_runtime.qwen27b_case_bundle import (  # noqa: E402
    CASE_BUNDLE_MANIFEST,
    CASE_IDS,
    MODEL_REPOSITORY,
    MODEL_REVISION,
    Qwen27BCaseBundleError,
    build_qwen27b_case_bundle,
    compose_qwen27b_model_text,
    validate_qwen27b_case_bundle,
)


class Qwen27BCaseBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.shared = ROOT / ("tmp-qwen27b-case-source-" + uuid4().hex)
        cls.shared.mkdir()
        cls.repo = cls.shared / "repo"
        cls.repo.mkdir()
        for relative in (
            "fixtures/stage3_path3_compatibility_cases_v1.json",
            "fixtures/stage3_path3_external_action_gate_preparation_v1.json",
        ):
            source = ROOT / relative
            target = cls.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        shutil.copytree(
            ROOT / "data/processed/rag",
            cls.repo / "data/processed/rag",
        )
        cls._git("init")
        cls._git("config", "user.email", "tester@example.invalid")
        cls._git("config", "user.name", "Req2Web Test")
        cls._git("add", ".")
        cls._git("commit", "-m", "case source")
        cls.first_head = cls._git("rev-parse", "HEAD")
        cls._git("update-ref", "refs/remotes/origin/main", cls.first_head)
        cls.first_output = cls.shared / "archive-one"
        cls.first_output.mkdir()
        cls.first_manifest = archive.write_repository_archive(
            cls.repo, cls.first_head, cls.first_output
        )
        cls.first_archive_path = cls.first_output / archive.ARCHIVE_FILENAME
        cls.first_manifest_path = cls.first_output / archive.MANIFEST_FILENAME

        (cls.repo / "ordinary-note.txt").write_text(
            "different accepted tree\n", encoding="utf-8"
        )
        cls._git("add", "ordinary-note.txt")
        cls._git("commit", "-m", "different tree")
        cls.second_head = cls._git("rev-parse", "HEAD")
        cls._git("update-ref", "refs/remotes/origin/main", cls.second_head)
        cls.second_output = cls.shared / "archive-two"
        cls.second_output.mkdir()
        cls.second_manifest = archive.write_repository_archive(
            cls.repo, cls.second_head, cls.second_output
        )
        cls.second_archive_path = cls.second_output / archive.ARCHIVE_FILENAME
        cls.second_manifest_path = cls.second_output / archive.MANIFEST_FILENAME

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.shared, ignore_errors=True)

    @classmethod
    def _git(cls, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=cls.repo,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)
        return completed.stdout.strip()

    def setUp(self) -> None:
        self.work = ROOT / ("tmp-qwen27b-case-bundle-" + uuid4().hex)
        self.work.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def build(self):
        return build_qwen27b_case_bundle(
            self.first_archive_path,
            self.first_manifest_path,
            self.work / "bundle",
        )

    def test_accepted_archive_builds_exact_two_case_no_model_bundle(self) -> None:
        bundle = self.build()
        self.assertEqual(bundle.manifest["case_ids"], list(CASE_IDS))
        self.assertEqual(
            bundle.manifest["model_profile"],
            {
                "model_id": "Qwen3.5-27B",
                "repository": MODEL_REPOSITORY,
                "exact_revision": MODEL_REVISION,
            },
        )
        source = bundle.manifest["source"]["repository_archive_authority"]
        self.assertEqual(source["commit_sha"], self.first_head)
        self.assertEqual(
            source["manifest_id"],
            self.first_manifest.to_dict()["manifest_id"],
        )
        self.assertFalse(bundle.manifest["claims"]["model_loaded"])
        self.assertFalse(bundle.manifest["claims"]["run_occurred"])
        self.assertEqual(bundle.manifest["inventory"]["file_count"], 4)
        replay = validate_qwen27b_case_bundle(
            bundle.root,
            repository_archive_path=self.first_archive_path,
            repository_archive_manifest_path=self.first_manifest_path,
        )
        self.assertEqual(replay.sha256(), bundle.sha256())
        self.assertEqual(tuple(replay.verified_case_bindings), CASE_IDS)
        for row in bundle.manifest["cases"]:
            self.assertEqual(
                row["agent_context"]["schema_version"],
                "req2web.agent.context.v1",
            )
            self.assertEqual(
                row["retrieval_guidance"]["schema_version"],
                "req2web.retrieval.guidance.v1",
            )
            self.assertEqual(
                row["retrieval_guidance"]["source_context"],
                row["agent_context"],
            )
            self.assertEqual(
                replay.verified_case_bindings[row["case_id"]],
                {
                    "agent_context": row["agent_context"],
                    "retrieval_guidance": row["retrieval_guidance"],
                },
            )
        for case_id in CASE_IDS:
            text = compose_qwen27b_model_text(bundle, case_id)
            self.assertIn("<provider_visible_input>", text)
            self.assertIn("</provider_visible_input>", text)
            self.assertIn("req2web.provider.semantic_candidate.v1", text)

    def test_cross_tree_archive_cannot_validate_existing_bundle(self) -> None:
        bundle = self.build()
        with self.assertRaisesRegex(
            Qwen27BCaseBundleError, "archive_authority_mismatch"
        ):
            validate_qwen27b_case_bundle(
                bundle.root,
                repository_archive_path=self.second_archive_path,
                repository_archive_manifest_path=self.second_manifest_path,
            )

    def test_dirty_worktree_is_not_a_case_bundle_input(self) -> None:
        bundle = self.build()
        case_path = (
            self.repo / "fixtures/stage3_path3_compatibility_cases_v1.json"
        )
        original = case_path.read_bytes()
        case_path.write_bytes(original + b" ")
        try:
            replay = validate_qwen27b_case_bundle(
                bundle.root,
                repository_archive_path=self.first_archive_path,
                repository_archive_manifest_path=self.first_manifest_path,
            )
            self.assertEqual(replay.sha256(), bundle.sha256())
        finally:
            case_path.write_bytes(original)

    def test_payload_manifest_and_model_identity_tamper_fail_closed(self) -> None:
        bundle = self.build()
        prompt = bundle.root / "cases" / CASE_IDS[0] / "prompt.json"
        prompt.write_bytes(prompt.read_bytes() + b" ")
        with self.assertRaisesRegex(Qwen27BCaseBundleError, "inventory"):
            validate_qwen27b_case_bundle(bundle.root)

        shutil.rmtree(bundle.root)
        bundle = self.build()
        manifest_path = bundle.root / CASE_BUNDLE_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["model_profile"]["repository"] = "Qwen/Qwen3.5-9B"
        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaisesRegex(Qwen27BCaseBundleError, "model_profile"):
            validate_qwen27b_case_bundle(bundle.root)

    def test_upstream_context_guidance_identity_drift_fails_closed(self) -> None:
        bundle = self.build()
        manifest_path = bundle.root / CASE_BUNDLE_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        row = manifest["cases"][0]
        row["agent_context"]["sha256"] = "0" * 64
        row["retrieval_guidance"]["source_context"] = dict(
            row["agent_context"]
        )
        body = {
            key: value for key, value in manifest.items() if key != "bundle_id"
        }
        manifest["bundle_id"] = (
            "qwen35-27b-case-bundle-v1-"
            + hashlib.sha256(
                json.dumps(
                    body,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        )
        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaisesRegex(
            Qwen27BCaseBundleError,
            "upstream_context_guidance_mismatch",
        ):
            validate_qwen27b_case_bundle(
                bundle.root,
                repository_archive_path=self.first_archive_path,
                repository_archive_manifest_path=self.first_manifest_path,
            )

    def test_legacy_manifest_replay_derives_verified_upstream_bindings(self) -> None:
        bundle = self.build()
        manifest_path = bundle.root / CASE_BUNDLE_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for row in manifest["cases"]:
            row.pop("agent_context")
            row.pop("retrieval_guidance")
        body = {
            key: value for key, value in manifest.items() if key != "bundle_id"
        }
        manifest["bundle_id"] = (
            "qwen35-27b-case-bundle-v1-"
            + hashlib.sha256(
                json.dumps(
                    body,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        )
        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
            newline="\n",
        )
        replay = validate_qwen27b_case_bundle(
            bundle.root,
            repository_archive_path=self.first_archive_path,
            repository_archive_manifest_path=self.first_manifest_path,
        )
        self.assertEqual(tuple(replay.verified_case_bindings), CASE_IDS)

    def test_nonempty_output_root_is_rejected(self) -> None:
        output = self.work / "nonempty"
        output.mkdir()
        (output / "user.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(Qwen27BCaseBundleError, "not_empty"):
            build_qwen27b_case_bundle(
                self.first_archive_path,
                self.first_manifest_path,
                output,
            )
        self.assertEqual(
            (output / "user.txt").read_text(encoding="utf-8"), "preserve"
        )


if __name__ == "__main__":
    unittest.main()
