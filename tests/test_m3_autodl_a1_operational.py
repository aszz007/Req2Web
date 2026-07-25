from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime as runtime_package
import req2web_runtime.autodl_a1_linux_worker as worker
import req2web_runtime.autodl_a1_operational as op
import req2web_runtime.autodl_a1_preflight as pre


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def identify(prefix, value, key):
    value[key] = prefix + hashlib.sha256(canonical({name: item for name, item in value.items() if name != key})).hexdigest()[:20]


def copied(value):
    return json.loads(canonical(value).decode("utf-8"))


def file_row(relative_path, content):
    return {"relative_path": relative_path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


class RealA1OperationalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / f"req2web_a1_operational_{uuid.uuid4().hex}"
        self.root.mkdir()
        self.fixture_index = 0

    def tearDown(self):
        shutil.rmtree(self.root)

    def rejected(self, action, code=None):
        with self.assertRaises(op.RealA1OperationalError) as raised:
            action()
        if code is not None:
            self.assertEqual(str(raised.exception), code)

    def synthetic_inventory_payload(self, rows=None):
        rows = rows or [file_row("model.bin", b"model")]
        return op._build_synthetic_model_inventory_for_tests(rows)

    def validate_private_test_model_root(self, root, _inventory):
        worker._validate_test_inventory_root(root, [file_row("model.bin", b"model")])

    def base_no_action(self, package_rows=None):
        package_rows = package_rows or [{"path": "runtime.py", "bytes": len(b"package"), "sha256": hashlib.sha256(b"package").hexdigest()}]
        manifest = {
            "schema_version": pre.AUTODL_A1_PACKAGE_MANIFEST_SCHEMA_VERSION,
            "source_commit_sha": "c" * 40,
            "files": package_rows,
        }
        identify("autodl-a1-package-", manifest, "manifest_id")
        parsed_manifest = pre.AutoDLA1DeploymentPackageManifest.from_dict(manifest)
        disposition = {
            "schema_version": pre.AUTODL_A1_APPROVED_DISPOSITION_SCHEMA_VERSION,
            "disposition": "approved",
            "approval_record_sha256": "a" * 64,
            "approval_timestamp_utc": "2026-07-25T00:00:00Z",
            "authorization_expiry_utc": "2026-08-01T00:00:00Z",
            "action_time_git_sha": "c" * 40,
            "d17_gate_request_sha256": pre.D17_GATE_REQUEST_SHA256,
            "d17_decision_set_sha256": pre.D17_DECISION_SET_SHA256,
            "d17_field_policy_sha256": pre.D17_FIELD_POLICY_SHA256,
            "manager_run_plan_sha256": pre._manager_plan_factory().sha256(),
            "local_smoke_identity_sha256": pre.LOCAL_SMOKE_REPORT_SHA256,
            "deployment_package_manifest_sha256": parsed_manifest.sha256(),
        }
        identify("autodl-a1-disposition-", disposition, "disposition_id")
        return pre.AutoDLA1PreflightPlan.create(pre.AutoDLA1ApprovedDisposition.from_dict(disposition), parsed_manifest)

    def parts(self):
        inventory = op.A1ModelInventory.production()
        plan = op.A1OperationalPlan.create(self.base_no_action(), inventory)
        controls = op.A1OperationalControls.create(plan, "1" * 64, 2.78, "2" * 64, "2026-07-25T00:00:00Z", ["3" * 64, "4" * 64, "5" * 64, "6" * 64])
        observation = op.SyntheticA1DryRunAuthority.observe(plan)
        receipt = op.A1OperationalReceipt.create(plan, controls, observation)
        gate = op.A1IndependentGate.evaluate(plan, controls, receipt)
        intent = op.A1CloseoutIntent.create(plan, receipt)
        closeout = op.A1CloseoutReceipt.create(intent)
        return inventory, plan, controls, observation, receipt, gate, intent, closeout

    def write_tree(self, root, rows, path_key, contents):
        root.mkdir(parents=True)
        for row in rows:
            relative = row[path_key]
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents[relative])

    def worker_fixture(self):
        self.fixture_index += 1
        base = self.root / f"fixture-{self.fixture_index}"
        control_root = base / "control"
        package_root = base / "package"
        model_root = base / "model"
        evidence_root = base / "evidence"
        package_content = {"runtime.py": b"package"}
        model_content = {"model.bin": b"model"}
        package_rows = [{"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()} for name, content in sorted(package_content.items())]
        model_rows = [file_row(name, content) for name, content in sorted(model_content.items())]
        base_plan = self.base_no_action(package_rows)
        inventory = op.A1ModelInventory.production()
        plan = op.A1OperationalPlan.create(base_plan, inventory)
        controls = op.A1OperationalControls.create(plan, "1" * 64, 2.78, "2" * 64, "2026-07-25T00:00:00Z", ["3" * 64, "4" * 64, "5" * 64, "6" * 64])
        control_root.mkdir(parents=True)
        plan_path = control_root / "plan.json"
        controls_path = control_root / "controls.json"
        plan_path.write_bytes(plan.canonical_bytes())
        controls_path.write_bytes(controls.canonical_bytes())
        self.write_tree(package_root, package_rows, "path", package_content)
        self.write_tree(model_root, model_rows, "relative_path", model_content)
        return {"base": base, "control": control_root, "plan_path": plan_path, "controls_path": controls_path, "package": package_root, "model": model_root, "evidence": evidence_root, "plan": plan, "controls": controls}

    def test_valid_artifact_chain_is_local_and_non_manager_consumable(self):
        inventory, plan, controls, observation, receipt, gate, intent, closeout = self.parts()
        self.assertEqual(inventory.data["inventory_mode"], "production_pinned")
        self.assertEqual([item["role"] for item in controls.data["root_markers"]], ["control", "package", "model", "evidence"])
        receipt.validate_against(plan, controls, observation)
        self.assertEqual(gate.data["status"], "synthetic_pass_not_manager_consumable")
        self.assertFalse(gate.data["manager_consumable"])
        self.assertEqual(closeout.data["status"], "cleanup_not_executed")
        self.assertEqual(op.A1CloseoutReceipt.incomplete(intent).data["status"], "cleanup_incomplete")

    def test_public_plan_and_worker_reject_reidentified_synthetic_inventory(self):
        synthetic = self.synthetic_inventory_payload()
        self.rejected(lambda: op.A1ModelInventory.from_dict(synthetic), "model_inventory_mode_invalid")
        direct = op.A1ModelInventory(synthetic)
        self.rejected(lambda: op.A1OperationalPlan.create(self.base_no_action(), direct), "model_inventory_mode_invalid")

        _, plan, _, _, _, _, _, _ = self.parts()
        forged = copied(plan.data)
        forged["model_inventory"] = copied(synthetic)
        forged["model_inventory_sha256"] = hashlib.sha256(canonical(synthetic)).hexdigest()
        forged["model"] = {
            "repository": synthetic["repository"],
            "revision": synthetic["revision"],
            "file_count": synthetic["file_count"],
            "total_bytes": synthetic["total_bytes"],
            "inventory_tree_sha256": synthetic["tree_sha256"],
            "max_download_bytes": 22548578304,
            "max_managed_bytes": 65 * 1024**3,
        }
        identify("real-a1-plan-", forged, "plan_id")
        self.rejected(lambda: op.A1OperationalPlan.from_bytes(canonical(forged)), "model_inventory_mode_invalid")

        fixture = self.worker_fixture()
        fixture["plan_path"].write_bytes(canonical(forged))
        argv = ["--plan", str(fixture["plan_path"]), "--controls", str(fixture["controls_path"]), "--package-root", str(fixture["package"]), "--model-root", str(fixture["model"]), "--evidence-root", str(fixture["evidence"])]
        with patch.object(worker, "_PLATFORM", "linux"):
            self.rejected(lambda: worker.main(argv), "worker_model_inventory_mode_invalid")

        class ForgedPlan:
            data = forged
            plan_id = forged["plan_id"]

        class BypassPlanType:
            @classmethod
            def from_bytes(cls, _raw):
                return ForgedPlan()

        fixture = self.worker_fixture()
        bypass_argv = ["--plan", str(fixture["plan_path"]), "--controls", str(fixture["controls_path"]), "--package-root", "missing-package", "--model-root", "missing-model", "--evidence-root", "missing-evidence"]
        with patch.object(worker, "_PLATFORM", "linux"), patch.object(worker, "A1OperationalPlan", BypassPlanType):
            self.rejected(lambda: worker.main(bypass_argv), "worker_model_inventory_mode_invalid")

    def test_production_inventory_is_exact_pinned_contract(self):
        inventory = op.A1ModelInventory.production()
        self.assertEqual(inventory.data["repository"], op.MODEL_REPOSITORY)
        self.assertEqual(inventory.data["revision"], op.MODEL_REVISION)
        self.assertEqual(inventory.data["file_count"], 16)
        self.assertEqual(inventory.data["total_bytes"], 19329393661)
        self.assertEqual(inventory.data["files"][0]["relative_path"], ".gitattributes")
        self.assertEqual(inventory.data["files"][-1]["relative_path"], "vocab.json")
        forged = copied(inventory.data)
        forged["files"][0]["bytes"] += 1
        forged["total_bytes"] += 1
        forged["tree_sha256"] = hashlib.sha256(canonical(forged["files"])).hexdigest()
        identify("real-a1-model-inventory-", forged, "inventory_id")
        self.rejected(lambda: op.A1ModelInventory.from_dict(forged), "model_inventory_production_contract_invalid")

    def test_public_signatures_have_no_authority_override_parameters(self):
        expected = {
            op.A1OperationalPlan.create: ("no_action_plan", "model_inventory"),
            op.A1OperationalControls.create: ("plan", "instance_id_hash", "price", "ssh_hash", "at", "evidence_hashes"),
            op.A1OperationalReceipt.create: ("plan", "controls", "observation"),
            op.A1IndependentGate.evaluate: ("plan", "controls", "receipt"),
            op.build_real_a1_worker_command: ("plan_path", "controls_path", "package_root", "model_root", "evidence_root"),
            op.run_real_a1_operational: ("plan_path", "controls_path", "package_root", "model_root", "evidence_root"),
            op.validate_a1_operational_receipt_bytes: ("raw", "plan", "controls", "observation"),
        }
        for function, names in expected.items():
            self.assertEqual(tuple(inspect.signature(function).parameters), names)
        for artifact_type in (op.A1ModelInventory, op.A1RootMarker, op.A1OperationalPlan, op.A1OperationalControls, op.A1Observation, op.A1OperationalReceipt, op.A1IndependentGate, op.A1CloseoutIntent, op.A1CloseoutReceipt):
            for name in ("from_dict", "from_bytes", "validate", "to_dict", "canonical_bytes", "sha256"):
                signature = inspect.signature(getattr(artifact_type, name))
                self.assertFalse(any(parameter.startswith("_") for parameter in signature.parameters))

    def test_direct_authority_override_calls_are_type_errors(self):
        _, plan, controls, observation, receipt, _, _, _ = self.parts()
        calls = (
            lambda: op.A1OperationalPlan.from_dict(plan.data, _validator=lambda value: value),
            lambda: op.A1OperationalPlan.from_bytes(plan.canonical_bytes(), _json_fn=lambda value: value),
            lambda: op.A1OperationalControls.create(plan, "1" * 64, 1, "2" * 64, "2026-07-25T00:00:00Z", ["3" * 64] * 4, _id_fn=lambda *_: "x"),
            lambda: receipt.validate_against(plan, controls, observation, _validator=lambda value: value),
            lambda: op.A1IndependentGate.evaluate(plan, controls, receipt, _validator=lambda value: value),
            lambda: op.validate_a1_operational_receipt_bytes(receipt.canonical_bytes(), plan, controls, observation, _from_bytes=lambda value: value),
            lambda: op.build_real_a1_worker_command("p", "c", "package-root", "model-root", "evidence-root", _executable="sh"),
            lambda: op.run_real_a1_operational("p", "c", "package-root", "model-root", "evidence-root", callback=lambda: None),
        )
        for call in calls:
            with self.assertRaises(TypeError):
                call()

    def test_package_root_exports_are_narrow(self):
        self.assertIn("A1ModelInventory", runtime_package.__all__)
        self.assertNotIn("SyntheticA1DryRunAuthority", runtime_package.__all__)
        self.assertNotIn("run_real_a1_operational", runtime_package.__all__)
        self.assertFalse(hasattr(runtime_package, "SyntheticA1DryRunAuthority"))
        self.assertFalse(hasattr(runtime_package, "run_real_a1_operational"))

    def test_extra_missing_and_direct_constructor_artifacts_fail_closed(self):
        artifacts = self.parts()
        identities = (
            ("inventory_id", "real-a1-model-inventory-", "repository"),
            ("plan_id", "real-a1-plan-", "attempt_index"),
            ("control_id", "real-a1-control-", "provider"),
            ("observation_id", "real-a1-observation-", "source"),
            ("receipt_id", "real-a1-receipt-", "status"),
            ("gate_id", "real-a1-gate-", "status"),
            ("intent_id", "real-a1-intent-", "receipt_id"),
            ("closeout_id", "real-a1-closeout-", "status"),
        )
        for artifact, (identity_key, prefix, missing_key) in zip(artifacts, identities):
            artifact_type = type(artifact)
            extra = copied(artifact.data)
            extra["unexpected"] = True
            identify(prefix, extra, identity_key)
            missing = copied(artifact.data)
            missing.pop(missing_key)
            identify(prefix, missing, identity_key)
            self.rejected(lambda artifact_type=artifact_type, extra=extra: artifact_type.from_bytes(canonical(extra)))
            self.rejected(lambda artifact_type=artifact_type, missing=missing: artifact_type.from_bytes(canonical(missing)))
            self.rejected(artifact_type(extra).to_dict)

    def test_plan_inventory_package_and_root_marker_tamper_fail_closed(self):
        _, plan, controls, _, _, _, _, _ = self.parts()
        forged = copied(plan.data)
        forged["model_inventory"]["repository"] = "synthetic/other"
        identify("real-a1-model-inventory-", forged["model_inventory"], "inventory_id")
        forged["model_inventory_sha256"] = hashlib.sha256(canonical(forged["model_inventory"])).hexdigest()
        identify("real-a1-plan-", forged, "plan_id")
        self.rejected(lambda: op.A1OperationalPlan.from_bytes(canonical(forged)), "model_inventory_production_contract_invalid")
        forged_controls = copied(controls.data)
        forged_controls["root_markers"][0]["role"] = "package"
        identify("real-a1-control-", forged_controls, "control_id")
        self.rejected(lambda: op.A1OperationalControls.from_bytes(canonical(forged_controls)))

    def test_receipt_bytes_validator_runs_and_forged_pass_cleanup_or_a2_reject(self):
        _, plan, controls, observation, receipt, gate, intent, closeout = self.parts()
        self.assertEqual(op.validate_a1_operational_receipt_bytes(receipt.canonical_bytes(), plan, controls, observation).to_dict(), receipt.data)
        forged_receipt = copied(receipt.data)
        forged_receipt["a2_unlocked"] = True
        identify("real-a1-receipt-", forged_receipt, "receipt_id")
        self.rejected(lambda: op.A1OperationalReceipt.from_bytes(canonical(forged_receipt)))
        forged_gate = copied(gate.data)
        forged_gate["status"] = "A1_passed"
        forged_gate["manager_consumable"] = True
        identify("real-a1-gate-", forged_gate, "gate_id")
        self.rejected(lambda: op.A1IndependentGate.from_bytes(canonical(forged_gate)))
        forged_closeout = copied(closeout.data)
        forged_closeout["status"] = "cleanup_complete"
        identify("real-a1-closeout-", forged_closeout, "closeout_id")
        self.rejected(lambda: op.A1CloseoutReceipt.from_bytes(canonical(forged_closeout)))
        forged_intent = copied(intent.data)
        forged_intent["physical_erasure_claimed"] = True
        identify("real-a1-intent-", forged_intent, "intent_id")
        self.rejected(lambda: op.A1CloseoutIntent.from_bytes(canonical(forged_intent)))

    def test_definition_time_closure_survives_full_module_rebinding(self):
        artifacts = self.parts()
        types = tuple(type(artifact) for artifact in artifacts)
        parsers = tuple(artifact_type.from_bytes for artifact_type in types)
        snapshots = tuple((artifact.canonical_bytes(), artifact.sha256(), artifact.to_dict()) for artifact in artifacts)
        saved = {"inventory": op.A1ModelInventory, "plan": op.A1OperationalPlan, "controls": op.A1OperationalControls, "observation": op.A1Observation, "receipt": op.A1OperationalReceipt, "gate": op.A1IndependentGate, "intent": op.A1CloseoutIntent, "closeout": op.A1CloseoutReceipt, "synthetic": op.SyntheticA1DryRunAuthority, "builder": op.build_real_a1_worker_command, "receipt_validator": op.validate_a1_operational_receipt_bytes}
        base = self.base_no_action()
        calls = []

        def rebound(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("rebound module authority used")

        class ReboundType:
            @classmethod
            def from_dict(cls, *args, **kwargs):
                return rebound(*args, **kwargs)

            @classmethod
            def from_bytes(cls, *args, **kwargs):
                return rebound(*args, **kwargs)

        patches = {
            "_AUTHORITIES": {"rebound": True}, "_TRUST": {"rebound": True}, "_KEYS": {"rebound": ("x",)}, "_OFFICIAL_MODEL_ROWS": (("x", 0, "0" * 64),),
            "PLAN_SCHEMA": "rebound", "CONTROL_SCHEMA": "rebound", "MODEL_INVENTORY_SCHEMA": "rebound", "ROOT_MARKER_SCHEMA": "rebound", "OBS_SCHEMA": "rebound", "RECEIPT_SCHEMA": "rebound", "GATE_SCHEMA": "rebound", "INTENT_SCHEMA": "rebound", "CLOSEOUT_SCHEMA": "rebound", "MODEL_REPOSITORY": "rebound", "MODEL_REVISION": "1" * 40, "MODEL_BYTES": 0, "MODEL_FILE_COUNT": 0, "BENIGN_PROMPT": "rebound",
            "_captured_canon": rebound, "_captured_sha": rebound, "_captured_id": rebound, "_captured_json": rebound, "_captured_map": rebound, "_captured_hex": rebound, "_captured_time": rebound, "_captured_datetime": rebound, "_captured_payload_free": rebound, "_captured_base_plan": rebound, "_captured_base_plan_dict": rebound,
            "_validate_model_inventory_data": rebound, "_validate_plan_data": rebound, "_validate_root_marker_data": rebound, "_validate_control_data": rebound, "_validate_observation_data": rebound, "_validate_receipt_data": rebound, "_validate_gate_data": rebound, "_validate_intent_data": rebound, "_validate_closeout_data": rebound, "_captured_inventory_parser": rebound, "_captured_plan_parser": rebound, "_captured_control_parser": rebound, "_captured_observation_parser": rebound, "_captured_receipt_parser": rebound, "_captured_intent_parser": rebound,
            "A1ModelInventory": ReboundType, "A1RootMarker": ReboundType, "A1OperationalPlan": ReboundType, "A1OperationalControls": ReboundType, "A1Observation": ReboundType, "A1OperationalReceipt": ReboundType, "A1IndependentGate": ReboundType, "A1CloseoutIntent": ReboundType, "A1CloseoutReceipt": ReboundType, "SyntheticA1DryRunAuthority": ReboundType, "AutoDLA1PreflightPlan": ReboundType, "AutoDLA1DeploymentPackageManifest": ReboundType,
        }
        with patch.multiple(op, **patches):
            for parser, snapshot, artifact in zip(parsers, snapshots, artifacts):
                raw, sha256, data = snapshot
                self.assertEqual(parser(raw).to_dict(), data)
                self.assertEqual(artifact.canonical_bytes(), raw)
                self.assertEqual(artifact.sha256(), sha256)
            inventory = saved["inventory"].from_bytes(artifacts[0].canonical_bytes())
            plan = saved["plan"].create(base, inventory)
            controls = saved["controls"].create(plan, "1" * 64, 2.78, "2" * 64, "2026-07-25T00:00:00Z", ["3" * 64, "4" * 64, "5" * 64, "6" * 64])
            observation = saved["synthetic"].observe(plan)
            receipt = saved["receipt"].create(plan, controls, observation)
            self.assertEqual(saved["receipt_validator"](receipt.canonical_bytes(), plan, controls, observation).to_dict(), receipt.data)
            self.assertEqual(saved["gate"].evaluate(plan, controls, receipt).data["status"], "synthetic_pass_not_manager_consumable")
            intent = saved["intent"].create(plan, receipt)
            self.assertEqual(saved["closeout"].create(intent).data["status"], "cleanup_not_executed")
            command = saved["builder"]("plan.json", "controls.json", "package-root", "model-root", "evidence-root")
            self.assertEqual(command[2], "req2web_runtime.autodl_a1_linux_worker")
        self.assertEqual(calls, [])

    def test_worker_validates_exact_package_model_control_and_redacted_root_bindings(self):
        fixture = self.worker_fixture()
        with patch.object(worker, "_validate_model_root", side_effect=self.validate_private_test_model_root):
            result = worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"]))
        self.assertEqual(result.plan.plan_id, fixture["plan"].plan_id)
        self.assertFalse(fixture["evidence"].exists())
        self.assertEqual(tuple(result.root_bindings), ("control", "package", "model", "evidence"))
        serialized = canonical(result.root_bindings).decode("utf-8")
        self.assertNotIn(str(fixture["base"]), serialized)
        self.assertIn("<model-root>", serialized)
        self.assertEqual(result.root_bindings["evidence"]["observed_state"], "dedicated_missing_no_write")

    def test_worker_rejects_empty_or_mismatched_package_and_model(self):
        fixture = self.worker_fixture()
        (fixture["package"] / "runtime.py").unlink()
        self.rejected(lambda: worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"])), "worker_package_regular_file_set_invalid")
        fixture = self.worker_fixture()
        (fixture["model"] / "model.bin").unlink()
        self.rejected(lambda: worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"])), "worker_model_regular_file_set_invalid")
        fixture = self.worker_fixture()
        (fixture["package"] / "runtime.py").write_bytes(b"mismatch")
        self.rejected(lambda: worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"])), "worker_package_identity_invalid")
        fixture = self.worker_fixture()
        (fixture["model"] / "model.bin").write_bytes(b"mismatch")
        self.rejected(lambda: worker._validate_test_inventory_root(fixture["model"], [file_row("model.bin", b"model")]), "worker_test_model_identity_drift")

    def test_worker_rejects_control_containment_and_four_root_topology(self):
        fixture = self.worker_fixture()
        outside = fixture["base"] / "outside-controls.json"
        outside.write_bytes(fixture["controls_path"].read_bytes())
        self.rejected(lambda: worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(outside), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"])), "worker_control_containment_invalid")
        fixture = self.worker_fixture()
        self.rejected(lambda: worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["package"]), str(fixture["evidence"])), "worker_root_topology_invalid")
        fixture = self.worker_fixture()
        nested_evidence = fixture["model"] / "future-evidence"
        self.rejected(lambda: worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(nested_evidence)), "worker_root_topology_invalid")

    def test_worker_accepts_empty_evidence_but_rejects_nonempty(self):
        fixture = self.worker_fixture()
        fixture["evidence"].mkdir()
        with patch.object(worker, "_validate_model_root", side_effect=self.validate_private_test_model_root):
            result = worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"]))
        self.assertEqual(result.root_bindings["evidence"]["observed_state"], "dedicated_empty_no_write")
        fixture = self.worker_fixture()
        fixture["evidence"].mkdir()
        (fixture["evidence"] / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        self.rejected(lambda: worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"])), "worker_evidence_root_not_empty")

    def test_worker_rejects_non_linux_before_paths(self):
        with patch.object(worker, "_PLATFORM", "win32"):
            self.rejected(lambda: worker.validate_preparation_roots("missing-plan", "missing-controls", "missing-package", "missing-model", "missing-evidence"), "real_a1_linux_worker_required")

    def test_worker_rejects_root_and_nested_symlinks(self):
        fixture = self.worker_fixture()
        root_link = fixture["base"] / "package-link"
        nested_link = fixture["model"] / "nested-link"
        try:
            root_link.symlink_to(fixture["package"], target_is_directory=True)
            nested_link.symlink_to(fixture["package"] / "runtime.py")
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        self.rejected(lambda: worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(fixture["controls_path"]), str(root_link), str(fixture["model"]), str(fixture["evidence"])), "worker_package_root_invalid")
        self.rejected(lambda: worker._validate_preparation_roots_linux(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"])), "worker_model_symlink_or_nonregular_invalid")

    def test_builder_worker_argv_match_and_terminal_is_fixed_not_ready(self):
        fixture = self.worker_fixture()
        command = op.build_real_a1_worker_command(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"]))
        self.assertEqual(command[1:3], ["-m", "req2web_runtime.autodl_a1_linux_worker"])
        with patch.object(worker, "_PLATFORM", "linux"), patch.object(worker, "_validate_model_root", side_effect=self.validate_private_test_model_root):
            self.rejected(lambda: worker.main(command[3:]), "real_a1_executor_not_ready")
        self.assertFalse(fixture["evidence"].exists())
        self.rejected(lambda: op.run_real_a1_operational(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"])), "real_a1_executor_not_ready")
        with patch.object(op, "build_real_a1_worker_command", side_effect=AssertionError("runner must not construct command")):
            self.rejected(lambda: op.run_real_a1_operational(str(fixture["plan_path"]), str(fixture["controls_path"]), str(fixture["package"]), str(fixture["model"]), str(fixture["evidence"])), "real_a1_executor_not_ready")


if __name__ == "__main__":
    unittest.main()
