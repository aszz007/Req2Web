"""Fixed loader for the two frozen non-H1 trusted-remote case inputs."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from collections.abc import Mapping
from pathlib import Path

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain
from req2web_evaluation.frozen_g0_reference import freeze_verified_g0_package_reference
from req2web_faults.fallback_delivery import freeze_g0_fallback_package
from req2web_generation import (
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_orchestration.model_route import SCRIPTED_ACCEPTANCE_PASS_KEY, ScriptedAcceptanceFixture
from req2web_provider.d17_audit import create_d17_path3_pre_invocation_audit_record
from req2web_provider.d17_input_view import select_d17_path3_provider_input
from req2web_provider.d17_manifest import D17Path3TierAManifest
from req2web_provider.d17_serializer import serialize_d17_path3_local_request
from req2web_provider.local_qwen_provider import prepare_local_qwen_provider_interface
from req2web_rag import RetrieverConfig, create_retriever
from req2web_rag.corpus import ROLE_ORDER

from . import autodl_trusted_remote_executor as _executor

CASE_INPUTS_MANIFEST_SCHEMA = "req2web.runtime.trusted_remote_fixed_case_inputs.v2"
_REQUIRED_CASE_IDS = _executor.REQUIRED_CASE_IDS
_CASE_SET_RELATIVE_PATH = "fixtures/stage3_path3_compatibility_cases_v1.json"
_TRACKED_RECORD_RELATIVE_PATH = "fixtures/stage3_path3_external_action_gate_preparation_v1.json"
_RAG_INDEX_RELATIVE_PATH = "data/processed/rag"


class TrustedRemoteCaseLoaderError(ValueError):
    pass


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _load_json(path, label):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustedRemoteCaseLoaderError(f"{label}_invalid") from exc
    if not isinstance(value, dict):
        raise TrustedRemoteCaseLoaderError(f"{label}_invalid")
    return value


def _binding_matches(binding, raw):
    return binding == {"id": binding["id"], "sha256": _sha(raw), "byte_length": len(raw)}


def _case_set(repository_root):
    root = Path(repository_root).resolve(strict=True)
    case_path = root / _CASE_SET_RELATIVE_PATH
    record_path = root / _TRACKED_RECORD_RELATIVE_PATH
    cases_payload = _load_json(case_path, "fixed_case_set")
    tracked = _load_json(record_path, "tracked_preparation_record")
    raw = _dumps(cases_payload)
    if tracked.get("case_set", {}).get("sha256") != _sha(raw) or tracked.get("case_set", {}).get("byte_length") != len(raw):
        raise TrustedRemoteCaseLoaderError("fixed_case_set_tracked_binding_invalid")
    cases = cases_payload.get("cases")
    if not isinstance(cases, list) or tuple(row.get("case_id") for row in cases if isinstance(row, Mapping)) != _REQUIRED_CASE_IDS:
        raise TrustedRemoteCaseLoaderError("fixed_case_coverage_invalid")
    if tracked.get("inherited_boundary") != {"execution_path": "path_3", "non_h1": True, "frozen_regression_cases_used": False, "h1_or_gold_accessed": False, "reference_only_assets_used": False, "third_party_payload_used": False}:
        raise TrustedRemoteCaseLoaderError("fixed_case_boundary_invalid")
    tracked_cases = tracked.get("cases")
    if not isinstance(tracked_cases, list) or tuple(row.get("case_id") for row in tracked_cases if isinstance(row, Mapping)) != _REQUIRED_CASE_IDS:
        raise TrustedRemoteCaseLoaderError("tracked_case_coverage_invalid")
    return tuple(cases), {row["case_id"]: row for row in tracked_cases}


def _validate_binding(record, key, raw):
    binding = record.get(key)
    if not isinstance(binding, Mapping) or binding.get("sha256") != _sha(raw) or binding.get("byte_length") != len(raw):
        raise TrustedRemoteCaseLoaderError(f"tracked_{key}_binding_invalid")


class FixedTrustedRemoteCaseInputsV2:
    __slots__ = ("case_inputs", "case_payloads", "manifest")

    def __init__(self, case_inputs, case_payloads, manifest):
        self.case_inputs = case_inputs
        self.case_payloads = case_payloads
        self.manifest = manifest


def build_fixed_trusted_remote_case_materials_v2(repository_root, project_temp_root):
    repository_root = Path(repository_root).resolve(strict=True)
    project_temp_root = Path(project_temp_root).resolve(strict=True)
    marker = project_temp_root / ".req2web_project_temp_root.json"
    if not marker.is_file():
        raise TrustedRemoteCaseLoaderError("project_temp_root_marker_missing")
    cases, tracked = _case_set(repository_root)
    index_dir = (repository_root / _RAG_INDEX_RELATIVE_PATH).resolve(strict=True)
    retriever = create_retriever(RetrieverConfig(index_dir=index_dir, backend="tfidf"))
    chain = MinimalAgentChain(DeterministicRequirementProvider(), retriever, top_k_per_role=2)
    case_inputs = {}
    case_payloads = {}
    manifest_rows = []
    for case in cases:
        case_id = case["case_id"]
        case_root = project_temp_root / "case-loader" / case_id
        case_root.mkdir(parents=True, exist_ok=False)
        context = chain.run(case["requirement"], target_device=case["target_device"], task_type=case["task_type"], constraints=case["constraints"])
        guidance = RetrievalGuidanceBuilder().build(context)
        baseline = PageSpecBuilder().build(context)
        guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
        ablations = {role: RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,)) for role in ROLE_ORDER}
        render_work = case_root / "g0-render"
        render = DeterministicPageRenderer().render(guided.page_spec, render_work)
        consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
        influence = RetrievalInfluenceChecker().check(context, guidance, baseline, guided, ablations, render)
        package = DeterministicRetrievalEnhancedResultPackager().package(context, guidance, guided, guided.page_spec, render, consistency, influence, case_root / "frozen_g0_v2_package")
        package.validate()
        shutil.rmtree(render_work)
        reference = freeze_verified_g0_package_reference(package, context, guidance)
        reference.validate_against(package, context, guidance)
        manifest = D17Path3TierAManifest.create(structural_signal_names=tuple(case["structural_signal_names"]))
        selected = select_d17_path3_provider_input(context, manifest, original_requirement_source_class=case["source_class"])
        local_request = serialize_d17_path3_local_request(context, selected, manifest)
        audit = create_d17_path3_pre_invocation_audit_record(context, manifest, selected, local_request)
        preparation = prepare_local_qwen_provider_interface(context, manifest, selected, local_request, audit)
        selected.validate_against(context, manifest)
        local_request.validate_against(context, manifest)
        audit.validate_against(context, manifest, selected, local_request)
        preparation.validate_against(context, manifest, selected, local_request, audit)
        raws = {
            "complete_local_context": _dumps(context.to_dict()),
            "retrieval_guidance": _dumps(guidance.to_dict()),
            "d17_manifest": manifest.canonical_bytes(),
            "provider_visible_payload": selected.provider_visible_input.canonical_bytes(),
            "selection_record": selected.selection_record.canonical_bytes(),
            "input_view_artifact": local_request.input_view_artifact.canonical_bytes(),
            "prompt_artifact": local_request.prompt_artifact.canonical_bytes(),
            "config_artifact": local_request.config_artifact.canonical_bytes(),
            "local_request": local_request.canonical_bytes(),
            "pre_invocation_audit": audit.canonical_bytes(),
            "preparation_record": preparation.canonical_bytes(),
            "same_case_frozen_g0_reference": _dumps(reference.to_dict()),
        }
        for key, raw in raws.items():
            _validate_binding(tracked[case_id], key, raw)
        provider_raw = raws["provider_visible_payload"]
        executor_prompt = _dumps({"schema_version": _executor.PROMPT_ARTIFACT_SCHEMA, "prompt_text": local_request.prompt_artifact.prompt_text, "provider_input_mode": "append_exact_provider_visible_input_utf8"})
        fallback_snapshot = case_root / "fallback_snapshot"
        fallback_record = freeze_g0_fallback_package(case_id, package, fallback_snapshot)
        case_inputs[case_id] = {
            "frozen_g0_reference": reference,
            "package": package,
            "context": context,
            "guidance": guidance,
            "manifest": manifest,
            "selected": selected,
            "local_request": local_request,
            "pre_invocation_audit": audit,
            "local_qwen_preparation": preparation,
            "fallback_record": fallback_record,
            "fallback_snapshot_dir": fallback_snapshot,
            "render_output_dir": case_root / "render",
            "model_package_output_dir": case_root / "model_package",
            "fallback_output_dir": case_root / "fallback",
            "scripted_acceptance_fixture": ScriptedAcceptanceFixture.create(SCRIPTED_ACCEPTANCE_PASS_KEY),
        }
        case_payloads[case_id] = {"provider_input": provider_raw, "prompt": executor_prompt}
        manifest_rows.append({"case_id": case_id, "provider_input_id": selected.selection_record.input_view_id, "provider_input_sha256": _sha(provider_raw), "provider_input_byte_length": len(provider_raw), "prompt_sha256": _sha(executor_prompt), "prompt_byte_length": len(executor_prompt), "local_request_sha256": _sha(raws["local_request"]), "frozen_g0_reference_sha256": _sha(raws["same_case_frozen_g0_reference"])})
    manifest = {"schema_version": CASE_INPUTS_MANIFEST_SCHEMA, "case_ids": list(_REQUIRED_CASE_IDS), "cases": manifest_rows, "source": {"case_set": _CASE_SET_RELATIVE_PATH, "tracked_preparation_record": _TRACKED_RECORD_RELATIVE_PATH, "rag_index": _RAG_INDEX_RELATIVE_PATH}, "boundary": {"non_h1": True, "reference_only": False, "model_repository_enumeration": False}}
    (project_temp_root / "case-loader" / "case_inputs_manifest.json").write_bytes(_dumps(manifest))
    return FixedTrustedRemoteCaseInputsV2(case_inputs, case_payloads, manifest)


def bind_fixed_trusted_remote_case_inputs_v2(materials, execution_package):
    if type(materials) is not FixedTrustedRemoteCaseInputsV2:
        raise TrustedRemoteCaseLoaderError("fixed_case_materials_exact_type_required")
    package = _executor._replay_package(execution_package)
    package_cases = package.to_dict()["cases"]
    for row in package_cases:
        case_id = row["case_id"]
        payload = materials.case_payloads[case_id]
        provider_raw = payload["provider_input"]
        prompt_raw = payload["prompt"]
        expected_provider = {"input_id": materials.manifest["cases"][list(_REQUIRED_CASE_IDS).index(case_id)]["provider_input_id"], "sha256": _sha(provider_raw), "byte_length": len(provider_raw)}
        if row["provider_input"] != expected_provider or row["provider_input_file"]["sha256"] != expected_provider["sha256"] or row["provider_input_file"]["byte_length"] != expected_provider["byte_length"]:
            raise TrustedRemoteCaseLoaderError("execution_package_provider_input_binding_invalid")
        if row["prompt_file"]["sha256"] != _sha(prompt_raw) or row["prompt_file"]["byte_length"] != len(prompt_raw):
            raise TrustedRemoteCaseLoaderError("execution_package_prompt_binding_invalid")
        g0 = materials.case_inputs[case_id]["frozen_g0_reference"]
        if row["frozen_g0"]["package_id"] != g0.package_id or row["frozen_g0"]["sha256"] != _sha(_dumps(g0.to_dict())):
            raise TrustedRemoteCaseLoaderError("execution_package_g0_binding_invalid")
    return materials


def load_fixed_trusted_remote_case_inputs_v2(repository_root, execution_package, project_temp_root):
    return bind_fixed_trusted_remote_case_inputs_v2(build_fixed_trusted_remote_case_materials_v2(repository_root, project_temp_root), execution_package)


__all__ = ["CASE_INPUTS_MANIFEST_SCHEMA", "FixedTrustedRemoteCaseInputsV2", "TrustedRemoteCaseLoaderError", "bind_fixed_trusted_remote_case_inputs_v2", "build_fixed_trusted_remote_case_materials_v2", "load_fixed_trusted_remote_case_inputs_v2"]
