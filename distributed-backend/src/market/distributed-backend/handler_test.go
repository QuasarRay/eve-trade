package distributedbackend

import (
	"context"
	"errors"
	"testing"

	"connectrpc.com/connect"
	marketv1 "github.com/astral/eve-trade/market/distributed-backend/gen/market/v1"
	tradesettlementv1 "github.com/astral/eve-trade/market/distributed-backend/gen/trade_settlement/v1"
)

type fakeSettlementExecutor struct {
	err error
}

func (f fakeSettlementExecutor) ExecuteSettlementBatch(context.Context, *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	if f.err != nil {
		return nil, f.err
	}
	return &tradesettlementv1.ExecuteSettlementBatchResponse{SettlementBatchId: "settlement-batch"}, nil
}

func TestMarketHandlerReportsTradeSettlementUnavailable(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{err: errors.New("connection refused")})

	_, err := handler.IssueTradeInstance(context.Background(), connect.NewRequest(&marketv1.IssueTradeInstanceRequest{
		IdempotencyKey:      "issue-key",
		IssuedByCapsuleerId: 1001,
		ItemStack:           &marketv1.ItemStackRow{ItemStackId: "11111111-1111-4111-8111-111111111111", OwnerId: 1001, ItemTypeId: 34, StationId: 60003760, Quantity: 10},
		Quantity:            4,
		UnitPriceIsk:        25,
	}))
	if connect.CodeOf(err) != connect.CodeUnavailable {
		t.Fatalf("error code = %v, want unavailable: %v", connect.CodeOf(err), err)
	}
}
