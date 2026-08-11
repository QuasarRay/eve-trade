from __future__ import annotations

import os
from typing import Any, Callable

import pytest
from hypothesis import HealthCheck, given, settings

from eve_trade_hypothesis.catalog import category_for, load_catalog
from eve_trade_hypothesis.engine import execute_contract, execute_existing
from eve_trade_hypothesis.modes import runner_for
from eve_trade_hypothesis.strategies import HASH_SEED, contract_strategy


def _examples_for_runner(runner: str) -> int:
    defaults = {
        "edge": ("EVE_TRADE_HYPOTHESIS_LIVE_EXAMPLES", 3),
        "trade": ("EVE_TRADE_HYPOTHESIS_LIVE_EXAMPLES", 3),
        "fault": ("EVE_TRADE_HYPOTHESIS_MODEL_EXAMPLES", 4),
        "repo": ("EVE_TRADE_HYPOTHESIS_STATIC_EXAMPLES", 5),
        "existing": ("EVE_TRADE_HYPOTHESIS_NATIVE_EXAMPLES", 2),
    }
    env, default = defaults[runner]
    return max(1, int(os.environ.get(env, str(default))))


def _property_settings(runner: str):
    return settings(
        max_examples=_examples_for_runner(runner),
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        print_blob=True,
    )


def register_category(namespace: dict[str, Any], category: int, names: list[str]) -> None:
    expected = load_catalog()["categories"].get(str(category))
    if expected is None:
        raise RuntimeError(f"generated module references unknown category {category}")
    expected_names = expected["names"]
    if names != expected_names:
        raise RuntimeError(f"generated category {category} differs from catalog.json")

    runner = runner_for(category)  # collection-time proof of a concrete runner.
    for name in names:
        if category_for(name) != category:
            raise RuntimeError(f"{name} is not uniquely bound to category {category}")
        namespace[name] = _make_contract_test(category, name, runner)


def _make_contract_test(category: int, name: str, runner: str) -> Callable[..., None]:
    strategy = contract_strategy(category, name)

    @_property_settings(runner)
    @given(case=strategy)
    def property_test(contract_runtime, case):
        execute_contract(contract_runtime, category, name, case)

    property_test.__name__ = name
    property_test.__qualname__ = name
    property_test.__doc__ = f"Hypothesis contract {name} (category {category}, runner {runner})."
    property_test.pytestmark = [pytest.mark.hypothesis_contract]
    if runner in {"trade", "edge"}:
        property_test.pytestmark.append(pytest.mark.eve_live)
    elif runner == "fault":
        property_test.pytestmark.append(pytest.mark.eve_fault)
    else:
        property_test.pytestmark.append(pytest.mark.eve_static)
    return property_test


def register_existing(namespace: dict[str, Any], native_file: str, names: list[str]) -> None:
    catalog = load_catalog()["existing"]
    for name in names:
        if name not in catalog:
            raise RuntimeError(f"generated existing wrapper is not cataloged: {name}")
        if native_file not in set(catalog[name].get("native_files", {}).values()):
            raise RuntimeError(f"{name} is not cataloged in native file {native_file}")
        namespace[name] = _make_existing_test(name)


def _make_existing_test(name: str) -> Callable[..., None]:
    @_property_settings("existing")
    @given(hash_seed=HASH_SEED)
    def property_test(contract_runtime, hash_seed):
        execute_existing(contract_runtime, name, hash_seed)

    property_test.__name__ = name
    property_test.__qualname__ = name
    property_test.__doc__ = f"Hypothesis wrapper around existing native E2E test {name}."
    property_test.pytestmark = [pytest.mark.hypothesis_contract, pytest.mark.eve_existing]
    return property_test
