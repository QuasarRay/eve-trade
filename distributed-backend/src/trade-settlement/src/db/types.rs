use chrono::{DateTime, Utc};
use sqlx::{Postgres, Transaction};
use uuid::Uuid;

use crate::generated::eve_trade::{
    common::v1::OperationMetadata, settlement::v1::TradeSettlementResult,
};

pub(crate) const SERVICE_NAME: &str = "trade-settlement";

pub(crate) const OP_ISSUE: i32 = 1;
pub(crate) const OP_CANCEL: i32 = 3;
pub(crate) const OP_EXPIRE: i32 = 4;
pub(crate) const OP_SETTLE: i32 = 5;

pub(crate) const ATTEMPT_COMMITTED: i32 = 1;
pub(crate) const ATTEMPT_REJECTED: i32 = 2;
pub(crate) const ATTEMPT_RESULT_UNKNOWN: i32 = 4;
pub(crate) const ATTEMPT_IDEMPOTENT_REPLAY: i32 = 5;

pub(crate) const TRADE_STATE_OUTSTANDING: i32 = 1;
pub(crate) const TRADE_STATE_COMPLETED: i32 = 2;
pub(crate) const TRADE_STATE_FAILED: i32 = 3;
pub(crate) const TRADE_STATE_EXPIRED: i32 = 4;
pub(crate) const TRADE_STATE_CANCELLED: i32 = 5;

pub(crate) const TRANSACTION_STATE_COMPLETED: i32 = 2;
pub(crate) const TRANSACTION_STATE_EXPIRED: i32 = 4;

pub(crate) const ESCROW_STATE_HELD: i32 = 1;
pub(crate) const ESCROW_STATE_PARTIALLY_USED: i32 = 2;
pub(crate) const ESCROW_STATE_USED: i32 = 3;
pub(crate) const ESCROW_STATE_RELEASED: i32 = 4;
pub(crate) const ESCROW_STATE_CANCELLED: i32 = 5;
pub(crate) const ESCROW_STATE_EXPIRED: i32 = 6;

pub(crate) const CLAIM_STATE_CREATED: i32 = 1;

pub(crate) const SETTLEMENT_STATE_COMPLETED: i32 = 2;
pub(crate) const SETTLEMENT_STATE_IDEMPOTENT_REPLAY: i32 = 6;

pub(crate) const SETTLEMENT_PHASE_VALIDATING_METADATA: i32 = 2;
pub(crate) const SETTLEMENT_PHASE_LOCKING_ROWS: i32 = 3;
pub(crate) const SETTLEMENT_PHASE_APPLYING_OWNERSHIP: i32 = 4;
pub(crate) const SETTLEMENT_PHASE_WRITING_AUDIT: i32 = 5;
pub(crate) const SETTLEMENT_PHASE_COMPLETED: i32 = 6;

pub(crate) type DbTx<'a> = Transaction<'a, Postgres>;

#[derive(Clone)]
pub(crate) struct CommandContext {
    pub(crate) metadata: OperationMetadata,
    pub(crate) operation_kind: i32,
    pub(crate) operation_name: &'static str,
    pub(crate) request_fingerprint: String,
    pub(crate) operation_id: Uuid,
    pub(crate) request_id: Uuid,
    pub(crate) idempotency_key: String,
    pub(crate) source_system: String,
    pub(crate) external_operation_id: Option<String>,
    pub(crate) caused_by_capsuleer_id: Option<i64>,
    pub(crate) created_by_service: String,
    pub(crate) requested_at: DateTime<Utc>,
}

#[derive(sqlx::FromRow)]
pub(crate) struct IdempotencyRecordRow {
    pub(crate) request_fingerprint: String,
    pub(crate) operation_name: String,
}

#[derive(sqlx::FromRow)]
pub(crate) struct IdempotencyResultRow {
    pub(crate) result_kind: String,
    pub(crate) trade_instance_id: Option<Uuid>,
    pub(crate) trade_transaction_id: Option<Uuid>,
    pub(crate) settlement_id: Option<Uuid>,
    pub(crate) result_state: String,
}

#[derive(Clone, Debug, sqlx::FromRow)]
pub(crate) struct TradeInstanceRow {
    pub(crate) trade_instance_id: Uuid,
    pub(crate) operation_id: Uuid,
    pub(crate) trade_state: String,
    pub(crate) issuer_id: i64,
    pub(crate) issuer_wallet_id: Uuid,
    pub(crate) item_type_id: i64,
    pub(crate) station_id: i64,
    pub(crate) region_id: i64,
    pub(crate) total_quantity: i64,
    pub(crate) remaining_quantity: i64,
    pub(crate) unit_price_minor: i64,
    pub(crate) expires_at: Option<DateTime<Utc>>,
    pub(crate) created_at: DateTime<Utc>,
    pub(crate) updated_at: DateTime<Utc>,
}

#[derive(Clone, Debug, sqlx::FromRow)]
pub(crate) struct ItemStackRow {
    pub(crate) item_stack_id: Uuid,
    pub(crate) owner_id: i64,
    pub(crate) item_type_id: i64,
    pub(crate) station_id: i64,
    pub(crate) region_id: i64,
    pub(crate) quantity: i64,
    pub(crate) stack_state: String,
    pub(crate) stack_version: i64,
    pub(crate) stack_checksum: String,
}

#[derive(Clone, Debug, sqlx::FromRow)]
pub(crate) struct ItemStackEscrowRow {
    pub(crate) item_stack_escrow_id: Uuid,
    pub(crate) issuer_id: i64,
    pub(crate) trade_instance_id: Uuid,
    pub(crate) quantity: i64,
    pub(crate) created_at: DateTime<Utc>,
    pub(crate) updated_at: DateTime<Utc>,
    pub(crate) released_at: Option<DateTime<Utc>>,
    pub(crate) escrow_state: String,
    pub(crate) release_reason: Option<String>,
    pub(crate) source_item_stack_id: Uuid,
}

#[derive(Clone, Debug, sqlx::FromRow)]
pub(crate) struct WalletRow {
    pub(crate) wallet_id: Uuid,
    pub(crate) capsuleer_id: i64,
    pub(crate) isk_minor: i64,
    pub(crate) wallet_state: String,
    pub(crate) wallet_version: i64,
    pub(crate) wallet_checksum: String,
}

#[derive(Clone, Debug, sqlx::FromRow)]
pub(crate) struct WalletEscrowRow {
    pub(crate) wallet_escrow_id: Uuid,
    pub(crate) trade_instance_id: Uuid,
    pub(crate) isk_minor: i64,
    pub(crate) owner_id: i64,
    pub(crate) created_wallet_operation_id: Uuid,
    pub(crate) released_wallet_operation_id: Option<Uuid>,
    pub(crate) created_at: DateTime<Utc>,
    pub(crate) updated_at: DateTime<Utc>,
    pub(crate) released_at: Option<DateTime<Utc>>,
}

#[derive(Clone, Debug)]
pub(crate) struct CreateNewTradeInstanceRowInput {
    pub(crate) trade_instance_id: Uuid,
    pub(crate) operation_id: Uuid,
    pub(crate) trade_state: String,
    pub(crate) issuer_id: i64,
    pub(crate) issuer_wallet_id: Uuid,
    pub(crate) item_type_id: i64,
    pub(crate) station_id: i64,
    pub(crate) region_id: i64,
    pub(crate) total_quantity: i64,
    pub(crate) unit_price_minor: i64,
    pub(crate) expires_at: Option<DateTime<Utc>>,
    pub(crate) created_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct ModifyTradeInstanceStateInput {
    pub(crate) trade_instance_id: Uuid,
    pub(crate) expected_trade_state: Option<String>,
    pub(crate) new_trade_state: String,
    pub(crate) remaining_quantity: Option<i64>,
    pub(crate) updated_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct CreateNewEmptyItemStackInput {
    pub(crate) item_stack_id: Uuid,
    pub(crate) owner_id: i64,
    pub(crate) item_type_id: i64,
    pub(crate) station_id: i64,
    pub(crate) created_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct TransferQuantityFromItemStackToItemStackEscrowInput {
    pub(crate) operation_id: Uuid,
    pub(crate) operation_kind: String,
    pub(crate) item_stack_operation_id: Uuid,
    pub(crate) source_item_stack_id: Uuid,
    pub(crate) item_stack_escrow_id: Uuid,
    pub(crate) trade_instance_id: Uuid,
    pub(crate) issuer_id: i64,
    pub(crate) quantity: i64,
    pub(crate) created_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct TransferQuantityFromItemStackEscrowToItemStackWithNewOwnerInput {
    pub(crate) operation_id: Uuid,
    pub(crate) operation_kind: String,
    pub(crate) item_stack_operation_id: Uuid,
    pub(crate) item_stack_escrow_id: Uuid,
    pub(crate) destination_item_stack_id: Uuid,
    pub(crate) new_owner_id: i64,
    pub(crate) quantity: i64,
    pub(crate) transferred_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwnerInput {
    pub(crate) operation_id: Uuid,
    pub(crate) operation_kind: String,
    pub(crate) item_stack_operation_id: Uuid,
    pub(crate) item_stack_escrow_id: Uuid,
    pub(crate) quantity: i64,
    pub(crate) release_reason: String,
    pub(crate) transferred_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct MergeItemStacksWithIdenticalItemTypeAndIdenticalOwnerInput {
    pub(crate) operation_id: Uuid,
    pub(crate) operation_kind: String,
    pub(crate) item_stack_operation_id: Uuid,
    pub(crate) source_item_stack_id: Uuid,
    pub(crate) target_item_stack_id: Uuid,
    pub(crate) merged_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct CreateNewEmptyWalletEscrowInput {
    pub(crate) operation_id: Uuid,
    pub(crate) operation_kind: String,
    pub(crate) wallet_operation_id: Uuid,
    pub(crate) wallet_escrow_id: Uuid,
    pub(crate) trade_instance_id: Uuid,
    pub(crate) owner_id: i64,
    pub(crate) created_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct TransferIskAmountFromWalletToWalletEscrowInput {
    pub(crate) operation_id: Uuid,
    pub(crate) operation_kind: String,
    pub(crate) wallet_operation_id: Uuid,
    pub(crate) source_wallet_id: Uuid,
    pub(crate) wallet_escrow_id: Uuid,
    pub(crate) isk_minor: i64,
    pub(crate) transferred_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct TransferIskAmountFromWalletEscrowToWalletWithNewOwnerInput {
    pub(crate) operation_id: Uuid,
    pub(crate) operation_kind: String,
    pub(crate) wallet_operation_id: Uuid,
    pub(crate) wallet_escrow_id: Uuid,
    pub(crate) destination_wallet_id: Uuid,
    pub(crate) new_owner_id: i64,
    pub(crate) isk_minor: i64,
    pub(crate) transferred_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct TransferIskAmountFromWalletEscrowToWalletWithPreviousOwnerInput {
    pub(crate) operation_id: Uuid,
    pub(crate) operation_kind: String,
    pub(crate) wallet_operation_id: Uuid,
    pub(crate) wallet_escrow_id: Uuid,
    pub(crate) destination_wallet_id: Uuid,
    pub(crate) isk_minor: i64,
    pub(crate) transferred_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub(crate) struct ItemStackEscrowTransferResult {
    pub(crate) item_stack_operation_id: Uuid,
    pub(crate) item_stack: ItemStackRow,
    pub(crate) item_stack_escrow: ItemStackEscrowRow,
}

#[derive(Clone, Debug)]
pub(crate) struct ItemStackMergeResult {
    pub(crate) item_stack_operation_id: Uuid,
    pub(crate) source_item_stack: ItemStackRow,
    pub(crate) target_item_stack: ItemStackRow,
}

#[derive(Clone, Debug)]
pub(crate) struct WalletEscrowTransferResult {
    pub(crate) wallet_operation_id: Uuid,
    pub(crate) wallet: WalletRow,
    pub(crate) wallet_escrow: WalletEscrowRow,
}

#[derive(Clone, Debug, sqlx::FromRow)]
pub(crate) struct TradeTransactionRow {
    pub(crate) trade_transaction_id: Uuid,
    pub(crate) operation_id: Uuid,
    pub(crate) trade_instance_id: Uuid,
    pub(crate) trade_transaction_state: String,
    pub(crate) buyer_capsuleer_id: i64,
    pub(crate) buyer_wallet_id: Uuid,
    pub(crate) seller_capsuleer_id: i64,
    pub(crate) seller_wallet_id: Uuid,
    pub(crate) item_type_id: i64,
    pub(crate) source_item_stack_escrow_id: Uuid,
    pub(crate) destination_item_stack_id: Option<Uuid>,
    pub(crate) quantity: i64,
    pub(crate) unit_price_minor: i64,
    pub(crate) total_price_minor: i64,
    pub(crate) created_at: DateTime<Utc>,
    pub(crate) updated_at: DateTime<Utc>,
    pub(crate) completed_at: Option<DateTime<Utc>>,
}

#[derive(Clone, Debug, sqlx::FromRow)]
pub(crate) struct SettlementStepRow {
    pub(crate) settlement_step_id: Uuid,
    pub(crate) settlement_id: Uuid,
    pub(crate) step_name: String,
    pub(crate) step_state: String,
    pub(crate) started_at: DateTime<Utc>,
    pub(crate) completed_at: Option<DateTime<Utc>>,
    pub(crate) failure_code: Option<String>,
    pub(crate) failure_message: Option<String>,
}

#[derive(Clone, Debug, sqlx::FromRow)]
pub(crate) struct TradeClaimRow {
    pub(crate) trade_claim_id: Uuid,
    pub(crate) operation_id: Uuid,
    pub(crate) trade_transaction_id: Uuid,
    pub(crate) settlement_id: Uuid,
    pub(crate) claiming_capsuleer_id: i64,
    pub(crate) claim_state: String,
    pub(crate) created_at: DateTime<Utc>,
    pub(crate) claimed_at: Option<DateTime<Utc>>,
}

#[derive(Clone, Debug, sqlx::FromRow)]
pub(crate) struct TradeClaimIskRow {
    pub(crate) trade_claim_isk_id: Uuid,
    pub(crate) trade_claim_id: Uuid,
    pub(crate) wallet_id: Uuid,
    pub(crate) amount_minor: i64,
}

#[derive(Clone, Debug, sqlx::FromRow)]
pub(crate) struct TradeClaimItemStackRow {
    pub(crate) trade_claim_item_stack_id: Uuid,
    pub(crate) trade_claim_id: Uuid,
    pub(crate) item_type_id: i64,
    pub(crate) item_stack_id: Uuid,
    pub(crate) quantity: i64,
}

#[allow(clippy::large_enum_variant)]
pub(crate) enum BeginCommand {
    Started,
    Replay(TradeSettlementResult),
}

pub(crate) struct FinishIds {
    pub(crate) trade_instance_id: Option<Uuid>,
    pub(crate) trade_transaction_id: Option<Uuid>,
    pub(crate) settlement_id: Option<Uuid>,
    pub(crate) wallet_operation_id: Option<Uuid>,
    pub(crate) item_stack_operation_id: Option<Uuid>,
    pub(crate) result_kind: &'static str,
    pub(crate) result_state: &'static str,
}
