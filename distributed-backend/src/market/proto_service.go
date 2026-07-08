package market

import (
	"context"

	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
)

type ProtoService struct {
	handler *MarketHandler
}

func NewProtoService(handler *MarketHandler) ProtoService {
	return ProtoService{handler: handler}
}

func (s ProtoService) IssueTradeInstance(ctx context.Context, request *marketv1.IssueTradeInstanceRequest) (*marketv1.IssueTradeInstanceResponse, error) {
	response, err := s.handler.issueTradeInstance(ctx, issueTradeInstanceRequest{
		IdempotencyKey:      request.GetIdempotencyKey(),
		ExternalRequestID:   request.GetExternalRequestId(),
		IssuedByCapsuleerID: request.GetIssuedByCapsuleerId(),
		ItemStack:           itemStackInputFromProto(request.GetItemStack()),
		Quantity:            request.GetQuantity(),
		UnitPriceISK:        request.GetUnitPriceIsk(),
		ExpiresAt:           request.GetExpiresAt(),
	})
	if err != nil {
		return nil, err
	}
	return &marketv1.IssueTradeInstanceResponse{
		TradeInstanceId:   response.TradeInstanceID,
		ItemStackEscrowId: response.ItemStackEscrowID,
		SettlementBatchId: response.SettlementBatchID,
	}, nil
}

func (s ProtoService) AcceptTradeInstance(ctx context.Context, request *marketv1.AcceptTradeInstanceRequest) (*marketv1.AcceptTradeInstanceResponse, error) {
	response, err := s.handler.acceptTradeInstance(ctx, acceptTradeInstanceRequest{
		IdempotencyKey:              request.GetIdempotencyKey(),
		ExternalRequestID:           request.GetExternalRequestId(),
		TradeInstanceID:             request.GetTradeInstanceId(),
		BuyerCapsuleerID:            request.GetBuyerCapsuleerId(),
		QuantityRequested:           request.GetQuantityRequested(),
		BuyerWalletID:               request.GetBuyerWalletId(),
		BuyerDestinationItemStackID: request.GetBuyerDestinationItemStackId(),
	})
	if err != nil {
		return nil, err
	}
	return &marketv1.AcceptTradeInstanceResponse{
		WalletEscrowId:              response.WalletEscrowID,
		BuyerDestinationItemStackId: response.BuyerDestinationItemStackID,
		SettlementBatchId:           response.SettlementBatchID,
	}, nil
}

func (s ProtoService) CancelTradeInstance(ctx context.Context, request *marketv1.CancelTradeInstanceRequest) (*marketv1.CancelTradeInstanceResponse, error) {
	response, err := s.handler.cancelTradeInstance(ctx, cancelTradeInstanceRequest{
		IdempotencyKey:         request.GetIdempotencyKey(),
		ExternalRequestID:      request.GetExternalRequestId(),
		TradeInstanceID:        request.GetTradeInstanceId(),
		CancelledByCapsuleerID: request.GetCancelledByCapsuleerId(),
	})
	if err != nil {
		return nil, err
	}
	return &marketv1.CancelTradeInstanceResponse{
		SettlementBatchId: response.SettlementBatchID,
	}, nil
}

func (s ProtoService) SubmitTradeGuiInteraction(ctx context.Context, request *marketv1.SubmitTradeGuiInteractionRequest) (*marketv1.SubmitTradeGuiInteractionResponse, error) {
	response, err := s.handler.SubmitTradeGuiInteraction(ctx, &SubmitTradeGuiInteractionRequest{
		RawPayload: request.GetRawPayload(),
	})
	if err != nil {
		return nil, err
	}
	return &marketv1.SubmitTradeGuiInteractionResponse{
		InteractionId:               response.InteractionID,
		Status:                      response.Status,
		SettlementBatchId:           response.SettlementBatchID,
		TradeInstanceId:             response.TradeInstanceID,
		ItemStackEscrowId:           response.ItemStackEscrowID,
		WalletEscrowId:              response.WalletEscrowID,
		BuyerDestinationItemStackId: response.BuyerDestinationItemStackID,
	}, nil
}
