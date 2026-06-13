from __future__ import annotations

import pytest

from helpers.assertions import (
    assert_escrow_quantity,
    assert_item_stack_quantity,
    assert_result_committed,
    assert_result_rejected,
    assert_result_replayed,
)
from helpers.builders import (
    TradeScenarioIds,
    issue_command,
    operation_metadata,
    with_idempotency_key,
)
from helpers.grpc_clients import single_settlement_response


pytestmark = [pytest.mark.live, pytest.mark.settlement]


def test_duplicate_issue_request_is_idempotent_replay(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    command = issue_command(proto, ids, world)

    first = single_settlement_response(proto, settlement_target, command)
    second = single_settlement_response(proto, settlement_target, command)

    assert_result_committed(proto, first)
    assert_result_replayed(proto, second)
    assert_item_stack_quantity(trade_db, ids.issuer_item_stack_id, 5)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 5)


def test_same_idempotency_key_with_different_request_is_rejected(
    proto_modules, trade_db, settlement_target
) -> None:
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
        purpose="idempotency-conflict",
    )
    idempotency_key = metadata.idempotency_key.value

    first = issue_command(proto, ids, world, metadata=metadata, total_quantity=5)
    second_ids = TradeScenarioIds(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    second_metadata = with_idempotency_key(
        proto,
        operation_metadata(
            proto,
            caused_by_capsuleer_id=world.issuer_id,
            purpose="idempotency-conflict",
        ),
        idempotency_key,
    )
    second = issue_command(
        proto,
        second_ids,
        world,
        metadata=second_metadata,
        total_quantity=4,
    )

    assert_result_committed(proto, single_settlement_response(proto, settlement_target, first))
    conflict = single_settlement_response(proto, settlement_target, second)

    assert_result_rejected(proto, conflict)
    assert conflict.rejected.error.code == proto.errors.ERROR_CODE_CONFLICT
