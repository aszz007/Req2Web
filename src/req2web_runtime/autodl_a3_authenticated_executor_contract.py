"""Structural contract for a future authenticated A3 executor.

No callable in this module opens SSH, reads a key, verifies a signature, executes a
command, writes remote evidence, or authorizes destructive work.
"""
from __future__ import annotations

import hashlib
import json
import re
import weakref

from . import autodl_action_time_authority as action_authority
from .autodl_action_time_authority import ActionTimeAuthorityRequest
from . import autodl_a3_handoff_protocol as handoff_protocol
from .autodl_a3_handoff_protocol import A3HandoffCapsuleTemplate


class A3AuthenticatedExecutorContractError(ValueError):
    """Raised when the no-action executor contract is invalid."""


def _build_authorities():
    bool_type = bool
    dict_type = dict
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
    capsule_type = A3HandoffCapsuleTemplate
    request_parse_trusted = action_authority._parse_action_time_request_for_trust
    request_to_dict_trusted = action_authority._action_time_request_to_dict_for_trust
    request_canonical_trusted = action_authority._action_time_request_canonical_for_trust
    request_sha256_trusted = action_authority._action_time_request_sha256_for_trust
    capsule_parse_trusted = handoff_protocol._parse_a3_handoff_capsule_for_trust
    capsule_to_dict_trusted = handoff_protocol._a3_handoff_capsule_to_dict_for_trust
    capsule_canonical_trusted = handoff_protocol._a3_handoff_capsule_canonical_for_trust
    capsule_sha256_trusted = handoff_protocol._a3_handoff_capsule_sha256_for_trust
    error_type = A3AuthenticatedExecutorContractError
    json_loads = json.loads
    json_dumps = json.dumps
    sha256_fn = hashlib.sha256
    regex_fullmatch = re.fullmatch

    schema = "req2web.runtime.a3_authenticated_executor_contract.v1"
    keys = (
        "schema_version", "contract_id", "status", "request_id",
        "request_sha256", "capsule_id", "capsule_sha256", "channel_mode",
        "host_key_pin_sha256", "executor_pin_sha256", "credential_pin_sha256",
        "signature_algorithm", "signature_format", "verification_phase",
        "capability_transport", "single_use", "no_retry",
        "no_second_provision", "permit_issued", "signature_verified",
        "destructive_action_authorized", "external_action_executed",
        "cleanup_complete", "manager_consumable", "next_run_allowed",
        "a2_unlocked", "h1_allowed", "formal_quality_allowed",
    )

    def exact_map(value, expected, code):
        if type_fn(value) is not dict_type or len_fn(value) != len_fn(expected) or set_type(value.keys()) != set_type(expected):
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
            raise error_type("a3_executor_contract_canonical_invalid") from exc

    def digest(raw):
        if type_fn(raw) is not bytes_type:
            raise error_type("a3_executor_contract_digest_bytes_required")
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

    def identity(value):
        root = dict_copy(value)
        root.pop("contract_id")
        return "a3-executor-contract-" + digest(canonical(root))[:20]

    def validate(value):
        data = exact_map(value, keys, "a3_executor_contract_exact_keys_invalid")
        if data["schema_version"] != schema or data["status"] != "authenticated_executor_protocol_declared_no_action":
            raise error_type("a3_executor_contract_schema_or_status_invalid")
        for key in ("contract_id", "request_id", "capsule_id"):
            req_id(data[key], "a3_executor_contract_id_invalid")
        for key in (
            "request_sha256", "capsule_sha256", "host_key_pin_sha256",
            "executor_pin_sha256", "credential_pin_sha256",
        ):
            req_hash(data[key], "a3_executor_contract_hash_invalid")
        if data["channel_mode"] != "pinned_ssh_channel" or data["signature_algorithm"] != "ed25519_openssh_detached" or data["signature_format"] != "openssh_detached_signature_capsule_v1":
            raise error_type("a3_executor_contract_channel_invalid")
        if data["verification_phase"] != "future_remote_before_first_os_action" or data["capability_transport"] != "future_authenticated_capsule_not_public_json_authority":
            raise error_type("a3_executor_contract_capability_boundary_invalid")
        req_bool(data["single_use"], True, "a3_executor_contract_policy_invalid")
        req_bool(data["no_retry"], True, "a3_executor_contract_policy_invalid")
        req_bool(data["no_second_provision"], True, "a3_executor_contract_policy_invalid")
        for key in (
            "permit_issued", "signature_verified", "destructive_action_authorized",
            "external_action_executed", "cleanup_complete", "manager_consumable",
            "next_run_allowed", "a2_unlocked", "h1_allowed",
            "formal_quality_allowed",
        ):
            req_bool(data[key], False, "a3_executor_contract_authority_forbidden")
        if data["contract_id"] != identity(data):
            raise error_type("a3_executor_contract_identity_invalid")
        return data

    contract_registry = {}

    def contract_snapshot_bytes(instance):
        if type_fn(instance) is not contract_type:
            raise type_error("a3_executor_contract_exact_type_required")
        entry = contract_registry.get(id_fn(instance))
        if entry is None or entry[0]() is not instance:
            raise type_error("a3_executor_contract_not_registered")
        raw = entry[1]
        validate(parse_json(raw, "a3_executor_contract_snapshot_invalid"))
        return raw

    class A3AuthenticatedExecutorContract:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            raise type_error("a3_executor_contract_factory_required")

        @classmethod
        def from_bytes(cls, raw):
            if cls is not A3AuthenticatedExecutorContract:
                raise type_error("a3_executor_contract_exact_type_required")
            return parse_contract_trusted(raw)

        @property
        def data(self):
            return dict_copy(parse_json(contract_snapshot_bytes(self), "a3_executor_contract_snapshot_invalid"))

        def to_dict(self):
            return dict_copy(validate(parse_json(contract_snapshot_bytes(self), "a3_executor_contract_snapshot_invalid")))

        def canonical_bytes(self):
            return canonical(validate(parse_json(contract_snapshot_bytes(self), "a3_executor_contract_snapshot_invalid")))

        def sha256(self):
            return digest(canonical(validate(parse_json(contract_snapshot_bytes(self), "a3_executor_contract_snapshot_invalid"))))

    contract_type = A3AuthenticatedExecutorContract
    contract_to_dict_impl = A3AuthenticatedExecutorContract.to_dict
    contract_canonical_impl = A3AuthenticatedExecutorContract.canonical_bytes
    contract_sha256_impl = A3AuthenticatedExecutorContract.sha256

    def construct_contract_trusted(data):
        raw = canonical(dict_copy(validate(data)))
        instance = object_new(contract_type)
        identity_key = id_fn(instance)

        def discard(stored_ref, identity=identity_key):
            current = contract_registry.get(identity)
            if current is not None and current[0] is stored_ref:
                contract_registry.pop(identity, None)

        instance_ref = weakref_ref(instance, discard)
        contract_registry[identity_key] = (instance_ref, raw)
        return instance

    def parse_contract_trusted(raw):
        return construct_contract_trusted(parse_json(raw, "a3_executor_contract_bytes_invalid"))

    def contract_to_dict_trusted(instance):
        return contract_to_dict_impl(instance)

    def contract_canonical_trusted(instance):
        return contract_canonical_impl(instance)

    def contract_sha256_trusted(instance):
        return contract_sha256_impl(instance)

    def create_contract(request, capsule):
        if type_fn(request) is not request_type:
            raise type_error("action_time_authority_request_exact_type_required")
        parsed_request = request_parse_trusted(request_canonical_trusted(request))
        if type_fn(capsule) is not capsule_type:
            raise type_error("a3_handoff_capsule_exact_type_required")
        parsed_capsule = capsule_parse_trusted(capsule_canonical_trusted(capsule))
        request_data = request_to_dict_trusted(parsed_request)
        capsule_data = capsule_to_dict_trusted(parsed_capsule)
        if capsule_data["request_id"] != request_data["request_id"] or capsule_data["request_sha256"] != request_sha256_trusted(parsed_request):
            raise error_type("a3_executor_contract_cross_bundle_invalid")
        endpoint = request_data["endpoint"]
        root = {
            "schema_version": schema,
            "contract_id": "pending",
            "status": "authenticated_executor_protocol_declared_no_action",
            "request_id": request_data["request_id"],
            "request_sha256": request_sha256_trusted(parsed_request),
            "capsule_id": capsule_data["capsule_id"],
            "capsule_sha256": capsule_sha256_trusted(parsed_capsule),
            "channel_mode": "pinned_ssh_channel",
            "host_key_pin_sha256": endpoint["ssh_host_fingerprint_sha256"],
            "executor_pin_sha256": endpoint["executor_fingerprint_sha256"],
            "credential_pin_sha256": endpoint["credential_fingerprint_sha256"],
            "signature_algorithm": "ed25519_openssh_detached",
            "signature_format": "openssh_detached_signature_capsule_v1",
            "verification_phase": "future_remote_before_first_os_action",
            "capability_transport": "future_authenticated_capsule_not_public_json_authority",
            "single_use": True,
            "no_retry": True,
            "no_second_provision": True,
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
        root["contract_id"] = identity(root)
        return construct_contract_trusted(root)

    return {
        "A3AuthenticatedExecutorContract": A3AuthenticatedExecutorContract,
        "create_a3_authenticated_executor_contract": create_contract,
        "validate_a3_authenticated_executor_contract_bytes": parse_contract_trusted,
        "_parse_a3_authenticated_executor_contract_for_trust": parse_contract_trusted,
        "_a3_authenticated_executor_contract_to_dict_for_trust": contract_to_dict_trusted,
        "_a3_authenticated_executor_contract_canonical_for_trust": contract_canonical_trusted,
        "_a3_authenticated_executor_contract_sha256_for_trust": contract_sha256_trusted,
        "_canonical_for_tests": canonical,
        "_digest_for_tests": digest,
    }


_AUTHORITIES = _build_authorities()
globals().update(_AUTHORITIES)

__all__ = [
    "A3AuthenticatedExecutorContractError", "A3AuthenticatedExecutorContract",
    "create_a3_authenticated_executor_contract",
    "validate_a3_authenticated_executor_contract_bytes",
]
