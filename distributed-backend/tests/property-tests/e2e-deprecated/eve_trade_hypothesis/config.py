from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SuiteConfig:
    repo_root: Path
    strict: bool
    target_branch: str
    fault_driver: str | None
    evidence_driver: str | None
    native_max_examples: int
    live_max_examples: int
    static_max_examples: int
    model_max_examples: int
    run_id: str

    @classmethod
    def from_env(cls) -> "SuiteConfig":
        repo_root = discover_repo_root()
        strict = _truthy(os.environ.get("EVE_TRADE_HYPOTHESIS_STRICT")) or _truthy(
            os.environ.get("EVE_TRADE_E2E_PRODUCTION_GATE")
        )
        return cls(
            repo_root=repo_root,
            strict=strict,
            target_branch=os.environ.get("EVE_TRADE_TARGET_BRANCH", "experimental").strip() or "experimental",
            fault_driver=os.environ.get("EVE_TRADE_FAULT_DRIVER"),
            evidence_driver=os.environ.get("EVE_TRADE_EVIDENCE_DRIVER"),
            native_max_examples=max(1, int(os.environ.get("EVE_TRADE_HYPOTHESIS_NATIVE_EXAMPLES", "2"))),
            live_max_examples=max(1, int(os.environ.get("EVE_TRADE_HYPOTHESIS_LIVE_EXAMPLES", "3"))),
            static_max_examples=max(1, int(os.environ.get("EVE_TRADE_HYPOTHESIS_STATIC_EXAMPLES", "5"))),
            model_max_examples=max(1, int(os.environ.get("EVE_TRADE_HYPOTHESIS_MODEL_EXAMPLES", "40"))),
            run_id=_run_id(),
        )


def discover_repo_root() -> Path:
    explicit = os.environ.get("EVE_TRADE_REPO_ROOT")
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if (root / "go.mod").exists() and (root / "distributed-backend").exists():
            return root
        raise RuntimeError(f"EVE_TRADE_REPO_ROOT does not look like eve-trade: {root}")

    starts = [Path.cwd(), Path(__file__).resolve()]
    for start in starts:
        for candidate in [start, *start.parents]:
            if (candidate / "go.mod").exists() and (candidate / "distributed-backend").exists():
                return candidate
    # Keep collection possible after unpacking away from the repo. Execution will
    # fail/skip via Runtime.require_repo instead of accidentally inspecting cwd.
    return Path.cwd().resolve()


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _run_id() -> str:
    value = os.environ.get("EVE_TRADE_TEST_RUN_ID") or f"pt-{secrets.token_hex(8)}"
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise RuntimeError(
            "EVE_TRADE_TEST_RUN_ID must be a DNS-label-safe identifier of at most 63 characters"
        )
    return value
