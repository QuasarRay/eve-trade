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
	"google.golang.org/protobuf/types/known/timestamppb"
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
	err    error
}

func (r fakeReplayRepository) LoadCompletedIdempotencyReplay(context.Context, string) (*IdempotencyReplay, error) {
	if r.err != nil {
		return nil, r.err
	}
	return r.replay, nil
}

type fakeInactiveIssueRepository struct {
	fakeTradeRepository
}

func (fakeInactiveIssueRepository) LoadItemStack(context.Context, string) (ItemStackSnapshot, error) {
	return ItemStackSnapshot{
		ItemStackID: "11111111-1111-4111-8111-111111111111",
		OwnerID:     1001,
		ItemTypeID:  34,
		StationID:   60003760,
		Quantity:    10,
		StackState:  "MERGED",
	}, nil
}

type fakeAcceptRepository struct {
	fakeTradeRepository
	destinationState string
}

func (r fakeAcceptRepository) LoadTrade(context.Context, string) (TradeSnapshot, error) {
	return TradeSnapshot{
		TradeInstanceID:   "22222222-2222-4222-8222-222222222222",
		TradeState:        "OPEN",
		IssuerID:          1001,
		ItemTypeID:        34,
		StationID:         60003760,
		RemainingQuantity: 4,
		UnitPriceISK:      25,
		ItemStackEscrowID: "44444444-4444-4444-8444-444444444444",
		EscrowQuantity:    4,
		SourceItemStackID: "11111111-1111-4111-8111-111111111111",
		TotalQuantity:     4,
	}, nil
}

func (r fakeAcceptRepository) LoadWallet(context.Context, string) (WalletSnapshot, error) {
	return WalletSnapshot{
		WalletID:    "33333333-3333-4333-8333-333333333333",
		CapsuleerID: 2002,
		ISKAmount:   1000,
		WalletState: "ACTIVE",
	}, nil
}

func (r fakeAcceptRepository) LoadPrimaryWallet(context.Context, int64) (WalletSnapshot, error) {
	return WalletSnapshot{
		WalletID:    "55555555-5555-4555-8555-555555555555",
		CapsuleerID: 1001,
		ISKAmount:   100,
		WalletState: "ACTIVE",
	}, nil
}

func (r fakeAcceptRepository) LoadItemStack(context.Context, string) (ItemStackSnapshot, error) {
	state := r.destinationState
	if state == "" {
		state = "ACTIVE"
	}
	return ItemStackSnapshot{
		ItemStackID: "66666666-6666-4666-8666-666666666666",
		OwnerID:     2002,
		ItemTypeID:  34,
		StationID:   60003760,
		Quantity:    1,
		StackState:  state,
	}, nil
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

func TestMarketHandlerRejectsReplayWithDifferentExpiresAt(t *testing.T) {
	originalExpiresAt := time.Now().Add(time.Hour).UTC()
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
							"expires_at":     originalExpiresAt.Format(time.RFC3339Nano),
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
		ExternalRequestId:   "external-original",
		IssuedByCapsuleerId: 1001,
		ItemStack:           &marketv1.ItemStackRow{ItemStackId: "11111111-1111-4111-8111-111111111111"},
		Quantity:            4,
		UnitPriceIsk:        25,
		ExpiresAt:           timestamppb.New(originalExpiresAt.Add(time.Hour)),
	}))
	if connect.CodeOf(err) != connect.CodeAborted {
		t.Fatalf("error code = %v, want aborted: %v", connect.CodeOf(err), err)
	}
}

func TestMarketHandlerRejectsReplayWithDifferentRequestFingerprint(t *testing.T) {
	original := &marketv1.IssueTradeInstanceRequest{
		IdempotencyKey:      "issue-replay",
		ExternalRequestId:   "external-original",
		IssuedByCapsuleerId: 1001,
		ItemStack: &marketv1.ItemStackRow{
			ItemStackId: "11111111-1111-4111-8111-111111111111",
			OwnerId:     1001,
		},
		Quantity:     4,
		UnitPriceIsk: 25,
	}
	fingerprint, err := marketRequestFingerprint("issue_trade_instance", original)
	if err != nil {
		t.Fatalf("marketRequestFingerprint returned error: %v", err)
	}

	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeReplayRepository{
		replay: &IdempotencyReplay{
			SettlementBatchID:  "settlement-batch",
			RequestFingerprint: fingerprint,
			ExternalRequestID:  "external-original",
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

	_, err = handler.IssueTradeInstance(context.Background(), connect.NewRequest(&marketv1.IssueTradeInstanceRequest{
		IdempotencyKey:      "issue-replay",
		ExternalRequestId:   "external-original",
		IssuedByCapsuleerId: 1001,
		ItemStack: &marketv1.ItemStackRow{
			ItemStackId: "11111111-1111-4111-8111-111111111111",
			OwnerId:     3003,
		},
		Quantity:     4,
		UnitPriceIsk: 25,
	}))
	if connect.CodeOf(err) != connect.CodeAborted {
		t.Fatalf("error code = %v, want aborted: %v", connect.CodeOf(err), err)
	}
}

func TestMarketHandlerRejectsReplayWhenDestinationModeChanged(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeReplayRepository{
		replay: &IdempotencyReplay{
			SettlementBatchID:   "settlement-batch",
			ExternalRequestID:   "external-original",
			CausedByCapsuleerID: 2002,
			Steps: []ReplayStep{
				{
					StepKind: "transfer_isk_amount_from_wallet_to_wallet_escrow",
					Payload: map[string]AnyJSON{
						"payload": map[string]AnyJSON{
							"source_wallet_id":  "33333333-3333-4333-8333-333333333333",
							"wallet_escrow_id":  "77777777-7777-4777-8777-777777777777",
							"trade_instance_id": "22222222-2222-4222-8222-222222222222",
						},
					},
				},
				{
					StepKind: "transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner",
					Payload: map[string]AnyJSON{
						"payload": map[string]AnyJSON{
							"destination_item_stack_id": "66666666-6666-4666-8666-666666666666",
							"quantity":                  float64(1),
						},
					},
				},
			},
		},
	})

	_, err := handler.AcceptTradeInstance(context.Background(), connect.NewRequest(&marketv1.AcceptTradeInstanceRequest{
		IdempotencyKey:    "accept-replay",
		ExternalRequestId: "external-original",
		TradeInstanceId:   "22222222-2222-4222-8222-222222222222",
		BuyerCapsuleerId:  2002,
		QuantityRequested: 1,
		BuyerWalletId:     "33333333-3333-4333-8333-333333333333",
	}))
	if connect.CodeOf(err) != connect.CodeAborted {
		t.Fatalf("error code = %v, want aborted: %v", connect.CodeOf(err), err)
	}
}

func TestMarketHandlerReportsReplayLoadErrorUnavailable(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeReplayRepository{err: errors.New("postgres unavailable")})

	_, err := handler.IssueTradeInstance(context.Background(), connect.NewRequest(&marketv1.IssueTradeInstanceRequest{
		IdempotencyKey: "issue-replay-load-error",
		ItemStack:      &marketv1.ItemStackRow{ItemStackId: "11111111-1111-4111-8111-111111111111"},
	}))
	if connect.CodeOf(err) != connect.CodeUnavailable {
		t.Fatalf("error code = %v, want unavailable: %v", connect.CodeOf(err), err)
	}
}

func TestMarketHandlerRejectsInactiveIssueItemStack(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeInactiveIssueRepository{})

	_, err := handler.IssueTradeInstance(context.Background(), connect.NewRequest(&marketv1.IssueTradeInstanceRequest{
		IdempotencyKey:      "issue-inactive",
		IssuedByCapsuleerId: 1001,
		ItemStack:           &marketv1.ItemStackRow{ItemStackId: "11111111-1111-4111-8111-111111111111"},
		Quantity:            4,
		UnitPriceIsk:        25,
	}))
	if connect.CodeOf(err) != connect.CodeFailedPrecondition {
		t.Fatalf("error code = %v, want failed_precondition: %v", connect.CodeOf(err), err)
	}
}

func TestMarketHandlerRejectsInactiveDestinationItemStack(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeAcceptRepository{destinationState: "MERGED"})

	_, err := handler.AcceptTradeInstance(context.Background(), connect.NewRequest(&marketv1.AcceptTradeInstanceRequest{
		IdempotencyKey:              "accept-inactive-destination",
		ExternalRequestId:           "external-accept-inactive-destination",
		TradeInstanceId:             "22222222-2222-4222-8222-222222222222",
		BuyerCapsuleerId:            2002,
		QuantityRequested:           1,
		BuyerWalletId:               "33333333-3333-4333-8333-333333333333",
		BuyerDestinationItemStackId: "66666666-6666-4666-8666-666666666666",
	}))
	if connect.CodeOf(err) != connect.CodeFailedPrecondition {
		t.Fatalf("error code = %v, want failed_precondition: %v", connect.CodeOf(err), err)
	}
}
