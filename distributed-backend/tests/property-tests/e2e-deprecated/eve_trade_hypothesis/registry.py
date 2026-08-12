from __future__ import annotations

import os
from typing import Any, Callable

import pytest
from hypothesis import HealthCheck, given, settings

from eve_trade_hypothesis.catalog import category_for, load_catalog
from eve_trade_hypothesis.engine import execute_contract, execute_existing
from eve_trade_hypothesis.requirements import requirement_for
from eve_trade_hypothesis.strategies import contract_strategy


def _examples_for(mechanism: str) -> int:
    defaults = {
        "DIRECT_LIVE": ("EVE_TRADE_HYPOTHESIS_LIVE_EXAMPLES", 3),
        "LITMUS_CHAOS": ("EVE_TRADE_HYPOTHESIS_CHAOS_EXAMPLES", 3),
        "PLATFORM_EXTERNAL": ("EVE_TRADE_HYPOTHESIS_PLATFORM_EXAMPLES", 3),
    }
    env, default = defaults[mechanism]
    return max(1, int(os.environ.get(env, str(default))))


def _property_settings(mechanism: str):
    # These properties deliberately reuse a function-scoped runtime while each
    # example resets/isolates authoritative state inside the adapter. Suppress
    # only that one applicable health check; keep all other health checks active.
    deadline_ms = {
        "DIRECT_LIVE": 120_000,
        "LITMUS_CHAOS": 650_000,
        "PLATFORM_EXTERNAL": 650_000,
    }[mechanism]
    return settings(
        max_examples=_examples_for(mechanism),
        deadline=deadline_ms,
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

    for name in names:
        if category_for(name) != category:
            raise RuntimeError(f"{name} is not uniquely bound to category {category}")
        requirement = requirement_for(name)
        if requirement.get("category") != category:
            raise RuntimeError(f"{name} requirement category differs from generated registry")
        namespace[name] = _make_contract_test(category, name, requirement)


def _make_contract_test(
    category: int,
    name: str,
    requirement: dict[str, Any],
) -> Callable[..., None]:
    mechanism = str(requirement["mechanism"])
    runner = str(requirement["runner"])
    if mechanism in {"REPOSITORY_STATIC", "NON_APPLICABLE"}:
        def deterministic_test(contract_runtime):
            execute_contract(
                contract_runtime,
                category,
                name,
                {"nonce": "deterministic-static", "reverse": False, "sample": 0},
            )

        property_test = deterministic_test
    else:
        strategy = contract_strategy(category, name)

        @_property_settings(mechanism)
        @given(case=strategy)
        def generated_test(contract_runtime, case):
            execute_contract(contract_runtime, category, name, case)

        property_test = generated_test

    property_test.__name__ = name
    property_test.__qualname__ = name
    property_test.__doc__ = (
        f"{mechanism} contract {name} (category {category}, runner {runner})."
    )
    property_test.pytestmark = [pytest.mark.hypothesis_contract]
    if requirement["implementation_status"] not in {"IMPLEMENTED", "JUSTIFIED_NON_APPLICABLE"}:
        property_test.pytestmark.append(pytest.mark.eve_unimplemented)
    if mechanism == "DIRECT_LIVE":
        property_test.pytestmark.append(pytest.mark.eve_live)
    elif mechanism == "LITMUS_CHAOS":
        property_test.pytestmark.append(pytest.mark.eve_fault)
    elif mechanism == "PLATFORM_EXTERNAL":
        property_test.pytestmark.append(pytest.mark.eve_platform)
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
        requirement = requirement_for(name)
        if requirement["mechanism"] != "NATIVE_EXISTING":
            raise RuntimeError(f"native wrapper has wrong requirement route: {name}")
        namespace[name] = _make_existing_test(name)


def _make_existing_test(name: str) -> Callable[..., None]:
    def native_test(contract_runtime):
        execute_existing(contract_runtime, name)

    native_test.__name__ = name
    native_test.__qualname__ = name
    native_test.__doc__ = f"Native E2E contract {name}; executed once with its original oracle."
    native_test.pytestmark = [pytest.mark.hypothesis_contract, pytest.mark.eve_existing]
    return native_test
