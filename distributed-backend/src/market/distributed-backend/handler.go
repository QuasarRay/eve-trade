package distributedbackend

import (
	"context"
	"errors"

	"connectrpc.com/connect"
	marketv1 "github.com/astral/eve-trade/market/distributed-backend/gen/market/v1"
	marketv1connect "github.com/astral/eve-trade/market/distributed-backend/gen/market/v1/marketv1connect"
	tradesettlementv1 "github.com/astral/eve-trade/market/distributed-backend/gen/trade_settlement/v1"
	gametrade "github.com/astral/eve-trade/market/game-trade"
)

var _ marketv1connect.MarketServiceHandler = (*MarketHandler)(nil)

type MarketHandler struct {
	settlement SettlementExecutor
}

func NewMarketHandler(settlement SettlementExecutor) *MarketHandler {
	return &MarketHandler{settlement: settlement}
}

func (h *MarketHandler) IssueTradeInstance(ctx context.Context, request *connect.Request[marketv1.IssueTradeInstanceRequest]) (*connect.Response[marketv1.IssueTradeInstanceResponse], error) {
	message := request.Msg
	if message.ItemStack == nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("item_stack is required"))
	}

	plan, err := gametrade.IssueTradeInstance(gametrade.IssueTradeInstanceInput{
		IdempotencyKey:      message.IdempotencyKey,
		ExternalRequestID:   message.ExternalRequestId,
		IssuedByCapsuleerID: message.IssuedByCapsuleerId,
		ItemStack: gametrade.ItemStackRow{
			ItemStackID: message.ItemStack.ItemStackId,
			OwnerID:     message.ItemStack.OwnerId,
			ItemTypeID:  message.ItemStack.ItemTypeId,
			StationID:   message.ItemStack.StationId,
			Quantity:    message.ItemStack.Quantity,
		},
		Quantity:     message.Quantity,
		UnitPriceISK: message.UnitPriceIsk,
		ExpiresAt:    message.ExpiresAt,
	})
	if err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}

	settlementResponse, err := h.executePlan(ctx, plan)
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.IssueTradeInstanceResponse{
		TradeInstanceId:   plan.TradeInstanceID,
		ItemStackEscrowId: plan.ItemStackEscrowID,
		SettlementBatchId: settlementResponse.SettlementBatchId,
	}), nil
}

func (h *MarketHandler) AcceptTradeInstance(ctx context.Context, request *connect.Request[marketv1.AcceptTradeInstanceRequest]) (*connect.Response[marketv1.AcceptTradeInstanceResponse], error) {
	message := request.Msg
	plan, err := gametrade.AcceptTradeInstance(gametrade.AcceptTradeInstanceInput{
		IdempotencyKey:                  message.IdempotencyKey,
		ExternalRequestID:               message.ExternalRequestId,
		TradeInstanceID:                 message.TradeInstanceId,
		BuyerCapsuleerID:                message.BuyerCapsuleerId,
		SellerCapsuleerID:               message.SellerCapsuleerId,
		ItemTypeID:                      message.ItemTypeId,
		StationID:                       message.StationId,
		QuantityRequested:               message.QuantityRequested,
		ISKAmountPaid:                   message.IskAmountPaid,
		BuyerWalletID:                   message.BuyerWalletId,
		SellerWalletID:                  message.SellerWalletId,
		ItemStackEscrowID:               message.ItemStackEscrowId,
		BuyerDestinationItemStackID:     message.BuyerDestinationItemStackId,
		CreateBuyerDestinationItemStack: message.CreateBuyerDestinationItemStack,
	})
	if err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}

	settlementResponse, err := h.executePlan(ctx, plan)
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.AcceptTradeInstanceResponse{
		WalletEscrowId:              plan.WalletEscrowID,
		BuyerDestinationItemStackId: plan.DestinationItemStackID,
		SettlementBatchId:           settlementResponse.SettlementBatchId,
	}), nil
}

func (h *MarketHandler) CancelTradeInstance(ctx context.Context, request *connect.Request[marketv1.CancelTradeInstanceRequest]) (*connect.Response[marketv1.CancelTradeInstanceResponse], error) {
	message := request.Msg
	plan, err := gametrade.CancelTradeInstance(gametrade.CancelTradeInstanceInput{
		IdempotencyKey:         message.IdempotencyKey,
		ExternalRequestID:      message.ExternalRequestId,
		TradeInstanceID:        message.TradeInstanceId,
		CancelledByCapsuleerID: message.CancelledByCapsuleerId,
		ItemStackEscrowID:      message.ItemStackEscrowId,
		ReturnItemStackID:      message.ReturnItemStackId,
		ReturnQuantity:         message.ReturnQuantity,
		WalletEscrowID:         message.WalletEscrowId,
		ReturnWalletID:         message.ReturnWalletId,
		ReturnISKAmount:        message.ReturnIskAmount,
	})
	if err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}

	settlementResponse, err := h.executePlan(ctx, plan)
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.CancelTradeInstanceResponse{
		SettlementBatchId: settlementResponse.SettlementBatchId,
	}), nil
}

func (h *MarketHandler) executePlan(ctx context.Context, plan gametrade.SettlementPlan) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	settlementRequest, err := gametrade.SettleTradeInstance(plan)
	if err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}
	response, err := h.settlement.ExecuteSettlementBatch(ctx, settlementRequest)
	if err != nil {
		return nil, downstreamUnavailable("trade-settlement", err)
	}
	return response, nil
}
