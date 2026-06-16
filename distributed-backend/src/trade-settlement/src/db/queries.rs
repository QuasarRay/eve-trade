#![allow(clippy::too_many_arguments)]

use chrono::{DateTime, Utc};
use sqlx::PgPool;
use uuid::Uuid;

use crate::error::SettlementError;
use crate::generated::eve_trade::settlement::v1::SettlementStep;

use super::{
    responses::{settlement_phase_for_step, settlement_step_proto},
    support::{tx_conn, DbTx},
    types::*,
};

pub(crate) async fn load_trade_instance(
    pool: &PgPool,
    trade_instance_id: Uuid,
) -> Result<TradeInstanceRow, SettlementError> {
    Ok(sqlx::query_as::<_, TradeInstanceRow>(
        r#"
        SELECT trade_instance_id, operation_id, trade_state, issuer_id, issuer_wallet_id,
               item_type_id, station_id, region_id, total_quantity, remaining_quantity,
               (unit_price_isk * 100)::bigint AS unit_price_minor,
               expires_at, created_at, updated_at
        FROM trade_instance
        WHERE trade_instance_id = $1
        "#,
    )
    .bind(trade_instance_id)
    .fetch_one(pool)
    .await?)
}

pub(crate) async fn load_trade_instance_tx(
    tx: &mut DbTx<'_>,
    trade_instance_id: Uuid,
) -> Result<TradeInstanceRow, SettlementError> {
    Ok(sqlx::query_as::<_, TradeInstanceRow>(
        r#"
        SELECT trade_instance_id, operation_id, trade_state, issuer_id, issuer_wallet_id,
               item_type_id, station_id, region_id, total_quantity, remaining_quantity,
               (unit_price_isk * 100)::bigint AS unit_price_minor,
               expires_at, created_at, updated_at
        FROM trade_instance
        WHERE trade_instance_id = $1
        "#,
    )
    .bind(trade_instance_id)
    .fetch_one(tx_conn(tx))
    .await?)
}

pub(crate) async fn load_item_stack(
    pool: &PgPool,
    item_stack_id: Uuid,
) -> Result<ItemStackRow, SettlementError> {
    Ok(sqlx::query_as::<_, ItemStackRow>(
        r#"
        SELECT s.item_stack_id, s.owner_id, s.item_type_id, s.station_id, st.region_id,
               s.quantity, s.stack_state, s.stack_version, s.stack_checksum
        FROM item_stack s
        JOIN station st ON st.station_id = s.station_id
        WHERE s.item_stack_id = $1
        "#,
    )
    .bind(item_stack_id)
    .fetch_one(pool)
    .await?)
}

pub(crate) async fn load_wallet(pool: &PgPool, wallet_id: Uuid) -> Result<WalletRow, SettlementError> {
    Ok(sqlx::query_as::<_, WalletRow>(
        r#"
        SELECT wallet_id, capsuleer_id, (isk_amount * 100)::bigint AS isk_minor,
               wallet_state, wallet_version, wallet_checksum
        FROM wallet
        WHERE wallet_id = $1
        "#,
    )
    .bind(wallet_id)
    .fetch_one(pool)
    .await?)
}

pub(crate) async fn load_item_stack_escrow(
    pool: &PgPool,
    item_stack_escrow_id: Uuid,
) -> Result<ItemStackEscrowRow, SettlementError> {
    Ok(sqlx::query_as::<_, ItemStackEscrowRow>(
        r#"
        SELECT item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
               created_at, updated_at, released_at, escrow_state, release_reason,
               source_item_stack_id
        FROM item_stack_escrow
        WHERE item_stack_escrow_id = $1
        "#,
    )
    .bind(item_stack_escrow_id)
    .fetch_one(pool)
    .await?)
}

pub(crate) async fn load_releasable_item_stack_escrows(
    pool: &PgPool,
    trade_instance_id: Uuid,
) -> Result<Vec<ItemStackEscrowRow>, SettlementError> {
    Ok(sqlx::query_as::<_, ItemStackEscrowRow>(
        r#"
        SELECT item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
               created_at, updated_at, released_at, escrow_state, release_reason,
               source_item_stack_id
        FROM item_stack_escrow
        WHERE trade_instance_id = $1
          AND quantity > 0
          AND escrow_state IN ('held', 'partially_used')
        ORDER BY created_at, item_stack_escrow_id
        "#,
    )
    .bind(trade_instance_id)
    .fetch_all(pool)
    .await?)
}

pub(crate) async fn load_item_stack_escrows_tx(
    tx: &mut DbTx<'_>,
    trade_instance_id: Uuid,
) -> Result<Vec<ItemStackEscrowRow>, SettlementError> {
    Ok(sqlx::query_as::<_, ItemStackEscrowRow>(
        r#"
        SELECT item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
               created_at, updated_at, released_at, escrow_state, release_reason,
               source_item_stack_id
        FROM item_stack_escrow
        WHERE trade_instance_id = $1
        ORDER BY created_at, item_stack_escrow_id
        "#,
    )
    .bind(trade_instance_id)
    .fetch_all(tx_conn(tx))
    .await?)
}

pub(crate) async fn load_wallet_escrows_tx(
    tx: &mut DbTx<'_>,
    trade_instance_id: Uuid,
) -> Result<Vec<WalletEscrowRow>, SettlementError> {
    Ok(sqlx::query_as::<_, WalletEscrowRow>(
        r#"
        SELECT wallet_escrow_id, trade_instance_id, (isk_amount * 100)::bigint AS isk_minor,
               owner_id, created_wallet_operation_id, released_wallet_operation_id,
               created_at, updated_at, released_at
        FROM wallet_escrow
        WHERE trade_instance_id = $1
        ORDER BY created_at, wallet_escrow_id
        "#,
    )
    .bind(trade_instance_id)
    .fetch_all(tx_conn(tx))
    .await?)
}

pub(crate) async fn insert_trade_transaction(
    tx: &mut DbTx<'_>,
    operation_id: Uuid,
    trade_transaction_id: Uuid,
    trade_instance_id: Uuid,
    state: &str,
    buyer_capsuleer_id: i64,
    buyer_wallet_id: Uuid,
    seller_capsuleer_id: i64,
    seller_wallet_id: Uuid,
    item_type_id: i64,
    source_item_stack_escrow_id: Uuid,
    destination_item_stack_id: Option<Uuid>,
    quantity: i64,
    unit_price_minor: i64,
    total_price_minor: i64,
    at: DateTime<Utc>,
) -> Result<TradeTransactionRow, SettlementError> {
    Ok(sqlx::query_as::<_, TradeTransactionRow>(
        r#"
        INSERT INTO trade_transaction (
            trade_transaction_id, operation_id, trade_instance_id, trade_transaction_state,
            buyer_capsuleer_id, buyer_wallet_id, seller_capsuleer_id, seller_wallet_id,
            item_type_id, source_item_stack_id, destination_item_stack_id, quantity,
            unit_price_isk, total_price_isk, created_at, updated_at, completed_at
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
            ($13::numeric / 100), ($14::numeric / 100), $15, $15, $15
        )
        RETURNING trade_transaction_id, operation_id, trade_instance_id,
                  trade_transaction_state, buyer_capsuleer_id, buyer_wallet_id,
                  seller_capsuleer_id, seller_wallet_id, item_type_id,
                  source_item_stack_id AS source_item_stack_escrow_id,
                  destination_item_stack_id, quantity,
                  (unit_price_isk * 100)::bigint AS unit_price_minor,
                  (total_price_isk * 100)::bigint AS total_price_minor,
                  created_at, updated_at, completed_at
        "#,
    )
    .bind(trade_transaction_id)
    .bind(operation_id)
    .bind(trade_instance_id)
    .bind(state)
    .bind(buyer_capsuleer_id)
    .bind(buyer_wallet_id)
    .bind(seller_capsuleer_id)
    .bind(seller_wallet_id)
    .bind(item_type_id)
    .bind(source_item_stack_escrow_id)
    .bind(destination_item_stack_id)
    .bind(quantity)
    .bind(unit_price_minor)
    .bind(total_price_minor)
    .bind(at)
    .fetch_one(tx_conn(tx))
    .await?)
}

pub(crate) async fn load_trade_transaction_tx(
    tx: &mut DbTx<'_>,
    trade_transaction_id: Uuid,
) -> Result<TradeTransactionRow, SettlementError> {
    Ok(sqlx::query_as::<_, TradeTransactionRow>(
        r#"
        SELECT trade_transaction_id, operation_id, trade_instance_id,
               trade_transaction_state, buyer_capsuleer_id, buyer_wallet_id,
               seller_capsuleer_id, seller_wallet_id, item_type_id,
               source_item_stack_id AS source_item_stack_escrow_id,
               destination_item_stack_id, quantity,
               (unit_price_isk * 100)::bigint AS unit_price_minor,
               (total_price_isk * 100)::bigint AS total_price_minor,
               created_at, updated_at, completed_at
        FROM trade_transaction
        WHERE trade_transaction_id = $1
        "#,
    )
    .bind(trade_transaction_id)
    .fetch_one(tx_conn(tx))
    .await?)
}

pub(crate) async fn insert_settlement(
    tx: &mut DbTx<'_>,
    settlement_id: Uuid,
    operation_id: Uuid,
    trade_transaction_id: Uuid,
    idempotency_key: &str,
    state: &str,
    phase: &str,
    at: DateTime<Utc>,
    failure_code: Option<&str>,
    failure_message: Option<&str>,
) -> Result<(), SettlementError> {
    sqlx::query(
        r#"
        INSERT INTO settlement (
            settlement_id, operation_id, trade_transaction_id, idempotency_key,
            settlement_state, settlement_phase, retry_count, started_at, decided_at,
            failure_code, failure_message
        )
        VALUES ($1, $2, $3, $4, $5, $6, 0, $7, $7, $8, $9)
        "#,
    )
    .bind(settlement_id)
    .bind(operation_id)
    .bind(trade_transaction_id)
    .bind(idempotency_key)
    .bind(state)
    .bind(phase)
    .bind(at)
    .bind(failure_code)
    .bind(failure_message)
    .execute(tx_conn(tx))
    .await?;
    Ok(())
}

pub(crate) async fn create_settlement_steps(
    tx: &mut DbTx<'_>,
    settlement_id: Uuid,
) -> Result<Vec<SettlementStep>, SettlementError> {
    let names = [
        ("validating_metadata", SETTLEMENT_PHASE_VALIDATING_METADATA),
        ("locking_rows", SETTLEMENT_PHASE_LOCKING_ROWS),
        ("applying_ownership", SETTLEMENT_PHASE_APPLYING_OWNERSHIP),
        ("writing_audit", SETTLEMENT_PHASE_WRITING_AUDIT),
        ("completed", SETTLEMENT_PHASE_COMPLETED),
    ];
    let mut steps = Vec::with_capacity(names.len());

    for (name, phase) in names {
        let row = sqlx::query_as::<_, SettlementStepRow>(
            r#"
            INSERT INTO settlement_step (
                settlement_step_id, settlement_id, step_name, step_state,
                started_at, completed_at
            )
            VALUES ($1, $2, $3, 'completed', now(), now())
            RETURNING settlement_step_id, settlement_id, step_name, step_state,
                      started_at, completed_at, failure_code, failure_message
            "#,
        )
        .bind(Uuid::new_v4())
        .bind(settlement_id)
        .bind(name)
        .fetch_one(tx_conn(tx))
        .await?;
        steps.push(settlement_step_proto(&row, phase));
    }

    Ok(steps)
}

pub(crate) async fn load_settlement_steps_tx(
    tx: &mut DbTx<'_>,
    settlement_id: Uuid,
) -> Result<Vec<SettlementStep>, SettlementError> {
    let rows = sqlx::query_as::<_, SettlementStepRow>(
        r#"
        SELECT settlement_step_id, settlement_id, step_name, step_state,
               started_at, completed_at, failure_code, failure_message
        FROM settlement_step
        WHERE settlement_id = $1
        ORDER BY started_at, settlement_step_id
        "#,
    )
    .bind(settlement_id)
    .fetch_all(tx_conn(tx))
    .await?;

    Ok(rows
        .iter()
        .map(|row| settlement_step_proto(row, settlement_phase_for_step(&row.step_name)))
        .collect())
}

pub(crate) async fn create_trade_claims(
    tx: &mut DbTx<'_>,
    operation_id: Uuid,
    trade_transaction_id: Uuid,
    settlement_id: Uuid,
    seller_capsuleer_id: i64,
    seller_wallet_id: Uuid,
    buyer_capsuleer_id: i64,
    destination_item_stack_id: Uuid,
    item_type_id: i64,
    quantity: i64,
    total_price_minor: i64,
    at: DateTime<Utc>,
) -> Result<
    (
        Vec<TradeClaimRow>,
        Vec<TradeClaimIskRow>,
        Vec<TradeClaimItemStackRow>,
    ),
    SettlementError,
> {
    let seller_claim_id = Uuid::new_v4();
    let buyer_claim_id = Uuid::new_v4();
    let seller_claim = insert_trade_claim(
        tx,
        seller_claim_id,
        operation_id,
        trade_transaction_id,
        settlement_id,
        seller_capsuleer_id,
        at,
    )
    .await?;
    let buyer_claim = insert_trade_claim(
        tx,
        buyer_claim_id,
        operation_id,
        trade_transaction_id,
        settlement_id,
        buyer_capsuleer_id,
        at,
    )
    .await?;

    let claim_isk = sqlx::query_as::<_, TradeClaimIskRow>(
        r#"
        INSERT INTO trade_claim_isk (
            trade_claim_isk_id, trade_claim_id, wallet_id, amount_isk
        )
        VALUES ($1, $2, $3, ($4::numeric / 100))
        RETURNING trade_claim_isk_id, trade_claim_id, wallet_id,
                  (amount_isk * 100)::bigint AS amount_minor
        "#,
    )
    .bind(Uuid::new_v4())
    .bind(seller_claim_id)
    .bind(seller_wallet_id)
    .bind(total_price_minor)
    .fetch_one(tx_conn(tx))
    .await?;

    let claim_item_stack = sqlx::query_as::<_, TradeClaimItemStackRow>(
        r#"
        INSERT INTO trade_claim_item_stack (
            trade_claim_item_stack_id, trade_claim_id, item_type_id, item_stack_id, quantity
        )
        VALUES ($1, $2, $3, $4, $5)
        RETURNING trade_claim_item_stack_id, trade_claim_id, item_type_id,
                  item_stack_id, quantity
        "#,
    )
    .bind(Uuid::new_v4())
    .bind(buyer_claim_id)
    .bind(item_type_id)
    .bind(destination_item_stack_id)
    .bind(quantity)
    .fetch_one(tx_conn(tx))
    .await?;

    Ok((
        vec![seller_claim, buyer_claim],
        vec![claim_isk],
        vec![claim_item_stack],
    ))
}

pub(crate) async fn insert_trade_claim(
    tx: &mut DbTx<'_>,
    trade_claim_id: Uuid,
    operation_id: Uuid,
    trade_transaction_id: Uuid,
    settlement_id: Uuid,
    claiming_capsuleer_id: i64,
    at: DateTime<Utc>,
) -> Result<TradeClaimRow, SettlementError> {
    Ok(sqlx::query_as::<_, TradeClaimRow>(
        r#"
        INSERT INTO trade_claim (
            trade_claim_id, operation_id, trade_transaction_id, settlement_id,
            claiming_capsuleer_id, claim_state, created_at
        )
        VALUES ($1, $2, $3, $4, $5, 'created', $6)
        RETURNING trade_claim_id, operation_id, trade_transaction_id, settlement_id,
                  claiming_capsuleer_id, claim_state, created_at, claimed_at
        "#,
    )
    .bind(trade_claim_id)
    .bind(operation_id)
    .bind(trade_transaction_id)
    .bind(settlement_id)
    .bind(claiming_capsuleer_id)
    .bind(at)
    .fetch_one(tx_conn(tx))
    .await?)
}

pub(crate) async fn load_trade_claims_tx(
    tx: &mut DbTx<'_>,
    trade_transaction_id: Uuid,
) -> Result<
    (
        Vec<TradeClaimRow>,
        Vec<TradeClaimIskRow>,
        Vec<TradeClaimItemStackRow>,
    ),
    SettlementError,
> {
    let claims = sqlx::query_as::<_, TradeClaimRow>(
        r#"
        SELECT trade_claim_id, operation_id, trade_transaction_id, settlement_id,
               claiming_capsuleer_id, claim_state, created_at, claimed_at
        FROM trade_claim
        WHERE trade_transaction_id = $1
        ORDER BY created_at, trade_claim_id
        "#,
    )
    .bind(trade_transaction_id)
    .fetch_all(tx_conn(tx))
    .await?;
    let claim_ids: Vec<Uuid> = claims.iter().map(|claim| claim.trade_claim_id).collect();

    let isks = if claim_ids.is_empty() {
        Vec::new()
    } else {
        sqlx::query_as::<_, TradeClaimIskRow>(
            r#"
            SELECT trade_claim_isk_id, trade_claim_id, wallet_id,
                   (amount_isk * 100)::bigint AS amount_minor
            FROM trade_claim_isk
            WHERE trade_claim_id = ANY($1)
            ORDER BY trade_claim_isk_id
            "#,
        )
        .bind(&claim_ids)
        .fetch_all(tx_conn(tx))
        .await?
    };

    let stacks = if claim_ids.is_empty() {
        Vec::new()
    } else {
        sqlx::query_as::<_, TradeClaimItemStackRow>(
            r#"
            SELECT trade_claim_item_stack_id, trade_claim_id, item_type_id,
                   item_stack_id, quantity
            FROM trade_claim_item_stack
            WHERE trade_claim_id = ANY($1)
            ORDER BY trade_claim_item_stack_id
            "#,
        )
        .bind(&claim_ids)
        .fetch_all(tx_conn(tx))
        .await?
    };

    Ok((claims, isks, stacks))
}

pub(crate) async fn insert_trade_state_change(
    tx: &mut DbTx<'_>,
    ctx: &CommandContext,
    trade_instance_id: Uuid,
    trade_transaction_id: Option<Uuid>,
    settlement_id: Option<Uuid>,
    from_trade_state: Option<&str>,
    to_trade_state: &str,
    kind: &str,
) -> Result<(), SettlementError> {
    sqlx::query(
        r#"
        INSERT INTO trade_state_change (
            trade_state_change_id, operation_id, trade_instance_id, trade_transaction_id,
            settlement_id, idempotency_key, request_id, from_trade_state, to_trade_state,
            trade_state_change_kind, changed_by_service, changed_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        "#,
    )
    .bind(Uuid::new_v4())
    .bind(ctx.operation_id)
    .bind(trade_instance_id)
    .bind(trade_transaction_id)
    .bind(settlement_id)
    .bind(&ctx.idempotency_key)
    .bind(ctx.request_id)
    .bind(from_trade_state)
    .bind(to_trade_state)
    .bind(kind)
    .bind(SERVICE_NAME)
    .bind(ctx.requested_at)
    .execute(tx_conn(tx))
    .await?;
    Ok(())
}

pub(crate) async fn insert_domain_event(
    tx: &mut DbTx<'_>,
    operation_id: Uuid,
    event_kind: &str,
    aggregate_kind: &str,
    aggregate_id: Uuid,
) -> Result<(), SettlementError> {
    sqlx::query(
        r#"
        INSERT INTO domain_event_outbox (
            domain_event_id, operation_id, event_kind, aggregate_kind, aggregate_id,
            event_version, payload_reference, publish_state, created_at
        )
        VALUES ($1, $2, $3, $4, $5, 1, $6, 'pending', now())
        "#,
    )
    .bind(Uuid::new_v4())
    .bind(operation_id)
    .bind(event_kind)
    .bind(aggregate_kind)
    .bind(aggregate_id.to_string())
    .bind(format!("{aggregate_kind}:{aggregate_id}"))
    .execute(tx_conn(tx))
    .await?;
    Ok(())
}
