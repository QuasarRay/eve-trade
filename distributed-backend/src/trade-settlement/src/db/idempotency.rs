use sqlx::PgPool;

use crate::error::SettlementError;
use crate::generated::eve_trade::{common::v1::*, operation::v1::*, settlement::v1::*};

use super::{
    queries::*,
    responses::*,
    support::tx_conn,
    types::*,
};

pub(crate) async fn begin_operation(
    pool: &PgPool,
    ctx: &CommandContext,
) -> Result<BeginCommand, SettlementError> {
    let mut tx = pool.begin().await?;

    sqlx::query(
        r#"
        INSERT INTO idempotency_record (
            idempotency_key, request_fingerprint, operation_name, operation_state,
            created_by_service, created_at
        )
        VALUES ($1, $2, $3, 'started', $4, $5)
        ON CONFLICT (idempotency_key) DO NOTHING
        "#,
    )
    .bind(&ctx.idempotency_key)
    .bind(&ctx.request_fingerprint)
    .bind(ctx.operation_name)
    .bind(&ctx.created_by_service)
    .bind(ctx.requested_at)
    .execute(tx_conn(&mut tx))
    .await?;

    let idem = sqlx::query_as::<_, IdempotencyRecordRow>(
        "SELECT request_fingerprint, operation_name FROM idempotency_record WHERE idempotency_key = $1",
    )
    .bind(&ctx.idempotency_key)
    .fetch_one(tx_conn(&mut tx))
    .await?;

    if idem.request_fingerprint != ctx.request_fingerprint
        || idem.operation_name != ctx.operation_name
    {
        return Err(SettlementError::RequestIdConflict);
    }

    sqlx::query(
        r#"
        INSERT INTO request_attempt (
            request_id, idempotency_key, received_by_service, attempt_state, received_at
        )
        VALUES ($1, $2, $3, 'started', $4)
        ON CONFLICT (request_id) DO NOTHING
        "#,
    )
    .bind(ctx.request_id)
    .bind(&ctx.idempotency_key)
    .bind(SERVICE_NAME)
    .bind(ctx.requested_at)
    .execute(tx_conn(&mut tx))
    .await?;

    let request_key: String =
        sqlx::query_scalar("SELECT idempotency_key FROM request_attempt WHERE request_id = $1")
            .bind(ctx.request_id)
            .fetch_one(tx_conn(&mut tx))
            .await?;
    if request_key != ctx.idempotency_key {
        return Err(SettlementError::RequestIdConflict);
    }

    if let Some(row) = load_idempotency_result(&mut tx, &ctx.idempotency_key).await? {
        sqlx::query(
            r#"
            UPDATE request_attempt
            SET attempt_state = 'idempotent_replay', completed_at = $2
            WHERE request_id = $1
            "#,
        )
        .bind(ctx.request_id)
        .bind(ctx.requested_at)
        .execute(tx_conn(&mut tx))
        .await?;

        let result =
            build_result_from_idempotency(&mut tx, ctx, &row, ATTEMPT_IDEMPOTENT_REPLAY).await?;
        tx.commit().await?;
        return Ok(BeginCommand::Replay(result));
    }

    sqlx::query(
        r#"
        INSERT INTO operation (
            operation_id, operation_kind, source_system, external_operation_id,
            request_id, idempotency_key, caused_by_capsuleer_id, operation_state,
            created_by_service, started_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'started', $8, $9)
        ON CONFLICT (operation_id) DO NOTHING
        "#,
    )
    .bind(ctx.operation_id)
    .bind(ctx.operation_name)
    .bind(&ctx.source_system)
    .bind(&ctx.external_operation_id)
    .bind(ctx.request_id)
    .bind(&ctx.idempotency_key)
    .bind(ctx.caused_by_capsuleer_id)
    .bind(&ctx.created_by_service)
    .bind(ctx.requested_at)
    .execute(tx_conn(&mut tx))
    .await?;

    let operation_key: Option<String> =
        sqlx::query_scalar("SELECT idempotency_key FROM operation WHERE operation_id = $1")
            .bind(ctx.operation_id)
            .fetch_one(tx_conn(&mut tx))
            .await?;
    if operation_key.as_deref() != Some(ctx.idempotency_key.as_str()) {
        return Err(SettlementError::RequestIdConflict);
    }

    tx.commit().await?;
    Ok(BeginCommand::Started)
}

pub(crate) async fn finish_operation(
    pool: &PgPool,
    ctx: &CommandContext,
    ids: FinishIds,
) -> Result<(), SettlementError> {
    let mut tx = pool.begin().await?;

    sqlx::query(
        r#"
        UPDATE operation
        SET operation_state = 'completed', completed_at = $2
        WHERE operation_id = $1
        "#,
    )
    .bind(ctx.operation_id)
    .bind(ctx.requested_at)
    .execute(tx_conn(&mut tx))
    .await?;

    sqlx::query(
        r#"
        UPDATE request_attempt
        SET attempt_state = 'completed', completed_at = $2
        WHERE request_id = $1
        "#,
    )
    .bind(ctx.request_id)
    .bind(ctx.requested_at)
    .execute(tx_conn(&mut tx))
    .await?;

    sqlx::query(
        r#"
        UPDATE idempotency_record
        SET operation_state = 'completed', completed_at = $2
        WHERE idempotency_key = $1
        "#,
    )
    .bind(&ctx.idempotency_key)
    .bind(ctx.requested_at)
    .execute(tx_conn(&mut tx))
    .await?;

    sqlx::query(
        r#"
        INSERT INTO idempotency_result (
            idempotency_key, operation_id, result_kind, trade_instance_id,
            trade_transaction_id, settlement_id, wallet_operation_id,
            item_stack_operation_id, result_state, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (idempotency_key) DO NOTHING
        "#,
    )
    .bind(&ctx.idempotency_key)
    .bind(ctx.operation_id)
    .bind(ids.result_kind)
    .bind(ids.trade_instance_id)
    .bind(ids.trade_transaction_id)
    .bind(ids.settlement_id)
    .bind(ids.wallet_operation_id)
    .bind(ids.item_stack_operation_id)
    .bind(ids.result_state)
    .bind(ctx.requested_at)
    .execute(tx_conn(&mut tx))
    .await?;

    tx.commit().await?;
    Ok(())
}

pub(crate) async fn load_idempotency_result(
    tx: &mut DbTx<'_>,
    idempotency_key: &str,
) -> Result<Option<IdempotencyResultRow>, SettlementError> {
    Ok(sqlx::query_as::<_, IdempotencyResultRow>(
        r#"
        SELECT result_kind, trade_instance_id, trade_transaction_id, settlement_id, result_state
        FROM idempotency_result
        WHERE idempotency_key = $1
        "#,
    )
    .bind(idempotency_key)
    .fetch_optional(tx_conn(tx))
    .await?)
}

pub(crate) async fn build_result_from_idempotency(
    tx: &mut DbTx<'_>,
    ctx: &CommandContext,
    row: &IdempotencyResultRow,
    attempt_status: i32,
) -> Result<TradeSettlementResult, SettlementError> {
    let trade = match row.trade_instance_id {
        Some(id) => Some(load_trade_instance_tx(tx, id).await?),
        None => None,
    };
    let transaction = match row.trade_transaction_id {
        Some(id) => Some(load_trade_transaction_tx(tx, id).await?),
        None => None,
    };
    let item_escrows = match row.trade_instance_id {
        Some(id) => load_item_stack_escrows_tx(tx, id).await?,
        None => Vec::new(),
    };
    let wallet_escrows = match row.trade_instance_id {
        Some(id) => load_wallet_escrows_tx(tx, id).await?,
        None => Vec::new(),
    };

    let result = match row.result_kind.as_str() {
        "issue_trade_instance" => {
            trade_settlement_result::Result::IssueTradeInstance(IssueTradeInstanceOutcome {
                applied: Some(IssueTradeInstanceApplied {
                    trade_instance: trade.as_ref().map(trade_instance_proto),
                    item_stack_escrow: item_escrows.first().map(item_stack_escrow_proto),
                    wallet_escrow: wallet_escrows.first().map(wallet_escrow_proto),
                }),
            })
        }
        "settle_trade_instance" => {
            let claims = match row.trade_transaction_id {
                Some(id) => load_trade_claims_tx(tx, id).await?,
                None => (Vec::new(), Vec::new(), Vec::new()),
            };
            trade_settlement_result::Result::SettleTradeInstance(SettleTradeInstanceOutcome {
                applied: Some(SettleTradeInstanceApplied {
                    trade_instance: trade.as_ref().map(trade_instance_proto),
                    trade_transaction: transaction.as_ref().map(trade_transaction_proto),
                    trade_claims: claims.0.iter().map(trade_claim_proto).collect(),
                    trade_claim_isks: claims.1.iter().map(trade_claim_isk_proto).collect(),
                    trade_claim_item_stacks: claims
                        .2
                        .iter()
                        .map(trade_claim_item_stack_proto)
                        .collect(),
                }),
                resulting_trade_state: trade_state_i32(&row.result_state),
            })
        }
        "cancel_trade_instance" => {
            trade_settlement_result::Result::CancelTradeInstance(CancelTradeInstanceOutcome {
                applied: Some(CancelTradeInstanceApplied {
                    trade_instance: trade.as_ref().map(trade_instance_proto),
                    released_item_stack_escrows: item_escrows
                        .iter()
                        .filter(|escrow| escrow.escrow_state != "held")
                        .map(item_stack_escrow_proto)
                        .collect(),
                    released_wallet_escrows: wallet_escrows
                        .iter()
                        .map(wallet_escrow_proto)
                        .collect(),
                }),
            })
        }
        "expire_trade_instance" => {
            trade_settlement_result::Result::ExpireTradeInstance(ExpireTradeInstanceOutcome {
                applied: Some(ExpireTradeInstanceApplied {
                    trade_instance: trade.as_ref().map(trade_instance_proto),
                    released_item_stack_escrows: item_escrows
                        .iter()
                        .filter(|escrow| escrow.escrow_state != "held")
                        .map(item_stack_escrow_proto)
                        .collect(),
                    released_wallet_escrows: wallet_escrows
                        .iter()
                        .map(wallet_escrow_proto)
                        .collect(),
                }),
            })
        }
        _ => {
            return Err(SettlementError::DatabaseConflict(format!(
                "unknown idempotency result kind {}",
                row.result_kind
            )));
        }
    };

    let steps = match row.settlement_id {
        Some(id) => load_settlement_steps_tx(tx, id).await?,
        None => Vec::new(),
    };

    Ok(TradeSettlementResult {
        metadata: Some(ctx.metadata.clone()),
        operation_kind: ctx.operation_kind,
        attempt_status,
        trade_instance_id: row.trade_instance_id.map(|id| TradeInstanceId {
            value: id.to_string(),
        }),
        trade_transaction_id: row.trade_transaction_id.map(|id| TradeTransactionId {
            value: id.to_string(),
        }),
        settlement_id: row.settlement_id.map(|id| SettlementId {
            value: id.to_string(),
        }),
        resulting_trade_state: trade_state_i32(&row.result_state),
        settlement_steps: steps,
        result: Some(result),
    })
}
