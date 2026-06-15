use sqlx::PgPool;

use crate::error::SettlementError;

use super::{
    support::{ensure_nonnegative, ensure_not_blank, ensure_positive, tx_conn},
    types::{CreateNewTradeInstanceRowInput, ModifyTradeInstanceStateInput, TradeInstanceRow},
};

pub(crate) async fn create_new_trade_instance_row(
    pool: &PgPool,
    input: CreateNewTradeInstanceRowInput,
) -> Result<TradeInstanceRow, SettlementError> {
    ensure_positive(input.total_quantity, "total_quantity")?;
    ensure_nonnegative(input.unit_price_minor, "unit_price_minor")?;
    ensure_not_blank(&input.trade_state, "trade_state")?;

    let mut tx = pool.begin().await?;
    let row = sqlx::query_as::<_, TradeInstanceRow>(
        r#"
        INSERT INTO trade_instance (
            trade_instance_id, operation_id, trade_state, issuer_id, issuer_wallet_id,
            item_type_id, station_id, region_id, total_quantity, remaining_quantity,
            unit_price_isk, expires_at, created_at, updated_at
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $9,
            ($10::numeric / 100), $11, $12, $12
        )
        RETURNING trade_instance_id, operation_id, trade_state, issuer_id, issuer_wallet_id,
                  item_type_id, station_id, region_id, total_quantity, remaining_quantity,
                  (unit_price_isk * 100)::bigint AS unit_price_minor,
                  expires_at, created_at, updated_at
        "#,
    )
    .bind(input.trade_instance_id)
    .bind(input.operation_id)
    .bind(&input.trade_state)
    .bind(input.issuer_id)
    .bind(input.issuer_wallet_id)
    .bind(input.item_type_id)
    .bind(input.station_id)
    .bind(input.region_id)
    .bind(input.total_quantity)
    .bind(input.unit_price_minor)
    .bind(input.expires_at)
    .bind(input.created_at)
    .fetch_one(tx_conn(&mut tx))
    .await?;
    tx.commit().await?;
    Ok(row)
}

pub(crate) async fn modify_trade_instance_state(
    pool: &PgPool,
    input: ModifyTradeInstanceStateInput,
) -> Result<TradeInstanceRow, SettlementError> {
    ensure_not_blank(&input.new_trade_state, "new_trade_state")?;
    if let Some(state) = input.expected_trade_state.as_deref() {
        ensure_not_blank(state, "expected_trade_state")?;
    }
    if let Some(quantity) = input.remaining_quantity {
        ensure_nonnegative(quantity, "remaining_quantity")?;
    }

    let mut tx = pool.begin().await?;
    let row = sqlx::query_as::<_, TradeInstanceRow>(
        r#"
        UPDATE trade_instance
        SET trade_state = $2,
            remaining_quantity = COALESCE($3, remaining_quantity),
            updated_at = $4
        WHERE trade_instance_id = $1
          AND ($5::text IS NULL OR trade_state = $5)
        RETURNING trade_instance_id, operation_id, trade_state, issuer_id, issuer_wallet_id,
                  item_type_id, station_id, region_id, total_quantity, remaining_quantity,
                  (unit_price_isk * 100)::bigint AS unit_price_minor,
                  expires_at, created_at, updated_at
        "#,
    )
    .bind(input.trade_instance_id)
    .bind(&input.new_trade_state)
    .bind(input.remaining_quantity)
    .bind(input.updated_at)
    .bind(input.expected_trade_state.as_deref())
    .fetch_one(tx_conn(&mut tx))
    .await?;
    tx.commit().await?;
    Ok(row)
}
