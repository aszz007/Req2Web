from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime.phase5_publication_browser import (  # noqa: E402
    BROWSER_SUMMARY_SCHEMA_VERSION,
    EVIDENCE_SCOPE,
    Phase5PublicationBrowserError,
    _identity,
    _source_identity as _select_source_identity,
    replay_phase5_publication_browser_result,
    run_phase5_publication_browser,
    validate_phase5_publication_browser_summary,
)
from req2web_inspector.local_data import framework_evidence_root  # noqa: E402


TEST_ROOT = ROOT / ".phase5-publication-browser-test"
EVIDENCE_ROOT = framework_evidence_root(ROOT)
LIVE_REVALIDATION_ROOT = (
    EVIDENCE_ROOT / "p5_policy_replay"
)
LIVE_BROWSER_ROOT = (
    EVIDENCE_ROOT / "p5_browser_final"
)


def _source_identity(index: int, revision: str) -> dict[str, object]:
    return {
        "identity_kind": "canonical_json",
        "sha256": "sha256:" + f"{index:064x}",
        "byte_length": index + 10,
        "revision": revision,
    }


def _package_binding(index: int) -> dict[str, object]:
    return {
        "schema_version": "req2web.result.package.v1",
        "package_id": f"package-{index:02d}",
        "page_id": f"page-{index:02d}",
        "entrypoint": "page/index.html",
        "manifest_identity": _source_identity(
            index + 20,
            "req2web.result.package.v1",
        ),
        "inventory_identity": _source_identity(
            index + 40,
            "req2web.result.package.v1.inventory",
        ),
    }


def _write_amended_summary(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(1, 13):
        revalidated = index in {2, 5, 9}
        rows.append(
            {
                "execution_index": index,
                "row_id": f"row-{index:02d}",
                "case_id": f"case-{index:02d}",
                "condition_id": "none",
                "historical_row_summary_identity": _source_identity(
                    index,
                    "historical.row.v1",
                ),
                "historical_downstream_first_pass_success": not revalidated,
                "delivery_evidence_available": True,
                "result_package": _package_binding(index),
                "disposition": (
                    "validated_after_f4_policy_revalidation"
                    if revalidated
                    else "historical_direct_first_pass_success"
                ),
                "revalidation_receipt_identity": (
                    _source_identity(index + 60, "revalidation.receipt.v1")
                    if revalidated
                    else None
                ),
            }
        )
    source_schema = (
        "req2web.phase5.publication_f4_policy_revalidation.v1.summary"
    )
    source_root = {
        "schema_version": source_schema,
        "run_id": "phase5-publication-browser-test",
        "historical_result_return": {},
        "historical_prompt_authority_sha256": "sha256:" + "a" * 64,
        "amended_prompt_authority_identity": _source_identity(
            100,
            "prompt.v17",
        ),
        "row_results": rows,
        "counts": {
            "row_count": 12,
            "historical_downstream_first_pass_success_count": 9,
            "f4_policy_revalidated_row_count": 3,
            "delivery_evidence_available_count": 12,
            "model_generate_calls_added": 0,
            "automatic_retry_count_added": 0,
            "normalization_count_added": 0,
            "repair_count_added": 0,
            "fallback_count_added": 0,
            "real_browser_executed_count": 0,
        },
        "status": "zero_model_policy_revalidation_complete_pending_browser",
        "historical_result_overwritten": False,
        "claim_boundary": "test",
    }
    summary = {
        **source_root,
        "summary_identity": _identity(source_root, revision=source_schema),
    }
    root.mkdir(parents=True)
    (root / "amended_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return rows


class Phase5PublicationBrowserTest(unittest.TestCase):
    def tearDown(self) -> None:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_explicit_browser_confirmation_is_required(self) -> None:
        with self.assertRaisesRegex(
            Phase5PublicationBrowserError,
            "confirmation",
        ):
            run_phase5_publication_browser(
                revalidation_root=TEST_ROOT / "source",
                output_root=TEST_ROOT / "output",
                confirm_real_browser=False,
            )

    def test_all_twelve_packages_use_shared_objective_authority(self) -> None:
        source = TEST_ROOT / "source"
        rows = _write_amended_summary(source)
        calls: list[dict[str, object]] = []

        def fake_package_binding(path: Path) -> dict[str, object]:
            index = int(Path(path).name[:2])
            return _package_binding(index)

        def fake_audit(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            index = int(kwargs["case_index"])
            return {
                "run_id": kwargs["run_id"],
                "case_index": index,
                "case_id": kwargs["case_id"],
                "evidence_scope": kwargs["evidence_scope"],
                "source_case_summary_identity": kwargs[
                    "source_case_summary_identity"
                ],
                "result_package": _package_binding(index),
                "audit_identity": _source_identity(
                    index + 80,
                    "browser.audit.v1",
                ),
                "browser_status": "pass",
                "browser_execution_status": "pass",
                "page_spec_conformance_status": "pass",
                "semantic_alignment": {
                    "status": "not_executed",
                    "disposition": "needs_semantic_review",
                    "model_generate_calls": 0,
                },
                "real_browser_executed": True,
                "automation_reliable": True,
            }

        with (
            patch(
                "req2web_runtime.phase5_publication_browser."
                "validate_result_package_binding",
                side_effect=fake_package_binding,
            ),
            patch(
                "req2web_runtime.phase5_publication_browser."
                "run_real_browser_case_audit",
                side_effect=fake_audit,
            ),
        ):
            summary = run_phase5_publication_browser(
                revalidation_root=source,
                output_root=TEST_ROOT / "output",
                confirm_real_browser=True,
            )

        self.assertEqual(len(calls), 12)
        self.assertEqual(summary["schema_version"], BROWSER_SUMMARY_SCHEMA_VERSION)
        self.assertEqual(summary["evidence_scope"], EVIDENCE_SCOPE)
        self.assertTrue(summary["all_objective_browser_checks_pass"])
        self.assertEqual(summary["counts"]["browser_execution_pass_count"], 12)
        self.assertEqual(
            summary["counts"]["page_spec_conformance_pass_count"],
            12,
        )
        self.assertEqual(summary["counts"]["model_generate_calls_added"], 0)
        for index in (2, 5, 9):
            self.assertEqual(
                calls[index - 1]["source_case_summary_identity"],
                rows[index - 1]["revalidation_receipt_identity"],
            )

    def test_revalidated_row_cannot_claim_historical_first_pass(self) -> None:
        row = {
            "disposition": "validated_after_f4_policy_revalidation",
            "historical_downstream_first_pass_success": True,
            "revalidation_receipt_identity": _source_identity(
                62,
                "revalidation.receipt.v1",
            ),
        }
        with self.assertRaisesRegex(
            Phase5PublicationBrowserError,
            "provenance drifted",
        ):
            _select_source_identity(row)

    @unittest.skipUnless(
        (LIVE_REVALIDATION_ROOT / "amended_summary.json").is_file()
        and (LIVE_BROWSER_ROOT / "browser_summary.json").is_file(),
        "the local Phase 5 browser result is not present",
    )
    def test_live_twelve_row_browser_result_replays_by_pure_reads(self) -> None:
        summary = replay_phase5_publication_browser_result(
            revalidation_root=LIVE_REVALIDATION_ROOT,
            browser_root=LIVE_BROWSER_ROOT,
        )
        self.assertTrue(summary["all_objective_browser_checks_pass"])
        self.assertEqual(summary["counts"]["real_browser_executed_count"], 12)
        self.assertEqual(
            summary["counts"]["page_spec_conformance_pass_count"],
            12,
        )

    def test_browser_summary_identity_tamper_fails_closed(self) -> None:
        source = TEST_ROOT / "source"
        _write_amended_summary(source)
        with (
            patch(
                "req2web_runtime.phase5_publication_browser."
                "validate_result_package_binding",
                side_effect=lambda path: _package_binding(
                    int(Path(path).name[:2])
                ),
            ),
            patch(
                "req2web_runtime.phase5_publication_browser."
                "run_real_browser_case_audit",
                side_effect=lambda **kwargs: {
                    "run_id": kwargs["run_id"],
                    "case_index": kwargs["case_index"],
                    "case_id": kwargs["case_id"],
                    "evidence_scope": kwargs["evidence_scope"],
                    "source_case_summary_identity": kwargs[
                        "source_case_summary_identity"
                    ],
                    "result_package": _package_binding(
                        int(kwargs["case_index"])
                    ),
                    "audit_identity": _source_identity(
                        int(kwargs["case_index"]) + 80,
                        "browser.audit.v1",
                    ),
                    "browser_status": "pass",
                    "browser_execution_status": "pass",
                    "page_spec_conformance_status": "pass",
                    "semantic_alignment": {
                        "status": "not_executed",
                        "disposition": "needs_semantic_review",
                        "model_generate_calls": 0,
                    },
                    "real_browser_executed": True,
                    "automation_reliable": True,
                },
            ),
        ):
            summary = run_phase5_publication_browser(
                revalidation_root=source,
                output_root=TEST_ROOT / "output",
                confirm_real_browser=True,
            )
        summary["counts"]["browser_execution_pass_count"] = 11
        with self.assertRaisesRegex(
            Phase5PublicationBrowserError,
            "identity drifted",
        ):
            validate_phase5_publication_browser_summary(summary)


if __name__ == "__main__":
    unittest.main()
