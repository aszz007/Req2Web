"""Bounded external EVL artifact comparison; separate from production authority."""
from __future__ import annotations

import argparse
import base64
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/phase7_experiment2_comparison_v1/external-epsilon'
HISTORY = ROOT / 'outputs/phase7_experiment2_v1/attempt-3'
JAVA_HOME = Path('C:/Program Files/Java/jdk1.8.0_491')
SOURCES = ROOT / 'scripts/phase7_epsilon'
VERSION = '2.8.0'
COORDINATES = [('org.eclipse.epsilon', 'org.eclipse.epsilon.' + name, VERSION) for name in
               ('common', 'eol.engine', 'erl.engine', 'evl.engine', 'emc.plainxml')]
COORDINATES += [('org.antlr', 'antlr-runtime', '3.5.2')]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def record(path, obj):
    write(path, canonical(obj))


def read(path):
    return json.loads(path.read_bytes())


def acquire():
    dest = BASE / 'dependencies'
    if dest.exists():
        raise ValueError('dependencies already exist; do not reacquire')
    rows = []
    for group, name, version in COORDINATES:
        prefix = f'https://repo.maven.apache.org/maven2/{group.replace(".", "/")}/{name}/{version}/{name}-{version}'
        for suffix in ('.pom', '.jar'):
            url = prefix + suffix
            with urllib.request.urlopen(url, timeout=30) as response:
                raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ValueError('dependency size cap exceeded')
            with urllib.request.urlopen(url + '.sha1', timeout=30) as response:
                checksum = response.read(1024).decode().strip().split()[0]
            if hashlib.sha1(raw).hexdigest() != checksum:
                raise ValueError('Maven checksum mismatch')
            filename = name + '-' + version + suffix
            write(dest / filename, raw)
            rows.append({'path': filename, 'url': url, 'bytes': len(raw), 'sha256': sha(raw), 'maven_sha1': checksum})
    record(dest / 'receipt.json', {'epsilon_version': VERSION, 'files': rows, 'installed_globally': False})
    return {'downloaded_files': len(rows), 'bytes': sum(r['bytes'] for r in rows)}


class DomIds(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        self.ids.extend(value for name, value in attrs if name == 'data-component-id' and value)


def project(bundle):
    """Extract facts and raw bytes; never calculate a failed constraint."""
    manifest = read(bundle / 'fault_case_bundle_manifest.json')
    raw_files = {}
    for row in manifest['files']:
        name = row['path']
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or ':' in name or '\\' in name:
            raise ValueError('unsafe path')
        actual_path = bundle.joinpath(*path.parts)
        actual_path.resolve().relative_to(bundle.resolve())
        raw = actual_path.read_bytes()
        if sha(raw) != row['sha256'] or len(raw) != row['size']:
            raise ValueError('frozen bundle bytes changed')
        raw_files[name] = raw
    objects = {name: json.loads(raw) for name, raw in raw_files.items() if name.endswith('.json')}
    root = ET.Element('bundle')

    def add(tag, **attrs):
        return ET.SubElement(root, tag, {k: str(v) for k, v in attrs.items()})

    for name, raw in raw_files.items():
        add('artifact', path=name, sha256=sha(raw), encoding='base64').text = base64.b64encode(raw).decode()
    add('artifact', path='fault_case_bundle_manifest.json', encoding='base64').text = base64.b64encode((bundle / 'fault_case_bundle_manifest.json').read_bytes()).decode()
    for row in manifest['files']:
        add('binding', declared=row['sha256'], computed=sha(raw_files[row['path']]), target=row['path'])
    add('binding', declared=manifest['inventory_sha256'], computed=sha(canonical({'files': manifest['files']})), target='fault_case_bundle_manifest.json')

    # Explicit object bindings from their published field contracts.
    bindings = {
        'artifact/acceptance/acceptance_plan.json': {'source_requirement_view_sha256': 'artifact/acceptance/requirement_view.json'},
        'artifact/acceptance/acceptance_binding.json': {'source_acceptance_plan_sha256': 'artifact/acceptance/acceptance_plan.json', 'source_requirement_view_sha256': 'artifact/acceptance/requirement_view.json', 'source_page_spec_sha256': 'artifact/page_spec.json', 'observed_render_manifest_sha256': 'artifact/render/render_manifest.json'},
        'artifact/inspector_fact_set.json': {'page_spec_sha256': 'artifact/page_spec.json', 'guidance_sha256': 'artifact/result_package/internal/retrieval_guidance.json', 'guided_build_result_sha256': 'artifact/result_package/internal/guided_page_spec_build_result.json', 'retrieval_influence_report_sha256': 'artifact/result_package/internal/retrieval_influence_report.json'}}
    for owner, fields in bindings.items():
        for field, target in fields.items():
            if field in objects[owner]:
                raw = raw_files[target] if field == 'observed_render_manifest_sha256' else canonical(objects[target])
                add('binding', declared=objects[owner][field], computed=sha(raw), target=target, owner=owner, field=field)
    package = 'artifact/result_package/'
    for row in objects[package + 'package_manifest.json']['files']:
        add('binding', declared=row['sha256'], computed=sha(raw_files[package + row['path']]), target=row['path'])
    for parent in ('artifact/render/', package + 'page/'):
        for row in objects[parent + 'render_manifest.json']['files']:
            add('binding', declared=row['sha256'], computed=sha(raw_files[parent + row['name']]), target=parent + row['name'])

    spec = objects['artifact/page_spec.json']
    for collection, key, kind in [('components', 'component_id', 'component'), ('sections', 'section_id', 'section'), ('states', 'state_id', 'state'), ('interactions', 'interaction_id', 'interaction'), ('use_cases', 'use_case_id', 'use_case')]:
        for row in spec[collection]:
            add('entity', scope='pagespec', kind=kind, key=row[key])
    for row in spec['components']:
        add('component', key=row['component_id'])
    for collection, fields in [('sections', {'component_ids': 'component'}), ('states', {'visible_component_ids': 'component'}), ('interactions', {'trigger_component_id': 'component', 'source_state_id': 'state', 'target_state_id': 'state'})]:
        for row in spec[collection]:
            for field, kind in fields.items():
                for target in row[field] if isinstance(row[field], list) else [row[field]]:
                    add('ref', scope='pagespec', kind=kind, target=target)
    for row in spec['traceability']['use_cases']:
        for field, kind in [('component_ids', 'component'), ('section_ids', 'section'), ('interaction_ids', 'interaction')]:
            for target in row[field]:
                add('ref', scope='pagespec', kind=kind, target=target)

    inspector = objects['artifact/inspector_fact_set.json']
    for collection, key in [('facts', 'fact_id'), ('source_refs', 'source_ref_id'), ('trace_links', 'trace_link_id')]:
        for row in inspector[collection]:
            add('entity', scope='inspector', kind=collection, key=row[key])
    for row in inspector['facts']:
        for target in row['trace_link_ids']:
            add('ref', scope='inspector', kind='trace_links', target=target)
    for row in inspector['trace_links']:
        add('link', key=row['trace_link_id'], decision=row['decision_id'], entity=row['entity_id'], field=row['field_path'].rsplit('.', 1)[-1], field_path=row['field_path'])
    build = objects[package + 'internal/guided_page_spec_build_result.json']
    for group in ('adopted', 'ignored', 'fallback'):
        for row in build[group]:
            for field in row['affected_fields']:
                add('affected', decision=row['decision_id'], entity=field['entity_id'], field=field['field_name'])
    dom = DomIds()
    dom.feed(raw_files['artifact/render/index.html'].decode('utf-8'))
    for key in dom.ids:
        add('dom', key=key)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def dependency_identity():
    receipt = read(BASE / 'dependencies/receipt.json')
    for row in receipt['files']:
        if sha((BASE / 'dependencies' / row['path']).read_bytes()) != row['sha256']:
            raise ValueError('dependency drift')
    return receipt


def call(command, out):
    env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC'}}
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=60, check=False)
    write(out.with_suffix('.stdout'), completed.stdout)
    write(out.with_suffix('.stderr'), completed.stderr)
    record(out.with_suffix('.execution.json'), {'command': command, 'exit_code': completed.returncode})
    if completed.returncode:
        raise RuntimeError(f'external process failed; inspect {out}.stderr')
    return completed.stdout


def compile_runner():
    dependency_identity()
    dest = BASE / 'compiled'
    if dest.exists():
        raise ValueError('compiled output exists')
    dest.mkdir(parents=True)
    command = [str(JAVA_HOME / 'bin/javac.exe'), '-encoding', 'UTF-8', '-cp', str(BASE / 'dependencies/*'), '-d', str(dest), str(SOURCES / 'EpsilonArtifactRunner.java')]
    call(command, BASE / 'compile')
    return {'compiled': True}


def execute(xml, cross, log):
    classpath = os.pathsep.join([str(BASE / 'compiled'), str(BASE / 'dependencies/*')])
    command = [str(JAVA_HOME / 'bin/java.exe'), '-Xmx512m', '-Dfile.encoding=UTF-8', '-Djava.security.manager', '-Djava.security.policy==' + str(BASE / 'offline.policy'), '-cp', classpath, 'EpsilonArtifactRunner', str(SOURCES / 'ArtifactChecks.evl'), str(xml), str(cross).lower()]
    raw = call(command, log).decode('utf-8').splitlines()
    if not raw or raw[0] != 'EPSILON_RESULT_V1' or not raw[-1].startswith('EPSILON_END '):
        raise ValueError('invalid external report framing')
    findings = []
    for line in raw[1:-1]:
        rule, target = base64.b64decode(line, validate=True).decode('utf-8').split('\t', 1)
        findings.append({'rule': rule, 'target': target})
    if len(findings) != int(raw[-1].split()[1]):
        raise ValueError('external report count mismatch')
    return {'findings': findings, 'detected': bool(findings), 'target_candidates': sorted({f['target'] for f in findings})}


def setup_policy():
    # Java 8's process-level SecurityManager grants only these read paths.
    # No SocketPermission, write, exec, or environment-read permissions.
    paths = [str(BASE.resolve()).replace('\\', '/') + '/-', str(SOURCES.resolve()).replace('\\', '/') + '/-', str(JAVA_HOME).replace('\\', '/') + '/-']
    policy = 'grant {\n' + ''.join(' permission java.io.FilePermission "' + p + '", "read";\n' for p in paths)
    policy += ' permission java.util.PropertyPermission "*", "read";\n permission java.lang.RuntimePermission "accessDeclaredMembers";\n permission java.lang.reflect.ReflectPermission "suppressAccessChecks";\n};\n'
    write(BASE / 'offline.policy', policy.encode())


def smoke():
    dest = BASE / 'smoke-1'
    if dest.exists():
        raise ValueError('smoke output exists; retain attempts')
    setup_policy()
    root = ET.Element('bundle')
    # All examples are synthetic and have no relationship to the measured slots.
    ET.SubElement(root, 'binding', declared='abc', computed='abc', target='clean-file')
    ET.SubElement(root, 'entity', scope='p', kind='component', key='present')
    ET.SubElement(root, 'ref', scope='p', kind='component', target='present')
    ET.SubElement(root, 'component', key='present')
    ET.SubElement(root, 'dom', key='present')
    ET.SubElement(root, 'affected', decision='decision', entity='present', field='label')
    ET.SubElement(root, 'link', decision='decision', entity='present', field='label')
    write(dest / 'clean.xml', ET.tostring(root))
    clean = execute(dest / 'clean.xml', True, dest / 'clean-run')
    if clean['detected']:
        raise ValueError('clean toy unexpectedly alarmed')
    root.find('binding').set('computed', 'changed')
    root.find('ref').set('target', 'missing')
    root.find('dom').set('key', 'changed')
    root.find('link').set('field', 'changed')
    write(dest / 'faults.xml', ET.tostring(root))
    broken = execute(dest / 'faults.xml', True, dest / 'faults-run')
    expected = {'file|clean-file', 'entity|missing', 'entity|present', 'relation|decision|present|label'}
    if set(broken['target_candidates']) != expected:
        raise ValueError('toy exact targets do not match')
    record(dest / 'result.json', {'synthetic_only': True, 'clean': clean, 'four_faults': broken, 'passed': True})
    return {'smoke_passed': True, 'external_calls': 2}


def run():
    dest = BASE / 'run-1'
    if dest.exists():
        raise ValueError('measured run already exists; never repeat automatically')
    if not read(BASE / 'smoke-1/result.json')['passed']:
        raise ValueError('smoke not passed')
    dependencies = dependency_identity()
    source_paths = [Path(__file__), SOURCES / 'ArtifactChecks.evl', SOURCES / 'EpsilonArtifactRunner.java', BASE / 'compiled/EpsilonArtifactRunner.class', BASE / 'offline.policy', JAVA_HOME / 'bin/java.exe']
    inventory = []
    for i in range(1, 31):
        slot = f'slot-{i:03d}'
        payload = project(HISTORY / 'detector_input' / slot)
        write(dest / 'inputs' / (slot + '.xml'), payload)
        inventory.append({'slot': slot, 'sha256': sha(payload)})
    protocol = {'tool': 'Eclipse Epsilon', 'version': VERSION, 'conditions': ['EVL-local', 'EVL-cross'], 'known_posthoc_set': True,
                'rule_author': 'Req2Web experiment team, not Epsilon upstream', 'projection_contains_verdicts': False,
                'no_model_or_recovery': True, 'measured_calls_cap': 60, 'per_process_timeout_seconds': 60,
                'sources': [{'path': str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p), 'sha256': sha(p.read_bytes())} for p in source_paths],
                'dependencies': dependencies, 'inputs': inventory}
    record(dest / 'frozen_protocol.json', protocol)
    diagnostics = []
    for item in inventory:
        slot = item['slot']
        for condition, cross in [('EVL-local', False), ('EVL-cross', True)]:
            value = execute(dest / 'inputs' / (slot + '.xml'), cross, dest / 'raw' / condition / slot)
            value.update(slot_id=slot, condition=condition)
            record(dest / 'diagnostics' / condition / (slot + '.json'), value)
            diagnostics.append(value)
    # Gold is opened only after every external diagnostic has been persisted.
    labels = read(HISTORY / 'evaluator_only/frozen_labels_and_targets.json')['rows']
    gold = {row['slot_id']: row for row in labels}
    relation_keys = {}
    for row in labels:
        if row['mutation_kind'] == 'clean_control':
            inspector = read(HISTORY / 'detector_input' / row['slot_id'] / 'artifact/inspector_fact_set.json')
            for link in inspector['trace_links']:
                relation_keys[(row['case_id'], link['trace_link_id'])] = 'relation|' + '|'.join([link['decision_id'], link['entity_id'], link['field_path'].rsplit('.', 1)[-1]])
    observations = []
    for value in diagnostics:
        row = gold[value['slot_id']]
        kind = row['mutation_kind']
        if kind == 'clean_control':
            target = None
        elif kind == 'inspector_trace_relation_removed':
            target = relation_keys[(row['case_id'], row['target_id'])]
        else:
            target = ('file|' if kind == 'package_manifest_sha256_tampered' else 'entity|') + row['target_id']
        observations.append({**value, 'case_id': row['case_id'], 'mutation_kind': kind, 'gold_target': target, 'target_hit': target in value['target_candidates'], 'single_exact_target': value['target_candidates'] == [target]})
    summary = {}
    for condition in protocol['conditions']:
        rows = [r for r in observations if r['condition'] == condition]
        mutants = [r for r in rows if r['mutation_kind'] != 'clean_control']
        controls = [r for r in rows if r['mutation_kind'] == 'clean_control']
        summary[condition] = {'detected': sum(r['detected'] for r in mutants), 'mutant_total': len(mutants), 'target_hit': sum(r['target_hit'] for r in mutants), 'single_exact_target': sum(r['single_exact_target'] for r in mutants), 'clean_alarms': sum(r['detected'] for r in controls), 'clean_total': len(controls),
                              'per_mutation': {kind: {'detected': sum(r['detected'] for r in mutants if r['mutation_kind'] == kind), 'target_hit': sum(r['target_hit'] for r in mutants if r['mutation_kind'] == kind)} for kind in sorted({r['mutation_kind'] for r in mutants})}}
    record(dest / 'observations.json', observations)
    record(dest / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['acquire', 'compile', 'smoke', 'run'])
    args = parser.parse_args()
    print(json.dumps({'acquire': acquire, 'compile': compile_runner, 'smoke': smoke, 'run': run}[args.command](), sort_keys=True))


if __name__ == '__main__':
    main()
