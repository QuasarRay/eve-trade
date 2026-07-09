from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from threading import Barrier

import psycopg
import pytest

from helpers import (
    OTHER_STATION_ID,
    RpcFailure,
    accept_payload,
    accept_trade,
    cancel_payload,
    cancel_trade,
    create_trade,
    expect_grpc_error,
    expect_rpc_error,
    fresh_key,
    idempotency_record_row,
    insert_item_stack,
    issue_payload,
    item_escrow_row,
    item_stack_row,
    minimum_numeric_value,
    open_trade_count,
    seed_world,
    settlement_batch_count,
    settlement_batch_row,
    settlement_step_rows,
    table_count,
    total_isk_amount,
    total_item_quantity,
    trade_row,
    uuid_str,
    wallet_row,
)


def test_creating_trade_offer_makes_requested_item_quantity_unavailable_to_seller(db, gateway):
    world = seed_world(db, seller_quantity=10)
    create_trade(gateway, world, quantity=4)

    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 6


def test_runtime_database_role_has_exact_required_privileges_and_no_administration_rights(db, runtime_db, gateway):
    role = runtime_db.fetchone(
        """
        SELECT current_user AS role_name, rolsuper, rolcreatedb, rolcreaterole,
               rolreplication, rolbypassrls
        FROM pg_roles
        WHERE rolname = current_user
        """
    )
    assert role == {
        "role_name": "eve_trade_runtime",
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolreplication": False,
        "rolbypassrls": False,
    }

    expected_insert = {
        "idempotency_record", "request_attempt", "settlement_batch", "settlement_step",
        "wallet", "item_stack", "trade_instance", "wallet_escrow", "item_stack_escrow",
        "wallet_ledger", "item_stack_ledger", "trade_state_change",
    }
    expected_update = {
        "idempotency_record", "request_attempt", "settlement_batch", "settlement_step",
        "wallet", "item_stack", "trade_instance", "wallet_escrow", "item_stack_escrow",
    }
    all_tables = {
        row["table_name"]
        for row in db.fetchall(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    }
    expected = (
        {(table, "SELECT") for table in all_tables}
        | {(table, "INSERT") for table in expected_insert}
        | {(table, "UPDATE") for table in expected_update}
    )
    actual = {
        (row["table_name"], row["privilege_type"])
        for row in runtime_db.fetchall(
            """
            SELECT table_name, privilege_type
            FROM information_schema.role_table_grants
            WHERE grantee = current_user AND table_schema = 'public'
            """
        )
    }
    assert actual == expected

    forbidden_statements = {
        "create schema": "CREATE SCHEMA runtime_role_must_not_create_schema",
        "create table": "CREATE TABLE runtime_role_must_not_create_table (id bigint)",
        "alter table": "ALTER TABLE capsuleer ADD COLUMN runtime_role_forbidden bigint",
        "drop table": "DROP TABLE capsuleer",
        "mutate catalog": "UPDATE capsuleer SET capsuleer_name = capsuleer_name",
    }
    for name, statement in forbidden_statements.items():
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime_db.execute(statement)

    public_grants_before = db.scalar(
        """
        SELECT count(*) FROM information_schema.table_privileges
        WHERE grantee = 'PUBLIC' AND table_schema = 'public'
          AND table_name = 'capsuleer' AND privilege_type = 'SELECT'
        """
    )
    runtime_db.execute("GRANT SELECT ON capsuleer TO PUBLIC")
    public_grants_after = db.scalar(
        """
        SELECT count(*) FROM information_schema.table_privileges
        WHERE grantee = 'PUBLIC' AND table_schema = 'public'
          AND table_name = 'capsuleer' AND privilege_type = 'SELECT'
        """
    )
    assert public_grants_before == 0
    assert public_grants_after == public_grants_before

    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=1, unit_price_isk=1)
    accept_trade(gateway, world, trade)
    ledger_id = db.scalar("SELECT item_stack_ledger_id FROM item_stack_ledger LIMIT 1")
    for statement in (
        "UPDATE item_stack_ledger SET quantity_after = quantity_after WHERE item_stack_ledger_id = %s",
        "DELETE FROM item_stack_ledger WHERE item_stack_ledger_id = %s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime_db.execute(statement, (ledger_id,))
    wallet_ledger_id = db.scalar("SELECT wallet_ledger_id FROM wallet_ledger LIMIT 1")
    for statement in (
        "UPDATE wallet_ledger SET isk_amount_after = isk_amount_after WHERE wallet_ledger_id = %s",
        "DELETE FROM wallet_ledger WHERE wallet_ledger_id = %s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime_db.execute(statement, (wallet_ledger_id,))
    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 9


def test_market_database_role_is_readonly_and_cannot_mutate_settlement_state(db, market_db):
    required_select = {
        "item_stack",
        "wallet",
        "trade_instance",
        "item_stack_escrow",
        "idempotency_record",
        "settlement_batch",
        "settlement_step",
    }
    actual = {
        (row["table_name"], row["privilege_type"])
        for row in market_db.fetchall(
            """
            SELECT table_name, privilege_type
            FROM information_schema.role_table_grants
            WHERE grantee = current_user AND table_schema = 'public'
            """
        )
    }
    assert {(table, "SELECT") for table in required_select}.issubset(actual)
    assert all(privilege == "SELECT" for _, privilege in actual)

    assert market_db.scalar("SELECT count(*) FROM item_stack") >= 0
    protected_writes = (
        "UPDATE wallet SET isk_amount = isk_amount",
        "UPDATE item_stack SET quantity = quantity",
        "UPDATE trade_instance SET trade_state = trade_state",
        "INSERT INTO idempotency_record (idempotency_key, request_fingerprint, request_kind, idempotency_state, created_by_service) VALUES ('market-role-write-test', 'x', 'x', 'IN_PROGRESS', 'test')",
        "DELETE FROM settlement_step",
        "CREATE TABLE market_role_must_not_create_table (id bigint)",
    )
    for statement in protected_writes:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            market_db.execute(statement)


def test_creating_trade_offer_keeps_seller_non_offered_item_quantity_available(db, gateway):
    world = seed_world(db, seller_second_quantity=8)
    create_trade(gateway, world, quantity=4)

    assert item_stack_row(db, world.seller_other_stack_id)["quantity"] == 8


def test_creating_trade_offer_persists_an_open_trade(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    row = trade_row(db, trade)
    assert row["trade_state"] == "OPEN"
    assert row["remaining_quantity"] == 4
    assert open_trade_count(db) == 1


def test_creating_trade_offer_rejects_zero_item_quantity(db, gateway):
    world = seed_world(db)

    expect_rpc_error(
        lambda: gateway.issue_trade_instance(issue_payload(world, quantity=0)),
        code="invalid_argument",
        contains="quantity",
    )
    assert table_count(db, "trade_instance") == 0


def test_creating_trade_offer_rejects_negative_item_quantity(db, gateway):
    world = seed_world(db)

    expect_rpc_error(
        lambda: gateway.issue_trade_instance(issue_payload(world, quantity=-1)),
        code="invalid_argument",
        contains="quantity",
    )
    assert table_count(db, "trade_instance") == 0


def test_creating_trade_offer_rejects_more_items_than_seller_owns(db, gateway):
    world = seed_world(db, seller_quantity=5)

    expect_rpc_error(
        lambda: gateway.issue_trade_instance(
            issue_payload(world, quantity=6, item_stack_quantity=5)
        ),
        code="invalid_argument",
        contains="quantity",
    )
    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 5


def test_creating_trade_offer_rejects_item_stack_not_owned_by_seller(db, gateway):
    world = seed_world(db)

    expect_rpc_error(
        lambda: gateway.issue_trade_instance(
            issue_payload(world, item_stack_owner_id=world.other_id)
        ),
        code="principal_mismatch",
        contains="owner",
    )
    assert table_count(db, "trade_instance") == 0


def test_creating_trade_offer_rejects_item_stack_in_wrong_station(db, gateway):
    world = seed_world(db)

    expect_rpc_error(
        lambda: gateway.issue_trade_instance(
            issue_payload(world, station_id=OTHER_STATION_ID)
        ),
        code="invalid_argument",
        contains="station",
    )
    assert table_count(db, "trade_instance") == 0


def test_creating_trade_offer_rejects_invalid_unit_price(db, gateway):
    world = seed_world(db)

    expect_rpc_error(
        lambda: gateway.issue_trade_instance(issue_payload(world, unit_price_isk=-1)),
        code="invalid_argument",
        contains="unit_price_isk",
    )
    assert table_count(db, "trade_instance") == 0


def test_creating_trade_offer_does_not_change_wallet_balances(db, gateway):
    world = seed_world(db, seller_isk=100, buyer_isk=1_000)
    seller_before = wallet_row(db, world.seller_wallet_id)["isk_amount"]
    buyer_before = wallet_row(db, world.buyer_wallet_id)["isk_amount"]

    create_trade(gateway, world, quantity=4)

    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == seller_before
    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == buyer_before


def test_accepting_trade_transfers_requested_item_quantity_to_buyer(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    response = accept_trade(gateway, world, trade)

    buyer_stack = item_stack_row(db, response["buyerDestinationItemStackId"])
    assert buyer_stack["owner_id"] == world.buyer_id
    assert buyer_stack["quantity"] == 4


def test_accepting_trade_transfers_correct_isk_amount_to_seller(db, gateway):
    world = seed_world(db, seller_isk=100, buyer_isk=1_000)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    accept_trade(gateway, world, trade)

    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == 200


def test_accepting_trade_debits_buyer_wallet_by_quantity_times_unit_price(db, gateway):
    world = seed_world(db, buyer_isk=1_000)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    accept_trade(gateway, world, trade)

    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 900


def test_accepting_trade_creates_buyer_item_stack_when_buyer_has_no_matching_stack(db, gateway):
    world = seed_world(db, buyer_stack_quantity=None)
    trade = create_trade(gateway, world, quantity=4)

    response = accept_trade(gateway, world, trade)

    buyer_stack = item_stack_row(db, response["buyerDestinationItemStackId"])
    assert buyer_stack["owner_id"] == world.buyer_id
    assert buyer_stack["item_type_id"] == world.item_type_id
    assert buyer_stack["quantity"] == 4


def test_accepting_trade_merges_items_into_existing_buyer_stack_when_matching_stack_exists(db, gateway):
    world = seed_world(db, buyer_stack_quantity=3)
    trade = create_trade(gateway, world, quantity=4)

    accept_trade(
        gateway,
        world,
        trade,
        buyer_destination_item_stack_id=world.buyer_stack_id,
    )

    assert item_stack_row(db, world.buyer_stack_id)["quantity"] == 7


def test_accepting_partial_trade_keeps_trade_outstanding(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=10)

    accept_trade(gateway, world, trade, quantity=4)

    assert trade_row(db, trade)["trade_state"] == "OPEN"


def test_accepting_partial_trade_reduces_available_trade_quantity(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=10)

    accept_trade(gateway, world, trade, quantity=4)

    assert item_escrow_row(db, trade)["quantity"] == 6


def test_accepting_partial_trade_updates_persisted_remaining_quantity(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=10)

    accept_trade(gateway, world, trade, quantity=4)

    assert trade_row(db, trade)["remaining_quantity"] == 6


def test_accepting_partial_trade_keeps_database_available_quantity_consistent(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=10)

    accept_trade(gateway, world, trade, quantity=4)

    assert trade_row(db, trade)["remaining_quantity"] == item_escrow_row(db, trade)["quantity"]


def test_accepting_full_remaining_trade_quantity_marks_trade_completed(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    accept_trade(gateway, world, trade)

    assert trade_row(db, trade)["trade_state"] == "COMPLETED"


def test_accepting_trade_does_not_complete_trade_while_item_escrow_quantity_remains_positive(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=10)

    accept_trade(gateway, world, trade, quantity=4)

    assert item_escrow_row(db, trade)["quantity"] > 0
    assert trade_row(db, trade)["trade_state"] == "OPEN"


def test_accepting_trade_completes_trade_when_item_escrow_quantity_becomes_zero(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    accept_trade(gateway, world, trade)

    assert item_escrow_row(db, trade)["quantity"] == 0
    assert trade_row(db, trade)["trade_state"] == "COMPLETED"


def test_accepting_full_remaining_trade_leaves_zero_available_quantity(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    accept_trade(gateway, world, trade)

    assert item_escrow_row(db, trade)["quantity"] == 0
    assert trade_row(db, trade)["remaining_quantity"] == 0


def test_accepting_trade_rejects_quantity_above_available_trade_quantity(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade, quantity=5),
        code="failed_precondition",
        contains="requested 5",
    )
    assert item_escrow_row(db, trade)["quantity"] == 4


def test_accepting_trade_rejects_zero_quantity(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade, quantity=0),
        code="invalid_argument",
        contains="quantity_requested",
    )


def test_accepting_trade_rejects_negative_quantity(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade, quantity=-1),
        code="invalid_argument",
        contains="quantity_requested",
    )


def test_accepting_trade_rejects_when_buyer_has_insufficient_isk(db, gateway):
    world = seed_world(db, buyer_isk=50)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="requested 100",
    )
    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 50


def test_accepting_trade_rejects_when_trade_is_cancelled(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)
    cancel_trade(gateway, world, trade)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="cancelled",
    )


def test_accepting_trade_rejects_when_trade_is_already_completed(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)
    accept_trade(gateway, world, trade)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade, idempotency_key=fresh_key("accept-again")),
        code="failed_precondition",
        contains="completed",
    )


def test_accepting_trade_rejects_when_buyer_is_seller_if_self_purchase_is_disallowed(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    expect_rpc_error(
        lambda: accept_trade(
            gateway,
            world,
            trade,
            buyer_capsuleer_id=world.seller_id,
            buyer_wallet_id=world.seller_wallet_id,
        ),
        code="invalid_argument",
        contains="buyer and seller must differ",
    )


def test_cancelling_trade_returns_remaining_item_quantity_to_seller(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=4)

    cancel_trade(gateway, world, trade)

    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 10


def test_cancelling_trade_makes_remaining_item_quantity_available_to_seller(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=4)

    cancel_trade(gateway, world, trade)

    assert item_stack_row(db, world.seller_stack_id)["stack_state"] == "ACTIVE"
    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 10


def test_cancelling_trade_marks_trade_unavailable_to_buyers(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    cancel_trade(gateway, world, trade)

    assert trade_row(db, trade)["trade_state"] == "CANCELLED"
    assert item_escrow_row(db, trade)["quantity"] == 0


def test_cancelling_partially_accepted_trade_refunds_only_remaining_item_quantity(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=10)
    accept_trade(gateway, world, trade, quantity=4)

    cancel_trade(gateway, world, trade)

    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 6


def test_cancelling_partially_accepted_trade_does_not_reverse_already_completed_purchase(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=10)
    response = accept_trade(gateway, world, trade, quantity=4)

    cancel_trade(gateway, world, trade)

    assert item_stack_row(db, response["buyerDestinationItemStackId"])["quantity"] == 4


def test_cancelling_trade_rejects_mismatched_claimed_canceller(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    expect_rpc_error(
        lambda: cancel_trade(
            gateway,
            world,
            trade,
            cancelled_by_capsuleer_id=world.buyer_id,
        ),
        code="permission_denied",
        contains="issuer",
    )


def test_cancelling_trade_rejects_already_completed_trade(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)
    accept_trade(gateway, world, trade)

    expect_rpc_error(
        lambda: cancel_trade(gateway, world, trade),
        code="failed_precondition",
        contains="completed",
    )


def test_cancelling_trade_rejects_already_cancelled_trade(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)
    cancel_trade(gateway, world, trade)

    expect_rpc_error(
        lambda: cancel_trade(gateway, world, trade, idempotency_key=fresh_key("cancel-again")),
        code="failed_precondition",
        contains="cancelled",
    )


def test_cancelling_trade_does_not_change_wallet_balances(db, gateway):
    world = seed_world(db, seller_isk=100, buyer_isk=1_000)
    trade = create_trade(gateway, world, quantity=4)
    seller_before = wallet_row(db, world.seller_wallet_id)["isk_amount"]
    buyer_before = wallet_row(db, world.buyer_wallet_id)["isk_amount"]

    cancel_trade(gateway, world, trade)

    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == seller_before
    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == buyer_before


def test_retrying_create_trade_offer_does_not_duplicate_trade(db, gateway):
    world = seed_world(db)
    key = fresh_key("issue-retry")
    payload = issue_payload(world, idempotency_key=key)

    first = gateway.issue_trade_instance(payload)
    second = gateway.issue_trade_instance(payload)

    assert second == first
    assert table_count(db, "trade_instance") == 1
    assert settlement_batch_count(db, key) == 1


def test_retrying_create_trade_offer_does_not_duplicate_item_escrow(db, gateway):
    world = seed_world(db)
    key = fresh_key("issue-retry")
    payload = issue_payload(world, idempotency_key=key)

    first = gateway.issue_trade_instance(payload)
    second = gateway.issue_trade_instance(payload)

    assert second == first
    assert table_count(db, "item_stack_escrow") == 1
    assert settlement_batch_count(db, key) == 1


def test_retrying_accept_trade_does_not_transfer_items_twice(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)
    key = fresh_key("accept-retry")
    payload = accept_payload(world, trade, idempotency_key=key)

    first = gateway.accept_trade_instance(payload)
    second = gateway.accept_trade_instance(payload)

    assert second == first
    assert item_stack_row(db, first["buyerDestinationItemStackId"])["quantity"] == 4
    assert settlement_batch_count(db, key) == 1


def test_retrying_accept_trade_does_not_transfer_isk_twice(db, gateway):
    world = seed_world(db, seller_isk=100, buyer_isk=1_000)
    trade = create_trade(gateway, world, quantity=4)
    key = fresh_key("accept-retry")
    payload = accept_payload(world, trade, idempotency_key=key)

    first = gateway.accept_trade_instance(payload)
    second = gateway.accept_trade_instance(payload)

    assert second == first
    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == 200
    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 900
    assert settlement_batch_count(db, key) == 1


def test_retrying_cancel_trade_does_not_refund_items_twice(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=4)
    key = fresh_key("cancel-retry")
    payload = cancel_payload(world, trade, idempotency_key=key)

    first = gateway.cancel_trade_instance(payload)
    second = gateway.cancel_trade_instance(payload)

    assert second == first
    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 10
    assert settlement_batch_count(db, key) == 1


def test_same_interaction_id_with_different_create_trade_payload_is_rejected_at_edge(db, gateway):
    world = seed_world(db)
    key = fresh_key("issue-conflict")
    gateway.issue_trade_instance(issue_payload(world, idempotency_key=key, quantity=3))

    expect_rpc_error(
        lambda: gateway.issue_trade_instance(
            issue_payload(world, idempotency_key=key, quantity=4)
        ),
        code="replay",
        contains="replay",
    )
    assert settlement_batch_count(db, key) == 1


def test_same_interaction_id_with_different_accept_trade_payload_is_rejected_at_edge(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=10)
    key = fresh_key("accept-conflict")
    accept_trade(gateway, world, trade, idempotency_key=key, quantity=3)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade, idempotency_key=key, quantity=4),
        code="replay",
        contains="replay",
    )
    assert settlement_batch_count(db, key) == 1


def test_same_interaction_id_with_different_cancel_trade_payload_is_rejected_at_edge(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=10)
    key = fresh_key("cancel-conflict")
    cancel_trade(gateway, world, trade, idempotency_key=key)

    expect_rpc_error(
        lambda: cancel_trade(
            gateway,
            world,
            trade,
            idempotency_key=key,
            cancelled_by_capsuleer_id=world.other_id,
        ),
        code="replay",
        contains="replay",
    )
    assert settlement_batch_count(db, key) == 1


def test_retried_successful_request_returns_cached_response_without_second_settlement(db, gateway):
    world = seed_world(db)
    key = fresh_key("issue-retry")
    payload = issue_payload(world, idempotency_key=key)

    first = gateway.issue_trade_instance(payload)
    second = gateway.issue_trade_instance(payload)

    assert first["status"] in {"accepted", "queued"}
    assert second == first
    assert table_count(db, "trade_instance") == 1
    assert settlement_batch_count(db, key) == 1


def test_failed_create_trade_offer_does_not_create_open_trade(db, gateway):
    world = seed_world(db)

    expect_rpc_error(
        lambda: gateway.issue_trade_instance(issue_payload(world, quantity=0)),
        code="invalid_argument",
        contains="quantity",
    )

    assert open_trade_count(db) == 0


def test_failed_create_trade_offer_does_not_remove_items_from_seller(db, gateway):
    world = seed_world(db, seller_quantity=10)

    expect_rpc_error(
        lambda: gateway.issue_trade_instance(issue_payload(world, quantity=0)),
        code="invalid_argument",
        contains="quantity",
    )

    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 10


def test_failed_accept_trade_does_not_debit_buyer_wallet(db, gateway):
    world = seed_world(db, buyer_isk=50)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="requested 100",
    )

    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 50


def test_failed_accept_trade_does_not_credit_seller_wallet(db, gateway):
    world = seed_world(db, seller_isk=100, buyer_isk=50)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="requested 100",
    )

    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == 100


def test_failed_accept_trade_does_not_transfer_items_to_buyer(db, gateway):
    world = seed_world(db, buyer_isk=50)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="requested 100",
    )

    assert table_count(db, "item_stack") == 3


def test_failed_accept_trade_does_not_reduce_available_trade_quantity(db, gateway):
    world = seed_world(db, buyer_isk=50)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="requested 100",
    )

    assert item_escrow_row(db, trade)["quantity"] == 4


def test_failed_accept_trade_does_not_complete_trade(db, gateway):
    world = seed_world(db, buyer_isk=50)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="requested 100",
    )

    assert trade_row(db, trade)["trade_state"] == "OPEN"


def test_failed_accept_rolls_back_wallet_and_item_changes(db, gateway):
    world = seed_world(db, buyer_isk=50)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="requested 100",
    )

    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 50
    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == 100
    assert item_escrow_row(db, trade)["quantity"] == 4
    assert trade_row(db, trade)["remaining_quantity"] == 4


def test_failed_accept_records_failed_batch_after_rollback(db, gateway):
    world = seed_world(db, buyer_isk=50, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    key = fresh_key("accept-failed-diagnostics")

    expect_rpc_error(
        lambda: accept_trade(
            gateway,
            world,
            trade,
            idempotency_key=key,
            buyer_destination_item_stack_id=world.buyer_stack_id,
        ),
        code="failed_precondition",
        contains="requested 100",
    )

    batch = settlement_batch_row(db, key)
    assert batch["batch_state"] == "FAILED"
    assert batch["failure_code"] == "INSUFFICIENT_FUNDS"


def test_failed_accept_records_failed_step_after_rollback(db, gateway):
    world = seed_world(db, buyer_isk=50, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    key = fresh_key("accept-failed-step")

    expect_rpc_error(
        lambda: accept_trade(
            gateway,
            world,
            trade,
            idempotency_key=key,
            buyer_destination_item_stack_id=world.buyer_stack_id,
        ),
        code="failed_precondition",
        contains="requested 100",
    )

    steps = settlement_step_rows(db, settlement_batch_row(db, key)["settlement_batch_id"])
    assert any(step["step_state"] == "FAILED" for step in steps)
    assert all(step["step_state"] != "COMPLETED" for step in steps)


def test_failed_accept_keeps_original_error_code_for_diagnostics(db, gateway):
    world = seed_world(db, buyer_isk=50, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    key = fresh_key("accept-failed-error-code")

    expect_rpc_error(
        lambda: accept_trade(
            gateway,
            world,
            trade,
            idempotency_key=key,
            buyer_destination_item_stack_id=world.buyer_stack_id,
        ),
        code="failed_precondition",
        contains="requested 100",
    )

    assert settlement_batch_row(db, key)["failure_code"] == "INSUFFICIENT_FUNDS"
    assert idempotency_record_row(db, key)["failure_code"] == "INSUFFICIENT_FUNDS"


def test_failed_cancel_trade_does_not_refund_partial_item_quantity(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=4)

    expect_rpc_error(
        lambda: cancel_trade(
            gateway,
            world,
            trade,
            cancelled_by_capsuleer_id=world.other_id,
        ),
        code="permission_denied",
        contains="issuer",
    )

    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 6
    assert item_escrow_row(db, trade)["quantity"] == 4


def test_failed_cancel_trade_does_not_hide_trade_from_buyers_unless_refund_succeeds(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=4)

    expect_rpc_error(
        lambda: cancel_trade(
            gateway,
            world,
            trade,
            cancelled_by_capsuleer_id=world.other_id,
        ),
        code="permission_denied",
        contains="issuer",
    )

    assert trade_row(db, trade)["trade_state"] == "OPEN"


def test_concurrent_accepts_cannot_sell_more_than_available_quantity(db, gateway):
    world = seed_world(db, seller_quantity=10, buyer_isk=10_000, other_isk=10_000)
    trade = create_trade(gateway, world, quantity=10)
    owned_before = sum(
        row["quantity"]
        for row in db.fetchall(
            "SELECT quantity FROM item_stack WHERE owner_id IN (%s, %s)",
            (world.buyer_id, world.other_id),
        )
    )

    start = Barrier(2)

    def accept_as(wallet_id, buyer_id):
        start.wait(timeout=5)
        return gateway.accept_trade_instance(
            accept_payload(
                world,
                trade,
                quantity=7,
                buyer_capsuleer_id=buyer_id,
                buyer_wallet_id=wallet_id,
                idempotency_key=fresh_key("accept-concurrent"),
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(accept_as, world.buyer_wallet_id, world.buyer_id),
            executor.submit(accept_as, world.other_wallet_id, world.other_id),
        ]
        results = []
        failures = []
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - the assertion is about mixed outcomes.
                failures.append(exc)

    owned_after = sum(
        row["quantity"]
        for row in db.fetchall(
            "SELECT quantity FROM item_stack WHERE owner_id IN (%s, %s)",
            (world.buyer_id, world.other_id),
        )
    )
    assert len(results) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], RpcFailure)
    assert failures[0].code == "failed_precondition"
    assert owned_after - owned_before == 7
    assert item_escrow_row(db, trade)["quantity"] == 3
    assert trade_row(db, trade)["remaining_quantity"] == 3


def test_concurrent_accepts_cannot_make_buyer_wallet_negative(db, gateway):
    world = seed_world(db, seller_quantity=10, buyer_isk=70)
    trade = create_trade(gateway, world, quantity=10, unit_price_isk=10)

    start = Barrier(2)

    def accept_with_overlapping_wallet():
        start.wait(timeout=5)
        return gateway.accept_trade_instance(
            accept_payload(
                world,
                trade,
                quantity=5,
                idempotency_key=fresh_key("accept-wallet-concurrent"),
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(accept_with_overlapping_wallet) for _ in range(2)]
        successes = 0
        failures = []
        for future in as_completed(futures):
            try:
                future.result()
                successes += 1
            except Exception as exc:  # noqa: BLE001 - validated below to reject false positives.
                failures.append(exc)

    assert successes == 1
    assert len(failures) == 1
    assert isinstance(failures[0], RpcFailure)
    assert failures[0].code == "failed_precondition"
    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 20
    assert item_escrow_row(db, trade)["quantity"] == 5
    assert trade_row(db, trade)["remaining_quantity"] == 5


def test_concurrent_full_accept_and_cancel_have_exactly_one_winner_and_conserve_state(db, gateway):
    world = seed_world(db, seller_quantity=10, seller_isk=100, buyer_isk=1_000)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    items_before = total_item_quantity(db)
    isk_before = total_isk_amount(db)
    start = Barrier(2)

    def run(name, call):
        start.wait(timeout=5)
        try:
            return name, call(), None
        except Exception as exc:  # noqa: BLE001 - exact failure is asserted below.
            return name, None, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                run,
                "accept",
                lambda: accept_trade(
                    gateway,
                    world,
                    trade,
                    quantity=4,
                    idempotency_key=fresh_key("accept-cancel-accept"),
                ),
            ),
            executor.submit(
                run,
                "cancel",
                lambda: cancel_trade(
                    gateway,
                    world,
                    trade,
                    idempotency_key=fresh_key("accept-cancel-cancel"),
                ),
            ),
        ]
        outcomes = [future.result() for future in futures]

    successes = [(name, result) for name, result, error in outcomes if error is None]
    failures = [(name, error) for name, result, error in outcomes if error is not None]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0][1], RpcFailure)
    assert failures[0][1].code == "failed_precondition"
    assert total_item_quantity(db) == items_before
    assert total_isk_amount(db) == isk_before

    final_trade = trade_row(db, trade)
    if successes[0][0] == "accept":
        assert final_trade["trade_state"] == "COMPLETED"
        assert final_trade["remaining_quantity"] == 0
        assert item_escrow_row(db, trade)["quantity"] == 0
        assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 900
        assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == 200
        assert item_stack_row(db, world.seller_stack_id)["quantity"] == 6
    else:
        assert final_trade["trade_state"] == "CANCELLED"
        assert final_trade["remaining_quantity"] == 0
        assert item_escrow_row(db, trade)["quantity"] == 0
        assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 1_000
        assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == 100
        assert item_stack_row(db, world.seller_stack_id)["quantity"] == 10


def test_total_item_quantity_is_preserved_after_trade_offer_creation(db, gateway):
    world = seed_world(db, seller_quantity=10)
    before = total_item_quantity(db)

    create_trade(gateway, world, quantity=4)

    assert total_item_quantity(db) == before


def test_total_item_quantity_is_preserved_after_trade_acceptance(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=4)
    before = total_item_quantity(db)

    accept_trade(gateway, world, trade)

    assert total_item_quantity(db) == before


def test_total_item_quantity_is_preserved_after_trade_cancellation(db, gateway):
    world = seed_world(db, seller_quantity=10)
    trade = create_trade(gateway, world, quantity=4)
    before = total_item_quantity(db)

    cancel_trade(gateway, world, trade)

    assert total_item_quantity(db) == before


def test_total_isk_amount_is_preserved_after_trade_acceptance(db, gateway):
    world = seed_world(db, seller_isk=100, buyer_isk=1_000)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    before = total_isk_amount(db)

    accept_trade(gateway, world, trade)

    assert total_isk_amount(db) == before


def test_no_wallet_balance_becomes_negative_after_successful_request(db, gateway):
    world = seed_world(db, buyer_isk=1_000)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    accept_trade(gateway, world, trade)

    assert minimum_numeric_value(db, "wallet", "isk_amount") >= 0


def test_no_item_stack_quantity_becomes_negative_after_successful_request(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    accept_trade(gateway, world, trade)

    assert minimum_numeric_value(db, "item_stack", "quantity") >= 0


def test_available_trade_quantity_never_becomes_negative(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    accept_trade(gateway, world, trade)

    assert minimum_numeric_value(db, "item_stack_escrow", "quantity") >= 0
    assert minimum_numeric_value(db, "trade_instance", "remaining_quantity") >= 0


def test_completed_trade_has_zero_available_item_quantity(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    accept_trade(gateway, world, trade)

    assert trade_row(db, trade)["trade_state"] == "COMPLETED"
    assert item_escrow_row(db, trade)["quantity"] == 0
    assert trade_row(db, trade)["remaining_quantity"] == 0


def test_completed_trade_cannot_have_positive_available_quantity(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    accept_trade(gateway, world, trade)

    assert (
        db.scalar(
            """
            SELECT count(*)
            FROM trade_instance
            WHERE trade_state = 'COMPLETED'
              AND remaining_quantity > 0
            """
        )
        == 0
    )


def test_outstanding_trade_can_have_positive_available_item_quantity(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    assert trade_row(db, trade)["trade_state"] == "OPEN"
    assert item_escrow_row(db, trade)["quantity"] == 4


def test_cancelled_trade_has_no_available_quantity_for_buyers(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    cancel_trade(gateway, world, trade)

    assert trade_row(db, trade)["trade_state"] == "CANCELLED"
    assert item_escrow_row(db, trade)["quantity"] == 0


def test_claimed_issuer_must_match_canonical_item_stack_owner(db, gateway):
    world = seed_world(db)

    expect_rpc_error(
        lambda: gateway.issue_trade_instance(
            issue_payload(
                world,
                item_stack_id=world.other_stack_id,
                item_stack_owner_id=world.seller_id,
            )
        ),
        code="invalid_argument",
        contains="owner",
    )


def test_claimed_canceller_must_match_canonical_trade_issuer(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    expect_rpc_error(
        lambda: cancel_trade(
            gateway,
            world,
            trade,
            cancelled_by_capsuleer_id=world.other_id,
        ),
        code="permission_denied",
        contains="issuer",
    )


def test_buyer_receives_exactly_the_requested_item_quantity(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    response = accept_trade(gateway, world, trade, quantity=3)

    assert item_stack_row(db, response["buyerDestinationItemStackId"])["quantity"] == 3


def test_seller_receives_exactly_quantity_times_trade_price(db, gateway):
    world = seed_world(db, seller_isk=100, buyer_isk=1_000)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    accept_trade(gateway, world, trade)

    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == 200


def test_trade_acceptance_uses_trade_price_not_client_supplied_price(db, gateway):
    world = seed_world(db, seller_isk=100, buyer_isk=1_000)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    accept_trade(gateway, world, trade, unit_price_isk=1)

    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == 200
    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 900


def test_trade_acceptance_uses_trade_item_type_not_client_supplied_item_type(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    response = accept_trade(gateway, world, trade, item_type_id=world.other_item_type_id)

    assert item_stack_row(db, response["buyerDestinationItemStackId"])["item_type_id"] == world.item_type_id


def test_trade_acceptance_uses_trade_station_not_client_supplied_station(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    response = accept_trade(gateway, world, trade, station_id=world.other_station_id)

    assert item_stack_row(db, response["buyerDestinationItemStackId"])["station_id"] == world.station_id


def test_trade_acceptance_rejects_conflicting_client_supplied_seller_actor(db, gateway):
    world = seed_world(db, seller_isk=100)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    expect_rpc_error(
        lambda: accept_trade(
            gateway,
            world,
            trade,
            seller_capsuleer_id=world.other_id,
            seller_wallet_id=world.other_wallet_id,
        ),
        code="principal_mismatch",
        contains="seller_capsuleer_id",
    )

    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == 100
    assert wallet_row(db, world.other_wallet_id)["isk_amount"] == 1_000
    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 1_000
    assert item_escrow_row(db, trade)["quantity"] == 4


def test_create_trade_offer_returns_clear_error_for_insufficient_item_quantity(db, gateway):
    world = seed_world(db, seller_quantity=5)

    error = expect_rpc_error(
        lambda: gateway.issue_trade_instance(
            issue_payload(world, quantity=6, item_stack_quantity=5)
        ),
        code="invalid_argument",
        contains="item stack quantity",
    )
    assert "item stack quantity" in error.message


def test_create_trade_offer_returns_clear_error_for_invalid_item_stack_owner(db, gateway):
    world = seed_world(db)

    error = expect_rpc_error(
        lambda: gateway.issue_trade_instance(
            issue_payload(world, item_stack_owner_id=world.other_id)
        ),
        code="principal_mismatch",
        contains="owner",
    )
    assert "owner" in error.message


def test_accept_trade_returns_clear_error_for_insufficient_wallet_balance(db, gateway):
    world = seed_world(db, buyer_isk=50)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    error = expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="requested 100",
    )

    assert error.code == "failed_precondition"
    assert "wallet" in error.message
    assert "requested 100" in error.message


def test_accept_trade_returns_clear_error_for_unavailable_trade_quantity(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    error = expect_rpc_error(
        lambda: accept_trade(gateway, world, trade, quantity=5),
        code="failed_precondition",
        contains="requested 5",
    )

    assert error.code == "failed_precondition"
    assert "item_stack_escrow" in error.message
    assert "requested 5" in error.message


def test_accept_trade_returns_clear_error_for_cancelled_trade(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)
    cancel_trade(gateway, world, trade)

    error = expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="cancelled",
    )

    assert error.code == "failed_precondition"
    assert "cancelled" in error.message


def test_accept_trade_returns_clear_error_for_completed_trade(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)
    accept_trade(gateway, world, trade)

    error = expect_rpc_error(
        lambda: accept_trade(gateway, world, trade),
        code="failed_precondition",
        contains="completed",
    )

    assert error.code == "failed_precondition"
    assert "completed" in error.message.lower()


def test_cancel_trade_returns_clear_error_for_non_seller_caller(db, gateway):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)

    error = expect_rpc_error(
        lambda: cancel_trade(
            gateway,
            world,
            trade,
            cancelled_by_capsuleer_id=world.buyer_id,
        ),
        code="permission_denied",
        contains="issuer",
    )

    assert "issuer" in error.message.lower()


def test_rejected_request_does_not_return_success_status(db, gateway):
    world = seed_world(db)
    settlement_batches_before = table_count(db, "settlement_batch")

    error = expect_rpc_error(
        lambda: gateway.issue_trade_instance(issue_payload(world, quantity=0)),
        code="invalid_argument",
        contains="quantity",
    )

    assert error.status_code == 400
    assert table_count(db, "settlement_batch") == settlement_batches_before


def test_authenticated_buyer_cannot_impersonate_seller_at_udp_edge(db, authenticated_edge):
    world = seed_world(db)
    key_id = os.environ.get("EVE_TRADE_EDGE_BUYER_KEY_ID")
    secret = os.environ.get("EVE_TRADE_EDGE_BUYER_SECRET")
    if not key_id or not secret:
        pytest.skip("set buyer edge credential to run authenticated impersonation test")
    packet = {
        "schema_version": "eve-trade-gui.v1",
        "interaction_id": fresh_key("authenticated-impersonation"),
        "ui": {"window": "regional_market", "action": "market_place_sell_order"},
        "input": {
            "issued_by_capsuleer_id": world.seller_id,
            "item_stack": {
                "item_stack_id": world.seller_stack_id,
                "owner_id": world.seller_id,
                "item_type_id": world.item_type_id,
                "station_id": world.station_id,
                "quantity": 100,
            },
            "quantity": 1,
            "unit_price_isk": 1,
        },
    }

    response = authenticated_edge.submit(packet, key_id, secret)

    assert response["code"] == "principal_mismatch"
    assert "authenticated capsuleer" in response["message"]
    assert table_count(db, "trade_instance") == 0
    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 10


def test_authenticated_udp_edge_rejects_unknown_wrong_key_and_cross_principal_actions(db, authenticated_edge):
    world = seed_world(db)
    required_names = (
        "EVE_TRADE_EDGE_SELLER_KEY_ID",
        "EVE_TRADE_EDGE_SELLER_SECRET",
        "EVE_TRADE_EDGE_BUYER_KEY_ID",
        "EVE_TRADE_EDGE_BUYER_SECRET",
        "EVE_TRADE_EDGE_OTHER_KEY_ID",
        "EVE_TRADE_EDGE_OTHER_SECRET",
    )
    if any(not os.environ.get(name) for name in required_names):
        pytest.skip("set seller, buyer, and other edge credentials to run the principal matrix")
    credentials = {
        "seller": (
            os.environ["EVE_TRADE_EDGE_SELLER_KEY_ID"],
            os.environ["EVE_TRADE_EDGE_SELLER_SECRET"],
        ),
        "buyer": (
            os.environ["EVE_TRADE_EDGE_BUYER_KEY_ID"],
            os.environ["EVE_TRADE_EDGE_BUYER_SECRET"],
        ),
        "other": (
            os.environ["EVE_TRADE_EDGE_OTHER_KEY_ID"],
            os.environ["EVE_TRADE_EDGE_OTHER_SECRET"],
        ),
    }

    def packet(action, actor_field, actor_id, suffix):
        return {
            "schema_version": "eve-trade-gui.v1",
            "interaction_id": fresh_key(suffix),
            "ui": {"window": "regional_market", "action": action},
            "input": {actor_field: actor_id, "quantity": 1},
        }

    cases = [
        ("unknown key", packet("market_place_sell_order", "issued_by_capsuleer_id", world.seller_id, "unknown-key"), "unknown", "wrong", "invalid_signature"),
        ("seller key wrong HMAC", packet("market_place_sell_order", "issued_by_capsuleer_id", world.seller_id, "wrong-hmac"), credentials["seller"][0], "wrong", "invalid_signature"),
        ("seller cannot act as buyer", packet("market_buy_from_sell_order", "buyer_capsuleer_id", world.buyer_id, "seller-as-buyer"), *credentials["seller"], "principal_mismatch"),
        ("buyer cannot act as seller", packet("market_place_sell_order", "issued_by_capsuleer_id", world.seller_id, "buyer-as-seller"), *credentials["buyer"], "principal_mismatch"),
        ("other cannot act as seller", packet("market_place_sell_order", "issued_by_capsuleer_id", world.seller_id, "other-as-seller"), *credentials["other"], "principal_mismatch"),
        ("other cannot act as buyer", packet("market_buy_from_sell_order", "buyer_capsuleer_id", world.buyer_id, "other-as-buyer"), *credentials["other"], "principal_mismatch"),
    ]
    for name, request, key_id, secret, expected_code in cases:
        response = authenticated_edge.submit(request, key_id, secret)
        assert response["code"] == expected_code, name
        assert response["interaction_id"] == request["interaction_id"], name

    assert table_count(db, "trade_instance") == 0
    assert item_stack_row(db, world.seller_stack_id)["quantity"] == 10


def test_settlement_rejects_completing_trade_while_item_escrow_quantity_remains_positive(db, gateway, settlement):
    world = seed_world(db)
    trade = create_trade(gateway, world, quantity=4)
    pb = settlement.pb

    expect_grpc_error(
        lambda: settlement.execute_settlement_batch(
            pb.ExecuteSettlementBatchRequest(
                idempotency_key=fresh_key("settlement-complete-positive-escrow"),
                external_request_id="settlement-complete-positive-escrow",
                caused_by_capsuleer_id=world.buyer_id,
                created_by_service="settlement-e2e",
                operations=[
                    pb.SettlementOperation(
                        modify_trade_instance_state=pb.ModifyTradeInstanceState(
                            trade_instance_id=trade.trade_instance_id,
                            to_trade_state="COMPLETED",
                            trade_state_change_kind="ACCEPTED_BY_BUYER",
                            changed_by_service="settlement-e2e",
                        )
                    )
                ],
            )
        ),
        code="FAILED_PRECONDITION",
        contains="remaining item escrow",
    )

    assert trade_row(db, trade)["trade_state"] == "OPEN"
    assert item_escrow_row(db, trade)["quantity"] == 4


def test_settlement_rejects_releasing_more_item_quantity_than_escrow_contains(db, gateway, settlement):
    world = seed_world(db, buyer_isk=1_000, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    pb = settlement.pb
    request = _settlement_accept_request(pb, world, trade, quantity=5, isk_amount=100)
    wallet_escrow_id = request.operations[0].transfer_isk_amount_from_wallet_to_wallet_escrow.wallet_escrow_id
    buyer_isk_before = wallet_row(db, world.buyer_wallet_id)["isk_amount"]
    seller_isk_before = wallet_row(db, world.seller_wallet_id)["isk_amount"]
    wallet_ledger_before = table_count(db, "wallet_ledger")
    item_ledger_before = table_count(db, "item_stack_ledger")

    expect_grpc_error(
        lambda: settlement.execute_settlement_batch(request),
        code="FAILED_PRECONDITION",
        contains="requested 5",
    )

    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == buyer_isk_before
    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == seller_isk_before
    assert item_escrow_row(db, trade)["quantity"] == 4
    assert trade_row(db, trade)["remaining_quantity"] == 4
    assert db.scalar("SELECT count(*) FROM wallet_escrow WHERE wallet_escrow_id = %s", (wallet_escrow_id,)) == 0
    assert table_count(db, "wallet_ledger") == wallet_ledger_before
    assert table_count(db, "item_stack_ledger") == item_ledger_before


def test_settlement_rejects_releasing_same_item_escrow_twice(db, gateway, settlement):
    world = seed_world(db, buyer_isk=1_000, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    pb = settlement.pb

    settlement.execute_settlement_batch(
        _settlement_accept_request(pb, world, trade, quantity=4, isk_amount=100, complete=True)
    )

    expect_grpc_error(
        lambda: settlement.execute_settlement_batch(
            _settlement_accept_request(pb, world, trade, quantity=4, isk_amount=100)
        ),
        code="FAILED_PRECONDITION",
        contains="not OPEN",
    )


def test_settlement_rejects_wallet_payment_that_does_not_match_trade_price(db, gateway, settlement):
    world = seed_world(db, buyer_isk=1_000, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    pb = settlement.pb

    expect_grpc_error(
        lambda: settlement.execute_settlement_batch(
            _settlement_accept_request(pb, world, trade, quantity=2, isk_amount=1)
        ),
        code="FAILED_PRECONDITION",
        contains="does not match trade price",
    )

    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 1_000
    assert item_escrow_row(db, trade)["quantity"] == 4


def test_settlement_rejects_wallet_payment_that_exceeds_remaining_trade_quantity(db, gateway, settlement):
    world = seed_world(db, buyer_isk=1_000, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    pb = settlement.pb

    expect_grpc_error(
        lambda: settlement.execute_settlement_batch(
            _settlement_accept_request(pb, world, trade, quantity=4, isk_amount=125)
        ),
        code="FAILED_PRECONDITION",
        contains="remaining quantity",
    )

    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 1_000
    assert item_escrow_row(db, trade)["quantity"] == 4
    assert trade_row(db, trade)["remaining_quantity"] == 4


def test_settlement_rejects_paying_more_than_wallet_escrow_and_rolls_back_every_prior_step(db, gateway, settlement):
    world = seed_world(db, buyer_isk=1_000, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    pb = settlement.pb
    wallet_escrow_id = uuid_str()
    buyer_isk_before = wallet_row(db, world.buyer_wallet_id)["isk_amount"]
    seller_isk_before = wallet_row(db, world.seller_wallet_id)["isk_amount"]
    wallet_ledger_before = table_count(db, "wallet_ledger")
    item_ledger_before = table_count(db, "item_stack_ledger")

    expect_grpc_error(
        lambda: settlement.execute_settlement_batch(
            pb.ExecuteSettlementBatchRequest(
                idempotency_key=fresh_key("settlement-wallet-over-release"),
                external_request_id="settlement-wallet-over-release",
                caused_by_capsuleer_id=world.buyer_id,
                created_by_service="settlement-e2e",
                operations=[
                    pb.SettlementOperation(
                        transfer_isk_amount_from_wallet_to_wallet_escrow=pb.TransferIskAmountFromWalletToWalletEscrow(
                            source_wallet_id=world.buyer_wallet_id,
                            wallet_escrow_id=wallet_escrow_id,
                            trade_instance_id=trade.trade_instance_id,
                            isk_amount=100,
                        )
                    ),
                    pb.SettlementOperation(
                        transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner=pb.TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(
                            item_stack_escrow_id=trade.item_stack_escrow_id,
                            destination_item_stack_id=world.buyer_stack_id,
                            quantity=4,
                        )
                    ),
                    pb.SettlementOperation(
                        transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner=pb.TransferIskAmountFromWalletEscrowToWalletWithNewOwner(
                            wallet_escrow_id=wallet_escrow_id,
                            destination_wallet_id=world.seller_wallet_id,
                            isk_amount=101,
                        )
                    ),
                ],
            )
        ),
        code="FAILED_PRECONDITION",
        contains="requested 101",
    )

    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == buyer_isk_before
    assert wallet_row(db, world.seller_wallet_id)["isk_amount"] == seller_isk_before
    assert item_escrow_row(db, trade)["quantity"] == 4
    assert trade_row(db, trade)["remaining_quantity"] == 4
    assert db.scalar("SELECT count(*) FROM wallet_escrow WHERE wallet_escrow_id = %s", (wallet_escrow_id,)) == 0
    assert table_count(db, "wallet_ledger") == wallet_ledger_before
    assert table_count(db, "item_stack_ledger") == item_ledger_before


def test_settlement_rejects_completing_trade_while_wallet_escrow_remains_active(db, gateway, settlement):
    world = seed_world(db, buyer_isk=1_000, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    pb = settlement.pb
    wallet_escrow_id = uuid_str()

    expect_grpc_error(
        lambda: settlement.execute_settlement_batch(
            pb.ExecuteSettlementBatchRequest(
                idempotency_key=fresh_key("settlement-complete-wallet-escrow"),
                external_request_id="settlement-complete-wallet-escrow",
                caused_by_capsuleer_id=world.buyer_id,
                created_by_service="settlement-e2e",
                operations=[
                    pb.SettlementOperation(
                        transfer_isk_amount_from_wallet_to_wallet_escrow=pb.TransferIskAmountFromWalletToWalletEscrow(
                            source_wallet_id=world.buyer_wallet_id,
                            wallet_escrow_id=wallet_escrow_id,
                            trade_instance_id=trade.trade_instance_id,
                            isk_amount=100,
                        )
                    ),
                    pb.SettlementOperation(
                        transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner=pb.TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(
                            item_stack_escrow_id=trade.item_stack_escrow_id,
                            destination_item_stack_id=world.buyer_stack_id,
                            quantity=4,
                        )
                    ),
                    pb.SettlementOperation(
                        modify_trade_instance_state=pb.ModifyTradeInstanceState(
                            trade_instance_id=trade.trade_instance_id,
                            to_trade_state="COMPLETED",
                            trade_state_change_kind="ACCEPTED_BY_BUYER",
                            changed_by_service="settlement-e2e",
                        )
                    ),
                ],
            )
        ),
        code="FAILED_PRECONDITION",
        contains="active wallet escrow",
    )

    assert trade_row(db, trade)["trade_state"] == "OPEN"
    assert wallet_row(db, world.buyer_wallet_id)["isk_amount"] == 1_000
    assert item_escrow_row(db, trade)["quantity"] == 4


def test_database_rejects_remaining_quantity_drift(db, gateway):
    world = seed_world(db, buyer_isk=1_000, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)

    with pytest.raises(psycopg.errors.CheckViolation, match="remaining_quantity"):
        db.execute(
            "UPDATE trade_instance SET remaining_quantity = 3 WHERE trade_instance_id = %s",
            (trade.trade_instance_id,),
        )

    assert trade_row(db, trade)["remaining_quantity"] == 4
    assert item_escrow_row(db, trade)["quantity"] == 4


def test_database_rejects_item_stack_ledger_updates(db, gateway):
    world = seed_world(db)
    create_trade(gateway, world, quantity=4)
    ledger_id = db.scalar("SELECT item_stack_ledger_id FROM item_stack_ledger LIMIT 1")
    ledger_count = table_count(db, "item_stack_ledger")

    with pytest.raises(psycopg.errors.CheckViolation, match="append-only"):
        db.execute(
            "UPDATE item_stack_ledger SET quantity_after = quantity_after WHERE item_stack_ledger_id = %s",
            (ledger_id,),
        )

    assert table_count(db, "item_stack_ledger") == ledger_count


def test_database_rejects_item_stack_ledger_deletes(db, gateway):
    world = seed_world(db)
    create_trade(gateway, world, quantity=4)
    ledger_id = db.scalar("SELECT item_stack_ledger_id FROM item_stack_ledger LIMIT 1")
    ledger_count = table_count(db, "item_stack_ledger")

    with pytest.raises(psycopg.errors.CheckViolation, match="append-only"):
        db.execute("DELETE FROM item_stack_ledger WHERE item_stack_ledger_id = %s", (ledger_id,))

    assert table_count(db, "item_stack_ledger") == ledger_count


def test_database_rejects_wallet_ledger_deletes(db, gateway):
    world = seed_world(db, buyer_isk=1_000, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    accept_trade(gateway, world, trade)
    ledger_id = db.scalar("SELECT wallet_ledger_id FROM wallet_ledger LIMIT 1")
    ledger_count = table_count(db, "wallet_ledger")

    with pytest.raises(psycopg.errors.CheckViolation, match="append-only"):
        db.execute("DELETE FROM wallet_ledger WHERE wallet_ledger_id = %s", (ledger_id,))

    assert table_count(db, "wallet_ledger") == ledger_count


def test_database_rejects_wallet_ledger_updates(db, gateway):
    world = seed_world(db, buyer_isk=1_000, buyer_stack_quantity=0)
    trade = create_trade(gateway, world, quantity=4, unit_price_isk=25)
    accept_trade(gateway, world, trade)
    ledger_id = db.scalar("SELECT wallet_ledger_id FROM wallet_ledger LIMIT 1")
    ledger_count = table_count(db, "wallet_ledger")

    with pytest.raises(psycopg.errors.CheckViolation, match="append-only"):
        db.execute(
            "UPDATE wallet_ledger SET isk_amount_after = isk_amount_after WHERE wallet_ledger_id = %s",
            (ledger_id,),
        )

    assert table_count(db, "wallet_ledger") == ledger_count


def _settlement_accept_request(pb, world, trade, *, quantity: int, isk_amount: int, complete: bool = False):
    wallet_escrow_id = uuid_str()
    operations = [
        pb.SettlementOperation(
            transfer_isk_amount_from_wallet_to_wallet_escrow=pb.TransferIskAmountFromWalletToWalletEscrow(
                source_wallet_id=world.buyer_wallet_id,
                wallet_escrow_id=wallet_escrow_id,
                trade_instance_id=trade.trade_instance_id,
                isk_amount=isk_amount,
            )
        ),
        pb.SettlementOperation(
            transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner=pb.TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(
                item_stack_escrow_id=trade.item_stack_escrow_id,
                destination_item_stack_id=world.buyer_stack_id,
                quantity=quantity,
            )
        ),
        pb.SettlementOperation(
            transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner=pb.TransferIskAmountFromWalletEscrowToWalletWithNewOwner(
                wallet_escrow_id=wallet_escrow_id,
                destination_wallet_id=world.seller_wallet_id,
                isk_amount=isk_amount,
            )
        ),
    ]
    if complete:
        operations.append(
            pb.SettlementOperation(
                modify_trade_instance_state=pb.ModifyTradeInstanceState(
                    trade_instance_id=trade.trade_instance_id,
                    to_trade_state="COMPLETED",
                    trade_state_change_kind="ACCEPTED_BY_BUYER",
                    changed_by_service="settlement-e2e",
                )
            )
        )
    key = fresh_key("settlement-accept")
    return pb.ExecuteSettlementBatchRequest(
        idempotency_key=key,
        external_request_id=f"external-{key}",
        caused_by_capsuleer_id=world.buyer_id,
        created_by_service="settlement-e2e",
        operations=operations,
    )
