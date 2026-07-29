"""Deterministic Tier A path-3 local serializer and request artifacts."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping
from req2web_agent.schema import AgentContextBundle
from req2web_provider.d17_input_view import (
    D17Path3InputSelectionRecord,
    D17Path3ProviderVisibleInputView,
    D17Path3SelectedInput,
)
from req2web_provider.d17_manifest import (
    D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
    D17Path3TierAManifest,
)
D17_PATH3_INPUT_VIEW_ARTIFACT_SCHEMA_VERSION = 'req2web.d17.path3.input_view_artifact.v1'
D17_PATH3_PROMPT_ARTIFACT_SCHEMA_VERSION = 'req2web.d17.path3.prompt_artifact.v1'
D17_PATH3_CONFIG_ARTIFACT_SCHEMA_VERSION = 'req2web.d17.path3.config_artifact.v1'
D17_PATH3_LOCAL_REQUEST_ARTIFACT_SCHEMA_VERSION = 'req2web.d17.path3.local_request_artifact.v1'
D17_PATH3_SERIALIZER_ERROR_ENVELOPE_SCHEMA_VERSION = 'req2web.d17.path3.serializer.error.v1'
SEMANTIC_CANDIDATE_SCHEMA_VERSION = 'req2web.provider.semantic_candidate.v1'
_AUTHORIZATION = 'tier_a_local_implementation_only'
_PATH_3 = 'path_3'
_TIER_B_UNAPPROVED = 'tier_b_unapproved'
_PROMPT_TEXT = """Return exactly one UTF-8 JSON object and nothing else. Do not emit Markdown, code fences, prose, or a wrapper such as semantic_candidate. The root object itself must conform to req2web.provider.semantic_candidate.v1.

The root object must contain exactly these keys and no others:
"schema_version", "title", "layout", "sections", "components", "states", "interactions", "constraints", "acceptance_checks", "use_case_mappings", "claimed_attribution_edges".
Set "schema_version" to "req2web.provider.semantic_candidate.v1".

Every nested object must also use its exact key set:
- layout: "pattern", "section_stable_ids"
- each sections item: "stable_id", "title", "purpose", "component_stable_ids", "use_case_ids"
- each components item: "stable_id", "section_stable_id", "component_type", "label", "purpose"
- each states item: "stable_id", "name", "description", "visible_component_stable_ids"
- each interactions item: "stable_id", "trigger_component_stable_id", "source_state_stable_id", "action", "target_state_stable_id", "user_feedback", "use_case_ids"
- each constraints item: "stable_id", "description"
- each acceptance_checks item: "stable_id", "description", "use_case_ids", "state_stable_id"
- each use_case_mappings item: "use_case_id", "section_stable_ids", "component_stable_ids", "interaction_stable_ids"
- each claimed_attribution_edges item: "candidate_entity_stable_id", "source_kind", "source_id"

For this Path 3 run, output "constraints": [] because supplied canonical constraints are added locally, and output "claimed_attribution_edges": [] because evidence or provenance claims are prohibited. Use every supplied use_case_id exactly once in use_case_mappings and use no other use-case ID; there must be 2 to 4 mappings. sections, components, states, interactions, and acceptance_checks must be non-empty. Stable IDs must match ^[a-z][a-z0-9-]{0,95}$ and be globally unique.

All references must be complete and consistent: layout.section_stable_ids must equal all section IDs; each section must own exactly its listed components; each section and interaction must reference supplied use cases; every interaction must reference existing component and state IDs; each use-case mapping must list exactly the sections carrying that use case, all components owned by those sections, and all interactions carrying that use case; acceptance_checks must collectively cover every supplied use case, and each description must contain at least 8 characters.
For every interaction, source_state_stable_id and target_state_stable_id must be different; self-loop interactions are invalid. If an action conceptually stays in the same visual area, represent distinct before and after semantic states.

Do not output authoritative PageSpec or local-only fields including page_id, summary, target_device, page_type, use_cases, traceability, evidence, evidence_inventory, source_context_schema_version, agent_context, retrieval_guidance, retrieval_influence, or result_package. Use only the supplied input view to propose page semantics. Do not rerun requirement understanding or retrieval. Do not claim retrieval or evidence use, provenance, source locations, or authority beyond the supplied input view."""
_CONFIG_CONTRACT = {
    'semantic_candidate_schema_version': SEMANTIC_CANDIDATE_SCHEMA_VERSION,
    'response_format': 'strict_json_object_only',
    'authoritative_input_policy': 'supplied_input_view_only',
    'requirement_understanding': 'not_reexecuted',
    'retrieval': 'not_reexecuted',
    'evidence_or_provenance_claims': 'prohibited'
}
_TIER_B_RUNTIME_STATUS = {
    'runtime_model_revision': _TIER_B_UNAPPROVED,
    'precision_or_quantization': _TIER_B_UNAPPROVED,
    'context_decode_seed': _TIER_B_UNAPPROVED,
    'timeout_retry_network': _TIER_B_UNAPPROVED,
    'gpu_budget': _TIER_B_UNAPPROVED
}
_SAFE = {
    'serializer_schema_invalid': 'D17 serializer schema is invalid.',
    'serializer_exact_keys_invalid': 'D17 serializer field shape is invalid.',
    'serializer_identity_invalid': 'D17 serializer identity does not bind approved facts.',
    'serializer_cross_binding_invalid': 'D17 serializer artifacts do not bind one another.',
    'serializer_local_only_boundary_invalid': (
        'D17 serializer artifact is not local-only and not-sent.'
    ),
    'serializer_input_view_invalid': 'D17 serializer input view is invalid.',
    'serializer_prompt_invalid': 'D17 serializer prompt contract is invalid.',
    'serializer_config_invalid': 'D17 serializer config contract is invalid.',
    'serializer_bytes_invalid': 'D17 serializer canonical bytes are invalid.'
}

class _DuplicateJsonKey(ValueError):
    pass

@dataclass(frozen=True)
class D17SerializerErrorEnvelope:
    code: str
    schema_version: str = D17_PATH3_SERIALIZER_ERROR_ENVELOPE_SCHEMA_VERSION
    stage: str = 'd17'
    phase: str = 'local_serialization'
    payload_disclosure: str = 'none'
    retryable: bool = False
    def to_dict(self) -> dict[str, object]:
        if (
            self.code not in _SAFE
            or self.schema_version
            != D17_PATH3_SERIALIZER_ERROR_ENVELOPE_SCHEMA_VERSION
        ):
            raise ValueError('D17 serializer error envelope is invalid')
        if (
            self.stage,
            self.phase,
            self.payload_disclosure,
            self.retryable
        ) != (
            'd17',
            'local_serialization',
            'none',
            False
        ):
            raise ValueError('D17 serializer error envelope is invalid')
        return {
            'schema_version': self.schema_version,
            'code': self.code,
            'stage': self.stage,
            'phase': self.phase,
            'payload_disclosure': self.payload_disclosure,
            'retryable': self.retryable
        }

class D17SerializerValidationError(ValueError):
    def __init__(self, code: str) -> None:
        if code not in _SAFE:
            raise ValueError('D17 serializer error code is invalid')
        super().__init__(_SAFE[code])
        self.code = code
        self.envelope = D17SerializerErrorEnvelope(code)

def _fail(code: str) -> None:
    raise D17SerializerValidationError(code)

def _bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False
    ).encode(
        'utf-8'
    )

def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()

def _id(prefix: str, root: Mapping[str, object]) -> str:
    return prefix + _sha(_bytes(root))

def _mapping(value: object, keys: tuple[str, ...], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        _fail(code)
    return dict(value)

def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(code)
    return value

def _hex(value: object, code: str) -> str:
    value = _text(value, code)
    if len(value) != 64 or any((c not in '0123456789abcdef' for c in value)):
        _fail(code)
    return value

def _length(value: object, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _fail(code)
    return value

def _bool(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        _fail(code)
    return value

def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey()
        result[key] = value
    return result

def _constant(_: str) -> object:
    raise ValueError('non-finite JSON constant')

def _loads(raw: bytes, code: str) -> object:
    if not isinstance(raw, bytes) or not raw or raw.startswith(b'\xef\xbb\xbf'):
        _fail(code)
    try:
        return json.loads(
            raw.decode('utf-8', 'strict'),
            object_pairs_hook=_pairs,
            parse_constant=_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError):
        _fail(code)

def _boundary(
    authorization: object,
    path: object,
    local_only: object,
    not_sent: object,
    egress: object
) -> None:
    if (
        authorization,
        path,
        local_only,
        not_sent,
        egress
    ) != (
        _AUTHORIZATION,
        _PATH_3,
        True,
        True,
        False
    ):
        _fail('serializer_local_only_boundary_invalid')

def _manifest(manifest: object) -> D17Path3TierAManifest:
    if not isinstance(manifest, D17Path3TierAManifest):
        _fail('serializer_cross_binding_invalid')
    try:
        manifest.validate()
    except (TypeError, ValueError):
        _fail('serializer_cross_binding_invalid')
    return manifest

def _policy(manifest_id: object, identity: object, policy_sha: object) -> None:
    _text(manifest_id, 'serializer_cross_binding_invalid')
    expected = D17Path3TierAManifest.create().field_policy.approved_snapshot_sha256()
    if identity != D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY or _hex(
        policy_sha,
        'serializer_cross_binding_invalid'
    ) != expected:
        _fail('serializer_cross_binding_invalid')

def _digest(artifact: object, code: str) -> tuple[str, str, int]:
    if not hasattr(artifact, 'artifact_id') or not hasattr(artifact, 'canonical_bytes'):
        _fail(code)
    blob = artifact.canonical_bytes()
    return (_text(artifact.artifact_id, code), _sha(blob), len(blob))

@dataclass(frozen=True)
class D17Path3InputViewArtifact:
    schema_version: str
    authorization: str
    d17_path: str
    local_only: bool
    not_sent: bool
    external_egress_allowed: bool
    manifest_id: str
    field_policy_identity: str
    field_policy_snapshot_sha256: str
    input_view_id: str
    selection_record: D17Path3InputSelectionRecord
    provider_visible_input: D17Path3ProviderVisibleInputView
    provider_visible_input_sha256: str
    provider_visible_input_byte_length: int
    artifact_id: str
    @classmethod
    def create(
        cls,
        context: AgentContextBundle,
        selected: D17Path3SelectedInput,
        manifest: D17Path3TierAManifest
    ) -> 'D17Path3InputViewArtifact':
        manifest = _manifest(manifest)
        if not isinstance(selected, D17Path3SelectedInput):
            _fail('serializer_input_view_invalid')
        try:
            selected.validate_against(context, manifest)
        except (TypeError, ValueError):
            _fail('serializer_input_view_invalid')
        raw = selected.provider_visible_input.canonical_bytes()
        root = cls._root_for(
            manifest,
            selected.selection_record,
            selected.provider_visible_input,
            _sha(raw),
            len(raw)
        )
        result = cls(
            root['schema_version'],
            root['authorization'],
            root['d17_path'],
            root['local_only'],
            root['not_sent'],
            root['external_egress_allowed'],
            root['manifest_id'],
            root['field_policy_identity'],
            root['field_policy_snapshot_sha256'],
            root['input_view_id'],
            selected.selection_record,
            selected.provider_visible_input,
            root['provider_visible_input_sha256'],
            root['provider_visible_input_byte_length'],
            _id('d17-input-view-artifact-', root)
        )
        result.validate(manifest)
        return result
    @classmethod
    def from_dict(cls, payload: object) -> 'D17Path3InputViewArtifact':
        keys = (
            'schema_version',
            'authorization',
            'd17_path',
            'local_only',
            'not_sent',
            'external_egress_allowed',
            'manifest_id',
            'field_policy_identity',
            'field_policy_snapshot_sha256',
            'input_view_id',
            'selection_record',
            'provider_visible_input',
            'provider_visible_input_sha256',
            'provider_visible_input_byte_length',
            'artifact_id'
        )
        data = _mapping(payload, keys, 'serializer_exact_keys_invalid')
        result = cls(
            data['schema_version'],
            data['authorization'],
            data['d17_path'],
            _bool(data['local_only'], 'serializer_schema_invalid'),
            _bool(data['not_sent'], 'serializer_schema_invalid'),
            _bool(data['external_egress_allowed'], 'serializer_schema_invalid'),
            _text(data['manifest_id'], 'serializer_schema_invalid'),
            _text(data['field_policy_identity'], 'serializer_schema_invalid'),
            _hex(data['field_policy_snapshot_sha256'], 'serializer_schema_invalid'),
            _text(data['input_view_id'], 'serializer_schema_invalid'),
            D17Path3InputSelectionRecord.from_dict(data['selection_record']),
            D17Path3ProviderVisibleInputView.from_dict(data['provider_visible_input']),
            _hex(data['provider_visible_input_sha256'], 'serializer_schema_invalid'),
            _length(data['provider_visible_input_byte_length'], 'serializer_schema_invalid'),
            _text(data['artifact_id'], 'serializer_schema_invalid')
        )
        result.validate()
        return result
    @classmethod
    def from_bytes(cls, raw: bytes) -> 'D17Path3InputViewArtifact':
        return cls.from_dict(_loads(raw, 'serializer_bytes_invalid'))
    @classmethod
    def _root_for(
        cls,
        manifest: D17Path3TierAManifest,
        selection: D17Path3InputSelectionRecord,
        view: D17Path3ProviderVisibleInputView,
        view_sha: str,
        view_len: int
    ) -> dict[
        str,
        object
    ]:
        return {
            'schema_version': D17_PATH3_INPUT_VIEW_ARTIFACT_SCHEMA_VERSION,
            'authorization': _AUTHORIZATION,
            'd17_path': _PATH_3,
            'local_only': True,
            'not_sent': True,
            'external_egress_allowed': False,
            'manifest_id': manifest.manifest_id,
            'field_policy_identity': D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
            'field_policy_snapshot_sha256': manifest.field_policy.approved_snapshot_sha256(),
            'input_view_id': selection.input_view_id,
            'selection_record': selection.to_dict(),
            'provider_visible_input': view.to_dict(),
            'provider_visible_input_sha256': view_sha,
            'provider_visible_input_byte_length': view_len
        }
    def _root(self) -> dict[str, object]:
        return {
            'schema_version': self.schema_version,
            'authorization': self.authorization,
            'd17_path': self.d17_path,
            'local_only': self.local_only,
            'not_sent': self.not_sent,
            'external_egress_allowed': self.external_egress_allowed,
            'manifest_id': self.manifest_id,
            'field_policy_identity': self.field_policy_identity,
            'field_policy_snapshot_sha256': self.field_policy_snapshot_sha256,
            'input_view_id': self.input_view_id,
            'selection_record': self.selection_record.to_dict(),
            'provider_visible_input': self.provider_visible_input.to_dict(),
            'provider_visible_input_sha256': self.provider_visible_input_sha256,
            'provider_visible_input_byte_length': self.provider_visible_input_byte_length
        }
    def validate(self, manifest: D17Path3TierAManifest | None=None) -> None:
        if self.schema_version != D17_PATH3_INPUT_VIEW_ARTIFACT_SCHEMA_VERSION:
            _fail('serializer_schema_invalid')
        _boundary(
            self.authorization,
            self.d17_path,
            self.local_only,
            self.not_sent,
            self.external_egress_allowed
        )
        _policy(self.manifest_id, self.field_policy_identity, self.field_policy_snapshot_sha256)
        if self.input_view_id != self.selection_record.input_view_id:
            _fail('serializer_cross_binding_invalid')
        self.provider_visible_input.validate(manifest)
        try:
            self.selection_record.validate(
                manifest=manifest,
                provider_visible_input=self.provider_visible_input
            )
        except (TypeError, ValueError):
            _fail('serializer_cross_binding_invalid')
        raw = self.provider_visible_input.canonical_bytes()
        if (
            self.provider_visible_input_sha256,
            self.provider_visible_input_byte_length
        ) != (
            _sha(raw),
            len(raw)
        ):
            _fail('serializer_cross_binding_invalid')
        if self.artifact_id != _id('d17-input-view-artifact-', self._root()):
            _fail('serializer_identity_invalid')
        if manifest is not None and (
            self.manifest_id != manifest.manifest_id
            or self.field_policy_snapshot_sha256
            != manifest.field_policy.approved_snapshot_sha256()
        ):
            _fail('serializer_cross_binding_invalid')
    def validate_against(
        self,
        context: AgentContextBundle,
        manifest: D17Path3TierAManifest
    ) -> None:
        manifest = _manifest(manifest)
        self.validate(manifest)
        selected = D17Path3SelectedInput(self.provider_visible_input, self.selection_record)
        try:
            selected.validate_against(context, manifest)
        except (TypeError, ValueError):
            _fail('serializer_cross_binding_invalid')
    def to_dict(self) -> dict[str, object]:
        self.validate()
        result = self._root()
        result['artifact_id'] = self.artifact_id
        return result
    def canonical_bytes(self) -> bytes:
        return _bytes(self.to_dict())
    def sha256(self) -> str:
        return _sha(self.canonical_bytes())

@dataclass(frozen=True)
class D17Path3PromptArtifact:
    schema_version: str
    authorization: str
    d17_path: str
    local_only: bool
    not_sent: bool
    external_egress_allowed: bool
    manifest_id: str
    field_policy_identity: str
    field_policy_snapshot_sha256: str
    input_view_id: str
    input_view_artifact_id: str
    input_view_artifact_sha256: str
    input_view_artifact_byte_length: int
    semantic_candidate_schema_version: str
    prompt_text: str
    artifact_id: str
    @classmethod
    def create(
        cls,
        context: AgentContextBundle,
        manifest: D17Path3TierAManifest,
        input_artifact: D17Path3InputViewArtifact
    ) -> 'D17Path3PromptArtifact':
        if not isinstance(input_artifact, D17Path3InputViewArtifact):
            _fail('serializer_prompt_invalid')
        try:
            input_artifact.validate_against(context, manifest)
        except (TypeError, ValueError):
            _fail('serializer_prompt_invalid')
        aid, sha, length = _digest(input_artifact, 'serializer_prompt_invalid')
        root = {
            'schema_version': D17_PATH3_PROMPT_ARTIFACT_SCHEMA_VERSION,
            'authorization': _AUTHORIZATION,
            'd17_path': _PATH_3,
            'local_only': True,
            'not_sent': True,
            'external_egress_allowed': False,
            'manifest_id': input_artifact.manifest_id,
            'field_policy_identity': D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
            'field_policy_snapshot_sha256': input_artifact.field_policy_snapshot_sha256,
            'input_view_id': input_artifact.input_view_id,
            'input_view_artifact_id': aid,
            'input_view_artifact_sha256': sha,
            'input_view_artifact_byte_length': length,
            'semantic_candidate_schema_version': SEMANTIC_CANDIDATE_SCHEMA_VERSION,
            'prompt_text': _PROMPT_TEXT
        }
        result = cls(**root, artifact_id=_id('d17-prompt-artifact-', root))
        result.validate(input_artifact=input_artifact)
        return result
    @classmethod
    def from_dict(cls, payload: object) -> 'D17Path3PromptArtifact':
        keys = (
            'schema_version',
            'authorization',
            'd17_path',
            'local_only',
            'not_sent',
            'external_egress_allowed',
            'manifest_id',
            'field_policy_identity',
            'field_policy_snapshot_sha256',
            'input_view_id',
            'input_view_artifact_id',
            'input_view_artifact_sha256',
            'input_view_artifact_byte_length',
            'semantic_candidate_schema_version',
            'prompt_text',
            'artifact_id'
        )
        data = _mapping(payload, keys, 'serializer_exact_keys_invalid')
        result = cls(
            data['schema_version'],
            data['authorization'],
            data['d17_path'],
            _bool(data['local_only'], 'serializer_schema_invalid'),
            _bool(data['not_sent'], 'serializer_schema_invalid'),
            _bool(data['external_egress_allowed'], 'serializer_schema_invalid'),
            _text(data['manifest_id'], 'serializer_schema_invalid'),
            _text(data['field_policy_identity'], 'serializer_schema_invalid'),
            _hex(data['field_policy_snapshot_sha256'], 'serializer_schema_invalid'),
            _text(data['input_view_id'], 'serializer_schema_invalid'),
            _text(data['input_view_artifact_id'], 'serializer_schema_invalid'),
            _hex(data['input_view_artifact_sha256'], 'serializer_schema_invalid'),
            _length(data['input_view_artifact_byte_length'], 'serializer_schema_invalid'),
            _text(data['semantic_candidate_schema_version'], 'serializer_schema_invalid'),
            _text(data['prompt_text'], 'serializer_prompt_invalid'),
            _text(data['artifact_id'], 'serializer_schema_invalid')
        )
        result.validate()
        return result
    @classmethod
    def from_bytes(cls, raw: bytes) -> 'D17Path3PromptArtifact':
        return cls.from_dict(_loads(raw, 'serializer_bytes_invalid'))
    def _root(self) -> dict[str, object]:
        return {
            'schema_version': self.schema_version,
            'authorization': self.authorization,
            'd17_path': self.d17_path,
            'local_only': self.local_only,
            'not_sent': self.not_sent,
            'external_egress_allowed': self.external_egress_allowed,
            'manifest_id': self.manifest_id,
            'field_policy_identity': self.field_policy_identity,
            'field_policy_snapshot_sha256': self.field_policy_snapshot_sha256,
            'input_view_id': self.input_view_id,
            'input_view_artifact_id': self.input_view_artifact_id,
            'input_view_artifact_sha256': self.input_view_artifact_sha256,
            'input_view_artifact_byte_length': self.input_view_artifact_byte_length,
            'semantic_candidate_schema_version': self.semantic_candidate_schema_version,
            'prompt_text': self.prompt_text
        }
    def validate(self, *, input_artifact: D17Path3InputViewArtifact | None=None) -> None:
        if self.schema_version != D17_PATH3_PROMPT_ARTIFACT_SCHEMA_VERSION:
            _fail('serializer_schema_invalid')
        _boundary(
            self.authorization,
            self.d17_path,
            self.local_only,
            self.not_sent,
            self.external_egress_allowed
        )
        _policy(self.manifest_id, self.field_policy_identity, self.field_policy_snapshot_sha256)
        if (
            self.semantic_candidate_schema_version
            != SEMANTIC_CANDIDATE_SCHEMA_VERSION
            or self.prompt_text != _PROMPT_TEXT
        ):
            _fail('serializer_prompt_invalid')
        _hex(self.input_view_artifact_sha256, 'serializer_schema_invalid')
        _length(self.input_view_artifact_byte_length, 'serializer_schema_invalid')
        if self.artifact_id != _id('d17-prompt-artifact-', self._root()):
            _fail('serializer_identity_invalid')
        if input_artifact is not None:
            input_artifact.validate()
            aid, sha, length = _digest(input_artifact, 'serializer_cross_binding_invalid')
            if (
                self.manifest_id,
                self.field_policy_snapshot_sha256,
                self.input_view_id,
                self.input_view_artifact_id,
                self.input_view_artifact_sha256,
                self.input_view_artifact_byte_length
            ) != (
                input_artifact.manifest_id,
                input_artifact.field_policy_snapshot_sha256,
                input_artifact.input_view_id,
                aid,
                sha,
                length
            ):
                _fail('serializer_cross_binding_invalid')
    def to_dict(self) -> dict[str, object]:
        self.validate()
        result = self._root()
        result['artifact_id'] = self.artifact_id
        return result
    def canonical_bytes(self) -> bytes:
        return _bytes(self.to_dict())
    def sha256(self) -> str:
        return _sha(self.canonical_bytes())

@dataclass(frozen=True)
class D17Path3ConfigArtifact:
    schema_version: str
    authorization: str
    d17_path: str
    local_only: bool
    not_sent: bool
    external_egress_allowed: bool
    manifest_id: str
    field_policy_identity: str
    field_policy_snapshot_sha256: str
    input_view_id: str
    input_view_artifact_id: str
    input_view_artifact_sha256: str
    input_view_artifact_byte_length: int
    serializer_contract: tuple[tuple[str, str], ...]
    tier_b_runtime_status: tuple[tuple[str, str], ...]
    artifact_id: str
    @classmethod
    def create(
        cls,
        context: AgentContextBundle,
        manifest: D17Path3TierAManifest,
        input_artifact: D17Path3InputViewArtifact
    ) -> 'D17Path3ConfigArtifact':
        if not isinstance(input_artifact, D17Path3InputViewArtifact):
            _fail('serializer_config_invalid')
        try:
            input_artifact.validate_against(context, manifest)
        except (TypeError, ValueError):
            _fail('serializer_config_invalid')
        aid, sha, length = _digest(input_artifact, 'serializer_config_invalid')
        root = {
            'schema_version': D17_PATH3_CONFIG_ARTIFACT_SCHEMA_VERSION,
            'authorization': _AUTHORIZATION,
            'd17_path': _PATH_3,
            'local_only': True,
            'not_sent': True,
            'external_egress_allowed': False,
            'manifest_id': input_artifact.manifest_id,
            'field_policy_identity': D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
            'field_policy_snapshot_sha256': input_artifact.field_policy_snapshot_sha256,
            'input_view_id': input_artifact.input_view_id,
            'input_view_artifact_id': aid,
            'input_view_artifact_sha256': sha,
            'input_view_artifact_byte_length': length,
            'serializer_contract': dict(_CONFIG_CONTRACT),
            'tier_b_runtime_status': dict(_TIER_B_RUNTIME_STATUS)
        }
        result = cls(
            root['schema_version'],
            root['authorization'],
            root['d17_path'],
            root['local_only'],
            root['not_sent'],
            root['external_egress_allowed'],
            root['manifest_id'],
            root['field_policy_identity'],
            root['field_policy_snapshot_sha256'],
            root['input_view_id'],
            root['input_view_artifact_id'],
            root['input_view_artifact_sha256'],
            root['input_view_artifact_byte_length'],
            tuple(_CONFIG_CONTRACT.items()),
            tuple(_TIER_B_RUNTIME_STATUS.items()),
            _id('d17-config-artifact-', root)
        )
        result.validate(input_artifact=input_artifact)
        return result
    @classmethod
    def from_dict(cls, payload: object) -> 'D17Path3ConfigArtifact':
        keys = (
            'schema_version',
            'authorization',
            'd17_path',
            'local_only',
            'not_sent',
            'external_egress_allowed',
            'manifest_id',
            'field_policy_identity',
            'field_policy_snapshot_sha256',
            'input_view_id',
            'input_view_artifact_id',
            'input_view_artifact_sha256',
            'input_view_artifact_byte_length',
            'serializer_contract',
            'tier_b_runtime_status',
            'artifact_id'
        )
        data = _mapping(payload, keys, 'serializer_exact_keys_invalid')
        contract = _mapping(
            data['serializer_contract'],
            tuple(_CONFIG_CONTRACT),
            'serializer_config_invalid'
        )
        runtime = _mapping(
            data['tier_b_runtime_status'],
            tuple(_TIER_B_RUNTIME_STATUS),
            'serializer_config_invalid'
        )
        result = cls(
            data['schema_version'],
            data['authorization'],
            data['d17_path'],
            _bool(data['local_only'], 'serializer_schema_invalid'),
            _bool(data['not_sent'], 'serializer_schema_invalid'),
            _bool(data['external_egress_allowed'], 'serializer_schema_invalid'),
            _text(data['manifest_id'], 'serializer_schema_invalid'),
            _text(data['field_policy_identity'], 'serializer_schema_invalid'),
            _hex(data['field_policy_snapshot_sha256'], 'serializer_schema_invalid'),
            _text(data['input_view_id'], 'serializer_schema_invalid'),
            _text(data['input_view_artifact_id'], 'serializer_schema_invalid'),
            _hex(data['input_view_artifact_sha256'], 'serializer_schema_invalid'),
            _length(data['input_view_artifact_byte_length'], 'serializer_schema_invalid'),
            tuple(
                (key, _text(contract[key], 'serializer_config_invalid'))
                for key in _CONFIG_CONTRACT
            ),
            tuple(
                (key, _text(runtime[key], 'serializer_config_invalid'))
                for key in _TIER_B_RUNTIME_STATUS
            ),
            _text(data['artifact_id'], 'serializer_schema_invalid')
        )
        result.validate()
        return result
    @classmethod
    def from_bytes(cls, raw: bytes) -> 'D17Path3ConfigArtifact':
        return cls.from_dict(_loads(raw, 'serializer_bytes_invalid'))
    def _root(self) -> dict[str, object]:
        return {
            'schema_version': self.schema_version,
            'authorization': self.authorization,
            'd17_path': self.d17_path,
            'local_only': self.local_only,
            'not_sent': self.not_sent,
            'external_egress_allowed': self.external_egress_allowed,
            'manifest_id': self.manifest_id,
            'field_policy_identity': self.field_policy_identity,
            'field_policy_snapshot_sha256': self.field_policy_snapshot_sha256,
            'input_view_id': self.input_view_id,
            'input_view_artifact_id': self.input_view_artifact_id,
            'input_view_artifact_sha256': self.input_view_artifact_sha256,
            'input_view_artifact_byte_length': self.input_view_artifact_byte_length,
            'serializer_contract': dict(self.serializer_contract),
            'tier_b_runtime_status': dict(self.tier_b_runtime_status)
        }
    def validate(self, *, input_artifact: D17Path3InputViewArtifact | None=None) -> None:
        if self.schema_version != D17_PATH3_CONFIG_ARTIFACT_SCHEMA_VERSION:
            _fail('serializer_schema_invalid')
        _boundary(
            self.authorization,
            self.d17_path,
            self.local_only,
            self.not_sent,
            self.external_egress_allowed
        )
        _policy(self.manifest_id, self.field_policy_identity, self.field_policy_snapshot_sha256)
        if dict(
            self.serializer_contract
        ) != _CONFIG_CONTRACT or tuple(
            (key for key, _ in self.serializer_contract)
        ) != tuple(
            _CONFIG_CONTRACT
        ):
            _fail('serializer_config_invalid')
        if dict(
            self.tier_b_runtime_status
        ) != _TIER_B_RUNTIME_STATUS or tuple(
            (key for key, _ in self.tier_b_runtime_status)
        ) != tuple(
            _TIER_B_RUNTIME_STATUS
        ):
            _fail('serializer_config_invalid')
        _hex(self.input_view_artifact_sha256, 'serializer_schema_invalid')
        _length(self.input_view_artifact_byte_length, 'serializer_schema_invalid')
        if self.artifact_id != _id('d17-config-artifact-', self._root()):
            _fail('serializer_identity_invalid')
        if input_artifact is not None:
            input_artifact.validate()
            aid, sha, length = _digest(input_artifact, 'serializer_cross_binding_invalid')
            if (
                self.manifest_id,
                self.field_policy_snapshot_sha256,
                self.input_view_id,
                self.input_view_artifact_id,
                self.input_view_artifact_sha256,
                self.input_view_artifact_byte_length
            ) != (
                input_artifact.manifest_id,
                input_artifact.field_policy_snapshot_sha256,
                input_artifact.input_view_id,
                aid,
                sha,
                length
            ):
                _fail('serializer_cross_binding_invalid')
    def to_dict(self) -> dict[str, object]:
        self.validate()
        result = self._root()
        result['artifact_id'] = self.artifact_id
        return result
    def canonical_bytes(self) -> bytes:
        return _bytes(self.to_dict())
    def sha256(self) -> str:
        return _sha(self.canonical_bytes())

@dataclass(frozen=True)
class D17Path3LocalRequestArtifact:
    """Assembled local-only request artifact, explicitly not sent or invoked."""
    schema_version: str
    authorization: str
    d17_path: str
    local_only: bool
    not_sent: bool
    transport_state: str
    provider_invocation_state: str
    external_egress_allowed: bool
    manifest_id: str
    field_policy_identity: str
    field_policy_snapshot_sha256: str
    input_view_artifact: D17Path3InputViewArtifact
    prompt_artifact: D17Path3PromptArtifact
    config_artifact: D17Path3ConfigArtifact
    input_view_artifact_sha256: str
    input_view_artifact_byte_length: int
    prompt_artifact_sha256: str
    prompt_artifact_byte_length: int
    config_artifact_sha256: str
    config_artifact_byte_length: int
    artifact_id: str
    @classmethod
    def create(
        cls,
        context: AgentContextBundle,
        selected: D17Path3SelectedInput,
        manifest: D17Path3TierAManifest
    ) -> 'D17Path3LocalRequestArtifact':
        manifest = _manifest(manifest)
        if not isinstance(selected, D17Path3SelectedInput):
            _fail('serializer_input_view_invalid')
        try:
            selected.validate_against(context, manifest)
        except (TypeError, ValueError):
            _fail('serializer_input_view_invalid')
        input_artifact = D17Path3InputViewArtifact.create(context, selected, manifest)
        prompt = D17Path3PromptArtifact.create(context, manifest, input_artifact)
        config = D17Path3ConfigArtifact.create(context, manifest, input_artifact)
        _, input_sha, input_len = _digest(input_artifact, 'serializer_input_view_invalid')
        _, prompt_sha, prompt_len = _digest(prompt, 'serializer_prompt_invalid')
        _, config_sha, config_len = _digest(config, 'serializer_config_invalid')
        root = {
            'schema_version': D17_PATH3_LOCAL_REQUEST_ARTIFACT_SCHEMA_VERSION,
            'authorization': _AUTHORIZATION,
            'd17_path': _PATH_3,
            'local_only': True,
            'not_sent': True,
            'transport_state': 'not_sent',
            'provider_invocation_state': 'not_invoked',
            'external_egress_allowed': False,
            'manifest_id': input_artifact.manifest_id,
            'field_policy_identity': D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
            'field_policy_snapshot_sha256': input_artifact.field_policy_snapshot_sha256,
            'input_view_artifact': input_artifact.to_dict(),
            'prompt_artifact': prompt.to_dict(),
            'config_artifact': config.to_dict(),
            'input_view_artifact_sha256': input_sha,
            'input_view_artifact_byte_length': input_len,
            'prompt_artifact_sha256': prompt_sha,
            'prompt_artifact_byte_length': prompt_len,
            'config_artifact_sha256': config_sha,
            'config_artifact_byte_length': config_len
        }
        result = cls(
            root['schema_version'],
            root['authorization'],
            root['d17_path'],
            root['local_only'],
            root['not_sent'],
            root['transport_state'],
            root['provider_invocation_state'],
            root['external_egress_allowed'],
            root['manifest_id'],
            root['field_policy_identity'],
            root['field_policy_snapshot_sha256'],
            input_artifact,
            prompt,
            config,
            input_sha,
            input_len,
            prompt_sha,
            prompt_len,
            config_sha,
            config_len,
            _id('d17-local-request-', root)
        )
        result.validate(manifest)
        return result
    @classmethod
    def from_dict(cls, payload: object) -> 'D17Path3LocalRequestArtifact':
        keys = (
            'schema_version',
            'authorization',
            'd17_path',
            'local_only',
            'not_sent',
            'transport_state',
            'provider_invocation_state',
            'external_egress_allowed',
            'manifest_id',
            'field_policy_identity',
            'field_policy_snapshot_sha256',
            'input_view_artifact',
            'prompt_artifact',
            'config_artifact',
            'input_view_artifact_sha256',
            'input_view_artifact_byte_length',
            'prompt_artifact_sha256',
            'prompt_artifact_byte_length',
            'config_artifact_sha256',
            'config_artifact_byte_length',
            'artifact_id'
        )
        data = _mapping(payload, keys, 'serializer_exact_keys_invalid')
        result = cls(
            data['schema_version'],
            data['authorization'],
            data['d17_path'],
            _bool(data['local_only'], 'serializer_schema_invalid'),
            _bool(data['not_sent'], 'serializer_schema_invalid'),
            _text(data['transport_state'], 'serializer_schema_invalid'),
            _text(data['provider_invocation_state'], 'serializer_schema_invalid'),
            _bool(data['external_egress_allowed'], 'serializer_schema_invalid'),
            _text(data['manifest_id'], 'serializer_schema_invalid'),
            _text(data['field_policy_identity'], 'serializer_schema_invalid'),
            _hex(data['field_policy_snapshot_sha256'], 'serializer_schema_invalid'),
            D17Path3InputViewArtifact.from_dict(data['input_view_artifact']),
            D17Path3PromptArtifact.from_dict(data['prompt_artifact']),
            D17Path3ConfigArtifact.from_dict(data['config_artifact']),
            _hex(data['input_view_artifact_sha256'], 'serializer_schema_invalid'),
            _length(data['input_view_artifact_byte_length'], 'serializer_schema_invalid'),
            _hex(data['prompt_artifact_sha256'], 'serializer_schema_invalid'),
            _length(data['prompt_artifact_byte_length'], 'serializer_schema_invalid'),
            _hex(data['config_artifact_sha256'], 'serializer_schema_invalid'),
            _length(data['config_artifact_byte_length'], 'serializer_schema_invalid'),
            _text(data['artifact_id'], 'serializer_schema_invalid')
        )
        result.validate()
        return result
    @classmethod
    def from_bytes(cls, raw: bytes) -> 'D17Path3LocalRequestArtifact':
        return cls.from_dict(_loads(raw, 'serializer_bytes_invalid'))
    def _root(self) -> dict[str, object]:
        return {
            'schema_version': self.schema_version,
            'authorization': self.authorization,
            'd17_path': self.d17_path,
            'local_only': self.local_only,
            'not_sent': self.not_sent,
            'transport_state': self.transport_state,
            'provider_invocation_state': self.provider_invocation_state,
            'external_egress_allowed': self.external_egress_allowed,
            'manifest_id': self.manifest_id,
            'field_policy_identity': self.field_policy_identity,
            'field_policy_snapshot_sha256': self.field_policy_snapshot_sha256,
            'input_view_artifact': self.input_view_artifact.to_dict(),
            'prompt_artifact': self.prompt_artifact.to_dict(),
            'config_artifact': self.config_artifact.to_dict(),
            'input_view_artifact_sha256': self.input_view_artifact_sha256,
            'input_view_artifact_byte_length': self.input_view_artifact_byte_length,
            'prompt_artifact_sha256': self.prompt_artifact_sha256,
            'prompt_artifact_byte_length': self.prompt_artifact_byte_length,
            'config_artifact_sha256': self.config_artifact_sha256,
            'config_artifact_byte_length': self.config_artifact_byte_length
        }
    def validate(self, manifest: D17Path3TierAManifest | None=None) -> None:
        if self.schema_version != D17_PATH3_LOCAL_REQUEST_ARTIFACT_SCHEMA_VERSION:
            _fail('serializer_schema_invalid')
        _boundary(
            self.authorization,
            self.d17_path,
            self.local_only,
            self.not_sent,
            self.external_egress_allowed
        )
        if (self.transport_state, self.provider_invocation_state) != ('not_sent', 'not_invoked'):
            _fail('serializer_local_only_boundary_invalid')
        _policy(self.manifest_id, self.field_policy_identity, self.field_policy_snapshot_sha256)
        self.input_view_artifact.validate(manifest)
        self.prompt_artifact.validate(input_artifact=self.input_view_artifact)
        self.config_artifact.validate(input_artifact=self.input_view_artifact)
        input_id, input_sha, input_len = _digest(
            self.input_view_artifact,
            'serializer_cross_binding_invalid'
        )
        _, prompt_sha, prompt_len = _digest(
            self.prompt_artifact,
            'serializer_cross_binding_invalid'
        )
        _, config_sha, config_len = _digest(
            self.config_artifact,
            'serializer_cross_binding_invalid'
        )
        if (
            self.manifest_id,
            self.field_policy_snapshot_sha256,
            self.input_view_artifact_sha256,
            self.input_view_artifact_byte_length,
            self.prompt_artifact_sha256,
            self.prompt_artifact_byte_length,
            self.config_artifact_sha256,
            self.config_artifact_byte_length
        ) != (
            self.input_view_artifact.manifest_id,
            self.input_view_artifact.field_policy_snapshot_sha256,
            input_sha,
            input_len,
            prompt_sha,
            prompt_len,
            config_sha,
            config_len
        ):
            _fail('serializer_cross_binding_invalid')
        if (
            self.prompt_artifact.input_view_artifact_id != input_id
            or self.config_artifact.input_view_artifact_id != input_id
        ):
            _fail('serializer_cross_binding_invalid')
        if self.artifact_id != _id('d17-local-request-', self._root()):
            _fail('serializer_identity_invalid')
        if manifest is not None:
            manifest = _manifest(manifest)
            if (
                self.manifest_id,
                self.field_policy_snapshot_sha256
            ) != (
                manifest.manifest_id,
                manifest.field_policy.approved_snapshot_sha256()
            ):
                _fail('serializer_cross_binding_invalid')
    def validate_against(
        self,
        context: AgentContextBundle,
        manifest: D17Path3TierAManifest
    ) -> None:
        manifest = _manifest(manifest)
        self.validate(manifest)
        self.input_view_artifact.validate_against(context, manifest)
    def to_dict(self) -> dict[str, object]:
        self.validate()
        result = self._root()
        result['artifact_id'] = self.artifact_id
        return result
    def canonical_bytes(self) -> bytes:
        return _bytes(self.to_dict())
    def sha256(self) -> str:
        return _sha(self.canonical_bytes())

def serialize_d17_path3_local_request(
    context: AgentContextBundle,
    selected: D17Path3SelectedInput,
    manifest: D17Path3TierAManifest
) -> D17Path3LocalRequestArtifact:
    """Build deterministic local-only artifacts after live trust-anchor validation."""
    return D17Path3LocalRequestArtifact.create(context, selected, manifest)
