//! Protobuf response construction.
//!
//! What this file contains:
//! - Small constructors for generated protobuf wrapper/view messages.
//!
//! How it works:
//! - DB rows are converted into transport views at the boundary.
//! - Enum fields are assigned with stable numeric constants from `types::proto_i32`.
//!
//! Why it exists:
//! - SQL modules should not be cluttered with transport construction details.
//! - The DB layer can evolve internally while returning stable gRPC responses.

// DB-BLOCK src_db_proto_builders_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for SQL row to protobuf response mapping.
// Why: explicit imports make coupling visible during review.
use crate::db::rows::*;
use crate::db::time;
use crate::db::types::{proto_i32, OrderSide, TradeState};
use crate::error::SettlementError;
use crate::generated::trade::v1::*;

// DB-BLOCK src_db_proto_builders_002
// What: implements `id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn id<T>(value: String, make: impl FnOnce(String) -> T) -> Option<T> {
    Some(make(value))
}
// DB-BLOCK src_db_proto_builders_003
// What: implements `isk`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn isk(value: i64) -> Option<IskAmount> {
    Some(IskAmount { minor_units: value })
}
// DB-BLOCK src_db_proto_builders_004
// What: implements `qty`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn qty(value: i64) -> Option<Quantity> {
    Some(Quantity {
        units: value as u64,
    })
}

// DB-BLOCK src_db_proto_builders_005
// What: implements `trade_error`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn trade_error(code: i32, message: impl Into<String>) -> TradeError {
    TradeError {
        code,
        message: message.into(),
        request_id: None,
        idempotency_key: None,
        trade_order_id: None,
        trade_transaction_id: None,
        settlement_id: None,
    }
}

// DB-BLOCK src_db_proto_builders_006
// What: implements `operation_view`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn operation_view(row: OperationRow) -> Result<OperationView, SettlementError> {
    // DB-BLOCK src_db_proto_builders_007
    // What: binds `operation_state` as a named intermediate.
    // How: computes/extracts `operation_state` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let operation_state = match row.operation_state.as_str() {
        "pending" => proto_i32::OPERATION_PENDING,
        "in_progress" => proto_i32::OPERATION_IN_PROGRESS,
        "completed" => proto_i32::OPERATION_COMPLETED,
        "failed" => proto_i32::OPERATION_FAILED,
        other => {
            return Err(SettlementError::IntegrityConflict(format!(
                "unknown operation state {other}"
            )))
        }
    };
    // DB-BLOCK src_db_proto_builders_008
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(OperationView {`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(OperationView {
        operation_id: id(row.operation_id, |value| OperationId { value }),
        operation_kind: operation_kind_proto_i32(&row.operation_kind),
        source_system: row.source_system,
        external_operation_id: row.external_operation_id.unwrap_or_default(),
        request_id: row.request_id.map(|value| RequestId { value }),
        idempotency_key: row.idempotency_key.map(|value| IdempotencyKey { value }),
        caused_by_capsuleer_id: row
            .caused_by_capsuleer_id
            .map(|value| CapsuleerId { value }),
        operation_state,
        created_by_service: row.created_by_service,
        started_at: Some(time::to_proto(row.started_at)),
        completed_at: time::to_proto_opt(row.completed_at),
        failure: row
            .failure_message
            .map(|m| trade_error(proto_i32::ERROR_SETTLEMENT_CONFLICT, m)),
    })
}

// DB-BLOCK src_db_proto_builders_009
// What: implements `operation_kind_proto_i32`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn operation_kind_proto_i32(value: &str) -> i32 {
    // DB-BLOCK src_db_proto_builders_010
    // What: branches across known alternatives.
    // How: uses Rust `match` on `match value {`.
    // Why: closed branching is safer than ad-hoc string/boolean decision trees.
    match value {
        "create_trade_order" => proto_i32::OP_CREATE_TRADE_ORDER,
        "cancel_trade_order" => proto_i32::OP_CANCEL_TRADE_ORDER,
        "expire_trade_order" => proto_i32::OP_EXPIRE_TRADE_ORDER,
        "accept_trade_order" => proto_i32::OP_ACCEPT_TRADE_ORDER,
        "settle_trade" => proto_i32::OP_SETTLE_TRADE,
        "claim_trade_result" => proto_i32::OP_CLAIM_TRADE_RESULT,
        _ => 0,
    }
}

// DB-BLOCK src_db_proto_builders_011
// What: implements `trade_order_view`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn trade_order_view(row: TradeOrderRow) -> Result<TradeOrderView, SettlementError> {
    // DB-BLOCK src_db_proto_builders_012
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(TradeOrderView {`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(TradeOrderView {
        trade_order_id: id(row.trade_order_id, |value| TradeOrderId { value }),
        operation_id: id(row.operation_id, |value| OperationId { value }),
        order_side: OrderSide::from_db(&row.order_side)?.as_proto_i32(),
        state: TradeState::from_db(&row.state)?.as_proto_i32(),
        owner_capsuleer_id: id(row.owner_capsuleer_id, |value| CapsuleerId { value }),
        owner_wallet_id: id(row.owner_wallet_id, |value| WalletId { value }),
        item_type_id: id(row.item_type_id, |value| ItemTypeId { value }),
        offered_item_stack_id: row.offered_item_stack_id.map(|value| ItemStackId { value }),
        offered_item_instance_id: row
            .offered_item_instance_id
            .map(|value| ItemInstanceId { value }),
        station_id: id(row.station_id, |value| StationId { value }),
        region_id: id(row.region_id, |value| RegionId { value }),
        total_quantity: qty(row.total_quantity),
        remaining_quantity: qty(row.remaining_quantity),
        unit_price_isk: isk(row.unit_price_isk),
        expires_at: Some(time::to_proto(row.expires_at)),
        created_at: Some(time::to_proto(row.created_at)),
        updated_at: Some(time::to_proto(row.updated_at)),
    })
}

// DB-BLOCK src_db_proto_builders_013
// What: implements `wallet_reservation_view`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn wallet_reservation_view(row: WalletReservationRow) -> WalletReservationView {
    WalletReservationView {
        wallet_reservation_id: row.wallet_reservation_id,
        trade_order_id: id(row.trade_order_id, |value| TradeOrderId { value }),
        wallet_id: id(row.wallet_id, |value| WalletId { value }),
        created_wallet_operation_id: id(row.created_wallet_operation_id, |value| {
            WalletOperationId { value }
        }),
        released_wallet_operation_id: row
            .released_wallet_operation_id
            .map(|value| WalletOperationId { value }),
        original_reserved_isk: isk(row.original_reserved_isk),
        remaining_reserved_isk: isk(row.remaining_reserved_isk),
        used_reserved_isk: isk(row.used_reserved_isk),
        released_reserved_isk: isk(row.released_reserved_isk),
        reservation_state: reservation_state_proto_i32(&row.reservation_state),
        release_reason: row.release_reason.unwrap_or_default(),
        created_at: Some(time::to_proto(row.created_at)),
        updated_at: Some(time::to_proto(row.updated_at)),
        released_at: time::to_proto_opt(row.released_at),
    }
}

// DB-BLOCK src_db_proto_builders_014
// What: implements `item_stack_reservation_view`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn item_stack_reservation_view(row: ItemStackReservationRow) -> ItemStackReservationView {
    ItemStackReservationView {
        item_stack_reservation_id: row.item_stack_reservation_id,
        trade_order_id: id(row.trade_order_id, |value| TradeOrderId { value }),
        item_stack_id: id(row.item_stack_id, |value| ItemStackId { value }),
        created_item_stack_operation_id: id(row.created_item_stack_operation_id, |value| {
            ItemStackOperationId { value }
        }),
        released_item_stack_operation_id: row
            .released_item_stack_operation_id
            .map(|value| ItemStackOperationId { value }),
        original_reserved_quantity: qty(row.original_reserved_quantity),
        remaining_reserved_quantity: qty(row.remaining_reserved_quantity),
        used_reserved_quantity: qty(row.used_reserved_quantity),
        released_reserved_quantity: qty(row.released_reserved_quantity),
        reservation_state: reservation_state_proto_i32(&row.reservation_state),
        release_reason: row.release_reason.unwrap_or_default(),
        created_at: Some(time::to_proto(row.created_at)),
        updated_at: Some(time::to_proto(row.updated_at)),
        released_at: time::to_proto_opt(row.released_at),
    }
}

// DB-BLOCK src_db_proto_builders_015
// What: implements `reservation_state_proto_i32`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn reservation_state_proto_i32(value: &str) -> i32 {
    // DB-BLOCK src_db_proto_builders_016
    // What: branches across known alternatives.
    // How: uses Rust `match` on `match value {`.
    // Why: closed branching is safer than ad-hoc string/boolean decision trees.
    match value {
        "active" => proto_i32::RESERVATION_ACTIVE,
        "partially_used" => proto_i32::RESERVATION_PARTIALLY_USED,
        "used" => proto_i32::RESERVATION_USED,
        "released" => proto_i32::RESERVATION_RELEASED,
        _ => 0,
    }
}

// DB-BLOCK src_db_proto_builders_017
// What: implements `trade_transaction_view`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn trade_transaction_view(
    row: TradeTransactionRow,
) -> Result<TradeTransactionView, SettlementError> {
    // DB-BLOCK src_db_proto_builders_018
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(TradeTransactionView {`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(TradeTransactionView {
        trade_transaction_id: id(row.trade_transaction_id, |value| TradeTransactionId {
            value,
        }),
        operation_id: id(row.operation_id, |value| OperationId { value }),
        trade_order_id: id(row.trade_order_id, |value| TradeOrderId { value }),
        state: TradeState::from_db(&row.state)?.as_proto_i32(),
        buyer_capsuleer_id: id(row.buyer_capsuleer_id, |value| CapsuleerId { value }),
        buyer_wallet_id: id(row.buyer_wallet_id, |value| WalletId { value }),
        seller_capsuleer_id: id(row.seller_capsuleer_id, |value| CapsuleerId { value }),
        seller_wallet_id: id(row.seller_wallet_id, |value| WalletId { value }),
        item_type_id: id(row.item_type_id, |value| ItemTypeId { value }),
        source_item_stack_id: row.source_item_stack_id.map(|value| ItemStackId { value }),
        destination_item_stack_id: row
            .destination_item_stack_id
            .map(|value| ItemStackId { value }),
        source_item_instance_id: row
            .source_item_instance_id
            .map(|value| ItemInstanceId { value }),
        destination_item_instance_id: row
            .destination_item_instance_id
            .map(|value| ItemInstanceId { value }),
        quantity: qty(row.quantity),
        unit_price_isk: isk(row.unit_price_isk),
        total_price_isk: isk(row.total_price_isk),
        created_at: Some(time::to_proto(row.created_at)),
        updated_at: Some(time::to_proto(row.updated_at)),
        completed_at: time::to_proto_opt(row.completed_at),
    })
}

// DB-BLOCK src_db_proto_builders_019
// What: implements `settlement_view`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn settlement_view(row: SettlementRow) -> SettlementView {
    SettlementView {
        settlement_id: id(row.settlement_id, |value| SettlementId { value }),
        operation_id: id(row.operation_id, |value| OperationId { value }),
        trade_transaction_id: id(row.trade_transaction_id, |value| TradeTransactionId {
            value,
        }),
        idempotency_key: id(row.idempotency_key, |value| IdempotencyKey { value }),
        state: match row.state.as_str() {
            "completed" => proto_i32::OPERATION_COMPLETED,
            "failed" => proto_i32::OPERATION_FAILED,
            _ => proto_i32::OPERATION_IN_PROGRESS,
        },
        settlement_phase: settlement_phase_proto_i32(&row.settlement_phase),
        retry_count: row.retry_count as u32,
        started_at: Some(time::to_proto(row.started_at)),
        decided_at: time::to_proto_opt(row.decided_at),
        failure: row
            .failure_message
            .map(|m| trade_error(proto_i32::ERROR_SETTLEMENT_CONFLICT, m)),
    }
}

// DB-BLOCK src_db_proto_builders_020
// What: implements `settlement_phase_proto_i32`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn settlement_phase_proto_i32(value: &str) -> i32 {
    // DB-BLOCK src_db_proto_builders_021
    // What: branches across known alternatives.
    // How: uses Rust `match` on `match value {`.
    // Why: closed branching is safer than ad-hoc string/boolean decision trees.
    match value {
        "created" => proto_i32::SETTLEMENT_CREATED,
        "locked_trade" => proto_i32::SETTLEMENT_LOCKED_TRADE,
        "locked_wallets" => proto_i32::SETTLEMENT_LOCKED_WALLETS,
        "locked_items" => proto_i32::SETTLEMENT_LOCKED_ITEMS,
        "wallet_moved" => proto_i32::SETTLEMENT_WALLET_MOVED,
        "items_moved" => proto_i32::SETTLEMENT_ITEMS_MOVED,
        "state_recorded" => proto_i32::SETTLEMENT_STATE_RECORDED,
        "completed" => proto_i32::SETTLEMENT_COMPLETED,
        "failed" => proto_i32::SETTLEMENT_FAILED,
        _ => 0,
    }
}

// DB-BLOCK src_db_proto_builders_022
// What: implements `settlement_step_view`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn settlement_step_view(row: SettlementStepRow) -> SettlementStepView {
    SettlementStepView {
        settlement_step_id: row.settlement_step_id,
        settlement_id: id(row.settlement_id, |value| SettlementId { value }),
        step_name: row.step_name,
        step_state: match row.step_state.as_str() {
            "completed" => proto_i32::OPERATION_COMPLETED,
            "failed" => proto_i32::OPERATION_FAILED,
            "pending" => proto_i32::OPERATION_PENDING,
            _ => proto_i32::OPERATION_IN_PROGRESS,
        },
        started_at: Some(time::to_proto(row.started_at)),
        completed_at: time::to_proto_opt(row.completed_at),
        failure: row
            .failure_message
            .map(|m| trade_error(proto_i32::ERROR_SETTLEMENT_CONFLICT, m)),
    }
}
