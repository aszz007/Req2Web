from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "req2web.phase7_experiment2_comparison.v1"
EXPECTED_SLOTS = tuple(f"slot-{index:03d}" for index in range(1, 31))
CONDITIONS = ("C0", "C1")
INPUT_ROOT = ROOT / "outputs" / "phase7_experiment2_v1" / "attempt-3"
OUTPUT_ROOT = ROOT / "outputs" / "phase7_experiment2_comparison_v1" / "local-baselines-final"


class ComparisonError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def identity(raw: bytes) -> str:
    return "sha256:" + sha256(raw).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot load JSON {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComparisonError(f"expected JSON object: {path}")
    return value


def write_new(path: Path, value: Any) -> None:
    if path.exists():
        raise ComparisonError(f"refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def _safe_bundle_path(bundle: Path, relative: str) -> Path:
    item = PurePosixPath(relative)
    if item.is_absolute() or ".." in item.parts or not item.parts or ":" in relative or "\\" in relative:
        raise ComparisonError(f"unsafe bundle path: {relative!r}")
    path = bundle.joinpath(*item.parts)
    try:
        path.resolve(strict=False).relative_to(bundle.resolve(strict=True))
    except ValueError as exc:
        raise ComparisonError(f"bundle path escapes root: {relative!r}") from exc
    return path


def _finding(code: str, evidence_path: str, source_field: str, candidates: list[str] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "evidence_path": evidence_path,
        "source_field": source_field,
        "target_candidates": sorted(set(candidates or [])),
    }


def _unique_ids(rows: Any, key: str, evidence_path: str, source_field: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return [_finding("invalid_collection_type", evidence_path, source_field)]
    values = [row.get(key) for row in rows if isinstance(row, dict)]
    invalid = [value for value in values if not isinstance(value, str) or not value]
    duplicate = sorted(value for value, count in Counter(values).items() if isinstance(value, str) and count > 1)
    findings = []
    if invalid:
        findings.append(_finding("missing_or_invalid_id", evidence_path, source_field))
    if duplicate:
        findings.append(_finding("duplicate_id", evidence_path, source_field, duplicate))
    return findings


def _page_spec_contracts(value: dict[str, Any], path: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    collections = {"components": "component_id", "sections": "section_id", "states": "state_id", "interactions": "interaction_id", "use_cases": "use_case_id"}
    for name, key in collections.items():
        findings.extend(_unique_ids(value.get(name), key, path, name))
    components = {row.get("component_id") for row in value.get("components", []) if isinstance(row, dict)}
    sections = {row.get("section_id") for row in value.get("sections", []) if isinstance(row, dict)}
    states = {row.get("state_id") for row in value.get("states", []) if isinstance(row, dict)}
    interactions = {row.get("interaction_id") for row in value.get("interactions", []) if isinstance(row, dict)}
    references: list[tuple[str, Any, set[Any]]] = []
    for row in value.get("sections", []):
        if isinstance(row, dict):
            references.extend((f"sections[{row.get('section_id')}].component_ids", item, components) for item in row.get("component_ids", []))
    for row in value.get("states", []):
        if isinstance(row, dict):
            references.extend((f"states[{row.get('state_id')}].visible_component_ids", item, components) for item in row.get("visible_component_ids", []))
    for row in value.get("interactions", []):
        if isinstance(row, dict):
            references.extend([(f"interactions[{row.get('interaction_id')}].trigger_component_id", row.get("trigger_component_id"), components), (f"interactions[{row.get('interaction_id')}].source_state_id", row.get("source_state_id"), states), (f"interactions[{row.get('interaction_id')}].target_state_id", row.get("target_state_id"), states)])
    trace = value.get("traceability", {})
    for row in trace.get("use_cases", []) if isinstance(trace, dict) else []:
        if isinstance(row, dict):
            prefix = f"traceability.use_cases[{row.get('use_case_id')}]"
            references.extend((prefix + ".component_ids", item, components) for item in row.get("component_ids", []))
            references.extend((prefix + ".section_ids", item, sections) for item in row.get("section_ids", []))
            references.extend((prefix + ".interaction_ids", item, interactions) for item in row.get("interaction_ids", []))
    missing: dict[str, list[str]] = defaultdict(list)
    for field, target, allowed in references:
        if isinstance(target, str) and target not in allowed:
            missing[target].append(field)
    for target, fields in sorted(missing.items()):
        findings.append(_finding("dangling_local_reference", path, ";".join(fields), [target]))
    return findings


def _inspector_contracts(value: dict[str, Any], path: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for name, key in (("facts", "fact_id"), ("source_refs", "source_ref_id"), ("trace_links", "trace_link_id")):
        findings.extend(_unique_ids(value.get(name), key, path, name))
    trace_ids = {row.get("trace_link_id") for row in value.get("trace_links", []) if isinstance(row, dict)}
    missing: dict[str, list[str]] = defaultdict(list)
    for row in value.get("facts", []):
        if not isinstance(row, dict):
            continue
        for target in row.get("trace_link_ids", []):
            if isinstance(target, str) and target not in trace_ids:
                missing[target].append(f"facts[{row.get('fact_id')}].trace_link_ids")
    for target, fields in sorted(missing.items()):
        findings.append(_finding("dangling_local_reference", path, ";".join(fields), [target]))
    return findings


class _DomIds(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.component_ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "data-component-id" and value:
                self.component_ids.append(value)


def _declared_hash_checks(bundle: Path, manifest: dict[str, Any], parsed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    mappings = {
        "artifact/acceptance/acceptance_plan.json": {"source_requirement_view_sha256": "artifact/acceptance/requirement_view.json"},
        "artifact/acceptance/acceptance_binding.json": {"source_acceptance_plan_sha256": "artifact/acceptance/acceptance_plan.json", "source_requirement_view_sha256": "artifact/acceptance/requirement_view.json", "source_page_spec_sha256": "artifact/page_spec.json", "observed_render_manifest_sha256": "artifact/render/render_manifest.json"},
        "artifact/inspector_fact_set.json": {"page_spec_sha256": "artifact/page_spec.json", "guidance_sha256": "artifact/result_package/internal/retrieval_guidance.json", "guided_build_result_sha256": "artifact/result_package/internal/guided_page_spec_build_result.json", "retrieval_influence_report_sha256": "artifact/result_package/internal/retrieval_influence_report.json"},
    }
    for owner, fields in mappings.items():
        value = parsed.get(owner)
        if not value:
            continue
        for field, target in fields.items():
            declared = value.get(field)
            if not isinstance(declared, str):
                continue
            target_path = _safe_bundle_path(bundle, target)
            # These declared bindings identify canonical JSON objects, not the
            # package writer's pretty-printed bytes. Inventory entries below
            # identify literal file bytes instead.
            if field == "observed_render_manifest_sha256":
                # binding.py:_inspect_render binds the observed raw file.
                actual = sha256(target_path.read_bytes()).hexdigest() if target_path.is_file() else None
            else:
                actual = sha256(canonical(parsed[target])).hexdigest() if target in parsed else None
            if actual != declared.removeprefix("sha256:"):
                findings.append(_finding("declared_sha256_mismatch", owner, field, [target]))
    package_path = "artifact/result_package/package_manifest.json"
    package = parsed.get(package_path)
    if package:
        rows = package.get("files")
        if isinstance(rows, list):
            for index, row in enumerate(rows):
                if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                    findings.append(_finding("invalid_declared_inventory_entry", package_path, f"files[{index}]"))
                    continue
                relative = "artifact/result_package/" + row["path"]
                target = _safe_bundle_path(bundle, relative)
                actual = sha256(target.read_bytes()).hexdigest() if target.is_file() else None
                declared = row.get("sha256")
                if actual != declared:
                    findings.append(_finding("declared_sha256_mismatch", package_path, f"files[{index}].sha256", [row["path"]]))
    for render_path in ("artifact/render/render_manifest.json", "artifact/result_package/page/render_manifest.json"):
        render = parsed.get(render_path)
        if not render:
            continue
        parent = str(PurePosixPath(render_path).parent)
        for index, row in enumerate(render.get("files", [])):
            relative = parent + "/" + row["name"]
            path = _safe_bundle_path(bundle, relative)
            actual = sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if actual != row.get("sha256"):
                findings.append(_finding("declared_sha256_mismatch", render_path, f"files[{index}].sha256", [relative]))
        for field, filename in (("html_sha256", "index.html"), ("css_sha256", "styles.css"), ("javascript_sha256", "app.js"), ("js_sha256", "app.js")):
            declared = render.get(field)
            if isinstance(declared, str):
                relative = parent + "/" + filename
                actual = sha256(_safe_bundle_path(bundle, relative).read_bytes()).hexdigest()
                if actual != declared.removeprefix("sha256:"):
                    findings.append(_finding("declared_sha256_mismatch", render_path, field, [relative]))
    return findings


def diagnose(bundle: Path, condition: str) -> dict[str, Any]:
    if condition not in CONDITIONS:
        raise ComparisonError(f"unsupported condition: {condition}")
    manifest_path = bundle / "fault_case_bundle_manifest.json"
    manifest = load_object(manifest_path)
    findings: list[dict[str, Any]] = []
    parsed: dict[str, dict[str, Any]] = {}
    declared_paths: set[str] = set()
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise ComparisonError("bundle manifest files must be a list")
    if sha256(canonical({"files": rows})).hexdigest() != manifest.get("inventory_sha256"):
        findings.append(_finding("inventory_digest_mismatch", "fault_case_bundle_manifest.json", "inventory_sha256"))
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            findings.append(_finding("invalid_bundle_inventory_entry", "fault_case_bundle_manifest.json", f"files[{index}]"))
            continue
        relative = row["path"]
        if relative in declared_paths:
            findings.append(_finding("duplicate_inventory_path", "fault_case_bundle_manifest.json", f"files[{index}].path", [relative]))
        declared_paths.add(relative)
        path = _safe_bundle_path(bundle, relative)
        if not path.is_file():
            findings.append(_finding("missing_declared_file", "fault_case_bundle_manifest.json", f"files[{index}].path", [relative]))
            continue
        raw = path.read_bytes()
        if len(raw) != row.get("size"):
            findings.append(_finding("declared_size_mismatch", "fault_case_bundle_manifest.json", f"files[{index}].size", [relative]))
        if sha256(raw).hexdigest() != row.get("sha256"):
            findings.append(_finding("declared_sha256_mismatch", "fault_case_bundle_manifest.json", f"files[{index}].sha256", [relative]))
        if relative.endswith(".json"):
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    findings.append(_finding("json_root_not_object", relative, "$"))
                else:
                    parsed[relative] = value
            except (UnicodeError, json.JSONDecodeError):
                findings.append(_finding("json_parse_error", relative, "$"))
    actual_paths = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()} - {"fault_case_bundle_manifest.json"}
    for extra in sorted(actual_paths - declared_paths):
        findings.append(_finding("undeclared_file", extra, "filesystem", [extra]))
    for missing in sorted(declared_paths - actual_paths):
        findings.append(_finding("missing_declared_file", "fault_case_bundle_manifest.json", "files.path", [missing]))
    findings.extend(_declared_hash_checks(bundle, manifest, parsed))
    if condition == "C1":
        if "artifact/page_spec.json" in parsed:
            findings.extend(_page_spec_contracts(parsed["artifact/page_spec.json"], "artifact/page_spec.json"))
        if "artifact/inspector_fact_set.json" in parsed:
            findings.extend(_inspector_contracts(parsed["artifact/inspector_fact_set.json"], "artifact/inspector_fact_set.json"))
    candidates = sorted({candidate for finding in findings for candidate in finding["target_candidates"]})
    return {
        "candidate_count": len(candidates),
        "condition": condition,
        "detected": bool(findings),
        "findings": findings,
        "status": "alarm" if findings else "clean_pass",
        "target_candidates": candidates,
    }


def _c2_diagnostic(input_root: Path, slot: str) -> dict[str, Any]:
    report_path = input_root / "detector_reports" / slot / "fault_detection_report.json"
    report_manifest_path = input_root / "detector_reports" / slot / "fault_detection_report_manifest.json"
    report = load_object(report_path)
    report_manifest = load_object(report_manifest_path)
    bundle = load_object(input_root / "detector_input" / slot / "fault_case_bundle_manifest.json")
    errors = []
    if report.get("bundle_id") != bundle.get("bundle_id") or report.get("bundle_inventory_sha256") != bundle.get("inventory_sha256"):
        errors.append("report_bundle_binding_mismatch")
    report_raw = report_path.read_bytes()
    expected_entries = [{"path": report_path.name, "sha256": sha256(report_raw).hexdigest(), "size": len(report_raw)}]
    if report_manifest.get("files") != expected_entries or report_manifest.get("report_sha256") != sha256(report_raw).hexdigest() or report_manifest.get("report_id") != report.get("report_id"):
        errors.append("report_manifest_binding_mismatch")
    if errors:
        raise ComparisonError("historical C2 identity mismatch: " + ",".join(errors))
    candidates: list[str] = []
    evidence = []
    for finding_index, finding in enumerate(report.get("findings", [])):
        if not isinstance(finding, dict):
            continue
        for row in finding.get("expected", []):
            if not isinstance(row, dict):
                continue
            key, value = row.get("key"), row.get("value")
            if key in {"trace_link_id", "data_component_id"} and isinstance(value, str):
                candidates.append(value)
                evidence.append({"evidence_path": report_path.relative_to(input_root).as_posix(), "source_field": f"findings[{finding_index}].expected[{key}]"})
        if finding.get("error_code") == "page_spec_dangling_component_reference":
            for row in finding.get("actual", []):
                if isinstance(row, dict) and row.get("key") == "missing_component_ids":
                    try:
                        values = json.loads(row.get("value", ""))
                    except json.JSONDecodeError:
                        values = []
                    if isinstance(values, list) and all(isinstance(item, str) for item in values):
                        candidates.extend(values)
                        evidence.append({"evidence_path": report_path.relative_to(input_root).as_posix(), "source_field": f"findings[{finding_index}].actual[missing_component_ids]"})
        if finding.get("error_code") == "package_manifest_sha256_mismatch":
            paths = finding.get("related_paths", [])
            content = [path.removeprefix("artifact/result_package/") for path in paths if isinstance(path, str) and path.startswith("artifact/result_package/internal/")]
            if len(content) == 1:
                candidates.extend(content)
                evidence.append({"evidence_path": report_path.relative_to(input_root).as_posix(), "source_field": f"findings[{finding_index}].related_paths.unique_package_content"})
    candidates = sorted(set(candidates))
    return {
        "candidate_count": len(candidates), "condition": "C2", "detected": report.get("status") == "fault_detected",
        "evidence": evidence, "historical_identity_errors": errors, "report_identity": identity(report_raw),
        "status": "historical_invalid" if errors else ("alarm" if report.get("status") == "fault_detected" else "clean_pass"),
        "target_candidates": candidates,
    }


def _score(diagnostics: list[dict[str, Any]], labels: list[dict[str, Any]]) -> dict[str, Any]:
    gold = {row["slot_id"]: row for row in labels}
    observations = []
    for diagnostic in diagnostics:
        row = gold[diagnostic["slot_id"]]
        control = row["mutation_kind"] == "clean_control"
        candidates = diagnostic["target_candidates"]
        hit = not control and row["target_id"] in candidates
        observations.append({
            **diagnostic, "case_id": row["case_id"], "mutation_kind": row["mutation_kind"],
            "candidate_hit": hit, "single_exact_target": hit and len(candidates) == 1,
            "target_known_to_scorer": row["target_id"] if not control else "none",
        })
    summary: dict[str, Any] = {}
    for condition in ("C0", "C1", "C2"):
        rows = [row for row in observations if row["condition"] == condition]
        mutants = [row for row in rows if row["mutation_kind"] != "clean_control"]
        controls = [row for row in rows if row["mutation_kind"] == "clean_control"]
        summary[condition] = {
            "candidate_burden_total": sum(row["candidate_count"] for row in mutants),
            "candidate_hit": sum(row["candidate_hit"] for row in mutants),
            "clean_control_alarms": sum(row["status"] == "alarm" for row in controls),
            "clean_control_total": len(controls),
            "detected": sum(row["status"] == "alarm" for row in mutants),
            "mutant_total": len(mutants),
            "single_exact_target": sum(row["single_exact_target"] for row in mutants),
            "unknown_or_ambiguous_target": sum(row["candidate_count"] != 1 for row in mutants),
            "per_mutation": {kind: {"detected": sum(row["status"] == "alarm" for row in mutants if row["mutation_kind"] == kind), "single_exact_target": sum(row["single_exact_target"] for row in mutants if row["mutation_kind"] == kind), "total": sum(row["mutation_kind"] == kind for row in mutants)} for kind in sorted({row["mutation_kind"] for row in mutants})},
        }
    return {"observations": observations, "schema_version": SCHEMA + ".scored", "summary": summary}


def _input_inventory(input_root: Path) -> dict[str, Any]:
    files = [input_root / "evaluator_only" / "frozen_labels_and_targets.json", input_root / "observations.json", input_root / "machine_summary.json"]
    for slot in EXPECTED_SLOTS:
        files.extend([input_root / "detector_input" / slot / "fault_case_bundle_manifest.json", input_root / "detector_reports" / slot / "fault_detection_report.json", input_root / "detector_reports" / slot / "fault_detection_report_manifest.json"])
        bundle = input_root / "detector_input" / slot
        for row in load_object(bundle / "fault_case_bundle_manifest.json")["files"]:
            path = _safe_bundle_path(bundle, row["path"])
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != row["sha256"] or len(raw) != row["size"]:
                raise ComparisonError("frozen input content drift")
            files.append(path)
    if any(not path.is_file() for path in files):
        missing = [str(path) for path in files if not path.is_file()]
        raise ComparisonError(f"required frozen inputs missing: {missing[:3]}")
    return {"files": [{"path": path.relative_to(input_root).as_posix(), "sha256": sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size} for path in files]}


def run(input_root: Path, output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        raise ComparisonError("output root already exists; comparison runs are immutable")
    labels_payload = load_object(input_root / "evaluator_only" / "frozen_labels_and_targets.json")
    labels = labels_payload.get("rows")
    if not isinstance(labels, list) or {row.get("slot_id") for row in labels if isinstance(row, dict)} != set(EXPECTED_SLOTS):
        raise ComparisonError("frozen evaluator inventory is not the expected 30 slots")
    source_raw = Path(__file__).read_bytes()
    protocol = {
        "algorithm_label_access": "withheld_until_separate_scoring",
        "conditions": {"C0": "parse, complete bundle inventory/size/hash, and every implemented applicable declared digest binding", "C1": "C0 plus genuine unique-ID and intra-artifact referential-integrity checks", "C2": "historical verified reports reused without detector rerun"},
        "known_posthoc_set": True,
        "metric_definitions": {"candidate_hit": "gold target occurs in returned candidates", "detection": "alarm on mutant", "single_exact_target": "candidate hit with exactly one returned candidate; stage/type/filename alone never qualifies", "control_alarm": "alarm on clean control"},
        "no_tuning_after_results": True,
        "correction": "Supersedes local-baselines adapter-invalid run: canonical object versus file digests, actual binding field names and render inventory. Historical outputs retained; no fault-specific tuning.",
        "schema_version": SCHEMA + ".protocol",
        "source_identity": identity(source_raw),
    }
    inventory = _input_inventory(input_root)
    inventory["identity"] = identity(canonical(inventory))
    write_new(output_root / "frozen_protocol.json", protocol)
    write_new(output_root / "frozen_input_inventory.json", inventory)
    diagnostics = []
    for slot in EXPECTED_SLOTS:
        bundle = input_root / "detector_input" / slot
        for condition in CONDITIONS:
            result = diagnose(bundle, condition)
            result["slot_id"] = slot
            write_new(output_root / "raw-diagnostics" / condition / slot / "diagnostic.json", result)
            diagnostics.append(result)
        c2 = _c2_diagnostic(input_root, slot)
        c2["slot_id"] = slot
        write_new(output_root / "raw-diagnostics" / "C2" / slot / "diagnostic.json", c2)
        diagnostics.append(c2)
    scored = _score(diagnostics, labels)
    write_new(output_root / "scored_observations.json", scored)
    write_new(output_root / "machine_summary.json", scored["summary"])
    return scored["summary"]


def correct_observed_binding(input_root: Path, prior: Path, output_root: Path) -> dict[str, Any]:
    """Recheck one incorrectly encoded digest per bundle; reuse all other work.

    This is an explicitly disclosed adapter correction, never a fresh independent
    replication. The original two output sets remain unchanged.
    """
    if output_root.exists():
        raise ComparisonError("correction output already exists")
    inventory = _input_inventory(input_root)
    inventory["identity"] = identity(canonical(inventory))
    if inventory != load_object(prior / "frozen_input_inventory.json"):
        raise ComparisonError("prior input identity changed")
    prior_files = sorted((prior / "raw-diagnostics").rglob("diagnostic.json"))
    if len(prior_files) != 90:
        raise ComparisonError("prior diagnostic inventory incomplete")
    protocol = {"schema_version": SCHEMA + ".correction", "source_identity": identity(Path(__file__).read_bytes()),
                "prior_root": str(prior), "prior_reports": [{"path": str(p.relative_to(prior)), "sha256": sha256(p.read_bytes()).hexdigest()} for p in prior_files],
                "scope": "30 raw observed_render_manifest_sha256 checks; reuse all other findings and C2; then rescore",
                "owning_contract": "src/req2web_acceptance/binding.py:_inspect_render sha256(manifest_bytes)",
                "known_posthoc_set": True, "new_detector_runs": 0, "new_full_baseline_runs": 0}
    write_new(output_root / "frozen_protocol.json", protocol)
    write_new(output_root / "frozen_input_inventory.json", inventory)
    diagnostics = []
    owner = "artifact/acceptance/acceptance_binding.json"
    field = "observed_render_manifest_sha256"
    target = "artifact/render/render_manifest.json"
    checks = []
    for slot in EXPECTED_SLOTS:
        bundle = input_root / "detector_input" / slot
        declared = load_object(bundle / owner)[field]
        actual = sha256((bundle / target).read_bytes()).hexdigest()
        checks.append({"slot_id": slot, "declared": declared, "actual": actual, "equal": declared == actual})
        for condition in ("C0", "C1", "C2"):
            value = load_object(prior / "raw-diagnostics" / condition / slot / "diagnostic.json")
            if condition != "C2":
                findings = [f for f in value["findings"] if not (f["evidence_path"] == owner and f["source_field"] == field)]
                if declared != actual:
                    findings.append(_finding("declared_sha256_mismatch", owner, field, [target]))
                candidates = sorted({c for f in findings for c in f["target_candidates"]})
                value.update(findings=findings, target_candidates=candidates, candidate_count=len(candidates), detected=bool(findings), status="alarm" if findings else "clean_pass")
            write_new(output_root / "raw-diagnostics" / condition / slot / "diagnostic.json", value)
            diagnostics.append(value)
    write_new(output_root / "targeted_binding_checks.json", {"checks": checks})
    labels = load_object(input_root / "evaluator_only/frozen_labels_and_targets.json")["rows"]
    scored = _score(diagnostics, labels)
    write_new(output_root / "scored_observations.json", scored)
    write_new(output_root / "machine_summary.json", scored["summary"])
    return scored["summary"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the bounded CPU-only Phase 7 Experiment 2 local comparison.")
    parser.add_argument("--input-root", type=Path, default=INPUT_ROOT)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--correct-observed-render-from", type=Path)
    args = parser.parse_args(argv)
    action = correct_observed_binding(args.input_root.resolve(), args.correct_observed_render_from.resolve(), args.output_root.resolve()) if args.correct_observed_render_from else run(args.input_root.resolve(), args.output_root.resolve())
    print(json.dumps(action, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
