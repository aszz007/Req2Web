"""No-action action-time authority artifacts for Req2Web Stage 3.

This module records a future trusted handoff protocol.  It intentionally does
not issue permits, load keys, verify signatures, execute commands, or authorize
external/destructive actions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from types import MappingProxyType
import weakref


class ActionTimeAuthorityError(ValueError):
    """Raised when a no-action authority artifact is invalid."""


def _build_authorities():
    bool_type = bool
    dict_type = dict
    int_type = int
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
    type_error = TypeError
    value_error = ValueError
    unicode_decode_error = UnicodeDecodeError
    json_decode_error = json.JSONDecodeError
    error_type = ActionTimeAuthorityError
    json_loads = json.loads
    json_dumps = json.dumps
    sha256_fn = hashlib.sha256
    regex_fullmatch = re.fullmatch
    datetime_type = datetime
    timezone_utc = timezone.utc
    mapping_proxy_type = MappingProxyType
    weakref_ref = weakref.ref
    object_new = object.__new__

    request_schema = "req2web.runtime.action_time_authority_request.v1"
    ledger_schema = "req2web.runtime.action_time_nonce_ledger_snapshot.v1"
    trigger_kinds = ("a1_passed_closeout", "a1_failure_cleanup")
    nonce_states = ("issued", "consumed", "ambiguous", "expired", "replay")
    root_roles = ("work", "cache", "log", "transfer", "evidence")
    signature_algorithm = "ed25519_openssh_detached"
    signature_format = "openssh_detached_signature_capsule_v1"
    cleanup_mode = "delete_work_cache_release_instance_revoke_access"

    request_keys = (
        "schema_version", "request_id", "protocol_status", "trigger_kind",
        "action_time_git_sha", "approved_disposition", "d17",
        "deployment_manifest", "a1", "model_inventory", "endpoint",
        "root_contracts", "a3", "executor_package", "signature_profile",
        "nonce", "permit_issued", "signature_verified",
        "destructive_action_authorized", "external_action_executed",
        "cleanup_complete", "manager_consumable", "next_run_allowed",
        "a2_unlocked", "h1_allowed", "formal_quality_allowed",
    )
    disposition_keys = ("disposition_id", "disposition_sha256")
    d17_keys = (
        "manifest_id", "manifest_sha256", "manager_plan_id",
        "manager_plan_sha256", "field_policy_sha256",
    )
    deployment_keys = (
        "manifest_id", "manifest_sha256", "source_commit_sha",
        "package_tree_sha256",
    )
    a1_keys = (
        "no_action_plan_id", "no_action_plan_sha256", "operational_plan_id",
        "operational_plan_sha256", "controls_id", "controls_sha256",
        "gate_report_id", "gate_report_sha256", "gate_receipt_id",
        "gate_receipt_sha256", "gate_bundle_id", "gate_bundle_sha256",
        "gate_result_snapshot_sha256", "source_decision_status",
        "source_manager_a1_passed", "source_a2_unlocked",
    )
    model_keys = (
        "repository", "revision", "file_count", "total_bytes", "tree_sha256",
    )
    endpoint_keys = (
        "instance_id_hash", "ssh_host_fingerprint_sha256",
        "executor_fingerprint_sha256", "credential_fingerprint_sha256",
    )
    root_keys = (
        "role", "normalized_path_sha256", "marker_id", "marker_sha256",
        "inventory_tree_sha256",
    )
    a3_keys = (
        "policy_id", "policy_sha256", "trigger_id", "trigger_sha256",
        "cleanup_mode",
    )
    package_keys = ("package_id", "package_sha256", "tree_sha256")
    signature_keys = (
        "algorithm", "format", "issuer_public_key_fingerprint_sha256",
        "capsule_version", "real_key_loaded", "signature_bytes_present",
    )
    nonce_keys = (
        "nonce_id", "nonce_sha256", "issued_at_utc", "expires_at_utc",
        "single_use", "no_retry", "no_second_provision",
    )
    ledger_keys = (
        "schema_version", "ledger_id", "status", "entries",
        "live_authority", "permit_issued", "destructive_action_authorized",
        "external_action_executed", "manager_consumable",
    )
    ledger_row_keys = ("nonce_id", "nonce_sha256", "state", "transition_index")

    def exact_map(value, keys, code):
        if type_fn(value) is not dict_type or len_fn(value) != len_fn(keys) or set_type(value.keys()) != set_type(keys):
            raise error_type(code)
        return value

    def exact_list(value, code):
        if type_fn(value) is not list_type:
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

    def req_git_sha(value, code):
        if type_fn(value) is not str_type or regex_fullmatch(r"[0-9a-f]{40}", value) is None:
            raise error_type(code)
        return value

    def req_nonnegative(value, code):
        if type_fn(value) is not int_type or value < 0:
            raise error_type(code)
        return value

    def req_bool(value, expected, code):
        if type_fn(value) is not bool_type or value is not expected:
            raise error_type(code)
        return value

    def parse_utc(value, code):
        req_str(value, code)
        if not value.endswith("Z"):
            raise error_type(code)
        try:
            parsed = datetime_type.fromisoformat(value[:-1] + "+00:00")
        except value_error as exc:
            raise error_type(code) from exc
        if parsed.tzinfo != timezone_utc:
            raise error_type(code)
        return parsed

    def canonical(value):
        try:
            return json_dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (type_error, value_error) as exc:
            raise error_type("action_time_canonical_value_invalid") from exc

    def digest(raw):
        if type_fn(raw) is not bytes_type:
            raise error_type("action_time_digest_bytes_required")
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

    def derived_identity(prefix, value, key):
        root = dict_copy(value)
        root.pop(key)
        return prefix + digest(canonical(root))[:20]

    def validate_named_hash_pair(value, keys, code):
        data = exact_map(value, keys, code)
        req_id(data[keys[0]], code)
        req_hash(data[keys[1]], code)
        return data

    def validate_request(value):
        data = exact_map(value, request_keys, "action_time_request_exact_keys_invalid")
        if data["schema_version"] != request_schema:
            raise error_type("action_time_request_schema_invalid")
        req_id(data["request_id"], "action_time_request_id_invalid")
        if data["protocol_status"] != "structural_protocol_ready_no_action":
            raise error_type("action_time_request_status_invalid")
        if data["trigger_kind"] not in trigger_kinds:
            raise error_type("action_time_trigger_kind_invalid")
        req_git_sha(data["action_time_git_sha"], "action_time_git_sha_invalid")

        validate_named_hash_pair(
            data["approved_disposition"], disposition_keys,
            "action_time_disposition_invalid",
        )
        d17 = exact_map(data["d17"], d17_keys, "action_time_d17_invalid")
        req_id(d17["manifest_id"], "action_time_d17_invalid")
        req_hash(d17["manifest_sha256"], "action_time_d17_invalid")
        req_id(d17["manager_plan_id"], "action_time_d17_invalid")
        req_hash(d17["manager_plan_sha256"], "action_time_d17_invalid")
        req_hash(d17["field_policy_sha256"], "action_time_d17_invalid")

        deployment = exact_map(
            data["deployment_manifest"], deployment_keys,
            "action_time_deployment_manifest_invalid",
        )
        req_id(deployment["manifest_id"], "action_time_deployment_manifest_invalid")
        req_hash(deployment["manifest_sha256"], "action_time_deployment_manifest_invalid")
        req_git_sha(deployment["source_commit_sha"], "action_time_deployment_manifest_invalid")
        req_hash(deployment["package_tree_sha256"], "action_time_deployment_manifest_invalid")
        if deployment["source_commit_sha"] != data["action_time_git_sha"]:
            raise error_type("action_time_source_commit_binding_invalid")

        a1 = exact_map(data["a1"], a1_keys, "action_time_a1_binding_invalid")
        for key in (
            "no_action_plan_id", "operational_plan_id", "controls_id",
            "gate_report_id", "gate_receipt_id", "gate_bundle_id",
        ):
            req_id(a1[key], "action_time_a1_binding_invalid")
        for key in (
            "no_action_plan_sha256", "operational_plan_sha256", "controls_sha256",
            "gate_report_sha256", "gate_receipt_sha256", "gate_bundle_sha256",
            "gate_result_snapshot_sha256",
        ):
            req_hash(a1[key], "action_time_a1_binding_invalid")
        req_str(a1["source_decision_status"], "action_time_a1_binding_invalid")
        if type_fn(a1["source_manager_a1_passed"]) is not bool_type or type_fn(a1["source_a2_unlocked"]) is not bool_type:
            raise error_type("action_time_a1_binding_invalid")
        if data["trigger_kind"] == "a1_passed_closeout":
            if a1["source_manager_a1_passed"] is not True or a1["source_a2_unlocked"] is not True:
                raise error_type("action_time_pass_trigger_requires_manager_result")
        elif a1["source_manager_a1_passed"] is not False or a1["source_a2_unlocked"] is not False:
            raise error_type("action_time_failure_trigger_must_not_unlock_a2")

        model = exact_map(data["model_inventory"], model_keys, "action_time_model_inventory_invalid")
        req_str(model["repository"], "action_time_model_inventory_invalid")
        req_git_sha(model["revision"], "action_time_model_inventory_invalid")
        req_nonnegative(model["file_count"], "action_time_model_inventory_invalid")
        req_nonnegative(model["total_bytes"], "action_time_model_inventory_invalid")
        req_hash(model["tree_sha256"], "action_time_model_inventory_invalid")

        endpoint = exact_map(data["endpoint"], endpoint_keys, "action_time_endpoint_invalid")
        for key in endpoint_keys:
            req_hash(endpoint[key], "action_time_endpoint_invalid")

        rows = exact_list(data["root_contracts"], "action_time_root_contracts_invalid")
        if len_fn(rows) != len_fn(root_roles):
            raise error_type("action_time_root_contracts_invalid")
        for index, role in enumerate_fn(root_roles):
            row = exact_map(rows[index], root_keys, "action_time_root_contract_invalid")
            if row["role"] != role:
                raise error_type("action_time_root_role_order_invalid")
            req_hash(row["normalized_path_sha256"], "action_time_root_contract_invalid")
            req_id(row["marker_id"], "action_time_root_contract_invalid")
            req_hash(row["marker_sha256"], "action_time_root_contract_invalid")
            req_hash(row["inventory_tree_sha256"], "action_time_root_contract_invalid")
        if len_fn(set_type(row["normalized_path_sha256"] for row in rows)) != len_fn(rows):
            raise error_type("action_time_root_path_identity_duplicate")
        if len_fn(set_type(row["marker_id"] for row in rows)) != len_fn(rows):
            raise error_type("action_time_root_marker_identity_duplicate")

        a3 = exact_map(data["a3"], a3_keys, "action_time_a3_binding_invalid")
        req_id(a3["policy_id"], "action_time_a3_binding_invalid")
        req_hash(a3["policy_sha256"], "action_time_a3_binding_invalid")
        req_id(a3["trigger_id"], "action_time_a3_binding_invalid")
        req_hash(a3["trigger_sha256"], "action_time_a3_binding_invalid")
        if a3["cleanup_mode"] != cleanup_mode:
            raise error_type("action_time_cleanup_mode_invalid")

        package = exact_map(data["executor_package"], package_keys, "action_time_executor_package_invalid")
        req_id(package["package_id"], "action_time_executor_package_invalid")
        req_hash(package["package_sha256"], "action_time_executor_package_invalid")
        req_hash(package["tree_sha256"], "action_time_executor_package_invalid")

        signature = exact_map(data["signature_profile"], signature_keys, "action_time_signature_profile_invalid")
        if signature["algorithm"] != signature_algorithm or signature["format"] != signature_format or signature["capsule_version"] != "v1":
            raise error_type("action_time_signature_profile_invalid")
        req_hash(signature["issuer_public_key_fingerprint_sha256"], "action_time_signature_profile_invalid")
        req_bool(signature["real_key_loaded"], False, "action_time_real_key_forbidden")
        req_bool(signature["signature_bytes_present"], False, "action_time_signature_bytes_forbidden")

        nonce = exact_map(data["nonce"], nonce_keys, "action_time_nonce_invalid")
        req_id(nonce["nonce_id"], "action_time_nonce_invalid")
        req_hash(nonce["nonce_sha256"], "action_time_nonce_invalid")
        issued = parse_utc(nonce["issued_at_utc"], "action_time_nonce_time_invalid")
        expires = parse_utc(nonce["expires_at_utc"], "action_time_nonce_time_invalid")
        if expires <= issued:
            raise error_type("action_time_nonce_window_invalid")
        req_bool(nonce["single_use"], True, "action_time_nonce_policy_invalid")
        req_bool(nonce["no_retry"], True, "action_time_nonce_policy_invalid")
        req_bool(nonce["no_second_provision"], True, "action_time_nonce_policy_invalid")

        for key, expected in (
            ("permit_issued", False),
            ("signature_verified", False),
            ("destructive_action_authorized", False),
            ("external_action_executed", False),
            ("cleanup_complete", False),
            ("manager_consumable", False),
            ("next_run_allowed", False),
            ("a2_unlocked", False),
            ("h1_allowed", False),
            ("formal_quality_allowed", False),
        ):
            req_bool(data[key], expected, "action_time_public_authority_forbidden")
        if data["request_id"] != derived_identity("action-time-request-", data, "request_id"):
            raise error_type("action_time_request_identity_invalid")
        return data

    request_registry = {}

    def request_snapshot_bytes(instance):
        if type_fn(instance) is not request_type:
            raise type_error("action_time_request_exact_type_required")
        entry = request_registry.get(id_fn(instance))
        if entry is None or entry[0]() is not instance:
            raise type_error("action_time_request_not_registered")
        raw = entry[1]
        validate_request(parse_json(raw, "action_time_request_snapshot_invalid"))
        return raw

    class ActionTimeAuthorityRequest:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            raise type_error("action_time_request_factory_required")

        @classmethod
        def from_bytes(cls, raw):
            if cls is not ActionTimeAuthorityRequest:
                raise type_error("action_time_request_exact_type_required")
            return parse_request_trusted(raw)

        @property
        def data(self):
            return mapping_proxy_type(dict_copy(parse_json(request_snapshot_bytes(self), "action_time_request_snapshot_invalid")))

        def to_dict(self):
            return dict_copy(validate_request(parse_json(request_snapshot_bytes(self), "action_time_request_snapshot_invalid")))

        def canonical_bytes(self):
            return canonical(validate_request(parse_json(request_snapshot_bytes(self), "action_time_request_snapshot_invalid")))

        def sha256(self):
            return digest(canonical(validate_request(parse_json(request_snapshot_bytes(self), "action_time_request_snapshot_invalid"))))

    request_type = ActionTimeAuthorityRequest
    request_to_dict_impl = ActionTimeAuthorityRequest.to_dict
    request_canonical_impl = ActionTimeAuthorityRequest.canonical_bytes
    request_sha256_impl = ActionTimeAuthorityRequest.sha256

    def construct_request_trusted(data):
        raw = canonical(dict_copy(validate_request(data)))
        instance = object_new(request_type)
        identity = id_fn(instance)

        def discard(stored_ref, identity_key=identity):
            current = request_registry.get(identity_key)
            if current is not None and current[0] is stored_ref:
                request_registry.pop(identity_key, None)

        instance_ref = weakref_ref(instance, discard)
        request_registry[identity] = (instance_ref, raw)
        return instance

    def parse_request_trusted(raw):
        return construct_request_trusted(parse_json(raw, "action_time_request_bytes_invalid"))

    def request_to_dict_trusted(instance):
        return request_to_dict_impl(instance)

    def request_canonical_trusted(instance):
        return request_canonical_impl(instance)

    def request_sha256_trusted(instance):
        return request_sha256_impl(instance)

    def validate_ledger(value):
        data = exact_map(value, ledger_keys, "nonce_ledger_exact_keys_invalid")
        if data["schema_version"] != ledger_schema or data["status"] != "synthetic_in_memory_no_action":
            raise error_type("nonce_ledger_schema_or_status_invalid")
        req_id(data["ledger_id"], "nonce_ledger_id_invalid")
        rows = exact_list(data["entries"], "nonce_ledger_entries_invalid")
        for index, raw in enumerate_fn(rows):
            row = exact_map(raw, ledger_row_keys, "nonce_ledger_row_invalid")
            req_id(row["nonce_id"], "nonce_ledger_row_invalid")
            req_hash(row["nonce_sha256"], "nonce_ledger_row_invalid")
            if row["state"] not in nonce_states:
                raise error_type("nonce_ledger_state_invalid")
            if type_fn(row["transition_index"]) is not int_type or row["transition_index"] != index:
                raise error_type("nonce_ledger_transition_index_invalid")
        for key, expected in (
            ("live_authority", False), ("permit_issued", False),
            ("destructive_action_authorized", False),
            ("external_action_executed", False), ("manager_consumable", False),
        ):
            req_bool(data[key], expected, "nonce_ledger_authority_invalid")
        if data["ledger_id"] != derived_identity("action-time-ledger-", data, "ledger_id"):
            raise error_type("nonce_ledger_identity_invalid")
        return data

    class NonceLedgerSnapshot:
        __slots__ = ("__bytes",)

        def __init__(self, data):
            self.__bytes = canonical(dict_copy(validate_ledger(data)))

        @classmethod
        def from_bytes(cls, raw):
            if cls is not NonceLedgerSnapshot:
                raise type_error("nonce_ledger_snapshot_exact_type_required")
            return cls(parse_json(raw, "nonce_ledger_snapshot_bytes_invalid"))

        def to_dict(self):
            return dict_copy(validate_ledger(parse_json(self.__bytes, "nonce_ledger_snapshot_invalid")))

        def canonical_bytes(self):
            return canonical(validate_ledger(parse_json(self.__bytes, "nonce_ledger_snapshot_invalid")))

        def sha256(self):
            return digest(canonical(validate_ledger(parse_json(self.__bytes, "nonce_ledger_snapshot_invalid"))))

    ledger_snapshot_type = NonceLedgerSnapshot
    ledger_init_impl = NonceLedgerSnapshot.__init__
    ledger_to_dict_impl = NonceLedgerSnapshot.to_dict
    ledger_canonical_impl = NonceLedgerSnapshot.canonical_bytes
    ledger_sha256_impl = NonceLedgerSnapshot.sha256

    def construct_ledger_snapshot_trusted(data):
        instance = object_new(ledger_snapshot_type)
        ledger_init_impl(instance, data)
        return instance

    def parse_ledger_snapshot_trusted(raw):
        return construct_ledger_snapshot_trusted(parse_json(raw, "nonce_ledger_snapshot_bytes_invalid"))
    ledger_registry = {}

    class _ScriptedNonceLedger:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            raise type_error("scripted_nonce_ledger_factory_required")

        def transition(self, nonce_id, nonce_sha256, state):
            entry = ledger_registry.get(id_fn(self))
            if entry is None or entry[0]() is not self:
                raise type_error("scripted_nonce_ledger_not_registered")
            req_id(nonce_id, "scripted_nonce_id_invalid")
            req_hash(nonce_sha256, "scripted_nonce_sha256_invalid")
            if state not in nonce_states:
                raise error_type("scripted_nonce_state_invalid")
            rows = entry[1]
            if rows:
                previous = rows[-1]
                if previous["nonce_id"] != nonce_id or previous["nonce_sha256"] != nonce_sha256:
                    raise error_type("scripted_nonce_identity_drift")
                allowed = {
                    "issued": ("consumed", "ambiguous", "expired", "replay"),
                    "consumed": ("replay",),
                    "ambiguous": ("replay",),
                    "expired": ("replay",),
                    "replay": (),
                }
                if state not in allowed[previous["state"]]:
                    raise error_type("scripted_nonce_transition_invalid")
            elif state != "issued":
                raise error_type("scripted_nonce_must_start_issued")
            rows.append({
                "nonce_id": nonce_id,
                "nonce_sha256": nonce_sha256,
                "state": state,
                "transition_index": len_fn(rows),
            })
            return self.snapshot()

        def snapshot(self):
            entry = ledger_registry.get(id_fn(self))
            if entry is None or entry[0]() is not self:
                raise type_error("scripted_nonce_ledger_not_registered")
            root = {
                "schema_version": ledger_schema,
                "ledger_id": "action-time-ledger-pending",
                "status": "synthetic_in_memory_no_action",
                "entries": [dict_copy(row) for row in entry[1]],
                "live_authority": False,
                "permit_issued": False,
                "destructive_action_authorized": False,
                "external_action_executed": False,
                "manager_consumable": False,
            }
            base = dict_copy(root)
            base.pop("ledger_id")
            root["ledger_id"] = "action-time-ledger-" + digest(canonical(base))[:20]
            return construct_ledger_snapshot_trusted(root)

    scripted_ledger_type = _ScriptedNonceLedger

    def create_scripted_ledger_for_tests():
        instance = object_new(scripted_ledger_type)
        identity = id_fn(instance)

        def discard(stored_ref, identity_key=identity):
            current = ledger_registry.get(identity_key)
            if current is not None and current[0] is stored_ref:
                ledger_registry.pop(identity_key, None)

        instance_ref = weakref_ref(instance, discard)
        ledger_registry[identity] = (instance_ref, [])
        return instance

    def validate_request_bytes(raw):
        return parse_request_trusted(raw)

    def validate_ledger_bytes(raw):
        return parse_ledger_snapshot_trusted(raw)

    return {
        "ActionTimeAuthorityRequest": ActionTimeAuthorityRequest,
        "NonceLedgerSnapshot": NonceLedgerSnapshot,
        "validate_action_time_authority_request_bytes": validate_request_bytes,
        "validate_nonce_ledger_snapshot_bytes": validate_ledger_bytes,
        "_create_scripted_nonce_ledger_for_tests": create_scripted_ledger_for_tests,
        "_parse_action_time_request_for_trust": parse_request_trusted,
        "_action_time_request_to_dict_for_trust": request_to_dict_trusted,
        "_action_time_request_canonical_for_trust": request_canonical_trusted,
        "_action_time_request_sha256_for_trust": request_sha256_trusted,
        "_parse_nonce_ledger_snapshot_for_trust": parse_ledger_snapshot_trusted,
        "_canonical_for_tests": canonical,
        "_digest_for_tests": digest,
        "ACTION_TIME_TRIGGER_KINDS": trigger_kinds,
        "ACTION_TIME_ROOT_ROLES": root_roles,
        "ACTION_TIME_SIGNATURE_ALGORITHM": signature_algorithm,
        "ACTION_TIME_SIGNATURE_FORMAT": signature_format,
        "ACTION_TIME_CLEANUP_MODE": cleanup_mode,
    }


_AUTHORITIES = _build_authorities()
globals().update(_AUTHORITIES)

__all__ = [
    "ActionTimeAuthorityError", "ActionTimeAuthorityRequest",
    "NonceLedgerSnapshot", "validate_action_time_authority_request_bytes",
    "validate_nonce_ledger_snapshot_bytes", "ACTION_TIME_TRIGGER_KINDS",
    "ACTION_TIME_ROOT_ROLES", "ACTION_TIME_SIGNATURE_ALGORITHM",
    "ACTION_TIME_SIGNATURE_FORMAT", "ACTION_TIME_CLEANUP_MODE",
]
