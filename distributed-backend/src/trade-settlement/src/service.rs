use std::pin::Pin;

use chrono::{DateTime, Utc};
use prost_types::Timestamp;
use sqlx::FromRow;
use summer::plugin::service::Service;
use summer_sqlx::ConnectPool;

use tonic::codegen::tokio_stream::Stream;
use tonic::{Request, Response, Status};
use tracing::{info_span, Instrument};

use crate::commands::ExecuteBatchCommand;
use crate::executor::BatchExecutionResult;
use crate::executor::SettlementExecutor;
use crate::proto::health;
use crate::proto::trade_settlement as pb;
use health::health_server::{Health, HealthServer};
use pb::trade_settlement_service_server::{TradeSettlementService, TradeSettlementServiceServer};

#[derive(Clone, Service)]
#[service(grpc = "TradeSettlementServiceServer")]
pub struct TradeSettlementGrpc {
    #[inject(component)]
    db: ConnectPool,
}

pub fn ensure_linked() {}

#[derive(Clone, Service)]
#[service(grpc = "HealthServer")]
pub struct SettlementHealthGrpc {
    #[inject(component)]
    db: ConnectPool,
}

#[tonic::async_trait]
impl Health for SettlementHealthGrpc {
    async fn check(
        &self,
        request: Request<health::HealthCheckRequest>,
    ) -> std::result::Result<Response<health::HealthCheckResponse>, Status> {
        let service = request.into_inner().service;
        let serving = match service.as_str() {
            "" | "liveness" => true,
            "readiness" | "eve.trade_settlement.v1.TradeSettlementService" => {
                sqlx::query_scalar::<_, bool>(
                    "SELECT to_regclass('public.settlement_batch') IS NOT NULL",
                )
                .fetch_one(&self.db)
                .await
                .map_err(|error| {
                    Status::unavailable(format!("settlement database unavailable: {error}"))
                })?
            }
            _ => {
                return Ok(Response::new(health::HealthCheckResponse {
                    status: health::health_check_response::ServingStatus::ServiceUnknown as i32,
                }));
            }
        };
        Ok(Response::new(health::HealthCheckResponse {
            status: if serving {
                health::health_check_response::ServingStatus::Serving as i32
            } else {
                health::health_check_response::ServingStatus::NotServing as i32
            },
        }))
    }

    type WatchStream = Pin<
        Box<dyn Stream<Item = std::result::Result<health::HealthCheckResponse, Status>> + Send>,
    >;

    async fn watch(
        &self,
        _request: Request<health::HealthCheckRequest>,
    ) -> std::result::Result<Response<Self::WatchStream>, Status> {
        Err(Status::unimplemented(
            "streaming health watch is not supported",
        ))
    }
}

#[tonic::async_trait]
impl TradeSettlementService for TradeSettlementGrpc {
    async fn execute_settlement_batch(
        &self,
        request: Request<pb::ExecuteSettlementBatchRequest>,
    ) -> std::result::Result<Response<pb::ExecuteSettlementBatchResponse>, Status> {
        let receive_span = info_span!(
            "settlement.receive_batch",
            idempotency_key = %request.get_ref().idempotency_key,
            operation_count = request.get_ref().operations.len(),
        );
        let command = ExecuteBatchCommand::try_from(request.into_inner())
            .map_err(|error| error.into_status())?;
        let executor = SettlementExecutor::new(self.db.clone());
        let result = executor
            .execute_batch(command)
            .instrument(receive_span)
            .await
            .map_err(|error| error.into_status())?;

        Ok(Response::new(result.into()))
    }

    async fn queue_settlement_operation(
        &self,
        request: Request<pb::QueueSettlementOperationRequest>,
    ) -> std::result::Result<Response<pb::QueueSettlementOperationResponse>, Status> {
        let request = request.into_inner();
        let intent = intent_name(request.intent)?;
        if request.idempotency_key.trim().is_empty()
            || request.request_fingerprint.trim().is_empty()
            || request.caused_by_capsuleer_id <= 0
        {
            return Err(Status::invalid_argument(
                "idempotency_key, request_fingerprint, and actor are required",
            ));
        }
        let mut tx = self.db.begin().await.map_err(internal_database_status)?;
        let existing = sqlx::query_as::<_, OperationRow>(
            "SELECT * FROM settlement_operation WHERE idempotency_key = $1 FOR UPDATE",
        )
        .bind(&request.idempotency_key)
        .fetch_optional(&mut *tx)
        .await
        .map_err(internal_database_status)?;
        let row = if let Some(existing) = existing {
            if existing.request_fingerprint != request.request_fingerprint
                || existing.intent != intent
                || existing.caused_by_capsuleer_id != request.caused_by_capsuleer_id
            {
                return Err(Status::aborted(
                    "idempotency_key was already queued with a different operation fingerprint",
                ));
            }
            existing
        } else {
            sqlx::query_as::<_, OperationRow>(
                r#"
                INSERT INTO settlement_operation (
                    operation_id, idempotency_key, request_fingerprint, intent,
                    caused_by_capsuleer_id, external_request_id, operation_state
                ) VALUES ($1, $2, $3, $4, $5, NULLIF($6, ''), 'QUEUED')
                RETURNING *
                "#,
            )
            .bind(uuid::Uuid::new_v4())
            .bind(&request.idempotency_key)
            .bind(&request.request_fingerprint)
            .bind(intent)
            .bind(request.caused_by_capsuleer_id)
            .bind(&request.external_request_id)
            .fetch_one(&mut *tx)
            .await
            .map_err(internal_database_status)?
        };
        tx.commit().await.map_err(internal_database_status)?;
        Ok(Response::new(pb::QueueSettlementOperationResponse {
            operation: Some(row.into()),
        }))
    }

    async fn get_settlement_operation(
        &self,
        request: Request<pb::GetSettlementOperationRequest>,
    ) -> std::result::Result<Response<pb::GetSettlementOperationResponse>, Status> {
        let operation_id = uuid::Uuid::parse_str(&request.into_inner().operation_id)
            .map_err(|_| Status::invalid_argument("operation_id must be a UUID"))?;
        let row = sqlx::query_as::<_, OperationRow>(
            "SELECT * FROM settlement_operation WHERE operation_id = $1",
        )
        .bind(operation_id)
        .fetch_optional(&self.db)
        .await
        .map_err(internal_database_status)?
        .ok_or_else(|| Status::not_found(format!("settlement operation {operation_id}")))?;
        Ok(Response::new(pb::GetSettlementOperationResponse {
            operation: Some(row.into()),
        }))
    }

    async fn update_settlement_operation(
        &self,
        request: Request<pb::UpdateSettlementOperationRequest>,
    ) -> std::result::Result<Response<pb::UpdateSettlementOperationResponse>, Status> {
        let request = request.into_inner();
        let operation_id = uuid::Uuid::parse_str(&request.operation_id)
            .map_err(|_| Status::invalid_argument("operation_id must be a UUID"))?;
        let next_state = operation_state_name(request.state)?;
        let settlement_batch_id = if request.settlement_batch_id.trim().is_empty() {
            None
        } else {
            Some(
                uuid::Uuid::parse_str(&request.settlement_batch_id)
                    .map_err(|_| Status::invalid_argument("settlement_batch_id must be a UUID"))?,
            )
        };
        let mut tx = self.db.begin().await.map_err(internal_database_status)?;
        let current = sqlx::query_as::<_, OperationRow>(
            "SELECT * FROM settlement_operation WHERE operation_id = $1 FOR UPDATE",
        )
        .bind(operation_id)
        .fetch_optional(&mut *tx)
        .await
        .map_err(internal_database_status)?
        .ok_or_else(|| Status::not_found(format!("settlement operation {operation_id}")))?;
        if !valid_operation_transition(&current.operation_state, next_state) {
            return Err(Status::failed_precondition(format!(
                "invalid settlement operation transition {} -> {next_state}",
                current.operation_state
            )));
        }
        if next_state == "SUCCEEDED"
            && settlement_batch_id
                .or(current.settlement_batch_id)
                .is_none()
        {
            return Err(Status::invalid_argument(
                "SUCCEEDED operation requires settlement_batch_id",
            ));
        }
        if next_state == "FAILED" && request.failure_code.trim().is_empty() {
            return Err(Status::invalid_argument(
                "FAILED operation requires failure_code",
            ));
        }
        let row = sqlx::query_as::<_, OperationRow>(
            r#"
            UPDATE settlement_operation
            SET operation_state = $2,
                settlement_batch_id = COALESCE($3, settlement_batch_id),
                failure_code = NULLIF($4, ''),
                failure_description = NULLIF($5, ''),
                result_published = result_published OR $6,
                updated_at = now(),
                completed_at = CASE WHEN $2 IN ('SUCCEEDED', 'FAILED', 'CANCELLED', 'EXPIRED')
                    THEN COALESCE(completed_at, now()) ELSE completed_at END
            WHERE operation_id = $1
            RETURNING *
            "#,
        )
        .bind(operation_id)
        .bind(next_state)
        .bind(settlement_batch_id)
        .bind(&request.failure_code)
        .bind(&request.failure_description)
        .bind(request.result_published)
        .fetch_one(&mut *tx)
        .await
        .map_err(internal_database_status)?;
        tx.commit().await.map_err(internal_database_status)?;
        Ok(Response::new(pb::UpdateSettlementOperationResponse {
            operation: Some(row.into()),
        }))
    }
}

#[derive(Debug, FromRow)]
struct OperationRow {
    operation_id: uuid::Uuid,
    idempotency_key: String,
    request_fingerprint: String,
    intent: String,
    caused_by_capsuleer_id: i64,
    operation_state: String,
    settlement_batch_id: Option<uuid::Uuid>,
    failure_code: Option<String>,
    failure_description: Option<String>,
    result_published: bool,
    queued_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
}

impl From<OperationRow> for pb::SettlementOperationStatus {
    fn from(value: OperationRow) -> Self {
        Self {
            operation_id: value.operation_id.to_string(),
            idempotency_key: value.idempotency_key,
            request_fingerprint: value.request_fingerprint,
            intent: intent_value(&value.intent) as i32,
            caused_by_capsuleer_id: value.caused_by_capsuleer_id,
            state: operation_state_value(&value.operation_state) as i32,
            queued_at: Some(timestamp(value.queued_at)),
            updated_at: Some(timestamp(value.updated_at)),
            settlement_batch_id: value
                .settlement_batch_id
                .map(|id| id.to_string())
                .unwrap_or_default(),
            failure_code: value.failure_code.unwrap_or_default(),
            failure_description: value.failure_description.unwrap_or_default(),
            result_published: value.result_published,
        }
    }
}

fn intent_name(value: i32) -> std::result::Result<&'static str, Status> {
    match pb::SettlementIntent::try_from(value).ok() {
        Some(pb::SettlementIntent::Issue) => Ok("ISSUE"),
        Some(pb::SettlementIntent::Accept) => Ok("ACCEPT"),
        Some(pb::SettlementIntent::Cancel) => Ok("CANCEL"),
        _ => Err(Status::invalid_argument("settlement intent is required")),
    }
}

fn intent_value(value: &str) -> pb::SettlementIntent {
    match value {
        "ISSUE" => pb::SettlementIntent::Issue,
        "ACCEPT" => pb::SettlementIntent::Accept,
        "CANCEL" => pb::SettlementIntent::Cancel,
        _ => pb::SettlementIntent::Unspecified,
    }
}

fn operation_state_name(value: i32) -> std::result::Result<&'static str, Status> {
    match pb::SettlementOperationState::try_from(value).ok() {
        Some(pb::SettlementOperationState::Queued) => Ok("QUEUED"),
        Some(pb::SettlementOperationState::Processing) => Ok("PROCESSING"),
        Some(pb::SettlementOperationState::Succeeded) => Ok("SUCCEEDED"),
        Some(pb::SettlementOperationState::Failed) => Ok("FAILED"),
        Some(pb::SettlementOperationState::Cancelled) => Ok("CANCELLED"),
        Some(pb::SettlementOperationState::Expired) => Ok("EXPIRED"),
        _ => Err(Status::invalid_argument(
            "settlement operation state is required",
        )),
    }
}

fn operation_state_value(value: &str) -> pb::SettlementOperationState {
    match value {
        "QUEUED" => pb::SettlementOperationState::Queued,
        "PROCESSING" => pb::SettlementOperationState::Processing,
        "SUCCEEDED" => pb::SettlementOperationState::Succeeded,
        "FAILED" => pb::SettlementOperationState::Failed,
        "CANCELLED" => pb::SettlementOperationState::Cancelled,
        "EXPIRED" => pb::SettlementOperationState::Expired,
        _ => pb::SettlementOperationState::Unspecified,
    }
}

fn valid_operation_transition(current: &str, next: &str) -> bool {
    current == next
        || matches!(
            (current, next),
            ("QUEUED", "PROCESSING" | "FAILED" | "CANCELLED" | "EXPIRED")
                | (
                    "PROCESSING",
                    "SUCCEEDED" | "FAILED" | "CANCELLED" | "EXPIRED"
                )
        )
}

fn timestamp(value: DateTime<Utc>) -> Timestamp {
    Timestamp {
        seconds: value.timestamp(),
        nanos: value.timestamp_subsec_nanos() as i32,
    }
}

fn internal_database_status(error: sqlx::Error) -> Status {
    Status::internal(format!("settlement operation database error: {error}"))
}

impl From<BatchExecutionResult> for pb::ExecuteSettlementBatchResponse {
    fn from(value: BatchExecutionResult) -> Self {
        Self {
            settlement_batch_id: value.settlement_batch_id.to_string(),
            idempotency_key: value.idempotency_key,
            batch_state: value.batch_state,
            idempotent_replay: value.idempotent_replay,
            step_results: value
                .step_results
                .into_iter()
                .map(|step| pb::SettlementStepResult {
                    step_index: step.step_index,
                    settlement_step_id: step.settlement_step_id.to_string(),
                    step_kind: step.step_kind as i32,
                    outputs: step
                        .output
                        .entity_references
                        .into_iter()
                        .map(|reference| pb::EntityReference {
                            entity_kind: reference.entity_kind.to_string(),
                            entity_id: reference.entity_id.to_string(),
                        })
                        .collect(),
                })
                .collect(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::executor::StepExecutionResult;
    use crate::operations::{EntityReferenceOutput, OperationOutput};
    use pb::SettlementOperationKind;
    use uuid::Uuid;

    fn uuid(value: u128) -> Uuid {
        Uuid::from_u128(value)
    }

    #[test]
    fn response_conversion_preserves_batch_steps_and_entity_references() {
        let batch_id = uuid(1);
        let first_step_id = uuid(2);
        let second_step_id = uuid(3);
        let entity_id = uuid(4);
        let response: pb::ExecuteSettlementBatchResponse = BatchExecutionResult {
            settlement_batch_id: batch_id,
            idempotency_key: "settlement-key".to_string(),
            batch_state: "COMPLETED".to_string(),
            idempotent_replay: true,
            step_results: vec![
                StepExecutionResult {
                    step_index: 7,
                    settlement_step_id: first_step_id,
                    step_kind: SettlementOperationKind::CreateNewTradeInstanceRow,
                    output: OperationOutput {
                        entity_references: vec![EntityReferenceOutput {
                            entity_kind: "trade_instance".to_string(),
                            entity_id,
                        }],
                    },
                },
                StepExecutionResult {
                    step_index: 8,
                    settlement_step_id: second_step_id,
                    step_kind: SettlementOperationKind::ModifyTradeInstanceState,
                    output: OperationOutput::default(),
                },
            ],
        }
        .into();

        assert_eq!(response.settlement_batch_id, batch_id.to_string());
        assert_eq!(response.idempotency_key, "settlement-key");
        assert_eq!(response.batch_state, "COMPLETED");
        assert!(response.idempotent_replay);
        assert_eq!(response.step_results.len(), 2);

        let first = &response.step_results[0];
        assert_eq!(first.step_index, 7);
        assert_eq!(first.settlement_step_id, first_step_id.to_string());
        assert_eq!(
            first.step_kind,
            SettlementOperationKind::CreateNewTradeInstanceRow as i32
        );
        assert_eq!(first.outputs.len(), 1);
        assert_eq!(first.outputs[0].entity_kind, "trade_instance");
        assert_eq!(first.outputs[0].entity_id, entity_id.to_string());

        let second = &response.step_results[1];
        assert_eq!(second.step_index, 8);
        assert_eq!(second.settlement_step_id, second_step_id.to_string());
        assert_eq!(
            second.step_kind,
            SettlementOperationKind::ModifyTradeInstanceState as i32
        );
        assert!(second.outputs.is_empty());
    }

    #[test]
    fn response_conversion_does_not_invent_steps_for_an_empty_result() {
        let response: pb::ExecuteSettlementBatchResponse = BatchExecutionResult {
            settlement_batch_id: uuid(5),
            idempotency_key: "empty-key".to_string(),
            batch_state: "COMPLETED".to_string(),
            idempotent_replay: false,
            step_results: Vec::new(),
        }
        .into();

        assert_eq!(response.idempotency_key, "empty-key");
        assert!(!response.idempotent_replay);
        assert!(response.step_results.is_empty());
    }
}
