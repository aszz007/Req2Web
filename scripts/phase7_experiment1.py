"""Prepare, validate, and (after separate approval) run Phase 7 Experiment 1."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from phase7_direct_html_worker import build_direct_prompt, canonical_bytes, identity

SCHEMA = "req2web.phase7.experiment1.v1"
SEED = 20260913
CASE_PATH = ROOT / "fixtures/phase7_experiment1_cases_v1.json"
OBLIGATION_PATH = ROOT / "fixtures/phase7_experiment1_obligations_v1.json"
DEFAULT_ROOT = ROOT / "outputs/phase7_experiment1_v1"
HIGH_GPU = "high_gpu_bf16"
MODEL_ID = "Qwen/Qwen3.5-9B"
MODEL_REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
KNOWN_INVENTORY = ROOT / "fixtures/phase4/stability/cases/10-p4-05-stability-10-office/model_inventory.json"
EXPECTED_CASE_IDS = ("p7-e1-expense-entry","p7-e1-workshop-enrollment","p7-e1-maintenance-ticket","p7-e1-room-directory","p7-e1-supply-catalogue","p7-e1-course-directory","p7-e1-editorial-review","p7-e1-parcel-handover","p7-e1-volunteer-assignment","p7-e1-draft-notice","p7-e1-booking-request","p7-e1-bulk-tag-edit")


class ExperimentError(ValueError):
    pass


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write_once(path: Path, value: object, *, raw: bool = False) -> None:
    data = value if raw else canonical_bytes(value)
    assert isinstance(data, bytes)
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != data:
            raise ExperimentError(f"write-once artifact drifted: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())


def append_ledger(path: Path, row: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_bytes(dict(row)) + b"\n"
    with path.open("ab") as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())


def load_and_validate_sources() -> tuple[dict[str, object], dict[str, object]]:
    cases = read_json(CASE_PATH); obligations = read_json(OBLIGATION_PATH)
    if not isinstance(cases, dict) or not isinstance(obligations, dict):
        raise ExperimentError("source fixtures must be objects")
    rows = cases.get("cases")
    if not isinstance(rows, list) or len(rows) != 12:
        raise ExperimentError("exactly twelve public cases are required")
    ids = [row.get("case_id") for row in rows if isinstance(row, dict)]
    if tuple(ids) != EXPECTED_CASE_IDS:
        raise ExperimentError("case IDs or fixed plan order drifted")
    if len(ids) != 12 or len(set(ids)) != 12:
        raise ExperimentError("case IDs must be twelve unique strings")
    evaluator = obligations.get("cases")
    if not isinstance(evaluator, dict) or list(evaluator) != ids:
        raise ExperimentError("evaluator cases must bind the public order exactly")
    if any(not isinstance(evaluator[c], list) or len(evaluator[c]) != 4 for c in ids):
        raise ExperimentError("every case requires exactly four obligations")
    forbidden = {"obligations", "criterion", "expected_verdict", "scoring"}
    if any(forbidden.intersection(row) for row in rows):
        raise ExperimentError("public cases contain evaluator-only keys")
    return cases, obligations


def model_visible_case(case: Mapping[str, object]) -> dict[str, object]:
    value = dict(case)
    value["requirement"] = str(case["requirement"]) + " Exact public mock data JSON: " + canonical_bytes(case["sample_data"]).decode("utf-8")
    return value


def bounded_duplicate_check(cases: list[dict[str, object]]) -> dict[str, object]:
    known = [ROOT/"fixtures/phase4_canonical_full_flow_cases_v1.json", ROOT/"fixtures/phase5_publication_case_drafts_v1.json", ROOT/"fixtures/phase5_publication_case_templates_v1.json"]
    texts: set[str] = set()
    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key,item in value.items(): texts.add(item) if key=="requirement" and isinstance(item,str) else walk(item)
        elif isinstance(value,list):
            for item in value: walk(item)
    for path in known:
        if path.is_file() and path.stat().st_size <= 1_000_000: walk(read_json(path))
    matches=[row["case_id"] for row in cases if row["requirement"] in texts]
    if matches: raise ExperimentError("exact requirement duplicate found")
    return {"checked_paths":[str(p.relative_to(ROOT)) for p in known if p.is_file()],"exact_duplicate_case_ids":matches}


def fixed_schedule(cases: list[dict[str, object]]) -> list[dict[str, object]]:
    ordered = sorted(cases, key=lambda row: hashlib.sha256(f"{SEED}:{row['case_id']}".encode()).hexdigest())
    result: list[dict[str, object]] = []
    for position, case in enumerate(ordered):
        arms = ("A", "B") if position % 2 == 0 else ("B", "A")
        for arm in arms:
            result.append({"execution_index":len(result)+1, "case_position":position+1, "case_id":case["case_id"], "arm":arm, "row_id":f"{case['case_id']}:{arm}"})
    return result


def runtime_preflight(model_root_override: Path | None = None, integrity_evidence_override: Path | None = None) -> dict[str, object]:
    profile = None
    try:
        from req2web_runtime.phase4_local_qwen_langgraph_integrated import local_langgraph_profile
        profile = local_langgraph_profile(HIGH_GPU).to_dict()
    except Exception as exc:
        profile = {"profile_name":HIGH_GPU, "profile_read_error":f"{type(exc).__name__}: {exc}"}
    packages = {name: bool(importlib.util.find_spec(name)) for name in ("torch", "transformers", "langgraph")}
    gpu: dict[str, object] = {"cuda_available":False, "device_count":0}
    if packages["torch"]:
        try:
            import torch
            gpu["torch_version"] = torch.__version__
            gpu["cuda_available"] = bool(torch.cuda.is_available())
            gpu["device_count"] = int(torch.cuda.device_count())
            if gpu["cuda_available"] and gpu["device_count"]:
                props = torch.cuda.get_device_properties(0)
                free, total = torch.cuda.mem_get_info(0)
                gpu.update({"device_name":props.name, "total_vram_bytes":int(total), "free_vram_bytes":int(free), "compute_capability":[props.major, props.minor]})
        except Exception as exc:
            gpu["probe_error"] = f"{type(exc).__name__}: {exc}"
    inventory = read_json(KNOWN_INVENTORY) if KNOWN_INVENTORY.is_file() else {}
    model_root_text = inventory.get("relocation_binding", {}).get("source_evidence_root") if isinstance(inventory, dict) else None
    model_root = model_root_override.resolve(strict=False) if model_root_override else (Path(model_root_text) if isinstance(model_root_text, str) else None)
    model_present = bool(model_root and model_root.is_dir() and not model_root.is_symlink())
    integrity_present = bool(integrity_evidence_override and integrity_evidence_override.is_file() and not integrity_evidence_override.is_symlink()) if model_root_override else KNOWN_INVENTORY.is_file()
    min_total = int(profile.get("min_total_vram_bytes", 30_000_000_000))
    min_free = int(profile.get("min_free_vram_bytes", 24_000_000_000))
    ready = bool(all(packages.values()) and model_present and integrity_present and gpu.get("cuda_available") and int(gpu.get("total_vram_bytes",0)) >= min_total and int(gpu.get("free_vram_bytes",0)) >= min_free)
    blockers = []
    if not all(packages.values()): blockers.append("required_python_packages_unavailable")
    if not model_present: blockers.append("pinned_model_root_unavailable")
    if not integrity_present: blockers.append("integrity_evidence_unavailable")
    if not gpu.get("cuda_available"): blockers.append("cuda_gpu_unavailable")
    elif int(gpu.get("total_vram_bytes",0)) < min_total or int(gpu.get("free_vram_bytes",0)) < min_free: blockers.append("high_gpu_bf16_vram_requirement_unmet")
    return {"schema_version":f"{SCHEMA}.runtime_preflight", "read_only":True, "model_loaded":False, "inference_executed":False, "network_used":False, "python":sys.version, "executable":sys.executable, "packages":packages, "profile":profile, "model":{"model_id":MODEL_ID,"revision":MODEL_REVISION,"selected_root":None if model_root is None else str(model_root),"root_present":model_present,"selected_integrity_evidence":None if integrity_evidence_override is None else str(integrity_evidence_override.resolve(strict=False)),"integrity_evidence_present":integrity_present}, "gpu":gpu, "runtime_eligible":ready, "blockers":blockers}


def prepare(destination: Path) -> dict[str, object]:
    cases, obligations = load_and_validate_sources()
    if destination.exists():
        raise ExperimentError("preparation destination already exists")
    destination.mkdir(parents=True)
    from req2web_inspector.canonical_run import _build_upstream
    upstream_root = destination / "upstream"
    case_rows = cases["cases"]
    duplicate_receipt = bounded_duplicate_check(case_rows)
    prompts = []
    evidence_records = []
    for public_case in case_rows:
        case = model_visible_case(public_case)
        out = upstream_root / str(case["case_id"])
        owned_case = {"case_id":case["case_id"],"request_id":f"request-{case['case_id']}","requirement":case["requirement"],"target_device":case["target_device"],"task_type":case["task_type"],"constraints":case["constraints"]}
        upstream = _build_upstream(case=owned_case, index_dir=ROOT / "data/processed/rag", output_root=out)
        evidence = {"schema_version":f"{SCHEMA}.shared_provider_visibility", "case_id":case["case_id"], "provider_visible_retrieval_evidence":[], "basis":"owning local F1-F4 node input has canonical B and authority state but no retrieval-document payload", "same_payload_for_arm_a_and_b":True}
        evidence_records.append({"case_id":case["case_id"], "identity":identity(canonical_bytes(evidence), f"{SCHEMA}.shared_evidence"), "relative_path":f"shared-evidence/{case['case_id']}.json"})
        write_once(destination / "shared-evidence" / f"{case['case_id']}.json", evidence)
        prompt = build_direct_prompt(case, evidence)
        write_once(destination / "prompts" / f"{case['case_id']}-B.txt", prompt.encode(), raw=True)
        prompts.append({"case_id":case["case_id"],"arm":"B","identity":identity(prompt.encode(),f"{SCHEMA}.direct_prompt")})
    schedule = fixed_schedule(case_rows)
    aliases = []
    for index, row in enumerate(schedule):
        aliases.append({"alias":f"artifact-{index+1:02d}","row_id":row["row_id"]})
    preflight = runtime_preflight()
    from req2web_agent import PROMPT_AUTHORITY_IDENTITY
    from req2web_runtime.phase4_local_qwen_langgraph_integrated import local_langgraph_profile
    sources=(Path(__file__),ROOT/"scripts/phase7_direct_html_worker.py",CASE_PATH,OBLIGATION_PATH,ROOT/"src/req2web_inspector/canonical_run.py",ROOT/"src/req2web_runtime/phase4_local_qwen_langgraph_integrated.py",ROOT/"src/req2web_runtime/phase4_remote_qwen_fresh_integrated.py",ROOT/"src/req2web_orchestration/phase4_graph.py")
    freeze = {"schema_version":f"{SCHEMA}.freeze.v2", "preparation_revision":"v2_execution_visibility_metrics", "seed":SEED,"case_source":identity(CASE_PATH.read_bytes(),f"{SCHEMA}.cases"),"obligation_source":identity(OBLIGATION_PATH.read_bytes(),f"{SCHEMA}.obligations"),"source_bindings":{p.relative_to(ROOT).as_posix():identity(p.read_bytes(),f"{SCHEMA}.source") for p in sources},"owning_prompt_authority_identity":PROMPT_AUTHORITY_IDENTITY,"owning_profile":local_langgraph_profile(HIGH_GPU).to_dict(),"duplicate_check":duplicate_receipt,"schedule":schedule,"shared_evidence":evidence_records,"direct_prompts":prompts,"limits":{"planned_rows":24,"max_generation_calls":60,"arm_a_node_call_cap":48,"arm_b_call_cap":12,"arm_b_timeout_seconds":1200,"batch_wall_seconds":21600,"automatic_retry_count":0},"subject":{"model_id":MODEL_ID,"revision":MODEL_REVISION,"profile":HIGH_GPU,"dtype":"bfloat16","quantization":"none","do_sample":False,"thinking":False}}
    write_once(destination / "freeze_manifest.json", freeze)
    write_once(destination / "runtime_preflight.json", preflight)
    write_once(destination / "blind" / "alias_mapping.private.json", {"schema_version":f"{SCHEMA}.alias_mapping", "mapping":aliases})
    write_once(destination / "blind" / "review_packet.json", {"schema_version":f"{SCHEMA}.blind_packet", "status":"awaiting_generated_artifacts", "aliases":[{"alias":x["alias"]} for x in aliases], "observation_import_schema":{"alias":"string","criterion_id":"one frozen obligation ID","label":"pass|fail|unknown|not_supported","actions":"visible UI action records, maximum 12","elapsed_seconds":"0..90","evidence":"screenshot or DOM/action evidence reference","artifact_sha256":"bound generated HTML hash","reason":"non-empty text"}, "instructions":["Keep alias mapping hidden from the observer.","Reset the page before each obligation and perform only visible UI actions.","Do not treat labels or PageSpec claims as observed behavior.","Freeze selector bindings before measured observation.","Import one result per alias and obligation; agent observations are not human evaluation."]})
    write_once(destination / "observations.json", {"schema_version":f"{SCHEMA}.observations", "status":"scoring_pending", "rows":[]})
    write_once(destination / "machine_summary.json", {"schema_version":f"{SCHEMA}.summary", "status":"prepared" if preflight["runtime_eligible"] else "runtime_blocked", "planned_rows":24,"generated_rows":0,"observed_rows":0,"comparative_conclusion":"withheld_no_generation_or_observation","runtime_blockers":preflight["blockers"]})
    return read_json(destination / "machine_summary.json")


def validate(root: Path) -> dict[str, object]:
    cases, obligations = load_and_validate_sources()
    freeze = read_json(root / "freeze_manifest.json"); summary = read_json(root / "machine_summary.json")
    if freeze["case_source"] != identity(CASE_PATH.read_bytes(),f"{SCHEMA}.cases") or freeze["obligation_source"] != identity(OBLIGATION_PATH.read_bytes(),f"{SCHEMA}.obligations"):
        raise ExperimentError("frozen source identity drifted")
    if freeze["schedule"] != fixed_schedule(cases["cases"]): raise ExperimentError("schedule drifted")
    if len(freeze["shared_evidence"]) != 12 or len(freeze["direct_prompts"]) != 12: raise ExperimentError("freeze inventory incomplete")
    required={Path(x).as_posix() for x in ("scripts/phase7_experiment1.py","scripts/phase7_direct_html_worker.py","fixtures/phase7_experiment1_cases_v1.json","fixtures/phase7_experiment1_obligations_v1.json","src/req2web_inspector/canonical_run.py","src/req2web_runtime/phase4_local_qwen_langgraph_integrated.py","src/req2web_runtime/phase4_remote_qwen_fresh_integrated.py","src/req2web_orchestration/phase4_graph.py")}
    if set(freeze.get("source_bindings",{})) != required: raise ExperimentError("source binding inventory missing or extra")
    for relative,binding in freeze["source_bindings"].items():
        path=ROOT/relative
        if identity(path.read_bytes(),f"{SCHEMA}.source") != binding: raise ExperimentError("source binding drifted")
    from req2web_agent import PROMPT_AUTHORITY_IDENTITY
    from req2web_runtime.phase4_local_qwen_langgraph_integrated import local_langgraph_profile
    if freeze.get("owning_prompt_authority_identity") != PROMPT_AUTHORITY_IDENTITY or freeze.get("owning_profile") != local_langgraph_profile(HIGH_GPU).to_dict(): raise ExperimentError("owning prompt or profile binding drifted")
    for row in freeze["shared_evidence"]:
        path=root/row["relative_path"]
        if identity(path.read_bytes(),f"{SCHEMA}.shared_evidence") != row["identity"]: raise ExperimentError("shared evidence drifted")
    for row in freeze["direct_prompts"]:
        path=root/"prompts"/f"{row['case_id']}-B.txt"
        if identity(path.read_bytes(),f"{SCHEMA}.direct_prompt") != row["identity"]: raise ExperimentError("direct prompt drifted")
    return {"status":"validated_read_only","experiment_status":summary["status"],"case_count":len(cases["cases"]),"obligation_count":sum(len(x) for x in obligations["cases"].values()),"schedule_rows":len(freeze["schedule"])}


def metrics(observations: list[dict[str, object]], artifacts: Mapping[str, tuple[str,str,str,Path]]) -> dict[str, object]:
    _, obligation_doc=load_and_validate_sources(); frozen=obligation_doc["cases"]
    valid={"pass","fail","unknown","not_supported"}; grouped={}; seen=set()
    for row in observations:
        key=(row.get("case_id"),row.get("arm"),row.get("criterion_id"))
        if row.get("case_id") not in EXPECTED_CASE_IDS or row.get("arm") not in {"A","B"} or row.get("criterion_id") not in frozen[row["case_id"]] or key in seen: raise ExperimentError("observation inventory invalid")
        if row.get("label") not in valid: raise ExperimentError("invalid observation label")
        alias=row.get("alias"); artifact_hash=row.get("artifact_sha256")
        binding=artifacts.get(alias) if isinstance(alias,str) else None
        if binding is None or binding[:3] != (row["case_id"],row["arm"],artifact_hash) or not binding[3].is_file() or "sha256:"+hashlib.sha256(binding[3].read_bytes()).hexdigest()!=artifact_hash: raise ExperimentError("observation artifact binding invalid")
        if not isinstance(row.get("actions"),list) or len(row["actions"])>12 or not isinstance(row.get("elapsed_seconds"),(int,float)) or not 0<=row["elapsed_seconds"]<=90 or not isinstance(row.get("evidence"),str) or not row["evidence"].strip() or not isinstance(row.get("reason"),str) or not row["reason"].strip(): raise ExperimentError("observation budget or evidence invalid")
        seen.add(key); grouped.setdefault((row["case_id"],row["arm"]),[]).append(row)
    arm={}
    for name in ("A","B"):
        rows=[v for (c,a),v in grouped.items() if a==name]
        passes=sum(sum(x["label"]=="pass" for x in r) for r in rows)
        unknown=sum(sum(x["label"]=="unknown" for x in r) for r in rows)
        exact=len(rows)==12 and all(len(r)==4 for r in rows)
        arm[name]={"observed_case_count":len(rows),"known_passes":passes,"unknown":unknown,"mean_case_score":None if not exact else sum(sum(x["label"]=="pass" for x in r)/4 for r in rows)/12,"sensitivity_upper_mean":None if not exact else sum((sum(x["label"]=="pass" for x in r)+sum(x["label"]=="unknown" for x in r))/4 for r in rows)/12,"all_obligations_success_count":sum(all(x["label"]=="pass" for x in r) and len(r)==4 for r in rows)}
    complete=all(set(x["criterion_id"] for x in grouped.get((case,arm),[]))==set(frozen[case]) for case in EXPECTED_CASE_IDS for arm in ("A","B"))
    unknown=any(x["label"] in {"unknown","not_supported"} for x in observations)
    pairs=[]
    if complete:
        for case in EXPECTED_CASE_IDS:
            pairs.append(sum(x["label"]=="pass" for x in grouped[(case,"A")])/4-sum(x["label"]=="pass" for x in grouped[(case,"B")])/4)
    bootstrap=None
    if complete:
        rng=random.Random(SEED); samples=sorted(sum(rng.choice(pairs) for _ in pairs)/12 for _ in range(10000))
        bootstrap={"resamples":10000,"seed":SEED,"low":samples[249],"high":samples[9749]}
    return {"arms":arm,"paired_case_differences":pairs,"mean_paired_difference":None if not complete else sum(pairs)/12,"bootstrap_interval":bootstrap,"comparative_conclusion":"withheld_incomplete_inventory" if not complete else "withheld_infrastructure_uncertainty" if unknown else "paired_results_available"}


def load_artifact_bindings(preparation: Path, inventory_path: Path) -> dict[str,tuple[str,str,str,Path]]:
    validate(preparation); freeze=read_json(preparation/"freeze_manifest.json"); aliases=read_json(preparation/"blind/alias_mapping.private.json")["mapping"]
    rebuilt=[{"alias":f"artifact-{index:02d}","row_id":row["row_id"]} for index,row in enumerate(freeze["schedule"],1)]
    if aliases != rebuilt: raise ExperimentError("frozen alias mapping drifted")
    schedule_by_row={row["row_id"]:row for row in freeze["schedule"]}; expected={item["alias"]:schedule_by_row[item["row_id"]] for item in aliases}
    doc=read_json(inventory_path); rows=doc.get("artifacts")
    if not isinstance(rows,list) or len({r.get("alias") for r in rows if isinstance(r,dict)})!=len(rows): raise ExperimentError("artifact aliases are duplicated or invalid")
    root=inventory_path.resolve().parent; result={}
    for row in rows:
        exp=expected.get(row.get("alias"))
        if exp is None or (row.get("row_id"),row.get("case_id"),row.get("arm"))!=(exp["row_id"],exp["case_id"],exp["arm"]): raise ExperimentError("artifact alias schedule binding invalid")
        entry=root/row["entrypoint"]
        if not entry.is_file() or "sha256:"+hashlib.sha256(entry.read_bytes()).hexdigest()!=row.get("artifact_sha256"): raise ExperimentError("artifact entrypoint hash invalid")
        assets=row.get("package_assets")
        if not isinstance(assets,list) or not assets: raise ExperimentError("artifact assets missing")
        seen=set()
        for asset in assets:
            path=asset.get("path")
            if not isinstance(path,str) or path in seen: raise ExperimentError("artifact asset inventory invalid")
            seen.add(path); actual=root/path
            if not actual.is_file() or "sha256:"+hashlib.sha256(actual.read_bytes()).hexdigest()!=asset.get("sha256"): raise ExperimentError("artifact asset hash invalid")
        result[row["alias"]]=(row["case_id"],row["arm"],row["artifact_sha256"],entry)
    return result


_ACTIVE_RUN: dict[str, object] = {}

def _run_experiment_unlocked(*, preparation: Path, destination: Path, model_root: Path, integrity_evidence: Path, manager_approved: bool) -> dict[str, object]:
    if manager_approved is not True: raise ExperimentError("explicit manager run approval is required")
    validated=validate(preparation); preflight=runtime_preflight(model_root,integrity_evidence)
    if not preflight["runtime_eligible"]: raise ExperimentError("high_gpu_bf16 runtime is unavailable")
    if destination.exists(): raise ExperimentError("run destination already exists")
    destination.mkdir(parents=True); started=time.monotonic(); ledger=destination/"invocation_ledger.jsonl"; calls=0
    write_once(destination/"batch_declaration.json",{"schema_version":f"{SCHEMA}.batch","preparation_identity":identity((preparation/"freeze_manifest.json").read_bytes(),f"{SCHEMA}.freeze_bytes"),"planned_rows":24,"max_generation_calls":60,"wall_seconds":21600,"single_flight":True,"automatic_retry_count":0})
    cases,_=load_and_validate_sources(); by_id={row["case_id"]:model_visible_case(row) for row in cases["cases"]}; freeze=read_json(preparation/"freeze_manifest.json")
    from req2web_inspector.canonical_run import CanonicalInspectorRunStore
    store=CanonicalInspectorRunStore(root=destination/"canonical-store",index_dir=ROOT/"data/processed/rag",model_root=model_root,integrity_evidence=integrity_evidence,profile_name=HIGH_GPU)
    artifacts=[]; _ACTIVE_RUN["store"]=store
    for scheduled in freeze["schedule"]:
        if time.monotonic()-started >= 21600: append_ledger(ledger,{"event":"batch_stopped","reason":"six_hour_cap"}); break
        case=by_id[scheduled["case_id"]]; arm=scheduled["arm"]; alias=f"artifact-{scheduled['execution_index']:02d}"
        if arm=="A":
            if calls+4>60: raise ExperimentError("generation call cap would be exceeded")
            append_ledger(ledger,{"event":"arm_a_four_call_reservation","row_id":scheduled["row_id"],"reserved_call_count":4,"automatic_retry_count":0}); calls+=4
            rec=store.create({"requirement":case["requirement"],"target_device":case["target_device"],"task_type":case["task_type"],"constraints":case["constraints"],"confirm_local_model_action":True,"run_requirement_assist":False,"run_browser_acceptance":False,"run_semantic_acceptance":False}); _ACTIVE_RUN["run_id"]=rec["run_id"]
            deadline=min(started+21600,time.monotonic()+5400)
            while rec["status"] in {"queued","running","cancel_requested"} and time.monotonic()<deadline: time.sleep(1); rec=store.get(rec["run_id"])
            if rec["status"] in {"queued","running","cancel_requested"}:
                store.cancel(rec["run_id"],{"confirm_cancel":True}); teardown=time.monotonic()+60
                while time.monotonic()<teardown and store.get(rec["run_id"])["status"] in {"queued","running","cancel_requested"}: time.sleep(.25)
                terminal=store.get(rec["run_id"])["status"]
                append_ledger(ledger,{"event":"arm_a_timeout","row_id":scheduled["row_id"],"terminal_status":terminal,"teardown_waited":True}); break
            actual=int(rec.get("canonical_summary",{}).get("model_call_ledger",{}).get("total_generate_started_count",0)); _ACTIVE_RUN.pop("run_id",None)
            calls += actual-4
            append_ledger(ledger,{"event":"arm_a_terminal","row_id":scheduled["row_id"],"reserved_call_count":4,"actual_started_call_count":actual,"status":rec["status"],"canonical_run_id":rec["run_id"]})
            package_rel=rec.get("result",{}).get("package_relative_path")
            page=destination/"canonical-store"/rec["run_id"]/str(package_rel)/"page/index.html" if package_rel else None
            if page and page.is_file():
                package=page.parent.parent; assets=[{"path":str(p.relative_to(destination)),"sha256":"sha256:"+hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(package.rglob("*")) if p.is_file()]
                artifacts.append({"alias":alias,"row_id":scheduled["row_id"],"case_id":scheduled["case_id"],"arm":"A","artifact_sha256":"sha256:"+hashlib.sha256(page.read_bytes()).hexdigest(),"entrypoint":str(page.relative_to(destination)),"selected_delivery_kind":rec.get("result",{}).get("selected_delivery_kind"),"package_assets":assets})
        else:
            if calls+1>60: raise ExperimentError("generation call cap would be exceeded")
            calls+=1; row_root=destination/"rows"/alias; row_root.mkdir(parents=True)
            append_ledger(ledger,{"event":"arm_b_call_reservation","row_id":scheduled["row_id"],"arm":"B","reserved_call_count":1,"automatic_retry_count":0})
            prompt=preparation/"prompts"/f"{case['case_id']}-B.txt"; raw=row_root/"raw_response.bin"; result=row_root/"worker_result.json"; started_record=row_root/"generation_started.json"
            cmd=[sys.executable,str(ROOT/"scripts/phase7_direct_html_worker.py"),"--model-root",str(model_root),"--integrity-evidence",str(integrity_evidence),"--prompt",str(prompt),"--raw-output",str(raw),"--started-output",str(started_record),"--result",str(result)]
            process=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)); _ACTIVE_RUN["process"]=process
            try: _,stderr=process.communicate(timeout=max(1,min(1200,started+21600-time.monotonic())))
            except subprocess.TimeoutExpired:
                process.kill(); _,stderr=process.communicate(); actual=int(started_record.is_file()); calls+=actual-1; append_ledger(ledger,{"event":"generation_failed","row_id":scheduled["row_id"],"reason":"timeout_1200_seconds","actual_started_call_count":actual,"process_reaped":True,"stderr_sha256":"sha256:"+hashlib.sha256(stderr).hexdigest()}); break
            actual=int(started_record.is_file()); calls+=actual-1
            _ACTIVE_RUN.pop("process",None)
            append_ledger(ledger,{"event":"generation_terminal","row_id":scheduled["row_id"],"actual_started_call_count":actual,"exit_code":process.returncode,"raw_formed":raw.is_file(),"stderr_sha256":"sha256:"+hashlib.sha256(stderr).hexdigest()})
            page=row_root/"page.html"
            if page.is_file(): artifacts.append({"alias":alias,"row_id":scheduled["row_id"],"case_id":scheduled["case_id"],"arm":"B","artifact_sha256":"sha256:"+hashlib.sha256(page.read_bytes()).hexdigest(),"entrypoint":str(page.relative_to(destination)),"selected_delivery_kind":"direct_html","package_assets":[{"path":str(page.relative_to(destination)),"sha256":"sha256:"+hashlib.sha256(page.read_bytes()).hexdigest()}]})
        if artifacts and artifacts[-1]["row_id"]==scheduled["row_id"]: append_ledger(destination/"artifact_inventory.jsonl",artifacts[-1])
    write_once(destination/"artifact_inventory.json",{"schema_version":f"{SCHEMA}.artifacts","artifacts":artifacts})
    summary={"schema_version":f"{SCHEMA}.run_summary","status":"generated" if len(artifacts)==24 else "incomplete","generated_rows":len(artifacts),"started_generation_calls":calls,"automatic_retry_count":0,"scoring_status":"scoring_pending"}
    write_once(destination/"machine_summary.json",summary); return summary


def run_experiment(**kwargs) -> dict[str, object]:
    if kwargs.get("manager_approved") is not True: raise ExperimentError("explicit manager run approval is required")
    preparation=Path(kwargs["preparation"]); destination=Path(kwargs["destination"])
    validate(preparation)
    if not runtime_preflight(Path(kwargs["model_root"]),Path(kwargs["integrity_evidence"]))["runtime_eligible"]: raise ExperimentError("high_gpu_bf16 runtime is unavailable")
    if destination.exists(): raise ExperimentError("run destination already exists")
    claim=DEFAULT_ROOT/"measured_batch_claim.json"
    if claim.exists(): raise ExperimentError("Experiment 1 measured batch was already claimed")
    write_once(claim,{"schema_version":f"{SCHEMA}.measured_claim","freeze_identity":identity((preparation/"freeze_manifest.json").read_bytes(),f"{SCHEMA}.freeze_bytes"),"destination":str(destination.resolve(strict=False)),"no_automatic_resume":True})
    lock=DEFAULT_ROOT/".experiment1-single-flight.lock"; lock.parent.mkdir(parents=True,exist_ok=True)
    try:
        with lock.open("xb") as handle:
            handle.write(canonical_bytes({"pid":os.getpid(),"status":"active"})); handle.flush(); os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ExperimentError("another or uncertain Experiment 1 batch exists") from exc
    try:
        result=_run_experiment_unlocked(**kwargs)
        return result
    finally:
        cleanup_ok=True
        process=_ACTIVE_RUN.get("process")
        if process is not None and process.poll() is None:
            try: process.kill(); process.communicate(timeout=30)
            except Exception: cleanup_ok=False
        store=_ACTIVE_RUN.get("store"); run_id=_ACTIVE_RUN.get("run_id")
        if store is not None and isinstance(run_id,str):
            try:
                if store.get(run_id)["status"] in {"queued","running","cancel_requested"}: store.cancel(run_id,{"confirm_cancel":True})
                deadline=time.monotonic()+60
                while time.monotonic()<deadline and store.get(run_id)["status"] in {"queued","running","cancel_requested"}: time.sleep(.25)
                if store.get(run_id)["status"] in {"queued","running","cancel_requested"} or store.is_busy(): cleanup_ok=False
            except Exception: cleanup_ok=False
        _ACTIVE_RUN.clear()
        if 'destination' in locals() and destination.is_dir():
            try:
                inventory_path=destination/"artifact_inventory.json"; stream=destination/"artifact_inventory.jsonl"
                if not inventory_path.exists() and stream.is_file(): write_once(inventory_path,{"schema_version":f"{SCHEMA}.artifacts","artifacts":[json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()]})
                schedule=read_json(preparation/"freeze_manifest.json")["schedule"]
                delivered=set()
                if inventory_path.is_file(): delivered={x["row_id"] for x in read_json(inventory_path)["artifacts"]}
                write_once(destination/"row_terminal_inventory.json",{"schema_version":f"{SCHEMA}.row_terminal_inventory","rows":[{**row,"status":"delivered" if row["row_id"] in delivered else "unfinished"} for row in schedule]})
            except Exception: cleanup_ok=False
        if not cleanup_ok and destination.is_dir():
            try: write_once(destination/"cleanup_error.json",{"status":"cleanup_uncertain","lock_retained":True})
            except Exception: pass
        if cleanup_ok:
            try: lock.unlink()
            except OSError: pass


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("prepare"); p.add_argument("--output",type=Path,default=DEFAULT_ROOT/"preparation")
    v=sub.add_parser("validate"); v.add_argument("--root",type=Path,default=DEFAULT_ROOT/"preparation")
    m=sub.add_parser("metrics"); m.add_argument("--observations",type=Path,required=True)
    m.add_argument("--artifact-inventory",type=Path,required=True)
    m.add_argument("--preparation",type=Path,required=True)
    r=sub.add_parser("run"); r.add_argument("--preparation",type=Path,required=True); r.add_argument("--output",type=Path,required=True); r.add_argument("--model-root",type=Path,required=True); r.add_argument("--integrity-evidence",type=Path,required=True); r.add_argument("--confirm-manager-approved-run",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.command=="prepare": value=prepare(args.output)
        elif args.command=="validate": value=validate(args.root)
        elif args.command=="run": value=run_experiment(preparation=args.preparation, destination=args.output, model_root=args.model_root, integrity_evidence=args.integrity_evidence, manager_approved=args.confirm_manager_approved_run)
        else:
            artifact_map=load_artifact_bindings(args.preparation,args.artifact_inventory)
            value=metrics(read_json(args.observations)["rows"],artifact_map)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status":"failed_closed","error_type":type(exc).__name__,"message":str(exc)},sort_keys=True),file=sys.stderr); return 2
    print(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))); return 0


if __name__ == "__main__":
    raise SystemExit(main())
