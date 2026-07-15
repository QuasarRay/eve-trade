package market

import (
	"context"
	"errors"
	"log/slog"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/observability"
)

var errUnsupportedTradeAction = errors.New("unsupported trade GUI action")

type tradeActionKind string

const (
	tradeActionIssue  tradeActionKind = "issue"
	tradeActionAccept tradeActionKind = "accept"
	tradeActionCancel tradeActionKind = "cancel"
)

var tradeGUIActions = map[string]tradeActionKind{
	"market_place_sell_order":       tradeActionIssue,
	"contract_create_item_exchange": tradeActionIssue,
	"direct_trade_offer":            tradeActionIssue,
	"market_buy_from_sell_order":    tradeActionAccept,
	"contract_accept_item_exchange": tradeActionAccept,
	"direct_trade_accept":           tradeActionAccept,
	"market_cancel_order":           tradeActionCancel,
	"contract_cancel_item_exchange": tradeActionCancel,
	"direct_trade_cancel":           tradeActionCancel,
}

func (h *MarketHandler) SubmitTradeGuiInteraction(ctx context.Context, request *SubmitTradeGuiInteractionRequest) (*SubmitTradeGuiInteractionResponse, error) {
	ctx, validationSpan := observability.StartSpan(ctx, "market.validation")
	defer validationSpan.End()
	if err := validateSubmitTradeGuiInteractionRequest(request); err != nil {
		return nil, err
	}

	interaction, err := decodeTradeGUIInteraction(request.RawPayload)
	if err != nil {
		return nil, err
	}
	validationSpan.Set(slog.String("interaction_id", interaction.GetInteractionId()))
	input := normalizeTradeGUIInput(interaction)

	action := interaction.GetUi().GetAction()
	kind := tradeGUIActions[action]
	validationSpan.Set(slog.String("ui.action", action), slog.String("validation.result", "accepted"))

	switch kind {
	case tradeActionIssue:
		return h.submitIssueTrade(ctx, interaction.GetInteractionId(), input)
	case tradeActionAccept:
		return h.submitAcceptTrade(ctx, interaction.GetInteractionId(), input)
	case tradeActionCancel:
		return h.submitCancelTrade(ctx, interaction.GetInteractionId(), input)
	default:
		return nil, apiError(errs.InvalidArgument, errUnsupportedTradeAction)
	}
}

func (h *MarketHandler) submitIssueTrade(ctx context.Context, interactionID string, input tradeGUIInput) (*SubmitTradeGuiInteractionResponse, error) {
	response, err := h.issueTradeInstance(ctx, issueTradeInstanceRequest{
		IdempotencyKey:      input.IdempotencyKey,
		ExternalRequestID:   input.ExternalRequestID,
		IssuedByCapsuleerID: input.IssuedByCapsuleerID,
		ItemStack:           input.ItemStack,
		Quantity:            input.Quantity,
		UnitPriceISK:        input.UnitPriceISK,
		ExpiresAt:           input.ExpiresAt,
	})
	if err != nil {
		return nil, err
	}
	return &SubmitTradeGuiInteractionResponse{
		InteractionID:     interactionID,
		OperationID:       response.OperationID,
		QueuedAt:          response.QueuedAt,
		Status:            "queued",
		SettlementBatchID: response.SettlementBatchID,
		TradeInstanceID:   response.TradeInstanceID,
		ItemStackEscrowID: response.ItemStackEscrowID,
	}, nil
}

func (h *MarketHandler) submitAcceptTrade(ctx context.Context, interactionID string, input tradeGUIInput) (*SubmitTradeGuiInteractionResponse, error) {
	response, err := h.acceptTradeInstance(ctx, acceptTradeInstanceRequest{
		IdempotencyKey:              input.IdempotencyKey,
		ExternalRequestID:           input.ExternalRequestID,
		TradeInstanceID:             input.TradeInstanceID,
		BuyerCapsuleerID:            input.BuyerCapsuleerID,
		QuantityRequested:           input.QuantityRequested,
		BuyerWalletID:               input.BuyerWalletID,
		BuyerDestinationItemStackID: input.BuyerDestinationItemStackID,
	})
	if err != nil {
		return nil, err
	}
	return &SubmitTradeGuiInteractionResponse{
		InteractionID:               interactionID,
		OperationID:                 response.OperationID,
		QueuedAt:                    response.QueuedAt,
		Status:                      "queued",
		SettlementBatchID:           response.SettlementBatchID,
		WalletEscrowID:              response.WalletEscrowID,
		BuyerDestinationItemStackID: response.BuyerDestinationItemStackID,
	}, nil
}

func (h *MarketHandler) submitCancelTrade(ctx context.Context, interactionID string, input tradeGUIInput) (*SubmitTradeGuiInteractionResponse, error) {
	response, err := h.cancelTradeInstance(ctx, cancelTradeInstanceRequest{
		IdempotencyKey:         input.IdempotencyKey,
		ExternalRequestID:      input.ExternalRequestID,
		TradeInstanceID:        input.TradeInstanceID,
		CancelledByCapsuleerID: input.CancelledByCapsuleerID,
	})
	if err != nil {
		return nil, err
	}
	return &SubmitTradeGuiInteractionResponse{
		InteractionID:     interactionID,
		OperationID:       response.OperationID,
		QueuedAt:          response.QueuedAt,
		Status:            "queued",
		SettlementBatchID: response.SettlementBatchID,
	}, nil
}
