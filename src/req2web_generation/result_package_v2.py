from __future__ import annotations

"""Deterministic, retrieval-enhanced result packages.

This module deliberately sits after every generation and checking node.  It
only serializes and cross-validates supplied artifacts; it never reruns the
Agent, Retriever, guided builder, renderer, or either checker.
"""

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle

from .consistency import CONSISTENCY_REPORT_SCHEMA_VERSION, ConsistencyReport
from .guided_builder import (
    GUIDED_PAGE_SPEC_BUILD_RESULT_SCHEMA_VERSION,
    GuidedPageSpecBuildResult,
)
from .renderer import RenderResult
from .result_package import (
    _RENDER_FILES,
    _REQUIRED_REPORT_CHECKS,
    _ensure_no_absolute_paths,
    _json_bytes,
    _sha256,
    _validate_relative_path,
    DeterministicResultPackager,
    ResultPackageError,
)
from .retrieval_guidance import RETRIEVAL_GUIDANCE_SCHEMA_VERSION, RetrievalGuidance
from .retrieval_influence import (
    RETRIEVAL_INFLUENCE_REPORT_SCHEMA_VERSION,
    RetrievalInfluenceReport,
)
from .schema import PAGE_SPEC_SCHEMA_VERSION, PageSpec


RESULT_SUMMARY_V2_SCHEMA_VERSION = "req2web.result.summary.v2"
RESULT_PACKAGE_V2_SCHEMA_VERSION = "req2web.result.package.v2"

_MANIFEST = "package_manifest.json"
_PAGE_FILES = (
    "page/index.html",
    "page/styles.css",
    "page/app.js",
    "page/render_manifest.json",
)
_INTERNAL_FILES = (
    "internal/agent_context.json",
    "internal/retrieval_guidance.json",
    "internal/guided_page_spec_build_result.json",
    "internal/page_spec.json",
    "internal/consistency_report.json",
    "internal/retrieval_influence_report.json",
)
_CONTENT_FILES = (*_PAGE_FILES, *_INTERNAL_FILES, "result_summary.json")
_ROLES = {
    "page/index.html": "browser_entrypoint",
    "page/styles.css": "page_styles",
    "page/app.js": "page_script",
    "page/render_manifest.json": "render_manifest",
    "internal/agent_context.json": "agent_context",
    "internal/retrieval_guidance.json": "retrieval_guidance",
    "internal/guided_page_spec_build_result.json": "guided_page_spec_build_result",
    "internal/page_spec.json": "guided_page_spec",
    "internal/consistency_report.json": "consistency_report",
    "internal/retrieval_influence_report.json": "retrieval_influence_report",
    "result_summary.json": "result_summary",
}


class RetrievalEnhancedResultPackageError(ResultPackageError):
    """Raised when a v2 package gate or on-disk verification fails."""


def _load_object(content: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalEnhancedResultPackageError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RetrievalEnhancedResultPackageError(f"{name} must be a JSON object")
    return value


def _influence_report_id(report: dict[str, Any]) -> str:
    payload = dict(report)
    payload.pop("report_id", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "retrieval-influence-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class RetrievalEnhancedResultPackage:
    package_id: str
    page_id: str
    package_dir: Path
    entrypoint: str = "page/index.html"
    result_summary: str = "result_summary.json"
    package_manifest: str = _MANIFEST
    schema_version: str = RESULT_PACKAGE_V2_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != RESULT_PACKAGE_V2_SCHEMA_VERSION:
            raise RetrievalEnhancedResultPackageError("unsupported result package v2 schema")
        if not self.package_id.strip() or not self.page_id.strip():
            raise RetrievalEnhancedResultPackageError("result package v2 identity must not be empty")
        for field in ("entrypoint", "result_summary", "package_manifest"):
            _validate_relative_path(getattr(self, field), field)
        root = Path(self.package_dir)
        manifest_path = root / self.package_manifest
        if not manifest_path.is_file():
            raise RetrievalEnhancedResultPackageError("package_manifest.json is missing")
        manifest = _load_object(manifest_path.read_bytes(), "package_manifest.json")
        expected_manifest_fields = {"schema_version", "package_id", "page_id", "entrypoint", "result_summary", "files"}
        if set(manifest) != expected_manifest_fields:
            raise RetrievalEnhancedResultPackageError("package manifest fields do not match v2")
        if manifest.get("schema_version") != RESULT_PACKAGE_V2_SCHEMA_VERSION:
            raise RetrievalEnhancedResultPackageError("package manifest schema version is incorrect")
        for name, value in (("package_id", self.package_id), ("page_id", self.page_id), ("entrypoint", self.entrypoint), ("result_summary", self.result_summary)):
            if manifest.get(name) != value:
                raise RetrievalEnhancedResultPackageError(f"package manifest {name} is inconsistent")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise RetrievalEnhancedResultPackageError("package manifest files must be a list")
        files: dict[str, bytes] = {}
        paths: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "role", "sha256", "size"}:
                raise RetrievalEnhancedResultPackageError("package manifest file entry is invalid")
            path = _validate_relative_path(entry.get("path"), "package manifest file path")
            if path == _MANIFEST or entry.get("role") != _ROLES.get(path):
                raise RetrievalEnhancedResultPackageError(f"package manifest role or self-reference is invalid: {path}")
            content_path = root.joinpath(*PurePosixPath(path).parts)
            if not content_path.is_file():
                raise RetrievalEnhancedResultPackageError(f"declared package file is missing: {path}")
            content = content_path.read_bytes()
            if not isinstance(entry.get("size"), int) or entry["size"] < 0 or entry["size"] != len(content):
                raise RetrievalEnhancedResultPackageError(f"package manifest size is incorrect: {path}")
            if entry.get("sha256") != _sha256(content):
                raise RetrievalEnhancedResultPackageError(f"package manifest SHA-256 is incorrect: {path}")
            files[path] = content
            paths.append(path)
        if paths != sorted(_CONTENT_FILES):
            raise RetrievalEnhancedResultPackageError("package manifest must declare the v2 fixed file set in stable order")
        actual = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
        if actual != sorted((*_CONTENT_FILES, _MANIFEST)):
            raise RetrievalEnhancedResultPackageError("package contains undeclared or missing files")
        _ensure_no_absolute_paths(files)
        self._validate_internal_identity(files)
        self._validate_summary(files)

    def _validate_internal_identity(self, files: dict[str, bytes]) -> None:
        context = _load_object(files["internal/agent_context.json"], "agent_context.json")
        guidance = _load_object(files["internal/retrieval_guidance.json"], "retrieval_guidance.json")
        guided = _load_object(files["internal/guided_page_spec_build_result.json"], "guided_page_spec_build_result.json")
        page_spec = _load_object(files["internal/page_spec.json"], "page_spec.json")
        consistency = _load_object(files["internal/consistency_report.json"], "consistency_report.json")
        influence = _load_object(files["internal/retrieval_influence_report.json"], "retrieval_influence_report.json")
        if context.get("schema_version") != AGENT_BUNDLE_SCHEMA_VERSION:
            raise RetrievalEnhancedResultPackageError("agent context schema is incorrect")
        if guidance.get("schema_version") != RETRIEVAL_GUIDANCE_SCHEMA_VERSION or guidance.get("source_context_schema_version") != context.get("schema_version"):
            raise RetrievalEnhancedResultPackageError("retrieval guidance identity is inconsistent")
        if guided.get("schema_version") != GUIDED_PAGE_SPEC_BUILD_RESULT_SCHEMA_VERSION or guided.get("source_context_schema_version") != context.get("schema_version") or guided.get("source_guidance_schema_version") != guidance.get("schema_version"):
            raise RetrievalEnhancedResultPackageError("guided build-result schema identity is inconsistent")
        if guided.get("guidance_bundle_id") != guidance.get("guidance_bundle_id") or guided.get("page_spec") != page_spec:
            raise RetrievalEnhancedResultPackageError("guided build-result does not match guidance or final PageSpec")
        if page_spec.get("schema_version") != PAGE_SPEC_SCHEMA_VERSION or page_spec.get("page_id") != self.page_id:
            raise RetrievalEnhancedResultPackageError("final PageSpec identity is incorrect")
        if consistency.get("schema_version") != CONSISTENCY_REPORT_SCHEMA_VERSION or consistency.get("page_id") != self.page_id or consistency.get("passed") is not True or consistency.get("summary", {}).get("fail") != 0:
            raise RetrievalEnhancedResultPackageError("consistency gate is not a passing report for the final PageSpec")
        if influence.get("schema_version") != RETRIEVAL_INFLUENCE_REPORT_SCHEMA_VERSION or influence.get("page_id") != self.page_id or influence.get("guidance_bundle_id") != guidance.get("guidance_bundle_id"):
            raise RetrievalEnhancedResultPackageError("retrieval influence identity is inconsistent")
        if influence.get("report_id") != _influence_report_id(influence):
            raise RetrievalEnhancedResultPackageError("retrieval influence report_id is inconsistent")
        renderer_gate = influence.get("renderer_consistency")
        if influence.get("passed") is not True or not isinstance(renderer_gate, dict) or renderer_gate.get("passed") is not True or renderer_gate.get("summary") != consistency.get("summary"):
            raise RetrievalEnhancedResultPackageError("retrieval influence gate is not a passing report for this consistency report")

    def _validate_summary(self, files: dict[str, bytes]) -> None:
        summary = _load_object(files[self.result_summary], "result_summary.json")
        expected_fields = {"schema_version", "package_id", "page_id", "title", "summary", "target_device", "page_type", "entrypoint", "text_description", "ui_references", "interaction_flow", "interaction_flow_semantics", "retrieval_enhancement", "quality_gate", "artifacts"}
        if set(summary) != expected_fields:
            raise RetrievalEnhancedResultPackageError("result summary fields do not match v2")
        if summary.get("schema_version") != RESULT_SUMMARY_V2_SCHEMA_VERSION:
            raise RetrievalEnhancedResultPackageError("result summary schema version is incorrect")
        if summary.get("package_id") != self.package_id or summary.get("page_id") != self.page_id or summary.get("entrypoint") != self.entrypoint:
            raise RetrievalEnhancedResultPackageError("result summary identity is inconsistent")
        artifacts = summary.get("artifacts")
        if not isinstance(artifacts, list) or artifacts != [{"path": path, "role": _ROLES[path]} for path in _CONTENT_FILES if path != "result_summary.json"]:
            raise RetrievalEnhancedResultPackageError("result summary artifacts do not match the v2 fixed package")
        enhancement = summary.get("retrieval_enhancement")
        quality = summary.get("quality_gate")
        if not isinstance(enhancement, dict) or not isinstance(quality, dict) or quality.get("passed") is not True:
            raise RetrievalEnhancedResultPackageError("result summary lacks passing retrieval-enhancement quality gates")
        guidance = _load_object(files["internal/retrieval_guidance.json"], "retrieval_guidance.json")
        guided = _load_object(files["internal/guided_page_spec_build_result.json"], "guided_page_spec_build_result.json")
        influence = _load_object(files["internal/retrieval_influence_report.json"], "retrieval_influence_report.json")
        if enhancement.get("guidance_bundle_id") != guidance.get("guidance_bundle_id") or enhancement.get("guided_build_result_id") != guided.get("build_result_id") or enhancement.get("retrieval_influence_report_id") != influence.get("report_id"):
            raise RetrievalEnhancedResultPackageError("result summary retrieval identities are inconsistent")
        if not isinstance(summary.get("interaction_flow"), list) or not isinstance(summary.get("interaction_flow_semantics"), str) or "stable_interaction_list" not in summary["interaction_flow_semantics"]:
            raise RetrievalEnhancedResultPackageError("result summary interaction flow semantics are invalid")
        for index, item in enumerate(summary["interaction_flow"], 1):
            if not isinstance(item, dict) or set(item) != {"step", "use_case_ids", "trigger_component_id", "action", "user_feedback", "target_state_id"} or item.get("step") != index:
                raise RetrievalEnhancedResultPackageError("result summary interaction flow is invalid")
        consistency = _load_object(files["internal/consistency_report.json"], "consistency_report.json")
        expected_quality = {"passed": True, "consistency": {"passed": True, **consistency["summary"], "warnings": consistency["warnings"], "report": "internal/consistency_report.json"}, "retrieval_influence": {"passed": True, "total": len(influence["checks"]), "pass": sum(item.get("status") == "pass" for item in influence["checks"]), "fail": sum(item.get("status") == "fail" for item in influence["checks"]), "report": "internal/retrieval_influence_report.json"}}
        if quality != expected_quality:
            raise RetrievalEnhancedResultPackageError("result summary quality gates are inconsistent")

    def to_dict(self) -> dict[str, str]:
        return {"schema_version": self.schema_version, "package_id": self.package_id, "page_id": self.page_id, "package_dir": str(self.package_dir), "entrypoint": self.entrypoint, "result_summary": self.result_summary, "package_manifest": self.package_manifest}


class DeterministicRetrievalEnhancedResultPackager:
    """Publish a v2 package from explicit, already-materialized artifacts."""

    def package(self, context: AgentContextBundle, guidance: RetrievalGuidance, guided_build_result: GuidedPageSpecBuildResult, page_spec: PageSpec, render_result: RenderResult, consistency_report: ConsistencyReport, retrieval_influence_report: RetrievalInfluenceReport, package_dir: Path) -> RetrievalEnhancedResultPackage:
        destination = Path(package_dir).expanduser().resolve(strict=False)
        self._validate_destination(destination)
        page_files, internal = self._gate(context, guidance, guided_build_result, page_spec, render_result, consistency_report, retrieval_influence_report)
        package_id = self._derive_package_id(page_files, internal)
        summary = self._build_summary(package_id, page_spec, guidance, guided_build_result, consistency_report, retrieval_influence_report)
        files = {**page_files, **internal, "result_summary.json": _json_bytes(summary)}
        _ensure_no_absolute_paths(files)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            for path, content in sorted(files.items()):
                output = staging.joinpath(*PurePosixPath(path).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(content)
            manifest = {"schema_version": RESULT_PACKAGE_V2_SCHEMA_VERSION, "package_id": package_id, "page_id": page_spec.page_id, "entrypoint": "page/index.html", "result_summary": "result_summary.json", "files": [{"path": path, "size": len(files[path]), "sha256": _sha256(files[path]), "role": _ROLES[path]} for path in sorted(files)]}
            (staging / _MANIFEST).write_bytes(_json_bytes(manifest))
            staged = RetrievalEnhancedResultPackage(package_id, page_spec.page_id, staging)
            staged.validate()
            if destination.exists():
                if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
                    raise RetrievalEnhancedResultPackageError("package_dir already exists and is not empty; refusing to overwrite")
                destination.rmdir()
            os.replace(staging, destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return RetrievalEnhancedResultPackage(package_id, page_spec.page_id, destination)

    @staticmethod
    def _validate_destination(destination: Path) -> None:
        if destination.exists() and (destination.is_symlink() or not destination.is_dir() or any(destination.iterdir())):
            raise RetrievalEnhancedResultPackageError("package_dir already exists and is not empty; refusing to overwrite")

    @staticmethod
    def _gate(context: AgentContextBundle, guidance: RetrievalGuidance, guided: GuidedPageSpecBuildResult, page_spec: PageSpec, render: RenderResult, consistency: ConsistencyReport, influence: RetrievalInfluenceReport) -> tuple[dict[str, bytes], dict[str, bytes]]:
        if not all((isinstance(context, AgentContextBundle), isinstance(guidance, RetrievalGuidance), isinstance(guided, GuidedPageSpecBuildResult), isinstance(page_spec, PageSpec), isinstance(render, RenderResult), isinstance(consistency, ConsistencyReport), isinstance(influence, RetrievalInfluenceReport))):
            raise TypeError("v2 packager requires the seven explicit Req2Web artifact objects")
        context.validate(); guidance.validate(); guided.validate(guidance); page_spec.validate(); consistency.validate(); influence.validate()
        if guided.page_spec.to_dict() != page_spec.to_dict():
            raise RetrievalEnhancedResultPackageError("guided build-result PageSpec does not match the supplied final PageSpec")
        if guidance.target_device != context.target_device or guidance.task_type != context.task_type:
            raise RetrievalEnhancedResultPackageError("guidance identity does not match the supplied Agent context")
        DeterministicResultPackager._validate_context_source(context, page_spec)
        if {page_spec.page_id, render.page_id, consistency.page_id, influence.page_id} != {page_spec.page_id}:
            raise RetrievalEnhancedResultPackageError("final PageSpec, render, consistency, and influence page_id values must match")
        if influence.guidance_bundle_id != guidance.guidance_bundle_id:
            raise RetrievalEnhancedResultPackageError("retrieval influence report references the wrong guidance bundle")
        if not consistency.passed or consistency.summary.get("fail") != 0:
            raise RetrievalEnhancedResultPackageError("consistency report did not pass")
        checks = {item.check_id: item.status for item in consistency.checks}
        missing = [name for name in _REQUIRED_REPORT_CHECKS if checks.get(name) != "pass"]
        if missing:
            raise RetrievalEnhancedResultPackageError("consistency report lacks required passing package gates: " + ", ".join(missing))
        if not influence.passed or influence.renderer_consistency.get("passed") is not True or influence.renderer_consistency.get("summary") != consistency.summary:
            raise RetrievalEnhancedResultPackageError("retrieval influence report did not pass the final renderer consistency gate")
        render_dir = Path(render.output_dir).expanduser().resolve(strict=False)
        rendered: dict[str, bytes] = {}
        for name, value in {"index.html": render.index_html, "styles.css": render.styles_css, "app.js": render.app_js, "render_manifest.json": render.render_manifest}.items():
            path = Path(value).expanduser().resolve(strict=True)
            if path != render_dir / name or not path.is_file():
                raise RetrievalEnhancedResultPackageError(f"RenderResult path is not the canonical output file: {name}")
            rendered[name] = path.read_bytes()
            rendered[name].decode("utf-8")
        DeterministicResultPackager._validate_render_manifest(rendered, page_spec.page_id)
        return ({f"page/{name}": rendered[name] for name in (*_RENDER_FILES, "render_manifest.json")}, {"internal/agent_context.json": _json_bytes(context.to_dict()), "internal/retrieval_guidance.json": _json_bytes(guidance.to_dict()), "internal/guided_page_spec_build_result.json": _json_bytes(guided.to_dict()), "internal/page_spec.json": _json_bytes(page_spec.to_dict()), "internal/consistency_report.json": _json_bytes(consistency.to_dict()), "internal/retrieval_influence_report.json": _json_bytes(influence.to_dict())})

    @staticmethod
    def _derive_package_id(page_files: dict[str, bytes], internal: dict[str, bytes]) -> str:
        digest = hashlib.sha256()
        for path, content in sorted({**page_files, **internal}.items()):
            encoded = path.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big")); digest.update(encoded)
            digest.update(len(content).to_bytes(8, "big")); digest.update(content)
        return "result-v2-" + digest.hexdigest()[:20]

    @staticmethod
    def _build_summary(package_id: str, page_spec: PageSpec, guidance: RetrievalGuidance, guided: GuidedPageSpecBuildResult, consistency: ConsistencyReport, influence: RetrievalInfluenceReport) -> dict[str, Any]:
        interactions = [{"step": index, "use_case_ids": item.use_case_ids, "trigger_component_id": item.trigger_component_id, "action": item.action, "user_feedback": item.user_feedback, "target_state_id": item.target_state_id} for index, item in enumerate(page_spec.interactions, 1)]
        role_summaries = [{key: value for key, value in item.items()} for item in influence.role_summaries]
        return {"schema_version": RESULT_SUMMARY_V2_SCHEMA_VERSION, "package_id": package_id, "page_id": page_spec.page_id, "title": page_spec.title, "summary": page_spec.summary, "target_device": page_spec.target_device, "page_type": page_spec.page_type, "entrypoint": "page/index.html", "text_description": f"{page_spec.title}. {page_spec.summary}", "ui_references": [{"doc_id": item.doc_id, "title": item.title, "reference_uris": item.reference_uris} for item in page_spec.traceability.evidence if item.role == "ui_reference"], "interaction_flow": interactions, "interaction_flow_semantics": "stable_interaction_list; entries describe normal and recovery scenarios and are not a strict single execution path.", "retrieval_enhancement": {"guidance_bundle_id": guidance.guidance_bundle_id, "guided_build_result_id": guided.build_result_id, "retrieval_influence_report_id": influence.report_id, "role_summaries": role_summaries, "role_ablation_conclusions": [{"role": item.role, "outcome": item.outcome, "difference_count": item.difference_count} for item in influence.ablations], "structural_influence": {"field_category_counts": influence.field_category_counts, "decision_status_counts": influence.decision_status_counts}}, "quality_gate": {"passed": True, "consistency": {"passed": consistency.passed, **consistency.summary, "warnings": consistency.warnings, "report": "internal/consistency_report.json"}, "retrieval_influence": {"passed": influence.passed, "total": len(influence.checks), "pass": sum(item.status == "pass" for item in influence.checks), "fail": sum(item.status == "fail" for item in influence.checks), "report": "internal/retrieval_influence_report.json"}}, "artifacts": [{"path": path, "role": _ROLES[path]} for path in _CONTENT_FILES if path != "result_summary.json"]}
