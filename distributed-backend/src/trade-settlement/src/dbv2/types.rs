use chrono::{DateTime, Utc};
use uuid::Uuid;

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
pub(crate) struct CreateNewEmptyWallerEscrowInput {
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
