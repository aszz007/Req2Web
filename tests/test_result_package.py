from __future__ import annotations

import hashlib
import json
import shutil
import sys
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain  # noqa: E402
from req2web_generation import (  # noqa: E402
    RESULT_PACKAGE_SCHEMA_VERSION,
    RESULT_SUMMARY_SCHEMA_VERSION,
    ConsistencyReport,
    DeterministicPageRenderer,
    DeterministicResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    ResultPackageError,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402


ECOMMERCE_REQUIREMENT = (
    "我想做一个移动端电商应用，支持搜索筛选商品、查看详情、加入购物车和结算，"
    "需要清楚的异常反馈。"
)
PET_REQUIREMENT = (
    "做一个宠物情绪识别 App，用户拍照后系统分析宠物情绪并展示结果，"
    "相机权限被拒绝时要给出恢复提示。"
)
ECOMMERCE_RECOVERY_CONSTRAINT = "输入错误时给出可恢复提示"


class FixtureRetriever:
    def __init__(self, *, absolute_reference: bool = False) -> None:
        self.absolute_reference = absolute_reference

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[dict[str, object]]:
        role = tuple(roles or ("requirement",))[0]
        return [
            {
                "score": 1.0 - index / 10,
                "doc_id": f"{role}:fixture:sample-{index}",
                "role": role,
                "title": f"{role} fixture {index}",
                "references": [
                    {
                        "kind": "fixture",
                        "uri": (
                            str(ROOT / "data/raw/fixture.json")
                            if self.absolute_reference
                            else f"fixtures/{role}/sample-{index}.json"
                        ),
                    }
                ],
            }
            for index in range(1, top_k + 1)
        ]

    def search_by_role(
        self, query: str, top_k: int = 2
    ) -> dict[str, list[dict[str, object]]]:
        return {
            role: self.search(query, top_k=top_k, roles=(role,))
            for role in ROLE_ORDER
        }


def build_context(
    requirement: str,
    *,
    absolute_reference: bool = False,
    constraints: Iterable[str] = (),
):
    return MinimalAgentChain(
        DeterministicRequirementProvider(),
        FixtureRetriever(absolute_reference=absolute_reference),
        top_k_per_role=2,
    ).run(requirement, constraints=list(constraints))


def failed_report(report: ConsistencyReport) -> ConsistencyReport:
    checks = list(report.checks)
    pass_index = next(
        index for index, item in enumerate(checks) if item.status == "pass"
    )
    checks[pass_index] = replace(checks[pass_index], status="fail")
    counts = Counter(item.status for item in checks)
    failed = ConsistencyReport(
        page_id=report.page_id,
        passed=False,
        summary={
            "total": len(checks),
            "pass": counts["pass"],
            "fail": counts["fail"],
            "warning": counts["warning"],
        },
        checks=checks,
        warnings=[item.message for item in checks if item.status == "warning"],
    )
    failed.validate()
    return failed


class ResultPackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_result_package_v2" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.packager = DeterministicResultPackager()
        self.context = build_context(ECOMMERCE_REQUIREMENT)
        self.spec = PageSpecBuilder().build(self.context)
        self.render_result = DeterministicPageRenderer().render(
            self.spec,
            self.root / "render",
        )
        self.report = MinimalConsistencyChecker().check(
            self.spec,
            self.render_result,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        try:
            self.root.parent.rmdir()
        except OSError:
            pass

    def _package(self, name: str = "package"):
        return self.packager.package(
            self.context,
            self.spec,
            self.render_result,
            self.report,
            self.root / name,
        )

    def _load(self, package, relative_path: str):
        return json.loads(
            (package.package_dir / relative_path).read_text(encoding="utf-8")
        )

    def test_ecommerce_result_package_is_generated(self) -> None:
        package = self._package()
        package.validate()
        self.assertEqual(package.schema_version, RESULT_PACKAGE_SCHEMA_VERSION)
        self.assertEqual(package.page_id, self.spec.page_id)
        self.assertTrue((package.package_dir / package.entrypoint).is_file())

    def test_pet_recognition_result_package_is_generated(self) -> None:
        context = build_context(PET_REQUIREMENT)
        spec = PageSpecBuilder().build(context)
        result = DeterministicPageRenderer().render(spec, self.root / "pet-render")
        report = MinimalConsistencyChecker().check(spec, result)
        package = self.packager.package(
            context,
            spec,
            result,
            report,
            self.root / "pet-package",
        )
        package.validate()
        summary = self._load(package, "result_summary.json")
        self.assertEqual(summary["page_type"], "recognition_tool")
        self.assertTrue(summary["quality_gate"]["passed"])

    def test_fixed_directory_structure_is_complete(self) -> None:
        package = self._package()
        actual = sorted(
            path.relative_to(package.package_dir).as_posix()
            for path in package.package_dir.rglob("*")
            if path.is_file()
        )
        self.assertEqual(
            actual,
            sorted(
                (
                    "page/index.html",
                    "page/styles.css",
                    "page/app.js",
                    "page/render_manifest.json",
                    "internal/agent_context.json",
                    "internal/page_spec.json",
                    "internal/consistency_report.json",
                    "result_summary.json",
                    "package_manifest.json",
                )
            ),
        )

    def test_internal_artifacts_are_exact_serializations(self) -> None:
        package = self._package()
        self.assertEqual(
            self._load(package, "internal/agent_context.json"),
            self.context.to_dict(),
        )
        self.assertEqual(
            self._load(package, "internal/page_spec.json"),
            self.spec.to_dict(),
        )
        self.assertEqual(
            self._load(package, "internal/consistency_report.json"),
            self.report.to_dict(),
        )

    def test_result_summary_contains_deterministic_text_description(self) -> None:
        summary = self._load(self._package(), "result_summary.json")
        self.assertEqual(summary["schema_version"], RESULT_SUMMARY_SCHEMA_VERSION)
        self.assertIn(self.spec.title, summary["text_description"])
        self.assertIn(self.spec.summary, summary["text_description"])
        for use_case in self.spec.use_cases:
            self.assertIn(use_case.title, summary["text_description"])
        for constraint in self.spec.constraints:
            self.assertIn(constraint.description, summary["text_description"])

    def test_result_summary_contains_lightweight_ui_references_only(self) -> None:
        summary = self._load(self._package(), "result_summary.json")
        references = summary["ui_references"]
        self.assertTrue(references)
        for reference in references:
            self.assertEqual(
                set(reference), {"doc_id", "title", "reference_uris"}
            )
            self.assertTrue(reference["doc_id"].startswith("ui_reference:"))

    def test_result_summary_contains_stable_interaction_flow(self) -> None:
        summary = self._load(self._package(), "result_summary.json")
        flow = summary["interaction_flow"]
        self.assertEqual([item["step"] for item in flow], list(range(1, len(flow) + 1)))
        self.assertEqual(
            {item["trigger_component_id"] for item in flow},
            {item.trigger_component_id for item in self.spec.interactions},
        )
        for item in flow:
            self.assertEqual(
                set(item),
                {
                    "step",
                    "use_case_ids",
                    "trigger_component_id",
                    "action",
                    "user_feedback",
                    "target_state_id",
                },
            )

    def test_quality_gate_preserves_all_three_ecommerce_warnings(self) -> None:
        self.assertEqual(self.report.summary["warning"], 3)
        summary = self._load(self._package(), "result_summary.json")
        gate = summary["quality_gate"]
        self.assertEqual(gate["warning"], 3)
        self.assertEqual(gate["warnings"], self.report.warnings)
        self.assertEqual(
            gate["consistency_report"], "internal/consistency_report.json"
        )

    def test_result_summary_lists_user_and_internal_artifacts_separately(self) -> None:
        summary = self._load(self._package(), "result_summary.json")
        artifacts = {item["path"]: item["role"] for item in summary["artifacts"]}
        self.assertEqual(artifacts["page/index.html"], "browser_entrypoint")
        self.assertEqual(artifacts["page/styles.css"], "page_styles")
        self.assertEqual(artifacts["page/app.js"], "page_script")
        self.assertEqual(artifacts["page/render_manifest.json"], "render_manifest")
        self.assertEqual(artifacts["internal/page_spec.json"], "page_spec")
        self.assertEqual(
            artifacts["internal/consistency_report.json"], "consistency_report"
        )

    def test_package_manifest_declares_every_file_except_itself(self) -> None:
        package = self._package()
        manifest = self._load(package, "package_manifest.json")
        declared = [item["path"] for item in manifest["files"]]
        self.assertEqual(declared, sorted(declared))
        self.assertNotIn("package_manifest.json", declared)
        actual_without_manifest = sorted(
            path.relative_to(package.package_dir).as_posix()
            for path in package.package_dir.rglob("*")
            if path.is_file() and path.name != "package_manifest.json"
        )
        self.assertEqual(declared, actual_without_manifest)

    def test_package_manifest_hashes_and_sizes_match_final_bytes(self) -> None:
        package = self._package()
        manifest = self._load(package, "package_manifest.json")
        for entry in manifest["files"]:
            content = (package.package_dir / entry["path"]).read_bytes()
            self.assertEqual(entry["size"], len(content), entry["path"])
            self.assertEqual(
                entry["sha256"],
                hashlib.sha256(content).hexdigest(),
                entry["path"],
            )

    def test_all_package_references_are_posix_relative_paths(self) -> None:
        package = self._package()
        manifest = self._load(package, "package_manifest.json")
        summary = self._load(package, "result_summary.json")
        paths = [
            manifest["entrypoint"],
            manifest["result_summary"],
            *(item["path"] for item in manifest["files"]),
            summary["entrypoint"],
            summary["quality_gate"]["consistency_report"],
            *(item["path"] for item in summary["artifacts"]),
        ]
        for value in paths:
            self.assertNotIn("\\", value)
            self.assertFalse(Path(value).is_absolute(), value)
            self.assertNotIn("..", value.split("/"), value)

    def test_package_contains_no_workspace_absolute_path(self) -> None:
        package = self._package()
        content = b"\n".join(
            path.read_bytes()
            for path in package.package_dir.rglob("*")
            if path.is_file()
        ).decode("utf-8")
        self.assertNotIn(str(ROOT), content)
        self.assertNotIn(str(ROOT).replace("\\", "/"), content)

    def test_same_input_in_different_directories_is_byte_identical(self) -> None:
        first = self._package("first")
        second = self._package("second")
        self.assertEqual(first.package_id, second.package_id)
        first_files = sorted(
            path.relative_to(first.package_dir).as_posix()
            for path in first.package_dir.rglob("*")
            if path.is_file()
        )
        second_files = sorted(
            path.relative_to(second.package_dir).as_posix()
            for path in second.package_dir.rglob("*")
            if path.is_file()
        )
        self.assertEqual(first_files, second_files)
        for relative_path in first_files:
            self.assertEqual(
                (first.package_dir / relative_path).read_bytes(),
                (second.package_dir / relative_path).read_bytes(),
                relative_path,
            )

    def test_recovery_flow_and_all_artifacts_are_deterministic(self) -> None:
        builds = []
        for name in ("recovery-first", "recovery-second"):
            context = build_context(
                ECOMMERCE_REQUIREMENT,
                constraints=(ECOMMERCE_RECOVERY_CONSTRAINT,),
            )
            spec = PageSpecBuilder().build(context)
            render = DeterministicPageRenderer().render(
                spec,
                self.root / f"{name}-render",
            )
            report = MinimalConsistencyChecker().check(spec, render)
            package = self.packager.package(
                context,
                spec,
                render,
                report,
                self.root / name,
            )
            package.validate()
            builds.append((spec, render, report, package))

        first_spec, first_render, first_report, first_package = builds[0]
        second_spec, second_render, second_report, second_package = builds[1]
        self.assertEqual(first_spec.to_dict(), second_spec.to_dict())
        self.assertEqual(first_report.to_dict(), second_report.to_dict())
        self.assertTrue(first_report.passed)
        self.assertEqual(first_report.summary["fail"], 0)
        self.assertFalse(
            any(
                item.check_id == "warning.unreachable-state:state-error"
                for item in first_report.checks
            )
        )
        for filename in (
            "index.html",
            "styles.css",
            "app.js",
            "render_manifest.json",
        ):
            self.assertEqual(
                (first_render.output_dir / filename).read_bytes(),
                (second_render.output_dir / filename).read_bytes(),
                filename,
            )

        first_files = sorted(
            path.relative_to(first_package.package_dir).as_posix()
            for path in first_package.package_dir.rglob("*")
            if path.is_file()
        )
        second_files = sorted(
            path.relative_to(second_package.package_dir).as_posix()
            for path in second_package.package_dir.rglob("*")
            if path.is_file()
        )
        self.assertEqual(len(first_files), 9)
        self.assertEqual(first_files, second_files)
        for relative_path in first_files:
            self.assertEqual(
                (first_package.package_dir / relative_path).read_bytes(),
                (second_package.package_dir / relative_path).read_bytes(),
                relative_path,
            )

        summary = self._load(first_package, "result_summary.json")
        transitions = {
            (item["action"], item["target_state_id"])
            for item in summary["interaction_flow"]
        }
        self.assertIn(("模拟输入错误", "state-error"), transitions)
        self.assertIn(("恢复：修改输入并重试", "state-initial"), transitions)

    def test_failed_consistency_report_is_rejected(self) -> None:
        with self.assertRaisesRegex(ResultPackageError, "did not pass"):
            self.packager.package(
                self.context,
                self.spec,
                self.render_result,
                failed_report(self.report),
                self.root / "rejected",
            )

    def test_nonzero_fail_count_is_rejected(self) -> None:
        invalid = replace(
            self.report,
            summary={**self.report.summary, "fail": 1},
        )
        with self.assertRaisesRegex(ValueError, "summary does not match"):
            self.packager.package(
                self.context,
                self.spec,
                self.render_result,
                invalid,
                self.root / "rejected",
            )

    def test_page_id_mismatch_is_rejected(self) -> None:
        mismatched = replace(self.render_result, page_id="page-mismatched")
        with self.assertRaisesRegex(ResultPackageError, "page_id values must match"):
            self.packager.package(
                self.context,
                self.spec,
                mismatched,
                self.report,
                self.root / "rejected",
            )

    def test_context_source_mismatch_is_rejected(self) -> None:
        other_context = build_context(PET_REQUIREMENT)
        with self.assertRaisesRegex(ResultPackageError, "do not match"):
            self.packager.package(
                other_context,
                self.spec,
                self.render_result,
                self.report,
                self.root / "rejected",
            )

    def test_static_file_tampered_after_report_is_rejected(self) -> None:
        self.render_result.index_html.write_text(
            self.render_result.index_html.read_text(encoding="utf-8")
            + "<!-- tampered after report -->\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ResultPackageError, "SHA-256 does not match"):
            self._package("rejected")

    def test_render_manifest_hash_error_is_rejected(self) -> None:
        manifest = json.loads(
            self.render_result.render_manifest.read_text(encoding="utf-8")
        )
        manifest["files"][0]["sha256"] = "0" * 64
        self.render_result.render_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ResultPackageError, "SHA-256 does not match"):
            self._package("rejected")

    def test_missing_render_file_is_rejected(self) -> None:
        self.render_result.styles_css.unlink()
        with self.assertRaisesRegex(ResultPackageError, "required render file is missing"):
            self._package("rejected")

    def test_nonempty_destination_is_rejected_without_overwrite(self) -> None:
        destination = self.root / "occupied"
        destination.mkdir()
        sentinel = destination / "user-file.txt"
        sentinel.write_text("keep me\n", encoding="utf-8")
        with self.assertRaisesRegex(ResultPackageError, "refusing to overwrite"):
            self.packager.package(
                self.context,
                self.spec,
                self.render_result,
                self.report,
                destination,
            )
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep me\n")
        self.assertFalse((destination / "package_manifest.json").exists())

    def test_existing_empty_destination_is_published_atomically(self) -> None:
        destination = self.root / "empty"
        destination.mkdir()
        package = self.packager.package(
            self.context,
            self.spec,
            self.render_result,
            self.report,
            destination,
        )
        package.validate()
        self.assertTrue((destination / "package_manifest.json").is_file())

    def test_gate_failure_leaves_no_complete_manifest(self) -> None:
        destination = self.root / "failed"
        with self.assertRaises(ResultPackageError):
            self.packager.package(
                self.context,
                self.spec,
                self.render_result,
                failed_report(self.report),
                destination,
            )
        self.assertFalse((destination / "package_manifest.json").exists())
        self.assertFalse(
            list(self.root.glob(f".{destination.name}.staging-*")),
            "temporary staging directories must be cleaned after failure",
        )

    def test_absolute_local_reference_is_rejected(self) -> None:
        context = build_context(ECOMMERCE_REQUIREMENT, absolute_reference=True)
        spec = PageSpecBuilder().build(context)
        result = DeterministicPageRenderer().render(spec, self.root / "absolute-render")
        report = MinimalConsistencyChecker().check(spec, result)
        with self.assertRaisesRegex(ResultPackageError, "absolute local path"):
            self.packager.package(
                context,
                spec,
                result,
                report,
                self.root / "rejected",
            )

    def test_result_package_validation_detects_post_publish_tampering(self) -> None:
        package = self._package()
        (package.package_dir / "page/app.js").write_text(
            "tampered\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ResultPackageError, "hash or size"):
            package.validate()


if __name__ == "__main__":
    unittest.main()
