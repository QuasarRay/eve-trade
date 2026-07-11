use sqlx::{Postgres, Transaction};
use uuid::Uuid;

use crate::commands::{ExecuteBatchCommand, SettlementCommand, SettlementIntent};
use crate::error::{Result, SettlementError};

pub async fn authorize_plan(
    tx: &mut Transaction<'_, Postgres>,
    command: &ExecuteBatchCommand,
) -> Result<()> {
    let actor = command.caused_by_capsuleer_id.ok_or_else(|| {
        SettlementError::PermissionDenied("settlement actor is required".to_string())
    })?;
    match command.intent {
        SettlementIntent::Issue => authorize_issue(tx, actor, &command.operations).await,
        SettlementIntent::Accept => authorize_accept(tx, actor, &command.operations).await,
        SettlementIntent::Cancel => authorize_cancel(tx, actor, &command.operations).await,
        SettlementIntent::Unspecified => Err(SettlementError::PermissionDenied(
            "settlement intent is required".to_string(),
        )),
    }
}

async fn authorize_issue(
    tx: &mut Transaction<'_, Postgres>,
    actor: i64,
    operations: &[SettlementCommand],
) -> Result<()> {
    let SettlementCommand::TransferQuantityFromItemStackToItemStackEscrow(escrow) = &operations[1]
    else {
        return denied("ISSUE source item operation is missing");
    };
    let owner = item_owner(tx, escrow.source_item_stack_id).await?;
    require(
        owner == actor,
        "ISSUE source item stack does not belong to actor",
    )
}

async fn authorize_accept(
    tx: &mut Transaction<'_, Postgres>,
    actor: i64,
    operations: &[SettlementCommand],
) -> Result<()> {
    let debit = operations
        .iter()
        .find_map(|operation| match operation {
            SettlementCommand::TransferIskAmountFromWalletToWalletEscrow(value) => Some(value),
            _ => None,
        })
        .ok_or_else(|| {
            SettlementError::PermissionDenied("ACCEPT wallet debit is missing".to_string())
        })?;
    let item_delivery = operations
        .iter()
        .find_map(|operation| match operation {
            SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(
                value,
            ) => Some(value),
            _ => None,
        })
        .ok_or_else(|| {
            SettlementError::PermissionDenied("ACCEPT item delivery is missing".to_string())
        })?;
    let credit = operations
        .iter()
        .find_map(|operation| match operation {
            SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithNewOwner(value) => {
                Some(value)
            }
            _ => None,
        })
        .ok_or_else(|| {
            SettlementError::PermissionDenied("ACCEPT seller credit is missing".to_string())
        })?;

    let source_wallet_owner = wallet_owner(tx, debit.source_wallet_id).await?;
    require(
        source_wallet_owner == actor,
        "ACCEPT debited wallet does not belong to actor",
    )?;

    let trade = sqlx::query_as::<_, (i64, String, i64, i64)>(
        "SELECT issuer_id, trade_state, remaining_quantity, unit_price_isk FROM trade_instance WHERE trade_instance_id = $1 FOR UPDATE",
    )
    .bind(debit.trade_instance_id)
    .fetch_optional(&mut **tx)
    .await?
    .ok_or_else(|| SettlementError::NotFound(format!("trade_instance {}", debit.trade_instance_id)))?;
    require(trade.1 == "OPEN", "ACCEPT trade is not open")?;

    let escrow = sqlx::query_as::<_, (Uuid, i64, i64, bool)>(
        "SELECT trade_instance_id, owner_id, quantity, is_released FROM item_stack_escrow WHERE item_stack_escrow_id = $1 FOR UPDATE",
    )
    .bind(item_delivery.item_stack_escrow_id)
    .fetch_optional(&mut **tx)
    .await?
    .ok_or_else(|| SettlementError::NotFound(format!("item_stack_escrow {}", item_delivery.item_stack_escrow_id)))?;
    require(
        escrow.0 == debit.trade_instance_id,
        "ACCEPT item escrow belongs to another trade",
    )?;
    require(
        escrow.1 == trade.0,
        "ACCEPT item escrow is not owned by the trade issuer",
    )?;
    require(
        !escrow.3 && escrow.2 >= item_delivery.quantity,
        "ACCEPT item escrow is unavailable",
    )?;
    require(
        trade.2 >= item_delivery.quantity,
        "ACCEPT quantity exceeds trade remainder",
    )?;

    let expected_isk = item_delivery.quantity.checked_mul(trade.3).ok_or_else(|| {
        SettlementError::InvalidArgument("ACCEPT price multiplication overflow".to_string())
    })?;
    require(
        debit.isk_amount == expected_isk,
        "ACCEPT wallet debit does not match authoritative trade price",
    )?;
    require(
        wallet_owner(tx, credit.destination_wallet_id).await? == trade.0,
        "ACCEPT proceeds wallet does not belong to issuer",
    )?;

    let creates_destination = operations.iter().any(|operation| match operation {
        SettlementCommand::CreateNewEmptyItemStack(value) => {
            value.item_stack_id == Some(item_delivery.destination_item_stack_id)
                && value.owner_id == actor
        }
        _ => false,
    });
    if !creates_destination {
        require(
            item_owner(tx, item_delivery.destination_item_stack_id).await? == actor,
            "ACCEPT destination item stack does not belong to actor",
        )?;
    }
    Ok(())
}

async fn authorize_cancel(
    tx: &mut Transaction<'_, Postgres>,
    actor: i64,
    operations: &[SettlementCommand],
) -> Result<()> {
    let state = operations
        .iter()
        .find_map(|operation| match operation {
            SettlementCommand::ModifyTradeInstanceState(value) => Some(value),
            _ => None,
        })
        .ok_or_else(|| {
            SettlementError::PermissionDenied("CANCEL state transition is missing".to_string())
        })?;
    let trade = sqlx::query_as::<_, (i64, String)>(
        "SELECT issuer_id, trade_state FROM trade_instance WHERE trade_instance_id = $1 FOR UPDATE",
    )
    .bind(state.trade_instance_id)
    .fetch_optional(&mut **tx)
    .await?
    .ok_or_else(|| {
        SettlementError::NotFound(format!("trade_instance {}", state.trade_instance_id))
    })?;
    require(trade.0 == actor, "CANCEL actor is not the trade issuer")?;
    require(trade.1 == "OPEN", "CANCEL trade is not open")?;

    let mut item_returns = 0_i64;
    let mut wallet_returns = 0_i64;
    for operation in operations {
        match operation {
            SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner(
                value,
            ) => {
                item_returns += 1;
                let escrow = sqlx::query_as::<_, (Uuid, i64, bool)>(
                    "SELECT trade_instance_id, owner_id, is_released FROM item_stack_escrow WHERE item_stack_escrow_id = $1 FOR UPDATE",
                )
                .bind(value.item_stack_escrow_id)
                .fetch_optional(&mut **tx)
                .await?
                .ok_or_else(|| SettlementError::NotFound(format!("item_stack_escrow {}", value.item_stack_escrow_id)))?;
                require(
                    escrow.0 == state.trade_instance_id && escrow.1 == actor && !escrow.2,
                    "CANCEL item escrow is not returnable by actor",
                )?;
                require(
                    item_owner(tx, value.destination_item_stack_id).await? == actor,
                    "CANCEL item destination is not owned by issuer",
                )?;
            }
            SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner(
                value,
            ) => {
                wallet_returns += 1;
                let escrow = sqlx::query_as::<_, (Uuid, i64, bool)>(
                    "SELECT trade_instance_id, owner_id, is_released FROM wallet_escrow WHERE wallet_escrow_id = $1 FOR UPDATE",
                )
                .bind(value.wallet_escrow_id)
                .fetch_optional(&mut **tx)
                .await?
                .ok_or_else(|| SettlementError::NotFound(format!("wallet_escrow {}", value.wallet_escrow_id)))?;
                require(
                    escrow.0 == state.trade_instance_id && !escrow.2,
                    "CANCEL wallet escrow belongs to another trade or was released",
                )?;
                require(
                    wallet_owner(tx, value.destination_wallet_id).await? == escrow.1,
                    "CANCEL wallet refund destination is not the previous owner",
                )?;
            }
            _ => {}
        }
    }
    let active_items: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM item_stack_escrow WHERE trade_instance_id = $1 AND NOT is_released",
    )
    .bind(state.trade_instance_id)
    .fetch_one(&mut **tx)
    .await?;
    let active_wallets: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM wallet_escrow WHERE trade_instance_id = $1 AND NOT is_released",
    )
    .bind(state.trade_instance_id)
    .fetch_one(&mut **tx)
    .await?;
    require(
        item_returns == active_items,
        "CANCEL leaves residual item escrow",
    )?;
    require(
        wallet_returns == active_wallets,
        "CANCEL leaves residual wallet escrow",
    )
}

async fn item_owner(tx: &mut Transaction<'_, Postgres>, item_stack_id: Uuid) -> Result<i64> {
    sqlx::query_scalar("SELECT owner_id FROM item_stack WHERE item_stack_id = $1 FOR UPDATE")
        .bind(item_stack_id)
        .fetch_optional(&mut **tx)
        .await?
        .ok_or_else(|| SettlementError::NotFound(format!("item_stack {item_stack_id}")))
}

async fn wallet_owner(tx: &mut Transaction<'_, Postgres>, wallet_id: Uuid) -> Result<i64> {
    sqlx::query_scalar("SELECT capsuleer_id FROM wallet WHERE wallet_id = $1 FOR UPDATE")
        .bind(wallet_id)
        .fetch_optional(&mut **tx)
        .await?
        .ok_or_else(|| SettlementError::NotFound(format!("wallet {wallet_id}")))
}

fn require(condition: bool, message: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        denied(message)
    }
}

fn denied<T>(message: &str) -> Result<T> {
    Err(SettlementError::PermissionDenied(message.to_string()))
}
