from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from .models import GameGuiButton


FORBIDDEN_PACKET_TERMS = {
    "django",
    "rest",
    "framework",
    "simulator",
    "test",
    "debug",
    "environment",
    "browser",
    "source",
    "source_transport",
    "source_address",
}


class CapturingSocket:
    sent: list[tuple[bytes, tuple[str, int]]] = []

    def __init__(self, *args: Any, **kwargs: Any):
        self.timeout = None

    def __enter__(self) -> "CapturingSocket":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendto(self, payload: bytes, address: tuple[str, int]) -> int:
        self.sent.append((payload, address))
        return len(payload)

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        return b'{"interaction_id":"accepted-by-edge","status":"accepted"}', ("127.0.0.1", 26001)


@override_settings(
    GAME_PACKET_HMAC_SECRET="edge-secret",
    GAME_PACKET_HMAC_KEY_ID="primary",
    QUILKIN_UDP_HOST="127.0.0.1",
    QUILKIN_UDP_PORT=26001,
)
class GameGuiPacketBoundaryTests(TestCase):
    def setUp(self) -> None:
        CapturingSocket.sent = []
        self.button = GameGuiButton.objects.create(
            window=GameGuiButton.Window.REGIONAL_MARKET,
            label="Sell This Item",
            action="market_place_sell_order",
            default_payload={},
            enabled=True,
        )

    def test_button_press_sends_production_identical_signed_game_packet(self) -> None:
        client = Client()
        player_input = {
            "idempotency_key": "packet-id-1",
            "external_request_id": "local-request-1",
            "issued_by_capsuleer_id": 1001,
            "item_stack": {
                "item_stack_id": "11111111-1111-4111-8111-111111111111",
                "owner_id": 1001,
                "item_type_id": 34,
                "station_id": 60003760,
                "quantity": 10,
            },
            "quantity": 4,
            "unit_price_isk": 25,
        }

        with patch("trade_gui.udp_client.socket.socket", CapturingSocket):
            response = client.post(
                f"/api/gui/buttons/{self.button.id}/press/",
                data=json.dumps({"player_input": player_input}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(CapturingSocket.sent), 1)
        payload, address = CapturingSocket.sent[0]
        self.assertEqual(address, ("127.0.0.1", 26001))

        envelope = json.loads(payload.decode("utf-8"))
        self.assertEqual(envelope["schema_version"], "eve-trade-edge.v1")
        self.assertEqual(envelope["auth"]["algorithm"], "hmac-sha256")
        self.assertEqual(envelope["auth"]["key_id"], "primary")

        game_packet = envelope["payload"]
        self.assertEqual(game_packet["schema_version"], "eve-trade-gui.v1")
        self.assertEqual(game_packet["interaction_id"], "packet-id-1")
        self.assertEqual(game_packet["ui"]["window"], GameGuiButton.Window.REGIONAL_MARKET)
        self.assertEqual(game_packet["ui"]["control_id"], "market_place_sell_order")
        self.assertEqual(game_packet["ui"]["action"], "market_place_sell_order")
        self.assertEqual(game_packet["input"]["issued_by_capsuleer_id"], 1001)
        self.assertNotIn("idempotency_key", game_packet["input"])
        self.assertNotIn("external_request_id", game_packet["input"])

        signed_payload = json.dumps(game_packet, separators=(",", ":"), sort_keys=True).encode("utf-8")
        expected_signature = base64.urlsafe_b64encode(
            hmac.new(b"edge-secret", signed_payload, hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        self.assertEqual(envelope["auth"]["signature"], expected_signature)

        leaked_terms = sorted(
            term
            for term in FORBIDDEN_PACKET_TERMS
            if packet_contains_term(game_packet, term)
        )
        self.assertEqual(leaked_terms, [])


class GameGuiIndexTests(TestCase):
    def setUp(self) -> None:
        GameGuiButton.objects.create(
            window=GameGuiButton.Window.REGIONAL_MARKET,
            label="Sell This Item",
            action="market_place_sell_order",
            default_payload={},
            enabled=True,
        )

    def test_index_exposes_stable_automation_contract_without_stale_stack_quantity(self) -> None:
        response = Client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="action-market_place_sell_order"')
        self.assertContains(response, 'data-testid="gateway-response"')
        self.assertContains(response, "quantity: 0")


def packet_contains_term(value: Any, term: str) -> bool:
    term = term.lower()
    if isinstance(value, dict):
        return any(
            term in str(key).lower() or packet_contains_term(child, term)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(packet_contains_term(child, term) for child in value)
    if isinstance(value, str):
        return term in value.lower()
    return False
