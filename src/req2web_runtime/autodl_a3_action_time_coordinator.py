"""No-action coordinator for the future AutoDL A3 trusted handoff.

The coordinator consumes a complete registered ``A1ProductionGateResult``.
It captures the gate owner's private immutable snapshot accessor and consumes
only that versioned ABI. Mutable exported class properties or helper methods
are never trusted, and owner reload/generation drift fails closed.
"""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
import weakref

from . import autodl_a1_production_gate as a1_gate
from . import autodl_action_time_authority as action_authority
from . import autodl_a3_handoff_protocol as handoff_protocol
from . import autodl_a3_authenticated_executor_contract as executor_contract_module
from .autodl_action_time_authority import ACTION_TIME_CLEANUP_MODE, ActionTimeAuthorityRequest
from .autodl_a3_handoff_protocol import A3HandoffCapsuleTemplate
from .autodl_a3_authenticated_executor_contract import A3AuthenticatedExecutorContract


class A3ActionTimeCoordinatorError(ValueError):
    """Raised when no-action handoff preparation fails closed."""


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
    dict_copy = dict
    object_new = object.__new__
    id_fn = id
    weakref_ref = weakref.ref
    mapping_proxy_type = MappingProxyType
    mapping_proxy_instance_type = type(mapping_proxy_type({}))
    type_error = TypeError
    value_error = ValueError
    exception_type = Exception
    error_type = A3ActionTimeCoordinatorError
    json_dumps = json.dumps
    sha256_fn = hashlib.sha256
    callable_fn = callable
    getattr_fn = getattr

    gate_result_type = a1_gate.A1ProductionGateResult
    gate_owner_accessor = getattr_fn(
        a1_gate,
        "_read_a1_production_gate_result_owning_snapshot",
        None,
    )
    request_type = ActionTimeAuthorityRequest
    capsule_type = A3HandoffCapsuleTemplate
    contract_type = A3AuthenticatedExecutorContract
    cleanup_mode = ACTION_TIME_CLEANUP_MODE

    request_parse_trusted = action_authority._parse_action_time_request_for_trust
    request_to_dict_trusted = action_authority._action_time_request_to_dict_for_trust
    request_canonical_trusted = action_authority._action_time_request_canonical_for_trust
    request_sha256_trusted = action_authority._action_time_request_sha256_for_trust
    capsule_factory = handoff_protocol.create_a3_handoff_capsule_template
    capsule_parse_trusted = handoff_protocol._parse_a3_handoff_capsule_for_trust
    capsule_canonical_trusted = handoff_protocol._a3_handoff_capsule_canonical_for_trust
    capsule_sha256_trusted = handoff_protocol._a3_handoff_capsule_sha256_for_trust
    contract_factory = executor_contract_module.create_a3_authenticated_executor_contract
    contract_parse_trusted = executor_contract_module._parse_a3_authenticated_executor_contract_for_trust
    contract_canonical_trusted = executor_contract_module._a3_authenticated_executor_contract_canonical_for_trust
    contract_sha256_trusted = executor_contract_module._a3_authenticated_executor_contract_sha256_for_trust

    binding_keys = (
        "action_time_git_sha", "approved_disposition", "d17",
        "deployment_manifest", "no_action_plan", "operational_plan",
        "controls", "model_inventory", "endpoint", "root_contracts", "a3",
        "executor_package", "signature_profile", "nonce",
    )
    id_hash_keys = ("id", "sha256")
    owner_metadata_keys = (
        "authority_schema", "authority_abi", "authority_generation",
    )
    owner_snapshot_keys = owner_metadata_keys + (
        "validation_mode", "action_time_git_sha", "plan_id",
        "plan_sha256", "control_id", "control_sha256",
        "gate_report_canonical_bytes", "gate_report_id",
        "gate_report_sha256", "gate_receipt_canonical_bytes",
        "gate_receipt_id", "gate_receipt_sha256",
        "gate_bundle_canonical_bytes", "gate_bundle_id",
        "gate_bundle_sha256", "manager_authority",
        "decision_validation_mode", "decision_status",
        "decision_next_state", "decision_manager_a1_passed",
        "decision_a2_unlocked", "decision_provider_invoked",
        "decision_project_data_transferred", "decision_h1_allowed",
        "decision_formal_quality_allowed", "decision_validated_at_utc",
        "return_code", "snapshot_sha256",
    )
    owner_snapshot_schema = (
        "req2web.runtime.real_a1_production_gate_result_owning_snapshot.v1"
    )
    owner_snapshot_abi = (
        "req2web.runtime.real_a1_production_gate_result_owning_snapshot.abi.v1"
    )
    owner_bytes_fields = (
        "gate_report_canonical_bytes", "gate_receipt_canonical_bytes",
        "gate_bundle_canonical_bytes",
    )
    owner_bool_fields = (
        "manager_authority", "decision_manager_a1_passed",
        "decision_a2_unlocked", "decision_provider_invoked",
        "decision_project_data_transferred", "decision_h1_allowed",
        "decision_formal_quality_allowed",
    )
    owner_int_fields = ("return_code",)
    owner_str_fields = tuple(
        key
        for key in owner_snapshot_keys
        if key not in owner_bytes_fields
        and key not in owner_bool_fields
        and key not in owner_int_fields
    )
    owner_hash_fields = (
        "plan_sha256", "control_sha256", "gate_report_sha256",
        "gate_receipt_sha256", "gate_bundle_sha256", "snapshot_sha256",
    )
    result_registry = {}

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
            raise error_type("a3_coordinator_canonical_invalid") from exc

    def digest(raw):
        if type_fn(raw) is not bytes_type:
            raise error_type("a3_coordinator_digest_bytes_required")
        return sha256_fn(raw).hexdigest()

    def exact_map(value, keys, code):
        if (
            type_fn(value) is not dict_type
            or len_fn(value) != len_fn(keys)
            or set_type(value.keys()) != set_type(keys)
        ):
            raise error_type(code)
        return value

    def exact_immutable_snapshot(value, keys, code):
        if (
            type_fn(value) is not mapping_proxy_instance_type
            or len_fn(value) != len_fn(keys)
            or set_type(value.keys()) != set_type(keys)
        ):
            raise error_type(code)
        return value

    def parse_pair(value, code):
        data = exact_map(value, id_hash_keys, code)
        return data["id"], data["sha256"]

    def valid_hex(value, length):
        return (
            type_fn(value) is str_type
            and len_fn(value) == length
            and all(char in "0123456789abcdef" for char in value)
        )

    def owner_snapshot_hash(value):
        rows = []
        for key in owner_snapshot_keys:
            if key == "snapshot_sha256":
                continue
            item = value[key]
            if type_fn(item) is bytes_type:
                rows.append([key, "bytes_hex", item.hex()])
            elif type_fn(item) is bool_type:
                rows.append([key, "bool", item])
            elif type_fn(item) is int_type:
                rows.append([key, "int", item])
            elif type_fn(item) is str_type:
                rows.append([key, "str", item])
            else:
                raise error_type("a3_coordinator_gate_owner_snapshot_type_invalid")
        return digest(canonical(rows))

    def capture_owner_metadata():
        if not callable_fn(gate_owner_accessor):
            return None, (
                "a3_coordinator_gate_owner_accessor_unavailable_"
                "restart_and_rebuild_required"
            )
        try:
            metadata = gate_owner_accessor()
            exact_immutable_snapshot(
                metadata,
                owner_metadata_keys,
                "a3_coordinator_gate_owner_metadata_shape_invalid",
            )
            if (
                type_fn(metadata["authority_schema"]) is not str_type
                or metadata["authority_schema"] != owner_snapshot_schema
                or type_fn(metadata["authority_abi"]) is not str_type
                or metadata["authority_abi"] != owner_snapshot_abi
                or type_fn(metadata["authority_generation"]) is not str_type
                or not metadata["authority_generation"]
            ):
                raise error_type("a3_coordinator_gate_owner_metadata_invalid")
            return mapping_proxy_type(dict_copy(metadata)), None
        except exception_type:
            return None, (
                "a3_coordinator_gate_owner_metadata_invalid_"
                "restart_and_rebuild_required"
            )

    owner_metadata, owner_metadata_error = capture_owner_metadata()

    def read_owner_snapshot(result):
        if owner_metadata_error is not None or owner_metadata is None:
            raise error_type(owner_metadata_error)
        if type_fn(result) is not gate_result_type:
            raise error_type(
                "a3_coordinator_gate_owner_generation_or_type_drift_"
                "restart_and_rebuild_required"
            )
        try:
            snapshot = gate_owner_accessor(result)
        except exception_type as exc:
            raise error_type(
                "a3_coordinator_gate_owner_replay_failed_"
                "restart_and_rebuild_required"
            ) from exc
        exact_immutable_snapshot(
            snapshot,
            owner_snapshot_keys,
            "a3_coordinator_gate_owner_snapshot_shape_invalid",
        )
        for key in owner_bytes_fields:
            if type_fn(snapshot[key]) is not bytes_type:
                raise error_type("a3_coordinator_gate_owner_snapshot_type_invalid")
        for key in owner_bool_fields:
            if type_fn(snapshot[key]) is not bool_type:
                raise error_type("a3_coordinator_gate_owner_snapshot_type_invalid")
        for key in owner_int_fields:
            if type_fn(snapshot[key]) is not int_type:
                raise error_type("a3_coordinator_gate_owner_snapshot_type_invalid")
        for key in owner_str_fields:
            if type_fn(snapshot[key]) is not str_type:
                raise error_type("a3_coordinator_gate_owner_snapshot_type_invalid")
        if (
            snapshot["authority_schema"] != owner_metadata["authority_schema"]
            or snapshot["authority_abi"] != owner_metadata["authority_abi"]
            or snapshot["authority_generation"]
            != owner_metadata["authority_generation"]
        ):
            raise error_type(
                "a3_coordinator_gate_owner_generation_drift_"
                "restart_and_rebuild_required"
            )
        if snapshot["return_code"] != 0:
            raise error_type("a3_coordinator_gate_owner_return_code_invalid")
        if (
            digest(snapshot["gate_report_canonical_bytes"])
            != snapshot["gate_report_sha256"]
            or digest(snapshot["gate_receipt_canonical_bytes"])
            != snapshot["gate_receipt_sha256"]
            or digest(snapshot["gate_bundle_canonical_bytes"])
            != snapshot["gate_bundle_sha256"]
            or snapshot["snapshot_sha256"] != owner_snapshot_hash(snapshot)
        ):
            raise error_type("a3_coordinator_gate_owner_snapshot_hash_invalid")
        if any(not valid_hex(snapshot[key], 64) for key in owner_hash_fields):
            raise error_type("a3_coordinator_gate_owner_snapshot_hash_invalid")
        if not valid_hex(snapshot["action_time_git_sha"], 40):
            raise error_type("a3_coordinator_gate_owner_action_sha_invalid")
        return snapshot

    def owning_gate_snapshot(result):
        snapshot = read_owner_snapshot(result)
        provenance = {
            "report_id": snapshot["gate_report_id"],
            "report_sha256": snapshot["gate_report_sha256"],
            "gate_receipt_id": snapshot["gate_receipt_id"],
            "gate_receipt_sha256": snapshot["gate_receipt_sha256"],
            "gate_bundle_id": snapshot["gate_bundle_id"],
            "gate_bundle_sha256": snapshot["gate_bundle_sha256"],
            "source_decision_status": snapshot["decision_status"],
            "source_manager_a1_passed": snapshot[
                "decision_manager_a1_passed"
            ],
            "source_a2_unlocked": snapshot["decision_a2_unlocked"],
            "gate_result_snapshot_sha256": snapshot["snapshot_sha256"],
        }
        return snapshot, provenance, snapshot["manager_authority"]

    def validate_trigger(trigger_kind, provenance, manager_authority):
        if type_fn(trigger_kind) is not str_type:
            raise error_type("a3_coordinator_trigger_invalid")
        if trigger_kind == "a1_passed_closeout":
            if manager_authority is not True:
                raise type_error(
                    "a1_passed_trigger_requires_registered_manager_result"
                )
            if (
                provenance["source_manager_a1_passed"] is not True
                or provenance["source_a2_unlocked"] is not True
            ):
                raise error_type("a1_passed_trigger_binding_invalid")
        elif trigger_kind == "a1_failure_cleanup":
            if manager_authority is not False:
                raise type_error(
                    "a1_failure_trigger_requires_nonpassing_registered_result"
                )
            if (
                provenance["source_manager_a1_passed"] is not False
                or provenance["source_a2_unlocked"] is not False
            ):
                raise error_type("a1_failure_trigger_must_not_unlock_a2")
        else:
            raise error_type("a3_coordinator_trigger_invalid")

    def build_request(result, trigger_kind, structural_bindings):
        bindings = exact_map(
            structural_bindings,
            binding_keys,
            "a3_coordinator_binding_exact_keys_invalid",
        )
        for field in (
            "approved_disposition", "d17", "deployment_manifest",
            "no_action_plan", "operational_plan", "controls",
            "model_inventory", "endpoint", "a3", "executor_package",
            "signature_profile", "nonce",
        ):
            if type_fn(bindings[field]) is not dict_type:
                raise error_type(
                    "a3_coordinator_nested_mapping_exact_type_required"
                )
        if type_fn(bindings["root_contracts"]) is not list_type:
            raise error_type("a3_coordinator_root_contracts_exact_type_required")
        for row in bindings["root_contracts"]:
            if type_fn(row) is not dict_type:
                raise error_type(
                    "a3_coordinator_root_contract_exact_type_required"
                )

        gate_snapshot, provenance, manager_authority = owning_gate_snapshot(result)
        validate_trigger(trigger_kind, provenance, manager_authority)
        no_action_plan_id, no_action_plan_sha = parse_pair(
            bindings["no_action_plan"], "a3_coordinator_no_action_plan_invalid"
        )
        operational_plan_id, operational_plan_sha = parse_pair(
            bindings["operational_plan"],
            "a3_coordinator_operational_plan_invalid",
        )
        controls_id, controls_sha = parse_pair(
            bindings["controls"], "a3_coordinator_controls_invalid"
        )
        if (
            operational_plan_id != gate_snapshot["plan_id"]
            or operational_plan_sha != gate_snapshot["plan_sha256"]
        ):
            raise error_type(
                "a3_coordinator_operational_plan_gate_binding_invalid"
            )
        if (
            controls_id != gate_snapshot["control_id"]
            or controls_sha != gate_snapshot["control_sha256"]
        ):
            raise error_type("a3_coordinator_controls_gate_binding_invalid")

        disposition_id, disposition_sha = parse_pair(
            bindings["approved_disposition"],
            "a3_coordinator_disposition_invalid",
        )
        root = {
            "schema_version": "req2web.runtime.action_time_authority_request.v1",
            "request_id": "pending",
            "protocol_status": "structural_protocol_ready_no_action",
            "trigger_kind": trigger_kind,
            "action_time_git_sha": bindings["action_time_git_sha"],
            "approved_disposition": {
                "disposition_id": disposition_id,
                "disposition_sha256": disposition_sha,
            },
            "d17": dict_copy(bindings["d17"]),
            "deployment_manifest": dict_copy(bindings["deployment_manifest"]),
            "a1": {
                "no_action_plan_id": no_action_plan_id,
                "no_action_plan_sha256": no_action_plan_sha,
                "operational_plan_id": operational_plan_id,
                "operational_plan_sha256": operational_plan_sha,
                "controls_id": controls_id,
                "controls_sha256": controls_sha,
                "gate_report_id": provenance["report_id"],
                "gate_report_sha256": provenance["report_sha256"],
                "gate_receipt_id": provenance["gate_receipt_id"],
                "gate_receipt_sha256": provenance["gate_receipt_sha256"],
                "gate_bundle_id": provenance["gate_bundle_id"],
                "gate_bundle_sha256": provenance["gate_bundle_sha256"],
                "gate_result_snapshot_sha256": provenance[
                    "gate_result_snapshot_sha256"
                ],
                "source_decision_status": provenance[
                    "source_decision_status"
                ],
                "source_manager_a1_passed": provenance[
                    "source_manager_a1_passed"
                ],
                "source_a2_unlocked": provenance["source_a2_unlocked"],
            },
            "model_inventory": dict_copy(bindings["model_inventory"]),
            "endpoint": dict_copy(bindings["endpoint"]),
            "root_contracts": [
                dict_copy(row) for row in bindings["root_contracts"]
            ],
            "a3": dict_copy(bindings["a3"]),
            "executor_package": dict_copy(bindings["executor_package"]),
            "signature_profile": dict_copy(bindings["signature_profile"]),
            "nonce": dict_copy(bindings["nonce"]),
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
        identity_root = dict_copy(root)
        identity_root.pop("request_id")
        root["request_id"] = (
            "action-time-request-" + digest(canonical(identity_root))[:20]
        )
        return request_parse_trusted(canonical(root))

    class A3ActionTimeCoordinatorResult:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            raise type_error("a3_coordinator_result_factory_required")

        @property
        def request(self):
            return parse_result(self)[0]

        @property
        def capsule(self):
            return parse_result(self)[1]

        @property
        def executor_contract(self):
            return parse_result(self)[2]

        @property
        def status(self):
            return registered_result_data(self)["status"]

        @property
        def permit_issued(self):
            return False

        @property
        def destructive_action_authorized(self):
            return False

    result_type = A3ActionTimeCoordinatorResult

    def registered_result_data(instance):
        if type_fn(instance) is not result_type:
            raise type_error("a3_coordinator_result_exact_type_required")
        entry = result_registry.get(id_fn(instance))
        if entry is None or entry[0]() is not instance:
            raise type_error("a3_coordinator_result_not_registered")
        return entry[1]

    def parse_result(instance):
        data = registered_result_data(instance)
        request = request_parse_trusted(data["request_bytes"])
        capsule = capsule_parse_trusted(data["capsule_bytes"])
        contract = contract_parse_trusted(data["contract_bytes"])
        if (
            request_sha256_trusted(request) != data["request_sha256"]
            or capsule_sha256_trusted(capsule) != data["capsule_sha256"]
            or contract_sha256_trusted(contract) != data["contract_sha256"]
        ):
            raise type_error("a3_coordinator_result_snapshot_invalid")
        return request, capsule, contract

    def register_result(request, capsule, contract):
        if (
            type_fn(request) is not request_type
            or type_fn(capsule) is not capsule_type
            or type_fn(contract) is not contract_type
        ):
            raise type_error(
                "a3_coordinator_result_component_exact_type_required"
            )
        instance = object_new(result_type)
        identity = id_fn(instance)
        data = mapping_proxy_type(
            {
                "status": "a3_trusted_handoff_structural_readiness_no_action",
                "request_bytes": request_canonical_trusted(request),
                "request_sha256": request_sha256_trusted(request),
                "capsule_bytes": capsule_canonical_trusted(capsule),
                "capsule_sha256": capsule_sha256_trusted(capsule),
                "contract_bytes": contract_canonical_trusted(contract),
                "contract_sha256": contract_sha256_trusted(contract),
            }
        )

        def discard(stored_ref, identity_key=identity):
            current = result_registry.get(identity_key)
            if current is not None and current[0] is stored_ref:
                result_registry.pop(identity_key, None)

        instance_ref = weakref_ref(instance, discard)
        result_registry[identity] = (instance_ref, data)
        return instance

    def prepare(result, trigger_kind, structural_bindings):
        request = build_request(result, trigger_kind, structural_bindings)
        capsule = capsule_factory(request)
        contract = contract_factory(request, capsule)
        return register_result(request, capsule, contract)

    return {
        "A3ActionTimeCoordinatorResult": A3ActionTimeCoordinatorResult,
        "prepare_a3_action_time_handoff_no_action": prepare,
        "_build_action_time_request_for_tests": build_request,
        "_owning_gate_snapshot_for_tests": owning_gate_snapshot,
        "_canonical_for_tests": canonical,
        "_digest_for_tests": digest,
        "A3_ACTION_TIME_BINDING_KEYS": binding_keys,
        "A3_ACTION_TIME_CLEANUP_MODE": cleanup_mode,
    }


_AUTHORITIES = _build_authorities()
globals().update(_AUTHORITIES)

__all__ = [
    "A3ActionTimeCoordinatorError", "A3ActionTimeCoordinatorResult",
    "prepare_a3_action_time_handoff_no_action", "A3_ACTION_TIME_BINDING_KEYS",
    "A3_ACTION_TIME_CLEANUP_MODE",
]
