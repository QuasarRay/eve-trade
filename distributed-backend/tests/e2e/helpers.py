from __future__ import annotations

import hashlib
import hmac
import base64
import importlib
import json
import os
import queue
import re
import socket
import sys
import tempfile
import threading
import time
import uuid
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
EDGE_REQUEST_SCHEMA = "eve-trade-edge.v2"
EDGE_RESPONSE_SCHEMA = "eve-trade-edge-response.v2"
HMAC_SHA256_ALGORITHM = "hmac-sha256"
ENVELOPE_SIGNING_DOMAIN = "eve-trade.udp-envelope.hmac-sha256.v1"

_SETTLEMENT_PROTO_CACHE: tuple[Any, Any] | None = None
_SETTLEMENT_PROTO_TMPDIR: tempfile.TemporaryDirectory[str] | None = None
_E2E_PUBSUB_CHANNELS = (
    ("settlement-work", "trade-settlement-executor"),
    ("settlement-results", "market-settlement-result-projection"),
)


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
        self.settlement_db = None
        database_url = os.environ.get("EVE_TRADE_DATABASE_URL")
        if database_url:
            self.settlement_db = psycopg.connect(
                database_url,
                autocommit=True,
                row_factory=dict_row,
            )

    def close(self) -> None:
        self.http.close()
        if self.settlement_db is not None:
            self.settlement_db.close()


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
        result = snake_to_camel(gateway_payload)
        self._wait_for_queued_settlement(payload, result)
        return result

    def _wait_for_queued_settlement(self, payload: dict[str, Any], response: dict[str, Any]) -> None:
        if response.get("status") not in {"queued", "accepted"}:
            return
        idempotency_key = str(payload.get("idempotencyKey") or payload.get("idempotency_key") or "")
        if not idempotency_key or self.settlement_db is None:
            return
        row = self._wait_for_settlement_batch(idempotency_key)
        response.setdefault("settlementBatchId", row["settlement_batch_id"])
        state = str(row["batch_state"])
        if state == "COMPLETED":
            return
        code = settlement_failure_rpc_code(row.get("failure_code"))
        message = str(row.get("failure_message") or f"settlement batch {state}")
        raise RpcFailure(500, code, message, row)

    def _wait_for_settlement_batch(self, idempotency_key: str, timeout_seconds: float = 20.0) -> dict[str, Any]:
        assert self.settlement_db is not None
        deadline = time.monotonic() + timeout_seconds
        last_state = "missing"
        while time.monotonic() < deadline:
            with self.settlement_db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT batch.settlement_batch_id,
                           batch.batch_state,
                           batch.failure_code,
                           batch.failure_message,
                           operation.result_published
                    FROM settlement_batch AS batch
                    JOIN settlement_operation AS operation
                      ON operation.idempotency_key = batch.idempotency_key
                    WHERE batch.idempotency_key = %s
                    ORDER BY batch.started_at DESC
                    LIMIT 1
                    """,
                    (idempotency_key,),
                )
                row = cursor.fetchone()
            if row is not None:
                last_state = str(row["batch_state"])
                if last_state in {"COMPLETED", "FAILED"} and row["result_published"]:
                    return row
                if last_state in {"COMPLETED", "FAILED"}:
                    last_state += "_RESULT_PENDING"
            with self.settlement_db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT operation_id::text AS settlement_batch_id,
                           CASE operation_state
                               WHEN 'SUCCEEDED' THEN 'COMPLETED'
                               ELSE operation_state
                           END AS batch_state,
                           failure_code,
                           failure_description AS failure_message,
                           result_published
                    FROM settlement_operation
                    WHERE idempotency_key = %s
                    """,
                    (idempotency_key,),
                )
                operation = cursor.fetchone()
            if operation is not None:
                last_state = str(operation["batch_state"])
                if last_state in {"COMPLETED", "FAILED"} and operation["result_published"]:
                    return operation
                if last_state in {"COMPLETED", "FAILED"}:
                    last_state += "_RESULT_PENDING"
            time.sleep(0.1)
        raise RuntimeError(
            f"settlement batch for idempotency key {idempotency_key!r} did not complete; last state={last_state}"
        )
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


def settlement_failure_rpc_code(value: Any) -> str:
    code = str(value or "SETTLEMENT_FAILED").upper()
    return {
        "INSUFFICIENT_FUNDS": "failed_precondition",
        "INSUFFICIENT_QUANTITY": "failed_precondition",
        "FAILED_PRECONDITION": "failed_precondition",
        "FAILEDPRECONDITION": "failed_precondition",
        "PERMISSION_DENIED": "permission_denied",
        "PERMISSIONDENIED": "permission_denied",
        "INVALID_ARGUMENT": "invalid_argument",
        "NOT_FOUND": "not_found",
        "CONFLICT": "aborted",
    }.get(code, code.lower())


class _EdgeSocketPool:
    def __init__(self, capacity: int, factory: Callable[[], socket.socket]):
        self.capacity = capacity
        self._factory = factory
        self._condition = threading.Condition()
        self._idle: list[socket.socket] = []
        self._members: set[socket.socket] = set()
        self._leased: set[socket.socket] = set()
        self._closed = False

    @property
    def queue(self) -> list[socket.socket]:
        with self._condition:
            return list(self._idle)

    def empty(self) -> bool:
        with self._condition:
            return not self._idle

    def get_nowait(self) -> socket.socket:
        return self.get(timeout=0)

    def get(
        self,
        block: bool = True,
        timeout: float | None = None,
        cancelled: threading.Event | None = None,
    ) -> socket.socket:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._condition:
            while True:
                if self._closed:
                    raise RuntimeError("E2E UDP socket pool is closed")
                if cancelled is not None and cancelled.is_set():
                    raise RuntimeError("E2E UDP socket checkout cancelled")
                if self._idle:
                    result = self._idle.pop()
                    self._leased.add(result)
                    return result
                if len(self._members) < self.capacity:
                    result = self._factory()
                    self._members.add(result)
                    self._leased.add(result)
                    return result
                if not block or timeout == 0:
                    raise queue.Empty
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("E2E UDP socket checkout timed out")
                self._condition.wait(remaining)

    def put_nowait(self, udp: socket.socket) -> None:
        with self._condition:
            self._leased.discard(udp)
            if self._closed or getattr(udp, "is_closed", False):
                self._members.discard(udp)
                close = True
            else:
                self._members.add(udp)
                if udp not in self._idle:
                    self._idle.append(udp)
                close = False
            self._condition.notify_all()
        if close:
            udp.close()

    def discard(self, udp: socket.socket) -> None:
        with self._condition:
            self._leased.discard(udp)
            self._members.discard(udp)
            if udp in self._idle:
                self._idle.remove(udp)
            self._condition.notify_all()
        udp.close()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            sockets = list(self._members)
            self._idle.clear()
            self._leased.clear()
            self._members.clear()
            self._condition.notify_all()
        for udp in sockets:
            udp.close()


class AuthenticatedEdgeClient:
    def __init__(
        self,
        host: str,
        port: int,
        response_secret: str,
        response_key_id: str,
        checkout_timeout: float = 10,
    ):
        self.endpoint = (host, port)
        self.response_secret = response_secret.encode("utf-8")
        self.response_key_id = response_key_id
        self.checkout_timeout = checkout_timeout
        self._socket_constructor = socket.socket
        self.sockets = _EdgeSocketPool(10, lambda: self._new_socket())

    def _new_socket(self) -> socket.socket:
        udp = self._socket_constructor(socket.AF_INET, socket.SOCK_DGRAM)
        udp.settimeout(10)
        return udp

    def close(self) -> None:
        self.sockets.close()

    def reset(self) -> None:
        self.sockets.close()

    def submit(
        self,
        packet: dict[str, Any],
        key_id: str,
        principal_secret: str,
        cancelled: threading.Event | None = None,
    ) -> dict[str, Any]:
        signing_bytes = envelope_signing_bytes(
            EDGE_REQUEST_SCHEMA,
            HMAC_SHA256_ALGORITHM,
            key_id,
            packet,
        )
        signature = base64.urlsafe_b64encode(
            hmac.new(principal_secret.encode("utf-8"), signing_bytes, hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        envelope = json.dumps(
            {
                "schema_version": EDGE_REQUEST_SCHEMA,
                "payload": packet,
                "auth": {"algorithm": HMAC_SHA256_ALGORITHM, "key_id": key_id, "signature": signature},
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        udp = self.sockets.get(timeout=self.checkout_timeout, cancelled=cancelled)
        return_socket = True
        try:
            udp.sendto(envelope, self.endpoint)
            while True:
                response, source = udp.recvfrom(65535)
                payload = self._validated_payload(response, source)
                if payload.get("interaction_id") == packet.get("interaction_id"):
                    return payload
        except OSError as original:
            return_socket = False
            self.sockets.discard(udp)
            try:
                replacement = self._new_socket()
            except OSError:
                raise original
            self.sockets.put_nowait(replacement)
            raise original
        finally:
            if return_socket:
                self.sockets.put_nowait(udp)

    def _validated_payload(self, response: bytes, source: tuple[str, int]) -> dict[str, Any]:
        expected_ips = {
            row[4][0]
            for row in socket.getaddrinfo(self.endpoint[0], self.endpoint[1], socket.AF_INET, socket.SOCK_DGRAM)
        }
        if source[0] not in expected_ips or source[1] != self.endpoint[1]:
            raise AssertionError(f"edge response source {source!r} does not match {self.endpoint!r}")
        decoded = json.loads(response)
        if decoded.get("schema_version") != EDGE_RESPONSE_SCHEMA:
            raise AssertionError(f"edge response is unsigned: {decoded!r}")
        auth = decoded.get("auth") or {}
        payload = decoded.get("payload")
        if auth.get("algorithm") != HMAC_SHA256_ALGORITHM or auth.get("key_id") != self.response_key_id:
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
        if not isinstance(payload, dict):
            raise AssertionError(f"edge response payload is not an object: {payload!r}")
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
    proto_root = repo_root / "proto"
    proto_file = proto_root / "eve" / "trade_settlement" / "v1" / "trade_settlement.proto"
    validation_files = [
        proto_root / "buf" / "validate" / "validate.proto",
        proto_root / "eve" / "validation" / "v1" / "validation_rules.proto",
    ]
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
            *(str(path) for path in validation_files),
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
    url = api_gateway_url.rstrip("/") + "/gateway/healthz"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001 - preserve final connection error.
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Encore gateway did not become reachable: {last_error}")


def wait_for_market(market_url: str, timeout_seconds: float = 60.0) -> None:
    wait_for_http_health(market_url.rstrip("/") + "/market/readyz", "Encore backend", timeout_seconds)


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


def wait_for_pubsub(endpoint: str, timeout_seconds: float = 60.0) -> None:
    host, separator, port_text = endpoint.rpartition(":")
    if not separator or not host:
        raise RuntimeError(f"invalid NSQ endpoint {endpoint!r}")
    wait_for_tcp(host, int(port_text), "NSQ", timeout_seconds)


def pubsub_pending_messages(stats: dict[str, Any]) -> int:
    topics = stats.get("topics")
    if not isinstance(topics, list):
        raise ValueError("NSQ stats response must contain a topics list")
    topics_by_name = {
        str(topic.get("topic_name")): topic
        for topic in topics
        if isinstance(topic, dict) and topic.get("topic_name")
    }
    pending = 0
    for topic_name, channel_name in _E2E_PUBSUB_CHANNELS:
        topic = topics_by_name.get(topic_name)
        if topic is None:
            continue
        channels = topic.get("channels")
        if not isinstance(channels, list):
            raise ValueError(f"NSQ topic {topic_name!r} must contain a channels list")
        channel = next(
            (
                candidate
                for candidate in channels
                if isinstance(candidate, dict) and candidate.get("channel_name") == channel_name
            ),
            None,
        )
        if channel is None:
            pending += _nsq_count(topic, "depth") + _nsq_count(topic, "backend_depth")
            continue
        pending += sum(
            _nsq_count(channel, field)
            for field in ("depth", "backend_depth", "in_flight_count", "deferred_count")
        )
    return pending


def wait_for_pubsub_idle(nsq_http_url: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    url = nsq_http_url.rstrip("/") + "/stats"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, params={"format": "json"}, timeout=2.0)
            response.raise_for_status()
            pending = pubsub_pending_messages(response.json())
            if pending == 0:
                return
            last_error = RuntimeError(f"{pending} settlement messages remain pending")
        except Exception as exc:  # noqa: BLE001 - preserve the final NSQ diagnostic.
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"NSQ settlement channels did not become idle: {last_error}")


def _nsq_count(row: dict[str, Any], field: str) -> int:
    value = row.get(field, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"NSQ stats field {field!r} must be a non-negative integer")
    return value


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
    return envelope_signing_bytes(schema_version, HMAC_SHA256_ALGORITHM, key_id, payload)


def envelope_signing_bytes(schema_version: str, algorithm: str, key_id: str, payload: Any) -> bytes:
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
