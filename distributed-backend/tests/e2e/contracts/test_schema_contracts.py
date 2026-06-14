from __future__ import annotations

import re

from helpers.paths import POSTGRES_MIGRATIONS, REPO_ROOT


def test_conceptual_database_schema_is_represented_in_postgres_migration() -> None:
    conceptual = (
        REPO_ROOT / "Architecture" / "Conceptual Database Schema" / "v1.md"
    ).read_text(encoding="utf-8")
    migration = (POSTGRES_MIGRATIONS / "001_create_trade_schema.up.sql").read_text(
        encoding="utf-8"
    )

    conceptual_tables = parse_conceptual_schema(conceptual)
    migration_tables = parse_migration_tables(migration)

    assert conceptual_tables, "conceptual schema parser found no tables"
    missing_tables = sorted(set(conceptual_tables) - set(migration_tables))
    assert not missing_tables, f"migration is missing conceptual tables: {missing_tables}"

    missing_columns = {
        table: sorted(columns - migration_tables[table])
        for table, columns in conceptual_tables.items()
        if table in migration_tables and columns - migration_tables[table]
    }
    assert not missing_columns, f"migration is missing conceptual columns: {missing_columns}"


def parse_conceptual_schema(source: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for table, body in re.findall(r"(?m)^([a-z_]+) \{\n(.*?)\n\}", source, re.S):
        columns = set()
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            columns.add(line.split()[0])
        tables[table] = columns
    return tables


def parse_migration_tables(source: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for table, body in re.findall(
        r"CREATE TABLE ([a-z_]+) \(\n(.*?)\n\);",
        source,
        re.S,
    ):
        columns = set()
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            if not line or line.startswith(("CONSTRAINT ", "PRIMARY ", "FOREIGN ")):
                continue
            columns.add(line.split()[0])
        tables[table] = columns
    return tables
