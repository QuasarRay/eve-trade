CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION compute_item_stack_ledger_payload_hash(
    p_settlement_step_id UUID,
    p_item_stack_id UUID,
    p_ledger_sequence BIGINT,
    p_item_type_id BIGINT,
    p_owner_id BIGINT,
    p_station_id BIGINT,
    p_entry_kind TEXT,
    p_quantity_delta BIGINT,
    p_quantity_before BIGINT,
    p_quantity_after BIGINT,
    p_stack_state_before TEXT,
    p_stack_state_after TEXT,
    p_stack_version_before BIGINT,
    p_stack_version_after BIGINT,
    p_stack_checksum_before TEXT,
    p_stack_checksum_after TEXT
)
RETURNS TEXT AS $$
    SELECT encode(
        digest(
            concat_ws(
                '|',
                'item_stack_ledger_payload_v1',
                p_settlement_step_id::text,
                p_item_stack_id::text,
                p_ledger_sequence::text,
                p_item_type_id::text,
                p_owner_id::text,
                p_station_id::text,
                p_entry_kind,
                p_quantity_delta::text,
                p_quantity_before::text,
                p_quantity_after::text,
                p_stack_state_before,
                p_stack_state_after,
                p_stack_version_before::text,
                p_stack_version_after::text,
                p_stack_checksum_before,
                p_stack_checksum_after
            ),
            'sha256'
        ),
        'hex'
    );
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION compute_item_stack_ledger_hash(
    p_previous_item_stack_ledger_hash TEXT,
    p_ledger_payload_hash TEXT
)
RETURNS TEXT AS $$
    SELECT encode(
        digest(
            concat_ws(
                '|',
                'item_stack_ledger_hash_v1',
                p_previous_item_stack_ledger_hash,
                p_ledger_payload_hash
            ),
            'sha256'
        ),
        'hex'
    );
$$ LANGUAGE sql IMMUTABLE;

CREATE TABLE IF NOT EXISTS capsuleer (
    capsuleer_id BIGINT PRIMARY KEY,
    capsuleer_name TEXT NOT NULL,
    projection_state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (projection_state IN ('ACTIVE', 'DELETED')),
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS region (
    region_id BIGINT PRIMARY KEY,
    region_name TEXT NOT NULL,
    projection_state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (projection_state IN ('ACTIVE', 'DELETED')),
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS station (
    station_id BIGINT PRIMARY KEY,
    region_id BIGINT NOT NULL REFERENCES region(region_id),
    station_name TEXT NOT NULL,
    projection_state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (projection_state IN ('ACTIVE', 'DELETED')),
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS item_type (
    item_type_id BIGINT PRIMARY KEY,
    item_type_name TEXT NOT NULL,
    category_name TEXT NOT NULL,
    group_name TEXT NOT NULL,
    projection_state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (projection_state IN ('ACTIVE', 'DELETED')),
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idempotency_record (
    idempotency_key TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    idempotency_state TEXT NOT NULL CHECK (idempotency_state IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')),
    created_by_service TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    result_settlement_batch_id UUID,
    failure_code TEXT,
    failure_message TEXT
);

CREATE TABLE IF NOT EXISTS request_attempt (
    request_id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL REFERENCES idempotency_record(idempotency_key),
    attempt_number INTEGER NOT NULL,
    received_by_service TEXT NOT NULL,
    attempt_state TEXT NOT NULL CHECK (attempt_state IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')),
    failure_code TEXT,
    failure_message TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    UNIQUE (idempotency_key, attempt_number)
);

CREATE TABLE IF NOT EXISTS settlement_batch (
    settlement_batch_id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES request_attempt(request_id),
    idempotency_key TEXT NOT NULL REFERENCES idempotency_record(idempotency_key),
    external_request_id TEXT,
    caused_by_capsuleer_id BIGINT REFERENCES capsuleer(capsuleer_id),
    batch_state TEXT NOT NULL CHECK (batch_state IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')),
    created_by_service TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    failure_code TEXT,
    failure_message TEXT
);

ALTER TABLE idempotency_record
    DROP CONSTRAINT IF EXISTS idempotency_record_result_batch_fk;

ALTER TABLE idempotency_record
    ADD CONSTRAINT idempotency_record_result_batch_fk
    FOREIGN KEY (result_settlement_batch_id)
    REFERENCES settlement_batch(settlement_batch_id);

CREATE TABLE IF NOT EXISTS settlement_step (
    settlement_step_id UUID PRIMARY KEY,
    settlement_batch_id UUID NOT NULL REFERENCES settlement_batch(settlement_batch_id),
    step_index INTEGER NOT NULL,
    step_kind TEXT NOT NULL,
    step_payload JSONB NOT NULL,
    step_payload_hash TEXT NOT NULL,
    step_output JSONB NOT NULL DEFAULT '{}'::jsonb,
    step_state TEXT NOT NULL CHECK (step_state IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failure_code TEXT,
    failure_message TEXT,
    UNIQUE (settlement_batch_id, step_index)
);

ALTER TABLE settlement_step
    ADD COLUMN IF NOT EXISTS step_output JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS wallet (
    wallet_id UUID PRIMARY KEY,
    capsuleer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    wallet_kind TEXT NOT NULL CHECK (wallet_kind IN ('PRIMARY')),
    isk_amount BIGINT NOT NULL CHECK (isk_amount >= 0),
    wallet_state TEXT NOT NULL CHECK (wallet_state IN ('ACTIVE', 'SUSPENDED', 'CLOSED')),
    wallet_version BIGINT NOT NULL,
    wallet_checksum TEXT NOT NULL,
    checksum_algorithm TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS item_stack (
    item_stack_id UUID PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    station_id BIGINT NOT NULL REFERENCES station(station_id),
    quantity BIGINT NOT NULL CHECK (quantity >= 0),
    stack_state TEXT NOT NULL CHECK (stack_state IN ('ACTIVE', 'LOCKED', 'DEPLETED', 'MERGED')),
    stack_version BIGINT NOT NULL,
    stack_checksum TEXT NOT NULL,
    checksum_algorithm TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trade_instance (
    trade_instance_id UUID PRIMARY KEY,
    created_settlement_step_id UUID NOT NULL REFERENCES settlement_step(settlement_step_id),
    trade_kind TEXT NOT NULL CHECK (trade_kind IN ('SELL')),
    trade_state TEXT NOT NULL CHECK (trade_state IN ('OPEN', 'CANCELLED', 'COMPLETED')),
    issuer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    station_id BIGINT NOT NULL REFERENCES station(station_id),
    total_quantity BIGINT NOT NULL CHECK (total_quantity > 0),
    remaining_quantity BIGINT NOT NULL CHECK (remaining_quantity >= 0 AND remaining_quantity <= total_quantity),
    unit_price_isk BIGINT NOT NULL CHECK (unit_price_isk > 0),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wallet_escrow (
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

CREATE TABLE IF NOT EXISTS item_stack_escrow (
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

CREATE TABLE IF NOT EXISTS wallet_ledger (
    wallet_ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_step_id UUID NOT NULL REFERENCES settlement_step(settlement_step_id),
    wallet_id UUID NOT NULL REFERENCES wallet(wallet_id),
    capsuleer_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    entry_kind TEXT NOT NULL CHECK (entry_kind IN (
        'TRANSFER_TO_ESCROW',
        'TRANSFER_FROM_ESCROW_TO_NEW_OWNER',
        'TRANSFER_FROM_ESCROW_TO_PREVIOUS_OWNER'
    )),
    isk_amount_delta BIGINT NOT NULL,
    isk_amount_before BIGINT NOT NULL,
    isk_amount_after BIGINT NOT NULL,
    wallet_version_before BIGINT NOT NULL,
    wallet_version_after BIGINT NOT NULL,
    wallet_checksum_before TEXT NOT NULL,
    wallet_checksum_after TEXT NOT NULL,
    CONSTRAINT wallet_ledger_amount_delta_chk CHECK (isk_amount_after = isk_amount_before + isk_amount_delta),
    CONSTRAINT wallet_ledger_version_increment_chk CHECK (wallet_version_after = wallet_version_before + 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS item_stack_ledger (
    item_stack_ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_step_id UUID NOT NULL REFERENCES settlement_step(settlement_step_id),
    item_stack_id UUID NOT NULL REFERENCES item_stack(item_stack_id),
    ledger_sequence BIGINT NOT NULL CHECK (ledger_sequence > 0),
    previous_item_stack_ledger_hash TEXT NOT NULL,
    ledger_payload_hash TEXT NOT NULL,
    item_stack_ledger_hash TEXT NOT NULL,
    item_type_id BIGINT NOT NULL REFERENCES item_type(item_type_id),
    owner_id BIGINT NOT NULL REFERENCES capsuleer(capsuleer_id),
    station_id BIGINT NOT NULL REFERENCES station(station_id),
    entry_kind TEXT NOT NULL CHECK (entry_kind IN (
        'CREATE_STACK',
        'TRANSFER_TO_ESCROW',
        'TRANSFER_FROM_ESCROW_TO_NEW_OWNER',
        'TRANSFER_FROM_ESCROW_TO_PREVIOUS_OWNER',
        'MERGE_IN',
        'MERGE_OUT'
    )),
    quantity_delta BIGINT NOT NULL,
    quantity_before BIGINT NOT NULL,
    quantity_after BIGINT NOT NULL,
    stack_state_before TEXT NOT NULL CHECK (stack_state_before IN ('ABSENT', 'ACTIVE', 'LOCKED', 'DEPLETED', 'MERGED')),
    stack_state_after TEXT NOT NULL CHECK (stack_state_after IN ('ACTIVE', 'LOCKED', 'DEPLETED', 'MERGED')),
    stack_version_before BIGINT NOT NULL,
    stack_version_after BIGINT NOT NULL,
    stack_checksum_before TEXT NOT NULL,
    stack_checksum_after TEXT NOT NULL,
    CONSTRAINT item_stack_ledger_quantity_delta_chk CHECK (quantity_after = quantity_before + quantity_delta),
    CONSTRAINT item_stack_ledger_version_increment_chk CHECK (stack_version_after = stack_version_before + 1),
    CONSTRAINT item_stack_ledger_sequence_version_chk CHECK (ledger_sequence = stack_version_after),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION reject_ledger_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'ledger tables are append-only; write a new ledger row instead'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

ALTER TABLE item_stack
    DROP CONSTRAINT IF EXISTS item_stack_stack_state_check;

ALTER TABLE item_stack
    ADD CONSTRAINT item_stack_stack_state_check
    CHECK (stack_state IN ('ACTIVE', 'LOCKED', 'DEPLETED', 'MERGED'));

ALTER TABLE wallet_ledger
    DROP CONSTRAINT IF EXISTS wallet_ledger_entry_kind_check;

ALTER TABLE wallet_ledger
    ADD CONSTRAINT wallet_ledger_entry_kind_check
    CHECK (entry_kind IN (
        'TRANSFER_TO_ESCROW',
        'TRANSFER_FROM_ESCROW_TO_NEW_OWNER',
        'TRANSFER_FROM_ESCROW_TO_PREVIOUS_OWNER'
    ));

ALTER TABLE item_stack_ledger
    DROP CONSTRAINT IF EXISTS item_stack_ledger_entry_kind_check;

ALTER TABLE item_stack_ledger
    ADD CONSTRAINT item_stack_ledger_entry_kind_check
    CHECK (entry_kind IN (
        'CREATE_STACK',
        'TRANSFER_TO_ESCROW',
        'TRANSFER_FROM_ESCROW_TO_NEW_OWNER',
        'TRANSFER_FROM_ESCROW_TO_PREVIOUS_OWNER',
        'MERGE_IN',
        'MERGE_OUT'
    ));

CREATE OR REPLACE FUNCTION enforce_item_stack_ledger_insert_integrity()
RETURNS TRIGGER AS $$
DECLARE
    previous_record RECORD;
    expected_payload_hash TEXT;
    expected_ledger_hash TEXT;
BEGIN
    PERFORM 1
    FROM item_stack
    WHERE item_stack_id = NEW.item_stack_id
    FOR UPDATE;

    SELECT
        ledger_sequence,
        quantity_after,
        stack_state_after,
        stack_version_after,
        stack_checksum_after,
        item_stack_ledger_hash
    INTO previous_record
    FROM item_stack_ledger
    WHERE item_stack_id = NEW.item_stack_id
    ORDER BY ledger_sequence DESC
    LIMIT 1;

    IF NOT FOUND THEN
        IF NEW.ledger_sequence <> 1
            OR NEW.quantity_before <> 0
            OR NEW.stack_state_before <> 'ABSENT'
            OR NEW.stack_version_before <> 0
            OR NEW.stack_checksum_before <> 'GENESIS'
            OR NEW.previous_item_stack_ledger_hash <> 'GENESIS'
        THEN
            RAISE EXCEPTION 'first item_stack_ledger row for % must start from GENESIS', NEW.item_stack_id
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.ledger_sequence <> previous_record.ledger_sequence + 1
            OR NEW.quantity_before <> previous_record.quantity_after
            OR NEW.stack_state_before <> previous_record.stack_state_after
            OR NEW.stack_version_before <> previous_record.stack_version_after
            OR NEW.stack_checksum_before <> previous_record.stack_checksum_after
            OR NEW.previous_item_stack_ledger_hash <> previous_record.item_stack_ledger_hash
        THEN
            RAISE EXCEPTION 'item_stack_ledger row for % does not extend the latest ledger row', NEW.item_stack_id
                USING ERRCODE = '23514';
        END IF;
    END IF;

    expected_payload_hash := compute_item_stack_ledger_payload_hash(
        NEW.settlement_step_id,
        NEW.item_stack_id,
        NEW.ledger_sequence,
        NEW.item_type_id,
        NEW.owner_id,
        NEW.station_id,
        NEW.entry_kind,
        NEW.quantity_delta,
        NEW.quantity_before,
        NEW.quantity_after,
        NEW.stack_state_before,
        NEW.stack_state_after,
        NEW.stack_version_before,
        NEW.stack_version_after,
        NEW.stack_checksum_before,
        NEW.stack_checksum_after
    );
    expected_ledger_hash := compute_item_stack_ledger_hash(
        NEW.previous_item_stack_ledger_hash,
        expected_payload_hash
    );

    IF NEW.ledger_payload_hash <> expected_payload_hash
        OR NEW.item_stack_ledger_hash <> expected_ledger_hash
    THEN
        RAISE EXCEPTION 'item_stack_ledger hash mismatch for % sequence %', NEW.item_stack_id, NEW.ledger_sequence
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION check_item_stack_ledger_projection_invariant(target_item_stack_id UUID)
RETURNS VOID AS $$
DECLARE
    stack_record RECORD;
    ledger_record RECORD;
BEGIN
    SELECT
        item_stack_id,
        owner_id,
        item_type_id,
        station_id,
        quantity,
        stack_state,
        stack_version,
        stack_checksum
    INTO stack_record
    FROM item_stack
    WHERE item_stack_id = target_item_stack_id;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT
        item_type_id,
        owner_id,
        station_id,
        quantity_after,
        stack_state_after,
        stack_version_after,
        stack_checksum_after
    INTO ledger_record
    FROM item_stack_ledger
    WHERE item_stack_id = target_item_stack_id
    ORDER BY ledger_sequence DESC
    LIMIT 1;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'item_stack % has no reconstructable item_stack_ledger rows', target_item_stack_id
            USING ERRCODE = '23514';
    END IF;

    IF stack_record.owner_id <> ledger_record.owner_id
        OR stack_record.item_type_id <> ledger_record.item_type_id
        OR stack_record.station_id <> ledger_record.station_id
        OR stack_record.quantity <> ledger_record.quantity_after
        OR stack_record.stack_state <> ledger_record.stack_state_after
        OR stack_record.stack_version <> ledger_record.stack_version_after
        OR stack_record.stack_checksum <> ledger_record.stack_checksum_after
    THEN
        RAISE EXCEPTION 'item_stack % projection does not match latest item_stack_ledger row', target_item_stack_id
            USING ERRCODE = '23514';
    END IF;

    RETURN;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION enforce_item_stack_ledger_projection_invariant()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM check_item_stack_ledger_projection_invariant(OLD.item_stack_id);
    ELSE
        PERFORM check_item_stack_ledger_projection_invariant(NEW.item_stack_id);
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS wallet_ledger_append_only_trigger ON wallet_ledger;
CREATE TRIGGER wallet_ledger_append_only_trigger
BEFORE UPDATE OR DELETE ON wallet_ledger
FOR EACH ROW
EXECUTE FUNCTION reject_ledger_mutation();

DROP TRIGGER IF EXISTS item_stack_ledger_append_only_trigger ON item_stack_ledger;
CREATE TRIGGER item_stack_ledger_append_only_trigger
BEFORE UPDATE OR DELETE ON item_stack_ledger
FOR EACH ROW
EXECUTE FUNCTION reject_ledger_mutation();

DROP TRIGGER IF EXISTS item_stack_ledger_insert_integrity_trigger ON item_stack_ledger;
CREATE TRIGGER item_stack_ledger_insert_integrity_trigger
BEFORE INSERT ON item_stack_ledger
FOR EACH ROW
EXECUTE FUNCTION enforce_item_stack_ledger_insert_integrity();

CREATE TABLE IF NOT EXISTS trade_state_change (
    trade_state_change_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_step_id UUID NOT NULL REFERENCES settlement_step(settlement_step_id),
    trade_instance_id UUID NOT NULL REFERENCES trade_instance(trade_instance_id),
    from_trade_state TEXT NOT NULL CHECK (from_trade_state IN ('OPEN', 'CANCELLED', 'COMPLETED')),
    to_trade_state TEXT NOT NULL CHECK (to_trade_state IN ('OPEN', 'CANCELLED', 'COMPLETED')),
    trade_state_change_kind TEXT NOT NULL CHECK (trade_state_change_kind IN (
        'ISSUED',
        'CANCELLED_BY_ISSUER',
        'ACCEPTED_BY_BUYER'
    )),
    changed_by_service TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS settlement_batch_idempotency_key_idx ON settlement_batch(idempotency_key);
CREATE INDEX IF NOT EXISTS settlement_step_batch_idx ON settlement_step(settlement_batch_id, step_index);
CREATE INDEX IF NOT EXISTS wallet_capsuleer_idx ON wallet(capsuleer_id, wallet_kind);
CREATE UNIQUE INDEX IF NOT EXISTS wallet_primary_capsuleer_unique_idx ON wallet(capsuleer_id) WHERE wallet_kind = 'PRIMARY';
CREATE INDEX IF NOT EXISTS wallet_ledger_wallet_idx ON wallet_ledger(wallet_id, created_at);
CREATE INDEX IF NOT EXISTS item_stack_owner_item_station_idx ON item_stack(owner_id, item_type_id, station_id, stack_state);
CREATE INDEX IF NOT EXISTS item_stack_ledger_stack_idx ON item_stack_ledger(item_stack_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS item_stack_ledger_stack_sequence_unique_idx ON item_stack_ledger(item_stack_id, ledger_sequence);
CREATE UNIQUE INDEX IF NOT EXISTS item_stack_ledger_stack_hash_unique_idx ON item_stack_ledger(item_stack_id, item_stack_ledger_hash);
CREATE INDEX IF NOT EXISTS trade_instance_lookup_idx ON trade_instance(item_type_id, station_id, trade_state);
CREATE INDEX IF NOT EXISTS trade_instance_open_expires_at_idx ON trade_instance(expires_at) WHERE trade_state = 'OPEN' AND expires_at IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS item_stack_escrow_active_trade_unique_idx ON item_stack_escrow(trade_instance_id) WHERE is_released = false;

CREATE OR REPLACE FUNCTION check_trade_remaining_quantity_invariant(target_trade_instance_id UUID)
RETURNS VOID AS $$
DECLARE
    stored_trade_state TEXT;
    stored_remaining_quantity BIGINT;
    active_escrow_quantity BIGINT;
BEGIN
    SELECT
        t.trade_state,
        t.remaining_quantity,
        COALESCE(SUM(e.quantity) FILTER (WHERE e.is_released = false), 0)::BIGINT
    INTO
        stored_trade_state,
        stored_remaining_quantity,
        active_escrow_quantity
    FROM trade_instance t
    LEFT JOIN item_stack_escrow e ON e.trade_instance_id = t.trade_instance_id
    WHERE t.trade_instance_id = target_trade_instance_id
    GROUP BY t.trade_state, t.remaining_quantity;

    IF stored_remaining_quantity IS NULL THEN
        RETURN;
    END IF;

    IF stored_remaining_quantity <> active_escrow_quantity THEN
        RAISE EXCEPTION 'trade_instance % remaining_quantity % does not match active item escrow quantity %',
            target_trade_instance_id,
            stored_remaining_quantity,
            active_escrow_quantity
            USING ERRCODE = '23514';
    END IF;

    IF stored_trade_state IN ('CANCELLED', 'COMPLETED') AND active_escrow_quantity <> 0 THEN
        RAISE EXCEPTION 'trade_instance % cannot be % while active item escrow quantity is %',
            target_trade_instance_id,
            stored_trade_state,
            active_escrow_quantity
            USING ERRCODE = '23514';
    END IF;

    RETURN;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION enforce_trade_remaining_quantity_invariant()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.trade_instance_id IS DISTINCT FROM NEW.trade_instance_id THEN
            PERFORM check_trade_remaining_quantity_invariant(OLD.trade_instance_id);
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        PERFORM check_trade_remaining_quantity_invariant(OLD.trade_instance_id);
    ELSE
        PERFORM check_trade_remaining_quantity_invariant(NEW.trade_instance_id);
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trade_instance_remaining_quantity_invariant_trigger ON trade_instance;
CREATE CONSTRAINT TRIGGER trade_instance_remaining_quantity_invariant_trigger
AFTER INSERT OR UPDATE ON trade_instance
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION enforce_trade_remaining_quantity_invariant();

DROP TRIGGER IF EXISTS item_stack_escrow_remaining_quantity_invariant_trigger ON item_stack_escrow;
CREATE CONSTRAINT TRIGGER item_stack_escrow_remaining_quantity_invariant_trigger
AFTER INSERT OR UPDATE OR DELETE ON item_stack_escrow
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION enforce_trade_remaining_quantity_invariant();

DROP TRIGGER IF EXISTS item_stack_projection_ledger_invariant_trigger ON item_stack;
CREATE CONSTRAINT TRIGGER item_stack_projection_ledger_invariant_trigger
AFTER INSERT OR UPDATE ON item_stack
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION enforce_item_stack_ledger_projection_invariant();

DROP TRIGGER IF EXISTS item_stack_ledger_projection_invariant_trigger ON item_stack_ledger;
CREATE CONSTRAINT TRIGGER item_stack_ledger_projection_invariant_trigger
AFTER INSERT ON item_stack_ledger
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION enforce_item_stack_ledger_projection_invariant();

CREATE OR REPLACE FUNCTION check_trade_wallet_escrow_closed_state_invariant(target_trade_instance_id UUID)
RETURNS VOID AS $$
DECLARE
    stored_trade_state TEXT;
    active_wallet_escrow_isk BIGINT;
BEGIN
    SELECT
        t.trade_state,
        COALESCE(SUM(w.isk_amount) FILTER (WHERE w.is_released = false), 0)::BIGINT
    INTO
        stored_trade_state,
        active_wallet_escrow_isk
    FROM trade_instance t
    LEFT JOIN wallet_escrow w ON w.trade_instance_id = t.trade_instance_id
    WHERE t.trade_instance_id = target_trade_instance_id
    GROUP BY t.trade_state;

    IF stored_trade_state IS NULL THEN
        RETURN;
    END IF;

    IF stored_trade_state IN ('CANCELLED', 'COMPLETED') AND active_wallet_escrow_isk <> 0 THEN
        RAISE EXCEPTION 'trade_instance % cannot be % while active wallet escrow amount is %',
            target_trade_instance_id,
            stored_trade_state,
            active_wallet_escrow_isk
            USING ERRCODE = '23514';
    END IF;

    RETURN;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION enforce_trade_wallet_escrow_closed_state_invariant()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.trade_instance_id IS DISTINCT FROM NEW.trade_instance_id THEN
            PERFORM check_trade_wallet_escrow_closed_state_invariant(OLD.trade_instance_id);
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        PERFORM check_trade_wallet_escrow_closed_state_invariant(OLD.trade_instance_id);
    ELSE
        PERFORM check_trade_wallet_escrow_closed_state_invariant(NEW.trade_instance_id);
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trade_instance_wallet_escrow_closed_state_trigger ON trade_instance;
CREATE CONSTRAINT TRIGGER trade_instance_wallet_escrow_closed_state_trigger
AFTER INSERT OR UPDATE ON trade_instance
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION enforce_trade_wallet_escrow_closed_state_invariant();

DROP TRIGGER IF EXISTS wallet_escrow_closed_trade_invariant_trigger ON wallet_escrow;
CREATE CONSTRAINT TRIGGER wallet_escrow_closed_trade_invariant_trigger
AFTER INSERT OR UPDATE OR DELETE ON wallet_escrow
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION enforce_trade_wallet_escrow_closed_state_invariant();
