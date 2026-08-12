from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from hypothesis import given

from eve_trade_hypothesis.catalog import existing_names, load_catalog, proposed_names
from eve_trade_hypothesis.modes import (
    EDGE_CATEGORIES,
    FAULT_CATEGORIES,
    REPO_CATEGORIES,
    TRADE_CATEGORIES,
    runner_for,
)
from eve_trade_hypothesis.requirements import requirement_document, requirements_by_name


ROOT = Path(__file__).resolve().parents[1]
PROPERTY_ROOT = ROOT.parent
GENERATED = ROOT / "eve_trade_hypothesis" / "generated"
AUTHORITATIVE = PROPERTY_ROOT / "tests-to-implement.md"


def _module_constants(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                result[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
    return result


def _authoritative_names() -> list[str]:
    return re.findall(r"`(test_[a-z0-9_]+)`", AUTHORITATIVE.read_text(encoding="utf-8"))


def test_every_proposed_catalog_name_is_bound_exactly_once_to_generated_module():
    bound: list[str] = []
    for path in GENERATED.glob("test_category_*.py"):
        constants = _module_constants(path)
        names = constants.get("CONTRACT_NAMES", [])
        bound.extend(names)
        runner_for(int(constants["CATEGORY_ID"]))
    assert len(bound) == len(set(bound)), "a proposed test is generated more than once"
    assert set(bound) == proposed_names()


def test_every_existing_catalog_name_is_bound_exactly_once_to_native_wrapper():
    bound: list[str] = []
    for path in GENERATED.glob("test_existing_*.py"):
        constants = _module_constants(path)
        bound.extend(constants.get("CONTRACT_NAMES", []))
    assert len(bound) == len(set(bound)), "an existing test is wrapped more than once"
    assert set(bound) == existing_names()


def test_authoritative_catalog_registry_and_requirement_names_are_exactly_equal():
    authoritative = _authoritative_names()
    combined = existing_names() | proposed_names()
    requirements = requirements_by_name()
    assert len(authoritative) == len(set(authoritative)), "authoritative catalog contains duplicates"
    assert combined == set(authoritative)
    assert set(requirements) == set(authoritative)
    assert requirement_document()["catalog_contract_count"] == len(authoritative)


def test_existing_and_proposed_catalog_names_are_disjoint():
    assert existing_names().isdisjoint(proposed_names())


def test_every_generated_category_has_a_concrete_native_runner():
    for category_id in sorted(load_catalog()["categories"], key=int):
        assert runner_for(int(category_id)) in {"trade", "edge", "repo", "fault"}


def test_every_requirement_has_exactly_one_known_execution_route():
    valid = {
        "DIRECT_LIVE": "trade edge repo fault",
        "LITMUS_CHAOS": "litmus",
        "PLATFORM_EXTERNAL": "platform",
        "REPOSITORY_STATIC": "repo",
        "NATIVE_EXISTING": "native",
        "NON_APPLICABLE": "non_applicable",
    }
    for name, requirement in requirements_by_name().items():
        mechanism = requirement["mechanism"]
        assert mechanism in valid, name
        assert requirement["runner"] in valid[mechanism].split(), name
        if mechanism == "DIRECT_LIVE":
            supported = {
                "trade": TRADE_CATEGORIES,
                "edge": EDGE_CATEGORIES,
                "repo": REPO_CATEGORIES,
                "fault": FAULT_CATEGORIES,
            }
            assert requirement["category"] in supported[requirement["runner"]], name
        assert requirement["prerequisite"], name
        assert requirement["observable"], name


def test_every_implemented_direct_route_has_a_concrete_adapter_category():
    supported = {
        "trade": TRADE_CATEGORIES,
        "edge": EDGE_CATEGORIES,
        "repo": REPO_CATEGORIES,
        "fault": FAULT_CATEGORIES,
    }
    for name, requirement in requirements_by_name().items():
        if (
            requirement["mechanism"] == "DIRECT_LIVE"
            and requirement["implementation_status"] == "IMPLEMENTED"
        ):
            assert requirement["category"] in supported[requirement["runner"]], name


def test_unresolved_semantic_overrides_are_fail_closed_in_canonical_requirements():
    overrides = json.loads((ROOT / "semantic_override_manifest.json").read_text(encoding="utf-8"))
    requirements = requirements_by_name()
    for name in overrides:
        requirement = requirements[name]
        if requirement["mechanism"] not in {
            "PLATFORM_EXTERNAL",
            "LITMUS_CHAOS",
            "NON_APPLICABLE",
        }:
            if requirement.get("semantic_binding"):
                assert requirement["implementation_status"] == "IMPLEMENTED", name
            else:
                assert requirement["implementation_status"] == "SEMANTIC_ORACLE_REQUIRED", name

    engine_source = (ROOT / "eve_trade_hypothesis" / "engine.py").read_text(encoding="utf-8")
    gate = engine_source.index('if requirement["implementation_status"] in {')
    direct_runner = engine_source.index('runner = str(requirement["runner"])')
    assert gate < direct_runner
    assert "SEMANTIC_EVIDENCE_OVERRIDES" not in engine_source


def test_authoritative_contradictory_issue_item_type_contract_name_is_retained():
    names = proposed_names()
    assert "test_issue_rejects_item_stack_with_wrong_item_type_claim" in names
    assert (
        "test_issue_response_does_not_echo_client_item_type_claim_when_it_differs_from_authoritative_source_stack_item_type"
        not in names
    )
    assert "test_issue_uses_authoritative_item_type_instead_of_client_claim" in names


def test_contract_implementations_do_not_use_uncontrolled_uuid4_generation():
    contracts = ROOT / "eve_trade_hypothesis" / "contracts"
    offenders = []
    for path in contracts.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "uuid4":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


@given(
    case=__import__(
        "eve_trade_hypothesis.strategies", fromlist=["contract_strategy"]
    ).contract_strategy(
        32,
        "test_random_domain_invalid_settlement_operation_sequence_never_creates_negative_wallet_balance",
    )
)
def test_invalid_sequence_strategy_always_reaches_invalid_operation(case):
    assert "invalid" in case["ops"]


@given(
    case=__import__(
        "eve_trade_hypothesis.strategies", fromlist=["contract_strategy"]
    ).contract_strategy(
        32,
        "test_random_domain_valid_issue_accept_cancel_sequence_preserves_total_items",
    )
)
def test_valid_conservation_strategy_always_crosses_business_transition(case):
    assert case["ops"][0] == "issue"
    assert case["ops"][1] in {"accept", "cancel"}


def test_external_protocol_rejects_naked_ok_trust_oracle(tmp_path):
    import sys

    from eve_trade_hypothesis.external import ExternalContractDriver

    script = tmp_path / "naked_ok.py"
    script.write_text(
        "import json,sys\n"
        "r=json.load(sys.stdin)\n"
        "print(json.dumps({'protocol_version':3,'contract':r['contract'],'case_sha256':r['case_sha256'],'ok':True}))\n",
        encoding="utf-8",
    )
    driver = ExternalContractDriver(
        f'"{sys.executable}" "{script}"',
        repo_root=tmp_path,
        strict=True,
        role="evidence",
        run_id="pt-negative-control",
    )
    with pytest.raises(AssertionError, match="generic ok"):
        driver.run(
            "test_issue_rejects_nonexistent_item_stack",
            {"category": 8, "nonce": "meta"},
        )
