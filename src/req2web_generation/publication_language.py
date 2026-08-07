from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from .schema import PageSpec


ENGLISH_PUBLICATION_LANGUAGE_POLICY_VERSION = (
    "req2web.publication_language.english_only.v1"
)

_CJK_TEXT = re.compile(
    r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\ufe30-\ufe4f\uff01-\uff65"
    r"\U00020000-\U0002fa1f]"
)


class PublicationLanguageError(ValueError):
    """Raised when an active publication or runtime artifact contains CJK."""


def contains_cjk_text(value: str) -> bool:
    return bool(_CJK_TEXT.search(value))


def _visible_page_spec_text(
    page_spec: PageSpec,
) -> Iterable[tuple[str, str]]:
    yield "title", page_spec.title
    yield "summary", page_spec.summary
    yield "target_device", page_spec.target_device
    yield "page_type", page_spec.page_type
    for index, item in enumerate(page_spec.use_cases):
        prefix = f"use_cases[{index}]"
        yield f"{prefix}.title", item.title
        yield f"{prefix}.actor", item.actor
        yield f"{prefix}.goal", item.goal
        yield f"{prefix}.expected_outcome", item.expected_outcome
    for index, item in enumerate(page_spec.sections):
        prefix = f"sections[{index}]"
        yield f"{prefix}.title", item.title
        yield f"{prefix}.purpose", item.purpose
    for index, item in enumerate(page_spec.components):
        prefix = f"components[{index}]"
        yield f"{prefix}.label", item.label
        yield f"{prefix}.purpose", item.purpose
    for index, item in enumerate(page_spec.states):
        prefix = f"states[{index}]"
        yield f"{prefix}.name", item.name
        yield f"{prefix}.description", item.description
    for index, item in enumerate(page_spec.interactions):
        prefix = f"interactions[{index}]"
        yield f"{prefix}.action", item.action
        yield f"{prefix}.user_feedback", item.user_feedback
    for index, item in enumerate(page_spec.constraints):
        yield f"constraints[{index}].description", item.description
    for index, item in enumerate(page_spec.acceptance_checks):
        yield f"acceptance_checks[{index}].description", item.description


def validate_english_publication_page_spec(page_spec: PageSpec) -> None:
    if not isinstance(page_spec, PageSpec):
        raise TypeError("English publication validation requires a PageSpec")
    page_spec.validate()
    violations = [
        field_name
        for field_name, value in _visible_page_spec_text(page_spec)
        if contains_cjk_text(value)
    ]
    if violations:
        joined = ", ".join(violations[:5])
        suffix = "" if len(violations) <= 5 else ", ..."
        raise PublicationLanguageError(
            "English publication PageSpec contains CJK text in "
            f"{joined}{suffix}"
        )


def validate_english_publication_artifact(
    content: bytes | str,
    *,
    artifact_name: str,
) -> None:
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PublicationLanguageError(
                f"{artifact_name} must be UTF-8"
            ) from exc
    elif isinstance(content, str):
        text = content
    else:
        raise TypeError("publication artifact must be bytes or text")
    if contains_cjk_text(text):
        raise PublicationLanguageError(
            f"English publication artifact contains CJK text: {artifact_name}"
        )


def validate_english_publication_value(
    value: object,
    *,
    artifact_name: str,
) -> None:
    """Reject CJK text anywhere in a JSON-compatible active artifact."""
    if isinstance(value, str):
        validate_english_publication_artifact(
            value,
            artifact_name=artifact_name,
        )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            validate_english_publication_value(
                item,
                artifact_name=f"{artifact_name}.{key}",
            )
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_english_publication_value(
                item,
                artifact_name=f"{artifact_name}[{index}]",
            )


def validate_english_publication_tree(root: Path) -> None:
    """Reject CJK text in every text artifact under an active result root."""
    directory = Path(root)
    if not directory.is_dir():
        raise PublicationLanguageError(
            f"publication artifact root is not a directory: {directory}"
        )
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        if path.suffix.casefold() not in {
            ".css",
            ".html",
            ".js",
            ".json",
            ".jsonl",
            ".txt",
        }:
            continue
        validate_english_publication_artifact(
            path.read_bytes(),
            artifact_name=path.relative_to(directory).as_posix(),
        )


__all__ = [
    "ENGLISH_PUBLICATION_LANGUAGE_POLICY_VERSION",
    "PublicationLanguageError",
    "contains_cjk_text",
    "validate_english_publication_artifact",
    "validate_english_publication_page_spec",
    "validate_english_publication_tree",
    "validate_english_publication_value",
]
