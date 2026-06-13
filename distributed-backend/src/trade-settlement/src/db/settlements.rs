//! Atomic trade settlement.
//!
//! What this file contains:
//! - `request_settlement`: the correctness-critical path market calls after it
//!   has decided a transaction is valid.
//! - settlement/transaction read APIs.
//!
//! How it works:
//! - The function starts one SQL transaction, claims the idempotency key, locks
//!   order/reservation/ownership rows, creates or reuses the transaction row,
//!   moves wallet and item state, writes ledgers, records settlement phase/steps,
//!   updates order/transaction state, records idempotency result, and commits.
//!
//! Why it exists:
//! - Returning `completed` without one atomic DB commit would be a correctness bug.
//! - Retrying settlement must never duplicate ownership movement.

// DB-BLOCK src_db_settlements_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for atomic settlement: order/transaction/settlement, wallet move, item move, idempotent commit.
// Why: explicit imports make coupling visible during review.
use sqlx::{PgPool, Postgres, Transaction};

use crate::db::types::{ItemKind, OrderSide, TradeState};
use crate::db::{extract, idempotency, operation_log, ownership, proto_builders, queries};
use crate::error::SettlementError;
use crate::generated::settlement::v1::*;
use crate::generated::trade::v1::{ItemStackOperationId, WalletOperationId};

// DB-BLOCK src_db_settlements_002
// What: records one completed settlement step.
// How: inserts a settlement_step row with the given step name and completed state.
// Why: step-level history allows crash/retry diagnosis without guessing from side effects.
async fn record_step(
    tx: &mut Transaction<'_, Postgres>,
    settlement_id: &str,
    step: &str,
) -> Result<(), SettlementError> {
    // DB-BLOCK src_db_settlements_003
    // What: performs a parameterized SQL operation against `settlement_step`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query("INSERT INTO trade.settlement_step (settlement_id, step_name, step_state, completed_at) VALUES ($1::uuid, $2, 'completed', now())")
        .bind(settlement_id)
        .bind(step)
        .execute(&mut **tx)
        .await?;
    // DB-BLOCK src_db_settlements_004
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(())`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(())
}

// DB-BLOCK src_db_settlements_005
// What: updates the settlement phase and mirrors it into step history.
// How: updates settlement.settlement_phase then records the phase through `record_step` in the same transaction.
// Why: phase and step history must not diverge.
async fn set_phase(
    tx: &mut Transaction<'_, Postgres>,
    settlement_id: &str,
    phase: &str,
) -> Result<(), SettlementError> {
    // DB-BLOCK src_db_settlements_006
    // What: performs a parameterized SQL operation against `settlement`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query("UPDATE trade.settlement SET settlement_phase = $2::trade.settlement_phase WHERE settlement_id = $1::uuid")
        .bind(settlement_id)
        .bind(phase)
        .execute(&mut **tx)
        .await?;
    record_step(tx, settlement_id, phase).await
}

// DB-BLOCK src_db_settlements_007
// What: creates the trade_transaction row once or returns the existing locked row.
// How: locks by transaction ID, validates request/order terms, checks total price arithmetic, inserts when absent, and reloads.
// Why: settlement retries must not create duplicate transactions or alter market-decided terms.
async fn create_transaction_if_needed(
    tx: &mut Transaction<'_, Postgres>,
    operation_id: &str,
    req: &RequestSettlementRequest,
    order: &crate::db::rows::TradeInstanceRow,
) -> Result<crate::db::rows::TradeTransactionRow, SettlementError> {
    // DB-BLOCK src_db_settlements_008
    // What: binds `tx_id` as a named intermediate.
    // How: computes/extracts `tx_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let tx_id = extract::trade_transaction_id("trade_transaction_id", &req.trade_transaction_id)?;
    // DB-BLOCK src_db_settlements_009
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if let Some(existing) = queries::lock_transaction(tx, &tx_id).await? {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if let Some(existing) = queries::lock_transaction(tx, &tx_id).await? {
        // DB-BLOCK src_db_settlements_010
        // What: exits the current workflow early.
        // How: returns from `return Ok(existing);` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Ok(existing);
    }
    // DB-BLOCK src_db_settlements_011
    // What: binds `buyer_capsuleer_id` as a named intermediate.
    // How: computes/extracts `buyer_capsuleer_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let buyer_capsuleer_id = extract::capsuleer_id("buyer_capsuleer_id", &req.buyer_capsuleer_id)?;
    // DB-BLOCK src_db_settlements_012
    // What: binds `buyer_wallet_id` as a named intermediate.
    // How: computes/extracts `buyer_wallet_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let buyer_wallet_id = extract::wallet_id("buyer_wallet_id", &req.buyer_wallet_id)?;
    // DB-BLOCK src_db_settlements_013
    // What: binds `seller_capsuleer_id` as a named intermediate.
    // How: computes/extracts `seller_capsuleer_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let seller_capsuleer_id =
        extract::capsuleer_id("seller_capsuleer_id", &req.seller_capsuleer_id)?;
    // DB-BLOCK src_db_settlements_014
    // What: binds `seller_wallet_id` as a named intermediate.
    // How: computes/extracts `seller_wallet_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let seller_wallet_id = extract::wallet_id("seller_wallet_id", &req.seller_wallet_id)?;
    // DB-BLOCK src_db_settlements_015
    // What: binds `item_type_id` as a named intermediate.
    // How: computes/extracts `item_type_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let item_type_id = extract::item_type_id("item_type_id", &req.item_type_id)?;
    // DB-BLOCK src_db_settlements_016
    // What: binds `source_stack` as a named intermediate.
    // How: computes/extracts `source_stack` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let source_stack = extract::item_stack_id("source_item_stack_id", &req.source_item_stack_id)?;
    // DB-BLOCK src_db_settlements_017
    // What: binds `destination_stack` as a named intermediate.
    // How: computes/extracts `destination_stack` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let destination_stack = extract::item_stack_id_optional(
        "destination_item_stack_id",
        &req.destination_item_stack_id,
    )?;
    // DB-BLOCK src_db_settlements_018
    // What: binds `quantity` as a named intermediate.
    // How: computes/extracts `quantity` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let quantity = extract::quantity("quantity", &req.quantity)?;
    // DB-BLOCK src_db_settlements_019
    // What: binds `unit` as a named intermediate.
    // How: computes/extracts `unit` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let unit = extract::isk_amount("unit_price_isk", &req.unit_price_isk)?;
    // DB-BLOCK src_db_settlements_020
    // What: binds `total` as a named intermediate.
    // How: computes/extracts `total` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let total = extract::isk_amount("total_price_isk", &req.total_price_isk)?;
    // DB-BLOCK src_db_settlements_021
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if total != quantity.checked_mul(unit).ok_or_else(|| SettlementError::InvalidRequest("sett` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if total
        != quantity.checked_mul(unit).ok_or_else(|| {
            SettlementError::InvalidRequest("settlement total ISK overflow".to_string())
        })?
    {
        // DB-BLOCK src_db_settlements_022
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::InvalidRequest("total_price_isk must equal quantity ` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::InvalidRequest(
            "total_price_isk must equal quantity * unit_price_isk".to_string(),
        ));
    }
    // DB-BLOCK src_db_settlements_023
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if order.item_type_id != item_type_id || order.unit_price_isk != unit || quantity > order.` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if order.item_type_id != item_type_id
        || order.unit_price_isk != unit
        || quantity > order.remaining_quantity
    {
        // DB-BLOCK src_db_settlements_024
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::TradeMismatch { trade_instance_id: order.trade_instance_id` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::TradeMismatch {
            trade_instance_id: order.trade_instance_id.clone(),
        });
    }
    // DB-BLOCK src_db_settlements_025
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        INSERT INTO trade.trade_transaction (
            trade_transaction_id, operation_id, trade_instance_id, state,
            buyer_capsuleer_id, buyer_wallet_id, seller_capsuleer_id, seller_wallet_id,
            item_type_id, source_item_stack_id, destination_item_stack_id,
            quantity, unit_price_isk, total_price_isk
        ) VALUES ($1::uuid, $2::uuid, $3::uuid, 'in_progress', $4::uuid, $5::uuid, $6::uuid, $7::uuid,
                  $8::uuid, $9::uuid, $10::uuid, $11, $12, $13)
        "#,
    )
    .bind(&tx_id).bind(operation_id).bind(&order.trade_instance_id)
    .bind(&buyer_capsuleer_id).bind(&buyer_wallet_id).bind(&seller_capsuleer_id).bind(&seller_wallet_id)
    .bind(&item_type_id).bind(&source_stack).bind(&destination_stack)
    .bind(quantity).bind(unit).bind(total)
    .execute(&mut **tx)
    .await?;
    // DB-BLOCK src_db_settlements_026
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(queries::lock_transaction(tx, &tx_id).await?.expect("transaction was just ins`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(queries::lock_transaction(tx, &tx_id)
        .await?
        .expect("transaction was just inserted"))
}

// DB-BLOCK src_db_settlements_027
// What: checks buyer/seller roles against the locked order side.
// How: compares request buyer/seller identities and wallets with the order owner depending on buy/sell side.
// Why: settlement must not let a request redirect the order to different actors.
fn verify_roles(
    order: &crate::db::rows::TradeInstanceRow,
    req: &RequestSettlementRequest,
) -> Result<OrderSide, SettlementError> {
    // DB-BLOCK src_db_settlements_028
    // What: binds `side` as a named intermediate.
    // How: computes/extracts `side` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let side = OrderSide::from_db(&order.order_side)?;
    // DB-BLOCK src_db_settlements_029
    // What: binds `buyer` as a named intermediate.
    // How: computes/extracts `buyer` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let buyer = extract::capsuleer_id("buyer_capsuleer_id", &req.buyer_capsuleer_id)?;
    // DB-BLOCK src_db_settlements_030
    // What: binds `buyer_wallet` as a named intermediate.
    // How: computes/extracts `buyer_wallet` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let buyer_wallet = extract::wallet_id("buyer_wallet_id", &req.buyer_wallet_id)?;
    // DB-BLOCK src_db_settlements_031
    // What: binds `seller` as a named intermediate.
    // How: computes/extracts `seller` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let seller = extract::capsuleer_id("seller_capsuleer_id", &req.seller_capsuleer_id)?;
    // DB-BLOCK src_db_settlements_032
    // What: binds `seller_wallet` as a named intermediate.
    // How: computes/extracts `seller_wallet` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let seller_wallet = extract::wallet_id("seller_wallet_id", &req.seller_wallet_id)?;
    // DB-BLOCK src_db_settlements_033
    // What: branches across known alternatives.
    // How: uses Rust `match` on `match side {`.
    // Why: closed branching is safer than ad-hoc string/boolean decision trees.
    match side {
        OrderSide::Buy => {
            // DB-BLOCK src_db_settlements_034
            // What: guards a correctness-sensitive branch.
            // How: evaluates `if buyer != order.owner_capsuleer_id || buyer_wallet != order.owner_wallet_id { return Err` before continuing.
            // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
            if buyer != order.owner_capsuleer_id || buyer_wallet != order.owner_wallet_id {
                return Err(SettlementError::TradeMismatch {
                    trade_instance_id: order.trade_instance_id.clone(),
                });
            }
        }
        OrderSide::Sell => {
            // DB-BLOCK src_db_settlements_035
            // What: guards a correctness-sensitive branch.
            // How: evaluates `if seller != order.owner_capsuleer_id || seller_wallet != order.owner_wallet_id { return E` before continuing.
            // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
            if seller != order.owner_capsuleer_id || seller_wallet != order.owner_wallet_id {
                return Err(SettlementError::TradeMismatch {
                    trade_instance_id: order.trade_instance_id.clone(),
                });
            }
        }
    }
    // DB-BLOCK src_db_settlements_036
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(side)`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(side)
}

// DB-BLOCK src_db_settlements_037
// What: performs the market-to-settlement DB transaction.
// How: validates request fields, claims idempotency, locks order/transaction/ownership rows, moves ISK/items, writes ledgers, records settlement state, and commits once.
// Why: this is the correctness-critical path; duplicate or partial ownership movement would corrupt the world state.
pub async fn request_settlement(
    pool: &PgPool,
    req: &RequestSettlementRequest,
) -> Result<RequestSettlementResponse, SettlementError> {
    extract::validate_settlement_request(req)?;
    // DB-BLOCK src_db_settlements_038
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if ItemKind::from_proto_i32(req.item_kind)? != ItemKind::Stackable {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if ItemKind::from_proto_i32(req.item_kind)? != ItemKind::Stackable {
        // DB-BLOCK src_db_settlements_039
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::Unsupported("only stackable item settlement is imple` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::Unsupported(
            "only stackable item settlement is implemented".to_string(),
        ));
    }
    // DB-BLOCK src_db_settlements_040
    // What: opens a SQL transaction.
    // How: calls `pool.begin()` and passes the transaction through subsequent DB work.
    // Why: related writes must commit or roll back as one atomic unit.
    let mut tx = pool.begin().await?;
    // DB-BLOCK src_db_settlements_041
    // What: binds `guard` as a named intermediate.
    // How: computes/extracts `guard` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let guard = idempotency::begin(&mut tx, &req.context, "request_settlement", req).await?;
    // DB-BLOCK src_db_settlements_042
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if let Some(replay) = guard.replay.clone() {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if let Some(replay) = guard.replay.clone() {
        // DB-BLOCK src_db_settlements_043
        // What: binds `trade_transaction_id` as a named intermediate.
        // How: computes/extracts `trade_transaction_id` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let trade_transaction_id = replay.trade_transaction_id.ok_or_else(|| {
            SettlementError::IntegrityConflict(
                "settlement replay missing transaction id".to_string(),
            )
        })?;
        // DB-BLOCK src_db_settlements_044
        // What: binds `settlement_id` as a named intermediate.
        // How: computes/extracts `settlement_id` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let settlement_id = replay.settlement_id.ok_or_else(|| {
            SettlementError::IntegrityConflict(
                "settlement replay missing settlement id".to_string(),
            )
        })?;
        // DB-BLOCK src_db_settlements_045
        // What: binds `order_id` as a named intermediate.
        // How: computes/extracts `order_id` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let order_id = replay.trade_instance_id.ok_or_else(|| {
            SettlementError::IntegrityConflict("settlement replay missing order id".to_string())
        })?;
        // DB-BLOCK src_db_settlements_046
        // What: binds `order` as a named intermediate.
        // How: computes/extracts `order` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let order = queries::load_order(&mut tx, &order_id).await?;
        // DB-BLOCK src_db_settlements_047
        // What: binds `trade_tx` as a named intermediate.
        // How: computes/extracts `trade_tx` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let trade_tx = queries::load_transaction(&mut tx, &trade_transaction_id).await?;
        // DB-BLOCK src_db_settlements_048
        // What: binds `settlement` as a named intermediate.
        // How: computes/extracts `settlement` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let settlement = queries::load_settlement(&mut tx, &settlement_id).await?;
        // DB-BLOCK src_db_settlements_049
        // What: binds `steps` as a named intermediate.
        // How: computes/extracts `steps` once before SQL or response construction.
        // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
        let steps = queries::settlement_steps(&mut tx, &settlement_id).await?;
        tx.commit().await?;
        // DB-BLOCK src_db_settlements_050
        // What: exits the current workflow early.
        // How: returns from `return Ok(RequestSettlementResponse { operation: None, trade_order: Some(proto_builders::` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Ok(RequestSettlementResponse {
            operation: None,
            trade_order: Some(proto_builders::trade_order_view(order)?),
            trade_transaction: Some(proto_builders::trade_transaction_view(trade_tx)?),
            settlement: Some(proto_builders::settlement_view(settlement)),
            settlement_steps: steps
                .into_iter()
                .map(proto_builders::settlement_step_view)
                .collect(),
            wallet_operation_id: replay
                .wallet_operation_id
                .map(|value| WalletOperationId { value }),
            item_stack_operation_id: replay
                .item_stack_operation_id
                .map(|value| ItemStackOperationId { value }),
            item_instance_operation_id: None,
            idempotent_replay: true,
            failure: None,
        });
    }

    // DB-BLOCK src_db_settlements_051
    // What: binds `operation_id` as a named intermediate.
    // How: computes/extracts `operation_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let operation_id = operation_log::create(&mut tx, &req.context, "settle_trade").await?;
    // DB-BLOCK src_db_settlements_052
    // What: binds `order_id` as a named intermediate.
    // How: computes/extracts `order_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let order_id = extract::trade_instance_id("trade_instance_id", &req.trade_instance_id)?;
    // DB-BLOCK src_db_settlements_053
    // What: binds `order` as a named intermediate.
    // How: computes/extracts `order` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let order = queries::lock_order(&mut tx, &order_id).await?;
    // DB-BLOCK src_db_settlements_054
    // What: binds `order_state` as a named intermediate.
    // How: computes/extracts `order_state` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let order_state = TradeState::from_db(&order.state)?;
    // DB-BLOCK src_db_settlements_055
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if order_state != TradeState::Outstanding {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if order_state != TradeState::Outstanding {
        // DB-BLOCK src_db_settlements_056
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::InvalidTransition { from: order.state.clone(), actio` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::InvalidTransition {
            from: order.state.clone(),
            action: "request_settlement",
        });
    }
    // DB-BLOCK src_db_settlements_057
    // What: binds `side` as a named intermediate.
    // How: computes/extracts `side` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let side = verify_roles(&order, req)?;
    // DB-BLOCK src_db_settlements_058
    // What: binds `trade_tx` as a named intermediate.
    // How: computes/extracts `trade_tx` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let trade_tx = create_transaction_if_needed(&mut tx, &operation_id, req, &order).await?;
    // DB-BLOCK src_db_settlements_059
    // What: binds `settlement_id` as a named intermediate.
    // How: computes/extracts `settlement_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let settlement_id = extract::settlement_id_optional("settlement_id", &req.settlement_id)?
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    // DB-BLOCK src_db_settlements_060
    // What: performs a parameterized SQL operation against `trade_transaction`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query("INSERT INTO trade.settlement (settlement_id, operation_id, trade_transaction_id, idempotency_key, state, settlement_phase) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'in_progress', 'created')")
        .bind(&settlement_id).bind(&operation_id).bind(&trade_tx.trade_transaction_id).bind(&guard.idempotency_key)
        .execute(&mut *tx).await?;
    set_phase(&mut tx, &settlement_id, "locked_trade").await?;

    // DB-BLOCK src_db_settlements_061
    // What: binds `wallet_op` as a named intermediate.
    // How: computes/extracts `wallet_op` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let wallet_op =
        ownership::create_wallet_operation(&mut tx, &operation_id, "settle_trade_wallets").await?;
    // DB-BLOCK src_db_settlements_062
    // What: binds `stack_op` as a named intermediate.
    // How: computes/extracts `stack_op` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let stack_op =
        ownership::create_stack_operation(&mut tx, &operation_id, "settle_trade_items").await?;

    // DB-BLOCK src_db_settlements_063
    // What: branches across known alternatives.
    // How: uses Rust `match` on `match side {`.
    // Why: closed branching is safer than ad-hoc string/boolean decision trees.
    match side {
        OrderSide::Buy => {
            // DB-BLOCK src_db_settlements_064
            // What: binds `wallet_res` as a named intermediate.
            // How: computes/extracts `wallet_res` once before SQL or response construction.
            // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
            let wallet_res = queries::lock_wallet_reservation(&mut tx, &order_id)
                .await?
                .ok_or_else(|| {
                    SettlementError::ReservationConflict(
                        "buy order missing wallet reservation".to_string(),
                    )
                })?;
            // DB-BLOCK src_db_settlements_065
            // What: guards a correctness-sensitive branch.
            // How: evaluates `if wallet_res.remaining_reserved_isk < trade_tx.total_price_isk { return Err(SettlementErr` before continuing.
            // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
            if wallet_res.remaining_reserved_isk < trade_tx.total_price_isk {
                return Err(SettlementError::InsufficientIsk {
                    wallet_id: wallet_res.wallet_id,
                });
            }
            ownership::move_wallet(
                &mut tx,
                &wallet_op,
                &trade_tx.buyer_wallet_id,
                0,
                -trade_tx.total_price_isk,
                "debit_reserved_for_trade",
            )
            .await?;
            ownership::move_wallet(
                &mut tx,
                &wallet_op,
                &trade_tx.seller_wallet_id,
                trade_tx.total_price_isk,
                0,
                "credit_from_trade",
            )
            .await?;
            // DB-BLOCK src_db_settlements_066
            // What: performs a parameterized SQL operation against `wallet_reservation`.
            // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
            // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
            sqlx::query("UPDATE trade.wallet_reservation SET remaining_reserved_isk = remaining_reserved_isk - $2, used_reserved_isk = used_reserved_isk + $2, reservation_state = CASE WHEN remaining_reserved_isk - $2 = 0 THEN 'used'::trade.reservation_state ELSE 'partially_used'::trade.reservation_state END, updated_at = now() WHERE wallet_reservation_id = $1::uuid")
                .bind(&wallet_res.wallet_reservation_id).bind(trade_tx.total_price_isk).execute(&mut *tx).await?;
        }
        OrderSide::Sell => {
            ownership::move_wallet(
                &mut tx,
                &wallet_op,
                &trade_tx.buyer_wallet_id,
                -trade_tx.total_price_isk,
                0,
                "debit_available_for_trade",
            )
            .await?;
            ownership::move_wallet(
                &mut tx,
                &wallet_op,
                &trade_tx.seller_wallet_id,
                trade_tx.total_price_isk,
                0,
                "credit_from_trade",
            )
            .await?;
        }
    }
    set_phase(&mut tx, &settlement_id, "wallet_moved").await?;

    // DB-BLOCK src_db_settlements_067
    // What: binds `source_stack_id` as a named intermediate.
    // How: computes/extracts `source_stack_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let source_stack_id = trade_tx.source_item_stack_id.clone().ok_or_else(|| {
        SettlementError::InvalidRequest("source_item_stack_id required".to_string())
    })?;
    // DB-BLOCK src_db_settlements_068
    // What: branches across known alternatives.
    // How: uses Rust `match` on `match side {`.
    // Why: closed branching is safer than ad-hoc string/boolean decision trees.
    match side {
        OrderSide::Sell => {
            // DB-BLOCK src_db_settlements_069
            // What: binds `stack_res` as a named intermediate.
            // How: computes/extracts `stack_res` once before SQL or response construction.
            // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
            let stack_res = queries::lock_stack_reservation(&mut tx, &order_id)
                .await?
                .ok_or_else(|| {
                    SettlementError::ReservationConflict(
                        "sell order missing stack reservation".to_string(),
                    )
                })?;
            // DB-BLOCK src_db_settlements_070
            // What: guards a correctness-sensitive branch.
            // How: evaluates `if stack_res.item_stack_id != source_stack_id { return Err(SettlementError::TradeMismatch ` before continuing.
            // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
            if stack_res.item_stack_id != source_stack_id {
                return Err(SettlementError::TradeMismatch {
                    trade_instance_id: order_id.clone(),
                });
            }
            ownership::move_stack(
                &mut tx,
                &stack_op,
                &source_stack_id,
                0,
                -trade_tx.quantity,
                "debit_reserved_for_trade",
            )
            .await?;
            // DB-BLOCK src_db_settlements_071
            // What: performs a parameterized SQL operation against `item_stack_reservation`.
            // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
            // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
            sqlx::query("UPDATE trade.item_stack_reservation SET remaining_reserved_quantity = remaining_reserved_quantity - $2, used_reserved_quantity = used_reserved_quantity + $2, reservation_state = CASE WHEN remaining_reserved_quantity - $2 = 0 THEN 'used'::trade.reservation_state ELSE 'partially_used'::trade.reservation_state END, updated_at = now() WHERE item_stack_reservation_id = $1::uuid")
                .bind(&stack_res.item_stack_reservation_id).bind(trade_tx.quantity).execute(&mut *tx).await?;
        }
        OrderSide::Buy => {
            ownership::move_stack(
                &mut tx,
                &stack_op,
                &source_stack_id,
                -trade_tx.quantity,
                0,
                "debit_available_for_trade",
            )
            .await?;
        }
    }
    // DB-BLOCK src_db_settlements_072
    // What: binds `destination_stack_id` as a named intermediate.
    // How: computes/extracts `destination_stack_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let destination_stack_id = if let Some(id) = trade_tx.destination_item_stack_id.clone() {
        id
    } else {
        ownership::create_empty_stack(
            &mut tx,
            &trade_tx.buyer_capsuleer_id,
            &trade_tx.item_type_id,
            &order.station_id,
        )
        .await?
        .item_stack_id
    };
    ownership::move_stack(
        &mut tx,
        &stack_op,
        &destination_stack_id,
        trade_tx.quantity,
        0,
        "credit_from_trade",
    )
    .await?;
    set_phase(&mut tx, &settlement_id, "items_moved").await?;

    // DB-BLOCK src_db_settlements_073
    // What: performs a parameterized SQL operation against `trade_order`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query("UPDATE trade.trade_instance SET remaining_quantity = remaining_quantity - $2, state = CASE WHEN remaining_quantity - $2 = 0 THEN 'completed'::trade.trade_state ELSE state END, updated_at = now() WHERE trade_instance_id = $1::uuid")
        .bind(&order_id).bind(trade_tx.quantity).execute(&mut *tx).await?;
    // DB-BLOCK src_db_settlements_074
    // What: performs a parameterized SQL operation against `trade_transaction`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query("UPDATE trade.trade_transaction SET state = 'completed', destination_item_stack_id = $2::uuid, completed_at = now(), updated_at = now() WHERE trade_transaction_id = $1::uuid")
        .bind(&trade_tx.trade_transaction_id).bind(&destination_stack_id).execute(&mut *tx).await?;
    // DB-BLOCK src_db_settlements_075
    // What: performs a parameterized SQL operation against `settlement`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query("UPDATE trade.settlement SET state = 'completed', settlement_phase = 'completed', decided_at = now() WHERE settlement_id = $1::uuid")
        .bind(&settlement_id).execute(&mut *tx).await?;
    set_phase(&mut tx, &settlement_id, "completed").await?;
    ownership::complete_wallet_operation(&mut tx, &wallet_op).await?;
    ownership::complete_stack_operation(&mut tx, &stack_op).await?;
    operation_log::complete(&mut tx, &operation_id).await?;
    idempotency::record_success(
        &mut tx,
        idempotency::RecordSuccessInput {
            guard: &guard,
            result_kind: "request_settlement",
            operation_id: Some(&operation_id),
            trade_instance_id: Some(&order_id),
            trade_transaction_id: Some(&trade_tx.trade_transaction_id),
            settlement_id: Some(&settlement_id),
            wallet_operation_id: Some(&wallet_op),
            item_stack_operation_id: Some(&stack_op),
            result_state: TradeState::Completed.as_db(),
        },
    )
    .await?;

    // DB-BLOCK src_db_settlements_076
    // What: binds `operation` as a named intermediate.
    // How: computes/extracts `operation` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let operation = operation_log::load(&mut tx, &operation_id).await?;
    // DB-BLOCK src_db_settlements_077
    // What: binds `order` as a named intermediate.
    // How: computes/extracts `order` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let order = queries::load_order(&mut tx, &order_id).await?;
    // DB-BLOCK src_db_settlements_078
    // What: binds `trade_tx` as a named intermediate.
    // How: computes/extracts `trade_tx` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let trade_tx = queries::load_transaction(&mut tx, &trade_tx.trade_transaction_id).await?;
    // DB-BLOCK src_db_settlements_079
    // What: binds `settlement` as a named intermediate.
    // How: computes/extracts `settlement` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let settlement = queries::load_settlement(&mut tx, &settlement_id).await?;
    // DB-BLOCK src_db_settlements_080
    // What: binds `steps` as a named intermediate.
    // How: computes/extracts `steps` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let steps = queries::settlement_steps(&mut tx, &settlement_id).await?;
    tx.commit().await?;

    // DB-BLOCK src_db_settlements_081
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(RequestSettlementResponse { operation: Some(proto_builders::operation_view(operation)?`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(RequestSettlementResponse {
        operation: Some(proto_builders::operation_view(operation)?),
        trade_order: Some(proto_builders::trade_order_view(order)?),
        trade_transaction: Some(proto_builders::trade_transaction_view(trade_tx)?),
        settlement: Some(proto_builders::settlement_view(settlement)),
        settlement_steps: steps
            .into_iter()
            .map(proto_builders::settlement_step_view)
            .collect(),
        wallet_operation_id: Some(WalletOperationId { value: wallet_op }),
        item_stack_operation_id: Some(ItemStackOperationId { value: stack_op }),
        item_instance_operation_id: None,
        idempotent_replay: false,
        failure: None,
    })
}

// DB-BLOCK src_db_settlements_082
// What: returns transaction state and related settlement if present.
// How: loads trade_transaction and optional settlement rows in one read transaction.
// Why: callers need state visibility after asynchronous/retried settlement attempts.
pub async fn get_transaction_state(
    pool: &PgPool,
    req: &GetTransactionStateRequest,
) -> Result<GetTransactionStateResponse, SettlementError> {
    // DB-BLOCK src_db_settlements_083
    // What: binds `id` as a named intermediate.
    // How: computes/extracts `id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let id = extract::trade_transaction_id("trade_transaction_id", &req.trade_transaction_id)?;
    // DB-BLOCK src_db_settlements_084
    // What: opens a SQL transaction.
    // How: calls `pool.begin()` and passes the transaction through subsequent DB work.
    // Why: related writes must commit or roll back as one atomic unit.
    let mut tx = pool.begin().await?;
    // DB-BLOCK src_db_settlements_085
    // What: binds `trade_tx` as a named intermediate.
    // How: computes/extracts `trade_tx` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let trade_tx = queries::load_transaction(&mut tx, &id).await?;
    // DB-BLOCK src_db_settlements_086
    // What: binds `settlement` as a named intermediate.
    // How: computes/extracts `settlement` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let settlement = sqlx::query_as::<_, crate::db::rows::SettlementRow>(
        r#"SELECT settlement_id::text AS settlement_id, operation_id::text AS operation_id, trade_transaction_id::text AS trade_transaction_id, idempotency_key, state::text AS state, settlement_phase::text AS settlement_phase, retry_count, started_at, decided_at, failure_message FROM trade.settlement WHERE trade_transaction_id = $1::uuid"#
    ).bind(&id).fetch_optional(&mut *tx).await?;
    tx.commit().await?;
    // DB-BLOCK src_db_settlements_087
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(GetTransactionStateResponse { trade_transaction: Some(proto_builders::trade_t`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(GetTransactionStateResponse {
        trade_transaction: Some(proto_builders::trade_transaction_view(trade_tx)?),
        settlement: settlement.map(proto_builders::settlement_view),
    })
}

// DB-BLOCK src_db_settlements_088
// What: returns settlement details and step history.
// How: loads settlement by ID and maps settlement_step rows to protobuf.
// Why: phase/step history is needed for crash diagnosis and operator confidence.
pub async fn get_settlement(
    pool: &PgPool,
    req: &GetSettlementRequest,
) -> Result<GetSettlementResponse, SettlementError> {
    // DB-BLOCK src_db_settlements_089
    // What: binds `id` as a named intermediate.
    // How: computes/extracts `id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let id = extract::settlement_id_optional("settlement_id", &req.settlement_id)?
        .ok_or_else(|| SettlementError::InvalidRequest("settlement_id is required".to_string()))?;
    // DB-BLOCK src_db_settlements_090
    // What: opens a SQL transaction.
    // How: calls `pool.begin()` and passes the transaction through subsequent DB work.
    // Why: related writes must commit or roll back as one atomic unit.
    let mut tx = pool.begin().await?;
    // DB-BLOCK src_db_settlements_091
    // What: binds `settlement` as a named intermediate.
    // How: computes/extracts `settlement` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let settlement = queries::load_settlement(&mut tx, &id).await?;
    // DB-BLOCK src_db_settlements_092
    // What: binds `steps` as a named intermediate.
    // How: computes/extracts `steps` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let steps = queries::settlement_steps(&mut tx, &id).await?;
    tx.commit().await?;
    // DB-BLOCK src_db_settlements_093
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(GetSettlementResponse { settlement: Some(proto_builders::settlement_view(sett`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(GetSettlementResponse {
        settlement: Some(proto_builders::settlement_view(settlement)),
        settlement_steps: steps
            .into_iter()
            .map(proto_builders::settlement_step_view)
            .collect(),
    })
}
