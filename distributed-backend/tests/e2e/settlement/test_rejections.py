from __future__ import annotations

from decimal import Decimal

import pytest

from helpers.assertions import (
    assert_no_mutation,
    assert_no_trade_side_effects,
    assert_result_committed,
    assert_result_rejected,
    assert_trade_state,
    assert_value_conservation,
)
from helpers.builders import (
    TradeScenarioIds,
    expire_command,
    cancel_command,
    issue_command,
    now_millis,
    operation_metadata,
    settle_command,
)
from helpers.grpc_clients import single_settlement_response


pytestmark = [pytest.mark.live, pytest.mark.settlement]


def test_empty_settlement_command_is_rejected(proto_modules, settlement_target) -> None:
    proto = proto_modules
    command = proto.settlement_command.TradeSettlementCommand()

    result = single_settlement_response(proto, settlement_target, command)

    assert_result_rejected(
        proto,
        result,
        error_code=proto.errors.ERROR_CODE_VALIDATION_FAILED,
        retryable=False,
        message_contains="command is required",
    )


def test_issue_with_insufficient_items_is_rejected_without_side_effects(
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
    before = trade_db.scenario_snapshot(ids)

    result = single_settlement_response(
        proto,
        settlement_target,
        issue_command(proto, ids, world, total_quantity=5),
    )

    assert_result_rejected(
        proto,
        result,
        error_code=proto.errors.ERROR_CODE_FAILED_PRECONDITION,
        retryable=False,
        message_contains="insufficient quantity",
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_no_trade_side_effects(trade_db, ids)


def test_issue_with_huge_quantity_is_rejected_without_side_effects(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
        source_quantity=10,
    )
    before = trade_db.scenario_snapshot(ids)

    result = single_settlement_response(
        proto,
        settlement_target,
        issue_command(proto, ids, world, total_quantity=9_223_372_036_854_775_000),
    )

    assert_result_rejected(
        proto,
        result,
        error_code=proto.errors.ERROR_CODE_FAILED_PRECONDITION,
        retryable=False,
        message_contains="insufficient quantity",
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_no_trade_side_effects(trade_db, ids)


@pytest.mark.parametrize("invalid_quantity", [0, -1])
def test_issue_with_non_positive_quantity_is_rejected_before_mutation(
    proto_modules, trade_db, settlement_target, invalid_quantity: int
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    before = trade_db.scenario_snapshot(ids)

    result = single_settlement_response(
        proto,
        settlement_target,
        issue_command(proto, ids, world, total_quantity=invalid_quantity),
    )

    assert_result_rejected(
        proto,
        result,
        error_code=proto.errors.ERROR_CODE_VALIDATION_FAILED,
        retryable=False,
        message_contains="must be positive",
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_no_trade_side_effects(trade_db, ids)


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
    assert_result_committed(proto, issue_result, expected_oneof="issue_trade_instance")
    before = trade_db.scenario_snapshot(ids)

    rejected = single_settlement_response(
        proto,
        settlement_target,
        settle_command(proto, ids, world, quantity=1, unit_price_minor=9_999),
    )

    assert_result_rejected(
        proto,
        rejected,
        error_code=proto.errors.ERROR_CODE_FAILED_PRECONDITION,
        retryable=False,
    )
    assert_trade_state(trade_db, ids.trade_instance_id, state="outstanding", remaining=5)
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_value_conservation(trade_db, ids, world)


def test_settle_with_total_price_overflow_is_rejected_without_side_effects(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
        source_quantity=10,
    )
    assert_result_committed(
        proto,
        single_settlement_response(
            proto,
            settlement_target,
            issue_command(proto, ids, world, total_quantity=5, unit_price_minor=10_000),
        ),
        expected_oneof="issue_trade_instance",
    )
    before = trade_db.scenario_snapshot(ids)

    rejected = single_settlement_response(
        proto,
        settlement_target,
        settle_command(
            proto,
            ids,
            world,
            quantity=9_223_372_036_854_775,
            unit_price_minor=9_223_372_036_854_775,
            total_price_minor=0,
        ),
    )

    assert_result_rejected(
        proto,
        rejected,
        error_code=proto.errors.ERROR_CODE_VALIDATION_FAILED,
        retryable=False,
        message_contains="overflow",
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_value_conservation(trade_db, ids, world)


def test_settle_over_remaining_quantity_is_rejected_without_side_effects(
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
    before = trade_db.scenario_snapshot(ids)

    rejected = single_settlement_response(
        proto,
        settlement_target,
        settle_command(proto, ids, world, quantity=6),
    )

    assert_result_rejected(
        proto,
        rejected,
        error_code=proto.errors.ERROR_CODE_FAILED_PRECONDITION,
        retryable=False,
        message_contains="insufficient quantity",
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_value_conservation(trade_db, ids, world)


def test_settle_with_insufficient_buyer_isk_is_rejected_without_side_effects(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
        buyer_wallet_major=Decimal("100.00"),
    )
    assert_result_committed(
        proto,
        single_settlement_response(
            proto,
            settlement_target,
            issue_command(proto, ids, world, total_quantity=5, unit_price_minor=10_000),
        ),
        expected_oneof="issue_trade_instance",
    )
    before = trade_db.scenario_snapshot(ids)

    rejected = single_settlement_response(
        proto,
        settlement_target,
        settle_command(proto, ids, world, quantity=2, unit_price_minor=10_000),
    )

    assert_result_rejected(
        proto,
        rejected,
        error_code=proto.errors.ERROR_CODE_FAILED_PRECONDITION,
        retryable=False,
        message_contains="insufficient ISK",
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_value_conservation(trade_db, ids, world)


def test_settle_with_wrong_buyer_identity_is_rejected_without_side_effects(
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
    before = trade_db.scenario_snapshot(ids)

    rejected = single_settlement_response(
        proto,
        settlement_target,
        settle_command(
            proto,
            ids,
            world,
            quantity=1,
            buyer_capsuleer_id=world.issuer_id,
            buyer_wallet_id=ids.buyer_wallet_id,
        ),
    )

    assert_result_rejected(
        proto,
        rejected,
        error_code=proto.errors.ERROR_CODE_FAILED_PRECONDITION,
        retryable=False,
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_value_conservation(trade_db, ids, world)


def test_wrong_actor_cannot_cancel_open_trade(
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
    before = trade_db.scenario_snapshot(ids)

    rejected = single_settlement_response(
        proto,
        settlement_target,
        cancel_command(proto, ids, world, requesting_capsuleer_id=world.buyer_id),
    )

    assert_result_rejected(
        proto,
        rejected,
        error_code=proto.errors.ERROR_CODE_VALIDATION_FAILED,
        retryable=False,
        message_contains="only the issuer can cancel",
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_value_conservation(trade_db, ids, world)


def test_expire_before_expiry_is_rejected_without_side_effects(
    proto_modules, trade_db, settlement_target
) -> None:
    proto = proto_modules
    ids = TradeScenarioIds()
    world = trade_db.seed_basic_trade_world(
        issuer_wallet_id=ids.issuer_wallet_id,
        buyer_wallet_id=ids.buyer_wallet_id,
        issuer_item_stack_id=ids.issuer_item_stack_id,
    )
    expires_at = now_millis() + 3_600_000
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
                expires_at_unix_millis=expires_at,
            ),
        ),
        expected_oneof="issue_trade_instance",
    )
    before = trade_db.scenario_snapshot(ids)

    rejected = single_settlement_response(
        proto,
        settlement_target,
        expire_command(proto, ids, world, evaluated_at_unix_millis=expires_at - 1),
    )

    assert_result_rejected(
        proto,
        rejected,
        error_code=proto.errors.ERROR_CODE_VALIDATION_FAILED,
        retryable=False,
        message_contains="has not reached its expiration time",
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_value_conservation(trade_db, ids, world)


@pytest.mark.parametrize("invalid_quantity", [0, -1])
def test_settle_with_non_positive_quantity_is_rejected_before_mutation(
    proto_modules, trade_db, settlement_target, invalid_quantity: int
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
    before = trade_db.scenario_snapshot(ids)

    result = single_settlement_response(
        proto,
        settlement_target,
        settle_command(proto, ids, world, quantity=invalid_quantity),
    )

    assert_result_rejected(
        proto,
        result,
        error_code=proto.errors.ERROR_CODE_VALIDATION_FAILED,
        retryable=False,
        message_contains="must be positive",
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_value_conservation(trade_db, ids, world)


def test_issue_with_invalid_metadata_is_rejected_without_side_effects(
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
        purpose="invalid-metadata",
    )
    metadata.operation_id.value = "not-a-uuid"
    before = trade_db.scenario_snapshot(ids)

    result = single_settlement_response(
        proto,
        settlement_target,
        issue_command(proto, ids, world, metadata=metadata),
    )

    assert_result_rejected(
        proto,
        result,
        error_code=proto.errors.ERROR_CODE_VALIDATION_FAILED,
        retryable=False,
        message_contains="metadata.operation_id must be a UUID",
    )
    assert_no_mutation(before, trade_db.scenario_snapshot(ids))
    assert_no_trade_side_effects(trade_db, ids)
