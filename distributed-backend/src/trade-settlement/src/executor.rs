use serde::Serialize;
use sqlx::{PgPool, Postgres, Transaction};
use uuid::Uuid;

use crate::checksum;
use crate::commands::{ExecuteBatchCommand, SettlementCommand};
use crate::error::{Result, SettlementError};
use crate::operations::{execute_settlement_command, OperationOutput};
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
}

impl SettlementExecutor {
    pub fn new(db: PgPool) -> Self {
        Self { db }
    }

    pub async fn execute_batch(
        &self,
        command: ExecuteBatchCommand,
    ) -> Result<BatchExecutionResult> {
        if command.operations.is_empty() {
            return Err(SettlementError::InvalidArgument(
                "operations must contain at least one settlement operation".to_string(),
            ));
        }

        let request_fingerprint = match command.request_fingerprint.clone() {
            Some(fingerprint) => fingerprint,
            None => build_request_fingerprint(&command)?,
        };

        let mut tx = self.db.begin().await?;

        if let Some(existing) = lock_idempotency_record(&mut tx, &command.idempotency_key).await? {
            if existing.request_fingerprint != request_fingerprint {
                return Err(SettlementError::Conflict(format!(
                    "idempotency_key {} was already used with a different request fingerprint",
                    command.idempotency_key
                )));
            }

            match existing.idempotency_state.as_str() {
                IDEMPOTENCY_STATE_COMPLETED => {
                    tx.rollback().await?;
                    let batch_id = existing.result_settlement_batch_id.ok_or_else(|| {
                        SettlementError::FailedPrecondition(format!(
                            "idempotency_key {} completed without a result batch",
                            command.idempotency_key
                        ))
                    })?;
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
            insert_idempotency_record(
                &mut tx,
                &command.idempotency_key,
                &request_fingerprint,
                &command.created_by_service,
            )
            .await?;
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
        insert_settlement_batch(&mut tx, settlement_batch_id, request_id, &command).await?;

        let step_records =
            insert_settlement_steps(&mut tx, settlement_batch_id, &command.operations).await?;

        create_business_savepoint(&mut tx).await?;
        let mut step_results = Vec::with_capacity(command.operations.len());
        for (operation, step) in command.operations.iter().zip(step_records.iter()) {
            mark_step_running(&mut tx, step.settlement_step_id).await?;
            let output =
                match execute_settlement_command(&mut tx, step.settlement_step_id, operation).await
                {
                    Ok(output) => output,
                    Err(error) => {
                        let failure_code = error.code().to_string();
                        let failure_message = error.to_string();
                        let attempted_steps = step_results.len() + 1;
                        rollback_business_savepoint(&mut tx).await?;
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
                        .await?;
                        tx.commit().await?;
                        return Err(error);
                    }
                };
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
            &step_records,
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
                   step_kind
            FROM settlement_step
            WHERE settlement_batch_id = $1
            ORDER BY step_index
            "#,
        )
        .bind(settlement_batch_id)
        .fetch_all(&self.db)
        .await?;

        Ok(rows
            .into_iter()
            .map(|row| StepExecutionResult {
                step_index: row.step_index as u32,
                settlement_step_id: row.settlement_step_id,
                step_kind: kind_name_to_proto(&row.step_kind),
                output: OperationOutput::default(),
            })
            .collect())
    }
}

async fn complete_execution(
    tx: &mut Transaction<'_, Postgres>,
    settlement_batch_id: Uuid,
    request_id: Uuid,
    idempotency_key: &str,
    step_records: &[StepRecord],
) -> Result<()> {
    for step in step_records {
        mark_step_completed(tx, step.settlement_step_id).await?;
    }
    complete_batch(tx, settlement_batch_id).await?;
    complete_request_attempt(tx, request_id).await?;
    complete_idempotency_record(tx, idempotency_key, settlement_batch_id).await?;
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
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE settlement_step
        SET step_state = $2,
            completed_at = now()
        WHERE settlement_step_id = $1
        "#,
    )
    .bind(settlement_step_id)
    .bind(STEP_STATE_COMPLETED)
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
    request_kind: &'static str,
    external_request_id: &'a Option<String>,
    caused_by_capsuleer_id: Option<i64>,
    operations: &'a [SettlementCommand],
}

fn build_request_fingerprint(command: &ExecuteBatchCommand) -> Result<String> {
    checksum::hash_json(&FingerprintMaterial {
        request_kind: REQUEST_KIND,
        external_request_id: &command.external_request_id,
        caused_by_capsuleer_id: command.caused_by_capsuleer_id,
        operations: &command.operations,
    })
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
