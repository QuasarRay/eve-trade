use sqlx::{Postgres, Transaction};
use uuid::Uuid;

use crate::checksum::{item_stack_checksum, wallet_checksum, CHECKSUM_ALGORITHM};
use crate::commands::{
    CreateNewEmptyItemStack, CreateNewEmptyWalletEscrow, CreateNewTradeInstanceRow,
    MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner, ModifyTradeInstanceState,
    SettlementCommand, TransferIskAmountFromWalletEscrowToWalletWithNewOwner,
    TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner,
    TransferIskAmountFromWalletToWalletEscrow,
    TransferQuantityFromItemStackEscrowToItemStackWithNewOwner,
    TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner,
    TransferQuantityFromItemStackToItemStackEscrow,
};
use crate::error::{Result, SettlementError};

#[derive(Debug, Clone)]
pub struct EntityReferenceOutput {
    pub entity_kind: &'static str,
    pub entity_id: Uuid,
}

#[derive(Debug, Clone, Default)]
pub struct OperationOutput {
    pub entity_references: Vec<EntityReferenceOutput>,
}

impl OperationOutput {
    fn single(entity_kind: &'static str, entity_id: Uuid) -> Self {
        Self {
            entity_references: vec![EntityReferenceOutput {
                entity_kind,
                entity_id,
            }],
        }
    }

    fn many(entity_references: Vec<EntityReferenceOutput>) -> Self {
        Self { entity_references }
    }
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct TradeInstanceStateRow {
    trade_state: String,
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct ItemStackRow {
    item_stack_id: Uuid,
    owner_id: i64,
    item_type_id: i64,
    station_id: i64,
    quantity: i64,
    stack_state: String,
    stack_version: i64,
    stack_checksum: String,
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct ItemStackEscrowRow {
    item_stack_escrow_id: Uuid,
    trade_instance_id: Uuid,
    owner_id: i64,
    source_item_stack_id: Uuid,
    item_type_id: i64,
    station_id: i64,
    quantity: i64,
    is_released: bool,
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct WalletRow {
    wallet_id: Uuid,
    capsuleer_id: i64,
    isk_amount: i64,
    wallet_state: String,
    wallet_version: i64,
    wallet_checksum: String,
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct WalletEscrowRow {
    wallet_escrow_id: Uuid,
    trade_instance_id: Uuid,
    owner_id: i64,
    source_wallet_id: Uuid,
    isk_amount: i64,
    is_released: bool,
}

pub async fn execute_settlement_command(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    command: &SettlementCommand,
) -> Result<OperationOutput> {
    match command {
        SettlementCommand::CreateNewTradeInstanceRow(payload) => {
            create_new_trade_instance_row(tx, settlement_step_id, payload).await
        }
        SettlementCommand::ModifyTradeInstanceState(payload) => {
            modify_trade_instance_state(tx, settlement_step_id, payload).await
        }
        SettlementCommand::CreateNewEmptyItemStack(payload) => {
            create_new_empty_item_stack(tx, settlement_step_id, payload).await
        }
        SettlementCommand::TransferQuantityFromItemStackToItemStackEscrow(payload) => {
            transfer_quantity_from_item_stack_to_item_stack_escrow(tx, settlement_step_id, payload)
                .await
        }
        SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(payload) => {
            transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner(
                tx,
                settlement_step_id,
                payload,
            )
            .await
        }
        SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner(
            payload,
        ) => {
            transfer_quantity_from_item_stack_escrow_to_item_stack_with_previous_owner(
                tx,
                settlement_step_id,
                payload,
            )
            .await
        }
        SettlementCommand::MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner(payload) => {
            merge_item_stacks_with_identical_item_type_and_identical_owner(
                tx,
                settlement_step_id,
                payload,
            )
            .await
        }
        SettlementCommand::CreateNewEmptyWalletEscrow(payload) => {
            create_new_empty_wallet_escrow(tx, settlement_step_id, payload).await
        }
        SettlementCommand::TransferIskAmountFromWalletToWalletEscrow(payload) => {
            transfer_isk_amount_from_wallet_to_wallet_escrow(tx, settlement_step_id, payload).await
        }
        SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithNewOwner(payload) => {
            transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner(
                tx,
                settlement_step_id,
                payload,
            )
            .await
        }
        SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner(payload) => {
            transfer_isk_amount_from_wallet_escrow_to_wallet_with_previous_owner(
                tx,
                settlement_step_id,
                payload,
            )
            .await
        }
    }
}

pub async fn create_new_trade_instance_row(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    payload: &CreateNewTradeInstanceRow,
) -> Result<OperationOutput> {
    ensure_positive(payload.total_quantity, "total_quantity")?;
    ensure_non_negative(payload.unit_price_isk, "unit_price_isk")?;

    let trade_instance_id = payload.trade_instance_id.unwrap_or_else(Uuid::new_v4);

    sqlx::query(
        r#"
        INSERT INTO trade_instance (
            trade_instance_id,
            created_settlement_step_id,
            trade_kind,
            trade_state,
            issuer_id,
            item_type_id,
            station_id,
            total_quantity,
            remaining_quantity,
            unit_price_isk,
            expires_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, $9, $10)
        "#,
    )
    .bind(trade_instance_id)
    .bind(settlement_step_id)
    .bind(&payload.trade_kind)
    .bind(&payload.trade_state)
    .bind(payload.issuer_id)
    .bind(payload.item_type_id)
    .bind(payload.station_id)
    .bind(payload.total_quantity)
    .bind(payload.unit_price_isk)
    .bind(payload.expires_at)
    .execute(&mut **tx)
    .await?;

    Ok(OperationOutput::single("trade_instance", trade_instance_id))
}

pub async fn modify_trade_instance_state(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    payload: &ModifyTradeInstanceState,
) -> Result<OperationOutput> {
    let current = sqlx::query_as::<_, TradeInstanceStateRow>(
        r#"
        SELECT trade_state
        FROM trade_instance
        WHERE trade_instance_id = $1
        FOR UPDATE
        "#,
    )
    .bind(payload.trade_instance_id)
    .fetch_optional(&mut **tx)
    .await?
    .ok_or_else(|| {
        SettlementError::NotFound(format!(
            "trade_instance {} does not exist",
            payload.trade_instance_id
        ))
    })?;

    if payload.to_trade_state == "COMPLETED" {
        ensure_no_remaining_item_escrow(tx, payload.trade_instance_id).await?;
    }

    sqlx::query(
        r#"
        UPDATE trade_instance
        SET trade_state = $2,
            updated_at = now()
        WHERE trade_instance_id = $1
        "#,
    )
    .bind(payload.trade_instance_id)
    .bind(&payload.to_trade_state)
    .execute(&mut **tx)
    .await?;

    sqlx::query(
        r#"
        INSERT INTO trade_state_change (
            settlement_step_id,
            trade_instance_id,
            from_trade_state,
            to_trade_state,
            trade_state_change_kind,
            changed_by_service
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        "#,
    )
    .bind(settlement_step_id)
    .bind(payload.trade_instance_id)
    .bind(current.trade_state)
    .bind(&payload.to_trade_state)
    .bind(&payload.trade_state_change_kind)
    .bind(&payload.changed_by_service)
    .execute(&mut **tx)
    .await?;

    Ok(OperationOutput::single(
        "trade_instance",
        payload.trade_instance_id,
    ))
}

pub async fn create_new_empty_item_stack(
    tx: &mut Transaction<'_, Postgres>,
    _settlement_step_id: Uuid,
    payload: &CreateNewEmptyItemStack,
) -> Result<OperationOutput> {
    let item_stack_id = payload.item_stack_id.unwrap_or_else(Uuid::new_v4);
    let stack_version = 1_i64;
    let stack_checksum = item_stack_checksum(item_stack_id, 0, stack_version);

    sqlx::query(
        r#"
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
        VALUES ($1, $2, $3, $4, 0, 'ACTIVE', $5, $6, $7)
        "#,
    )
    .bind(item_stack_id)
    .bind(payload.owner_id)
    .bind(payload.item_type_id)
    .bind(payload.station_id)
    .bind(stack_version)
    .bind(stack_checksum)
    .bind(CHECKSUM_ALGORITHM)
    .execute(&mut **tx)
    .await?;

    Ok(OperationOutput::single("item_stack", item_stack_id))
}

pub async fn transfer_quantity_from_item_stack_to_item_stack_escrow(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    payload: &TransferQuantityFromItemStackToItemStackEscrow,
) -> Result<OperationOutput> {
    ensure_positive(payload.quantity, "quantity")?;

    let source = lock_item_stack(tx, payload.source_item_stack_id).await?;
    ensure_item_stack_active(&source)?;
    if source.quantity < payload.quantity {
        return Err(SettlementError::InsufficientQuantity(format!(
            "item_stack {} has {}, requested {}",
            source.item_stack_id, source.quantity, payload.quantity
        )));
    }

    let item_stack_escrow_id = payload.item_stack_escrow_id.unwrap_or_else(Uuid::new_v4);
    let existing_escrow = lock_item_stack_escrow_optional(tx, item_stack_escrow_id).await?;

    let new_source_quantity =
        checked_sub(source.quantity, payload.quantity, "item_stack quantity")?;
    let (source_version_after, source_checksum_after) =
        update_item_stack(tx, &source, new_source_quantity, "ACTIVE").await?;
    insert_item_stack_ledger(
        tx,
        settlement_step_id,
        &source,
        "TRANSFER_TO_ESCROW",
        -payload.quantity,
        new_source_quantity,
        source_version_after,
        &source_checksum_after,
    )
    .await?;

    if let Some(escrow) = existing_escrow {
        ensure_escrow_not_released(
            "item_stack_escrow",
            escrow.is_released,
            escrow.item_stack_escrow_id,
        )?;
        if escrow.trade_instance_id != payload.trade_instance_id
            || escrow.owner_id != source.owner_id
            || escrow.source_item_stack_id != source.item_stack_id
            || escrow.item_type_id != source.item_type_id
            || escrow.station_id != source.station_id
        {
            return Err(SettlementError::FailedPrecondition(format!(
                "item_stack_escrow {item_stack_escrow_id} is not compatible with source item stack"
            )));
        }

        let escrow_quantity_after = checked_add(
            escrow.quantity,
            payload.quantity,
            "item_stack_escrow quantity",
        )?;
        sqlx::query(
            r#"
            UPDATE item_stack_escrow
            SET quantity = $2,
                updated_at = now()
            WHERE item_stack_escrow_id = $1
            "#,
        )
        .bind(item_stack_escrow_id)
        .bind(escrow_quantity_after)
        .execute(&mut **tx)
        .await?;
    } else {
        sqlx::query(
            r#"
            INSERT INTO item_stack_escrow (
                item_stack_escrow_id,
                trade_instance_id,
                owner_id,
                source_item_stack_id,
                item_type_id,
                station_id,
                quantity,
                is_released,
                created_settlement_step_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, false, $8)
            "#,
        )
        .bind(item_stack_escrow_id)
        .bind(payload.trade_instance_id)
        .bind(source.owner_id)
        .bind(source.item_stack_id)
        .bind(source.item_type_id)
        .bind(source.station_id)
        .bind(payload.quantity)
        .bind(settlement_step_id)
        .execute(&mut **tx)
        .await?;
    }

    Ok(OperationOutput::single(
        "item_stack_escrow",
        item_stack_escrow_id,
    ))
}

pub async fn transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    payload: &TransferQuantityFromItemStackEscrowToItemStackWithNewOwner,
) -> Result<OperationOutput> {
    transfer_quantity_from_item_stack_escrow_to_item_stack(
        tx,
        settlement_step_id,
        payload.item_stack_escrow_id,
        payload.destination_item_stack_id,
        payload.quantity,
        EscrowOwnerRule::NewOwner,
    )
    .await
}

pub async fn transfer_quantity_from_item_stack_escrow_to_item_stack_with_previous_owner(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    payload: &TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner,
) -> Result<OperationOutput> {
    transfer_quantity_from_item_stack_escrow_to_item_stack(
        tx,
        settlement_step_id,
        payload.item_stack_escrow_id,
        payload.destination_item_stack_id,
        payload.quantity,
        EscrowOwnerRule::PreviousOwner,
    )
    .await
}

pub async fn merge_item_stacks_with_identical_item_type_and_identical_owner(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    payload: &MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner,
) -> Result<OperationOutput> {
    if payload.source_item_stack_id == payload.destination_item_stack_id {
        return Err(SettlementError::InvalidArgument(
            "source_item_stack_id and destination_item_stack_id must differ".to_string(),
        ));
    }

    let source = lock_item_stack(tx, payload.source_item_stack_id).await?;
    let destination = lock_item_stack(tx, payload.destination_item_stack_id).await?;
    ensure_item_stack_active(&source)?;
    ensure_item_stack_active(&destination)?;

    if source.owner_id != destination.owner_id
        || source.item_type_id != destination.item_type_id
        || source.station_id != destination.station_id
    {
        return Err(SettlementError::FailedPrecondition(
            "item stacks must have identical owner, item type, and station".to_string(),
        ));
    }

    let destination_quantity_after = checked_add(
        destination.quantity,
        source.quantity,
        "destination item_stack quantity",
    )?;

    let (destination_version_after, destination_checksum_after) =
        update_item_stack(tx, &destination, destination_quantity_after, "ACTIVE").await?;
    insert_item_stack_ledger(
        tx,
        settlement_step_id,
        &destination,
        "MERGE_IN",
        source.quantity,
        destination_quantity_after,
        destination_version_after,
        &destination_checksum_after,
    )
    .await?;

    let (source_version_after, source_checksum_after) =
        update_item_stack(tx, &source, 0, "MERGED").await?;
    insert_item_stack_ledger(
        tx,
        settlement_step_id,
        &source,
        "MERGE_OUT",
        -source.quantity,
        0,
        source_version_after,
        &source_checksum_after,
    )
    .await?;

    Ok(OperationOutput::many(vec![
        EntityReferenceOutput {
            entity_kind: "item_stack",
            entity_id: payload.destination_item_stack_id,
        },
        EntityReferenceOutput {
            entity_kind: "item_stack",
            entity_id: payload.source_item_stack_id,
        },
    ]))
}

pub async fn create_new_empty_wallet_escrow(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    payload: &CreateNewEmptyWalletEscrow,
) -> Result<OperationOutput> {
    let source_wallet = lock_wallet(tx, payload.source_wallet_id).await?;
    if source_wallet.capsuleer_id != payload.owner_id {
        return Err(SettlementError::FailedPrecondition(format!(
            "source_wallet {} is not owned by capsuleer {}",
            payload.source_wallet_id, payload.owner_id
        )));
    }
    ensure_wallet_active(&source_wallet)?;

    let wallet_escrow_id = payload.wallet_escrow_id.unwrap_or_else(Uuid::new_v4);
    sqlx::query(
        r#"
        INSERT INTO wallet_escrow (
            wallet_escrow_id,
            trade_instance_id,
            owner_id,
            source_wallet_id,
            isk_amount,
            is_released,
            created_settlement_step_id
        )
        VALUES ($1, $2, $3, $4, 0, false, $5)
        "#,
    )
    .bind(wallet_escrow_id)
    .bind(payload.trade_instance_id)
    .bind(payload.owner_id)
    .bind(payload.source_wallet_id)
    .bind(settlement_step_id)
    .execute(&mut **tx)
    .await?;

    Ok(OperationOutput::single("wallet_escrow", wallet_escrow_id))
}

pub async fn transfer_isk_amount_from_wallet_to_wallet_escrow(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    payload: &TransferIskAmountFromWalletToWalletEscrow,
) -> Result<OperationOutput> {
    ensure_positive(payload.isk_amount, "isk_amount")?;

    let source = lock_wallet(tx, payload.source_wallet_id).await?;
    ensure_wallet_active(&source)?;
    ensure_wallet_payment_matches_trade_price(tx, payload.trade_instance_id, payload.isk_amount)
        .await?;
    if source.isk_amount < payload.isk_amount {
        return Err(SettlementError::InsufficientFunds(format!(
            "wallet {} has {}, requested {}",
            source.wallet_id, source.isk_amount, payload.isk_amount
        )));
    }

    let wallet_escrow_id = payload.wallet_escrow_id.unwrap_or_else(Uuid::new_v4);
    let existing_escrow = lock_wallet_escrow_optional(tx, wallet_escrow_id).await?;

    let source_amount_after =
        checked_sub(source.isk_amount, payload.isk_amount, "wallet isk_amount")?;
    let (source_version_after, source_checksum_after) =
        update_wallet(tx, &source, source_amount_after).await?;
    insert_wallet_ledger(
        tx,
        settlement_step_id,
        &source,
        "TRANSFER_TO_ESCROW",
        -payload.isk_amount,
        source_amount_after,
        source_version_after,
        &source_checksum_after,
    )
    .await?;

    if let Some(escrow) = existing_escrow {
        ensure_escrow_not_released("wallet_escrow", escrow.is_released, escrow.wallet_escrow_id)?;
        if escrow.trade_instance_id != payload.trade_instance_id
            || escrow.owner_id != source.capsuleer_id
            || escrow.source_wallet_id != source.wallet_id
        {
            return Err(SettlementError::FailedPrecondition(format!(
                "wallet_escrow {wallet_escrow_id} is not compatible with source wallet"
            )));
        }

        let escrow_amount_after = checked_add(
            escrow.isk_amount,
            payload.isk_amount,
            "wallet_escrow isk_amount",
        )?;
        sqlx::query(
            r#"
            UPDATE wallet_escrow
            SET isk_amount = $2,
                updated_at = now()
            WHERE wallet_escrow_id = $1
            "#,
        )
        .bind(wallet_escrow_id)
        .bind(escrow_amount_after)
        .execute(&mut **tx)
        .await?;
    } else {
        sqlx::query(
            r#"
            INSERT INTO wallet_escrow (
                wallet_escrow_id,
                trade_instance_id,
                owner_id,
                source_wallet_id,
                isk_amount,
                is_released,
                created_settlement_step_id
            )
            VALUES ($1, $2, $3, $4, $5, false, $6)
            "#,
        )
        .bind(wallet_escrow_id)
        .bind(payload.trade_instance_id)
        .bind(source.capsuleer_id)
        .bind(source.wallet_id)
        .bind(payload.isk_amount)
        .bind(settlement_step_id)
        .execute(&mut **tx)
        .await?;
    }

    Ok(OperationOutput::single("wallet_escrow", wallet_escrow_id))
}

pub async fn transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    payload: &TransferIskAmountFromWalletEscrowToWalletWithNewOwner,
) -> Result<OperationOutput> {
    transfer_isk_amount_from_wallet_escrow_to_wallet(
        tx,
        settlement_step_id,
        payload.wallet_escrow_id,
        payload.destination_wallet_id,
        payload.isk_amount,
        EscrowOwnerRule::NewOwner,
    )
    .await
}

pub async fn transfer_isk_amount_from_wallet_escrow_to_wallet_with_previous_owner(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    payload: &TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner,
) -> Result<OperationOutput> {
    transfer_isk_amount_from_wallet_escrow_to_wallet(
        tx,
        settlement_step_id,
        payload.wallet_escrow_id,
        payload.destination_wallet_id,
        payload.isk_amount,
        EscrowOwnerRule::PreviousOwner,
    )
    .await
}

#[derive(Debug, Clone, Copy)]
enum EscrowOwnerRule {
    NewOwner,
    PreviousOwner,
}

async fn transfer_quantity_from_item_stack_escrow_to_item_stack(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    item_stack_escrow_id: Uuid,
    destination_item_stack_id: Uuid,
    quantity: i64,
    owner_rule: EscrowOwnerRule,
) -> Result<OperationOutput> {
    ensure_positive(quantity, "quantity")?;

    let escrow = lock_item_stack_escrow(tx, item_stack_escrow_id).await?;
    ensure_escrow_not_released(
        "item_stack_escrow",
        escrow.is_released,
        escrow.item_stack_escrow_id,
    )?;
    if escrow.quantity < quantity {
        return Err(SettlementError::InsufficientQuantity(format!(
            "item_stack_escrow {} has {}, requested {}",
            escrow.item_stack_escrow_id, escrow.quantity, quantity
        )));
    }

    let destination = lock_item_stack(tx, destination_item_stack_id).await?;
    ensure_item_stack_active(&destination)?;
    if destination.item_type_id != escrow.item_type_id
        || destination.station_id != escrow.station_id
    {
        return Err(SettlementError::FailedPrecondition(
            "destination item stack must match escrow item type and station".to_string(),
        ));
    }

    validate_owner_rule(
        owner_rule,
        escrow.owner_id,
        destination.owner_id,
        "destination item stack",
    )?;
    if matches!(owner_rule, EscrowOwnerRule::NewOwner) {
        ensure_active_wallet_escrow_matches_item_release(tx, escrow.trade_instance_id, quantity)
            .await?;
    }

    let destination_quantity_after = checked_add(
        destination.quantity,
        quantity,
        "destination item_stack quantity",
    )?;
    let (destination_version_after, destination_checksum_after) =
        update_item_stack(tx, &destination, destination_quantity_after, "ACTIVE").await?;
    let entry_kind = match owner_rule {
        EscrowOwnerRule::NewOwner => "TRANSFER_FROM_ESCROW_TO_NEW_OWNER",
        EscrowOwnerRule::PreviousOwner => "TRANSFER_FROM_ESCROW_TO_PREVIOUS_OWNER",
    };
    insert_item_stack_ledger(
        tx,
        settlement_step_id,
        &destination,
        entry_kind,
        quantity,
        destination_quantity_after,
        destination_version_after,
        &destination_checksum_after,
    )
    .await?;

    let escrow_quantity_after =
        checked_sub(escrow.quantity, quantity, "item_stack_escrow quantity")?;
    release_or_update_item_stack_escrow(
        tx,
        escrow.item_stack_escrow_id,
        escrow_quantity_after,
        settlement_step_id,
    )
    .await?;
    update_trade_remaining_quantity(
        tx,
        escrow.trade_instance_id,
        escrow.quantity,
        escrow_quantity_after,
    )
    .await?;

    Ok(OperationOutput::many(vec![
        EntityReferenceOutput {
            entity_kind: "item_stack",
            entity_id: destination_item_stack_id,
        },
        EntityReferenceOutput {
            entity_kind: "item_stack_escrow",
            entity_id: item_stack_escrow_id,
        },
    ]))
}

async fn update_trade_remaining_quantity(
    tx: &mut Transaction<'_, Postgres>,
    trade_instance_id: Uuid,
    remaining_quantity_before: i64,
    remaining_quantity: i64,
) -> Result<()> {
    let result = sqlx::query(
        r#"
        UPDATE trade_instance
        SET remaining_quantity = $2,
            updated_at = now()
        WHERE trade_instance_id = $1
          AND remaining_quantity = $3
        "#,
    )
    .bind(trade_instance_id)
    .bind(remaining_quantity)
    .bind(remaining_quantity_before)
    .execute(&mut **tx)
    .await?;

    if result.rows_affected() != 1 {
        return Err(SettlementError::FailedPrecondition(format!(
            "trade_instance {trade_instance_id} remaining_quantity is inconsistent with item_stack_escrow quantity"
        )));
    }

    Ok(())
}

async fn ensure_no_remaining_item_escrow(
    tx: &mut Transaction<'_, Postgres>,
    trade_instance_id: Uuid,
) -> Result<()> {
    let remaining = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT COALESCE(SUM(quantity), 0)::BIGINT
        FROM item_stack_escrow
        WHERE trade_instance_id = $1
          AND is_released = false
        "#,
    )
    .bind(trade_instance_id)
    .fetch_one(&mut **tx)
    .await?;

    if remaining > 0 {
        return Err(SettlementError::FailedPrecondition(format!(
            "cannot complete trade_instance {trade_instance_id} while remaining item escrow quantity is {remaining}"
        )));
    }

    Ok(())
}

async fn ensure_wallet_payment_matches_trade_price(
    tx: &mut Transaction<'_, Postgres>,
    trade_instance_id: Uuid,
    isk_amount: i64,
) -> Result<()> {
    let unit_price_isk = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT unit_price_isk
        FROM trade_instance
        WHERE trade_instance_id = $1
        FOR UPDATE
        "#,
    )
    .bind(trade_instance_id)
    .fetch_optional(&mut **tx)
    .await?
    .ok_or_else(|| {
        SettlementError::NotFound(format!("trade_instance {trade_instance_id} does not exist"))
    })?;

    if unit_price_isk <= 0 || isk_amount % unit_price_isk != 0 {
        return Err(SettlementError::FailedPrecondition(format!(
            "wallet payment {isk_amount} does not match trade price {unit_price_isk}"
        )));
    }

    Ok(())
}

async fn ensure_active_wallet_escrow_matches_item_release(
    tx: &mut Transaction<'_, Postgres>,
    trade_instance_id: Uuid,
    quantity: i64,
) -> Result<()> {
    let unit_price_isk = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT unit_price_isk
        FROM trade_instance t
        WHERE trade_instance_id = $1
        FOR UPDATE
        "#,
    )
    .bind(trade_instance_id)
    .fetch_optional(&mut **tx)
    .await?
    .ok_or_else(|| {
        SettlementError::NotFound(format!("trade_instance {trade_instance_id} does not exist"))
    })?;
    let active_wallet_escrow_isk = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT COALESCE(SUM(isk_amount), 0)::BIGINT
        FROM wallet_escrow
        WHERE trade_instance_id = $1
          AND is_released = false
        "#,
    )
    .bind(trade_instance_id)
    .fetch_one(&mut **tx)
    .await?;

    let expected_payment = unit_price_isk
        .checked_mul(quantity)
        .ok_or_else(|| SettlementError::FailedPrecondition("trade price overflow".to_string()))?;
    if active_wallet_escrow_isk != expected_payment {
        return Err(SettlementError::FailedPrecondition(format!(
            "active wallet escrow payment {} does not match trade price {} for quantity {}",
            active_wallet_escrow_isk, unit_price_isk, quantity
        )));
    }

    Ok(())
}

async fn transfer_isk_amount_from_wallet_escrow_to_wallet(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    wallet_escrow_id: Uuid,
    destination_wallet_id: Uuid,
    isk_amount: i64,
    owner_rule: EscrowOwnerRule,
) -> Result<OperationOutput> {
    ensure_positive(isk_amount, "isk_amount")?;

    let escrow = lock_wallet_escrow(tx, wallet_escrow_id).await?;
    ensure_escrow_not_released("wallet_escrow", escrow.is_released, escrow.wallet_escrow_id)?;
    if escrow.isk_amount < isk_amount {
        return Err(SettlementError::InsufficientFunds(format!(
            "wallet_escrow {} has {}, requested {}",
            escrow.wallet_escrow_id, escrow.isk_amount, isk_amount
        )));
    }

    let destination = lock_wallet(tx, destination_wallet_id).await?;
    ensure_wallet_active(&destination)?;
    validate_owner_rule(
        owner_rule,
        escrow.owner_id,
        destination.capsuleer_id,
        "destination wallet",
    )?;

    let destination_amount_after = checked_add(
        destination.isk_amount,
        isk_amount,
        "destination wallet isk_amount",
    )?;
    let (destination_version_after, destination_checksum_after) =
        update_wallet(tx, &destination, destination_amount_after).await?;
    let entry_kind = match owner_rule {
        EscrowOwnerRule::NewOwner => "TRANSFER_FROM_ESCROW_TO_NEW_OWNER",
        EscrowOwnerRule::PreviousOwner => "TRANSFER_FROM_ESCROW_TO_PREVIOUS_OWNER",
    };
    insert_wallet_ledger(
        tx,
        settlement_step_id,
        &destination,
        entry_kind,
        isk_amount,
        destination_amount_after,
        destination_version_after,
        &destination_checksum_after,
    )
    .await?;

    let escrow_amount_after =
        checked_sub(escrow.isk_amount, isk_amount, "wallet_escrow isk_amount")?;
    release_or_update_wallet_escrow(
        tx,
        escrow.wallet_escrow_id,
        escrow_amount_after,
        settlement_step_id,
    )
    .await?;

    Ok(OperationOutput::many(vec![
        EntityReferenceOutput {
            entity_kind: "wallet",
            entity_id: destination_wallet_id,
        },
        EntityReferenceOutput {
            entity_kind: "wallet_escrow",
            entity_id: wallet_escrow_id,
        },
    ]))
}

async fn lock_item_stack(
    tx: &mut Transaction<'_, Postgres>,
    item_stack_id: Uuid,
) -> Result<ItemStackRow> {
    sqlx::query_as::<_, ItemStackRow>(
        r#"
        SELECT item_stack_id,
               owner_id,
               item_type_id,
               station_id,
               quantity,
               stack_state,
               stack_version,
               stack_checksum
        FROM item_stack
        WHERE item_stack_id = $1
        FOR UPDATE
        "#,
    )
    .bind(item_stack_id)
    .fetch_optional(&mut **tx)
    .await?
    .ok_or_else(|| SettlementError::NotFound(format!("item_stack {item_stack_id} does not exist")))
}

async fn lock_item_stack_escrow(
    tx: &mut Transaction<'_, Postgres>,
    item_stack_escrow_id: Uuid,
) -> Result<ItemStackEscrowRow> {
    lock_item_stack_escrow_optional(tx, item_stack_escrow_id)
        .await?
        .ok_or_else(|| {
            SettlementError::NotFound(format!(
                "item_stack_escrow {item_stack_escrow_id} does not exist"
            ))
        })
}

async fn lock_item_stack_escrow_optional(
    tx: &mut Transaction<'_, Postgres>,
    item_stack_escrow_id: Uuid,
) -> Result<Option<ItemStackEscrowRow>> {
    Ok(sqlx::query_as::<_, ItemStackEscrowRow>(
        r#"
        SELECT item_stack_escrow_id,
               trade_instance_id,
               owner_id,
               source_item_stack_id,
               item_type_id,
               station_id,
               quantity,
               is_released
        FROM item_stack_escrow
        WHERE item_stack_escrow_id = $1
        FOR UPDATE
        "#,
    )
    .bind(item_stack_escrow_id)
    .fetch_optional(&mut **tx)
    .await?)
}

async fn lock_wallet(tx: &mut Transaction<'_, Postgres>, wallet_id: Uuid) -> Result<WalletRow> {
    sqlx::query_as::<_, WalletRow>(
        r#"
        SELECT wallet_id,
               capsuleer_id,
               isk_amount,
               wallet_state,
               wallet_version,
               wallet_checksum
        FROM wallet
        WHERE wallet_id = $1
        FOR UPDATE
        "#,
    )
    .bind(wallet_id)
    .fetch_optional(&mut **tx)
    .await?
    .ok_or_else(|| SettlementError::NotFound(format!("wallet {wallet_id} does not exist")))
}

async fn lock_wallet_escrow(
    tx: &mut Transaction<'_, Postgres>,
    wallet_escrow_id: Uuid,
) -> Result<WalletEscrowRow> {
    lock_wallet_escrow_optional(tx, wallet_escrow_id)
        .await?
        .ok_or_else(|| {
            SettlementError::NotFound(format!("wallet_escrow {wallet_escrow_id} does not exist"))
        })
}

async fn lock_wallet_escrow_optional(
    tx: &mut Transaction<'_, Postgres>,
    wallet_escrow_id: Uuid,
) -> Result<Option<WalletEscrowRow>> {
    Ok(sqlx::query_as::<_, WalletEscrowRow>(
        r#"
        SELECT wallet_escrow_id,
               trade_instance_id,
               owner_id,
               source_wallet_id,
               isk_amount,
               is_released
        FROM wallet_escrow
        WHERE wallet_escrow_id = $1
        FOR UPDATE
        "#,
    )
    .bind(wallet_escrow_id)
    .fetch_optional(&mut **tx)
    .await?)
}

async fn update_item_stack(
    tx: &mut Transaction<'_, Postgres>,
    stack: &ItemStackRow,
    quantity_after: i64,
    stack_state_after: &str,
) -> Result<(i64, String)> {
    let version_after = checked_add(stack.stack_version, 1, "item_stack stack_version")?;
    let checksum_after = item_stack_checksum(stack.item_stack_id, quantity_after, version_after);

    sqlx::query(
        r#"
        UPDATE item_stack
        SET quantity = $2,
            stack_state = $3,
            stack_version = $4,
            stack_checksum = $5,
            checksum_algorithm = $6,
            updated_at = now()
        WHERE item_stack_id = $1
        "#,
    )
    .bind(stack.item_stack_id)
    .bind(quantity_after)
    .bind(stack_state_after)
    .bind(version_after)
    .bind(&checksum_after)
    .bind(CHECKSUM_ALGORITHM)
    .execute(&mut **tx)
    .await?;

    Ok((version_after, checksum_after))
}

#[allow(clippy::too_many_arguments)]
async fn insert_item_stack_ledger(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    stack: &ItemStackRow,
    entry_kind: &str,
    quantity_delta: i64,
    quantity_after: i64,
    stack_version_after: i64,
    stack_checksum_after: &str,
) -> Result<()> {
    sqlx::query(
        r#"
        INSERT INTO item_stack_ledger (
            settlement_step_id,
            item_stack_id,
            item_type_id,
            owner_id,
            station_id,
            entry_kind,
            quantity_delta,
            quantity_before,
            quantity_after,
            stack_version_before,
            stack_version_after,
            stack_checksum_before,
            stack_checksum_after
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        "#,
    )
    .bind(settlement_step_id)
    .bind(stack.item_stack_id)
    .bind(stack.item_type_id)
    .bind(stack.owner_id)
    .bind(stack.station_id)
    .bind(entry_kind)
    .bind(quantity_delta)
    .bind(stack.quantity)
    .bind(quantity_after)
    .bind(stack.stack_version)
    .bind(stack_version_after)
    .bind(&stack.stack_checksum)
    .bind(stack_checksum_after)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn update_wallet(
    tx: &mut Transaction<'_, Postgres>,
    wallet: &WalletRow,
    isk_amount_after: i64,
) -> Result<(i64, String)> {
    let version_after = checked_add(wallet.wallet_version, 1, "wallet wallet_version")?;
    let checksum_after = wallet_checksum(wallet.wallet_id, isk_amount_after, version_after);

    sqlx::query(
        r#"
        UPDATE wallet
        SET isk_amount = $2,
            wallet_version = $3,
            wallet_checksum = $4,
            checksum_algorithm = $5,
            updated_at = now()
        WHERE wallet_id = $1
        "#,
    )
    .bind(wallet.wallet_id)
    .bind(isk_amount_after)
    .bind(version_after)
    .bind(&checksum_after)
    .bind(CHECKSUM_ALGORITHM)
    .execute(&mut **tx)
    .await?;

    Ok((version_after, checksum_after))
}

#[allow(clippy::too_many_arguments)]
async fn insert_wallet_ledger(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    wallet: &WalletRow,
    entry_kind: &str,
    isk_amount_delta: i64,
    isk_amount_after: i64,
    wallet_version_after: i64,
    wallet_checksum_after: &str,
) -> Result<()> {
    sqlx::query(
        r#"
        INSERT INTO wallet_ledger (
            settlement_step_id,
            wallet_id,
            capsuleer_id,
            entry_kind,
            isk_amount_delta,
            isk_amount_before,
            isk_amount_after,
            wallet_version_before,
            wallet_version_after,
            wallet_checksum_before,
            wallet_checksum_after
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        "#,
    )
    .bind(settlement_step_id)
    .bind(wallet.wallet_id)
    .bind(wallet.capsuleer_id)
    .bind(entry_kind)
    .bind(isk_amount_delta)
    .bind(wallet.isk_amount)
    .bind(isk_amount_after)
    .bind(wallet.wallet_version)
    .bind(wallet_version_after)
    .bind(&wallet.wallet_checksum)
    .bind(wallet_checksum_after)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn release_or_update_item_stack_escrow(
    tx: &mut Transaction<'_, Postgres>,
    item_stack_escrow_id: Uuid,
    quantity_after: i64,
    released_settlement_step_id: Uuid,
) -> Result<()> {
    if quantity_after == 0 {
        sqlx::query(
            r#"
            UPDATE item_stack_escrow
            SET quantity = 0,
                is_released = true,
                released_settlement_step_id = $2,
                released_at = now(),
                updated_at = now()
            WHERE item_stack_escrow_id = $1
            "#,
        )
        .bind(item_stack_escrow_id)
        .bind(released_settlement_step_id)
        .execute(&mut **tx)
        .await?;
    } else {
        sqlx::query(
            r#"
            UPDATE item_stack_escrow
            SET quantity = $2,
                updated_at = now()
            WHERE item_stack_escrow_id = $1
            "#,
        )
        .bind(item_stack_escrow_id)
        .bind(quantity_after)
        .execute(&mut **tx)
        .await?;
    }

    Ok(())
}

async fn release_or_update_wallet_escrow(
    tx: &mut Transaction<'_, Postgres>,
    wallet_escrow_id: Uuid,
    isk_amount_after: i64,
    released_settlement_step_id: Uuid,
) -> Result<()> {
    if isk_amount_after == 0 {
        sqlx::query(
            r#"
            UPDATE wallet_escrow
            SET isk_amount = 0,
                is_released = true,
                released_settlement_step_id = $2,
                released_at = now(),
                updated_at = now()
            WHERE wallet_escrow_id = $1
            "#,
        )
        .bind(wallet_escrow_id)
        .bind(released_settlement_step_id)
        .execute(&mut **tx)
        .await?;
    } else {
        sqlx::query(
            r#"
            UPDATE wallet_escrow
            SET isk_amount = $2,
                updated_at = now()
            WHERE wallet_escrow_id = $1
            "#,
        )
        .bind(wallet_escrow_id)
        .bind(isk_amount_after)
        .execute(&mut **tx)
        .await?;
    }

    Ok(())
}

fn ensure_item_stack_active(stack: &ItemStackRow) -> Result<()> {
    if stack.stack_state == "ACTIVE" {
        Ok(())
    } else {
        Err(SettlementError::FailedPrecondition(format!(
            "item_stack {} is not ACTIVE",
            stack.item_stack_id
        )))
    }
}

fn ensure_wallet_active(wallet: &WalletRow) -> Result<()> {
    if wallet.wallet_state == "ACTIVE" {
        Ok(())
    } else {
        Err(SettlementError::FailedPrecondition(format!(
            "wallet {} is not ACTIVE",
            wallet.wallet_id
        )))
    }
}

fn ensure_escrow_not_released(entity_kind: &str, is_released: bool, entity_id: Uuid) -> Result<()> {
    if is_released {
        Err(SettlementError::FailedPrecondition(format!(
            "{entity_kind} {entity_id} is already released"
        )))
    } else {
        Ok(())
    }
}

fn validate_owner_rule(
    rule: EscrowOwnerRule,
    escrow_owner_id: i64,
    destination_owner_id: i64,
    destination_name: &str,
) -> Result<()> {
    match rule {
        EscrowOwnerRule::NewOwner if destination_owner_id == escrow_owner_id => {
            Err(SettlementError::FailedPrecondition(format!(
                "{destination_name} must belong to a new owner"
            )))
        }
        EscrowOwnerRule::PreviousOwner if destination_owner_id != escrow_owner_id => {
            Err(SettlementError::FailedPrecondition(format!(
                "{destination_name} must belong to the previous owner"
            )))
        }
        _ => Ok(()),
    }
}

fn ensure_positive(value: i64, field_name: &str) -> Result<()> {
    if value > 0 {
        Ok(())
    } else {
        Err(SettlementError::InvalidArgument(format!(
            "{field_name} must be greater than zero"
        )))
    }
}

fn ensure_non_negative(value: i64, field_name: &str) -> Result<()> {
    if value >= 0 {
        Ok(())
    } else {
        Err(SettlementError::InvalidArgument(format!(
            "{field_name} must be non-negative"
        )))
    }
}

fn checked_add(left: i64, right: i64, label: &str) -> Result<i64> {
    left.checked_add(right)
        .ok_or_else(|| SettlementError::FailedPrecondition(format!("{label} overflow")))
}

fn checked_sub(left: i64, right: i64, label: &str) -> Result<i64> {
    left.checked_sub(right)
        .ok_or_else(|| SettlementError::FailedPrecondition(format!("{label} underflow")))
}
