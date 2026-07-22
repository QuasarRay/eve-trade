from __future__ import annotations

import json
import hmac
import hashlib
import base64
import errno
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from django.conf import settings


RETRYABLE_RESPONSE_CODES = {
    "request_in_progress",
    "downstream_timeout",
    "downstream_unavailable",
    "gateway_capacity",
    "queue_full",
    "rate_limited",
    "resource_exhausted",
}

MAX_UDP_PAYLOAD_BYTES = 65_507
MAX_MALFORMED_DATAGRAMS = 8
MAX_UDP_SESSION_POOL_SIZE = 256

EDGE_REQUEST_SCHEMA = "eve-trade-edge.v2"
EDGE_RESPONSE_SCHEMA = "eve-trade-edge-response.v2"
HMAC_SHA256_ALGORITHM = "hmac-sha256"
ENVELOPE_SIGNING_DOMAIN = "eve-trade.udp-envelope.hmac-sha256.v1"


@dataclass
class _UdpSession:
    sock: Any | None = None
    generation: int = 0
    on_replacement: Callable[[], None] | None = None

    def get_socket(self) -> Any:
        if self.sock is None:
            family = configured_socket_family()
            self.sock = socket.socket(family, socket.SOCK_DGRAM)
            if family == socket.AF_INET6 and settings.QUILKIN_UDP_ADDRESS_FAMILY == "dual":
                set_option = getattr(self.sock, "setsockopt", None)
                if set_option is not None:
                    set_option(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        self.sock.settimeout(settings.QUILKIN_UDP_TIMEOUT_SECONDS)
        return self.sock

    def reset(self, *, replacement: bool = True) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None
            if replacement and self.on_replacement is not None:
                self.on_replacement()


class UdpPoolSaturatedError(TimeoutError):
    retryable = True
    code = "udp_pool_saturated"


class UdpResponseDeadlineError(TimeoutError):
    pass


class _AvailableSessions:
    def __init__(self, pool: "_UdpSessionPool"):
        self._pool = pool

    def qsize(self) -> int:
        with self._pool._condition:
            return len(self._pool._available)


class _UdpSessionPool:
    def __init__(self, size: int):
        if size < 1 or size > MAX_UDP_SESSION_POOL_SIZE:
            raise ValueError(
                f"QUILKIN_UDP_SESSION_POOL_SIZE must be between 1 and {MAX_UDP_SESSION_POOL_SIZE}"
            )
        self.size = size
        self._condition = threading.Condition()
        self._generation = 1
        self._closed = False
        self._checked_out = 0
        self._waiters = 0
        self._acquisition_wait_seconds = 0.0
        self._acquisition_timeouts = 0
        self._session_replacements = 0
        self.sessions = [self._new_session() for _ in range(size)]
        self._available = list(self.sessions)
        self.available = _AvailableSessions(self)

    def _new_session(self) -> _UdpSession:
        return _UdpSession(
            generation=self._generation,
            on_replacement=self._record_replacement,
        )

    def _record_replacement(self) -> None:
        with self._condition:
            self._session_replacements += 1

    @contextmanager
    def checkout(
        self,
        *,
        timeout: float | None = None,
        cancelled: threading.Event | None = None,
    ) -> Iterator[_UdpSession]:
        session, generation = self._acquire(timeout, cancelled)
        try:
            yield session
        finally:
            self._release(session, generation)

    def _acquire(
        self,
        timeout: float | None,
        cancelled: threading.Event | None,
    ) -> tuple[_UdpSession, int]:
        if timeout is None:
            timeout = float(settings.QUILKIN_UDP_TIMEOUT_SECONDS)
        timeout = max(0.0, timeout)
        with self._condition:
            self._waiters += 1
            try:
                started: float | None = None
                deadline: float | None = None
                while True:
                    if self._closed:
                        raise RuntimeError("UDP session pool is closed")
                    if cancelled is not None and cancelled.is_set():
                        raise RuntimeError("UDP session checkout cancelled")
                    if self._available:
                        session = self._available.pop()
                        self._checked_out += 1
                        if started is not None:
                            self._acquisition_wait_seconds += max(0.0, time.monotonic() - started)
                        return session, self._generation
                    if started is None:
                        started = time.monotonic()
                        deadline = started + timeout
                    assert deadline is not None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._acquisition_timeouts += 1
                        self._acquisition_wait_seconds += max(0.0, time.monotonic() - started)
                        raise UdpPoolSaturatedError("UDP session pool checkout timed out")
                    self._condition.wait(remaining)
            finally:
                self._waiters -= 1

    def _release(self, session: _UdpSession, generation: int) -> None:
        retire = False
        with self._condition:
            self._checked_out -= 1
            if self._closed or generation != self._generation or session.generation != self._generation:
                retire = True
            else:
                self._available.append(session)
            self._condition.notify_all()
        if retire:
            session.reset(replacement=False)

    def reset(self) -> None:
        self._retire()

    def close(self, *, deadline: float | None = None) -> None:
        self._retire()
        if deadline is None:
            return
        with self._condition:
            while self._checked_out > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"UDP session pool close timed out with {self._checked_out} checked-out leases"
                    )
                self._condition.wait(remaining)

    def _retire(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            idle = self._available
            self._available = []
            self._condition.notify_all()
        for session in idle:
            session.reset(replacement=False)

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "checked_out": self._checked_out,
                "waiting": self._waiters,
                "capacity": self.size,
                "acquisition_wait_seconds": self._acquisition_wait_seconds,
                "acquisition_timeouts": self._acquisition_timeouts,
                "session_replacements": self._session_replacements,
                "saturated": self._checked_out >= self.size and not self._available,
                "closed": self._closed,
                "generation": self._generation,
            }


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
    pool_size = int(settings.QUILKIN_UDP_SESSION_POOL_SIZE)
    if pool_size < 1 or pool_size > MAX_UDP_SESSION_POOL_SIZE:
        raise ValueError(
            f"QUILKIN_UDP_SESSION_POOL_SIZE must be between 1 and {MAX_UDP_SESSION_POOL_SIZE}"
        )
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
    hosts = tuple(getattr(settings, "QUILKIN_UDP_HOSTS", ())) or (settings.QUILKIN_UDP_HOST,)
    addresses = [(host, settings.QUILKIN_UDP_PORT) for host in hosts]
    max_attempts = max(1, settings.QUILKIN_UDP_MAX_ATTEMPTS)
    retry_backoff = max(0.0, settings.QUILKIN_UDP_RETRY_BACKOFF_SECONDS)

    pool = _get_udp_session_pool()
    with pool.checkout(timeout=float(settings.QUILKIN_UDP_TIMEOUT_SECONDS)) as session:
        for attempt in range(1, max_attempts + 1):
            address = addresses[(attempt - 1) % len(addresses)]
            sock = session.get_socket()
            try:
                sock.sendto(payload, address)
                decoded = _receive_matching_response(
                    sock,
                    address,
                    str(packet.get("interaction_id") or ""),
                )
            except OSError as exc:
                session.reset()
                if attempt >= max_attempts or not is_transient_socket_error(exc):
                    raise
                time.sleep(retry_backoff * attempt)
                continue
            except ValueError:
                session.reset()
                raise
            if decoded.get("code") not in RETRYABLE_RESPONSE_CODES or attempt >= max_attempts:
                return decoded
            if decoded.get("code") != "request_in_progress":
                session.reset()
            retry_delay = retry_backoff * attempt
            if decoded.get("code") == "request_in_progress":
                retry_delay = max(retry_delay, settings.QUILKIN_UDP_TIMEOUT_SECONDS)
            time.sleep(retry_delay)

    raise TimeoutError("UDP request did not produce a terminal response")


def _receive_matching_response(
    sock: Any,
    address: tuple[str, int],
    interaction_id: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + float(settings.QUILKIN_UDP_TIMEOUT_SECONDS)
    malformed = 0
    mismatched = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if mismatched:
                raise ValueError("UDP response interaction_id does not match request")
            raise UdpResponseDeadlineError("UDP response deadline expired")
        sock.settimeout(remaining)
        try:
            response, response_address = sock.recvfrom(MAX_UDP_PAYLOAD_BYTES + 1)
        except socket.timeout:
            if malformed and time.monotonic() >= deadline:
                raise UdpResponseDeadlineError("UDP response deadline expired")
            if mismatched:
                raise ValueError("UDP response interaction_id does not match request")
            raise
        if not response_from_expected_endpoint(response_address, address):
            raise ValueError(f"UDP response came from unexpected endpoint {response_address!r}")
        if len(response) > MAX_UDP_PAYLOAD_BYTES:
            raise ValueError(
                f"UDP response is too large: {len(response)} bytes exceeds {MAX_UDP_PAYLOAD_BYTES}"
            )
        try:
            decoded = decode_udp_response(response)
        except ValueError as exc:
            if "signature" in str(exc) or "authentication metadata" in str(exc):
                raise
            malformed += 1
            if malformed >= MAX_MALFORMED_DATAGRAMS:
                raise ValueError(
                    f"UDP response is not valid JSON; malformed datagram limit {MAX_MALFORMED_DATAGRAMS} exceeded"
                )
            continue
        if decoded.get("interaction_id") != interaction_id:
            mismatched += 1
            continue
        return decoded


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
        for row in socket.getaddrinfo(
            expected[0],
            expected[1],
            configured_socket_family(),
            socket.SOCK_DGRAM,
        )
    }
    return actual[0] in expected_addresses


def configured_socket_family() -> int:
    configured = str(getattr(settings, "QUILKIN_UDP_ADDRESS_FAMILY", "ipv4")).strip().lower()
    if configured == "ipv4":
        return socket.AF_INET
    if configured in {"ipv6", "dual"}:
        return socket.AF_INET6
    raise ValueError("QUILKIN_UDP_ADDRESS_FAMILY must be one of ipv4, ipv6, or dual")


def is_transient_socket_error(exc: OSError) -> bool:
    if isinstance(exc, UdpResponseDeadlineError):
        return False
    if isinstance(exc, (TimeoutError, ConnectionResetError, ConnectionRefusedError)):
        return True
    return exc.errno is None or exc.errno in {
        errno.EAGAIN,
        errno.EWOULDBLOCK,
        errno.ECONNRESET,
        errno.ECONNREFUSED,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
    }
