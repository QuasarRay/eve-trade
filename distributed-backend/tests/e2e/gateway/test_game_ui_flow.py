from __future__ import annotations

import pytest

from helpers.builders import TradeScenarioIds, game_trade_ui_activity
from helpers.grpc_clients import single_gateway_response


pytestmark = [pytest.mark.live, pytest.mark.gateway]


def test_gateway_translates_game_ui_activity_and_returns_player_safe_result(
    proto_modules, trade_db, gateway_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    activity = game_trade_ui_activity(proto, world, selected_quantity=5)

    result = single_gateway_response(proto, gateway_target, activity)

    assert result.activity_id.value == activity.activity_id.value
    assert result.result_status in {
        proto.gateway_activity.GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_ACCEPTED,
        proto.gateway_activity.GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_REJECTED,
        proto.gateway_activity.GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_RESULT_UNKNOWN,
    }
    assert result.player_safe_message
