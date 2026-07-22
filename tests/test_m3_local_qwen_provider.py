from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent.schema import AgentContextBundle, UseCase  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_provider.d17_audit import (  # noqa: E402
    create_d17_path3_pre_invocation_audit_record,
)
from req2web_provider.d17_input_view import (  # noqa: E402
    D17Path3SelectedInput,
    D17Path3ProviderVisibleInputView,
    select_d17_path3_provider_input,
)
from req2web_provider.d17_manifest import D17Path3TierAManifest  # noqa: E402
from req2web_provider.d17_serializer import serialize_d17_path3_local_request  # noqa: E402
from req2web_provider import local_qwen_provider as provider_module  # noqa: E402
from req2web_provider.local_qwen_provider import (  # noqa: E402
    LocalQwenProviderInvocationBlockedError,
    LocalQwenProviderPreparationRecord,
    LocalQwenProviderPreparationValidationError,
    invoke_local_qwen_provider,
    prepare_local_qwen_provider_interface,
)


def build_context(*, original_requirement: str = "QWEN_PRIVATE_REQUIREMENT_102") -> AgentContextBundle:
    return AgentContextBundle(
        original_requirement=original_requirement,
        requirement_summary="QWEN_PRIVATE_SUMMARY_103",
        target_device="desktop",
        task_type="planner",
        constraints=["QWEN_PRIVATE_CONSTRAINT_104"],
        use_cases=[
            UseCase("uc_plan", "QWEN_PRIVATE_PLAN", "cook", "plan", "scheduled"),
            UseCase("uc_view", "QWEN_PRIVATE_VIEW", "cook", "view", "visible"),
        ],
        retrieval_queries={role: f"QWEN_PRIVATE_QUERY_{role}" for role in ROLE_ORDER},
        retrieval_results={
            role: [{"role": role, "title": f"QWEN_PRIVATE_EVIDENCE_{role}", "secret": "QWEN_PRIVATE_SECRET"}]
            for role in ROLE_ORDER
        },
    )


def build_chain(*, context: AgentContextBundle | None = None):
    context = context or build_context()
    manifest = D17Path3TierAManifest.create(structural_signal_names=("has_form", "has_empty_state"))
    selected = select_d17_path3_provider_input(
        context, manifest, original_requirement_source_class="project_authored"
    )
    request = serialize_d17_path3_local_request(context, selected, manifest)
    audit = create_d17_path3_pre_invocation_audit_record(context, manifest, selected, request)
    preparation = prepare_local_qwen_provider_interface(context, manifest, selected, request, audit)
    return context, manifest, selected, request, audit, preparation


class LocalQwenProviderTests(unittest.TestCase):
    def assert_code(self, callback, code: str) -> None:
        with self.assertRaises(LocalQwenProviderPreparationValidationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.envelope.payload_disclosure, "none")
        self.assertNotIn("QWEN_PRIVATE", str(raised.exception))

    def test_happy_path_is_byte_identical_and_declares_only_preparation(self) -> None:
        context, manifest, selected, request, audit, preparation = build_chain()
        preparation.validate_against(context, manifest, selected, request, audit)
        again = prepare_local_qwen_provider_interface(context, manifest, selected, request, audit)
        self.assertEqual(preparation.canonical_bytes(), again.canonical_bytes())
        replay = LocalQwenProviderPreparationRecord.from_bytes(preparation.canonical_bytes())
        self.assertEqual(replay.to_dict(), preparation.to_dict())
        replay.validate_against(context, manifest, selected, request, audit)
        self.assertTrue(preparation.local_only)
        self.assertTrue(preparation.not_sent)
        self.assertFalse(preparation.external_egress_allowed)
        self.assertFalse(preparation.model_loaded)
        self.assertEqual(preparation.provider_invocation_state, "not_invoked")
        self.assertEqual(preparation.transport_state, "not_sent")
        self.assertEqual(preparation.tier_b_authorization, "tier_b_unapproved")
        self.assertEqual(preparation.candidate_model_family, "Qwen3.5-9B")
        self.assertEqual(
            set(preparation.tier_b_unapproved_declaration.to_values()),
            {"tier_b_unapproved"},
        )

    def test_binds_every_live_layer_by_exact_identity_digest_and_length(self) -> None:
        context, manifest, selected, request, audit, preparation = build_chain()
        expected = (
            (preparation.selection_record_binding, selected.selection_record.selection_record_id, selected.selection_record.canonical_bytes()),
            (preparation.input_view_binding, selected.selection_record.input_view_id, selected.provider_visible_input.canonical_bytes()),
            (preparation.input_view_artifact_binding, request.input_view_artifact.artifact_id, request.input_view_artifact.canonical_bytes()),
            (preparation.prompt_artifact_binding, request.prompt_artifact.artifact_id, request.prompt_artifact.canonical_bytes()),
            (preparation.config_artifact_binding, request.config_artifact.artifact_id, request.config_artifact.canonical_bytes()),
            (preparation.local_request_binding, request.artifact_id, request.canonical_bytes()),
            (preparation.pre_invocation_audit_binding, audit.audit_record_id, audit.canonical_bytes()),
        )
        for binding, identity, raw in expected:
            with self.subTest(kind=binding.artifact_kind):
                self.assertEqual(binding.artifact_id, identity)
                self.assertEqual(binding.sha256, hashlib.sha256(raw).hexdigest())
                self.assertEqual(binding.byte_length, len(raw))
                self.assertEqual(binding.content_status, "identity_hash_and_length_only")
        self.assertEqual(preparation.complete_local_context_sha256, selected.selection_record.local_context_sha256)
        preparation.validate_against(context, manifest, selected, request, audit)

    def test_payload_free_record_does_not_copy_request_or_context_values(self) -> None:
        _, _, _, request, _, preparation = build_chain()
        payload = preparation.canonical_bytes().decode("utf-8")
        forbidden = (
            "QWEN_PRIVATE_REQUIREMENT_102", "QWEN_PRIVATE_SUMMARY_103",
            "QWEN_PRIVATE_CONSTRAINT_104", "QWEN_PRIVATE_PLAN", "QWEN_PRIVATE_VIEW",
            "QWEN_PRIVATE_SECRET", "QWEN_PRIVATE_EVIDENCE_", "QWEN_PRIVATE_QUERY_",
            request.canonical_bytes().decode("utf-8"), "has_form", "has_empty_state",
        )
        for value in forbidden:
            with self.subTest(value=value[:24]):
                self.assertNotIn(value, payload)

    def test_creation_rejects_context_request_and_audit_tamper(self) -> None:
        context, manifest, selected, request, audit, _ = build_chain()
        altered_context = build_context()
        altered_context.retrieval_queries[ROLE_ORDER[0]] = "QWEN_PRIVATE_CHANGED_QUERY"
        self.assert_code(
            lambda: prepare_local_qwen_provider_interface(altered_context, manifest, selected, request, audit),
            "preparation_selection_binding_invalid",
        )
        self.assert_code(
            lambda: prepare_local_qwen_provider_interface(
                context, manifest, selected, replace(request, transport_state="sent"), audit
            ),
            "preparation_request_binding_invalid",
        )
        self.assert_code(
            lambda: prepare_local_qwen_provider_interface(
                context, manifest, selected, request, replace(audit, provider_invocation_state="invoked")
            ),
            "preparation_audit_binding_invalid",
        )

    def test_forged_rehashed_allowed_shaped_request_or_audit_cannot_enter(self) -> None:
        context, manifest, selected, request, audit, _ = build_chain()
        other_context = build_context(original_requirement="QWEN_PRIVATE_OTHER_REQUIREMENT")
        other_selected = select_d17_path3_provider_input(
            other_context, manifest, original_requirement_source_class="project_authored"
        )
        other_request = serialize_d17_path3_local_request(other_context, other_selected, manifest)
        other_audit = create_d17_path3_pre_invocation_audit_record(
            other_context, manifest, other_selected, other_request
        )
        self.assert_code(
            lambda: prepare_local_qwen_provider_interface(context, manifest, selected, other_request, audit),
            "preparation_request_binding_invalid",
        )
        self.assert_code(
            lambda: prepare_local_qwen_provider_interface(context, manifest, selected, request, other_audit),
            "preparation_audit_binding_invalid",
        )
        forged_view = D17Path3ProviderVisibleInputView.from_dict(
            {**selected.provider_visible_input.to_dict(), "original_requirement": "QWEN_PRIVATE_FORGED"},
            manifest=manifest,
        )
        forged_selected = D17Path3SelectedInput(forged_view, selected.selection_record)
        self.assert_code(
            lambda: prepare_local_qwen_provider_interface(context, manifest, forged_selected, request, audit),
            "preparation_selection_binding_invalid",
        )


    def test_status_and_tier_b_tamper_fail_closed_even_when_rehashed(self) -> None:
        _, _, _, _, _, preparation = build_chain()
        for changed, code in (
            (replace(preparation, model_loaded=True), "preparation_local_boundary_invalid"),
            (replace(preparation, transport_state="sent"), "preparation_local_boundary_invalid"),
            (replace(preparation, provider_invocation_state="invoked"), "preparation_local_boundary_invalid"),
            (replace(preparation, tier_b_authorization="approved"), "preparation_tier_b_declaration_invalid"),
            (
                replace(
                    preparation,
                    tier_b_unapproved_declaration=replace(
                        preparation.tier_b_unapproved_declaration,
                        model_revision="invented-default",
                    ),
                ),
                "preparation_tier_b_declaration_invalid",
            ),
        ):
            with self.subTest(code=code):
                self.assert_code(changed.validate, code)

    def test_record_tamper_and_strict_json_fail_closed(self) -> None:
        _, _, _, _, _, preparation = build_chain()
        self.assert_code(
            lambda: LocalQwenProviderPreparationRecord.from_bytes(b"\xef\xbb\xbf" + preparation.canonical_bytes()),
            "preparation_bytes_invalid",
        )
        self.assert_code(
            lambda: LocalQwenProviderPreparationRecord.from_bytes(
                b'{"schema_version":"x","schema_version":"y"}'
            ),
            "preparation_bytes_invalid",
        )
        self.assert_code(
            lambda: LocalQwenProviderPreparationRecord.from_bytes(b'{"schema_version":NaN}'),
            "preparation_bytes_invalid",
        )
        unknown = copy.deepcopy(preparation.to_dict())
        unknown["unexpected"] = "x"
        raw = json.dumps(
            unknown, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        self.assert_code(
            lambda: LocalQwenProviderPreparationRecord.from_bytes(raw),
            "preparation_exact_keys_invalid",
        )
        noncanonical = json.dumps(preparation.to_dict(), ensure_ascii=False).encode("utf-8")
        self.assert_code(
            lambda: LocalQwenProviderPreparationRecord.from_bytes(noncanonical),
            "preparation_bytes_invalid",
        )
        self.assert_code(
            lambda: replace(preparation, preparation_record_id="local-qwen-provider-preparation-" + "0" * 64).validate(),
            "preparation_identity_invalid",
        )

    def test_invocation_gate_has_no_injection_parameters_and_always_blocks(self) -> None:
        self.assertEqual(tuple(inspect.signature(invoke_local_qwen_provider).parameters), ())
        with self.assertRaises(LocalQwenProviderInvocationBlockedError) as raised:
            invoke_local_qwen_provider()
        self.assertEqual(raised.exception.code, "tier_b_unapproved")
        self.assertEqual(raised.exception.envelope.payload_disclosure, "none")
        self.assertNotIn("QWEN_PRIVATE", str(raised.exception))

    def test_static_boundary_has_no_runtime_network_evaluation_or_io_imports_or_calls(self) -> None:
        source_path = ROOT / "src" / "req2web_provider" / "local_qwen_provider.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("\ufeff"))
        tree = ast.parse(source)
        imports: list[str] = []
        calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.append(node.func.attr)
        forbidden_import_tokens = (
            "socket", "subprocess", "urllib", "requests", "http", "httpx", "openai",
            "transformers", "torch", "vllm", "sglang", "req2web_evaluation", "runtime",
        )
        for name in imports:
            with self.subTest(import_name=name):
                self.assertFalse(any(token in name.lower() for token in forbidden_import_tokens))
        forbidden_calls = {"open", "getenv", "read_text", "read_bytes", "write_text", "write_bytes", "system", "run", "popen", "urlopen"}
        self.assertFalse(forbidden_calls.intersection(calls))


if __name__ == "__main__":
    unittest.main()
