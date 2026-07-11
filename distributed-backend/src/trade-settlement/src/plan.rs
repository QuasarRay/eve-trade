use crate::commands::{ExecuteBatchCommand, SettlementCommand, SettlementIntent};
use crate::error::{Result, SettlementError};

pub fn validate_plan_semantics(command: &ExecuteBatchCommand) -> Result<()> {
    let actor = command.caused_by_capsuleer_id.ok_or_else(|| {
        SettlementError::InvalidArgument("caused_by_capsuleer_id is required".to_string())
    })?;
    match command.intent {
        SettlementIntent::Issue => validate_issue(actor, &command.operations),
        SettlementIntent::Accept => validate_accept(actor, &command.operations),
        SettlementIntent::Cancel => validate_cancel(&command.operations),
        SettlementIntent::Unspecified => Err(SettlementError::InvalidArgument(
            "settlement intent is required".to_string(),
        )),
    }
}

fn validate_issue(actor: i64, operations: &[SettlementCommand]) -> Result<()> {
    let [SettlementCommand::CreateNewTradeInstanceRow(create), SettlementCommand::TransferQuantityFromItemStackToItemStackEscrow(escrow)] =
        operations
    else {
        return invalid_grammar("ISSUE requires create-trade followed by item escrow funding");
    };
    if create.issuer_id != actor {
        return Err(SettlementError::PermissionDenied(
            "ISSUE actor must equal trade issuer".to_string(),
        ));
    }
    if create.trade_instance_id != Some(escrow.trade_instance_id)
        || create.total_quantity != escrow.quantity
    {
        return invalid_grammar("ISSUE trade identity and escrow quantity must match");
    }
    Ok(())
}

fn validate_accept(actor: i64, operations: &[SettlementCommand]) -> Result<()> {
    let mut index = 0;
    if let Some(SettlementCommand::CreateNewEmptyItemStack(destination)) = operations.first() {
        if destination.owner_id != actor {
            return Err(SettlementError::PermissionDenied(
                "ACCEPT destination item stack must belong to actor".to_string(),
            ));
        }
        index += 1;
    }
    let Some(SettlementCommand::TransferIskAmountFromWalletToWalletEscrow(debit)) =
        operations.get(index)
    else {
        return invalid_grammar("ACCEPT requires actor wallet debit");
    };
    let Some(SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(_)) =
        operations.get(index + 1)
    else {
        return invalid_grammar("ACCEPT requires item delivery to the new owner");
    };
    let Some(SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithNewOwner(credit)) =
        operations.get(index + 2)
    else {
        return invalid_grammar("ACCEPT requires seller wallet credit");
    };
    if debit.wallet_escrow_id != Some(credit.wallet_escrow_id)
        || debit.isk_amount != credit.isk_amount
    {
        return invalid_grammar("ACCEPT wallet escrow identity and amounts must balance");
    }
    index += 3;
    if let Some(SettlementCommand::ModifyTradeInstanceState(state)) = operations.get(index) {
        if state.trade_instance_id != debit.trade_instance_id
            || state.to_trade_state != "COMPLETED"
            || state.trade_state_change_kind != "ACCEPTED_BY_BUYER"
        {
            return invalid_grammar("ACCEPT completion state transition is invalid");
        }
        index += 1;
    }
    if index != operations.len() {
        return invalid_grammar(
            "ACCEPT contains a forbidden, duplicate, or out-of-order operation",
        );
    }
    Ok(())
}

fn validate_cancel(operations: &[SettlementCommand]) -> Result<()> {
    let mut index = 0;
    if matches!(
        operations.get(index),
        Some(SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner(_))
    ) {
        index += 1;
    }
    if matches!(
        operations.get(index),
        Some(SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner(_))
    ) {
        index += 1;
    }
    let Some(SettlementCommand::ModifyTradeInstanceState(state)) = operations.get(index) else {
        return invalid_grammar("CANCEL requires a final trade cancellation transition");
    };
    if state.to_trade_state != "CANCELLED" || state.trade_state_change_kind != "CANCELLED_BY_ISSUER"
    {
        return invalid_grammar("CANCEL state transition is invalid");
    }
    index += 1;
    if index != operations.len() {
        return invalid_grammar(
            "CANCEL contains a forbidden, duplicate, or out-of-order operation",
        );
    }
    Ok(())
}

fn invalid_grammar<T>(message: &str) -> Result<T> {
    Err(SettlementError::InvalidArgument(format!(
        "settlement operation grammar: {message}"
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::{
        CreateNewTradeInstanceRow, TransferQuantityFromItemStackToItemStackEscrow,
    };
    use uuid::Uuid;

    fn issue_command(actor: i64) -> ExecuteBatchCommand {
        let trade_id = Uuid::from_u128(1);
        ExecuteBatchCommand {
            intent: SettlementIntent::Issue,
            idempotency_key: "issue-1".into(),
            request_fingerprint: None,
            external_request_id: None,
            caused_by_capsuleer_id: Some(actor),
            operations: vec![
                SettlementCommand::CreateNewTradeInstanceRow(CreateNewTradeInstanceRow {
                    trade_instance_id: Some(trade_id),
                    trade_kind: "SELL".into(),
                    trade_state: "OPEN".into(),
                    issuer_id: 1001,
                    item_type_id: 34,
                    station_id: 60003760,
                    total_quantity: 4,
                    unit_price_isk: 25,
                    expires_at: None,
                }),
                SettlementCommand::TransferQuantityFromItemStackToItemStackEscrow(
                    TransferQuantityFromItemStackToItemStackEscrow {
                        source_item_stack_id: Uuid::from_u128(2),
                        item_stack_escrow_id: Some(Uuid::from_u128(3)),
                        trade_instance_id: trade_id,
                        quantity: 4,
                    },
                ),
            ],
            created_by_service: "market".into(),
            request_id: None,
        }
    }

    #[test]
    fn issue_requires_actor_to_equal_issuer() {
        let error = validate_plan_semantics(&issue_command(2002)).unwrap_err();
        assert_eq!(error.code(), "PERMISSION_DENIED");
    }

    #[test]
    fn issue_rejects_legal_primitives_in_illegal_order() {
        let mut command = issue_command(1001);
        command.operations.reverse();
        assert!(validate_plan_semantics(&command).is_err());
    }

    #[test]
    fn issue_rejects_missing_required_operation() {
        let mut command = issue_command(1001);
        command.operations.pop();
        assert!(validate_plan_semantics(&command).is_err());
    }

    #[test]
    fn issue_rejects_accept_only_operation() {
        let mut command = issue_command(1001);
        command.intent = SettlementIntent::Cancel;
        assert!(validate_plan_semantics(&command).is_err());
    }
}
