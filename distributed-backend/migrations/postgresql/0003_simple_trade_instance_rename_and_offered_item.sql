-- 0003_simple_trade_instance_rename_and_offered_item.sql
--
-- What changed:
-- - Renames trade.trade_order into trade.trade_instance.
-- - Renames the primary key from trade_order_id into trade_instance_id.
-- - Replaces offered_item_stack_id/offered_item_instance_id with one simple offered_item column.
-- - Makes offered_item a foreign key to exactly one trade.item_stack row.
-- - Renames item_stack.capsuleer_id into item_stack.owner_id so ownership can temporarily point at a trade_instance.
-- - Adds a small trigger so a stack offered by a trade_instance becomes owned by that trade_instance.
--
-- What this deliberately does NOT do:
-- - No offered-item arrays.
-- - No join tables.
-- - No compatibility view named trade.trade_order.
-- - No polymorphic owner_kind enum.
-- - No item_instance migration yet.

BEGIN;

-- This block renames the core durable trade aggregate to the lifecycle name used by the latest proto files.
ALTER TABLE trade.trade_order
    RENAME TO trade_instance;

-- This block renames the aggregate primary-key column so the database no longer speaks in trade_order names.
ALTER TABLE trade.trade_instance
    RENAME COLUMN trade_order_id TO trade_instance_id;

-- This block drops old trade_order-named constraints before replacing them with trade_instance-named constraints.
ALTER TABLE trade.trade_instance
    DROP CONSTRAINT IF EXISTS trade_order_state_allowed,
    DROP CONSTRAINT IF EXISTS trade_order_total_positive,
    DROP CONSTRAINT IF EXISTS trade_order_remaining_not_more_than_total,
    DROP CONSTRAINT IF EXISTS trade_order_unit_price_positive,
    DROP CONSTRAINT IF EXISTS trade_order_not_both_stack_and_instance,
    DROP CONSTRAINT IF EXISTS trade_order_sell_has_offer;

-- This block simplifies the offered item model to exactly one item_stack reference.
ALTER TABLE trade.trade_instance
    RENAME COLUMN offered_item_stack_id TO offered_item;

-- This block removes the old unique-item side path from trade_instance.
-- Unique items are now represented as item_stack rows with quantity 1 and item_type.is_unique = true.
ALTER TABLE trade.trade_instance
    DROP COLUMN IF EXISTS offered_item_instance_id;

-- This block adds the item-type flag needed by the simplified single-stack model.
-- A later service/game-server rule can prevent split/merge for rows where is_unique = true.
ALTER TABLE trade.item_type
    ADD COLUMN IF NOT EXISTS is_unique BOOLEAN NOT NULL DEFAULT false;

-- This block drops the capsuleer-only ownership foreign key from item_stack.
-- owner_id must be able to hold either a capsuleer_id or a trade_instance_id during the simple lifecycle.
ALTER TABLE trade.item_stack
    DROP CONSTRAINT IF EXISTS item_stack_capsuleer_id_fkey;

-- This block renames item_stack ownership to the simple neutral name required by the lifecycle.
ALTER TABLE trade.item_stack
    RENAME COLUMN capsuleer_id TO owner_id;

-- This block adds the simplified offered_item foreign key.
ALTER TABLE trade.trade_instance
    ADD CONSTRAINT trade_instance_offered_item_fkey
        FOREIGN KEY (offered_item)
        REFERENCES trade.item_stack(item_stack_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT;

-- This block restores the trade_instance structural constraints with the new names and simplified offered_item column.
ALTER TABLE trade.trade_instance
    ADD CONSTRAINT trade_instance_state_allowed
        CHECK (state IN ('being_created', 'outstanding', 'completed', 'expired', 'cancelled', 'failed')),
    ADD CONSTRAINT trade_instance_total_positive
        CHECK (total_quantity > 0),
    ADD CONSTRAINT trade_instance_remaining_not_more_than_total
        CHECK (remaining_quantity <= total_quantity),
    ADD CONSTRAINT trade_instance_unit_price_positive
        CHECK (unit_price_isk > 0),
    ADD CONSTRAINT trade_instance_sell_has_offer
        CHECK (order_side <> 'sell_order' OR offered_item IS NOT NULL);

-- This block renames dependent foreign-key columns so every direct reference uses trade_instance_id.
ALTER TABLE trade.wallet_reservation
    RENAME COLUMN trade_order_id TO trade_instance_id;

ALTER TABLE trade.item_stack_reservation
    RENAME COLUMN trade_order_id TO trade_instance_id;

ALTER TABLE trade.item_instance_reservation
    RENAME COLUMN trade_order_id TO trade_instance_id;

ALTER TABLE trade.trade_transaction
    RENAME COLUMN trade_order_id TO trade_instance_id;

ALTER TABLE trade.trade_state_change
    RENAME COLUMN trade_order_id TO trade_instance_id;

ALTER TABLE trade.idempotency_result
    RENAME COLUMN trade_order_id TO trade_instance_id;

-- This block renames indexes that mention trade_order so schema navigation matches the new name.
ALTER INDEX IF EXISTS trade.trade_order_pkey RENAME TO trade_instance_pkey;
ALTER INDEX IF EXISTS trade.idx_trade_order_listing RENAME TO idx_trade_instance_listing;
ALTER INDEX IF EXISTS trade.idx_trade_order_expires_at RENAME TO idx_trade_instance_expires_at;
ALTER INDEX IF EXISTS trade.idx_trade_order_owner_capsuleer RENAME TO idx_trade_instance_owner_capsuleer;
ALTER INDEX IF EXISTS trade.idx_trade_order_owner_wallet RENAME TO idx_trade_instance_owner_wallet;
ALTER INDEX IF EXISTS trade.idx_trade_transaction_order RENAME TO idx_trade_transaction_instance;
ALTER INDEX IF EXISTS trade.idx_trade_state_change_order_time RENAME TO idx_trade_state_change_instance_time;

-- This block creates one small database rule for the simple ownership cycle.
-- When a trade_instance has an offered_item, that item_stack.owner_id becomes the trade_instance_id.
CREATE OR REPLACE FUNCTION trade.assign_offered_item_to_trade_instance()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.offered_item IS NOT NULL THEN
        UPDATE trade.item_stack
        SET owner_id = NEW.trade_instance_id,
            updated_at = now()
        WHERE item_stack_id = NEW.offered_item;
    END IF;

    RETURN NEW;
END;
$$;

-- This block connects the ownership rule to trade_instance creation and offered_item changes.
DROP TRIGGER IF EXISTS trade_instance_assigns_offered_item_owner ON trade.trade_instance;

CREATE TRIGGER trade_instance_assigns_offered_item_owner
AFTER INSERT OR UPDATE OF offered_item
ON trade.trade_instance
FOR EACH ROW
EXECUTE FUNCTION trade.assign_offered_item_to_trade_instance();

-- This block backfills ownership for rows that existed before the migration.
UPDATE trade.item_stack AS stack
SET owner_id = instance.trade_instance_id,
    updated_at = now()
FROM trade.trade_instance AS instance
WHERE instance.offered_item IS NOT NULL
  AND stack.item_stack_id = instance.offered_item;

COMMIT;
