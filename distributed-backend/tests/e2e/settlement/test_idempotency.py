from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from helpers.assertions import (
    assert_escrow_quantity,
    assert_item_stack_quantity,
    assert_no_trade_side_effects,
    assert_result_committed,
    assert_result_rejected,
    assert_result_replayed,
    assert_single_trade_side_effects,
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

    assert_result_committed(proto, first, expected_oneof="issue_trade_instance")
    assert_result_replayed(
        proto,
        second,
        original=first,
        expected_oneof="issue_trade_instance",
    )
    assert_item_stack_quantity(trade_db, ids.issuer_item_stack_id, 5)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 5)
    assert_single_trade_side_effects(trade_db, ids, settlement_count=0)
    assert (
        trade_db.table_count(
            "operation", "idempotency_key = %s", (command.metadata.idempotency_key.value,)
        )
        == 1
    )
    assert (
        trade_db.table_count(
            "idempotency_result",
            "idempotency_key = %s",
            (command.metadata.idempotency_key.value,),
        )
        == 1
    )


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

    assert_result_committed(
        proto,
        single_settlement_response(proto, settlement_target, first),
        expected_oneof="issue_trade_instance",
    )
    before_conflict = trade_db.scenario_snapshot(ids)
    conflict = single_settlement_response(proto, settlement_target, second)

    assert_result_rejected(
        proto,
        conflict,
        error_code=proto.errors.ERROR_CODE_CONFLICT,
        retryable=False,
    )
    assert conflict.rejected.error.code == proto.errors.ERROR_CODE_CONFLICT
    assert trade_db.scenario_snapshot(ids) == before_conflict
    assert_single_trade_side_effects(trade_db, ids, settlement_count=0)
    assert_no_trade_side_effects(trade_db, second_ids)


def test_concurrent_duplicate_issue_requests_commit_once_and_replay_safely(
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

    def send_duplicate():
        cloned = proto.settlement_command.TradeSettlementCommand()
        cloned.CopyFrom(command)
        return single_settlement_response(proto, settlement_target, cloned)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: send_duplicate(), range(4)))

    committed = [
        result
        for result in results
        if result.attempt_status
        == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_COMMITTED
    ]
    replays = [
        result
        for result in results
        if result.attempt_status
        == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_IDEMPOTENT_REPLAY
    ]
    assert len(committed) == 1
    assert len(replays) == 3
    assert_result_committed(proto, committed[0], expected_oneof="issue_trade_instance")
    for replay in replays:
        assert_result_replayed(
            proto,
            replay,
            original=committed[0],
            expected_oneof="issue_trade_instance",
        )
    assert_item_stack_quantity(trade_db, ids.issuer_item_stack_id, 5)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 5)
    assert_single_trade_side_effects(trade_db, ids, settlement_count=0)
    assert (
        trade_db.table_count(
            "operation", "idempotency_key = %s", (command.metadata.idempotency_key.value,)
        )
        == 1
    )
