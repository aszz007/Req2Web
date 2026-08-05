from __future__ import annotations

import base64
import inspect
import json
import sys
import tempfile
from types import SimpleNamespace
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


try:
    import langgraph  # type: ignore[import-not-found]
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise

    class _StubInMemorySaver:
        pass

    class _StubStateGraph:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def add_node(self, *_: object, **__: object) -> None:
            pass

        def add_edge(self, *_: object, **__: object) -> None:
            pass

        def add_conditional_edges(self, *_: object, **__: object) -> None:
            pass

        def compile(self, *_: object, **__: object) -> object:
            return object()

    _langgraph = types.ModuleType("langgraph")
    _langgraph.__path__ = []  # type: ignore[attr-defined]
    _checkpoint = types.ModuleType("langgraph.checkpoint")
    _checkpoint.__path__ = []  # type: ignore[attr-defined]
    _memory = types.ModuleType("langgraph.checkpoint.memory")
    _memory.InMemorySaver = _StubInMemorySaver
    _graph = types.ModuleType("langgraph.graph")
    _graph.END = "__END__"
    _graph.START = "__START__"
    _graph.StateGraph = _StubStateGraph
    sys.modules.update(
        {
            "langgraph": _langgraph,
            "langgraph.checkpoint": _checkpoint,
            "langgraph.checkpoint.memory": _memory,
            "langgraph.graph": _graph,
        }
    )


from req2web_agent.schema import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import (  # noqa: E402
    RetrievalGuidanceBuilder,
)
from req2web_orchestration.model_route import (  # noqa: E402
    MODEL_ROUTE_OUTCOME_SCHEMA_VERSION,
    SCRIPTED_ACCEPTANCE_FAIL_KEY,
    SCRIPTED_ACCEPTANCE_PASS_KEY,
    TierA07bFieldGateReport,
    TierA07bRepairPatch,
)
from req2web_orchestration.phase4_graph import (  # noqa: E402
    phase4_create_portable_authority_state,
    phase4_synthetic_assembler_bindings,
    synthetic_commerce_b_input,
)
from req2web_provider.d17_audit import (  # noqa: E402
    create_d17_path3_pre_invocation_audit_record,
)
from req2web_provider.d17_input_view import (  # noqa: E402
    select_d17_path3_provider_input,
)
from req2web_provider.d17_manifest import D17Path3TierAManifest  # noqa: E402
from req2web_provider.d17_serializer import (  # noqa: E402
    serialize_d17_path3_local_request,
)
from req2web_provider.local_qwen_provider import (  # noqa: E402
    prepare_local_qwen_provider_interface,
)
from req2web_provider.semantic_candidate import (  # noqa: E402
    CanonicalPageSpecAssembler,
    ProviderRawResponse,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
import req2web_runtime.phase4_fresh_delivery as fresh_delivery  # noqa: E402
from req2web_runtime.phase4_fresh_delivery import (  # noqa: E402
    PHASE4_FRESH_ROUTE_OUTCOME_SCHEMA_VERSION,
    PHASE4_GRAPH_BOUND_DELIVERY_MATERIALS_SCHEMA_VERSION,
    Phase4FreshDeliveryInput,
    _write_once,
    build_phase4_graph_bound_delivery_materials,
    run_phase4_fresh_delivery,
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _phase4_context() -> AgentContextBundle:
    references = {
        "requirement": [{"kind": "workflow", "uri": "fixtures/phase4/requirement.json"}],
        "ui_reference": [{"kind": "screenshot", "uri": "fixtures/phase4/ui-reference.png"}],
        "interaction_flow": [{"kind": "step_screenshot", "uri": "fixtures/phase4/flow-step.png"}],
        "implementation": [{"kind": "html", "uri": "fixtures/phase4/implementation.html"}],
        "validation": [{"kind": "issue", "uri": "https://example.test/phase4/validation"}],
    }
    labels = {
        "requirement": ("Commerce workflow", "search filter cart checkout form"),
        "ui_reference": ("Mobile commerce reference", "search results cart form button"),
        "interaction_flow": ("Checkout interaction flow", "click search open checkout submit retry"),
        "implementation": ("Responsive commerce implementation", "responsive mobile form cart checkout"),
        "validation": ("Recoverable validation", "error message retains valid input and allows retry"),
    }
    retrieval_results = {
        role: [
            {
                "score": 1.0,
                "doc_id": f"{role}:phase4:synthetic-commerce",
                "role": role,
                "dataset": "phase4_synthetic_fixture",
                "subset": "p4_02a",
                "sample_id": f"synthetic-commerce-{role}",
                "title": labels[role][0],
                "summary": labels[role][1],
                "references": references[role],
            }
        ]
        for role in ROLE_ORDER
    }
    return AgentContextBundle(
        original_requirement=(
            "Create a mobile commerce page with product search, cart, delivery "
            "form, checkout, and recoverable validation errors."
        ),
        requirement_summary=(
            "Build a synthetic mobile commerce flow from product search through "
            "recoverable checkout validation."
        ),
        target_device="mobile",
        task_type="ecommerce",
        constraints=[
            "Use only text and generic placeholders.",
            "Retain valid form input after validation failure.",
        ],
        use_cases=[
            UseCase(
                "UC-01",
                "Find products",
                "Shopper",
                "Search and filter products",
                "Relevant products are visible",
            ),
            UseCase(
                "UC-02",
                "Submit checkout",
                "Shopper",
                "Enter delivery details and submit",
                "Invalid fields can be corrected without losing valid input",
            ),
        ],
        retrieval_queries={role: f"phase4 synthetic {role}" for role in ROLE_ORDER},
        retrieval_results=retrieval_results,
    )


def _candidate_bytes() -> bytes:
    value = json.loads(
        (
            ROOT
            / "phase4_fresh_integrated_revalidation_savepoint_20260804"
            / "candidate_composition_record.json"
        ).read_text(encoding="utf-8")
    )
    return base64.b64decode(
        value["model_semantic_candidate_canonical_b64"],
        validate=True,
    )


def _field_gate_pass_candidate(raw: bytes) -> bytes:
    value = json.loads(raw.decode("utf-8"))
    first = min(value["components"], key=lambda item: item["stable_id"])
    first["label"] = "component-label-" + first["stable_id"]
    return _canonical_bytes(value)


class Phase4FreshDeliveryTests(unittest.TestCase):
    SOURCE_ROOT = ROOT / "phase4_fresh_integrated_revalidation_savepoint_20260804"

    def setUp(self) -> None:
        if not self.SOURCE_ROOT.is_dir():
            self.skipTest("tracked fresh-integrated revalidation savepoint is unavailable")
        self.context = _phase4_context()
        self.guidance = RetrievalGuidanceBuilder().build(self.context)

    def tearDown(self) -> None:
        return None

    def test_source_is_fresh_and_normalized_route_is_not_historical_model_route(
        self,
    ) -> None:
        source = Phase4FreshDeliveryInput.from_result_root(
            self.SOURCE_ROOT,
            context=self.context,
            guidance=self.guidance,
        )
        self.assertEqual(source.source_result["claim_boundary"].split(";")[0], "amended_normalized_agent_chain_revalidation_only")
        self.assertEqual(
            source.source_result["schema_version"],
            "req2web.phase4.local_qwen.fresh_integrated_revalidation.result.v1",
        )
        self.assertFalse(source.raw_model_contract_success)
        self.assertTrue(source.normalized_node_contract_success)
        self.assertTrue(source.agent_chain_system_output_usable)
        self.assertIsNotNone(source.normalization_receipt)
        self.assertEqual(
            source.normalization_receipt["raw_model_contract_success"],
            False,
        )
        self.assertEqual(
            source.normalization_receipt["normalized_node_contract_success"],
            True,
        )
        self.assertNotEqual(
            PHASE4_FRESH_ROUTE_OUTCOME_SCHEMA_VERSION,
            MODEL_ROUTE_OUTCOME_SCHEMA_VERSION,
        )

    def test_source_context_and_guidance_are_live_bound(self) -> None:
        source = Phase4FreshDeliveryInput.from_result_root(
            self.SOURCE_ROOT,
            context=self.context,
            guidance=self.guidance,
        )
        source.validate_context_guidance(self.context, self.guidance)


class Phase4GraphBoundDeliveryMaterialTests(unittest.TestCase):
    def test_builder_identities_are_exact_graph_assembler_bindings(self) -> None:
        state = phase4_create_portable_authority_state(
            synthetic_commerce_b_input()
        )
        graph_context, graph_guidance = phase4_synthetic_assembler_bindings(
            state
        )
        reference = SimpleNamespace(
            package_id="graph-bound-g0-package",
            page_id="graph-bound-page",
            to_dict=lambda: {
                "package_id": "graph-bound-g0-package",
                "page_id": "graph-bound-page",
            },
        )
        fallback = SimpleNamespace(
            case_id="path3-commerce-checkout",
            package_id=reference.package_id,
            page_id=reference.page_id,
            to_dict=lambda: {
                "case_id": "path3-commerce-checkout",
                "package_id": reference.package_id,
                "page_id": reference.page_id,
            },
        )
        material_root = (
            Path(tempfile.gettempdir())
            / f"req2web-p4-05-graph-bound-{uuid.uuid4().hex}"
        )
        with patch.object(
            fresh_delivery,
            "_build_same_context_g0",
            return_value=(
                object(),
                reference,
                fallback,
                material_root / "fallback-snapshot",
            ),
        ), patch.object(
            fresh_delivery,
            "_write_once",
            return_value=None,
        ), patch.object(
            Path,
            "mkdir",
            return_value=None,
        ):
            materials = build_phase4_graph_bound_delivery_materials(
                graph_state=state,
                material_root=material_root,
            )

        self.assertEqual(
            materials.binding["schema_version"],
            PHASE4_GRAPH_BOUND_DELIVERY_MATERIALS_SCHEMA_VERSION,
        )
        self.assertEqual(
            materials.context.to_dict(),
            graph_context.to_dict(),
        )
        self.assertEqual(
            materials.guidance.to_dict(),
            graph_guidance.to_dict(),
        )
        self.assertEqual(
            materials.binding["context_identity"],
            fresh_delivery._json_identity(
                graph_context.to_dict(),
                revision="req2web.agent.context.v1",
            ),
        )
        self.assertEqual(
            materials.binding["guidance_identity"],
            fresh_delivery._json_identity(
                graph_guidance.to_dict(),
                revision="req2web.retrieval.guidance.v1",
            ),
        )
        self.assertTrue(
            materials.binding["graph_assembler_bindings_exact"]
        )

    def test_shared_builder_and_clis_do_not_reference_stage3_fixed_loader(
        self,
    ) -> None:
        paths = (
            ROOT / "src" / "req2web_runtime" / "phase4_fresh_delivery.py",
            ROOT / "src" / "req2web_runtime" / "phase4_remote_qwen_fresh_integrated.py",
            ROOT / "scripts" / "run_phase4_fresh_delivery.py",
        )
        for path in paths:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "build_fixed_trusted_remote_case_materials_v2",
                source,
            )
            self.assertNotIn(
                "autodl_trusted_remote_case_loader",
                source,
            )


class Phase4FreshDeliveryTerminalIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_savepoint = (
            ROOT / "phase4_fresh_integrated_revalidation_savepoint_20260804"
        )
        if not source_savepoint.is_dir():
            raise unittest.SkipTest(
                "tracked fresh-integrated revalidation savepoint is unavailable"
            )
        cls.context = _phase4_context()
        cls.guidance = RetrievalGuidanceBuilder().build(cls.context)

        manifest = D17Path3TierAManifest.create(
            structural_signal_names=(
                "has_search",
                "has_filter_controls",
                "has_cart_panel",
                "has_checkout_form",
                "has_inline_validation",
            )
        )
        selected = select_d17_path3_provider_input(
            cls.context,
            manifest,
            original_requirement_source_class="synthetic",
        )
        local_request = serialize_d17_path3_local_request(
            cls.context,
            selected,
            manifest,
        )
        audit = create_d17_path3_pre_invocation_audit_record(
            cls.context,
            manifest,
            selected,
            local_request,
        )
        preparation = prepare_local_qwen_provider_interface(
            cls.context,
            manifest,
            selected,
            local_request,
            audit,
        )
        cls.live_chain = {
            "manifest": manifest,
            "selected": selected,
            "local_request": local_request,
            "pre_invocation_audit": audit,
            "local_qwen_preparation": preparation,
        }
        cls.raw_repair_candidate = _candidate_bytes()
        cls.raw_pass_candidate = _field_gate_pass_candidate(
            cls.raw_repair_candidate
        )

    def _dispatch(
        self,
        *,
        raw_candidate: bytes,
        acceptance_key: str,
    ) -> tuple[object, dict[str, object]]:
        assembled = CanonicalPageSpecAssembler().assemble(
            ProviderRawResponse.from_bytes(raw_candidate),
            self.context,
            self.guidance,
        )
        assembled.validate()
        source = SimpleNamespace(
            case_id="path3-commerce-checkout",
            request_id="p4-05-focused-test-request",
            assembled=assembled,
            validate_context_guidance=lambda *_: None,
        )
        route = SimpleNamespace(
            outcome_id="phase4-fresh-route-focused",
            execution_branch="phase4_fresh_integrated_assembled",
            validate_against=lambda **_: None,
            canonical_bytes=lambda: b'{"route":"focused"}',
        )
        reference = SimpleNamespace(
            package_id="g0-package-focused",
            page_id=assembled.page_spec.page_id,
            validate_against=lambda *_args, **_kwargs: None,
        )
        fallback_record = SimpleNamespace(
            case_id=source.case_id,
            package_id=reference.package_id,
            page_id=reference.page_id,
            validate_against=lambda *_args, **_kwargs: None,
        )
        captured: dict[str, object] = {}

        def first_pass(_self: object, **kwargs: object) -> object:
            captured["runner"] = "tier_a_07a"
            captured["acceptance_fixture"] = kwargs[
                "scripted_acceptance_fixture"
            ]
            status = (
                "fallback_delivery"
                if kwargs["scripted_acceptance_fixture"].fixture_key
                == SCRIPTED_ACCEPTANCE_FAIL_KEY
                else "first_pass_success"
            )
            return {"status": status}

        def one_repair(_self: object, **kwargs: object) -> object:
            captured["runner"] = "tier_a_07b"
            captured["field_gate_report"] = TierA07bFieldGateReport.from_bytes(
                kwargs["field_gate_report"]
            )
            captured["repair_patch"] = TierA07bRepairPatch.from_bytes(
                kwargs["repair_patch"]
            )
            return {"status": "recovered_success"}

        receipt = SimpleNamespace(
            validate=lambda: None,
            canonical_bytes=lambda: b'{"receipt":"focused"}',
        )
        with patch.object(
            Phase4FreshDeliveryInput,
            "from_result_root",
            return_value=source,
        ), patch.object(
            fresh_delivery.Phase4FreshRouteOutcome,
            "create",
            return_value=route,
        ), patch.object(
            fresh_delivery,
            "_fresh_route_validate",
            return_value=None,
        ), patch.object(
            fresh_delivery,
            "_build_phase4_fresh_delivery_runners",
            return_value=(first_pass, one_repair),
        ), patch.object(
            fresh_delivery.Phase4FreshDeliveryReceipt,
            "create",
            return_value=receipt,
        ), patch.object(
            fresh_delivery,
            "_write_once",
            return_value=None,
        ):
            result = run_phase4_fresh_delivery(
                source_root=ROOT,
                delivery_root=(
                    Path(tempfile.gettempdir())
                    / f"req2web-p4-05-dispatch-{uuid.uuid4().hex}"
                ),
                context=self.context,
                guidance=self.guidance,
                package=object(),
                frozen_g0_reference=reference,
                fallback_record=fallback_record,
                fallback_snapshot_dir=Path(tempfile.gettempdir()),
                scripted_acceptance_fixture=acceptance_key,
                **self.live_chain,
            )
        return result, captured

    def test_automatic_field_gate_pass_routes_a07a(self) -> None:
        receipt, captured = self._dispatch(
            raw_candidate=self.raw_pass_candidate,
            acceptance_key=SCRIPTED_ACCEPTANCE_PASS_KEY,
        )
        self.assertIsNotNone(receipt)
        self.assertEqual(captured["runner"], "tier_a_07a")
        self.assertEqual(
            captured["acceptance_fixture"].fixture_key,
            SCRIPTED_ACCEPTANCE_PASS_KEY,
        )

    def test_automatic_field_gate_repair_routes_a07b_once(self) -> None:
        receipt, captured = self._dispatch(
            raw_candidate=self.raw_repair_candidate,
            acceptance_key=SCRIPTED_ACCEPTANCE_PASS_KEY,
        )
        self.assertIsNotNone(receipt)
        self.assertEqual(captured["runner"], "tier_a_07b")
        report = captured["field_gate_report"]
        repair_patch = captured["repair_patch"]
        self.assertEqual(report.decision, "repair")
        self.assertTrue(report.repair_eligible)
        self.assertEqual(repair_patch.attempt_index, 1)
        self.assertEqual(
            repair_patch.operations,
            ((report.reported_field, report.expected),),
        )
        self.assertEqual(repair_patch.report_id, report.report_id)
        self.assertEqual(repair_patch.report_sha256, report.sha256())
        self.assertEqual(
            repair_patch.first_page_spec_sha256,
            report.first_page_spec_sha256,
        )

    def test_acceptance_failure_delivers_same_case_g0_fallback(self) -> None:
        receipt, captured = self._dispatch(
            raw_candidate=self.raw_pass_candidate,
            acceptance_key=SCRIPTED_ACCEPTANCE_FAIL_KEY,
        )
        self.assertIsNotNone(receipt)
        self.assertEqual(captured["runner"], "tier_a_07a")
        self.assertEqual(
            captured["acceptance_fixture"].fixture_key,
            SCRIPTED_ACCEPTANCE_FAIL_KEY,
        )

    def test_callers_cannot_supply_field_gate_or_repair_patch(self) -> None:
        parameters = inspect.signature(run_phase4_fresh_delivery).parameters
        self.assertNotIn("field_gate_report", parameters)
        self.assertNotIn("repair_patch", parameters)
        for required in (
            "manifest",
            "selected",
            "local_request",
            "pre_invocation_audit",
            "local_qwen_preparation",
        ):
            self.assertIs(parameters[required].default, inspect.Parameter.empty)

    def test_write_once_can_persist_a_real_system_temp_artifact(self) -> None:
        path = (
            Path(tempfile.gettempdir())
            / f"req2web-p4-05-write-once-{uuid.uuid4().hex}.json"
        )
        raw = b'{"status":"writable-temp-integration"}'
        try:
            _write_once(path, raw)
            _write_once(path, raw)
            self.assertEqual(path.read_bytes(), raw)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
