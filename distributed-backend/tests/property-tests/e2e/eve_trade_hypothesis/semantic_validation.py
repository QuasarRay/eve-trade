from __future__ import annotations

"""Extra validation for contracts rerouted after the source-level semantic audit.

EvidenceSpec predicates encode the positive property.  This module additionally
checks that the experiment actually isolates the named cause and measures the
named postcondition.  These checks specifically close the false-green paths
reported by the first implementation audit (confounded inputs, vacuous guards,
keyword proxies, missing concurrency, and so on).
"""

from typing import Any
import re

from .semantic_overrides import SEMANTIC_OVERRIDE_FINDINGS


def _get(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            raise AssertionError(f"semantic evidence missing required path {path!r}; stopped at {part!r}")
        cur = cur[part]
    return cur


def _eq(data: dict[str, Any], path: str, expected: Any) -> None:
    actual = _get(data, path)
    assert actual == expected, (path, actual, expected)


def _true(data: dict[str, Any], path: str) -> None:
    _eq(data, path, True)


def _false(data: dict[str, Any], path: str) -> None:
    _eq(data, path, False)


def _same(data: dict[str, Any], left: str, right: str) -> None:
    a, b = _get(data, left), _get(data, right)
    assert a == b, (left, a, right, b)


def _different(data: dict[str, Any], left: str, right: str) -> None:
    a, b = _get(data, left), _get(data, right)
    assert a != b, (left, a, right, b)


def _nonempty_list(data: dict[str, Any], path: str) -> list[Any]:
    value = _get(data, path)
    assert isinstance(value, list) and value, (path, value)
    return value


def validate_audited_semantics(name: str, result: dict[str, Any]) -> None:
    """Close the exact false-positive class that caused a contract to be rerouted."""
    findings = SEMANTIC_OVERRIDE_FINDINGS.get(name)
    if not findings:
        return

    # Every rerouted direct test must prove a real control and a real mutant/probe
    # were executed.  This prevents a driver from supplying only a desired final
    # value without demonstrating the experiment that made the name meaningful.
    _true(result, 'scenario.control_path_executed')
    _true(result, 'scenario.named_path_executed')

    for kind in findings:
        if kind in {'confounded_input', 'confounded_payload', 'confounded_failure', 'multiple_invalid_fields'}:
            _true(result, 'scenario.control_case_valid')
            _true(result, 'scenario.only_named_dimension_differs')
        elif kind == 'confounded_authorization':
            _true(result, 'scenario.first_request_valid_in_isolation')
            _true(result, 'scenario.second_request_valid_in_isolation')
            _different(result, 'security.first_authenticated_principal', 'security.second_authenticated_principal')
            _same(result, 'identity.first_interaction_id', 'identity.second_interaction_id')
        elif kind == 'panic_not_proven':
            _true(result, 'process.target_was_alive_before_input')
            _true(result, 'process.target_was_alive_after_input')
            _true(result, 'process.valid_followup_probe_succeeded')
        elif kind == 'vacuous_pass':
            _true(result, 'transport.response_received')
        elif kind == 'wrong_measurement':
            _same(result, 'transport.measured_response_size', 'transport.raw_response_size')
            assert _get(result, 'transport.raw_response_size') > 0
        elif kind == 'wrong_input':
            _true(result, 'scenario.named_wire_representation_observed')
            raw = _get(result, 'transport.raw_request_utf8')
            assert isinstance(raw, str) and raw, raw
        elif kind in {'missing_postcondition', 'postcondition_incomplete'}:
            _true(result, 'outcome.named_postcondition_observed')
        elif kind == 'partial_assertion':
            _true(result, 'outcome.named_secondary_postcondition_observed')
        elif kind == 'contradictory_contract':
            _true(result, 'scenario.authoritative_value_differs_from_client_claim')
            _true(result, 'comparison.authoritative_value_won_over_client_claim')
        elif kind == 'wrong_generator':
            assert _get(result, 'scenario.generated_invalid_operation_count') >= 1
            _true(result, 'scenario.invalid_operation_reached_target_boundary')
        elif kind == 'vacuous_container_name_filter':
            assert _get(result, 'container.matched_target_container_count') >= 1
            _true(result, 'container.every_matched_target_checked')
        elif kind == 'wrong_security_boundary':
            _true(result, 'security.named_authenticated_boundary_exercised')
        elif kind == 'wrong_state_mutation':
            _true(result, 'scenario.authoritative_state_was_mutated')
            _false(result, 'scenario.only_client_claim_was_mutated')
        elif kind == 'weak_relation':
            _true(result, 'comparison.named_relation_compared_against_authoritative_state')
        elif kind in {'constraint_not_tested', 'constraint_definition_wrong'}:
            _true(result, 'database.constraint_observed_in_catalog')
            _true(result, 'database.direct_violation_attempted')
            _true(result, 'database.direct_violation_rejected_by_database')
        elif kind == 'atomicity_not_tested':
            _true(result, 'fault.atomicity_boundary_injected')
            _false(result, 'effects.split_visibility_observed')
        elif kind == 'internal_consistency_only':
            _true(result, 'comparison.external_authoritative_delta_observed')
            _same(result, 'comparison.ledger_delta', 'comparison.authoritative_business_delta')
        elif kind == 'exactly_one_not_tested':
            _true(result, 'effects.one_to_one_mapping_enumerated')
            _eq(result, 'effects.maximum_matches_per_mutation', 1)
            _eq(result, 'effects.minimum_matches_per_mutation', 1)
        elif kind == 'outbox_not_required':
            _eq(result, 'effects.outbox_row_delta', 1)
            _false(result, 'effects.business_without_outbox_visibility_observed')
            _false(result, 'effects.outbox_without_business_visibility_observed')
        elif kind == 'unrelated_assertion':
            _true(result, 'comparison.assertion_targets_named_subject')
        elif kind == 'global_text_proxy':
            _true(result, 'inspection.structured_parser_used')
            _true(result, 'inspection.named_object_selected')
        elif kind == 'partial_state':
            _true(result, 'comparison.full_named_state_snapshot_compared')
        elif kind == 'regex_incomplete':
            _true(result, 'inspection.complete_authoritative_set_enumerated')
        elif kind == 'presence_not_cardinality':
            _true(result, 'inspection.cardinality_computed_from_authoritative_set')
        elif kind == 'half_assertion':
            _true(result, 'outcome.both_named_halves_asserted')
        elif kind == 'retry_count_ambiguous':
            _true(result, 'retry.attempt_identity_correlation_verified')
            assert _get(result, 'retry.attempt_count') >= 1
        elif kind == 'narrow_pattern':
            _true(result, 'inspection.all_declared_forms_enumerated')
        elif kind == 'vacuous_schema_guard':
            _true(result, 'database.named_column_exists')
            _true(result, 'database.named_column_value_asserted')
        elif kind == 'substring_proxy':
            _true(result, 'inspection.structured_value_compared')
        elif kind == 'exactly_once_not_tested':
            _eq(result, 'effects.business_effect_count', 1)
            _true(result, 'retry.duplicate_or_retry_was_actually_delivered')
        elif kind in {'some_provider_vs_every_provider', 'any_not_every', 'coverage_not_enumerated', 'existence_not_traceability', 'implementation_presence_not_test_traceability'}:
            subjects = _nonempty_list(result, 'coverage.authoritative_subjects')
            covered = _get(result, 'coverage.covered_subjects')
            assert isinstance(covered, list)
            assert set(covered) == set(subjects), (subjects, covered)
        elif kind == 'wrong_object_type':
            _true(result, 'inspection.named_object_type_selected')
        elif kind == 'workflow_only_scope':
            _true(result, 'inspection.runtime_or_deployment_scope_checked')
        elif kind in {'sha_presence_not_equality', 'global_sha_proxy'}:
            _true(result, 'comparison.sha_values_observed_from_named_sources')
            _same(result, 'comparison.observed_sha', 'comparison.expected_sha')
        elif kind == 'build_context_proxy':
            _true(result, 'inspection.actual_build_context_observed')
        elif kind == 'only_existence_checked':
            _true(result, 'inspection.named_semantic_value_asserted')
        elif kind == 'database_boundary_not_observed':
            _true(result, 'database.named_boundary_observed_at_database_layer')
        elif kind == 'audit_identity_not_compared':
            _true(result, 'comparison.audit_identity_compared_across_retry')
            _same(result, 'comparison.audit_identity_first', 'comparison.audit_identity_retry')
        elif kind == 'hardcoded_initial_state':
            _true(result, 'state.initial_value_read_from_authoritative_store')
        elif kind == 'failure_not_injected':
            _true(result, 'fault.injected')
            _true(result, 'fault.active_during_target_window')
        elif kind == 'token_presence_proxy':
            _true(result, 'inspection.semantic_subjects_parsed')
        elif kind == 'global_duplicate_test_proxy':
            _true(result, 'coverage.handler_to_duplicate_delivery_test_mapping_built')
        elif kind == 'wrong_assertion':
            _true(result, 'comparison.observed_inventory_collected_natively')
            _same(result, 'comparison.observed_inventory', 'comparison.catalog_inventory')
        elif kind == 'arrow_presence_only':
            mappings = _nonempty_list(result, 'inspection.rename_mappings')
            for mapping in mappings:
                assert isinstance(mapping, dict)
                assert isinstance(mapping.get('old'), str) and mapping['old'].startswith('test_')
                assert isinstance(mapping.get('new'), str) and mapping['new'].startswith('test_')
                assert mapping['old'] != mapping['new']
        elif kind == 'required_set_not_proven':
            required = _nonempty_list(result, 'coverage.required_subjects')
            observed = _get(result, 'coverage.observed_subjects')
            assert isinstance(observed, list)
            assert set(observed) == set(required), (required, observed)
        elif kind == 'start_at_one_not_tested':
            vals = _nonempty_list(result, 'ordering.observed_ordinals')
            assert vals == list(range(1, len(vals) + 1)), vals
        elif kind == 'wrong_relation':
            _true(result, 'comparison.named_cross_table_relation_checked')
        elif kind == 'scope_weaker_than_name':
            _true(result, 'inspection.full_named_scope_checked')
            _true(result, 'inspection.scope_matches_test_name_exactly')
        elif kind == 'scope_overbroad':
            _true(result, 'coverage.named_scope_resolved_from_authoritative_source')
        elif kind == 'failure_path_not_proven':
            _true(result, 'fault.preceding_step_forced_to_fail')
            _true(result, 'outcome.named_cleanup_or_finish_path_still_executed')
        elif kind == 'job_override_not_checked':
            _true(result, 'inspection.effective_workflow_and_job_permissions_resolved')
        elif kind == 'trigger_not_checked':
            _true(result, 'inspection.workflow_event_triggers_and_job_conditions_evaluated')
            _false(result, 'outcome.named_job_reachable_from_forbidden_event')
        elif kind == 'command_presence_only':
            _true(result, 'inspection.exact_command_working_directory_and_arguments_verified')
        elif kind == 'keyword_presence_proxy':
            _true(result, 'inspection.structured_parser_used')
            _true(result, 'inspection.named_resource_effective_value_checked')
        elif kind == 'generic_presence_branch':
            _true(result, 'scenario.named_negative_case_executed')
            _true(result, 'outcome.named_gate_behavior_observed')
        elif kind == 'lockfile_nonempty_proxy':
            _true(result, 'inspection.lockfile_semantics_validated_by_native_tool')
        elif kind == 'index_existence_not_usage':
            _true(result, 'database.explain_analyze_executed')
            _true(result, 'database.named_index_used_by_critical_query')
        elif kind == 'allowlist_semantics_not_tested':
            _true(result, 'inspection.allowlist_parsed_structurally')
            _true(result, 'inspection.named_allowlist_policy_exercised')
        elif kind == 'existence_only':
            _true(result, 'inspection.named_resource_selected')
            _true(result, 'inspection.named_resource_semantics_checked')
        elif kind == 'source_string_presence':
            _true(result, 'scenario.runtime_behavior_exercised')
        elif kind == 'line_based_hcl_proxy':
            _true(result, 'inspection.hcl_parsed_structurally')
            _true(result, 'inspection.effective_rule_evaluated')
        elif kind == 'heuristic_not_semantic':
            _true(result, 'inspection.explicit_naming_grammar_applied')
        elif kind == 'static_presence_vs_runtime_behavior':
            _true(result, 'fault.named_dependency_failure_injected')
            _true(result, 'outcome.runtime_probe_observed')
        elif kind == 'vacuous_resource_lookup':
            _true(result, 'inspection.required_resource_found')
        elif kind == 'dockerfile_text_only':
            _true(result, 'container.image_built')
            _true(result, 'container.runtime_image_inspected_or_executed')
        elif kind == 'global_validation_token_proxy':
            _true(result, 'inspection.named_proto_field_validation_rule_resolved')
            _true(result, 'scenario.named_field_boundary_exercised')
        elif kind == 'retryability_not_tested':
            _true(result, 'retry.retry_policy_exercised')
            _true(result, 'retry.classification_matches_expected')
        elif kind == 'race_flag_presence_only':
            _true(result, 'concurrency.race_detector_process_executed')
            _true(result, 'concurrency.target_test_function_executed')
        elif kind == 'concurrency_not_exercised':
            assert _get(result, 'concurrency.contenders') >= 2
            _true(result, 'concurrency.overlap_at_target_boundary')
        elif kind == 'wrong_failure_path':
            _true(result, 'fault.named_failure_path_triggered')
        elif kind == 'cache_confounded_determinism':
            _true(result, 'scenario.plan_constructed_twice_without_response_cache_reuse')
        elif kind == 'platform_not_checked':
            required = _get(result, 'coverage.required_platforms')
            observed = _get(result, 'coverage.platforms_verified_by_native_lock_command')
            assert isinstance(required, list) and required
            assert set(required).issubset(set(observed)), (required, observed)
        elif kind == 'name_stronger_than_logic':
            _true(result, 'inspection.full_named_scope_checked')
        elif kind == 'multi_stage_scope_error':
            _true(result, 'container.final_runtime_stage_selected')
        elif kind == 'generation_equivalence_not_tested':
            _true(result, 'inspection.regeneration_executed')
            _eq(result, 'comparison.diff_after_regeneration', '')
        elif kind == 'global_reserved_proxy':
            _true(result, 'inspection.reserved_numbers_checked_against_removed_field_history')
        elif kind == 'token_presence_not_equality':
            _true(result, 'comparison.named_values_resolved_and_compared')
        elif kind == 'scale_and_randomness_missing':
            assert _get(result, 'scenario.sample_count') >= 100
            _true(result, 'scenario.multiple_distinct_schedules_or_jitters_observed')
        elif kind == 'scope_not_checked':
            _true(result, 'inspection.required_paths_or_targets_enumerated')
            _true(result, 'inspection.all_required_paths_or_targets_covered')
        elif kind == 'principal_fingerprint_not_isolated':
            _true(result, 'scenario.same_canonical_business_payload_for_both_principals')
            _different(result, 'security.first_authenticated_principal', 'security.second_authenticated_principal')
            _different(result, 'identity.fingerprint_first', 'identity.fingerprint_second')
        elif kind == 'partial_information_disclosure_check':
            _true(result, 'scenario.unknown_key_id_case_executed')
            _true(result, 'scenario.known_key_wrong_secret_case_executed')
            _same(result, 'comparison.public_error_code_unknown_key', 'comparison.public_error_code_wrong_secret')
            _same(result, 'comparison.public_error_status_unknown_key', 'comparison.public_error_status_wrong_secret')
            _same(result, 'comparison.public_error_schema_unknown_key', 'comparison.public_error_schema_wrong_secret')
            _false(result, 'security.unknown_key_response_discloses_key_existence')
            _false(result, 'security.wrong_secret_response_discloses_key_existence')
        elif kind == 'orphan_postcondition_not_checked':
            _eq(result, 'counts.orphan_item_escrows', 0)
            _eq(result, 'counts.item_escrow_rows_for_trade', 1)
            _same(result, 'state.item_escrow_trade_id', 'state.expected_trade_id')
        elif kind == 'success_count_not_required':
            _same(result, 'effects.success_count', 'effects.expected_success_count')
        elif kind == 'payload_not_held_constant':
            _true(result, 'scenario.only_idempotency_key_differs')
            _different(result, 'identity.first_idempotency_key', 'identity.second_idempotency_key')
            _same(result, 'comparison.business_payload_without_idempotency_first', 'comparison.business_payload_without_idempotency_second')
        elif kind == 'nonexistent_seller_not_isolated':
            _true(result, 'security.authenticated_principal_is_valid')
            _true(result, 'scenario.referenced_seller_row_is_absent')
            _true(result, 'scenario.all_non_seller_references_are_valid')
            _eq(result, 'outcome.rejection_reason', 'seller_not_found')
        elif kind == 'wrong_enum_field_exercised':
            _true(result, 'scenario.request_contains_valid_non_enum_fields')
            _true(result, 'scenario.unknown_operation_enum_value_present')
            _true(result, 'scenario.intent_enum_value_is_valid')
            _eq(result, 'outcome.error_code', 'INVALID_ARGUMENT')
            _false(result, 'effects.database_mutation_observed')
        elif kind == 'race_target_not_resolved':
            contracts = _nonempty_list(result, 'coverage.declared_race_contracts')
            resolved = _get(result, 'coverage.matched_go_test_count_by_race_contract')
            assert isinstance(resolved, dict)
            assert set(resolved) == set(contracts), (contracts, resolved)
            assert all(int(resolved[c]) >= 1 for c in contracts), resolved
        elif kind == 'search_path_not_bound_to_role':
            _true(result, 'database.runtime_role_search_path_observed')
            _same(result, 'database.runtime_role_search_path', 'database.expected_runtime_role_search_path')
        elif kind == 'security_definer_search_path_not_checked':
            funcs = _nonempty_list(result, 'database.security_definer_functions')
            bad = _get(result, 'database.security_definer_functions_without_safe_search_path')
            assert isinstance(bad, list) and not bad, (funcs, bad)
        elif kind == 'overlay_comparison_not_performed':
            _true(result, 'comparison.named_overlays_rendered')
            _same(result, 'comparison.istio_overlay_value', 'comparison.gateway_api_overlay_value')
        elif kind == 'response_cardinality_not_checked':
            _true(result, 'transport.gateway_received_datagram')
            _eq(result, 'transport.response_datagram_count', 1)
        elif kind == 'ordering_not_observed':
            _true(result, 'ordering.reordered_delivery_was_observed')
            _true(result, 'outcome.requests_processed_independently')
        elif kind == 'mutation_not_attempted':
            _true(result, 'scenario.named_mutation_attempted')
            _false(result, 'outcome.named_mutation_committed')
            _same(result, 'comparison.value_before_mutation_attempt', 'comparison.value_after_mutation_attempt')
        elif kind == 'terminal_response_not_compared':
            _true(result, 'retry.duplicate_request_was_delivered')
            _same(result, 'comparison.terminal_response_before_retry', 'comparison.terminal_response_after_retry')
            _eq(result, 'effects.business_effect_count', 1)
        else:
            raise AssertionError(f"no semantic-audit closure rule for finding kind {kind!r} on {name}")

    # Exact-name closures for especially subtle bugs where the generic finding
    # class is not enough to prove the input or arithmetic boundary.
    if name == 'test_udp_edge_rejects_empty_datagram_without_allocating_replay_entry':
        _same(result, 'counts.replay_entries_before', 'counts.replay_entries_after')
        _eq(result, 'transport.sent_size', 0)
    elif name in {
        'test_udp_edge_rejects_truncated_json_without_panicking',
        'test_udp_edge_rejects_invalid_utf8_without_panicking',
        'test_udp_edge_rejects_overlong_utf8_encoding',
    }:
        _false(result, 'outcome.accepted')
        _true(result, 'process.valid_followup_probe_succeeded')
    elif name == 'test_udp_edge_rejects_exponent_notation_for_integer_only_quantity':
        raw = _get(result, 'transport.raw_request_utf8')
        assert re.search(r'"quantity"\s*:\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+', raw), raw
        _false(result, 'outcome.accepted')
    elif name == 'test_udp_edge_rejects_fractional_quantity_even_when_fraction_is_mathematically_integral':
        raw = _get(result, 'transport.raw_request_utf8')
        match = re.search(r'"quantity"\s*:\s*([+-]?\d+)\.(0+)(?![\deE])', raw)
        assert match is not None, raw
        _false(result, 'outcome.accepted')
    elif name == 'test_udp_edge_rejects_hmac_signed_with_different_key_id':
        _same(result, 'security.signature_key_id', 'security.envelope_key_id')
        _true(result, 'security.signature_valid_for_envelope_key_id')
        _true(result, 'security.key_id_is_different_from_required_principal_key')
        _false(result, 'outcome.accepted')
    elif name in {
        'test_replay_cache_is_scoped_by_authenticated_principal',
        'test_identical_interaction_id_from_different_principals_cannot_reuse_cached_response',
    }:
        _false(result, 'identity.cache_cross_principal_hit')
    elif name in {
        'test_interaction_id_reuse_across_issue_and_accept_is_rejected',
        'test_interaction_id_reuse_across_accept_and_cancel_is_rejected',
    }:
        _true(result, 'scenario.first_action_valid_with_fresh_interaction_id')
        _true(result, 'scenario.second_action_valid_with_fresh_interaction_id')
        _eq(result, 'outcome.error_code', 'replay_conflict')
    elif name == 'test_issue_rejects_item_stack_with_wrong_item_type_claim':
        _different(result, 'state.client_claimed_item_type', 'state.authoritative_source_stack_item_type')
        _same(result, 'state.response_item_type', 'state.authoritative_source_stack_item_type')
        _different(result, 'state.response_item_type', 'state.client_claimed_item_type')
    elif name == 'test_issue_uses_authoritative_item_type_instead_of_client_claim':
        _different(result, 'state.client_claimed_item_type', 'state.authoritative_source_stack_item_type')
        _same(result, 'state.persisted_trade_item_type', 'state.authoritative_source_stack_item_type')
        _different(result, 'state.persisted_trade_item_type', 'state.client_claimed_item_type')
    elif name == 'test_accept_rejects_quantity_times_price_integer_overflow':
        q = _get(result, 'boundary.quantity')
        price = _get(result, 'boundary.unit_price')
        maxv = _get(result, 'boundary.product_maximum')
        assert 0 < q <= _get(result, 'boundary.available_quantity')
        assert 0 < price <= _get(result, 'boundary.unit_price_maximum')
        assert q * price > maxv, (q, price, maxv)
        _true(result, 'scenario.buyer_balance_sufficient_for_nonoverflowing_boundary_check')
        _false(result, 'outcome.accepted')
        _eq(result, 'outcome.rejection_reason', 'arithmetic_overflow')
    elif name == 'test_accept_rejects_destination_stack_with_different_item_type':
        _same(result, 'state.destination_stack_owner', 'state.buyer_id')
        _same(result, 'state.destination_stack_station', 'state.trade_station')
        _different(result, 'state.destination_stack_item_type', 'state.trade_item_type')
        _eq(result, 'outcome.rejection_reason', 'destination_item_type_mismatch')
    elif name == 'test_trade_state_change_history_matches_current_trade_state':
        states = _nonempty_list(result, 'state.trade_state_history')
        assert states[-1] == _get(result, 'state.current_trade_state'), (states, _get(result, 'state.current_trade_state'))
    elif name == 'test_idempotency_key_uniqueness_is_enforced_by_database':
        cols = _nonempty_list(result, 'database.unique_constraint_columns')
        assert 'idempotency_key' in cols
        _true(result, 'database.duplicate_insert_rejected_with_unique_violation')
    elif name in {
        'test_idempotency_record_is_written_in_same_transaction_as_business_mutations',
        'test_idempotency_success_record_cannot_exist_without_corresponding_committed_business_effect',
    }:
        _false(result, 'effects.idempotency_visible_without_business_effect')
        _false(result, 'effects.business_effect_visible_without_idempotency')
    elif name == 'test_wallet_escrow_is_bound_to_exact_trade_instance':
        assert _get(result, 'counts.wallet_escrow_rows_for_trade') >= 1
        _same(result, 'state.wallet_escrow_trade_id', 'state.expected_trade_id')
    elif name in {
        'test_every_item_mutation_has_exactly_one_corresponding_ledger_entry',
        'test_every_wallet_mutation_has_exactly_one_corresponding_ledger_entry',
    }:
        _eq(result, 'effects.minimum_matches_per_mutation', 1)
        _eq(result, 'effects.maximum_matches_per_mutation', 1)
    elif name == 'test_settlement_transaction_atomically_commits_business_state_and_outbox_record':
        _eq(result, 'effects.outbox_row_delta', 1)
        _eq(result, 'effects.business_effect_count', 1)
        _false(result, 'effects.split_visibility_observed')
    elif name == 'test_proto_field_reordering_does_not_change_semantics':
        _true(result, 'proto.reordered_descriptor_built')
        _same(result, 'proto.semantic_message_before', 'proto.semantic_message_after')
    elif name == 'test_zero_value_proto_fields_are_not_silently_reinterpreted_as_missing':
        fields = _nonempty_list(result, 'proto.zero_sensitive_fields')
        exercised = _get(result, 'proto.fields_exercised_zero_and_missing')
        assert set(exercised) == set(fields), (fields, exercised)
        _true(result, 'proto.zero_and_missing_outcomes_are_distinguishable_where_contract_requires')
    elif name == 'test_rendered_kubernetes_configures_resource_requests_and_limits':
        _true(result, 'configuration.all_target_containers_have_resource_requests')
        _true(result, 'configuration.all_target_containers_have_resource_limits')
    elif name == 'test_replay_cache_fingerprint_includes_authenticated_principal':
        _true(result, 'scenario.same_canonical_business_payload_for_both_principals')
        _different(result, 'security.first_authenticated_principal', 'security.second_authenticated_principal')
        _different(result, 'identity.fingerprint_first', 'identity.fingerprint_second')
    elif name == 'test_udp_edge_does_not_reveal_whether_failure_was_unknown_key_id_or_wrong_secret':
        _same(result, 'comparison.public_error_code_unknown_key', 'comparison.public_error_code_wrong_secret')
        _same(result, 'comparison.public_error_status_unknown_key', 'comparison.public_error_status_wrong_secret')
        _same(result, 'comparison.public_error_schema_unknown_key', 'comparison.public_error_schema_wrong_secret')
        _false(result, 'security.unknown_key_response_discloses_key_existence')
        _false(result, 'security.wrong_secret_response_discloses_key_existence')
    elif name == 'test_issue_of_entire_source_stack_leaves_source_stack_quantity_zero_without_negative_quantity_or_orphaned_escrow':
        _eq(result, 'state.source_stack_quantity_after', 0)
        assert _get(result, 'state.minimum_source_stack_quantity_observed') >= 0
        _same(result, 'state.item_escrow_quantity_after', 'state.source_stack_quantity_before')
        _eq(result, 'counts.orphan_item_escrows', 0)
        _same(result, 'state.item_escrow_trade_id', 'state.expected_trade_id')
    elif name == 'test_concurrent_accepts_into_same_destination_stack_do_not_lose_updates':
        assert _get(result, 'concurrency.contenders') >= 2
        _true(result, 'concurrency.overlap_at_target_boundary')
        _true(result, 'scenario.each_accept_valid_in_isolation')
        _same(result, 'effects.success_count', 'effects.expected_success_count')
        _same(result, 'state.actual_destination_quantity_delta', 'state.expected_destination_quantity_delta')
    elif name in {
        'test_concurrent_identical_requests_execute_business_operation_exactly_once',
        'test_concurrent_issues_from_same_item_stack_cannot_escrow_more_than_owned',
        'test_concurrent_issues_from_same_stack_preserve_total_item_quantity',
        'test_partial_accept_and_cancel_race_conserves_items_and_isk',
        'test_multiple_partial_accepts_and_cancel_race_conserves_items_and_isk',
        'test_concurrent_insert_of_same_idempotency_key_executes_single_settlement',
    }:
        assert _get(result, 'concurrency.contenders') >= 2
        _true(result, 'concurrency.overlap_at_target_boundary')
    elif name == 'test_different_idempotency_keys_generate_different_trade_ids_for_same_trade_payload':
        _true(result, 'scenario.only_idempotency_key_differs')
        _same(result, 'comparison.business_payload_without_idempotency_first', 'comparison.business_payload_without_idempotency_second')
        _different(result, 'identity.first_idempotency_key', 'identity.second_idempotency_key')
        _different(result, 'state.first_trade_id', 'state.second_trade_id')
    elif name == 'test_issue_rejects_nonexistent_seller':
        _true(result, 'security.authenticated_principal_is_valid')
        _true(result, 'scenario.referenced_seller_row_is_absent')
        _eq(result, 'outcome.rejection_reason', 'seller_not_found')
    elif name == 'test_unknown_operation_enum_value_is_rejected_instead_of_mapping_to_zero_value_operation':
        _true(result, 'scenario.unknown_operation_enum_value_present')
        _true(result, 'scenario.intent_enum_value_is_valid')
        _eq(result, 'outcome.error_code', 'INVALID_ARGUMENT')
        _false(result, 'effects.database_mutation_observed')
    elif name == 'test_existing_repository_test_names_are_never_silently_rewritten_in_observed_inventory':
        _same(result, 'comparison.observed_inventory', 'comparison.catalog_inventory')
