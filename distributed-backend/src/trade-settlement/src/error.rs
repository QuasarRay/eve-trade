use crate::generated::settlement::TradeState;
use tonic::Status;

// SettlementError separates caller mistakes, invalid state transitions, and
// database failures. The service converts these errors to gRPC Status values at
// the boundary so database/state logic does not depend on transport details.
#[derive(Debug, thiserror::Error)]
pub enum SettlementError {
    #[error("invalid request: {0}")]
    InvalidRequest(String),

    #[error("request_id was reused with different request content")]
    RequestIdConflict,

    #[error("trade {trade_id} was not found")]
    TradeNotFound { trade_id: String },

    #[error("invalid trade transition from {from:?} using {action}")]
    InvalidTransition { from: TradeState, action: &'static str },

    #[error("request does not match the durable trade terms stored for trade {trade_id}")]
    TradeMismatch { trade_id: String },

    #[error("database error: {0}")]
    Database(#[from] sqlx::Error),

    #[error("database pool was not initialized before the gRPC service started")]
    PoolNotInitialized,

    #[error("database pool was initialized more than once")]
    PoolAlreadyInitialized,
}

// This block defines the transport mapping. Invalid requests become
// InvalidArgument, missing trades become NotFound, idempotency conflicts become
// AlreadyExists, and internal SQL/runtime failures remain Internal.
impl From<SettlementError> for Status {
    fn from(err: SettlementError) -> Self {
        match err {
            SettlementError::InvalidRequest(message) => Status::invalid_argument(message),
            SettlementError::RequestIdConflict => Status::already_exists(err.to_string()),
            SettlementError::TradeNotFound { .. } => Status::not_found(err.to_string()),
            SettlementError::InvalidTransition { .. } => Status::failed_precondition(err.to_string()),
            SettlementError::TradeMismatch { .. } => Status::failed_precondition(err.to_string()),
            SettlementError::Database(_) => Status::internal(err.to_string()),
            SettlementError::PoolNotInitialized => Status::internal(err.to_string()),
            SettlementError::PoolAlreadyInitialized => Status::internal(err.to_string()),
        }
    }
}
