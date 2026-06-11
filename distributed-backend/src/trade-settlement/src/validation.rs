use crate::error::SettlementError;
use crate::generated::settlement::{SettleTradeRequest, TradeAction};

// This block validates the whole request before any database lock is taken.
// Settlement treats the request from market as the authoritative instruction,
// so malformed player/item/money fields must be rejected before state changes.
pub fn validate_request(req: &SettleTradeRequest) -> Result<TradeAction, SettlementError> {
    require_text("request_id", &req.request_id)?;
    require_text("trade_id", &req.trade_id)?;
    require_text("item_owner_id", &req.item_owner_id)?;
    require_text("item_receiver_id", &req.item_receiver_id)?;
    require_text("isk_payer_id", &req.isk_payer_id)?;
    require_text("isk_receiver_id", &req.isk_receiver_id)?;
    require_text("item_type_id", &req.item_type_id)?;

    if req.item_owner_id == req.item_receiver_id {
        return Err(SettlementError::InvalidRequest(
            "item_owner_id and item_receiver_id must be different".to_string(),
        ));
    }

    if req.isk_payer_id == req.isk_receiver_id {
        return Err(SettlementError::InvalidRequest(
            "isk_payer_id and isk_receiver_id must be different".to_string(),
        ));
    }

    if req.quantity <= 0 {
        return Err(SettlementError::InvalidRequest(
            "quantity must be greater than zero".to_string(),
        ));
    }

    if req.isk_units <= 0 {
        return Err(SettlementError::InvalidRequest(
            "isk_units must be greater than zero".to_string(),
        ));
    }

    let action = TradeAction::try_from(req.action).map_err(|_| {
        SettlementError::InvalidRequest(format!("unknown trade action value: {}", req.action))
    })?;

    if action == TradeAction::Unspecified {
        return Err(SettlementError::InvalidRequest(
            "TRADE_ACTION_UNSPECIFIED is not allowed".to_string(),
        ));
    }

    Ok(action)
}

// This helper exists so every string identity field uses the same emptiness rule
// and future trimming/normalization behavior can be changed in one place.
fn require_text(field: &str, value: &str) -> Result<(), SettlementError> {
    if value.trim().is_empty() {
        return Err(SettlementError::InvalidRequest(format!(
            "{field} must not be empty"
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // This helper builds a valid request so each validation test can change only
    // the field it is trying to prove.
    fn valid_request() -> SettleTradeRequest {
        SettleTradeRequest {
            request_id: "request-1".to_string(),
            trade_id: "trade-1".to_string(),
            action: TradeAction::Prepare as i32,
            item_owner_id: "seller".to_string(),
            item_receiver_id: "buyer".to_string(),
            isk_payer_id: "buyer".to_string(),
            isk_receiver_id: "seller".to_string(),
            item_type_id: "tritanium".to_string(),
            quantity: 100,
            isk_units: 500,
        }
    }

    // This test protects against no-op item movement, which would make the trade
    // look valid without changing ownership.
    #[test]
    fn rejects_same_item_owner_and_receiver() {
        let mut req = valid_request();
        req.item_receiver_id = req.item_owner_id.clone();
        assert!(validate_request(&req).is_err());
    }

    // This test protects against settlement requests that could accidentally
    // create meaningless zero-value trades.
    #[test]
    fn rejects_non_positive_amounts() {
        let mut req = valid_request();
        req.quantity = 0;
        assert!(validate_request(&req).is_err());

        let mut req = valid_request();
        req.isk_units = 0;
        assert!(validate_request(&req).is_err());
    }
}
