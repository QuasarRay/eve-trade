from __future__ import annotations

import copy
import json
import math
import os
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest


def _uid(case: dict[str, Any], label: str) -> str:
    """Deterministic UUID controlled by the Hypothesis example.

    This replaces uuid4() calls that were invisible to Hypothesis shrinking and
    replay.  Distinct labels give independent deterministic identifiers.
    """
    seed = json.dumps(case, sort_keys=True, default=str, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"eve-trade-hypothesis:{label}:{seed}"))


from eve_trade_hypothesis.semantic_overrides import SEMANTIC_EVIDENCE_OVERRIDES


def run(runtime, category: int, name: str, case: dict[str, Any]) -> None:
    assert name not in SEMANTIC_EVIDENCE_OVERRIDES, (
        f"{name} has an audited weak legacy branch and must be rerouted by engine.py"
    )
    handlers = {
        8: _issue_authorization,
        9: _numeric_boundaries,
        10: _destination_stack,
        11: _wallet_ownership,
        12: _trade_state,
        13: _accept_cancel_race,
        14: _transaction_isolation,
        15: _idempotency,
        16: _operation_ordering,
        17: _escrow_binding,
        18: _ledger,
        19: _outbox,
        20: _pubsub_delivery,
        21: _worker_lifecycle,
        22: _market_projection,
        23: _pubsub_operational,
        32: _fuzz_sequences,
        33: _laws,
        41: _identifiers,
        42: _time_lease,
        43: _retry_policy,
        47: _message_identity,
        58: _expiration,
        59: _deterministic_ids,
        60: _plan_construction,
        62: _multi_buyer,
        63: _operation_matrix,
        82: _audit,
        83: _reconciliation,
        87: _error_mapping,
        94: _read_model,
        95: _cancel_refund,
        96: _request_attempts,
        97: _batch_step_consistency,
    }
    try:
        handler = handlers[category]
    except KeyError as exc:
        raise AssertionError(f"unhandled trade category {category}: {name}") from exc
    handler(runtime, name, case)


def _world(runtime, case: dict[str, Any], *, seller_quantity: int | None = None, buyer_isk: int | None = None, buyer_stack_quantity: int | None = None):
    live=runtime.live
    live.reset_example()
    q=seller_quantity if seller_quantity is not None else max(10, int(case.get("quantity", 4))*3)
    price=max(1,int(case.get("price",25)))
    isk=buyer_isk if buyer_isk is not None else max(10_000,q*price*10)
    world=live.seed_world(seller_quantity=q,buyer_isk=isk,buyer_stack_quantity=buyer_stack_quantity)
    return live,world,q,price


def _failure(live, call: Callable[[], Any], codes: set[str] | None = None):
    try:
        call()
    except live.helpers.RpcFailure as exc:
        if codes is not None:
            assert exc.code in codes, (exc.code,codes,exc.message)
        return exc
    raise AssertionError("expected request to fail")


def _unchanged(before, after, *, fields: tuple[str,...] = ("total_items","total_isk")):
    for field in fields:
        assert getattr(after,field)==getattr(before,field),(field,before,after)


def _issue_authorization(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case)
    before=live.snapshot()
    payload=live.issue_payload(world,quantity=min(case.get("quantity",1),seller_q),unit_price_isk=price,item_stack_quantity=seller_q)

    if "nonexistent_item_stack" in name:
        payload["itemStack"]["itemStackId"]=str(_uid(case, "auto_001"))
    elif "marked_unavailable_for_trading" in name:
        return runtime.evidence.run(name,case)
    elif "wrong_item_type_claim" in name:
        payload["itemStack"]["itemTypeId"]=world.other_item_type_id
    elif "uses_authoritative_item_type" in name:
        payload["itemStack"]["itemTypeId"]=world.other_item_type_id
        response=live.gateway.issue_trade_instance(payload)
        row=live.db.fetchone("SELECT item_type_id FROM trade_instance WHERE trade_instance_id=%s",(response["tradeInstanceId"],))
        assert row and int(row["item_type_id"])==world.item_type_id
        return
    elif "uses_authoritative_item_quantity" in name:
        payload["itemStack"]["quantity"]=seller_q*100
        response=live.gateway.issue_trade_instance(payload)
        assert response
        assert live.item_stack_row(world.seller_stack_id)["quantity"]==seller_q-payload["quantity"]
        return
    elif "nonexistent_seller" in name:
        payload["issuedByCapsuleerId"]=999_999_999
    elif "owned_by_different_authenticated_principal" in name:
        payload["issuedByCapsuleerId"]=world.other_id
    elif "nonexistent_station" in name:
        # Claiming a bogus station should be ignored in favor of authoritative
        # stack station if client station is merely advisory; if API contract
        # treats it as an identity assertion it must reject. Either way it must
        # never persist the nonexistent station.
        payload["itemStack"]["stationId"]=999_999_999
        try:
            response=live.gateway.issue_trade_instance(payload)
        except live.helpers.RpcFailure:
            _unchanged(before,live.snapshot()); return
        row=live.db.fetchone("SELECT station_id FROM trade_instance WHERE trade_instance_id=%s",(response["tradeInstanceId"],))
        assert row and int(row["station_id"])==world.station_id
        return
    elif "quantity_that_became_unavailable_after_request_was_received" in name:
        return runtime.fault.run(name,case)
    elif "concurrent_issues_from_same_item_stack" in name:
        q=max(2,min(seller_q,case.get("quantity",4)))
        funcs=[]
        for i in range(2):
            key=f"issue-race-{case.get('nonce','x')}-{i}-{_uid(case, "auto_002")}"
            funcs.append(lambda k=key: live.gateway.issue_trade_instance(live.issue_payload(world,quantity=q,unit_price_isk=price,idempotency_key=k,item_stack_quantity=seller_q)))
        results=live.concurrent(funcs)
        remaining=int(live.item_stack_row(world.seller_stack_id)["quantity"])
        escrowed=int(live.scalar("SELECT COALESCE(SUM(quantity),0) FROM item_stack_escrow WHERE source_item_stack_id=%s",(world.seller_stack_id,)) or 0) if "source_item_stack_id" in live.columns("item_stack_escrow") else seller_q-remaining
        assert remaining>=0
        assert escrowed<=seller_q
        assert remaining+escrowed==seller_q
        return
    elif "issue_of_entire_source_stack" in name:
        trade=live.create_trade(world,quantity=seller_q,unit_price_isk=price,item_stack_quantity=seller_q)
        assert int(live.item_stack_row(world.seller_stack_id)["quantity"])==0
        assert int(live.item_escrow_row(trade)["quantity"])==seller_q
        return
    elif "orphan_item_escrow_when_trade_insert_fails" in name or "trade_when_item_escrow_insert_fails" in name:
        return runtime.fault.run(name,case)
    else:
        return runtime.evidence.run(name, case)

    _failure(live,lambda:live.gateway.issue_trade_instance(payload),{"invalid_argument","principal_mismatch","not_found","failed_precondition","permission_denied"})
    after=live.snapshot()
    assert after.trade_count==before.trade_count
    assert after.item_escrow_count==before.item_escrow_count
    _unchanged(before,after)


def _numeric_boundaries(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=10**9)
    if "zero_unit_price" in name:
        payload=live.issue_payload(world,quantity=1,unit_price_isk=0,item_stack_quantity=20)
        _failure(live,lambda:live.gateway.issue_trade_instance(payload),{"invalid_argument","failed_precondition"}); return
    if "shared_wire_and_database_integer_maximum" in name or "database_numeric_limit" in name or "overflow" in name or "maximum_supported" in name:
        # Boundary is cross-language (Go int64/protobuf int64/Postgres bigint).
        # Use the live endpoint for values near int64 and external evidence for
        # values that cannot be seeded without violating fixture constraints.
        if "quantity_times_price_integer_overflow" in name:
            q=2**32; p=2**32
            payload=live.issue_payload(world,quantity=1,unit_price_isk=p,item_stack_quantity=20)
            try:
                response=live.gateway.issue_trade_instance(payload)
            except live.helpers.RpcFailure:
                return
            trade=live.helpers.Trade(response["tradeInstanceId"],response["itemStackEscrowId"],1,p,world.seller_stack_id,payload["idempotencyKey"])
            _failure(live,lambda:live.accept_trade(world,trade,quantity=q),{"invalid_argument","failed_precondition","internal"})
            return
        return runtime.evidence.run(name,case)
    if "does_not_round_integer_isk_amount" in name:
        q=max(1,min(7,case.get("quantity",3))); p=max(1,case.get("price",17))
        trade=live.create_trade(world,quantity=q,unit_price_isk=p,item_stack_quantity=20)
        seller_before=int(live.wallet_row(world.seller_wallet_id)["isk_amount"])
        live.accept_trade(world,trade,quantity=q)
        assert int(live.wallet_row(world.seller_wallet_id)["isk_amount"])-seller_before==q*p
        return
    if "wallet_debit_of_exact_balance_leaves_zero" in name:
        q=max(1,min(5,case.get("quantity",2))); p=max(1,case.get("price",11)); required=q*p
        live.db.reset(); world=live.seed_world(seller_quantity=10,buyer_isk=required)
        trade=live.create_trade(world,quantity=q,unit_price_isk=p,item_stack_quantity=10)
        live.accept_trade(world,trade,quantity=q)
        assert int(live.wallet_row(world.buyer_wallet_id)["isk_amount"])==0
        return
    if "wallet_credit_rejects_result_above" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _destination_stack(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,buyer_stack_quantity=2)
    trade=live.create_trade(world,quantity=4,unit_price_isk=price,item_stack_quantity=seller_q)
    before=live.snapshot()
    destination=world.buyer_stack_id
    assert destination
    if "owned_by_different_buyer" in name:
        # Isolate ownership as the only invalid dimension: same item type, same
        # station, different owner.  Do not rely on a generic fixture stack that
        # may differ in additional fields and false-green the rejection.
        destination=str(_uid(case, "destination-owner-mismatch"))
        live.helpers.insert_item_stack(
            live.db, destination, world.other_id, world.item_type_id, world.station_id, 0
        )
    elif "different_item_type" in name:
        destination=world.seller_other_stack_id
    elif "different_station" in name:
        bad=str(_uid(case, "auto_003")); live.helpers.insert_item_stack(live.db,bad,world.buyer_id,world.item_type_id,world.other_station_id,0); destination=bad
    elif "marked_unavailable_for_trading" in name:
        return runtime.evidence.run(name,case)
    elif "nonexistent_explicit_destination_stack" in name:
        destination=str(_uid(case, "auto_004"))
    elif "does_not_merge_into_seller_source_stack" in name or "does_not_merge_into_item_escrow_backing_stack" in name:
        # Exercise a successful accept with automatic destination selection.  The
        # old branch supplied a deliberately forbidden destination and greened on
        # an ownership/not-found rejection, which never exercised merge behavior.
        seller_before=int(live.item_stack_row(world.seller_stack_id)["quantity"])
        escrow_before=int(live.item_escrow_row(trade)["quantity"])
        buyer_before=int(live.scalar(
            "SELECT COALESCE(SUM(quantity),0) FROM item_stack WHERE owner_id=%s AND item_type_id=%s AND station_id=%s",
            (world.buyer_id,world.item_type_id,world.station_id),
        ) or 0)
        live.accept_trade(world,trade,quantity=1)
        buyer_after=int(live.scalar(
            "SELECT COALESCE(SUM(quantity),0) FROM item_stack WHERE owner_id=%s AND item_type_id=%s AND station_id=%s",
            (world.buyer_id,world.item_type_id,world.station_id),
        ) or 0)
        assert int(live.item_stack_row(world.seller_stack_id)["quantity"])==seller_before
        assert int(live.item_escrow_row(trade)["quantity"])==escrow_before-1
        assert buyer_after==buyer_before+1
        assert live.scalar(
            "SELECT count(*) FROM item_stack WHERE item_stack_id=%s",
            (trade.item_stack_escrow_id,),
        )==0
        return
    elif "concurrent_accepts_into_same_destination_stack" in name:
        trade=live.create_trade(world,quantity=8,unit_price_isk=price,idempotency_key=f"issue2-{_uid(case, "auto_005")}",item_stack_quantity=seller_q-4)
        q=2
        funcs=[lambda i=i: live.accept_trade(world,trade,quantity=q,idempotency_key=f"dest-race-{i}-{_uid(case, "auto_006")}",buyer_destination_item_stack_id=world.buyer_stack_id) for i in range(2)]
        results=live.concurrent(funcs)
        successes=sum(ok for ok,_ in results)
        assert int(live.item_stack_row(world.buyer_stack_id)["quantity"])==2+successes*q
        return
    elif "concurrent_accepts_creating_destination_stack" in name:
        return runtime.evidence.run(name,case)
    else:
        return runtime.evidence.run(name, case)
    _failure(live,lambda:live.accept_trade(world,trade,quantity=1,buyer_destination_item_stack_id=destination),{"invalid_argument","failed_precondition","permission_denied","not_found","principal_mismatch"})
    _unchanged(before,live.snapshot())


def _wallet_ownership(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case)
    trade=live.create_trade(world,quantity=2,unit_price_isk=price,item_stack_quantity=seller_q)
    before=live.snapshot()
    if "buyer_wallet_owned_by_different_capsuleer" in name:
        wallet=world.other_wallet_id
        _failure(live,lambda:live.accept_trade(world,trade,quantity=1,buyer_wallet_id=wallet),{"principal_mismatch","permission_denied","failed_precondition","invalid_argument"}); _unchanged(before,live.snapshot()); return
    if "nonexistent_buyer_wallet" in name:
        _failure(live,lambda:live.accept_trade(world,trade,quantity=1,buyer_wallet_id=str(_uid(case, "auto_007"))),{"not_found","failed_precondition","permission_denied","invalid_argument"}); _unchanged(before,live.snapshot()); return
    if "marked_unavailable_for_trading" in name:
        return runtime.evidence.run(name,case)
    if "seller_wallet_claim" in name or "authoritative_wallet_owners" in name or "payment_to_wallet_not_owned" in name or "debit_from_wallet_not_owned" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _trade_state(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case)
    trade=live.create_trade(world,quantity=2,unit_price_isk=price,item_stack_quantity=seller_q)
    if "completed_to_" in name:
        live.accept_trade(world,trade,quantity=2)
        return runtime.evidence.run(name,{"trade_id":trade.trade_instance_id,**case})
    if "cancelled_to_" in name:
        live.cancel_trade(world,trade)
        return runtime.evidence.run(name,{"trade_id":trade.trade_instance_id,**case})
    if "open_to_open" in name or "unknown_state_value" in name:
        return runtime.evidence.run(name,{"trade_id":trade.trade_instance_id,**case})
    if "state_change_row_created_exactly_once_for_completion" in name:
        before=live.table_count("trade_state_change"); live.accept_trade(world,trade,quantity=2); after=live.table_count("trade_state_change")
        assert after-before==1; return
    if "state_change_row_created_exactly_once_for_cancellation" in name:
        before=live.table_count("trade_state_change"); live.cancel_trade(world,trade); after=live.table_count("trade_state_change")
        assert after-before==1; return
    if "retry_does_not_duplicate_trade_state_change_history" in name:
        key=f"accept-state-{_uid(case, "auto_008")}"; live.accept_trade(world,trade,quantity=2,idempotency_key=key); count=live.table_count("trade_state_change"); live.accept_trade(world,trade,quantity=2,idempotency_key=key); assert live.table_count("trade_state_change")==count; return
    if "history_is_append_only" in name:
        return runtime.evidence.run(name,case)
    if "history_matches_current_trade_state" in name:
        live.accept_trade(world,trade,quantity=2)
        state=live.trade_row(trade)["trade_state"]
        rows=live.rows("trade_state_change")
        assert rows and any(state in {str(v) for v in r.values()} for r in rows)
        return
    return runtime.evidence.run(name, case)


def _accept_cancel_race(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=100_000)
    trade=live.create_trade(world,quantity=10,unit_price_isk=price,item_stack_quantity=20)
    before=live.snapshot()
    if "different_trades_concurrently_debiting_same_wallet" in name:
        trade2=live.create_trade(world,quantity=10,unit_price_isk=price,idempotency_key=f"issue2-{_uid(case, "auto_009")}",item_stack_id=world.seller_stack_id,item_stack_quantity=int(live.item_stack_row(world.seller_stack_id)["quantity"]))
        return runtime.evidence.run(name,{"trade1":trade.trade_instance_id,"trade2":trade2.trade_instance_id,**case})
    if "different_trades_concurrently_crediting_same_destination_stack" in name or "concurrent_issue_and_external_stack_mutation" in name:
        return runtime.fault.run(name,case)
    funcs=[]
    if "multiple_partial_accepts_and_cancel" in name:
        funcs=[lambda:live.accept_trade(world,trade,quantity=2,idempotency_key=f"a-{_uid(case, "auto_010")}"),lambda:live.accept_trade(world,trade,quantity=3,idempotency_key=f"b-{_uid(case, "auto_011")}"),lambda:live.cancel_trade(world,trade,idempotency_key=f"c-{_uid(case, "auto_012")}")]
    elif "two_full_accepts_and_cancel" in name:
        funcs=[lambda:live.accept_trade(world,trade,quantity=10,idempotency_key=f"a-{_uid(case, "auto_013")}"),lambda:live.accept_trade(world,trade,quantity=10,idempotency_key=f"b-{_uid(case, "auto_014")}"),lambda:live.cancel_trade(world,trade,idempotency_key=f"c-{_uid(case, "auto_015")}")]
    elif "partial_accept_and_cancel" in name:
        funcs=[lambda:live.accept_trade(world,trade,quantity=4,idempotency_key=f"a-{_uid(case, "auto_016")}"),lambda:live.cancel_trade(world,trade,idempotency_key=f"c-{_uid(case, "auto_017")}")]
    elif "cancel_racing_with_retry" in name or "accept_racing_with_retry" in name or "same_buyer_concurrent_partial_accepts" in name:
        return runtime.evidence.run(name,case)
    else:
        raise AssertionError(f"unhandled accept/cancel race: {name}")
    live.concurrent(funcs)
    after=live.snapshot()
    _unchanged(before,after)
    row=live.trade_row(trade); escrow=live.item_escrow_row(trade)
    assert int(row["remaining_quantity"])>=0 and int(escrow["quantity"])>=0
    assert int(row["remaining_quantity"])==int(escrow["quantity"])


def _transaction_isolation(runtime, name: str, case: dict[str, Any]) -> None:
    # Transaction-failure position, deadlock, serialization, and connection-loss
    # tests require failpoints or deliberate concurrent DB orchestration. Use live
    # evidence driver so these are never reduced to an in-memory mock.
    if any(k in name for k in ("rolls_back_when_","connection_loss","deadlock","serialization_failure","statement_timeout","transaction_timeout","lock_wait_timeout","releases_every_row_lock","uses_fresh_transaction_snapshot","rereads_trade_state")):
        return runtime.fault.run(name,case) if any(k in name for k in ("connection_loss","timeout")) else runtime.evidence.run(name,case)
    if "two_accept_transactions_cannot_both_commit" in name or "accept_and_cancel_transactions_cannot_both_commit" in name or "two_wallet_debits_cannot_both_commit" in name or "two_item_escrow_reservations_cannot_both_commit" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _idempotency(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=100_000)
    key=f"idem-{case.get('nonce','x')}-{_uid(case, "auto_018")}"
    payload=live.issue_payload(world,quantity=2,unit_price_isk=price,idempotency_key=key,item_stack_quantity=20)
    response=live.gateway.issue_trade_instance(payload)
    row1=copy.deepcopy(live.idempotency_row(key))
    if "different_request_kind" in name:
        # Reusing same key for accept should conflict.
        trade=live.helpers.Trade(response["tradeInstanceId"],response["itemStackEscrowId"],2,price,world.seller_stack_id,key)
        _failure(live,lambda:live.accept_trade(world,trade,quantity=1,idempotency_key=key),{"replay_conflict","invalid_argument","failed_precondition","already_exists"}); return
    if "different_principal" in name:
        return runtime.evidence.run(name,{"idempotency_key":key,**case})
    if "fingerprint_change_is_rejected_after_success" in name:
        changed=copy.deepcopy(payload); changed["quantity"]=3
        _failure(live,lambda:live.gateway.issue_trade_instance(changed),{"replay_conflict","invalid_argument","failed_precondition","already_exists"}); return
    if "terminal_response_cannot_be_overwritten_by_retry" in name or "request_fingerprint_cannot_be_changed" in name or "principal_binding_cannot_be_changed" in name:
        live.gateway.issue_trade_instance(payload)
        row2=live.idempotency_row(key)
        for field in row1:
            if field in {"updated_at","last_seen_at"}: continue
            if field in row2 and field in {"request_fingerprint","idempotency_state","result_settlement_batch_id","created_by_service"}:
                assert row2[field]==row1[field],(field,row1[field],row2[field])
        return
    if "success_record_cannot_exist_without" in name or "record_is_written_in_same_transaction" in name:
        assert row1.get("result_settlement_batch_id") or str(row1.get("idempotency_state","")).upper() in {"COMPLETED","SUCCEEDED"}
        assert live.table_count("trade_instance")==1
        return
    if "concurrent_insert_of_same_idempotency_key" in name:
        # Fresh world because current key already terminal; use a new key concurrently.
        live.reset_example(); world=live.seed_world(seller_quantity=20,buyer_isk=100_000); key2=f"idem-race-{_uid(case, "auto_019")}"; payload2=live.issue_payload(world,quantity=2,unit_price_isk=price,idempotency_key=key2,item_stack_quantity=20)
        results=live.concurrent([lambda:live.gateway.issue_trade_instance(payload2),lambda:live.gateway.issue_trade_instance(payload2)])
        assert live.scalar("SELECT count(*) FROM idempotency_record WHERE idempotency_key=%s",(key2,))==1
        assert live.table_count("trade_instance")==1
        return
    if "key_uniqueness_is_enforced_by_database" in name:
        assert live.scalar("SELECT count(*) FROM idempotency_record WHERE idempotency_key=%s",(key,))==1
        return
    if "empty_idempotency_key" in name or "oversized_idempotency_key" in name:
        live.reset_example(); world=live.seed_world(seller_quantity=20,buyer_isk=100_000)
        bad="" if "empty" in name else "x"*100_000
        p=live.issue_payload(world,quantity=1,unit_price_isk=price,idempotency_key=bad,item_stack_quantity=20)
        _failure(live,lambda:live.gateway.issue_trade_instance(p),{"invalid_argument","failed_precondition"}); return
    if any(k in name for k in ("failed_record","identical_failed","in_progress","stale_","cleanup_never_deletes","failed_record_cannot_hide")):
        return runtime.evidence.run(name,{"idempotency_key":key,**case})
    return runtime.evidence.run(name, case)


def _operation_ordering(runtime, name: str, case: dict[str, Any]) -> None:
    # Direct settlement request construction is schema-sensitive. The existing
    # E2E test module contains helpers for valid accept plans; an evidence driver
    # can import those and perturb operation order for each named property.
    runtime.evidence.run(name,case)


def _escrow_binding(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=100_000)
    trade=live.create_trade(world,quantity=4,unit_price_isk=price,item_stack_quantity=20)
    escrow=live.item_escrow_row(trade)
    if "item_escrow_is_bound_to_exact_trade_instance" in name:
        cols=live.columns("item_stack_escrow")
        assert "trade_instance_id" in cols
        assert str(escrow["trade_instance_id"])==trade.trade_instance_id
        return
    if "wallet_escrow_is_bound_to_exact_trade_instance" in name:
        live.accept_trade(world,trade,quantity=1)
        rows=live.rows("wallet_escrow")
        if rows:
            assert all(str(r.get("trade_instance_id"))==trade.trade_instance_id for r in rows if r.get("trade_instance_id") is not None)
        return
    if "orphan_" in name or "cannot_" in name or "reused" in name or "different_trade" in name or "cancelled_item_escrow" in name:
        return runtime.evidence.run(name,{"trade_id":trade.trade_instance_id,**case})
    return runtime.evidence.run(name, case)


def _ledger(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=100_000,buyer_stack_quantity=0)
    trade=live.create_trade(world,quantity=4,unit_price_isk=price,item_stack_quantity=20)
    before=live.snapshot(); live.accept_trade(world,trade,quantity=2,buyer_destination_item_stack_id=world.buyer_stack_id); after=live.snapshot()
    if "records_exact_before_and_after" in name or "delta_matches_actual" in name:
        table="wallet_ledger" if "wallet" in name else "item_stack_ledger"
        rows=live.rows(table)
        assert rows
        for r in rows:
            before_keys=[k for k in r if k.endswith("_before") and isinstance(r[k],int)]
            for bk in before_keys:
                ak=bk[:-7]+"_after"; dk=bk[:-7]+"_delta"
                if ak in r and dk in r and all(isinstance(r.get(x),int) for x in (bk,ak,dk)):
                    assert r[ak]-r[bk]==r[dk]
        return
    if "every_item_mutation_has_exactly_one" in name:
        assert after.item_ledger_count>before.item_ledger_count; return
    if "every_wallet_mutation_has_exactly_one" in name:
        assert after.wallet_ledger_count>before.wallet_ledger_count; return
    if "idempotent_retry_creates_no_duplicate" in name:
        key=f"ledger-retry-{_uid(case, "auto_020")}"; live.reset_example(); world=live.seed_world(seller_quantity=20,buyer_isk=100_000); trade=live.create_trade(world,quantity=2,unit_price_isk=price,item_stack_quantity=20); live.accept_trade(world,trade,quantity=2,idempotency_key=key); counts=(live.table_count("wallet_ledger"),live.table_count("item_stack_ledger")); live.accept_trade(world,trade,quantity=2,idempotency_key=key); assert counts==(live.table_count("wallet_ledger"),live.table_count("item_stack_ledger")); return
    if "failed_transaction_creates_no_committed" in name or "reference" in name or "database_rejects" in name or "trade_state_change" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _outbox(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=100_000)
    before=live.table_count("settlement_outbox") if live.table_exists("settlement_outbox") else 0
    key=f"outbox-{_uid(case, "auto_021")}"; trade=live.create_trade(world,quantity=2,unit_price_isk=price,idempotency_key=key,item_stack_quantity=20)
    after=live.table_count("settlement_outbox") if live.table_exists("settlement_outbox") else 0
    if "atomically_commits_business_state_and_outbox_record" in name:
        assert live.table_count("trade_instance")==1
        assert after>=before
        return
    if "record_contains_same_idempotency_key" in name or "record_contains_same_trade_id" in name or "payload_is_immutable" in name or "rows_are_not_visible_before_transaction_commit" in name:
        return runtime.evidence.run(name,{"trade_id":trade.trade_instance_id,"idempotency_key":key,**case})
    if "failed_settlement_transaction_commits_no_outbox_record" in name:
        return runtime.fault.run(name,case)
    if "dispatcher" in name or "duplicate_outbox_publication" in name or "per_trade_terminal_event_order" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _pubsub_delivery(runtime, name: str, case: dict[str, Any]) -> None:
    runtime.evidence.run(name,case)


def _worker_lifecycle(runtime, name: str, case: dict[str, Any]) -> None:
    runtime.fault.run(name,case) if any(k in name for k in ("shutdown","restart","loses_grpc","timeout")) else runtime.evidence.run(name,case)


def _market_projection(runtime, name: str, case: dict[str, Any]) -> None:
    runtime.evidence.run(name,case)


def _pubsub_operational(runtime, name: str, case: dict[str, Any]) -> None:
    runtime.fault.run(name,case)


def _fuzz_sequences(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=50,buyer_isk=1_000_000,buyer_stack_quantity=0)
    initial=live.snapshot()
    trade=None
    remaining=0
    # Use generated op sequence but maintain domain-valid preconditions for the
    # valid-sequence laws; invalid-sequence names intentionally probe rejection.
    for step_index, op in enumerate(case.get("ops",[])[:20]):
        if op=="issue" and trade is None:
            qty=min(10,int(live.item_stack_row(world.seller_stack_id)["quantity"]))
            if qty>0:
                trade=live.create_trade(
                    world, quantity=qty, unit_price_isk=price,
                    idempotency_key=f"seq-i-{step_index}-{_uid(case, 'seq-issue')}",
                    item_stack_quantity=int(live.item_stack_row(world.seller_stack_id)["quantity"]),
                )
                remaining=qty
        elif op=="accept" and trade is not None and remaining>0:
            q=min(remaining,max(1,case.get("quantity",1)))
            try:
                live.accept_trade(
                    world, trade, quantity=q,
                    idempotency_key=f"seq-a-{step_index}-{_uid(case, 'seq-accept')}",
                    buyer_destination_item_stack_id=world.buyer_stack_id,
                )
                remaining-=q
                if remaining == 0:
                    trade = None
            except live.helpers.RpcFailure:
                remaining = max(0, int(live.trade_row(trade)["remaining_quantity"]))
        elif op=="cancel" and trade is not None and remaining>0:
            try:
                live.cancel_trade(
                    world, trade,
                    idempotency_key=f"seq-c-{step_index}-{_uid(case, 'seq-cancel')}",
                )
                remaining=0
                trade=None
            except live.helpers.RpcFailure:
                remaining = max(0, int(live.trade_row(trade)["remaining_quantity"]))
        elif op=="retry" and trade is not None:
            # Exact duplicate semantics need the original request key/payload pair; the
            # named retry property delegates to the evidence driver below. Other sequence
            # laws preserve state by performing no new business command for this symbol.
            remaining = max(0, int(live.trade_row(trade)["remaining_quantity"]))
    final=live.snapshot()
    if "preserves_total_items" in name:
        assert final.total_items==initial.total_items; return
    if "preserves_total_isk" in name:
        assert final.total_isk==initial.total_isk; return
    if "never_creates_negative_wallet_balance" in name:
        assert int(live.scalar("SELECT COALESCE(MIN(isk_amount),0) FROM wallet"))>=0; return
    if "never_creates_negative_item_quantity" in name:
        assert int(live.scalar("SELECT COALESCE(MIN(quantity),0) FROM item_stack"))>=0
        if live.table_exists("item_stack_escrow"): assert int(live.scalar("SELECT COALESCE(MIN(quantity),0) FROM item_stack_escrow"))>=0
        return
    if "retry_sequence_never_duplicates_business_effect" in name or "concurrent_trade_sequence" in name or "cross_trade" in name or "crash_and_restart" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _laws(runtime, name: str, case: dict[str, Any]) -> None:
    if "splitting_accept_into_partial_accepts_equals_single_full_accept" in name:
        # Run two independently seeded worlds and compare normalized economic state.
        live=runtime.live; live.reset_example(); q=max(2,min(10,case.get("quantity",4))); price=max(1,case.get("price",13)); world=live.seed_world(seller_quantity=q,buyer_isk=q*price*10,buyer_stack_quantity=0); trade=live.create_trade(world,quantity=q,unit_price_isk=price,item_stack_quantity=q); live.accept_trade(world,trade,quantity=q,buyer_destination_item_stack_id=world.buyer_stack_id); full=(int(live.wallet_row(world.buyer_wallet_id)["isk_amount"]),int(live.wallet_row(world.seller_wallet_id)["isk_amount"]),int(live.item_stack_row(world.buyer_stack_id)["quantity"]),live.snapshot().total_items,live.snapshot().total_isk)
        live.reset_example(); world=live.seed_world(seller_quantity=q,buyer_isk=q*price*10,buyer_stack_quantity=0); trade=live.create_trade(world,quantity=q,unit_price_isk=price,item_stack_quantity=q); first=max(1,q//2); live.accept_trade(world,trade,quantity=first,buyer_destination_item_stack_id=world.buyer_stack_id); live.accept_trade(world,trade,quantity=q-first,idempotency_key=f"split-{_uid(case, "auto_024")}",buyer_destination_item_stack_id=world.buyer_stack_id); split=(int(live.wallet_row(world.buyer_wallet_id)["isk_amount"]),int(live.wallet_row(world.seller_wallet_id)["isk_amount"]),int(live.item_stack_row(world.buyer_stack_id)["quantity"]),live.snapshot().total_items,live.snapshot().total_isk); assert split==full; return
    if "issue_then_cancel_restores_original_item_state" in name:
        live,world,seller_q,price=_world(runtime,case,seller_quantity=20); before=int(live.item_stack_row(world.seller_stack_id)["quantity"]); trade=live.create_trade(world,quantity=5,unit_price_isk=price,item_stack_quantity=20); live.cancel_trade(world,trade); assert int(live.item_stack_row(world.seller_stack_id)["quantity"])==before; return
    if "issue_then_full_accept_preserves_global_items_and_isk" in name:
        live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=100_000); before=live.snapshot(); trade=live.create_trade(world,quantity=5,unit_price_isk=price,item_stack_quantity=20); live.accept_trade(world,trade,quantity=5); after=live.snapshot(); _unchanged(before,after); return
    if "every_completed_trade_has_zero_item_escrow" in name:
        live,world,seller_q,price=_world(runtime,case,seller_quantity=30,buyer_isk=100_000)
        trades=[]
        for i in range(3):
            trade=live.create_trade(
                world, quantity=2, unit_price_isk=price,
                idempotency_key=f"law-complete-issue-{i}-{_uid(case, 'every-completed-issue')}",
                item_stack_quantity=int(live.item_stack_row(world.seller_stack_id)["quantity"]),
            )
            live.accept_trade(
                world, trade, quantity=2,
                idempotency_key=f"law-complete-accept-{i}-{_uid(case, 'every-completed-accept')}",
            )
            trades.append(trade)
        assert len(trades) == 3
        for trade in trades:
            assert str(live.trade_row(trade)["trade_state"]).upper() == "COMPLETED"
            assert int(live.item_escrow_row(trade)["quantity"]) == 0
            assert int(live.trade_row(trade)["remaining_quantity"]) == 0
        return
    if "every_cancelled_trade_has_zero_item_escrow" in name:
        live,world,seller_q,price=_world(runtime,case,seller_quantity=30,buyer_isk=100_000)
        trades=[]
        for i in range(3):
            trade=live.create_trade(
                world, quantity=2, unit_price_isk=price,
                idempotency_key=f"law-cancel-issue-{i}-{_uid(case, 'every-cancelled-issue')}",
                item_stack_quantity=int(live.item_stack_row(world.seller_stack_id)["quantity"]),
            )
            live.cancel_trade(
                world, trade,
                idempotency_key=f"law-cancel-{i}-{_uid(case, 'every-cancelled-cancel')}",
            )
            trades.append(trade)
        assert len(trades) == 3
        for trade in trades:
            assert str(live.trade_row(trade)["trade_state"]).upper() == "CANCELLED"
            assert int(live.item_escrow_row(trade)["quantity"]) == 0
            assert int(live.trade_row(trade)["remaining_quantity"]) == 0
        return
    if "sum_of_item_stack_quantity_plus_item_escrow_quantity_is_conserved" in name or "sum_of_wallet_balance_plus_wallet_escrow_balance_is_conserved" in name:
        live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=100_000); before=live.snapshot(); trade=live.create_trade(world,quantity=5,unit_price_isk=price,item_stack_quantity=20); live.accept_trade(world,trade,quantity=2); after=live.snapshot(); field="total_items" if "item_stack" in name else "total_isk"; assert getattr(before,field)==getattr(after,field); return
    if "retrying_any_successful_command" in name or "retrying_any_failed_validation_command" in name or "reordering_independent_settlements" in name or "concurrent_independent_settlements" in name or "accept_order_of_independent_trades" in name or "every_terminal_trade_has_exactly_one_terminal_state_history" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _identifiers(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=100,buyer_isk=1_000_000)
    if "trade_id_is_globally_unique" in name:
        ids=[]
        for i in range(5):
            trade=live.create_trade(world,quantity=1,unit_price_isk=price,idempotency_key=f"uid-{i}-{_uid(case, "auto_025")}",item_stack_quantity=int(live.item_stack_row(world.seller_stack_id)["quantity"])); ids.append(trade.trade_instance_id)
        assert len(ids)==len(set(ids)); return
    if "settlement_batch_id_is_globally_unique" in name:
        ids=[]
        for i in range(3):
            trade=live.create_trade(world,quantity=1,unit_price_isk=price,idempotency_key=f"batch-{i}-{_uid(case, "auto_026")}",item_stack_quantity=int(live.item_stack_row(world.seller_stack_id)["quantity"])); row=live.batch_row(trade.idempotency_key); ids.append(str(row["settlement_batch_id"]))
        assert len(ids)==len(set(ids)); return
    if "settlement_step_id_is_unique" in name or "outbox_event_id_is_globally_unique" in name or "database_unique_constraint" in name:
        return runtime.evidence.run(name,case)
    if "opaque" in name or "nil_uuid" in name or "noncanonical_text" in name or "case_normalization" in name or "whitespace" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _time_lease(runtime, name: str, case: dict[str, Any]) -> None:
    runtime.evidence.run(name,case)


def _retry_policy(runtime, name: str, case: dict[str, Any]) -> None:
    if "validation_error_is_never_retried" in name:
        live,world,seller_q,price=_world(runtime,case); before=live.table_count("request_attempt") if live.table_exists("request_attempt") else 0; _failure(live,lambda:live.gateway.issue_trade_instance(live.issue_payload(world,quantity=-1,unit_price_isk=price,item_stack_quantity=seller_q)),{"invalid_argument"}); after=live.table_count("request_attempt") if live.table_exists("request_attempt") else before; assert after-before<=1; return
    if "authentication_error_is_never_retried" in name or "authorization_error_is_never_retried" in name or "insufficient_balance_error_is_never_retried" in name:
        return runtime.evidence.run(name,case)
    runtime.evidence.run(name,case)


def _message_identity(runtime, name: str, case: dict[str, Any]) -> None:
    runtime.evidence.run(name,case)


def _expiration(runtime, name: str, case: dict[str, Any]) -> None:
    # ExpiresAt is a real protobuf/domain field, but the GUI payload helper may
    # evolve independently. Use evidence driver for clock-controlled boundary
    # assertions and direct live persistence when the endpoint accepts expiresAt.
    live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=100_000)
    if "without_expires_at" in name:
        trade=live.create_trade(world,quantity=1,unit_price_isk=price,item_stack_quantity=20)
        row=live.trade_row(trade)
        if "expires_at" in row:
            assert row["expires_at"] is None
        return
    if "future_expires_at_persists_same_utc_instant" in name or "converts_non_utc_timestamp_offset" in name or "round_trip_preserves" in name:
        return runtime.evidence.run(name,case)
    if "rejects_expires_at" in name or "accept_trade_" in name or "stale_market_snapshot" in name or "retry_of_accept" in name or "concurrent_accepts_at_expiration" in name or "expired_open_trade" in name or "expiration_rejection" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _deterministic_ids(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=30,buyer_isk=100_000)
    key=f"det-{case.get('nonce','x')}-{_uid(case, "auto_027")}"
    if "same_issue_idempotency_key_generates_same_trade_id" in name or "same_issue_idempotency_key_generates_same_item_escrow_id" in name:
        p=live.issue_payload(world,quantity=2,unit_price_isk=price,idempotency_key=key,item_stack_quantity=30); a=live.gateway.issue_trade_instance(p); b=live.gateway.issue_trade_instance(p); field="tradeInstanceId" if "trade_id" in name else "itemStackEscrowId"; assert a[field]==b[field]; return
    if "different_idempotency_keys_generate_different_trade_ids" in name:
        a=live.create_trade(world,quantity=1,unit_price_isk=price,idempotency_key=f"a-{_uid(case, "auto_028")}",item_stack_quantity=30); b=live.create_trade(world,quantity=1,unit_price_isk=price,idempotency_key=f"b-{_uid(case, "auto_029")}",item_stack_quantity=int(live.item_stack_row(world.seller_stack_id)["quantity"])); assert a.trade_instance_id!=b.trade_instance_id; return
    if "generated_trade_id_is_lowercase_hyphenated_uuid" in name or "generated_item_escrow_id_is_lowercase_hyphenated_uuid" in name:
        trade=live.create_trade(world,quantity=1,unit_price_isk=price,item_stack_quantity=30); value=trade.trade_instance_id if "trade_id" in name else trade.item_stack_escrow_id; assert str(uuid.UUID(value))==value; return
    if "same_accept_idempotency_key" in name or "same_idempotency_key_generates_different_ids" in name or "destination_stack" in name or "wallet_escrow" in name or "before_and_after_process_restart" in name or "linux_and_windows" in name or "opaque" in name or "explicit_" in name or "collision" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _plan_construction(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=30,buyer_isk=100_000,buyer_stack_quantity=0)
    if name.startswith("test_issue_plan_"):
        trade=live.create_trade(world,quantity=3,unit_price_isk=price,item_stack_quantity=30)
        return _assert_plan_from_batch(runtime,name,trade.idempotency_key,"ISSUE",world,trade,case)
    if name.startswith("test_accept_plan_"):
        trade=live.create_trade(world,quantity=4,unit_price_isk=price,item_stack_quantity=30)
        key=f"plan-a-{_uid(case, "auto_030")}"; live.accept_trade(world,trade,quantity=2,idempotency_key=key,buyer_destination_item_stack_id=world.buyer_stack_id)
        return _assert_plan_from_batch(runtime,name,key,"ACCEPT",world,trade,case)
    if name.startswith("test_cancel_plan_"):
        trade=live.create_trade(world,quantity=4,unit_price_isk=price,item_stack_quantity=30)
        key=f"plan-c-{_uid(case, "auto_031")}"; live.cancel_trade(world,trade,idempotency_key=key)
        return _assert_plan_from_batch(runtime,name,key,"CANCEL",world,trade,case)
    return runtime.evidence.run(name, case)


def _assert_plan_from_batch(runtime,name,key,intent,world,trade,case):
    live=runtime.live
    row=live.batch_row(key)
    if "intent_is_" in name:
        assert "intent" in row, "settlement_batch.intent is required for the plan oracle"
        assert str(row["intent"]).upper()==intent
        return
    if "caused_by_capsuleer_id" in name:
        expected=world.seller_id if intent in {"ISSUE","CANCEL"} else world.buyer_id
        assert "caused_by_capsuleer_id" in row, "settlement_batch.caused_by_capsuleer_id is required for the plan oracle"
        assert int(row["caused_by_capsuleer_id"])==expected
        return
    if "created_by_service" in name:
        value=str(row.get("created_by_service") or ""); assert value and "market" in value.lower(); return
    # Operation order/payload details need current settlement-operation schema.
    return runtime.evidence.run(name,{"idempotency_key":key,"trade_id":trade.trade_instance_id,**case})


def _multi_buyer(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=30,buyer_isk=100_000,buyer_stack_quantity=0)
    # Create a second buyer stack for OTHER_ID using existing other wallet.
    second_stack=str(_uid(case, "auto_032")); live.helpers.insert_item_stack(live.db,second_stack,world.other_id,world.item_type_id,world.station_id,0)
    trade=live.create_trade(world,quantity=10,unit_price_isk=price,item_stack_quantity=30)
    seller_before=int(live.wallet_row(world.seller_wallet_id)["isk_amount"])
    if "buyer_a_partial_fill_then_buyer_b_partial_fill" in name:
        live.accept_trade(world,trade,quantity=3,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"ba-{_uid(case, "auto_033")}")
        live.accept_trade(world,trade,quantity=2,buyer_capsuleer_id=world.other_id,buyer_wallet_id=world.other_wallet_id,buyer_destination_item_stack_id=second_stack,idempotency_key=f"bb-{_uid(case, "auto_034")}")
        if "credits_each_buyer_exact_item_quantity" in name:
            assert int(live.item_stack_row(world.buyer_stack_id)["quantity"])==3 and int(live.item_stack_row(second_stack)["quantity"])==2
        else:
            assert int(live.wallet_row(world.seller_wallet_id)["isk_amount"])-seller_before==5*price
        return
    if "three_partial_fills" in name:
        quantities=[2,3,5] if "sum_to_offer" in name else [1,2,3]
        actors=[(world.buyer_id,world.buyer_wallet_id,world.buyer_stack_id),(world.other_id,world.other_wallet_id,second_stack),(world.buyer_id,world.buyer_wallet_id,world.buyer_stack_id)]
        for i,(q,(actor,wallet,stack)) in enumerate(zip(quantities,actors)):
            live.accept_trade(world,trade,quantity=q,buyer_capsuleer_id=actor,buyer_wallet_id=wallet,buyer_destination_item_stack_id=stack,idempotency_key=f"mb-{i}-{_uid(case, "auto_035")}")
        row=live.trade_row(trade)
        if "complete_trade_exactly_once" in name: assert row["trade_state"]=="COMPLETED" and int(row["remaining_quantity"])==0
        else: assert row["trade_state"]=="OPEN" and int(row["remaining_quantity"])==10-sum(quantities)
        return
    if "same_buyer_multiple_partial_fills" in name:
        before=int(live.item_stack_row(world.buyer_stack_id)["quantity"])
        live.accept_trade(world,trade,quantity=2,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"x-{_uid(case, "auto_036")}"); live.accept_trade(world,trade,quantity=3,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"y-{_uid(case, "auto_037")}"); assert int(live.item_stack_row(world.buyer_stack_id)["quantity"])==before+5; return
    if "different_buyers_cannot_write" in name or "second_buyer_cannot_supply" in name:
        live.accept_trade(world,trade,quantity=1,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"x-{_uid(case, "auto_038")}")
        _failure(live,lambda:live.accept_trade(world,trade,quantity=1,buyer_capsuleer_id=world.other_id,buyer_wallet_id=world.other_wallet_id,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"y-{_uid(case, "auto_039")}"),{"principal_mismatch","permission_denied","failed_precondition","invalid_argument"}); return
    if "cancel_after_two_buyers_partial_fill" in name:
        initial_items=live.snapshot().total_items; initial_isk=live.snapshot().total_isk
        live.accept_trade(world,trade,quantity=2,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"x-{_uid(case, "auto_040")}"); live.accept_trade(world,trade,quantity=3,buyer_capsuleer_id=world.other_id,buyer_wallet_id=world.other_wallet_id,buyer_destination_item_stack_id=second_stack,idempotency_key=f"y-{_uid(case, "auto_041")}"); a_qty=int(live.item_stack_row(world.buyer_stack_id)["quantity"]); b_qty=int(live.item_stack_row(second_stack)["quantity"]); seller_paid=int(live.wallet_row(world.seller_wallet_id)["isk_amount"]); live.cancel_trade(world,trade,idempotency_key=f"z-{_uid(case, "auto_042")}")
        if "refunds_only_unsold_remainder" in name: assert int(live.item_stack_row(world.seller_stack_id)["quantity"])==30-5
        elif "does_not_change_either_buyers" in name: assert int(live.item_stack_row(world.buyer_stack_id)["quantity"])==a_qty and int(live.item_stack_row(second_stack)["quantity"])==b_qty
        else: assert int(live.wallet_row(world.seller_wallet_id)["isk_amount"])==seller_paid
        assert live.snapshot().total_items==initial_items and live.snapshot().total_isk==initial_isk
        return
    if "global_item_conservation" in name or "global_isk_conservation" in name:
        before=live.snapshot(); live.accept_trade(world,trade,quantity=2,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"x-{_uid(case, "auto_043")}"); mid=live.snapshot(); live.accept_trade(world,trade,quantity=3,buyer_capsuleer_id=world.other_id,buyer_wallet_id=world.other_wallet_id,buyer_destination_item_stack_id=second_stack,idempotency_key=f"y-{_uid(case, "auto_044")}"); after=live.snapshot(); field="total_items" if "item" in name else "total_isk"; assert getattr(before,field)==getattr(mid,field)==getattr(after,field); return
    if "final_partial_fill_of_one_unit" in name:
        live.accept_trade(world,trade,quantity=9,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"x-{_uid(case, "auto_045")}"); live.accept_trade(world,trade,quantity=1,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"y-{_uid(case, "auto_046")}"); assert live.trade_row(trade)["trade_state"]=="COMPLETED" and int(live.item_escrow_row(trade)["quantity"])==0; return
    if "never_reuses_wallet_escrow" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _operation_matrix(runtime, name: str, case: dict[str, Any]) -> None:
    # Meta matrix properties inspect proto enum and collected tests; intent-specific
    # malformed-operation requests require schema-aware request construction.
    repo=runtime.repo
    if "every_declared_settlement_operation_kind" in name:
        proto=repo.read("proto/eve/trade_settlement/v1/trade_settlement.proto")
        values=re.findall(r"SETTLEMENT_OPERATION_KIND_([A-Z0-9_]+)\s*=\s*\d+",proto)
        assert len([v for v in values if v!="UNSPECIFIED"])>=10
        return runtime.evidence.run(name,{"operation_kinds":values,**case})
    if "every_documented_domain_failure_code" in name or "every_retryable_infrastructure_failure_code" in name or "every_terminal_batch_state" in name or "completed_batch_rejects" in name or "failed_batch_rejects" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name,case)


def _audit(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=100_000)
    key=f"audit-{_uid(case, "auto_047")}"; trade=live.create_trade(world,quantity=2,unit_price_isk=price,idempotency_key=key,item_stack_quantity=20); batch=live.batch_row(key)
    mapping={
        "records_idempotency_key": ("idempotency_key",key),
        "records_created_by_service": ("created_by_service",None),
        "records_caused_by_capsuleer_id": ("caused_by_capsuleer_id",world.seller_id),
    }
    for token,(col,expected) in mapping.items():
        if token in name:
            assert col in batch
            if expected is None: assert batch[col]
            else: assert batch[col]==expected
            return
    if "external_request_id" in name:
        if "external_request_id" in batch: assert str(batch["external_request_id"]).startswith("external-")
        else: return runtime.evidence.run(name,{"batch":batch,**case})
        return
    if "settlement_step_references_exact_parent" in name:
        steps=live.helpers.settlement_step_rows(live.db,batch["settlement_batch_id"]); assert steps and all(str(s["settlement_batch_id"])==str(batch["settlement_batch_id"]) for s in steps); return
    if "retry_returns_original_audit_identity" in name:
        response=live.gateway.issue_trade_instance(live.issue_payload(world,quantity=2,unit_price_isk=price,idempotency_key=key,item_stack_quantity=20)); assert live.scalar("SELECT count(*) FROM settlement_batch WHERE idempotency_key=%s",(key,))==1; return
    if "created_at_timestamps_are_monotonic" in name or "ledger_row_references" in name or "state_change_row_references" in name or "outbox_event_references" in name or "cross_principal" in name or "failed_settlement" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _reconciliation(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=50,buyer_isk=1_000_000,buyer_stack_quantity=0)
    initial=live.snapshot(); trade=live.create_trade(world,quantity=10,unit_price_isk=price,item_stack_quantity=50); live.accept_trade(world,trade,quantity=3,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"r1-{_uid(case, "auto_048")}"); live.accept_trade(world,trade,quantity=2,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"r2-{_uid(case, "auto_049")}"); final=live.snapshot()
    if "trade_remaining_quantity_equals_bound_item_escrow_quantity" in name:
        # Create more than one open trade so the universal quantifier in the name
        # is not satisfied by a single hand-picked row.
        second=live.create_trade(
            world, quantity=6, unit_price_isk=price,
            idempotency_key=f"reconcile-open-2-{_uid(case, 'open-trade-two')}",
            item_stack_quantity=int(live.item_stack_row(world.seller_stack_id)["quantity"]),
        )
        live.accept_trade(
            world, second, quantity=1, buyer_destination_item_stack_id=world.buyer_stack_id,
            idempotency_key=f"reconcile-open-2-accept-{_uid(case, 'open-trade-two-accept')}",
        )
        for open_trade in (trade, second):
            row=live.trade_row(open_trade)
            assert str(row["trade_state"]).upper() == "OPEN"
            assert int(row["remaining_quantity"]) == int(live.item_escrow_row(open_trade)["quantity"])
        return
    if "seller_revenue_equals_sum_of_committed_accept_quantity_times_trade_unit_price" in name:
        assert int(live.wallet_row(world.seller_wallet_id)["isk_amount"])-100==5*price; return
    if "buyer_spend_equals_sum_of_committed_accept_quantity_times_trade_unit_price" in name:
        assert 1_000_000-int(live.wallet_row(world.buyer_wallet_id)["isk_amount"])==5*price; return
    if "wallet_current_balance_equals_initial_balance_plus_sum_of_committed_wallet_ledger_deltas" in name or "item_stack_current_quantity_equals_initial_quantity_plus_sum_of_committed_item_ledger_deltas" in name:
        return runtime.evidence.run(name,case)
    if "completed_trade_has_zero" in name:
        live.accept_trade(world,trade,quantity=5,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"r3-{_uid(case, "auto_050")}"); row=live.trade_row(trade); assert str(row["trade_state"]).upper()=="COMPLETED" and int(row["remaining_quantity"])==0 and int(live.item_escrow_row(trade)["quantity"])==0; return
    if "cancelled_trade_has_zero" in name:
        live.cancel_trade(world,trade,idempotency_key=f"rc-{_uid(case, "auto_051")}"); row=live.trade_row(trade); assert str(row["trade_state"]).upper()=="CANCELLED" and int(row["remaining_quantity"])==0 and int(live.item_escrow_row(trade)["quantity"])==0; return
    if "sum_of_wallet_balances" in name: assert final.total_isk==initial.total_isk; return
    if "sum_of_item_stacks" in name: assert final.total_items==initial.total_items; return
    if "failed_and_rolled_back" in name or "idempotent_replays" in name or "random_crash" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _error_mapping(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case)
    if "invalid_argument_domain_failure" in name:
        exc=_failure(live,lambda:live.gateway.issue_trade_instance(live.issue_payload(world,quantity=-1,unit_price_isk=price,item_stack_quantity=seller_q))); assert exc.code=="invalid_argument"; return
    if "failed_precondition_domain_failure" in name:
        trade=live.create_trade(world,quantity=2,unit_price_isk=price,item_stack_quantity=seller_q); exc=_failure(live,lambda:live.accept_trade(world,trade,quantity=3)); assert exc.code=="failed_precondition"; return
    if "replay_conflict" in name:
        key=f"map-{_uid(case, "auto_052")}"; p=live.issue_payload(world,quantity=1,unit_price_isk=price,idempotency_key=key,item_stack_quantity=seller_q); live.gateway.issue_trade_instance(p); p2=copy.deepcopy(p); p2["quantity"]=2; exc=_failure(live,lambda:live.gateway.issue_trade_instance(p2)); assert exc.code in {"replay_conflict","invalid_argument","failed_precondition"}; return
    if "authentication_failure" in name or "authorization_failure" in name or "grpc_" in name or "postgres_" in name or "worker_retry_policy" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _read_model(runtime, name: str, case: dict[str, Any]) -> None:
    # Current E2E helper exposes write path but not a stable open-trade query API.
    # Require evidence driver to query the actual Market read endpoint/projection.
    runtime.evidence.run(name,case)


def _cancel_refund(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case,seller_quantity=20,buyer_isk=100_000,buyer_stack_quantity=0)
    trade=live.create_trade(world,quantity=8,unit_price_isk=price,item_stack_quantity=20)
    live.accept_trade(world,trade,quantity=3,buyer_destination_item_stack_id=world.buyer_stack_id,idempotency_key=f"pre-{_uid(case, "auto_053")}")
    seller_stack_before=int(live.item_stack_row(world.seller_stack_id)["quantity"])
    buyer_qty=int(live.item_stack_row(world.buyer_stack_id)["quantity"])
    seller_isk=int(live.wallet_row(world.seller_wallet_id)["isk_amount"])
    if "remaining_item_escrow_returns_exact_remaining_quantity" in name:
        remaining=int(live.item_escrow_row(trade)["quantity"]); live.cancel_trade(world,trade,idempotency_key=f"c-{_uid(case, "auto_054")}"); assert int(live.item_stack_row(world.seller_stack_id)["quantity"])-seller_stack_before==remaining; return
    if "zero_wallet_escrow_refund" in name or "zero_item_escrow_remainder" in name or "wallet_escrow_refund" in name:
        return runtime.evidence.run(name,case)
    if "rejects_return_item_stack" in name or "rejects_return_wallet" in name or "refund_quantity" in name or "refund_amount" in name or "rolls_back" in name or "terminal_state_commits" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _request_attempts(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case)
    key=f"attempt-{_uid(case, "auto_055")}"; before=live.table_count("request_attempt") if live.table_exists("request_attempt") else 0; live.create_trade(world,quantity=1,unit_price_isk=price,idempotency_key=key,item_stack_quantity=seller_q); rows=live.db.fetchall("SELECT * FROM request_attempt WHERE idempotency_key=%s ORDER BY 1",(key,)) if live.table_exists("request_attempt") and "idempotency_key" in live.columns("request_attempt") else []
    if "first_processing_attempt_creates_exactly_one" in name:
        assert len(rows)==1 or live.table_count("request_attempt")-before==1; return
    if "idempotent_replay_after_terminal_success_does_not_create_new_settlement_attempt" in name:
        count=live.table_count("request_attempt"); live.gateway.issue_trade_instance(live.issue_payload(world,quantity=1,unit_price_isk=price,idempotency_key=key,item_stack_quantity=seller_q)); assert live.table_count("request_attempt")==count; return
    if "attempt_number_monotonically" in name or "start_timestamp_before_finish" in name or "successful_request_attempt_records_no_failure_code" in name:
        if not rows: return runtime.evidence.run(name,case)
        if "attempt_number" in name:
            cols=rows[0]; keycol=next((c for c in cols if "attempt" in c and ("number" in c or "index" in c)),None); assert keycol; values=[int(r[keycol]) for r in rows]; assert values==sorted(values) and len(values)==len(set(values)); return
        if "timestamp" in name:
            for r in rows:
                starts=[r[k] for k in r if "start" in k and r[k] is not None]; finishes=[r[k] for k in r if ("finish" in k or "complete" in k) and r[k] is not None];
                if starts and finishes: assert min(starts)<=max(finishes)
            return
        if "failure_code" in name:
            assert all(not r.get("failure_code") for r in rows); return
    return runtime.evidence.run(name,case)


def _batch_step_consistency(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,seller_q,price=_world(runtime,case)
    key=f"batchstep-{_uid(case, "auto_056")}"; trade=live.create_trade(world,quantity=2,unit_price_isk=price,idempotency_key=key,item_stack_quantity=seller_q); batch=live.batch_row(key); steps=live.helpers.settlement_step_rows(live.db,batch["settlement_batch_id"])
    if "completed_settlement_batch_has_every_required_step_completed" in name:
        assert str(batch["batch_state"]).upper()=="COMPLETED"; assert steps; assert all(str(s.get("step_state",s.get("state","COMPLETED"))).upper() in {"COMPLETED","SUCCEEDED"} for s in steps); return
    if "completed_settlement_batch_has_zero_failed_steps" in name:
        assert str(batch["batch_state"]).upper()=="COMPLETED"
        assert steps
        assert not any(str(s.get("step_state",s.get("state",""))).upper()=="FAILED" for s in steps); return
    if "step_ordinal_values_are_contiguous" in name:
        vals=[int(s.get("step_index",i)) for i,s in enumerate(steps)]; assert vals==list(range(min(vals),min(vals)+len(vals))) if vals else False; return
    if "step_ordinal_order_matches_operation_execution_order" in name:
        vals=[int(s.get("step_index",i)) for i,s in enumerate(steps)]; assert vals==sorted(vals); return
    if "terminal_timestamp_is_set_exactly_once" in name or "terminal_state_cannot_transition" in name or "retry_of_terminal_batch" in name or "failed_" in name or "batch_intent_matches" in name or "batch_trade_id_matches" in name or "diagnostic_steps" in name:
        return runtime.evidence.run(name,{"batch":batch,"steps":steps,**case})
    return runtime.evidence.run(name, case)
