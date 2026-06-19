use tonic::Status;

#[derive(Debug, thiserror::Error)]
pub enum SettlementError {
    #[error("invalid argument: {0}")]
    InvalidArgument(String),
    #[error("not found: {0}")]
    NotFound(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error("failed precondition: {0}")]
    FailedPrecondition(String),
    #[error("insufficient funds: {0}")]
    InsufficientFunds(String),
    #[error("insufficient quantity: {0}")]
    InsufficientQuantity(String),
    #[error("database error: {0}")]
    Database(#[from] sqlx::Error),
    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, SettlementError>;

impl SettlementError {
    pub fn code(&self) -> &'static str {
        match self {
            SettlementError::InvalidArgument(_) => "INVALID_ARGUMENT",
            SettlementError::NotFound(_) => "NOT_FOUND",
            SettlementError::Conflict(_) => "CONFLICT",
            SettlementError::FailedPrecondition(_) => "FAILED_PRECONDITION",
            SettlementError::InsufficientFunds(_) => "INSUFFICIENT_FUNDS",
            SettlementError::InsufficientQuantity(_) => "INSUFFICIENT_QUANTITY",
            SettlementError::Database(_) => "DATABASE_ERROR",
            SettlementError::Serialization(_) => "SERIALIZATION_ERROR",
        }
    }

    pub fn into_status(self) -> Status {
        match self {
            SettlementError::InvalidArgument(message) => Status::invalid_argument(message),
            SettlementError::NotFound(message) => Status::not_found(message),
            SettlementError::Conflict(message) => Status::aborted(message),
            SettlementError::FailedPrecondition(message) => Status::failed_precondition(message),
            SettlementError::InsufficientFunds(message) => Status::failed_precondition(message),
            SettlementError::InsufficientQuantity(message) => Status::failed_precondition(message),
            SettlementError::Database(error) => Status::internal(error.to_string()),
            SettlementError::Serialization(error) => Status::internal(error.to_string()),
        }
    }
}
