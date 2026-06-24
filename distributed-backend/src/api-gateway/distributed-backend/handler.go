package distributedbackend

import (
	"context"

	"connectrpc.com/connect"
	api_gatewayv1connect "github.com/QuasarRay/eve-trade/proto/gen/eve/api_gateway/v1/api_gatewayv1connect"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
)

var _ api_gatewayv1connect.GameTradeGatewayServiceHandler = (*GatewayHandler)(nil)

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

func (h *GatewayHandler) SubmitTradeGuiInteraction(ctx context.Context, request *connect.Request[marketv1.SubmitTradeGuiInteractionRequest]) (*connect.Response[marketv1.SubmitTradeGuiInteractionResponse], error) {
	response, err := h.market.SubmitTradeGuiInteraction(ctx, request.Msg)
	if err != nil {
		return nil, downstreamUnavailable("market", err)
	}
	return connect.NewResponse(response), nil
}
