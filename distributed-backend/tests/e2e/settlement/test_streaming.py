from __future__ import annotations

import pytest

from helpers.assertions import (
    assert_escrow_quantity,
    assert_item_stack_quantity,
    assert_result_committed,
    assert_trade_state,
    assert_value_conservation,
)
from helpers.builders import TradeScenarioIds, issue_command, settle_command
from helpers.grpc_clients import settlement_stream_responses


pytestmark = [pytest.mark.live, pytest.mark.settlement]


def test_settlement_stream_processes_multiple_commands_in_order(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )

    issue = issue_command(proto, ids, world, total_quantity=5)
    first_settle = settle_command(proto, ids, world, quantity=2)
    first_transaction_id = ids.transaction_id

    ids.transaction_id = TradeScenarioIds().transaction_id
    ids.settlement_id = TradeScenarioIds().settlement_id
    final_settle = settle_command(proto, ids, world, quantity=3)
    final_transaction_id = ids.transaction_id

    responses = settlement_stream_responses(
        proto,
        settlement_target,
        [issue, first_settle, final_settle],
    )

    assert len(responses) == 3
    assert_result_committed(proto, responses[0], expected_oneof="issue_trade_instance")
    assert responses[0].trade_instance_id.value == ids.trade_instance_id
    assert_result_committed(proto, responses[1], expected_oneof="settle_trade_instance")
    assert responses[1].trade_transaction_id.value == first_transaction_id
    assert_result_committed(proto, responses[2], expected_oneof="settle_trade_instance")
    assert responses[2].trade_transaction_id.value == final_transaction_id

    assert_trade_state(trade_db, ids.trade_instance_id, state="completed", remaining=0)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_item_stack_quantity(trade_db, ids.buyer_destination_stack_id, 5)
    assert_value_conservation(trade_db, ids, world)
