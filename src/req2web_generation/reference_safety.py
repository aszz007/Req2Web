from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


_WINDOWS_DRIVE_PATH = re.compile(r"^[a-zA-Z]:[/\\]")


def is_absolute_local_path(value: str) -> bool:
    """Return whether *value* identifies a machine-local absolute path."""

    decoded = unquote(value.strip())
    lowered = decoded.casefold()
    return (
        lowered.startswith("file:")
        or decoded.startswith(("/", "\\"))
        or bool(_WINDOWS_DRIVE_PATH.match(decoded))
    )


def validate_reference_uri(value: str) -> None:
    """Allow project-relative references and HTTPS URLs only.

    In particular, all spellings of local ``file:`` URIs are rejected before
    URL parsing, including ``file://`` and ``file:///C:/...``.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("reference URI must be a non-empty string")
    candidate = unquote(value.strip())
    if is_absolute_local_path(candidate):
        raise ValueError("reference URI must not contain a local absolute path")
    parsed = urlsplit(candidate)
    if parsed.scheme and parsed.scheme.casefold() != "https":
        raise ValueError("reference URI must be project-relative or use HTTPS")

