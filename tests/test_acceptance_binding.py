from __future__ import annotations

import copy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_acceptance import (
    AcceptanceBindingPlan,
    compile_acceptance_binding,
    compile_acceptance_plan,
    project_requirement_view,
)
from req2web_agent import AgentContextBundle, UseCase
from req2web_generation import DeterministicPageRenderer, PageSpecBuilder
from req2web_rag.corpus import ROLE_ORDER
from req2web_rag.validation_signals import build_validation_signals


def _references() -> list[dict[str, str]]:
    return [
        {"kind": "issue", "uri": "https://example.test/issues/1"},
        {"kind": "pull_request", "uri": "https://example.test/pull/2"},
    ]


def _record(role: str, doc_id: str, *, category: str | None = None) -> dict[str, object]:
    references = _references()
    result: dict[str, object] = {
        "doc_id": doc_id,
        "references": references,
        "role": role,
        "summary": f"{role} evidence summary",
        "title": f"{role} evidence title",
    }
    if category is not None:
        result["validation_signals"] = build_validation_signals(
            {
                "doc_id": doc_id,
                "metadata": {"category": category},
                "references": references,
                "role": role,
            }
        )
    return result


def make_bundle(
    *,
    original_requirement: str = "Create a mobile product search page with input recovery.",
    validation_categories: tuple[str, ...] = ("input_error",),
    constraints: list[str] | None = None,
) -> AgentContextBundle:
    results = {role: [_record(role, f"{role}:doc-001")] for role in ROLE_ORDER}
    results["validation"] = [
        _record("validation", f"validation:doc-{index:03d}", category=category)
        for index, category in enumerate(validation_categories, 1)
    ]
    return AgentContextBundle(
        original_requirement=original_requirement,
        requirement_summary="A mobile product search flow with explicit input recovery.",
        target_device="mobile",
        task_type="search_list",
        constraints=constraints or ["Support responsive layout.", "Support invalid input recovery."],
        use_cases=[
            UseCase(
                use_case_id="use-case-search",
                title="Search products",
                actor="shopper",
                goal="find a matching product",
                expected_outcome="a result list is shown",
            ),
            UseCase(
                use_case_id="use-case-retry",
                title="Recover from invalid input",
                actor="shopper",
                goal="retry an invalid search",
                expected_outcome="clear feedback and a retry path are shown",
            ),
        ],
        retrieval_queries={role: f"query for {role}" for role in ROLE_ORDER},
        retrieval_results=results,
    )


def _sync_manifest_hash(output_dir: Path, filename: str) -> None:
    manifest_path = output_dir / "render_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["name"] == filename:
            entry["sha256"] = sha256((output_dir / filename).read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class AcceptanceBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_acceptance_binding" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.bundle = make_bundle()
        self.view = project_requirement_view(self.bundle)
        self.plan = compile_acceptance_plan(self.view)
        self.spec = PageSpecBuilder().build(self.bundle)
        self.renderer = DeterministicPageRenderer()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        if self.root.parent.exists() and not any(self.root.parent.iterdir()):
            self.root.parent.rmdir()

    def render(self, spec=None):
        actual_spec = self.spec if spec is None else spec
        return self.renderer.render(actual_spec, self.root / "page")

    def binding_for(
        self,
        result: AcceptanceBindingPlan,
        source_kind: str,
        source_id: str | None = None,
    ):
        criterion = next(
            item
            for item in self.plan.criteria
            if item.source_kind == source_kind
            and (source_id is None or item.source_id == source_id)
        )
        return next(item for item in result.bindings if item.criterion_id == criterion.criterion_id)

    def test_normal_binding_preserves_one_record_per_criterion_and_static_steps(self) -> None:
        render = self.render()
        result = compile_acceptance_binding(self.view, self.plan, self.spec, render)

        self.assertEqual(
            tuple(item.criterion_id for item in result.bindings),
            tuple(item.criterion_id for item in self.plan.criteria),
        )
        self.assertTrue(any(item.disposition == "bound" for item in result.bindings))
        self.assertEqual(
            {item.terminal_status for item in result.bindings if item.disposition == "terminal"},
            {"not_supported"},
        )
        self.assertTrue(all(item.terminal_status is None for item in result.bindings if item.disposition == "bound"))
        self.assertFalse(any("pass" in item.to_dict() or "unknown" in item.to_dict() for item in result.bindings))
        self.assertTrue(result.steps)
        self.assertEqual(tuple(range(len(result.steps))), tuple(item.ordinal for item in result.steps))
        self.assertTrue(all(item.selector and item.source for item in result.steps))
        self.assertTrue(any(item.action_kind == "load_page" for item in result.steps))
        self.assertTrue(any(item.action_kind == "trigger_interaction" for item in result.steps))
        self.assertTrue(any(item.action_kind == "assert_state" for item in result.steps))
        use_case_criterion = next(
            item for item in self.plan.criteria if item.source_id == "use-case-search"
        )
        expected_outcome = dict(use_case_criterion.expected_payload)["expected_outcome"]
        outcome_steps = [
            item for item in result.steps
            if item.criterion_id == use_case_criterion.criterion_id
            and item.action_kind == "assert_feedback"
        ]
        self.assertEqual(len(outcome_steps), 1)
        self.assertEqual(
            dict(outcome_steps[0].expected_payload),
            {"feedback": expected_outcome},
        )
        self.assertEqual(
            outcome_steps[0].source,
            "acceptance_plan.criteria.expected_payload.expected_outcome",
        )
        result.validate_against(self.view, self.plan, self.spec, render)

    def test_unsupported_requirement_constraint_and_device_are_retained(self) -> None:
        result = compile_acceptance_binding(self.view, self.plan, self.spec, self.render())

        for source_kind in ("requirement", "target_device", "constraint"):
            binding = self.binding_for(result, source_kind)
            self.assertEqual(binding.disposition, "terminal")
            self.assertEqual(binding.terminal_status, "not_supported")
            self.assertEqual(binding.terminal_stage, "capability_boundary")
            self.assertFalse(binding.step_ids)

    def test_supported_use_case_target_missing_is_pagespec_binding_failure(self) -> None:
        spec = copy.deepcopy(self.spec)
        trace = next(item for item in spec.traceability.use_cases if item.use_case_id == "use-case-search")
        trace.interaction_ids = [item for item in trace.interaction_ids if "recovery" in item]
        spec.validate()
        result = compile_acceptance_binding(self.view, self.plan, spec, self.render(spec))

        binding = self.binding_for(result, "use_case", "use-case-search")
        self.assertEqual(binding.disposition, "terminal")
        self.assertEqual(binding.terminal_status, "fail")
        self.assertEqual(binding.terminal_stage, "page_spec_binding")
        self.assertIn("incomplete_use_case_target", binding.reason_code)

    def test_dom_stable_id_missing_is_render_binding_failure(self) -> None:
        render = self.render()
        target = self.spec.traceability.use_cases[0].component_ids[0]
        html = render.index_html.read_text(encoding="utf-8")
        render.index_html.write_text(
            html.replace(f'data-component-id="{target}"', 'data-component-id="forged-component"', 1),
            encoding="utf-8",
        )
        _sync_manifest_hash(render.output_dir, "index.html")

        result = compile_acceptance_binding(self.view, self.plan, self.spec, render)

        binding = self.binding_for(result, "use_case", "use-case-search")
        self.assertEqual(binding.terminal_status, "fail")
        self.assertEqual(binding.terminal_stage, "render_binding")
        self.assertIn("dom_component_count_mismatch", binding.reason_code)

    def test_manifest_hash_and_page_identity_tampering_are_render_failures(self) -> None:
        for name, mutation in (("hash", "hash"), ("identity", "identity")):
            with self.subTest(name=name):
                render = self.renderer.render(self.spec, self.root / name)
                if mutation == "hash":
                    render.index_html.write_text(
                        render.index_html.read_text(encoding="utf-8") + "\n<!-- tampered -->\n",
                        encoding="utf-8",
                    )
                else:
                    manifest = json.loads(render.render_manifest.read_text(encoding="utf-8"))
                    manifest["page_id"] = "forged-page"
                    render.render_manifest.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                result = compile_acceptance_binding(self.view, self.plan, self.spec, render)
                binding = self.binding_for(result, "use_case", "use-case-search")
                self.assertEqual(binding.terminal_status, "fail")
                self.assertEqual(binding.terminal_stage, "render_binding")

    def test_use_case_outcome_mismatch_is_pagespec_binding_failure(self) -> None:
        spec = copy.deepcopy(self.spec)
        interaction = next(
            item for item in spec.interactions
            if item.interaction_id == "interaction-use-case-search-primary"
        )
        interaction.user_feedback = "candidate-defined outcome"
        spec.validate()

        result = compile_acceptance_binding(self.view, self.plan, spec, self.render(spec))

        binding = self.binding_for(result, "use_case", "use-case-search")
        self.assertEqual(binding.terminal_status, "fail")
        self.assertEqual(binding.terminal_stage, "page_spec_binding")
        self.assertIn("use_case_expected_outcome_mismatch", binding.reason_code)

    def test_recovery_criteria_bind_distinct_stable_scenario_fixtures(self) -> None:
        bundle = make_bundle(
            original_requirement=(
                "Create a mobile page with invalid input recovery and camera permission "
                "denied recovery."
            ),
            validation_categories=("input_error", "auth_access"),
            constraints=[
                "Support invalid input recovery.",
                "Support camera permission denied recovery.",
            ],
        )
        view = project_requirement_view(bundle)
        plan = compile_acceptance_plan(view)
        spec = PageSpecBuilder().build(bundle)
        render = self.renderer.render(spec, self.root / "dual-recovery")

        result = compile_acceptance_binding(view, plan, spec, render)
        bindings_by_criterion = {item.criterion_id: item for item in result.bindings}
        signal_criteria = [
            item for item in plan.criteria if item.source_kind == "validation_signal"
        ]
        self.assertEqual(len(signal_criteria), 2)
        by_source_value = {
            dict(item.expected_payload)["source_value"]: bindings_by_criterion[item.criterion_id]
            for item in signal_criteria
        }
        input_refs = dict(by_source_value["input_error"].target_refs)
        permission_refs = dict(by_source_value["auth_access"].target_refs)
        self.assertEqual(by_source_value["input_error"].disposition, "bound")
        self.assertEqual(by_source_value["auth_access"].disposition, "bound")
        self.assertEqual(input_refs["recovery_scenario_token"], "input")
        self.assertEqual(permission_refs["recovery_scenario_token"], "permission")
        self.assertTrue(input_refs["error_entry_interaction_id"].endswith("-error-input"))
        self.assertTrue(input_refs["recovery_interaction_id"].endswith("-recovery-input"))
        self.assertTrue(permission_refs["error_entry_interaction_id"].endswith("-error-permission"))
        self.assertTrue(permission_refs["recovery_interaction_id"].endswith("-recovery-permission"))
        self.assertNotEqual(
            input_refs["error_entry_interaction_id"],
            permission_refs["error_entry_interaction_id"],
        )
        self.assertNotEqual(
            input_refs["recovery_interaction_id"],
            permission_refs["recovery_interaction_id"],
        )

    def test_supported_recovery_without_matching_stable_fixture_fails(self) -> None:
        bundle = make_bundle(
            original_requirement=(
                "Create a mobile page with invalid input recovery and camera permission "
                "denied recovery."
            ),
            validation_categories=("input_error", "auth_access"),
            constraints=["Support invalid input recovery."],
        )
        view = project_requirement_view(bundle)
        plan = compile_acceptance_plan(view)
        spec = PageSpecBuilder().build(bundle)
        render = self.renderer.render(spec, self.root / "missing-permission")

        result = compile_acceptance_binding(view, plan, spec, render)
        permission_criterion = next(
            item
            for item in plan.criteria
            if item.source_kind == "validation_signal"
            and dict(item.expected_payload)["source_value"] == "auth_access"
        )
        binding = next(
            item for item in result.bindings
            if item.criterion_id == permission_criterion.criterion_id
        )
        self.assertEqual(binding.terminal_status, "fail")
        self.assertEqual(binding.terminal_stage, "page_spec_binding")
        self.assertIn("missing_recovery_fixture_error_entry:permission", binding.reason_code)

    def test_path_traversal_manifest_never_reads_external_target(self) -> None:
        render = self.render()
        sentinel = self.root / "outside-sentinel.txt"
        sentinel.write_text("do not read", encoding="utf-8")
        manifest = json.loads(render.render_manifest.read_text(encoding="utf-8"))
        manifest["files"][0]["name"] = "../outside-sentinel.txt"
        render.render_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        external_reads: list[Path] = []
        original_read_bytes = Path.read_bytes

        def guarded_read_bytes(path: Path) -> bytes:
            if path.resolve() == sentinel.resolve():
                external_reads.append(path)
                raise AssertionError("manifest traversal attempted an external read")
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", new=guarded_read_bytes):
            result = compile_acceptance_binding(self.view, self.plan, self.spec, render)

        self.assertFalse(external_reads)
        binding = self.binding_for(result, "use_case", "use-case-search")
        self.assertEqual(binding.terminal_status, "fail")
        self.assertEqual(binding.terminal_stage, "render_binding")
        self.assertIn("render_manifest_unsafe_file_name", binding.reason_code)

    def test_plan_view_mismatch_is_rejected_before_binding(self) -> None:
        other_view = project_requirement_view(
            make_bundle(original_requirement="Create a different mobile checkout page with input recovery.")
        )

        with self.assertRaises(ValueError):
            compile_acceptance_binding(other_view, self.plan, self.spec, self.render())

    def test_repeat_compilation_is_byte_stable_and_does_not_mutate_inputs(self) -> None:
        original_bundle = copy.deepcopy(self.bundle)
        original_view = copy.deepcopy(self.view)
        original_plan = copy.deepcopy(self.plan)
        original_spec = copy.deepcopy(self.spec)
        first_render = self.renderer.render(self.spec, self.root / "first")
        second_render = self.renderer.render(self.spec, self.root / "second")

        first = compile_acceptance_binding(self.view, self.plan, self.spec, first_render)
        second = compile_acceptance_binding(self.view, self.plan, self.spec, second_render)

        self.assertEqual(self.bundle, original_bundle)
        self.assertEqual(self.view, original_view)
        self.assertEqual(self.plan, original_plan)
        self.assertEqual(self.spec, original_spec)
        self.assertEqual(first.canonical_json_bytes(), second.canonical_json_bytes())
        self.assertEqual(first.sha256(), second.sha256())

    def test_validation_rejects_tampered_source_target_order_and_missing_records(self) -> None:
        render = self.render()
        result = compile_acceptance_binding(self.view, self.plan, self.spec, render)
        first = result.bindings[0]
        forged_target = replace(first, target_refs=(("forged_target", "forged"),))
        forged = replace(result, bindings=(forged_target, *result.bindings[1:]))
        with self.assertRaises(ValueError):
            forged.validate()
        with self.assertRaises(ValueError):
            replace(result, source_page_spec_sha256="0" * 64).validate()
        with self.assertRaises(ValueError):
            replace(result, bindings=tuple(reversed(result.bindings))).validate_against(
                self.view, self.plan, self.spec, render
            )
        with self.assertRaises(ValueError):
            replace(result, bindings=result.bindings[:-1]).validate_against(
                self.view, self.plan, self.spec, render
            )


if __name__ == "__main__":
    unittest.main()
