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
GRANT CONNECT ON DATABASE eve_trade TO eve_trade_runtime;
GRANT USAGE ON SCHEMA public TO eve_trade_runtime;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM eve_trade_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO eve_trade_runtime;
GRANT INSERT ON
  idempotency_record, request_attempt, settlement_batch, settlement_step,
  wallet, item_stack, trade_instance, wallet_escrow, item_stack_escrow,
  wallet_ledger, item_stack_ledger, trade_state_change
TO eve_trade_runtime;
GRANT UPDATE ON
  idempotency_record, request_attempt, settlement_batch, settlement_step,
  wallet, item_stack, trade_instance, wallet_escrow, item_stack_escrow
TO eve_trade_runtime;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM eve_trade_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eve_trade_runtime;

GRANT CONNECT ON DATABASE eve_trade TO eve_trade_market_readonly;
GRANT USAGE ON SCHEMA public TO eve_trade_market_readonly;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM eve_trade_market_readonly;
GRANT SELECT ON
  item_stack, wallet, trade_instance, item_stack_escrow,
  idempotency_record, settlement_batch, settlement_step
TO eve_trade_market_readonly;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM eve_trade_market_readonly;
