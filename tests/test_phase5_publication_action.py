from __future__ import annotations

import copy
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import unittest
from unittest import mock
import uuid

from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    phase4_create_portable_authority_state,
    phase4_project_node_input_authority,
    synthetic_commerce_b_input,
)
from req2web_runtime import phase4_remote_qwen_fresh_integrated as fresh
from req2web_runtime.phase5_publication_action import (
    IRRELEVANT_EVIDENCE_ROLE,
    Phase5PublicationActionError,
    TOTAL_GENERATE_CALL_CAP,
    build_phase5_provider_evidence_projection,
    build_phase5_publication_baseline_documents,
    create_phase5_publication_owner_action_receipt,
    load_phase5_publication_fixtures,
    phase5_publication_fixture_blob_ids,
    run_phase5_publication_action,
    validate_phase5_publication_run_summary,
    write_phase5_publication_baseline_index,
)


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _writable_test_root():
    root = ROOT / f".phase5-publication-action-test-{uuid.uuid4().hex}"
    try:
        root.mkdir()
    except PermissionError as exc:
        raise unittest.SkipTest("host denied a writable test root") from exc
    try:
        yield root
    finally:
        try:
            shutil.rmtree(root)
        except PermissionError:
            pass


class Phase5PublicationActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = load_phase5_publication_fixtures(ROOT)
        cls.case = cls.fixtures["drafts"]["cases"][0]
        template_id = cls.case["template_id"]
        cls.template = next(
            item
            for item in cls.fixtures["templates"]["templates"]
            if item["template_id"] == template_id
        )

    def test_tracked_fixtures_fix_twelve_rows_and_48_calls(self) -> None:
        matrix = self.fixtures["matrix"]
        self.assertEqual(matrix["runtime_row_count"], 12)
        self.assertEqual(matrix["node_generate_call_cap"], TOTAL_GENERATE_CALL_CAP)
        self.assertEqual(len(matrix["execution_order"]), 12)
        self.assertEqual(matrix["active_reserve_case_count"], 0)

    def test_fixture_blob_binding_rejects_uncommitted_drift(self) -> None:
        committed = mock.Mock(returncode=0, stdout="a" * 40 + "\n")
        worktree = mock.Mock(returncode=0, stdout="b" * 40 + "\n")
        with mock.patch(
            "req2web_runtime.phase5_publication_action.subprocess.run",
            side_effect=(committed, worktree),
        ):
            with self.assertRaisesRegex(ValueError, "drifted from source commit"):
                phase5_publication_fixture_blob_ids(ROOT, "c" * 40)

    def test_baseline_corpus_has_one_project_authored_document_per_role(self) -> None:
        documents = build_phase5_publication_baseline_documents(
            self.case,
            self.template,
        )
        self.assertEqual(
            [item["role"] for item in documents],
            [
                "requirement",
                "ui_reference",
                "interaction_flow",
                "implementation",
                "validation",
            ],
        )
        self.assertEqual(len({item["doc_id"] for item in documents}), 5)
        self.assertTrue(all(item["references"] == [] for item in documents))
        self.assertTrue(all(item["metadata"]["h1_or_gold"] is False for item in documents))

    def test_baseline_index_is_replayable(self) -> None:
        documents = build_phase5_publication_baseline_documents(
            self.case,
            self.template,
        )
        with _writable_test_root() as temporary:
            receipt = write_phase5_publication_baseline_index(
                index_root=temporary / "index",
                documents=documents,
            )
            self.assertEqual(receipt["document_count"], 5)
            self.assertEqual(receipt["role_order"][0], "requirement")
            self.assertTrue((temporary / "index" / "tfidf_index.json.gz").is_file())

    def _context_value(self) -> dict[str, object]:
        documents = build_phase5_publication_baseline_documents(
            self.case,
            self.template,
        )
        return {
            "retrieval_results": {
                role: [
                    {
                        "doc_id": item["doc_id"],
                        "role": role,
                        "summary": item["summary"],
                    }
                ]
                for role, item in (
                    (str(document["role"]), document) for document in documents
                )
            }
        }

    def test_conditions_change_only_the_provider_evidence_projection(self) -> None:
        kwargs = {
            "context_value": self._context_value(),
            "irrelevant_evidence": self.template["irrelevant_evidence_outline"],
            "critical_role_id": self.case["intervention_binding"]["critical_role_id"],
        }
        none, none_receipt = build_phase5_provider_evidence_projection(
            condition_id="none", **kwargs
        )
        irrelevant, irrelevant_receipt = build_phase5_provider_evidence_projection(
            condition_id="irrelevant_evidence", **kwargs
        )
        removed, removed_receipt = build_phase5_provider_evidence_projection(
            condition_id="remove_critical_role", **kwargs
        )
        self.assertEqual(
            none_receipt["full_local_baseline_identity"],
            irrelevant_receipt["full_local_baseline_identity"],
        )
        self.assertEqual(
            none_receipt["full_local_baseline_identity"],
            removed_receipt["full_local_baseline_identity"],
        )
        self.assertEqual(
            len(irrelevant["F1"]["items"]),
            len(none["F1"]["items"]) + 1,
        )
        self.assertEqual(
            len(removed["F1"]["items"]),
            len(none["F1"]["items"]) - 1,
        )
        self.assertEqual(IRRELEVANT_EVIDENCE_ROLE, "implementation")

    def test_model_visible_projection_never_exposes_condition_labels(self) -> None:
        views, _ = build_phase5_provider_evidence_projection(
            context_value=self._context_value(),
            irrelevant_evidence=self.template["irrelevant_evidence_outline"],
            condition_id="irrelevant_evidence",
            critical_role_id=self.case["intervention_binding"]["critical_role_id"],
        )
        serialized = json.dumps(views, sort_keys=True)
        self.assertNotIn("condition_id", serialized)
        self.assertNotIn("irrelevant_evidence", serialized)
        self.assertNotIn("critical_role", serialized)
        self.assertNotIn("gold", serialized)

    def test_phase4_default_node_input_is_byte_identical(self) -> None:
        b_input = synthetic_commerce_b_input()
        state = phase4_create_portable_authority_state(b_input)
        authority = phase4_project_node_input_authority(state, "F1")
        actual = fresh._node_input(
            node_id="F1",
            b_input=b_input,
            state=state,
            authority_projection=authority,
        )
        expected = fresh._canonical_bytes(
            {
                "schema_version": fresh.P4_05_INPUT_SCHEMA_VERSION,
                "node_id": "F1",
                "case_id": b_input["case_id"],
                "request_id": b_input["request_id"],
                "logical_input_classes": list(fresh.P4_05_INPUT_CLASSES["F1"]),
                "projection": fresh._provider_visible_projection(
                    node_id="F1",
                    b_input=b_input,
                    state=state,
                    authority_projection=authority,
                ),
            }
        )
        self.assertEqual(actual, expected)

    def test_external_owner_receipt_binds_exact_action_without_execution(self) -> None:
        receipt = create_phase5_publication_owner_action_receipt(
            source_action_commit="a" * 40,
            run_id="phase5-publication-test",
            fixtures=self.fixtures,
            fixture_blob_ids={name: "d" * 40 for name in ("templates", "intervention", "drafts", "matrix")},
            owner_confirmation_sha256="b" * 64,
            instance_id="instance-test",
            gpu_uuid="GPU-test",
            ssh_fingerprint_sha256="c" * 64,
            repository_root="/root/action/repository",
            model_root="/root/action/model",
            integrity_evidence="/root/action/integrity.json",
            python_executable="/root/action/python",
            result_root="/root/action/result",
            hourly_rate_minor_units=0,
            time_cap_seconds=100,
            cost_cap_minor_units=100,
            storage_cap_bytes=1000000,
        )
        self.assertIs(receipt["authorization"]["owner_approved_exact_run"], True)
        self.assertIs(receipt["authorization"]["h1_open_allowed"], False)
        self.assertIs(receipt["action_state"]["model_loaded"], False)

    def test_optional_evidence_view_uses_shared_input_and_prompt(self) -> None:
        b_input = synthetic_commerce_b_input()
        state = phase4_create_portable_authority_state(b_input)
        authority = phase4_project_node_input_authority(state, "F1")
        view = {
            "schema_version": fresh.P4_05_PROVIDER_EVIDENCE_VIEW_SCHEMA_VERSION,
            "items": [
                {
                    "role": "implementation",
                    "opaque_doc_id": "doc-0123456789abcdef",
                    "project_authored_nonverbatim_short_summary": "Keep state in the page session.",
                }
            ],
        }
        input_bytes = fresh._node_input(
            node_id="F1",
            b_input=b_input,
            state=state,
            authority_projection=authority,
            provider_evidence_view=view,
        )
        parsed = json.loads(input_bytes)
        self.assertEqual(parsed["logical_input_classes"][-1], "provider_evidence_view")
        prompt = json.loads(
            fresh._node_prompt(
                node_id="F1",
                input_bytes=input_bytes,
                prompt_revision=fresh.P4_05_FULL_DIRECT_PROMPT_REVISION,
            )
        )
        self.assertEqual(prompt["prompt_revision"], fresh.P4_05_FULL_DIRECT_PROMPT_REVISION)
        self.assertIn(
            "finalize the complete components array",
            "\n".join(prompt["instructions"]),
        )

    def test_parent_runs_exact_matrix_and_delegates_each_row_once(self) -> None:
        fake_inventory = {
            "model_root_identity": {"sha256": "sha256:model-root"},
            "inventory_identity": {"sha256": "sha256:model-inventory"},
            "file_count": 16,
        }
        fake_runtime = {
            "python_version": "3.11.15",
            "transformers_version": "5.14.1",
            "torch_version": "2.7.1+cu128",
            "accelerate_version": "1.14.0",
        }
        fake_gpu = {
            "device_name": "NVIDIA GeForce RTX 5090",
            "device_uuid": "GPU-test",
            "total_vram_bytes": 34190917632,
            "free_vram_bytes": 33669775360,
            "driver_version": "595.71.05",
            "cuda_version": "12.8",
        }

        def fake_summary(**kwargs: object) -> dict[str, object]:
            return {
                "status": "failed_closed",
                "per_node_generate_started": {node: 1 for node in NODE_ORDER},
                "per_node_raw_contract_pass": {node: False for node in NODE_ORDER},
                "downstream_first_pass_success": False,
                "downstream_repair_success": False,
                "downstream_fallback_success": False,
                "downstream_delivery_success": False,
            }

        with _writable_test_root() as base:
            model_root = base / "model"
            model_root.mkdir()
            evidence = base / "integrity.json"
            evidence.write_text("{}", encoding="utf-8")
            result_root = base / "result"
            receipt_path = base / "owner_action_receipt.json"
            receipt_value = {
                "source_action_commit": "a" * 40,
                "run_id": "phase5-publication-test",
                "instance": {
                    "instance_id": "instance-test",
                    "gpu_uuid": "GPU-test",
                    "ssh_fingerprint_sha256": "b" * 64,
                },
                "paths": {
                    "repository_root": str(ROOT.resolve()),
                    "model_root": str(model_root.resolve()),
                    "integrity_evidence": str(evidence.resolve()),
                    "python_executable": "python-test",
                    "result_root": str(result_root.resolve()),
                },
                "limits": {
                    "time_cap_seconds": 100,
                    "cost_cap_minor_units": 100,
                    "storage_cap_bytes": 1000000,
                },
            }
            receipt_path.write_bytes(fresh._canonical_bytes(receipt_value))
            with (
                mock.patch(
                    "req2web_runtime.phase5_publication_action._repository_head",
                    return_value="a" * 40,
                ),
                mock.patch(
                    "req2web_runtime.phase5_publication_action.phase5_publication_fixture_blob_ids",
                    return_value={name: "d" * 40 for name in ("templates", "intervention", "drafts", "matrix")},
                ),
                mock.patch(
                    "req2web_runtime.phase5_publication_action.load_phase5_publication_fixtures",
                    return_value=self.fixtures,
                ),
                mock.patch.object(
                    fresh._remote,
                    "validate_remote_model_inventory",
                    return_value=fake_inventory,
                ),
                mock.patch.object(
                    fresh._remote,
                    "_collect_remote_runtime_facts",
                    return_value=fake_runtime,
                ),
                mock.patch.object(
                    fresh._remote,
                    "_probe_remote_gpu_facts",
                    return_value=fake_gpu,
                ),
                mock.patch(
                    "req2web_runtime.phase5_publication_action.run_phase4_remote_qwen_langgraph_integrated",
                    return_value={"status": "failed_closed"},
                ) as child,
                mock.patch(
                    "req2web_runtime.phase5_publication_action.validate_phase5_publication_owner_action_receipt",
                    return_value=receipt_value,
                ),
                mock.patch(
                    "req2web_runtime.phase5_publication_action._summarize_case",
                    side_effect=fake_summary,
                ),
            ):
                summary = run_phase5_publication_action(
                    repository_root=ROOT,
                    model_root=model_root,
                    integrity_evidence=evidence,
                    result_root=result_root,
                    owner_action_receipt=receipt_path,
                    source_action_commit="a" * 40,
                    run_id="phase5-publication-test",
                    instance_id="instance-test",
                    gpu_uuid="GPU-test",
                    ssh_fingerprint_sha256="b" * 64,
                    time_cap_seconds=100,
                    cost_cap_minor_units=100,
                    storage_cap_bytes=1000000,
                    python_executable="python-test",
                    confirm_publication_action=True,
                )
                preflight = json.loads(
                    (result_root / "baseline_g0_preflight_summary.json").read_text(
                        encoding="utf-8"
                    )
                )
        self.assertEqual(child.call_count, 12)
        self.assertEqual(summary["completed_runtime_row_count"], 12)
        self.assertEqual(summary["aggregate"]["total_generate_started_count"], 48)
        self.assertEqual(summary["status"], "completed_descriptive_results")
        self.assertEqual(
            validate_phase5_publication_run_summary(summary),
            summary,
        )
        tampered = copy.deepcopy(summary)
        tampered["aggregate"]["total_generate_started_count"] = 47
        with self.assertRaisesRegex(
            Phase5PublicationActionError,
            "aggregate drifted",
        ):
            validate_phase5_publication_run_summary(tampered)
        self.assertIs(preflight["all_cases_passed"], True)
        self.assertEqual(preflight["case_count"], 4)
        self.assertEqual(preflight["generate_started_count"], 0)
        self.assertEqual(
            summary["baseline_g0_preflight_identity"],
            preflight["preflight_identity"],
        )
        for call in child.call_args_list:
            self.assertEqual(tuple(call.kwargs["provider_evidence_projection_by_node"]), NODE_ORDER)


if __name__ == "__main__":
    unittest.main()
