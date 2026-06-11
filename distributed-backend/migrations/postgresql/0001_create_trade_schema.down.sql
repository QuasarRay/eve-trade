-- 0001_create_trade_schema.down.sql
-- Destructive rollback for local/dev use. Do not run in production without a backup and a migration plan.

BEGIN;
DROP SCHEMA IF EXISTS trade CASCADE;
COMMIT;
