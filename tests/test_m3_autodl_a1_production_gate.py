from __future__ import annotations

from copy import deepcopy
import gc
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime as runtime_package
import req2web_runtime.autodl_a1_executor as executor
import req2web_runtime.autodl_a1_linux_worker as worker
import req2web_runtime.autodl_a1_production_gate as gate
from tests import test_m3_autodl_a1_operational as operational_fixtures


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def identify(prefix, value, key):
    root = {name: item for name, item in value.items() if name != key}
    value[key] = prefix + hashlib.sha256(canonical(root)).hexdigest()[:20]


class A1ProductionGateTests(unittest.TestCase):
    def setUp(self):
        self.owner = operational_fixtures.RealA1OperationalTests(
            "test_valid_artifact_chain_is_local_and_non_manager_consumable"
        )
        self.owner.setUp()
        self.fixture = self.owner.worker_fixture()
        self.committed_validator = worker._make_committed_evidence_validator_for_tests(
            self.validate_private_model_root
        )
        root_bindings = self.root_bindings(
            self.fixture["control"],
            self.fixture["package"],
            self.fixture["model"],
            self.fixture["evidence"],
        )
        payload = executor._success_payload_for_tests(self.fixture["plan"])
        phases = (
            ("setup", "started"),
            ("setup", "completed"),
            ("load", "started"),
            ("load", "completed"),
            ("probe", "started"),
            ("probe", "completed"),
        )
        script = [
            {
                "delay_ms": 1,
                "raw": executor._channel_bytes_for_tests(
                    index,
                    phase,
                    transition,
                    payload if index == 6 else {},
                ),
            }
            for index, (phase, transition) in enumerate(phases, 1)
        ]
        self.observation, self.executor_receipt, _ = (
            executor._run_scripted_executor_for_tests(
                self.fixture["plan"],
                self.fixture["controls"],
                script,
                root_bindings=root_bindings,
            )
        )
        self.staging = executor._prepare_evidence_staging_root(self.fixture["evidence"])
        executor._write_executor_artifacts(
            self.staging,
            self.observation,
            self.executor_receipt,
        )
        self.validation_result = self.validation()
        self.evaluated_utc = "2026-07-25T00:00:02Z"
        self.validated_utc = "2026-07-25T00:00:03Z"
        self.report, self.gate_receipt, self.bundle = (
            gate._build_synthetic_gate_bundle_for_tests(
                self.validation_result,
                self.evaluated_utc,
            )
        )

    def tearDown(self):
        self.owner.tearDown()

    def validate_private_model_root(self, root, inventory):
        if inventory.data["inventory_mode"] != "production_pinned":
            raise worker.RealA1OperationalError("private_model_mode_invalid")
        resolved = Path(root).resolve(strict=True)
        entries = list(resolved.iterdir())
        if [item.name for item in entries] != ["model.bin"]:
            raise worker.RealA1OperationalError("private_model_set_invalid")
        raw = entries[0].read_bytes()
        if raw != b"model":
            raise worker.RealA1OperationalError("private_model_bytes_invalid")

    def root_bindings(self, control, package, model, evidence):
        markers = {
            row["role"]: row
            for row in self.fixture["controls"].data["root_markers"]
        }
        states = {
            "control": "existing_exact_control_inputs",
            "package": "existing_exact_package_manifest",
            "model": "existing_exact_model_inventory",
            "evidence": "dedicated_missing_no_write",
        }
        roots = {
            "control": Path(control).resolve(),
            "package": Path(package).resolve(),
            "model": Path(model).resolve(),
            "evidence": Path(evidence).resolve(),
        }
        return {
            role: {
                "role": role,
                "redacted_path": f"<{role}-root>",
                "normalized_path_sha256": hashlib.sha256(
                    os.path.normcase(str(roots[role])).encode("utf-8")
                ).hexdigest(),
                "marker_id": markers[role]["marker_id"],
                "marker_sha256": markers[role]["marker_sha256"],
                "expected_state": markers[role]["expected_state"],
                "observed_state": states[role],
            }
            for role in ("control", "package", "model", "evidence")
        }

    def validation(self, **overrides):
        plan_path = overrides.get("plan_path", self.fixture["plan_path"])
        controls_path = overrides.get("controls_path", self.fixture["controls_path"])
        package = overrides.get("package", self.fixture["package"])
        model = overrides.get("model", self.fixture["model"])
        evidence = overrides.get("evidence", self.fixture["evidence"])
        return self.committed_validator(
            str(plan_path),
            str(controls_path),
            str(package),
            str(model),
            str(evidence),
        )

    def rejected(self, action, code=None):
        errors = (
            gate.A1ProductionGateError,
            executor.RealA1ExecutorError,
            worker.RealA1OperationalError,
        )
        with self.assertRaises(errors) as raised:
            action()
        if code is not None:
            self.assertEqual(str(raised.exception), code)

    def validate_saved_bundle(self, bundle=None, validation=None, validated_utc=None):
        parsed = gate.A1ProductionGateBundle.from_bytes(
            (bundle or self.bundle).canonical_bytes()
        )
        report = parsed.report()
        receipt = parsed.receipt()
        return gate._validate_synthetic_gate_bundle_for_tests(
            report.canonical_bytes(),
            receipt.canonical_bytes(),
            validation or self.validation_result,
            validated_utc or self.validated_utc,
        )

    def test_exact_committed_evidence_bundle_is_authority_neutral(self):
        self.assertEqual(self.report.data["validation_mode"], "synthetic_test_only")
        self.assertEqual(
            self.report.data["authority_state"],
            "record_only_not_manager_authority",
        )
        self.assertFalse(self.report.data["manager_a1_passed"])
        self.assertFalse(self.report.data["a2_unlocked"])
        self.assertEqual(self.gate_receipt.data["status"], "synthetic_not_manager_consumable")
        self.assertFalse(self.gate_receipt.data["manager_a1_passed"])
        self.assertFalse(self.gate_receipt.data["a2_unlocked"])
        decision = self.validate_saved_bundle()
        self.assertEqual(decision.status, "synthetic_validated_not_manager_consumable")
        self.assertFalse(decision.manager_a1_passed)
        self.assertFalse(decision.a2_unlocked)
        actual_invariants = [
            {
                key: self.validation_result.root_bindings[role][key]
                for key in gate._ROOT_INVARIANT_KEYS
            }
            for role in ("control", "package", "model", "evidence")
        ]
        self.assertEqual(self.observation.data["root_invariants"], actual_invariants)
        self.assertEqual(
            gate.A1ProductionGateReport.from_bytes(
                self.report.canonical_bytes()
            ).to_dict(),
            self.report.to_dict(),
        )
        self.assertEqual(
            gate.A1ProductionGateReceipt.from_bytes(
                self.gate_receipt.canonical_bytes()
            ).to_dict(),
            self.gate_receipt.to_dict(),
        )

    def test_parsers_and_direct_constructors_never_create_manager_authority(self):
        production_report = deepcopy(self.report.data)
        production_report.update(
            {
                "validation_mode": "production_linux",
                "status": "production_evidence_replayed_record_only",
            }
        )
        identify(
            "real-a1-production-gate-report-",
            production_report,
            "report_id",
        )
        parsed_report = gate.A1ProductionGateReport.from_bytes(
            canonical(production_report)
        )
        self.assertFalse(parsed_report.data["manager_a1_passed"])
        self.assertFalse(parsed_report.data["a2_unlocked"])

        production_receipt = deepcopy(self.gate_receipt.data)
        production_receipt.update(
            {
                "report_id": parsed_report.data["report_id"],
                "report_sha256": parsed_report.sha256(),
                "status": "production_bundle_recorded_not_manager_authority",
                "next_state": "requires_owning_bundle_replay",
            }
        )
        identify(
            "real-a1-production-gate-receipt-",
            production_receipt,
            "gate_receipt_id",
        )
        parsed_receipt = gate.A1ProductionGateReceipt.from_bytes(
            canonical(production_receipt)
        )
        parsed_receipt.validate_against(parsed_report)
        self.assertFalse(parsed_receipt.data["manager_a1_passed"])
        self.assertFalse(parsed_receipt.data["a2_unlocked"])

        forged_report = deepcopy(production_report)
        forged_report["manager_a1_passed"] = True
        forged_report["a2_unlocked"] = True
        identify("real-a1-production-gate-report-", forged_report, "report_id")
        self.rejected(
            lambda: gate.A1ProductionGateReport.from_bytes(canonical(forged_report)),
            "gate_report_authority_invalid",
        )
        self.rejected(
            lambda: gate.A1ProductionGateReport(forged_report).canonical_bytes(),
            "gate_report_authority_invalid",
        )

        forged_receipt = deepcopy(production_receipt)
        forged_receipt["manager_a1_passed"] = True
        forged_receipt["a2_unlocked"] = True
        identify(
            "real-a1-production-gate-receipt-",
            forged_receipt,
            "gate_receipt_id",
        )
        self.rejected(
            lambda: gate.A1ProductionGateReceipt.from_bytes(
                canonical(forged_receipt)
            ),
            "gate_receipt_authority_invalid",
        )
        self.rejected(
            lambda: gate.A1ProductionGateReceipt(forged_receipt).canonical_bytes(),
            "gate_receipt_authority_invalid",
        )
        with self.assertRaises(TypeError):
            gate.ValidatedA1ManagerDecision()
        forged_decision = object.__new__(gate.ValidatedA1ManagerDecision)
        self.assertFalse(hasattr(forged_decision, "_token"))
        self.assertFalse(hasattr(forged_decision, "_data"))
        for name, value in (("_token", object()), ("_data", {"manager_a1_passed": True})):
            with self.subTest(authority_slot=name):
                with self.assertRaises(AttributeError):
                    object.__setattr__(forged_decision, name, value)
        with self.assertRaises(TypeError):
            _ = forged_decision.manager_a1_passed
        with self.assertRaises(TypeError):
            gate.A1ProductionGateResult(
                parsed_report,
                parsed_receipt,
                self.bundle,
                forged_decision,
                0,
            )
        self.assertFalse(hasattr(gate.ValidatedA1ManagerDecision, "from_dict"))
        self.assertFalse(hasattr(gate.ValidatedA1ManagerDecision, "from_bytes"))

    def test_decision_provenance_registry_blocks_token_theft_and_cross_bundle_forgery(self):
        decision_a = self.validate_saved_bundle()
        report_b, receipt_b, bundle_b = gate._build_synthetic_gate_bundle_for_tests(
            self.validation_result,
            "2026-07-25T00:00:03Z",
        )
        decision_b = gate._validate_synthetic_gate_bundle_for_tests(
            report_b.canonical_bytes(),
            receipt_b.canonical_bytes(),
            self.validation_result,
            "2026-07-25T00:00:04Z",
        )

        self.assertNotIsInstance(decision_a, gate.ValidatedA1ManagerDecision)
        self.assertIs(type(decision_a), type(decision_b))
        self.assertFalse(hasattr(decision_a, "_token"))
        self.assertFalse(hasattr(decision_a, "_data"))
        for name, value in (
            ("_token", object()),
            ("_data", {"manager_a1_passed": True}),
            ("manager_a1_passed", True),
        ):
            with self.subTest(replace=name):
                with self.assertRaises(AttributeError):
                    object.__setattr__(decision_a, name, value)
        self.assertFalse(decision_a.manager_a1_passed)
        self.assertFalse(decision_a.a2_unlocked)

        self.assertTrue(
            gate._validate_synthetic_decision_provenance_for_tests(
                decision_a,
                self.report,
                self.gate_receipt,
                self.bundle,
            )
        )
        with self.assertRaisesRegex(
            TypeError,
            "synthetic_decision_bundle_binding_invalid",
        ):
            gate._validate_synthetic_decision_provenance_for_tests(
                decision_a,
                report_b,
                receipt_b,
                bundle_b,
            )

        copied_provenance = {
            "report_id": decision_a.report_id,
            "report_sha256": self.report.sha256(),
            "gate_receipt_id": decision_a.gate_receipt_id,
            "gate_receipt_sha256": self.gate_receipt.sha256(),
            "bundle_id": decision_a.bundle_id,
            "bundle_sha256": self.bundle.sha256(),
        }
        with self.assertRaisesRegex(TypeError, "synthetic_gate_decision_required"):
            gate._validate_synthetic_decision_provenance_for_tests(
                copied_provenance,
                self.report,
                self.gate_receipt,
                self.bundle,
            )
        unregistered_synthetic = object.__new__(type(decision_a))
        with self.assertRaisesRegex(
            TypeError,
            "synthetic_gate_decision_not_registered",
        ):
            _ = unregistered_synthetic.report_id
        with self.assertRaisesRegex(
            TypeError,
            "synthetic_gate_decision_not_registered",
        ):
            gate._validate_synthetic_decision_provenance_for_tests(
                unregistered_synthetic,
                self.report,
                self.gate_receipt,
                self.bundle,
            )

        unregistered_manager = object.__new__(gate.ValidatedA1ManagerDecision)
        with self.assertRaisesRegex(
            TypeError,
            "validated_manager_decision_not_registered",
        ):
            _ = unregistered_manager.manager_a1_passed
        unregistered_result = object.__new__(gate.A1ProductionGateResult)
        with self.assertRaisesRegex(TypeError, "production_gate_result_not_registered"):
            _ = unregistered_result.decision
        with self.assertRaises(AttributeError):
            object.__setattr__(unregistered_result, "decision", decision_a)
        with self.assertRaisesRegex(TypeError, "production_gate_result_factory_required"):
            gate.A1ProductionGateResult(
                self.report,
                self.gate_receipt,
                self.bundle,
                decision_a,
                0,
            )
        self.assertEqual(gate._decision_registry_counts_for_tests()["manager"], 0)

        gc.collect()
        before = gate._decision_registry_counts_for_tests()
        temporary = self.validate_saved_bundle()
        temporary_ref = weakref.ref(temporary)
        during = gate._decision_registry_counts_for_tests()
        self.assertEqual(during["manager"], before["manager"])
        self.assertEqual(during["synthetic"], before["synthetic"] + 1)
        del temporary
        gc.collect()
        self.assertIsNone(temporary_ref())
        self.assertEqual(gate._decision_registry_counts_for_tests(), before)

    def test_identity_registries_reject_equal_hash_subclass_collisions(self):
        decision = self.validate_saved_bundle()
        result = gate._make_synthetic_gate_result_for_tests(
            self.report,
            self.gate_receipt,
            self.bundle,
            decision,
        )

        class EqualSynthetic(type(decision)):
            __slots__ = ()

            def __eq__(self, other):
                return True

            def __hash__(self):
                return hash(decision)

        synthetic_collision = object.__new__(EqualSynthetic)
        self.assertEqual(synthetic_collision, decision)
        self.assertEqual(hash(synthetic_collision), hash(decision))
        with self.assertRaisesRegex(
            TypeError,
            "synthetic_gate_decision_not_registered",
        ):
            _ = synthetic_collision.report_id
        with self.assertRaisesRegex(TypeError, "synthetic_gate_decision_required"):
            gate._make_synthetic_gate_result_for_tests(
                self.report,
                self.gate_receipt,
                self.bundle,
                synthetic_collision,
            )

        manager_base = object.__new__(gate.ValidatedA1ManagerDecision)

        class EqualManager(gate.ValidatedA1ManagerDecision):
            __slots__ = ()

            def __eq__(self, other):
                return True

            def __hash__(self):
                return hash(manager_base)

        manager_collision = object.__new__(EqualManager)
        self.assertEqual(manager_collision, manager_base)
        self.assertEqual(hash(manager_collision), hash(manager_base))
        with self.assertRaisesRegex(
            TypeError,
            "validated_manager_decision_not_registered",
        ):
            _ = manager_collision.manager_a1_passed

        class EqualResult(gate.A1ProductionGateResult):
            __slots__ = ()

            def __eq__(self, other):
                return True

            def __hash__(self):
                return hash(result)

        result_collision = object.__new__(EqualResult)
        self.assertEqual(result_collision, result)
        self.assertEqual(hash(result_collision), hash(result))
        with self.assertRaisesRegex(
            TypeError,
            "production_gate_result_not_registered",
        ):
            _ = result_collision.report

    def test_result_registry_replays_immutable_artifact_and_decision_snapshots(self):
        decision_a = self.validate_saved_bundle()
        result = gate._make_synthetic_gate_result_for_tests(
            self.report,
            self.gate_receipt,
            self.bundle,
            decision_a,
        )
        expected_report = self.report.canonical_bytes()
        expected_receipt = self.gate_receipt.canonical_bytes()
        expected_bundle = self.bundle.canonical_bytes()

        returned_report = result.report
        returned_receipt = result.receipt
        returned_bundle = result.bundle
        returned_report.data.clear()
        returned_receipt.data["status"] = "forged"
        returned_bundle.data["report"]["status"] = "forged"

        self.assertEqual(result.report.canonical_bytes(), expected_report)
        self.assertEqual(result.receipt.canonical_bytes(), expected_receipt)
        self.assertEqual(result.bundle.canonical_bytes(), expected_bundle)
        self.assertIs(result.decision, decision_a)
        self.assertEqual(result.return_code, 0)

        report_b, receipt_b, bundle_b = gate._build_synthetic_gate_bundle_for_tests(
            self.validation_result,
            "2026-07-25T00:00:03Z",
        )
        with self.assertRaisesRegex(
            TypeError,
            "synthetic_decision_bundle_binding_invalid",
        ):
            gate._make_synthetic_gate_result_for_tests(
                report_b,
                receipt_b,
                bundle_b,
                decision_a,
            )

        copied_fields = {
            "report_id": decision_a.report_id,
            "report_sha256": self.report.sha256(),
            "gate_receipt_id": decision_a.gate_receipt_id,
            "gate_receipt_sha256": self.gate_receipt.sha256(),
            "bundle_id": decision_a.bundle_id,
            "bundle_sha256": self.bundle.sha256(),
        }
        with self.assertRaisesRegex(TypeError, "synthetic_gate_decision_required"):
            gate._make_synthetic_gate_result_for_tests(
                self.report,
                self.gate_receipt,
                self.bundle,
                copied_fields,
            )

        gc.collect()
        before = gate._decision_registry_counts_for_tests()
        temporary_decision = self.validate_saved_bundle()
        temporary_result = gate._make_synthetic_gate_result_for_tests(
            self.report,
            self.gate_receipt,
            self.bundle,
            temporary_decision,
        )
        decision_ref = weakref.ref(temporary_decision)
        result_ref = weakref.ref(temporary_result)
        during = gate._decision_registry_counts_for_tests()
        self.assertEqual(during["synthetic"], before["synthetic"] + 1)
        self.assertEqual(during["results"], before["results"] + 1)
        del temporary_decision
        gc.collect()
        self.assertIsNotNone(decision_ref())
        del temporary_result
        gc.collect()
        self.assertIsNone(result_ref())
        self.assertIsNone(decision_ref())
        self.assertEqual(gate._decision_registry_counts_for_tests(), before)

    def test_only_saved_cli_envelope_is_sufficient_for_owning_replay(self):
        saved_stdout = self.bundle.canonical_bytes()
        loaded = gate.A1ProductionGateBundle.from_bytes(saved_stdout)
        decision = gate._validate_synthetic_gate_bundle_for_tests(
            loaded.report().canonical_bytes(),
            loaded.receipt().canonical_bytes(),
            self.validation_result,
            self.validated_utc,
        )
        self.assertEqual(loaded.canonical_bytes(), saved_stdout)
        self.assertFalse(decision.manager_a1_passed)
        self.assertFalse(decision.a2_unlocked)

    def test_authorization_window_is_replayed_and_live_expiry_is_enforced(self):
        self.rejected(
            lambda: gate._build_synthetic_gate_bundle_for_tests(
                self.validation_result,
                "2026-07-24T23:59:59Z",
            ),
            "gate_authorization_window_invalid",
        )
        self.rejected(
            lambda: gate._build_synthetic_gate_bundle_for_tests(
                self.validation_result,
                "2026-08-01T00:00:01Z",
            ),
            "gate_authorization_window_invalid",
        )
        self.rejected(
            lambda: self.validate_saved_bundle(
                validated_utc="2026-08-01T00:00:01Z"
            ),
            "gate_authorization_window_invalid",
        )

        forged_report = deepcopy(self.report.data)
        forged_report["gate_evaluated_utc"] = "2026-08-01T00:00:01Z"
        identify("real-a1-production-gate-report-", forged_report, "report_id")
        forged_receipt = deepcopy(self.gate_receipt.data)
        forged_receipt["report_id"] = forged_report["report_id"]
        forged_receipt["report_sha256"] = hashlib.sha256(
            canonical(forged_report)
        ).hexdigest()
        identify(
            "real-a1-production-gate-receipt-",
            forged_receipt,
            "gate_receipt_id",
        )
        self.rejected(
            lambda: gate._validate_synthetic_gate_bundle_for_tests(
                canonical(forged_report),
                canonical(forged_receipt),
                self.validation_result,
                "2026-08-01T00:00:01Z",
            ),
            "gate_authorization_window_invalid",
        )

    def test_all_four_sibling_root_substitutions_are_rejected(self):
        cases = []
        control_copy = self.fixture["base"] / "control-copy"
        shutil.copytree(self.fixture["control"], control_copy)
        cases.append(
            (
                "control",
                {
                    "plan_path": control_copy / self.fixture["plan_path"].name,
                    "controls_path": control_copy / self.fixture["controls_path"].name,
                },
            )
        )
        for role in ("package", "model", "evidence"):
            copied_root = self.fixture["base"] / f"{role}-copy"
            shutil.copytree(self.fixture[role], copied_root)
            cases.append((role, {role: copied_root}))

        for role, overrides in cases:
            with self.subTest(role=role):
                substituted = self.validation(**overrides)
                self.rejected(
                    lambda substituted=substituted: (
                        gate._validate_synthetic_gate_bundle_for_tests(
                            self.report.canonical_bytes(),
                            self.gate_receipt.canonical_bytes(),
                            substituted,
                            self.validated_utc,
                        )
                    ),
                    "gate_root_identity_invalid",
                )

    def test_preparation_contract_still_rejects_committed_nonempty_evidence(self):
        self.validation()
        self.rejected(
            lambda: worker._validate_preparation_roots_linux(
                str(self.fixture["plan_path"]),
                str(self.fixture["controls_path"]),
                str(self.fixture["package"]),
                str(self.fixture["model"]),
                str(self.fixture["evidence"]),
            ),
            "worker_evidence_root_not_empty",
        )

    def test_committed_validator_rejects_missing_extra_and_nonregular(self):
        marker_path = self.staging / executor.EVIDENCE_COMMIT_FILENAME
        marker_bytes = marker_path.read_bytes()
        marker_path.unlink()
        self.rejected(
            self.validation,
            "committed_worker_evidence_regular_file_set_invalid",
        )
        marker_path.write_bytes(marker_bytes)
        extra = self.staging / "extra.json"
        extra.write_bytes(b"{}")
        self.rejected(
            self.validation,
            "committed_worker_evidence_regular_file_set_invalid",
        )
        extra.unlink()
        marker_path.unlink()
        marker_path.mkdir()
        self.rejected(
            self.validation,
            "committed_worker_evidence_regular_file_set_invalid",
        )

    def test_gate_rejects_noncanonical_and_reidentified_marker_drift(self):
        observation_path = self.staging / executor.OBSERVATION_FILENAME
        observation_path.write_bytes(observation_path.read_bytes() + b"\n")
        self.rejected(
            lambda: gate._build_synthetic_gate_bundle_for_tests(
                self.validation_result,
                self.evaluated_utc,
            ),
            "noncanonical_json",
        )
        observation_path.write_bytes(self.observation.canonical_bytes())
        marker_path = self.staging / executor.EVIDENCE_COMMIT_FILENAME
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["files"][0]["sha256"] = "0" * 64
        marker["tree_sha256"] = hashlib.sha256(
            canonical(marker["files"])
        ).hexdigest()
        identify("real-a1-evidence-commit-", marker, "commit_id")
        marker_path.write_bytes(canonical(marker))
        self.rejected(
            lambda: gate._build_synthetic_gate_bundle_for_tests(
                self.validation_result,
                self.evaluated_utc,
            ),
            "evidence_commit_replay_invalid",
        )

    def test_committed_validator_is_closed_over_all_worker_authorities(self):
        saved_validator = self.committed_validator
        expected = saved_validator(
            str(self.fixture["plan_path"]),
            str(self.fixture["controls_path"]),
            str(self.fixture["package"]),
            str(self.fixture["model"]),
            str(self.fixture["evidence"]),
        )
        error_type = worker.RealA1OperationalError
        calls = []

        def rebound(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("rebound worker authority used")

        patch_values = {
            "Mapping": rebound,
            "Path": rebound,
            "os": rebound,
            "stat": rebound,
            "hashlib": rebound,
            "A1ModelInventory": rebound,
            "A1OperationalControls": rebound,
            "A1OperationalPlan": rebound,
            "A1RootMarker": rebound,
            "AutoDLA1DeploymentPackageManifest": rebound,
            "AutoDLA1PreflightPlan": rebound,
            "AutoDLA1PreflightError": rebound,
            "RealA1OperationalError": rebound,
            "A1CommittedEvidenceRootValidation": rebound,
            "_PLATFORM": "rebound",
            "_ROLES": ("rebound",),
            "_EXECUTOR_STAGING_DIRECTORY": "rebound",
            "_COMMITTED_EVIDENCE_FILES": ("rebound",),
            "_resolve_existing": rebound,
            "_scan_tree": rebound,
            "_validate_topology": rebound,
            "_root_markers": rebound,
            "_root_binding": rebound,
            "_require_production_inventory": rebound,
            "_validate_exact_tree": rebound,
            "_validate_model_root": rebound,
        }
        with patch.multiple(worker, **patch_values):
            again = saved_validator(
                str(self.fixture["plan_path"]),
                str(self.fixture["controls_path"]),
                str(self.fixture["package"]),
                str(self.fixture["model"]),
                str(self.fixture["evidence"]),
            )
            self.assertEqual(again.plan.canonical_bytes(), expected.plan.canonical_bytes())
            self.assertEqual(
                again.controls.canonical_bytes(),
                expected.controls.canonical_bytes(),
            )
            forged = self.staging / "forged.json"
            forged.write_bytes(b"{}")
            with self.assertRaises(error_type) as raised:
                saved_validator(
                    str(self.fixture["plan_path"]),
                    str(self.fixture["controls_path"]),
                    str(self.fixture["package"]),
                    str(self.fixture["model"]),
                    str(self.fixture["evidence"]),
                )
            self.assertEqual(
                str(raised.exception),
                "committed_worker_evidence_regular_file_set_invalid",
            )
            forged.unlink()
        self.assertEqual(calls, [])

    def test_gate_definition_time_rebinding_keeps_bundle_replay_stable(self):
        saved_build = gate._build_synthetic_gate_bundle_for_tests
        saved_validate = gate._validate_synthetic_gate_bundle_for_tests
        saved_bundle_type = gate.A1ProductionGateBundle
        expected = self.bundle.canonical_bytes()
        calls = []

        def rebound(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("rebound gate authority used")

        with patch.object(gate, "type", rebound, create=True), patch.object(
            gate,
            "id",
            rebound,
            create=True,
        ), patch.multiple(
            gate,
            Mapping=rebound,
            MappingProxyType=rebound,
            Path=rebound,
            json=rebound,
            hashlib=rebound,
            stat=rebound,
            datetime=rebound,
            timezone=rebound,
            weakref=rebound,
            validate_committed_evidence_roots=rebound,
            A1CommittedEvidenceRootValidation=rebound,
            A1ProductionPhaseEvent=rebound,
            A1ProductionObservation=rebound,
            A1ExecutionReceipt=rebound,
            A1EvidenceCommitMarker=rebound,
            A1ProductionGateError=rebound,
            GATE_REPORT_SCHEMA="rebound",
            GATE_RECEIPT_SCHEMA="rebound",
            GATE_BUNDLE_SCHEMA="rebound",
            _GATE_REPORT_KEYS=("rebound",),
            _GATE_RECEIPT_KEYS=("rebound",),
            _GATE_BUNDLE_KEYS=("rebound",),
            _ROOT_INVARIANT_KEYS=("rebound",),
            _EVIDENCE_FILES=("rebound",),
            _MAX_FILE_BYTES=rebound,
            WORKER_AUTHORITY="rebound",
            WORKER_VERSION="rebound",
        ):
            report, receipt, bundle = saved_build(
                self.validation_result,
                self.evaluated_utc,
            )
            self.assertEqual(bundle.canonical_bytes(), expected)
            parsed = saved_bundle_type.from_bytes(expected)
            decision = saved_validate(
                parsed.report().canonical_bytes(),
                parsed.receipt().canonical_bytes(),
                self.validation_result,
                self.validated_utc,
            )
            self.assertFalse(decision.manager_a1_passed)
            self.assertFalse(decision.a2_unlocked)
            self.assertEqual(report.canonical_bytes(), self.report.canonical_bytes())
            self.assertEqual(
                receipt.canonical_bytes(),
                self.gate_receipt.canonical_bytes(),
            )
        self.assertEqual(calls, [])

    def test_public_boundaries_are_narrow_linux_only_and_not_root_exported(self):
        self.assertFalse(hasattr(runtime_package, "run_a1_production_gate"))
        self.assertNotIn("run_a1_production_gate", runtime_package.__all__)
        self.assertFalse(
            hasattr(runtime_package, "validate_production_gate_bundle_bytes")
        )
        self.assertEqual(
            tuple(inspect.signature(gate.run_a1_production_gate).parameters),
            ("plan_path", "controls_path", "package_root", "model_root", "evidence_root"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    gate.validate_production_gate_bundle_bytes
                ).parameters
            ),
            (
                "report_bytes",
                "receipt_bytes",
                "plan_path",
                "controls_path",
                "package_root",
                "model_root",
                "evidence_root",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(worker.validate_committed_evidence_roots).parameters),
            ("plan_file", "controls_file", "package_root", "model_root", "evidence_root"),
        )
        for artifact in (
            gate.A1ProductionGateReport,
            gate.A1ProductionGateReceipt,
            gate.A1ProductionGateBundle,
        ):
            for name in (
                "from_dict",
                "from_bytes",
                "validate",
                "to_dict",
                "canonical_bytes",
                "sha256",
            ):
                self.assertFalse(
                    any(
                        item.startswith("_")
                        for item in inspect.signature(getattr(artifact, name)).parameters
                    )
                )
        with self.assertRaises(TypeError):
            gate.validate_production_gate_bundle_bytes(
                b"{}",
                b"{}",
                "p",
                "c",
                "package",
                "model",
                "evidence",
                clock=lambda: None,
            )
        with self.assertRaises(TypeError):
            gate.A1ProductionGateReport.from_bytes(
                self.report.canonical_bytes(),
                _validator=lambda value: value,
            )
        if sys.platform != "linux":
            self.rejected(
                lambda: gate.run_a1_production_gate(
                    "missing", "missing", "missing", "missing", "missing"
                ),
                "real_a1_production_gate_linux_required",
            )
            self.rejected(
                lambda: gate.validate_production_gate_bundle_bytes(
                    b"{}",
                    b"{}",
                    "missing",
                    "missing",
                    "missing",
                    "missing",
                    "missing",
                ),
                "real_a1_production_gate_linux_required",
            )

    def test_static_cli_outputs_full_canonical_bundle_without_overrides(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "req2web_runtime"
            / "autodl_a1_production_gate.py"
        ).read_text(encoding="utf-8")
        cli = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "run_stage3_autodl_a1_production_gate.py"
        ).read_text(encoding="utf-8")
        self.assertIn("validate_production_gate_bundle_bytes", source)
        self.assertIn("ValidatedA1ManagerDecision", source)
        self.assertIn("weakref.ref", source)
        self.assertIn("exact_type = type", source)
        self.assertIn("identity_of = id", source)
        self.assertNotIn("weakref.WeakKeyDictionary", source)
        self.assertIn("manager_decision_registry", source)
        self.assertIn("synthetic_decision_registry", source)
        self.assertNotIn("manager_token = object()", source)
        self.assertNotIn("__slots__ = (\"_token\", \"_data\")", source)
        self.assertIn("--evaluate-production-a1", cli)
        self.assertIn("sys.stdout.buffer.write(result.bundle.canonical_bytes())", cli)
        self.assertNotIn("result.receipt.canonical_bytes()", cli)
        for forbidden in (
            "--backend", "--callback", "--observer", "--expected", "--pass",
            "--platform", "--executable", "--command", "--module", "--clock",
        ):
            self.assertNotIn(forbidden, cli)


    def test_private_owner_snapshot_accessor_is_immutable_and_descriptor_independent(self):
        decision = self.validate_saved_bundle()
        result = gate._make_synthetic_gate_result_for_tests(
            self.report,
            self.gate_receipt,
            self.bundle,
            decision,
        )
        metadata = gate._read_a1_production_gate_result_owning_snapshot()
        snapshot = gate._read_a1_production_gate_result_owning_snapshot(result)
        self.assertEqual(
            metadata["authority_schema"],
            "req2web.runtime.real_a1_production_gate_result_owning_snapshot.v1",
        )
        self.assertEqual(snapshot["authority_generation"], metadata["authority_generation"])
        self.assertEqual(snapshot["gate_report_id"], self.report.data["report_id"])
        self.assertEqual(
            snapshot["gate_report_sha256"],
            hashlib.sha256(snapshot["gate_report_canonical_bytes"]).hexdigest(),
        )
        self.assertFalse(snapshot["manager_authority"])
        self.assertFalse(snapshot["decision_manager_a1_passed"])
        self.assertFalse(snapshot["decision_a2_unlocked"])
        self.assertEqual(snapshot["return_code"], 0)
        self.assertEqual(len(snapshot["snapshot_sha256"]), 64)
        with self.assertRaises(TypeError):
            snapshot["manager_authority"] = True
        self.assertFalse(
            hasattr(runtime_package, "_read_a1_production_gate_result_owning_snapshot")
        )

        forged = object()
        targets = []
        for cls in (
            gate.A1ProductionGateResult,
            gate.ValidatedA1ManagerDecision,
            gate._SyntheticA1GateDecision,
        ):
            for name, descriptor in vars(cls).items():
                if isinstance(descriptor, property):
                    targets.append((cls, name, descriptor))
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
        for cls, names in method_names.items():
            for name in names:
                targets.append((cls, name, vars(cls)[name]))
        try:
            for cls, name, descriptor in targets:
                if isinstance(descriptor, property):
                    replacement = property(lambda self, value=forged: value)
                elif isinstance(descriptor, classmethod):
                    replacement = classmethod(
                        lambda cls, *args, value=forged, **kwargs: value
                    )
                else:
                    replacement = lambda *args, value=forged, **kwargs: value
                setattr(cls, name, replacement)
            replay = gate._read_a1_production_gate_result_owning_snapshot(result)
            self.assertEqual(replay, snapshot)
        finally:
            for cls, name, descriptor in reversed(targets):
                setattr(cls, name, descriptor)

    def test_private_owner_snapshot_generation_drift_fails_closed(self):
        decision = self.validate_saved_bundle()
        result = gate._make_synthetic_gate_result_for_tests(
            self.report,
            self.gate_receipt,
            self.bundle,
            decision,
        )
        generation = gate._A1_PRODUCTION_GATE_OWNER_GENERATION
        try:
            gate._A1_PRODUCTION_GATE_OWNER_GENERATION = "reloaded-owner-generation"
            with self.assertRaisesRegex(
                gate.A1ProductionGateError,
                "owner_generation_drift_restart_and_rebuild_required",
            ):
                gate._read_a1_production_gate_result_owning_snapshot(result)
        finally:
            gate._A1_PRODUCTION_GATE_OWNER_GENERATION = generation
        replay = gate._read_a1_production_gate_result_owning_snapshot(result)
        self.assertEqual(replay["authority_generation"], generation)



if __name__ == "__main__":
    unittest.main()
