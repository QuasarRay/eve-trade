use summer::plugin::service::Service;
use summer_sqlx::ConnectPool;
use tonic::{Request, Response, Status};
use tracing::{info_span, Instrument};

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
