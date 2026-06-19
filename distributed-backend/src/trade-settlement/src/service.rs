use summer::plugin::service::Service;
use summer_sqlx::ConnectPool;
use tonic::{Request, Response, Status};

use crate::commands::ExecuteBatchCommand;
use crate::executor::BatchExecutionResult;
use crate::executor::SettlementExecutor;
use crate::proto::trade_settlement as pb;
use pb::trade_settlement_service_server::{TradeSettlementService, TradeSettlementServiceServer};

#[derive(Clone, Service)]
#[service(grpc = "TradeSettlementServiceServer")]
pub struct TradeSettlementGrpc {
    #[inject(component)]
    db: ConnectPool,
}

pub fn ensure_linked() {}

#[tonic::async_trait]
impl TradeSettlementService for TradeSettlementGrpc {
    async fn execute_settlement_batch(
        &self,
        request: Request<pb::ExecuteSettlementBatchRequest>,
    ) -> std::result::Result<Response<pb::ExecuteSettlementBatchResponse>, Status> {
        let command = ExecuteBatchCommand::try_from(request.into_inner())
            .map_err(|error| error.into_status())?;
        let executor = SettlementExecutor::new(self.db.clone());
        let result = executor
            .execute_batch(command)
            .await
            .map_err(|error| error.into_status())?;

        Ok(Response::new(result.into()))
    }
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
