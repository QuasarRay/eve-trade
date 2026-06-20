package distributedbackend

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"connectrpc.com/connect"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
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

type fakeTradeRepository struct{}

func (fakeTradeRepository) LoadItemStack(context.Context, string) (ItemStackSnapshot, error) {
	return ItemStackSnapshot{
		ItemStackID: "11111111-1111-4111-8111-111111111111",
		OwnerID:     1001,
		ItemTypeID:  34,
		StationID:   60003760,
		Quantity:    10,
		StackState:  "ACTIVE",
	}, nil
}

func (fakeTradeRepository) LoadWallet(context.Context, string) (WalletSnapshot, error) {
	return WalletSnapshot{}, nil
}

func (fakeTradeRepository) LoadPrimaryWallet(context.Context, int64) (WalletSnapshot, error) {
	return WalletSnapshot{}, nil
}

func (fakeTradeRepository) LoadTrade(context.Context, string) (TradeSnapshot, error) {
	return TradeSnapshot{}, nil
}

func (fakeTradeRepository) LoadCompletedIdempotencyReplay(context.Context, string) (*IdempotencyReplay, error) {
	return nil, nil
}

type fakeExpiredTradeRepository struct {
	fakeTradeRepository
}

func (fakeExpiredTradeRepository) LoadTrade(context.Context, string) (TradeSnapshot, error) {
	return TradeSnapshot{
		TradeInstanceID: "22222222-2222-4222-8222-222222222222",
		TradeState:      "OPEN",
		IssuerID:        1001,
		ItemTypeID:      34,
		StationID:       60003760,
		UnitPriceISK:    25,
		EscrowQuantity:  4,
		ExpiresAt:       time.Now().Add(-time.Minute),
		ExpiresAtValid:  true,
	}, nil
}

type fakeReplayRepository struct {
	fakeTradeRepository
	replay *IdempotencyReplay
}

func (r fakeReplayRepository) LoadCompletedIdempotencyReplay(context.Context, string) (*IdempotencyReplay, error) {
	return r.replay, nil
}

func TestMarketHandlerReportsTradeSettlementUnavailable(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{err: errors.New("connection refused")}, fakeTradeRepository{})

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

func TestMarketHandlerRejectsAcceptingExpiredTrade(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeExpiredTradeRepository{})

	_, err := handler.AcceptTradeInstance(context.Background(), connect.NewRequest(&marketv1.AcceptTradeInstanceRequest{
		IdempotencyKey:    "accept-expired",
		ExternalRequestId: "external-accept-expired",
		TradeInstanceId:   "22222222-2222-4222-8222-222222222222",
		BuyerCapsuleerId:  2002,
		QuantityRequested: 1,
		BuyerWalletId:     "33333333-3333-4333-8333-333333333333",
	}))
	if connect.CodeOf(err) != connect.CodeFailedPrecondition {
		t.Fatalf("error code = %v, want failed_precondition: %v", connect.CodeOf(err), err)
	}
	if !strings.Contains(err.Error(), "expired") {
		t.Fatalf("error = %v, want expired", err)
	}
}

func TestMarketHandlerRejectsReplayWithDifferentExternalRequestID(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeReplayRepository{
		replay: &IdempotencyReplay{
			SettlementBatchID: "settlement-batch",
			ExternalRequestID: "external-original",
			Steps: []ReplayStep{
				{
					StepKind: "create_new_trade_instance_row",
					Payload: map[string]AnyJSON{
						"payload": map[string]AnyJSON{
							"issuer_id":      float64(1001),
							"total_quantity": float64(4),
							"unit_price_isk": float64(25),
						},
					},
				},
				{
					StepKind: "transfer_quantity_from_item_stack_to_item_stack_escrow",
					Payload: map[string]AnyJSON{
						"payload": map[string]AnyJSON{
							"source_item_stack_id": "11111111-1111-4111-8111-111111111111",
						},
					},
				},
			},
		},
	})

	_, err := handler.IssueTradeInstance(context.Background(), connect.NewRequest(&marketv1.IssueTradeInstanceRequest{
		IdempotencyKey:      "issue-replay",
		ExternalRequestId:   "external-different",
		IssuedByCapsuleerId: 1001,
		ItemStack:           &marketv1.ItemStackRow{ItemStackId: "11111111-1111-4111-8111-111111111111"},
		Quantity:            4,
		UnitPriceIsk:        25,
	}))
	if connect.CodeOf(err) != connect.CodeAborted {
		t.Fatalf("error code = %v, want aborted: %v", connect.CodeOf(err), err)
	}
	if !strings.Contains(err.Error(), "different request fingerprint") {
		t.Fatalf("error = %v, want fingerprint conflict", err)
	}
}
