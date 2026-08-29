from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4


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
from req2web_evaluation import phase5_publication_semantic as publication  # noqa: E402
from req2web_evaluation.phase5_publication_semantic import (  # noqa: E402
    PUBLICATION_SEMANTIC_CRITERION_COUNT,
    PUBLICATION_SEMANTIC_GENERATE_CALL_CAP,
    PUBLICATION_SEMANTIC_ROW_COUNT,
    Phase5PublicationSemanticError,
    aggregate_phase5_publication_semantic_results,
    prepare_phase5_publication_semantic_manifest,
    validate_phase5_publication_semantic_manifest,
)
from req2web_evaluation.phase5_semantic_evaluator import (  # noqa: E402
    SEMANTIC_EVALUATOR_PROMPT_REVISION,
)
from req2web_inspector.local_data import framework_evidence_root  # noqa: E402
from req2web_evaluation.phase5_semantic_qwen_runtime import (  # noqa: E402
    HIGH_GPU_PROFILE,
    MODEL_INPUT_SCHEMA_VERSION,
    RUN_SUMMARY_SCHEMA_VERSION,
    RUNTIME_SCHEMA_VERSION,
    semantic_qwen_runtime_profile,
)
from scripts import aggregate_phase5_publication_semantic as aggregate_cli  # noqa: E402


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
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


def _runtime_identity(raw: bytes) -> dict[str, object]:
    return {
        "sha256": "sha256:" + sha256(raw).hexdigest(),
        "byte_length": len(raw),
    }


class Phase5PublicationSemanticTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "outputs" / (
            ".phase5-publication-semantic-test-" + uuid4().hex
        )
        self.root.mkdir()
        self.source_root = self.root / "source"
        self.browser_root = self.root / "browser"
        self.results_root = self.root / "results"
        self.source_root.mkdir()
        self.browser_root.mkdir()
        self.results_root.mkdir()
        self.requests: dict[int, SemanticAlignmentRequest] = {}
        self.prepared: dict[int, dict[str, object]] = {}
        self.audits: dict[int, dict[str, object]] = {}
        self.browser_summary = self._build_browser_summary()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _request(self, index: int, case_id: str) -> SemanticAlignmentRequest:
        evidence = tuple(
            SemanticAlignmentEvidence(
                evidence_id=evidence_id,
                artifact_role=evidence_id,
                identity_kind="raw_bytes",
                sha256="sha256:" + (f"{index:02x}" * 32)[:64],
                byte_length=index + position + 1,
                revision="synthetic.v1",
            )
            for position, evidence_id in enumerate(
                (
                    "acceptance_binding",
                    "acceptance_plan",
                    "browser_execution_report",
                    "browser_screenshot",
                    "page_spec",
                    "result_package_manifest",
                )
            )
        )
        review_items = tuple(
            SemanticAlignmentReviewItem(
                criterion_id=f"criterion-{item_index:03d}",
                use_case_id=f"UC-{item_index:02d}",
                abstract_expected_outcome=(
                    f"The user completes outcome {item_index}."
                ),
                interaction_id=f"interaction-{item_index:03d}",
                observed_user_feedback=(
                    f"The page confirms outcome {item_index}."
                ),
                evidence_ids=tuple(item.evidence_id for item in evidence),
            )
            for item_index in (1, 2)
        )
        return SemanticAlignmentRequest(
            schema_version=SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
            contract_revision=SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
            case_id=case_id,
            source_acceptance_plan_sha256=("a" * 64),
            source_binding_plan_sha256=("b" * 64),
            source_browser_execution_sha256=("c" * 64),
            source_page_spec_sha256=("d" * 64),
            browser_control_allowed=False,
            model_generate_call_limit=1,
            automatic_retry_limit=0,
            allowed_verdicts=SEMANTIC_ALIGNMENT_VERDICTS,
            response_schema_version=SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
            evidence=evidence,
            review_items=review_items,
        )

    def _build_browser_summary(self) -> dict[str, object]:
        conditions = (
            "none",
            "irrelevant_evidence",
            "remove_critical_role",
        )
        rows: list[dict[str, object]] = []
        for index in range(1, 13):
            case_number = (index - 1) // 3 + 1
            condition_id = conditions[(index - 1) % 3]
            case_id = f"case-{case_number:02d}"
            row_id = f"row-{index:02d}"
            source_identity = _identity(
                f"source-{index}".encode(),
                revision="synthetic.source.v1",
                identity_kind="raw_bytes",
            )
            package_identity = {
                "schema_version": "synthetic.package.binding.v1",
                "package_identity": _identity(
                    f"package-{index}".encode(),
                    revision="synthetic.package.v1",
                    identity_kind="raw_bytes",
                ),
            }
            audit_identity = _identity(
                f"audit-{index}".encode(),
                revision="synthetic.audit.v1",
                identity_kind="raw_bytes",
            )
            request = self._request(index, case_id)
            request_raw = request.canonical_json_bytes()
            no_action_identity = _identity(
                f"bundle-{index}".encode(),
                revision="synthetic.bundle.v1",
            )
            screenshot = f"screenshot-{index}".encode()
            prepared = {
                "request": request,
                "request_raw": request_raw,
                "prompt_raw": _canonical(
                    {
                        "revision": SEMANTIC_EVALUATOR_PROMPT_REVISION,
                        "row": index,
                    }
                ),
                "model_input_raw": _canonical({"row": index}),
                "evidence_payloads": {
                    **{
                        item.evidence_id: f"evidence-{index}-{item.evidence_id}".encode()
                        for item in request.evidence
                    },
                    "browser_screenshot": screenshot,
                },
                "no_action_bundle": {
                    "bundle_identity": no_action_identity,
                },
            }
            audit = {
                "case_index": index,
                "case_id": case_id,
                "audit_identity": audit_identity,
                "result_package": package_identity,
                "semantic_alignment": {
                    "status": "not_executed",
                    "request_identity": _identity(
                        request_raw,
                        revision=request.schema_version,
                    ),
                },
            }
            self.requests[index] = request
            self.prepared[index] = prepared
            self.audits[index] = audit
            audit_path = (
                self.browser_root
                / "cases"
                / f"{index:02d}"
                / "case_browser_audit.json"
            )
            audit_path.parent.mkdir(parents=True)
            audit_path.write_bytes(_canonical(audit))
            (
                self.source_root
                / "packages"
                / f"{index:02d}-{row_id}"
            ).mkdir(parents=True)
            rows.append(
                {
                    "execution_index": index,
                    "row_id": row_id,
                    "case_id": case_id,
                    "condition_id": condition_id,
                    "source_disposition": (
                        "historical_direct_first_pass_success"
                    ),
                    "source_identity": source_identity,
                    "result_package": package_identity,
                    "browser_audit_identity": audit_identity,
                    "browser_status": "pass",
                    "browser_execution_status": "pass",
                    "page_spec_conformance_status": "pass",
                    "semantic_alignment_status": "not_executed",
                    "semantic_alignment_disposition": (
                        "needs_semantic_review"
                    ),
                    "real_browser_executed": True,
                    "automation_reliable": True,
                }
            )
        root = {
            "run_id": "synthetic-publication-run",
            "source_amended_summary_identity": _identity(
                b"amended",
                revision="synthetic.amended.v1",
            ),
            "row_results": rows,
            "all_objective_browser_checks_pass": True,
            "semantic_alignment_executed": False,
            "historical_v16_first_pass_count_preserved": 9,
            "historical_v16_failure_count_preserved": 3,
        }
        return {
            **root,
            "summary_identity": _identity(
                _canonical(root),
                revision="synthetic.browser.summary.v1",
            ),
        }

    def _prepare_side_effect(self, **kwargs: object) -> dict[str, object]:
        audit_root = kwargs["audit_root"]
        self.assertIsInstance(audit_root, Path)
        index = int(Path(audit_root).name)
        return deepcopy(self.prepared[index])

    def _manifest(self) -> dict[str, object]:
        with (
            patch.object(
                publication,
                "replay_phase5_publication_browser_result",
                return_value=deepcopy(self.browser_summary),
            ),
            patch.object(
                publication,
                "validate_real_browser_case_audit",
                side_effect=lambda value: value,
            ),
            patch.object(
                publication,
                "prepare_phase5_semantic_case",
                side_effect=self._prepare_side_effect,
            ),
            patch.object(
                publication,
                "validate_phase5_semantic_evaluator_no_action_bundle",
                side_effect=lambda **kwargs: kwargs["value"],
            ),
        ):
            return prepare_phase5_publication_semantic_manifest(
                revalidation_root=self.source_root,
                browser_root=self.browser_root,
            )

    def test_manifest_binds_twelve_rows_without_paths_or_action(self) -> None:
        manifest = self._manifest()
        self.assertEqual(manifest["row_count"], PUBLICATION_SEMANTIC_ROW_COUNT)
        self.assertEqual(
            manifest["criterion_count"],
            PUBLICATION_SEMANTIC_CRITERION_COUNT,
        )
        self.assertEqual(
            manifest["aggregate_generate_call_cap"],
            PUBLICATION_SEMANTIC_GENERATE_CALL_CAP,
        )
        self.assertFalse(manifest["semantic_alignment_executed"])
        self.assertFalse(manifest["model_loaded"])
        self.assertFalse(manifest["gpu_or_remote_action"])
        self.assertFalse(manifest["h1_or_gold_access"])
        raw = _canonical(manifest).decode("utf-8")
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn("audit_root", raw)
        self.assertNotIn("package_root", raw)
        self.assertNotRegex(raw, r"[\u3400-\u9fff]")
        self.assertEqual(
            {row["result_leaf"] for row in manifest["rows"]},
            {
                f"{index:02d}-row-{index:02d}"
                for index in range(1, 13)
            },
        )
        self.assertTrue(
            all("prompt_identity" in row for row in manifest["rows"])
        )
        self.assertTrue(
            all("model_input_identity" in row for row in manifest["rows"])
        )

    def test_manifest_replay_rejects_identity_drift(self) -> None:
        manifest = self._manifest()
        with (
            patch.object(
                publication,
                "replay_phase5_publication_browser_result",
                return_value=deepcopy(self.browser_summary),
            ),
            patch.object(
                publication,
                "validate_real_browser_case_audit",
                side_effect=lambda value: value,
            ),
            patch.object(
                publication,
                "prepare_phase5_semantic_case",
                side_effect=self._prepare_side_effect,
            ),
            patch.object(
                publication,
                "validate_phase5_semantic_evaluator_no_action_bundle",
                side_effect=lambda **kwargs: kwargs["value"],
            ),
        ):
            for field in (
                "semantic_request_sha256",
                "prompt_identity",
                "model_input_identity",
            ):
                with self.subTest(field=field):
                    drifted = deepcopy(manifest)
                    drifted["rows"][0][field] = "drifted"
                    with self.assertRaisesRegex(
                        Phase5PublicationSemanticError,
                        "does not match replayed evidence",
                    ):
                        validate_phase5_publication_semantic_manifest(
                            value=drifted,
                            revalidation_root=self.source_root,
                            browser_root=self.browser_root,
                        )

    def test_manifest_rejects_missing_or_duplicate_rows(self) -> None:
        missing = deepcopy(self.browser_summary)
        missing["row_results"].pop()
        duplicate = deepcopy(self.browser_summary)
        duplicate["row_results"][1]["row_id"] = duplicate["row_results"][0][
            "row_id"
        ]
        for summary, message in (
            (missing, "exactly twelve"),
            (duplicate, "not eligible"),
        ):
            with patch.object(
                publication,
                "replay_phase5_publication_browser_result",
                return_value=summary,
            ):
                with self.assertRaisesRegex(
                    Phase5PublicationSemanticError,
                    message,
                ):
                    prepare_phase5_publication_semantic_manifest(
                        revalidation_root=self.source_root,
                        browser_root=self.browser_root,
                    )

    def test_aggregate_retains_failed_closed_and_owner_review(self) -> None:
        manifest = self._manifest()
        terminal_values: dict[int, tuple[dict[str, object], object]] = {}
        for index, row in enumerate(manifest["rows"], start=1):
            result_dir = self.results_root / str(row["result_leaf"])
            result_dir.mkdir()
            if index == 1:
                summary = {
                    "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
                    "status": "failed_closed",
                    "case_id": row["case_id"],
                    "generate_started_count": 1,
                    "automatic_retry_count": 0,
                    "raw_response_formed": False,
                    "raw_response_identity": None,
                    "worker_error": "worker_failed",
                    "parse_error": None,
                    "owner_review_required": False,
                }
                parsed = None
            else:
                if index == 2:
                    verdict_names = ("partial", "supported")
                elif index == 3:
                    verdict_names = ("unknown", "supported")
                elif index == 4:
                    verdict_names = ("violated", "supported")
                else:
                    verdict_names = ("supported", "supported")
                summary = {
                    "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
                    "status": "semantic_alignment_complete_pending_owner_review",
                    "case_id": row["case_id"],
                    "generate_started_count": 1,
                    "automatic_retry_count": 0,
                    "raw_response_formed": True,
                    "raw_response_identity": _runtime_identity(
                        f"raw-{index}".encode()
                    ),
                    "worker_error": None,
                    "parse_error": None,
                    "owner_review_required": bool(
                        {"partial", "unknown"}.intersection(verdict_names)
                    ),
                }
                parsed = {
                    "schema_version": SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
                    "request_sha256": row["semantic_request_sha256"],
                    "verdicts": [
                        {
                            "criterion_id": f"criterion-{position:03d}",
                            "evidence_ids": ["acceptance_plan"],
                            "limitations": "Bounded synthetic evidence.",
                            "reason_summary": "The evidence was reviewed.",
                            "verdict": verdict,
                        }
                        for position, verdict in enumerate(
                            verdict_names,
                            start=1,
                        )
                    ],
                }
            (result_dir / "run_summary.json").write_bytes(_canonical(summary))
            terminal_values[index] = (summary, parsed)

        def terminal_side_effect(**kwargs: object) -> tuple[dict[str, object], object]:
            result_root = kwargs["result_root"]
            self.assertIsInstance(result_root, Path)
            index = int(Path(result_root).name[:2])
            return deepcopy(terminal_values[index])

        with (
            patch.object(
                publication,
                "validate_phase5_publication_semantic_manifest",
                return_value=manifest,
            ),
            patch.object(
                publication,
                "_validate_terminal_result",
                side_effect=terminal_side_effect,
            ),
        ):
            summary = aggregate_phase5_publication_semantic_results(
                manifest=manifest,
                revalidation_root=self.source_root,
                browser_root=self.browser_root,
                results_root=self.results_root,
            )
        counts = summary["counts"]
        self.assertEqual(counts["terminal_row_count"], 12)
        self.assertEqual(counts["accepted_semantic_result_count"], 11)
        self.assertEqual(counts["failed_closed_count"], 1)
        self.assertEqual(counts["generate_started_count"], 12)
        self.assertEqual(counts["automatic_retry_count"], 0)
        self.assertEqual(counts["accepted_criterion_count"], 22)
        self.assertEqual(counts["supported_count"], 19)
        self.assertEqual(counts["violated_count"], 1)
        self.assertEqual(counts["partial_count"], 1)
        self.assertEqual(counts["unknown_count"], 1)
        self.assertEqual(counts["owner_review_required_row_count"], 2)
        self.assertEqual(summary["objective_browser_pass_count_preserved"], 12)
        self.assertEqual(
            summary["historical_v16_first_pass_count_preserved"],
            9,
        )
        self.assertEqual(
            summary["status"],
            "semantic_evaluation_complete_with_failed_closed_rows",
        )
        self.assertTrue(summary["semantic_attempted"])
        raw = _canonical(summary).decode("utf-8")
        self.assertNotIn(str(self.root), raw)
        self.assertNotRegex(raw, r"[\u3400-\u9fff]")

    def test_failed_terminal_replay_keeps_zero_call_failure(self) -> None:
        index = 1
        prepared = self.prepared[index]
        request = self.requests[index]
        result_root = self.results_root / "failed-row"
        result_root.mkdir()
        profile = semantic_qwen_runtime_profile(HIGH_GPU_PROFILE).to_dict()
        expected_files = {
            "semantic_alignment_request.json": prepared["request_raw"],
            "evaluator_prompt.json": prepared["prompt_raw"],
            "model_input.json": prepared["model_input_raw"],
            "runtime_profile.json": _canonical(profile),
            "evidence_inventory.json": _canonical(
                [item.to_dict() for item in request.evidence]
            ),
            "no_action_preparation.json": _canonical(
                prepared["no_action_bundle"]
            ),
        }
        for name, raw in expected_files.items():
            (result_root / name).write_bytes(raw)
        pre_call = {
            "schema_version": "req2web.phase5.semantic_pre_call.v1",
            "case_id": request.case_id,
            "request_identity": _runtime_identity(prepared["request_raw"]),
            "prompt_identity": _runtime_identity(prepared["prompt_raw"]),
            "model_input_identity": _runtime_identity(
                prepared["model_input_raw"]
            ),
            "screenshot_identity": _runtime_identity(
                prepared["evidence_payloads"]["browser_screenshot"]
            ),
            "profile": profile,
            "model_generate_call_limit": 1,
            "automatic_retry_limit": 0,
            "raw_first": True,
        }
        (result_root / "pre_call_record.json").write_bytes(_canonical(pre_call))
        summary = {
            "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
            "runtime_schema_version": RUNTIME_SCHEMA_VERSION,
            "status": "failed_closed",
            "case_id": request.case_id,
            "profile": profile,
            "worker_exit_code": 1,
            "worker_timed_out": False,
            "worker_error": "worker_failed",
            "worker_receipt": None,
            "generate_started_count": 0,
            "automatic_retry_count": 0,
            "raw_response_formed": False,
            "raw_response_identity": None,
            "parse_error": None,
            "semantic_alignment_executed": False,
            "owner_review_required": False,
            "formal_quality_claimed": False,
            "finalized_without_model_call": False,
            "claim_boundary": (
                "bounded high-GPU semantic evaluation pending owner review"
            ),
        }
        (result_root / "run_summary.json").write_bytes(_canonical(summary))
        with patch.object(
            publication,
            "prepare_phase5_semantic_case",
            return_value=deepcopy(prepared),
        ):
            validated, parsed = publication._validate_failed_terminal_result(
                audit_root=self.browser_root / "cases" / "01",
                package_root=self.source_root / "packages" / "01-row-01",
                result_root=result_root,
                expected_prompt_identity=_identity(
                    prepared["prompt_raw"],
                    revision=SEMANTIC_EVALUATOR_PROMPT_REVISION,
                ),
                expected_model_input_identity=_identity(
                    prepared["model_input_raw"],
                    revision=MODEL_INPUT_SCHEMA_VERSION,
                ),
            )
        self.assertEqual(validated, summary)
        self.assertIsNone(parsed)

        for field, value in (
            ("generate_started_count", True),
            ("automatic_retry_count", False),
            ("raw_response_formed", True),
        ):
            with self.subTest(field=field):
                drifted = deepcopy(summary)
                drifted[field] = value
                (result_root / "run_summary.json").write_bytes(
                    _canonical(drifted)
                )
                with (
                    patch.object(
                        publication,
                        "prepare_phase5_semantic_case",
                        return_value=deepcopy(prepared),
                    ),
                    self.assertRaisesRegex(
                        Phase5PublicationSemanticError,
                        "terminal summary drifted",
                    ),
                ):
                    publication._validate_failed_terminal_result(
                        audit_root=self.browser_root / "cases" / "01",
                        package_root=(
                            self.source_root / "packages" / "01-row-01"
                        ),
                        result_root=result_root,
                        expected_prompt_identity=_identity(
                            prepared["prompt_raw"],
                            revision=SEMANTIC_EVALUATOR_PROMPT_REVISION,
                        ),
                        expected_model_input_identity=_identity(
                            prepared["model_input_raw"],
                            revision=MODEL_INPUT_SCHEMA_VERSION,
                        ),
                    )

    def test_aggregate_cli_rejects_noncanonical_summary_bytes(self) -> None:
        path = self.root / "noncanonical-summary.json"
        path.write_text('{"value": 1}\n', encoding="utf-8")
        with self.assertRaisesRegex(
            Phase5PublicationSemanticError,
            "not a canonical JSON object",
        ):
            aggregate_cli._load_json(path, "semantic summary")

    def test_current_evidence_prepares_when_available(self) -> None:
        evidence = framework_evidence_root(ROOT)
        source = evidence / "p5_policy_replay"
        browser = evidence / "p5_browser_final"
        if not source.is_dir() or not browser.is_dir():
            self.skipTest("current publication browser evidence is unavailable")
        manifest = prepare_phase5_publication_semantic_manifest(
            revalidation_root=source,
            browser_root=browser,
        )
        self.assertEqual(manifest["row_count"], 12)
        self.assertEqual(manifest["criterion_count"], 24)
        self.assertEqual(
            manifest["manifest_identity"]["sha256"],
            "sha256:47f10198e67b52c9f5d9d5548b2f594f464c0b3fec7267374b51c4ef5e1b31c7",
        )


if __name__ == "__main__":
    unittest.main()
