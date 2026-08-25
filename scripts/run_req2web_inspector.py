"""Validate or serve the single local Req2Web Inspector."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import mimetypes
from pathlib import Path
import sys
import threading
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.phase6_replay import (  # noqa: E402
    _APP_JS,
    _INDEX_HTML,
    _STYLES_CSS,
    Phase6ReplayError,
    validate_phase6_reviewer_bundle,
)
from req2web_inspector.guispector_sidecar import (  # noqa: E402
    guispector_runtime_preflight,
)
from req2web_inspector.guispector_optional import (  # noqa: E402
    optional_provider_capabilities,
    test_optional_provider_connection,
)
from req2web_inspector.live_draft import (  # noqa: E402
    InspectorLiveDraftError,
    LocalDraftRunStore,
    analyze_requirement,
)
from req2web_inspector.local_data import (  # noqa: E402
    LOCAL_DATA_ROOT_ENV,
    inspector_replay_bundle_root,
)
from req2web_inspector.semantic_assist import (  # noqa: E402
    HIGH_GPU_PROFILE,
    LOCAL_INTEGRITY_PROFILE,
    LOCAL_LOW_GPU_PROFILE,
    SemanticRequirementAssistError,
    SemanticRequirementAssistStore,
    provider_capabilities,
)
from req2web_evaluation.phase5_semantic_qwen_runtime import (  # noqa: E402
    HIGH_GPU_PROFILE as SEMANTIC_HIGH_GPU_PROFILE,
    LOW_GPU_PROFILE as SEMANTIC_LOW_GPU_PROFILE,
)


CANONICAL_LOW_GPU_PROFILE = "local_low_gpu_nf4"
CANONICAL_INTEGRITY_PROFILE = "local_integrity_nf4"
CANONICAL_HIGH_GPU_PROFILE = "high_gpu_bf16"

_CURRENT_UI_ASSETS = {
    "/": ("text/html; charset=utf-8", _INDEX_HTML),
    "/index.html": ("text/html; charset=utf-8", _INDEX_HTML),
    "/styles.css": ("text/css; charset=utf-8", _STYLES_CSS),
    "/app.js": ("text/javascript; charset=utf-8", _APP_JS),
}


def _startup_preflight(args: argparse.Namespace) -> dict[str, object]:
    """Check the selected local runtime without loading a model or calling a service."""

    model_actions = bool(
        args.enable_local_semantic_assist or args.enable_local_canonical_run
    )
    checks: list[dict[str, object]] = []

    def add(name: str, passed: bool, detail: str, *, blocking: bool = True) -> None:
        checks.append(
            {
                "name": name,
                "passed": passed,
                "blocking": blocking,
                "detail": detail,
            }
        )

    repository_python = ROOT / ".venv" / "Scripts" / "python.exe"
    running_python = Path(sys.executable).resolve()
    add(
        "repository_virtual_environment",
        not repository_python.is_file() or running_python == repository_python.resolve(),
        (
            f"running with {running_python}"
            if running_python == repository_python.resolve(strict=False)
            else f"recommended interpreter is {repository_python}"
        ),
        blocking=False,
    )
    retrieval_required = not args.read_only
    add(
        "retrieval_index",
        (not retrieval_required)
        or (args.index_dir.is_dir() and not args.index_dir.is_symlink()),
        (
            "not required in read-only replay mode"
            if not retrieval_required
            else str(args.index_dir)
        ),
        blocking=retrieval_required,
    )

    if model_actions:
        model_inputs: list[tuple[str, Path | None, Path | None]] = []
        if args.enable_local_semantic_assist:
            model_inputs.append(
                (
                    "semantic",
                    args.semantic_model_root or args.local_model_root,
                    args.semantic_integrity_evidence
                    or args.local_integrity_evidence,
                )
            )
        if args.enable_local_canonical_run:
            model_inputs.append(
                (
                    "canonical",
                    args.local_model_root or args.semantic_model_root,
                    args.local_integrity_evidence
                    or args.semantic_integrity_evidence,
                )
            )
        for route, model_root, integrity_evidence in model_inputs:
            add(
                f"{route}_model_root",
                model_root is not None
                and model_root.is_dir()
                and not model_root.is_symlink(),
                "missing" if model_root is None else str(model_root),
            )
            add(
                f"{route}_model_integrity_evidence",
                integrity_evidence is not None
                and integrity_evidence.is_file()
                and not integrity_evidence.is_symlink(),
                "missing" if integrity_evidence is None else str(integrity_evidence),
            )
        required_packages = ["torch", "transformers"]
        if args.enable_local_canonical_run:
            required_packages.extend(["langgraph", "playwright"])
        selected_profiles = {
            args.semantic_profile if args.enable_local_semantic_assist else None,
            args.canonical_profile if args.enable_local_canonical_run else None,
        }
        if selected_profiles & {
            LOCAL_LOW_GPU_PROFILE,
            LOCAL_INTEGRITY_PROFILE,
            CANONICAL_LOW_GPU_PROFILE,
            CANONICAL_INTEGRITY_PROFILE,
        }:
            required_packages.append("bitsandbytes")
        for package in sorted(set(required_packages)):
            add(
                f"python_package:{package}",
                importlib.util.find_spec(package) is not None,
                "available" if importlib.util.find_spec(package) is not None else "missing",
            )
        try:
            import torch  # type: ignore

            cuda_available = bool(torch.cuda.is_available())
            cuda_detail = (
                torch.cuda.get_device_name(0)
                if cuda_available and torch.cuda.device_count() > 0
                else "CUDA GPU0 is unavailable"
            )
        except Exception as exc:  # pragma: no cover - depends on local CUDA install
            cuda_available = False
            cuda_detail = f"{type(exc).__name__}: {exc}"
        add("cuda_gpu0", cuda_available, cuda_detail)

    ready = not any(
        item["blocking"] and not item["passed"] for item in checks
    )
    return {
        "schema_version": "req2web.inspector.startup_preflight.v1",
        "status": "ready" if ready else "failed_closed",
        "ready": ready,
        "checks": checks,
        "model_loaded": False,
        "model_call_count": 0,
        "external_service_called": False,
    }


class _InspectorHandler(SimpleHTTPRequestHandler):
    run_store: LocalDraftRunStore | None = None
    semantic_assist_store: SemanticRequirementAssistStore | None = None
    canonical_run_store: object | None = None
    model_dispatch_lock = threading.Lock()
    live_drafts_enabled = False

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        print(f"[REQ2WEB-INSPECTOR] {format % args}")

    def _json_response(self, status: int, value: object) -> None:
        content = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _asset_response(self, content_type: str, content: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _error(self, status: int, exc: Exception) -> None:
        self._json_response(
            status,
            {
                "status": "failed_closed",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )

    def _capabilities(self) -> dict[str, object]:
        return {
            "schema_version": "req2web.inspector.capabilities.v1",
            "service_mode": "local_inspector",
            "precomputed_replay": "available",
            "deterministic_guided_draft": (
                "available" if self.live_drafts_enabled else "disabled_read_only"
            ),
            "run_history": (
                "available" if self.live_drafts_enabled else "disabled_read_only"
            ),
            "result_package_import": (
                "available" if self.live_drafts_enabled else "disabled_read_only"
            ),
            "semantic_requirement_assist": (
                "available_local_qwen"
                if self.semantic_assist_store is not None
                else "not_connected_no_model_action"
            ),
            "semantic_requirement_assist_detail": (
                self.semantic_assist_store.capability()
                if self.semantic_assist_store is not None
                else {
                    "status": "not_connected_no_model_action",
                    "canonical_b_writeback": False,
                    "f1_f4_input": False,
                }
            ),
            "semantic_requirement_assist_providers": provider_capabilities(),
            "canonical_model_flow": (
                self.canonical_run_store.capability()
                if self.canonical_run_store is not None
                else {
                    "status": "not_connected_no_model_action",
                    "b_aux_consumed_by_f1_f4": False,
                }
            ),
            "model_f1_f4_generation": (
                "available_explicit_local_qwen"
                if self.canonical_run_store is not None
                else "not_connected_no_model_action"
            ),
            "real_browser_acceptance_for_new_drafts": (
                "available_for_canonical_runs"
                if self.canonical_run_store is not None
                else "not_connected"
            ),
            "optional_guispector_verification": {
                "packet_status": "prepared_not_executed",
                "canonical_flow_modified": False,
                "option": optional_provider_capabilities(),
                **guispector_runtime_preflight(),
            },
            "external_api_or_paid_service": (
                "not_used_until_explicit_optional_connection_test"
            ),
        }

    def _send_file(self, path: Path, *, download_name: str | None = None) -> None:
        content = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        if download_name:
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{download_name}"',
            )
        self.end_headers()
        self.wfile.write(content)

    def _api_get(self, path: str) -> bool:
        if path == "/api/capabilities":
            self._json_response(200, self._capabilities())
            return True
        if path == "/api/runs":
            if not self.live_drafts_enabled or self.run_store is None:
                self._json_response(200, {"runs": []})
            else:
                self._json_response(200, {"runs": self.run_store.list()})
            return True
        if path == "/api/canonical-runs":
            self._json_response(
                200,
                {
                    "runs": (
                        []
                        if self.canonical_run_store is None
                        else self.canonical_run_store.list()
                    )
                },
            )
            return True
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["api", "canonical-runs"]:
            if self.canonical_run_store is None:
                raise InspectorLiveDraftError(
                    "local canonical model runs are disabled"
                )
            run_id = parts[2]
            if len(parts) == 3:
                self._json_response(200, self.canonical_run_store.get(run_id))
                return True
            if len(parts) == 4 and parts[3] == "download":
                self._send_file(
                    self.canonical_run_store.artifact_path(
                        run_id,
                        "result-package.zip",
                    ),
                    download_name=f"{run_id}-result-package.zip",
                )
                return True
            if len(parts) == 5 and parts[3] == "evidence":
                relative = "evidence/" + parts[4]
                self._send_file(
                    self.canonical_run_store.artifact_path(run_id, relative)
                )
                return True
            if len(parts) >= 5 and parts[3] == "package":
                relative = "package/" + "/".join(parts[4:])
                self._send_file(
                    self.canonical_run_store.artifact_path(run_id, relative)
                )
                return True
        if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
            if not self.live_drafts_enabled or self.run_store is None:
                raise InspectorLiveDraftError("local draft runs are disabled")
            run_id = parts[2]
            if len(parts) == 3:
                self._json_response(200, self.run_store.get(run_id))
                return True
            if len(parts) == 4 and parts[3] == "download":
                self._send_file(
                    self.run_store.artifact_path(run_id, "result-package.zip"),
                    download_name=f"{run_id}-result-package.zip",
                )
                return True
            if len(parts) >= 5 and parts[3] == "package":
                relative = "package/" + "/".join(parts[4:])
                self._send_file(self.run_store.artifact_path(run_id, relative))
                return True
        return False

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        current_asset = _CURRENT_UI_ASSETS.get(path)
        if current_asset is not None:
            self._asset_response(*current_asset)
            return
        if path.startswith("/api/"):
            try:
                if self._api_get(path):
                    return
                self._json_response(404, {"status": "not_found"})
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._error(404, exc)
            return
        super().do_GET()

    def _read_body(self, *, maximum: int, allowed_content_types: tuple[str, ...]) -> bytes:
        content_type = self.headers.get("Content-Type", "")
        if not any(
            content_type.casefold().startswith(item) for item in allowed_content_types
        ):
            raise InspectorLiveDraftError("Content-Type is not supported for this endpoint")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise InspectorLiveDraftError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise InspectorLiveDraftError("Content-Length is invalid") from exc
        if not 0 <= length <= maximum:
            raise InspectorLiveDraftError("request body exceeds the endpoint limit")
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host and origin.rstrip("/") != f"http://{host}":
            raise InspectorLiveDraftError("cross-origin local API request was rejected")
        return self.rfile.read(length)

    def _read_json_body(self) -> object:
        content = self._read_body(
            maximum=131_072,
            allowed_content_types=("application/json",),
        )
        return json.loads(content.decode("utf-8"))

    def do_POST(self) -> None:
        path = unquote(urlsplit(self.path).path)
        try:
            if path == "/api/imports":
                if not self.live_drafts_enabled or self.run_store is None:
                    raise InspectorLiveDraftError("local package imports are disabled")
                content = self._read_body(
                    maximum=32 * 1024 * 1024,
                    allowed_content_types=("application/zip", "application/octet-stream"),
                )
                encoded_filename = self.headers.get("X-Req2Web-Filename", "")
                record = self.run_store.import_package_zip(
                    content,
                    unquote(encoded_filename),
                )
                self._json_response(201, record)
                return
            value = self._read_json_body()
            parts = path.strip("/").split("/")
            if (
                len(parts) == 4
                and parts[:2] == ["api", "canonical-runs"]
                and parts[3] == "cancel"
            ):
                if self.canonical_run_store is None:
                    raise InspectorLiveDraftError(
                        "local canonical model runs are disabled"
                    )
                self._json_response(
                    202,
                    self.canonical_run_store.cancel(parts[2], value),
                )
                return
            if path == "/api/guispector/test-connection":
                self._json_response(200, test_optional_provider_connection(value))
                return
            if path == "/api/intake/analyze":
                self._json_response(200, analyze_requirement(value))
                return
            if path == "/api/intake/semantic-assist":
                if self.semantic_assist_store is None:
                    raise InspectorLiveDraftError(
                        "local semantic requirement assistance is not enabled"
                    )
                with self.model_dispatch_lock:
                    if (
                        self.canonical_run_store is not None
                        and self.canonical_run_store.is_busy()
                    ):
                        raise InspectorLiveDraftError(
                            "a canonical local-model run is already active"
                        )
                    semantic_record = self.semantic_assist_store.run(value)
                self._json_response(200, semantic_record)
                return
            if path == "/api/runs":
                if not self.live_drafts_enabled or self.run_store is None:
                    raise InspectorLiveDraftError("local draft runs are disabled")
                record = self.run_store.create(value)
                self._json_response(
                    201 if record["status"] == "completed_deterministic_draft" else 422,
                    record,
                )
                return
            if path == "/api/canonical-runs":
                if self.canonical_run_store is None:
                    raise InspectorLiveDraftError(
                        "local canonical model runs are disabled"
                    )
                with self.model_dispatch_lock:
                    record = self.canonical_run_store.create(value)
                self._json_response(202, record)
                return
            self._json_response(404, {"status": "not_found"})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._error(422, exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or serve the Req2Web Inspector. The local server can "
            "create model-free deterministic drafts and, when explicitly enabled, "
            "run the canonical local-Qwen flow with browser and semantic evidence."
        )
    )
    parser.add_argument(
        "--bundle-root",
        type=Path,
        default=inspector_replay_bundle_root(ROOT),
        help=(
            "Precomputed Inspector replay bundle. The default is the stable "
            f"replay path under {LOCAL_DATA_ROOT_ENV}, or a Req2Web_LocalData "
            "directory beside the repository when that variable is unset."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--run-root",
        type=Path,
        default=ROOT / "outputs" / "req2web_inspector_runs",
        help="Module-owned directory for immutable local deterministic draft runs.",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=ROOT / "data" / "processed" / "rag",
        help="Existing frozen retrieval index used by local deterministic drafts.",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Disable local draft creation and serve only the precomputed replay.",
    )
    parser.add_argument(
        "--enable-local-semantic-assist",
        action="store_true",
        help=(
            "Expose the one-call advisory-only local Qwen requirement assistant. "
            "The model remains lazy and is loaded only when a user requests it."
        ),
    )
    parser.add_argument(
        "--semantic-model-root",
        type=Path,
        help="Existing exact Qwen3.5-9B model root for semantic assistance.",
    )
    parser.add_argument(
        "--semantic-integrity-evidence",
        type=Path,
        help="Existing exact local model integrity evidence JSON.",
    )
    parser.add_argument(
        "--semantic-profile",
        choices=(LOCAL_LOW_GPU_PROFILE, LOCAL_INTEGRITY_PROFILE, HIGH_GPU_PROFILE),
        default=LOCAL_LOW_GPU_PROFILE,
        help=(
            "Select low-GPU double-quantized NF4, local integrity NF4, or "
            "high-GPU BF16 execution."
        ),
    )
    parser.add_argument(
        "--semantic-assist-root",
        type=Path,
        default=ROOT / "outputs" / "req2web_semantic_assist_runs",
        help="Module-owned immutable semantic-assist evidence directory.",
    )
    parser.add_argument(
        "--enable-local-canonical-run",
        action="store_true",
        help=(
            "Expose the asynchronous canonical B -> F1-F4 -> delivery -> browser "
            "-> semantic route. Each request still requires explicit model confirmation."
        ),
    )
    parser.add_argument(
        "--local-model-root",
        type=Path,
        help="Existing exact local Qwen model root for canonical and semantic runs.",
    )
    parser.add_argument(
        "--local-integrity-evidence",
        type=Path,
        help="Existing exact local model integrity evidence for canonical runs.",
    )
    parser.add_argument(
        "--canonical-profile",
        choices=(
            CANONICAL_LOW_GPU_PROFILE,
            CANONICAL_INTEGRITY_PROFILE,
            CANONICAL_HIGH_GPU_PROFILE,
        ),
        default=CANONICAL_LOW_GPU_PROFILE,
        help=(
            "Select low-GPU debug, local integrity, or high-GPU quality "
            "generation."
        ),
    )
    parser.add_argument(
        "--canonical-semantic-profile",
        choices=(SEMANTIC_LOW_GPU_PROFILE, SEMANTIC_HIGH_GPU_PROFILE),
        help=(
            "Select the separate semantic acceptance precision. By default, "
            "high-GPU generation uses high-GPU semantic acceptance and local "
            "generation uses the local smoke profile."
        ),
    )
    parser.add_argument(
        "--canonical-run-root",
        type=Path,
        default=ROOT / "outputs" / "req2web_inspector_canonical_runs",
        help="Module-owned canonical model run and evidence directory.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help=(
            "Validate the selected local runtime and exit without loading a model, "
            "calling a service, or starting the server."
        ),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the precomputed bundle and exit without starting a server.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = validate_phase6_reviewer_bundle(args.bundle_root)
    except (OSError, ValueError, Phase6ReplayError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    summary = {
        "status": "validated_precomputed_replay",
        "counts": manifest["counts"],
        "bundle_root": str(args.bundle_root.resolve()),
    }
    if args.validate_only:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            "[REQ2WEB-INSPECTOR] failed closed: the default reviewer server is local-only",
            file=sys.stderr,
        )
        return 2
    preflight = _startup_preflight(args)
    if args.preflight_only:
        stream = sys.stdout if preflight["ready"] else sys.stderr
        print(
            json.dumps(preflight, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            file=stream,
        )
        return 0 if preflight["ready"] else 2
    if not preflight["ready"]:
        print(
            json.dumps(preflight, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            file=sys.stderr,
        )
        return 2
    run_store: LocalDraftRunStore | None = None
    semantic_assist_store: SemanticRequirementAssistStore | None = None
    canonical_run_store: object | None = None
    if not args.read_only:
        try:
            run_store = LocalDraftRunStore(args.run_root, args.index_dir)
        except (OSError, ValueError, InspectorLiveDraftError) as exc:
            print(f"[REQ2WEB-INSPECTOR] failed closed: {exc}", file=sys.stderr)
            return 2
    if args.enable_local_semantic_assist:
        if args.read_only:
            print(
                "[REQ2WEB-INSPECTOR] failed closed: semantic assistance is unavailable in read-only mode",
                file=sys.stderr,
            )
            return 2
        semantic_model_root = args.semantic_model_root or args.local_model_root
        semantic_integrity_evidence = (
            args.semantic_integrity_evidence or args.local_integrity_evidence
        )
        if semantic_model_root is None or semantic_integrity_evidence is None:
            print(
                "[REQ2WEB-INSPECTOR] failed closed: semantic model root and integrity evidence are required",
                file=sys.stderr,
            )
            return 2
        try:
            semantic_assist_store = SemanticRequirementAssistStore(
                root=args.semantic_assist_root,
                model_root=semantic_model_root,
                integrity_evidence=semantic_integrity_evidence,
                profile_name=args.semantic_profile,
            )
        except (OSError, ValueError, SemanticRequirementAssistError) as exc:
            print(f"[REQ2WEB-INSPECTOR] failed closed: {exc}", file=sys.stderr)
            return 2
    if args.enable_local_canonical_run:
        if args.read_only:
            print(
                "[REQ2WEB-INSPECTOR] failed closed: canonical model runs are unavailable in read-only mode",
                file=sys.stderr,
            )
            return 2
        model_root = args.local_model_root or args.semantic_model_root
        integrity_evidence = (
            args.local_integrity_evidence or args.semantic_integrity_evidence
        )
        if model_root is None or integrity_evidence is None:
            print(
                "[REQ2WEB-INSPECTOR] failed closed: local model root and integrity evidence are required",
                file=sys.stderr,
            )
            return 2
        try:
            from req2web_inspector.canonical_run import CanonicalInspectorRunStore

            canonical_run_store = CanonicalInspectorRunStore(
                root=args.canonical_run_root,
                index_dir=args.index_dir,
                model_root=model_root,
                integrity_evidence=integrity_evidence,
                profile_name=args.canonical_profile,
                semantic_profile_name=(
                    args.canonical_semantic_profile
                    or (
                        SEMANTIC_HIGH_GPU_PROFILE
                        if args.canonical_profile == CANONICAL_HIGH_GPU_PROFILE
                        else SEMANTIC_LOW_GPU_PROFILE
                    )
                ),
                requirement_assist_runner=(
                    None
                    if semantic_assist_store is None
                    else semantic_assist_store.run
                ),
            )
        except (ImportError, OSError, ValueError) as exc:
            print(f"[REQ2WEB-INSPECTOR] failed closed: {exc}", file=sys.stderr)
            return 2
    _InspectorHandler.run_store = run_store
    _InspectorHandler.semantic_assist_store = semantic_assist_store
    _InspectorHandler.canonical_run_store = canonical_run_store
    _InspectorHandler.live_drafts_enabled = run_store is not None
    handler = partial(_InspectorHandler, directory=str(args.bundle_root.resolve()))
    try:
        server = ThreadingHTTPServer((args.host, args.port), handler)
    except OSError as exc:
        print(f"[REQ2WEB-INSPECTOR] failed closed: {exc}", file=sys.stderr)
        return 2
    print(
        f"[REQ2WEB-INSPECTOR] validated {manifest['counts']['row_count']} rows; "
        f"local drafts {'enabled' if run_store is not None else 'disabled'}; "
        f"semantic assist {'enabled' if semantic_assist_store is not None else 'disabled'}; "
        f"canonical model runs {'enabled' if canonical_run_store is not None else 'disabled'}; "
        f"open http://{args.host}:{args.port}/",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
