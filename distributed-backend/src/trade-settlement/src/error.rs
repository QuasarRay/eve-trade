#[derive(Debug, thiserror::Error)]
pub enum SettlementError {
    #[error("invalid request: {0}")]
    InvalidRequest(String),

    #[error("idempotency key or request id was reused with different request content")]
    RequestIdConflict,

    #[error("invalid transition from {from} using {action}")]
    InvalidTransition { from: String, action: &'static str },

    #[error("request does not match durable trade instance {trade_instance_id}")]
    TradeMismatch { trade_instance_id: String },

    #[error("wallet {wallet_id} has insufficient ISK")]
    InsufficientIsk { wallet_id: String },

    #[error("item stack {item_stack_id} has insufficient quantity")]
    InsufficientItems { item_stack_id: String },

    #[error("database conflict: {0}")]
    DatabaseConflict(String),

    #[error(transparent)]
    Database(#[from] sqlx::Error),
}

impl SettlementError {
    pub fn error_code(&self) -> i32 {
        match self {
            Self::InvalidRequest(_) => 1,
            Self::RequestIdConflict | Self::DatabaseConflict(_) => 6,
            Self::InvalidTransition { .. }
            | Self::TradeMismatch { .. }
            | Self::InsufficientIsk { .. }
            | Self::InsufficientItems { .. } => 4,
            Self::Database(sqlx::Error::RowNotFound) => 2,
            Self::Database(_) => 7,
        }
    }

    pub fn retryable(&self) -> bool {
        matches!(
            self,
            Self::Database(sqlx::Error::PoolTimedOut | sqlx::Error::Io(_))
        )
    }
}
