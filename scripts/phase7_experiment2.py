"""Run and validate the deterministic Phase 7 Experiment 2 matrix.

This adapter uses the existing G0/v2 builders, mutation injectors, blinded
bundle assembler, inventory parity gate, and public deterministic detector.
Evaluator labels and injector audit data never enter detector-visible bundles.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_acceptance import (  # noqa: E402
    compile_acceptance_binding,
    compile_acceptance_plan,
    project_requirement_view,
)
from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain  # noqa: E402
from req2web_faults.bundle import (  # noqa: E402
    BlindedBundleInventoryParityReport,
    BlindedFaultBundleSource,
    assemble_blinded_fault_bundle,
    load_blinded_fault_bundle,
    validate_blinded_bundle_inventory_parity,
)
from req2web_faults.detector import (  # noqa: E402
    DOM_COMPONENT_STABLE_ID_MISMATCH,
    FAULT_DETECTED,
    INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH,
    NO_FAULT_DETECTED,
    PACKAGE_MANIFEST_SHA256_MISMATCH,
    PAGE_SPEC_DANGLING_COMPONENT_REFERENCE,
    detect_blinded_fault_bundle,
    write_fault_detection_report,
)
from req2web_faults.injector_audit import build_injector_mutation_audit  # noqa: E402
from req2web_faults.mutation import FaultMutationRequest, build_fault_copy  # noqa: E402
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_inspector.facts import project_g0_inspector_facts  # noqa: E402
from req2web_rag import RetrieverConfig, create_retriever  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402


SCHEMA = "req2web.phase7_experiment2_result.v1"
EXPECTED_CASE_IDS = (
    "p7-e2-expense", "p7-e2-enrollment", "p7-e2-directory",
    "p7-e2-review", "p7-e2-notice", "p7-e2-booking",
)
MUTATIONS = (
    ("page_spec_component_removed", "page_spec", PAGE_SPEC_DANGLING_COMPONENT_REFERENCE),
    ("inspector_trace_relation_removed", "guidance/adoption", INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH),
    ("render_component_stable_id_tampered", "render_binding", DOM_COMPONENT_STABLE_ID_MISMATCH),
    ("package_manifest_sha256_tampered", "package", PACKAGE_MANIFEST_SHA256_MISMATCH),
)


class ExperimentError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ExperimentError(f"refusing to overwrite {path}")
    path.write_bytes(_canonical(value))


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExperimentError(f"expected JSON object: {path}")
    return value


class _StableIdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "data-component-id" and value:
                self.values.add(value)


@dataclass(frozen=True)
class BuiltCase:
    source: BlindedFaultBundleSource
    context: object
    guidance: object
    guided: object
    render: object
    package: object
    targets: dict[str, str]


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = _load_object(path)
    if payload.get("schema_version") != "req2web.phase7_experiment2_cases.v1":
        raise ExperimentError("unsupported case fixture schema")
    cases = payload.get("cases")
    if not isinstance(cases, list) or tuple(item.get("case_id") for item in cases) != EXPECTED_CASE_IDS:
        raise ExperimentError("case fixture must contain the exact six ordered case IDs")
    requirements = [item.get("requirement") for item in cases]
    if len(set(requirements)) != 6 or any(not isinstance(value, str) or not value.strip() for value in requirements):
        raise ExperimentError("all six cases require distinct nonempty requirements")
    return cases


def _referenced_component_ids(page_spec: object) -> list[str]:
    payload = page_spec.to_dict()
    component_ids = {item["component_id"] for item in payload["components"]}
    elsewhere = dict(payload)
    elsewhere.pop("components")
    encoded = _canonical(elsewhere).decode("utf-8")
    return sorted(value for value in component_ids if json.dumps(value) in encoded)


def _package_manifest_paths(package: object) -> list[str]:
    manifest = _load_object(Path(package.package_dir) / "package_manifest.json")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ExperimentError("package manifest has no file inventory")
    return sorted(item["path"] for item in entries if isinstance(item, dict) and isinstance(item.get("sha256"), str))


def build_case(case: dict[str, Any], root: Path, index_dir: Path) -> BuiltCase:
    case_id = case["case_id"]
    context = MinimalAgentChain(
        DeterministicRequirementProvider(output_language="en"),
        create_retriever(RetrieverConfig(index_dir=index_dir, backend="tfidf")),
        top_k_per_role=2,
    ).run(
        case["requirement"], target_device=case["target_device"],
        task_type=case["task_type"], constraints=case["constraints"],
    )
    view = project_requirement_view(context)
    plan = compile_acceptance_plan(view)
    guidance = RetrievalGuidanceBuilder().build(context)
    baseline = PageSpecBuilder().build(context)
    guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
    ablations = {
        role: RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,))
        for role in ROLE_ORDER
    }
    render = DeterministicPageRenderer().render(guided.page_spec, root / "source" / "render")
    binding = compile_acceptance_binding(view, plan, guided.page_spec, render)
    consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
    influence = RetrievalInfluenceChecker().check(
        context, guidance, baseline, guided, ablations, render,
    )
    if not consistency.passed or not influence.passed:
        raise ExperimentError(f"{case_id}: clean deterministic source did not pass owning gates")
    fact_set = project_g0_inspector_facts(guidance, guided, guided.page_spec, influence)
    package = DeterministicRetrievalEnhancedResultPackager().package(
        context, guidance, guided, guided.page_spec, render, consistency, influence,
        root / "source" / "package-v2",
    )
    source = BlindedFaultBundleSource(
        case_id=case_id, requirement_view=view, acceptance_plan=plan,
        acceptance_binding=binding, page_spec=guided.page_spec,
        inspector_fact_set=fact_set, render_result=render, result_package=package,
    )
    component_targets = _referenced_component_ids(guided.page_spec)
    parser = _StableIdParser()
    parser.feed((Path(render.output_dir) / "index.html").read_text(encoding="utf-8"))
    render_targets = sorted(parser.values & {item.component_id for item in guided.page_spec.components})
    trace_targets = sorted(item.trace_link_id for item in fact_set.trace_links)
    package_targets = _package_manifest_paths(package)
    if not component_targets or not render_targets or not trace_targets or not package_targets:
        raise ExperimentError(f"{case_id}: one or more preregistered mutation classes lack an eligible target")
    targets = {
        "page_spec_component_removed": component_targets[0],
        "inspector_trace_relation_removed": trace_targets[0],
        "render_component_stable_id_tampered": render_targets[0],
        "package_manifest_sha256_tampered": package_targets[0],
    }
    return BuiltCase(source, context, guidance, guided, render, package, targets)


def _request_and_artifact(built: BuiltCase, kind: str) -> tuple[FaultMutationRequest, object]:
    target = built.targets[kind]
    if kind == "page_spec_component_removed":
        return FaultMutationRequest(built.source.case_id, kind, target), built.source.page_spec
    if kind == "inspector_trace_relation_removed":
        return FaultMutationRequest(built.source.case_id, kind, target), built.source.inspector_fact_set
    if kind == "render_component_stable_id_tampered":
        return FaultMutationRequest(built.source.case_id, kind, target), built.render
    return FaultMutationRequest(built.source.case_id, kind, target, "sha256"), built.package


def run_experiment(cases_path: Path, output_root: Path, index_dir: Path) -> dict[str, Any]:
    if output_root.exists():
        raise ExperimentError("output root already exists; measured runs are immutable")
    output_root.mkdir(parents=True)
    cases = load_cases(cases_path)
    source_identity = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    built_cases: dict[str, BuiltCase] = {}
    bundle_rows: list[dict[str, Any]] = []
    evaluator_rows: list[dict[str, Any]] = []
    records = []
    try:
        for case in cases:
            case_id = case["case_id"]
            case_root = output_root / "cases" / case_id
            built = build_case(case, case_root, index_dir)
            built_cases[case_id] = built
            _write_new(case_root / "source" / "source_identity.json", {
                "agent_context_sha256": sha256(_canonical(built.context.to_dict())).hexdigest(),
                "case_id": case_id,
                "guidance_sha256": sha256(_canonical(built.guidance.to_dict())).hexdigest(),
                "package_id": built.package.package_id,
                "page_id": built.source.page_spec.page_id,
                "targets_frozen_before_detection": True,
            })
            slots = [("clean", None), *((kind, kind) for kind, _, _ in MUTATIONS)]
            for slot_name, mutation_kind in slots:
                slot_id = f"slot-{len(bundle_rows) + 1:03d}"
                fragment_dir = None
                if mutation_kind:
                    request, artifact = _request_and_artifact(built, mutation_kind)
                    fragment_dir = case_root / "injector" / mutation_kind / "fragment"
                    build = build_fault_copy(request, artifact, fragment_dir)
                    audit = build_injector_mutation_audit(build)
                    _write_new(case_root / "evaluator" / mutation_kind / "injector_audit.json", audit.to_dict())
                bundle_dir = output_root / "detector_input" / slot_id
                record = assemble_blinded_fault_bundle(built.source, bundle_dir, fragment_dir=fragment_dir)
                records.append(record)
                bundle_rows.append({"bundle_dir": bundle_dir, "case_id": case_id, "slot_id": slot_id})
                expected = next(((stage, error) for kind, stage, error in MUTATIONS if kind == mutation_kind), ("none", "none"))
                evaluator_rows.append({
                    "case_id": case_id, "expected_error_code": expected[1],
                    "expected_stage": expected[0], "mutation_kind": mutation_kind or "clean_control",
                    "slot_id": slot_id, "target_id": built.targets.get(mutation_kind, "none"),
                })
        parity = validate_blinded_bundle_inventory_parity(records)
        _write_new(output_root / "detector_input" / "inventory_parity_report.json", parity.to_dict())
        _write_new(output_root / "evaluator_only" / "frozen_labels_and_targets.json", {
            "rows": evaluator_rows, "schema_version": "req2web.phase7_experiment2_evaluator.v1",
        })
        observations = []
        labels = {item["slot_id"]: item for item in evaluator_rows}
        for row in bundle_rows:
            started = perf_counter()
            try:
                report = detect_blinded_fault_bundle(row["bundle_dir"], parity)
                elapsed_ms = round((perf_counter() - started) * 1000, 3)
                write_fault_detection_report(report, output_root / "detector_reports" / row["slot_id"])
                gold = labels[row["slot_id"]]
                is_control = gold["mutation_kind"] == "clean_control"
                detected = report.status == FAULT_DETECTED
                localization = (not is_control and detected and report.predicted_stage == gold["expected_stage"] and report.predicted_error_code == gold["expected_error_code"])
                observations.append({
                    **gold, "bundle_id": report.bundle_id, "detected": detected,
                    "elapsed_ms": elapsed_ms, "exact_localization": localization,
                    "predicted_error_code": report.predicted_error_code,
                    "predicted_stage": report.predicted_stage,
                    "referenced_identifiers": list(report.related_identifiers),
                    "referenced_paths": list(report.related_paths), "status": report.status,
                })
            except Exception as exc:
                observations.append({
                    **labels[row["slot_id"]], "detected": False,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                    "exact_localization": False, "missing_report": True,
                    "predicted_error_code": "missing", "predicted_stage": "missing",
                    "referenced_identifiers": [], "referenced_paths": [],
                    "status": "detector_invocation_error", "error": f"{type(exc).__name__}: {exc}",
                })
        summary = summarize(observations)
        result = {
            "cases_fixture_sha256": sha256(cases_path.read_bytes()).hexdigest(),
            "experiment_id": "phase7-experiment2-v1", "observations": observations,
            "schema_version": SCHEMA, "source_git_head": source_identity, "summary": summary,
        }
        _write_new(output_root / "observations.json", result)
        _write_new(output_root / "machine_summary.json", summary)
        return result
    except Exception as exc:
        _write_new(output_root / "failed_run.json", {
            "error": f"{type(exc).__name__}: {exc}", "schema_version": SCHEMA,
            "source_git_head": source_identity,
        })
        raise


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    controls = [row for row in rows if row["mutation_kind"] == "clean_control"]
    mutants = [row for row in rows if row["mutation_kind"] != "clean_control"]
    strata = {}
    for kind, _, _ in MUTATIONS:
        subset = [row for row in mutants if row["mutation_kind"] == kind]
        strata[kind] = {
            "detected": sum(bool(row["detected"]) for row in subset),
            "exact_localization": sum(bool(row["exact_localization"]) for row in subset),
            "total": len(subset),
        }
    case_means = {}
    for case_id in EXPECTED_CASE_IDS:
        subset = [row for row in mutants if row["case_id"] == case_id]
        case_means[case_id] = {
            "detection_mean": sum(bool(row["detected"]) for row in subset) / len(subset) if subset else None,
            "localization_mean": sum(bool(row["exact_localization"]) for row in subset) / len(subset) if subset else None,
        }
    return {
        "clean_control_false_alarms": sum(row["status"] == FAULT_DETECTED for row in controls),
        "clean_control_non_fault_errors": sum(row["status"] not in {NO_FAULT_DETECTED, FAULT_DETECTED} for row in controls),
        "clean_control_total": len(controls), "mutant_detected": sum(bool(row["detected"]) for row in mutants),
        "mutant_exact_localization": sum(bool(row["exact_localization"]) for row in mutants),
        "mutant_total": len(mutants), "missing_reports": sum(bool(row.get("missing_report")) for row in rows),
        "observation_total": len(rows), "per_mutation": strata, "per_source_case_means": case_means,
        "rejected_or_unsupported_bundles": sum(row["status"] in {"detector_invocation_error", "unclassified_failure"} for row in rows),
    }


def validate_output(output_root: Path) -> dict[str, Any]:
    result = _load_object(output_root / "observations.json")
    if result.get("schema_version") != SCHEMA:
        raise ExperimentError("unsupported result schema")
    rows = result.get("observations")
    if not isinstance(rows, list) or len(rows) != 30:
        raise ExperimentError("result must contain exactly 30 observations")
    if len({row["slot_id"] for row in rows}) != 30:
        raise ExperimentError("observation slot IDs are not unique")
    if sum(row["mutation_kind"] == "clean_control" for row in rows) != 6:
        raise ExperimentError("result must contain exactly six controls")
    expected_kinds = {"clean_control", *(item[0] for item in MUTATIONS)}
    for case_id in EXPECTED_CASE_IDS:
        case_rows = [row for row in rows if row.get("case_id") == case_id]
        if len(case_rows) != 5 or {row.get("mutation_kind") for row in case_rows} != expected_kinds:
            raise ExperimentError(f"matrix inventory mismatch for {case_id}")
    evaluator = _load_object(output_root / "evaluator_only" / "frozen_labels_and_targets.json")
    evaluator_rows = evaluator.get("rows")
    if not isinstance(evaluator_rows, list):
        raise ExperimentError("evaluator row inventory is missing")
    labels = {row["slot_id"]: row for row in evaluator_rows}
    if set(labels) != {row["slot_id"] for row in rows}:
        raise ExperimentError("observation/evaluator slot inventory mismatch")
    parity_payload = _load_object(output_root / "detector_input" / "inventory_parity_report.json")
    parity = BlindedBundleInventoryParityReport(
        bundle_count=parity_payload["bundle_count"], path_count=parity_payload["path_count"],
        paths=tuple(parity_payload["paths"]), slots=tuple(parity_payload["slots"]),
        path_set_sha256=parity_payload["path_set_sha256"], schema_version=parity_payload["schema_version"],
    )
    parity.validate()
    for row in rows:
        bundle = load_blinded_fault_bundle(output_root / "detector_input" / row["slot_id"])
        report_path = output_root / "detector_reports" / row["slot_id"] / "fault_detection_report.json"
        if row.get("missing_report"):
            if report_path.exists() or row.get("status") != "detector_invocation_error":
                raise ExperimentError(f"invalid missing-report record for {row['slot_id']}")
            continue
        report_payload = _load_object(report_path)
        actual = detect_blinded_fault_bundle(output_root / "detector_input" / row["slot_id"], parity)
        if bundle.bundle_id != report_payload.get("bundle_id") or actual.to_dict() != report_payload:
            raise ExperimentError(f"report binding mismatch for {row['slot_id']}")
        gold = labels[row["slot_id"]]
        if any(row.get(name) != gold.get(name) for name in ("case_id", "mutation_kind", "expected_stage", "expected_error_code", "target_id")):
            raise ExperimentError(f"evaluator-label binding mismatch for {row['slot_id']}")
        expected_observation = {
            "bundle_id": actual.bundle_id, "detected": actual.status == FAULT_DETECTED,
            "exact_localization": gold["mutation_kind"] != "clean_control" and actual.status == FAULT_DETECTED
            and actual.predicted_stage == gold["expected_stage"] and actual.predicted_error_code == gold["expected_error_code"],
            "predicted_error_code": actual.predicted_error_code, "predicted_stage": actual.predicted_stage,
            "referenced_identifiers": list(actual.related_identifiers), "referenced_paths": list(actual.related_paths),
            "status": actual.status,
        }
        if any(row.get(name) != value for name, value in expected_observation.items()):
            raise ExperimentError(f"observation/report mismatch for {row['slot_id']}")
    recomputed = summarize(rows)
    stored_summary = result["summary"]
    if not isinstance(stored_summary, dict):
        raise ExperimentError("stored summary is not an object")
    if any(stored_summary.get(name) != value for name, value in recomputed.items() if name != "clean_control_non_fault_errors"):
        raise ExperimentError("stored summary does not match recomputation")
    if "clean_control_non_fault_errors" in stored_summary and stored_summary["clean_control_non_fault_errors"] != recomputed["clean_control_non_fault_errors"]:
        raise ExperimentError("stored clean-control error count does not match recomputation")
    if "clean_control_non_fault_errors" not in stored_summary and recomputed["clean_control_non_fault_errors"] != 0:
        raise ExperimentError("legacy summary omitted a nonzero clean-control error count")
    if _load_object(output_root / "machine_summary.json") != result["summary"]:
        raise ExperimentError("machine_summary.json does not match observations")
    return result["summary"]


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run or validate Phase 7 Experiment 2.")
    value.add_argument("--cases", type=Path, default=ROOT / "fixtures" / "phase7_experiment2_cases_v1.json")
    value.add_argument("--index-dir", type=Path, default=ROOT / "data" / "processed" / "rag")
    value.add_argument("--output-root", type=Path, default=ROOT / "outputs" / "phase7_experiment2_v1")
    value.add_argument("--validate-only", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    result = validate_output(args.output_root) if args.validate_only else run_experiment(args.cases, args.output_root, args.index_dir)["summary"]
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
