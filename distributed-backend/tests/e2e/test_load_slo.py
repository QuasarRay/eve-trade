from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import os
import time

import pytest

from helpers import (
    fresh_key,
    insert_item_stack,
    item_stack_row,
    open_trade_count,
    seed_world,
    total_item_quantity,
    total_isk_amount,
    uuid_str,
)


def test_authenticated_udp_issue_burst_meets_slo_and_preserves_state(db, authenticated_edge):
    key_id = os.environ.get("EVE_TRADE_EDGE_SELLER_KEY_ID")
    secret = os.environ.get("EVE_TRADE_EDGE_SELLER_SECRET")
    if not key_id or not secret:
        pytest.skip("set seller edge credential to run the load/SLO gate")
    requests = int(os.environ.get("EVE_TRADE_LOAD_REQUESTS", "20"))
    p95_budget = float(os.environ.get("EVE_TRADE_LOAD_P95_SECONDS", "5"))
    assert requests >= 10
    world = seed_world(db, seller_quantity=1)
    source_stack_ids = [world.seller_stack_id]
    for _ in range(requests - 1):
        stack_id = uuid_str()
        insert_item_stack(db, stack_id, world.seller_id, world.item_type_id, world.station_id, 1)
        source_stack_ids.append(stack_id)
    item_total_before = total_item_quantity(db)
    isk_total_before = total_isk_amount(db)

    def issue(index: int):
        packet = {
            "schema_version": "eve-trade-gui.v1",
            "interaction_id": fresh_key(f"load-issue-{index}"),
            "ui": {"window": "regional_market", "action": "market_place_sell_order"},
            "input": {
                "issued_by_capsuleer_id": world.seller_id,
                "item_stack": {
                    "item_stack_id": source_stack_ids[index],
                    "owner_id": world.seller_id,
                    "item_type_id": world.item_type_id,
                    "station_id": world.station_id,
                    "quantity": 1,
                },
                "quantity": 1,
                "unit_price_isk": index + 1,
            },
        }
        started = time.monotonic()
        response = authenticated_edge.submit(packet, key_id, secret)
        return time.monotonic() - started, response

    outcomes = []
    with ThreadPoolExecutor(max_workers=min(10, requests)) as executor:
        futures = [executor.submit(issue, index) for index in range(requests)]
        for future in as_completed(futures):
            outcomes.append(future.result())

    latencies = sorted(latency for latency, _ in outcomes)
    p95 = latencies[math.ceil(0.95 * len(latencies)) - 1]
    failures = [response for _, response in outcomes if response.get("status") not in {"accepted", "queued"}]
    assert failures == []
    assert p95 <= p95_budget, f"p95={p95:.3f}s exceeds {p95_budget:.3f}s"
    assert open_trade_count(db) == requests
    assert all(item_stack_row(db, stack_id)["quantity"] == 0 for stack_id in source_stack_ids)
    assert total_item_quantity(db) == item_total_before
    assert total_isk_amount(db) == isk_total_before
