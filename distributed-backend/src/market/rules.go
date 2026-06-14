package market

import (
	"errors"
	"fmt"

	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/market/v1"
	operationv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/operation/v1"
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
	ErrMissingVisibleTradeContext  = errors.New("project trade interaction.visible_trade_context is required")
	ErrMissingVisibleTradeID       = errors.New("visible_trade_context.trade_instance_id is required for existing trade interactions")
	ErrMissingVisibleWalletID      = errors.New("visible_trade_context.wallet_id is required")
	ErrMissingVisibleStationID     = errors.New("visible_trade_context.station_id is required")
	ErrMissingVisibleRegionID      = errors.New("visible_trade_context.region_id is required")
	ErrMissingSourceItemStackID    = errors.New("source item stack id is required")
	ErrMissingDestinationStackID   = errors.New("destination item stack id is required")
	ErrMissingSelectedItem         = errors.New("at least one selected item is required to issue a trade instance")
	ErrMissingSelectedItemType     = errors.New("selected item.item_type_id is required")
	ErrMissingSelectedItemQuantity = errors.New("selected item.quantity must be greater than zero")
	ErrMissingSettlementCommand    = errors.New("trade decision is missing settlement command")
	ErrMissingSettlementResult     = errors.New("trade-settlement response is missing settlement result")
)

type tradeLifecycleDecisionDraft struct {
	requiredOperation operationv1.TradeOperationKind
	interaction       *marketv1.ProjectTradeInteraction
}

func newTradeLifecycleDecisionDraft(requiredOperation operationv1.TradeOperationKind, interaction *marketv1.ProjectTradeInteraction) *tradeLifecycleDecisionDraft {
	return &tradeLifecycleDecisionDraft{
		requiredOperation: requiredOperation,
		interaction:       interaction,
	}
}

func validateProjectTradeInteraction(interaction *marketv1.ProjectTradeInteraction) error {
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
	if interaction.GetTradeWindow() == marketv1.KnownTradeWindow_KNOWN_TRADE_WINDOW_UNSPECIFIED {
		return ErrInvalidTradeWindow
	}

	return validateButtonMatchesInteractionKind(interaction)
}

func validateButtonMatchesInteractionKind(interaction *marketv1.ProjectTradeInteraction) error {
	button := interaction.GetTradeButton()
	if button == marketv1.KnownTradeButton_KNOWN_TRADE_BUTTON_UNSPECIFIED {
		return nil
	}

	switch interaction.GetInteractionKind() {
	case marketv1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_ISSUED_VISIBLE_TRADE:
		if button == marketv1.KnownTradeButton_KNOWN_TRADE_BUTTON_ISSUE {
			return nil
		}
	case marketv1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_ACCEPTED_VISIBLE_TRADE:
		if button == marketv1.KnownTradeButton_KNOWN_TRADE_BUTTON_ACCEPT {
			return nil
		}
	case marketv1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_CANCELLED_VISIBLE_TRADE:
		if button == marketv1.KnownTradeButton_KNOWN_TRADE_BUTTON_CANCEL {
			return nil
		}
	}

	return ErrInvalidTradeButton
}

func validateIssueInteraction(interaction *marketv1.ProjectTradeInteraction) error {
	if err := validateVisibleContextForIssue(interaction.GetVisibleTradeContext()); err != nil {
		return err
	}
	if len(interaction.GetSelectedItems()) == 0 {
		return ErrMissingSelectedItem
	}
	for index, selected := range interaction.GetSelectedItems() {
		if selected.GetItemTypeId().GetValue() == 0 {
			return fmt.Errorf("%w at index %d", ErrMissingSelectedItemType, index)
		}
		if selected.GetQuantity().GetUnits() <= 0 {
			return fmt.Errorf("%w at index %d", ErrMissingSelectedItemQuantity, index)
		}
	}

	return nil
}

func validateVisibleContextForIssue(context *marketv1.VisibleTradeContext) error {
	if context == nil {
		return ErrMissingVisibleTradeContext
	}
	if context.GetWalletId().GetValue() == "" {
		return ErrMissingVisibleWalletID
	}
	if context.GetStationId().GetValue() == 0 {
		return ErrMissingVisibleStationID
	}
	if context.GetRegionId().GetValue() == 0 {
		return ErrMissingVisibleRegionID
	}
	if context.GetSourceItemStackId().GetValue() == "" {
		return ErrMissingSourceItemStackID
	}

	return nil
}

func validateSettleInteraction(interaction *marketv1.ProjectTradeInteraction) error {
	if err := validateExistingTradeInteraction(interaction, "accepted visible trade"); err != nil {
		return err
	}
	if interaction.GetVisibleTradeContext().GetWalletId().GetValue() == "" {
		return ErrMissingVisibleWalletID
	}
	if interaction.GetVisibleTradeContext().GetDestinationItemStackId().GetValue() == "" {
		return ErrMissingDestinationStackID
	}
	if interaction.GetTypedValues().GetQuantity().GetUnits() <= 0 {
		return ErrMissingSelectedItemQuantity
	}

	return nil
}

func validateExistingTradeInteraction(interaction *marketv1.ProjectTradeInteraction, _ string) error {
	if interaction.GetVisibleTradeContext() == nil {
		return ErrMissingVisibleTradeContext
	}
	if interaction.GetVisibleTradeContext().GetTradeInstanceId().GetValue() == "" {
		return ErrMissingVisibleTradeID
	}

	return nil
}
