#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
MIGRATION="${MIGRATION_FILE:-distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql}"

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$MIGRATION"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c \
  "INSERT INTO capsuleer (capsuleer_id, capsuleer_name) VALUES (999999, 'migration-preservation-marker') ON CONFLICT (capsuleer_id) DO UPDATE SET capsuleer_name = EXCLUDED.capsuleer_name;"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$MIGRATION"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -Atc \
  "SELECT capsuleer_name FROM capsuleer WHERE capsuleer_id = 999999" | grep -qx migration-preservation-marker

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
DO $role$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eve_trade_runtime') THEN
    CREATE ROLE eve_trade_runtime LOGIN PASSWORD 'runtime-password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eve_trade_market_readonly') THEN
    CREATE ROLE eve_trade_market_readonly LOGIN PASSWORD 'market-readonly-password';
  END IF;
END
$role$;
ALTER ROLE eve_trade_runtime NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE eve_trade_market_readonly NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
DO $grant$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO eve_trade_runtime', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO eve_trade_market_readonly', current_database());
END
$grant$;
GRANT USAGE ON SCHEMA public TO eve_trade_runtime;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM eve_trade_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO eve_trade_runtime;
GRANT INSERT ON
  idempotency_record,
  request_attempt,
  settlement_batch,
  settlement_step,
  wallet,
  item_stack,
  trade_instance,
  wallet_escrow,
  item_stack_escrow,
  wallet_ledger,
  item_stack_ledger,
  trade_state_change
TO eve_trade_runtime;
GRANT UPDATE ON
  idempotency_record,
  request_attempt,
  settlement_batch,
  settlement_step,
  wallet,
  item_stack,
  trade_instance,
  wallet_escrow,
  item_stack_escrow
TO eve_trade_runtime;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM eve_trade_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eve_trade_runtime;
GRANT USAGE ON SCHEMA public TO eve_trade_market_readonly;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM eve_trade_market_readonly;
GRANT SELECT ON
  item_stack,
  wallet,
  trade_instance,
  item_stack_escrow,
  idempotency_record,
  settlement_batch,
  settlement_step
TO eve_trade_market_readonly;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM eve_trade_market_readonly;
SQL

# Reapplying the migration after grants proves rerun safety does not widen the
# runtime role. The following catalog check is an allowlist, not a spot check.
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$MIGRATION"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -At <<'SQL' | grep -qx 0
WITH expected(table_name, privilege_type) AS (
  SELECT table_name, 'SELECT'
  FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  UNION ALL
  SELECT table_name, 'INSERT'
  FROM unnest(ARRAY[
    'idempotency_record', 'request_attempt', 'settlement_batch', 'settlement_step',
    'wallet', 'item_stack', 'trade_instance', 'wallet_escrow', 'item_stack_escrow',
    'wallet_ledger', 'item_stack_ledger', 'trade_state_change'
  ]) AS table_name
  UNION ALL
  SELECT table_name, 'UPDATE'
  FROM unnest(ARRAY[
    'idempotency_record', 'request_attempt', 'settlement_batch', 'settlement_step',
    'wallet', 'item_stack', 'trade_instance', 'wallet_escrow', 'item_stack_escrow'
  ]) AS table_name
), actual AS (
  SELECT table_name, privilege_type
  FROM information_schema.role_table_grants
  WHERE grantee = 'eve_trade_runtime' AND table_schema = 'public'
), differences AS (
  (SELECT * FROM expected EXCEPT SELECT * FROM actual)
  UNION ALL
  (SELECT * FROM actual EXCEPT SELECT * FROM expected)
)
SELECT count(*) FROM differences;
SQL

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -At <<'SQL' | grep -qx 0
WITH expected(table_name, privilege_type) AS (
  SELECT table_name, 'SELECT'
  FROM unnest(ARRAY[
    'item_stack', 'wallet', 'trade_instance', 'item_stack_escrow',
    'idempotency_record', 'settlement_batch', 'settlement_step'
  ]) AS table_name
), actual AS (
  SELECT table_name, privilege_type
  FROM information_schema.role_table_grants
  WHERE grantee = 'eve_trade_market_readonly' AND table_schema = 'public'
), differences AS (
  (SELECT * FROM expected EXCEPT SELECT * FROM actual)
  UNION ALL
  (SELECT * FROM actual EXCEPT SELECT * FROM expected)
)
SELECT count(*) FROM differences;
SQL
