from __future__ import annotations

import ast
import copy
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
    D17Path3TierAPreInvocationAuditRecord,
    D17Path3TierAAuditValidationError,
    create_d17_path3_pre_invocation_audit_record,
)
from req2web_provider.d17_input_view import (  # noqa: E402
    D17Path3SelectedInput,
    select_d17_path3_provider_input,
)
from req2web_provider.d17_manifest import D17Path3TierAManifest  # noqa: E402
from req2web_provider.d17_serializer import (  # noqa: E402
    serialize_d17_path3_local_request,
)


def build_context(
    *,
    original_requirement: str = "Build a local meal planner.",
    requirement_summary: str = "AUDIT_PRIVATE_SUMMARY_84d2",
) -> AgentContextBundle:
    return AgentContextBundle(
        original_requirement=original_requirement,
        requirement_summary=requirement_summary,
        target_device="desktop",
        task_type="planner",
        constraints=["AUDIT_CONSTRAINT_ALPHA"],
        use_cases=[
            UseCase("uc_plan", "Plan meal", "cook", "plan a meal", "meal is scheduled"),
            UseCase("uc_view", "View meal", "cook", "view a meal", "meal is visible"),
        ],
        retrieval_queries={role: f"AUDIT_RETRIEVAL_QUERY_{role}" for role in ROLE_ORDER},
        retrieval_results={
            role: [
                {
                    "role": role,
                    "title": f"AUDIT_EVIDENCE_{role}",
                    "uri": "https://example.invalid/audit-private",
                    "secret": "AUDIT_SECRET",
                }
            ]
            for role in ROLE_ORDER
        },
    )


def build_manifest() -> D17Path3TierAManifest:
    return D17Path3TierAManifest.create(
        structural_signal_names=("has_form", "has_empty_state")
    )


def build_audit(
    *,
    source_class: str = "project_authored",
) -> tuple[
    AgentContextBundle,
    D17Path3TierAManifest,
    D17Path3SelectedInput,
    object,
    D17Path3TierAPreInvocationAuditRecord,
]:
    context = build_context()
    manifest = build_manifest()
    selected = select_d17_path3_provider_input(
        context,
        manifest,
        original_requirement_source_class=source_class,
    )
    request = serialize_d17_path3_local_request(context, selected, manifest)
    audit = create_d17_path3_pre_invocation_audit_record(
        context,
        manifest,
        selected,
        request,
    )
    return context, manifest, selected, request, audit


class D17AuditTests(unittest.TestCase):
    def assert_code(self, callback, code: str) -> None:
        with self.assertRaises(D17Path3TierAAuditValidationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn("AUDIT_PRIVATE", str(raised.exception))
        self.assertEqual(raised.exception.envelope.payload_disclosure, "none")

    def test_happy_path_is_replayable_byte_identical_and_pre_invocation_only(self) -> None:
        context, manifest, selected, request, audit = build_audit()
        audit.validate()
        audit.validate_against(context, manifest, selected, request)
        again = create_d17_path3_pre_invocation_audit_record(
            context,
            manifest,
            select_d17_path3_provider_input(
                context,
                manifest,
                original_requirement_source_class="project_authored",
            ),
            serialize_d17_path3_local_request(context, selected, manifest),
        )
        self.assertEqual(audit.canonical_bytes(), again.canonical_bytes())
        replay = D17Path3TierAPreInvocationAuditRecord.from_bytes(audit.canonical_bytes())
        self.assertEqual(replay.to_dict(), audit.to_dict())
        replay.validate_against(context, manifest, selected, request)
        self.assertTrue(audit.local_only)
        self.assertTrue(audit.not_sent)
        self.assertFalse(audit.external_egress_allowed)
        self.assertEqual(audit.provider_invocation_state, "not_invoked")
        self.assertNotIn("invocation_result", audit.to_dict())

    def test_visibility_and_exclusion_cover_the_exact_manifest_boundary(self) -> None:
        _, manifest, _, _, audit = build_audit()
        visibility = audit.visibility_declaration
        self.assertEqual(
            visibility.provider_visible_categories,
            manifest.field_policy.provider_visible_fields,
        )
        self.assertEqual(visibility.use_case_item_keys, manifest.field_policy.use_case_item_keys)
        self.assertEqual(
            visibility.structural_signal_names,
            manifest.field_policy.structural_signal_names,
        )
        exclusions = audit.exclusion_declaration
        self.assertEqual(exclusions.prohibited_data_categories, manifest.prohibited_data_categories)
        self.assertEqual(
            tuple(record.category for record in exclusions.records),
            manifest.prohibited_data_categories,
        )
        self.assertTrue(
            all(record.status == "not_included_in_audit_bytes" for record in exclusions.records)
        )
        self.assertTrue(
            {
                "retrieval_queries",
                "third_party_evidence_text",
                "rico_or_reference_only_assets",
                "secrets_or_credentials",
                "h1",
                "gold",
                "g0_package_paths_or_bytes",
            }.issubset(exclusions.prohibited_data_categories)
        )

    def test_source_attestation_is_local_only_and_never_claims_license_verification(self) -> None:
        context, manifest, selected, request, audit = build_audit(source_class="synthetic")
        attestation = audit.source_attestation
        self.assertEqual(attestation.original_requirement_source_class, "synthetic")
        self.assertEqual(attestation.attestation_scope, "local_attestation_only")
        self.assertFalse(attestation.license_verified)
        self.assertEqual(
            attestation.copyright_license_or_redistribution_rights_status,
            "not_verified_or_asserted",
        )
        self.assert_code(
            lambda: replace(
                audit,
                source_attestation=replace(attestation, license_verified=True),
            ).validate(),
            "audit_source_attestation_invalid",
        )
        audit.validate_against(context, manifest, selected, request)

    def test_audit_bytes_do_not_copy_allowed_or_prohibited_payload_values(self) -> None:
        _, _, _, _, audit = build_audit()
        text = audit.canonical_bytes().decode("utf-8")
        for needle in (
            "Build a local meal planner.",
            "Plan meal",
            "AUDIT_CONSTRAINT_ALPHA",
            "AUDIT_PRIVATE_SUMMARY_84d2",
            "AUDIT_RETRIEVAL_QUERY_",
            "AUDIT_EVIDENCE_",
            "example.invalid/audit-private",
            "AUDIT_SECRET",
        ):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, text)
        self.assertIn("has_form", text)
        self.assertIn("g0_package_paths_or_bytes", text)

    def test_creation_rejects_tampered_selection_request_and_each_request_layer(self) -> None:
        context, manifest, selected, request, _ = build_audit()
        tampered_selection = D17Path3SelectedInput(
            selected.provider_visible_input,
            replace(selected.selection_record, local_only=False),
        )
        self.assert_code(
            lambda: create_d17_path3_pre_invocation_audit_record(
                context, manifest, tampered_selection, request
            ),
            "audit_selection_binding_invalid",
        )
        for layer_name in (
            "input_view_artifact",
            "prompt_artifact",
            "config_artifact",
        ):
            altered_layer = replace(getattr(request, layer_name), local_only=False)
            altered_request = replace(request, **{layer_name: altered_layer})
            with self.subTest(layer=layer_name):
                self.assert_code(
                    lambda altered_request=altered_request: create_d17_path3_pre_invocation_audit_record(
                        context, manifest, selected, altered_request
                    ),
                    "audit_request_binding_invalid",
                )
        self.assert_code(
            lambda: create_d17_path3_pre_invocation_audit_record(
                context,
                manifest,
                selected,
                replace(request, provider_invocation_state="invoked"),
            ),
            "audit_request_binding_invalid",
        )

    def test_live_validation_rejects_forged_rehashed_allowed_shaped_selection_and_request(self) -> None:
        context, manifest, selected, request, audit = build_audit()
        forged_context = build_context(
            original_requirement="Build a different local meal planner.",
        )
        forged_selected = select_d17_path3_provider_input(
            forged_context,
            manifest,
            original_requirement_source_class="project_authored",
        )
        forged_request = serialize_d17_path3_local_request(
            forged_context,
            forged_selected,
            manifest,
        )
        self.assert_code(
            lambda: create_d17_path3_pre_invocation_audit_record(
                context, manifest, forged_selected, forged_request
            ),
            "audit_selection_binding_invalid",
        )
        self.assert_code(
            lambda: audit.validate_against(
                context, manifest, forged_selected, forged_request
            ),
            "audit_selection_binding_invalid",
        )
        self.assert_code(
            lambda: create_d17_path3_pre_invocation_audit_record(
                context, manifest, selected, forged_request
            ),
            "audit_request_binding_invalid",
        )

    def test_record_level_tamper_and_noncanonical_or_unknown_json_fail_closed(self) -> None:
        _, _, _, _, audit = build_audit()
        self.assert_code(
            lambda: replace(audit, not_sent=False).validate(),
            "audit_local_boundary_invalid",
        )
        self.assert_code(
            lambda: replace(audit, manifest_id="d17-manifest-forged").validate(),
            "audit_manifest_binding_invalid",
        )
        for binding_name in (
            "selection_record_binding",
            "input_view_binding",
            "input_view_artifact_binding",
            "prompt_artifact_binding",
            "config_artifact_binding",
            "local_request_binding",
        ):
            binding = getattr(audit, binding_name)
            with self.subTest(binding=binding_name):
                self.assert_code(
                    lambda binding_name=binding_name, binding=binding: replace(
                        audit,
                        **{
                            binding_name: replace(
                                binding,
                                byte_length=binding.byte_length + 1,
                            )
                        },
                    ).validate(),
                    "audit_identity_invalid",
                )
        self.assert_code(
            lambda: replace(
                audit,
                visibility_declaration=replace(
                    audit.visibility_declaration,
                    provider_visible_categories=("original_requirement",),
                ),
            ).validate(),
            "audit_visibility_declaration_invalid",
        )
        self.assert_code(
            lambda: replace(
                audit,
                exclusion_declaration=replace(
                    audit.exclusion_declaration,
                    prohibited_data_categories=("gold",),
                ),
            ).validate(),
            "audit_exclusion_declaration_invalid",
        )
        self.assert_code(
            lambda: D17Path3TierAPreInvocationAuditRecord.from_bytes(
                b"\xef\xbb\xbf" + audit.canonical_bytes()
            ),
            "audit_bytes_invalid",
        )
        self.assert_code(
            lambda: D17Path3TierAPreInvocationAuditRecord.from_bytes(
                b'{"schema_version":"x","schema_version":"y"}'
            ),
            "audit_bytes_invalid",
        )
        self.assert_code(
            lambda: D17Path3TierAPreInvocationAuditRecord.from_bytes(
                b'{"schema_version":NaN}'
            ),
            "audit_bytes_invalid",
        )
        payload = copy.deepcopy(audit.to_dict())
        payload["unexpected"] = "x"
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assert_code(
            lambda: D17Path3TierAPreInvocationAuditRecord.from_bytes(raw),
            "audit_exact_keys_invalid",
        )
        noncanonical = json.dumps(audit.to_dict(), ensure_ascii=False).encode("utf-8")
        self.assert_code(
            lambda: D17Path3TierAPreInvocationAuditRecord.from_bytes(noncanonical),
            "audit_bytes_invalid",
        )

    def test_all_identity_and_length_bindings_match_the_live_artifacts(self) -> None:
        context, manifest, selected, request, audit = build_audit()
        bindings = (
            (audit.selection_record_binding, selected.selection_record.selection_record_id, selected.selection_record.canonical_bytes()),
            (audit.input_view_binding, selected.selection_record.input_view_id, selected.provider_visible_input.canonical_bytes()),
            (audit.input_view_artifact_binding, request.input_view_artifact.artifact_id, request.input_view_artifact.canonical_bytes()),
            (audit.prompt_artifact_binding, request.prompt_artifact.artifact_id, request.prompt_artifact.canonical_bytes()),
            (audit.config_artifact_binding, request.config_artifact.artifact_id, request.config_artifact.canonical_bytes()),
            (audit.local_request_binding, request.artifact_id, request.canonical_bytes()),
        )
        for binding, expected_id, raw in bindings:
            with self.subTest(kind=binding.artifact_kind):
                self.assertEqual(binding.artifact_id, expected_id)
                self.assertEqual(binding.sha256, __import__("hashlib").sha256(raw).hexdigest())
                self.assertEqual(binding.byte_length, len(raw))
                self.assertEqual(binding.content_status, "identity_hash_and_length_only")
        self.assertEqual(
            audit.complete_local_context_sha256,
            selected.selection_record.local_context_sha256,
        )
        audit.validate_against(context, manifest, selected, request)

    def test_static_boundary_has_no_network_runtime_model_or_evaluation_imports(self) -> None:
        module_path = ROOT / "src" / "req2web_provider" / "d17_audit.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        forbidden = (
            "socket",
            "subprocess",
            "urllib",
            "requests",
            "http",
            "httpx",
            "openai",
            "transformers",
            "torch",
            "req2web_evaluation",
        )
        for name in imports:
            with self.subTest(name=name):
                self.assertFalse(name.startswith(forbidden))


if __name__ == "__main__":
    unittest.main()
