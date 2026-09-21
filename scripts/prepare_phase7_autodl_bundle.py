"""Build a local-only allowlisted Phase 7 source handoff; never connect or run models."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'req2web.phase7.autodl.payload.v1'
PREPARATION = 'outputs/phase7_experiment1_v1/preparation-v2-final-20260914'
INTEGRITY = Path('D:/Models/Req2Web/evidence/local_integrity_c202236235762e1c871ad0ccb60c8ee5ba337b9a.json')
MODEL_REVISION = 'c202236235762e1c871ad0ccb60c8ee5ba337b9a'
EXPLICIT_FILES = (
    'scripts/phase7_experiment1.py', 'scripts/phase7_direct_html_worker.py',
    'scripts/phase7_remote_control.py', 'scripts/phase7_autodl_launch.sh',
    'scripts/prepare_phase7_autodl_bundle.py',
    'scripts/phase7_e1_model_free_preflight.py',
    'fixtures/phase7_experiment1_cases_v1.json',
    'fixtures/phase7_experiment1_obligations_v1.json',
    'fixtures/phase7_autodl_config.template.json',
    'docs/phase4_langgraph_dependency_acquisition_receipt.json',
    'requirements-phase4-agent.txt',
    'requirements-phase4-agent-lock.txt',
    'data/processed/rag/documents.jsonl', 'data/processed/rag/tfidf_index.json.gz',
    'data/processed/rag/index_manifest.json',
)
PREP_FILES = ('freeze_manifest.json', 'machine_summary.json',
              'blind/alias_mapping.private.json', 'blind/review_packet.json')
MODEL_PINS = {
    'torch':'2.7.1+cu128', 'transformers':'5.14.1', 'accelerate':'1.14.0',
    'safetensors':'0.8.0', 'huggingface-hub':'1.24.0', 'tokenizers':'0.22.2',
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_path(value):
    p = PurePosixPath(value)
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or p.is_absolute() or '..' in p.parts or p.as_posix() != value:
        raise ValueError('unsafe payload path')
    return p


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True, encoding='utf-8').strip()


def checked_bytes(path):
    path = Path(path).absolute()
    if path.is_symlink() or path.resolve(strict=True) != path or not path.is_file():
        raise ValueError(f'not a regular non-linked file: {path}')
    return path.read_bytes()


def add_source(files, relative):
    safe_path(relative)
    if relative in files:
        raise ValueError('duplicate source path')
    files[relative] = checked_bytes(ROOT / relative)


def metadata_integrity(raw):
    data = json.loads(raw)
    if data.get('repo_id') != 'Qwen/Qwen3.5-9B' or data.get('requested_revision') != MODEL_REVISION or data.get('resolved_sha') != MODEL_REVISION:
        raise ValueError('model metadata identity mismatch')
    if data.get('missing') != [] or data.get('extra') != [] or data.get('all_sizes_match') is not True or data.get('all_identities_match') is not True:
        raise ValueError('incomplete model metadata')
    rows = data.get('files', [])
    if len(rows) != 16 or data.get('expected_file_count') != 16:
        raise ValueError('expected pinned sixteen-file inventory')
    if len({r['rfilename'] for r in rows}) != 16:
        raise ValueError('duplicate model metadata paths')
    for row in rows:
        safe_path(row['rfilename'])
        if len(row['actual_sha256']) != 64 or any(x not in '0123456789abcdef' for x in row['actual_sha256']):
            raise ValueError('bad model digest')
    return data


def write_archive(files, destination, metadata):
    """Write a single immutable archive, with no extraction or external action."""
    if destination.exists():
        raise ValueError('bundle output already exists')
    manifest = {
        'schema_version':SCHEMA, 'source_commit':metadata['source_commit'],
        'source_kind':'allowlisted_working_bytes_not_complete_git_snapshot',
        'files':[{'path':p, 'byte_length':len(files[p]), 'sha256':sha(files[p])} for p in sorted(files)],
        'metadata':metadata,
        'excluded_by_default':'everything_not_explicitly_listed',
        'remote_action_occurred':False,
    }
    manifest_bytes = canonical(manifest)
    destination.mkdir(parents=True)
    archive = destination / 'phase7-autodl-payload.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path, raw in sorted({**files, 'payload_manifest.json':manifest_bytes}.items()):
            safe_path(path)
            zi=zipfile.ZipInfo(path, (2026,9,14,0,0,0)); zi.compress_type=zipfile.ZIP_DEFLATED
            zi.external_attr = (stat.S_IFREG | (0o755 if path.endswith('.sh') else 0o644)) << 16
            z.writestr(zi,raw)
    (destination/'payload_manifest.json').write_bytes(manifest_bytes)
    handoff={'schema_version':'req2web.phase7.autodl.handoff.v1',
             'archive_filename':archive.name,'archive_bytes':archive.stat().st_size,
             'archive_sha256':sha(archive.read_bytes()),'payload_manifest_sha256':sha(manifest_bytes),
             'file_count':len(files),'uncompressed_payload_bytes':sum(map(len,files.values())),
             'source_commit':metadata['source_commit'],'remote_action_occurred':False,
             'linux_execution_verified':False,'model_weights_included':False}
    (destination/'handoff_manifest.json').write_bytes(canonical(handoff))
    return handoff


def validate_archive(archive, expected_sha=None):
    if expected_sha and sha(archive.read_bytes()) != expected_sha.removeprefix('sha256:'):
        raise ValueError('archive hash mismatch')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)): raise ValueError('duplicate ZIP members')
        for info in z.infolist():
            safe_path(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16) or info.is_dir(): raise ValueError('non-file ZIP member')
        manifest=json.loads(z.read('payload_manifest.json'))
        if manifest['schema_version']!=SCHEMA: raise ValueError('wrong payload schema')
        rows=manifest['files']
        if len(rows)!=len({r['path'] for r in rows}) or set(names)!={r['path'] for r in rows}|{'payload_manifest.json'}:
            raise ValueError('payload inventory mismatch')
        for row in rows:
            raw=z.read(row['path'])
            if len(raw)!=row['byte_length'] or sha(raw)!=row['sha256']: raise ValueError('payload byte drift')
        return {'status':'validated_local_archive','files':len(rows),'remote_action_occurred':False}


def build(destination, integrity_path=INTEGRITY):
    changed=git('diff','--name-only','HEAD','--','src')
    if changed: raise ValueError('tracked production source has local changes; do not silently upload unrelated edits')
    files={}
    for row in git('ls-files','--stage','--','src').splitlines():
        mode, _, suffix=row.partition(' ')
        path=suffix.split('\t',1)[1]
        if mode!='100644' and mode!='100755': raise ValueError('non-regular tracked source')
        add_source(files,path)
    for path in EXPLICIT_FILES: add_source(files,path)
    for path in PREP_FILES: add_source(files,f'{PREPARATION}/{path}')
    freeze=json.loads(files[f'{PREPARATION}/freeze_manifest.json'])
    for row in freeze['shared_evidence']: add_source(files,f"{PREPARATION}/{row['relative_path']}")
    for row in freeze['direct_prompts']: add_source(files,f"{PREPARATION}/prompts/{row['case_id']}-B.txt")
    for path,binding in freeze['source_bindings'].items():
        if path not in files or 'sha256:'+sha(files[path])!=binding['sha256']:
            raise ValueError(f'preparation-bound source mismatch: {path}')
    integrity_raw=checked_bytes(integrity_path)
    model=metadata_integrity(integrity_raw)
    files['runtime/model_integrity.json']=integrity_raw
    files['runtime/requirements-model.txt']=('# Pinned model dependencies; installation is a later operator action.\n'+''.join(f'{k}=={v}\n' for k,v in MODEL_PINS.items())).encode()
    files['runtime/runtime_profile.json']=canonical({
        'schema_version':'req2web.phase7.autodl.runtime_preparation.v1',
        'model_id':model['repo_id'],'model_revision':MODEL_REVISION,
        'model_file_count':16,'model_total_bytes':model['total_actual_bytes'],
        'historical_linux_python':'3.11.15','local_preparation_python':sys.version.split()[0],
        'core_pins':MODEL_PINS,'langgraph':'1.2.9','profile':'high_gpu_bf16',
        'local_installed':{n:importlib.metadata.version(n) for n in MODEL_PINS},
        'new_remote_environment_verified':False,'model_weights_read_or_copied':False,
    })
    metadata={'source_commit':git('rev-parse','HEAD'),'preparation_relative_root':PREPARATION,
              'freeze_sha256':sha(files[f'{PREPARATION}/freeze_manifest.json']),
              'builder_sha256':sha(checked_bytes(Path(__file__))),
              'model_integrity_relative_path':'runtime/model_integrity.json',
              'rag_scope':'three existing derived text/index files; referenced raw files and image assets are not copied',
              'criteria_scope':'project-authored E1 harness criteria only; never passed to either model prompt',
              'excluded':['.git','data/raw','model weights','credentials','historical runs','restricted images','unrelated untracked files'],
              'transfer_status':'prepared_locally_waiting_for_owner_instance_and_action_time_checks'}
    return write_archive(files,destination,metadata)


def extract_archive(archive, destination, expected_sha):
    """Extract a verified payload into a new dedicated directory, without execution."""
    result = validate_archive(archive, expected_sha)
    destination = destination.absolute()
    if destination.exists() or destination.parent.resolve(strict=True) != destination.parent:
        raise ValueError('extraction needs a new root under a non-linked existing parent')
    destination.mkdir()
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            target = destination.joinpath(*safe_path(entry.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(z.read(entry))
            if entry.filename.endswith('.sh'):
                target.chmod(0o755)
    return {**result, 'status':'extracted_verified_local_payload', 'destination':str(destination)}


def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('build'); b.add_argument('--output',type=Path,required=True); b.add_argument('--model-integrity',type=Path,default=INTEGRITY)
    v=sub.add_parser('validate'); v.add_argument('--archive',type=Path,required=True); v.add_argument('--expected-sha256')
    e=sub.add_parser('extract'); e.add_argument('--archive',type=Path,required=True); e.add_argument('--destination',type=Path,required=True); e.add_argument('--expected-sha256',required=True)
    a=p.parse_args()
    try:
        if a.command=='build': value=build(a.output,a.model_integrity)
        elif a.command=='extract': value=extract_archive(a.archive,a.destination,a.expected_sha256)
        else: value=validate_archive(a.archive,a.expected_sha256)
    except (ValueError,OSError,KeyError,zipfile.BadZipFile) as exc:
        print(json.dumps({'status':'failed_closed','error':str(exc)})); return 2
    print(json.dumps(value,sort_keys=True)); return 0


if __name__=='__main__': raise SystemExit(main())
