"""Prepare and assemble transparent LLM-assisted retrieval qrels.

The workflow never calls a model. It validates an externally completed,
method-blind prelabel packet, creates deterministic worklists for two human
reviewers, retains a blind overlap audit, and assembles exploratory qrels only
after all required human fields are complete.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, Mapping

from req2web_generation.demo_regression import RegressionCaseSet
from req2web_rag.retrieval_comparison import (
    RETRIEVAL_ASSISTED_QRELS_SCHEMA_VERSION,
    RETRIEVAL_METRIC_PROTOCOL_REVISION,
    _linear_weighted_kappa,
    _load_qrels,
    validate_retrieval_comparison,
)
from req2web_rag.retrieval_experiment import (
    HUMAN_REVIEWER_SLOTS,
    HUMAN_REVIEW_ASSIGNMENT_REVISION,
    validate_retrieval_experiment,
)


HUMAN_REVIEW_PACKET_SCHEMA_VERSION = "req2web.retrieval.human_review.v1"
JOINT_RESOLUTION_PACKET_SCHEMA_VERSION = (
    "req2web.retrieval.joint_resolution.v1"
)
HUMAN_REVIEW_BUNDLE_MANIFEST_SCHEMA_VERSION = (
    "req2web.retrieval.human_review_bundle.manifest.v1"
)
ASSISTED_REVIEW_PROTOCOL_REVISION = "llm_prelabel_split_review_overlap84_v1"
ASSISTED_QRELS_CLAIM_BOUNDARY = (
    "LLM-assisted, split-human-reviewed exploratory pooled relevance judgments "
    "on the twelve frozen Demo v2 cases. These labels are not independent human "
    "gold, H1, full-corpus recall, formal quality, broad generalization, or "
    "production evidence."
)


class RetrievalQrelsError(ValueError):
    """Raised when assisted review evidence is incomplete or drifted."""


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RetrievalQrelsError("review value is not canonical JSON") from exc


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RetrievalQrelsError(f"{name} must be an object")
    return dict(value)


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise RetrievalQrelsError(f"{name} must be a list")
    return value


def _read_json(path: Path, name: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RetrievalQrelsError(f"{name} is unavailable")
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalQrelsError(f"{name} is not UTF-8 JSON") from exc
    return _mapping(value, name)


def _write_json(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise RetrievalQrelsError(f"review output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json_bytes(value))


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RetrievalQrelsError(f"review file is unavailable: {path}")
    return sha256(path.read_bytes()).hexdigest()


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or ".." in path.parts
        or str(path) != value
    ):
        raise RetrievalQrelsError("review path is not safely relative")
    return value


def _completed_llm_prelabels(
    *, packet_path: Path, template_path: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    packet = _read_json(packet_path, "completed LLM prelabel packet")
    template = _read_json(template_path, "LLM prelabel template")
    if packet.get("status") != "completed":
        raise RetrievalQrelsError("LLM prelabel packet is not complete")
    model_run = _mapping(packet.get("model_run"), "LLM model run")
    if set(model_run) != {
        "provider",
        "model_id",
        "raw_response_sha256",
        "paid_api_used",
    }:
        raise RetrievalQrelsError("LLM model run keys drifted")
    if (
        any(
            not isinstance(model_run[key], str) or not model_run[key].strip()
            for key in ("provider", "model_id", "raw_response_sha256")
        )
        or not isinstance(model_run["paid_api_used"], bool)
    ):
        raise RetrievalQrelsError("LLM model identity is incomplete")
    normalized = json.loads(json.dumps(packet))
    normalized["status"] = "template_unscored"
    normalized["model_run"] = {
        "provider": None,
        "model_id": None,
        "raw_response_sha256": None,
        "paid_api_used": None,
    }
    judgments = []
    units = _list(packet.get("units"), "LLM prelabel units")
    normalized_units = _list(normalized.get("units"), "normalized prelabel units")
    for unit, normalized_unit in zip(units, normalized_units, strict=True):
        row = _mapping(unit, "LLM prelabel unit")
        normalized_row = _mapping(normalized_unit, "normalized prelabel unit")
        candidates = _list(row.get("candidates"), "LLM prelabel candidates")
        normalized_candidates = _list(
            normalized_row.get("candidates"), "normalized prelabel candidates"
        )
        for candidate, normalized_candidate in zip(
            candidates, normalized_candidates, strict=True
        ):
            item = _mapping(candidate, "LLM prelabel candidate")
            relevance = item.get("suggested_relevance")
            rationale = item.get("rationale")
            confidence = item.get("confidence")
            if (
                isinstance(relevance, bool)
                or not isinstance(relevance, int)
                or relevance not in {0, 1, 2, 3}
            ):
                raise RetrievalQrelsError(
                    "LLM suggested relevance must be an integer from 0 through 3"
                )
            if not isinstance(rationale, str) or not rationale.strip():
                raise RetrievalQrelsError("LLM rationale must be non-empty")
            if confidence not in {"low", "medium", "high"}:
                raise RetrievalQrelsError("LLM confidence is invalid")
            normalized_candidate["suggested_relevance"] = None
            normalized_candidate["rationale"] = None
            normalized_candidate["confidence"] = None
            judgments.append(
                {
                    "case_id": row["case_id"],
                    "role": row["role"],
                    "query_sha256": row["query_sha256"],
                    "doc_id": item["doc_id"],
                    "relevance": relevance,
                    "rationale": rationale.strip(),
                    "confidence": confidence,
                }
            )
    if normalized != template:
        raise RetrievalQrelsError(
            "LLM prelabel packet changed fields outside declared result fields"
        )
    if len(judgments) != 420:
        raise RetrievalQrelsError("LLM prelabel packet does not cover 420 candidates")
    return packet, judgments


def _candidate_views(template: Mapping[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    result = {}
    for raw_unit in _list(template.get("units"), "prelabel template units"):
        unit = _mapping(raw_unit, "prelabel template unit")
        for raw_candidate in _list(unit.get("candidates"), "prelabel candidates"):
            candidate = _mapping(raw_candidate, "prelabel candidate")
            key = (str(unit["case_id"]), str(unit["role"]), str(candidate["doc_id"]))
            result[key] = {
                "case_id": unit["case_id"],
                "role": unit["role"],
                "query_sha256": unit["query_sha256"],
                "judgment_context": unit["judgment_context"],
                "candidate": {
                    field: candidate[field]
                    for field in ("doc_id", "dataset", "subset", "title", "summary")
                },
            }
    return result


def _prelabel_map(
    judgments: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {
        (str(row["case_id"]), str(row["role"]), str(row["doc_id"])): {
            "relevance": row["relevance"],
            "rationale": row["rationale"],
            "confidence": row["confidence"],
        }
        for row in judgments
    }


def _review_packets(
    *,
    experiment_root: Path,
    completed_llm_packet: Mapping[str, Any],
    llm_judgments: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    template = _read_json(
        experiment_root / "annotation" / "llm_prelabel_template.json",
        "LLM prelabel template",
    )
    assignment = _read_json(
        experiment_root / "annotation" / "human_review_assignment.json",
        "human review assignment",
    )
    views = _candidate_views(template)
    suggestions = _prelabel_map(llm_judgments)
    packet_sha = sha256(_canonical_json_bytes(completed_llm_packet)).hexdigest()
    packets: dict[str, dict[str, Any]] = {}
    for slot in HUMAN_REVIEWER_SLOTS:
        items = []
        for raw in _list(assignment.get("assignments"), "review assignments"):
            row = _mapping(raw, "review assignment")
            if slot not in _list(row.get("reviewer_slots"), "reviewer slots"):
                continue
            key = (str(row["case_id"]), str(row["role"]), str(row["doc_id"]))
            mode = str(row["review_mode"])
            items.append(
                {
                    **views[key],
                    "review_mode": mode,
                    "llm_suggestion": (
                        suggestions[key]
                        if row["llm_suggestion_visible_during_initial_review"]
                        else None
                    ),
                    "human_relevance": None,
                }
            )
        packets[slot] = {
            "schema_version": HUMAN_REVIEW_PACKET_SCHEMA_VERSION,
            "protocol_revision": ASSISTED_REVIEW_PROTOCOL_REVISION,
            "case_set_id": completed_llm_packet["case_set_id"],
            "reviewer_slot": slot,
            "status": "template_unscored",
            "assignment_revision": HUMAN_REVIEW_ASSIGNMENT_REVISION,
            "llm_prelabel_sha256": packet_sha,
            "instructions": [
                "Complete every assigned item and change only status and human_relevance.",
                "For llm_assisted_primary items, review rather than copy the model suggestion.",
                "For blind_overlap_audit items, the model suggestion is intentionally hidden.",
                "Use only integer grades 0 through 3 and do not force a positive grade.",
            ],
            "items": items,
        }
    audit_items = []
    for raw in _list(assignment.get("assignments"), "review assignments"):
        row = _mapping(raw, "review assignment")
        if row["review_mode"] != "blind_overlap_audit":
            continue
        key = (str(row["case_id"]), str(row["role"]), str(row["doc_id"]))
        audit_items.append(
            {
                **views[key],
                "llm_suggestion": suggestions[key],
                "final_relevance": None,
            }
        )
    joint = {
        "schema_version": JOINT_RESOLUTION_PACKET_SCHEMA_VERSION,
        "protocol_revision": ASSISTED_REVIEW_PROTOCOL_REVISION,
        "case_set_id": completed_llm_packet["case_set_id"],
        "reviewer_slots": list(HUMAN_REVIEWER_SLOTS),
        "status": "template_unscored",
        "llm_prelabel_sha256": packet_sha,
        "instructions": [
            "Begin only after both human review packets are complete and frozen.",
            "Review both blind audit judgments and the disclosed model suggestion.",
            "Both reviewers jointly assign one final integer grade from 0 through 3.",
            "Do not force a positive grade when the pooled candidate is not relevant.",
        ],
        "items": audit_items,
    }
    return packets, joint


def _manifest_files(root: Path) -> list[dict[str, Any]]:
    paths = [
        "human_reviewer_1_template.json",
        "human_reviewer_2_template.json",
        "joint_resolution_template.json",
        "llm_prelabels.json",
    ]
    rows = []
    for relative in paths:
        _safe_relative(relative)
        path = root / relative
        rows.append(
            {
                "path": relative,
                "sha256": _file_sha256(path),
                "byte_length": path.stat().st_size,
            }
        )
    return rows


def prepare_human_review_packets(
    *,
    fixture_path: Path,
    index_dir: Path,
    comparison_root: Path,
    experiment_root: Path,
    llm_prelabel_packet: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Validate external prelabels and publish deterministic human worklists."""

    fixture = Path(fixture_path).resolve(strict=True)
    index = Path(index_dir).resolve(strict=True)
    comparison = Path(comparison_root).resolve(strict=True)
    experiment = Path(experiment_root).resolve(strict=True)
    target = Path(output_root).resolve()
    if target.exists() or target.is_symlink():
        raise RetrievalQrelsError("human review output root already exists")
    validate_retrieval_experiment(
        fixture_path=fixture,
        index_dir=index,
        comparison_root=comparison,
        output_root=experiment,
    )
    packet, judgments = _completed_llm_prelabels(
        packet_path=Path(llm_prelabel_packet).resolve(strict=True),
        template_path=experiment / "annotation" / "llm_prelabel_template.json",
    )
    packets, joint = _review_packets(
        experiment_root=experiment,
        completed_llm_packet=packet,
        llm_judgments=judgments,
    )
    build_root = target.with_name(f".{target.name}.building")
    if build_root.exists() or build_root.is_symlink():
        raise RetrievalQrelsError("human review build root already exists")
    build_root.mkdir(parents=True)
    try:
        _write_json(build_root / "llm_prelabels.json", packet)
        for slot, value in packets.items():
            _write_json(build_root / f"{slot}_template.json", value)
        _write_json(build_root / "joint_resolution_template.json", joint)
        _write_json(
            build_root / "review_manifest.json",
            {
                "schema_version": HUMAN_REVIEW_BUNDLE_MANIFEST_SCHEMA_VERSION,
                "status": "prepared_awaiting_two_human_reviewers",
                "files": _manifest_files(build_root),
            },
        )
        result = validate_human_review_packets(
            fixture_path=fixture,
            index_dir=index,
            comparison_root=comparison,
            experiment_root=experiment,
            output_root=build_root,
        )
        build_root.rename(target)
        return {**result, "output_root": str(target)}
    except Exception:
        shutil.rmtree(build_root, ignore_errors=True)
        raise


def validate_human_review_packets(
    *,
    fixture_path: Path,
    index_dir: Path,
    comparison_root: Path,
    experiment_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Pure-read validation of prepared human review worklists."""

    fixture = Path(fixture_path).resolve(strict=True)
    index = Path(index_dir).resolve(strict=True)
    comparison = Path(comparison_root).resolve(strict=True)
    experiment = Path(experiment_root).resolve(strict=True)
    root = Path(output_root).resolve(strict=True)
    validate_retrieval_experiment(
        fixture_path=fixture,
        index_dir=index,
        comparison_root=comparison,
        output_root=experiment,
    )
    packet, judgments = _completed_llm_prelabels(
        packet_path=root / "llm_prelabels.json",
        template_path=experiment / "annotation" / "llm_prelabel_template.json",
    )
    packets, joint = _review_packets(
        experiment_root=experiment,
        completed_llm_packet=packet,
        llm_judgments=judgments,
    )
    for slot, expected in packets.items():
        if _read_json(root / f"{slot}_template.json", slot) != expected:
            raise RetrievalQrelsError(f"{slot} template does not replay")
    if _read_json(root / "joint_resolution_template.json", "joint template") != joint:
        raise RetrievalQrelsError("joint resolution template does not replay")
    manifest = _read_json(root / "review_manifest.json", "review manifest")
    if manifest != {
        "schema_version": HUMAN_REVIEW_BUNDLE_MANIFEST_SCHEMA_VERSION,
        "status": "prepared_awaiting_two_human_reviewers",
        "files": _manifest_files(root),
    }:
        raise RetrievalQrelsError("human review manifest drifted")
    expected_paths = {
        "human_reviewer_1_template.json",
        "human_reviewer_2_template.json",
        "joint_resolution_template.json",
        "llm_prelabels.json",
        "review_manifest.json",
    }
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise RetrievalQrelsError("human review file inventory drifted")
    return {
        "status": "prepared_awaiting_two_human_reviewers",
        "reviewer_count": 2,
        "judgments_per_reviewer": 252,
        "blind_overlap_audit_count": 84,
        "full_human_coverage": True,
    }


def _completed_human_packet(
    *, packet_path: Path, template_path: Path, expected_slot: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    packet = _read_json(packet_path, f"completed {expected_slot} packet")
    template = _read_json(template_path, f"{expected_slot} template")
    if packet.get("reviewer_slot") != expected_slot or packet.get("status") != "completed":
        raise RetrievalQrelsError(f"{expected_slot} packet is not complete")
    normalized = json.loads(json.dumps(packet))
    normalized["status"] = "template_unscored"
    primary = []
    audit = []
    items = _list(packet.get("items"), f"{expected_slot} items")
    normalized_items = _list(normalized.get("items"), "normalized review items")
    for item, normalized_item in zip(items, normalized_items, strict=True):
        row = _mapping(item, f"{expected_slot} item")
        relevance = row.get("human_relevance")
        if (
            isinstance(relevance, bool)
            or not isinstance(relevance, int)
            or relevance not in {0, 1, 2, 3}
        ):
            raise RetrievalQrelsError(
                f"{expected_slot} relevance must be an integer from 0 through 3"
            )
        normalized_item["human_relevance"] = None
        judgment = {
            "case_id": row["case_id"],
            "role": row["role"],
            "query_sha256": row["query_sha256"],
            "doc_id": row["candidate"]["doc_id"],
            "relevance": relevance,
        }
        (audit if row["review_mode"] == "blind_overlap_audit" else primary).append(
            judgment
        )
    if normalized != template:
        raise RetrievalQrelsError(
            f"{expected_slot} changed fields outside status and human_relevance"
        )
    if len(primary) != 168 or len(audit) != 84:
        raise RetrievalQrelsError(f"{expected_slot} review counts drifted")
    return primary, audit


def _completed_joint_packet(
    *, packet_path: Path, template_path: Path
) -> list[dict[str, Any]]:
    packet = _read_json(packet_path, "completed joint resolution")
    template = _read_json(template_path, "joint resolution template")
    if packet.get("status") != "completed":
        raise RetrievalQrelsError("joint resolution packet is not complete")
    normalized = json.loads(json.dumps(packet))
    normalized["status"] = "template_unscored"
    judgments = []
    items = _list(packet.get("items"), "joint resolution items")
    normalized_items = _list(normalized.get("items"), "normalized joint items")
    for item, normalized_item in zip(items, normalized_items, strict=True):
        row = _mapping(item, "joint resolution item")
        relevance = row.get("final_relevance")
        if (
            isinstance(relevance, bool)
            or not isinstance(relevance, int)
            or relevance not in {0, 1, 2, 3}
        ):
            raise RetrievalQrelsError(
                "joint relevance must be an integer from 0 through 3"
            )
        normalized_item["final_relevance"] = None
        judgments.append(
            {
                "case_id": row["case_id"],
                "role": row["role"],
                "query_sha256": row["query_sha256"],
                "doc_id": row["candidate"]["doc_id"],
                "relevance": relevance,
            }
        )
    if normalized != template or len(judgments) != 84:
        raise RetrievalQrelsError("joint resolution packet drifted")
    return judgments


def _judgment_map(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, int]]:
    result: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        key = (str(row["case_id"]), str(row["role"]))
        result.setdefault(key, {})[str(row["doc_id"])] = int(row["relevance"])
    return result


def assemble_retrieval_qrels(
    *,
    fixture_path: Path,
    index_dir: Path,
    comparison_root: Path,
    experiment_root: Path,
    review_root: Path,
    reviewer_1_packet: Path,
    reviewer_2_packet: Path,
    joint_resolution_packet: Path,
    reviewer_1_id: str,
    reviewer_2_id: str,
    output_path: Path,
) -> dict[str, Any]:
    """Assemble immutable assisted qrels from completed two-human review."""

    fixture = Path(fixture_path).resolve(strict=True)
    index = Path(index_dir).resolve(strict=True)
    comparison = Path(comparison_root).resolve(strict=True)
    experiment = Path(experiment_root).resolve(strict=True)
    review = Path(review_root).resolve(strict=True)
    target = Path(output_path).resolve()
    if target.exists() or target.is_symlink():
        raise RetrievalQrelsError("qrels output already exists")
    reviewer_ids = [reviewer_1_id, reviewer_2_id]
    if (
        len(set(reviewer_ids)) != 2
        or any(not isinstance(value, str) or not value.strip() for value in reviewer_ids)
    ):
        raise RetrievalQrelsError("two distinct human reviewer ids are required")
    validate_human_review_packets(
        fixture_path=fixture,
        index_dir=index,
        comparison_root=comparison,
        experiment_root=experiment,
        output_root=review,
    )
    llm_packet, model_judgments = _completed_llm_prelabels(
        packet_path=review / "llm_prelabels.json",
        template_path=experiment / "annotation" / "llm_prelabel_template.json",
    )
    completed = []
    for slot, packet_path in zip(
        HUMAN_REVIEWER_SLOTS,
        (reviewer_1_packet, reviewer_2_packet),
        strict=True,
    ):
        completed.append(
            _completed_human_packet(
                packet_path=Path(packet_path).resolve(strict=True),
                template_path=review / f"{slot}_template.json",
                expected_slot=slot,
            )
        )
    joint = _completed_joint_packet(
        packet_path=Path(joint_resolution_packet).resolve(strict=True),
        template_path=review / "joint_resolution_template.json",
    )
    audit_kappa = _linear_weighted_kappa(
        _judgment_map(completed[0][1]),
        _judgment_map(completed[1][1]),
    )
    final_judgments = [*completed[0][0], *completed[1][0], *joint]
    model_run = _mapping(llm_packet["model_run"], "LLM model run")
    qrels = {
        "schema_version": RETRIEVAL_ASSISTED_QRELS_SCHEMA_VERSION,
        "protocol_revision": RETRIEVAL_METRIC_PROTOCOL_REVISION,
        "case_set_id": llm_packet["case_set_id"],
        "model_prelabels": {
            "provider": model_run["provider"],
            "model_id": model_run["model_id"],
            "prompt_revision": llm_packet["prompt_revision"],
            "request_sha256": llm_packet["request_sha256"],
            "raw_response_sha256": model_run["raw_response_sha256"],
            "paid_api_used": model_run["paid_api_used"],
            "judgments": model_judgments,
        },
        "human_review": {
            "protocol_revision": ASSISTED_REVIEW_PROTOCOL_REVISION,
            "reviewer_ids": reviewer_ids,
            "full_human_coverage": True,
            "primary_judgments": [
                {"reviewer_id": reviewer_ids[index], "judgments": completed[index][0]}
                for index in range(2)
            ],
            "blind_overlap_audit": {
                "candidate_count": 84,
                "model_suggestion_hidden": True,
                "reviewer_judgments": [
                    {
                        "reviewer_id": reviewer_ids[index],
                        "judgments": completed[index][1],
                    }
                    for index in range(2)
                ],
                "agreement": {
                    "metric": "linear_weighted_cohen_kappa",
                    "value": audit_kappa,
                },
            },
            "joint_resolution": {
                "reviewer_ids": reviewer_ids,
                "judgments": joint,
            },
            "final_judgments": final_judgments,
        },
        "claim_boundary": ASSISTED_QRELS_CLAIM_BOUNDARY,
    }
    build_path = target.with_name(f".{target.name}.building")
    if build_path.exists() or build_path.is_symlink():
        raise RetrievalQrelsError("qrels build path already exists")
    build_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        build_path.write_bytes(_canonical_json_bytes(qrels))
        case_set = RegressionCaseSet.load(fixture)
        report = validate_retrieval_comparison(
            fixture_path=fixture,
            index_dir=index,
            output_root=comparison,
        )
        _, metadata = _load_qrels(
            path=build_path,
            case_set=case_set,
            units=report["units"],
        )
        build_path.rename(target)
    except Exception:
        if build_path.exists() and not build_path.is_symlink():
            build_path.unlink()
        raise
    return {
        "status": "complete_assisted_qrels_assembled",
        "model_prelabel_count": len(model_judgments),
        "human_reviewed_candidate_count": len(final_judgments),
        "judgments_per_human_reviewer": 252,
        "blind_overlap_audit_count": 84,
        "audit_agreement_metric": "linear_weighted_cohen_kappa",
        "audit_agreement_value": audit_kappa,
        "insufficient_pool_unit_count": metadata["insufficient_pool_unit_count"],
        "independent_human_gold_claim_allowed": False,
        "output_path": str(target),
    }


__all__ = [
    "ASSISTED_QRELS_CLAIM_BOUNDARY",
    "ASSISTED_REVIEW_PROTOCOL_REVISION",
    "HUMAN_REVIEW_BUNDLE_MANIFEST_SCHEMA_VERSION",
    "HUMAN_REVIEW_PACKET_SCHEMA_VERSION",
    "JOINT_RESOLUTION_PACKET_SCHEMA_VERSION",
    "RetrievalQrelsError",
    "assemble_retrieval_qrels",
    "prepare_human_review_packets",
    "validate_human_review_packets",
]
