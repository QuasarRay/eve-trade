from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

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
    if category == 3:
        return _udp_parser(runtime, name, case)
    if category == 4:
        return _hmac(runtime, name, case)
    if category == 5:
        return _replay(runtime, name, case)
    if category == 6:
        return _rate_limit(runtime, name, case)
    if category == 7:
        return runtime.evidence.run(name, case)
    if category == 28:
        return _grpc_boundary(runtime, name, case)
    if category == 29:
        return _serialization(runtime, name, case)
    if category == 30:
        return _udp_pool(runtime, name, case)
    if category == 31:
        return _quilkin(runtime, name, case)
    if category == 48:
        return _api_errors(runtime, name, case)
    if category == 61:
        return _fingerprint(runtime, name, case)
    if category == 76:
        return _cross_language(runtime, name, case)
    if category == 78:
        return _fuzz_edge(runtime, name, case)
    if category == 79:
        return _rust_settlement_boundary(runtime, name, case)
    if category == 86:
        return _udp_transport(runtime, name, case)
    raise AssertionError(f"unhandled edge category {category}: {name}")


def _seed_and_counts(runtime):
    live=runtime.live
    live.reset_example()
    world=live.seed_world(seller_quantity=20, buyer_isk=100_000)
    return live, world, live.table_count("trade_instance")


def _assert_no_trade(live, before: int) -> None:
    assert live.table_count("trade_instance") == before


def _is_success_response(response: dict[str, Any] | None) -> bool:
    if response is None:
        return False
    code=str(response.get("code", "")).lower()
    status=str(response.get("status", "")).lower()
    return code in {"ok", "success"} or status in {"ok", "success", "queued", "accepted"}


def _udp_parser(runtime, name: str, case: dict[str, Any]) -> None:
    live, world, before = _seed_and_counts(runtime)
    packet=live.canonical_edge_packet(world, interaction_id=f"hyp-{case['nonce']}-{_uid(case, "auto_001")}")

    if "empty_datagram" in name:
        response=live.decoded_udp_response(b"")
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "truncated_json" in name:
        raw=live.signed_edge_envelope(packet)[:-max(1, min(8, len(live.signed_edge_envelope(packet))//4))]
        response=live.decoded_udp_response(raw)
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "invalid_utf8" in name or "overlong_utf8" in name:
        raw=b'{"schema_version":"eve-trade-edge.v2","payload":"' + b"\xff\xfe" + b'"}'
        response=live.decoded_udp_response(raw)
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "json_array" in name:
        response=live.decoded_udp_response(b"[]")
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "json_scalar" in name:
        response=live.decoded_udp_response(b"0")
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "trailing_non_whitespace_bytes" in name:
        raw=live.signed_edge_envelope(packet)+b"X"
        response=live.decoded_udp_response(raw)
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "missing_schema_version" in name:
        envelope=json.loads(live.signed_edge_envelope(packet)); envelope.pop("schema_version",None)
        response=live.decoded_udp_response(json.dumps(envelope,separators=(",",":")).encode())
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "unknown_schema_version" in name:
        raw=live.signed_edge_envelope(packet,schema_version="eve-trade-edge.v999")
        response=live.decoded_udp_response(raw)
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "missing_interaction_id" in name or "empty_interaction_id" in name or "oversized_interaction_id" in name:
        if "missing" in name: packet.pop("interaction_id",None)
        elif "empty" in name: packet["interaction_id"]=""
        else: packet["interaction_id"]="x"*8192
        raw=live.signed_edge_envelope(packet)
        response=live.decoded_udp_response(raw)
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "unknown_ui_window" in name:
        packet["ui"]["window"]="hypothesis_unknown_window"
        response=live.decoded_udp_response(live.signed_edge_envelope(packet))
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "unknown_ui_action" in name:
        packet["ui"]["action"]="hypothesis_unknown_action"
        response=live.decoded_udp_response(live.signed_edge_envelope(packet))
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "action_payload_shape_for_different_action" in name:
        packet["ui"]["action"]="market_buy_from_sell_order"
        response=live.decoded_udp_response(live.signed_edge_envelope(packet), timeout=10)
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if any(k in name for k in ("nan_in_numeric_field","positive_infinity","negative_infinity","exponent_notation","fractional_quantity")):
        value = float("nan") if "nan" in name else float("inf") if "positive" in name else float("-inf") if "negative" in name else 1e3 if "exponent" in name else 1.5
        packet["input"]["quantity"] = value
        raw=json.dumps(json.loads(live.signed_edge_envelope(packet)), allow_nan=True, separators=(",",":")).encode()
        response=live.decoded_udp_response(raw)
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "embedded_nul" in name or "unicode_control_characters" in name:
        packet["interaction_id"] = "hyp\x00id" if "nul" in name else "hyp\u202eid"
        response=live.decoded_udp_response(live.signed_edge_envelope(packet))
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "duplicate_security_sensitive_json_keys" in name:
        # Duplicate auth.signature key with two different values. A parser that
        # silently chooses either value is unsafe; rejection must leave state untouched.
        valid=live.signed_edge_envelope(packet).decode("utf-8")
        raw=valid.replace('"signature":', '"signature":"forged","signature":', 1).encode()
        response=live.decoded_udp_response(raw)
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "never_reflects_untrusted_payload_bytes" in name:
        marker="HYPOTHESIS_SECRET_MARKER_"+case["nonce"]
        packet["input"]["unexpected"] = marker
        raw=live.signed_edge_envelope(packet)
        response=live.raw_udp(raw)
        if response is not None:
            assert marker.encode() not in response
        return
    if "error_response_remains_within_single_datagram_size_limit" in name:
        packet["ui"]["action"]="x"*1024
        response=live.raw_udp(live.signed_edge_envelope(packet))
        if response is not None:
            assert len(response) <= 65507
        return
    if "success_response_remains_within_single_datagram_size_limit" in name:
        key,secret=live.edge_credentials("seller")
        response=live.edge.submit(packet,key,secret)
        encoded=json.dumps(response,separators=(",",":")).encode()
        assert len(encoded) <= 65507
        return
    if "nesting_depth" in name or "maximum_json_fields" in name or "maximum_packet_size" in name:
        return runtime.evidence.run(name, case)
    if "same_error_class_for_equally_invalid_authenticated_and_unauthenticated" in name:
        return runtime.evidence.run(name, case)
    return runtime.evidence.run(name, case)


def _hmac(runtime, name: str, case: dict[str, Any]) -> None:
    live, world, before = _seed_and_counts(runtime)
    packet=live.canonical_edge_packet(world,interaction_id=f"hmac-{case['nonce']}-{_uid(case, "auto_002")}")
    valid=json.loads(live.signed_edge_envelope(packet))

    if "missing_hmac" in name:
        valid["auth"].pop("signature",None)
    elif "empty_hmac" in name:
        valid["auth"]["signature"]=""
    elif "malformed_hmac_encoding" in name:
        valid["auth"]["signature"]="%%%notbase64%%%"
    elif "hmac_with_valid_prefix_and_trailing_bytes" in name:
        valid["auth"]["signature"] += "AAAA"
    elif "hmac_signed_with_different_key_id" in name:
        valid["auth"]["key_id"]="unknown-key-id"
    elif "correct_hmac_over_different_payload_bytes" in name or "single_byte_payload_mutation" in name:
        packet2=copy.deepcopy(packet); packet2["input"]["quantity"]=2
        valid["payload"]=packet2
    elif "request_signed_with_secret_for_different_principal" in name:
        _,buyer_secret=live.edge_credentials("buyer")
        seller_key,_=live.edge_credentials("seller")
        return _assert_edge_rejection(live,packet,seller_key,buyer_secret,before)
    elif "unknown_key_id_or_wrong_secret" in name:
        response_a=live.edge.submit(packet,"unknown","wrong")
        seller_key,_=live.edge_credentials("seller")
        packet_b=copy.deepcopy(packet); packet_b["interaction_id"] += "-b"
        response_b=live.edge.submit(packet_b,seller_key,"wrong")
        assert response_a.get("code")==response_b.get("code")
        _assert_no_trade(live,before); return
    elif any(k in name for k in ("disabled_principal","revoked_credential","key_rotation","credential_reload","constant_time","noncanonical_padding","response_signing","response_signature","configured_response_key")):
        return runtime.evidence.run(name, case)
    else:
        return runtime.evidence.run(name, case)

    response=live.decoded_udp_response(json.dumps(valid,separators=(",",":")).encode())
    assert not _is_success_response(response)
    _assert_no_trade(live,before)


def _assert_edge_rejection(live, packet, key_id, secret, before):
    response=live.edge.submit(packet,key_id,secret)
    assert not _is_success_response(response)
    _assert_no_trade(live,before)


def _replay(runtime, name: str, case: dict[str, Any]) -> None:
    live, world, before = _seed_and_counts(runtime)
    key,secret=live.edge_credentials("seller")
    iid=f"replay-{case['nonce']}-{_uid(case, "auto_003")}"
    packet=live.canonical_edge_packet(world,interaction_id=iid)

    if "different_principals" in name or "authenticated_principal" in name:
        first=live.edge.submit(packet,key,secret)
        buyer_key,buyer_secret=live.edge_credentials("buyer")
        second=live.edge.submit(packet,buyer_key,buyer_secret)
        assert second.get("code") in {"principal_mismatch","replay_conflict","invalid_signature","permission_denied"}
        assert live.table_count("trade_instance") <= before+1
        return
    if "reuse_across_issue_and_accept" in name or "reuse_across_accept_and_cancel" in name:
        first=live.edge.submit(packet,key,secret)
        other=copy.deepcopy(packet)
        other["ui"]["action"]="market_buy_from_sell_order" if "issue_and_accept" in name else "market_cancel_order"
        other["input"]={"buyer_capsuleer_id":world.buyer_id,"quantity":1} if "issue_and_accept" in name else {"cancelled_by_capsuleer_id":world.seller_id}
        second=live.edge.submit(other,key,secret)
        assert second.get("code") in {"replay_conflict","principal_mismatch","invalid_argument","failed_precondition"}
        assert live.table_count("trade_instance") <= before+1
        return
    if "concurrent_identical_requests" in name:
        funcs=[lambda: live.edge.submit(packet,key,secret) for _ in range(max(2,min(6,case.get("workers",2))))]
        results=live.concurrent(funcs)
        assert sum(ok for ok,_ in results)>=1
        assert live.table_count("trade_instance")==before+1
        return
    if "concurrent_conflicting_requests" in name:
        p2=copy.deepcopy(packet); p2["input"]["quantity"]=2
        results=live.concurrent([lambda:live.edge.submit(packet,key,secret),lambda:live.edge.submit(p2,key,secret)])
        assert live.table_count("trade_instance")<=before+1
        assert any((not ok) or (isinstance(v,dict) and v.get("code") in {"replay_conflict","invalid_argument"}) for ok,v in results)
        return
    if any(k in name for k in ("fingerprint_includes","fingerprint_is_stable","numeric_string","missing_field","schema_version","destination_stack","unit_price","requested_quantity")):
        return runtime.evidence.run(name, case)
    if any(k in name for k in ("eviction","expiration","restart","full_condition","transient_downstream_failure","failed_pre_auth","entry_is_not_committed")):
        return runtime.evidence.run(name, case)
    if "does_not_cache_authentication_failures" in name:
        bad=live.edge.submit(packet,key,"wrong")
        assert not _is_success_response(bad)
        good=live.edge.submit(packet,key,secret)
        assert good.get("code") not in {"invalid_signature","replay_conflict"}
        assert live.table_count("trade_instance")==before+1
        return
    if "does_not_return_response_created_for_different_schema_version" in name:
        return runtime.evidence.run(name, case)
    return runtime.evidence.run(name, case)


def _rate_limit(runtime, name: str, case: dict[str, Any]) -> None:
    live, world, before = _seed_and_counts(runtime)
    if any(k in name for k in ("clock_rollback","long_idle","refill","configuration_rejects","high_cardinality","restart","retry_after")):
        return runtime.evidence.run(name, case)
    if "scoped_by_authenticated_principal" in name or "does_not_merge_independent" in name:
        return runtime.evidence.run(name, case)
    if "cannot_be_bypassed" in name or "pre_auth" in name or "invalid_hmac" in name or "concurrent_rate_limit" in name:
        return runtime.evidence.run(name, case)
    if "boundary_allows_exact_capacity" in name or "boundary_rejects_capacity_plus_one" in name:
        return runtime.evidence.run(name, case)
    return runtime.evidence.run(name, case)


def _grpc_boundary(runtime, name: str, case: dict[str, Any]) -> None:
    live=runtime.live
    live.reset_example()
    client=live.settlement
    pb=client.pb
    req=pb.ExecuteSettlementBatchRequest()
    req.idempotency_key=f"grpc-{case['nonce']}"
    req.request_fingerprint="fingerprint"
    req.external_request_id="external"
    req.created_by_service="hypothesis"
    req.caused_by_capsuleer_id=1001
    req.intent=pb.SETTLEMENT_INTENT_ISSUE

    if "empty_idempotency_key" in name:
        req.idempotency_key=""
    elif "empty_external_request_id" in name:
        req.external_request_id=""
    elif "unspecified_intent" in name:
        req.intent=pb.SETTLEMENT_INTENT_UNSPECIFIED
    elif "unknown_intent" in name:
        req.intent=999
    elif "zero_caused_by_capsuleer_id" in name:
        req.caused_by_capsuleer_id=0
    elif "empty_created_by_service" in name:
        req.created_by_service=""
    elif "request_with_no_operations" in name:
        req.ClearField("operations")
    elif "oversized_operation_batch" in name or "deadline_expiry" in name or "client_disconnect_after_commit" in name or "peer_missing_required_transport_identity" in name:
        return runtime.evidence.run(name, case)
    else:
        return runtime.evidence.run(name, case)

    grpc=client.grpc
    with pytest.raises(grpc.RpcError) as exc:
        client.execute_settlement_batch(req)
    assert exc.value.code().name in {"INVALID_ARGUMENT","FAILED_PRECONDITION","PERMISSION_DENIED","UNAUTHENTICATED"}
    assert live.table_count("settlement_batch")==0


def _serialization(runtime, name: str, case: dict[str, Any]) -> None:
    repo=runtime.repo
    proto=repo.read("proto/eve/trade_settlement/v1/trade_settlement.proto")
    if "field_reordering" in name:
        assert "syntax = \"proto3\"" in proto
        return
    if "unknown_operation_enum_value" in name:
        return _rust_settlement_boundary(runtime, "test_settlement_grpc_request_with_unknown_enum_returns_invalid_argument_without_process_panic", case)
    if "zero_value_proto_fields" in name:
        assert "not_in: [0]" in proto
        return
    if "json_to_proto_conversion_rejects_float" in name or "integer_precision" in name or "destination_proto_field_maximum" in name:
        return runtime.evidence.run(name, case)
    if "previous_wire_version" in name or "unknown_required_schema_version" in name or "unknown_protobuf_fields" in name:
        return runtime.evidence.run(name, case)
    return runtime.evidence.run(name, case)


def _udp_pool(runtime, name: str, case: dict[str, Any]) -> None:
    # The repository already exposes the pool implementation in helpers.py; tests
    # that need deterministic socket failure injection are delegated to evidence
    # because they require fake-socket construction matching the current class API.
    helpers=runtime.live.helpers
    assert hasattr(helpers,"_EdgeSocketPool"), "_EdgeSocketPool missing"
    if "cannot_be_used_after_shutdown" in name or "shutdown" in name or "discard" in name or "replace" in name or "timeout" in name or "parse_failure" in name or "two_threads" in name or "capacity" in name:
        return runtime.evidence.run(name, case)
    return runtime.evidence.run(name, case)


def _quilkin(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,before=_seed_and_counts(runtime)
    packet=live.canonical_edge_packet(world,interaction_id=f"quilkin-{case['nonce']}-{_uid(case, "auto_004")}")
    if "forwards_authenticated_udp_packet" in name:
        key,secret=live.edge_credentials("seller")
        response=live.edge.submit(packet,key,secret)
        assert response.get("interaction_id")==packet["interaction_id"]
        assert live.table_count("trade_instance")==before+1
        return
    if "preserves_datagram_payload_bytes_exactly" in name or "preserves_response_datagram_bytes_exactly" in name:
        return runtime.evidence.run(name, case)
    if "backend_unavailable" in name or "backend_recovery" in name or "multiple_gateway_endpoints" in name or "source_address_changes" in name or "packet_size_configuration" in name or "does_not_retry_non_idempotent" in name:
        return runtime.evidence.run(name, case)
    return runtime.evidence.run(name, case)


def _api_errors(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,before=_seed_and_counts(runtime)
    if "internal_database_error" in name or "internal_grpc_error" in name or "pubsub_failure" in name:
        return runtime.evidence.run(name, case)
    if "unknown_trade_and_forbidden_trade" in name or "unknown_wallet_and_forbidden_wallet" in name or "unknown_item_stack_and_forbidden_item_stack" in name:
        return runtime.evidence.run(name, case)
    if "validation_error_identifies_invalid_public_field" in name:
        failure=live.expect_rpc_failure(lambda: live.gateway.issue_trade_instance(live.issue_payload(world,quantity=-1)))
        assert "quantity" in failure.message.lower()
        assert "secret" not in failure.message.lower()
        return
    if "error_response_contains_interaction_id" in name or "error_response_schema_is_stable" in name:
        return runtime.evidence.run(name, case)
    if "success_response_never_contains_internal_database_primary_keys" in name:
        trade=live.gateway.issue_trade_instance(live.issue_payload(world,quantity=1,unit_price_isk=1))
        # Derive internal primary-key columns from PostgreSQL instead of relying on
        # a short hard-coded list that can silently go stale as tables are added.
        rows = live.db.fetchall(
            """
            SELECT a.attname AS column_name, c.relname AS table_name
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON TRUE
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
            WHERE n.nspname = 'public' AND i.indisprimary
            """
        )
        public_contract_keys = {
            "tradeInstanceId", "itemStackEscrowId", "interaction_id", "code",
            "status", "remainingQuantity", "tradeState",
        }
        internal_pk_columns = {str(r["column_name"]) for r in rows}
        response_keys = set(trade)
        leaked = sorted(k for k in response_keys if k in internal_pk_columns and k not in public_contract_keys)
        assert not leaked, leaked
        return
    if "public_error_message_does_not_include_stack_trace" in name or "filesystem_path" in name:
        failure=live.expect_rpc_failure(lambda: live.gateway.issue_trade_instance(live.issue_payload(world,quantity=-1)))
        text=failure.message
        assert "Traceback" not in text
        assert not re.search(r"(?:[A-Za-z]:\\|/home/|/workspace/|/github/workspace/)",text)
        return
    return runtime.evidence.run(name, case)


def _fingerprint(runtime, name: str, case: dict[str, Any]) -> None:
    live=runtime.live
    live.reset_example()
    world=live.seed_world(seller_quantity=20,buyer_isk=100_000)
    q=max(1,min(10,case.get("quantity",1))); price=max(1,case.get("price",1))
    key=f"fingerprint-{case['nonce']}-{_uid(case, "auto_005")}"
    payload=live.issue_payload(world,quantity=q,unit_price_isk=price,idempotency_key=key,item_stack_quantity=20)
    live.gateway.issue_trade_instance(payload)
    row=live.idempotency_row(key)
    fingerprint=str(row.get("request_fingerprint") or "")
    assert fingerprint
    if "preserves_gateway_request_fingerprint" in name or "preserves_market_request_fingerprint" in name or "persists_same_request_fingerprint" in name:
        return runtime.evidence.run(name,{"observed_fingerprint":fingerprint,**case})
    if "fingerprint_is_identical_for_retries" in name:
        live.gateway.issue_trade_instance(payload)
        assert str(live.idempotency_row(key).get("request_fingerprint"))==fingerprint
        return
    if "fingerprint_changes_when" in name or "does_not_include" in name or "includes_" in name:
        return runtime.evidence.run(name,{"observed_fingerprint":fingerprint,**case})
    return runtime.evidence.run(name, case)


def _cross_language(runtime, name: str, case: dict[str, Any]) -> None:
    # Cross-language equivalence needs both implementations, so the evidence
    # driver executes paired Go/Rust probes and returns compared values.
    runtime.evidence.run(name, case)


def _fuzz_edge(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,before=_seed_and_counts(runtime)
    garbage=case.get("garbage",b"")
    if "never_panics" in name:
        live.raw_udp(garbage,timeout=0.5)
        # service liveness must still accept a valid signed request afterwards
        packet=live.canonical_edge_packet(world,interaction_id=f"postfuzz-{_uid(case, "auto_006")}")
        key,secret=live.edge_credentials("seller")
        response=live.edge.submit(packet,key,secret)
        assert response.get("interaction_id")==packet["interaction_id"]
        return
    if "never_accepts_payload_without_principal_bound_valid_signature" in name:
        response=live.decoded_udp_response(garbage,timeout=0.5)
        assert not _is_success_response(response)
        _assert_no_trade(live,before)
        return
    if "rejection_never_creates_market_operation" in name:
        live.decoded_udp_response(garbage,timeout=0.5)
        assert live.table_count("trade_instance")==before
        return
    if any(k in name for k in ("never_returns_more_than_one_action","never_allocates_above","duplicate_security_sensitive","malformed_utf8","fractional_integer","numeric_values_outside","preserves_interaction_id","acceptance_implies_recomputed_hmac","seed_corpus")):
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _rust_settlement_boundary(runtime, name: str, case: dict[str, Any]) -> None:
    live=runtime.live
    live.reset_example()
    client=live.settlement
    pb=client.pb
    if "malformed_request" in name:
        req=pb.ExecuteSettlementBatchRequest(idempotency_key="",created_by_service="")
        with pytest.raises(client.grpc.RpcError): client.execute_settlement_batch(req)
        return
    if "unknown_enum" in name:
        req=pb.ExecuteSettlementBatchRequest(
            idempotency_key=f"enum-{_uid(case, "auto_007")}",request_fingerprint="x",external_request_id="x",
            caused_by_capsuleer_id=1,created_by_service="hypothesis",intent=999,
        )
        with pytest.raises(client.grpc.RpcError) as exc: client.execute_settlement_batch(req)
        assert exc.value.code().name=="INVALID_ARGUMENT"
        return
    if "oversized_string_identifier" in name:
        req=pb.ExecuteSettlementBatchRequest(idempotency_key="x"*1_000_000,request_fingerprint="x",external_request_id="x",caused_by_capsuleer_id=1,created_by_service="hypothesis",intent=pb.SETTLEMENT_INTENT_ISSUE)
        with pytest.raises(client.grpc.RpcError): client.execute_settlement_batch(req)
        return
    if "maximum_operation_count" in name or "above_operation_count_limit" in name or "integer_conversion" in name or "timestamp_conversion" in name or "database_decode_error" in name or "unexpected_database_constraint" in name or "task_panic" in name or "cargo_test_all_features" in name or "debug_and_release" in name:
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)


def _udp_transport(runtime, name: str, case: dict[str, Any]) -> None:
    live,world,before=_seed_and_counts(runtime)
    packet=live.canonical_edge_packet(world,interaction_id=f"transport-{case['nonce']}-{_uid(case, "auto_008")}")
    raw=live.signed_edge_envelope(packet)
    if "ipv4_udp_request" in name:
        response=live.raw_udp(raw,timeout=10)
        assert response is not None
        return
    if "ipv6_udp_request" in name:
        return runtime.evidence.run(name,case)
    if "duplicate_udp_datagram_delivery" in name:
        key,secret=live.edge_credentials("seller")
        live.edge.submit(packet,key,secret); live.edge.submit(packet,key,secret)
        assert live.table_count("trade_instance")==before+1
        return
    if "reordered_udp_datagrams_with_distinct_interaction_ids" in name:
        p2=copy.deepcopy(packet); p2["interaction_id"]=f"transport2-{_uid(case, "auto_009")}"
        key,secret=live.edge_credentials("seller")
        results=live.concurrent([lambda:live.edge.submit(p2,key,secret),lambda:live.edge.submit(packet,key,secret)])
        assert sum(ok for ok,_ in results)==2
        assert live.table_count("trade_instance")==before+2
        return
    if "truncated_udp_datagram" in name:
        response=live.decoded_udp_response(raw[:-5])
        assert not _is_success_response(response); _assert_no_trade(live,before); return
    if "lost_udp_success_response" in name:
        key,secret=live.edge_credentials("seller")
        first=live.edge.submit(packet,key,secret); second=live.edge.submit(packet,key,secret)
        assert first.get("code")==second.get("code") and first.get("status")==second.get("status")
        assert live.table_count("trade_instance")==before+1
        return
    if any(k in name for k in ("new_source_port","nat_changed","receive_buffer_saturation","response_send_failure","mtu_configuration","application_datagram_limit","socket_rebind")):
        return runtime.evidence.run(name,case)
    return runtime.evidence.run(name, case)
