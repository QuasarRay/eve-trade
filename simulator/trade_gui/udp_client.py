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
    secret = settings.GAME_PACKET_HMAC_SECRET.encode("utf-8")
    signature = base64.urlsafe_b64encode(hmac.new(secret, payload, hashlib.sha256).digest()).rstrip(b"=").decode("ascii")
    envelope = {
        "schema_version": "eve-trade-edge.v1",
        "payload": packet,
        "auth": {
            "algorithm": "hmac-sha256",
            "key_id": settings.GAME_PACKET_HMAC_KEY_ID,
            "signature": signature,
        },
    }
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")


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
                response, _ = sock.recvfrom(65535)
            except OSError as exc:
                last_error = exc
                if attempt >= max_attempts or not is_transient_socket_error(exc):
                    raise
                time.sleep(retry_backoff * attempt)
                continue

            decoded = decode_udp_response(response)
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
        return decoded
    return {"raw_response": decoded}


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
