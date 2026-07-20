from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_acceptance import (  # noqa: E402
    INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION,
    RequirementView,
    project_requirement_view,
)
from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle, UseCase  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_rag.validation_signals import build_validation_signals  # noqa: E402


def references() -> list[dict[str, str]]:
    return [
        {"kind": "issue", "uri": "https://example.test/issues/1"},
        {"kind": "pull_request", "uri": "https://example.test/pull/2"},
    ]


def validation_document(
    doc_id: str,
    *,
    category: str = "input_error",
    document_references: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "doc_id": doc_id,
        "metadata": {"category": category},
        "references": references() if document_references is None else document_references,
        "role": "validation",
    }


def legal_validation_signals(
    doc_id: str, *, category: str = "input_error"
) -> list[dict[str, str]]:
    return build_validation_signals(validation_document(doc_id, category=category))


def retrieval_record(
    role: str,
    doc_id: str,
    *,
    record_references: list[dict[str, str]] | None = None,
    validation_signals: list[dict[str, str]] | None = None,
    include_validation_signals: bool = True,
) -> dict[str, object]:
    result: dict[str, object] = {
        "doc_id": doc_id,
        "references": references() if record_references is None else record_references,
        "role": role,
        "summary": f"{role} evidence summary",
        "title": f"{role} evidence title",
    }
    if include_validation_signals:
        result["validation_signals"] = validation_signals or []
    return result


def make_bundle() -> AgentContextBundle:
    validation_doc_id = "validation:doc-001"
    retrieval_results = {
        role: [
            retrieval_record(
                role,
                f"{role}:doc-001",
                validation_signals=legal_validation_signals(f"{role}:doc-001")
                if role != "validation"
                else legal_validation_signals(validation_doc_id),
            )
        ]
        for role in ROLE_ORDER
    }
    retrieval_results["validation"] = [
        retrieval_record(
            "validation",
            validation_doc_id,
            validation_signals=legal_validation_signals(validation_doc_id),
        )
    ]
    return AgentContextBundle(
        original_requirement="Create a mobile product search page with input recovery.",
        requirement_summary="A mobile product search flow with explicit input recovery.",
        target_device="mobile",
        task_type="search_list",
        constraints=["Support responsive layout.", "Show input-error recovery."],
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


def replace_evidence(view: RequirementView, index: int, evidence: object) -> RequirementView:
    evidence_items = list(view.validation_evidence)
    evidence_items[index] = evidence
    return replace(view, validation_evidence=tuple(evidence_items))


class RequirementViewTest(unittest.TestCase):
    def test_projects_a_read_only_internal_requirement_view(self) -> None:
        bundle = make_bundle()

        view = project_requirement_view(bundle)

        self.assertEqual(view.schema_version, INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION)
        self.assertEqual(view.source_context_schema_version, AGENT_BUNDLE_SCHEMA_VERSION)
        self.assertTrue(view.requirement.requirement_id.startswith("requirement-"))
        self.assertEqual(
            view.requirement.requirement_id,
            "requirement-"
            + hashlib.sha256(bundle.original_requirement.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            [item.use_case_id for item in view.use_cases],
            ["use-case-search", "use-case-retry"],
        )
        self.assertEqual(
            [item.constraint_id for item in view.constraints],
            [
                "constraint-"
                + hashlib.sha256(item.encode("utf-8")).hexdigest()
                for item in bundle.constraints
            ],
        )
        self.assertEqual(len(view.validation_evidence), 1)
        evidence = view.validation_evidence[0]
        self.assertEqual(evidence.source_doc_id, "validation:doc-001")
        self.assertEqual(
            evidence.reference_uris,
            ("https://example.test/issues/1", "https://example.test/pull/2"),
        )
        self.assertEqual(len(evidence.signals), 1)
        self.assertEqual(evidence.signals[0].value, "retry_recovery")
        self.assertNotIn("requirement:doc-001", view.canonical_json_bytes().decode("utf-8"))

    def test_projection_is_deterministic_and_hashes_canonical_utf8(self) -> None:
        first = project_requirement_view(make_bundle())
        second = project_requirement_view(make_bundle())

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.canonical_json_bytes(), second.canonical_json_bytes())
        self.assertEqual(first.sha256(), second.sha256())
        self.assertEqual(first.sha256(), hashlib.sha256(first.canonical_json_bytes()).hexdigest())
        self.assertIn(
            b'"schema_version":"req2web.acceptance.requirement_view.v1"',
            first.canonical_json_bytes(),
        )

    def test_projection_rejects_duplicate_or_illegal_source_fields(self) -> None:
        duplicate_constraints = make_bundle()
        duplicate_constraints.constraints = ["same constraint", "same constraint"]
        with self.assertRaisesRegex(ValueError, "constraints must not contain duplicate"):
            project_requirement_view(duplicate_constraints)

        duplicate_use_case_ids = make_bundle()
        duplicate_use_case_ids.use_cases[1].use_case_id = duplicate_use_case_ids.use_cases[0].use_case_id
        with self.assertRaisesRegex(ValueError, "use_case_id"):
            project_requirement_view(duplicate_use_case_ids)

        malformed_validation_result = make_bundle()
        malformed_validation_result.retrieval_results["validation"][0]["validation_signals"] = "not-a-list"
        view = project_requirement_view(malformed_validation_result)
        self.assertEqual(view.validation_evidence[0].signals, ())

        wrong_schema = replace(
            make_bundle(), schema_version="req2web.agent.context.invalid"
        )
        with self.assertRaisesRegex(ValueError, "unsupported AgentContext schema"):
            project_requirement_view(wrong_schema)

    def test_complete_tampered_signal_is_excluded_without_dropping_evidence(self) -> None:
        bundle = make_bundle()
        raw_signal = bundle.retrieval_results["validation"][0]["validation_signals"][0]
        raw_signal["source_value"] = "auth_access"

        view = project_requirement_view(bundle)

        self.assertEqual(len(view.validation_evidence), 1)
        self.assertEqual(view.validation_evidence[0].source_doc_id, "validation:doc-001")
        self.assertEqual(view.validation_evidence[0].signals, ())

    def test_old_compact_result_without_signal_field_remains_compatible(self) -> None:
        bundle = make_bundle()
        bundle.retrieval_results["validation"][0].pop("validation_signals")

        view = project_requirement_view(bundle)

        self.assertEqual(len(view.validation_evidence), 1)
        self.assertEqual(view.validation_evidence[0].signals, ())

    def test_validation_evidence_cannot_cross_the_validation_role_boundary(self) -> None:
        bundle = make_bundle()
        bundle.retrieval_results["validation"][0]["role"] = "implementation"

        with self.assertRaisesRegex(ValueError, "wrong role: validation"):
            project_requirement_view(bundle)

    def test_self_constructed_view_tampering_is_rejected(self) -> None:
        view = project_requirement_view(make_bundle())
        evidence = view.validation_evidence[0]
        signal = evidence.signals[0]

        bad_constraint = replace(view.constraints[0], source="generated_candidate")
        with self.assertRaisesRegex(ValueError, "constraint.source"):
            replace(view, constraints=(bad_constraint, *view.constraints[1:])).validate()

        bad_signal_source = replace(signal, source_value="auth_access")
        with self.assertRaisesRegex(ValueError, "not accepted by the validation signal adapter"):
            replace_evidence(
                view,
                0,
                replace(evidence, signals=(bad_signal_source,)),
            ).validate()

        bad_signal_reference = replace(
            signal, reference_uri="https://example.test/issues/not-in-evidence"
        )
        with self.assertRaisesRegex(ValueError, "reference_uri must belong"):
            replace_evidence(
                view,
                0,
                replace(evidence, signals=(bad_signal_reference,)),
            ).validate()

        with self.assertRaisesRegex(ValueError, "use_cases must contain 2-4"):
            replace(view, use_cases=(view.use_cases[0],)).validate()

    def test_json_serialization_and_source_object_are_unchanged(self) -> None:
        bundle = make_bundle()
        before = copy.deepcopy(bundle)

        view = project_requirement_view(bundle)
        encoded = json.dumps(
            view.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

        self.assertEqual(bundle, before)
        self.assertEqual(encoded.encode("utf-8"), view.canonical_json_bytes())
        self.assertEqual(json.loads(encoded), view.to_dict())
        self.assertEqual(view.to_dict()["validation_evidence"][0]["signals"][0]["value"], "retry_recovery")


if __name__ == "__main__":
    unittest.main()
