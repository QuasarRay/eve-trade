"""Explicit C33 business identities backed by independent reusable oracles."""
from __future__ import annotations

from pathlib import Path

import pytest

from eve_trade_oracles import (
    assert_catalog_digest,
    assert_catalog_names_equal_classification,
    assert_catalog_names_unique,
    assert_concurrent_names_identify_shared_resource_or_boundary,
    assert_crash_names_identify_window_and_recovery,
    assert_invalid_names_identify_invalid_property,
    assert_no_alternative_accept_reject_outcomes,
    assert_no_placeholder_words,
    assert_no_vague_success_verbs,
    assert_real_deployment_prerequisite,
    assert_reject_names_identify_rejected_condition,
    assert_retry_names_identify_business_effect_repeatability,
    assert_timeout_names_identify_boundary_and_persistence_invariant,
)


PROPERTY_ROOT = Path(__file__).resolve().parent.parent
SOURCE_CATALOG_DIGEST = "3756963f3dc7a896d4565bdbb24523c29566462c38b8a962f65fddcb137b5e84"


def _establish_real_context(evidence: dict[str, object]) -> None:
    assert_real_deployment_prerequisite(evidence)


@pytest.mark.context_independent
def test_proposed_test_names_are_unique_across_categories_except_explicit_priority_index(
    real_deployment_evidence, source_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_catalog_names_unique(source_business_records)


@pytest.mark.context_independent
def test_proposed_test_names_contain_no_placeholder_words_todo_fixme_or_tbd(
    real_deployment_evidence, source_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_no_placeholder_words(source_business_records)


@pytest.mark.context_independent
def test_proposed_test_names_contain_no_vague_success_verbs(
    real_deployment_evidence, source_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_no_vague_success_verbs(source_business_records)


@pytest.mark.context_independent
def test_proposed_test_names_do_not_encode_two_alternative_expected_outcomes_with_rejected_or_accepted_wording(
    real_deployment_evidence, source_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_no_alternative_accept_reject_outcomes(source_business_records)


@pytest.mark.context_independent
def test_proposed_test_names_using_reject_identify_the_specific_rejected_condition(
    real_deployment_evidence, source_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_reject_names_identify_rejected_condition(source_business_records)


@pytest.mark.context_independent
def test_proposed_test_names_using_retry_identify_whether_business_effect_may_repeat(
    real_deployment_evidence, source_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_retry_names_identify_business_effect_repeatability(source_business_records)


@pytest.mark.context_independent
def test_proposed_test_names_using_concurrent_identify_the_shared_resource_or_race_boundary(
    real_deployment_evidence, source_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_concurrent_names_identify_shared_resource_or_boundary(source_business_records)


@pytest.mark.context_independent
def test_proposed_test_names_using_crash_identify_crash_window_and_post_restart_invariant(
    real_deployment_evidence, source_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_crash_names_identify_window_and_recovery(source_business_records)


@pytest.mark.context_independent
def test_proposed_test_names_using_timeout_identify_timeout_boundary_and_persistence_invariant(
    real_deployment_evidence, source_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_timeout_names_identify_boundary_and_persistence_invariant(source_business_records)


@pytest.mark.context_independent
def test_proposed_test_names_using_invalid_identify_the_exact_invalid_property(
    real_deployment_evidence, source_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_invalid_names_identify_invalid_property(source_business_records)


@pytest.mark.context_independent
def test_regression_canonical_test_catalog_contains_every_required_contract(
    real_deployment_evidence, source_business_records, classified_business_records
):
    _establish_real_context(real_deployment_evidence)
    assert_catalog_names_equal_classification(
        source_business_records,
        classified_business_records,
        expected_count=1484,
    )


@pytest.mark.context_independent
def test_regression_canonical_test_catalog_rejects_removed_contract_without_explicit_update(
    real_deployment_evidence,
):
    _establish_real_context(real_deployment_evidence)
    assert_catalog_digest(PROPERTY_ROOT / "tests-to-implement", SOURCE_CATALOG_DIGEST)
