from __future__ import annotations

import gzip
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from .corpus import ROLE_ORDER
from .schema import validate_document
from .validation_signals import build_validation_signals
from .ui_structure_signals import build_ui_structure_signals


INDEX_SCHEMA_VERSION = "req2web.rag.tfidf.v1"
ASCII_TOKEN = re.compile(r"[a-z0-9][a-z0-9_+.-]{1,}")
CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
CJK_TEXT = re.compile(
    r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\ufe30-\ufe4f\uff01-\uff65"
    r"\U00020000-\U0002fa1f]"
)

QUERY_EXPANSIONS = {
    "login": "sign in authentication account",
    "register": "sign up account",
    "search": "query results",
    "filter": "sort results",
    "product": "commerce shopping store",
    "cart": "basket checkout commerce",
    "checkout": "payment order",
    "payment": "checkout order",
    "list": "feed catalog results",
    "detail": "profile content",
    "form": "input field submit",
    "mobile": "app responsive",
    "responsive": "desktop tablet mobile webpage",
    "tap": "click gesture interaction",
    "swipe": "scroll gesture interaction",
    "flow": "workflow interaction state steps",
    "error": "edge case failure validation",
    "permission": "access auth validation",
    "acceptance": "validation test expected behavior",
}

RETRIEVAL_RESULT_PROJECTION_REVISION = (
    "req2web.rag.compact_result.english_projection.v1"
)


def _english_projection(value: object, *, role: str, field: str, doc_id: str) -> str:
    text = str(value).strip()
    if not CJK_TEXT.search(text):
        return text
    role_label = role.replace("_", " ")
    if field == "title":
        return f"{role_label.title()} reference {doc_id}"
    return f"English structural evidence for the {role_label} role"


def _ascii_references(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    projected: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        uri = row.get("uri")
        if isinstance(uri, str):
            row["uri"] = quote(
                uri,
                safe="/:@?&=#%._-",
            )
        projected.append(row)
    return projected


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    tokens = ASCII_TOKEN.findall(normalized)
    for run in CJK_RUN.findall(normalized):
        tokens.append(run)
        tokens.extend(run[index : index + 2] for index in range(max(0, len(run) - 1)))
        if len(run) <= 8:
            tokens.extend(run)
    return tokens


def expand_query(query: str) -> str:
    additions = [expansion for phrase, expansion in QUERY_EXPANSIONS.items() if phrase in query]
    return " ".join([query, *additions])


def _document_text(document: dict[str, Any]) -> str:
    weighted = [
        document["title"],
        document["title"],
        document["summary"],
        document["summary"],
        document["content"],
        " ".join(document["tags"]),
    ]
    return "\n".join(weighted)


def _compact_result(document: dict[str, Any], score: float) -> dict[str, Any]:
    """Project one backend score through the shared retrieval-result contract."""

    result = {
        "score": round(score, 6),
        "doc_id": document["doc_id"],
        "role": document["role"],
        "dataset": document["dataset"],
        "subset": document["subset"],
        "sample_id": document["sample_id"],
        "title": _english_projection(
            document["title"],
            role=document["role"],
            field="title",
            doc_id=document["doc_id"],
        ),
        "summary": _english_projection(
            document["summary"],
            role=document["role"],
            field="summary",
            doc_id=document["doc_id"],
        ),
        "result_projection_revision": RETRIEVAL_RESULT_PROJECTION_REVISION,
        "references": _ascii_references(document["references"]),
    }
    if document["role"] == "validation":
        result["validation_signals"] = build_validation_signals(document)
    if document["role"] == "ui_reference":
        result["ui_structure_signals"] = build_ui_structure_signals(document)
    return result


def build_tfidf_index(
    documents: list[dict[str, Any]], max_features: int = 30_000
) -> dict[str, Any]:
    if not documents:
        raise ValueError("cannot build an index without documents")
    term_counts: list[Counter[str]] = []
    document_frequency: Counter[str] = Counter()
    for document in documents:
        validate_document(document)
        counts = Counter(tokenize(_document_text(document)))
        term_counts.append(counts)
        document_frequency.update(counts.keys())

    ranked_terms = sorted(
        document_frequency,
        key=lambda term: (-document_frequency[term], term),
    )[:max_features]
    vocabulary = set(ranked_terms)
    document_count = len(documents)
    idf = {
        term: math.log((document_count + 1) / (document_frequency[term] + 1)) + 1.0
        for term in ranked_terms
    }
    postings: dict[str, list[list[float | int]]] = defaultdict(list)
    for document_index, counts in enumerate(term_counts):
        weights = {
            term: (1.0 + math.log(count)) * idf[term]
            for term, count in counts.items()
            if term in vocabulary
        }
        norm = math.sqrt(sum(weight * weight for weight in weights.values())) or 1.0
        for term, weight in weights.items():
            postings[term].append([document_index, round(weight / norm, 8)])

    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "index_type": "sparse_tfidf_cosine",
        "document_count": document_count,
        "document_ids": [document["doc_id"] for document in documents],
        "idf": {term: round(idf[term], 8) for term in ranked_terms},
        "postings": dict(postings),
    }


def write_index(path: Path, index: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    temporary.write_bytes(gzip.compress(payload, mtime=0))
    temporary.replace(path)


def _load_documents(path: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid document JSON at {path}:{line_number}") from exc
            validate_document(document)
            documents.append(document)
    return documents


class TfidfIndex:
    def __init__(self, documents: list[dict[str, Any]], index: dict[str, Any]) -> None:
        if index.get("schema_version") != INDEX_SCHEMA_VERSION:
            raise ValueError(f"unsupported index schema: {index.get('schema_version')}")
        if len(documents) != index.get("document_count"):
            raise ValueError("document and index counts do not match")
        ids = [document["doc_id"] for document in documents]
        if ids != index.get("document_ids"):
            raise ValueError("document order does not match the index")
        self.documents = documents
        self.index = index

    @classmethod
    def load(cls, directory: Path) -> "TfidfIndex":
        documents = _load_documents(directory / "documents.jsonl")
        with gzip.open(directory / "tfidf_index.json.gz", "rt", encoding="utf-8") as stream:
            index = json.load(stream)
        return cls(documents, index)

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be empty")
        allowed_roles = set(roles) if roles is not None else None
        query_counts = Counter(tokenize(expand_query(query)))
        idf = self.index["idf"]
        query_weights = {
            term: (1.0 + math.log(count)) * idf[term]
            for term, count in query_counts.items()
            if term in idf
        }
        norm = math.sqrt(sum(weight * weight for weight in query_weights.values())) or 1.0
        scores: defaultdict[int, float] = defaultdict(float)
        for term, weight in query_weights.items():
            query_weight = weight / norm
            for document_index, document_weight in self.index["postings"].get(term, []):
                document = self.documents[document_index]
                if allowed_roles is None or document["role"] in allowed_roles:
                    scores[document_index] += query_weight * document_weight

        ranked = sorted(
            scores.items(),
            key=lambda item: (
                -item[1],
                self.documents[item[0]]["doc_id"],
            ),
        )
        results: list[dict[str, Any]] = []
        for document_index, score in ranked[:top_k]:
            document = self.documents[document_index]
            results.append(_compact_result(document, score))
        return results

    def search_by_role(self, query: str, top_k: int = 2) -> dict[str, list[dict[str, Any]]]:
        return {role: self.search(query, top_k=top_k, roles=[role]) for role in ROLE_ORDER}


class Bm25Index:
    """In-memory Okapi BM25 over the same validated document envelope."""

    def __init__(
        self,
        documents: list[dict[str, Any]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        if not documents:
            raise ValueError("cannot build BM25 without documents")
        if not isinstance(k1, float) or not 0.0 < k1 <= 5.0:
            raise ValueError("BM25 k1 must be a float in (0, 5]")
        if not isinstance(b, float) or not 0.0 <= b <= 1.0:
            raise ValueError("BM25 b must be a float in [0, 1]")
        term_counts: list[Counter[str]] = []
        document_frequency: Counter[str] = Counter()
        document_lengths: list[int] = []
        for document in documents:
            validate_document(document)
            counts = Counter(tokenize(_document_text(document)))
            term_counts.append(counts)
            document_frequency.update(counts.keys())
            document_lengths.append(sum(counts.values()))
        self.documents = documents
        self.k1 = k1
        self.b = b
        self._term_counts = term_counts
        self._document_frequency = document_frequency
        self._document_lengths = document_lengths
        self._average_document_length = (
            sum(document_lengths) / len(document_lengths)
        )

    @classmethod
    def load(
        cls,
        directory: Path,
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> "Bm25Index":
        return cls(_load_documents(directory / "documents.jsonl"), k1=k1, b=b)

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        allowed_roles = set(roles) if roles is not None else None
        query_terms = sorted(set(tokenize(expand_query(query))))
        document_count = len(self.documents)
        scores: list[tuple[int, float]] = []
        for document_index, document in enumerate(self.documents):
            if allowed_roles is not None and document["role"] not in allowed_roles:
                continue
            counts = self._term_counts[document_index]
            length = self._document_lengths[document_index]
            normalization = self.k1 * (
                1.0
                - self.b
                + self.b * length / self._average_document_length
            )
            score = 0.0
            for term in query_terms:
                frequency = counts.get(term, 0)
                if frequency == 0:
                    continue
                frequency_document_count = self._document_frequency[term]
                inverse_document_frequency = math.log(
                    1.0
                    + (
                        document_count
                        - frequency_document_count
                        + 0.5
                    )
                    / (frequency_document_count + 0.5)
                )
                score += inverse_document_frequency * (
                    frequency * (self.k1 + 1.0)
                    / (frequency + normalization)
                )
            if score > 0.0:
                scores.append((document_index, score))
        ranked = sorted(
            scores,
            key=lambda item: (-item[1], self.documents[item[0]]["doc_id"]),
        )
        return [
            _compact_result(self.documents[document_index], score)
            for document_index, score in ranked[:top_k]
        ]

    def search_by_role(
        self,
        query: str,
        top_k: int = 2,
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            role: self.search(query, top_k=top_k, roles=[role])
            for role in ROLE_ORDER
        }
