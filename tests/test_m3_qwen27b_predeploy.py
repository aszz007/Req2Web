"""Focused tests for the Qwen3.5-27B no-GPU predeploy contract."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime import qwen27b_predeploy as predeploy


class Qwen27BPredeployTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / f"tmp-qwen27b-predeploy-{uuid4().hex}"
        self.root.mkdir()
        self.model = self.root / "model"
        self.model.mkdir()
        for name in predeploy._MODEL_FILES:
            path = self.model / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture:" + name).encode("utf-8"))

        self.runtime_root = self.root / "runtime"
        for relative in ("bin/python", *predeploy._RUNTIME_STATIC_FILES):
            path = self.runtime_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("runtime:" + relative).encode("utf-8"))
        for relative in (
            "lib/python3.11/site-packages/transformers/modeling_utils.py",
            "lib/python3.11/site-packages/transformers/models/qwen3_5/modeling_qwen3_5.py",
            "lib/python3.11/site-packages/torch/lib/libtorch_cuda.so",
        ):
            path = self.runtime_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("runtime:" + relative).encode("utf-8"))
        self.runtime = predeploy.collect_runtime_root_inventory(self.runtime_root)
        self.runtime_bytes = predeploy._dump(self.runtime)
        self.probe = predeploy._create_no_gpu_runtime_probe(self.runtime_bytes)
        self.probe_bytes = predeploy._dump(self.probe)

        self.archive = self.root / "req2web.zip"
        self.archive.write_bytes(b"small-archive")
        self.cases = self.root / "cases"
        self.cases.mkdir()
        (self.cases / "case.json").write_bytes(b"case-bundle")
        self.archive_inventory = predeploy.collect_file_inventory(
            self.archive, "repository_archive"
        )
        archive_row = self.archive_inventory.to_dict()["files"][0]
        self.archive_authority = {
            "manifest_id": "a" * 64,
            "manifest_sha256": "b" * 64,
            "commit_sha": "1" * 40,
            "tree_sha": "2" * 40,
            "archive_byte_length": archive_row["bytes"],
            "archive_sha256": archive_row["sha256"],
        }
        official = predeploy._official_model_inventory_authority()["inventory"]
        self.model_inventory = predeploy.Qwen27BPredeployInventory.from_dict(
            official
        )
        self.case_inventory = predeploy.collect_directory_inventory(
            self.cases, "case_bundle"
        )
        self.manifest = predeploy.create_predeploy_manifest(
            self.model_inventory,
            self.runtime_bytes,
            self.probe_bytes,
            self.archive_inventory,
            self.case_inventory,
            self.archive_authority,
            self.archive_authority,
            source_commit="1" * 40,
            source_tree="2" * 40,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def rejected(self, call, contains: str) -> None:
        with self.assertRaises(predeploy.Qwen27BPredeployError) as raised:
            call()
        self.assertIn(contains, str(raised.exception))

    def _bundle(self) -> SimpleNamespace:
        return SimpleNamespace(
            manifest={
                "source": {
                    "repository_archive_authority": self.archive_authority
                }
            }
        )

    def _ready(self, selected_copy: str = "none", manifest=None):
        with (
            mock.patch.object(
                predeploy,
                "collect_model_root_inventory",
                return_value=self.model_inventory,
            ),
            mock.patch(
                "req2web_runtime.qwen27b_case_bundle."
                "validate_qwen27b_case_bundle",
                return_value=self._bundle(),
            ),
        ):
            return predeploy.create_predeploy_ready_no_gpu(
                self.manifest if manifest is None else manifest,
                self.model,
                self.archive,
                self.cases,
                selected_copy,
            )

    def test_official_inventory_is_exact_revision_authority(self) -> None:
        authority = predeploy._official_model_inventory_authority()
        self.assertEqual(authority["repository"], "Qwen/Qwen3.5-27B")
        self.assertEqual(authority["revision"], predeploy.MODEL_REVISION)
        self.assertEqual(authority["file_count"], 24)
        self.assertEqual(authority["total_bytes"], 55_586_167_982)
        self.assertEqual(
            authority["inventory"]["files"],
            sorted(
                authority["inventory"]["files"],
                key=lambda row: row["relative_path"],
            ),
        )

    def test_model_inventory_hashes_in_bounded_chunks(self) -> None:
        rows = []
        for name in predeploy._MODEL_FILES:
            raw = (self.model / name).read_bytes()
            rows.append(
                {
                    "relative_path": name,
                    "bytes": len(raw),
                    "sha256": predeploy._sha(raw),
                }
            )
        expected = predeploy._inventory_data(
            "model_root",
            sorted(rows, key=lambda row: row["relative_path"]),
        )
        with (
            mock.patch.object(
                predeploy,
                "_official_model_inventory_authority",
                return_value={"inventory": expected},
            ),
            mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("model inventory must stream files"),
            ),
        ):
            inventory = predeploy.collect_model_root_inventory(self.model)
            self.assertEqual(inventory.to_dict(), expected)

    def test_arbitrary_fixture_bytes_cannot_claim_official_revision(self) -> None:
        self.rejected(
            lambda: predeploy.collect_model_root_inventory(self.model),
            "model_official_inventory_mismatch",
        )
        forged = copy.deepcopy(self.model_inventory.to_dict())
        forged["files"][0]["sha256"] = "f" * 64
        forged["tree_sha256"] = predeploy._sha(
            predeploy._dump(forged["files"])
        )
        forged["inventory_id"] = "0" * 64
        forged = predeploy._identified(forged, "inventory_id")
        self.rejected(
            lambda: predeploy.Qwen27BPredeployInventory(forged).canonical_bytes(),
            "model_official_inventory_mismatch",
        )

    def test_runtime_inventory_is_collected_from_and_bound_to_root(self) -> None:
        second = self.root / "runtime-copy"
        shutil.copytree(self.runtime_root, second)
        copied = predeploy.collect_runtime_root_inventory(second)
        self.assertNotEqual(copied["inventory_id"], self.runtime["inventory_id"])
        self.assertNotEqual(
            copied["runtime_root_sha256"], self.runtime["runtime_root_sha256"]
        )
        self.rejected(
            lambda: predeploy.create_runtime_inventory(
                [{"relative_path": "bin/python"}]
            ),
            "runtime_root_path_required",
        )

    def test_runtime_file_and_probe_executable_drift_fail_closed(self) -> None:
        target = self.runtime_root / predeploy._RUNTIME_STATIC_FILES[0]
        target.write_bytes(b"runtime-drift")
        with (
            mock.patch.object(
                predeploy,
                "collect_model_root_inventory",
                return_value=self.model_inventory,
            ),
            mock.patch(
                "req2web_runtime.qwen27b_case_bundle."
                "validate_qwen27b_case_bundle",
                return_value=self._bundle(),
            ),
        ):
            self.rejected(
                lambda: predeploy.create_predeploy_ready_no_gpu(
                    self.manifest,
                    self.model,
                    self.archive,
                    self.cases,
                    "none",
                ),
                "ready_runtime_inventory_mismatch",
            )
        bad_probe = copy.deepcopy(self.probe)
        bad_probe["observed"]["sys_executable_relative_path"] = "bin/python3.11"
        bad_probe = predeploy._identified(bad_probe, "probe_id")
        self.rejected(
            lambda: predeploy._runtime_probe(
                predeploy._dump(bad_probe), self.runtime_bytes
            ),
            "runtime_probe_observation_invalid",
        )

    def test_runtime_inventory_covers_modeling_and_cuda_native_files(self) -> None:
        targets = (
            "lib/python3.11/site-packages/transformers/modeling_utils.py",
            "lib/python3.11/site-packages/transformers/models/qwen3_5/modeling_qwen3_5.py",
            "lib/python3.11/site-packages/torch/lib/libtorch_cuda.so",
        )
        for relative in targets:
            with self.subTest(relative=relative):
                target = self.runtime_root / relative
                original = target.read_bytes()
                target.write_bytes(b"modified-executable")
                self.rejected(
                    lambda: self._ready(),
                    "ready_runtime_inventory_mismatch",
                )
                target.write_bytes(original)

    def test_runtime_inventory_detects_new_regular_file(self) -> None:
        added = self.runtime_root / (
            "lib/python3.11/site-packages/transformers/new_executable.py"
        )
        added.write_bytes(b"new executable")
        self.rejected(
            lambda: self._ready(),
            "ready_runtime_inventory_mismatch",
        )

    def test_runtime_inventory_canonical_roundtrip_covers_complete_tree(self) -> None:
        replayed = predeploy._runtime_inventory(self.runtime_bytes)
        self.assertEqual(replayed, self.runtime)
        self.assertEqual(replayed["file_count"], len(replayed["files"]))
        self.assertEqual(replayed["symlink_count"], len(replayed["symlinks"]))
        self.assertEqual(
            replayed["entry_count"],
            replayed["file_count"] + replayed["symlink_count"],
        )
        self.assertEqual(
            replayed["total_bytes"],
            sum(row["bytes"] for row in replayed["files"]),
        )
        self.assertIn(
            "lib/python3.11/site-packages/torch/lib/libtorch_cuda.so",
            {row["relative_path"] for row in replayed["files"]},
        )

    def _symlink_or_skip(
        self, link: Path, target: str | Path, *, target_is_directory: bool = False
    ) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not permitted")

    def _manifest_for_current_runtime(self):
        runtime = predeploy.collect_runtime_root_inventory(self.runtime_root)
        runtime_bytes = predeploy._dump(runtime)
        probe_bytes = predeploy._dump(
            predeploy._create_no_gpu_runtime_probe(runtime_bytes)
        )
        return predeploy.create_predeploy_manifest(
            self.model_inventory,
            runtime_bytes,
            probe_bytes,
            self.archive_inventory,
            self.case_inventory,
            self.archive_authority,
            self.archive_authority,
            source_commit="1" * 40,
            source_tree="2" * 40,
        )

    def test_runtime_internal_interpreter_file_symlink_is_accepted(self) -> None:
        interpreter = self.runtime_root / "bin/python"
        target = self.runtime_root / "bin/python3.11"
        interpreter.replace(target)
        self._symlink_or_skip(interpreter, "python3.11")
        inventory = predeploy.collect_runtime_root_inventory(self.runtime_root)
        self.assertEqual(inventory["interpreter_relative_path"], "bin/python3.11")
        self.assertIn(
            {
                "relative_path": "bin/python",
                "readlink_target": "python3.11",
                "resolved_relative_path": "bin/python3.11",
                "target_type": "file",
            },
            inventory["symlinks"],
        )

    def test_runtime_internal_shared_library_symlink_retarget_fails_ready(self) -> None:
        link = self.runtime_root / "lib/python3.11/site-packages/torch/lib/libtorch_cuda.so"
        first = link.with_name("libtorch_cuda.so.1")
        second = link.with_name("libtorch_cuda.so.2")
        link.replace(first)
        second.write_bytes(b"second-shared-library")
        self._symlink_or_skip(link, first.name)
        manifest = self._manifest_for_current_runtime()
        link.unlink()
        self._symlink_or_skip(link, second.name)
        self.rejected(
            lambda: self._ready(manifest=manifest),
            "ready_runtime_inventory_mismatch",
        )

    def test_runtime_internal_directory_symlink_is_recorded_without_rewalk(self) -> None:
        target = self.runtime_root / "lib/python3.11/site-packages/shared-target"
        target.mkdir()
        (target / "payload.py").write_bytes(b"payload")
        link = self.runtime_root / "lib/python3.11/site-packages/shared-alias"
        self._symlink_or_skip(link, target.name, target_is_directory=True)
        inventory = predeploy.collect_runtime_root_inventory(self.runtime_root)
        target_path = "lib/python3.11/site-packages/shared-target/payload.py"
        self.assertEqual(
            [row["relative_path"] for row in inventory["files"]].count(target_path),
            1,
        )
        self.assertNotIn(
            "lib/python3.11/site-packages/shared-alias/payload.py",
            {row["relative_path"] for row in inventory["files"]},
        )
        self.assertIn(
            "lib/python3.11/site-packages/shared-alias",
            {row["relative_path"] for row in inventory["symlinks"]},
        )

    def test_runtime_external_file_and_directory_symlinks_are_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        outside_file = outside / "payload.bin"
        outside_file.write_bytes(b"outside")
        for name, target, is_directory in (
            ("escape-file", outside_file, False),
            ("escape-directory", outside, True),
        ):
            with self.subTest(name=name):
                link = self.runtime_root / "lib/python3.11/site-packages" / name
                self._symlink_or_skip(
                    link, target, target_is_directory=is_directory
                )
                self.rejected(
                    lambda: predeploy.collect_runtime_root_inventory(
                        self.runtime_root
                    ),
                    "runtime_symlink_target_invalid",
                )
                link.unlink()

    def test_runtime_broken_and_cyclic_symlinks_are_rejected(self) -> None:
        package_root = self.runtime_root / "lib/python3.11/site-packages"
        broken = package_root / "broken-link"
        self._symlink_or_skip(broken, "missing-target")
        self.rejected(
            lambda: predeploy.collect_runtime_root_inventory(self.runtime_root),
            "runtime_symlink_target_invalid",
        )
        broken.unlink()
        cycle_root = package_root / "cycle-root"
        cycle_root.mkdir()
        cycle = cycle_root / "cycle-link"
        self._symlink_or_skip(cycle, ".", target_is_directory=True)
        self.rejected(
            lambda: predeploy.collect_runtime_root_inventory(self.runtime_root),
            "runtime_symlink_cycle_forbidden",
        )

    def test_runtime_symlink_canonical_roundtrip_and_tamper_rejection(self) -> None:
        link = self.runtime_root / "lib/python3.11/site-packages/torch/lib/libtorch.so"
        target = link.with_name("libtorch.so.1")
        target.write_bytes(b"shared-library")
        self._symlink_or_skip(link, target.name)
        inventory = predeploy.collect_runtime_root_inventory(self.runtime_root)
        raw = predeploy._dump(inventory)
        self.assertEqual(predeploy._runtime_inventory(raw), inventory)
        tampered = copy.deepcopy(inventory)
        tampered["symlinks"][0]["readlink_target"] = "other-target"
        tampered = predeploy._identified(tampered, "inventory_id")
        self.rejected(
            lambda: predeploy._runtime_inventory(predeploy._dump(tampered)),
            "runtime_inventory_tree_invalid",
        )

    def test_case_bundle_cross_tree_authority_is_rejected(self) -> None:
        other = dict(self.archive_authority)
        other["tree_sha"] = "3" * 40
        self.rejected(
            lambda: predeploy.create_predeploy_manifest(
                self.model_inventory,
                self.runtime_bytes,
                self.probe_bytes,
                self.archive_inventory,
                self.case_inventory,
                self.archive_authority,
                other,
                source_commit="1" * 40,
                source_tree="2" * 40,
            ),
            "case_bundle_archive_authority_mismatch",
        )

    def test_primary_transport_and_optional_copy_are_distinct(self) -> None:
        profile = predeploy.create_profile()
        self.assertEqual(
            profile.data["transport"]["primary_transport"],
            "autodl_same_region_data_disk_clone",
        )
        ready = self._ready()
        self.assertEqual(
            ready.data["transport"],
            {
                "primary_transport": "autodl_same_region_data_disk_clone",
                "selected_copy": "none",
            },
        )
        self.rejected(
            lambda: self._ready("aliyundrive_autopanel_backup"),
            "ready_copy_binding_invalid",
        )
        changed = copy.deepcopy(self.manifest.to_dict())
        changed["transport"]["primary_transport"] = "aliyundrive_autopanel_backup"
        changed = predeploy._identified(changed, "manifest_id")
        self.rejected(
            lambda: predeploy.Qwen27BPredeployManifest(changed).canonical_bytes(),
            "manifest_transport_invalid",
        )

    def test_manifest_binds_official_inventory_authority(self) -> None:
        persisted = self.manifest.to_dict()
        authority = predeploy._official_model_inventory_authority()
        authority.pop("inventory")
        self.assertEqual(
            persisted["official_model_inventory_authority"], authority
        )
        tampered = copy.deepcopy(persisted)
        tampered["official_model_inventory_authority"]["authority_id"] = "f" * 64
        tampered = predeploy._identified(tampered, "manifest_id")
        self.rejected(
            lambda: predeploy.Qwen27BPredeployManifest(
                tampered
            ).canonical_bytes(),
            "official_model_inventory_authority_mismatch",
        )


if __name__ == "__main__":
    unittest.main()
