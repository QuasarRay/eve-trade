use uuid::Uuid;

use crate::error::SettlementError;
use crate::generated::eve_trade::{common::v1::*, domain::trade::v1::*, settlement::v1::*};

use super::{
    types::*,
    validation::{millis, option_millis},
};

pub(crate) fn command_identity_hints(
    command: &TradeSettlementCommand,
) -> (
    Option<TradeInstanceId>,
    Option<TradeTransactionId>,
    Option<SettlementId>,
) {
    match command.command.as_ref() {
        Some(trade_settlement_command::Command::IssueTradeInstance(cmd)) => {
            let trade_id = cmd
                .row_ids
                .as_ref()
                .and_then(|row_ids| row_ids.trade_instance_id.clone());
            (trade_id, None, None)
        }
        Some(trade_settlement_command::Command::SettleTradeInstance(cmd)) => {
            let row_ids = cmd.row_ids.as_ref();
            (
                row_ids.and_then(|x| x.trade_instance_id.clone()),
                row_ids.and_then(|x| x.trade_transaction_id.clone()),
                row_ids.and_then(|x| x.settlement_id.clone()),
            )
        }
        Some(trade_settlement_command::Command::CancelTradeInstance(cmd)) => {
            let trade_id = cmd
                .row_ids
                .as_ref()
                .and_then(|row_ids| row_ids.trade_instance_id.clone());
            (trade_id, None, None)
        }
        Some(trade_settlement_command::Command::ExpireTradeInstance(cmd)) => {
            let trade_id = cmd
                .row_ids
                .as_ref()
                .and_then(|row_ids| row_ids.trade_instance_id.clone());
            (trade_id, None, None)
        }
        None => (None, None, None),
    }
}

pub(crate) fn rejected_result(
    metadata: Option<OperationMetadata>,
    operation_kind: i32,
    trade_instance_id: Option<TradeInstanceId>,
    trade_transaction_id: Option<TradeTransactionId>,
    settlement_id: Option<SettlementId>,
    err: SettlementError,
) -> TradeSettlementResult {
    TradeSettlementResult {
        metadata,
        operation_kind,
        attempt_status: ATTEMPT_REJECTED,
        trade_instance_id,
        trade_transaction_id,
        settlement_id,
        resulting_trade_state: 0,
        settlement_steps: Vec::new(),
        result: Some(trade_settlement_result::Result::Rejected(
            TradeSettlementRejected {
                error: Some(error_detail(err)),
            },
        )),
    }
}

pub(crate) fn result_unknown(
    metadata: Option<OperationMetadata>,
    operation_kind: i32,
    trade_instance_id: Option<TradeInstanceId>,
    trade_transaction_id: Option<TradeTransactionId>,
    settlement_id: Option<SettlementId>,
    err: SettlementError,
) -> TradeSettlementResult {
    TradeSettlementResult {
        metadata,
        operation_kind,
        attempt_status: ATTEMPT_RESULT_UNKNOWN,
        trade_instance_id,
        trade_transaction_id,
        settlement_id,
        resulting_trade_state: 0,
        settlement_steps: Vec::new(),
        result: Some(trade_settlement_result::Result::ResultUnknown(
            TradeSettlementResultUnknown {
                error: Some(error_detail(err)),
            },
        )),
    }
}

pub(crate) fn error_detail(err: SettlementError) -> ErrorDetail {
    ErrorDetail {
        code: err.error_code(),
        message: err.to_string(),
        field_violations: Vec::<FieldViolation>::new(),
        retryable: err.retryable(),
    }
}

pub(crate) fn trade_instance_proto(row: &TradeInstanceRow) -> TradeInstance {
    TradeInstance {
        trade_instance_id: some_trade_instance_id(row.trade_instance_id),
        operation_id: Some(OperationId {
            value: row.operation_id.to_string(),
        }),
        trade_state: trade_state_i32(&row.trade_state),
        issuer_id: Some(CapsuleerId {
            value: row.issuer_id,
        }),
        issuer_wallet_id: Some(WalletId {
            value: row.issuer_wallet_id.to_string(),
        }),
        item_type_id: Some(ItemTypeId {
            value: row.item_type_id,
        }),
        station_id: Some(StationId {
            value: row.station_id,
        }),
        region_id: Some(RegionId {
            value: row.region_id,
        }),
        total_quantity: Some(ItemQuantity {
            units: row.total_quantity,
        }),
        remaining_quantity: Some(ItemQuantity {
            units: row.remaining_quantity,
        }),
        unit_price_isk: Some(IskAmount {
            minor_units: row.unit_price_minor,
        }),
        expires_at_unix_millis: option_millis(row.expires_at),
        created_at_unix_millis: millis(row.created_at),
        updated_at_unix_millis: millis(row.updated_at),
    }
}

pub(crate) fn item_stack_escrow_proto(row: &ItemStackEscrowRow) -> ItemStackEscrow {
    ItemStackEscrow {
        item_stack_escrow_id: Some(ItemStackEscrowId {
            value: row.item_stack_escrow_id.to_string(),
        }),
        issuer_id: Some(CapsuleerId {
            value: row.issuer_id,
        }),
        trade_instance_id: some_trade_instance_id(row.trade_instance_id),
        quantity: Some(ItemQuantity {
            units: row.quantity,
        }),
        created_at_unix_millis: millis(row.created_at),
        updated_at_unix_millis: millis(row.updated_at),
        released_at_unix_millis: option_millis(row.released_at),
        escrow_state: escrow_state_i32(&row.escrow_state),
        release_reason: row.release_reason.clone().unwrap_or_default(),
        source_item_stack_id: Some(ItemStackId {
            value: row.source_item_stack_id.to_string(),
        }),
    }
}

pub(crate) fn wallet_escrow_proto(row: &WalletEscrowRow) -> WalletEscrow {
    WalletEscrow {
        wallet_escrow_id: Some(WalletEscrowId {
            value: row.wallet_escrow_id.to_string(),
        }),
        trade_instance_id: some_trade_instance_id(row.trade_instance_id),
        isk_amount: Some(IskAmount {
            minor_units: row.isk_minor,
        }),
        owner_id: Some(CapsuleerId {
            value: row.owner_id,
        }),
        created_wallet_operation_id: Some(WalletOperationId {
            value: row.created_wallet_operation_id.to_string(),
        }),
        released_wallet_operation_id: row.released_wallet_operation_id.map(|id| {
            WalletOperationId {
                value: id.to_string(),
            }
        }),
        created_at_unix_millis: millis(row.created_at),
        updated_at_unix_millis: millis(row.updated_at),
        released_at_unix_millis: option_millis(row.released_at),
    }
}

pub(crate) fn trade_transaction_proto(row: &TradeTransactionRow) -> TradeTransaction {
    TradeTransaction {
        trade_transaction_id: some_trade_transaction_id(row.trade_transaction_id),
        operation_id: Some(OperationId {
            value: row.operation_id.to_string(),
        }),
        trade_instance_id: some_trade_instance_id(row.trade_instance_id),
        trade_transaction_state: trade_transaction_state_i32(&row.trade_transaction_state),
        buyer_capsuleer_id: Some(CapsuleerId {
            value: row.buyer_capsuleer_id,
        }),
        buyer_wallet_id: Some(WalletId {
            value: row.buyer_wallet_id.to_string(),
        }),
        seller_capsuleer_id: Some(CapsuleerId {
            value: row.seller_capsuleer_id,
        }),
        seller_wallet_id: Some(WalletId {
            value: row.seller_wallet_id.to_string(),
        }),
        item_type_id: Some(ItemTypeId {
            value: row.item_type_id,
        }),
        source_item_stack_escrow_id: Some(ItemStackEscrowId {
            value: row.source_item_stack_escrow_id.to_string(),
        }),
        destination_item_stack_id: row.destination_item_stack_id.map(|id| ItemStackId {
            value: id.to_string(),
        }),
        quantity: Some(ItemQuantity {
            units: row.quantity,
        }),
        unit_price_isk: Some(IskAmount {
            minor_units: row.unit_price_minor,
        }),
        total_price_isk: Some(IskAmount {
            minor_units: row.total_price_minor,
        }),
        created_at_unix_millis: millis(row.created_at),
        updated_at_unix_millis: millis(row.updated_at),
        completed_at_unix_millis: option_millis(row.completed_at),
    }
}

pub(crate) fn settlement_step_proto(row: &SettlementStepRow, phase: i32) -> SettlementStep {
    SettlementStep {
        settlement_step_id: Some(SettlementStepId {
            value: row.settlement_step_id.to_string(),
        }),
        settlement_id: some_settlement_id(row.settlement_id),
        step_name: row.step_name.clone(),
        step_phase: phase,
        step_state: settlement_state_i32(&row.step_state),
        started_at_unix_millis: millis(row.started_at),
        completed_at_unix_millis: option_millis(row.completed_at),
        failure_code: row.failure_code.clone().unwrap_or_default(),
        failure_message: row.failure_message.clone().unwrap_or_default(),
    }
}

pub(crate) fn trade_claim_proto(row: &TradeClaimRow) -> TradeClaim {
    TradeClaim {
        trade_claim_id: Some(TradeClaimId {
            value: row.trade_claim_id.to_string(),
        }),
        operation_id: Some(OperationId {
            value: row.operation_id.to_string(),
        }),
        trade_transaction_id: some_trade_transaction_id(row.trade_transaction_id),
        settlement_id: some_settlement_id(row.settlement_id),
        claiming_capsuleer_id: Some(CapsuleerId {
            value: row.claiming_capsuleer_id,
        }),
        claim_state: claim_state_i32(&row.claim_state),
        created_at_unix_millis: millis(row.created_at),
        claimed_at_unix_millis: option_millis(row.claimed_at),
    }
}

pub(crate) fn trade_claim_isk_proto(row: &TradeClaimIskRow) -> TradeClaimIsk {
    TradeClaimIsk {
        trade_claim_isk_id: Some(TradeClaimIskId {
            value: row.trade_claim_isk_id.to_string(),
        }),
        trade_claim_id: Some(TradeClaimId {
            value: row.trade_claim_id.to_string(),
        }),
        wallet_id: Some(WalletId {
            value: row.wallet_id.to_string(),
        }),
        amount_isk: Some(IskAmount {
            minor_units: row.amount_minor,
        }),
    }
}

pub(crate) fn trade_claim_item_stack_proto(row: &TradeClaimItemStackRow) -> TradeClaimItemStack {
    TradeClaimItemStack {
        trade_claim_item_stack_id: Some(TradeClaimItemStackId {
            value: row.trade_claim_item_stack_id.to_string(),
        }),
        trade_claim_id: Some(TradeClaimId {
            value: row.trade_claim_id.to_string(),
        }),
        item_type_id: Some(ItemTypeId {
            value: row.item_type_id,
        }),
        item_stack_id: Some(ItemStackId {
            value: row.item_stack_id.to_string(),
        }),
        quantity: Some(ItemQuantity {
            units: row.quantity,
        }),
    }
}

pub(crate) fn trade_state_i32(value: &str) -> i32 {
    match value {
        "outstanding" => TRADE_STATE_OUTSTANDING,
        "completed" => TRADE_STATE_COMPLETED,
        "failed" => TRADE_STATE_FAILED,
        "expired" => TRADE_STATE_EXPIRED,
        "cancelled" => TRADE_STATE_CANCELLED,
        _ => 0,
    }
}

pub(crate) fn trade_transaction_state_i32(value: &str) -> i32 {
    match value {
        "completed" => TRANSACTION_STATE_COMPLETED,
        "expired" => TRANSACTION_STATE_EXPIRED,
        _ => 0,
    }
}

pub(crate) fn escrow_state_i32(value: &str) -> i32 {
    match value {
        "held" => ESCROW_STATE_HELD,
        "partially_used" => ESCROW_STATE_PARTIALLY_USED,
        "used" => ESCROW_STATE_USED,
        "released" => ESCROW_STATE_RELEASED,
        "cancelled" => ESCROW_STATE_CANCELLED,
        "expired" => ESCROW_STATE_EXPIRED,
        _ => 0,
    }
}

pub(crate) fn settlement_state_i32(value: &str) -> i32 {
    match value {
        "completed" => SETTLEMENT_STATE_COMPLETED,
        "idempotent_replay" => SETTLEMENT_STATE_IDEMPOTENT_REPLAY,
        _ => SETTLEMENT_STATE_COMPLETED,
    }
}

pub(crate) fn settlement_phase_for_step(value: &str) -> i32 {
    match value {
        "validating_metadata" => SETTLEMENT_PHASE_VALIDATING_METADATA,
        "locking_rows" => SETTLEMENT_PHASE_LOCKING_ROWS,
        "applying_ownership" => SETTLEMENT_PHASE_APPLYING_OWNERSHIP,
        "writing_audit" => SETTLEMENT_PHASE_WRITING_AUDIT,
        "completed" => SETTLEMENT_PHASE_COMPLETED,
        _ => 0,
    }
}

pub(crate) fn claim_state_i32(value: &str) -> i32 {
    match value {
        "created" => CLAIM_STATE_CREATED,
        _ => 0,
    }
}

pub(crate) fn some_trade_instance_id(id: Uuid) -> Option<TradeInstanceId> {
    Some(TradeInstanceId {
        value: id.to_string(),
    })
}

pub(crate) fn some_trade_transaction_id(id: Uuid) -> Option<TradeTransactionId> {
    Some(TradeTransactionId {
        value: id.to_string(),
    })
}

pub(crate) fn some_settlement_id(id: Uuid) -> Option<SettlementId> {
    Some(SettlementId {
        value: id.to_string(),
    })
}
