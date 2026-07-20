from __future__ import annotations

import ast
import copy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_acceptance import (  # noqa: E402
    ACCEPTANCE_PLAN_SCHEMA_VERSION,
    AcceptancePlan,
    RequirementView,
    compile_acceptance_plan,
    project_requirement_view,
)
from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_rag.validation_signals import build_validation_signals  # noqa: E402


def _references() -> list[dict[str, str]]:
    return [
        {"kind": "issue", "uri": "https://example.test/issues/1"},
        {"kind": "pull_request", "uri": "https://example.test/pull/2"},
    ]


def _retrieval_record(
    role: str,
    doc_id: str,
    *,
    validation_category: str | None = None,
) -> dict[str, object]:
    references = _references()
    record: dict[str, object] = {
        "doc_id": doc_id,
        "references": references,
        "role": role,
        "summary": f"{role} evidence summary",
        "title": f"{role} evidence title",
    }
    if validation_category is not None:
        record["validation_signals"] = build_validation_signals(
            {
                "doc_id": doc_id,
                "metadata": {"category": validation_category},
                "references": references,
                "role": role,
            }
        )
    return record


def make_bundle(
    *,
    validation_categories: tuple[str, ...] = ("input_error",),
    validation_doc_ids: tuple[str, ...] | None = None,
    original_requirement: str = "Create a mobile product search page with input recovery.",
    constraints: list[str] | None = None,
) -> AgentContextBundle:
    if validation_doc_ids is None:
        validation_doc_ids = tuple(
            f"validation:doc-{index:03d}" for index in range(1, len(validation_categories) + 1)
        )
    if len(validation_doc_ids) != len(validation_categories):
        raise ValueError("validation_doc_ids must align with validation_categories")
    retrieval_results = {
        role: [_retrieval_record(role, f"{role}:doc-001")]
        for role in ROLE_ORDER
    }
    retrieval_results["validation"] = [
        _retrieval_record("validation", doc_id, validation_category=category)
        for doc_id, category in zip(validation_doc_ids, validation_categories, strict=True)
    ]
    return AgentContextBundle(
        original_requirement=original_requirement,
        requirement_summary="A mobile product search flow with explicit input recovery.",
        target_device="mobile",
        task_type="search_list",
        constraints=constraints or ["Support responsive layout.", "Show input-error recovery."],
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
        retrieval_results=retrieval_results,
    )


def criteria_of_kind(plan: AcceptancePlan, source_kind: str) -> list[object]:
    return [criterion for criterion in plan.criteria if criterion.source_kind == source_kind]


class AcceptancePlanTest(unittest.TestCase):
    def test_compiles_requirement_and_target_device_obligations(self) -> None:
        view = project_requirement_view(make_bundle())

        plan = compile_acceptance_plan(view)

        requirement_criteria = criteria_of_kind(plan, "requirement")
        target_device_criteria = criteria_of_kind(plan, "target_device")
        self.assertEqual(len(requirement_criteria), 1)
        self.assertEqual(len(target_device_criteria), 1)
        requirement_payload = requirement_criteria[0].to_dict()["expected_payload"]
        target_device_payload = target_device_criteria[0].to_dict()["expected_payload"]
        self.assertEqual(requirement_payload["original_requirement"], view.requirement.original_requirement)
        self.assertEqual(requirement_payload["requirement_summary"], view.requirement.requirement_summary)
        self.assertEqual(target_device_payload["target_device"], view.requirement.target_device)
        self.assertTrue(all(criterion.must_have for criterion in plan.criteria))
        self.assertTrue(all(criterion.criticality == "must_have" for criterion in plan.criteria))
        plan.validate_against(view)

    def test_compiles_each_use_case_constraint_and_contextual_signal_once(self) -> None:
        view = project_requirement_view(make_bundle())

        plan = compile_acceptance_plan(view)

        self.assertEqual(plan.schema_version, ACCEPTANCE_PLAN_SCHEMA_VERSION)
        self.assertEqual(plan.source_requirement_view_schema_version, view.schema_version)
        self.assertEqual(plan.source_requirement_view_sha256, view.sha256())
        self.assertEqual(
            {criterion.source_id for criterion in criteria_of_kind(plan, "use_case")},
            {use_case.use_case_id for use_case in view.use_cases},
        )
        self.assertEqual(
            {criterion.source_id for criterion in criteria_of_kind(plan, "constraint")},
            {constraint.constraint_id for constraint in view.constraints},
        )
        validation_criteria = criteria_of_kind(plan, "validation_signal")
        self.assertEqual(len(validation_criteria), 1)
        payload = validation_criteria[0].to_dict()["expected_payload"]
        self.assertEqual(validation_criteria[0].semantic_kind, "retry_recovery")
        self.assertEqual(payload["context_gate_rule"], "acceptance_context_gate:v1:retry_recovery")
        self.assertEqual(
            payload["context_gate_source"],
            "requirement_view.requirement.original_requirement+requirement_view.constraints",
        )
        self.assertIn("recovery_or_retry", payload["context_gate_matches_json"])
        self.assertIn("error_failure_or_input", payload["context_gate_matches_json"])
        plan.validate_against(view)

    def test_permission_recovery_signal_requires_and_uses_permission_context(self) -> None:
        view = project_requirement_view(
            make_bundle(
                validation_categories=("auth_access",),
                original_requirement="Allow a user to retry after permission is denied.",
                constraints=["Provide permission-denial recovery."],
            )
        )

        plan = compile_acceptance_plan(view)

        validation_criteria = criteria_of_kind(plan, "validation_signal")
        self.assertEqual(len(validation_criteria), 1)
        self.assertEqual(validation_criteria[0].semantic_kind, "permission_recovery")
        self.assertEqual(
            validation_criteria[0].to_dict()["expected_payload"]["context_gate_rule"],
            "acceptance_context_gate:v1:permission_recovery",
        )
        plan.validate_against(view)

    def test_unicode_escaped_chinese_context_admits_retry_and_permission_signals(self) -> None:
        retry_view = project_requirement_view(
            make_bundle(
                original_requirement="\u8f93\u5165\u9519\u8bef\u540e\u5e94\u6062\u590d\u5e76\u91cd\u8bd5\u641c\u7d22\u3002",
                constraints=["\u652f\u6301\u9519\u8bef\u6062\u590d\u3002"],
            )
        )
        permission_view = project_requirement_view(
            make_bundle(
                validation_categories=("auth_access",),
                original_requirement="\u6743\u9650\u88ab\u62d2\u7edd\u540e\u5141\u8bb8\u7528\u6237\u6062\u590d\u5e76\u91cd\u8bd5\u3002",
                constraints=["\u63d0\u4f9b\u6743\u9650\u62d2\u7edd\u540e\u7684\u6062\u590d\u8def\u5f84\u3002"],
            )
        )

        retry_plan = compile_acceptance_plan(retry_view)
        permission_plan = compile_acceptance_plan(permission_view)

        self.assertEqual(criteria_of_kind(retry_plan, "validation_signal")[0].semantic_kind, "retry_recovery")
        self.assertEqual(
            criteria_of_kind(permission_plan, "validation_signal")[0].semantic_kind,
            "permission_recovery",
        )
        retry_plan.validate_against(retry_view)
        permission_plan.validate_against(permission_view)

    def test_candidate_signal_without_explicit_allowed_context_is_not_an_obligation(self) -> None:
        view = project_requirement_view(
            make_bundle(
                original_requirement="Create a mobile product search page.",
                constraints=["Support responsive layout.", "Show a result list."],
            )
        )

        plan = compile_acceptance_plan(view)

        self.assertEqual(criteria_of_kind(plan, "validation_signal"), [])
        self.assertEqual(len(plan.criteria), 2 + len(view.use_cases) + len(view.constraints))
        plan.validate_against(view)

    def test_audit_only_signal_never_becomes_an_obligation(self) -> None:
        view = project_requirement_view(make_bundle(validation_categories=("legacy_unknown",)))

        plan = compile_acceptance_plan(view)

        self.assertEqual(criteria_of_kind(plan, "validation_signal"), [])
        self.assertEqual(len(plan.criteria), 2 + len(view.use_cases) + len(view.constraints))
        plan.validate_against(view)

    def test_same_validation_value_multiple_sources_compiles_once_and_is_byte_stable(self) -> None:
        first_view = project_requirement_view(
            make_bundle(
                validation_categories=("input_error", "input_error"),
                validation_doc_ids=("validation:doc-b", "validation:doc-a"),
            )
        )
        second_view = project_requirement_view(
            make_bundle(
                validation_categories=("input_error", "input_error"),
                validation_doc_ids=("validation:doc-a", "validation:doc-b"),
            )
        )

        first_plan = compile_acceptance_plan(first_view)
        second_plan = compile_acceptance_plan(second_view)

        validation_criteria = criteria_of_kind(first_plan, "validation_signal")
        self.assertEqual(len(validation_criteria), 1)
        payload = validation_criteria[0].to_dict()["expected_payload"]
        self.assertEqual(payload["source_doc_id"], "validation:doc-a")
        self.assertEqual(first_view.canonical_json_bytes(), second_view.canonical_json_bytes())
        self.assertEqual(first_plan.canonical_json_bytes(), second_plan.canonical_json_bytes())
        self.assertEqual(first_plan.sha256(), second_plan.sha256())
        first_plan.validate_against(first_view)
        second_plan.validate_against(second_view)

    def test_task_type_is_explicitly_metadata_only_classification(self) -> None:
        view = project_requirement_view(make_bundle())

        plan = compile_acceptance_plan(view)

        self.assertEqual(plan.requirement_metadata.task_type, view.requirement.task_type)
        self.assertEqual(criteria_of_kind(plan, "task_type"), [])
        self.assertFalse(any(criterion.semantic_kind == "task_type" for criterion in plan.criteria))

    def test_repeat_compilation_is_byte_stable_and_does_not_mutate_source(self) -> None:
        bundle = make_bundle()
        original_bundle = copy.deepcopy(bundle)
        first_view = project_requirement_view(bundle)
        second_view = project_requirement_view(bundle)

        first_plan = compile_acceptance_plan(first_view)
        second_plan = compile_acceptance_plan(second_view)

        self.assertEqual(bundle, original_bundle)
        self.assertEqual(first_view.canonical_json_bytes(), second_view.canonical_json_bytes())
        self.assertEqual(first_plan.canonical_json_bytes(), second_plan.canonical_json_bytes())
        self.assertEqual(first_plan.sha256(), second_plan.sha256())
        self.assertEqual(
            first_plan.criteria,
            tuple(sorted(first_plan.criteria, key=lambda item: (
                {"requirement": 0, "target_device": 1, "use_case": 2, "constraint": 3, "validation_signal": 4}[item.source_kind],
                item.source_id,
                item.criterion_id,
            ))),
        )

    def test_plan_is_frozen_and_serializes_expected_payloads(self) -> None:
        plan = compile_acceptance_plan(project_requirement_view(make_bundle()))

        with self.assertRaises(FrozenInstanceError):
            plan.schema_version = "forged"  # type: ignore[misc]
        payload = plan.to_dict()
        self.assertIsInstance(payload["criteria"][0]["expected_payload"], dict)
        self.assertEqual(plan.canonical_json_bytes(), plan.canonical_json_bytes())

    def test_plan_validation_rejects_schema_hash_gate_payload_order_and_source_tampering(self) -> None:
        view = project_requirement_view(make_bundle())
        plan = compile_acceptance_plan(view)
        first = plan.criteria[0]
        signal_criterion = criteria_of_kind(plan, "validation_signal")[0]
        signal_payload = list(signal_criterion.expected_payload)
        gate_rule_index = next(index for index, item in enumerate(signal_payload) if item[0] == "context_gate_rule")
        signal_payload[gate_rule_index] = ("context_gate_rule", "forged")

        with self.assertRaises(ValueError):
            replace(plan, schema_version="req2web.acceptance.plan.v0").validate()
        with self.assertRaises(ValueError):
            replace(plan, source_requirement_view_sha256="0" * 64).validate_against(view)
        with self.assertRaises(ValueError):
            replace(
                plan,
                criteria=(replace(first, obligation_kind="forged"), *plan.criteria[1:]),
            ).validate()
        with self.assertRaises(ValueError):
            replace(
                plan,
                criteria=(replace(signal_criterion, expected_payload=tuple(signal_payload)), *[
                    criterion for criterion in plan.criteria if criterion != signal_criterion
                ]),
            ).validate()
        with self.assertRaises(ValueError):
            replace(plan, criteria=tuple(reversed(plan.criteria))).validate()
        with self.assertRaises(ValueError):
            replace(plan, criteria=(first, first, *plan.criteria[2:])).validate()
        with self.assertRaises(ValueError):
            replace(
                plan,
                requirement_metadata=replace(plan.requirement_metadata, task_type="forged"),
            ).validate_against(view)

    def test_compiler_rejects_invalid_or_forged_requirement_view_inputs(self) -> None:
        view = project_requirement_view(make_bundle())
        forged_requirement = replace(view.requirement, requirement_id="requirement-" + "0" * 64)
        forged_view = replace(view, requirement=forged_requirement)

        with self.assertRaises(TypeError):
            compile_acceptance_plan(object())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            compile_acceptance_plan(forged_view)
        with self.assertRaises(TypeError):
            AcceptancePlan.validate_against(compile_acceptance_plan(view), object())  # type: ignore[arg-type]

    def test_module_has_no_pagespec_or_generation_import(self) -> None:
        source_path = ROOT / "src" / "req2web_acceptance" / "acceptance_plan.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_modules.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )

        self.assertFalse(
            any(module == "req2web_generation" or module.startswith("req2web_generation.") for module in imported_modules)
        )
        self.assertNotIn("?", source_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
