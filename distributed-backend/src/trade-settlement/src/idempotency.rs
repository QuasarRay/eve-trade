use crate::generated::settlement::SettleTradeRequest;
use sha2::{Digest, Sha256};

// This block creates a stable hash of the market request excluding request_id.
// Excluding request_id lets the hash represent the actual trade operation, while
// settlement_requests still uses request_id as the retry key.
pub fn request_hash(req: &SettleTradeRequest) -> String {
    let mut hasher = Sha256::new();

    write_string(&mut hasher, &req.trade_id);
    write_i32(&mut hasher, req.action);
    write_string(&mut hasher, &req.item_owner_id);
    write_string(&mut hasher, &req.item_receiver_id);
    write_string(&mut hasher, &req.isk_payer_id);
    write_string(&mut hasher, &req.isk_receiver_id);
    write_string(&mut hasher, &req.item_type_id);
    write_i64(&mut hasher, req.quantity);
    write_i64(&mut hasher, req.isk_units);

    format!("{:x}", hasher.finalize())
}

// This helper writes a length prefix before the string. The prefix prevents
// ambiguous concatenations such as ["ab", "c"] and ["a", "bc"] from producing
// the same byte stream.
fn write_string(hasher: &mut Sha256, value: &str) {
    hasher.update((value.len() as u64).to_be_bytes());
    hasher.update(value.as_bytes());
}

// This helper serializes i32 values in a fixed byte order so hashes are stable
// across platforms.
fn write_i32(hasher: &mut Sha256, value: i32) {
    hasher.update(value.to_be_bytes());
}

// This helper serializes i64 values in a fixed byte order so hashes are stable
// across platforms.
fn write_i64(hasher: &mut Sha256, value: i64) {
    hasher.update(value.to_be_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::generated::settlement::TradeAction;

    // This helper builds a valid request so hash tests can focus only on the
    // idempotency behavior.
    fn request(request_id: &str) -> SettleTradeRequest {
        SettleTradeRequest {
            request_id: request_id.to_string(),
            trade_id: "trade-1".to_string(),
            action: TradeAction::Complete as i32,
            item_owner_id: "seller".to_string(),
            item_receiver_id: "buyer".to_string(),
            isk_payer_id: "buyer".to_string(),
            isk_receiver_id: "seller".to_string(),
            item_type_id: "tritanium".to_string(),
            quantity: 100,
            isk_units: 500,
        }
    }

    // This test proves request_id is not part of the operation hash. Retrying the
    // same operation with a different id can be recognized as the same business
    // operation even though the request_id table still decides idempotency.
    #[test]
    fn hash_ignores_request_id() {
        assert_eq!(request_hash(&request("r1")), request_hash(&request("r2")));
    }

    // This test proves changing trade content changes the hash, which lets the
    // database detect request_id reuse with different content.
    #[test]
    fn hash_changes_when_content_changes() {
        let a = request("r1");
        let mut b = request("r1");
        b.quantity += 1;
        assert_ne!(request_hash(&a), request_hash(&b));
    }
}
