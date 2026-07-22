package market

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/settlementrpc"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
)

type fakeSettlementExecutor struct {
	err error
}

func (f fakeSettlementExecutor) PublishSettlementWork(context.Context, *settlement.Work) (*SettlementPublication, error) {
	if f.err != nil {
		return nil, f.err
	}
	return &SettlementPublication{MessageID: "settlement-message", OperationID: "operation-1", QueuedAt: time.Unix(100, 0)}, nil
}

type recordingSettlementExecutor struct {
	mu       sync.Mutex
	requests []*settlement.Work
}

func (r *recordingSettlementExecutor) PublishSettlementWork(_ context.Context, request *settlement.Work) (*SettlementPublication, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.requests = append(r.requests, request)
	return &SettlementPublication{MessageID: "settlement-message", OperationID: "operation-1", QueuedAt: time.Unix(100, 0)}, nil
}

func (r *recordingSettlementExecutor) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.requests)
}

func (r *recordingSettlementExecutor) lastRequest(t *testing.T) *settlement.Work {
	t.Helper()
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.requests) == 0 {
		t.Fatalf("no settlement request recorded")
	}
	return r.requests[len(r.requests)-1]
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

func mustMarketRequestFingerprint(t *testing.T, requestKind string, message any) string {
	t.Helper()
	fingerprint, err := marketRequestFingerprint(requestKind, message)
	if err != nil {
		t.Fatalf("marketRequestFingerprint returned error: %v", err)
	}
	return fingerprint
}

func validIssueItemStackInput() *tradeGUIItemStackInput {
	return &tradeGUIItemStackInput{
		ItemStackID: "11111111-1111-4111-8111-111111111111",
		OwnerID:     1001,
		ItemTypeID:  34,
		StationID:   60003760,
		Quantity:    10,
	}
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

func TestSubmitTradeGuiInteractionDefaultsIdempotencyFromInteractionID(t *testing.T) {
	settlement := &recordingSettlementExecutor{}
	handler := NewMarketHandler(settlement, fakeTradeRepository{})
	rawPayload := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"gui-issue-1","ui":{"window":"regional_market","action":"market_place_sell_order"},"input":{"issued_by_capsuleer_id":1001,"item_stack":{"item_stack_id":"11111111-1111-4111-8111-111111111111","owner_id":1001,"item_type_id":34,"station_id":60003760,"quantity":10},"quantity":4,"unit_price_isk":25}}`)

	response, err := handler.SubmitTradeGuiInteraction(context.Background(), &SubmitTradeGuiInteractionRequest{
		RawPayload: rawPayload,
	})
	if err != nil {
		t.Fatalf("SubmitTradeGuiInteraction returned error: %v", err)
	}
	if response.InteractionID != "gui-issue-1" {
		t.Fatalf("interaction_id = %q, want gui-issue-1", response.InteractionID)
	}
	if response.Status != "queued" {
		t.Fatalf("status = %q, want queued", response.Status)
	}
	request := settlement.lastRequest(t)
	if request.IdempotencyKey != "gui-issue-1" {
		t.Fatalf("settlement idempotency key = %q, want gui-issue-1", request.IdempotencyKey)
	}
	if request.ExternalRequestID != "gui-issue-1" {
		t.Fatalf("settlement external request ID = %q, want gui-issue-1", request.ExternalRequestID)
	}
}

func TestSubmitTradeGuiInteractionReplaysDuplicateInteractionWithoutSecondSettlement(t *testing.T) {
	settlement := &recordingSettlementExecutor{}
	repo := &fakeReplayRepository{}
	handler := NewMarketHandler(settlement, repo)
	rawPayload := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"gui-replay-1","ui":{"window":"regional_market","action":"market_place_sell_order"},"input":{"issued_by_capsuleer_id":1001,"item_stack":{"item_stack_id":"11111111-1111-4111-8111-111111111111","owner_id":1001,"item_type_id":34,"station_id":60003760,"quantity":10},"quantity":4,"unit_price_isk":25}}`)

	first, err := handler.SubmitTradeGuiInteraction(context.Background(), &SubmitTradeGuiInteractionRequest{
		RawPayload: rawPayload,
	})
	if err != nil {
		t.Fatalf("first SubmitTradeGuiInteraction returned error: %v", err)
	}
	if settlement.count() != 1 {
		t.Fatalf("settlement calls after first submit = %d, want 1", settlement.count())
	}

	request := issueTradeInstanceRequest{
		IdempotencyKey:      "gui-replay-1",
		ExternalRequestID:   "gui-replay-1",
		IssuedByCapsuleerID: 1001,
		ItemStack: &tradeGUIItemStackInput{
			ItemStackID: "11111111-1111-4111-8111-111111111111",
			OwnerID:     1001,
			ItemTypeID:  34,
			StationID:   60003760,
			Quantity:    10,
		},
		Quantity:     4,
		UnitPriceISK: 25,
	}
	fingerprint, err := marketRequestFingerprint("issue_trade_instance", request)
	if err != nil {
		t.Fatalf("marketRequestFingerprint returned error: %v", err)
	}
	repo.replay = &IdempotencyReplay{
		SettlementBatchID:  "settlement-batch",
		RequestFingerprint: fingerprint,
		ExternalRequestID:  "gui-replay-1",
		Steps: []ReplayStep{
			{
				StepKind: "create_new_trade_instance_row",
				Payload: map[string]AnyJSON{
					"payload": map[string]AnyJSON{
						"trade_instance_id": first.TradeInstanceID,
						"issuer_id":         float64(1001),
						"item_type_id":      float64(34),
						"station_id":        float64(60003760),
						"total_quantity":    float64(4),
						"unit_price_isk":    float64(25),
					},
				},
			},
			{
				StepKind: "transfer_quantity_from_item_stack_to_item_stack_escrow",
				Payload: map[string]AnyJSON{
					"payload": map[string]AnyJSON{
						"source_item_stack_id": "11111111-1111-4111-8111-111111111111",
						"item_stack_escrow_id": first.ItemStackEscrowID,
					},
				},
			},
		},
	}

	second, err := handler.SubmitTradeGuiInteraction(context.Background(), &SubmitTradeGuiInteractionRequest{
		RawPayload: rawPayload,
	})
	if err != nil {
		t.Fatalf("second SubmitTradeGuiInteraction returned error: %v", err)
	}
	if settlement.count() != 1 {
		t.Fatalf("settlement calls after replay = %d, want 1", settlement.count())
	}
	if second.TradeInstanceID != first.TradeInstanceID {
		t.Fatalf("replayed trade instance ID = %q, want %q", second.TradeInstanceID, first.TradeInstanceID)
	}
	if second.SettlementBatchID != "settlement-batch" {
		t.Fatalf("replayed settlement batch ID = %q, want settlement-batch", second.SettlementBatchID)
	}
}

func TestMarketHandlerReportsTradeSettlementUnavailable(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{err: errors.New("connection refused")}, fakeTradeRepository{})

	_, err := handler.issueTradeInstance(context.Background(), issueTradeInstanceRequest{
		IdempotencyKey:      "issue-key",
		IssuedByCapsuleerID: 1001,
		ItemStack:           &tradeGUIItemStackInput{ItemStackID: "11111111-1111-4111-8111-111111111111", OwnerID: 1001, ItemTypeID: 34, StationID: 60003760, Quantity: 10},
		Quantity:            4,
		UnitPriceISK:        25,
	})
	if apiErrorCode(err) != errs.Unavailable {
		t.Fatalf("error code = %v, want unavailable: %v", apiErrorCode(err), err)
	}
}

func TestMarketHandlerRejectsAcceptingExpiredTrade(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeExpiredTradeRepository{})

	_, err := handler.acceptTradeInstance(context.Background(), acceptTradeInstanceRequest{
		IdempotencyKey:    "accept-expired",
		ExternalRequestID: "external-accept-expired",
		TradeInstanceID:   "22222222-2222-4222-8222-222222222222",
		BuyerCapsuleerID:  2002,
		QuantityRequested: 1,
		BuyerWalletID:     "33333333-3333-4333-8333-333333333333",
	})
	if apiErrorCode(err) != errs.FailedPrecondition {
		t.Fatalf("error code = %v, want failed_precondition: %v", apiErrorCode(err), err)
	}
	if !strings.Contains(err.Error(), "expired") {
		t.Fatalf("error = %v, want expired", err)
	}
}

func TestMarketHandlerRejectsReplayWithDifferentExternalRequestID(t *testing.T) {
	original := issueTradeInstanceRequest{
		IdempotencyKey:      "issue-replay",
		ExternalRequestID:   "external-original",
		IssuedByCapsuleerID: 1001,
		ItemStack:           validIssueItemStackInput(),
		Quantity:            4,
		UnitPriceISK:        25,
	}
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeReplayRepository{
		replay: &IdempotencyReplay{
			SettlementBatchID:  "settlement-batch",
			RequestFingerprint: mustMarketRequestFingerprint(t, "issue_trade_instance", original),
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

	_, err := handler.issueTradeInstance(context.Background(), issueTradeInstanceRequest{
		IdempotencyKey:      "issue-replay",
		ExternalRequestID:   "external-different",
		IssuedByCapsuleerID: 1001,
		ItemStack:           validIssueItemStackInput(),
		Quantity:            4,
		UnitPriceISK:        25,
	})
	if apiErrorCode(err) != errs.Aborted {
		t.Fatalf("error code = %v, want aborted: %v", apiErrorCode(err), err)
	}
	if !strings.Contains(err.Error(), "different request fingerprint") {
		t.Fatalf("error = %v, want fingerprint conflict", err)
	}
}

func TestMarketHandlerRejectsReplayWithDifferentExpiresAt(t *testing.T) {
	originalExpiresAt := time.Now().Add(time.Hour).UTC()
	original := issueTradeInstanceRequest{
		IdempotencyKey:      "issue-replay",
		ExternalRequestID:   "external-original",
		IssuedByCapsuleerID: 1001,
		ItemStack:           validIssueItemStackInput(),
		Quantity:            4,
		UnitPriceISK:        25,
		ExpiresAt:           settlementrpc.Timestamp(originalExpiresAt),
	}
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeReplayRepository{
		replay: &IdempotencyReplay{
			SettlementBatchID:  "settlement-batch",
			RequestFingerprint: mustMarketRequestFingerprint(t, "issue_trade_instance", original),
			ExternalRequestID:  "external-original",
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

	_, err := handler.issueTradeInstance(context.Background(), issueTradeInstanceRequest{
		IdempotencyKey:      "issue-replay",
		ExternalRequestID:   "external-original",
		IssuedByCapsuleerID: 1001,
		ItemStack:           validIssueItemStackInput(),
		Quantity:            4,
		UnitPriceISK:        25,
		ExpiresAt:           settlementrpc.Timestamp(originalExpiresAt.Add(time.Hour)),
	})
	if apiErrorCode(err) != errs.Aborted {
		t.Fatalf("error code = %v, want aborted: %v", apiErrorCode(err), err)
	}
}

func TestMarketHandlerRejectsReplayWithDifferentRequestFingerprint(t *testing.T) {
	original := issueTradeInstanceRequest{
		IdempotencyKey:      "issue-replay",
		ExternalRequestID:   "external-original",
		IssuedByCapsuleerID: 1001,
		ItemStack:           validIssueItemStackInput(),
		Quantity:            4,
		UnitPriceISK:        25,
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

	_, err = handler.issueTradeInstance(context.Background(), issueTradeInstanceRequest{
		IdempotencyKey:      "issue-replay",
		ExternalRequestID:   "external-original",
		IssuedByCapsuleerID: 1001,
		ItemStack: &tradeGUIItemStackInput{
			ItemStackID: "11111111-1111-4111-8111-111111111111",
			OwnerID:     1001,
			ItemTypeID:  34,
			StationID:   60003760,
			Quantity:    9,
		},
		Quantity:     4,
		UnitPriceISK: 25,
	})
	if apiErrorCode(err) != errs.Aborted {
		t.Fatalf("error code = %v, want aborted: %v", apiErrorCode(err), err)
	}
}

func TestReplayRequestFingerprintRejectsSettlementDomainFingerprint(t *testing.T) {
	message := issueTradeInstanceRequest{
		IdempotencyKey:      "issue-server-fingerprint-replay",
		ExternalRequestID:   "external-original",
		IssuedByCapsuleerID: 1001,
		ItemStack:           &tradeGUIItemStackInput{ItemStackID: "11111111-1111-4111-8111-111111111111"},
		Quantity:            4,
		UnitPriceISK:        25,
	}
	replay := &IdempotencyReplay{
		RequestFingerprint: "trade-settlement.execute_settlement_batch.v1.sha256:" + strings.Repeat("a", 64),
	}

	ok, err := replayRequestFingerprintMatches(replay, "issue_trade_instance", message)
	if err != nil {
		t.Fatalf("replayRequestFingerprintMatches returned error: %v", err)
	}
	if ok {
		t.Fatal("settlement-domain fingerprint matched a Market request")
	}
}

func TestDecodeReplayStepPayloadPreservesInt64Boundaries(t *testing.T) {
	for _, value := range []int64{1<<53 - 1, 1 << 53, 1<<53 + 1, 9223372036854775807} {
		t.Run(fmt.Sprintf("%d", value), func(t *testing.T) {
			payload, err := decodeReplayStepPayload(fmt.Appendf(nil, `{"payload":{"value":%d}}`, value))
			if err != nil {
				t.Fatalf("decodeReplayStepPayload returned error: %v", err)
			}
			nested, ok := payload["payload"].(map[string]AnyJSON)
			if !ok {
				t.Fatalf("decoded payload = %#v", payload)
			}
			if got := int64Field(nested, "value"); got != value {
				t.Fatalf("decoded value = %d, want %d", got, value)
			}
		})
	}
}

func TestMarketHandlerRejectsReplayWhenDestinationModeChanged(t *testing.T) {
	original := acceptTradeInstanceRequest{
		IdempotencyKey:              "accept-replay",
		ExternalRequestID:           "external-original",
		TradeInstanceID:             "22222222-2222-4222-8222-222222222222",
		BuyerCapsuleerID:            2002,
		QuantityRequested:           1,
		BuyerWalletID:               "33333333-3333-4333-8333-333333333333",
		BuyerDestinationItemStackID: "66666666-6666-4666-8666-666666666666",
	}
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeReplayRepository{
		replay: &IdempotencyReplay{
			SettlementBatchID:   "settlement-batch",
			RequestFingerprint:  mustMarketRequestFingerprint(t, "accept_trade_instance", original),
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

	_, err := handler.acceptTradeInstance(context.Background(), acceptTradeInstanceRequest{
		IdempotencyKey:    "accept-replay",
		ExternalRequestID: "external-original",
		TradeInstanceID:   "22222222-2222-4222-8222-222222222222",
		BuyerCapsuleerID:  2002,
		QuantityRequested: 1,
		BuyerWalletID:     "33333333-3333-4333-8333-333333333333",
	})
	if apiErrorCode(err) != errs.Aborted {
		t.Fatalf("error code = %v, want aborted: %v", apiErrorCode(err), err)
	}
}

func TestMarketHandlerReportsReplayLoadErrorUnavailable(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeReplayRepository{err: errors.New("postgres unavailable")})

	_, err := handler.issueTradeInstance(context.Background(), issueTradeInstanceRequest{
		IdempotencyKey:      "issue-replay-load-error",
		ItemStack:           validIssueItemStackInput(),
		IssuedByCapsuleerID: 1001,
		Quantity:            4,
		UnitPriceISK:        25,
	})
	if apiErrorCode(err) != errs.Unavailable {
		t.Fatalf("error code = %v, want unavailable: %v", apiErrorCode(err), err)
	}
}

func TestMarketHandlerRejectsInactiveIssueItemStack(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeInactiveIssueRepository{})

	_, err := handler.issueTradeInstance(context.Background(), issueTradeInstanceRequest{
		IdempotencyKey:      "issue-inactive",
		IssuedByCapsuleerID: 1001,
		ItemStack:           validIssueItemStackInput(),
		Quantity:            4,
		UnitPriceISK:        25,
	})
	if apiErrorCode(err) != errs.FailedPrecondition {
		t.Fatalf("error code = %v, want failed_precondition: %v", apiErrorCode(err), err)
	}
}

func TestMarketHandlerRejectsInactiveDestinationItemStack(t *testing.T) {
	handler := NewMarketHandler(fakeSettlementExecutor{}, fakeAcceptRepository{destinationState: "MERGED"})

	_, err := handler.acceptTradeInstance(context.Background(), acceptTradeInstanceRequest{
		IdempotencyKey:              "accept-inactive-destination",
		ExternalRequestID:           "external-accept-inactive-destination",
		TradeInstanceID:             "22222222-2222-4222-8222-222222222222",
		BuyerCapsuleerID:            2002,
		QuantityRequested:           1,
		BuyerWalletID:               "33333333-3333-4333-8333-333333333333",
		BuyerDestinationItemStackID: "66666666-6666-4666-8666-666666666666",
	})
	if apiErrorCode(err) != errs.FailedPrecondition {
		t.Fatalf("error code = %v, want failed_precondition: %v", apiErrorCode(err), err)
	}
}
