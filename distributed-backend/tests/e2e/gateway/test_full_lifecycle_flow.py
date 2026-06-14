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
    game_accept_ui_activity,
    game_cancel_ui_activity,
    issue_command,
)
from helpers.grpc_clients import single_gateway_response, single_settlement_response


pytestmark = [pytest.mark.live, pytest.mark.gateway]


def test_gateway_accept_activity_settles_open_trade_end_to_end(
    proto_modules, trade_db, settlement_target, gateway_target
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

    activity = game_accept_ui_activity(proto, world, ids, selected_quantity=5)
    result = single_gateway_response(proto, gateway_target, activity)

    assert result.activity_id.value == activity.activity_id.value
    assert (
        result.result_status
        == proto.gateway_activity.GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_ACCEPTED
    )
    assert result.player_safe_trade_reference.value == ids.trade_instance_id
    assert_trade_state(trade_db, ids.trade_instance_id, state="completed", remaining=0)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_value_conservation(trade_db, ids, world)


def test_gateway_cancel_activity_cancels_open_trade_end_to_end(
    proto_modules, trade_db, settlement_target, gateway_target
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

    activity = game_cancel_ui_activity(proto, world, ids)
    result = single_gateway_response(proto, gateway_target, activity)

    assert result.activity_id.value == activity.activity_id.value
    assert (
        result.result_status
        == proto.gateway_activity.GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_ACCEPTED
    )
    assert result.player_safe_trade_reference.value == ids.trade_instance_id
    assert_trade_state(trade_db, ids.trade_instance_id, state="cancelled")
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_value_conservation(trade_db, ids, world)
