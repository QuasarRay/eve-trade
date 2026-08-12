"""Reusable declarative business oracles for the EVE Trade property catalog."""

from .catalog_quality import (
    assert_catalog_digest,
    assert_catalog_names_equal_classification,
    assert_catalog_names_unique,
    assert_concurrent_names_identify_shared_resource_or_boundary,
    assert_crash_names_identify_window_and_recovery,
    assert_invalid_names_identify_invalid_property,
    assert_no_alternative_accept_reject_outcomes,
    assert_no_placeholder_words,
    assert_no_vague_success_verbs,
    assert_reject_names_identify_rejected_condition,
    assert_retry_names_identify_business_effect_repeatability,
    assert_timeout_names_identify_boundary_and_persistence_invariant,
)
from .deployment_evidence import assert_real_deployment_prerequisite

__all__ = [name for name in globals() if name.startswith("assert_")]
