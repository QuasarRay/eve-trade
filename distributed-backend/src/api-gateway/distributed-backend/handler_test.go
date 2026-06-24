package distributedbackend

import (
	"context"
	"errors"
	"testing"

	"connectrpc.com/connect"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
)

type fakeMarketClient struct {
	issue  *marketv1.IssueTradeInstanceRequest
	accept *marketv1.AcceptTradeInstanceRequest
	cancel *marketv1.CancelTradeInstanceRequest
	gui    *marketv1.SubmitTradeGuiInteractionRequest
	err    error
}

func (f *fakeMarketClient) IssueTradeInstance(_ context.Context, request *marketv1.IssueTradeInstanceRequest) (*marketv1.IssueTradeInstanceResponse, error) {
	f.issue = request
	if f.err != nil {
		return nil, f.err
	}
	return &marketv1.IssueTradeInstanceResponse{SettlementBatchId: "issue-batch"}, nil
}

func (f *fakeMarketClient) AcceptTradeInstance(_ context.Context, request *marketv1.AcceptTradeInstanceRequest) (*marketv1.AcceptTradeInstanceResponse, error) {
	f.accept = request
	if f.err != nil {
		return nil, f.err
	}
	return &marketv1.AcceptTradeInstanceResponse{SettlementBatchId: "accept-batch"}, nil
}

func (f *fakeMarketClient) CancelTradeInstance(_ context.Context, request *marketv1.CancelTradeInstanceRequest) (*marketv1.CancelTradeInstanceResponse, error) {
	f.cancel = request
	if f.err != nil {
		return nil, f.err
	}
	return &marketv1.CancelTradeInstanceResponse{SettlementBatchId: "cancel-batch"}, nil
}

func (f *fakeMarketClient) SubmitTradeGuiInteraction(_ context.Context, request *marketv1.SubmitTradeGuiInteractionRequest) (*marketv1.SubmitTradeGuiInteractionResponse, error) {
	f.gui = request
	if f.err != nil {
		return nil, f.err
	}
	return &marketv1.SubmitTradeGuiInteractionResponse{InteractionId: "interaction-1", MappedOperation: "IssueTradeInstance"}, nil
}

func TestGatewayHandlerForwardsIssueTradeInstance(t *testing.T) {
	market := &fakeMarketClient{}
	handler := NewGatewayHandler(market)

	response, err := handler.IssueTradeInstance(context.Background(), connect.NewRequest(&marketv1.IssueTradeInstanceRequest{
		IdempotencyKey:      "issue-key",
		IssuedByCapsuleerId: 1001,
		Quantity:            5,
		UnitPriceIsk:        20,
		ItemStack:           &marketv1.ItemStackRow{ItemStackId: "stack-1", OwnerId: 1001},
	}))
	if err != nil {
		t.Fatalf("IssueTradeInstance returned error: %v", err)
	}
	if market.issue == nil || market.issue.IdempotencyKey != "issue-key" {
		t.Fatalf("market issue request was not forwarded")
	}
	if response.Msg.SettlementBatchId != "issue-batch" {
		t.Fatalf("settlement batch id = %q, want issue-batch", response.Msg.SettlementBatchId)
	}
}

func TestGatewayHandlerForwardsAcceptTradeInstance(t *testing.T) {
	market := &fakeMarketClient{}
	handler := NewGatewayHandler(market)

	response, err := handler.AcceptTradeInstance(context.Background(), connect.NewRequest(&marketv1.AcceptTradeInstanceRequest{
		IdempotencyKey:    "accept-key",
		BuyerCapsuleerId:  2002,
		QuantityRequested: 3,
	}))
	if err != nil {
		t.Fatalf("AcceptTradeInstance returned error: %v", err)
	}
	if market.accept == nil || market.accept.BuyerCapsuleerId != 2002 {
		t.Fatalf("market accept request was not forwarded")
	}
	if response.Msg.SettlementBatchId != "accept-batch" {
		t.Fatalf("settlement batch id = %q, want accept-batch", response.Msg.SettlementBatchId)
	}
}

func TestGatewayHandlerForwardsCancelTradeInstance(t *testing.T) {
	market := &fakeMarketClient{}
	handler := NewGatewayHandler(market)

	response, err := handler.CancelTradeInstance(context.Background(), connect.NewRequest(&marketv1.CancelTradeInstanceRequest{
		IdempotencyKey:         "cancel-key",
		TradeInstanceId:        "trade-1",
		CancelledByCapsuleerId: 1001,
	}))
	if err != nil {
		t.Fatalf("CancelTradeInstance returned error: %v", err)
	}
	if market.cancel == nil || market.cancel.TradeInstanceId != "trade-1" {
		t.Fatalf("market cancel request was not forwarded")
	}
	if response.Msg.SettlementBatchId != "cancel-batch" {
		t.Fatalf("settlement batch id = %q, want cancel-batch", response.Msg.SettlementBatchId)
	}
}

func TestGatewayHandlerForwardsTradeGuiInteraction(t *testing.T) {
	market := &fakeMarketClient{}
	handler := NewGatewayHandler(market)
	raw := []byte(`{"schema_version":"eve-trade-gui.v1"}`)

	response, err := handler.SubmitTradeGuiInteraction(context.Background(), connect.NewRequest(&marketv1.SubmitTradeGuiInteractionRequest{
		SourceTransport: "quilkin_udp",
		SourceAddress:   "127.0.0.1:26001",
		RawPayload:      raw,
	}))
	if err != nil {
		t.Fatalf("SubmitTradeGuiInteraction returned error: %v", err)
	}
	if market.gui == nil || string(market.gui.RawPayload) != string(raw) {
		t.Fatalf("market GUI request was not forwarded unchanged")
	}
	if response.Msg.InteractionId != "interaction-1" {
		t.Fatalf("interaction id = %q, want interaction-1", response.Msg.InteractionId)
	}
}

func TestGatewayHandlerReportsMarketUnavailable(t *testing.T) {
	handler := NewGatewayHandler(&fakeMarketClient{err: errors.New("connection refused")})

	_, err := handler.CancelTradeInstance(context.Background(), connect.NewRequest(&marketv1.CancelTradeInstanceRequest{
		IdempotencyKey:         "cancel-key",
		TradeInstanceId:        "trade-1",
		CancelledByCapsuleerId: 1001,
	}))
	if connect.CodeOf(err) != connect.CodeUnavailable {
		t.Fatalf("error code = %v, want unavailable: %v", connect.CodeOf(err), err)
	}
}
