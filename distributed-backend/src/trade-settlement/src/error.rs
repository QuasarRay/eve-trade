// DB-BLOCK src_replacements_error_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for typed service errors and error conversions.
// Why: explicit imports make coupling visible during review.
use tonic::Status;

// DB-BLOCK src_replacements_error_002
// What: defines the `enum` controlled vocabulary.
// How: uses Rust variants and conversion functions instead of scattering raw strings.
// Why: DB state must be explicit and reject unknown values.
#[derive(Debug, thiserror::Error)]
// DB-BLOCK src_replacements_error_003
// What: defines the `SettlementError` controlled vocabulary.
// How: uses Rust variants and conversion functions instead of scattering raw strings.
// Why: DB state must be explicit and reject unknown values.
pub enum SettlementError {
    #[error("invalid request: {0}")]
    InvalidRequest(String),

    #[error("unsupported operation: {0}")]
    Unsupported(String),

    #[error("database pool was already initialized")]
    PoolAlreadyInitialized,

    #[error("database pool was not initialized")]
    PoolNotInitialized,

    #[error("idempotency key or request id was reused with different request content")]
    RequestIdConflict,

    #[error("invalid transition from {from} using {action}")]
    InvalidTransition { from: String, action: &'static str },

    #[error("request does not match durable trade order {trade_order_id}")]
    TradeMismatch { trade_order_id: String },

    #[error("reservation conflict: {0}")]
    ReservationConflict(String),

    #[error("wallet {wallet_id} has insufficient ISK")]
    InsufficientIsk { wallet_id: String },

    #[error("item stack {item_stack_id} has insufficient quantity")]
    InsufficientItems { item_stack_id: String },

    #[error("stale version conflict: {0}")]
    StaleVersionConflict(String),

    #[error("integrity conflict: {0}")]
    IntegrityConflict(String),

    #[error(transparent)]
    Database(#[from] sqlx::Error),
}

// DB-BLOCK src_replacements_error_004
// What: groups behavior for a type or trait.
// How: keeps conversion/validation/service methods attached to the thing they operate on.
// Why: centralized behavior prevents duplicate inconsistent logic.
impl From<SettlementError> for Status {
    // DB-BLOCK src_replacements_error_005
    // What: implements `from`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    fn from(value: SettlementError) -> Self {
        // DB-BLOCK src_replacements_error_006
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match value {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match value {
            SettlementError::InvalidRequest(message) => Status::invalid_argument(message),
            SettlementError::Unsupported(message) => Status::unimplemented(message),
            SettlementError::RequestIdConflict => Status::already_exists(value.to_string()),
            SettlementError::InvalidTransition { .. }
            | SettlementError::TradeMismatch { .. }
            | SettlementError::ReservationConflict(_)
            | SettlementError::InsufficientIsk { .. }
            | SettlementError::InsufficientItems { .. }
            | SettlementError::StaleVersionConflict(_) => {
                Status::failed_precondition(value.to_string())
            }
            SettlementError::Database(sqlx::Error::RowNotFound) => {
                Status::not_found("requested row was not found")
            }
            SettlementError::Database(err) => Status::internal(err.to_string()),
            other => Status::internal(other.to_string()),
        }
    }
}
