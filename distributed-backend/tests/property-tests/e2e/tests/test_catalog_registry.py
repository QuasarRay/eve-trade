from __future__ import annotations

import ast
from pathlib import Path

from hypothesis import given, strategies as st

from eve_trade_hypothesis.catalog import existing_names, load_catalog, proposed_names
from eve_trade_hypothesis.modes import runner_for


ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "eve_trade_hypothesis" / "generated"


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


def test_existing_and_proposed_catalog_names_are_disjoint():
    assert existing_names().isdisjoint(proposed_names())


def test_combined_catalog_contains_exactly_1484_unique_named_contracts():
    combined = existing_names() | proposed_names()
    assert len(existing_names()) == 131
    assert len(proposed_names()) == 1353
    assert len(combined) == 1484


@given(category_id=st.sampled_from(sorted(load_catalog()["categories"], key=int)))
def test_every_hypothesis_generated_category_has_concrete_runner(category_id: str):
    assert runner_for(int(category_id)) in {"trade", "edge", "repo", "fault"}


def test_every_proposed_contract_has_nonempty_semantic_evidence_spec():
    from eve_trade_hypothesis.evidence_specs import build_evidence_spec

    catalog = load_catalog()
    for category_id, category in catalog["categories"].items():
        for name in category["names"]:
            spec = build_evidence_spec(int(category_id), name)
            # Three setup predicates are mandatory; at least one additional
            # predicate must encode the named postcondition.
            assert len(spec.predicates) >= 4, name
            predicate_paths = {predicate.path for predicate in spec.predicates}
            assert "scenario.generated_case_applied" in predicate_paths, name
            assert {"scenario", "outcome"}.issubset(set(spec.required_sections)), name


def test_audited_weak_direct_contracts_are_rerouted_before_direct_runner():
    from eve_trade_hypothesis.semantic_overrides import (
        SEMANTIC_EVIDENCE_OVERRIDES,
        SEMANTIC_OVERRIDE_FINDINGS,
    )

    assert SEMANTIC_EVIDENCE_OVERRIDES == frozenset(SEMANTIC_OVERRIDE_FINDINGS)
    assert len(SEMANTIC_EVIDENCE_OVERRIDES) >= 202
    assert SEMANTIC_EVIDENCE_OVERRIDES <= proposed_names()
    second_pass = {
        "test_concurrent_accepts_into_same_destination_stack_do_not_lose_updates",
        "test_concurrent_identical_requests_execute_business_operation_exactly_once",
        "test_concurrent_insert_of_same_idempotency_key_executes_single_settlement",
        "test_concurrent_issues_from_same_item_stack_cannot_escrow_more_than_owned",
        "test_different_idempotency_keys_generate_different_trade_ids_for_same_trade_payload",
        "test_every_declared_race_contract_targets_at_least_one_go_test",
        "test_govulncheck_scans_every_go_package_in_module",
        "test_idempotency_record_principal_binding_cannot_be_changed_after_creation",
        "test_idempotency_record_request_fingerprint_cannot_be_changed_after_creation",
        "test_idempotency_record_terminal_response_cannot_be_overwritten_by_retry",
        "test_issue_of_entire_source_stack_leaves_source_stack_quantity_zero_without_negative_quantity_or_orphaned_escrow",
        "test_issue_rejects_nonexistent_seller",
        "test_istio_and_gateway_api_production_overlays_preserve_same_readiness_and_liveness_probes",
        "test_istio_and_gateway_api_production_overlays_preserve_same_resource_requests",
        "test_multiple_partial_accepts_and_cancel_race_conserves_items_and_isk",
        "test_partial_accept_and_cancel_race_conserves_items_and_isk",
        "test_postgres_search_path_is_fixed_for_runtime_role",
        "test_reordered_udp_datagrams_with_distinct_interaction_ids_are_processed_as_independent_requests",
        "test_replay_cache_fingerprint_includes_authenticated_principal",
        "test_security_definer_functions_set_safe_search_path_before_accessing_objects",
        "test_udp_edge_does_not_reveal_whether_failure_was_unknown_key_id_or_wrong_secret",
        "test_unknown_operation_enum_value_is_rejected_instead_of_mapping_to_zero_value_operation",
    }
    assert second_pass <= SEMANTIC_EVIDENCE_OVERRIDES

    engine_source = (ROOT / "eve_trade_hypothesis" / "engine.py").read_text(encoding="utf-8")
    override_pos = engine_source.index("if name in SEMANTIC_EVIDENCE_OVERRIDES")
    runner_pos = engine_source.index("runner = runner_for(category)")
    assert override_pos < runner_pos


def test_old_contradictory_issue_item_type_contract_name_was_replaced():
    names = proposed_names()
    assert "test_issue_rejects_item_stack_with_wrong_item_type_claim" not in names
    assert (
        "test_issue_response_does_not_echo_client_item_type_claim_when_it_differs_from_authoritative_source_stack_item_type"
        in names
    )
    assert "test_issue_uses_authoritative_item_type_instead_of_client_claim" in names


def test_contract_implementations_do_not_use_uncontrolled_uuid4_generation():
    contracts = ROOT / "eve_trade_hypothesis" / "contracts"
    offenders = []
    for path in contracts.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        # Ignore explanatory comments/docstrings; inspect AST calls instead.
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "uuid4":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


@given(case=__import__("eve_trade_hypothesis.strategies", fromlist=["contract_strategy"]).contract_strategy(
    32, "test_random_domain_invalid_settlement_operation_sequence_never_creates_negative_wallet_balance"
))
def test_invalid_sequence_strategy_always_reaches_invalid_operation(case):
    assert "invalid" in case["ops"]


def test_external_protocol_rejects_naked_ok_trust_oracle(tmp_path):
    import json
    import sys

    from eve_trade_hypothesis.external import ExternalContractDriver

    script = tmp_path / "naked_ok.py"
    script.write_text(
        "import json,sys\n"
        "r=json.load(sys.stdin)\n"
        "print(json.dumps({'protocol_version':2,'contract':r['contract'],'case_sha256':r['case_sha256'],'ok':True}))\n",
        encoding="utf-8",
    )
    driver = ExternalContractDriver(
        f'"{sys.executable}" "{script}"',
        repo_root=tmp_path,
        strict=True,
        role="evidence",
    )
    with pytest.raises(AssertionError, match="no raw evidence"):
        driver.run(
            "test_replay_cache_is_scoped_by_authenticated_principal",
            {"category": 5, "nonce": "meta"},
        )
