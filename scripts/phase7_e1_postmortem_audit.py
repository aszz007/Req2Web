"""Read-only artifact audit for historical E1; no inference or rescoring.

Writes one new diagnostic JSON file. Product code, returned files and verdicts
are never modified. Current source identities are recorded separately from
historical artifact identities; source equality to the remote run is not assumed.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def bounded(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    if root not in path.parents or not path.is_file():
        raise ValueError("Artifact path escaped the result root")
    return path


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = Counter()
        self.kinds = Counter()

    def handle_starttag(self, tag, attrs):
        self.tags[tag] += 1
        values = dict(attrs)
        if "data-renderer-kind" in values:
            self.kinds[values["data-renderer-kind"]] += 1


def audit(result_root: Path) -> dict:
    root = result_root.resolve(strict=True)
    inventory_path = root / "artifact_inventory.json"
    inventory = read(inventory_path)["artifacts"]
    if len(inventory) != 24 or len({r["alias"] for r in inventory}) != 24:
        raise ValueError("Expected the immutable 24-artifact E1 inventory")
    renderer = ROOT / "src/req2web_generation/renderer.py"
    declarations = ast.parse(renderer.read_text(encoding="utf-8")).body
    supported = next(
        ast.literal_eval(node.value)
        for node in declarations
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SUPPORTED_COMPONENT_TYPES"
                for t in node.targets)
    )
    cases_path = ROOT / "fixtures/phase7_experiment1_cases_v1.json"
    cases = read(cases_path)["cases"]
    by_id = {c["case_id"]: c for c in cases}
    rows = []
    input_audit = []
    verified_assets = set()
    for artifact in inventory:
        page = bounded(root, artifact["entrypoint"])
        if digest(page) != artifact["artifact_sha256"]:
            raise ValueError("Entrypoint hash mismatch")
        for asset in artifact["package_assets"]:
            asset_path = bounded(root, asset["path"])
            if digest(asset_path) != asset["sha256"]:
                raise ValueError("Companion asset hash mismatch")
            verified_assets.add(asset["path"])
        controls = Controls()
        controls.feed(page.read_text(encoding="utf-8"))
        row = {
            "alias": artifact["alias"], "case_id": artifact["case_id"],
            "arm": artifact["arm"],
            "delivery": artifact["selected_delivery_kind"],
            "entrypoint": artifact["entrypoint"],
            "entrypoint_sha256": digest(page),
            "html_control_counts": {t: controls.tags[t]
                                    for t in ("input", "select", "textarea", "button")},
            "historical_html_renderer_kinds": dict(controls.kinds),
        }
        if artifact["arm"] == "A":
            spec_path = page.parent.parent / "internal/page_spec.json"
            spec = read(spec_path)
            types = Counter(c["component_type"] for c in spec["components"])
            row.update({
                "page_spec_sha256": digest(spec_path),
                "component_types": dict(types),
                "component_count": sum(types.values()),
                "unsupported_by_current_renderer": sum(
                    n for kind, n in types.items() if kind not in supported),
            })
            store_id = page.relative_to(root).parts[1]
            upstream = root / "canonical-store" / store_id / "upstream"
            raw_input_path = upstream / "raw_input.json"
            canonical_b_path = upstream / "canonical_b.json"
            raw_input = read(raw_input_path)
            canonical_b = read(canonical_b_path)
            case = by_id[artifact["case_id"]]
            serialized = json.dumps(case["sample_data"], sort_keys=True,
                                    separators=(",", ":"), ensure_ascii=False)
            expected = case["requirement"] + " Exact public mock data JSON: " + serialized
            input_audit.append({
                "case_id": case["case_id"],
                "raw_input_sha256": digest(raw_input_path),
                "canonical_b_sha256": digest(canonical_b_path),
                "raw_input_matches_full_public_task": raw_input["requirement"] == expected,
                "canonical_b_matches_full_public_task": canonical_b["requirement"] == expected,
                "canonical_use_case_titles": [u["title"] for u in canonical_b["use_cases"]],
            })
        rows.append(row)
    model_rows = [r for r in rows if r["delivery"] == "model_result_package_v1"]
    sources = [renderer, cases_path,
               ROOT / "scripts/phase7_experiment1.py",
               ROOT / "scripts/phase7_direct_html_worker.py",
               ROOT / "scripts/phase7_e1_browser_observation.py",
               ROOT / "src/req2web_generation/schema.py",
               ROOT / "src/req2web_generation/consistency.py",
               ROOT / "src/req2web_orchestration/phase4_graph.py"]
    return {
        "schema_version": "req2web.phase7.e1_postmortem.v2",
        "supersedes": "postmortem-20260917.json: source-text-only sample visibility inference",
        "kind": "post_hoc_static_diagnostic_not_new_experiment",
        "model_calls": 0, "browser_runs": 0, "rescored_obligations": 0,
        "inventory_sha256": digest(inventory_path),
        "verified_asset_paths": len(verified_assets),
        "current_source_bindings": {p.relative_to(ROOT).as_posix(): digest(p)
                                    for p in sources},
        "current_renderer_supported_types": list(supported),
        "summary": {
            "model_deliveries": len(model_rows),
            "model_components": sum(r["component_count"] for r in model_rows),
            "model_components_rendered_as_fallback": sum(
                r["historical_html_renderer_kinds"].get("fallback", 0)
                for r in model_rows),
            "model_pages_without_editable_controls": sum(
                sum(r["html_control_counts"][t] for t in ("input", "select", "textarea")) == 0
                for r in model_rows),
            "full_public_task_present_in_a_raw_inputs": sum(
                r["raw_input_matches_full_public_task"] for r in input_audit),
            "full_public_task_present_in_a_canonical_b": sum(
                r["canonical_b_matches_full_public_task"] for r in input_audit),
        },
        "artifacts": rows, "public_input_static_audit": input_audit,
        "limitations": [
            "Actual returned raw inputs and canonical B supersede source-text-only inference.",
            "B receives the same expanded requirement plus a duplicate structured sample_data field.",
            "Static controls are not a new browser behavioral measurement.",
            "No counterfactual score or causal effect size is inferred.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.result_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
