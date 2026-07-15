package market

import (
	"context"
	"fmt"
	"log/slog"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/observability"
	gametrade2 "github.com/QuasarRay/eve-trade/gametrade"
)

func (h *MarketHandler) issueTradeInstance(ctx context.Context, message issueTradeInstanceRequest) (*issueTradeInstanceResult, error) {
	ctx, span := observability.StartSpan(ctx, "market.create_trade_offer",
		slog.String("idempotency_key", message.IdempotencyKey),
		slog.Int64("quantity", message.Quantity),
		slog.String("seller_id_hash", observability.HashIdentifier(message.IssuedByCapsuleerID)),
	)
	defer span.End()
	if err := validateIssueTradeInstanceRequest(message); err != nil {
		return nil, err
	}
	if response, ok, err := h.replayIssueTradeInstance(ctx, message); ok || err != nil {
		return response, err
	}
	itemStack, err := h.trades.LoadItemStack(ctx, message.ItemStack.ItemStackID)
	if err != nil {
		return nil, apiError(errs.FailedPrecondition, err)
	}
	span.Set(
		slog.Int64("item_type_id", itemStack.ItemTypeID),
		slog.Int64("station_id", itemStack.StationID),
	)
	if itemStack.StackState != "ACTIVE" {
		return nil, apiError(errs.FailedPrecondition, fmt.Errorf("item_stack is not ACTIVE"))
	}

	plan, err := gametrade2.IssueTradeInstance(gametrade2.IssueTradeInstanceInput{
		IdempotencyKey:      message.IdempotencyKey,
		ExternalRequestID:   message.ExternalRequestID,
		IssuedByCapsuleerID: message.IssuedByCapsuleerID,
		ItemStack: gametrade2.ItemStackRow{
			ItemStackID: itemStack.ItemStackID,
			OwnerID:     itemStack.OwnerID,
			ItemTypeID:  itemStack.ItemTypeID,
			StationID:   itemStack.StationID,
			Quantity:    itemStack.Quantity,
		},
		Quantity:     message.Quantity,
		UnitPriceISK: message.UnitPriceISK,
		ExpiresAt:    message.ExpiresAt,
	})
	if err != nil {
		span.RecordError(err)
		return nil, apiError(errs.InvalidArgument, err)
	}
	if err := attachRequestFingerprint(&plan, "issue_trade_instance", message); err != nil {
		return nil, apiError(errs.InvalidArgument, err)
	}
	span.Set(slog.String("trade_id", plan.TradeInstanceID), slog.String("trade_state", "OPEN"))

	publication, err := h.executePlan(ctx, plan)
	if err != nil {
		span.RecordError(err)
		return nil, err
	}
	return &issueTradeInstanceResult{
		OperationID:       publication.OperationID,
		QueuedAt:          publication.QueuedAt,
		TradeInstanceID:   plan.TradeInstanceID,
		ItemStackEscrowID: plan.ItemStackEscrowID,
		SettlementBatchID: "",
	}, nil
}
