#!/usr/bin/env python3
"""Fail closed when a dependency-advisory exception is malformed or stale."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


REQUIRED_FIELDS = frozenset(
    {
        "advisory_id",
        "dependency",
        "justification",
        "expires_on",
        "owner",
        "status",
        "target",
        "features",
        "severities",
    }
)
VALID_SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})


class AllowlistError(ValueError):
    """Raised when an advisory exception is not safe to consume."""


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AllowlistError(f"{field} must be a non-empty string")
    result = value.strip()
    if "*" in result:
        raise AllowlistError(f"{field} must not contain wildcards")
    return result


def _parse_date(value: Any, field: str) -> dt.date:
    text = _nonempty_string(value, field)
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise AllowlistError(f"{field} must be an ISO-8601 calendar date") from exc


def validate_document(
    document: Mapping[str, Any], *, today: dt.date | None = None
) -> tuple[Mapping[str, Any], ...]:
    """Validate and return the active, unexpired exceptions."""
    if document.get("schema_version") != 1:
        raise AllowlistError("schema_version must be exactly 1")
    raw_exceptions = document.get("exceptions")
    if not isinstance(raw_exceptions, list):
        raise AllowlistError("exceptions must be a list")

    check_date = today or dt.datetime.now(dt.timezone.utc).date()
    seen: set[tuple[str, str]] = set()
    validated: list[Mapping[str, Any]] = []
    for index, raw in enumerate(raw_exceptions):
        prefix = f"exceptions[{index}]"
        if not isinstance(raw, dict):
            raise AllowlistError(f"{prefix} must be an object")
        missing = REQUIRED_FIELDS - raw.keys()
        if missing:
            raise AllowlistError(f"{prefix} is missing fields: {sorted(missing)}")

        advisory_id = _nonempty_string(raw["advisory_id"], f"{prefix}.advisory_id")
        dependency = _nonempty_string(raw["dependency"], f"{prefix}.dependency")
        _nonempty_string(raw["justification"], f"{prefix}.justification")
        _nonempty_string(raw["owner"], f"{prefix}.owner")
        _nonempty_string(raw["target"], f"{prefix}.target")
        _nonempty_string(raw["features"], f"{prefix}.features")

        if raw["status"] != "active":
            raise AllowlistError(f"{prefix}.status must be exactly 'active'")
        expires_on = _parse_date(raw["expires_on"], f"{prefix}.expires_on")
        if check_date > expires_on:
            raise AllowlistError(
                f"{prefix} expired on {expires_on.isoformat()} (today is {check_date.isoformat()})"
            )

        severities = raw["severities"]
        if not isinstance(severities, list) or not severities:
            raise AllowlistError(f"{prefix}.severities must be a non-empty list")
        if any(not isinstance(value, str) or "*" in value for value in severities):
            raise AllowlistError(f"{prefix}.severities must not contain wildcards")
        normalized_severities = {value.upper() for value in severities}
        if normalized_severities - VALID_SEVERITIES:
            raise AllowlistError(
                f"{prefix}.severities contains unsupported values: "
                f"{sorted(normalized_severities - VALID_SEVERITIES)}"
            )

        key = (advisory_id, dependency)
        if key in seen:
            raise AllowlistError(f"duplicate advisory/dependency exception: {key!r}")
        seen.add(key)
        validated.append(raw)
    return tuple(validated)


def load_and_validate(path: Path, *, today: dt.date | None = None) -> tuple[Mapping[str, Any], ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AllowlistError(f"cannot read advisory allowlist {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise AllowlistError("allowlist root must be an object")
    return validate_document(document, today=today)


def exception_is_active(
    exceptions: Iterable[Mapping[str, Any]],
    *,
    advisory_id: str,
    dependency: str,
    severity: str,
) -> bool:
    """Return true only for an exact advisory, crate, and severity match."""
    normalized_severity = severity.upper()
    return any(
        item["advisory_id"] == advisory_id
        and item["dependency"] == dependency
        and normalized_severity in {value.upper() for value in item["severities"]}
        for item in exceptions
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--today", type=dt.date.fromisoformat)
    args = parser.parse_args()
    exceptions = load_and_validate(args.path, today=args.today)
    print(f"validated {len(exceptions)} active advisory exception(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
