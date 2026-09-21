"""Linux parent watchdog for one separately authorized E2 split.

No SSH, installation, billing control or instance shutdown. All child output is
kept. A watchdog stop terminates the complete split, never retries a session.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
V3_ACTION_SCHEMA="req2web.phase7.e2_action.v3"
V3_RECOVERY_ACTION_SCHEMA="req2web.phase7.e2_action.v3.recovery1"
V4_ACTION_SCHEMA="req2web.phase7.e2_action.v4"
V5_ACTION_SCHEMA="req2web.phase7.e2_action.v5"


def canonical(v):return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def digest(raw):return hashlib.sha256(raw).hexdigest()
def read(p):return json.loads(p.read_bytes())


def worker_command(config, config_path):
    schema=config.get("schema_version")
    if schema=="req2web.phase7.e2_action.v1":
        runtime_name="phase7_e2_diagnostic_runtime.py"
        worker_python=Path(sys.executable).resolve()
    elif schema==V3_ACTION_SCHEMA:
        runtime_name="phase7_e2_diagnostic_v3_runtime.py"
        worker_python=Path(sys.executable).resolve()
    elif schema in {V3_RECOVERY_ACTION_SCHEMA,V4_ACTION_SCHEMA,V5_ACTION_SCHEMA}:
        environment=config.get("runtime_environment")
        if type(environment) is not dict or type(environment.get("python_executable")) is not str:
            raise ValueError("v3 recovery, v4, and v5 actions require a bound worker interpreter")
        worker_python=Path(environment["python_executable"]).resolve(strict=True)
        if not worker_python.is_file():raise ValueError("bound worker interpreter is not a file")
        runtime_name=(
            "phase7_e2_diagnostic_v3_recovery_runtime.py"
            if schema==V3_RECOVERY_ACTION_SCHEMA
            else "phase7_e2_diagnostic_v4_runtime.py"
            if schema==V4_ACTION_SCHEMA
            else "phase7_e2_diagnostic_v5_runtime.py"
        )
    else:
        raise ValueError("unsupported E2 action schema")
    return [str(worker_python),str(ROOT/"scripts"/runtime_name),"run","--config",str(config_path.resolve())]


def write_new(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("xb") as f:f.write(canonical(value));f.flush();os.fsync(f.fileno())


def watch_reason(active, now, launch_time, batch_cap):
    if now-launch_time >= batch_cap:return "batch_or_rental_time_cap"
    if active is None:
        return "startup_deadline" if now-launch_time > 180 else None
    phase=active.get("phase")
    if phase not in {"loading","session","idle"}:return "invalid_active_phase"
    deadline=active.get("deadline_unix")
    if phase in {"loading","session"}:
        if type(deadline) not in {int,float} or deadline <= 0:return "missing_deadline"
        if now >= deadline:return phase+"_deadline"
    return None


def recovered_summary(config, root, reason):
    """Account for interrupted work from durable starts, not invented zeros."""
    manifest=read(Path(config["public_root"])/"manifest.json")
    completed={}
    ledger=root/"session_ledger.jsonl"
    if ledger.exists():
        for line in ledger.read_bytes().splitlines():
            try:
                row=json.loads(line);completed[row["session_id"]]=row
            except (ValueError,KeyError):break
    sessions=[]
    for scheduled in manifest["schedule"][config["split"]]:
        sid=scheduled["session_id"]
        if sid in completed:
            sessions.append(completed[sid]);continue
        starts=[];refs=[]
        folder=root/sid
        if folder.exists():
            for p in sorted(folder.iterdir()):
                if p.is_symlink() or not p.is_file():raise ValueError("non-regular return artifact")
                if p.name.endswith(".started.json"):
                    starts.append(read(p))
                elif p.suffix in {".raw",".partial"}:
                    raw=p.read_bytes();refs.append({"path":p.relative_to(root).as_posix(),"sha256":digest(raw),"bytes":len(raw)})
        sessions.append({**scheduled,"status":"interrupted" if starts else "not_started_after_interruption",
                         "answer":None,"reads":None,"provider_turns":len(starts),
                         "input_tokens":sum(s.get("backend",{}).get("input_tokens",0) for s in starts),
                         "output_tokens":None,"raw_refs":refs,"reason":reason})
    result_schema = (
        "req2web.phase7.e2_runtime_result.v5"
        if config.get("schema_version")==V5_ACTION_SCHEMA
        else "req2web.phase7.e2_runtime_result.v4"
        if config.get("schema_version")==V4_ACTION_SCHEMA
        else "req2web.phase7.e2_runtime_result.v3"
        if config.get("schema_version") in {V3_ACTION_SCHEMA,V3_RECOVERY_ACTION_SCHEMA}
        else "req2web.phase7.e2_runtime_result.v1"
    )
    return {"schema_version":result_schema,"split":config["split"],
            "public_manifest_sha256":config["public_manifest_sha256"],"fatal":reason,
            "sessions":sessions,"scope":"watchdog reconstruction; unknown output token counts are not zero"}


def terminate_group(proc):
    if proc.poll() is None:
        try:os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError:pass
    try:proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        proc.wait(timeout=10)
    # Also terminate same-group descendants after a parent has exited.
    try:os.killpg(proc.pid,signal.SIGKILL)
    except ProcessLookupError:pass


def supervise(config_path, supervision_root, rental_seconds_remaining):
    if sys.platform != "linux":raise ValueError("real supervision requires Linux")
    config=read(config_path)
    if config.get("approved") is not True:raise ValueError("explicit action approval required")
    if config.get("schema_version") not in {"req2web.phase7.e2_action.v1",V3_ACTION_SCHEMA,V3_RECOVERY_ACTION_SCHEMA,V4_ACTION_SCHEMA,V5_ACTION_SCHEMA}:raise ValueError("unsupported E2 action schema")
    if type(rental_seconds_remaining) is not int or rental_seconds_remaining <= 60:raise ValueError("rental time remaining must leave return overhead")
    split=config["split"]
    if split not in {"development","measured"}:raise ValueError("invalid split")
    batch_cap=min(900 if split=="development" else 5400,rental_seconds_remaining-60)
    if supervision_root.exists():raise ValueError("supervision output already exists")
    output=Path(config["output_root"])
    if output.exists():raise ValueError("run output already exists")
    supervision_root.mkdir(parents=True)
    command=worker_command(config,config_path)
    started=time.time();reason=None;cancel=[False]
    handlers={s:signal.getsignal(s) for s in (signal.SIGTERM,signal.SIGINT)}
    for s in handlers:signal.signal(s,lambda *_:cancel.__setitem__(0,True))
    proc=None
    try:
        with (supervision_root/"worker.log").open("xb") as log:
            proc=subprocess.Popen(command,start_new_session=True,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
            write_new(supervision_root/"launch.json",{"pid":proc.pid,"command":command,"started_at_unix":started,
                      "config_sha256":digest(config_path.read_bytes()),"batch_wall_cap":batch_cap,"automatic_retries":0})
            while proc.poll() is None:
                if cancel[0]:reason="operator_cancelled";break
                active_path=output/"active.json"
                try:active=read(active_path) if active_path.exists() else None
                except (ValueError,OSError):active=None
                reason=watch_reason(active,time.time(),started,batch_cap)
                if reason:break
                time.sleep(.25)
            if not reason:proc.wait()
            terminate_group(proc)
    finally:
        for s,h in handlers.items():signal.signal(s,h)
        if proc is not None and proc.poll() is None:terminate_group(proc)
    if proc is None:raise ValueError("worker did not launch")
    if reason or not (output/"summary.json").exists():
        reason=reason or f"worker_exit_{proc.returncode}_without_summary"
        write_new(supervision_root/"interrupted_summary.json",recovered_summary(config,output,reason))
    inventory=[]
    if output.exists():
        for p in sorted(output.rglob("*")):
            if p.is_symlink():raise ValueError("return inventory symlink")
            if p.is_file():
                raw=p.read_bytes();inventory.append({"path":p.relative_to(output).as_posix(),"bytes":len(raw),"sha256":digest(raw)})
    if config["schema_version"]==V5_ACTION_SCHEMA:
        supervision_schema="req2web.phase7.e2_supervision.v5"
    elif config["schema_version"]==V4_ACTION_SCHEMA:
        supervision_schema="req2web.phase7.e2_supervision.v4"
    elif config["schema_version"]==V3_RECOVERY_ACTION_SCHEMA:
        supervision_schema="req2web.phase7.e2_supervision.v3.recovery1"
    elif config["schema_version"]==V3_ACTION_SCHEMA:
        supervision_schema="req2web.phase7.e2_supervision.v3"
    else:
        supervision_schema="req2web.phase7.e2_supervision.v1"
    receipt={"schema_version":supervision_schema,"exit_code":proc.returncode,
             "reason":reason,"elapsed_seconds":time.time()-started,"files":inventory,
             "model_process_exited":proc.poll() is not None,"automatic_retry_count":0,
             "instance_stopped":False,"billing_ended":False}
    write_new(supervision_root/"return_inventory.json",receipt)
    return receipt


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",type=Path,required=True);p.add_argument("--supervision-root",type=Path,required=True)
    p.add_argument("--rental-seconds-remaining",type=int,required=True)
    a=p.parse_args()
    print(json.dumps(supervise(a.config,a.supervision_root,a.rental_seconds_remaining),indent=2))
