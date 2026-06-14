from __future__ import annotations

from helpers.paths import PROTO_ROOT


def test_proto_tree_is_present() -> None:
    expected_files = [
        "eve_trade/common/v1/identity.proto",
        "eve_trade/domain/trade/v1/trade_instance.proto",
        "eve_trade/operation/v1/issue_trade_instance.proto",
        "eve_trade/gateway/v1/game_trade_gateway_service.proto",
        "eve_trade/market/v1/market_trade_service.proto",
        "eve_trade/settlement/v1/trade_settlement_service.proto",
    ]

    for relative_path in expected_files:
        assert (PROTO_ROOT / relative_path).is_file()


def test_proto_stubs_compile_and_expose_all_service_boundaries(proto_modules) -> None:
    proto = proto_modules

    assert hasattr(proto.gateway_service_grpc, "GameTradeGatewayServiceStub")
    assert hasattr(proto.market_service_grpc, "MarketTradeServiceStub")
    assert hasattr(proto.settlement_service_grpc, "TradeSettlementServiceStub")

    settlement_command_oneof = proto.settlement_command.TradeSettlementCommand.DESCRIPTOR.oneofs_by_name[
        "command"
    ]
    assert {
        field.name for field in settlement_command_oneof.fields
    } == {
        "issue_trade_instance",
        "settle_trade_instance",
        "cancel_trade_instance",
        "expire_trade_instance",
    }


def test_operation_messages_follow_row_ids_and_terms_contract(proto_modules) -> None:
    proto = proto_modules

    assert "row_ids" in proto.issue.IssueTradeInstanceCommand.DESCRIPTOR.fields_by_name
    assert "terms" in proto.issue.IssueTradeInstanceCommand.DESCRIPTOR.fields_by_name
    assert "row_ids" in proto.settle.SettleTradeInstanceCommand.DESCRIPTOR.fields_by_name
    assert "terms" in proto.settle.SettleTradeInstanceCommand.DESCRIPTOR.fields_by_name
    assert "accepted_trade" in proto.settle.SettleTradeInstanceCommand.DESCRIPTOR.fields_by_name


def test_gateway_keeps_game_ui_input_as_raw_activity(proto_modules) -> None:
    fields = proto_modules.gateway_activity.GameTradeUiActivity.DESCRIPTOR.fields_by_name

    assert "visible_fields" in fields
    assert "raw_game_screen_name" in fields
    assert "raw_game_button_name" in fields
    assert "selected_item_stack_id" not in fields


def test_core_message_field_numbers_are_stable(proto_modules) -> None:
    proto = proto_modules

    assert_field_numbers(
        proto.metadata.OperationMetadata,
        {
            "operation_id": 1,
            "request_id": 2,
            "idempotency_key": 3,
            "correlation_id": 4,
            "trace_id": 5,
            "source_system": 6,
            "external_operation_id": 7,
            "caused_by_capsuleer_id": 8,
            "created_by_service": 9,
            "requested_at_unix_millis": 10,
        },
    )
    assert_field_numbers(
        proto.settlement_command.TradeSettlementCommand,
        {
            "metadata": 1,
            "operation_kind": 2,
            "issue_trade_instance": 10,
            "settle_trade_instance": 11,
            "cancel_trade_instance": 12,
            "expire_trade_instance": 13,
        },
    )
    assert_field_numbers(
        proto.settlement_result.TradeSettlementResult,
        {
            "metadata": 1,
            "operation_kind": 2,
            "attempt_status": 3,
            "trade_instance_id": 4,
            "trade_transaction_id": 5,
            "settlement_id": 6,
            "resulting_trade_state": 7,
            "settlement_steps": 8,
            "issue_trade_instance": 20,
            "settle_trade_instance": 21,
            "cancel_trade_instance": 22,
            "expire_trade_instance": 23,
            "rejected": 30,
            "rolled_back": 31,
            "result_unknown": 32,
        },
    )
    assert_field_numbers(
        proto.market_decision.TradeDecision,
        {
            "source_interaction_id": 1,
            "correlation_id": 2,
            "required_operation_kind": 3,
            "source_interaction": 4,
            "issue_trade_instance": 10,
            "settle_trade_instance": 11,
            "cancel_trade_instance": 12,
            "expire_trade_instance": 13,
        },
    )
    assert_field_numbers(
        proto.market_decision.MarketTradeResult,
        {
            "interaction_id": 1,
            "correlation_id": 2,
            "decision": 3,
            "settlement_result": 4,
            "error": 5,
        },
    )
    assert_field_numbers(
        proto.gateway_activity.GameTradeUiActivity,
        {
            "activity_id": 1,
            "game_server_id": 2,
            "game_session_id": 3,
            "capsuleer_id": 4,
            "game_ui_version": 5,
            "activity_kind": 6,
            "raw_game_screen_name": 7,
            "raw_game_button_name": 8,
            "visible_fields": 9,
            "occurred_at_unix_millis": 10,
        },
    )
    assert_field_numbers(
        proto.gateway_activity.GameTradeUiActivityResult,
        {
            "activity_id": 1,
            "correlation_id": 2,
            "result_status": 3,
            "player_safe_message": 4,
            "player_safe_trade_reference": 5,
        },
    )


def test_lifecycle_enum_values_are_stable(proto_modules) -> None:
    proto = proto_modules

    assert_enum_values(
        proto.operation_kind.TradeOperationKind.DESCRIPTOR,
        {
            "TRADE_OPERATION_KIND_UNSPECIFIED": 0,
            "TRADE_OPERATION_KIND_ISSUE_TRADE_INSTANCE": 1,
            "TRADE_OPERATION_KIND_ACCEPT_TRADE_INSTANCE": 2,
            "TRADE_OPERATION_KIND_CANCEL_TRADE_INSTANCE": 3,
            "TRADE_OPERATION_KIND_EXPIRE_TRADE_INSTANCE": 4,
            "TRADE_OPERATION_KIND_SETTLE_TRADE_INSTANCE": 5,
        },
    )
    assert_enum_values(
        proto.settlement_result.TransactionAttemptStatus.DESCRIPTOR,
        {
            "TRANSACTION_ATTEMPT_STATUS_UNSPECIFIED": 0,
            "TRANSACTION_ATTEMPT_STATUS_COMMITTED": 1,
            "TRANSACTION_ATTEMPT_STATUS_REJECTED": 2,
            "TRANSACTION_ATTEMPT_STATUS_ROLLED_BACK": 3,
            "TRANSACTION_ATTEMPT_STATUS_RESULT_UNKNOWN": 4,
            "TRANSACTION_ATTEMPT_STATUS_IDEMPOTENT_REPLAY": 5,
        },
    )
    assert_enum_values(
        proto.trade_state.TradeState.DESCRIPTOR,
        {
            "TRADE_STATE_UNSPECIFIED": 0,
            "TRADE_STATE_OUTSTANDING": 1,
            "TRADE_STATE_COMPLETED": 2,
            "TRADE_STATE_FAILED": 3,
            "TRADE_STATE_EXPIRED": 4,
            "TRADE_STATE_CANCELLED": 5,
        },
    )
    assert_enum_values(
        proto.errors.ErrorCode.DESCRIPTOR,
        {
            "ERROR_CODE_UNSPECIFIED": 0,
            "ERROR_CODE_VALIDATION_FAILED": 1,
            "ERROR_CODE_NOT_FOUND": 2,
            "ERROR_CODE_ALREADY_EXISTS": 3,
            "ERROR_CODE_FAILED_PRECONDITION": 4,
            "ERROR_CODE_PERMISSION_DENIED": 5,
            "ERROR_CODE_CONFLICT": 6,
            "ERROR_CODE_INTERNAL": 7,
            "ERROR_CODE_UNAVAILABLE": 8,
            "ERROR_CODE_RESULT_UNKNOWN": 9,
        },
    )


def assert_field_numbers(message_type, expected: dict[str, int]) -> None:
    actual = {
        field.name: field.number
        for field in message_type.DESCRIPTOR.fields
        if field.name in expected
    }
    assert actual == expected


def assert_enum_values(enum_descriptor, expected: dict[str, int]) -> None:
    actual = {
        value.name: value.number
        for value in enum_descriptor.values
        if value.name in expected
    }
    assert actual == expected
