from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from eve_trade_hypothesis.catalog import load_catalog
from eve_trade_hypothesis.contracts import edge_contracts, fault_contracts, repo_contracts, trade_contracts
from eve_trade_hypothesis.requirements import requirement_for


def execute_contract(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    requirement = requirement_for(name)
    mechanism = requirement["mechanism"]
    if mechanism == "NON_APPLICABLE":
        pytest.skip(requirement["rationale"])
    if requirement["implementation_status"] in {
        "SEMANTIC_ORACLE_REQUIRED",
        "EXTERNAL_CAPABILITY_REQUIRED",
        "INFRASTRUCTURE_READY",
    }:
        blockers = "; ".join(requirement.get("blockers", []))
        runtime.live.unavailable(
            f"{name} is fail-closed ({requirement['implementation_status']}) until its "
            f"named independent oracle and physical capability are implemented: {blockers}"
        )
    if mechanism == "LITMUS_CHAOS":
        runtime.fault.run(name, {"category": category, **case})
        return
    if mechanism == "PLATFORM_EXTERNAL":
        runtime.evidence.run(name, {"category": category, **case})
        return
    runner = str(requirement["runner"])
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


def execute_existing(runtime, name: str) -> None:
    """Run an existing native E2E test once with its original semantic oracle."""
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

    runtime.repo.require_repo()
    repo = runtime.repo.root
    target = repo / "distributed-backend" / "tests" / "e2e" / native_file
    if not target.exists():
        message = f"native E2E file missing for {name}: {target}"
        if runtime.config.strict:
            pytest.fail(message)
        pytest.skip(message)

    env = os.environ.copy()
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
