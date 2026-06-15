#![allow(clippy::too_many_arguments)]

use chrono::{DateTime, Utc};
use prost::Message;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::error::SettlementError;
use crate::generated::eve_trade::{
    common::v1::{IskAmount, ItemQuantity, OperationMetadata},
    operation::v1::AcceptTradeInstanceCommand,
    settlement::v1::{trade_settlement_command, TradeSettlementCommand},
};

use super::types::*;

pub(crate) fn command_context(
    command: &TradeSettlementCommand,
    operation_kind: i32,
) -> Result<CommandContext, SettlementError> {
    let metadata = command
        .metadata
        .clone()
        .ok_or_else(|| SettlementError::InvalidRequest("metadata is required".to_string()))?;
    let operation_id = parse_uuid_message(
        metadata.operation_id.as_ref().map(|x| x.value.as_str()),
        "metadata.operation_id",
    )?;
    let request_id = parse_uuid_message(
        metadata.request_id.as_ref().map(|x| x.value.as_str()),
        "metadata.request_id",
    )?;
    let idempotency_key = required_text(
        metadata.idempotency_key.as_ref().map(|x| x.value.as_str()),
        "metadata.idempotency_key",
    )?;
    let source_system = metadata
        .source_system
        .as_ref()
        .map(|x| x.value.trim())
        .filter(|value| !value.is_empty())
        .unwrap_or("eve-trade")
        .to_string();
    let external_operation_id = metadata
        .external_operation_id
        .as_ref()
        .map(|x| x.value.trim())
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned);
    let caused_by_capsuleer_id = metadata.caused_by_capsuleer_id.as_ref().map(|x| x.value);
    let created_by_service = metadata
        .created_by_service
        .as_ref()
        .map(|x| x.value.trim())
        .filter(|value| !value.is_empty())
        .unwrap_or(SERVICE_NAME)
        .to_string();
    let requested_at =
        millis_to_datetime(metadata.requested_at_unix_millis).unwrap_or_else(Utc::now);

    Ok(CommandContext {
        metadata,
        operation_kind,
        operation_name: operation_name(operation_kind),
        request_fingerprint: request_fingerprint(command),
        operation_id,
        request_id,
        idempotency_key,
        source_system,
        external_operation_id,
        caused_by_capsuleer_id,
        created_by_service,
        requested_at,
    })
}

pub(crate) fn inferred_operation_kind(command: &trade_settlement_command::Command) -> i32 {
    match command {
        trade_settlement_command::Command::IssueTradeInstance(_) => OP_ISSUE,
        trade_settlement_command::Command::SettleTradeInstance(_) => OP_SETTLE,
        trade_settlement_command::Command::CancelTradeInstance(_) => OP_CANCEL,
        trade_settlement_command::Command::ExpireTradeInstance(_) => OP_EXPIRE,
    }
}

pub(crate) fn operation_name(operation_kind: i32) -> &'static str {
    match operation_kind {
        OP_ISSUE => "issue_trade_instance",
        OP_SETTLE => "settle_trade_instance",
        OP_CANCEL => "cancel_trade_instance",
        OP_EXPIRE => "expire_trade_instance",
        _ => "unknown_trade_operation",
    }
}

pub(crate) fn request_fingerprint(command: &TradeSettlementCommand) -> String {
    let bytes = command.encode_to_vec();
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

pub(crate) fn validate_nested_metadata(
    outer: &OperationMetadata,
    nested: Option<&OperationMetadata>,
    path: &str,
) -> Result<(), SettlementError> {
    let Some(nested) = nested else {
        return Ok(());
    };

    let outer_operation_id = outer.operation_id.as_ref().map(|x| x.value.as_str());
    let nested_operation_id = nested.operation_id.as_ref().map(|x| x.value.as_str());
    let outer_request_id = outer.request_id.as_ref().map(|x| x.value.as_str());
    let nested_request_id = nested.request_id.as_ref().map(|x| x.value.as_str());
    let outer_idempotency_key = outer.idempotency_key.as_ref().map(|x| x.value.as_str());
    let nested_idempotency_key = nested.idempotency_key.as_ref().map(|x| x.value.as_str());

    if nested_operation_id.is_some() && nested_operation_id != outer_operation_id
        || nested_request_id.is_some() && nested_request_id != outer_request_id
        || nested_idempotency_key.is_some() && nested_idempotency_key != outer_idempotency_key
    {
        return Err(SettlementError::InvalidRequest(format!(
            "{path}.metadata does not match envelope metadata"
        )));
    }

    Ok(())
}

pub(crate) fn validate_accept_matches_settle(
    accepted: &AcceptTradeInstanceCommand,
    trade_instance_id: Uuid,
    buyer_capsuleer_id: i64,
    buyer_wallet_id: Uuid,
    destination_item_stack_id: Uuid,
    quantity: i64,
    unit_price_minor: i64,
    total_price_minor: i64,
) -> Result<(), SettlementError> {
    let row_ids = accepted.row_ids.as_ref().ok_or_else(|| {
        SettlementError::InvalidRequest("accepted_trade.row_ids is required".to_string())
    })?;
    let terms = accepted.terms.as_ref().ok_or_else(|| {
        SettlementError::InvalidRequest("accepted_trade.terms is required".to_string())
    })?;

    let accepted_trade_id = parse_uuid_message(
        row_ids.trade_instance_id.as_ref().map(|x| x.value.as_str()),
        "accepted_trade.row_ids.trade_instance_id",
    )?;
    let accepted_buyer_id = required_positive_i64(
        row_ids.buyer_capsuleer_id.as_ref().map(|x| x.value),
        "accepted_trade.row_ids.buyer_capsuleer_id",
    )?;
    let accepted_wallet_id = parse_uuid_message(
        row_ids.buyer_wallet_id.as_ref().map(|x| x.value.as_str()),
        "accepted_trade.row_ids.buyer_wallet_id",
    )?;
    let accepted_stack_id = parse_uuid_message(
        row_ids
            .destination_item_stack_id
            .as_ref()
            .map(|x| x.value.as_str()),
        "accepted_trade.row_ids.destination_item_stack_id",
    )?;
    let accepted_quantity =
        required_quantity(terms.quantity.as_ref(), "accepted_trade.terms.quantity")?;
    let accepted_unit = required_money(
        terms.expected_unit_price_isk.as_ref(),
        "accepted_trade.terms.expected_unit_price_isk",
    )?;
    let accepted_total = required_money(
        terms.expected_total_price_isk.as_ref(),
        "accepted_trade.terms.expected_total_price_isk",
    )?;

    if accepted_trade_id != trade_instance_id
        || accepted_buyer_id != buyer_capsuleer_id
        || accepted_wallet_id != buyer_wallet_id
        || accepted_stack_id != destination_item_stack_id
        || accepted_quantity != quantity
        || accepted_unit != unit_price_minor
        || accepted_total != total_price_minor
    {
        return Err(SettlementError::InvalidRequest(
            "accepted_trade does not match settlement terms".to_string(),
        ));
    }

    Ok(())
}

pub(crate) fn validate_total_price(
    quantity: i64,
    unit_price_minor: i64,
    total_price_minor: i64,
) -> Result<(), SettlementError> {
    let expected = quantity
        .checked_mul(unit_price_minor)
        .ok_or_else(|| SettlementError::InvalidRequest("total price overflow".to_string()))?;
    if expected != total_price_minor {
        return Err(SettlementError::InvalidRequest(
            "total_price_isk must equal quantity * unit_price_isk".to_string(),
        ));
    }
    Ok(())
}

pub(crate) fn trade_is_expired(trade: &TradeInstanceRow, at: DateTime<Utc>) -> bool {
    trade.expires_at.is_some_and(|expires_at| at > expires_at)
}

pub(crate) fn parse_uuid_message(
    value: Option<&str>,
    field: &str,
) -> Result<Uuid, SettlementError> {
    let value = required_text(value, field)?;
    Uuid::parse_str(&value)
        .map_err(|_| SettlementError::InvalidRequest(format!("{field} must be a UUID")))
}

pub(crate) fn required_text(value: Option<&str>, field: &str) -> Result<String, SettlementError> {
    let value = value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| SettlementError::InvalidRequest(format!("{field} is required")))?;
    Ok(value.to_string())
}

pub(crate) fn required_positive_i64(
    value: Option<i64>,
    field: &str,
) -> Result<i64, SettlementError> {
    let value =
        value.ok_or_else(|| SettlementError::InvalidRequest(format!("{field} is required")))?;
    if value <= 0 {
        return Err(SettlementError::InvalidRequest(format!(
            "{field} must be positive"
        )));
    }
    Ok(value)
}

pub(crate) fn required_quantity(
    value: Option<&ItemQuantity>,
    field: &str,
) -> Result<i64, SettlementError> {
    let value = value
        .ok_or_else(|| SettlementError::InvalidRequest(format!("{field} is required")))?
        .units;
    if value <= 0 {
        return Err(SettlementError::InvalidRequest(format!(
            "{field} must be positive"
        )));
    }
    Ok(value)
}

pub(crate) fn required_money(
    value: Option<&IskAmount>,
    field: &str,
) -> Result<i64, SettlementError> {
    let value = value
        .ok_or_else(|| SettlementError::InvalidRequest(format!("{field} is required")))?
        .minor_units;
    if value < 0 {
        return Err(SettlementError::InvalidRequest(format!(
            "{field} must be non-negative"
        )));
    }
    Ok(value)
}

pub(crate) fn millis_to_datetime(millis: i64) -> Option<DateTime<Utc>> {
    if millis <= 0 {
        None
    } else {
        DateTime::<Utc>::from_timestamp_millis(millis)
    }
}

pub(crate) fn millis(value: DateTime<Utc>) -> i64 {
    value.timestamp_millis()
}

pub(crate) fn option_millis(value: Option<DateTime<Utc>>) -> i64 {
    value.map(millis).unwrap_or_default()
}

pub(crate) fn checksum(parts: &[String]) -> String {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update(part.as_bytes());
        hasher.update(b"\0");
    }
    format!("{:x}", hasher.finalize())
}

pub(crate) fn wallet_checksum(
    wallet_id: Uuid,
    capsuleer_id: i64,
    isk_minor: i64,
    version: i64,
) -> String {
    checksum(&[
        "wallet".to_string(),
        wallet_id.to_string(),
        capsuleer_id.to_string(),
        isk_minor.to_string(),
        version.to_string(),
    ])
}

pub(crate) fn item_stack_checksum(
    item_stack_id: Uuid,
    owner_id: i64,
    item_type_id: i64,
    station_id: i64,
    quantity: i64,
    version: i64,
) -> String {
    checksum(&[
        "item_stack".to_string(),
        item_stack_id.to_string(),
        owner_id.to_string(),
        item_type_id.to_string(),
        station_id.to_string(),
        quantity.to_string(),
        version.to_string(),
    ])
}
