//! Idempotency protocol.
//!
//! What this file contains:
//! - The database protocol for one logical command, many retry attempts, and one
//!   final result.
//!
//! How it works:
//! - A request fingerprint is computed from the protobuf bytes plus operation name.
//! - `idempotency_record` is inserted once per logical command.
//! - `request_attempt` is inserted once per RPC attempt.
//! - Existing result rows are loaded under lock and returned as replay.
//!
//! Why it exists:
//! - Retrying `RequestSettlement` must never move ISK/items twice.
//! - Same key with different request body must be rejected as unsafe.

// DB-BLOCK src_db_idempotency_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for idempotency records, attempts, replay detection, and result recording.
// Why: explicit imports make coupling visible during review.
use prost::Message;
use sha2::{Digest, Sha256};
use sqlx::{Postgres, Transaction};

use crate::db::extract;
use crate::db::rows::IdempotencyResultRow;
use crate::error::SettlementError;
use crate::generated::trade::v1::RequestContext;

// DB-BLOCK src_db_idempotency_002
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Debug, Clone)]
// DB-BLOCK src_db_idempotency_003
// What: defines the `Guard` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct Guard {
    pub request_id: String,
    pub idempotency_key: String,
    pub replay: Option<IdempotencyResultRow>,
}

// DB-BLOCK src_db_idempotency_004
// What: implements `fingerprint`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn fingerprint<M: Message>(operation_name: &str, msg: &M) -> String {
    // DB-BLOCK src_db_idempotency_005
    // What: binds `bytes` as a named intermediate.
    // How: computes/extracts `bytes` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut bytes = Vec::new();
    msg.encode(&mut bytes)
        .expect("encoding protobuf message into Vec cannot fail");
    // DB-BLOCK src_db_idempotency_006
    // What: binds `h` as a named intermediate.
    // How: computes/extracts `h` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let mut h = Sha256::new();
    h.update((operation_name.len() as u64).to_be_bytes());
    h.update(operation_name.as_bytes());
    h.update((bytes.len() as u64).to_be_bytes());
    h.update(bytes);
    format!("{:x}", h.finalize())
}

// DB-BLOCK src_db_idempotency_007
// What: starts idempotency handling for a write request.
// How: extracts context, hashes the request, locks or creates idempotency rows, and records the attempt.
// Why: same key/same request must replay; same key/different request must be rejected.
pub async fn begin<M: Message>(
    tx: &mut Transaction<'_, Postgres>,
    context: &Option<RequestContext>,
    operation_name: &str,
    msg: &M,
) -> Result<Guard, SettlementError> {
    // DB-BLOCK src_db_idempotency_008
    // What: binds `request_id` as a named intermediate.
    // How: computes/extracts `request_id` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let request_id = extract::request_id(context)?;
    // DB-BLOCK src_db_idempotency_009
    // What: binds `idempotency_key` as a named intermediate.
    // How: computes/extracts `idempotency_key` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let idempotency_key = extract::idempotency_key(context)?;
    // DB-BLOCK src_db_idempotency_010
    // What: binds `created_by_service` as a named intermediate.
    // How: computes/extracts `created_by_service` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let created_by_service = extract::created_by_service(context)?;
    // DB-BLOCK src_db_idempotency_011
    // What: binds `fingerprint` as a named intermediate.
    // How: computes/extracts `fingerprint` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let fingerprint = fingerprint(operation_name, msg);

    // DB-BLOCK src_db_idempotency_012
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        INSERT INTO trade.idempotency_record
            (idempotency_key, request_fingerprint, operation_name, operation_state, created_by_service)
        VALUES ($1, $2, $3, 'in_progress', $4)
        ON CONFLICT (idempotency_key) DO NOTHING
        "#,
    )
    .bind(&idempotency_key)
    .bind(&fingerprint)
    .bind(operation_name)
    .bind(&created_by_service)
    .execute(&mut **tx)
    .await?;

    // DB-BLOCK src_db_idempotency_013
    // What: binds `existing` as a named intermediate.
    // How: computes/extracts `existing` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let existing: (String,) = sqlx::query_as(
        r#"
        SELECT request_fingerprint
        FROM trade.idempotency_record
        WHERE idempotency_key = $1
        FOR UPDATE
        "#,
    )
    .bind(&idempotency_key)
    .fetch_one(&mut **tx)
    .await?;

    // DB-BLOCK src_db_idempotency_014
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if existing.0 != fingerprint {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if existing.0 != fingerprint {
        // DB-BLOCK src_db_idempotency_015
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::RequestIdConflict);` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::RequestIdConflict);
    }

    // DB-BLOCK src_db_idempotency_016
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        INSERT INTO trade.request_attempt
            (request_id, idempotency_key, received_by_service, attempt_state)
        VALUES ($1::uuid, $2, $3, 'in_progress')
        ON CONFLICT (request_id) DO UPDATE
        SET attempt_state = 'replayed'
        "#,
    )
    .bind(&request_id)
    .bind(&idempotency_key)
    .bind(&created_by_service)
    .execute(&mut **tx)
    .await?;

    // DB-BLOCK src_db_idempotency_017
    // What: binds `replay` as a named intermediate.
    // How: computes/extracts `replay` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let replay = sqlx::query_as::<_, IdempotencyResultRow>(
        r#"
        SELECT
            operation_id::text AS operation_id,
            trade_instance_id::text AS trade_instance_id,
            trade_transaction_id::text AS trade_transaction_id,
            settlement_id::text AS settlement_id,
            wallet_operation_id::text AS wallet_operation_id,
            item_stack_operation_id::text AS item_stack_operation_id
        FROM trade.idempotency_result
        WHERE idempotency_key = $1
        FOR UPDATE
        "#,
    )
    .bind(&idempotency_key)
    .fetch_optional(&mut **tx)
    .await?;

    // DB-BLOCK src_db_idempotency_018
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(Guard { request_id, idempotency_key, replay })`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(Guard {
        request_id,
        idempotency_key,
        replay,
    })
}

// DB-BLOCK src_db_idempotency_019
// What: defines the `RecordSuccessInput` data shape.
// How: groups the replay result columns that must be written together.
// Why: named fields prevent wallet/item/settlement IDs from being swapped accidentally.
pub struct RecordSuccessInput<'a> {
    pub guard: &'a Guard,
    pub result_kind: &'a str,
    pub operation_id: Option<&'a str>,
    pub trade_instance_id: Option<&'a str>,
    pub trade_transaction_id: Option<&'a str>,
    pub settlement_id: Option<&'a str>,
    pub wallet_operation_id: Option<&'a str>,
    pub item_stack_operation_id: Option<&'a str>,
    pub result_state: &'a str,
}

// DB-BLOCK src_db_idempotency_020
// What: records the durable success result for idempotency replay.
// How: inserts idempotency_result and completes idempotency_record inside the caller transaction.
// Why: after commit, retries must return the existing result without re-running ownership movement.
pub async fn record_success(
    tx: &mut Transaction<'_, Postgres>,
    input: RecordSuccessInput<'_>,
) -> Result<(), SettlementError> {
    // DB-BLOCK src_db_idempotency_021
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        INSERT INTO trade.idempotency_result (
            idempotency_key, operation_id, result_kind, trade_instance_id,
            trade_transaction_id, settlement_id, wallet_operation_id,
            item_stack_operation_id, result_state
        )
        VALUES ($1, $2::uuid, $3, $4::uuid, $5::uuid, $6::uuid, $7::uuid, $8::uuid, $9)
        ON CONFLICT (idempotency_key) DO NOTHING
        "#,
    )
    .bind(&input.guard.idempotency_key)
    .bind(input.operation_id)
    .bind(input.result_kind)
    .bind(input.trade_instance_id)
    .bind(input.trade_transaction_id)
    .bind(input.settlement_id)
    .bind(input.wallet_operation_id)
    .bind(input.item_stack_operation_id)
    .bind(input.result_state)
    .execute(&mut **tx)
    .await?;

    // DB-BLOCK src_db_idempotency_021
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        UPDATE trade.idempotency_record
        SET operation_state = 'completed', completed_at = now()
        WHERE idempotency_key = $1
        "#,
    )
    .bind(&input.guard.idempotency_key)
    .execute(&mut **tx)
    .await?;

    // DB-BLOCK src_db_idempotency_022
    // What: performs a parameterized SQL operation against `the relevant trade schema table`.
    // How: uses `sqlx::query` or `query_as` with bind parameters inside the active transaction.
    // Why: database reads/writes must be explicit, typed, injection-safe, and atomic with surrounding work.
    sqlx::query(
        r#"
        UPDATE trade.request_attempt
        SET attempt_state = 'completed', completed_at = now()
        WHERE request_id = $1::uuid
        "#,
    )
    .bind(&input.guard.request_id)
    .execute(&mut **tx)
    .await?;
    // DB-BLOCK src_db_idempotency_023
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(())`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(())
}
