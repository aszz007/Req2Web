from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable

from .index import TfidfIndex


SearchResult = dict[str, Any]


@runtime_checkable
class Retriever(Protocol):
    """Backend-neutral retrieval contract used by the Agent chain."""

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[SearchResult]: ...

    def search_by_role(
        self, query: str, top_k: int = 2
    ) -> dict[str, list[SearchResult]]: ...


@dataclass(frozen=True)
class RetrieverConfig:
    index_dir: Path
    backend: str = "tfidf"
    options: Mapping[str, Any] = field(default_factory=dict)


class TfidfRetriever:
    """Adapter exposing the existing TF-IDF index through ``Retriever``."""

    def __init__(self, index: TfidfIndex) -> None:
        self._index = index

    @classmethod
    def load(cls, index_dir: Path) -> "TfidfRetriever":
        return cls(TfidfIndex.load(index_dir))

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[SearchResult]:
        return self._index.search(query=query, top_k=top_k, roles=roles)

    def search_by_role(
        self, query: str, top_k: int = 2
    ) -> dict[str, list[SearchResult]]:
        return self._index.search_by_role(query=query, top_k=top_k)


RetrieverFactory = Callable[[RetrieverConfig], Retriever]


class RetrieverRegistry:
    """Small registry that keeps future retrieval backends out of Agent code."""

    def __init__(self) -> None:
        self._factories: dict[str, RetrieverFactory] = {}

    def register(
        self, name: str, factory: RetrieverFactory, *, replace: bool = False
    ) -> None:
        normalized = name.strip().casefold()
        if not normalized:
            raise ValueError("retriever backend name must not be empty")
        if normalized in self._factories and not replace:
            raise ValueError(f"retriever backend is already registered: {normalized}")
        self._factories[normalized] = factory

    def create(self, config: RetrieverConfig) -> Retriever:
        name = config.backend.strip().casefold()
        try:
            factory = self._factories[name]
        except KeyError as exc:
            available = ", ".join(sorted(self._factories)) or "none"
            raise ValueError(
                f"unregistered retriever backend {config.backend!r}; available: {available}"
            ) from exc
        retriever = factory(config)
        if not isinstance(retriever, Retriever):
            raise TypeError(
                f"retriever backend {name!r} does not implement the Retriever protocol"
            )
        return retriever

    def available_backends(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


def _create_tfidf_retriever(config: RetrieverConfig) -> Retriever:
    return TfidfRetriever.load(config.index_dir)


DEFAULT_RETRIEVER_REGISTRY = RetrieverRegistry()
DEFAULT_RETRIEVER_REGISTRY.register("tfidf", _create_tfidf_retriever)


def create_retriever(
    config: RetrieverConfig,
    registry: RetrieverRegistry = DEFAULT_RETRIEVER_REGISTRY,
) -> Retriever:
    return registry.create(config)
