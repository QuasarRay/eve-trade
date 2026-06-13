package market

import (
	"errors"
	"fmt"

	evetradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/v1"
)

type transactionFunction string

const (
	transactionFunctionIssueTradeInstance  transactionFunction = "issue_trade_instance"
	transactionFunctionSettleTradeInstance transactionFunction = "settle_trade_instance"
	transactionFunctionCancelTradeInstance transactionFunction = "cancel_trade_instance"
)

var (
	ErrMissingInteraction          = errors.New("project trade interaction is required")
	ErrMissingInteractionID        = errors.New("project trade interaction.interaction_id is required")
	ErrMissingSourceActivityID     = errors.New("project trade interaction.source_activity_id is required")
	ErrMissingCorrelationID        = errors.New("project trade interaction.correlation_id is required")
	ErrMissingTraceID              = errors.New("project trade interaction.trace_id is required")
	ErrMissingCapsuleerID          = errors.New("project trade interaction.capsuleer_id is required")
	ErrMissingGameSessionID        = errors.New("project trade interaction.game_session_id is required")
	ErrInvalidInteractionKind      = errors.New("project trade interaction.interaction_kind is invalid")
	ErrInvalidTradeButton          = errors.New("project trade interaction.trade_button contradicts interaction_kind")
	ErrInvalidTradeWindow          = errors.New("project trade interaction.trade_window is required")
	ErrMissingVisibleTradeID       = errors.New("visible_trade_instance_id is required for existing trade interactions")
	ErrMissingSelectedItem         = errors.New("at least one selected item is required to issue a trade instance")
	ErrMissingSelectedItemType     = errors.New("selected item.item_type_id is required")
	ErrMissingSelectedItemQuantity = errors.New("selected item.quantity must be greater than zero")
	ErrMissingTransactionMetadata  = errors.New("trade lifecycle decision is missing transaction metadata")
	ErrMissingSettlementOutcome    = errors.New("trade-settlement response is missing transaction outcome")
)

type tradeLifecycleDecisionDraft struct {
	requiredFunction transactionFunction
	interaction      *evetradev1.ProjectTradeInteraction
}

func newTradeLifecycleDecisionDraft(requiredFunction transactionFunction, interaction *evetradev1.ProjectTradeInteraction) *tradeLifecycleDecisionDraft {
	return &tradeLifecycleDecisionDraft{
		requiredFunction: requiredFunction,
		interaction:      interaction,
	}
}

func validateProjectTradeInteraction(interaction *evetradev1.ProjectTradeInteraction) error {
	if interaction == nil {
		return ErrMissingInteraction
	}
	if interaction.GetInteractionId().GetValue() == "" {
		return ErrMissingInteractionID
	}
	if interaction.GetSourceActivityId().GetValue() == "" {
		return ErrMissingSourceActivityID
	}
	if interaction.GetCorrelationId().GetValue() == "" {
		return ErrMissingCorrelationID
	}
	if interaction.GetTraceId().GetValue() == "" {
		return ErrMissingTraceID
	}
	if interaction.GetCapsuleerId().GetValue() == 0 {
		return ErrMissingCapsuleerID
	}
	if interaction.GetGameSessionId().GetValue() == "" {
		return ErrMissingGameSessionID
	}
	if interaction.GetTradeWindow() == evetradev1.KnownTradeWindow_KNOWN_TRADE_WINDOW_UNSPECIFIED {
		return ErrInvalidTradeWindow
	}

	return validateButtonMatchesInteractionKind(interaction)
}

func validateButtonMatchesInteractionKind(interaction *evetradev1.ProjectTradeInteraction) error {
	button := interaction.GetTradeButton()
	if button == evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_UNSPECIFIED {
		return nil
	}

	switch interaction.GetInteractionKind() {
	case evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_ISSUED_VISIBLE_TRADE:
		if button == evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_ISSUE {
			return nil
		}
	case evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_ACCEPTED_VISIBLE_TRADE:
		if button == evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_ACCEPT {
			return nil
		}
	case evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_CANCELLED_VISIBLE_TRADE:
		if button == evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_CANCEL {
			return nil
		}
	}

	return ErrInvalidTradeButton
}

func validateIssueInteraction(interaction *evetradev1.ProjectTradeInteraction) error {
	if len(interaction.GetSelectedItems()) == 0 {
		return ErrMissingSelectedItem
	}
	for index, selected := range interaction.GetSelectedItems() {
		if selected.GetItemTypeId().GetValue() == 0 {
			return fmt.Errorf("%w at index %d", ErrMissingSelectedItemType, index)
		}
		if selected.GetQuantity().GetValue() <= 0 {
			return fmt.Errorf("%w at index %d", ErrMissingSelectedItemQuantity, index)
		}
	}

	return nil
}

func validateExistingTradeInteraction(interaction *evetradev1.ProjectTradeInteraction, _ string) error {
	if interaction.GetVisibleTradeInstanceId().GetValue() == "" {
		return ErrMissingVisibleTradeID
	}

	return nil
}

func tradeTypeNameForWindow(window evetradev1.KnownTradeWindow) string {
	switch window {
	case evetradev1.KnownTradeWindow_KNOWN_TRADE_WINDOW_MARKET_WINDOW:
		return "market"
	case evetradev1.KnownTradeWindow_KNOWN_TRADE_WINDOW_AUCTION_WINDOW:
		return "auction"
	case evetradev1.KnownTradeWindow_KNOWN_TRADE_WINDOW_DIRECT_TRADE_WINDOW:
		return "direct_trade"
	case evetradev1.KnownTradeWindow_KNOWN_TRADE_WINDOW_CONTRACT_WINDOW:
		return "contract"
	default:
		return "unspecified"
	}
}
