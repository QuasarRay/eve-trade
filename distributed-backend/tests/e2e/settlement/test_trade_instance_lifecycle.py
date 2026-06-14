from __future__ import annotations

from decimal import Decimal

import pytest

from helpers.assertions import (
    assert_escrow_quantity,
    assert_issue_audit_complete,
    assert_item_stack_quantity,
    assert_result_committed,
    assert_settlement_audit_complete,
    assert_trade_state,
    assert_value_conservation,
    assert_wallet_amount,
)
from helpers.builders import TradeScenarioIds, issue_command, settle_command
from helpers.grpc_clients import single_settlement_response


pytestmark = [pytest.mark.live, pytest.mark.settlement]


def test_trade_instance_issue_partial_settle_and_complete(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )

    issue = issue_command(proto, ids, world, total_quantity=5, unit_price_minor=10_000)
    issue_result = single_settlement_response(proto, settlement_target, issue)

    assert_result_committed(proto, issue_result, expected_oneof="issue_trade_instance")
    assert issue_result.trade_instance_id.value == ids.trade_instance_id
    assert_trade_state(trade_db, ids.trade_instance_id, state="outstanding", remaining=5)
    assert_item_stack_quantity(trade_db, ids.issuer_item_stack_id, 5)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 5)
    assert_issue_audit_complete(trade_db, ids, issue, quantity=5)
    assert_value_conservation(trade_db, ids, world)

    first_settle = settle_command(proto, ids, world, quantity=2, unit_price_minor=10_000)
    first_settle_result = single_settlement_response(
        proto,
        settlement_target,
        first_settle,
    )

    assert_result_committed(proto, first_settle_result, expected_oneof="settle_trade_instance")
    assert_trade_state(trade_db, ids.trade_instance_id, state="outstanding", remaining=3)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 3)
    assert_item_stack_quantity(trade_db, ids.buyer_destination_stack_id, 2)
    assert_wallet_amount(
        trade_db,
        ids.buyer_wallet_id,
        world.initial_buyer_wallet_major - Decimal("200.00"),
    )
    assert_wallet_amount(
        trade_db,
        ids.issuer_wallet_id,
        world.initial_issuer_wallet_major + Decimal("200.00"),
    )
    assert_settlement_audit_complete(
        trade_db,
        ids,
        first_settle,
        quantity=2,
        unit_price_minor=10_000,
        resulting_state="outstanding",
    )
    assert_value_conservation(trade_db, ids, world)

    ids.transaction_id = TradeScenarioIds().transaction_id
    ids.settlement_id = TradeScenarioIds().settlement_id
    final_settle = settle_command(proto, ids, world, quantity=3, unit_price_minor=10_000)
    final_settle_result = single_settlement_response(
        proto,
        settlement_target,
        final_settle,
    )

    assert_result_committed(proto, final_settle_result, expected_oneof="settle_trade_instance")
    assert_trade_state(trade_db, ids.trade_instance_id, state="completed", remaining=0)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_item_stack_quantity(trade_db, ids.buyer_destination_stack_id, 5)
    assert_wallet_amount(
        trade_db,
        ids.buyer_wallet_id,
        world.initial_buyer_wallet_major - Decimal("500.00"),
    )
    assert_wallet_amount(
        trade_db,
        ids.issuer_wallet_id,
        world.initial_issuer_wallet_major + Decimal("500.00"),
    )
    assert_settlement_audit_complete(
        trade_db,
        ids,
        final_settle,
        quantity=3,
        unit_price_minor=10_000,
        resulting_state="completed",
    )
    assert_value_conservation(trade_db, ids, world)
