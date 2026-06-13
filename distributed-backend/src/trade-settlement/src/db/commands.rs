use sqlx::PgPool;

use crate::error::SettlementError;
use crate::generated::eve_trade::settlement::v1::{
    trade_settlement_command, TradeSettlementCommand, TradeSettlementResult,
};

use super::{
    responses::{command_identity_hints, rejected_result, result_unknown},
    transactions::{
        cancel_trade_instance, expire_trade_instance, issue_trade_instance, settle_trade_instance,
    },
    validation::{command_context, inferred_operation_kind},
};

pub async fn execute_trade_settlement_command(
    pool: &PgPool,
    command: TradeSettlementCommand,
) -> Result<TradeSettlementResult, SettlementError> {
    let command_kind = command
        .command
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("command is required".to_string()))?;
    let operation_kind = inferred_operation_kind(command_kind);

    if command.operation_kind != 0 && command.operation_kind != operation_kind {
        return Err(SettlementError::InvalidRequest(
            "operation_kind does not match command payload".to_string(),
        ));
    }

    let ctx = command_context(&command, operation_kind)?;

    match command.command {
        Some(trade_settlement_command::Command::IssueTradeInstance(issue)) => {
            issue_trade_instance(pool, ctx, issue).await
        }
        Some(trade_settlement_command::Command::SettleTradeInstance(settle)) => {
            settle_trade_instance(pool, ctx, settle).await
        }
        Some(trade_settlement_command::Command::CancelTradeInstance(cancel)) => {
            cancel_trade_instance(pool, ctx, cancel).await
        }
        Some(trade_settlement_command::Command::ExpireTradeInstance(expire)) => {
            expire_trade_instance(pool, ctx, expire).await
        }
        None => unreachable!("command payload was checked above"),
    }
}

pub fn missing_command_result() -> TradeSettlementResult {
    rejected_result(
        None,
        0,
        None,
        None,
        None,
        SettlementError::InvalidRequest("command is required".to_string()),
    )
}

pub fn settlement_error_result(
    command: &TradeSettlementCommand,
    err: SettlementError,
) -> TradeSettlementResult {
    let operation_kind = command
        .command
        .as_ref()
        .map(inferred_operation_kind)
        .unwrap_or(command.operation_kind);
    let (trade_instance_id, trade_transaction_id, settlement_id) = command_identity_hints(command);

    if err.error_code() == 7 || err.retryable() {
        result_unknown(
            command.metadata.clone(),
            operation_kind,
            trade_instance_id,
            trade_transaction_id,
            settlement_id,
            err,
        )
    } else {
        rejected_result(
            command.metadata.clone(),
            operation_kind,
            trade_instance_id,
            trade_transaction_id,
            settlement_id,
            err,
        )
    }
}
