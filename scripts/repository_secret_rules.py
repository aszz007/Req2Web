"""Local credential heuristics shared by data preparation and Git hygiene.

These rules identify candidates, not credential validity. No network call is
made, and findings contain only line numbers and rule names.
"""

from __future__ import annotations

import re


SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b")),
    ("bearer_token", re.compile(r"\bBearer[ \t]+[A-Za-z0-9._~+/-]{24,}={0,2}\b", re.I)),
)

# Bound key length and horizontal whitespace prevent quadratic identifier
# matching and accidental capture of the next line after an empty assignment.
ASSIGNMENT = re.compile(
    r'''(?i)(?<![a-z0-9_.-])["']?'''
    r'''(?P<key>[a-z_][a-z0-9_.-]{0,96}(?:password|passwd|pwd|secret|token)|password|passwd|pwd|secret|token)'''
    r'''["']?[ \t]*(?:=|:)[ \t]*'''
    r'''(?:"(?P<double>[^"\r\n]*)"|'(?P<single>[^'\r\n]*)'|(?P<bare>[^\s,;\r\n}#]+))'''
)
SERVICE_CONTEXT = re.compile(
    r"(?i)\b(?:smtp|database|postgres(?:ql)?|mysql|mongodb|redis|ldap|sasl)\b"
)
SERVICE_KEY = re.compile(
    r"(?i)(?:smtp|email|mail|(?:^|[_.-])db(?:[_.-]|$)|database|postgres|mysql|redis|ldap|clientsecret)"
)
AUTHENTICATED_URI = re.compile(
    r'''(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|smtp)://'''
    r'''(?P<user>[^\s:/@"'<>]+):(?P<password>[^\s/@"'<>]+)@[^\s"'<>]+'''
)
PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?P<kind>(?:[A-Z0-9 ]+ )?PRIVATE KEY)-----"
    r"[\s\S]*?(?:-----END (?P=kind)-----|\Z)"
)


def _value_group(match: re.Match[str]) -> str:
    return next(name for name in ("double", "single", "bare") if match.group(name) is not None)


def _is_literal(value: str) -> bool:
    value = value.strip()
    if not value or value.lower() in {"none", "null", "false", "true"}:
        return False
    if value.startswith(("$", "<", "{", "[", "os.", "self.", "config.", "settings.", "process.", "getenv(", "environ", "SecretStr(")):
        return False
    if value.upper().startswith(("YOUR_", "EXAMPLE_", "PLACEHOLDER_")):
        return False
    return bool(re.search(r"[a-z0-9]", value, re.I))


def _assignment_matches(text: str):
    for match in ASSIGNMENT.finditer(text):
        key = match.group("key")
        value = match.group(_value_group(match))
        nearby = text[max(0, match.start() - 700):match.end() + 700]
        if _is_literal(value) and (SERVICE_KEY.search(key) or SERVICE_CONTEXT.search(nearby)):
            yield match


def find_secret_locations(text: str) -> list[dict[str, object]]:
    """Return deterministic, value-free credential candidate locations."""
    positions: set[tuple[int, str]] = set()
    for kind, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            positions.add((text.count("\n", 0, match.start()) + 1, kind))
    for match in _assignment_matches(text):
        key = match.group("key").lower()
        kind = "smtp_credential_assignment" if re.search(r"smtp|email|mail", key) else "service_credential_assignment"
        positions.add((text.count("\n", 0, match.start()) + 1, kind))
    for match in AUTHENTICATED_URI.finditer(text):
        if _is_literal(match.group("password")):
            positions.add((text.count("\n", 0, match.start()) + 1, "authenticated_service_uri"))
    return [{"line": line, "kind": kind} for line, kind in sorted(positions)]


def redact_credential_literals(text: str) -> str:
    """Remove credential-like literals from untrusted, non-authoritative text.

    Use at dataset-ingestion time only. Never apply this function to frozen
    scientific evidence, active contracts, source code, or Git history.
    """
    spans = []
    spans.extend(match.span() for match in PRIVATE_KEY_BLOCK.finditer(text))
    for match in _assignment_matches(text):
        spans.append(match.span(_value_group(match)))
    for match in AUTHENTICATED_URI.finditer(text):
        if _is_literal(match.group("password")):
            spans.append(match.span("password"))
    for _, pattern in SECRET_PATTERNS:
        spans.extend(match.span() for match in pattern.finditer(text))
    # Merge overlapping matches before replacing from right to left.
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + "[REDACTED]" + text[end:]
    return text
