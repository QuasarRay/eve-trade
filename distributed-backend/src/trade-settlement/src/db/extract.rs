//! Protobuf extraction and structural request validation.
//!
//! What this file contains:
//! - Small helpers that convert optional protobuf wrapper messages into plain
//!   validated Rust values.
//!
//! How it works:
//! - Every required wrapper is checked for presence and non-empty value.
//! - UUID-shaped IDs are parsed before hitting SQL so invalid input fails as a
//!   client error rather than as a database cast error.
//! - Amounts and quantities are checked for positive/non-negative semantics at
//!   the boundary.
//!
//! Why it exists:
//! - Service/database code should not repeatedly unwrap protobuf messages.
//! - All request-shape mistakes should be rejected before locks and writes.

// DB-BLOCK src_db_extract_001
// What: imports this file’s dependencies.
// How: brings required symbols into scope for protobuf wrapper extraction and structural validation.
// Why: explicit imports make coupling visible during review.
use uuid::Uuid;

use crate::error::SettlementError;
use crate::generated::settlement::v1::{
    CloseTradeOrderRequest, OpenTradeOrderRequest, RequestSettlementRequest, TradeOrderTerms,
};
// DB-BLOCK src_db_extract_002
// What: imports this file’s dependencies.
// How: brings required symbols into scope for protobuf wrapper extraction and structural validation.
// Why: explicit imports make coupling visible during review.
use crate::db::types::{ItemKind, OrderSide};
use crate::generated::trade::v1::*;

// DB-BLOCK src_db_extract_003
// What: implements `require_text`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn require_text(field: &str, value: Option<&String>) -> Result<String, SettlementError> {
    value
        .map(|v| v.trim())
        .filter(|v| !v.is_empty())
        .map(ToOwned::to_owned)
        .ok_or_else(|| SettlementError::InvalidRequest(format!("{field} is required")))
}

// DB-BLOCK src_db_extract_004
// What: implements `require_uuid_text`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn require_uuid_text(field: &str, value: Option<&String>) -> Result<String, SettlementError> {
    // DB-BLOCK src_db_extract_005
    // What: binds `value` as a named intermediate.
    // How: computes/extracts `value` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let value = require_text(field, value)?;
    Uuid::parse_str(&value).map_err(|_| {
        SettlementError::InvalidRequest(format!("{field} must be a valid UUID string"))
    })?;
    // DB-BLOCK src_db_extract_006
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(value)`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(value)
}

// DB-BLOCK src_db_extract_007
// What: implements `optional_uuid_text`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn optional_uuid_text(
    field: &str,
    value: Option<&String>,
) -> Result<Option<String>, SettlementError> {
    // DB-BLOCK src_db_extract_008
    // What: branches across known alternatives.
    // How: uses Rust `match` on `match value.map(|v| v.trim()).filter(|v| !v.is_empty()) {`.
    // Why: closed branching is safer than ad-hoc string/boolean decision trees.
    match value.map(|v| v.trim()).filter(|v| !v.is_empty()) {
        Some(v) => {
            Uuid::parse_str(v).map_err(|_| {
                SettlementError::InvalidRequest(format!("{field} must be a valid UUID string"))
            })?;
            // DB-BLOCK src_db_extract_009
            // What: returns the branch result.
            // How: wraps the computed response/error with `Ok(Some(v.to_owned()))`.
            // Why: DB boundaries must propagate success/failure explicitly.
            Ok(Some(v.to_owned()))
        }
        None => Ok(None),
    }
}

// DB-BLOCK src_db_extract_010
// What: implements `request_id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn request_id(ctx: &Option<RequestContext>) -> Result<String, SettlementError> {
    // DB-BLOCK src_db_extract_011
    // What: binds `ctx` as a named intermediate.
    // How: computes/extracts `ctx` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let ctx = ctx
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("context is required".to_string()))?;
    require_uuid_text(
        "context.request_id",
        ctx.request_id.as_ref().map(|x| &x.value),
    )
}

// DB-BLOCK src_db_extract_012
// What: implements `idempotency_key`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn idempotency_key(ctx: &Option<RequestContext>) -> Result<String, SettlementError> {
    // DB-BLOCK src_db_extract_013
    // What: binds `ctx` as a named intermediate.
    // How: computes/extracts `ctx` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let ctx = ctx
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("context is required".to_string()))?;
    require_text(
        "context.idempotency_key",
        ctx.idempotency_key.as_ref().map(|x| &x.value),
    )
}

// DB-BLOCK src_db_extract_014
// What: implements `source_system`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn source_system(ctx: &Option<RequestContext>) -> Result<String, SettlementError> {
    // DB-BLOCK src_db_extract_015
    // What: binds `ctx` as a named intermediate.
    // How: computes/extracts `ctx` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let ctx = ctx
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("context is required".to_string()))?;
    require_text("context.source_system", Some(&ctx.source_system))
}

// DB-BLOCK src_db_extract_016
// What: implements `created_by_service`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn created_by_service(ctx: &Option<RequestContext>) -> Result<String, SettlementError> {
    // DB-BLOCK src_db_extract_017
    // What: binds `ctx` as a named intermediate.
    // How: computes/extracts `ctx` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let ctx = ctx
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("context is required".to_string()))?;
    require_text("context.created_by_service", Some(&ctx.created_by_service))
}

// DB-BLOCK src_db_extract_018
// What: implements `acting_capsuleer_id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn acting_capsuleer_id(
    ctx: &Option<RequestContext>,
) -> Result<Option<String>, SettlementError> {
    // DB-BLOCK src_db_extract_019
    // What: branches across known alternatives.
    // How: uses Rust `match` on `match ctx.as_ref().and_then(|c| c.acting_capsuleer_id.as_ref()) {`.
    // Why: closed branching is safer than ad-hoc string/boolean decision trees.
    match ctx.as_ref().and_then(|c| c.acting_capsuleer_id.as_ref()) {
        Some(id) => optional_uuid_text("context.acting_capsuleer_id", Some(&id.value)),
        None => Ok(None),
    }
}

// DB-BLOCK src_db_extract_020
// What: implements `capsuleer_id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn capsuleer_id(field: &str, value: &Option<CapsuleerId>) -> Result<String, SettlementError> {
    require_uuid_text(field, value.as_ref().map(|x| &x.value))
}

// DB-BLOCK src_db_extract_021
// What: implements `wallet_id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn wallet_id(field: &str, value: &Option<WalletId>) -> Result<String, SettlementError> {
    require_uuid_text(field, value.as_ref().map(|x| &x.value))
}

// DB-BLOCK src_db_extract_022
// What: implements `item_type_id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn item_type_id(field: &str, value: &Option<ItemTypeId>) -> Result<String, SettlementError> {
    require_uuid_text(field, value.as_ref().map(|x| &x.value))
}

// DB-BLOCK src_db_extract_023
// What: implements `item_stack_id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn item_stack_id(field: &str, value: &Option<ItemStackId>) -> Result<String, SettlementError> {
    require_uuid_text(field, value.as_ref().map(|x| &x.value))
}

// DB-BLOCK src_db_extract_024
// What: implements `item_stack_id_optional`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn item_stack_id_optional(
    field: &str,
    value: &Option<ItemStackId>,
) -> Result<Option<String>, SettlementError> {
    optional_uuid_text(field, value.as_ref().map(|x| &x.value))
}

// DB-BLOCK src_db_extract_026
// What: implements `station_id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn station_id(field: &str, value: &Option<StationId>) -> Result<String, SettlementError> {
    require_uuid_text(field, value.as_ref().map(|x| &x.value))
}

// DB-BLOCK src_db_extract_027
// What: implements `region_id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn region_id(field: &str, value: &Option<RegionId>) -> Result<String, SettlementError> {
    require_uuid_text(field, value.as_ref().map(|x| &x.value))
}

// DB-BLOCK src_db_extract_028
// What: implements `trade_order_id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn trade_order_id(
    field: &str,
    value: &Option<TradeOrderId>,
) -> Result<String, SettlementError> {
    require_uuid_text(field, value.as_ref().map(|x| &x.value))
}

// DB-BLOCK src_db_extract_029
// What: implements `trade_transaction_id`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn trade_transaction_id(
    field: &str,
    value: &Option<TradeTransactionId>,
) -> Result<String, SettlementError> {
    require_uuid_text(field, value.as_ref().map(|x| &x.value))
}

// DB-BLOCK src_db_extract_030
// What: implements `settlement_id_optional`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn settlement_id_optional(
    field: &str,
    value: &Option<SettlementId>,
) -> Result<Option<String>, SettlementError> {
    optional_uuid_text(field, value.as_ref().map(|x| &x.value))
}

// DB-BLOCK src_db_extract_031
// What: implements `quantity`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn quantity(field: &str, value: &Option<Quantity>) -> Result<i64, SettlementError> {
    // DB-BLOCK src_db_extract_032
    // What: binds `value` as a named intermediate.
    // How: computes/extracts `value` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let value = value
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest(format!("{field} is required")))?
        .units;
    // DB-BLOCK src_db_extract_033
    // What: binds `value` as a named intermediate.
    // How: computes/extracts `value` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let value = i64::try_from(value)
        .map_err(|_| SettlementError::InvalidRequest(format!("{field} exceeds i64 range")))?;
    // DB-BLOCK src_db_extract_034
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if value <= 0 {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if value <= 0 {
        // DB-BLOCK src_db_extract_035
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::InvalidRequest(format!("{field} must be positive")))` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::InvalidRequest(format!(
            "{field} must be positive"
        )));
    }
    // DB-BLOCK src_db_extract_036
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(value)`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(value)
}

// DB-BLOCK src_db_extract_037
// What: implements `isk_amount`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn isk_amount(field: &str, value: &Option<IskAmount>) -> Result<i64, SettlementError> {
    // DB-BLOCK src_db_extract_038
    // What: binds `value` as a named intermediate.
    // How: computes/extracts `value` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let value = value
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest(format!("{field} is required")))?
        .minor_units;
    // DB-BLOCK src_db_extract_039
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if value <= 0 {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if value <= 0 {
        // DB-BLOCK src_db_extract_040
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::InvalidRequest(format!("{field} must be positive")))` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::InvalidRequest(format!(
            "{field} must be positive"
        )));
    }
    // DB-BLOCK src_db_extract_041
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(value)`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(value)
}

// DB-BLOCK src_db_extract_042
// What: implements `validate_open_trade_order`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn validate_open_trade_order(req: &OpenTradeOrderRequest) -> Result<(), SettlementError> {
    // DB-BLOCK src_db_extract_043
    // What: binds `terms` as a named intermediate.
    // How: computes/extracts `terms` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let terms = req
        .terms
        .as_ref()
        .ok_or_else(|| SettlementError::InvalidRequest("terms is required".to_string()))?;
    request_id(&req.context)?;
    idempotency_key(&req.context)?;
    validate_terms(terms)
}

// DB-BLOCK src_db_extract_044
// What: implements `validate_terms`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
fn validate_terms(terms: &TradeOrderTerms) -> Result<(), SettlementError> {
    // DB-BLOCK src_db_extract_045
    // What: binds `side` as a named intermediate.
    // How: computes/extracts `side` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let side = OrderSide::from_proto_i32(terms.order_side)?;
    // DB-BLOCK src_db_extract_046
    // What: binds `kind` as a named intermediate.
    // How: computes/extracts `kind` once before SQL or response construction.
    // Why: named intermediates make invariants visible and avoid repeating fallible extraction.
    let kind = ItemKind::from_proto_i32(terms.item_kind)?;
    capsuleer_id("terms.owner_capsuleer_id", &terms.owner_capsuleer_id)?;
    wallet_id("terms.owner_wallet_id", &terms.owner_wallet_id)?;
    item_type_id("terms.item_type_id", &terms.item_type_id)?;
    station_id("terms.station_id", &terms.station_id)?;
    region_id("terms.region_id", &terms.region_id)?;
    quantity("terms.total_quantity", &terms.total_quantity)?;
    isk_amount("terms.unit_price_isk", &terms.unit_price_isk)?;
    // DB-BLOCK src_db_extract_047
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if terms.expires_at.is_none() {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if terms.expires_at.is_none() {
        // DB-BLOCK src_db_extract_048
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::InvalidRequest("terms.expires_at is required".to_str` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::InvalidRequest(
            "terms.expires_at is required".to_string(),
        ));
    }
    // DB-BLOCK src_db_extract_049
    // What: branches across known alternatives.
    // How: uses Rust `match` on `match (side, kind) {`.
    // Why: closed branching is safer than ad-hoc string/boolean decision trees.
    match (side, kind) {
        (OrderSide::Sell, ItemKind::Stackable) => {
            item_stack_id("terms.offered_item_stack_id", &terms.offered_item_stack_id)?;
        }
        (OrderSide::Sell, ItemKind::Singleton) => {
            // DB-BLOCK src_db_extract_050
            // What: exits the current workflow early.
            // How: returns from `return Err(SettlementError::Unsupported("singleton sell orders are not implement` before later mutation blocks execute.
            // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
            return Err(SettlementError::Unsupported(
                "singleton sell orders are not implemented in MVP".to_string(),
            ));
        }
        (OrderSide::Buy, ItemKind::Stackable) => {}
        (OrderSide::Buy, ItemKind::Singleton) => {
            // DB-BLOCK src_db_extract_051
            // What: exits the current workflow early.
            // How: returns from `return Err(SettlementError::Unsupported("singleton buy orders are not implemente` before later mutation blocks execute.
            // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
            return Err(SettlementError::Unsupported(
                "singleton buy orders are not implemented in MVP".to_string(),
            ));
        }
    }
    // DB-BLOCK src_db_extract_052
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(())`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(())
}

// DB-BLOCK src_db_extract_053
// What: implements `validate_close_trade_order`.
// How: performs the smallest focused operation implied by this module and propagates typed errors.
// Why: small named functions make correctness review and testing possible.
pub fn validate_close_trade_order(req: &CloseTradeOrderRequest) -> Result<(), SettlementError> {
    request_id(&req.context)?;
    idempotency_key(&req.context)?;
    trade_order_id("trade_order_id", &req.trade_order_id)?;
    crate::db::types::CloseTarget::from_requested_change(req.requested_change)?;
    // DB-BLOCK src_db_extract_054
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(())`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(())
}

// DB-BLOCK src_db_extract_055
// What: validates structural settlement request fields.
// How: extracts required wrappers and checks positive quantity/price consistency before DB mutation.
// Why: invalid transport input must be rejected before locks or side effects.
pub fn validate_settlement_request(req: &RequestSettlementRequest) -> Result<(), SettlementError> {
    request_id(&req.context)?;
    idempotency_key(&req.context)?;
    trade_order_id("trade_order_id", &req.trade_order_id)?;
    trade_transaction_id("trade_transaction_id", &req.trade_transaction_id)?;
    ItemKind::from_proto_i32(req.item_kind)?;
    // DB-BLOCK src_db_extract_056
    // What: guards a correctness-sensitive branch.
    // How: evaluates `if ItemKind::from_proto_i32(req.item_kind)? != ItemKind::Stackable {` before continuing.
    // Why: bad state, replay, mismatch, or unsupported flow must stop before side effects.
    if ItemKind::from_proto_i32(req.item_kind)? != ItemKind::Stackable {
        // DB-BLOCK src_db_extract_057
        // What: exits the current workflow early.
        // How: returns from `return Err(SettlementError::Unsupported("only stackable settlement is implemente` before later mutation blocks execute.
        // Why: replay/invalid/unsupported paths must not fall through into ownership movement.
        return Err(SettlementError::Unsupported(
            "only stackable settlement is implemented in MVP".to_string(),
        ));
    }
    capsuleer_id("buyer_capsuleer_id", &req.buyer_capsuleer_id)?;
    wallet_id("buyer_wallet_id", &req.buyer_wallet_id)?;
    capsuleer_id("seller_capsuleer_id", &req.seller_capsuleer_id)?;
    wallet_id("seller_wallet_id", &req.seller_wallet_id)?;
    item_type_id("item_type_id", &req.item_type_id)?;
    item_stack_id("source_item_stack_id", &req.source_item_stack_id)?;
    quantity("quantity", &req.quantity)?;
    isk_amount("unit_price_isk", &req.unit_price_isk)?;
    isk_amount("total_price_isk", &req.total_price_isk)?;
    // DB-BLOCK src_db_extract_058
    // What: returns the branch result.
    // How: wraps the computed response/error with `Ok(())`.
    // Why: DB boundaries must propagate success/failure explicitly.
    Ok(())
}
