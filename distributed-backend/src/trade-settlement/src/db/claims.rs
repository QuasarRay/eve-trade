//! Claim-result API.
//!
//! What this file contains:
//! - A deliberately strict MVP implementation of `ClaimResult`.
//!
//! How it works:
//! - The schema/proto supports `claimable` and `claimed`, but this package uses
//!   immediate delivery in `request_settlement`.
//! - Therefore this function rejects claim attempts unless claimable delivery is
//!   intentionally implemented later.
//!
//! Why it exists:
//! - Silent fake claim support would be dangerous. A production system must fail
//!   unsupported state-machine branches explicitly.

// DB-BLOCK src_db_claims_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for claim-result DB behavior and MVP-safe rejection.
// Why: explicit imports make coupling visible during review.
use sqlx::PgPool;

use crate::error::SettlementError;
use crate::generated::settlement::v1::{ClaimResultRequest, ClaimResultResponse};

// DB-BLOCK src_db_claims_002
// What: handles claim-result requests at the DB boundary.
// How: rejects unsupported claimable-delivery flow for MVP with a typed error.
// Why: unsafe partial implementation is worse than explicit unsupported behavior.
pub async fn claim_result(
    _pool: &PgPool,
    _req: &ClaimResultRequest,
) -> Result<ClaimResultResponse, SettlementError> {
    // DB-BLOCK src_db_claims_003
    // What: returns the branch result.
    // How: wraps the computed response/error with `Err(SettlementError::Unsupported(`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Err(SettlementError::Unsupported(
        "claimable delivery is not implemented; MVP uses immediate delivery".to_string(),
    ))
}
