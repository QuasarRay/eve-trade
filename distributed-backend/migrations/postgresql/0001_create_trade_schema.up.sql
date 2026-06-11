-- 0001_create_trade_schema.up.sql
-- EVE-inspired trade system conceptual schema implemented as PostgreSQL DDL.
-- Scope: production-shaped shared trade/inventory/wallet persistence model.
-- Boundary: database enforces structural integrity only; services/game server own business/game logic.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS trade;

-- =========================
-- 1. Shared enum types
-- =========================

CREATE TYPE trade.projection_state AS ENUM (
  'active',
  'stale',
  'deleted_upstream'
);

CREATE TYPE trade.operation_state AS ENUM (
  'pending',
  'in_progress',
  'completed',
  'failed'
);

CREATE TYPE trade.request_attempt_state AS ENUM (
  'received',
  'in_progress',
  'completed',
  'failed',
  'replayed'
);

CREATE TYPE trade.wallet_state AS ENUM (
  'active',
  'frozen',
  'closed'
);

CREATE TYPE trade.wallet_kind AS ENUM (
  'personal',
  'escrow',
  'corporation',
  'system'
);

CREATE TYPE trade.stack_state AS ENUM (
  'active',
  'depleted',
  'merged',
  'destroyed'
);

CREATE TYPE trade.instance_state AS ENUM (
  'active',
  'reserved',
  'transferred',
  'destroyed'
);

CREATE TYPE trade.order_side AS ENUM (
  'buy_order',
  'sell_order'
);

CREATE TYPE trade.trade_state AS ENUM (
  'being_created',
  'outstanding',
  'accepted',
  'in_progress',
  'completed',
  'claimable',
  'claimed',
  'expired',
  'failed',
  'cancelled'
);

CREATE TYPE trade.reservation_state AS ENUM (
  'active',
  'partially_used',
  'used',
  'released'
);

CREATE TYPE trade.settlement_state AS ENUM (
  'in_progress',
  'completed',
  'failed'
);

CREATE TYPE trade.settlement_phase AS ENUM (
  'created',
  'locked_trade',
  'locked_wallets',
  'locked_items',
  'wallet_moved',
  'items_moved',
  'state_recorded',
  'completed',
  'failed'
);

CREATE TYPE trade.publish_state AS ENUM (
  'pending',
  'published',
  'failed'
);

-- Money and quantity are fixed-scale integers.
-- isk_amount is non-negative and stores minor units, for example 1 minor unit = 0.01 ISK.
CREATE DOMAIN trade.isk_amount AS BIGINT
  CHECK (VALUE >= 0);

CREATE DOMAIN trade.quantity_amount AS BIGINT
  CHECK (VALUE >= 0);

-- Signed deltas are allowed to be negative.
CREATE DOMAIN trade.isk_delta AS BIGINT;
CREATE DOMAIN trade.quantity_delta AS BIGINT;

-- =========================
-- 2. World projection tables
-- =========================

CREATE TABLE trade.capsuleer (
  capsuleer_id UUID PRIMARY KEY,
  capsuleer_name TEXT NOT NULL,
  projection_state trade.projection_state NOT NULL DEFAULT 'active',
  source_system TEXT NOT NULL,
  source_version TEXT NOT NULL,
  last_synced_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE trade.region (
  region_id UUID PRIMARY KEY,
  region_name TEXT NOT NULL,
  projection_state trade.projection_state NOT NULL DEFAULT 'active',
  source_system TEXT NOT NULL,
  source_version TEXT NOT NULL,
  last_synced_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE trade.station (
  station_id UUID PRIMARY KEY,
  region_id UUID NOT NULL REFERENCES trade.region(region_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  station_name TEXT NOT NULL,
  projection_state trade.projection_state NOT NULL DEFAULT 'active',
  source_system TEXT NOT NULL,
  source_version TEXT NOT NULL,
  last_synced_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE trade.item_type (
  item_type_id UUID PRIMARY KEY,
  item_type_name TEXT NOT NULL,
  is_stackable BOOLEAN NOT NULL,
  is_singleton_capable BOOLEAN NOT NULL,
  category_name TEXT NOT NULL,
  group_name TEXT NOT NULL,
  catalog_version TEXT NOT NULL,
  projection_state trade.projection_state NOT NULL DEFAULT 'active',
  source_system TEXT NOT NULL,
  last_synced_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT item_type_stack_or_singleton_capability CHECK (is_stackable OR is_singleton_capable)
);

-- =========================
-- 3. Idempotency and request attempts
-- =========================

CREATE TABLE trade.idempotency_record (
  idempotency_key TEXT PRIMARY KEY,
  request_fingerprint TEXT NOT NULL,
  operation_name TEXT NOT NULL,
  operation_state trade.operation_state NOT NULL DEFAULT 'pending',
  created_by_service TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  CONSTRAINT idempotency_completion_time_valid CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE TABLE trade.request_attempt (
  request_id UUID PRIMARY KEY,
  idempotency_key TEXT NOT NULL REFERENCES trade.idempotency_record(idempotency_key) ON UPDATE RESTRICT ON DELETE RESTRICT,
  received_by_service TEXT NOT NULL,
  attempt_state trade.request_attempt_state NOT NULL DEFAULT 'received',
  failure_code TEXT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  CONSTRAINT request_attempt_completion_time_valid CHECK (completed_at IS NULL OR completed_at >= received_at)
);

-- =========================
-- 4. Top-level operation
-- =========================

CREATE TABLE trade.operation (
  operation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_kind TEXT NOT NULL,
  source_system TEXT NOT NULL,
  external_operation_id TEXT NULL,
  request_id UUID NULL REFERENCES trade.request_attempt(request_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  idempotency_key TEXT NULL REFERENCES trade.idempotency_record(idempotency_key) ON UPDATE RESTRICT ON DELETE RESTRICT,
  caused_by_capsuleer_id UUID NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  operation_state trade.operation_state NOT NULL DEFAULT 'pending',
  created_by_service TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  failure_code TEXT NULL,
  failure_message TEXT NULL,
  CONSTRAINT operation_completion_time_valid CHECK (completed_at IS NULL OR completed_at >= started_at),
  CONSTRAINT operation_audit_source_present CHECK (
    idempotency_key IS NOT NULL OR external_operation_id IS NOT NULL OR request_id IS NOT NULL
  )
);

CREATE UNIQUE INDEX operation_source_external_unique
  ON trade.operation(source_system, external_operation_id)
  WHERE external_operation_id IS NOT NULL;

-- =========================
-- 5. Wallet state, operations, ledger
-- =========================

CREATE TABLE trade.wallet (
  wallet_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  capsuleer_id UUID NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  wallet_kind trade.wallet_kind NOT NULL DEFAULT 'personal',
  available_isk trade.isk_amount NOT NULL DEFAULT 0,
  reserved_isk trade.isk_amount NOT NULL DEFAULT 0,
  wallet_state trade.wallet_state NOT NULL DEFAULT 'active',
  wallet_version BIGINT NOT NULL DEFAULT 1,
  wallet_checksum TEXT NOT NULL,
  checksum_algorithm TEXT NOT NULL DEFAULT 'blake3-v1',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT wallet_version_positive CHECK (wallet_version >= 1),
  CONSTRAINT wallet_personal_has_capsuleer CHECK (
    wallet_kind <> 'personal' OR capsuleer_id IS NOT NULL
  )
);

CREATE UNIQUE INDEX wallet_unique_personal_capsuleer
  ON trade.wallet(capsuleer_id, wallet_kind)
  WHERE wallet_kind = 'personal';

CREATE TABLE trade.wallet_operation (
  wallet_operation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES trade.operation(operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  operation_kind TEXT NOT NULL,
  wallet_operation_state trade.operation_state NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  CONSTRAINT wallet_operation_completion_time_valid CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE TABLE trade.wallet_ledger (
  wallet_ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  wallet_operation_id UUID NOT NULL REFERENCES trade.wallet_operation(wallet_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  wallet_id UUID NOT NULL REFERENCES trade.wallet(wallet_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  capsuleer_id UUID NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  entry_kind TEXT NOT NULL,
  available_isk_delta trade.isk_delta NOT NULL,
  reserved_isk_delta trade.isk_delta NOT NULL,
  available_isk_before trade.isk_amount NOT NULL,
  reserved_isk_before trade.isk_amount NOT NULL,
  available_isk_after trade.isk_amount NOT NULL,
  reserved_isk_after trade.isk_amount NOT NULL,
  wallet_version_before BIGINT NOT NULL,
  wallet_version_after BIGINT NOT NULL,
  wallet_checksum_before TEXT NOT NULL,
  wallet_checksum_after TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT wallet_ledger_available_delta_matches CHECK (available_isk_after = available_isk_before + available_isk_delta),
  CONSTRAINT wallet_ledger_reserved_delta_matches CHECK (reserved_isk_after = reserved_isk_before + reserved_isk_delta),
  CONSTRAINT wallet_ledger_version_increments CHECK (wallet_version_after = wallet_version_before + 1)
);

-- =========================
-- 6. Stackable item state, operations, ledger
-- =========================

CREATE TABLE trade.item_stack (
  item_stack_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  capsuleer_id UUID NOT NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_type_id UUID NOT NULL REFERENCES trade.item_type(item_type_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  station_id UUID NOT NULL REFERENCES trade.station(station_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  available_quantity trade.quantity_amount NOT NULL DEFAULT 0,
  reserved_quantity trade.quantity_amount NOT NULL DEFAULT 0,
  stack_state trade.stack_state NOT NULL DEFAULT 'active',
  stack_version BIGINT NOT NULL DEFAULT 1,
  stack_checksum TEXT NOT NULL,
  checksum_algorithm TEXT NOT NULL DEFAULT 'blake3-v1',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT item_stack_version_positive CHECK (stack_version >= 1)
);

CREATE TABLE trade.item_stack_operation (
  item_stack_operation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES trade.operation(operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  operation_kind TEXT NOT NULL,
  item_stack_operation_state trade.operation_state NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  CONSTRAINT item_stack_operation_completion_time_valid CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE TABLE trade.item_stack_ledger (
  item_stack_ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  item_stack_operation_id UUID NOT NULL REFERENCES trade.item_stack_operation(item_stack_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_stack_id UUID NOT NULL REFERENCES trade.item_stack(item_stack_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_type_id UUID NOT NULL REFERENCES trade.item_type(item_type_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  capsuleer_id UUID NOT NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  station_id UUID NOT NULL REFERENCES trade.station(station_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  entry_kind TEXT NOT NULL,
  available_quantity_delta trade.quantity_delta NOT NULL,
  reserved_quantity_delta trade.quantity_delta NOT NULL,
  available_quantity_before trade.quantity_amount NOT NULL,
  reserved_quantity_before trade.quantity_amount NOT NULL,
  available_quantity_after trade.quantity_amount NOT NULL,
  reserved_quantity_after trade.quantity_amount NOT NULL,
  stack_version_before BIGINT NOT NULL,
  stack_version_after BIGINT NOT NULL,
  stack_checksum_before TEXT NOT NULL,
  stack_checksum_after TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT item_stack_ledger_available_delta_matches CHECK (available_quantity_after = available_quantity_before + available_quantity_delta),
  CONSTRAINT item_stack_ledger_reserved_delta_matches CHECK (reserved_quantity_after = reserved_quantity_before + reserved_quantity_delta),
  CONSTRAINT item_stack_ledger_version_increments CHECK (stack_version_after = stack_version_before + 1)
);

-- =========================
-- 7. Singleton item state, operations, ledger
-- =========================

CREATE TABLE trade.item_instance (
  item_instance_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  item_type_id UUID NOT NULL REFERENCES trade.item_type(item_type_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  capsuleer_id UUID NOT NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  station_id UUID NOT NULL REFERENCES trade.station(station_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  instance_state trade.instance_state NOT NULL DEFAULT 'active',
  instance_version BIGINT NOT NULL DEFAULT 1,
  instance_checksum TEXT NOT NULL,
  checksum_algorithm TEXT NOT NULL DEFAULT 'blake3-v1',
  source_system TEXT NOT NULL,
  source_version TEXT NOT NULL,
  last_synced_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT item_instance_version_positive CHECK (instance_version >= 1)
);

CREATE TABLE trade.item_instance_operation (
  item_instance_operation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES trade.operation(operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  operation_kind TEXT NOT NULL,
  item_instance_operation_state trade.operation_state NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  CONSTRAINT item_instance_operation_completion_time_valid CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE TABLE trade.item_instance_ledger (
  item_instance_ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  item_instance_operation_id UUID NOT NULL REFERENCES trade.item_instance_operation(item_instance_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_instance_id UUID NOT NULL REFERENCES trade.item_instance(item_instance_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_type_id UUID NOT NULL REFERENCES trade.item_type(item_type_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  capsuleer_id_before UUID NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  capsuleer_id_after UUID NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  station_id_before UUID NULL REFERENCES trade.station(station_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  station_id_after UUID NULL REFERENCES trade.station(station_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  instance_state_before trade.instance_state NOT NULL,
  instance_state_after trade.instance_state NOT NULL,
  instance_version_before BIGINT NOT NULL,
  instance_version_after BIGINT NOT NULL,
  instance_checksum_before TEXT NOT NULL,
  instance_checksum_after TEXT NOT NULL,
  entry_kind TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT item_instance_ledger_version_increments CHECK (instance_version_after = instance_version_before + 1)
);

-- =========================
-- 8. Trade orders and reservations
-- =========================

CREATE TABLE trade.trade_order (
  trade_order_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES trade.operation(operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  order_side trade.order_side NOT NULL,
  state trade.trade_state NOT NULL,
  owner_capsuleer_id UUID NOT NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  owner_wallet_id UUID NOT NULL REFERENCES trade.wallet(wallet_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_type_id UUID NOT NULL REFERENCES trade.item_type(item_type_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  offered_item_stack_id UUID NULL REFERENCES trade.item_stack(item_stack_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  offered_item_instance_id UUID NULL REFERENCES trade.item_instance(item_instance_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  station_id UUID NOT NULL REFERENCES trade.station(station_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  region_id UUID NOT NULL REFERENCES trade.region(region_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  total_quantity trade.quantity_amount NOT NULL,
  remaining_quantity trade.quantity_amount NOT NULL,
  unit_price_isk trade.isk_amount NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT trade_order_state_allowed CHECK (state IN ('being_created', 'outstanding', 'completed', 'expired', 'cancelled', 'failed')),
  CONSTRAINT trade_order_total_positive CHECK (total_quantity > 0),
  CONSTRAINT trade_order_remaining_not_more_than_total CHECK (remaining_quantity <= total_quantity),
  CONSTRAINT trade_order_unit_price_positive CHECK (unit_price_isk > 0),
  CONSTRAINT trade_order_not_both_stack_and_instance CHECK (
    NOT (offered_item_stack_id IS NOT NULL AND offered_item_instance_id IS NOT NULL)
  ),
  CONSTRAINT trade_order_sell_has_offer CHECK (
    order_side <> 'sell_order' OR offered_item_stack_id IS NOT NULL OR offered_item_instance_id IS NOT NULL
  )
);

CREATE TABLE trade.wallet_reservation (
  wallet_reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trade_order_id UUID NOT NULL UNIQUE REFERENCES trade.trade_order(trade_order_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  wallet_id UUID NOT NULL REFERENCES trade.wallet(wallet_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  created_wallet_operation_id UUID NOT NULL REFERENCES trade.wallet_operation(wallet_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  released_wallet_operation_id UUID NULL REFERENCES trade.wallet_operation(wallet_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  original_reserved_isk trade.isk_amount NOT NULL,
  remaining_reserved_isk trade.isk_amount NOT NULL,
  used_reserved_isk trade.isk_amount NOT NULL DEFAULT 0,
  released_reserved_isk trade.isk_amount NOT NULL DEFAULT 0,
  reservation_state trade.reservation_state NOT NULL DEFAULT 'active',
  release_reason TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  released_at TIMESTAMPTZ NULL,
  CONSTRAINT wallet_reservation_accounting_balances CHECK (
    original_reserved_isk = remaining_reserved_isk + used_reserved_isk + released_reserved_isk
  )
);

CREATE TABLE trade.item_stack_reservation (
  item_stack_reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trade_order_id UUID NOT NULL UNIQUE REFERENCES trade.trade_order(trade_order_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_stack_id UUID NOT NULL REFERENCES trade.item_stack(item_stack_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  created_item_stack_operation_id UUID NOT NULL REFERENCES trade.item_stack_operation(item_stack_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  released_item_stack_operation_id UUID NULL REFERENCES trade.item_stack_operation(item_stack_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  original_reserved_quantity trade.quantity_amount NOT NULL,
  remaining_reserved_quantity trade.quantity_amount NOT NULL,
  used_reserved_quantity trade.quantity_amount NOT NULL DEFAULT 0,
  released_reserved_quantity trade.quantity_amount NOT NULL DEFAULT 0,
  reservation_state trade.reservation_state NOT NULL DEFAULT 'active',
  release_reason TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  released_at TIMESTAMPTZ NULL,
  CONSTRAINT item_stack_reservation_accounting_balances CHECK (
    original_reserved_quantity = remaining_reserved_quantity + used_reserved_quantity + released_reserved_quantity
  )
);

CREATE TABLE trade.item_instance_reservation (
  item_instance_reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trade_order_id UUID NOT NULL UNIQUE REFERENCES trade.trade_order(trade_order_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_instance_id UUID NOT NULL REFERENCES trade.item_instance(item_instance_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  created_item_instance_operation_id UUID NOT NULL REFERENCES trade.item_instance_operation(item_instance_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  released_item_instance_operation_id UUID NULL REFERENCES trade.item_instance_operation(item_instance_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  reservation_state trade.reservation_state NOT NULL DEFAULT 'active',
  release_reason TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  released_at TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX item_instance_reservation_one_active
  ON trade.item_instance_reservation(item_instance_id)
  WHERE reservation_state = 'active';

-- =========================
-- 9. Trade transactions and settlement
-- =========================

CREATE TABLE trade.trade_transaction (
  trade_transaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES trade.operation(operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  trade_order_id UUID NOT NULL REFERENCES trade.trade_order(trade_order_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  state trade.trade_state NOT NULL,
  buyer_capsuleer_id UUID NOT NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  buyer_wallet_id UUID NOT NULL REFERENCES trade.wallet(wallet_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  seller_capsuleer_id UUID NOT NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  seller_wallet_id UUID NOT NULL REFERENCES trade.wallet(wallet_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_type_id UUID NOT NULL REFERENCES trade.item_type(item_type_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  source_item_stack_id UUID NULL REFERENCES trade.item_stack(item_stack_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  destination_item_stack_id UUID NULL REFERENCES trade.item_stack(item_stack_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  source_item_instance_id UUID NULL REFERENCES trade.item_instance(item_instance_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  destination_item_instance_id UUID NULL REFERENCES trade.item_instance(item_instance_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  quantity trade.quantity_amount NOT NULL,
  unit_price_isk trade.isk_amount NOT NULL,
  total_price_isk trade.isk_amount NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  CONSTRAINT trade_transaction_state_allowed CHECK (state IN ('accepted', 'in_progress', 'completed', 'claimable', 'claimed', 'failed')),
  CONSTRAINT trade_transaction_quantity_positive CHECK (quantity > 0),
  CONSTRAINT trade_transaction_unit_price_positive CHECK (unit_price_isk > 0),
  CONSTRAINT trade_transaction_total_matches CHECK (total_price_isk = quantity * unit_price_isk),
  CONSTRAINT trade_transaction_not_both_source_stack_and_instance CHECK (
    NOT (source_item_stack_id IS NOT NULL AND source_item_instance_id IS NOT NULL)
  )
);

CREATE TABLE trade.settlement (
  settlement_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES trade.operation(operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  trade_transaction_id UUID NOT NULL UNIQUE REFERENCES trade.trade_transaction(trade_transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE REFERENCES trade.idempotency_record(idempotency_key) ON UPDATE RESTRICT ON DELETE RESTRICT,
  state trade.settlement_state NOT NULL DEFAULT 'in_progress',
  settlement_phase trade.settlement_phase NOT NULL DEFAULT 'created',
  retry_count INTEGER NOT NULL DEFAULT 0,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ NULL,
  failure_code TEXT NULL,
  failure_message TEXT NULL,
  CONSTRAINT settlement_retry_count_non_negative CHECK (retry_count >= 0),
  CONSTRAINT settlement_decision_time_valid CHECK (decided_at IS NULL OR decided_at >= started_at)
);

CREATE TABLE trade.settlement_step (
  settlement_step_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  settlement_id UUID NOT NULL REFERENCES trade.settlement(settlement_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  step_name TEXT NOT NULL,
  step_state trade.operation_state NOT NULL DEFAULT 'pending',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NULL,
  failure_code TEXT NULL,
  failure_message TEXT NULL,
  CONSTRAINT settlement_step_completion_time_valid CHECK (completed_at IS NULL OR completed_at >= started_at)
);

-- =========================
-- 10. Trade state history and claim results
-- =========================

CREATE TABLE trade.trade_state_change (
  trade_state_change_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES trade.operation(operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  trade_order_id UUID NULL REFERENCES trade.trade_order(trade_order_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  trade_transaction_id UUID NULL REFERENCES trade.trade_transaction(trade_transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  settlement_id UUID NULL REFERENCES trade.settlement(settlement_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  idempotency_key TEXT NULL REFERENCES trade.idempotency_record(idempotency_key) ON UPDATE RESTRICT ON DELETE RESTRICT,
  request_id UUID NULL REFERENCES trade.request_attempt(request_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  from_state trade.trade_state NULL,
  to_state trade.trade_state NOT NULL,
  state_change_kind TEXT NOT NULL,
  changed_by_service TEXT NOT NULL,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT trade_state_change_targets_something CHECK (
    trade_order_id IS NOT NULL OR trade_transaction_id IS NOT NULL OR settlement_id IS NOT NULL
  )
);

CREATE TABLE trade.trade_claim (
  trade_claim_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES trade.operation(operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  trade_transaction_id UUID NOT NULL REFERENCES trade.trade_transaction(trade_transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  settlement_id UUID NOT NULL REFERENCES trade.settlement(settlement_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  claiming_capsuleer_id UUID NOT NULL REFERENCES trade.capsuleer(capsuleer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  state trade.trade_state NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at TIMESTAMPTZ NULL,
  CONSTRAINT trade_claim_state_allowed CHECK (state IN ('claimable', 'claimed')),
  CONSTRAINT trade_claim_claimed_time_valid CHECK (claimed_at IS NULL OR claimed_at >= created_at)
);

CREATE TABLE trade.trade_claim_isk (
  trade_claim_isk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trade_claim_id UUID NOT NULL REFERENCES trade.trade_claim(trade_claim_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  wallet_id UUID NOT NULL REFERENCES trade.wallet(wallet_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  amount_isk trade.isk_amount NOT NULL,
  CONSTRAINT trade_claim_isk_positive CHECK (amount_isk > 0)
);

CREATE TABLE trade.trade_claim_item_stack (
  trade_claim_item_stack_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trade_claim_id UUID NOT NULL REFERENCES trade.trade_claim(trade_claim_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_type_id UUID NOT NULL REFERENCES trade.item_type(item_type_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_stack_id UUID NULL REFERENCES trade.item_stack(item_stack_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  quantity trade.quantity_amount NOT NULL,
  CONSTRAINT trade_claim_item_stack_quantity_positive CHECK (quantity > 0)
);

CREATE TABLE trade.trade_claim_item_instance (
  trade_claim_item_instance_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trade_claim_id UUID NOT NULL REFERENCES trade.trade_claim(trade_claim_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_type_id UUID NOT NULL REFERENCES trade.item_type(item_type_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_instance_id UUID NOT NULL REFERENCES trade.item_instance(item_instance_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  quantity trade.quantity_amount NOT NULL DEFAULT 1,
  CONSTRAINT trade_claim_item_instance_quantity_one CHECK (quantity = 1)
);

-- =========================
-- 11. Idempotency result and event outbox
-- Created late because it references many result tables.
-- =========================

CREATE TABLE trade.idempotency_result (
  idempotency_key TEXT PRIMARY KEY REFERENCES trade.idempotency_record(idempotency_key) ON UPDATE RESTRICT ON DELETE RESTRICT,
  operation_id UUID NULL REFERENCES trade.operation(operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  result_kind TEXT NOT NULL,
  trade_order_id UUID NULL REFERENCES trade.trade_order(trade_order_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  trade_transaction_id UUID NULL REFERENCES trade.trade_transaction(trade_transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  settlement_id UUID NULL REFERENCES trade.settlement(settlement_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  wallet_operation_id UUID NULL REFERENCES trade.wallet_operation(wallet_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_stack_operation_id UUID NULL REFERENCES trade.item_stack_operation(item_stack_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  item_instance_operation_id UUID NULL REFERENCES trade.item_instance_operation(item_instance_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  result_state TEXT NOT NULL,
  failure_code TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT idempotency_result_references_something CHECK (
    operation_id IS NOT NULL OR
    trade_order_id IS NOT NULL OR
    trade_transaction_id IS NOT NULL OR
    settlement_id IS NOT NULL OR
    wallet_operation_id IS NOT NULL OR
    item_stack_operation_id IS NOT NULL OR
    item_instance_operation_id IS NOT NULL
  )
);

CREATE TABLE trade.domain_event_outbox (
  domain_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES trade.operation(operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
  event_kind TEXT NOT NULL,
  aggregate_kind TEXT NOT NULL,
  aggregate_id UUID NOT NULL,
  event_version BIGINT NOT NULL DEFAULT 1,
  payload_reference TEXT NOT NULL,
  publish_state trade.publish_state NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ NULL,
  failure_code TEXT NULL,
  CONSTRAINT domain_event_version_positive CHECK (event_version >= 1),
  CONSTRAINT domain_event_publish_time_valid CHECK (published_at IS NULL OR published_at >= created_at)
);

-- =========================
-- 12. Operational indexes
-- =========================

CREATE INDEX idx_station_region ON trade.station(region_id);
CREATE INDEX idx_wallet_capsuleer ON trade.wallet(capsuleer_id);
CREATE INDEX idx_wallet_state ON trade.wallet(wallet_state);

CREATE INDEX idx_operation_kind_state ON trade.operation(operation_kind, operation_state);
CREATE INDEX idx_operation_idempotency_key ON trade.operation(idempotency_key);
CREATE INDEX idx_request_attempt_idempotency_time ON trade.request_attempt(idempotency_key, received_at);

CREATE INDEX idx_wallet_ledger_wallet_time ON trade.wallet_ledger(wallet_id, created_at);
CREATE INDEX idx_wallet_ledger_operation ON trade.wallet_ledger(wallet_operation_id);

CREATE INDEX idx_item_stack_owner_location_type_state ON trade.item_stack(capsuleer_id, station_id, item_type_id, stack_state);
CREATE INDEX idx_item_stack_ledger_stack_time ON trade.item_stack_ledger(item_stack_id, created_at);
CREATE INDEX idx_item_stack_ledger_operation ON trade.item_stack_ledger(item_stack_operation_id);

CREATE INDEX idx_item_instance_owner_location_type_state ON trade.item_instance(capsuleer_id, station_id, item_type_id, instance_state);
CREATE INDEX idx_item_instance_ledger_instance_time ON trade.item_instance_ledger(item_instance_id, created_at);
CREATE INDEX idx_item_instance_ledger_operation ON trade.item_instance_ledger(item_instance_operation_id);

CREATE INDEX idx_trade_order_listing ON trade.trade_order(state, region_id, station_id, item_type_id, order_side);
CREATE INDEX idx_trade_order_expires_at ON trade.trade_order(expires_at);
CREATE INDEX idx_trade_order_owner_capsuleer ON trade.trade_order(owner_capsuleer_id);
CREATE INDEX idx_trade_order_owner_wallet ON trade.trade_order(owner_wallet_id);

CREATE INDEX idx_trade_transaction_order ON trade.trade_transaction(trade_order_id);
CREATE INDEX idx_trade_transaction_state ON trade.trade_transaction(state);
CREATE INDEX idx_trade_transaction_buyer ON trade.trade_transaction(buyer_capsuleer_id);
CREATE INDEX idx_trade_transaction_seller ON trade.trade_transaction(seller_capsuleer_id);

CREATE INDEX idx_settlement_operation ON trade.settlement(operation_id);
CREATE INDEX idx_settlement_state_phase ON trade.settlement(state, settlement_phase);
CREATE INDEX idx_settlement_step_settlement ON trade.settlement_step(settlement_id, started_at);

CREATE INDEX idx_trade_state_change_order_time ON trade.trade_state_change(trade_order_id, changed_at);
CREATE INDEX idx_trade_state_change_transaction_time ON trade.trade_state_change(trade_transaction_id, changed_at);
CREATE INDEX idx_trade_state_change_operation ON trade.trade_state_change(operation_id);

CREATE INDEX idx_trade_claim_transaction ON trade.trade_claim(trade_transaction_id);
CREATE INDEX idx_trade_claim_capsuleer_state ON trade.trade_claim(claiming_capsuleer_id, state);

CREATE INDEX idx_domain_event_outbox_publish_state_time ON trade.domain_event_outbox(publish_state, created_at);
CREATE INDEX idx_domain_event_outbox_operation ON trade.domain_event_outbox(operation_id);

COMMIT;
