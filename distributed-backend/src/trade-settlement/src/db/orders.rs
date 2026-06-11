//! Trade order opening, closing, and listing.
//!
//! What this file contains:
//! - `open_trade_order`: creates an outstanding market order and its reservation.
//! - `close_trade_order`: cancels/expires/fails an order and releases remaining reservation.
//! - read helpers for `GetTradeOrder` and `ListOutstandingTradeOrders`.
//!
//! How it works:
//! - Market decides whether order creation/closing is allowed.
//! - Settlement records the durable order and reserves/release wallet or item state.
//! - All writes are wrapped in one SQL transaction and one idempotency record.
//!
//! Why it exists:
//! - Orders and reservations are the durable preconditions for later settlement.

// DB-BLOCK src_db_orders_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for trade order open/close/read/list behavior.
// Why: explicit imports make coupling visible during review.
use sqlx::{PgPool, Postgres, Transaction};

use crate::db::types::{CloseTarget, ItemKind, OrderSide, TradeState};
use crate::db::{extract, idempotency, operation_log, ownership, proto_builders, queries, time};
use crate::error::SettlementError;
use crate::generated::settlement::v1::*;

// DB-BLOCK src_db_orders_002
// What: implements `release_wallet_reservation`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
async fn release_wallet_reservation(
    tx: &mut Transaction<'_, Postgres>,
    operation_id: &str,
    reservation: &crate::db::rows::WalletReservationRow,
    reason: &str,
) -> Result<String, SettlementError> {
    // DB-BLOCK src_db_orders_003
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if reservation.remaining_reserved_isk == 0 || reservation.reservation_state == "released" ` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if reservation.remaining_reserved_isk == 0 || reservation.reservation_state == "released" {
        // DB-BLOCK src_db_orders_004
        // What: exits the current workflow early.
        // How: returns from `return Ok(reservation.created_wallet_operation_id.clone());` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Ok(reservation.created_wallet_operation_id.clone());
    }
    // DB-BLOCK src_db_orders_005
    // What: binds `wallet_op` as a named intermediate.
    // How: computes/extracts `wallet_op` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let wallet_op =
        ownership::create_wallet_operation(tx, operation_id, "release_reserved_isk").await?;
    ownership::move_wallet(
        tx,
        &wallet_op,
        &reservation.wallet_id,
        reservation.remaining_reserved_isk,
        -reservation.remaining_reserved_isk,
        "release_trade_reservation",
    )
    .await?;
    // DB-BLOCK src_db_orders_006
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        UPDATE trade.wallet_reservation
        SET remaining_reserved_isk = 0,
            released_reserved_isk = released_reserved_isk + $2,
            reservation_state = 'released',
            released_wallet_operation_id = $3::uuid,
            release_reason = $4,
            released_at = now(),
            updated_at = now()
        WHERE wallet_reservation_id = $1::uuid
        "#,
    )
    .bind(&reservation.wallet_reservation_id)
    .bind(reservation.remaining_reserved_isk)
    .bind(&wallet_op)
    .bind(reason)
    .execute(&mut **tx)
    .await?;
    ownership::complete_wallet_operation(tx, &wallet_op).await?;
    // DB-BLOCK src_db_orders_007
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(wallet_op)`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(wallet_op)
}

// DB-BLOCK src_db_orders_008
// What: implements `release_stack_reservation`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
async fn release_stack_reservation(
    tx: &mut Transaction<'_, Postgres>,
    operation_id: &str,
    reservation: &crate::db::rows::ItemStackReservationRow,
    reason: &str,
) -> Result<String, SettlementError> {
    // DB-BLOCK src_db_orders_009
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if reservation.remaining_reserved_quantity == 0 || reservation.reservation_state == "relea` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if reservation.remaining_reserved_quantity == 0 || reservation.reservation_state == "released" {
        // DB-BLOCK src_db_orders_010
        // What: exits the current workflow early.
        // How: returns from `return Ok(reservation.created_item_stack_operation_id.clone());` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Ok(reservation.created_item_stack_operation_id.clone());
    }
    // DB-BLOCK src_db_orders_011
    // What: binds `stack_op` as a named intermediate.
    // How: computes/extracts `stack_op` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let stack_op =
        ownership::create_stack_operation(tx, operation_id, "release_trade_reservation").await?;
    ownership::move_stack(
        tx,
        &stack_op,
        &reservation.item_stack_id,
        reservation.remaining_reserved_quantity,
        -reservation.remaining_reserved_quantity,
        "release_trade_reservation",
    )
    .await?;
    // DB-BLOCK src_db_orders_012
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        UPDATE trade.item_stack_reservation
        SET remaining_reserved_quantity = 0,
            released_reserved_quantity = released_reserved_quantity + $2,
            reservation_state = 'released',
            released_item_stack_operation_id = $3::uuid,
            release_reason = $4,
            released_at = now(),
            updated_at = now()
        WHERE item_stack_reservation_id = $1::uuid
        "#,
    )
    .bind(&reservation.item_stack_reservation_id)
    .bind(reservation.remaining_reserved_quantity)
    .bind(&stack_op)
    .bind(reason)
    .execute(&mut **tx)
    .await?;
    ownership::complete_stack_operation(tx, &stack_op).await?;
    // DB-BLOCK src_db_orders_013
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(stack_op)`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(stack_op)
}

// DB-BLOCK src_db_orders_014
// What: opens or replays a durable trade order request.
// How: delegates to the order workflow that validates, idempotency-checks, reserves assets if needed, and commits.
// Why: order creation is a write boundary and must be centralized.
pub async fn open_trade_order(
    pool: &PgPool,
    req: &OpenTradeOrderRequest,
) -> Result<OpenTradeOrderResponse, SettlementError> {
    extract::validate_open_trade_order(req)?;
    // DB-BLOCK src_db_orders_015
    // What: opens a SQL transaction.
    // How: calls `pool.begin()` and passes the transaction through subsequent DB work.
    // Why: related writes must commit or roll back as one atomic unit.
    let mut tx = pool.begin().await?;
    // DB-BLOCK src_db_orders_016
    // What: binds `guard` as a named intermediate.
    // How: computes/extracts `guard` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let guard = idempotency::begin(&mut tx, &req.context, "open_trade_order", req).await?;

    // DB-BLOCK src_db_orders_017
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if let Some(replay) = guard.replay.clone() {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if let Some(replay) = guard.replay.clone() {
        // DB-BLOCK src_db_orders_018
        // What: binds `order_id` as a named intermediate.
        // How: computes/extracts `order_id` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let order_id = replay.trade_order_id.ok_or_else(|| {
            SettlementError::IntegrityConflict(
                "open_trade_order replay missing trade_order_id".to_string(),
            )
        })?;
        // DB-BLOCK src_db_orders_019
        // What: binds `order` as a named intermediate.
        // How: computes/extracts `order` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let order = queries::load_order(&mut tx, &order_id).await?;
        // DB-BLOCK src_db_orders_020
        // What: binds `operation` as a named intermediate.
        // How: computes/extracts `operation` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let operation = if let Some(id) = replay.operation_id {
            Some(operation_log::load(&mut tx, &id).await?)
        } else {
            None
        };
        tx.commit().await?;
        // DB-BLOCK src_db_orders_021
        // What: exits the current workflow early.
        // How: returns from `return Ok(OpenTradeOrderResponse {` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Ok(OpenTradeOrderResponse {
            operation: operation.map(proto_builders::operation_view).transpose()?,
            trade_order: Some(proto_builders::trade_order_view(order)?),
            wallet_reservation: None,
            item_stack_reservation: None,
            item_instance_reservation: None,
            idempotent_replay: true,
            failure: None,
        });
    }

    // DB-BLOCK src_db_orders_022
    // What: binds `terms` as a named intermediate.
    // How: computes/extracts `terms` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let terms = req.terms.as_ref().expect("terms was validated");
    // DB-BLOCK src_db_orders_023
    // What: binds `side` as a named intermediate.
    // How: computes/extracts `side` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let side = OrderSide::from_proto_i32(terms.order_side)?;
    // DB-BLOCK src_db_orders_024
    // What: binds `kind` as a named intermediate.
    // How: computes/extracts `kind` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let kind = ItemKind::from_proto_i32(terms.item_kind)?;
    // DB-BLOCK src_db_orders_025
    // What: binds `owner_capsuleer_id` as a named intermediate.
    // How: computes/extracts `owner_capsuleer_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let owner_capsuleer_id =
        extract::capsuleer_id("terms.owner_capsuleer_id", &terms.owner_capsuleer_id)?;
    // DB-BLOCK src_db_orders_026
    // What: binds `owner_wallet_id` as a named intermediate.
    // How: computes/extracts `owner_wallet_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let owner_wallet_id = extract::wallet_id("terms.owner_wallet_id", &terms.owner_wallet_id)?;
    // DB-BLOCK src_db_orders_027
    // What: binds `item_type_id` as a named intermediate.
    // How: computes/extracts `item_type_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let item_type_id = extract::item_type_id("terms.item_type_id", &terms.item_type_id)?;
    // DB-BLOCK src_db_orders_028
    // What: binds `station_id` as a named intermediate.
    // How: computes/extracts `station_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let station_id = extract::station_id("terms.station_id", &terms.station_id)?;
    // DB-BLOCK src_db_orders_029
    // What: binds `region_id` as a named intermediate.
    // How: computes/extracts `region_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let region_id = extract::region_id("terms.region_id", &terms.region_id)?;
    // DB-BLOCK src_db_orders_030
    // What: binds `quantity` as a named intermediate.
    // How: computes/extracts `quantity` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let quantity = extract::quantity("terms.total_quantity", &terms.total_quantity)?;
    // DB-BLOCK src_db_orders_031
    // What: binds `unit_price_isk` as a named intermediate.
    // How: computes/extracts `unit_price_isk` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let unit_price_isk = extract::isk_amount("terms.unit_price_isk", &terms.unit_price_isk)?;
    // DB-BLOCK src_db_orders_032
    // What: binds `total_price_isk` as a named intermediate.
    // How: computes/extracts `total_price_isk` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let total_price_isk = quantity
        .checked_mul(unit_price_isk)
        .ok_or_else(|| SettlementError::InvalidRequest("order total ISK overflow".to_string()))?;
    // DB-BLOCK src_db_orders_033
    // What: binds `expires_at` as a named intermediate.
    // How: computes/extracts `expires_at` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let expires_at = time::from_proto_required("terms.expires_at", &terms.expires_at)?;

    // DB-BLOCK src_db_orders_034
    // What: binds `operation_id` as a named intermediate.
    // How: computes/extracts `operation_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let operation_id = operation_log::create(&mut tx, &req.context, "create_trade_order").await?;
    // DB-BLOCK src_db_orders_035
    // What: binds `offered_stack` as a named intermediate.
    // How: computes/extracts `offered_stack` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let offered_stack = extract::item_stack_id_optional(
        "terms.offered_item_stack_id",
        &terms.offered_item_stack_id,
    )?;

    // DB-BLOCK src_db_orders_036
    // What: binds `order_id` as a named intermediate.
    // How: computes/extracts `order_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let order_id: (String,) = sqlx::query_as(
        r#"
        INSERT INTO trade.trade_order (
            operation_id, order_side, state, owner_capsuleer_id, owner_wallet_id,
            item_type_id, offered_item_stack_id, station_id, region_id,
            total_quantity, remaining_quantity, unit_price_isk, expires_at
        ) VALUES ($1::uuid, $2::trade.order_side, 'outstanding', $3::uuid, $4::uuid, $5::uuid,
                  $6::uuid, $7::uuid, $8::uuid, $9, $9, $10, $11)
        RETURNING trade_order_id::text
        "#,
    )
    .bind(&operation_id)
    .bind(side.as_db())
    .bind(&owner_capsuleer_id)
    .bind(&owner_wallet_id)
    .bind(&item_type_id)
    .bind(&offered_stack)
    .bind(&station_id)
    .bind(&region_id)
    .bind(quantity)
    .bind(unit_price_isk)
    .bind(expires_at)
    .fetch_one(&mut *tx)
    .await?;

    // DB-BLOCK src_db_orders_037
    // What: binds `wallet_reservation` as a named intermediate.
    // How: computes/extracts `wallet_reservation` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut wallet_reservation = None;
    // DB-BLOCK src_db_orders_038
    // What: binds `stack_reservation` as a named intermediate.
    // How: computes/extracts `stack_reservation` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut stack_reservation = None;
    // DB-BLOCK src_db_orders_039
    // What: binds `wallet_operation_id` as a named intermediate.
    // How: computes/extracts `wallet_operation_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut wallet_operation_id = None;
    // DB-BLOCK src_db_orders_040
    // What: binds `stack_operation_id` as a named intermediate.
    // How: computes/extracts `stack_operation_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut stack_operation_id = None;

    // DB-BLOCK src_db_orders_041
    // What: branches across known alternatives.
    // How: uses Rust `match` on `match (side, kind) {`.
    // Why: closed branching is safer than ad-hoc string/boolean decision trees.
    match (side, kind) {
        (OrderSide::Buy, ItemKind::Stackable) => {
            // DB-BLOCK src_db_orders_042
            // What: binds `wallet_op` as a named intermediate.
            // How: computes/extracts `wallet_op` once before SQL or response construction.
            // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
            let wallet_op =
                ownership::create_wallet_operation(&mut tx, &operation_id, "reserve_isk").await?;
            ownership::move_wallet(
                &mut tx,
                &wallet_op,
                &owner_wallet_id,
                -total_price_isk,
                total_price_isk,
                "reserve_for_buy_order",
            )
            .await?;
            // DB-BLOCK src_db_orders_043
            // What: binds `row` as a named intermediate.
            // How: computes/extracts `row` once before SQL or response construction.
            // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
            let row = sqlx::query_as::<_, crate::db::rows::WalletReservationRow>(
                r#"
                INSERT INTO trade.wallet_reservation (
                    trade_order_id, wallet_id, created_wallet_operation_id,
                    original_reserved_isk, remaining_reserved_isk, reservation_state
                ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $4, 'active')
                RETURNING wallet_reservation_id::text AS wallet_reservation_id, trade_order_id::text AS trade_order_id,
                          wallet_id::text AS wallet_id, created_wallet_operation_id::text AS created_wallet_operation_id,
                          released_wallet_operation_id::text AS released_wallet_operation_id, original_reserved_isk,
                          remaining_reserved_isk, used_reserved_isk, released_reserved_isk,
                          reservation_state::text AS reservation_state, release_reason, created_at, updated_at, released_at
                "#,
            )
            .bind(&order_id.0)
            .bind(&owner_wallet_id)
            .bind(&wallet_op)
            .bind(total_price_isk)
            .fetch_one(&mut *tx)
            .await?;
            ownership::complete_wallet_operation(&mut tx, &wallet_op).await?;
            wallet_operation_id = Some(wallet_op);
            wallet_reservation = Some(row);
        }
        (OrderSide::Sell, ItemKind::Stackable) => {
            // DB-BLOCK src_db_orders_044
            // What: binds `stack_id` as a named intermediate.
            // How: computes/extracts `stack_id` once before SQL or response construction.
            // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
            let stack_id = offered_stack.clone().expect("validated sell stack exists");
            // DB-BLOCK src_db_orders_045
            // What: binds `stack` as a named intermediate.
            // How: computes/extracts `stack` once before SQL or response construction.
            // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
            let stack = ownership::lock_stack(&mut tx, &stack_id).await?;
            // DB-BLOCK src_db_orders_046
            // What: guards a correctness-sensitive branch.
            // How: evaluates `if stack.capsuleer_id != owner_capsuleer_id || stack.item_type_id != item_type_id || stack` before continuing.
            // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
            if stack.capsuleer_id != owner_capsuleer_id
                || stack.item_type_id != item_type_id
                || stack.station_id != station_id
            {
                // DB-BLOCK src_db_orders_047
                // What: exits the current workflow early.
                // How: returns from `return Err(SettlementError::TradeMismatch { trade_order_id: order_id.0 });` before later mutation blocks execute.
                // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
                return Err(SettlementError::TradeMismatch {
                    trade_order_id: order_id.0,
                });
            }
            // DB-BLOCK src_db_orders_048
            // What: binds `stack_op` as a named intermediate.
            // How: computes/extracts `stack_op` once before SQL or response construction.
            // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
            let stack_op =
                ownership::create_stack_operation(&mut tx, &operation_id, "reserve_for_trade")
                    .await?;
            ownership::move_stack(
                &mut tx,
                &stack_op,
                &stack_id,
                -quantity,
                quantity,
                "reserve_for_sell_order",
            )
            .await?;
            // DB-BLOCK src_db_orders_049
            // What: binds `row` as a named intermediate.
            // How: computes/extracts `row` once before SQL or response construction.
            // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
            let row = sqlx::query_as::<_, crate::db::rows::ItemStackReservationRow>(
                r#"
                INSERT INTO trade.item_stack_reservation (
                    trade_order_id, item_stack_id, created_item_stack_operation_id,
                    original_reserved_quantity, remaining_reserved_quantity, reservation_state
                ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $4, 'active')
                RETURNING item_stack_reservation_id::text AS item_stack_reservation_id, trade_order_id::text AS trade_order_id,
                          item_stack_id::text AS item_stack_id, created_item_stack_operation_id::text AS created_item_stack_operation_id,
                          released_item_stack_operation_id::text AS released_item_stack_operation_id, original_reserved_quantity,
                          remaining_reserved_quantity, used_reserved_quantity, released_reserved_quantity,
                          reservation_state::text AS reservation_state, release_reason, created_at, updated_at, released_at
                "#,
            )
            .bind(&order_id.0)
            .bind(&stack_id)
            .bind(&stack_op)
            .bind(quantity)
            .fetch_one(&mut *tx)
            .await?;
            ownership::complete_stack_operation(&mut tx, &stack_op).await?;
            stack_operation_id = Some(stack_op);
            stack_reservation = Some(row);
        }
        (_, ItemKind::Singleton) => {
            return Err(SettlementError::Unsupported(
                "singleton order path is not implemented".to_string(),
            ))
        }
    }

    operation_log::complete(&mut tx, &operation_id).await?;
    idempotency::record_success(
        &mut tx,
        idempotency::RecordSuccessInput {
            guard: &guard,
            result_kind: "open_trade_order",
            operation_id: Some(&operation_id),
            trade_order_id: Some(&order_id.0),
            trade_transaction_id: None,
            settlement_id: None,
            wallet_operation_id: wallet_operation_id.as_deref(),
            item_stack_operation_id: stack_operation_id.as_deref(),
            result_state: TradeState::Outstanding.as_db(),
        },
    )
    .await?;

    // DB-BLOCK src_db_orders_050
    // What: binds `order` as a named intermediate.
    // How: computes/extracts `order` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let order = queries::load_order(&mut tx, &order_id.0).await?;
    // DB-BLOCK src_db_orders_051
    // What: binds `operation` as a named intermediate.
    // How: computes/extracts `operation` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let operation = operation_log::load(&mut tx, &operation_id).await?;
    tx.commit().await?;

    // DB-BLOCK src_db_orders_052
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(OpenTradeOrderResponse {`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(OpenTradeOrderResponse {
        operation: Some(proto_builders::operation_view(operation)?),
        trade_order: Some(proto_builders::trade_order_view(order)?),
        wallet_reservation: wallet_reservation.map(proto_builders::wallet_reservation_view),
        item_stack_reservation: stack_reservation.map(proto_builders::item_stack_reservation_view),
        item_instance_reservation: None,
        idempotent_replay: false,
        failure: None,
    })
}

// DB-BLOCK src_db_orders_053
// What: closes a trade order with a requested terminal state.
// How: delegates to the order workflow that locks the order and writes a valid close result.
// Why: cancel/expire/fail transitions must be durable and replay-safe.
pub async fn close_trade_order(
    pool: &PgPool,
    req: &CloseTradeOrderRequest,
) -> Result<CloseTradeOrderResponse, SettlementError> {
    extract::validate_close_trade_order(req)?;
    // DB-BLOCK src_db_orders_054
    // What: binds `target` as a named intermediate.
    // How: computes/extracts `target` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let target = CloseTarget::from_requested_change(req.requested_change)?;
    // DB-BLOCK src_db_orders_055
    // What: opens a SQL transaction.
    // How: calls `pool.begin()` and passes the transaction through subsequent DB work.
    // Why: related writes must commit or roll back as one atomic unit.
    let mut tx = pool.begin().await?;
    // DB-BLOCK src_db_orders_056
    // What: binds `guard` as a named intermediate.
    // How: computes/extracts `guard` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let guard = idempotency::begin(&mut tx, &req.context, "close_trade_order", req).await?;
    // DB-BLOCK src_db_orders_057
    // What: binds `order_id` as a named intermediate.
    // How: computes/extracts `order_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let order_id = extract::trade_order_id("trade_order_id", &req.trade_order_id)?;
    // DB-BLOCK src_db_orders_058
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if guard.replay.is_some() {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if guard.replay.is_some() {
        // DB-BLOCK src_db_orders_059
        // What: binds `order` as a named intermediate.
        // How: computes/extracts `order` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let order = queries::load_order(&mut tx, &order_id).await?;
        tx.commit().await?;
        // DB-BLOCK src_db_orders_060
        // What: exits the current workflow early.
        // How: returns from `return Ok(CloseTradeOrderResponse { operation: None, trade_order: Some(proto_build` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Ok(CloseTradeOrderResponse {
            operation: None,
            trade_order: Some(proto_builders::trade_order_view(order)?),
            wallet_reservation: None,
            item_stack_reservation: None,
            item_instance_reservation: None,
            idempotent_replay: true,
            failure: None,
        });
    }
    // DB-BLOCK src_db_orders_061
    // What: binds `order` as a named intermediate.
    // How: computes/extracts `order` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let order = queries::lock_order(&mut tx, &order_id).await?;
    // DB-BLOCK src_db_orders_062
    // What: binds `state` as a named intermediate.
    // How: computes/extracts `state` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let state = TradeState::from_db(&order.state)?;
    // DB-BLOCK src_db_orders_063
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if state.is_terminal() {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if state.is_terminal() {
        // DB-BLOCK src_db_orders_064
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::InvalidTransition { from: order.state, action: "clos` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::InvalidTransition {
            from: order.state,
            action: "close_trade_order",
        });
    }
    // DB-BLOCK src_db_orders_065
    // What: binds `operation_id` as a named intermediate.
    // How: computes/extracts `operation_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let operation_id =
        operation_log::create(&mut tx, &req.context, target.operation_kind_db()).await?;
    // DB-BLOCK src_db_orders_066
    // What: binds `wallet_res` as a named intermediate.
    // How: computes/extracts `wallet_res` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let wallet_res = queries::lock_wallet_reservation(&mut tx, &order_id).await?;
    // DB-BLOCK src_db_orders_067
    // What: binds `stack_res` as a named intermediate.
    // How: computes/extracts `stack_res` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let stack_res = queries::lock_stack_reservation(&mut tx, &order_id).await?;
    // DB-BLOCK src_db_orders_068
    // What: binds `wallet_op` as a named intermediate.
    // How: computes/extracts `wallet_op` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut wallet_op = None;
    // DB-BLOCK src_db_orders_069
    // What: binds `stack_op` as a named intermediate.
    // How: computes/extracts `stack_op` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut stack_op = None;
    // DB-BLOCK src_db_orders_070
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if let Some(res) = wallet_res.as_ref() { wallet_op = Some(release_wallet_reservation(&mut ` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if let Some(res) = wallet_res.as_ref() {
        wallet_op =
            Some(release_wallet_reservation(&mut tx, &operation_id, res, &req.reason).await?);
    }
    // DB-BLOCK src_db_orders_071
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if let Some(res) = stack_res.as_ref() { stack_op = Some(release_stack_reservation(&mut tx,` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if let Some(res) = stack_res.as_ref() {
        stack_op = Some(release_stack_reservation(&mut tx, &operation_id, res, &req.reason).await?);
    }
    // DB-BLOCK src_db_orders_072
    // What: performs a parameterized SQL operation against `trade_order`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query("UPDATE trade.trade_order SET state = $2::trade.trade_state, updated_at = now() WHERE trade_order_id = $1::uuid")
        .bind(&order_id)
        .bind(target.as_trade_state().as_db())
        .execute(&mut *tx)
        .await?;
    operation_log::complete(&mut tx, &operation_id).await?;
    idempotency::record_success(
        &mut tx,
        idempotency::RecordSuccessInput {
            guard: &guard,
            result_kind: "close_trade_order",
            operation_id: Some(&operation_id),
            trade_order_id: Some(&order_id),
            trade_transaction_id: None,
            settlement_id: None,
            wallet_operation_id: wallet_op.as_deref(),
            item_stack_operation_id: stack_op.as_deref(),
            result_state: target.as_trade_state().as_db(),
        },
    )
    .await?;

    // DB-BLOCK src_db_orders_073
    // What: binds `order` as a named intermediate.
    // How: computes/extracts `order` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let order = queries::load_order(&mut tx, &order_id).await?;
    // DB-BLOCK src_db_orders_074
    // What: binds `operation` as a named intermediate.
    // How: computes/extracts `operation` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let operation = operation_log::load(&mut tx, &operation_id).await?;
    tx.commit().await?;
    // DB-BLOCK src_db_orders_075
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(CloseTradeOrderResponse { operation: Some(proto_builders::operation_view(operat`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(CloseTradeOrderResponse {
        operation: Some(proto_builders::operation_view(operation)?),
        trade_order: Some(proto_builders::trade_order_view(order)?),
        wallet_reservation: wallet_res.map(proto_builders::wallet_reservation_view),
        item_stack_reservation: stack_res.map(proto_builders::item_stack_reservation_view),
        item_instance_reservation: None,
        idempotent_replay: false,
        failure: None,
    })
}

// DB-BLOCK src_db_orders_076
// What: loads one durable trade order.
// How: extracts the request ID and maps the row into a protobuf response.
// Why: read APIs should not duplicate SQL or bypass the DB boundary.
pub async fn get_trade_order(
    pool: &PgPool,
    req: &GetTradeOrderRequest,
) -> Result<GetTradeOrderResponse, SettlementError> {
    // DB-BLOCK src_db_orders_077
    // What: binds `id` as a named intermediate.
    // How: computes/extracts `id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let id = extract::trade_order_id("trade_order_id", &req.trade_order_id)?;
    // DB-BLOCK src_db_orders_078
    // What: opens a SQL transaction.
    // How: calls `pool.begin()` and passes the transaction through subsequent DB work.
    // Why: related writes must commit or roll back as one atomic unit.
    let mut tx = pool.begin().await?;
    // DB-BLOCK src_db_orders_079
    // What: binds `row` as a named intermediate.
    // How: computes/extracts `row` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let row = queries::load_order(&mut tx, &id).await?;
    tx.commit().await?;
    // DB-BLOCK src_db_orders_080
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(GetTradeOrderResponse { trade_order: Some(proto_builders::trade_order_view(ro`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(GetTradeOrderResponse {
        trade_order: Some(proto_builders::trade_order_view(row)?),
    })
}

// DB-BLOCK src_db_orders_081
// What: lists outstanding orders with optional filters.
// How: extracts filter fields, runs a paginated query, and builds protobuf views.
// Why: market/gateway need controlled read access to order state.
pub async fn list_outstanding_trade_orders(
    pool: &PgPool,
    req: &ListOutstandingTradeOrdersRequest,
) -> Result<ListOutstandingTradeOrdersResponse, SettlementError> {
    // DB-BLOCK src_db_orders_082
    // What: binds `region` as a named intermediate.
    // How: computes/extracts `region` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let region = extract::region_id("region_id", &req.region_id)?;
    // DB-BLOCK src_db_orders_083
    // What: binds `station` as a named intermediate.
    // How: computes/extracts `station` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let station = extract::station_id("station_id", &req.station_id)?;
    // DB-BLOCK src_db_orders_084
    // What: binds `item_type` as a named intermediate.
    // How: computes/extracts `item_type` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let item_type = extract::item_type_id("item_type_id", &req.item_type_id)?;
    // DB-BLOCK src_db_orders_085
    // What: binds `side` as a named intermediate.
    // How: computes/extracts `side` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let side = OrderSide::from_proto_i32(req.order_side)?;
    // DB-BLOCK src_db_orders_086
    // What: binds `limit` as a named intermediate.
    // How: computes/extracts `limit` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let limit = req.page_size.clamp(1, 100) as i64;
    // DB-BLOCK src_db_orders_087
    // What: binds `offset` as a named intermediate.
    // How: computes/extracts `offset` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let offset = req.page_token.parse::<i64>().unwrap_or(0).max(0);
    // DB-BLOCK src_db_orders_088
    // What: binds `rows` as a named intermediate.
    // How: computes/extracts `rows` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let rows = sqlx::query_as::<_, crate::db::rows::TradeOrderRow>(
        r#"
        SELECT trade_order_id::text AS trade_order_id, operation_id::text AS operation_id,
               order_side::text AS order_side, state::text AS state,
               owner_capsuleer_id::text AS owner_capsuleer_id, owner_wallet_id::text AS owner_wallet_id,
               item_type_id::text AS item_type_id, offered_item_stack_id::text AS offered_item_stack_id,
               offered_item_instance_id::text AS offered_item_instance_id,
               station_id::text AS station_id, region_id::text AS region_id,
               total_quantity, remaining_quantity, unit_price_isk, expires_at, created_at, updated_at
        FROM trade.trade_order
        WHERE state = 'outstanding' AND region_id = $1::uuid AND station_id = $2::uuid
          AND item_type_id = $3::uuid AND order_side = $4::trade.order_side
        ORDER BY unit_price_isk ASC, created_at ASC
        LIMIT $5 OFFSET $6
        "#,
    )
    .bind(region).bind(station).bind(item_type).bind(side.as_db()).bind(limit).bind(offset)
    .fetch_all(pool)
    .await?;
    // DB-BLOCK src_db_orders_089
    // What: binds `next` as a named intermediate.
    // How: computes/extracts `next` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let next = if rows.len() as i64 == limit {
        (offset + limit).to_string()
    } else {
        String::new()
    };
    // DB-BLOCK src_db_orders_090
    // What: binds `views` as a named intermediate.
    // How: computes/extracts `views` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut views = Vec::with_capacity(rows.len());
    // DB-BLOCK src_db_orders_091
    // What: iterates over a bounded collection.
    // How: applies the same operation described by `for row in rows { views.push(proto_builders::trade_order_view(row)?); }` to each element.
    // Why: repeated row/view transformations should be explicit and auditable.
    for row in rows {
        views.push(proto_builders::trade_order_view(row)?);
    }
    // DB-BLOCK src_db_orders_092
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(ListOutstandingTradeOrdersResponse { trade_orders: views, next_page_token: ne`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(ListOutstandingTradeOrdersResponse {
        trade_orders: views,
        next_page_token: next,
    })
}
