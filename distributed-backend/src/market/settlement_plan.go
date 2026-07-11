package market

import (
	"context"
	"fmt"
	"log/slog"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/observability"
	"github.com/QuasarRay/eve-trade/gametrade"
)

func (h *MarketHandler) executePlan(ctx context.Context, plan gametrade.SettlementPlan) (*SettlementPublication, error) {
	ctx, span := observability.StartSpan(ctx, "market.build_settlement_operations",
		slog.String("idempotency_key", plan.IdempotencyKey),
		slog.String("trade_id", plan.TradeInstanceID),
		slog.Int("settlement.operation_count", len(plan.Operations)),
	)
	defer span.End()
	settlementRequest, err := gametrade.SettleTradeInstance(plan)
	if err != nil {
		span.RecordError(err)
		return nil, apiError(errs.InvalidArgument, err)
	}
	publication, err := h.settlement.PublishSettlementWork(ctx, settlementRequest)
	if err != nil {
		span.RecordError(err)
		return nil, apiError(errs.Unavailable, fmt.Errorf("publish settlement work: %w", err))
	}
	span.Set(
		slog.String("settlement.message_id", publication.MessageID),
		slog.String("settlement.operation_id", publication.OperationID),
	)
	return publication, nil
}
