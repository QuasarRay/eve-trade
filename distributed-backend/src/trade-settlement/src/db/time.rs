//! Time conversion helpers.
//!
//! What this file contains:
//! - Conversions between `chrono::DateTime<Utc>` and `prost_types::Timestamp`.
//!
//! How it works:
//! - Database rows use `TIMESTAMPTZ`, decoded by SQLx as `DateTime<Utc>`.
//! - Protobuf responses use `google.protobuf.Timestamp`.
//!
//! Why it exists:
//! - Keeping this conversion in one place avoids inconsistent timestamp handling.

// DB-BLOCK src_db_time_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for timestamp conversion for protobuf responses.
// Why: explicit imports make coupling visible during review.
use chrono::{DateTime, TimeZone, Utc};
use prost_types::Timestamp;

use crate::error::SettlementError;

// DB-BLOCK src_db_time_002
// What: implements `to_proto`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn to_proto(value: DateTime<Utc>) -> Timestamp {
    Timestamp {
        seconds: value.timestamp(),
        nanos: value.timestamp_subsec_nanos() as i32,
    }
}

// DB-BLOCK src_db_time_003
// What: implements `to_proto_opt`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn to_proto_opt(value: Option<DateTime<Utc>>) -> Option<Timestamp> {
    value.map(to_proto)
}

// DB-BLOCK src_db_time_004
// What: implements `from_proto_required`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn from_proto_required(field: &str, value: &Option<Timestamp>) -> Result<DateTime<Utc>, SettlementError> {
    // DB-BLOCK src_db_time_005
    // What: binds `value` as a named intermediate.
    // How: computes/extracts `value` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let value = value
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest(format!("{field} is required")))?;
    Utc.timestamp_opt(value.seconds, value.nanos as u32)
        .single()
        .ok_or_else(|| SettlementError::InvalidRequest(format!("{field} is not a valid timestamp")))
}
