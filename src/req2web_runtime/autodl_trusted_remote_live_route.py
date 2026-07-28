"""Source-exclusive live route adapter for the trusted-remote two-case run.

This adapter accepts only the non-serializable verified execution handle created
by the fixed parent/worker path.  It reuses the captured A-07a/A-07b authorities
without changing their accepted public orchestrators or claiming that the real
source was a scripted fixture.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from . import autodl_trusted_remote_executor as _executor

LIVE_ROUTE_BUNDLE_SCHEMA = "req2web.runtime.trusted_remote_live_route_bundle.v2"
LIVE_GATE_RECORD_SCHEMA = "req2web.runtime.trusted_remote_live_gate_delivery.v2"
REQUIRED_CASE_IDS = _executor.REQUIRED_CASE_IDS
_LIVE_TOKEN = object()


class TrustedRemoteLiveRouteError(ValueError):
    pass


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _keys(value, names, label):
    if not isinstance(value, Mapping) or set(value) != set(names) or len(value) != len(names):
        raise TrustedRemoteLiveRouteError(f"{label}_exact_keys_invalid")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value:
        raise TrustedRemoteLiveRouteError(f"{label}_invalid")
    return value


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _identified(data, field, prefix):
    body = copy.deepcopy(dict(data))
    body[field] = prefix + "0" * 64
    expected = prefix + _sha(_dumps(body))
    if data.get(field) not in ("", prefix + "0" * 64, expected):
        raise TrustedRemoteLiveRouteError(f"{field}_invalid")
    body[field] = expected
    return body


class _LiveRawFixture:
    __slots__ = ("raw_response", "case_id", "execution_result_sha256", "_token")

    def __init__(self, raw_response, case_id, execution_result_sha256, token=None):
        if token is not _LIVE_TOKEN:
            raise TrustedRemoteLiveRouteError("live_fixture_requires_verified_execution")
        self.raw_response = raw_response
        self.case_id = case_id
        self.execution_result_sha256 = execution_result_sha256
        self._token = token


class _LiveFailure:
    __slots__ = ("code",)

    def __init__(self, code):
        self.code = code


class _LiveModelOutcome:
    """Private compatibility object consumed only by captured A-07 builders.

    The accepted builders use the historical string scripted_fixture_assembled
    as an internal assembled sentinel.  This object is never returned as source
    evidence; the public live-route record carries the verified execution
    binding and labels the sentinel as internal compatibility only.
    """

    __slots__ = ("outcome_id", "disposition", "failure", "artifacts", "source_binding", "frozen_g0_reference", "_token")

    def __init__(self, *, outcome_id, disposition, failure, artifacts, source_binding, frozen_g0_reference, token=None):
        if token is not _LIVE_TOKEN:
            raise TrustedRemoteLiveRouteError("live_model_outcome_requires_verified_execution")
        self.outcome_id = outcome_id
        self.disposition = disposition
        self.failure = failure
        self.artifacts = copy.deepcopy(artifacts)
        self.source_binding = copy.deepcopy(source_binding)
        self.frozen_g0_reference = frozen_g0_reference
        self._token = token
        self.validate()

    def validate(self):
        if self._token is not _LIVE_TOKEN or self.disposition not in ("scripted_fixture_assembled", "fail_closed"):
            raise TrustedRemoteLiveRouteError("live_model_outcome_invalid")
        if self.disposition == "scripted_fixture_assembled":
            if self.failure is not None or any(self.artifacts.get(key) is None for key in self.artifacts):
                raise TrustedRemoteLiveRouteError("live_model_outcome_invalid")
        elif self.failure is None:
            raise TrustedRemoteLiveRouteError("live_model_outcome_invalid")

    def canonical_bytes(self):
        self.validate()
        return _dumps({"outcome_id": self.outcome_id, "internal_compatibility_disposition": self.disposition, "failure_code": None if self.failure is None else self.failure.code, "artifacts": self.artifacts, "source_binding": self.source_binding})


def _fixture_authority(value):
    if type(value) is not _LiveRawFixture or value._token is not _LIVE_TOKEN:
        raise TrustedRemoteLiveRouteError("live_fixture_invalid")
    return value


def _model_validate(outcome, **args):
    if type(outcome) is not _LiveModelOutcome:
        raise TrustedRemoteLiveRouteError("live_model_outcome_type_invalid")
    outcome.validate()
    fixture = args.get("scripted_local_fixture")
    if args.get("execution_branch") != "trusted_remote_verified_execution" or type(fixture) is not _LiveRawFixture:
        raise TrustedRemoteLiveRouteError("live_execution_branch_invalid")
    if fixture.case_id != outcome.source_binding["case_id"] or fixture.execution_result_sha256 != outcome.source_binding["execution_result_sha256"]:
        raise TrustedRemoteLiveRouteError("live_source_cross_binding_invalid")


def _model_bytes(outcome):
    return outcome.canonical_bytes()


def _build_live_model_outcome(mr, provider_raw_response, context, guidance, case_id, execution_result_sha256, frozen_g0_reference):
    source = {"kind": "trusted_remote_verified_execution", "case_id": case_id, "execution_result_sha256": execution_result_sha256, "raw_response_sha256": provider_raw_response.sha256, "raw_response_byte_length": len(provider_raw_response.raw_bytes)}
    try:
        assembled = mr._FIXED_CANONICAL_ASSEMBLY_AUTHORITY(provider_raw_response, context, guidance)
    except Exception:
        body = {"source": source, "failure_code": "semantic_or_assembly_failed"}
        outcome_id = "trusted-remote-live-model-outcome-" + _sha(_dumps(body))
        return _LiveModelOutcome(outcome_id=outcome_id, disposition="fail_closed", failure=_LiveFailure("semantic_or_assembly_failed"), artifacts={key: None for key in ("raw_response_sha256", "raw_response_byte_length", "model_semantic_candidate_sha256", "assembled_page_id", "assembly_report_id", "assembly_report_sha256", "assembled_page_spec_sha256")}, source_binding=source, frozen_g0_reference=frozen_g0_reference, token=_LIVE_TOKEN), None
    report = assembled.report
    artifacts = {"raw_response_sha256": provider_raw_response.sha256, "raw_response_byte_length": len(provider_raw_response.raw_bytes), "model_semantic_candidate_sha256": assembled.candidate.sha256(), "assembled_page_id": assembled.page_spec.page_id, "assembly_report_id": report.report_id, "assembly_report_sha256": report.sha256(), "assembled_page_spec_sha256": report.assembled_page_spec_sha256}
    outcome_id = "trusted-remote-live-model-outcome-" + _sha(_dumps({"source": source, "artifacts": artifacts}))
    return _LiveModelOutcome(outcome_id=outcome_id, disposition="scripted_fixture_assembled", failure=None, artifacts=artifacts, source_binding=source, frozen_g0_reference=frozen_g0_reference, token=_LIVE_TOKEN), assembled


def _a07a_runner(mr):
    return mr._build_tier_a_07a_run(
        outcome_type=mr.TierA07aGateDeliveryOutcome,
        failure_type=mr.TierA07aGateDeliveryFailure,
        model_route_type=_LiveModelOutcome,
        model_route_validate=_model_validate,
        model_route_structural_validate=_LiveModelOutcome.validate,
        model_route_canonical_bytes=_model_bytes,
        model_fixture_authority=_fixture_authority,
        assembly_authority=mr._FIXED_CANONICAL_ASSEMBLY_AUTHORITY,
        renderer_type=mr.DeterministicPageRenderer,
        render_result_type=mr.RenderResult,
        consistency_checker_type=mr.MinimalConsistencyChecker,
        requirement_projector=mr.project_requirement_view,
        acceptance_plan_compiler=mr.compile_acceptance_plan,
        acceptance_binding_compiler=mr.compile_acceptance_binding,
        acceptance_fixture_authority=mr._FIXED_ACCEPTANCE_FIXTURE_AUTHORITY,
        acceptance_executor=mr._FIXED_ACCEPTANCE_EXECUTION_AUTHORITY,
        packager_type=mr.DeterministicResultPackager,
        result_package_type=mr.ResultPackage,
        fallback_deliverer=mr.deliver_frozen_g0_fallback,
        fallback_binding_authority=mr._FIXED_07A_FALLBACK_BINDING_AUTHORITY,
        path_preflight=mr._FIXED_07A_PATH_PREFLIGHT,
        staging_path=mr._FIXED_07A_STAGING_PATH,
        cleanup_directory=mr._FIXED_07A_CLEANUP_DIRECTORY,
        commit_staging=mr._FIXED_07A_COMMIT_STAGING,
        rollback_commit=mr._FIXED_07A_ROLLBACK_COMMIT,
        page_spec_projection=mr._07a_page_spec_artifacts,
        g0_projection=mr._07a_g0_binding,
        render_projection=mr._FIXED_07A_RENDER_PROJECTION,
        consistency_projection=mr._FIXED_07A_CONSISTENCY_PROJECTION,
        requirement_projection=mr._FIXED_07A_REQUIREMENT_PROJECTION,
        plan_projection=mr._FIXED_07A_PLAN_PROJECTION,
        binding_projection=mr._FIXED_07A_BINDING_PROJECTION,
        browser_projection=mr._FIXED_07A_BROWSER_PROJECTION,
        acceptance_counts_projection=mr._FIXED_07A_ACCEPTANCE_COUNTS,
        package_projection=mr._FIXED_07A_PACKAGE_PROJECTION,
        load_fallback_report=mr._FIXED_07A_LOAD_FALLBACK_REPORT,
        fallback_report_projection=mr._FIXED_07A_FALLBACK_REPORT_PROJECTION,
    )


def _a07b_runner(mr):
    return mr._build_tier_a_07b_run(
        outcome_type=mr.TierA07bGateDeliveryOutcome, report_type=mr.TierA07bFieldGateReport, patch_type=mr.TierA07bRepairPatch,
        model_type=_LiveModelOutcome, model_validate=_model_validate, model_structural=_LiveModelOutcome.validate, model_bytes=_model_bytes,
        model_binding_projection=mr._07b_model_binding, g0_projection=mr._07a_g0_binding, fallback_binding_projection=mr._07b_binding_projection,
        early_failure_factory=mr._07b_make_early_failure_outcome, empty_model_binding=mr._07b_empty_model_binding, empty_binding=mr._07b_empty,
        empty_counts=mr._07b_empty_counts, page_binding_projection=mr._07b_page_binding, report_binding_projection=mr._07b_report_binding,
        scope_intersection=mr._07b_intersection, fallback_prefix=mr._07b_fallback_prefix, fixture_authority=_fixture_authority,
        assembly_authority=mr._FIXED_CANONICAL_ASSEMBLY_AUTHORITY, field_gate_authority=mr._FIXED_07B_FIELD_GATE_AUTHORITY,
        renderer_type=mr.DeterministicPageRenderer, render_result_type=mr.RenderResult, render_projection=mr._FIXED_07A_RENDER_PROJECTION,
        consistency_type=mr.MinimalConsistencyChecker, consistency_projection=mr._FIXED_07A_CONSISTENCY_PROJECTION,
        requirement_projector=mr.project_requirement_view, requirement_projection=mr._FIXED_07A_REQUIREMENT_PROJECTION,
        plan_compiler=mr.compile_acceptance_plan, plan_projection=mr._FIXED_07A_PLAN_PROJECTION,
        binding_compiler=mr.compile_acceptance_binding, binding_projection=mr._FIXED_07A_BINDING_PROJECTION,
        acceptance_fixture_authority=mr._FIXED_ACCEPTANCE_FIXTURE_AUTHORITY, acceptance_executor=mr._FIXED_ACCEPTANCE_EXECUTION_AUTHORITY,
        browser_projection=mr._FIXED_07A_BROWSER_PROJECTION, count_projection=mr._FIXED_07A_ACCEPTANCE_COUNTS,
        packager_type=mr.DeterministicResultPackager, package_type=mr.ResultPackage, package_projection=mr._FIXED_07A_PACKAGE_PROJECTION,
        fallback_binding_authority=mr._FIXED_07A_FALLBACK_BINDING_AUTHORITY, fallback_deliverer=mr.deliver_frozen_g0_fallback,
        load_fallback_report=mr._FIXED_07A_LOAD_FALLBACK_REPORT, fallback_projection=mr._FIXED_07A_FALLBACK_REPORT_PROJECTION,
        path_preflight=mr._FIXED_07A_PATH_PREFLIGHT, staging_path=mr._FIXED_07A_STAGING_PATH, cleanup=mr._FIXED_07A_CLEANUP_DIRECTORY,
        commit=mr._FIXED_07A_COMMIT_STAGING, rollback=mr._FIXED_07A_ROLLBACK_COMMIT,
    )


def _route_inputs(value, case_id):
    names = ("frozen_g0_reference", "package", "context", "guidance", "manifest", "selected", "local_request", "pre_invocation_audit", "local_qwen_preparation", "fallback_record", "fallback_snapshot_dir", "render_output_dir", "model_package_output_dir", "fallback_output_dir", "scripted_acceptance_fixture")
    value = _keys(value, names, f"route_inputs_{case_id}")
    return dict(value)


def _manifest_bytes(mr, outcome, inputs):
    if outcome.delivery_source in ("model_first_pass_v1", "model_repaired_v1"):
        path = Path(inputs["model_package_output_dir"]) / "package_manifest.json"
    elif outcome.delivery_source == "g0_frozen_fallback":
        path = Path(inputs["fallback_output_dir"]) / "result_package" / "package_manifest.json"
    else:
        return _dumps({"schema_version": "req2web.runtime.trusted_remote_absent_result_package_manifest.v2", "status": outcome.status, "delivery_source": outcome.delivery_source})
    if not path.is_file():
        raise TrustedRemoteLiveRouteError("result_package_manifest_missing")
    raw = path.read_bytes()
    json.loads(raw.decode("utf-8"))
    return raw


def _gate_record(case_id, variant, outcome, execution_result_sha256):
    payload = {"schema_version": LIVE_GATE_RECORD_SCHEMA, "case_id": case_id, "source_kind": "trusted_remote_verified_execution", "execution_result_sha256": execution_result_sha256, "a07_semantics": variant, "status": outcome.status, "delivery_source": outcome.delivery_source, "g1_package_purpose": outcome.g1_package_purpose, "g2_action": outcome.g2_action, "repair_attempted": getattr(outcome, "repair_attempted", 0), "retry_performed": getattr(outcome, "retry_performed", False), "internal_a07_outcome_sha256": _sha(outcome.canonical_bytes()), "internal_scripted_disposition_is_not_source_claim": True}
    return _dumps(payload)


class TrustedRemoteLiveRouteBundleV2:
    __slots__ = ("_data", "_artifacts")

    def __init__(self, data, artifacts):
        self._data = copy.deepcopy(data)
        self._artifacts = copy.deepcopy(artifacts)

    def to_dict(self):
        return copy.deepcopy(self._data)

    def canonical_bytes(self):
        return _dumps(self._data)

    def sha256(self):
        return _sha(self.canonical_bytes())

    def to_return_artifacts(self):
        return copy.deepcopy(self._artifacts)


def route_verified_trusted_remote_two_case_v2(verified_execution, case_inputs):
    if type(verified_execution) is not _executor._VerifiedExecutionHandle:
        raise TrustedRemoteLiveRouteError("verified_execution_handle_required")
    handle = verified_execution._validated()
    if not isinstance(case_inputs, Mapping) or tuple(case_inputs) != REQUIRED_CASE_IDS:
        raise TrustedRemoteLiveRouteError("route_case_coverage_invalid")
    import req2web_orchestration.model_route as mr
    from req2web_provider.semantic_candidate import ProviderRawResponse

    result = handle._result.to_dict()
    cases = []
    return_artifacts = {}
    by_case = {row["case_id"]: row for row in result["cases"]}
    for case_id in REQUIRED_CASE_IDS:
        _executor._emit_progress("route_case_started", case_id=case_id)
        inputs = _route_inputs(case_inputs[case_id], case_id)
        package_case = next(item for item in handle._package.to_dict()["cases"] if item["case_id"] == case_id)
        try:
            inputs["selected"].validate_against(inputs["context"], inputs["manifest"])
            inputs["local_request"].validate_against(inputs["context"], inputs["manifest"])
        except Exception as exc:
            raise TrustedRemoteLiveRouteError("route_local_chain_validation_failed") from exc
        selection = inputs["selected"].selection_record
        provider_raw_expected = inputs["selected"].provider_visible_input.canonical_bytes()
        provider_binding = {"input_id": selection.input_view_id, "sha256": _sha(provider_raw_expected), "byte_length": len(provider_raw_expected)}
        if provider_binding != package_case["provider_input"] or package_case["provider_input_file"]["sha256"] != provider_binding["sha256"] or package_case["provider_input_file"]["byte_length"] != provider_binding["byte_length"]:
            raise TrustedRemoteLiveRouteError("route_provider_input_cross_binding_invalid")
        prompt_text = inputs["local_request"].prompt_artifact.prompt_text
        executor_prompt_raw = _dumps({"schema_version": _executor.PROMPT_ARTIFACT_SCHEMA, "prompt_text": prompt_text, "provider_input_mode": "append_exact_provider_visible_input_utf8"})
        if package_case["prompt_file"]["sha256"] != _sha(executor_prompt_raw) or package_case["prompt_file"]["byte_length"] != len(executor_prompt_raw):
            raise TrustedRemoteLiveRouteError("route_prompt_artifact_cross_binding_invalid")
        g0_binding = mr._07a_g0_binding(inputs["frozen_g0_reference"])
        if g0_binding["package_id"] != package_case["frozen_g0"]["package_id"] or g0_binding["reference_sha256"] != package_case["frozen_g0"]["sha256"]:
            raise TrustedRemoteLiveRouteError("same_case_frozen_g0_binding_invalid")
        row = by_case[case_id]
        raw = base64.b64decode(row["raw_response_base64"].encode("ascii"), validate=True)
        provider_raw = ProviderRawResponse.from_bytes(raw)
        fixture = _LiveRawFixture(provider_raw, case_id, handle._result.sha256(), _LIVE_TOKEN)
        model_outcome, assembled = _build_live_model_outcome(mr, provider_raw, inputs["context"], inputs["guidance"], case_id, handle._result.sha256(), inputs["frozen_g0_reference"])
        common = {**inputs, "model_route_outcome": model_outcome, "execution_branch": "trusted_remote_verified_execution", "scripted_local_fixture": fixture, "case_id": case_id}
        variant = "A-07a"
        if assembled is not None:
            report = mr.create_tier_a_07b_field_gate_report(case_id=case_id, page_spec=assembled.page_spec, local_request=inputs["local_request"])
        else:
            report = None
        if report is not None and report.decision == "repair" and report.repair_eligible:
            patch = mr.TierA07bRepairPatch.create(report_id=report.report_id, report_sha256=report.sha256(), first_page_id=assembled.page_spec.page_id, first_page_spec_sha256=mr._07b_page_binding(assembled.page_spec)["page_spec_sha256"], attempt_index=1, operations=((report.reported_field, report.expected),))
            outcome = _a07b_runner(mr)(object(), **common, field_gate_report=report.canonical_bytes(), repair_patch=patch.canonical_bytes())
            variant = "A-07b"
        else:
            outcome = _a07a_runner(mr)(object(), **common)
        outcome.validate()
        gate_raw = _gate_record(case_id, variant, outcome, handle._result.sha256())
        manifest_raw = _manifest_bytes(mr, outcome, inputs)
        live_record = {"schema_version": "req2web.runtime.trusted_remote_live_model_route.v2", "case_id": case_id, "source_kind": "trusted_remote_verified_execution", "execution_result_sha256": handle._result.sha256(), "raw_response_sha256": row["raw_response_sha256"], "raw_response_byte_length": row["raw_response_byte_length"], "a07_semantics": variant, "gate_delivery_sha256": _sha(gate_raw), "result_package_manifest_sha256": _sha(manifest_raw), "status": outcome.status, "delivery_source": outcome.delivery_source, "provider_call_count": row["provider_call_count"], "retry_count": 0, "claims": {"verified_real_provider": False, "browser_quality": False, "formal_quality": False}}
        live_raw = _dumps(live_record)
        error = None if outcome.status in ("first_pass_success", "recovered_success", "fallback_delivery") else _dumps({"code": "live_route_failed_closed", "status": outcome.status})
        return_artifacts[case_id] = {"model_route_outcome": live_raw, "gate_delivery_outcome": gate_raw, "result_package_manifest": manifest_raw, "error": error}
        cases.append({"case_id": case_id, "live_model_route_sha256": _sha(live_raw), "gate_delivery_sha256": _sha(gate_raw), "result_package_manifest_sha256": _sha(manifest_raw), "status": outcome.status, "delivery_source": outcome.delivery_source, "a07_semantics": variant})
        _executor._emit_progress(
            "route_case_complete",
            case_id=case_id,
            a07_semantics=variant,
            status=outcome.status,
            delivery_source=outcome.delivery_source,
        )
    data = {"schema_version": LIVE_ROUTE_BUNDLE_SCHEMA, "bundle_id": "trusted-remote-live-route-bundle-v2-" + "0" * 64, "state": "live_route_completed_for_return", "execution_result": {"result_id": result["result_id"], "sha256": handle._result.sha256()}, "cases": cases, "claims": {"two_cases_only": True, "one_model_call_per_case": True, "retry_count": 0, "slice2_final_gate_activated": False, "h1": False, "browser_quality": False, "formal_quality": False}}
    data = _identified(data, "bundle_id", "trusted-remote-live-route-bundle-v2-")
    return TrustedRemoteLiveRouteBundleV2(data, return_artifacts)


class TrustedRemoteLiveSliceRunV2:
    __slots__ = ("verified_execution", "route_bundle", "return_manifest", "return_root")

    def __init__(self, verified_execution, route_bundle, return_manifest, return_root):
        if type(verified_execution) is not _executor._VerifiedExecutionHandle:
            raise TrustedRemoteLiveRouteError("verified_execution_handle_required")
        self.verified_execution = verified_execution._validated()
        self.route_bundle = route_bundle
        self.return_manifest = copy.deepcopy(return_manifest)
        self.return_root = Path(return_root).resolve(strict=True)

    def finalize_closeout(self, instance_release_evidence, temporary_access_revocation_evidence):
        return _executor.finalize_trusted_remote_closeout_v2(self.verified_execution, self.return_root, instance_release_evidence, temporary_access_revocation_evidence)


def run_trusted_remote_two_case_live_slice_v2(*, execution_package, action_time_plan, pre_run_receipt, expected_signer, expected_instance_facts, repository_root, package_root, model_root, runtime_root, result_root, case_inputs, return_root, project_temp_root, cancel_request_path=None):
    _executor._emit_progress("trusted_remote_live_slice_started")
    verified = _executor.run_trusted_remote_executor_v2(execution_package, action_time_plan, pre_run_receipt, expected_signer, expected_instance_facts, repository_root, package_root, model_root, runtime_root, result_root, cancel_request_path)
    _executor._emit_progress(
        "verified_execution_complete",
        execution_result_sha256=verified._result.sha256(),
    )
    _executor._emit_progress("route_gate_package_started")
    route_bundle = route_verified_trusted_remote_two_case_v2(verified, case_inputs)
    _executor._emit_progress(
        "route_gate_package_complete",
        route_bundle_sha256=route_bundle.sha256(),
    )
    _executor._emit_progress("return_bundle_started")
    return_manifest = _executor.write_trusted_remote_return_bundle_v2(verified, route_bundle.to_return_artifacts(), return_root, project_temp_root)
    _executor._emit_progress(
        "return_bundle_complete",
        state=return_manifest["state"],
        manifest_sha256=_sha(_dumps(return_manifest)),
    )
    return TrustedRemoteLiveSliceRunV2(verified, route_bundle, return_manifest, return_root)


__all__ = ["LIVE_GATE_RECORD_SCHEMA", "LIVE_ROUTE_BUNDLE_SCHEMA", "TrustedRemoteLiveRouteBundleV2", "TrustedRemoteLiveRouteError", "TrustedRemoteLiveSliceRunV2", "route_verified_trusted_remote_two_case_v2", "run_trusted_remote_two_case_live_slice_v2"]
