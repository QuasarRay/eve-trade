use std::pin::Pin;

use summer::plugin::service::Service;
use summer_sqlx::ConnectPool;
use tokio_stream::Stream;
use tonic::{Request, Response, Status};

use crate::db;
use crate::generated::eve_trade::settlement::v1::{
    trade_settlement_service_server::{TradeSettlementService, TradeSettlementServiceServer},
    StreamTradeSettlementCommandsRequest, StreamTradeSettlementCommandsResponse,
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
        let mut inbound = request.into_inner();

        let outbound = async_stream::try_stream! {
            while let Some(envelope) = inbound.message().await? {
                let result = match envelope.command {
                    Some(command) => {
                        db::execute_trade_settlement_command(&pool, command.clone())
                            .await
                            .unwrap_or_else(|err| db::settlement_error_result(&command, err))
                    }
                    None => db::missing_command_result(),
                };

                yield StreamTradeSettlementCommandsResponse {
                    result: Some(result),
                };
            }
        };

        Ok(Response::new(Box::pin(outbound)))
    }
}
