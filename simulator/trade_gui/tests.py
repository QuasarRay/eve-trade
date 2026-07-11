from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from jsonschema import Draft202012Validator

from .models import GameGuiButton, GameGuiInteraction
from .udp_client import EDGE_REQUEST_SCHEMA, EDGE_RESPONSE_SCHEMA, envelope_signing_bytes, response_signing_bytes
from .views import udp_error_http_status


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
        request = json.loads(self.sent[-1][0].decode("utf-8"))["payload"]
        return signed_response(
            {"interaction_id": request["interaction_id"], "status": "accepted"}
        ), ("127.0.0.1", 26001)


class TimeoutThenSuccessSocket(CapturingSocket):
    recv_calls = 0

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        type(self).recv_calls += 1
        if type(self).recv_calls == 1:
            raise socket.timeout("simulated packet loss")
        return super().recvfrom(size)


class RetryableResponseThenSuccessSocket(CapturingSocket):
    recv_calls = 0

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        type(self).recv_calls += 1
        if type(self).recv_calls == 1:
            request = json.loads(self.sent[-1][0].decode("utf-8"))["payload"]
            return signed_response(
                {
                    "interaction_id": request["interaction_id"],
                    "code": "downstream_unavailable",
                    "message": "retry later",
                }
            ), ("127.0.0.1", 26001)
        return super().recvfrom(size)


class InvalidSignatureResponseSocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        response, address = super().recvfrom(size)
        envelope = json.loads(response.decode("utf-8"))
        envelope["auth"]["signature"] = "invalid"
        return json.dumps(envelope).encode("utf-8"), address


class UnexpectedSourceResponseSocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        response, _ = super().recvfrom(size)
        return response, ("127.0.0.2", 26001)


class WrongInteractionSuccessSocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        return signed_response({"interaction_id": "old-request", "status": "accepted"}), ("127.0.0.1", 26001)


class WrongInteractionBusinessErrorSocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        return signed_response({"interaction_id": "old-request", "code": "invalid_argument", "message": "bad quantity"}), ("127.0.0.1", 26001)


class WrongInteractionTransientErrorSocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        return signed_response({"interaction_id": "old-request", "code": "downstream_unavailable", "message": "retry"}), ("127.0.0.1", 26001)


class MissingInteractionErrorSocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        return signed_response({"code": "invalid_argument", "message": "bad quantity"}), ("127.0.0.1", 26001)


class WrongResponseKeySocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        response, address = super().recvfrom(size)
        envelope = json.loads(response)
        envelope["auth"]["key_id"] = "wrong-key"
        return json.dumps(envelope).encode("utf-8"), address


class TamperedResponseBodySocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        response, address = super().recvfrom(size)
        envelope = json.loads(response)
        envelope["payload"]["status"] = "rejected"
        return json.dumps(envelope).encode("utf-8"), address


class TamperedResponseVersionSocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        response, address = super().recvfrom(size)
        envelope = json.loads(response)
        envelope["schema_version"] = "eve-trade-edge-response.v1"
        return json.dumps(envelope).encode("utf-8"), address


class MalformedJSONResponseSocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        return b"{not-json", ("127.0.0.1", 26001)


class AlwaysTimeoutSocket(CapturingSocket):
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        raise socket.timeout("simulated persistent packet loss")


def signed_response(payload: dict[str, Any]) -> bytes:
    canonical = response_signing_bytes(EDGE_RESPONSE_SCHEMA, "primary", payload)
    signature = base64.urlsafe_b64encode(
        hmac.new(b"edge-secret", canonical, hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    return json.dumps(
        {
            "schema_version": EDGE_RESPONSE_SCHEMA,
            "payload": payload,
            "auth": {
                "algorithm": "hmac-sha256",
                "key_id": "primary",
                "signature": signature,
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@override_settings(
    GAME_PACKET_HMAC_SECRET="edge-secret",
    GAME_PACKET_HMAC_KEY_ID="primary",
    GAME_PACKET_PRINCIPAL_KEYS_JSON='{"1001":{"key_id":"seller","secret":"edge-secret"}}',
    QUILKIN_UDP_HOST="127.0.0.1",
    QUILKIN_UDP_PORT=26001,
)
class GameGuiPacketBoundaryTests(TestCase):
    def setUp(self) -> None:
        CapturingSocket.sent = []
        TimeoutThenSuccessSocket.sent = []
        TimeoutThenSuccessSocket.recv_calls = 0
        RetryableResponseThenSuccessSocket.sent = []
        RetryableResponseThenSuccessSocket.recv_calls = 0
        self.button = GameGuiButton.objects.create(
            window=GameGuiButton.Window.REGIONAL_MARKET,
            label="Sell This Item",
            action="market_place_sell_order",
            default_payload={},
            enabled=True,
        )

    def test_button_press_conforms_to_versioned_protocol_schema_and_golden_packet(self) -> None:
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
        self.assertEqual(response.json()["response_payload"]["status"], "accepted")
        self.assertEqual(response.json()["response_payload"]["interaction_id"], "packet-id-1")
        self.assertEqual(len(CapturingSocket.sent), 1)
        payload, address = CapturingSocket.sent[0]
        self.assertEqual(address, ("127.0.0.1", 26001))

        envelope = json.loads(payload.decode("utf-8"))
        self.assertEqual(envelope["schema_version"], EDGE_REQUEST_SCHEMA)
        self.assertEqual(envelope["auth"]["algorithm"], "hmac-sha256")
        self.assertEqual(envelope["auth"]["key_id"], "seller")

        game_packet = envelope["payload"]
        protocol_root = Path(__file__).resolve().parents[2] / "distributed-backend" / "protocol"
        schema = json.loads((protocol_root / "eve-trade-gui-v1.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(game_packet)
        golden = json.loads((protocol_root / "fixtures" / "sell-order.packet.json").read_text(encoding="utf-8"))
        self.assertEqual(game_packet, golden)
        self.assertEqual(game_packet["schema_version"], "eve-trade-gui.v1")
        self.assertEqual(game_packet["interaction_id"], "packet-id-1")
        self.assertEqual(game_packet["ui"]["window"], GameGuiButton.Window.REGIONAL_MARKET)
        self.assertEqual(game_packet["ui"]["control_id"], "market_place_sell_order")
        self.assertEqual(game_packet["ui"]["action"], "market_place_sell_order")
        self.assertEqual(game_packet["input"]["issued_by_capsuleer_id"], 1001)
        self.assertNotIn("idempotency_key", game_packet["input"])
        self.assertEqual(game_packet["input"]["external_request_id"], "local-request-1")

        signed_payload = envelope_signing_bytes(
            EDGE_REQUEST_SCHEMA,
            "hmac-sha256",
            "seller",
            game_packet,
        )
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

    @override_settings(
        QUILKIN_UDP_MAX_ATTEMPTS=2,
        QUILKIN_UDP_RETRY_BACKOFF_SECONDS=0,
    )
    def test_button_press_retries_transient_udp_timeout_with_same_packet(self) -> None:
        client = Client()
        request_body = {
            "interaction_id": "retry-safe-interaction",
            "player_input": {"issued_by_capsuleer_id": 1001, "quantity": 4},
        }

        with patch("trade_gui.udp_client.socket.socket", TimeoutThenSuccessSocket):
            response = client.post(
                f"/api/gui/buttons/{self.button.id}/press/",
                data=json.dumps(request_body),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["response_payload"]["status"], "accepted")
        self.assertEqual(len(TimeoutThenSuccessSocket.sent), 2)
        first_packet, _ = TimeoutThenSuccessSocket.sent[0]
        second_packet, _ = TimeoutThenSuccessSocket.sent[1]
        self.assertEqual(first_packet, second_packet)

    @override_settings(
        QUILKIN_UDP_MAX_ATTEMPTS=2,
        QUILKIN_UDP_RETRY_BACKOFF_SECONDS=0,
    )
    def test_button_press_retries_retryable_gateway_response_with_same_packet(self) -> None:
        client = Client()
        request_body = {
            "interaction_id": "retry-safe-interaction",
            "player_input": {"issued_by_capsuleer_id": 1001, "quantity": 4},
        }

        with patch("trade_gui.udp_client.socket.socket", RetryableResponseThenSuccessSocket):
            response = client.post(
                f"/api/gui/buttons/{self.button.id}/press/",
                data=json.dumps(request_body),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["response_payload"]["status"], "accepted")
        self.assertEqual(len(RetryableResponseThenSuccessSocket.sent), 2)
        first_packet, _ = RetryableResponseThenSuccessSocket.sent[0]
        second_packet, _ = RetryableResponseThenSuccessSocket.sent[1]
        self.assertEqual(first_packet, second_packet)

    @override_settings(
        QUILKIN_UDP_MAX_ATTEMPTS=2,
        QUILKIN_UDP_RETRY_BACKOFF_SECONDS=0,
    )
    def test_button_press_fails_after_retry_budget_is_exhausted(self) -> None:
        with patch("trade_gui.udp_client.socket.socket", AlwaysTimeoutSocket):
            response = Client().post(
                f"/api/gui/buttons/{self.button.id}/press/",
                data=json.dumps({"interaction_id": "timeout", "player_input": {"issued_by_capsuleer_id": 1001, "quantity": 4}}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(AlwaysTimeoutSocket.sent), 2)
        self.assertEqual(response.json()["status"], "failed")

    def test_button_press_rejects_response_with_invalid_signature(self) -> None:
        with patch("trade_gui.udp_client.socket.socket", InvalidSignatureResponseSocket):
            response = Client().post(
                f"/api/gui/buttons/{self.button.id}/press/",
                data=json.dumps({"interaction_id": "forged", "player_input": {"issued_by_capsuleer_id": 1001, "quantity": 4}}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("signature", response.json()["error_message"])

    def test_button_press_rejects_response_from_unexpected_endpoint(self) -> None:
        with patch("trade_gui.udp_client.socket.socket", UnexpectedSourceResponseSocket):
            response = Client().post(
                f"/api/gui/buttons/{self.button.id}/press/",
                data=json.dumps({"interaction_id": "wrong-source", "player_input": {"issued_by_capsuleer_id": 1001, "quantity": 4}}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("unexpected endpoint", response.json()["error_message"])

    def test_every_structured_response_class_requires_the_request_interaction_id(self) -> None:
        cases = {
            "success": WrongInteractionSuccessSocket,
            "business error": WrongInteractionBusinessErrorSocket,
            "transient error": WrongInteractionTransientErrorSocket,
            "missing interaction": MissingInteractionErrorSocket,
        }
        for name, socket_type in cases.items():
            with self.subTest(name=name), patch("trade_gui.udp_client.socket.socket", socket_type):
                response = Client().post(
                    f"/api/gui/buttons/{self.button.id}/press/",
                    data=json.dumps({"interaction_id": f"request-{name}", "player_input": {"issued_by_capsuleer_id": 1001, "quantity": 4}}),
                    content_type="application/json",
                )
            self.assertEqual(response.status_code, 502)
            self.assertIn("interaction_id", response.json()["error_message"])

    def test_response_authentication_binds_key_version_and_body(self) -> None:
        cases = {
            "wrong key": WrongResponseKeySocket,
            "tampered body": TamperedResponseBodySocket,
            "tampered version": TamperedResponseVersionSocket,
        }
        for name, socket_type in cases.items():
            with self.subTest(name=name), patch("trade_gui.udp_client.socket.socket", socket_type):
                response = Client().post(
                    f"/api/gui/buttons/{self.button.id}/press/",
                    data=json.dumps({"interaction_id": f"auth-{name}", "player_input": {"issued_by_capsuleer_id": 1001, "quantity": 4}}),
                    content_type="application/json",
                )
            self.assertEqual(response.status_code, 502)

    def test_malformed_response_cannot_bypass_authentication_or_interaction_binding(self) -> None:
        with patch("trade_gui.udp_client.socket.socket", MalformedJSONResponseSocket):
            response = Client().post(
                f"/api/gui/buttons/{self.button.id}/press/",
                data=json.dumps({"interaction_id": "malformed-response", "player_input": {"issued_by_capsuleer_id": 1001, "quantity": 4}}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 502)
        self.assertIn("valid JSON", response.json()["error_message"])


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


class GameGuiErrorPropagationTests(TestCase):
    def setUp(self) -> None:
        self.button = GameGuiButton.objects.create(
            window=GameGuiButton.Window.REGIONAL_MARKET,
            label="Sell This Item",
            action="market_place_sell_order",
            default_payload={},
            enabled=True,
        )

    @patch(
        "trade_gui.views.send_gui_packet",
        return_value={"code": "invalid_argument", "message": "quantity must be positive"},
    )
    def test_rejected_udp_response_is_not_exposed_as_http_success(self, _send) -> None:
        response = Client().post(
            f"/api/gui/buttons/{self.button.id}/press/",
            data=json.dumps({"player_input": {"issued_by_capsuleer_id": 1001, "quantity": 0}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_argument")
        interaction = GameGuiInteraction.objects.get()
        self.assertEqual(interaction.status, GameGuiInteraction.Status.FAILED)
        self.assertEqual(interaction.error_message, "quantity must be positive")

    def test_udp_error_codes_map_to_non_success_http_statuses(self) -> None:
        expectations = {
            "invalid_argument": 400,
            "unauthenticated": 401,
            "permission_denied": 403,
            "not_found": 404,
            "replay": 409,
            "failed_precondition": 409,
            "resource_exhausted": 429,
            "downstream_unavailable": 503,
            "downstream_timeout": 504,
            "internal": 502,
        }
        for code, expected in expectations.items():
            with self.subTest(code=code):
                self.assertEqual(udp_error_http_status(code), expected)

    @patch(
        "trade_gui.views.send_gui_packet",
        return_value={"interaction_id": "unknown", "status": "queued"},
    )
    def test_http_202_accepts_explicit_queued_gateway_status(self, _send) -> None:
        response = Client().post(
            f"/api/gui/buttons/{self.button.id}/press/",
            data=json.dumps({"interaction_id": "unknown", "player_input": {"issued_by_capsuleer_id": 1001}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 202)
        interaction = GameGuiInteraction.objects.get()
        self.assertEqual(interaction.status, GameGuiInteraction.Status.SENT)

    @patch(
        "trade_gui.views.send_gui_packet",
        return_value={"interaction_id": "unknown", "status": "processing"},
    )
    def test_http_202_requires_an_explicit_acceptance_state(self, _send) -> None:
        response = Client().post(
            f"/api/gui/buttons/{self.button.id}/press/",
            data=json.dumps({"interaction_id": "unknown", "player_input": {"issued_by_capsuleer_id": 1001}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "invalid_gateway_response")
        interaction = GameGuiInteraction.objects.get()
        self.assertEqual(interaction.status, GameGuiInteraction.Status.FAILED)


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
