use std::pin::Pin;

use summer::plugin::service::Service;
use summer_sqlx::ConnectPool;
use tokio_stream::Stream;
use tonic::{Request, Response, Status};
use tracing::{Instrument, Span};

use crate::db;
use crate::generated::eve_trade::common::v1::OperationMetadata;
use crate::generated::eve_trade::settlement::v1::{
    trade_settlement_command, trade_settlement_result,
    trade_settlement_service_server::{TradeSettlementService, TradeSettlementServiceServer},
    StreamTradeSettlementCommandsRequest, StreamTradeSettlementCommandsResponse,
    TradeSettlementCommand, TradeSettlementResult,
};

#[derive(Clone, Service)]
#[service(grpc = "TradeSettlementServiceServer")]
pub struct TradeSettlementGrpc {
    #[inject(component)]
    pool: ConnectPool,
}

#[tonic::async_trait]
impl TradeSettlementService for TradeSettlementGrpc {
    type StreamTradeSettlementCommandsStream = Pin<
        Box<
            dyn Stream<Item = Result<StreamTradeSettlementCommandsResponse, Status>>
                + Send
                + 'static,
        >,
    >;

    async fn stream_trade_settlement_commands(
        &self,
        request: Request<tonic::Streaming<StreamTradeSettlementCommandsRequest>>,
    ) -> Result<Response<Self::StreamTradeSettlementCommandsStream>, Status> {
        let pool = self.pool.clone();
        let remote_addr = request
            .remote_addr()
            .map(|addr| addr.to_string())
            .unwrap_or_else(|| "unknown".to_string());
        let mut inbound = request.into_inner();

        let outbound = async_stream::try_stream! {
            let mut sequence = 0_u64;
            while let Some(envelope) = inbound.message().await? {
                sequence += 1;
                let result = match envelope.command {
                    Some(command) => {
                        let span = command_span(sequence, &remote_addr, &command);
                        db::execute_trade_settlement_command(&pool, command.clone())
                            .instrument(span.clone())
                            .await
                            .map(|result| {
                                record_result(&span, &result);
                                tracing::info!(parent: &span, "settlement command completed");
                                result
                            })
                            .unwrap_or_else(|err| {
                                span.record("error", true);
                                tracing::error!(parent: &span, error = %err, "settlement command failed");
                                db::settlement_error_result(&command, err)
                            })
                    }
                    None => {
                        tracing::warn!(
                            rpc.system = "grpc",
                            rpc.service = "eve_trade.settlement.v1.TradeSettlementService",
                            rpc.method = "StreamTradeSettlementCommands",
                            stream.sequence = sequence,
                            net.peer.addr = %remote_addr,
                            "settlement stream message was missing command"
                        );
                        db::missing_command_result()
                    }
                };

                yield StreamTradeSettlementCommandsResponse {
                    result: Some(result),
                };
            }
        };

        Ok(Response::new(Box::pin(outbound)))
    }
}

fn command_span(sequence: u64, remote_addr: &str, command: &TradeSettlementCommand) -> Span {
    let metadata = command.metadata.as_ref();
    let payload_kind = command_payload_kind(command.command.as_ref());

    tracing::info_span!(
        "grpc.stream_trade_settlement_commands.command",
        otel.kind = "server",
        rpc.system = "grpc",
        rpc.service = "eve_trade.settlement.v1.TradeSettlementService",
        rpc.method = "StreamTradeSettlementCommands",
        net.peer.addr = %remote_addr,
        stream.sequence = sequence,
        trade.operation.kind = command.operation_kind,
        trade.operation.name = %payload_kind,
        trade.operation.id = %metadata_text(metadata, |metadata| metadata.operation_id.as_ref().map(|id| id.value.as_str())),
        trade.request.id = %metadata_text(metadata, |metadata| metadata.request_id.as_ref().map(|id| id.value.as_str())),
        trade.source.system = %metadata_text(metadata, |metadata| metadata.source_system.as_ref().map(|source| source.value.as_str())),
        error = tracing::field::Empty,
        settlement.attempt.status = tracing::field::Empty,
    )
}

fn record_result(span: &Span, result: &TradeSettlementResult) {
    span.record("settlement.attempt.status", result.attempt_status);
    span.record("trade.operation.kind", result.operation_kind);

    match result.result.as_ref() {
        Some(trade_settlement_result::Result::Rejected(_)) => {
            span.record("error", true);
            tracing::warn!(parent: span, "settlement command was rejected");
        }
        Some(trade_settlement_result::Result::ResultUnknown(_)) => {
            span.record("error", true);
            tracing::warn!(parent: span, "settlement command result is unknown");
        }
        Some(trade_settlement_result::Result::RolledBack(_)) => {
            span.record("error", true);
            tracing::warn!(parent: span, "settlement command was rolled back");
        }
        _ => {}
    }
}

fn command_payload_kind(command: Option<&trade_settlement_command::Command>) -> &'static str {
    match command {
        Some(trade_settlement_command::Command::IssueTradeInstance(_)) => "issue_trade_instance",
        Some(trade_settlement_command::Command::SettleTradeInstance(_)) => "settle_trade_instance",
        Some(trade_settlement_command::Command::CancelTradeInstance(_)) => "cancel_trade_instance",
        Some(trade_settlement_command::Command::ExpireTradeInstance(_)) => "expire_trade_instance",
        None => "missing_command",
    }
}

fn metadata_text<'a>(
    metadata: Option<&'a OperationMetadata>,
    value: impl FnOnce(&'a OperationMetadata) -> Option<&'a str>,
) -> &'a str {
    metadata.and_then(value).unwrap_or("unknown")
}
