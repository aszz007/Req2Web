"""Local no-action A1/A2 lifecycle coordination records."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import weakref

from . import autodl_a1_executor as executor_module
from . import autodl_a1_production_gate as gate_module
from . import autodl_action_time_authority as action_authority_module
from . import autodl_a3_action_time_coordinator as coordinator_module
from . import autodl_a3_action_time_issuer as issuer_module
from . import autodl_a3_closeout as closeout_module
from . import autodl_a3_handoff_protocol as handoff_module
from . import autodl_a3_restricted_executor_contract as restricted_module


class A3LifecycleCoordinationError(ValueError):
    """Raised when a no-action lifecycle coordination artifact is invalid."""


def _build_authorities():
    bool_type, bytes_type, dict_type, int_type, list_type, str_type = (
        bool, bytes, dict, int, list, str
    )
    type_fn, len_fn, set_type, tuple_fn, id_fn = type, len, set, tuple, id
    object_new, type_error = object.__new__, TypeError
    json_loads, json_dumps = json.loads, json.dumps
    json_decode_error, unicode_decode_error = json.JSONDecodeError, UnicodeDecodeError
    sha256_fn, regex_fullmatch = hashlib.sha256, re.fullmatch
    weakref_ref = weakref.ref
    datetime_cls, timezone_utc = datetime, timezone.utc
    error_type = A3LifecycleCoordinationError

    intent_type = issuer_module.ActionTimeIssuanceIntent
    intent_canonical = issuer_module._action_time_issuance_intent_canonical_for_trust
    intent_parse = issuer_module._parse_action_time_issuance_intent_for_trust
    intent_to_dict = issuer_module._action_time_issuance_intent_to_dict_for_trust
    intent_sha = issuer_module._action_time_issuance_intent_sha256_for_trust
    intent_eligible = issuer_module._action_time_issuance_intent_ledger_eligible_for_trust

    coordinator_type = coordinator_module.A3ActionTimeCoordinatorResult
    coordinator_request_getter = coordinator_type.request.fget
    coordinator_capsule_getter = coordinator_type.capsule.fget
    request_canonical = action_authority_module._action_time_request_canonical_for_trust
    request_parse = action_authority_module._parse_action_time_request_for_trust
    request_to_dict = action_authority_module._action_time_request_to_dict_for_trust
    request_sha = action_authority_module._action_time_request_sha256_for_trust
    capsule_canonical = handoff_module._a3_handoff_capsule_canonical_for_trust
    capsule_parse = handoff_module._parse_a3_handoff_capsule_for_trust
    capsule_to_dict = handoff_module._a3_handoff_capsule_to_dict_for_trust
    capsule_sha = handoff_module._a3_handoff_capsule_sha256_for_trust

    restricted_type = restricted_module.A3RestrictedExecutorContract
    restricted_create = restricted_module.create_a3_restricted_executor_contract
    restricted_to_dict = restricted_type.to_dict
    restricted_canonical = restricted_type.canonical_bytes
    restricted_sha = restricted_type.sha256

    execution_receipt_type = executor_module.A1ExecutionReceipt
    execution_receipt_from_dict = execution_receipt_type.from_dict
    execution_receipt_to_dict = execution_receipt_type.to_dict
    execution_receipt_canonical = execution_receipt_type.canonical_bytes
    execution_receipt_sha = execution_receipt_type.sha256

    gate_result_type = gate_module.A1ProductionGateResult
    gate_owner_snapshot = gate_module._read_a1_production_gate_result_owning_snapshot
    gate_report_type = gate_module.A1ProductionGateReport
    gate_report_from_bytes = gate_report_type.from_bytes
    gate_report_to_dict = gate_report_type.to_dict

    closeout_bundle_type = closeout_module.A3CloseoutEvidenceBundle
    closeout_receipt_type = closeout_module.A3CloseoutReadinessReceipt
    closeout_bundle_from_dict = closeout_bundle_type.from_dict
    closeout_receipt_from_dict = closeout_receipt_type.from_dict
    closeout_bundle_to_dict = closeout_bundle_type.to_dict
    closeout_receipt_to_dict = closeout_receipt_type.to_dict
    closeout_bundle_sha = closeout_bundle_type.sha256
    closeout_receipt_sha = closeout_receipt_type.sha256
    closeout_validate = closeout_module.validate_a3_closeout_readiness_against
    closeout_error = closeout_module.A3CloseoutError

    schema = "req2web.runtime.a3_lifecycle_coordination_record.v1"
    closeout_status = "a3_closeout_required_no_action"
    separate_status = "a2_candidate_requires_separate_gate_no_action"
    absent_id = "absent"
    absent_hash = "0" * 64
    keys = (
        "schema_version", "record_id", "status", "reason_codes", "source",
        "trigger_kind", "source_manager_authority", "source_manager_a1_passed",
        "source_a2_unlocked", "source_execution_status", "source_closeout_status",
        "a3_closeout_required", "separate_a2_gate_required",
        "manager_consumable", "cleanup_complete", "next_run_allowed",
        "a2_unlocked", "external_action_authorized", "external_action_executed",
        "provider_invoked", "project_data_transferred", "h1_allowed",
        "formal_quality_allowed",
    )
    source_keys = (
        "issuance_id", "issuance_sha256", "request_id", "request_sha256",
        "capsule_id", "capsule_sha256", "restricted_contract_id",
        "restricted_contract_sha256", "gate_snapshot_sha256", "gate_report_id",
        "gate_report_sha256", "gate_receipt_id", "gate_receipt_sha256",
        "gate_bundle_id", "gate_bundle_sha256", "execution_receipt_id",
        "execution_receipt_sha256", "closeout_bundle_id", "closeout_bundle_sha256",
        "closeout_receipt_id", "closeout_receipt_sha256",
    )
    false_keys = (
        "manager_consumable", "cleanup_complete", "next_run_allowed",
        "a2_unlocked", "external_action_authorized", "external_action_executed",
        "provider_invoked", "project_data_transferred", "h1_allowed",
        "formal_quality_allowed",
    )
    allowed_reasons = frozenset((
        "a1_failure_or_non_manager_result",
        "a1_execution_receipt_missing",
        "a1_execution_receipt_mismatch",
        "a1_execution_failed_cleanup_required",
        "action_time_intent_expired",
        "a3_closeout_artifacts_missing",
        "a3_closeout_receipt_mismatch",
        "a3_closeout_source_binding_mismatch",
        "a3_closeout_evidence_incomplete",
        "a3_closeout_not_executed",
        "a3_structural_evidence_not_manager_authority",
        "separate_a2_gate_required",
    ))
    record_registry = {}
    eligible_restricted_registry = {}

    def canonical(value):
        return json_dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def digest(raw):
        return sha256_fn(raw).hexdigest()

    def parse_json(raw, code):
        if type_fn(raw) is not bytes_type:
            raise type_error(f"{code}_bytes_required")
        try:
            value = json_loads(raw.decode("utf-8", errors="strict"))
        except (unicode_decode_error, json_decode_error) as exc:
            raise error_type(code) from exc
        if canonical(value) != raw:
            raise error_type(f"{code}_noncanonical")
        return value

    def exact_map(value, expected, code):
        if (
            type_fn(value) is not dict_type
            or len_fn(value) != len_fn(expected)
            or set_type(value.keys()) != set_type(expected)
        ):
            raise error_type(code)
        return value

    def req_id(value, code, allow_absent=False):
        if allow_absent and value == absent_id:
            return value
        if type_fn(value) is not str_type or regex_fullmatch(
            r"[a-z0-9][a-z0-9._:-]{7,159}", value
        ) is None:
            raise error_type(code)
        return value

    def req_hash(value, code):
        if type_fn(value) is not str_type or regex_fullmatch(r"[0-9a-f]{64}", value) is None:
            raise error_type(code)
        return value

    def req_bool(value, expected, code):
        if type_fn(value) is not bool_type or value is not expected:
            raise error_type(code)

    def validate_optional_pair(identity, identity_hash, code):
        if identity == absent_id or identity_hash == absent_hash:
            if identity != absent_id or identity_hash != absent_hash:
                raise error_type(code)
            return
        req_id(identity, code)
        req_hash(identity_hash, code)

    def identity(value):
        root = dict_type(value)
        root.pop("record_id", None)
        return "a3-lifecycle-" + digest(canonical(root))[:20]

    def validate_record_data(value):
        data = dict_type(exact_map(value, keys, "a3_lifecycle_record_exact_keys_invalid"))
        if data["schema_version"] != schema or data["status"] not in (closeout_status, separate_status):
            raise error_type("a3_lifecycle_record_schema_or_status_invalid")
        req_id(data["record_id"], "a3_lifecycle_record_id_invalid")
        reasons = data["reason_codes"]
        if (
            type_fn(reasons) is not list_type
            or reasons != sorted(set_type(reasons))
            or not reasons
            or any(type_fn(code) is not str_type or code not in allowed_reasons for code in reasons)
        ):
            raise error_type("a3_lifecycle_reason_codes_invalid")
        source = exact_map(data["source"], source_keys, "a3_lifecycle_source_exact_keys_invalid")
        for name in (
            "issuance_id", "request_id", "capsule_id", "restricted_contract_id",
            "gate_report_id", "gate_receipt_id", "gate_bundle_id",
        ):
            req_id(source[name], "a3_lifecycle_source_id_invalid")
        for name in (
            "issuance_sha256", "request_sha256", "capsule_sha256",
            "restricted_contract_sha256", "gate_snapshot_sha256",
            "gate_report_sha256", "gate_receipt_sha256", "gate_bundle_sha256",
        ):
            req_hash(source[name], "a3_lifecycle_source_hash_invalid")
        for id_name, hash_name in (
            ("execution_receipt_id", "execution_receipt_sha256"),
            ("closeout_bundle_id", "closeout_bundle_sha256"),
            ("closeout_receipt_id", "closeout_receipt_sha256"),
        ):
            validate_optional_pair(source[id_name], source[hash_name], "a3_lifecycle_optional_source_invalid")
        if data["trigger_kind"] not in ("a1_passed_closeout", "a1_failure_cleanup"):
            raise error_type("a3_lifecycle_trigger_kind_invalid")
        for name in ("source_manager_authority", "source_manager_a1_passed", "source_a2_unlocked"):
            if type_fn(data[name]) is not bool_type:
                raise error_type("a3_lifecycle_source_state_invalid")
        if type_fn(data["source_execution_status"]) is not str_type or type_fn(data["source_closeout_status"]) is not str_type:
            raise error_type("a3_lifecycle_source_status_invalid")
        for name in false_keys:
            req_bool(data[name], False, f"a3_lifecycle_{name}_invalid")
        if data["status"] == closeout_status:
            req_bool(data["a3_closeout_required"], True, "a3_lifecycle_closeout_flag_invalid")
            req_bool(data["separate_a2_gate_required"], False, "a3_lifecycle_separate_gate_flag_invalid")
            if "separate_a2_gate_required" in reasons:
                raise error_type("a3_lifecycle_closeout_reason_invalid")
        else:
            req_bool(data["a3_closeout_required"], False, "a3_lifecycle_closeout_flag_invalid")
            req_bool(data["separate_a2_gate_required"], True, "a3_lifecycle_separate_gate_flag_invalid")
            if reasons != [
                "a3_structural_evidence_not_manager_authority",
                "separate_a2_gate_required",
            ]:
                raise error_type("a3_lifecycle_separate_gate_reason_invalid")
            if (
                data["trigger_kind"] != "a1_passed_closeout"
                or data["source_manager_authority"] is not True
                or data["source_manager_a1_passed"] is not True
                or data["source_a2_unlocked"] is not True
                or data["source_execution_status"] != "a1_passed_awaiting_independent_gate"
                or data["source_closeout_status"] != "a3_structural_evidence_ready_not_manager_consumable"
            ):
                raise error_type("a3_lifecycle_separate_gate_source_invalid")
        if data["record_id"] != identity(data):
            raise error_type("a3_lifecycle_record_identity_invalid")
        return data

    class A3LifecycleCoordinationRecord:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            raise type_error("a3_lifecycle_coordination_record_factory_required")

        def to_dict(self):
            return dict_type(registered_record_data(self))

        def canonical_bytes(self):
            return registered_record_bytes(self)

        def sha256(self):
            return digest(registered_record_bytes(self))

        @classmethod
        def from_bytes(cls, raw):
            if cls is not A3LifecycleCoordinationRecord:
                raise type_error("a3_lifecycle_coordination_record_exact_type_required")
            data = parse_json(raw, "a3_lifecycle_record_bytes_invalid")
            validated = validate_record_data(data)
            if validated["status"] != closeout_status:
                raise error_type(
                    "a3_lifecycle_separate_gate_bytes_replay_forbidden"
                )
            return construct_record(validated)

    record_type = A3LifecycleCoordinationRecord

    def construct_record(data):
        raw = canonical(validate_record_data(data))
        instance = object_new(record_type)
        key = id_fn(instance)
        def discard(stored_ref, identity_key=key):
            current = record_registry.get(identity_key)
            if current is not None and current[0] is stored_ref:
                record_registry.pop(identity_key, None)
        ref = weakref_ref(instance, discard)
        record_registry[key] = (ref, raw)
        return instance

    def registered_record_bytes(instance):
        if type_fn(instance) is not record_type:
            raise type_error("a3_lifecycle_coordination_record_exact_type_required")
        entry = record_registry.get(id_fn(instance))
        if entry is None or entry[0]() is not instance:
            raise type_error("a3_lifecycle_coordination_record_not_registered")
        validate_record_data(parse_json(entry[1], "a3_lifecycle_record_bytes_invalid"))
        return entry[1]

    def registered_record_data(instance):
        return validate_record_data(parse_json(
            registered_record_bytes(instance), "a3_lifecycle_record_bytes_invalid"
        ))

    def register_restricted_contract(contract):
        if type_fn(contract) is not restricted_type:
            raise type_error("a3_lifecycle_restricted_contract_exact_type_required")
        raw = restricted_canonical(contract)
        restricted_to_dict(contract)
        key = id_fn(contract)
        def discard(stored_ref, identity_key=key):
            current = eligible_restricted_registry.get(identity_key)
            if current is not None and current[0] is stored_ref:
                eligible_restricted_registry.pop(identity_key, None)
        ref = weakref_ref(contract, discard)
        eligible_restricted_registry[key] = (ref, raw)
        return contract

    def prepare_restricted(intent, coordinator_result, executor_binding):
        intent_eligible(intent)
        contract = restricted_create(intent, coordinator_result, executor_binding)
        return register_restricted_contract(contract)

    def registered_restricted_data(contract):
        if type_fn(contract) is not restricted_type:
            raise type_error("a3_lifecycle_restricted_contract_exact_type_required")
        entry = eligible_restricted_registry.get(id_fn(contract))
        if entry is None or entry[0]() is not contract:
            raise type_error("a3_lifecycle_restricted_contract_not_lifecycle_registered")
        raw = restricted_canonical(contract)
        data = restricted_to_dict(contract)
        if raw != entry[1] or canonical(data) != raw:
            raise error_type("a3_lifecycle_restricted_contract_drift")
        return data, raw

    def parse_core(intent, coordinator_result, restricted_contract, gate_result):
        if type_fn(intent) is not intent_type:
            raise type_error("a3_lifecycle_intent_exact_type_required")
        intent_eligible(intent)
        parsed_intent = intent_parse(intent_canonical(intent))
        intent_data = intent_to_dict(parsed_intent)
        if type_fn(coordinator_result) is not coordinator_type:
            raise type_error("a3_lifecycle_coordinator_result_exact_type_required")
        request = request_parse(request_canonical(coordinator_request_getter(coordinator_result)))
        request_data = request_to_dict(request)
        capsule = capsule_parse(capsule_canonical(coordinator_capsule_getter(coordinator_result)))
        capsule_data = capsule_to_dict(capsule)
        restricted_data, restricted_raw = registered_restricted_data(restricted_contract)
        if type_fn(gate_result) is not gate_result_type:
            raise type_error("a3_lifecycle_gate_result_exact_type_required")
        snapshot = dict_type(gate_owner_snapshot(gate_result))
        owner = intent_data["a1_owner_snapshot"]
        request_a1 = request_data["a1"]
        if (
            owner["owner_snapshot_sha256"] != snapshot["snapshot_sha256"]
            or owner["plan_id"] != snapshot["plan_id"]
            or owner["plan_sha256"] != snapshot["plan_sha256"]
            or owner["control_id"] != snapshot["control_id"]
            or owner["control_sha256"] != snapshot["control_sha256"]
            or owner["manager_authority"] != snapshot["manager_authority"]
            or owner["manager_a1_passed"] != snapshot["decision_manager_a1_passed"]
            or owner["a2_unlocked"] != snapshot["decision_a2_unlocked"]
        ):
            raise error_type("a3_lifecycle_intent_gate_cross_binding_invalid")
        if (
            request_a1["gate_result_snapshot_sha256"] != snapshot["snapshot_sha256"]
            or request_a1["gate_report_id"] != snapshot["gate_report_id"]
            or request_a1["gate_report_sha256"] != snapshot["gate_report_sha256"]
            or request_a1["gate_receipt_id"] != snapshot["gate_receipt_id"]
            or request_a1["gate_receipt_sha256"] != snapshot["gate_receipt_sha256"]
            or request_a1["gate_bundle_id"] != snapshot["gate_bundle_id"]
            or request_a1["gate_bundle_sha256"] != snapshot["gate_bundle_sha256"]
            or request_a1["source_manager_a1_passed"] != snapshot["decision_manager_a1_passed"]
            or request_a1["source_a2_unlocked"] != snapshot["decision_a2_unlocked"]
        ):
            raise error_type("a3_lifecycle_request_gate_cross_binding_invalid")
        if (
            restricted_data["issuance_id"] != intent_data["issuance_id"]
            or restricted_data["issuance_sha256"] != intent_sha(intent)
            or restricted_data["request_id"] != request_data["request_id"]
            or restricted_data["request_sha256"] != request_sha(request)
            or restricted_data["capsule_id"] != capsule_data["capsule_id"]
            or restricted_data["capsule_sha256"] != capsule_sha(capsule)
        ):
            raise error_type("a3_lifecycle_restricted_source_cross_binding_invalid")
        report = gate_report_from_bytes(snapshot["gate_report_canonical_bytes"])
        report_data = gate_report_to_dict(report)
        if (
            report_data["report_id"] != snapshot["gate_report_id"]
            or digest(snapshot["gate_report_canonical_bytes"]) != snapshot["gate_report_sha256"]
        ):
            raise error_type("a3_lifecycle_gate_report_replay_invalid")
        return (
            intent_data, request, request_data, capsule, capsule_data,
            restricted_data, restricted_raw, snapshot, report_data,
        )

    def parse_execution_receipt(value, report_data):
        if value is None:
            return absent_id, absent_hash, "missing", ["a1_execution_receipt_missing"]
        if type_fn(value) is not execution_receipt_type:
            raise type_error("a3_lifecycle_execution_receipt_exact_type_required")
        parsed = execution_receipt_from_dict(execution_receipt_to_dict(value))
        raw = execution_receipt_canonical(parsed)
        data = execution_receipt_to_dict(parsed)
        result_reasons = []
        if data["receipt_id"] != report_data["receipt_id"] or execution_receipt_sha(parsed) != report_data["receipt_sha256"]:
            result_reasons.append("a1_execution_receipt_mismatch")
        elif data["status"] != "a1_passed_awaiting_independent_gate":
            result_reasons.append("a1_execution_failed_cleanup_required")
        return data["receipt_id"], digest(raw), data["status"], result_reasons

    def parse_closeout(bundle, receipt, report_data):
        if bundle is None or receipt is None:
            return (
                absent_id, absent_hash, absent_id, absent_hash,
                "missing", ["a3_closeout_artifacts_missing"],
            )
        if type_fn(bundle) is not closeout_bundle_type:
            raise type_error("a3_lifecycle_closeout_bundle_exact_type_required")
        if type_fn(receipt) is not closeout_receipt_type:
            raise type_error("a3_lifecycle_closeout_receipt_exact_type_required")
        parsed_bundle = closeout_bundle_from_dict(closeout_bundle_to_dict(bundle))
        parsed_receipt = closeout_receipt_from_dict(closeout_receipt_to_dict(receipt))
        bundle_data = closeout_bundle_to_dict(parsed_bundle)
        receipt_data = closeout_receipt_to_dict(parsed_receipt)
        reasons = []
        try:
            closeout_validate(parsed_receipt, parsed_bundle)
        except closeout_error:
            reasons.append("a3_closeout_receipt_mismatch")
        trigger = bundle_data["trigger"]
        if (
            trigger["a1_plan_id"] != report_data["plan_id"]
            or trigger["a1_plan_sha256"] != report_data["plan_sha256"]
            or trigger["a1_execution_receipt_id"] != report_data["receipt_id"]
            or trigger["a1_execution_receipt_sha256"] != report_data["receipt_sha256"]
        ):
            reasons.append("a3_closeout_source_binding_mismatch")
        if receipt_data["status"] == "a3_evidence_incomplete":
            reasons.append("a3_closeout_evidence_incomplete")
        elif receipt_data["status"] == "a3_not_executed":
            reasons.append("a3_closeout_not_executed")
        return (
            bundle_data["bundle_id"], closeout_bundle_sha(parsed_bundle),
            receipt_data["receipt_id"], closeout_receipt_sha(parsed_receipt),
            receipt_data["status"], reasons,
        )

    def intent_expired(intent_data):
        value = intent_data["nonce"]["expires_at_utc"]
        parsed = datetime_cls.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise error_type("a3_lifecycle_intent_expiry_invalid")
        return datetime_cls.now(timezone_utc) >= parsed.astimezone(timezone_utc)

    def classify_for_tests(
        manager_authority, manager_passed, source_a2_unlocked, trigger_kind,
        execution_status, closeout_source_status, expired, other_reasons,
    ):
        reasons = list_type(other_reasons)
        manager_candidate = (
            manager_authority is True
            and manager_passed is True
            and source_a2_unlocked is True
            and trigger_kind == "a1_passed_closeout"
        )
        if not manager_candidate:
            reasons.append("a1_failure_or_non_manager_result")
        if expired:
            reasons.append("action_time_intent_expired")
        if (
            manager_candidate
            and not reasons
            and execution_status == "a1_passed_awaiting_independent_gate"
            and closeout_source_status == "a3_structural_evidence_ready_not_manager_consumable"
        ):
            return separate_status, [
                "a3_structural_evidence_not_manager_authority",
                "separate_a2_gate_required",
            ]
        if not reasons:
            reasons.append("a3_structural_evidence_not_manager_authority")
        return closeout_status, sorted(set_type(reasons))

    def coordinate(
        intent, coordinator_result, restricted_contract, gate_result,
        execution_receipt=None, closeout_bundle=None, closeout_receipt=None,
    ):
        (
            intent_data, request, request_data, capsule, capsule_data,
            restricted_data, restricted_raw, snapshot, report_data,
        ) = parse_core(intent, coordinator_result, restricted_contract, gate_result)
        execution_id, execution_hash, execution_status, reasons = parse_execution_receipt(
            execution_receipt, report_data
        )
        (
            closeout_bundle_id, closeout_bundle_hash,
            closeout_receipt_id, closeout_receipt_hash,
            closeout_source_status, closeout_reasons,
        ) = parse_closeout(closeout_bundle, closeout_receipt, report_data)
        reasons.extend(closeout_reasons)
        status, reasons = classify_for_tests(
            snapshot["manager_authority"],
            snapshot["decision_manager_a1_passed"],
            snapshot["decision_a2_unlocked"],
            intent_data["trigger_kind"],
            execution_status,
            closeout_source_status,
            intent_expired(intent_data),
            reasons,
        )
        source = {
            "issuance_id": intent_data["issuance_id"],
            "issuance_sha256": intent_sha(intent),
            "request_id": request_data["request_id"],
            "request_sha256": request_sha(request),
            "capsule_id": capsule_data["capsule_id"],
            "capsule_sha256": capsule_sha(capsule),
            "restricted_contract_id": restricted_data["contract_id"],
            "restricted_contract_sha256": digest(restricted_raw),
            "gate_snapshot_sha256": snapshot["snapshot_sha256"],
            "gate_report_id": snapshot["gate_report_id"],
            "gate_report_sha256": snapshot["gate_report_sha256"],
            "gate_receipt_id": snapshot["gate_receipt_id"],
            "gate_receipt_sha256": snapshot["gate_receipt_sha256"],
            "gate_bundle_id": snapshot["gate_bundle_id"],
            "gate_bundle_sha256": snapshot["gate_bundle_sha256"],
            "execution_receipt_id": execution_id,
            "execution_receipt_sha256": execution_hash,
            "closeout_bundle_id": closeout_bundle_id,
            "closeout_bundle_sha256": closeout_bundle_hash,
            "closeout_receipt_id": closeout_receipt_id,
            "closeout_receipt_sha256": closeout_receipt_hash,
        }
        root = {
            "schema_version": schema,
            "record_id": "pending",
            "status": status,
            "reason_codes": reasons,
            "source": source,
            "trigger_kind": intent_data["trigger_kind"],
            "source_manager_authority": snapshot["manager_authority"],
            "source_manager_a1_passed": snapshot["decision_manager_a1_passed"],
            "source_a2_unlocked": snapshot["decision_a2_unlocked"],
            "source_execution_status": execution_status,
            "source_closeout_status": closeout_source_status,
            "a3_closeout_required": status == closeout_status,
            "separate_a2_gate_required": status == separate_status,
            "manager_consumable": False,
            "cleanup_complete": False,
            "next_run_allowed": False,
            "a2_unlocked": False,
            "external_action_authorized": False,
            "external_action_executed": False,
            "provider_invoked": False,
            "project_data_transferred": False,
            "h1_allowed": False,
            "formal_quality_allowed": False,
        }
        root["record_id"] = identity(root)
        return construct_record(root)

    def validate_against(
        record, intent, coordinator_result, restricted_contract, gate_result,
        execution_receipt=None, closeout_bundle=None, closeout_receipt=None,
    ):
        if type_fn(record) is not record_type:
            raise type_error("a3_lifecycle_coordination_record_exact_type_required")
        actual_bytes = registered_record_bytes(record)
        expected = coordinate(
            intent, coordinator_result, restricted_contract, gate_result,
            execution_receipt, closeout_bundle, closeout_receipt,
        )
        if actual_bytes != registered_record_bytes(expected):
            raise error_type("a3_lifecycle_record_live_source_binding_invalid")
        return True

    return {
        "A3LifecycleCoordinationRecord": A3LifecycleCoordinationRecord,
        "prepare_lifecycle_bound_restricted_executor_contract_no_action": prepare_restricted,
        "coordinate_a1_a2_lifecycle_no_action": coordinate,
        "validate_a3_lifecycle_coordination_bytes": A3LifecycleCoordinationRecord.from_bytes,
        "validate_a3_lifecycle_coordination_against": validate_against,
        "_canonical_for_tests": canonical,
        "_digest_for_tests": digest,
        "_classify_lifecycle_state_for_tests": classify_for_tests,
    }


globals().update(_build_authorities())

__all__ = [
    "A3LifecycleCoordinationError",
    "A3LifecycleCoordinationRecord",
    "prepare_lifecycle_bound_restricted_executor_contract_no_action",
    "coordinate_a1_a2_lifecycle_no_action",
    "validate_a3_lifecycle_coordination_bytes",
    "validate_a3_lifecycle_coordination_against",
]
