package market

import (
	"context"
	"net/http"

	"connectrpc.com/connect"
	settlementv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/settlement/v1"
	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/settlement/v1/settlementv1connect"
)

// Settlement defines the exact settlement operations market depends on.
// It mirrors the current settlement/v1 proto RPC names and keeps generated
// transport clients behind a small interface so tests can inject a fake
// settlement implementation. It exists to prevent market logic from importing
// stale RPC names such as OpenMarketOrder, CancelMarketOrder, or SettleFill.
type Settlement interface {
	OpenTradeOrder(context.Context, *settlementv1.OpenTradeOrderRequest) (*settlementv1.OpenTradeOrderResponse, error)
	CloseTradeOrder(context.Context, *settlementv1.CloseTradeOrderRequest) (*settlementv1.CloseTradeOrderResponse, error)
	RequestSettlement(context.Context, *settlementv1.RequestSettlementRequest) (*settlementv1.RequestSettlementResponse, error)
	ClaimResult(context.Context, *settlementv1.ClaimResultRequest) (*settlementv1.ClaimResultResponse, error)
	GetTradeOrder(context.Context, *settlementv1.GetTradeOrderRequest) (*settlementv1.GetTradeOrderResponse, error)
	ListOutstandingTradeOrders(context.Context, *settlementv1.ListOutstandingTradeOrdersRequest) (*settlementv1.ListOutstandingTradeOrdersResponse, error)
	GetTransactionState(context.Context, *settlementv1.GetTransactionStateRequest) (*settlementv1.GetTransactionStateResponse, error)
}

// SettlementClient adapts the generated connect-go settlement client to the
// Settlement interface used by market service code. It stores only the generated
// client and forwards each call through connect.NewRequest. It exists so the
// rest of market can speak in settlement proto messages without knowing connect
// transport details.
type SettlementClient struct {
	client settlementv1connect.TradeSettlementServiceClient
}

// NewSettlementClient builds a gRPC-compatible connect-go client for settlement.
// It uses http.DefaultClient and connect.WithGRPC so the Go market service can
// talk to the Rust tonic settlement service over standard gRPC. It exists as the
// only production constructor for the settlement dependency.
func NewSettlementClient(url string) SettlementClient {
	return SettlementClient{
		client: settlementv1connect.NewTradeSettlementServiceClient(
			http.DefaultClient,
			url,
			connect.WithGRPC(),
		),
	}
}

// OpenTradeOrder forwards a validated market order-opening command to
// settlement. It wraps the protobuf request in a connect request, waits for the
// generated client response, and returns the settlement proto result. It exists
// because settlement, not market, owns durable order creation and reservations.
func (s SettlementClient) OpenTradeOrder(ctx context.Context, request *settlementv1.OpenTradeOrderRequest) (*settlementv1.OpenTradeOrderResponse, error) {
	response, err := s.client.OpenTradeOrder(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, err
	}

	return response.Msg, nil
}

// CloseTradeOrder forwards a market-approved order close command to settlement.
// It sends the requested state change and returns settlement's durable result.
// It exists because market decides that cancellation or expiration is allowed,
// while settlement performs the safe state/reservation update.
func (s SettlementClient) CloseTradeOrder(ctx context.Context, request *settlementv1.CloseTradeOrderRequest) (*settlementv1.CloseTradeOrderResponse, error) {
	response, err := s.client.CloseTradeOrder(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, err
	}

	return response.Msg, nil
}

// RequestSettlement forwards an accepted fill to settlement for atomic transfer.
// It sends the full buyer/seller/item/money terms and returns the durable
// settlement result. It exists because wallet and item ownership must move in one
// correctness-critical operation outside the market service.
func (s SettlementClient) RequestSettlement(ctx context.Context, request *settlementv1.RequestSettlementRequest) (*settlementv1.RequestSettlementResponse, error) {
	response, err := s.client.RequestSettlement(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, err
	}

	return response.Msg, nil
}

// ClaimResult forwards a claim request to settlement. It sends the claim command
// directly and returns the durable claim result. It exists so market can expose
// the market proto's ClaimResult RPC without duplicating claim-state logic.
func (s SettlementClient) ClaimResult(ctx context.Context, request *settlementv1.ClaimResultRequest) (*settlementv1.ClaimResultResponse, error) {
	response, err := s.client.ClaimResult(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, err
	}

	return response.Msg, nil
}

// GetTradeOrder reads the current durable order from settlement. It forwards the
// read request without local caching and returns the settlement view. It exists
// because market should not maintain a second source of truth for order state.
func (s SettlementClient) GetTradeOrder(ctx context.Context, request *settlementv1.GetTradeOrderRequest) (*settlementv1.GetTradeOrderResponse, error) {
	response, err := s.client.GetTradeOrder(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, err
	}

	return response.Msg, nil
}

// ListOutstandingTradeOrders reads outstanding order views from settlement. It
// forwards the filter and pagination fields to settlement and returns its result.
// It exists so list behavior follows the durable state rather than an in-memory
// market cache.
func (s SettlementClient) ListOutstandingTradeOrders(ctx context.Context, request *settlementv1.ListOutstandingTradeOrdersRequest) (*settlementv1.ListOutstandingTradeOrdersResponse, error) {
	response, err := s.client.ListOutstandingTradeOrders(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, err
	}

	return response.Msg, nil
}

// GetTransactionState reads durable transaction and settlement state from
// settlement. It forwards the transaction ID and returns the settlement-owned
// projection. It exists so market exposes transaction status without inspecting
// settlement storage internals.
func (s SettlementClient) GetTransactionState(ctx context.Context, request *settlementv1.GetTransactionStateRequest) (*settlementv1.GetTransactionStateResponse, error) {
	response, err := s.client.GetTransactionState(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, err
	}

	return response.Msg, nil
}
