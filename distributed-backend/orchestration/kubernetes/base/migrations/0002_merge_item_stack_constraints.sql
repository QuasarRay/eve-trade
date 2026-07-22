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
        'TRANSFER_TO_ESCROW',
        'TRANSFER_FROM_ESCROW_TO_NEW_OWNER',
        'TRANSFER_FROM_ESCROW_TO_PREVIOUS_OWNER',
        'MERGE_IN',
        'MERGE_OUT'
    ));

CREATE OR REPLACE FUNCTION reject_ledger_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'ledger tables are append-only; write a new ledger row instead'
        USING ERRCODE = '23514';
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
