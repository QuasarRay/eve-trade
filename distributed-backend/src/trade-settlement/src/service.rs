use crate::db;
use crate::generated::settlement::trade_settlement_service_server::{
    TradeSettlementService, TradeSettlementServiceServer,
};
use crate::generated::settlement::{SettleTradeRequest, SettleTradeResponse};
use summer::plugin::service::Service;
use tonic::{Request, Response, Status};

// This service struct is intentionally fieldless because summer-grpc registers
// services through the Service derive macro. The database pool is initialized once
// in main and read from db.rs so the gRPC boundary stays thin.
#[derive(Clone, Service)]
#[service(grpc = "TradeSettlementServiceServer")]
pub struct TradeSettlementGrpc;

// This block implements the protobuf-generated gRPC trait. It is the only place
// where transport types are converted into service behavior; all correctness
// rules live in db.rs/state.rs/validation.rs.
#[tonic::async_trait]
impl TradeSettlementService for TradeSettlementGrpc {
    // This RPC receives the exact SettleTradeRequest from market, executes it
    // through the database-backed settlement function, and returns the resulting
    // durable TradeState. It does not allow market to directly set the state.
    async fn settle_trade(
        &self,
        request: Request<SettleTradeRequest>,
    ) -> Result<Response<SettleTradeResponse>, Status> {
        let req = request.into_inner();
        let pool = db::pool().map_err(Status::from)?;
        let (state, message) = db::settle_trade(pool, &req).await.map_err(Status::from)?;

        Ok(Response::new(SettleTradeResponse {
            trade_id: req.trade_id,
            state: state as i32,
            message,
        }))
    }
}
