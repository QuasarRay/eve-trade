from __future__ import annotations

import pytest

from helpers.assertions import (
    assert_escrow_quantity,
    assert_item_stack_quantity,
    assert_no_mutation,
    assert_result_committed,
    assert_result_rejected,
    assert_terminal_audit_complete,
    assert_trade_state,
    assert_value_conservation,
)
from helpers.builders import (
    TradeScenarioIds,
    cancel_command,
    expire_command,
    issue_command,
    now_millis,
    settle_command,
)
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
    issue = issue_command(proto, ids, world, total_quantity=5)
    assert_result_committed(
        proto,
        single_settlement_response(proto, settlement_target, issue),
        expected_oneof="issue_trade_instance",
    )

    cancel = cancel_command(proto, ids, world)
    cancel_result = single_settlement_response(proto, settlement_target, cancel)

    assert_result_committed(proto, cancel_result, expected_oneof="cancel_trade_instance")
    assert_trade_state(trade_db, ids.trade_instance_id, state="cancelled")
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_item_stack_quantity(
        trade_db,
        ids.issuer_item_stack_id,
        world.initial_source_quantity,
    )
    assert_terminal_audit_complete(
        trade_db,
        ids,
        cancel,
        kind="cancel_trade_instance",
        event_kind="trade_instance_cancelled",
        final_state="cancelled",
    )
    assert_value_conservation(trade_db, ids, world)


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
    issue = issue_command(proto, ids, world, total_quantity=5)
    assert_result_committed(
        proto,
        single_settlement_response(proto, settlement_target, issue),
        expected_oneof="issue_trade_instance",
    )

    expire = expire_command(
        proto,
        ids,
        world,
        evaluated_at_unix_millis=now_millis() + 3_700_000,
    )
    expire_result = single_settlement_response(proto, settlement_target, expire)

    assert_result_committed(proto, expire_result, expected_oneof="expire_trade_instance")
    assert_trade_state(trade_db, ids.trade_instance_id, state="expired")
    assert_escrow_quantity(trade_db, ids.item_stack_escrow_id, 0)
    assert_item_stack_quantity(
        trade_db,
        ids.issuer_item_stack_id,
        world.initial_source_quantity,
    )
    assert_terminal_audit_complete(
        trade_db,
        ids,
        expire,
        kind="expire_trade_instance",
        event_kind="trade_instance_expired",
        final_state="expired",
    )
    assert_value_conservation(trade_db, ids, world)


@pytest.mark.parametrize("terminal_state", ["completed", "cancelled", "expired"])
@pytest.mark.parametrize("illegal_operation", ["settle", "cancel", "expire"])
def test_terminal_trade_rejects_illegal_follow_up_operations_without_mutation(
    proto_modules, trade_db, settlement_target, terminal_state: str, illegal_operation: str
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    _drive_trade_to_terminal_state(proto, trade_db, settlement_target, ids, world, terminal_state)

    before = trade_db.scenario_snapshot(ids)
    rejected = single_settlement_response(
        proto,
        settlement_target,
        _illegal_follow_up_command(proto, ids, world, illegal_operation),
    )

    assert_result_rejected(
        proto,
        rejected,
        error_code=proto.errors.ERROR_CODE_FAILED_PRECONDITION,
        retryable=False,
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_value_conservation(trade_db, ids, world)


def _drive_trade_to_terminal_state(
    proto, trade_db, settlement_target, ids: TradeScenarioIds, world, terminal_state: str
) -> None:
    assert_result_committed(
        proto,
        single_settlement_response(
            proto,
            settlement_target,
            issue_command(proto, ids, world, total_quantity=5),
        ),
        expected_oneof="issue_trade_instance",
    )
    if terminal_state == "completed":
        assert_result_committed(
            proto,
            single_settlement_response(
                proto,
                settlement_target,
                settle_command(proto, ids, world, quantity=5),
            ),
            expected_oneof="settle_trade_instance",
        )
    elif terminal_state == "cancelled":
        assert_result_committed(
            proto,
            single_settlement_response(
                proto,
                settlement_target,
                cancel_command(proto, ids, world),
            ),
            expected_oneof="cancel_trade_instance",
        )
    elif terminal_state == "expired":
        assert_result_committed(
            proto,
            single_settlement_response(
                proto,
                settlement_target,
                expire_command(
                    proto,
                    ids,
                    world,
                    evaluated_at_unix_millis=now_millis() + 3_700_000,
                ),
            ),
            expected_oneof="expire_trade_instance",
        )
    else:
        raise AssertionError(f"unknown terminal state {terminal_state}")
    assert_trade_state(trade_db, ids.trade_instance_id, state=terminal_state)


def _illegal_follow_up_command(proto, ids: TradeScenarioIds, world, operation: str):
    next_ids = TradeScenarioIds(
        trade_instance_id=ids.trade_instance_id,
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
        item_stack_escrow_id=ids.item_stack_escrow_id,
        buyer_destination_stack_id=ids.buyer_destination_stack_id,
    )
    if operation == "settle":
        return settle_command(proto, next_ids, world, quantity=1)
    if operation == "cancel":
        return cancel_command(proto, next_ids, world)
    if operation == "expire":
        return expire_command(
            proto,
            next_ids,
            world,
            evaluated_at_unix_millis=now_millis() + 3_700_000,
        )
    raise AssertionError(f"unknown illegal operation {operation}")
