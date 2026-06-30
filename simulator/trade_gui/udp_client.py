from __future__ import annotations

import json
import hmac
import hashlib
import base64
import errno
import socket
import time
from typing import Any

from django.conf import settings


RETRYABLE_RESPONSE_CODES = {
    "request_in_progress",
    "downstream_timeout",
    "downstream_unavailable",
}


def encode_udp_packet(packet: dict[str, Any]) -> bytes:
    payload = json.dumps(packet, separators=(",", ":"), sort_keys=True).encode("utf-8")
    principal_id = packet_principal_id(packet)
    try:
        principal_keys = json.loads(settings.GAME_PACKET_PRINCIPAL_KEYS_JSON)
        credential = principal_keys[str(principal_id)]
        key_id = str(credential["key_id"])
        secret = str(credential["secret"]).encode("utf-8")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"no signing credential is configured for capsuleer {principal_id}") from exc
    if not key_id or not secret:
        raise ValueError(f"invalid signing credential for capsuleer {principal_id}")
    signature = base64.urlsafe_b64encode(hmac.new(secret, payload, hashlib.sha256).digest()).rstrip(b"=").decode("ascii")
    envelope = {
        "schema_version": "eve-trade-edge.v1",
        "payload": packet,
        "auth": {
            "algorithm": "hmac-sha256",
            "key_id": key_id,
            "signature": signature,
        },
    }
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")


def packet_principal_id(packet: dict[str, Any]) -> int:
    action = str(packet.get("ui", {}).get("action") or "").strip()
    actor_field = {
        "market_place_sell_order": "issued_by_capsuleer_id",
        "contract_create_item_exchange": "issued_by_capsuleer_id",
        "direct_trade_offer": "issued_by_capsuleer_id",
        "market_buy_from_sell_order": "buyer_capsuleer_id",
        "contract_accept_item_exchange": "buyer_capsuleer_id",
        "direct_trade_accept": "buyer_capsuleer_id",
        "market_cancel_order": "cancelled_by_capsuleer_id",
        "contract_cancel_item_exchange": "cancelled_by_capsuleer_id",
        "direct_trade_cancel": "cancelled_by_capsuleer_id",
    }.get(action)
    if actor_field is None:
        raise ValueError(f"unsupported trade GUI action {action!r}")
    try:
        principal_id = int(packet.get("input", {})[actor_field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{actor_field} is required to authenticate this request") from exc
    if principal_id <= 0:
        raise ValueError(f"{actor_field} must be positive")
    return principal_id


def send_gui_packet(packet: dict[str, Any]) -> dict[str, Any]:
    payload = encode_udp_packet(packet)
    address = (settings.QUILKIN_UDP_HOST, settings.QUILKIN_UDP_PORT)
    max_attempts = max(1, settings.QUILKIN_UDP_MAX_ATTEMPTS)
    retry_backoff = max(0.0, settings.QUILKIN_UDP_RETRY_BACKOFF_SECONDS)
    last_error: OSError | None = None

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(settings.QUILKIN_UDP_TIMEOUT_SECONDS)
        for attempt in range(1, max_attempts + 1):
            try:
                sock.sendto(payload, address)
                response, response_address = sock.recvfrom(65535)
            except OSError as exc:
                last_error = exc
                if attempt >= max_attempts or not is_transient_socket_error(exc):
                    raise
                time.sleep(retry_backoff * attempt)
                continue

            if not response_from_expected_endpoint(response_address, address):
                raise ValueError(f"UDP response came from unexpected endpoint {response_address!r}")
            decoded = decode_udp_response(response)
            if decoded.get("status") == "accepted" and decoded.get("interaction_id") != packet.get("interaction_id"):
                raise ValueError("UDP response interaction_id does not match request")
            if decoded.get("code") not in RETRYABLE_RESPONSE_CODES or attempt >= max_attempts:
                return decoded
            retry_delay = retry_backoff * attempt
            if decoded.get("code") == "request_in_progress":
                retry_delay = max(retry_delay, settings.QUILKIN_UDP_TIMEOUT_SECONDS)
            time.sleep(retry_delay)

    if last_error is not None:
        raise last_error
    raise TimeoutError("UDP request did not produce a terminal response")


def decode_udp_response(response: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(response.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw_response": response.decode("utf-8", errors="replace")}
    if isinstance(decoded, dict):
        if decoded.get("schema_version") == "eve-trade-edge-response.v1":
            payload = decoded.get("payload")
            auth = decoded.get("auth")
            if not isinstance(payload, dict) or not isinstance(auth, dict):
                raise ValueError("signed UDP response envelope is malformed")
            if auth.get("algorithm") != "hmac-sha256" or auth.get("key_id") != settings.GAME_PACKET_HMAC_KEY_ID:
                raise ValueError("signed UDP response uses unexpected authentication metadata")
            canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
            expected = base64.urlsafe_b64encode(
                hmac.new(settings.GAME_PACKET_HMAC_SECRET.encode("utf-8"), canonical, hashlib.sha256).digest()
            ).rstrip(b"=").decode("ascii")
            if not hmac.compare_digest(str(auth.get("signature") or ""), expected):
                raise ValueError("signed UDP response signature is invalid")
            return payload
        raise ValueError("UDP response is not an authenticated edge response envelope")
    return {"raw_response": decoded}


def response_from_expected_endpoint(actual: tuple[str, int], expected: tuple[str, int]) -> bool:
    if actual[1] != expected[1]:
        return False
    expected_addresses = {
        row[4][0]
        for row in socket.getaddrinfo(expected[0], expected[1], socket.AF_INET, socket.SOCK_DGRAM)
    }
    return actual[0] in expected_addresses


def is_transient_socket_error(exc: OSError) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionResetError, ConnectionRefusedError)):
        return True
    return exc.errno in {
        errno.EAGAIN,
        errno.EWOULDBLOCK,
        errno.ECONNRESET,
        errno.ECONNREFUSED,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
    }
