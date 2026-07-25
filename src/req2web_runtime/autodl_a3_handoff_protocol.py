"""No-action handoff and remote evidence replay artifacts for AutoDL A3."""
from __future__ import annotations

import hashlib
import json
import re
import weakref
from types import MappingProxyType

from . import autodl_action_time_authority as action_authority
from .autodl_action_time_authority import (
    ACTION_TIME_SIGNATURE_ALGORITHM,
    ACTION_TIME_SIGNATURE_FORMAT,
    ActionTimeAuthorityRequest,
)


class A3HandoffProtocolError(ValueError):
    """Raised when a structural handoff artifact is invalid."""


def _build_authorities():
    bool_type = bool
    dict_type = dict
    list_type = list
    str_type = str
    type_fn = type
    len_fn = len
    set_type = set
    bytes_type = bytes
    dict_copy = dict
    list_copy = list
    tuple_fn = tuple
    enumerate_fn = enumerate
    id_fn = id
    object_new = object.__new__
    weakref_ref = weakref.ref
    type_error = TypeError
    value_error = ValueError
    unicode_decode_error = UnicodeDecodeError
    json_decode_error = json.JSONDecodeError
    request_type = ActionTimeAuthorityRequest
    request_parse_trusted = action_authority._parse_action_time_request_for_trust
    request_to_dict_trusted = action_authority._action_time_request_to_dict_for_trust
    request_canonical_trusted = action_authority._action_time_request_canonical_for_trust
    request_sha256_trusted = action_authority._action_time_request_sha256_for_trust
    error_type = A3HandoffProtocolError
    json_loads = json.loads
    json_dumps = json.dumps
    sha256_fn = hashlib.sha256
    regex_fullmatch = re.fullmatch
    mapping_proxy_type = MappingProxyType
    signature_algorithm = ACTION_TIME_SIGNATURE_ALGORITHM
    signature_format = ACTION_TIME_SIGNATURE_FORMAT

    capsule_schema = "req2web.runtime.a3_handoff_capsule_template.v1"
    replay_schema = "req2web.runtime.a3_remote_evidence_replay.v1"
    capsule_keys = (
        "schema_version", "capsule_id", "status", "request_id",
        "request_sha256", "algorithm", "format", "signed_field_set",
        "payload_sha256", "signature_bytes_present", "signer_key_loaded",
        "permit_issued", "signature_verified", "destructive_action_authorized",
        "external_action_executed", "cleanup_complete", "manager_consumable",
        "next_run_allowed", "a2_unlocked", "h1_allowed",
        "formal_quality_allowed",
    )
    replay_keys = (
        "schema_version", "replay_id", "status", "request_id",
        "request_sha256", "capsule_id", "capsule_sha256", "evidence_bundle_id",
        "evidence_bundle_sha256", "process_observation_state",
        "project_deletion_state", "instance_release_state",
        "credential_revocation_state", "signature_verified",
        "destructive_action_authorized", "external_action_executed",
        "cleanup_complete", "manager_consumable", "next_run_allowed",
        "a2_unlocked", "h1_allowed", "formal_quality_allowed",
    )
    signed_fields = (
        "action_time_git_sha", "approved_disposition", "d17",
        "deployment_manifest", "a1", "model_inventory", "endpoint",
        "root_contracts", "a3", "executor_package", "signature_profile",
        "nonce", "trigger_kind",
    )

    def exact_map(value, keys, code):
        if type_fn(value) is not dict_type or len_fn(value) != len_fn(keys) or set_type(value.keys()) != set_type(keys):
            raise error_type(code)
        return value

    def req_str(value, code):
        if type_fn(value) is not str_type or not value:
            raise error_type(code)
        return value

    def req_id(value, code):
        req_str(value, code)
        if regex_fullmatch(r"[a-z0-9][a-z0-9._:-]{7,159}", value) is None:
            raise error_type(code)
        return value

    def req_hash(value, code):
        if type_fn(value) is not str_type or regex_fullmatch(r"[0-9a-f]{64}", value) is None:
            raise error_type(code)
        return value

    def req_bool(value, expected, code):
        if type_fn(value) is not bool_type or value is not expected:
            raise error_type(code)
        return value

    def canonical(value):
        try:
            return json_dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (type_error, value_error) as exc:
            raise error_type("a3_handoff_canonical_value_invalid") from exc

    def digest(raw):
        if type_fn(raw) is not bytes_type:
            raise error_type("a3_handoff_digest_bytes_required")
        return sha256_fn(raw).hexdigest()

    def parse_json(raw, code):
        if type_fn(raw) is not bytes_type:
            raise error_type(code)
        try:
            value = json_loads(raw.decode("utf-8"))
        except (unicode_decode_error, json_decode_error) as exc:
            raise error_type(code) from exc
        if canonical(value) != raw:
            raise error_type(code)
        return value

    def identity(prefix, value, key):
        root = dict_copy(value)
        root.pop(key)
        return prefix + digest(canonical(root))[:20]

    def request_snapshot(request):
        if type_fn(request) is not request_type:
            raise type_error("action_time_authority_request_exact_type_required")
        raw = request_canonical_trusted(request)
        parsed = request_parse_trusted(raw)
        if request_canonical_trusted(parsed) != raw:
            raise type_error("action_time_authority_request_snapshot_invalid")
        return parsed

    def validate_capsule(value):
        data = exact_map(value, capsule_keys, "a3_handoff_capsule_exact_keys_invalid")
        if data["schema_version"] != capsule_schema or data["status"] != "detached_signature_capsule_template_no_action":
            raise error_type("a3_handoff_capsule_schema_or_status_invalid")
        req_id(data["capsule_id"], "a3_handoff_capsule_id_invalid")
        req_id(data["request_id"], "a3_handoff_capsule_request_invalid")
        req_hash(data["request_sha256"], "a3_handoff_capsule_request_invalid")
        if data["algorithm"] != signature_algorithm or data["format"] != signature_format:
            raise error_type("a3_handoff_signature_profile_invalid")
        if type_fn(data["signed_field_set"]) is not list_type or tuple_fn(data["signed_field_set"]) != signed_fields:
            raise error_type("a3_handoff_signed_field_set_invalid")
        req_hash(data["payload_sha256"], "a3_handoff_payload_sha256_invalid")
        for key in (
            "signature_bytes_present", "signer_key_loaded", "permit_issued",
            "signature_verified", "destructive_action_authorized",
            "external_action_executed", "cleanup_complete", "manager_consumable",
            "next_run_allowed", "a2_unlocked", "h1_allowed",
            "formal_quality_allowed",
        ):
            req_bool(data[key], False, "a3_handoff_public_authority_forbidden")
        if data["capsule_id"] != identity("a3-handoff-capsule-", data, "capsule_id"):
            raise error_type("a3_handoff_capsule_identity_invalid")
        return data

    capsule_registry = {}

    def capsule_snapshot_bytes(instance):
        if type_fn(instance) is not capsule_type:
            raise type_error("a3_handoff_capsule_exact_type_required")
        entry = capsule_registry.get(id_fn(instance))
        if entry is None or entry[0]() is not instance:
            raise type_error("a3_handoff_capsule_not_registered")
        raw = entry[1]
        validate_capsule(parse_json(raw, "a3_handoff_capsule_snapshot_invalid"))
        return raw

    class A3HandoffCapsuleTemplate:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            raise type_error("a3_handoff_capsule_factory_required")

        @classmethod
        def from_bytes(cls, raw):
            if cls is not A3HandoffCapsuleTemplate:
                raise type_error("a3_handoff_capsule_exact_type_required")
            return parse_capsule_trusted(raw)

        @property
        def data(self):
            return mapping_proxy_type(dict_copy(parse_json(capsule_snapshot_bytes(self), "a3_handoff_capsule_snapshot_invalid")))

        def to_dict(self):
            return dict_copy(validate_capsule(parse_json(capsule_snapshot_bytes(self), "a3_handoff_capsule_snapshot_invalid")))

        def canonical_bytes(self):
            return canonical(validate_capsule(parse_json(capsule_snapshot_bytes(self), "a3_handoff_capsule_snapshot_invalid")))

        def sha256(self):
            return digest(canonical(validate_capsule(parse_json(capsule_snapshot_bytes(self), "a3_handoff_capsule_snapshot_invalid"))))

    capsule_type = A3HandoffCapsuleTemplate
    capsule_to_dict_impl = A3HandoffCapsuleTemplate.to_dict
    capsule_canonical_impl = A3HandoffCapsuleTemplate.canonical_bytes
    capsule_sha256_impl = A3HandoffCapsuleTemplate.sha256

    def construct_capsule_trusted(data):
        raw = canonical(dict_copy(validate_capsule(data)))
        instance = object_new(capsule_type)
        identity_key = id_fn(instance)

        def discard(stored_ref, identity=identity_key):
            current = capsule_registry.get(identity)
            if current is not None and current[0] is stored_ref:
                capsule_registry.pop(identity, None)

        instance_ref = weakref_ref(instance, discard)
        capsule_registry[identity_key] = (instance_ref, raw)
        return instance

    def parse_capsule_trusted(raw):
        return construct_capsule_trusted(parse_json(raw, "a3_handoff_capsule_bytes_invalid"))

    def capsule_to_dict_trusted(instance):
        return capsule_to_dict_impl(instance)

    def capsule_canonical_trusted(instance):
        return capsule_canonical_impl(instance)

    def capsule_sha256_trusted(instance):
        return capsule_sha256_impl(instance)

    def create_capsule(request):
        parsed = request_snapshot(request)
        request_data = request_to_dict_trusted(parsed)
        payload = {key: request_data[key] for key in signed_fields}
        root = {
            "schema_version": capsule_schema,
            "capsule_id": "pending",
            "status": "detached_signature_capsule_template_no_action",
            "request_id": request_data["request_id"],
            "request_sha256": request_sha256_trusted(parsed),
            "algorithm": signature_algorithm,
            "format": signature_format,
            "signed_field_set": list_copy(signed_fields),
            "payload_sha256": digest(canonical(payload)),
            "signature_bytes_present": False,
            "signer_key_loaded": False,
            "permit_issued": False,
            "signature_verified": False,
            "destructive_action_authorized": False,
            "external_action_executed": False,
            "cleanup_complete": False,
            "manager_consumable": False,
            "next_run_allowed": False,
            "a2_unlocked": False,
            "h1_allowed": False,
            "formal_quality_allowed": False,
        }
        root["capsule_id"] = identity("a3-handoff-capsule-", root, "capsule_id")
        return construct_capsule_trusted(root)

    def validate_replay(value):
        data = exact_map(value, replay_keys, "a3_remote_replay_exact_keys_invalid")
        if data["schema_version"] != replay_schema or data["status"] not in (
            "remote_evidence_structural_replay_complete",
            "remote_evidence_structural_replay_incomplete",
        ):
            raise error_type("a3_remote_replay_schema_or_status_invalid")
        for key in ("replay_id", "request_id", "capsule_id", "evidence_bundle_id"):
            req_id(data[key], "a3_remote_replay_id_invalid")
        for key in ("request_sha256", "capsule_sha256", "evidence_bundle_sha256"):
            req_hash(data[key], "a3_remote_replay_hash_invalid")
        states = {
            "process_observation_state": ("structurally_present", "missing_or_invalid"),
            "project_deletion_state": ("structurally_present", "missing_or_invalid"),
            "instance_release_state": ("deferred_external_browser_observation",),
            "credential_revocation_state": ("deferred_external_manager_observation",),
        }
        for key, allowed in states.items():
            if data[key] not in allowed:
                raise error_type("a3_remote_replay_state_invalid")
        for key in (
            "signature_verified", "destructive_action_authorized",
            "external_action_executed", "cleanup_complete", "manager_consumable",
            "next_run_allowed", "a2_unlocked", "h1_allowed",
            "formal_quality_allowed",
        ):
            req_bool(data[key], False, "a3_remote_replay_authority_forbidden")
        if data["replay_id"] != identity("a3-remote-replay-", data, "replay_id"):
            raise error_type("a3_remote_replay_identity_invalid")
        return data

    class A3RemoteEvidenceReplay:
        __slots__ = ("__bytes",)

        def __init__(self, data):
            self.__bytes = canonical(dict_copy(validate_replay(data)))

        @classmethod
        def from_bytes(cls, raw):
            if cls is not A3RemoteEvidenceReplay:
                raise type_error("a3_remote_replay_exact_type_required")
            return cls(parse_json(raw, "a3_remote_replay_bytes_invalid"))

        def to_dict(self):
            return dict_copy(validate_replay(parse_json(self.__bytes, "a3_remote_replay_snapshot_invalid")))

        def canonical_bytes(self):
            return canonical(validate_replay(parse_json(self.__bytes, "a3_remote_replay_snapshot_invalid")))

        def sha256(self):
            return digest(canonical(validate_replay(parse_json(self.__bytes, "a3_remote_replay_snapshot_invalid"))))

    replay_type = A3RemoteEvidenceReplay
    replay_init_impl = A3RemoteEvidenceReplay.__init__
    replay_to_dict_impl = A3RemoteEvidenceReplay.to_dict
    replay_canonical_impl = A3RemoteEvidenceReplay.canonical_bytes
    replay_sha256_impl = A3RemoteEvidenceReplay.sha256

    def construct_replay_trusted(data):
        instance = object_new(replay_type)
        replay_init_impl(instance, data)
        return instance

    def parse_replay_trusted(raw):
        return construct_replay_trusted(parse_json(raw, "a3_remote_replay_bytes_invalid"))

    def create_replay(request, capsule, *, evidence_bundle_id, evidence_bundle_sha256, structurally_complete):
        parsed_request = request_snapshot(request)
        if type_fn(capsule) is not capsule_type:
            raise type_error("a3_handoff_capsule_exact_type_required")
        parsed_capsule = parse_capsule_trusted(capsule_canonical_trusted(capsule))
        request_data = request_to_dict_trusted(parsed_request)
        capsule_data = capsule_to_dict_trusted(parsed_capsule)
        if capsule_data["request_id"] != request_data["request_id"] or capsule_data["request_sha256"] != request_sha256_trusted(parsed_request):
            raise error_type("a3_remote_replay_cross_bundle_invalid")
        req_id(evidence_bundle_id, "a3_remote_replay_evidence_id_invalid")
        req_hash(evidence_bundle_sha256, "a3_remote_replay_evidence_hash_invalid")
        if type_fn(structurally_complete) is not bool_type:
            raise error_type("a3_remote_replay_complete_flag_invalid")
        root = {
            "schema_version": replay_schema,
            "replay_id": "pending",
            "status": "remote_evidence_structural_replay_complete" if structurally_complete else "remote_evidence_structural_replay_incomplete",
            "request_id": request_data["request_id"],
            "request_sha256": request_sha256_trusted(parsed_request),
            "capsule_id": capsule_data["capsule_id"],
            "capsule_sha256": capsule_sha256_trusted(parsed_capsule),
            "evidence_bundle_id": evidence_bundle_id,
            "evidence_bundle_sha256": evidence_bundle_sha256,
            "process_observation_state": "structurally_present" if structurally_complete else "missing_or_invalid",
            "project_deletion_state": "structurally_present" if structurally_complete else "missing_or_invalid",
            "instance_release_state": "deferred_external_browser_observation",
            "credential_revocation_state": "deferred_external_manager_observation",
            "signature_verified": False,
            "destructive_action_authorized": False,
            "external_action_executed": False,
            "cleanup_complete": False,
            "manager_consumable": False,
            "next_run_allowed": False,
            "a2_unlocked": False,
            "h1_allowed": False,
            "formal_quality_allowed": False,
        }
        root["replay_id"] = identity("a3-remote-replay-", root, "replay_id")
        return construct_replay_trusted(root)

    return {
        "A3HandoffCapsuleTemplate": A3HandoffCapsuleTemplate,
        "A3RemoteEvidenceReplay": A3RemoteEvidenceReplay,
        "create_a3_handoff_capsule_template": create_capsule,
        "create_a3_remote_evidence_replay": create_replay,
        "validate_a3_handoff_capsule_template_bytes": parse_capsule_trusted,
        "validate_a3_remote_evidence_replay_bytes": parse_replay_trusted,
        "_parse_a3_handoff_capsule_for_trust": parse_capsule_trusted,
        "_a3_handoff_capsule_to_dict_for_trust": capsule_to_dict_trusted,
        "_a3_handoff_capsule_canonical_for_trust": capsule_canonical_trusted,
        "_a3_handoff_capsule_sha256_for_trust": capsule_sha256_trusted,
        "_parse_a3_remote_replay_for_trust": parse_replay_trusted,
        "_canonical_for_tests": canonical,
        "_digest_for_tests": digest,
        "A3_HANDOFF_SIGNED_FIELD_SET": signed_fields,
    }


_AUTHORITIES = _build_authorities()
globals().update(_AUTHORITIES)

__all__ = [
    "A3HandoffProtocolError", "A3HandoffCapsuleTemplate",
    "A3RemoteEvidenceReplay", "create_a3_handoff_capsule_template",
    "create_a3_remote_evidence_replay",
    "validate_a3_handoff_capsule_template_bytes",
    "validate_a3_remote_evidence_replay_bytes",
    "A3_HANDOFF_SIGNED_FIELD_SET",
]
