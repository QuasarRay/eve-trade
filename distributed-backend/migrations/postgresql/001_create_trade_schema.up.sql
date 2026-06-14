-- PostgreSQL migration: create EVE-inspired trade settlement schema
-- Intended as an "up" migration. It creates structural tables, foreign keys,
-- timestamp maintenance triggers, and indexes for common relationship lookups.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Keeps updated_at current for mutable rows.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- External/game projection of a character/capsuleer.
CREATE TABLE capsuleer (
    capsuleer_id BIGINT PRIMARY KEY,
    capsuleer_name TEXT NOT NULL,
    projection_state TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_version TEXT NOT NULL,
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_capsuleer_name_not_blank CHECK (btrim(capsuleer_name) <> ''),
    CONSTRAINT ck_capsuleer_projection_state_not_blank CHECK (btrim(projection_state) <> ''),
    CONSTRAINT ck_capsuleer_source_system_not_blank CHECK (btrim(source_system) <> ''),
    CONSTRAINT ck_capsuleer_source_version_not_blank CHECK (btrim(source_version) <> '')
);

-- External/game projection of a market region.
CREATE TABLE region (
    region_id BIGINT PRIMARY KEY,
    region_name TEXT NOT NULL,
    projection_state TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_version TEXT NOT NULL,
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_region_name_not_blank CHECK (btrim(region_name) <> ''),
    CONSTRAINT ck_region_projection_state_not_blank CHECK (btrim(projection_state) <> ''),
    CONSTRAINT ck_region_source_system_not_blank CHECK (btrim(source_system) <> ''),
    CONSTRAINT ck_region_source_version_not_blank CHECK (btrim(source_version) <> '')
);

-- External/game projection of a station inside a region.
CREATE TABLE station (
    station_id BIGINT PRIMARY KEY,
    region_id BIGINT NOT NULL REFERENCES region(region_id),
    station_name TEXT NOT NULL,
    projection_state TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_version TEXT NOT NULL,
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_station_name_not_blank CHECK (btrim(station_name) <> ''),
    CONSTRAINT ck_station_projection_state_not_blank CHECK (btrim(projection_state) <> ''),
    CONSTRAINT ck_station_source_system_not_blank CHECK (btrim(source_system) <> ''),
    CONSTRAINT ck_station_source_version_not_blank CHECK (btrim(source_version) <> '')
);

-- External/game projection of tradable item catalog data.
CREATE TABLE item_type (
    item_type_id BIGINT PRIMARY KEY,
    item_type_name TEXT NOT NULL,
    category_name TEXT NOT NULL,
    group_name TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    projection_state TEXT NOT NULL,
    source_system TEXT NOT NULL,
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_item_type_name_not_blank CHECK (btrim(item_type_name) <> ''),
    CONSTRAINT ck_item_type_category_name_not_blank CHECK (btrim(category_name) <> ''),
    CONSTRAINT ck_item_type_group_name_not_blank CHECK (btrim(group_name) <> ''),
    CONSTRAINT ck_item_type_catalog_version_not_blank CHECK (btrim(catalog_version) <> ''),
    CONSTRAINT ck_item_type_projection_state_not_blank CHECK (btrim(projection_state) <> ''),
    CONSTRAINT ck_item_type_source_system_not_blank CHECK (btrim(source_system) <> '')
);

-- Records one logical external request key so repeated requests can be deduplicated.
CREATE TABLE idempotency_record (
    idempotency_key TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    operation_name TEXT NOT NULL,
    operation_state TEXT NOT NULL,
    created_by_service TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT ck_idempotency_key_not_blank CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT ck_idempotency_request_fingerprint_not_blank CHECK (btrim(request_fingerprint) <> ''),
    CONSTRAINT ck_idempotency_operation_name_not_blank CHECK (btrim(operation_name) <> ''),
    CONSTRAINT ck_idempotency_operation_state_not_blank CHECK (btrim(operation_state) <> ''),
    CONSTRAINT ck_idempotency_created_by_service_not_blank CHECK (btrim(created_by_service) <> '')
);

-- Records each concrete attempt to process an idempotent request.
CREATE TABLE request_attempt (
    request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL REFERENCES idempotency_record(idempotency_key),
    received_by_service TEXT NOT NULL,
    attempt_state TEXT NOT NULL,
    failure_code TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT ck_request_attempt_received_by_service_not_blank CHECK (btrim(received_by_service) <> ''),
    CONSTRAINT ck_request_attempt_state_not_blank CHECK (btrim(attempt_state) <> ''),
    CONSTRAINT ck_request_attempt_failure_code_not_blank CHECK (failure_code IS NULL OR btrim(failure_code) <> '')
);

-- Root operation row used to tie together wallet, item, trade, settlement, and outbox work.
CREATE TABLE operation (
    operation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_kind TEXT NOT NULL,
    source_system TEXT NOT NULL,
    external_operation_id TEXT,
    request_id UUID REFERENCES request_attempt(request_id),
    idempotency_key TEXT REFERENCES idempotency_record(idempotency_key),
    caused_by_capsuleer_id BIGINT REFERENCES capsuleer(capsuleer_id),
    operation_state TEXT NOT NULL,
    created_by_service TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failure_code TEXT,
    failure_message TEXT,
    CONSTRAINT ck_operation_kind_not_blank CHECK (btrim(operation_kind) <> ''),
    CONSTRAINT ck_operation_source_system_not_blank CHECK (btrim(source_system) <> ''),
    CONSTRAINT ck_operation_external_operation_id_not_blank CHECK (external_operation_id IS NULL OR btrim(external_operation_id) <> ''),
    CONSTRAINT ck_operation_state_not_blank CHECK (btrim(operation_state) <> ''),
    CONSTRAINT ck_operation_created_by_service_not_blank CHECK (btrim(created_by_service) <> ''),
    CONSTRAINT ck_operation_failure_code_not_blank CHECK (failure_code IS NULL OR btrim(failure_code) <> '')
);

CREATE UNIQUE INDEX uq_operation_source_external_id
    ON operation(source_system, external_operation_id)
    WHERE external_operation_id IS NOT NULL;

-- Current wallet state. The ledger keeps history; this row is the current balance snapshot.
CREATE TABLE wallet (
    wallet_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capsuleer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    wallet_kind TEXT NOT NULL,
    isk_amount NUMERIC(20, 2) NOT NULL,
    wallet_state TEXT NOT NULL,
    wallet_version BIGINT NOT NULL DEFAULT 0,
    wallet_checksum TEXT NOT NULL,
    checksum_algorithm TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_wallet_kind_not_blank CHECK (btrim(wallet_kind) <> ''),
    CONSTRAINT ck_wallet_isk_amount_nonnegative CHECK (isk_amount >= 0),
    CONSTRAINT ck_wallet_state_not_blank CHECK (btrim(wallet_state) <> ''),
    CONSTRAINT ck_wallet_version_nonnegative CHECK (wallet_version >= 0),
    CONSTRAINT ck_wallet_checksum_not_blank CHECK (btrim(wallet_checksum) <> ''),
    CONSTRAINT ck_wallet_checksum_algorithm_not_blank CHECK (btrim(checksum_algorithm) <> '')
);

-- Logical wallet mutation grouped under a root operation.
CREATE TABLE wallet_operation (
    wallet_operation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL REFERENCES operation(operation_id),
    operation_kind TEXT NOT NULL,
    wallet_operation_state TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT ck_wallet_operation_kind_not_blank CHECK (btrim(operation_kind) <> ''),
    CONSTRAINT ck_wallet_operation_state_not_blank CHECK (btrim(wallet_operation_state) <> '')
);

-- Immutable wallet balance movement ledger.
CREATE TABLE wallet_ledger (
    wallet_ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_operation_id UUID NOT NULL REFERENCES wallet_operation(wallet_operation_id),
    wallet_id UUID NOT NULL REFERENCES wallet(wallet_id),
    capsuleer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    entry_kind TEXT NOT NULL,
    isk_amount_delta NUMERIC(20, 2) NOT NULL,
    isk_amount_before NUMERIC(20, 2) NOT NULL,
    isk_amount_after NUMERIC(20, 2) NOT NULL,
    wallet_version_before BIGINT NOT NULL,
    wallet_version_after BIGINT NOT NULL,
    wallet_checksum_before TEXT NOT NULL,
    wallet_checksum_after TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_wallet_ledger_entry_kind_not_blank CHECK (btrim(entry_kind) <> ''),
    CONSTRAINT ck_wallet_ledger_before_nonnegative CHECK (isk_amount_before >= 0),
    CONSTRAINT ck_wallet_ledger_after_nonnegative CHECK (isk_amount_after >= 0),
    CONSTRAINT ck_wallet_ledger_version_before_nonnegative CHECK (wallet_version_before >= 0),
    CONSTRAINT ck_wallet_ledger_version_after_nonnegative CHECK (wallet_version_after >= 0),
    CONSTRAINT ck_wallet_ledger_version_progression CHECK (wallet_version_after >= wallet_version_before),
    CONSTRAINT ck_wallet_ledger_checksum_before_not_blank CHECK (btrim(wallet_checksum_before) <> ''),
    CONSTRAINT ck_wallet_ledger_checksum_after_not_blank CHECK (btrim(wallet_checksum_after) <> '')
);

-- Current item stack state. The item stack ledger keeps history.
CREATE TABLE item_stack (
    item_stack_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    station_id BIGINT NOT NULL REFERENCES station(station_id),
    quantity BIGINT NOT NULL,
    stack_state TEXT NOT NULL,
    stack_version BIGINT NOT NULL DEFAULT 0,
    stack_checksum TEXT NOT NULL,
    checksum_algorithm TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_item_stack_quantity_nonnegative CHECK (quantity >= 0),
    CONSTRAINT ck_item_stack_state_not_blank CHECK (btrim(stack_state) <> ''),
    CONSTRAINT ck_item_stack_version_nonnegative CHECK (stack_version >= 0),
    CONSTRAINT ck_item_stack_checksum_not_blank CHECK (btrim(stack_checksum) <> ''),
    CONSTRAINT ck_item_stack_checksum_algorithm_not_blank CHECK (btrim(checksum_algorithm) <> '')
);

-- Logical item stack mutation grouped under a root operation.
CREATE TABLE item_stack_operation (
    item_stack_operation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL REFERENCES operation(operation_id),
    operation_kind TEXT NOT NULL,
    item_stack_operation_state TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT ck_item_stack_operation_kind_not_blank CHECK (btrim(operation_kind) <> ''),
    CONSTRAINT ck_item_stack_operation_state_not_blank CHECK (btrim(item_stack_operation_state) <> '')
);

-- Immutable item quantity movement ledger.
CREATE TABLE item_stack_ledger (
    item_stack_ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_stack_operation_id UUID NOT NULL REFERENCES item_stack_operation(item_stack_operation_id),
    item_stack_id UUID NOT NULL REFERENCES item_stack(item_stack_id),
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    owner_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    station_id BIGINT NOT NULL REFERENCES station(station_id),
    entry_kind TEXT NOT NULL,
    quantity_delta BIGINT NOT NULL,
    quantity_before BIGINT NOT NULL,
    quantity_after BIGINT NOT NULL,
    stack_version_before BIGINT NOT NULL,
    stack_version_after BIGINT NOT NULL,
    stack_checksum_before TEXT NOT NULL,
    stack_checksum_after TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_item_stack_ledger_entry_kind_not_blank CHECK (btrim(entry_kind) <> ''),
    CONSTRAINT ck_item_stack_ledger_quantity_before_nonnegative CHECK (quantity_before >= 0),
    CONSTRAINT ck_item_stack_ledger_quantity_after_nonnegative CHECK (quantity_after >= 0),
    CONSTRAINT ck_item_stack_ledger_version_before_nonnegative CHECK (stack_version_before >= 0),
    CONSTRAINT ck_item_stack_ledger_version_after_nonnegative CHECK (stack_version_after >= 0),
    CONSTRAINT ck_item_stack_ledger_version_progression CHECK (stack_version_after >= stack_version_before),
    CONSTRAINT ck_item_stack_ledger_checksum_before_not_blank CHECK (btrim(stack_checksum_before) <> ''),
    CONSTRAINT ck_item_stack_ledger_checksum_after_not_blank CHECK (btrim(stack_checksum_after) <> '')
);

-- Trade/order instance created by an issuer.
CREATE TABLE trade_instance (
    trade_instance_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL REFERENCES operation(operation_id),
    trade_state TEXT NOT NULL,
    issuer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    issuer_wallet_id UUID NOT NULL REFERENCES wallet(wallet_id),
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    station_id BIGINT NOT NULL REFERENCES station(station_id),
    region_id BIGINT NOT NULL REFERENCES region(region_id),
    total_quantity BIGINT NOT NULL,
    remaining_quantity BIGINT NOT NULL,
    unit_price_isk NUMERIC(20, 2) NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_trade_instance_state_not_blank CHECK (btrim(trade_state) <> ''),
    CONSTRAINT ck_trade_instance_total_quantity_positive CHECK (total_quantity > 0),
    CONSTRAINT ck_trade_instance_remaining_quantity_nonnegative CHECK (remaining_quantity >= 0),
    CONSTRAINT ck_trade_instance_remaining_lte_total CHECK (remaining_quantity <= total_quantity),
    CONSTRAINT ck_trade_instance_unit_price_nonnegative CHECK (unit_price_isk >= 0)
);

-- ISK escrow attached to a trade instance.
CREATE TABLE wallet_escrow (
    wallet_escrow_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_instance_id UUID NOT NULL REFERENCES trade_instance(trade_instance_id),
    isk_amount NUMERIC(20, 2) NOT NULL,
    owner_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    created_wallet_operation_id UUID NOT NULL REFERENCES wallet_operation(wallet_operation_id),
    released_wallet_operation_id UUID REFERENCES wallet_operation(wallet_operation_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,
    CONSTRAINT ck_wallet_escrow_isk_amount_nonnegative CHECK (isk_amount >= 0)
);

-- Item escrow attached to a trade instance.
CREATE TABLE item_stack_escrow (
    item_stack_escrow_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issuer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    trade_instance_id UUID NOT NULL REFERENCES trade_instance(trade_instance_id),
    quantity BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,
    escrow_state TEXT NOT NULL,
    release_reason TEXT,
    source_item_stack_id UUID NOT NULL REFERENCES item_stack(item_stack_id),
    CONSTRAINT ck_item_stack_escrow_quantity_positive CHECK (quantity >= 0),
    CONSTRAINT ck_item_stack_escrow_state_not_blank CHECK (btrim(escrow_state) <> ''),
    CONSTRAINT ck_item_stack_escrow_release_reason_not_blank CHECK (release_reason IS NULL OR btrim(release_reason) <> '')
);

-- Trade transaction created when a buyer matches against a trade instance.
CREATE TABLE trade_transaction (
    trade_transaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL REFERENCES operation(operation_id),
    trade_instance_id UUID NOT NULL REFERENCES trade_instance(trade_instance_id),
    trade_transaction_state TEXT NOT NULL,
    buyer_capsuleer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    buyer_wallet_id UUID NOT NULL REFERENCES wallet(wallet_id),
    seller_capsuleer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    seller_wallet_id UUID NOT NULL REFERENCES wallet(wallet_id),
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    source_item_stack_id UUID NOT NULL REFERENCES item_stack_escrow(item_stack_escrow_id),
    destination_item_stack_id UUID REFERENCES item_stack(item_stack_id),
    quantity BIGINT NOT NULL,
    unit_price_isk NUMERIC(20, 2) NOT NULL,
    total_price_isk NUMERIC(20, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT ck_trade_transaction_state_not_blank CHECK (btrim(trade_transaction_state) <> ''),
    CONSTRAINT ck_trade_transaction_quantity_positive CHECK (quantity > 0),
    CONSTRAINT ck_trade_transaction_unit_price_nonnegative CHECK (unit_price_isk >= 0),
    CONSTRAINT ck_trade_transaction_total_price_nonnegative CHECK (total_price_isk >= 0)
);

-- Settlement decision/progress for a trade transaction.
CREATE TABLE settlement (
    settlement_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL REFERENCES operation(operation_id),
    trade_transaction_id UUID NOT NULL REFERENCES trade_transaction(trade_transaction_id),
    idempotency_key TEXT NOT NULL REFERENCES idempotency_record(idempotency_key),
    settlement_state TEXT NOT NULL,
    settlement_phase TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    failure_code TEXT,
    failure_message TEXT,
    CONSTRAINT ck_settlement_state_not_blank CHECK (btrim(settlement_state) <> ''),
    CONSTRAINT ck_settlement_phase_not_blank CHECK (btrim(settlement_phase) <> ''),
    CONSTRAINT ck_settlement_retry_count_nonnegative CHECK (retry_count >= 0),
    CONSTRAINT ck_settlement_failure_code_not_blank CHECK (failure_code IS NULL OR btrim(failure_code) <> '')
);

-- Step-level trace of settlement work.
CREATE TABLE settlement_step (
    settlement_step_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_id UUID NOT NULL REFERENCES settlement(settlement_id),
    step_name TEXT NOT NULL,
    step_state TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    failure_code TEXT,
    failure_message TEXT,
    CONSTRAINT ck_settlement_step_name_not_blank CHECK (btrim(step_name) <> ''),
    CONSTRAINT ck_settlement_step_state_not_blank CHECK (btrim(step_state) <> ''),
    CONSTRAINT ck_settlement_step_failure_code_not_blank CHECK (failure_code IS NULL OR btrim(failure_code) <> '')
);

-- Audit trail for state transitions across trades/transactions/settlements.
CREATE TABLE trade_state_change (
    trade_state_change_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL REFERENCES operation(operation_id),
    trade_instance_id UUID REFERENCES trade_instance(trade_instance_id),
    trade_transaction_id UUID REFERENCES trade_transaction(trade_transaction_id),
    settlement_id UUID REFERENCES settlement(settlement_id),
    idempotency_key TEXT REFERENCES idempotency_record(idempotency_key),
    request_id UUID REFERENCES request_attempt(request_id),
    from_trade_state TEXT,
    to_trade_state TEXT NOT NULL,
    trade_state_change_kind TEXT NOT NULL,
    changed_by_service TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_trade_state_change_from_not_blank CHECK (from_trade_state IS NULL OR btrim(from_trade_state) <> ''),
    CONSTRAINT ck_trade_state_change_to_not_blank CHECK (btrim(to_trade_state) <> ''),
    CONSTRAINT ck_trade_state_change_kind_not_blank CHECK (btrim(trade_state_change_kind) <> ''),
    CONSTRAINT ck_trade_state_change_changed_by_service_not_blank CHECK (btrim(changed_by_service) <> '')
);

-- Claim container for assets produced by settlement.
CREATE TABLE trade_claim (
    trade_claim_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL REFERENCES operation(operation_id),
    trade_transaction_id UUID NOT NULL REFERENCES trade_transaction(trade_transaction_id),
    settlement_id UUID NOT NULL REFERENCES settlement(settlement_id),
    claiming_capsuleer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    claim_state TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    CONSTRAINT ck_trade_claim_state_not_blank CHECK (btrim(claim_state) <> '')
);

-- ISK portion of a trade claim.
CREATE TABLE trade_claim_isk (
    trade_claim_isk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_claim_id UUID NOT NULL REFERENCES trade_claim(trade_claim_id),
    wallet_id UUID NOT NULL REFERENCES wallet(wallet_id),
    amount_isk NUMERIC(20, 2) NOT NULL,
    CONSTRAINT ck_trade_claim_isk_amount_nonnegative CHECK (amount_isk >= 0)
);

-- Item stack portion of a trade claim.
CREATE TABLE trade_claim_item_stack (
    trade_claim_item_stack_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_claim_id UUID NOT NULL REFERENCES trade_claim(trade_claim_id),
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    item_stack_id UUID NOT NULL REFERENCES item_stack(item_stack_id),
    quantity BIGINT NOT NULL,
    CONSTRAINT ck_trade_claim_item_stack_quantity_positive CHECK (quantity > 0)
);

-- Cached result for replaying idempotent requests without repeating side effects.
CREATE TABLE idempotency_result (
    idempotency_key TEXT PRIMARY KEY REFERENCES idempotency_record(idempotency_key),
    operation_id UUID REFERENCES operation(operation_id),
    result_kind TEXT NOT NULL,
    trade_instance_id UUID REFERENCES trade_instance(trade_instance_id),
    trade_transaction_id UUID REFERENCES trade_transaction(trade_transaction_id),
    settlement_id UUID REFERENCES settlement(settlement_id),
    wallet_operation_id UUID REFERENCES wallet_operation(wallet_operation_id),
    item_stack_operation_id UUID REFERENCES item_stack_operation(item_stack_operation_id),
    result_state TEXT NOT NULL,
    failure_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_idempotency_result_kind_not_blank CHECK (btrim(result_kind) <> ''),
    CONSTRAINT ck_idempotency_result_state_not_blank CHECK (btrim(result_state) <> ''),
    CONSTRAINT ck_idempotency_result_failure_code_not_blank CHECK (failure_code IS NULL OR btrim(failure_code) <> '')
);

-- Transactional outbox for publishing domain events after database commit.
CREATE TABLE domain_event_outbox (
    domain_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL REFERENCES operation(operation_id),
    event_kind TEXT NOT NULL,
    aggregate_kind TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    payload_reference TEXT NOT NULL,
    publish_state TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    failure_code TEXT,
    CONSTRAINT ck_domain_event_kind_not_blank CHECK (btrim(event_kind) <> ''),
    CONSTRAINT ck_domain_event_aggregate_kind_not_blank CHECK (btrim(aggregate_kind) <> ''),
    CONSTRAINT ck_domain_event_aggregate_id_not_blank CHECK (btrim(aggregate_id) <> ''),
    CONSTRAINT ck_domain_event_version_positive CHECK (event_version > 0),
    CONSTRAINT ck_domain_event_payload_reference_not_blank CHECK (btrim(payload_reference) <> ''),
    CONSTRAINT ck_domain_event_publish_state_not_blank CHECK (btrim(publish_state) <> ''),
    CONSTRAINT ck_domain_event_failure_code_not_blank CHECK (failure_code IS NULL OR btrim(failure_code) <> '')
);

-- updated_at triggers for mutable current-state/projection tables.
CREATE TRIGGER trg_capsuleer_set_updated_at
    BEFORE UPDATE ON capsuleer
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_region_set_updated_at
    BEFORE UPDATE ON region
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_station_set_updated_at
    BEFORE UPDATE ON station
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_item_type_set_updated_at
    BEFORE UPDATE ON item_type
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_wallet_set_updated_at
    BEFORE UPDATE ON wallet
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_item_stack_set_updated_at
    BEFORE UPDATE ON item_stack
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_trade_instance_set_updated_at
    BEFORE UPDATE ON trade_instance
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_wallet_escrow_set_updated_at
    BEFORE UPDATE ON wallet_escrow
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_item_stack_escrow_set_updated_at
    BEFORE UPDATE ON item_stack_escrow
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_trade_transaction_set_updated_at
    BEFORE UPDATE ON trade_transaction
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Foreign-key and state lookup indexes.
CREATE INDEX ix_station_region_id ON station(region_id);

CREATE INDEX ix_request_attempt_idempotency_key ON request_attempt(idempotency_key);
CREATE INDEX ix_request_attempt_attempt_state ON request_attempt(attempt_state);

CREATE INDEX ix_operation_request_id ON operation(request_id);
CREATE INDEX ix_operation_idempotency_key ON operation(idempotency_key);
CREATE INDEX ix_operation_caused_by_capsuleer_id ON operation(caused_by_capsuleer_id);
CREATE INDEX ix_operation_operation_state ON operation(operation_state);

CREATE INDEX ix_wallet_capsuleer_id ON wallet(capsuleer_id);
CREATE INDEX ix_wallet_wallet_state ON wallet(wallet_state);

CREATE INDEX ix_wallet_operation_operation_id ON wallet_operation(operation_id);
CREATE INDEX ix_wallet_operation_state ON wallet_operation(wallet_operation_state);

CREATE INDEX ix_wallet_ledger_wallet_operation_id ON wallet_ledger(wallet_operation_id);
CREATE INDEX ix_wallet_ledger_wallet_id ON wallet_ledger(wallet_id);
CREATE INDEX ix_wallet_ledger_capsuleer_id ON wallet_ledger(capsuleer_id);
CREATE INDEX ix_wallet_ledger_created_at ON wallet_ledger(created_at);

CREATE INDEX ix_item_stack_owner_id ON item_stack(owner_id);
CREATE INDEX ix_item_stack_item_type_id ON item_stack(item_type_id);
CREATE INDEX ix_item_stack_station_id ON item_stack(station_id);
CREATE INDEX ix_item_stack_stack_state ON item_stack(stack_state);

CREATE INDEX ix_item_stack_operation_operation_id ON item_stack_operation(operation_id);
CREATE INDEX ix_item_stack_operation_state ON item_stack_operation(item_stack_operation_state);

CREATE INDEX ix_item_stack_ledger_operation_id ON item_stack_ledger(item_stack_operation_id);
CREATE INDEX ix_item_stack_ledger_item_stack_id ON item_stack_ledger(item_stack_id);
CREATE INDEX ix_item_stack_ledger_item_type_id ON item_stack_ledger(item_type_id);
CREATE INDEX ix_item_stack_ledger_owner_id ON item_stack_ledger(owner_id);
CREATE INDEX ix_item_stack_ledger_station_id ON item_stack_ledger(station_id);
CREATE INDEX ix_item_stack_ledger_created_at ON item_stack_ledger(created_at);

CREATE INDEX ix_trade_instance_operation_id ON trade_instance(operation_id);
CREATE INDEX ix_trade_instance_trade_state ON trade_instance(trade_state);
CREATE INDEX ix_trade_instance_issuer_id ON trade_instance(issuer_id);
CREATE INDEX ix_trade_instance_issuer_wallet_id ON trade_instance(issuer_wallet_id);
CREATE INDEX ix_trade_instance_item_type_id ON trade_instance(item_type_id);
CREATE INDEX ix_trade_instance_station_id ON trade_instance(station_id);
CREATE INDEX ix_trade_instance_region_id ON trade_instance(region_id);
CREATE INDEX ix_trade_instance_expires_at ON trade_instance(expires_at);

CREATE INDEX ix_wallet_escrow_trade_instance_id ON wallet_escrow(trade_instance_id);
CREATE INDEX ix_wallet_escrow_owner_id ON wallet_escrow(owner_id);
CREATE INDEX ix_wallet_escrow_created_operation_id ON wallet_escrow(created_wallet_operation_id);
CREATE INDEX ix_wallet_escrow_released_operation_id ON wallet_escrow(released_wallet_operation_id);

CREATE INDEX ix_item_stack_escrow_issuer_id ON item_stack_escrow(issuer_id);
CREATE INDEX ix_item_stack_escrow_trade_instance_id ON item_stack_escrow(trade_instance_id);
CREATE INDEX ix_item_stack_escrow_source_stack_id ON item_stack_escrow(source_item_stack_id);
CREATE INDEX ix_item_stack_escrow_state ON item_stack_escrow(escrow_state);

CREATE INDEX ix_trade_transaction_operation_id ON trade_transaction(operation_id);
CREATE INDEX ix_trade_transaction_trade_instance_id ON trade_transaction(trade_instance_id);
CREATE INDEX ix_trade_transaction_state ON trade_transaction(trade_transaction_state);
CREATE INDEX ix_trade_transaction_buyer_capsuleer_id ON trade_transaction(buyer_capsuleer_id);
CREATE INDEX ix_trade_transaction_buyer_wallet_id ON trade_transaction(buyer_wallet_id);
CREATE INDEX ix_trade_transaction_seller_capsuleer_id ON trade_transaction(seller_capsuleer_id);
CREATE INDEX ix_trade_transaction_seller_wallet_id ON trade_transaction(seller_wallet_id);
CREATE INDEX ix_trade_transaction_item_type_id ON trade_transaction(item_type_id);
CREATE INDEX ix_trade_transaction_source_stack_id ON trade_transaction(source_item_stack_id);
CREATE INDEX ix_trade_transaction_destination_stack_id ON trade_transaction(destination_item_stack_id);

CREATE INDEX ix_settlement_operation_id ON settlement(operation_id);
CREATE INDEX ix_settlement_trade_transaction_id ON settlement(trade_transaction_id);
CREATE INDEX ix_settlement_idempotency_key ON settlement(idempotency_key);
CREATE INDEX ix_settlement_state ON settlement(settlement_state);
CREATE INDEX ix_settlement_phase ON settlement(settlement_phase);

CREATE INDEX ix_settlement_step_settlement_id ON settlement_step(settlement_id);
CREATE INDEX ix_settlement_step_state ON settlement_step(step_state);

CREATE INDEX ix_trade_state_change_operation_id ON trade_state_change(operation_id);
CREATE INDEX ix_trade_state_change_trade_instance_id ON trade_state_change(trade_instance_id);
CREATE INDEX ix_trade_state_change_trade_transaction_id ON trade_state_change(trade_transaction_id);
CREATE INDEX ix_trade_state_change_settlement_id ON trade_state_change(settlement_id);
CREATE INDEX ix_trade_state_change_idempotency_key ON trade_state_change(idempotency_key);
CREATE INDEX ix_trade_state_change_request_id ON trade_state_change(request_id);
CREATE INDEX ix_trade_state_change_changed_at ON trade_state_change(changed_at);

CREATE INDEX ix_trade_claim_operation_id ON trade_claim(operation_id);
CREATE INDEX ix_trade_claim_trade_transaction_id ON trade_claim(trade_transaction_id);
CREATE INDEX ix_trade_claim_settlement_id ON trade_claim(settlement_id);
CREATE INDEX ix_trade_claim_claiming_capsuleer_id ON trade_claim(claiming_capsuleer_id);
CREATE INDEX ix_trade_claim_state ON trade_claim(claim_state);

CREATE INDEX ix_trade_claim_isk_trade_claim_id ON trade_claim_isk(trade_claim_id);
CREATE INDEX ix_trade_claim_isk_wallet_id ON trade_claim_isk(wallet_id);

CREATE INDEX ix_trade_claim_item_stack_trade_claim_id ON trade_claim_item_stack(trade_claim_id);
CREATE INDEX ix_trade_claim_item_stack_item_type_id ON trade_claim_item_stack(item_type_id);
CREATE INDEX ix_trade_claim_item_stack_item_stack_id ON trade_claim_item_stack(item_stack_id);

CREATE INDEX ix_idempotency_result_operation_id ON idempotency_result(operation_id);
CREATE INDEX ix_idempotency_result_trade_instance_id ON idempotency_result(trade_instance_id);
CREATE INDEX ix_idempotency_result_trade_transaction_id ON idempotency_result(trade_transaction_id);
CREATE INDEX ix_idempotency_result_settlement_id ON idempotency_result(settlement_id);
CREATE INDEX ix_idempotency_result_wallet_operation_id ON idempotency_result(wallet_operation_id);
CREATE INDEX ix_idempotency_result_item_stack_operation_id ON idempotency_result(item_stack_operation_id);
CREATE INDEX ix_idempotency_result_state ON idempotency_result(result_state);

CREATE INDEX ix_domain_event_outbox_operation_id ON domain_event_outbox(operation_id);
CREATE INDEX ix_domain_event_outbox_publish_state ON domain_event_outbox(publish_state);
CREATE INDEX ix_domain_event_outbox_aggregate ON domain_event_outbox(aggregate_kind, aggregate_id);
CREATE INDEX ix_domain_event_outbox_created_at ON domain_event_outbox(created_at);

COMMIT;
