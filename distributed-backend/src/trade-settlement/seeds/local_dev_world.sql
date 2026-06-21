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
