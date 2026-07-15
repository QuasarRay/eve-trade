from __future__ import annotations

import json
import hmac
import hashlib
import base64
import errno
import queue
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from django.conf import settings


RETRYABLE_RESPONSE_CODES = {
    "request_in_progress",
    "downstream_timeout",
    "downstream_unavailable",
}

EDGE_REQUEST_SCHEMA = "eve-trade-edge.v2"
EDGE_RESPONSE_SCHEMA = "eve-trade-edge-response.v2"
HMAC_SHA256_ALGORITHM = "hmac-sha256"
ENVELOPE_SIGNING_DOMAIN = "eve-trade.udp-envelope.hmac-sha256.v1"


@dataclass
class _UdpSession:
    sock: Any | None = None

    def get_socket(self) -> Any:
        if self.sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(settings.QUILKIN_UDP_TIMEOUT_SECONDS)
        return self.sock

    def reset(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None


class _UdpSessionPool:
    def __init__(self, size: int):
        self.sessions = [_UdpSession() for _ in range(size)]
        self.available: queue.LifoQueue[_UdpSession] = queue.LifoQueue(maxsize=size)
        for session in self.sessions:
            self.available.put_nowait(session)

    @contextmanager
    def checkout(self) -> Iterator[_UdpSession]:
        session = self.available.get()
        try:
            yield session
        finally:
            self.available.put_nowait(session)

    def close(self) -> None:
        for session in self.sessions:
            session.reset()


_session_pool_guard = threading.Lock()
_session_pool_state: tuple[tuple[str, int, float, int], _UdpSessionPool] | None = None


def reset_udp_session_pool() -> None:
    global _session_pool_state
    with _session_pool_guard:
        if _session_pool_state is not None:
            _session_pool_state[1].close()
            _session_pool_state = None


def _get_udp_session_pool() -> _UdpSessionPool:
    global _session_pool_state
    pool_size = max(1, settings.QUILKIN_UDP_SESSION_POOL_SIZE)
    config = (
        settings.QUILKIN_UDP_HOST,
        settings.QUILKIN_UDP_PORT,
        settings.QUILKIN_UDP_TIMEOUT_SECONDS,
        pool_size,
    )
    with _session_pool_guard:
        if _session_pool_state is None or _session_pool_state[0] != config:
            if _session_pool_state is not None:
                _session_pool_state[1].close()
            _session_pool_state = (config, _UdpSessionPool(pool_size))
        return _session_pool_state[1]


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
    signing_bytes = envelope_signing_bytes(
        EDGE_REQUEST_SCHEMA,
        HMAC_SHA256_ALGORITHM,
        key_id,
        packet,
    )
    signature = base64.urlsafe_b64encode(hmac.new(secret, signing_bytes, hashlib.sha256).digest()).rstrip(b"=").decode("ascii")
    envelope = {
        "schema_version": EDGE_REQUEST_SCHEMA,
        "payload": packet,
        "auth": {
            "algorithm": HMAC_SHA256_ALGORITHM,
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

    pool = _get_udp_session_pool()
    with pool.checkout() as session:
        for attempt in range(1, max_attempts + 1):
            sock = session.get_socket()
            try:
                sock.sendto(payload, address)
                response, response_address = sock.recvfrom(65535)
            except OSError as exc:
                session.reset()
                if attempt >= max_attempts or not is_transient_socket_error(exc):
                    raise
                time.sleep(retry_backoff * attempt)
                continue

            try:
                if not response_from_expected_endpoint(response_address, address):
                    raise ValueError(f"UDP response came from unexpected endpoint {response_address!r}")
                decoded = decode_udp_response(response)
                if decoded.get("interaction_id") != packet.get("interaction_id"):
                    raise ValueError("UDP response interaction_id does not match request")
            except ValueError:
                session.reset()
                raise
            if decoded.get("code") not in RETRYABLE_RESPONSE_CODES or attempt >= max_attempts:
                return decoded
            retry_delay = retry_backoff * attempt
            if decoded.get("code") == "request_in_progress":
                retry_delay = max(retry_delay, settings.QUILKIN_UDP_TIMEOUT_SECONDS)
            time.sleep(retry_delay)

    raise TimeoutError("UDP request did not produce a terminal response")


def decode_udp_response(response: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("UDP response is not valid JSON") from exc
    if isinstance(decoded, dict):
        if decoded.get("schema_version") == EDGE_RESPONSE_SCHEMA:
            payload = decoded.get("payload")
            auth = decoded.get("auth")
            if not isinstance(payload, dict) or not isinstance(auth, dict):
                raise ValueError("signed UDP response envelope is malformed")
            if auth.get("algorithm") != HMAC_SHA256_ALGORITHM or auth.get("key_id") != settings.GAME_PACKET_HMAC_KEY_ID:
                raise ValueError("signed UDP response uses unexpected authentication metadata")
            canonical = response_signing_bytes(
                decoded["schema_version"],
                str(auth.get("key_id") or ""),
                payload,
            )
            expected = base64.urlsafe_b64encode(
                hmac.new(settings.GAME_PACKET_HMAC_SECRET.encode("utf-8"), canonical, hashlib.sha256).digest()
            ).rstrip(b"=").decode("ascii")
            if not hmac.compare_digest(str(auth.get("signature") or ""), expected):
                raise ValueError("signed UDP response signature is invalid")
            return payload
        raise ValueError("UDP response is not an authenticated edge response envelope")
    raise ValueError("UDP response envelope must be a JSON object")


def response_signing_bytes(schema_version: str, key_id: str, payload: dict[str, Any]) -> bytes:
    return envelope_signing_bytes(schema_version, HMAC_SHA256_ALGORITHM, key_id, payload)


def envelope_signing_bytes(
    schema_version: str,
    algorithm: str,
    key_id: str,
    payload: dict[str, Any],
) -> bytes:
    return json.dumps(
        {
            "algorithm": algorithm,
            "domain": ENVELOPE_SIGNING_DOMAIN,
            "key_id": key_id,
            "payload": payload,
            "schema_version": schema_version,
        },
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")


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
