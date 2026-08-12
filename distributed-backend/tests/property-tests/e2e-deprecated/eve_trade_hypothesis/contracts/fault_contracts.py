from __future__ import annotations

from typing import Any


def run(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    """Execute a real fault scenario and validate raw post-fault observations.

    The external process only injects/observes the environment.  Protocol v2
    binds the observations to the exact Hypothesis case and Python evaluates the
    name-derived predicates; a child process cannot make the property green by
    returning a generic success flag.
    """
    runtime.fault.run(name, {"category": category, **case})
