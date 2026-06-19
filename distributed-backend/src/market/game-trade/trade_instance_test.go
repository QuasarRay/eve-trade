package gametrade

import (
	"testing"

	tradesettlementv1 "github.com/astral/eve-trade/market/distributed-backend/gen/trade_settlement/v1"
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
	if _, ok := plan.Operations[0].Operation.(*tradesettlementv1.SettlementOperation_CreateNewTradeInstanceRow); !ok {
		t.Fatalf("operation 0 = %T, want create trade", plan.Operations[0].Operation)
	}
	escrowOp, ok := plan.Operations[1].Operation.(*tradesettlementv1.SettlementOperation_TransferQuantityFromItemStackToItemStackEscrow)
	if !ok {
		t.Fatalf("operation 1 = %T, want transfer item stack to escrow", plan.Operations[1].Operation)
	}
	if escrowOp.TransferQuantityFromItemStackToItemStackEscrow.Quantity != 4 {
		t.Fatalf("escrow quantity = %d, want 4", escrowOp.TransferQuantityFromItemStackToItemStackEscrow.Quantity)
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
	})
	if err != nil {
		t.Fatalf("AcceptTradeInstance returned error: %v", err)
	}
	if len(plan.Operations) != 5 {
		t.Fatalf("expected 5 operations, got %d", len(plan.Operations))
	}
	if _, ok := plan.Operations[0].Operation.(*tradesettlementv1.SettlementOperation_CreateNewEmptyItemStack); !ok {
		t.Fatalf("operation 0 = %T, want create buyer item stack", plan.Operations[0].Operation)
	}
	if _, ok := plan.Operations[2].Operation.(*tradesettlementv1.SettlementOperation_TransferQuantityFromItemStackEscrowToItemStackWithNewOwner); !ok {
		t.Fatalf("operation 2 = %T, want item escrow to new owner", plan.Operations[2].Operation)
	}
	if _, ok := plan.Operations[3].Operation.(*tradesettlementv1.SettlementOperation_TransferIskAmountFromWalletEscrowToWalletWithNewOwner); !ok {
		t.Fatalf("operation 3 = %T, want wallet escrow to seller as new owner", plan.Operations[3].Operation)
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
	stateOp, ok := plan.Operations[0].Operation.(*tradesettlementv1.SettlementOperation_ModifyTradeInstanceState)
	if !ok {
		t.Fatalf("operation 0 = %T, want modify trade state", plan.Operations[0].Operation)
	}
	if stateOp.ModifyTradeInstanceState.ToTradeState != TradeStateCancelled {
		t.Fatalf("state = %q, want %q", stateOp.ModifyTradeInstanceState.ToTradeState, TradeStateCancelled)
	}
}
