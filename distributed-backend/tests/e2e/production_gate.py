"""Fail-closed policy primitives for the production E2E gate.

This module deliberately has no pytest or service-client imports.  The pytest
hooks use these pure decisions, while contract tests can exercise the same
production policy with hostile session/settings observations.
"""

from __future__ import annotations

from collections.abc import Mapping


PRODUCTION_GATE_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

REQUIRED_PRODUCTION_SETTINGS = (
    "EVE_TRADE_ENCORE_URL",
    "EVE_TRADE_SIMULATOR_URL",
    "EVE_TRADE_DATABASE_URL",
    "EVE_TRADE_MARKET_DATABASE_URL",
    "EVE_TRADE_SETTLEMENT_GRPC",
    "EVE_TRADE_NSQ_TCP",
    "EVE_TRADE_NSQ_HTTP",
    "EVE_TRADE_RUNTIME_DATABASE_URL",
    "EVE_TRADE_QUILKIN_UDP_HOST",
    "EVE_TRADE_EDGE_RESPONSE_SECRET",
    "EVE_TRADE_EDGE_RESPONSE_KEY_ID",
    "EVE_TRADE_EDGE_SELLER_KEY_ID",
    "EVE_TRADE_EDGE_SELLER_SECRET",
    "EVE_TRADE_EDGE_BUYER_KEY_ID",
    "EVE_TRADE_EDGE_BUYER_SECRET",
    "EVE_TRADE_EDGE_OTHER_KEY_ID",
    "EVE_TRADE_EDGE_OTHER_SECRET",
)

# Exact fixture values and template fragments are rejected.  Loopback addresses
# are intentionally allowed: the disposable Kind runner reaches real pods via
# owned kubectl port-forwards and therefore cannot be classified from hostname
# alone.
_PLACEHOLDER_EXACT = frozenset(
    {
        "changeme",
        "change-me",
        "example",
        "example-value",
        "placeholder",
        "replace-me",
        "todo",
        "tbd",
        "xxx",
    }
)
_PLACEHOLDER_FRAGMENTS = (
    "${",
    "{{",
    "<replace",
    "your-",
    "example.invalid",
)


def production_gate_enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in PRODUCTION_GATE_TRUE_VALUES


def placeholder_setting_names(settings: Mapping[str, str | None]) -> list[str]:
    invalid: list[str] = []
    for name in REQUIRED_PRODUCTION_SETTINGS:
        value = str(settings.get(name) or "").strip()
        lowered = value.lower()
        if not value:
            continue
        if lowered in _PLACEHOLDER_EXACT or any(fragment in lowered for fragment in _PLACEHOLDER_FRAGMENTS):
            invalid.append(name)
    return invalid


def validate_required_settings(settings: Mapping[str, str | None]) -> None:
    missing = sorted(name for name in REQUIRED_PRODUCTION_SETTINGS if not settings.get(name))
    if missing:
        raise ValueError("production-gate E2E settings are missing: " + ", ".join(missing))
    placeholders = placeholder_setting_names(settings)
    if placeholders:
        raise ValueError(
            "production-gate E2E settings contain placeholder values: "
            + ", ".join(placeholders)
        )


def production_session_failed(
    *,
    tests_collected: int,
    skipped: int,
    pytest_exit_ok: bool,
) -> bool:
    """Return whether the production gate must override an otherwise clean run."""

    if tests_collected == 0:
        return True
    if not pytest_exit_ok:
        return False
    return skipped > 0
