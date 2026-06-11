use crate::error::SettlementError;
use crate::generated::settlement::{TradeAction, TradeState};

// This block converts durable database strings into proto enum states. The
// database is the source of truth for trade state, while protobuf is the network
// representation returned to market.
pub fn state_from_db(value: &str) -> Result<TradeState, SettlementError> {
    match value {
        "being_created" => Ok(TradeState::BeingCreated),
        "outstanding" => Ok(TradeState::Outstanding),
        "accepted" => Ok(TradeState::Accepted),
        "in_progress" => Ok(TradeState::InProgress),
        "completed" => Ok(TradeState::Completed),
        "claimable" => Ok(TradeState::Claimable),
        "claimed" => Ok(TradeState::Claimed),
        "expired" => Ok(TradeState::Expired),
        "failed" => Ok(TradeState::Failed),
        "cancelled" => Ok(TradeState::Cancelled),
        other => Err(SettlementError::InvalidRequest(format!(
            "database contains unknown trade state: {other}"
        ))),
    }
}

// This block converts proto state values into the exact lowercase domain names
// stored in Postgres. Using one function prevents spelling drift across SQL.
pub fn state_to_db(state: TradeState) -> Result<&'static str, SettlementError> {
    match state {
        TradeState::BeingCreated => Ok("being_created"),
        TradeState::Outstanding => Ok("outstanding"),
        TradeState::Accepted => Ok("accepted"),
        TradeState::InProgress => Ok("in_progress"),
        TradeState::Completed => Ok("completed"),
        TradeState::Claimable => Ok("claimable"),
        TradeState::Claimed => Ok("claimed"),
        TradeState::Expired => Ok("expired"),
        TradeState::Failed => Ok("failed"),
        TradeState::Cancelled => Ok("cancelled"),
        TradeState::Unspecified => Err(SettlementError::InvalidRequest(
            "TRADE_STATE_UNSPECIFIED cannot be stored".to_string(),
        )),
    }
}

// This block maps action enum values to stable strings for idempotency records.
// Stable action strings make it easy to audit which market request produced a
// recorded settlement result.
pub fn action_to_db(action: TradeAction) -> Result<&'static str, SettlementError> {
    match action {
        TradeAction::Prepare => Ok("prepare"),
        TradeAction::Issue => Ok("issue"),
        TradeAction::Accept => Ok("accept"),
        TradeAction::Start => Ok("start"),
        TradeAction::Complete => Ok("complete"),
        TradeAction::MakeClaimable => Ok("make_claimable"),
        TradeAction::Claim => Ok("claim"),
        TradeAction::Expire => Ok("expire"),
        TradeAction::Fail => Ok("fail"),
        TradeAction::Cancel => Ok("cancel"),
        TradeAction::Unspecified => Err(SettlementError::InvalidRequest(
            "TRADE_ACTION_UNSPECIFIED is not a settlement operation".to_string(),
        )),
    }
}

// This block identifies terminal states. Terminal states reject further state
// changes so settled, failed, expired, or cancelled trades cannot be mutated by
// a later retry or bad market request.
pub fn is_terminal(state: TradeState) -> bool {
    matches!(
        state,
        TradeState::Claimed | TradeState::Expired | TradeState::Failed | TradeState::Cancelled
    )
}

// This block centralizes every legal non-asset-moving transition. COMPLETE is
// deliberately excluded because it is not just a state transition: it is legal
// only if the database ownership transfer commits successfully.
pub fn next_state_without_asset_movement(
    current: TradeState,
    action: TradeAction,
) -> Result<TradeState, SettlementError> {
    use TradeAction::*;
    use TradeState::*;

    match (current, action) {
        (BeingCreated, Issue) => Ok(Outstanding),
        (Outstanding, Issue) => Ok(Outstanding),

        (Outstanding, Accept) => Ok(Accepted),
        (Accepted, Accept) => Ok(Accepted),

        (Accepted, Start) => Ok(InProgress),
        (InProgress, Start) => Ok(InProgress),

        (Completed, MakeClaimable) => Ok(Claimable),
        (Claimable, MakeClaimable) => Ok(Claimable),

        (Claimable, Claim) => Ok(Claimed),
        (Claimed, Claim) => Ok(Claimed),

        (BeingCreated, Expire) | (Outstanding, Expire) | (Accepted, Expire) => Ok(Expired),
        (Expired, Expire) => Ok(Expired),

        (Accepted, Fail) | (InProgress, Fail) => Ok(Failed),
        (Failed, Fail) => Ok(Failed),

        (BeingCreated, Cancel) | (Outstanding, Cancel) | (Accepted, Cancel) => Ok(Cancelled),
        (Cancelled, Cancel) => Ok(Cancelled),

        (from, action) => Err(SettlementError::InvalidTransition {
            from,
            action: action_to_db(action)?,
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // This test proves the successful lifecycle can pass through every expected
    // pre-completion and post-completion state without jumping directly to a
    // terminal state.
    #[test]
    fn non_asset_lifecycle_transitions_are_legal() {
        assert_eq!(
            next_state_without_asset_movement(TradeState::BeingCreated, TradeAction::Issue)
                .unwrap(),
            TradeState::Outstanding
        );
        assert_eq!(
            next_state_without_asset_movement(TradeState::Outstanding, TradeAction::Accept)
                .unwrap(),
            TradeState::Accepted
        );
        assert_eq!(
            next_state_without_asset_movement(TradeState::Accepted, TradeAction::Start).unwrap(),
            TradeState::InProgress
        );
        assert_eq!(
            next_state_without_asset_movement(TradeState::Completed, TradeAction::MakeClaimable)
                .unwrap(),
            TradeState::Claimable
        );
        assert_eq!(
            next_state_without_asset_movement(TradeState::Claimable, TradeAction::Claim).unwrap(),
            TradeState::Claimed
        );
    }

    // This test proves COMPLETED is not reachable through the generic state
    // function. It must come from the database transfer path in db.rs.
    #[test]
    fn completed_is_not_a_plain_state_transition() {
        assert!(next_state_without_asset_movement(TradeState::InProgress, TradeAction::Complete)
            .is_err());
    }

    // This test protects terminal-state correctness. A cancelled trade cannot be
    // accidentally restarted or accepted later.
    #[test]
    fn terminal_states_reject_unrelated_actions() {
        assert!(next_state_without_asset_movement(TradeState::Cancelled, TradeAction::Accept)
            .is_err());
    }
}
