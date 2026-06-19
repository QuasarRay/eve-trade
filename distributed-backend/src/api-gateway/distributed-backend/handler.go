package distributedbackend

import (
	"context"

	"connectrpc.com/connect"
	apigatewayv1connect "github.com/astral/eve-trade/api-gateway/distributed-backend/gen/api_gateway/v1/apigatewayv1connect"
	marketv1 "github.com/astral/eve-trade/market/distributed-backend/gen/market/v1"
)

var _ apigatewayv1connect.GameTradeGatewayServiceHandler = (*GatewayHandler)(nil)

type GatewayHandler struct {
	market MarketClient
}

func NewGatewayHandler(market MarketClient) *GatewayHandler {
	return &GatewayHandler{market: market}
}

func (h *GatewayHandler) IssueTradeInstance(ctx context.Context, request *connect.Request[marketv1.IssueTradeInstanceRequest]) (*connect.Response[marketv1.IssueTradeInstanceResponse], error) {
	response, err := h.market.IssueTradeInstance(ctx, request.Msg)
	if err != nil {
		return nil, downstreamUnavailable("market", err)
	}
	return connect.NewResponse(response), nil
}

func (h *GatewayHandler) AcceptTradeInstance(ctx context.Context, request *connect.Request[marketv1.AcceptTradeInstanceRequest]) (*connect.Response[marketv1.AcceptTradeInstanceResponse], error) {
	response, err := h.market.AcceptTradeInstance(ctx, request.Msg)
	if err != nil {
		return nil, downstreamUnavailable("market", err)
	}
	return connect.NewResponse(response), nil
}

func (h *GatewayHandler) CancelTradeInstance(ctx context.Context, request *connect.Request[marketv1.CancelTradeInstanceRequest]) (*connect.Response[marketv1.CancelTradeInstanceResponse], error) {
	response, err := h.market.CancelTradeInstance(ctx, request.Msg)
	if err != nil {
		return nil, downstreamUnavailable("market", err)
	}
	return connect.NewResponse(response), nil
}
