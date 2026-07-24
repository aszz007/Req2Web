from __future__ import annotations

"""Prepare a local-only Stage 3 Path 3 external-action gate bundle.

This command never downloads or loads a model, invokes a Provider, uses
credentials, transfers project data, creates a paid resource, or accesses H1.
It only freezes two synthetic development cases through the existing local
D17 preparation and deterministic guided G0 authorities.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain  # noqa: E402
from req2web_evaluation.frozen_g0_reference import (  # noqa: E402
    freeze_verified_g0_package_reference,
)
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_provider.d17_audit import (  # noqa: E402
    create_d17_path3_pre_invocation_audit_record,
)
from req2web_provider.d17_input_view import (  # noqa: E402
    select_d17_path3_provider_input,
)
from req2web_provider.d17_manifest import D17Path3TierAManifest  # noqa: E402
from req2web_provider.d17_serializer import (  # noqa: E402
    serialize_d17_path3_local_request,
)
from req2web_provider.local_qwen_provider import (  # noqa: E402
    prepare_local_qwen_provider_interface,
)
from req2web_rag import RetrieverConfig, create_retriever  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_runtime.tier_b_readiness import (  # noqa: E402
    create_tier_b_manager_run_plan,
)


CASE_SET_SCHEMA_VERSION = "req2web.stage3.path3.compatibility_case_set.v1"
PREPARATION_SCHEMA_VERSION = "req2web.stage3.d17.external_action_gate_preparation.v1"
PREPARATION_STATUS = "local_artifact_chain_prepared_no_action"
PURPOSE = "development_non_h1_compatibility_only"
DATASET_MEMBERSHIP = "not_a_dataset_not_regression_not_holdout_not_training"
_CAPTURED_CASE_SET_CANONICAL_SHA256 = "163365a761cb9313b8de933caace58e616221063c7d25bca379b452874b17dae"
_CAPTURED_CASE_SET_PATH = (ROOT / "fixtures" / "stage3_path3_compatibility_cases_v1.json").resolve(strict=False)
_CAPTURED_INDEX_DIR = (ROOT / "data" / "processed" / "rag").resolve(strict=False)
_TRACKED_PREPARATION_RECORD = (ROOT / "fixtures" / "stage3_path3_external_action_gate_preparation_v1.json").resolve(strict=False)
_SOURCE_CLASS = "synthetic"
_CASE_KEYS = {
    "case_id",
    "source_class",
    "run_order",
    "requirement",
    "target_device",
    "task_type",
    "constraints",
    "structural_signal_names",
}
_CASE_ID = re.compile(r"^[a-z][a-z0-9-]{2,95}$")
_SIGNAL_NAME = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_NO_ACTION_FLAGS = {
    "external_action_gate_eligible": False,
    "external_action_authorized": False,
    "external_egress_allowed": False,
    "model_downloaded": False,
    "model_loaded": False,
    "provider_invoked": False,
    "project_data_transferred": False,
    "real_credentials_used": False,
    "paid_resource_created": False,
    "run_occurred": False,
    "h1_allowed": False,
    "formal_quality_allowed": False,
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("case set must be a JSON object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    if "\ufffd" in value or not any(character.isalnum() for character in value):
        raise ValueError(f"{name} must contain semantic alphanumeric text")
    return value


def _string_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list")
    result = tuple(_text(item, name) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must be unique")
    return result


def _load_frozen_regression_cases(path: Path) -> tuple[Mapping[str, Any], ...]:
    payload = _load_json(path)
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("frozen regression case set is invalid")
    result: list[Mapping[str, Any]] = []
    for item in cases:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("case_id"), str)
            or not isinstance(item.get("requirement"), str)
            or not isinstance(item.get("constraints"), list)
            or not all(isinstance(value, str) for value in item["constraints"])
        ):
            raise ValueError("frozen regression case set is invalid")
        result.append(item)
    return tuple(result)


def _validate_captured_case_set_identity(payload: Mapping[str, Any]) -> bytes:
    raw = _canonical_bytes(payload)
    if _sha256(raw) != _CAPTURED_CASE_SET_CANONICAL_SHA256:
        raise ValueError("captured compatibility case set identity is invalid")
    return raw


def _validated_cases(
    payload: Mapping[str, Any],
    frozen_cases: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    if set(payload) != {
        "schema_version",
        "case_set_id",
        "purpose",
        "dataset_membership",
        "cases",
    }:
        raise ValueError("case set keys are invalid")
    if payload["schema_version"] != CASE_SET_SCHEMA_VERSION:
        raise ValueError("case set schema version is invalid")
    _text(payload["case_set_id"], "case_set_id")
    if payload["purpose"] != PURPOSE or payload["dataset_membership"] != DATASET_MEMBERSHIP:
        raise ValueError("case set scope is invalid")
    cases = payload["cases"]
    if not isinstance(cases, list) or len(cases) != 2:
        raise ValueError("exactly two compatibility cases are required")
    frozen_case_ids = {item["case_id"] for item in frozen_cases}
    frozen_requirements = {item["requirement"] for item in frozen_cases}
    validated: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for expected_order, raw in enumerate(cases, start=1):
        if not isinstance(raw, dict) or set(raw) != _CASE_KEYS:
            raise ValueError("compatibility case keys are invalid")
        case_id = _text(raw["case_id"], "case_id")
        if _CASE_ID.fullmatch(case_id) is None or case_id in seen or case_id in frozen_case_ids:
            raise ValueError("compatibility case id is invalid or overlaps the frozen regression set")
        seen.add(case_id)
        if raw["source_class"] != _SOURCE_CLASS or raw["run_order"] != expected_order:
            raise ValueError("compatibility source class or run order is invalid")
        requirement = _text(raw["requirement"], "requirement")
        if requirement in frozen_requirements:
            raise ValueError("compatibility requirement copies the frozen regression set")
        if raw["target_device"] not in {"mobile", "tablet", "desktop", "web", "responsive_web"}:
            raise ValueError("target_device is invalid")
        _text(raw["task_type"], "task_type")
        _string_list(raw["constraints"], "constraints")
        signals = _string_list(raw["structural_signal_names"], "structural_signal_names")
        if any(_SIGNAL_NAME.fullmatch(item) is None for item in signals):
            raise ValueError("structural signal name is invalid")
        validated.append(raw)
    return tuple(validated)


def _binding(identity: str, raw: bytes) -> dict[str, object]:
    return {"id": identity, "sha256": _sha256(raw), "byte_length": len(raw)}


def _write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _completion_marker_bytes(preparation_record_raw: bytes) -> bytes:
    return _canonical_bytes({
        "schema_version": "req2web.stage3.d17.external_action_gate_preparation.complete.v1",
        "status": "complete_no_action",
        "preparation_record_sha256": _sha256(preparation_record_raw),
        "preparation_record_byte_length": len(preparation_record_raw),
    })


def _validate_record_and_marker(record_raw: bytes, marker_raw: bytes) -> None:
    expected_record = _TRACKED_PREPARATION_RECORD.read_bytes()
    if record_raw != expected_record:
        raise ValueError("generated preparation record does not match the tracked authority")
    expected_marker = _completion_marker_bytes(record_raw)
    if marker_raw != expected_marker:
        raise ValueError("completion marker does not canonically bind the tracked preparation record")


def _artifact_record(case: Mapping[str, Any], chain: MinimalAgentChain, case_root: Path) -> dict[str, object]:
    case_root.mkdir(parents=True, exist_ok=False)
    context = chain.run(
        case["requirement"],
        target_device=case["target_device"],
        task_type=case["task_type"],
        constraints=case["constraints"],
    )
    guidance = RetrievalGuidanceBuilder().build(context)
    baseline = PageSpecBuilder().build(context)
    guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
    ablations = {
        role: RetrievalGuidedPageSpecBuilder().build(
            context, guidance, disabled_roles=(role,)
        )
        for role in ROLE_ORDER
    }

    render_root = case_root / "render_work"
    render = DeterministicPageRenderer().render(guided.page_spec, render_root)
    consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
    influence = RetrievalInfluenceChecker().check(
        context, guidance, baseline, guided, ablations, render
    )
    package = DeterministicRetrievalEnhancedResultPackager().package(
        context,
        guidance,
        guided,
        guided.page_spec,
        render,
        consistency,
        influence,
        case_root / "frozen_g0_v2_package",
    )
    package.validate()
    shutil.rmtree(render_root)
    reference = freeze_verified_g0_package_reference(package, context, guidance)
    reference.validate_against(package, context, guidance)

    manifest = D17Path3TierAManifest.create(
        structural_signal_names=tuple(case["structural_signal_names"])
    )
    selected = select_d17_path3_provider_input(
        context,
        manifest,
        original_requirement_source_class=case["source_class"],
    )
    request = serialize_d17_path3_local_request(context, selected, manifest)
    audit = create_d17_path3_pre_invocation_audit_record(
        context, manifest, selected, request
    )
    preparation = prepare_local_qwen_provider_interface(
        context, manifest, selected, request, audit
    )
    selected.validate_against(context, manifest)
    request.validate_against(context, manifest)
    audit.validate_against(context, manifest, selected, request)
    preparation.validate_against(context, manifest, selected, request, audit)

    context_raw = _canonical_bytes(context.to_dict())
    guidance_raw = _canonical_bytes(guidance.to_dict())
    manifest_raw = manifest.canonical_bytes()
    payload_raw = selected.provider_visible_input.canonical_bytes()
    selection_raw = selected.selection_record.canonical_bytes()
    input_artifact_raw = request.input_view_artifact.canonical_bytes()
    prompt_raw = request.prompt_artifact.canonical_bytes()
    config_raw = request.config_artifact.canonical_bytes()
    request_raw = request.canonical_bytes()
    audit_raw = audit.canonical_bytes()
    preparation_raw = preparation.canonical_bytes()
    reference_raw = _canonical_bytes(reference.to_dict())

    _write(case_root / "local_only" / "agent_context.json", context_raw)
    _write(case_root / "local_only" / "retrieval_guidance.json", guidance_raw)
    _write(case_root / "local_only" / "d17_manifest.json", manifest_raw)
    _write(case_root / "provider_visible" / "input_view.json", payload_raw)
    _write(case_root / "local_only" / "selection_record.json", selection_raw)
    _write(case_root / "local_only" / "input_view_artifact.json", input_artifact_raw)
    _write(case_root / "local_only" / "prompt_artifact.json", prompt_raw)
    _write(case_root / "local_only" / "config_artifact.json", config_raw)
    _write(case_root / "local_only" / "local_request.json", request_raw)
    _write(case_root / "local_only" / "pre_invocation_audit.json", audit_raw)
    _write(case_root / "local_only" / "preparation_record.json", preparation_raw)
    _write(case_root / "local_only" / "frozen_g0_reference.json", reference_raw)

    fixture_raw = _canonical_bytes(case)
    return {
        "case_id": case["case_id"],
        "source_class": case["source_class"],
        "run_order": case["run_order"],
        "fixture": _binding(case["case_id"], fixture_raw),
        "complete_local_context": _binding(reference.context_id, context_raw),
        "retrieval_guidance": _binding(guidance.guidance_bundle_id, guidance_raw),
        "d17_manifest": _binding(manifest.manifest_id, manifest_raw),
        "field_policy": {
            "schema_version": manifest.field_policy.schema_version,
            "id": manifest.field_policy.policy_identity,
            "sha256": manifest.field_policy.approved_snapshot_sha256(),
        },
        "provider_visible_payload": _binding(
            selected.selection_record.input_view_id, payload_raw
        ),
        "selection_record": _binding(
            selected.selection_record.selection_record_id, selection_raw
        ),
        "input_view_artifact": _binding(
            request.input_view_artifact.artifact_id, input_artifact_raw
        ),
        "prompt_artifact": _binding(request.prompt_artifact.artifact_id, prompt_raw),
        "config_artifact": _binding(request.config_artifact.artifact_id, config_raw),
        "local_request": _binding(request.artifact_id, request_raw),
        "pre_invocation_audit": _binding(audit.audit_record_id, audit_raw),
        "preparation_record": _binding(
            preparation.preparation_record_id, preparation_raw
        ),
        "exclusion_scan": {
            "status": audit.exclusion_declaration.declaration_status,
            "prohibited_category_count": len(
                audit.exclusion_declaration.prohibited_data_categories
            ),
        },
        "same_case_frozen_g0_reference": {
            **_binding(reference.reference_id, reference_raw),
            "package_id": reference.package_id,
            "page_id": reference.page_id,
            "package_manifest_sha256": reference.package_manifest_sha256,
            "inventory_tree_sha256": reference.inventory_tree_sha256,
            "inventory_file_count": len(reference.inventory),
        },
        "gate_state": dict(_NO_ACTION_FLAGS),
    }


def prepare(case_set_path: Path, output_root: Path, index_dir: Path) -> dict[str, object]:
    case_set_path = case_set_path.resolve(strict=True)
    index_dir = index_dir.resolve(strict=True)
    output_root = output_root.resolve(strict=False)
    outputs_root = (ROOT / "outputs").resolve(strict=True)
    if case_set_path != _CAPTURED_CASE_SET_PATH:
        raise ValueError("only the captured compatibility case set is allowed")
    if index_dir != _CAPTURED_INDEX_DIR:
        raise ValueError("only the captured local RAG index is allowed")
    if output_root.parent != outputs_root:
        raise ValueError("output_root must be a direct child of the repository outputs directory")
    if output_root.exists():
        raise ValueError("output_root already exists; refusing to overwrite")

    payload = _load_json(case_set_path)
    case_set_raw = _validate_captured_case_set_identity(payload)
    frozen_cases = _load_frozen_regression_cases(
        (ROOT / "fixtures" / "demo_v2_regression_cases_v1.json").resolve(strict=True)
    )
    cases = _validated_cases(payload, frozen_cases)
    staging_root = outputs_root / f".{output_root.name}.staging-{uuid.uuid4().hex}"
    staging_root.mkdir(parents=False, exist_ok=False)
    try:
        plan = create_tier_b_manager_run_plan()
        plan_raw = plan.canonical_bytes()
        _write(staging_root / "manager_run_plan.json", plan_raw)
        retriever = create_retriever(
            RetrieverConfig(index_dir=index_dir, backend="tfidf")
        )
        chain = MinimalAgentChain(
            DeterministicRequirementProvider(), retriever, top_k_per_role=2
        )
        records = [
            _artifact_record(case, chain, staging_root / "cases" / case["case_id"])
            for case in cases
        ]
        result = {
            "schema_version": PREPARATION_SCHEMA_VERSION,
            "status": PREPARATION_STATUS,
            "purpose": PURPOSE,
            "dataset_membership": DATASET_MEMBERSHIP,
            "case_set": _binding(payload["case_set_id"], case_set_raw),
            "manager_run_plan": _binding(plan.plan_id, plan_raw),
            "cases": records,
            "inherited_boundary": {
                "execution_path": "path_3",
                "non_h1": True,
                "frozen_regression_cases_used": False,
                "h1_or_gold_accessed": False,
                "reference_only_assets_used": False,
                "third_party_payload_used": False,
            },
            "gate_state": dict(_NO_ACTION_FLAGS),
        }
        raw = _canonical_bytes(result)
        _write(staging_root / "external_action_gate_preparation_record.json", raw)
        replay = json.loads(raw.decode("utf-8"))
        if replay != result or _canonical_bytes(replay) != raw:
            raise ValueError("preparation record canonical replay failed")
        completion = _completion_marker_bytes(raw)
        _validate_record_and_marker(raw, completion)
        marker_path = staging_root / "completion_marker.json"
        _write(marker_path, completion)
        _validate_record_and_marker(raw, marker_path.read_bytes())
        staging_root.replace(output_root)
        return result
    except Exception:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        raise


def validate_output_against_tracked_record(output_root: Path) -> None:
    output_root = output_root.resolve(strict=True)
    outputs_root = (ROOT / "outputs").resolve(strict=True)
    if output_root.parent != outputs_root:
        raise ValueError("validated output root must be a direct child of repository outputs")
    generated = (output_root / "external_action_gate_preparation_record.json").read_bytes()
    marker = (output_root / "completion_marker.json").read_bytes()
    _validate_record_and_marker(generated, marker)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare a local-only Path 3 external-action gate bundle without model or external actions."
    )
    parser.add_argument(
        "--case-set",
        type=Path,
        default=ROOT / "fixtures" / "stage3_path3_compatibility_cases_v1.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs" / "stage3_d17_external_action_gate_prep_v1",
    )
    parser.add_argument(
        "--index-dir", type=Path, default=ROOT / "data" / "processed" / "rag"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = prepare(args.case_set, args.output_root, args.index_dir)
        validate_output_against_tracked_record(args.output_root)
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    summary = {
        "status": result["status"],
        "case_set_id": result["case_set"]["id"],
        "case_ids": [item["case_id"] for item in result["cases"]],
        "manager_run_plan_id": result["manager_run_plan"]["id"],
        "external_action_authorized": False,
        "output_root": args.output_root.resolve(strict=True).as_posix(),
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())