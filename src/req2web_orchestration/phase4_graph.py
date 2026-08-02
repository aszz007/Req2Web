from __future__ import annotations

"""Local, deterministic Phase 4 LangGraph foundation.

LangGraph owns scheduling and in-memory checkpoint mechanics only.  Req2Web
code in this module owns validation, stable IDs, mappings, composition, and
fail-closed routing.  The runtime accepts deterministic synthetic fixtures;
it has no Provider, model, production-route, retry, fallback, or network seam.
"""

import base64
import copy
import csv
import hashlib
import importlib.metadata
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from req2web_agent import AgentContextBundle, UseCase
from req2web_generation import RetrievalGuidanceBuilder
from req2web_provider.semantic_candidate import (
    CanonicalPageSpecAssembler,
    MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION,
    ModelSemanticCandidate,
    ProviderRawResponse,
)
from req2web_rag.corpus import ROLE_ORDER


LANGGRAPH_VERSION = "1.2.9"
LANGGRAPH_CHECKPOINT_VERSION = "4.1.1"
STATE_SCHEMA_VERSION = "req2web.phase4.graph_state.p4_02a.v1"
GRAPH_REVISION = "req2web.phase4.graph.p4_02a.v1"
CONTRACT_REVISION = "req2web.phase4.contract.p4_01.v1"
REGISTRY_REVISION = "req2web.phase4.registry.p4_02a.v1"
MAPPING_REVISION = "req2web.phase4.mapping.p4_02a.v1"
COMPOSITION_PROFILE = "p4_01_empty_constraints_edges_v1"
DEPENDENCY_RECEIPT_REVISION = "req2web.phase4.langgraph_acquisition.p4_02a.v1"
CANDIDATE_PROJECTION_STATUS = "candidate_composition_validated_only"
ASSEMBLY_REVISION = "req2web.phase4.synthetic_assembly.p4_02a.v1"
EVENT_REVISION = "req2web.phase4.node_event.p4_02a.v1"
NODE_ORDER = ("F1", "F2", "F3", "F4")
GRAPH_NODE_ORDER = (
    "F1",
    "register_F1",
    "F2",
    "register_F2",
    "F3",
    "register_F3",
    "map_use_cases",
    "F4",
    "register_F4",
    "project_candidate",
    "assemble_page_spec",
)
_SUCCESS_EVENT_TYPES = {
    "F1": "fixture_output_validated",
    "register_F1": "registry_validated",
    "F2": "fixture_output_validated",
    "register_F2": "registry_validated",
    "F3": "fixture_output_validated",
    "register_F3": "registry_validated",
    "map_use_cases": "mapping_validated",
    "F4": "fixture_output_validated",
    "register_F4": "registry_validated",
    "project_candidate": "candidate_composition_validated",
    "assemble_page_spec": "page_spec_assembled",
}
NO_CAPTURE_SHA256 = "sha256:" + ("0" * 64)
_STABLE_ID = re.compile(r"^[a-z][a-z0-9-]{0,95}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_TRACING_ENV = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
_ACTION_STATE = {
    "action_state_version": "req2web.phase4.action_state.p4_02a.v1",
    "runtime_kind": "local_synthetic_langgraph",
    "model_action": False,
    "graph_runtime_execution": True,
    "dependency_installation": False,
    "training": False,
    "remote_action": False,
}
_ACQUISITION_ACTION_STATE = {
    "action_state_version": "req2web.phase4.action_state.p4_02a.v1",
    "runtime_kind": "dependency_acquisition",
    "model_action": False,
    "graph_runtime_execution": False,
    "dependency_installation": True,
    "training": False,
    "remote_action": False,
}
_FAILURE_KEYS = (
    "failure_code",
    "failure_stage",
    "retry_allowed",
    "fallback_allowed",
    "source_refs",
    "message_code",
)
_IDENTITY_KEYS = ("identity_kind", "sha256", "byte_length", "revision")
_REF_KEYS = ("ref_type", "ref_id", "ref_revision")
_DISPOSITION_KEYS = (
    "advisory_node_id",
    "consumer_node_id",
    "disposition",
    "sidecar_ref",
    "sidecar_sha256",
    "reason_code",
    "d17_ref",
)
_STATE_KEYS = (
    "schema_version",
    "graph_revision",
    "case_id",
    "request_id",
    "source_kind",
    "contract_identity",
    "dependency_receipt_identity",
    "b_input",
    "b_identity",
    "constraint_identity",
    "advisory_dispositions",
    "node_results",
    "pending_node_id",
    "pending_output",
    "registry_inventory",
    "registry_identities",
    "mapping_record",
    "candidate_composition_record",
    "assembly_record",
    "events",
    "execution_counts",
    "completed_graph_nodes",
    "status",
    "failure",
    "action_state",
)


class Phase4GraphState(TypedDict):
    schema_version: str
    graph_revision: str
    case_id: str
    request_id: str
    source_kind: str
    contract_identity: dict[str, object]
    dependency_receipt_identity: dict[str, object]
    b_input: dict[str, object]
    b_identity: dict[str, object]
    constraint_identity: dict[str, object]
    advisory_dispositions: list[dict[str, object]]
    node_results: dict[str, dict[str, object]]
    pending_node_id: str | None
    pending_output: dict[str, object] | None
    registry_inventory: list[dict[str, object]]
    registry_identities: dict[str, dict[str, object] | None]
    mapping_record: dict[str, object] | None
    candidate_composition_record: dict[str, object] | None
    assembly_record: dict[str, object] | None
    events: list[dict[str, object]]
    execution_counts: dict[str, int]
    completed_graph_nodes: list[str]
    status: str
    failure: dict[str, object] | None
    action_state: dict[str, object]


class Phase4ContractError(ValueError):
    """Raised before graph execution when a public contract is invalid."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase4ContractError("value is not canonical JSON") from exc


def _canonical_value(value: object) -> object:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(value: object, keys: tuple[str, ...], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or tuple(value) != keys:
        raise Phase4ContractError(f"{name} exact keys are invalid")
    return value


def _text(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\ufffd" in value
    ):
        raise Phase4ContractError(f"{name} must be non-empty canonical text")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise Phase4ContractError(f"{name} must be a non-boolean integer")
    return value


def _string_list(value: object, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise Phase4ContractError(f"{name} must be a list")
    result = [_text(item, name) for item in value]
    if len(result) != len(set(result)):
        raise Phase4ContractError(f"{name} must be duplicate-free")
    return result


def _identity(value: object, name: str) -> dict[str, object]:
    data = _object(value, _IDENTITY_KEYS, name)
    if data["identity_kind"] not in {"canonical_json", "canonical_row_list"}:
        raise Phase4ContractError(f"{name} kind is invalid")
    digest = _text(data["sha256"], f"{name}.sha256")
    if not digest.startswith("sha256:") or _HEX.fullmatch(digest[7:]) is None:
        raise Phase4ContractError(f"{name} digest is invalid")
    _integer(data["byte_length"], f"{name}.byte_length")
    _text(data["revision"], f"{name}.revision")
    return data


def make_identity(
    value: object,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    raw = _canonical_bytes(value)
    result: dict[str, object] = {
        "identity_kind": identity_kind,
        "sha256": _sha256(raw),
        "byte_length": len(raw),
        "revision": revision,
    }
    _identity(result, "identity")
    return result


def _identity_matches(
    supplied: object,
    value: object,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> bool:
    try:
        data = _identity(supplied, "identity")
    except Phase4ContractError:
        return False
    return data == make_identity(
        value, revision=revision, identity_kind=identity_kind
    )


def _dependency_receipt_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "phase4_langgraph_dependency_acquisition_receipt.json"
    )


def _requirements_file_identity() -> dict[str, object]:
    path = Path(__file__).resolve().parents[2] / "requirements-phase4-agent.txt"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Phase4ContractError("Phase 4 requirements file is unavailable") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise Phase4ContractError("Phase 4 requirements file must not use a BOM")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise Phase4ContractError("Phase 4 requirements file is not UTF-8") from exc
    expected_pin = f"langgraph=={LANGGRAPH_VERSION}"
    if lines != [expected_pin]:
        raise Phase4ContractError("Phase 4 direct dependency pin drift")
    canonical = (expected_pin + "\n").encode("utf-8")
    return {
        "relative_path": "requirements-phase4-agent.txt",
        "canonical_byte_length": len(canonical),
        "canonical_sha256": _sha256(canonical),
    }


def _verify_distribution_record_files(
    distribution: importlib.metadata.Distribution,
    record: str,
) -> list[dict[str, object]]:
    environment_root = Path(sys.prefix).resolve(strict=True)
    inventory: list[dict[str, object]] = []
    rows = list(csv.reader(record.splitlines()))
    if not rows:
        raise Phase4ContractError("dependency RECORD is empty")
    grouped: dict[str, list[list[str]]] = {}
    for row in rows:
        if len(row) != 3:
            raise Phase4ContractError("dependency RECORD row shape is invalid")
        relative_path = row[0]
        normalized = relative_path.replace("\\", "/")
        if not relative_path or "\x00" in relative_path:
            raise Phase4ContractError("dependency RECORD path is invalid")
        grouped.setdefault(normalized, []).append(row)
    for normalized, candidates in grouped.items():
        hashed = [row for row in candidates if row[1]]
        if len(hashed) > 1 and len({(row[1], row[2]) for row in hashed}) != 1:
            raise Phase4ContractError("duplicate dependency RECORD hash conflict")
        relative_path, hash_field, size_field = hashed[0] if hashed else candidates[0]
        is_generated_pyc = (
            ("/__pycache__/" in normalized or normalized.startswith("__pycache__/"))
            and normalized.endswith(".pyc")
        )
        if is_generated_pyc:
            continue
        try:
            located = Path(distribution.locate_file(relative_path))
            resolved = located.resolve(strict=True)
        except OSError as exc:
            raise Phase4ContractError("dependency RECORD file is unavailable") from exc
        try:
            if os.path.commonpath((str(environment_root), str(resolved))) != str(
                environment_root
            ):
                raise Phase4ContractError("dependency RECORD path escapes environment")
        except ValueError as exc:
            raise Phase4ContractError("dependency RECORD path root is invalid") from exc
        if not resolved.is_file():
            raise Phase4ContractError("dependency RECORD target is not a file")
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            raise Phase4ContractError("dependency RECORD file cannot be read") from exc
        if size_field:
            if not size_field.isdecimal():
                raise Phase4ContractError("dependency RECORD size is invalid")
            if len(raw) != int(size_field):
                raise Phase4ContractError("installed dependency file drift")
        if hash_field:
            algorithm, separator, encoded_digest = hash_field.partition("=")
            if separator != "=" or algorithm != "sha256" or not encoded_digest:
                raise Phase4ContractError("dependency RECORD hash is invalid")
            try:
                expected_digest = base64.urlsafe_b64decode(
                    encoded_digest + ("=" * (-len(encoded_digest) % 4))
                )
            except (ValueError, TypeError) as exc:
                raise Phase4ContractError("dependency RECORD digest is invalid") from exc
            if hashlib.sha256(raw).digest() != expected_digest:
                raise Phase4ContractError("installed dependency file drift")
        inventory.append(
            {
                "distribution": str(distribution.metadata["Name"]),
                "relative_path": normalized,
                "byte_length": len(raw),
                "sha256": _sha256(raw),
            }
        )
    return inventory


def _installed_langgraph_state() -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    pending = ["langgraph"]
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    file_inventory: list[dict[str, object]] = []
    while pending:
        requested = pending.pop(0)
        normalized = canonicalize_name(requested)
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            distribution = importlib.metadata.distribution(requested)
        except importlib.metadata.PackageNotFoundError as exc:
            raise Phase4ContractError(
                f"dependency closure distribution is unavailable: {normalized}"
            ) from exc
        record = distribution.read_text("RECORD")
        if record is None:
            raise Phase4ContractError(
                f"dependency closure RECORD is unavailable: {normalized}"
            )
        file_inventory.extend(_verify_distribution_record_files(distribution, record))
        rows.append(
            {
                "distribution": str(distribution.metadata["Name"]),
                "version": distribution.version,
                "record_sha256": _sha256(record.encode("utf-8")),
            }
        )
        for requirement_text in distribution.requires or ():
            requirement = Requirement(requirement_text)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return (
        sorted(rows, key=lambda row: canonicalize_name(row["distribution"])),
        sorted(
            file_inventory,
            key=lambda row: (
                canonicalize_name(str(row["distribution"])),
                str(row["relative_path"]),
            ),
        ),
    )


def _validate_dependency_acquisition_receipt(
    *,
    verify_installed_files: bool,
) -> dict[str, object]:
    path = _dependency_receipt_path()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Phase4ContractError("dependency acquisition receipt is unavailable") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise Phase4ContractError("dependency acquisition receipt must not use a BOM")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase4ContractError("dependency acquisition receipt is invalid JSON") from exc
    data = _object(
        parsed,
        (
            "receipt_version",
            "work_item",
            "source_index",
            "acquisition_network_used",
            "direct_pin",
            "requirements_file_identity",
            "resolved_closure",
            "installed_file_inventory_identity",
            "action_state",
        ),
        "dependency_acquisition_receipt",
    )
    if (
        data["receipt_version"] != DEPENDENCY_RECEIPT_REVISION
        or data["work_item"] != "P4-02a"
        or data["source_index"] != "https://pypi.org/simple"
        or data["acquisition_network_used"] is not True
        or data["direct_pin"] != f"langgraph=={LANGGRAPH_VERSION}"
    ):
        raise Phase4ContractError("dependency acquisition receipt scope drift")
    requirements_identity = _object(
        data["requirements_file_identity"],
        ("relative_path", "canonical_byte_length", "canonical_sha256"),
        "dependency_acquisition_receipt.requirements_file_identity",
    )
    if requirements_identity != _requirements_file_identity():
        raise Phase4ContractError("dependency requirements-file identity drift")
    _validate_exact_action_state(
        data["action_state"],
        _ACQUISITION_ACTION_STATE,
        "dependency acquisition action_state",
    )
    if not isinstance(data["resolved_closure"], list):
        raise Phase4ContractError("dependency acquisition closure is invalid")
    for row in data["resolved_closure"]:
        item = _object(
            row,
            ("distribution", "version", "record_sha256"),
            "dependency_acquisition_receipt.resolved_closure.row",
        )
        _text(item["distribution"], "dependency.distribution")
        _text(item["version"], "dependency.version")
        digest = _text(item["record_sha256"], "dependency.record_sha256")
        if not digest.startswith("sha256:") or _HEX.fullmatch(digest[7:]) is None:
            raise Phase4ContractError("dependency RECORD digest is invalid")
    if verify_installed_files:
        installed_closure, installed_file_inventory = _installed_langgraph_state()
        if data["resolved_closure"] != installed_closure:
            raise Phase4ContractError("installed LangGraph dependency closure drift")
        if not _identity_matches(
            data["installed_file_inventory_identity"],
            installed_file_inventory,
            revision="req2web.phase4.langgraph_installed_files.p4_02a.v1",
            identity_kind="canonical_row_list",
        ):
            raise Phase4ContractError("installed dependency file inventory drift")
    return data


def validate_dependency_acquisition_receipt() -> dict[str, object]:
    return _validate_dependency_acquisition_receipt(verify_installed_files=True)


def _ref(value: object, name: str) -> dict[str, object]:
    data = _object(value, _REF_KEYS, name)
    _text(data["ref_type"], f"{name}.ref_type")
    _text(data["ref_id"], f"{name}.ref_id")
    _text(data["ref_revision"], f"{name}.ref_revision")
    return data


def _refs(value: object, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise Phase4ContractError(f"{name} must be a ref list")
    rows = [_ref(item, name) for item in value]
    identities = [tuple(row[key] for key in _REF_KEYS) for row in rows]
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise Phase4ContractError(f"{name} order or uniqueness is invalid")
    return rows


def _failure(
    code: str,
    stage: str,
    *,
    message_code: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "failure_code": _text(code, "failure_code"),
        "failure_stage": _text(stage, "failure_stage"),
        "retry_allowed": False,
        "fallback_allowed": False,
        "source_refs": [],
        "message_code": _text(message_code or code, "message_code"),
    }
    validate_failure(result)
    return result


def validate_failure(value: object) -> dict[str, object]:
    data = _object(value, _FAILURE_KEYS, "failure")
    _text(data["failure_code"], "failure.failure_code")
    _text(data["failure_stage"], "failure.failure_stage")
    if data["retry_allowed"] is not False or data["fallback_allowed"] is not False:
        raise Phase4ContractError("failure must prohibit retry and fallback")
    _refs(data["source_refs"], "failure.source_refs")
    _text(data["message_code"], "failure.message_code")
    return data


def validate_runtime_environment() -> None:
    validate_dependency_acquisition_receipt()
    try:
        graph_version = importlib.metadata.version("langgraph")
        checkpoint_version = importlib.metadata.version("langgraph-checkpoint")
    except importlib.metadata.PackageNotFoundError as exc:
        raise Phase4ContractError("required LangGraph package is unavailable") from exc
    if graph_version != LANGGRAPH_VERSION:
        raise Phase4ContractError("LangGraph version drift")
    if checkpoint_version != LANGGRAPH_CHECKPOINT_VERSION:
        raise Phase4ContractError("LangGraph checkpoint version drift")
    for name in _TRACING_ENV:
        if os.environ.get(name, "").strip().casefold() in {"1", "true", "yes", "on"}:
            raise Phase4ContractError("external tracing is prohibited")


def _validate_use_case(value: object) -> dict[str, object]:
    data = _object(
        value,
        ("use_case_id", "title", "actor", "goal", "expected_outcome"),
        "use_case",
    )
    for key in data:
        _text(data[key], f"use_case.{key}")
    return data


def validate_b_input(value: object) -> dict[str, object]:
    data = _object(
        value,
        (
            "case_id",
            "request_id",
            "requirement",
            "requirement_summary",
            "target_device",
            "task_type",
            "constraints",
            "use_cases",
        ),
        "b_input",
    )
    for key in (
        "case_id",
        "request_id",
        "requirement",
        "requirement_summary",
        "target_device",
        "task_type",
    ):
        _text(data[key], f"b_input.{key}")
    _string_list(data["constraints"], "b_input.constraints", allow_empty=True)
    if not isinstance(data["use_cases"], list) or not 2 <= len(data["use_cases"]) <= 4:
        raise Phase4ContractError("b_input.use_cases must contain two to four rows")
    rows = [_validate_use_case(item) for item in data["use_cases"]]
    ids = [str(row["use_case_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise Phase4ContractError("canonical B use-case IDs must be unique")
    return data


def _absent_dispositions() -> list[dict[str, object]]:
    return [
        {
            "advisory_node_id": "B-Aux",
            "consumer_node_id": node_id,
            "disposition": "absent",
            "sidecar_ref": None,
            "sidecar_sha256": None,
            "reason_code": "sidecar_absent",
            "d17_ref": None,
        }
        for node_id in NODE_ORDER
    ]


def _validate_dispositions(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) != len(NODE_ORDER):
        raise Phase4ContractError("exactly four advisory dispositions are required")
    for expected, item in zip(NODE_ORDER, value, strict=True):
        data = _object(item, _DISPOSITION_KEYS, "advisory_disposition")
        if (
            data["advisory_node_id"] != "B-Aux"
            or data["consumer_node_id"] != expected
            or data["disposition"] != "absent"
            or data["sidecar_ref"] is not None
            or data["sidecar_sha256"] is not None
            or data["reason_code"] != "sidecar_absent"
            or data["d17_ref"] is not None
        ):
            raise Phase4ContractError("P4-02a B-Aux disposition is invalid")
    return value


def _validate_exact_action_state(
    value: object,
    expected: Mapping[str, object],
    name: str,
) -> dict[str, object]:
    data = _object(value, tuple(expected), name)
    if data != expected or any(
        type(data[key]) is not type(expected_value)
        for key, expected_value in expected.items()
    ):
        raise Phase4ContractError(f"{name.replace('_', '-')} declaration drift")
    return data


def _validate_action_state(value: object) -> dict[str, object]:
    return _validate_exact_action_state(value, _ACTION_STATE, "action_state")


def create_initial_state(b_input: Mapping[str, object]) -> Phase4GraphState:
    validated = validate_b_input(dict(b_input))
    contract_root = {
        "contract_revision": CONTRACT_REVISION,
        "graph_revision": GRAPH_REVISION,
        "node_order": list(NODE_ORDER),
    }
    constraints = validated["constraints"]
    return Phase4GraphState(
        schema_version=STATE_SCHEMA_VERSION,
        graph_revision=GRAPH_REVISION,
        case_id=str(validated["case_id"]),
        request_id=str(validated["request_id"]),
        source_kind="deterministic_synthetic_fixture",
        contract_identity=make_identity(
            contract_root, revision=CONTRACT_REVISION
        ),
        dependency_receipt_identity=make_identity(
            validate_dependency_acquisition_receipt(),
            revision=DEPENDENCY_RECEIPT_REVISION,
        ),
        b_input=copy.deepcopy(validated),
        b_identity=make_identity(validated, revision="canonical_b.p4.v1"),
        constraint_identity=make_identity(
            constraints,
            revision="canonical_b.constraints.p4.v1",
            identity_kind="canonical_row_list",
        ),
        advisory_dispositions=_absent_dispositions(),
        node_results={},
        pending_node_id=None,
        pending_output=None,
        registry_inventory=[],
        registry_identities={node: None for node in NODE_ORDER},
        mapping_record=None,
        candidate_composition_record=None,
        assembly_record=None,
        events=[],
        execution_counts={node: 0 for node in NODE_ORDER},
        completed_graph_nodes=[],
        status="ready",
        failure=None,
        action_state=dict(_ACTION_STATE),
    )


def _state_identity(state: Mapping[str, object]) -> dict[str, object]:
    projection = {key: state[key] for key in _STATE_KEYS if key != "events"}
    return make_identity(projection, revision="req2web.phase4.state.core.v1")


def _validate_event(
    value: object,
    expected_seq: int,
    previous_event_identity: object,
) -> dict[str, object]:
    data = _object(
        value,
        (
            "event_seq",
            "graph_node",
            "event_type",
            "status",
            "state_audit_identity",
            "previous_event_identity",
            "event_identity",
        ),
        "event",
    )
    if _integer(data["event_seq"], "event.event_seq", minimum=1) != expected_seq:
        raise Phase4ContractError("event sequence drift")
    graph_node = _text(data["graph_node"], "event.graph_node")
    event_type = _text(data["event_type"], "event.event_type")
    if data["status"] not in {"running", "foundation_completed", "failed_closed"}:
        raise Phase4ContractError("event status is invalid")
    _identity(data["state_audit_identity"], "event.state_audit_identity")
    if data["previous_event_identity"] != previous_event_identity:
        raise Phase4ContractError("event chain predecessor drift")
    if previous_event_identity is not None:
        _identity(previous_event_identity, "event.previous_event_identity")
    expected_type = (
        "failed_closed"
        if data["status"] == "failed_closed"
        else _SUCCESS_EVENT_TYPES.get(graph_node)
    )
    if expected_type is None or event_type != expected_type:
        raise Phase4ContractError("event type/topology drift")
    body = {key: data[key] for key in data if key != "event_identity"}
    if not _identity_matches(
        data["event_identity"],
        body,
        revision=EVENT_REVISION,
    ):
        raise Phase4ContractError("event identity drift")
    return data


def _append_event(
    state: Phase4GraphState,
    graph_node: str,
    event_type: str,
) -> None:
    body: dict[str, object] = {
        "event_seq": len(state["events"]) + 1,
        "graph_node": graph_node,
        "event_type": event_type,
        "status": state["status"],
        "state_audit_identity": _state_identity(state),
        "previous_event_identity": (
            state["events"][-1]["event_identity"] if state["events"] else None
        ),
    }
    state["events"].append(
        {
            **body,
            "event_identity": make_identity(body, revision=EVENT_REVISION),
        }
    )


def _raw_capture() -> dict[str, object]:
    return {
        "state": "not_captured",
        "byte_length": 0,
        "sha256": NO_CAPTURE_SHA256,
        "source_kind": "deterministic_synthetic_fixture",
    }


def _validate_raw_capture(value: object) -> dict[str, object]:
    data = _object(
        value,
        ("state", "byte_length", "sha256", "source_kind"),
        "raw_capture",
    )
    if (
        data["state"] != "not_captured"
        or _integer(data["byte_length"], "raw_capture.byte_length") != 0
        or data["sha256"] != NO_CAPTURE_SHA256
        or data["source_kind"] != "deterministic_synthetic_fixture"
    ):
        raise Phase4ContractError("P4-02a raw-capture sentinel is invalid")
    return data


def _entity_common(
    value: object,
    keys: tuple[str, ...],
    entity_type: str,
    name: str,
) -> dict[str, object]:
    data = _object(value, keys, name)
    _text(data["local_id"], f"{name}.local_id")
    if data["entity_type"] != entity_type:
        raise Phase4ContractError(f"{name}.entity_type is invalid")
    _refs(data["refs"], f"{name}.refs")
    return data


def _validate_f1(output: object) -> dict[str, object]:
    data = _object(output, ("page_title", "layout_pattern", "sections", "components"), "F1.output")
    _text(data["page_title"], "F1.page_title")
    _text(data["layout_pattern"], "F1.layout_pattern")
    if not isinstance(data["sections"], list) or not data["sections"]:
        raise Phase4ContractError("F1.sections must be non-empty")
    if not isinstance(data["components"], list) or not data["components"]:
        raise Phase4ContractError("F1.components must be non-empty")
    section_ids: list[str] = []
    component_order: list[str] = []
    section_components: dict[str, list[str]] = {}
    for raw in data["sections"]:
        row = _entity_common(
            raw,
            ("local_id", "entity_type", "title", "purpose", "component_local_ids", "refs"),
            "section",
            "F1.section",
        )
        section_id = str(row["local_id"])
        section_ids.append(section_id)
        _text(row["title"], "F1.section.title")
        _text(row["purpose"], "F1.section.purpose")
        component_ids = _string_list(row["component_local_ids"], "F1.section.component_local_ids")
        section_components[section_id] = component_ids
        component_order.extend(component_ids)
    if len(section_ids) != len(set(section_ids)) or len(component_order) != len(set(component_order)):
        raise Phase4ContractError("F1 local IDs conflict")
    seen_components: list[str] = []
    for raw in data["components"]:
        row = _entity_common(
            raw,
            ("local_id", "entity_type", "component_type", "section_local_id", "label", "purpose", "refs"),
            "component",
            "F1.component",
        )
        component_id = str(row["local_id"])
        section_id = _text(row["section_local_id"], "F1.component.section_local_id")
        if section_id not in section_components or component_id not in section_components[section_id]:
            raise Phase4ContractError("F1 component ownership is invalid")
        _text(row["component_type"], "F1.component.component_type")
        _text(row["label"], "F1.component.label")
        _text(row["purpose"], "F1.component.purpose")
        seen_components.append(component_id)
    if len(seen_components) != len(set(seen_components)):
        raise Phase4ContractError("F1 component local IDs conflict")
    if set(section_ids) & set(seen_components):
        raise Phase4ContractError("F1 cross-type local IDs conflict")
    if seen_components != component_order:
        raise Phase4ContractError("F1 component order or coverage drift")
    return data


def _f1_ids(state: Mapping[str, object]) -> tuple[list[str], list[str]]:
    output = state["node_results"]["F1"]["payload"]["node_output"]
    return (
        [str(item["local_id"]) for item in output["sections"]],
        [str(item["local_id"]) for item in output["components"]],
    )


def _validate_f2(output: object, state: Mapping[str, object]) -> dict[str, object]:
    data = _object(output, ("states",), "F2.output")
    if not isinstance(data["states"], list) or not data["states"]:
        raise Phase4ContractError("F2.states must be non-empty")
    _, component_order = _f1_ids(state)
    state_ids: list[str] = []
    for raw in data["states"]:
        row = _entity_common(
            raw,
            ("local_id", "entity_type", "name", "description", "visible_component_local_ids", "refs"),
            "state",
            "F2.state",
        )
        state_ids.append(str(row["local_id"]))
        _text(row["name"], "F2.state.name")
        _text(row["description"], "F2.state.description")
        visible = _string_list(row["visible_component_local_ids"], "F2.state.visible_component_local_ids", allow_empty=True)
        if any(item not in component_order for item in visible):
            raise Phase4ContractError("F2 contains a dangling component ref")
        if visible != [item for item in component_order if item in set(visible)]:
            raise Phase4ContractError("F2 visibility order drift")
    if len(state_ids) != len(set(state_ids)):
        raise Phase4ContractError("F2 state local IDs conflict")
    return data


def _f2_ids(state: Mapping[str, object]) -> list[str]:
    output = state["node_results"]["F2"]["payload"]["node_output"]
    return [str(item["local_id"]) for item in output["states"]]


def _validate_f3(output: object, state: Mapping[str, object]) -> dict[str, object]:
    data = _object(output, ("interactions",), "F3.output")
    if not isinstance(data["interactions"], list) or not data["interactions"]:
        raise Phase4ContractError("F3.interactions must be non-empty")
    _, component_ids = _f1_ids(state)
    state_ids = _f2_ids(state)
    interaction_ids: list[str] = []
    for raw in data["interactions"]:
        row = _entity_common(
            raw,
            (
                "local_id", "entity_type", "trigger_component_local_id",
                "source_state_local_id", "action", "target_state_local_id",
                "user_feedback", "refs",
            ),
            "interaction",
            "F3.interaction",
        )
        interaction_ids.append(str(row["local_id"]))
        if row["trigger_component_local_id"] not in component_ids:
            raise Phase4ContractError("F3 trigger component ref is invalid")
        if row["source_state_local_id"] not in state_ids or row["target_state_local_id"] not in state_ids:
            raise Phase4ContractError("F3 state ref is invalid")
        _text(row["action"], "F3.interaction.action")
        _text(row["user_feedback"], "F3.interaction.user_feedback")
    if len(interaction_ids) != len(set(interaction_ids)):
        raise Phase4ContractError("F3 interaction local IDs conflict")
    return data


def _validate_f4(output: object, state: Mapping[str, object]) -> dict[str, object]:
    data = _object(output, ("acceptance_checks",), "F4.output")
    if not isinstance(data["acceptance_checks"], list) or not data["acceptance_checks"]:
        raise Phase4ContractError("F4.acceptance_checks must be non-empty")
    use_case_ids = [str(item["use_case_id"]) for item in state["b_input"]["use_cases"]]
    f2_stable = {
        str(row["stable_id"])
        for row in state["registry_inventory"]
        if row["node_id"] == "F2"
    }
    seen_local: list[str] = []
    covered: set[str] = set()
    for raw in data["acceptance_checks"]:
        row = _entity_common(
            raw,
            ("local_id", "entity_type", "description", "use_case_refs", "state_ref", "refs"),
            "candidate_acceptance_check",
            "F4.acceptance_check",
        )
        seen_local.append(str(row["local_id"]))
        if len(_text(row["description"], "F4.acceptance_check.description")) < 8:
            raise Phase4ContractError("F4 acceptance description is too short")
        refs = _refs(row["use_case_refs"], "F4.acceptance_check.use_case_refs")
        row_use_cases = [str(ref["ref_id"]) for ref in refs]
        if not row_use_cases or any(ref["ref_type"] != "canonical_b_use_case" for ref in refs):
            raise Phase4ContractError("F4 use-case ref type is invalid")
        if row_use_cases != [item for item in use_case_ids if item in set(row_use_cases)]:
            raise Phase4ContractError("F4 use-case order or membership is invalid")
        covered.update(row_use_cases)
        state_ref = _ref(row["state_ref"], "F4.acceptance_check.state_ref")
        if state_ref["ref_type"] != "registry_stable" or state_ref["ref_id"] not in f2_stable:
            raise Phase4ContractError("F4 state ref is invalid")
    if len(seen_local) != len(set(seen_local)) or covered != set(use_case_ids):
        raise Phase4ContractError("F4 IDs or use-case coverage is invalid")
    return data


def _node_validator(node_id: str, output: object, state: Mapping[str, object]) -> dict[str, object]:
    if node_id == "F1":
        return _validate_f1(output)
    if node_id == "F2":
        return _validate_f2(output, state)
    if node_id == "F3":
        return _validate_f3(output, state)
    if node_id == "F4":
        return _validate_f4(output, state)
    raise Phase4ContractError("unknown F node")


def _happy_f1(_: Mapping[str, object]) -> dict[str, object]:
    return {
        "page_title": "Synthetic commerce workflow",
        "layout_pattern": "mobile-single-column",
        "sections": [
            {"local_id": "section-products", "entity_type": "section", "title": "Products", "purpose": "Search and select products", "component_local_ids": ["component-search", "component-cart"], "refs": []},
            {"local_id": "section-checkout", "entity_type": "section", "title": "Checkout", "purpose": "Collect delivery details and submit", "component_local_ids": ["component-form", "component-submit"], "refs": []},
        ],
        "components": [
            {"local_id": "component-search", "entity_type": "component", "component_type": "search_input", "section_local_id": "section-products", "label": "Search products", "purpose": "Enter and filter a product query", "refs": []},
            {"local_id": "component-cart", "entity_type": "component", "component_type": "cart_panel", "section_local_id": "section-products", "label": "Cart", "purpose": "Review selected products", "refs": []},
            {"local_id": "component-form", "entity_type": "component", "component_type": "form", "section_local_id": "section-checkout", "label": "Delivery details", "purpose": "Collect valid delivery input", "refs": []},
            {"local_id": "component-submit", "entity_type": "component", "component_type": "button", "section_local_id": "section-checkout", "label": "Submit order", "purpose": "Submit checkout for validation", "refs": []},
        ],
    }


def _happy_f2(state: Mapping[str, object]) -> dict[str, object]:
    _, components = _f1_ids(state)
    return {
        "states": [
            {"local_id": "state-browse", "entity_type": "state", "name": "Browse", "description": "Products and an empty cart are visible", "visible_component_local_ids": components[:2], "refs": []},
            {"local_id": "state-checkout", "entity_type": "state", "name": "Checkout", "description": "Cart and delivery form are visible", "visible_component_local_ids": components[1:], "refs": []},
            {"local_id": "state-error", "entity_type": "state", "name": "Validation error", "description": "Valid input is retained while invalid fields are explained", "visible_component_local_ids": components[1:], "refs": []},
        ]
    }


def _happy_f3(_: Mapping[str, object]) -> dict[str, object]:
    return {
        "interactions": [
            {"local_id": "interaction-search", "entity_type": "interaction", "trigger_component_local_id": "component-search", "source_state_local_id": "state-browse", "action": "Search and filter products", "target_state_local_id": "state-browse", "user_feedback": "Matching products are shown", "refs": []},
            {"local_id": "interaction-open-checkout", "entity_type": "interaction", "trigger_component_local_id": "component-cart", "source_state_local_id": "state-browse", "action": "Open checkout", "target_state_local_id": "state-checkout", "user_feedback": "Delivery form is shown", "refs": []},
            {"local_id": "interaction-submit", "entity_type": "interaction", "trigger_component_local_id": "component-submit", "source_state_local_id": "state-checkout", "action": "Submit delivery details", "target_state_local_id": "state-error", "user_feedback": "Invalid fields are identified and valid values remain", "refs": []},
        ]
    }


def _stable_for(state: Mapping[str, object], node_id: str, local_id: str) -> str:
    for row in state["registry_inventory"]:
        if row["node_id"] == node_id and row["local_id"] == local_id:
            return str(row["stable_id"])
    raise Phase4ContractError("registered stable ID is missing")


def _happy_f4(state: Mapping[str, object]) -> dict[str, object]:
    use_cases = state["b_input"]["use_cases"]
    state_id = _stable_for(state, "F2", "state-error")
    return {
        "acceptance_checks": [
            {
                "local_id": f"check-{index}",
                "entity_type": "candidate_acceptance_check",
                "description": f"The interface exposes the expected outcome for {row['title']}",
                "use_case_refs": [{"ref_type": "canonical_b_use_case", "ref_id": row["use_case_id"], "ref_revision": "canonical_b.use_case.v1"}],
                "state_ref": {"ref_type": "registry_stable", "ref_id": state_id, "ref_revision": REGISTRY_REVISION},
                "refs": [],
            }
            for index, row in enumerate(use_cases, start=1)
        ]
    }


def _fixture_output(node_id: str, state: Mapping[str, object], scenario: str) -> dict[str, object]:
    if node_id == "F1":
        output = _happy_f1(state)
        if scenario == "f1_duplicate_local_id":
            output["components"][1]["local_id"] = output["components"][0]["local_id"]
        if scenario == "f1_cross_type_local_id":
            shared_id = output["sections"][0]["local_id"]
            output["sections"][0]["component_local_ids"][0] = shared_id
            output["components"][0]["local_id"] = shared_id
        return output
    if node_id == "F2":
        output = _happy_f2(state)
        if scenario == "f2_invalid_reference":
            output["states"][0]["visible_component_local_ids"].append("component-missing")
        return output
    if node_id == "F3":
        return _happy_f3(state)
    if node_id == "F4":
        return _happy_f4(state)
    raise Phase4ContractError("fixture node is invalid")


def _entities(node_id: str, output: Mapping[str, object]) -> list[dict[str, object]]:
    if node_id == "F1":
        return [*output["sections"], *output["components"]]
    if node_id == "F2":
        return list(output["states"])
    if node_id == "F3":
        return list(output["interactions"])
    if node_id == "F4":
        return list(output["acceptance_checks"])
    raise Phase4ContractError("registry node is invalid")


def _stable_id(state: Mapping[str, object], node_id: str, row: Mapping[str, object], occurrence: int) -> tuple[str, str]:
    key = {
        "case_id": state["case_id"],
        "request_id": state["request_id"],
        "node_id": node_id,
        "entity_type": row["entity_type"],
        "local_id": row["local_id"],
        "semantic_row": row,
        "occurrence": occurrence,
        "registry_revision": REGISTRY_REVISION,
    }
    digest = hashlib.sha256(_canonical_bytes(key)).hexdigest()
    stable_id = f"p4-{node_id.casefold()}-{str(row['entity_type']).replace('_', '-')}-{digest[:16]}"
    if _STABLE_ID.fullmatch(stable_id) is None:
        raise Phase4ContractError("derived stable ID is invalid")
    return stable_id, "sha256:" + digest


def _register(state: Phase4GraphState, node_id: str) -> None:
    if state["pending_node_id"] != node_id or state["pending_output"] is None:
        raise Phase4ContractError("registry pending output binding is invalid")
    prior = list(state["registry_inventory"])
    existing_stable = {str(row["stable_id"]) for row in prior}
    new_rows: list[dict[str, object]] = []
    for occurrence, entity in enumerate(_entities(node_id, state["pending_output"])):
        stable_id, key_hash = _stable_id(state, node_id, entity, occurrence)
        if stable_id in existing_stable:
            raise Phase4ContractError("registry stable-ID conflict")
        existing_stable.add(stable_id)
        new_rows.append(
            {
                "node_id": node_id,
                "entity_type": entity["entity_type"],
                "local_id": entity["local_id"],
                "stable_id": stable_id,
                "canonical_key_sha256": key_hash,
            }
        )
    state["registry_inventory"].extend(new_rows)
    identity = make_identity(
        state["registry_inventory"],
        revision=f"{REGISTRY_REVISION}.{node_id.casefold()}",
        identity_kind="canonical_row_list",
    )
    state["registry_identities"][node_id] = identity
    state["node_results"][node_id] = {
        "status": "validated",
        "raw_capture": _raw_capture(),
        "payload": {
            "node_id": node_id,
            "call_count": 0,
            "local_output_identity": make_identity(
                state["pending_output"], revision=f"{node_id}.output.p4.v1"
            ),
            "local_refs": sorted(
                [
                    {"ref_type": "registry_stable", "ref_id": row["stable_id"], "ref_revision": REGISTRY_REVISION}
                    for row in new_rows
                ],
                key=lambda row: tuple(str(row[key]) for key in _REF_KEYS),
            ),
            "failure": None,
            "node_output": state["pending_output"],
        },
    }
    state["pending_node_id"] = None
    state["pending_output"] = None


def _fail_state(state: Phase4GraphState, node_id: str, code: str) -> Phase4GraphState:
    result = copy.deepcopy(state)
    result["status"] = "failed_closed"
    result["failure"] = _failure(code, node_id)
    result["pending_node_id"] = None
    result["pending_output"] = None
    if node_id == "map_use_cases":
        result["mapping_record"] = None
    if node_id == "project_candidate":
        result["candidate_composition_record"] = None
    if node_id == "assemble_page_spec":
        result["assembly_record"] = None
    if node_id in NODE_ORDER and node_id not in result["node_results"]:
        result["node_results"][node_id] = {
            "status": "failed_closed",
            "raw_capture": _raw_capture(),
            "payload": {
                "node_id": node_id,
                "call_count": 0,
                "local_output_identity": None,
                "local_refs": [],
                "failure": result["failure"],
                "node_output": {},
            },
        }
    result["completed_graph_nodes"].append(node_id)
    _append_event(result, node_id, "failed_closed")
    return result


def _registered_ids(state: Mapping[str, object], node_id: str) -> list[str]:
    return [str(row["stable_id"]) for row in state["registry_inventory"] if row["node_id"] == node_id]


def _expected_registry(
    state: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[str, dict[str, object] | None]]:
    inventory: list[dict[str, object]] = []
    identities: dict[str, dict[str, object] | None] = {
        node_id: None for node_id in NODE_ORDER
    }
    for node_id in NODE_ORDER:
        result = state["node_results"].get(node_id)
        if result is None or result.get("status") != "validated":
            continue
        output = result["payload"]["node_output"]
        for occurrence, entity in enumerate(_entities(node_id, output)):
            stable_id, key_hash = _stable_id(state, node_id, entity, occurrence)
            inventory.append(
                {
                    "node_id": node_id,
                    "entity_type": entity["entity_type"],
                    "local_id": entity["local_id"],
                    "stable_id": stable_id,
                    "canonical_key_sha256": key_hash,
                }
            )
        identities[node_id] = make_identity(
            inventory,
            revision=f"{REGISTRY_REVISION}.{node_id.casefold()}",
            identity_kind="canonical_row_list",
        )
    return inventory, identities


def _mapping_record(state: Mapping[str, object]) -> dict[str, object]:
    section_ids = [str(row["stable_id"]) for row in state["registry_inventory"] if row["node_id"] == "F1" and row["entity_type"] == "section"]
    component_ids = [str(row["stable_id"]) for row in state["registry_inventory"] if row["node_id"] == "F1" and row["entity_type"] == "component"]
    interaction_ids = _registered_ids(state, "F3")
    ordered = [
        {
            "use_case_id": item["use_case_id"],
            "section_stable_ids": section_ids,
            "component_stable_ids": component_ids,
            "interaction_stable_ids": interaction_ids,
        }
        for item in state["b_input"]["use_cases"]
    ]
    return {
        "mapping_revision": MAPPING_REVISION,
        "input_identities": {
            "b_identity": state["b_identity"],
            "constraint_identity": state["constraint_identity"],
            "f1_cumulative_registry_identity": state["registry_identities"]["F1"],
            "f2_cumulative_registry_identity": state["registry_identities"]["F2"],
            "f3_cumulative_registry_identity": state["registry_identities"]["F3"],
            "f1_advisory_identity": None,
            "f2_advisory_identity": None,
            "f3_advisory_identity": None,
        },
        "ordered_mappings": ordered,
        "status": "mapped",
        "failure": None,
    }


def _validate_mapping(value: object, state: Mapping[str, object]) -> dict[str, object]:
    data = _object(value, ("mapping_revision", "input_identities", "ordered_mappings", "status", "failure"), "mapping")
    if data["mapping_revision"] != MAPPING_REVISION or data["status"] != "mapped" or data["failure"] is not None:
        raise Phase4ContractError("mapping status is invalid")
    expected = _mapping_record(state)
    if data != expected:
        raise Phase4ContractError("mapping authority drift")
    return data


def _candidate_composition(state: Mapping[str, object]) -> dict[str, object]:
    f1 = state["node_results"]["F1"]["payload"]["node_output"]
    f2 = state["node_results"]["F2"]["payload"]["node_output"]
    f3 = state["node_results"]["F3"]["payload"]["node_output"]
    f4 = state["node_results"]["F4"]["payload"]["node_output"]
    mappings = state["mapping_record"]["ordered_mappings"]
    all_use_cases = [str(item["use_case_id"]) for item in state["b_input"]["use_cases"]]
    section_ids = [_stable_for(state, "F1", item["local_id"]) for item in f1["sections"]]
    candidate = {
        "schema_version": MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION,
        "title": f1["page_title"],
        "layout": {"pattern": f1["layout_pattern"], "section_stable_ids": section_ids},
        "sections": [
            {
                "stable_id": _stable_for(state, "F1", row["local_id"]),
                "title": row["title"],
                "purpose": row["purpose"],
                "component_stable_ids": [_stable_for(state, "F1", item) for item in row["component_local_ids"]],
                "use_case_ids": all_use_cases,
            }
            for row in f1["sections"]
        ],
        "components": [
            {
                "stable_id": _stable_for(state, "F1", row["local_id"]),
                "section_stable_id": _stable_for(state, "F1", row["section_local_id"]),
                "component_type": row["component_type"],
                "label": row["label"],
                "purpose": row["purpose"],
            }
            for row in f1["components"]
        ],
        "states": [
            {
                "stable_id": _stable_for(state, "F2", row["local_id"]),
                "name": row["name"],
                "description": row["description"],
                "visible_component_stable_ids": [_stable_for(state, "F1", item) for item in row["visible_component_local_ids"]],
            }
            for row in f2["states"]
        ],
        "interactions": [
            {
                "stable_id": _stable_for(state, "F3", row["local_id"]),
                "trigger_component_stable_id": _stable_for(state, "F1", row["trigger_component_local_id"]),
                "source_state_stable_id": _stable_for(state, "F2", row["source_state_local_id"]),
                "action": row["action"],
                "target_state_stable_id": _stable_for(state, "F2", row["target_state_local_id"]),
                "user_feedback": row["user_feedback"],
                "use_case_ids": all_use_cases,
            }
            for row in f3["interactions"]
        ],
        "constraints": [],
        "acceptance_checks": [
            {
                "stable_id": _stable_for(state, "F4", row["local_id"]),
                "description": row["description"],
                "use_case_ids": [ref["ref_id"] for ref in row["use_case_refs"]],
                "state_stable_id": row["state_ref"]["ref_id"],
            }
            for row in f4["acceptance_checks"]
        ],
        "use_case_mappings": mappings,
        "claimed_attribution_edges": [],
    }
    parsed = ModelSemanticCandidate.from_dict(candidate)
    raw = parsed.canonical_json_bytes()
    reparsed = ModelSemanticCandidate.from_dict(json.loads(raw.decode("utf-8")))
    if reparsed.canonical_json_bytes() != raw:
        raise Phase4ContractError("candidate canonical round-trip drift")
    registry = {
        "f1_cumulative": state["registry_identities"]["F1"],
        "f2_cumulative": state["registry_identities"]["F2"],
        "f3_cumulative": state["registry_identities"]["F3"],
        "f4_cumulative": state["registry_identities"]["F4"],
        "final_cumulative": state["registry_identities"]["F4"],
    }
    return {
        "composition_profile": COMPOSITION_PROFILE,
        "b_identity": state["b_identity"],
        "constraint_identity": state["constraint_identity"],
        "registry_identities": registry,
        "mapping_identity": make_identity(state["mapping_record"], revision=MAPPING_REVISION),
        "f4_candidate_identity": state["node_results"]["F4"]["payload"]["local_output_identity"],
        "model_semantic_candidate_schema_version": MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION,
        "model_semantic_candidate_canonical_b64": base64.b64encode(raw).decode("ascii"),
        "model_semantic_candidate_byte_length": len(raw),
        "model_semantic_candidate_sha256": _sha256(raw),
        "candidate_projection_status": CANDIDATE_PROJECTION_STATUS,
        "page_spec_assembler_status_at_projection": "not_executed_p4_02a",
        "integrated_success": False,
        "failure": None,
    }


def _validate_candidate_composition(
    value: object,
    state: Mapping[str, object],
) -> dict[str, object]:
    data = _object(
        value,
        (
            "composition_profile", "b_identity", "constraint_identity",
            "registry_identities", "mapping_identity", "f4_candidate_identity",
            "model_semantic_candidate_schema_version",
            "model_semantic_candidate_canonical_b64",
            "model_semantic_candidate_byte_length",
            "model_semantic_candidate_sha256", "candidate_projection_status",
            "page_spec_assembler_status_at_projection", "integrated_success", "failure",
        ),
        "candidate_composition",
    )
    if (
        data["composition_profile"] != COMPOSITION_PROFILE
        or data["candidate_projection_status"] != CANDIDATE_PROJECTION_STATUS
        or data["page_spec_assembler_status_at_projection"] != "not_executed_p4_02a"
        or data["integrated_success"] is not False
        or data["failure"] is not None
    ):
        raise Phase4ContractError("candidate-composition status is invalid")
    raw = base64.b64decode(
        _text(
            data["model_semantic_candidate_canonical_b64"],
            "candidate_composition.b64",
        ),
        validate=True,
    )
    if (
        _integer(
            data["model_semantic_candidate_byte_length"],
            "candidate_composition.byte_length",
            minimum=1,
        )
        != len(raw)
        or data["model_semantic_candidate_sha256"] != _sha256(raw)
    ):
        raise Phase4ContractError("candidate-composition identity drift")
    candidate = ModelSemanticCandidate.from_dict(json.loads(raw.decode("utf-8")))
    if candidate.constraints or candidate.claimed_attribution_edges:
        raise Phase4ContractError("P4 composition profile constants drift")
    if data != _candidate_composition(state):
        raise Phase4ContractError("candidate-composition authority drift")
    return data


def _synthetic_retrieval_result(role: str) -> dict[str, object]:
    references_by_role: dict[str, list[dict[str, str]]] = {
        "requirement": [
            {"kind": "workflow", "uri": "fixtures/phase4/requirement.json"}
        ],
        "ui_reference": [
            {"kind": "screenshot", "uri": "fixtures/phase4/ui-reference.png"}
        ],
        "interaction_flow": [
            {"kind": "step_screenshot", "uri": "fixtures/phase4/flow-step.png"}
        ],
        "implementation": [
            {"kind": "html", "uri": "fixtures/phase4/implementation.html"}
        ],
        "validation": [
            {"kind": "issue", "uri": "https://example.test/phase4/validation"}
        ],
    }
    text_by_role = {
        "requirement": ("Commerce workflow", "search filter cart checkout form"),
        "ui_reference": ("Mobile commerce reference", "search results cart form button"),
        "interaction_flow": ("Checkout interaction flow", "click search open checkout submit retry"),
        "implementation": ("Responsive commerce implementation", "responsive mobile form cart checkout"),
        "validation": ("Recoverable validation", "error message retains valid input and allows retry"),
    }
    title, summary = text_by_role[role]
    return {
        "score": 1.0,
        "doc_id": f"{role}:phase4:synthetic-commerce",
        "role": role,
        "dataset": "phase4_synthetic_fixture",
        "subset": "p4_02a",
        "sample_id": f"synthetic-commerce-{role}",
        "title": title,
        "summary": summary,
        "references": references_by_role[role],
    }


def _synthetic_assembler_bindings(
    state: Mapping[str, object],
    *,
    mismatched_guidance: bool = False,
) -> tuple[AgentContextBundle, object]:
    b_input = state["b_input"]
    context = AgentContextBundle(
        original_requirement=str(b_input["requirement"]),
        requirement_summary=str(b_input["requirement_summary"]),
        target_device=str(b_input["target_device"]),
        task_type=str(b_input["task_type"]),
        constraints=[str(item) for item in b_input["constraints"]],
        use_cases=[
            UseCase(
                use_case_id=str(item["use_case_id"]),
                title=str(item["title"]),
                actor=str(item["actor"]),
                goal=str(item["goal"]),
                expected_outcome=str(item["expected_outcome"]),
            )
            for item in b_input["use_cases"]
        ],
        retrieval_queries={role: f"phase4 synthetic {role}" for role in ROLE_ORDER},
        retrieval_results={
            role: [_synthetic_retrieval_result(role)] for role in ROLE_ORDER
        },
    )
    context.validate()
    guidance_context = context
    if mismatched_guidance:
        guidance_context = copy.deepcopy(context)
        guidance_context.target_device = "desktop"
    guidance = RetrievalGuidanceBuilder().build(guidance_context)
    return context, guidance


def _assembly_record(
    state: Mapping[str, object],
    *,
    mismatched_guidance: bool = False,
) -> dict[str, object]:
    candidate_record = state["candidate_composition_record"]
    if candidate_record is None:
        raise Phase4ContractError("candidate composition is unavailable for assembly")
    candidate_raw = base64.b64decode(
        str(candidate_record["model_semantic_candidate_canonical_b64"]),
        validate=True,
    )
    context, guidance = _synthetic_assembler_bindings(
        state,
        mismatched_guidance=mismatched_guidance,
    )
    assembled = CanonicalPageSpecAssembler().assemble(
        ProviderRawResponse.from_bytes(candidate_raw),
        context,
        guidance,
    )
    assembled.validate()
    page_spec = _canonical_value(assembled.page_spec.to_dict())
    report = _canonical_value(assembled.report.to_dict())
    downstream = {
        "consistency": "not_executed_p4_02a",
        "acceptance": "not_executed_p4_02a",
        "a07": "not_executed_p4_02a",
        "repair": "not_executed_p4_02a",
        "g0": "not_executed_p4_02a",
        "package": "not_executed_p4_02a",
        "production_route": "not_executed_p4_02a",
    }
    return {
        "assembly_revision": ASSEMBLY_REVISION,
        "candidate_composition_identity": make_identity(
            candidate_record,
            revision="req2web.phase4.candidate_composition.p4_02a.v1",
        ),
        "agent_context_identity": make_identity(
            context.to_dict(),
            revision="req2web.agent.context.v1",
        ),
        "retrieval_guidance_identity": make_identity(
            guidance.to_dict(),
            revision="req2web.retrieval.guidance.v1",
        ),
        "assembled_page_spec": page_spec,
        "assembled_page_spec_identity": make_identity(
            page_spec,
            revision="req2web.page_spec.v1",
        ),
        "assembly_report": report,
        "assembly_report_identity": make_identity(
            report,
            revision="req2web.provider.page_spec_assembly_report.v1",
        ),
        "assembly_status": "assembled_synthetic",
        "downstream_execution": downstream,
        "integrated_success": False,
        "failure": None,
    }


def _validate_assembly_record(
    value: object,
    state: Mapping[str, object],
) -> dict[str, object]:
    data = _object(
        value,
        (
            "assembly_revision",
            "candidate_composition_identity",
            "agent_context_identity",
            "retrieval_guidance_identity",
            "assembled_page_spec",
            "assembled_page_spec_identity",
            "assembly_report",
            "assembly_report_identity",
            "assembly_status",
            "downstream_execution",
            "integrated_success",
            "failure",
        ),
        "assembly_record",
    )
    if (
        data["assembly_revision"] != ASSEMBLY_REVISION
        or data["assembly_status"] != "assembled_synthetic"
        or data["integrated_success"] is not False
        or data["failure"] is not None
    ):
        raise Phase4ContractError("synthetic assembly status is invalid")
    downstream = _object(
        data["downstream_execution"],
        (
            "consistency",
            "acceptance",
            "a07",
            "repair",
            "g0",
            "package",
            "production_route",
        ),
        "assembly_record.downstream_execution",
    )
    if any(value != "not_executed_p4_02a" for value in downstream.values()):
        raise Phase4ContractError("synthetic assembly downstream scope drift")
    expected = _assembly_record(state)
    if data != expected:
        raise Phase4ContractError("synthetic assembly authority drift")
    return data


def _validate_node_result(node_id: str, value: object, state: Mapping[str, object]) -> dict[str, object]:
    data = _object(value, ("status", "raw_capture", "payload"), "node_result")
    _validate_raw_capture(data["raw_capture"])
    payload = _object(data["payload"], ("node_id", "call_count", "local_output_identity", "local_refs", "failure", "node_output"), "node_result.payload")
    if payload["node_id"] != node_id or _integer(payload["call_count"], "node_result.call_count") != 0:
        raise Phase4ContractError("node result binding is invalid")
    if data["status"] == "validated":
        if payload["failure"] is not None or not _identity_matches(payload["local_output_identity"], payload["node_output"], revision=f"{node_id}.output.p4.v1"):
            raise Phase4ContractError("validated node result identity is invalid")
        refs = _refs(payload["local_refs"], "node_result.local_refs")
        _node_validator(node_id, payload["node_output"], state)
        expected_refs = sorted(
            [
                {
                    "ref_type": "registry_stable",
                    "ref_id": row["stable_id"],
                    "ref_revision": REGISTRY_REVISION,
                }
                for row in state["registry_inventory"]
                if row["node_id"] == node_id
            ],
            key=lambda row: tuple(str(row[key]) for key in _REF_KEYS),
        )
        if refs != expected_refs:
            raise Phase4ContractError("node result registry refs drift")
    elif data["status"] == "failed_closed":
        validate_failure(payload["failure"])
        if payload["local_output_identity"] is not None or payload["local_refs"] != [] or payload["node_output"] != {}:
            raise Phase4ContractError("failed node result carries output")
    else:
        raise Phase4ContractError("node result status is invalid")
    return data


def _validate_graph_state_core(
    value: object,
    *,
    require_terminal: bool,
    verify_installed_files: bool,
) -> Phase4GraphState:
    data = _object(value, _STATE_KEYS, "graph_state")
    if data["schema_version"] != STATE_SCHEMA_VERSION or data["graph_revision"] != GRAPH_REVISION:
        raise Phase4ContractError("graph state version drift")
    _text(data["case_id"], "state.case_id")
    _text(data["request_id"], "state.request_id")
    if data["source_kind"] != "deterministic_synthetic_fixture":
        raise Phase4ContractError("graph source is invalid")
    b_input = validate_b_input(data["b_input"])
    if data["case_id"] != b_input["case_id"] or data["request_id"] != b_input["request_id"]:
        raise Phase4ContractError("graph/B identity mismatch")
    contract_root = {"contract_revision": CONTRACT_REVISION, "graph_revision": GRAPH_REVISION, "node_order": list(NODE_ORDER)}
    if not _identity_matches(data["contract_identity"], contract_root, revision=CONTRACT_REVISION):
        raise Phase4ContractError("contract identity drift")
    dependency_receipt = _validate_dependency_acquisition_receipt(
        verify_installed_files=verify_installed_files
    )
    if not _identity_matches(
        data["dependency_receipt_identity"],
        dependency_receipt,
        revision=DEPENDENCY_RECEIPT_REVISION,
    ):
        raise Phase4ContractError("dependency receipt identity drift")
    if not _identity_matches(data["b_identity"], b_input, revision="canonical_b.p4.v1"):
        raise Phase4ContractError("canonical B identity drift")
    if not _identity_matches(data["constraint_identity"], b_input["constraints"], revision="canonical_b.constraints.p4.v1", identity_kind="canonical_row_list"):
        raise Phase4ContractError("constraint identity drift")
    _validate_dispositions(data["advisory_dispositions"])
    _validate_action_state(data["action_state"])
    if not isinstance(data["node_results"], dict) or any(key not in NODE_ORDER for key in data["node_results"]):
        raise Phase4ContractError("node result inventory is invalid")
    node_result_ids = list(data["node_results"])
    if node_result_ids != list(NODE_ORDER[: len(node_result_ids)]):
        raise Phase4ContractError("node result topology order is invalid")
    if not isinstance(data["registry_inventory"], list) or not isinstance(data["registry_identities"], dict) or tuple(data["registry_identities"]) != NODE_ORDER:
        raise Phase4ContractError("registry state is invalid")
    stable_ids: list[str] = []
    for row in data["registry_inventory"]:
        item = _object(row, ("node_id", "entity_type", "local_id", "stable_id", "canonical_key_sha256"), "registry.row")
        if item["node_id"] not in NODE_ORDER or _STABLE_ID.fullmatch(str(item["stable_id"])) is None:
            raise Phase4ContractError("registry row is invalid")
        stable_ids.append(str(item["stable_id"]))
    if len(stable_ids) != len(set(stable_ids)):
        raise Phase4ContractError("registry contains a stable-ID conflict")
    for node_id, result in data["node_results"].items():
        _validate_node_result(node_id, result, data)
    expected_inventory, expected_registry_identities = _expected_registry(data)
    if data["registry_inventory"] != expected_inventory:
        raise Phase4ContractError("registry inventory authority drift")
    if data["registry_identities"] != expected_registry_identities:
        raise Phase4ContractError("registry identity authority drift")
    if data["mapping_record"] is not None:
        _validate_mapping(data["mapping_record"], data)
    if data["candidate_composition_record"] is not None:
        _validate_candidate_composition(data["candidate_composition_record"], data)
    if data["assembly_record"] is not None:
        _validate_assembly_record(data["assembly_record"], data)
    pending_id = data["pending_node_id"]
    pending_output = data["pending_output"]
    if (pending_id is None) != (pending_output is None):
        raise Phase4ContractError("pending node/output binding is incomplete")
    if pending_id is not None:
        if (
            pending_id not in NODE_ORDER
            or pending_id in data["node_results"]
            or not data["completed_graph_nodes"]
            or data["completed_graph_nodes"][-1] != pending_id
        ):
            raise Phase4ContractError("pending node/output topology is invalid")
        _node_validator(pending_id, pending_output, data)
    if not isinstance(data["events"], list):
        raise Phase4ContractError("event inventory is invalid")
    previous_event_identity: object = None
    for index, event in enumerate(data["events"], start=1):
        validated_event = _validate_event(event, index, previous_event_identity)
        previous_event_identity = validated_event["event_identity"]
    if not isinstance(data["execution_counts"], dict) or tuple(data["execution_counts"]) != NODE_ORDER:
        raise Phase4ContractError("execution counts are invalid")
    for count in data["execution_counts"].values():
        if _integer(count, "execution_count") > 1:
            raise Phase4ContractError("fixture execution retry is prohibited")
    if not isinstance(data["completed_graph_nodes"], list) or any(item not in GRAPH_NODE_ORDER for item in data["completed_graph_nodes"]):
        raise Phase4ContractError("completed graph-node inventory is invalid")
    completed = data["completed_graph_nodes"]
    if completed != list(GRAPH_NODE_ORDER[: len(completed)]):
        raise Phase4ContractError("completed graph-node topology is invalid")
    if len(data["events"]) != len(completed) or [event["graph_node"] for event in data["events"]] != completed:
        raise Phase4ContractError("event/completed-node binding is invalid")
    for index, event in enumerate(data["events"]):
        expected_status = data["status"] if index == len(data["events"]) - 1 else "running"
        if event["status"] != expected_status:
            raise Phase4ContractError("event status/topology drift")
    if data["events"] and data["events"][-1]["state_audit_identity"] != _state_identity(data):
        raise Phase4ContractError("latest event state identity drift")
    for node_id, count in data["execution_counts"].items():
        expected_count = 1 if node_id in completed else 0
        if count != expected_count:
            raise Phase4ContractError("fixture execution count/topology drift")
    if data["status"] not in {"ready", "running", "foundation_completed", "failed_closed"}:
        raise Phase4ContractError("graph status is invalid")
    if data["status"] == "ready" and (completed or data["events"] or pending_id is not None):
        raise Phase4ContractError("ready graph carries execution state")
    if data["status"] == "running" and (not completed or completed == list(GRAPH_NODE_ORDER)):
        raise Phase4ContractError("running graph topology is invalid")
    if data["status"] == "failed_closed":
        validate_failure(data["failure"])
        if data["assembly_record"] is not None:
            raise Phase4ContractError("failed graph carries successful assembly")
    elif data["failure"] is not None:
        raise Phase4ContractError("non-failed graph carries failure")
    if data["mapping_record"] is None and "map_use_cases" in completed and data["status"] != "failed_closed":
        raise Phase4ContractError("completed mapping node lacks mapping record")
    if data["mapping_record"] is not None and "map_use_cases" not in completed:
        raise Phase4ContractError("mapping record precedes mapping node")
    if data["candidate_composition_record"] is not None and data["status"] != "foundation_completed":
        if "project_candidate" not in completed:
            raise Phase4ContractError("candidate composition precedes projection node")
    if data["assembly_record"] is not None and (
        data["status"] != "foundation_completed"
        or "assemble_page_spec" not in completed
    ):
        raise Phase4ContractError("assembly record precedes assembly completion")
    if data["status"] == "foundation_completed":
        if (
            completed != list(GRAPH_NODE_ORDER)
            or data["candidate_composition_record"] is None
            or data["assembly_record"] is None
            or pending_id is not None
        ):
            raise Phase4ContractError("completed graph is incomplete")
    if require_terminal and data["status"] not in {"foundation_completed", "failed_closed"}:
        raise Phase4ContractError("graph is not terminal")
    return data  # type: ignore[return-value]


def validate_graph_state(
    value: object,
    *,
    require_terminal: bool = False,
) -> Phase4GraphState:
    """Live-validate public state, including the installed dependency tree."""

    return _validate_graph_state_core(
        value,
        require_terminal=require_terminal,
        verify_installed_files=True,
    )


@dataclass(frozen=True)
class PauseHandle:
    thread_id: str
    case_id: str
    request_id: str
    graph_revision: str
    contract_identity: dict[str, object]
    state_identity: dict[str, object]
    before_node: str
    handle_identity: dict[str, object]

    @classmethod
    def create(cls, state: Mapping[str, object], thread_id: str, before_node: str) -> "PauseHandle":
        root = {
            "thread_id": thread_id,
            "case_id": state["case_id"],
            "request_id": state["request_id"],
            "graph_revision": GRAPH_REVISION,
            "contract_identity": state["contract_identity"],
            "state_identity": _state_identity(state),
            "before_node": before_node,
        }
        return cls(**root, handle_identity=make_identity(root, revision="req2web.phase4.pause_handle.v1"))

    @classmethod
    def from_dict(cls, value: object) -> "PauseHandle":
        data = _object(value, ("thread_id", "case_id", "request_id", "graph_revision", "contract_identity", "state_identity", "before_node", "handle_identity"), "pause_handle")
        root = {key: data[key] for key in data if key != "handle_identity"}
        if data["graph_revision"] != GRAPH_REVISION or data["before_node"] not in GRAPH_NODE_ORDER:
            raise Phase4ContractError("pause handle scope is invalid")
        for key in ("thread_id", "case_id", "request_id"):
            _text(data[key], f"pause_handle.{key}")
        _identity(data["contract_identity"], "pause_handle.contract_identity")
        _identity(data["state_identity"], "pause_handle.state_identity")
        if not _identity_matches(data["handle_identity"], root, revision="req2web.phase4.pause_handle.v1"):
            raise Phase4ContractError("pause handle identity drift")
        return cls(**data)

    def to_dict(self) -> dict[str, object]:
        result = {
            "thread_id": self.thread_id,
            "case_id": self.case_id,
            "request_id": self.request_id,
            "graph_revision": self.graph_revision,
            "contract_identity": self.contract_identity,
            "state_identity": self.state_identity,
            "before_node": self.before_node,
            "handle_identity": self.handle_identity,
        }
        PauseHandle.from_dict(result)
        return result


class Phase4GraphRuntime:
    """Compiled deterministic P4-02a graph with in-memory checkpoints only."""

    def __init__(self, *, fixture_scenario: str = "happy") -> None:
        validate_runtime_environment()
        if fixture_scenario not in {
            "happy",
            "f1_duplicate_local_id",
            "f1_cross_type_local_id",
            "f2_invalid_reference",
            "assembler_binding_mismatch",
        }:
            raise Phase4ContractError("fixture scenario is invalid")
        self.fixture_scenario = fixture_scenario
        self._checkpointer = InMemorySaver()
        builder = StateGraph(Phase4GraphState)
        for node_id in NODE_ORDER:
            builder.add_node(node_id, self._semantic_node(node_id))
            builder.add_node(f"register_{node_id}", self._registry_node(node_id))
        builder.add_node("map_use_cases", self._mapping_node)
        builder.add_node("project_candidate", self._candidate_composition_node)
        builder.add_node("assemble_page_spec", self._assembly_node)
        builder.add_edge(START, "F1")
        edges = (
            ("F1", "register_F1"),
            ("register_F1", "F2"),
            ("F2", "register_F2"),
            ("register_F2", "F3"),
            ("F3", "register_F3"),
            ("register_F3", "map_use_cases"),
            ("map_use_cases", "F4"),
            ("F4", "register_F4"),
            ("register_F4", "project_candidate"),
        )
        for source, target in edges:
            builder.add_conditional_edges(source, self._route, {"continue": target, "stop": END})
        builder.add_conditional_edges(
            "project_candidate",
            self._route,
            {"continue": "assemble_page_spec", "stop": END},
        )
        builder.add_edge("assemble_page_spec", END)
        self._graph = builder.compile(checkpointer=self._checkpointer)

    @staticmethod
    def _route(state: Phase4GraphState) -> str:
        return "stop" if state["status"] == "failed_closed" else "continue"

    def _semantic_node(self, node_id: str):
        def run(state: Phase4GraphState) -> Phase4GraphState:
            current = copy.deepcopy(state)
            try:
                _validate_graph_state_core(
                    current,
                    require_terminal=False,
                    verify_installed_files=False,
                )
                if current["execution_counts"][node_id] != 0:
                    raise Phase4ContractError("fixture execution retry is prohibited")
                current["execution_counts"][node_id] = 1
                current["status"] = "running"
                output = _fixture_output(node_id, current, self.fixture_scenario)
                current["pending_output"] = _node_validator(node_id, output, current)
                current["pending_node_id"] = node_id
                current["completed_graph_nodes"].append(node_id)
                _append_event(current, node_id, "fixture_output_validated")
                return current
            except (KeyError, TypeError, Phase4ContractError, ValueError) as exc:
                code = "node_contract_invalid"
                if "conflict" in str(exc).casefold():
                    code = "registry_conflict"
                return _fail_state(current, node_id, code)
        return run

    def _registry_node(self, node_id: str):
        graph_node = f"register_{node_id}"
        def run(state: Phase4GraphState) -> Phase4GraphState:
            current = copy.deepcopy(state)
            try:
                _validate_graph_state_core(
                    current,
                    require_terminal=False,
                    verify_installed_files=False,
                )
                _register(current, node_id)
                current["completed_graph_nodes"].append(graph_node)
                _append_event(current, graph_node, "registry_validated")
                return current
            except (KeyError, TypeError, Phase4ContractError, ValueError):
                return _fail_state(current, graph_node, "registry_conflict")
        return run

    @staticmethod
    def _mapping_node(state: Phase4GraphState) -> Phase4GraphState:
        current = copy.deepcopy(state)
        try:
            _validate_graph_state_core(
                current,
                require_terminal=False,
                verify_installed_files=False,
            )
            current["mapping_record"] = _mapping_record(current)
            _validate_mapping(current["mapping_record"], current)
            current["completed_graph_nodes"].append("map_use_cases")
            _append_event(current, "map_use_cases", "mapping_validated")
            return current
        except (KeyError, TypeError, Phase4ContractError, ValueError):
            return _fail_state(current, "map_use_cases", "mapping_incomplete")

    @staticmethod
    def _candidate_composition_node(state: Phase4GraphState) -> Phase4GraphState:
        current = copy.deepcopy(state)
        try:
            _validate_graph_state_core(
                current,
                require_terminal=False,
                verify_installed_files=False,
            )
            current["candidate_composition_record"] = _candidate_composition(current)
            _validate_candidate_composition(
                current["candidate_composition_record"], current
            )
            current["status"] = "running"
            current["completed_graph_nodes"].append("project_candidate")
            _append_event(
                current,
                "project_candidate",
                "candidate_composition_validated",
            )
            return current
        except (KeyError, TypeError, Phase4ContractError, ValueError):
            return _fail_state(
                current,
                "project_candidate",
                "candidate_projection_invalid",
            )

    def _assembly_node(self, state: Phase4GraphState) -> Phase4GraphState:
        current = copy.deepcopy(state)
        try:
            _validate_graph_state_core(
                current,
                require_terminal=False,
                verify_installed_files=False,
            )
            current["assembly_record"] = _assembly_record(
                current,
                mismatched_guidance=(
                    self.fixture_scenario == "assembler_binding_mismatch"
                ),
            )
            _validate_assembly_record(current["assembly_record"], current)
            current["status"] = "foundation_completed"
            current["completed_graph_nodes"].append("assemble_page_spec")
            _append_event(
                current,
                "assemble_page_spec",
                "page_spec_assembled",
            )
            return current
        except (KeyError, TypeError, Phase4ContractError, ValueError):
            return _fail_state(
                current,
                "assemble_page_spec",
                "parser_assembler_invalid",
            )

    @staticmethod
    def _config(thread_id: str) -> dict[str, object]:
        return {"configurable": {"thread_id": _text(thread_id, "thread_id")}}

    def invoke(self, state: Mapping[str, object], *, thread_id: str) -> Phase4GraphState:
        validate_runtime_environment()
        validated = validate_graph_state(copy.deepcopy(dict(state)))
        result = self._graph.invoke(validated, config=self._config(thread_id))
        return validate_graph_state(result, require_terminal=True)

    def stream(self, state: Mapping[str, object], *, thread_id: str) -> tuple[tuple[dict[str, object], ...], Phase4GraphState]:
        validate_runtime_environment()
        validated = validate_graph_state(copy.deepcopy(dict(state)))
        chunks: list[dict[str, object]] = []
        config = self._config(thread_id)
        for sequence, chunk in enumerate(self._graph.stream(validated, config=config, stream_mode="updates"), start=1):
            if not isinstance(chunk, dict) or len(chunk) != 1:
                raise Phase4ContractError("LangGraph update stream shape is invalid")
            graph_node, update = next(iter(chunk.items()))
            current = _validate_graph_state_core(
                update,
                require_terminal=False,
                verify_installed_files=False,
            )
            chunks.append({"sequence": sequence, "graph_node": graph_node, "status": current["status"], "state_identity": _state_identity(current)})
        snapshot = self._graph.get_state(config)
        final = validate_graph_state(snapshot.values, require_terminal=True)
        return tuple(chunks), final

    def invoke_until_interrupt(
        self,
        state: Mapping[str, object],
        *,
        thread_id: str,
        before_node: str,
    ) -> PauseHandle:
        validate_runtime_environment()
        if before_node not in GRAPH_NODE_ORDER or before_node == "F1":
            raise Phase4ContractError("interrupt target is invalid")
        validated = validate_graph_state(copy.deepcopy(dict(state)))
        config = self._config(thread_id)
        self._graph.invoke(validated, config=config, interrupt_before=[before_node])
        snapshot = self._graph.get_state(config)
        paused = validate_graph_state(snapshot.values)
        if paused["status"] == "failed_closed" or before_node in paused["completed_graph_nodes"]:
            raise Phase4ContractError("graph did not pause at the requested boundary")
        return PauseHandle.create(paused, thread_id, before_node)

    def resume(self, handle: PauseHandle) -> Phase4GraphState:
        validate_runtime_environment()
        validated_handle = PauseHandle.from_dict(handle.to_dict())
        config = self._config(validated_handle.thread_id)
        snapshot = self._graph.get_state(config)
        current = validate_graph_state(snapshot.values)
        if (
            current["case_id"] != validated_handle.case_id
            or current["request_id"] != validated_handle.request_id
            or current["contract_identity"] != validated_handle.contract_identity
            or _state_identity(current) != validated_handle.state_identity
            or validated_handle.before_node in current["completed_graph_nodes"]
        ):
            raise Phase4ContractError("resume binding is invalid")
        result = self._graph.invoke(None, config=config)
        return validate_graph_state(result, require_terminal=True)


def synthetic_commerce_b_input(
    *,
    case_id: str = "path3-commerce-checkout",
    request_id: str = "p4-02a-synthetic-request-001",
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "request_id": request_id,
        "requirement": "Create a mobile commerce page with product search, cart, delivery form, checkout, and recoverable validation errors.",
        "requirement_summary": "Build a synthetic mobile commerce flow from product search through recoverable checkout validation.",
        "target_device": "mobile",
        "task_type": "ecommerce",
        "constraints": [
            "Use only text and generic placeholders.",
            "Retain valid form input after validation failure.",
        ],
        "use_cases": [
            {"use_case_id": "UC-01", "title": "Find products", "actor": "Shopper", "goal": "Search and filter products", "expected_outcome": "Relevant products are visible"},
            {"use_case_id": "UC-02", "title": "Submit checkout", "actor": "Shopper", "goal": "Enter delivery details and submit", "expected_outcome": "Invalid fields can be corrected without losing valid input"},
        ],
    }


__all__ = [
    "CONTRACT_REVISION",
    "GRAPH_NODE_ORDER",
    "GRAPH_REVISION",
    "LANGGRAPH_VERSION",
    "NODE_ORDER",
    "PauseHandle",
    "Phase4ContractError",
    "Phase4GraphRuntime",
    "create_initial_state",
    "make_identity",
    "synthetic_commerce_b_input",
    "validate_b_input",
    "validate_dependency_acquisition_receipt",
    "validate_graph_state",
    "validate_runtime_environment",
]
