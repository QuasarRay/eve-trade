//! Shared read/lock queries.
//!
//! What this file contains:
//! - Reusable loaders for trade orders, transactions, settlements, reservations,
//!   and operations.
//!
//! How it works:
//! - Locking functions use `FOR UPDATE` and are used only inside write transactions.
//! - Non-locking functions are used by read APIs.
//!
//! Why it exists:
//! - Keeping query shapes centralized prevents subtly different read semantics in
//!   order, settlement, and claim modules.

// DB-BLOCK src_db_queries_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for shared locked/read SQL queries.
// Why: explicit imports make coupling visible during review.
use sqlx::{Postgres, Transaction};

use crate::db::rows::*;
use crate::error::SettlementError;

// DB-BLOCK src_db_queries_002
// What: implements `lock_order`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn lock_order(tx: &mut Transaction<'_, Postgres>, id: &str) -> Result<TradeOrderRow, SettlementError> {
    order_query("FOR UPDATE", tx, id).await
}

// DB-BLOCK src_db_queries_003
// What: implements `load_order`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn load_order(tx: &mut Transaction<'_, Postgres>, id: &str) -> Result<TradeOrderRow, SettlementError> {
    order_query("", tx, id).await
}

// DB-BLOCK src_db_queries_004
// What: implements `order_query`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
async fn order_query(lock: &str, tx: &mut Transaction<'_, Postgres>, id: &str) -> Result<TradeOrderRow, SettlementError> {
    // DB-BLOCK src_db_queries_005
    // What: binds `sql` as a named intermediate.
    // How: computes/extracts `sql` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let sql = format!(r#"
        SELECT trade_order_id::text AS trade_order_id, operation_id::text AS operation_id,
               order_side::text AS order_side, state::text AS state,
               owner_capsuleer_id::text AS owner_capsuleer_id, owner_wallet_id::text AS owner_wallet_id,
               item_type_id::text AS item_type_id, offered_item_stack_id::text AS offered_item_stack_id,
               offered_item_instance_id::text AS offered_item_instance_id,
               station_id::text AS station_id, region_id::text AS region_id,
               total_quantity, remaining_quantity, unit_price_isk, expires_at, created_at, updated_at
        FROM trade.trade_order
        WHERE trade_order_id = $1::uuid
        {lock}
    "#);
    // DB-BLOCK src_db_queries_006
    // What: performs a parameterized SQL operation against `settlement`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query_as::<_, TradeOrderRow>(&sql).bind(id).fetch_one(&mut **tx).await.map_err(SettlementError::from)
}

// DB-BLOCK src_db_queries_007
// What: implements `lock_wallet_reservation`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn lock_wallet_reservation(tx: &mut Transaction<'_, Postgres>, order_id: &str) -> Result<Option<WalletReservationRow>, SettlementError> {
    // DB-BLOCK src_db_queries_008
    // What: performs a parameterized SQL operation against `wallet`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query_as::<_, WalletReservationRow>(
        r#"
        SELECT wallet_reservation_id::text AS wallet_reservation_id, trade_order_id::text AS trade_order_id,
               wallet_id::text AS wallet_id, created_wallet_operation_id::text AS created_wallet_operation_id,
               released_wallet_operation_id::text AS released_wallet_operation_id,
               original_reserved_isk, remaining_reserved_isk, used_reserved_isk, released_reserved_isk,
               reservation_state::text AS reservation_state, release_reason, created_at, updated_at, released_at
        FROM trade.wallet_reservation
        WHERE trade_order_id = $1::uuid
        FOR UPDATE
        "#,
    )
    .bind(order_id)
    .fetch_optional(&mut **tx)
    .await
    .map_err(SettlementError::from)
}

// DB-BLOCK src_db_queries_009
// What: implements `lock_stack_reservation`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn lock_stack_reservation(tx: &mut Transaction<'_, Postgres>, order_id: &str) -> Result<Option<ItemStackReservationRow>, SettlementError> {
    // DB-BLOCK src_db_queries_010
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query_as::<_, ItemStackReservationRow>(
        r#"
        SELECT item_stack_reservation_id::text AS item_stack_reservation_id, trade_order_id::text AS trade_order_id,
               item_stack_id::text AS item_stack_id, created_item_stack_operation_id::text AS created_item_stack_operation_id,
               released_item_stack_operation_id::text AS released_item_stack_operation_id,
               original_reserved_quantity, remaining_reserved_quantity, used_reserved_quantity, released_reserved_quantity,
               reservation_state::text AS reservation_state, release_reason, created_at, updated_at, released_at
        FROM trade.item_stack_reservation
        WHERE trade_order_id = $1::uuid
        FOR UPDATE
        "#,
    )
    .bind(order_id)
    .fetch_optional(&mut **tx)
    .await
    .map_err(SettlementError::from)
}

// DB-BLOCK src_db_queries_011
// What: implements `lock_transaction`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn lock_transaction(tx: &mut Transaction<'_, Postgres>, id: &str) -> Result<Option<TradeTransactionRow>, SettlementError> {
    // DB-BLOCK src_db_queries_012
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query_as::<_, TradeTransactionRow>(
        r#"
        SELECT trade_transaction_id::text AS trade_transaction_id, operation_id::text AS operation_id,
               trade_order_id::text AS trade_order_id, state::text AS state,
               buyer_capsuleer_id::text AS buyer_capsuleer_id, buyer_wallet_id::text AS buyer_wallet_id,
               seller_capsuleer_id::text AS seller_capsuleer_id, seller_wallet_id::text AS seller_wallet_id,
               item_type_id::text AS item_type_id, source_item_stack_id::text AS source_item_stack_id,
               destination_item_stack_id::text AS destination_item_stack_id,
               source_item_instance_id::text AS source_item_instance_id,
               destination_item_instance_id::text AS destination_item_instance_id,
               quantity, unit_price_isk, total_price_isk, created_at, updated_at, completed_at
        FROM trade.trade_transaction
        WHERE trade_transaction_id = $1::uuid
        FOR UPDATE
        "#,
    )
    .bind(id)
    .fetch_optional(&mut **tx)
    .await
    .map_err(SettlementError::from)
}

// DB-BLOCK src_db_queries_013
// What: implements `load_transaction`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn load_transaction(tx: &mut Transaction<'_, Postgres>, id: &str) -> Result<TradeTransactionRow, SettlementError> {
    // DB-BLOCK src_db_queries_014
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query_as::<_, TradeTransactionRow>(
        r#"
        SELECT trade_transaction_id::text AS trade_transaction_id, operation_id::text AS operation_id,
               trade_order_id::text AS trade_order_id, state::text AS state,
               buyer_capsuleer_id::text AS buyer_capsuleer_id, buyer_wallet_id::text AS buyer_wallet_id,
               seller_capsuleer_id::text AS seller_capsuleer_id, seller_wallet_id::text AS seller_wallet_id,
               item_type_id::text AS item_type_id, source_item_stack_id::text AS source_item_stack_id,
               destination_item_stack_id::text AS destination_item_stack_id,
               source_item_instance_id::text AS source_item_instance_id,
               destination_item_instance_id::text AS destination_item_instance_id,
               quantity, unit_price_isk, total_price_isk, created_at, updated_at, completed_at
        FROM trade.trade_transaction
        WHERE trade_transaction_id = $1::uuid
        "#,
    )
    .bind(id)
    .fetch_one(&mut **tx)
    .await
    .map_err(SettlementError::from)
}

// DB-BLOCK src_db_queries_015
// What: implements `load_settlement`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn load_settlement(tx: &mut Transaction<'_, Postgres>, settlement_id: &str) -> Result<SettlementRow, SettlementError> {
    // DB-BLOCK src_db_queries_016
    // What: performs a parameterized SQL operation against `settlement`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query_as::<_, SettlementRow>(
        r#"
        SELECT settlement_id::text AS settlement_id, operation_id::text AS operation_id,
               trade_transaction_id::text AS trade_transaction_id, idempotency_key,
               state::text AS state, settlement_phase::text AS settlement_phase, retry_count,
               started_at, decided_at, failure_code, failure_message
        FROM trade.settlement
        WHERE settlement_id = $1::uuid
        "#,
    )
    .bind(settlement_id)
    .fetch_one(&mut **tx)
    .await
    .map_err(SettlementError::from)
}

// DB-BLOCK src_db_queries_017
// What: implements `settlement_steps`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn settlement_steps(tx: &mut Transaction<'_, Postgres>, settlement_id: &str) -> Result<Vec<SettlementStepRow>, SettlementError> {
    // DB-BLOCK src_db_queries_018
    // What: performs a parameterized SQL operation against `settlement`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query_as::<_, SettlementStepRow>(
        r#"
        SELECT settlement_step_id::text AS settlement_step_id, settlement_id::text AS settlement_id,
               step_name, step_state::text AS step_state, started_at, completed_at, failure_code, failure_message
        FROM trade.settlement_step
        WHERE settlement_id = $1::uuid
        ORDER BY started_at ASC
        "#,
    )
    .bind(settlement_id)
    .fetch_all(&mut **tx)
    .await
    .map_err(SettlementError::from)
}
