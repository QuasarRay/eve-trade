use sqlx::PgPool;

use crate::error::SettlementError;
use crate::generated::eve_trade::{operation::v1::*, settlement::v1::*};

use super::{
    idempotency::{begin_operation, finish_operation},
    queries::*,
    responses::*,
    types::*,
    validation::*,
};

pub(crate) async fn issue_trade_instance(
    pool: &PgPool,
    ctx: CommandContext,
    command: IssueTradeInstanceCommand,
) -> Result<TradeSettlementResult, SettlementError> {
    validate_nested_metadata(
        &ctx.metadata,
        command.metadata.as_ref(),
        "issue_trade_instance",
    )?;

    let row_ids = command
        .row_ids
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("issue row_ids is required".to_string()))?;
    let terms = command
        .terms
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("issue terms is required".to_string()))?;

    let trade_instance_id = parse_uuid_message(
        row_ids.trade_instance_id.as_ref().map(|x| x.value.as_str()),
        "issue.row_ids.trade_instance_id",
    )?;
    let issuer_id = required_positive_i64(
        row_ids.issuer_id.as_ref().map(|x| x.value),
        "issue.row_ids.issuer_id",
    )?;
    let issuer_wallet_id = parse_uuid_message(
        row_ids.issuer_wallet_id.as_ref().map(|x| x.value.as_str()),
        "issue.row_ids.issuer_wallet_id",
    )?;
    let item_type_id = required_positive_i64(
        row_ids.item_type_id.as_ref().map(|x| x.value),
        "issue.row_ids.item_type_id",
    )?;
    let station_id = required_positive_i64(
        row_ids.station_id.as_ref().map(|x| x.value),
        "issue.row_ids.station_id",
    )?;
    let region_id = required_positive_i64(
        row_ids.region_id.as_ref().map(|x| x.value),
        "issue.row_ids.region_id",
    )?;
    let source_item_stack_id = parse_uuid_message(
        row_ids
            .source_item_stack_id
            .as_ref()
            .map(|x| x.value.as_str()),
        "issue.row_ids.source_item_stack_id",
    )?;
    let item_stack_escrow_id = parse_uuid_message(
        row_ids
            .item_stack_escrow_id
            .as_ref()
            .map(|x| x.value.as_str()),
        "issue.row_ids.item_stack_escrow_id",
    )?;

    let total_quantity =
        required_quantity(terms.total_quantity.as_ref(), "issue.terms.total_quantity")?;
    let unit_price_minor =
        required_money(terms.unit_price_isk.as_ref(), "issue.terms.unit_price_isk")?;
    let expires_at = millis_to_datetime(terms.expires_at_unix_millis);

    let mut tx = pool.begin().await?;
    if let BeginCommand::Replay(result) = begin_operation(&mut tx, &ctx).await? {
        tx.commit().await?;
        return Ok(result);
    }

    let source_stack = lock_item_stack(&mut tx, source_item_stack_id).await?;
    if source_stack.owner_id != issuer_id
        || source_stack.item_type_id != item_type_id
        || source_stack.station_id != station_id
        || source_stack.region_id != region_id
    {
        return Err(SettlementError::TradeMismatch {
            trade_instance_id: trade_instance_id.to_string(),
        });
    }
    if source_stack.quantity < total_quantity {
        return Err(SettlementError::InsufficientItems {
            item_stack_id: source_item_stack_id.to_string(),
        });
    }

    let item_stack_operation_id =
        create_item_stack_operation(&mut tx, ctx.operation_id, ctx.operation_name).await?;
    mutate_item_stack(
        &mut tx,
        item_stack_operation_id,
        source_item_stack_id,
        -total_quantity,
        "trade_escrow_hold",
    )
    .await?;

    let trade = sqlx::query_as::<_, TradeInstanceRow>(
        r#"
        INSERT INTO trade_instance (
            trade_instance_id, operation_id, trade_state, issuer_id, issuer_wallet_id,
            item_type_id, station_id, region_id, total_quantity, remaining_quantity,
            unit_price_isk, expires_at, created_at, updated_at
        )
        VALUES (
            $1, $2, 'outstanding', $3, $4, $5, $6, $7, $8, $8,
            ($9::numeric / 100), $10, $11, $11
        )
        RETURNING trade_instance_id, operation_id, trade_state, issuer_id, issuer_wallet_id,
                  item_type_id, station_id, region_id, total_quantity, remaining_quantity,
                  (unit_price_isk * 100)::bigint AS unit_price_minor,
                  expires_at, created_at, updated_at
        "#,
    )
    .bind(trade_instance_id)
    .bind(ctx.operation_id)
    .bind(issuer_id)
    .bind(issuer_wallet_id)
    .bind(item_type_id)
    .bind(station_id)
    .bind(region_id)
    .bind(total_quantity)
    .bind(unit_price_minor)
    .bind(expires_at)
    .bind(ctx.requested_at)
    .fetch_one(tx.as_mut())
    .await?;

    let item_escrow = sqlx::query_as::<_, ItemStackEscrowRow>(
        r#"
        INSERT INTO item_stack_escrow (
            item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
            created_at, updated_at, escrow_state, source_item_stack_id
        )
        VALUES ($1, $2, $3, $4, $5, $5, 'held', $6)
        RETURNING item_stack_escrow_id, issuer_id, trade_instance_id, quantity,
                  created_at, updated_at, released_at, escrow_state, release_reason,
                  source_item_stack_id
        "#,
    )
    .bind(item_stack_escrow_id)
    .bind(issuer_id)
    .bind(trade_instance_id)
    .bind(total_quantity)
    .bind(ctx.requested_at)
    .bind(source_item_stack_id)
    .fetch_one(tx.as_mut())
    .await?;

    insert_trade_state_change(
        &mut tx,
        &ctx,
        trade_instance_id,
        None,
        None,
        None,
        "outstanding",
        "issue_trade_instance",
    )
    .await?;
    insert_domain_event(
        &mut tx,
        ctx.operation_id,
        "trade_instance_issued",
        "trade_instance",
        trade_instance_id,
    )
    .await?;
    finish_operation(
        &mut tx,
        &ctx,
        FinishIds {
            trade_instance_id: Some(trade_instance_id),
            trade_transaction_id: None,
            settlement_id: None,
            wallet_operation_id: None,
            item_stack_operation_id: Some(item_stack_operation_id),
            result_kind: "issue_trade_instance",
            result_state: "outstanding",
        },
    )
    .await?;

    tx.commit().await?;

    Ok(TradeSettlementResult {
        metadata: Some(ctx.metadata),
        operation_kind: OP_ISSUE,
        attempt_status: ATTEMPT_COMMITTED,
        trade_instance_id: some_trade_instance_id(trade_instance_id),
        trade_transaction_id: None,
        settlement_id: None,
        resulting_trade_state: TRADE_STATE_OUTSTANDING,
        settlement_steps: Vec::new(),
        result: Some(trade_settlement_result::Result::IssueTradeInstance(
            IssueTradeInstanceOutcome {
                applied: Some(IssueTradeInstanceApplied {
                    trade_instance: Some(trade_instance_proto(&trade)),
                    item_stack_escrow: Some(item_stack_escrow_proto(&item_escrow)),
                    wallet_escrow: None,
                }),
            },
        )),
    })
}

pub(crate) async fn settle_trade_instance(
    pool: &PgPool,
    ctx: CommandContext,
    command: SettleTradeInstanceCommand,
) -> Result<TradeSettlementResult, SettlementError> {
    validate_nested_metadata(
        &ctx.metadata,
        command.metadata.as_ref(),
        "settle_trade_instance",
    )?;

    let row_ids = command
        .row_ids
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("settle row_ids is required".to_string()))?;
    let terms = command
        .terms
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("settle terms is required".to_string()))?;

    let trade_instance_id = parse_uuid_message(
        row_ids.trade_instance_id.as_ref().map(|x| x.value.as_str()),
        "settle.row_ids.trade_instance_id",
    )?;
    let source_escrow_id = parse_uuid_message(
        row_ids
            .source_item_stack_escrow_id
            .as_ref()
            .map(|x| x.value.as_str()),
        "settle.row_ids.source_item_stack_escrow_id",
    )?;
    let trade_transaction_id = parse_uuid_message(
        row_ids
            .trade_transaction_id
            .as_ref()
            .map(|x| x.value.as_str()),
        "settle.row_ids.trade_transaction_id",
    )?;
    let settlement_id = parse_uuid_message(
        row_ids.settlement_id.as_ref().map(|x| x.value.as_str()),
        "settle.row_ids.settlement_id",
    )?;
    let seller_capsuleer_id = required_positive_i64(
        row_ids.seller_capsuleer_id.as_ref().map(|x| x.value),
        "settle.row_ids.seller_capsuleer_id",
    )?;
    let seller_wallet_id = parse_uuid_message(
        row_ids.seller_wallet_id.as_ref().map(|x| x.value.as_str()),
        "settle.row_ids.seller_wallet_id",
    )?;
    let buyer_capsuleer_id = required_positive_i64(
        row_ids.buyer_capsuleer_id.as_ref().map(|x| x.value),
        "settle.row_ids.buyer_capsuleer_id",
    )?;
    let buyer_wallet_id = parse_uuid_message(
        row_ids.buyer_wallet_id.as_ref().map(|x| x.value.as_str()),
        "settle.row_ids.buyer_wallet_id",
    )?;
    let destination_item_stack_id = parse_uuid_message(
        row_ids
            .destination_item_stack_id
            .as_ref()
            .map(|x| x.value.as_str()),
        "settle.row_ids.destination_item_stack_id",
    )?;

    let quantity = required_quantity(terms.quantity.as_ref(), "settle.terms.quantity")?;
    let unit_price_minor =
        required_money(terms.unit_price_isk.as_ref(), "settle.terms.unit_price_isk")?;
    let total_price_minor = required_money(
        terms.total_price_isk.as_ref(),
        "settle.terms.total_price_isk",
    )?;
    validate_total_price(quantity, unit_price_minor, total_price_minor)?;

    if let Some(accepted) = command.accepted_trade.as_ref() {
        validate_accept_matches_settle(
            accepted,
            trade_instance_id,
            buyer_capsuleer_id,
            buyer_wallet_id,
            destination_item_stack_id,
            quantity,
            unit_price_minor,
            total_price_minor,
        )?;
    }

    let requested_at =
        millis_to_datetime(terms.requested_at_unix_millis).unwrap_or(ctx.requested_at);

    let mut tx = pool.begin().await?;
    if let BeginCommand::Replay(result) = begin_operation(&mut tx, &ctx).await? {
        tx.commit().await?;
        return Ok(result);
    }

    let trade = lock_trade_instance(&mut tx, trade_instance_id).await?;
    if trade.issuer_id != seller_capsuleer_id
        || trade.issuer_wallet_id != seller_wallet_id
        || trade.unit_price_minor != unit_price_minor
    {
        return Err(SettlementError::TradeMismatch {
            trade_instance_id: trade_instance_id.to_string(),
        });
    }

    if trade_is_expired(&trade, requested_at) {
        let (expired_trade, _released) = expire_locked_trade_instance(
            &mut tx,
            &ctx,
            &trade,
            Some(trade_transaction_id),
            Some(settlement_id),
            "settlement_request_after_expiration",
        )
        .await?;
        let expired_transaction = insert_trade_transaction(
            &mut tx,
            ctx.operation_id,
            trade_transaction_id,
            trade_instance_id,
            "expired",
            buyer_capsuleer_id,
            buyer_wallet_id,
            seller_capsuleer_id,
            seller_wallet_id,
            trade.item_type_id,
            source_escrow_id,
            Some(destination_item_stack_id),
            quantity,
            unit_price_minor,
            total_price_minor,
            requested_at,
        )
        .await?;
        insert_settlement(
            &mut tx,
            settlement_id,
            ctx.operation_id,
            trade_transaction_id,
            &ctx.idempotency_key,
            "completed",
            "completed",
            requested_at,
            None,
            None,
        )
        .await?;
        let settlement_steps = create_settlement_steps(&mut tx, settlement_id).await?;
        insert_domain_event(
            &mut tx,
            ctx.operation_id,
            "trade_instance_expired",
            "trade_instance",
            trade_instance_id,
        )
        .await?;
        finish_operation(
            &mut tx,
            &ctx,
            FinishIds {
                trade_instance_id: Some(trade_instance_id),
                trade_transaction_id: Some(trade_transaction_id),
                settlement_id: Some(settlement_id),
                wallet_operation_id: None,
                item_stack_operation_id: None,
                result_kind: "settle_trade_instance",
                result_state: "expired",
            },
        )
        .await?;
        tx.commit().await?;

        return Ok(TradeSettlementResult {
            metadata: Some(ctx.metadata),
            operation_kind: OP_SETTLE,
            attempt_status: ATTEMPT_COMMITTED,
            trade_instance_id: some_trade_instance_id(trade_instance_id),
            trade_transaction_id: some_trade_transaction_id(trade_transaction_id),
            settlement_id: some_settlement_id(settlement_id),
            resulting_trade_state: TRADE_STATE_EXPIRED,
            settlement_steps,
            result: Some(trade_settlement_result::Result::SettleTradeInstance(
                SettleTradeInstanceOutcome {
                    applied: Some(SettleTradeInstanceApplied {
                        trade_instance: Some(trade_instance_proto(&expired_trade)),
                        trade_transaction: Some(trade_transaction_proto(&expired_transaction)),
                        trade_claims: Vec::new(),
                        trade_claim_isks: Vec::new(),
                        trade_claim_item_stacks: Vec::new(),
                    }),
                    resulting_trade_state: TRADE_STATE_EXPIRED,
                },
            )),
        });
    }

    if trade.trade_state != "outstanding" {
        return Err(SettlementError::InvalidTransition {
            from: trade.trade_state,
            action: "settle_trade_instance",
        });
    }
    if trade.remaining_quantity < quantity {
        return Err(SettlementError::InsufficientItems {
            item_stack_id: source_escrow_id.to_string(),
        });
    }

    let escrow = lock_item_stack_escrow(&mut tx, source_escrow_id).await?;
    if escrow.trade_instance_id != trade_instance_id || escrow.quantity < quantity {
        return Err(SettlementError::InsufficientItems {
            item_stack_id: source_escrow_id.to_string(),
        });
    }

    let buyer_wallet = lock_wallet(&mut tx, buyer_wallet_id).await?;
    let seller_wallet = lock_wallet(&mut tx, seller_wallet_id).await?;
    if buyer_wallet.capsuleer_id != buyer_capsuleer_id
        || seller_wallet.capsuleer_id != seller_capsuleer_id
    {
        return Err(SettlementError::TradeMismatch {
            trade_instance_id: trade_instance_id.to_string(),
        });
    }
    if buyer_wallet.isk_minor < total_price_minor {
        return Err(SettlementError::InsufficientIsk {
            wallet_id: buyer_wallet_id.to_string(),
        });
    }

    let destination_stack = lock_or_create_item_stack(
        &mut tx,
        destination_item_stack_id,
        buyer_capsuleer_id,
        trade.item_type_id,
        trade.station_id,
        trade.region_id,
        requested_at,
    )
    .await?;
    if destination_stack.owner_id != buyer_capsuleer_id
        || destination_stack.item_type_id != trade.item_type_id
        || destination_stack.station_id != trade.station_id
    {
        return Err(SettlementError::TradeMismatch {
            trade_instance_id: trade_instance_id.to_string(),
        });
    }

    let wallet_operation_id =
        create_wallet_operation(&mut tx, ctx.operation_id, ctx.operation_name).await?;
    mutate_wallet(
        &mut tx,
        wallet_operation_id,
        buyer_wallet_id,
        -total_price_minor,
        "trade_purchase_debit",
    )
    .await?;
    mutate_wallet(
        &mut tx,
        wallet_operation_id,
        seller_wallet_id,
        total_price_minor,
        "trade_sale_credit",
    )
    .await?;

    let item_stack_operation_id =
        create_item_stack_operation(&mut tx, ctx.operation_id, ctx.operation_name).await?;
    mutate_item_stack(
        &mut tx,
        item_stack_operation_id,
        destination_item_stack_id,
        quantity,
        "trade_delivery_credit",
    )
    .await?;

    let remaining_escrow_quantity = escrow.quantity - quantity;
    let escrow_state = if remaining_escrow_quantity == 0 {
        "used"
    } else {
        "partially_used"
    };
    sqlx::query(
        r#"
        UPDATE item_stack_escrow
        SET quantity = $2, escrow_state = $3, updated_at = $4,
            released_at = CASE WHEN $2 = 0 THEN $4 ELSE released_at END
        WHERE item_stack_escrow_id = $1
        "#,
    )
    .bind(source_escrow_id)
    .bind(remaining_escrow_quantity)
    .bind(escrow_state)
    .bind(requested_at)
    .execute(tx.as_mut())
    .await?;

    let new_remaining_quantity = trade.remaining_quantity - quantity;
    let new_trade_state = if new_remaining_quantity == 0 {
        "completed"
    } else {
        "outstanding"
    };
    let updated_trade = update_trade_state_and_remaining(
        &mut tx,
        trade_instance_id,
        new_trade_state,
        new_remaining_quantity,
        requested_at,
    )
    .await?;

    let trade_transaction = insert_trade_transaction(
        &mut tx,
        ctx.operation_id,
        trade_transaction_id,
        trade_instance_id,
        "completed",
        buyer_capsuleer_id,
        buyer_wallet_id,
        seller_capsuleer_id,
        seller_wallet_id,
        trade.item_type_id,
        source_escrow_id,
        Some(destination_item_stack_id),
        quantity,
        unit_price_minor,
        total_price_minor,
        requested_at,
    )
    .await?;
    insert_settlement(
        &mut tx,
        settlement_id,
        ctx.operation_id,
        trade_transaction_id,
        &ctx.idempotency_key,
        "completed",
        "completed",
        requested_at,
        None,
        None,
    )
    .await?;
    let settlement_steps = create_settlement_steps(&mut tx, settlement_id).await?;

    let (claims, claim_isks, claim_item_stacks) = create_trade_claims(
        &mut tx,
        ctx.operation_id,
        trade_transaction_id,
        settlement_id,
        seller_capsuleer_id,
        seller_wallet_id,
        buyer_capsuleer_id,
        destination_item_stack_id,
        trade.item_type_id,
        quantity,
        total_price_minor,
        requested_at,
    )
    .await?;

    insert_trade_state_change(
        &mut tx,
        &ctx,
        trade_instance_id,
        Some(trade_transaction_id),
        Some(settlement_id),
        Some(&trade.trade_state),
        new_trade_state,
        "settle_trade_instance",
    )
    .await?;
    insert_domain_event(
        &mut tx,
        ctx.operation_id,
        "trade_instance_settled",
        "trade_instance",
        trade_instance_id,
    )
    .await?;
    finish_operation(
        &mut tx,
        &ctx,
        FinishIds {
            trade_instance_id: Some(trade_instance_id),
            trade_transaction_id: Some(trade_transaction_id),
            settlement_id: Some(settlement_id),
            wallet_operation_id: Some(wallet_operation_id),
            item_stack_operation_id: Some(item_stack_operation_id),
            result_kind: "settle_trade_instance",
            result_state: new_trade_state,
        },
    )
    .await?;

    tx.commit().await?;

    Ok(TradeSettlementResult {
        metadata: Some(ctx.metadata),
        operation_kind: OP_SETTLE,
        attempt_status: ATTEMPT_COMMITTED,
        trade_instance_id: some_trade_instance_id(trade_instance_id),
        trade_transaction_id: some_trade_transaction_id(trade_transaction_id),
        settlement_id: some_settlement_id(settlement_id),
        resulting_trade_state: trade_state_i32(new_trade_state),
        settlement_steps,
        result: Some(trade_settlement_result::Result::SettleTradeInstance(
            SettleTradeInstanceOutcome {
                applied: Some(SettleTradeInstanceApplied {
                    trade_instance: Some(trade_instance_proto(&updated_trade)),
                    trade_transaction: Some(trade_transaction_proto(&trade_transaction)),
                    trade_claims: claims.iter().map(trade_claim_proto).collect(),
                    trade_claim_isks: claim_isks.iter().map(trade_claim_isk_proto).collect(),
                    trade_claim_item_stacks: claim_item_stacks
                        .iter()
                        .map(trade_claim_item_stack_proto)
                        .collect(),
                }),
                resulting_trade_state: trade_state_i32(new_trade_state),
            },
        )),
    })
}

pub(crate) async fn cancel_trade_instance(
    pool: &PgPool,
    ctx: CommandContext,
    command: CancelTradeInstanceCommand,
) -> Result<TradeSettlementResult, SettlementError> {
    validate_nested_metadata(
        &ctx.metadata,
        command.metadata.as_ref(),
        "cancel_trade_instance",
    )?;
    let row_ids = command
        .row_ids
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("cancel row_ids is required".to_string()))?;
    let trade_instance_id = parse_uuid_message(
        row_ids.trade_instance_id.as_ref().map(|x| x.value.as_str()),
        "cancel.row_ids.trade_instance_id",
    )?;
    let requester_id = required_positive_i64(
        row_ids.requesting_capsuleer_id.as_ref().map(|x| x.value),
        "cancel.row_ids.requesting_capsuleer_id",
    )?;

    let mut tx = pool.begin().await?;
    if let BeginCommand::Replay(result) = begin_operation(&mut tx, &ctx).await? {
        tx.commit().await?;
        return Ok(result);
    }

    let trade = lock_trade_instance(&mut tx, trade_instance_id).await?;
    if trade.issuer_id != requester_id {
        return Err(SettlementError::InvalidRequest(
            "only the issuer can cancel the trade instance".to_string(),
        ));
    }
    if trade.trade_state != "outstanding" {
        return Err(SettlementError::InvalidTransition {
            from: trade.trade_state,
            action: "cancel_trade_instance",
        });
    }

    let reason = if command.reason.trim().is_empty() {
        "cancelled_by_issuer"
    } else {
        command.reason.trim()
    };
    let released_item_escrows = release_remaining_item_escrows(
        &mut tx,
        ctx.operation_id,
        trade_instance_id,
        "cancelled",
        reason,
        ctx.requested_at,
    )
    .await?;
    let updated_trade = update_trade_state_and_remaining(
        &mut tx,
        trade_instance_id,
        "cancelled",
        trade.remaining_quantity,
        ctx.requested_at,
    )
    .await?;
    insert_trade_state_change(
        &mut tx,
        &ctx,
        trade_instance_id,
        None,
        None,
        Some("outstanding"),
        "cancelled",
        "cancel_trade_instance",
    )
    .await?;
    insert_domain_event(
        &mut tx,
        ctx.operation_id,
        "trade_instance_cancelled",
        "trade_instance",
        trade_instance_id,
    )
    .await?;
    finish_operation(
        &mut tx,
        &ctx,
        FinishIds {
            trade_instance_id: Some(trade_instance_id),
            trade_transaction_id: None,
            settlement_id: None,
            wallet_operation_id: None,
            item_stack_operation_id: None,
            result_kind: "cancel_trade_instance",
            result_state: "cancelled",
        },
    )
    .await?;

    tx.commit().await?;

    Ok(TradeSettlementResult {
        metadata: Some(ctx.metadata),
        operation_kind: OP_CANCEL,
        attempt_status: ATTEMPT_COMMITTED,
        trade_instance_id: some_trade_instance_id(trade_instance_id),
        trade_transaction_id: None,
        settlement_id: None,
        resulting_trade_state: TRADE_STATE_CANCELLED,
        settlement_steps: Vec::new(),
        result: Some(trade_settlement_result::Result::CancelTradeInstance(
            CancelTradeInstanceOutcome {
                applied: Some(CancelTradeInstanceApplied {
                    trade_instance: Some(trade_instance_proto(&updated_trade)),
                    released_item_stack_escrows: released_item_escrows
                        .iter()
                        .map(item_stack_escrow_proto)
                        .collect(),
                    released_wallet_escrows: Vec::new(),
                }),
            },
        )),
    })
}

pub(crate) async fn expire_trade_instance(
    pool: &PgPool,
    ctx: CommandContext,
    command: ExpireTradeInstanceCommand,
) -> Result<TradeSettlementResult, SettlementError> {
    validate_nested_metadata(
        &ctx.metadata,
        command.metadata.as_ref(),
        "expire_trade_instance",
    )?;
    let row_ids = command
        .row_ids
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("expire row_ids is required".to_string()))?;
    let trade_instance_id = parse_uuid_message(
        row_ids.trade_instance_id.as_ref().map(|x| x.value.as_str()),
        "expire.row_ids.trade_instance_id",
    )?;
    let evaluated_at =
        millis_to_datetime(command.evaluated_at_unix_millis).unwrap_or(ctx.requested_at);

    let mut tx = pool.begin().await?;
    if let BeginCommand::Replay(result) = begin_operation(&mut tx, &ctx).await? {
        tx.commit().await?;
        return Ok(result);
    }

    let trade = lock_trade_instance(&mut tx, trade_instance_id).await?;
    if trade.trade_state != "outstanding" {
        return Err(SettlementError::InvalidTransition {
            from: trade.trade_state,
            action: "expire_trade_instance",
        });
    }
    if !trade_is_expired(&trade, evaluated_at) {
        return Err(SettlementError::InvalidRequest(
            "trade instance has not reached its expiration time".to_string(),
        ));
    }

    let (updated_trade, released_item_escrows) =
        expire_locked_trade_instance(&mut tx, &ctx, &trade, None, None, "expiration_sweeper")
            .await?;
    insert_domain_event(
        &mut tx,
        ctx.operation_id,
        "trade_instance_expired",
        "trade_instance",
        trade_instance_id,
    )
    .await?;
    finish_operation(
        &mut tx,
        &ctx,
        FinishIds {
            trade_instance_id: Some(trade_instance_id),
            trade_transaction_id: None,
            settlement_id: None,
            wallet_operation_id: None,
            item_stack_operation_id: None,
            result_kind: "expire_trade_instance",
            result_state: "expired",
        },
    )
    .await?;

    tx.commit().await?;

    Ok(TradeSettlementResult {
        metadata: Some(ctx.metadata),
        operation_kind: OP_EXPIRE,
        attempt_status: ATTEMPT_COMMITTED,
        trade_instance_id: some_trade_instance_id(trade_instance_id),
        trade_transaction_id: None,
        settlement_id: None,
        resulting_trade_state: TRADE_STATE_EXPIRED,
        settlement_steps: Vec::new(),
        result: Some(trade_settlement_result::Result::ExpireTradeInstance(
            ExpireTradeInstanceOutcome {
                applied: Some(ExpireTradeInstanceApplied {
                    trade_instance: Some(trade_instance_proto(&updated_trade)),
                    released_item_stack_escrows: released_item_escrows
                        .iter()
                        .map(item_stack_escrow_proto)
                        .collect(),
                    released_wallet_escrows: Vec::new(),
                }),
            },
        )),
    })
}
