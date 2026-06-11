//! Database boundary for trade-settlement.
//!
//! What this module contains:
//! - The public database API used by `service.rs`.
//! - Pool initialization and access.
//! - Submodules split by correctness responsibility.
//!
//! How it works:
//! - Service code calls these public async functions.
//! - Each write function owns one SQL transaction and commits only after all
//!   operation, idempotency, ledger, and state rows are consistent.
//!
//! Why it exists:
//! - This module is the anti-corruption boundary between gRPC transport and the
//!   shared PostgreSQL database. It keeps database correctness centralized.

// DB-BLOCK src_db_mod_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for public DB boundary, pool initialization, and service-facing entrypoints.
// Why: explicit imports make coupling visible during review.
use std::sync::OnceLock;

use sqlx::{PgPool, postgres::PgPoolOptions};

use crate::error::SettlementError;
use crate::generated::settlement::v1::*;

// DB-BLOCK src_db_mod_002
// What: exposes the `checksums` submodule.
// How: makes `checksums.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod checksums;
// DB-BLOCK src_db_mod_003
// What: exposes the `claims` submodule.
// How: makes `claims.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod claims;
// DB-BLOCK src_db_mod_004
// What: exposes the `extract` submodule.
// How: makes `extract.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod extract;
// DB-BLOCK src_db_mod_005
// What: exposes the `idempotency` submodule.
// How: makes `idempotency.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod idempotency;
// DB-BLOCK src_db_mod_006
// What: exposes the `operation_log` submodule.
// How: makes `operation_log.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod operation_log;
// DB-BLOCK src_db_mod_007
// What: exposes the `orders` submodule.
// How: makes `orders.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod orders;
// DB-BLOCK src_db_mod_008
// What: exposes the `ownership` submodule.
// How: makes `ownership.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod ownership;
// DB-BLOCK src_db_mod_009
// What: exposes the `proto_builders` submodule.
// How: makes `proto_builders.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod proto_builders;
// DB-BLOCK src_db_mod_010
// What: exposes the `queries` submodule.
// How: makes `queries.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod queries;
// DB-BLOCK src_db_mod_011
// What: exposes the `rows` submodule.
// How: makes `rows.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod rows;
// DB-BLOCK src_db_mod_012
// What: exposes the `settlements` submodule.
// How: makes `settlements.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod settlements;
// DB-BLOCK src_db_mod_013
// What: exposes the `time` submodule.
// How: makes `time.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod time;
// DB-BLOCK src_db_mod_014
// What: exposes the `types` submodule.
// How: makes `types.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod types;

// DB-BLOCK src_db_mod_015
// What: defines process-wide database state.
// How: uses `OnceLock<PgPool>` so initialization happens once and later reads are safe.
// Why: a single shared pool avoids hidden connection storms and startup races.
static DB_POOL: OnceLock<PgPool> = OnceLock::new();

// DB-BLOCK src_db_mod_016
// What: initializes the shared PostgreSQL pool before serving RPCs.
// How: constructs a bounded `PgPool` with explicit connection limits and stores it in `OnceLock`.
// Why: the service must fail fast if durable storage is not available before accepting settlement requests.
pub async fn initialize_pool(database_url: &str) -> Result<(), SettlementError> {
    // DB-BLOCK src_db_mod_017
    // What: binds `pool` as a named intermediate.
    // How: computes/extracts `pool` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let pool = PgPoolOptions::new()
        .max_connections(10)
        .min_connections(1)
        .acquire_timeout(std::time::Duration::from_secs(5))
        .connect(database_url)
        .await?;
    DB_POOL.set(pool).map_err(|_| SettlementError::PoolAlreadyInitialized)?;
    // DB-BLOCK src_db_mod_018
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(())`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(())
}

// DB-BLOCK src_db_mod_019
// What: returns the initialized process-wide pool.
// How: reads `DB_POOL` and maps missing initialization to `PoolNotInitialized`.
// Why: handlers need a safe shared pool without silently creating extra pools.
pub fn pool() -> Result<&'static PgPool, SettlementError> {
    DB_POOL.get().ok_or(SettlementError::PoolNotInitialized)
}

// DB-BLOCK src_db_mod_020
// What: opens or replays a durable trade order request.
// How: delegates to the order workflow that validates, idempotency-checks, reserves assets if needed, and commits.
// Why: order creation is a write boundary and must be centralized.
pub async fn open_trade_order(pool: &PgPool, req: &OpenTradeOrderRequest) -> Result<OpenTradeOrderResult, SettlementError> {
    orders::open_trade_order(pool, req).await
}

// DB-BLOCK src_db_mod_021
// What: closes a trade order with a requested terminal state.
// How: delegates to the order workflow that locks the order and writes a valid close result.
// Why: cancel/expire/fail transitions must be durable and replay-safe.
pub async fn close_trade_order(pool: &PgPool, req: &CloseTradeOrderRequest) -> Result<CloseTradeOrderResult, SettlementError> {
    orders::close_trade_order(pool, req).await
}

// DB-BLOCK src_db_mod_022
// What: performs the market-to-settlement DB transaction.
// How: validates request fields, claims idempotency, locks order/transaction/ownership rows, moves ISK/items, writes ledgers, records settlement state, and commits once.
// Why: this is the correctness-critical path; duplicate or partial ownership movement would corrupt the world state.
pub async fn request_settlement(pool: &PgPool, req: &SettlementRequest) -> Result<SettlementResult, SettlementError> {
    settlements::request_settlement(pool, req).await
}

// DB-BLOCK src_db_mod_023
// What: handles claim-result requests at the DB boundary.
// How: rejects unsupported claimable-delivery flow for MVP with a typed error.
// Why: unsafe partial implementation is worse than explicit unsupported behavior.
pub async fn claim_result(pool: &PgPool, req: &ClaimResultRequest) -> Result<ClaimResultResponse, SettlementError> {
    claims::claim_result(pool, req).await
}

// DB-BLOCK src_db_mod_024
// What: loads one durable trade order.
// How: extracts the request ID and maps the row into a protobuf response.
// Why: read APIs should not duplicate SQL or bypass the DB boundary.
pub async fn get_trade_order(pool: &PgPool, req: &GetTradeOrderRequest) -> Result<GetTradeOrderResponse, SettlementError> {
    orders::get_trade_order(pool, req).await
}

// DB-BLOCK src_db_mod_025
// What: lists outstanding orders with optional filters.
// How: extracts filter fields, runs a paginated query, and builds protobuf views.
// Why: market/gateway need controlled read access to order state.
pub async fn list_outstanding_trade_orders(pool: &PgPool, req: &ListOutstandingTradeOrdersRequest) -> Result<ListOutstandingTradeOrdersResponse, SettlementError> {
    orders::list_outstanding_trade_orders(pool, req).await
}

// DB-BLOCK src_db_mod_026
// What: returns transaction state and related settlement if present.
// How: loads trade_transaction and optional settlement rows in one read transaction.
// Why: callers need state visibility after asynchronous/retried settlement attempts.
pub async fn get_transaction_state(pool: &PgPool, req: &GetTransactionStateRequest) -> Result<GetTransactionStateResponse, SettlementError> {
    settlements::get_transaction_state(pool, req).await
}

// DB-BLOCK src_db_mod_027
// What: returns settlement details and step history.
// How: loads settlement by ID and maps settlement_step rows to protobuf.
// Why: phase/step history is needed for crash diagnosis and operator confidence.
pub async fn get_settlement(pool: &PgPool, req: &GetSettlementRequest) -> Result<GetSettlementResponse, SettlementError> {
    settlements::get_settlement(pool, req).await
}

// DB-BLOCK src_db_mod_028
// What: returns the operation audit root.
// How: extracts operation_id, loads operation row, and maps it to OperationView.
// Why: multi-table mutations need a single traceable parent record.
pub async fn get_operation(pool: &PgPool, req: &GetOperationRequest) -> Result<GetOperationResponse, SettlementError> {
    // DB-BLOCK src_db_mod_029
    // What: binds `operation_id` as a named intermediate.
    // How: computes/extracts `operation_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let operation_id = req.operation_id.as_ref().map(|x| x.value.clone()).filter(|x| !x.is_empty()).ok_or_else(|| SettlementError::InvalidRequest("operation_id is required".to_string()))?;
    // DB-BLOCK src_db_mod_030
    // What: opens a SQL transaction.
    // How: calls `pool.begin()` and passes the transaction through subsequent DB work.
    // Why: related writes must commit or roll back as one atomic unit.
    let mut tx = pool.begin().await?;
    // DB-BLOCK src_db_mod_031
    // What: binds `operation` as a named intermediate.
    // How: computes/extracts `operation` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let operation = operation_log::load(&mut tx, &operation_id).await?;
    tx.commit().await?;
    // DB-BLOCK src_db_mod_032
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(GetOperationResponse { operation: Some(proto_builders::operation_view(operati`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(GetOperationResponse { operation: Some(proto_builders::operation_view(operation)?) })
}
