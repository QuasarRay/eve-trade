CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE capsuleer (
    capsuleer_id BIGINT PRIMARY KEY,
    capsuleer_name TEXT NOT NULL,
    projection_state TEXT NOT NULL DEFAULT 'ACTIVE',
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE region (
    region_id BIGINT PRIMARY KEY,
    region_name TEXT NOT NULL,
    projection_state TEXT NOT NULL DEFAULT 'ACTIVE',
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE station (
    station_id BIGINT PRIMARY KEY,
    region_id BIGINT NOT NULL REFERENCES region(region_id),
    station_name TEXT NOT NULL,
    projection_state TEXT NOT NULL DEFAULT 'ACTIVE',
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE item_type (
    item_type_id BIGINT PRIMARY KEY,
    item_type_name TEXT NOT NULL,
    category_name TEXT NOT NULL,
    group_name TEXT NOT NULL,
    projection_state TEXT NOT NULL DEFAULT 'ACTIVE',
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE idempotency_record (
    idempotency_key TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    idempotency_state TEXT NOT NULL,
    created_by_service TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    result_settlement_batch_id UUID,
    failure_code TEXT,
    failure_message TEXT
);

CREATE TABLE request_attempt (
    request_id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL REFERENCES idempotency_record(idempotency_key),
    attempt_number INTEGER NOT NULL,
    received_by_service TEXT NOT NULL,
    attempt_state TEXT NOT NULL,
    failure_code TEXT,
    failure_message TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    UNIQUE (idempotency_key, attempt_number)
);

CREATE TABLE settlement_batch (
    settlement_batch_id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES request_attempt(request_id),
    idempotency_key TEXT NOT NULL REFERENCES idempotency_record(idempotency_key),
    external_request_id TEXT,
    caused_by_capsuleer_id BIGINT REFERENCES capsuleer(capsuleer_id),
    batch_state TEXT NOT NULL,
    created_by_service TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    failure_code TEXT,
    failure_message TEXT
);

ALTER TABLE idempotency_record
    ADD CONSTRAINT idempotency_record_result_batch_fk
    FOREIGN KEY (result_settlement_batch_id)
    REFERENCES settlement_batch(settlement_batch_id);

CREATE TABLE settlement_step (
    settlement_step_id UUID PRIMARY KEY,
    settlement_batch_id UUID NOT NULL REFERENCES settlement_batch(settlement_batch_id),
    step_index INTEGER NOT NULL,
    step_kind TEXT NOT NULL,
    step_payload JSONB NOT NULL,
    step_payload_hash TEXT NOT NULL,
    step_state TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failure_code TEXT,
    failure_message TEXT,
    UNIQUE (settlement_batch_id, step_index)
);

CREATE TABLE wallet (
    wallet_id UUID PRIMARY KEY,
    capsuleer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    wallet_kind TEXT NOT NULL,
    isk_amount BIGINT NOT NULL CHECK (isk_amount >= 0),
    wallet_state TEXT NOT NULL,
    wallet_version BIGINT NOT NULL,
    wallet_checksum TEXT NOT NULL,
    checksum_algorithm TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE item_stack (
    item_stack_id UUID PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    station_id BIGINT NOT NULL REFERENCES station(station_id),
    quantity BIGINT NOT NULL CHECK (quantity >= 0),
    stack_state TEXT NOT NULL,
    stack_version BIGINT NOT NULL,
    stack_checksum TEXT NOT NULL,
    checksum_algorithm TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE trade_instance (
    trade_instance_id UUID PRIMARY KEY,
    created_settlement_step_id UUID NOT NULL REFERENCES settlement_step(settlement_step_id),
    trade_kind TEXT NOT NULL,
    trade_state TEXT NOT NULL,
    issuer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    station_id BIGINT NOT NULL REFERENCES station(station_id),
    total_quantity BIGINT NOT NULL CHECK (total_quantity >= 0),
    remaining_quantity BIGINT NOT NULL CHECK (remaining_quantity >= 0),
    unit_price_isk BIGINT NOT NULL CHECK (unit_price_isk >= 0),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE wallet_escrow (
    wallet_escrow_id UUID PRIMARY KEY,
    trade_instance_id UUID NOT NULL REFERENCES trade_instance(trade_instance_id),
    owner_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    source_wallet_id UUID NOT NULL REFERENCES wallet(wallet_id),
    isk_amount BIGINT NOT NULL CHECK (isk_amount >= 0),
    is_released BOOLEAN NOT NULL DEFAULT false,
    created_settlement_step_id UUID NOT NULL REFERENCES settlement_step(settlement_step_id),
    released_settlement_step_id UUID REFERENCES settlement_step(settlement_step_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ
);

CREATE TABLE item_stack_escrow (
    item_stack_escrow_id UUID PRIMARY KEY,
    trade_instance_id UUID NOT NULL REFERENCES trade_instance(trade_instance_id),
    owner_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    source_item_stack_id UUID NOT NULL REFERENCES item_stack(item_stack_id),
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    station_id BIGINT NOT NULL REFERENCES station(station_id),
    quantity BIGINT NOT NULL CHECK (quantity >= 0),
    is_released BOOLEAN NOT NULL DEFAULT false,
    created_settlement_step_id UUID NOT NULL REFERENCES settlement_step(settlement_step_id),
    released_settlement_step_id UUID REFERENCES settlement_step(settlement_step_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ
);

CREATE TABLE wallet_ledger (
    wallet_ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_step_id UUID NOT NULL REFERENCES settlement_step(settlement_step_id),
    wallet_id UUID NOT NULL REFERENCES wallet(wallet_id),
    capsuleer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    entry_kind TEXT NOT NULL,
    isk_amount_delta BIGINT NOT NULL,
    isk_amount_before BIGINT NOT NULL,
    isk_amount_after BIGINT NOT NULL,
    wallet_version_before BIGINT NOT NULL,
    wallet_version_after BIGINT NOT NULL,
    wallet_checksum_before TEXT NOT NULL,
    wallet_checksum_after TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE item_stack_ledger (
    item_stack_ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_step_id UUID NOT NULL REFERENCES settlement_step(settlement_step_id),
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE trade_state_change (
    trade_state_change_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_step_id UUID NOT NULL REFERENCES settlement_step(settlement_step_id),
    trade_instance_id UUID NOT NULL REFERENCES trade_instance(trade_instance_id),
    from_trade_state TEXT NOT NULL,
    to_trade_state TEXT NOT NULL,
    trade_state_change_kind TEXT NOT NULL,
    changed_by_service TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX settlement_batch_idempotency_key_idx ON settlement_batch(idempotency_key);
CREATE INDEX settlement_step_batch_idx ON settlement_step(settlement_batch_id, step_index);
CREATE INDEX wallet_capsuleer_idx ON wallet(capsuleer_id, wallet_kind);
CREATE INDEX wallet_ledger_wallet_idx ON wallet_ledger(wallet_id, created_at);
CREATE INDEX item_stack_owner_item_station_idx ON item_stack(owner_id, item_type_id, station_id, stack_state);
CREATE INDEX item_stack_ledger_stack_idx ON item_stack_ledger(item_stack_id, created_at);
CREATE INDEX trade_instance_lookup_idx ON trade_instance(item_type_id, station_id, trade_state);

