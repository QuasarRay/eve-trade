from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from eve_trade_hypothesis.catalog import load_catalog
from eve_trade_hypothesis.contracts import edge_contracts, fault_contracts, repo_contracts, trade_contracts
from eve_trade_hypothesis.modes import runner_for
from eve_trade_hypothesis.semantic_overrides import SEMANTIC_EVIDENCE_OVERRIDES


def execute_contract(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    # Known weak direct implementations from the first audit are deliberately
    # unreachable.  Protocol-v2 evidence requires raw observations and evaluates
    # the named invariant in this Python process instead of trusting a boolean.
    if name in SEMANTIC_EVIDENCE_OVERRIDES:
        runtime.evidence.run(name, {"category": category, **case})
        return

    runner = runner_for(category)
    if runner == "edge":
        edge_contracts.run(runtime, category, name, case)
        return
    if runner == "trade":
        trade_contracts.run(runtime, category, name, case)
        return
    if runner == "repo":
        repo_contracts.run(runtime, category, name, case)
        return
    if runner == "fault":
        fault_contracts.run(runtime, category, name, case)
        return
    raise AssertionError(f"unknown contract runner {runner!r} for category {category}")


def execute_existing(runtime, name: str, hash_seed: int) -> None:
    """Re-run an existing native E2E test under Hypothesis-controlled process state.

    Existing repository tests retain their native assertions.  Hypothesis varies
    Python's hash seed across isolated pytest subprocesses, which is meaningful
    for order/hash-sensitive bugs without replacing the original test logic.
    """
    catalog = load_catalog()
    meta = catalog["existing"].get(name)
    if meta is None:
        raise AssertionError(f"existing test is absent from catalog: {name}")

    branch = runtime.config.target_branch
    native_files = meta.get("native_files", {})
    native_file = native_files.get(branch)
    if not native_file:
        # Branch applicability is not a correctness failure.  Even strict mode
        # must not turn an experimental-only native test into a failing main test.
        pytest.skip(f"{name} is not present on target branch {branch!r}")

    repo = runtime.repo.require_repo()
    target = repo / "distributed-backend" / "tests" / "e2e" / native_file
    if not target.exists():
        message = f"native E2E file missing for {name}: {target}"
        if runtime.config.strict:
            pytest.fail(message)
        pytest.skip(message)

    env = os.environ.copy()
    env["PYTHONHASHSEED"] = str(hash_seed)
    # The nested native test must not recursively collect this standalone
    # property package merely because it is adjacent to the checkout.
    env["EVE_TRADE_HYPOTHESIS_NATIVE_CHILD"] = "1"
    nodeid = f"distributed-backend/tests/e2e/{native_file}::{name}"
    result = runtime.repo.run(
        [sys.executable, "-m", "pytest", nodeid, "-q", "--maxfail=1"],
        env=env,
        timeout=900,
    )
    result.assert_ok(f"native existing E2E contract failed: {nodeid}")
