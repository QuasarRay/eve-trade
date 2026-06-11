use std::sync::OnceLock;

use crate::error::SettlementError;
use crate::generated::settlement::{SettleTradeRequest, TradeAction, TradeState};
use crate::idempotency::request_hash;
use crate::state::{
    action_to_db, next_state_without_asset_movement, state_from_db, state_to_db,
};
use crate::validation::validate_request;
use sqlx::{PgPool, Postgres, Transaction};

// This global pool is initialized once by main before summer-grpc starts serving
// requests. It keeps the gRPC service struct fieldless, which matches the current
// summer-grpc auto-registration model while still using one shared connection
// pool for all RPC calls.
static DB_POOL: OnceLock<PgPool> = OnceLock::new();

// This block initializes the database pool exactly once. If initialization fails,
// the service must not start, because returning COMPLETED without a database is
// invalid by definition.
pub async fn initialize_pool(database_url: &str) -> Result<(), SettlementError> {
    let pool = PgPool::connect(database_url).await?;
    DB_POOL
        .set(pool)
        .map_err(|_| SettlementError::PoolAlreadyInitialized)?;
    Ok(())
}

// This block gives service.rs read access to the shared pool. Failing fast here
// protects against a misconfigured executable that starts the gRPC boundary before
// database initialization.
pub fn pool() -> Result<&'static PgPool, SettlementError> {
    DB_POOL.get().ok_or(SettlementError::PoolNotInitialized)
}

// This struct mirrors one locked row from the trades table. It exists because the
// settlement logic must compare the current durable trade fields against the exact
// request received from market before any ownership movement is attempted.
#[derive(Debug, sqlx::FromRow)]
struct TradeRecord {
    trade_id: String,
    state: String,
    item_owner_id: String,
    item_receiver_id: String,
    isk_payer_id: String,
    isk_receiver_id: String,
    item_type_id: String,
    quantity: i64,
    isk_units: i64,
}

// This struct represents a previously recorded market request. It exists only for
// idempotency: same request_id + same hash returns the same recorded state;
// same request_id + different hash is rejected as unsafe.
#[derive(Debug, sqlx::FromRow)]
struct RecordedRequest {
    request_hash: String,
    resulting_state: String,
    response_message: String,
}

// This is the public database entrypoint used by service.rs. It validates the
// request, checks idempotency, runs the requested trade action in one database
// transaction, records the idempotent result, and commits before returning.
pub async fn settle_trade(
    pool: &PgPool,
    req: &SettleTradeRequest,
) -> Result<(TradeState, String), SettlementError> {
    let action = validate_request(req)?;
    let hash = request_hash(req);
    let action_name = action_to_db(action)?;

    let mut tx = pool.begin().await?;

    if let Some(recorded) = lock_recorded_request(&mut tx, &req.request_id).await? {
        if recorded.request_hash != hash {
            return Err(SettlementError::RequestIdConflict);
        }

        let state = state_from_db(&recorded.resulting_state)?;
        tx.commit().await?;
        return Ok((state, recorded.response_message));
    }

    let (state, message) = match action {
        TradeAction::Prepare => prepare_trade(&mut tx, req).await?,
        TradeAction::Issue
        | TradeAction::Accept
        | TradeAction::Start
        | TradeAction::MakeClaimable
        | TradeAction::Claim
        | TradeAction::Expire
        | TradeAction::Fail
        | TradeAction::Cancel => transition_trade_without_asset_movement(&mut tx, req, action).await?,
        TradeAction::Complete => complete_trade(&mut tx, req).await?,
        TradeAction::Unspecified => {
            return Err(SettlementError::InvalidRequest(
                "TRADE_ACTION_UNSPECIFIED is not allowed".to_string(),
            ))
        }
    };

    record_request_result(&mut tx, req, action_name, &hash, state, &message).await?;
    tx.commit().await?;

    Ok((state, message))
}

// This block locks an idempotency row if the same request_id was already seen.
// FOR UPDATE ensures concurrent retries with the same request_id serialize behind
// the first writer instead of racing to perform the trade twice.
async fn lock_recorded_request(
    tx: &mut Transaction<'_, Postgres>,
    request_id: &str,
) -> Result<Option<RecordedRequest>, SettlementError> {
    let row = sqlx::query_as::<_, RecordedRequest>(
        r#"
        SELECT request_hash, resulting_state, response_message
        FROM settlement_requests
        WHERE request_id = $1
        FOR UPDATE
        "#,
    )
    .bind(request_id)
    .fetch_optional(&mut **tx)
    .await?;

    Ok(row)
}

// This block records the result before commit. If commit succeeds, a later retry
// can return the same state without repeating item/ISK movement. If commit fails,
// this row rolls back with the rest of the trade changes.
async fn record_request_result(
    tx: &mut Transaction<'_, Postgres>,
    req: &SettleTradeRequest,
    action_name: &str,
    hash: &str,
    state: TradeState,
    message: &str,
) -> Result<(), SettlementError> {
    sqlx::query(
        r#"
        INSERT INTO settlement_requests
            (request_id, trade_id, action, request_hash, resulting_state, response_message)
        VALUES ($1, $2, $3, $4, $5, $6)
        "#,
    )
    .bind(&req.request_id)
    .bind(&req.trade_id)
    .bind(action_name)
    .bind(hash)
    .bind(state_to_db(state)?)
    .bind(message)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

// PREPARE is the only action that may create a trade row. It persists the exact
// market request fields so later ACCEPT/START/COMPLETE/CANCEL/EXPIRE requests can
// be checked against the original trade definition.
async fn prepare_trade(
    tx: &mut Transaction<'_, Postgres>,
    req: &SettleTradeRequest,
) -> Result<(TradeState, String), SettlementError> {
    if let Some(existing) = lock_trade(tx, &req.trade_id).await? {
        ensure_request_matches_trade(req, &existing)?;
        let state = state_from_db(&existing.state)?;
        if state == TradeState::BeingCreated {
            return Ok((state, "trade was already being created".to_string()));
        }
        return Err(SettlementError::InvalidTransition {
            from: state,
            action: "prepare",
        });
    }

    sqlx::query(
        r#"
        INSERT INTO trades (
            trade_id, state,
            item_owner_id, item_receiver_id,
            isk_payer_id, isk_receiver_id,
            item_type_id, quantity, isk_units
        )
        VALUES ($1, 'being_created', $2, $3, $4, $5, $6, $7, $8)
        "#,
    )
    .bind(&req.trade_id)
    .bind(&req.item_owner_id)
    .bind(&req.item_receiver_id)
    .bind(&req.isk_payer_id)
    .bind(&req.isk_receiver_id)
    .bind(&req.item_type_id)
    .bind(req.quantity)
    .bind(req.isk_units)
    .execute(&mut **tx)
    .await?;

    Ok((
        TradeState::BeingCreated,
        "trade record prepared with exact market request fields".to_string(),
    ))
}

// This block handles all legal state transitions that do not move ownership. It
// still locks the trade row and verifies the request fields so market cannot
// change item/ISK details between lifecycle steps.
async fn transition_trade_without_asset_movement(
    tx: &mut Transaction<'_, Postgres>,
    req: &SettleTradeRequest,
    action: TradeAction,
) -> Result<(TradeState, String), SettlementError> {
    let trade = require_locked_trade(tx, &req.trade_id).await?;
    ensure_request_matches_trade(req, &trade)?;

    let current = state_from_db(&trade.state)?;
    let next = next_state_without_asset_movement(current, action)?;

    if next == current {
        return Ok((
            next,
            format!("trade was already in state {}", state_to_db(next)?),
        ));
    }

    update_trade_state(tx, &req.trade_id, next, None).await?;

    Ok((
        next,
        format!(
            "trade moved from {} to {}",
            state_to_db(current)?,
            state_to_db(next)?
        ),
    ))
}

// COMPLETE is the core correctness path. It is the only function allowed to move
// ISK/items. It returns COMPLETED only after wallet debit, wallet credit, item
// debit, item credit, state update, idempotency insert, and COMMIT all succeed.
async fn complete_trade(
    tx: &mut Transaction<'_, Postgres>,
    req: &SettleTradeRequest,
) -> Result<(TradeState, String), SettlementError> {
    let trade = require_locked_trade(tx, &req.trade_id).await?;
    ensure_request_matches_trade(req, &trade)?;

    let current = state_from_db(&trade.state)?;

    if matches!(
        current,
        TradeState::Completed | TradeState::Claimable | TradeState::Claimed
    ) {
        return Ok((
            current,
            "trade was already completed; ownership movement was not repeated".to_string(),
        ));
    }

    if current != TradeState::InProgress {
        return Err(SettlementError::InvalidTransition {
            from: current,
            action: "complete",
        });
    }

    let payer_isk = lock_wallet_balance(tx, &req.isk_payer_id).await?;
    if payer_isk < req.isk_units {
        update_trade_state(
            tx,
            &req.trade_id,
            TradeState::Failed,
            Some("isk payer does not have enough ISK"),
        )
        .await?;
        return Ok((
            TradeState::Failed,
            "trade failed because ISK payer had insufficient balance".to_string(),
        ));
    }

    let owner_quantity = lock_item_quantity(tx, &req.item_owner_id, &req.item_type_id).await?;
    if owner_quantity < req.quantity {
        update_trade_state(
            tx,
            &req.trade_id,
            TradeState::Failed,
            Some("item owner does not have enough item quantity"),
        )
        .await?;
        return Ok((
            TradeState::Failed,
            "trade failed because item owner had insufficient quantity".to_string(),
        ));
    }

    debit_wallet(tx, &req.isk_payer_id, req.isk_units).await?;
    credit_wallet(tx, &req.isk_receiver_id, req.isk_units).await?;
    debit_item_stack(tx, &req.item_owner_id, &req.item_type_id, req.quantity).await?;
    credit_item_stack(tx, &req.item_receiver_id, &req.item_type_id, req.quantity).await?;
    update_trade_state(tx, &req.trade_id, TradeState::Completed, None).await?;

    Ok((
        TradeState::Completed,
        "trade completed; item and ISK ownership were committed atomically".to_string(),
    ))
}

// This block locks a trade row for a known trade_id. Every state-changing path
// calls this before making a decision, which serializes concurrent requests for
// the same trade.
async fn lock_trade(
    tx: &mut Transaction<'_, Postgres>,
    trade_id: &str,
) -> Result<Option<TradeRecord>, SettlementError> {
    let trade = sqlx::query_as::<_, TradeRecord>(
        r#"
        SELECT
            trade_id, state,
            item_owner_id, item_receiver_id,
            isk_payer_id, isk_receiver_id,
            item_type_id, quantity, isk_units
        FROM trades
        WHERE trade_id = $1
        FOR UPDATE
        "#,
    )
    .bind(trade_id)
    .fetch_optional(&mut **tx)
    .await?;

    Ok(trade)
}

// This block turns an optional locked row into a required one. It keeps missing
// trade behavior consistent across ISSUE/ACCEPT/START/COMPLETE/etc.
async fn require_locked_trade(
    tx: &mut Transaction<'_, Postgres>,
    trade_id: &str,
) -> Result<TradeRecord, SettlementError> {
    lock_trade(tx, trade_id)
        .await?
        .ok_or_else(|| SettlementError::TradeNotFound {
            trade_id: trade_id.to_string(),
        })
}

// This block protects against a dangerous class of bugs: market prepares one set
// of ownership movement fields, then later asks settlement to complete a different
// set of fields under the same trade_id.
fn ensure_request_matches_trade(
    req: &SettleTradeRequest,
    trade: &TradeRecord,
) -> Result<(), SettlementError> {
    let matches = trade.trade_id == req.trade_id
        && trade.item_owner_id == req.item_owner_id
        && trade.item_receiver_id == req.item_receiver_id
        && trade.isk_payer_id == req.isk_payer_id
        && trade.isk_receiver_id == req.isk_receiver_id
        && trade.item_type_id == req.item_type_id
        && trade.quantity == req.quantity
        && trade.isk_units == req.isk_units;

    if matches {
        Ok(())
    } else {
        Err(SettlementError::TradeMismatch {
            trade_id: req.trade_id.clone(),
        })
    }
}

// This block updates the durable trade state and the matching timestamp column.
// Keeping timestamps here makes it harder for a state transition to forget its
// own audit timestamp.
async fn update_trade_state(
    tx: &mut Transaction<'_, Postgres>,
    trade_id: &str,
    state: TradeState,
    failure_reason: Option<&str>,
) -> Result<(), SettlementError> {
    let state_name = state_to_db(state)?;

    match state {
        TradeState::InProgress => {
            sqlx::query(
                r#"
                UPDATE trades
                SET state = $2, started_at = now(), updated_at = now(), failure_reason = $3
                WHERE trade_id = $1
                "#,
            )
            .bind(trade_id)
            .bind(state_name)
            .bind(failure_reason)
            .execute(&mut **tx)
            .await?;
        }
        TradeState::Completed => {
            sqlx::query(
                r#"
                UPDATE trades
                SET state = $2, completed_at = now(), updated_at = now(), failure_reason = NULL
                WHERE trade_id = $1
                "#,
            )
            .bind(trade_id)
            .bind(state_name)
            .execute(&mut **tx)
            .await?;
        }
        TradeState::Claimable => {
            sqlx::query(
                r#"
                UPDATE trades
                SET state = $2, claimable_at = now(), updated_at = now()
                WHERE trade_id = $1
                "#,
            )
            .bind(trade_id)
            .bind(state_name)
            .execute(&mut **tx)
            .await?;
        }
        TradeState::Claimed => {
            sqlx::query(
                r#"
                UPDATE trades
                SET state = $2, claimed_at = now(), updated_at = now()
                WHERE trade_id = $1
                "#,
            )
            .bind(trade_id)
            .bind(state_name)
            .execute(&mut **tx)
            .await?;
        }
        TradeState::Expired => {
            sqlx::query(
                r#"
                UPDATE trades
                SET state = $2, expired_at = now(), updated_at = now()
                WHERE trade_id = $1
                "#,
            )
            .bind(trade_id)
            .bind(state_name)
            .execute(&mut **tx)
            .await?;
        }
        TradeState::Failed => {
            sqlx::query(
                r#"
                UPDATE trades
                SET state = $2, failed_at = now(), updated_at = now(), failure_reason = $3
                WHERE trade_id = $1
                "#,
            )
            .bind(trade_id)
            .bind(state_name)
            .bind(failure_reason)
            .execute(&mut **tx)
            .await?;
        }
        TradeState::Cancelled => {
            sqlx::query(
                r#"
                UPDATE trades
                SET state = $2, cancelled_at = now(), updated_at = now()
                WHERE trade_id = $1
                "#,
            )
            .bind(trade_id)
            .bind(state_name)
            .execute(&mut **tx)
            .await?;
        }
        TradeState::Outstanding | TradeState::Accepted | TradeState::BeingCreated => {
            sqlx::query(
                r#"
                UPDATE trades
                SET state = $2, updated_at = now()
                WHERE trade_id = $1
                "#,
            )
            .bind(trade_id)
            .bind(state_name)
            .execute(&mut **tx)
            .await?;
        }
        TradeState::Unspecified => {
            return Err(SettlementError::InvalidRequest(
                "TRADE_STATE_UNSPECIFIED cannot be written".to_string(),
            ));
        }
    }

    Ok(())
}

// This block locks the ISK payer wallet row before checking balance. FOR UPDATE
// prevents two concurrent trades from both seeing the same pre-debit balance.
async fn lock_wallet_balance(
    tx: &mut Transaction<'_, Postgres>,
    capsuleer_id: &str,
) -> Result<i64, SettlementError> {
    let balance: Option<(i64,)> = sqlx::query_as(
        r#"
        SELECT isk_units
        FROM wallets
        WHERE capsuleer_id = $1
        FOR UPDATE
        "#,
    )
    .bind(capsuleer_id)
    .fetch_optional(&mut **tx)
    .await?;

    Ok(balance.map(|row| row.0).unwrap_or(0))
}

// This block locks the item-owner stack before checking quantity. FOR UPDATE
// prevents concurrent settlements from spending the same item stack twice.
async fn lock_item_quantity(
    tx: &mut Transaction<'_, Postgres>,
    capsuleer_id: &str,
    item_type_id: &str,
) -> Result<i64, SettlementError> {
    let quantity: Option<(i64,)> = sqlx::query_as(
        r#"
        SELECT quantity
        FROM item_stacks
        WHERE capsuleer_id = $1 AND item_type_id = $2
        FOR UPDATE
        "#,
    )
    .bind(capsuleer_id)
    .bind(item_type_id)
    .fetch_optional(&mut **tx)
    .await?;

    Ok(quantity.map(|row| row.0).unwrap_or(0))
}

// This block debits ISK only after the balance has already been locked and
// checked. The database non-negative CHECK constraint remains as a final guard.
async fn debit_wallet(
    tx: &mut Transaction<'_, Postgres>,
    capsuleer_id: &str,
    isk_units: i64,
) -> Result<(), SettlementError> {
    sqlx::query(
        r#"
        UPDATE wallets
        SET isk_units = isk_units - $2, updated_at = now()
        WHERE capsuleer_id = $1
        "#,
    )
    .bind(capsuleer_id)
    .bind(isk_units)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

// This block credits ISK to the receiver. Upsert is used so a new recipient
// wallet can be created without a separate read-before-write path.
async fn credit_wallet(
    tx: &mut Transaction<'_, Postgres>,
    capsuleer_id: &str,
    isk_units: i64,
) -> Result<(), SettlementError> {
    sqlx::query(
        r#"
        INSERT INTO wallets (capsuleer_id, isk_units)
        VALUES ($1, $2)
        ON CONFLICT (capsuleer_id)
        DO UPDATE SET isk_units = wallets.isk_units + EXCLUDED.isk_units,
                      updated_at = now()
        "#,
    )
    .bind(capsuleer_id)
    .bind(isk_units)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

// This block debits the item owner stack only after the stack has already been
// locked and checked. The database non-negative CHECK constraint remains as a
// final guard against negative inventory.
async fn debit_item_stack(
    tx: &mut Transaction<'_, Postgres>,
    capsuleer_id: &str,
    item_type_id: &str,
    quantity: i64,
) -> Result<(), SettlementError> {
    sqlx::query(
        r#"
        UPDATE item_stacks
        SET quantity = quantity - $3, updated_at = now()
        WHERE capsuleer_id = $1 AND item_type_id = $2
        "#,
    )
    .bind(capsuleer_id)
    .bind(item_type_id)
    .bind(quantity)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

// This block credits the item receiver stack. Upsert is used so receiving a new
// item type does not require a pre-existing stack row.
async fn credit_item_stack(
    tx: &mut Transaction<'_, Postgres>,
    capsuleer_id: &str,
    item_type_id: &str,
    quantity: i64,
) -> Result<(), SettlementError> {
    sqlx::query(
        r#"
        INSERT INTO item_stacks (capsuleer_id, item_type_id, quantity)
        VALUES ($1, $2, $3)
        ON CONFLICT (capsuleer_id, item_type_id)
        DO UPDATE SET quantity = item_stacks.quantity + EXCLUDED.quantity,
                      updated_at = now()
        "#,
    )
    .bind(capsuleer_id)
    .bind(item_type_id)
    .bind(quantity)
    .execute(&mut **tx)
    .await?;

    Ok(())
}
