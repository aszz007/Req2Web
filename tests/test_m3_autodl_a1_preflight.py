from __future__ import annotations
import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a1_preflight as module
from req2web_runtime import (
    AutoDLA1ApprovedDisposition,
    AutoDLA1DeploymentPackageManifest,
    AutoDLA1NoActionOutcome,
    AutoDLA1OperationalReceipt,
    AutoDLA1PreflightError,
    AutoDLA1PreflightPlan,
    run_autodl_a1_enforcement,
    validate_autodl_a1_control_evidence_bytes,
    validate_autodl_a1_operational_receipt_bytes,
)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def identify(prefix, payload, key):
    root = {name: value for name, value in payload.items() if name != key}
    payload[key] = prefix + hashlib.sha256(canonical(root)).hexdigest()[:20]


def copied(value):
    return json.loads(canonical(value).decode("utf-8"))


class AutoDLA1FoundationTests(unittest.TestCase):
    @property
    def root(self):
        return Path(__file__).resolve().parents[1] / "src"

    def manifest(self, source_commit_sha="c" * 40):
        rows = []
        for item in sorted(
            (path for path in self.root.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix(),
        ):
            raw = item.read_bytes()
            rows.append(
                {
                    "path": item.relative_to(self.root).as_posix(),
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        value = {
            "schema_version": module.AUTODL_A1_PACKAGE_MANIFEST_SCHEMA_VERSION,
            "source_commit_sha": source_commit_sha,
            "files": rows,
        }
        identify("autodl-a1-package-", value, "manifest_id")
        return AutoDLA1DeploymentPackageManifest.from_dict(value)

    def disposition(self, manifest, action_time_git_sha=None):
        action_time_git_sha = action_time_git_sha or manifest.data["source_commit_sha"]
        value = {
            "schema_version": module.AUTODL_A1_APPROVED_DISPOSITION_SCHEMA_VERSION,
            "disposition": "approved",
            "approval_record_sha256": "a" * 64,
            "approval_timestamp_utc": "2026-07-25T00:00:00Z",
            "authorization_expiry_utc": "2026-08-01T00:00:00Z",
            "action_time_git_sha": action_time_git_sha,
            "d17_gate_request_sha256": module.D17_GATE_REQUEST_SHA256,
            "d17_decision_set_sha256": module.D17_DECISION_SET_SHA256,
            "d17_field_policy_sha256": module.D17_FIELD_POLICY_SHA256,
            "manager_run_plan_sha256": module._manager_plan_factory().sha256(),
            "local_smoke_identity_sha256": module.LOCAL_SMOKE_REPORT_SHA256,
            "deployment_package_manifest_sha256": manifest.sha256(),
        }
        identify("autodl-a1-disposition-", value, "disposition_id")
        return AutoDLA1ApprovedDisposition.from_dict(value)

    def plan(self, source_commit_sha="c" * 40):
        manifest = self.manifest(source_commit_sha)
        return AutoDLA1PreflightPlan.create(self.disposition(manifest), manifest)

    def controls(self):
        rows = []
        for index, kind in enumerate(("instance", "price", "ssh", "cleanup_readiness")):
            rows.append(
                {
                    "kind": kind,
                    "source": "independent_control_plane_capture",
                    "observed_at_utc": "2026-07-25T00:01:00Z",
                    "evidence_sha256": ("abcd"[index]) * 64,
                }
            )
        value = {
            "schema_version": module.AUTODL_A1_PREFLIGHT_CONTROL_EVIDENCE_SCHEMA_VERSION,
            "review_state": "awaiting_independent_control_plane_validation",
            "evidence_inventory": rows,
        }
        identify("autodl-a1-controls-", value, "control_evidence_id")
        return validate_autodl_a1_control_evidence_bytes(canonical(value))

    def rejected(self, fn):
        with self.assertRaises(AutoDLA1PreflightError):
            fn()

    def test_module_worker_bypass_is_disabled(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.root)
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "req2web_runtime.autodl_a1_preflight",
                "--worker",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "AutoModelForImageTextToText",
            "from_pretrained",
            "generate(",
            "unshare",
            "Popen(",
        ):
            self.assertNotIn(forbidden, source)

    def test_success_or_observation_complete_schema_cannot_parse(self):
        plan = self.plan()
        controls = self.controls()
        outcome = AutoDLA1NoActionOutcome.create(plan, module.FAILURE_WORKER_NOT_EXECUTED)
        receipt = AutoDLA1OperationalReceipt.create(plan, controls, outcome).to_dict()
        for status in (
            "a1_passed",
            "operational_observation_evidence_complete_awaiting_independent_gate_validation",
        ):
            forged = copied(receipt)
            forged["status"] = status
            identify("autodl-a1-receipt-", forged, "receipt_id")
            self.rejected(
                lambda forged=forged: AutoDLA1OperationalReceipt.from_bytes(canonical(forged))
            )

    def test_plan_replay_rejects_disposition_manifest_or_source_rebinding(self):
        plan = self.plan().to_dict()
        manifest_sha_mismatch = copied(plan)
        disposition = manifest_sha_mismatch["approved_disposition"]
        disposition["deployment_package_manifest_sha256"] = "f" * 64
        identify("autodl-a1-disposition-", disposition, "disposition_id")
        identify("autodl-a1-plan-", manifest_sha_mismatch, "plan_id")
        self.rejected(
            lambda: AutoDLA1PreflightPlan.from_bytes(canonical(manifest_sha_mismatch))
        )

        source_sha_mismatch = copied(plan)
        manifest = source_sha_mismatch["deployment_package_manifest"]
        manifest["source_commit_sha"] = "d" * 40
        identify("autodl-a1-package-", manifest, "manifest_id")
        parsed_manifest = AutoDLA1DeploymentPackageManifest.from_dict(manifest)
        disposition = source_sha_mismatch["approved_disposition"]
        disposition["deployment_package_manifest_sha256"] = parsed_manifest.sha256()
        identify("autodl-a1-disposition-", disposition, "disposition_id")
        identify("autodl-a1-plan-", source_sha_mismatch, "plan_id")
        self.rejected(
            lambda: AutoDLA1PreflightPlan.from_bytes(canonical(source_sha_mismatch))
        )

        bad_manifest = self.manifest("d" * 40)
        self.rejected(
            lambda: AutoDLA1PreflightPlan.create(
                self.disposition(bad_manifest, "c" * 40), bad_manifest
            )
        )

    def test_receipt_failure_codes_are_exact_typed_outcome_reason(self):
        plan = self.plan()
        controls = self.controls()
        outcome = AutoDLA1NoActionOutcome.create(plan, module.FAILURE_WORKER_NOT_EXECUTED)
        receipt = AutoDLA1OperationalReceipt.create(plan, controls, outcome).to_dict()
        for failure_codes in (
            [],
            ["deployment_package_precheck_failed"],
            [outcome.data["reason"], "extra"],
            "operational_worker_not_executed_in_this_boundary",
        ):
            forged = copied(receipt)
            forged["failure_codes"] = failure_codes
            identify("autodl-a1-receipt-", forged, "receipt_id")
            self.rejected(
                lambda forged=forged: AutoDLA1OperationalReceipt.from_bytes(canonical(forged))
            )
        wrong_type = copied(receipt)
        wrong_type["failure_codes"] = (outcome.data["reason"],)
        identify("autodl-a1-receipt-", wrong_type, "receipt_id")
        self.rejected(lambda: AutoDLA1OperationalReceipt.from_dict(wrong_type))

    def test_receipt_rejects_nested_outcome_plan_rebinding_on_all_paths(self):
        plan = self.plan()
        other_plan = self.plan("d" * 40)
        controls = self.controls()
        outcome = AutoDLA1NoActionOutcome.create(plan, module.FAILURE_WORKER_NOT_EXECUTED)
        other_outcome = AutoDLA1NoActionOutcome.create(
            other_plan, module.FAILURE_WORKER_NOT_EXECUTED
        )
        self.rejected(lambda: AutoDLA1OperationalReceipt.create(plan, controls, other_outcome))

        receipt = AutoDLA1OperationalReceipt.create(plan, controls, outcome).to_dict()
        forged = copied(receipt)
        forged["outcome"] = other_outcome.to_dict()
        forged["failure_codes"] = [other_outcome.data["reason"]]
        identify("autodl-a1-receipt-", forged, "receipt_id")
        self.rejected(lambda: AutoDLA1OperationalReceipt.from_bytes(canonical(forged)))

        parsed_receipt = AutoDLA1OperationalReceipt.create(plan, controls, outcome)
        self.rejected(lambda: parsed_receipt.validate_against(plan, controls, other_outcome))

    def test_control_evidence_source_requires_string(self):
        controls = self.controls().to_dict()
        controls["evidence_inventory"][0]["source"] = 1
        identify("autodl-a1-controls-", controls, "control_evidence_id")
        self.rejected(lambda: validate_autodl_a1_control_evidence_bytes(canonical(controls)))

    def test_exact_regular_file_set_rejects_manifest_extra_or_missing(self):
        manifest = self.manifest().to_dict()
        manifest["files"] = manifest["files"][:-1]
        identify("autodl-a1-package-", manifest, "manifest_id")
        self.rejected(
            lambda: AutoDLA1DeploymentPackageManifest.from_dict(manifest).validate_against_root(
                self.root
            )
        )

    def test_public_runner_is_only_typed_fail_closed(self):
        plan = self.plan()
        controls = self.controls()
        receipt = run_autodl_a1_enforcement(plan, controls, self.root)
        self.assertEqual(receipt.data["status"], "a1_observation_failed_cleanup_required")
        self.assertEqual(receipt.data["failure_codes"], [module.FAILURE_WORKER_NOT_EXECUTED])
        self.assertFalse(receipt.data["a2_unlocked"])
        outcome = AutoDLA1NoActionOutcome.create(plan, module.FAILURE_WORKER_NOT_EXECUTED)
        validate_autodl_a1_operational_receipt_bytes(
            receipt.canonical_bytes(), plan, controls, outcome
        )

    def test_canonical_attack_and_forged_outcome_fail_closed(self):
        plan = self.plan()
        self.rejected(
            lambda: AutoDLA1PreflightPlan.from_bytes(b"\xef\xbb\xbf" + plan.canonical_bytes())
        )
        self.rejected(
            lambda: AutoDLA1PreflightPlan.from_bytes(
                plan.canonical_bytes().replace(b"{", b'{"plan_id":"x",', 1)
            )
        )
        outcome = AutoDLA1NoActionOutcome.create(
            plan, module.FAILURE_WORKER_NOT_EXECUTED
        ).to_dict()
        outcome["measurement_complete"] = True
        identify("autodl-a1-outcome-", outcome, "outcome_id")
        self.rejected(lambda: AutoDLA1NoActionOutcome.from_bytes(canonical(outcome)))

    def test_direct_constructor_controls_are_revalidated_at_all_public_boundaries(self):
        plan = self.plan()
        controls = self.controls()
        outcome = AutoDLA1NoActionOutcome.create(plan, module.FAILURE_WORKER_NOT_EXECUTED)
        receipt = AutoDLA1OperationalReceipt.create(plan, controls, outcome)
        forged_data = copied(controls.data)
        forged_data["evidence_inventory"][0]["source"] = 1
        forged_controls = module.AutoDLA1ControlPlaneEvidence(forged_data)

        self.rejected(forged_controls.to_dict)
        self.rejected(forged_controls.canonical_bytes)
        self.rejected(forged_controls.sha256)
        self.rejected(lambda: AutoDLA1OperationalReceipt.create(plan, forged_controls, outcome))
        self.rejected(lambda: receipt.validate_against(plan, forged_controls, outcome))
        self.rejected(lambda: run_autodl_a1_enforcement(plan, forged_controls, self.root))
        self.rejected(
            lambda: validate_autodl_a1_operational_receipt_bytes(
                receipt.canonical_bytes(), plan, forged_controls, outcome
            )
        )

    def test_direct_constructor_artifacts_revalidate_before_public_use(self):
        plan = self.plan()
        manifest = self.manifest()
        disposition_data = copied(plan.data["approved_disposition"])
        disposition_data["d17_gate_request_sha256"] = "0" * 64
        bad_disposition = AutoDLA1ApprovedDisposition(disposition_data)
        manifest_data = copied(manifest.data)
        manifest_data["source_commit_sha"] = "z" * 40
        bad_manifest = AutoDLA1DeploymentPackageManifest(manifest_data)
        plan_data = copied(plan.data)
        plan_data["provider_invoked"] = True
        bad_plan = AutoDLA1PreflightPlan(plan_data)

        for artifact in (bad_disposition, bad_manifest, bad_plan):
            self.rejected(artifact.to_dict)
            self.rejected(artifact.canonical_bytes)
            self.rejected(artifact.sha256)

        self.rejected(lambda: AutoDLA1PreflightPlan.create(bad_disposition, manifest))
        self.rejected(
            lambda: AutoDLA1NoActionOutcome.create(
                bad_plan, module.FAILURE_WORKER_NOT_EXECUTED
            )
        )
        self.rejected(lambda: run_autodl_a1_enforcement(bad_plan, self.controls(), self.root))

        outcome = AutoDLA1NoActionOutcome.create(plan, module.FAILURE_WORKER_NOT_EXECUTED)
        bad_outcome_data = copied(outcome.data)
        bad_outcome_data["measurement_complete"] = True
        bad_outcome = AutoDLA1NoActionOutcome(bad_outcome_data)
        self.rejected(bad_outcome.to_dict)
        self.rejected(bad_outcome.canonical_bytes)
        self.rejected(lambda: AutoDLA1OperationalReceipt.create(plan, self.controls(), bad_outcome))

        receipt = AutoDLA1OperationalReceipt.create(plan, self.controls(), outcome)
        bad_receipt_data = copied(receipt.data)
        bad_receipt_data["status"] = "a1_passed"
        bad_receipt = AutoDLA1OperationalReceipt(bad_receipt_data)
        self.rejected(bad_receipt.to_dict)
        self.rejected(bad_receipt.canonical_bytes)
        self.rejected(lambda: bad_receipt.validate_against(plan, self.controls(), outcome))

    def test_definition_time_authority_capture_rejects_runtime_rebinding(self):
        plan = self.plan()
        controls = self.controls()
        outcome = AutoDLA1NoActionOutcome.create(plan, module.FAILURE_WORKER_NOT_EXECUTED)
        receipt = AutoDLA1OperationalReceipt.create(plan, controls, outcome)
        forged = copied(plan.data)
        forged["approved_disposition"]["d17_gate_request_sha256"] = "0" * 64
        identify("autodl-a1-disposition-", forged["approved_disposition"], "disposition_id")
        identify("autodl-a1-plan-", forged, "plan_id")

        def rebound_helper(*_args, **_kwargs):
            raise AssertionError("rebound module helper must not be used")

        with (
            patch.object(module, "_manager_plan_factory", side_effect=rebound_helper),
            patch.object(module, "D17_GATE_REQUEST_SHA256", "0" * 64),
            patch.object(module, "D17_DECISION_SET_SHA256", "0" * 64),
            patch.object(module, "D17_FIELD_POLICY_SHA256", "0" * 64),
            patch.object(module, "LOCAL_SMOKE_REPORT_SHA256", "0" * 64),
            patch.object(module, "AUTODL_A1_APPROVED_DISPOSITION_SCHEMA_VERSION", "rebound"),
            patch.object(module, "AUTODL_A1_PACKAGE_MANIFEST_SCHEMA_VERSION", "rebound"),
            patch.object(module, "AUTODL_A1_PREFLIGHT_PLAN_SCHEMA_VERSION", "rebound"),
            patch.object(module, "AUTODL_A1_PREFLIGHT_CONTROL_EVIDENCE_SCHEMA_VERSION", "rebound"),
            patch.object(module, "AUTODL_A1_OPERATIONAL_RECEIPT_SCHEMA_VERSION", "rebound"),
            patch.object(module, "FAILURE_WORKER_NOT_EXECUTED", "rebound"),
            patch.object(module, "_FAILURE_REASONS", ("rebound",)),
            patch.object(module, "_captured_canon", side_effect=rebound_helper),
            patch.object(module, "_captured_sha", side_effect=rebound_helper),
            patch.object(module, "_captured_id", side_effect=rebound_helper),
            patch.object(module, "_captured_map", side_effect=rebound_helper),
            patch.object(module, "_captured_hex", side_effect=rebound_helper),
            patch.object(module, "_captured_time", side_effect=rebound_helper),
            patch.object(module, "_captured_datetime", side_effect=rebound_helper),
            patch.object(module, "_captured_now", side_effect=rebound_helper),
            patch.object(module, "_captured_json", side_effect=rebound_helper),
        ):
            self.assertEqual(plan.to_dict(), plan.data)
            self.assertEqual(plan.canonical_bytes(), canonical(plan.data))
            self.assertEqual(plan.sha256(), hashlib.sha256(canonical(plan.data)).hexdigest())
            receipt.validate_against(plan, controls, outcome)
            self.assertEqual(
                run_autodl_a1_enforcement(plan, controls, self.root).data["status"],
                "a1_observation_failed_cleanup_required",
            )
            self.rejected(lambda: AutoDLA1PreflightPlan.from_bytes(canonical(forged)))
    def test_definition_time_exact_key_snapshot_rejects_rebound_schema_tuples(self):
        manifest = self.manifest()
        disposition = self.disposition(manifest)
        plan = AutoDLA1PreflightPlan.create(disposition, manifest)
        controls = self.controls()
        outcome = AutoDLA1NoActionOutcome.create(plan, module.FAILURE_WORKER_NOT_EXECUTED)
        receipt = AutoDLA1OperationalReceipt.create(plan, controls, outcome)
        records = (
            ("disposition_id", "autodl-a1-disposition-", disposition, AutoDLA1ApprovedDisposition.from_bytes, "disposition"),
            ("manifest_id", "autodl-a1-package-", manifest, AutoDLA1DeploymentPackageManifest.from_bytes, "source_commit_sha"),
            ("plan_id", "autodl-a1-plan-", plan, AutoDLA1PreflightPlan.from_bytes, "attempt_index"),
            ("control_evidence_id", "autodl-a1-controls-", controls, module.AutoDLA1ControlPlaneEvidence.from_bytes, "review_state"),
            ("outcome_id", "autodl-a1-outcome-", outcome, AutoDLA1NoActionOutcome.from_bytes, "reason"),
            ("receipt_id", "autodl-a1-receipt-", receipt, AutoDLA1OperationalReceipt.from_bytes, "status"),
        )
        with patch.multiple(
            module,
            _DISP=("rebound",),
            _MANIFEST=("rebound",),
            _PLAN=("rebound",),
            _CONTROL=("rebound",),
            _OUTCOME=("rebound",),
            _RECEIPT=("rebound",),
        ):
            for identity_key, prefix, artifact, parser, missing_key in records:
                self.assertEqual(parser(artifact.canonical_bytes()).to_dict(), artifact.data)
                extra = copied(artifact.data)
                extra["unexpected"] = True
                identify(prefix, extra, identity_key)
                self.rejected(lambda extra=extra, parser=parser: parser(canonical(extra)))
                missing = copied(artifact.data)
                missing.pop(missing_key)
                identify(prefix, missing, identity_key)
                self.rejected(lambda missing=missing, parser=parser: parser(canonical(missing)))
    def test_doc_and_cli_keep_no_action_boundary(self):
        doc = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "stage3_m3_autodl_a1_preflight.md"
        ).read_text(encoding="utf-8")
        cli = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "run_stage3_autodl_a1_preflight.py"
        ).read_text(encoding="utf-8")
        self.assertIn("fail-closed", doc)
        self.assertIn("no module worker mode", doc.lower())
        self.assertNotIn("--prompt", cli)
        self.assertIn("cli_path_overlap_or_existing_output", cli)


if __name__ == "__main__":
    unittest.main()