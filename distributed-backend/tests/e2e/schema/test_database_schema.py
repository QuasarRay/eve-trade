from __future__ import annotations

import re

import pytest

from helpers.paths import POSTGRES_MIGRATIONS


pytestmark = [pytest.mark.live, pytest.mark.schema]


def test_running_database_matches_postgres_migration_schema(trade_db) -> None:
    migration = (POSTGRES_MIGRATIONS / "001_create_trade_schema.up.sql").read_text(
        encoding="utf-8"
    )
    expected_tables = parse_migration_tables(migration)
    actual_tables = {
        row["table_name"]: set(row["columns"])
        for row in trade_db.fetch_all(
            """
            SELECT table_name, array_agg(column_name ORDER BY ordinal_position) AS columns
            FROM information_schema.columns
            WHERE table_schema = 'public'
            GROUP BY table_name
            """
        )
        if row["table_name"] in expected_tables
    }

    assert actual_tables == expected_tables
    assert trade_db.scalar(
        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto')"
    )

    expected_indexes = parse_index_names(migration)
    actual_indexes = {
        row["indexname"]
        for row in trade_db.fetch_all(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
            """
        )
    }
    assert expected_indexes <= actual_indexes

    expected_triggers = parse_trigger_names(migration)
    actual_triggers = {
        row["trigger_name"]
        for row in trade_db.fetch_all(
            """
            SELECT trigger_name
            FROM information_schema.triggers
            WHERE trigger_schema = 'public'
            """
        )
    }
    assert expected_triggers <= actual_triggers


def parse_migration_tables(source: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for table, body in re.findall(
        r"CREATE TABLE ([a-z_]+) \(\n(.*?)\n\);",
        source,
        re.S,
    ):
        columns = []
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            if not line or line.startswith(("CONSTRAINT ", "PRIMARY ", "FOREIGN ")):
                continue
            columns.append(line.split()[0])
        tables[table] = set(columns)
    return tables


def parse_index_names(source: str) -> set[str]:
    return set(re.findall(r"CREATE (?:UNIQUE )?INDEX ([a-z_]+)", source))


def parse_trigger_names(source: str) -> set[str]:
    return set(re.findall(r"CREATE TRIGGER ([a-z_]+)", source))
