from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_acceptance import (  # noqa: E402
    SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
    SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
    SEMANTIC_ALIGNMENT_VERDICTS,
    SemanticAlignmentEvidence,
    SemanticAlignmentRequest,
    SemanticAlignmentReviewItem,
)
from req2web_evaluation.phase5_semantic_canary import (  # noqa: E402
    GENERATOR_MODEL_IDENTITY,
    Phase5SemanticCanaryError,
    prepare_phase5_semantic_canary,
    validate_phase5_semantic_canary,
    write_phase5_semantic_canary_manifest,
)
from req2web_evaluation.phase5_semantic_evaluator import (  # noqa: E402
    SEMANTIC_EVALUATOR_PROMPT_REVISION,
)
from req2web_runtime.phase4_browser_acceptance import (  # noqa: E402
    BROWSER_HARNESS_REVISION,
    CASE_AUDIT_SCHEMA_VERSION,
    validate_real_browser_case_audit,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _identity(
    raw: bytes,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    return {
        "identity_kind": identity_kind,
        "sha256": "sha256:" + sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _write_json(path: Path, value: object) -> bytes:
    raw = _canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _build_case(
    root: Path,
    *,
    case_order: int,
    case_id: str,
) -> dict[str, object]:
    audit_root = root / f"audit-{case_order}"
    package_root = root / f"package-{case_order}"
    roles = (
        "acceptance_binding",
        "acceptance_plan",
        "browser_execution_report",
        "browser_screenshot",
        "page_spec",
        "result_package_manifest",
    )
    payloads: dict[str, bytes] = {}
    evidence: list[SemanticAlignmentEvidence] = []
    for role in roles:
        if role == "browser_screenshot":
            raw = b"synthetic-browser-screenshot"
            identity_kind = "raw_bytes"
        else:
            raw = _canonical(
                {
                    "artifact_role": role,
                    "case_id": case_id,
                    "language": "English",
                    "schema_version": f"synthetic.{role}.v1",
                }
            )
            identity_kind = "canonical_json"
        payloads[role] = raw
        evidence.append(
            SemanticAlignmentEvidence(
                evidence_id=role,
                artifact_role=role,
                identity_kind=identity_kind,
                sha256="sha256:" + sha256(raw).hexdigest(),
                byte_length=len(raw),
                revision=f"synthetic.{role}.v1",
            )
        )
    request = SemanticAlignmentRequest(
        schema_version=SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
        contract_revision=SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
        case_id=case_id,
        source_acceptance_plan_sha256="1" * 64,
        source_binding_plan_sha256="2" * 64,
        source_browser_execution_sha256="3" * 64,
        source_page_spec_sha256="4" * 64,
        browser_control_allowed=False,
        model_generate_call_limit=1,
        automatic_retry_limit=0,
        allowed_verdicts=SEMANTIC_ALIGNMENT_VERDICTS,
        response_schema_version=SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
        evidence=tuple(evidence),
        review_items=(
            SemanticAlignmentReviewItem(
                criterion_id="criterion-001",
                use_case_id="UC-01",
                abstract_expected_outcome=(
                    "The user can complete the intended task."
                ),
                interaction_id="interaction-001",
                observed_user_feedback="The page reports a completed task.",
                evidence_ids=roles,
            ),
        ),
    )
    request.validate()
    request_raw = request.canonical_json_bytes()
    _write_json(
        audit_root / "semantic_alignment_request.json",
        request.to_dict(),
    )
    for role in (
        "acceptance_binding",
        "acceptance_plan",
        "browser_execution_report",
    ):
        _write_json(audit_root / f"{role}.json", json.loads(payloads[role]))
    (audit_root / "browser_screenshot.png").parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    (audit_root / "browser_screenshot.png").write_bytes(
        payloads["browser_screenshot"]
    )
    page_spec_raw = _write_json(
        package_root / "internal" / "page_spec.json",
        json.loads(payloads["page_spec"]),
    )
    package_manifest_raw = _write_json(
        package_root / "package_manifest.json",
        json.loads(payloads["result_package_manifest"]),
    )
    semantic_request_identity = _identity(
        request_raw,
        revision=SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
    )
    root_value: dict[str, object] = {
        "schema_version": CASE_AUDIT_SCHEMA_VERSION,
        "harness_revision": BROWSER_HARNESS_REVISION,
        "evidence_scope": "historical_synthetic_canary",
        "run_id": "synthetic-semantic-canary-run",
        "case_index": case_order,
        "case_id": case_id,
        "source_case_summary_identity": None,
        "result_package": {
            "manifest_identity": _identity(
                package_manifest_raw,
                revision="synthetic.package_manifest.v1",
            ),
            "inventory_identity": _identity(
                b"synthetic-package-inventory",
                revision="synthetic.package_inventory.v1",
                identity_kind="raw_bytes",
            ),
        },
        "page_spec_identity": _identity(
            page_spec_raw,
            revision="synthetic.page_spec.v1",
        ),
        "requirement_view_identity": _identity(
            b"synthetic-requirement-view",
            revision="synthetic.requirement_view.v1",
        ),
        "acceptance_plan_identity": _identity(
            payloads["acceptance_plan"],
            revision="synthetic.acceptance_plan.v1",
        ),
        "acceptance_binding_identity": _identity(
            payloads["acceptance_binding"],
            revision="synthetic.acceptance_binding.v1",
        ),
        "browser_execution_report_identity": _identity(
            payloads["browser_execution_report"],
            revision="synthetic.browser_execution_report.v1",
        ),
        "browser": {
            "name": "synthetic-browser",
            "version": "1",
        },
        "viewport": {"width": 390, "height": 844},
        "interactions": [],
        "console_messages": [],
        "page_errors": [],
        "screenshot_identity": _identity(
            payloads["browser_screenshot"],
            revision="synthetic.browser_screenshot.v1",
            identity_kind="raw_bytes",
        ),
        "criteria_counts": {
            "pass": 1,
            "fail": 0,
            "unknown": 0,
            "not_supported": 0,
        },
        "browser_status": "pass",
        "browser_execution_status": "pass",
        "page_spec_conformance_status": "pass",
        "semantic_alignment": {
            "status": "not_executed",
            "disposition": "needs_semantic_review",
            "review_item_count": 1,
            "request_identity": semantic_request_identity,
            "model_generate_calls": 0,
            "automatic_retry_count": 0,
            "claim_boundary": (
                "Objective browser evidence passed; semantic alignment "
                "remains unexecuted."
            ),
        },
        "real_browser_executed": True,
        "automation_reliable": True,
        "started_at_utc": "2026-08-08T00:00:00Z",
        "completed_at_utc": "2026-08-08T00:00:01Z",
        "claim_boundary": (
            "Synthetic browser evidence is used only to test the no-action "
            "canary contract."
        ),
    }
    audit = {
        **root_value,
        "audit_identity": _identity(
            _canonical(root_value),
            revision=CASE_AUDIT_SCHEMA_VERSION,
        ),
    }
    validate_real_browser_case_audit(audit)
    _write_json(audit_root / "case_browser_audit.json", audit)
    return {
        "case_order": case_order,
        "case_id": case_id,
        "audit_root": audit_root,
        "result_package_root": package_root,
    }


class Phase5SemanticCanaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.cases = [
            _build_case(root, case_order=index, case_id=f"case-{index:02d}")
            for index in range(1, 4)
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_happy_path_builds_and_replays_three_rows(self) -> None:
        manifest = prepare_phase5_semantic_canary(cases=self.cases)
        self.assertEqual(manifest["status"], "prepared_no_action")
        self.assertEqual(manifest["aggregate_generate_call_cap"], 3)
        self.assertEqual(manifest["aggregate_time_cap_seconds"], 3_600)
        self.assertEqual(manifest["storage_cap_bytes"], 250_000_000)
        self.assertTrue(manifest["raw_response_capture_required"])
        self.assertTrue(manifest["result_return_required"])
        self.assertEqual(
            [row["case_order"] for row in manifest["cases"]],
            [1, 2, 3],
        )
        self.assertEqual(
            manifest["model_identity"],
            GENERATOR_MODEL_IDENTITY,
        )
        self.assertEqual(
            manifest["evaluator_prompt_revision"],
            SEMANTIC_EVALUATOR_PROMPT_REVISION,
        )
        self.assertEqual(
            validate_phase5_semantic_canary(
                manifest=manifest,
                cases=self.cases,
            ),
            manifest,
        )

        output = Path(self.temporary.name) / "semantic_canary.json"
        written = write_phase5_semantic_canary_manifest(
            output_path=output,
            cases=self.cases,
        )
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), written)
        self.assertNotIn(
            self.temporary.name,
            output.read_text(encoding="utf-8"),
        )

    def test_tampered_manifest_fails_closed(self) -> None:
        manifest = prepare_phase5_semantic_canary(cases=self.cases)
        tampered = deepcopy(manifest)
        tampered["cases"][0]["request_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            Phase5SemanticCanaryError,
            "does not match replayed evidence",
        ):
            validate_phase5_semantic_canary(
                manifest=tampered,
                cases=self.cases,
            )

    def test_duplicate_case_id_fails_closed(self) -> None:
        duplicate = deepcopy(self.cases)
        duplicate[1]["case_id"] = duplicate[0]["case_id"]
        with self.assertRaisesRegex(
            Phase5SemanticCanaryError,
            "case_id values must be unique",
        ):
            prepare_phase5_semantic_canary(cases=duplicate)

    def test_browser_failure_cannot_enter_canary(self) -> None:
        audit_path = self.cases[1]["audit_root"] / "case_browser_audit.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["browser_status"] = "fail"
        audit["browser_execution_status"] = "fail"
        audit["semantic_alignment"] = {
            "status": "not_executed",
            "disposition": "blocked_by_objective_failure",
            "review_item_count": 0,
            "request_identity": None,
            "model_generate_calls": 0,
            "automatic_retry_count": 0,
            "claim_boundary": (
                "Objective browser failure blocks semantic review."
            ),
        }
        root_value = {
            key: value for key, value in audit.items() if key != "audit_identity"
        }
        audit["audit_identity"] = _identity(
            _canonical(root_value),
            revision=CASE_AUDIT_SCHEMA_VERSION,
        )
        audit_path.write_bytes(_canonical(audit))
        with self.assertRaisesRegex(
            Phase5SemanticCanaryError,
            "not an eligible no-action canary",
        ):
            prepare_phase5_semantic_canary(cases=self.cases)

    def test_manifest_cannot_gain_local_path_fields(self) -> None:
        manifest = prepare_phase5_semantic_canary(cases=self.cases)
        tampered = deepcopy(manifest)
        tampered["cases"][0]["audit_root"] = "C:\\private\\audit"
        with self.assertRaisesRegex(
            Phase5SemanticCanaryError,
            "does not match replayed evidence",
        ):
            validate_phase5_semantic_canary(
                manifest=tampered,
                cases=self.cases,
            )

    def test_unknown_browser_criterion_cannot_enter_canary(self) -> None:
        audit_path = self.cases[0]["audit_root"] / "case_browser_audit.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["criteria_counts"] = {
            "pass": 0,
            "fail": 0,
            "unknown": 1,
            "not_supported": 0,
        }
        root_value = {
            key: value for key, value in audit.items() if key != "audit_identity"
        }
        audit["audit_identity"] = _identity(
            _canonical(root_value),
            revision=CASE_AUDIT_SCHEMA_VERSION,
        )
        audit_path.write_bytes(_canonical(audit))
        with self.assertRaisesRegex(
            Phase5SemanticCanaryError,
            "browser audit is invalid",
        ):
            prepare_phase5_semantic_canary(cases=self.cases)

    def test_manifest_output_must_be_new_regular_file(self) -> None:
        output = Path(self.temporary.name) / "semantic_canary.json"
        output.write_bytes(b"occupied")
        with self.assertRaisesRegex(
            Phase5SemanticCanaryError,
            "manifest output must be a new file",
        ):
            write_phase5_semantic_canary_manifest(
                output_path=output,
                cases=self.cases,
            )

    def test_manifest_output_parent_cannot_be_symlink(self) -> None:
        root = Path(self.temporary.name)
        target = root / "target"
        target.mkdir()
        linked_parent = root / "linked-parent"
        try:
            os.symlink(target, linked_parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlink is unavailable: {exc}")
        with self.assertRaisesRegex(
            Phase5SemanticCanaryError,
            "manifest output must be a new file",
        ):
            write_phase5_semantic_canary_manifest(
                output_path=linked_parent / "semantic_canary.json",
                cases=self.cases,
            )


if __name__ == "__main__":
    unittest.main()
