"""No-action local action-time issuance intent for Req2Web Stage 3.

This module freezes a local, canonical intent only.  It never loads a key,
creates a signature, issues a permit, starts a process, or contacts a service.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
import weakref

import req2web_runtime.autodl_a1_production_gate as _a1_gate


class ActionTimeIssuanceError(ValueError):
    """Raised when a no-action issuance intent is invalid."""


def _build_authorities():
    bool_type = bool
    bytes_type = bytes
    dict_type = dict
    int_type = int
    list_type = list
    str_type = str
    type_fn = type
    len_fn = len
    set_type = set
    tuple_fn = tuple
    id_fn = id
    object_new = object.__new__
    weakref_ref = weakref.ref
    mapping_proxy_type = MappingProxyType
    mapping_proxy_instance_type = type(MappingProxyType({}))
    json_loads = json.loads
    json_dumps = json.dumps
    json_error = json.JSONDecodeError
    sha256_fn = hashlib.sha256
    datetime_type = datetime
    timezone_utc = timezone.utc
    error_type = ActionTimeIssuanceError
    type_error = TypeError
    value_error = ValueError

    schema = "req2web.runtime.action_time_issuance_intent.v1"
    protocol_status = "local_issuance_intent_declared_no_action"
    trigger_kinds = ("a1_passed_closeout", "a1_failure_cleanup")
    root_roles = ("work", "cache", "log", "transfer", "evidence")
    signature_algorithm = "ed25519_openssh_detached"
    signature_format = "openssh_detached_signature_capsule_v1"
    signature_backend = "openssh_keygen_y_sign_verify"
    profile_values = ("quality_experiment", "local_smoke")
    owner_snapshot_schema = (
        "req2web.runtime.real_a1_production_gate_result_owning_snapshot.v1"
    )
    owner_snapshot_abi = (
        "req2web.runtime.real_a1_production_gate_result_owning_snapshot.abi.v1"
    )
    owner_snapshot_keys = (
        "authority_schema", "authority_abi", "authority_generation",
        "validation_mode", "action_time_git_sha", "plan_id", "plan_sha256",
        "control_id", "control_sha256", "gate_report_canonical_bytes",
        "gate_report_id", "gate_report_sha256", "gate_receipt_canonical_bytes",
        "gate_receipt_id", "gate_receipt_sha256", "gate_bundle_canonical_bytes",
        "gate_bundle_id", "gate_bundle_sha256", "manager_authority",
        "decision_validation_mode", "decision_status", "decision_next_state",
        "decision_manager_a1_passed", "decision_a2_unlocked",
        "decision_provider_invoked", "decision_project_data_transferred",
        "decision_h1_allowed", "decision_formal_quality_allowed",
        "decision_validated_at_utc", "return_code", "snapshot_sha256",
    )
    owner_bytes = (
        "gate_report_canonical_bytes", "gate_receipt_canonical_bytes",
        "gate_bundle_canonical_bytes",
    )
    owner_bools = (
        "manager_authority", "decision_manager_a1_passed",
        "decision_a2_unlocked", "decision_provider_invoked",
        "decision_project_data_transferred", "decision_h1_allowed",
        "decision_formal_quality_allowed",
    )
    owner_ints = ("return_code",)
    owner_strings = tuple_fn(
        key for key in owner_snapshot_keys
        if key not in owner_bytes and key not in owner_bools and key not in owner_ints
    )
    intent_keys = (
        "schema_version", "issuance_id", "protocol_status", "trigger_kind",
        "action_time_git_sha", "approved_disposition", "d17",
        "a1_owner_snapshot", "deployment_manifest", "model_inventory",
        "endpoint", "root_contracts", "profile", "executor_package", "a3",
        "signature_profile", "nonce", "single_use", "no_retry",
        "no_second_provision", "permit_issued", "signature_verified",
        "destructive_action_authorized", "external_action_executed",
        "cleanup_complete", "manager_consumable", "next_run_allowed",
        "a2_unlocked", "h1_allowed", "formal_quality_allowed",
    )
    binding_keys = (
        "action_time_git_sha", "approved_disposition", "d17",
        "deployment_manifest", "no_action_plan", "operational_plan",
        "controls", "model_inventory", "endpoint", "root_contracts", "profile",
        "a3", "executor_package", "signature_profile", "nonce",
    )
    pair_keys = ("id", "sha256")
    d17_keys = (
        "manifest_id", "manifest_sha256", "manager_plan_id",
        "manager_plan_sha256", "field_policy_sha256",
    )
    deployment_keys = (
        "manifest_id", "manifest_sha256", "source_commit_sha",
        "package_tree_sha256",
    )
    a1_keys = (
        "owner_snapshot_sha256", "validation_mode", "plan_id", "plan_sha256",
        "control_id", "control_sha256", "gate_report_id", "gate_report_sha256",
        "gate_receipt_id", "gate_receipt_sha256", "gate_bundle_id",
        "gate_bundle_sha256", "decision_status", "manager_authority",
        "manager_a1_passed", "a2_unlocked", "return_code",
    )
    model_keys = ("repository", "revision", "file_count", "total_bytes", "tree_sha256")
    endpoint_keys = (
        "instance_id_hash", "ssh_host_fingerprint_sha256",
        "executor_fingerprint_sha256", "credential_fingerprint_sha256",
    )
    root_keys = (
        "role", "normalized_path_sha256", "marker_id", "marker_sha256",
        "inventory_tree_sha256",
    )
    profile_keys = (
        "profile_name", "dtype", "quantization", "selected_device",
        "selection_basis", "override",
    )
    a3_keys = (
        "policy_id", "policy_sha256", "trigger_id", "trigger_sha256",
        "cleanup_mode",
    )
    package_keys = ("package_id", "package_sha256", "tree_sha256")
    signature_keys = (
        "algorithm", "format", "backend", "issuer_public_key_fingerprint_sha256",
        "capsule_version", "real_key_loaded", "signature_bytes_present",
    )
    nonce_keys = (
        "nonce_id", "nonce_sha256", "issued_at_utc", "expires_at_utc",
        "single_use", "no_retry", "no_second_provision",
    )
    result_registry = {}

    gate_result_type = _a1_gate.A1ProductionGateResult
    owner_accessor = getattr(_a1_gate, "_read_a1_production_gate_result_owning_snapshot", None)
    if not callable(owner_accessor):
        owner_accessor_error = "action_time_issuer_owner_authority_unavailable_restart_and_rebuild_required"
        owner_metadata = None
    else:
        try:
            owner_metadata = owner_accessor()
            if (
                type_fn(owner_metadata) is not mapping_proxy_instance_type
                or set_type(owner_metadata) != {"authority_schema", "authority_abi", "authority_generation"}
                or owner_metadata["authority_schema"] != owner_snapshot_schema
                or owner_metadata["authority_abi"] != owner_snapshot_abi
                or type_fn(owner_metadata["authority_generation"]) is not str_type
                or not owner_metadata["authority_generation"]
            ):
                raise error_type("owner_metadata_invalid")
            owner_metadata = mapping_proxy_type(dict_type(owner_metadata))
            owner_accessor_error = None
        except Exception:
            owner_metadata = None
            owner_accessor_error = "action_time_issuer_owner_authority_unavailable_restart_and_rebuild_required"

    def canonical(value):
        try:
            return json_dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (type_error, value_error) as exc:
            raise error_type("action_time_issuer_canonical_invalid") from exc

    def digest(raw):
        if type_fn(raw) is not bytes_type:
            raise error_type("action_time_issuer_digest_bytes_required")
        return sha256_fn(raw).hexdigest()

    def parse_json(raw, code):
        if type_fn(raw) is not bytes_type:
            raise error_type(code)
        try:
            return json_loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json_error) as exc:
            raise error_type(code) from exc

    def exact_map(value, keys, code):
        if (
            type_fn(value) is not dict_type
            or len_fn(value) != len_fn(keys)
            or set_type(value.keys()) != set_type(keys)
        ):
            raise error_type(code)
        return value

    def require_text(value, code):
        if type_fn(value) is not str_type or not value:
            raise error_type(code)
        return value

    def require_hex(value, length, code):
        if (
            type_fn(value) is not str_type or len_fn(value) != length
            or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise error_type(code)
        return value

    def require_bool(value, expected, code):
        if type_fn(value) is not bool_type or value is not expected:
            raise error_type(code)
        return value

    def parse_utc(value, code):
        require_text(value, code)
        if not value.endswith("Z"):
            raise error_type(code)
        try:
            parsed = datetime_type.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise error_type(code) from exc
        if parsed.tzinfo != timezone_utc:
            raise error_type(code)
        return parsed

    def validate_pair(value, code):
        data = exact_map(value, pair_keys, code)
        require_text(data["id"], code)
        require_hex(data["sha256"], 64, code)
        return data

    def validate_owner_snapshot(result):
        if owner_accessor_error is not None or owner_metadata is None:
            raise error_type(owner_accessor_error)
        if type_fn(result) is not gate_result_type:
            raise type_error("action_time_issuer_complete_registered_a1_result_required")
        try:
            snapshot = owner_accessor(result)
        except Exception as exc:
            raise error_type(
                "action_time_issuer_owner_snapshot_replay_failed_restart_and_rebuild_required"
            ) from exc
        if (
            type_fn(snapshot) is not mapping_proxy_instance_type
            or set_type(snapshot.keys()) != set_type(owner_snapshot_keys)
            or len_fn(snapshot) != len_fn(owner_snapshot_keys)
        ):
            raise error_type("action_time_issuer_owner_snapshot_shape_invalid")
        for key in owner_bytes:
            if type_fn(snapshot[key]) is not bytes_type:
                raise error_type("action_time_issuer_owner_snapshot_type_invalid")
        for key in owner_bools:
            if type_fn(snapshot[key]) is not bool_type:
                raise error_type("action_time_issuer_owner_snapshot_type_invalid")
        for key in owner_ints:
            if type_fn(snapshot[key]) is not int_type:
                raise error_type("action_time_issuer_owner_snapshot_type_invalid")
        for key in owner_strings:
            if type_fn(snapshot[key]) is not str_type or not snapshot[key]:
                raise error_type("action_time_issuer_owner_snapshot_type_invalid")
        if (
            snapshot["authority_schema"] != owner_metadata["authority_schema"]
            or snapshot["authority_abi"] != owner_metadata["authority_abi"]
            or snapshot["authority_generation"] != owner_metadata["authority_generation"]
            or snapshot["return_code"] != 0
        ):
            raise error_type(
                "action_time_issuer_owner_generation_or_return_code_invalid_restart_and_rebuild_required"
            )
        for key in (
            "plan_sha256", "control_sha256", "gate_report_sha256",
            "gate_receipt_sha256", "gate_bundle_sha256", "snapshot_sha256",
        ):
            require_hex(snapshot[key], 64, "action_time_issuer_owner_snapshot_hash_invalid")
        if (
            digest(snapshot["gate_report_canonical_bytes"]) != snapshot["gate_report_sha256"]
            or digest(snapshot["gate_receipt_canonical_bytes"]) != snapshot["gate_receipt_sha256"]
            or digest(snapshot["gate_bundle_canonical_bytes"]) != snapshot["gate_bundle_sha256"]
        ):
            raise error_type("action_time_issuer_owner_snapshot_hash_invalid")
        return snapshot

    def validate_bindings(value, snapshot, trigger_kind):
        data = exact_map(value, binding_keys, "action_time_issuer_binding_exact_keys_invalid")
        if trigger_kind not in trigger_kinds:
            raise error_type("action_time_issuer_trigger_invalid")
        require_hex(data["action_time_git_sha"], 40, "action_time_issuer_action_sha_invalid")
        if data["action_time_git_sha"] != snapshot["action_time_git_sha"]:
            raise error_type("action_time_issuer_action_sha_owner_binding_invalid")
        validate_pair(data["approved_disposition"], "action_time_issuer_disposition_invalid")
        d17 = exact_map(data["d17"], d17_keys, "action_time_issuer_d17_invalid")
        for key in d17_keys:
            if key.endswith("sha256"):
                require_hex(d17[key], 64, "action_time_issuer_d17_invalid")
            else:
                require_text(d17[key], "action_time_issuer_d17_invalid")
        deployment = exact_map(data["deployment_manifest"], deployment_keys, "action_time_issuer_deployment_invalid")
        require_text(deployment["manifest_id"], "action_time_issuer_deployment_invalid")
        for key in ("manifest_sha256", "package_tree_sha256"):
            require_hex(deployment[key], 64, "action_time_issuer_deployment_invalid")
        require_hex(deployment["source_commit_sha"], 40, "action_time_issuer_deployment_invalid")
        if deployment["source_commit_sha"] != data["action_time_git_sha"]:
            raise error_type("action_time_issuer_deployment_action_sha_invalid")
        no_action_plan = validate_pair(data["no_action_plan"], "action_time_issuer_no_action_plan_invalid")
        operational = validate_pair(data["operational_plan"], "action_time_issuer_operational_plan_invalid")
        controls = validate_pair(data["controls"], "action_time_issuer_controls_invalid")
        if operational["id"] != snapshot["plan_id"] or operational["sha256"] != snapshot["plan_sha256"]:
            raise error_type("action_time_issuer_operational_plan_owner_binding_invalid")
        if controls["id"] != snapshot["control_id"] or controls["sha256"] != snapshot["control_sha256"]:
            raise error_type("action_time_issuer_controls_owner_binding_invalid")
        model = exact_map(data["model_inventory"], model_keys, "action_time_issuer_model_inventory_invalid")
        require_text(model["repository"], "action_time_issuer_model_inventory_invalid")
        require_text(model["revision"], "action_time_issuer_model_inventory_invalid")
        if type_fn(model["file_count"]) is not int_type or model["file_count"] <= 0:
            raise error_type("action_time_issuer_model_inventory_invalid")
        if type_fn(model["total_bytes"]) is not int_type or model["total_bytes"] <= 0:
            raise error_type("action_time_issuer_model_inventory_invalid")
        require_hex(model["tree_sha256"], 64, "action_time_issuer_model_inventory_invalid")
        endpoint = exact_map(data["endpoint"], endpoint_keys, "action_time_issuer_endpoint_invalid")
        for key in endpoint_keys:
            require_hex(endpoint[key], 64, "action_time_issuer_endpoint_invalid")
        rows = data["root_contracts"]
        if type_fn(rows) is not list_type or len_fn(rows) != len_fn(root_roles):
            raise error_type("action_time_issuer_root_contracts_invalid")
        seen_paths = set_type()
        for index, role in enumerate(root_roles):
            row = exact_map(rows[index], root_keys, "action_time_issuer_root_contract_invalid")
            if row["role"] != role:
                raise error_type("action_time_issuer_root_role_order_invalid")
            require_hex(row["normalized_path_sha256"], 64, "action_time_issuer_root_contract_invalid")
            require_text(row["marker_id"], "action_time_issuer_root_contract_invalid")
            require_hex(row["marker_sha256"], 64, "action_time_issuer_root_contract_invalid")
            require_hex(row["inventory_tree_sha256"], 64, "action_time_issuer_root_contract_invalid")
            if row["normalized_path_sha256"] in seen_paths:
                raise error_type("action_time_issuer_root_containment_or_identity_invalid")
            seen_paths.add(row["normalized_path_sha256"])
        profile = exact_map(data["profile"], profile_keys, "action_time_issuer_profile_invalid")
        if profile["profile_name"] not in profile_values:
            raise error_type("action_time_issuer_profile_invalid")
        if profile["profile_name"] == "quality_experiment" and (
            profile["dtype"] != "bf16" or profile["quantization"] != "none"
        ):
            raise error_type("action_time_issuer_quality_profile_drift")
        require_text(profile["selected_device"], "action_time_issuer_profile_invalid")
        require_text(profile["selection_basis"], "action_time_issuer_profile_invalid")
        if type_fn(profile["override"]) is not bool_type:
            raise error_type("action_time_issuer_profile_invalid")
        a3 = exact_map(data["a3"], a3_keys, "action_time_issuer_a3_invalid")
        for key in ("policy_id", "trigger_id", "cleanup_mode"):
            require_text(a3[key], "action_time_issuer_a3_invalid")
        for key in ("policy_sha256", "trigger_sha256"):
            require_hex(a3[key], 64, "action_time_issuer_a3_invalid")
        package = exact_map(data["executor_package"], package_keys, "action_time_issuer_executor_package_invalid")
        require_text(package["package_id"], "action_time_issuer_executor_package_invalid")
        for key in ("package_sha256", "tree_sha256"):
            require_hex(package[key], 64, "action_time_issuer_executor_package_invalid")
        signature = exact_map(data["signature_profile"], signature_keys, "action_time_issuer_signature_profile_invalid")
        if (signature["algorithm"] != signature_algorithm or signature["format"] != signature_format or signature["backend"] != signature_backend):
            raise error_type("action_time_issuer_signature_profile_invalid")
        require_hex(signature["issuer_public_key_fingerprint_sha256"], 64, "action_time_issuer_signature_profile_invalid")
        require_text(signature["capsule_version"], "action_time_issuer_signature_profile_invalid")
        require_bool(signature["real_key_loaded"], False, "action_time_issuer_real_key_forbidden")
        require_bool(signature["signature_bytes_present"], False, "action_time_issuer_signature_bytes_forbidden")
        nonce = exact_map(data["nonce"], nonce_keys, "action_time_issuer_nonce_invalid")
        require_text(nonce["nonce_id"], "action_time_issuer_nonce_invalid")
        require_hex(nonce["nonce_sha256"], 64, "action_time_issuer_nonce_invalid")
        issued = parse_utc(nonce["issued_at_utc"], "action_time_issuer_nonce_time_invalid")
        expires = parse_utc(nonce["expires_at_utc"], "action_time_issuer_nonce_time_invalid")
        if expires <= issued:
            raise error_type("action_time_issuer_nonce_window_invalid")
        for key in ("single_use", "no_retry", "no_second_provision"):
            require_bool(nonce[key], True, "action_time_issuer_nonce_policy_invalid")
        if trigger_kind == "a1_passed_closeout":
            if not (snapshot["manager_authority"] and snapshot["decision_manager_a1_passed"] and snapshot["decision_a2_unlocked"]):
                raise error_type("action_time_issuer_pass_trigger_owner_invalid")
        else:
            if snapshot["manager_authority"] or snapshot["decision_manager_a1_passed"] or snapshot["decision_a2_unlocked"]:
                raise error_type("action_time_issuer_failure_trigger_owner_invalid")
        return data

    def snapshot_from_owner(snapshot):
        return {
            "owner_snapshot_sha256": snapshot["snapshot_sha256"],
            "validation_mode": snapshot["validation_mode"],
            "plan_id": snapshot["plan_id"],
            "plan_sha256": snapshot["plan_sha256"],
            "control_id": snapshot["control_id"],
            "control_sha256": snapshot["control_sha256"],
            "gate_report_id": snapshot["gate_report_id"],
            "gate_report_sha256": snapshot["gate_report_sha256"],
            "gate_receipt_id": snapshot["gate_receipt_id"],
            "gate_receipt_sha256": snapshot["gate_receipt_sha256"],
            "gate_bundle_id": snapshot["gate_bundle_id"],
            "gate_bundle_sha256": snapshot["gate_bundle_sha256"],
            "decision_status": snapshot["decision_status"],
            "manager_authority": snapshot["manager_authority"],
            "manager_a1_passed": snapshot["decision_manager_a1_passed"],
            "a2_unlocked": snapshot["decision_a2_unlocked"],
            "return_code": snapshot["return_code"],
        }

    def validate_intent_data(value):
        data = exact_map(value, intent_keys, "action_time_issuance_intent_exact_keys_invalid")
        if data["schema_version"] != schema or data["protocol_status"] != protocol_status:
            raise error_type("action_time_issuance_intent_schema_or_status_invalid")
        require_text(data["issuance_id"], "action_time_issuance_intent_id_invalid")
        if data["trigger_kind"] not in trigger_kinds:
            raise error_type("action_time_issuance_intent_trigger_invalid")
        require_hex(data["action_time_git_sha"], 40, "action_time_issuance_intent_action_sha_invalid")
        validate_pair(data["approved_disposition"], "action_time_issuance_intent_disposition_invalid")
        exact_map(data["a1_owner_snapshot"], a1_keys, "action_time_issuance_intent_owner_snapshot_invalid")
        validate_bindings({
            "action_time_git_sha": data["action_time_git_sha"],
            "approved_disposition": data["approved_disposition"], "d17": data["d17"],
            "deployment_manifest": data["deployment_manifest"],
            "no_action_plan": {"id": "intent-replayed-no-action-plan", "sha256": "0" * 64},
            "operational_plan": {"id": data["a1_owner_snapshot"]["plan_id"], "sha256": data["a1_owner_snapshot"]["plan_sha256"]},
            "controls": {"id": data["a1_owner_snapshot"]["control_id"], "sha256": data["a1_owner_snapshot"]["control_sha256"]},
            "model_inventory": data["model_inventory"], "endpoint": data["endpoint"],
            "root_contracts": data["root_contracts"], "profile": data["profile"],
            "a3": data["a3"], "executor_package": data["executor_package"],
            "signature_profile": data["signature_profile"], "nonce": data["nonce"],
        }, {
            "action_time_git_sha": data["action_time_git_sha"],
            "plan_id": data["a1_owner_snapshot"]["plan_id"],
            "plan_sha256": data["a1_owner_snapshot"]["plan_sha256"],
            "control_id": data["a1_owner_snapshot"]["control_id"],
            "control_sha256": data["a1_owner_snapshot"]["control_sha256"],
            "manager_authority": data["a1_owner_snapshot"]["manager_authority"],
            "decision_manager_a1_passed": data["a1_owner_snapshot"]["manager_a1_passed"],
            "decision_a2_unlocked": data["a1_owner_snapshot"]["a2_unlocked"],
        }, data["trigger_kind"])
        require_hex(data["a1_owner_snapshot"]["owner_snapshot_sha256"], 64, "action_time_issuance_intent_owner_snapshot_invalid")
        for key in ("plan_sha256", "control_sha256", "gate_report_sha256", "gate_receipt_sha256", "gate_bundle_sha256"):
            require_hex(data["a1_owner_snapshot"][key], 64, "action_time_issuance_intent_owner_snapshot_invalid")
        if type_fn(data["a1_owner_snapshot"]["return_code"]) is not int_type or data["a1_owner_snapshot"]["return_code"] != 0:
            raise error_type("action_time_issuance_intent_owner_snapshot_invalid")
        for key in ("manager_authority", "manager_a1_passed", "a2_unlocked"):
            if type_fn(data["a1_owner_snapshot"][key]) is not bool_type:
                raise error_type("action_time_issuance_intent_owner_snapshot_invalid")
        identity_root = dict_type(data)
        identity_root.pop("issuance_id")
        expected_id = "action-time-issuance-" + digest(canonical(identity_root))[:20]
        if data["issuance_id"] != expected_id:
            raise error_type("action_time_issuance_intent_identity_invalid")
        for key in (
            "single_use", "no_retry", "no_second_provision", "permit_issued",
            "signature_verified", "destructive_action_authorized",
            "external_action_executed", "cleanup_complete", "manager_consumable",
            "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed",
        ):
            require_bool(data[key], False if key not in ("single_use", "no_retry", "no_second_provision") else True,
                         "action_time_issuance_intent_public_authority_forbidden")
        return data

    class ActionTimeIssuanceIntent:
        __slots__ = ("__weakref__",)
        def __new__(cls, *args, **kwargs):
            raise type_error("action_time_issuance_intent_factory_required")
        def to_dict(self):
            return dict_type(registered_data(self))
        def canonical_bytes(self):
            return registered_bytes(self)
        def sha256(self):
            return digest(registered_bytes(self))
        @classmethod
        def from_bytes(cls, raw):
            if cls is not ActionTimeIssuanceIntent:
                raise type_error("action_time_issuance_intent_exact_type_required")
            return parse_intent_trusted(raw)

    intent_type = ActionTimeIssuanceIntent
    intent_to_dict_impl = ActionTimeIssuanceIntent.to_dict
    intent_canonical_impl = ActionTimeIssuanceIntent.canonical_bytes
    intent_sha_impl = ActionTimeIssuanceIntent.sha256

    def registered_bytes(instance):
        if type_fn(instance) is not intent_type:
            raise type_error("action_time_issuance_intent_exact_type_required")
        entry = result_registry.get(id_fn(instance))
        if entry is None or entry[0]() is not instance:
            raise type_error("action_time_issuance_intent_not_registered")
        return entry[1]

    def registered_data(instance):
        return validate_intent_data(parse_json(registered_bytes(instance), "action_time_issuance_intent_bytes_invalid"))

    def construct_intent_trusted(data, ledger_eligible=False):
        raw = canonical(validate_intent_data(data))
        instance = object_new(intent_type)
        identity = id_fn(instance)
        def discard(stored_ref, identity_key=identity):
            current = result_registry.get(identity_key)
            if current is not None and current[0] is stored_ref:
                result_registry.pop(identity_key, None)
        ref = weakref_ref(instance, discard)
        result_registry[identity] = (ref, raw, ledger_eligible)
        return instance

    def parse_intent_trusted(raw):
        return construct_intent_trusted(parse_json(raw, "action_time_issuance_intent_bytes_invalid"), False)

    def ledger_eligible_trusted(instance):
        if type_fn(instance) is not intent_type:
            raise type_error("action_time_issuance_intent_exact_type_required")
        entry = result_registry.get(id_fn(instance))
        if entry is None or entry[0]() is not instance:
            raise type_error("action_time_issuance_intent_not_registered")
        if entry[2] is not True:
            raise error_type("action_time_issuance_intent_replay_bytes_not_ledger_eligible")
        return True

    def build_intent(result, trigger_kind, structural_bindings):
        snapshot = validate_owner_snapshot(result)
        bindings = validate_bindings(structural_bindings, snapshot, trigger_kind)
        root = {
            "schema_version": schema, "issuance_id": "pending",
            "protocol_status": protocol_status, "trigger_kind": trigger_kind,
            "action_time_git_sha": bindings["action_time_git_sha"],
            "approved_disposition": dict_type(bindings["approved_disposition"]),
            "d17": dict_type(bindings["d17"]),
            "a1_owner_snapshot": snapshot_from_owner(snapshot),
            "deployment_manifest": dict_type(bindings["deployment_manifest"]),
            "model_inventory": dict_type(bindings["model_inventory"]),
            "endpoint": dict_type(bindings["endpoint"]),
            "root_contracts": [dict_type(row) for row in bindings["root_contracts"]],
            "profile": dict_type(bindings["profile"]),
            "executor_package": dict_type(bindings["executor_package"]),
            "a3": dict_type(bindings["a3"]),
            "signature_profile": dict_type(bindings["signature_profile"]),
            "nonce": dict_type(bindings["nonce"]),
            "single_use": True, "no_retry": True, "no_second_provision": True,
            "permit_issued": False, "signature_verified": False,
            "destructive_action_authorized": False, "external_action_executed": False,
            "cleanup_complete": False, "manager_consumable": False,
            "next_run_allowed": False, "a2_unlocked": False, "h1_allowed": False,
            "formal_quality_allowed": False,
        }
        identity_root = dict_type(root)
        identity_root.pop("issuance_id")
        root["issuance_id"] = "action-time-issuance-" + digest(canonical(identity_root))[:20]
        return construct_intent_trusted(root, True)

    return {
        "ActionTimeIssuanceIntent": ActionTimeIssuanceIntent,
        "prepare_action_time_issuance_intent_no_action": build_intent,
        "validate_action_time_issuance_intent_bytes": parse_intent_trusted,
        "_parse_action_time_issuance_intent_for_trust": parse_intent_trusted,
        "_action_time_issuance_intent_canonical_for_trust": registered_bytes,
        "_action_time_issuance_intent_to_dict_for_trust": registered_data,
        "_action_time_issuance_intent_sha256_for_trust": lambda item: digest(registered_bytes(item)),
        "_action_time_issuance_intent_ledger_eligible_for_trust": ledger_eligible_trusted,
        "_canonical_for_tests": canonical,
        "ACTION_TIME_ISSUANCE_BINDING_KEYS": binding_keys,
    }


_AUTHORITIES = _build_authorities()
globals().update(_AUTHORITIES)

__all__ = [
    "ActionTimeIssuanceError", "ActionTimeIssuanceIntent",
    "prepare_action_time_issuance_intent_no_action",
    "validate_action_time_issuance_intent_bytes", "ACTION_TIME_ISSUANCE_BINDING_KEYS",
]
