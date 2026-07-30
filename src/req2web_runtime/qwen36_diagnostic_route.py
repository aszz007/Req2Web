"""Final local route for the single Qwen3.6-27B diagnostic run.

The module reuses the accepted Qwen3.5-27B raw-to-PageSpec route unchanged.
Only the validated source-run/model identity and diagnostic record schemas differ.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from . import qwen27b_final_route as _accepted_route
from . import qwen27b_case_bundle as _case_bundle
from .autodl_trusted_remote_case_loader import (
    build_fixed_trusted_remote_case_materials_v2,
)
from .qwen36_diagnostic import (
    CASE_IDS,
    MODEL_REPOSITORY,
    MODEL_REVISION,
    RUN_MANIFEST_FILENAME,
    Qwen36DiagnosticActionBinding,
    Qwen36DiagnosticError,
    Qwen36DiagnosticPlan,
    Qwen36ModelInventory,
    validate_qwen36_diagnostic_result_against,
)

FINAL_ROUTE_SCHEMA = "req2web.runtime.qwen36_27b_diagnostic_final_route.v1"
FINAL_ROUTE_CASE_SCHEMA = (
    "req2web.runtime.qwen36_27b_diagnostic_final_route_case.v1"
)


class Qwen36DiagnosticRouteError(ValueError):
    """The stronger-checkpoint diagnostic route failed closed."""


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


def _evaluate_qwen36_diagnostic_route(
    *,
    run_root: Path | str,
    plan: Qwen36DiagnosticPlan | bytes | Path | str,
    action_binding: Qwen36DiagnosticActionBinding | bytes | Path | str,
    model_inventory: Qwen36ModelInventory | bytes | Path | str,
    runtime_inventory: bytes | Path | str,
    repository_archive: Path | str,
    repository_archive_manifest: Path | str,
    case_bundle_root: Path | str,
    work_root: Path | str,
) -> dict[str, Any]:
    """Replay the unchanged accepted route for a validated Qwen3.6 run."""

    run_path = Path(run_root).resolve(strict=True)
    run = validate_qwen36_diagnostic_result_against(
        run_path,
        plan,
        action_binding,
        model_inventory,
        runtime_inventory,
        repository_archive,
        repository_archive_manifest,
        case_bundle_root,
    )
    bundle = _case_bundle.validate_qwen27b_case_bundle(
        case_bundle_root,
        repository_archive_path=repository_archive,
        repository_archive_manifest_path=repository_archive_manifest,
    )
    route_work = _accepted_route._prepare_work_root(work_root)
    source_root = _accepted_route._extract_archive_source_tree(
        repository_archive,
        repository_archive_manifest,
        route_work,
    )
    materials = build_fixed_trusted_remote_case_materials_v2(
        source_root,
        route_work,
    )
    _accepted_route._cross_bind_case_bundle(bundle, materials)
    raw_by_case = _accepted_route._raw_by_case(run_path, run)
    execution_result_sha256 = _sha(_canonical(run))
    cases: list[dict[str, Any]] = []
    for case_id in CASE_IDS:
        case = copy.deepcopy(
            _accepted_route._route_case(
                case_id=case_id,
                raw=raw_by_case[case_id],
                inputs=materials.case_inputs[case_id],
                execution_result_sha256=execution_result_sha256,
            )
        )
        case["schema_version"] = FINAL_ROUTE_CASE_SCHEMA
        claims = dict(case["claims"])
        claims.pop("development_recovery_pilot_only", None)
        claims["stronger_checkpoint_diagnostic_only"] = True
        claims["lora_evidence"] = False
        case["claims"] = claims
        cases.append(case)
    body = {
        "schema_version": FINAL_ROUTE_SCHEMA,
        "record_id": "qwen36-27b-diagnostic-route-v1-" + "0" * 64,
        "source_run": {
            "run_id": run["run_id"],
            "run_manifest_relative_path": RUN_MANIFEST_FILENAME,
            "run_sha256": execution_result_sha256,
        },
        "model": {
            "repository": MODEL_REPOSITORY,
            "revision": MODEL_REVISION,
        },
        "case_ids": list(CASE_IDS),
        "cases": cases,
        "overall_decision": (
            "diagnostic_pass"
            if all(case["decision"] == "recovery_pass" for case in cases)
            else "fail_closed"
        ),
        "claims": {
            "stronger_checkpoint_diagnostic_only": True,
            "unchanged_current_contract": True,
            "formal_quality": False,
            "h1": False,
            "browser_quality": False,
            "evidence_use": False,
            "lora_evidence": False,
            "model_loaded_by_this_adapter": False,
            "provider_call_by_this_adapter": False,
            "retry_performed": False,
        },
    }
    body["record_id"] = (
        "qwen36-27b-diagnostic-route-v1-" + _sha(_canonical(body))
    )
    return body


def evaluate_qwen36_diagnostic_route(
    *,
    run_root: Path | str,
    plan: Qwen36DiagnosticPlan | bytes | Path | str,
    action_binding: Qwen36DiagnosticActionBinding | bytes | Path | str,
    model_inventory: Qwen36ModelInventory | bytes | Path | str,
    runtime_inventory: bytes | Path | str,
    repository_archive: Path | str,
    repository_archive_manifest: Path | str,
    case_bundle_root: Path | str,
    work_root: Path | str,
) -> dict[str, Any]:
    """Fail-closed public wrapper over the unchanged accepted route."""

    try:
        return _evaluate_qwen36_diagnostic_route(
            run_root=run_root,
            plan=plan,
            action_binding=action_binding,
            model_inventory=model_inventory,
            runtime_inventory=runtime_inventory,
            repository_archive=repository_archive,
            repository_archive_manifest=repository_archive_manifest,
            case_bundle_root=case_bundle_root,
            work_root=work_root,
        )
    except Qwen36DiagnosticRouteError:
        raise
    except (OSError, ValueError, Qwen36DiagnosticError) as exc:
        raise Qwen36DiagnosticRouteError(
            str(exc) or type(exc).__name__
        ) from exc


__all__ = (
    "FINAL_ROUTE_CASE_SCHEMA",
    "FINAL_ROUTE_SCHEMA",
    "Qwen36DiagnosticRouteError",
    "evaluate_qwen36_diagnostic_route",
)
