"""Bounded local dry-run package preparation for the trusted-remote two-case run."""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping

from . import autodl_trusted_remote_live_authority as _authority
from . import autodl_trusted_remote_records as _records
from . import autodl_trusted_remote_slice3 as _slice3

TWO_CASE_RUNNER_PACKAGE_SCHEMA = "req2web.runtime.trusted_remote_two_case_runner_package.v1"
TWO_CASE_RUNNER_RESULT_SCHEMA = "req2web.runtime.trusted_remote_two_case_runner_dry_run_result.v1"
PACKAGE_PREFIX = "trusted-remote-two-case-runner-package-"
REQUIRED_CASE_IDS = _records.REQUIRED_CASE_IDS


class TrustedRemoteTwoCaseRunnerError(ValueError):
    pass


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _keys(value, names, label):
    if not isinstance(value, Mapping) or set(value) != set(names) or len(value) != len(names):
        raise TrustedRemoteTwoCaseRunnerError(f"{label}_exact_keys_invalid")
    return value


def _plan(plan):
    if type(plan) is _records.TrustedRemoteActionTimePlan:
        raw = plan.canonical_bytes()
    elif type(plan) is bytes:
        raw = plan
    else:
        raise TrustedRemoteTwoCaseRunnerError("action_time_plan_exact_type_or_bytes_required")
    try:
        replay = _records.TrustedRemoteActionTimePlan.from_bytes(raw)
    except Exception as exc:
        raise TrustedRemoteTwoCaseRunnerError("action_time_plan_replay_invalid") from exc
    if replay.canonical_bytes() != raw:
        raise TrustedRemoteTwoCaseRunnerError("action_time_plan_not_canonical")
    return replay.to_dict(), raw


def _identified(data):
    body = copy.deepcopy(dict(data))
    body["package_id"] = PACKAGE_PREFIX + "0" * 64
    expected = PACKAGE_PREFIX + _sha(_dumps(body))
    if data.get("package_id") not in ("", PACKAGE_PREFIX + "0" * 64, expected):
        raise TrustedRemoteTwoCaseRunnerError("package_id_invalid")
    body["package_id"] = expected
    return body


class TrustedRemoteTwoCaseRunnerPackage:
    __slots__ = ("_data",)
    def __init__(self, data): self._data = data
    @classmethod
    def from_dict(cls, value):
        names = ("schema_version", "package_id", "action_time_plan_binding", "cases", "quality_profile", "command", "return_paths", "closeout_steps", "dry_run_contract")
        value = _keys(value, names, "two_case_runner_package")
        if value["schema_version"] != TWO_CASE_RUNNER_PACKAGE_SCHEMA:
            raise TrustedRemoteTwoCaseRunnerError("package_schema_invalid")
        if not isinstance(value["cases"], list) or [row.get("case_id") for row in value["cases"] if isinstance(row, Mapping)] != list(REQUIRED_CASE_IDS):
            raise TrustedRemoteTwoCaseRunnerError("package_cases_invalid")
        if value["quality_profile"] != {"profile": "quality_experiment", "dtype": "bf16", "quantization": "none", "provider_call_limit_per_case": 1, "retry_allowed": False}:
            raise TrustedRemoteTwoCaseRunnerError("package_quality_profile_invalid")
        if value["command"] != ["python", "scripts/run_stage3_trusted_remote_two_case.py", "--dry-run", "--package", "RUN_PACKAGE.json", "--action-time-plan", "ACTION_TIME_PLAN.json", "--candidate-records", "CANDIDATE_RECORDS.json"]:
            raise TrustedRemoteTwoCaseRunnerError("package_command_invalid")
        if value["return_paths"] != list(_records.RESULT_RETURN_PATHS) or value["closeout_steps"] != list(_records.CLOSEOUT_STEP_ORDER):
            raise TrustedRemoteTwoCaseRunnerError("package_inventory_invalid")
        if value["dry_run_contract"] != {"network_used": False, "credentials_used": False, "model_loaded": False, "run_occurred": False, "provider_success": False, "live_execution_requires_authority": True}:
            raise TrustedRemoteTwoCaseRunnerError("package_dry_run_contract_invalid")
        body = _identified(value)
        if value["package_id"] != body["package_id"]:
            raise TrustedRemoteTwoCaseRunnerError("package_id_invalid")
        return cls(body)
    @classmethod
    def from_bytes(cls, raw):
        if not isinstance(raw, bytes): raise TrustedRemoteTwoCaseRunnerError("canonical_bytes_required")
        try: value=json.loads(raw.decode("utf-8"))
        except Exception as exc: raise TrustedRemoteTwoCaseRunnerError("canonical_json_invalid") from exc
        instance=cls.from_dict(value)
        if instance.canonical_bytes()!=raw: raise TrustedRemoteTwoCaseRunnerError("package_not_canonical")
        return instance
    def to_dict(self): return copy.deepcopy(self._data)
    def canonical_bytes(self): return _dumps(self._data)
    def sha256(self): return _sha(self.canonical_bytes())


def prepare_trusted_remote_two_case_runner_package(action_time_plan, candidate_records):
    plan, raw = _plan(action_time_plan)
    if not isinstance(candidate_records, Mapping) or tuple(candidate_records) != REQUIRED_CASE_IDS:
        raise TrustedRemoteTwoCaseRunnerError("candidate_records_coverage_invalid")
    plan_object = _records.TrustedRemoteActionTimePlan.from_dict(plan)
    rows=[]
    for case in plan["cases"]:
        case_id=case["case_id"]
        try: record=_slice3.validate_trusted_remote_production_run_record_binding(candidate_records[case_id], plan_object, case_id)
        except Exception as exc: raise TrustedRemoteTwoCaseRunnerError("candidate_record_binding_invalid") from exc
        item=record.to_dict()
        rows.append({"case_id":case_id,"provider_input":copy.deepcopy(case["provider_input"]),"frozen_g0":copy.deepcopy(case["frozen_g0"]),"candidate_record_id":item["run_record_id"],"candidate_record_sha256":_sha(record.canonical_bytes()),"raw_response_sha256":item["raw_response"]["sha256"],"raw_response_byte_length":item["raw_response"]["byte_length"]})
    data={"schema_version":TWO_CASE_RUNNER_PACKAGE_SCHEMA,"package_id":PACKAGE_PREFIX+"0"*64,"action_time_plan_binding":{"record_id":plan["record_id"],"sha256":_sha(raw),"commit_sha":plan["policy"]["implementation_commit_sha"],"tree_sha":plan["policy"]["implementation_tree_sha"]},"cases":rows,"quality_profile":{"profile":"quality_experiment","dtype":"bf16","quantization":"none","provider_call_limit_per_case":1,"retry_allowed":False},"command":["python","scripts/run_stage3_trusted_remote_two_case.py","--dry-run","--package","RUN_PACKAGE.json","--action-time-plan","ACTION_TIME_PLAN.json","--candidate-records","CANDIDATE_RECORDS.json"],"return_paths":list(_records.RESULT_RETURN_PATHS),"closeout_steps":list(_records.CLOSEOUT_STEP_ORDER),"dry_run_contract":{"network_used":False,"credentials_used":False,"model_loaded":False,"run_occurred":False,"provider_success":False,"live_execution_requires_authority":True}}
    return TrustedRemoteTwoCaseRunnerPackage.from_dict(_identified(data))


def validate_trusted_remote_two_case_runner_package(value, action_time_plan, candidate_records):
    if type(value) is TrustedRemoteTwoCaseRunnerPackage: raw=value.canonical_bytes()
    elif type(value) is bytes: raw=value
    else: raise TrustedRemoteTwoCaseRunnerError("package_exact_type_or_bytes_required")
    package=TrustedRemoteTwoCaseRunnerPackage.from_bytes(raw)
    expected=prepare_trusted_remote_two_case_runner_package(action_time_plan,candidate_records)
    if package.canonical_bytes()!=expected.canonical_bytes(): raise TrustedRemoteTwoCaseRunnerError("package_binding_invalid")
    return package


def run_trusted_remote_two_case_dry_run(value, action_time_plan, candidate_records, *, dry_run=True):
    if dry_run is not True: raise TrustedRemoteTwoCaseRunnerError("dry_run_true_required")
    package=validate_trusted_remote_two_case_runner_package(value,action_time_plan,candidate_records)
    return {"schema_version":TWO_CASE_RUNNER_RESULT_SCHEMA,"package_id":package.to_dict()["package_id"],"package_sha256":package.sha256(),"state":"prepared_dry_run_not_executed","model_loaded":False,"run_occurred":False,"provider_call_count":0,"network_used":False,"credentials_used":False,"provider_success":False}


def validate_trusted_remote_two_case_live_preconditions(value, action_time_plan, candidate_records, live_authority_receipt, expected_signer, expected_instance_facts, now_utc, *, dry_run):
    if dry_run is not False: raise TrustedRemoteTwoCaseRunnerError("live_execution_requires_explicit_dry_run_false")
    package=validate_trusted_remote_two_case_runner_package(value,action_time_plan,candidate_records)
    try: receipt=_authority.consume_trusted_remote_live_authority_receipt(live_authority_receipt,action_time_plan,candidate_records,expected_instance_facts,expected_signer,now_utc)
    except Exception as exc: raise TrustedRemoteTwoCaseRunnerError("live_authority_precondition_invalid") from exc
    return {"package_id":package.to_dict()["package_id"],"authority_receipt_id":receipt.to_dict()["receipt_id"],"state":"preconditions_verified_execution_not_started"}


__all__=["PACKAGE_PREFIX","REQUIRED_CASE_IDS","TWO_CASE_RUNNER_PACKAGE_SCHEMA","TWO_CASE_RUNNER_RESULT_SCHEMA","TrustedRemoteTwoCaseRunnerError","TrustedRemoteTwoCaseRunnerPackage","prepare_trusted_remote_two_case_runner_package","run_trusted_remote_two_case_dry_run","validate_trusted_remote_two_case_live_preconditions","validate_trusted_remote_two_case_runner_package"]
