INSERT INTO capsuleer (capsuleer_id, capsuleer_name)
VALUES
    (1001, 'Seller'),
    (2002, 'Buyer'),
    (3003, 'Other'),
    (4004, 'Outsider')
ON CONFLICT DO NOTHING;

INSERT INTO region (region_id, region_name)
VALUES (10000002, 'The Forge')
ON CONFLICT DO NOTHING;

INSERT INTO station (station_id, region_id, station_name)
VALUES
    (60003760, 10000002, 'Jita IV - Moon 4'),
    (60008494, 10000002, 'Perimeter II')
ON CONFLICT DO NOTHING;

INSERT INTO item_type (item_type_id, item_type_name, category_name, group_name)
VALUES
    (34, 'Tritanium', 'Material', 'Mineral'),
    (35, 'Pyerite', 'Material', 'Mineral')
ON CONFLICT DO NOTHING;

INSERT INTO wallet (
    wallet_id,
    capsuleer_id,
    wallet_kind,
    isk_amount,
    wallet_state,
    wallet_version,
    wallet_checksum,
    checksum_algorithm
)
SELECT
    wallet_id,
    capsuleer_id,
    'PRIMARY',
    isk_amount,
    'ACTIVE',
    wallet_version,
    encode(digest('wallet:' || wallet_id::text || ':' || isk_amount::text || ':' || wallet_version::text, 'sha256'), 'hex'),
    'sha256-v1'
FROM (
    VALUES
        ('00000000-0000-4000-8000-000000001001'::uuid, 1001::bigint, 1000000::bigint, 1::bigint),
        ('00000000-0000-4000-8000-000000002002'::uuid, 2002::bigint, 1000000::bigint, 1::bigint),
        ('00000000-0000-4000-8000-000000003003'::uuid, 3003::bigint, 1000000::bigint, 1::bigint),
        ('00000000-0000-4000-8000-000000004004'::uuid, 4004::bigint, 1000000::bigint, 1::bigint)
) AS seed(wallet_id, capsuleer_id, isk_amount, wallet_version)
ON CONFLICT DO NOTHING;

BEGIN;

INSERT INTO item_stack (
    item_stack_id,
    owner_id,
    item_type_id,
    station_id,
    quantity,
    stack_state,
    stack_version,
    stack_checksum,
    checksum_algorithm
)
SELECT
    item_stack_id,
    owner_id,
    item_type_id,
    station_id,
    quantity,
    'ACTIVE',
    stack_version,
    encode(digest('item_stack:' || item_stack_id::text || ':' || quantity::text || ':' || stack_version::text, 'sha256'), 'hex'),
    'sha256-v1'
FROM (
    VALUES
        ('11111111-1111-4111-8111-111111111111'::uuid, 1001::bigint, 34::bigint, 60003760::bigint, 100::bigint, 1::bigint),
        ('22222222-2222-4222-8222-222222222222'::uuid, 1001::bigint, 35::bigint, 60003760::bigint, 50::bigint, 1::bigint),
        ('33333333-3333-4333-8333-333333333333'::uuid, 2002::bigint, 34::bigint, 60003760::bigint, 5::bigint, 1::bigint),
        ('44444444-4444-4444-8444-444444444444'::uuid, 3003::bigint, 34::bigint, 60003760::bigint, 25::bigint, 1::bigint)
) AS seed(item_stack_id, owner_id, item_type_id, station_id, quantity, stack_version)
ON CONFLICT DO NOTHING;

INSERT INTO idempotency_record (
    idempotency_key,
    request_fingerprint,
    request_kind,
    idempotency_state,
    created_by_service,
    completed_at
)
VALUES (
    'local-dev-world-item-stack-seed',
    encode(digest('local-dev-world-item-stack-seed-v1', 'sha256'), 'hex'),
    'local_dev_world.seed_item_stacks',
    'COMPLETED',
    'local-dev-seed',
    now()
)
ON CONFLICT DO NOTHING;

INSERT INTO request_attempt (
    request_id,
    idempotency_key,
    attempt_number,
    received_by_service,
    attempt_state,
    completed_at
)
VALUES (
    '99999999-0000-4000-8000-000000000001'::uuid,
    'local-dev-world-item-stack-seed',
    1,
    'local-dev-seed',
    'COMPLETED',
    now()
)
ON CONFLICT DO NOTHING;

INSERT INTO settlement_batch (
    settlement_batch_id,
    request_id,
    idempotency_key,
    external_request_id,
    batch_state,
    created_by_service,
    completed_at
)
VALUES (
    '99999999-0000-4000-8000-000000000002'::uuid,
    '99999999-0000-4000-8000-000000000001'::uuid,
    'local-dev-world-item-stack-seed',
    'local-dev-world-item-stack-seed',
    'COMPLETED',
    'local-dev-seed',
    now()
)
ON CONFLICT DO NOTHING;

INSERT INTO settlement_step (
    settlement_step_id,
    settlement_batch_id,
    step_index,
    step_kind,
    step_payload,
    step_payload_hash,
    step_output,
    step_state,
    started_at,
    completed_at
)
VALUES (
    '99999999-0000-4000-8000-000000000003'::uuid,
    '99999999-0000-4000-8000-000000000002'::uuid,
    0,
    'local_dev_world.seed_item_stack_ledgers',
    '{"source":"local_dev_world.sql"}'::jsonb,
    encode(digest('local_dev_world.seed_item_stack_ledgers', 'sha256'), 'hex'),
    '{}'::jsonb,
    'COMPLETED',
    now(),
    now()
)
ON CONFLICT DO NOTHING;

UPDATE idempotency_record
SET result_settlement_batch_id = '99999999-0000-4000-8000-000000000002'::uuid
WHERE idempotency_key = 'local-dev-world-item-stack-seed'
  AND result_settlement_batch_id IS NULL;

WITH seed_ledgers AS (
    SELECT
        '99999999-0000-4000-8000-000000000003'::uuid AS settlement_step_id,
        item_stack_id,
        stack_version AS ledger_sequence,
        item_type_id,
        owner_id,
        station_id,
        'CREATE_STACK'::text AS entry_kind,
        quantity AS quantity_delta,
        0::bigint AS quantity_before,
        quantity AS quantity_after,
        'ABSENT'::text AS stack_state_before,
        stack_state AS stack_state_after,
        0::bigint AS stack_version_before,
        stack_version AS stack_version_after,
        'GENESIS'::text AS stack_checksum_before,
        stack_checksum AS stack_checksum_after
    FROM item_stack
    WHERE item_stack_id IN (
        '11111111-1111-4111-8111-111111111111'::uuid,
        '22222222-2222-4222-8222-222222222222'::uuid,
        '33333333-3333-4333-8333-333333333333'::uuid,
        '44444444-4444-4444-8444-444444444444'::uuid
    )
      AND NOT EXISTS (
          SELECT 1
          FROM item_stack_ledger existing
          WHERE existing.item_stack_id = item_stack.item_stack_id
      )
),
hashed AS (
    SELECT
        seed_ledgers.*,
        compute_item_stack_ledger_payload_hash(
            settlement_step_id,
            item_stack_id,
            ledger_sequence,
            item_type_id,
            owner_id,
            station_id,
            entry_kind,
            quantity_delta,
            quantity_before,
            quantity_after,
            stack_state_before,
            stack_state_after,
            stack_version_before,
            stack_version_after,
            stack_checksum_before,
            stack_checksum_after
        ) AS ledger_payload_hash
    FROM seed_ledgers
)
INSERT INTO item_stack_ledger (
    settlement_step_id,
    item_stack_id,
    ledger_sequence,
    previous_item_stack_ledger_hash,
    ledger_payload_hash,
    item_stack_ledger_hash,
    item_type_id,
    owner_id,
    station_id,
    entry_kind,
    quantity_delta,
    quantity_before,
    quantity_after,
    stack_state_before,
    stack_state_after,
    stack_version_before,
    stack_version_after,
    stack_checksum_before,
    stack_checksum_after
)
SELECT
    settlement_step_id,
    item_stack_id,
    ledger_sequence,
    'GENESIS',
    ledger_payload_hash,
    compute_item_stack_ledger_hash('GENESIS', ledger_payload_hash),
    item_type_id,
    owner_id,
    station_id,
    entry_kind,
    quantity_delta,
    quantity_before,
    quantity_after,
    stack_state_before,
    stack_state_after,
    stack_version_before,
    stack_version_after,
    stack_checksum_before,
    stack_checksum_after
FROM hashed;

COMMIT;
