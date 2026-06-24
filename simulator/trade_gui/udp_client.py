from __future__ import annotations

import json
import socket
from typing import Any

from django.conf import settings


def send_gui_packet(packet: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(packet, separators=(",", ":"), sort_keys=True).encode("utf-8")
    address = (settings.QUILKIN_UDP_HOST, settings.QUILKIN_UDP_PORT)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(settings.QUILKIN_UDP_TIMEOUT_SECONDS)
        sock.sendto(payload, address)
        response, _ = sock.recvfrom(65535)

    try:
        return json.loads(response.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw_response": response.decode("utf-8", errors="replace")}
