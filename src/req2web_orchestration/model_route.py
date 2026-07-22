"""Tier A local-only model-route control flow for the approved D17 path 3 boundary.

The module verifies the same-case frozen G0 package before the complete local
D17 preparation chain. The real Local Qwen gate remains blocked by Tier B. Its
only parser/assembler seam is a module-owned registry of exact scripted bytes;
no scripted fixture is a Qwen, Provider, G1/G2, pilot, or delivery result.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from req2web_agent import AgentContextBundle
from req2web_evaluation.frozen_g0_reference import (
    FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION,
    FrozenG0InventoryEntry,
    FrozenG0PackageReference,
    FrozenG0PackageReferenceError,
)
from req2web_generation import RetrievalEnhancedResultPackage, RetrievalGuidance
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

    def validate(self) -> None:
        if (
            not _is_text(self.fixture_key)
            or not _is_sha256(self.raw_response_sha256)
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
    )
    registry_schema_version = SCRIPTED_FIXTURE_REGISTRY_SCHEMA_VERSION
    expected_sha256 = "360dc05648679f8cb1fe2b81bcb8d83444ec7de7a19ac1d1062bde25efb1c982"
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

        def validate(self) -> None:
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

        def validate(self) -> None:
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
                or not _is_sha256(self.raw_response_sha256)
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

    def _validate_artifact_values(self) -> None:
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
            if self.artifacts[key] is not None and not _is_sha256(self.artifacts[key]):
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
