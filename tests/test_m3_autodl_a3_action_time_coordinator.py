from __future__ import annotations

from copy import deepcopy
import importlib
import inspect
from pathlib import Path
from types import MappingProxyType
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a1_production_gate as gate
import req2web_runtime.autodl_action_time_authority as authority
import req2web_runtime.autodl_a3_action_time_coordinator as coordinator
import req2web_runtime.autodl_a3_handoff_protocol as protocol
import req2web_runtime.autodl_a3_authenticated_executor_contract as executor_contract
from tests.test_m3_autodl_action_time_authority import make_fixture


class A3ActionTimeCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner, cls.gate_result, cls.bindings = make_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.owner.tearDown()

    def prepare(self, bindings=None):
        return coordinator.prepare_a3_action_time_handoff_no_action(
            self.gate_result,
            "a1_failure_cleanup",
            deepcopy(bindings or self.bindings),
        )

    def test_result_registry_replays_immutable_component_snapshots(self):
        result = self.prepare()
        request_a = result.request
        capsule_a = result.capsule
        contract_a = result.executor_contract
        mutable = request_a.to_dict()
        mutable["endpoint"]["instance_id_hash"] = "f" * 64
        self.assertNotEqual(mutable, result.request.to_dict())
        self.assertEqual(request_a.canonical_bytes(), result.request.canonical_bytes())
        self.assertEqual(capsule_a.canonical_bytes(), result.capsule.canonical_bytes())
        self.assertEqual(contract_a.canonical_bytes(), result.executor_contract.canonical_bytes())
        self.assertEqual(result.status, "a3_trusted_handoff_structural_readiness_no_action")
        self.assertFalse(result.permit_issued)
        self.assertFalse(result.destructive_action_authorized)

    def test_unregistered_and_equal_subclass_results_are_rejected(self):
        unregistered = object.__new__(coordinator.A3ActionTimeCoordinatorResult)
        with self.assertRaisesRegex(TypeError, "a3_coordinator_result_not_registered"):
            _ = unregistered.request

        valid = self.prepare()

        class EqualResult(coordinator.A3ActionTimeCoordinatorResult):
            __slots__ = ()
            def __eq__(self, other):
                return True
            def __hash__(self):
                return hash(valid)

        collision = object.__new__(EqualResult)
        with self.assertRaisesRegex(TypeError, "a3_coordinator_result_exact_type_required"):
            _ = collision.request
        with self.assertRaisesRegex(TypeError, "a3_coordinator_result_factory_required"):
            coordinator.A3ActionTimeCoordinatorResult()

    def test_binding_set_is_exact_and_gate_plan_controls_are_cross_bound(self):
        missing = deepcopy(self.bindings)
        missing.pop("executor_package")
        with self.assertRaisesRegex(coordinator.A3ActionTimeCoordinatorError, "a3_coordinator_binding_exact_keys_invalid"):
            self.prepare(missing)

        extra = deepcopy(self.bindings)
        extra["permit"] = {"authorized": True}
        with self.assertRaisesRegex(coordinator.A3ActionTimeCoordinatorError, "a3_coordinator_binding_exact_keys_invalid"):
            self.prepare(extra)

        wrong_plan = deepcopy(self.bindings)
        wrong_plan["operational_plan"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(coordinator.A3ActionTimeCoordinatorError, "a3_coordinator_operational_plan_gate_binding_invalid"):
            self.prepare(wrong_plan)

        wrong_controls = deepcopy(self.bindings)
        wrong_controls["controls"]["id"] = "different-controls-test"
        with self.assertRaisesRegex(coordinator.A3ActionTimeCoordinatorError, "a3_coordinator_controls_gate_binding_invalid"):
            self.prepare(wrong_controls)

    def test_definition_time_captures_survive_module_rebinding(self):
        saved_handoff = coordinator.handoff_protocol
        saved_authority = coordinator.action_authority
        saved_executor = coordinator.executor_contract_module
        saved_gate = coordinator.a1_gate
        try:
            coordinator.handoff_protocol = object()
            coordinator.action_authority = object()
            coordinator.executor_contract_module = object()
            coordinator.a1_gate = object()
            result = self.prepare()
            self.assertIs(type(result.capsule), protocol.A3HandoffCapsuleTemplate)
            self.assertFalse(result.capsule.data["permit_issued"])
        finally:
            coordinator.handoff_protocol = saved_handoff
            coordinator.action_authority = saved_authority
            coordinator.executor_contract_module = saved_executor
            coordinator.a1_gate = saved_gate

    def test_grouped_class_descriptor_rebinding_preserves_owning_authority(self):
        before = self.prepare()
        request_bytes = authority._action_time_request_canonical_for_trust(
            before.request
        )
        capsule_bytes = protocol._a3_handoff_capsule_canonical_for_trust(
            before.capsule
        )
        contract_bytes = (
            executor_contract._a3_authenticated_executor_contract_canonical_for_trust(
                before.executor_contract
            )
        )
        forged = object()
        originals = {}

        def replace(cls, name, descriptor):
            originals[(cls, name)] = vars(cls)[name]
            setattr(cls, name, descriptor)

        gate_method_names = {
            gate.A1ProductionGateReport: (
                "from_dict", "from_bytes", "validate", "to_dict",
                "canonical_bytes", "sha256",
            ),
            gate.A1ProductionGateReceipt: (
                "from_dict", "from_bytes", "validate", "to_dict",
                "canonical_bytes", "sha256", "validate_against",
            ),
            gate.A1ProductionGateBundle: (
                "from_dict", "from_bytes", "validate", "to_dict",
                "canonical_bytes", "sha256", "report", "receipt",
            ),
        }
        try:
            for name, descriptor in vars(gate.A1ProductionGateResult).items():
                if isinstance(descriptor, property):
                    replace(
                        gate.A1ProductionGateResult,
                        name,
                        property(lambda self, value=forged: value),
                    )
            for decision_cls in (
                gate.ValidatedA1ManagerDecision,
                gate._SyntheticA1GateDecision,
            ):
                for name, descriptor in vars(decision_cls).items():
                    if isinstance(descriptor, property):
                        replace(
                            decision_cls,
                            name,
                            property(lambda self, value=forged: value),
                        )
            for cls, names in gate_method_names.items():
                for name in names:
                    descriptor = vars(cls)[name]
                    replacement = (
                        classmethod(lambda cls, *args, value=forged, **kwargs: value)
                        if isinstance(descriptor, classmethod)
                        else (lambda *args, value=forged, **kwargs: value)
                    )
                    replace(cls, name, replacement)

            artifact_classes = (
                authority.ActionTimeAuthorityRequest,
                protocol.A3HandoffCapsuleTemplate,
                executor_contract.A3AuthenticatedExecutorContract,
            )
            for cls in artifact_classes:
                for name in ("from_bytes", "data", "to_dict", "canonical_bytes", "sha256"):
                    descriptor = vars(cls)[name]
                    if isinstance(descriptor, property):
                        replacement = property(
                            lambda self, value={"forged": True}: value
                        )
                    elif isinstance(descriptor, classmethod):
                        replacement = classmethod(
                            lambda cls, *args, value=forged, **kwargs: value
                        )
                    else:
                        replacement = lambda *args, value=forged, **kwargs: value
                    replace(cls, name, replacement)

            after = self.prepare()
            self.assertFalse(after.permit_issued)
            self.assertEqual(
                authority.validate_action_time_authority_request_bytes(
                    request_bytes
                ).__class__,
                authority.ActionTimeAuthorityRequest,
            )
            self.assertEqual(
                protocol.validate_a3_handoff_capsule_template_bytes(
                    capsule_bytes
                ).__class__,
                protocol.A3HandoffCapsuleTemplate,
            )
            self.assertEqual(
                executor_contract.validate_a3_authenticated_executor_contract_bytes(
                    contract_bytes
                ).__class__,
                executor_contract.A3AuthenticatedExecutorContract,
            )

            forged_request = object.__new__(authority.ActionTimeAuthorityRequest)
            with self.assertRaisesRegex(TypeError, "action_time_request_not_registered"):
                protocol.create_a3_handoff_capsule_template(forged_request)
            forged_capsule = object.__new__(protocol.A3HandoffCapsuleTemplate)
            with self.assertRaisesRegex(TypeError, "a3_handoff_capsule_not_registered"):
                executor_contract.create_a3_authenticated_executor_contract(
                    before.request,
                    forged_capsule,
                )
            forged_contract = object.__new__(
                executor_contract.A3AuthenticatedExecutorContract
            )
            with self.assertRaisesRegex(TypeError, "a3_executor_contract_not_registered"):
                executor_contract._a3_authenticated_executor_contract_canonical_for_trust(
                    forged_contract
                )
        finally:
            for (cls, name), descriptor in reversed(tuple(originals.items())):
                setattr(cls, name, descriptor)

    def test_owner_accessor_unavailable_malformed_and_generation_mismatch_fail_closed(self):
        original = gate._read_a1_production_gate_result_owning_snapshot
        metadata = original()
        cases = []
        cases.append(
            (
                None,
                "gate_owner_accessor_unavailable_restart_and_rebuild_required",
            )
        )
        cases.append(
            (
                lambda *args: MappingProxyType({"forged": True}),
                "gate_owner_metadata_invalid_restart_and_rebuild_required",
            )
        )

        def malformed_snapshot(*args):
            if not args:
                return metadata
            return MappingProxyType({"forged": True})

        cases.append((malformed_snapshot, "gate_owner_snapshot_shape_invalid"))

        def mismatched_generation(*args):
            value = original(*args)
            if not args:
                return value
            changed = dict(value)
            changed["authority_generation"] = "forged-owner-generation"
            return MappingProxyType(changed)

        cases.append((mismatched_generation, "gate_owner_generation_drift"))
        try:
            for replacement, code in cases:
                gate._read_a1_production_gate_result_owning_snapshot = replacement
                authorities = coordinator._build_authorities()
                prepare = authorities[
                    "prepare_a3_action_time_handoff_no_action"
                ]
                with self.assertRaisesRegex(
                    coordinator.A3ActionTimeCoordinatorError,
                    code,
                ):
                    prepare(
                        self.gate_result,
                        "a1_failure_cleanup",
                        deepcopy(self.bindings),
                    )
        finally:
            gate._read_a1_production_gate_result_owning_snapshot = original

    def test_descriptor_rebinding_before_coordinator_build_uses_owner_accessor(self):
        forged = object()
        originals = []
        targets = (
            gate.A1ProductionGateResult,
            gate.ValidatedA1ManagerDecision,
            gate._SyntheticA1GateDecision,
        )
        method_names = {
            gate.A1ProductionGateReport: (
                "from_dict", "from_bytes", "validate", "to_dict",
                "canonical_bytes", "sha256",
            ),
            gate.A1ProductionGateReceipt: (
                "from_dict", "from_bytes", "validate", "to_dict",
                "canonical_bytes", "sha256", "validate_against",
            ),
            gate.A1ProductionGateBundle: (
                "from_dict", "from_bytes", "validate", "to_dict",
                "canonical_bytes", "sha256", "report", "receipt",
            ),
        }
        try:
            for cls in targets:
                for name, descriptor in vars(cls).items():
                    if isinstance(descriptor, property):
                        originals.append((cls, name, descriptor))
                        setattr(
                            cls,
                            name,
                            property(lambda self, value=forged: value),
                        )
            for cls, names in method_names.items():
                for name in names:
                    descriptor = vars(cls)[name]
                    originals.append((cls, name, descriptor))
                    replacement = (
                        classmethod(
                            lambda cls, *args, value=forged, **kwargs: value
                        )
                        if isinstance(descriptor, classmethod)
                        else (lambda *args, value=forged, **kwargs: value)
                    )
                    setattr(cls, name, replacement)
            authorities = coordinator._build_authorities()
            prepare = authorities["prepare_a3_action_time_handoff_no_action"]
            try:
                result = prepare(
                    self.gate_result,
                    "a1_failure_cleanup",
                    deepcopy(self.bindings),
                )
            except KeyError as exc:
                self.fail(f"owner accessor build leaked KeyError: {exc}")
            self.assertFalse(result.permit_issued)
        finally:
            for cls, name, descriptor in reversed(originals):
                setattr(cls, name, descriptor)

    def test_coordinator_source_uses_owner_accessor_without_reflection_or_gate_rule_copy(self):
        source = Path(coordinator.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "__closure__",
            "co_freevars",
            "vars(",
            "expected_gate_receipt",
            "validate_registered_provenance",
            "production_bundle_recorded_not_manager_authority",
            "requires_owning_bundle_replay",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn(
            "_read_a1_production_gate_result_owning_snapshot",
            source,
        )

    def test_zz_owner_reload_and_stale_results_require_restart_and_rebuild(self):
        saved_gate_namespace = dict(gate.__dict__)
        saved_coordinator_namespace = dict(coordinator.__dict__)
        old_prepare = coordinator.prepare_a3_action_time_handoff_no_action
        old_result = self.gate_result
        fresh_owner = None

        def restore_module(module, saved):
            for key in tuple(module.__dict__):
                if key not in saved:
                    del module.__dict__[key]
            module.__dict__.update(saved)

        try:
            importlib.reload(gate)
            with self.assertRaisesRegex(
                saved_coordinator_namespace["A3ActionTimeCoordinatorError"],
                "restart_and_rebuild_required",
            ):
                old_prepare(
                    old_result,
                    "a1_failure_cleanup",
                    deepcopy(self.bindings),
                )

            fresh_owner, fresh_result, fresh_bindings = make_fixture()
            with self.assertRaisesRegex(
                saved_coordinator_namespace["A3ActionTimeCoordinatorError"],
                "restart_and_rebuild_required",
            ):
                old_prepare(
                    fresh_result,
                    "a1_failure_cleanup",
                    deepcopy(fresh_bindings),
                )

            importlib.reload(coordinator)
            with self.assertRaisesRegex(
                coordinator.A3ActionTimeCoordinatorError,
                "restart_and_rebuild_required",
            ):
                coordinator.prepare_a3_action_time_handoff_no_action(
                    old_result,
                    "a1_failure_cleanup",
                    deepcopy(self.bindings),
                )
            fresh = coordinator.prepare_a3_action_time_handoff_no_action(
                fresh_result,
                "a1_failure_cleanup",
                deepcopy(fresh_bindings),
            )
            self.assertFalse(fresh.permit_issued)
        finally:
            if fresh_owner is not None:
                fresh_owner.tearDown()
            restore_module(gate, saved_gate_namespace)
            restore_module(coordinator, saved_coordinator_namespace)

    def test_public_coordinator_has_no_authority_override_parameters(self):
        signature = inspect.signature(coordinator.prepare_a3_action_time_handoff_no_action)
        self.assertEqual(tuple(signature.parameters), ("result", "trigger_kind", "structural_bindings"))
        forbidden = {
            "signer", "verifier", "backend", "callback", "command", "platform",
            "clock", "secret", "key", "approval_file", "permit_file",
        }
        self.assertTrue(forbidden.isdisjoint(signature.parameters))


if __name__ == "__main__":
    unittest.main()
