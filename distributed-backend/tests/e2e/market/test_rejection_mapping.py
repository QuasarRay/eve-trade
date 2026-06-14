from __future__ import annotations

import pytest

from helpers.builders import TradeScenarioIds, project_accept_interaction
from helpers.grpc_clients import single_market_response


pytestmark = [pytest.mark.live, pytest.mark.market]


def test_market_invalid_interaction_returns_structured_error_without_settlement(
    proto_modules, trade_db, market_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    interaction = project_accept_interaction(proto, world, ids, selected_quantity=1)
    interaction.visible_trade_context.trade_instance_id.value = ""

    result = single_market_response(proto, market_target, interaction)

    assert result.interaction_id.value == interaction.interaction_id.value
    assert result.error.code == proto.errors.ERROR_CODE_VALIDATION_FAILED
    assert result.settlement_result.attempt_status == (
        proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_UNSPECIFIED
    )
    assert (
        trade_db.table_count(
            "trade_transaction",
            "trade_instance_id = %s",
            (ids.trade_instance_id,),
        )
        == 0
    )
