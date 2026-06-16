#![allow(clippy::too_many_arguments)]

use sqlx::PgPool;
use uuid::Uuid;

use crate::error::SettlementError;
use crate::generated::eve_trade::{operation::v1::*, settlement::v1::*};

use super::{
    idempotency::{begin_operation, finish_operation},
    item_stacks::{
        transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner,
        transfer_quantity_from_item_stack_escrow_to_item_stack_with_previous_owner,
        transfer_quantity_from_item_stack_to_item_stack_escrow,
    },
    queries::*,
    responses::*,
    support::tx_conn,
    trade::{create_new_trade_instance_row, modify_trade_instance_state},
    types::*,
    validation::*,
    wallets::{
        create_new_empty_wallet_escrow, transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner,
        transfer_isk_amount_from_wallet_to_wallet_escrow,
    },
};

#[tracing::instrument(
    name = "trade_settlement.issue_trade_instance",
    skip(pool, ctx, command),
    fields(
        trade.operation.id = %ctx.operation_id,
        trade.request.id = %ctx.request_id,
        trade.operation.kind = ctx.operation_kind,
    )
)]
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
    let wallet_escrow_id = parse_optional_uuid_message(
        row_ids.wallet_escrow_id.as_ref().map(|x| x.value.as_str()),
        "issue.row_ids.wallet_escrow_id",
    )?;

    let total_quantity =
        required_quantity(terms.total_quantity.as_ref(), "issue.terms.total_quantity")?;
    let unit_price_minor =
        required_money(terms.unit_price_isk.as_ref(), "issue.terms.unit_price_isk")?;
    let expires_at = millis_to_datetime(terms.expires_at_unix_millis);

    if let BeginCommand::Replay(result) = begin_operation(pool, &ctx).await? {
        return Ok(result);
    }

    let source_stack = load_item_stack(pool, source_item_stack_id).await?;
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

    let trade = create_new_trade_instance_row(
        pool,
        CreateNewTradeInstanceRowInput {
            trade_instance_id,
            operation_id: ctx.operation_id,
            trade_state: "outstanding".to_string(),
            issuer_id,
            issuer_wallet_id,
            item_type_id,
            station_id,
            region_id,
            total_quantity,
            unit_price_minor,
            expires_at,
            created_at: ctx.requested_at,
        },
    )
    .await?;

    let item_transfer = transfer_quantity_from_item_stack_to_item_stack_escrow(
        pool,
        TransferQuantityFromItemStackToItemStackEscrowInput {
            operation_id: ctx.operation_id,
            operation_kind: ctx.operation_name.to_string(),
            item_stack_operation_id: Uuid::new_v4(),
            source_item_stack_id,
            item_stack_escrow_id,
            trade_instance_id,
            issuer_id,
            quantity: total_quantity,
            created_at: ctx.requested_at,
        },
    )
    .await?;

    let wallet_escrow = match wallet_escrow_id {
        Some(wallet_escrow_id) => Some(
            create_new_empty_wallet_escrow(
                pool,
                CreateNewEmptyWalletEscrowInput {
                    operation_id: ctx.operation_id,
                    operation_kind: ctx.operation_name.to_string(),
                    wallet_operation_id: Uuid::new_v4(),
                    wallet_escrow_id,
                    trade_instance_id,
                    owner_id: issuer_id,
                    created_at: ctx.requested_at,
                },
            )
            .await?,
        ),
        None => None,
    };

    {
        let mut tx = pool.begin().await?;
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
        tx.commit().await?;
    }

    finish_operation(
        pool,
        &ctx,
        FinishIds {
            trade_instance_id: Some(trade_instance_id),
            trade_transaction_id: None,
            settlement_id: None,
            wallet_operation_id: wallet_escrow
                .as_ref()
                .map(|escrow| escrow.created_wallet_operation_id),
            item_stack_operation_id: Some(item_transfer.item_stack_operation_id),
            result_kind: "issue_trade_instance",
            result_state: "outstanding",
        },
    )
    .await?;

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
                    item_stack_escrow: Some(item_stack_escrow_proto(
                        &item_transfer.item_stack_escrow,
                    )),
                    wallet_escrow: wallet_escrow.as_ref().map(wallet_escrow_proto),
                }),
            },
        )),
    })
}

#[tracing::instrument(
    name = "trade_settlement.settle_trade_instance",
    skip(pool, ctx, command),
    fields(
        trade.operation.id = %ctx.operation_id,
        trade.request.id = %ctx.request_id,
        trade.operation.kind = ctx.operation_kind,
    )
)]
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

    if let BeginCommand::Replay(result) = begin_operation(pool, &ctx).await? {
        return Ok(result);
    }

    let trade = load_trade_instance(pool, trade_instance_id).await?;
    if trade.issuer_id != seller_capsuleer_id
        || trade.issuer_wallet_id != seller_wallet_id
        || trade.unit_price_minor != unit_price_minor
    {
        return Err(SettlementError::TradeMismatch {
            trade_instance_id: trade_instance_id.to_string(),
        });
    }

    if trade_is_expired(&trade, requested_at) {
        release_remaining_item_escrows(
            pool,
            &ctx,
            trade_instance_id,
            "settlement_request_after_expiration",
            requested_at,
        )
        .await?;
        let expired_trade = modify_trade_instance_state(
            pool,
            ModifyTradeInstanceStateInput {
                trade_instance_id,
                expected_trade_state: Some(trade.trade_state.clone()),
                new_trade_state: "expired".to_string(),
                remaining_quantity: Some(trade.remaining_quantity),
                updated_at: requested_at,
            },
        )
        .await?;

        let (expired_transaction, settlement_steps) = {
            let mut tx = pool.begin().await?;
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
            insert_trade_state_change(
                &mut tx,
                &ctx,
                trade_instance_id,
                Some(trade_transaction_id),
                Some(settlement_id),
                Some(&trade.trade_state),
                "expired",
                "expire_trade_instance",
            )
            .await?;
            insert_domain_event(
                &mut tx,
                ctx.operation_id,
                "trade_instance_expired",
                "trade_instance",
                trade_instance_id,
            )
            .await?;
            tx.commit().await?;
            (expired_transaction, settlement_steps)
        };

        finish_operation(
            pool,
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

    let escrow = load_item_stack_escrow(pool, source_escrow_id).await?;
    if escrow.trade_instance_id != trade_instance_id || escrow.quantity < quantity {
        return Err(SettlementError::InsufficientItems {
            item_stack_id: source_escrow_id.to_string(),
        });
    }

    let buyer_wallet = load_wallet(pool, buyer_wallet_id).await?;
    let seller_wallet = load_wallet(pool, seller_wallet_id).await?;
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

    let wallet_operation_id = if total_price_minor > 0 {
        let wallet_escrow_id = Uuid::new_v4();
        create_new_empty_wallet_escrow(
            pool,
            CreateNewEmptyWalletEscrowInput {
                operation_id: ctx.operation_id,
                operation_kind: ctx.operation_name.to_string(),
                wallet_operation_id: Uuid::new_v4(),
                wallet_escrow_id,
                trade_instance_id,
                owner_id: buyer_capsuleer_id,
                created_at: requested_at,
            },
        )
        .await?;
        transfer_isk_amount_from_wallet_to_wallet_escrow(
            pool,
            TransferIskAmountFromWalletToWalletEscrowInput {
                operation_id: ctx.operation_id,
                operation_kind: ctx.operation_name.to_string(),
                wallet_operation_id: Uuid::new_v4(),
                source_wallet_id: buyer_wallet_id,
                wallet_escrow_id,
                isk_minor: total_price_minor,
                transferred_at: requested_at,
            },
        )
        .await?;
        let released = transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner(
            pool,
            TransferIskAmountFromWalletEscrowToWalletWithNewOwnerInput {
                operation_id: ctx.operation_id,
                operation_kind: ctx.operation_name.to_string(),
                wallet_operation_id: Uuid::new_v4(),
                wallet_escrow_id,
                destination_wallet_id: seller_wallet_id,
                new_owner_id: seller_capsuleer_id,
                isk_minor: total_price_minor,
                transferred_at: requested_at,
            },
        )
        .await?;
        Some(released.wallet_operation_id)
    } else {
        None
    };

    let item_transfer = transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner(
        pool,
        TransferQuantityFromItemStackEscrowToItemStackWithNewOwnerInput {
            operation_id: ctx.operation_id,
            operation_kind: ctx.operation_name.to_string(),
            item_stack_operation_id: Uuid::new_v4(),
            item_stack_escrow_id: source_escrow_id,
            destination_item_stack_id,
            new_owner_id: buyer_capsuleer_id,
            quantity,
            transferred_at: requested_at,
        },
    )
    .await?;

    let new_remaining_quantity = trade.remaining_quantity - quantity;
    let new_trade_state: &'static str = if new_remaining_quantity == 0 {
        "completed"
    } else {
        "outstanding"
    };
    let updated_trade = modify_trade_instance_state(
        pool,
        ModifyTradeInstanceStateInput {
            trade_instance_id,
            expected_trade_state: Some("outstanding".to_string()),
            new_trade_state: new_trade_state.to_string(),
            remaining_quantity: Some(new_remaining_quantity),
            updated_at: requested_at,
        },
    )
    .await?;

    let (trade_transaction, settlement_steps, claims, claim_isks, claim_item_stacks) = {
        let mut tx = pool.begin().await?;
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
        tx.commit().await?;
        (
            trade_transaction,
            settlement_steps,
            claims,
            claim_isks,
            claim_item_stacks,
        )
    };

    finish_operation(
        pool,
        &ctx,
        FinishIds {
            trade_instance_id: Some(trade_instance_id),
            trade_transaction_id: Some(trade_transaction_id),
            settlement_id: Some(settlement_id),
            wallet_operation_id,
            item_stack_operation_id: Some(item_transfer.item_stack_operation_id),
            result_kind: "settle_trade_instance",
            result_state: new_trade_state,
        },
    )
    .await?;

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

#[tracing::instrument(
    name = "trade_settlement.cancel_trade_instance",
    skip(pool, ctx, command),
    fields(
        trade.operation.id = %ctx.operation_id,
        trade.request.id = %ctx.request_id,
        trade.operation.kind = ctx.operation_kind,
    )
)]
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

    if let BeginCommand::Replay(result) = begin_operation(pool, &ctx).await? {
        return Ok(result);
    }

    let trade = load_trade_instance(pool, trade_instance_id).await?;
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
    let released_item_escrows =
        release_remaining_item_escrows(pool, &ctx, trade_instance_id, reason, ctx.requested_at)
            .await?;
    let updated_trade = modify_trade_instance_state(
        pool,
        ModifyTradeInstanceStateInput {
            trade_instance_id,
            expected_trade_state: Some("outstanding".to_string()),
            new_trade_state: "cancelled".to_string(),
            remaining_quantity: Some(trade.remaining_quantity),
            updated_at: ctx.requested_at,
        },
    )
    .await?;

    {
        let mut tx = pool.begin().await?;
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
        tx.commit().await?;
    }

    finish_operation(
        pool,
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

#[tracing::instrument(
    name = "trade_settlement.expire_trade_instance",
    skip(pool, ctx, command),
    fields(
        trade.operation.id = %ctx.operation_id,
        trade.request.id = %ctx.request_id,
        trade.operation.kind = ctx.operation_kind,
    )
)]
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

    if let BeginCommand::Replay(result) = begin_operation(pool, &ctx).await? {
        return Ok(result);
    }

    let trade = load_trade_instance(pool, trade_instance_id).await?;
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

    let released_item_escrows = release_remaining_item_escrows(
        pool,
        &ctx,
        trade_instance_id,
        "expiration_sweeper",
        evaluated_at,
    )
    .await?;
    let updated_trade = modify_trade_instance_state(
        pool,
        ModifyTradeInstanceStateInput {
            trade_instance_id,
            expected_trade_state: Some("outstanding".to_string()),
            new_trade_state: "expired".to_string(),
            remaining_quantity: Some(trade.remaining_quantity),
            updated_at: evaluated_at,
        },
    )
    .await?;

    {
        let mut tx = pool.begin().await?;
        insert_trade_state_change(
            &mut tx,
            &ctx,
            trade_instance_id,
            None,
            None,
            Some("outstanding"),
            "expired",
            "expire_trade_instance",
        )
        .await?;
        insert_domain_event(
            &mut tx,
            ctx.operation_id,
            "trade_instance_expired",
            "trade_instance",
            trade_instance_id,
        )
        .await?;
        tx.commit().await?;
    }

    finish_operation(
        pool,
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

async fn release_remaining_item_escrows(
    pool: &PgPool,
    ctx: &CommandContext,
    trade_instance_id: Uuid,
    reason: &str,
    transferred_at: chrono::DateTime<chrono::Utc>,
) -> Result<Vec<ItemStackEscrowRow>, SettlementError> {
    let escrows = load_releasable_item_stack_escrows(pool, trade_instance_id).await?;
    let mut released = Vec::with_capacity(escrows.len());

    for escrow in escrows {
        let result = transfer_quantity_from_item_stack_escrow_to_item_stack_with_previous_owner(
            pool,
            TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwnerInput {
                operation_id: ctx.operation_id,
                operation_kind: ctx.operation_name.to_string(),
                item_stack_operation_id: Uuid::new_v4(),
                item_stack_escrow_id: escrow.item_stack_escrow_id,
                quantity: escrow.quantity,
                release_reason: reason.to_string(),
                transferred_at,
            },
        )
        .await?;
        released.push(result.item_stack_escrow);
    }

    Ok(released)
}
