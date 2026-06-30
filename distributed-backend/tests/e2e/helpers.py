from __future__ import annotations

import hashlib
import hmac
import base64
import importlib
import json
import re
import socket
import sys
import tempfile
import time
import uuid
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
import psycopg
from psycopg.rows import dict_row


SELLER_ID = 1001
BUYER_ID = 2002
OTHER_ID = 3003
OUTSIDER_ID = 4004

REGION_ID = 10000002
STATION_ID = 60003760
OTHER_STATION_ID = 60008494
ITEM_TYPE_ID = 34
OTHER_ITEM_TYPE_ID = 35
CHECKSUM_ALGORITHM = "sha256-v1"

_SETTLEMENT_PROTO_CACHE: tuple[Any, Any] | None = None
_SETTLEMENT_PROTO_TMPDIR: tempfile.TemporaryDirectory[str] | None = None


class RpcFailure(Exception):
    def __init__(self, status_code: int, code: str, message: str, body: Any):
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.body = body


@dataclass(frozen=True)
class World:
    seller_id: int
    buyer_id: int
    other_id: int
    outsider_id: int
    item_type_id: int
    other_item_type_id: int
    station_id: int
    other_station_id: int
    seller_wallet_id: str
    buyer_wallet_id: str
    other_wallet_id: str
    outsider_wallet_id: str
    seller_stack_id: str
    seller_other_stack_id: str
    other_stack_id: str
    buyer_stack_id: str | None


@dataclass(frozen=True)
class Trade:
    trade_instance_id: str
    item_stack_escrow_id: str
    quantity: int
    unit_price_isk: int
    seller_stack_id: str
    idempotency_key: str


class GatewayClient:
    def __init__(self, simulator_url: str):
        self.simulator_url = simulator_url.rstrip("/")
        self.http = httpx.Client(timeout=httpx.Timeout(20.0))
        self._buttons_by_action: dict[str, int] | None = None

    def close(self) -> None:
        self.http.close()


    def issue_trade_instance(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._press("market_place_sell_order", payload)

    def accept_trade_instance(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._press("market_buy_from_sell_order", payload)

    def cancel_trade_instance(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._press("market_cancel_order", payload)

    def _press(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        button_id = self._button_id(action)
        response = self.http.post(
            f"{self.simulator_url}/api/gui/buttons/{button_id}/press/",
            json={"player_input": camel_to_snake(payload)},
        )
        if response.status_code >= 400:
            body: Any
            try:
                body = response.json()
            except ValueError:
                body = response.text
            if isinstance(body, dict):
                code = str(body.get("code") or response.status_code)
                message = str(body.get("message") or body)
            else:
                code = str(response.status_code)
                message = str(body)
            raise RpcFailure(response.status_code, code, message, body)
        body = response.json()
        gateway_payload = body.get("response_payload", body)
        if isinstance(gateway_payload, dict) and "code" in gateway_payload:
            raise RpcFailure(
                response.status_code,
                str(gateway_payload.get("code") or "error"),
                str(gateway_payload.get("message") or gateway_payload),
                gateway_payload,
            )
        if not isinstance(gateway_payload, dict):
            raise RpcFailure(response.status_code, "invalid_response", str(gateway_payload), body)
        return snake_to_camel(gateway_payload)

    def _button_id(self, action: str) -> int:
        if self._buttons_by_action is None:
            response = self.http.get(f"{self.simulator_url}/api/gui/buttons/")
            response.raise_for_status()
            rows = response.json()
            if isinstance(rows, dict) and "results" in rows:
                rows = rows["results"]
            self._buttons_by_action = {
                str(row["action"]): int(row["id"])
                for row in rows
                if row.get("enabled", True)
            }
        try:
            return self._buttons_by_action[action]
        except KeyError as exc:
            raise RuntimeError(f"simulator button for action {action!r} was not seeded") from exc


class AuthenticatedEdgeClient:
    def __init__(self, host: str, port: int, response_secret: str, response_key_id: str):
        self.endpoint = (host, port)
        self.response_secret = response_secret.encode("utf-8")
        self.response_key_id = response_key_id

    def submit(self, packet: dict[str, Any], key_id: str, principal_secret: str) -> dict[str, Any]:
        raw_payload = json.dumps(packet, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = base64.urlsafe_b64encode(
            hmac.new(principal_secret.encode("utf-8"), raw_payload, hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        envelope = json.dumps(
            {
                "schema_version": "eve-trade-edge.v1",
                "payload": packet,
                "auth": {"algorithm": "hmac-sha256", "key_id": key_id, "signature": signature},
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(10)
            udp.sendto(envelope, self.endpoint)
            response, source = udp.recvfrom(65535)
        expected_ips = {
            row[4][0]
            for row in socket.getaddrinfo(self.endpoint[0], self.endpoint[1], socket.AF_INET, socket.SOCK_DGRAM)
        }
        if source[0] not in expected_ips or source[1] != self.endpoint[1]:
            raise AssertionError(f"edge response source {source!r} does not match {self.endpoint!r}")
        decoded = json.loads(response)
        if decoded.get("schema_version") != "eve-trade-edge-response.v1":
            raise AssertionError(f"edge response is unsigned: {decoded!r}")
        auth = decoded.get("auth") or {}
        payload = decoded.get("payload")
        if auth.get("algorithm") != "hmac-sha256" or auth.get("key_id") != self.response_key_id:
            raise AssertionError(f"edge response authentication metadata is invalid: {auth!r}")
        canonical = response_signing_bytes(
            decoded["schema_version"],
            str(auth.get("key_id") or ""),
            payload,
        )
        expected = base64.urlsafe_b64encode(
            hmac.new(self.response_secret, canonical, hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(str(auth.get("signature") or ""), expected):
            raise AssertionError("edge response signature is invalid")
        expected_interaction_id = packet.get("interaction_id")
        actual_interaction_id = payload.get("interaction_id") if isinstance(payload, dict) else None
        if actual_interaction_id != expected_interaction_id:
            raise AssertionError(
                "edge response interaction_id does not match request: "
                f"got {actual_interaction_id!r}, expected {expected_interaction_id!r}, payload={payload!r}"
            )
        return payload


class SettlementClient:
    def __init__(self, endpoint: str):
        grpc, pb, pb_grpc = settlement_proto_modules()
        self.grpc = grpc
        self.pb = pb
        self.channel = grpc.insecure_channel(endpoint)
        self.stub = pb_grpc.TradeSettlementServiceStub(self.channel)

    def close(self) -> None:
        self.channel.close()

    def execute_settlement_batch(self, request: Any) -> Any:
        return self.stub.ExecuteSettlementBatch(request, timeout=20)


class GrpcFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def settlement_proto_modules() -> tuple[Any, Any, Any]:
    global _SETTLEMENT_PROTO_CACHE, _SETTLEMENT_PROTO_TMPDIR
    if _SETTLEMENT_PROTO_CACHE is not None:
        return _SETTLEMENT_PROTO_CACHE

    import grpc
    import grpc_tools
    from grpc_tools import protoc

    repo_root = Path(__file__).resolve().parents[3]
    proto_root = repo_root / "distributed-backend" / "proto"
    proto_file = proto_root / "eve" / "trade_settlement" / "v1" / "trade_settlement.proto"
    _SETTLEMENT_PROTO_TMPDIR = tempfile.TemporaryDirectory(prefix="eve-trade-proto-")
    out_dir = Path(_SETTLEMENT_PROTO_TMPDIR.name)
    include_google = Path(grpc_tools.__file__).resolve().parent / "_proto"

    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{proto_root}",
            f"-I{include_google}",
            f"--python_out={out_dir}",
            f"--grpc_python_out={out_dir}",
            str(proto_file),
        ]
    )
    if result != 0:
        raise RuntimeError(f"protoc failed with exit code {result}")

    sys.path.insert(0, str(out_dir))
    importlib.invalidate_caches()
    pb = importlib.import_module("eve.trade_settlement.v1.trade_settlement_pb2")
    pb_grpc = importlib.import_module("eve.trade_settlement.v1.trade_settlement_pb2_grpc")
    _SETTLEMENT_PROTO_CACHE = (grpc, pb, pb_grpc)
    return _SETTLEMENT_PROTO_CACHE


def expect_grpc_error(call: Callable[[], Any], *, code: str, contains: str | None = None) -> GrpcFailure:
    grpc, _, _ = settlement_proto_modules()
    try:
        call()
    except grpc.RpcError as exc:
        failure = GrpcFailure(exc.code().name, exc.details() or "")
        if failure.code != code:
            raise AssertionError(f"gRPC code {failure.code!r}, expected {code!r}") from exc
        if contains is not None and contains.lower() not in failure.message.lower():
            raise AssertionError(
                f"gRPC message {failure.message!r} did not contain {contains!r}"
            ) from exc
        return failure
    raise AssertionError("expected gRPC request to fail")


class Database:
    def __init__(self, database_url: str):
        self.conn = psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
        )

    def close(self) -> None:
        self.conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.fetchone(sql, params)
        if row is None:
            return None
        return next(iter(row.values()))

    def reset(self) -> None:
        self.execute(
            """
            TRUNCATE
                wallet_ledger,
                item_stack_ledger,
                trade_state_change,
                wallet_escrow,
                item_stack_escrow,
                trade_instance,
                settlement_step,
                settlement_batch,
                request_attempt,
                idempotency_record,
                wallet,
                item_stack,
                station,
                region,
                item_type,
                capsuleer
            RESTART IDENTITY CASCADE
            """
        )


def wait_for_database(database_url: str, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            connection = psycopg.connect(database_url, connect_timeout=2)
            connection.close()
            return
        except Exception as exc:  # noqa: BLE001 - preserve final connection error.
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"database did not become reachable: {last_error}")


def wait_for_gateway(api_gateway_url: str, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    url = api_gateway_url.rstrip("/") + "/healthz"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001 - preserve final connection error.
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"api gateway did not become reachable: {last_error}")


def wait_for_market(market_url: str, timeout_seconds: float = 60.0) -> None:
    wait_for_http_health(market_url.rstrip("/") + "/readyz", "Market", timeout_seconds)


def wait_for_http_health(url: str, service: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
            last_error = RuntimeError(f"HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001 - preserve final connection error.
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"{service} did not become ready: {last_error}")


def wait_for_settlement(endpoint: str, timeout_seconds: float = 60.0) -> None:
    host, separator, port_text = endpoint.rpartition(":")
    if not separator or not host:
        raise RuntimeError(f"invalid settlement endpoint {endpoint!r}")
    wait_for_tcp(host, int(port_text), "settlement service", timeout_seconds)


def wait_for_rabbitmq(url: str, timeout_seconds: float = 60.0) -> None:
    parsed = urlparse(url)
    if not parsed.hostname:
        raise RuntimeError(f"invalid RabbitMQ URL {url!r}")
    wait_for_tcp(parsed.hostname, parsed.port or 5672, "RabbitMQ", timeout_seconds)


def wait_for_tcp(host: str, port: int, service: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"{service} did not become reachable: {last_error}")


def wait_for_simulator(simulator_url: str, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    url = simulator_url.rstrip("/") + "/api/gui/buttons/"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200 and response.json():
                return
        except Exception as exc:  # noqa: BLE001 - preserve final connection error.
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"simulator did not become reachable: {last_error}")


def camel_to_snake(value: Any) -> Any:
    if isinstance(value, dict):
        return {camel_name_to_snake(str(key)): camel_to_snake(child) for key, child in value.items()}
    if isinstance(value, list):
        return [camel_to_snake(child) for child in value]
    return value


def snake_to_camel(value: Any) -> Any:
    if isinstance(value, dict):
        return {snake_name_to_camel(str(key)): snake_to_camel(child) for key, child in value.items()}
    if isinstance(value, list):
        return [snake_to_camel(child) for child in value]
    return value


def camel_name_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()


def snake_name_to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def fresh_key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def uuid_str() -> str:
    return str(uuid.uuid4())


def wallet_checksum(wallet_id: str, isk_amount: int, wallet_version: int) -> str:
    return hashlib.sha256(
        f"wallet:{wallet_id}:{isk_amount}:{wallet_version}".encode("utf-8")
    ).hexdigest()


def item_stack_checksum(item_stack_id: str, quantity: int, stack_version: int) -> str:
    return hashlib.sha256(
        f"item_stack:{item_stack_id}:{quantity}:{stack_version}".encode("utf-8")
    ).hexdigest()


def seed_world(
    db: Database,
    *,
    seller_quantity: int = 10,
    seller_second_quantity: int = 8,
    seller_isk: int = 100,
    buyer_isk: int = 1_000,
    other_isk: int = 1_000,
    buyer_stack_quantity: int | None = None,
) -> World:
    seed_reference_data(db)

    seller_wallet_id = uuid_str()
    buyer_wallet_id = uuid_str()
    other_wallet_id = uuid_str()
    outsider_wallet_id = uuid_str()
    seller_stack_id = uuid_str()
    seller_other_stack_id = uuid_str()
    other_stack_id = uuid_str()
    buyer_stack_id = uuid_str() if buyer_stack_quantity is not None else None

    insert_wallet(db, seller_wallet_id, SELLER_ID, seller_isk)
    insert_wallet(db, buyer_wallet_id, BUYER_ID, buyer_isk)
    insert_wallet(db, other_wallet_id, OTHER_ID, other_isk)
    insert_wallet(db, outsider_wallet_id, OUTSIDER_ID, other_isk)
    insert_item_stack(db, seller_stack_id, SELLER_ID, ITEM_TYPE_ID, STATION_ID, seller_quantity)
    insert_item_stack(
        db,
        seller_other_stack_id,
        SELLER_ID,
        OTHER_ITEM_TYPE_ID,
        STATION_ID,
        seller_second_quantity,
    )
    insert_item_stack(db, other_stack_id, OTHER_ID, ITEM_TYPE_ID, STATION_ID, seller_quantity)
    if buyer_stack_id is not None:
        insert_item_stack(
            db,
            buyer_stack_id,
            BUYER_ID,
            ITEM_TYPE_ID,
            STATION_ID,
            buyer_stack_quantity,
        )

    return World(
        seller_id=SELLER_ID,
        buyer_id=BUYER_ID,
        other_id=OTHER_ID,
        outsider_id=OUTSIDER_ID,
        item_type_id=ITEM_TYPE_ID,
        other_item_type_id=OTHER_ITEM_TYPE_ID,
        station_id=STATION_ID,
        other_station_id=OTHER_STATION_ID,
        seller_wallet_id=seller_wallet_id,
        buyer_wallet_id=buyer_wallet_id,
        other_wallet_id=other_wallet_id,
        outsider_wallet_id=outsider_wallet_id,
        seller_stack_id=seller_stack_id,
        seller_other_stack_id=seller_other_stack_id,
        other_stack_id=other_stack_id,
        buyer_stack_id=buyer_stack_id,
    )


def seed_reference_data(db: Database) -> None:
    db.execute(
        """
        INSERT INTO capsuleer (capsuleer_id, capsuleer_name)
        VALUES
            (%s, 'Seller'),
            (%s, 'Buyer'),
            (%s, 'Other'),
            (%s, 'Outsider')
        ON CONFLICT DO NOTHING
        """,
        (SELLER_ID, BUYER_ID, OTHER_ID, OUTSIDER_ID),
    )
    db.execute(
        """
        INSERT INTO region (region_id, region_name)
        VALUES (%s, 'The Forge')
        ON CONFLICT DO NOTHING
        """,
        (REGION_ID,),
    )
    db.execute(
        """
        INSERT INTO station (station_id, region_id, station_name)
        VALUES
            (%s, %s, 'Jita IV - Moon 4'),
            (%s, %s, 'Perimeter II')
        ON CONFLICT DO NOTHING
        """,
        (STATION_ID, REGION_ID, OTHER_STATION_ID, REGION_ID),
    )
    db.execute(
        """
        INSERT INTO item_type (item_type_id, item_type_name, category_name, group_name)
        VALUES
            (%s, 'Tritanium', 'Material', 'Mineral'),
            (%s, 'Pyerite', 'Material', 'Mineral')
        ON CONFLICT DO NOTHING
        """,
        (ITEM_TYPE_ID, OTHER_ITEM_TYPE_ID),
    )


def insert_wallet(
    db: Database,
    wallet_id: str,
    capsuleer_id: int,
    isk_amount: int,
    *,
    wallet_state: str = "ACTIVE",
    wallet_kind: str = "PRIMARY",
    wallet_version: int = 1,
) -> None:
    db.execute(
        """
        INSERT INTO wallet (
            wallet_id,
            capsuleer_id,
            wallet_kind,
            isk_amount,
            wallet_state,
            wallet_version,
            wallet_checksum,
            checksum_algorithm
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            wallet_id,
            capsuleer_id,
            wallet_kind,
            isk_amount,
            wallet_state,
            wallet_version,
            wallet_checksum(wallet_id, isk_amount, wallet_version),
            CHECKSUM_ALGORITHM,
        ),
    )


def insert_item_stack(
    db: Database,
    item_stack_id: str,
    owner_id: int,
    item_type_id: int,
    station_id: int,
    quantity: int,
    *,
    stack_state: str = "ACTIVE",
    stack_version: int = 1,
) -> None:
    batch_id = uuid_str()
    request_id = uuid_str()
    settlement_step_id = uuid_str()
    idempotency_key = fresh_key("seed-item-stack")
    stack_checksum = item_stack_checksum(item_stack_id, quantity, stack_version)

    with db.conn.transaction():
        db.execute(
            """
            INSERT INTO idempotency_record (
                idempotency_key,
                request_fingerprint,
                request_kind,
                idempotency_state,
                created_by_service,
                completed_at
            )
            VALUES (%s, encode(digest(%s, 'sha256'), 'hex'), %s, 'COMPLETED', %s, now())
            """,
            (idempotency_key, idempotency_key, "TEST_SEED_ITEM_STACK", "e2e-tests"),
        )
        db.execute(
            """
            INSERT INTO request_attempt (
                request_id,
                idempotency_key,
                attempt_number,
                received_by_service,
                attempt_state,
                completed_at
            )
            VALUES (%s, %s, 1, %s, 'COMPLETED', now())
            """,
            (request_id, idempotency_key, "e2e-tests"),
        )
        db.execute(
            """
            INSERT INTO settlement_batch (
                settlement_batch_id,
                request_id,
                idempotency_key,
                external_request_id,
                caused_by_capsuleer_id,
                batch_state,
                created_by_service,
                completed_at
            )
            VALUES (%s, %s, %s, %s, %s, 'COMPLETED', %s, now())
            """,
            (
                batch_id,
                request_id,
                idempotency_key,
                f"external-{idempotency_key}",
                owner_id,
                "e2e-tests",
            ),
        )
        db.execute(
            """
            INSERT INTO settlement_step (
                settlement_step_id,
                settlement_batch_id,
                step_index,
                step_kind,
                step_payload,
                step_payload_hash,
                step_state,
                started_at,
                completed_at
            )
            VALUES (
                %s,
                %s,
                0,
                %s,
                %s::jsonb,
                encode(digest(%s, 'sha256'), 'hex'),
                'COMPLETED',
                now(),
                now()
            )
            """,
            (
                settlement_step_id,
                batch_id,
                "e2e-tests.seed_item_stack",
                "{}",
                f"seed-item-stack:{item_stack_id}",
            ),
        )
        db.execute(
            """
            INSERT INTO item_stack (
                item_stack_id,
                owner_id,
                item_type_id,
                station_id,
                quantity,
                stack_state,
                stack_version,
                stack_checksum,
                checksum_algorithm
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item_stack_id,
                owner_id,
                item_type_id,
                station_id,
                quantity,
                stack_state,
                stack_version,
                stack_checksum,
                CHECKSUM_ALGORITHM,
            ),
        )
        db.execute(
            """
            WITH seed_ledger AS (
                SELECT
                    %s::uuid AS settlement_step_id,
                    %s::uuid AS item_stack_id,
                    %s::bigint AS ledger_sequence,
                    %s::bigint AS item_type_id,
                    %s::bigint AS owner_id,
                    %s::bigint AS station_id,
                    'CREATE_STACK'::text AS entry_kind,
                    %s::bigint AS quantity_delta,
                    0::bigint AS quantity_before,
                    %s::bigint AS quantity_after,
                    'ABSENT'::text AS stack_state_before,
                    %s::text AS stack_state_after,
                    0::bigint AS stack_version_before,
                    %s::bigint AS stack_version_after,
                    'GENESIS'::text AS stack_checksum_before,
                    %s::text AS stack_checksum_after
            ),
            hashed AS (
                SELECT
                    seed_ledger.*,
                    compute_item_stack_ledger_payload_hash(
                        settlement_step_id,
                        item_stack_id,
                        ledger_sequence,
                        item_type_id,
                        owner_id,
                        station_id,
                        entry_kind,
                        quantity_delta,
                        quantity_before,
                        quantity_after,
                        stack_state_before,
                        stack_state_after,
                        stack_version_before,
                        stack_version_after,
                        stack_checksum_before,
                        stack_checksum_after
                    ) AS ledger_payload_hash
                FROM seed_ledger
            )
            INSERT INTO item_stack_ledger (
                settlement_step_id,
                item_stack_id,
                ledger_sequence,
                previous_item_stack_ledger_hash,
                ledger_payload_hash,
                item_stack_ledger_hash,
                item_type_id,
                owner_id,
                station_id,
                entry_kind,
                quantity_delta,
                quantity_before,
                quantity_after,
                stack_state_before,
                stack_state_after,
                stack_version_before,
                stack_version_after,
                stack_checksum_before,
                stack_checksum_after
            )
            SELECT
                settlement_step_id,
                item_stack_id,
                ledger_sequence,
                'GENESIS',
                ledger_payload_hash,
                compute_item_stack_ledger_hash('GENESIS', ledger_payload_hash),
                item_type_id,
                owner_id,
                station_id,
                entry_kind,
                quantity_delta,
                quantity_before,
                quantity_after,
                stack_state_before,
                stack_state_after,
                stack_version_before,
                stack_version_after,
                stack_checksum_before,
                stack_checksum_after
            FROM hashed
            """,
            (
                settlement_step_id,
                item_stack_id,
                stack_version,
                item_type_id,
                owner_id,
                station_id,
                quantity,
                quantity,
                stack_state,
                stack_version,
                stack_checksum,
            ),
        )
        db.execute(
            """
            UPDATE idempotency_record
            SET result_settlement_batch_id = %s
            WHERE idempotency_key = %s
            """,
            (batch_id, idempotency_key),
        )


def issue_payload(
    world: World,
    *,
    quantity: int = 4,
    unit_price_isk: int = 25,
    idempotency_key: str | None = None,
    issued_by_capsuleer_id: int | None = None,
    item_stack_id: str | None = None,
    item_stack_owner_id: int | None = None,
    item_type_id: int | None = None,
    station_id: int | None = None,
    item_stack_quantity: int = 10,
) -> dict[str, Any]:
    key = fresh_key("issue") if idempotency_key is None else idempotency_key
    return {
        "idempotencyKey": key,
        "externalRequestId": f"external-{key}",
        "issuedByCapsuleerId": world.seller_id
        if issued_by_capsuleer_id is None
        else issued_by_capsuleer_id,
        "itemStack": {
            "itemStackId": world.seller_stack_id if item_stack_id is None else item_stack_id,
            "ownerId": item_stack_owner_id
            if item_stack_owner_id is not None
            else world.seller_id,
            "itemTypeId": item_type_id if item_type_id is not None else world.item_type_id,
            "stationId": station_id if station_id is not None else world.station_id,
            "quantity": item_stack_quantity,
        },
        "quantity": quantity,
        "unitPriceIsk": unit_price_isk,
    }


def create_trade(
    gateway: GatewayClient,
    world: World,
    *,
    quantity: int = 4,
    unit_price_isk: int = 25,
    idempotency_key: str | None = None,
    **payload_overrides: Any,
) -> Trade:
    key = fresh_key("issue") if idempotency_key is None else idempotency_key
    payload = issue_payload(
        world,
        quantity=quantity,
        unit_price_isk=unit_price_isk,
        idempotency_key=key,
        item_stack_quantity=payload_overrides.pop("item_stack_quantity", 10),
        **payload_overrides,
    )
    response = gateway.issue_trade_instance(payload)
    return Trade(
        trade_instance_id=response["tradeInstanceId"],
        item_stack_escrow_id=response["itemStackEscrowId"],
        quantity=quantity,
        unit_price_isk=unit_price_isk,
        seller_stack_id=payload["itemStack"]["itemStackId"],
        idempotency_key=key,
    )


def accept_payload(
    world: World,
    trade: Trade,
    *,
    quantity: int | None = None,
    idempotency_key: str | None = None,
    buyer_capsuleer_id: int | None = None,
    buyer_wallet_id: str | None = None,
    buyer_destination_item_stack_id: str | None = None,
    **client_facts: Any,
) -> dict[str, Any]:
    requested = trade.quantity if quantity is None else quantity
    key = fresh_key("accept") if idempotency_key is None else idempotency_key
    payload = {
        "idempotencyKey": key,
        "externalRequestId": f"external-{key}",
        "tradeInstanceId": trade.trade_instance_id,
        "buyerCapsuleerId": world.buyer_id
        if buyer_capsuleer_id is None
        else buyer_capsuleer_id,
        "quantityRequested": requested,
        "buyerWalletId": world.buyer_wallet_id if buyer_wallet_id is None else buyer_wallet_id,
        "buyerDestinationItemStackId": ""
        if buyer_destination_item_stack_id is None
        else buyer_destination_item_stack_id,
    }
    payload.update({snake_name_to_camel(name): value for name, value in client_facts.items()})
    return payload


def accept_trade(
    gateway: GatewayClient,
    world: World,
    trade: Trade,
    **payload_overrides: Any,
) -> dict[str, Any]:
    return gateway.accept_trade_instance(accept_payload(world, trade, **payload_overrides))


def cancel_payload(
    world: World,
    trade: Trade,
    *,
    idempotency_key: str | None = None,
    cancelled_by_capsuleer_id: int | None = None,
    **client_facts: Any,
) -> dict[str, Any]:
    key = fresh_key("cancel") if idempotency_key is None else idempotency_key
    payload = {
        "idempotencyKey": key,
        "externalRequestId": f"external-{key}",
        "tradeInstanceId": trade.trade_instance_id,
        "cancelledByCapsuleerId": world.seller_id
        if cancelled_by_capsuleer_id is None
        else cancelled_by_capsuleer_id,
    }
    payload.update({snake_name_to_camel(name): value for name, value in client_facts.items()})
    return payload


def cancel_trade(
    gateway: GatewayClient,
    world: World,
    trade: Trade,
    **payload_overrides: Any,
) -> dict[str, Any]:
    return gateway.cancel_trade_instance(cancel_payload(world, trade, **payload_overrides))


def expect_rpc_error(
    call: Callable[[], Any],
    *,
    code: str,
    contains: str,
) -> RpcFailure:
    try:
        call()
    except RpcFailure as exc:
        if exc.code != code:
            raise AssertionError(f"RPC code {exc.code!r}, expected {code!r}") from exc
        if contains.lower() not in exc.message.lower():
            raise AssertionError(
                f"RPC message {exc.message!r} did not contain {contains!r}"
            ) from exc
        return exc
    raise AssertionError("expected RPC request to fail")


def response_signing_bytes(schema_version: str, key_id: str, payload: Any) -> bytes:
    return json.dumps(
        {
            "algorithm": "hmac-sha256",
            "key_id": key_id,
            "payload": payload,
            "schema_version": schema_version,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def item_stack_row(db: Database, item_stack_id: str) -> dict[str, Any]:
    row = db.fetchone("SELECT * FROM item_stack WHERE item_stack_id = %s", (item_stack_id,))
    assert row is not None, f"missing item_stack {item_stack_id}"
    return row


def wallet_row(db: Database, wallet_id: str) -> dict[str, Any]:
    row = db.fetchone("SELECT * FROM wallet WHERE wallet_id = %s", (wallet_id,))
    assert row is not None, f"missing wallet {wallet_id}"
    return row


def trade_row(db: Database, trade: Trade) -> dict[str, Any]:
    row = db.fetchone(
        "SELECT * FROM trade_instance WHERE trade_instance_id = %s",
        (trade.trade_instance_id,),
    )
    assert row is not None, f"missing trade_instance {trade.trade_instance_id}"
    return row


def item_escrow_row(db: Database, trade: Trade) -> dict[str, Any]:
    row = db.fetchone(
        "SELECT * FROM item_stack_escrow WHERE item_stack_escrow_id = %s",
        (trade.item_stack_escrow_id,),
    )
    assert row is not None, f"missing item_stack_escrow {trade.item_stack_escrow_id}"
    return row


def wallet_escrow_row(db: Database, wallet_escrow_id: str) -> dict[str, Any]:
    row = db.fetchone(
        "SELECT * FROM wallet_escrow WHERE wallet_escrow_id = %s",
        (wallet_escrow_id,),
    )
    assert row is not None, f"missing wallet_escrow {wallet_escrow_id}"
    return row


def table_count(db: Database, table_name: str) -> int:
    return int(db.scalar(f"SELECT count(*) FROM {table_name}"))


def open_trade_count(db: Database) -> int:
    return int(
        db.scalar("SELECT count(*) FROM trade_instance WHERE trade_state = 'OPEN'")
    )


def total_item_quantity(
    db: Database,
    *,
    item_type_id: int = ITEM_TYPE_ID,
    station_id: int = STATION_ID,
) -> int:
    return int(
        db.scalar(
            """
            SELECT
                COALESCE((
                    SELECT SUM(quantity)
                    FROM item_stack
                    WHERE item_type_id = %s AND station_id = %s
                ), 0)
                +
                COALESCE((
                    SELECT SUM(quantity)
                    FROM item_stack_escrow
                    WHERE item_type_id = %s AND station_id = %s
                ), 0)
            """,
            (item_type_id, station_id, item_type_id, station_id),
        )
    )


def total_isk_amount(db: Database) -> int:
    return int(
        db.scalar(
            """
            SELECT
                COALESCE((SELECT SUM(isk_amount) FROM wallet), 0)
                +
                COALESCE((SELECT SUM(isk_amount) FROM wallet_escrow), 0)
            """
        )
    )


def minimum_numeric_value(db: Database, table_name: str, column_name: str) -> int:
    value = db.scalar(f"SELECT COALESCE(MIN({column_name}), 0) FROM {table_name}")
    return int(value)


def settlement_batch_count(db: Database, idempotency_key: str) -> int:
    return int(
        db.scalar(
            "SELECT count(*) FROM settlement_batch WHERE idempotency_key = %s",
            (idempotency_key,),
        )
    )


def idempotency_record_row(db: Database, idempotency_key: str) -> dict[str, Any]:
    row = db.fetchone(
        "SELECT * FROM idempotency_record WHERE idempotency_key = %s",
        (idempotency_key,),
    )
    assert row is not None, f"missing idempotency_record {idempotency_key}"
    return row


def settlement_batch_row(db: Database, idempotency_key: str) -> dict[str, Any]:
    row = db.fetchone(
        "SELECT * FROM settlement_batch WHERE idempotency_key = %s",
        (idempotency_key,),
    )
    assert row is not None, f"missing settlement_batch for {idempotency_key}"
    return row


def settlement_step_rows(db: Database, settlement_batch_id: str) -> list[dict[str, Any]]:
    return db.fetchall(
        """
        SELECT *
        FROM settlement_step
        WHERE settlement_batch_id = %s
        ORDER BY step_index
        """,
        (settlement_batch_id,),
    )
