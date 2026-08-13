"""Asynchronous final-Inspector entry for the existing canonical full flow.

The store coordinates existing authorities only. It does not define another
requirement adapter, prompt, F1-F4 loop, graph, renderer, acceptance policy, or
semantic evaluator. Local model actions remain explicit, single-flight, and
fail closed while the same-context deterministic G0 package stays separately
available.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading
from typing import Any, Callable, Mapping
import uuid
import zipfile

from req2web_evaluation.phase5_semantic_qwen_runtime import (
    LOW_GPU_PROFILE as SEMANTIC_LOW_GPU_PROFILE,
    run_phase5_semantic_qwen,
)
from req2web_inspector.live_draft import analyze_requirement
from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    make_identity,
)
from req2web_runtime.phase4_browser_acceptance import run_real_browser_case_audit
from req2web_runtime.phase4_canonical_full_flow import _build_upstream
from req2web_runtime.phase4_local_qwen_langgraph_integrated import (
    LOW_GPU_PROFILE,
    local_langgraph_profile,
    run_phase4_local_qwen_langgraph_integrated,
    runtime_capabilities,
)


STORE_SCHEMA_VERSION = "req2web.inspector.canonical_run_store.v1"
RUN_SCHEMA_VERSION = "req2web.inspector.canonical_run.v1"
STORE_MARKER = ".req2web-inspector-canonical-run-store.json"
RUN_ID = re.compile(r"canonical-[0-9a-f]{12}\Z")


class InspectorCanonicalRunError(ValueError):
    """Raised when the final Inspector canonical route cannot continue safely."""


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
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def _read_json(path: Path, name: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise InspectorCanonicalRunError(f"{name} is unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InspectorCanonicalRunError(f"{name} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise InspectorCanonicalRunError(f"{name} must be an object")
    return value


def _bool(value: object, name: str, *, default: bool) -> bool:
    selected = default if value is None else value
    if type(selected) is not bool:
        raise InspectorCanonicalRunError(f"{name} must be a boolean")
    return selected


def _stage(stage_id: str, label: str, status: str = "pending") -> dict[str, object]:
    return {"stage_id": stage_id, "label": label, "status": status}


def _set_stage(
    record: dict[str, Any],
    stage_id: str,
    status: str,
    *,
    detail: str | None = None,
) -> None:
    for stage in record["stages"]:
        if stage["stage_id"] == stage_id:
            stage["status"] = status
            if detail is not None:
                stage["detail"] = detail
            return
    raise InspectorCanonicalRunError(f"unknown canonical run stage: {stage_id}")


def _deterministic_zip(source: Path, destination: Path) -> None:
    rows = sorted(path for path in source.rglob("*") if path.is_file())
    if not rows:
        raise InspectorCanonicalRunError("selected ResultPackage is empty")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in rows:
                if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                    raise InspectorCanonicalRunError(
                        "selected ResultPackage contains an unsupported file"
                    )
                relative = path.relative_to(source).as_posix()
                info = zipfile.ZipInfo(
                    f"result-package/{relative}",
                    (1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


BrowserRunner = Callable[..., dict[str, object]]
SemanticRunner = Callable[..., dict[str, object]]
RequirementAssistRunner = Callable[[object], dict[str, object]]


class CanonicalInspectorRunStore:
    """Run one explicit local canonical job at a time and expose its evidence."""

    def __init__(
        self,
        *,
        root: Path,
        index_dir: Path,
        model_root: Path,
        integrity_evidence: Path,
        profile_name: str = LOW_GPU_PROFILE,
        requirement_assist_runner: RequirementAssistRunner | None = None,
        browser_runner: BrowserRunner = run_real_browser_case_audit,
        semantic_runner: SemanticRunner = run_phase5_semantic_qwen,
        semantic_profile_name: str = SEMANTIC_LOW_GPU_PROFILE,
        _raw_node_generator: Callable[[str, Mapping[str, object]], bytes] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.index_dir = Path(index_dir).expanduser().resolve(strict=True)
        self.model_root = Path(model_root).expanduser().resolve(strict=True)
        self.integrity_evidence = Path(integrity_evidence).expanduser().resolve(
            strict=True
        )
        self.profile = local_langgraph_profile(profile_name)
        self.semantic_profile_name = semantic_profile_name
        self.requirement_assist_runner = requirement_assist_runner
        self.browser_runner = browser_runner
        self.semantic_runner = semantic_runner
        self._raw_node_generator = _raw_node_generator
        self._record_lock = threading.RLock()
        self._model_slot = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._prepare_root()

    def _prepare_root(self) -> None:
        if self.root.exists():
            if self.root.is_symlink() or not self.root.is_dir():
                raise InspectorCanonicalRunError(
                    "canonical run store must be a regular directory"
                )
            marker = self.root / STORE_MARKER
            if not marker.is_file():
                if any(self.root.iterdir()):
                    raise InspectorCanonicalRunError(
                        "refusing a non-empty canonical run directory without its marker"
                    )
                _atomic_json(marker, {"schema_version": STORE_SCHEMA_VERSION})
        else:
            self.root.mkdir(parents=True)
            _atomic_json(
                self.root / STORE_MARKER,
                {"schema_version": STORE_SCHEMA_VERSION},
            )
        marker_value = _read_json(self.root / STORE_MARKER, "canonical store marker")
        if marker_value != {"schema_version": STORE_SCHEMA_VERSION}:
            raise InspectorCanonicalRunError("canonical run store marker drifted")
        if self.model_root.is_symlink() or not self.model_root.is_dir():
            raise InspectorCanonicalRunError("local Qwen model root is invalid")
        if self.integrity_evidence.is_symlink() or not self.integrity_evidence.is_file():
            raise InspectorCanonicalRunError("model integrity evidence is invalid")

    def capability(self) -> dict[str, object]:
        return {
            **runtime_capabilities(),
            "status": "available_explicit_single_flight_local_model_action",
            "profile": self.profile.to_dict(),
            "semantic_profile_name": self.semantic_profile_name,
            "asynchronous": True,
            "single_flight": True,
            "requirement_assist_available": self.requirement_assist_runner is not None,
            "real_browser_acceptance_available": True,
            "semantic_acceptance_available": True,
            "deterministic_g0_prebuilt_before_model": True,
            "model_action_active": self.is_busy(),
        }

    def is_busy(self) -> bool:
        """Report whether this store currently owns the local model slot."""

        return self._model_slot.locked()

    def _run_dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise InspectorCanonicalRunError("canonical run ID is invalid")
        selected = (self.root / run_id).resolve(strict=False)
        if selected.parent != self.root:
            raise InspectorCanonicalRunError("canonical run path escaped its store")
        return selected

    def get(self, run_id: str) -> dict[str, Any]:
        return _read_json(self._run_dir(run_id) / "run.json", "canonical run")

    def list(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self.root.iterdir():
            if path.is_dir() and RUN_ID.fullmatch(path.name):
                try:
                    rows.append(self.get(path.name))
                except (OSError, ValueError):
                    rows.append(
                        {
                            "schema_version": RUN_SCHEMA_VERSION,
                            "run_id": path.name,
                            "status": "failed_closed_unreadable_record",
                            "created_at": "",
                        }
                    )
        rows.sort(
            key=lambda item: (str(item.get("created_at", "")), item["run_id"]),
            reverse=True,
        )
        return rows

    def create(self, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise InspectorCanonicalRunError("canonical request must be a JSON object")
        options = {
            "confirm_local_model_action",
            "run_requirement_assist",
            "run_browser_acceptance",
            "run_semantic_acceptance",
        }
        request_value = {key: item for key, item in value.items() if key not in options}
        unknown_options = sorted(
            key
            for key in value
            if key.startswith("run_") or key.startswith("confirm_")
            if key not in options
        )
        if unknown_options:
            raise InspectorCanonicalRunError(
                "canonical request contains unsupported options: "
                + ", ".join(unknown_options)
            )
        if value.get("confirm_local_model_action") is not True:
            raise InspectorCanonicalRunError(
                "explicit local model action confirmation is required"
            )
        diagnostics = analyze_requirement(request_value)
        if not diagnostics["accepted_for_deterministic_draft"]:
            raise InspectorCanonicalRunError(
                "requirement diagnostics contain a blocking finding"
            )
        request = diagnostics["normalized_request"]
        if request["retriever_backend"] != "tfidf" or request["top_k"] != 2:
            raise InspectorCanonicalRunError(
                "canonical runs require the active TF-IDF backend and top_k=2"
            )
        include_assist = _bool(
            value.get("run_requirement_assist"),
            "run_requirement_assist",
            default=False,
        )
        include_browser = _bool(
            value.get("run_browser_acceptance"),
            "run_browser_acceptance",
            default=True,
        )
        include_semantic = _bool(
            value.get("run_semantic_acceptance"),
            "run_semantic_acceptance",
            default=True,
        )
        if include_assist and self.requirement_assist_runner is None:
            raise InspectorCanonicalRunError(
                "semantic requirement assistance was requested but is unavailable"
            )
        if include_semantic and not include_browser:
            raise InspectorCanonicalRunError(
                "semantic acceptance requires fresh real-browser evidence"
            )
        if not self._model_slot.acquire(blocking=False):
            raise InspectorCanonicalRunError(
                "another canonical local-model run is already active"
            )
        run_id = f"canonical-{uuid.uuid4().hex[:12]}"
        run_dir = self._run_dir(run_id)
        try:
            run_dir.mkdir()
            record: dict[str, Any] = {
                "schema_version": RUN_SCHEMA_VERSION,
                "run_id": run_id,
                "mode": "canonical_local_qwen_full_flow",
                "status": "queued",
                "created_at": _now(),
                "updated_at": _now(),
                "input": request,
                "diagnostics": diagnostics,
                "options": {
                    "run_requirement_assist": include_assist,
                    "run_browser_acceptance": include_browser,
                    "run_semantic_acceptance": include_semantic,
                },
                "profile": self.profile.to_dict(),
                "stages": [
                    _stage("input", "Requirement diagnostics", "completed"),
                    _stage(
                        "b_aux",
                        "Advisory semantic requirement Agent",
                        "pending" if include_assist else "not_requested",
                    ),
                    _stage("upstream", "Canonical B and retrieval context"),
                    _stage("model", "Local Qwen load and integrity preflight"),
                    *[_stage(node_id, f"{node_id} model Agent") for node_id in NODE_ORDER],
                    _stage("compose", "Registry, mapping, composition, and assembly"),
                    _stage("package", "Renderer, gates, and final ResultPackage"),
                    _stage(
                        "browser",
                        "Real browser acceptance",
                        "pending" if include_browser else "not_requested",
                    ),
                    _stage(
                        "semantic",
                        "Semantic acceptance Agent",
                        "pending" if include_semantic else "not_requested",
                    ),
                    _stage("export", "ResultPackage ZIP export"),
                ],
                "authority_boundary": {
                    "sole_canonical_b_adapter": True,
                    "sole_prompt_authority": True,
                    "sole_formal_langgraph_runtime": True,
                    "b_aux_writeback": False,
                    "b_aux_consumed_by_f1_f4": False,
                    "one_call_per_model_node": True,
                    "automatic_retry_count": 0,
                    "formal_quality_claimed": False,
                    "h1_or_gold_access": False,
                    "external_service_used": False,
                },
            }
            _atomic_json(run_dir / "run.json", record)
            thread = threading.Thread(
                target=self._execute,
                args=(run_id,),
                name=f"req2web-{run_id}",
                daemon=True,
            )
            with self._record_lock:
                self._threads[run_id] = thread
            thread.start()
            return record
        except Exception:
            self._model_slot.release()
            raise

    def _save(self, run_id: str, record: dict[str, Any]) -> None:
        record["updated_at"] = _now()
        with self._record_lock:
            _atomic_json(self._run_dir(run_id) / "run.json", record)

    def _execute(self, run_id: str) -> None:
        run_dir = self._run_dir(run_id)
        record = self.get(run_id)
        active_stage = "upstream"
        try:
            record["status"] = "running"
            self._save(run_id, record)
            b_aux_sidecar: Mapping[str, object] | None = None
            if record["options"]["run_requirement_assist"]:
                active_stage = "b_aux"
                try:
                    assert self.requirement_assist_runner is not None
                    assist = self.requirement_assist_runner(record["input"])
                    record["requirement_assist"] = assist
                    if assist.get("status") == "advisory_available" and isinstance(
                        assist.get("sidecar"), Mapping
                    ):
                        b_aux_sidecar = assist["sidecar"]
                        _set_stage(record, "b_aux", "completed_advisory_only")
                    else:
                        _set_stage(record, "b_aux", "failed_closed_nonblocking")
                except Exception as exc:
                    record["requirement_assist_failure"] = {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                    _set_stage(
                        record,
                        "b_aux",
                        "failed_closed_nonblocking",
                        detail=str(exc),
                    )
                self._save(run_id, record)

            active_stage = "upstream"
            preview = record["diagnostics"]["canonical_b_preview"]
            case = {
                "case_id": f"inspector-{run_id}",
                "request_id": f"inspector-request-{run_id[10:]}",
                "requirement": record["input"]["requirement"],
                "target_device": preview["target_device"],
                "task_type": preview["task_type"],
                "constraints": record["input"]["constraints"],
            }
            upstream = _build_upstream(
                case=case,
                index_dir=self.index_dir,
                output_root=run_dir / "upstream",
            )
            _set_stage(record, "upstream", "completed")
            _set_stage(record, "model", "loading")
            self._save(run_id, record)

            def progress(
                node_id: str,
                status: str,
                attempt: Mapping[str, object] | None,
            ) -> None:
                if node_id == "runtime":
                    _set_stage(record, "model", status)
                else:
                    _set_stage(record, node_id, status)
                    if attempt is not None:
                        record.setdefault("live_attempts", {})[node_id] = {
                            "status": attempt.get("status"),
                            "generate_call_count": attempt.get("generate_call_count"),
                            "automatic_retry_count": attempt.get(
                                "automatic_retry_count"
                            ),
                            "metrics": attempt.get("metrics"),
                            "failure": attempt.get("failure"),
                        }
                        output_path = (
                            run_dir
                            / "canonical"
                            / "attempts"
                            / node_id
                            / "validated_node_output.json"
                        )
                        raw_path = (
                            run_dir
                            / "canonical"
                            / "attempts"
                            / node_id
                            / "raw_response.bin"
                        )
                        record.setdefault("node_evidence", {})[node_id] = {
                            "attempt": (
                                f"/api/canonical-runs/{run_id}/evidence/"
                                f"node-{node_id}-attempt.json"
                            ),
                            "output": (
                                f"/api/canonical-runs/{run_id}/evidence/"
                                f"node-{node_id}-output.json"
                                if output_path.is_file()
                                else None
                            ),
                            "raw": (
                                f"/api/canonical-runs/{run_id}/evidence/"
                                f"node-{node_id}-raw.bin"
                                if raw_path.is_file()
                                else None
                            ),
                        }
                self._save(run_id, record)

            active_stage = "F1"
            summary = run_phase4_local_qwen_langgraph_integrated(
                model_root=self.model_root,
                integrity_evidence=self.integrity_evidence,
                result_root=run_dir / "canonical",
                b_input=upstream["adaptation"].b_input,
                upstream_context=upstream["context"],
                upstream_guidance=upstream["guidance"],
                upstream_binding=upstream["receipt"],
                confirm_one_local_langgraph_run=True,
                profile_name=self.profile.profile_name,
                run_id=run_id,
                b_aux_sidecar=b_aux_sidecar,
                progress_callback=progress,
                _raw_node_generator=self._raw_node_generator,
            )
            record["canonical_summary"] = summary
            _set_stage(
                record,
                "model",
                (
                    "not_executed_test_generator"
                    if self._raw_node_generator is not None
                    else "model_loaded"
                ),
            )
            attempts_root = run_dir / "canonical" / "attempts"
            record.setdefault("node_evidence", {})
            for node_id in NODE_ORDER:
                attempt_path = attempts_root / node_id / "attempt_result.json"
                if attempt_path.is_file():
                    attempt = _read_json(attempt_path, f"{node_id} attempt")
                    _set_stage(record, node_id, str(attempt["status"]))
                    output_path = attempts_root / node_id / "validated_node_output.json"
                    raw_path = attempts_root / node_id / "raw_response.bin"
                    record["node_evidence"][node_id] = {
                        "attempt": (
                            f"/api/canonical-runs/{run_id}/evidence/"
                            f"node-{node_id}-attempt.json"
                        ),
                        "output": (
                            f"/api/canonical-runs/{run_id}/evidence/"
                            f"node-{node_id}-output.json"
                            if output_path.is_file()
                            else None
                        ),
                        "raw": (
                            f"/api/canonical-runs/{run_id}/evidence/"
                            f"node-{node_id}-raw.bin"
                            if raw_path.is_file()
                            else None
                        ),
                    }
                else:
                    _set_stage(record, node_id, "not_started")
            graph_completed = summary["agent_chain_completed"] is True
            _set_stage(
                record,
                "compose",
                "completed" if graph_completed else "not_completed_model_failed_closed",
            )
            _set_stage(
                record,
                "package",
                str(summary["selected_delivery_kind"]),
            )
            package_root = Path(str(summary["selected_package_root"])).resolve(
                strict=True
            )
            relative_package = package_root.relative_to(run_dir).as_posix()
            _deterministic_zip(package_root, run_dir / "result-package.zip")
            _set_stage(record, "export", "completed")
            record["result"] = {
                "selected_delivery_kind": summary["selected_delivery_kind"],
                "model_result_available": summary["model_result_available"],
                "deterministic_g0_available": summary["deterministic_g0_available"],
                "package_relative_path": relative_package,
                "entrypoint": f"/api/canonical-runs/{run_id}/package/page/index.html",
                "download": f"/api/canonical-runs/{run_id}/download",
                "record": f"/api/canonical-runs/{run_id}",
                "page_spec": (
                    f"/api/canonical-runs/{run_id}/package/internal/page_spec.json"
                ),
            }
            self._save(run_id, record)

            browser_audit: dict[str, object] | None = None
            if record["options"]["run_browser_acceptance"]:
                active_stage = "browser"
                source_identity = make_identity(
                    summary,
                    revision=f"{RUN_SCHEMA_VERSION}.canonical_summary.v1",
                )
                try:
                    browser_audit = self.browser_runner(
                        package_root=package_root,
                        output_root=run_dir / "browser",
                        run_id=run_id,
                        case_index=1,
                        case_id=str(case["case_id"]),
                        evidence_scope="phase4_canonical_canary",
                        source_case_summary_identity=source_identity,
                    )
                    record["browser"] = {
                        "browser_status": browser_audit["browser_status"],
                        "browser_execution_status": browser_audit[
                            "browser_execution_status"
                        ],
                        "page_spec_conformance_status": browser_audit[
                            "page_spec_conformance_status"
                        ],
                        "real_browser_executed": browser_audit[
                            "real_browser_executed"
                        ],
                        "interaction_count": len(browser_audit.get("interactions", [])),
                        "console_error_count": sum(
                            isinstance(item, Mapping) and item.get("type") == "error"
                            for item in browser_audit.get("console_messages", [])
                        ),
                        "page_error_count": len(browser_audit.get("page_errors", [])),
                        "screenshot": (
                            f"/api/canonical-runs/{run_id}/evidence/browser-screenshot.png"
                        ),
                        "audit": (
                            f"/api/canonical-runs/{run_id}/evidence/browser-audit.json"
                        ),
                        "execution": (
                            f"/api/canonical-runs/{run_id}/evidence/"
                            "browser-execution.json"
                        ),
                    }
                    _set_stage(record, "browser", str(browser_audit["browser_status"]))
                except Exception as exc:
                    record["browser_failure"] = {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                    _set_stage(record, "browser", "failed_closed", detail=str(exc))
                self._save(run_id, record)

            if record["options"]["run_semantic_acceptance"]:
                active_stage = "semantic"
                if browser_audit is None:
                    _set_stage(
                        record,
                        "semantic",
                        "not_executed_browser_evidence_unavailable",
                    )
                elif (
                    browser_audit.get("browser_execution_status") != "pass"
                    or browser_audit.get("page_spec_conformance_status") != "pass"
                ):
                    record["semantic"] = {
                        "status": "not_executed_objective_browser_gate",
                        "semantic_alignment_executed": False,
                        "automatic_retry_count": 0,
                        "reason": (
                            "Semantic acceptance cannot override unavailable or "
                            "failed objective browser/PageSpec evidence."
                        ),
                        "browser_execution_status": browser_audit.get(
                            "browser_execution_status"
                        ),
                        "page_spec_conformance_status": browser_audit.get(
                            "page_spec_conformance_status"
                        ),
                    }
                    _set_stage(
                        record,
                        "semantic",
                        "not_executed_objective_browser_gate",
                    )
                else:
                    try:
                        semantic = self.semantic_runner(
                            audit_root=run_dir / "browser",
                            result_package_root=package_root,
                            model_root=self.model_root,
                            result_root=run_dir / "semantic",
                            profile_name=self.semantic_profile_name,
                            confirm_model_action=True,
                        )
                        record["semantic"] = {
                            **semantic,
                            "summary": (
                                f"/api/canonical-runs/{run_id}/evidence/semantic-summary.json"
                            ),
                            "result": (
                                f"/api/canonical-runs/{run_id}/evidence/semantic-result.json"
                                if (run_dir / "semantic" / "semantic_alignment_result.json").is_file()
                                else None
                            ),
                        }
                        _set_stage(record, "semantic", str(semantic["status"]))
                    except Exception as exc:
                        record["semantic_failure"] = {
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                        _set_stage(record, "semantic", "failed_closed", detail=str(exc))
                self._save(run_id, record)

            record["status"] = str(summary["status"])
            record["completed_at"] = _now()
            self._save(run_id, record)
        except Exception as exc:
            try:
                _set_stage(record, active_stage, "failed_closed", detail=str(exc))
            except InspectorCanonicalRunError:
                pass
            record["status"] = "failed_closed_before_package"
            record["failure"] = {
                "stage_id": active_stage,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            record["completed_at"] = _now()
            self._save(run_id, record)
        finally:
            self._model_slot.release()
            with self._record_lock:
                self._threads.pop(run_id, None)

    def wait(self, run_id: str, timeout: float | None = None) -> dict[str, Any]:
        with self._record_lock:
            thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout=timeout)
        return self.get(run_id)

    def artifact_path(self, run_id: str, relative: str) -> Path:
        run_dir = self._run_dir(run_id).resolve(strict=True)
        record = self.get(run_id)
        if relative == "result-package.zip":
            selected = run_dir / relative
        elif relative.startswith("evidence/"):
            evidence_paths = {
                "evidence/browser-screenshot.png": run_dir
                / "browser"
                / "browser_screenshot.png",
                "evidence/browser-audit.json": run_dir
                / "browser"
                / "case_browser_audit.json",
                "evidence/browser-execution.json": run_dir
                / "browser"
                / "browser_execution_report.json",
                "evidence/semantic-summary.json": run_dir
                / "semantic"
                / "run_summary.json",
                "evidence/semantic-result.json": run_dir
                / "semantic"
                / "semantic_alignment_result.json",
            }
            for node_id in NODE_ORDER:
                evidence_paths[f"evidence/node-{node_id}-attempt.json"] = (
                    run_dir / "canonical" / "attempts" / node_id / "attempt_result.json"
                )
                evidence_paths[f"evidence/node-{node_id}-output.json"] = (
                    run_dir
                    / "canonical"
                    / "attempts"
                    / node_id
                    / "validated_node_output.json"
                )
                evidence_paths[f"evidence/node-{node_id}-raw.bin"] = (
                    run_dir
                    / "canonical"
                    / "attempts"
                    / node_id
                    / "raw_response.bin"
                )
            selected = evidence_paths.get(relative)
            if selected is None:
                raise InspectorCanonicalRunError(
                    "canonical evidence artifact is unsupported"
                )
        elif relative.startswith("package/"):
            package_relative = record.get("result", {}).get("package_relative_path")
            if not isinstance(package_relative, str):
                raise InspectorCanonicalRunError("canonical ResultPackage is unavailable")
            suffix = PurePosixPath(relative).relative_to("package")
            if not suffix.parts or any(part in {"", ".", ".."} for part in suffix.parts):
                raise InspectorCanonicalRunError("canonical artifact path is unsafe")
            package_root = run_dir.joinpath(*PurePosixPath(package_relative).parts)
            selected = package_root.joinpath(*suffix.parts)
        else:
            raise InspectorCanonicalRunError("canonical artifact kind is unsupported")
        resolved = selected.resolve(strict=True)
        if run_dir not in resolved.parents or resolved.is_symlink() or not resolved.is_file():
            raise InspectorCanonicalRunError("canonical artifact path is invalid")
        return resolved


__all__ = [
    "CanonicalInspectorRunStore",
    "InspectorCanonicalRunError",
    "RUN_SCHEMA_VERSION",
    "STORE_SCHEMA_VERSION",
]
