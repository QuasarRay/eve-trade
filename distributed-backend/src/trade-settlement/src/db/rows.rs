//! SQL row structs used by the DB layer.
//!
//! What this file contains:
//! - `sqlx::FromRow` structs matching selected SQL result shapes.
//!
//! How it works:
//! - UUIDs are selected as text using `uuid_column::text AS uuid_column`.
//! - This keeps generated protobuf wrapper construction simple and avoids binding
//!   generated ID wrappers directly into SQLx.
//!
//! Why it exists:
//! - Query code should return typed rows instead of anonymous tuples.
//! - View mapping should not know SQL details.

// DB-BLOCK src_db_rows_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for sqlx row structs matching selected columns.
// Why: explicit imports make coupling visible during review.
use chrono::{DateTime, Utc};

// DB-BLOCK src_db_rows_002
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone, sqlx::FromRow)]
// DB-BLOCK src_db_rows_003
// What: defines the `OperationRow` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct OperationRow {
    pub operation_id: String,
    pub operation_kind: String,
    pub source_system: String,
    pub external_operation_id: Option<String>,
    pub request_id: Option<String>,
    pub idempotency_key: Option<String>,
    pub caused_by_capsuleer_id: Option<String>,
    pub operation_state: String,
    pub created_by_service: String,
    pub started_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
    pub failure_code: Option<String>,
    pub failure_message: Option<String>,
}

// DB-BLOCK src_db_rows_004
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone, sqlx::FromRow)]
// DB-BLOCK src_db_rows_005
// What: defines the `IdempotencyResultRow` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct IdempotencyResultRow {
    pub idempotency_key: String,
    pub operation_id: Option<String>,
    pub result_kind: String,
    pub trade_order_id: Option<String>,
    pub trade_transaction_id: Option<String>,
    pub settlement_id: Option<String>,
    pub wallet_operation_id: Option<String>,
    pub item_stack_operation_id: Option<String>,
    pub item_instance_operation_id: Option<String>,
    pub result_state: String,
    pub failure_code: Option<String>,
}

// DB-BLOCK src_db_rows_006
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone, sqlx::FromRow)]
// DB-BLOCK src_db_rows_007
// What: defines the `WalletRow` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct WalletRow {
    pub wallet_id: String,
    pub capsuleer_id: Option<String>,
    pub wallet_kind: String,
    pub available_isk: i64,
    pub reserved_isk: i64,
    pub wallet_state: String,
    pub wallet_version: i64,
    pub wallet_checksum: String,
}

// DB-BLOCK src_db_rows_008
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone, sqlx::FromRow)]
// DB-BLOCK src_db_rows_009
// What: defines the `ItemStackRow` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct ItemStackRow {
    pub item_stack_id: String,
    pub capsuleer_id: String,
    pub item_type_id: String,
    pub station_id: String,
    pub available_quantity: i64,
    pub reserved_quantity: i64,
    pub stack_state: String,
    pub stack_version: i64,
    pub stack_checksum: String,
}

// DB-BLOCK src_db_rows_010
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone, sqlx::FromRow)]
// DB-BLOCK src_db_rows_011
// What: defines the `TradeOrderRow` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct TradeOrderRow {
    pub trade_order_id: String,
    pub operation_id: String,
    pub order_side: String,
    pub state: String,
    pub owner_capsuleer_id: String,
    pub owner_wallet_id: String,
    pub item_type_id: String,
    pub offered_item_stack_id: Option<String>,
    pub offered_item_instance_id: Option<String>,
    pub station_id: String,
    pub region_id: String,
    pub total_quantity: i64,
    pub remaining_quantity: i64,
    pub unit_price_isk: i64,
    pub expires_at: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

// DB-BLOCK src_db_rows_012
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone, sqlx::FromRow)]
// DB-BLOCK src_db_rows_013
// What: defines the `WalletReservationRow` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct WalletReservationRow {
    pub wallet_reservation_id: String,
    pub trade_order_id: String,
    pub wallet_id: String,
    pub created_wallet_operation_id: String,
    pub released_wallet_operation_id: Option<String>,
    pub original_reserved_isk: i64,
    pub remaining_reserved_isk: i64,
    pub used_reserved_isk: i64,
    pub released_reserved_isk: i64,
    pub reservation_state: String,
    pub release_reason: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub released_at: Option<DateTime<Utc>>,
}

// DB-BLOCK src_db_rows_014
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone, sqlx::FromRow)]
// DB-BLOCK src_db_rows_015
// What: defines the `ItemStackReservationRow` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct ItemStackReservationRow {
    pub item_stack_reservation_id: String,
    pub trade_order_id: String,
    pub item_stack_id: String,
    pub created_item_stack_operation_id: String,
    pub released_item_stack_operation_id: Option<String>,
    pub original_reserved_quantity: i64,
    pub remaining_reserved_quantity: i64,
    pub used_reserved_quantity: i64,
    pub released_reserved_quantity: i64,
    pub reservation_state: String,
    pub release_reason: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub released_at: Option<DateTime<Utc>>,
}

// DB-BLOCK src_db_rows_016
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone, sqlx::FromRow)]
// DB-BLOCK src_db_rows_017
// What: defines the `TradeTransactionRow` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct TradeTransactionRow {
    pub trade_transaction_id: String,
    pub operation_id: String,
    pub trade_order_id: String,
    pub state: String,
    pub buyer_capsuleer_id: String,
    pub buyer_wallet_id: String,
    pub seller_capsuleer_id: String,
    pub seller_wallet_id: String,
    pub item_type_id: String,
    pub source_item_stack_id: Option<String>,
    pub destination_item_stack_id: Option<String>,
    pub source_item_instance_id: Option<String>,
    pub destination_item_instance_id: Option<String>,
    pub quantity: i64,
    pub unit_price_isk: i64,
    pub total_price_isk: i64,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

// DB-BLOCK src_db_rows_018
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone, sqlx::FromRow)]
// DB-BLOCK src_db_rows_019
// What: defines the `SettlementRow` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct SettlementRow {
    pub settlement_id: String,
    pub operation_id: String,
    pub trade_transaction_id: String,
    pub idempotency_key: String,
    pub state: String,
    pub settlement_phase: String,
    pub retry_count: i32,
    pub started_at: DateTime<Utc>,
    pub decided_at: Option<DateTime<Utc>>,
    pub failure_code: Option<String>,
    pub failure_message: Option<String>,
}

// DB-BLOCK src_db_rows_020
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone, sqlx::FromRow)]
// DB-BLOCK src_db_rows_021
// What: defines the `SettlementStepRow` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct SettlementStepRow {
    pub settlement_step_id: String,
    pub settlement_id: String,
    pub step_name: String,
    pub step_state: String,
    pub started_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
    pub failure_code: Option<String>,
    pub failure_message: Option<String>,
}
