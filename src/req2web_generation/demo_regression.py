from __future__ import annotations

"""Deterministic fixture-driven Demo v2 regression orchestration.

This layer deliberately owns only fixture validation, node orchestration and
aggregate evidence.  All requirement understanding, retrieval, PageSpec
generation, rendering, checking and v2 packaging remain in their existing
modules.
"""

import csv
import hashlib
import io
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain
from req2web_rag import RetrieverConfig, create_retriever
from req2web_rag.corpus import ROLE_ORDER

from .builder import PageSpecBuilder
from .consistency import MinimalConsistencyChecker
from .guided_builder import RetrievalGuidedPageSpecBuilder
from .renderer import DeterministicPageRenderer
from .result_package_v2 import (
    DeterministicRetrievalEnhancedResultPackager,
    RetrievalEnhancedResultPackage,
)
from .retrieval_guidance import RetrievalGuidanceBuilder
from .retrieval_influence import RetrievalInfluenceChecker


REGRESSION_CASE_SET_SCHEMA_VERSION = "req2web.demo.regression.case_set.v1"
REGRESSION_REPORT_SCHEMA_VERSION = "req2web.demo.regression.report.v1"
REGRESSION_MANIFEST_SCHEMA_VERSION = "req2web.demo.regression.manifest.v1"
_GUIDANCE_FIELDS = {
    "requirement": "requirement_guidance",
    "ui_reference": "ui_guidance",
    "interaction_flow": "interaction_guidance",
    "implementation": "implementation_guidance",
    "validation": "validation_guidance",
}
_CSV_FIELDS = (
    "case_id", "role", "retrieval_count", "guidance_count", "adopted",
    "ignored", "fallback", "outcome", "difference_count", "influence_class",
)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _ensure_relative_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/") or ":" in value:
        raise ValueError(f"{name} must be a safe relative POSIX path")
    return value


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    requirement: str
    target_device: str
    constraints: tuple[str, ...]
    coverage_tags: tuple[str, ...]
    task_type: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> "RegressionCase":
        if not isinstance(value, dict):
            raise ValueError("case must be an object")
        allowed = {"case_id", "requirement", "target_device", "task_type", "constraints", "coverage_tags"}
        if set(value) - allowed or not {"case_id", "requirement", "target_device", "constraints", "coverage_tags"}.issubset(value):
            raise ValueError("case fields do not match regression case set v1")
        case_id = value["case_id"]
        requirement = value["requirement"]
        target_device = value["target_device"]
        task_type = value.get("task_type")
        constraints = value["constraints"]
        tags = value["coverage_tags"]
        if not isinstance(case_id, str) or not case_id or case_id != case_id.strip() or any(not (part.islower() or part.isdigit() or part == "-") for part in case_id):
            raise ValueError("case_id must be a stable lowercase hyphenated identifier")
        if not isinstance(requirement, str) or not requirement.strip():
            raise ValueError(f"case {case_id} requirement must not be empty")
        if target_device not in {"mobile", "tablet", "desktop", "web", "responsive_web"}:
            raise ValueError(f"case {case_id} target_device is unsupported")
        if task_type is not None and (not isinstance(task_type, str) or not task_type.strip()):
            raise ValueError(f"case {case_id} task_type must be a non-empty string when supplied")
        if not isinstance(constraints, list) or not all(isinstance(item, str) and item.strip() for item in constraints) or len(constraints) != len(set(constraints)):
            raise ValueError(f"case {case_id} constraints must be unique non-empty strings")
        if not isinstance(tags, list) or not tags or tags != sorted(tags) or len(tags) != len(set(tags)) or not all(isinstance(item, str) and item and item == item.strip() for item in tags):
            raise ValueError(f"case {case_id} coverage_tags must be sorted unique non-empty strings")
        return cls(case_id, requirement, target_device, tuple(constraints), tuple(tags), task_type)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"case_id": self.case_id, "requirement": self.requirement, "target_device": self.target_device, "constraints": list(self.constraints), "coverage_tags": list(self.coverage_tags)}
        if self.task_type is not None:
            value["task_type"] = self.task_type
        return value


@dataclass(frozen=True)
class RegressionCaseSet:
    case_set_id: str
    cases: tuple[RegressionCase, ...]
    schema_version: str = REGRESSION_CASE_SET_SCHEMA_VERSION

    @classmethod
    def load(cls, path: Path) -> "RegressionCaseSet":
        payload = _read_json(Path(path), "regression fixture")
        if set(payload) != {"schema_version", "case_set_id", "cases"} or payload.get("schema_version") != REGRESSION_CASE_SET_SCHEMA_VERSION:
            raise ValueError("fixture schema does not match regression case set v1")
        if not isinstance(payload.get("case_set_id"), str) or not payload["case_set_id"]:
            raise ValueError("fixture case_set_id must not be empty")
        if not isinstance(payload.get("cases"), list):
            raise ValueError("fixture cases must be a list")
        result = cls(payload["case_set_id"], tuple(RegressionCase.from_dict(item) for item in payload["cases"]))
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != REGRESSION_CASE_SET_SCHEMA_VERSION or len(self.cases) != 12:
            raise ValueError("regression case set v1 must contain exactly 12 cases")
        ids = [item.case_id for item in self.cases]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("regression cases must be uniquely sorted by case_id")
        required = {"ecommerce", "pet-recognition", "map-address-search", "desktop-dashboard"}
        if not required.issubset(ids):
            raise ValueError("regression case set must retain the four legacy fixed cases")
        if sum(bool(item.constraints) for item in self.cases if item.case_id not in required) < 4:
            raise ValueError("at least four new cases must declare an explicit recovery or state constraint")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema_version": self.schema_version, "case_set_id": self.case_set_id, "cases": [item.to_dict() for item in self.cases]}


def _case_record(case: RegressionCase, package_dir: Path) -> dict[str, Any]:
    package = RetrievalEnhancedResultPackage(
        package_id=_read_json(package_dir / "package_manifest.json", "package manifest")["package_id"],
        page_id=_read_json(package_dir / "package_manifest.json", "package manifest")["page_id"],
        package_dir=package_dir,
    )
    package.validate()
    context = _read_json(package_dir / "internal/agent_context.json", "agent context")
    guidance = _read_json(package_dir / "internal/retrieval_guidance.json", "retrieval guidance")
    consistency = _read_json(package_dir / "internal/consistency_report.json", "consistency report")
    influence = _read_json(package_dir / "internal/retrieval_influence_report.json", "retrieval influence report")
    role_summaries = {item["role"]: item for item in influence["role_summaries"]}
    ablations = {item["role"]: item for item in influence["ablations"]}
    if set(role_summaries) != set(ROLE_ORDER) or set(ablations) != set(ROLE_ORDER):
        raise ValueError(f"package {case.case_id} has incomplete role summaries")
    roles = []
    for role in ROLE_ORDER:
        summary = role_summaries[role]
        ablation = ablations[role]
        roles.append({
            "role": role,
            "retrieval_count": len(context["retrieval_results"][role]),
            "guidance_count": len(guidance[_GUIDANCE_FIELDS[role]]),
            "adopted": summary["adopted"], "ignored": summary["ignored"], "fallback": summary["fallback"],
            "outcome": ablation["outcome"], "difference_count": ablation["difference_count"],
            "influence_class": "has_influence" if ablation["outcome"] == "role_has_influence" else ("not_applicable_or_ignored" if ablation["outcome"] == "guidance_not_applicable_or_ignored" else "verification_failed"),
        })
    return {
        "case_id": case.case_id, "target_device": case.target_device, "task_type": context["task_type"],
        "coverage_tags": list(case.coverage_tags), "package_id": package.package_id, "page_id": package.page_id,
        "package_manifest_sha256": _sha256_file(package_dir / "package_manifest.json"), "roles": roles,
        "structural_field_category_counts": influence["field_category_counts"],
        "decision_status_counts": influence["decision_status_counts"],
        "consistency": {"passed": consistency["passed"], **consistency["summary"], "warnings": len(consistency["warnings"])},
        "retrieval_influence_passed": influence["passed"],
        "ui_reference_count": len(_read_json(package_dir / "result_summary.json", "result summary")["ui_references"]),
        "interaction_count": len(_read_json(package_dir / "result_summary.json", "result summary")["interaction_flow"]),
        "recovery_closure_count": sum(1 for item in _read_json(package_dir / "internal/page_spec.json", "page spec")["acceptance_checks"] if str(item["check_id"]).startswith("check-recovery-")),
    }


def _matrix_rows(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in cases:
        for role in item["roles"]:
            rows.append({"case_id": item["case_id"], **role})
    return rows


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=_CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


class DemoV2RegressionRunner:
    """Build exactly one fixture set and publish package plus aggregate evidence."""

    def __init__(self, *, index_dir: Path, top_k: int = 2) -> None:
        self.index_dir = Path(index_dir)
        self.top_k = top_k

    def run(self, case_set: RegressionCaseSet, output_root: Path) -> dict[str, Any]:
        case_set.validate()
        root = Path(output_root).expanduser().resolve(strict=False)
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise ValueError("output_root already exists and is not empty; refusing to overwrite")
        root.mkdir(parents=True, exist_ok=True)
        packages = root / "packages"; packages.mkdir()
        retriever = create_retriever(RetrieverConfig(index_dir=self.index_dir.resolve(), backend="tfidf"))
        chain = MinimalAgentChain(DeterministicRequirementProvider(), retriever, top_k_per_role=self.top_k)
        records = []
        try:
            for case in case_set.cases:
                context = chain.run(case.requirement, target_device=case.target_device, task_type=case.task_type, constraints=case.constraints)
                guidance = RetrievalGuidanceBuilder().build(context)
                baseline = PageSpecBuilder().build(context)
                guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
                ablations = {role: RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,)) for role in ROLE_ORDER}
                temporary = root / f".{case.case_id}.render-{uuid.uuid4().hex}"
                temporary.mkdir()
                try:
                    render = DeterministicPageRenderer().render(guided.page_spec, temporary)
                    consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
                    influence = RetrievalInfluenceChecker().check(context, guidance, baseline, guided, ablations, render)
                    result = DeterministicRetrievalEnhancedResultPackager().package(context, guidance, guided, guided.page_spec, render, consistency, influence, packages / case.case_id)
                    result.validate()
                finally:
                    shutil.rmtree(temporary, ignore_errors=True)
                records.append(_case_record(case, packages / case.case_id))
            report = {"schema_version": REGRESSION_REPORT_SCHEMA_VERSION, "case_set_id": case_set.case_set_id, "case_count": len(records), "cases": records, "aggregate": self._aggregate(records)}
            matrix = _csv_bytes(_matrix_rows(records))
            report_bytes = _json_bytes(report)
            (root / "aggregate_report.json").write_bytes(report_bytes)
            (root / "aggregate_matrix.csv").write_bytes(matrix)
            manifest = {"schema_version": REGRESSION_MANIFEST_SCHEMA_VERSION, "case_set_id": case_set.case_set_id, "package_count": len(records), "files": [{"path": f"packages/{item['case_id']}/package_manifest.json", "sha256": item["package_manifest_sha256"]} for item in records] + [{"path": "aggregate_report.json", "sha256": _sha256_bytes(report_bytes)}, {"path": "aggregate_matrix.csv", "sha256": _sha256_bytes(matrix)}]}
            (root / "aggregate_manifest.json").write_bytes(_json_bytes(manifest))
            RegressionSuiteValidator().validate(case_set, root)
            return report
        except Exception:
            raise

    @staticmethod
    def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
        rows = _matrix_rows(records)
        outcomes = {name: sum(item["outcome"] == name for item in rows) for name in ("role_has_influence", "guidance_not_applicable_or_ignored", "verification_failed")}
        return {"package_count": len(records), "all_consistency_passed": all(item["consistency"]["passed"] for item in records), "all_retrieval_influence_passed": all(item["retrieval_influence_passed"] for item in records), "role_outcomes": outcomes, "role_totals": [{"role": role, "retrieval_count": sum(item["retrieval_count"] for item in rows if item["role"] == role), "guidance_count": sum(item["guidance_count"] for item in rows if item["role"] == role), "adopted": sum(item["adopted"] for item in rows if item["role"] == role), "ignored": sum(item["ignored"] for item in rows if item["role"] == role), "fallback": sum(item["fallback"] for item in rows if item["role"] == role), "has_influence": sum(item["outcome"] == "role_has_influence" for item in rows if item["role"] == role), "not_applicable_or_ignored": sum(item["outcome"] == "guidance_not_applicable_or_ignored" for item in rows if item["role"] == role), "verification_failed": sum(item["outcome"] == "verification_failed" for item in rows if item["role"] == role)} for role in ROLE_ORDER]}


class RegressionSuiteValidator:
    """Rebuild aggregate evidence from disk and reject drift or incomplete gates."""

    def validate(self, case_set: RegressionCaseSet, output_root: Path) -> None:
        case_set.validate(); root = Path(output_root)
        report = _read_json(root / "aggregate_report.json", "aggregate report")
        manifest = _read_json(root / "aggregate_manifest.json", "aggregate manifest")
        matrix_path = root / "aggregate_matrix.csv"
        if not matrix_path.is_file() or report.get("schema_version") != REGRESSION_REPORT_SCHEMA_VERSION or report.get("case_set_id") != case_set.case_set_id or report.get("case_count") != 12:
            raise ValueError("aggregate report identity is invalid")
        expected = [_case_record(case, root / "packages" / case.case_id) for case in case_set.cases]
        rebuilt = {"schema_version": REGRESSION_REPORT_SCHEMA_VERSION, "case_set_id": case_set.case_set_id, "case_count": len(expected), "cases": expected, "aggregate": DemoV2RegressionRunner._aggregate(expected)}
        if report != rebuilt:
            raise ValueError("aggregate report statistics do not match packages")
        if matrix_path.read_bytes() != _csv_bytes(_matrix_rows(expected)):
            raise ValueError("aggregate matrix does not match packages")
        expected_manifest = {"schema_version": REGRESSION_MANIFEST_SCHEMA_VERSION, "case_set_id": case_set.case_set_id, "package_count": 12, "files": [{"path": f"packages/{item['case_id']}/package_manifest.json", "sha256": item["package_manifest_sha256"]} for item in expected] + [{"path": "aggregate_report.json", "sha256": _sha256_file(root / "aggregate_report.json")}, {"path": "aggregate_matrix.csv", "sha256": _sha256_file(matrix_path)}]}
        if manifest != expected_manifest:
            raise ValueError("aggregate manifest hashes do not match files")
        if any(item["consistency"]["passed"] is not True or item["consistency"]["fail"] != 0 or item["retrieval_influence_passed"] is not True for item in expected):
            raise ValueError("one or more packages did not pass both quality gates")
