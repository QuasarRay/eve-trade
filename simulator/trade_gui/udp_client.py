from __future__ import annotations

import json
import hmac
import hashlib
import base64
import socket
from typing import Any

from django.conf import settings


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

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(settings.QUILKIN_UDP_TIMEOUT_SECONDS)
        sock.sendto(payload, address)
        response, _ = sock.recvfrom(65535)

    try:
        return json.loads(response.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw_response": response.decode("utf-8", errors="replace")}
