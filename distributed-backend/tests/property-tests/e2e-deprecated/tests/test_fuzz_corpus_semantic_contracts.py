from __future__ import annotations

from pathlib import Path

import pytest

from eve_trade_hypothesis.contracts.fuzz_corpus_contracts import (
    CORPUS_DIRECTORY,
    FUZZ_CORPUS_CONTRACTS,
    binding_metadata,
    decode_go_fuzz_seed,
    rust_fuzz_ci_is_wired,
    seed_satisfies_oracle,
    validate_fuzz_corpus_contract,
)
from eve_trade_hypothesis.requirements import requirements_by_name


REPO_ROOT = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize("name", sorted(FUZZ_CORPUS_CONTRACTS))
def test_each_fuzz_corpus_binding_executes_its_exact_oracle(name):
    validate_fuzz_corpus_contract(name, REPO_ROOT)


def test_fuzz_corpus_bindings_are_exactly_the_executable_registry():
    requirements = requirements_by_name()
    bound = {
        name: requirement["semantic_binding"]
        for name, requirement in requirements.items()
        if requirement.get("semantic_binding", {}).get("family") == "fuzz_corpus"
    }
    assert bound == {
        name: binding_metadata(spec)
        for name, spec in FUZZ_CORPUS_CONTRACTS.items()
    }
    assert all(requirements[name]["implementation_status"] == "IMPLEMENTED" for name in bound)


@pytest.mark.parametrize(
    ("oracle", "counterexample"),
    [
        ("duplicate_json_key", b'{"key_id":"one","signature":"two"}'),
        ("integer_overflow", b'{"quantity":9223372036854775807}'),
        ("invalid_utf8", b'{"valid":"utf8"}'),
        ("nesting_depth", b'[[[[0]]]]'),
        ("trailing_bytes", b'{"one":1}   \n\t'),
    ],
)
def test_seed_oracles_reject_nearby_non_regression_counterexamples(oracle, counterexample):
    assert not seed_satisfies_oracle(oracle, counterexample)


def test_every_materialized_gateway_seed_uses_the_go_corpus_encoding():
    directory = REPO_ROOT / CORPUS_DIRECTORY
    decoded = [decode_go_fuzz_seed(path) for path in directory.iterdir() if path.is_file()]
    assert decoded and all(isinstance(item, bytes) for item in decoded)


def test_rust_fuzz_conditional_fails_closed_when_a_new_target_has_no_ci_command():
    targets = (Path("fuzz/fuzz_targets/parser.rs"),)
    assert not rust_fuzz_ci_is_wired(targets, "cargo test --locked")
    assert rust_fuzz_ci_is_wired(targets, "cargo fuzz run parser")
