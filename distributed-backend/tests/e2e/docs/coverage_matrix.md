# Eve Trade E2E Coverage Matrix

This matrix is the authoritative e2e map for the canonical lifecycle in
`Architecture/Trade Request Lifecycle/v1.md`. Every lifecycle `@` step must be
listed here and tied to at least one test file. Do not delete or weaken a test
named here unless the same lifecycle coverage is replaced in the same change.

| Lifecycle step | E2E coverage |
| --- | --- |
| `@API-gateway receives GameUI activity(Issue) from Game Server via gRPC` | `gateway/test_game_ui_flow.py`, `gateway/test_full_lifecycle_flow.py` |
| `@API-gateway translates GameUI activities to Project Proto contract` | `gateway/test_game_ui_flow.py`, `gateway/test_full_lifecycle_flow.py`, `gateway/test_rejection_mapping.py` |
| `@Market_Layer_1_receives_Project_Proto_trade_interactions_from_API_gateway_via_gRPC` | `market/test_market_decision_flow.py`, `market/test_full_lifecycle_flow.py`, `market/test_rejection_mapping.py` |
| `@Market_Layer_1_converts_Project_Proto_contract_into_game_trade_domain_input_while_shielding_Layer_2_from_gRPC_web_and_DevOps_details` | `market/test_market_decision_flow.py`, `market/test_rejection_mapping.py` |
| `@Market_Layer_2_determines_required_transaction_function_name(issue_trade_instance)_based_on_trade_instance_absent_derived_trade_type_game_mechanics_and_player_interaction` | `market/test_market_decision_flow.py`, `market/test_full_lifecycle_flow.py` |
| `@Market_Layer_2_writes_required_transaction_function_name(issue_trade_instance)_and_required_row_identities_into_request_metadata` | `market/test_market_decision_flow.py`, `contracts/test_proto_contracts.py` |
| `@Market_Layer_3_sends_issue_trade_instance_request_to_trade_settlement_via_gRPC_using_the_same_transaction_function_name_chosen_by_Layer_2` | `market/test_market_decision_flow.py`, `market/test_full_lifecycle_flow.py` |
| `@trade_settlement_receives_transaction_request_via_gRPC_validates_metadata_reads_requested_transaction_function_name_and_dispatches_to_same_named_internal_transaction_function` | `settlement/test_trade_instance_lifecycle.py`, `settlement/test_rejections.py`, `settlement/test_streaming.py` |
| `@issue_trade_instance_decides_affected_tables_and_columns_from_its_own_implementation_uses_metadata_identities_to_find_rows_and_writes_trade_instance_creation_in_one_database_transaction` | `settlement/test_trade_instance_lifecycle.py`, `settlement/test_idempotency.py` |
| `@Database_commits_or_rolls_back_the_database_transaction_requested_by_trade_settlement` | `settlement/test_trade_instance_lifecycle.py`, `settlement/test_rejections.py`, `settlement/test_concurrency.py` |
| `@trade_settlement_receives_database_result_and_returns_settlement_response_to_market(OUTSTANDING)` | `settlement/test_trade_instance_lifecycle.py`, `market/test_market_decision_flow.py` |
| `@market responds back to api-gateway` | `gateway/test_game_ui_flow.py`, `gateway/test_full_lifecycle_flow.py` |
| `@api-gateway responds back to game server` | `gateway/test_game_ui_flow.py`, `gateway/test_full_lifecycle_flow.py`, `gateway/test_rejection_mapping.py` |
| `@API-gateway receives GameUI activity(accept) from Game Server via gRPC` | `gateway/test_full_lifecycle_flow.py` |
| `@Market_Layer_2_determines_required_transaction_function_name(settle_trade_instance)_based_on_existing_trade_instance_derived_trade_type_game_mechanics_and_TradeInstance_TradeState(OUTSTANDING)` | `market/test_full_lifecycle_flow.py`, `settlement/test_trade_instance_lifecycle.py` |
| `@Market_Layer_2_writes_required_transaction_function_name(settle_trade_instance)_and_required_row_identities_into_request_metadata` | `market/test_full_lifecycle_flow.py`, `contracts/test_proto_contracts.py` |
| `@Market_Layer_3_sends_settle_trade_instance_request_to_trade_settlement_via_gRPC_using_the_same_transaction_function_name_chosen_by_Layer_2` | `market/test_full_lifecycle_flow.py`, `settlement/test_trade_instance_lifecycle.py` |
| `@settle_trade_instance_independently_rejects_or_expires_expired_trade_instances_even_if_market_requested_settlement` | `settlement/test_terminal_lifecycle.py`, `settlement/test_rejections.py` |
| `@settle_trade_instance_decides_affected_tables_and_columns_from_its_own_implementation_uses_metadata_identities_to_find_rows_and_writes_ownership_wallet_reservation_and_trade_state_changes_in_one_database_transaction` | `settlement/test_trade_instance_lifecycle.py`, `settlement/test_concurrency.py` |
| `@trade_settlement_receives_database_result_and_returns_settlement_response_to_market` | `settlement/test_trade_instance_lifecycle.py`, `settlement/test_streaming.py` |
| `@API-gateway receives GameUI activity(cancel) from Game Server via gRPC` | `gateway/test_full_lifecycle_flow.py` |
| `@Market_Layer_2_determines_required_transaction_function_name(cancel_trade_instance)_based_on_existing_trade_instance_derived_trade_type_game_mechanics_and_TradeInstance_TradeState(OUTSTANDING)` | `market/test_full_lifecycle_flow.py`, `settlement/test_terminal_lifecycle.py` |
| `@Market_Layer_2_writes_required_transaction_function_name(cancel_trade_instance)_and_required_row_identities_into_request_metadata` | `market/test_full_lifecycle_flow.py`, `contracts/test_proto_contracts.py` |
| `@Market_Layer_3_sends_cancel_trade_instance_request_to_trade_settlement_via_gRPC_using_the_same_transaction_function_name_chosen_by_Layer_2` | `market/test_full_lifecycle_flow.py`, `settlement/test_terminal_lifecycle.py` |
| `@cancel_trade_instance_decides_affected_tables_and_columns_from_its_own_implementation_uses_metadata_identities_to_find_rows_and_writes_cancellation_and_reservation_release_in_one_database_transaction` | `settlement/test_terminal_lifecycle.py`, `settlement/test_idempotency.py`, `settlement/test_concurrency.py` |

## Boundary Rules

- Gateway tests may assert GameUI translation, public player-safe response shape,
  and absence of leaked lower-layer details. They must not assert market rule
  internals except through observable response and database state.
- Market tests may assert lifecycle operation selection and settlement command
  effects. They must not depend on raw GameUI field names except through
  `ProjectTradeInteraction`.
- Settlement tests are the source for database mutation, accounting,
  idempotency, concurrency, and rollback/no-mutation invariants.
- Contract tests protect proto and static architecture compatibility.
- Chaos and restart behavior belongs in Litmus/Dagger chaos jobs; e2e tests may
  only assert the steady-state contract unless a deterministic fault hook exists.
