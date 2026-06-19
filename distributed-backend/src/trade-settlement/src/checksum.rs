use serde::Serialize;
use sha2::{Digest, Sha256};
use uuid::Uuid;

pub const CHECKSUM_ALGORITHM: &str = "sha256-v1";

pub fn wallet_checksum(wallet_id: Uuid, isk_amount: i64, wallet_version: i64) -> String {
    hash_bytes(format!("wallet:{wallet_id}:{isk_amount}:{wallet_version}").as_bytes())
}

pub fn item_stack_checksum(item_stack_id: Uuid, quantity: i64, stack_version: i64) -> String {
    hash_bytes(format!("item_stack:{item_stack_id}:{quantity}:{stack_version}").as_bytes())
}

pub fn hash_json<T>(value: &T) -> crate::error::Result<String>
where
    T: Serialize,
{
    let bytes = serde_json::to_vec(value)?;
    Ok(hash_bytes(&bytes))
}

pub fn hash_bytes(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hex::encode(hasher.finalize())
}
