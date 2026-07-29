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
from .qwen27b_semantic_gate import (
    evaluate_qwen27b_semantic_coverage_after_route,
)

FINAL_ROUTE_SCHEMA = "req2web.runtime.qwen35_27b_final_route.v1"
FINAL_ROUTE_CASE_SCHEMA = "req2web.runtime.qwen35_27b_final_route_case.v1"


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
    fixture = _live_route._LiveRawFixture(
        provider_raw,
        case_id,
        execution_result_sha256,
        _live_route._LIVE_TOKEN,
    )
    model_outcome, assembled = _live_route._build_live_model_outcome(
        model_route,
        provider_raw,
        route_inputs["context"],
        route_inputs["guidance"],
        case_id,
        execution_result_sha256,
        route_inputs["frozen_g0_reference"],
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
        if assembled is None
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
    if candidate is not None and assembled is not None:
        if candidate.sha256() != assembled.candidate.sha256():
            raise Qwen27BFinalRouteError(
                "final_route_candidate_assembly_cross_binding_invalid"
            )
        semantic = evaluate_qwen27b_semantic_coverage_after_route(
            case_id=case_id,
            candidate=candidate,
            route_outcome=outcome,
        ).to_dict()
    model_route_pass = (
        (outcome.status, outcome.delivery_source)
        in {
            ("first_pass_success", "model_first_pass_v1"),
            ("recovered_success", "model_repaired_v1"),
        }
    )
    decision = (
        "recovery_pass"
        if model_route_pass
        and semantic is not None
        and semantic["decision"] == "pass"
        else "fail_closed"
    )
    gate_raw = _live_route._gate_record(
        case_id,
        variant,
        outcome,
        execution_result_sha256,
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
        "assembled_page_spec_sha256": (
            None
            if assembled is None
            else assembled.report.assembled_page_spec_sha256
        ),
        "generic_route": {
            "a07_semantics": variant,
            "status": outcome.status,
            "delivery_source": outcome.delivery_source,
            "g1_package_purpose": outcome.g1_package_purpose,
            "g2_action": outcome.g2_action,
            "repair_attempted": getattr(outcome, "repair_attempted", 0),
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
            "evidence_use": False,
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
        "record_id": "qwen35-27b-final-route-v1-" + "0" * 64,
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
            "evidence_use": False,
            "model_loaded_by_this_adapter": False,
            "provider_call_by_this_adapter": False,
            "retry_performed": False,
        },
    }
    identified = dict(body)
    identified["record_id"] = (
        "qwen35-27b-final-route-v1-" + _sha(_canonical(body))
    )
    return identified


__all__ = (
    "FINAL_ROUTE_CASE_SCHEMA",
    "FINAL_ROUTE_SCHEMA",
    "Qwen27BFinalRouteError",
    "evaluate_qwen27b_final_route",
)
