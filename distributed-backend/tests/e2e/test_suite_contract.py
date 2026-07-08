from __future__ import annotations

import os

import pytest

import conftest
from helpers import Trade, World, accept_payload, cancel_payload, issue_payload


def contract_world() -> World:
    return World(
        seller_id=1001,
        buyer_id=2002,
        other_id=3003,
        outsider_id=4004,
        item_type_id=34,
        other_item_type_id=35,
        station_id=60003760,
        other_station_id=60008494,
        seller_wallet_id="seller-wallet",
        buyer_wallet_id="buyer-wallet",
        other_wallet_id="other-wallet",
        outsider_wallet_id="outsider-wallet",
        seller_stack_id="seller-stack",
        seller_other_stack_id="seller-other-stack",
        other_stack_id="other-stack",
        buyer_stack_id=None,
    )


def contract_trade() -> Trade:
    return Trade(
        trade_instance_id="trade",
        item_stack_escrow_id="item-escrow",
        quantity=4,
        unit_price_isk=25,
        seller_stack_id="seller-stack",
        idempotency_key="issue-key",
    )


def test_payload_helpers_preserve_every_falsey_value_on_the_wire():
    world = contract_world()
    issue = issue_payload(
        world,
        quantity=0,
        unit_price_isk=0,
        idempotency_key="",
        issued_by_capsuleer_id=0,
        item_stack_id="",
        item_stack_owner_id=0,
        item_type_id=0,
        station_id=0,
        item_stack_quantity=0,
    )
    assert issue == {
        "idempotencyKey": "",
        "externalRequestId": "external-",
        "issuedByCapsuleerId": 0,
        "itemStack": {
            "itemStackId": "",
            "ownerId": 0,
            "itemTypeId": 0,
            "stationId": 0,
            "quantity": 0,
        },
        "quantity": 0,
        "unitPriceIsk": 0,
    }

    accepted = accept_payload(
        world,
        contract_trade(),
        quantity=0,
        idempotency_key="",
        buyer_capsuleer_id=0,
        buyer_wallet_id="",
        buyer_destination_item_stack_id="",
        hostile_flag=False,
        hostile_values=[],
    )
    assert accepted["quantityRequested"] == 0
    assert accepted["idempotencyKey"] == ""
    assert accepted["buyerCapsuleerId"] == 0
    assert accepted["buyerWalletId"] == ""
    assert accepted["buyerDestinationItemStackId"] == ""
    assert accepted["hostileFlag"] is False
    assert accepted["hostileValues"] == []

    cancelled = cancel_payload(
        world,
        contract_trade(),
        idempotency_key="",
        cancelled_by_capsuleer_id=0,
        hostile_flag=False,
        hostile_values=[],
    )
    assert cancelled["idempotencyKey"] == ""
    assert cancelled["cancelledByCapsuleerId"] == 0
    assert cancelled["hostileFlag"] is False
    assert cancelled["hostileValues"] == []


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_documented_production_gate_values_are_strict(value, monkeypatch):
    monkeypatch.setenv("EVE_TRADE_E2E_PRODUCTION_GATE", value)
    assert conftest.production_gate_enabled() is True
    with pytest.raises(pytest.fail.Exception, match="missing dependency"):
        conftest.require_or_skip(False, "missing dependency")


def test_production_gate_does_not_inherit_the_all_skipped_escape_hatch(monkeypatch):
    monkeypatch.setenv("EVE_TRADE_E2E_PRODUCTION_GATE", "1")
    monkeypatch.setenv("EVE_TRADE_E2E_ALLOW_ALL_SKIPPED", "true")
    assert conftest.production_gate_enabled() is True
    with pytest.raises(pytest.fail.Exception, match="production dependency"):
        conftest.require_or_skip(False, "missing production dependency")


@pytest.mark.parametrize(
    "missing_name",
    [
        "EVE_TRADE_ENCORE_URL",
        "EVE_TRADE_SETTLEMENT_GRPC",
        "EVE_TRADE_SIMULATOR_URL",
        "EVE_TRADE_NSQ_TCP",
        "EVE_TRADE_DATABASE_URL",
        "EVE_TRADE_RUNTIME_DATABASE_URL",
        "EVE_TRADE_QUILKIN_UDP_HOST",
        "EVE_TRADE_EDGE_RESPONSE_SECRET",
        "EVE_TRADE_EDGE_RESPONSE_KEY_ID",
        "EVE_TRADE_EDGE_SELLER_KEY_ID",
        "EVE_TRADE_EDGE_SELLER_SECRET",
        "EVE_TRADE_EDGE_BUYER_KEY_ID",
        "EVE_TRADE_EDGE_BUYER_SECRET",
        "EVE_TRADE_EDGE_OTHER_KEY_ID",
        "EVE_TRADE_EDGE_OTHER_SECRET",
    ],
)
def test_production_gate_fails_each_missing_dependency_or_credential(missing_name, monkeypatch):
    monkeypatch.setenv("EVE_TRADE_E2E_PRODUCTION_GATE", "1")
    monkeypatch.delenv(missing_name, raising=False)
    with pytest.raises(pytest.fail.Exception, match=missing_name):
        conftest.service_urls.__wrapped__()
