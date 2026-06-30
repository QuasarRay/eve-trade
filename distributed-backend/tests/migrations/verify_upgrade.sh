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
END
$role$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
DO $grant$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO eve_trade_runtime', current_database());
END
$grant$;
GRANT USAGE ON SCHEMA public TO eve_trade_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eve_trade_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eve_trade_runtime;
SQL
