"""Central Sentry defaults used by the observed runner."""

from __future__ import annotations

import os


def environment_name() -> str:
    return os.getenv("OBSERVABILITY_ENV") or ("github-actions" if os.getenv("GITHUB_ACTIONS") else "local")


def release_name() -> str | None:
    return os.getenv("GITHUB_SHA") or None


def traces_sample_rate() -> float:
    try:
        return float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
    except ValueError:
        return 0.1

