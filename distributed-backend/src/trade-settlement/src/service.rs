// DB-BLOCK src_replacements_service_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for gRPC service implementation delegating to db module.
// Why: explicit imports make coupling visible during review.
use crate::db;
use crate::generated::settlement::v1::trade_settlement_service_server::{
    TradeSettlementService, TradeSettlementServiceServer,
};
// DB-BLOCK src_replacements_service_002
// What: imports this file’s dependencies.
// How: brings required symbols into scope for gRPC service implementation delegating to db module.
// Why: explicit imports make coupling visible during review.
use crate::generated::settlement::v1::*;
use summer::plugin::service::Service;
use tonic::{Request, Response, Status};

// DB-BLOCK src_replacements_service_003
// What: defines the `struct` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
#[derive(Clone, Service)]
#[service(grpc = "TradeSettlementServiceServer")]
// DB-BLOCK src_replacements_service_004
// What: defines the `TradeSettlementGrpc` data shape.
// How: groups fields that are read, written, or returned together.
// Why: named row/request/result shapes prevent accidental tuple-order bugs.
pub struct TradeSettlementGrpc;

#[tonic::async_trait]
// DB-BLOCK src_replacements_service_005
// What: groups behavior for a type or trait.
// How: keeps conversion/validation/service methods attached to the thing they operate on.
// Why: centralized behavior prevents duplicate inconsistent logic.
impl TradeSettlementService for TradeSettlementGrpc {
    // DB-BLOCK src_replacements_service_006
    // What: opens or replays a durable trade order request.
    // How: delegates to the order workflow that validates, idempotency-checks, reserves assets if needed, and commits.
    // Why: order creation is a write boundary and must be centralized.
    async fn open_trade_order(&self, request: Request<OpenTradeOrderRequest>) -> Result<Response<OpenTradeOrderResult>, Status> {
        // DB-BLOCK src_replacements_service_007
        // What: returns the branch result.
        // How: wraps the computed response/error with `Ok(Response::new(db::open_trade_order(db::pool().map_err(Status::from)?, &reques`.
        // Why: DB boundaries must propagate success/failure explicitly.
        Ok(Response::new(db::open_trade_order(db::pool().map_err(Status::from)?, &request.into_inner()).await.map_err(Status::from)?))
    }

    // DB-BLOCK src_replacements_service_008
    // What: closes a trade order with a requested terminal state.
    // How: delegates to the order workflow that locks the order and writes a valid close result.
    // Why: cancel/expire/fail transitions must be durable and replay-safe.
    async fn close_trade_order(&self, request: Request<CloseTradeOrderRequest>) -> Result<Response<CloseTradeOrderResult>, Status> {
        // DB-BLOCK src_replacements_service_009
        // What: returns the branch result.
        // How: wraps the computed response/error with `Ok(Response::new(db::close_trade_order(db::pool().map_err(Status::from)?, &reque`.
        // Why: DB boundaries must propagate success/failure explicitly.
        Ok(Response::new(db::close_trade_order(db::pool().map_err(Status::from)?, &request.into_inner()).await.map_err(Status::from)?))
    }

    // DB-BLOCK src_replacements_service_010
    // What: performs the market-to-settlement DB transaction.
    // How: validates request fields, claims idempotency, locks order/transaction/ownership rows, moves ISK/items, writes ledgers, records settlement state, and commits once.
    // Why: this is the correctness-critical path; duplicate or partial ownership movement would corrupt the world state.
    async fn request_settlement(&self, request: Request<SettlementRequest>) -> Result<Response<SettlementResult>, Status> {
        // DB-BLOCK src_replacements_service_011
        // What: returns the branch result.
        // How: wraps the computed response/error with `Ok(Response::new(db::request_settlement(db::pool().map_err(Status::from)?, &requ`.
        // Why: DB boundaries must propagate success/failure explicitly.
        Ok(Response::new(db::request_settlement(db::pool().map_err(Status::from)?, &request.into_inner()).await.map_err(Status::from)?))
    }

    // DB-BLOCK src_replacements_service_012
    // What: handles claim-result requests at the DB boundary.
    // How: rejects unsupported claimable-delivery flow for MVP with a typed error.
    // Why: unsafe partial implementation is worse than explicit unsupported behavior.
    async fn claim_result(&self, request: Request<ClaimResultRequest>) -> Result<Response<ClaimResultResponse>, Status> {
        // DB-BLOCK src_replacements_service_013
        // What: returns the branch result.
        // How: wraps the computed response/error with `Ok(Response::new(db::claim_result(db::pool().map_err(Status::from)?, &request.in`.
        // Why: DB boundaries must propagate success/failure explicitly.
        Ok(Response::new(db::claim_result(db::pool().map_err(Status::from)?, &request.into_inner()).await.map_err(Status::from)?))
    }

    // DB-BLOCK src_replacements_service_014
    // What: loads one durable trade order.
    // How: extracts the request ID and maps the row into a protobuf response.
    // Why: read APIs should not duplicate SQL or bypass the DB boundary.
    async fn get_trade_order(&self, request: Request<GetTradeOrderRequest>) -> Result<Response<GetTradeOrderResponse>, Status> {
        // DB-BLOCK src_replacements_service_015
        // What: returns the branch result.
        // How: wraps the computed response/error with `Ok(Response::new(db::get_trade_order(db::pool().map_err(Status::from)?, &request`.
        // Why: DB boundaries must propagate success/failure explicitly.
        Ok(Response::new(db::get_trade_order(db::pool().map_err(Status::from)?, &request.into_inner()).await.map_err(Status::from)?))
    }

    // DB-BLOCK src_replacements_service_016
    // What: lists outstanding orders with optional filters.
    // How: extracts filter fields, runs a paginated query, and builds protobuf views.
    // Why: market/gateway need controlled read access to order state.
    async fn list_outstanding_trade_orders(&self, request: Request<ListOutstandingTradeOrdersRequest>) -> Result<Response<ListOutstandingTradeOrdersResponse>, Status> {
        // DB-BLOCK src_replacements_service_017
        // What: returns the branch result.
        // How: wraps the computed response/error with `Ok(Response::new(db::list_outstanding_trade_orders(db::pool().map_err(Status::fr`.
        // Why: DB boundaries must propagate success/failure explicitly.
        Ok(Response::new(db::list_outstanding_trade_orders(db::pool().map_err(Status::from)?, &request.into_inner()).await.map_err(Status::from)?))
    }

    // DB-BLOCK src_replacements_service_018
    // What: returns transaction state and related settlement if present.
    // How: loads trade_transaction and optional settlement rows in one read transaction.
    // Why: callers need state visibility after asynchronous/retried settlement attempts.
    async fn get_transaction_state(&self, request: Request<GetTransactionStateRequest>) -> Result<Response<GetTransactionStateResponse>, Status> {
        // DB-BLOCK src_replacements_service_019
        // What: returns the branch result.
        // How: wraps the computed response/error with `Ok(Response::new(db::get_transaction_state(db::pool().map_err(Status::from)?, &r`.
        // Why: DB boundaries must propagate success/failure explicitly.
        Ok(Response::new(db::get_transaction_state(db::pool().map_err(Status::from)?, &request.into_inner()).await.map_err(Status::from)?))
    }

    // DB-BLOCK src_replacements_service_020
    // What: returns settlement details and step history.
    // How: loads settlement by ID and maps settlement_step rows to protobuf.
    // Why: phase/step history is needed for crash diagnosis and operator confidence.
    async fn get_settlement(&self, request: Request<GetSettlementRequest>) -> Result<Response<GetSettlementResponse>, Status> {
        // DB-BLOCK src_replacements_service_021
        // What: returns the branch result.
        // How: wraps the computed response/error with `Ok(Response::new(db::get_settlement(db::pool().map_err(Status::from)?, &request.`.
        // Why: DB boundaries must propagate success/failure explicitly.
        Ok(Response::new(db::get_settlement(db::pool().map_err(Status::from)?, &request.into_inner()).await.map_err(Status::from)?))
    }

    // DB-BLOCK src_replacements_service_022
    // What: returns the operation audit root.
    // How: extracts operation_id, loads operation row, and maps it to OperationView.
    // Why: multi-table mutations need a single traceable parent record.
    async fn get_operation(&self, request: Request<GetOperationRequest>) -> Result<Response<GetOperationResponse>, Status> {
        // DB-BLOCK src_replacements_service_023
        // What: returns the branch result.
        // How: wraps the computed response/error with `Ok(Response::new(db::get_operation(db::pool().map_err(Status::from)?, &request.i`.
        // Why: DB boundaries must propagate success/failure explicitly.
        Ok(Response::new(db::get_operation(db::pool().map_err(Status::from)?, &request.into_inner()).await.map_err(Status::from)?))
    }
}
