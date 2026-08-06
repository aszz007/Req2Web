"""Run one real Qwen F1-F4 case through the authoritative LangGraph path."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Mapping

from req2web_agent import AgentContextBundle, PROMPT_AUTHORITY_IDENTITY
from req2web_generation import RetrievalGuidance
from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    NO_CAPTURE_SHA256,
    REAL_MODEL_GRAPH_REVISION,
    REAL_MODEL_SOURCE_KIND,
    Phase4RealModelGraphRuntime,
    create_real_model_graph_state,
    make_identity,
    make_real_model_raw_capture,
    phase4_create_real_model_mapping,
    phase4_normalize_and_validate_f4_output,
    phase4_validate_node_output,
)
from req2web_runtime import phase4_remote_qwen_fresh_integrated as _fresh
from req2web_runtime.phase4_fresh_delivery import run_phase4_fresh_delivery


RUNNER_SCHEMA_VERSION = "req2web.phase4.remote_qwen_langgraph_integrated.v1"
FLOW_AUTHORITY_ROLE = "active_internal_real_model_executor"
ACTIVE_DEFAULT_ENTRY = False
NODE_EXECUTION_SCHEMA_VERSION = (
    "req2web.phase4.real_model_node_execution.v1"
)
ACTIVE_RUNNER_REVISION = "raw_requirement_shared_prompt_langgraph_v1"


class Phase4RemoteQwenLangGraphError(ValueError):
    """Raised when the active real-model LangGraph run cannot close safely."""


def _not_formed_raw_capture() -> dict[str, object]:
    return {
        "state": "not_formed",
        "byte_length": 0,
        "sha256": NO_CAPTURE_SHA256,
        "source_kind": REAL_MODEL_SOURCE_KIND,
    }


def _require_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4RemoteQwenLangGraphError(f"{name} must be an object")
    return copy.deepcopy(dict(value))


def _read_json(path: Path) -> object:
    return _fresh._strict_json(path.read_bytes(), str(path))


def run_phase4_remote_qwen_langgraph_integrated(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    b_input: Mapping[str, object],
    upstream_context: AgentContextBundle,
    upstream_guidance: RetrievalGuidance,
    upstream_binding: Mapping[str, object],
    parent_experiment_binding: Mapping[str, object],
    expected_profile_identity: Mapping[str, object],
    expected_model_inventory_identity: Mapping[str, object],
    confirm_one_remote_langgraph_run: bool,
    run_id: str | None = None,
    console: object | None = None,
) -> dict[str, object]:
    """Execute one fresh case; LangGraph is the only F1-F4 scheduler."""

    if confirm_one_remote_langgraph_run is not True:
        raise Phase4RemoteQwenLangGraphError(
            "explicit confirmation is required for real-model LangGraph execution"
        )
    prepared = _fresh.prepare_phase4_remote_qwen_fresh_integrated(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        result_root=result_root,
        run_id=run_id,
        b_input=b_input,
        prompt_revision=_fresh.P4_05_FULL_DIRECT_PROMPT_REVISION,
        parent_experiment_binding=parent_experiment_binding,
        upstream_context=upstream_context,
        upstream_guidance=upstream_guidance,
        require_actual_upstream=True,
    )
    result_root = prepared["result_root"]
    profile = prepared["profile"]
    selected_run_id = str(prepared["run_id"])
    selected_b_input = _require_mapping(prepared["b_input"], "canonical B")
    graph_bound_delivery = _require_mapping(
        prepared["graph_bound_delivery"],
        "graph-bound delivery",
    )
    live_delivery = _require_mapping(
        graph_bound_delivery["live"],
        "live delivery bindings",
    )
    prepared_profile_identity = _require_mapping(
        prepared["preflight"]["profile_identity"],
        "prepared profile identity",
    )
    prepared_inventory_identity = _require_mapping(
        prepared["preflight"]["model_inventory_identity"],
        "prepared model inventory identity",
    )
    if (
        _fresh._canonical_bytes(prepared_profile_identity)
        != _fresh._canonical_bytes(dict(expected_profile_identity))
        or _fresh._canonical_bytes(prepared_inventory_identity)
        != _fresh._canonical_bytes(dict(expected_model_inventory_identity))
    ):
        raise Phase4RemoteQwenLangGraphError(
            "parent experiment runtime identity drifted"
        )
    if prepared.get("upstream_binding_mode") != "actual_agent_context_required":
        raise Phase4RemoteQwenLangGraphError(
            "active preparation did not require the actual upstream context"
        )

    history_receipt = _fresh._build_call_history(
        history_result_roots=(),
        result_root=result_root,
    )
    _fresh._write_fsync(
        result_root / "model_call_history_receipt.json",
        _fresh._canonical_bytes(history_receipt),
    )
    graph_upstream_binding = {
        "runner_revision": ACTIVE_RUNNER_REVISION,
        "prompt_authority_identity": copy.deepcopy(
            PROMPT_AUTHORITY_IDENTITY
        ),
        "actual_upstream_binding": copy.deepcopy(dict(upstream_binding)),
        "delivery_materials_binding": copy.deepcopy(
            graph_bound_delivery["binding"]
        ),
        "synthetic_assembler_bindings_used": False,
        "scripted_acceptance_used": True,
        "real_browser_acceptance_used": False,
    }
    _fresh._write_fsync(
        result_root / "langgraph_runtime_manifest.json",
        _fresh._canonical_bytes(
            {
                "schema_version": RUNNER_SCHEMA_VERSION,
                "runner_revision": ACTIVE_RUNNER_REVISION,
                "graph_revision": REAL_MODEL_GRAPH_REVISION,
                "run_id": selected_run_id,
                "case_id": selected_b_input["case_id"],
                "request_id": selected_b_input["request_id"],
                "node_order": list(NODE_ORDER),
                "prompt_authority_identity": PROMPT_AUTHORITY_IDENTITY,
                "upstream_binding": graph_upstream_binding,
                "manual_f1_f4_loop_used": False,
                "langgraph_is_workflow_scheduler": True,
                "claim_boundary": (
                    "raw requirement through renderer/result package engineering "
                    "integration with scripted acceptance; not real browser, "
                    "production, H1/gold, or formal-quality evidence"
                ),
            }
        ),
    )

    mirror = _fresh.FreshIntegratedStreamMirror(console)
    worker: _fresh.FreshIntegratedRemoteWorker | None = None
    attempt_results: dict[str, object] = {}
    direct_acceptance_receipt: dict[str, object] | None = None
    terminal_result: dict[str, object] | None = None

    def node_executor(
        node_id: str,
        authority_state: Mapping[str, object],
        authority_projection: Mapping[str, object],
    ) -> Mapping[str, object]:
        nonlocal direct_acceptance_receipt
        if worker is None:
            raise Phase4RemoteQwenLangGraphError("model worker is not loaded")
        index = NODE_ORDER.index(node_id) + 1
        if node_id == "F4":
            mapping = phase4_create_real_model_mapping(authority_state)
            _fresh._write_fsync(
                result_root / "mapping.json",
                _fresh._canonical_bytes(mapping),
            )
        input_bytes = _fresh._node_input(
            node_id=node_id,
            b_input=selected_b_input,
            state=authority_state,
            authority_projection=authority_projection,
        )
        prompt_bytes = _fresh._node_prompt(
            node_id=node_id,
            input_bytes=input_bytes,
            prompt_revision=_fresh.P4_05_FULL_DIRECT_PROMPT_REVISION,
        )
        config_bytes = _fresh._node_config(
            node_id=node_id,
            profile=profile,
        )
        request_bytes = _fresh._node_request(
            run_id=selected_run_id,
            case_id=str(selected_b_input["case_id"]),
            request_id=str(selected_b_input["request_id"]),
            node_id=node_id,
            index=index,
            prompt_revision=_fresh.P4_05_FULL_DIRECT_PROMPT_REVISION,
            parent_experiment_binding=prepared[
                "parent_experiment_binding"
            ],
        )
        pre_call = _fresh._pre_call_record(
            profile=profile,
            run_id=selected_run_id,
            case_id=str(selected_b_input["case_id"]),
            request_id=str(selected_b_input["request_id"]),
            node_id=node_id,
            index=index,
            input_bytes=input_bytes,
            prompt_bytes=prompt_bytes,
            config_bytes=config_bytes,
            request_bytes=request_bytes,
            worker_id=worker.worker_id,
            worker_pid=worker.worker_pid,
            prompt_revision=_fresh.P4_05_FULL_DIRECT_PROMPT_REVISION,
            parent_experiment_binding=prepared[
                "parent_experiment_binding"
            ],
        )
        attempt_root = result_root / "attempts" / node_id
        _fresh._write_fsync(attempt_root / "input.json", input_bytes)
        _fresh._write_fsync(attempt_root / "prompt.json", prompt_bytes)
        _fresh._write_fsync(attempt_root / "config.json", config_bytes)
        _fresh._write_fsync(attempt_root / "request.json", request_bytes)
        _fresh._write_fsync(
            attempt_root / "pre_call_record.json",
            _fresh._canonical_bytes(pre_call),
        )
        mirror.target.write(  # type: ignore[union-attr]
            f"[P4-05-LANGGRAPH] {node_id} generation started\n"
        )
        mirror.target.flush()  # type: ignore[union-attr]
        raw: bytes | None = None
        generate_started = False
        try:
            raw = worker.generate(
                node_id=node_id,
                input_bytes=input_bytes,
                prompt_bytes=prompt_bytes,
                config_bytes=config_bytes,
                request_bytes=request_bytes,
            )
            generate_started = True
            _fresh._write_fsync(attempt_root / "raw_response.bin", raw)
            parsed = _fresh._parse_model_json(
                raw,
                f"{node_id} raw response",
            )
            if not isinstance(parsed, Mapping):
                raise Phase4RemoteQwenLangGraphError(
                    f"{node_id} raw response is not an object"
                )
            output = copy.deepcopy(dict(parsed))
            raw_model_contract_success = True
            if node_id == "F4":
                output, normalization_receipt = (
                    phase4_normalize_and_validate_f4_output(
                        raw_bytes=raw,
                        output=output,
                        state=authority_state,
                    )
                )
                raw_model_contract_success = bool(
                    normalization_receipt["raw_model_contract_success"]
                )
                _fresh._write_fsync(
                    attempt_root / "normalization_receipt.json",
                    _fresh._canonical_bytes(normalization_receipt),
                )
                _fresh._write_fsync(
                    result_root / "core_f4_normalization_receipt.json",
                    _fresh._canonical_bytes(normalization_receipt),
                )
                direct_acceptance_receipt = (
                    _fresh._f4_direct_acceptance_policy_receipt(
                        input_bytes=input_bytes,
                        output=output,
                        raw_bytes=raw,
                    )
                )
                _fresh._write_fsync(
                    result_root
                    / "f4_direct_acceptance_policy_receipt.json",
                    _fresh._canonical_bytes(direct_acceptance_receipt),
                )
            else:
                phase4_validate_node_output(
                    node_id,
                    output,
                    authority_state,
                )
            _fresh._write_fsync(
                attempt_root / "validated_node_output.json",
                _fresh._canonical_bytes(output),
            )
            attempt = _fresh._attempt_record(
                run_id=selected_run_id,
                case_id=str(selected_b_input["case_id"]),
                request_id=str(selected_b_input["request_id"]),
                node_id=node_id,
                input_bytes=input_bytes,
                prompt_bytes=prompt_bytes,
                config_bytes=config_bytes,
                request_bytes=request_bytes,
                pre_call_record=pre_call,
                worker_id=worker.worker_id,
                worker_pid=worker.worker_pid,
                raw=raw,
                generate_started=True,
                status="validated",
                failure_code=None,
                prompt_revision=_fresh.P4_05_FULL_DIRECT_PROMPT_REVISION,
                parent_experiment_binding=prepared[
                    "parent_experiment_binding"
                ],
            )
            attempt_results[node_id] = attempt
            _fresh._write_fsync(
                attempt_root / "attempt_result.json",
                _fresh._canonical_bytes(attempt),
            )
            mirror.target.write(  # type: ignore[union-attr]
                f"\n[P4-05-LANGGRAPH] {node_id} generation completed\n"
            )
            mirror.target.flush()  # type: ignore[union-attr]
            return {
                "schema_version": NODE_EXECUTION_SCHEMA_VERSION,
                "node_id": node_id,
                "status": "validated",
                "source_kind": REAL_MODEL_SOURCE_KIND,
                "generate_call_count": 1,
                "raw_capture": make_real_model_raw_capture(raw),
                "attempt_identity": _fresh._identity(
                    attempt,
                    revision=_fresh.P4_05_ATTEMPT_SCHEMA_VERSION,
                ),
                "output": output,
                "raw_model_contract_success": raw_model_contract_success,
                "normalized_node_contract_success": True,
                "failure": None,
            }
        except Exception as exc:
            generate_started = (
                generate_started
                or mirror.generation_started_for(node_id)
            )
            failure_code = (
                "node_failed_closed"
                if generate_started
                else "worker_not_started"
            )
            attempt = _fresh._attempt_record(
                run_id=selected_run_id,
                case_id=str(selected_b_input["case_id"]),
                request_id=str(selected_b_input["request_id"]),
                node_id=node_id,
                input_bytes=input_bytes,
                prompt_bytes=prompt_bytes,
                config_bytes=config_bytes,
                request_bytes=request_bytes,
                pre_call_record=pre_call,
                worker_id=worker.worker_id,
                worker_pid=worker.worker_pid,
                raw=raw,
                generate_started=generate_started,
                status="failed_closed",
                failure_code=failure_code,
                prompt_revision=_fresh.P4_05_FULL_DIRECT_PROMPT_REVISION,
                parent_experiment_binding=prepared[
                    "parent_experiment_binding"
                ],
            )
            attempt_results[node_id] = attempt
            _fresh._write_fsync(
                attempt_root / "attempt_result.json",
                _fresh._canonical_bytes(attempt),
            )
            failure = {
                "failure_code": failure_code,
                "failure_stage": node_id,
                "retry_allowed": False,
                "fallback_allowed": False,
                "message_code": type(exc).__name__,
            }
            _fresh._write_fsync(
                result_root / "failure.json",
                _fresh._canonical_bytes(failure),
            )
            return {
                "schema_version": NODE_EXECUTION_SCHEMA_VERSION,
                "node_id": node_id,
                "status": "failed_closed",
                "source_kind": REAL_MODEL_SOURCE_KIND,
                "generate_call_count": 1 if generate_started else 0,
                "raw_capture": (
                    make_real_model_raw_capture(raw)
                    if raw is not None
                    else _not_formed_raw_capture()
                ),
                "attempt_identity": _fresh._identity(
                    attempt,
                    revision=_fresh.P4_05_ATTEMPT_SCHEMA_VERSION,
                ),
                "output": None,
                "raw_model_contract_success": False,
                "normalized_node_contract_success": False,
                "failure": failure,
            }

    def delivery_executor(
        graph_state: Mapping[str, object],
        page_spec: Mapping[str, object],
        assembly_report: Mapping[str, object],
    ) -> Mapping[str, object]:
        mapping = _require_mapping(
            graph_state["mapping_record"],
            "LangGraph mapping",
        )
        composition = _require_mapping(
            graph_state["candidate_composition_record"],
            "LangGraph candidate composition",
        )
        _fresh._write_fsync(
            result_root / "mapping.json",
            _fresh._canonical_bytes(mapping),
        )
        _fresh._write_fsync(
            result_root / "candidate_composition_record.json",
            _fresh._canonical_bytes(composition),
        )
        _fresh._write_fsync(
            result_root / "assembled_page_spec.json",
            _fresh._canonical_bytes(dict(page_spec)),
        )
        _fresh._write_fsync(
            result_root / "assembly_report.json",
            _fresh._canonical_bytes(dict(assembly_report)),
        )
        ledger = _fresh._call_ledger(
            run_id=selected_run_id,
            node_results=attempt_results,
        )
        aggregate_ledger = _fresh._aggregate_call_ledger(
            run_id=selected_run_id,
            history_receipt=history_receipt,
            current_ledger=ledger,
        )
        _fresh._write_fsync(
            result_root / "model_call_ledger.json",
            _fresh._canonical_bytes(ledger),
        )
        _fresh._write_fsync(
            result_root / "aggregate_model_call_ledger.json",
            _fresh._canonical_bytes(aggregate_ledger),
        )
        f4_execution = _require_mapping(
            graph_state["node_execution_records"]["F4"],
            "F4 graph execution",
        )
        source_result = {
            "schema_version": _fresh.P4_05_RESULT_SCHEMA_VERSION,
            "pilot_id": _fresh.P4_05_PILOT_ID,
            "run_id": selected_run_id,
            "case_id": selected_b_input["case_id"],
            "request_id": selected_b_input["request_id"],
            "parent_experiment_binding": prepared[
                "parent_experiment_binding"
            ],
            "source_kind": REAL_MODEL_SOURCE_KIND,
            "status": "assembled",
            "model_generate_calls": aggregate_ledger[
                "aggregate_total_generate_calls"
            ],
            "model_call_ledger_identity": _fresh._identity(
                ledger,
                revision=_fresh.P4_05_LEDGER_SCHEMA_VERSION,
            ),
            "aggregate_model_call_ledger_identity": _fresh._identity(
                aggregate_ledger,
                revision=_fresh.P4_05_AGGREGATE_LEDGER_SCHEMA_VERSION,
            ),
            "resume_receipt_identity": None,
            "raw_model_contract_success": f4_execution[
                "raw_model_contract_success"
            ],
            "normalized_node_contract_success": f4_execution[
                "normalized_node_contract_success"
            ],
            "agent_chain_system_output_usable": True,
            "composition_status": "composed_in_langgraph",
            "assembler_status": "assembled_in_langgraph",
            "downstream": "not_executed",
            "downstream_policy": (
                _fresh.PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1
            ),
            "a07b_status": "not_executed_by_policy",
            "f4_direct_acceptance_policy_receipt_identity": (
                None
                if direct_acceptance_receipt is None
                else _fresh._identity(
                    direct_acceptance_receipt,
                    revision=(
                        _fresh
                        .P4_05_F4_DIRECT_ACCEPTANCE_POLICY_RECEIPT_SCHEMA_VERSION
                    ),
                )
            ),
            "prompt_revision": _fresh.P4_05_FULL_DIRECT_PROMPT_REVISION,
            "prompt_authority_identity": PROMPT_AUTHORITY_IDENTITY,
            "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
            "manual_f1_f4_loop_used": False,
            "historical_strict_result": "0/2_unchanged",
            "claim_boundary": (
                "real-model LangGraph engineering integration through "
                "renderer/result package with scripted acceptance; not real "
                "browser, production, H1/gold, or formal-quality evidence"
            ),
            "failure": None,
        }
        _fresh._write_fsync(
            result_root / "revalidation_result.json",
            _fresh._canonical_bytes(source_result),
        )
        try:
            receipt = run_phase4_fresh_delivery(
                source_root=result_root,
                delivery_root=result_root / "delivery",
                context=upstream_context,
                guidance=upstream_guidance,
                manifest=live_delivery["manifest"],
                selected=live_delivery["selected"],
                local_request=live_delivery["local_request"],
                pre_invocation_audit=live_delivery[
                    "pre_invocation_audit"
                ],
                local_qwen_preparation=live_delivery[
                    "local_qwen_preparation"
                ],
                package=live_delivery["package"],
                frozen_g0_reference=live_delivery[
                    "frozen_g0_reference"
                ],
                fallback_record=live_delivery["fallback_record"],
                fallback_snapshot_dir=live_delivery[
                    "fallback_snapshot_dir"
                ],
                scripted_acceptance_fixture=live_delivery[
                    "scripted_acceptance_fixture"
                ],
                delivery_policy=(
                    _fresh.PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1
                ),
            )
            delivery = receipt.to_dict()
            success = (
                receipt.success_accounting["delivery_success"] is True
            )
        except Exception as exc:
            delivery = {
                "status": "failed_closed",
                "failure": {
                    "code": "fresh_delivery_exception",
                    "message_code": type(exc).__name__,
                    "retry_allowed": False,
                    "model_retry_performed": False,
                    "automatic_fallback_performed": False,
                },
            }
            success = False
            _fresh._write_fsync(
                result_root / "delivery_failure.json",
                _fresh._canonical_bytes(delivery),
            )
        final = {
            **source_result,
            "status": (
                "delivery_terminal_success"
                if success
                else "failed_closed"
            ),
            "delivery_receipt": (
                delivery if "receipt_id" in delivery else None
            ),
            "delivery_failure": (
                None if "receipt_id" in delivery else delivery
            ),
            "delivery_result_identity": _fresh._identity(
                delivery,
                revision=(
                    f"{_fresh.P4_05_RESULT_SCHEMA_VERSION}.delivery"
                ),
            ),
        }
        _fresh._write_fsync(
            result_root / "p4_05_final_result.json",
            _fresh._canonical_bytes(final),
        )
        return {
            "graph_delivery_success": success,
            "terminal_status": final["status"],
            "delivery": delivery,
            "final_result_identity": _fresh._identity(
                final,
                revision=_fresh.P4_05_RESULT_SCHEMA_VERSION,
            ),
        }

    graph_result: dict[str, object] | None = None
    try:
        mirror.target.write("[P4-05-LANGGRAPH] load started\n")  # type: ignore[union-attr]
        mirror.target.flush()  # type: ignore[union-attr]
        worker = _fresh.FreshIntegratedRemoteWorker(
            model_root=model_root,
            profile=profile,
            mirror=mirror,
        )
        _fresh._write_fsync(
            result_root / "load_receipt.json",
            _fresh._canonical_bytes(
                {
                    "schema_version": (
                        f"{RUNNER_SCHEMA_VERSION}.load_receipt.v1"
                    ),
                    "run_id": selected_run_id,
                    "profile_identity": prepared_profile_identity,
                    "loaded_facts": worker.loaded_facts,
                    "model_loaded": True,
                    "dtype": _fresh.P4_05_DTYPE,
                    "quantization": _fresh.P4_05_QUANTIZATION,
                    "device": "cuda:0",
                    "cpu_offload": False,
                }
            ),
        )
        mirror.target.write("[P4-05-LANGGRAPH] load completed\n")  # type: ignore[union-attr]
        mirror.target.flush()  # type: ignore[union-attr]
        runtime = Phase4RealModelGraphRuntime(
            node_executor=node_executor,
            context=upstream_context,
            guidance=upstream_guidance,
            delivery_executor=delivery_executor,
        )
        initial_state = create_real_model_graph_state(
            run_id=selected_run_id,
            b_input=selected_b_input,
            upstream_binding=graph_upstream_binding,
        )
        graph_result = runtime.invoke(
            initial_state,
            thread_id=f"{selected_run_id}:langgraph",
        )
        _fresh._write_fsync(
            result_root / "langgraph_final_state.json",
            _fresh._canonical_bytes(graph_result),
        )
        _fresh._write_fsync(
            result_root / "langgraph_events.json",
            _fresh._canonical_bytes(graph_result["events"]),
        )
        final_path = result_root / "p4_05_final_result.json"
        if final_path.is_file():
            terminal_result = _require_mapping(
                _read_json(final_path),
                "LangGraph terminal result",
            )
        else:
            terminal_result = {
                "schema_version": _fresh.P4_05_RESULT_SCHEMA_VERSION,
                "pilot_id": _fresh.P4_05_PILOT_ID,
                "run_id": selected_run_id,
                "case_id": selected_b_input["case_id"],
                "request_id": selected_b_input["request_id"],
                "source_kind": REAL_MODEL_SOURCE_KIND,
                "status": "failed_closed",
                "prompt_revision": _fresh.P4_05_FULL_DIRECT_PROMPT_REVISION,
                "prompt_authority_identity": PROMPT_AUTHORITY_IDENTITY,
                "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
                "manual_f1_f4_loop_used": False,
                "failure": graph_result["failure"],
            }
            _fresh._write_fsync(
                final_path,
                _fresh._canonical_bytes(terminal_result),
            )
    finally:
        teardown = (
            worker.close()
            if worker is not None
            else {
                "worker_id": None,
                "worker_pid": None,
                "worker_exit_code": None,
                "worker_exit_verified": False,
                "terminal_status": "worker_not_started",
                "generation_started": False,
                "generate_calls": {
                    node_id: 0 for node_id in NODE_ORDER
                },
                "attempt_envelopes": {
                    node_id: 0 for node_id in NODE_ORDER
                },
            }
        )
        ledger = _fresh._call_ledger(
            run_id=selected_run_id,
            node_results=attempt_results,
        )
        aggregate_ledger = _fresh._aggregate_call_ledger(
            run_id=selected_run_id,
            history_receipt=history_receipt,
            current_ledger=ledger,
        )
        _fresh._write_fsync(
            result_root / "model_call_ledger.json",
            _fresh._canonical_bytes(ledger),
        )
        _fresh._write_fsync(
            result_root / "aggregate_model_call_ledger.json",
            _fresh._canonical_bytes(aggregate_ledger),
        )
        _fresh._write_fsync(
            result_root / "supervisor_result.json",
            _fresh._canonical_bytes(
                {
                    "schema_version": (
                        f"{RUNNER_SCHEMA_VERSION}.supervisor.v1"
                    ),
                    "run_id": selected_run_id,
                    "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
                    "terminal_status": teardown["terminal_status"],
                    "worker_id": teardown["worker_id"],
                    "worker_pid": teardown["worker_pid"],
                    "worker_exit_code": teardown["worker_exit_code"],
                    "worker_exit_verified": teardown[
                        "worker_exit_verified"
                    ],
                    "generation_started": teardown[
                        "generation_started"
                    ],
                    "generate_calls": teardown["generate_calls"],
                    "attempt_envelopes": teardown[
                        "attempt_envelopes"
                    ],
                    "model_call_ledger_identity": _fresh._identity(
                        ledger,
                        revision=_fresh.P4_05_LEDGER_SCHEMA_VERSION,
                    ),
                    "aggregate_model_call_ledger_identity": (
                        _fresh._identity(
                            aggregate_ledger,
                            revision=(
                                _fresh
                                .P4_05_AGGREGATE_LEDGER_SCHEMA_VERSION
                            ),
                        )
                    ),
                }
            ),
        )
    if terminal_result is None:
        raise Phase4RemoteQwenLangGraphError(
            "real-model LangGraph run did not produce a terminal result"
        )
    return terminal_result


__all__ = [
    "ACTIVE_DEFAULT_ENTRY",
    "ACTIVE_RUNNER_REVISION",
    "FLOW_AUTHORITY_ROLE",
    "Phase4RemoteQwenLangGraphError",
    "RUNNER_SCHEMA_VERSION",
    "run_phase4_remote_qwen_langgraph_integrated",
]
