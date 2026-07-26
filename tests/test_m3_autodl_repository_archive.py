from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from types import MappingProxyType

from src.req2web_runtime import autodl_repository_archive as ara


class RepositoryArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init")
        self._git("config", "user.email", "tester@example.invalid")
        self._git("config", "user.name", "Req2Web Test")
        (self.repo / "README.md").write_text("hello\n", encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
        self._git("add", "README.md", "src/app.py")
        self._git("commit", "-m", "initial")
        self.head = self._git("rev-parse", "HEAD")
        self._git("update-ref", "refs/remotes/origin/main", self.head)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _git(self, *args: str, check: bool = True) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=self.repo, text=True, encoding="utf-8",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if check and completed.returncode != 0:
            self.fail(f"git {' '.join(args)} failed: {completed.stderr}")
        return completed.stdout.strip()

    def _build(self):
        return ara.build_repository_archive_manifest(self.repo, self.head)

    def _commit_restricted_shapes(self) -> None:
        files = {
            "data/processed/rico_candidate_pool.csv": "restricted-rico-content\n",
            "data/processed/rico_review_sheets/commerce.jpg": "restricted-rico-image\n",
            "data/h1/case.json": "restricted-h1-data\n",
            "data/reference_only/asset.txt": "restricted-reference-content\n",
            "secrets/api_token.txt": "restricted-credential-content\n",
            "models/qwen/weights.safetensors": "restricted-weight-content\n",
            "artifacts/cache/cache.bin": "restricted-cache-content\n",
            "scripts/audit_rico_combined.py": "project-authored-rico-tool\n",
            "docs/rico_reference_asset_permission_review.md": "project-authored-review-doc\n",
            "src/req2web_faults/evaluator_gold.py": "project-authored-evaluator-code\n",
            "tests/test_fault_gold_manifest.py": "project-authored-gold-test\n",
            "src/criterion.py": "allowed-criterion\n",
            "docs/golden_ratio.md": "allowed-golden\n",
            "src/lightweight_adapter.py": "allowed-lightweight\n",
        }
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self._git("add", *files)
        self._git("commit", "-m", "material classification shapes")
        self.head = self._git("rev-parse", "HEAD")
        self._git("update-ref", "refs/remotes/origin/main", self.head)
    def _manifest_from_data(self, data):
        data["manifest_id"] = "0" * 64
        data["manifest_id"] = hashlib.sha256(json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")).hexdigest()
        return ara.RepositoryArchiveManifest.from_dict(data)

    def _manifest_for_archive(
        self,
        base_manifest: ara.RepositoryArchiveManifest,
        archive_bytes: bytes,
    ) -> ara.RepositoryArchiveManifest:
        data = base_manifest.to_dict()
        data["archive"]["byte_length"] = len(archive_bytes)  # type: ignore[index]
        data["archive"]["sha256"] = hashlib.sha256(archive_bytes).hexdigest()  # type: ignore[index]
        return self._manifest_from_data(data)

    def _zip_from_rows(self, rows, *, duplicate=False, extra=None, omit=None, mode_override=None, data_override=None):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for row in rows:
                if row["path"] == omit:
                    continue
                data = subprocess.run(
                    ["git", "cat-file", "blob", row["blob_sha"]], cwd=self.repo,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                ).stdout
                if data_override and row["path"] in data_override:
                    data = data_override[row["path"]]
                mode = mode_override.get(row["path"], row["git_mode"]) if mode_override else row["git_mode"]
                info = zipfile.ZipInfo(row["path"], date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (int(mode, 8) & 0xFFFF) << 16
                zf.writestr(info, data)
                if duplicate and row["path"] == rows[0]["path"]:
                    zf.writestr(info, data)
            if extra:
                info = zipfile.ZipInfo(extra, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100644 & 0xFFFF) << 16
                zf.writestr(info, b"extra")
        return out.getvalue()

    def test_builds_deterministic_tracked_archive_and_manifest(self):
        manifest, archive_bytes = self._build()
        manifest.validate_archive_bytes(archive_bytes)
        again, again_bytes = self._build()
        self.assertEqual(manifest.canonical_bytes(), again.canonical_bytes())
        self.assertEqual(archive_bytes, again_bytes)
        data = manifest.to_dict()
        self.assertEqual(data["commit_sha"], self.head)
        self.assertEqual([row["path"] for row in data["files"]], ["README.md", "src/app.py"])
        self.assertEqual(data["excluded_files"], [])
        self.assertEqual(data["source_tree"]["tracked_file_count"], 2)
        self.assertEqual(data["source_tree"]["included_file_count"], 2)
        self.assertEqual(data["source_tree"]["excluded_file_count"], 0)
        self.assertEqual(data["foundation_status"], ara.FOUNDATION_STATUS)

    def test_action_writes_only_empty_dedicated_directory_and_rereads(self):
        out = self.root / "out"
        out.mkdir()
        manifest = ara.write_repository_archive(self.repo, self.head, out)
        self.assertEqual(sorted(p.name for p in out.iterdir()), sorted([ara.ARCHIVE_FILENAME, ara.MANIFEST_FILENAME]))
        reread = ara.validate_archive_file(out / ara.ARCHIVE_FILENAME, out / ara.MANIFEST_FILENAME)
        self.assertEqual(manifest.sha256(), reread.sha256())

    def test_output_root_symlink_and_non_empty_rejected(self):
        non_empty = self.root / "non-empty"
        non_empty.mkdir()
        (non_empty / "x").write_text("x", encoding="utf-8")
        with self.assertRaises(ara.RepositoryArchiveError):
            ara.write_repository_archive(self.repo, self.head, non_empty)
        if hasattr(os, "symlink"):
            target = self.root / "target"
            target.mkdir()
            link = self.root / "link"
            try:
                os.symlink(target, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlink unavailable on this platform")
            with self.assertRaises(ara.RepositoryArchiveError):
                ara.write_repository_archive(self.repo, self.head, link)

    def test_dirty_tracked_index_head_and_origin_drift_rejected(self):
        (self.repo / "README.md").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "tracked_or_index_state_not_clean"):
            self._build()
        self._git("checkout", "--", "README.md")
        (self.repo / "new.txt").write_text("new\n", encoding="utf-8")
        self._git("add", "new.txt")
        with self.assertRaises(ara.RepositoryArchiveError):
            self._build()
        self._git("reset", "--hard", "HEAD")
        self._git("commit", "--allow-empty", "-m", "remote-only")
        remote_only = self._git("rev-parse", "HEAD")
        self._git("update-ref", "refs/remotes/origin/main", remote_only)
        self._git("reset", "--hard", "HEAD~1")
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "head_not_equal_local_origin_main"):
            self._build()

    def test_untracked_and_ignored_content_is_not_read_or_included(self):
        (self.repo / ".gitignore").write_text("ignored-secret.env\n", encoding="utf-8")
        self._git("add", ".gitignore")
        self._git("commit", "-m", "ignore rule")
        self.head = self._git("rev-parse", "HEAD")
        self._git("update-ref", "refs/remotes/origin/main", self.head)
        (self.repo / "ignored-secret.env").write_text("TOKEN=do-not-read\n", encoding="utf-8")
        (self.repo / "Req2Web Demo 测试结果包说明.docx").write_text("do-not-read\n", encoding="utf-8")
        manifest, archive_bytes = self._build()
        manifest.validate_archive_bytes(archive_bytes)
        paths = [row["path"] for row in manifest.to_dict()["files"]]
        self.assertNotIn("ignored-secret.env", paths)
        self.assertNotIn("Req2Web Demo 测试结果包说明.docx", paths)

    def test_material_classification_is_explicit_and_ambiguous_fail_closed(self):
        expected = {
            "data/processed/rico_candidate_pool.csv": ("exclude", "restricted_rico_material"),
            "data/processed/rico_review_sheets/commerce.jpg": ("exclude", "restricted_rico_material"),
            "scripts/audit_rico_combined.py": ("include", "explicit_safe_path"),
            "docs/rico_reference_asset_permission_review.md": ("include", "explicit_safe_path"),
            "src/req2web_faults/evaluator_gold.py": ("include", "explicit_safe_path"),
            "tests/test_fault_gold_manifest.py": ("include", "explicit_safe_path"),
            "secrets/api-token.txt": ("exclude", "credential_secret"),
            "models/qwen/weights.safetensors": ("exclude", "model_weight"),
            "artifacts/cache/cache.bin": ("exclude", "generated_output_cache"),
            "misc/rico_secret.csv": ("ambiguous_sensitive", "ambiguous_rico"),
            "data/h1_gold.json": ("ambiguous_sensitive", "ambiguous_h1_gold_evaluator"),
            "misc/evaluator_gold.json": ("ambiguous_sensitive", "ambiguous_h1_gold_evaluator"),
            "docs/reference_only/asset.txt": ("ambiguous_sensitive", "ambiguous_reference_only"),
        }
        self.assertEqual(
            {path: ara._classify_path(path) for path in expected},
            expected,
        )
        for allowed in (
            "src/criterion.py",
            "docs/golden_ratio.md",
            "src/lightweight_adapter.py",
            "data/processed/ricochet_notes.txt",
        ):
            self.assertEqual(
                ara._classify_path(allowed),
                ("include", "ordinary_project_path"),
            )

    def test_ambiguous_sensitive_paths_fail_closed_before_manifest(self):
        for relative in ("misc/rico_secret.csv", "data/h1_gold.json"):
            with self.subTest(relative=relative):
                path = self.repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("ambiguous-sensitive-content\n", encoding="utf-8")
                self._git("add", relative)
                self._git("commit", "-m", f"ambiguous {relative}")
                ambiguous_head = self._git("rev-parse", "HEAD")
                self._git("update-ref", "refs/remotes/origin/main", ambiguous_head)
                with self.assertRaisesRegex(
                    ara.RepositoryArchiveError,
                    "ambiguous_sensitive_path",
                ):
                    ara.build_repository_archive_manifest(self.repo, ambiguous_head)
                self._git("reset", "--hard", "HEAD~1")
                self.head = self._git("rev-parse", "HEAD")
                self._git("update-ref", "refs/remotes/origin/main", self.head)

    def test_restricted_tracked_paths_are_excluded_with_exact_coverage(self):
        self._commit_restricted_shapes()
        manifest, archive_bytes = self._build()
        manifest.validate_archive_bytes(archive_bytes)
        data = manifest.to_dict()
        excluded = {row["path"]: row["reason"] for row in data["excluded_files"]}
        self.assertEqual(excluded, {
            "artifacts/cache/cache.bin": "generated_output_cache",
            "data/h1/case.json": "h1_gold_evaluator_data",
            "data/processed/rico_candidate_pool.csv": "restricted_rico_material",
            "data/processed/rico_review_sheets/commerce.jpg": "restricted_rico_material",
            "data/reference_only/asset.txt": "reference_only_material",
            "models/qwen/weights.safetensors": "model_weight",
            "secrets/api_token.txt": "credential_secret",
        })
        included_paths = [row["path"] for row in data["files"]]
        for allowed in (
            "scripts/audit_rico_combined.py",
            "docs/rico_reference_asset_permission_review.md",
            "src/req2web_faults/evaluator_gold.py",
            "tests/test_fault_gold_manifest.py",
            "src/criterion.py",
            "docs/golden_ratio.md",
            "src/lightweight_adapter.py",
        ):
            self.assertIn(allowed, included_paths)
        self.assertEqual(data["source_tree"]["tracked_file_count"], 16)
        self.assertEqual(data["source_tree"]["included_file_count"], 9)
        self.assertEqual(data["source_tree"]["excluded_file_count"], 7)
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as zf:
            self.assertEqual(zf.namelist(), sorted(included_paths))
            self.assertTrue(set(excluded).isdisjoint(zf.namelist()))

    def test_excluded_blob_content_is_never_read(self):
        identities = {
            "README.md": "a" * 40,
            "data/processed/rico_candidate_pool.csv": "b" * 40,
            "data/processed/rico_review_sheets/commerce.jpg": "c" * 40,
            "scripts/audit_rico_combined.py": "d" * 40,
            "docs/rico_reference_asset_permission_review.md": "e" * 40,
            "src/req2web_faults/evaluator_gold.py": "f" * 40,
            "tests/test_fault_gold_manifest.py": "1" * 40,
        }
        ls_tree = "".join(
            f"100644 blob {blob_sha}\t{path}\0"
            for path, blob_sha in identities.items()
        ).encode("utf-8")
        excluded_shas = {
            identities["data/processed/rico_candidate_pool.csv"],
            identities["data/processed/rico_review_sheets/commerce.jpg"],
        }
        expected_read_shas = [
            blob_sha for blob_sha in identities.values() if blob_sha not in excluded_shas
        ]
        cat_file_shas = []

        class Result:
            def __init__(self, stdout=b"", stderr=b"", returncode=0):
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = returncode

        def fake_run(command, **_kwargs):
            if command[1] == "ls-tree":
                return Result(stdout=ls_tree)
            if command[1:3] == ["cat-file", "blob"]:
                blob_sha = command[-1]
                cat_file_shas.append(blob_sha)
                if blob_sha in excluded_shas:
                    raise AssertionError("excluded blob content was read")
                return Result(stdout=f"allowed:{blob_sha}\n".encode("ascii"))
            raise AssertionError(f"unexpected command: {command}")

        included, excluded = ara._ls_tree_rows(
            self.repo,
            "2" * 40,
            authority=ara._AUTHORITY,
            _run=fake_run,
            _pipe=object(),
        )
        self.assertEqual(cat_file_shas, expected_read_shas)
        self.assertEqual(
            [row["path"] for row in included],
            sorted(path for path, sha in identities.items() if sha not in excluded_shas),
        )
        self.assertEqual(
            {row["path"]: row["reason"] for row in excluded},
            {
                "data/processed/rico_candidate_pool.csv": "restricted_rico_material",
                "data/processed/rico_review_sheets/commerce.jpg": "restricted_rico_material",
            },
        )
        self.assertTrue(
            all(
                set(row) == {"path", "git_mode", "blob_sha", "reason"}
                for row in excluded
            )
        )

    def test_excluded_inventory_tamper_missing_duplicate_and_coverage_drift_fail_closed(self):
        self._commit_restricted_shapes()
        manifest, _ = self._build()

        missing = manifest.to_dict()
        missing["excluded_files"].pop()  # type: ignore[index]
        missing["source_tree"]["excluded_file_count"] -= 1  # type: ignore[index]
        missing["source_tree"]["tracked_file_count"] -= 1  # type: ignore[index]
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "tracked_inventory_sha256_mismatch"):
            self._manifest_from_data(missing)

        duplicate = manifest.to_dict()
        duplicate["excluded_files"].append(dict(duplicate["excluded_files"][0]))  # type: ignore[index]
        duplicate["excluded_files"].sort(key=lambda row: row["path"])  # type: ignore[index]
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "duplicate_excluded_path"):
            self._manifest_from_data(duplicate)

        unordered = manifest.to_dict()
        unordered["excluded_files"].reverse()  # type: ignore[index]
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "excluded_files_not_ordered"):
            self._manifest_from_data(unordered)

        reason_drift = manifest.to_dict()
        reason_drift["excluded_files"][0]["reason"] = "model_weight"  # type: ignore[index]
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "excluded_reason_not_canonical_for_path"):
            self._manifest_from_data(reason_drift)

        coverage_drift = manifest.to_dict()
        coverage_drift["source_tree"]["coverage_sha256"] = "f" * 64  # type: ignore[index]
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "coverage_sha256_mismatch"):
            self._manifest_from_data(coverage_drift)

        count_drift = manifest.to_dict()
        count_drift["source_tree"]["tracked_file_count"] += 1  # type: ignore[index]
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "tracked_file_count_mismatch"):
            self._manifest_from_data(count_drift)

    def test_direct_constructor_and_canonical_bytes_reject_forgery(self):
        manifest, _ = self._build()
        data = manifest.to_dict()
        data["manifest_id"] = "f" * 64
        with self.assertRaises(ara.RepositoryArchiveError):
            ara.RepositoryArchiveManifest(data)
        raw = manifest.canonical_bytes().replace(b'"schema_version"', b'"schema_version","schema_version"', 1)
        with self.assertRaises(ara.RepositoryArchiveError):
            ara.RepositoryArchiveManifest.from_bytes(raw)

    def test_public_signatures_expose_no_trust_override(self):
        manifest, _ = self._build()
        public_routes = (
            ara.RepositoryArchiveManifest,
            ara.RepositoryArchiveManifest.from_dict,
            ara.RepositoryArchiveManifest.from_bytes,
            manifest.to_dict,
            manifest.canonical_bytes,
            manifest.sha256,
            manifest.validate_archive_bytes,
            ara.build_repository_archive_manifest,
            ara.write_repository_archive,
            ara.validate_archive_file,
        )
        for route in public_routes:
            names = tuple(inspect.signature(route).parameters)
            self.assertNotIn("_authority", names)
            self.assertFalse(any("authority" in name or "callback" in name for name in names))

    def test_bypassed_constructor_is_rejected_by_every_public_object_route(self):
        manifest, archive_bytes = self._build()
        forged_data = manifest.to_dict()
        forged_data["manifest_id"] = "f" * 64
        forged = object.__new__(ara.RepositoryArchiveManifest)
        object.__setattr__(forged, "_data", MappingProxyType(forged_data))
        for action in (
            forged.to_dict,
            forged.canonical_bytes,
            forged.sha256,
            lambda: forged.validate_archive_bytes(archive_bytes),
        ):
            with self.assertRaises(ara.RepositoryArchiveError):
                action()
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "manifest_not_canonical"):
            ara.RepositoryArchiveManifest.from_bytes(b" " + manifest.canonical_bytes())
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "manifest_bytes_required"):
            ara.RepositoryArchiveManifest.from_bytes(bytearray(manifest.canonical_bytes()))  # type: ignore[arg-type]

    def test_archive_tamper_missing_extra_duplicate_path_escape_mode_hash(self):
        manifest, _ = self._build()
        rows = manifest.to_dict()["files"]
        tampered = self._zip_from_rows(rows, data_override={"README.md": b"X" * len(b"hello\n")})
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "archive_entry_sha256_drift"):
            self._manifest_for_archive(manifest, tampered).validate_archive_bytes(tampered)
        missing = self._zip_from_rows(rows, omit="README.md")
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "archive_entry_set_mismatch"):
            self._manifest_for_archive(manifest, missing).validate_archive_bytes(missing)
        extra = self._zip_from_rows(rows, extra="extra.txt")
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "archive_entry_set_mismatch"):
            self._manifest_for_archive(manifest, extra).validate_archive_bytes(extra)
        duplicate = self._zip_from_rows(rows, duplicate=True)
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "archive_duplicate_entry"):
            self._manifest_for_archive(manifest, duplicate).validate_archive_bytes(duplicate)
        escape = self._zip_from_rows(rows, extra="../evil.txt")
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "path"):
            self._manifest_for_archive(manifest, escape).validate_archive_bytes(escape)
        mode = self._zip_from_rows(rows, mode_override={"README.md": "100755"})
        with self.assertRaisesRegex(ara.RepositoryArchiveError, "mode_drift"):
            self._manifest_for_archive(manifest, mode).validate_archive_bytes(mode)

    def test_archive_symlink_mode_rejected_by_synthetic_fixture(self):
        manifest, _ = self._build()
        rows = manifest.to_dict()["files"]
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            for row in rows:
                info = zipfile.ZipInfo(row["path"], date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = ((0o120777 if row["path"] == "README.md" else 0o100644) & 0xFFFF) << 16
                zf.writestr(info, b"x" if row["path"] == "README.md" else b"print('ok')\n")
        bad = out.getvalue()
        with self.assertRaises(ara.RepositoryArchiveError):
            self._manifest_for_archive(manifest, bad).validate_archive_bytes(bad)

    def test_definition_time_trust_bundle_survives_module_rebinding(self):
        trusted_manifest_type = ara.RepositoryArchiveManifest
        trusted_error_type = ara.RepositoryArchiveError
        trusted_build = ara.build_repository_archive_manifest
        trusted_write = ara.write_repository_archive
        trusted_validate = ara.validate_archive_file
        manifest, archive_bytes = trusted_build(self.repo, self.head)

        def trap(*_args, **_kwargs):
            raise AssertionError("rebound module global was consulted")

        class ForgedManifest:
            from_dict = staticmethod(trap)
            from_bytes = staticmethod(trap)

        replacements = {
            "_AUTHORITY": object(),
            "_validate_manifest_dict": trap,
            "_validate_excluded_file_row": trap,
            "_inventory_identities": trap,
            "_contains_token_sequence": trap,
            "_classify_path": trap,
            "_deepcopy_json": trap,
            "_validate_zip_archive": trap,
            "_run_git": trap,
            "_git_root": trap,
            "_validate_git_state": trap,
            "_ls_tree_rows": trap,
            "_make_archive": trap,
            "_manifest_body": trap,
            "_validate_output_directory": trap,
            "_zip_mode": trap,
            "RepositoryArchiveManifest": ForgedManifest,
            "RepositoryArchiveError": RuntimeError,
            "Path": trap,
            "Mapping": trap,
            "SCHEMA_VERSION": "forged.schema",
            "FOUNDATION_STATUS": "forged_status",
            "_POLICY_SHA256": "f" * 64,
            "_POLICY_REASON_CODES": (),
            "_AMBIGUOUS_REASON_CODES": (),
            "_SOURCE_TREE_KEYS": (),
            "_EXCLUDED_FILE_KEYS": (),
            "_EXPLICIT_SAFE_PATHS": (),
            "_EXPLICIT_EXCLUDED_PREFIX_RULES": (),
            "_RICO_MATERIAL_ROOT": "forged/",
            "_RICO_MATERIAL_COMPONENT_NAMES": ("forged",),
            "_RICO_MATERIAL_COMPONENT_PREFIXES": ("forged_",),
            "_EXACT_COMPONENT_RULES": (),
            "_AMBIGUOUS_TOKEN_SEQUENCE_RULES": (),
            "_SUFFIX_RULES": (),
            "_BASENAME_RULES": (),
            "build_repository_archive_manifest": trap,
            "write_repository_archive": trap,
            "validate_archive_file": trap,
        }
        originals = {name: getattr(ara, name) for name in replacements}
        try:
            for name, value in replacements.items():
                setattr(ara, name, value)

            rebuilt, rebuilt_archive = trusted_build(self.repo, self.head)
            self.assertEqual(rebuilt.canonical_bytes(), manifest.canonical_bytes())
            self.assertEqual(rebuilt_archive, archive_bytes)

            out = self.root / "rebound-out"
            out.mkdir()
            written = trusted_write(self.repo, self.head, out)
            reread = trusted_validate(
                out / ara.ARCHIVE_FILENAME,
                out / ara.MANIFEST_FILENAME,
            )
            self.assertEqual(written.sha256(), reread.sha256())

            forged_data = manifest.to_dict()
            forged_data["manifest_id"] = "f" * 64
            with self.assertRaises(trusted_error_type):
                trusted_manifest_type.from_dict(forged_data)
            forged_bytes = json.dumps(
                forged_data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            (out / ara.MANIFEST_FILENAME).write_bytes(forged_bytes)
            with self.assertRaises(trusted_error_type):
                trusted_validate(
                    out / ara.ARCHIVE_FILENAME,
                    out / ara.MANIFEST_FILENAME,
                )

            (self.repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
            self._git("add", ".env")
            self._git("commit", "-m", "prohibited-after-rebind")
            prohibited_head = self._git("rev-parse", "HEAD")
            self._git("update-ref", "refs/remotes/origin/main", prohibited_head)
            restricted, restricted_archive = trusted_build(self.repo, prohibited_head)
            restricted.validate_archive_bytes(restricted_archive)
            excluded = {
                row["path"]: row["reason"]
                for row in restricted.to_dict()["excluded_files"]
            }
            self.assertEqual(excluded[".env"], "credential_secret")

            ambiguous_path = self.repo / "misc/rico_secret.csv"
            ambiguous_path.parent.mkdir(parents=True, exist_ok=True)
            ambiguous_path.write_text("ambiguous\n", encoding="utf-8")
            self._git("add", "misc/rico_secret.csv")
            self._git("commit", "-m", "ambiguous-after-rebind")
            ambiguous_head = self._git("rev-parse", "HEAD")
            self._git("update-ref", "refs/remotes/origin/main", ambiguous_head)
            with self.assertRaisesRegex(trusted_error_type, "ambiguous_sensitive_path"):
                trusted_build(self.repo, ambiguous_head)
        finally:
            for name, value in originals.items():
                setattr(ara, name, value)


if __name__ == "__main__":
    unittest.main()
