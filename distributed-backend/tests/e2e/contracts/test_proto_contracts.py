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
