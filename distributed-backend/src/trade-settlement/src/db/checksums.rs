//! Deterministic checksums for mutable ownership rows.
//!
//! What this file contains:
//! - Pure SHA-256 checksum functions for wallet and item-stack rows.
//!
//! How it works:
//! - Each field is length-prefixed before hashing. This avoids accidental
//!   ambiguity such as `["ab", "c"]` and `["a", "bc"]` hashing the same stream.
//! - Only stable business-state fields are hashed. Timestamps are excluded.
//!
//! Why it exists:
//! - Checksums make out-of-band mutation and stale state easier to detect.
//! - Checksums are structural integrity aids, not gameplay authorization rules.

// DB-BLOCK src_db_checksums_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for deterministic wallet/item checksum construction.
// Why: explicit imports make coupling visible during review.
use sha2::{Digest, Sha256};

// DB-BLOCK src_db_checksums_002
// What: implements `write_text`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn write_text(hasher: &mut Sha256, value: &str) {
    hasher.update((value.len() as u64).to_be_bytes());
    hasher.update(value.as_bytes());
}

// DB-BLOCK src_db_checksums_003
// What: implements `write_i64`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn write_i64(hasher: &mut Sha256, value: i64) {
    hasher.update(value.to_be_bytes());
}

// DB-BLOCK src_db_checksums_004
// What: implements `wallet_checksum`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn wallet_checksum(
    wallet_id: &str,
    capsuleer_id: Option<&str>,
    wallet_kind: &str,
    available_isk: i64,
    reserved_isk: i64,
    wallet_state: &str,
    wallet_version: i64,
) -> String {
    // DB-BLOCK src_db_checksums_005
    // What: binds `h` as a named intermediate.
    // How: computes/extracts `h` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut h = Sha256::new();
    write_text(&mut h, wallet_id);
    write_text(&mut h, capsuleer_id.unwrap_or(""));
    write_text(&mut h, wallet_kind);
    write_i64(&mut h, available_isk);
    write_i64(&mut h, reserved_isk);
    write_text(&mut h, wallet_state);
    write_i64(&mut h, wallet_version);
    format!("{:x}", h.finalize())
}

// DB-BLOCK src_db_checksums_006
// What: implements `item_stack_checksum`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn item_stack_checksum(
    item_stack_id: &str,
    capsuleer_id: &str,
    item_type_id: &str,
    station_id: &str,
    available_quantity: i64,
    reserved_quantity: i64,
    stack_state: &str,
    stack_version: i64,
) -> String {
    // DB-BLOCK src_db_checksums_007
    // What: binds `h` as a named intermediate.
    // How: computes/extracts `h` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut h = Sha256::new();
    write_text(&mut h, item_stack_id);
    write_text(&mut h, capsuleer_id);
    write_text(&mut h, item_type_id);
    write_text(&mut h, station_id);
    write_i64(&mut h, available_quantity);
    write_i64(&mut h, reserved_quantity);
    write_text(&mut h, stack_state);
    write_i64(&mut h, stack_version);
    format!("{:x}", h.finalize())
}
