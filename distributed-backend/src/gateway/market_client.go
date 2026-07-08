package gateway

import (
	"context"

	"github.com/QuasarRay/eve-trade/distributed-backend/src/market"
)

type MarketClient interface {
	SubmitTradeGuiInteraction(ctx context.Context, request *market.SubmitTradeGuiInteractionRequest) (*market.SubmitTradeGuiInteractionResponse, error)
}

type EncoreMarketClient struct{}

func (EncoreMarketClient) SubmitTradeGuiInteraction(ctx context.Context, request *market.SubmitTradeGuiInteractionRequest) (*market.SubmitTradeGuiInteractionResponse, error) {
	return market.SubmitTradeGuiInteraction(ctx, request)
}
