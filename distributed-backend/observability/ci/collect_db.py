"""Bounded, read-only PostgreSQL diagnostic snapshot collector."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from typing import Any

from .redaction import redact_text
from .run_context import RunContext
from .storage import RunStorage

LIKELY_TABLES = (
    "trade_instance", "item_stack", "item_stack_escrow", "wallet", "wallet_escrow",
    "idempotency_record", "request_attempt", "settlement_batch", "settlement_step",
    "wallet_ledger", "item_stack_ledger",
)


def collect_db(
    context: RunContext,
    storage: RunStorage | None = None,
) -> dict[str, Any]:
    storage = storage or RunStorage(context.run_dir)
    database_url = os.getenv("EVE_TRADE_DATABASE_URL") or os.getenv("DATABASE_URL", "")
    metadata: dict[str, Any] = {"available": False, "tables": [], "errors": []}
    if not database_url or not shutil.which("psql"):
        metadata["errors"].append("EVE_TRADE_DATABASE_URL/DATABASE_URL or psql executable unavailable")
        storage.write_json("db/metadata.json", metadata)
        return metadata
    metadata.update({"available": True, "database": "<redacted:url>"})
    tables_query = "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' ORDER BY tablename;"
    code, table_output = _psql(database_url, tables_query, context.repo_root, tuples=True)
    storage.write_text("db/tables.txt", redact_text(table_output))
    if code:
        metadata["errors"].append("table list query failed")
    tables = [line.strip() for line in table_output.splitlines() if line.strip()]
    metadata["tables"] = tables
    schema_query = (
        "SELECT table_name,column_name,data_type,is_nullable "
        "FROM information_schema.columns WHERE table_schema='public' "
        "ORDER BY table_name,ordinal_position;"
    )
    schema_code, schema = _psql(database_url, schema_query, context.repo_root, tuples=True)
    storage.write_text("db/schema.txt", redact_text(schema))
    schema_hash = hashlib.sha256(schema.encode("utf-8")).hexdigest() if schema_code == 0 else ""
    metadata["db.schema_hash"] = schema_hash
    for table in LIKELY_TABLES:
        if table not in tables:
            continue
        query = f'SELECT * FROM "{table}" ORDER BY 1 DESC LIMIT 200;'
        json_query = (
            "SELECT COALESCE(json_agg(row_to_json(snapshot)), '[]'::json)::text "
            f'FROM (SELECT * FROM "{table}" ORDER BY 1 DESC LIMIT 200) snapshot;'
        )
        text_code, text_output = _psql(database_url, query, context.repo_root)
        csv_code, csv_output = _psql(database_url, query, context.repo_root, csv=True)
        json_code, json_output = _psql(database_url, json_query, context.repo_root, tuples=True)
        storage.write_text(f"db/tables/{table}.txt", redact_text(text_output))
        storage.write_text(f"db/tables/{table}.csv", redact_text(csv_output))
        storage.write_text(f"db/tables/{table}.json", redact_text(json_output))
        if text_code or csv_code or json_code:
            metadata["errors"].append(f"snapshot failed for {table}")
    migration_tables = [name for name in tables if "migration" in name.lower()]
    for table in migration_tables:
        code, output = _psql(database_url, f'SELECT * FROM "{table}" ORDER BY 1;', context.repo_root)
        storage.write_text(f"db/migrations/{table}.txt", redact_text(output))
        if code:
            metadata["errors"].append(f"migration snapshot failed for {table}")
    storage.write_json("db/metadata.json", metadata)
    return metadata


def _psql(
    database_url: str, query: str, cwd: Any,
    *, tuples: bool = False, csv: bool = False,
) -> tuple[int, str]:
    argv = ["psql", database_url, "-X", "-v", "ON_ERROR_STOP=1"]
    if tuples:
        argv.extend(["-A", "-t"])
    if csv:
        argv.append("--csv")
    argv.extend(["-c", query])
    try:
        result = subprocess.run(argv, cwd=cwd, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, check=False)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}: {exc}\n"
