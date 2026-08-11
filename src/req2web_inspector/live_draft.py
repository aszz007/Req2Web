"""Local deterministic draft support for the single Req2Web Inspector.

This module deliberately does not implement a second canonical full flow. It
adapts the existing deterministic guided/G0 components for user-facing local
drafts and reports every unavailable model or browser stage explicitly.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import threading
import uuid
import zipfile
from typing import Any, Iterable

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain
from req2web_generation import (
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RESULT_PACKAGE_SCHEMA_VERSION,
    RESULT_PACKAGE_V2_SCHEMA_VERSION,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
    RetrievalEnhancedResultPackage,
    ResultPackage,
)
from req2web_rag import RetrieverConfig, create_retriever
from req2web_rag.corpus import ROLE_ORDER


RUN_SCHEMA_VERSION = "req2web.inspector.local_draft_run.v1"
DIAGNOSTIC_SCHEMA_VERSION = "req2web.inspector.requirement_diagnostics.v1"
STORE_SCHEMA_VERSION = "req2web.inspector.local_run_store.v1"
STORE_MARKER = ".req2web-inspector-run-store.json"
MAX_REQUEST_CHARS = 12_000
MAX_CONSTRAINTS = 12
MAX_CONSTRAINT_CHARS = 500
MAX_IMPORT_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_IMPORT_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_IMPORT_FILE_COUNT = 512
_RUN_ID = re.compile(r"(?:draft|import)-[0-9a-f]{12}\Z")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ACTION_WORDS = re.compile(
    r"\b(build|create|design|make|show|manage|track|search|find|upload|"
    r"submit|review|compare|generate|display|allow|support|need|want)\b",
    re.IGNORECASE,
)
_CJK_ACTION_WORDS = re.compile(
    "(?:\\u505a|\\u5efa|\\u751f\\u6210|\\u5c55\\u793a|"
    "\\u7ba1\\u7406|\\u641c\\u7d22|\\u67e5\\u627e|\\u4e0a\\u4f20|"
    "\\u63d0\\u4ea4|\\u67e5\\u770b|\\u6bd4\\u8f83|\\u652f\\u6301|"
    "\\u9700\\u8981|\\u60f3\\u8981|\\u586b\\u5199|\\u9009\\u62e9|"
    "\\u91cd\\u8bd5|\\u786e\\u8ba4)"
)
_MOBILE_SIGNALS = re.compile(
    r"\b(mobile|phone|smartphone)\b|(?:\u624b\u673a|\u79fb\u52a8\u7aef)",
    re.IGNORECASE,
)
_DESKTOP_SIGNALS = re.compile(
    r"\b(desktop|computer|pc)\b|(?:\u7535\u8111|\u684c\u9762\u7aef)",
    re.IGNORECASE,
)


class InspectorLiveDraftError(ValueError):
    """Raised when a local draft request or run store fails closed."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def _atomic_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _normalize_text(value: object, field_name: str, *, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InspectorLiveDraftError(f"{field_name} must be a string")
    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) > maximum:
        raise InspectorLiveDraftError(
            f"{field_name} must contain at most {maximum} characters"
        )
    if _CONTROL.search(normalized):
        raise InspectorLiveDraftError(
            f"{field_name} contains unsupported control characters"
        )
    return normalized


def normalize_request(value: object) -> dict[str, Any]:
    """Validate one user request without changing its semantic content."""

    if not isinstance(value, dict):
        raise InspectorLiveDraftError("request body must be a JSON object")
    allowed = {
        "requirement",
        "target_device",
        "task_type",
        "constraints",
        "retriever_backend",
        "top_k",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise InspectorLiveDraftError(
            "request body contains unsupported fields: " + ", ".join(unknown)
        )
    requirement = _normalize_text(
        value.get("requirement"), "requirement", maximum=MAX_REQUEST_CHARS
    )
    target_device = _normalize_text(
        value.get("target_device"), "target_device", maximum=80
    )
    task_type = _normalize_text(value.get("task_type"), "task_type", maximum=80)
    backend = _normalize_text(
        value.get("retriever_backend") or "tfidf",
        "retriever_backend",
        maximum=40,
    ).casefold()
    if backend != "tfidf":
        raise InspectorLiveDraftError(
            "the local draft route currently permits only the active TF-IDF backend"
        )
    raw_constraints = value.get("constraints", [])
    if not isinstance(raw_constraints, list):
        raise InspectorLiveDraftError("constraints must be a JSON array")
    if len(raw_constraints) > MAX_CONSTRAINTS:
        raise InspectorLiveDraftError(
            f"constraints must contain at most {MAX_CONSTRAINTS} items"
        )
    constraints: list[str] = []
    for index, item in enumerate(raw_constraints):
        normalized = _normalize_text(
            item,
            f"constraints[{index}]",
            maximum=MAX_CONSTRAINT_CHARS,
        )
        if normalized and normalized not in constraints:
            constraints.append(normalized)
    top_k = value.get("top_k", 2)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 5:
        raise InspectorLiveDraftError("top_k must be an integer from 1 through 5")
    return {
        "requirement": requirement,
        "target_device": target_device or None,
        "task_type": task_type or None,
        "constraints": constraints,
        "retriever_backend": backend,
        "top_k": top_k,
    }


def analyze_requirement(value: object) -> dict[str, Any]:
    """Return deterministic diagnostics and a canonical-B preview.

    The preview is produced by the active deterministic requirement authority.
    It is not a B-Aux model call and never mutates the supplied requirement.
    """

    request = normalize_request(value)
    requirement = request["requirement"]
    findings: list[dict[str, str]] = []

    def add(code: str, severity: str, message: str, suggestion: str) -> None:
        findings.append(
            {
                "code": code,
                "severity": severity,
                "message": message,
                "suggestion": suggestion,
            }
        )

    if not requirement:
        add(
            "empty_requirement",
            "blocking",
            "No requirement text was supplied.",
            "Describe the page, its primary user, and at least one action.",
        )
    else:
        compact_length = len(re.sub(r"\W", "", requirement, flags=re.UNICODE))
        if compact_length < 12:
            add(
                "very_short_requirement",
                "warning",
                "The request is extremely short, so the generated draft will rely on generic defaults.",
                "Add the main user action, expected result, and any important failure state.",
            )
        if not (_ACTION_WORDS.search(requirement) or _CJK_ACTION_WORDS.search(requirement)):
            add(
                "action_not_explicit",
                "warning",
                "A primary user action was not detected with confidence.",
                "State what the user should be able to do on the page.",
            )
        if (
            not request["target_device"]
            and _MOBILE_SIGNALS.search(requirement)
            and _DESKTOP_SIGNALS.search(requirement)
        ):
            add(
                "multiple_device_signals",
                "notice",
                "More than one target device is mentioned without an explicit target selection.",
                "Choose Responsive web, or select one primary target before relying on the draft.",
            )
        if _CJK.search(requirement):
            add(
                "multilingual_draft_input",
                "notice",
                "The local draft can preserve multilingual input, but the publication flow remains English-only.",
                "Provide an English version before using a future publication-grade model route.",
            )
        if not request["constraints"]:
            add(
                "constraints_not_explicit",
                "notice",
                "No separate constraints were supplied.",
                "Optionally add accessibility, responsive behavior, permissions, or error recovery requirements.",
            )

    accepted = not any(item["severity"] == "blocking" for item in findings)
    preview: dict[str, Any] | None = None
    if accepted:
        preview = asdict(
            DeterministicRequirementProvider(output_language="en").understand(
                requirement,
                target_device=request["target_device"],
                task_type=request["task_type"],
                constraints=request["constraints"],
            )
        )
    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "accepted_for_deterministic_draft": accepted,
        "raw_requirement_preserved": True,
        "normalized_request": request,
        "findings": findings,
        "canonical_b_preview": preview,
        "semantic_assist": {
            "status": "not_connected_no_model_action",
            "call_count": 0,
            "automatic_retry_count": 0,
            "explanation": (
                "These diagnostics are deterministic preflight checks. The advisory-only "
                "B-Aux model Agent is not executed and cannot write back to canonical B."
            ),
        },
    }


def _new_stage(stage_id: str, label: str, *, status: str = "pending") -> dict[str, Any]:
    return {"stage_id": stage_id, "label": label, "status": status}


def _set_stage(
    record: dict[str, Any],
    stage_id: str,
    status: str,
    *,
    detail: str | None = None,
) -> None:
    for item in record["stages"]:
        if item["stage_id"] == stage_id:
            item["status"] = status
            if detail:
                item["detail"] = detail
            return
    raise InspectorLiveDraftError(f"unknown local draft stage: {stage_id}")


def _deterministic_zip(source: Path, destination: Path) -> None:
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise InspectorLiveDraftError("result package is empty")
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in files:
            if path.is_symlink():
                raise InspectorLiveDraftError("result package contains a symbolic link")
            relative = path.relative_to(source).as_posix()
            if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
                raise InspectorLiveDraftError("result package contains an unsafe path")
            info = zipfile.ZipInfo(f"result-package/{relative}", (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def _safe_import_members(archive_bytes: bytes) -> list[zipfile.ZipInfo]:
    if not archive_bytes:
        raise InspectorLiveDraftError("the imported ZIP is empty")
    if len(archive_bytes) > MAX_IMPORT_ARCHIVE_BYTES:
        raise InspectorLiveDraftError("the imported ZIP exceeds the 32 MiB limit")
    source = io.BytesIO(archive_bytes)
    if not zipfile.is_zipfile(source):
        raise InspectorLiveDraftError("the imported file is not a readable ZIP archive")
    source.seek(0)
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        files = [info for info in infos if not info.is_dir()]
        if not files or len(files) > MAX_IMPORT_FILE_COUNT:
            raise InspectorLiveDraftError(
                "the imported ZIP file count is outside the supported range"
            )
        if sum(info.file_size for info in files) > MAX_IMPORT_EXPANDED_BYTES:
            raise InspectorLiveDraftError("the imported ZIP expands beyond 64 MiB")
        paths: set[str] = set()
        for info in infos:
            if "\\" in info.filename:
                raise InspectorLiveDraftError("the imported ZIP contains a non-portable path")
            path = PurePosixPath(info.filename)
            if (
                path.is_absolute()
                or not path.parts
                or path.parts[0] != "result-package"
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise InspectorLiveDraftError("the imported ZIP contains an unsafe path")
            mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise InspectorLiveDraftError(
                    "the imported ZIP contains a symbolic link or special file"
                )
            if not info.is_dir():
                relative = PurePosixPath(*path.parts[1:]).as_posix()
                if not relative or relative in paths:
                    raise InspectorLiveDraftError(
                        "the imported ZIP contains an empty or duplicate file path"
                    )
                paths.add(relative)
        if archive.testzip() is not None:
            raise InspectorLiveDraftError("the imported ZIP failed its integrity check")
        return files


def _validate_imported_package(package_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = package_dir / "package_manifest.json"
    if not manifest_path.is_file():
        raise InspectorLiveDraftError("the imported ZIP lacks package_manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InspectorLiveDraftError(
            "the imported package manifest is not readable JSON"
        ) from exc
    if not isinstance(manifest, dict):
        raise InspectorLiveDraftError("the imported package manifest must be an object")
    required = ("package_id", "page_id", "entrypoint", "result_summary")
    if any(not isinstance(manifest.get(name), str) for name in required):
        raise InspectorLiveDraftError("the imported package identity is incomplete")
    schema = manifest.get("schema_version")
    if schema == RESULT_PACKAGE_V2_SCHEMA_VERSION:
        package: ResultPackage | RetrievalEnhancedResultPackage = (
            RetrievalEnhancedResultPackage(
                package_id=manifest["package_id"],
                page_id=manifest["page_id"],
                package_dir=package_dir,
                entrypoint=manifest["entrypoint"],
                result_summary=manifest["result_summary"],
            )
        )
    elif schema == RESULT_PACKAGE_SCHEMA_VERSION:
        package = ResultPackage(
            package_id=manifest["package_id"],
            page_id=manifest["page_id"],
            package_dir=package_dir,
            entrypoint=manifest["entrypoint"],
            result_summary=manifest["result_summary"],
            package_manifest="package_manifest.json",
        )
    else:
        raise InspectorLiveDraftError(
            "the imported ZIP is not a supported ResultPackage v1 or v2"
        )
    package.validate()
    summary_path = package_dir.joinpath(*PurePosixPath(package.result_summary).parts)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise InspectorLiveDraftError("the imported result summary must be an object")
    return manifest, summary


class LocalDraftRunStore:
    """Create and list immutable local deterministic draft runs."""

    def __init__(self, root: Path, index_dir: Path) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.index_dir = Path(index_dir).expanduser().resolve(strict=False)
        self._lock = threading.RLock()
        self._prepare_root()

    def _prepare_root(self) -> None:
        if self.root.exists():
            if self.root.is_symlink() or not self.root.is_dir():
                raise InspectorLiveDraftError("local run store must be a regular directory")
            marker = self.root / STORE_MARKER
            if not marker.is_file():
                if any(self.root.iterdir()):
                    raise InspectorLiveDraftError(
                        "refusing to use a non-empty directory without the Inspector run-store marker"
                    )
                _atomic_json(marker, {"schema_version": STORE_SCHEMA_VERSION})
        else:
            self.root.mkdir(parents=True)
            _atomic_json(
                self.root / STORE_MARKER,
                {"schema_version": STORE_SCHEMA_VERSION},
            )
        marker_value = json.loads((self.root / STORE_MARKER).read_text(encoding="utf-8"))
        if marker_value != {"schema_version": STORE_SCHEMA_VERSION}:
            raise InspectorLiveDraftError("local run-store marker is invalid")
        if not self.index_dir.is_dir():
            raise InspectorLiveDraftError("the configured retrieval index directory is missing")

    def _run_dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise InspectorLiveDraftError("run_id is invalid")
        path = (self.root / run_id).resolve(strict=False)
        if path.parent != self.root:
            raise InspectorLiveDraftError("run path escaped the local run store")
        return path

    def _record_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run.json"

    def get(self, run_id: str) -> dict[str, Any]:
        path = self._record_path(run_id)
        if not path.is_file():
            raise InspectorLiveDraftError("local draft run was not found")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("run_id") != run_id:
            raise InspectorLiveDraftError("local draft run record is invalid")
        return value

    def list(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self.root.iterdir():
            if not path.is_dir() or not _RUN_ID.fullmatch(path.name):
                continue
            try:
                rows.append(self.get(path.name))
            except (OSError, ValueError, json.JSONDecodeError):
                rows.append(
                    {
                        "schema_version": RUN_SCHEMA_VERSION,
                        "run_id": path.name,
                        "status": "failed_closed_unreadable_record",
                        "created_at": "",
                    }
                )
        rows.sort(key=lambda item: (str(item.get("created_at", "")), item["run_id"]), reverse=True)
        return rows

    def create(self, value: object) -> dict[str, Any]:
        diagnostics = analyze_requirement(value)
        if not diagnostics["accepted_for_deterministic_draft"]:
            raise InspectorLiveDraftError("requirement diagnostics contain a blocking finding")
        request = diagnostics["normalized_request"]
        run_id = f"draft-{uuid.uuid4().hex[:12]}"
        run_dir = self._run_dir(run_id)
        with self._lock:
            run_dir.mkdir()
        record: dict[str, Any] = {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": run_id,
            "mode": "deterministic_guided_draft",
            "status": "running",
            "created_at": _now(),
            "updated_at": _now(),
            "input": request,
            "diagnostics": diagnostics,
            "authority_boundary": {
                "canonical_full_flow_executed": False,
                "model_or_f1_f4_executed": False,
                "semantic_aux_agent_executed": False,
                "browser_acceptance_executed": False,
                "result_kind": "component_level_deterministic_guided_g0_draft",
            },
            "stages": [
                _new_stage("input", "Requirement diagnostics", status="completed"),
                _new_stage("canonical_b", "Deterministic requirement understanding"),
                _new_stage("retrieval", "TF-IDF retrieval and context"),
                _new_stage("guidance", "Retrieval guidance"),
                _new_stage("page_spec", "Deterministic guided PageSpec"),
                _new_stage("model_f1_f4", "Model F1-F4 generation", status="not_executed"),
                _new_stage("render", "Offline page rendering"),
                _new_stage("consistency", "Structural consistency"),
                _new_stage("influence", "Retrieval influence checks"),
                _new_stage("package", "ResultPackage v2"),
                _new_stage("browser", "Real browser acceptance", status="not_executed"),
                _new_stage("semantic", "Semantic acceptance assistant", status="not_executed"),
                _new_stage("export", "Deterministic ZIP export"),
            ],
        }
        _atomic_json(run_dir / "run.json", record)
        _atomic_json(run_dir / "input.json", request)
        _atomic_json(run_dir / "diagnostics.json", diagnostics)
        active_stage = "canonical_b"
        render_temporary: Path | None = None
        try:
            provider = DeterministicRequirementProvider(output_language="en")
            retriever = create_retriever(
                RetrieverConfig(
                    index_dir=self.index_dir,
                    backend=request["retriever_backend"],
                )
            )
            context = MinimalAgentChain(
                provider,
                retriever,
                top_k_per_role=request["top_k"],
            ).run(
                request["requirement"],
                target_device=request["target_device"],
                task_type=request["task_type"],
                constraints=request["constraints"],
            )
            _set_stage(record, "canonical_b", "completed")
            _set_stage(record, "retrieval", "completed")

            active_stage = "guidance"
            guidance = RetrievalGuidanceBuilder().build(context)
            _set_stage(record, "guidance", "completed")

            active_stage = "page_spec"
            baseline = PageSpecBuilder().build(context)
            guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
            ablations = {
                role: RetrievalGuidedPageSpecBuilder().build(
                    context,
                    guidance,
                    disabled_roles=(role,),
                )
                for role in ROLE_ORDER
            }
            _set_stage(record, "page_spec", "completed")

            active_stage = "render"
            render_temporary = run_dir / f".render-{uuid.uuid4().hex}"
            render_temporary.mkdir()
            render = DeterministicPageRenderer().render(
                guided.page_spec,
                render_temporary,
            )
            _set_stage(record, "render", "completed")

            active_stage = "consistency"
            consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
            if not consistency.passed:
                raise InspectorLiveDraftError("structural consistency did not pass")
            _set_stage(record, "consistency", "completed")

            active_stage = "influence"
            influence = RetrievalInfluenceChecker().check(
                context,
                guidance,
                baseline,
                guided,
                ablations,
                render,
            )
            if not influence.passed:
                raise InspectorLiveDraftError("retrieval influence checks did not pass")
            _set_stage(record, "influence", "completed")

            active_stage = "package"
            result = DeterministicRetrievalEnhancedResultPackager().package(
                context,
                guidance,
                guided,
                guided.page_spec,
                render,
                consistency,
                influence,
                run_dir / "package",
            )
            result.validate()
            _set_stage(record, "package", "completed")

            active_stage = "export"
            _deterministic_zip(run_dir / "package", run_dir / "result-package.zip")
            _set_stage(record, "export", "completed")
            summary = json.loads(
                (run_dir / "package" / "result_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            record["status"] = "completed_deterministic_draft"
            record["result"] = {
                "package_id": result.package_id,
                "page_id": result.page_id,
                "title": summary["title"],
                "summary": summary["summary"],
                "entrypoint": f"/api/runs/{run_id}/package/page/index.html",
                "download": f"/api/runs/{run_id}/download",
                "record": f"/api/runs/{run_id}",
            }
        except Exception as exc:
            _set_stage(record, active_stage, "failed_closed", detail=str(exc))
            record["status"] = "failed_closed"
            record["failure"] = {
                "stage_id": active_stage,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        finally:
            if render_temporary is not None and render_temporary.exists():
                shutil.rmtree(render_temporary, ignore_errors=True)
            record["updated_at"] = _now()
            _atomic_json(run_dir / "run.json", record)
        return record

    def import_package_zip(self, archive_bytes: bytes, source_filename: str) -> dict[str, Any]:
        """Import one already-complete ResultPackage ZIP after strict local validation."""

        if not isinstance(archive_bytes, bytes):
            raise InspectorLiveDraftError("the imported ZIP body must be bytes")
        filename = _normalize_text(source_filename, "source_filename", maximum=180)
        if not filename or Path(filename).name != filename or not filename.casefold().endswith(".zip"):
            raise InspectorLiveDraftError("the imported filename must be a simple .zip name")
        members = _safe_import_members(archive_bytes)
        run_id = f"import-{uuid.uuid4().hex[:12]}"
        run_dir = self._run_dir(run_id)
        staging = self.root / f".{run_id}.staging-{uuid.uuid4().hex}"
        package_dir = staging / "package"
        try:
            package_dir.mkdir(parents=True)
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                for info in members:
                    path = PurePosixPath(info.filename)
                    relative = PurePosixPath(*path.parts[1:])
                    output = package_dir.joinpath(*relative.parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    content = archive.read(info)
                    if len(content) != info.file_size:
                        raise InspectorLiveDraftError(
                            "the imported ZIP member size changed during extraction"
                        )
                    output.write_bytes(content)
            manifest, summary = _validate_imported_package(package_dir)
            _atomic_bytes(staging / "result-package.zip", archive_bytes)
            created_at = _now()
            record: dict[str, Any] = {
                "schema_version": RUN_SCHEMA_VERSION,
                "run_id": run_id,
                "mode": "validated_result_package_import",
                "status": "completed_imported_result_package",
                "created_at": created_at,
                "updated_at": created_at,
                "input": {
                    "source_filename": filename,
                    "archive_byte_length": len(archive_bytes),
                    "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
                },
                "authority_boundary": {
                    "canonical_full_flow_executed": False,
                    "model_or_f1_f4_executed": False,
                    "semantic_aux_agent_executed": False,
                    "browser_acceptance_executed": False,
                    "result_kind": "validated_imported_result_package",
                },
                "stages": [
                    _new_stage("upload", "Local ZIP intake", status="completed"),
                    _new_stage("archive", "Archive safety checks", status="completed"),
                    _new_stage("package", "Existing ResultPackage validation", status="completed"),
                    _new_stage("model_f1_f4", "Model F1-F4 generation", status="not_executed"),
                    _new_stage("browser", "Real browser acceptance", status="not_executed"),
                    _new_stage("semantic", "Semantic acceptance assistant", status="not_executed"),
                ],
                "source_package_schema_version": manifest["schema_version"],
                "result": {
                    "package_id": manifest["package_id"],
                    "page_id": manifest["page_id"],
                    "title": str(summary.get("title") or manifest["page_id"]),
                    "summary": str(summary.get("summary") or "Validated imported ResultPackage."),
                    "entrypoint": f"/api/runs/{run_id}/package/{manifest['entrypoint']}",
                    "download": f"/api/runs/{run_id}/download",
                    "record": f"/api/runs/{run_id}",
                },
            }
            _atomic_json(staging / "run.json", record)
            with self._lock:
                if run_dir.exists():
                    raise InspectorLiveDraftError("the imported run identity already exists")
                os.replace(staging, run_dir)
            return record
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

    def artifact_path(self, run_id: str, relative: str) -> Path:
        run_dir = self._run_dir(run_id)
        candidate = (run_dir / relative).resolve(strict=False)
        if candidate == run_dir or run_dir not in candidate.parents:
            raise InspectorLiveDraftError("artifact path escaped the local run")
        if not candidate.is_file() or candidate.is_symlink():
            raise InspectorLiveDraftError("local draft artifact was not found")
        return candidate
