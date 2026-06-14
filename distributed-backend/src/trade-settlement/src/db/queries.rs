use chrono::{DateTime, Utc};
use uuid::Uuid;

use crate::error::SettlementError;
use crate::generated::eve_trade::settlement::v1::SettlementStep;

use super::{
    responses::{settlement_phase_for_step, settlement_step_proto},
    types::*,
    validation::{item_stack_checksum, wallet_checksum},
};

pub(crate) async fn lock_trade_instance(
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
        FOR UPDATE
        "#,
    )
    .bind(trade_instance_id)
    .fetch_one(&mut tx.executor())
    .await?)
}

pub(crate) async fn load_trade_instance(
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
    .fetch_one(&mut tx.executor())
    .await?)
}

pub(crate) async fn update_trade_state_and_remaining(
    tx: &mut DbTx<'_>,
    trade_instance_id: Uuid,
    trade_state: &str,
    remaining_quantity: i64,
    updated_at: DateTime<Utc>,
) -> Result<TradeInstanceRow, SettlementError> {
    Ok(sqlx::query_as::<_, TradeInstanceRow>(
        r#"
        UPDATE trade_instance
        SET trade_state = $2, remaining_quantity = $3, updated_at = $4
        WHERE trade_instance_id = $1
        RETURNING trade_instance_id, operation_id, trade_state, issuer_id, issuer_wallet_id,
                  item_type_id, station_id, region_id, total_quantity, remaining_quantity,
                  (unit_price_isk * 100)::bigint AS unit_price_minor,
                  expires_at, created_at, updated_at
        "#,
    )
    .bind(trade_instance_id)
    .bind(trade_state)
    .bind(remaining_quantity)
    .bind(updated_at)
    .fetch_one(&mut tx.executor())
    .await?)
}

pub(crate) async fn lock_item_stack(
    tx: &mut DbTx<'_>,
    item_stack_id: Uuid,
) -> Result<ItemStackRow, SettlementError> {
    Ok(sqlx::query_as::<_, ItemStackRow>(
        r#"
        SELECT s.item_stack_id, s.owner_id, s.item_type_id, s.station_id, st.region_id,
               s.quantity, s.stack_version, s.stack_checksum
        FROM item_stack s
        JOIN station st ON st.station_id = s.station_id
        WHERE s.item_stack_id = $1
        FOR UPDATE
        "#,
    )
    .bind(item_stack_id)
    .fetch_one(&mut tx.executor())
    .await?)
}

pub(crate) async fn lock_or_create_item_stack(
    tx: &mut DbTx<'_>,
    item_stack_id: Uuid,
    owner_id: i64,
    item_type_id: i64,
    station_id: i64,
    region_id: i64,
    created_at: DateTime<Utc>,
) -> Result<ItemStackRow, SettlementError> {
    if let Some(row) = sqlx::query_as::<_, ItemStackRow>(
        r#"
        SELECT s.item_stack_id, s.owner_id, s.item_type_id, s.station_id, st.region_id,
               s.quantity, s.stack_version, s.stack_checksum
        FROM item_stack s
        JOIN station st ON st.station_id = s.station_id
        WHERE s.item_stack_id = $1
        FOR UPDATE
        "#,
    )
    .bind(item_stack_id)
    .fetch_optional(&mut tx.executor())
    .await?
    {
        if row.region_id != region_id {
            return Err(SettlementError::InvalidRequest(
                "destination item stack station does not belong to requested region".to_string(),
            ));
        }
        return Ok(row);
    }

    let checksum = item_stack_checksum(item_stack_id, owner_id, item_type_id, station_id, 0, 0);
    sqlx::query(
        r#"
        INSERT INTO item_stack (
            item_stack_id, owner_id, item_type_id, station_id, quantity, stack_state,
            stack_version, stack_checksum, checksum_algorithm, created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, 0, 'active', 0, $5, $6, $7, $7)
        ON CONFLICT (item_stack_id) DO NOTHING
        "#,
    )
    .bind(item_stack_id)
    .bind(owner_id)
    .bind(item_type_id)
    .bind(station_id)
    .bind(checksum)
    .bind(CHECKSUM_ALGORITHM)
    .bind(created_at)
    .execute(&mut tx.executor())
    .await?;

    let row = lock_item_stack(tx, item_stack_id).await?;
    if row.region_id != region_id {
        return Err(SettlementError::InvalidRequest(
            "destination item stack station does not belong to requested region".to_string(),
        ));
    }
    Ok(row)
}

pub(crate) async fn lock_wallet(
    tx: &mut DbTx<'_>,
    wallet_id: Uuid,
) -> Result<WalletRow, SettlementError> {
    Ok(sqlx::query_as::<_, WalletRow>(
        r#"
        SELECT wallet_id, capsuleer_id, (isk_amount * 100)::bigint AS isk_minor,
               wallet_version, wallet_checksum
        FROM wallet
        WHERE wallet_id = $1
        FOR UPDATE
        "#,
    )
    .bind(wallet_id)
    .fetch_one(&mut tx.executor())
    .await?)
}

pub(crate) async fn lock_item_stack_escrow(
    tx: &mut DbTx<'_>,
    item_stack_escrow_id: Uuid,
) -> Result<ItemStackEscrowRow, SettlementError> {
    Ok(sqlx::query_as::<_, ItemStackEscrowRow>(
        r#"
        SELECT item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
               created_at, updated_at, released_at, escrow_state, release_reason,
               source_item_stack_id
        FROM item_stack_escrow
        WHERE item_stack_escrow_id = $1
        FOR UPDATE
        "#,
    )
    .bind(item_stack_escrow_id)
    .fetch_one(&mut tx.executor())
    .await?)
}

pub(crate) async fn load_item_stack_escrows(
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
    .fetch_all(&mut tx.executor())
    .await?)
}

pub(crate) async fn load_wallet_escrows(
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
    .fetch_all(&mut tx.executor())
    .await?)
}

pub(crate) async fn create_wallet_operation(
    tx: &mut DbTx<'_>,
    operation_id: Uuid,
    operation_kind: &str,
) -> Result<Uuid, SettlementError> {
    let wallet_operation_id = Uuid::new_v4();
    sqlx::query(
        r#"
        INSERT INTO wallet_operation (
            wallet_operation_id, operation_id, operation_kind, wallet_operation_state,
            created_at, completed_at
        )
        VALUES ($1, $2, $3, 'completed', now(), now())
        "#,
    )
    .bind(wallet_operation_id)
    .bind(operation_id)
    .bind(operation_kind)
    .execute(&mut tx.executor())
    .await?;
    Ok(wallet_operation_id)
}

pub(crate) async fn create_item_stack_operation(
    tx: &mut DbTx<'_>,
    operation_id: Uuid,
    operation_kind: &str,
) -> Result<Uuid, SettlementError> {
    let item_stack_operation_id = Uuid::new_v4();
    sqlx::query(
        r#"
        INSERT INTO item_stack_operation (
            item_stack_operation_id, operation_id, operation_kind, item_stack_operation_state,
            created_at, completed_at
        )
        VALUES ($1, $2, $3, 'completed', now(), now())
        "#,
    )
    .bind(item_stack_operation_id)
    .bind(operation_id)
    .bind(operation_kind)
    .execute(&mut tx.executor())
    .await?;
    Ok(item_stack_operation_id)
}

pub(crate) async fn mutate_wallet(
    tx: &mut DbTx<'_>,
    wallet_operation_id: Uuid,
    wallet_id: Uuid,
    delta_minor: i64,
    entry_kind: &str,
) -> Result<WalletRow, SettlementError> {
    let before = lock_wallet(tx, wallet_id).await?;
    let after_minor = before
        .isk_minor
        .checked_add(delta_minor)
        .ok_or_else(|| SettlementError::DatabaseConflict("wallet amount overflow".to_string()))?;
    if after_minor < 0 {
        return Err(SettlementError::InsufficientIsk {
            wallet_id: wallet_id.to_string(),
        });
    }
    let after_version = before.wallet_version + 1;
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
            wallet_checksum = $4, checksum_algorithm = $5, updated_at = now()
        WHERE wallet_id = $1
        RETURNING wallet_id, capsuleer_id, (isk_amount * 100)::bigint AS isk_minor,
                  wallet_version, wallet_checksum
        "#,
    )
    .bind(wallet_id)
    .bind(after_minor)
    .bind(after_version)
    .bind(&after_checksum)
    .bind(CHECKSUM_ALGORITHM)
    .fetch_one(&mut tx.executor())
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
            $9, $10, $11, $12, now()
        )
        "#,
    )
    .bind(Uuid::new_v4())
    .bind(wallet_operation_id)
    .bind(wallet_id)
    .bind(before.capsuleer_id)
    .bind(entry_kind)
    .bind(delta_minor)
    .bind(before.isk_minor)
    .bind(after_minor)
    .bind(before.wallet_version)
    .bind(after_version)
    .bind(&before.wallet_checksum)
    .bind(&after_checksum)
    .execute(&mut tx.executor())
    .await?;

    Ok(row)
}

pub(crate) async fn mutate_item_stack(
    tx: &mut DbTx<'_>,
    item_stack_operation_id: Uuid,
    item_stack_id: Uuid,
    delta_quantity: i64,
    entry_kind: &str,
) -> Result<ItemStackRow, SettlementError> {
    let before = lock_item_stack(tx, item_stack_id).await?;
    let after_quantity = before
        .quantity
        .checked_add(delta_quantity)
        .ok_or_else(|| SettlementError::DatabaseConflict("item quantity overflow".to_string()))?;
    if after_quantity < 0 {
        return Err(SettlementError::InsufficientItems {
            item_stack_id: item_stack_id.to_string(),
        });
    }
    let after_version = before.stack_version + 1;
    let after_checksum = item_stack_checksum(
        before.item_stack_id,
        before.owner_id,
        before.item_type_id,
        before.station_id,
        after_quantity,
        after_version,
    );
    let stack_state = if after_quantity == 0 {
        "depleted"
    } else {
        "active"
    };

    let row = sqlx::query_as::<_, ItemStackRow>(
        r#"
        UPDATE item_stack
        SET quantity = $2, stack_state = $3, stack_version = $4,
            stack_checksum = $5, checksum_algorithm = $6, updated_at = now()
        WHERE item_stack_id = $1
        RETURNING item_stack_id, owner_id, item_type_id, station_id,
                  $7::bigint AS region_id, quantity, stack_version, stack_checksum
        "#,
    )
    .bind(item_stack_id)
    .bind(after_quantity)
    .bind(stack_state)
    .bind(after_version)
    .bind(&after_checksum)
    .bind(CHECKSUM_ALGORITHM)
    .bind(before.region_id)
    .fetch_one(&mut tx.executor())
    .await?;

    sqlx::query(
        r#"
        INSERT INTO item_stack_ledger (
            item_stack_ledger_id, item_stack_operation_id, item_stack_id, item_type_id,
            owner_id, station_id, entry_kind, quantity_delta, quantity_before,
            quantity_after, stack_version_before, stack_version_after,
            stack_checksum_before, stack_checksum_after, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, now())
        "#,
    )
    .bind(Uuid::new_v4())
    .bind(item_stack_operation_id)
    .bind(item_stack_id)
    .bind(before.item_type_id)
    .bind(before.owner_id)
    .bind(before.station_id)
    .bind(entry_kind)
    .bind(delta_quantity)
    .bind(before.quantity)
    .bind(after_quantity)
    .bind(before.stack_version)
    .bind(after_version)
    .bind(&before.stack_checksum)
    .bind(&after_checksum)
    .execute(&mut tx.executor())
    .await?;

    Ok(row)
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
    .fetch_one(&mut tx.executor())
    .await?)
}

pub(crate) async fn load_trade_transaction(
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
    .fetch_one(&mut tx.executor())
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
    .execute(&mut tx.executor())
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
        let id = Uuid::new_v4();
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
        .bind(id)
        .bind(settlement_id)
        .bind(name)
        .fetch_one(&mut tx.executor())
        .await?;
        steps.push(settlement_step_proto(&row, phase));
    }

    Ok(steps)
}

pub(crate) async fn load_settlement_steps(
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
    .fetch_all(&mut tx.executor())
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
    .fetch_one(&mut tx.executor())
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
    .fetch_one(&mut tx.executor())
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
    .fetch_one(&mut tx.executor())
    .await?)
}

pub(crate) async fn load_trade_claims(
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
    .fetch_all(&mut tx.executor())
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
        .fetch_all(&mut tx.executor())
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
        .fetch_all(&mut tx.executor())
        .await?
    };

    Ok((claims, isks, stacks))
}

pub(crate) async fn release_remaining_item_escrows(
    tx: &mut DbTx<'_>,
    operation_id: Uuid,
    trade_instance_id: Uuid,
    released_state: &str,
    reason: &str,
    at: DateTime<Utc>,
) -> Result<Vec<ItemStackEscrowRow>, SettlementError> {
    let escrows = sqlx::query_as::<_, ItemStackEscrowRow>(
        r#"
        SELECT item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
               created_at, updated_at, released_at, escrow_state, release_reason,
               source_item_stack_id
        FROM item_stack_escrow
        WHERE trade_instance_id = $1
          AND quantity > 0
          AND escrow_state IN ('held', 'partially_used')
        ORDER BY created_at, item_stack_escrow_id
        FOR UPDATE
        "#,
    )
    .bind(trade_instance_id)
    .fetch_all(&mut tx.executor())
    .await?;

    if escrows.is_empty() {
        return Ok(Vec::new());
    }

    let item_stack_operation_id =
        create_item_stack_operation(tx, operation_id, "release_trade_escrow").await?;
    let mut released = Vec::with_capacity(escrows.len());

    for escrow in escrows {
        mutate_item_stack(
            tx,
            item_stack_operation_id,
            escrow.source_item_stack_id,
            escrow.quantity,
            "trade_escrow_release",
        )
        .await?;
        let row = sqlx::query_as::<_, ItemStackEscrowRow>(
            r#"
            UPDATE item_stack_escrow
            SET quantity = 0, escrow_state = $2, release_reason = $3,
                released_at = $4, updated_at = $4
            WHERE item_stack_escrow_id = $1
            RETURNING item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
                      created_at, updated_at, released_at, escrow_state, release_reason,
                      source_item_stack_id
            "#,
        )
        .bind(escrow.item_stack_escrow_id)
        .bind(released_state)
        .bind(reason)
        .bind(at)
        .fetch_one(&mut tx.executor())
        .await?;
        released.push(row);
    }

    Ok(released)
}

pub(crate) async fn expire_locked_trade_instance(
    tx: &mut DbTx<'_>,
    ctx: &CommandContext,
    trade: &TradeInstanceRow,
    trade_transaction_id: Option<Uuid>,
    settlement_id: Option<Uuid>,
    reason: &str,
) -> Result<(TradeInstanceRow, Vec<ItemStackEscrowRow>), SettlementError> {
    let released = release_remaining_item_escrows(
        tx,
        ctx.operation_id,
        trade.trade_instance_id,
        "expired",
        reason,
        ctx.requested_at,
    )
    .await?;
    let updated = update_trade_state_and_remaining(
        tx,
        trade.trade_instance_id,
        "expired",
        trade.remaining_quantity,
        ctx.requested_at,
    )
    .await?;
    insert_trade_state_change(
        tx,
        ctx,
        trade.trade_instance_id,
        trade_transaction_id,
        settlement_id,
        Some(&trade.trade_state),
        "expired",
        "expire_trade_instance",
    )
    .await?;

    Ok((updated, released))
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
    .execute(&mut tx.executor())
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
    .execute(&mut tx.executor())
    .await?;
    Ok(())
}
