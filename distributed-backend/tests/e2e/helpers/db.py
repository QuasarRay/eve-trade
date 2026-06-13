from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg
import pytest
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


class TradeDatabase:
    def __init__(self, conn):
        self.conn = conn

    @classmethod
    def connect_or_skip(cls, database_url: str) -> "TradeDatabase":
        try:
            conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
        except psycopg.OperationalError as exc:
            pytest.skip(f"PostgreSQL is not reachable: {exc}")
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    def assert_schema_ready(self) -> None:
        row = self.fetch_one("SELECT to_regclass('trade_instance') AS table_name")
        if not row or row["table_name"] is None:
            pytest.skip("trade schema is not migrated in the configured database")

    def reset_mutable_state(self) -> None:
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
        table_list = ", ".join(present)
        self.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)

    def fetch_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def fetch_all(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def seed_basic_trade_world(
        self,
        *,
        issuer_wallet_id: str,
        buyer_wallet_id: str,
        issuer_item_stack_id: str,
        issuer_id: int = 1001,
        buyer_id: int = 2002,
        region_id: int = 30000001,
        station_id: int = 60003760,
        item_type_id: int = 34,
        source_quantity: int = 10,
        issuer_wallet_major: Decimal = Decimal("100.00"),
        buyer_wallet_major: Decimal = Decimal("1000000.00"),
    ) -> SeedWorld:
        self.execute(
            """
            INSERT INTO capsuleer (
                capsuleer_id, capsuleer_name, projection_state, source_system,
                source_version
            )
            VALUES
                (%s, 'Issuer Pilot', 'active', 'e2e', '1'),
                (%s, 'Buyer Pilot', 'active', 'e2e', '1')
            """,
            (issuer_id, buyer_id),
        )
        self.execute(
            """
            INSERT INTO region (
                region_id, region_name, projection_state, source_system,
                source_version
            )
            VALUES (%s, 'The Forge', 'active', 'e2e', '1')
            """,
            (region_id,),
        )
        self.execute(
            """
            INSERT INTO station (
                station_id, region_id, station_name, projection_state,
                source_system, source_version
            )
            VALUES (%s, %s, 'Jita IV - Moon 4', 'active', 'e2e', '1')
            """,
            (station_id, region_id),
        )
        self.execute(
            """
            INSERT INTO item_type (
                item_type_id, item_type_name, category_name, group_name,
                catalog_version, projection_state, source_system
            )
            VALUES (%s, 'Tritanium', 'Material', 'Mineral', '1', 'active', 'e2e')
            """,
            (item_type_id,),
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
