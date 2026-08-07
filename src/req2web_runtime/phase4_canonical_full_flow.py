"""Canonical ten-case Phase 4 flow from raw requirements to result packages."""

from __future__ import annotations

import copy
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Mapping
import uuid

from req2web_agent import (
    DeterministicRequirementProvider,
    MinimalAgentChain,
    PROMPT_AUTHORITY_IDENTITY,
)
from req2web_generation import RetrievalGuidanceBuilder
from req2web_orchestration.phase4_canonical_b_adapter import (
    ADAPTER_REVISION,
    adapt_agent_context_to_phase4_canonical_b,
)
from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    REAL_MODEL_GRAPH_REVISION,
)
from req2web_rag import RetrieverConfig, create_retriever
from req2web_runtime import phase4_remote_qwen_fresh_integrated as _fresh
from req2web_runtime.phase4_browser_acceptance import (
    validate_browser_canary_receipt,
)
from req2web_runtime.phase4_canonical_full_flow_cases import (
    CANARY_COUNT,
    CASE_COUNT,
    CASE_SET_ID,
    get_case_set,
    validate_supported_requirement_scope,
)
from req2web_runtime.phase4_remote_qwen_langgraph_integrated import (
    ACTIVE_RUNNER_REVISION,
    run_phase4_remote_qwen_langgraph_integrated,
)


FLOW_SCHEMA_VERSION = "req2web.phase4.canonical_full_flow.v1"
FLOW_AUTHORITY_ROLE = "active_canonical_full_flow"
ACTIVE_DEFAULT_ENTRY = True
POLICY_SCHEMA_VERSION = f"{FLOW_SCHEMA_VERSION}.policy"
SUMMARY_SCHEMA_VERSION = f"{FLOW_SCHEMA_VERSION}.summary"
CASE_SUMMARY_SCHEMA_VERSION = f"{FLOW_SCHEMA_VERSION}.case_summary"
UPSTREAM_RECEIPT_SCHEMA_VERSION = f"{FLOW_SCHEMA_VERSION}.upstream_receipt"
RUN_PREFIX = "p4-canonical-full-flow-"
ROOT_MARKER = ".req2web-phase4-canonical-full-flow-root"
TOTAL_GENERATE_STARTED_CAP = CASE_COUNT * len(NODE_ORDER)
TOP_K_PER_ROLE = 2


class Phase4CanonicalFullFlowError(ValueError):
    """Raised when the canonical full flow cannot continue safely."""


class _RecordingRequirementProvider:
    def __init__(self) -> None:
        self._provider = DeterministicRequirementProvider()
        self.calls: list[dict[str, object]] = []

    def understand(
        self,
        original_requirement: str,
        *,
        target_device: str | None = None,
        task_type: str | None = None,
        constraints: Iterable[str] = (),
    ):
        if self.calls:
            raise Phase4CanonicalFullFlowError(
                "requirement provider was called more than once"
            )
        constraint_list = list(constraints)
        result = self._provider.understand(
            original_requirement,
            target_device=target_device,
            task_type=task_type,
            constraints=constraint_list,
        )
        self.calls.append(
            {
                "original_requirement": original_requirement,
                "target_device": target_device,
                "task_type": task_type,
                "constraints": constraint_list,
                "result": asdict(result),
            }
        )
        return result


class _RecordingRetriever:
    def __init__(self, delegate: object) -> None:
        self._delegate = delegate
        self.calls: list[dict[str, object]] = []

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[dict[str, object]]:
        role_list = None if roles is None else list(roles)
        result = self._delegate.search(  # type: ignore[attr-defined]
            query,
            top_k=top_k,
            roles=role_list,
        )
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "roles": role_list,
                "results": copy.deepcopy(result),
            }
        )
        return result

    def search_by_role(
        self,
        query: str,
        top_k: int = 2,
    ) -> dict[str, list[dict[str, object]]]:
        return self._delegate.search_by_role(  # type: ignore[attr-defined]
            query,
            top_k=top_k,
        )


def _require_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4CanonicalFullFlowError(f"{name} must be an object")
    return copy.deepcopy(dict(value))


def _write_json(path: Path, value: object) -> None:
    _fresh._write_fsync(path, _fresh._canonical_bytes(value))


def _progress_checkpoint_path(result_root: Path, index: int) -> Path:
    if index < 1 or index > CASE_COUNT:
        raise Phase4CanonicalFullFlowError("progress checkpoint index is invalid")
    return result_root / "progress" / f"{index:02d}.json"


def _read_json(path: Path) -> object:
    return _fresh._strict_json(path.read_bytes(), str(path))


def _parent_experiment_binding(
    *,
    experiment_run_id: str,
    experiment_policy_identity: Mapping[str, object],
    index: int,
    case: Mapping[str, object],
) -> dict[str, object]:
    """Build the child binding without importing a historical stability runner."""

    return {
        "schema_version": _fresh.P4_05_PARENT_BINDING_SCHEMA_VERSION,
        "experiment_id": CASE_SET_ID,
        "experiment_run_id": experiment_run_id,
        "experiment_policy_identity": copy.deepcopy(
            dict(experiment_policy_identity)
        ),
        "case_index": index,
        "case_id": case["case_id"],
        "request_id": case["request_id"],
    }


def _build_upstream(
    *,
    case: Mapping[str, object],
    index_dir: Path,
    output_root: Path,
) -> dict[str, object]:
    scope_receipt = validate_supported_requirement_scope(case["requirement"])
    _write_json(output_root / "scope_receipt.json", scope_receipt)
    if scope_receipt["supported"] is not True:
        raise Phase4CanonicalFullFlowError(
            "case is outside the supported research scope"
        )
    provider = _RecordingRequirementProvider()
    retriever = _RecordingRetriever(
        create_retriever(
            RetrieverConfig(
                index_dir=index_dir,
                backend="tfidf",
            )
        )
    )
    context = MinimalAgentChain(
        provider,
        retriever,
        top_k_per_role=TOP_K_PER_ROLE,
    ).run(
        str(case["requirement"]),
        target_device=str(case["target_device"]),
        task_type=str(case["task_type"]),
        constraints=copy.deepcopy(case["constraints"]),
    )
    guidance = RetrievalGuidanceBuilder().build(context)
    adaptation = adapt_agent_context_to_phase4_canonical_b(
        context=context,
        case_id=str(case["case_id"]),
        request_id=str(case["request_id"]),
        explicit_target_device=str(case["target_device"]),
        explicit_task_type=str(case["task_type"]),
        explicit_user_constraints=copy.deepcopy(case["constraints"]),
    )
    if len(provider.calls) != 1 or len(retriever.calls) != 5:
        raise Phase4CanonicalFullFlowError(
            "formal upstream call accounting drifted"
        )
    requirement_understanding = provider.calls[0]["result"]
    _write_json(
        output_root / "raw_input.json",
        copy.deepcopy(dict(case)),
    )
    _write_json(
        output_root / "requirement_understanding.json",
        requirement_understanding,
    )
    _write_json(
        output_root / "retrieval_calls.json",
        retriever.calls,
    )
    _write_json(
        output_root / "agent_context.json",
        context.to_dict(),
    )
    _write_json(
        output_root / "retrieval_guidance.json",
        guidance.to_dict(),
    )
    _write_json(
        output_root / "canonical_b.json",
        adaptation.b_input,
    )
    _write_json(
        output_root / "canonical_b_adapter_receipt.json",
        adaptation.receipt,
    )
    receipt_root = {
        "schema_version": UPSTREAM_RECEIPT_SCHEMA_VERSION,
        "case_id": case["case_id"],
        "request_id": case["request_id"],
        "scope_receipt_identity": _fresh._identity(
            scope_receipt,
            revision="req2web.phase4.requirement_scope_check.v1",
        ),
        "requirement_provider": "DeterministicRequirementProvider",
        "requirement_provider_call_count": 1,
        "retriever_backend": "tfidf",
        "retriever_index_root": str(index_dir),
        "retriever_call_count": len(retriever.calls),
        "top_k_per_role": TOP_K_PER_ROLE,
        "agent_context_identity": _fresh._identity(
            context.to_dict(),
            revision="req2web.agent.context.v1",
        ),
        "retrieval_guidance_identity": _fresh._identity(
            guidance.to_dict(),
            revision="req2web.retrieval.guidance.v1",
        ),
        "canonical_b_identity": _fresh._identity(
            adaptation.b_input,
            revision="canonical_b.p4.v1",
        ),
        "canonical_b_adapter_revision": ADAPTER_REVISION,
        "canonical_b_adapter_receipt_identity": _fresh._identity(
            adaptation.receipt,
            revision=(
                "req2web.phase4.agent_context_to_canonical_b.v1"
            ),
        ),
        "prewritten_upstream_fields_consumed": False,
        "same_context_guidance_used_for_downstream": True,
        "synthetic_downstream_context_rebuilt": False,
    }
    receipt = {
        **receipt_root,
        "receipt_identity": _fresh._identity(
            receipt_root,
            revision=UPSTREAM_RECEIPT_SCHEMA_VERSION,
        ),
    }
    _write_json(output_root / "upstream_receipt.json", receipt)
    return {
        "context": context,
        "guidance": guidance,
        "adaptation": adaptation,
        "receipt": receipt,
    }


def _summarize_case(
    *,
    index: int,
    case: Mapping[str, object],
    upstream_receipt: Mapping[str, object],
    child_root: Path,
    final_result: Mapping[str, object],
) -> dict[str, object]:
    graph_state = _require_mapping(
        _read_json(child_root / "langgraph_final_state.json"),
        "LangGraph final state",
    )
    execution_records = _require_mapping(
        graph_state["node_execution_records"],
        "node execution records",
    )
    per_node_generate_started = {
        node_id: int(
            _require_mapping(
                execution_records[node_id],
                f"{node_id} execution",
            )["generate_call_count"]
        )
        for node_id in execution_records
    }
    per_node_raw_contract_pass = {
        node_id: bool(
            _require_mapping(
                execution_records[node_id],
                f"{node_id} execution",
            )["raw_model_contract_success"]
        )
        for node_id in execution_records
    }
    delivery_receipt = final_result.get("delivery_receipt")
    delivery_status = None
    delivery_success = False
    repair_success = False
    fallback_success = False
    if isinstance(delivery_receipt, Mapping):
        downstream = delivery_receipt.get("downstream")
        success_accounting = delivery_receipt.get("success_accounting")
        if isinstance(downstream, Mapping):
            delivery_status = downstream.get("status")
        if isinstance(success_accounting, Mapping):
            delivery_success = (
                success_accounting.get("delivery_success") is True
            )
            repair_success = (
                success_accounting.get("repair_success") is True
            )
            fallback_success = (
                success_accounting.get("fallback_success") is True
            )
    root = {
        "schema_version": CASE_SUMMARY_SCHEMA_VERSION,
        "case_index": index,
        "case_id": case["case_id"],
        "request_id": case["request_id"],
        "child_result_root": str(child_root),
        "upstream_receipt_identity": copy.deepcopy(
            upstream_receipt["receipt_identity"]
        ),
        "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
        "prompt_authority_identity": copy.deepcopy(
            PROMPT_AUTHORITY_IDENTITY
        ),
        "status": final_result["status"],
        "per_node_generate_started": per_node_generate_started,
        "per_node_raw_contract_pass": per_node_raw_contract_pass,
        "all_four_nodes_executed": (
            tuple(execution_records) == NODE_ORDER
        ),
        "all_four_nodes_raw_contract_pass": (
            tuple(per_node_raw_contract_pass) == NODE_ORDER
            and all(per_node_raw_contract_pass.values())
        ),
        "downstream_status": delivery_status,
        "downstream_delivery_success": delivery_success,
        "downstream_first_pass_success": (
            delivery_status == "first_pass_success"
        ),
        "downstream_repair_success": repair_success,
        "downstream_fallback_success": fallback_success,
        "scripted_acceptance_executed": delivery_receipt is not None,
        "real_browser_executed": False,
        "manual_f1_f4_loop_used": False,
        "failure": final_result.get("failure"),
    }
    return {
        **root,
        "case_summary_identity": _fresh._identity(
            root,
            revision=CASE_SUMMARY_SCHEMA_VERSION,
        ),
    }


def _aggregate(case_summaries: list[dict[str, object]]) -> dict[str, object]:
    per_node_calls = {node_id: 0 for node_id in NODE_ORDER}
    per_node_raw_pass = {node_id: 0 for node_id in NODE_ORDER}
    for summary in case_summaries:
        for node_id, count in summary[
            "per_node_generate_started"
        ].items():
            per_node_calls[node_id] += int(count)
        for node_id, passed in summary[
            "per_node_raw_contract_pass"
        ].items():
            per_node_raw_pass[node_id] += int(passed is True)
    return {
        "completed_case_count": len(case_summaries),
        "per_node_generate_started_count": per_node_calls,
        "per_node_raw_contract_pass_count": per_node_raw_pass,
        "total_generate_started_count": sum(per_node_calls.values()),
        "downstream_first_pass_success_count": sum(
            summary["downstream_first_pass_success"] is True
            for summary in case_summaries
        ),
        "downstream_repair_success_count": sum(
            summary["downstream_repair_success"] is True
            for summary in case_summaries
        ),
        "downstream_fallback_success_count": sum(
            summary["downstream_fallback_success"] is True
            for summary in case_summaries
        ),
        "downstream_delivery_success_count": sum(
            summary["downstream_delivery_success"] is True
            for summary in case_summaries
        ),
        "failed_closed_count": sum(
            summary["status"] == "failed_closed"
            for summary in case_summaries
        ),
        "real_browser_executed_count": 0,
    }


def run_phase4_canonical_full_flow(
    *,
    model_root: Path,
    integrity_evidence: Path,
    index_dir: Path,
    result_root: Path,
    confirm_canonical_full_flow: bool,
    max_cases: int = CANARY_COUNT,
    resume_existing: bool = False,
    browser_canary_receipt: Path | None = None,
    run_id: str | None = None,
    console: object | None = None,
) -> dict[str, object]:
    """Run three canaries or resume the same run to all ten cases."""

    if confirm_canonical_full_flow is not True:
        raise Phase4CanonicalFullFlowError(
            "explicit canonical full-flow confirmation is required"
        )
    if max_cases not in {CANARY_COUNT, CASE_COUNT}:
        raise Phase4CanonicalFullFlowError(
            "max_cases must be the canary count or all ten cases"
        )
    model_root = model_root.resolve(strict=True)
    integrity_evidence = integrity_evidence.resolve(strict=True)
    index_dir = index_dir.resolve(strict=True)
    result_root = result_root.resolve(strict=False)
    case_set = get_case_set()
    existing_policy = (
        _read_json(result_root / "flow_policy.json")
        if result_root.exists()
        else None
    )
    if existing_policy is None:
        if result_root.exists() and any(result_root.iterdir()):
            raise Phase4CanonicalFullFlowError(
                "result root must be new or already owned"
            )
        if resume_existing:
            raise Phase4CanonicalFullFlowError(
                "resume requires an existing owned result root"
            )
        result_root.mkdir(parents=True, exist_ok=False)
        _fresh._write_fsync(
            result_root / ROOT_MARKER,
            ROOT_MARKER.encode("ascii"),
        )
        selected_run_id = run_id or f"{RUN_PREFIX}{uuid.uuid4().hex[:16]}"
        inventory = _fresh._remote.validate_remote_model_inventory(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
        )
        runtime_facts = _fresh._remote._collect_remote_runtime_facts()
        gpu_facts = _fresh._remote._probe_remote_gpu_facts()
        profile = _fresh.RemoteFreshIntegratedProfile.create(
            inventory=inventory,
            runtime_facts=runtime_facts,
            gpu_facts=gpu_facts,
        )
        profile_identity = _fresh.make_stable_profile_binding_identity(profile)
        inventory_identity = _require_mapping(
            inventory["inventory_identity"],
            "model inventory identity",
        )
        policy_root = {
            "schema_version": POLICY_SCHEMA_VERSION,
            "run_id": selected_run_id,
            "case_set_id": CASE_SET_ID,
            "case_set_identity": case_set["case_set_identity"],
            "case_count": CASE_COUNT,
            "canary_count": CANARY_COUNT,
            "node_order": list(NODE_ORDER),
            "total_generate_started_cap": TOTAL_GENERATE_STARTED_CAP,
            "prompt_authority_identity": PROMPT_AUTHORITY_IDENTITY,
            "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
            "active_runner_revision": ACTIVE_RUNNER_REVISION,
            "profile_identity": profile_identity,
            "model_inventory_identity": inventory_identity,
            "one_call_per_node_per_case": True,
            "automatic_retry": False,
            "dynamic_agent_routing": False,
            "scripted_acceptance_is_browser_evidence": False,
            "browser_canary_required_before_cases_4_to_10": True,
            "formal_h1": False,
            "training": False,
            "lora": False,
        }
        policy = {
            **policy_root,
            "policy_identity": _fresh._identity(
                policy_root,
                revision=POLICY_SCHEMA_VERSION,
            ),
        }
        _write_json(result_root / "flow_policy.json", policy)
        _write_json(result_root / "case_set.json", case_set)
        _write_json(result_root / "profile.json", profile.to_dict())
        _write_json(result_root / "model_inventory.json", inventory)
    else:
        if not resume_existing:
            raise Phase4CanonicalFullFlowError(
                "existing result root requires resume_existing"
            )
        policy = _require_mapping(existing_policy, "flow policy")
        selected_run_id = str(policy["run_id"])
        if run_id is not None and run_id != selected_run_id:
            raise Phase4CanonicalFullFlowError("run identity drifted")
        saved_case_set = _read_json(result_root / "case_set.json")
        if (
            _fresh._canonical_bytes(saved_case_set)
            != _fresh._canonical_bytes(case_set)
        ):
            raise Phase4CanonicalFullFlowError("saved case set drifted")
        profile_identity = _require_mapping(
            policy["profile_identity"],
            "profile identity",
        )
        inventory_identity = _require_mapping(
            policy["model_inventory_identity"],
            "model inventory identity",
        )

    completed: list[dict[str, object]] = []
    for index in range(1, CASE_COUNT + 1):
        summary_path = (
            result_root / "case-summaries" / f"{index:02d}.json"
        )
        if summary_path.is_file():
            completed.append(
                _require_mapping(
                    _read_json(summary_path),
                    "saved case summary",
                )
            )
            continue
        break
    if max_cases == CASE_COUNT:
        if len(completed) < CANARY_COUNT:
            raise Phase4CanonicalFullFlowError(
                "complete run requires the three model canaries first"
            )
        if browser_canary_receipt is None:
            raise Phase4CanonicalFullFlowError(
                "browser canary receipt is required before cases 4 to 10"
            )
        browser_receipt = validate_browser_canary_receipt(
            flow_result_root=result_root,
            receipt=_require_mapping(
            _read_json(browser_canary_receipt.resolve(strict=True)),
            "browser canary receipt",
            ),
        )
        _write_json(
            result_root / "browser_canary_receipt.json",
            browser_receipt,
        )

    cases = case_set["cases"]
    for index, case_value in enumerate(cases, start=1):
        if index <= len(completed) or index > max_cases:
            continue
        case = _require_mapping(case_value, "raw case")
        if console is not None:
            print(
                f"[P4-CANONICAL-FLOW] case {index:02d}/{max_cases} "
                f"{case['case_id']} upstream started",
                file=console,
                flush=True,
            )
        upstream_root = result_root / "upstream" / f"{index:02d}"
        upstream_root.mkdir(parents=True, exist_ok=False)
        upstream = _build_upstream(
            case=case,
            index_dir=index_dir,
            output_root=upstream_root,
        )
        child_root = result_root / "cases" / f"{index:02d}-{case['case_id']}"
        parent_binding = _parent_experiment_binding(
            experiment_run_id=selected_run_id,
            experiment_policy_identity=policy["policy_identity"],
            index=index,
            case=case,
        )
        final_result = run_phase4_remote_qwen_langgraph_integrated(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
            result_root=child_root,
            b_input=upstream["adaptation"].b_input,
            upstream_context=upstream["context"],
            upstream_guidance=upstream["guidance"],
            upstream_binding=upstream["receipt"],
            parent_experiment_binding=parent_binding,
            expected_profile_identity=profile_identity,
            expected_model_inventory_identity=inventory_identity,
            confirm_one_remote_langgraph_run=True,
            run_id=f"{selected_run_id}-case-{index:02d}",
            console=console,
        )
        summary = _summarize_case(
            index=index,
            case=case,
            upstream_receipt=upstream["receipt"],
            child_root=child_root,
            final_result=final_result,
        )
        _write_json(
            result_root / "case-summaries" / f"{index:02d}.json",
            summary,
        )
        completed.append(summary)
        aggregate = _aggregate(completed)
        if (
            aggregate["total_generate_started_count"]
            > TOTAL_GENERATE_STARTED_CAP
        ):
            raise Phase4CanonicalFullFlowError(
                "aggregate model call cap was exceeded"
            )
        _write_json(
            _progress_checkpoint_path(result_root, index),
            {
                "schema_version": f"{FLOW_SCHEMA_VERSION}.progress",
                "run_id": selected_run_id,
                "last_completed_case_index": index,
                "aggregate": aggregate,
            },
        )
        if console is not None:
            print(
                f"[P4-CANONICAL-FLOW] case {index:02d}/{max_cases} "
                f"status={summary['status']} "
                f"raw_pass={summary['all_four_nodes_raw_contract_pass']} "
                f"first_pass={summary['downstream_first_pass_success']}",
                file=console,
                flush=True,
            )

    aggregate = _aggregate(completed)
    canary_complete = len(completed) >= CANARY_COUNT
    all_cases_complete = len(completed) == CASE_COUNT
    any_failed_closed = aggregate["failed_closed_count"] > 0
    if all_cases_complete:
        summary_status = (
            "model_cases_complete_with_failures_waiting_browser_audit"
            if any_failed_closed
            else "model_cases_complete_waiting_browser_audit"
        )
    elif canary_complete:
        summary_status = (
            "canary_model_flow_failed_closed"
            if any_failed_closed
            else "canary_model_execution_complete_waiting_browser_audit"
        )
    else:
        summary_status = "model_execution_incomplete"
    root = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "run_id": selected_run_id,
        "case_set_id": CASE_SET_ID,
        "policy_identity": policy["policy_identity"],
        "prompt_authority_identity": PROMPT_AUTHORITY_IDENTITY,
        "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
        "completed_case_count": len(completed),
        "canary_complete": canary_complete,
        "all_cases_complete": all_cases_complete,
        "status": summary_status,
        "case_summaries": completed,
        "aggregate": aggregate,
        "scripted_acceptance_executed": bool(completed),
        "real_browser_executed": False,
        "manual_f1_f4_loop_used": False,
        "claim_boundary": (
            "raw requirement through result-package engineering flow; "
            "real-browser evidence remains separate and pending"
        ),
    }
    summary = {
        **root,
        "summary_identity": _fresh._identity(
            root,
            revision=SUMMARY_SCHEMA_VERSION,
        ),
    }
    _write_json(result_root / "flow_summary.json", summary)
    return summary


__all__ = [
    "ACTIVE_DEFAULT_ENTRY",
    "CANARY_COUNT",
    "CASE_COUNT",
    "FLOW_AUTHORITY_ROLE",
    "FLOW_SCHEMA_VERSION",
    "Phase4CanonicalFullFlowError",
    "run_phase4_canonical_full_flow",
]
