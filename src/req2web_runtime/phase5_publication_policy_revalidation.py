"""Zero-model revalidation for the Phase 5 publication F4 policy defect.

The immutable English v16 action remains unchanged.  This module validates its
returned tar, replays the three preserved F4 raw responses against the amended
per-use-case target policy, and runs only deterministic composition, assembly,
and delivery.  It never calls a model, retries generation, or relabels the
historical v16 first-pass count.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
from typing import Mapping

from req2web_agent.prompt_authority import (
    PROMPT_AUTHORITY_IDENTITY,
    PROMPT_AUTHORITY_REVISION,
)
from req2web_generation.retrieval_guidance import RetrievalGuidanceBuilder
from req2web_orchestration.phase4_graph import (
    REAL_MODEL_SOURCE_KIND,
    make_real_model_raw_capture,
    phase4_assemble_candidate,
    phase4_compose_real_model_candidate,
    phase4_create_real_model_authority_state,
    phase4_create_real_model_mapping,
    phase4_register_real_model_node_output,
    phase4_validate_node_output,
)
from req2web_runtime.phase4_browser_acceptance import (
    _agent_context_from_dict,
    validate_result_package_binding,
)
from req2web_runtime.phase4_fresh_delivery import (
    PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1,
    build_phase4_actual_context_delivery_materials,
    run_phase4_fresh_delivery,
)
from req2web_runtime.phase4_remote_qwen_fresh_integrated import (
    P4_05_F4_DIRECT_ACCEPTANCE_POLICY_RECEIPT_SCHEMA_VERSION,
    _f4_direct_acceptance_policy_receipt,
)
from req2web_runtime.phase5_result_return import validate_phase5_result_return


RUN_ID = "phase5-publication-path2-v16-full-20260809-a"
HISTORICAL_PROMPT_SHA256 = (
    "sha256:68de2486f92ccf20aa3c2b6ba3826c3f201ba2ba14e9355e1a86ae40aca4c66f"
)
FAILED_EXECUTION_INDICES = (2, 5, 9)
ROW_COUNT = 12
NODE_ORDER = ("F1", "F2", "F3", "F4")
RECEIPT_SCHEMA_VERSION = (
    "req2web.phase5.publication_f4_policy_revalidation.v1.row_receipt"
)
SUMMARY_SCHEMA_VERSION = (
    "req2web.phase5.publication_f4_policy_revalidation.v1.summary"
)


class Phase5PublicationPolicyRevalidationError(ValueError):
    """Raised when preserved publication evidence cannot be replayed safely."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase5PublicationPolicyRevalidationError(
            "revalidation value is not canonical JSON"
        ) from exc


def _identity(
    value: object,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    raw = value if type(value) is bytes else _canonical(value)
    return {
        "identity_kind": identity_kind,
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase5PublicationPolicyRevalidationError(
            f"{name} must be an object"
        )
    return copy.deepcopy(dict(value))


def _json(raw: bytes, name: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5PublicationPolicyRevalidationError(
            f"{name} is not valid UTF-8 JSON"
        ) from exc
    return _mapping(value, name)


def _write_once(path: Path, raw: bytes) -> None:
    target = Path(path).resolve(strict=False)
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise Phase5PublicationPolicyRevalidationError(
                f"write-once path drifted: {target.name}"
            )
        if target.read_bytes() != raw:
            raise Phase5PublicationPolicyRevalidationError(
                f"write-once bytes drifted: {target.name}"
            )
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


class _ValidatedArchive:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve(strict=True)
        self._archive = tarfile.open(self.path, mode="r:")
        self._members: dict[str, tarfile.TarInfo] = {}
        for member in self._archive.getmembers():
            pure = PurePosixPath(member.name)
            if (
                not member.isfile()
                or member.issym()
                or member.islnk()
                or pure.is_absolute()
                or ".." in pure.parts
                or member.name in self._members
            ):
                raise Phase5PublicationPolicyRevalidationError(
                    "result archive member is unsafe"
                )
            self._members[member.name] = member

    def close(self) -> None:
        self._archive.close()

    def read(self, name: str) -> bytes:
        member = self._members.get(name)
        if member is None:
            raise Phase5PublicationPolicyRevalidationError(
                f"required archive member is missing: {name}"
            )
        stream = self._archive.extractfile(member)
        if stream is None:
            raise Phase5PublicationPolicyRevalidationError(
                f"archive member is unreadable: {name}"
            )
        return stream.read()

    def names(self) -> tuple[str, ...]:
        return tuple(self._members)

    def copy_prefix(self, prefix: str, destination: Path) -> None:
        normalized = prefix.rstrip("/") + "/"
        names = [name for name in self._members if name.startswith(normalized)]
        if not names:
            raise Phase5PublicationPolicyRevalidationError(
                f"archive prefix is empty: {prefix}"
            )
        for name in sorted(names):
            relative = PurePosixPath(name[len(normalized) :])
            if not relative.parts:
                continue
            _write_once(
                Path(destination).joinpath(*relative.parts),
                self.read(name),
            )


def _validate_embedded_identity(
    value: Mapping[str, object],
    *,
    identity_key: str,
    revision: str,
    name: str,
) -> dict[str, object]:
    data = copy.deepcopy(dict(value))
    actual = _mapping(data.get(identity_key), f"{name} identity")
    root = {key: item for key, item in data.items() if key != identity_key}
    if actual != _identity(root, revision=revision):
        raise Phase5PublicationPolicyRevalidationError(
            f"{name} identity drifted"
        )
    return data


def _row_root(archive: _ValidatedArchive, index: int, row_id: str) -> str:
    root = f"rows/{index:02d}-{row_id}"
    if not any(name.startswith(root + "/") for name in archive.names()):
        raise Phase5PublicationPolicyRevalidationError(
            f"row {index} result root is absent"
        )
    return root


def _rehydrate_b_input(value: Mapping[str, object]) -> dict[str, object]:
    keys = (
        "case_id",
        "request_id",
        "requirement",
        "requirement_summary",
        "target_device",
        "task_type",
        "constraints",
        "use_cases",
    )
    use_case_keys = (
        "use_case_id",
        "title",
        "actor",
        "goal",
        "expected_outcome",
    )
    if set(value) != set(keys) or not isinstance(value.get("use_cases"), list):
        raise Phase5PublicationPolicyRevalidationError(
            "persisted canonical B input shape drifted"
        )
    rows: list[dict[str, object]] = []
    for item in value["use_cases"]:
        if not isinstance(item, Mapping) or set(item) != set(use_case_keys):
            raise Phase5PublicationPolicyRevalidationError(
                "persisted canonical B use-case shape drifted"
            )
        rows.append({key: copy.deepcopy(item[key]) for key in use_case_keys})
    result = {key: copy.deepcopy(value[key]) for key in keys}
    result["use_cases"] = rows
    return result


def _rehydrate_identity(
    value: Mapping[str, object],
    *,
    name: str,
) -> dict[str, object]:
    keys = ("identity_kind", "sha256", "byte_length", "revision")
    if set(value) != set(keys):
        raise Phase5PublicationPolicyRevalidationError(
            f"{name} shape drifted"
        )
    return {key: copy.deepcopy(value[key]) for key in keys}


def _validate_row_summary(
    value: Mapping[str, object],
    *,
    index: int,
) -> dict[str, object]:
    data = _validate_embedded_identity(
        value,
        identity_key="row_summary_identity",
        revision="req2web.phase5.publication_action.v1.row_summary",
        name=f"row {index} summary",
    )
    flow = _mapping(data.get("shared_flow_summary"), f"row {index} flow")
    prompt = _mapping(
        flow.get("prompt_authority_identity"),
        f"row {index} historical prompt identity",
    )
    calls = _mapping(
        flow.get("per_node_generate_started"),
        f"row {index} generate accounting",
    )
    if (
        data.get("execution_index") != index
        or data.get("schema_version")
        != "req2web.phase5.publication_action.v1.row_summary"
        or not isinstance(data.get("row_id"), str)
        or tuple(calls) != NODE_ORDER
        or any(calls[node] != 1 for node in NODE_ORDER)
        or prompt.get("sha256") != HISTORICAL_PROMPT_SHA256
    ):
        raise Phase5PublicationPolicyRevalidationError(
            f"row {index} summary scope drifted"
        )
    return data


def _context_and_guidance(
    archive: _ValidatedArchive,
    row_root: str,
) -> tuple[object, object]:
    context_value = _json(
        archive.read(
            row_root
            + "/graph-bound-delivery-materials/g0-package-v2/"
            "internal/agent_context.json"
        ),
        "AgentContext",
    )
    guidance_value = _json(
        archive.read(
            row_root
            + "/graph-bound-delivery-materials/g0-package-v2/"
            "internal/retrieval_guidance.json"
        ),
        "RetrievalGuidance",
    )
    context = _agent_context_from_dict(context_value)
    guidance = RetrievalGuidanceBuilder().build(context)
    if _canonical(guidance.to_dict()) != _canonical(guidance_value):
        raise Phase5PublicationPolicyRevalidationError(
            "stored RetrievalGuidance differs from live deterministic rebuild"
        )
    return context, guidance


def _copy_historical_package(
    archive: _ValidatedArchive,
    *,
    row_root: str,
    destination: Path,
) -> dict[str, object]:
    archive.copy_prefix(
        row_root + "/delivery/result_package_v1",
        destination,
    )
    return validate_result_package_binding(destination)


def _revalidate_failed_row(
    archive: _ValidatedArchive,
    *,
    index: int,
    row: Mapping[str, object],
    row_root: str,
    output_root: Path,
) -> tuple[dict[str, object], Path]:
    flow = _mapping(row["shared_flow_summary"], f"row {index} flow")
    per_node = _mapping(
        flow.get("per_node_raw_contract_pass"),
        f"row {index} historical raw accounting",
    )
    failure = _mapping(flow.get("failure"), f"row {index} failure")
    if (
        index not in FAILED_EXECUTION_INDICES
        or flow.get("status") != "failed_closed"
        or flow.get("downstream_first_pass_success") is not False
        or tuple(per_node) != NODE_ORDER
        or any(per_node[node] is not True for node in NODE_ORDER[:3])
        or per_node["F4"] is not False
        or failure.get("failure_stage") != "F4"
        or failure.get("message_code") != "Phase4RemoteFreshIntegratedError"
    ):
        raise Phase5PublicationPolicyRevalidationError(
            f"row {index} is not the recognized v16 F4 policy-failure shape"
        )

    input_raw = archive.read(row_root + "/attempts/F4/input.json")
    response_raw = archive.read(row_root + "/attempts/F4/raw_response.bin")
    output = _json(response_raw, f"row {index} F4 raw response")
    graph = _json(
        archive.read(row_root + "/langgraph_final_state.json"),
        f"row {index} LangGraph state",
    )
    mapping = _json(
        archive.read(row_root + "/mapping.json"),
        f"row {index} mapping",
    )

    executions = _mapping(
        graph.get("node_execution_records"),
        f"row {index} node executions",
    )
    persisted_authority = _mapping(
        graph.get("authority_state"),
        f"row {index} persisted authority state",
    )
    b_input = _rehydrate_b_input(
        _mapping(
            persisted_authority.get("b_input"),
            f"row {index} canonical B input",
        )
    )
    authority_state = phase4_create_real_model_authority_state(b_input)
    for node_id in NODE_ORDER[:3]:
        prior_raw = archive.read(
            row_root + f"/attempts/{node_id}/raw_response.bin"
        )
        prior_output = _json(
            prior_raw,
            f"row {index} {node_id} raw response",
        )
        prior_execution = _mapping(
            executions.get(node_id),
            f"row {index} historical {node_id} execution",
        )
        prior_attempt_identity = _rehydrate_identity(
            _mapping(
                prior_execution.get("attempt_identity"),
                f"row {index} {node_id} attempt identity",
            ),
            name=f"row {index} {node_id} attempt identity",
        )
        phase4_validate_node_output(node_id, prior_output, authority_state)
        authority_state = phase4_register_real_model_node_output(
            authority_state,
            node_id,
            prior_output,
            raw_capture=make_real_model_raw_capture(prior_raw),
            execution_binding={
                "source_kind": REAL_MODEL_SOURCE_KIND,
                "node_id": node_id,
                "generate_call_count": 1,
                "attempt_identity": prior_attempt_identity,
            },
        )
    if _canonical(phase4_create_real_model_mapping(authority_state)) != _canonical(
        mapping
    ):
        raise Phase5PublicationPolicyRevalidationError(
            f"row {index} pre-F4 mapping changed during replay"
        )

    phase4_validate_node_output("F4", output, authority_state)
    policy_receipt = _f4_direct_acceptance_policy_receipt(
        input_bytes=input_raw,
        output=output,
        raw_bytes=response_raw,
    )
    f4_execution = _mapping(
        executions.get("F4"),
        f"row {index} historical F4 execution",
    )
    attempt_identity = _rehydrate_identity(
        _mapping(
            f4_execution.get("attempt_identity"),
            f"row {index} F4 attempt identity",
        ),
        name=f"row {index} F4 attempt identity",
    )
    amended_state = phase4_register_real_model_node_output(
        authority_state,
        "F4",
        output,
        raw_capture=make_real_model_raw_capture(response_raw),
        execution_binding={
            "source_kind": REAL_MODEL_SOURCE_KIND,
            "node_id": "F4",
            "generate_call_count": 1,
            "attempt_identity": attempt_identity,
        },
    )
    if _canonical(phase4_create_real_model_mapping(amended_state)) != _canonical(
        mapping
    ):
        raise Phase5PublicationPolicyRevalidationError(
            f"row {index} mapping changed during revalidation"
        )
    composition = phase4_compose_real_model_candidate(amended_state)
    encoded = composition.get("model_semantic_candidate_canonical_b64")
    if not isinstance(encoded, str):
        raise Phase5PublicationPolicyRevalidationError(
            f"row {index} candidate bytes are absent"
        )
    candidate_raw = base64.b64decode(encoded, validate=True)
    context, guidance = _context_and_guidance(archive, row_root)
    assembled = phase4_assemble_candidate(candidate_raw, context, guidance)
    assembled.validate()

    row_output = Path(output_root) / "revalidated" / f"{index:02d}-{row['row_id']}"
    source_root = row_output / "source"
    marker = b"req2web.phase5.publication_f4_policy_revalidation.v1\n"
    _write_once(source_root / ".req2web-phase5-f4-policy-revalidation-root", marker)
    _write_once(
        source_root / "candidate_composition_record.json",
        _canonical(composition),
    )
    _write_once(
        source_root / "assembled_page_spec.json",
        _canonical(assembled.page_spec.to_dict()),
    )
    _write_once(
        source_root / "assembly_report.json",
        _canonical(assembled.report.to_dict()),
    )
    source_result = {
        "schema_version": (
            "req2web.phase5.publication_f4_policy_revalidation.v1.source_result"
        ),
        "run_id": RUN_ID,
        "case_id": flow["case_id"],
        "request_id": flow["request_id"],
        "source_kind": REAL_MODEL_SOURCE_KIND,
        "status": "assembled_after_policy_revalidation",
        "raw_model_contract_success": True,
        "normalized_node_contract_success": True,
        "agent_chain_system_output_usable": True,
        "composition_status": "composed",
        "assembler_status": "assembled",
        "historical_v16_first_pass_success": False,
        "historical_row_summary_identity": copy.deepcopy(
            row["row_summary_identity"]
        ),
        "amended_prompt_authority_identity": copy.deepcopy(
            PROMPT_AUTHORITY_IDENTITY
        ),
        "f4_policy_receipt_identity": _identity(
            policy_receipt,
            revision=P4_05_F4_DIRECT_ACCEPTANCE_POLICY_RECEIPT_SCHEMA_VERSION,
        ),
        "model_generate_calls_added": 0,
        "retry_count_added": 0,
        "normalization_count_added": 0,
        "repair_count_added": 0,
        "fallback_count_added": 0,
    }
    _write_once(
        source_root / "revalidation_result.json",
        _canonical(source_result),
    )
    _write_once(
        source_root / "f4_policy_revalidation_receipt.json",
        _canonical(policy_receipt),
    )

    materials = build_phase4_actual_context_delivery_materials(
        graph_state=amended_state,
        context=context,
        guidance=guidance,
        material_root=row_output / "graph-bound-delivery-materials",
    )
    materials.validate()
    delivery = run_phase4_fresh_delivery(
        source_root=source_root,
        delivery_root=row_output / "delivery",
        context=context,
        guidance=guidance,
        manifest=materials.live["manifest"],
        selected=materials.live["selected"],
        local_request=materials.live["local_request"],
        pre_invocation_audit=materials.live["pre_invocation_audit"],
        local_qwen_preparation=materials.live["local_qwen_preparation"],
        package=materials.live["package"],
        frozen_g0_reference=materials.live["frozen_g0_reference"],
        fallback_record=materials.live["fallback_record"],
        fallback_snapshot_dir=materials.live["fallback_snapshot_dir"],
        scripted_acceptance_fixture=materials.live[
            "scripted_acceptance_fixture"
        ],
        delivery_policy=PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1,
    )
    delivery.validate()
    if delivery.success_accounting["delivery_success"] is not True:
        raise Phase5PublicationPolicyRevalidationError(
            f"row {index} deterministic delivery did not pass"
        )
    package_root = row_output / "delivery" / "result_package_v1"
    package_binding = validate_result_package_binding(package_root)

    root = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "run_id": RUN_ID,
        "execution_index": index,
        "row_id": row["row_id"],
        "case_id": flow["case_id"],
        "condition_id": row["condition_id"],
        "historical_row_summary_identity": copy.deepcopy(
            row["row_summary_identity"]
        ),
        "historical_v16_status": "failed_closed",
        "historical_v16_downstream_first_pass_success": False,
        "historical_v16_raw_contract_pass_f4": False,
        "preserved_f4_input_identity": _identity(
            input_raw,
            revision="req2web.phase5.publication_f4_policy_revalidation.v1.input",
            identity_kind="raw_bytes",
        ),
        "preserved_f4_raw_identity": _identity(
            response_raw,
            revision="req2web.phase5.publication_f4_policy_revalidation.v1.raw",
            identity_kind="raw_bytes",
        ),
        "amended_prompt_authority_identity": copy.deepcopy(
            PROMPT_AUTHORITY_IDENTITY
        ),
        "f4_policy_receipt_identity": _identity(
            policy_receipt,
            revision=P4_05_F4_DIRECT_ACCEPTANCE_POLICY_RECEIPT_SCHEMA_VERSION,
        ),
        "amended_policy_raw_bytes_accepted": True,
        "deterministic_delivery_success": True,
        "result_package": package_binding,
        "model_generate_calls_added": 0,
        "automatic_retry_count_added": 0,
        "normalization_count_added": 0,
        "repair_count_added": 0,
        "fallback_count_added": 0,
        "historical_artifact_overwritten": False,
        "claim_boundary": (
            "zero-model policy and downstream revalidation only; the immutable "
            "English v16 row remains failed_closed and the original 9/12 "
            "first-pass count is not changed"
        ),
    }
    receipt = {
        **root,
        "receipt_identity": _identity(root, revision=RECEIPT_SCHEMA_VERSION),
    }
    _write_once(
        Path(output_root) / "row_receipts" / f"{index:02d}.json",
        _canonical(receipt),
    )
    return receipt, package_root


def run_phase5_publication_policy_revalidation(
    *,
    result_tar_path: Path,
    result_manifest_path: Path,
    output_root: Path,
    confirm_zero_model_revalidation: bool,
) -> dict[str, object]:
    """Replay preserved v16 evidence and create a separate amended summary."""

    if confirm_zero_model_revalidation is not True:
        raise Phase5PublicationPolicyRevalidationError(
            "explicit zero-model revalidation confirmation is required"
        )
    if PROMPT_AUTHORITY_REVISION != (
        "f3_f4_explicit_actual_state_plan_a07a_direct_english_v17"
    ):
        raise Phase5PublicationPolicyRevalidationError(
            "shared prompt authority is not the accepted English v17 revision"
        )
    manifest = validate_phase5_result_return(
        tar_path=Path(result_tar_path),
        manifest_path=Path(result_manifest_path),
    )
    manifest_value = manifest.to_dict()
    destination = Path(output_root).resolve(strict=False)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir() or any(
            destination.iterdir()
        ):
            raise Phase5PublicationPolicyRevalidationError(
                "output root must be new or empty"
            )
    else:
        destination.mkdir(parents=True, exist_ok=False)

    archive = _ValidatedArchive(Path(result_tar_path))
    try:
        run_summary = _json(archive.read("run_summary.json"), "run summary")
        historical_aggregate = _mapping(
            run_summary.get("aggregate"), "historical run aggregate"
        )
        if (
            run_summary.get("run_id") != RUN_ID
            or run_summary.get("planned_runtime_row_count") != ROW_COUNT
            or run_summary.get("completed_runtime_row_count") != ROW_COUNT
            or historical_aggregate.get("completed_case_count") != ROW_COUNT
            or historical_aggregate.get(
                "downstream_first_pass_success_count"
            )
            != 9
            or historical_aggregate.get("failed_closed_count") != 3
            or run_summary.get("status") != "completed_descriptive_results"
        ):
            raise Phase5PublicationPolicyRevalidationError(
                "historical run summary scope drifted"
            )

        rows: list[dict[str, object]] = []
        for index in range(1, ROW_COUNT + 1):
            row = _validate_row_summary(
                _json(
                    archive.read(f"row-summaries/{index:02d}.json"),
                    f"row {index} summary",
                ),
                index=index,
            )
            row_root = _row_root(archive, index, str(row["row_id"]))
            package_destination = (
                destination / "packages" / f"{index:02d}-{row['row_id']}"
            )
            if index in FAILED_EXECUTION_INDICES:
                receipt, revalidated_package = _revalidate_failed_row(
                    archive,
                    index=index,
                    row=row,
                    row_root=row_root,
                    output_root=destination,
                )
                package_binding = _copy_package_tree(
                    revalidated_package,
                    package_destination,
                )
                disposition = "validated_after_f4_policy_revalidation"
                receipt_identity = copy.deepcopy(receipt["receipt_identity"])
                historical_first_pass = False
            else:
                flow = _mapping(
                    row["shared_flow_summary"], f"row {index} flow"
                )
                if (
                    flow.get("downstream_first_pass_success") is not True
                    or flow.get("status") != "delivery_terminal_success"
                ):
                    raise Phase5PublicationPolicyRevalidationError(
                        f"row {index} is not a historical first-pass row"
                    )
                package_binding = _copy_historical_package(
                    archive,
                    row_root=row_root,
                    destination=package_destination,
                )
                disposition = "historical_direct_first_pass_success"
                receipt_identity = None
                historical_first_pass = True
            rows.append(
                {
                    "execution_index": index,
                    "row_id": row["row_id"],
                    "case_id": row["case_ref"],
                    "condition_id": row["condition_id"],
                    "historical_row_summary_identity": copy.deepcopy(
                        row["row_summary_identity"]
                    ),
                    "historical_downstream_first_pass_success": (
                        historical_first_pass
                    ),
                    "delivery_evidence_available": True,
                    "result_package": package_binding,
                    "disposition": disposition,
                    "revalidation_receipt_identity": receipt_identity,
                }
            )
    finally:
        archive.close()

    root = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "run_id": RUN_ID,
        "historical_result_return": {
            "manifest_identity": _identity(
                manifest_value,
                revision="req2web.phase5.result_return.v3",
            ),
            "tar_sha256": "sha256:" + str(manifest_value["tar_sha256"]),
            "tar_byte_length": manifest_value["tar_byte_length"],
        },
        "historical_prompt_authority_sha256": HISTORICAL_PROMPT_SHA256,
        "amended_prompt_authority_identity": copy.deepcopy(
            PROMPT_AUTHORITY_IDENTITY
        ),
        "row_results": rows,
        "counts": {
            "row_count": ROW_COUNT,
            "historical_downstream_first_pass_success_count": 9,
            "f4_policy_revalidated_row_count": len(FAILED_EXECUTION_INDICES),
            "delivery_evidence_available_count": len(rows),
            "model_generate_calls_added": 0,
            "automatic_retry_count_added": 0,
            "normalization_count_added": 0,
            "repair_count_added": 0,
            "fallback_count_added": 0,
            "real_browser_executed_count": 0,
        },
        "status": "zero_model_policy_revalidation_complete_pending_browser",
        "historical_result_overwritten": False,
        "claim_boundary": (
            "the immutable English v16 experiment remains 9/12 downstream "
            "first-pass; three preserved raw F4 answers gained separate "
            "amended-policy deterministic delivery evidence with no model call"
        ),
    }
    summary = {
        **root,
        "summary_identity": _identity(root, revision=SUMMARY_SCHEMA_VERSION),
    }
    _write_once(destination / "amended_summary.json", _canonical(summary))
    return summary


def _copy_package_tree(source: Path, destination: Path) -> dict[str, object]:
    source_root = Path(source).resolve(strict=True)
    if source_root.is_symlink() or not source_root.is_dir():
        raise Phase5PublicationPolicyRevalidationError(
            "revalidated package root is invalid"
        )
    for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink() or not path.is_file():
            if path.is_dir() and not path.is_symlink():
                continue
            raise Phase5PublicationPolicyRevalidationError(
                "revalidated package tree contains a non-regular entry"
            )
        _write_once(
            Path(destination) / path.relative_to(source_root),
            path.read_bytes(),
        )
    return validate_result_package_binding(destination)


__all__ = [
    "FAILED_EXECUTION_INDICES",
    "Phase5PublicationPolicyRevalidationError",
    "run_phase5_publication_policy_revalidation",
]
