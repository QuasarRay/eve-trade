"""Secret-safe serialization helpers for telemetry and run artifacts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEY_PARTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "DSN",
    "KEY",
    "AUTH",
    "CREDENTIAL",
    "PRIVATE",
)

_ASSIGNMENT = re.compile(
    r"(?i)(\b[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|DSN|KEY|AUTH|CREDENTIAL|PRIVATE)[A-Z0-9_]*\b\s*[=:]\s*)([^\s,;]+)"
)
_AUTH_HEADER = re.compile(r"(?i)(authorization\s*:\s*)(?:bearer\s+|basic\s+)?[^\s]+")
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<credentials>[^/@\s]+@)", re.IGNORECASE)


def is_sensitive_key(key: str) -> bool:
    upper = key.upper()
    return any(part in upper for part in SENSITIVE_KEY_PARTS)


def redacted_presence(value: Any) -> str:
    return "<redacted:present>" if value not in (None, "") else "<redacted:empty>"


def redact_value(key: str, value: Any) -> Any:
    if is_sensitive_key(key):
        return redacted_presence(value)
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(key, item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): redact_value(str(key), value) for key, value in sorted(values.items())}


def redact_text(text: str) -> str:
    """Redact common inline secret shapes without removing useful diagnostics."""

    text = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}<redacted:present>", text)
    text = _AUTH_HEADER.sub(r"\1<redacted:present>", text)
    return _URL_CREDENTIALS.sub(lambda match: f"{match.group('scheme')}<redacted>@", text)


def safe_argv(argv: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    redact_next = False
    for value in argv:
        if redact_next:
            result.append("<redacted:present>")
            redact_next = False
            continue
        if value.startswith("--") and "=" in value:
            name, raw = value.split("=", 1)
            result.append(f"{name}={redacted_presence(raw)}" if is_sensitive_key(name) else redact_text(value))
            continue
        result.append(redact_text(value))
        redact_next = value.startswith("-") and is_sensitive_key(value.lstrip("-"))
    return result

