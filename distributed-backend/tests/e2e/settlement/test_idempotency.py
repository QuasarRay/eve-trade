from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from helpers.assertions import (
    assert_escrow_quantity,
    assert_item_stack_quantity,
    assert_no_mutation,
    assert_no_trade_side_effects,
    assert_result_committed,
    assert_result_rejected,
    assert_result_replayed,
    assert_single_trade_side_effects,
)
from helpers.builders import (
    TradeScenarioIds,
    cancel_command,
    expire_command,
    issue_command,
    now_millis,
    operation_metadata,
    settle_command,
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


def test_same_idempotency_key_across_operation_kinds_is_rejected(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    first = issue_command(proto, ids, world, total_quantity=5)
    assert_result_committed(
        proto,
        single_settlement_response(proto, settlement_target, first),
        expected_oneof="issue_trade_instance",
    )
    conflict_metadata = with_idempotency_key(
        proto,
        operation_metadata(
            proto,
            caused_by_capsuleer_id=world.issuer_id,
            purpose="cross-kind-conflict",
        ),
        first.metadata.idempotency_key.value,
    )
    before = trade_db.scenario_snapshot(ids)

    conflict = single_settlement_response(
        proto,
        settlement_target,
        cancel_command(proto, ids, world, metadata=conflict_metadata),
    )

    assert_result_rejected(
        proto,
        conflict,
        error_code=proto.errors.ERROR_CODE_CONFLICT,
        retryable=False,
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))


def test_settle_same_idempotency_key_with_different_terms_is_rejected(
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
        expected_oneof="issue_trade_instance",
    )
    metadata = operation_metadata(
        proto,
        caused_by_capsuleer_id=world.buyer_id,
        purpose="settle-conflict",
    )
    first = settle_command(proto, ids, world, quantity=1, metadata=metadata)
    assert_result_committed(
        proto,
        single_settlement_response(proto, settlement_target, first),
        expected_oneof="settle_trade_instance",
    )

    second_ids = TradeScenarioIds(
        trade_instance_id=ids.trade_instance_id,
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
        item_stack_escrow_id=ids.item_stack_escrow_id,
        buyer_destination_stack_id=ids.buyer_destination_stack_id,
    )
    second_metadata = with_idempotency_key(
        proto,
        operation_metadata(
            proto,
            caused_by_capsuleer_id=world.buyer_id,
            purpose="settle-conflict",
        ),
        metadata.idempotency_key.value,
    )
    before = trade_db.scenario_snapshot(ids)
    conflict = single_settlement_response(
        proto,
        settlement_target,
        settle_command(proto, second_ids, world, quantity=2, metadata=second_metadata),
    )

    assert_result_rejected(
        proto,
        conflict,
        error_code=proto.errors.ERROR_CODE_CONFLICT,
        retryable=False,
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))


def test_rejected_request_is_not_cached_as_idempotent_replay(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
        source_quantity=1,
    )
    command = issue_command(proto, ids, world, total_quantity=2)

    first = single_settlement_response(proto, settlement_target, command)
    second = single_settlement_response(proto, settlement_target, command)

    assert_result_rejected(
        proto,
        first,
        error_code=proto.errors.ERROR_CODE_FAILED_PRECONDITION,
        retryable=False,
    )
    assert_result_rejected(
        proto,
        second,
        error_code=proto.errors.ERROR_CODE_FAILED_PRECONDITION,
        retryable=False,
    )
    assert (
        trade_db.table_count(
            "idempotency_result",
            "idempotency_key = %s",
            (command.metadata.idempotency_key.value,),
        )
        == 0
    )


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


def test_duplicate_settle_request_is_idempotent_replay(
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
        expected_oneof="issue_trade_instance",
    )
    command = settle_command(proto, ids, world, quantity=5)

    first = single_settlement_response(proto, settlement_target, command)
    second = single_settlement_response(proto, settlement_target, command)

    assert_result_committed(proto, first, expected_oneof="settle_trade_instance")
    assert_result_replayed(
        proto,
        second,
        original=first,
        expected_oneof="settle_trade_instance",
    )
    assert_single_trade_side_effects(trade_db, ids, settlement_count=1)
    assert (
        trade_db.table_count(
            "settlement", "idempotency_key = %s", (command.metadata.idempotency_key.value,)
        )
        == 1
    )


def test_duplicate_cancel_request_is_idempotent_replay(
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
        expected_oneof="issue_trade_instance",
    )
    command = cancel_command(proto, ids, world)

    first = single_settlement_response(proto, settlement_target, command)
    second = single_settlement_response(proto, settlement_target, command)

    assert_result_committed(proto, first, expected_oneof="cancel_trade_instance")
    assert_result_replayed(
        proto,
        second,
        original=first,
        expected_oneof="cancel_trade_instance",
    )
    assert_single_trade_side_effects(trade_db, ids, settlement_count=0)


def test_duplicate_expire_request_is_idempotent_replay(
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
            issue_command(
                proto,
                ids,
                world,
                total_quantity=5,
                expires_at_unix_millis=now_millis() - 1_000,
            ),
        ),
        expected_oneof="issue_trade_instance",
    )
    command = expire_command(
        proto,
        ids,
        world,
        evaluated_at_unix_millis=now_millis() + 1_000,
    )

    first = single_settlement_response(proto, settlement_target, command)
    second = single_settlement_response(proto, settlement_target, command)

    assert_result_committed(proto, first, expected_oneof="expire_trade_instance")
    assert_result_replayed(
        proto,
        second,
        original=first,
        expected_oneof="expire_trade_instance",
    )
    assert_single_trade_side_effects(trade_db, ids, settlement_count=0)


def test_concurrent_duplicate_settle_requests_commit_once_and_replay_safely(
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
        expected_oneof="issue_trade_instance",
    )
    command = settle_command(proto, ids, world, quantity=5)

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
    assert_result_committed(proto, committed[0], expected_oneof="settle_trade_instance")
    for replay in replays:
        assert_result_replayed(
            proto,
            replay,
            original=committed[0],
            expected_oneof="settle_trade_instance",
        )
    assert_single_trade_side_effects(trade_db, ids, settlement_count=1)
