use sha2::{Digest, Sha256};
use sqlx::{PgConnection, Postgres, Transaction};
use uuid::Uuid;

use crate::error::SettlementError;

pub(super) const CHECKSUM_ALGORITHM: &str = "sha256-v1";
pub(super) type DbTx<'a> = Transaction<'a, Postgres>;

pub(super) fn tx_conn<'tx>(tx: &'tx mut DbTx<'_>) -> &'tx mut PgConnection {
    tx
}

pub(super) fn ordered_pair(left: Uuid, right: Uuid) -> (Uuid, Uuid) {
    if left.as_bytes() <= right.as_bytes() {
        (left, right)
    } else {
        (right, left)
    }
}

pub(super) fn ensure_positive(value: i64, field: &str) -> Result<(), SettlementError> {
    if value <= 0 {
        return Err(SettlementError::InvalidRequest(format!(
            "{field} must be positive"
        )));
    }
    Ok(())
}

pub(super) fn ensure_nonnegative(value: i64, field: &str) -> Result<(), SettlementError> {
    if value < 0 {
        return Err(SettlementError::InvalidRequest(format!(
            "{field} must be non-negative"
        )));
    }
    Ok(())
}

pub(super) fn ensure_not_blank(value: &str, field: &str) -> Result<(), SettlementError> {
    if value.trim().is_empty() {
        return Err(SettlementError::InvalidRequest(format!(
            "{field} must not be blank"
        )));
    }
    Ok(())
}

fn checksum(parts: &[String]) -> String {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update(part.as_bytes());
        hasher.update(b"\0");
    }
    format!("{:x}", hasher.finalize())
}

pub(super) fn item_stack_checksum(
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

pub(super) fn wallet_checksum(
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
