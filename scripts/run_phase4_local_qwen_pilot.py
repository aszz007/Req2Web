"""Prepare or explicitly run the bounded Phase 4 local Qwen 9B pilot."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from req2web_orchestration.phase4_graph import synthetic_commerce_b_input
from req2web_runtime.phase4_local_qwen import (
    ATTEMPT_RESULT_SCHEMA_VERSION,
    CHANGE_REASONS,
    NodeProjectionPolicy,
    OUTCOME_SCHEMA_VERSION,
    P4R2_PILOT_ID,
    P4R2_PROMPT_REVISION,
    P4R2_PROJECTION_REVISION,
    P4R3_PILOT_ID,
    P4R3_PROMPT_REVISION,
    P4R4_PILOT_ID,
    P4R4_PROMPT_REVISION,
    P4R5_PILOT_ID,
    P4R5_PROMPT_REVISION,
    P4R5_PROJECTION_REVISION,
    P4R6_PILOT_ID,
    P4R6_PROMPT_REVISION,
    P4R6_PROJECTION_REVISION,
    PilotSupervisorReceipt,
    Phase4LocalQwenContractError,
    Phase4LocalQwenPilotRunner,
    SupervisedWorkerStartFailure,
    acquire_pilot_execution_lease,
    build_synthetic_case_binding,
    load_prepared_local_qwen_pilot,
    load_p4r2_policy_revision,
    load_p4r3_policy_revision,
    load_p4r4_policy_revision,
    load_p4r5_policy_revision,
    load_p4r6_policy_revision,
    make_canonical_identity,
    persist_pilot_outcome,
    persist_supervisor_receipt,
    persist_worker_stderr_artifact,
    prepare_local_qwen_pilot,
    probe_local_gpu_facts,
    start_supervised_local_qwen_runtime,
    validate_p4r6_attempt_continuation,
)


def _policies(*, prompt_revision: str, config_revision: str, projection_revision: str = P4R2_PROJECTION_REVISION) -> tuple[NodeProjectionPolicy, ...]:
    caps = {
        "input_bytes": 131072,
        "output_bytes": 65536,
        "prompt_bytes": 16384,
        "config_bytes": 8192,
        "request_bytes": 8192,
        "ref_count": 32,
    }
    categories = {
        "F1": ["canonical_b_input", "case_identity"],
        "F2": ["canonical_b_input", "validated_F1_output", "deterministic_registry"],
        "F3": ["canonical_b_input", "validated_F1_output", "validated_F2_output", "deterministic_registry"],
        "F4": ["canonical_b_input", "validated_F1_output", "validated_F2_output", "validated_F3_output", "deterministic_registry", "deterministic_mapping"],
    }
    prohibited = [
        "b_aux_sidecar", "retrieval_evidence", "h1_gold", "browser_evidence",
        "hidden_reasoning",
    ]
    upstream = {"F1": [], "F2": ["F1"], "F3": ["F1", "F2"], "F4": ["F1", "F2", "F3"]}
    return tuple(
        NodeProjectionPolicy.create(
            node_id=node_id,
            allowed_categories=categories[node_id],
            prohibited_categories=prohibited,
            field_caps=caps,
            upstream_required_node_ids=upstream[node_id],
            projection_revision=projection_revision,
            prompt_template_revision=prompt_revision,
            config_revision=config_revision,
        )
        for node_id in ("F1", "F2", "F3", "F4")
    )


def _r2_historical_policies() -> tuple[NodeProjectionPolicy, ...]:
    return _policies(prompt_revision=P4R2_PROMPT_REVISION, config_revision="p4-03r2-config-v1")


def _default_policies() -> tuple[NodeProjectionPolicy, ...]:
    return _policies(prompt_revision=P4R3_PROMPT_REVISION, config_revision="p4-03r3-config-v1")


def _r3_policies() -> tuple[NodeProjectionPolicy, ...]:
    return _default_policies()


def _r4_policies() -> tuple[NodeProjectionPolicy, ...]:
    return _policies(prompt_revision=P4R4_PROMPT_REVISION, config_revision="p4-03r4-config-v1")


def _r5_policies() -> tuple[NodeProjectionPolicy, ...]:
    return _policies(
        prompt_revision=P4R5_PROMPT_REVISION,
        config_revision="p4-03r5-config-v1",
        projection_revision=P4R5_PROJECTION_REVISION,
    )


def _r6_policies() -> tuple[NodeProjectionPolicy, ...]:
    return _policies(
        prompt_revision=P4R6_PROMPT_REVISION,
        config_revision="p4-03r6-config-v1",
        projection_revision=P4R6_PROJECTION_REVISION,
    )


def _offline_process() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["LANGSMITH_TRACING"] = "0"
    os.environ["LANGCHAIN_TRACING_V2"] = "0"


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--integrity-evidence", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare or explicitly run the bounded local Qwen 9B Phase 4 pilot."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Live-validate and write the no-run pre-call manifest.")
    _common_arguments(prepare)
    run = commands.add_parser("run", help="Revalidate, explicitly load, and execute the bounded pilot.")
    _common_arguments(run)
    run.add_argument(
        "--second-attempt-change-reason",
        choices=CHANGE_REASONS,
        help="Explicitly authorize one new v2 envelope after a first node-local failure.",
    )
    for command in (prepare, run):
        command.add_argument(
            "--pilot",
            choices=("r2", "r3", "r4", "r5", "r6"),
            default="r6",
            help="Explicit pilot revision; the default is prompt-order recovery R6.",
        )
        command.add_argument(
            "--resume-r6-timeout",
            action="store_true",
            help="Continue the fixed R6 generation-timeout as independent F3 attempt 2; this is not graph resume.",
        )
    return parser


def _validate_cli_combination(args: argparse.Namespace) -> None:
    if args.resume_r6_timeout and args.pilot != "r6":
        raise Phase4LocalQwenContractError("--resume-r6-timeout is only valid with --pilot r6")
    if args.resume_r6_timeout and args.command == "run" and args.second_attempt_change_reason != "output_schema_clarification":
        raise Phase4LocalQwenContractError("R6 timeout continuation requires --second-attempt-change-reason=output_schema_clarification")


def _summary(outcome) -> dict[str, object]:
    return {
        "status": outcome.status,
        "stop_reason": outcome.stop_reason,
        "node_local_statuses": outcome.node_local_statuses,
        "node_total_counts": outcome.node_total_counts,
        "integrated_outcome": outcome.integrated_outcome,
        "integrated_node_raw_contract_pass_count": outcome.integrated_node_raw_contract_pass_count,
        "composition_status": outcome.composition_status,
        "assembler_status": outcome.assembler_status,
        "model_calls_performed": outcome.model_calls_performed,
        "historical_strict_result": outcome.historical_strict_result,
        "claim_boundary": outcome.claim_boundary,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _validate_cli_combination(args)
    _offline_process()
    b_input = synthetic_commerce_b_input()
    try:
        if args.command == "prepare":
            if args.pilot == "r2":
                policy_kwargs = {"r2_policy": load_p4r2_policy_revision()[0], "pilot_id": P4R2_PILOT_ID, "policies": _r2_historical_policies()}
            elif args.pilot == "r3":
                policy_kwargs = {"r3_policy": load_p4r3_policy_revision()[0], "pilot_id": P4R3_PILOT_ID, "policies": _r3_policies()}
            elif args.pilot == "r4":
                policy_kwargs = {"r4_policy": load_p4r4_policy_revision()[0], "pilot_id": P4R4_PILOT_ID, "policies": _r4_policies()}
            elif args.pilot == "r5":
                policy_kwargs = {"r5_policy": load_p4r5_policy_revision()[0], "pilot_id": P4R5_PILOT_ID, "policies": _r5_policies(), "checkpoint_b_input": b_input}
            elif args.pilot == "r6":
                policy_kwargs = {"r6_policy": load_p4r6_policy_revision()[0], "pilot_id": P4R6_PILOT_ID, "policies": _r6_policies(), "checkpoint_b_input": b_input}
            else:
                raise Phase4LocalQwenContractError("unknown pilot revision before prepare")
            binding, policies, profile, manifest = prepare_local_qwen_pilot(
                model_root=args.model_root,
                integrity_evidence=args.integrity_evidence,
                result_root=args.result_root,
                case_binding=build_synthetic_case_binding(b_input),
                gpu_facts=probe_local_gpu_facts(),
                r6_timeout_continuation=args.resume_r6_timeout,
                **policy_kwargs,
            )
            print(json.dumps({
                "status": manifest.status,
                "manifest_id": manifest.manifest_id,
                "pilot_id": binding.pilot_id,
                "profile_id": profile.profile_id,
                "node_policy_ids": {policy.node_id: policy.policy_id for policy in policies},
                "weight_bytes_hashed": manifest.weight_bytes_hashed,
                "model_loaded": manifest.model_loaded,
                "run_occurred": manifest.run_occurred,
                "authorization_state": manifest.authorization_state,
                "action_state": manifest.action_state,
            }, ensure_ascii=False, sort_keys=True))
            return 0

        if args.pilot == "r2":
            policy_kwargs = {"r2_policy": load_p4r2_policy_revision()[0]}
        elif args.pilot == "r3":
            policy_kwargs = {"r3_policy": load_p4r3_policy_revision()[0]}
        elif args.pilot == "r4":
            policy_kwargs = {"r4_policy": load_p4r4_policy_revision()[0]}
        elif args.pilot == "r5":
            policy_kwargs = {"r5_policy": load_p4r5_policy_revision()[0]}
        elif args.pilot == "r6":
            policy_kwargs = {"r6_policy": load_p4r6_policy_revision()[0]}
        else:
            raise Phase4LocalQwenContractError("unknown pilot revision before Popen")
        binding, policies, profile, manifest = load_prepared_local_qwen_pilot(
            model_root=args.model_root,
            integrity_evidence=args.integrity_evidence,
            result_root=args.result_root,
            b_input=b_input,
            r6_timeout_continuation=args.resume_r6_timeout,
            **policy_kwargs,
        )
        lease = acquire_pilot_execution_lease(
            result_root=args.result_root,
            pilot=binding,
            manifest=manifest,
        )
        backend = None
        runner = None
        outcome = None
        load_attempted = False
        execution_failed = False
        evidence_persistence_failed = False
        outcome_persisted = False
        startup_teardown = None
        startup_stderr_bytes = None
        try:
            load_attempted = True
            runtime = start_supervised_local_qwen_runtime(
                model_root=args.model_root,
                integrity_evidence=args.integrity_evidence,
                result_root=args.result_root,
                r6_timeout_continuation=args.resume_r6_timeout,
            )
            backend = runtime.backend
            continuation = None
            if args.resume_r6_timeout:
                continuation_source = validate_p4r6_attempt_continuation(
                    model_root=args.model_root,
                    result_root=args.result_root,
                    pilot=runtime.pilot,
                    profile=runtime.profile,
                    manifest=runtime.manifest,
                )
                continuation = (
                    continuation_source["continuation_receipt"],
                    continuation_source["attempt"],
                )
            runner = Phase4LocalQwenPilotRunner(
                pilot=runtime.pilot,
                policies=runtime.policies,
                profile=runtime.profile,
                manifest=runtime.manifest,
                result_root=args.result_root,
                b_input=runtime.b_input,
                model_root=args.model_root,
                backend=backend,
                source_kind="real_local_qwen",
                load_receipt=runtime.load_receipt,
                execution_lease=runtime.execution_lease,
                runtime_start_claim=runtime.runtime_start_claim,
                r6_attempt_continuation=continuation,
            )
            if args.resume_r6_timeout:
                continued_f3 = runner.run_node_local(
                    node_id="F3",
                    prompt_version=2,
                    change_reason="output_schema_clarification",
                )
                if continued_f3.failure_code is not None:
                    outcome = runner.outcome(
                        stop_reason=str(
                            runner.ledger.stop_reason or "node_budget_exhausted"
                        )
                    )
            node_order = ("F3", "F4") if args.pilot in {"r5", "r6"} else ("F1", "F2", "F3", "F4")
            if args.resume_r6_timeout:
                node_order = () if outcome is not None else ("F4",)
            for node_id in node_order:
                first = runner.run_node_local(node_id=node_id)
                if first.failure_code is None:
                    continue
                if runner.stopped:
                    outcome = runner.outcome(
                        stop_reason="worker_terminal_failed_closed"
                    )
                    break
                if args.second_attempt_change_reason is None:
                    outcome = runner.stop_for_report(
                        reason="node_local_first_failure_report"
                    )
                    break
                second = runner.run_node_local(
                    node_id=node_id,
                    prompt_version=2,
                    change_reason=args.second_attempt_change_reason,
                )
                if second.failure_code is not None:
                    outcome = runner.outcome(stop_reason="node_budget_exhausted")
                    break
            if outcome is None:
                if args.pilot in {"r5", "r6"}:
                    outcome = runner.stop_for_report(
                        reason="checkpoint_node_local_complete"
                    )
                else:
                    outcome = runner.run_integrated()
            try:
                persist_pilot_outcome(result_root=args.result_root, outcome=outcome)
                outcome_persisted = True
            except Exception:
                evidence_persistence_failed = True
                raise
        except BaseException as exc:
            execution_failed = True
            if isinstance(exc, SupervisedWorkerStartFailure):
                startup_teardown = exc.teardown_facts
                startup_stderr_bytes = exc.stderr_bytes
                if exc.failure_code == "evidence_persistence_failed":
                    evidence_persistence_failed = True
            if runner is not None and outcome is None:
                try:
                    outcome = runner.stop_for_report(
                        reason="parent_execution_failed"
                    )
                    persist_pilot_outcome(
                        result_root=args.result_root,
                        outcome=outcome,
                    )
                    outcome_persisted = True
                except Exception:
                    evidence_persistence_failed = True
            raise
        finally:
            if backend is None:
                teardown = startup_teardown or {
                    "worker_id": None,
                    "worker_pid": None,
                    "worker_exit_code": None,
                    "worker_exit_verified": True,
                    "graceful_shutdown_requested": False,
                    "terminate_sent": False,
                    "kill_sent": False,
                    "terminal_status": "load_failed",
                    "stderr_capture_completed": False,
                    "stderr_capture_error": "worker_not_started",
                    "stderr_thread_joined": False,
                    "stderr_identity": None,
                }
            else:
                teardown = backend.close()
            stderr_bytes = (
                startup_stderr_bytes
                if backend is None
                else backend.stderr_bytes
            )
            stderr_identity = None
            if (
                teardown.get("stderr_capture_completed") is not True
                or stderr_bytes is None
            ):
                evidence_persistence_failed = True
            else:
                try:
                    stderr_artifact = persist_worker_stderr_artifact(
                        result_root=args.result_root,
                        stderr_bytes=stderr_bytes,
                    )
                    stderr_identity = stderr_artifact.identity()
                except Exception:
                    evidence_persistence_failed = True
            latest_result = None if runner is None else runner.latest_result
            terminal_status = str(teardown["terminal_status"])
            if teardown["worker_exit_verified"] is not True:
                terminal_status = "worker_teardown_unverified"
            elif evidence_persistence_failed:
                terminal_status = "evidence_persistence_failed"
            elif execution_failed and backend is not None:
                terminal_status = "parent_execution_failed"
            elif latest_result is not None and latest_result.failure_code in {
                "generation_timeout", "generation_cancelled"
            }:
                terminal_status = latest_result.failure_code
            elif terminal_status == "normal_completed" and outcome is not None:
                terminal_status = (
                    "normal_completed"
                    if outcome.status == "integrated_success"
                    else "pilot_stopped"
                )
            receipt = PilotSupervisorReceipt.create(
                lease=lease,
                terminal_status=terminal_status,
                worker_id=teardown["worker_id"],
                worker_pid=teardown["worker_pid"],
                worker_exit_code=teardown["worker_exit_code"],
                worker_exit_verified=bool(teardown["worker_exit_verified"]),
                graceful_shutdown_requested=bool(
                    teardown["graceful_shutdown_requested"]
                ),
                terminate_sent=bool(teardown["terminate_sent"]),
                kill_sent=bool(teardown["kill_sent"]),
                generation_started=(
                    False if backend is None else backend.generation_started
                ),
                raw_status=(
                    "no_generate"
                    if latest_result is None
                    else (
                        "captured"
                        if str(latest_result.raw_status).startswith("captured")
                        else "not_captured"
                    )
                ),
                latest_attempt_result_identity=(
                    None
                    if latest_result is None
                    else make_canonical_identity(
                        latest_result.to_dict(),
                        revision=ATTEMPT_RESULT_SCHEMA_VERSION,
                    )
                ),
                pilot_outcome_identity=(
                    None
                    if outcome is None or not outcome_persisted
                    else make_canonical_identity(
                        outcome.to_dict(), revision=OUTCOME_SCHEMA_VERSION
                    )
                ),
                stderr_identity=stderr_identity,
                model_action=load_attempted,
            )
            persist_supervisor_receipt(
                result_root=args.result_root,
                receipt=receipt,
            )
        if terminal_status not in {"normal_completed", "pilot_stopped"}:
            raise Phase4LocalQwenContractError(
                f"pilot supervisor terminated as {terminal_status}"
            )
        if outcome is None:
            raise Phase4LocalQwenContractError("pilot ended without an outcome")
        print(json.dumps(_summary(outcome), ensure_ascii=False, sort_keys=True))
        return 0 if outcome.status == "integrated_success" else 3
    except (OSError, Phase4LocalQwenContractError) as exc:
        print(f"p4_03_failed_closed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
