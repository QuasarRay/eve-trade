from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


MUTABLE_TABLES = [
    "domain_event_outbox",
    "idempotency_result",
    "trade_claim_item_stack",
    "trade_claim_isk",
    "trade_claim",
    "trade_state_change",
    "settlement_step",
    "settlement",
    "trade_transaction",
    "item_stack_escrow",
    "wallet_escrow",
    "trade_instance",
    "item_stack_ledger",
    "item_stack_operation",
    "wallet_ledger",
    "wallet_operation",
    "operation",
    "request_attempt",
    "idempotency_record",
    "item_stack",
    "wallet",
    "item_type",
    "station",
    "region",
    "capsuleer",
]

REQUIRED_TABLES = set(MUTABLE_TABLES)
REQUIRED_COLUMNS = {
    "trade_instance": {
        "trade_instance_id",
        "operation_id",
        "trade_state",
        "remaining_quantity",
    },
    "operation": {"operation_id", "operation_kind", "operation_state"},
    "idempotency_record": {
        "idempotency_key",
        "request_fingerprint",
        "operation_state",
    },
    "idempotency_result": {"idempotency_key", "result_kind", "result_state"},
    "wallet_ledger": {
        "wallet_operation_id",
        "wallet_id",
        "isk_amount_delta",
        "isk_amount_before",
        "isk_amount_after",
    },
    "item_stack_ledger": {
        "item_stack_operation_id",
        "item_stack_id",
        "quantity_delta",
        "quantity_before",
        "quantity_after",
    },
    "domain_event_outbox": {
        "operation_id",
        "event_kind",
        "aggregate_kind",
        "aggregate_id",
    },
}

SAFE_DATABASE_TOKENS = ("e2e", "test", "testing", "ci")
BLOCKED_DATABASE_NAMES = {"eve_trade", "postgres", "template0", "template1"}


@dataclass(frozen=True)
class SeedWorld:
    issuer_id: int
    buyer_id: int
    region_id: int
    station_id: int
    item_type_id: int
    issuer_wallet_id: str
    buyer_wallet_id: str
    issuer_item_stack_id: str
    initial_source_quantity: int
    initial_buyer_wallet_major: Decimal
    initial_issuer_wallet_major: Decimal


@dataclass(frozen=True)
class SeedBuyer:
    buyer_id: int
    buyer_wallet_id: str
    initial_buyer_wallet_major: Decimal


def env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def database_name_from_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    return parsed.path.lstrip("/") if parsed.path else ""


def is_safe_database_name(database_name: str) -> bool:
    lowered = database_name.lower()
    if lowered in BLOCKED_DATABASE_NAMES:
        return False
    return any(token in lowered for token in SAFE_DATABASE_TOKENS)


def unique_bigint() -> int:
    return 1_000_000_000 + (uuid.uuid4().int % 8_000_000_000_000)


def stable_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [stable_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(stable_value(item) for item in value)
    if isinstance(value, dict):
        return {key: stable_value(value[key]) for key in sorted(value)}
    return value


class TradeDatabase:
    def __init__(self, conn, database_url: str):
        self.conn = conn
        self.database_url = database_url
        self.database_name = conn.info.dbname or database_name_from_url(database_url)

    @classmethod
    def connect(cls, database_url: str) -> "TradeDatabase":
        try:
            conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
        except psycopg.OperationalError as exc:
            raise AssertionError(f"PostgreSQL is not reachable: {exc}") from exc
        return cls(conn, database_url)

    @classmethod
    def connect_or_skip(cls, database_url: str) -> "TradeDatabase":
        return cls.connect(database_url)

    def close(self) -> None:
        self.conn.close()

    def assert_schema_ready(self) -> None:
        rows = self.fetch_all(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = ANY(%s)
            """,
            (list(REQUIRED_TABLES),),
        )
        present = {row["table_name"] for row in rows}
        missing = sorted(REQUIRED_TABLES - present)
        assert not missing, f"trade schema is not fully migrated; missing tables: {missing}"

        for table_name, expected_columns in REQUIRED_COLUMNS.items():
            rows = self.fetch_all(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                """,
                (table_name,),
            )
            present_columns = {row["column_name"] for row in rows}
            missing_columns = sorted(expected_columns - present_columns)
            assert not missing_columns, (
                f"trade schema table {table_name!r} is missing columns: "
                f"{missing_columns}"
            )

    def assert_safe_for_destructive_reset(self) -> None:
        if env_flag("EVE_TRADE_ALLOW_DESTRUCTIVE_DB_RESET"):
            return
        assert is_safe_database_name(self.database_name), (
            "Refusing destructive e2e reset against database "
            f"{self.database_name!r}. Use a disposable database whose name "
            "contains e2e/test/ci, or set EVE_TRADE_ALLOW_DESTRUCTIVE_DB_RESET=true."
        )

    def reset_mutable_state(self) -> None:
        self.assert_safe_for_destructive_reset()
        rows = self.fetch_all(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename = ANY(%s)
            """,
            (MUTABLE_TABLES,),
        )
        present = [row["tablename"] for row in rows]
        if not present:
            return
        table_list = sql.SQL(", ").join(sql.Identifier(table) for table in present)
        statement = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(table_list)
        with self.conn.cursor() as cur:
            cur.execute(statement)

    def execute(self, query: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(query, params)

    def fetch_one(
        self, query: str, params: tuple[Any, ...] | dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def fetch_all(
        self, query: str, params: tuple[Any, ...] | dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            return list(cur.fetchall())

    def scalar(self, query: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> Any:
        row = self.fetch_one(query, params)
        assert row is not None
        return next(iter(row.values()))

    def table_count(
        self, table_name: str, where: str = "TRUE", params: tuple[Any, ...] = ()
    ) -> int:
        statement = sql.SQL("SELECT count(*) AS count FROM {} WHERE ").format(
            sql.Identifier(table_name)
        ) + sql.SQL(where)
        with self.conn.cursor() as cur:
            cur.execute(statement, params)
            row = cur.fetchone()
        assert row is not None
        return int(row["count"])

    def seed_basic_trade_world(
        self,
        *,
        issuer_wallet_id: str,
        buyer_wallet_id: str,
        issuer_item_stack_id: str,
        issuer_id: int | None = None,
        buyer_id: int | None = None,
        region_id: int | None = None,
        station_id: int | None = None,
        item_type_id: int | None = None,
        source_quantity: int = 10,
        issuer_wallet_major: Decimal = Decimal("100.00"),
        buyer_wallet_major: Decimal = Decimal("1000000.00"),
    ) -> SeedWorld:
        issuer_id = issuer_id or unique_bigint()
        buyer_id = buyer_id or unique_bigint()
        while buyer_id == issuer_id:
            buyer_id = unique_bigint()
        region_id = region_id or unique_bigint()
        station_id = station_id or unique_bigint()
        item_type_id = item_type_id or unique_bigint()

        self.execute(
            """
            INSERT INTO capsuleer (
                capsuleer_id, capsuleer_name, projection_state, source_system,
                source_version
            )
            VALUES
                (%s, %s, 'active', 'e2e', '1'),
                (%s, %s, 'active', 'e2e', '1')
            """,
            (
                issuer_id,
                f"Issuer Pilot {issuer_id}",
                buyer_id,
                f"Buyer Pilot {buyer_id}",
            ),
        )
        self.execute(
            """
            INSERT INTO region (
                region_id, region_name, projection_state, source_system,
                source_version
            )
            VALUES (%s, %s, 'active', 'e2e', '1')
            """,
            (region_id, f"E2E Region {region_id}"),
        )
        self.execute(
            """
            INSERT INTO station (
                station_id, region_id, station_name, projection_state,
                source_system, source_version
            )
            VALUES (%s, %s, %s, 'active', 'e2e', '1')
            """,
            (station_id, region_id, f"E2E Station {station_id}"),
        )
        self.execute(
            """
            INSERT INTO item_type (
                item_type_id, item_type_name, category_name, group_name,
                catalog_version, projection_state, source_system
            )
            VALUES (%s, %s, 'Material', 'Mineral', '1', 'active', 'e2e')
            """,
            (item_type_id, f"E2E Tritanium {item_type_id}"),
        )
        self.execute(
            """
            INSERT INTO wallet (
                wallet_id, capsuleer_id, wallet_kind, isk_amount, wallet_state,
                wallet_checksum, checksum_algorithm
            )
            VALUES
                (%s, %s, 'personal', %s, 'active', 'seed-issuer', 'seed'),
                (%s, %s, 'personal', %s, 'active', 'seed-buyer', 'seed')
            """,
            (
                issuer_wallet_id,
                issuer_id,
                issuer_wallet_major,
                buyer_wallet_id,
                buyer_id,
                buyer_wallet_major,
            ),
        )
        self.execute(
            """
            INSERT INTO item_stack (
                item_stack_id, owner_id, item_type_id, station_id, quantity,
                stack_state, stack_checksum, checksum_algorithm
            )
            VALUES (%s, %s, %s, %s, %s, 'active', 'seed-source', 'seed')
            """,
            (
                issuer_item_stack_id,
                issuer_id,
                item_type_id,
                station_id,
                source_quantity,
            ),
        )
        return SeedWorld(
            issuer_id=issuer_id,
            buyer_id=buyer_id,
            region_id=region_id,
            station_id=station_id,
            item_type_id=item_type_id,
            issuer_wallet_id=issuer_wallet_id,
            buyer_wallet_id=buyer_wallet_id,
            issuer_item_stack_id=issuer_item_stack_id,
            initial_source_quantity=source_quantity,
            initial_buyer_wallet_major=buyer_wallet_major,
            initial_issuer_wallet_major=issuer_wallet_major,
        )

    def seed_extra_buyer_wallet(
        self,
        *,
        buyer_wallet_id: str,
        buyer_id: int | None = None,
        buyer_wallet_major: Decimal = Decimal("1000000.00"),
    ) -> SeedBuyer:
        buyer_id = buyer_id or unique_bigint()
        self.execute(
            """
            INSERT INTO capsuleer (
                capsuleer_id, capsuleer_name, projection_state, source_system,
                source_version
            )
            VALUES (%s, %s, 'active', 'e2e', '1')
            """,
            (buyer_id, f"Competing Buyer {buyer_id}"),
        )
        self.execute(
            """
            INSERT INTO wallet (
                wallet_id, capsuleer_id, wallet_kind, isk_amount, wallet_state,
                wallet_checksum, checksum_algorithm
            )
            VALUES (%s, %s, 'personal', %s, 'active', 'seed-buyer', 'seed')
            """,
            (buyer_wallet_id, buyer_id, buyer_wallet_major),
        )
        return SeedBuyer(
            buyer_id=buyer_id,
            buyer_wallet_id=buyer_wallet_id,
            initial_buyer_wallet_major=buyer_wallet_major,
        )

    def scenario_snapshot(self, ids) -> dict[str, Any]:
        rows = {
            "wallets": self.fetch_all(
                """
                SELECT wallet_id, capsuleer_id, isk_amount, wallet_state,
                       wallet_version, wallet_checksum, checksum_algorithm
                FROM wallet
                WHERE wallet_id = ANY(%s::uuid[])
                ORDER BY wallet_id
                """,
                ([ids.issuer_wallet_id, ids.buyer_wallet_id],),
            ),
            "item_stacks": self.fetch_all(
                """
                SELECT item_stack_id, owner_id, item_type_id, station_id, quantity,
                       stack_state, stack_version, stack_checksum, checksum_algorithm
                FROM item_stack
                WHERE item_stack_id = ANY(%s::uuid[])
                ORDER BY item_stack_id
                """,
                ([ids.issuer_item_stack_id, ids.buyer_destination_stack_id],),
            ),
            "trade_instances": self.fetch_all(
                """
                SELECT trade_instance_id, trade_state, issuer_id, issuer_wallet_id,
                       item_type_id, station_id, region_id, total_quantity,
                       remaining_quantity, unit_price_isk
                FROM trade_instance
                WHERE trade_instance_id = %s
                ORDER BY trade_instance_id
                """,
                (ids.trade_instance_id,),
            ),
            "item_stack_escrows": self.fetch_all(
                """
                SELECT item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
                       escrow_state, release_reason, source_item_stack_id
                FROM item_stack_escrow
                WHERE item_stack_escrow_id = %s
                ORDER BY item_stack_escrow_id
                """,
                (ids.item_stack_escrow_id,),
            ),
            "trade_transactions": self.fetch_all(
                """
                SELECT trade_transaction_id, operation_id, trade_instance_id,
                       trade_transaction_state, buyer_capsuleer_id, buyer_wallet_id,
                       seller_capsuleer_id, seller_wallet_id, item_type_id,
                       source_item_stack_id, destination_item_stack_id, quantity,
                       unit_price_isk, total_price_isk
                FROM trade_transaction
                WHERE trade_instance_id = %s
                   OR trade_transaction_id = %s
                ORDER BY trade_transaction_id
                """,
                (ids.trade_instance_id, ids.transaction_id),
            ),
            "settlements": self.fetch_all(
                """
                SELECT settlement_id, operation_id, trade_transaction_id,
                       idempotency_key, settlement_state, settlement_phase,
                       retry_count, failure_code
                FROM settlement
                WHERE settlement_id = %s
                   OR trade_transaction_id IN (
                       SELECT trade_transaction_id
                       FROM trade_transaction
                       WHERE trade_instance_id = %s
                   )
                ORDER BY settlement_id
                """,
                (ids.settlement_id, ids.trade_instance_id),
            ),
            "trade_claims": self.fetch_all(
                """
                SELECT trade_claim_id, operation_id, trade_transaction_id,
                       settlement_id, claiming_capsuleer_id, claim_state
                FROM trade_claim
                WHERE settlement_id IN (
                    SELECT settlement_id
                    FROM settlement
                    WHERE trade_transaction_id IN (
                        SELECT trade_transaction_id
                        FROM trade_transaction
                        WHERE trade_instance_id = %s
                    )
                )
                ORDER BY trade_claim_id
                """,
                (ids.trade_instance_id,),
            ),
            "trade_state_changes": self.fetch_all(
                """
                SELECT operation_id, trade_instance_id, trade_transaction_id,
                       settlement_id, from_trade_state, to_trade_state,
                       trade_state_change_kind, changed_by_service
                FROM trade_state_change
                WHERE trade_instance_id = %s
                ORDER BY changed_at, trade_state_change_id
                """,
                (ids.trade_instance_id,),
            ),
            "domain_events": self.fetch_all(
                """
                SELECT operation_id, event_kind, aggregate_kind, aggregate_id,
                       event_version, payload_reference, publish_state, failure_code
                FROM domain_event_outbox
                WHERE aggregate_id = %s
                ORDER BY created_at, domain_event_id
                """,
                (ids.trade_instance_id,),
            ),
        }
        return stable_value(rows)
