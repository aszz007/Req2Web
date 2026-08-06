"""Final local route adapter for the bounded Qwen3.5-27B recovery pilot.

The adapter starts from a runner result cross-bound to the canonical remote
model/runtime inventories and live-replayed local return artifacts. It rebuilds
case materials only from the owning-validated filtered repository archive,
reuses the accepted A-07a/A-07b live-route machinery, and then recomputes the
pre-registered semantic coverage. Caller-provided parser, generic-gate,
semantic, or decision values are not accepted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, Mapping
import zipfile

from req2web_provider.semantic_candidate import (
    ProviderRawResponse,
    parse_provider_raw_response,
)

from . import autodl_trusted_remote_live_route as _live_route
from . import autodl_repository_archive as _repository_archive
from . import qwen27b_case_bundle as _case_bundle
from .autodl_trusted_remote_case_loader import (
    build_fixed_trusted_remote_case_materials_v2,
)
from .qwen27b_recovery_runner import (
    RUN_MANIFEST_FILENAME,
    validate_qwen27b_recovery_return_against,
)
from .qwen27b_semantic_closure import (
    Qwen27bSemanticClosureResult,
    apply_qwen27b_deterministic_semantic_closure,
)
from .qwen27b_semantic_gate import (
    evaluate_qwen27b_semantic_coverage_after_route,
)

FINAL_ROUTE_SCHEMA = "req2web.runtime.qwen35_27b_final_route.v2"
FINAL_ROUTE_CASE_SCHEMA = "req2web.runtime.qwen35_27b_final_route_case.v2"
FINAL_ROUTE_CLOSURE_GATE_SCHEMA = (
    "req2web.runtime.qwen35_27b_semantic_closure_gate.v1"
)


class Qwen27BFinalRouteError(ValueError):
    """Raised when the fixed recovery route cannot be replayed exactly."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _prepare_work_root(value: Path | str) -> Path:
    root = Path(value)
    if root.exists():
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise Qwen27BFinalRouteError("final_route_work_root_not_empty")
        root = root.resolve(strict=True)
    else:
        root.mkdir(parents=True, exist_ok=False)
        root = root.resolve(strict=True)
    marker = {
        "schema_version": "req2web.runtime.qwen35_27b_final_route_work_root.v1",
        "purpose": "local_replay_only",
    }
    (root / ".req2web_project_temp_root.json").write_bytes(_canonical(marker))
    return root


def _extract_archive_source_tree(
    repository_archive: Path | str,
    repository_archive_manifest: Path | str,
    route_work: Path,
) -> Path:
    """Extract exact owning-validated archive bytes into a dedicated source root."""

    try:
        manifest = _repository_archive.validate_archive_file(
            repository_archive,
            repository_archive_manifest,
        )
    except (OSError, _repository_archive.RepositoryArchiveError) as exc:
        raise Qwen27BFinalRouteError(
            "final_route_repository_archive_invalid"
        ) from exc
    rows = manifest.to_dict()["files"]
    source_root = route_work / "archive-source"
    if source_root.exists():
        raise Qwen27BFinalRouteError(
            "final_route_archive_source_root_exists"
        )
    source_root.mkdir()
    source_root = source_root.resolve(strict=True)
    try:
        with zipfile.ZipFile(Path(repository_archive), "r") as archive:
            names = archive.namelist()
            expected_names = [row["path"] for row in rows]
            if names != expected_names:
                raise Qwen27BFinalRouteError(
                    "final_route_archive_entry_set_invalid"
                )
            for row in rows:
                relative = row["path"]
                parts = PurePosixPath(relative).parts
                if (
                    not parts
                    or PurePosixPath(relative).is_absolute()
                    or any(part in {"", ".", ".."} for part in parts)
                    or "\\" in relative
                ):
                    raise Qwen27BFinalRouteError(
                        "final_route_archive_path_invalid"
                    )
                destination = source_root.joinpath(*parts)
                try:
                    destination.resolve(strict=False).relative_to(source_root)
                except ValueError as exc:
                    raise Qwen27BFinalRouteError(
                        "final_route_archive_path_escape"
                    ) from exc
                raw = archive.read(relative)
                if (
                    len(raw) != row["byte_length"]
                    or _sha(raw) != row["sha256"]
                ):
                    raise Qwen27BFinalRouteError(
                        "final_route_archive_file_binding_invalid"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise Qwen27BFinalRouteError(
                        "final_route_archive_destination_exists"
                    )
                destination.write_bytes(raw)
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        shutil.rmtree(source_root, ignore_errors=True)
        raise Qwen27BFinalRouteError(
            "final_route_archive_extraction_failed"
        ) from exc
    return source_root


def _cross_bind_case_bundle(
    bundle: _case_bundle.Qwen27BCaseBundle,
    materials: object,
) -> None:
    rows = {
        row["case_id"]: row
        for row in bundle.manifest["cases"]
        if isinstance(row, Mapping)
    }
    if tuple(rows) != _case_bundle.CASE_IDS:
        raise Qwen27BFinalRouteError("final_route_case_bundle_order_invalid")
    verified = bundle.verified_case_bindings
    if not isinstance(verified, Mapping) or tuple(verified) != _case_bundle.CASE_IDS:
        raise Qwen27BFinalRouteError(
            "final_route_case_bundle_upstream_authority_missing"
        )
    material_rows = {
        row["case_id"]: row
        for row in materials.manifest["cases"]
        if isinstance(row, Mapping)
    }
    if tuple(material_rows) != _case_bundle.CASE_IDS:
        raise Qwen27BFinalRouteError(
            "final_route_case_loader_order_invalid"
        )
    for case_id in _case_bundle.CASE_IDS:
        payloads = materials.case_payloads[case_id]
        row = rows[case_id]
        for key, payload_key in (
            ("prompt", "prompt"),
            ("provider_input", "provider_input"),
        ):
            raw = payloads[payload_key]
            binding = row[key]
            if (
                not isinstance(binding, Mapping)
                or binding.get("byte_length") != len(raw)
                or binding.get("sha256") != _sha(raw)
            ):
                raise Qwen27BFinalRouteError(
                    f"final_route_{key}_cross_binding_invalid"
                )
        context = materials.case_inputs[case_id]["context"]
        guidance = materials.case_inputs[case_id]["guidance"]
        context_raw = _canonical(context.to_dict())
        guidance_raw = _canonical(guidance.to_dict())
        context_binding = {
            "schema_version": context.schema_version,
            "sha256": _sha(context_raw),
            "byte_length": len(context_raw),
        }
        if guidance.source_context_schema_version != context.schema_version:
            raise Qwen27BFinalRouteError(
                "final_route_guidance_source_context_schema_invalid"
            )
        expected_semantic_bindings = {
            "agent_context": context_binding,
            "retrieval_guidance": {
                "schema_version": guidance.schema_version,
                "sha256": _sha(guidance_raw),
                "byte_length": len(guidance_raw),
                "source_context": dict(context_binding),
            },
        }
        for key, expected in expected_semantic_bindings.items():
            if (
                material_rows[case_id].get(key) != expected
                or verified[case_id].get(key) != expected
            ):
                raise Qwen27BFinalRouteError(
                    f"final_route_{key}_cross_binding_invalid"
                )


def _raw_by_case(run_root: Path, run: Mapping[str, Any]) -> dict[str, bytes]:
    raw_by_case: dict[str, bytes] = {}
    for row in run["cases"]:
        case_id = row["case_id"]
        relative = row["raw_response"]["relative_path"]
        path = run_root.joinpath(*relative.split("/"))
        raw = path.read_bytes()
        if row["raw_response"] != {
            "relative_path": relative,
            "byte_length": len(raw),
            "sha256": _sha(raw),
        }:
            raise Qwen27BFinalRouteError("final_route_raw_binding_invalid")
        raw_by_case[case_id] = raw
    if tuple(raw_by_case) != _case_bundle.CASE_IDS:
        raise Qwen27BFinalRouteError("final_route_raw_case_order_invalid")
    return raw_by_case


def _build_semantic_closure_model_outcome(
    *,
    model_route: object,
    original_raw: ProviderRawResponse,
    closure: Qwen27bSemanticClosureResult,
    context: object,
    guidance: object,
    case_id: str,
    execution_result_sha256: str,
    frozen_g0_reference: object,
) -> tuple[object, object | None, ProviderRawResponse]:
    """Build the private A-07 compatibility object from a repaired candidate.

    Original Provider bytes remain the source identity. The canonical repaired
    candidate is an internal deterministic replay input bound by the closure
    receipt; it is never represented as the original Provider response.
    """

    closure.validate()
    source = {
        "kind": "trusted_remote_verified_execution_with_semantic_closure",
        "case_id": case_id,
        "execution_result_sha256": execution_result_sha256,
        "original_raw_response_sha256": original_raw.sha256,
        "original_raw_response_byte_length": len(original_raw.raw_bytes),
        "semantic_closure_report_sha256": closure.report.sha256(),
        "semantic_closure_decision": closure.report.decision,
        "original_candidate_sha256": closure.report.original_candidate_sha256,
        "repaired_candidate_sha256": closure.report.repaired_candidate_sha256,
    }
    if closure.report.decision == "fail_closed":
        body = {"source": source, "failure_code": closure.report.failure_code}
        outcome_id = "trusted-remote-live-model-outcome-" + _sha(_canonical(body))
        outcome = _live_route._LiveModelOutcome(
            outcome_id=outcome_id,
            disposition="fail_closed",
            failure=_live_route._LiveFailure(
                closure.report.failure_code
                or "semantic_closure_failed_closed"
            ),
            artifacts={
                key: None
                for key in (
                    "raw_response_sha256",
                    "raw_response_byte_length",
                    "model_semantic_candidate_sha256",
                    "assembled_page_id",
                    "assembly_report_id",
                    "assembly_report_sha256",
                    "assembled_page_spec_sha256",
                )
            },
            source_binding=source,
            frozen_g0_reference=frozen_g0_reference,
            token=_live_route._LIVE_TOKEN,
        )
        return outcome, None, original_raw

    repaired_candidate = closure.candidate
    if repaired_candidate is None:
        raise Qwen27BFinalRouteError(
            "final_route_semantic_closure_candidate_missing"
        )
    replay_raw = ProviderRawResponse.from_bytes(
        repaired_candidate.canonical_json_bytes()
    )
    try:
        assembled = model_route._FIXED_CANONICAL_ASSEMBLY_AUTHORITY(
            replay_raw,
            context,
            guidance,
        )
    except Exception as exc:
        raise Qwen27BFinalRouteError(
            "final_route_semantic_closure_assembly_failed"
        ) from exc
    report = assembled.report
    artifacts = {
        "raw_response_sha256": replay_raw.sha256,
        "raw_response_byte_length": len(replay_raw.raw_bytes),
        "model_semantic_candidate_sha256": assembled.candidate.sha256(),
        "assembled_page_id": assembled.page_spec.page_id,
        "assembly_report_id": report.report_id,
        "assembly_report_sha256": report.sha256(),
        "assembled_page_spec_sha256": report.assembled_page_spec_sha256,
    }
    source["closure_replay_sha256"] = replay_raw.sha256
    source["closure_replay_byte_length"] = len(replay_raw.raw_bytes)
    outcome_id = "trusted-remote-live-model-outcome-" + _sha(
        _canonical({"source": source, "artifacts": artifacts})
    )
    outcome = _live_route._LiveModelOutcome(
        outcome_id=outcome_id,
        disposition="scripted_fixture_assembled",
        failure=None,
        artifacts=artifacts,
        source_binding=source,
        frozen_g0_reference=frozen_g0_reference,
        token=_live_route._LIVE_TOKEN,
    )
    return outcome, assembled, replay_raw


def _effective_route_state(
    *,
    outcome: object,
    closure: Qwen27bSemanticClosureResult | None,
) -> dict[str, object]:
    repaired = closure is not None and closure.report.decision == "repaired"
    model_success = (outcome.status, outcome.delivery_source) in {
        ("first_pass_success", "model_first_pass_v1"),
        ("recovered_success", "model_repaired_v1"),
    }
    if repaired and model_success:
        return {
            "status": "recovered_success",
            "delivery_source": "model_repaired_v1",
            "g2_action": "model_repair",
            "repair_attempted": 1,
            "repair_kind": "deterministic_semantic_closure_v1",
        }
    return {
        "status": outcome.status,
        "delivery_source": outcome.delivery_source,
        "g2_action": outcome.g2_action,
        "repair_attempted": (
            1 if repaired else getattr(outcome, "repair_attempted", 0)
        ),
        "repair_kind": (
            "deterministic_semantic_closure_v1" if repaired else None
        ),
    }


def _closure_gate_record(
    *,
    case_id: str,
    variant: str,
    outcome: object,
    effective: Mapping[str, object],
    closure: Qwen27bSemanticClosureResult | None,
    execution_result_sha256: str,
) -> bytes:
    if closure is None or closure.report.decision != "repaired":
        return _live_route._gate_record(
            case_id,
            variant,
            outcome,
            execution_result_sha256,
        )
    return _canonical(
        {
            "schema_version": FINAL_ROUTE_CLOSURE_GATE_SCHEMA,
            "case_id": case_id,
            "source_kind": "trusted_remote_verified_execution",
            "execution_result_sha256": execution_result_sha256,
            "underlying_a07_semantics": variant,
            "underlying_a07_status": outcome.status,
            "underlying_a07_delivery_source": outcome.delivery_source,
            "underlying_a07_outcome_sha256": _sha(outcome.canonical_bytes()),
            "semantic_closure_report_sha256": closure.report.sha256(),
            "status": effective["status"],
            "delivery_source": effective["delivery_source"],
            "g2_action": effective["g2_action"],
            "repair_attempted": effective["repair_attempted"],
            "repair_kind": effective["repair_kind"],
            "retry_performed": False,
        }
    )
def _route_case(
    *,
    case_id: str,
    raw: bytes,
    inputs: Mapping[str, Any],
    execution_result_sha256: str,
) -> dict[str, Any]:
    import req2web_orchestration.model_route as model_route

    provider_raw = ProviderRawResponse.from_bytes(raw)
    parser_status = "passed"
    parser_failure = None
    try:
        candidate = parse_provider_raw_response(provider_raw)
    except Exception as exc:
        candidate = None
        parser_status = "failed"
        parser_failure = {
            "code": "semantic_parse_failed",
            "type": type(exc).__name__,
            "message": str(exc)[:300],
        }

    route_inputs = _live_route._route_inputs(inputs, case_id)
    closure = None
    routed_candidate = candidate
    route_provider_raw = provider_raw
    if candidate is None:
        model_outcome, assembled = _live_route._build_live_model_outcome(
            model_route,
            provider_raw,
            route_inputs["context"],
            route_inputs["guidance"],
            case_id,
            execution_result_sha256,
            route_inputs["frozen_g0_reference"],
        )
    else:
        closure = apply_qwen27b_deterministic_semantic_closure(
            case_id=case_id,
            candidate=candidate,
        )
        if closure.report.decision == "unchanged":
            model_outcome, assembled = _live_route._build_live_model_outcome(
                model_route,
                provider_raw,
                route_inputs["context"],
                route_inputs["guidance"],
                case_id,
                execution_result_sha256,
                route_inputs["frozen_g0_reference"],
            )
        else:
            routed_candidate = closure.candidate
            (
                model_outcome,
                assembled,
                route_provider_raw,
            ) = _build_semantic_closure_model_outcome(
                model_route=model_route,
                original_raw=provider_raw,
                closure=closure,
                context=route_inputs["context"],
                guidance=route_inputs["guidance"],
                case_id=case_id,
                execution_result_sha256=execution_result_sha256,
                frozen_g0_reference=route_inputs["frozen_g0_reference"],
            )
    fixture = _live_route._LiveRawFixture(
        route_provider_raw,
        case_id,
        execution_result_sha256,
        _live_route._LIVE_TOKEN,
    )
    common = {
        **route_inputs,
        "model_route_outcome": model_outcome,
        "execution_branch": "trusted_remote_verified_execution",
        "scripted_local_fixture": fixture,
        "case_id": case_id,
    }
    variant = "A-07a"
    field_report = (
        None
        if (
            assembled is None
            or (
                closure is not None
                and closure.report.repair_attempted == 1
            )
        )
        else model_route.create_tier_a_07b_field_gate_report(
            case_id=case_id,
            page_spec=assembled.page_spec,
            local_request=route_inputs["local_request"],
        )
    )
    if (
        field_report is not None
        and field_report.decision == "repair"
        and field_report.repair_eligible
    ):
        patch = model_route.TierA07bRepairPatch.create(
            report_id=field_report.report_id,
            report_sha256=field_report.sha256(),
            first_page_id=assembled.page_spec.page_id,
            first_page_spec_sha256=model_route._07b_page_binding(
                assembled.page_spec
            )["page_spec_sha256"],
            attempt_index=1,
            operations=((field_report.reported_field, field_report.expected),),
        )
        outcome = _live_route._a07b_runner(model_route)(
            object(),
            **common,
            field_gate_report=field_report.canonical_bytes(),
            repair_patch=patch.canonical_bytes(),
        )
        variant = "A-07b"
    else:
        outcome = _live_route._a07a_runner(model_route)(object(), **common)
    outcome.validate()

    assembler_status = "passed" if assembled is not None else "failed"
    semantic = None
    if routed_candidate is not None and assembled is not None:
        if routed_candidate.sha256() != assembled.candidate.sha256():
            raise Qwen27BFinalRouteError(
                "final_route_candidate_assembly_cross_binding_invalid"
            )
        semantic = evaluate_qwen27b_semantic_coverage_after_route(
            case_id=case_id,
            candidate=routed_candidate,
            route_outcome=outcome,
        ).to_dict()
    effective = _effective_route_state(outcome=outcome, closure=closure)
    model_route_pass = (
        effective["status"],
        effective["delivery_source"],
    ) in {
        ("first_pass_success", "model_first_pass_v1"),
        ("recovered_success", "model_repaired_v1"),
    }
    decision = (
        "recovery_pass"
        if model_route_pass
        and semantic is not None
        and semantic["decision"] == "pass"
        else "fail_closed"
    )
    gate_raw = _closure_gate_record(
        case_id=case_id,
        variant=variant,
        outcome=outcome,
        effective=effective,
        closure=closure,
        execution_result_sha256=execution_result_sha256,
    )
    package_manifest_raw = _live_route._manifest_bytes(
        model_route,
        outcome,
        route_inputs,
    )
    failures: list[str] = []
    if parser_failure is not None:
        failures.append(parser_failure["code"])
    if assembled is None:
        failures.append("canonical_assembly_failed")
    if not model_route_pass:
        failures.append("generic_route_model_success_not_reached")
    if semantic is not None:
        failures.extend(semantic["failure_codes"])
    if closure is not None and closure.report.failure_code is not None:
        failures.append(closure.report.failure_code)
    route_failure_code = getattr(outcome, "failure_code", None)
    if route_failure_code is None:
        failure = getattr(outcome, "failure", None)
        route_failure_code = None if failure is None else failure.code
    record = {
        "schema_version": FINAL_ROUTE_CASE_SCHEMA,
        "case_id": case_id,
        "source_raw": {
            "byte_length": len(raw),
            "sha256": _sha(raw),
        },
        "parser_status": parser_status,
        "assembler_status": assembler_status,
        "candidate_sha256": None if candidate is None else candidate.sha256(),
        "routed_candidate_sha256": (
            None if routed_candidate is None else routed_candidate.sha256()
        ),
        "semantic_closure": (
            None if closure is None else closure.report.to_dict()
        ),
        "assembled_page_spec_sha256": (
            None
            if assembled is None
            else assembled.report.assembled_page_spec_sha256
        ),
        "generic_route": {
            "a07_semantics": variant,
            "underlying_status": outcome.status,
            "underlying_delivery_source": outcome.delivery_source,
            "status": effective["status"],
            "delivery_source": effective["delivery_source"],
            "g1_package_purpose": outcome.g1_package_purpose,
            "g2_action": effective["g2_action"],
            "repair_attempted": effective["repair_attempted"],
            "repair_kind": effective["repair_kind"],
            "retry_performed": getattr(outcome, "retry_performed", False),
            "gate_status": getattr(
                outcome,
                "final_gate_status",
                getattr(outcome, "gate_status", "not_executed"),
            ),
            "consistency_status": outcome.consistency_status,
            "acceptance_status": outcome.acceptance_status,
            "fallback_reason": outcome.fallback_reason,
            "fallback_attempted": outcome.fallback_attempted,
            "fallback_succeeded": outcome.fallback_succeeded,
            "failure_code": route_failure_code,
            "outcome_sha256": _sha(outcome.canonical_bytes()),
            "gate_delivery_sha256": _sha(gate_raw),
            "result_package_manifest_sha256": _sha(package_manifest_raw),
        },
        "semantic_coverage": semantic,
        "failure_codes": sorted(set(failures)),
        "failure_detail": parser_failure,
        "decision": decision,
        "claims": {
            "development_recovery_pilot_only": True,
            "formal_quality": False,
            "h1": False,
            "browser_quality": False,
            "real_browser_executed": False,
            "formal_browser_success": False,
            "evidence_use": False,
            "original_model_raw_preserved": True,
            "semantic_closure_is_system_owned": (
                closure is not None and closure.report.decision == "repaired"
            ),
        },
    }
    return record


def evaluate_qwen27b_final_route(
    *,
    run_root: Path | str,
    predeploy_manifest: Path | str,
    predeploy_ready: Path | str,
    repository_archive: Path | str,
    repository_archive_manifest: Path | str,
    case_bundle_root: Path | str,
    selected_copy: str,
    work_root: Path | str,
) -> dict[str, Any]:
    """Replay the fixed route and return the sole final pilot decision record."""

    run_path = Path(run_root).resolve(strict=True)
    run = validate_qwen27b_recovery_return_against(
        run_path,
        predeploy_manifest,
        predeploy_ready,
        repository_archive,
        repository_archive_manifest,
        case_bundle_root,
        selected_copy,
    )
    bundle = _case_bundle.validate_qwen27b_case_bundle(
        case_bundle_root,
        repository_archive_path=repository_archive,
        repository_archive_manifest_path=repository_archive_manifest,
    )
    route_work = _prepare_work_root(work_root)
    source_root = _extract_archive_source_tree(
        repository_archive,
        repository_archive_manifest,
        route_work,
    )
    materials = build_fixed_trusted_remote_case_materials_v2(
        source_root,
        route_work,
    )
    _cross_bind_case_bundle(bundle, materials)
    raw_by_case = _raw_by_case(run_path, run)
    execution_result_sha256 = _sha(_canonical(run))
    cases = [
        _route_case(
            case_id=case_id,
            raw=raw_by_case[case_id],
            inputs=materials.case_inputs[case_id],
            execution_result_sha256=execution_result_sha256,
        )
        for case_id in _case_bundle.CASE_IDS
    ]
    body = {
        "schema_version": FINAL_ROUTE_SCHEMA,
        "record_id": "qwen35-27b-final-route-v2-" + "0" * 64,
        "source_run": {
            "run_id": run["run_id"],
            "run_manifest_relative_path": RUN_MANIFEST_FILENAME,
            "run_sha256": execution_result_sha256,
        },
        "case_ids": list(_case_bundle.CASE_IDS),
        "cases": cases,
        "overall_decision": (
            "recovery_pass"
            if all(case["decision"] == "recovery_pass" for case in cases)
            else "fail_closed"
        ),
        "claims": {
            "development_recovery_pilot_only": True,
            "formal_quality": False,
            "h1": False,
            "browser_quality": False,
            "real_browser_executed": False,
            "formal_browser_success": False,
            "evidence_use": False,
            "model_loaded_by_this_adapter": False,
            "provider_call_by_this_adapter": False,
            "retry_performed": False,
        },
    }
    identified = dict(body)
    identified["record_id"] = (
        "qwen35-27b-final-route-v2-" + _sha(_canonical(body))
    )
    return identified


__all__ = (
    "FINAL_ROUTE_CASE_SCHEMA",
    "FINAL_ROUTE_SCHEMA",
    "Qwen27BFinalRouteError",
    "evaluate_qwen27b_final_route",
)
