package gametrade

import (
	"fmt"

	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type IssueTradeInstanceInput struct {
	IdempotencyKey      string
	ExternalRequestID   string
	IssuedByCapsuleerID int64
	ItemStack           ItemStackRow
	Quantity            int64
	UnitPriceISK        int64
	ExpiresAt           *timestamppb.Timestamp
	TradeInstanceID     string
	ItemStackEscrowID   string
}

func IssueTradeInstance(input IssueTradeInstanceInput) (SettlementPlan, error) {
	if err := validateRequired("idempotency_key", input.IdempotencyKey); err != nil {
		return SettlementPlan{}, err
	}
	if err := validateRequired("item_stack_id", input.ItemStack.ItemStackID); err != nil {
		return SettlementPlan{}, err
	}
	if input.IssuedByCapsuleerID <= 0 {
		return SettlementPlan{}, fmt.Errorf("issued_by_capsuleer_id is required")
	}
	if input.ItemStack.OwnerID != input.IssuedByCapsuleerID {
		return SettlementPlan{}, fmt.Errorf("item stack owner must match issued_by_capsuleer_id")
	}
	if err := validatePositive("quantity", input.Quantity); err != nil {
		return SettlementPlan{}, err
	}
	if input.ItemStack.Quantity < input.Quantity {
		return SettlementPlan{}, fmt.Errorf("item stack quantity is lower than requested issue quantity")
	}
	if err := validatePositive("unit_price_isk", input.UnitPriceISK); err != nil {
		return SettlementPlan{}, err
	}

	tradeInstanceID := input.TradeInstanceID
	if tradeInstanceID == "" {
		var err error
		tradeInstanceID, err = deterministicID(input.IdempotencyKey, "trade-instance")
		if err != nil {
			return SettlementPlan{}, err
		}
	}
	itemStackEscrowID := input.ItemStackEscrowID
	if itemStackEscrowID == "" {
		var err error
		itemStackEscrowID, err = deterministicID(input.IdempotencyKey, "item-stack-escrow")
		if err != nil {
			return SettlementPlan{}, err
		}
	}

	ops := []*tradesettlementv1.SettlementOperation{
		{
			Operation: &tradesettlementv1.SettlementOperation_CreateNewTradeInstanceRow{
				CreateNewTradeInstanceRow: &tradesettlementv1.CreateNewTradeInstanceRow{
					TradeInstanceId: tradeInstanceID,
					TradeKind:       TradeKindSell,
					TradeState:      TradeStateOpen,
					IssuerId:        input.IssuedByCapsuleerID,
					ItemTypeId:      input.ItemStack.ItemTypeID,
					StationId:       input.ItemStack.StationID,
					TotalQuantity:   input.Quantity,
					UnitPriceIsk:    input.UnitPriceISK,
					ExpiresAt:       input.ExpiresAt,
				},
			},
		},
		{
			Operation: &tradesettlementv1.SettlementOperation_TransferQuantityFromItemStackToItemStackEscrow{
				TransferQuantityFromItemStackToItemStackEscrow: &tradesettlementv1.TransferQuantityFromItemStackToItemStackEscrow{
					SourceItemStackId: input.ItemStack.ItemStackID,
					ItemStackEscrowId: itemStackEscrowID,
					TradeInstanceId:   tradeInstanceID,
					Quantity:          input.Quantity,
				},
			},
		},
	}

	return SettlementPlan{
		IdempotencyKey:      input.IdempotencyKey,
		ExternalRequestID:   input.ExternalRequestID,
		CausedByCapsuleerID: input.IssuedByCapsuleerID,
		Operations:          ops,
		TradeInstanceID:     tradeInstanceID,
		ItemStackEscrowID:   itemStackEscrowID,
	}, nil
}
