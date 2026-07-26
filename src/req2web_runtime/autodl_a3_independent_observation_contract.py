"""Local no-action declaration of independent A3 observation requirements.

This module binds already validated structural artifacts.  It performs no
observation, cleanup, release, revocation, transport, or external action.
"""

from __future__ import annotations

import hashlib
import json
import re
import weakref

from req2web_runtime.autodl_a3_closeout import validate_a3_closeout_bundle_bytes
from req2web_runtime.autodl_a3_finalizer import validate_a3_finalizer_envelope_bytes
from req2web_runtime.autodl_a3_remote_closeout import validate_a3_remote_closeout_envelope_bytes


INDEPENDENT_OBSERVATION_CONTRACT_SCHEMA = (
    "req2web.runtime.a3_independent_observation_contract.v1"
)
INDEPENDENT_OBSERVATION_CONTRACT_STATUS = (
    "independent_observation_requirements_declared_no_action"
)


class A3IndependentObservationContractError(ValueError):
    """Raised when an independent-observation declaration is invalid."""


def _build_authorities():
    canonical_json = json.dumps
    parse_json = json.loads
    json_decode_error = json.JSONDecodeError
    sha256_factory = hashlib.sha256
    fullmatch = re.fullmatch
    exact_type = type
    dict_type = dict
    list_type = list
    tuple_type = tuple
    str_type = str
    bool_type = bool
    len_of = len
    sorted_values = sorted
    set_type = set
    enumerate_values = enumerate
    zip_values = zip
    bytes_type = bytes
    unicode_decode_error = UnicodeDecodeError
    object_new = object.__new__
    object_setattr = object.__setattr__
    weak_registry_type = weakref.WeakKeyDictionary
    error_type = A3IndependentObservationContractError
    schema = INDEPENDENT_OBSERVATION_CONTRACT_SCHEMA
    status = INDEPENDENT_OBSERVATION_CONTRACT_STATUS
    validate_bundle = validate_a3_closeout_bundle_bytes
    validate_remote = validate_a3_remote_closeout_envelope_bytes
    validate_finalizer = validate_a3_finalizer_envelope_bytes

    contract_keys = (
        "schema_version",
        "contract_id",
        "status",
        "plan_id",
        "plan_sha256",
        "remote_receipt_id",
        "remote_receipt_sha256",
        "remote_envelope_sha256",
        "remote_event_sha256",
        "foundation_bundle_id",
        "foundation_bundle_sha256",
        "foundation_receipt_id",
        "foundation_receipt_sha256",
        "finalizer_report_id",
        "finalizer_report_sha256",
        "finalizer_receipt_id",
        "finalizer_receipt_sha256",
        "finalizer_envelope_sha256",
        "requirements",
        "manager_consumable",
        "cleanup_complete",
        "release_complete",
        "revocation_complete",
        "next_run_allowed",
        "a2_unlocked",
        "external_action_authorized",
        "external_action_executed",
        "physical_erasure_claimed",
    )
    requirement_keys = (
        "category",
        "evidence_id",
        "evidence_sha256",
        "source_class",
        "structural_source_role",
        "required_independent_source_role",
        "self_certification_accepted",
        "observation_occurred",
        "status",
    )
    requirement_spec = (
        (
            "process_group_listener",
            "process_termination",
            "ssh_remote_observer",
            "remote_executor_receipt",
            "independent_process_group_listener_observer",
        ),
        (
            "root_residual",
            "project_side_deletion",
            "ssh_remote_observer",
            "remote_executor_receipt",
            "independent_root_residual_observer",
        ),
        (
            "instance_release",
            "instance_release",
            "autodl_browser_control_plane_operator",
            "local_receipt",
            "autodl_control_plane_release_observer",
        ),
        (
            "credential_access_revocation",
            "access_revocation",
            "local_operator_attestation",
            "local_receipt",
            "independent_access_revocation_observer",
        ),
    )
    no_action_fields = (
        "manager_consumable",
        "cleanup_complete",
        "release_complete",
        "revocation_complete",
        "next_run_allowed",
        "a2_unlocked",
        "external_action_authorized",
        "external_action_executed",
        "physical_erasure_claimed",
    )

    def canonical(value: object) -> bytes:
        return canonical_json(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def digest(raw: bytes) -> str:
        return sha256_factory(raw).hexdigest()

    def exact_map(value: object, keys: tuple[str, ...], code: str) -> dict[str, object]:
        if exact_type(value) is not dict_type or tuple_type(sorted_values(value)) != tuple_type(sorted_values(keys)):
            raise error_type(code)
        return dict_type(value)

    def require_id(value: object, code: str) -> str:
        if exact_type(value) is not str_type or fullmatch(r"[a-z0-9][a-z0-9._:-]{2,255}", value) is None:
            raise error_type(code)
        return value

    def require_hash(value: object, code: str) -> str:
        if exact_type(value) is not str_type or fullmatch(r"[0-9a-f]{64}", value) is None:
            raise error_type(code)
        return value

    def identify(root: dict[str, object]) -> str:
        body = dict_type(root)
        body.pop("contract_id", None)
        return "autodl-a3-independent-observation-" + digest(canonical(body))[:20]

    def validate_requirement(value: object, index: int) -> dict[str, object]:
        row = exact_map(value, requirement_keys, "independent_observation_requirement_exact_keys_invalid")
        category, evidence_category, source_class, structural_role, independent_role = requirement_spec[index]
        if (
            row["category"] != category
            or row["source_class"] != source_class
            or row["structural_source_role"] != structural_role
            or row["required_independent_source_role"] != independent_role
        ):
            raise error_type("independent_observation_source_separation_invalid")
        require_id(row["evidence_id"], "independent_observation_evidence_id_invalid")
        require_hash(row["evidence_sha256"], "independent_observation_evidence_hash_invalid")
        if row["self_certification_accepted"] is not False:
            raise error_type("independent_observation_self_certification_invalid")
        if row["observation_occurred"] is not False:
            raise error_type("independent_observation_occurrence_claim_invalid")
        if row["status"] != "independent_observer_required":
            raise error_type("independent_observation_requirement_status_invalid")
        if structural_role == independent_role:
            raise error_type("independent_observation_source_separation_invalid")
        if not evidence_category:
            raise error_type("independent_observation_category_invalid")
        return row

    def validate_contract_data(value: object) -> dict[str, object]:
        data = exact_map(value, contract_keys, "independent_observation_contract_exact_keys_invalid")
        if data["schema_version"] != schema or data["status"] != status:
            raise error_type("independent_observation_contract_schema_or_status_invalid")
        require_id(data["plan_id"], "independent_observation_plan_id_invalid")
        require_hash(data["plan_sha256"], "independent_observation_plan_hash_invalid")
        for field in (
            "remote_receipt_id",
            "foundation_bundle_id",
            "foundation_receipt_id",
            "finalizer_report_id",
            "finalizer_receipt_id",
        ):
            require_id(data[field], "independent_observation_artifact_id_invalid")
        for field in (
            "remote_receipt_sha256",
            "remote_envelope_sha256",
            "remote_event_sha256",
            "foundation_bundle_sha256",
            "foundation_receipt_sha256",
            "finalizer_report_sha256",
            "finalizer_receipt_sha256",
            "finalizer_envelope_sha256",
        ):
            require_hash(data[field], "independent_observation_artifact_hash_invalid")
        requirements = data["requirements"]
        if exact_type(requirements) is not list_type or len_of(requirements) != len_of(requirement_spec):
            raise error_type("independent_observation_requirements_invalid")
        parsed = [validate_requirement(row, index) for index, row in enumerate_values(requirements)]
        independent_roles = [row["required_independent_source_role"] for row in parsed]
        if len_of(set_type(independent_roles)) != len_of(independent_roles):
            raise error_type("independent_observation_source_role_merge_invalid")
        for field in no_action_fields:
            if exact_type(data[field]) is not bool_type or data[field] is not False:
                raise error_type("independent_observation_no_action_invariant_invalid")
        require_id(data["contract_id"], "independent_observation_contract_id_invalid")
        if data["contract_id"] != identify(data):
            raise error_type("independent_observation_contract_identity_invalid")
        return data

    registry = weak_registry_type()

    class IndependentObservationContract:
        """Registered canonical declaration; never an observation or authority."""

        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            if args or kwargs or cls is not IndependentObservationContract:
                raise error_type("independent_observation_direct_constructor_invalid")
            raise error_type("independent_observation_direct_constructor_invalid")

        @property
        def data(self) -> dict[str, object]:
            raw = registry.get(self)
            if raw is None:
                raise error_type("independent_observation_contract_not_registered")
            return validate_contract_data(parse_json(raw.decode("utf-8")))

        def to_dict(self) -> dict[str, object]:
            return dict_type(self.data)

        def canonical_bytes(self) -> bytes:
            raw = registry.get(self)
            if raw is None:
                raise error_type("independent_observation_contract_not_registered")
            if canonical(self.data) != raw:
                raise error_type("independent_observation_contract_registry_drift")
            return raw

        def sha256(self) -> str:
            return digest(self.canonical_bytes())

    def register(data: dict[str, object]) -> IndependentObservationContract:
        parsed = validate_contract_data(data)
        raw = canonical(parsed)
        obj = object_new(IndependentObservationContract)
        registry[obj] = raw
        return obj

    def parse_contract_bytes(raw: object) -> dict[str, object]:
        if exact_type(raw) is not bytes_type:
            raise error_type("independent_observation_contract_bytes_invalid")
        try:
            value = parse_json(raw.decode("utf-8"))
        except (unicode_decode_error, json_decode_error) as exc:
            raise error_type("independent_observation_contract_bytes_invalid") from exc
        parsed = validate_contract_data(value)
        if canonical(parsed) != raw:
            raise error_type("independent_observation_contract_bytes_not_canonical")
        return parsed

    def build_expected(
        foundation_bundle_bytes: object,
        foundation_receipt_bytes: object,
        remote_plan_bytes: object,
        remote_envelope_bytes: object,
        remote_event_bytes: object,
        finalizer_envelope_bytes: object,
    ) -> dict[str, object]:
        bundle, readiness = validate_bundle(foundation_bundle_bytes, foundation_receipt_bytes)
        plan, remote_receipt, _remote_commit = validate_remote(
            remote_plan_bytes, remote_envelope_bytes, remote_event_bytes
        )
        finalizer_report, finalizer_receipt = validate_finalizer(finalizer_envelope_bytes)

        evidence = bundle.data["evidence"]
        if exact_type(evidence) is not list_type or len_of(evidence) != len_of(requirement_spec):
            raise error_type("independent_observation_evidence_count_invalid")
        evidence_categories = tuple_type(row["category"] for row in evidence)
        expected_categories = tuple_type(item[1] for item in requirement_spec)
        if evidence_categories != expected_categories:
            raise error_type("independent_observation_evidence_order_invalid")
        if (
            finalizer_report.data["plan_id"],
            finalizer_report.data["plan_sha256"],
            finalizer_report.data["remote_receipt_id"],
            finalizer_report.data["remote_receipt_sha256"],
            finalizer_report.data["foundation_bundle_id"],
            finalizer_report.data["foundation_bundle_sha256"],
            finalizer_report.data["foundation_receipt_id"],
            finalizer_report.data["foundation_receipt_sha256"],
        ) != (
            plan.data["plan_id"],
            plan.sha256(),
            remote_receipt.data["receipt_id"],
            remote_receipt.sha256(),
            bundle.data["bundle_id"],
            bundle.sha256(),
            readiness.data["receipt_id"],
            readiness.sha256(),
        ):
            raise error_type("independent_observation_cross_artifact_binding_invalid")
        release, revocation = evidence[2], evidence[3]
        if (
            finalizer_report.data["release_evidence_id"],
            finalizer_report.data["release_evidence_sha256"],
            finalizer_report.data["revocation_evidence_id"],
            finalizer_report.data["revocation_evidence_sha256"],
        ) != (
            release["evidence_id"],
            digest(canonical(release)),
            revocation["evidence_id"],
            digest(canonical(revocation)),
        ):
            raise error_type("independent_observation_finalizer_evidence_binding_invalid")
        if evidence[0]["source_artifact_sha256"] != remote_receipt.sha256() or evidence[1]["source_artifact_sha256"] != remote_receipt.sha256():
            raise error_type("independent_observation_remote_receipt_binding_invalid")

        requirements = []
        for row, spec in zip_values(evidence, requirement_spec):
            category, _evidence_category, source_class, structural_role, independent_role = spec
            if row["source_class"] != source_class:
                raise error_type("independent_observation_source_class_invalid")
            requirements.append(
                {
                    "category": category,
                    "evidence_id": row["evidence_id"],
                    "evidence_sha256": digest(canonical(row)),
                    "source_class": source_class,
                    "structural_source_role": structural_role,
                    "required_independent_source_role": independent_role,
                    "self_certification_accepted": False,
                    "observation_occurred": False,
                    "status": "independent_observer_required",
                }
            )

        root = {
            "schema_version": schema,
            "contract_id": "pending",
            "status": status,
            "plan_id": plan.data["plan_id"],
            "plan_sha256": plan.sha256(),
            "remote_receipt_id": remote_receipt.data["receipt_id"],
            "remote_receipt_sha256": remote_receipt.sha256(),
            "remote_envelope_sha256": digest(remote_envelope_bytes),
            "remote_event_sha256": digest(remote_event_bytes),
            "foundation_bundle_id": bundle.data["bundle_id"],
            "foundation_bundle_sha256": bundle.sha256(),
            "foundation_receipt_id": readiness.data["receipt_id"],
            "foundation_receipt_sha256": readiness.sha256(),
            "finalizer_report_id": finalizer_report.data["report_id"],
            "finalizer_report_sha256": finalizer_report.sha256(),
            "finalizer_receipt_id": finalizer_receipt.data["receipt_id"],
            "finalizer_receipt_sha256": finalizer_receipt.sha256(),
            "finalizer_envelope_sha256": digest(finalizer_envelope_bytes),
            "requirements": requirements,
            **{field: False for field in no_action_fields},
        }
        root["contract_id"] = identify(root)
        return validate_contract_data(root)

    def create_contract(
        foundation_bundle_bytes: object,
        foundation_receipt_bytes: object,
        remote_plan_bytes: object,
        remote_envelope_bytes: object,
        remote_event_bytes: object,
        finalizer_envelope_bytes: object,
    ) -> IndependentObservationContract:
        return register(
            build_expected(
                foundation_bundle_bytes,
                foundation_receipt_bytes,
                remote_plan_bytes,
                remote_envelope_bytes,
                remote_event_bytes,
                finalizer_envelope_bytes,
            )
        )

    def validate_contract_bytes(
        contract_bytes: object,
        foundation_bundle_bytes: object,
        foundation_receipt_bytes: object,
        remote_plan_bytes: object,
        remote_envelope_bytes: object,
        remote_event_bytes: object,
        finalizer_envelope_bytes: object,
    ) -> IndependentObservationContract:
        parsed = parse_contract_bytes(contract_bytes)
        expected = build_expected(
            foundation_bundle_bytes,
            foundation_receipt_bytes,
            remote_plan_bytes,
            remote_envelope_bytes,
            remote_event_bytes,
            finalizer_envelope_bytes,
        )
        if canonical(parsed) != canonical(expected):
            raise error_type("independent_observation_contract_source_binding_invalid")
        return register(parsed)

    return {
        "IndependentObservationContract": IndependentObservationContract,
        "create_independent_observation_contract": create_contract,
        "validate_independent_observation_contract_bytes": validate_contract_bytes,
    }


_AUTH = _build_authorities()
IndependentObservationContract = _AUTH["IndependentObservationContract"]
create_independent_observation_contract = _AUTH["create_independent_observation_contract"]
validate_independent_observation_contract_bytes = _AUTH[
    "validate_independent_observation_contract_bytes"
]

__all__ = [
    "A3IndependentObservationContractError",
    "INDEPENDENT_OBSERVATION_CONTRACT_SCHEMA",
    "INDEPENDENT_OBSERVATION_CONTRACT_STATUS",
    "IndependentObservationContract",
    "create_independent_observation_contract",
    "validate_independent_observation_contract_bytes",
]