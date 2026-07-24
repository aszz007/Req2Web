import hashlib
import inspect
import json
import unittest
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.tier_b_readiness as module
from req2web_runtime import (
    TIER_B_EXTERNAL_ACTION_BINDING_DECLARATION_SCHEMA_VERSION,
    TIER_B_MANAGER_RUN_PLAN_SCHEMA_VERSION,
    TierBExternalActionBindingDeclaration,
    TierBManagerRunPlan,
    TierBReadinessError,
    create_tier_b_external_action_binding_declaration,
    create_tier_b_manager_run_plan,
    validate_tier_b_external_action_binding_declaration_bytes,
    validate_tier_b_manager_run_plan_bytes,
)


ARTIFACT_PREFIXES = (
    "d17_manifest", "provider_visible_payload", "selection_record",
    "input_view_artifact", "prompt_artifact", "config_artifact", "local_request",
    "pre_invocation_audit", "preparation_record", "tier_a_08_bundle",
    "same_case_frozen_g0_reference", "model_artifact_integrity_manifest",
    "runtime_environment_image_record",
)
SAFE_IDS = {
    "d17_manifest": "d17-manifest-alpha",
    "provider_visible_payload": "visible-input-alpha",
    "selection_record": "selection-record-alpha",
    "input_view_artifact": "input-view-alpha",
    "prompt_artifact": "prompt-artifact-alpha",
    "config_artifact": "config-artifact-alpha",
    "local_request": "local-request-alpha",
    "pre_invocation_audit": "pre-invocation-audit-alpha",
    "preparation_record": "preparation-record-alpha",
    "tier_a_08_bundle": "tier-a-08-bundle-alpha",
    "same_case_frozen_g0_reference": "same-case-g0-reference-alpha",
    "model_artifact_integrity_manifest": "model-integrity-manifest-alpha",
    "runtime_environment_image_record": "runtime-environment-image-alpha",
}


def artifact_bindings():
    values = {}
    digits = "0123456789abcdef"
    for index, prefix in enumerate(ARTIFACT_PREFIXES):
        values[f"{prefix}_id"] = SAFE_IDS[prefix]
        values[f"{prefix}_sha256"] = digits[index] * 64
        values[f"{prefix}_byte_length"] = 100 + index
    return values


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def reidentify_plan(payload):
    root = {key: value for key, value in payload.items() if key != "plan_id"}
    payload["plan_id"] = module.TIER_B_MANAGER_RUN_PLAN_ID_PREFIX + hashlib.sha256(canonical(root)).hexdigest()[:20]


def reidentify_declaration(payload):
    root = {key: value for key, value in payload.items() if key != "declaration_id"}
    payload["declaration_id"] = module.TIER_B_EXTERNAL_ACTION_BINDING_DECLARATION_ID_PREFIX + hashlib.sha256(canonical(root)).hexdigest()[:20]


class TierBReadinessTests(unittest.TestCase):
    def plan(self, **overrides):
        return create_tier_b_manager_run_plan(overrides=overrides or None)

    def declaration(self, plan=None, **changes):
        values = artifact_bindings()
        values.update(changes)
        return create_tier_b_external_action_binding_declaration(
            plan or self.plan(), case_id="pilot-case-alpha", source_class="synthetic", **values
        )

    def assert_rejected(self, thunk):
        with self.assertRaises(TierBReadinessError):
            thunk()

    def test_default_plan_snapshot_is_manager_metadata_and_no_action(self):
        plan = self.plan()
        self.assertEqual(plan.schema_version, TIER_B_MANAGER_RUN_PLAN_SCHEMA_VERSION)
        self.assertEqual(plan.plan_status, "manager_recorded_recommendation_not_authorization")
        self.assertEqual(plan.execution_path, "path_3")
        self.assertEqual(plan.profile_name, "quality_experiment")
        self.assertEqual(plan.service_mode, "in_process_local_adapter")
        self.assertEqual(plan.endpoint_mode, "no_http_endpoint")
        self.assertEqual(plan.precision, "bf16")
        self.assertEqual(plan.quantization, "none")
        self.assertEqual(plan.gpu_class, "RTX_5090_32GB_x1")
        self.assertTrue(plan.text_only)
        self.assertFalse(plan.thinking_enabled)
        self.assertFalse(plan.do_sample)
        self.assertFalse(plan.h1_allowed)
        self.assertFalse(plan.formal_quality_allowed)
        self.assertEqual(plan.tier_a_code_commit_sha, "a366ca22bc6b7c35d1818483c4d0f21148fd5735")
        self.assertNotIn("tier_a_code_sha256", plan.to_dict())
        for name in (
            "external_action_gate_eligible", "external_action_authorized",
            "external_egress_allowed", "model_downloaded", "model_loaded",
            "provider_invoked", "project_data_transferred", "real_credentials_used",
            "paid_resource_created", "run_occurred",
        ):
            self.assertFalse(getattr(plan, name), name)

    def test_plan_round_trip_identity_and_allowed_replacements_remain_no_action(self):
        plan = self.plan()
        self.assertEqual(TierBManagerRunPlan.from_dict(plan.to_dict()), plan)
        self.assertEqual(TierBManagerRunPlan.from_bytes(plan.canonical_bytes()), plan)
        self.assertEqual(validate_tier_b_manager_run_plan_bytes(plan.canonical_bytes()), plan)
        changed = self.plan(
            model_revision="1" * 40,
            runtime_transformers="5.14.2",
            base_image_identity="immutable_image_20260724a",
            max_fixtures=1,
            max_initial_calls=1,
            provider_retry_total=1,
            max_total_generation_calls=2,
            acquisition_hosts=["huggingface.co"],
        )
        self.assertNotEqual(changed.plan_id, plan.plan_id)
        self.assertNotEqual(changed.sha256(), plan.sha256())
        self.assertFalse(changed.external_action_gate_eligible)
        self.assertFalse(changed.external_action_authorized)

    def test_plan_requires_exact_list_containers_before_tuple_conversion(self):
        with self.assertRaises(TierBReadinessError):
            create_tier_b_manager_run_plan(overrides={"acquisition_hosts": ("huggingface.co",)})
        with self.assertRaises(TierBReadinessError):
            create_tier_b_manager_run_plan(overrides={"acquisition_hosts": {"huggingface.co": True}})

        original = self.plan().to_dict()
        for field in ("network_phases", "acquisition_hosts"):
            for invalid in ({"offline_execution": True}, ("offline_execution",), "offline_execution", b"offline_execution"):
                with self.subTest(field=field, invalid_type=type(invalid).__name__):
                    payload = deepcopy(original)
                    payload[field] = invalid
                    self.assert_rejected(lambda payload=payload: TierBManagerRunPlan.from_dict(payload))

        canonical_object = deepcopy(original)
        canonical_object["network_phases"] = {"offline_execution": True}
        reidentify_plan(canonical_object)
        canonical_raw = canonical(canonical_object)
        self.assertEqual(canonical(canonical_object), canonical_raw)
        self.assert_rejected(lambda: validate_tier_b_manager_run_plan_bytes(canonical_raw))

        legal_raw = self.plan().canonical_bytes()
        legal = validate_tier_b_manager_run_plan_bytes(legal_raw)
        self.assertEqual(legal.canonical_bytes(), legal_raw)
        self.assertIs(type(legal.to_dict()["network_phases"]), list)
        self.assertIs(type(legal.to_dict()["acquisition_hosts"]), list)

    def test_plan_rejects_every_structured_scalar_and_wrong_scalar_type(self):
        original = self.plan().to_dict()
        for key, value in original.items():
            with self.subTest(key=key):
                payload = deepcopy(original)
                if key == "plan_id":
                    payload[key] = {"nested": "forbidden"}
                elif type(value) is bool:
                    payload[key] = 0
                elif type(value) is int:
                    payload[key] = "1"
                else:
                    payload[key] = {"nested": "forbidden"}
                    reidentify_plan(payload)
                self.assert_rejected(lambda payload=payload: TierBManagerRunPlan.from_dict(payload))

    def test_plan_override_allowlist_rejects_boundary_and_leakage_attempts(self):
        rejected = (
            {"profile_name": "deterministic_fallback"},
            {"service_mode": "external_api"},
            {"endpoint_mode": "public_http"},
            {"precision": "int4"},
            {"quantization": "4bit"},
            {"text_only": False},
            {"thinking_enabled": True},
            {"do_sample": True},
            {"gpu_class": "other_gpu"},
            {"execution_path": "path_1"},
            {"h1_allowed": True},
            {"runtime_python": {"nested": "3.11"}},
            {"runtime_python": "external_api"},
            {"runtime_pytorch": "latest"},
            {"runtime_transformers": "api.openai.com"},
            {"base_image_identity": "https://example.invalid/image"},
            {"base_image_identity": "secret-token"},
            {"acquisition_hosts": ["api.openai.com"]},
            {"acquisition_hosts": ["huggingface.co", {"nested": "bad"}]},
            {"acquisition_hosts": []},
        )
        for overrides in rejected:
            with self.subTest(overrides=overrides):
                self.assert_rejected(lambda overrides=overrides: create_tier_b_manager_run_plan(overrides=overrides))

    def test_plan_cross_field_invariants_accept_one_consistent_tightening(self):
        tightened = self.plan(
            max_fixtures=1, max_initial_calls=1, provider_retry_per_case=1,
            provider_retry_total=1, max_total_generation_calls=2,
            call_timeout_seconds=90, cancel_grace_seconds=20, case_timeout_seconds=120,
            max_billable_minutes=300, max_total_spend_cny_fen=2500,
            max_model_download_bytes=20 * 1024**3,
            max_managed_disk_usage_gib=24, disk_free_gib=24,
        )
        tightened.validate()
        self.assertEqual(tightened.max_total_generation_calls, 2)

    def test_plan_cross_field_invariants_reject_inconsistent_relations(self):
        variants = (
            {"max_fixtures": 1},
            {"max_fixtures": 1, "max_initial_calls": 1, "provider_retry_per_case": 0, "provider_retry_total": 1, "max_total_generation_calls": 2},
            {"max_total_generation_calls": 3},
            {"call_timeout_seconds": 30, "cancel_grace_seconds": 30},
            {"call_timeout_seconds": 100, "cancel_grace_seconds": 30, "case_timeout_seconds": 120},
            {"max_total_spend_cny_fen": 100},
            {"max_managed_disk_usage_gib": 23},
            {"max_managed_disk_usage_gib": 65, "disk_free_gib": 64},
        )
        for overrides in variants:
            with self.subTest(overrides=overrides):
                self.assert_rejected(lambda overrides=overrides: self.plan(**overrides))

    def test_public_signatures_have_no_type_authority_or_variadic_injection(self):
        callables = (
            TierBManagerRunPlan.create,
            TierBManagerRunPlan.from_dict,
            TierBManagerRunPlan.from_bytes,
            TierBExternalActionBindingDeclaration.create,
            TierBExternalActionBindingDeclaration.from_dict,
            TierBExternalActionBindingDeclaration.from_bytes,
            create_tier_b_manager_run_plan,
            create_tier_b_external_action_binding_declaration,
            validate_tier_b_manager_run_plan_bytes,
            validate_tier_b_external_action_binding_declaration_bytes,
        )
        for target in callables:
            with self.subTest(target=target):
                parameters = inspect.signature(target).parameters.values()
                self.assertFalse(any(item.name.startswith("_") for item in parameters))
                self.assertFalse(any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters))
        with self.assertRaises(TypeError):
            create_tier_b_manager_run_plan(_plan_type=object)
        with self.assertRaises(TypeError):
            TierBManagerRunPlan.create(_authority=object)
        with self.assertRaises(TypeError):
            self.declaration(_plan_type=object)
        self.assertFalse(hasattr(TierBExternalActionBindingDeclaration, "validate_against"))

    def test_exact_captured_types_survive_public_name_rebinding_and_reject_subclasses(self):
        plan_type = TierBManagerRunPlan
        declaration_type = TierBExternalActionBindingDeclaration
        original_plan_name = module.TierBManagerRunPlan
        original_declaration_name = module.TierBExternalActionBindingDeclaration
        try:
            module.TierBManagerRunPlan = object
            module.TierBExternalActionBindingDeclaration = object
            self.assertIs(type(module.create_tier_b_manager_run_plan()), plan_type)
            self.assertIs(type(module.create_tier_b_external_action_binding_declaration(self.plan(), case_id="pilot-case-alpha", source_class="synthetic", **artifact_bindings())), declaration_type)
        finally:
            module.TierBManagerRunPlan = original_plan_name
            module.TierBExternalActionBindingDeclaration = original_declaration_name
        class PlanSubclass(TierBManagerRunPlan):
            pass
        class DeclarationSubclass(TierBExternalActionBindingDeclaration):
            pass
        self.assert_rejected(lambda: PlanSubclass.create())
        self.assert_rejected(lambda: DeclarationSubclass.create(self.plan(), case_id="pilot-case-alpha", source_class="synthetic", **artifact_bindings()))

    def test_declaration_has_complete_hash_only_inventory_and_non_authority_state(self):
        declaration = self.declaration()
        self.assertEqual(declaration.schema_version, TIER_B_EXTERNAL_ACTION_BINDING_DECLARATION_SCHEMA_VERSION)
        self.assertEqual(declaration.declaration_status, "syntactic_binding_declaration_complete_not_live_validated")
        keys = set(declaration.to_dict())
        for prefix in ARTIFACT_PREFIXES:
            self.assertTrue({f"{prefix}_id", f"{prefix}_sha256", f"{prefix}_byte_length"}.issubset(keys))
        for prefix in (
            "network_policy", "budget_policy", "credential_log_redaction_policy",
            "retention_cleanup_policy", "authorization_expiry_policy",
        ):
            self.assertTrue({f"{prefix}_id", f"{prefix}_sha256", f"{prefix}_byte_length"}.issubset(keys))
        self.assertNotIn("tier_a_code_sha256", keys)
        for name in (
            "external_action_gate_eligible", "external_action_authorized", "external_egress_allowed",
            "model_downloaded", "model_loaded", "provider_invoked", "project_data_transferred",
            "real_credentials_used", "paid_resource_created", "run_occurred", "h1_accessed",
            "formal_quality_claimed",
        ):
            self.assertFalse(getattr(declaration, name), name)

    def test_declaration_round_trip_and_standalone_validation_are_syntactic_only(self):
        declaration = self.declaration()
        self.assertEqual(TierBExternalActionBindingDeclaration.from_dict(declaration.to_dict()), declaration)
        self.assertEqual(TierBExternalActionBindingDeclaration.from_bytes(declaration.canonical_bytes()), declaration)
        self.assertEqual(validate_tier_b_external_action_binding_declaration_bytes(declaration.canonical_bytes()), declaration)
        changed = declaration.to_dict()
        changed["runtime_environment_image_record_sha256"] = "f" * 64
        reidentify_declaration(changed)
        syntactically_valid = TierBExternalActionBindingDeclaration.from_dict(changed)
        self.assertNotEqual(syntactically_valid.sha256(), declaration.sha256())
        self.assertFalse(syntactically_valid.external_action_gate_eligible)

    def test_policy_bindings_are_factory_derived_from_plan_not_caller_supplied(self):
        base = self.declaration()
        changed_network = self.declaration(self.plan(acquisition_hosts=["huggingface.co"]))
        self.assertNotEqual(base.network_policy_sha256, changed_network.network_policy_sha256)
        changed_budget = self.declaration(self.plan(max_billable_minutes=300))
        self.assertNotEqual(base.budget_policy_sha256, changed_budget.budget_policy_sha256)
        changed_retention = self.declaration(self.plan(local_archive_retention_days=20))
        self.assertNotEqual(base.retention_cleanup_policy_sha256, changed_retention.retention_cleanup_policy_sha256)
        changed_expiry = self.declaration(self.plan(authorization_term_days=5))
        self.assertNotEqual(base.authorization_expiry_policy_sha256, changed_expiry.authorization_expiry_policy_sha256)
        with self.assertRaises(TypeError):
            create_tier_b_external_action_binding_declaration(
                self.plan(), case_id="pilot-case-alpha", source_class="synthetic",
                network_policy_sha256="a" * 64, **artifact_bindings()
            )

    def test_declaration_rejects_wrong_types_unknown_missing_duplicate_nan_and_leakage(self):
        declaration = self.declaration()
        original = declaration.to_dict()
        for key, value in original.items():
            with self.subTest(key=key):
                payload = deepcopy(original)
                if key == "declaration_id":
                    payload[key] = {"nested": "bad"}
                elif type(value) is bool:
                    payload[key] = 0
                    reidentify_declaration(payload)
                elif type(value) is int:
                    payload[key] = "1"
                    reidentify_declaration(payload)
                else:
                    payload[key] = {"nested": "bad"}
                    reidentify_declaration(payload)
                self.assert_rejected(lambda payload=payload: TierBExternalActionBindingDeclaration.from_dict(payload))
        extra = deepcopy(original); extra["extra"] = True
        missing = deepcopy(original); del missing["selection_record_id"]
        self.assert_rejected(lambda: TierBExternalActionBindingDeclaration.from_dict(extra))
        self.assert_rejected(lambda: TierBExternalActionBindingDeclaration.from_dict(missing))
        raw = declaration.canonical_bytes()
        duplicate = raw[:-1] + b',"run_occurred":false}'
        self.assert_rejected(lambda: TierBExternalActionBindingDeclaration.from_bytes(duplicate))
        self.assert_rejected(lambda: TierBExternalActionBindingDeclaration.from_bytes(raw + b" "))
        self.assert_rejected(lambda: TierBExternalActionBindingDeclaration.from_bytes(raw.replace(b"100", b"NaN", 1)))
        leak = artifact_bindings(); leak["runtime_environment_image_record_id"] = "secret-token-location"
        self.assert_rejected(lambda: self.declaration(**leak))
        structured = artifact_bindings(); structured["selection_record_id"] = {"payload": "bad"}
        self.assert_rejected(lambda: self.declaration(**structured))

    def test_unresigned_tamper_fails_but_resigning_never_becomes_live_authority(self):
        declaration = self.declaration()
        payload = declaration.to_dict()
        payload["selection_record_sha256"] = "f" * 64
        self.assert_rejected(lambda: TierBExternalActionBindingDeclaration.from_dict(payload))
        reidentify_declaration(payload)
        syntactic = TierBExternalActionBindingDeclaration.from_dict(payload)
        self.assertFalse(syntactic.external_action_gate_eligible)
        self.assertFalse(syntactic.external_action_authorized)
        self.assertNotEqual(syntactic.sha256(), declaration.sha256())

    def test_module_has_no_external_action_or_live_validator_surface(self):
        exported_callables = {
            name for name, value in module.__dict__.items()
            if callable(value) and not name.startswith("_")
        }
        for forbidden in (
            "authorize", "invoke", "download", "backend", "callback", "endpoint_handle",
            "delete", "live_validator", "live_ready",
        ):
            self.assertFalse(any(forbidden in name.lower() for name in exported_callables), forbidden)


if __name__ == "__main__":
    unittest.main()
