from __future__ import annotations

import ast
import copy
import json
import sys
import unittest
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent.schema import AgentContextBundle, UseCase  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_provider.d17_input_view import (  # noqa: E402
    D17InputViewValidationError,
    D17Path3InputSelectionRecord,
    D17Path3ProviderVisibleInputView,
    select_d17_path3_provider_input,
)
from req2web_provider.d17_manifest import D17Path3TierAManifest  # noqa: E402
import req2web_provider.d17_input_view as input_view_module  # noqa: E402


def build_context(*, target_device: str = "desktop", task_type: str = "catalog") -> AgentContextBundle:
    return AgentContextBundle(
        original_requirement="Build a local reading catalog.",
        requirement_summary="D17_SECRET_REQUIREMENT_SUMMARY_6a8d",
        target_device=target_device,
        task_type=task_type,
        constraints=["D17_CONSTRAINT_ALPHA", "D17_CONSTRAINT_BETA"],
        use_cases=[
            UseCase("uc_add", "Add book", "reader", "record a title", "title is saved"),
            UseCase("uc_find", "Find book", "reader", "find a title", "matching title is visible"),
        ],
        retrieval_queries={role: f"D17_RETRIEVAL_QUERY_{role}" for role in ROLE_ORDER},
        retrieval_results={
            role: [{"role": role, "title": f"D17_EVIDENCE_TITLE_{role}", "uri": f"https://example.invalid/{role}", "path": "D17_SOURCE_PATH", "secret": "D17_RETRIEVAL_SECRET"}]
            for role in ROLE_ORDER
        },
    )


def build_manifest() -> D17Path3TierAManifest:
    return D17Path3TierAManifest.create(
        structural_signal_names=("has_form", "supports_recovery")
    )


class D17InputViewTests(unittest.TestCase):
    def assert_code(self, callback, code: str) -> None:
        with self.assertRaises(D17InputViewValidationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn("D17_SECRET", str(raised.exception))

    def test_explicit_selection_is_exact_replayable_and_local_only(self) -> None:
        context = build_context()
        manifest = build_manifest()
        selected = select_d17_path3_provider_input(
            context, manifest, original_requirement_source_class="project_authored"
        )
        selected.validate(manifest)
        view = selected.provider_visible_input
        self.assertEqual(
            tuple(view.to_dict()),
            ("original_requirement", "use_cases", "constraints", "target_device", "task_type", "structural_signals"),
        )
        self.assertEqual(tuple(view.to_dict()["use_cases"][0]), ("use_case_id", "title", "actor", "goal", "expected_outcome"))
        self.assertEqual(view.to_dict()["structural_signals"], ["has_form", "supports_recovery"])
        repeated = select_d17_path3_provider_input(
            context, manifest, original_requirement_source_class="project_authored"
        )
        self.assertEqual(view.canonical_bytes(), repeated.provider_visible_input.canonical_bytes())
        self.assertEqual(selected.selection_record.canonical_bytes(), repeated.selection_record.canonical_bytes())
        self.assertTrue(selected.selection_record.local_only)
        self.assertTrue(selected.selection_record.not_sent)
        self.assertFalse(selected.selection_record.external_egress_allowed)
        self.assertEqual(
            D17Path3ProviderVisibleInputView.from_bytes(view.canonical_bytes()).to_dict(),
            view.to_dict(),
        )
        self.assertEqual(
            D17Path3InputSelectionRecord.from_bytes(selected.selection_record.canonical_bytes()).to_dict(),
            selected.selection_record.to_dict(),
        )

    def test_prohibited_context_values_do_not_enter_provider_visible_bytes_or_selection_record(self) -> None:
        selected = select_d17_path3_provider_input(
            build_context(), build_manifest(), original_requirement_source_class="synthetic"
        )
        visible = selected.provider_visible_input.canonical_bytes().decode("utf-8")
        local_record = selected.selection_record.canonical_bytes().decode("utf-8")
        forbidden_values = (
            "D17_SECRET_REQUIREMENT_SUMMARY_6a8d",
            "D17_RETRIEVAL_QUERY_",
            "D17_EVIDENCE_TITLE_",
            "example.invalid",
            "D17_SOURCE_PATH",
            "D17_RETRIEVAL_SECRET",
        )
        for value in forbidden_values:
            with self.subTest(value=value):
                self.assertNotIn(value, visible)
                self.assertNotIn(value, local_record)

    def test_source_class_and_input_field_validation_fail_closed(self) -> None:
        context = build_context()
        manifest = build_manifest()
        self.assert_code(
            lambda: select_d17_path3_provider_input(context, manifest, original_requirement_source_class="third_party"),
            "source_class_invalid",
        )
        self.assert_code(
            lambda: select_d17_path3_provider_input(build_context(target_device=" "), manifest, original_requirement_source_class="synthetic"),
            "input_view_value_invalid",
        )
        self.assert_code(
            lambda: select_d17_path3_provider_input(build_context(task_type=""), manifest, original_requirement_source_class="synthetic"),
            "input_view_value_invalid",
        )
        bad_use_cases = build_context()
        bad_use_cases.use_cases[0].goal = " "
        self.assert_code(
            lambda: select_d17_path3_provider_input(bad_use_cases, manifest, original_requirement_source_class="synthetic"),
            "input_view_value_invalid",
        )

    def test_structural_signal_presence_is_exact_ordered_and_names_only(self) -> None:
        manifest = build_manifest()
        selected = select_d17_path3_provider_input(build_context(), manifest, original_requirement_source_class="synthetic")
        for signals in (("has_form",), ("supports_recovery", "has_form"), ("has_form", "unexpected")):
            with self.subTest(signals=signals):
                forged = replace(selected.provider_visible_input, structural_signals=signals)
                self.assert_code(lambda forged=forged: forged.validate(manifest), "selection_record_cross_binding_invalid")
        with self.assertRaises(ValueError):
            D17Path3TierAManifest.create(structural_signal_names=("evidence_signal",))

    def test_replay_rejects_unknown_duplicate_bom_and_nonfinite_values(self) -> None:
        selected = select_d17_path3_provider_input(build_context(), build_manifest(), original_requirement_source_class="synthetic")
        payload = selected.provider_visible_input.to_dict()
        payload["unexpected"] = "x"
        self.assert_code(
            lambda: D17Path3ProviderVisibleInputView.from_dict(payload),
            "input_view_exact_keys_invalid",
        )
        good = selected.provider_visible_input.canonical_bytes()
        self.assert_code(
            lambda: D17Path3ProviderVisibleInputView.from_bytes(b"\xef\xbb\xbf" + good),
            "input_view_schema_invalid",
        )
        duplicate = b'{"original_requirement":"a","original_requirement":"b","use_cases":[],"constraints":[],"target_device":"desktop","task_type":"catalog","structural_signals":[]}'
        self.assert_code(
            lambda: D17Path3ProviderVisibleInputView.from_bytes(duplicate),
            "input_view_schema_invalid",
        )
        nonfinite = b'{"original_requirement":"a","use_cases":[{"use_case_id":"u1","title":"t","actor":"a","goal":"g","expected_outcome":"o"},{"use_case_id":"u2","title":"t2","actor":"a","goal":"g2","expected_outcome":"o2"}],"constraints":[],"target_device":NaN,"task_type":"catalog","structural_signals":[]}'
        self.assert_code(
            lambda: D17Path3ProviderVisibleInputView.from_bytes(nonfinite),
            "input_view_schema_invalid",
        )

    def test_forged_allowed_shaped_view_cannot_construct_selection_record(self) -> None:
        context = build_context()
        manifest = build_manifest()
        selected = select_d17_path3_provider_input(
            context, manifest, original_requirement_source_class="project_authored"
        )
        forged_view = D17Path3ProviderVisibleInputView.from_dict(
            {
                **selected.provider_visible_input.to_dict(),
                "original_requirement": "Forged allowed-shaped requirement.",
            },
            manifest=manifest,
        )
        self.assert_code(
            lambda: D17Path3InputSelectionRecord.create(
                context=context,
                manifest=manifest,
                original_requirement_source_class="project_authored",
                provider_visible_input=forged_view,
            ),
            "selection_record_cross_binding_invalid",
        )

    def test_full_context_hash_binds_retrieval_queries_results_and_order(self) -> None:
        context = build_context()
        manifest = build_manifest()
        selected = select_d17_path3_provider_input(
            context, manifest, original_requirement_source_class="synthetic"
        )
        query_changed = copy.deepcopy(context)
        query_changed.retrieval_queries[ROLE_ORDER[0]] = "D17_CHANGED_QUERY"
        query_record = select_d17_path3_provider_input(
            query_changed, manifest, original_requirement_source_class="synthetic"
        ).selection_record
        self.assertNotEqual(
            selected.selection_record.local_context_sha256,
            query_record.local_context_sha256,
        )
        self.assert_code(
            lambda: selected.validate_against(query_changed, manifest),
            "selection_record_cross_binding_invalid",
        )

        result_changed = copy.deepcopy(context)
        result_changed.retrieval_results[ROLE_ORDER[0]].append(
            {"role": ROLE_ORDER[0], "title": "D17_CHANGED_RESULT"}
        )
        result_record = select_d17_path3_provider_input(
            result_changed, manifest, original_requirement_source_class="synthetic"
        ).selection_record
        self.assertNotEqual(
            selected.selection_record.local_context_sha256,
            result_record.local_context_sha256,
        )
        self.assert_code(
            lambda: selected.validate_against(result_changed, manifest),
            "selection_record_cross_binding_invalid",
        )

        order_changed = copy.deepcopy(result_changed)
        order_changed.retrieval_results[ROLE_ORDER[0]].reverse()
        order_record = select_d17_path3_provider_input(
            order_changed, manifest, original_requirement_source_class="synthetic"
        ).selection_record
        self.assertNotEqual(
            result_record.local_context_sha256,
            order_record.local_context_sha256,
        )
        self.assert_code(
            lambda: selected.validate_against(order_changed, manifest),
            "selection_record_cross_binding_invalid",
        )

    def test_manifest_owned_policy_rejects_local_constant_drift_and_source_widening(self) -> None:
        context = build_context()
        manifest = build_manifest()
        for attribute, replacement in (
            ("_SOURCE_CLASSES", ("project_authored", "synthetic", "third_party")),
            ("_PROVIDER_VISIBLE_KEYS", ("original_requirement",)),
            ("_USE_CASE_KEYS", ("use_case_id",)),
        ):
            with self.subTest(attribute=attribute), patch.object(
                input_view_module, attribute, replacement
            ):
                self.assert_code(
                    lambda: select_d17_path3_provider_input(
                        context,
                        manifest,
                        original_requirement_source_class="third_party",
                    ),
                    "manifest_invalid",
                )

    def test_static_boundary_has_no_guidance_runtime_network_model_or_evaluation_imports(self) -> None:
        source_path = ROOT / "src" / "req2web_provider" / "d17_input_view.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("\ufeff"))
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        forbidden = ("req2web_evaluation", "retrieval_guidance", "socket", "urllib", "requests", "http.client", "subprocess", "model", "runtime")
        for name in imports:
            with self.subTest(name=name):
                self.assertFalse(any(token in name.lower() for token in forbidden))


if __name__ == "__main__":
    unittest.main()
