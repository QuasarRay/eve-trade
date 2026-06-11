//! Internal database-domain types.
//!
//! What this file contains:
//! - Small enums and constants used by the DB layer.
//! - Numeric protobuf enum constants copied from the current proto contract.
//!
//! How it works:
//! - The DB layer uses these internal enums instead of matching directly on
//!   prost-generated enum variants.
//! - The proto messages still receive `i32` enum values at the boundary.
//!
//! Why it exists:
//! - Durable correctness must not depend on generated Rust variant names.
//! - The proto deliberately uses canonical lowercase state names, and prost's
//!   naming conversion can become irritating. This file makes DB logic stable.

// DB-BLOCK src_db_types_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for internal DB-domain enums independent from generated protobuf enum names.
// Why: explicit imports make coupling visible during review.
use crate::error::SettlementError;

// -----------------------------------------------------------------------------
// Protobuf enum numeric constants.
// -----------------------------------------------------------------------------
// These values match the latest proto generated in the conversation. Using
// numeric constants here makes the DB code independent of prost variant names
// while still producing correct protobuf responses.

// DB-BLOCK src_db_types_002
// What: exposes the `proto_i32` submodule.
// How: makes `proto_i32.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod proto_i32 {
    pub const TRANSACTION_BEING_CREATED: i32 = 1;
    pub const TRANSACTION_OUTSTANDING: i32 = 2;
    pub const TRANSACTION_ACCEPTED: i32 = 3;
    pub const TRANSACTION_IN_PROGRESS: i32 = 4;
    pub const TRANSACTION_COMPLETED: i32 = 5;
    pub const TRANSACTION_CLAIMABLE: i32 = 6;
    pub const TRANSACTION_CLAIMED: i32 = 7;
    pub const TRANSACTION_EXPIRED: i32 = 8;
    pub const TRANSACTION_FAILED: i32 = 9;
    pub const TRANSACTION_CANCELLED: i32 = 10;

    pub const CHANGE_SET_TO_EXPIRED: i32 = 7;
    pub const CHANGE_SET_TO_FAILED: i32 = 8;
    pub const CHANGE_SET_TO_CANCELLED: i32 = 9;

    pub const OPERATION_PENDING: i32 = 1;
    pub const OPERATION_IN_PROGRESS: i32 = 2;
    pub const OPERATION_COMPLETED: i32 = 3;
    pub const OPERATION_FAILED: i32 = 4;

    pub const RESERVATION_ACTIVE: i32 = 1;
    pub const RESERVATION_PARTIALLY_USED: i32 = 2;
    pub const RESERVATION_USED: i32 = 3;
    pub const RESERVATION_RELEASED: i32 = 4;

    pub const SETTLEMENT_CREATED: i32 = 1;
    pub const SETTLEMENT_LOCKED_TRADE: i32 = 2;
    pub const SETTLEMENT_LOCKED_WALLETS: i32 = 3;
    pub const SETTLEMENT_LOCKED_ITEMS: i32 = 4;
    pub const SETTLEMENT_WALLET_MOVED: i32 = 5;
    pub const SETTLEMENT_ITEMS_MOVED: i32 = 6;
    pub const SETTLEMENT_STATE_RECORDED: i32 = 7;
    pub const SETTLEMENT_COMPLETED: i32 = 8;
    pub const SETTLEMENT_FAILED: i32 = 9;

    pub const ORDER_SIDE_BUY_ORDER: i32 = 1;
    pub const ORDER_SIDE_SELL_ORDER: i32 = 2;

    pub const ITEM_KIND_STACKABLE: i32 = 1;
    pub const ITEM_KIND_SINGLETON: i32 = 2;

    pub const OP_CREATE_TRADE_ORDER: i32 = 60;
    pub const OP_CANCEL_TRADE_ORDER: i32 = 61;
    pub const OP_EXPIRE_TRADE_ORDER: i32 = 62;
    pub const OP_ACCEPT_TRADE_ORDER: i32 = 63;
    pub const OP_SETTLE_TRADE: i32 = 64;
    pub const OP_CLAIM_TRADE_RESULT: i32 = 65;

    pub const ERROR_INSUFFICIENT_ISK: i32 = 1;
    pub const ERROR_INSUFFICIENT_ITEMS: i32 = 2;
    pub const ERROR_INVALID_STATE_TRANSITION: i32 = 3;
    pub const ERROR_ORDER_EXPIRED: i32 = 4;
    pub const ERROR_ORDER_CANCELLED: i32 = 5;
    pub const ERROR_DUPLICATE_REQUEST: i32 = 7;
    pub const ERROR_SETTLEMENT_CONFLICT: i32 = 8;
    pub const ERROR_STALE_VERSION_CONFLICT: i32 = 9;
    pub const ERROR_INTEGRITY_CONFLICT: i32 = 10;
    pub const ERROR_RESERVATION_CONFLICT: i32 = 11;
}

// DB-BLOCK src_db_types_003
// What: defines the `enum` controlled vocabulary.
// How: uses Rust variants and conversion functions instead of scattering raw strings.
// Why: DB state must be explicit and reject unknown values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
// DB-BLOCK src_db_types_004
// What: defines the `TradeState` controlled vocabulary.
// How: uses Rust variants and conversion functions instead of scattering raw strings.
// Why: DB state must be explicit and reject unknown values.
pub enum TradeState {
    BeingCreated,
    Outstanding,
    Accepted,
    InProgress,
    Completed,
    Claimable,
    Claimed,
    Expired,
    Failed,
    Cancelled,
}

// DB-BLOCK src_db_types_005
// What: groups behavior for a type or trait.
// How: keeps conversion/validation/service methods attached to the thing they operate on.
// Why: centralized behavior prevents duplicate inconsistent logic.
impl TradeState {
    // Converts the DB enum text into the internal enum.
    // This gives one trusted place for validating persisted state values.
    // DB-BLOCK src_db_types_006
    // What: implements `from_db`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn from_db(value: &str) -> Result<Self, SettlementError> {
        // DB-BLOCK src_db_types_007
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match value {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match value {
            "being_created" => Ok(Self::BeingCreated),
            "outstanding" => Ok(Self::Outstanding),
            "accepted" => Ok(Self::Accepted),
            "in_progress" => Ok(Self::InProgress),
            "completed" => Ok(Self::Completed),
            "claimable" => Ok(Self::Claimable),
            "claimed" => Ok(Self::Claimed),
            "expired" => Ok(Self::Expired),
            "failed" => Ok(Self::Failed),
            "cancelled" => Ok(Self::Cancelled),
            other => Err(SettlementError::IntegrityConflict(format!(
                "unknown persisted trade state: {other}"
            ))),
        }
    }

    // Converts the internal enum into the canonical database string.
    // These strings are the canonical lifecycle names chosen for this project.
    // DB-BLOCK src_db_types_008
    // What: implements `as_db`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn as_db(self) -> &'static str {
        // DB-BLOCK src_db_types_009
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match self {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match self {
            Self::BeingCreated => "being_created",
            Self::Outstanding => "outstanding",
            Self::Accepted => "accepted",
            Self::InProgress => "in_progress",
            Self::Completed => "completed",
            Self::Claimable => "claimable",
            Self::Claimed => "claimed",
            Self::Expired => "expired",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }

    // Converts internal state to protobuf enum numeric value.
    // Response construction uses this to avoid prost enum naming coupling.
    // DB-BLOCK src_db_types_010
    // What: implements `as_proto_i32`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn as_proto_i32(self) -> i32 {
        // DB-BLOCK src_db_types_011
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match self {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match self {
            Self::BeingCreated => proto_i32::TRANSACTION_BEING_CREATED,
            Self::Outstanding => proto_i32::TRANSACTION_OUTSTANDING,
            Self::Accepted => proto_i32::TRANSACTION_ACCEPTED,
            Self::InProgress => proto_i32::TRANSACTION_IN_PROGRESS,
            Self::Completed => proto_i32::TRANSACTION_COMPLETED,
            Self::Claimable => proto_i32::TRANSACTION_CLAIMABLE,
            Self::Claimed => proto_i32::TRANSACTION_CLAIMED,
            Self::Expired => proto_i32::TRANSACTION_EXPIRED,
            Self::Failed => proto_i32::TRANSACTION_FAILED,
            Self::Cancelled => proto_i32::TRANSACTION_CANCELLED,
        }
    }

    // DB-BLOCK src_db_types_012
    // What: implements `is_terminal`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn is_terminal(self) -> bool {
        matches!(
            self,
            Self::Completed | Self::Claimed | Self::Expired | Self::Failed | Self::Cancelled
        )
    }
}

// DB-BLOCK src_db_types_013
// What: defines the `enum` controlled vocabulary.
// How: uses Rust variants and conversion functions instead of scattering raw strings.
// Why: DB state must be explicit and reject unknown values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
// DB-BLOCK src_db_types_014
// What: defines the `OrderSide` controlled vocabulary.
// How: uses Rust variants and conversion functions instead of scattering raw strings.
// Why: DB state must be explicit and reject unknown values.
pub enum OrderSide {
    Buy,
    Sell,
}

// DB-BLOCK src_db_types_015
// What: groups behavior for a type or trait.
// How: keeps conversion/validation/service methods attached to the thing they operate on.
// Why: centralized behavior prevents duplicate inconsistent logic.
impl OrderSide {
    // DB-BLOCK src_db_types_016
    // What: implements `from_proto_i32`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn from_proto_i32(value: i32) -> Result<Self, SettlementError> {
        // DB-BLOCK src_db_types_017
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match value {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match value {
            proto_i32::ORDER_SIDE_BUY_ORDER => Ok(Self::Buy),
            proto_i32::ORDER_SIDE_SELL_ORDER => Ok(Self::Sell),
            other => Err(SettlementError::InvalidRequest(format!(
                "unknown order_side enum value: {other}"
            ))),
        }
    }

    // DB-BLOCK src_db_types_018
    // What: implements `from_db`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn from_db(value: &str) -> Result<Self, SettlementError> {
        // DB-BLOCK src_db_types_019
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match value {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match value {
            "buy_order" => Ok(Self::Buy),
            "sell_order" => Ok(Self::Sell),
            other => Err(SettlementError::IntegrityConflict(format!(
                "unknown persisted order_side: {other}"
            ))),
        }
    }

    // DB-BLOCK src_db_types_020
    // What: implements `as_db`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn as_db(self) -> &'static str {
        // DB-BLOCK src_db_types_021
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match self {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match self {
            Self::Buy => "buy_order",
            Self::Sell => "sell_order",
        }
    }

    // DB-BLOCK src_db_types_022
    // What: implements `as_proto_i32`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn as_proto_i32(self) -> i32 {
        // DB-BLOCK src_db_types_023
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match self {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match self {
            Self::Buy => proto_i32::ORDER_SIDE_BUY_ORDER,
            Self::Sell => proto_i32::ORDER_SIDE_SELL_ORDER,
        }
    }
}

// DB-BLOCK src_db_types_024
// What: defines the `enum` controlled vocabulary.
// How: uses Rust variants and conversion functions instead of scattering raw strings.
// Why: DB state must be explicit and reject unknown values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
// DB-BLOCK src_db_types_025
// What: defines the `ItemKind` controlled vocabulary.
// How: uses Rust variants and conversion functions instead of scattering raw strings.
// Why: DB state must be explicit and reject unknown values.
pub enum ItemKind {
    Stackable,
    Singleton,
}

// DB-BLOCK src_db_types_026
// What: groups behavior for a type or trait.
// How: keeps conversion/validation/service methods attached to the thing they operate on.
// Why: centralized behavior prevents duplicate inconsistent logic.
impl ItemKind {
    // DB-BLOCK src_db_types_027
    // What: implements `from_proto_i32`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn from_proto_i32(value: i32) -> Result<Self, SettlementError> {
        // DB-BLOCK src_db_types_028
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match value {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match value {
            proto_i32::ITEM_KIND_STACKABLE => Ok(Self::Stackable),
            proto_i32::ITEM_KIND_SINGLETON => Ok(Self::Singleton),
            other => Err(SettlementError::InvalidRequest(format!(
                "unknown item_kind enum value: {other}"
            ))),
        }
    }
}

// DB-BLOCK src_db_types_029
// What: defines the `enum` controlled vocabulary.
// How: uses Rust variants and conversion functions instead of scattering raw strings.
// Why: DB state must be explicit and reject unknown values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
// DB-BLOCK src_db_types_030
// What: defines the `CloseTarget` controlled vocabulary.
// How: uses Rust variants and conversion functions instead of scattering raw strings.
// Why: DB state must be explicit and reject unknown values.
pub enum CloseTarget {
    Cancelled,
    Expired,
    Failed,
}

// DB-BLOCK src_db_types_031
// What: groups behavior for a type or trait.
// How: keeps conversion/validation/service methods attached to the thing they operate on.
// Why: centralized behavior prevents duplicate inconsistent logic.
impl CloseTarget {
    // DB-BLOCK src_db_types_032
    // What: implements `from_requested_change`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn from_requested_change(value: i32) -> Result<Self, SettlementError> {
        // DB-BLOCK src_db_types_033
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match value {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match value {
            proto_i32::CHANGE_SET_TO_CANCELLED => Ok(Self::Cancelled),
            proto_i32::CHANGE_SET_TO_EXPIRED => Ok(Self::Expired),
            proto_i32::CHANGE_SET_TO_FAILED => Ok(Self::Failed),
            other => Err(SettlementError::InvalidRequest(format!(
                "close_trade_order accepts only cancelled/expired/failed; got enum value {other}"
            ))),
        }
    }

    // DB-BLOCK src_db_types_034
    // What: implements `as_trade_state`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn as_trade_state(self) -> TradeState {
        // DB-BLOCK src_db_types_035
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match self {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match self {
            Self::Cancelled => TradeState::Cancelled,
            Self::Expired => TradeState::Expired,
            Self::Failed => TradeState::Failed,
        }
    }

    // DB-BLOCK src_db_types_036
    // What: implements `operation_kind_db`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn operation_kind_db(self) -> &'static str {
        // DB-BLOCK src_db_types_037
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match self {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match self {
            Self::Cancelled => "cancel_trade_order",
            Self::Expired => "expire_trade_order",
            Self::Failed => "fail_trade_order",
        }
    }

    // DB-BLOCK src_db_types_038
    // What: implements `operation_kind_proto_i32`.
    // How: performs the smallest focused operation implied by this module and propagates typed errors.
    // Why: small named functions make correctness review and testing possible.
    pub fn operation_kind_proto_i32(self) -> i32 {
        // DB-BLOCK src_db_types_039
        // What: branches across known alternatives.
        // How: uses Rust `match` on `match self {`.
        // Why: closed branching is safer than ad-hoc string/boolean decision trees.
        match self {
            Self::Cancelled => proto_i32::OP_CANCEL_TRADE_ORDER,
            Self::Expired => proto_i32::OP_EXPIRE_TRADE_ORDER,
            Self::Failed => proto_i32::OP_EXPIRE_TRADE_ORDER,
        }
    }
}
