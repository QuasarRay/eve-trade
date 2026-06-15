use chrono::{DateTime, Utc};
use sqlx::PgPool;
use uuid::Uuid;

use crate::error::SettlementError;

use super::{
    support::{
        ensure_not_blank, ensure_positive, item_stack_checksum, ordered_pair, tx_conn, DbTx,
        CHECKSUM_ALGORITHM,
    },
    types::{
        CreateNewEmptyItemStackInput, ItemStackEscrowRow, ItemStackEscrowTransferResult,
        ItemStackMergeResult, ItemStackRow,
        MergeItemStacksWithIdenticalItemTypeAndIdenticalOwnerInput,
        TransferQuantityFromItemStackEscrowToItemStackWithNewOwnerInput,
        TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwnerInput,
        TransferQuantityFromItemStackToItemStackEscrowInput,
    },
};

pub(crate) async fn create_new_empty_item_stack(
    pool: &PgPool,
    input: CreateNewEmptyItemStackInput,
) -> Result<ItemStackRow, SettlementError> {
    let checksum = item_stack_checksum(
        input.item_stack_id,
        input.owner_id,
        input.item_type_id,
        input.station_id,
        0,
        0,
    );

    let mut tx = pool.begin().await?;
    let row = sqlx::query_as::<_, ItemStackRow>(
        r#"
        INSERT INTO item_stack (
            item_stack_id, owner_id, item_type_id, station_id, quantity, stack_state,
            stack_version, stack_checksum, checksum_algorithm, created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, 0, 'active', 0, $5, $6, $7, $7)
        RETURNING item_stack_id, owner_id, item_type_id, station_id,
                  (SELECT region_id FROM station WHERE station.station_id = item_stack.station_id) AS region_id,
                  quantity, stack_state, stack_version, stack_checksum
        "#,
    )
    .bind(input.item_stack_id)
    .bind(input.owner_id)
    .bind(input.item_type_id)
    .bind(input.station_id)
    .bind(checksum)
    .bind(CHECKSUM_ALGORITHM)
    .bind(input.created_at)
    .fetch_one(tx_conn(&mut tx))
    .await?;
    tx.commit().await?;
    Ok(row)
}

pub(crate) async fn transfer_quantity_from_item_stack_to_item_stack_escrow(
    pool: &PgPool,
    input: TransferQuantityFromItemStackToItemStackEscrowInput,
) -> Result<ItemStackEscrowTransferResult, SettlementError> {
    ensure_positive(input.quantity, "quantity")?;

    let mut tx = pool.begin().await?;
    insert_item_stack_operation(
        &mut tx,
        input.item_stack_operation_id,
        input.operation_id,
        &input.operation_kind,
        input.created_at,
    )
    .await?;

    let source = lock_item_stack(&mut tx, input.source_item_stack_id).await?;
    if source.owner_id != input.issuer_id {
        return Err(SettlementError::InvalidRequest(
            "source item stack owner must match issuer_id".to_string(),
        ));
    }
    let item_stack = mutate_locked_item_stack(
        &mut tx,
        source,
        input.item_stack_operation_id,
        -input.quantity,
        "item_stack_to_escrow_debit",
        input.created_at,
    )
    .await?;

    let item_stack_escrow = sqlx::query_as::<_, ItemStackEscrowRow>(
        r#"
        INSERT INTO item_stack_escrow (
            item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
            created_at, updated_at, escrow_state, source_item_stack_id
        )
        VALUES ($1, $2, $3, $4, $5, $5, 'held', $6)
        RETURNING item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
                  created_at, updated_at, released_at, escrow_state, release_reason,
                  source_item_stack_id
        "#,
    )
    .bind(input.item_stack_escrow_id)
    .bind(input.issuer_id)
    .bind(input.trade_instance_id)
    .bind(input.quantity)
    .bind(input.created_at)
    .bind(input.source_item_stack_id)
    .fetch_one(tx_conn(&mut tx))
    .await?;

    tx.commit().await?;
    Ok(ItemStackEscrowTransferResult {
        item_stack_operation_id: input.item_stack_operation_id,
        item_stack,
        item_stack_escrow,
    })
}

pub(crate) async fn transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner(
    pool: &PgPool,
    input: TransferQuantityFromItemStackEscrowToItemStackWithNewOwnerInput,
) -> Result<ItemStackEscrowTransferResult, SettlementError> {
    ensure_positive(input.quantity, "quantity")?;

    let mut tx = pool.begin().await?;
    insert_item_stack_operation(
        &mut tx,
        input.item_stack_operation_id,
        input.operation_id,
        &input.operation_kind,
        input.transferred_at,
    )
    .await?;

    let escrow = lock_item_stack_escrow(&mut tx, input.item_stack_escrow_id).await?;
    ensure_escrow_can_debit(&escrow, input.quantity)?;
    let source = lock_item_stack(&mut tx, escrow.source_item_stack_id).await?;
    let destination = lock_or_create_item_stack(
        &mut tx,
        input.destination_item_stack_id,
        input.new_owner_id,
        source.item_type_id,
        source.station_id,
        input.transferred_at,
    )
    .await?;
    let item_stack = mutate_locked_item_stack(
        &mut tx,
        destination,
        input.item_stack_operation_id,
        input.quantity,
        "escrow_to_new_owner_credit",
        input.transferred_at,
    )
    .await?;
    let item_stack_escrow = debit_item_stack_escrow(
        &mut tx,
        input.item_stack_escrow_id,
        input.quantity,
        "used",
        None,
        input.transferred_at,
    )
    .await?;

    tx.commit().await?;
    Ok(ItemStackEscrowTransferResult {
        item_stack_operation_id: input.item_stack_operation_id,
        item_stack,
        item_stack_escrow,
    })
}

pub(crate) async fn transfer_quantity_from_item_stack_escrow_to_item_stack_with_previous_owner(
    pool: &PgPool,
    input: TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwnerInput,
) -> Result<ItemStackEscrowTransferResult, SettlementError> {
    ensure_positive(input.quantity, "quantity")?;
    ensure_not_blank(&input.release_reason, "release_reason")?;

    let mut tx = pool.begin().await?;
    insert_item_stack_operation(
        &mut tx,
        input.item_stack_operation_id,
        input.operation_id,
        &input.operation_kind,
        input.transferred_at,
    )
    .await?;

    let escrow = lock_item_stack_escrow(&mut tx, input.item_stack_escrow_id).await?;
    ensure_escrow_can_debit(&escrow, input.quantity)?;
    let source = lock_item_stack(&mut tx, escrow.source_item_stack_id).await?;
    if source.owner_id != escrow.issuer_id {
        return Err(SettlementError::InvalidRequest(
            "source item stack owner must match item escrow issuer".to_string(),
        ));
    }
    let item_stack = mutate_locked_item_stack(
        &mut tx,
        source,
        input.item_stack_operation_id,
        input.quantity,
        "escrow_to_previous_owner_credit",
        input.transferred_at,
    )
    .await?;
    let item_stack_escrow = debit_item_stack_escrow(
        &mut tx,
        input.item_stack_escrow_id,
        input.quantity,
        "released",
        Some(&input.release_reason),
        input.transferred_at,
    )
    .await?;

    tx.commit().await?;
    Ok(ItemStackEscrowTransferResult {
        item_stack_operation_id: input.item_stack_operation_id,
        item_stack,
        item_stack_escrow,
    })
}

pub(crate) async fn merge_item_stacks_with_identical_item_type_and_identical_owner(
    pool: &PgPool,
    input: MergeItemStacksWithIdenticalItemTypeAndIdenticalOwnerInput,
) -> Result<ItemStackMergeResult, SettlementError> {
    if input.source_item_stack_id == input.target_item_stack_id {
        return Err(SettlementError::InvalidRequest(
            "source and target item stacks must be different".to_string(),
        ));
    }

    let mut tx = pool.begin().await?;
    insert_item_stack_operation(
        &mut tx,
        input.item_stack_operation_id,
        input.operation_id,
        &input.operation_kind,
        input.merged_at,
    )
    .await?;

    let (first_id, second_id) =
        ordered_pair(input.source_item_stack_id, input.target_item_stack_id);
    let first = lock_item_stack(&mut tx, first_id).await?;
    let second = lock_item_stack(&mut tx, second_id).await?;
    let (source, target) = if first.item_stack_id == input.source_item_stack_id {
        (first, second)
    } else {
        (second, first)
    };

    if source.owner_id != target.owner_id
        || source.item_type_id != target.item_type_id
        || source.station_id != target.station_id
    {
        return Err(SettlementError::InvalidRequest(
            "item stacks must have identical owner, item type, and station".to_string(),
        ));
    }

    let quantity = source.quantity;
    let target_item_stack = mutate_locked_item_stack(
        &mut tx,
        target,
        input.item_stack_operation_id,
        quantity,
        "item_stack_merge_credit",
        input.merged_at,
    )
    .await?;
    let source_item_stack = mutate_locked_item_stack(
        &mut tx,
        source,
        input.item_stack_operation_id,
        -quantity,
        "item_stack_merge_debit",
        input.merged_at,
    )
    .await?;

    tx.commit().await?;
    Ok(ItemStackMergeResult {
        item_stack_operation_id: input.item_stack_operation_id,
        source_item_stack,
        target_item_stack,
    })
}

async fn insert_item_stack_operation(
    tx: &mut DbTx<'_>,
    item_stack_operation_id: Uuid,
    operation_id: Uuid,
    operation_kind: &str,
    at: DateTime<Utc>,
) -> Result<(), SettlementError> {
    ensure_not_blank(operation_kind, "operation_kind")?;
    sqlx::query(
        r#"
        INSERT INTO item_stack_operation (
            item_stack_operation_id, operation_id, operation_kind,
            item_stack_operation_state, created_at, completed_at
        )
        VALUES ($1, $2, $3, 'completed', $4, $4)
        "#,
    )
    .bind(item_stack_operation_id)
    .bind(operation_id)
    .bind(operation_kind)
    .bind(at)
    .execute(tx_conn(tx))
    .await?;
    Ok(())
}

async fn lock_item_stack(
    tx: &mut DbTx<'_>,
    item_stack_id: Uuid,
) -> Result<ItemStackRow, SettlementError> {
    Ok(sqlx::query_as::<_, ItemStackRow>(
        r#"
        SELECT s.item_stack_id, s.owner_id, s.item_type_id, s.station_id, st.region_id,
               s.quantity, s.stack_state, s.stack_version, s.stack_checksum
        FROM item_stack s
        JOIN station st ON st.station_id = s.station_id
        WHERE s.item_stack_id = $1
        FOR UPDATE
        "#,
    )
    .bind(item_stack_id)
    .fetch_one(tx_conn(tx))
    .await?)
}

async fn lock_or_create_item_stack(
    tx: &mut DbTx<'_>,
    item_stack_id: Uuid,
    owner_id: i64,
    item_type_id: i64,
    station_id: i64,
    created_at: DateTime<Utc>,
) -> Result<ItemStackRow, SettlementError> {
    if let Some(row) = sqlx::query_as::<_, ItemStackRow>(
        r#"
        SELECT s.item_stack_id, s.owner_id, s.item_type_id, s.station_id, st.region_id,
               s.quantity, s.stack_state, s.stack_version, s.stack_checksum
        FROM item_stack s
        JOIN station st ON st.station_id = s.station_id
        WHERE s.item_stack_id = $1
        FOR UPDATE
        "#,
    )
    .bind(item_stack_id)
    .fetch_optional(tx_conn(tx))
    .await?
    {
        if row.owner_id != owner_id
            || row.item_type_id != item_type_id
            || row.station_id != station_id
        {
            return Err(SettlementError::InvalidRequest(
                "existing destination item stack does not match requested owner, item type, and station"
                    .to_string(),
            ));
        }
        return Ok(row);
    }

    let checksum = item_stack_checksum(item_stack_id, owner_id, item_type_id, station_id, 0, 0);
    sqlx::query(
        r#"
        INSERT INTO item_stack (
            item_stack_id, owner_id, item_type_id, station_id, quantity, stack_state,
            stack_version, stack_checksum, checksum_algorithm, created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, 0, 'active', 0, $5, $6, $7, $7)
        "#,
    )
    .bind(item_stack_id)
    .bind(owner_id)
    .bind(item_type_id)
    .bind(station_id)
    .bind(checksum)
    .bind(CHECKSUM_ALGORITHM)
    .bind(created_at)
    .execute(tx_conn(tx))
    .await?;

    lock_item_stack(tx, item_stack_id).await
}

async fn lock_item_stack_escrow(
    tx: &mut DbTx<'_>,
    item_stack_escrow_id: Uuid,
) -> Result<ItemStackEscrowRow, SettlementError> {
    Ok(sqlx::query_as::<_, ItemStackEscrowRow>(
        r#"
        SELECT item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
               created_at, updated_at, released_at, escrow_state, release_reason,
               source_item_stack_id
        FROM item_stack_escrow
        WHERE item_stack_escrow_id = $1
        FOR UPDATE
        "#,
    )
    .bind(item_stack_escrow_id)
    .fetch_one(tx_conn(tx))
    .await?)
}

async fn mutate_locked_item_stack(
    tx: &mut DbTx<'_>,
    before: ItemStackRow,
    item_stack_operation_id: Uuid,
    delta_quantity: i64,
    entry_kind: &str,
    at: DateTime<Utc>,
) -> Result<ItemStackRow, SettlementError> {
    ensure_not_blank(entry_kind, "entry_kind")?;
    let after_quantity = before
        .quantity
        .checked_add(delta_quantity)
        .ok_or_else(|| SettlementError::DatabaseConflict("item quantity overflow".to_string()))?;
    if after_quantity < 0 {
        return Err(SettlementError::InsufficientItems {
            item_stack_id: before.item_stack_id.to_string(),
        });
    }
    let after_version = before
        .stack_version
        .checked_add(1)
        .ok_or_else(|| SettlementError::DatabaseConflict("item version overflow".to_string()))?;
    let after_checksum = item_stack_checksum(
        before.item_stack_id,
        before.owner_id,
        before.item_type_id,
        before.station_id,
        after_quantity,
        after_version,
    );
    let stack_state = if after_quantity == 0 {
        "depleted"
    } else {
        "active"
    };

    let row = sqlx::query_as::<_, ItemStackRow>(
        r#"
        UPDATE item_stack
        SET quantity = $2, stack_state = $3, stack_version = $4,
            stack_checksum = $5, checksum_algorithm = $6, updated_at = $7
        WHERE item_stack_id = $1
        RETURNING item_stack_id, owner_id, item_type_id, station_id,
                  (SELECT region_id FROM station WHERE station.station_id = item_stack.station_id) AS region_id,
                  quantity, stack_state, stack_version, stack_checksum
        "#,
    )
    .bind(before.item_stack_id)
    .bind(after_quantity)
    .bind(stack_state)
    .bind(after_version)
    .bind(&after_checksum)
    .bind(CHECKSUM_ALGORITHM)
    .bind(at)
    .fetch_one(tx_conn(tx))
    .await?;

    sqlx::query(
        r#"
        INSERT INTO item_stack_ledger (
            item_stack_ledger_id, item_stack_operation_id, item_stack_id, item_type_id,
            owner_id, station_id, entry_kind, quantity_delta, quantity_before,
            quantity_after, stack_version_before, stack_version_after,
            stack_checksum_before, stack_checksum_after, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        "#,
    )
    .bind(Uuid::new_v4())
    .bind(item_stack_operation_id)
    .bind(before.item_stack_id)
    .bind(before.item_type_id)
    .bind(before.owner_id)
    .bind(before.station_id)
    .bind(entry_kind)
    .bind(delta_quantity)
    .bind(before.quantity)
    .bind(after_quantity)
    .bind(before.stack_version)
    .bind(after_version)
    .bind(&before.stack_checksum)
    .bind(&after_checksum)
    .bind(at)
    .execute(tx_conn(tx))
    .await?;

    Ok(row)
}

async fn debit_item_stack_escrow(
    tx: &mut DbTx<'_>,
    item_stack_escrow_id: Uuid,
    quantity: i64,
    empty_state: &str,
    release_reason: Option<&str>,
    at: DateTime<Utc>,
) -> Result<ItemStackEscrowRow, SettlementError> {
    let row = sqlx::query_as::<_, ItemStackEscrowRow>(
        r#"
        UPDATE item_stack_escrow
        SET quantity = quantity - $2,
            escrow_state = CASE WHEN quantity - $2 = 0 THEN $3 ELSE 'partially_used' END,
            release_reason = CASE WHEN quantity - $2 = 0 THEN $4 ELSE release_reason END,
            released_at = CASE WHEN quantity - $2 = 0 THEN $5 ELSE released_at END,
            updated_at = $5
        WHERE item_stack_escrow_id = $1
          AND quantity >= $2
        RETURNING item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
                  created_at, updated_at, released_at, escrow_state, release_reason,
                  source_item_stack_id
        "#,
    )
    .bind(item_stack_escrow_id)
    .bind(quantity)
    .bind(empty_state)
    .bind(release_reason)
    .bind(at)
    .fetch_one(tx_conn(tx))
    .await?;
    Ok(row)
}

fn ensure_escrow_can_debit(
    escrow: &ItemStackEscrowRow,
    quantity: i64,
) -> Result<(), SettlementError> {
    if escrow.quantity < quantity {
        return Err(SettlementError::InsufficientItems {
            item_stack_id: escrow.item_stack_escrow_id.to_string(),
        });
    }
    if escrow.escrow_state != "held" && escrow.escrow_state != "partially_used" {
        return Err(SettlementError::InvalidTransition {
            from: escrow.escrow_state.clone(),
            action: "transfer_quantity_from_item_stack_escrow",
        });
    }
    Ok(())
}
