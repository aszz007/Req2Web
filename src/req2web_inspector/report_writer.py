"""Deterministic static writer for M2 Inspector trace artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

from .trace import (
    INSPECTOR_ELEMENT_ACCEPTANCE_TRACE_SCHEMA_VERSION,
    INSPECTOR_TRACE_UNAVAILABLE_SCHEMA_VERSION,
    InspectorElementAcceptanceTraceReport,
    InspectorTraceUnavailable,
)

INSPECTOR_TRACE_MANIFEST_SCHEMA_VERSION = "req2web.inspector.trace_manifest.v1"
INSPECTOR_TRACE_REPORT_WRITE_SCHEMA_VERSION = "req2web.inspector.trace_report_write.v1"
_JSON_NAME = "inspector_trace.json"
_MARKDOWN_NAME = "inspector_trace.md"
_MANIFEST_NAME = "inspector_trace_manifest.json"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class InspectorTraceWriterError(ValueError):
    """Raised for unsafe report destinations or non-reproducible report output."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    return f"{prefix}-{sha256(_canonical_bytes(payload)).hexdigest()[:20]}"


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InspectorTraceWriterError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class InspectorTraceManifestFile:
    file_name: str
    byte_size: int
    sha256: str

    def validate(self) -> None:
        if self.file_name not in {_JSON_NAME, _MARKDOWN_NAME}:
            raise InspectorTraceWriterError("manifest may list only fixed static report file names")
        if not isinstance(self.byte_size, int) or isinstance(self.byte_size, bool) or self.byte_size < 0:
            raise InspectorTraceWriterError("manifest byte_size must be a non-negative integer")
        if not isinstance(self.sha256, str) or not _HASH_RE.fullmatch(self.sha256):
            raise InspectorTraceWriterError("manifest sha256 must be a lowercase SHA-256 digest")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"byte_size": self.byte_size, "file_name": self.file_name, "sha256": self.sha256}


@dataclass(frozen=True)
class InspectorTraceManifest:
    manifest_id: str
    trace_schema_version: str
    trace_sha256: str
    files: tuple[InspectorTraceManifestFile, ...]
    schema_version: str = INSPECTOR_TRACE_MANIFEST_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("manifest_id", None)
        return value

    def validate(self) -> None:
        if self.schema_version != INSPECTOR_TRACE_MANIFEST_SCHEMA_VERSION:
            raise InspectorTraceWriterError("unsupported trace manifest schema")
        _text(self.manifest_id, "manifest_id")
        if self.trace_schema_version not in {
            INSPECTOR_ELEMENT_ACCEPTANCE_TRACE_SCHEMA_VERSION,
            INSPECTOR_TRACE_UNAVAILABLE_SCHEMA_VERSION,
        }:
            raise InspectorTraceWriterError("manifest trace schema is unsupported")
        if not isinstance(self.trace_sha256, str) or not _HASH_RE.fullmatch(self.trace_sha256):
            raise InspectorTraceWriterError("trace_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.files, tuple) or len(self.files) != 2:
            raise InspectorTraceWriterError("manifest must contain exactly JSON and Markdown files")
        names: list[str] = []
        for item in self.files:
            if not isinstance(item, InspectorTraceManifestFile):
                raise InspectorTraceWriterError("manifest files contain an invalid record")
            item.validate()
            names.append(item.file_name)
        if names != sorted(names) or set(names) != {_JSON_NAME, _MARKDOWN_NAME}:
            raise InspectorTraceWriterError("manifest file list must be sorted and complete")
        if self.manifest_id != _stable_id("inspector-trace-manifest", self.to_payload()):
            raise InspectorTraceWriterError("manifest_id is not canonical")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "files": [item.to_dict() for item in self.files],
            "manifest_id": self.manifest_id,
            "schema_version": self.schema_version,
            "trace_schema_version": self.trace_schema_version,
            "trace_sha256": self.trace_sha256,
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


@dataclass(frozen=True)
class InspectorTraceWriteResult:
    output_dir: str
    json_path: str
    markdown_path: str
    manifest_path: str
    manifest: InspectorTraceManifest
    schema_version: str = INSPECTOR_TRACE_REPORT_WRITE_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != INSPECTOR_TRACE_REPORT_WRITE_SCHEMA_VERSION:
            raise InspectorTraceWriterError("unsupported trace write-result schema")
        for name in ("output_dir", "json_path", "markdown_path", "manifest_path"):
            _text(getattr(self, name), name)
        if not isinstance(self.manifest, InspectorTraceManifest):
            raise InspectorTraceWriterError("write result requires a trace manifest")
        self.manifest.validate()


def write_inspector_trace_report(
    trace: InspectorElementAcceptanceTraceReport | InspectorTraceUnavailable,
    output_dir: str | Path,
) -> InspectorTraceWriteResult:
    """Write fixed-name canonical JSON, English Markdown, and a hash manifest.

    The writer accepts only a local output directory. It neither reads URLs nor
    embeds assets, HTML, JavaScript, PageSpec packages, or any input artifact.
    """

    if not isinstance(trace, (InspectorElementAcceptanceTraceReport, InspectorTraceUnavailable)):
        raise TypeError("trace must be an Inspector trace artifact")
    trace.validate()
    destination = _prepare_output_dir(output_dir)
    json_bytes = trace.canonical_json_bytes()
    markdown_bytes = _markdown(trace).encode("utf-8")
    json_path = destination / _JSON_NAME
    markdown_path = destination / _MARKDOWN_NAME
    json_path.write_bytes(json_bytes)
    markdown_path.write_bytes(markdown_bytes)
    files = tuple(
        sorted(
            (
                InspectorTraceManifestFile(_JSON_NAME, len(json_bytes), sha256(json_bytes).hexdigest()),
                InspectorTraceManifestFile(_MARKDOWN_NAME, len(markdown_bytes), sha256(markdown_bytes).hexdigest()),
            ),
            key=lambda item: item.file_name,
        )
    )
    manifest = InspectorTraceManifest("", trace.schema_version, trace.sha256(), files)
    manifest = replace(manifest, manifest_id=_stable_id("inspector-trace-manifest", manifest.to_payload()))
    manifest.validate()
    manifest_path = destination / _MANIFEST_NAME
    manifest_path.write_bytes(manifest.canonical_json_bytes())
    _verify_written(destination, trace, manifest)
    result = InspectorTraceWriteResult(
        output_dir=str(destination), json_path=str(json_path), markdown_path=str(markdown_path),
        manifest_path=str(manifest_path), manifest=manifest,
    )
    result.validate()
    return result


def _prepare_output_dir(output_dir: str | Path) -> Path:
    raw = Path(output_dir)
    if any(parent.exists() and parent.is_symlink() for parent in (raw, *raw.parents)):
        raise InspectorTraceWriterError("output_dir must not be inside a symlinked path")
    if not str(raw).strip() or ".." in raw.parts:
        raise InspectorTraceWriterError("output_dir must not contain parent traversal")
    if raw.exists():
        if raw.is_symlink() or not raw.is_dir():
            raise InspectorTraceWriterError("output_dir must be a real directory")
        if any(raw.iterdir()):
            raise InspectorTraceWriterError("output_dir must be empty to prevent replacement of unrelated files")
    else:
        raw.mkdir(parents=True, exist_ok=False)
    resolved = raw.resolve()
    if resolved.is_symlink():
        raise InspectorTraceWriterError("output_dir must not resolve through a symlink")
    return resolved


def _verify_written(destination: Path, trace: InspectorElementAcceptanceTraceReport | InspectorTraceUnavailable, manifest: InspectorTraceManifest) -> None:
    expected = {
        _JSON_NAME: trace.canonical_json_bytes(),
        _MARKDOWN_NAME: _markdown(trace).encode("utf-8"),
    }
    for item in manifest.files:
        actual = (destination / item.file_name).read_bytes()
        if actual != expected[item.file_name] or len(actual) != item.byte_size or sha256(actual).hexdigest() != item.sha256:
            raise InspectorTraceWriterError("written trace report does not match its manifest")
    if (destination / _MANIFEST_NAME).read_bytes() != manifest.canonical_json_bytes():
        raise InspectorTraceWriterError("written manifest is not canonical")


def _markdown(trace: InspectorElementAcceptanceTraceReport | InspectorTraceUnavailable) -> str:
    lines = [
        "# Req2Web Inspector Element-to-Acceptance Trace",
        "",
        "This static report records exact local identity joins and structural gaps. "
        "`verified` means structural identity only; it does not claim semantic correctness.",
        "",
        "## Run",
        "",
        f"- Schema: `{trace.schema_version}`",
        f"- Run group: `{trace.run_group}`",
    ]
    if isinstance(trace, InspectorTraceUnavailable):
        lines.extend([
            f"- Status: `{trace.status}`",
            f"- Error code: `{trace.error_code}`",
            f"- Fact-unavailable SHA-256: `{trace.fact_unavailable_sha256}`",
            "",
            "## Missing Artifacts",
            "",
        ])
        lines.extend(f"- `{item}`" for item in trace.missing_artifacts)
        return "\n".join(lines) + "\n"

    lines.extend([
        f"- Case: `{trace.case_id}`",
        f"- Page: `{trace.page_id}`",
        f"- Fact set: `{trace.fact_set_id}`",
        f"- Fact-set SHA-256: `{trace.fact_set_sha256}`",
        "",
        "## Retrieval Influence",
        "",
        f"- Overall structural result: `{'pass' if trace.retrieval_influence_report_passed else 'fail'}`",
        f"- Report ID: `{trace.retrieval_influence_report_id}`",
        "",
        "| Check ID | Status |",
        "| --- | --- |",
    ])
    lines.extend(f"| `{check_id}` | `{status}` |" for check_id, status in trace.influence_checks)
    lines.extend(["", "## Sources", "", "| Source ref | Kind | Role | Guidance | Document |", "| --- | --- | --- | --- | --- |"])
    lines.extend(
        f"| `{item.source_ref_id}` | `{item.source_kind}` | `{item.role or 'not_applicable'}` | `{item.guidance_id or 'not_applicable'}` | `{item.doc_id or 'not_applicable'}` |"
        for item in trace.sources
    )
    lines.extend(["", "## Source-to-Use-Case Links", "", "| Source ref | Structural status | Use cases |", "| --- | --- | --- |"])
    lines.extend(
        f"| `{item.source_ref_id}` | `{item.status}` | {', '.join(f'`{value}`' for value in item.use_case_ids) or '`not_applicable`'} |"
        for item in trace.source_use_case_links
    )
    lines.extend(["", "## Decision Observations", "", "| Decision | Disposition | Source | Element links |", "| --- | --- | --- | --- |"])
    lines.extend(
        f"| `{item.decision_id}` | `{item.disposition}` | `{item.source_ref_id}` | {', '.join(f'`{value}`' for value in item.trace_link_ids) or '`none`'} |"
        for item in trace.decision_observations
    )
    lines.extend(["", "## Element Traces", ""])
    for item in trace.element_traces:
        lines.extend([
            f"### `{item.field_path}`",
            "",
            f"- Entity: `{item.entity_id}`",
            f"- G0 decision: `{item.decision_id}`",
            f"- Candidate decisions: {', '.join(f'`{value}`' for value in item.candidate_decision_ids) or '`missing`'}",
        ])
        for alignment in (value for value in trace.alignment_traces if value.element_trace_id == item.element_trace_id):
            lines.append(f"- Alignment: candidate `{alignment.candidate_decision_id}` -> gold `{alignment.gold_unit_id}` via `{alignment.alignment_id}`")
        for criterion in (value for value in trace.criterion_traces if value.element_trace_id == item.element_trace_id):
            outcomes = ", ".join(f"`{requirement}`=`{outcome}`" for requirement, outcome in criterion.requirement_outcomes)
            lines.append(f"- Criterion `{criterion.criterion_id}` / binding `{criterion.binding_id}`: runtime `{criterion.runtime_status}`; requirement outcome(s) {outcomes}")
            if criterion.runtime_steps:
                lines.append("  - Steps: " + ", ".join(f"`{step.step_id}`=`{step.status}`" for step in criterion.runtime_steps))
        lines.append("")
    lines.extend(["## Complete Acceptance Status", "", "| Gold | Criterion | Binding | Runtime | Requirement outcome(s) |", "| --- | --- | --- | --- | --- |"])
    lines.extend(
        f"| `{item.gold_unit_id}` | `{item.criterion_id}` | `{item.binding_id}` ({item.binding_disposition}) | `{item.runtime_status}` | "
        + ", ".join(f"`{requirement}`=`{outcome}`" for requirement, outcome in item.requirement_outcomes) + " |"
        for item in trace.acceptance_observations
    )
    lines.extend(["", "## Structural Gaps", "", "| Owner | Hop | Status | Related IDs |", "| --- | --- | --- | --- |"])
    lines.extend(
        f"| `{item.owner_id}` | `{item.hop}` | `{item.status}` | {', '.join(f'`{value}`' for value in item.related_ids) or '`none`'} |"
        for item in trace.gaps
    )
    return "\n".join(lines) + "\n"
