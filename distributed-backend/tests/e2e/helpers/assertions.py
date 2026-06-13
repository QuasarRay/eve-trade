from __future__ import annotations

from decimal import Decimal


def assert_result_committed(proto, result) -> None:
    assert (
        result.attempt_status
        == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_COMMITTED
    )
    assert result.WhichOneof("result") == "committed"


def assert_result_rejected(proto, result) -> None:
    assert (
        result.attempt_status
        == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_REJECTED
    )
    assert result.WhichOneof("result") == "rejected"


def assert_result_replayed(proto, result) -> None:
    assert (
        result.attempt_status
        == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_IDEMPOTENT_REPLAY
    )


def assert_trade_state(db, trade_instance_id: str, *, state: str, remaining: int) -> None:
    row = db.fetch_one(
        """
        SELECT trade_state, remaining_quantity
        FROM trade_instance
        WHERE trade_instance_id = %s
        """,
        (trade_instance_id,),
    )
    assert row is not None
    assert row["trade_state"] == state
    assert row["remaining_quantity"] == remaining


def assert_item_stack_quantity(db, item_stack_id: str, expected: int) -> None:
    row = db.fetch_one(
        "SELECT quantity FROM item_stack WHERE item_stack_id = %s",
        (item_stack_id,),
    )
    assert row is not None
    assert row["quantity"] == expected


def assert_escrow_quantity(db, item_stack_escrow_id: str, expected: int) -> None:
    row = db.fetch_one(
        "SELECT quantity FROM item_stack_escrow WHERE item_stack_escrow_id = %s",
        (item_stack_escrow_id,),
    )
    assert row is not None
    assert row["quantity"] == expected


def assert_wallet_amount(db, wallet_id: str, expected_major: Decimal) -> None:
    row = db.fetch_one(
        "SELECT isk_amount FROM wallet WHERE wallet_id = %s",
        (wallet_id,),
    )
    assert row is not None
    assert Decimal(row["isk_amount"]) == expected_major
