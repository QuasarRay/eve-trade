package market

import (
	"context"
	"log/slog"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/observability"
	"github.com/QuasarRay/eve-trade/gametrade"
)

func (h *MarketHandler) cancelTradeInstance(ctx context.Context, message cancelTradeInstanceRequest) (*cancelTradeInstanceResult, error) {
	ctx, span := observability.StartSpan(ctx, "market.cancel_trade",
		slog.String("idempotency_key", message.IdempotencyKey),
		slog.String("trade_id", message.TradeInstanceID),
		slog.String("seller_id_hash", observability.HashIdentifier(message.CancelledByCapsuleerID)),
	)
	defer span.End()
	if err := validateCancelTradeInstanceRequest(message); err != nil {
		return nil, err
	}
	if response, ok, err := h.replayCancelTradeInstance(ctx, message); ok || err != nil {
		return response, err
	}
	trade, err := h.loadCancellableTrade(ctx, message.TradeInstanceID, message.CancelledByCapsuleerID)
	if err != nil {
		span.RecordError(err)
		return nil, err
	}
	span.Set(slog.String("trade_state", trade.TradeState), slog.Int64("quantity", trade.EscrowQuantity))
	plan, err := gametrade.CancelTradeInstance(gametrade.CancelTradeInstanceInput{
		IdempotencyKey:         message.IdempotencyKey,
		ExternalRequestID:      message.ExternalRequestID,
		TradeInstanceID:        message.TradeInstanceID,
		CancelledByCapsuleerID: message.CancelledByCapsuleerID,
		ItemStackEscrowID:      trade.ItemStackEscrowID,
		ReturnItemStackID:      trade.SourceItemStackID,
		ReturnQuantity:         trade.EscrowQuantity,
	})
	if err != nil {
		return nil, apiError(errs.InvalidArgument, err)
	}
	if err := attachRequestFingerprint(&plan, "cancel_trade_instance", message); err != nil {
		return nil, apiError(errs.InvalidArgument, err)
	}

	publication, err := h.executePlan(ctx, plan)
	if err != nil {
		span.RecordError(err)
		return nil, err
	}
	return &cancelTradeInstanceResult{
		OperationID:       publication.OperationID,
		QueuedAt:          publication.QueuedAt,
		SettlementBatchID: "",
	}, nil
}
