from __future__ import annotations

import pytest

from helpers.assertions import (
    assert_item_stack_quantity,
    assert_result_committed,
    assert_result_rejected,
    assert_trade_state,
)
from helpers.builders import (
    TradeScenarioIds,
    issue_command,
    operation_metadata,
    settle_command,
)
from helpers.grpc_clients import single_settlement_response


pytestmark = [pytest.mark.live, pytest.mark.settlement]


def test_empty_settlement_command_is_rejected(proto_modules, settlement_target) -> None:
    proto = proto_modules
    command = proto.settlement_command.TradeSettlementCommand()

    result = single_settlement_response(proto, settlement_target, command)

    assert_result_rejected(proto, result)


def test_issue_with_insufficient_items_is_rejected_without_debiting_source_stack(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
        source_quantity=3,
    )

    result = single_settlement_response(
        proto,
        settlement_target,
        issue_command(proto, ids, world, total_quantity=5),
    )

    assert_result_rejected(proto, result)
    assert_item_stack_quantity(trade_db, ids.issuer_item_stack_id, 3)


def test_settle_with_price_mismatch_is_rejected_and_trade_remains_open(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    issue_result = single_settlement_response(
        proto,
        settlement_target,
        issue_command(proto, ids, world, total_quantity=5, unit_price_minor=10_000),
    )
    assert_result_committed(proto, issue_result)

    rejected = single_settlement_response(
        proto,
        settlement_target,
        settle_command(proto, ids, world, quantity=1, unit_price_minor=9_999),
    )

    assert_result_rejected(proto, rejected)
    assert_trade_state(trade_db, ids.trade_instance_id, state="outstanding", remaining=5)


def test_issue_with_invalid_metadata_is_rejected(proto_modules, trade_db, settlement_target) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    metadata = operation_metadata(
        proto,
        caused_by_capsuleer_id=world.issuer_id,
        purpose="invalid-metadata",
    )
    metadata.operation_id.value = "not-a-uuid"

    result = single_settlement_response(
        proto,
        settlement_target,
        issue_command(proto, ids, world, metadata=metadata),
    )

    assert_result_rejected(proto, result)
