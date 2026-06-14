from __future__ import annotations

import pytest

from helpers.assertions import (
    assert_escrow_quantity,
    assert_result_committed,
    assert_trade_state,
    assert_value_conservation,
)
from helpers.builders import (
    TradeScenarioIds,
    issue_command,
    project_accept_interaction,
    project_cancel_interaction,
)
from helpers.grpc_clients import single_market_response, single_settlement_response


pytestmark = [pytest.mark.live, pytest.mark.market]


def test_market_accept_interaction_settles_open_trade(
    proto_modules, trade_db, settlement_target, market_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    assert_result_committed(
        proto,
        single_settlement_response(
            proto,
            settlement_target,
            issue_command(proto, ids, world, total_quantity=5),
        ),
        expected_oneof="issue_trade_instance",
    )

    interaction = project_accept_interaction(proto, world, ids, selected_quantity=5)
    result = single_market_response(proto, market_target, interaction)

    assert result.interaction_id.value == interaction.interaction_id.value
    assert result.decision.required_operation_kind == (
        proto.operation_kind.TRADE_OPERATION_KIND_SETTLE_TRADE_INSTANCE
    )
    assert result.decision.WhichOneof("required_operation") == "settle_trade_instance"
    assert_result_committed(
        proto,
        result.settlement_result,
        expected_oneof="settle_trade_instance",
    )
    assert_trade_state(trade_db, ids.trade_instance_id, state="completed", remaining=0)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_value_conservation(trade_db, ids, world)


def test_market_cancel_interaction_cancels_open_trade(
    proto_modules, trade_db, settlement_target, market_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    assert_result_committed(
        proto,
        single_settlement_response(
            proto,
            settlement_target,
            issue_command(proto, ids, world, total_quantity=5),
        ),
        expected_oneof="issue_trade_instance",
    )

    interaction = project_cancel_interaction(proto, world, ids)
    result = single_market_response(proto, market_target, interaction)

    assert result.interaction_id.value == interaction.interaction_id.value
    assert result.decision.required_operation_kind == (
        proto.operation_kind.TRADE_OPERATION_KIND_CANCEL_TRADE_INSTANCE
    )
    assert result.decision.WhichOneof("required_operation") == "cancel_trade_instance"
    assert_result_committed(
        proto,
        result.settlement_result,
        expected_oneof="cancel_trade_instance",
    )
    assert_trade_state(trade_db, ids.trade_instance_id, state="cancelled")
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_value_conservation(trade_db, ids, world)
