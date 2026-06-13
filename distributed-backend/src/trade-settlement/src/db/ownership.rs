//! Ownership mutation helpers.
//!
//! What this file contains:
//! - Wallet and item-stack locking/mutation functions.
//! - Wallet/item operation creation.
//! - Ledger writes paired with every mutable row update.
//!
//! How it works:
//! - Mutable rows are selected `FOR UPDATE` before decisions are made.
//! - Before/after balances are computed in Rust and rejected before SQL update
//!   if they would go negative.
//! - Updates include the previous version in the `WHERE` clause as a second
//!   stale-write guard, even though the row is already locked.
//! - A ledger row is inserted inside the same transaction.
//!
//! Why it exists:
//! - This is the structural protection layer for shared game/project ownership
//!   state. It does not decide gameplay permission. It only performs safe
//!   accounting once a service has requested a valid operation.

// DB-BLOCK src_db_ownership_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for wallet/stack ownership mutation, row locks, ledgers, versions, and checksums.
// Why: explicit imports make coupling visible during review.
use sqlx::{Postgres, Transaction};

use crate::db::checksums::{item_stack_checksum, wallet_checksum, ItemStackChecksumInput};
use crate::db::rows::{ItemStackRow, WalletRow};
use crate::error::SettlementError;

// DB-BLOCK src_db_ownership_002
// What: implements `create_wallet_operation`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn create_wallet_operation(
    tx: &mut Transaction<'_, Postgres>,
    operation_id: &str,
    operation_kind: &str,
) -> Result<String, SettlementError> {
    // DB-BLOCK src_db_ownership_003
    // What: binds `id` as a named intermediate.
    // How: computes/extracts `id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let id: (String,) = sqlx::query_as(
        r#"
        INSERT INTO trade.wallet_operation (operation_id, operation_kind, wallet_operation_state)
        VALUES ($1::uuid, $2, 'in_progress')
        RETURNING wallet_operation_id::text
        "#,
    )
    .bind(operation_id)
    .bind(operation_kind)
    .fetch_one(&mut **tx)
    .await?;
    // DB-BLOCK src_db_ownership_004
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(id.0)`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(id.0)
}

// DB-BLOCK src_db_ownership_005
// What: implements `create_stack_operation`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn create_stack_operation(
    tx: &mut Transaction<'_, Postgres>,
    operation_id: &str,
    operation_kind: &str,
) -> Result<String, SettlementError> {
    // DB-BLOCK src_db_ownership_006
    // What: binds `id` as a named intermediate.
    // How: computes/extracts `id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let id: (String,) = sqlx::query_as(
        r#"
        INSERT INTO trade.item_stack_operation (operation_id, operation_kind, item_stack_operation_state)
        VALUES ($1::uuid, $2, 'in_progress')
        RETURNING item_stack_operation_id::text
        "#,
    )
    .bind(operation_id)
    .bind(operation_kind)
    .fetch_one(&mut **tx)
    .await?;
    // DB-BLOCK src_db_ownership_007
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(id.0)`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(id.0)
}

// DB-BLOCK src_db_ownership_008
// What: implements `complete_wallet_operation`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn complete_wallet_operation(
    tx: &mut Transaction<'_, Postgres>,
    id: &str,
) -> Result<(), SettlementError> {
    // DB-BLOCK src_db_ownership_009
    // What: performs a parameterized SQL operation against `wallet_operation`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query("UPDATE trade.wallet_operation SET wallet_operation_state = 'completed', completed_at = now() WHERE wallet_operation_id = $1::uuid")
        .bind(id)
        .execute(&mut **tx)
        .await?;
    // DB-BLOCK src_db_ownership_010
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(())`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(())
}

// DB-BLOCK src_db_ownership_011
// What: implements `complete_stack_operation`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn complete_stack_operation(
    tx: &mut Transaction<'_, Postgres>,
    id: &str,
) -> Result<(), SettlementError> {
    // DB-BLOCK src_db_ownership_012
    // What: performs a parameterized SQL operation against `item_stack_operation`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query("UPDATE trade.item_stack_operation SET item_stack_operation_state = 'completed', completed_at = now() WHERE item_stack_operation_id = $1::uuid")
        .bind(id)
        .execute(&mut **tx)
        .await?;
    // DB-BLOCK src_db_ownership_013
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(())`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(())
}

// DB-BLOCK src_db_ownership_014
// What: implements `lock_wallet`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn lock_wallet(
    tx: &mut Transaction<'_, Postgres>,
    wallet_id: &str,
) -> Result<WalletRow, SettlementError> {
    // DB-BLOCK src_db_ownership_015
    // What: performs a parameterized SQL operation against `wallet`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query_as::<_, WalletRow>(
        r#"
        SELECT wallet_id::text AS wallet_id, owner_id::text AS owner_id,
               wallet_kind::text AS wallet_kind, available_isk, reserved_isk,
               wallet_state::text AS wallet_state, wallet_version, wallet_checksum
        FROM trade.wallet
        WHERE wallet_id = $1::uuid
        FOR UPDATE
        "#,
    )
    .bind(wallet_id)
    .fetch_one(&mut **tx)
    .await
    .map_err(SettlementError::from)
}

// DB-BLOCK src_db_ownership_016
// What: implements `lock_stack`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn lock_stack(
    tx: &mut Transaction<'_, Postgres>,
    stack_id: &str,
) -> Result<ItemStackRow, SettlementError> {
    // DB-BLOCK src_db_ownership_017
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query_as::<_, ItemStackRow>(
        r#"
        SELECT item_stack_id::text AS item_stack_id, owner_id::text AS owner_id,
               item_type_id::text AS item_type_id, station_id::text AS station_id,
               available_quantity, reserved_quantity, stack_state::text AS stack_state,
               stack_version, stack_checksum
        FROM trade.item_stack
        WHERE item_stack_id = $1::uuid
        FOR UPDATE
        "#,
    )
    .bind(stack_id)
    .fetch_one(&mut **tx)
    .await
    .map_err(SettlementError::from)
}

// DB-BLOCK src_db_ownership_018
// What: creates a destination stack shell for item credit.
// How: inserts an active zero-quantity stack for receiver/type/station and returns the row.
// Why: settlement can credit a receiver who does not already have a compatible stack.
pub async fn create_empty_stack(
    tx: &mut Transaction<'_, Postgres>,
    capsuleer_id: &str,
    item_type_id: &str,
    station_id: &str,
) -> Result<ItemStackRow, SettlementError> {
    // DB-BLOCK src_db_ownership_019
    // What: binds `id` as a named intermediate.
    // How: computes/extracts `id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let id: (String,) = sqlx::query_as(
        r#"
        INSERT INTO trade.item_stack (
            owner_id, item_type_id, station_id, available_quantity, reserved_quantity,
            stack_state, stack_version, stack_checksum, checksum_algorithm
        )
        VALUES ($1::uuid, $2::uuid, $3::uuid, 0, 0, 'active', 1, 'pending', 'sha256-v1')
        RETURNING item_stack_id::text
        "#,
    )
    .bind(capsuleer_id)
    .bind(item_type_id)
    .bind(station_id)
    .fetch_one(&mut **tx)
    .await?;

    // DB-BLOCK src_db_ownership_020
    // What: binds `checksum` as a named intermediate.
    // How: computes/extracts `checksum` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let checksum = item_stack_checksum(ItemStackChecksumInput {
        item_stack_id: &id.0,
        owner_id,
        item_type_id,
        station_id,
        available_quantity: 0,
        reserved_quantity: 0,
        stack_state: "active",
        stack_version: 1,
    });
    // DB-BLOCK src_db_ownership_021
    // What: performs a parameterized SQL operation against `item_stack`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query("UPDATE trade.item_stack SET stack_checksum = $2 WHERE item_stack_id = $1::uuid")
        .bind(&id.0)
        .bind(&checksum)
        .execute(&mut **tx)
        .await?;

    lock_stack(tx, &id.0).await
}

// DB-BLOCK src_db_ownership_022
// What: mutates wallet available/reserved ISK and writes the wallet ledger.
// How: locks the wallet, computes before/after balances, rejects negative results, increments version, updates checksum, updates row, and inserts wallet_ledger.
// Why: wallet movement must be atomic, auditable, and concurrency-safe.
pub async fn move_wallet(
    tx: &mut Transaction<'_, Postgres>,
    wallet_operation_id: &str,
    wallet_id: &str,
    available_delta: i64,
    reserved_delta: i64,
    entry_kind: &str,
) -> Result<WalletRow, SettlementError> {
    // DB-BLOCK src_db_ownership_023
    // What: binds `before` as a named intermediate.
    // How: computes/extracts `before` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let before = lock_wallet(tx, wallet_id).await?;
    // DB-BLOCK src_db_ownership_024
    // What: binds `after_available` as a named intermediate.
    // How: computes/extracts `after_available` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let after_available = before
        .available_isk
        .checked_add(available_delta)
        .ok_or_else(|| {
            SettlementError::IntegrityConflict("wallet available_isk overflow".to_string())
        })?;
    // DB-BLOCK src_db_ownership_025
    // What: binds `after_reserved` as a named intermediate.
    // How: computes/extracts `after_reserved` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let after_reserved = before
        .reserved_isk
        .checked_add(reserved_delta)
        .ok_or_else(|| {
            SettlementError::IntegrityConflict("wallet reserved_isk overflow".to_string())
        })?;
    // DB-BLOCK src_db_ownership_026
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if after_available < 0 || after_reserved < 0 {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if after_available < 0 || after_reserved < 0 {
        // DB-BLOCK src_db_ownership_027
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::InsufficientIsk { wallet_id: wallet_id.to_string() }` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::InsufficientIsk {
            wallet_id: wallet_id.to_string(),
        });
    }
    // DB-BLOCK src_db_ownership_028
    // What: binds `after_version` as a named intermediate.
    // How: computes/extracts `after_version` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let after_version = before.wallet_version + 1;
    // DB-BLOCK src_db_ownership_029
    // What: binds `after_checksum` as a named intermediate.
    // How: computes/extracts `after_checksum` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let after_checksum = wallet_checksum(
        &before.wallet_id,
        before.capsuleer_id.as_deref(),
        &before.wallet_kind,
        after_available,
        after_reserved,
        &before.wallet_state,
        after_version,
    );

    // DB-BLOCK src_db_ownership_030
    // What: binds `result` as a named intermediate.
    // How: computes/extracts `result` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let result = sqlx::query(
        r#"
        UPDATE trade.wallet
        SET available_isk = $2, reserved_isk = $3, wallet_version = $4,
            wallet_checksum = $5, checksum_algorithm = 'sha256-v1', updated_at = now()
        WHERE wallet_id = $1::uuid AND wallet_version = $6
        "#,
    )
    .bind(&before.wallet_id)
    .bind(after_available)
    .bind(after_reserved)
    .bind(after_version)
    .bind(&after_checksum)
    .bind(before.wallet_version)
    .execute(&mut **tx)
    .await?;
    // DB-BLOCK src_db_ownership_031
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if result.rows_affected() != 1 {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if result.rows_affected() != 1 {
        // DB-BLOCK src_db_ownership_032
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::StaleVersionConflict(format!("wallet {} version chan` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::StaleVersionConflict(format!(
            "wallet {} version changed unexpectedly",
            before.wallet_id
        )));
    }

    // DB-BLOCK src_db_ownership_033
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        INSERT INTO trade.wallet_ledger (
          wallet_operation_id, wallet_id, owner_id, entry_kind,
          available_isk_delta, reserved_isk_delta,
          available_isk_before, reserved_isk_before,
          available_isk_after, reserved_isk_after,
          wallet_version_before, wallet_version_after,
          wallet_checksum_before, wallet_checksum_after
        ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        "#,
    )
    .bind(wallet_operation_id)
    .bind(&before.wallet_id)
    .bind(&before.capsuleer_id)
    .bind(entry_kind)
    .bind(available_delta)
    .bind(reserved_delta)
    .bind(before.available_isk)
    .bind(before.reserved_isk)
    .bind(after_available)
    .bind(after_reserved)
    .bind(before.wallet_version)
    .bind(after_version)
    .bind(&before.wallet_checksum)
    .bind(&after_checksum)
    .execute(&mut **tx)
    .await?;

    // DB-BLOCK src_db_ownership_034
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(WalletRow { available_isk: after_available, reserved_isk: after_reserved, wal`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(WalletRow {
        available_isk: after_available,
        reserved_isk: after_reserved,
        wallet_version: after_version,
        wallet_checksum: after_checksum,
        ..before
    })
}

// DB-BLOCK src_db_ownership_035
// What: mutates item-stack available/reserved quantity and writes the stack ledger.
// How: locks the stack, computes before/after quantities, rejects negative results, increments version, updates checksum, updates row, and inserts item_stack_ledger.
// Why: item quantity movement must prevent double spending and preserve inventory audit history.
pub async fn move_stack(
    tx: &mut Transaction<'_, Postgres>,
    item_stack_operation_id: &str,
    stack_id: &str,
    available_delta: i64,
    reserved_delta: i64,
    entry_kind: &str,
) -> Result<ItemStackRow, SettlementError> {
    // DB-BLOCK src_db_ownership_036
    // What: binds `before` as a named intermediate.
    // How: computes/extracts `before` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let before = lock_stack(tx, stack_id).await?;
    // DB-BLOCK src_db_ownership_037
    // What: binds `after_available` as a named intermediate.
    // How: computes/extracts `after_available` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let after_available = before
        .available_quantity
        .checked_add(available_delta)
        .ok_or_else(|| {
            SettlementError::IntegrityConflict("stack available_quantity overflow".to_string())
        })?;
    // DB-BLOCK src_db_ownership_038
    // What: binds `after_reserved` as a named intermediate.
    // How: computes/extracts `after_reserved` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let after_reserved = before
        .reserved_quantity
        .checked_add(reserved_delta)
        .ok_or_else(|| {
            SettlementError::IntegrityConflict("stack reserved_quantity overflow".to_string())
        })?;
    // DB-BLOCK src_db_ownership_039
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if after_available < 0 || after_reserved < 0 {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if after_available < 0 || after_reserved < 0 {
        // DB-BLOCK src_db_ownership_040
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::InsufficientItems { item_stack_id: stack_id.to_strin` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::InsufficientItems {
            item_stack_id: stack_id.to_string(),
        });
    }
    // DB-BLOCK src_db_ownership_041
    // What: binds `after_version` as a named intermediate.
    // How: computes/extracts `after_version` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let after_version = before.stack_version + 1;
    // DB-BLOCK src_db_ownership_042
    // What: binds `after_state` as a named intermediate.
    // How: computes/extracts `after_state` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let after_state = if after_available == 0 && after_reserved == 0 {
        "depleted"
    } else {
        before.stack_state.as_str()
    };
    // DB-BLOCK src_db_ownership_043
    // What: binds `after_checksum` as a named intermediate.
    // How: computes/extracts `after_checksum` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let after_checksum = item_stack_checksum(ItemStackChecksumInput {
        item_stack_id: &before.item_stack_id,
        capsuleer_id: &before.owner_id,
        item_type_id: &before.item_type_id,
        station_id: &before.station_id,
        available_quantity: after_available,
        reserved_quantity: after_reserved,
        stack_state: after_state,
        stack_version: after_version,
    });

    // DB-BLOCK src_db_ownership_044
    // What: binds `result` as a named intermediate.
    // How: computes/extracts `result` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let result = sqlx::query(
        r#"
        UPDATE trade.item_stack
        SET available_quantity = $2, reserved_quantity = $3, stack_state = $4::trade.stack_state,
            stack_version = $5, stack_checksum = $6, checksum_algorithm = 'sha256-v1', updated_at = now()
        WHERE item_stack_id = $1::uuid AND stack_version = $7
        "#,
    )
    .bind(&before.item_stack_id)
    .bind(after_available)
    .bind(after_reserved)
    .bind(after_state)
    .bind(after_version)
    .bind(&after_checksum)
    .bind(before.stack_version)
    .execute(&mut **tx)
    .await?;
    // DB-BLOCK src_db_ownership_045
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if result.rows_affected() != 1 {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if result.rows_affected() != 1 {
        // DB-BLOCK src_db_ownership_046
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::StaleVersionConflict(format!("item stack {} version ` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::StaleVersionConflict(format!(
            "item stack {} version changed unexpectedly",
            before.item_stack_id
        )));
    }

    // DB-BLOCK src_db_ownership_047
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        INSERT INTO trade.item_stack_ledger (
          item_stack_operation_id, item_stack_id, item_type_id, owner_id, station_id, entry_kind,
          available_quantity_delta, reserved_quantity_delta,
          available_quantity_before, reserved_quantity_before,
          available_quantity_after, reserved_quantity_after,
          stack_version_before, stack_version_after,
          stack_checksum_before, stack_checksum_after
        ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
        "#,
    )
    .bind(item_stack_operation_id)
    .bind(&before.item_stack_id)
    .bind(&before.item_type_id)
    .bind(&before.capsuleer_id)
    .bind(&before.station_id)
    .bind(entry_kind)
    .bind(available_delta)
    .bind(reserved_delta)
    .bind(before.available_quantity)
    .bind(before.reserved_quantity)
    .bind(after_available)
    .bind(after_reserved)
    .bind(before.stack_version)
    .bind(after_version)
    .bind(&before.stack_checksum)
    .bind(&after_checksum)
    .execute(&mut **tx)
    .await?;

    // DB-BLOCK src_db_ownership_048
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(ItemStackRow { available_quantity: after_available, reserved_quantity: after_`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(ItemStackRow {
        available_quantity: after_available,
        reserved_quantity: after_reserved,
        stack_state: after_state.to_string(),
        stack_version: after_version,
        stack_checksum: after_checksum,
        ..before
    })
}
