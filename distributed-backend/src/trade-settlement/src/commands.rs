use chrono::{DateTime, Utc};
use prost_types::Timestamp;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::{Result, SettlementError};
use crate::proto::trade_settlement as pb;
use pb::settlement_operation::Operation as ProtoOperation;
use pb::SettlementOperationKind;

const TRADE_KIND_SELL: &str = "SELL";
const TRADE_STATE_OPEN: &str = "OPEN";
const TRADE_STATE_CANCELLED: &str = "CANCELLED";
const TRADE_STATE_COMPLETED: &str = "COMPLETED";
const TRADE_STATE_CHANGE_CANCELLED: &str = "CANCELLED_BY_ISSUER";
const TRADE_STATE_CHANGE_ACCEPTED: &str = "ACCEPTED_BY_BUYER";

#[derive(Debug, Clone)]
pub struct ExecuteBatchCommand {
    pub idempotency_key: String,
    pub request_fingerprint: Option<String>,
    pub external_request_id: Option<String>,
    pub caused_by_capsuleer_id: Option<i64>,
    pub operations: Vec<SettlementCommand>,
    pub created_by_service: String,
    pub request_id: Option<Uuid>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", content = "payload", rename_all = "snake_case")]
pub enum SettlementCommand {
    CreateNewTradeInstanceRow(CreateNewTradeInstanceRow),
    ModifyTradeInstanceState(ModifyTradeInstanceState),
    CreateNewEmptyItemStack(CreateNewEmptyItemStack),
    TransferQuantityFromItemStackToItemStackEscrow(TransferQuantityFromItemStackToItemStackEscrow),
    TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(
        TransferQuantityFromItemStackEscrowToItemStackWithNewOwner,
    ),
    TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner(
        TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner,
    ),
    MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner(
        MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner,
    ),
    CreateNewEmptyWalletEscrow(CreateNewEmptyWalletEscrow),
    TransferIskAmountFromWalletToWalletEscrow(TransferIskAmountFromWalletToWalletEscrow),
    TransferIskAmountFromWalletEscrowToWalletWithNewOwner(
        TransferIskAmountFromWalletEscrowToWalletWithNewOwner,
    ),
    TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner(
        TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner,
    ),
}

impl SettlementCommand {
    pub fn kind_name(&self) -> &'static str {
        match self {
            SettlementCommand::CreateNewTradeInstanceRow(_) => "create_new_trade_instance_row",
            SettlementCommand::ModifyTradeInstanceState(_) => "modify_trade_instance_state",
            SettlementCommand::CreateNewEmptyItemStack(_) => "create_new_empty_item_stack",
            SettlementCommand::TransferQuantityFromItemStackToItemStackEscrow(_) => {
                "transfer_quantity_from_item_stack_to_item_stack_escrow"
            }
            SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(_) => {
                "transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner"
            }
            SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner(
                _,
            ) => "transfer_quantity_from_item_stack_escrow_to_item_stack_with_previous_owner",
            SettlementCommand::MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner(_) => {
                "merge_item_stacks_with_identical_item_type_and_identical_owner"
            }
            SettlementCommand::CreateNewEmptyWalletEscrow(_) => "create_new_empty_wallet_escrow",
            SettlementCommand::TransferIskAmountFromWalletToWalletEscrow(_) => {
                "transfer_isk_amount_from_wallet_to_wallet_escrow"
            }
            SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithNewOwner(_) => {
                "transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner"
            }
            SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner(_) => {
                "transfer_isk_amount_from_wallet_escrow_to_wallet_with_previous_owner"
            }
        }
    }

    pub fn proto_kind(&self) -> SettlementOperationKind {
        match self {
            SettlementCommand::CreateNewTradeInstanceRow(_) => {
                SettlementOperationKind::CreateNewTradeInstanceRow
            }
            SettlementCommand::ModifyTradeInstanceState(_) => {
                SettlementOperationKind::ModifyTradeInstanceState
            }
            SettlementCommand::CreateNewEmptyItemStack(_) => {
                SettlementOperationKind::CreateNewEmptyItemStack
            }
            SettlementCommand::TransferQuantityFromItemStackToItemStackEscrow(_) => {
                SettlementOperationKind::TransferQuantityFromItemStackToItemStackEscrow
            }
            SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(_) => {
                SettlementOperationKind::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner
            }
            SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner(_) => {
                SettlementOperationKind::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner
            }
            SettlementCommand::MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner(_) => {
                SettlementOperationKind::MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner
            }
            SettlementCommand::CreateNewEmptyWalletEscrow(_) => {
                SettlementOperationKind::CreateNewEmptyWalletEscrow
            }
            SettlementCommand::TransferIskAmountFromWalletToWalletEscrow(_) => {
                SettlementOperationKind::TransferIskAmountFromWalletToWalletEscrow
            }
            SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithNewOwner(_) => {
                SettlementOperationKind::TransferIskAmountFromWalletEscrowToWalletWithNewOwner
            }
            SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner(_) => {
                SettlementOperationKind::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner
            }
        }
    }

    pub fn validate(&self) -> Result<()> {
        match self {
            SettlementCommand::CreateNewTradeInstanceRow(command) => {
                ensure_supported("trade_kind", &command.trade_kind, &[TRADE_KIND_SELL])?;
                ensure_supported("trade_state", &command.trade_state, &[TRADE_STATE_OPEN])?;
                ensure_positive(command.issuer_id, "issuer_id")?;
                ensure_positive(command.item_type_id, "item_type_id")?;
                ensure_positive(command.station_id, "station_id")?;
                ensure_positive(command.total_quantity, "total_quantity")?;
                ensure_positive(command.unit_price_isk, "unit_price_isk")?;
                ensure_future_timestamp(command.expires_at.as_ref(), "expires_at")?;
            }
            SettlementCommand::ModifyTradeInstanceState(command) => {
                ensure_supported(
                    "to_trade_state",
                    &command.to_trade_state,
                    &[
                        TRADE_STATE_OPEN,
                        TRADE_STATE_CANCELLED,
                        TRADE_STATE_COMPLETED,
                    ],
                )?;
                ensure_supported(
                    "trade_state_change_kind",
                    &command.trade_state_change_kind,
                    &[TRADE_STATE_CHANGE_CANCELLED, TRADE_STATE_CHANGE_ACCEPTED],
                )?;
            }
            SettlementCommand::CreateNewEmptyItemStack(command) => {
                ensure_positive(command.owner_id, "owner_id")?;
                ensure_positive(command.item_type_id, "item_type_id")?;
                ensure_positive(command.station_id, "station_id")?;
            }
            SettlementCommand::TransferQuantityFromItemStackToItemStackEscrow(command) => {
                ensure_positive(command.quantity, "quantity")?;
            }
            SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(
                command,
            ) => {
                ensure_positive(command.quantity, "quantity")?;
            }
            SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner(
                command,
            ) => {
                ensure_positive(command.quantity, "quantity")?;
            }
            SettlementCommand::MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner(command) => {
                if command.source_item_stack_id == command.destination_item_stack_id {
                    return Err(SettlementError::InvalidArgument(
                        "source_item_stack_id and destination_item_stack_id must differ"
                            .to_string(),
                    ));
                }
            }
            SettlementCommand::CreateNewEmptyWalletEscrow(command) => {
                ensure_positive(command.owner_id, "owner_id")?;
            }
            SettlementCommand::TransferIskAmountFromWalletToWalletEscrow(command) => {
                ensure_positive(command.isk_amount, "isk_amount")?;
            }
            SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithNewOwner(command) => {
                ensure_positive(command.isk_amount, "isk_amount")?;
            }
            SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner(
                command,
            ) => {
                ensure_positive(command.isk_amount, "isk_amount")?;
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateNewTradeInstanceRow {
    pub trade_instance_id: Option<Uuid>,
    pub trade_kind: String,
    pub trade_state: String,
    pub issuer_id: i64,
    pub item_type_id: i64,
    pub station_id: i64,
    pub total_quantity: i64,
    pub unit_price_isk: i64,
    pub expires_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModifyTradeInstanceState {
    pub trade_instance_id: Uuid,
    pub to_trade_state: String,
    pub trade_state_change_kind: String,
    pub changed_by_service: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateNewEmptyItemStack {
    pub item_stack_id: Option<Uuid>,
    pub owner_id: i64,
    pub item_type_id: i64,
    pub station_id: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransferQuantityFromItemStackToItemStackEscrow {
    pub source_item_stack_id: Uuid,
    pub item_stack_escrow_id: Option<Uuid>,
    pub trade_instance_id: Uuid,
    pub quantity: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransferQuantityFromItemStackEscrowToItemStackWithNewOwner {
    pub item_stack_escrow_id: Uuid,
    pub destination_item_stack_id: Uuid,
    pub quantity: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner {
    pub item_stack_escrow_id: Uuid,
    pub destination_item_stack_id: Uuid,
    pub quantity: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner {
    pub source_item_stack_id: Uuid,
    pub destination_item_stack_id: Uuid,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateNewEmptyWalletEscrow {
    pub wallet_escrow_id: Option<Uuid>,
    pub trade_instance_id: Uuid,
    pub owner_id: i64,
    pub source_wallet_id: Uuid,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransferIskAmountFromWalletToWalletEscrow {
    pub source_wallet_id: Uuid,
    pub wallet_escrow_id: Option<Uuid>,
    pub trade_instance_id: Uuid,
    pub isk_amount: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransferIskAmountFromWalletEscrowToWalletWithNewOwner {
    pub wallet_escrow_id: Uuid,
    pub destination_wallet_id: Uuid,
    pub isk_amount: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner {
    pub wallet_escrow_id: Uuid,
    pub destination_wallet_id: Uuid,
    pub isk_amount: i64,
}

impl TryFrom<pb::ExecuteSettlementBatchRequest> for ExecuteBatchCommand {
    type Error = SettlementError;

    fn try_from(value: pb::ExecuteSettlementBatchRequest) -> Result<Self> {
        let idempotency_key = required_string(value.idempotency_key, "idempotency_key")?;
        let created_by_service = non_empty_or(value.created_by_service, "market");
        let request_id = optional_uuid(value.request_id, "request_id")?;

        let operations = value
            .operations
            .into_iter()
            .map(SettlementCommand::try_from)
            .collect::<Result<Vec<_>>>()?;
        for operation in &operations {
            operation.validate()?;
        }

        Ok(Self {
            idempotency_key,
            request_fingerprint: optional_string(value.request_fingerprint),
            external_request_id: optional_string(value.external_request_id),
            caused_by_capsuleer_id: value.caused_by_capsuleer_id,
            operations,
            created_by_service,
            request_id,
        })
    }
}

impl TryFrom<pb::SettlementOperation> for SettlementCommand {
    type Error = SettlementError;

    fn try_from(value: pb::SettlementOperation) -> Result<Self> {
        let operation = value.operation.ok_or_else(|| {
            SettlementError::InvalidArgument("settlement operation is missing".to_string())
        })?;

        match operation {
            ProtoOperation::CreateNewTradeInstanceRow(value) => Ok(
                SettlementCommand::CreateNewTradeInstanceRow(CreateNewTradeInstanceRow {
                    trade_instance_id: optional_uuid(value.trade_instance_id, "trade_instance_id")?,
                    trade_kind: required_string(value.trade_kind, "trade_kind")?,
                    trade_state: non_empty_or(value.trade_state, "OPEN"),
                    issuer_id: value.issuer_id,
                    item_type_id: value.item_type_id,
                    station_id: value.station_id,
                    total_quantity: value.total_quantity,
                    unit_price_isk: value.unit_price_isk,
                    expires_at: optional_timestamp(value.expires_at)?,
                }),
            ),
            ProtoOperation::ModifyTradeInstanceState(value) => Ok(
                SettlementCommand::ModifyTradeInstanceState(ModifyTradeInstanceState {
                    trade_instance_id: required_uuid(value.trade_instance_id, "trade_instance_id")?,
                    to_trade_state: required_string(value.to_trade_state, "to_trade_state")?,
                    trade_state_change_kind: required_string(
                        value.trade_state_change_kind,
                        "trade_state_change_kind",
                    )?,
                    changed_by_service: non_empty_or(value.changed_by_service, "market"),
                }),
            ),
            ProtoOperation::CreateNewEmptyItemStack(value) => Ok(
                SettlementCommand::CreateNewEmptyItemStack(CreateNewEmptyItemStack {
                    item_stack_id: optional_uuid(value.item_stack_id, "item_stack_id")?,
                    owner_id: value.owner_id,
                    item_type_id: value.item_type_id,
                    station_id: value.station_id,
                }),
            ),
            ProtoOperation::TransferQuantityFromItemStackToItemStackEscrow(value) => Ok(
                SettlementCommand::TransferQuantityFromItemStackToItemStackEscrow(
                    TransferQuantityFromItemStackToItemStackEscrow {
                        source_item_stack_id: required_uuid(
                            value.source_item_stack_id,
                            "source_item_stack_id",
                        )?,
                        item_stack_escrow_id: optional_uuid(
                            value.item_stack_escrow_id,
                            "item_stack_escrow_id",
                        )?,
                        trade_instance_id: required_uuid(
                            value.trade_instance_id,
                            "trade_instance_id",
                        )?,
                        quantity: value.quantity,
                    },
                ),
            ),
            ProtoOperation::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(value) => {
                Ok(
                    SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(
                        TransferQuantityFromItemStackEscrowToItemStackWithNewOwner {
                            item_stack_escrow_id: required_uuid(
                                value.item_stack_escrow_id,
                                "item_stack_escrow_id",
                            )?,
                            destination_item_stack_id: required_uuid(
                                value.destination_item_stack_id,
                                "destination_item_stack_id",
                            )?,
                            quantity: value.quantity,
                        },
                    ),
                )
            }
            ProtoOperation::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner(
                value,
            ) => Ok(
                SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner(
                    TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner {
                        item_stack_escrow_id: required_uuid(
                            value.item_stack_escrow_id,
                            "item_stack_escrow_id",
                        )?,
                        destination_item_stack_id: required_uuid(
                            value.destination_item_stack_id,
                            "destination_item_stack_id",
                        )?,
                        quantity: value.quantity,
                    },
                ),
            ),
            ProtoOperation::MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner(value) => Ok(
                SettlementCommand::MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner(
                    MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner {
                        source_item_stack_id: required_uuid(
                            value.source_item_stack_id,
                            "source_item_stack_id",
                        )?,
                        destination_item_stack_id: required_uuid(
                            value.destination_item_stack_id,
                            "destination_item_stack_id",
                        )?,
                    },
                ),
            ),
            ProtoOperation::CreateNewEmptyWalletEscrow(value) => Ok(
                SettlementCommand::CreateNewEmptyWalletEscrow(CreateNewEmptyWalletEscrow {
                    wallet_escrow_id: optional_uuid(value.wallet_escrow_id, "wallet_escrow_id")?,
                    trade_instance_id: required_uuid(value.trade_instance_id, "trade_instance_id")?,
                    owner_id: value.owner_id,
                    source_wallet_id: required_uuid(value.source_wallet_id, "source_wallet_id")?,
                }),
            ),
            ProtoOperation::TransferIskAmountFromWalletToWalletEscrow(value) => Ok(
                SettlementCommand::TransferIskAmountFromWalletToWalletEscrow(
                    TransferIskAmountFromWalletToWalletEscrow {
                        source_wallet_id: required_uuid(
                            value.source_wallet_id,
                            "source_wallet_id",
                        )?,
                        wallet_escrow_id: optional_uuid(
                            value.wallet_escrow_id,
                            "wallet_escrow_id",
                        )?,
                        trade_instance_id: required_uuid(
                            value.trade_instance_id,
                            "trade_instance_id",
                        )?,
                        isk_amount: value.isk_amount,
                    },
                ),
            ),
            ProtoOperation::TransferIskAmountFromWalletEscrowToWalletWithNewOwner(value) => Ok(
                SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithNewOwner(
                    TransferIskAmountFromWalletEscrowToWalletWithNewOwner {
                        wallet_escrow_id: required_uuid(
                            value.wallet_escrow_id,
                            "wallet_escrow_id",
                        )?,
                        destination_wallet_id: required_uuid(
                            value.destination_wallet_id,
                            "destination_wallet_id",
                        )?,
                        isk_amount: value.isk_amount,
                    },
                ),
            ),
            ProtoOperation::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner(value) => {
                Ok(
                    SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner(
                        TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner {
                            wallet_escrow_id: required_uuid(
                                value.wallet_escrow_id,
                                "wallet_escrow_id",
                            )?,
                            destination_wallet_id: required_uuid(
                                value.destination_wallet_id,
                                "destination_wallet_id",
                            )?,
                            isk_amount: value.isk_amount,
                        },
                    ),
                )
            }
        }
    }
}

fn optional_timestamp(value: Option<Timestamp>) -> Result<Option<DateTime<Utc>>> {
    value
        .map(|timestamp| {
            DateTime::<Utc>::from_timestamp(timestamp.seconds, timestamp.nanos as u32).ok_or_else(
                || SettlementError::InvalidArgument("expires_at is invalid".to_string()),
            )
        })
        .transpose()
}

pub fn required_uuid(value: String, field_name: &str) -> Result<Uuid> {
    let value = required_string(value, field_name)?;
    Uuid::parse_str(&value)
        .map_err(|_| SettlementError::InvalidArgument(format!("{field_name} must be a valid UUID")))
}

pub fn optional_uuid(value: String, field_name: &str) -> Result<Option<Uuid>> {
    if value.trim().is_empty() {
        Ok(None)
    } else {
        required_uuid(value, field_name).map(Some)
    }
}

fn required_string(value: String, field_name: &str) -> Result<String> {
    let value = value.trim().to_string();
    if value.is_empty() {
        Err(SettlementError::InvalidArgument(format!(
            "{field_name} is required"
        )))
    } else {
        Ok(value)
    }
}

fn optional_string(value: String) -> Option<String> {
    let value = value.trim().to_string();
    (!value.is_empty()).then_some(value)
}

fn non_empty_or(value: String, fallback: &str) -> String {
    let value = value.trim().to_string();
    if value.is_empty() {
        fallback.to_string()
    } else {
        value
    }
}

fn ensure_positive(value: i64, field_name: &str) -> Result<()> {
    if value > 0 {
        Ok(())
    } else {
        Err(SettlementError::InvalidArgument(format!(
            "{field_name} must be greater than zero"
        )))
    }
}

fn ensure_supported(field_name: &str, value: &str, supported: &[&str]) -> Result<()> {
    if supported.contains(&value) {
        Ok(())
    } else {
        Err(SettlementError::InvalidArgument(format!(
            "{field_name} {value} is not supported"
        )))
    }
}

fn ensure_future_timestamp(value: Option<&DateTime<Utc>>, field_name: &str) -> Result<()> {
    if let Some(value) = value {
        if *value <= Utc::now() {
            return Err(SettlementError::InvalidArgument(format!(
                "{field_name} must be in the future"
            )));
        }
    }
    Ok(())
}
