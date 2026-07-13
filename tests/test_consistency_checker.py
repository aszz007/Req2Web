from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain  # noqa: E402
from req2web_generation import (  # noqa: E402
    CONSISTENCY_REPORT_SCHEMA_VERSION,
    DeterministicPageRenderer,
    MinimalConsistencyChecker,
    PageSpecBuilder,
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
_PAGE_DATA_PREFIX = "const PAGE_DATA = Object.freeze("


class FixtureRetriever:
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
                        "uri": f"fixtures/{role}/sample-{index}.json",
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


def build_spec(requirement: str):
    context = MinimalAgentChain(
        DeterministicRequirementProvider(),
        FixtureRetriever(),
        top_k_per_role=2,
    ).run(requirement)
    return PageSpecBuilder().build(context)


def _javascript_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        encoded.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


class ConsistencyCheckerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_consistency_checker" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.renderer = DeterministicPageRenderer()
        self.checker = MinimalConsistencyChecker()
        self.ecommerce_spec = build_spec(ECOMMERCE_REQUIREMENT)
        self.pet_spec = build_spec(PET_REQUIREMENT)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        try:
            self.root.parent.rmdir()
        except OSError:
            pass

    def _render(self, spec=None, name: str = "page"):
        return self.renderer.render(spec or self.ecommerce_spec, self.root / name)

    def _check(self, spec=None, result=None):
        active_spec = spec or self.ecommerce_spec
        active_result = result or self._render(active_spec)
        return self.checker.check(active_spec, active_result)

    def _status(self, report, check_id: str) -> str:
        matches = [item.status for item in report.checks if item.check_id == check_id]
        self.assertEqual(len(matches), 1, check_id)
        return matches[0]

    def _update_manifest_hash(self, result, filename: str) -> None:
        manifest = json.loads(result.render_manifest.read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            if entry["name"] == filename:
                entry["sha256"] = hashlib.sha256(
                    (result.output_dir / filename).read_bytes()
                ).hexdigest()
                break
        result.render_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _mutate_page_data(
        self,
        result,
        mutate: Callable[[dict[str, Any]], None],
    ) -> None:
        script = result.app_js.read_text(encoding="utf-8")
        start = script.index(_PAGE_DATA_PREFIX) + len(_PAGE_DATA_PREFIX)
        payload, consumed = json.JSONDecoder().raw_decode(script[start:])
        mutate(payload)
        result.app_js.write_text(
            script[:start] + _javascript_json(payload) + script[start + consumed :],
            encoding="utf-8",
        )
        self._update_manifest_hash(result, "app.js")

    def _remove_html_block(self, result, pattern: str) -> str:
        markup = result.index_html.read_text(encoding="utf-8")
        updated, count = re.subn(pattern, "", markup, count=1, flags=re.DOTALL)
        self.assertEqual(count, 1)
        result.index_html.write_text(updated, encoding="utf-8")
        self._update_manifest_hash(result, "index.html")
        return updated

    def test_normal_ecommerce_report_passes(self) -> None:
        report = self._check()
        self.assertTrue(report.passed)
        self.assertEqual(report.schema_version, CONSISTENCY_REPORT_SCHEMA_VERSION)
        self.assertEqual(report.summary["fail"], 0)
        self.assertEqual(
            self._status(report, "file.sha256:index.html"),
            "pass",
        )

    def test_normal_pet_recognition_report_passes(self) -> None:
        result = self._render(self.pet_spec, "pet")
        report = self._check(self.pet_spec, result)
        self.assertTrue(report.passed)
        self.assertEqual(report.summary["fail"], 0)

    def test_report_is_json_serializable_and_deterministic(self) -> None:
        first = self._check(result=self._render(name="first"))
        second = self._check(result=self._render(name="second"))
        first_payload = first.to_dict()
        self.assertEqual(first_payload, second.to_dict())
        encoded = json.dumps(first_payload, ensure_ascii=False, sort_keys=True)
        self.assertIn(CONSISTENCY_REPORT_SCHEMA_VERSION, encoded)
        self.assertNotIn(str(self.root), encoded)

    def test_missing_stylesheet_returns_fail_report(self) -> None:
        result = self._render()
        result.styles_css.unlink()
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(self._status(report, "file.required:styles.css"), "fail")
        self.assertEqual(self._status(report, "file.sha256:styles.css"), "fail")

    def test_modified_index_hash_returns_fail_report(self) -> None:
        result = self._render()
        result.index_html.write_text(
            result.index_html.read_text(encoding="utf-8") + "<!-- tampered -->\n",
            encoding="utf-8",
        )
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(self._status(report, "file.sha256:index.html"), "fail")

    def test_manifest_page_id_mismatch_returns_fail_report(self) -> None:
        result = self._render()
        manifest = json.loads(result.render_manifest.read_text(encoding="utf-8"))
        manifest["page_id"] = "page-tampered"
        result.render_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(self._status(report, "manifest.page-id"), "fail")

    def test_render_result_page_id_mismatch_returns_fail_report(self) -> None:
        result = replace(self._render(), page_id="page-tampered")
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(
            self._status(report, "input.render-result-page-id"),
            "fail",
        )

    def test_missing_section_returns_structured_fail(self) -> None:
        result = self._render()
        section_id = self.ecommerce_spec.sections[0].section_id
        self._remove_html_block(
            result,
            rf'      <section\b[^>]*data-section-id="{re.escape(section_id)}".*?      </section>\n',
        )
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(
            self._status(report, f"html.section-occurrence:{section_id}"),
            "fail",
        )

    def test_missing_component_returns_structured_fail(self) -> None:
        result = self._render()
        component_id = self.ecommerce_spec.components[0].component_id
        self._remove_html_block(
            result,
            rf'          <article\b[^>]*data-component-id="{re.escape(component_id)}".*?          </article>\n',
        )
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(
            self._status(report, f"html.component-occurrence:{component_id}"),
            "fail",
        )

    def test_component_moved_to_wrong_section_returns_fail(self) -> None:
        result = self._render()
        component = self.ecommerce_spec.components[0]
        target_section = self.ecommerce_spec.sections[1]
        markup = result.index_html.read_text(encoding="utf-8")
        article_pattern = re.compile(
            rf'          <article\b[^>]*data-component-id="{re.escape(component.component_id)}".*?          </article>\n',
            re.DOTALL,
        )
        article_match = article_pattern.search(markup)
        self.assertIsNotNone(article_match)
        article = article_match.group(0)
        markup = article_pattern.sub("", markup, count=1)
        section_pattern = re.compile(
            rf'(      <section\b[^>]*data-section-id="{re.escape(target_section.section_id)}".*?)(      </section>)',
            re.DOTALL,
        )
        markup, count = section_pattern.subn(
            lambda match: match.group(1) + article + match.group(2),
            markup,
            count=1,
        )
        self.assertEqual(count, 1)
        result.index_html.write_text(markup, encoding="utf-8")
        self._update_manifest_hash(result, "index.html")
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(
            self._status(
                report,
                f"html.component-section:{component.component_id}",
            ),
            "fail",
        )

    def test_removed_runtime_interaction_returns_fail(self) -> None:
        result = self._render()
        interaction_id = self.ecommerce_spec.interactions[0].interaction_id
        self._mutate_page_data(
            result,
            lambda payload: payload["interactions"].pop(0),
        )
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(
            self._status(report, f"runtime.interaction:{interaction_id}"),
            "fail",
        )
        acceptance_id = self.ecommerce_spec.acceptance_checks[0].check_id
        self.assertEqual(
            self._status(report, f"acceptance.reachable:{acceptance_id}"),
            "fail",
        )

    def test_modified_runtime_target_state_returns_fail(self) -> None:
        result = self._render()
        interaction_id = self.ecommerce_spec.interactions[0].interaction_id

        def mutate(payload: dict[str, Any]) -> None:
            payload["interactions"][0]["target_state_id"] = "state-error"

        self._mutate_page_data(result, mutate)
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(
            self._status(report, f"runtime.interaction:{interaction_id}"),
            "fail",
        )

        acceptance_id = self.ecommerce_spec.acceptance_checks[0].check_id
        self.assertEqual(
            self._status(report, f"acceptance.reachable:{acceptance_id}"),
            "fail",
        )

    def test_removed_runtime_state_returns_fail(self) -> None:
        result = self._render()
        state_id = self.ecommerce_spec.states[1].state_id

        def mutate(payload: dict[str, Any]) -> None:
            payload["states"] = [
                state for state in payload["states"] if state["state_id"] != state_id
            ]

        self._mutate_page_data(result, mutate)
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(self._status(report, f"runtime.state:{state_id}"), "fail")

    def test_external_network_url_returns_fail(self) -> None:
        result = self._render()
        markup = result.index_html.read_text(encoding="utf-8")
        result.index_html.write_text(
            markup.replace(
                "</body>",
                '  <script src="https://example.invalid/tampered.js"></script>\n</body>',
            ),
            encoding="utf-8",
        )
        self._update_manifest_hash(result, "index.html")
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(self._status(report, "safety.offline-only"), "fail")

    def test_inner_html_injection_returns_fail(self) -> None:
        result = self._render()
        result.app_js.write_text(
            result.app_js.read_text(encoding="utf-8")
            + '\ndocument.body.innerHTML = "tampered";\n',
            encoding="utf-8",
        )
        self._update_manifest_hash(result, "app.js")
        report = self._check(result=result)
        self.assertFalse(report.passed)
        self.assertEqual(self._status(report, "safety.no-inner-html"), "fail")

    def test_same_state_interaction_is_legal(self) -> None:
        interaction = self.ecommerce_spec.interactions[0]
        interaction.target_state_id = interaction.source_state_id
        self.ecommerce_spec.acceptance_checks[0].state_id = interaction.source_state_id
        result = self._render()
        report = self._check(result=result)
        self.assertTrue(report.passed)
        self.assertEqual(
            self._status(report, f"runtime.interaction:{interaction.interaction_id}"),
            "pass",
        )

    def test_unreachable_non_acceptance_states_are_warnings(self) -> None:
        report = self._check()
        self.assertTrue(report.passed)
        self.assertGreater(report.summary["warning"], 0)
        self.assertEqual(
            self._status(report, "warning.unreachable-state:state-loading"),
            "warning",
        )

    def test_unknown_component_fallback_is_visible_warning(self) -> None:
        component = self.ecommerce_spec.components[0]
        component.component_type = "custom_chart"
        report = self._check(result=self._render())
        self.assertTrue(report.passed)
        self.assertEqual(
            self._status(report, f"warning.fallback-component:{component.component_id}"),
            "warning",
        )
        self.assertEqual(
            self._status(report, f"html.renderer-kind:{component.component_id}"),
            "pass",
        )

    def test_extra_local_file_is_warning_only(self) -> None:
        result = self._render()
        (result.output_dir / "notes.txt").write_text("local note\n", encoding="utf-8")
        report = self._check(result=result)
        self.assertTrue(report.passed)
        self.assertEqual(
            self._status(report, "warning.extra-local-files"),
            "warning",
        )

    def test_invalid_page_spec_is_rejected_before_checking_files(self) -> None:
        result = self._render()
        self.ecommerce_spec.sections[0].component_ids.pop()
        with self.assertRaisesRegex(ValueError, "not listed in its owning section"):
            self.checker.check(self.ecommerce_spec, result)


if __name__ == "__main__":
    unittest.main()
