"""Tier A local orchestration for the approved D17 path 3 boundary.

``TierAModelRouteOrchestrator`` remains the payload-free, no-I/O Tier A-06
preparation route. ``TierA07aGateDeliveryOrchestrator`` is a separate, colocated
first-pass/no-change/frozen-fallback slice that uses only captured deterministic
gate and delivery authorities. Neither class invokes a model, Provider backend,
network service, real browser, repair route, H1/gold source, or external egress.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from shutil import rmtree as _stdlib_rmtree
import stat as _stat
from typing import Mapping
from uuid import uuid4 as _stdlib_uuid4

from req2web_acceptance.acceptance_plan import (
    ACCEPTANCE_PLAN_SCHEMA_VERSION,
    AcceptancePlan,
    compile_acceptance_plan,
)
from req2web_acceptance.binding import (
    ACCEPTANCE_BINDING_SCHEMA_VERSION,
    AcceptanceBindingPlan,
    compile_acceptance_binding,
)
from req2web_acceptance.browser_executor import (
    BROWSER_EXECUTION_SCHEMA_VERSION,
    BrowserEvidenceUnavailable,
    BrowserExecutionReport,
    execute_acceptance_binding_plan,
)
from req2web_acceptance.requirement_view import (
    INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION,
    RequirementView,
    project_requirement_view,
)
from req2web_agent import AgentContextBundle
from req2web_evaluation.frozen_g0_reference import (
    FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION,
    FrozenG0InventoryEntry,
    FrozenG0PackageReference,
    FrozenG0PackageReferenceError,
)
from req2web_faults.fallback_delivery import (
    FALLBACK_DELIVERY_REPORT_SCHEMA_VERSION,
    FROZEN_G0_FALLBACK_RECORD_SCHEMA_VERSION,
    FallbackDeliveryReport,
    FrozenG0FallbackRecord,
    deliver_frozen_g0_fallback,
)
from req2web_generation import RetrievalEnhancedResultPackage, RetrievalGuidance
from req2web_generation.consistency import (
    CONSISTENCY_REPORT_SCHEMA_VERSION,
    ConsistencyReport,
    MinimalConsistencyChecker,
)
from req2web_generation.renderer import (
    RENDER_MANIFEST_SCHEMA_VERSION,
    DeterministicPageRenderer,
    RenderResult,
)
from req2web_generation.result_package import (
    RESULT_PACKAGE_SCHEMA_VERSION,
    DeterministicResultPackager,
    ResultPackage,
)
from req2web_generation.result_package_v2 import RESULT_PACKAGE_V2_SCHEMA_VERSION
from req2web_generation.schema import PAGE_SPEC_SCHEMA_VERSION, PageSpec
from req2web_provider.d17_audit import D17Path3TierAPreInvocationAuditRecord
from req2web_provider.d17_input_view import D17Path3SelectedInput
from req2web_provider.d17_manifest import D17Path3TierAManifest
from req2web_provider.d17_serializer import D17Path3LocalRequestArtifact
from req2web_provider.local_qwen_provider import (
    LocalQwenProviderInvocationBlockedError,
    LocalQwenProviderPreparationRecord,
    invoke_local_qwen_provider,
)
from req2web_provider.semantic_candidate import (
    AssembledPageSpec,
    CanonicalPageSpecAssembler,
    ProviderProtocolError,
    ProviderRawResponse,
    parse_provider_raw_response,
)


MODEL_ROUTE_OUTCOME_SCHEMA_VERSION = "req2web.orchestration.tier_a_model_route_outcome.v1"
MODEL_ROUTE_FAILURE_SCHEMA_VERSION = "req2web.orchestration.tier_a_model_route_failure.v1"
SCRIPTED_LOCAL_FIXTURE_SCHEMA_VERSION = "req2web.orchestration.scripted_local_fixture.v1"
SCRIPTED_LOCAL_FIXTURE_PROJECTION_SCHEMA_VERSION = "req2web.orchestration.scripted_local_fixture_projection.v1"
SCRIPTED_FIXTURE_REGISTRY_SCHEMA_VERSION = "req2web.orchestration.scripted_fixture_registry.v1"
_MODEL_ROUTE_ID_PREFIX = "tier-a-model-route-"
_SCRIPTED_FIXTURE_ID_PREFIX = "scripted-local-fixture-"
_SCRIPTED_FIXTURE_REGISTRY_ID_PREFIX = "scripted-fixture-registry-"

SCRIPTED_FIXTURE_VALID_CANDIDATE_KEY = "synthetic_valid_candidate_v1"
SCRIPTED_FIXTURE_GATE_DELIVERY_VALID_KEY = "synthetic_gate_delivery_valid_v1"
SCRIPTED_FIXTURE_SEMANTIC_INVALID_KEY = "synthetic_semantic_invalid_v1"
SCRIPTED_FIXTURE_ASSEMBLY_INVALID_KEY = "synthetic_assembly_invalid_v1"

_BRANCH_NOT_SELECTED = "not_selected"
_BRANCH_AUTHORIZATION_PROBE = "tier_b_authorization_probe"
_BRANCH_SCRIPTED_FIXTURE = "scripted_local_fixture"
_ALLOWED_REQUEST_BRANCHES = (_BRANCH_AUTHORIZATION_PROBE, _BRANCH_SCRIPTED_FIXTURE)
_OUTCOME_BRANCHES = (_BRANCH_NOT_SELECTED, *_ALLOWED_REQUEST_BRANCHES)

_STEP_G0 = "g0_reference_validation"
_STEP_MANIFEST = "d17_manifest_validation"
_STEP_SELECTION = "d17_input_selection_validation"
_STEP_REQUEST = "d17_local_request_validation"
_STEP_AUDIT = "d17_pre_invocation_audit_validation"
_STEP_PREPARATION = "local_qwen_preparation_validation"
_STEP_AUTHORIZATION = "local_qwen_invocation_authorization_probe"
_STEP_FIXTURE = "scripted_local_fixture_validation"
_STEP_PARSE = "semantic_candidate_parse"
_STEP_ASSEMBLY = "canonical_page_spec_assembly"
_ALL_SCRIPTED_STEPS = (
    _STEP_G0,
    _STEP_MANIFEST,
    _STEP_SELECTION,
    _STEP_REQUEST,
    _STEP_AUDIT,
    _STEP_PREPARATION,
    _STEP_FIXTURE,
    _STEP_PARSE,
    _STEP_ASSEMBLY,
)

_ARTIFACT_KEYS = (
    "raw_response_sha256",
    "raw_response_byte_length",
    "model_semantic_candidate_sha256",
    "assembled_page_id",
    "assembly_report_id",
    "assembly_report_sha256",
    "assembled_page_spec_sha256",
)
_ARTIFACT_NONE = "none"
_ARTIFACT_RAW = "raw"
_ARTIFACT_RAW_CANDIDATE = "raw_candidate"

_ERROR_MESSAGES = {
    "artifact_binding_invalid": "safe outcome artifacts do not match the recorded stage",
    "artifact_shape_invalid": "safe outcome artifact shape is invalid",
    "declarations_invalid": "model-route declarations are invalid",
    "failure_envelope_invalid": "model-route failure envelope is invalid",
    "failure_replay_invalid": "the claimed fail-closed stage was not reproduced",
    "fixture_projection_invalid": "scripted fixture projection is invalid",
    "fixture_registry_invalid": "scripted fixture registry identity is invalid",
    "frozen_reference_invalid": "frozen G0 reference projection is invalid",
    "outcome_exact_keys_invalid": "model-route outcome keys are invalid",
    "outcome_identity_invalid": "model-route outcome identity is invalid",
    "outcome_json_invalid": "model-route outcome JSON is invalid",
    "outcome_schema_invalid": "model-route outcome schema is invalid",
    "outcome_state_invalid": "model-route outcome state is invalid",
    "preparation_projection_invalid": "Local Qwen preparation projection is invalid",
    "serialized_bytes_invalid": "model-route serialized bytes are invalid",
}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _error(code: str) -> "ModelRouteOutcomeError":
    return ModelRouteOutcomeError(code)


def _empty_artifacts() -> dict[str, str | int | None]:
    return {key: None for key in _ARTIFACT_KEYS}


def _reference_sha256(reference: FrozenG0PackageReference) -> str:
    return _sha256(_canonical_json_bytes(reference.to_dict()))


@dataclass(frozen=True)
class _ScriptedFixtureRegistryEntry:
    fixture_key: str
    raw_response_sha256: str
    raw_response_byte_length: int
    source_class: str

    def validate(self, _is_sha256_fn=_is_sha256) -> None:
        if (
            not _is_text(self.fixture_key)
            or not _is_sha256_fn(self.raw_response_sha256)
            or not isinstance(self.raw_response_byte_length, int)
            or isinstance(self.raw_response_byte_length, bool)
            or self.raw_response_byte_length < 1
            or self.source_class not in {"project_authored", "synthetic"}
        ):
            raise _error("fixture_registry_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "fixture_key": self.fixture_key,
            "raw_response_sha256": self.raw_response_sha256,
            "raw_response_byte_length": self.raw_response_byte_length,
            "source_class": self.source_class,
        }


def _build_scripted_fixture_registry_authority():
    entries = (
        _ScriptedFixtureRegistryEntry(
            SCRIPTED_FIXTURE_VALID_CANDIDATE_KEY,
            "cbe8cd39fe9852af25d6abe1869fea5368eaf0eeb1e2cce4a8248961c195a265",
            3183,
            "synthetic",
        ),
        _ScriptedFixtureRegistryEntry(
            SCRIPTED_FIXTURE_SEMANTIC_INVALID_KEY,
            "5d90708c2f8e7da0f6f6d5ce3b2c705e73e557ab3be2d3e1897c334d63561667",
            30,
            "synthetic",
        ),
        _ScriptedFixtureRegistryEntry(
            SCRIPTED_FIXTURE_ASSEMBLY_INVALID_KEY,
            "14baad8e7c60c005b055565e7a6a168e5b35d97afc83db0caa21b74298611fac",
            3183,
            "synthetic",
        ),
        _ScriptedFixtureRegistryEntry(
            SCRIPTED_FIXTURE_GATE_DELIVERY_VALID_KEY,
            "78e213d1581f8709f4284f87b04f5572d742c8192969bc3a148c40b25e6c9629",
            4115,
            "synthetic",
        ),
    )
    registry_schema_version = SCRIPTED_FIXTURE_REGISTRY_SCHEMA_VERSION
    expected_sha256 = "3c0cd15b4cc5446106d207ba59e1b468640363d4aa8756eda4855188aaf63092"
    registry_id = _SCRIPTED_FIXTURE_REGISTRY_ID_PREFIX + expected_sha256
    root = {
        "schema_version": registry_schema_version,
        "entries": [entry.to_dict() for entry in entries],
        "immutable": True,
        "runtime_registration_api": "none",
        "payload_bytes_stored": False,
    }
    if _sha256(_canonical_json_bytes(root)) != expected_sha256:
        raise RuntimeError("fixed scripted fixture registry digest mismatch")
    entries_by_key = {entry.fixture_key: entry for entry in entries}
    if len(entries_by_key) != len(entries):
        raise RuntimeError("fixed scripted fixture registry keys are not unique")

    def lookup(fixture_key: object) -> _ScriptedFixtureRegistryEntry:
        if not isinstance(fixture_key, str) or fixture_key not in entries_by_key:
            raise _error("fixture_projection_invalid")
        return entries_by_key[fixture_key]

    def validate_binding(schema_version: object, identity: object, sha256: object) -> None:
        if (
            schema_version != registry_schema_version
            or identity != registry_id
            or sha256 != expected_sha256
        ):
            raise _error("fixture_projection_invalid")

    report_entries = tuple(
        _ScriptedFixtureRegistryEntry(**entry.to_dict()) for entry in entries
    )
    return (
        lookup,
        validate_binding,
        report_entries,
        registry_schema_version,
        registry_id,
        expected_sha256,
    )


(
    _FIXED_REGISTRY_LOOKUP,
    _FIXED_REGISTRY_VALIDATE_BINDING,
    _FIXED_REGISTRY_ENTRY_REPORTS,
    _FIXED_REGISTRY_SCHEMA_VERSION,
    _FIXED_REGISTRY_ID,
    _FIXED_REGISTRY_SHA256,
) = _build_scripted_fixture_registry_authority()

# Report-only copies. Rebinding these module names cannot change the captured authority.
_SCRIPTED_FIXTURE_REGISTRY_ENTRIES = tuple(_FIXED_REGISTRY_ENTRY_REPORTS)
SCRIPTED_FIXTURE_REGISTRY_SHA256 = _FIXED_REGISTRY_SHA256
SCRIPTED_FIXTURE_REGISTRY_ID = _FIXED_REGISTRY_ID


@dataclass(frozen=True)
class _FailureRule:
    stage: str
    phase: str
    completed_steps: tuple[str, ...]
    execution_branch: str
    reference_present: bool
    preparation_present: bool
    fixture_present: bool
    artifact_mode: str


_FAILURES = {
    "g0_reference_validation_failed": _FailureRule("package", "g0_reference_validation", (), _BRANCH_NOT_SELECTED, False, False, False, _ARTIFACT_NONE),
    "d17_manifest_invalid": _FailureRule("input_policy", "d17_manifest", (_STEP_G0,), _BRANCH_NOT_SELECTED, True, False, False, _ARTIFACT_NONE),
    "d17_input_selection_invalid": _FailureRule("input_context", "d17_input_selection", (_STEP_G0, _STEP_MANIFEST), _BRANCH_NOT_SELECTED, True, False, False, _ARTIFACT_NONE),
    "d17_local_request_invalid": _FailureRule("input_context", "d17_local_request", (_STEP_G0, _STEP_MANIFEST, _STEP_SELECTION), _BRANCH_NOT_SELECTED, True, False, False, _ARTIFACT_NONE),
    "d17_pre_invocation_audit_invalid": _FailureRule("audit", "d17_pre_invocation_audit", (_STEP_G0, _STEP_MANIFEST, _STEP_SELECTION, _STEP_REQUEST), _BRANCH_NOT_SELECTED, True, False, False, _ARTIFACT_NONE),
    "local_qwen_preparation_invalid": _FailureRule("provider_preparation", "local_qwen_preparation", (_STEP_G0, _STEP_MANIFEST, _STEP_SELECTION, _STEP_REQUEST, _STEP_AUDIT), _BRANCH_NOT_SELECTED, True, False, False, _ARTIFACT_NONE),
    "route_branch_invalid": _FailureRule("orchestration", "route_branch", (_STEP_G0, _STEP_MANIFEST, _STEP_SELECTION, _STEP_REQUEST, _STEP_AUDIT, _STEP_PREPARATION), _BRANCH_NOT_SELECTED, True, True, False, _ARTIFACT_NONE),
    "tier_b_unapproved": _FailureRule("provider_authorization", "local_qwen_invocation", (_STEP_G0, _STEP_MANIFEST, _STEP_SELECTION, _STEP_REQUEST, _STEP_AUDIT, _STEP_PREPARATION, _STEP_AUTHORIZATION), _BRANCH_AUTHORIZATION_PROBE, True, True, False, _ARTIFACT_NONE),
    "local_qwen_invocation_unexpected_return": _FailureRule("provider_authorization", "local_qwen_invocation", (_STEP_G0, _STEP_MANIFEST, _STEP_SELECTION, _STEP_REQUEST, _STEP_AUDIT, _STEP_PREPARATION, _STEP_AUTHORIZATION), _BRANCH_AUTHORIZATION_PROBE, True, True, False, _ARTIFACT_NONE),
    "scripted_fixture_provenance_invalid": _FailureRule("scripted_fixture", "fixture_provenance", (_STEP_G0, _STEP_MANIFEST, _STEP_SELECTION, _STEP_REQUEST, _STEP_AUDIT, _STEP_PREPARATION), _BRANCH_SCRIPTED_FIXTURE, True, True, False, _ARTIFACT_NONE),
    "semantic_candidate_invalid": _FailureRule("provider_generation", "semantic_candidate", (_STEP_G0, _STEP_MANIFEST, _STEP_SELECTION, _STEP_REQUEST, _STEP_AUDIT, _STEP_PREPARATION, _STEP_FIXTURE), _BRANCH_SCRIPTED_FIXTURE, True, True, True, _ARTIFACT_RAW),
    "page_spec_assembly_invalid": _FailureRule("page_spec", "canonical_assembly", (_STEP_G0, _STEP_MANIFEST, _STEP_SELECTION, _STEP_REQUEST, _STEP_AUDIT, _STEP_PREPARATION, _STEP_FIXTURE, _STEP_PARSE), _BRANCH_SCRIPTED_FIXTURE, True, True, True, _ARTIFACT_RAW_CANDIDATE),
}

def _declarations(
    execution_branch: str,
    fixture: "ScriptedLocalFixtureProjection | None",
    _registry_schema_version: str = _FIXED_REGISTRY_SCHEMA_VERSION,
    _registry_id: str = _FIXED_REGISTRY_ID,
    _registry_sha256: str = _FIXED_REGISTRY_SHA256,
) -> dict[str, object]:
    return {
        "tier_a_local_only": True,
        "d17_path": "path_3",
        "external_egress_allowed": False,
        "not_sent": True,
        "model_loaded": False,
        "provider_invoked": False,
        "tier_b_authorization": "tier_b_unapproved",
        "g1_g2_status": "not_assigned",
        "delivery_route": "not_executed",
        "fallback_materialized": False,
        "repair_status": "not_executed",
        "retry_performed": False,
        "formal_quality_status": "not_executed",
        "execution_branch": execution_branch,
        "scripted_fixture_status": (
            "synthetic_scripted_only" if fixture is not None else "not_used"
        ),
        "scripted_fixture_registry_schema_version": _registry_schema_version,
        "scripted_fixture_registry_id": _registry_id,
        "scripted_fixture_registry_sha256": _registry_sha256,
    }


def _load_canonical_json(raw: bytes) -> object:
    if not isinstance(raw, bytes) or raw.startswith(b"\xef\xbb\xbf"):
        raise _error("serialized_bytes_invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error("serialized_bytes_invalid") from exc

    def reject_constant(_: str) -> object:
        raise ValueError("non-finite JSON constant")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _error("outcome_json_invalid") from exc


def _reference_from_dict(payload: object) -> FrozenG0PackageReference:
    expected = {
        "schema_version",
        "reference_id",
        "package_schema_version",
        "package_id",
        "page_id",
        "context_schema_version",
        "context_id",
        "context_sha256",
        "guidance_schema_version",
        "guidance_bundle_id",
        "guidance_sha256",
        "package_manifest_sha256",
        "inventory_tree_sha256",
        "inventory",
        "declarations",
    }
    declarations = {
        "baseline_kind": "deterministic_guided_g0",
        "retrieval_influence": "v1_only",
        "result_package": "v2_only",
        "package_copied": False,
        "fallback_materialized": False,
        "delivery_route": "not_executed",
        "provider_invocation_status": "not_executed",
        "unguided_builder_used": False,
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != expected
        or payload.get("schema_version") != FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION
        or payload.get("declarations") != declarations
        or not isinstance(payload.get("inventory"), list)
    ):
        raise _error("frozen_reference_invalid")
    try:
        result = FrozenG0PackageReference(
            package_id=payload["package_id"],
            page_id=payload["page_id"],
            context_id=payload["context_id"],
            context_sha256=payload["context_sha256"],
            guidance_bundle_id=payload["guidance_bundle_id"],
            guidance_sha256=payload["guidance_sha256"],
            package_manifest_sha256=payload["package_manifest_sha256"],
            inventory_tree_sha256=payload["inventory_tree_sha256"],
            inventory=tuple(FrozenG0InventoryEntry(**item) for item in payload["inventory"]),
            reference_id=payload["reference_id"],
            schema_version=payload["schema_version"],
            package_schema_version=payload["package_schema_version"],
            context_schema_version=payload["context_schema_version"],
            guidance_schema_version=payload["guidance_schema_version"],
        )
        result.validate()
        return result
    except (FrozenG0PackageReferenceError, KeyError, TypeError, ValueError) as exc:
        raise _error("frozen_reference_invalid") from exc


@dataclass(frozen=True)
class ModelRouteErrorEnvelope:
    code: str
    schema_version: str = MODEL_ROUTE_FAILURE_SCHEMA_VERSION
    payload_disclosure: str = "none"

    def validate(self) -> None:
        if (
            self.code not in _ERROR_MESSAGES
            or self.schema_version != MODEL_ROUTE_FAILURE_SCHEMA_VERSION
            or self.payload_disclosure != "none"
        ):
            raise _error("failure_envelope_invalid")

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "code": self.code,
            "payload_disclosure": self.payload_disclosure,
        }


class ModelRouteOutcomeError(ValueError):
    def __init__(self, code: str) -> None:
        if code not in _ERROR_MESSAGES:
            raise ValueError("unsupported model-route error code")
        self.code = code
        self.envelope = ModelRouteErrorEnvelope(code)
        super().__init__(_ERROR_MESSAGES[code])


@dataclass(frozen=True)
class ModelRouteFailure:
    code: str
    stage: str
    phase: str
    retryable: bool = False
    payload_disclosure: str = "none"

    @classmethod
    def fixed(cls, code: str) -> "ModelRouteFailure":
        rule = _FAILURES[code]
        return cls(code, rule.stage, rule.phase)

    @classmethod
    def from_dict(cls, payload: object) -> "ModelRouteFailure":
        if not isinstance(payload, Mapping) or set(payload) != {
            "code",
            "stage",
            "phase",
            "retryable",
            "payload_disclosure",
        }:
            raise _error("failure_envelope_invalid")
        result = cls(
            payload["code"],
            payload["stage"],
            payload["phase"],
            payload["retryable"],
            payload["payload_disclosure"],
        )
        result.validate()
        return result

    def validate(self) -> None:
        rule = _FAILURES.get(self.code)
        if (
            rule is None
            or self.retryable is not False
            or self.payload_disclosure != "none"
            or (self.stage, self.phase) != (rule.stage, rule.phase)
        ):
            raise _error("failure_envelope_invalid")

    def to_dict(self) -> dict[str, str | bool]:
        self.validate()
        return {
            "code": self.code,
            "stage": self.stage,
            "phase": self.phase,
            "retryable": self.retryable,
            "payload_disclosure": self.payload_disclosure,
        }


def _build_scripted_fixture_types(
    registry_lookup,
    validate_registry_binding,
    registry_schema_version: str,
    registry_id: str,
    registry_sha256: str,
):
    @dataclass(frozen=True)
    class ScriptedLocalFixture:
        """Exact caller bytes admitted only by the captured fixed registry."""

        raw_response: ProviderRawResponse
        registry_entry_key: str
        source_class: str
        registry_schema_version: str
        registry_id: str
        registry_sha256: str
        fixture_id: str
        schema_version: str = SCRIPTED_LOCAL_FIXTURE_SCHEMA_VERSION
        fixture_scope: str = "synthetic_scripted_only"
        provider_invoked: bool = False
        model_loaded: bool = False
        not_sent: bool = True

        @classmethod
        def create(
            cls,
            registry_entry_key: str,
            raw_response: ProviderRawResponse,
        ) -> "ScriptedLocalFixture":
            entry = registry_lookup(registry_entry_key)
            if not isinstance(raw_response, ProviderRawResponse):
                raise _error("fixture_projection_invalid")
            if (
                len(raw_response.raw_bytes) < 1
                or raw_response.sha256 != entry.raw_response_sha256
                or len(raw_response.raw_bytes) != entry.raw_response_byte_length
            ):
                raise _error("fixture_projection_invalid")
            root = cls._root_for(entry, raw_response)
            result = cls(
                raw_response=raw_response,
                registry_entry_key=entry.fixture_key,
                source_class=entry.source_class,
                registry_schema_version=registry_schema_version,
                registry_id=registry_id,
                registry_sha256=registry_sha256,
                fixture_id=(
                    _SCRIPTED_FIXTURE_ID_PREFIX
                    + _sha256(_canonical_json_bytes(root))
                ),
            )
            result.validate()
            return result

        @staticmethod
        def _root_for(
            entry: _ScriptedFixtureRegistryEntry,
            raw_response: ProviderRawResponse,
        ) -> dict[str, object]:
            return {
                "schema_version": SCRIPTED_LOCAL_FIXTURE_SCHEMA_VERSION,
                "fixture_scope": "synthetic_scripted_only",
                "registry_schema_version": registry_schema_version,
                "registry_id": registry_id,
                "registry_sha256": registry_sha256,
                "registry_entry_key": entry.fixture_key,
                "source_class": entry.source_class,
                "provider_invoked": False,
                "model_loaded": False,
                "not_sent": True,
                "raw_response_sha256": raw_response.sha256,
                "raw_response_byte_length": len(raw_response.raw_bytes),
            }

        def validate(self, _is_sha256_fn=_is_sha256) -> None:
            entry = registry_lookup(self.registry_entry_key)
            validate_registry_binding(
                self.registry_schema_version,
                self.registry_id,
                self.registry_sha256,
            )
            if (
                not isinstance(self.raw_response, ProviderRawResponse)
                or self.schema_version != SCRIPTED_LOCAL_FIXTURE_SCHEMA_VERSION
                or self.fixture_scope != "synthetic_scripted_only"
                or self.source_class != entry.source_class
                or self.provider_invoked is not False
                or self.model_loaded is not False
                or self.not_sent is not True
                or len(self.raw_response.raw_bytes) < 1
                or self.raw_response.sha256 != entry.raw_response_sha256
                or len(self.raw_response.raw_bytes) != entry.raw_response_byte_length
            ):
                raise _error("fixture_projection_invalid")
            expected = _SCRIPTED_FIXTURE_ID_PREFIX + _sha256(
                _canonical_json_bytes(self._root_for(entry, self.raw_response))
            )
            if self.fixture_id != expected:
                raise _error("fixture_projection_invalid")

        def safe_projection(self) -> "ScriptedLocalFixtureProjection":
            self.validate()
            return ScriptedLocalFixtureProjection(
                schema_version=SCRIPTED_LOCAL_FIXTURE_PROJECTION_SCHEMA_VERSION,
                fixture_id=self.fixture_id,
                fixture_scope=self.fixture_scope,
                registry_schema_version=self.registry_schema_version,
                registry_id=self.registry_id,
                registry_sha256=self.registry_sha256,
                registry_entry_key=self.registry_entry_key,
                source_class=self.source_class,
                provider_invoked=self.provider_invoked,
                model_loaded=self.model_loaded,
                not_sent=self.not_sent,
                raw_response_sha256=self.raw_response.sha256,
                raw_response_byte_length=len(self.raw_response.raw_bytes),
            )

    @dataclass(frozen=True)
    class ScriptedLocalFixtureProjection:
        schema_version: str
        fixture_id: str
        fixture_scope: str
        registry_schema_version: str
        registry_id: str
        registry_sha256: str
        registry_entry_key: str
        source_class: str
        provider_invoked: bool
        model_loaded: bool
        not_sent: bool
        raw_response_sha256: str
        raw_response_byte_length: int

        @classmethod
        def from_dict(cls, payload: object) -> "ScriptedLocalFixtureProjection":
            keys = {
                "schema_version",
                "fixture_id",
                "fixture_scope",
                "registry_schema_version",
                "registry_id",
                "registry_sha256",
                "registry_entry_key",
                "source_class",
                "provider_invoked",
                "model_loaded",
                "not_sent",
                "raw_response_sha256",
                "raw_response_byte_length",
            }
            if not isinstance(payload, Mapping) or set(payload) != keys:
                raise _error("fixture_projection_invalid")
            result = cls(**dict(payload))
            result.validate()
            return result

        def validate(self, _is_sha256_fn=_is_sha256) -> None:
            entry = registry_lookup(self.registry_entry_key)
            validate_registry_binding(
                self.registry_schema_version,
                self.registry_id,
                self.registry_sha256,
            )
            if (
                self.schema_version
                != SCRIPTED_LOCAL_FIXTURE_PROJECTION_SCHEMA_VERSION
                or not _is_text(self.fixture_id)
                or self.fixture_scope != "synthetic_scripted_only"
                or self.source_class != entry.source_class
                or self.provider_invoked is not False
                or self.model_loaded is not False
                or self.not_sent is not True
                or not _is_sha256_fn(self.raw_response_sha256)
                or self.raw_response_sha256 != entry.raw_response_sha256
                or not isinstance(self.raw_response_byte_length, int)
                or isinstance(self.raw_response_byte_length, bool)
                or self.raw_response_byte_length < 1
                or self.raw_response_byte_length != entry.raw_response_byte_length
            ):
                raise _error("fixture_projection_invalid")
            root = {
                "schema_version": SCRIPTED_LOCAL_FIXTURE_SCHEMA_VERSION,
                "fixture_scope": self.fixture_scope,
                "registry_schema_version": self.registry_schema_version,
                "registry_id": self.registry_id,
                "registry_sha256": self.registry_sha256,
                "registry_entry_key": self.registry_entry_key,
                "source_class": self.source_class,
                "provider_invoked": self.provider_invoked,
                "model_loaded": self.model_loaded,
                "not_sent": self.not_sent,
                "raw_response_sha256": self.raw_response_sha256,
                "raw_response_byte_length": self.raw_response_byte_length,
            }
            if self.fixture_id != _SCRIPTED_FIXTURE_ID_PREFIX + _sha256(
                _canonical_json_bytes(root)
            ):
                raise _error("fixture_projection_invalid")

        def to_dict(self) -> dict[str, object]:
            self.validate()
            return {
                "schema_version": self.schema_version,
                "fixture_id": self.fixture_id,
                "fixture_scope": self.fixture_scope,
                "registry_schema_version": self.registry_schema_version,
                "registry_id": self.registry_id,
                "registry_sha256": self.registry_sha256,
                "registry_entry_key": self.registry_entry_key,
                "source_class": self.source_class,
                "provider_invoked": self.provider_invoked,
                "model_loaded": self.model_loaded,
                "not_sent": self.not_sent,
                "raw_response_sha256": self.raw_response_sha256,
                "raw_response_byte_length": self.raw_response_byte_length,
            }

    return ScriptedLocalFixture, ScriptedLocalFixtureProjection


ScriptedLocalFixture, ScriptedLocalFixtureProjection = _build_scripted_fixture_types(
    _FIXED_REGISTRY_LOOKUP,
    _FIXED_REGISTRY_VALIDATE_BINDING,
    _FIXED_REGISTRY_SCHEMA_VERSION,
    _FIXED_REGISTRY_ID,
    _FIXED_REGISTRY_SHA256,
)


def _build_live_fixture_authority(fixture_type):
    captured_fixture_type = fixture_type

    def validate(fixture: object) -> ScriptedLocalFixture:
        if not isinstance(fixture, captured_fixture_type):
            raise TypeError("registered scripted fixture required")
        fixture.validate()
        return fixture

    return validate


_FIXED_LIVE_FIXTURE_AUTHORITY = _build_live_fixture_authority(
    ScriptedLocalFixture
)


def _build_canonical_assembly_authority(assembler_type):
    captured_assembler_type = assembler_type

    def assemble(
        raw_response: ProviderRawResponse,
        context: object,
        guidance: object,
    ) -> AssembledPageSpec:
        assembled = captured_assembler_type().assemble(
            raw_response,
            context,
            guidance,
        )
        assembled.validate()
        return assembled

    return assemble


_FIXED_CANONICAL_ASSEMBLY_AUTHORITY = _build_canonical_assembly_authority(
    CanonicalPageSpecAssembler
)


def _assemble_canonically(
    raw_response: ProviderRawResponse,
    context: object,
    guidance: object,
    _captured_authority=_FIXED_CANONICAL_ASSEMBLY_AUTHORITY,
) -> AssembledPageSpec:
    """Report-facing helper; orchestration and replay do not trust this name."""
    return _captured_authority(raw_response, context, guidance)


def _outcome_root(
    *,
    disposition: str,
    execution_branch: str,
    completed_steps: tuple[str, ...],
    failure: ModelRouteFailure | None,
    frozen_g0_reference: FrozenG0PackageReference | None,
    frozen_g0_reference_sha256: str | None,
    local_qwen_preparation: LocalQwenProviderPreparationRecord | None,
    scripted_local_fixture: ScriptedLocalFixtureProjection | None,
    artifacts: Mapping[str, str | int | None],
) -> dict[str, object]:
    return {
        "schema_version": MODEL_ROUTE_OUTCOME_SCHEMA_VERSION,
        "disposition": disposition,
        "execution_branch": execution_branch,
        "completed_steps": list(completed_steps),
        "failure": None if failure is None else failure.to_dict(),
        "frozen_g0_reference": (
            None if frozen_g0_reference is None else frozen_g0_reference.to_dict()
        ),
        "frozen_g0_reference_sha256": frozen_g0_reference_sha256,
        "local_qwen_preparation": (
            None if local_qwen_preparation is None else local_qwen_preparation.to_dict()
        ),
        "scripted_local_fixture": (
            None if scripted_local_fixture is None else scripted_local_fixture.to_dict()
        ),
        "artifacts": dict(artifacts),
        "execution_declarations": _declarations(execution_branch, scripted_local_fixture),
    }


@dataclass(frozen=True)
class ModelRouteOutcome:
    """Canonical payload-safe Tier A result with optional private assembly."""

    disposition: str
    execution_branch: str
    completed_steps: tuple[str, ...]
    failure: ModelRouteFailure | None
    frozen_g0_reference: FrozenG0PackageReference | None
    frozen_g0_reference_sha256: str | None
    local_qwen_preparation: LocalQwenProviderPreparationRecord | None
    scripted_local_fixture: ScriptedLocalFixtureProjection | None
    artifacts: Mapping[str, str | int | None]
    assembled_page_spec: AssembledPageSpec | None
    outcome_id: str
    schema_version: str = MODEL_ROUTE_OUTCOME_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        disposition: str,
        execution_branch: str,
        completed_steps: tuple[str, ...],
        failure: ModelRouteFailure | None,
        frozen_g0_reference: FrozenG0PackageReference | None = None,
        local_qwen_preparation: LocalQwenProviderPreparationRecord | None = None,
        scripted_local_fixture: ScriptedLocalFixtureProjection | None = None,
        assembled_page_spec: AssembledPageSpec | None = None,
        **artifacts: str | int | None,
    ) -> "ModelRouteOutcome":
        all_artifacts = _empty_artifacts()
        if set(artifacts) - set(_ARTIFACT_KEYS):
            raise _error("artifact_shape_invalid")
        all_artifacts.update(artifacts)
        reference_sha256 = (
            None if frozen_g0_reference is None else _reference_sha256(frozen_g0_reference)
        )
        root = _outcome_root(
            disposition=disposition,
            execution_branch=execution_branch,
            completed_steps=completed_steps,
            failure=failure,
            frozen_g0_reference=frozen_g0_reference,
            frozen_g0_reference_sha256=reference_sha256,
            local_qwen_preparation=local_qwen_preparation,
            scripted_local_fixture=scripted_local_fixture,
            artifacts=all_artifacts,
        )
        result = cls(
            disposition=disposition,
            execution_branch=execution_branch,
            completed_steps=completed_steps,
            failure=failure,
            frozen_g0_reference=frozen_g0_reference,
            frozen_g0_reference_sha256=reference_sha256,
            local_qwen_preparation=local_qwen_preparation,
            scripted_local_fixture=scripted_local_fixture,
            artifacts=all_artifacts,
            assembled_page_spec=assembled_page_spec,
            outcome_id=_MODEL_ROUTE_ID_PREFIX + _sha256(_canonical_json_bytes(root)),
        )
        result.validate()
        return result

    def _root(self) -> dict[str, object]:
        return _outcome_root(
            disposition=self.disposition,
            execution_branch=self.execution_branch,
            completed_steps=self.completed_steps,
            failure=self.failure,
            frozen_g0_reference=self.frozen_g0_reference,
            frozen_g0_reference_sha256=self.frozen_g0_reference_sha256,
            local_qwen_preparation=self.local_qwen_preparation,
            scripted_local_fixture=self.scripted_local_fixture,
            artifacts=self.artifacts,
        )

    def _validate_artifact_values(self, _is_sha256_fn=_is_sha256) -> None:
        if set(self.artifacts) != set(_ARTIFACT_KEYS):
            raise _error("artifact_shape_invalid")
        for key, value in self.artifacts.items():
            if key == "raw_response_byte_length":
                if value is not None and (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 1
                ):
                    raise _error("artifact_shape_invalid")
            elif value is not None and not _is_text(value):
                raise _error("artifact_shape_invalid")
        for key in (
            "raw_response_sha256",
            "model_semantic_candidate_sha256",
            "assembly_report_sha256",
            "assembled_page_spec_sha256",
        ):
            if self.artifacts[key] is not None and not _is_sha256_fn(self.artifacts[key]):
                raise _error("artifact_shape_invalid")

    def _validate_reference_presence(self, expected: bool) -> None:
        if expected != (self.frozen_g0_reference is not None):
            raise _error("outcome_state_invalid")
        if not expected:
            if self.frozen_g0_reference_sha256 is not None:
                raise _error("outcome_state_invalid")
            return
        try:
            self.frozen_g0_reference.validate()
        except (FrozenG0PackageReferenceError, TypeError, ValueError) as exc:
            raise _error("frozen_reference_invalid") from exc
        if self.frozen_g0_reference_sha256 != _reference_sha256(self.frozen_g0_reference):
            raise _error("frozen_reference_invalid")

    def _validate_preparation_presence(self, expected: bool) -> None:
        if expected != (self.local_qwen_preparation is not None):
            raise _error("outcome_state_invalid")
        if not expected:
            return
        try:
            self.local_qwen_preparation.validate()
        except (TypeError, ValueError) as exc:
            raise _error("preparation_projection_invalid") from exc

    def _validate_fixture_presence(self, expected: bool) -> None:
        if expected != (self.scripted_local_fixture is not None):
            raise _error("outcome_state_invalid")
        if not expected:
            return
        try:
            self.scripted_local_fixture.validate()
        except (TypeError, ValueError) as exc:
            raise _error("fixture_projection_invalid") from exc

    def _validate_fixture_artifact_binding(self) -> None:
        fixture = self.scripted_local_fixture
        if (
            fixture is None
            or self.artifacts["raw_response_sha256"] != fixture.raw_response_sha256
            or self.artifacts["raw_response_byte_length"] != fixture.raw_response_byte_length
        ):
            raise _error("artifact_binding_invalid")

    def _validate_artifact_mode(self, mode: str) -> None:
        if mode == _ARTIFACT_NONE:
            if any(value is not None for value in self.artifacts.values()):
                raise _error("artifact_binding_invalid")
            return
        self._validate_fixture_artifact_binding()
        raw_keys = {"raw_response_sha256", "raw_response_byte_length"}
        if mode == _ARTIFACT_RAW:
            if any(
                self.artifacts[key] is not None
                for key in _ARTIFACT_KEYS
                if key not in raw_keys
            ):
                raise _error("artifact_binding_invalid")
            return
        if mode == _ARTIFACT_RAW_CANDIDATE:
            if self.artifacts["model_semantic_candidate_sha256"] is None:
                raise _error("artifact_binding_invalid")
            if any(
                self.artifacts[key] is not None
                for key in (
                    "assembled_page_id",
                    "assembly_report_id",
                    "assembly_report_sha256",
                    "assembled_page_spec_sha256",
                )
            ):
                raise _error("artifact_binding_invalid")
            return
        raise _error("artifact_binding_invalid")

    def _validate_private_assembly(self) -> None:
        if self.assembled_page_spec is None:
            return
        try:
            self.assembled_page_spec.validate()
        except (TypeError, ValueError) as exc:
            raise _error("artifact_binding_invalid") from exc
        report = self.assembled_page_spec.report
        if (
            self.assembled_page_spec.page_spec.page_id != self.artifacts["assembled_page_id"]
            or report.report_id != self.artifacts["assembly_report_id"]
            or report.sha256() != self.artifacts["assembly_report_sha256"]
            or report.assembled_page_spec_sha256
            != self.artifacts["assembled_page_spec_sha256"]
        ):
            raise _error("artifact_binding_invalid")

    def validate(self) -> None:
        if (
            self.schema_version != MODEL_ROUTE_OUTCOME_SCHEMA_VERSION
            or self.execution_branch not in _OUTCOME_BRANCHES
            or not isinstance(self.completed_steps, tuple)
            or any(not isinstance(step, str) for step in self.completed_steps)
        ):
            raise _error("outcome_schema_invalid")
        self._validate_artifact_values()
        if self.failure is None:
            if (
                self.disposition != "scripted_fixture_assembled"
                or self.execution_branch != _BRANCH_SCRIPTED_FIXTURE
                or self.completed_steps != _ALL_SCRIPTED_STEPS
            ):
                raise _error("outcome_state_invalid")
            self._validate_reference_presence(True)
            self._validate_preparation_presence(True)
            self._validate_fixture_presence(True)
            if any(self.artifacts[key] is None for key in _ARTIFACT_KEYS):
                raise _error("artifact_binding_invalid")
            self._validate_fixture_artifact_binding()
            if self.artifacts["assembled_page_id"] != self.frozen_g0_reference.page_id:
                raise _error("artifact_binding_invalid")
            self._validate_private_assembly()
        else:
            self.failure.validate()
            rule = _FAILURES[self.failure.code]
            if (
                self.disposition != "fail_closed"
                or self.execution_branch != rule.execution_branch
                or self.completed_steps != rule.completed_steps
            ):
                raise _error("outcome_state_invalid")
            self._validate_reference_presence(rule.reference_present)
            self._validate_preparation_presence(rule.preparation_present)
            self._validate_fixture_presence(rule.fixture_present)
            if self.assembled_page_spec is not None:
                raise _error("outcome_state_invalid")
            self._validate_artifact_mode(rule.artifact_mode)
        if self.outcome_id != _MODEL_ROUTE_ID_PREFIX + _sha256(
            _canonical_json_bytes(self._root())
        ):
            raise _error("outcome_identity_invalid")

    @classmethod
    def from_dict(cls, payload: object) -> "ModelRouteOutcome":
        expected = {
            "schema_version",
            "disposition",
            "execution_branch",
            "completed_steps",
            "failure",
            "frozen_g0_reference",
            "frozen_g0_reference_sha256",
            "local_qwen_preparation",
            "scripted_local_fixture",
            "artifacts",
            "execution_declarations",
            "outcome_id",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise _error("outcome_exact_keys_invalid")
        if (
            not isinstance(payload["artifacts"], Mapping)
            or not isinstance(payload["completed_steps"], list)
        ):
            raise _error("outcome_state_invalid")
        try:
            result = cls(
                disposition=payload["disposition"],
                execution_branch=payload["execution_branch"],
                completed_steps=tuple(payload["completed_steps"]),
                failure=(
                    None
                    if payload["failure"] is None
                    else ModelRouteFailure.from_dict(payload["failure"])
                ),
                frozen_g0_reference=(
                    None
                    if payload["frozen_g0_reference"] is None
                    else _reference_from_dict(payload["frozen_g0_reference"])
                ),
                frozen_g0_reference_sha256=payload["frozen_g0_reference_sha256"],
                local_qwen_preparation=(
                    None
                    if payload["local_qwen_preparation"] is None
                    else LocalQwenProviderPreparationRecord.from_dict(
                        payload["local_qwen_preparation"]
                    )
                ),
                scripted_local_fixture=(
                    None
                    if payload["scripted_local_fixture"] is None
                    else ScriptedLocalFixtureProjection.from_dict(
                        payload["scripted_local_fixture"]
                    )
                ),
                artifacts=dict(payload["artifacts"]),
                assembled_page_spec=None,
                outcome_id=payload["outcome_id"],
                schema_version=payload["schema_version"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ModelRouteOutcomeError):
                raise
            raise _error("outcome_state_invalid") from exc
        if payload["execution_declarations"] != _declarations(
            result.execution_branch,
            result.scripted_local_fixture,
        ):
            raise _error("declarations_invalid")
        result.validate()
        return result

    @classmethod
    def from_bytes(cls, raw: bytes) -> "ModelRouteOutcome":
        result = cls.from_dict(_load_canonical_json(raw))
        if result.canonical_bytes() != raw:
            raise _error("serialized_bytes_invalid")
        return result

    def to_dict(self) -> dict[str, object]:
        self.validate()
        root = self._root()
        root["outcome_id"] = self.outcome_id
        return root

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def _validate_against_with_authorities(
        self,
        fixture_authority,
        assembly_authority,
        *,
        frozen_g0_reference: object,
        package: object,
        context: object,
        guidance: object,
        manifest: object = None,
        selected: object = None,
        local_request: object = None,
        pre_invocation_audit: object = None,
        local_qwen_preparation: object = None,
        execution_branch: object = None,
        scripted_local_fixture: object = None,
    ) -> None:
        """Replay every claimed stage against the exact supplied attempt."""

        self.validate()
        failure_code = None if self.failure is None else self.failure.code

        g0_attempt = lambda: _validate_live_g0(
            frozen_g0_reference,
            package,
            context,
            guidance,
        )
        if failure_code == "g0_reference_validation_failed":
            _require_replayed_failure(g0_attempt)
            return
        live_reference = _require_replayed_success(g0_attempt)
        if self.frozen_g0_reference != live_reference:
            raise _error("failure_replay_invalid")

        manifest_attempt = lambda: _validate_live_manifest(manifest)
        if failure_code == "d17_manifest_invalid":
            _require_replayed_failure(manifest_attempt)
            return
        live_manifest = _require_replayed_success(manifest_attempt)

        selection_attempt = lambda: _validate_live_selection(
            selected,
            context,
            live_manifest,
        )
        if failure_code == "d17_input_selection_invalid":
            _require_replayed_failure(selection_attempt)
            return
        live_selected = _require_replayed_success(selection_attempt)

        request_attempt = lambda: _validate_live_request(
            local_request,
            context,
            live_manifest,
        )
        if failure_code == "d17_local_request_invalid":
            _require_replayed_failure(request_attempt)
            return
        live_request = _require_replayed_success(request_attempt)

        audit_attempt = lambda: _validate_live_audit(
            pre_invocation_audit,
            context,
            live_manifest,
            live_selected,
            live_request,
        )
        if failure_code == "d17_pre_invocation_audit_invalid":
            _require_replayed_failure(audit_attempt)
            return
        live_audit = _require_replayed_success(audit_attempt)

        preparation_attempt = lambda: _validate_live_preparation(
            local_qwen_preparation,
            context,
            live_manifest,
            live_selected,
            live_request,
            live_audit,
        )
        if failure_code == "local_qwen_preparation_invalid":
            _require_replayed_failure(preparation_attempt)
            return
        live_preparation = _require_replayed_success(preparation_attempt)
        if self.local_qwen_preparation != live_preparation:
            raise _error("failure_replay_invalid")

        if failure_code == "route_branch_invalid":
            if execution_branch in _ALLOWED_REQUEST_BRANCHES:
                raise _error("failure_replay_invalid")
            return

        if failure_code in {
            "tier_b_unapproved",
            "local_qwen_invocation_unexpected_return",
        }:
            if execution_branch != _BRANCH_AUTHORIZATION_PROBE:
                raise _error("failure_replay_invalid")
            actual_gate = _invoke_gate_outcome()
            expected_gate = (
                "tier_b_unapproved"
                if failure_code == "tier_b_unapproved"
                else "unexpected"
            )
            if actual_gate != expected_gate:
                raise _error("failure_replay_invalid")
            return

        if execution_branch != _BRANCH_SCRIPTED_FIXTURE:
            raise _error("failure_replay_invalid")
        fixture_attempt = lambda: fixture_authority(scripted_local_fixture)
        if failure_code == "scripted_fixture_provenance_invalid":
            _require_replayed_failure(fixture_attempt)
            return
        live_fixture = _require_replayed_success(fixture_attempt)
        if self.scripted_local_fixture != live_fixture.safe_projection():
            raise _error("failure_replay_invalid")

        raw_response = live_fixture.raw_response
        parse_attempt = lambda: parse_provider_raw_response(raw_response)
        if failure_code == "semantic_candidate_invalid":
            _require_replayed_failure(parse_attempt)
            return
        candidate = _require_replayed_success(parse_attempt)
        if self.artifacts["model_semantic_candidate_sha256"] != candidate.sha256():
            raise _error("failure_replay_invalid")

        assembly_attempt = lambda: assembly_authority(
            raw_response,
            context,
            guidance,
        )
        if failure_code == "page_spec_assembly_invalid":
            _require_replayed_failure(assembly_attempt)
            return
        assembled = _require_replayed_success(assembly_attempt)
        report = assembled.report
        expected_artifacts = {
            "raw_response_sha256": raw_response.sha256,
            "raw_response_byte_length": len(raw_response.raw_bytes),
            "model_semantic_candidate_sha256": candidate.sha256(),
            "assembled_page_id": assembled.page_spec.page_id,
            "assembly_report_id": report.report_id,
            "assembly_report_sha256": report.sha256(),
            "assembled_page_spec_sha256": report.assembled_page_spec_sha256,
        }
        if (
            failure_code is not None
            or assembled.candidate.sha256() != candidate.sha256()
            or assembled.page_spec.page_id != live_reference.page_id
            or report.raw_response_sha256 != raw_response.sha256
            or report.candidate_sha256 != candidate.sha256()
            or dict(self.artifacts) != expected_artifacts
        ):
            raise _error("failure_replay_invalid")


def _bind_outcome_validate_against(fixture_authority, assembly_authority):
    core = ModelRouteOutcome._validate_against_with_authorities

    def validate_against(
        self,
        *,
        frozen_g0_reference: object,
        package: object,
        context: object,
        guidance: object,
        manifest: object = None,
        selected: object = None,
        local_request: object = None,
        pre_invocation_audit: object = None,
        local_qwen_preparation: object = None,
        execution_branch: object = None,
        scripted_local_fixture: object = None,
    ) -> None:
        return core(
            self,
            fixture_authority,
            assembly_authority,
            frozen_g0_reference=frozen_g0_reference,
            package=package,
            context=context,
            guidance=guidance,
            manifest=manifest,
            selected=selected,
            local_request=local_request,
            pre_invocation_audit=pre_invocation_audit,
            local_qwen_preparation=local_qwen_preparation,
            execution_branch=execution_branch,
            scripted_local_fixture=scripted_local_fixture,
        )

    return validate_against


ModelRouteOutcome.validate_against = _bind_outcome_validate_against(
    _FIXED_LIVE_FIXTURE_AUTHORITY,
    _FIXED_CANONICAL_ASSEMBLY_AUTHORITY,
)


def _require_replayed_success(callback):
    try:
        return callback()
    except Exception:
        raise _error("failure_replay_invalid") from None


def _require_replayed_failure(callback) -> None:
    try:
        callback()
    except Exception:
        return
    raise _error("failure_replay_invalid")


def _validate_live_g0(
    reference: object,
    package: object,
    context: object,
    guidance: object,
) -> FrozenG0PackageReference:
    if not isinstance(reference, FrozenG0PackageReference):
        raise TypeError("frozen G0 reference required")
    reference.validate_against(package, context, guidance)
    return reference


def _validate_live_manifest(manifest: object) -> D17Path3TierAManifest:
    if not isinstance(manifest, D17Path3TierAManifest):
        raise TypeError("D17 path 3 manifest required")
    manifest.validate()
    return manifest


def _validate_live_selection(
    selected: object,
    context: object,
    manifest: D17Path3TierAManifest,
) -> D17Path3SelectedInput:
    if not isinstance(selected, D17Path3SelectedInput):
        raise TypeError("D17 selected input required")
    selected.validate_against(context, manifest)
    return selected


def _validate_live_request(
    local_request: object,
    context: object,
    manifest: D17Path3TierAManifest,
) -> D17Path3LocalRequestArtifact:
    if not isinstance(local_request, D17Path3LocalRequestArtifact):
        raise TypeError("D17 local request required")
    local_request.validate_against(context, manifest)
    return local_request


def _validate_live_audit(
    audit: object,
    context: object,
    manifest: D17Path3TierAManifest,
    selected: D17Path3SelectedInput,
    local_request: D17Path3LocalRequestArtifact,
) -> D17Path3TierAPreInvocationAuditRecord:
    if not isinstance(audit, D17Path3TierAPreInvocationAuditRecord):
        raise TypeError("D17 pre-invocation audit required")
    audit.validate_against(context, manifest, selected, local_request)
    return audit


def _validate_live_preparation(
    preparation: object,
    context: object,
    manifest: D17Path3TierAManifest,
    selected: D17Path3SelectedInput,
    local_request: D17Path3LocalRequestArtifact,
    audit: D17Path3TierAPreInvocationAuditRecord,
) -> LocalQwenProviderPreparationRecord:
    if not isinstance(preparation, LocalQwenProviderPreparationRecord):
        raise TypeError("Local Qwen preparation required")
    preparation.validate_against(context, manifest, selected, local_request, audit)
    return preparation


def _invoke_gate_outcome() -> str:
    try:
        invoke_local_qwen_provider()
    except LocalQwenProviderInvocationBlockedError as exc:
        return "tier_b_unapproved" if exc.code == "tier_b_unapproved" else "unexpected"
    except Exception:
        return "unexpected"
    return "unexpected"


def validate_serialized_model_route_outcome(payload: object) -> ModelRouteOutcome:
    if isinstance(payload, bytes):
        return ModelRouteOutcome.from_bytes(payload)
    return ModelRouteOutcome.from_dict(payload)


def _build_orchestrator_run(fixture_authority, assembly_authority):
    captured_fixture_authority = fixture_authority
    captured_assembly_authority = assembly_authority

    def run(
        self,
        *,
        frozen_g0_reference: object,
        package: object,
        context: object,
        guidance: object,
        manifest: object,
        selected: object,
        local_request: object,
        pre_invocation_audit: object,
        local_qwen_preparation: object,
        execution_branch: object = _BRANCH_AUTHORIZATION_PROBE,
        scripted_local_fixture: object = None,
    ) -> ModelRouteOutcome:
        try:
            live_reference = _validate_live_g0(
                frozen_g0_reference,
                package,
                context,
                guidance,
            )
        except Exception:
            return self._failure("g0_reference_validation_failed")
        try:
            live_manifest = _validate_live_manifest(manifest)
        except Exception:
            return self._failure(
                "d17_manifest_invalid",
                frozen_g0_reference=live_reference,
            )
        try:
            live_selected = _validate_live_selection(selected, context, live_manifest)
        except Exception:
            return self._failure(
                "d17_input_selection_invalid",
                frozen_g0_reference=live_reference,
            )
        try:
            live_request = _validate_live_request(local_request, context, live_manifest)
        except Exception:
            return self._failure(
                "d17_local_request_invalid",
                frozen_g0_reference=live_reference,
            )
        try:
            live_audit = _validate_live_audit(
                pre_invocation_audit,
                context,
                live_manifest,
                live_selected,
                live_request,
            )
        except Exception:
            return self._failure(
                "d17_pre_invocation_audit_invalid",
                frozen_g0_reference=live_reference,
            )
        try:
            live_preparation = _validate_live_preparation(
                local_qwen_preparation,
                context,
                live_manifest,
                live_selected,
                live_request,
                live_audit,
            )
        except Exception:
            return self._failure(
                "local_qwen_preparation_invalid",
                frozen_g0_reference=live_reference,
            )

        if execution_branch not in _ALLOWED_REQUEST_BRANCHES:
            return self._failure(
                "route_branch_invalid",
                frozen_g0_reference=live_reference,
                local_qwen_preparation=live_preparation,
            )
        if execution_branch == _BRANCH_AUTHORIZATION_PROBE:
            gate_outcome = _invoke_gate_outcome()
            return self._failure(
                (
                    "tier_b_unapproved"
                    if gate_outcome == "tier_b_unapproved"
                    else "local_qwen_invocation_unexpected_return"
                ),
                frozen_g0_reference=live_reference,
                local_qwen_preparation=live_preparation,
            )

        try:
            live_fixture = captured_fixture_authority(scripted_local_fixture)
        except Exception:
            return self._failure(
                "scripted_fixture_provenance_invalid",
                frozen_g0_reference=live_reference,
                local_qwen_preparation=live_preparation,
            )
        fixture_projection = live_fixture.safe_projection()
        raw_response = live_fixture.raw_response
        raw_artifacts = {
            "raw_response_sha256": raw_response.sha256,
            "raw_response_byte_length": len(raw_response.raw_bytes),
        }
        try:
            candidate = parse_provider_raw_response(raw_response)
        except (ProviderProtocolError, TypeError, ValueError):
            return self._failure(
                "semantic_candidate_invalid",
                frozen_g0_reference=live_reference,
                local_qwen_preparation=live_preparation,
                scripted_local_fixture=fixture_projection,
                **raw_artifacts,
            )
        candidate_sha256 = candidate.sha256()
        try:
            assembled = captured_assembly_authority(raw_response, context, guidance)
            report = assembled.report
            if (
                assembled.candidate.sha256() != candidate_sha256
                or assembled.page_spec.page_id != live_reference.page_id
                or report.raw_response_sha256 != raw_response.sha256
                or report.candidate_sha256 != candidate_sha256
            ):
                raise ValueError("canonical assembly binding invalid")
        except (ProviderProtocolError, TypeError, ValueError):
            return self._failure(
                "page_spec_assembly_invalid",
                frozen_g0_reference=live_reference,
                local_qwen_preparation=live_preparation,
                scripted_local_fixture=fixture_projection,
                model_semantic_candidate_sha256=candidate_sha256,
                **raw_artifacts,
            )
        return ModelRouteOutcome.create(
            disposition="scripted_fixture_assembled",
            execution_branch=_BRANCH_SCRIPTED_FIXTURE,
            completed_steps=_ALL_SCRIPTED_STEPS,
            failure=None,
            frozen_g0_reference=live_reference,
            local_qwen_preparation=live_preparation,
            scripted_local_fixture=fixture_projection,
            raw_response_sha256=raw_response.sha256,
            raw_response_byte_length=len(raw_response.raw_bytes),
            model_semantic_candidate_sha256=candidate_sha256,
            assembled_page_id=assembled.page_spec.page_id,
            assembly_report_id=report.report_id,
            assembly_report_sha256=report.sha256(),
            assembled_page_spec_sha256=report.assembled_page_spec_sha256,
            assembled_page_spec=assembled,
        )

    return run


class TierAModelRouteOrchestrator:
    """Fail-closed Tier A route with captured canonical authorities."""

    __slots__ = ()

    run = _build_orchestrator_run(
        _FIXED_LIVE_FIXTURE_AUTHORITY,
        _FIXED_CANONICAL_ASSEMBLY_AUTHORITY,
    )

    @staticmethod
    def _failure(
        code: str,
        *,
        frozen_g0_reference: FrozenG0PackageReference | None = None,
        local_qwen_preparation: LocalQwenProviderPreparationRecord | None = None,
        scripted_local_fixture: ScriptedLocalFixtureProjection | None = None,
        **artifacts: str | int | None,
    ) -> ModelRouteOutcome:
        rule = _FAILURES[code]
        return ModelRouteOutcome.create(
            disposition="fail_closed",
            execution_branch=rule.execution_branch,
            completed_steps=rule.completed_steps,
            failure=ModelRouteFailure.fixed(code),
            frozen_g0_reference=frozen_g0_reference,
            local_qwen_preparation=local_qwen_preparation,
            scripted_local_fixture=scripted_local_fixture,
            **artifacts,
        )


# Tier A-07a is intentionally colocated with Tier A-06 because approval packet
# 13.1 fixes the real G1/G2 orchestration boundary to this module.  The A-06
# class above remains a no-I/O preparation route; the separate class below owns
# only the approved first-pass/no-change/frozen-fallback delivery slice.

TIER_A_07A_GATE_DELIVERY_OUTCOME_SCHEMA_VERSION = (
    "req2web.orchestration.tier_a_07a_gate_delivery_outcome.v1"
)
TIER_A_07A_GATE_DELIVERY_FAILURE_SCHEMA_VERSION = (
    "req2web.orchestration.tier_a_07a_gate_delivery_failure.v1"
)
SCRIPTED_ACCEPTANCE_FIXTURE_SCHEMA_VERSION = (
    "req2web.orchestration.scripted_acceptance_fixture.v1"
)
SCRIPTED_ACCEPTANCE_FIXTURE_REGISTRY_SCHEMA_VERSION = (
    "req2web.orchestration.scripted_acceptance_fixture_registry.v1"
)

SCRIPTED_ACCEPTANCE_PASS_KEY = "deterministic_pass_v1"
SCRIPTED_ACCEPTANCE_FAIL_KEY = "deterministic_fail_v1"
SCRIPTED_ACCEPTANCE_UNKNOWN_KEY = "deterministic_unknown_v1"

_07A_OUTCOME_ID_PREFIX = "tier-a-07a-gate-delivery-"
_07A_ACCEPTANCE_FIXTURE_ID_PREFIX = "scripted-acceptance-fixture-"
_07A_ACCEPTANCE_REGISTRY_ID_PREFIX = "scripted-acceptance-registry-"
_07A_FIXED_PAGE_URL = "file:///req2web-tier-a-07a-scripted/index.html"

_07A_STEP_MODEL_ROUTE = "model_route_live_validation"
_07A_STEP_FALLBACK_VALIDATION = "frozen_g0_fallback_validation"
_07A_STEP_DESTINATION_PREFLIGHT = "output_destination_preflight"
_07A_STEP_PAGE_SPEC = "assembled_page_spec_binding"
_07A_STEP_RENDER = "deterministic_render"
_07A_STEP_CONSISTENCY = "consistency_gate"
_07A_STEP_REQUIREMENT_VIEW = "independent_requirement_view"
_07A_STEP_ACCEPTANCE_PLAN = "independent_acceptance_plan"
_07A_STEP_ACCEPTANCE_BINDING = "acceptance_binding_plan"
_07A_STEP_ACCEPTANCE_FIXTURE = "scripted_acceptance_fixture_validation"
_07A_STEP_ACCEPTANCE_EXECUTION = "scripted_acceptance_execution"
_07A_STEP_G1_PACKAGE = "g1_result_package_v1"
_07A_STEP_G2_NO_CHANGE = "g2_no_change_delivery"
_07A_STEP_FALLBACK_DELIVERY = "frozen_g0_fallback_delivery"

_07A_CANDIDATE_SEQUENCE = (
    _07A_STEP_MODEL_ROUTE,
    _07A_STEP_FALLBACK_VALIDATION,
    _07A_STEP_DESTINATION_PREFLIGHT,
    _07A_STEP_PAGE_SPEC,
    _07A_STEP_RENDER,
    _07A_STEP_CONSISTENCY,
    _07A_STEP_REQUIREMENT_VIEW,
    _07A_STEP_ACCEPTANCE_PLAN,
    _07A_STEP_ACCEPTANCE_BINDING,
    _07A_STEP_ACCEPTANCE_FIXTURE,
    _07A_STEP_ACCEPTANCE_EXECUTION,
    _07A_STEP_G1_PACKAGE,
    _07A_STEP_G2_NO_CHANGE,
)
_07A_DIRECT_FALLBACK_PREFIX = (
    _07A_STEP_MODEL_ROUTE,
    _07A_STEP_FALLBACK_VALIDATION,
    _07A_STEP_DESTINATION_PREFLIGHT,
)

_07A_G0_BINDING_KEYS = (
    "reference_id",
    "reference_sha256",
    "package_id",
    "page_id",
    "package_schema_version",
    "package_manifest_sha256",
    "inventory_tree_sha256",
    "inventory_file_count",
)
_07A_FALLBACK_BINDING_KEYS = (
    "record_id",
    "record_sha256",
    "case_id",
    "package_id",
    "page_id",
    "package_schema_version",
    "package_manifest_sha256",
    "package_tree_sha256",
    "normalized_inventory_sha256",
    "inventory_file_count",
    "agent_context_bundle_id",
    "agent_context_bundle_sha256",
    "retrieval_guidance_bundle_id",
    "retrieval_guidance_bundle_sha256",
)
_07A_ACCEPTANCE_COUNT_KEYS = (
    "total",
    "pass",
    "fail",
    "unknown",
    "not_supported",
)
_07A_ARTIFACT_KEYS = (
    "page_spec_schema_version",
    "page_spec_id",
    "page_spec_sha256",
    "render_manifest_schema_version",
    "render_result_id",
    "render_manifest_sha256",
    "render_tree_sha256",
    "render_file_count",
    "consistency_report_schema_version",
    "consistency_report_id",
    "consistency_report_sha256",
    "requirement_view_schema_version",
    "requirement_view_id",
    "requirement_view_sha256",
    "acceptance_plan_schema_version",
    "acceptance_plan_id",
    "acceptance_plan_sha256",
    "acceptance_binding_schema_version",
    "acceptance_binding_plan_id",
    "acceptance_binding_plan_sha256",
    "browser_execution_schema_version",
    "browser_execution_report_id",
    "browser_execution_report_sha256",
    "model_package_schema_version",
    "model_package_id",
    "model_package_manifest_sha256",
    "model_package_tree_sha256",
    "model_package_file_count",
    "fallback_delivery_report_schema_version",
    "fallback_delivery_report_id",
    "fallback_delivery_report_sha256",
)

_07A_FAILURES = {
    "model_route_validation_failed": (
        "input_context",
        "model_route_live_validation",
        "the Tier A model-route claim did not replay against the supplied live chain",
    ),
    "frozen_g0_fallback_invalid": (
        "package",
        "frozen_g0_fallback_validation",
        "the pre-existing same-case frozen G0 fallback could not be verified",
    ),
    "output_destination_invalid": (
        "package",
        "output_destination_preflight",
        "an output destination is unsafe, overlapping, occupied, or non-real",
    ),
    "fallback_delivery_failed": (
        "package",
        "frozen_g0_fallback_delivery",
        "the verified frozen G0 fallback could not be delivered and revalidated",
    ),
}

_07A_FALLBACK_REASONS = {
    "model_route_not_assembled",
    "render_failed",
    "consistency_failed",
    "requirement_view_failed",
    "acceptance_plan_failed",
    "acceptance_binding_failed",
    "acceptance_fixture_invalid",
    "acceptance_execution_failed",
    "consistency_or_acceptance_blocked",
    "model_package_failed",
}

_07A_FALLBACK_REASON_PREFIXES = {
    "model_route_not_assembled": _07A_DIRECT_FALLBACK_PREFIX,
    "render_failed": (
        *_07A_DIRECT_FALLBACK_PREFIX,
        _07A_STEP_PAGE_SPEC,
    ),
    "consistency_failed": (
        *_07A_DIRECT_FALLBACK_PREFIX,
        _07A_STEP_PAGE_SPEC,
        _07A_STEP_RENDER,
    ),
    "requirement_view_failed": (
        *_07A_DIRECT_FALLBACK_PREFIX,
        _07A_STEP_PAGE_SPEC,
        _07A_STEP_RENDER,
        _07A_STEP_CONSISTENCY,
    ),
    "acceptance_plan_failed": (
        *_07A_DIRECT_FALLBACK_PREFIX,
        _07A_STEP_PAGE_SPEC,
        _07A_STEP_RENDER,
        _07A_STEP_CONSISTENCY,
        _07A_STEP_REQUIREMENT_VIEW,
    ),
    "acceptance_binding_failed": (
        *_07A_DIRECT_FALLBACK_PREFIX,
        _07A_STEP_PAGE_SPEC,
        _07A_STEP_RENDER,
        _07A_STEP_CONSISTENCY,
        _07A_STEP_REQUIREMENT_VIEW,
        _07A_STEP_ACCEPTANCE_PLAN,
    ),
    "acceptance_fixture_invalid": (
        *_07A_DIRECT_FALLBACK_PREFIX,
        _07A_STEP_PAGE_SPEC,
        _07A_STEP_RENDER,
        _07A_STEP_CONSISTENCY,
        _07A_STEP_REQUIREMENT_VIEW,
        _07A_STEP_ACCEPTANCE_PLAN,
        _07A_STEP_ACCEPTANCE_BINDING,
    ),
    "acceptance_execution_failed": (
        *_07A_DIRECT_FALLBACK_PREFIX,
        _07A_STEP_PAGE_SPEC,
        _07A_STEP_RENDER,
        _07A_STEP_CONSISTENCY,
        _07A_STEP_REQUIREMENT_VIEW,
        _07A_STEP_ACCEPTANCE_PLAN,
        _07A_STEP_ACCEPTANCE_BINDING,
        _07A_STEP_ACCEPTANCE_FIXTURE,
    ),
    "consistency_or_acceptance_blocked": (
        *_07A_DIRECT_FALLBACK_PREFIX,
        _07A_STEP_PAGE_SPEC,
        _07A_STEP_RENDER,
        _07A_STEP_CONSISTENCY,
        _07A_STEP_REQUIREMENT_VIEW,
        _07A_STEP_ACCEPTANCE_PLAN,
        _07A_STEP_ACCEPTANCE_BINDING,
        _07A_STEP_ACCEPTANCE_FIXTURE,
        _07A_STEP_ACCEPTANCE_EXECUTION,
    ),
    "model_package_failed": (
        *_07A_DIRECT_FALLBACK_PREFIX,
        _07A_STEP_PAGE_SPEC,
        _07A_STEP_RENDER,
        _07A_STEP_CONSISTENCY,
        _07A_STEP_REQUIREMENT_VIEW,
        _07A_STEP_ACCEPTANCE_PLAN,
        _07A_STEP_ACCEPTANCE_BINDING,
        _07A_STEP_ACCEPTANCE_FIXTURE,
        _07A_STEP_ACCEPTANCE_EXECUTION,
    ),
}

_07A_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _07a_empty_mapping(keys: tuple[str, ...]) -> dict[str, object]:
    return {key: None for key in keys}


def _07a_empty_artifacts() -> dict[str, object]:
    return _07a_empty_mapping(_07A_ARTIFACT_KEYS)


def _07a_empty_counts() -> dict[str, int]:
    return {key: 0 for key in _07A_ACCEPTANCE_COUNT_KEYS}


def _07a_safe_id(value: object) -> bool:
    return isinstance(value, str) and bool(_07A_SAFE_ID_RE.fullmatch(value))


def _07a_json_object(raw: bytes) -> dict[str, object]:
    try:
        text = raw.decode("utf-8")

        def reject_constant(_: str) -> object:
            raise ValueError("non-finite JSON constant")

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        value = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise TierA07aGateDeliveryOutcomeError("artifact_validation_failed") from exc
    if not isinstance(value, dict):
        raise TierA07aGateDeliveryOutcomeError("artifact_validation_failed")
    return value


class TierA07aGateDeliveryOutcomeError(ValueError):
    """Fixed payload-free error raised by Tier A-07a validators."""

    def __init__(self, code: str):
        messages = {
            "artifact_validation_failed": "Tier A-07a artifact validation failed",
            "declarations_invalid": "Tier A-07a execution declarations are invalid",
            "failure_invalid": "Tier A-07a failure envelope is invalid",
            "fixture_invalid": "Tier A-07a scripted acceptance fixture is invalid",
            "live_replay_invalid": "Tier A-07a live replay does not match the outcome",
            "outcome_exact_keys_invalid": "Tier A-07a outcome keys are invalid",
            "outcome_identity_invalid": "Tier A-07a outcome identity is invalid",
            "outcome_schema_invalid": "Tier A-07a outcome schema is invalid",
            "outcome_state_invalid": "Tier A-07a outcome state is invalid",
            "serialized_bytes_invalid": "Tier A-07a serialized bytes are invalid",
        }
        if code not in messages:
            code = "outcome_state_invalid"
        self.code = code
        super().__init__(messages[code])


@dataclass(frozen=True)
class TierA07aGateDeliveryFailure:
    code: str
    stage: str
    phase: str
    message: str
    retryable: bool = False
    payload_included: bool = False
    schema_version: str = TIER_A_07A_GATE_DELIVERY_FAILURE_SCHEMA_VERSION

    @classmethod
    def fixed(cls, code: str) -> "TierA07aGateDeliveryFailure":
        if code not in _07A_FAILURES:
            raise TierA07aGateDeliveryOutcomeError("failure_invalid")
        stage, phase, message = _07A_FAILURES[code]
        return cls(code=code, stage=stage, phase=phase, message=message)

    @classmethod
    def from_dict(cls, value: object) -> "TierA07aGateDeliveryFailure":
        expected = {
            "schema_version",
            "code",
            "stage",
            "phase",
            "message",
            "retryable",
            "payload_included",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise TierA07aGateDeliveryOutcomeError("failure_invalid")
        result = cls(
            schema_version=value["schema_version"],
            code=value["code"],
            stage=value["stage"],
            phase=value["phase"],
            message=value["message"],
            retryable=value["retryable"],
            payload_included=value["payload_included"],
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != TIER_A_07A_GATE_DELIVERY_FAILURE_SCHEMA_VERSION:
            raise TierA07aGateDeliveryOutcomeError("failure_invalid")
        if self.code not in _07A_FAILURES:
            raise TierA07aGateDeliveryOutcomeError("failure_invalid")
        stage, phase, message = _07A_FAILURES[self.code]
        if (
            self.stage != stage
            or self.phase != phase
            or self.message != message
            or self.retryable is not False
            or self.payload_included is not False
        ):
            raise TierA07aGateDeliveryOutcomeError("failure_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "code": self.code,
            "stage": self.stage,
            "phase": self.phase,
            "message": self.message,
            "retryable": self.retryable,
            "payload_included": self.payload_included,
        }


@dataclass(frozen=True)
class _ScriptedAcceptanceRegistryEntry:
    fixture_key: str
    execution_mode: str

    def to_dict(self) -> dict[str, str]:
        return {
            "fixture_key": self.fixture_key,
            "execution_mode": self.execution_mode,
        }


def _build_scripted_acceptance_fixture_authority(
    executor,
    evidence_unavailable_type,
):
    entries = (
        _ScriptedAcceptanceRegistryEntry(SCRIPTED_ACCEPTANCE_PASS_KEY, "pass"),
        _ScriptedAcceptanceRegistryEntry(SCRIPTED_ACCEPTANCE_FAIL_KEY, "fail"),
        _ScriptedAcceptanceRegistryEntry(SCRIPTED_ACCEPTANCE_UNKNOWN_KEY, "unknown"),
    )
    registry_schema_version = SCRIPTED_ACCEPTANCE_FIXTURE_REGISTRY_SCHEMA_VERSION
    registry_sha256 = "31ade200e4acb43e5fd4db32e2256fb36f105f4d367adf1eb1d14541967a21f8"
    registry_id = _07A_ACCEPTANCE_REGISTRY_ID_PREFIX + registry_sha256
    root = {
        "schema_version": registry_schema_version,
        "entries": [entry.to_dict() for entry in entries],
        "immutable": True,
        "runtime_registration_api": "none",
        "real_browser_executed": False,
        "network_used": False,
        "service_started": False,
    }
    if _sha256(_canonical_json_bytes(root)) != registry_sha256:
        raise RuntimeError("fixed scripted acceptance registry digest mismatch")
    by_key = {entry.fixture_key: entry for entry in entries}

    @dataclass(frozen=True)
    class ScriptedAcceptanceFixture:
        fixture_key: str
        execution_mode: str
        fixture_id: str
        registry_schema_version: str
        registry_id: str
        registry_sha256: str
        schema_version: str = SCRIPTED_ACCEPTANCE_FIXTURE_SCHEMA_VERSION
        fixture_scope: str = "local_deterministic_scripted_only"
        real_browser_executed: bool = False
        network_used: bool = False
        service_started: bool = False

        @classmethod
        def create(cls, fixture_key: str) -> "ScriptedAcceptanceFixture":
            if not isinstance(fixture_key, str) or fixture_key not in by_key:
                raise TierA07aGateDeliveryOutcomeError("fixture_invalid")
            entry = by_key[fixture_key]
            root_value = {
                "schema_version": SCRIPTED_ACCEPTANCE_FIXTURE_SCHEMA_VERSION,
                "fixture_key": entry.fixture_key,
                "execution_mode": entry.execution_mode,
                "registry_schema_version": registry_schema_version,
                "registry_id": registry_id,
                "registry_sha256": registry_sha256,
                "fixture_scope": "local_deterministic_scripted_only",
                "real_browser_executed": False,
                "network_used": False,
                "service_started": False,
            }
            result = cls(
                fixture_key=entry.fixture_key,
                execution_mode=entry.execution_mode,
                fixture_id=(
                    _07A_ACCEPTANCE_FIXTURE_ID_PREFIX
                    + _sha256(_canonical_json_bytes(root_value))
                ),
                registry_schema_version=registry_schema_version,
                registry_id=registry_id,
                registry_sha256=registry_sha256,
            )
            result.validate()
            return result

        @classmethod
        def from_dict(cls, value: object) -> "ScriptedAcceptanceFixture":
            expected = {
                "schema_version",
                "fixture_key",
                "execution_mode",
                "fixture_id",
                "registry_schema_version",
                "registry_id",
                "registry_sha256",
                "fixture_scope",
                "real_browser_executed",
                "network_used",
                "service_started",
            }
            if not isinstance(value, Mapping) or set(value) != expected:
                raise TierA07aGateDeliveryOutcomeError("fixture_invalid")
            result = cls(**value)
            result.validate()
            return result

        def _root(self) -> dict[str, object]:
            return {
                "schema_version": self.schema_version,
                "fixture_key": self.fixture_key,
                "execution_mode": self.execution_mode,
                "registry_schema_version": self.registry_schema_version,
                "registry_id": self.registry_id,
                "registry_sha256": self.registry_sha256,
                "fixture_scope": self.fixture_scope,
                "real_browser_executed": self.real_browser_executed,
                "network_used": self.network_used,
                "service_started": self.service_started,
            }

        def validate(self) -> None:
            if self.schema_version != SCRIPTED_ACCEPTANCE_FIXTURE_SCHEMA_VERSION:
                raise TierA07aGateDeliveryOutcomeError("fixture_invalid")
            if self.fixture_key not in by_key:
                raise TierA07aGateDeliveryOutcomeError("fixture_invalid")
            entry = by_key[self.fixture_key]
            if (
                self.execution_mode != entry.execution_mode
                or self.registry_schema_version != registry_schema_version
                or self.registry_id != registry_id
                or self.registry_sha256 != registry_sha256
                or self.fixture_scope != "local_deterministic_scripted_only"
                or self.real_browser_executed is not False
                or self.network_used is not False
                or self.service_started is not False
                or self.fixture_id
                != _07A_ACCEPTANCE_FIXTURE_ID_PREFIX
                + _sha256(_canonical_json_bytes(self._root()))
            ):
                raise TierA07aGateDeliveryOutcomeError("fixture_invalid")

        def to_dict(self) -> dict[str, object]:
            self.validate()
            result = self._root()
            result["fixture_id"] = self.fixture_id
            return result

    class _DeterministicAcceptanceBackend:
        __slots__ = ("mode", "state_ids", "feedback", "closed")

        def __init__(self, plan: AcceptanceBindingPlan, mode: str):
            self.mode = mode
            self.state_ids = [
                dict(step.expected_payload)["state_id"]
                for step in plan.steps
                if step.action_kind == "assert_state"
            ]
            self.feedback = [
                dict(step.expected_payload)["feedback"]
                for step in plan.steps
                if step.action_kind == "assert_feedback"
            ]
            self.closed = False

        def navigate(self, page_url: str, timeout_ms: int) -> Mapping[str, str]:
            if self.mode == "unknown":
                raise evidence_unavailable_type("scripted browser evidence is unavailable")
            return {"loaded": "true", "transport": "local_scripted"}

        def element_exists(self, selector: str, timeout_ms: int) -> bool:
            return self.mode != "fail"

        def trigger(self, selector: str, timeout_ms: int) -> Mapping[str, str]:
            return {"triggered": "true", "mechanism": "click"}

        def read_state(self, timeout_ms: int) -> Mapping[str, str]:
            if not self.state_ids:
                raise evidence_unavailable_type("scripted state evidence is exhausted")
            return {"state_id": self.state_ids.pop(0)}

        def read_text(self, selector: str, timeout_ms: int) -> str | None:
            if not self.feedback:
                raise evidence_unavailable_type("scripted feedback evidence is exhausted")
            return self.feedback.pop(0)

        def close(self) -> None:
            self.closed = True

    def authority(value: object) -> ScriptedAcceptanceFixture:
        if type(value) is not ScriptedAcceptanceFixture:
            raise TierA07aGateDeliveryOutcomeError("fixture_invalid")
        value.validate()
        return value

    def execute(
        fixture: object,
        binding_plan: AcceptanceBindingPlan,
    ) -> BrowserExecutionReport:
        live_fixture = authority(fixture)
        backend = _DeterministicAcceptanceBackend(
            binding_plan,
            live_fixture.execution_mode,
        )
        report = executor(
            binding_plan,
            _07A_FIXED_PAGE_URL,
            backend=backend,
            timeout_ms=5_000,
            clock=lambda: 0.0,
        )
        report.validate_against(binding_plan)
        return report

    report_entries = tuple(
        _ScriptedAcceptanceRegistryEntry(**entry.to_dict()) for entry in entries
    )
    return (
        ScriptedAcceptanceFixture,
        authority,
        execute,
        report_entries,
        registry_schema_version,
        registry_id,
        registry_sha256,
    )


(
    ScriptedAcceptanceFixture,
    _FIXED_ACCEPTANCE_FIXTURE_AUTHORITY,
    _FIXED_ACCEPTANCE_EXECUTION_AUTHORITY,
    _FIXED_ACCEPTANCE_REGISTRY_ENTRY_REPORTS,
    _FIXED_ACCEPTANCE_REGISTRY_SCHEMA_VERSION,
    _FIXED_ACCEPTANCE_REGISTRY_ID,
    _FIXED_ACCEPTANCE_REGISTRY_SHA256,
) = _build_scripted_acceptance_fixture_authority(
    execute_acceptance_binding_plan,
    BrowserEvidenceUnavailable,
)

# Report-only copies.  Captured lookup and execution do not trust these names.
_SCRIPTED_ACCEPTANCE_FIXTURE_REGISTRY_ENTRIES = tuple(
    _FIXED_ACCEPTANCE_REGISTRY_ENTRY_REPORTS
)
SCRIPTED_ACCEPTANCE_FIXTURE_REGISTRY_ID = _FIXED_ACCEPTANCE_REGISTRY_ID
SCRIPTED_ACCEPTANCE_FIXTURE_REGISTRY_SHA256 = _FIXED_ACCEPTANCE_REGISTRY_SHA256


@dataclass(frozen=True)
class _TierA07aDestinationPlan:
    render_output_dir: object
    model_package_output_dir: object
    fallback_output_dir: object
    render_existed: bool
    model_package_existed: bool
    fallback_existed: bool


@dataclass(frozen=True)
class _TierA07aCommitReceipt:
    destination_existed: bool
    created_names: tuple[str, ...]


def _build_tier_a_07a_path_authority(
    rmtree_fn,
    uuid_fn,
    is_link_mode,
    reparse_flag: int,
):
    def load_bytes(path: object) -> bytes:
        reader = getattr(path, "read_bytes")
        value = reader()
        if not isinstance(value, bytes):
            raise TierA07aGateDeliveryOutcomeError("artifact_validation_failed")
        return value

    def lstat_or_none(path: object) -> object | None:
        try:
            return getattr(path, "lstat")()
        except FileNotFoundError:
            return None

    def is_link_or_reparse(path: object) -> bool:
        info = lstat_or_none(path)
        if info is None:
            return False
        if is_link_mode(info.st_mode):
            return True
        attributes = getattr(info, "st_file_attributes", 0)
        return bool(attributes & reparse_flag)

    def validate_raw_path(value: object, field_name: str) -> object:
        expanded = getattr(value, "expanduser")()
        if ".." in tuple(getattr(expanded, "parts")):
            raise ValueError(field_name + " must not contain traversal")
        absolute = (
            expanded
            if getattr(expanded, "is_absolute")()
            else getattr(expanded, "absolute")()
        )
        current = absolute
        while True:
            if is_link_or_reparse(current):
                raise ValueError(
                    field_name + " must not traverse a link or reparse point"
                )
            parent = getattr(current, "parent")
            if parent == current:
                break
            current = parent
        return absolute

    def require_real_directory(path: object, field_name: str) -> object:
        validate_raw_path(path, field_name)
        if not getattr(path, "exists")() or is_link_or_reparse(path):
            raise ValueError(field_name + " must be a real directory")
        if not getattr(path, "is_dir")():
            raise ValueError(field_name + " must be a real directory")
        return path

    def within(path: object, root: object) -> bool:
        try:
            getattr(path, "relative_to")(root)
            return True
        except ValueError:
            return False

    def normalize_destination(
        value: object,
        *,
        path_type: type,
        field_name: str,
    ) -> tuple[object, bool]:
        if not isinstance(value, path_type):
            raise ValueError(field_name + " must use the live package path type")
        expanded = validate_raw_path(value, field_name)
        destination = getattr(expanded, "resolve")(strict=False)
        parent = getattr(destination, "parent")
        require_real_directory(parent, field_name + " parent")
        existed = lstat_or_none(destination) is not None
        if existed:
            require_real_directory(destination, field_name)
            if next(iter(getattr(destination, "iterdir")()), None) is not None:
                raise ValueError(field_name + " must be empty or new")
        return destination, existed

    def preflight(
        render_output_dir: object,
        model_package_output_dir: object,
        fallback_output_dir: object,
        *,
        package_dir: object,
        snapshot_dir: object,
    ) -> _TierA07aDestinationPlan:
        path_type = type(package_dir)
        if not isinstance(snapshot_dir, path_type):
            raise ValueError("snapshot_dir must use the live package path type")
        package_root = getattr(
            validate_raw_path(package_dir, "G0 package root"),
            "resolve",
        )(strict=True)
        snapshot_root = getattr(
            validate_raw_path(snapshot_dir, "frozen fallback snapshot root"),
            "resolve",
        )(strict=True)
        require_real_directory(package_root, "G0 package root")
        require_real_directory(snapshot_root, "frozen fallback snapshot root")
        render, render_existed = normalize_destination(
            render_output_dir,
            path_type=path_type,
            field_name="render_output_dir",
        )
        model_package, model_existed = normalize_destination(
            model_package_output_dir,
            path_type=path_type,
            field_name="model_package_output_dir",
        )
        fallback, fallback_existed = normalize_destination(
            fallback_output_dir,
            path_type=path_type,
            field_name="fallback_output_dir",
        )
        destinations = (render, model_package, fallback)
        for index, left in enumerate(destinations):
            for right in destinations[index + 1 :]:
                if within(left, right) or within(right, left):
                    raise ValueError("output destinations must be pairwise disjoint")
        for destination in destinations:
            for protected in (package_root, snapshot_root):
                if within(destination, protected) or within(protected, destination):
                    raise ValueError("output destination overlaps a protected input")
        return _TierA07aDestinationPlan(
            render_output_dir=render,
            model_package_output_dir=model_package,
            fallback_output_dir=fallback,
            render_existed=render_existed,
            model_package_existed=model_existed,
            fallback_existed=fallback_existed,
        )

    def staging_path(destination: object, label: str) -> object:
        validate_raw_path(destination, "staging destination")
        parent = getattr(destination, "parent")
        name = getattr(destination, "name")
        staging = parent / ("." + name + "." + label + "-" + uuid_fn().hex)
        if lstat_or_none(staging) is not None:
            raise ValueError("staging path already exists")
        return staging

    def cleanup_directory(path: object) -> None:
        try:
            validate_raw_path(path, "cleanup path")
            if getattr(path, "exists")():
                if is_link_or_reparse(path) or not getattr(path, "is_dir")():
                    return
                rmtree_fn(path)
        except Exception:
            return

    def remove_entry(path: object) -> None:
        try:
            validate_raw_path(path, "cleanup entry")
            if lstat_or_none(path) is None:
                return
            if is_link_or_reparse(path):
                try:
                    getattr(path, "unlink")()
                except Exception:
                    try:
                        getattr(path, "rmdir")()
                    except Exception:
                        return
                return
            if getattr(path, "is_dir")():
                rmtree_fn(path)
            else:
                getattr(path, "unlink")()
        except Exception:
            return

    def commit_staging(
        staging: object,
        destination: object,
        *,
        destination_existed: bool,
    ) -> _TierA07aCommitReceipt:
        validate_raw_path(staging, "staging path")
        validate_raw_path(destination, "output destination")
        if destination_existed:
            require_real_directory(destination, "output destination")
            if next(iter(getattr(destination, "iterdir")()), None) is not None:
                raise ValueError("existing output destination must be empty")
            created: list[str] = []
            try:
                children = sorted(
                    tuple(getattr(staging, "iterdir")()),
                    key=lambda item: getattr(item, "name"),
                )
                for child in children:
                    target = destination / getattr(child, "name")
                    if lstat_or_none(target) is not None:
                        raise ValueError("output destination changed during commit")
                    getattr(child, "replace")(target)
                    created.append(getattr(child, "name"))
                getattr(staging, "rmdir")()
            except Exception:
                for name in reversed(created):
                    remove_entry(destination / name)
                raise
            return _TierA07aCommitReceipt(
                destination_existed=True,
                created_names=tuple(created),
            )
        if lstat_or_none(destination) is not None:
            raise ValueError("new output destination already exists")
        getattr(staging, "replace")(destination)
        return _TierA07aCommitReceipt(
            destination_existed=False,
            created_names=(),
        )

    def rollback_commit(
        receipt: _TierA07aCommitReceipt | None,
        destination: object,
    ) -> None:
        if receipt is None:
            return
        if receipt.destination_existed:
            for name in reversed(receipt.created_names):
                remove_entry(destination / name)
        else:
            cleanup_directory(destination)

    def replay_layout(
        render_output_dir: object,
        model_package_output_dir: object,
        fallback_output_dir: object,
        *,
        package_dir: object,
        snapshot_dir: object,
        render_expected: bool,
        model_package_expected: bool,
        fallback_expected: bool,
    ) -> _TierA07aDestinationPlan:
        path_type = type(package_dir)
        values = (
            render_output_dir,
            model_package_output_dir,
            fallback_output_dir,
            snapshot_dir,
        )
        if any(not isinstance(value, path_type) for value in values):
            raise ValueError("all paths must use the live package path type")
        package_root = getattr(
            validate_raw_path(package_dir, "G0 package root"),
            "resolve",
        )(strict=True)
        snapshot_root = getattr(
            validate_raw_path(snapshot_dir, "frozen fallback snapshot root"),
            "resolve",
        )(strict=True)
        require_real_directory(package_root, "G0 package root")
        require_real_directory(snapshot_root, "frozen fallback snapshot root")
        destinations: list[object] = []
        for index, value in enumerate(
            (render_output_dir, model_package_output_dir, fallback_output_dir)
        ):
            expanded = validate_raw_path(value, "output destination " + str(index))
            destination = getattr(expanded, "resolve")(strict=False)
            require_real_directory(
                getattr(destination, "parent"),
                "output destination parent",
            )
            destinations.append(destination)
        destinations_tuple = tuple(destinations)
        for index, left in enumerate(destinations_tuple):
            for right in destinations_tuple[index + 1 :]:
                if within(left, right) or within(right, left):
                    raise ValueError("output destinations overlap")
        for destination in destinations_tuple:
            for protected in (package_root, snapshot_root):
                if within(destination, protected) or within(protected, destination):
                    raise ValueError("output overlaps a protected input")
        expected = (render_expected, model_package_expected, fallback_expected)
        for destination, should_exist in zip(destinations_tuple, expected, strict=True):
            exists = lstat_or_none(destination) is not None
            if should_exist:
                if not exists or not getattr(destination, "is_dir")():
                    raise ValueError("expected output directory is missing")
            elif exists:
                if is_link_or_reparse(destination) or not getattr(
                    destination, "is_dir"
                ):
                    raise ValueError("unused output path is not a directory")
                if next(iter(getattr(destination, "iterdir")()), None) is not None:
                    raise ValueError("unused output directory is not empty")
        return _TierA07aDestinationPlan(
            render_output_dir=destinations_tuple[0],
            model_package_output_dir=destinations_tuple[1],
            fallback_output_dir=destinations_tuple[2],
            render_existed=False,
            model_package_existed=False,
            fallback_existed=False,
        )

    return (
        load_bytes,
        validate_raw_path,
        preflight,
        staging_path,
        cleanup_directory,
        commit_staging,
        rollback_commit,
        replay_layout,
    )


(
    _FIXED_07A_READ_BYTES,
    _FIXED_07A_VALIDATE_RAW_PATH,
    _FIXED_07A_PATH_PREFLIGHT,
    _FIXED_07A_STAGING_PATH,
    _FIXED_07A_CLEANUP_DIRECTORY,
    _FIXED_07A_COMMIT_STAGING,
    _FIXED_07A_ROLLBACK_COMMIT,
    _FIXED_07A_PATH_REPLAY,
) = _build_tier_a_07a_path_authority(
    _stdlib_rmtree,
    _stdlib_uuid4,
    _stat.S_ISLNK,
    getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
)


_07A_MODEL_ROUTE_BINDING_KEYS = (
    "verified",
    "outcome_id",
    "outcome_sha256",
    "disposition",
    "failure_code",
    "assembled_page_id",
    "assembled_page_spec_sha256",
)


def _07a_declarations(
    fixture: object,
    *,
    registry_schema_version: str = _FIXED_ACCEPTANCE_REGISTRY_SCHEMA_VERSION,
    registry_id: str = _FIXED_ACCEPTANCE_REGISTRY_ID,
    registry_sha256: str = _FIXED_ACCEPTANCE_REGISTRY_SHA256,
) -> dict[str, object]:
    return {
        "tier_a_local_only": True,
        "tier_a_07a_slice": "first_pass_no_change_fallback_only",
        "approval_step_6_complete": False,
        "d17_path": "path_3",
        "external_egress_allowed": False,
        "not_sent": True,
        "model_loaded": False,
        "provider_invoked": False,
        "real_browser_executed": False,
        "network_used": False,
        "service_started": False,
        "acceptance_execution_mode": (
            "local_deterministic_scripted_only"
            if fixture is not None
            else "not_used"
        ),
        "scripted_acceptance_registry_schema_version": registry_schema_version,
        "scripted_acceptance_registry_id": registry_id,
        "scripted_acceptance_registry_sha256": registry_sha256,
        "repair_limit": 1,
        "repair_attempted": 0,
        "repair_status": "not_attempted_tier_a_07a",
        "retry_performed": False,
        "formal_quality_status": "not_executed",
        "h1_gold_accessed": False,
        "fallback_rebuilt": False,
        "guided_builder_invoked": False,
        "unguided_builder_invoked": False,
    }


def _07a_model_route_binding(outcome: ModelRouteOutcome) -> dict[str, object]:
    outcome.validate()
    raw = outcome.canonical_bytes()
    return {
        "verified": True,
        "outcome_id": outcome.outcome_id,
        "outcome_sha256": _sha256(raw),
        "disposition": outcome.disposition,
        "failure_code": None if outcome.failure is None else outcome.failure.code,
        "assembled_page_id": outcome.artifacts["assembled_page_id"],
        "assembled_page_spec_sha256": outcome.artifacts[
            "assembled_page_spec_sha256"
        ],
    }


def _07a_g0_binding(reference: FrozenG0PackageReference | None) -> dict[str, object]:
    if reference is None:
        return _07a_empty_mapping(_07A_G0_BINDING_KEYS)
    reference.validate()
    return {
        "reference_id": reference.reference_id,
        "reference_sha256": _reference_sha256(reference),
        "package_id": reference.package_id,
        "page_id": reference.page_id,
        "package_schema_version": reference.package_schema_version,
        "package_manifest_sha256": reference.package_manifest_sha256,
        "inventory_tree_sha256": reference.inventory_tree_sha256,
        "inventory_file_count": len(reference.inventory),
    }


def _07a_normalized_inventory_sha256(entries: object) -> str:
    normalized = [
        {
            "path": entry.path,
            "role": entry.role,
            "size": entry.size,
            "sha256": entry.sha256,
        }
        for entry in entries
    ]
    return _sha256(_canonical_json_bytes({"files": normalized}))


def _build_tier_a_07a_fallback_authority(record_type, package_type,
                                          _safe_id=_07a_safe_id):
    def validate_binding(
        *,
        case_id: object,
        record: object,
        snapshot_dir: object,
        reference: object,
        package: object,
        context: object,
        guidance: object,
    ) -> dict[str, object]:
        if not _safe_id(case_id):
            raise ValueError("case_id is invalid")
        if type(record) is not record_type:
            raise TypeError("fallback_record must be a FrozenG0FallbackRecord")
        if type(package) is not package_type:
            raise TypeError("package must be the exact G0 ResultPackage v2 type")
        if not isinstance(reference, FrozenG0PackageReference):
            raise TypeError("reference must be a FrozenG0PackageReference")
        reference.validate_against(package, context, guidance)
        record.validate_against(snapshot_dir)
        if record.case_id != case_id:
            raise ValueError("fallback record case_id is cross-case")
        if (
            record.package_id != reference.package_id
            or record.page_id != reference.page_id
            or record.package_schema_version != reference.package_schema_version
            or record.package_manifest_sha256 != reference.package_manifest_sha256
        ):
            raise ValueError("fallback record does not bind the frozen G0 package")
        reference_inventory = tuple(
            (item.relative_path, item.role, item.size, item.sha256)
            for item in reference.inventory
        )
        record_inventory = tuple(
            (item.path, item.role, item.size, item.sha256)
            for item in record.package_inventory
        )
        if reference_inventory != record_inventory:
            raise ValueError("fallback snapshot inventory differs from frozen G0")
        inventory_by_path = {
            item.relative_path: item for item in reference.inventory
        }
        provenance = record.provenance
        expected_bindings = (
            (provenance.agent_context_bundle_id, reference.context_id),
            (provenance.agent_context_bundle_sha256, reference.context_sha256),
            (provenance.retrieval_guidance_bundle_id, reference.guidance_bundle_id),
            (provenance.retrieval_guidance_bundle_sha256, reference.guidance_sha256),
            (provenance.page_spec_id, reference.page_id),
            (
                provenance.page_spec_sha256,
                inventory_by_path["internal/page_spec.json"].sha256,
            ),
            (
                provenance.guided_page_spec_build_result_sha256,
                inventory_by_path[
                    "internal/guided_page_spec_build_result.json"
                ].sha256,
            ),
            (
                provenance.influence_report_sha256,
                inventory_by_path[
                    "internal/retrieval_influence_report.json"
                ].sha256,
            ),
        )
        if any(left != right for left, right in expected_bindings):
            raise ValueError("fallback provenance differs from frozen G0")
        return {
            "record_id": record.record_id,
            "record_sha256": record.record_sha256,
            "case_id": record.case_id,
            "package_id": record.package_id,
            "page_id": record.page_id,
            "package_schema_version": record.package_schema_version,
            "package_manifest_sha256": record.package_manifest_sha256,
            "package_tree_sha256": record.package_tree_sha256,
            "normalized_inventory_sha256": (
                _07a_normalized_inventory_sha256(record.package_inventory)
            ),
            "inventory_file_count": len(record.package_inventory),
            "agent_context_bundle_id": provenance.agent_context_bundle_id,
            "agent_context_bundle_sha256": provenance.agent_context_bundle_sha256,
            "retrieval_guidance_bundle_id": provenance.retrieval_guidance_bundle_id,
            "retrieval_guidance_bundle_sha256": (
                provenance.retrieval_guidance_bundle_sha256
            ),
        }

    return validate_binding


_FIXED_07A_FALLBACK_BINDING_AUTHORITY = _build_tier_a_07a_fallback_authority(
    FrozenG0FallbackRecord,
    RetrievalEnhancedResultPackage,
)


def _07a_page_spec_artifacts(page_spec: PageSpec) -> dict[str, object]:
    if not isinstance(page_spec, PageSpec):
        raise TypeError("page_spec must be a PageSpec")
    page_spec.validate()
    digest = _sha256(_canonical_json_bytes(page_spec.to_dict()))
    return {
        "page_spec_schema_version": PAGE_SPEC_SCHEMA_VERSION,
        "page_spec_id": page_spec.page_id,
        "page_spec_sha256": digest,
    }



def _build_tier_a_07a_artifact_authority(
    *,
    render_result_type,
    consistency_report_type,
    requirement_view_type,
    acceptance_plan_type,
    acceptance_binding_type,
    browser_report_type,
    result_package_type,
    packager_type,
    fallback_report_type,
    load_bytes,
    staging_path,
    cleanup_directory,
    validate_raw_path,
    _result_package_schema_version=RESULT_PACKAGE_SCHEMA_VERSION,
):
    def artifact_id(prefix: str, digest: str) -> str:
        return prefix + digest[:20]

    def render_projection(
        page_spec: PageSpec,
        render_result: object,
    ) -> dict[str, object]:
        if type(render_result) is not render_result_type:
            raise TypeError("render_result has the wrong type")
        if render_result.page_id != page_spec.page_id:
            raise ValueError("render_result page_id is inconsistent")
        root = getattr(render_result.output_dir, "resolve")(strict=True)
        expected_paths = {
            "index.html": render_result.index_html,
            "styles.css": render_result.styles_css,
            "app.js": render_result.app_js,
            "render_manifest.json": render_result.render_manifest,
        }
        inventory: list[dict[str, object]] = []
        file_bytes: dict[str, bytes] = {}
        for name in sorted(expected_paths):
            path = getattr(expected_paths[name], "resolve")(strict=True)
            if path != root / name or not getattr(path, "is_file")():
                raise ValueError("render output path binding is invalid")
            content = load_bytes(path)
            file_bytes[name] = content
            inventory.append(
                {
                    "path": name,
                    "size": len(content),
                    "sha256": _sha256(content),
                }
            )
        manifest = _07a_json_object(file_bytes["render_manifest.json"])
        if set(manifest) != {
            "files",
            "page_id",
            "page_spec_schema_version",
            "schema_version",
        }:
            raise ValueError("render manifest fields are invalid")
        if (
            manifest["schema_version"] != RENDER_MANIFEST_SCHEMA_VERSION
            or manifest["page_spec_schema_version"] != PAGE_SPEC_SCHEMA_VERSION
            or manifest["page_id"] != page_spec.page_id
        ):
            raise ValueError("render manifest identity is invalid")
        declared = manifest["files"]
        if not isinstance(declared, list):
            raise ValueError("render manifest files are invalid")
        expected_declared = [
            {"name": name, "sha256": _sha256(file_bytes[name])}
            for name in ("index.html", "styles.css", "app.js")
        ]
        if declared != expected_declared:
            raise ValueError("render manifest file binding is invalid")
        manifest_sha256 = _sha256(file_bytes["render_manifest.json"])
        tree_sha256 = _sha256(_canonical_json_bytes({"files": inventory}))
        return {
            "render_manifest_schema_version": RENDER_MANIFEST_SCHEMA_VERSION,
            "render_result_id": artifact_id("render-result-", tree_sha256),
            "render_manifest_sha256": manifest_sha256,
            "render_tree_sha256": tree_sha256,
            "render_file_count": len(inventory),
        }

    def consistency_projection(report: object) -> dict[str, object]:
        if type(report) is not consistency_report_type:
            raise TypeError("consistency_report has the wrong type")
        report.validate()
        digest = _sha256(_canonical_json_bytes(report.to_dict()))
        return {
            "consistency_report_schema_version": CONSISTENCY_REPORT_SCHEMA_VERSION,
            "consistency_report_id": artifact_id("consistency-report-", digest),
            "consistency_report_sha256": digest,
        }

    def requirement_projection(view: object) -> dict[str, object]:
        if type(view) is not requirement_view_type:
            raise TypeError("requirement_view has the wrong type")
        view.validate()
        digest = view.sha256()
        return {
            "requirement_view_schema_version": (
                INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION
            ),
            "requirement_view_id": artifact_id("requirement-view-", digest),
            "requirement_view_sha256": digest,
        }

    def plan_projection(plan: object) -> dict[str, object]:
        if type(plan) is not acceptance_plan_type:
            raise TypeError("acceptance_plan has the wrong type")
        plan.validate()
        digest = plan.sha256()
        return {
            "acceptance_plan_schema_version": ACCEPTANCE_PLAN_SCHEMA_VERSION,
            "acceptance_plan_id": artifact_id("acceptance-plan-", digest),
            "acceptance_plan_sha256": digest,
        }

    def binding_projection(binding: object) -> dict[str, object]:
        if type(binding) is not acceptance_binding_type:
            raise TypeError("acceptance_binding has the wrong type")
        binding.validate()
        digest = binding.sha256()
        return {
            "acceptance_binding_schema_version": ACCEPTANCE_BINDING_SCHEMA_VERSION,
            "acceptance_binding_plan_id": artifact_id(
                "acceptance-binding-", digest
            ),
            "acceptance_binding_plan_sha256": digest,
        }

    def browser_projection(report: object) -> dict[str, object]:
        if type(report) is not browser_report_type:
            raise TypeError("browser_report has the wrong type")
        report.validate()
        digest = _sha256(report.canonical_json_bytes())
        return {
            "browser_execution_schema_version": BROWSER_EXECUTION_SCHEMA_VERSION,
            "browser_execution_report_id": artifact_id(
                "browser-execution-", digest
            ),
            "browser_execution_report_sha256": digest,
        }

    def acceptance_counts(report: object) -> dict[str, int]:
        if type(report) is not browser_report_type:
            raise TypeError("browser_report has the wrong type")
        report.validate()
        counts = Counter(item.status for item in report.criteria)
        return {
            "total": len(report.criteria),
            "pass": counts["pass"],
            "fail": counts["fail"],
            "unknown": counts["unknown"],
            "not_supported": counts["not_supported"],
        }


    def package_projection(package: object, expected_dir: object) -> dict[str, object]:
        if type(package) is not result_package_type:
            raise TypeError("model package has the wrong type")
        package.validate()
        if getattr(package.package_dir, "resolve")(strict=True) != getattr(
            expected_dir, "resolve"
        )(strict=True):
            raise ValueError("model package destination is inconsistent")
        manifest_path = package.package_dir / package.package_manifest
        manifest_bytes = load_bytes(manifest_path)
        manifest = _07a_json_object(manifest_bytes)
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("model package manifest inventory is invalid")
        inventory: list[dict[str, object]] = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "path",
                "role",
                "sha256",
                "size",
            }:
                raise ValueError("model package inventory entry is invalid")
            inventory.append(dict(entry))
        inventory.append(
            {
                "path": package.package_manifest,
                "role": "package_manifest",
                "size": len(manifest_bytes),
                "sha256": _sha256(manifest_bytes),
            }
        )
        inventory.sort(key=lambda item: item["path"])
        tree_sha256 = _sha256(_canonical_json_bytes({"files": inventory}))
        return {
            "model_package_schema_version": _result_package_schema_version,
            "model_package_id": package.package_id,
            "model_package_manifest_sha256": _sha256(manifest_bytes),
            "model_package_tree_sha256": tree_sha256,
            "model_package_file_count": len(inventory),
        }

    def package_tree_bytes(root: object) -> dict[str, bytes]:
        validate_raw_path(root, "model package tree")
        if not getattr(root, "exists")() or not getattr(root, "is_dir")():
            raise ValueError("model package tree is not a directory")
        result: dict[str, bytes] = {}
        paths = sorted(
            tuple(getattr(root, "rglob")("*")),
            key=lambda item: getattr(item, "relative_to")(root).as_posix(),
        )
        for path in paths:
            validate_raw_path(path, "model package tree entry")
            if getattr(path, "is_file")():
                result[getattr(path, "relative_to")(root).as_posix()] = load_bytes(
                    path
                )
        return result

    def package_live_binding(
        *,
        context: object,
        page_spec: PageSpec,
        render_result: object,
        consistency_report: object,
        supplied_package: object,
        supplied_dir: object,
    ) -> dict[str, object]:
        supplied_projection = package_projection(supplied_package, supplied_dir)
        replay_dir = staging_path(
            supplied_dir,
            "tier-a-07a-package-live-binding",
        )
        try:
            expected_package = packager_type().package(
                context,
                page_spec,
                render_result,
                consistency_report,
                replay_dir,
            )
            expected_projection = package_projection(expected_package, replay_dir)
            if (
                expected_projection != supplied_projection
                or package_tree_bytes(replay_dir)
                != package_tree_bytes(supplied_dir)
            ):
                raise ValueError("model package live binding is invalid")
            return expected_projection
        finally:
            cleanup_directory(replay_dir)

    def load_fallback_report(
        fallback_output_dir: object,
        snapshot_dir: object,
        record: FrozenG0FallbackRecord,
    ) -> FallbackDeliveryReport:
        report_path = fallback_output_dir / "fallback_delivery_report.json"
        report = fallback_report_type.from_dict(
            _07a_json_object(load_bytes(report_path))
        )
        report.validate_against(
            snapshot_dir,
            fallback_output_dir / "result_package",
            record,
        )
        return report

    def fallback_report_projection(report: object) -> dict[str, object]:
        if type(report) is not fallback_report_type:
            raise TypeError("fallback report has the wrong type")
        report.validate()
        return {
            "fallback_delivery_report_schema_version": (
                FALLBACK_DELIVERY_REPORT_SCHEMA_VERSION
            ),
            "fallback_delivery_report_id": report.report_id,
            "fallback_delivery_report_sha256": report.report_sha256,
        }

    return (
        render_projection,
        consistency_projection,
        requirement_projection,
        plan_projection,
        binding_projection,
        browser_projection,
        acceptance_counts,
        package_projection,
        package_live_binding,
        load_fallback_report,
        fallback_report_projection,
    )


(
    _FIXED_07A_RENDER_PROJECTION,
    _FIXED_07A_CONSISTENCY_PROJECTION,
    _FIXED_07A_REQUIREMENT_PROJECTION,
    _FIXED_07A_PLAN_PROJECTION,
    _FIXED_07A_BINDING_PROJECTION,
    _FIXED_07A_BROWSER_PROJECTION,
    _FIXED_07A_ACCEPTANCE_COUNTS,
    _FIXED_07A_PACKAGE_PROJECTION,
    _FIXED_07A_PACKAGE_LIVE_BINDING,
    _FIXED_07A_LOAD_FALLBACK_REPORT,
    _FIXED_07A_FALLBACK_REPORT_PROJECTION,
) = _build_tier_a_07a_artifact_authority(
    render_result_type=RenderResult,
    consistency_report_type=ConsistencyReport,
    requirement_view_type=RequirementView,
    acceptance_plan_type=AcceptancePlan,
    acceptance_binding_type=AcceptanceBindingPlan,
    browser_report_type=BrowserExecutionReport,
    result_package_type=ResultPackage,
    packager_type=DeterministicResultPackager,
    fallback_report_type=FallbackDeliveryReport,
    load_bytes=_FIXED_07A_READ_BYTES,
    staging_path=_FIXED_07A_STAGING_PATH,
    cleanup_directory=_FIXED_07A_CLEANUP_DIRECTORY,
    validate_raw_path=_FIXED_07A_VALIDATE_RAW_PATH,
)


def _07a_outcome_root(
    *,
    status: str,
    case_id: str,
    execution_branch: str,
    completed_steps: tuple[str, ...],
    model_route_binding: Mapping[str, object],
    g0_binding: Mapping[str, object],
    fallback_binding: Mapping[str, object],
    scripted_acceptance_fixture: object,
    g1_package_purpose: str,
    g2_action: str,
    gate_status: str,
    consistency_status: str,
    acceptance_status: str,
    acceptance_counts: Mapping[str, int],
    fallback_reason: str,
    fallback_attempted: bool,
    fallback_succeeded: bool,
    delivery_source: str,
    final_package_schema_version: str | None,
    final_package_id: str | None,
    final_package_manifest_sha256: str | None,
    final_package_tree_sha256: str | None,
    final_package_file_count: int | None,
    artifacts: Mapping[str, object],
    failure: TierA07aGateDeliveryFailure | None,
) -> dict[str, object]:
    return {
        "schema_version": TIER_A_07A_GATE_DELIVERY_OUTCOME_SCHEMA_VERSION,
        "status": status,
        "case_id": case_id,
        "execution_branch": execution_branch,
        "completed_steps": list(completed_steps),
        "model_route_binding": dict(model_route_binding),
        "g0_binding": dict(g0_binding),
        "fallback_binding": dict(fallback_binding),
        "scripted_acceptance_fixture": (
            None
            if scripted_acceptance_fixture is None
            else scripted_acceptance_fixture.to_dict()
        ),
        "g1_package_purpose": g1_package_purpose,
        "g2_action": g2_action,
        "gate_status": gate_status,
        "consistency_status": consistency_status,
        "acceptance_status": acceptance_status,
        "acceptance_counts": dict(acceptance_counts),
        "repair_limit": 1,
        "repair_attempted": 0,
        "repair_status": "not_attempted_tier_a_07a",
        "fallback_reason": fallback_reason,
        "fallback_attempted": fallback_attempted,
        "fallback_succeeded": fallback_succeeded,
        "delivery_source": delivery_source,
        "final_package_schema_version": final_package_schema_version,
        "final_package_id": final_package_id,
        "final_package_manifest_sha256": final_package_manifest_sha256,
        "final_package_tree_sha256": final_package_tree_sha256,
        "final_package_file_count": final_package_file_count,
        "artifacts": dict(artifacts),
        "failure": None if failure is None else failure.to_dict(),
        "execution_declarations": _07a_declarations(scripted_acceptance_fixture),
    }



@dataclass(frozen=True)
class TierA07aGateDeliveryOutcome:
    status: str
    case_id: str
    execution_branch: str
    completed_steps: tuple[str, ...]
    model_route_binding: Mapping[str, object]
    g0_binding: Mapping[str, object]
    fallback_binding: Mapping[str, object]
    scripted_acceptance_fixture: object
    g1_package_purpose: str
    g2_action: str
    gate_status: str
    consistency_status: str
    acceptance_status: str
    acceptance_counts: Mapping[str, int]
    fallback_reason: str
    fallback_attempted: bool
    fallback_succeeded: bool
    delivery_source: str
    final_package_schema_version: str | None
    final_package_id: str | None
    final_package_manifest_sha256: str | None
    final_package_tree_sha256: str | None
    final_package_file_count: int | None
    artifacts: Mapping[str, object]
    failure: TierA07aGateDeliveryFailure | None
    outcome_id: str
    schema_version: str = TIER_A_07A_GATE_DELIVERY_OUTCOME_SCHEMA_VERSION

    @classmethod
    def create(cls, **values: object) -> "TierA07aGateDeliveryOutcome":
        root = _07a_outcome_root(**values)
        result = cls(
            **values,
            outcome_id=(
                _07A_OUTCOME_ID_PREFIX
                + _sha256(_canonical_json_bytes(root))
            ),
        )
        result.validate()
        return result

    def _root(self) -> dict[str, object]:
        return _07a_outcome_root(
            status=self.status,
            case_id=self.case_id,
            execution_branch=self.execution_branch,
            completed_steps=self.completed_steps,
            model_route_binding=self.model_route_binding,
            g0_binding=self.g0_binding,
            fallback_binding=self.fallback_binding,
            scripted_acceptance_fixture=self.scripted_acceptance_fixture,
            g1_package_purpose=self.g1_package_purpose,
            g2_action=self.g2_action,
            gate_status=self.gate_status,
            consistency_status=self.consistency_status,
            acceptance_status=self.acceptance_status,
            acceptance_counts=self.acceptance_counts,
            fallback_reason=self.fallback_reason,
            fallback_attempted=self.fallback_attempted,
            fallback_succeeded=self.fallback_succeeded,
            delivery_source=self.delivery_source,
            final_package_schema_version=self.final_package_schema_version,
            final_package_id=self.final_package_id,
            final_package_manifest_sha256=self.final_package_manifest_sha256,
            final_package_tree_sha256=self.final_package_tree_sha256,
            final_package_file_count=self.final_package_file_count,
            artifacts=self.artifacts,
            failure=self.failure,
        )

    @staticmethod
    def _mapping_presence(
        value: Mapping[str, object],
        keys: tuple[str, ...],
    ) -> bool:
        if set(value) != set(keys):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        present = [item is not None for item in value.values()]
        if any(present) and not all(present):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        return all(present)

    def _validate_model_route_binding(self) -> bool:
        if set(self.model_route_binding) != set(_07A_MODEL_ROUTE_BINDING_KEYS):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        verified = self.model_route_binding["verified"]
        if type(verified) is not bool:
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        fields = (
            "outcome_id",
            "outcome_sha256",
            "disposition",
            "failure_code",
            "assembled_page_id",
            "assembled_page_spec_sha256",
        )
        if not verified:
            if any(self.model_route_binding[key] is not None for key in fields):
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
            return False
        if (
            not _07a_safe_id(self.model_route_binding["outcome_id"])
            or not _is_sha256(self.model_route_binding["outcome_sha256"])
            or self.model_route_binding["disposition"]
            not in {"scripted_fixture_assembled", "fail_closed"}
        ):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        failure_code = self.model_route_binding["failure_code"]
        if failure_code is not None and not _07a_safe_id(failure_code):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        disposition = self.model_route_binding["disposition"]
        if (disposition == "fail_closed") != (failure_code is not None):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        assembled_page_id = self.model_route_binding["assembled_page_id"]
        assembled_sha256 = self.model_route_binding[
            "assembled_page_spec_sha256"
        ]
        if disposition == "scripted_fixture_assembled":
            if (
                not _07a_safe_id(assembled_page_id)
                or not _is_sha256(assembled_sha256)
            ):
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        elif assembled_page_id is not None or assembled_sha256 is not None:
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        return True

    def _validate_steps(self) -> None:
        if not isinstance(self.completed_steps, tuple):
            raise TierA07aGateDeliveryOutcomeError("outcome_schema_invalid")
        if any(type(step) is not str for step in self.completed_steps):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if len(self.completed_steps) != len(set(self.completed_steps)):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        allowed = set((*_07A_CANDIDATE_SEQUENCE, _07A_STEP_FALLBACK_DELIVERY))
        if any(step not in allowed for step in self.completed_steps):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if not self.completed_steps:
            return
        without_fallback = tuple(
            step for step in self.completed_steps
            if step != _07A_STEP_FALLBACK_DELIVERY
        )
        valid_prefixes = {
            _07A_CANDIDATE_SEQUENCE[:index]
            for index in range(1, len(_07A_CANDIDATE_SEQUENCE) + 1)
        }
        if without_fallback not in valid_prefixes:
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if _07A_STEP_FALLBACK_DELIVERY in self.completed_steps:
            if (
                self.completed_steps[-1] != _07A_STEP_FALLBACK_DELIVERY
                or _07A_STEP_G1_PACKAGE in without_fallback
            ):
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")


    def _validate_artifacts(self) -> None:
        artifact_keys = tuple(self.artifacts)
        if any(type(key) is not str for key in artifact_keys):
            raise TierA07aGateDeliveryOutcomeError("artifact_validation_failed")
        if set(artifact_keys) != set(_07A_ARTIFACT_KEYS):
            raise TierA07aGateDeliveryOutcomeError("artifact_validation_failed")
        integer_keys = {"render_file_count", "model_package_file_count"}
        sha_keys = {
            key for key in _07A_ARTIFACT_KEYS if key.endswith("_sha256")
        }
        for key, value in self.artifacts.items():
            if value is None:
                continue
            if key in integer_keys:
                if type(value) is not int or value < 1:
                    raise TierA07aGateDeliveryOutcomeError(
                        "artifact_validation_failed"
                    )
            elif key in sha_keys:
                if not _is_sha256(value):
                    raise TierA07aGateDeliveryOutcomeError(
                        "artifact_validation_failed"
                    )
            elif not _07a_safe_id(value):
                raise TierA07aGateDeliveryOutcomeError(
                    "artifact_validation_failed"
                )
        groups = (
            (_07A_STEP_PAGE_SPEC, (
                "page_spec_schema_version", "page_spec_id", "page_spec_sha256",
            )),
            (_07A_STEP_RENDER, (
                "render_manifest_schema_version", "render_result_id",
                "render_manifest_sha256", "render_tree_sha256", "render_file_count",
            )),
            (_07A_STEP_CONSISTENCY, (
                "consistency_report_schema_version", "consistency_report_id",
                "consistency_report_sha256",
            )),
            (_07A_STEP_REQUIREMENT_VIEW, (
                "requirement_view_schema_version", "requirement_view_id",
                "requirement_view_sha256",
            )),
            (_07A_STEP_ACCEPTANCE_PLAN, (
                "acceptance_plan_schema_version", "acceptance_plan_id",
                "acceptance_plan_sha256",
            )),
            (_07A_STEP_ACCEPTANCE_BINDING, (
                "acceptance_binding_schema_version", "acceptance_binding_plan_id",
                "acceptance_binding_plan_sha256",
            )),
            (_07A_STEP_ACCEPTANCE_EXECUTION, (
                "browser_execution_schema_version", "browser_execution_report_id",
                "browser_execution_report_sha256",
            )),
            (_07A_STEP_G1_PACKAGE, (
                "model_package_schema_version", "model_package_id",
                "model_package_manifest_sha256", "model_package_tree_sha256",
                "model_package_file_count",
            )),
            (_07A_STEP_FALLBACK_DELIVERY, (
                "fallback_delivery_report_schema_version",
                "fallback_delivery_report_id", "fallback_delivery_report_sha256",
            )),
        )
        for step, keys in groups:
            expected = step in self.completed_steps
            if any((self.artifacts[key] is not None) != expected for key in keys):
                raise TierA07aGateDeliveryOutcomeError(
                    "artifact_validation_failed"
                )
        schemas = {
            "page_spec_schema_version": PAGE_SPEC_SCHEMA_VERSION,
            "render_manifest_schema_version": RENDER_MANIFEST_SCHEMA_VERSION,
            "consistency_report_schema_version": CONSISTENCY_REPORT_SCHEMA_VERSION,
            "requirement_view_schema_version": INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION,
            "acceptance_plan_schema_version": ACCEPTANCE_PLAN_SCHEMA_VERSION,
            "acceptance_binding_schema_version": ACCEPTANCE_BINDING_SCHEMA_VERSION,
            "browser_execution_schema_version": BROWSER_EXECUTION_SCHEMA_VERSION,
            "model_package_schema_version": RESULT_PACKAGE_SCHEMA_VERSION,
            "fallback_delivery_report_schema_version": (
                FALLBACK_DELIVERY_REPORT_SCHEMA_VERSION
            ),
        }
        for key, expected in schemas.items():
            if self.artifacts[key] is not None and self.artifacts[key] != expected:
                raise TierA07aGateDeliveryOutcomeError(
                    "artifact_validation_failed"
                )
        if (
            self.artifacts["render_file_count"] is not None
            and self.artifacts["render_file_count"] != 4
        ):
            raise TierA07aGateDeliveryOutcomeError("artifact_validation_failed")
        if (
            self.artifacts["model_package_file_count"] is not None
            and self.artifacts["model_package_file_count"] != 9
        ):
            raise TierA07aGateDeliveryOutcomeError("artifact_validation_failed")

    def _validate_counts(self) -> None:
        if set(self.acceptance_counts) != set(_07A_ACCEPTANCE_COUNT_KEYS):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if any(
            type(value) is not int
            or value < 0
            for value in self.acceptance_counts.values()
        ):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if self.acceptance_counts["total"] != sum(
            self.acceptance_counts[key]
            for key in ("pass", "fail", "unknown", "not_supported")
        ):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        executed = _07A_STEP_ACCEPTANCE_EXECUTION in self.completed_steps
        if executed != (self.acceptance_counts["total"] > 0):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")

    def _validate_final_package(self, expected_present: bool) -> None:
        fields = (
            self.final_package_schema_version,
            self.final_package_id,
            self.final_package_manifest_sha256,
            self.final_package_tree_sha256,
            self.final_package_file_count,
        )
        if expected_present:
            if (
                not _07a_safe_id(self.final_package_schema_version)
                or not _07a_safe_id(self.final_package_id)
                or not _is_sha256(self.final_package_manifest_sha256)
                or not _is_sha256(self.final_package_tree_sha256)
                or type(self.final_package_file_count) is not int
                or self.final_package_file_count < 1
            ):
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        elif any(value is not None for value in fields):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")


    def _validate_identity_mapping(
        self,
        value: Mapping[str, object],
        *,
        present: bool,
    ) -> None:
        if not present:
            return
        for key, item in value.items():
            if type(key) is not str:
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
            if key.endswith("_sha256"):
                if not _is_sha256(item):
                    raise TierA07aGateDeliveryOutcomeError(
                        "outcome_state_invalid"
                    )
            elif key.endswith("_count"):
                if type(item) is not int or item < 1:
                    raise TierA07aGateDeliveryOutcomeError(
                        "outcome_state_invalid"
                    )
            elif not _07a_safe_id(item):
                raise TierA07aGateDeliveryOutcomeError(
                    "outcome_state_invalid"
                )

    def validate(self) -> None:
        text_fields = (
            self.status,
            self.case_id,
            self.execution_branch,
            self.g1_package_purpose,
            self.g2_action,
            self.gate_status,
            self.consistency_status,
            self.acceptance_status,
            self.fallback_reason,
            self.delivery_source,
        )
        if any(type(value) is not str for value in text_fields):
            raise TierA07aGateDeliveryOutcomeError("outcome_schema_invalid")
        if (
            self.schema_version
            != TIER_A_07A_GATE_DELIVERY_OUTCOME_SCHEMA_VERSION
            or not _07a_safe_id(self.case_id)
        ):
            raise TierA07aGateDeliveryOutcomeError("outcome_schema_invalid")
        self._validate_steps()
        model_verified = self._validate_model_route_binding()
        g0_present = self._mapping_presence(
            self.g0_binding,
            _07A_G0_BINDING_KEYS,
        )
        fallback_present = self._mapping_presence(
            self.fallback_binding,
            _07A_FALLBACK_BINDING_KEYS,
        )
        self._validate_identity_mapping(self.g0_binding, present=g0_present)
        self._validate_identity_mapping(
            self.fallback_binding,
            present=fallback_present,
        )
        if fallback_present and self.fallback_binding["case_id"] != self.case_id:
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        self._validate_artifacts()
        if g0_present and fallback_present:
            for key in (
                "package_id",
                "page_id",
                "package_schema_version",
                "package_manifest_sha256",
                "inventory_file_count",
            ):
                if self.g0_binding[key] != self.fallback_binding[key]:
                    raise TierA07aGateDeliveryOutcomeError(
                        "outcome_state_invalid"
                    )
        if (
            model_verified
            and self.model_route_binding["assembled_page_id"] is not None
            and g0_present
            and self.model_route_binding["assembled_page_id"]
            != self.g0_binding["page_id"]
        ):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if _07A_STEP_PAGE_SPEC in self.completed_steps:
            if (
                self.model_route_binding["assembled_page_id"]
                != self.artifacts["page_spec_id"]
                or self.model_route_binding["assembled_page_spec_sha256"]
                != self.artifacts["page_spec_sha256"]
            ):
                raise TierA07aGateDeliveryOutcomeError(
                    "artifact_validation_failed"
                )
        self._validate_counts()
        if self.failure is not None:
            self.failure.validate()
        fixture_present = self.scripted_acceptance_fixture is not None
        if fixture_present:
            _FIXED_ACCEPTANCE_FIXTURE_AUTHORITY(
                self.scripted_acceptance_fixture
            )
        if fixture_present != (
            _07A_STEP_ACCEPTANCE_FIXTURE in self.completed_steps
        ):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if (
            self.consistency_status not in {"not_executed", "pass", "fail"}
            or self.acceptance_status
            not in {"not_executed", "pass", "blocked"}
            or self.gate_status not in {"not_executed", "blocked", "passed"}
        ):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        consistency_done = _07A_STEP_CONSISTENCY in self.completed_steps
        acceptance_done = _07A_STEP_ACCEPTANCE_EXECUTION in self.completed_steps
        if consistency_done != (self.consistency_status != "not_executed"):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if acceptance_done != (self.acceptance_status != "not_executed"):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if acceptance_done:
            blocked = (
                self.acceptance_counts["fail"] > 0
                or self.acceptance_counts["unknown"] > 0
            )
            if self.acceptance_status != ("blocked" if blocked else "pass"):
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        expected_gate = (
            "passed"
            if self.consistency_status == "pass"
            and self.acceptance_status == "pass"
            else (
                "blocked"
                if consistency_done or acceptance_done
                else "not_executed"
            )
        )
        if self.gate_status != expected_gate:
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if type(self.fallback_attempted) is not bool or type(
            self.fallback_succeeded
        ) is not bool:
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")

        if self.status == "first_pass_success":
            if (
                not model_verified
                or not g0_present
                or not fallback_present
                or self.execution_branch != "model_first_pass"
                or self.completed_steps != _07A_CANDIDATE_SEQUENCE
                or self.failure is not None
                or self.fallback_reason != "none"
                or self.g1_package_purpose != "evaluation_only"
                or self.g2_action != "no_change"
                or self.gate_status != "passed"
                or self.fallback_attempted is not False
                or self.fallback_succeeded is not False
                or self.delivery_source != "model_first_pass_v1"
            ):
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
            self._validate_final_package(True)
            if (
                self.final_package_schema_version != RESULT_PACKAGE_SCHEMA_VERSION
                or self.final_package_id != self.artifacts["model_package_id"]
                or self.final_package_manifest_sha256
                != self.artifacts["model_package_manifest_sha256"]
                or self.final_package_tree_sha256
                != self.artifacts["model_package_tree_sha256"]
                or self.final_package_file_count
                != self.artifacts["model_package_file_count"]
            ):
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        elif self.status == "fallback_delivery":
            if (
                not model_verified
                or not g0_present
                or not fallback_present
                or self.execution_branch != "g0_frozen_fallback"
                or self.failure is not None
                or self.fallback_reason not in _07A_FALLBACK_REASONS
                or self.g1_package_purpose != "not_generated"
                or self.g2_action != "frozen_g0_fallback"
                or self.fallback_attempted is not True
                or self.fallback_succeeded is not True
                or self.delivery_source != "g0_frozen_fallback"
                or not self.completed_steps
                or self.completed_steps[-1] != _07A_STEP_FALLBACK_DELIVERY
                or self.completed_steps
                != (
                    *_07A_FALLBACK_REASON_PREFIXES[self.fallback_reason],
                    _07A_STEP_FALLBACK_DELIVERY,
                )
            ):
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
            self._validate_final_package(True)
            if (
                self.final_package_schema_version
                != self.fallback_binding["package_schema_version"]
                or self.final_package_id != self.fallback_binding["package_id"]
                or self.final_package_manifest_sha256
                != self.fallback_binding["package_manifest_sha256"]
                or self.final_package_tree_sha256
                != self.fallback_binding["package_tree_sha256"]
                or self.final_package_file_count
                != self.fallback_binding["inventory_file_count"]
            ):
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")


        elif self.status == "failed_delivery":
            if (
                self.execution_branch != "failed_delivery"
                or self.failure is None
                or self.g1_package_purpose != "not_generated"
                or self.g2_action != "failed_delivery"
                or self.fallback_succeeded is not False
                or self.delivery_source != "unavailable"
            ):
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
            self._validate_final_package(False)
            code = self.failure.code
            if code == "model_route_validation_failed":
                if (
                    model_verified
                    or g0_present
                    or fallback_present
                    or self.completed_steps
                    or self.fallback_attempted is not False
                    or self.fallback_reason != "not_attempted"
                ):
                    raise TierA07aGateDeliveryOutcomeError(
                        "outcome_state_invalid"
                    )
            elif code == "frozen_g0_fallback_invalid":
                if (
                    not model_verified
                    or fallback_present
                    or self.completed_steps != (_07A_STEP_MODEL_ROUTE,)
                    or self.fallback_attempted is not False
                    or self.fallback_reason != "not_attempted"
                ):
                    raise TierA07aGateDeliveryOutcomeError(
                        "outcome_state_invalid"
                    )
            elif code == "output_destination_invalid":
                if (
                    not model_verified
                    or not fallback_present
                    or self.completed_steps
                    != (
                        _07A_STEP_MODEL_ROUTE,
                        _07A_STEP_FALLBACK_VALIDATION,
                    )
                    or self.fallback_attempted is not False
                    or self.fallback_reason != "not_attempted"
                ):
                    raise TierA07aGateDeliveryOutcomeError(
                        "outcome_state_invalid"
                    )
            elif code == "fallback_delivery_failed":
                if (
                    not model_verified
                    or not fallback_present
                    or self.fallback_attempted is not True
                    or self.fallback_reason not in _07A_FALLBACK_REASONS
                    or _07A_STEP_FALLBACK_DELIVERY in self.completed_steps
                    or self.completed_steps
                    != _07A_FALLBACK_REASON_PREFIXES[self.fallback_reason]
                ):
                    raise TierA07aGateDeliveryOutcomeError(
                        "outcome_state_invalid"
                    )
            else:
                raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        else:
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")

        expected_id = _07A_OUTCOME_ID_PREFIX + _sha256(
            _canonical_json_bytes(self._root())
        )
        if self.outcome_id != expected_id:
            raise TierA07aGateDeliveryOutcomeError("outcome_identity_invalid")

    @classmethod
    def from_dict(cls, payload: object) -> "TierA07aGateDeliveryOutcome":
        expected = {
            "schema_version", "status", "case_id", "execution_branch",
            "completed_steps", "model_route_binding", "g0_binding",
            "fallback_binding", "scripted_acceptance_fixture",
            "g1_package_purpose", "g2_action", "gate_status",
            "consistency_status", "acceptance_status", "acceptance_counts",
            "repair_limit", "repair_attempted", "repair_status",
            "fallback_reason", "fallback_attempted", "fallback_succeeded",
            "delivery_source", "final_package_schema_version",
            "final_package_id", "final_package_manifest_sha256",
            "final_package_tree_sha256", "final_package_file_count",
            "artifacts", "failure", "execution_declarations", "outcome_id",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise TierA07aGateDeliveryOutcomeError(
                "outcome_exact_keys_invalid"
            )
        mapping_keys = (
            "model_route_binding", "g0_binding", "fallback_binding",
            "acceptance_counts", "artifacts", "execution_declarations",
        )
        if (
            any(not isinstance(payload[key], Mapping) for key in mapping_keys)
            or type(payload["completed_steps"]) is not list
            or not isinstance(payload["artifacts"], Mapping)
        ):
            raise TierA07aGateDeliveryOutcomeError("outcome_state_invalid")
        if type(payload["repair_limit"]) is not int or type(
            payload["repair_attempted"]
        ) is not int:
            raise TierA07aGateDeliveryOutcomeError("declarations_invalid")
        expected_declarations = _07a_declarations(
            None
            if payload["scripted_acceptance_fixture"] is None
            else ScriptedAcceptanceFixture.from_dict(
                payload["scripted_acceptance_fixture"]
            )
        )
        declarations = payload["execution_declarations"]
        if set(declarations) != set(expected_declarations):
            raise TierA07aGateDeliveryOutcomeError("declarations_invalid")
        for key, expected_value in expected_declarations.items():
            actual_value = declarations[key]
            if type(actual_value) is not type(expected_value):
                raise TierA07aGateDeliveryOutcomeError("declarations_invalid")
            if actual_value != expected_value:
                raise TierA07aGateDeliveryOutcomeError("declarations_invalid")
        try:
            fixture = (
                None
                if payload["scripted_acceptance_fixture"] is None
                else ScriptedAcceptanceFixture.from_dict(
                    payload["scripted_acceptance_fixture"]
                )
            )
            failure = (
                None
                if payload["failure"] is None
                else TierA07aGateDeliveryFailure.from_dict(payload["failure"])
            )
            result = cls(
                schema_version=payload["schema_version"],
                status=payload["status"],
                case_id=payload["case_id"],
                execution_branch=payload["execution_branch"],
                completed_steps=tuple(payload["completed_steps"]),
                model_route_binding=dict(payload["model_route_binding"]),
                g0_binding=dict(payload["g0_binding"]),
                fallback_binding=dict(payload["fallback_binding"]),
                scripted_acceptance_fixture=fixture,
                g1_package_purpose=payload["g1_package_purpose"],
                g2_action=payload["g2_action"],
                gate_status=payload["gate_status"],
                consistency_status=payload["consistency_status"],
                acceptance_status=payload["acceptance_status"],
                acceptance_counts=dict(payload["acceptance_counts"]),
                fallback_reason=payload["fallback_reason"],
                fallback_attempted=payload["fallback_attempted"],
                fallback_succeeded=payload["fallback_succeeded"],
                delivery_source=payload["delivery_source"],
                final_package_schema_version=payload[
                    "final_package_schema_version"
                ],
                final_package_id=payload["final_package_id"],
                final_package_manifest_sha256=payload[
                    "final_package_manifest_sha256"
                ],
                final_package_tree_sha256=payload[
                    "final_package_tree_sha256"
                ],
                final_package_file_count=payload["final_package_file_count"],
                artifacts=dict(payload["artifacts"]),
                failure=failure,
                outcome_id=payload["outcome_id"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, TierA07aGateDeliveryOutcomeError):
                raise
            raise TierA07aGateDeliveryOutcomeError(
                "outcome_state_invalid"
            ) from exc
        if (
            payload["repair_limit"] != 1
            or payload["repair_attempted"] != 0
            or payload["repair_status"] != "not_attempted_tier_a_07a"
            or payload["execution_declarations"] != _07a_declarations(fixture)
        ):
            raise TierA07aGateDeliveryOutcomeError("declarations_invalid")
        result.validate()
        return result

    @classmethod
    def from_bytes(cls, raw: bytes) -> "TierA07aGateDeliveryOutcome":
        try:
            payload = _load_canonical_json(raw)
        except Exception as exc:
            raise TierA07aGateDeliveryOutcomeError(
                "serialized_bytes_invalid"
            ) from exc
        result = cls.from_dict(payload)
        if result.canonical_bytes() != raw:
            raise TierA07aGateDeliveryOutcomeError("serialized_bytes_invalid")
        return result

    def to_dict(self) -> dict[str, object]:
        self.validate()
        root = self._root()
        root["outcome_id"] = self.outcome_id
        return root

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    @property
    def repair_limit(self) -> int:
        return 1

    @property
    def repair_attempted(self) -> int:
        return 0

    @property
    def repair_status(self) -> str:
        return "not_attempted_tier_a_07a"


def validate_serialized_tier_a_07a_gate_delivery_outcome(
    payload: object,
) -> TierA07aGateDeliveryOutcome:
    if isinstance(payload, bytes):
        return TierA07aGateDeliveryOutcome.from_bytes(payload)
    return TierA07aGateDeliveryOutcome.from_dict(payload)



def _build_tier_a_07a_run(
    *,
    outcome_type,
    failure_type,
    model_route_type,
    model_route_validate,
    model_route_structural_validate,
    model_route_canonical_bytes,
    model_fixture_authority,
    assembly_authority,
    renderer_type,
    render_result_type,
    consistency_checker_type,
    requirement_projector,
    acceptance_plan_compiler,
    acceptance_binding_compiler,
    acceptance_fixture_authority,
    acceptance_executor,
    packager_type,
    result_package_type,
    fallback_deliverer,
    fallback_binding_authority,
    path_preflight,
    staging_path,
    cleanup_directory,
    commit_staging,
    rollback_commit,
    page_spec_projection,
    g0_projection,
    render_projection,
    consistency_projection,
    requirement_projection,
    plan_projection,
    binding_projection,
    browser_projection,
    acceptance_counts_projection,
    package_projection,
    load_fallback_report,
    fallback_report_projection,
):
    empty_model_binding = {
        "verified": False,
        "outcome_id": None,
        "outcome_sha256": None,
        "disposition": None,
        "failure_code": None,
        "assembled_page_id": None,
        "assembled_page_spec_sha256": None,
    }

    def make_outcome(
        *,
        status: str,
        case_id: str,
        execution_branch: str,
        completed_steps: tuple[str, ...],
        model_route_binding: Mapping[str, object],
        g0_binding: Mapping[str, object],
        fallback_binding: Mapping[str, object],
        fixture: object = None,
        g1_package_purpose: str = "not_generated",
        g2_action: str,
        gate_status: str,
        consistency_status: str,
        acceptance_status: str,
        acceptance_counts: Mapping[str, int],
        fallback_reason: str,
        fallback_attempted: bool,
        fallback_succeeded: bool,
        delivery_source: str,
        artifacts: Mapping[str, object],
        failure: object = None,
        final_package_schema_version: str | None = None,
        final_package_id: str | None = None,
        final_package_manifest_sha256: str | None = None,
        final_package_tree_sha256: str | None = None,
        final_package_file_count: int | None = None,
    ) -> TierA07aGateDeliveryOutcome:
        return outcome_type.create(
            status=status,
            case_id=case_id,
            execution_branch=execution_branch,
            completed_steps=completed_steps,
            model_route_binding=dict(model_route_binding),
            g0_binding=dict(g0_binding),
            fallback_binding=dict(fallback_binding),
            scripted_acceptance_fixture=fixture,
            g1_package_purpose=g1_package_purpose,
            g2_action=g2_action,
            gate_status=gate_status,
            consistency_status=consistency_status,
            acceptance_status=acceptance_status,
            acceptance_counts=dict(acceptance_counts),
            fallback_reason=fallback_reason,
            fallback_attempted=fallback_attempted,
            fallback_succeeded=fallback_succeeded,
            delivery_source=delivery_source,
            final_package_schema_version=final_package_schema_version,
            final_package_id=final_package_id,
            final_package_manifest_sha256=final_package_manifest_sha256,
            final_package_tree_sha256=final_package_tree_sha256,
            final_package_file_count=final_package_file_count,
            artifacts=dict(artifacts),
            failure=failure,
        )

    def failed(
        code: str,
        *,
        case_id: str,
        completed_steps: tuple[str, ...],
        model_route_binding: Mapping[str, object],
        g0_binding: Mapping[str, object],
        fallback_binding: Mapping[str, object],
        artifacts: Mapping[str, object],
        fixture: object = None,
        gate_status: str = "not_executed",
        consistency_status: str = "not_executed",
        acceptance_status: str = "not_executed",
        acceptance_counts: Mapping[str, int] | None = None,
        fallback_reason: str = "not_attempted",
        fallback_attempted: bool = False,
    ) -> TierA07aGateDeliveryOutcome:
        return make_outcome(
            status="failed_delivery",
            case_id=case_id,
            execution_branch="failed_delivery",
            completed_steps=completed_steps,
            model_route_binding=model_route_binding,
            g0_binding=g0_binding,
            fallback_binding=fallback_binding,
            fixture=fixture,
            g2_action="failed_delivery",
            gate_status=gate_status,
            consistency_status=consistency_status,
            acceptance_status=acceptance_status,
            acceptance_counts=(
                _07a_empty_counts()
                if acceptance_counts is None
                else acceptance_counts
            ),
            fallback_reason=fallback_reason,
            fallback_attempted=fallback_attempted,
            fallback_succeeded=False,
            delivery_source="unavailable",
            artifacts=artifacts,
            failure=failure_type.fixed(code),
        )

    def run(
        self,
        *,
        model_route_outcome: object,
        frozen_g0_reference: object,
        package: object,
        context: object,
        guidance: object,
        manifest: object,
        selected: object,
        local_request: object,
        pre_invocation_audit: object,
        local_qwen_preparation: object,
        execution_branch: object,
        scripted_local_fixture: object = None,
        case_id: str,
        fallback_record: object,
        fallback_snapshot_dir: object,
        render_output_dir: object,
        model_package_output_dir: object,
        fallback_output_dir: object,
        scripted_acceptance_fixture: object = None,
    ) -> TierA07aGateDeliveryOutcome:
        if not _07a_safe_id(case_id):
            raise TierA07aGateDeliveryOutcomeError("outcome_schema_invalid")
        artifacts = _07a_empty_artifacts()
        counts = _07a_empty_counts()
        g0_binding = _07a_empty_mapping(_07A_G0_BINDING_KEYS)
        fallback_binding = _07a_empty_mapping(_07A_FALLBACK_BINDING_KEYS)
        try:
            if type(model_route_outcome) is not model_route_type:
                raise TypeError("model_route_outcome has the wrong type")
            model_route_validate(
                model_route_outcome,
                frozen_g0_reference=frozen_g0_reference,
                package=package,
                context=context,
                guidance=guidance,
                manifest=manifest,
                selected=selected,
                local_request=local_request,
                pre_invocation_audit=pre_invocation_audit,
                local_qwen_preparation=local_qwen_preparation,
                execution_branch=execution_branch,
                scripted_local_fixture=scripted_local_fixture,
            )
            model_route_structural_validate(model_route_outcome)
            model_binding = {
                "verified": True,
                "outcome_id": model_route_outcome.outcome_id,
                "outcome_sha256": _sha256(
                    model_route_canonical_bytes(model_route_outcome)
                ),
                "disposition": model_route_outcome.disposition,
                "failure_code": (
                    None
                    if model_route_outcome.failure is None
                    else model_route_outcome.failure.code
                ),
                "assembled_page_id": model_route_outcome.artifacts[
                    "assembled_page_id"
                ],
                "assembled_page_spec_sha256": model_route_outcome.artifacts[
                    "assembled_page_spec_sha256"
                ],
            }
            g0_binding = g0_projection(
                model_route_outcome.frozen_g0_reference
            )
        except Exception:
            return failed(
                "model_route_validation_failed",
                case_id=case_id,
                completed_steps=(),
                model_route_binding=empty_model_binding,
                g0_binding=g0_binding,
                fallback_binding=fallback_binding,
                artifacts=artifacts,
            )

        try:
            fallback_binding = fallback_binding_authority(
                case_id=case_id,
                record=fallback_record,
                snapshot_dir=fallback_snapshot_dir,
                reference=frozen_g0_reference,
                package=package,
                context=context,
                guidance=guidance,
            )
        except Exception:
            return failed(
                "frozen_g0_fallback_invalid",
                case_id=case_id,
                completed_steps=(_07A_STEP_MODEL_ROUTE,),
                model_route_binding=model_binding,
                g0_binding=g0_binding,
                fallback_binding=_07a_empty_mapping(
                    _07A_FALLBACK_BINDING_KEYS
                ),
                artifacts=artifacts,
            )

        try:
            destinations = path_preflight(
                render_output_dir,
                model_package_output_dir,
                fallback_output_dir,
                package_dir=package.package_dir,
                snapshot_dir=fallback_snapshot_dir,
            )
        except Exception:
            return failed(
                "output_destination_invalid",
                case_id=case_id,
                completed_steps=(
                    _07A_STEP_MODEL_ROUTE,
                    _07A_STEP_FALLBACK_VALIDATION,
                ),
                model_route_binding=model_binding,
                g0_binding=g0_binding,
                fallback_binding=fallback_binding,
                artifacts=artifacts,
            )


        def deliver_current_fallback(
            *,
            reason: str,
            completed_before_delivery: tuple[str, ...],
            current_artifacts: Mapping[str, object],
            fixture: object = None,
            gate_status: str = "not_executed",
            consistency_status: str = "not_executed",
            acceptance_status: str = "not_executed",
            acceptance_counts: Mapping[str, int] | None = None,
        ) -> TierA07aGateDeliveryOutcome:
            local_artifacts = dict(current_artifacts)
            local_counts = (
                _07a_empty_counts()
                if acceptance_counts is None
                else dict(acceptance_counts)
            )
            stage = staging_path(
                destinations.fallback_output_dir,
                "tier-a-07a-fallback-staging",
            )
            commit_receipt = None
            try:
                report = fallback_deliverer(
                    fallback_snapshot_dir,
                    fallback_record,
                    stage,
                )
                report.validate_against(
                    fallback_snapshot_dir,
                    stage / "result_package",
                    fallback_record,
                )
                if (
                    report.case_id != case_id
                    or report.package_id != fallback_binding["package_id"]
                    or report.page_id != fallback_binding["page_id"]
                    or report.package_schema_version
                    != fallback_binding["package_schema_version"]
                    or report.frozen_record_id
                    != fallback_binding["record_id"]
                    or report.frozen_record_sha256
                    != fallback_binding["record_sha256"]
                    or report.delivered_package_tree_sha256
                    != fallback_binding["package_tree_sha256"]
                    or len(report.delivered_inventory)
                    != fallback_binding["inventory_file_count"]
                ):
                    raise ValueError("fallback delivery binding is invalid")
                commit_receipt = commit_staging(
                    stage,
                    destinations.fallback_output_dir,
                    destination_existed=destinations.fallback_existed,
                )
                stage = None
                delivered_report = load_fallback_report(
                    destinations.fallback_output_dir,
                    fallback_snapshot_dir,
                    fallback_record,
                )
                if delivered_report.to_dict() != report.to_dict():
                    raise ValueError("fallback delivery changed during commit")
                local_artifacts.update(fallback_report_projection(delivered_report))
            except Exception:
                if stage is not None:
                    cleanup_directory(stage)
                rollback_commit(commit_receipt, destinations.fallback_output_dir)
                return failed(
                    "fallback_delivery_failed",
                    case_id=case_id,
                    completed_steps=completed_before_delivery,
                    model_route_binding=model_binding,
                    g0_binding=g0_binding,
                    fallback_binding=fallback_binding,
                    artifacts=local_artifacts,
                    fixture=fixture,
                    gate_status=gate_status,
                    consistency_status=consistency_status,
                    acceptance_status=acceptance_status,
                    acceptance_counts=local_counts,
                fallback_reason=reason,
                fallback_attempted=True,
            )
            return make_outcome(
                status="fallback_delivery",
                case_id=case_id,
                execution_branch="g0_frozen_fallback",
                completed_steps=(
                    *completed_before_delivery,
                    _07A_STEP_FALLBACK_DELIVERY,
                ),
                model_route_binding=model_binding,
                g0_binding=g0_binding,
                fallback_binding=fallback_binding,
                fixture=fixture,
                g2_action="frozen_g0_fallback",
                gate_status=gate_status,
                consistency_status=consistency_status,
                acceptance_status=acceptance_status,
                acceptance_counts=local_counts,
                fallback_reason=reason,
                fallback_attempted=True,
                fallback_succeeded=True,
                delivery_source="g0_frozen_fallback",
                 final_package_schema_version=delivered_report.package_schema_version,
                 final_package_id=delivered_report.package_id,
                final_package_manifest_sha256=fallback_binding[
                    "package_manifest_sha256"
                ],
                final_package_tree_sha256=(
                     delivered_report.delivered_package_tree_sha256
                ),
                final_package_file_count=len(delivered_report.delivered_inventory),
                artifacts=local_artifacts,
            )

        base_steps = _07A_DIRECT_FALLBACK_PREFIX
        assembled = None
        if model_route_outcome.disposition == "scripted_fixture_assembled":
            try:
                live_fixture = model_fixture_authority(scripted_local_fixture)
                assembled = assembly_authority(
                    live_fixture.raw_response,
                    context,
                    guidance,
                )
                report = assembled.report
                expected = {
                    "raw_response_sha256": live_fixture.raw_response.sha256,
                    "raw_response_byte_length": len(
                        live_fixture.raw_response.raw_bytes
                    ),
                    "model_semantic_candidate_sha256": (
                        assembled.candidate.sha256()
                    ),
                    "assembled_page_id": assembled.page_spec.page_id,
                    "assembly_report_id": report.report_id,
                    "assembly_report_sha256": report.sha256(),
                    "assembled_page_spec_sha256": (
                        report.assembled_page_spec_sha256
                    ),
                }
                if (
                    dict(model_route_outcome.artifacts) != expected
                    or assembled.page_spec.page_id
                    != frozen_g0_reference.page_id
                ):
                    raise ValueError("assembled PageSpec binding is invalid")
            except Exception:
                assembled = None
        if assembled is None:
            return deliver_current_fallback(
                reason="model_route_not_assembled",
                completed_before_delivery=base_steps,
                current_artifacts=artifacts,
            )

        page_spec = assembled.page_spec
        artifacts.update(page_spec_projection(page_spec))
        steps = (*base_steps, _07A_STEP_PAGE_SPEC)
        stage = staging_path(
            destinations.render_output_dir,
            "tier-a-07a-render-staging",
        )
        commit_receipt = None
        try:
            staged_render = renderer_type().render(page_spec, stage)
            render_projection(page_spec, staged_render)
            commit_receipt = commit_staging(
                stage,
                destinations.render_output_dir,
                destination_existed=destinations.render_existed,
            )
            stage = None
            render_result = render_result_type(
                page_id=page_spec.page_id,
                output_dir=destinations.render_output_dir,
                index_html=destinations.render_output_dir / "index.html",
                styles_css=destinations.render_output_dir / "styles.css",
                app_js=destinations.render_output_dir / "app.js",
                render_manifest=(
                    destinations.render_output_dir / "render_manifest.json"
                ),
            )
            artifacts.update(render_projection(page_spec, render_result))
            steps = (*steps, _07A_STEP_RENDER)
        except Exception:
            if stage is not None:
                cleanup_directory(stage)
            rollback_commit(commit_receipt, destinations.render_output_dir)
            return deliver_current_fallback(
                reason="render_failed",
                completed_before_delivery=steps,
                current_artifacts=artifacts,
            )


        try:
            consistency = consistency_checker_type().check(
                page_spec,
                render_result,
            )
            consistency.validate()
            artifacts.update(consistency_projection(consistency))
            consistency_status = "pass" if consistency.passed else "fail"
            steps = (*steps, _07A_STEP_CONSISTENCY)
        except Exception:
            return deliver_current_fallback(
                reason="consistency_failed",
                completed_before_delivery=steps,
                current_artifacts=artifacts,
            )

        try:
            requirement_view = requirement_projector(context)
            requirement_view.validate()
            artifacts.update(requirement_projection(requirement_view))
            steps = (*steps, _07A_STEP_REQUIREMENT_VIEW)
        except Exception:
            return deliver_current_fallback(
                reason="requirement_view_failed",
                completed_before_delivery=steps,
                current_artifacts=artifacts,
                gate_status="blocked",
                consistency_status=consistency_status,
            )

        try:
            acceptance_plan = acceptance_plan_compiler(requirement_view)
            acceptance_plan.validate_against(requirement_view)
            artifacts.update(plan_projection(acceptance_plan))
            steps = (*steps, _07A_STEP_ACCEPTANCE_PLAN)
        except Exception:
            return deliver_current_fallback(
                reason="acceptance_plan_failed",
                completed_before_delivery=steps,
                current_artifacts=artifacts,
                gate_status="blocked",
                consistency_status=consistency_status,
            )

        try:
            binding_plan = acceptance_binding_compiler(
                requirement_view,
                acceptance_plan,
                page_spec,
                render_result,
            )
            binding_plan.validate_against(
                requirement_view,
                acceptance_plan,
                page_spec,
                render_result,
            )
            artifacts.update(binding_projection(binding_plan))
            steps = (*steps, _07A_STEP_ACCEPTANCE_BINDING)
        except Exception:
            return deliver_current_fallback(
                reason="acceptance_binding_failed",
                completed_before_delivery=steps,
                current_artifacts=artifacts,
                gate_status="blocked",
                consistency_status=consistency_status,
            )

        try:
            live_acceptance_fixture = acceptance_fixture_authority(
                scripted_acceptance_fixture
            )
        except Exception:
            return deliver_current_fallback(
                reason="acceptance_fixture_invalid",
                completed_before_delivery=steps,
                current_artifacts=artifacts,
                gate_status="blocked",
                consistency_status=consistency_status,
            )
        steps = (*steps, _07A_STEP_ACCEPTANCE_FIXTURE)

        try:
            browser_report = acceptance_executor(
                live_acceptance_fixture,
                binding_plan,
            )
            browser_report.validate_against(binding_plan)
            artifacts.update(browser_projection(browser_report))
            counts = acceptance_counts_projection(browser_report)
            acceptance_status = (
                "blocked"
                if counts["fail"] > 0 or counts["unknown"] > 0
                else "pass"
            )
            steps = (*steps, _07A_STEP_ACCEPTANCE_EXECUTION)
        except Exception:
            return deliver_current_fallback(
                reason="acceptance_execution_failed",
                completed_before_delivery=steps,
                current_artifacts=artifacts,
                fixture=live_acceptance_fixture,
                gate_status="blocked",
                consistency_status=consistency_status,
            )

        gate_status = (
            "passed"
            if consistency_status == "pass" and acceptance_status == "pass"
            else "blocked"
        )
        if gate_status != "passed":
            return deliver_current_fallback(
                reason="consistency_or_acceptance_blocked",
                completed_before_delivery=steps,
                current_artifacts=artifacts,
                fixture=live_acceptance_fixture,
                gate_status=gate_status,
                consistency_status=consistency_status,
                acceptance_status=acceptance_status,
                acceptance_counts=counts,
            )

        package_stage = staging_path(
            destinations.model_package_output_dir,
            "tier-a-07a-model-package-staging",
        )
        package_commit_receipt = None
        try:
            staged_package = packager_type().package(
                context,
                page_spec,
                render_result,
                consistency,
                package_stage,
            )
            staged_package_artifacts = package_projection(
                staged_package,
                package_stage,
            )
            package_commit_receipt = commit_staging(
                package_stage,
                destinations.model_package_output_dir,
                destination_existed=destinations.model_package_existed,
            )
            package_stage = None
            model_package = result_package_type(
                package_id=staged_package.package_id,
                page_id=staged_package.page_id,
                package_dir=destinations.model_package_output_dir,
                entrypoint=staged_package.entrypoint,
                result_summary=staged_package.result_summary,
                package_manifest=staged_package.package_manifest,
            )
            package_artifacts = package_projection(
                model_package,
                destinations.model_package_output_dir,
            )
            if package_artifacts != staged_package_artifacts:
                raise ValueError("model package changed during commit")
            artifacts.update(package_artifacts)
            steps = (*steps, _07A_STEP_G1_PACKAGE)
        except Exception:
            if package_stage is not None:
                cleanup_directory(package_stage)
            rollback_commit(
                package_commit_receipt,
                destinations.model_package_output_dir,
            )
            return deliver_current_fallback(
                reason="model_package_failed",
                completed_before_delivery=steps,
                current_artifacts=artifacts,
                fixture=live_acceptance_fixture,
                gate_status="passed",
                consistency_status=consistency_status,
                acceptance_status=acceptance_status,
                acceptance_counts=counts,
            )

        steps = (*steps, _07A_STEP_G2_NO_CHANGE)
        return make_outcome(
            status="first_pass_success",
            case_id=case_id,
            execution_branch="model_first_pass",
            completed_steps=steps,
            model_route_binding=model_binding,
            g0_binding=g0_binding,
            fallback_binding=fallback_binding,
            fixture=live_acceptance_fixture,
            g1_package_purpose="evaluation_only",
            g2_action="no_change",
            gate_status="passed",
            consistency_status=consistency_status,
            acceptance_status=acceptance_status,
            acceptance_counts=counts,
            fallback_reason="none",
            fallback_attempted=False,
            fallback_succeeded=False,
            delivery_source="model_first_pass_v1",
            final_package_schema_version=package_artifacts[
                "model_package_schema_version"
            ],
            final_package_id=package_artifacts["model_package_id"],
            final_package_manifest_sha256=package_artifacts[
                "model_package_manifest_sha256"
            ],
            final_package_tree_sha256=package_artifacts[
                "model_package_tree_sha256"
            ],
            final_package_file_count=package_artifacts[
                "model_package_file_count"
            ],
            artifacts=artifacts,
        )

    return run


class TierA07aGateDeliveryOrchestrator:
    """Tier A-07a first-pass/no-change/frozen-fallback gate route."""

    __slots__ = ()

    run = _build_tier_a_07a_run(
        outcome_type=TierA07aGateDeliveryOutcome,
        failure_type=TierA07aGateDeliveryFailure,
        model_route_type=ModelRouteOutcome,
        model_route_validate=ModelRouteOutcome.validate_against,
        model_route_structural_validate=ModelRouteOutcome.validate,
        model_route_canonical_bytes=ModelRouteOutcome.canonical_bytes,
        model_fixture_authority=_FIXED_LIVE_FIXTURE_AUTHORITY,
        assembly_authority=_FIXED_CANONICAL_ASSEMBLY_AUTHORITY,
        renderer_type=DeterministicPageRenderer,
        render_result_type=RenderResult,
        consistency_checker_type=MinimalConsistencyChecker,
        requirement_projector=project_requirement_view,
        acceptance_plan_compiler=compile_acceptance_plan,
        acceptance_binding_compiler=compile_acceptance_binding,
        acceptance_fixture_authority=_FIXED_ACCEPTANCE_FIXTURE_AUTHORITY,
        acceptance_executor=_FIXED_ACCEPTANCE_EXECUTION_AUTHORITY,
        packager_type=DeterministicResultPackager,
        result_package_type=ResultPackage,
        fallback_deliverer=deliver_frozen_g0_fallback,
        fallback_binding_authority=_FIXED_07A_FALLBACK_BINDING_AUTHORITY,
        path_preflight=_FIXED_07A_PATH_PREFLIGHT,
        staging_path=_FIXED_07A_STAGING_PATH,
        cleanup_directory=_FIXED_07A_CLEANUP_DIRECTORY,
        commit_staging=_FIXED_07A_COMMIT_STAGING,
        rollback_commit=_FIXED_07A_ROLLBACK_COMMIT,
        page_spec_projection=_07a_page_spec_artifacts,
        g0_projection=_07a_g0_binding,
        render_projection=_FIXED_07A_RENDER_PROJECTION,
        consistency_projection=_FIXED_07A_CONSISTENCY_PROJECTION,
        requirement_projection=_FIXED_07A_REQUIREMENT_PROJECTION,
        plan_projection=_FIXED_07A_PLAN_PROJECTION,
        binding_projection=_FIXED_07A_BINDING_PROJECTION,
        browser_projection=_FIXED_07A_BROWSER_PROJECTION,
        acceptance_counts_projection=_FIXED_07A_ACCEPTANCE_COUNTS,
        package_projection=_FIXED_07A_PACKAGE_PROJECTION,
        load_fallback_report=_FIXED_07A_LOAD_FALLBACK_REPORT,
        fallback_report_projection=_FIXED_07A_FALLBACK_REPORT_PROJECTION,
    )



def _bind_tier_a_07a_validate_against(
    *,
    model_route_type,
    model_route_validate,
    model_route_structural_validate,
    model_route_canonical_bytes,
    model_fixture_authority,
    assembly_authority,
    fallback_binding_authority,
    path_preflight,
    path_replay,
    renderer_type,
    render_result_type,
    consistency_checker_type,
    requirement_projector,
    acceptance_plan_compiler,
    acceptance_binding_compiler,
    acceptance_fixture_authority,
    acceptance_executor,
    packager_type,
    fallback_deliverer,
    staging_path,
    cleanup_directory,
    result_package_type,
    g0_projection,
    page_spec_projection,
    render_projection,
    consistency_projection,
    requirement_projection,
    plan_projection,
    binding_projection,
    browser_projection,
    acceptance_counts_projection,
    package_projection,
    package_live_binding,
    load_fallback_report,
    fallback_report_projection,
):
    def validate_against(
        self,
        *,
        model_route_outcome: object,
        frozen_g0_reference: object,
        package: object,
        context: object,
        guidance: object,
        manifest: object,
        selected: object,
        local_request: object,
        pre_invocation_audit: object,
        local_qwen_preparation: object,
        execution_branch: object,
        scripted_local_fixture: object = None,
        case_id: str,
        fallback_record: object,
        fallback_snapshot_dir: object,
        render_output_dir: object,
        model_package_output_dir: object,
        fallback_output_dir: object,
        scripted_acceptance_fixture: object = None,
    ) -> None:
        self.validate()
        if case_id != self.case_id:
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
        try:
            if type(model_route_outcome) is not model_route_type:
                raise TypeError("model route type is invalid")
            model_route_validate(
                model_route_outcome,
                frozen_g0_reference=frozen_g0_reference,
                package=package,
                context=context,
                guidance=guidance,
                manifest=manifest,
                selected=selected,
                local_request=local_request,
                pre_invocation_audit=pre_invocation_audit,
                local_qwen_preparation=local_qwen_preparation,
                execution_branch=execution_branch,
                scripted_local_fixture=scripted_local_fixture,
            )
            model_route_structural_validate(model_route_outcome)
        except Exception:
            if (
                self.status == "failed_delivery"
                and self.failure is not None
                and self.failure.code == "model_route_validation_failed"
            ):
                return
            raise TierA07aGateDeliveryOutcomeError(
                "live_replay_invalid"
            )
        actual_model_binding = {
            "verified": True,
            "outcome_id": model_route_outcome.outcome_id,
            "outcome_sha256": _sha256(
                model_route_canonical_bytes(model_route_outcome)
            ),
            "disposition": model_route_outcome.disposition,
            "failure_code": (
                None
                if model_route_outcome.failure is None
                else model_route_outcome.failure.code
            ),
            "assembled_page_id": model_route_outcome.artifacts[
                "assembled_page_id"
            ],
            "assembled_page_spec_sha256": model_route_outcome.artifacts[
                "assembled_page_spec_sha256"
            ],
        }
        actual_g0_binding = g0_projection(
            model_route_outcome.frozen_g0_reference
        )
        if (
            dict(self.model_route_binding) != actual_model_binding
            or dict(self.g0_binding) != actual_g0_binding
        ):
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")

        try:
            actual_fallback_binding = fallback_binding_authority(
                case_id=case_id,
                record=fallback_record,
                snapshot_dir=fallback_snapshot_dir,
                reference=frozen_g0_reference,
                package=package,
                context=context,
                guidance=guidance,
            )
        except Exception:
            if (
                self.status == "failed_delivery"
                and self.failure is not None
                and self.failure.code == "frozen_g0_fallback_invalid"
            ):
                return
            raise TierA07aGateDeliveryOutcomeError(
                "live_replay_invalid"
            )
        if dict(self.fallback_binding) != actual_fallback_binding:
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")

        if (
            self.status == "failed_delivery"
            and self.failure is not None
            and self.failure.code == "output_destination_invalid"
        ):
            try:
                path_preflight(
                    render_output_dir,
                    model_package_output_dir,
                    fallback_output_dir,
                    package_dir=package.package_dir,
                    snapshot_dir=fallback_snapshot_dir,
                )
            except Exception:
                return
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")

        try:
            destinations = path_replay(
                render_output_dir,
                model_package_output_dir,
                fallback_output_dir,
                package_dir=package.package_dir,
                snapshot_dir=fallback_snapshot_dir,
                render_expected=(_07A_STEP_RENDER in self.completed_steps),
                model_package_expected=(
                    _07A_STEP_G1_PACKAGE in self.completed_steps
                ),
                fallback_expected=(
                    _07A_STEP_FALLBACK_DELIVERY in self.completed_steps
                ),
            )
        except Exception as exc:
            raise TierA07aGateDeliveryOutcomeError(
                "live_replay_invalid"
            ) from exc

        expected_artifacts = _07a_empty_artifacts()
        expected_counts = _07a_empty_counts()

        def replay_staging_path(destination: object, label: str) -> object:
            try:
                return staging_path(destination, label)
            except Exception:
                raise TierA07aGateDeliveryOutcomeError(
                    "live_replay_invalid"
                ) from None

        def replay_render_failure(page_spec: PageSpec) -> bool:
            replay_dir = replay_staging_path(
                destinations.render_output_dir,
                "tier-a-07a-render-failure-replay",
            )
            try:
                # Only the captured Renderer call can prove render_failed.
                try:
                    rendered = renderer_type().render(page_spec, replay_dir)
                except Exception:
                    return True
                # A normal return enters a separate, fail-closed
                # validation phase.
                try:
                    render_projection(page_spec, rendered)
                except Exception:
                    raise TierA07aGateDeliveryOutcomeError(
                        "live_replay_invalid"
                    ) from None
                return False
            finally:
                cleanup_directory(replay_dir)

        def replay_package_failure(
            page_spec: PageSpec,
            render_result: object,
            consistency: object,
        ) -> bool:
            replay_dir = replay_staging_path(
                destinations.model_package_output_dir,
                "tier-a-07a-package-failure-replay",
            )
            try:
                # Only the captured v1 packager call can prove
                # model_package_failed.
                try:
                    package = packager_type().package(
                        context,
                        page_spec,
                        render_result,
                        consistency,
                        replay_dir,
                    )
                except Exception:
                    return True
                # A normal return enters a separate, fail-closed
                # validation phase.
                try:
                    package_projection(package, replay_dir)
                except Exception:
                    raise TierA07aGateDeliveryOutcomeError(
                        "live_replay_invalid"
                    ) from None
                return False
            finally:
                cleanup_directory(replay_dir)

        def replay_fallback_delivery_failure() -> bool:
            replay_dir = replay_staging_path(
                destinations.fallback_output_dir,
                "tier-a-07a-fallback-failure-replay",
            )
            try:
                # Only the captured fallback deliverer call can prove
                # fallback_delivery_failed.
                try:
                    report = fallback_deliverer(
                        fallback_snapshot_dir,
                        fallback_record,
                        replay_dir,
                    )
                except Exception:
                    return True
                # A normal return enters a separate, fail-closed
                # validation phase.
                try:
                    report.validate_against(
                        fallback_snapshot_dir,
                        replay_dir / "result_package",
                        fallback_record,
                    )
                except Exception:
                    raise TierA07aGateDeliveryOutcomeError(
                        "live_replay_invalid"
                    ) from None
                return False
            finally:
                cleanup_directory(replay_dir)

        def validate_fallback(
            reason: str,
            prefix: tuple[str, ...],
            fixture: object,
        ) -> None:
            if self.fallback_reason != reason:
                raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
            if self.status == "fallback_delivery":
                if self.completed_steps != (
                    *prefix,
                    _07A_STEP_FALLBACK_DELIVERY,
                ):
                    raise TierA07aGateDeliveryOutcomeError(
                        "live_replay_invalid"
                    )
                report = load_fallback_report(
                    destinations.fallback_output_dir,
                    fallback_snapshot_dir,
                    fallback_record,
                )
                expected_artifacts.update(fallback_report_projection(report))
                if (
                    report.case_id != case_id
                    or report.package_id
                    != actual_fallback_binding["package_id"]
                    or report.page_id != actual_fallback_binding["page_id"]
                    or report.delivered_package_tree_sha256
                    != actual_fallback_binding["package_tree_sha256"]
                ):
                    raise TierA07aGateDeliveryOutcomeError(
                        "live_replay_invalid"
                    )
            elif (
                self.status == "failed_delivery"
                and self.failure is not None
                and self.failure.code == "fallback_delivery_failed"
            ):
                if self.completed_steps != prefix:
                    raise TierA07aGateDeliveryOutcomeError(
                        "live_replay_invalid"
                    )
                if not replay_fallback_delivery_failure():
                    raise TierA07aGateDeliveryOutcomeError(
                        "live_replay_invalid"
                    )
            else:
                raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
            if fixture != self.scripted_acceptance_fixture:
                raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
            if dict(self.artifacts) != expected_artifacts:
                raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")


        assembled = None
        if model_route_outcome.disposition == "scripted_fixture_assembled":
            try:
                live_model_fixture = model_fixture_authority(
                    scripted_local_fixture
                )
                assembled = assembly_authority(
                    live_model_fixture.raw_response,
                    context,
                    guidance,
                )
                assembly_report = assembled.report
                expected_model_artifacts = {
                    "raw_response_sha256": live_model_fixture.raw_response.sha256,
                    "raw_response_byte_length": len(
                        live_model_fixture.raw_response.raw_bytes
                    ),
                    "model_semantic_candidate_sha256": (
                        assembled.candidate.sha256()
                    ),
                    "assembled_page_id": assembled.page_spec.page_id,
                    "assembly_report_id": assembly_report.report_id,
                    "assembly_report_sha256": assembly_report.sha256(),
                    "assembled_page_spec_sha256": (
                        assembly_report.assembled_page_spec_sha256
                    ),
                }
                if (
                    dict(model_route_outcome.artifacts)
                    != expected_model_artifacts
                    or assembled.page_spec.page_id
                    != frozen_g0_reference.page_id
                ):
                    raise ValueError("assembled model-route binding is invalid")
            except Exception as exc:
                raise TierA07aGateDeliveryOutcomeError(
                    "live_replay_invalid"
                ) from exc
        if assembled is None:
            if (
                self.gate_status != "not_executed"
                or self.consistency_status != "not_executed"
                or self.acceptance_status != "not_executed"
                or dict(self.acceptance_counts) != expected_counts
            ):
                raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
            validate_fallback(
                "model_route_not_assembled",
                _07A_DIRECT_FALLBACK_PREFIX,
                None,
            )
            return

        page_spec = assembled.page_spec
        expected_artifacts.update(page_spec_projection(page_spec))
        page_prefix = (*_07A_DIRECT_FALLBACK_PREFIX, _07A_STEP_PAGE_SPEC)
        if _07A_STEP_RENDER not in self.completed_steps:
            if not replay_render_failure(page_spec):
                raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
            validate_fallback("render_failed", page_prefix, None)
            return
        render_result = render_result_type(
            page_id=page_spec.page_id,
            output_dir=destinations.render_output_dir,
            index_html=destinations.render_output_dir / "index.html",
            styles_css=destinations.render_output_dir / "styles.css",
            app_js=destinations.render_output_dir / "app.js",
            render_manifest=destinations.render_output_dir / "render_manifest.json",
        )
        expected_artifacts.update(render_projection(page_spec, render_result))
        render_prefix = (*page_prefix, _07A_STEP_RENDER)

        try:
            consistency = consistency_checker_type().check(
                page_spec,
                render_result,
            )
            consistency.validate()
            expected_artifacts.update(consistency_projection(consistency))
        except Exception:
            validate_fallback("consistency_failed", render_prefix, None)
            return
        if _07A_STEP_CONSISTENCY not in self.completed_steps:
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
        consistency_status = "pass" if consistency.passed else "fail"
        if self.consistency_status != consistency_status:
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
        consistency_prefix = (*render_prefix, _07A_STEP_CONSISTENCY)

        try:
            requirement_view = requirement_projector(context)
            requirement_view.validate()
            expected_artifacts.update(requirement_projection(requirement_view))
        except Exception:
            validate_fallback(
                "requirement_view_failed",
                consistency_prefix,
                None,
            )
            return
        if _07A_STEP_REQUIREMENT_VIEW not in self.completed_steps:
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
        requirement_prefix = (*consistency_prefix, _07A_STEP_REQUIREMENT_VIEW)

        try:
            acceptance_plan = acceptance_plan_compiler(requirement_view)
            acceptance_plan.validate_against(requirement_view)
            expected_artifacts.update(plan_projection(acceptance_plan))
        except Exception:
            validate_fallback(
                "acceptance_plan_failed",
                requirement_prefix,
                None,
            )
            return
        if _07A_STEP_ACCEPTANCE_PLAN not in self.completed_steps:
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
        plan_prefix = (*requirement_prefix, _07A_STEP_ACCEPTANCE_PLAN)

        try:
            binding_plan = acceptance_binding_compiler(
                requirement_view,
                acceptance_plan,
                page_spec,
                render_result,
            )
            binding_plan.validate_against(
                requirement_view,
                acceptance_plan,
                page_spec,
                render_result,
            )
            expected_artifacts.update(binding_projection(binding_plan))
        except Exception:
            validate_fallback(
                "acceptance_binding_failed",
                plan_prefix,
                None,
            )
            return
        if _07A_STEP_ACCEPTANCE_BINDING not in self.completed_steps:
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
        binding_prefix = (*plan_prefix, _07A_STEP_ACCEPTANCE_BINDING)

        try:
            live_acceptance_fixture = acceptance_fixture_authority(
                scripted_acceptance_fixture
            )
        except Exception:
            validate_fallback(
                "acceptance_fixture_invalid",
                binding_prefix,
                None,
            )
            return
        fixture_prefix = (*binding_prefix, _07A_STEP_ACCEPTANCE_FIXTURE)
        if _07A_STEP_ACCEPTANCE_FIXTURE not in self.completed_steps:
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
        if self.scripted_acceptance_fixture != live_acceptance_fixture:
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")

        try:
            browser_report = acceptance_executor(
                live_acceptance_fixture,
                binding_plan,
            )
            browser_report.validate_against(binding_plan)
            expected_artifacts.update(browser_projection(browser_report))
            expected_counts = acceptance_counts_projection(browser_report)
        except Exception:
            validate_fallback(
                "acceptance_execution_failed",
                fixture_prefix,
                live_acceptance_fixture,
            )
            return
        execution_prefix = (*fixture_prefix, _07A_STEP_ACCEPTANCE_EXECUTION)
        if _07A_STEP_ACCEPTANCE_EXECUTION not in self.completed_steps:
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
        acceptance_status = (
            "blocked"
            if expected_counts["fail"] > 0
            or expected_counts["unknown"] > 0
            else "pass"
        )
        gate_status = (
            "passed"
            if consistency_status == "pass" and acceptance_status == "pass"
            else "blocked"
        )
        if (
            self.consistency_status != consistency_status
            or self.acceptance_status != acceptance_status
            or self.gate_status != gate_status
            or dict(self.acceptance_counts) != expected_counts
        ):
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
        if gate_status != "passed":
            validate_fallback(
                "consistency_or_acceptance_blocked",
                execution_prefix,
                live_acceptance_fixture,
            )
            return


        if _07A_STEP_G1_PACKAGE not in self.completed_steps:
            if not replay_package_failure(
                page_spec,
                render_result,
                consistency,
            ):
                raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")
            validate_fallback(
                "model_package_failed",
                execution_prefix,
                live_acceptance_fixture,
            )
            return
        model_package = result_package_type(
            package_id=self.artifacts["model_package_id"],
            page_id=page_spec.page_id,
            package_dir=destinations.model_package_output_dir,
            entrypoint="page/index.html",
            result_summary="result_summary.json",
            package_manifest="package_manifest.json",
        )
        try:
            expected_package_artifacts = package_live_binding(
                context=context,
                page_spec=page_spec,
                render_result=render_result,
                consistency_report=consistency,
                supplied_package=model_package,
                supplied_dir=destinations.model_package_output_dir,
            )
        except Exception as exc:
            raise TierA07aGateDeliveryOutcomeError(
                "live_replay_invalid"
            ) from exc
        expected_artifacts.update(expected_package_artifacts)
        if (
            self.status != "first_pass_success"
            or self.completed_steps != _07A_CANDIDATE_SEQUENCE
            or self.fallback_reason != "none"
            or self.scripted_acceptance_fixture != live_acceptance_fixture
            or dict(self.artifacts) != expected_artifacts
            or self.final_package_schema_version
            != expected_package_artifacts["model_package_schema_version"]
            or self.final_package_id
            != expected_package_artifacts["model_package_id"]
            or self.final_package_manifest_sha256
            != expected_package_artifacts["model_package_manifest_sha256"]
            or self.final_package_tree_sha256
            != expected_package_artifacts["model_package_tree_sha256"]
            or self.final_package_file_count
            != expected_package_artifacts["model_package_file_count"]
        ):
            raise TierA07aGateDeliveryOutcomeError("live_replay_invalid")

    return validate_against


TierA07aGateDeliveryOutcome.validate_against = (
    _bind_tier_a_07a_validate_against(
        model_route_type=ModelRouteOutcome,
        model_route_validate=ModelRouteOutcome.validate_against,
        model_route_structural_validate=ModelRouteOutcome.validate,
        model_route_canonical_bytes=ModelRouteOutcome.canonical_bytes,
        model_fixture_authority=_FIXED_LIVE_FIXTURE_AUTHORITY,
        assembly_authority=_FIXED_CANONICAL_ASSEMBLY_AUTHORITY,
        fallback_binding_authority=_FIXED_07A_FALLBACK_BINDING_AUTHORITY,
        path_preflight=_FIXED_07A_PATH_PREFLIGHT,
        path_replay=_FIXED_07A_PATH_REPLAY,
        renderer_type=DeterministicPageRenderer,
        render_result_type=RenderResult,
        consistency_checker_type=MinimalConsistencyChecker,
        requirement_projector=project_requirement_view,
        acceptance_plan_compiler=compile_acceptance_plan,
        acceptance_binding_compiler=compile_acceptance_binding,
        acceptance_fixture_authority=_FIXED_ACCEPTANCE_FIXTURE_AUTHORITY,
        acceptance_executor=_FIXED_ACCEPTANCE_EXECUTION_AUTHORITY,
        packager_type=DeterministicResultPackager,
        fallback_deliverer=deliver_frozen_g0_fallback,
        staging_path=_FIXED_07A_STAGING_PATH,
        cleanup_directory=_FIXED_07A_CLEANUP_DIRECTORY,
        result_package_type=ResultPackage,
        g0_projection=_07a_g0_binding,
        page_spec_projection=_07a_page_spec_artifacts,
        render_projection=_FIXED_07A_RENDER_PROJECTION,
        consistency_projection=_FIXED_07A_CONSISTENCY_PROJECTION,
        requirement_projection=_FIXED_07A_REQUIREMENT_PROJECTION,
        plan_projection=_FIXED_07A_PLAN_PROJECTION,
        binding_projection=_FIXED_07A_BINDING_PROJECTION,
        browser_projection=_FIXED_07A_BROWSER_PROJECTION,
        acceptance_counts_projection=_FIXED_07A_ACCEPTANCE_COUNTS,
        package_projection=_FIXED_07A_PACKAGE_PROJECTION,
        package_live_binding=_FIXED_07A_PACKAGE_LIVE_BINDING,
        load_fallback_report=_FIXED_07A_LOAD_FALLBACK_REPORT,
        fallback_report_projection=_FIXED_07A_FALLBACK_REPORT_PROJECTION,
    )
)


# Tier A-07b one-repair is appended below.
# This local deterministic slice captures its rule and delivery authorities at
# class-definition time. Field-gate receipts are replay evidence, not authority.

TIER_A_07B_FIELD_GATE_REPORT_SCHEMA_VERSION = "req2web.orchestration.tier_a_07b_field_gate_report.v1"
TIER_A_07B_REPAIR_PATCH_SCHEMA_VERSION = "req2web.orchestration.tier_a_07b_repair_patch.v1"
TIER_A_07B_GATE_DELIVERY_OUTCOME_SCHEMA_VERSION = "req2web.orchestration.tier_a_07b_gate_delivery_outcome.v1"
TIER_A_07B_FIELD_GATE_AUTHORITY_SCHEMA_VERSION = "req2web.orchestration.tier_a_07b_field_gate_authority.v1"
TIER_A_07B_FIELD_GATE_RULE_VERSION = "tier-a-07b-scripted-field-rules.v2"
_07B_REPORT_ID_PREFIX = "tier-a-07b-field-gate-"
_07B_PATCH_ID_PREFIX = "tier-a-07b-repair-patch-"
_07B_OUTCOME_ID_PREFIX = "tier-a-07b-gate-delivery-"
_07B_AUTHORITY_ID_PREFIX = "tier-a-07b-field-gate-authority-"
_07B_SCOPE_PATTERN = re.compile(r"^pagespec(?:\.(?:title|summary|target_device|page_type)|\.(?:components|states|interactions|acceptance_checks)\.[a-z0-9][a-z0-9-]*\.(?:label|name|user_feedback|description))$")
_07B_FORBIDDEN = ("/", "\\", "://", "\x00", "\n", "\r", "bearer", "token", "secret", "credential", "authorization", "api_key", "apikey")
_07B_REPAIRABLE = {"page_title_invalid": "page_title", "component_label_invalid": "component_label", "state_name_invalid": "state_name", "interaction_feedback_invalid": "interaction_feedback", "acceptance_description_invalid": "acceptance_description"}

def _07b_hash(value: object, _sha256_fn=_sha256,
              _canonical_json_bytes_fn=_canonical_json_bytes) -> str:
    return _sha256_fn(_canonical_json_bytes_fn(value))

def _07b_safe_public_text(value: object, _forbidden=_07B_FORBIDDEN) -> bool:
    return type(value) is str and bool(value) and len(value) <= 160 and all(ch.isprintable() for ch in value) and not re.match(r"^[A-Za-z]:", value) and not any(token in value.lower() for token in _forbidden)

def _07b_safe_scope(value: object, _pattern=_07B_SCOPE_PATTERN) -> bool:
    return type(value) is str and _pattern.fullmatch(value) is not None

def _07b_scope(values: object, name: str, *, allow_empty: bool = True,
               _safe_scope=_07b_safe_scope) -> tuple[str, ...]:
    if type(values) is not tuple or (not allow_empty and not values) or values != tuple(sorted(values)) or len(values) != len(set(values)) or any(not _safe_scope(item) for item in values):
        raise TierA07bGateDeliveryOutcomeError(name + "_invalid")
    return values

def _07b_page_binding(page_spec: PageSpec, _hash=_07b_hash) -> dict[str, str]:
    page_spec.validate()
    payload = page_spec.to_dict()
    return {"page_id": page_spec.page_id, "page_spec_sha256": _hash(payload), "traceability_sha256": _hash(payload["traceability"])}

def _07b_empty(keys: tuple[str, ...]) -> dict[str, object]:
    return {key: None for key in keys}

def _07b_slots(page_spec: PageSpec, _safe_id=_07a_safe_id) -> dict[str, tuple[object, str]]:
    page_spec.validate()
    slots: dict[str, tuple[object, str]] = {"pagespec.title": (page_spec, "title"), "pagespec.summary": (page_spec, "summary"), "pagespec.target_device": (page_spec, "target_device"), "pagespec.page_type": (page_spec, "page_type")}
    for collection, prefix, id_name, field in ((page_spec.components, "components", "component_id", "label"), (page_spec.states, "states", "state_id", "name"), (page_spec.interactions, "interactions", "interaction_id", "user_feedback"), (page_spec.acceptance_checks, "acceptance_checks", "check_id", "description")):
        for item in collection:
            stable_id = getattr(item, id_name)
            if _safe_id(stable_id):
                slots[f"pagespec.{prefix}.{stable_id}.{field}"] = (item, field)
    return slots

def _07b_policy_scope(error_code: str, page_spec: PageSpec,
                      _repairable=_07B_REPAIRABLE, _slots=_07b_slots) -> tuple[str, ...]:
    kind = _repairable.get(error_code)
    if kind == "page_title":
        return ("pagespec.title",)
    forms = {"component_label": ("pagespec.components.", ".label"), "state_name": ("pagespec.states.", ".name"), "interaction_feedback": ("pagespec.interactions.", ".user_feedback"), "acceptance_description": ("pagespec.acceptance_checks.", ".description")}
    if kind not in forms:
        return ()
    left, right = forms[kind]
    return tuple(sorted(path for path in _slots(page_spec) if path.startswith(left) and path.endswith(right)))

def _07b_intersection(predicted: tuple[str, ...], policy: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(predicted).intersection(policy)))

def _07b_replace_payload_value(payload: dict[str, object], path: str, value: str) -> None:
    parts = path.split(".")
    if len(parts) == 2:
        payload[parts[1]] = value
        return
    if len(parts) != 4:
        raise TierA07bGateDeliveryOutcomeError("repair_patch_invalid")
    collection, stable_id, field = parts[1:]
    id_name = {"components": "component_id", "states": "state_id", "interactions": "interaction_id", "acceptance_checks": "check_id"}.get(collection)
    values = payload.get(collection)
    matches = [] if id_name is None or type(values) is not list else [item for item in values if type(item) is dict and item.get(id_name) == stable_id]
    if len(matches) != 1 or field not in matches[0]:
        raise TierA07bGateDeliveryOutcomeError("repair_patch_invalid")
    matches[0][field] = value

def _07b_validate_repaired(before: PageSpec, after: PageSpec, scope: tuple[str, ...],
                           _slots=_07b_slots,
                           _replace_payload_value=_07b_replace_payload_value) -> None:
    before.validate(); after.validate()
    original, changed = before.to_dict(), after.to_dict()
    if before.page_id != after.page_id or original["traceability"] != changed["traceability"] or original["use_cases"] != changed["use_cases"] or original["constraints"] != changed["constraints"]:
        raise TierA07bGateDeliveryOutcomeError("repair_identity_or_traceability_drift")
    expected = deepcopy(original)
    slots = _slots(after)
    for path in scope:
        if path not in slots:
            raise TierA07bGateDeliveryOutcomeError("repair_patch_invalid")
        target, field = slots[path]
        _replace_payload_value(expected, path, getattr(target, field))
    if expected != changed:
        raise TierA07bGateDeliveryOutcomeError("repair_patch_invalid")

class TierA07bGateDeliveryOutcomeError(ValueError):
    """Unsafe, noncanonical, or non-replayable A-07b state."""

@dataclass(frozen=True)
class TierA07bFieldGateReport:
    report_id: str
    authority_id: str
    authority_sha256: str
    rule_version: str
    case_id: str
    request_sha256: str
    field_gate_input_sha256: str
    page_id: str
    first_page_spec_sha256: str
    reported_field: str | None
    error_code: str
    expected: str | None
    actual: str | None
    predicted_repairable: bool
    predicted_repair_scope: tuple[str, ...]
    policy_allowed_scope: tuple[str, ...]
    repair_eligible: bool
    decision: str
    schema_version: str = TIER_A_07B_FIELD_GATE_REPORT_SCHEMA_VERSION

    @classmethod
    def create(cls, _hash=_07b_hash, _schema_version=TIER_A_07B_FIELD_GATE_REPORT_SCHEMA_VERSION,
               _id_prefix=_07B_REPORT_ID_PREFIX, **values: object) -> "TierA07bFieldGateReport":
        values = dict(values); values.setdefault("schema_version", _schema_version)
        root = {key: (list(value) if key in {"predicted_repair_scope", "policy_allowed_scope"} else value) for key, value in values.items() if key != "report_id"}
        values["report_id"] = _id_prefix + _hash(root)[:20]
        result = cls(**values); result.validate(); return result

    def _root(self) -> dict[str, object]:
        return {"authority_id": self.authority_id, "authority_sha256": self.authority_sha256, "rule_version": self.rule_version, "case_id": self.case_id, "request_sha256": self.request_sha256, "field_gate_input_sha256": self.field_gate_input_sha256, "page_id": self.page_id, "first_page_spec_sha256": self.first_page_spec_sha256, "reported_field": self.reported_field, "error_code": self.error_code, "expected": self.expected, "actual": self.actual, "predicted_repairable": self.predicted_repairable, "predicted_repair_scope": list(self.predicted_repair_scope), "policy_allowed_scope": list(self.policy_allowed_scope), "repair_eligible": self.repair_eligible, "decision": self.decision, "schema_version": self.schema_version}

    def validate(self, _schema_version=TIER_A_07B_FIELD_GATE_REPORT_SCHEMA_VERSION,
                 _id_prefix=_07B_REPORT_ID_PREFIX, _safe_id=_07a_safe_id,
                 _scope=_07b_scope, _safe_scope=_07b_safe_scope,
                 _safe_text=_07b_safe_public_text, _hash=_07b_hash,
                 _is_sha256_fn=_is_sha256,
                 _repairable=_07B_REPAIRABLE) -> None:
        if self.schema_version != _schema_version or not all(_safe_id(value) for value in (self.report_id, self.authority_id, self.rule_version, self.case_id, self.page_id, self.error_code)) or not all(_is_sha256_fn(value) for value in (self.authority_sha256, self.request_sha256, self.field_gate_input_sha256, self.first_page_spec_sha256)) or type(self.predicted_repairable) is not bool or type(self.repair_eligible) is not bool:
            raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")
        predicted = _scope(self.predicted_repair_scope, "predicted_scope")
        policy = _scope(self.policy_allowed_scope, "policy_scope")
        if self.decision == "repair":
            if not _safe_scope(self.reported_field) or self.error_code not in _repairable or not _safe_text(self.expected) or not _safe_text(self.actual) or self.expected == self.actual or not self.predicted_repairable or not self.repair_eligible or predicted != (self.reported_field,) or self.reported_field not in policy:
                raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")
        elif self.decision == "pass":
            if self.error_code != "none" or any(value is not None for value in (self.reported_field, self.expected, self.actual)) or self.predicted_repairable or self.repair_eligible or predicted or policy:
                raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")
        else:
            raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")
        if self.report_id != _id_prefix + _hash(self._root())[:20]:
            raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")

    def validate_against(self, case_id: object, page_spec: object, request_sha256: object,
                         _page_binding=_07b_page_binding, _slots=_07b_slots,
                         _policy_scope=_07b_policy_scope,
                         _page_spec_type=PageSpec) -> None:
        self.validate()
        if type(page_spec) is not _page_spec_type or self.case_id != case_id or self.request_sha256 != request_sha256:
            raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")
        binding = _page_binding(page_spec)
        if self.page_id != binding["page_id"] or self.first_page_spec_sha256 != binding["page_spec_sha256"]:
            raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")
        if self.decision == "repair":
            slots = _slots(page_spec)
            if self.reported_field not in slots or getattr(*slots[self.reported_field]) != self.actual or _policy_scope(self.error_code, page_spec) != self.policy_allowed_scope:
                raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate(); return {"report_id": self.report_id, **self._root()}
    def canonical_bytes(self, _canonical_json_bytes_fn=_canonical_json_bytes) -> bytes:
        return _canonical_json_bytes_fn(self.to_dict())
    def sha256(self, _sha256_fn=_sha256) -> str:
        return _sha256_fn(self.canonical_bytes())
    @classmethod
    def from_dict(cls, payload: object) -> "TierA07bFieldGateReport":
        keys = {"report_id", *cls.create.__annotations__.keys()}
        expected = {"report_id", "authority_id", "authority_sha256", "rule_version", "case_id", "request_sha256", "field_gate_input_sha256", "page_id", "first_page_spec_sha256", "reported_field", "error_code", "expected", "actual", "predicted_repairable", "predicted_repair_scope", "policy_allowed_scope", "repair_eligible", "decision", "schema_version"}
        if not isinstance(payload, Mapping) or set(payload) != expected or type(payload["predicted_repair_scope"]) is not list or type(payload["policy_allowed_scope"]) is not list:
            raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")
        result = cls(**{key: (tuple(payload[key]) if key in {"predicted_repair_scope", "policy_allowed_scope"} else payload[key]) for key in expected})
        result.validate(); return result
    @classmethod
    def from_bytes(cls, raw: object, _load_canonical_json_fn=_load_canonical_json) -> "TierA07bFieldGateReport":
        if type(raw) is not bytes:
            raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")
        try: result = cls.from_dict(_load_canonical_json_fn(raw))
        except Exception as exc:
            if isinstance(exc, TierA07bGateDeliveryOutcomeError): raise
            raise TierA07bGateDeliveryOutcomeError("repair_report_invalid") from exc
        if result.canonical_bytes() != raw:
            raise TierA07bGateDeliveryOutcomeError("repair_report_invalid")
        return result

@dataclass(frozen=True)
class TierA07bRepairPatch:
    patch_id: str
    report_id: str
    report_sha256: str
    first_page_id: str
    first_page_spec_sha256: str
    attempt_index: int
    operations: tuple[tuple[str, str], ...]
    schema_version: str = TIER_A_07B_REPAIR_PATCH_SCHEMA_VERSION

    @classmethod
    def create(cls, _hash=_07b_hash, _schema_version=TIER_A_07B_REPAIR_PATCH_SCHEMA_VERSION,
               _id_prefix=_07B_PATCH_ID_PREFIX, **values: object) -> "TierA07bRepairPatch":
        root = {"report_id": values["report_id"], "report_sha256": values["report_sha256"], "first_page_id": values["first_page_id"], "first_page_spec_sha256": values["first_page_spec_sha256"], "attempt_index": values["attempt_index"], "operations": [{"path": path, "value": value} for path, value in values["operations"]], "schema_version": _schema_version}
        result = cls(**values, patch_id=_id_prefix + _hash(root)[:20]); result.validate(); return result
    def _root(self) -> dict[str, object]:
        return {"report_id": self.report_id, "report_sha256": self.report_sha256, "first_page_id": self.first_page_id, "first_page_spec_sha256": self.first_page_spec_sha256, "attempt_index": self.attempt_index, "operations": [{"path": path, "value": value} for path, value in self.operations], "schema_version": self.schema_version}
    def validate(self, _schema_version=TIER_A_07B_REPAIR_PATCH_SCHEMA_VERSION,
                 _id_prefix=_07B_PATCH_ID_PREFIX, _safe_id=_07a_safe_id,
                 _safe_scope=_07b_safe_scope, _safe_text=_07b_safe_public_text,
                 _hash=_07b_hash, _is_sha256_fn=_is_sha256) -> None:
        if self.schema_version != _schema_version or not all(_safe_id(value) for value in (self.patch_id, self.report_id, self.first_page_id)) or not _is_sha256_fn(self.report_sha256) or not _is_sha256_fn(self.first_page_spec_sha256) or type(self.attempt_index) is not int or self.attempt_index != 1 or type(self.operations) is not tuple or not self.operations or len(self.operations) > 4:
            raise TierA07bGateDeliveryOutcomeError("repair_request_invalid")
        paths = tuple(path for path, _ in self.operations)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)) or any(not _safe_scope(path) or not _safe_text(value) for path, value in self.operations) or self.patch_id != _id_prefix + _hash(self._root())[:20]:
            raise TierA07bGateDeliveryOutcomeError("repair_request_invalid")
    def apply(self, report: TierA07bFieldGateReport, page_spec: PageSpec, scope: tuple[str, ...],
              _page_binding=_07b_page_binding, _slots=_07b_slots,
              _validate_repaired=_07b_validate_repaired) -> PageSpec:
        self.validate(); report.validate(); binding = _page_binding(page_spec)
        if self.report_id != report.report_id or self.report_sha256 != report.sha256() or self.first_page_id != binding["page_id"] or self.first_page_spec_sha256 != binding["page_spec_sha256"] or tuple(path for path, _ in self.operations) != scope:
            raise TierA07bGateDeliveryOutcomeError("repair_patch_invalid")
        repaired = deepcopy(page_spec); slots = _slots(repaired)
        for path, value in self.operations:
            if path not in slots: raise TierA07bGateDeliveryOutcomeError("repair_patch_invalid")
            target, field = slots[path]; setattr(target, field, value)
        _validate_repaired(page_spec, repaired, scope); return repaired
    def to_dict(self) -> dict[str, object]:
        self.validate(); return {"patch_id": self.patch_id, **self._root()}
    def canonical_bytes(self, _canonical_json_bytes_fn=_canonical_json_bytes) -> bytes:
        return _canonical_json_bytes_fn(self.to_dict())
    def sha256(self, _sha256_fn=_sha256) -> str:
        return _sha256_fn(self.canonical_bytes())
    @classmethod
    def from_dict(cls, payload: object) -> "TierA07bRepairPatch":
        keys = {"patch_id", "report_id", "report_sha256", "first_page_id", "first_page_spec_sha256", "attempt_index", "operations", "schema_version"}
        if not isinstance(payload, Mapping) or set(payload) != keys or type(payload["operations"]) is not list:
            raise TierA07bGateDeliveryOutcomeError("repair_request_invalid")
        ops = []
        for item in payload["operations"]:
            if not isinstance(item, Mapping) or set(item) != {"path", "value"}: raise TierA07bGateDeliveryOutcomeError("repair_request_invalid")
            ops.append((item["path"], item["value"]))
        result = cls(patch_id=payload["patch_id"], report_id=payload["report_id"], report_sha256=payload["report_sha256"], first_page_id=payload["first_page_id"], first_page_spec_sha256=payload["first_page_spec_sha256"], attempt_index=payload["attempt_index"], operations=tuple(ops), schema_version=payload["schema_version"]); result.validate(); return result
    @classmethod
    def from_bytes(cls, raw: object, _load_canonical_json_fn=_load_canonical_json) -> "TierA07bRepairPatch":
        if type(raw) is not bytes: raise TierA07bGateDeliveryOutcomeError("repair_request_invalid")
        try: result = cls.from_dict(_load_canonical_json_fn(raw))
        except Exception as exc:
            if isinstance(exc, TierA07bGateDeliveryOutcomeError): raise
            raise TierA07bGateDeliveryOutcomeError("repair_request_invalid") from exc
        if result.canonical_bytes() != raw: raise TierA07bGateDeliveryOutcomeError("repair_request_invalid")
        return result


def _build_tier_a_07b_field_gate_authority(
    report_type, request_type, request_bytes, _hash=_07b_hash,
    _sha256_fn=_sha256, _canonical_json_bytes_fn=_canonical_json_bytes,
    _page_binding=_07b_page_binding, _slots=_07b_slots,
    _safe_id=_07a_safe_id, _safe_text=_07b_safe_public_text,
    _policy_scope=_07b_policy_scope,
    _schema_version=TIER_A_07B_FIELD_GATE_AUTHORITY_SCHEMA_VERSION,
    _rule_version=TIER_A_07B_FIELD_GATE_RULE_VERSION,
    _authority_id_prefix=_07B_AUTHORITY_ID_PREFIX,
    _page_spec_type=PageSpec,
):
    registry = {"schema_version": _schema_version, "rule_version": _rule_version, "rules": [{"rule_id": "primary_component_label_suffix", "error_code": "component_label_invalid", "selection": "first_canonical_component_label", "canonical_expected_template": "component-label-{component_id}"}], "immutable": True, "runtime_registration_api": "none", "model_loaded": False, "provider_invoked": False, "network_used": False, "h1_gold_accessed": False}
    authority_sha256 = _sha256_fn(_canonical_json_bytes_fn(registry))
    authority_id = _authority_id_prefix + authority_sha256[:20]
    def evaluate(*, case_id: object, page_spec: object, local_request: object) -> TierA07bFieldGateReport:
        if not _safe_id(case_id) or type(page_spec) is not _page_spec_type or type(local_request) is not request_type:
            raise ValueError("field gate inputs are invalid")
        page_spec.validate()
        request_raw = request_bytes(local_request)
        if type(request_raw) is not bytes: raise ValueError("local request bytes are invalid")
        request_sha256 = _sha256_fn(request_raw); binding = _page_binding(page_spec)
        input_sha256 = _hash({"authority_id": authority_id, "authority_sha256": authority_sha256, "rule_version": _rule_version, "case_id": case_id, "request_sha256": request_sha256, "page_id": binding["page_id"], "page_spec_sha256": binding["page_spec_sha256"]})
        paths = tuple(sorted(path for path in _slots(page_spec) if path.startswith("pagespec.components.") and path.endswith(".label")))
        common = {"authority_id": authority_id, "authority_sha256": authority_sha256, "rule_version": _rule_version, "case_id": case_id, "request_sha256": request_sha256, "field_gate_input_sha256": input_sha256, "page_id": binding["page_id"], "first_page_spec_sha256": binding["page_spec_sha256"]}
        if not paths:
            return report_type.create(**common, reported_field=None, error_code="none", expected=None, actual=None, predicted_repairable=False, predicted_repair_scope=(), policy_allowed_scope=(), repair_eligible=False, decision="pass")
        field = paths[0]; target, name = _slots(page_spec)[field]; actual = getattr(target, name)
        expected = "component-label-" + field.split(".")[2]
        if not _safe_text(actual) or not _safe_text(expected): raise ValueError("field gate scalar is invalid")
        if actual == expected:
            return report_type.create(**common, reported_field=None, error_code="none", expected=None, actual=None, predicted_repairable=False, predicted_repair_scope=(), policy_allowed_scope=(), repair_eligible=False, decision="pass")
        return report_type.create(**common, reported_field=field, error_code="component_label_invalid", expected=expected, actual=actual, predicted_repairable=True, predicted_repair_scope=(field,), policy_allowed_scope=_policy_scope("component_label_invalid", page_spec), repair_eligible=True, decision="repair")
    evaluate.authority_id = authority_id
    evaluate.authority_sha256 = authority_sha256
    evaluate.rule_version = _rule_version
    return evaluate

_FIXED_07B_FIELD_GATE_AUTHORITY = _build_tier_a_07b_field_gate_authority(TierA07bFieldGateReport, D17Path3LocalRequestArtifact, D17Path3LocalRequestArtifact.canonical_bytes)

def create_tier_a_07b_field_gate_report(*, case_id: object, page_spec: object, local_request: object) -> TierA07bFieldGateReport:
    return _FIXED_07B_FIELD_GATE_AUTHORITY(case_id=case_id, page_spec=page_spec, local_request=local_request)

_07B_COUNT_KEYS = _07A_ACCEPTANCE_COUNT_KEYS
_07B_FIRST_KEYS = ("page_id", "page_spec_sha256", "traceability_sha256")
_07B_PATCH_KEYS = ("patch_id", "patch_sha256", "operation_count")
_07B_REPORT_KEYS = ("report_id", "report_sha256", "authority_id", "authority_sha256", "rule_version", "request_sha256", "field_gate_input_sha256", "error_code", "decision", "repair_eligible")
_07B_FAILURES = {"model_route_validation_failed", "frozen_g0_fallback_invalid", "output_destination_invalid", "fallback_delivery_failed", "fallback_live_replay_invalid"}

def _07b_empty_counts(_keys=_07B_COUNT_KEYS) -> dict[str, int]: return {key: 0 for key in _keys}
def _07b_validate_counts(value: object, _keys=_07B_COUNT_KEYS) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(_keys): raise TierA07bGateDeliveryOutcomeError("acceptance_counts_invalid")
    result = {key: value[key] for key in _keys}
    if any(type(item) is not int or item < 0 for item in result.values()) or result["pass"] + result["fail"] + result["unknown"] + result["not_supported"] != result["total"]: raise TierA07bGateDeliveryOutcomeError("acceptance_counts_invalid")
    return result

def _07b_all_or_none(value: object, keys: tuple[str, ...], *, report: bool = False,
                      _safe_id=_07a_safe_id, _is_sha256_fn=_is_sha256) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(keys): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
    present = [item is not None for item in value.values()]
    if any(present) and not all(present): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
    if not any(present): return False
    for key, item in value.items():
        if key.endswith("sha256") and not _is_sha256_fn(item): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if key in {"inventory_file_count", "operation_count"} and (type(item) is not int or item < 1): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if key == "repair_eligible" and type(item) is not bool: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if key not in {"repair_eligible", "operation_count", "inventory_file_count"} and not key.endswith("sha256") and not _safe_id(item): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
    if report and value["decision"] not in {"repair", "pass"}: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
    return True

def _07b_empty_model_binding() -> dict[str, object]: return {"verified": False, "outcome_id": None, "outcome_sha256": None, "disposition": None, "failure_code": None, "assembled_page_id": None, "assembled_page_spec_sha256": None}
def _07b_model_binding(outcome: ModelRouteOutcome, canonical, _sha256_fn=_sha256) -> dict[str, object]: return {"verified": True, "outcome_id": outcome.outcome_id, "outcome_sha256": _sha256_fn(canonical(outcome)), "disposition": outcome.disposition, "failure_code": None if outcome.failure is None else outcome.failure.code, "assembled_page_id": outcome.artifacts["assembled_page_id"], "assembled_page_spec_sha256": outcome.artifacts["assembled_page_spec_sha256"]}

def _07b_binding_projection(value: object, keys: tuple[str, ...]) -> dict[str, object]:
    projected = dict(value)
    if set(projected) != set(keys):
        raise TierA07bGateDeliveryOutcomeError("live_replay_invalid")
    return projected


def _07b_validate_model_route_binding(value: object, _safe_id=_07a_safe_id,
                                       _keys=_07A_MODEL_ROUTE_BINDING_KEYS,
                                       _is_sha256_fn=_is_sha256) -> str:
    if not isinstance(value, Mapping) or set(value) != set(_keys) or type(value["verified"]) is not bool:
        raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
    if not value["verified"]:
        if any(item is not None for key, item in value.items() if key != "verified"):
            raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        return "unverified"
    if not _safe_id(value["outcome_id"]) or not _is_sha256_fn(value["outcome_sha256"]) or value["disposition"] not in {"scripted_fixture_assembled", "fail_closed"}:
        raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
    failure_code = value["failure_code"]
    assembled_id = value["assembled_page_id"]
    assembled_sha256 = value["assembled_page_spec_sha256"]
    if value["disposition"] == "scripted_fixture_assembled":
        if failure_code is not None or not _safe_id(assembled_id) or not _is_sha256_fn(assembled_sha256):
            raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        return "assembled"
    if not _safe_id(failure_code) or assembled_id is not None or assembled_sha256 is not None:
        raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
    return "fail_closed"

def _07b_report_binding(report: TierA07bFieldGateReport) -> dict[str, object]: return {"report_id": report.report_id, "report_sha256": report.sha256(), "authority_id": report.authority_id, "authority_sha256": report.authority_sha256, "rule_version": report.rule_version, "request_sha256": report.request_sha256, "field_gate_input_sha256": report.field_gate_input_sha256, "error_code": report.error_code, "decision": report.decision, "repair_eligible": report.repair_eligible}

_07B_STEP_MODEL = "model_route_live_validation"; _07B_STEP_G0 = "frozen_g0_fallback_validation"; _07B_STEP_PATH = "output_destination_preflight"; _07B_STEP_FIRST = "assembled_first_page_spec_binding"; _07B_STEP_GATE = "captured_field_gate_authority"; _07B_STEP_SCOPE = "repair_policy_scope_intersection"; _07B_STEP_PATCH = "one_repair_patch_application"; _07B_STEP_POST = "captured_field_gate_post_repair"; _07B_STEP_RENDER = "deterministic_render"; _07B_STEP_CONSISTENCY = "consistency_gate"; _07B_STEP_VIEW = "independent_requirement_view"; _07B_STEP_PLAN = "independent_acceptance_plan"; _07B_STEP_BINDING = "acceptance_binding_plan"; _07B_STEP_FIXTURE = "scripted_acceptance_fixture_validation"; _07B_STEP_EXECUTION = "scripted_acceptance_execution"; _07B_STEP_PACKAGE = "g1_result_package_v1"; _07B_STEP_DELIVERY = "g2_one_repair_delivery"; _07B_STEP_FALLBACK = "frozen_g0_fallback_delivery"
_07B_EARLY_FAILURE_STEPS = {
    "model_route_validation_failed": (),
    "frozen_g0_fallback_invalid": (_07B_STEP_MODEL,),
    "output_destination_invalid": (_07B_STEP_MODEL, _07B_STEP_G0),
}

def _07b_make_early_failure_outcome(
    outcome_type,
    *,
    case_id: str,
    failure_code: str,
    model_route_binding: Mapping[str, object],
    g0_binding: Mapping[str, object],
    fallback_binding: Mapping[str, object],
    _steps: Mapping[str, tuple[str, ...]] = _07B_EARLY_FAILURE_STEPS,
    _empty: object = _07b_empty,
    _empty_counts: object = _07b_empty_counts,
    _first_keys: tuple[str, ...] = _07B_FIRST_KEYS,
    _report_keys: tuple[str, ...] = _07B_REPORT_KEYS,
    _patch_keys: tuple[str, ...] = _07B_PATCH_KEYS,
):
    """Build the exact no-delivery envelope used by the first three run stages."""
    if failure_code not in _steps:
        raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
    return outcome_type.create(
        status="failed_delivery",
        case_id=case_id,
        completed_steps=_steps[failure_code],
        model_route_binding=dict(model_route_binding),
        g0_binding=dict(g0_binding),
        fallback_binding=dict(fallback_binding),
        first_candidate=_empty(_first_keys),
        repaired_candidate=_empty(_first_keys),
        repair_report=_empty(_report_keys),
        post_repair_gate=_empty(_report_keys),
        predicted_repair_scope=(),
        policy_allowed_scope=(),
        effective_repair_scope=(),
        repair_patch=_empty(_patch_keys),
        repair_limit=1,
        repair_attempted=0,
        repair_status="not_attempted",
        g1_package_purpose="not_generated",
        g2_action="failed_delivery",
        retry_performed=False,
        affected_gate_status="not_executed",
        final_gate_status="not_executed",
        consistency_status="not_executed",
        acceptance_status="not_executed",
        acceptance_counts=_empty_counts(),
        fallback_reason="not_attempted",
        fallback_attempted=False,
        fallback_succeeded=False,
        delivery_source="unavailable",
        final_package_schema_version=None,
        final_package_id=None,
        final_package_manifest_sha256=None,
        final_package_tree_sha256=None,
        final_package_file_count=None,
        fallback_delivery_report_id=None,
        fallback_delivery_report_sha256=None,
        failure_code=failure_code,
    )

_07B_BASE = (_07B_STEP_MODEL, _07B_STEP_G0, _07B_STEP_PATH)
_07B_SUCCESS = (*_07B_BASE, _07B_STEP_FIRST, _07B_STEP_GATE, _07B_STEP_SCOPE, _07B_STEP_PATCH, _07B_STEP_POST, _07B_STEP_RENDER, _07B_STEP_CONSISTENCY, _07B_STEP_VIEW, _07B_STEP_PLAN, _07B_STEP_BINDING, _07B_STEP_FIXTURE, _07B_STEP_EXECUTION, _07B_STEP_PACKAGE, _07B_STEP_DELIVERY)
_07B_PREFIX_INDEX = {"model_route_not_assembled": 3, "repair_receipt_invalid": 4, "field_gate_authority_failed": 4, "field_gate_receipt_mismatch": 5, "repair_not_eligible": 6, "repair_scope_not_authorized": 6, "repair_request_invalid": 6, "repair_patch_invalid": 6, "repair_post_gate_not_pass": 8, "render_failed": 8, "render_live_replay_invalid": 8, "consistency_failed": 9, "consistency_live_replay_invalid": 9, "requirement_view_failed": 10, "requirement_view_live_replay_invalid": 10, "acceptance_plan_failed": 11, "acceptance_plan_live_replay_invalid": 11, "acceptance_binding_failed": 12, "acceptance_binding_live_replay_invalid": 12, "acceptance_fixture_failed": 13, "acceptance_execution_failed": 14, "acceptance_execution_live_replay_invalid": 14, "consistency_or_acceptance_blocked": 15, "model_package_failed": 15, "model_package_live_replay_invalid": 15}


def _07b_reason_state(
    *, first: bool, report: bool, repaired: bool, post: str,
    pre: str, scope: str, attempted: int, repair_status: str,
    affected: str, final_gate: str, consistency: str, acceptance: str,
) -> dict[str, object]:
    return {
        "first": first, "report": report, "repaired": repaired, "post": post,
        "pre": pre, "scope": scope, "attempted": attempted,
        "repair_status": repair_status, "affected": affected,
        "final_gate": final_gate, "consistency": consistency,
        "acceptance": acceptance,
    }


_07B_REASON_STATE = {
    "model_route_not_assembled": _07b_reason_state(first=False, report=False, repaired=False, post="empty", pre="empty", scope="empty", attempted=0, repair_status="not_attempted", affected="not_executed", final_gate="not_executed", consistency="not_executed", acceptance="not_executed"),
    "repair_receipt_invalid": _07b_reason_state(first=True, report=False, repaired=False, post="empty", pre="empty", scope="empty", attempted=0, repair_status="not_attempted_receipt_invalid", affected="not_executed", final_gate="not_executed", consistency="not_executed", acceptance="not_executed"),
    "field_gate_authority_failed": _07b_reason_state(first=True, report=False, repaired=False, post="empty", pre="empty", scope="empty", attempted=0, repair_status="not_attempted_authority_failed", affected="not_executed", final_gate="not_executed", consistency="not_executed", acceptance="not_executed"),
    "field_gate_receipt_mismatch": _07b_reason_state(first=True, report=False, repaired=False, post="empty", pre="empty", scope="empty", attempted=0, repair_status="not_attempted_receipt_mismatch", affected="field_error", final_gate="not_executed", consistency="not_executed", acceptance="not_executed"),
    "repair_not_eligible": _07b_reason_state(first=True, report=True, repaired=False, post="empty", pre="pass", scope="empty", attempted=0, repair_status="not_attempted_not_eligible", affected="field_error", final_gate="not_executed", consistency="not_executed", acceptance="not_executed"),
    "repair_scope_not_authorized": _07b_reason_state(first=True, report=True, repaired=False, post="empty", pre="repair", scope="intersection_empty", attempted=0, repair_status="not_attempted_scope_rejected", affected="field_error", final_gate="not_executed", consistency="not_executed", acceptance="not_executed"),
    "repair_request_invalid": _07b_reason_state(first=True, report=True, repaired=False, post="empty", pre="repair", scope="effective", attempted=0, repair_status="not_attempted_patch_rejected", affected="field_error", final_gate="not_executed", consistency="not_executed", acceptance="not_executed"),
    "repair_patch_invalid": _07b_reason_state(first=True, report=True, repaired=False, post="empty", pre="repair", scope="effective", attempted=0, repair_status="not_attempted_patch_rejected", affected="field_error", final_gate="not_executed", consistency="not_executed", acceptance="not_executed"),
    "repair_post_gate_not_pass": _07b_reason_state(first=True, report=True, repaired=True, post="repair", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_post_gate_failed", affected="failed", final_gate="blocked", consistency="not_executed", acceptance="not_executed"),
    "render_failed": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="not_executed", acceptance="not_executed"),
    "render_live_replay_invalid": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="not_executed", acceptance="not_executed"),
    "consistency_failed": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="not_executed", acceptance="not_executed"),
    "consistency_live_replay_invalid": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="not_executed", acceptance="not_executed"),
    "requirement_view_failed": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="completed", acceptance="not_executed"),
    "requirement_view_live_replay_invalid": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="completed", acceptance="not_executed"),
    "acceptance_plan_failed": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="completed", acceptance="not_executed"),
    "acceptance_plan_live_replay_invalid": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="completed", acceptance="not_executed"),
    "acceptance_binding_failed": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="completed", acceptance="not_executed"),
    "acceptance_binding_live_replay_invalid": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="completed", acceptance="not_executed"),
    "acceptance_fixture_failed": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="completed", acceptance="not_executed"),
    "acceptance_execution_failed": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="completed", acceptance="not_executed"),
    "acceptance_execution_live_replay_invalid": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="completed", acceptance="not_executed"),
    "consistency_or_acceptance_blocked": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency="completed", acceptance="completed"),
    "model_package_failed": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="passed", consistency="pass", acceptance="pass"),
    "model_package_live_replay_invalid": _07b_reason_state(first=True, report=True, repaired=True, post="pass", pre="repair", scope="effective", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="passed", consistency="pass", acceptance="pass"),
}
def _07b_fallback_prefix(reason: str, _prefix_index=_07B_PREFIX_INDEX,
                          _success=_07B_SUCCESS) -> tuple[str, ...]:
    if reason not in _prefix_index: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
    return _success[:_prefix_index[reason]]

@dataclass(frozen=True)
class TierA07bGateDeliveryOutcome:
    status: str; case_id: str; completed_steps: tuple[str, ...]; model_route_binding: Mapping[str, object]; g0_binding: Mapping[str, object]; fallback_binding: Mapping[str, object]; first_candidate: Mapping[str, object]; repaired_candidate: Mapping[str, object]; repair_report: Mapping[str, object]; post_repair_gate: Mapping[str, object]; predicted_repair_scope: tuple[str, ...]; policy_allowed_scope: tuple[str, ...]; effective_repair_scope: tuple[str, ...]; repair_patch: Mapping[str, object]; repair_limit: int; repair_attempted: int; repair_status: str; g1_package_purpose: str; g2_action: str; retry_performed: bool; affected_gate_status: str; final_gate_status: str; consistency_status: str; acceptance_status: str; acceptance_counts: Mapping[str, int]; fallback_reason: str; fallback_attempted: bool; fallback_succeeded: bool; delivery_source: str; final_package_schema_version: str | None; final_package_id: str | None; final_package_manifest_sha256: str | None; final_package_tree_sha256: str | None; final_package_file_count: int | None; fallback_delivery_report_id: str | None; fallback_delivery_report_sha256: str | None; failure_code: str | None; outcome_id: str; schema_version: str = TIER_A_07B_GATE_DELIVERY_OUTCOME_SCHEMA_VERSION
    @classmethod
    def create(cls, _hash=_07b_hash,
               _id_prefix=_07B_OUTCOME_ID_PREFIX, **values: object) -> "TierA07bGateDeliveryOutcome":
        root = cls._root_values(**values); result = cls(**values, outcome_id=_id_prefix + _hash(root)[:20]); result.validate(); return result
    @staticmethod
    def _root_values(_schema_version=TIER_A_07B_GATE_DELIVERY_OUTCOME_SCHEMA_VERSION,
                     **values: object) -> dict[str, object]:
        root = dict(values)
        for key in ("completed_steps", "predicted_repair_scope", "policy_allowed_scope", "effective_repair_scope"): root[key] = list(root[key])
        for key in ("model_route_binding", "g0_binding", "fallback_binding", "first_candidate", "repaired_candidate", "repair_report", "post_repair_gate", "repair_patch", "acceptance_counts"): root[key] = dict(root[key])
        root["schema_version"] = _schema_version; return root
    def _root(self) -> dict[str, object]: return self._root_values(**{key: getattr(self, key) for key in self.__dataclass_fields__ if key not in {"outcome_id", "schema_version"}})

    def validate(self, _schema_version=TIER_A_07B_GATE_DELIVERY_OUTCOME_SCHEMA_VERSION,
                 _id_prefix=_07B_OUTCOME_ID_PREFIX, _safe_id=_07a_safe_id,
                 _validate_model_binding=_07b_validate_model_route_binding,
                 _all_or_none=_07b_all_or_none, _scope=_07b_scope,
                 _intersection=_07b_intersection, _validate_counts=_07b_validate_counts,
                 _empty_counts=_07b_empty_counts, _reason_state=_07B_REASON_STATE,
                 _prefix_index=_07B_PREFIX_INDEX, _fallback_prefix=_07b_fallback_prefix,
                 _success=_07B_SUCCESS, _failures=frozenset(_07B_FAILURES),
                 _step_first=_07B_STEP_FIRST, _step_scope=_07B_STEP_SCOPE,
                 _step_patch=_07B_STEP_PATCH, _step_consistency=_07B_STEP_CONSISTENCY,
                 _step_execution=_07B_STEP_EXECUTION, _step_fallback=_07B_STEP_FALLBACK,
                 _step_model=_07B_STEP_MODEL, _step_g0=_07B_STEP_G0,
                 _model_binding_keys=_07A_MODEL_ROUTE_BINDING_KEYS,
                 _g0_binding_keys=_07A_G0_BINDING_KEYS,
                 _fallback_binding_keys=_07A_FALLBACK_BINDING_KEYS,
                 _first_keys=_07B_FIRST_KEYS, _report_keys=_07B_REPORT_KEYS,
                 _patch_keys=_07B_PATCH_KEYS,
                 _result_package_schema_version=RESULT_PACKAGE_SCHEMA_VERSION,
                 _result_package_v2_schema_version=RESULT_PACKAGE_V2_SCHEMA_VERSION,
                 _hash=_07b_hash, _is_sha256_fn=_is_sha256) -> None:
        if self.schema_version != _schema_version or not _safe_id(self.case_id) or type(self.completed_steps) is not tuple or len(self.completed_steps) != len(set(self.completed_steps)) or any(not _safe_id(step) for step in self.completed_steps): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        model_binding_state = _validate_model_binding(
            self.model_route_binding, _keys=_model_binding_keys
        )
        g0 = _all_or_none(self.g0_binding, _g0_binding_keys); fallback = _all_or_none(self.fallback_binding, _fallback_binding_keys); first = _all_or_none(self.first_candidate, _first_keys); repaired = _all_or_none(self.repaired_candidate, _first_keys);
        if first and repaired and (self.first_candidate["page_id"] != self.repaired_candidate["page_id"] or self.first_candidate["traceability_sha256"] != self.repaired_candidate["traceability_sha256"]): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        report = _all_or_none(self.repair_report, _report_keys, report=True); post = _all_or_none(self.post_repair_gate, _report_keys, report=True); patch = _all_or_none(self.repair_patch, _patch_keys)
        predicted = _scope(self.predicted_repair_scope, "predicted_scope"); policy = _scope(self.policy_allowed_scope, "policy_scope"); effective = _scope(self.effective_repair_scope, "effective_scope")
        if effective != _intersection(predicted, policy): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        counts = _validate_counts(self.acceptance_counts)

        def late_state_is_empty() -> bool:
            return (
                not first and not repaired and not report and not post and not patch
                and predicted == () and policy == () and effective == ()
                and self.repair_attempted == 0 and self.repair_status == "not_attempted"
                and self.affected_gate_status == "not_executed"
                and self.final_gate_status == "not_executed"
                and self.consistency_status == "not_executed"
                and self.acceptance_status == "not_executed"
                and counts == _empty_counts()
            )

        def require_assembled_first_binding() -> None:
            if (
                model_binding_state != "assembled" or not first
                or self.first_candidate["page_id"]
                != self.model_route_binding["assembled_page_id"]
                or self.first_candidate["page_spec_sha256"]
                != self.model_route_binding["assembled_page_spec_sha256"]
            ):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")

        def validate_repair_route_prefix(reason: str) -> None:
            """Validate the exact reason-specific state before fallback."""
            state = _reason_state[reason]
            require_assembled_first_binding()
            if (
                first is not state["first"]
                or report is not state["report"]
                or repaired is not state["repaired"]
                or patch is not state["repaired"]
            ):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")

            post_mode = state["post"]
            if post is not (post_mode != "empty"):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if post_mode == "repair" and (
                self.post_repair_gate["decision"] != "repair"
                or self.post_repair_gate["repair_eligible"] is not True
            ):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if post_mode == "pass" and (
                self.post_repair_gate["decision"] != "pass"
                or self.post_repair_gate["repair_eligible"] is not False
            ):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")

            pre_mode = state["pre"]
            if pre_mode == "repair" and (
                self.repair_report["decision"] != "repair"
                or self.repair_report["repair_eligible"] is not True
            ):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if pre_mode == "pass" and (
                self.repair_report["decision"] != "pass"
                or self.repair_report["repair_eligible"] is not False
            ):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")

            scope_mode = state["scope"]
            if scope_mode == "empty" and (predicted or policy or effective):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if scope_mode == "intersection_empty" and (
                not predicted or effective
                or effective != _intersection(predicted, policy)
            ):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if scope_mode == "effective" and (
                not predicted or not policy or not effective
                or effective != _intersection(predicted, policy)
            ):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")

            if (
                self.repair_attempted != state["attempted"]
                or self.repair_status != state["repair_status"]
                or self.affected_gate_status != state["affected"]
                or self.final_gate_status != state["final_gate"]
            ):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")

            consistency_mode = state["consistency"]
            if consistency_mode == "not_executed":
                if self.consistency_status != "not_executed":
                    raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            elif consistency_mode == "pass":
                if self.consistency_status != "pass":
                    raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            elif self.consistency_status not in {"pass", "fail"}:
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")

            acceptance_mode = state["acceptance"]
            if acceptance_mode == "not_executed":
                if self.acceptance_status != "not_executed" or counts != _empty_counts():
                    raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            else:
                if self.acceptance_status not in {"pass", "blocked"} or counts["total"] < 1:
                    raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
                if self.acceptance_status == "pass" and (counts["fail"] or counts["unknown"]):
                    raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
                if self.acceptance_status == "blocked" and not (counts["fail"] or counts["unknown"]):
                    raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
                if acceptance_mode == "pass" and self.acceptance_status != "pass":
                    raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")

            if reason == "consistency_or_acceptance_blocked" and (
                self.consistency_status == "pass" and self.acceptance_status == "pass"
            ):
                raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")

        if _step_first not in self.completed_steps and first:
            raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if _step_scope not in self.completed_steps and report:
            raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if _step_patch not in self.completed_steps and (repaired or patch or post or self.repair_attempted):
            raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if _step_consistency not in self.completed_steps and self.consistency_status != "not_executed":
            raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if _step_execution not in self.completed_steps and (self.acceptance_status != "not_executed" or counts != _empty_counts()):
            raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if self.repair_status not in {"not_attempted", "not_attempted_receipt_invalid", "not_attempted_authority_failed", "not_attempted_receipt_mismatch", "not_attempted_not_eligible", "not_attempted_scope_rejected", "not_attempted_patch_rejected", "repair_completed_post_gate_failed", "repair_completed_final_gate_failed", "recovered_success"}: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if type(self.repair_limit) is not int or self.repair_limit != 1 or type(self.repair_attempted) is not int or self.repair_attempted not in {0, 1} or type(self.retry_performed) is not bool or self.retry_performed: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if self.g1_package_purpose not in {"not_generated", "evaluation_only"} or self.g2_action not in {"model_repair", "frozen_g0_fallback", "failed_delivery"} or self.affected_gate_status not in {"not_executed", "field_error", "passed", "failed"} or self.final_gate_status not in {"not_executed", "blocked", "passed"} or self.consistency_status not in {"not_executed", "pass", "fail"} or self.acceptance_status not in {"not_executed", "pass", "blocked"}: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if self.acceptance_status == "pass" and (counts["fail"] or counts["unknown"]):
            raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if self.acceptance_status == "blocked" and not (counts["fail"] or counts["unknown"]):
            raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if type(self.fallback_attempted) is not bool or type(self.fallback_succeeded) is not bool: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        package_values = (self.final_package_schema_version, self.final_package_id, self.final_package_manifest_sha256, self.final_package_tree_sha256, self.final_package_file_count); package_present = [item is not None for item in package_values]
        if any(package_present) and not all(package_present): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if all(package_present) and (self.final_package_schema_version not in {_result_package_schema_version, _result_package_v2_schema_version} or not _safe_id(self.final_package_id) or not _is_sha256_fn(self.final_package_manifest_sha256) or not _is_sha256_fn(self.final_package_tree_sha256) or type(self.final_package_file_count) is not int or self.final_package_file_count < 1): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        report_pair = (self.fallback_delivery_report_id, self.fallback_delivery_report_sha256); fallback_report = any(item is not None for item in report_pair)
        if fallback_report and (not all(item is not None for item in report_pair) or not _safe_id(self.fallback_delivery_report_id) or not _is_sha256_fn(self.fallback_delivery_report_sha256)): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if self.status == "recovered_success":
            if self.completed_steps != _success or not (self.model_route_binding["verified"] and g0 and fallback and first and repaired and report and post and patch) or self.repair_status != "recovered_success" or self.repair_attempted != 1 or self.repair_report["decision"] != "repair" or self.post_repair_gate["decision"] != "pass" or self.affected_gate_status != "passed" or self.final_gate_status != "passed" or self.consistency_status != "pass" or counts["total"] < 1 or counts["fail"] or counts["unknown"] or self.g1_package_purpose != "evaluation_only" or self.g2_action != "model_repair" or self.fallback_reason != "none" or self.fallback_attempted or self.fallback_succeeded or self.delivery_source != "model_repaired_v1" or not all(package_present) or self.final_package_schema_version != _result_package_schema_version or fallback_report or self.failure_code is not None: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            require_assembled_first_binding()
        elif self.status == "fallback_delivery":
            if self.fallback_reason not in _prefix_index or self.completed_steps != (*_fallback_prefix(self.fallback_reason), _step_fallback) or not (self.model_route_binding["verified"] and g0 and fallback) or not self.fallback_attempted or not self.fallback_succeeded or self.delivery_source != "g0_frozen_fallback" or self.g1_package_purpose != "not_generated" or self.g2_action != "frozen_g0_fallback" or not all(package_present) or self.final_package_schema_version != _result_package_v2_schema_version or not fallback_report or self.failure_code is not None: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if self.fallback_reason == "model_route_not_assembled":
                if model_binding_state != "fail_closed" or not late_state_is_empty(): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            else:
                validate_repair_route_prefix(self.fallback_reason)
        elif self.status == "failed_delivery":
            if self.failure_code not in _failures or self.delivery_source != "unavailable" or any(package_present) or fallback_report or self.fallback_succeeded or self.g1_package_purpose != "not_generated" or self.g2_action != "failed_delivery": raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            expected = {"model_route_validation_failed": (), "frozen_g0_fallback_invalid": (_step_model,), "output_destination_invalid": (_step_model, _step_g0)}.get(self.failure_code)
            if expected is not None and self.completed_steps != expected: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if expected is not None and (self.fallback_reason != "not_attempted" or self.fallback_attempted or self.repair_status != "not_attempted" or not late_state_is_empty()): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if self.failure_code == "model_route_validation_failed" and (model_binding_state != "unverified" or g0 or fallback): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if self.failure_code == "frozen_g0_fallback_invalid" and (model_binding_state == "unverified" or not g0 or fallback): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if self.failure_code == "output_destination_invalid" and (model_binding_state == "unverified" or not g0 or not fallback): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
            if expected is None:
                if not self.completed_steps or self.completed_steps[-1] == _step_fallback or self.fallback_reason not in _prefix_index or self.completed_steps != _fallback_prefix(self.fallback_reason) or not self.fallback_attempted:
                    raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
                if self.fallback_reason == "model_route_not_assembled":
                    if model_binding_state != "fail_closed" or not late_state_is_empty(): raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
                else:
                    validate_repair_route_prefix(self.fallback_reason)
        else: raise TierA07bGateDeliveryOutcomeError("outcome_state_invalid")
        if self.outcome_id != _id_prefix + _hash(self._root())[:20]: raise TierA07bGateDeliveryOutcomeError("outcome_identity_invalid")

    def to_dict(self) -> dict[str, object]: self.validate(); return {"outcome_id": self.outcome_id, **self._root()}
    def canonical_bytes(self, _canonical_json_bytes_fn=_canonical_json_bytes) -> bytes: return _canonical_json_bytes_fn(self.to_dict())
    @classmethod
    def from_dict(cls, payload: object) -> "TierA07bGateDeliveryOutcome":
        expected = set(cls.__dataclass_fields__); maps = {"model_route_binding", "g0_binding", "fallback_binding", "first_candidate", "repaired_candidate", "repair_report", "post_repair_gate", "repair_patch", "acceptance_counts"}; lists = {"completed_steps", "predicted_repair_scope", "policy_allowed_scope", "effective_repair_scope"}
        if not isinstance(payload, Mapping) or set(payload) != expected or any(not isinstance(payload[key], Mapping) for key in maps) or any(type(payload[key]) is not list for key in lists): raise TierA07bGateDeliveryOutcomeError("outcome_schema_invalid")
        try: result = cls(**{key: tuple(payload[key]) if key in lists else dict(payload[key]) if key in maps else payload[key] for key in expected})
        except (KeyError, TypeError, ValueError) as exc: raise TierA07bGateDeliveryOutcomeError("outcome_schema_invalid") from exc
        result.validate(); return result
    @classmethod
    def from_bytes(cls, raw: object, _load_canonical_json_fn=_load_canonical_json) -> "TierA07bGateDeliveryOutcome":
        if type(raw) is not bytes: raise TierA07bGateDeliveryOutcomeError("serialized_bytes_invalid")
        try: result = cls.from_dict(_load_canonical_json_fn(raw))
        except Exception as exc:
            if isinstance(exc, TierA07bGateDeliveryOutcomeError): raise
            raise TierA07bGateDeliveryOutcomeError("serialized_bytes_invalid") from exc
        if result.canonical_bytes() != raw: raise TierA07bGateDeliveryOutcomeError("serialized_bytes_invalid")
        return result


def _build_tier_a_07b_run(*, outcome_type, report_type, patch_type, model_type, model_validate, model_structural, model_bytes, model_binding_projection=_07b_model_binding, g0_projection=_07a_g0_binding, fallback_binding_projection=_07b_binding_projection, early_failure_factory=_07b_make_early_failure_outcome, empty_model_binding=_07b_empty_model_binding, empty_binding=_07b_empty, empty_counts=_07b_empty_counts, page_binding_projection=_07b_page_binding, report_binding_projection=_07b_report_binding, scope_intersection=_07b_intersection, fallback_prefix=_07b_fallback_prefix, fixture_authority, assembly_authority, field_gate_authority, renderer_type, render_result_type, render_projection, consistency_type, consistency_projection, requirement_projector, requirement_projection, plan_compiler, plan_projection, binding_compiler, binding_projection, acceptance_fixture_authority, acceptance_executor, browser_projection, count_projection, packager_type, package_type, package_projection, fallback_binding_authority, fallback_deliverer, load_fallback_report, fallback_projection, path_preflight, staging_path, cleanup, commit, rollback, _safe_id=_07a_safe_id, _g0_binding_keys=_07A_G0_BINDING_KEYS, _fallback_binding_keys=_07A_FALLBACK_BINDING_KEYS, _first_keys=_07B_FIRST_KEYS, _report_keys=_07B_REPORT_KEYS, _patch_keys=_07B_PATCH_KEYS, _success=_07B_SUCCESS, _step_fallback=_07B_STEP_FALLBACK):
    def live_first(outcome: object, fixture: object, context: object, guidance: object) -> PageSpec:
        if type(outcome) is not model_type: raise TierA07bGateDeliveryOutcomeError("model_route_invalid")
        live_fixture = fixture_authority(fixture); assembled = assembly_authority(live_fixture.raw_response, context, guidance)
        expected = {"raw_response_sha256": live_fixture.raw_response.sha256, "raw_response_byte_length": len(live_fixture.raw_response.raw_bytes), "model_semantic_candidate_sha256": assembled.candidate.sha256(), "assembled_page_id": assembled.page_spec.page_id, "assembly_report_id": assembled.report.report_id, "assembly_report_sha256": assembled.report.sha256(), "assembled_page_spec_sha256": assembled.report.assembled_page_spec_sha256}
        if outcome.disposition != "scripted_fixture_assembled" or dict(outcome.artifacts) != expected: raise TierA07bGateDeliveryOutcomeError("model_route_invalid")
        return assembled.page_spec
    def run(self, *, model_route_outcome: object, frozen_g0_reference: object, package: object, context: object, guidance: object, manifest: object, selected: object, local_request: object, pre_invocation_audit: object, local_qwen_preparation: object, execution_branch: object, scripted_local_fixture: object = None, case_id: str, fallback_record: object, fallback_snapshot_dir: object, render_output_dir: object, model_package_output_dir: object, fallback_output_dir: object, scripted_acceptance_fixture: object, field_gate_report: object, repair_patch: object) -> TierA07bGateDeliveryOutcome:
        if not _safe_id(case_id): raise TierA07bGateDeliveryOutcomeError("case_id_invalid")
        def make_early(**values: object) -> TierA07bGateDeliveryOutcome:
            try:
                return early_failure_factory(outcome_type, **values)
            except Exception as exc:
                raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
        model_binding = empty_model_binding(); g0_binding = empty_binding(_g0_binding_keys); fallback_binding = empty_binding(_fallback_binding_keys); first = empty_binding(_first_keys); repaired = empty_binding(_first_keys); report_binding = empty_binding(_report_keys); post_binding = empty_binding(_report_keys); patch_binding = empty_binding(_patch_keys); predicted: tuple[str, ...] = (); policy: tuple[str, ...] = (); effective: tuple[str, ...] = ()
        def make(status: str, steps: tuple[str, ...], **changes: object) -> TierA07bGateDeliveryOutcome:
            values = {"model_route_binding": model_binding, "g0_binding": g0_binding, "fallback_binding": fallback_binding, "first_candidate": first, "repaired_candidate": repaired, "repair_report": report_binding, "post_repair_gate": post_binding, "predicted_repair_scope": (), "policy_allowed_scope": (), "effective_repair_scope": (), "repair_patch": patch_binding, "repair_limit": 1, "repair_attempted": 0, "repair_status": "not_attempted", "g1_package_purpose": "not_generated", "g2_action": "failed_delivery", "retry_performed": False, "affected_gate_status": "not_executed", "final_gate_status": "not_executed", "consistency_status": "not_executed", "acceptance_status": "not_executed", "acceptance_counts": empty_counts(), "fallback_reason": "not_attempted", "fallback_attempted": False, "fallback_succeeded": False, "delivery_source": "unavailable", "final_package_schema_version": None, "final_package_id": None, "final_package_manifest_sha256": None, "final_package_tree_sha256": None, "final_package_file_count": None, "fallback_delivery_report_id": None, "fallback_delivery_report_sha256": None, "failure_code": None}
            values.update(changes); return outcome_type.create(status=status, case_id=case_id, completed_steps=steps, **values)
        def failed(code: str, steps: tuple[str, ...], **changes: object) -> TierA07bGateDeliveryOutcome: return make("failed_delivery", steps, failure_code=code, **changes)
        try:
            if type(model_route_outcome) is not model_type: raise TypeError("model outcome")
            model_validate(model_route_outcome, frozen_g0_reference=frozen_g0_reference, package=package, context=context, guidance=guidance, manifest=manifest, selected=selected, local_request=local_request, pre_invocation_audit=pre_invocation_audit, local_qwen_preparation=local_qwen_preparation, execution_branch=execution_branch, scripted_local_fixture=scripted_local_fixture)
            model_structural(model_route_outcome)
        except Exception:
            return make_early(
                case_id=case_id,
                failure_code="model_route_validation_failed",
                model_route_binding=model_binding, g0_binding=g0_binding,
                fallback_binding=fallback_binding,
            )
        try:
            model_binding = model_binding_projection(model_route_outcome, model_bytes)
            g0_binding = g0_projection(model_route_outcome.frozen_g0_reference)
        except Exception as exc:
            raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
        try: fallback_binding = fallback_binding_authority(case_id=case_id, record=fallback_record, snapshot_dir=fallback_snapshot_dir, reference=frozen_g0_reference, package=package, context=context, guidance=guidance)
        except Exception:
            return make_early(
                case_id=case_id,
                failure_code="frozen_g0_fallback_invalid",
                model_route_binding=model_binding, g0_binding=g0_binding,
                fallback_binding=fallback_binding,
            )
        try:
            fallback_binding = fallback_binding_projection(
                fallback_binding, _fallback_binding_keys
            )
        except Exception as exc:
            raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
        try: destinations = path_preflight(render_output_dir, model_package_output_dir, fallback_output_dir, package_dir=package.package_dir, snapshot_dir=fallback_snapshot_dir)
        except Exception:
            return make_early(
                case_id=case_id,
                failure_code="output_destination_invalid",
                model_route_binding=model_binding, g0_binding=g0_binding,
                fallback_binding=fallback_binding,
            )
        def fallback(reason: str, *, attempted: int = 0, repair_status: str = "not_attempted", affected: str = "not_executed", final_gate: str = "not_executed", consistency: str = "not_executed", acceptance: str = "not_executed", counts: Mapping[str, int] | None = None) -> TierA07bGateDeliveryOutcome:
            prefix = fallback_prefix(reason); acceptance_counts = empty_counts() if counts is None else dict(counts)
            try: stage = staging_path(destinations.fallback_output_dir, "tier-a-07b-fallback-staging")
            except Exception: return failed("fallback_delivery_failed", prefix, predicted_repair_scope=predicted, policy_allowed_scope=policy, effective_repair_scope=effective, repair_attempted=attempted, repair_status=repair_status, affected_gate_status=affected, final_gate_status=final_gate, consistency_status=consistency, acceptance_status=acceptance, acceptance_counts=acceptance_counts, fallback_reason=reason, fallback_attempted=True)
            receipt = None
            try: delivered_stage = fallback_deliverer(fallback_snapshot_dir, fallback_record, stage)
            except Exception:
                cleanup(stage); return failed("fallback_delivery_failed", prefix, predicted_repair_scope=predicted, policy_allowed_scope=policy, effective_repair_scope=effective, repair_attempted=attempted, repair_status=repair_status, affected_gate_status=affected, final_gate_status=final_gate, consistency_status=consistency, acceptance_status=acceptance, acceptance_counts=acceptance_counts, fallback_reason=reason, fallback_attempted=True)
            try:
                delivered_stage.validate_against(fallback_snapshot_dir, stage / "result_package", fallback_record); fallback_projection(delivered_stage); receipt = commit(stage, destinations.fallback_output_dir, destination_existed=destinations.fallback_existed); delivered = load_fallback_report(destinations.fallback_output_dir, fallback_snapshot_dir, fallback_record)
                if delivered.to_dict() != delivered_stage.to_dict(): raise ValueError("fallback changed")
            except Exception:
                cleanup(stage); rollback(receipt, destinations.fallback_output_dir); return failed("fallback_live_replay_invalid", prefix, predicted_repair_scope=predicted, policy_allowed_scope=policy, effective_repair_scope=effective, repair_attempted=attempted, repair_status=repair_status, affected_gate_status=affected, final_gate_status=final_gate, consistency_status=consistency, acceptance_status=acceptance, acceptance_counts=acceptance_counts, fallback_reason=reason, fallback_attempted=True)
            return make("fallback_delivery", (*prefix, _step_fallback), g1_package_purpose="not_generated", g2_action="frozen_g0_fallback", predicted_repair_scope=predicted, policy_allowed_scope=policy, effective_repair_scope=effective, repair_attempted=attempted, repair_status=repair_status, affected_gate_status=affected, final_gate_status=final_gate, consistency_status=consistency, acceptance_status=acceptance, acceptance_counts=acceptance_counts, fallback_reason=reason, fallback_attempted=True, fallback_succeeded=True, delivery_source="g0_frozen_fallback", final_package_schema_version=delivered.package_schema_version, final_package_id=delivered.package_id, final_package_manifest_sha256=fallback_binding["package_manifest_sha256"], final_package_tree_sha256=delivered.delivered_package_tree_sha256, final_package_file_count=len(delivered.delivered_inventory), fallback_delivery_report_id=delivered.report_id, fallback_delivery_report_sha256=delivered.report_sha256)
        if model_binding["disposition"] == "fail_closed":
            return fallback("model_route_not_assembled")
        try:
            first_page = live_first(model_route_outcome, scripted_local_fixture, context, guidance)
            if first_page.page_id != frozen_g0_reference.page_id: raise ValueError("same case")
            first = page_binding_projection(first_page)
        except Exception as exc:
            raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
        try: supplied = report_type.from_bytes(field_gate_report)
        except Exception: return fallback("repair_receipt_invalid", repair_status="not_attempted_receipt_invalid")
        try: authority = field_gate_authority(case_id=case_id, page_spec=first_page, local_request=local_request)
        except Exception: return fallback("field_gate_authority_failed", repair_status="not_attempted_authority_failed")
        try:
            if supplied.canonical_bytes() != authority.canonical_bytes(): raise ValueError("receipt mismatch")
            authority.validate_against(case_id, first_page, authority.request_sha256); report_binding = report_binding_projection(authority)
        except Exception: return fallback("field_gate_receipt_mismatch", repair_status="not_attempted_receipt_mismatch", affected="field_error")
        predicted, policy = authority.predicted_repair_scope, authority.policy_allowed_scope; effective = scope_intersection(predicted, policy)
        if authority.decision != "repair" or not authority.repair_eligible: return fallback("repair_not_eligible", repair_status="not_attempted_not_eligible", affected="field_error")
        if not effective: return fallback("repair_scope_not_authorized", repair_status="not_attempted_scope_rejected", affected="field_error")
        try: patch = patch_type.from_bytes(repair_patch)
        except Exception: return fallback("repair_request_invalid", repair_status="not_attempted_patch_rejected", affected="field_error")
        try:
            repaired_page = patch.apply(authority, first_page, effective); repaired = page_binding_projection(repaired_page); patch_binding = {"patch_id": patch.patch_id, "patch_sha256": patch.sha256(), "operation_count": len(patch.operations)}
        except Exception: return fallback("repair_patch_invalid", repair_status="not_attempted_patch_rejected", affected="field_error")
        try:
            post = field_gate_authority(case_id=case_id, page_spec=repaired_page, local_request=local_request); post_binding = report_binding_projection(post)
            if post.decision != "pass" or (post.authority_id, post.authority_sha256, post.rule_version, post.request_sha256) != (authority.authority_id, authority.authority_sha256, authority.rule_version, authority.request_sha256): raise ValueError("post gate")
        except Exception: return fallback("repair_post_gate_not_pass", attempted=1, repair_status="repair_completed_post_gate_failed", affected="failed", final_gate="blocked")

        try: render_stage = staging_path(destinations.render_output_dir, "tier-a-07b-render-staging")
        except Exception: return fallback("render_live_replay_invalid", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked")
        render_receipt = None
        try: staged_render = renderer_type().render(repaired_page, render_stage)
        except Exception:
            cleanup(render_stage); return fallback("render_failed", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked")
        try:
            render_projection(repaired_page, staged_render); render_receipt = commit(render_stage, destinations.render_output_dir, destination_existed=destinations.render_existed)
            render_result = render_result_type(page_id=repaired_page.page_id, output_dir=destinations.render_output_dir, index_html=destinations.render_output_dir / "index.html", styles_css=destinations.render_output_dir / "styles.css", app_js=destinations.render_output_dir / "app.js", render_manifest=destinations.render_output_dir / "render_manifest.json")
            render_projection(repaired_page, render_result)
        except Exception:
            cleanup(render_stage); rollback(render_receipt, destinations.render_output_dir); return fallback("render_live_replay_invalid", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked")
        try: consistency = consistency_type().check(repaired_page, render_result)
        except Exception: return fallback("consistency_failed", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked")
        try: consistency_projection(consistency); consistency_status = "pass" if consistency.passed else "fail"
        except Exception: return fallback("consistency_live_replay_invalid", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked")
        try: view = requirement_projector(context)
        except Exception: return fallback("requirement_view_failed", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency=consistency_status)
        try: requirement_projection(view)
        except Exception: return fallback("requirement_view_live_replay_invalid", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency=consistency_status)
        try: plan = plan_compiler(view)
        except Exception: return fallback("acceptance_plan_failed", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency=consistency_status)
        try:
            plan.validate_against(view); plan_projection(plan)
        except Exception: return fallback("acceptance_plan_live_replay_invalid", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency=consistency_status)
        try: binding = binding_compiler(view, plan, repaired_page, render_result)
        except Exception: return fallback("acceptance_binding_failed", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency=consistency_status)
        try:
            binding.validate_against(view, plan, repaired_page, render_result); binding_projection(binding)
        except Exception: return fallback("acceptance_binding_live_replay_invalid", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency=consistency_status)
        try: fixture = acceptance_fixture_authority(scripted_acceptance_fixture)
        except Exception: return fallback("acceptance_fixture_failed", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency=consistency_status)
        try: browser = acceptance_executor(fixture, binding)
        except Exception: return fallback("acceptance_execution_failed", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency=consistency_status)
        try:
            browser.validate_against(binding); browser_projection(browser); counts = count_projection(browser); acceptance_status = "blocked" if counts["fail"] or counts["unknown"] else "pass"
        except Exception: return fallback("acceptance_execution_live_replay_invalid", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency=consistency_status)
        if consistency_status != "pass" or acceptance_status != "pass": return fallback("consistency_or_acceptance_blocked", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="blocked", consistency=consistency_status, acceptance=acceptance_status, counts=counts)
        try: package_stage = staging_path(destinations.model_package_output_dir, "tier-a-07b-model-package-staging")
        except Exception: return fallback("model_package_live_replay_invalid", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="passed", consistency=consistency_status, acceptance=acceptance_status, counts=counts)
        package_receipt = None
        try: staged_package = packager_type().package(context, repaired_page, render_result, consistency, package_stage)
        except Exception:
            cleanup(package_stage); return fallback("model_package_failed", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="passed", consistency=consistency_status, acceptance=acceptance_status, counts=counts)
        try:
            staged_projection = package_projection(staged_package, package_stage); package_receipt = commit(package_stage, destinations.model_package_output_dir, destination_existed=destinations.model_package_existed)
            result_package = package_type(package_id=staged_package.package_id, page_id=staged_package.page_id, package_dir=destinations.model_package_output_dir, entrypoint=staged_package.entrypoint, result_summary=staged_package.result_summary, package_manifest=staged_package.package_manifest)
            final_projection = package_projection(result_package, destinations.model_package_output_dir)
            if staged_projection != final_projection: raise ValueError("package changed")
        except Exception:
            cleanup(package_stage); rollback(package_receipt, destinations.model_package_output_dir); return fallback("model_package_live_replay_invalid", attempted=1, repair_status="repair_completed_final_gate_failed", affected="passed", final_gate="passed", consistency=consistency_status, acceptance=acceptance_status, counts=counts)
        return make("recovered_success", _success, g1_package_purpose="evaluation_only", g2_action="model_repair", repaired_candidate=repaired, predicted_repair_scope=predicted, policy_allowed_scope=policy, effective_repair_scope=effective, repair_patch=patch_binding, repair_attempted=1, repair_status="recovered_success", affected_gate_status="passed", final_gate_status="passed", consistency_status=consistency_status, acceptance_status=acceptance_status, acceptance_counts=counts, fallback_reason="none", delivery_source="model_repaired_v1", final_package_schema_version=final_projection["model_package_schema_version"], final_package_id=final_projection["model_package_id"], final_package_manifest_sha256=final_projection["model_package_manifest_sha256"], final_package_tree_sha256=final_projection["model_package_tree_sha256"], final_package_file_count=final_projection["model_package_file_count"])
    return run

class TierA07bOneRepairOrchestrator:
    """Captured local deterministic/synthetic one-repair route; no model call."""
    __slots__ = ()
    run = _build_tier_a_07b_run(outcome_type=TierA07bGateDeliveryOutcome, report_type=TierA07bFieldGateReport, patch_type=TierA07bRepairPatch, model_type=ModelRouteOutcome, model_validate=ModelRouteOutcome.validate_against, model_structural=ModelRouteOutcome.validate, model_bytes=ModelRouteOutcome.canonical_bytes, model_binding_projection=_07b_model_binding, g0_projection=_07a_g0_binding, fallback_binding_projection=_07b_binding_projection, early_failure_factory=_07b_make_early_failure_outcome, empty_model_binding=_07b_empty_model_binding, empty_binding=_07b_empty, empty_counts=_07b_empty_counts, page_binding_projection=_07b_page_binding, report_binding_projection=_07b_report_binding, scope_intersection=_07b_intersection, fallback_prefix=_07b_fallback_prefix, fixture_authority=_FIXED_LIVE_FIXTURE_AUTHORITY, assembly_authority=_FIXED_CANONICAL_ASSEMBLY_AUTHORITY, field_gate_authority=_FIXED_07B_FIELD_GATE_AUTHORITY, renderer_type=DeterministicPageRenderer, render_result_type=RenderResult, render_projection=_FIXED_07A_RENDER_PROJECTION, consistency_type=MinimalConsistencyChecker, consistency_projection=_FIXED_07A_CONSISTENCY_PROJECTION, requirement_projector=project_requirement_view, requirement_projection=_FIXED_07A_REQUIREMENT_PROJECTION, plan_compiler=compile_acceptance_plan, plan_projection=_FIXED_07A_PLAN_PROJECTION, binding_compiler=compile_acceptance_binding, binding_projection=_FIXED_07A_BINDING_PROJECTION, acceptance_fixture_authority=_FIXED_ACCEPTANCE_FIXTURE_AUTHORITY, acceptance_executor=_FIXED_ACCEPTANCE_EXECUTION_AUTHORITY, browser_projection=_FIXED_07A_BROWSER_PROJECTION, count_projection=_FIXED_07A_ACCEPTANCE_COUNTS, packager_type=DeterministicResultPackager, package_type=ResultPackage, package_projection=_FIXED_07A_PACKAGE_PROJECTION, fallback_binding_authority=_FIXED_07A_FALLBACK_BINDING_AUTHORITY, fallback_deliverer=deliver_frozen_g0_fallback, load_fallback_report=_FIXED_07A_LOAD_FALLBACK_REPORT, fallback_projection=_FIXED_07A_FALLBACK_REPORT_PROJECTION, path_preflight=_FIXED_07A_PATH_PREFLIGHT, staging_path=_FIXED_07A_STAGING_PATH, cleanup=_FIXED_07A_CLEANUP_DIRECTORY, commit=_FIXED_07A_COMMIT_STAGING, rollback=_FIXED_07A_ROLLBACK_COMMIT)


def _build_tier_a_07b_validate_against(*, runner, report_type, patch_type, model_type, model_validate, model_structural, model_bytes, model_binding_projection, g0_projection, fallback_binding_projection, early_failure_factory, empty_model_binding, empty_binding, report_binding_projection, fixture_authority, assembly_authority, field_gate_authority, render_result_type, consistency_type, package_type, package_projection, package_live_binding, fallback_binding_authority, fallback_loader, path_preflight, path_replay, staging_path, cleanup, _safe_id=_07a_safe_id, _g0_binding_keys=_07A_G0_BINDING_KEYS, _fallback_binding_keys=_07A_FALLBACK_BINDING_KEYS, _early_failure_steps=frozenset(_07B_EARLY_FAILURE_STEPS), _step_render=_07B_STEP_RENDER):
    def first_page(outcome: object, fixture: object, context: object, guidance: object) -> PageSpec:
        if type(outcome) is not model_type:
            raise TypeError("model route type")
        live_fixture = fixture_authority(fixture)
        return assembly_authority(live_fixture.raw_response, context, guidance).page_spec

    def validate_against(self: TierA07bGateDeliveryOutcome, **args: object) -> None:
        self.validate()

        def make_early_failure(**values: object) -> TierA07bGateDeliveryOutcome:
            try:
                return early_failure_factory(type(self), **values)
            except Exception as exc:
                raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc

        def replay_early_failure_envelope() -> TierA07bGateDeliveryOutcome | None:
            case_id = args["case_id"]
            if not _safe_id(case_id):
                raise ValueError("case_id is invalid")
            model_binding = empty_model_binding()
            g0_binding = empty_binding(_g0_binding_keys)
            fallback_binding = empty_binding(_fallback_binding_keys)
            try:
                outcome = args["model_route_outcome"]
                if type(outcome) is not model_type:
                    raise TypeError("model outcome")
                model_validate(
                    outcome,
                    frozen_g0_reference=args["frozen_g0_reference"],
                    package=args["package"], context=args["context"],
                    guidance=args["guidance"], manifest=args["manifest"],
                    selected=args["selected"], local_request=args["local_request"],
                    pre_invocation_audit=args["pre_invocation_audit"],
                    local_qwen_preparation=args["local_qwen_preparation"],
                    execution_branch=args["execution_branch"],
                    scripted_local_fixture=args.get("scripted_local_fixture"),
                )
                model_structural(outcome)
            except Exception:
                try:
                    return make_early_failure(
                        case_id=case_id,
                        failure_code="model_route_validation_failed",
                        model_route_binding=model_binding, g0_binding=g0_binding,
                        fallback_binding=fallback_binding,
                    )
                except Exception as exc:
                    raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
            model_binding = model_binding_projection(outcome, model_bytes)
            g0_binding = g0_projection(outcome.frozen_g0_reference)
            try:
                fallback_binding = fallback_binding_authority(
                    case_id=case_id, record=args["fallback_record"],
                    snapshot_dir=args["fallback_snapshot_dir"],
                    reference=args["frozen_g0_reference"],
                    package=args["package"], context=args["context"],
                    guidance=args["guidance"],
                )
            except Exception:
                try:
                    return make_early_failure(
                        case_id=case_id,
                        failure_code="frozen_g0_fallback_invalid",
                        model_route_binding=model_binding, g0_binding=g0_binding,
                        fallback_binding=fallback_binding,
                    )
                except Exception as exc:
                    raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
            fallback_binding = fallback_binding_projection(
                fallback_binding, _fallback_binding_keys
            )
            try:
                path_preflight(
                    args["render_output_dir"], args["model_package_output_dir"],
                    args["fallback_output_dir"],
                    package_dir=args["package"].package_dir,
                    snapshot_dir=args["fallback_snapshot_dir"],
                )
            except Exception:
                try:
                    return make_early_failure(
                        case_id=case_id,
                        failure_code="output_destination_invalid",
                        model_route_binding=model_binding, g0_binding=g0_binding,
                        fallback_binding=fallback_binding,
                    )
                except Exception as exc:
                    raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
            return None

        if self.status == "failed_delivery" and self.failure_code in _early_failure_steps:
            try:
                replay = replay_early_failure_envelope()
                if replay is None or replay.canonical_bytes() != self.canonical_bytes():
                    raise ValueError("early failure replay mismatch")
            except Exception as exc:
                raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
            return

        # Replay in the exact run order.  A later path failure is valid only
        # when the current model-route and frozen-G0 stages are still valid.
        try:
            model_validate(
                args["model_route_outcome"],
                frozen_g0_reference=args["frozen_g0_reference"],
                package=args["package"], context=args["context"],
                guidance=args["guidance"], manifest=args["manifest"],
                selected=args["selected"], local_request=args["local_request"],
                pre_invocation_audit=args["pre_invocation_audit"],
                local_qwen_preparation=args["local_qwen_preparation"],
                execution_branch=args["execution_branch"],
                scripted_local_fixture=args.get("scripted_local_fixture"),
            )
            model_structural(args["model_route_outcome"])
        except Exception as exc:
            raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
        try:
            actual_model = model_binding_projection(args["model_route_outcome"], model_bytes)
            actual_g0 = g0_projection(args["model_route_outcome"].frozen_g0_reference)
            if dict(self.model_route_binding) != actual_model or dict(self.g0_binding) != actual_g0:
                raise ValueError("model route binding mismatch")
        except Exception as exc:
            raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc

        try:
            actual_fallback = fallback_binding_authority(
                case_id=args["case_id"], record=args["fallback_record"],
                snapshot_dir=args["fallback_snapshot_dir"],
                reference=args["frozen_g0_reference"], package=args["package"],
                context=args["context"], guidance=args["guidance"],
            )
        except Exception as exc:
            raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
        try:
            actual_fallback = fallback_binding_projection(
                actual_fallback, _fallback_binding_keys
            )
        except Exception as exc:
            raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
        if dict(self.fallback_binding) != actual_fallback:
            raise TierA07bGateDeliveryOutcomeError("live_replay_invalid")

        render_expected = _step_render in self.completed_steps and self.failure_code is None
        try:
            path_replay(
                args["render_output_dir"], args["model_package_output_dir"],
                args["fallback_output_dir"], package_dir=args["package"].package_dir,
                snapshot_dir=args["fallback_snapshot_dir"],
                render_expected=render_expected,
                model_package_expected=self.status == "recovered_success",
                fallback_expected=self.status == "fallback_delivery",
            )
        except Exception as exc:
            raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc

        if self.repair_report["report_id"] is not None:
            try:
                receipt = report_type.from_bytes(args["field_gate_report"])
                first = first_page(
                    args["model_route_outcome"], args.get("scripted_local_fixture"),
                    args["context"], args["guidance"],
                )
                authority = field_gate_authority(
                    case_id=args["case_id"], page_spec=first,
                    local_request=args["local_request"],
                )
                if receipt.canonical_bytes() != authority.canonical_bytes() or dict(self.repair_report) != report_binding_projection(authority):
                    raise ValueError("pre repair receipt mismatch")
                if self.status == "recovered_success":
                    repaired = patch_type.from_bytes(args["repair_patch"]).apply(
                        authority, first, self.effective_repair_scope
                    )
                    post = field_gate_authority(
                        case_id=args["case_id"], page_spec=repaired,
                        local_request=args["local_request"],
                    )
                    if post.decision != "pass" or dict(self.post_repair_gate) != report_binding_projection(post):
                        raise ValueError("post repair receipt mismatch")
            except Exception as exc:
                raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc

        if self.status == "recovered_success":
            try:
                package_dir = args["model_package_output_dir"]
                package = package_type(
                    package_id=self.final_package_id,
                    page_id=self.repaired_candidate["page_id"],
                    package_dir=package_dir, entrypoint="page/index.html",
                    result_summary="result_summary.json",
                    package_manifest="package_manifest.json",
                )
                projection = package_projection(package, package_dir)
                if projection["model_package_id"] != self.final_package_id or projection["model_package_manifest_sha256"] != self.final_package_manifest_sha256 or projection["model_package_tree_sha256"] != self.final_package_tree_sha256 or projection["model_package_file_count"] != self.final_package_file_count:
                    raise ValueError("package mismatch")
                first = first_page(args["model_route_outcome"], args.get("scripted_local_fixture"), args["context"], args["guidance"])
                receipt = field_gate_authority(case_id=args["case_id"], page_spec=first, local_request=args["local_request"])
                repaired = patch_type.from_bytes(args["repair_patch"]).apply(receipt, first, self.effective_repair_scope)
                render_dir = args["render_output_dir"]
                render = render_result_type(
                    page_id=repaired.page_id, output_dir=render_dir,
                    index_html=render_dir / "index.html", styles_css=render_dir / "styles.css",
                    app_js=render_dir / "app.js", render_manifest=render_dir / "render_manifest.json",
                )
                consistency = consistency_type().check(repaired, render)
                package_live_binding(
                    context=args["context"], page_spec=repaired,
                    render_result=render, consistency_report=consistency,
                    supplied_package=package, supplied_dir=package_dir,
                )
            except Exception as exc:
                raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
        elif self.status == "fallback_delivery":
            try:
                fallback = fallback_loader(
                    args["fallback_output_dir"], args["fallback_snapshot_dir"],
                    args["fallback_record"],
                )
                if fallback.report_id != self.fallback_delivery_report_id or fallback.report_sha256 != self.fallback_delivery_report_sha256 or fallback.package_id != self.final_package_id or fallback.delivered_package_tree_sha256 != self.final_package_tree_sha256:
                    raise ValueError("fallback mismatch")
            except Exception as exc:
                raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc

        replay_root = None
        try:
            replay_root = staging_path(args["model_package_output_dir"], "tier-a-07b-replay")
            replay_root.mkdir()
            replay_args = dict(args)
            replay_args.update({
                "render_output_dir": replay_root / "render",
                "model_package_output_dir": replay_root / "model-package",
                "fallback_output_dir": replay_root / "fallback",
            })
            replay = runner(**replay_args)
            if replay.to_dict() != self.to_dict():
                raise ValueError("canonical replay mismatch")
        except Exception as exc:
            raise TierA07bGateDeliveryOutcomeError("live_replay_invalid") from exc
        finally:
            if replay_root is not None:
                cleanup(replay_root)
    return validate_against

TierA07bGateDeliveryOutcome.validate_against = _build_tier_a_07b_validate_against(runner=TierA07bOneRepairOrchestrator().run, report_type=TierA07bFieldGateReport, patch_type=TierA07bRepairPatch, model_type=ModelRouteOutcome, model_validate=ModelRouteOutcome.validate_against, model_structural=ModelRouteOutcome.validate, model_bytes=ModelRouteOutcome.canonical_bytes, model_binding_projection=_07b_model_binding, g0_projection=_07a_g0_binding, fallback_binding_projection=_07b_binding_projection, early_failure_factory=_07b_make_early_failure_outcome, empty_model_binding=_07b_empty_model_binding, empty_binding=_07b_empty, report_binding_projection=_07b_report_binding, fallback_binding_authority=_FIXED_07A_FALLBACK_BINDING_AUTHORITY, fixture_authority=_FIXED_LIVE_FIXTURE_AUTHORITY, assembly_authority=_FIXED_CANONICAL_ASSEMBLY_AUTHORITY, field_gate_authority=_FIXED_07B_FIELD_GATE_AUTHORITY, render_result_type=RenderResult, consistency_type=MinimalConsistencyChecker, package_type=ResultPackage, package_projection=_FIXED_07A_PACKAGE_PROJECTION, package_live_binding=_FIXED_07A_PACKAGE_LIVE_BINDING, fallback_loader=_FIXED_07A_LOAD_FALLBACK_REPORT, path_preflight=_FIXED_07A_PATH_PREFLIGHT, path_replay=_FIXED_07A_PATH_REPLAY, staging_path=_FIXED_07A_STAGING_PATH, cleanup=_FIXED_07A_CLEANUP_DIRECTORY)

def _07b_live_first_page_spec(model_route_outcome: object, scripted_local_fixture: object, context: object, guidance: object) -> PageSpec:
    return _FIXED_CANONICAL_ASSEMBLY_AUTHORITY(_FIXED_LIVE_FIXTURE_AUTHORITY(scripted_local_fixture).raw_response, context, guidance).page_spec

def validate_serialized_tier_a_07b_gate_delivery_outcome(payload: object) -> TierA07bGateDeliveryOutcome:
    return TierA07bGateDeliveryOutcome.from_bytes(payload) if type(payload) is bytes else TierA07bGateDeliveryOutcome.from_dict(payload)
