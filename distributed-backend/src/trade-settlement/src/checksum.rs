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

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Serialize;

    #[derive(Serialize)]
    struct Fixture<'a> {
        kind: &'a str,
        amount: i64,
    }

    #[test]
    fn sha256_matches_known_vector() {
        assert_eq!(
            hash_bytes(b"eve-trade"),
            "5b50e3acde405f3d939cb56c4715c4a4c08674f7581757685974930bfc969bd3"
        );
    }

    #[test]
    fn wallet_checksum_is_domain_separated_and_versioned() {
        let id = Uuid::parse_str("00000000-0000-4000-8000-000000000001").unwrap();
        let first = wallet_checksum(id, 100, 1);
        assert_eq!(first, wallet_checksum(id, 100, 1));
        assert_ne!(first, wallet_checksum(id, 101, 1));
        assert_ne!(first, wallet_checksum(id, 100, 2));
        assert_ne!(first, item_stack_checksum(id, 100, 1));
    }

    #[test]
    fn item_stack_checksum_changes_for_every_material_field() {
        let first_id = Uuid::parse_str("00000000-0000-4000-8000-000000000001").unwrap();
        let second_id = Uuid::parse_str("00000000-0000-4000-8000-000000000002").unwrap();
        let baseline = item_stack_checksum(first_id, 10, 1);
        assert_ne!(baseline, item_stack_checksum(second_id, 10, 1));
        assert_ne!(baseline, item_stack_checksum(first_id, 11, 1));
        assert_ne!(baseline, item_stack_checksum(first_id, 10, 2));
    }

    #[test]
    fn json_hash_is_stable_for_a_serializable_value() {
        let value = Fixture {
            kind: "transfer",
            amount: 42,
        };
        assert_eq!(hash_json(&value).unwrap(), hash_json(&value).unwrap());
    }
}
