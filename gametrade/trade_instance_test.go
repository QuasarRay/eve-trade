package gametrade

import (
	"strings"
	"testing"
	"time"

	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	"google.golang.org/protobuf/types/known/timestamppb"
)

func TestIssueTradeInstanceBuildsTradeAndItemEscrowOperations(t *testing.T) {
	plan, err := IssueTradeInstance(IssueTradeInstanceInput{
		IdempotencyKey:      "issue-1",
		ExternalRequestID:   "external-1",
		IssuedByCapsuleerID: 1001,
		ItemStack: ItemStackRow{
			ItemStackID: "11111111-1111-4111-8111-111111111111",
			OwnerID:     1001,
			ItemTypeID:  34,
			StationID:   60003760,
			Quantity:    10,
		},
		Quantity:     4,
		UnitPriceISK: 25,
	})
	if err != nil {
		t.Fatalf("IssueTradeInstance returned error: %v", err)
	}
	if plan.TradeInstanceID == "" || plan.ItemStackEscrowID == "" {
		t.Fatal("expected generated trade and item escrow IDs")
	}
	if len(plan.Operations) != 2 {
		t.Fatalf("expected 2 operations, got %d", len(plan.Operations))
	}
	if plan.Operations[0].Kind != settlement.OperationCreateNewTradeInstanceRow || plan.Operations[0].CreateNewTradeInstanceRow == nil {
		t.Fatalf("operation 0 = %#v, want create trade", plan.Operations[0])
	}
	escrowOp := plan.Operations[1].TransferQuantityFromItemStackToItemStackEscrow
	if plan.Operations[1].Kind != settlement.OperationTransferQuantityFromItemStackToItemStackEscrow || escrowOp == nil {
		t.Fatalf("operation 1 = %#v, want transfer item stack to escrow", plan.Operations[1])
	}
	if escrowOp.Quantity != 4 {
		t.Fatalf("escrow quantity = %d, want 4", escrowOp.Quantity)
	}
}

func TestSettleTradeInstanceCarriesRequestFingerprint(t *testing.T) {
	request, err := SettleTradeInstance(SettlementPlan{
		IdempotencyKey:      "issue-1",
		RequestFingerprint:  "market.issue_trade_instance.sha256:fingerprint",
		ExternalRequestID:   "external-1",
		CausedByCapsuleerID: 1001,
		Operations: []settlement.Operation{
			{
				Kind: settlement.OperationCreateNewTradeInstanceRow,
				CreateNewTradeInstanceRow: &settlement.CreateNewTradeInstanceRow{
					TradeInstanceID: "22222222-2222-4222-8222-222222222222",
					TradeKind:       TradeKindSell,
					TradeState:      TradeStateOpen,
					IssuerID:        1001,
					ItemTypeID:      34,
					StationID:       60003760,
					TotalQuantity:   4,
					UnitPriceISK:    25,
				},
			},
		},
	})
	if err != nil {
		t.Fatalf("SettleTradeInstance returned error: %v", err)
	}
	if request.RequestFingerprint != "market.issue_trade_instance.sha256:fingerprint" {
		t.Fatalf("request fingerprint = %q, want plan fingerprint", request.RequestFingerprint)
	}
}

func TestIssueTradeInstanceRejectsExpiredTrade(t *testing.T) {
	_, err := IssueTradeInstance(IssueTradeInstanceInput{
		IdempotencyKey:      "issue-expired",
		ExternalRequestID:   "external-expired",
		IssuedByCapsuleerID: 1001,
		ItemStack: ItemStackRow{
			ItemStackID: "11111111-1111-4111-8111-111111111111",
			OwnerID:     1001,
			ItemTypeID:  34,
			StationID:   60003760,
			Quantity:    10,
		},
		Quantity:     4,
		UnitPriceISK: 25,
		ExpiresAt:    timestamppb.New(time.Now().Add(-time.Minute)),
	})
	if err == nil {
		t.Fatal("expected expired trade to be rejected")
	}
	if !strings.Contains(err.Error(), "expires_at") {
		t.Fatalf("error = %v, want expires_at", err)
	}
}

func TestAcceptTradeInstancePaysSellerAsNewWalletOwner(t *testing.T) {
	plan, err := AcceptTradeInstance(AcceptTradeInstanceInput{
		IdempotencyKey:                  "accept-1",
		ExternalRequestID:               "external-2",
		TradeInstanceID:                 "22222222-2222-4222-8222-222222222222",
		BuyerCapsuleerID:                2002,
		SellerCapsuleerID:               1001,
		ItemTypeID:                      34,
		StationID:                       60003760,
		QuantityRequested:               4,
		ISKAmountPaid:                   100,
		BuyerWalletID:                   "33333333-3333-4333-8333-333333333333",
		SellerWalletID:                  "44444444-4444-4444-8444-444444444444",
		ItemStackEscrowID:               "55555555-5555-4555-8555-555555555555",
		CreateBuyerDestinationItemStack: true,
		CompleteTrade:                   true,
	})
	if err != nil {
		t.Fatalf("AcceptTradeInstance returned error: %v", err)
	}
	if len(plan.Operations) != 5 {
		t.Fatalf("expected 5 operations, got %d", len(plan.Operations))
	}
	if plan.Operations[0].Kind != settlement.OperationCreateNewEmptyItemStack || plan.Operations[0].CreateNewEmptyItemStack == nil {
		t.Fatalf("operation 0 = %#v, want create buyer item stack", plan.Operations[0])
	}
	if plan.Operations[2].Kind != settlement.OperationTransferQuantityFromItemStackEscrowToItemStackWithNewOwner || plan.Operations[2].TransferQuantityFromItemStackEscrowToItemStackWithNewOwner == nil {
		t.Fatalf("operation 2 = %#v, want item escrow to new owner", plan.Operations[2])
	}
	if plan.Operations[3].Kind != settlement.OperationTransferISKAmountFromWalletEscrowToWalletWithNewOwner || plan.Operations[3].TransferISKAmountFromWalletEscrowToWalletWithNewOwner == nil {
		t.Fatalf("operation 3 = %#v, want wallet escrow to seller as new owner", plan.Operations[3])
	}
}

func TestCancelTradeInstanceCanOnlyModifyTradeState(t *testing.T) {
	plan, err := CancelTradeInstance(CancelTradeInstanceInput{
		IdempotencyKey:         "cancel-1",
		ExternalRequestID:      "external-3",
		TradeInstanceID:        "66666666-6666-4666-8666-666666666666",
		CancelledByCapsuleerID: 1001,
	})
	if err != nil {
		t.Fatalf("CancelTradeInstance returned error: %v", err)
	}
	if len(plan.Operations) != 1 {
		t.Fatalf("expected 1 operation, got %d", len(plan.Operations))
	}
	stateOp := plan.Operations[0].ModifyTradeInstanceState
	if plan.Operations[0].Kind != settlement.OperationModifyTradeInstanceState || stateOp == nil {
		t.Fatalf("operation 0 = %#v, want modify trade state", plan.Operations[0])
	}
	if stateOp.ToTradeState != TradeStateCancelled {
		t.Fatalf("state = %q, want %q", stateOp.ToTradeState, TradeStateCancelled)
	}
}
