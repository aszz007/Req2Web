"""Prepare source-grounded, model-free E2 packets and score returned diagnoses.

This experiment-only projection is not a production Inspector, a full-flow
execution, a browser trace, or evidence that a model used retrieval material.
Native deterministic artifacts are built locally; only enumerated structural
fields and genuinely recorded local fixture calls enter the public packets.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
BUDGETS = dict(reads=6, input_tokens=12000, output_tokens=2048, provider_turns=7,
               session_seconds=120, measured_sessions=40, development_sessions=4)
FIELDS = ("page_id", "use_cases", "sections", "components", "states", "interactions")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical(value))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def pointer(value, path):
    if path == "":
        return value
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("invalid JSON pointer")
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def walk(value, path=""):
    if isinstance(value, dict):
        for key, child in sorted(value.items()):
            yield from walk(child, path + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from walk(child, path + "/" + str(i))
    else:
        yield path, value


def make_index(files):
    """Index existing ID occurrences only: no comparison, verdict or gold read."""
    identifiers = set()
    for name in ("specification.json", "handoff.json", "render_bindings.json", "runtime.json"):
        for path, value in walk(files[name]):
            if isinstance(value, str) and (path.endswith("_id") or path.endswith("/entity")):
                identifiers.add(value)
    records = {}
    for entity in sorted(identifiers):
        locations = []
        for name, value in sorted(files.items()):
            if name == "readme.json":
                continue
            digest = sha(canonical(value))
            for path, scalar in walk(value):
                if scalar == entity:
                    locations.append(dict(artifact=name, pointer=path, sha256=digest))
        records[entity] = locations
    return {"kind":"experiment_only_source_occurrence_index", "entities":records,
            "meaning":"Each occurrence cites an existing scalar in the listed source. Shared identifiers are navigation aids, not causal claims or correctness verdicts."}


def check_index(files):
    if files["trace_index.json"] != make_index({k:v for k,v in files.items() if k != "trace_index.json"}):
        raise ValueError("index is not the exact source-only projection")
    for entity, locations in files["trace_index.json"]["entities"].items():
        for loc in locations:
            if pointer(files[loc["artifact"]], loc["pointer"]) != entity:
                raise ValueError("index occurrence is not source-grounded")


class Bindings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("data-component-id"):
            self.rows.append({"element_id":"element-" + str(len(self.rows) + 1),
                              "component_id":attrs["data-component-id"], "tag":tag})


def native_projection(case, version, destination, index_dir):
    # Existing owning deterministic chain, no replacement context/provider.
    from phase7_experiment2 import build_case
    spec_case = {k:case[k] for k in ("case_id", "requirement", "constraints", "target_device", "task_type")}
    spec_case["case_id"] += "-r" + str(version)
    spec_case["constraints"] = list(spec_case["constraints"])
    if version == 2:
        spec_case["constraints"].append("Show an explicit confirmation message after completion")
    built = build_case(spec_case, destination, index_dir)
    spec = built.guided.page_spec.to_dict()
    projection = {key:deepcopy(spec[key]) for key in FIELDS}
    projection["revision"] = version
    links = []
    for section in spec["sections"]:
        for component in section["component_ids"]:
            for uc in section["use_case_ids"]:
                links.append(dict(link_id="link-" + str(len(links) + 1), component_id=component,
                                  section_id=section["section_id"], use_case_id=uc))
    if len({x["use_case_id"] for x in links}) < 2:
        raise ValueError("case needs two native use-case mappings; do not manufacture them")
    raw_html = (Path(built.render.output_dir) / "index.html").read_bytes()
    parser = Bindings(); parser.feed(raw_html.decode("utf-8"))
    if len(parser.rows) < 2:
        raise ValueError("native render has insufficient bindings")
    labels = {x["component_id"]:x["label"] for x in spec["components"]}
    for row in parser.rows:
        row["label"] = labels[row["component_id"]]
    result = {
        "requirements.json":{"requirement":spec_case["requirement"], "constraints":spec_case["constraints"],
                             "target_device":case["target_device"], "task_type":case["task_type"]},
        "specification.json":projection,
        "handoff.json":{"revision":version, "page_id":spec["page_id"], "links":links},
        "render_bindings.json":{"revision":version, "page_id":spec["page_id"], "bindings":parser.rows},
    }
    write_new(destination / "projection_receipt.json", {
        "kind":"local_native_projection_receipt_not_inference", "version":version,
        "native_page_spec_sha256":sha(canonical(spec)), "native_html_sha256":sha(raw_html),
        "projected_sha256":{k:sha(canonical(v)) for k,v in result.items()},
        "excluded":"retrieval contents, external reference assets, diagnostics, gold and browser claims",
        "native_gates_passed":True, "model_called":False,
    })
    return result


def fixture_call(operation, state, fail):
    """A real local deterministic test call, never represented as an LLM event."""
    if fail == "timeout":
        raise TimeoutError("Injected local deadline exception; no real network timeout")
    if fail == "parameter":
        raise ValueError("Injected local invalid destination parameter")
    if operation == "advance":
        return state + 1
    return state


def runtime_events(family, use_case_id, parameter_error=False):
    events = []
    state = 0
    operations = ["read_status", "read_status", "advance", "read_status"]
    if family == "no_progress":
        operations = ["advance"] * 5
    elif family == "explicit_error":
        operations = ["read_status", "advance"]
    for i, operation in enumerate(operations):
        before = state
        error = None
        started = time.perf_counter_ns()
        try:
            fail = ("parameter" if parameter_error else "timeout") if family == "explicit_error" and i == 1 else None
            actual_operation = "blocked_advance" if family == "no_progress" else operation
            state = fixture_call(actual_operation, state, fail)
        except (ValueError, TimeoutError) as exc:
            error = {"type":type(exc).__name__, "message":str(exc)}
        events.append({"event_id":"event-" + str(i + 1), "operation":operation,
                       "use_case_id":use_case_id, "before_state":before, "after_state":state,
                       "error":error, "elapsed_ns":time.perf_counter_ns() - started})
    return {"scope":"actual_local_deterministic_fixture_calls_not_agent_execution",
            "warning":"Optional telemetry is disabled; this is not an execution failure.", "events":events}


README = {
    "scope":"Bounded experiment projections from native deterministic Req2Web artifacts, plus local tool fixtures. Not a browser or model execution trace.",
    "task":"Diagnose inconsistencies in supplied artifacts and incomplete local tool execution. Report all independent faulty locations and cite source endpoints. Do not assume every packet is faulty.",
    "authority":{
        "requirements.json":"Current task input.",
        "specification.json":"Current native PageSpec structural projection. Its component ownership, use-case mappings and labels are the structural reference, not proof of business satisfaction.",
        "versions.json":"Release operator's active revision for this fixture. All active downstream views must use that revision.",
        "handoff.json":"Downstream links must preserve specification component-to-section-to-use-case ownership.",
        "render_bindings.json":"Experiment view combining component IDs observed in native rendered HTML with labels copied from native PageSpec. Labels must retain the specification value; they are not browser-observed text.",
        "runtime.json":"Actual local test-call order and outcomes. advance must increase state; read_status may repeat without changing state. Report the first no-progress advance as the origin of that sequence. An explicit exception is an execution fault.",
    },
    "answer_locations":"Use artifact name, entity ID (link_id, element_id, event_id or handoff), and exact RFC6901 field pointer for origins. For a runtime exception use /events/N/error; for no-progress use /events/N/after_state. Cite evidence_edges using artifact/pointer endpoints. Use the handoff revision field for stale revision origins. A leaf or its immediate containing record is an acceptable citation, not an entire document.",
    "reading":"read supports RFC6901 pointers; an empty pointer reads the entire JSON. File contents and index accesses have equal budgets. Source files may contain irrelevant valid context. No filesystem or network actions are requested.",
}


def endpoint(artifact, path):
    return {"artifact":artifact, "pointer":path}


def origin(artifact, entity, field):
    return {"artifact":artifact, "entity":entity, "field":field}


def evidence_group(a, ap, b, bp):
    # Equivalent leaf and immediate-record citations, in either direction.
    paths_a = [ap] + ([ap.rsplit("/",1)[0]] if ap.count("/") > 1 else [])
    paths_b = [bp] + ([bp.rsplit("/",1)[0]] if bp.count("/") > 1 else [])
    choices = []
    for left in paths_a:
        for right in paths_b:
            edge = {"from":endpoint(a,left), "to":endpoint(b,right)}
            choices.extend([edge, {"from":edge["to"], "to":edge["from"]}])
    return choices


def make_packet(current, old, family, parameter_error=False):
    files = deepcopy(current)
    spec = files["specification.json"]
    uc = spec["use_cases"][0]["use_case_id"]
    files["versions.json"] = {"active_revision":2, "published_revisions":[1,2]}
    files["readme.json"] = deepcopy(README)
    files["runtime.json"] = runtime_events(family, uc, parameter_error)
    gold = {"status":"no_fault" if family == "clean" else "fault", "origins":[],
            "evidence_groups":[], "affected_use_cases":[], "identifiable_from_public_sources":True}
    if family in {"wrong_relation", "two_defects"}:
        link = files["handoff.json"]["links"][0]
        other = next(x for x in files["handoff.json"]["links"] if x["use_case_id"] != link["use_case_id"])
        original_uc = link["use_case_id"]
        link["use_case_id"] = other["use_case_id"]
        si = next(i for i,s in enumerate(spec["sections"]) if s["section_id"] == link["section_id"])
        gold["origins"].append(origin("handoff.json",link["link_id"],"/links/0/use_case_id"))
        gold["evidence_groups"].append(evidence_group("handoff.json","/links/0/use_case_id","specification.json",f"/sections/{si}/use_case_ids"))
        gold["affected_use_cases"].append(original_uc)
    if family == "stale_revision":
        files["handoff.json"] = deepcopy(old["handoff.json"])
        gold["origins"].append(origin("handoff.json","handoff","/revision"))
        gold["evidence_groups"].append(evidence_group("handoff.json","/revision","versions.json","/active_revision"))
        gold["affected_use_cases"] = [x["use_case_id"] for x in spec["use_cases"]]
    if family == "two_defects":
        row = files["render_bindings.json"]["bindings"][-1]
        ci = next(i for i,c in enumerate(spec["components"]) if c["component_id"] == row["component_id"])
        row["label"] = "Unrelated archived control"
        idx = len(files["render_bindings.json"]["bindings"]) - 1
        gold["origins"].append(origin("render_bindings.json",row["element_id"],f"/bindings/{idx}/label"))
        gold["evidence_groups"].append(evidence_group("render_bindings.json",f"/bindings/{idx}/label","specification.json",f"/components/{ci}/label"))
        section = next(s for s in spec["sections"] if s["section_id"] == spec["components"][ci]["section_id"])
        gold["affected_use_cases"].extend(section["use_case_ids"])
    if family == "explicit_error":
        gold["origins"].append(origin("runtime.json","event-2","/events/1/error"))
        gold["evidence_groups"].append(evidence_group("runtime.json","/events/1/error","runtime.json","/events/1/operation"))
        gold["affected_use_cases"] = [uc]
    if family == "no_progress":
        gold["origins"].append(origin("runtime.json","event-1","/events/0/after_state"))
        gold["evidence_groups"].append(evidence_group("runtime.json","/events/0/before_state","runtime.json","/events/0/after_state"))
        gold["affected_use_cases"] = [uc]
    gold["affected_use_cases"] = sorted(set(gold["affected_use_cases"]))
    files["trace_index.json"] = make_index(files)
    check_index(files)
    # Ground truth cites only public facts; it is never passed to make_index.
    for group in gold["evidence_groups"]:
        for edge in group:
            for end in edge.values():
                pointer(files[end["artifact"]], end["pointer"])
    return files, gold


def prepare(output, fixture, index_dir):
    if output.exists():
        raise ValueError("preparation output exists; preserve earlier attempts")
    cases = read(fixture)["cases"]
    if len(cases) != 6 or [c["split"] for c in cases] != ["measured"]*4 + ["development"]*2:
        raise ValueError("exact four measured and two development templates required")
    # Exact duplicate audit against public existing E1/E2 fixtures only.
    previous = []
    for path in (ROOT/"fixtures/phase7_experiment1_cases_v1.json", ROOT/"fixtures/phase7_experiment2_cases_v1.json"):
        previous.extend(c["requirement"] for c in read(path)["cases"])
    if len({c["requirement"] for c in cases}) != 6 or any(c["requirement"] in previous for c in cases):
        raise ValueError("duplicate requirement in known public development fixtures")
    output.mkdir(parents=True)
    private = []; candidates = []
    for ci,case in enumerate(cases):
        versions = [native_projection(case,v,output/"local_sources"/case["case_id"]/f"r{v}",index_dir) for v in (1,2)]
        families = ["wrong_relation","stale_revision","two_defects",case["runtime_family"],"clean"] if case["split"] == "measured" else (["wrong_relation"] if ci == 4 else ["no_progress"])
        for family in families:
            files,gold = make_packet(versions[1],versions[0],family,ci==1)
            candidates.append((case["case_id"],case["split"],family,files,gold))
    random.Random(9152026).shuffle(candidates)
    manifest = {"schema_version":"req2web.phase7.e2_public.v1", "budgets":BUDGETS,
                "packets":[], "schedule":{"development":[],"measured":[]}}
    for i,(case_id,split,family,files,gold) in enumerate(candidates,1):
        packet_id = f"p{i:03d}"
        rows = []
        for name,value in sorted(files.items()):
            raw = canonical(value)
            write_new(output/"public"/"packets"/packet_id/name,value)
            rows.append(dict(path=name,sha256=sha(raw),bytes=len(raw)))
        manifest["packets"].append(dict(packet_id=packet_id,files=rows))
        schedule = manifest["schedule"][split]
        for arm in (["A","B"] if i%2 else ["B","A"]):
            schedule.append(dict(session_id=("d" if split=="development" else "m") + f"{len(schedule)+1:03d}",packet_id=packet_id,arm=arm))
        private.append({"packet_id":packet_id,"case_id":case_id,"split":split,"family":family,**gold})
    write_new(output/"public"/"manifest.json",manifest)
    write_new(output/"private"/"gold.json",{"schema_version":"req2web.phase7.e2_gold.v1","packets":private})
    sources = [Path(__file__),fixture,ROOT/"scripts/phase7_experiment2.py"]
    receipt = {"schema_version":"req2web.phase7.e2_preparation.v1", "status":"local_candidate_not_measured",
               "public_manifest_sha256":sha(canonical(manifest)), "gold_sha256":sha((output/"private/gold.json").read_bytes()),
               "source_hashes":{str(p.relative_to(ROOT)).replace("\\","/"):sha(p.read_bytes()) for p in sources},
               "cases":4,"measured_packets":20,"development_packets":2,"measured_sessions":40,"development_sessions":4,
               "measured_sessions_executed":0,"model_loaded":False,"gpu_used":False,
               "native_checkers":"Public projection format is not the native fault-bundle input; no native accuracy is inferred.",
               "trace_index":"experiment_only_ID_occurrence_navigation_not_production_Inspector"}
    write_new(output/"preparation.json",receipt)
    validate(output)
    return receipt


def validate(root):
    receipt = read(root/"preparation.json")
    manifest_raw = (root/"public/manifest.json").read_bytes()
    if sha(manifest_raw) != receipt["public_manifest_sha256"] or sha((root/"private/gold.json").read_bytes()) != receipt["gold_sha256"]:
        raise ValueError("preparation manifest or gold drift")
    manifest = json.loads(manifest_raw)
    if manifest["budgets"] != BUDGETS or len(manifest["packets"]) != 22:
        raise ValueError("budget or packet count drift")
    for packet in manifest["packets"]:
        base = root/"public/packets"/packet["packet_id"]
        files = {}
        if {p.name for p in base.iterdir()} != {r["path"] for r in packet["files"]}:
            raise ValueError("public packet inventory drift")
        for row in packet["files"]:
            p = base/row["path"]
            if p.is_symlink() or p.resolve().parent != base.resolve():
                raise ValueError("unsafe public file")
            raw = p.read_bytes()
            if sha(raw) != row["sha256"] or len(raw) != row["bytes"]:
                raise ValueError("source file drift")
            files[row["path"]] = json.loads(raw)
        check_index(files)
    for split,count in (("measured",40),("development",4)):
        rows = manifest["schedule"][split]
        if len(rows) != count or len({r["session_id"] for r in rows}) != count:
            raise ValueError("schedule drift")
        grouped = {}
        for row in rows:
            grouped.setdefault(row["packet_id"],[]).append(row["arm"])
        if any(sorted(arms) != ["A","B"] for arms in grouped.values()):
            raise ValueError("paired schedule drift")
    return {"status":"validated_local_preparation", "measured_sessions_executed":0, "public_packets":22}


def rebind_index(source, destination):
    """Create a new pre-measurement index revision, reusing exact case bytes."""
    if destination.exists(): raise ValueError("destination exists")
    old_receipt = read(source/"preparation.json")
    manifest = read(source/"public/manifest.json")
    if sha(canonical(manifest)) != old_receipt["public_manifest_sha256"]:
        raise ValueError("candidate manifest drift")
    for packet in manifest["packets"]:
        files = {}
        for row in packet["files"]:
            raw=(source/"public/packets"/packet["packet_id"]/row["path"]).read_bytes()
            if sha(raw) != row["sha256"] or len(raw) != row["bytes"]: raise ValueError("candidate file drift")
            if row["path"] != "trace_index.json": files[row["path"]]=json.loads(raw)
        files["trace_index.json"] = make_index(files)
        check_index(files)
        for row in packet["files"]:
            value=files[row["path"]];raw=canonical(value)
            write_new(destination/"public/packets"/packet["packet_id"]/row["path"],value)
            row.update(sha256=sha(raw),bytes=len(raw))
    gold_raw=(source/"private/gold.json").read_bytes()
    if sha(gold_raw) != old_receipt["gold_sha256"]: raise ValueError("gold drift")
    write_new(destination/"private/gold.json",json.loads(gold_raw))
    write_new(destination/"public/manifest.json",manifest)
    receipt={**old_receipt,"public_manifest_sha256":sha(canonical(manifest)),
             "predecessor_preparation_sha256":sha((source/"preparation.json").read_bytes()),
             "native_sources_preserved_at":str(source/"local_sources"),
             "revision_reason":"canonical index ordering correction only; no cases, labels or local runtime calls regenerated"}
    receipt["source_hashes"]["scripts/phase7_e2_diagnostic_packets.py"]=sha(Path(__file__).read_bytes())
    write_new(destination/"preparation.json",receipt)
    validate(destination)
    return receipt


def pair_key(edge):
    return canonical(edge)


def pr(predicted, expected):
    hit = len(predicted & expected)
    return {"precision":hit/len(predicted) if predicted else None,
            "recall":hit/len(expected) if expected else None,
            "true_positive":hit,"predicted":len(predicted),"expected":len(expected)}


def score_answer(answer, gold, files):
    """Strict common contract; all alternative supporting endpoints preregistered."""
    keys = {"status","origin_candidates","affected_use_cases","evidence_edges","uncertainty"}
    if not isinstance(answer,dict) or set(answer) != keys or answer["status"] not in {"fault","no_fault","unknown","unsupported_input"} or not isinstance(answer["uncertainty"],str):
        raise ValueError("invalid diagnostic answer")
    for key in ("origin_candidates","affected_use_cases","evidence_edges"):
        if not isinstance(answer[key],list): raise ValueError("expected answer list")
    origins = answer["origin_candidates"]
    if any(not isinstance(o,dict) or set(o) != {"artifact","entity","field"} or any(not isinstance(v,str) for v in o.values()) for o in origins):
        raise ValueError("invalid origin")
    if any(not isinstance(x,str) for x in answer["affected_use_cases"]): raise ValueError("invalid use cases")
    expected = {canonical(x) for x in gold["origins"]}; predicted = {canonical(x) for x in origins}
    groups = [{pair_key(x) for x in g} for g in gold["evidence_groups"]]
    all_valid = set().union(*groups) if groups else set()
    edges = set(); resolvable = True
    for edge in answer["evidence_edges"]:
        if not isinstance(edge,dict) or set(edge) != {"from","to"}: raise ValueError("invalid evidence edge")
        for end in edge.values():
            if not isinstance(end,dict) or set(end) != {"artifact","pointer"} or any(not isinstance(v,str) for v in end.values()):
                raise ValueError("invalid evidence endpoint")
            try:
                if end["artifact"] == "trace_index.json": raise ValueError("cite raw source, not the experimental index")
                pointer(files[end["artifact"]],end["pointer"])
            except (KeyError,IndexError,ValueError,TypeError): resolvable = False
        edges.add(pair_key(edge))
    hit_groups = sum(bool(g & edges) for g in groups)
    evidence = {"precision":len(edges & all_valid)/len(edges) if edges else None,
                "recall":hit_groups/len(groups) if groups else None,
                "supported_groups":hit_groups,"expected_groups":len(groups),"raw_endpoints_resolvable":resolvable}
    complete = (answer["status"] == gold["status"] and predicted == expected and hit_groups == len(groups)
                and not edges-all_valid and resolvable)
    if gold["status"] == "no_fault":
        complete = complete and not answer["affected_use_cases"]
    return {"origin":pr(predicted,expected),"evidence":evidence,
            "use_cases":pr(set(answer["affected_use_cases"]),set(gold["affected_use_cases"])),
            "complete_supported_diagnosis":complete,
            "clean_false_alarm":gold["status"] == "no_fault" and (answer["status"] == "fault" or bool(predicted)),
            "status":answer["status"]}


def verify_raw_answer(row, raw_root):
    """A summary answer is usable only when its exact preserved raw agrees."""
    from phase7_e2_diagnostic_runtime import _strict_json
    refs=row.get("raw_refs",[])
    if not refs:raise ValueError("answer has no raw response")
    final=None
    for ref in refs:
        relative=PurePosixPath(ref["path"])
        if not relative.parts or relative.is_absolute() or ".." in relative.parts or "\\" in ref["path"] or ":" in ref["path"] or relative.parts[0] != row["session_id"]:
            raise ValueError("unsafe/misbound raw response path")
        path=raw_root.joinpath(*relative.parts)
        if path.is_symlink() or raw_root.resolve() not in path.resolve().parents:raise ValueError("unsafe raw file")
        raw=path.read_bytes()
        if sha(raw) != ref["sha256"] or len(raw) != ref["bytes"]:raise ValueError("raw response identity mismatch")
        if path.suffix == ".raw":final=_strict_json(raw)
    if not isinstance(final,dict) or final.get("action") != "final" or final.get("answer") != row["answer"]:
        raise ValueError("summary answer differs from preserved raw response")


def score(root, results, output, raw_root=None):
    validate(root)
    manifest = read(root/"public/manifest.json")
    raw_results = read(results)
    if raw_results.get("public_manifest_sha256") != sha((root/"public/manifest.json").read_bytes()):
        raise ValueError("result/preparation identity mismatch")
    split = raw_results["split"]
    scheduled = manifest["schedule"][split]
    observed = raw_results["sessions"]
    if len({x["session_id"] for x in observed}) != len(observed) or {x["session_id"] for x in observed} != {x["session_id"] for x in scheduled}:
        raise ValueError("all scheduled sessions must be accounted for")
    observed = {x["session_id"]:x for x in observed}
    gold = {x["packet_id"]:x for x in read(root/"private/gold.json")["packets"]}
    rows=[]; paired={}
    for schedule in scheduled:
        row = observed[schedule["session_id"]]
        if row["packet_id"] != schedule["packet_id"] or row["arm"] != schedule["arm"]:
            raise ValueError("misbound session")
        g = gold[schedule["packet_id"]]
        files = {p.name:read(p) for p in (root/"public/packets"/schedule["packet_id"]).iterdir()}
        try:
            if row.get("status") not in {"fault","no_fault","unknown","unsupported_input"}:
                raise ValueError("non-completed runtime result")
            verify_raw_answer(row,raw_root or results.parent)
            result = score_answer(row.get("answer"),g,files)
        except (ValueError,KeyError,OSError) as exc:
            runtime_status=row.get("status","missing_status")
            result = {"complete_supported_diagnosis":False,
                      "status":"invalid_answer" if runtime_status in {"fault","no_fault","unknown","unsupported_input"} else runtime_status,
                      "runtime_status":runtime_status,"scoring_error":str(exc)}
        entry={**schedule,"case_id":g["case_id"],"family":g["family"],**result}
        rows.append(entry); paired.setdefault(schedule["packet_id"],{})[schedule["arm"]] = result["complete_supported_diagnosis"]
    counts={"both":0,"only_A":0,"only_B":0,"neither":0}
    for arms in paired.values():
        counts["both" if arms["A"] and arms["B"] else "only_A" if arms["A"] else "only_B" if arms["B"] else "neither"] += 1
    report={"schema_version":"req2web.phase7.e2_diagnostic_score.v1","split":split,"rows":rows,"paired":counts,
            "result_sha256":sha(results.read_bytes()),"public_manifest_sha256":raw_results["public_manifest_sha256"],
            "scope":"four case clusters; development separate; failures retained; no human-usability or causal-root claim"}
    write_new(output,report)
    return {"status":"scored","split":split,"sessions":len(rows),"paired":counts}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("prepare"); p.add_argument("--output",type=Path,required=True)
    p.add_argument("--fixture",type=Path,default=ROOT/"fixtures/phase7_e2_diagnostic_cases.json")
    p.add_argument("--index-dir",type=Path,default=ROOT/"data/processed/rag")
    p=sub.add_parser("validate");p.add_argument("--root",type=Path,required=True)
    p=sub.add_parser("rebind-index");p.add_argument("--source",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    p=sub.add_parser("score");p.add_argument("--root",type=Path,required=True);p.add_argument("--results",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--raw-root",type=Path)
    args=parser.parse_args()
    if args.command=="prepare":result=prepare(args.output,args.fixture,args.index_dir)
    elif args.command=="validate":result=validate(args.root)
    elif args.command=="rebind-index":result=rebind_index(args.source,args.output)
    else:result=score(args.root,args.results,args.output,args.raw_root)
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
