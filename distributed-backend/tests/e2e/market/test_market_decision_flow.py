from __future__ import annotations

import pytest

from helpers.builders import TradeScenarioIds, project_trade_interaction
from helpers.grpc_clients import single_market_response


pytestmark = [pytest.mark.live, pytest.mark.market]


def test_market_projects_issue_interaction_into_settlement_decision(
    proto_modules, trade_db, market_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    interaction = project_trade_interaction(proto, world, selected_quantity=5)

    result = single_market_response(proto, market_target, interaction)

    assert result.interaction_id.value == interaction.interaction_id.value
    assert result.decision.required_operation_kind == (
        proto.operation_kind.TRADE_OPERATION_KIND_ISSUE_TRADE_INSTANCE
    )
    assert result.decision.WhichOneof("required_operation") == "issue_trade_instance"
    assert result.settlement_result.attempt_status in {
        proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_COMMITTED,
        proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_REJECTED,
        proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_RESULT_UNKNOWN,
    }
