"""Prepare and supervise the bounded three-arm Phase 7 E1 v2 experiment."""

from __future__ import annotations

import argparse, hashlib, importlib, json, os, signal, subprocess, sys, time
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "src", ROOT / "scripts"):
    if str(item) not in sys.path: sys.path.insert(0, str(item))

from phase7_e1_v2_structured import adapt_and_render, build_structured_prompt, canonical_bytes

SCHEMA = "req2web.phase7.e1_v2.runtime.v1"
CASE_MODULE_NAME = "phase7_e1_v2_cases"
OBSERVER_MODULE_NAME = "phase7_e1_v2_observe"
SCHEDULE_SALT = "e1-v2-20260917:"
EXPECTED_DEVELOPMENT_CASES = 4
EXPECTED_MEASURED_CASES = 12
WORKER_SCRIPT = Path(__file__)
RUNTIME_SOURCE = ROOT / "scripts/phase7_e1_v2_runtime.py"
CASES_SOURCE = ROOT / "scripts/phase7_e1_v2_cases.py"
EXTRA_RUNTIME_SOURCES: tuple[Path, ...] = ()
DEVELOPMENT_GATE_SCHEMA = "req2web.phase7.e1_v2.observer.v4.development_gate.v1"
MODEL_ID = "Qwen/Qwen3.5-9B"
MODEL_REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
GLOBAL_CALL_CAP = 96
GLOBAL_WALL_SECONDS = 21600
PER_CALL_SECONDS = 1200

class RuntimeErrorClosed(ValueError): pass

def sha(raw: bytes) -> str: return "sha256:" + hashlib.sha256(raw).hexdigest()
def read(path: Path) -> object: return json.loads(path.read_text(encoding="utf-8"))
def write_once(path: Path, value: object, raw: bool=False) -> None:
    data = value if raw else canonical_bytes(value)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data: raise RuntimeErrorClosed(f"write-once drift: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f: f.write(data); f.flush(); os.fsync(f.fileno())
def append(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f: f.write(canonical_bytes(dict(value))+b"\n"); f.flush(); os.fsync(f.fileno())

def _case_module():
    return importlib.import_module(CASE_MODULE_NAME)

def _observer_identity() -> str:
    module = importlib.import_module(OBSERVER_MODULE_NAME)
    return module.observer_identity()

def _cases() -> list[dict[str, object]]:
    rows = _case_module().cases()
    expected_total=EXPECTED_DEVELOPMENT_CASES+EXPECTED_MEASURED_CASES
    if not isinstance(rows, list) or len(rows) != expected_total: raise RuntimeErrorClosed("study case inventory is invalid")
    required = {"case_id","split","family","requirement","target_device","task_type","constraints"}
    if any(type(row) is not dict or set(row) != required for row in rows): raise RuntimeErrorClosed("public case shape drifted")
    if len({str(row["case_id"]) for row in rows}) != expected_total or sum(row["split"]=="development" for row in rows)!=EXPECTED_DEVELOPMENT_CASES or sum(row["split"]=="measured" for row in rows)!=EXPECTED_MEASURED_CASES: raise RuntimeErrorClosed("case split inventory drifted")
    return rows

def _schedule(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    result=[]
    orders=("ABC","BCA","CAB")
    for split in ("development","measured"):
        selected=[row for row in rows if row["split"]==split]
        selected.sort(key=lambda row: hashlib.sha256((SCHEDULE_SALT+str(row["case_id"])).encode()).hexdigest())
        for index,row in enumerate(selected):
            for arm in orders[index%3]:
                result.append({"split":split,"case_id":row["case_id"],"family":row["family"],"arm":arm,"row_id":f"{row['case_id']}:{arm}","call_cap":4 if arm=="A" else 1})
    return result

def _direct_prompt(case: Mapping[str, object]) -> str:
    return "Create one complete self-contained offline HTML/CSS/JavaScript workflow prototype. Use no external dependencies or network calls. Return only the HTML document, without markdown or explanation. Implement every public behavior explicitly.\n\nINPUT_JSON:\n"+canonical_bytes(dict(case)).decode()

def prepare(output: Path) -> dict[str, object]:
    if output.exists(): raise RuntimeErrorClosed("preparation destination already exists")
    rows=_cases(); schedule=_schedule(rows)
    output.mkdir(parents=True)
    from req2web_inspector.canonical_run import _build_upstream
    prompt_rows=[]
    for case in rows:
        owned={**case,"request_id":f"request-{case['case_id']}"}; owned.pop("split"); owned.pop("family")
        upstream=_build_upstream(case=owned,index_dir=ROOT/"data/processed/rag",output_root=output/"upstream"/str(case["case_id"]))
        prompts={"B":_direct_prompt(case),"C":build_structured_prompt(upstream["adaptation"].b_input)}
        for arm,text in prompts.items():
            rel=f"prompts/{case['case_id']}-{arm}.txt"; write_once(output/rel,text.encode(),raw=True)
            prompt_rows.append({"case_id":case["case_id"],"arm":arm,"path":rel,"sha256":sha(text.encode())})
    sources=[RUNTIME_SOURCE,*EXTRA_RUNTIME_SOURCES,ROOT/"scripts/phase7_e1_v2_structured.py",CASES_SOURCE,ROOT/"scripts/phase7_direct_html_worker.py",ROOT/"src/req2web_orchestration/phase4_graph.py",ROOT/"src/req2web_generation/renderer.py",ROOT/"src/req2web_generation/consistency.py",ROOT/"src/req2web_agent/prompt_authority.py",ROOT/"src/req2web_agent/understanding.py",ROOT/"src/req2web_capabilities.py"]
    from req2web_agent import PROMPT_AUTHORITY_IDENTITY, PROMPT_AUTHORITY_REVISION
    freeze={"schema_version":f"{SCHEMA}.freeze","input_identity":_case_module().frozen_input_identity(),"observer_identity":_observer_identity(),"prompt_revision":PROMPT_AUTHORITY_REVISION,"prompt_authority":PROMPT_AUTHORITY_IDENTITY,"model":{"id":MODEL_ID,"revision":MODEL_REVISION,"dtype":"bf16","quantization":"none","do_sample":False,"thinking":False},"limits":{"development_calls":24,"measured_calls":72,"total_calls":96,"per_call_seconds":PER_CALL_SECONDS,"global_wall_seconds":GLOBAL_WALL_SECONDS,"automatic_retry_count":0},"schedule":schedule,"prompts":prompt_rows,"sources":{p.relative_to(ROOT).as_posix():sha(p.read_bytes()) for p in sources}}
    write_once(output/"freeze.json",freeze)
    summary={"status":"prepared_no_model","development_rows":12,"measured_rows":36,"planned_calls":96,"model_loaded":False,"run_occurred":False}
    write_once(output/"summary.json",summary); return summary

def validate(preparation: Path) -> dict[str, object]:
    freeze=read(preparation/"freeze.json"); rows=_cases()
    from req2web_agent import PROMPT_AUTHORITY_IDENTITY, PROMPT_AUTHORITY_REVISION
    if freeze.get("input_identity") != _case_module().frozen_input_identity() or freeze.get("schedule") != _schedule(rows): raise RuntimeErrorClosed("input or schedule freeze drifted")
    if freeze.get("prompt_revision") != PROMPT_AUTHORITY_REVISION or freeze.get("prompt_authority") != PROMPT_AUTHORITY_IDENTITY: raise RuntimeErrorClosed("prompt authority freeze drifted")
    if freeze.get("limits") != {"development_calls":24,"measured_calls":72,"total_calls":96,"per_call_seconds":1200,"global_wall_seconds":21600,"automatic_retry_count":0}: raise RuntimeErrorClosed("limits drifted")
    for rel,digest in freeze["sources"].items():
        if sha((ROOT/rel).read_bytes()) != digest: raise RuntimeErrorClosed("source drifted: "+rel)
    for row in freeze["prompts"]:
        if sha((preparation/row["path"]).read_bytes()) != row["sha256"]: raise RuntimeErrorClosed("prompt drifted")
    return {"status":"validated_no_action","rows":len(freeze["schedule"]),"calls":sum(x["call_cap"] for x in freeze["schedule"])}

def _inventory(root: Path) -> list[dict[str, object]]:
    return [{"path":p.relative_to(root).as_posix(),"sha256":sha(p.read_bytes())} for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()]

def _upstream(case: Mapping[str, object], root: Path):
    from req2web_inspector.canonical_run import _build_upstream
    owned=dict(case); owned.pop("split",None); owned.pop("family",None); owned["request_id"]=f"request-{case['case_id']}"
    return _build_upstream(case=owned,index_dir=ROOT/"data/processed/rag",output_root=root)

def _canonical_metrics(run_root: Path) -> dict[str, object]:
    rows=[]
    for path in sorted(run_root.glob("canonical/attempts/*/attempt_result.json")):
        try:
            value=read(path)
            if value.get("generate_started") is True and isinstance(value.get("metrics"),dict):
                rows.append({"node_id":value.get("node_id"),**value["metrics"]})
        except (OSError,ValueError,TypeError):
            continue
    return {"per_node":rows,"input_tokens":sum(int(row.get("input_token_length",0)) for row in rows),"output_tokens":sum(int(row.get("output_token_length",0)) for row in rows)}

def worker(*, arm: str, case: Mapping[str, object], output: Path, model_root: Path, integrity: Path, prompt: Path) -> dict[str, object]:
    output.mkdir(parents=True,exist_ok=False)
    if arm=="A":
        from req2web_inspector.canonical_run import CanonicalInspectorRunStore
        store=CanonicalInspectorRunStore(root=output/"store",index_dir=ROOT/"data/processed/rag",model_root=model_root,integrity_evidence=integrity,profile_name="high_gpu_bf16")
        rec=store.create({"requirement":case["requirement"],"target_device":case["target_device"],"task_type":case["task_type"],"constraints":case["constraints"],"confirm_local_model_action":True,"run_requirement_assist":False,"run_browser_acceptance":False,"run_semantic_acceptance":False})
        rec=store.wait(rec["run_id"])
        rel=rec.get("result",{}).get("package_relative_path"); entry=None if not rel else (Path("store")/rec["run_id"]/rel/"page/index.html").as_posix()
        result={"status":rec["status"],"calls":int(rec.get("canonical_summary",{}).get("model_call_ledger",{}).get("total_generate_started_count",0)),"entrypoint":entry,"metrics":_canonical_metrics(output/"store"/rec["run_id"])}
    else:
        from phase7_direct_html_worker import generate_direct_html
        generation=generate_direct_html(model_root=model_root,integrity_evidence=integrity,prompt=prompt.read_text(encoding="utf-8"),raw_path=output/"raw_response.bin",started_path=output/"generation_started.json")
        if arm=="B":
            if generation["html"] is None: raise RuntimeErrorClosed("direct HTML protocol failed")
            write_once(output/"page.html",generation["html"],raw=True); result={"status":"direct_html_ready","calls":1,"entrypoint":"page.html","metrics":{key:generation[key] for key in ("input_tokens","output_tokens","load_seconds","generation_seconds","transport_extraction")}}
        else:
            upstream=_upstream(case,output/"upstream")
            structured=adapt_and_render(raw=generation["raw"],b_input=upstream["adaptation"].b_input,context=upstream["context"],guidance=upstream["guidance"],output_root=output/"structured")
            result={"status":structured["status"],"calls":1,"entrypoint":"structured/page/index.html" if structured["status"]=="structured_artifact_ready" else None,"metrics":{key:generation[key] for key in ("input_tokens","output_tokens","load_seconds","generation_seconds","transport_extraction")}}
    write_once(output/"worker-result.json",result); return result

def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None: return
    if os.name=="nt": subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
    else:
        try: os.killpg(proc.pid,signal.SIGTERM); proc.wait(10)
        except (ProcessLookupError,subprocess.TimeoutExpired):
            try: os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
    try: proc.wait(30)
    except subprocess.TimeoutExpired: proc.kill(); proc.wait()

def _gate(receipt: Path, freeze: Mapping[str, object], freeze_sha: str) -> dict[str, object]:
    value=read(receipt)
    required={"schema_version":DEVELOPMENT_GATE_SCHEMA,"status":"development_gate_passed","freeze_sha256":freeze_sha,"input_identity":freeze["input_identity"],"observer_identity":freeze["observer_identity"],"actual_rendering_capability":True,"usable_protocol_output":True,"observation_operable":True,"development_rows":EXPECTED_DEVELOPMENT_CASES*3,"quality_pass_required":True,"quality_passed":True}
    if any(value.get(k)!=v for k,v in required.items()): raise RuntimeErrorClosed("development browser gate receipt is invalid")
    expected_policy={"arm":"A","minimum_total_passed_obligations":20,"minimum_passed_obligations_per_case":4,"required_available_deliveries":4,"minimum_completed_model_deliveries":3,"unknown_rows_allowed":0}
    if value.get("quality_policy") != expected_policy: raise RuntimeErrorClosed("development quality policy drifted")
    observed=value.get("quality_observed")
    if not isinstance(observed,dict) or observed.get("total_passed_obligations",-1)<20 or observed.get("available_deliveries")!=4 or observed.get("completed_model_deliveries",-1)<3 or observed.get("unknown_rows")!=0: raise RuntimeErrorClosed("development quality result is ineligible")
    per_case=observed.get("passed_obligations_per_case")
    if not isinstance(per_case,dict) or len(per_case)!=4 or any(type(count) is not int or count<4 for count in per_case.values()): raise RuntimeErrorClosed("development per-case quality is ineligible")
    files=value.get("files")
    if not isinstance(files,list) or not files: raise RuntimeErrorClosed("development browser gate lacks bound evidence inventory")
    base=receipt.resolve().parent
    for row in files:
        path=(base/row["path"]).resolve(strict=True)
        if base not in path.parents or sha(path.read_bytes())!=row["sha256"]: raise RuntimeErrorClosed("development browser evidence binding drifted")
    if type(value.get("development_started_calls")) is not int or not 0 <= value["development_started_calls"] <= 24: raise RuntimeErrorClosed("development started-call count is invalid")
    available=value.get("development_available_deliveries"); failures=value.get("development_terminal_product_failures")
    if type(available) is not int or type(failures) is not int or available < 0 or failures < 0 or available+failures != 12: raise RuntimeErrorClosed("development delivery accounting is invalid")
    declared={row["path"] for row in files}
    role_paths={key:value.get(key) for key in ("freeze_path","development_runtime_manifest_path","observation_path")}
    if any(type(path) is not str or path not in declared for path in role_paths.values()): raise RuntimeErrorClosed("development gate role binding is invalid")
    bound_freeze=(base/str(role_paths["freeze_path"])).resolve(strict=True)
    bound_manifest=(base/str(role_paths["development_runtime_manifest_path"])).resolve(strict=True)
    bound_observation=(base/str(role_paths["observation_path"])).resolve(strict=True)
    manifest=read(bound_manifest); observation=read(bound_observation)
    if sha(bound_freeze.read_bytes())!=freeze_sha or manifest.get("split")!="development" or manifest.get("freeze_sha256")!=freeze_sha or manifest.get("started_calls")!=value["development_started_calls"]: raise RuntimeErrorClosed("development runtime gate binding is invalid")
    if observation.get("status")!="observation_complete" or observation.get("split")!="development" or observation.get("input_identity")!=freeze["input_identity"] or observation.get("observer_identity")!=freeze["observer_identity"] or observation.get("artifact_manifest_sha256")!=sha(bound_manifest.read_bytes()): raise RuntimeErrorClosed("development observation gate binding is invalid")
    return value

def _actual_started_calls(row_root: Path, worker_result: Mapping[str, object]) -> int:
    if type(worker_result.get("calls")) is int and 0 <= int(worker_result["calls"]) <= 4:
        return int(worker_result["calls"])
    counts=[]
    if row_root.exists():
        for path in row_root.rglob("model_call_ledger.json"):
            try:
                value=read(path); count=value.get("total_generate_started_count")
                if type(count) is int and 0 <= count <= 4: counts.append(count)
            except (OSError, ValueError, TypeError):
                continue
        direct=sum(1 for path in row_root.rglob("generation_started.json") if path.is_file())
        counts.append(direct)
    return max(counts,default=0)

def run(*, split: str, preparation: Path, output: Path, model_root: Path, integrity: Path, claim_root: Path|None=None, browser_receipt: Path|None, approved: bool) -> dict[str, object]:
    if approved is not True: raise RuntimeErrorClosed("explicit run approval required")
    validate(preparation)
    if split not in {"development","measured"}: raise RuntimeErrorClosed("invalid split")
    from req2web_runtime.phase4_local_qwen import validate_model_inventory_metadata
    inventory=validate_model_inventory_metadata(model_root=model_root.resolve(strict=True),integrity_evidence=integrity.resolve(strict=True),allow_relocated_model_root=True)
    freeze_path=preparation/"freeze.json"; freeze_raw=freeze_path.read_bytes(); freeze=read(freeze_path); freeze_sha=sha(freeze_raw); development_started_calls=0
    if split=="measured":
        if browser_receipt is None: raise RuntimeErrorClosed("measured split requires imported development browser receipt")
        gate=_gate(browser_receipt,freeze,freeze_sha); development_started_calls=int(gate["development_started_calls"])
    fixed_claim_root=preparation.resolve().parent/"claims"; prior_development_claim=None
    if split=="measured":
        development_claim_path=fixed_claim_root/"development.claim.json"
        if not development_claim_path.is_file(): raise RuntimeErrorClosed("measured split requires the server-side development claim")
        prior_development_claim=read(development_claim_path)
        if prior_development_claim.get("freeze_sha256")!=freeze_sha: raise RuntimeErrorClosed("development claim binding drifted")
    if claim_root is not None and claim_root.resolve()!=fixed_claim_root: raise RuntimeErrorClosed("claim root is fixed by the preparation")
    claim=fixed_claim_root/f"{split}.claim.json"
    if claim.exists(): raise RuntimeErrorClosed(f"{split} split was already or uncertainly claimed")
    if output.exists(): raise RuntimeErrorClosed("output already exists")
    now=time.time(); write_once(claim,{"split":split,"freeze_sha256":freeze_sha,"output":str(output.resolve()),"no_retry_or_resume":True,"claimed_unix_seconds":now})
    global_started=now if prior_development_claim is None else float(prior_development_claim["claimed_unix_seconds"])
    output.mkdir(parents=True); started=time.monotonic(); rows=[x for x in freeze["schedule"] if x["split"]==split]; by_id={x["case_id"]:x for x in _cases()}; terminals=[]; calls=0; reserved_calls=0; stop=None
    for index,row in enumerate(rows):
        remaining=min(GLOBAL_WALL_SECONDS-(time.monotonic()-started),GLOBAL_WALL_SECONDS-(time.time()-global_started))
        if remaining<=0: stop="global_wall_cap"; break
        if calls+row["call_cap"] > (24 if split=="development" else 72): stop="split_call_cap"; break
        if development_started_calls+calls+row["call_cap"] > GLOBAL_CALL_CAP: stop="global_call_cap"; break
        row_root=output/"rows"/f"{index+1:02d}-{row['case_id']}-{row['arm']}"; case_path=output/"inputs"/f"{row['case_id']}.json"; write_once(case_path,by_id[row["case_id"]])
        prompt=preparation/"prompts"/f"{row['case_id']}-{row['arm']}.txt" if row["arm"]!="A" else preparation/"freeze.json"
        reservation={**row,"event":"generation_call_reservation","reserved_call_count":row["call_cap"],"automatic_retry_count":0,"unix_seconds":time.time()}; append(output/"attempt_ledger.jsonl",reservation); reserved_calls+=int(row["call_cap"])
        cmd=[sys.executable,str(WORKER_SCRIPT),"worker","--arm",row["arm"],"--case",str(case_path),"--output",str(row_root),"--model-root",str(model_root),"--integrity-evidence",str(integrity),"--prompt",str(prompt)]; row_started=time.monotonic()
        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=(os.name!="nt"),creationflags=(getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0) if os.name=="nt" else 0)); timeout=min(remaining,PER_CALL_SECONDS*(4 if row["arm"]=="A" else 1))
        try: stdout,stderr=proc.communicate(timeout=max(1,timeout))
        except subprocess.TimeoutExpired: _terminate(proc); stdout,stderr=proc.communicate(); stop="row_timeout"
        wr=read(row_root/"worker-result.json") if (row_root/"worker-result.json").is_file() else {}
        actual=_actual_started_calls(row_root,wr); calls+=actual
        row_root.mkdir(parents=True,exist_ok=True); write_once(row_root/"supervisor.stdout.bin",stdout,raw=True); write_once(row_root/"supervisor.stderr.bin",stderr,raw=True)
        files=[] if not row_root.exists() else [{"path":(row_root/path["path"]).relative_to(output).as_posix(),"sha256":path["sha256"]} for path in _inventory(row_root)]
        entry=None if not wr.get("entrypoint") else (row_root/str(wr["entrypoint"])).relative_to(output).as_posix()
        terminal={**row,"status":wr.get("status","failed_or_interrupted"),"started_calls":actual,"exit_code":proc.returncode,"entrypoint":entry,"elapsed_seconds":round(time.monotonic()-row_started,3),"metrics":wr.get("metrics",{}),"stderr_sha256":sha(stderr),"files":files}
        terminals.append(terminal); append(output/"terminal_rows.jsonl",terminal)
        if stop: break
    done={x["row_id"] for x in terminals}
    terminals.extend([{**row,"status":"unstarted","started_calls":0,"exit_code":None,"entrypoint":None,"elapsed_seconds":0.0,"metrics":{},"files":[]} for row in rows if row["row_id"] not in done])
    manifest={"schema_version":f"{SCHEMA}.return_inventory","split":split,"freeze_sha256":freeze_sha,"model_inventory_identity":inventory["inventory_identity"],"rows":terminals,"started_calls":calls,"reserved_calls":reserved_calls,"development_started_calls":development_started_calls,"global_started_calls":development_started_calls+calls,"call_cap":24 if split=="development" else 72,"global_call_cap":GLOBAL_CALL_CAP,"automatic_retry_count":0,"stop_reason":stop}
    write_once(output/"return_inventory.json",manifest)
    summary={"status":"complete" if all(x["status"]!="unstarted" for x in terminals) and stop is None else "partial","split":split,"terminal_rows":len(terminals),"started_calls":calls,"stop_reason":stop}
    write_once(output/"summary.json",summary); return summary

def main(argv=None) -> int:
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    a=sub.add_parser("prepare"); a.add_argument("--output",type=Path,required=True)
    a=sub.add_parser("validate"); a.add_argument("--preparation",type=Path,required=True)
    a=sub.add_parser("run"); a.add_argument("--split",choices=("development","measured"),required=True); a.add_argument("--preparation",type=Path,required=True); a.add_argument("--output",type=Path,required=True); a.add_argument("--model-root",type=Path,required=True); a.add_argument("--integrity-evidence",type=Path,required=True); a.add_argument("--development-browser-receipt",type=Path); a.add_argument("--confirm-approved-run",action="store_true")
    a=sub.add_parser("worker"); a.add_argument("--arm",choices=("A","B","C"),required=True); a.add_argument("--case",type=Path,required=True); a.add_argument("--output",type=Path,required=True); a.add_argument("--model-root",type=Path,required=True); a.add_argument("--integrity-evidence",type=Path,required=True); a.add_argument("--prompt",type=Path,required=True)
    args=p.parse_args(argv)
    try:
        if args.cmd=="prepare": result=prepare(args.output)
        elif args.cmd=="validate": result=validate(args.preparation)
        elif args.cmd=="worker": result=worker(arm=args.arm,case=read(args.case),output=args.output,model_root=args.model_root,integrity=args.integrity_evidence,prompt=args.prompt)
        else: result=run(split=args.split,preparation=args.preparation,output=args.output,model_root=args.model_root,integrity=args.integrity_evidence,browser_receipt=args.development_browser_receipt,approved=args.confirm_approved_run)
    except Exception as exc: print(json.dumps({"status":"failed_closed","error_type":type(exc).__name__,"message":str(exc)},sort_keys=True),file=sys.stderr); return 2
    print(json.dumps(result,sort_keys=True,separators=(",",":"))); return 0
if __name__=="__main__": raise SystemExit(main())
