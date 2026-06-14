from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from helpers.assertions import (
    assert_escrow_quantity,
    assert_result_committed,
    assert_result_rejected,
    assert_trade_state,
    assert_value_conservation,
)
from helpers.builders import (
    TradeScenarioIds,
    cancel_command,
    expire_command,
    issue_command,
    new_uuid,
    now_millis,
    settle_command,
)
from helpers.grpc_clients import single_settlement_response


pytestmark = [pytest.mark.live, pytest.mark.settlement]


def test_concurrent_competing_full_settlements_have_exactly_one_winner(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    second_buyer = trade_db.seed_extra_buyer_wallet(buyer_wallet_id=new_uuid())
    second_world = replace(
        world,
        buyer_id=second_buyer.buyer_id,
        buyer_wallet_id=second_buyer.buyer_wallet_id,
        initial_buyer_wallet_major=second_buyer.initial_buyer_wallet_major,
    )
    second_ids = TradeScenarioIds(
        trade_instance_id=ids.trade_instance_id,
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=second_buyer.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
        item_stack_escrow_id=ids.item_stack_escrow_id,
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

    commands = [
        settle_command(proto, ids, world, quantity=5),
        settle_command(proto, second_ids, second_world, quantity=5),
    ]
    results = _send_concurrently(proto, settlement_target, commands)

    assert _attempt_count(proto, results, "committed") == 1
    assert _attempt_count(proto, results, "rejected") == 1
    for result in results:
        if result.attempt_status == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_REJECTED:
            assert_result_rejected(
                proto,
                result,
                error_code=proto.errors.ERROR_CODE_FAILED_PRECONDITION,
                retryable=False,
            )
    assert_trade_state(trade_db, ids.trade_instance_id, state="completed", remaining=0)
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)


@pytest.mark.parametrize(
    "race",
    [
        "settle_vs_cancel",
        "settle_vs_expire",
        "cancel_vs_expire",
    ],
)
def test_terminal_races_have_exactly_one_committed_terminal_winner(
    proto_modules, trade_db, settlement_target, race: str
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

    commands = _race_commands(proto, ids, world, race)
    results = _send_concurrently(proto, settlement_target, commands)

    assert _attempt_count(proto, results, "committed") == 1
    assert _attempt_count(proto, results, "rejected") == 1
    committed = [
        result
        for result in results
        if result.attempt_status
        == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_COMMITTED
    ][0]
    assert_result_committed(proto, committed)
    if committed.WhichOneof("result") == "settle_trade_instance":
        assert_trade_state(trade_db, ids.trade_instance_id, state="expired")
    elif committed.WhichOneof("result") == "cancel_trade_instance":
        assert_trade_state(trade_db, ids.trade_instance_id, state="cancelled")
    elif committed.WhichOneof("result") == "expire_trade_instance":
        assert_trade_state(trade_db, ids.trade_instance_id, state="expired")
    else:
        raise AssertionError(f"unexpected terminal result {committed.WhichOneof('result')}")
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_value_conservation(trade_db, ids, world)


def _race_commands(proto, ids: TradeScenarioIds, world, race: str) -> list:
    settle_ids = TradeScenarioIds(
        trade_instance_id=ids.trade_instance_id,
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
        item_stack_escrow_id=ids.item_stack_escrow_id,
        buyer_destination_stack_id=ids.buyer_destination_stack_id,
    )
    expire_ids = TradeScenarioIds(
        trade_instance_id=ids.trade_instance_id,
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
        item_stack_escrow_id=ids.item_stack_escrow_id,
        buyer_destination_stack_id=ids.buyer_destination_stack_id,
    )
    if race == "settle_vs_cancel":
        return [
            settle_command(proto, settle_ids, world, quantity=5),
            cancel_command(proto, ids, world),
        ]
    if race == "settle_vs_expire":
        return [
            settle_command(proto, settle_ids, world, quantity=5),
            expire_command(
                proto,
                expire_ids,
                world,
                evaluated_at_unix_millis=now_millis() + 1_000,
            ),
        ]
    if race == "cancel_vs_expire":
        return [
            cancel_command(proto, ids, world),
            expire_command(
                proto,
                expire_ids,
                world,
                evaluated_at_unix_millis=now_millis() + 1_000,
            ),
        ]
    raise AssertionError(f"unknown race {race}")


def _send_concurrently(proto, settlement_target: str, commands: list) -> list:
    def send(command):
        cloned = proto.settlement_command.TradeSettlementCommand()
        cloned.CopyFrom(command)
        return single_settlement_response(proto, settlement_target, cloned)

    with ThreadPoolExecutor(max_workers=len(commands)) as pool:
        return list(pool.map(send, commands))


def _attempt_count(proto, results: list, kind: str) -> int:
    statuses = {
        "committed": proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_COMMITTED,
        "rejected": proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_REJECTED,
    }
    return sum(1 for result in results if result.attempt_status == statuses[kind])
