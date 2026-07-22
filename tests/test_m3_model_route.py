from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import ast
import inspect
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_evaluation.frozen_g0_reference import freeze_verified_g0_package_reference  # noqa: E402
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
import req2web_orchestration.model_route as model_route_module  # noqa: E402
from req2web_orchestration.model_route import (  # noqa: E402
    ModelRouteFailure,
    ModelRouteOutcome,
    ModelRouteOutcomeError,
    SCRIPTED_FIXTURE_ASSEMBLY_INVALID_KEY,
    SCRIPTED_FIXTURE_REGISTRY_ID,
    SCRIPTED_FIXTURE_REGISTRY_SCHEMA_VERSION,
    SCRIPTED_FIXTURE_REGISTRY_SHA256,
    SCRIPTED_FIXTURE_SEMANTIC_INVALID_KEY,
    SCRIPTED_FIXTURE_VALID_CANDIDATE_KEY,
    ScriptedLocalFixture,
    TierAModelRouteOrchestrator,
    validate_serialized_model_route_outcome,
)
from req2web_provider.d17_audit import create_d17_path3_pre_invocation_audit_record  # noqa: E402
from req2web_provider.d17_input_view import select_d17_path3_provider_input  # noqa: E402
from req2web_provider.d17_manifest import D17Path3TierAManifest  # noqa: E402
from req2web_provider.d17_serializer import serialize_d17_path3_local_request  # noqa: E402
from req2web_provider.local_qwen_provider import prepare_local_qwen_provider_interface  # noqa: E402
from req2web_provider.semantic_candidate import ProviderRawResponse, parse_provider_raw_response  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from test_m3_semantic_candidate_assembly import build_context, candidate_payload, raw_bytes  # noqa: E402


TEST_ROOT = ROOT / "outputs" / "_m3_model_route_tests"
SEMANTIC_INVALID_BYTES = b'{"not":"a semantic candidate"}'


def assembly_invalid_bytes() -> bytes:
    payload = deepcopy(candidate_payload())
    for path in (
        payload["sections"][0]["use_case_ids"],
        payload["interactions"][0]["use_case_ids"],
        payload["acceptance_checks"][0]["use_case_ids"],
    ):
        path[0] = "UC-99"
    payload["use_case_mappings"][0]["use_case_id"] = "UC-99"
    return raw_bytes(payload)


def build_g0(work: Path, *, context=None):
    context = context or build_context()
    guidance = RetrievalGuidanceBuilder().build(context)
    baseline = PageSpecBuilder().build(context)
    guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
    ablations = {
        role: RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,))
        for role in ROLE_ORDER
    }
    render = DeterministicPageRenderer().render(guided.page_spec, work / "render")
    consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
    influence = RetrievalInfluenceChecker().check(
        context,
        guidance,
        baseline,
        guided,
        ablations,
        render,
    )
    package = DeterministicRetrievalEnhancedResultPackager().package(
        context,
        guidance,
        guided,
        guided.page_spec,
        render,
        consistency,
        influence,
        work / "package",
    )
    return (
        package,
        context,
        guidance,
        freeze_verified_g0_package_reference(package, context, guidance),
    )


def build_chain(context):
    manifest = D17Path3TierAManifest.create(
        structural_signal_names=("has_form", "has_status")
    )
    selected = select_d17_path3_provider_input(
        context,
        manifest,
        original_requirement_source_class="synthetic",
    )
    request = serialize_d17_path3_local_request(context, selected, manifest)
    audit = create_d17_path3_pre_invocation_audit_record(
        context,
        manifest,
        selected,
        request,
    )
    preparation = prepare_local_qwen_provider_interface(
        context,
        manifest,
        selected,
        request,
        audit,
    )
    return manifest, selected, request, audit, preparation


def resign(payload: dict[str, object]) -> dict[str, object]:
    root = dict(payload)
    root.pop("outcome_id")
    payload["outcome_id"] = (
        model_route_module._MODEL_ROUTE_ID_PREFIX
        + model_route_module._sha256(model_route_module._canonical_json_bytes(root))
    )
    return payload


class TierAModelRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = TEST_ROOT / f"case-{uuid4().hex}"
        self.work.mkdir(parents=True)
        self.package, self.context, self.guidance, self.reference = build_g0(
            self.work / "g0"
        )
        (
            self.manifest,
            self.selected,
            self.request,
            self.audit,
            self.preparation,
        ) = build_chain(self.context)
        self.route = TierAModelRouteOrchestrator()

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def run_route(self, **changes):
        values = {
            "frozen_g0_reference": self.reference,
            "package": self.package,
            "context": self.context,
            "guidance": self.guidance,
            "manifest": self.manifest,
            "selected": self.selected,
            "local_request": self.request,
            "pre_invocation_audit": self.audit,
            "local_qwen_preparation": self.preparation,
        }
        values.update(changes)
        return self.route.run(**values)

    def replay(self, outcome, **changes) -> None:
        values = {
            "frozen_g0_reference": self.reference,
            "package": self.package,
            "context": self.context,
            "guidance": self.guidance,
            "manifest": self.manifest,
            "selected": self.selected,
            "local_request": self.request,
            "pre_invocation_audit": self.audit,
            "local_qwen_preparation": self.preparation,
            "execution_branch": (
                "scripted_local_fixture"
                if outcome.execution_branch == "scripted_local_fixture"
                else "tier_b_authorization_probe"
            ),
        }
        values.update(changes)
        outcome.validate_against(**values)

    def fixture(self, key=SCRIPTED_FIXTURE_VALID_CANDIDATE_KEY, raw=None):
        raw_bytes_value = raw
        if raw_bytes_value is None:
            raw_bytes_value = {
                SCRIPTED_FIXTURE_VALID_CANDIDATE_KEY: raw_bytes(candidate_payload()),
                SCRIPTED_FIXTURE_SEMANTIC_INVALID_KEY: SEMANTIC_INVALID_BYTES,
                SCRIPTED_FIXTURE_ASSEMBLY_INVALID_KEY: assembly_invalid_bytes(),
            }[key]
        return ScriptedLocalFixture.create(
            key,
            ProviderRawResponse.from_bytes(raw_bytes_value),
        )

    def assert_failure(self, outcome, code: str) -> None:
        self.assertEqual(outcome.disposition, "fail_closed")
        self.assertIsNotNone(outcome.failure)
        self.assertEqual(outcome.failure.code, code)
        declarations = outcome.to_dict()["execution_declarations"]
        self.assertFalse(declarations["fallback_materialized"])
        self.assertEqual(declarations["delivery_route"], "not_executed")
        self.assertEqual(declarations["repair_status"], "not_executed")
        self.assertFalse(declarations["retry_performed"])

    def assert_replay_rejected(self, outcome, **changes) -> None:
        with self.assertRaises(ModelRouteOutcomeError) as raised:
            self.replay(outcome, **changes)
        self.assertEqual(raised.exception.code, "failure_replay_invalid")
        self.assertEqual(raised.exception.envelope.payload_disclosure, "none")

    def test_g0_validation_precedes_every_later_operation(self) -> None:
        invalid_reference = replace(self.reference, reference_id="frozen-g0-forged")
        with patch.object(
            D17Path3TierAManifest,
            "validate",
            side_effect=AssertionError("D17 touched"),
        ), patch.object(
            model_route_module,
            "invoke_local_qwen_provider",
            side_effect=AssertionError("gate touched"),
        ):
            outcome = self.run_route(
                frozen_g0_reference=invalid_reference,
                execution_branch="scripted_local_fixture",
                scripted_local_fixture=self.fixture(),
            )
        self.assert_failure(outcome, "g0_reference_validation_failed")
        self.assertEqual(outcome.completed_steps, ())
        self.assertIsNone(outcome.frozen_g0_reference)
        self.replay(outcome, frozen_g0_reference=invalid_reference)
        self.assert_replay_rejected(outcome, frozen_g0_reference=self.reference)

    def test_registry_freezes_fixture_provenance_and_exact_bytes(self) -> None:
        fixture = self.fixture()
        projection = fixture.safe_projection()
        self.assertEqual(projection.registry_schema_version, SCRIPTED_FIXTURE_REGISTRY_SCHEMA_VERSION)
        self.assertEqual(projection.registry_id, SCRIPTED_FIXTURE_REGISTRY_ID)
        self.assertEqual(projection.registry_sha256, SCRIPTED_FIXTURE_REGISTRY_SHA256)
        self.assertEqual(projection.registry_entry_key, SCRIPTED_FIXTURE_VALID_CANDIDATE_KEY)
        self.assertEqual(projection.source_class, "synthetic")
        valid_raw = ProviderRawResponse.from_bytes(raw_bytes(candidate_payload()))
        for key in ("unregistered_fixture", SCRIPTED_FIXTURE_SEMANTIC_INVALID_KEY):
            with self.subTest(key=key), self.assertRaises(ModelRouteOutcomeError):
                ScriptedLocalFixture.create(key, valid_raw)
        with self.assertRaises(TypeError):
            ScriptedLocalFixture.create(
                SCRIPTED_FIXTURE_VALID_CANDIDATE_KEY,
                valid_raw,
                source_class="synthetic",
            )
        with self.assertRaises(ModelRouteOutcomeError):
            replace(fixture, source_class="project_authored").validate()
        self.assertNotIn("source_class", inspect.signature(ScriptedLocalFixture.create).parameters)

    def test_registry_authority_ignores_runtime_report_global_rebinding(self) -> None:
        forged_payload = deepcopy(candidate_payload())
        forged_payload["title"] = "Runtime-forged candidate"
        forged_raw = ProviderRawResponse.from_bytes(raw_bytes(forged_payload))
        forged_key = "runtime_forged_candidate_v1"
        forged_entry = model_route_module._ScriptedFixtureRegistryEntry(
            fixture_key=forged_key,
            raw_response_sha256=forged_raw.sha256,
            raw_response_byte_length=len(forged_raw.raw_bytes),
            source_class="synthetic",
        )
        forged_entries = (
            *model_route_module._SCRIPTED_FIXTURE_REGISTRY_ENTRIES,
            forged_entry,
        )
        forged_root = {
            "schema_version": SCRIPTED_FIXTURE_REGISTRY_SCHEMA_VERSION,
            "entries": [entry.to_dict() for entry in forged_entries],
            "immutable": True,
            "runtime_registration_api": "none",
            "payload_bytes_stored": False,
        }
        forged_sha256 = model_route_module._sha256(
            model_route_module._canonical_json_bytes(forged_root)
        )
        forged_id = model_route_module._SCRIPTED_FIXTURE_REGISTRY_ID_PREFIX + forged_sha256

        with patch.object(
            model_route_module,
            "_SCRIPTED_FIXTURE_REGISTRY_ENTRIES",
            forged_entries,
        ), patch.object(
            model_route_module,
            "SCRIPTED_FIXTURE_REGISTRY_SHA256",
            forged_sha256,
        ), patch.object(
            model_route_module,
            "SCRIPTED_FIXTURE_REGISTRY_ID",
            forged_id,
        ):
            self.assertEqual(model_route_module.SCRIPTED_FIXTURE_REGISTRY_ID, forged_id)
            fixture = self.fixture()
            projection = fixture.safe_projection()
            projection.validate()
            self.assertEqual(projection.registry_id, SCRIPTED_FIXTURE_REGISTRY_ID)
            self.assertEqual(projection.registry_sha256, SCRIPTED_FIXTURE_REGISTRY_SHA256)
            for attempted_key in (
                forged_key,
                SCRIPTED_FIXTURE_VALID_CANDIDATE_KEY,
            ):
                with self.subTest(attempted_key=attempted_key):
                    with self.assertRaises(ModelRouteOutcomeError):
                        ScriptedLocalFixture.create(attempted_key, forged_raw)

            outcome = self.run_route(
                execution_branch="scripted_local_fixture",
                scripted_local_fixture=fixture,
            )
            self.assertEqual(outcome.disposition, "scripted_fixture_assembled")
            declarations = outcome.to_dict()["execution_declarations"]
            self.assertEqual(
                declarations["scripted_fixture_registry_id"],
                SCRIPTED_FIXTURE_REGISTRY_ID,
            )
            self.assertEqual(
                declarations["scripted_fixture_registry_sha256"],
                SCRIPTED_FIXTURE_REGISTRY_SHA256,
            )
            self.replay(
                outcome,
                execution_branch="scripted_local_fixture",
                scripted_local_fixture=fixture,
            )
            replay = validate_serialized_model_route_outcome(outcome.to_dict())
            self.replay(
                replay,
                execution_branch="scripted_local_fixture",
                scripted_local_fixture=fixture,
            )

    def test_authorization_probe_requires_exact_current_gate_outcome(self) -> None:
        outcome = self.run_route()
        self.assert_failure(outcome, "tier_b_unapproved")
        self.replay(outcome, execution_branch="tier_b_authorization_probe")
        with patch.object(model_route_module, "invoke_local_qwen_provider", return_value=None):
            self.assert_replay_rejected(
                outcome,
                execution_branch="tier_b_authorization_probe",
            )

    def test_unexpected_gate_outcome_replays_only_when_currently_reproduced(self) -> None:
        with patch.object(model_route_module, "invoke_local_qwen_provider", return_value=None):
            outcome = self.run_route()
        self.assert_failure(outcome, "local_qwen_invocation_unexpected_return")
        self.assert_replay_rejected(
            outcome,
            execution_branch="tier_b_authorization_probe",
        )
        with patch.object(model_route_module, "invoke_local_qwen_provider", return_value=None):
            self.replay(outcome, execution_branch="tier_b_authorization_probe")

    def test_each_genuine_failure_stage_replays_and_valid_stage_cannot_be_falsely_claimed(self) -> None:
        cases = (
            ("d17_manifest_invalid", {"manifest": object()}),
            ("d17_input_selection_invalid", {"selected": object()}),
            ("d17_local_request_invalid", {"local_request": object()}),
            ("d17_pre_invocation_audit_invalid", {"pre_invocation_audit": object()}),
            ("local_qwen_preparation_invalid", {"local_qwen_preparation": object()}),
        )
        for code, changes in cases:
            with self.subTest(code=code):
                outcome = self.run_route(**changes)
                self.assert_failure(outcome, code)
                self.replay(outcome, **changes)
                self.assert_replay_rejected(outcome)

        invalid_branch = "unauthorized_branch"
        branch_outcome = self.run_route(execution_branch=invalid_branch)
        self.assert_failure(branch_outcome, "route_branch_invalid")
        self.replay(branch_outcome, execution_branch=invalid_branch)
        self.assert_replay_rejected(
            branch_outcome,
            execution_branch="tier_b_authorization_probe",
        )

        invalid_fixture = object()
        fixture_outcome = self.run_route(
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=invalid_fixture,
        )
        self.assert_failure(fixture_outcome, "scripted_fixture_provenance_invalid")
        self.replay(
            fixture_outcome,
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=invalid_fixture,
        )
        self.assert_replay_rejected(
            fixture_outcome,
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=self.fixture(),
        )

    def test_early_failure_shape_rejects_future_preparation_and_wrong_branch(self) -> None:
        rule = model_route_module._FAILURES["d17_manifest_invalid"]
        with self.assertRaises(ModelRouteOutcomeError):
            ModelRouteOutcome.create(
                disposition="fail_closed",
                execution_branch="tier_b_authorization_probe",
                completed_steps=rule.completed_steps,
                failure=ModelRouteFailure.fixed("d17_manifest_invalid"),
                frozen_g0_reference=self.reference,
            )
        with self.assertRaises(ModelRouteOutcomeError):
            ModelRouteOutcome.create(
                disposition="fail_closed",
                execution_branch="not_selected",
                completed_steps=rule.completed_steps,
                failure=ModelRouteFailure.fixed("d17_manifest_invalid"),
                frozen_g0_reference=self.reference,
                local_qwen_preparation=self.preparation,
            )

    def test_cross_case_fully_rehashed_chain_fails_at_exact_stage(self) -> None:
        other_context = build_context()
        other_context.original_requirement = "A distinct synthetic same-shape case."
        _, other_selected, other_request, other_audit, other_preparation = build_chain(other_context)
        for code, changes in (
            ("d17_input_selection_invalid", {"selected": other_selected}),
            ("d17_local_request_invalid", {"local_request": other_request}),
            ("d17_pre_invocation_audit_invalid", {"pre_invocation_audit": other_audit}),
            ("local_qwen_preparation_invalid", {"local_qwen_preparation": other_preparation}),
        ):
            with self.subTest(code=code):
                outcome = self.run_route(**changes)
                self.assert_failure(outcome, code)
                self.replay(outcome, **changes)

    def test_registered_parse_and_assembly_failures_replay_exactly(self) -> None:
        semantic_fixture = self.fixture(SCRIPTED_FIXTURE_SEMANTIC_INVALID_KEY)
        semantic_outcome = self.run_route(
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=semantic_fixture,
        )
        self.assert_failure(semantic_outcome, "semantic_candidate_invalid")
        self.replay(
            semantic_outcome,
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=semantic_fixture,
        )

        assembly_fixture = self.fixture(SCRIPTED_FIXTURE_ASSEMBLY_INVALID_KEY)
        assembly_outcome = self.run_route(
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=assembly_fixture,
        )
        self.assert_failure(assembly_outcome, "page_spec_assembly_invalid")
        self.replay(
            assembly_outcome,
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=assembly_fixture,
        )

    def test_valid_live_objects_and_fixture_cannot_be_falsely_reported_as_failures(self) -> None:
        g0_outcome = ModelRouteOutcome.create(
            disposition="fail_closed",
            execution_branch="not_selected",
            completed_steps=(),
            failure=ModelRouteFailure.fixed("g0_reference_validation_failed"),
        )
        self.assert_replay_rejected(g0_outcome)

        manifest_rule = model_route_module._FAILURES["d17_manifest_invalid"]
        manifest_outcome = ModelRouteOutcome.create(
            disposition="fail_closed",
            execution_branch=manifest_rule.execution_branch,
            completed_steps=manifest_rule.completed_steps,
            failure=ModelRouteFailure.fixed("d17_manifest_invalid"),
            frozen_g0_reference=self.reference,
        )
        self.assert_replay_rejected(manifest_outcome)

        fixture = self.fixture()
        projection = fixture.safe_projection()
        raw_response = fixture.raw_response
        semantic_rule = model_route_module._FAILURES["semantic_candidate_invalid"]
        semantic_outcome = ModelRouteOutcome.create(
            disposition="fail_closed",
            execution_branch=semantic_rule.execution_branch,
            completed_steps=semantic_rule.completed_steps,
            failure=ModelRouteFailure.fixed("semantic_candidate_invalid"),
            frozen_g0_reference=self.reference,
            local_qwen_preparation=self.preparation,
            scripted_local_fixture=projection,
            raw_response_sha256=raw_response.sha256,
            raw_response_byte_length=len(raw_response.raw_bytes),
        )
        self.assert_replay_rejected(
            semantic_outcome,
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=fixture,
        )

        candidate = parse_provider_raw_response(raw_response)
        assembly_rule = model_route_module._FAILURES["page_spec_assembly_invalid"]
        assembly_outcome = ModelRouteOutcome.create(
            disposition="fail_closed",
            execution_branch=assembly_rule.execution_branch,
            completed_steps=assembly_rule.completed_steps,
            failure=ModelRouteFailure.fixed("page_spec_assembly_invalid"),
            frozen_g0_reference=self.reference,
            local_qwen_preparation=self.preparation,
            scripted_local_fixture=projection,
            raw_response_sha256=raw_response.sha256,
            raw_response_byte_length=len(raw_response.raw_bytes),
            model_semantic_candidate_sha256=candidate.sha256(),
        )
        self.assert_replay_rejected(
            assembly_outcome,
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=fixture,
        )

    def test_scripted_success_is_canonical_safe_and_registry_bound(self) -> None:
        fixture = self.fixture()
        outcome = self.run_route(
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=fixture,
        )
        self.assertEqual(outcome.disposition, "scripted_fixture_assembled")
        self.assertIsNone(outcome.failure)
        self.assertIsNotNone(outcome.assembled_page_spec)
        self.assertEqual(outcome.artifacts["assembled_page_id"], self.reference.page_id)
        declarations = outcome.to_dict()["execution_declarations"]
        self.assertEqual(declarations["scripted_fixture_status"], "synthetic_scripted_only")
        self.assertEqual(declarations["scripted_fixture_registry_id"], SCRIPTED_FIXTURE_REGISTRY_ID)
        self.assertEqual(declarations["scripted_fixture_registry_sha256"], SCRIPTED_FIXTURE_REGISTRY_SHA256)
        self.replay(
            outcome,
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=fixture,
        )
        replay = validate_serialized_model_route_outcome(outcome.canonical_bytes())
        self.assertEqual(replay.to_dict(), outcome.to_dict())
        self.replay(
            replay,
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=fixture,
        )
        encoded = outcome.canonical_bytes().decode("utf-8")
        self.assertNotIn(self.context.original_requirement, encoded)
        self.assertNotIn(self.context.requirement_summary, encoded)
        self.assertNotIn(fixture.raw_response.raw_bytes.decode("utf-8"), encoded)
        self.assertNotIn(str(self.package.package_dir), encoded)

    def test_assembler_authority_is_not_replaceable_or_injectable(self) -> None:
        self.assertFalse(hasattr(self.route, "assembler"))
        with self.assertRaises(AttributeError):
            self.route.assembler = object()
        with self.assertRaises(TypeError):
            TierAModelRouteOrchestrator(assembler=object())
        fixture = self.fixture()
        outcome = self.run_route(
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=fixture,
        )
        self.assertEqual(outcome.disposition, "scripted_fixture_assembled")
        self.assertEqual(outcome.assembled_page_spec.page_spec.page_id, self.reference.page_id)

    def test_module_assembly_helper_rebinding_cannot_change_success(self) -> None:
        fixture = self.fixture()
        expected_candidate = parse_provider_raw_response(fixture.raw_response)
        with patch.object(
            model_route_module,
            "_assemble_canonically",
            side_effect=AssertionError("replaceable helper was called"),
        ) as forged_helper:
            route = TierAModelRouteOrchestrator()
            original_route = self.route
            self.route = route
            try:
                outcome = self.run_route(
                    execution_branch="scripted_local_fixture",
                    scripted_local_fixture=fixture,
                )
                self.replay(
                    outcome,
                    execution_branch="scripted_local_fixture",
                    scripted_local_fixture=fixture,
                )
            finally:
                self.route = original_route
        forged_helper.assert_not_called()
        self.assertEqual(outcome.disposition, "scripted_fixture_assembled")
        self.assertEqual(outcome.assembled_page_spec.candidate, expected_candidate)
        self.assertEqual(outcome.assembled_page_spec.page_spec.title, expected_candidate.title)

    def test_module_assembler_class_rebinding_cannot_change_success(self) -> None:
        fixture = self.fixture()
        expected_candidate = parse_provider_raw_response(fixture.raw_response)

        class ForgedAssembler:
            constructed = False

            def __init__(self) -> None:
                type(self).constructed = True
                raise AssertionError("replaceable assembler class was constructed")

        with patch.object(
            model_route_module,
            "CanonicalPageSpecAssembler",
            ForgedAssembler,
        ):
            route = TierAModelRouteOrchestrator()
            original_route = self.route
            self.route = route
            try:
                outcome = self.run_route(
                    execution_branch="scripted_local_fixture",
                    scripted_local_fixture=fixture,
                )
                self.replay(
                    outcome,
                    execution_branch="scripted_local_fixture",
                    scripted_local_fixture=fixture,
                )
            finally:
                self.route = original_route
        self.assertFalse(ForgedAssembler.constructed)
        self.assertEqual(outcome.disposition, "scripted_fixture_assembled")
        self.assertEqual(outcome.assembled_page_spec.candidate, expected_candidate)
        self.assertEqual(outcome.assembled_page_spec.page_spec.title, expected_candidate.title)

    def test_object_validator_requires_mapping_artifacts_and_list_steps(self) -> None:
        outcome = self.run_route(
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=self.fixture(),
        )
        payload = outcome.to_dict()
        self.assertEqual(ModelRouteOutcome.from_dict(deepcopy(payload)).to_dict(), payload)
        self.assertEqual(
            validate_serialized_model_route_outcome(deepcopy(payload)).to_dict(),
            payload,
        )
        self.assertEqual(
            validate_serialized_model_route_outcome(outcome.canonical_bytes()).to_dict(),
            payload,
        )

        invalid_values = (
            ("artifacts", list(payload["artifacts"].items())),
            ("completed_steps", tuple(payload["completed_steps"])),
        )
        for field, invalid_value in invalid_values:
            invalid = deepcopy(payload)
            invalid[field] = invalid_value
            for validator in (
                ModelRouteOutcome.from_dict,
                validate_serialized_model_route_outcome,
            ):
                with self.subTest(field=field, validator=validator.__name__):
                    with self.assertRaises(ModelRouteOutcomeError):
                        validator(invalid)

        array_artifacts = deepcopy(payload)
        array_artifacts["artifacts"] = list(payload["artifacts"].items())
        with self.assertRaises(ModelRouteOutcomeError):
            validate_serialized_model_route_outcome(
                model_route_module._canonical_json_bytes(resign(array_artifacts))
            )

    def test_safe_serialization_rejects_tamper_unknown_duplicate_bom_and_nonfinite(self) -> None:
        outcome = self.run_route(
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=self.fixture(),
        )
        payload = outcome.to_dict()
        forged = json.loads(json.dumps(payload))
        forged["artifacts"]["raw_response_sha256"] = "0" * 64
        with self.assertRaises(ModelRouteOutcomeError) as raised:
            ModelRouteOutcome.from_dict(resign(forged))
        self.assertEqual(raised.exception.code, "artifact_binding_invalid")
        uppercase = json.loads(json.dumps(payload))
        uppercase["artifacts"]["raw_response_sha256"] = uppercase["artifacts"]["raw_response_sha256"].upper()
        with self.assertRaises(ModelRouteOutcomeError):
            ModelRouteOutcome.from_dict(resign(uppercase))
        registry_tamper = json.loads(json.dumps(payload))
        registry_tamper["scripted_local_fixture"]["registry_id"] = "scripted-fixture-registry-forged"
        registry_tamper["execution_declarations"]["scripted_fixture_registry_id"] = "scripted-fixture-registry-forged"
        with self.assertRaises(ModelRouteOutcomeError):
            ModelRouteOutcome.from_dict(resign(registry_tamper))
        unknown = json.loads(json.dumps(payload))
        unknown["unexpected"] = True
        with self.assertRaises(ModelRouteOutcomeError):
            ModelRouteOutcome.from_bytes(
                json.dumps(
                    unknown,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        duplicate = b'{"schema_version":"x","schema_version":"x"}'
        bom = b"\xef\xbb\xbf" + outcome.canonical_bytes()
        nonfinite = b'{"schema_version":NaN}'
        for raw_value in (duplicate, bom, nonfinite):
            with self.subTest(raw=raw_value[:12]), self.assertRaises(ModelRouteOutcomeError):
                ModelRouteOutcome.from_bytes(raw_value)

    def test_live_replay_rejects_cross_bound_registry_fixture(self) -> None:
        fixture = self.fixture()
        outcome = self.run_route(
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=fixture,
        )
        other = self.fixture(SCRIPTED_FIXTURE_ASSEMBLY_INVALID_KEY)
        self.assert_replay_rejected(
            outcome,
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=other,
        )

    def test_static_import_io_docstring_and_reverse_boundary(self) -> None:
        source_path = ROOT / "src" / "req2web_orchestration" / "model_route.py"
        raw_source = source_path.read_bytes()
        source = raw_source.decode("utf-8")
        tree = ast.parse(source)
        self.assertTrue(raw_source.startswith(b'"""'))
        self.assertIsInstance(tree.body[0], ast.Expr)
        self.assertIsInstance(tree.body[1], ast.ImportFrom)
        self.assertEqual(tree.body[1].module, "__future__")
        self.assertIn('raw.startswith(b"\\xef\\xbb\\xbf")', source)
        imports = []
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.append(node.func.attr)
        forbidden_import_prefixes = (
            "os",
            "pathlib",
            "socket",
            "requests",
            "http",
            "urllib",
            "subprocess",
            "torch",
            "transformers",
            "vllm",
            "safetensors",
            "req2web_runtime",
            "req2web_provider.mock_provider",
            "req2web_provider.candidate_audit",
        )
        self.assertFalse(any(name.startswith(forbidden_import_prefixes) for name in imports))
        self.assertFalse(
            {"open", "write_text", "write_bytes", "read_text", "read_bytes"}
            & set(calls)
        )
        self.assertNotIn("register", inspect.signature(ScriptedLocalFixture.create).parameters)
        for provider_name in (
            "d17_manifest.py",
            "d17_input_view.py",
            "d17_serializer.py",
            "d17_audit.py",
            "local_qwen_provider.py",
            "semantic_candidate.py",
        ):
            provider_tree = ast.parse(
                (ROOT / "src" / "req2web_provider" / provider_name).read_text(
                    encoding="utf-8"
                )
            )
            provider_imports = []
            for node in ast.walk(provider_tree):
                if isinstance(node, ast.Import):
                    provider_imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    provider_imports.append(node.module or "")
            self.assertFalse(
                any(name.startswith("req2web_orchestration") for name in provider_imports)
            )


if __name__ == "__main__":
    unittest.main()
