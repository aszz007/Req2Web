"""Path-3 Tier A local input-view selector.

This module constructs the only approved Provider-visible view for a future
non-H1 path-3 compatibility pilot. It is deliberately local-only: it neither
imports guidance objects nor provides a transport, runtime, model, or
Provider invocation seam.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from req2web_agent.schema import AgentContextBundle, UseCase
from req2web_provider.d17_manifest import (
    D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
    D17_PATH3_TIER_A_FIELD_POLICY_SCHEMA_VERSION,
    D17Path3TierAManifest,
)


D17_PATH3_PROVIDER_VISIBLE_INPUT_VIEW_SCHEMA_VERSION = (
    "req2web.d17.path3.provider_visible_input_view.v1"
)
D17_PATH3_INPUT_SELECTION_RECORD_SCHEMA_VERSION = (
    "req2web.d17.path3.input_selection_record.v1"
)
D17_PATH3_INPUT_VIEW_ERROR_ENVELOPE_SCHEMA_VERSION = (
    "req2web.d17.path3.input_view.error.v1"
)

_PATH_3 = "path_3"
_AUTHORIZATION = "tier_a_local_implementation_only"
_SOURCE_CLASSES = ("project_authored", "synthetic")
_PROVIDER_VISIBLE_KEYS = (
    "original_requirement",
    "use_cases",
    "constraints",
    "target_device",
    "task_type",
    "structural_signals",
)
_USE_CASE_KEYS = (
    "use_case_id",
    "title",
    "actor",
    "goal",
    "expected_outcome",
)
_SAFE_ERROR_MESSAGES = {
    "context_invalid": "D17 input context is not valid.",
    "manifest_invalid": "D17 manifest is not valid for input selection.",
    "source_class_invalid": "D17 original requirement source class is not approved.",
    "input_view_schema_invalid": "D17 input-view schema is invalid.",
    "input_view_exact_keys_invalid": "D17 input-view field shape is invalid.",
    "input_view_value_invalid": "D17 input-view values are invalid.",
    "input_view_identity_invalid": "D17 input-view identity does not bind approved facts.",
    "selection_record_schema_invalid": "D17 selection-record schema is invalid.",
    "selection_record_exact_keys_invalid": "D17 selection-record field shape is invalid.",
    "selection_record_identity_invalid": "D17 selection-record identity does not bind approved facts.",
    "selection_record_cross_binding_invalid": "D17 selection record does not bind the approved input view.",
    "local_only_boundary_invalid": "D17 input selection is not local-only and not-sent.",
}


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class D17InputViewErrorEnvelope:
    """Fixed, payload-free local error envelope."""

    code: str
    schema_version: str = D17_PATH3_INPUT_VIEW_ERROR_ENVELOPE_SCHEMA_VERSION
    stage: str = "d17"
    phase: str = "input_selection"
    payload_disclosure: str = "none"
    retryable: bool = False

    def to_dict(self) -> dict[str, object]:
        if self.code not in _SAFE_ERROR_MESSAGES:
            raise ValueError("D17 input-view error envelope code is invalid")
        if (
            self.schema_version != D17_PATH3_INPUT_VIEW_ERROR_ENVELOPE_SCHEMA_VERSION
            or self.stage != "d17"
            or self.phase != "input_selection"
            or self.payload_disclosure != "none"
            or self.retryable is not False
        ):
            raise ValueError("D17 input-view error envelope is invalid")
        return {
            "schema_version": self.schema_version,
            "code": self.code,
            "stage": self.stage,
            "phase": self.phase,
            "payload_disclosure": self.payload_disclosure,
            "retryable": self.retryable,
        }


class D17InputViewValidationError(ValueError):
    """Fail-closed error that intentionally omits caller-supplied content."""

    def __init__(self, code: str) -> None:
        if code not in _SAFE_ERROR_MESSAGES:
            raise ValueError("D17 input-view error code is invalid")
        super().__init__(_SAFE_ERROR_MESSAGES[code])
        self.code = code
        self.envelope = D17InputViewErrorEnvelope(code)


def _fail(code: str) -> None:
    raise D17InputViewValidationError(code)


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_json(payload: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(payload))


def _id(prefix: str, root: Mapping[str, object]) -> str:
    return prefix + _sha256_json(root)


def _require_exact_mapping(
    value: object, expected_keys: tuple[str, ...], *, code: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(expected_keys):
        _fail(code)
    return dict(value)


def _require_text(value: object, *, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(code)
    return value


def _require_text_tuple(value: object, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _fail(code)
    return tuple(_require_text(item, code=code) for item in value)


def _require_bool(value: object, *, code: str) -> bool:
    if not isinstance(value, bool):
        _fail(code)
    return value


def _require_sha256(value: object, *, code: str) -> str:
    text = _require_text(value, code=code)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        _fail(code)
    return text


def _require_byte_length(value: object, *, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _fail(code)
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey()
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError("non-finite JSON constant")


def _strict_json_loads(raw_bytes: bytes, *, code: str) -> object:
    if not isinstance(raw_bytes, bytes) or not raw_bytes or raw_bytes.startswith(b"\xef\xbb\xbf"):
        _fail(code)
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError):
        _fail(code)


def _validated_manifest(manifest: object) -> D17Path3TierAManifest:
    if not isinstance(manifest, D17Path3TierAManifest):
        _fail("manifest_invalid")
    try:
        manifest.validate()
    except (TypeError, ValueError):
        _fail("manifest_invalid")
    return manifest


def _validated_manifest_policy(manifest: D17Path3TierAManifest):
    """Return the manifest-owned policy after checking local anti-drift anchors."""

    manifest = _validated_manifest(manifest)
    policy = manifest.field_policy
    if (
        tuple(policy.provider_visible_fields) != _PROVIDER_VISIBLE_KEYS
        or tuple(policy.use_case_item_keys) != _USE_CASE_KEYS
        or tuple(policy.original_requirement_source_classes) != _SOURCE_CLASSES
    ):
        _fail("manifest_invalid")
    return policy


def _validate_source_class(
    source_class: object, manifest: D17Path3TierAManifest
) -> str:
    policy = _validated_manifest_policy(manifest)
    source_class = _require_text(source_class, code="source_class_invalid")
    if source_class not in policy.original_requirement_source_classes:
        _fail("source_class_invalid")
    return source_class


def _validate_context(context: object) -> AgentContextBundle:
    if not isinstance(context, AgentContextBundle):
        _fail("context_invalid")
    try:
        context.validate()
    except (TypeError, ValueError):
        _fail("context_invalid")
    return context


def _use_case_payload(use_case: UseCase) -> dict[str, str]:
    if not isinstance(use_case, UseCase):
        _fail("input_view_value_invalid")
    payload = {
        "use_case_id": _require_text(use_case.use_case_id, code="input_view_value_invalid"),
        "title": _require_text(use_case.title, code="input_view_value_invalid"),
        "actor": _require_text(use_case.actor, code="input_view_value_invalid"),
        "goal": _require_text(use_case.goal, code="input_view_value_invalid"),
        "expected_outcome": _require_text(
            use_case.expected_outcome, code="input_view_value_invalid"
        ),
    }
    if tuple(payload) != _USE_CASE_KEYS:
        _fail("input_view_exact_keys_invalid")
    return payload


def _complete_local_context_identity_payload(
    context: AgentContextBundle,
) -> dict[str, object]:
    """Return the full validated local AgentContext payload for hashing only.

    This path never removes fields and never serializes the resulting values into
    a Provider-visible or request artifact. The resulting digest binds the full
    context, including stable five-role retrieval queries and result order.
    """

    context = _validate_context(context)
    try:
        payload = context.to_dict()
        _canonical_json_bytes(payload)
    except (TypeError, ValueError):
        _fail("context_invalid")
    return payload


@dataclass(frozen=True)
class D17Path3ProviderVisibleInputView:
    """The six-category view only; it contains no local selection metadata."""

    original_requirement: str
    use_cases: tuple[tuple[tuple[str, str], ...], ...]
    constraints: tuple[str, ...]
    target_device: str
    task_type: str
    structural_signals: tuple[str, ...]

    @classmethod
    def from_context(
        cls, context: AgentContextBundle, manifest: D17Path3TierAManifest
    ) -> "D17Path3ProviderVisibleInputView":
        context = _validate_context(context)
        manifest = _validated_manifest(manifest)
        policy = _validated_manifest_policy(manifest)
        use_cases = tuple(
            tuple(_use_case_payload(item).items()) for item in context.use_cases
        )
        view = cls(
            original_requirement=_require_text(
                context.original_requirement, code="input_view_value_invalid"
            ),
            use_cases=use_cases,
            constraints=_require_text_tuple(
                context.constraints, code="input_view_value_invalid"
            ),
            target_device=_require_text(
                context.target_device, code="input_view_value_invalid"
            ),
            task_type=_require_text(context.task_type, code="input_view_value_invalid"),
            structural_signals=tuple(policy.structural_signal_names),
        )
        view.validate(manifest)
        return view

    @classmethod
    def from_dict(
        cls,
        payload: object,
        *,
        manifest: D17Path3TierAManifest | None = None,
    ) -> "D17Path3ProviderVisibleInputView":
        data = _require_exact_mapping(
            payload, _PROVIDER_VISIBLE_KEYS, code="input_view_exact_keys_invalid"
        )
        raw_use_cases = data["use_cases"]
        if not isinstance(raw_use_cases, (list, tuple)) or not raw_use_cases:
            _fail("input_view_value_invalid")
        normalized_use_cases: list[tuple[tuple[str, str], ...]] = []
        for raw_use_case in raw_use_cases:
            use_case = _require_exact_mapping(
                raw_use_case, _USE_CASE_KEYS, code="input_view_exact_keys_invalid"
            )
            normalized_use_cases.append(
                tuple(
                    (key, _require_text(use_case[key], code="input_view_value_invalid"))
                    for key in _USE_CASE_KEYS
                )
            )
        view = cls(
            original_requirement=_require_text(
                data["original_requirement"], code="input_view_value_invalid"
            ),
            use_cases=tuple(normalized_use_cases),
            constraints=_require_text_tuple(
                data["constraints"], code="input_view_value_invalid"
            ),
            target_device=_require_text(
                data["target_device"], code="input_view_value_invalid"
            ),
            task_type=_require_text(data["task_type"], code="input_view_value_invalid"),
            structural_signals=_require_text_tuple(
                data["structural_signals"], code="input_view_value_invalid"
            ),
        )
        view.validate(manifest)
        return view

    @classmethod
    def from_bytes(
        cls,
        raw_bytes: bytes,
        *,
        manifest: D17Path3TierAManifest | None = None,
    ) -> "D17Path3ProviderVisibleInputView":
        return cls.from_dict(
            _strict_json_loads(raw_bytes, code="input_view_schema_invalid"),
            manifest=manifest,
        )

    def validate(self, manifest: D17Path3TierAManifest | None = None) -> None:
        _require_text(self.original_requirement, code="input_view_value_invalid")
        if not self.use_cases or not 2 <= len(self.use_cases) <= 4:
            _fail("input_view_value_invalid")
        seen_use_case_ids: set[str] = set()
        for raw_use_case in self.use_cases:
            if not isinstance(raw_use_case, tuple) or tuple(key for key, _ in raw_use_case) != _USE_CASE_KEYS:
                _fail("input_view_exact_keys_invalid")
            item = dict(raw_use_case)
            if len(item) != len(_USE_CASE_KEYS):
                _fail("input_view_exact_keys_invalid")
            for key in _USE_CASE_KEYS:
                _require_text(item.get(key), code="input_view_value_invalid")
            if item["use_case_id"] in seen_use_case_ids:
                _fail("input_view_value_invalid")
            seen_use_case_ids.add(item["use_case_id"])
        for value in self.constraints:
            _require_text(value, code="input_view_value_invalid")
        if len(set(self.constraints)) != len(self.constraints):
            _fail("input_view_value_invalid")
        _require_text(self.target_device, code="input_view_value_invalid")
        _require_text(self.task_type, code="input_view_value_invalid")
        if len(set(self.structural_signals)) != len(self.structural_signals):
            _fail("input_view_value_invalid")
        for name in self.structural_signals:
            _require_text(name, code="input_view_value_invalid")
        if manifest is not None:
            policy = _validated_manifest_policy(manifest)
            if self.structural_signals != tuple(policy.structural_signal_names):
                _fail("selection_record_cross_binding_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "original_requirement": self.original_requirement,
            "use_cases": [dict(item) for item in self.use_cases],
            "constraints": list(self.constraints),
            "target_device": self.target_device,
            "task_type": self.task_type,
            "structural_signals": list(self.structural_signals),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256_bytes(self.canonical_bytes())


@dataclass(frozen=True)
class D17Path3InputSelectionRecord:
    """Local-only binding record; never part of the Provider-visible view."""

    schema_version: str
    authorization: str
    d17_path: str
    local_only: bool
    not_sent: bool
    external_egress_allowed: bool
    manifest_id: str
    field_policy_schema_version: str
    field_policy_identity: str
    field_policy_snapshot_sha256: str
    original_requirement_source_class: str
    local_context_sha256: str
    provider_visible_input_sha256: str
    provider_visible_input_byte_length: int
    input_view_id: str
    selection_record_id: str

    @classmethod
    def create(
        cls,
        *,
        context: AgentContextBundle,
        manifest: D17Path3TierAManifest,
        original_requirement_source_class: str,
        provider_visible_input: D17Path3ProviderVisibleInputView,
    ) -> "D17Path3InputSelectionRecord":
        context = _validate_context(context)
        manifest = _validated_manifest(manifest)
        original_requirement_source_class = _validate_source_class(
            original_requirement_source_class, manifest
        )
        provider_visible_input.validate(manifest)
        expected_view = D17Path3ProviderVisibleInputView.from_context(context, manifest)
        if provider_visible_input.to_dict() != expected_view.to_dict():
            _fail("selection_record_cross_binding_invalid")
        provider_bytes = provider_visible_input.canonical_bytes()
        root = cls._root_for(
            manifest=manifest,
            original_requirement_source_class=original_requirement_source_class,
            local_context_sha256=_sha256_json(
                _complete_local_context_identity_payload(context)
            ),
            provider_visible_input_sha256=_sha256_bytes(provider_bytes),
            provider_visible_input_byte_length=len(provider_bytes),
        )
        record = cls(
            **root,
            selection_record_id=_id("d17-input-selection-", root),
        )
        record.validate(manifest=manifest, provider_visible_input=provider_visible_input)
        return record

    @classmethod
    def from_dict(cls, payload: object) -> "D17Path3InputSelectionRecord":
        keys = (
            "schema_version", "authorization", "d17_path", "local_only", "not_sent",
            "external_egress_allowed", "manifest_id", "field_policy_schema_version",
            "field_policy_identity", "field_policy_snapshot_sha256",
            "original_requirement_source_class", "local_context_sha256",
            "provider_visible_input_sha256", "provider_visible_input_byte_length",
            "input_view_id", "selection_record_id",
        )
        data = _require_exact_mapping(payload, keys, code="selection_record_exact_keys_invalid")
        record = cls(
            schema_version=data["schema_version"],
            authorization=data["authorization"],
            d17_path=data["d17_path"],
            local_only=_require_bool(data["local_only"], code="selection_record_schema_invalid"),
            not_sent=_require_bool(data["not_sent"], code="selection_record_schema_invalid"),
            external_egress_allowed=_require_bool(data["external_egress_allowed"], code="selection_record_schema_invalid"),
            manifest_id=_require_text(data["manifest_id"], code="selection_record_schema_invalid"),
            field_policy_schema_version=_require_text(data["field_policy_schema_version"], code="selection_record_schema_invalid"),
            field_policy_identity=_require_text(data["field_policy_identity"], code="selection_record_schema_invalid"),
            field_policy_snapshot_sha256=_require_sha256(data["field_policy_snapshot_sha256"], code="selection_record_schema_invalid"),
            original_requirement_source_class=_require_text(data["original_requirement_source_class"], code="source_class_invalid"),
            local_context_sha256=_require_sha256(data["local_context_sha256"], code="selection_record_schema_invalid"),
            provider_visible_input_sha256=_require_sha256(data["provider_visible_input_sha256"], code="selection_record_schema_invalid"),
            provider_visible_input_byte_length=_require_byte_length(data["provider_visible_input_byte_length"], code="selection_record_schema_invalid"),
            input_view_id=_require_text(data["input_view_id"], code="selection_record_schema_invalid"),
            selection_record_id=_require_text(data["selection_record_id"], code="selection_record_schema_invalid"),
        )
        record.validate()
        return record

    @classmethod
    def from_bytes(cls, raw_bytes: bytes) -> "D17Path3InputSelectionRecord":
        return cls.from_dict(_strict_json_loads(raw_bytes, code="selection_record_schema_invalid"))

    @staticmethod
    def _input_view_id(
        *,
        manifest_id: str,
        provider_visible_input_sha256: str,
        provider_visible_input_byte_length: int,
    ) -> str:
        return _id(
            "d17-input-view-",
            {
                "manifest_id": manifest_id,
                "field_policy_identity": D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
                "provider_visible_input_sha256": provider_visible_input_sha256,
                "provider_visible_input_byte_length": provider_visible_input_byte_length,
            },
        )

    @classmethod
    def _root_for(
        cls,
        *,
        manifest: D17Path3TierAManifest,
        original_requirement_source_class: str,
        local_context_sha256: str,
        provider_visible_input_sha256: str,
        provider_visible_input_byte_length: int,
    ) -> dict[str, object]:
        return {
            "schema_version": D17_PATH3_INPUT_SELECTION_RECORD_SCHEMA_VERSION,
            "authorization": _AUTHORIZATION,
            "d17_path": _PATH_3,
            "local_only": True,
            "not_sent": True,
            "external_egress_allowed": False,
            "manifest_id": manifest.manifest_id,
            "field_policy_schema_version": D17_PATH3_TIER_A_FIELD_POLICY_SCHEMA_VERSION,
            "field_policy_identity": D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
            "field_policy_snapshot_sha256": manifest.field_policy.approved_snapshot_sha256(),
            "original_requirement_source_class": original_requirement_source_class,
            "local_context_sha256": local_context_sha256,
            "provider_visible_input_sha256": provider_visible_input_sha256,
            "provider_visible_input_byte_length": provider_visible_input_byte_length,
            "input_view_id": cls._input_view_id(
                manifest_id=manifest.manifest_id,
                provider_visible_input_sha256=provider_visible_input_sha256,
                provider_visible_input_byte_length=provider_visible_input_byte_length,
            ),
        }

    def _root(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "authorization": self.authorization,
            "d17_path": self.d17_path,
            "local_only": self.local_only,
            "not_sent": self.not_sent,
            "external_egress_allowed": self.external_egress_allowed,
            "manifest_id": self.manifest_id,
            "field_policy_schema_version": self.field_policy_schema_version,
            "field_policy_identity": self.field_policy_identity,
            "field_policy_snapshot_sha256": self.field_policy_snapshot_sha256,
            "original_requirement_source_class": self.original_requirement_source_class,
            "local_context_sha256": self.local_context_sha256,
            "provider_visible_input_sha256": self.provider_visible_input_sha256,
            "provider_visible_input_byte_length": self.provider_visible_input_byte_length,
            "input_view_id": self.input_view_id,
        }

    def validate(
        self,
        *,
        manifest: D17Path3TierAManifest | None = None,
        provider_visible_input: D17Path3ProviderVisibleInputView | None = None,
    ) -> None:
        if self.schema_version != D17_PATH3_INPUT_SELECTION_RECORD_SCHEMA_VERSION:
            _fail("selection_record_schema_invalid")
        if self.authorization != _AUTHORIZATION or self.d17_path != _PATH_3:
            _fail("selection_record_schema_invalid")
        if self.local_only is not True or self.not_sent is not True or self.external_egress_allowed is not False:
            _fail("local_only_boundary_invalid")
        if self.field_policy_schema_version != D17_PATH3_TIER_A_FIELD_POLICY_SCHEMA_VERSION or self.field_policy_identity != D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY:
            _fail("selection_record_cross_binding_invalid")
        if manifest is not None:
            _validate_source_class(self.original_requirement_source_class, manifest)
        elif self.original_requirement_source_class not in _SOURCE_CLASSES:
            _fail("source_class_invalid")
        _require_sha256(self.field_policy_snapshot_sha256, code="selection_record_schema_invalid")
        _require_sha256(self.local_context_sha256, code="selection_record_schema_invalid")
        _require_sha256(self.provider_visible_input_sha256, code="selection_record_schema_invalid")
        _require_byte_length(self.provider_visible_input_byte_length, code="selection_record_schema_invalid")
        expected_view_id = self._input_view_id(
            manifest_id=self.manifest_id,
            provider_visible_input_sha256=self.provider_visible_input_sha256,
            provider_visible_input_byte_length=self.provider_visible_input_byte_length,
        )
        if self.input_view_id != expected_view_id:
            _fail("selection_record_cross_binding_invalid")
        if self.selection_record_id != _id("d17-input-selection-", self._root()):
            _fail("selection_record_identity_invalid")
        if manifest is not None:
            manifest = _validated_manifest(manifest)
            _validated_manifest_policy(manifest)
            if (
                self.manifest_id != manifest.manifest_id
                or self.field_policy_snapshot_sha256
                != manifest.field_policy.approved_snapshot_sha256()
            ):
                _fail("selection_record_cross_binding_invalid")
        if provider_visible_input is not None:
            provider_visible_input.validate(manifest)
            provider_bytes = provider_visible_input.canonical_bytes()
            if (
                self.provider_visible_input_sha256 != _sha256_bytes(provider_bytes)
                or self.provider_visible_input_byte_length != len(provider_bytes)
            ):
                _fail("selection_record_cross_binding_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        payload = self._root()
        payload["selection_record_id"] = self.selection_record_id
        return payload

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256_bytes(self.canonical_bytes())


@dataclass(frozen=True)
class D17Path3SelectedInput:
    """Pairing returned by the selector without constructing a request artifact."""

    provider_visible_input: D17Path3ProviderVisibleInputView
    selection_record: D17Path3InputSelectionRecord

    def validate(self, manifest: D17Path3TierAManifest | None = None) -> None:
        self.provider_visible_input.validate(manifest)
        self.selection_record.validate(
            manifest=manifest, provider_visible_input=self.provider_visible_input
        )

    def validate_against(
        self, context: AgentContextBundle, manifest: D17Path3TierAManifest
    ) -> None:
        context = _validate_context(context)
        manifest = _validated_manifest(manifest)
        self.validate(manifest)
        expected_view = D17Path3ProviderVisibleInputView.from_context(context, manifest)
        expected_record = D17Path3InputSelectionRecord.create(
            context=context,
            manifest=manifest,
            original_requirement_source_class=(
                self.selection_record.original_requirement_source_class
            ),
            provider_visible_input=expected_view,
        )
        if (
            self.provider_visible_input.to_dict() != expected_view.to_dict()
            or self.selection_record.to_dict() != expected_record.to_dict()
        ):
            _fail("selection_record_cross_binding_invalid")


def select_d17_path3_provider_input(
    context: AgentContextBundle,
    manifest: D17Path3TierAManifest,
    *,
    original_requirement_source_class: str,
) -> D17Path3SelectedInput:
    """Select the six approved categories by explicit field construction only."""

    context = _validate_context(context)
    manifest = _validated_manifest(manifest)
    original_requirement_source_class = _validate_source_class(
        original_requirement_source_class, manifest
    )
    provider_visible_input = D17Path3ProviderVisibleInputView.from_context(context, manifest)
    selection_record = D17Path3InputSelectionRecord.create(
        context=context,
        manifest=manifest,
        original_requirement_source_class=original_requirement_source_class,
        provider_visible_input=provider_visible_input,
    )
    selected = D17Path3SelectedInput(provider_visible_input, selection_record)
    selected.validate(manifest)
    return selected
