"""Bounded local AgentDebugX compatibility probe; never a root-cause benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import urllib.request
import zipfile
import sys
import importlib.metadata

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.5.2'
WHEEL_URL='https://files.pythonhosted.org/packages/8b/08/e24e1c48fc7009a0f024f282b9fa412f9c898aee6b631315ee2d1c8e9769/agentdebugx-0.5.2-py3-none-any.whl'
WHEEL_SHA256='4fdb90ae19167deb49cbaacce3fbc925220041408aee31c48e59c03bbf7ec82b'
BASE=ROOT/'outputs/phase7_experiment2_comparison_v1/external-agentdebugx'

def digest(raw): return hashlib.sha256(raw).hexdigest()

def write_new(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,sort_keys=True,indent=2,ensure_ascii=False)

def acquire(destination):
    if destination.exists(): raise ValueError('source directory exists; reuse reviewed bytes, never overwrite')
    destination.mkdir(parents=True)
    with urllib.request.urlopen(WHEEL_URL,timeout=30) as response:
        raw=response.read(2_000_001)
    if len(raw)!=735388 or digest(raw)!=WHEEL_SHA256: raise ValueError('pinned upstream wheel drift')
    wheel=destination/f'agentdebugx-{VERSION}-py3-none-any.whl'
    with wheel.open('xb') as stream: stream.write(raw)
    source=destination/'unpacked'; source.mkdir()
    entries=[]
    with zipfile.ZipFile(wheel) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)): raise ValueError('duplicate wheel entries')
        for item in archive.infolist():
            p=PurePosixPath(item.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in item.filename or ':' in item.filename or stat.S_ISLNK(item.external_attr>>16):
                raise ValueError('unsafe wheel path')
            if item.is_dir(): continue
            content=archive.read(item)
            target=source.joinpath(*p.parts); target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream: stream.write(content)
            entries.append({'path':item.filename,'bytes':len(content),'sha256':digest(content)})
    receipt={'schema_version':'req2web.phase7.external_tool_source.v1','tool':'AgentDebugX','version':VERSION,
             'upstream_url':WHEEL_URL,'wheel_sha256':WHEEL_SHA256,'wheel_bytes':len(raw),'files':entries,
             'acquisition_only':True,'upstream_code_executed':False,'environment_installed_into':None,
             'source_unmodified':True,'uploaded_local_data':False}
    write_new(destination/'acquisition.json',receipt)
    return {'status':'acquired_pinned_source_only','version':VERSION,'file_count':len(entries),'wheel_bytes':len(raw)}

def artifact_payload(bundle):
    """Expose only manifest-declared artifacts, never labels or detector reports."""
    manifest=json.loads((bundle/'fault_case_bundle_manifest.json').read_bytes())
    artifacts=[]
    for row in manifest['files']:
        relative=PurePosixPath(row['path'])
        if relative.is_absolute() or '..' in relative.parts or ':' in str(relative) or '\\' in str(relative):
            raise ValueError('unsafe artifact path')
        path=bundle.joinpath(*relative.parts)
        path.resolve().relative_to(bundle.resolve())
        raw=path.read_bytes()
        if digest(raw)!=row['sha256'] or len(raw)!=row['size']:
            raise ValueError('artifact differs from frozen manifest')
        artifacts.append({'path':str(relative),'content':raw.decode('utf-8')})
    return {'manifest':manifest,'artifacts':artifacts}

def verify_source(destination):
    receipt=json.loads((destination/'acquisition.json').read_bytes())
    wheel=destination/f'agentdebugx-{VERSION}-py3-none-any.whl'
    if digest(wheel.read_bytes())!=WHEEL_SHA256: raise ValueError('wheel drift')
    source=destination/'unpacked'
    expected={row['path']:row for row in receipt['files']}
    actual={path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file()}
    if actual!=set(expected): raise ValueError('source inventory drift')
    for relative,row in expected.items():
        raw=(source/relative).read_bytes()
        if digest(raw)!=row['sha256'] or len(raw)!=row['bytes']: raise ValueError('source content drift')
    return source

def probe(destination,output,input_root):
    if output.exists(): raise ValueError('probe output exists; never overwrite')
    source=verify_source(destination)
    payloads=[(f'slot-{i:03d}',artifact_payload(input_root/f'slot-{i:03d}')) for i in range(1,31)]
    protocol={'tool':'AgentDebugX','version':VERSION,'wheel_sha256':WHEEL_SHA256,
              'adapter_sha256':digest(Path(__file__).read_bytes()),'mode':'HeuristicAnalyzer/core',
              'purpose':'native artifact compatibility, not root-cause accuracy',
              'conversion':'auto; manifest plus complete declared UTF-8 artifact content; no fabricated events',
              'synthetic_canaries':2,'native_payloads':len(payloads),'no_llm':True,'no_recovery_or_rerun':True,
              'input_hashes':{slot:digest(json.dumps(value,sort_keys=True).encode()) for slot,value in payloads}}
    write_new(output/'frozen_protocol.json',protocol)
    blocked=[]
    def audit(event,args):
        if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen','os.system','os.exec','os.posix_spawn'}:
            blocked.append(event)
            raise PermissionError('offline probe forbids network and child processes')
    sys.addaudithook(audit)
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(source))
    result={'schema_version':'req2web.phase7.external_compatibility.v1','upstream_code_executed':False,
            'benchmark_accuracy':None,'network_process_guard':'Python audit hook, not an OS sandbox',
            'blocked_actions':blocked,'canaries':[],'conversions':[],
            'runtime':{'python':sys.version,'pydantic':importlib.metadata.version('pydantic'),
                       'httpx':importlib.metadata.version('httpx')}}
    try:
        from agentdebug import AgentEvent,AgentTrajectory,HeuristicAnalyzer,convert_payload
        from agentdebug.ingest.adapters.importers import ConversionError
        result['upstream_code_executed']=True
        for name,error in [('clean',None),('explicit_tool_error','Synthetic tool canary failed')]:
            event=AgentEvent(event_id='canary-event',trace_id='synthetic-canary',agent_name='synthetic-tool',
                             event_type='tool.result',step_index=0,output='Canary complete' if error is None else None,error=error)
            trajectory=AgentTrajectory(trace_id='synthetic-canary',events=[event],metadata={'synthetic':True})
            report=HeuristicAnalyzer(rule_packs=['core']).analyze(trajectory)
            write_new(output/f'canary-{name}.json',report.model_dump(mode='json'))
            result['canaries'].append({'name':name,'synthetic_not_measured':True,'finding_count':len(report.findings),
                                      'root_cause_event_id':report.root_cause_event_id})
        for slot,payload in payloads:
            try:
                trajectory=convert_payload(payload,format='auto')
                result['conversions'].append({'slot':slot,'status':'converted_requires_semantic_review','events':len(trajectory.events)})
            except ConversionError as exc:
                result['conversions'].append({'slot':slot,'status':'unsupported_input_format','reason':str(exc)})
        result['status']='compatibility_probe_complete_not_a_benchmark'
    except Exception as exc:
        result['status']='probe_interrupted'
        result['error']={'type':type(exc).__name__,'message':str(exc)}
    write_new(output/'result.json',result)
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    a=sub.add_parser('acquire'); a.add_argument('--output',type=Path,default=BASE/'source-0.5.2')
    p=sub.add_parser('probe'); p.add_argument('--source',type=Path,default=BASE/'source-0.5.2')
    p.add_argument('--output',type=Path,default=BASE/'probe-1')
    p.add_argument('--input-root',type=Path,default=ROOT/'outputs/phase7_experiment2_v1/attempt-3/detector_input')
    args=parser.parse_args()
    print(json.dumps(acquire(args.output) if args.command=='acquire' else probe(args.source,args.output,args.input_root),sort_keys=True))

if __name__=='__main__': main()
