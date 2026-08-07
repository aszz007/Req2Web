from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
for path in (SRC, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


from req2web_acceptance import (  # noqa: E402
    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
    SemanticAlignmentResult,
    SemanticAlignmentVerdict,
    build_semantic_alignment_request,
    compile_acceptance_binding,
    compile_acceptance_plan,
    execute_acceptance_binding_plan,
    project_requirement_view,
)
from req2web_generation import DeterministicPageRenderer, PageSpecBuilder  # noqa: E402
from test_acceptance_binding import make_bundle  # noqa: E402
from test_acceptance_browser_executor import FakeBrowserBackend  # noqa: E402


def _identity(index: int) -> dict[str, object]:
    return {
        "identity_kind": "canonical_json",
        "sha256": "sha256:" + f"{index:x}" * 64,
        "byte_length": 100 + index,
        "revision": f"test.identity.v{index}",
    }


class SemanticAlignmentContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = (
            ROOT
            / "tests"
            / ".tmp_semantic_alignment"
            / self._testMethodName
        )
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        bundle = make_bundle()
        self.view = project_requirement_view(bundle)
        self.plan = compile_acceptance_plan(self.view)
        self.spec = PageSpecBuilder().build(bundle)
        next(
            item
            for item in self.spec.interactions
            if item.interaction_id == "interaction-use-case-search-primary"
        ).user_feedback = "Search results updated with matching products."
        self.spec.validate()
        self.render = DeterministicPageRenderer().render(
            self.spec,
            self.root / "page",
        )
        self.binding = compile_acceptance_binding(
            self.view,
            self.plan,
            self.spec,
            self.render,
        )
        self.report = execute_acceptance_binding_plan(
            self.binding,
            self.render.index_html.resolve().as_uri(),
            backend=FakeBrowserBackend(self.binding),
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def request(self):
        roles = (
            "acceptance_binding",
            "acceptance_plan",
            "browser_execution_report",
            "browser_screenshot",
            "page_spec",
            "result_package_manifest",
        )
        evidence = {
            role: (role, _identity(index))
            for index, role in enumerate(roles, start=1)
        }
        return build_semantic_alignment_request(
            case_id="semantic-contract-case",
            acceptance_plan=self.plan,
            binding_plan=self.binding,
            browser_report=self.report,
            page_spec=self.spec,
            evidence_identities=evidence,
        )

    def test_request_separates_abstract_goal_from_page_feedback(self) -> None:
        request = self.request()

        self.assertFalse(request.browser_control_allowed)
        self.assertEqual(request.model_generate_call_limit, 1)
        self.assertEqual(request.automatic_retry_limit, 0)
        self.assertTrue(request.review_items)
        first = request.review_items[0]
        criterion = next(
            item
            for item in self.plan.criteria
            if item.criterion_id == first.criterion_id
        )
        interaction = next(
            item
            for item in self.spec.interactions
            if item.interaction_id == first.interaction_id
        )
        self.assertEqual(
            first.abstract_expected_outcome,
            dict(criterion.expected_payload)["expected_outcome"],
        )
        self.assertEqual(
            first.observed_user_feedback,
            interaction.user_feedback,
        )
        self.assertNotEqual(
            first.abstract_expected_outcome,
            first.observed_user_feedback,
        )

    def test_result_requires_one_raw_first_call_and_exact_criterion_coverage(self) -> None:
        request = self.request()
        verdicts = tuple(
            SemanticAlignmentVerdict(
                criterion_id=item.criterion_id,
                verdict="unknown",
                evidence_ids=item.evidence_ids,
                reason_summary="Development-only contract probe.",
                limitations="No semantic evaluator was executed in Phase 4.",
            )
            for item in request.review_items
        )
        result = SemanticAlignmentResult(
            schema_version=SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
            request_sha256=request.sha256(),
            evaluator_model_identity="independent-evaluator-placeholder",
            evaluator_prompt_revision="frozen-after-phase4",
            raw_response_sha256="a" * 64,
            raw_response_byte_length=128,
            model_generate_calls=1,
            automatic_retry_count=0,
            verdicts=verdicts,
        )

        result.validate_against(request)
        self.assertEqual(
            result.to_dict(request)["request_sha256"],
            request.sha256(),
        )

        forged = SemanticAlignmentResult(
            schema_version=result.schema_version,
            request_sha256=result.request_sha256,
            evaluator_model_identity=result.evaluator_model_identity,
            evaluator_prompt_revision=result.evaluator_prompt_revision,
            raw_response_sha256=result.raw_response_sha256,
            raw_response_byte_length=result.raw_response_byte_length,
            model_generate_calls=1,
            automatic_retry_count=1,
            verdicts=result.verdicts,
        )
        with self.assertRaisesRegex(ValueError, "one call and no retry"):
            forged.validate_against(request)


if __name__ == "__main__":
    unittest.main()
