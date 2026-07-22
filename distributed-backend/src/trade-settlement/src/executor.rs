use serde::Serialize;
use sqlx::{PgPool, Postgres, Transaction};
use tracing::{error, info_span, Instrument};
use uuid::Uuid;

use crate::authorization::authorize_plan;
use crate::checksum;
use crate::commands::{ExecuteBatchCommand, SettlementCommand, SettlementIntent};
use crate::error::{Result, SettlementError};
use crate::operations::{execute_settlement_command, OperationOutput};
use crate::plan::validate_plan_semantics;
use crate::proto::trade_settlement::SettlementOperationKind;

const REQUEST_KIND: &str = "trade_settlement.execute_settlement_batch";
const BATCH_STATE_IN_PROGRESS: &str = "IN_PROGRESS";
const BATCH_STATE_COMPLETED: &str = "COMPLETED";
const BATCH_STATE_FAILED: &str = "FAILED";
const IDEMPOTENCY_STATE_IN_PROGRESS: &str = "IN_PROGRESS";
const IDEMPOTENCY_STATE_COMPLETED: &str = "COMPLETED";
const IDEMPOTENCY_STATE_FAILED: &str = "FAILED";
const STEP_STATE_PENDING: &str = "PENDING";
const STEP_STATE_RUNNING: &str = "RUNNING";
const STEP_STATE_COMPLETED: &str = "COMPLETED";
const STEP_STATE_FAILED: &str = "FAILED";
const ATTEMPT_STATE_IN_PROGRESS: &str = "IN_PROGRESS";
const ATTEMPT_STATE_COMPLETED: &str = "COMPLETED";
const ATTEMPT_STATE_FAILED: &str = "FAILED";
const REQUEST_FINGERPRINT_FORMAT: &str = "trade-settlement.execute_settlement_batch.v1";
const REQUEST_FINGERPRINT_PREFIX: &str = "trade-settlement.execute_settlement_batch.v1.sha256:";

#[derive(Debug, Clone)]
pub struct SettlementExecutor {
    db: PgPool,
}

#[derive(Debug, Clone)]
pub struct BatchExecutionResult {
    pub settlement_batch_id: Uuid,
    pub idempotency_key: String,
    pub batch_state: String,
    pub idempotent_replay: bool,
    pub step_results: Vec<StepExecutionResult>,
}

#[derive(Debug, Clone)]
pub struct StepExecutionResult {
    pub step_index: u32,
    pub settlement_step_id: Uuid,
    pub step_kind: SettlementOperationKind,
    pub output: OperationOutput,
}

#[derive(Debug, Clone)]
struct StepRecord {
    step_index: i32,
    settlement_step_id: Uuid,
    step_kind: SettlementOperationKind,
}

struct FailedExecution<'a> {
    settlement_batch_id: Uuid,
    request_id: Uuid,
    idempotency_key: &'a str,
    step_records: &'a [StepRecord],
    attempted_steps: usize,
    failure_code: &'a str,
    failure_message: &'a str,
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct IdempotencyRecordRow {
    request_fingerprint: String,
    idempotency_state: String,
    result_settlement_batch_id: Option<Uuid>,
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct StepReplayRow {
    settlement_step_id: Uuid,
    step_index: i32,
    step_kind: String,
    step_output: serde_json::Value,
}

impl SettlementExecutor {
    pub fn new(db: PgPool) -> Self {
        Self { db }
    }

    #[tracing::instrument(
        name = "settlement.execute_transaction",
        skip_all,
        fields(
            idempotency_key = %command.idempotency_key,
            operation_count = command.operations.len(),
            settlement_batch_id = tracing::field::Empty
        )
    )]
    pub async fn execute_batch(
        &self,
        command: ExecuteBatchCommand,
    ) -> Result<BatchExecutionResult> {
        if command.intent != SettlementIntent::Unspecified {
            validate_plan_semantics(&command)?;
        }
        let validation_span = info_span!(
            "settlement.validate_batch",
            idempotency_key = %command.idempotency_key,
            operation_count = command.operations.len(),
        );
        let _validation_guard = validation_span.enter();
        if command.operations.is_empty() {
            return Err(SettlementError::InvalidArgument(
                "operations must contain at least one settlement operation".to_string(),
            ));
        }

        let request_fingerprint = build_request_fingerprint(&command)?;
        let client_fingerprint_assertion = command.request_fingerprint.clone();
        drop(_validation_guard);

        let mut tx = self.db.begin().await?;

        let mut trade_instance_ids: Vec<Uuid> = command
            .operations
            .iter()
            .filter_map(SettlementCommand::trade_instance_id)
            .collect();
        trade_instance_ids.sort_unstable();
        trade_instance_ids.dedup();
        lock_trade_instances(&mut tx, &trade_instance_ids).await?;

        if let Some(existing) = lock_idempotency_record(&mut tx, &command.idempotency_key).await? {
            if existing.request_fingerprint != request_fingerprint {
                return Err(SettlementError::Conflict(format!(
                    "idempotency_key {} was already used with a different request fingerprint",
                    command.idempotency_key
                )));
            }
            validate_request_fingerprint_assertion(
                client_fingerprint_assertion.as_deref(),
                &request_fingerprint,
            )?;

            match existing.idempotency_state.as_str() {
                IDEMPOTENCY_STATE_COMPLETED => {
                    let batch_id = existing.result_settlement_batch_id.ok_or_else(|| {
                        SettlementError::FailedPrecondition(format!(
                            "idempotency_key {} completed without a result batch",
                            command.idempotency_key
                        ))
                    })?;
                    complete_operation_if_present(&mut tx, &command.idempotency_key, batch_id)
                        .await?;
                    tx.commit().await?;
                    let step_results = self.load_replayed_steps(batch_id).await?;
                    return Ok(BatchExecutionResult {
                        settlement_batch_id: batch_id,
                        idempotency_key: command.idempotency_key,
                        batch_state: BATCH_STATE_COMPLETED.to_string(),
                        idempotent_replay: true,
                        step_results,
                    });
                }
                IDEMPOTENCY_STATE_IN_PROGRESS => {
                    return Err(SettlementError::Conflict(format!(
                        "idempotency_key {} is already in progress",
                        command.idempotency_key
                    )));
                }
                IDEMPOTENCY_STATE_FAILED => {
                    reset_failed_idempotency_record(&mut tx, &command.idempotency_key).await?;
                }
                other => {
                    return Err(SettlementError::FailedPrecondition(format!(
                        "idempotency_key {} is in unsupported state {}",
                        command.idempotency_key, other
                    )));
                }
            }
        } else {
            validate_request_fingerprint_assertion(
                client_fingerprint_assertion.as_deref(),
                &request_fingerprint,
            )?;
            insert_idempotency_record(
                &mut tx,
                &command.idempotency_key,
                &request_fingerprint,
                &command.created_by_service,
            )
            .await?;
        }

        if command.intent != SettlementIntent::Unspecified {
            authorize_plan(&mut tx, &command).await?;
        }

        let request_id = command.request_id.unwrap_or_else(Uuid::new_v4);
        let attempt_number = next_attempt_number(&mut tx, &command.idempotency_key).await?;
        insert_request_attempt(
            &mut tx,
            request_id,
            &command.idempotency_key,
            attempt_number,
            &command.created_by_service,
        )
        .await?;

        let settlement_batch_id = Uuid::new_v4();
        tracing::Span::current().record("settlement_batch_id", settlement_batch_id.to_string());
        insert_settlement_batch(&mut tx, settlement_batch_id, request_id, &command).await?;

        let step_records =
            insert_settlement_steps(&mut tx, settlement_batch_id, &command.operations).await?;

        create_business_savepoint(&mut tx).await?;
        let mut step_results = Vec::with_capacity(command.operations.len());
        for (operation, step) in command.operations.iter().zip(step_records.iter()) {
            mark_step_running(&mut tx, step.settlement_step_id).await?;
            let step_span = info_span!(
                "settlement.execute_step",
                settlement_batch_id = %settlement_batch_id,
                settlement_step_id = %step.settlement_step_id,
                step_kind = operation.kind_name(),
                step_order = step.step_index,
            );
            let output =
                match execute_settlement_command(&mut tx, step.settlement_step_id, operation)
                    .instrument(step_span)
                    .await
                {
                    Ok(output) => output,
                    Err(error) => {
                        let failure_code = error.code().to_string();
                        let failure_message = error.to_string();
                        let attempted_steps = step_results.len() + 1;
                        let rollback_span = info_span!(
                            "settlement.rollback",
                            settlement_batch_id = %settlement_batch_id,
                            settlement_step_id = %step.settlement_step_id,
                            rollback.performed = true,
                            failure_code = %failure_code,
                        );
                        rollback_business_savepoint(&mut tx)
                            .instrument(rollback_span)
                            .await?;
                        error!(
                            settlement_batch_id = %settlement_batch_id,
                            settlement_step_id = %step.settlement_step_id,
                            failure_code = %failure_code,
                            error.message = %failure_message,
                            rollback.performed = true,
                            "settlement step failed and business savepoint was rolled back"
                        );
                        let failure_span = info_span!(
                            "settlement.record_failure_audit",
                            settlement_batch_id = %settlement_batch_id,
                            failure_code = %failure_code,
                            attempted_steps,
                        );
                        fail_execution(
                            &mut tx,
                            FailedExecution {
                                settlement_batch_id,
                                request_id,
                                idempotency_key: &command.idempotency_key,
                                step_records: &step_records,
                                attempted_steps,
                                failure_code: &failure_code,
                                failure_message: &failure_message,
                            },
                        )
                        .instrument(failure_span)
                        .await?;
                        tx.commit().await?;
                        return Err(error);
                    }
                };
            mark_step_completed(&mut tx, step.settlement_step_id, &output).await?;
            step_results.push(StepExecutionResult {
                step_index: step.step_index as u32,
                settlement_step_id: step.settlement_step_id,
                step_kind: step.step_kind,
                output,
            });
        }

        release_business_savepoint(&mut tx).await?;
        complete_execution(
            &mut tx,
            settlement_batch_id,
            request_id,
            &command.idempotency_key,
        )
        .await?;
        tx.commit().await?;

        Ok(BatchExecutionResult {
            settlement_batch_id,
            idempotency_key: command.idempotency_key,
            batch_state: BATCH_STATE_COMPLETED.to_string(),
            idempotent_replay: false,
            step_results,
        })
    }

    async fn load_replayed_steps(
        &self,
        settlement_batch_id: Uuid,
    ) -> Result<Vec<StepExecutionResult>> {
        let rows = sqlx::query_as::<_, StepReplayRow>(
            r#"
            SELECT settlement_step_id,
                   step_index,
                   step_kind,
                   step_output
            FROM settlement_step
            WHERE settlement_batch_id = $1
            ORDER BY step_index
            "#,
        )
        .bind(settlement_batch_id)
        .fetch_all(&self.db)
        .await?;

        rows.into_iter()
            .map(|row| {
                Ok(StepExecutionResult {
                    step_index: row.step_index as u32,
                    settlement_step_id: row.settlement_step_id,
                    step_kind: kind_name_to_proto(&row.step_kind),
                    output: serde_json::from_value(row.step_output)?,
                })
            })
            .collect()
    }
}

async fn lock_trade_instances(
    tx: &mut Transaction<'_, Postgres>,
    trade_instance_ids: &[Uuid],
) -> Result<()> {
    for trade_instance_id in trade_instance_ids {
        sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))")
            .bind(trade_instance_id.to_string())
            .execute(&mut **tx)
            .await?;
    }
    Ok(())
}

async fn complete_execution(
    tx: &mut Transaction<'_, Postgres>,
    settlement_batch_id: Uuid,
    request_id: Uuid,
    idempotency_key: &str,
) -> Result<()> {
    complete_batch(tx, settlement_batch_id).await?;
    complete_request_attempt(tx, request_id).await?;
    complete_idempotency_record(tx, idempotency_key, settlement_batch_id).await?;
    complete_operation_if_present(tx, idempotency_key, settlement_batch_id).await?;
    Ok(())
}

async fn complete_operation_if_present(
    tx: &mut Transaction<'_, Postgres>,
    idempotency_key: &str,
    settlement_batch_id: Uuid,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE settlement_operation
        SET operation_state = 'SUCCEEDED',
            settlement_batch_id = $2,
            updated_at = now(),
            completed_at = COALESCE(completed_at, now())
        WHERE idempotency_key = $1
          AND operation_state IN ('QUEUED', 'PROCESSING', 'SUCCEEDED')
        "#,
    )
    .bind(idempotency_key)
    .bind(settlement_batch_id)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

async fn fail_execution(
    tx: &mut Transaction<'_, Postgres>,
    failure: FailedExecution<'_>,
) -> Result<()> {
    for (index, step) in failure.step_records.iter().enumerate() {
        if index < failure.attempted_steps {
            let message = if index + 1 == failure.attempted_steps {
                failure.failure_message.to_string()
            } else {
                format!(
                    "rolled back after later step failed: {}",
                    failure.failure_message
                )
            };
            mark_step_failed(tx, step.settlement_step_id, failure.failure_code, &message).await?;
        }
    }
    fail_batch(
        tx,
        failure.settlement_batch_id,
        failure.failure_code,
        failure.failure_message,
    )
    .await?;
    fail_request_attempt(
        tx,
        failure.request_id,
        failure.failure_code,
        failure.failure_message,
    )
    .await?;
    fail_idempotency_record(
        tx,
        failure.idempotency_key,
        failure.failure_code,
        failure.failure_message,
    )
    .await?;
    Ok(())
}

async fn lock_idempotency_record(
    tx: &mut Transaction<'_, Postgres>,
    idempotency_key: &str,
) -> Result<Option<IdempotencyRecordRow>> {
    Ok(sqlx::query_as::<_, IdempotencyRecordRow>(
        r#"
        SELECT request_fingerprint,
               idempotency_state,
               result_settlement_batch_id
        FROM idempotency_record
        WHERE idempotency_key = $1
        FOR UPDATE
        "#,
    )
    .bind(idempotency_key)
    .fetch_optional(&mut **tx)
    .await?)
}

async fn insert_idempotency_record(
    tx: &mut Transaction<'_, Postgres>,
    idempotency_key: &str,
    request_fingerprint: &str,
    created_by_service: &str,
) -> Result<()> {
    sqlx::query(
        r#"
        INSERT INTO idempotency_record (
            idempotency_key,
            request_fingerprint,
            request_kind,
            idempotency_state,
            created_by_service
        )
        VALUES ($1, $2, $3, $4, $5)
        "#,
    )
    .bind(idempotency_key)
    .bind(request_fingerprint)
    .bind(REQUEST_KIND)
    .bind(IDEMPOTENCY_STATE_IN_PROGRESS)
    .bind(created_by_service)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn reset_failed_idempotency_record(
    tx: &mut Transaction<'_, Postgres>,
    idempotency_key: &str,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE idempotency_record
        SET idempotency_state = $2,
            failure_code = NULL,
            failure_message = NULL,
            completed_at = NULL,
            result_settlement_batch_id = NULL
        WHERE idempotency_key = $1
        "#,
    )
    .bind(idempotency_key)
    .bind(IDEMPOTENCY_STATE_IN_PROGRESS)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn next_attempt_number(
    tx: &mut Transaction<'_, Postgres>,
    idempotency_key: &str,
) -> Result<i32> {
    let attempt_number = sqlx::query_scalar::<_, i32>(
        r#"
        SELECT COALESCE(MAX(attempt_number), 0)::INT + 1
        FROM request_attempt
        WHERE idempotency_key = $1
        "#,
    )
    .bind(idempotency_key)
    .fetch_one(&mut **tx)
    .await?;

    Ok(attempt_number)
}

async fn insert_request_attempt(
    tx: &mut Transaction<'_, Postgres>,
    request_id: Uuid,
    idempotency_key: &str,
    attempt_number: i32,
    received_by_service: &str,
) -> Result<()> {
    sqlx::query(
        r#"
        INSERT INTO request_attempt (
            request_id,
            idempotency_key,
            attempt_number,
            received_by_service,
            attempt_state
        )
        VALUES ($1, $2, $3, $4, $5)
        "#,
    )
    .bind(request_id)
    .bind(idempotency_key)
    .bind(attempt_number)
    .bind(received_by_service)
    .bind(ATTEMPT_STATE_IN_PROGRESS)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn insert_settlement_batch(
    tx: &mut Transaction<'_, Postgres>,
    settlement_batch_id: Uuid,
    request_id: Uuid,
    command: &ExecuteBatchCommand,
) -> Result<()> {
    sqlx::query(
        r#"
        INSERT INTO settlement_batch (
            settlement_batch_id,
            request_id,
            idempotency_key,
            external_request_id,
            caused_by_capsuleer_id,
            batch_state,
            created_by_service
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        "#,
    )
    .bind(settlement_batch_id)
    .bind(request_id)
    .bind(&command.idempotency_key)
    .bind(&command.external_request_id)
    .bind(command.caused_by_capsuleer_id)
    .bind(BATCH_STATE_IN_PROGRESS)
    .bind(&command.created_by_service)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn insert_settlement_steps(
    tx: &mut Transaction<'_, Postgres>,
    settlement_batch_id: Uuid,
    operations: &[SettlementCommand],
) -> Result<Vec<StepRecord>> {
    let mut records = Vec::with_capacity(operations.len());

    for (index, command) in operations.iter().enumerate() {
        let step_index = i32::try_from(index).map_err(|_| {
            SettlementError::InvalidArgument("too many settlement operations".to_string())
        })?;
        let settlement_step_id = Uuid::new_v4();
        let step_payload = serde_json::to_value(command)?;
        let step_payload_hash = checksum::hash_json(command)?;

        sqlx::query(
            r#"
            INSERT INTO settlement_step (
                settlement_step_id,
                settlement_batch_id,
                step_index,
                step_kind,
                step_payload,
                step_payload_hash,
                step_state
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            "#,
        )
        .bind(settlement_step_id)
        .bind(settlement_batch_id)
        .bind(step_index)
        .bind(command.kind_name())
        .bind(step_payload)
        .bind(step_payload_hash)
        .bind(STEP_STATE_PENDING)
        .execute(&mut **tx)
        .await?;

        records.push(StepRecord {
            step_index,
            settlement_step_id,
            step_kind: command.proto_kind(),
        });
    }

    Ok(records)
}

async fn create_business_savepoint(tx: &mut Transaction<'_, Postgres>) -> Result<()> {
    sqlx::query("SAVEPOINT settlement_business")
        .execute(&mut **tx)
        .await?;

    Ok(())
}

async fn rollback_business_savepoint(tx: &mut Transaction<'_, Postgres>) -> Result<()> {
    sqlx::query("ROLLBACK TO SAVEPOINT settlement_business")
        .execute(&mut **tx)
        .await?;

    Ok(())
}

async fn release_business_savepoint(tx: &mut Transaction<'_, Postgres>) -> Result<()> {
    sqlx::query("RELEASE SAVEPOINT settlement_business")
        .execute(&mut **tx)
        .await?;

    Ok(())
}

async fn mark_step_running(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE settlement_step
        SET step_state = $2,
            started_at = COALESCE(started_at, now())
        WHERE settlement_step_id = $1
        "#,
    )
    .bind(settlement_step_id)
    .bind(STEP_STATE_RUNNING)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn mark_step_completed(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    output: &OperationOutput,
) -> Result<()> {
    let step_output = serde_json::to_value(output)?;
    sqlx::query(
        r#"
        UPDATE settlement_step
        SET step_state = $2,
            completed_at = now(),
            step_output = $3
        WHERE settlement_step_id = $1
        "#,
    )
    .bind(settlement_step_id)
    .bind(STEP_STATE_COMPLETED)
    .bind(step_output)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn mark_step_failed(
    tx: &mut Transaction<'_, Postgres>,
    settlement_step_id: Uuid,
    failure_code: &str,
    failure_message: &str,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE settlement_step
        SET step_state = $2,
            completed_at = now(),
            failure_code = $3,
            failure_message = $4
        WHERE settlement_step_id = $1
        "#,
    )
    .bind(settlement_step_id)
    .bind(STEP_STATE_FAILED)
    .bind(failure_code)
    .bind(failure_message)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn complete_batch(
    tx: &mut Transaction<'_, Postgres>,
    settlement_batch_id: Uuid,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE settlement_batch
        SET batch_state = $2,
            completed_at = now()
        WHERE settlement_batch_id = $1
        "#,
    )
    .bind(settlement_batch_id)
    .bind(BATCH_STATE_COMPLETED)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn fail_batch(
    tx: &mut Transaction<'_, Postgres>,
    settlement_batch_id: Uuid,
    failure_code: &str,
    failure_message: &str,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE settlement_batch
        SET batch_state = $2,
            completed_at = now(),
            failure_code = $3,
            failure_message = $4
        WHERE settlement_batch_id = $1
        "#,
    )
    .bind(settlement_batch_id)
    .bind(BATCH_STATE_FAILED)
    .bind(failure_code)
    .bind(failure_message)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn complete_request_attempt(
    tx: &mut Transaction<'_, Postgres>,
    request_id: Uuid,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE request_attempt
        SET attempt_state = $2,
            completed_at = now()
        WHERE request_id = $1
        "#,
    )
    .bind(request_id)
    .bind(ATTEMPT_STATE_COMPLETED)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn fail_request_attempt(
    tx: &mut Transaction<'_, Postgres>,
    request_id: Uuid,
    failure_code: &str,
    failure_message: &str,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE request_attempt
        SET attempt_state = $2,
            completed_at = now(),
            failure_code = $3,
            failure_message = $4
        WHERE request_id = $1
        "#,
    )
    .bind(request_id)
    .bind(ATTEMPT_STATE_FAILED)
    .bind(failure_code)
    .bind(failure_message)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn complete_idempotency_record(
    tx: &mut Transaction<'_, Postgres>,
    idempotency_key: &str,
    settlement_batch_id: Uuid,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE idempotency_record
        SET idempotency_state = $2,
            completed_at = now(),
            result_settlement_batch_id = $3
        WHERE idempotency_key = $1
        "#,
    )
    .bind(idempotency_key)
    .bind(IDEMPOTENCY_STATE_COMPLETED)
    .bind(settlement_batch_id)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

async fn fail_idempotency_record(
    tx: &mut Transaction<'_, Postgres>,
    idempotency_key: &str,
    failure_code: &str,
    failure_message: &str,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE idempotency_record
        SET idempotency_state = $2,
            completed_at = now(),
            failure_code = $3,
            failure_message = $4,
            result_settlement_batch_id = NULL
        WHERE idempotency_key = $1
        "#,
    )
    .bind(idempotency_key)
    .bind(IDEMPOTENCY_STATE_FAILED)
    .bind(failure_code)
    .bind(failure_message)
    .execute(&mut **tx)
    .await?;

    Ok(())
}

#[derive(Serialize)]
struct FingerprintMaterial<'a> {
    fingerprint_format: &'static str,
    request_kind: &'static str,
    intent: SettlementIntent,
    external_request_id: &'a Option<String>,
    caused_by_capsuleer_id: Option<i64>,
    operations: &'a [SettlementCommand],
}

pub(crate) fn build_request_fingerprint(command: &ExecuteBatchCommand) -> Result<String> {
    let digest = checksum::hash_json(&FingerprintMaterial {
        fingerprint_format: REQUEST_FINGERPRINT_FORMAT,
        request_kind: REQUEST_KIND,
        intent: command.intent,
        external_request_id: &command.external_request_id,
        caused_by_capsuleer_id: command.caused_by_capsuleer_id,
        operations: &command.operations,
    })?;
    Ok(format!("{REQUEST_FINGERPRINT_PREFIX}{digest}"))
}

fn validate_request_fingerprint_assertion(
    assertion: Option<&str>,
    request_fingerprint: &str,
) -> Result<()> {
    if let Some(assertion) = assertion {
        if assertion != request_fingerprint {
            return Err(SettlementError::InvalidArgument(
                "request_fingerprint assertion does not match canonical settlement request"
                    .to_string(),
            ));
        }
    }
    Ok(())
}

fn kind_name_to_proto(kind_name: &str) -> SettlementOperationKind {
    match kind_name {
        "create_new_trade_instance_row" => SettlementOperationKind::CreateNewTradeInstanceRow,
        "modify_trade_instance_state" => SettlementOperationKind::ModifyTradeInstanceState,
        "create_new_empty_item_stack" => SettlementOperationKind::CreateNewEmptyItemStack,
        "transfer_quantity_from_item_stack_to_item_stack_escrow" => {
            SettlementOperationKind::TransferQuantityFromItemStackToItemStackEscrow
        }
        "transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner" => {
            SettlementOperationKind::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner
        }
        "transfer_quantity_from_item_stack_escrow_to_item_stack_with_previous_owner" => {
            SettlementOperationKind::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner
        }
        "merge_item_stacks_with_identical_item_type_and_identical_owner" => {
            SettlementOperationKind::MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner
        }
        "create_new_empty_wallet_escrow" => SettlementOperationKind::CreateNewEmptyWalletEscrow,
        "transfer_isk_amount_from_wallet_to_wallet_escrow" => {
            SettlementOperationKind::TransferIskAmountFromWalletToWalletEscrow
        }
        "transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner" => {
            SettlementOperationKind::TransferIskAmountFromWalletEscrowToWalletWithNewOwner
        }
        "transfer_isk_amount_from_wallet_escrow_to_wallet_with_previous_owner" => {
            SettlementOperationKind::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner
        }
        _ => SettlementOperationKind::Unspecified,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::{
        CreateNewTradeInstanceRow, ModifyTradeInstanceState, SettlementCommand,
        TransferIskAmountFromWalletEscrowToWalletWithNewOwner,
        TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner,
        TransferIskAmountFromWalletToWalletEscrow,
        TransferQuantityFromItemStackEscrowToItemStackWithNewOwner,
        TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner,
        TransferQuantityFromItemStackToItemStackEscrow,
    };
    use chrono::{Duration, Utc};
    use sqlx::{postgres::PgPoolOptions, PgPool};
    use std::sync::LazyLock;
    use tokio::sync::{Mutex, MutexGuard};

    const SELLER_ID: i64 = 1001;
    const BUYER_ID: i64 = 2002;
    const SOURCE_STACK_ID: &str = "11111111-1111-4111-8111-111111111111";
    const BUYER_STACK_ID: &str = "33333333-3333-4333-8333-333333333333";
    const OTHER_OWNER_STACK_ID: &str = "44444444-4444-4444-8444-444444444444";
    const SELLER_WALLET_ID: &str = "00000000-0000-4000-8000-000000001001";
    const BUYER_WALLET_ID: &str = "00000000-0000-4000-8000-000000002002";
    const TEST_DATABASE_URL_ENV: &str = "EVE_TRADE_TEST_DATABASE_URL";
    const TEST_MIGRATIONS: [&str; 3] = [
        include_str!("../migrations/0001_settlement_schema.sql"),
        include_str!("../migrations/0002_merge_item_stack_constraints.sql"),
        include_str!("../migrations/0003_settlement_hardening_and_outbox.sql"),
    ];
    const TEST_SEED: &str = include_str!("../seeds/local_dev_world.sql");
    static TEST_DATABASE_LOCK: LazyLock<Mutex<()>> = LazyLock::new(|| Mutex::new(()));

    struct TestDatabase {
        pool: PgPool,
        _guard: MutexGuard<'static, ()>,
    }

    fn uuid(value: u8) -> Uuid {
        Uuid::parse_str(&format!("00000000-0000-4000-8000-{value:012}")).unwrap()
    }

    fn command(total_quantity: i64) -> ExecuteBatchCommand {
        ExecuteBatchCommand {
            intent: SettlementIntent::Unspecified,
            idempotency_key: "key-1".to_string(),
            request_fingerprint: None,
            external_request_id: Some("external-1".to_string()),
            caused_by_capsuleer_id: Some(1001),
            operations: vec![SettlementCommand::CreateNewTradeInstanceRow(
                CreateNewTradeInstanceRow {
                    trade_instance_id: Some(uuid(1)),
                    trade_kind: "SELL".to_string(),
                    trade_state: "OPEN".to_string(),
                    issuer_id: 1001,
                    item_type_id: 34,
                    station_id: 60003760,
                    total_quantity,
                    unit_price_isk: 25,
                    expires_at: None,
                },
            )],
            created_by_service: "market".to_string(),
            request_id: Some(uuid(2)),
        }
    }

    fn seeded_uuid(value: &str) -> Uuid {
        Uuid::parse_str(value).unwrap()
    }

    fn create_trade_command(
        idempotency_key: &str,
        trade_instance_id: Uuid,
        total_quantity: i64,
    ) -> ExecuteBatchCommand {
        ExecuteBatchCommand {
            intent: SettlementIntent::Unspecified,
            idempotency_key: idempotency_key.to_string(),
            request_fingerprint: None,
            external_request_id: Some(format!("external-{idempotency_key}")),
            caused_by_capsuleer_id: Some(SELLER_ID),
            operations: vec![SettlementCommand::CreateNewTradeInstanceRow(
                CreateNewTradeInstanceRow {
                    trade_instance_id: Some(trade_instance_id),
                    trade_kind: "SELL".to_string(),
                    trade_state: "OPEN".to_string(),
                    issuer_id: SELLER_ID,
                    item_type_id: 34,
                    station_id: 60003760,
                    total_quantity,
                    unit_price_isk: 25,
                    expires_at: Some(Utc::now() + Duration::hours(1)),
                },
            )],
            created_by_service: "market".to_string(),
            request_id: Some(Uuid::new_v4()),
        }
    }

    fn issue_trade_command(
        idempotency_key: &str,
        trade_instance_id: Uuid,
        source_item_stack_id: Uuid,
        item_stack_escrow_id: Uuid,
        quantity: i64,
    ) -> ExecuteBatchCommand {
        ExecuteBatchCommand {
            intent: SettlementIntent::Issue,
            idempotency_key: idempotency_key.to_string(),
            request_fingerprint: None,
            external_request_id: Some(format!("external-{idempotency_key}")),
            caused_by_capsuleer_id: Some(SELLER_ID),
            operations: vec![
                SettlementCommand::CreateNewTradeInstanceRow(CreateNewTradeInstanceRow {
                    trade_instance_id: Some(trade_instance_id),
                    trade_kind: "SELL".to_string(),
                    trade_state: "OPEN".to_string(),
                    issuer_id: SELLER_ID,
                    item_type_id: 34,
                    station_id: 60003760,
                    total_quantity: quantity,
                    unit_price_isk: 25,
                    expires_at: Some(Utc::now() + Duration::hours(1)),
                }),
                SettlementCommand::TransferQuantityFromItemStackToItemStackEscrow(
                    TransferQuantityFromItemStackToItemStackEscrow {
                        source_item_stack_id,
                        item_stack_escrow_id: Some(item_stack_escrow_id),
                        trade_instance_id,
                        quantity,
                    },
                ),
            ],
            created_by_service: "market".to_string(),
            request_id: Some(Uuid::new_v4()),
        }
    }

    fn accept_trade_command(
        idempotency_key: &str,
        trade_instance_id: Uuid,
        item_stack_escrow_id: Uuid,
        wallet_escrow_id: Uuid,
        quantity: i64,
    ) -> ExecuteBatchCommand {
        let isk_amount = quantity * 25;
        ExecuteBatchCommand {
            intent: SettlementIntent::Accept,
            idempotency_key: idempotency_key.to_string(),
            request_fingerprint: None,
            external_request_id: Some(format!("external-{idempotency_key}")),
            caused_by_capsuleer_id: Some(BUYER_ID),
            operations: vec![
                SettlementCommand::TransferIskAmountFromWalletToWalletEscrow(
                    TransferIskAmountFromWalletToWalletEscrow {
                        source_wallet_id: seeded_uuid(BUYER_WALLET_ID),
                        wallet_escrow_id: Some(wallet_escrow_id),
                        trade_instance_id,
                        isk_amount,
                    },
                ),
                SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(
                    TransferQuantityFromItemStackEscrowToItemStackWithNewOwner {
                        item_stack_escrow_id,
                        destination_item_stack_id: seeded_uuid(BUYER_STACK_ID),
                        quantity,
                    },
                ),
                SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithNewOwner(
                    TransferIskAmountFromWalletEscrowToWalletWithNewOwner {
                        wallet_escrow_id,
                        destination_wallet_id: seeded_uuid(SELLER_WALLET_ID),
                        isk_amount,
                    },
                ),
                SettlementCommand::ModifyTradeInstanceState(ModifyTradeInstanceState {
                    trade_instance_id,
                    to_trade_state: "COMPLETED".to_string(),
                    trade_state_change_kind: "ACCEPTED_BY_BUYER".to_string(),
                    changed_by_service: "market".to_string(),
                }),
            ],
            created_by_service: "market".to_string(),
            request_id: Some(Uuid::new_v4()),
        }
    }

    fn fund_wallet_escrow_command(
        idempotency_key: &str,
        trade_instance_id: Uuid,
        wallet_escrow_id: Uuid,
        quantity: i64,
    ) -> ExecuteBatchCommand {
        ExecuteBatchCommand {
            intent: SettlementIntent::Unspecified,
            idempotency_key: idempotency_key.to_string(),
            request_fingerprint: None,
            external_request_id: Some(format!("external-{idempotency_key}")),
            caused_by_capsuleer_id: Some(BUYER_ID),
            operations: vec![
                SettlementCommand::TransferIskAmountFromWalletToWalletEscrow(
                    TransferIskAmountFromWalletToWalletEscrow {
                        source_wallet_id: seeded_uuid(BUYER_WALLET_ID),
                        wallet_escrow_id: Some(wallet_escrow_id),
                        trade_instance_id,
                        isk_amount: quantity * 25,
                    },
                ),
            ],
            created_by_service: "market".to_string(),
            request_id: Some(Uuid::new_v4()),
        }
    }

    fn release_wallet_escrow_to_new_owner_command(
        idempotency_key: &str,
        wallet_escrow_id: Uuid,
        quantity: i64,
    ) -> ExecuteBatchCommand {
        ExecuteBatchCommand {
            intent: SettlementIntent::Unspecified,
            idempotency_key: idempotency_key.to_string(),
            request_fingerprint: None,
            external_request_id: Some(format!("external-{idempotency_key}")),
            caused_by_capsuleer_id: Some(BUYER_ID),
            operations: vec![
                SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithNewOwner(
                    TransferIskAmountFromWalletEscrowToWalletWithNewOwner {
                        wallet_escrow_id,
                        destination_wallet_id: seeded_uuid(SELLER_WALLET_ID),
                        isk_amount: quantity * 25,
                    },
                ),
            ],
            created_by_service: "market".to_string(),
            request_id: Some(Uuid::new_v4()),
        }
    }

    fn refund_wallet_escrow_to_previous_owner_command(
        idempotency_key: &str,
        wallet_escrow_id: Uuid,
        quantity: i64,
    ) -> ExecuteBatchCommand {
        ExecuteBatchCommand {
            intent: SettlementIntent::Unspecified,
            idempotency_key: idempotency_key.to_string(),
            request_fingerprint: None,
            external_request_id: Some(format!("external-{idempotency_key}")),
            caused_by_capsuleer_id: Some(BUYER_ID),
            operations: vec![
                SettlementCommand::TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner(
                    TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner {
                        wallet_escrow_id,
                        destination_wallet_id: seeded_uuid(BUYER_WALLET_ID),
                        isk_amount: quantity * 25,
                    },
                ),
            ],
            created_by_service: "market".to_string(),
            request_id: Some(Uuid::new_v4()),
        }
    }

    fn cancel_cleanup_command(
        idempotency_key: &str,
        trade_instance_id: Uuid,
        item_stack_escrow_id: Uuid,
        quantity: i64,
    ) -> ExecuteBatchCommand {
        ExecuteBatchCommand {
            intent: SettlementIntent::Cancel,
            idempotency_key: idempotency_key.to_string(),
            request_fingerprint: None,
            external_request_id: Some(format!("external-{idempotency_key}")),
            caused_by_capsuleer_id: Some(SELLER_ID),
            operations: vec![
                SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner(
                    TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner {
                        item_stack_escrow_id,
                        destination_item_stack_id: seeded_uuid(SOURCE_STACK_ID),
                        quantity,
                    },
                ),
                SettlementCommand::ModifyTradeInstanceState(ModifyTradeInstanceState {
                    trade_instance_id,
                    to_trade_state: "CANCELLED".to_string(),
                    trade_state_change_kind: "CANCELLED_BY_ISSUER".to_string(),
                    changed_by_service: "market".to_string(),
                }),
            ],
            created_by_service: "market".to_string(),
            request_id: Some(Uuid::new_v4()),
        }
    }

    async fn test_database() -> Option<TestDatabase> {
        let database_url = match std::env::var(TEST_DATABASE_URL_ENV) {
            Ok(value) if !value.trim().is_empty() => value,
            _ => {
                eprintln!("skipping database-backed settlement test; set {TEST_DATABASE_URL_ENV}");
                return None;
            }
        };

        let guard = TEST_DATABASE_LOCK.lock().await;
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect(&database_url)
            .await
            .expect("connect to settlement test database");
        sqlx::raw_sql(
            "DROP SCHEMA IF EXISTS eve_trade_executor_test CASCADE; \
             CREATE SCHEMA eve_trade_executor_test; \
             SET search_path TO eve_trade_executor_test, public;",
        )
        .execute(&pool)
        .await
        .expect("reset isolated settlement test schema");
        for migration in TEST_MIGRATIONS {
            sqlx::raw_sql(migration)
                .execute(&pool)
                .await
                .expect("apply settlement migration");
        }
        sqlx::raw_sql(TEST_SEED)
            .execute(&pool)
            .await
            .expect("seed settlement test database");

        Some(TestDatabase {
            pool,
            _guard: guard,
        })
    }

    #[tokio::test]
    async fn settlement_commit_atomically_completes_durable_operation() {
        let Some(database) = test_database().await else {
            return;
        };
        let pool = database.pool.clone();
        let executor = SettlementExecutor::new(pool.clone());
        let idempotency_key = "db-atomic-operation-completion";
        let operation_id = Uuid::new_v4();
        sqlx::query(
            r#"
            INSERT INTO settlement_operation (
                operation_id, idempotency_key, request_fingerprint, intent,
                caused_by_capsuleer_id, operation_state
            ) VALUES ($1, $2, 'market.issue.sha256:test', 'ISSUE', $3, 'PROCESSING')
            "#,
        )
        .bind(operation_id)
        .bind(idempotency_key)
        .bind(SELLER_ID)
        .execute(&pool)
        .await
        .unwrap();
        let command = issue_trade_command(
            idempotency_key,
            Uuid::new_v4(),
            seeded_uuid(SOURCE_STACK_ID),
            Uuid::new_v4(),
            1,
        );

        let result = executor.execute_batch(command.clone()).await.unwrap();
        let row = sqlx::query_as::<_, (String, Option<Uuid>, bool)>(
            r#"
            SELECT operation_state, settlement_batch_id, result_published
            FROM settlement_operation
            WHERE operation_id = $1
            "#,
        )
        .bind(operation_id)
        .fetch_one(&pool)
        .await
        .unwrap();

        assert_eq!(row.0, "SUCCEEDED");
        assert_eq!(row.1, Some(result.settlement_batch_id));
        assert!(!row.2);

        sqlx::query(
            "UPDATE settlement_operation SET operation_state = 'PROCESSING', settlement_batch_id = NULL WHERE operation_id = $1",
        )
        .bind(operation_id)
        .execute(&pool)
        .await
        .unwrap();
        let replay = executor.execute_batch(command).await.unwrap();
        let repaired = sqlx::query_as::<_, (String, Option<Uuid>)>(
            "SELECT operation_state, settlement_batch_id FROM settlement_operation WHERE operation_id = $1",
        )
        .bind(operation_id)
        .fetch_one(&pool)
        .await
        .unwrap();

        assert!(replay.idempotent_replay);
        assert_eq!(
            repaired,
            ("SUCCEEDED".to_string(), Some(result.settlement_batch_id))
        );
    }

    async fn scalar_i64(pool: &PgPool, sql: &str, id: Uuid) -> i64 {
        sqlx::query_scalar::<_, i64>(sql)
            .bind(id)
            .fetch_one(pool)
            .await
            .unwrap()
    }

    async fn scalar_bool(pool: &PgPool, sql: &str, id: Uuid) -> bool {
        sqlx::query_scalar::<_, bool>(sql)
            .bind(id)
            .fetch_one(pool)
            .await
            .unwrap()
    }

    #[test]
    fn server_request_fingerprint_is_versioned_and_stable() {
        let command = command(4);
        let first = build_request_fingerprint(&command).unwrap();
        let second = build_request_fingerprint(&command).unwrap();

        assert_eq!(first, second);
        assert!(first.starts_with(REQUEST_FINGERPRINT_PREFIX));
    }

    #[test]
    fn server_request_fingerprint_changes_with_operation_material() {
        let first = build_request_fingerprint(&command(4)).unwrap();
        let second = build_request_fingerprint(&command(5)).unwrap();

        assert_ne!(first, second);
    }

    #[test]
    fn client_fingerprint_assertion_must_match_server_fingerprint() {
        let fingerprint = build_request_fingerprint(&command(4)).unwrap();

        validate_request_fingerprint_assertion(Some(&fingerprint), &fingerprint).unwrap();
        let error = validate_request_fingerprint_assertion(
            Some("market.issue_trade_instance.sha256:legacy"),
            &fingerprint,
        )
        .unwrap_err();

        assert_eq!(error.code(), "INVALID_ARGUMENT");
        assert!(error.to_string().contains("request_fingerprint"));
    }

    #[tokio::test]
    async fn identical_retry_replays_existing_batch_without_duplicate_effects() {
        let Some(database) = test_database().await else {
            return;
        };
        let pool = database.pool.clone();
        let executor = SettlementExecutor::new(pool.clone());
        let trade_instance_id = Uuid::new_v4();
        let item_stack_escrow_id = Uuid::new_v4();
        let command = issue_trade_command(
            "db-identical-retry",
            trade_instance_id,
            seeded_uuid(SOURCE_STACK_ID),
            item_stack_escrow_id,
            2,
        );

        let first = executor.execute_batch(command.clone()).await.unwrap();
        let second = executor.execute_batch(command).await.unwrap();

        assert!(!first.idempotent_replay);
        assert!(second.idempotent_replay);
        assert_eq!(second.settlement_batch_id, first.settlement_batch_id);
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT COUNT(*)::BIGINT FROM trade_instance WHERE trade_instance_id = $1",
                trade_instance_id,
            )
            .await,
            1
        );
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT quantity FROM item_stack_escrow WHERE item_stack_escrow_id = $1",
                item_stack_escrow_id,
            )
            .await,
            2
        );
        let batch_count = sqlx::query_scalar::<_, i64>(
            "SELECT COUNT(*)::BIGINT FROM settlement_batch WHERE idempotency_key = $1",
        )
        .bind("db-identical-retry")
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(batch_count, 1);
    }

    #[tokio::test]
    async fn same_key_different_body_conflicts_even_with_forged_repeated_client_fingerprint() {
        let Some(database) = test_database().await else {
            return;
        };
        let pool = database.pool.clone();
        let executor = SettlementExecutor::new(pool.clone());
        let key = "db-forged-fingerprint-conflict";
        let mut first = issue_trade_command(
            key,
            Uuid::new_v4(),
            seeded_uuid(SOURCE_STACK_ID),
            Uuid::new_v4(),
            2,
        );
        let forged_client_fingerprint = build_request_fingerprint(&first).unwrap();
        first.request_fingerprint = Some(forged_client_fingerprint.clone());
        executor.execute_batch(first).await.unwrap();

        let mut second = issue_trade_command(
            key,
            Uuid::new_v4(),
            seeded_uuid(SOURCE_STACK_ID),
            Uuid::new_v4(),
            3,
        );
        second.request_fingerprint = Some(forged_client_fingerprint);

        let error = executor.execute_batch(second).await.unwrap_err();
        assert_eq!(error.code(), "CONFLICT");
        assert!(error.to_string().contains("different request fingerprint"));
    }

    #[tokio::test]
    async fn client_fingerprint_assertion_mismatch_is_rejected_before_persistence() {
        let Some(database) = test_database().await else {
            return;
        };
        let pool = database.pool.clone();
        let executor = SettlementExecutor::new(pool.clone());
        let key = "db-client-assertion-mismatch";
        let mut command = create_trade_command(key, Uuid::new_v4(), 2);
        command.request_fingerprint =
            Some("market.issue_trade_instance.sha256:caller-controlled".to_string());

        let error = executor.execute_batch(command).await.unwrap_err();

        assert_eq!(error.code(), "INVALID_ARGUMENT");
        assert!(error.to_string().contains("request_fingerprint"));
        let idempotency_count = sqlx::query_scalar::<_, i64>(
            "SELECT COUNT(*)::BIGINT FROM idempotency_record WHERE idempotency_key = $1",
        )
        .bind(key)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(idempotency_count, 0);
    }

    #[tokio::test]
    async fn source_stack_owner_must_match_authoritative_trade_issuer() {
        let Some(database) = test_database().await else {
            return;
        };
        let pool = database.pool.clone();
        let executor = SettlementExecutor::new(pool.clone());
        let valid_escrow_id = Uuid::new_v4();

        executor
            .execute_batch(issue_trade_command(
                "db-valid-source-owner",
                Uuid::new_v4(),
                seeded_uuid(SOURCE_STACK_ID),
                valid_escrow_id,
                1,
            ))
            .await
            .unwrap();
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT quantity FROM item_stack_escrow WHERE item_stack_escrow_id = $1",
                valid_escrow_id,
            )
            .await,
            1
        );

        let other_stack_id = seeded_uuid(OTHER_OWNER_STACK_ID);
        let other_quantity_before = scalar_i64(
            &pool,
            "SELECT quantity FROM item_stack WHERE item_stack_id = $1",
            other_stack_id,
        )
        .await;
        let other_ledger_count_before = scalar_i64(
            &pool,
            "SELECT COUNT(*)::BIGINT FROM item_stack_ledger WHERE item_stack_id = $1",
            other_stack_id,
        )
        .await;
        let rejected_escrow_id = Uuid::new_v4();
        let error = executor
            .execute_batch(issue_trade_command(
                "db-cross-source-owner",
                Uuid::new_v4(),
                other_stack_id,
                rejected_escrow_id,
                1,
            ))
            .await
            .unwrap_err();

        assert_eq!(error.code(), "PERMISSION_DENIED");
        assert!(error.to_string().contains("source item stack"));
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT quantity FROM item_stack WHERE item_stack_id = $1",
                other_stack_id
            )
            .await,
            other_quantity_before
        );
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT COUNT(*)::BIGINT FROM item_stack_ledger WHERE item_stack_id = $1",
                other_stack_id,
            )
            .await,
            other_ledger_count_before
        );
        assert!(
            !scalar_bool(
                &pool,
                "SELECT EXISTS (SELECT 1 FROM item_stack_escrow WHERE item_stack_escrow_id = $1)",
                rejected_escrow_id,
            )
            .await
        );
    }

    #[tokio::test]
    async fn actor_cannot_debit_another_capsuleers_wallet() {
        let Some(database) = test_database().await else {
            return;
        };
        let executor = SettlementExecutor::new(database.pool.clone());
        let trade_instance_id = Uuid::new_v4();
        let item_stack_escrow_id = Uuid::new_v4();
        executor
            .execute_batch(issue_trade_command(
                "db-wallet-auth-issue",
                trade_instance_id,
                seeded_uuid(SOURCE_STACK_ID),
                item_stack_escrow_id,
                1,
            ))
            .await
            .unwrap();
        let mut command = accept_trade_command(
            "db-wallet-auth-accept",
            trade_instance_id,
            item_stack_escrow_id,
            Uuid::new_v4(),
            1,
        );
        command.caused_by_capsuleer_id = Some(SELLER_ID);

        let error = executor.execute_batch(command).await.unwrap_err();
        assert_eq!(error.code(), "PERMISSION_DENIED");
        assert!(error.to_string().contains("debited wallet"));
    }

    #[tokio::test]
    async fn actor_cannot_cancel_another_capsuleers_trade() {
        let Some(database) = test_database().await else {
            return;
        };
        let executor = SettlementExecutor::new(database.pool.clone());
        let trade_instance_id = Uuid::new_v4();
        let item_stack_escrow_id = Uuid::new_v4();
        executor
            .execute_batch(issue_trade_command(
                "db-cancel-auth-issue",
                trade_instance_id,
                seeded_uuid(SOURCE_STACK_ID),
                item_stack_escrow_id,
                1,
            ))
            .await
            .unwrap();
        let mut command = cancel_cleanup_command(
            "db-cancel-auth-cancel",
            trade_instance_id,
            item_stack_escrow_id,
            1,
        );
        command.caused_by_capsuleer_id = Some(BUYER_ID);

        let error = executor.execute_batch(command).await.unwrap_err();
        assert_eq!(error.code(), "PERMISSION_DENIED");
        assert!(error.to_string().contains("trade issuer"));
    }

    #[tokio::test]
    async fn accept_destination_must_belong_to_accepting_actor() {
        let Some(database) = test_database().await else {
            return;
        };
        let executor = SettlementExecutor::new(database.pool.clone());
        let trade_instance_id = Uuid::new_v4();
        let item_stack_escrow_id = Uuid::new_v4();
        executor
            .execute_batch(issue_trade_command(
                "db-destination-auth-issue",
                trade_instance_id,
                seeded_uuid(SOURCE_STACK_ID),
                item_stack_escrow_id,
                1,
            ))
            .await
            .unwrap();
        let mut command = accept_trade_command(
            "db-destination-auth-accept",
            trade_instance_id,
            item_stack_escrow_id,
            Uuid::new_v4(),
            1,
        );
        for operation in &mut command.operations {
            if let SettlementCommand::TransferQuantityFromItemStackEscrowToItemStackWithNewOwner(
                value,
            ) = operation
            {
                value.destination_item_stack_id = seeded_uuid(OTHER_OWNER_STACK_ID);
            }
        }

        let error = executor.execute_batch(command).await.unwrap_err();
        assert_eq!(error.code(), "PERMISSION_DENIED");
        assert!(error.to_string().contains("destination item stack"));
    }

    #[tokio::test]
    async fn live_trade_acceptance_completes_when_executed_before_expiry() {
        let Some(database) = test_database().await else {
            return;
        };
        let pool = database.pool.clone();
        let executor = SettlementExecutor::new(pool.clone());
        let trade_instance_id = Uuid::new_v4();
        let item_stack_escrow_id = Uuid::new_v4();
        let wallet_escrow_id = Uuid::new_v4();

        executor
            .execute_batch(issue_trade_command(
                "db-live-issue",
                trade_instance_id,
                seeded_uuid(SOURCE_STACK_ID),
                item_stack_escrow_id,
                2,
            ))
            .await
            .unwrap();
        executor
            .execute_batch(accept_trade_command(
                "db-live-accept",
                trade_instance_id,
                item_stack_escrow_id,
                wallet_escrow_id,
                2,
            ))
            .await
            .unwrap();

        let trade_state = sqlx::query_scalar::<_, String>(
            "SELECT trade_state FROM trade_instance WHERE trade_instance_id = $1",
        )
        .bind(trade_instance_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(trade_state, "COMPLETED");
        assert!(
            scalar_bool(
                &pool,
                "SELECT is_released FROM item_stack_escrow WHERE item_stack_escrow_id = $1",
                item_stack_escrow_id,
            )
            .await
        );
        assert!(
            scalar_bool(
                &pool,
                "SELECT is_released FROM wallet_escrow WHERE wallet_escrow_id = $1",
                wallet_escrow_id,
            )
            .await
        );
    }

    #[tokio::test]
    async fn expired_trade_rejects_new_owner_wallet_release_but_allows_refund_cleanup() {
        let Some(database) = test_database().await else {
            return;
        };
        let pool = database.pool.clone();
        let executor = SettlementExecutor::new(pool.clone());
        let trade_instance_id = Uuid::new_v4();
        let item_stack_escrow_id = Uuid::new_v4();
        let wallet_escrow_id = Uuid::new_v4();

        executor
            .execute_batch(issue_trade_command(
                "db-wallet-release-expiring-issue",
                trade_instance_id,
                seeded_uuid(SOURCE_STACK_ID),
                item_stack_escrow_id,
                2,
            ))
            .await
            .unwrap();
        executor
            .execute_batch(fund_wallet_escrow_command(
                "db-wallet-release-funded-before-expiry",
                trade_instance_id,
                wallet_escrow_id,
                2,
            ))
            .await
            .unwrap();

        let seller_wallet_before = scalar_i64(
            &pool,
            "SELECT isk_amount FROM wallet WHERE wallet_id = $1",
            seeded_uuid(SELLER_WALLET_ID),
        )
        .await;
        let buyer_wallet_after_fund = scalar_i64(
            &pool,
            "SELECT isk_amount FROM wallet WHERE wallet_id = $1",
            seeded_uuid(BUYER_WALLET_ID),
        )
        .await;
        sqlx::query(
            "UPDATE trade_instance SET expires_at = clock_timestamp() - INTERVAL '1 second' WHERE trade_instance_id = $1",
        )
        .bind(trade_instance_id)
        .execute(&pool)
        .await
        .unwrap();

        let error = executor
            .execute_batch(release_wallet_escrow_to_new_owner_command(
                "db-new-owner-wallet-release-after-expiry",
                wallet_escrow_id,
                2,
            ))
            .await
            .unwrap_err();

        assert_eq!(error.code(), "FAILED_PRECONDITION");
        assert!(error.to_string().contains("expired"));
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT isk_amount FROM wallet WHERE wallet_id = $1",
                seeded_uuid(SELLER_WALLET_ID),
            )
            .await,
            seller_wallet_before
        );
        assert!(
            !scalar_bool(
                &pool,
                "SELECT is_released FROM wallet_escrow WHERE wallet_escrow_id = $1",
                wallet_escrow_id,
            )
            .await
        );

        executor
            .execute_batch(refund_wallet_escrow_to_previous_owner_command(
                "db-refund-wallet-escrow-after-expiry",
                wallet_escrow_id,
                2,
            ))
            .await
            .unwrap();
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT isk_amount FROM wallet WHERE wallet_id = $1",
                seeded_uuid(BUYER_WALLET_ID),
            )
            .await,
            buyer_wallet_after_fund + 50
        );
        assert!(
            scalar_bool(
                &pool,
                "SELECT is_released FROM wallet_escrow WHERE wallet_escrow_id = $1",
                wallet_escrow_id,
            )
            .await
        );
    }

    #[tokio::test]
    async fn expired_trade_rejects_live_acceptance_but_allows_previous_owner_cleanup() {
        let Some(database) = test_database().await else {
            return;
        };
        let pool = database.pool.clone();
        let executor = SettlementExecutor::new(pool.clone());
        let trade_instance_id = Uuid::new_v4();
        let item_stack_escrow_id = Uuid::new_v4();
        let wallet_escrow_id = Uuid::new_v4();

        executor
            .execute_batch(issue_trade_command(
                "db-expiring-issue",
                trade_instance_id,
                seeded_uuid(SOURCE_STACK_ID),
                item_stack_escrow_id,
                2,
            ))
            .await
            .unwrap();
        let accept_command = accept_trade_command(
            "db-accept-after-expiry",
            trade_instance_id,
            item_stack_escrow_id,
            wallet_escrow_id,
            2,
        );
        let buyer_wallet_before = scalar_i64(
            &pool,
            "SELECT isk_amount FROM wallet WHERE wallet_id = $1",
            seeded_uuid(BUYER_WALLET_ID),
        )
        .await;
        let source_quantity_after_issue = scalar_i64(
            &pool,
            "SELECT quantity FROM item_stack WHERE item_stack_id = $1",
            seeded_uuid(SOURCE_STACK_ID),
        )
        .await;
        let escrow_quantity_after_issue = scalar_i64(
            &pool,
            "SELECT quantity FROM item_stack_escrow WHERE item_stack_escrow_id = $1",
            item_stack_escrow_id,
        )
        .await;
        sqlx::query(
            "UPDATE trade_instance SET expires_at = clock_timestamp() - INTERVAL '1 second' WHERE trade_instance_id = $1",
        )
        .bind(trade_instance_id)
        .execute(&pool)
        .await
        .unwrap();

        let error = executor.execute_batch(accept_command).await.unwrap_err();

        assert_eq!(error.code(), "FAILED_PRECONDITION");
        assert!(error.to_string().contains("expired"));
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT isk_amount FROM wallet WHERE wallet_id = $1",
                seeded_uuid(BUYER_WALLET_ID),
            )
            .await,
            buyer_wallet_before
        );
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT quantity FROM item_stack_escrow WHERE item_stack_escrow_id = $1",
                item_stack_escrow_id,
            )
            .await,
            escrow_quantity_after_issue
        );
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT quantity FROM item_stack WHERE item_stack_id = $1",
                seeded_uuid(SOURCE_STACK_ID)
            )
            .await,
            source_quantity_after_issue
        );
        assert!(
            !scalar_bool(
                &pool,
                "SELECT EXISTS (SELECT 1 FROM wallet_escrow WHERE wallet_escrow_id = $1)",
                wallet_escrow_id,
            )
            .await
        );

        executor
            .execute_batch(cancel_cleanup_command(
                "db-expired-cleanup",
                trade_instance_id,
                item_stack_escrow_id,
                2,
            ))
            .await
            .unwrap();
        let trade_state = sqlx::query_scalar::<_, String>(
            "SELECT trade_state FROM trade_instance WHERE trade_instance_id = $1",
        )
        .bind(trade_instance_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(trade_state, "CANCELLED");
        assert!(
            scalar_bool(
                &pool,
                "SELECT is_released FROM item_stack_escrow WHERE item_stack_escrow_id = $1",
                item_stack_escrow_id,
            )
            .await
        );
        assert_eq!(
            scalar_i64(
                &pool,
                "SELECT quantity FROM item_stack WHERE item_stack_id = $1",
                seeded_uuid(SOURCE_STACK_ID)
            )
            .await,
            100
        );
    }
}
