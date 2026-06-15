use chrono::{DateTime, Utc};
use sqlx::PgPool;
use uuid::Uuid;

use crate::error::SettlementError;

use super::{
    support::{
        ensure_not_blank, ensure_positive, tx_conn, wallet_checksum, DbTx, CHECKSUM_ALGORITHM,
    },
    types::{
        CreateNewEmptyWallerEscrowInput,
        TransferIskAmountFromWalletEscrowToWalletWithNewOwnerInput,
        TransferIskAmountFromWalletEscrowToWalletWithPreviousOwnerInput,
        TransferIskAmountFromWalletToWalletEscrowInput, WalletEscrowRow,
        WalletEscrowTransferResult, WalletRow,
    },
};

pub(crate) async fn create_new_empty_waller_escrow(
    pool: &PgPool,
    input: CreateNewEmptyWallerEscrowInput,
) -> Result<WalletEscrowRow, SettlementError> {
    let mut tx = pool.begin().await?;
    insert_wallet_operation(
        &mut tx,
        input.wallet_operation_id,
        input.operation_id,
        &input.operation_kind,
        input.created_at,
    )
    .await?;

    let row = sqlx::query_as::<_, WalletEscrowRow>(
        r#"
        INSERT INTO wallet_escrow (
            wallet_escrow_id, trade_instance_id, isk_amount, owner_id,
            created_wallet_operation_id, created_at, updated_at
        )
        VALUES ($1, $2, 0, $3, $4, $5, $5)
        RETURNING wallet_escrow_id, trade_instance_id,
                  (isk_amount * 100)::bigint AS isk_minor,
                  owner_id, created_wallet_operation_id, released_wallet_operation_id,
                  created_at, updated_at, released_at
        "#,
    )
    .bind(input.wallet_escrow_id)
    .bind(input.trade_instance_id)
    .bind(input.owner_id)
    .bind(input.wallet_operation_id)
    .bind(input.created_at)
    .fetch_one(tx_conn(&mut tx))
    .await?;
    tx.commit().await?;
    Ok(row)
}

pub(crate) async fn transfer_isk_amount_from_wallet_to_wallet_escrow(
    pool: &PgPool,
    input: TransferIskAmountFromWalletToWalletEscrowInput,
) -> Result<WalletEscrowTransferResult, SettlementError> {
    ensure_positive(input.isk_minor, "isk_minor")?;

    let mut tx = pool.begin().await?;
    insert_wallet_operation(
        &mut tx,
        input.wallet_operation_id,
        input.operation_id,
        &input.operation_kind,
        input.transferred_at,
    )
    .await?;

    let escrow = lock_wallet_escrow(&mut tx, input.wallet_escrow_id).await?;
    let wallet = lock_wallet(&mut tx, input.source_wallet_id).await?;
    if wallet.capsuleer_id != escrow.owner_id {
        return Err(SettlementError::InvalidRequest(
            "source wallet owner must match wallet escrow owner".to_string(),
        ));
    }

    let wallet = mutate_locked_wallet(
        &mut tx,
        wallet,
        input.wallet_operation_id,
        -input.isk_minor,
        "wallet_to_escrow_debit",
        input.transferred_at,
    )
    .await?;
    let wallet_escrow = credit_wallet_escrow(
        &mut tx,
        input.wallet_escrow_id,
        input.isk_minor,
        input.transferred_at,
    )
    .await?;

    tx.commit().await?;
    Ok(WalletEscrowTransferResult {
        wallet_operation_id: input.wallet_operation_id,
        wallet,
        wallet_escrow,
    })
}

pub(crate) async fn transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner(
    pool: &PgPool,
    input: TransferIskAmountFromWalletEscrowToWalletWithNewOwnerInput,
) -> Result<WalletEscrowTransferResult, SettlementError> {
    ensure_positive(input.isk_minor, "isk_minor")?;

    let mut tx = pool.begin().await?;
    insert_wallet_operation(
        &mut tx,
        input.wallet_operation_id,
        input.operation_id,
        &input.operation_kind,
        input.transferred_at,
    )
    .await?;

    let escrow = lock_wallet_escrow(&mut tx, input.wallet_escrow_id).await?;
    ensure_wallet_escrow_can_debit(&escrow, input.isk_minor)?;
    let wallet = lock_wallet(&mut tx, input.destination_wallet_id).await?;
    if wallet.capsuleer_id != input.new_owner_id {
        return Err(SettlementError::InvalidRequest(
            "destination wallet owner must match new_owner_id".to_string(),
        ));
    }

    let wallet = mutate_locked_wallet(
        &mut tx,
        wallet,
        input.wallet_operation_id,
        input.isk_minor,
        "escrow_to_new_owner_credit",
        input.transferred_at,
    )
    .await?;
    let wallet_escrow = debit_wallet_escrow(
        &mut tx,
        input.wallet_escrow_id,
        input.wallet_operation_id,
        input.isk_minor,
        input.transferred_at,
    )
    .await?;

    tx.commit().await?;
    Ok(WalletEscrowTransferResult {
        wallet_operation_id: input.wallet_operation_id,
        wallet,
        wallet_escrow,
    })
}

pub(crate) async fn transfer_isk_amount_from_wallet_escrow_to_wallet_with_previous_owner(
    pool: &PgPool,
    input: TransferIskAmountFromWalletEscrowToWalletWithPreviousOwnerInput,
) -> Result<WalletEscrowTransferResult, SettlementError> {
    ensure_positive(input.isk_minor, "isk_minor")?;

    let mut tx = pool.begin().await?;
    insert_wallet_operation(
        &mut tx,
        input.wallet_operation_id,
        input.operation_id,
        &input.operation_kind,
        input.transferred_at,
    )
    .await?;

    let escrow = lock_wallet_escrow(&mut tx, input.wallet_escrow_id).await?;
    ensure_wallet_escrow_can_debit(&escrow, input.isk_minor)?;
    let wallet = lock_wallet(&mut tx, input.destination_wallet_id).await?;
    if wallet.capsuleer_id != escrow.owner_id {
        return Err(SettlementError::InvalidRequest(
            "destination wallet owner must match wallet escrow owner".to_string(),
        ));
    }

    let wallet = mutate_locked_wallet(
        &mut tx,
        wallet,
        input.wallet_operation_id,
        input.isk_minor,
        "escrow_to_previous_owner_credit",
        input.transferred_at,
    )
    .await?;
    let wallet_escrow = debit_wallet_escrow(
        &mut tx,
        input.wallet_escrow_id,
        input.wallet_operation_id,
        input.isk_minor,
        input.transferred_at,
    )
    .await?;

    tx.commit().await?;
    Ok(WalletEscrowTransferResult {
        wallet_operation_id: input.wallet_operation_id,
        wallet,
        wallet_escrow,
    })
}

async fn insert_wallet_operation(
    tx: &mut DbTx<'_>,
    wallet_operation_id: Uuid,
    operation_id: Uuid,
    operation_kind: &str,
    at: DateTime<Utc>,
) -> Result<(), SettlementError> {
    ensure_not_blank(operation_kind, "operation_kind")?;
    sqlx::query(
        r#"
        INSERT INTO wallet_operation (
            wallet_operation_id, operation_id, operation_kind,
            wallet_operation_state, created_at, completed_at
        )
        VALUES ($1, $2, $3, 'completed', $4, $4)
        "#,
    )
    .bind(wallet_operation_id)
    .bind(operation_id)
    .bind(operation_kind)
    .bind(at)
    .execute(tx_conn(tx))
    .await?;
    Ok(())
}

async fn lock_wallet(tx: &mut DbTx<'_>, wallet_id: Uuid) -> Result<WalletRow, SettlementError> {
    Ok(sqlx::query_as::<_, WalletRow>(
        r#"
        SELECT wallet_id, capsuleer_id, (isk_amount * 100)::bigint AS isk_minor,
               wallet_state, wallet_version, wallet_checksum
        FROM wallet
        WHERE wallet_id = $1
        FOR UPDATE
        "#,
    )
    .bind(wallet_id)
    .fetch_one(tx_conn(tx))
    .await?)
}

async fn lock_wallet_escrow(
    tx: &mut DbTx<'_>,
    wallet_escrow_id: Uuid,
) -> Result<WalletEscrowRow, SettlementError> {
    Ok(sqlx::query_as::<_, WalletEscrowRow>(
        r#"
        SELECT wallet_escrow_id, trade_instance_id, (isk_amount * 100)::bigint AS isk_minor,
               owner_id, created_wallet_operation_id, released_wallet_operation_id,
               created_at, updated_at, released_at
        FROM wallet_escrow
        WHERE wallet_escrow_id = $1
        FOR UPDATE
        "#,
    )
    .bind(wallet_escrow_id)
    .fetch_one(tx_conn(tx))
    .await?)
}

async fn mutate_locked_wallet(
    tx: &mut DbTx<'_>,
    before: WalletRow,
    wallet_operation_id: Uuid,
    delta_minor: i64,
    entry_kind: &str,
    at: DateTime<Utc>,
) -> Result<WalletRow, SettlementError> {
    ensure_not_blank(entry_kind, "entry_kind")?;
    let after_minor = before
        .isk_minor
        .checked_add(delta_minor)
        .ok_or_else(|| SettlementError::DatabaseConflict("wallet amount overflow".to_string()))?;
    if after_minor < 0 {
        return Err(SettlementError::InsufficientIsk {
            wallet_id: before.wallet_id.to_string(),
        });
    }
    let after_version = before
        .wallet_version
        .checked_add(1)
        .ok_or_else(|| SettlementError::DatabaseConflict("wallet version overflow".to_string()))?;
    let after_checksum = wallet_checksum(
        before.wallet_id,
        before.capsuleer_id,
        after_minor,
        after_version,
    );

    let row = sqlx::query_as::<_, WalletRow>(
        r#"
        UPDATE wallet
        SET isk_amount = ($2::numeric / 100), wallet_version = $3,
            wallet_checksum = $4, checksum_algorithm = $5, updated_at = $6
        WHERE wallet_id = $1
        RETURNING wallet_id, capsuleer_id, (isk_amount * 100)::bigint AS isk_minor,
                  wallet_state, wallet_version, wallet_checksum
        "#,
    )
    .bind(before.wallet_id)
    .bind(after_minor)
    .bind(after_version)
    .bind(&after_checksum)
    .bind(CHECKSUM_ALGORITHM)
    .bind(at)
    .fetch_one(tx_conn(tx))
    .await?;

    sqlx::query(
        r#"
        INSERT INTO wallet_ledger (
            wallet_ledger_id, wallet_operation_id, wallet_id, capsuleer_id, entry_kind,
            isk_amount_delta, isk_amount_before, isk_amount_after, wallet_version_before,
            wallet_version_after, wallet_checksum_before, wallet_checksum_after, created_at
        )
        VALUES (
            $1, $2, $3, $4, $5,
            ($6::numeric / 100), ($7::numeric / 100), ($8::numeric / 100),
            $9, $10, $11, $12, $13
        )
        "#,
    )
    .bind(Uuid::new_v4())
    .bind(wallet_operation_id)
    .bind(before.wallet_id)
    .bind(before.capsuleer_id)
    .bind(entry_kind)
    .bind(delta_minor)
    .bind(before.isk_minor)
    .bind(after_minor)
    .bind(before.wallet_version)
    .bind(after_version)
    .bind(&before.wallet_checksum)
    .bind(&after_checksum)
    .bind(at)
    .execute(tx_conn(tx))
    .await?;

    Ok(row)
}

async fn credit_wallet_escrow(
    tx: &mut DbTx<'_>,
    wallet_escrow_id: Uuid,
    amount_minor: i64,
    at: DateTime<Utc>,
) -> Result<WalletEscrowRow, SettlementError> {
    Ok(sqlx::query_as::<_, WalletEscrowRow>(
        r#"
        UPDATE wallet_escrow
        SET isk_amount = isk_amount + ($2::numeric / 100), updated_at = $3
        WHERE wallet_escrow_id = $1
        RETURNING wallet_escrow_id, trade_instance_id,
                  (isk_amount * 100)::bigint AS isk_minor,
                  owner_id, created_wallet_operation_id, released_wallet_operation_id,
                  created_at, updated_at, released_at
        "#,
    )
    .bind(wallet_escrow_id)
    .bind(amount_minor)
    .bind(at)
    .fetch_one(tx_conn(tx))
    .await?)
}

async fn debit_wallet_escrow(
    tx: &mut DbTx<'_>,
    wallet_escrow_id: Uuid,
    released_wallet_operation_id: Uuid,
    amount_minor: i64,
    at: DateTime<Utc>,
) -> Result<WalletEscrowRow, SettlementError> {
    Ok(sqlx::query_as::<_, WalletEscrowRow>(
        r#"
        UPDATE wallet_escrow
        SET isk_amount = isk_amount - ($2::numeric / 100),
            released_wallet_operation_id =
                CASE WHEN isk_amount - ($2::numeric / 100) = 0 THEN $3 ELSE released_wallet_operation_id END,
            released_at = CASE WHEN isk_amount - ($2::numeric / 100) = 0 THEN $4 ELSE released_at END,
            updated_at = $4
        WHERE wallet_escrow_id = $1
          AND isk_amount >= ($2::numeric / 100)
        RETURNING wallet_escrow_id, trade_instance_id,
                  (isk_amount * 100)::bigint AS isk_minor,
                  owner_id, created_wallet_operation_id, released_wallet_operation_id,
                  created_at, updated_at, released_at
        "#,
    )
    .bind(wallet_escrow_id)
    .bind(amount_minor)
    .bind(released_wallet_operation_id)
    .bind(at)
    .fetch_one(tx_conn(tx))
    .await?)
}

fn ensure_wallet_escrow_can_debit(
    escrow: &WalletEscrowRow,
    amount_minor: i64,
) -> Result<(), SettlementError> {
    if escrow.isk_minor < amount_minor {
        return Err(SettlementError::InsufficientIsk {
            wallet_id: escrow.wallet_escrow_id.to_string(),
        });
    }
    Ok(())
}
