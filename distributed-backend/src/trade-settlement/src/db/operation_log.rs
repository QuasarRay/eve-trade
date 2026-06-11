//! Top-level operation log.
//!
//! What this file contains:
//! - Functions for creating, loading, completing, and failing `trade.operation`.
//!
//! How it works:
//! - Every durable mutation starts with one top-level operation row.
//! - Child wallet/item/order/settlement rows point back to this operation.
//!
//! Why it exists:
//! - Production debugging needs to answer: "Which database changes belonged to
//!   this one logical command?"

// DB-BLOCK src_db_operation_log_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for top-level operation audit records.
// Why: explicit imports make coupling visible during review.
use sqlx::{Postgres, Transaction};

use crate::db::extract;
use crate::db::rows::OperationRow;
use crate::error::SettlementError;
use crate::generated::trade::v1::RequestContext;

// DB-BLOCK src_db_operation_log_002
// What: creates a top-level operation audit row.
// How: extracts request metadata and inserts an in-progress operation.
// Why: all child mutations need a single parent for audit and recovery.
pub async fn create(
    tx: &mut Transaction<'_, Postgres>,
    context: &Option<RequestContext>,
    operation_kind: &str,
) -> Result<String, SettlementError> {
    // DB-BLOCK src_db_operation_log_003
    // What: binds `source_system` as a named intermediate.
    // How: computes/extracts `source_system` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let source_system = extract::source_system(context)?;
    // DB-BLOCK src_db_operation_log_004
    // What: binds `created_by_service` as a named intermediate.
    // How: computes/extracts `created_by_service` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let created_by_service = extract::created_by_service(context)?;
    // DB-BLOCK src_db_operation_log_005
    // What: binds `request_id` as a named intermediate.
    // How: computes/extracts `request_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let request_id = extract::request_id(context)?;
    // DB-BLOCK src_db_operation_log_006
    // What: binds `idempotency_key` as a named intermediate.
    // How: computes/extracts `idempotency_key` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let idempotency_key = extract::idempotency_key(context)?;
    // DB-BLOCK src_db_operation_log_007
    // What: binds `acting_capsuleer_id` as a named intermediate.
    // How: computes/extracts `acting_capsuleer_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let acting_capsuleer_id = extract::acting_capsuleer_id(context)?;

    // DB-BLOCK src_db_operation_log_008
    // What: binds `id` as a named intermediate.
    // How: computes/extracts `id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let id: (String,) = sqlx::query_as(
        r#"
        INSERT INTO trade.operation (
            operation_kind, source_system, request_id, idempotency_key,
            caused_by_capsuleer_id, operation_state, created_by_service
        )
        VALUES ($1, $2, $3::uuid, $4, $5::uuid, 'in_progress', $6)
        RETURNING operation_id::text
        "#,
    )
    .bind(operation_kind)
    .bind(source_system)
    .bind(request_id)
    .bind(idempotency_key)
    .bind(acting_capsuleer_id)
    .bind(created_by_service)
    .fetch_one(&mut **tx)
    .await?;
    // DB-BLOCK src_db_operation_log_009
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(id.0)`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(id.0)
}

// DB-BLOCK src_db_operation_log_010
// What: implements `load`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub async fn load(
    tx: &mut Transaction<'_, Postgres>,
    operation_id: &str,
) -> Result<OperationRow, SettlementError> {
    // DB-BLOCK src_db_operation_log_011
    // What: performs a parameterized SQL operation against `operation`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query_as::<_, OperationRow>(
        r#"
        SELECT operation_id::text AS operation_id, operation_kind, source_system,
               external_operation_id, request_id::text AS request_id,
               idempotency_key, caused_by_capsuleer_id::text AS caused_by_capsuleer_id,
               operation_state::text AS operation_state, created_by_service,
               started_at, completed_at, failure_code, failure_message
        FROM trade.operation
        WHERE operation_id = $1::uuid
        "#,
    )
    .bind(operation_id)
    .fetch_one(&mut **tx)
    .await
    .map_err(SettlementError::from)
}

// DB-BLOCK src_db_operation_log_012
// What: marks an operation as completed.
// How: updates operation_state and completed_at.
// Why: operation status must reflect successful completion of all child writes.
pub async fn complete(
    tx: &mut Transaction<'_, Postgres>,
    operation_id: &str,
) -> Result<(), SettlementError> {
    // DB-BLOCK src_db_operation_log_013
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        UPDATE trade.operation
        SET operation_state = 'completed', completed_at = now(), failure_code = NULL, failure_message = NULL
        WHERE operation_id = $1::uuid
        "#,
    )
    .bind(operation_id)
    .execute(&mut **tx)
    .await?;
    // DB-BLOCK src_db_operation_log_014
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(())`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(())
}

// DB-BLOCK src_db_operation_log_015
// What: marks an operation as failed.
// How: stores failure code/message and completes the operation row.
// Why: operators and retries need durable failure evidence.
pub async fn fail(
    tx: &mut Transaction<'_, Postgres>,
    operation_id: &str,
    failure_code: &str,
    failure_message: &str,
) -> Result<(), SettlementError> {
    // DB-BLOCK src_db_operation_log_016
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        UPDATE trade.operation
        SET operation_state = 'failed', completed_at = now(), failure_code = $2, failure_message = $3
        WHERE operation_id = $1::uuid
        "#,
    )
    .bind(operation_id)
    .bind(failure_code)
    .bind(failure_message)
    .execute(&mut **tx)
    .await?;
    // DB-BLOCK src_db_operation_log_017
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(())`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(())
}
