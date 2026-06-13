from __future__ import annotations

import pytest

from helpers.assertions import (
    assert_escrow_quantity,
    assert_item_stack_quantity,
    assert_result_committed,
    assert_trade_state,
)
from helpers.builders import TradeScenarioIds, cancel_command, expire_command, issue_command
from helpers.grpc_clients import single_settlement_response


pytestmark = [pytest.mark.live, pytest.mark.settlement]


def test_cancel_releases_open_trade_back_to_issuer_stack(
    proto_modules, trade_db, settlement_target
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
    )

    cancel_result = single_settlement_response(
        proto,
        settlement_target,
        cancel_command(proto, ids, world),
    )

    assert_result_committed(proto, cancel_result)
    assert_trade_state(trade_db, ids.trade_instance_id, state="cancelled", remaining=0)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_item_stack_quantity(
        trade_db,
        ids.issuer_item_stack_id,
        world.initial_source_quantity,
    )


def test_expire_releases_open_trade_back_to_issuer_stack(
    proto_modules, trade_db, settlement_target
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
    )

    expire_result = single_settlement_response(
        proto,
        settlement_target,
        expire_command(proto, ids, world),
    )

    assert_result_committed(proto, expire_result)
    assert_trade_state(trade_db, ids.trade_instance_id, state="expired", remaining=0)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_item_stack_quantity(
        trade_db,
        ids.issuer_item_stack_id,
        world.initial_source_quantity,
    )
