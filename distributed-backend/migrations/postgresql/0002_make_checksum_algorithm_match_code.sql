-- Optional patch if you use the migration generated earlier in this conversation.
-- The earlier conceptual migration used `blake3-v1` as the default checksum algorithm,
-- but this Rust package deliberately uses SHA-256 to avoid adding native BLAKE3
-- dependency complexity during the portfolio stage.

BEGIN;

ALTER TABLE trade.wallet
  ALTER COLUMN checksum_algorithm SET DEFAULT 'sha256-v1';

ALTER TABLE trade.item_stack
  ALTER COLUMN checksum_algorithm SET DEFAULT 'sha256-v1';

ALTER TABLE trade.item_instance
  ALTER COLUMN checksum_algorithm SET DEFAULT 'sha256-v1';

COMMIT;
