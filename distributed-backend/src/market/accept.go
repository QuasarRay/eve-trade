package market

import (
	"context"
	"fmt"
	"log/slog"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/observability"
	"github.com/QuasarRay/eve-trade/gametrade"
)

func (h *MarketHandler) acceptTradeInstance(ctx context.Context, message acceptTradeInstanceRequest) (*acceptTradeInstanceResult, error) {
	ctx, span := observability.StartSpan(ctx, "market.accept_trade",
		slog.String("idempotency_key", message.IdempotencyKey),
		slog.String("trade_id", message.TradeInstanceID),
		slog.Int64("quantity", message.QuantityRequested),
		slog.String("buyer_id_hash", observability.HashIdentifier(message.BuyerCapsuleerID)),
	)
	defer span.End()
	if err := validateAcceptTradeInstanceRequest(message); err != nil {
		return nil, err
	}
	if response, ok, err := h.replayAcceptTradeInstance(ctx, message); ok || err != nil {
		return response, err
	}
	trade, err := h.loadAcceptableTrade(ctx, message.TradeInstanceID, message.QuantityRequested)
	if err != nil {
		span.RecordError(err)
		return nil, err
	}
	span.Set(
		slog.String("trade_state", trade.TradeState),
		slog.Int64("item_type_id", trade.ItemTypeID),
		slog.Int64("station_id", trade.StationID),
		slog.String("seller_id_hash", observability.HashIdentifier(trade.IssuerID)),
	)
	buyerWallet, err := h.trades.LoadWallet(ctx, message.BuyerWalletID)
	if err != nil {
		return nil, apiError(errs.FailedPrecondition, err)
	}
	if buyerWallet.CapsuleerID != message.BuyerCapsuleerID {
		return nil, apiError(errs.FailedPrecondition, fmt.Errorf("buyer_wallet_id is not owned by buyer_capsuleer_id"))
	}
	if buyerWallet.WalletState != "ACTIVE" {
		return nil, apiError(errs.FailedPrecondition, fmt.Errorf("buyer wallet is not ACTIVE"))
	}
	sellerWallet, err := h.trades.LoadPrimaryWallet(ctx, trade.IssuerID)
	if err != nil {
		return nil, apiError(errs.FailedPrecondition, err)
	}
	if sellerWallet.WalletState != "ACTIVE" {
		return nil, apiError(errs.FailedPrecondition, fmt.Errorf("seller wallet is not ACTIVE"))
	}
	destinationItemStackID := message.BuyerDestinationItemStackID
	createDestinationItemStack := destinationItemStackID == ""
	if err := h.validateBuyerDestination(ctx, destinationItemStackID, message.BuyerCapsuleerID, trade); err != nil {
		return nil, err
	}
	iskAmountPaid, err := checkedISKAmount(message.QuantityRequested, trade.UnitPriceISK)
	if err != nil {
		return nil, apiError(errs.InvalidArgument, err)
	}

	plan, err := gametrade.AcceptTradeInstance(gametrade.AcceptTradeInstanceInput{
		IdempotencyKey:                  message.IdempotencyKey,
		ExternalRequestID:               message.ExternalRequestID,
		TradeInstanceID:                 message.TradeInstanceID,
		BuyerCapsuleerID:                message.BuyerCapsuleerID,
		SellerCapsuleerID:               trade.IssuerID,
		ItemTypeID:                      trade.ItemTypeID,
		StationID:                       trade.StationID,
		QuantityRequested:               message.QuantityRequested,
		ISKAmountPaid:                   iskAmountPaid,
		BuyerWalletID:                   message.BuyerWalletID,
		SellerWalletID:                  sellerWallet.WalletID,
		ItemStackEscrowID:               trade.ItemStackEscrowID,
		BuyerDestinationItemStackID:     destinationItemStackID,
		CreateBuyerDestinationItemStack: createDestinationItemStack,
		CompleteTrade:                   message.QuantityRequested == trade.EscrowQuantity,
	})
	if err != nil {
		return nil, apiError(errs.InvalidArgument, err)
	}
	if err := attachRequestFingerprint(&plan, "accept_trade_instance", message); err != nil {
		return nil, apiError(errs.InvalidArgument, err)
	}

	publication, err := h.executePlan(ctx, plan)
	if err != nil {
		span.RecordError(err)
		return nil, err
	}
	return &acceptTradeInstanceResult{
		OperationID:                 publication.OperationID,
		QueuedAt:                    publication.QueuedAt,
		WalletEscrowID:              plan.WalletEscrowID,
		BuyerDestinationItemStackID: plan.DestinationItemStackID,
		SettlementBatchID:           "",
	}, nil
}

func (h *MarketHandler) validateBuyerDestination(ctx context.Context, destinationItemStackID string, buyerCapsuleerID int64, trade TradeSnapshot) error {
	if destinationItemStackID == "" {
		return nil
	}
	destination, err := h.trades.LoadItemStack(ctx, destinationItemStackID)
	if err != nil {
		return apiError(errs.FailedPrecondition, err)
	}
	if destination.OwnerID != buyerCapsuleerID {
		return apiError(errs.FailedPrecondition, fmt.Errorf("buyer_destination_item_stack_id is not owned by buyer_capsuleer_id"))
	}
	if destination.StackState != "ACTIVE" {
		return apiError(errs.FailedPrecondition, fmt.Errorf("buyer destination item stack is not ACTIVE"))
	}
	if destination.ItemTypeID != trade.ItemTypeID || destination.StationID != trade.StationID {
		return apiError(errs.FailedPrecondition, fmt.Errorf("buyer destination item stack must match trade item type and station"))
	}
	return nil
}
