from __future__ import annotations

import hashlib
import json
import shutil
import sys
import unittest
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain  # noqa: E402
from req2web_generation import (  # noqa: E402
    RENDER_MANIFEST_SCHEMA_VERSION,
    SUPPORTED_COMPONENT_TYPES,
    DeterministicPageRenderer,
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


class PageRendererTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_page_renderer" / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.renderer = DeterministicPageRenderer()
        self.ecommerce_spec = build_spec(ECOMMERCE_REQUIREMENT)
        self.pet_spec = build_spec(PET_REQUIREMENT)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)
        self.root.parent.rmdir()

    def test_ecommerce_and_pet_specs_render_static_files(self) -> None:
        for name, spec in (
            ("ecommerce", self.ecommerce_spec),
            ("pet", self.pet_spec),
        ):
            result = self.renderer.render(spec, self.root / name)
            self.assertEqual(result.page_id, spec.page_id)
            for path in (
                result.index_html,
                result.styles_css,
                result.app_js,
                result.render_manifest,
            ):
                self.assertTrue(path.is_file(), path)

    def test_layout_order_and_component_ids_are_traceable_in_html(self) -> None:
        result = self.renderer.render(self.ecommerce_spec, self.root / "page")
        markup = result.index_html.read_text(encoding="utf-8")
        positions = [
            markup.index(f'data-section-id="{section_id}"')
            for section_id in self.ecommerce_spec.layout.section_order
        ]
        self.assertEqual(positions, sorted(positions))
        for section in self.ecommerce_spec.sections:
            component_positions = [
                markup.index(f'data-component-id="{component_id}"')
                for component_id in section.component_ids
            ]
            self.assertEqual(component_positions, sorted(component_positions))
        for component in self.ecommerce_spec.components:
            self.assertIn(f'id="{component.component_id}"', markup)

    def test_all_supported_component_types_have_deterministic_mappings(self) -> None:
        for component_type in SUPPORTED_COMPONENT_TYPES:
            self.ecommerce_spec.components[0].component_type = component_type
            result = self.renderer.render(
                self.ecommerce_spec,
                self.root / "mappings" / component_type,
            )
            markup = result.index_html.read_text(encoding="utf-8")
            self.assertIn(f'data-renderer-kind="{component_type}"', markup)

    def test_interactions_states_and_feedback_are_in_runtime_logic(self) -> None:
        result = self.renderer.render(self.pet_spec, self.root / "runtime")
        script = result.app_js.read_text(encoding="utf-8")
        for interaction in self.pet_spec.interactions:
            self.assertIn(interaction.interaction_id, script)
            self.assertIn(interaction.trigger_component_id, script)
            self.assertIn(interaction.user_feedback, script)
        for state in self.pet_spec.states:
            self.assertIn(state.state_id, script)
            self.assertIn(state.name, script)
        self.assertIn("feedback.textContent", script)
        self.assertIn("applyState(interaction.target_state_id", script)

    def test_unknown_component_type_uses_visible_fallback(self) -> None:
        component = self.ecommerce_spec.components[0]
        component.component_type = "custom_chart"
        result = self.renderer.render(self.ecommerce_spec, self.root / "fallback")
        markup = result.index_html.read_text(encoding="utf-8")
        self.assertIn('data-component-type="custom_chart"', markup)
        self.assertIn('data-renderer-kind="fallback"', markup)
        self.assertIn("通用控件：custom_chart", markup)

    def test_invalid_page_spec_is_rejected_before_writing(self) -> None:
        destination = self.root / "invalid"
        self.ecommerce_spec.sections[0].component_ids.pop()
        with self.assertRaisesRegex(ValueError, "not listed in its owning section"):
            self.renderer.render(self.ecommerce_spec, destination)
        self.assertFalse(destination.exists())

    def test_html_and_javascript_injection_is_escaped(self) -> None:
        payload = '\"><script>window.pwned = "yes";</script><b>unsafe</b>'
        self.ecommerce_spec.title = payload
        self.ecommerce_spec.summary = payload
        self.ecommerce_spec.components[0].label = payload
        self.ecommerce_spec.components[0].purpose = payload
        self.ecommerce_spec.interactions[0].user_feedback = payload

        result = self.renderer.render(self.ecommerce_spec, self.root / "escaped")
        markup = result.index_html.read_text(encoding="utf-8")
        script = result.app_js.read_text(encoding="utf-8")
        self.assertNotIn("<script>window.pwned", markup)
        self.assertNotIn("<b>unsafe</b>", markup)
        self.assertIn("&lt;script&gt;window.pwned", markup)
        self.assertNotIn("<script>window.pwned", script)
        self.assertIn("\\u003cscript\\u003e", script)
        self.assertNotIn("innerHTML", script)

    def test_output_has_no_network_dependency(self) -> None:
        result = self.renderer.render(self.ecommerce_spec, self.root / "offline")
        contents = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (result.index_html, result.styles_css, result.app_js)
        ).casefold()
        for forbidden in (
            "http://",
            "https://",
            "cdn",
            "@import",
            "fetch(",
            "xmlhttprequest",
        ):
            self.assertNotIn(forbidden, contents)
        self.assertIn('href="styles.css"', contents)
        self.assertIn('src="app.js"', contents)

    def test_same_page_spec_is_byte_for_byte_deterministic(self) -> None:
        first = self.renderer.render(self.ecommerce_spec, self.root / "first")
        second = self.renderer.render(self.ecommerce_spec, self.root / "second")
        for filename in (
            "index.html",
            "styles.css",
            "app.js",
            "render_manifest.json",
        ):
            self.assertEqual(
                (first.output_dir / filename).read_bytes(),
                (second.output_dir / filename).read_bytes(),
                filename,
            )

    def test_manifest_references_real_files_and_correct_hashes(self) -> None:
        result = self.renderer.render(self.pet_spec, self.root / "manifest")
        manifest = json.loads(result.render_manifest.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["schema_version"], RENDER_MANIFEST_SCHEMA_VERSION
        )
        self.assertEqual(manifest["page_id"], self.pet_spec.page_id)
        for entry in manifest["files"]:
            path = result.output_dir / entry["name"]
            self.assertTrue(path.is_file())
            self.assertEqual(
                entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
            )


if __name__ == "__main__":
    unittest.main()
