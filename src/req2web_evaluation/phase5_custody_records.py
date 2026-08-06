"""Synthetic duplicate calibration and hash-only Phase 5 custody records."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from itertools import combinations
import json
import math
from typing import Mapping, Sequence
import unicodedata


FIXTURE_SCHEMA_VERSION = "req2web.phase5.duplicate_calibration.synthetic_fixture.v1"
CALIBRATION_SCHEMA_VERSION = "req2web.phase5.duplicate_calibration.synthetic_receipt.v1"
ADJUDICATION_SCHEMA_VERSION = "req2web.phase5.duplicate_adjudication.opaque.v1"
ANNOTATION_COMMITMENT_SCHEMA_VERSION = "req2web.phase5.annotation_commitment.synthetic.v1"
ANNOTATION_ADJUDICATION_SCHEMA_VERSION = "req2web.phase5.annotation_adjudication.synthetic.v1"

_FIXTURE_ID = "phase5-duplicate-calibration-synthetic-v1"
_LABELS = ("exact_duplicate", "near_duplicate", "same_domain_nonduplicate", "unrelated")
_CALIBRATION_STATUS = "synthetic_calibration_example_not_formal"
_CALIBRATION_METHOD = "char_3_4_5gram_tfidf_0_8_plus_signature_jaccard_0_2_v1"
_AGREEMENT_STATUSES = ("agree", "disagree_adjudication_required")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _id(prefix: str, value: object) -> str:
    return f"{prefix}-{_hash(value)}"


def _prefixed_digest_id(value: object, prefix: str, name: str) -> str:
    value = _text(value, name)
    marker = f"{prefix}-"
    if not value.startswith(marker):
        raise ValueError(f"{name} has invalid authority prefix")
    _digest(value[len(marker) :], f"{name} digest")
    return value


def _exact(value: object, keys: Sequence[str], name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError(f"{name} has invalid keys")
    return dict(value)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def _digest(value: object, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _integer(
    value: object,
    name: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be an integer <= {maximum}")
    return value


def _sequence(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return list(value)


def _false(value: object, name: str) -> None:
    if value is not False:
        raise ValueError(f"{name} must remain false")


def _code(value: object, name: str) -> str:
    value = _text(value, name)
    if len(value) > 128 or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for character in value
    ):
        raise ValueError(f"{name} must be a lowercase opaque code")
    return value


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _ngrams(value: str) -> set[str]:
    wrapped = f"  {value}  "
    return {
        wrapped[index : index + size]
        for size in (3, 4, 5)
        for index in range(len(wrapped) - size + 1)
    }


def _tfidf_vectors(texts: Mapping[str, str]) -> dict[str, dict[str, float]]:
    grams = {subject_id: _ngrams(text) for subject_id, text in texts.items()}
    document_count = len(grams)
    document_frequency: dict[str, int] = {}
    for values in grams.values():
        for gram in sorted(values):
            document_frequency[gram] = document_frequency.get(gram, 0) + 1
    vectors: dict[str, dict[str, float]] = {}
    for subject_id in sorted(grams):
        vector = {
            gram: math.log((1 + document_count) / (1 + document_frequency[gram])) + 1.0
            for gram in sorted(grams[subject_id])
        }
        norm = math.sqrt(math.fsum(weight * weight for weight in vector.values()))
        vectors[subject_id] = {gram: weight / norm for gram, weight in vector.items()}
    return vectors


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return math.fsum(weight * right.get(gram, 0.0) for gram, weight in left.items())


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 1.0


def _validate_fixture(value: object) -> dict[str, object]:
    fixture = _exact(
        value,
        ("schema_version", "fixture_id", "fixture_kind", "subjects", "pair_labels"),
        "fixture",
    )
    if fixture["schema_version"] != FIXTURE_SCHEMA_VERSION or fixture["fixture_id"] != _FIXTURE_ID:
        raise ValueError("fixture identity drifted")
    if fixture["fixture_kind"] != "synthetic_development_only_no_real_h1":
        raise ValueError("fixture scope must remain synthetic development only")
    subjects: list[dict[str, object]] = []
    ids: list[str] = []
    for raw in _sequence(fixture["subjects"], "fixture.subjects"):
        subject = _exact(raw, ("subject_id", "requirement_text", "structured_signature"), "subject")
        subject_id = _text(subject["subject_id"], "subject_id")
        if not subject_id.startswith("synthetic-dup-"):
            raise ValueError("subject id must remain synthetic")
        signature = tuple(
            _text(item, "structured signature item")
            for item in _sequence(subject["structured_signature"], "structured_signature")
        )
        if signature != tuple(sorted(set(signature))) or not signature:
            raise ValueError("structured signature must be sorted and unique")
        ids.append(subject_id)
        subjects.append(
            {
                "subject_id": subject_id,
                "requirement_text": _text(subject["requirement_text"], "requirement_text"),
                "structured_signature": list(signature),
            }
        )
    if len(ids) < 2 or ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("subjects must be sorted and unique")
    expected_pairs = list(combinations(ids, 2))
    labels: list[dict[str, object]] = []
    for raw in _sequence(fixture["pair_labels"], "fixture.pair_labels"):
        label = _exact(raw, ("left_subject_id", "right_subject_id", "label"), "pair label")
        pair = (_text(label["left_subject_id"], "left"), _text(label["right_subject_id"], "right"))
        if label["label"] not in _LABELS:
            raise ValueError("unsupported pair label")
        labels.append(
            {
                "left_subject_id": pair[0],
                "right_subject_id": pair[1],
                "label": label["label"],
            }
        )
    observed_pairs = [(item["left_subject_id"], item["right_subject_id"]) for item in labels]
    if observed_pairs != expected_pairs:
        raise ValueError("pair labels must cover every canonical pair")
    return {**fixture, "subjects": subjects, "pair_labels": labels}


def _metrics(rows: Sequence[Mapping[str, object]], threshold: int) -> tuple[int, int, int, int]:
    tp = fp = fn = tn = 0
    for row in rows:
        positive = row["label"] in {"exact_duplicate", "near_duplicate"}
        predicted = int(row["combined_score_ppm"]) >= threshold
        if positive and predicted:
            tp += 1
        elif positive:
            fn += 1
        elif predicted:
            fp += 1
        else:
            tn += 1
    return tp, fp, fn, tn


def _combined_score_ppm(text_score_ppm: int, signature_score_ppm: int) -> int:
    numerator = 4 * text_score_ppm + signature_score_ppm
    quotient, remainder = divmod(numerator, 5)
    return quotient + (1 if remainder >= 3 else 0)


def _select_threshold(
    rows: Sequence[Mapping[str, object]],
) -> tuple[int, tuple[int, int, int, int]]:
    thresholds = sorted({0, 1_000_001, *(int(row["combined_score_ppm"]) for row in rows)})
    candidates: list[
        tuple[Fraction, Fraction, int, tuple[int, int, int, int]]
    ] = []
    for threshold in thresholds:
        counts = _metrics(rows, threshold)
        tp, fp, fn, _ = counts
        f1 = Fraction(2 * tp, 2 * tp + fp + fn) if tp or fp or fn else Fraction()
        precision = Fraction(tp, tp + fp) if tp + fp else Fraction()
        candidates.append((f1, precision, threshold, counts))
    _, _, threshold, counts = max(candidates, key=lambda item: item[:3])
    return threshold, counts


def build_synthetic_duplicate_calibration(fixture: object) -> "SyntheticCalibrationReceipt":
    fixture = _validate_fixture(fixture)
    texts = {
        subject["subject_id"]: _normalize(subject["requirement_text"])
        for subject in fixture["subjects"]
    }
    signatures = {
        subject["subject_id"]: set(subject["structured_signature"])
        for subject in fixture["subjects"]
    }
    vectors = _tfidf_vectors(texts)
    labels = {
        (item["left_subject_id"], item["right_subject_id"]): item["label"]
        for item in fixture["pair_labels"]
    }
    rows: list[dict[str, object]] = []
    for left, right in combinations(sorted(texts), 2):
        text_score = _cosine(vectors[left], vectors[right])
        signature_score = _jaccard(signatures[left], signatures[right])
        text_score_ppm = round(text_score * 1_000_000)
        signature_score_ppm = round(signature_score * 1_000_000)
        rows.append(
            {
                "left_subject_id": left,
                "right_subject_id": right,
                "left_text_sha256": sha256(texts[left].encode()).hexdigest(),
                "right_text_sha256": sha256(texts[right].encode()).hexdigest(),
                "text_tfidf_score_ppm": text_score_ppm,
                "signature_jaccard_score_ppm": signature_score_ppm,
                "combined_score_ppm": _combined_score_ppm(
                    text_score_ppm, signature_score_ppm
                ),
                "label": labels[(left, right)],
            }
        )
    threshold, counts = _select_threshold(rows)
    tp, fp, fn, tn = counts
    body = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "status": _CALIBRATION_STATUS,
        "method": _CALIBRATION_METHOD,
        "fixture_id": fixture["fixture_id"],
        "fixture_sha256": _hash(fixture),
        "subject_count": len(texts),
        "pair_count": len(rows),
        "pairs": rows,
        "synthetic_threshold_ppm": threshold,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "formal_threshold_ppm": None,
        "formal_threshold_approved": False,
        "real_h1_audited": False,
        "raw_text_retained": False,
    }
    return SyntheticCalibrationReceipt.from_dict(
        {"receipt_id": _id("phase5-synthetic-calibration", body), **body}
    )


_CALIBRATION_KEYS = (
    "receipt_id",
    "schema_version",
    "status",
    "method",
    "fixture_id",
    "fixture_sha256",
    "subject_count",
    "pair_count",
    "pairs",
    "synthetic_threshold_ppm",
    "confusion",
    "formal_threshold_ppm",
    "formal_threshold_approved",
    "real_h1_audited",
    "raw_text_retained",
)
_CALIBRATION_PAIR_KEYS = (
    "left_subject_id",
    "right_subject_id",
    "left_text_sha256",
    "right_text_sha256",
    "text_tfidf_score_ppm",
    "signature_jaccard_score_ppm",
    "combined_score_ppm",
    "label",
)


def _validate_calibration_record(value: object) -> dict[str, object]:
    record = _exact(value, _CALIBRATION_KEYS, "calibration receipt")
    if (
        record["schema_version"] != CALIBRATION_SCHEMA_VERSION
        or record["status"] != _CALIBRATION_STATUS
        or record["method"] != _CALIBRATION_METHOD
        or record["fixture_id"] != _FIXTURE_ID
    ):
        raise ValueError("calibration receipt authority drifted")
    fixture_sha256 = _digest(record["fixture_sha256"], "fixture hash")

    rows: list[dict[str, object]] = []
    subject_hashes: dict[str, str] = {}
    observed_pairs: list[tuple[str, str]] = []
    for raw in _sequence(record["pairs"], "calibration pairs"):
        row = _exact(raw, _CALIBRATION_PAIR_KEYS, "calibration pair")
        left = _text(row["left_subject_id"], "left subject id")
        right = _text(row["right_subject_id"], "right subject id")
        if (
            not left.startswith("synthetic-dup-")
            or not right.startswith("synthetic-dup-")
            or left >= right
        ):
            raise ValueError("calibration pair order/scope drifted")
        left_hash = _digest(row["left_text_sha256"], "left text hash")
        right_hash = _digest(row["right_text_sha256"], "right text hash")
        for subject_id, digest in ((left, left_hash), (right, right_hash)):
            previous = subject_hashes.setdefault(subject_id, digest)
            if previous != digest:
                raise ValueError("subject text hash drifted across calibration pairs")
        text_score = _integer(
            row["text_tfidf_score_ppm"],
            "text TF-IDF score",
            maximum=1_000_000,
        )
        signature_score = _integer(
            row["signature_jaccard_score_ppm"],
            "signature Jaccard score",
            maximum=1_000_000,
        )
        combined_score = _integer(
            row["combined_score_ppm"],
            "combined score",
            maximum=1_000_000,
        )
        if combined_score != _combined_score_ppm(text_score, signature_score):
            raise ValueError("combined score replay drifted")
        if row["label"] not in _LABELS:
            raise ValueError("calibration pair label drifted")
        observed_pairs.append((left, right))
        rows.append(
            {
                "left_subject_id": left,
                "right_subject_id": right,
                "left_text_sha256": left_hash,
                "right_text_sha256": right_hash,
                "text_tfidf_score_ppm": text_score,
                "signature_jaccard_score_ppm": signature_score,
                "combined_score_ppm": combined_score,
                "label": row["label"],
            }
        )

    subject_ids = sorted(subject_hashes)
    subject_count = _integer(record["subject_count"], "subject_count", minimum=2)
    pair_count = _integer(record["pair_count"], "pair_count", minimum=1)
    expected_pairs = list(combinations(subject_ids, 2))
    if subject_count != len(subject_ids) or pair_count != len(rows):
        raise ValueError("calibration count replay drifted")
    if pair_count != subject_count * (subject_count - 1) // 2:
        raise ValueError("calibration pair count is not complete")
    if observed_pairs != expected_pairs:
        raise ValueError("calibration pairs must be complete and canonically ordered")

    threshold = _integer(
        record["synthetic_threshold_ppm"],
        "synthetic threshold",
        maximum=1_000_001,
    )
    replayed_threshold, replayed_counts = _select_threshold(rows)
    if threshold != replayed_threshold:
        raise ValueError("synthetic threshold replay drifted")
    confusion = _exact(record["confusion"], ("tp", "fp", "fn", "tn"), "confusion")
    normalized_confusion = {
        key: _integer(confusion[key], f"confusion.{key}") for key in ("tp", "fp", "fn", "tn")
    }
    if tuple(normalized_confusion[key] for key in ("tp", "fp", "fn", "tn")) != replayed_counts:
        raise ValueError("calibration confusion replay drifted")
    if sum(normalized_confusion.values()) != pair_count:
        raise ValueError("calibration confusion count drifted")

    if record["formal_threshold_ppm"] is not None:
        raise ValueError("formal threshold must remain null")
    for key in ("formal_threshold_approved", "real_h1_audited", "raw_text_retained"):
        _false(record[key], key)

    normalized = {
        **record,
        "fixture_sha256": fixture_sha256,
        "subject_count": subject_count,
        "pair_count": pair_count,
        "pairs": rows,
        "synthetic_threshold_ppm": threshold,
        "confusion": normalized_confusion,
    }
    body = {key: normalized[key] for key in normalized if key != "receipt_id"}
    if normalized["receipt_id"] != _id("phase5-synthetic-calibration", body):
        raise ValueError("calibration receipt id drifted")
    return normalized


@dataclass(frozen=True)
class SyntheticCalibrationReceipt:
    canonical_json: str
    sha256_digest: str

    @classmethod
    def from_dict(cls, value: object) -> "SyntheticCalibrationReceipt":
        record = _validate_calibration_record(value)
        canonical = _canonical(record)
        result = cls(canonical.decode(), sha256(canonical).hexdigest())
        result.validate()
        return result

    @classmethod
    def from_json_bytes(cls, value: bytes) -> "SyntheticCalibrationReceipt":
        if not isinstance(value, bytes) or not value:
            raise ValueError("calibration receipt JSON must be non-empty bytes")
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("calibration receipt JSON is invalid") from exc
        if _canonical(parsed) != value:
            raise ValueError("calibration receipt JSON bytes are not canonical")
        return cls.from_dict(parsed)

    def validate(self) -> None:
        if not isinstance(self.canonical_json, str) or not self.canonical_json:
            raise ValueError("stored calibration receipt JSON must be non-empty text")
        try:
            parsed = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise ValueError("stored calibration receipt JSON is invalid") from exc
        canonical = _canonical(parsed)
        if canonical.decode() != self.canonical_json:
            raise ValueError("stored calibration receipt JSON is not canonical")
        if sha256(canonical).hexdigest() != _digest(
            self.sha256_digest, "calibration receipt digest"
        ):
            raise ValueError("stored calibration receipt digest drifted")
        _validate_calibration_record(parsed)

    def validate_against_fixture(self, fixture: object) -> None:
        self.validate()
        replayed = build_synthetic_duplicate_calibration(fixture)
        if replayed.canonical_json_bytes() != self.canonical_json_bytes():
            raise ValueError("calibration receipt does not match the synthetic fixture")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):
            raise ValueError("stored calibration receipt root is not an object")
        return value

    def canonical_json_bytes(self) -> bytes:
        self.validate()
        return self.canonical_json.encode()


def create_opaque_duplicate_adjudication(
    *,
    left_case_commitment_sha256: str,
    right_case_commitment_sha256: str,
    verdict: str,
    reason_code: str,
    adjudicator_commitment_sha256: str,
) -> dict[str, object]:
    left = _digest(left_case_commitment_sha256, "left commitment")
    right = _digest(right_case_commitment_sha256, "right commitment")
    if left >= right or verdict not in _LABELS:
        raise ValueError("adjudication pair/verdict is invalid")
    body = {
        "schema_version": ADJUDICATION_SCHEMA_VERSION,
        "left_case_commitment_sha256": left,
        "right_case_commitment_sha256": right,
        "verdict": verdict,
        "reason_code": _text(reason_code, "reason_code"),
        "adjudicator_commitment_sha256": _digest(
            adjudicator_commitment_sha256, "adjudicator commitment"
        ),
        "case_content_visible": False,
        "candidate_output_visible": False,
    }
    return validate_opaque_duplicate_adjudication(
        {"decision_id": _id("phase5-opaque-adjudication", body), **body}
    )


def validate_opaque_duplicate_adjudication(value: object) -> dict[str, object]:
    record = _exact(
        value,
        (
            "decision_id",
            "schema_version",
            "left_case_commitment_sha256",
            "right_case_commitment_sha256",
            "verdict",
            "reason_code",
            "adjudicator_commitment_sha256",
            "case_content_visible",
            "candidate_output_visible",
        ),
        "opaque duplicate adjudication",
    )
    if record["schema_version"] != ADJUDICATION_SCHEMA_VERSION:
        raise ValueError("duplicate adjudication schema drifted")
    left = _digest(record["left_case_commitment_sha256"], "left commitment")
    right = _digest(record["right_case_commitment_sha256"], "right commitment")
    if left >= right or record["verdict"] not in _LABELS:
        raise ValueError("adjudication pair/verdict is invalid")
    normalized = {
        **record,
        "left_case_commitment_sha256": left,
        "right_case_commitment_sha256": right,
        "reason_code": _code(record["reason_code"], "reason_code"),
        "adjudicator_commitment_sha256": _digest(
            record["adjudicator_commitment_sha256"], "adjudicator commitment"
        ),
    }
    _false(normalized["case_content_visible"], "case_content_visible")
    _false(normalized["candidate_output_visible"], "candidate_output_visible")
    body = {key: normalized[key] for key in normalized if key != "decision_id"}
    if normalized["decision_id"] != _id("phase5-opaque-adjudication", body):
        raise ValueError("duplicate adjudication id drifted")
    return normalized


def create_synthetic_annotation_commitment(
    *,
    case_commitment_sha256: str,
    annotator_ref: str,
    annotation_bytes: bytes,
) -> dict[str, object]:
    if not isinstance(annotation_bytes, bytes) or not annotation_bytes:
        raise ValueError("annotation_bytes must be non-empty synthetic bytes")
    body = {
        "schema_version": ANNOTATION_COMMITMENT_SCHEMA_VERSION,
        "material_class": "synthetic_fixture",
        "case_commitment_sha256": _digest(case_commitment_sha256, "case commitment"),
        "annotator_ref": _text(annotator_ref, "annotator_ref"),
        "annotation_sha256": sha256(annotation_bytes).hexdigest(),
        "annotation_byte_length": len(annotation_bytes),
        "annotation_content_retained": False,
        "real_h1_or_gold": False,
    }
    return validate_synthetic_annotation_commitment(
        {"commitment_id": _id("phase5-annotation-commitment", body), **body}
    )


def validate_synthetic_annotation_commitment(value: object) -> dict[str, object]:
    record = _exact(
        value,
        (
            "commitment_id",
            "schema_version",
            "material_class",
            "case_commitment_sha256",
            "annotator_ref",
            "annotation_sha256",
            "annotation_byte_length",
            "annotation_content_retained",
            "real_h1_or_gold",
        ),
        "synthetic annotation commitment",
    )
    if (
        record["schema_version"] != ANNOTATION_COMMITMENT_SCHEMA_VERSION
        or record["material_class"] != "synthetic_fixture"
    ):
        raise ValueError("annotation commitment authority drifted")
    annotator_ref = _code(record["annotator_ref"], "annotator_ref")
    if not annotator_ref.startswith("synthetic-"):
        raise ValueError("annotator_ref must remain synthetic")
    normalized = {
        **record,
        "case_commitment_sha256": _digest(
            record["case_commitment_sha256"], "case commitment"
        ),
        "annotator_ref": annotator_ref,
        "annotation_sha256": _digest(record["annotation_sha256"], "annotation hash"),
        "annotation_byte_length": _integer(
            record["annotation_byte_length"], "annotation byte length", minimum=1
        ),
    }
    _false(normalized["annotation_content_retained"], "annotation_content_retained")
    _false(normalized["real_h1_or_gold"], "real_h1_or_gold")
    body = {key: normalized[key] for key in normalized if key != "commitment_id"}
    if normalized["commitment_id"] != _id("phase5-annotation-commitment", body):
        raise ValueError("annotation commitment id drifted")
    return normalized


def create_synthetic_annotation_adjudication(
    *,
    first_commitment: Mapping[str, object],
    second_commitment: Mapping[str, object],
    adjudicator_commitment_sha256: str,
    agreement_status: str,
) -> dict[str, object]:
    if agreement_status not in _AGREEMENT_STATUSES:
        raise ValueError("agreement_status is unsupported")
    first = validate_synthetic_annotation_commitment(first_commitment)
    second = validate_synthetic_annotation_commitment(second_commitment)
    first_id = first["commitment_id"]
    second_id = second["commitment_id"]
    if first_id >= second_id:
        raise ValueError("annotation commitments must be distinct and canonically ordered")
    if first["case_commitment_sha256"] != second["case_commitment_sha256"]:
        raise ValueError("annotation commitments must bind the same case")
    if first["annotator_ref"] == second["annotator_ref"]:
        raise ValueError("annotation commitments must bind distinct annotators")
    body = {
        "schema_version": ANNOTATION_ADJUDICATION_SCHEMA_VERSION,
        "case_commitment_sha256": first["case_commitment_sha256"],
        "annotation_commitment_ids": [first_id, second_id],
        "agreement_status": agreement_status,
        "adjudicator_commitment_sha256": _digest(
            adjudicator_commitment_sha256, "adjudicator commitment"
        ),
        "annotation_content_visible": False,
        "real_h1_or_gold": False,
    }
    return validate_synthetic_annotation_adjudication(
        {"receipt_id": _id("phase5-annotation-adjudication", body), **body}
    )


def validate_synthetic_annotation_adjudication(value: object) -> dict[str, object]:
    record = _exact(
        value,
        (
            "receipt_id",
            "schema_version",
            "case_commitment_sha256",
            "annotation_commitment_ids",
            "agreement_status",
            "adjudicator_commitment_sha256",
            "annotation_content_visible",
            "real_h1_or_gold",
        ),
        "synthetic annotation adjudication",
    )
    if record["schema_version"] != ANNOTATION_ADJUDICATION_SCHEMA_VERSION:
        raise ValueError("annotation adjudication schema drifted")
    commitment_ids = [
        _text(item, "annotation commitment id")
        for item in _sequence(
            record["annotation_commitment_ids"], "annotation commitment ids"
        )
    ]
    if len(commitment_ids) != 2 or commitment_ids != sorted(set(commitment_ids)):
        raise ValueError("annotation commitment ids must be two canonical unique ids")
    commitment_ids = [
        _prefixed_digest_id(
            item,
            "phase5-annotation-commitment",
            "annotation commitment id",
        )
        for item in commitment_ids
    ]
    if record["agreement_status"] not in _AGREEMENT_STATUSES:
        raise ValueError("agreement_status is unsupported")
    normalized = {
        **record,
        "case_commitment_sha256": _digest(
            record["case_commitment_sha256"], "case commitment"
        ),
        "annotation_commitment_ids": commitment_ids,
        "adjudicator_commitment_sha256": _digest(
            record["adjudicator_commitment_sha256"], "adjudicator commitment"
        ),
    }
    _false(normalized["annotation_content_visible"], "annotation_content_visible")
    _false(normalized["real_h1_or_gold"], "real_h1_or_gold")
    body = {key: normalized[key] for key in normalized if key != "receipt_id"}
    if normalized["receipt_id"] != _id("phase5-annotation-adjudication", body):
        raise ValueError("annotation adjudication id drifted")
    return normalized
