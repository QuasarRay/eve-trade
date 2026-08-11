from __future__ import annotations

"""Audited contracts rerouted to independently checked raw evidence.

The mapping records every non-generic semantic flaw from the first source audit.
The fixed engine checks this set before any old category handler, making known weak
proxy branches unreachable.
"""

SEMANTIC_OVERRIDE_FINDINGS: dict[str, tuple[str, ...]] = {
    'test_accept_rejects_destination_stack_with_different_item_type': ('confounded_failure',),  # category 10
    'test_accept_rejects_quantity_times_price_integer_overflow': ('confounded_failure',),  # category 9
    'test_all_outputs_derived_from_secret_values_are_marked_sensitive': ('wrong_object_type',),  # category 67
    'test_all_terraform_provider_lockfiles_include_linux_amd64_checksums': ('platform_not_checked',),  # category 67
    'test_all_terraform_provider_lockfiles_include_windows_amd64_checksums': ('platform_not_checked',),  # category 67
    'test_all_terraform_roots_pin_provider_versions_with_nonempty_constraints': ('some_provider_vs_every_provider',),  # category 67
    'test_cargo_audit_scans_locked_dependencies_used_by_trade_settlement_build': ('command_presence_only',),  # category 100
    'test_ci_refuses_mutable_latest_tag_as_production_release_identity': ('workflow_only_scope',),  # category 72
    'test_completed_settlement_batch_has_every_required_step_completed': ('required_set_not_proven',),  # category 97
    'test_concurrent_conflicting_requests_with_same_interaction_id_have_exactly_one_winner': ('missing_postcondition',),  # category 5
    'test_concurrent_issues_from_same_stack_preserve_total_item_quantity': ('scope_weaker_than_name', 'concurrency_not_exercised'),  # category 8 + second-pass
    'test_deployed_backend_image_sha_matches_ci_commit_sha': ('sha_presence_not_equality',),  # category 38
    'test_deployed_settlement_image_sha_matches_ci_commit_sha': ('sha_presence_not_equality',),  # category 38
    'test_downloaded_build_tools_are_verified_by_pinned_version_and_integrity_metadata_when_available': ('global_sha_proxy',),  # category 72
    'test_e2e_suite_targets_running_commit_sha': ('sha_presence_not_equality',),  # category 38
    'test_eks_cluster_endpoint_public_access_matches_declared_security_policy': ('keyword_presence_proxy',),  # category 68
    'test_eks_database_backup_retention_meets_configured_production_minimum': ('keyword_presence_proxy',),  # category 68
    'test_eks_database_deletion_protection_is_enabled_for_production_managed_database': ('keyword_presence_proxy',),  # category 68
    'test_eks_database_storage_encryption_is_enabled_when_database_is_managed_by_stack': ('keyword_presence_proxy',),  # category 68
    'test_eks_iam_roles_grant_no_wildcard_actions_outside_explicit_allowlist': ('keyword_presence_proxy',),  # category 68
    'test_eks_kubernetes_provider_uses_created_cluster_endpoint_and_ca_without_static_credentials': ('keyword_presence_proxy',),  # category 68
    'test_eks_secrets_are_not_rendered_into_nonsensitive_terraform_outputs': ('keyword_presence_proxy',),  # category 68
    'test_eks_security_groups_do_not_expose_postgres_port_to_world': ('line_based_hcl_proxy',),  # category 68
    'test_eks_security_groups_do_not_expose_settlement_grpc_port_to_world': ('line_based_hcl_proxy',),  # category 68
    'test_eks_worker_nodes_use_encrypted_root_volumes': ('keyword_presence_proxy',),  # category 68
    'test_eks_workload_identity_role_is_scoped_to_expected_service_account': ('keyword_presence_proxy',),  # category 68
    'test_encore_runtime_image_process_runs_as_non_root_user': ('multi_stage_scope_error',),  # category 73
    'test_error_metrics_use_bounded_documented_error_code_cardinality': ('presence_not_cardinality',),  # category 34
    'test_every_database_check_constraint_has_direct_constraint_test_reference': ('existence_not_traceability',),  # category 91
    'test_every_database_foreign_key_has_orphan_prevention_test_reference': ('existence_not_traceability',),  # category 91
    'test_every_declared_production_gate_test_is_collected_on_linux_ci_runner': ('generic_presence_branch',),  # category 38
    'test_every_documented_domain_error_code_has_mapping_test_reference': ('any_not_every',),  # category 91
    'test_every_gametrade_settlement_intent_has_plan_construction_and_e2e_test_reference': ('implementation_presence_not_test_traceability',),  # category 91
    'test_every_idempotency_terminal_state_has_replay_test_reference': ('token_presence_proxy',),  # category 91
    'test_every_item_mutation_has_exactly_one_corresponding_ledger_entry': ('exactly_one_not_tested',),  # category 18
    'test_every_public_gateway_action_has_unit_integration_and_e2e_test_reference': ('implementation_presence_not_test_traceability',),  # category 91
    'test_every_pubsub_handler_has_duplicate_delivery_test_reference': ('global_duplicate_test_proxy',),  # category 91
    'test_every_required_verify_job_emits_ci_evidence_finish_record_even_after_failure': ('failure_path_not_proven',),  # category 98
    'test_every_required_verify_job_emits_ci_evidence_start_record': ('scope_overbroad',),  # category 98
    'test_every_wallet_mutation_has_exactly_one_corresponding_ledger_entry': ('exactly_one_not_tested',),  # category 18
    'test_existing_repository_test_names_are_never_silently_rewritten_in_observed_inventory': ('wrong_assertion',),  # category 92
    'test_failed_precondition_domain_failure_maps_to_nonretryable_gateway_response': ('retryability_not_tested',),  # category 87
    'test_generated_go_proto_sources_match_current_proto_descriptors': ('generation_equivalence_not_tested',),  # category 75
    'test_generated_rust_proto_sources_match_current_proto_descriptors': ('generation_equivalence_not_tested',),  # category 75
    'test_gke_cluster_control_plane_access_matches_declared_security_policy': ('keyword_presence_proxy',),  # category 69
    'test_gke_database_backup_retention_meets_configured_production_minimum': ('keyword_presence_proxy',),  # category 69
    'test_gke_database_deletion_protection_is_enabled_for_production_managed_database': ('keyword_presence_proxy',),  # category 69
    'test_gke_database_storage_encryption_is_enabled_when_database_is_managed_by_stack': ('keyword_presence_proxy',),  # category 69
    'test_gke_firewall_rules_do_not_expose_postgres_port_to_world': ('line_based_hcl_proxy',),  # category 69
    'test_gke_firewall_rules_do_not_expose_settlement_grpc_port_to_world': ('line_based_hcl_proxy',),  # category 69
    'test_gke_kubernetes_provider_uses_created_cluster_endpoint_and_ca_without_static_credentials': ('keyword_presence_proxy',),  # category 69
    'test_gke_nodes_use_encrypted_persistent_storage': ('keyword_presence_proxy',),  # category 69
    'test_gke_secrets_are_not_rendered_into_nonsensitive_terraform_outputs': ('keyword_presence_proxy',),  # category 69
    'test_gke_service_account_roles_grant_no_project_wide_owner_or_editor_role': ('keyword_presence_proxy',),  # category 69
    'test_gke_workload_identity_binding_is_scoped_to_expected_service_account': ('keyword_presence_proxy',),  # category 69
    'test_health_endpoint_does_not_report_ready_when_postgres_is_unreachable': ('static_presence_vs_runtime_behavior',),  # category 34
    'test_health_endpoint_does_not_report_ready_when_required_pubsub_is_unavailable': ('static_presence_vs_runtime_behavior',),  # category 34
    'test_high_cardinality_business_identifiers_are_not_used_as_metric_label_values': ('regex_incomplete',),  # category 34
    'test_idempotency_key_uniqueness_is_enforced_by_database': ('constraint_not_tested',),  # category 15
    'test_idempotency_lookup_uses_index_on_idempotency_key': ('index_existence_not_usage',),  # category 80
    'test_idempotency_record_is_written_in_same_transaction_as_business_mutations': ('atomicity_not_tested',),  # category 15
    'test_idempotency_success_record_cannot_exist_without_corresponding_committed_business_effect': ('atomicity_not_tested',),  # category 15
    'test_identical_interaction_id_from_different_principals_cannot_reuse_cached_response': ('confounded_authorization',),  # category 5
    'test_ignored_rust_advisory_dependency_is_absent_from_cargo_tree_for_runtime_target_and_all_enabled_features': ('allowlist_semantics_not_tested',),  # category 100
    'test_interaction_id_reuse_across_accept_and_cancel_is_rejected': ('confounded_payload',),  # category 5
    'test_interaction_id_reuse_across_issue_and_accept_is_rejected': ('confounded_payload',),  # category 5
    'test_invalid_argument_domain_failure_maps_to_nonretryable_gateway_response': ('retryability_not_tested',),  # category 87
    'test_ipv4_udp_request_reaches_gateway_and_returns_single_response_datagram': ('postcondition_incomplete', 'response_cardinality_not_checked'),  # second-pass semantic audit
    'test_issue_plan_created_by_service_is_market_service_identity': ('substring_proxy',),  # category 60
    'test_issue_rejects_item_stack_owned_by_different_authenticated_principal': ('wrong_security_boundary',),  # category 8
    'test_issue_rejects_source_stack_referencing_nonexistent_station': ('wrong_state_mutation',),  # category 8
    'test_issue_rejects_item_stack_with_wrong_item_type_claim': ('contradictory_contract',),  # category 8; retained verbatim and classified NON_APPLICABLE
    'test_issue_then_cancel_restores_original_item_state': ('partial_state',),  # category 33
    'test_issue_trade_without_expires_at_persists_null_expiration': ('vacuous_schema_guard',),  # category 58
    'test_issue_uses_authoritative_item_type_instead_of_client_claim': ('contradictory_contract',),  # category 8
    'test_item_escrow_lookup_for_trade_uses_index_on_trade_instance_id': ('index_existence_not_usage',),  # category 80
    'test_item_stack_ledger_delta_matches_actual_stack_delta': ('internal_consistency_only',),  # category 18
    'test_item_stack_ledger_records_exact_before_and_after_quantities': ('internal_consistency_only',),  # category 18
    'test_network_policy_allows_only_required_gateway_market_worker_settlement_dependency_edges': ('existence_only',),  # category 65
    'test_network_policy_allows_worker_to_settlement_and_denies_unrelated_ingress': ('existence_only',),  # category 35
    'test_network_policy_denies_unlisted_ingress_to_postgres': ('existence_only',),  # category 65
    'test_network_policy_denies_unlisted_ingress_to_trade_settlement': ('existence_only',),  # category 65
    'test_new_critical_vulnerability_fails_ci_unless_exact_advisory_has_explicit_active_exception': ('allowlist_semantics_not_tested',),  # category 100
    'test_no_terraform_output_exposes_database_password': ('name_stronger_than_logic',),  # category 67
    'test_no_terraform_output_exposes_hmac_secret': ('name_stronger_than_logic',),  # category 67
    'test_outbox_dispatch_query_uses_index_covering_unpublished_rows_and_claim_order': ('index_existence_not_usage',),  # category 80
    'test_pip_audit_scans_every_committed_runtime_and_test_requirements_file': ('coverage_not_enumerated',),  # category 100
    'test_postgres_service_is_not_exposed_through_public_load_balancer': ('vacuous_resource_lookup',),  # category 65
    'test_production_deploy_job_cannot_run_from_pull_request_event': ('trigger_not_checked',),  # category 99
    'test_production_gate_collects_at_least_one_test_from_every_required_risk_group': ('generic_presence_branch',),  # category 38
    'test_production_gate_fails_if_all_crash_recovery_tests_are_skipped': ('generic_presence_branch', 'source_string_presence'),  # category 38
    'test_production_gate_fails_if_all_load_tests_are_skipped': ('generic_presence_branch', 'source_string_presence'),  # category 38
    'test_production_gate_fails_if_all_security_tests_are_skipped': ('generic_presence_branch', 'source_string_presence'),  # category 38
    'test_production_gate_fails_if_pytest_collects_zero_tests': ('generic_presence_branch', 'source_string_presence'),  # category 38
    'test_production_gate_fails_when_any_required_risk_group_is_deselected': ('generic_presence_branch',),  # category 38
    'test_production_gate_fails_when_endpoint_resolves_to_local_stub_instead_of_deployed_service': ('generic_presence_branch',),  # category 38
    'test_production_gate_fails_when_required_environment_variable_contains_placeholder_value': ('generic_presence_branch',),  # category 38
    'test_production_gate_fails_when_required_external_dependency_is_stubbed': ('generic_presence_branch',),  # category 38
    'test_production_gate_fails_when_required_test_fixture_skips': ('generic_presence_branch',),  # category 38
    'test_proposed_test_names_using_concurrent_identify_the_shared_resource_or_race_boundary': ('heuristic_not_semantic',),  # category 92
    'test_proposed_test_names_using_invalid_identify_the_exact_invalid_property': ('heuristic_not_semantic',),  # category 92
    'test_proposed_test_names_using_reject_identify_the_specific_rejected_condition': ('heuristic_not_semantic',),  # category 92
    'test_proposed_test_names_using_timeout_identify_timeout_boundary_and_persistence_invariant': ('heuristic_not_semantic',),  # category 92
    'test_proto_field_reordering_does_not_change_semantics': ('unrelated_assertion',),  # category 29
    'test_protovalidate_rules_are_present_on_every_required_business_identifier': ('global_validation_token_proxy',),  # category 75
    'test_protovalidate_rules_require_nonnegative_or_positive_isk_fields_according_to_domain_contract': ('global_validation_token_proxy',),  # category 75
    'test_protovalidate_rules_require_positive_trade_quantity': ('global_validation_token_proxy',),  # category 75
    'test_provider_lockfile_contains_checksum_for_every_selected_provider_version': ('lockfile_nonempty_proxy',),  # category 71
    'test_provider_lockfile_does_not_contain_unreferenced_provider_after_dependency_removal': ('lockfile_nonempty_proxy',),  # category 71
    'test_provider_lockfile_is_identical_after_two_consecutive_providers_lock_runs': ('lockfile_nonempty_proxy',),  # category 71
    'test_public_error_message_does_not_include_filesystem_path': ('wrong_failure_path',),  # category 48
    'test_public_error_message_does_not_include_stack_trace': ('wrong_failure_path',),  # category 48
    'test_python_dependency_audit_covers_e2e_requirements': ('coverage_not_enumerated',),  # category 74
    'test_python_dependency_audit_covers_observability_runtime_and_test_requirements': ('coverage_not_enumerated',),  # category 74
    'test_python_dependency_audit_covers_simulator_runtime_requirements': ('coverage_not_enumerated',),  # category 74
    'test_python_dependency_audit_covers_simulator_test_requirements': ('coverage_not_enumerated',),  # category 74
    'test_python_requirement_files_contain_no_unbounded_direct_dependency_versions_for_ci_critical_tools': ('constraint_definition_wrong',),  # category 74
    'test_race_contract_manifest_resolves_each_declared_go_test_name_to_exactly_one_test_function': ('race_flag_presence_only',),  # category 38
    'test_race_contracts_fail_if_target_test_regex_matches_zero_tests': ('race_flag_presence_only',),  # category 38
    'test_random_domain_invalid_settlement_operation_sequence_never_creates_negative_item_quantity': ('wrong_generator',),  # category 32
    'test_random_domain_invalid_settlement_operation_sequence_never_creates_negative_wallet_balance': ('wrong_generator',),  # category 32
    'test_recommended_existing_test_rename_file_contains_old_and_new_name_for_every_suggested_rename': ('arrow_presence_only',),  # category 92
    'test_rendered_kubernetes_configures_readiness_probe_for_encore_backend': ('vacuous_container_name_filter',),  # category 35
    'test_rendered_kubernetes_configures_readiness_probe_for_trade_settlement': ('vacuous_container_name_filter',),  # category 35
    'test_rendered_kubernetes_configures_resource_requests_and_limits': ('half_assertion',),  # category 35
    'test_rendered_manifest_sha_matches_deployed_manifest_sha': ('sha_presence_not_equality',),  # category 38
    'test_replay_cache_is_scoped_by_authenticated_principal': ('confounded_authorization',),  # category 5
    'test_replay_conflict_maps_to_nonretryable_gateway_response': ('retryability_not_tested',),  # category 87
    'test_request_attempt_records_attempt_number_monotonically_increasing_from_one': ('start_at_one_not_tested',),  # category 96
    'test_reserved_removed_enum_numbers_are_not_reused': ('global_reserved_proxy',),  # category 75
    'test_reserved_removed_field_numbers_are_not_reused': ('global_reserved_proxy',),  # category 75
    'test_retry_after_lost_udp_success_response_returns_same_terminal_business_result': ('failure_not_injected',),  # category 86
    'test_retry_returns_original_audit_identity_without_creating_second_batch': ('audit_identity_not_compared',),  # category 82
    'test_runtime_image_declares_only_ports_used_by_runtime_service': ('only_existence_checked',),  # category 73
    'test_runtime_images_contain_no_build_time_cloud_credentials': ('dockerfile_text_only',),  # category 73
    'test_runtime_images_contain_no_hmac_secret_fixture_values': ('dockerfile_text_only',),  # category 73
    'test_runtime_images_contain_no_repository_git_directory': ('build_context_proxy',),  # category 73
    'test_runtime_images_do_not_include_private_ssh_keys': ('dockerfile_text_only',),  # category 73
    'test_same_issue_idempotency_key_generates_same_item_escrow_id_across_repeated_plan_construction': ('cache_confounded_determinism',),  # category 59
    'test_same_issue_idempotency_key_generates_same_trade_id_across_repeated_plan_construction': ('cache_confounded_determinism',),  # category 59
    'test_security_advisory_allowlist_entry_includes_advisory_id_dependency_justification_and_expiration_owner_metadata': ('allowlist_semantics_not_tested',),  # category 100
    'test_security_advisory_allowlist_fails_after_declared_expiration_date': ('allowlist_semantics_not_tested',),  # category 100
    'test_security_advisory_allowlist_rejects_wildcard_advisory_suppression': ('allowlist_semantics_not_tested',),  # category 100
    'test_seller_revenue_equals_sum_of_committed_accept_quantity_times_trade_unit_price_across_partial_fills': ('hardcoded_initial_state',),  # category 83
    'test_settlement_batch_id_is_globally_unique_across_concurrent_settlements': ('concurrency_not_exercised',),  # category 41
    'test_settlement_grpc_malformed_request_returns_status_without_process_panic': ('panic_not_proven',),  # category 79
    'test_settlement_grpc_rejects_empty_created_by_service': ('multiple_invalid_fields',),  # category 28
    'test_settlement_grpc_rejects_empty_external_request_id': ('multiple_invalid_fields',),  # category 28
    'test_settlement_grpc_rejects_empty_idempotency_key': ('multiple_invalid_fields',),  # category 28
    'test_settlement_grpc_rejects_unknown_intent': ('multiple_invalid_fields',),  # category 28
    'test_settlement_grpc_rejects_unspecified_intent': ('multiple_invalid_fields',),  # category 28
    'test_settlement_grpc_rejects_zero_caused_by_capsuleer_id': ('multiple_invalid_fields',),  # category 28
    'test_settlement_grpc_request_with_oversized_string_identifier_is_rejected_before_database_query': ('database_boundary_not_observed',),  # category 79
    'test_settlement_grpc_request_with_unknown_enum_returns_invalid_argument_without_process_panic': ('panic_not_proven',),  # category 79
    'test_settlement_readiness_does_not_report_ready_before_schema_is_compatible': ('static_presence_vs_runtime_behavior',),  # category 34
    'test_settlement_result_topic_name_matches_worker_publisher_and_market_subscriber_configuration': ('token_presence_not_equality',),  # category 77
    'test_settlement_service_is_not_publicly_exposed_unless_explicitly_intended': ('vacuous_resource_lookup',),  # category 35
    'test_settlement_step_ordinal_order_matches_operation_execution_order': ('wrong_relation',),  # category 97
    'test_settlement_step_ordinal_values_are_contiguous_and_start_at_one': ('start_at_one_not_tested',),  # category 97
    'test_settlement_transaction_atomically_commits_business_state_and_outbox_record': ('outbox_not_required',),  # category 19
    'test_settlement_work_topic_name_matches_market_publisher_and_worker_subscriber_configuration': ('token_presence_not_equality',),  # category 77
    'test_stale_processing_operation_recovery_query_uses_index_on_state_and_lease_expiration': ('index_existence_not_usage',),  # category 80
    'test_sum_of_item_stacks_and_active_item_escrows_equals_initial_total_items_after_thousand_random_trades': ('scale_and_randomness_missing',),  # category 83
    'test_sum_of_wallet_balances_and_active_wallet_escrows_equals_initial_total_isk_after_thousand_random_trades': ('scale_and_randomness_missing',),  # category 83
    'test_talos_omni_database_password_is_marked_sensitive_in_variables_and_outputs': ('keyword_presence_proxy',),  # category 70
    'test_talos_omni_destroy_preserves_external_database_when_external_database_mode_is_selected': ('keyword_presence_proxy',),  # category 70
    'test_talos_omni_external_database_mode_does_not_create_in_cluster_postgres_statefulset': ('keyword_presence_proxy',),  # category 70
    'test_talos_omni_external_database_mode_requires_nonempty_external_database_url': ('keyword_presence_proxy',),  # category 70
    'test_talos_omni_in_cluster_database_mode_creates_persistent_volume_claim_for_postgres': ('keyword_presence_proxy',),  # category 70
    'test_talos_omni_in_cluster_database_mode_does_not_expose_postgres_outside_cluster': ('keyword_presence_proxy',),  # category 70
    'test_talos_omni_in_cluster_database_mode_is_rejected_for_production_profile_when_policy_requires_external_database': ('keyword_presence_proxy',),  # category 70
    'test_talos_omni_in_cluster_database_mode_requires_nondefault_database_password': ('keyword_presence_proxy',),  # category 70
    'test_talos_omni_machine_configuration_does_not_embed_application_hmac_secret': ('keyword_presence_proxy',),  # category 70
    'test_terraform_ci_fails_when_provider_lockfile_changes_after_read_only_init': ('lockfile_nonempty_proxy',),  # category 71
    'test_terraform_ci_fails_when_providers_lock_changes_committed_checksums': ('lockfile_nonempty_proxy',),  # category 71
    'test_tests_do_not_depend_on_wall_clock_second_boundaries': ('narrow_pattern',),  # category 57
    'test_three_partial_fills_whose_quantities_sum_to_offer_quantity_complete_trade_exactly_once': ('exactly_once_not_tested',),  # category 62
    'test_trade_id_is_globally_unique_across_concurrent_issue_requests': ('concurrency_not_exercised',),  # category 41
    'test_trade_lookup_for_accept_uses_index_on_trade_instance_id': ('index_existence_not_usage',),  # category 80
    'test_trade_settlement_runtime_image_process_runs_as_non_root_user': ('multi_stage_scope_error',),  # category 73
    'test_trade_settlement_service_is_cluster_internal': ('vacuous_resource_lookup',),  # category 65
    'test_trade_state_change_history_matches_current_trade_state': ('weak_relation',),  # category 12
    'test_trivy_configuration_scan_includes_rendered_kubernetes_and_terraform_sources': ('scope_not_checked',),  # category 100
    'test_trivy_secret_scan_includes_terraform_kubernetes_workflow_and_source_directories': ('scope_not_checked',),  # category 100
    'test_two_full_accepts_and_cancel_have_exactly_one_terminal_outcome': ('missing_postcondition',),  # category 13
    'test_udp_edge_never_reflects_untrusted_payload_bytes_in_error_response': ('vacuous_pass',),  # category 3
    'test_udp_edge_rejects_empty_datagram_without_allocating_replay_entry': ('partial_assertion',),  # category 3
    'test_udp_edge_rejects_exponent_notation_for_integer_only_quantity': ('wrong_input',),  # category 3
    'test_udp_edge_rejects_fractional_quantity_even_when_fraction_is_mathematically_integral': ('wrong_input',),  # category 3
    'test_udp_edge_rejects_hmac_signed_with_different_key_id': ('confounded_input',),  # category 4
    'test_udp_edge_rejects_invalid_utf8_without_panicking': ('panic_not_proven',),  # category 3
    'test_udp_edge_rejects_overlong_utf8_encoding': ('panic_not_proven',),  # category 3
    'test_udp_edge_rejects_truncated_json_without_panicking': ('panic_not_proven',),  # category 3
    'test_udp_error_response_remains_within_single_datagram_size_limit': ('vacuous_pass',),  # category 3
    'test_udp_success_response_remains_within_single_datagram_size_limit': ('wrong_measurement',),  # category 3
    'test_validation_error_is_never_retried': ('retry_count_ambiguous',),  # category 43
    'test_wallet_escrow_is_bound_to_exact_trade_instance': ('vacuous_pass',),  # category 17
    'test_wallet_ledger_delta_matches_actual_wallet_delta': ('internal_consistency_only',),  # category 18
    'test_wallet_ledger_records_exact_before_and_after_balances': ('internal_consistency_only',),  # category 18
    'test_wallet_lookup_for_settlement_uses_index_on_wallet_id': ('index_existence_not_usage',),  # category 80
    'test_workflow_permissions_default_to_read_only_unless_individual_job_requires_write': ('job_override_not_checked',),  # category 99
    'test_zero_value_proto_fields_are_not_silently_reinterpreted_as_missing': ('global_text_proxy',),  # category 29
    'test_replay_cache_fingerprint_includes_authenticated_principal': ('principal_fingerprint_not_isolated',),  # second-pass semantic audit

    'test_udp_edge_does_not_reveal_whether_failure_was_unknown_key_id_or_wrong_secret': ('partial_information_disclosure_check',),  # second-pass semantic audit

    'test_issue_of_entire_source_stack_leaves_source_stack_quantity_zero_without_negative_quantity_or_orphaned_escrow': ('orphan_postcondition_not_checked',),  # second-pass semantic audit

    'test_concurrent_accepts_into_same_destination_stack_do_not_lose_updates': ('success_count_not_required', 'concurrency_not_exercised'),  # second-pass semantic audit

    'test_issue_rejects_nonexistent_seller': ('nonexistent_seller_not_isolated',),  # second-pass semantic audit

    'test_concurrent_identical_requests_execute_business_operation_exactly_once': ('concurrency_not_exercised',),  # second-pass semantic audit

    'test_concurrent_issues_from_same_item_stack_cannot_escrow_more_than_owned': ('concurrency_not_exercised',),  # second-pass semantic audit

    'test_partial_accept_and_cancel_race_conserves_items_and_isk': ('concurrency_not_exercised',),  # second-pass semantic audit

    'test_multiple_partial_accepts_and_cancel_race_conserves_items_and_isk': ('concurrency_not_exercised',),  # second-pass semantic audit

    'test_concurrent_insert_of_same_idempotency_key_executes_single_settlement': ('concurrency_not_exercised',),  # second-pass semantic audit

    'test_different_idempotency_keys_generate_different_trade_ids_for_same_trade_payload': ('payload_not_held_constant',),  # second-pass semantic audit

    'test_unknown_operation_enum_value_is_rejected_instead_of_mapping_to_zero_value_operation': ('wrong_enum_field_exercised',),  # second-pass semantic audit

    'test_every_declared_race_contract_targets_at_least_one_go_test': ('race_target_not_resolved',),  # second-pass semantic audit

    'test_postgres_search_path_is_fixed_for_runtime_role': ('search_path_not_bound_to_role',),  # second-pass semantic audit

    'test_security_definer_functions_set_safe_search_path_before_accessing_objects': ('security_definer_search_path_not_checked',),  # second-pass semantic audit

    'test_istio_and_gateway_api_production_overlays_preserve_same_resource_requests': ('overlay_comparison_not_performed',),  # second-pass semantic audit

    'test_istio_and_gateway_api_production_overlays_preserve_same_readiness_and_liveness_probes': ('overlay_comparison_not_performed',),  # second-pass semantic audit

    'test_govulncheck_scans_every_go_package_in_module': ('command_presence_only',),  # second-pass semantic audit

    'test_reordered_udp_datagrams_with_distinct_interaction_ids_are_processed_as_independent_requests': ('ordering_not_observed',),  # second-pass semantic audit

    'test_idempotency_record_principal_binding_cannot_be_changed_after_creation': ('mutation_not_attempted',),  # second-pass semantic audit

    'test_idempotency_record_request_fingerprint_cannot_be_changed_after_creation': ('mutation_not_attempted',),  # second-pass semantic audit

    'test_idempotency_record_terminal_response_cannot_be_overwritten_by_retry': ('terminal_response_not_compared',),  # second-pass semantic audit

    'test_database_queries_do_not_construct_table_or_column_names_from_untrusted_request_fields': ('regex_taint_proxy',),  # final implemented-route audit

    'test_rendered_kubernetes_does_not_embed_plaintext_secrets': ('unrendered_manifest_scope',),  # final implemented-route audit

    'test_nsqd_configuration_rejects_ephemeral_channel_name_for_settlement_results': ('configuration_rejection_not_exercised',),  # final implemented-route audit

    'test_nsqd_configuration_rejects_ephemeral_channel_name_for_settlement_work': ('configuration_rejection_not_exercised',),  # final implemented-route audit

}

SEMANTIC_EVIDENCE_OVERRIDES: frozenset[str] = frozenset(SEMANTIC_OVERRIDE_FINDINGS)
