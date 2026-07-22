use std::pin::Pin;

use chrono::{DateTime, Duration, SecondsFormat, Utc};
use prost_types::Timestamp;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use sqlx::types::Json;
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

const MAX_SETTLEMENT_LEASE_DURATION: Duration = Duration::minutes(5);

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
        let (work_payload, work_payload_hash) = validated_work_payload(&request, intent)?;
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
                || existing
                    .work_payload_hash
                    .as_deref()
                    .is_some_and(|hash| hash != work_payload_hash)
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
                    caused_by_capsuleer_id, external_request_id, operation_state,
                    work_payload_hash
                ) VALUES ($1, $2, $3, $4, $5, NULLIF($6, ''), 'QUEUED', $7)
                RETURNING *
                "#,
            )
            .bind(uuid::Uuid::new_v4())
            .bind(&request.idempotency_key)
            .bind(&request.request_fingerprint)
            .bind(intent)
            .bind(request.caused_by_capsuleer_id)
            .bind(&request.external_request_id)
            .bind(&work_payload_hash)
            .fetch_one(&mut *tx)
            .await
            .map_err(internal_database_status)?
        };
        let canonical_payload =
            canonical_work_payload(work_payload, row.operation_id, row.queued_at);
        let row = sqlx::query_as::<_, OperationRow>(
            r#"
            UPDATE settlement_operation
            SET work_payload = COALESCE(work_payload, $2),
                work_payload_hash = COALESCE(work_payload_hash, $3),
                updated_at = CASE WHEN work_payload IS NULL THEN now() ELSE updated_at END
            WHERE operation_id = $1
            RETURNING *
            "#,
        )
        .bind(row.operation_id)
        .bind(Json(canonical_payload))
        .bind(&work_payload_hash)
        .fetch_one(&mut *tx)
        .await
        .map_err(internal_database_status)?;
        sqlx::query(
            r#"
            INSERT INTO settlement_outbox (operation_id)
            VALUES ($1)
            ON CONFLICT (operation_id) DO NOTHING
            "#,
        )
        .bind(row.operation_id)
        .execute(&mut *tx)
        .await
        .map_err(internal_database_status)?;
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
        let requested_owner = request.lease_owner.trim();
        let requested_generation = i64::try_from(request.lease_generation)
            .map_err(|_| Status::invalid_argument("lease_generation exceeds BIGINT range"))?;
        let requested_expiry = optional_timestamp(request.lease_expires_at)?;
        let now = Utc::now();
        validate_lease_transition(
            &current,
            next_state,
            requested_owner,
            requested_generation,
            requested_expiry,
            now,
        )?;
        let row = sqlx::query_as::<_, OperationRow>(
            r#"
            UPDATE settlement_operation
            SET operation_state = $2,
                settlement_batch_id = COALESCE($3, settlement_batch_id),
                failure_code = NULLIF($4, ''),
                failure_description = NULLIF($5, ''),
                result_published = result_published OR $6,
                lease_owner = CASE WHEN $2 = 'PROCESSING' THEN NULLIF($7, '') ELSE lease_owner END,
                lease_generation = CASE WHEN $2 = 'PROCESSING' THEN $8 ELSE lease_generation END,
                lease_expires_at = CASE WHEN $2 = 'PROCESSING' THEN $9 ELSE lease_expires_at END,
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
        .bind(requested_owner)
        .bind(requested_generation)
        .bind(requested_expiry)
        .fetch_one(&mut *tx)
        .await
        .map_err(internal_database_status)?;
        tx.commit().await.map_err(internal_database_status)?;
        Ok(Response::new(pb::UpdateSettlementOperationResponse {
            operation: Some(row.into()),
        }))
    }

    async fn claim_settlement_outbox(
        &self,
        request: Request<pb::ClaimSettlementOutboxRequest>,
    ) -> std::result::Result<Response<pb::ClaimSettlementOutboxResponse>, Status> {
        let request = request.into_inner();
        let worker_id = request.worker_id.trim();
        if worker_id.is_empty() || !(1..=100).contains(&request.limit) {
            return Err(Status::invalid_argument(
                "worker_id and an outbox claim limit from 1 through 100 are required",
            ));
        }
        if !(5..=300).contains(&request.lease_seconds) {
            return Err(Status::invalid_argument(
                "outbox lease_seconds must be from 5 through 300",
            ));
        }
        let lease_seconds = i64::from(request.lease_seconds);
        let mut tx = self.db.begin().await.map_err(internal_database_status)?;
        reconcile_queued_outbox(&mut tx).await?;
        let rows = sqlx::query_as::<_, OutboxDeliveryRow>(
            r#"
            WITH candidates AS (
                SELECT outbox.operation_id
                FROM settlement_outbox outbox
                JOIN settlement_operation operation USING (operation_id)
                WHERE operation.operation_state = 'QUEUED'
                  AND operation.work_payload IS NOT NULL
                  AND (
                      outbox.delivery_state = 'PENDING'
                      OR (outbox.delivery_state = 'IN_FLIGHT' AND outbox.lease_expires_at <= now())
                  )
                ORDER BY outbox.created_at, outbox.operation_id
                LIMIT $1
                FOR UPDATE OF outbox SKIP LOCKED
            ), claimed AS (
                UPDATE settlement_outbox outbox
                SET delivery_state = 'IN_FLIGHT',
                    attempt_count = attempt_count + 1,
                    lease_owner = $2,
                    lease_generation = lease_generation + 1,
                    lease_expires_at = now() + make_interval(secs => $3),
                    last_error = NULL,
                    updated_at = now()
                FROM candidates
                WHERE outbox.operation_id = candidates.operation_id
                RETURNING outbox.operation_id, outbox.attempt_count, outbox.lease_generation
            )
            SELECT claimed.operation_id,
                   operation.work_payload,
                   claimed.attempt_count,
                   claimed.lease_generation
            FROM claimed
            JOIN settlement_operation operation USING (operation_id)
            ORDER BY claimed.operation_id
            "#,
        )
        .bind(i64::from(request.limit))
        .bind(worker_id)
        .bind(lease_seconds)
        .fetch_all(&mut *tx)
        .await
        .map_err(internal_database_status)?;
        tx.commit().await.map_err(internal_database_status)?;

        let deliveries = rows
            .into_iter()
            .map(OutboxDeliveryRow::into_proto)
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(Response::new(pb::ClaimSettlementOutboxResponse {
            deliveries,
        }))
    }

    async fn complete_settlement_outbox(
        &self,
        request: Request<pb::CompleteSettlementOutboxRequest>,
    ) -> std::result::Result<Response<pb::CompleteSettlementOutboxResponse>, Status> {
        let request = request.into_inner();
        let operation_id = uuid::Uuid::parse_str(&request.operation_id)
            .map_err(|_| Status::invalid_argument("operation_id must be a UUID"))?;
        let worker_id = request.worker_id.trim();
        let message_id = request.message_id.trim();
        let generation = outbox_generation(request.lease_generation)?;
        if worker_id.is_empty() || message_id.is_empty() {
            return Err(Status::invalid_argument(
                "worker_id and message_id are required",
            ));
        }
        let result = sqlx::query(
            r#"
            UPDATE settlement_outbox
            SET delivery_state = 'DELIVERED',
                message_id = $4,
                lease_owner = NULL,
                lease_expires_at = NULL,
                last_error = NULL,
                delivered_at = COALESCE(delivered_at, now()),
                updated_at = now()
            WHERE operation_id = $1
              AND lease_generation = $3
              AND (
                  (delivery_state = 'IN_FLIGHT' AND lease_owner = $2)
                  OR (delivery_state = 'DELIVERED' AND message_id = $4)
              )
            "#,
        )
        .bind(operation_id)
        .bind(worker_id)
        .bind(generation)
        .bind(message_id)
        .execute(&self.db)
        .await
        .map_err(internal_database_status)?;
        if result.rows_affected() != 1 {
            return Err(Status::failed_precondition(
                "stale outbox lease cannot complete delivery",
            ));
        }
        Ok(Response::new(pb::CompleteSettlementOutboxResponse {}))
    }

    async fn release_settlement_outbox(
        &self,
        request: Request<pb::ReleaseSettlementOutboxRequest>,
    ) -> std::result::Result<Response<pb::ReleaseSettlementOutboxResponse>, Status> {
        let request = request.into_inner();
        let operation_id = uuid::Uuid::parse_str(&request.operation_id)
            .map_err(|_| Status::invalid_argument("operation_id must be a UUID"))?;
        let worker_id = request.worker_id.trim();
        let generation = outbox_generation(request.lease_generation)?;
        if worker_id.is_empty() {
            return Err(Status::invalid_argument("worker_id is required"));
        }
        let description = request
            .error_description
            .chars()
            .take(2048)
            .collect::<String>();
        let result = sqlx::query(
            r#"
            UPDATE settlement_outbox
            SET delivery_state = 'PENDING',
                lease_owner = NULL,
                lease_expires_at = NULL,
                last_error = NULLIF($4, ''),
                updated_at = now()
            WHERE operation_id = $1
              AND lease_generation = $3
              AND (
                  (delivery_state = 'IN_FLIGHT' AND lease_owner = $2)
                  OR (delivery_state = 'PENDING' AND lease_owner IS NULL)
              )
            "#,
        )
        .bind(operation_id)
        .bind(worker_id)
        .bind(generation)
        .bind(description)
        .execute(&self.db)
        .await
        .map_err(internal_database_status)?;
        if result.rows_affected() != 1 {
            return Err(Status::failed_precondition(
                "stale outbox lease cannot release delivery",
            ));
        }
        Ok(Response::new(pb::ReleaseSettlementOutboxResponse {}))
    }
}

#[derive(Debug, FromRow)]
struct OutboxDeliveryRow {
    operation_id: uuid::Uuid,
    work_payload: Json<Value>,
    attempt_count: i32,
    lease_generation: i64,
}

impl OutboxDeliveryRow {
    fn into_proto(self) -> std::result::Result<pb::SettlementOutboxDelivery, Status> {
        Ok(pb::SettlementOutboxDelivery {
            operation_id: self.operation_id.to_string(),
            work_payload_json: serde_json::to_vec(&self.work_payload.0)
                .map_err(|error| Status::internal(format!("encode outbox payload: {error}")))?,
            attempt_count: u32::try_from(self.attempt_count)
                .map_err(|_| Status::internal("outbox attempt count is invalid"))?,
            lease_generation: u64::try_from(self.lease_generation)
                .map_err(|_| Status::internal("outbox lease generation is invalid"))?,
        })
    }
}

async fn reconcile_queued_outbox(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
) -> std::result::Result<(), Status> {
    sqlx::query(
        r#"
        INSERT INTO settlement_outbox (operation_id)
        SELECT operation.operation_id
        FROM settlement_operation operation
        LEFT JOIN settlement_outbox outbox USING (operation_id)
        WHERE operation.operation_state = 'QUEUED'
          AND operation.work_payload IS NOT NULL
          AND outbox.operation_id IS NULL
        ON CONFLICT (operation_id) DO NOTHING
        "#,
    )
    .execute(&mut **tx)
    .await
    .map_err(internal_database_status)?;
    Ok(())
}

fn outbox_generation(value: u64) -> std::result::Result<i64, Status> {
    let value = i64::try_from(value)
        .map_err(|_| Status::invalid_argument("lease_generation exceeds BIGINT range"))?;
    if value <= 0 {
        return Err(Status::invalid_argument(
            "positive lease_generation is required",
        ));
    }
    Ok(value)
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
    work_payload_hash: Option<String>,
    lease_owner: Option<String>,
    lease_generation: i64,
    lease_expires_at: Option<DateTime<Utc>>,
    queued_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
}

fn validated_work_payload(
    request: &pb::QueueSettlementOperationRequest,
    intent: &str,
) -> std::result::Result<(Map<String, Value>, String), Status> {
    let mut payload = serde_json::from_slice::<Value>(&request.work_payload_json)
        .map_err(|error| {
            Status::invalid_argument(format!("work_payload_json is invalid: {error}"))
        })?
        .as_object()
        .cloned()
        .ok_or_else(|| Status::invalid_argument("work_payload_json must be a JSON object"))?;
    let required_string = |name: &str| {
        payload
            .get(name)
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| Status::invalid_argument(format!("work payload {name} is required")))
    };
    if required_string("idempotency_key")? != request.idempotency_key
        || required_string("request_fingerprint")? != request.request_fingerprint
        || required_string("intent")? != intent
        || required_string("created_by_service")? != "market"
    {
        return Err(Status::invalid_argument(
            "work payload metadata does not match the queue request",
        ));
    }
    if payload
        .get("caused_by_capsuleer_id")
        .and_then(Value::as_i64)
        != Some(request.caused_by_capsuleer_id)
    {
        return Err(Status::invalid_argument(
            "work payload actor does not match the queue request",
        ));
    }
    if payload
        .get("operations")
        .and_then(Value::as_array)
        .is_none_or(Vec::is_empty)
    {
        return Err(Status::invalid_argument(
            "work payload requires at least one settlement operation",
        ));
    }
    for field in ["operation_id", "queued_at", "request_id"] {
        payload.remove(field);
    }
    let canonical = serde_json::to_vec(&payload)
        .map_err(|error| Status::internal(format!("canonicalize work payload: {error}")))?;
    let hash = format!("sha256:{:x}", Sha256::digest(canonical));
    Ok((payload, hash))
}

fn canonical_work_payload(
    mut payload: Map<String, Value>,
    operation_id: uuid::Uuid,
    queued_at: DateTime<Utc>,
) -> Value {
    let operation_id = operation_id.to_string();
    payload.insert(
        "operation_id".to_string(),
        Value::String(operation_id.clone()),
    );
    payload.insert("request_id".to_string(), Value::String(operation_id));
    payload.insert(
        "queued_at".to_string(),
        Value::String(queued_at.to_rfc3339_opts(SecondsFormat::Nanos, true)),
    );
    Value::Object(payload)
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
            lease_owner: value.lease_owner.unwrap_or_default(),
            lease_generation: value.lease_generation as u64,
            lease_expires_at: value.lease_expires_at.map(timestamp),
        }
    }
}

fn validate_lease_transition(
    current: &OperationRow,
    next_state: &str,
    requested_owner: &str,
    requested_generation: i64,
    requested_expiry: Option<DateTime<Utc>>,
    now: DateTime<Utc>,
) -> std::result::Result<(), Status> {
    if next_state == "PROCESSING" {
        if requested_owner.is_empty() || requested_generation <= 0 {
            return Err(Status::invalid_argument(
                "PROCESSING operation requires lease_owner and positive lease_generation",
            ));
        }
        let expiry = requested_expiry.ok_or_else(|| {
            Status::invalid_argument("PROCESSING operation requires lease_expires_at")
        })?;
        if expiry <= now || expiry > now + MAX_SETTLEMENT_LEASE_DURATION {
            return Err(Status::invalid_argument(
                "lease_expires_at must be in the future and within the maximum lease duration",
            ));
        }
        let expected_generation = current
            .lease_generation
            .checked_add(1)
            .ok_or_else(|| Status::failed_precondition("lease generation exhausted"))?;
        if requested_generation != expected_generation {
            return Err(Status::failed_precondition(format!(
                "stale lease generation: got {requested_generation}, expected {expected_generation}"
            )));
        }
        if current.operation_state == "PROCESSING" {
            let owned_by_requester = current.lease_owner.as_deref() == Some(requested_owner);
            let current_expired = current
                .lease_expires_at
                .is_none_or(|expires_at| expires_at <= now);
            if !owned_by_requester && !current_expired {
                return Err(Status::failed_precondition(
                    "settlement operation has an active lease owned by another worker",
                ));
            }
        }
        return Ok(());
    }

    if current.operation_state == "PROCESSING" {
        let current_owner = current.lease_owner.as_deref().ok_or_else(|| {
            Status::failed_precondition("PROCESSING operation has no lease owner")
        })?;
        let current_expiry = current.lease_expires_at.ok_or_else(|| {
            Status::failed_precondition("PROCESSING operation has no lease expiry")
        })?;
        if current_expiry <= now {
            return Err(Status::failed_precondition(
                "stale lease cannot complete settlement operation",
            ));
        }
        if requested_owner != current_owner || requested_generation != current.lease_generation {
            return Err(Status::failed_precondition(
                "stale lease owner or generation cannot complete settlement operation",
            ));
        }
        return Ok(());
    }

    if current.operation_state == next_state
        && (!requested_owner.is_empty() || requested_generation != 0 || requested_expiry.is_some())
        && (current.lease_owner.as_deref() != Some(requested_owner)
            || current.lease_generation != requested_generation)
    {
        return Err(Status::failed_precondition(
            "stale lease owner or generation cannot update terminal settlement operation",
        ));
    }
    Ok(())
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

fn optional_timestamp(
    value: Option<Timestamp>,
) -> std::result::Result<Option<DateTime<Utc>>, Status> {
    value
        .map(|value| {
            if !(0..1_000_000_000).contains(&value.nanos) {
                return Err(Status::invalid_argument(
                    "lease_expires_at has invalid nanoseconds",
                ));
            }
            DateTime::from_timestamp(value.seconds, value.nanos as u32)
                .ok_or_else(|| Status::invalid_argument("lease_expires_at is out of range"))
        })
        .transpose()
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
    use serde_json::json;
    use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
    use sqlx::PgPool;
    use std::str::FromStr;
    use std::sync::LazyLock;
    use tokio::sync::Mutex;
    use uuid::Uuid;

    const TEST_MIGRATIONS: [&str; 3] = [
        include_str!("../migrations/0001_settlement_schema.sql"),
        include_str!("../migrations/0002_merge_item_stack_constraints.sql"),
        include_str!("../migrations/0003_settlement_hardening_and_outbox.sql"),
    ];
    static OUTBOX_MIGRATION_LOCK: LazyLock<Mutex<()>> = LazyLock::new(|| Mutex::new(()));

    struct OutboxTestDatabase {
        pool: PgPool,
        admin: PgPool,
        schema: String,
    }

    impl OutboxTestDatabase {
        async fn new() -> Self {
            let database_url = std::env::var("EVE_TRADE_TEST_DATABASE_URL")
                .expect("EVE_TRADE_TEST_DATABASE_URL is required for canonical outbox tests");
            let admin = PgPoolOptions::new()
                .max_connections(2)
                .connect(&database_url)
                .await
                .expect("connect to outbox test database");
            let schema = format!("eve_trade_outbox_test_{}", Uuid::new_v4().simple());
            sqlx::query(&format!(r#"CREATE SCHEMA "{schema}""#))
                .execute(&admin)
                .await
                .expect("create isolated outbox schema");
            let options = PgConnectOptions::from_str(&database_url)
                .expect("parse outbox database URL")
                .options([("search_path", format!("{schema},public"))]);
            let pool = PgPoolOptions::new()
                .max_connections(4)
                .connect_with(options)
                .await
                .expect("connect to isolated outbox schema");
            {
                let _migration_guard = OUTBOX_MIGRATION_LOCK.lock().await;
                let mut migration_admin = admin
                    .acquire()
                    .await
                    .expect("acquire migration lock connection");
                sqlx::query(
                    "SELECT pg_advisory_lock(hashtext('eve_trade_test_schema_migrations'))",
                )
                .execute(&mut *migration_admin)
                .await
                .expect("lock shared test schema migrations");
                sqlx::query("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
                    .execute(&mut *migration_admin)
                    .await
                    .expect("install pgcrypto in public schema");
                sqlx::query("ALTER EXTENSION pgcrypto SET SCHEMA public")
                    .execute(&mut *migration_admin)
                    .await
                    .expect("keep pgcrypto visible to isolated schemas");
                for migration in TEST_MIGRATIONS {
                    sqlx::raw_sql(migration)
                        .execute(&pool)
                        .await
                        .expect("apply outbox settlement migration");
                }
                sqlx::query(
                    "SELECT pg_advisory_unlock(hashtext('eve_trade_test_schema_migrations'))",
                )
                .execute(&mut *migration_admin)
                .await
                .expect("unlock shared test schema migrations");
            }
            sqlx::query(
                "INSERT INTO capsuleer (capsuleer_id, capsuleer_name) VALUES (1001, 'Outbox Tester')",
            )
            .execute(&pool)
            .await
            .expect("seed outbox capsuleer");
            Self {
                pool,
                admin,
                schema,
            }
        }

        async fn close(self) {
            self.pool.close().await;
            sqlx::query(&format!(r#"DROP SCHEMA "{}" CASCADE"#, self.schema))
                .execute(&self.admin)
                .await
                .expect("drop isolated outbox schema");
            self.admin.close().await;
        }

        fn service(&self) -> TradeSettlementGrpc {
            TradeSettlementGrpc {
                db: self.pool.clone(),
            }
        }
    }

    fn queue_request(key: &str) -> pb::QueueSettlementOperationRequest {
        let fingerprint = format!("market.issue.sha256:{key}");
        let payload = json!({
            "operation_id": "",
            "queued_at": "0001-01-01T00:00:00Z",
            "request_id": "",
            "intent": "ISSUE",
            "idempotency_key": key,
            "request_fingerprint": fingerprint,
            "external_request_id": format!("external-{key}"),
            "caused_by_capsuleer_id": 1001,
            "created_by_service": "market",
            "operations": [{
                "kind": "create_new_trade_instance_row",
                "create_new_trade_instance_row": {
                    "trade_instance_id": Uuid::new_v4().to_string(),
                    "trade_kind": "SELL",
                    "trade_state": "OPEN",
                    "issuer_id": 1001,
                    "item_type_id": 34,
                    "station_id": 60003760,
                    "total_quantity": 4,
                    "unit_price_isk": 25
                }
            }]
        });
        pb::QueueSettlementOperationRequest {
            idempotency_key: key.to_string(),
            request_fingerprint: fingerprint,
            intent: pb::SettlementIntent::Issue as i32,
            caused_by_capsuleer_id: 1001,
            external_request_id: format!("external-{key}"),
            work_payload_json: serde_json::to_vec(&payload).unwrap(),
        }
    }

    async fn queue_operation(service: &TradeSettlementGrpc, key: &str) -> String {
        service
            .queue_settlement_operation(Request::new(queue_request(key)))
            .await
            .expect("queue durable settlement work")
            .into_inner()
            .operation
            .expect("queue response operation")
            .operation_id
    }

    async fn claim(
        service: &TradeSettlementGrpc,
        worker_id: &str,
    ) -> Vec<pb::SettlementOutboxDelivery> {
        service
            .claim_settlement_outbox(Request::new(pb::ClaimSettlementOutboxRequest {
                worker_id: worker_id.to_string(),
                limit: 10,
                lease_seconds: 60,
            }))
            .await
            .expect("claim settlement outbox")
            .into_inner()
            .deliveries
    }

    async fn complete(
        service: &TradeSettlementGrpc,
        worker_id: &str,
        delivery: &pb::SettlementOutboxDelivery,
    ) {
        service
            .complete_settlement_outbox(Request::new(pb::CompleteSettlementOutboxRequest {
                operation_id: delivery.operation_id.clone(),
                worker_id: worker_id.to_string(),
                lease_generation: delivery.lease_generation,
                message_id: format!("message-{}", delivery.operation_id),
            }))
            .await
            .expect("complete settlement outbox delivery");
    }

    #[tokio::test]
    async fn test_market_persists_settlement_operation_and_outbox_message_atomically() {
        let database = OutboxTestDatabase::new().await;
        let operation_id = queue_operation(&database.service(), "atomic-persist").await;
        let counts = sqlx::query_as::<_, (i64, i64)>(
            r#"
            SELECT
                (SELECT count(*) FROM settlement_operation WHERE operation_id = $1),
                (SELECT count(*) FROM settlement_outbox WHERE operation_id = $1)
            "#,
        )
        .bind(Uuid::parse_str(&operation_id).unwrap())
        .fetch_one(&database.pool)
        .await
        .unwrap();
        assert_eq!(counts, (1, 1));
        database.close().await;
    }

    #[tokio::test]
    async fn test_market_rolls_back_settlement_operation_when_outbox_insert_fails() {
        let database = OutboxTestDatabase::new().await;
        sqlx::query(
            "ALTER TABLE settlement_outbox ADD CONSTRAINT force_outbox_failure CHECK (false)",
        )
        .execute(&database.pool)
        .await
        .unwrap();
        let result = database
            .service()
            .queue_settlement_operation(Request::new(queue_request("atomic-rollback")))
            .await;
        assert!(result.is_err(), "forced outbox failure was swallowed");
        let count = sqlx::query_scalar::<_, i64>(
            "SELECT count(*) FROM settlement_operation WHERE idempotency_key = 'atomic-rollback'",
        )
        .fetch_one(&database.pool)
        .await
        .unwrap();
        assert_eq!(count, 0, "operation survived failed outbox insertion");
        database.close().await;
    }

    #[tokio::test]
    async fn test_market_never_leaves_queued_operation_without_outbox_record() {
        let database = OutboxTestDatabase::new().await;
        queue_operation(&database.service(), "no-orphan").await;
        let count = sqlx::query_scalar::<_, i64>(
            r#"
            SELECT count(*)
            FROM settlement_operation operation
            LEFT JOIN settlement_outbox outbox USING (operation_id)
            WHERE operation.operation_state = 'QUEUED' AND outbox.operation_id IS NULL
            "#,
        )
        .fetch_one(&database.pool)
        .await
        .unwrap();
        assert_eq!(count, 0);
        database.close().await;
    }

    #[tokio::test]
    async fn test_outbox_dispatcher_publishes_unsent_settlement_messages() {
        let database = OutboxTestDatabase::new().await;
        let operation_id = queue_operation(&database.service(), "publish-unsent").await;
        let deliveries = claim(&database.service(), "worker-a").await;
        assert_eq!(deliveries.len(), 1);
        assert_eq!(deliveries[0].operation_id, operation_id);
        complete(&database.service(), "worker-a", &deliveries[0]).await;
        database.close().await;
    }

    #[tokio::test]
    async fn test_outbox_dispatcher_retries_failed_publications() {
        let database = OutboxTestDatabase::new().await;
        queue_operation(&database.service(), "retry-publish").await;
        let first = claim(&database.service(), "worker-a").await.remove(0);
        database
            .service()
            .release_settlement_outbox(Request::new(pb::ReleaseSettlementOutboxRequest {
                operation_id: first.operation_id.clone(),
                worker_id: "worker-a".to_string(),
                lease_generation: first.lease_generation,
                error_description: "injected broker failure".to_string(),
            }))
            .await
            .expect("release failed outbox publication");
        let second = claim(&database.service(), "worker-b").await.remove(0);
        assert_eq!(second.operation_id, first.operation_id);
        assert_eq!(second.attempt_count, 2);
        assert!(second.lease_generation > first.lease_generation);
        database.close().await;
    }

    #[tokio::test]
    async fn test_outbox_dispatcher_does_not_duplicate_delivered_messages() {
        let database = OutboxTestDatabase::new().await;
        queue_operation(&database.service(), "no-duplicate").await;
        let delivery = claim(&database.service(), "worker-a").await.remove(0);
        complete(&database.service(), "worker-a", &delivery).await;
        assert!(claim(&database.service(), "worker-b").await.is_empty());
        database.close().await;
    }

    #[tokio::test]
    async fn test_outbox_dispatcher_recovers_after_process_crash_before_publish() {
        let database = OutboxTestDatabase::new().await;
        let operation_id = queue_operation(&database.service(), "crash-before-publish").await;
        let first = claim(&database.service(), "crashed-worker").await.remove(0);
        sqlx::query(
            "UPDATE settlement_outbox SET lease_expires_at = now() - interval '1 second' WHERE operation_id = $1",
        )
        .bind(Uuid::parse_str(&operation_id).unwrap())
        .execute(&database.pool)
        .await
        .unwrap();
        let recovered = claim(&database.service(), "restarted-worker")
            .await
            .remove(0);
        assert_eq!(recovered.operation_id, first.operation_id);
        assert!(recovered.lease_generation > first.lease_generation);
        database.close().await;
    }

    #[tokio::test]
    async fn test_outbox_dispatcher_recovers_after_publish_before_delivery_mark() {
        let database = OutboxTestDatabase::new().await;
        let operation_id = queue_operation(&database.service(), "crash-after-publish").await;
        let first = claim(&database.service(), "crashed-worker").await.remove(0);
        let first_key = first.operation_id.clone();
        sqlx::query(
            "UPDATE settlement_outbox SET lease_expires_at = now() - interval '1 second' WHERE operation_id = $1",
        )
        .bind(Uuid::parse_str(&operation_id).unwrap())
        .execute(&database.pool)
        .await
        .unwrap();
        let replay = claim(&database.service(), "restarted-worker")
            .await
            .remove(0);
        assert_eq!(replay.operation_id, first_key);
        complete(&database.service(), "restarted-worker", &replay).await;
        database.close().await;
    }

    #[tokio::test]
    async fn test_outbox_dispatcher_uses_operation_id_as_idempotency_key() {
        let database = OutboxTestDatabase::new().await;
        let operation_id = queue_operation(&database.service(), "operation-key").await;
        let delivery = claim(&database.service(), "worker-a").await.remove(0);
        assert_eq!(delivery.operation_id, operation_id);
        let payload: Value = serde_json::from_slice(&delivery.work_payload_json).unwrap();
        assert_eq!(payload["operation_id"], operation_id);
        database.close().await;
    }

    #[tokio::test]
    async fn test_queued_operation_reconciler_repairs_missing_publication() {
        let database = OutboxTestDatabase::new().await;
        let operation_id = queue_operation(&database.service(), "repair-missing").await;
        sqlx::query("DELETE FROM settlement_outbox WHERE operation_id = $1")
            .bind(Uuid::parse_str(&operation_id).unwrap())
            .execute(&database.pool)
            .await
            .unwrap();
        let repaired = claim(&database.service(), "worker-a").await;
        assert_eq!(repaired.len(), 1);
        assert_eq!(repaired[0].operation_id, operation_id);
        database.close().await;
    }

    #[tokio::test]
    async fn test_queued_operation_reconciler_ignores_already_delivered_operations() {
        let database = OutboxTestDatabase::new().await;
        queue_operation(&database.service(), "delivered-ignore").await;
        let delivery = claim(&database.service(), "worker-a").await.remove(0);
        complete(&database.service(), "worker-a", &delivery).await;
        assert!(claim(&database.service(), "worker-b").await.is_empty());
        database.close().await;
    }

    fn uuid(value: u128) -> Uuid {
        Uuid::from_u128(value)
    }

    fn operation_row(state: &str, now: DateTime<Utc>) -> OperationRow {
        OperationRow {
            operation_id: uuid(10),
            idempotency_key: "operation-key".to_string(),
            request_fingerprint: "fingerprint".to_string(),
            intent: "ISSUE".to_string(),
            caused_by_capsuleer_id: 1001,
            operation_state: state.to_string(),
            settlement_batch_id: None,
            failure_code: None,
            failure_description: None,
            result_published: false,
            work_payload_hash: None,
            lease_owner: None,
            lease_generation: 0,
            lease_expires_at: None,
            queued_at: now,
            updated_at: now,
        }
    }

    #[test]
    fn queued_operation_accepts_first_bounded_lease() {
        let now = Utc::now();
        let current = operation_row("QUEUED", now);
        validate_lease_transition(
            &current,
            "PROCESSING",
            "worker-a",
            1,
            Some(now + Duration::minutes(1)),
            now,
        )
        .expect("first lease should be accepted");
    }

    #[test]
    fn active_lease_rejects_another_worker() {
        let now = Utc::now();
        let mut current = operation_row("PROCESSING", now);
        current.lease_owner = Some("worker-a".to_string());
        current.lease_generation = 4;
        current.lease_expires_at = Some(now + Duration::minutes(1));
        let error = validate_lease_transition(
            &current,
            "PROCESSING",
            "worker-b",
            5,
            Some(now + Duration::minutes(2)),
            now,
        )
        .expect_err("another worker acquired an active lease");
        assert_eq!(error.code(), tonic::Code::FailedPrecondition);
    }

    #[test]
    fn current_owner_can_renew_with_next_generation() {
        let now = Utc::now();
        let mut current = operation_row("PROCESSING", now);
        current.lease_owner = Some("worker-a".to_string());
        current.lease_generation = 4;
        current.lease_expires_at = Some(now + Duration::minutes(1));
        validate_lease_transition(
            &current,
            "PROCESSING",
            "worker-a",
            5,
            Some(now + Duration::minutes(2)),
            now,
        )
        .expect("current owner could not renew its lease");
    }

    #[test]
    fn expired_lease_allows_another_worker_to_take_over() {
        let now = Utc::now();
        let mut current = operation_row("PROCESSING", now);
        current.lease_owner = Some("worker-a".to_string());
        current.lease_generation = 4;
        current.lease_expires_at = Some(now - Duration::seconds(1));
        validate_lease_transition(
            &current,
            "PROCESSING",
            "worker-b",
            5,
            Some(now + Duration::minutes(1)),
            now,
        )
        .expect("expired lease could not be recovered");
    }

    #[test]
    fn only_current_unexpired_lease_can_complete_processing_operation() {
        let now = Utc::now();
        let mut current = operation_row("PROCESSING", now);
        current.lease_owner = Some("worker-a".to_string());
        current.lease_generation = 4;
        current.lease_expires_at = Some(now + Duration::minutes(1));

        let stale = validate_lease_transition(
            &current,
            "SUCCEEDED",
            "worker-a",
            3,
            Some(now + Duration::minutes(1)),
            now,
        )
        .expect_err("stale generation completed a processing operation");
        assert_eq!(stale.code(), tonic::Code::FailedPrecondition);

        validate_lease_transition(
            &current,
            "SUCCEEDED",
            "worker-a",
            4,
            Some(now + Duration::minutes(1)),
            now,
        )
        .expect("current lease could not complete processing operation");
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
