from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_orchestration import tier_b_live_readiness as module  # noqa: E402
import req2web_orchestration.model_route as model_route_module  # noqa: E402
from req2web_orchestration.model_route import TierA07bOneRepairOrchestrator  # noqa: E402
from req2web_runtime.tier_b_readiness import (  # noqa: E402
    TierBExternalActionBindingDeclaration,
    create_tier_b_external_action_binding_declaration,
    create_tier_b_manager_run_plan,
)
import test_m3_real_run_bundle as _real_bundle_fixtures  # noqa: E402
import test_m3_local_qwen_provider as _qwen_provider_fixtures  # noqa: E402


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def resign_declaration(payload: dict[str, object]) -> None:
    root = {key: value for key, value in payload.items() if key != "declaration_id"}
    payload["declaration_id"] = (
        "tier-b-binding-declaration-" + hashlib.sha256(canonical(root)).hexdigest()[:20]
    )


class TierBLiveReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _real_bundle_fixtures.TierA08RealRunBundleTests("test_happy_path_round_trip_and_live_replay")
        self.fixture.setUp()
        self.route_inputs = self.fixture.route_args("tier-b-live-readiness")
        self.gate = TierA07bOneRepairOrchestrator().run(**self.route_inputs)
        self.bundle = self.fixture.bundle(self.gate)
        self.plan = create_tier_b_manager_run_plan()
        self.chain = self._chain(self._syntactic_placeholder_declaration())
        bindings, _ = module._bindings(self.chain)
        values: dict[str, object] = {}
        by_name = {binding.name: binding for binding in bindings}
        for name in module._ARTIFACT_NAMES:
            binding = by_name[name]
            values[f"{name}_id"] = binding.identity
            values[f"{name}_sha256"] = binding.sha256
            values[f"{name}_byte_length"] = binding.byte_length
        declaration = create_tier_b_external_action_binding_declaration(
            self.plan,
            case_id=self.fixture.case_id,
            source_class="synthetic",
            **values,
        )
        self.chain = self._chain(declaration)

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _syntactic_placeholder_declaration(self):
        values: dict[str, object] = {}
        for index, name in enumerate(module._ARTIFACT_NAMES):
            values[f"{name}_id"] = f"artifact-alpha-{index}"
            values[f"{name}_sha256"] = f"{index:x}" * 64
            values[f"{name}_byte_length"] = 100 + index
        return create_tier_b_external_action_binding_declaration(
            self.plan,
            case_id=self.fixture.case_id,
            source_class="synthetic",
            **values,
        )

    def _chain(self, declaration):
        return module.TierBLocalReadinessArtifactChain(
            context=self.fixture.context,
            guidance=self.fixture.guidance,
            manifest=self.fixture.manifest,
            provider_visible_input=self.fixture.selected.provider_visible_input,
            selection_record=self.fixture.selected.selection_record,
            selected_input=self.fixture.selected,
            input_view_artifact=self.fixture.request.input_view_artifact,
            prompt_artifact=self.fixture.request.prompt_artifact,
            config_artifact=self.fixture.request.config_artifact,
            local_request=self.fixture.request,
            pre_invocation_audit=self.fixture.audit,
            preparation_record=self.fixture.preparation,
            tier_a_08_profile=self.fixture.profile,
            route_outcome=self.fixture.model_outcome,
            gate_delivery_outcome=self.gate,
            tier_a_08_bundle=self.bundle,
            frozen_g0_reference=self.fixture.reference,
            frozen_g0_package=self.fixture.package,
            scripted_local_fixture=self.fixture.fixture,
            fallback_record=self.fixture.fallback_record,
            fallback_snapshot_dir=self.fixture.snapshot,
            render_output_dir=self.route_inputs["render_output_dir"],
            model_package_output_dir=self.route_inputs["model_package_output_dir"],
            fallback_output_dir=self.route_inputs["fallback_output_dir"],
            scripted_acceptance_fixture=self.route_inputs["scripted_acceptance_fixture"],
            field_gate_report=self.fixture.receipt,
            repair_patch=model_route_module.TierA07bRepairPatch.from_bytes(self.route_inputs["repair_patch"]),
            manager_run_plan=self.plan,
            declaration=declaration,
        )

    def assert_rejected(self, chain) -> None:
        with self.assertRaises(module.TierBLocalReadinessValidationError):
            module.validate_tier_b_local_artifact_chain(chain)

    def test_complete_actual_chain_recomputes_all_bindings_and_round_trips(self) -> None:
        record = module.validate_tier_b_local_artifact_chain(self.chain)
        self.assertEqual(record.validation_status, "local_artifact_chain_validated_no_action")
        self.assertEqual(tuple(binding.name for binding in record.bindings), module._BINDING_NAMES)
        self.assertEqual(
            module.TierBLocalReadinessValidationRecord.from_bytes(record.canonical_bytes()),
            record,
        )
        for name in module._NO_ACTION_FLAGS:
            self.assertIs(getattr(record, name), False)
        planned = {binding.name: binding for binding in record.bindings}
        self.assertTrue(planned["model_artifact_integrity_manifest"].identity.startswith("tier-b-model-artifact-integrity-manifest-"))
        self.assertTrue(planned["runtime_environment_image_record"].identity.startswith("tier-b-runtime-environment-image-record-"))
        self.assertEqual(planned["tier_a_08_profile"].identity, self.fixture.profile.profile_id)
        self.assertEqual(planned["route_outcome"].identity, self.fixture.model_outcome.outcome_id)
        self.assertEqual(planned["gate_delivery_outcome"].identity, self.gate.outcome_id)
        self.assertEqual(planned["live_verified_g0_package"].identity, self.fixture.package.package_id)

    def test_owning_route_and_gate_replays_are_called(self) -> None:
        route_verifier = module._CAPTURED_ROUTE_LIVE_VERIFIER
        gate_verifier = module._CAPTURED_GATE_READ_ONLY_VERIFIER
        with patch.object(module, "_CAPTURED_ROUTE_LIVE_VERIFIER", wraps=route_verifier) as route_spy, patch.object(
            module, "_CAPTURED_GATE_READ_ONLY_VERIFIER", wraps=gate_verifier
        ) as gate_spy:
            record = module.validate_tier_b_local_artifact_chain(self.chain)
        self.assertIs(type(record), module._CAPTURED_RECORD_TYPE)
        self.assertEqual(route_spy.call_count, 1)
        self.assertEqual(gate_spy.call_count, 1)
        self.assertIs(route_spy.call_args.kwargs["scripted_local_fixture"], self.fixture.fixture)
        self.assertIs(gate_spy.call_args.kwargs["field_gate_report"], self.fixture.receipt)

    def _replay_directory_inventory(self) -> tuple[tuple[str, str, int], ...]:
        root = self.fixture.work
        rows = []
        for item in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
            if item.is_file():
                raw = item.read_bytes()
                rows.append((
                    item.relative_to(root).as_posix(),
                    hashlib.sha256(raw).hexdigest(), len(raw),
                ))
        return tuple(rows)

    def test_acceptance_read_only_replay_passes_without_directory_writes(self) -> None:
        before = self._replay_directory_inventory()
        record = module.validate_tier_b_local_artifact_chain(self.chain)
        after = self._replay_directory_inventory()
        self.assertEqual(record.validation_status, "local_artifact_chain_validated_no_action")
        self.assertEqual(after, before)

    def test_read_only_gate_replay_has_no_filesystem_mutation_capability(self) -> None:
        before = self._replay_directory_inventory()
        path_type = type(self.fixture.work)

        def forbidden(*args, **kwargs):
            raise AssertionError("read-only verifier attempted a filesystem mutation")

        with ExitStack() as stack:
            for name in ("mkdir", "write_bytes", "write_text", "replace", "unlink", "rmdir"):
                stack.enter_context(patch.object(path_type, name, side_effect=forbidden))
            stack.enter_context(patch.object(os, "replace", side_effect=forbidden))
            for name in ("copy", "copy2", "copyfile", "copytree", "rmtree"):
                stack.enter_context(patch.object(shutil, name, side_effect=forbidden))
            stack.enter_context(patch.object(model_route_module, "_stdlib_rmtree", side_effect=forbidden))
            record = module.validate_tier_b_local_artifact_chain(self.chain)
        self.assertEqual(record.validation_status, "local_artifact_chain_validated_no_action")
        self.assertEqual(self._replay_directory_inventory(), before)

    def test_read_only_package_content_drift_fails_closed(self) -> None:
        target = self.route_inputs["model_package_output_dir"] / "page" / "app.js"
        target.write_text("tampered model package\n", encoding="utf-8")
        self.assert_rejected(self.chain)

    def test_acceptance_fixture_fail_or_unknown_cannot_replace_passed_gate(self) -> None:
        for fixture_key in (
            model_route_module.SCRIPTED_ACCEPTANCE_FAIL_KEY,
            model_route_module.SCRIPTED_ACCEPTANCE_UNKNOWN_KEY,
        ):
            with self.subTest(fixture_key=fixture_key):
                replacement = model_route_module.ScriptedAcceptanceFixture.create(fixture_key)
                self.assert_rejected(
                    replace(self.chain, scripted_acceptance_fixture=replacement)
                )

    def test_resigned_gate_and_bundle_case_attack_fails_owning_read_only_replay(self) -> None:
        forged_case = "case-tier-a-08-forged"
        gate_payload = deepcopy(self.gate.to_dict())
        gate_payload["case_id"] = forged_case
        gate_payload["fallback_binding"]["case_id"] = forged_case
        gate_root = {key: value for key, value in gate_payload.items() if key != "outcome_id"}
        gate_payload["outcome_id"] = model_route_module._07B_OUTCOME_ID_PREFIX + model_route_module._07b_hash(gate_root)[:20]
        forged_gate = model_route_module.TierA07bGateDeliveryOutcome.from_dict(gate_payload)

        bundle_payload = deepcopy(self.bundle.to_dict())
        bundle_payload["gate_delivery_binding"]["a07b_outcome"] = forged_gate.to_dict()
        bundle_payload["gate_delivery_binding"]["outcome_id"] = forged_gate.outcome_id
        bundle_payload["gate_delivery_binding"]["outcome_sha256"] = hashlib.sha256(
            forged_gate.canonical_bytes()
        ).hexdigest()
        bundle_root = {key: value for key, value in bundle_payload.items() if key != "bundle_id"}
        bundle_payload["bundle_id"] = model_route_module._08_ID_PREFIX + model_route_module._sha256(
            canonical(bundle_root)
        )[:20]
        forged_bundle = model_route_module.TierA08RealRunBundlePlaceholder.from_dict(bundle_payload)

        declaration_payload = self.chain.declaration.to_dict()
        declaration_payload["case_id"] = forged_case
        resign_declaration(declaration_payload)
        forged_declaration = TierBExternalActionBindingDeclaration.from_dict(declaration_payload)
        self.assert_rejected(
            replace(
                self.chain,
                gate_delivery_outcome=forged_gate,
                tier_a_08_bundle=forged_bundle,
                declaration=forged_declaration,
            )
        )

    def test_public_name_rebinding_cannot_replace_captured_record_or_parser_types(self) -> None:
        record_type = module.TierBLocalReadinessValidationRecord
        binding_type = module.TierBLocalReadinessBinding

        class ReboundRecord:
            pass

        class ReboundChain:
            pass

        class ReboundBinding:
            pass

        with patch.object(module, "TierBLocalReadinessValidationRecord", ReboundRecord), patch.object(
            module, "TierBLocalReadinessArtifactChain", ReboundChain
        ), patch.object(module, "TierBLocalReadinessBinding", ReboundBinding):
            record = module.validate_tier_b_local_artifact_chain(self.chain)
            replayed = module.validate_tier_b_local_readiness_validation_record_bytes(
                record.canonical_bytes()
            )
        self.assertIs(type(record), record_type)
        self.assertIs(type(replayed), record_type)
        self.assertTrue(all(type(item) is binding_type for item in replayed.bindings))

    def test_lazy_exports_have_fixed_order_across_hash_seeds(self) -> None:
        command = "import req2web_orchestration as module; print(repr(module.__all__))"
        outputs = []
        for seed in ("1", "777"):
            environment = dict(os.environ)
            environment["PYTHONHASHSEED"] = seed
            environment["PYTHONPATH"] = str(ROOT / "src")
            completed = subprocess.run(
                [sys.executable, "-c", command], cwd=ROOT, env=environment,
                text=True, capture_output=True, check=True,
            )
            outputs.append(completed.stdout)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(tuple(module.__all__[-7:]), (
            "TIER_B_LOCAL_READINESS_VALIDATION_RECORD_SCHEMA_VERSION",
            "TierBLocalReadinessArtifactChain",
            "TierBLocalReadinessBinding",
            "TierBLocalReadinessValidationError",
            "TierBLocalReadinessValidationRecord",
            "validate_tier_b_local_artifact_chain",
            "validate_tier_b_local_readiness_validation_record_bytes",
        ))

    def test_resigned_declaration_never_becomes_authority(self) -> None:
        payload = self.chain.declaration.to_dict()
        payload["selection_record_sha256"] = "f" * 64
        resign_declaration(payload)
        forged = TierBExternalActionBindingDeclaration.from_dict(payload)
        self.assert_rejected(replace(self.chain, declaration=forged))

    def test_cross_case_plan_and_policy_drift_fail_closed(self) -> None:
        wrong_case_payload = self.chain.declaration.to_dict()
        wrong_case_payload["case_id"] = "other-case-beta"
        resign_declaration(wrong_case_payload)
        self.assert_rejected(replace(self.chain, declaration=TierBExternalActionBindingDeclaration.from_dict(wrong_case_payload)))
        changed_plan = create_tier_b_manager_run_plan(overrides={"acquisition_hosts": ["huggingface.co"]})
        self.assert_rejected(replace(self.chain, manager_run_plan=changed_plan))

    def test_same_chain_request_and_prompt_mismatches_fail_closed(self) -> None:
        other_context, _, _, other_request, _, _ = _qwen_provider_fixtures.build_chain()
        self.assert_rejected(replace(self.chain, context=other_context))
        self.assert_rejected(replace(self.chain, prompt_artifact=other_request.prompt_artifact))
        self.assert_rejected(replace(self.chain, local_request=other_request))

    def test_model_runtime_binding_and_exact_type_mismatches_fail_closed(self) -> None:
        payload = self.chain.declaration.to_dict()
        payload["model_artifact_integrity_manifest_sha256"] = "e" * 64
        resign_declaration(payload)
        self.assert_rejected(replace(self.chain, declaration=TierBExternalActionBindingDeclaration.from_dict(payload)))

        class ContextSubclass(type(self.chain.context)):
            pass

        injected = ContextSubclass(**self.chain.context.__dict__)
        self.assert_rejected(replace(self.chain, context=injected))

    def test_g0_live_drift_and_record_noncanonical_forms_fail_closed(self) -> None:
        record = module.validate_tier_b_local_artifact_chain(self.chain)
        target = self.fixture.package.package_dir / "page" / "app.js"
        target.write_text("tampered\n", encoding="utf-8")
        self.assert_rejected(self.chain)
        self.assert_rejected_record(record.canonical_bytes() + b" ")
        duplicate = record.canonical_bytes()[:-1] + b',"run_occurred":false}'
        self.assert_rejected_record(duplicate)
        self.assert_rejected_record(b'{"unexpected":NaN}')

    def assert_rejected_record(self, raw: bytes) -> None:
        with self.assertRaises(module.TierBLocalReadinessValidationError):
            module.validate_tier_b_local_readiness_validation_record_bytes(raw)

    def test_public_surface_has_no_action_or_injection_hook(self) -> None:
        self.assertEqual(tuple(inspect.signature(module.validate_tier_b_local_artifact_chain).parameters), ("artifacts",))
        exported = {name: getattr(module, name) for name in module.__all__}
        for forbidden in ("authorize", "invoke", "download", "backend", "callback", "endpoint", "network", "credential", "transfer", "paid_resource", "delete"):
            self.assertFalse(any(forbidden in name.lower() for name in exported), forbidden)
        from req2web_orchestration import validate_tier_b_local_artifact_chain
        self.assertIs(validate_tier_b_local_artifact_chain, module.validate_tier_b_local_artifact_chain)


if __name__ == "__main__":
    unittest.main()
