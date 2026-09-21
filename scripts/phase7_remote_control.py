"""Thin, no-retry remote controller for the approved Phase 7 Experiment 1."""
from __future__ import annotations
import argparse, ctypes, hashlib, json, os, signal, subprocess, sys, tarfile, time, uuid
from pathlib import Path, PurePosixPath

ROOT=Path(__file__).resolve().parents[1]
SCHEMA="req2web.phase7.autodl.remote_control.v1"
FORBIDDEN=("model.safetensors","tokenizer.json","credential","secret",".env","id_rsa")

class ControlError(ValueError): pass
def canon(v): return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def digest(raw): return "sha256:"+hashlib.sha256(raw).hexdigest()
def read(path):
    v=json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(v,dict): raise ControlError("JSON document must be an object")
    return v
def write_once(path,value):
    path=Path(path); raw=canon(value); path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=raw: raise ControlError(f"write-once drift: {path.name}")
        return
    with path.open("xb") as h: h.write(raw); h.flush(); os.fsync(h.fileno())

def validate_config(v):
    required={"schema_version","instance","ssh","repository","python","experiment","rental","transfer","claims","approvals"}
    if set(v)!=required or v["schema_version"]!=f"{SCHEMA}.config": raise ControlError("config keys/schema invalid")
    exp=v["experiment"]
    if exp.get("profile")!="high_gpu_bf16" or exp.get("max_generation_calls")!=60 or exp.get("batch_wall_seconds")!=21600 or exp.get("automatic_retry_count")!=0: raise ControlError("experiment boundary drifted")
    if v["claims"]!={"h1":False,"formal_quality":False}: raise ControlError("claim boundary drifted")
    if set(v["approvals"])!={"manager_run_approved","user_instance_started"} or any(type(x) is not bool for x in v["approvals"].values()): raise ControlError("approval fields invalid")
    ssh=v["ssh"]; rental=v["rental"]
    if not isinstance(ssh.get("port"),int) or not 1<=ssh["port"]<=65535 or any(not isinstance(ssh.get(k),str) or not ssh[k] for k in ("host","user","host_fingerprint_sha256")): raise ControlError("SSH fields invalid")
    if not isinstance(rental.get("rental_time_cap_seconds"),int) or rental["rental_time_cap_seconds"]<=0 or not isinstance(rental.get("hourly_price"),(int,float)) or rental["hourly_price"]<=0 or not isinstance(rental.get("cost_cap"),(int,float)) or rental["cost_cap"]<=0 or not isinstance(rental.get("currency"),str) or not rental["currency"]: raise ControlError("rental caps invalid")
    for group,keys in {"instance":("id","provider","region"),"repository":("expected_commit","remote_root","upload_manifest_sha256","payload_manifest_path"),"python":("executable",),"transfer":("return_local_root",)}.items():
        if any(not isinstance(v[group].get(k),str) or not v[group][k] for k in keys): raise ControlError(f"{group} action-time values missing")
    return v

def preflight(config,mode):
    c=validate_config(config); exp=c["experiment"]; checks={}
    for key in ("preparation_root","model_root","integrity_evidence"):
        p=Path(exp[key]); checks[key]={"path":str(p),"exists":p.exists()}
    py=Path(c["python"]["executable"]); checks["python"]={"path":str(py),"exists":py.is_file()}
    packages={}
    if py.is_file():
        proc=subprocess.run([str(py),"-c","import importlib.metadata as m,json; print(json.dumps({x:m.version(x) for x in ('torch','transformers','accelerate','safetensors','huggingface-hub','tokenizers','langgraph')}))"],capture_output=True,text=True,timeout=30)
        packages={"exit_code":proc.returncode,"stdout":proc.stdout.strip(),"stderr":proc.stderr.strip()}
        try:
            versions=json.loads(proc.stdout); profile=read(Path(c["repository"]["remote_root"])/"runtime/runtime_profile.json"); pins={**profile["core_pins"],"langgraph":profile["langgraph"]}
            packages["pins_match"]=all(versions.get(k)==v for k,v in pins.items()) and set(pins)=={"torch","transformers","accelerate","safetensors","huggingface-hub","tokenizers","langgraph"}
            packages["versions"]=versions
        except Exception: packages["pins_match"]=False
    payload=Path(c["repository"]["payload_manifest_path"])
    payload_ok=False
    if payload.is_file():
        raw=payload.read_bytes(); expected=c["repository"]["upload_manifest_sha256"].removeprefix("sha256:")
        manifest=json.loads(raw); rows=manifest.get("files"); payload_ok=hashlib.sha256(raw).hexdigest()==expected and manifest.get("schema_version")=="req2web.phase7.autodl.payload.v1" and manifest.get("source_commit")==c["repository"]["expected_commit"] and isinstance(rows,list) and bool(rows)
        if payload_ok:
            base=Path(c["repository"]["remote_root"])
            seen=set()
            for row in rows:
                rel=row["path"]; safe=isinstance(rel,str) and rel not in seen and not Path(rel).is_absolute() and ".." not in Path(rel).parts; seen.add(rel)
                p=(base/rel).resolve(strict=False); payload_ok=payload_ok and safe and base.resolve() in p.parents and p.is_file() and not p.is_symlink() and p.stat().st_size==row["byte_length"] and hashlib.sha256(p.read_bytes()).hexdigest()==row["sha256"].removeprefix("sha256:")
            freeze=Path(exp["preparation_root"])/"freeze_manifest.json"
            payload_ok=payload_ok and freeze.is_file() and hashlib.sha256(freeze.read_bytes()).hexdigest()==exp["expected_freeze_sha256"].removeprefix("sha256:")
            validation=subprocess.run([str(py),str(base/"scripts/phase7_experiment1.py"),"validate","--root",exp["preparation_root"]],capture_output=True,text=True,timeout=60) if py.is_file() else None
            payload_ok=payload_ok and validation is not None and validation.returncode==0
    checks["payload_manifest"]={"path":str(payload),"exact_inventory_valid":payload_ok}
    runtime_check = {"ready": False, "status": "not_checked_invalid_payload"}
    if payload_ok and py.is_file():
        base=Path(c["repository"]["remote_root"])
        diagnostic_root=Path(exp["run_root"]).parent / ("e1-model-free-preflight-" + uuid.uuid4().hex)
        proc=subprocess.run([str(py),str(base/"scripts/phase7_e1_model_free_preflight.py"),"--preparation",exp["preparation_root"],"--output",str(diagnostic_root)],capture_output=True,text=True,timeout=90)
        runtime_check={"ready": False, "exit_code":proc.returncode,"output_root":str(diagnostic_root),"stderr":proc.stderr.strip()}
        if proc.returncode == 0:
            runtime_check.update(json.loads(proc.stdout))
    checks["canonical_model_free_prerequisites"] = runtime_check
    gpu={"checked":False}
    if mode=="run-ready" and py.is_file():
        proc=subprocess.run([str(py),"-c","import json,torch; f,t=torch.cuda.mem_get_info(0) if torch.cuda.is_available() else (0,0); print(json.dumps({'available':torch.cuda.is_available(),'count':torch.cuda.device_count(),'free':f,'total':t,'name':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))"],capture_output=True,text=True,timeout=30)
        gpu={"checked":True,"exit_code":proc.returncode,"facts":json.loads(proc.stdout) if proc.returncode==0 else None,"stderr":proc.stderr.strip()}
    ready=all(checks[x]["exists"] for x in ("preparation_root","model_root","integrity_evidence","python")) and payload_ok and packages.get("exit_code")==0 and packages.get("pins_match") is True and runtime_check["ready"] is True
    if mode=="run-ready":
        facts=gpu.get("facts") or {}; ready=ready and facts.get("total",0)>=30_000_000_000 and facts.get("free",0)>=24_000_000_000
    return {"schema_version":f"{SCHEMA}.preflight","mode":mode,"ready":ready,"model_loaded":False,"weights_hashed":False,"network_used":False,"checks":checks,"packages":packages,"gpu":gpu}

def launch(config_path,evidence_root):
    c=validate_config(read(config_path))
    if c["approvals"]!={"manager_run_approved":True,"user_instance_started":True}: raise ControlError("launch approval missing")
    if not sys.platform.startswith("linux"): raise ControlError("launch requires Linux process supervision")
    if Path(c["repository"]["remote_root"]).resolve()!=ROOT: raise ControlError("launch must use the verified payload controller")
    facts=preflight(c,"run-ready")
    if not facts["ready"]: raise ControlError("run-ready preflight failed")
    root=Path(evidence_root)
    if root.exists(): raise ControlError("launch evidence root already exists")
    root.mkdir(parents=True); write_once(root/"preflight.json",facts); write_once(root/"config.json",c)
    exp=c["experiment"]; cmd=["bash",str(ROOT/"scripts/phase7_autodl_launch.sh"),c["python"]["executable"],str(ROOT/"scripts/phase7_remote_control.py"),str(ROOT/"scripts/phase7_experiment1.py"),exp["preparation_root"],exp["run_root"],exp["model_root"],exp["integrity_evidence"],str(root)]
    with (root/"supervisor.log").open("xb") as stream:
        proc=subprocess.Popen(cmd,start_new_session=True,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT)
    write_once(root/"launch.json",{"schema_version":f"{SCHEMA}.launch","pid":proc.pid,"proc_start_id":_proc_start(proc.pid),"command":cmd,"automatic_retry_count":0,"started_at_unix":int(time.time())})
    return {"status":"launched","pid":proc.pid,"evidence_root":str(root)}

def _proc_info(pid):
    path=Path(f"/proc/{pid}/stat")
    try: raw=path.read_text(encoding="utf-8")
    except FileNotFoundError: return None
    # Linux comm can contain spaces and parentheses; fields after the last ')'
    # begin at state (field 3), with starttime at field 22.
    parts=raw.rsplit(")",1)[1].split()
    return {"state":parts[0],"ppid":int(parts[1]),"start":parts[19]}

def _proc_start(pid):
    info=_proc_info(pid)
    return None if info is None else info["start"]

def _enable_subreaper():
    if not sys.platform.startswith("linux"): raise ControlError("supervisor requires Linux")
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(36,1,0,0,0)!=0: raise OSError(ctypes.get_errno(),"cannot enable process-local child subreaper")

def _descendants(root_pid):
    snapshot={}
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            info=_proc_info(int(path.name))
            if info is not None: snapshot[int(path.name)]=info
    parents={root_pid}; found={}; changed=True
    while changed:
        changed=False
        for pid,info in snapshot.items():
            if info["ppid"] in parents and pid not in parents:
                parents.add(pid); found[pid]=info["start"]; changed=True
    return found

def _cleanup_owned(proc,tracked):
    """Reap the E1 process and adopted descendants, including other sessions."""
    deadline=time.monotonic()+15
    forced=[]
    while True:
        tracked.update(_descendants(os.getpid()))
        for pid,start in list(tracked.items()):
            info=_proc_info(pid)
            if info is None or info["start"]!=start: continue
            if info["state"]!="Z":
                try: os.kill(pid,getattr(signal,"SIGKILL",9)); forced.append(pid)
                except ProcessLookupError: pass
        if proc.poll() is not None: proc.wait()
        # Adopted zombie children cannot be collected through Popen.
        for pid,start in list(tracked.items()):
            if pid==proc.pid: continue
            info=_proc_info(pid)
            if info is not None and info["start"]==start and info["ppid"]==os.getpid():
                try: os.waitpid(pid,os.WNOHANG)
                except ChildProcessError: pass
        remaining={pid:start for pid,start in tracked.items() if _proc_start(pid)==start}
        if not remaining and proc.poll() is not None:
            return {"verified":True,"forced_pids":sorted(set(forced))}
        if time.monotonic()>=deadline:
            return {"verified":False,"forced_pids":sorted(set(forced)),"remaining_pids":sorted(remaining)}
        time.sleep(.1)

def supervise(python_bin,runner,preparation,run_root,model_root,integrity,evidence_root):
    _enable_subreaper()
    root=Path(evidence_root); log=root/"run.log"; canceled={"reason":None}; proc=None; tracked={}; cleanup={"verified":False}; error=None
    def request(reason):
        canceled["reason"]=reason
        if proc is not None and proc.poll() is None:
            try: os.kill(proc.pid,signal.SIGINT)
            except OSError: pass
    old_int=signal.signal(signal.SIGINT,lambda *_:request("operator_interrupt")); old_term=signal.signal(signal.SIGTERM,lambda *_:request("operator_terminate")); old_hup=signal.signal(signal.SIGHUP,signal.SIG_IGN) if hasattr(signal,"SIGHUP") else None
    try:
        cmd=[python_bin,runner,"run","--preparation",preparation,"--output",run_root,"--model-root",model_root,"--integrity-evidence",integrity,"--confirm-manager-approved-run"]
        with log.open("xb") as stream:
            proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True); start_id=_proc_start(proc.pid)
            if start_id is not None: tracked[proc.pid]=start_id
            write_once(root/"supervisor.json",{"schema_version":f"{SCHEMA}.supervisor","child_pid":proc.pid,"proc_start_id":start_id,"command":cmd,"timeout_seconds":21600,"cleanup_grace_seconds":60,"forced_cleanup_seconds":15,"linux_subreaper":True,"automatic_retry_count":0})
            deadline=time.monotonic()+21600
            while proc.poll() is None and canceled["reason"] is None and time.monotonic()<deadline: tracked.update(_descendants(os.getpid())); time.sleep(1)
            if proc.poll() is None and canceled["reason"] is None: request("batch_timeout")
            if canceled["reason"] is not None:
                grace=time.monotonic()+60
                while proc.poll() is None and time.monotonic()<grace:
                    tracked.update(_descendants(os.getpid())); time.sleep(.25)
    except BaseException as exc:
        error=f"{type(exc).__name__}: {exc}"
    finally:
        if proc is not None:
            try: cleanup=_cleanup_owned(proc,tracked)
            except Exception as exc: cleanup={"verified":False,"error":f"{type(exc).__name__}: {exc}"}
        code=None if proc is None else proc.poll()
        write_once(root/"exit.json",{"schema_version":f"{SCHEMA}.exit","exit_code":code,"cancel_reason":canceled["reason"],"supervisor_error":error,"cleanup_verified":cleanup["verified"],"cleanup":cleanup,"tracked_descendants":[{"pid":pid,"start_id":value} for pid,value in sorted(tracked.items())],"automatic_retry_count":0})
        signal.signal(signal.SIGINT,old_int); signal.signal(signal.SIGTERM,old_term)
        if old_hup is not None: signal.signal(signal.SIGHUP,old_hup)
    return code if cleanup["verified"] and error is None and code is not None else 2

def interrupt(root):
    root=Path(root); launch=read(root/"launch.json"); pid=int(launch["pid"])
    if not launch.get("proc_start_id") or _proc_start(pid)!=launch["proc_start_id"]: raise ControlError("supervisor process identity drifted")
    os.kill(pid,signal.SIGTERM)
    write_once(root/"interrupt.json",{"status":"requested","pid":pid,"automatic_retry":False})
    return {"status":"interrupt_requested","pid":pid}

def package_results(run_root,output,evidence_root=None):
    if Path(run_root).is_symlink(): raise ControlError("linked run root is forbidden")
    root=Path(run_root).resolve(strict=True); output=Path(output).absolute()
    if output.exists(): raise ControlError("return archive exists")
    if root==output.parent or root in output.parents: raise ControlError("return archive must be outside the run root")
    if not (root/"batch_declaration.json").is_file() or not (root/"row_terminal_inventory.json").is_file() or len(read(root/"row_terminal_inventory.json").get("rows",[]))!=24: raise ControlError("run root is not a complete Experiment 1 result root")
    rows=[]; sources={}
    for p in sorted(root.rglob("*")):
        if p.is_symlink(): raise ControlError("result symlink is forbidden")
        if p.is_file():
            rel=p.relative_to(root).as_posix()
            if any(x.lower() in rel.lower() for x in FORBIDDEN): raise ControlError("forbidden return material")
            rows.append({"path":rel,"bytes":p.stat().st_size,"sha256":digest(p.read_bytes())})
            sources[rel]=p
    if evidence_root is not None:
        evidence=Path(evidence_root)
        if evidence.is_symlink(): raise ControlError("linked launch evidence is forbidden")
        config=read(evidence/"config.json")
        if Path(config["experiment"]["run_root"]).resolve()!=root: raise ControlError("launch evidence belongs to another run")
        for name in ("config.json","launch.json","preflight.json","supervisor.json","exit.json","run.log","supervisor.log","interrupt.json"):
            p=evidence/name
            if p.is_symlink(): raise ControlError("linked launch evidence file")
            if p.is_file():
                rel=f"_launch_evidence/{name}"
                if rel in sources: raise ControlError("duplicate launch evidence path")
                sources[rel]=p; rows.append({"path":rel,"bytes":p.stat().st_size,"sha256":digest(p.read_bytes())})
    write_once(output.with_suffix(output.suffix+".manifest.json"),{"schema_version":f"{SCHEMA}.return_manifest","files":rows})
    with tarfile.open(output,"x:gz") as t:
        for row in rows: t.add(sources[row["path"]],arcname=row["path"],recursive=False)
    return {"status":"packaged","file_count":len(rows),"archive_sha256":digest(output.read_bytes())}

def validate_return(archive,manifest):
    expected=read(manifest)["files"]
    with tarfile.open(archive,"r:gz") as t:
        all_members=t.getmembers()
        if any(not m.isfile() or PurePosixPath(m.name).is_absolute() or ".." in PurePosixPath(m.name).parts or "\\" in m.name or ":" in m.name for m in all_members) or len({m.name for m in all_members})!=len(all_members): raise ControlError("archive members are unsafe or unexpected")
        members=all_members
        actual=[{"path":m.name,"bytes":m.size,"sha256":digest(t.extractfile(m).read())} for m in members]
    if actual!=expected: raise ControlError("returned archive inventory drifted")
    return {"status":"validated","file_count":len(actual),"archive_sha256":digest(Path(archive).read_bytes())}

def main(argv=None):
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest="cmd",required=True)
    q=s.add_parser("preflight"); q.add_argument("--config",type=Path,required=True); q.add_argument("--mode",choices=("no-gpu","run-ready"),required=True); q.add_argument("--output",type=Path,required=True)
    q=s.add_parser("launch"); q.add_argument("--config",type=Path,required=True); q.add_argument("--evidence-root",type=Path,required=True)
    q=s.add_parser("interrupt"); q.add_argument("--evidence-root",type=Path,required=True)
    q=s.add_parser("package-results"); q.add_argument("--run-root",type=Path,required=True); q.add_argument("--output",type=Path,required=True)
    q.add_argument("--evidence-root",type=Path)
    q=s.add_parser("validate-return"); q.add_argument("--archive",type=Path,required=True); q.add_argument("--manifest",type=Path,required=True)
    q=s.add_parser("supervise");
    for name in ("python-bin","runner","preparation","run-root","model-root","integrity-evidence","evidence-root"): q.add_argument("--"+name,required=True)
    a=p.parse_args(argv)
    try:
        if a.cmd=="preflight": value=preflight(read(a.config),a.mode); write_once(a.output,value)
        elif a.cmd=="launch": value=launch(a.config,a.evidence_root)
        elif a.cmd=="interrupt": value=interrupt(a.evidence_root)
        elif a.cmd=="package-results": value=package_results(a.run_root,a.output,a.evidence_root)
        elif a.cmd=="validate-return": value=validate_return(a.archive,a.manifest)
        else: return supervise(a.python_bin,a.runner,a.preparation,a.run_root,a.model_root,a.integrity_evidence,a.evidence_root)
    except Exception as e: print(json.dumps({"status":"failed_closed","error":type(e).__name__,"message":str(e)}),file=sys.stderr); return 2
    print(json.dumps(value,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
