from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import req2web_provider.d17_manifest as d17_manifest_module  # noqa: E402
from req2web_provider.d17_manifest import (  # noqa: E402
    D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
    D17_PATH3_TIER_A_FIELD_POLICY_SHA256_HEX,
    D17Path3FieldPolicy,
    D17Path3TierAManifest,
    D17ManifestValidationError,
    validate_d17_path3_tier_a_manifest,
)


class D17Path3TierAManifestTest(unittest.TestCase):
    def build_manifest(self) -> D17Path3TierAManifest:
        return D17Path3TierAManifest.create(
            structural_signal_names=("has_form", "supports_recovery")
        )

    @staticmethod
    def recompute_manifest_id(payload: dict[str, object]) -> dict[str, object]:
        root = dict(payload)
        root.pop("manifest_id", None)
        root["manifest_id"] = d17_manifest_module._manifest_id(root)
        return root

    def assert_error_code(self, callback: object, code: str) -> None:
        with self.assertRaises(D17ManifestValidationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(
            raised.exception.envelope.to_dict(),
            {
                "schema_version": "req2web.d17.path3.tier_a.error.v1",
                "code": code,
                "stage": "d17",
                "phase": "manifest_validation",
                "payload_disclosure": "none",
                "retryable": False,
            },
        )

    def test_approved_snapshot_recomputes_the_exact_approval_digest(self) -> None:
        self.assertEqual(
            D17Path3FieldPolicy.approved_snapshot_sha256(),
            D17_PATH3_TIER_A_FIELD_POLICY_SHA256_HEX,
        )
        self.assertEqual(
            D17Path3FieldPolicy.approved_snapshot(),
            {
                "authorization": "tier_a_local_implementation_only",
                "d17_path": "path_3",
                "external_egress_allowed": False,
                "formal_quality_allowed": False,
                "h1_allowed": False,
                "path_1_status": "N/A / \u672a\u6388\u6743",
                "path_2_status": "N/A / \u672a\u6388\u6743",
                "provider_visibility_prohibited": [
                    "full_agent_context",
                    "requirement_summary",
                    "retrieval_queries",
                    "retrieval_results",
                    "full_retrieval_guidance",
                    "guidance_item_values",
                    "third_party_evidence_text",
                    "evidence_title",
                    "evidence_uri",
                    "source_path",
                    "evidence_doc_id_or_adoption_identifier",
                    "rico_or_reference_only_assets",
                    "secrets_or_credentials",
                    "h1",
                    "gold",
                    "deep_labels",
                    "local_canonical_identity_or_traceability_fields",
                    "g0_package_paths_or_bytes",
                ],
                "provider_visible_fields": {
                    "constraints": {
                        "content": "normalized",
                        "source_classes": ["project_authored", "synthetic"],
                    },
                    "original_requirement": {
                        "content": "minimal_requirement_text",
                        "source_classes": ["project_authored", "synthetic"],
                    },
                    "structural_signals": {
                        "content": "evidence_free",
                        "policy": "explicit_per_manifest_enumeration_only",
                    },
                    "target_device": {"content": "normalized"},
                    "task_type": {"content": "normalized"},
                    "use_cases": {
                        "content": "normalized_canonical_requirement_structure"
                    },
                },
                "schema": "req2web.d17.path3.tier_a.field_policy.v1",
                "serializer_policy": {
                    "bom": False,
                    "encoding": "UTF-8",
                    "exact_byte_length_and_sha256_required": True,
                    "prompt_and_config_separately_versioned": True,
                    "schema_and_version_fixed": True,
                    "unknown_or_unlisted_fields": "fail_closed",
                },
            },
        )

    def test_fixed_snapshot_declaration_drift_fails_closed(self) -> None:
        drift_cases = (
            (
                "provider_visible_fields",
                "_FIXED_PROVIDER_VISIBLE_FIELDS",
                d17_manifest_module._FIXED_PROVIDER_VISIBLE_FIELDS
                + ("requirement_summary",),
            ),
            (
                "use_case_item_keys",
                "_FIXED_USE_CASE_ITEM_KEYS",
                d17_manifest_module._FIXED_USE_CASE_ITEM_KEYS + ("unexpected_key",),
            ),
            (
                "prohibited_categories",
                "_FIXED_PROHIBITED_DATA_CATEGORIES",
                d17_manifest_module._FIXED_PROHIBITED_DATA_CATEGORIES
                + ("new_prohibited_category",),
            ),
            (
                "serializer_policy",
                "_FIXED_SERIALIZER_POLICY",
                {
                    **d17_manifest_module._FIXED_SERIALIZER_POLICY,
                    "encoding": "UTF-16",
                },
            ),
        )
        for name, attribute, replacement in drift_cases:
            with self.subTest(name=name), patch.object(
                d17_manifest_module, attribute, replacement
            ):
                self.assert_error_code(
                    lambda: D17Path3FieldPolicy.create(),
                    "field_policy_snapshot_invalid",
                )

    def test_happy_path_is_payload_free_and_replayable(self) -> None:
        manifest = self.build_manifest()
        payload = manifest.to_dict()
        replayed = validate_d17_path3_tier_a_manifest(json.loads(manifest.canonical_bytes()))

        self.assertEqual(replayed, manifest)
        self.assertEqual(manifest.canonical_bytes(), replayed.canonical_bytes())
        self.assertEqual(manifest.sha256(), replayed.sha256())
        self.assertFalse(manifest.external_egress_allowed)
        self.assertFalse(manifest.h1_allowed)
        self.assertFalse(manifest.formal_quality_allowed)
        self.assertEqual(manifest.d17_path, "path_3")
        self.assertEqual(manifest.path_1_status, "N/A / \u672a\u6388\u6743")
        self.assertEqual(manifest.path_2_status, "N/A / \u672a\u6388\u6743")
        self.assertEqual(
            manifest.field_policy.policy_identity,
            D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
        )
        self.assertEqual(
            manifest.field_policy.use_case_item_keys,
            ("use_case_id", "title", "actor", "goal", "expected_outcome"),
        )
        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "authorization",
                "d17_path",
                "path_1_status",
                "path_2_status",
                "execution_scope",
                "external_egress_allowed",
                "formal_quality_allowed",
                "h1_allowed",
                "payload_status",
                "field_policy",
                "prohibited_data_declared",
                "prohibited_data_categories",
                "manifest_id",
            },
        )
        self.assertNotIn("original_requirement", payload)
        self.assertNotIn("use_cases", payload)
        self.assertNotIn("constraints", payload)

    def test_unknown_field_and_payload_value_fail_closed_without_leakage(self) -> None:
        for field_name in ("unknown", "original_requirement", "payload_bytes"):
            payload = self.build_manifest().to_dict()
            payload[field_name] = "private-value-must-not-appear"
            payload = self.recompute_manifest_id(payload)
            with self.subTest(field_name=field_name):
                with self.assertRaises(D17ManifestValidationError) as raised:
                    validate_d17_path3_tier_a_manifest(payload)
                self.assertEqual(raised.exception.code, "manifest_exact_keys_invalid")
                self.assertNotIn("private-value-must-not-appear", str(raised.exception))
                self.assertNotIn(
                    "private-value-must-not-appear",
                    json.dumps(raised.exception.envelope.to_dict()),
                )

    def test_field_policy_hash_and_allowlist_expansion_fail_after_outer_id_recomputation(self) -> None:
        payload = self.build_manifest().to_dict()
        field_policy = dict(payload["field_policy"])
        field_policy["policy_identity"] = (
            "req2web.d17.path3.tier_a.field_policy.v1 / sha256:" + ("0" * 64)
        )
        payload["field_policy"] = field_policy
        self.assert_error_code(
            lambda: validate_d17_path3_tier_a_manifest(
                self.recompute_manifest_id(payload)
            ),
            "field_policy_identity_invalid",
        )

        expanded = self.build_manifest().to_dict()
        expanded_policy = dict(expanded["field_policy"])
        expanded_policy["provider_visible_fields"] = list(
            expanded_policy["provider_visible_fields"]
        ) + ["requirement_summary"]
        expanded["field_policy"] = expanded_policy
        self.assert_error_code(
            lambda: validate_d17_path3_tier_a_manifest(
                self.recompute_manifest_id(expanded)
            ),
            "field_policy_invalid",
        )

    def test_path_tier_egress_and_h1_switches_fail_after_outer_id_recomputation(self) -> None:
        changes = (
            ("d17_path", "path_1", "path_selection_invalid"),
            ("d17_path", "path_2", "path_selection_invalid"),
            ("authorization", "tier_b_pilot_execution", "tier_a_boundary_invalid"),
            ("external_egress_allowed", True, "tier_a_boundary_invalid"),
            ("h1_allowed", True, "tier_a_boundary_invalid"),
            ("formal_quality_allowed", True, "tier_a_boundary_invalid"),
        )
        for key, value, expected_code in changes:
            payload = self.build_manifest().to_dict()
            payload[key] = value
            with self.subTest(key=key):
                self.assert_error_code(
                    lambda payload=payload: validate_d17_path3_tier_a_manifest(
                        self.recompute_manifest_id(payload)
                    ),
                    expected_code,
                )

    def test_prohibited_data_declaration_is_fixed_and_complete(self) -> None:
        for key, value in (
            ("prohibited_data_declared", False),
            ("prohibited_data_categories", ["full_agent_context"]),
        ):
            payload = self.build_manifest().to_dict()
            payload[key] = value
            with self.subTest(key=key):
                self.assert_error_code(
                    lambda payload=payload: validate_d17_path3_tier_a_manifest(
                        self.recompute_manifest_id(payload)
                    ),
                    "prohibited_data_declaration_invalid",
                )

    def test_structural_signals_are_per_manifest_enumerated_and_cannot_launder_prohibited_data(self) -> None:
        self.assertEqual(
            D17Path3TierAManifest.create(
                structural_signal_names=("has_form", "has_recovery_state")
            ).field_policy.structural_signal_names,
            ("has_form", "has_recovery_state"),
        )
        for names in (
            ("has_form", "has_form"),
            ("evidence_title",),
            ("retrieval_state",),
            ("invalid-hyphen",),
        ):
            with self.subTest(names=names):
                self.assert_error_code(
                    lambda names=names: D17Path3TierAManifest.create(
                        structural_signal_names=names
                    ),
                    "structural_signal_enumeration_invalid",
                )

    def test_direct_tampering_cannot_be_repaired_by_recomputing_manifest_identity(self) -> None:
        manifest = self.build_manifest()
        forged_policy = replace(
            manifest.field_policy,
            use_case_item_keys=("use_case_id", "title", "actor", "goal"),
        )
        forged_root = manifest.to_dict()
        forged_root["field_policy"] = {
            "schema_version": forged_policy.schema_version,
            "policy_identity": forged_policy.policy_identity,
            "provider_visible_fields": list(forged_policy.provider_visible_fields),
            "original_requirement_source_classes": list(
                forged_policy.original_requirement_source_classes
            ),
            "use_case_item_keys": list(forged_policy.use_case_item_keys),
            "structural_signal_names": list(forged_policy.structural_signal_names),
            "structural_signal_policy": forged_policy.structural_signal_policy,
            "unknown_or_unlisted_fields": forged_policy.unknown_or_unlisted_fields,
        }
        self.assert_error_code(
            lambda: validate_d17_path3_tier_a_manifest(
                self.recompute_manifest_id(forged_root)
            ),
            "field_policy_invalid",
        )

    def test_direct_field_policy_validation_rejects_unknown_or_prohibited_shapes(self) -> None:
        payload = D17Path3FieldPolicy.create().to_dict()
        payload["extra"] = "not-allowed"
        self.assert_error_code(
            lambda: D17Path3FieldPolicy.from_dict(payload), "field_policy_invalid"
        )

    def test_static_boundary_has_no_runtime_or_network_dependency(self) -> None:
        source = (ROOT / "src" / "req2web_provider" / "d17_manifest.py").read_text(
            encoding="utf-8"
        )
        self.assertFalse(source.startswith("\ufeff"))
        for token in (
            "import socket",
            "import subprocess",
            "import urllib",
            "import requests",
            "req2web_evaluation",
            "MockSmokePageSpecProvider",
            "CanonicalPageSpecAssembler",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()