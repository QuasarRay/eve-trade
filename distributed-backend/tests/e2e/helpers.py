from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
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

GATEWAY_SERVICE_PATH = "/eve.api_gateway.v1.GameTradeGatewayService"


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
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(timeout=httpx.Timeout(20.0))

    def issue_trade_instance(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("IssueTradeInstance", payload)

    def accept_trade_instance(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("AcceptTradeInstance", payload)

    def cancel_trade_instance(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("CancelTradeInstance", payload)

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.http.post(
            f"{self.base_url}{GATEWAY_SERVICE_PATH}/{method}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Connect-Protocol-Version": "1",
            },
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
        return response.json()


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
    url = api_gateway_url.rstrip("/") + "/__e2e_ready_probe"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code >= 400:
                return
        except Exception as exc:  # noqa: BLE001 - preserve final connection error.
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"api gateway did not become reachable: {last_error}")


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
            item_stack_checksum(item_stack_id, quantity, stack_version),
            CHECKSUM_ALGORITHM,
        ),
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
    key = idempotency_key or fresh_key("issue")
    return {
        "idempotencyKey": key,
        "externalRequestId": f"external-{key}",
        "issuedByCapsuleerId": issued_by_capsuleer_id or world.seller_id,
        "itemStack": {
            "itemStackId": item_stack_id or world.seller_stack_id,
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
    key = idempotency_key or fresh_key("issue")
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
    **_ignored_client_facts: Any,
) -> dict[str, Any]:
    requested = trade.quantity if quantity is None else quantity
    key = idempotency_key or fresh_key("accept")
    return {
        "idempotencyKey": key,
        "externalRequestId": f"external-{key}",
        "tradeInstanceId": trade.trade_instance_id,
        "buyerCapsuleerId": buyer_capsuleer_id or world.buyer_id,
        "quantityRequested": requested,
        "buyerWalletId": buyer_wallet_id or world.buyer_wallet_id,
        "buyerDestinationItemStackId": buyer_destination_item_stack_id or "",
    }


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
    **_ignored_client_facts: Any,
) -> dict[str, Any]:
    key = idempotency_key or fresh_key("cancel")
    return {
        "idempotencyKey": key,
        "externalRequestId": f"external-{key}",
        "tradeInstanceId": trade.trade_instance_id,
        "cancelledByCapsuleerId": cancelled_by_capsuleer_id or world.seller_id,
    }


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
    code: str | None = None,
    contains: str | None = None,
) -> RpcFailure:
    try:
        call()
    except RpcFailure as exc:
        if code is not None and exc.code != code:
            raise AssertionError(f"RPC code {exc.code!r}, expected {code!r}") from exc
        if contains is not None and contains.lower() not in exc.message.lower():
            raise AssertionError(
                f"RPC message {exc.message!r} did not contain {contains!r}"
            ) from exc
        return exc
    raise AssertionError("expected RPC request to fail")


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
