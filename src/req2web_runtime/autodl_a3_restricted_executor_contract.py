"""Canonical no-action future restricted-executor contract.

This module records structural transport restrictions only.  It never opens a
network channel, invokes a process, reads credentials, or loads a model.
"""
from __future__ import annotations

import hashlib
import json
import re
import weakref

from . import autodl_a3_action_time_issuer as issuer_module
from . import autodl_a3_action_time_coordinator as coordinator_module
from . import autodl_a3_handoff_protocol as handoff_module
from . import autodl_a3_authenticated_executor_contract as authenticated_module


class A3RestrictedExecutorContractError(ValueError):
    """Raised when a no-action restricted-executor contract is invalid."""


def _build_authorities():
    bool_type, bytes_type, dict_type, list_type, str_type = bool, bytes, dict, list, str
    type_fn, len_fn, set_type, tuple_fn, id_fn = type, len, set, tuple, id
    type_error, value_error, object_new, weakref_ref = TypeError, ValueError, object.__new__, weakref.ref
    unicode_decode_error, json_decode_error = UnicodeDecodeError, json.JSONDecodeError
    json_loads, json_dumps, sha256_fn, regex_fullmatch = json.loads, json.dumps, hashlib.sha256, re.fullmatch
    error_type = A3RestrictedExecutorContractError

    intent_type = issuer_module.ActionTimeIssuanceIntent
    intent_canonical = issuer_module._action_time_issuance_intent_canonical_for_trust
    intent_parse = issuer_module._parse_action_time_issuance_intent_for_trust
    intent_to_dict = issuer_module._action_time_issuance_intent_to_dict_for_trust
    intent_sha = issuer_module._action_time_issuance_intent_sha256_for_trust
    intent_eligible = issuer_module._action_time_issuance_intent_ledger_eligible_for_trust
    result_type = coordinator_module.A3ActionTimeCoordinatorResult
    result_request_getter = coordinator_module.A3ActionTimeCoordinatorResult.request.fget
    result_capsule_getter = coordinator_module.A3ActionTimeCoordinatorResult.capsule.fget
    result_contract_getter = coordinator_module.A3ActionTimeCoordinatorResult.executor_contract.fget
    request_canonical = authenticated_module.action_authority._action_time_request_canonical_for_trust
    request_to_dict = authenticated_module.action_authority._action_time_request_to_dict_for_trust
    request_sha = authenticated_module.action_authority._action_time_request_sha256_for_trust
    request_parse = authenticated_module.action_authority._parse_action_time_request_for_trust
    capsule_canonical = handoff_module._a3_handoff_capsule_canonical_for_trust
    capsule_to_dict = handoff_module._a3_handoff_capsule_to_dict_for_trust
    capsule_sha = handoff_module._a3_handoff_capsule_sha256_for_trust
    capsule_parse = handoff_module._parse_a3_handoff_capsule_for_trust
    authenticated_canonical = authenticated_module._a3_authenticated_executor_contract_canonical_for_trust
    authenticated_to_dict = authenticated_module._a3_authenticated_executor_contract_to_dict_for_trust
    authenticated_sha = authenticated_module._a3_authenticated_executor_contract_sha256_for_trust
    authenticated_parse = authenticated_module._parse_a3_authenticated_executor_contract_for_trust

    schema = "req2web.runtime.a3_restricted_executor_contract.v1"
    status = "restricted_executor_transport_declared_no_action"
    keys = (
        "schema_version", "contract_id", "status", "issuance_id", "issuance_sha256",
        "request_id", "request_sha256", "capsule_id", "capsule_sha256",
        "authenticated_contract_id", "authenticated_contract_sha256", "endpoint",
        "identities", "worker_id_sha256", "package", "model_inventory", "root_contracts",
        "profile", "gpu_id_sha256", "fixed_command_sha256", "capsule_command_sha256",
        "transport_restrictions", "single_use", "no_retry", "no_second_provision",
        "permit_issued", "signature_verified", "destructive_action_authorized",
        "external_action_executed", "cleanup_complete", "manager_consumable",
        "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed",
    )
    binding_keys = (
        "bootstrap_identity", "restricted_executor_identity", "worker_id_sha256",
        "gpu_id_sha256", "fixed_command_sha256", "capsule_command_sha256",
    )
    identity_keys = ("identity", "identity_sha256")
    restriction_keys = ("pty", "port_forwarding", "agent_forwarding", "x11_forwarding", "interactive_shell")
    registry = {}

    def exact_map(value, expected, code):
        if type_fn(value) is not dict_type or len_fn(value) != len_fn(expected) or set_type(value.keys()) != set_type(expected):
            raise error_type(code)
        return value

    def req_id(value, code):
        if type_fn(value) is not str_type or regex_fullmatch(r"[a-z0-9][a-z0-9._:-]{7,159}", value) is None:
            raise error_type(code)
        return value

    def req_hash(value, code):
        if type_fn(value) is not str_type or regex_fullmatch(r"[0-9a-f]{64}", value) is None:
            raise error_type(code)
        return value

    def req_identity(value, code):
        exact_map(value, identity_keys, code)
        if type_fn(value["identity"]) is not str_type or regex_fullmatch(r"[a-z0-9][a-z0-9._:-]{2,159}", value["identity"]) is None:
            raise error_type(code)
        req_hash(value["identity_sha256"], code)
        if digest(value["identity"].encode("utf-8")) != value["identity_sha256"]:
            raise error_type(code)
        return value

    def canonical(value):
        try:
            return json_dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (type_error, value_error) as exc:
            raise error_type("a3_restricted_executor_contract_canonical_invalid") from exc

    def digest(raw):
        if type_fn(raw) is not bytes_type:
            raise error_type("a3_restricted_executor_contract_digest_bytes_required")
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
        root = dict_type(value); root.pop("contract_id")
        return "a3-restricted-executor-" + digest(canonical(root))[:20]

    def no_action_flags(data):
        for key in ("permit_issued", "signature_verified", "destructive_action_authorized", "external_action_executed", "cleanup_complete", "manager_consumable", "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed"):
            if type_fn(data[key]) is not bool_type or data[key] is not False:
                raise error_type("a3_restricted_executor_contract_public_authority_forbidden")
        for key in ("single_use", "no_retry", "no_second_provision"):
            if type_fn(data[key]) is not bool_type or data[key] is not True:
                raise error_type("a3_restricted_executor_contract_replay_policy_invalid")

    def validate(data):
        exact_map(data, keys, "a3_restricted_executor_contract_exact_keys_invalid")
        if data["schema_version"] != schema or data["status"] != status:
            raise error_type("a3_restricted_executor_contract_schema_or_status_invalid")
        for key in ("contract_id", "issuance_id", "request_id", "capsule_id", "authenticated_contract_id"):
            req_id(data[key], "a3_restricted_executor_contract_id_invalid")
        for key in ("issuance_sha256", "request_sha256", "capsule_sha256", "authenticated_contract_sha256", "worker_id_sha256", "gpu_id_sha256", "fixed_command_sha256", "capsule_command_sha256"):
            req_hash(data[key], "a3_restricted_executor_contract_hash_invalid")
        exact_map(data["endpoint"], ("instance_id_hash", "ssh_host_fingerprint_sha256", "executor_fingerprint_sha256", "credential_fingerprint_sha256"), "a3_restricted_executor_contract_endpoint_invalid")
        for value in data["endpoint"].values(): req_hash(value, "a3_restricted_executor_contract_endpoint_invalid")
        exact_map(data["identities"], ("bootstrap", "restricted_executor"), "a3_restricted_executor_contract_identity_invalid")
        req_identity(data["identities"]["bootstrap"], "a3_restricted_executor_contract_identity_invalid")
        req_identity(data["identities"]["restricted_executor"], "a3_restricted_executor_contract_identity_invalid")
        if data["identities"]["bootstrap"] == data["identities"]["restricted_executor"]:
            raise error_type("a3_restricted_executor_contract_identity_separation_invalid")
        exact_map(data["package"], ("package_id", "package_sha256", "tree_sha256"), "a3_restricted_executor_contract_package_invalid")
        req_id(data["package"]["package_id"], "a3_restricted_executor_contract_package_invalid")
        req_hash(data["package"]["package_sha256"], "a3_restricted_executor_contract_package_invalid"); req_hash(data["package"]["tree_sha256"], "a3_restricted_executor_contract_package_invalid")
        exact_map(data["model_inventory"], ("repository", "revision", "file_count", "total_bytes", "tree_sha256"), "a3_restricted_executor_contract_model_invalid")
        if type_fn(data["model_inventory"]["repository"]) is not str_type or not data["model_inventory"]["repository"] or type_fn(data["model_inventory"]["revision"]) is not str_type or not data["model_inventory"]["revision"] or type_fn(data["model_inventory"]["file_count"]) is not int or data["model_inventory"]["file_count"] < 0 or type_fn(data["model_inventory"]["total_bytes"]) is not int or data["model_inventory"]["total_bytes"] < 0:
            raise error_type("a3_restricted_executor_contract_model_invalid")
        req_hash(data["model_inventory"]["tree_sha256"], "a3_restricted_executor_contract_model_invalid")
        if type_fn(data["root_contracts"]) is not list_type or not data["root_contracts"]:
            raise error_type("a3_restricted_executor_contract_roots_invalid")
        if type_fn(data["profile"]) is not dict_type or type_fn(data["profile"].get("profile_name")) is not str_type or data["profile"]["profile_name"] not in ("quality_experiment", "local_smoke"):
            raise error_type("a3_restricted_executor_contract_profile_invalid")
        exact_map(data["transport_restrictions"], restriction_keys, "a3_restricted_executor_contract_transport_invalid")
        if any(type_fn(data["transport_restrictions"][key]) is not bool_type or data["transport_restrictions"][key] is not False for key in restriction_keys):
            raise error_type("a3_restricted_executor_contract_transport_invalid")
        no_action_flags(data)
        if data["contract_id"] != identity(data):
            raise error_type("a3_restricted_executor_contract_identity_invalid")
        return data

    class A3RestrictedExecutorContract:
        __slots__ = ("__weakref__",)
        def __new__(cls, *args, **kwargs):
            raise type_error("a3_restricted_executor_contract_factory_required")
        @classmethod
        def from_bytes(cls, raw):
            if cls is not A3RestrictedExecutorContract:
                raise type_error("a3_restricted_executor_contract_exact_type_required")
            return parse_trusted(raw)
        def to_dict(self): return dict_type(live_data(self))
        def canonical_bytes(self): return canonical(live_data(self))
        def sha256(self): return digest(canonical(live_data(self)))

    contract_type = A3RestrictedExecutorContract
    init_impl, to_dict_impl, canonical_impl, sha_impl = A3RestrictedExecutorContract.__new__, A3RestrictedExecutorContract.to_dict, A3RestrictedExecutorContract.canonical_bytes, A3RestrictedExecutorContract.sha256

    def registered_bytes(instance):
        if type_fn(instance) is not contract_type: raise type_error("a3_restricted_executor_contract_exact_type_required")
        entry = registry.get(id_fn(instance))
        if entry is None or entry[0]() is not instance: raise type_error("a3_restricted_executor_contract_not_registered")
        return entry[1]
    def live_data(instance): return validate(parse_json(registered_bytes(instance), "a3_restricted_executor_contract_snapshot_invalid"))
    def construct_trusted(data):
        raw = canonical(validate(data)); instance = object_new(contract_type); key = id_fn(instance)
        def discard(stored, identity_key=key):
            current = registry.get(identity_key)
            if current is not None and current[0] is stored: registry.pop(identity_key, None)
        ref = weakref_ref(instance, discard); registry[key] = (ref, raw); return instance
    def parse_trusted(raw): return construct_trusted(parse_json(raw, "a3_restricted_executor_contract_bytes_invalid"))

    def component_data(intent, bundle):
        if type_fn(intent) is not intent_type: raise type_error("action_time_issuance_intent_exact_type_required")
        intent_eligible(intent)
        parsed_intent = intent_parse(intent_canonical(intent))
        intent_data = intent_to_dict(parsed_intent)
        if type_fn(bundle) is not result_type: raise type_error("a3_coordinator_result_exact_type_required")
        request = request_parse(request_canonical(result_request_getter(bundle)))
        capsule = capsule_parse(capsule_canonical(result_capsule_getter(bundle)))
        authenticated = authenticated_parse(authenticated_canonical(result_contract_getter(bundle)))
        request_data, capsule_data, authenticated_data = request_to_dict(request), capsule_to_dict(capsule), authenticated_to_dict(authenticated)
        if (capsule_data["request_id"] != request_data["request_id"] or capsule_data["request_sha256"] != request_sha(request) or authenticated_data["request_id"] != request_data["request_id"] or authenticated_data["request_sha256"] != request_sha(request) or authenticated_data["capsule_id"] != capsule_data["capsule_id"] or authenticated_data["capsule_sha256"] != capsule_sha(capsule)):
            raise error_type("a3_restricted_executor_contract_cross_bundle_invalid")
        if intent_data["action_time_git_sha"] != request_data["action_time_git_sha"] or intent_data["deployment_manifest"]["source_commit_sha"] != request_data["deployment_manifest"]["source_commit_sha"]:
            raise error_type("a3_restricted_executor_contract_source_binding_invalid")
        for key in ("d17", "deployment_manifest", "model_inventory", "endpoint", "root_contracts", "a3", "executor_package", "nonce"):
            if canonical(intent_data[key]) != canonical(request_data[key]):
                raise error_type("a3_restricted_executor_contract_cross_bundle_invalid")
        return intent_data, request, request_data, capsule, capsule_data, authenticated, authenticated_data

    def create(intent, bundle, executor_binding):
        intent_data, request, request_data, capsule, capsule_data, authenticated, authenticated_data = component_data(intent, bundle)
        exact_map(executor_binding, binding_keys, "a3_restricted_executor_contract_binding_exact_keys_invalid")
        bootstrap, restricted = req_identity(executor_binding["bootstrap_identity"], "a3_restricted_executor_contract_binding_identity_invalid"), req_identity(executor_binding["restricted_executor_identity"], "a3_restricted_executor_contract_binding_identity_invalid")
        for key in ("worker_id_sha256", "gpu_id_sha256", "fixed_command_sha256", "capsule_command_sha256"): req_hash(executor_binding[key], "a3_restricted_executor_contract_binding_hash_invalid")
        root = {"schema_version": schema, "contract_id": "pending", "status": status,
          "issuance_id": intent_data["issuance_id"], "issuance_sha256": intent_sha(intent),
          "request_id": request_data["request_id"], "request_sha256": request_sha(request),
          "capsule_id": capsule_data["capsule_id"], "capsule_sha256": capsule_sha(capsule),
          "authenticated_contract_id": authenticated_data["contract_id"], "authenticated_contract_sha256": authenticated_sha(authenticated),
          "endpoint": dict_type(request_data["endpoint"]), "identities": {"bootstrap": dict_type(bootstrap), "restricted_executor": dict_type(restricted)},
          "worker_id_sha256": executor_binding["worker_id_sha256"], "package": dict_type(request_data["executor_package"]),
          "model_inventory": dict_type(request_data["model_inventory"]), "root_contracts": [dict_type(row) for row in request_data["root_contracts"]],
          "profile": dict_type(intent_data["profile"]), "gpu_id_sha256": executor_binding["gpu_id_sha256"],
          "fixed_command_sha256": executor_binding["fixed_command_sha256"], "capsule_command_sha256": executor_binding["capsule_command_sha256"],
          "transport_restrictions": {key: False for key in restriction_keys}, "single_use": True, "no_retry": True, "no_second_provision": True,
          "permit_issued": False, "signature_verified": False, "destructive_action_authorized": False, "external_action_executed": False, "cleanup_complete": False, "manager_consumable": False, "next_run_allowed": False, "a2_unlocked": False, "h1_allowed": False, "formal_quality_allowed": False}
        root["contract_id"] = identity(root)
        return construct_trusted(root)

    return {"A3RestrictedExecutorContract": A3RestrictedExecutorContract, "create_a3_restricted_executor_contract": create, "validate_a3_restricted_executor_contract_bytes": parse_trusted, "_canonical_for_tests": canonical, "_digest_for_tests": digest, "A3_RESTRICTED_EXECUTOR_TRANSPORT_RESTRICTIONS": restriction_keys}


_AUTHORITIES = _build_authorities()
globals().update(_AUTHORITIES)
__all__ = ["A3RestrictedExecutorContractError", "A3RestrictedExecutorContract", "create_a3_restricted_executor_contract", "validate_a3_restricted_executor_contract_bytes", "A3_RESTRICTED_EXECUTOR_TRANSPORT_RESTRICTIONS"]
