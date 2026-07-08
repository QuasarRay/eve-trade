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
    #[error("validation error: {0}")]
    Validation(#[from] prost_protovalidate::Error),
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
            SettlementError::Validation(prost_protovalidate::Error::Validation(_)) => {
                "INVALID_ARGUMENT"
            }
            SettlementError::Validation(_) => "VALIDATION_ERROR",
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
            SettlementError::Validation(prost_protovalidate::Error::Validation(error)) => {
                Status::invalid_argument(error.to_string())
            }
            SettlementError::Validation(error) => Status::internal(error.to_string()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tonic::Code;

    #[test]
    fn stable_codes_cover_each_domain_error() {
        let cases = [
            (
                SettlementError::InvalidArgument("x".into()),
                "INVALID_ARGUMENT",
            ),
            (SettlementError::NotFound("x".into()), "NOT_FOUND"),
            (SettlementError::Conflict("x".into()), "CONFLICT"),
            (
                SettlementError::FailedPrecondition("x".into()),
                "FAILED_PRECONDITION",
            ),
            (
                SettlementError::InsufficientFunds("x".into()),
                "INSUFFICIENT_FUNDS",
            ),
            (
                SettlementError::InsufficientQuantity("x".into()),
                "INSUFFICIENT_QUANTITY",
            ),
        ];
        for (error, expected) in cases {
            assert_eq!(error.code(), expected);
        }
    }

    #[test]
    fn tonic_status_mapping_does_not_turn_domain_rejections_into_internal_errors() {
        assert_eq!(
            SettlementError::InvalidArgument("bad".into())
                .into_status()
                .code(),
            Code::InvalidArgument
        );
        assert_eq!(
            SettlementError::NotFound("missing".into())
                .into_status()
                .code(),
            Code::NotFound
        );
        assert_eq!(
            SettlementError::Conflict("duplicate".into())
                .into_status()
                .code(),
            Code::Aborted
        );
        assert_eq!(
            SettlementError::InsufficientFunds("low".into())
                .into_status()
                .code(),
            Code::FailedPrecondition
        );
    }
}
