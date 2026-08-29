from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime.phase4_browser_acceptance import (  # noqa: E402
    Phase4BrowserAcceptanceError,
    build_browser_final_summary,
    build_browser_canary_receipt,
    run_real_browser_case_audit,
    validate_real_browser_case_audit,
)
from req2web_inspector.local_data import framework_evidence_root  # noqa: E402


PACKAGE_ROOT = (
    framework_evidence_root(ROOT)
    / "demo_form"
    / "packages"
    / "ecommerce"
)


class _ObservedPassingBackend:
    def __init__(self, binding, screenshot_path: Path) -> None:
        self.screenshot_path = screenshot_path
        self.current_state = "initial"
        self.current_feedback = ""
        self.group_index = -1
        self.trigger_index = 0
        steps_by_id = {step.step_id: step for step in binding.steps}
        self.groups: list[list[tuple[str, str, str]]] = []
        for criterion in binding.bindings:
            if criterion.disposition != "bound":
                continue
            steps = [steps_by_id[step_id] for step_id in criterion.step_ids]
            group: list[tuple[str, str, str]] = []
            for index, step in enumerate(steps):
                if step.action_kind != "trigger_interaction":
                    continue
                feedback = next(
                    dict(candidate.expected_payload)["feedback"]
                    for candidate in steps[index + 1 :]
                    if candidate.action_kind == "assert_feedback"
                )
                group.append(
                    (
                        step.selector,
                        dict(step.expected_payload)["target_state_id"],
                        feedback,
                    )
                )
            self.groups.append(group)
        self.interactions: list[dict[str, str]] = []

    def navigate(self, page_url: str, timeout_ms: int) -> dict[str, str]:
        self.group_index += 1
        self.trigger_index = 0
        self.current_state = "initial"
        self.current_feedback = ""
        return {"navigation": "completed"}

    def element_exists(self, selector: str, timeout_ms: int) -> bool:
        return True

    def trigger(self, selector: str, timeout_ms: int) -> dict[str, str]:
        expected, state, feedback = self.groups[self.group_index][
            self.trigger_index
        ]
        if selector != expected:
            raise AssertionError("browser trigger order drifted")
        self.trigger_index += 1
        self.current_state = state
        self.current_feedback = feedback
        self.interactions.append(
            {
                "kind": "click",
                "selector": selector,
                "mechanism": "click",
            }
        )
        return {"triggered": "true", "mechanism": "click"}

    def read_state(self, timeout_ms: int) -> dict[str, str]:
        return {"state_id": self.current_state}

    def read_text(self, selector: str, timeout_ms: int) -> str:
        return self.current_feedback

    def close(self) -> None:
        self.screenshot_path.write_bytes(b"controlled-browser-screenshot")

    def facts(self) -> dict[str, object]:
        return {
            "browser_started": True,
            "screenshot_captured": self.screenshot_path.is_file(),
            "console_messages": [],
            "page_errors": [],
            "interactions": copy.deepcopy(self.interactions),
            "browser": {
                "engine": "controlled_test_backend",
                "channel": None,
                "headless": True,
            },
        }


def _backend_factory(binding, screenshot_path, viewport):
    return _ObservedPassingBackend(binding, screenshot_path)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


@unittest.skipUnless(
    PACKAGE_ROOT.is_dir(),
    "local deterministic package evidence is unavailable",
)
class Phase4BrowserAcceptanceTest(unittest.TestCase):
    def test_final_summary_keeps_non_executable_cases_separate(self) -> None:
        identity = {
            "identity_kind": "canonical_json",
            "sha256": "sha256:" + "a" * 64,
            "byte_length": 10,
            "revision": "test.identity.v1",
        }
        source_rows = []
        audits = []
        executed_indices = {1, 2, 3, 5, 7, 8, 9, 10}
        for index in range(1, 11):
            case_id = f"case-{index:02d}"
            source_rows.append(
                {
                    "case_index": index,
                    "case_id": case_id,
                    "case_summary_identity": identity,
                    "status": (
                        "delivery_terminal_success"
                        if index in executed_indices
                        else "failed_closed"
                    ),
                    "downstream_delivery_success": (
                        index in executed_indices
                    ),
                    "failure": (
                        None
                        if index in executed_indices
                        else {"failure_stage": "F2"}
                    ),
                }
            )
            if index in executed_indices:
                audits.append(
                    {
                        "case_index": index,
                        "case_id": case_id,
                        "run_id": "run-final",
                        "evidence_scope": (
                            "phase4_canonical_canary"
                            if index <= 3
                            else "phase4_canonical_full"
                        ),
                        "source_case_summary_identity": identity,
                        "browser_execution_status": "pass",
                        "page_spec_conformance_status": "pass",
                        "semantic_alignment": {"status": "not_executed"},
                        "real_browser_executed": True,
                        "automation_reliable": True,
                        "audit_identity": identity,
                    }
                )
        flow_summary = {
            "schema_version": (
                "req2web.phase4.canonical_full_flow.v1.summary"
            ),
            "run_id": "run-final",
            "all_cases_complete": True,
            "completed_case_count": 10,
            "case_summaries": source_rows,
            "summary_identity": identity,
        }
        with patch(
            "req2web_runtime.phase4_browser_acceptance."
            "validate_real_browser_case_audit",
            side_effect=lambda value: dict(value),
        ):
            summary = build_browser_final_summary(
                flow_summary=flow_summary,
                case_audits=tuple(audits),
            )
        self.assertEqual(
            summary["status"],
            "browser_audit_complete_with_non_executable_cases",
        )
        self.assertEqual(
            summary["counts"]["real_browser_executed_count"],
            8,
        )
        self.assertEqual(
            summary["counts"][
                "not_executed_no_successful_result_package_count"
            ],
            2,
        )
        self.assertFalse(summary["all_ten_cases_browser_executed"])

    def test_v2_audit_separates_objective_layers_and_freezes_semantic_request(self) -> None:
        root = (
            ROOT
            / "tests"
            / ".tmp_phase4_browser_acceptance"
            / self._testMethodName
        )
        shutil.rmtree(root, ignore_errors=True)
        try:
            audit = run_real_browser_case_audit(
                package_root=PACKAGE_ROOT,
                output_root=root,
                run_id="historical-synthetic-browser-canary-v2",
                case_index=1,
                case_id="historical-ecommerce-v2",
                evidence_scope="historical_synthetic_canary",
                backend_factory=_backend_factory,
            )
            self.assertEqual(audit["browser_status"], "pass")
            self.assertEqual(audit["browser_execution_status"], "pass")
            self.assertEqual(
                audit["page_spec_conformance_status"],
                "pass",
            )
            self.assertEqual(
                audit["semantic_alignment"]["status"],
                "not_executed",
            )
            self.assertEqual(
                audit["semantic_alignment"]["disposition"],
                "needs_semantic_review",
            )
            self.assertEqual(
                audit["semantic_alignment"]["model_generate_calls"],
                0,
            )
            request = json.loads(
                (root / "semantic_alignment_request.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(request["browser_control_allowed"])
            self.assertEqual(request["model_generate_call_limit"], 1)
            self.assertEqual(request["automatic_retry_limit"], 0)
            self.assertTrue(request["review_items"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_phase5_publication_scope_reuses_objective_browser_authority(
        self,
    ) -> None:
        root = (
            ROOT
            / "tests"
            / ".tmp_phase4_browser_acceptance"
            / self._testMethodName
        )
        shutil.rmtree(root, ignore_errors=True)
        source_identity = {
            "identity_kind": "canonical_json",
            "sha256": "sha256:" + "b" * 64,
            "byte_length": 20,
            "revision": "req2web.phase5.publication.source.v1",
        }
        try:
            with patch(
                "req2web_runtime.phase4_browser_acceptance."
                "validate_english_publication_tree"
            ):
                audit = run_real_browser_case_audit(
                    package_root=PACKAGE_ROOT,
                    output_root=root,
                    run_id="phase5-publication-browser-test",
                    case_index=1,
                    case_id="publication-case-01",
                    evidence_scope="phase5_publication_engineering",
                    source_case_summary_identity=source_identity,
                    backend_factory=_backend_factory,
                )
            self.assertEqual(audit["browser_status"], "pass")
            self.assertEqual(
                audit["source_case_summary_identity"],
                source_identity,
            )
            self.assertEqual(
                audit["semantic_alignment"]["status"],
                "not_executed",
            )
            self.assertIn("Phase 5 publication", audit["claim_boundary"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_canonical_browser_audit_rejects_mixed_language_package(
        self,
    ) -> None:
        temporary_root = (
            ROOT
            / "tests"
            / ".tmp_phase4_browser_acceptance"
            / self._testMethodName
        )
        shutil.rmtree(temporary_root, ignore_errors=True)
        try:
            package_root = temporary_root / "package"
            output_root = temporary_root / "audit"
            shutil.copytree(PACKAGE_ROOT, package_root)
            context_path = package_root / "internal/agent_context.json"
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["requirement_summary"] += " \u4e2d\u6587"
            context_raw = json.dumps(
                context,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            context_path.write_bytes(context_raw)
            manifest_path = package_root / "package_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            row = next(
                item
                for item in manifest["files"]
                if item["path"] == "internal/agent_context.json"
            )
            row["sha256"] = sha256(context_raw).hexdigest()
            row["size"] = len(context_raw)
            manifest_path.write_text(
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            source_identity = {
                "identity_kind": "canonical_json",
                "sha256": "sha256:" + "a" * 64,
                "byte_length": 10,
                "revision": "test.case_summary.v1",
            }

            with self.assertRaisesRegex(
                Phase4BrowserAcceptanceError,
                "English-only package",
            ):
                run_real_browser_case_audit(
                    package_root=package_root,
                    output_root=output_root,
                    run_id="canonical-english-only-test",
                    case_index=1,
                    case_id="case-01",
                    evidence_scope="phase4_canonical_canary",
                    source_case_summary_identity=source_identity,
                    backend_factory=lambda *_: self.fail(
                        "browser backend must not be created"
                    ),
                )
            self.assertFalse(output_root.exists())
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows managed host denies nested temporary browser-audit directories",
    )
    def test_historical_package_runs_through_bound_browser_harness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audit = run_real_browser_case_audit(
                package_root=PACKAGE_ROOT,
                output_root=Path(temporary) / "audit",
                run_id="historical-synthetic-browser-canary",
                case_index=1,
                case_id="historical-ecommerce",
                evidence_scope="historical_synthetic_canary",
                backend_factory=_backend_factory,
            )
            self.assertEqual(audit["browser_status"], "pass")
            self.assertEqual(audit["browser_execution_status"], "pass")
            self.assertEqual(audit["page_spec_conformance_status"], "pass")
            self.assertEqual(
                audit["semantic_alignment"]["disposition"],
                "needs_semantic_review",
            )
            self.assertTrue(audit["real_browser_executed"])
            self.assertTrue(audit["automation_reliable"])
            self.assertIsNone(audit["source_case_summary_identity"])
            self.assertEqual(
                audit["result_package"]["entrypoint"],
                "page/index.html",
            )
            self.assertTrue(
                (Path(temporary) / "audit/browser_screenshot.png").is_file()
            )

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows managed host denies nested temporary browser-audit directories",
    )
    def test_case_audit_identity_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audit = run_real_browser_case_audit(
                package_root=PACKAGE_ROOT,
                output_root=Path(temporary) / "audit",
                run_id="historical-synthetic-browser-canary",
                case_index=1,
                case_id="historical-ecommerce",
                evidence_scope="historical_synthetic_canary",
                backend_factory=_backend_factory,
            )
            forged = copy.deepcopy(audit)
            forged["browser_status"] = "fail"
            with self.assertRaisesRegex(
                Phase4BrowserAcceptanceError,
                "identity",
            ):
                validate_real_browser_case_audit(forged)

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows managed host denies nested temporary browser-audit directories",
    )
    def test_three_exact_case_audits_build_continuation_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            flow = root / "flow"
            policy_identity = {
                "identity_kind": "canonical_json",
                "sha256": "sha256:" + "a" * 64,
                "byte_length": 100,
                "revision": "test.flow.policy.v1",
            }
            _write_json(
                flow / "flow_policy.json",
                {
                    "run_id": "canonical-flow-run",
                    "policy_identity": policy_identity,
                },
            )
            audit_paths: list[Path] = []
            for index in range(1, 4):
                summary_identity = {
                    "identity_kind": "canonical_json",
                    "sha256": "sha256:" + str(index) * 64,
                    "byte_length": 100 + index,
                    "revision": (
                        "req2web.phase4.canonical_full_flow.v1.case_summary"
                    ),
                }
                case_id = f"canonical-case-{index}"
                _write_json(
                    flow / "case-summaries" / f"{index:02d}.json",
                    {
                        "schema_version": (
                            "req2web.phase4.canonical_full_flow.v1.case_summary"
                        ),
                        "case_id": case_id,
                        "case_summary_identity": summary_identity,
                    },
                )
                output = root / "browser" / f"{index:02d}"
                audit = run_real_browser_case_audit(
                    package_root=PACKAGE_ROOT,
                    output_root=output,
                    run_id="canonical-flow-run",
                    case_index=index,
                    case_id=case_id,
                    evidence_scope="phase4_canonical_canary",
                    source_case_summary_identity=summary_identity,
                    backend_factory=_backend_factory,
                )
                audit_paths.append(output / "case_browser_audit.json")
                self.assertEqual(audit["browser_status"], "pass")
                self.assertEqual(
                    audit["browser_execution_status"],
                    "pass",
                )
                self.assertEqual(
                    audit["page_spec_conformance_status"],
                    "pass",
                )
            receipt = build_browser_canary_receipt(
                flow_result_root=flow,
                case_audit_paths=tuple(audit_paths),
                output_path=root / "browser/canary_receipt.json",
            )
            self.assertTrue(receipt["continuation_allowed"])
            self.assertEqual(receipt["canary_case_count"], 3)
            self.assertTrue(
                receipt["all_canary_browser_execution_pass"]
            )
            self.assertTrue(
                receipt["all_canary_page_spec_conformance_pass"]
            )
            self.assertFalse(receipt["semantic_alignment_executed"])


if __name__ == "__main__":
    unittest.main()
