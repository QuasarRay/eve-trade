from __future__ import annotations

from decimal import Decimal
from pprint import pformat
from typing import Any


COMMITTED_RESULT_ONEOFS = {
    "issue_trade_instance",
    "settle_trade_instance",
    "cancel_trade_instance",
    "expire_trade_instance",
}

SETTLEMENT_STEP_NAMES = [
    "validating_metadata",
    "locking_rows",
    "applying_ownership",
    "writing_audit",
    "completed",
]


def minor_to_major(minor_units: int) -> Decimal:
    return Decimal(minor_units) / Decimal(100)


def message_value(message) -> str:
    assert message is not None
    return message.value


def assert_result_committed(proto, result, *, expected_oneof: str | None = None) -> None:
    assert result is not None
    assert (
        result.attempt_status
        == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_COMMITTED
    )
    actual_oneof = result.WhichOneof("result")
    assert actual_oneof in COMMITTED_RESULT_ONEOFS
    if expected_oneof is not None:
        assert actual_oneof == expected_oneof


def assert_result_rejected(
    proto,
    result,
    *,
    error_code: int | None = None,
    retryable: bool | None = None,
    message_contains: str | None = None,
) -> None:
    assert result is not None
    assert (
        result.attempt_status
        == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_REJECTED
    )
    assert result.WhichOneof("result") == "rejected"
    assert result.rejected.error is not None
    if error_code is not None:
        assert result.rejected.error.code == error_code
    if retryable is not None:
        assert result.rejected.error.retryable is retryable
    if message_contains is not None:
        assert message_contains in result.rejected.error.message


def assert_result_unknown(proto, result, *, error_code: int | None = None) -> None:
    assert result is not None
    assert (
        result.attempt_status
        == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_RESULT_UNKNOWN
    )
    assert result.WhichOneof("result") == "result_unknown"
    assert result.result_unknown.error is not None
    if error_code is not None:
        assert result.result_unknown.error.code == error_code


def assert_result_replayed(
    proto,
    result,
    *,
    original=None,
    expected_oneof: str | None = None,
) -> None:
    assert result is not None
    assert (
        result.attempt_status
        == proto.settlement_result.TRANSACTION_ATTEMPT_STATUS_IDEMPOTENT_REPLAY
    )
    actual_oneof = result.WhichOneof("result")
    assert actual_oneof in COMMITTED_RESULT_ONEOFS
    if expected_oneof is not None:
        assert actual_oneof == expected_oneof
    if original is not None:
        assert actual_oneof == original.WhichOneof("result")
        assert result.operation_kind == original.operation_kind
        assert result.trade_instance_id.value == original.trade_instance_id.value
        assert result.trade_transaction_id.value == original.trade_transaction_id.value
        assert result.settlement_id.value == original.settlement_id.value
        assert result.resulting_trade_state == original.resulting_trade_state


def assert_no_mutation(before: dict[str, Any], after: dict[str, Any]) -> None:
    assert after == before, (
        "Rejected operation mutated scenario state.\n"
        f"Before:\n{pformat(before)}\nAfter:\n{pformat(after)}"
    )


def assert_trade_state(
    db, trade_instance_id: str, *, state: str, remaining: int | None = None
) -> None:
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
    if remaining is not None:
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


def assert_value_conservation(db, ids, world) -> None:
    issuer_wallet = _decimal_scalar(
        db,
        "SELECT isk_amount FROM wallet WHERE wallet_id = %s",
        (ids.issuer_wallet_id,),
    )
    buyer_wallet = _decimal_scalar(
        db,
        "SELECT isk_amount FROM wallet WHERE wallet_id = %s",
        (ids.buyer_wallet_id,),
    )
    assert issuer_wallet + buyer_wallet == (
        world.initial_issuer_wallet_major + world.initial_buyer_wallet_major
    )

    source_quantity = _int_scalar(
        db,
        "SELECT quantity FROM item_stack WHERE item_stack_id = %s",
        (ids.issuer_item_stack_id,),
    )
    buyer_quantity = _int_scalar(
        db,
        "SELECT COALESCE((SELECT quantity FROM item_stack WHERE item_stack_id = %s), 0)",
        (ids.buyer_destination_stack_id,),
    )
    escrow_quantity = _int_scalar(
        db,
        """
        SELECT COALESCE(
            (SELECT quantity FROM item_stack_escrow WHERE item_stack_escrow_id = %s),
            0
        )
        """,
        (ids.item_stack_escrow_id,),
    )
    assert (
        source_quantity + buyer_quantity + escrow_quantity
        == world.initial_source_quantity
    )


def assert_issued_trade_visible_state(
    db,
    trade_instance_id: str,
    world,
    *,
    quantity: int,
    unit_price_minor: int,
) -> None:
    trade = db.fetch_one(
        """
        SELECT operation_id, trade_state, issuer_id, issuer_wallet_id, item_type_id,
               station_id, region_id, total_quantity, remaining_quantity,
               unit_price_isk
        FROM trade_instance
        WHERE trade_instance_id = %s
        """,
        (trade_instance_id,),
    )
    assert trade is not None
    assert trade["trade_state"] == "outstanding"
    assert trade["issuer_id"] == world.issuer_id
    assert str(trade["issuer_wallet_id"]) == world.issuer_wallet_id
    assert trade["item_type_id"] == world.item_type_id
    assert trade["station_id"] == world.station_id
    assert trade["region_id"] == world.region_id
    assert trade["total_quantity"] == quantity
    assert trade["remaining_quantity"] == quantity
    assert Decimal(trade["unit_price_isk"]) == minor_to_major(unit_price_minor)

    escrow = _single_row(
        db.fetch_all(
            """
            SELECT quantity, escrow_state, source_item_stack_id
            FROM item_stack_escrow
            WHERE trade_instance_id = %s
            """,
            (trade_instance_id,),
        )
    )
    assert escrow["quantity"] == quantity
    assert escrow["escrow_state"] == "held"
    assert str(escrow["source_item_stack_id"]) == world.issuer_item_stack_id

    assert_item_stack_quantity(
        db,
        world.issuer_item_stack_id,
        world.initial_source_quantity - quantity,
    )

    event = _single_row(
        db.fetch_all(
            """
            SELECT event_kind, aggregate_kind, aggregate_id, publish_state
            FROM domain_event_outbox
            WHERE aggregate_id = %s
            """,
            (trade_instance_id,),
        )
    )
    assert event["event_kind"] == "trade_instance_issued"
    assert event["aggregate_kind"] == "trade_instance"
    assert event["aggregate_id"] == trade_instance_id
    assert event["publish_state"] == "pending"


def assert_operation_recorded(db, metadata, *, kind: str) -> dict[str, Any]:
    operation_id = metadata.operation_id.value
    request_id = metadata.request_id.value
    idempotency_key = metadata.idempotency_key.value

    operation = db.fetch_one(
        """
        SELECT operation_id, operation_kind, source_system, external_operation_id,
               request_id, idempotency_key, caused_by_capsuleer_id, operation_state,
               created_by_service, completed_at, failure_code, failure_message
        FROM operation
        WHERE operation_id = %s
        """,
        (operation_id,),
    )
    assert operation is not None
    assert str(operation["operation_id"]) == operation_id
    assert operation["operation_kind"] == kind
    assert operation["source_system"] == metadata.source_system.value
    assert operation["external_operation_id"] == metadata.external_operation_id.value
    assert str(operation["request_id"]) == request_id
    assert operation["idempotency_key"] == idempotency_key
    assert (
        operation["caused_by_capsuleer_id"]
        == metadata.caused_by_capsuleer_id.value
    )
    assert operation["operation_state"] == "completed"
    assert operation["created_by_service"] == metadata.created_by_service.value
    assert operation["completed_at"] is not None
    assert operation["failure_code"] is None
    assert operation["failure_message"] is None

    attempt = db.fetch_one(
        """
        SELECT request_id, idempotency_key, received_by_service, attempt_state,
               failure_code, completed_at
        FROM request_attempt
        WHERE request_id = %s
        """,
        (request_id,),
    )
    assert attempt is not None
    assert str(attempt["request_id"]) == request_id
    assert attempt["idempotency_key"] == idempotency_key
    assert attempt["received_by_service"] == "trade-settlement"
    assert attempt["attempt_state"] == "completed"
    assert attempt["failure_code"] is None
    assert attempt["completed_at"] is not None

    idempotency = db.fetch_one(
        """
        SELECT idempotency_key, operation_name, operation_state, created_by_service,
               completed_at
        FROM idempotency_record
        WHERE idempotency_key = %s
        """,
        (idempotency_key,),
    )
    assert idempotency is not None
    assert idempotency["operation_name"] == kind
    assert idempotency["operation_state"] == "completed"
    assert idempotency["created_by_service"] == metadata.created_by_service.value
    assert idempotency["completed_at"] is not None

    return operation


def assert_idempotency_result(
    db,
    metadata,
    *,
    result_kind: str,
    result_state: str,
    trade_instance_id: str,
    trade_transaction_id: str | None = None,
    settlement_id: str | None = None,
) -> None:
    row = db.fetch_one(
        """
        SELECT operation_id, result_kind, trade_instance_id, trade_transaction_id,
               settlement_id, wallet_operation_id, item_stack_operation_id,
               result_state, failure_code
        FROM idempotency_result
        WHERE idempotency_key = %s
        """,
        (metadata.idempotency_key.value,),
    )
    assert row is not None
    assert str(row["operation_id"]) == metadata.operation_id.value
    assert row["result_kind"] == result_kind
    assert str(row["trade_instance_id"]) == trade_instance_id
    assert _optional_uuid(row["trade_transaction_id"]) == trade_transaction_id
    assert _optional_uuid(row["settlement_id"]) == settlement_id
    assert row["result_state"] == result_state
    assert row["failure_code"] is None


def assert_issue_audit_complete(db, ids, command, *, quantity: int) -> None:
    metadata = command.metadata
    assert_operation_recorded(db, metadata, kind="issue_trade_instance")
    assert_idempotency_result(
        db,
        metadata,
        result_kind="issue_trade_instance",
        result_state="outstanding",
        trade_instance_id=ids.trade_instance_id,
    )

    stack_op = _single_row(
        db.fetch_all(
            """
            SELECT item_stack_operation_id, operation_kind, item_stack_operation_state
            FROM item_stack_operation
            WHERE operation_id = %s
            """,
            (metadata.operation_id.value,),
        )
    )
    assert stack_op["operation_kind"] == "issue_trade_instance"
    assert stack_op["item_stack_operation_state"] == "completed"

    ledger = _single_row(
        db.fetch_all(
            """
            SELECT item_stack_id, entry_kind, quantity_delta, quantity_before,
                   quantity_after, stack_version_after, stack_checksum_after
            FROM item_stack_ledger
            WHERE item_stack_operation_id = %s
            """,
            (stack_op["item_stack_operation_id"],),
        )
    )
    assert str(ledger["item_stack_id"]) == ids.issuer_item_stack_id
    assert ledger["entry_kind"] == "trade_escrow_hold"
    assert ledger["quantity_delta"] == -quantity
    assert ledger["quantity_before"] >= quantity
    assert ledger["quantity_after"] == ledger["quantity_before"] - quantity
    assert ledger["stack_version_after"] == 1
    assert ledger["stack_checksum_after"]

    state_change = _single_row(
        db.fetch_all(
            """
            SELECT from_trade_state, to_trade_state, trade_state_change_kind,
                   changed_by_service
            FROM trade_state_change
            WHERE operation_id = %s
            """,
            (metadata.operation_id.value,),
        )
    )
    assert state_change["from_trade_state"] is None
    assert state_change["to_trade_state"] == "outstanding"
    assert state_change["trade_state_change_kind"] == "issue_trade_instance"
    assert state_change["changed_by_service"] == "trade-settlement"

    assert_domain_event(
        db,
        metadata.operation_id.value,
        event_kind="trade_instance_issued",
        aggregate_id=ids.trade_instance_id,
    )


def assert_settlement_audit_complete(
    db,
    ids,
    command,
    *,
    quantity: int,
    unit_price_minor: int,
    resulting_state: str,
) -> None:
    metadata = command.metadata
    total_price_minor = quantity * unit_price_minor
    assert_operation_recorded(db, metadata, kind="settle_trade_instance")
    assert_idempotency_result(
        db,
        metadata,
        result_kind="settle_trade_instance",
        result_state=resulting_state,
        trade_instance_id=ids.trade_instance_id,
        trade_transaction_id=ids.transaction_id,
        settlement_id=ids.settlement_id,
    )

    transaction = _single_row(
        db.fetch_all(
            """
            SELECT trade_transaction_state, buyer_capsuleer_id, buyer_wallet_id,
                   seller_capsuleer_id, seller_wallet_id, source_item_stack_id,
                   destination_item_stack_id, quantity, unit_price_isk,
                   total_price_isk
            FROM trade_transaction
            WHERE trade_transaction_id = %s
            """,
            (ids.transaction_id,),
        )
    )
    assert transaction["trade_transaction_state"] == "completed"
    assert str(transaction["buyer_wallet_id"]) == ids.buyer_wallet_id
    assert str(transaction["seller_wallet_id"]) == ids.issuer_wallet_id
    assert str(transaction["source_item_stack_id"]) == ids.item_stack_escrow_id
    assert str(transaction["destination_item_stack_id"]) == ids.buyer_destination_stack_id
    assert transaction["quantity"] == quantity
    assert Decimal(transaction["unit_price_isk"]) == minor_to_major(unit_price_minor)
    assert Decimal(transaction["total_price_isk"]) == minor_to_major(total_price_minor)

    settlement = _single_row(
        db.fetch_all(
            """
            SELECT settlement_state, settlement_phase, retry_count, failure_code,
                   failure_message
            FROM settlement
            WHERE settlement_id = %s
            """,
            (ids.settlement_id,),
        )
    )
    assert settlement["settlement_state"] == "completed"
    assert settlement["settlement_phase"] == "completed"
    assert settlement["retry_count"] == 0
    assert settlement["failure_code"] is None
    assert settlement["failure_message"] is None

    steps = db.fetch_all(
        """
        SELECT step_name, step_state, failure_code, failure_message, completed_at
        FROM settlement_step
        WHERE settlement_id = %s
        ORDER BY started_at, settlement_step_id
        """,
        (ids.settlement_id,),
    )
    assert [step["step_name"] for step in steps] == SETTLEMENT_STEP_NAMES
    assert all(step["step_state"] == "completed" for step in steps)
    assert all(step["completed_at"] is not None for step in steps)
    assert all(step["failure_code"] is None for step in steps)
    assert all(step["failure_message"] is None for step in steps)

    wallet_op = _single_row(
        db.fetch_all(
            """
            SELECT wallet_operation_id, operation_kind, wallet_operation_state
            FROM wallet_operation
            WHERE operation_id = %s
            """,
            (metadata.operation_id.value,),
        )
    )
    assert wallet_op["operation_kind"] == "settle_trade_instance"
    assert wallet_op["wallet_operation_state"] == "completed"
    wallet_ledgers = db.fetch_all(
        """
        SELECT wallet_id, entry_kind, isk_amount_delta, isk_amount_before,
               isk_amount_after, wallet_version_after, wallet_checksum_after
        FROM wallet_ledger
        WHERE wallet_operation_id = %s
        ORDER BY entry_kind
        """,
        (wallet_op["wallet_operation_id"],),
    )
    assert len(wallet_ledgers) == 2
    deltas = {row["entry_kind"]: row for row in wallet_ledgers}
    assert Decimal(deltas["trade_purchase_debit"]["isk_amount_delta"]) == -minor_to_major(
        total_price_minor
    )
    assert str(deltas["trade_purchase_debit"]["wallet_id"]) == ids.buyer_wallet_id
    assert Decimal(deltas["trade_sale_credit"]["isk_amount_delta"]) == minor_to_major(
        total_price_minor
    )
    assert str(deltas["trade_sale_credit"]["wallet_id"]) == ids.issuer_wallet_id
    for row in wallet_ledgers:
        assert row["wallet_version_after"] >= 1
        assert row["wallet_checksum_after"]

    item_op = _single_row(
        db.fetch_all(
            """
            SELECT item_stack_operation_id, operation_kind, item_stack_operation_state
            FROM item_stack_operation
            WHERE operation_id = %s
            """,
            (metadata.operation_id.value,),
        )
    )
    assert item_op["operation_kind"] == "settle_trade_instance"
    assert item_op["item_stack_operation_state"] == "completed"
    item_ledger = _single_row(
        db.fetch_all(
            """
            SELECT item_stack_id, entry_kind, quantity_delta, quantity_before,
                   quantity_after, stack_version_after, stack_checksum_after
            FROM item_stack_ledger
            WHERE item_stack_operation_id = %s
            """,
            (item_op["item_stack_operation_id"],),
        )
    )
    assert str(item_ledger["item_stack_id"]) == ids.buyer_destination_stack_id
    assert item_ledger["entry_kind"] == "trade_delivery_credit"
    assert item_ledger["quantity_delta"] == quantity
    assert item_ledger["quantity_after"] == item_ledger["quantity_before"] + quantity
    assert item_ledger["stack_version_after"] >= 1
    assert item_ledger["stack_checksum_after"]

    claims = db.fetch_all(
        """
        SELECT trade_claim_id, claiming_capsuleer_id, claim_state
        FROM trade_claim
        WHERE settlement_id = %s
        """,
        (ids.settlement_id,),
    )
    assert len(claims) == 2
    assert {claim["claim_state"] for claim in claims} == {"created"}

    claim_isk = _single_row(
        db.fetch_all(
            """
            SELECT isk.wallet_id, isk.amount_isk
            FROM trade_claim_isk isk
            JOIN trade_claim claim ON claim.trade_claim_id = isk.trade_claim_id
            WHERE claim.settlement_id = %s
            """,
            (ids.settlement_id,),
        )
    )
    assert str(claim_isk["wallet_id"]) == ids.issuer_wallet_id
    assert Decimal(claim_isk["amount_isk"]) == minor_to_major(total_price_minor)

    claim_stack = _single_row(
        db.fetch_all(
            """
            SELECT stack.item_stack_id, stack.quantity
            FROM trade_claim_item_stack stack
            JOIN trade_claim claim ON claim.trade_claim_id = stack.trade_claim_id
            WHERE claim.settlement_id = %s
            """,
            (ids.settlement_id,),
        )
    )
    assert str(claim_stack["item_stack_id"]) == ids.buyer_destination_stack_id
    assert claim_stack["quantity"] == quantity

    state_change = _single_row(
        db.fetch_all(
            """
            SELECT from_trade_state, to_trade_state, trade_state_change_kind,
                   changed_by_service
            FROM trade_state_change
            WHERE operation_id = %s
            """,
            (metadata.operation_id.value,),
        )
    )
    assert state_change["from_trade_state"] == "outstanding"
    assert state_change["to_trade_state"] == resulting_state
    assert state_change["trade_state_change_kind"] == "settle_trade_instance"
    assert state_change["changed_by_service"] == "trade-settlement"

    assert_domain_event(
        db,
        metadata.operation_id.value,
        event_kind="trade_instance_settled",
        aggregate_id=ids.trade_instance_id,
    )


def assert_terminal_audit_complete(
    db,
    ids,
    command,
    *,
    kind: str,
    event_kind: str,
    final_state: str,
) -> None:
    metadata = command.metadata
    assert_operation_recorded(db, metadata, kind=kind)
    assert_idempotency_result(
        db,
        metadata,
        result_kind=kind,
        result_state=final_state,
        trade_instance_id=ids.trade_instance_id,
    )
    state_change = _single_row(
        db.fetch_all(
            """
            SELECT from_trade_state, to_trade_state, trade_state_change_kind,
                   changed_by_service
            FROM trade_state_change
            WHERE operation_id = %s
            """,
            (metadata.operation_id.value,),
        )
    )
    assert state_change["from_trade_state"] == "outstanding"
    assert state_change["to_trade_state"] == final_state
    assert state_change["trade_state_change_kind"] == kind
    assert state_change["changed_by_service"] == "trade-settlement"
    assert_domain_event(
        db,
        metadata.operation_id.value,
        event_kind=event_kind,
        aggregate_id=ids.trade_instance_id,
    )

    release_op = _single_row(
        db.fetch_all(
            """
            SELECT item_stack_operation_id, operation_kind, item_stack_operation_state
            FROM item_stack_operation
            WHERE operation_id = %s
            """,
            (metadata.operation_id.value,),
        )
    )
    assert release_op["operation_kind"] == "release_trade_escrow"
    assert release_op["item_stack_operation_state"] == "completed"

    release_ledger = _single_row(
        db.fetch_all(
            """
            SELECT item_stack_id, entry_kind, quantity_delta, quantity_before,
                   quantity_after, stack_version_after, stack_checksum_after
            FROM item_stack_ledger
            WHERE item_stack_operation_id = %s
            """,
            (release_op["item_stack_operation_id"],),
        )
    )
    assert str(release_ledger["item_stack_id"]) == ids.issuer_item_stack_id
    assert release_ledger["entry_kind"] == "trade_escrow_release"
    assert release_ledger["quantity_delta"] > 0
    assert (
        release_ledger["quantity_after"]
        == release_ledger["quantity_before"] + release_ledger["quantity_delta"]
    )
    assert release_ledger["stack_version_after"] >= 1
    assert release_ledger["stack_checksum_after"]


def assert_domain_event(
    db,
    operation_id: str,
    *,
    event_kind: str,
    aggregate_id: str,
) -> None:
    event = _single_row(
        db.fetch_all(
            """
            SELECT event_kind, aggregate_kind, aggregate_id, event_version,
                   payload_reference, publish_state, failure_code
            FROM domain_event_outbox
            WHERE operation_id = %s
            """,
            (operation_id,),
        )
    )
    assert event["event_kind"] == event_kind
    assert event["aggregate_kind"] == "trade_instance"
    assert event["aggregate_id"] == aggregate_id
    assert event["event_version"] == 1
    assert event["payload_reference"] == f"trade_instance:{aggregate_id}"
    assert event["publish_state"] == "pending"
    assert event["failure_code"] is None


def assert_single_trade_side_effects(db, ids, *, settlement_count: int = 0) -> None:
    assert (
        db.table_count(
            "trade_instance", "trade_instance_id = %s", (ids.trade_instance_id,)
        )
        == 1
    )
    assert (
        db.table_count(
            "item_stack_escrow",
            "item_stack_escrow_id = %s",
            (ids.item_stack_escrow_id,),
        )
        == 1
    )
    assert (
        db.table_count(
            "trade_transaction",
            "trade_instance_id = %s",
            (ids.trade_instance_id,),
        )
        == settlement_count
    )


def assert_no_trade_side_effects(db, ids) -> None:
    checks = [
        ("trade_instance", "trade_instance_id = %s", (ids.trade_instance_id,)),
        (
            "item_stack_escrow",
            "item_stack_escrow_id = %s",
            (ids.item_stack_escrow_id,),
        ),
        (
            "trade_transaction",
            "trade_transaction_id = %s OR trade_instance_id = %s",
            (ids.transaction_id, ids.trade_instance_id),
        ),
        ("settlement", "settlement_id = %s", (ids.settlement_id,)),
        (
            "trade_state_change",
            "trade_instance_id = %s",
            (ids.trade_instance_id,),
        ),
        (
            "domain_event_outbox",
            "aggregate_id = %s",
            (ids.trade_instance_id,),
        ),
    ]
    for table, where, params in checks:
        assert db.table_count(table, where, params) == 0


def _single_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(rows) == 1, f"expected one row, got {len(rows)}: {rows}"
    return rows[0]


def _optional_uuid(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _decimal_scalar(db, query: str, params: tuple[Any, ...]) -> Decimal:
    return Decimal(db.scalar(query, params))


def _int_scalar(db, query: str, params: tuple[Any, ...]) -> int:
    return int(db.scalar(query, params))
