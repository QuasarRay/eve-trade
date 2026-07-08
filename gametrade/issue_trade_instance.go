package gametrade

import (
	"time"

	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
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
	if err := validateIssueTradeInstanceInput(input); err != nil {
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
	var expiresAt *time.Time
	if input.ExpiresAt != nil {
		value := input.ExpiresAt.AsTime().UTC()
		expiresAt = &value
	}

	ops := []settlement.Operation{
		{
			Kind: settlement.OperationCreateNewTradeInstanceRow,
			CreateNewTradeInstanceRow: &settlement.CreateNewTradeInstanceRow{
				TradeInstanceID: tradeInstanceID,
				TradeKind:       TradeKindSell,
				TradeState:      TradeStateOpen,
				IssuerID:        input.IssuedByCapsuleerID,
				ItemTypeID:      input.ItemStack.ItemTypeID,
				StationID:       input.ItemStack.StationID,
				TotalQuantity:   input.Quantity,
				UnitPriceISK:    input.UnitPriceISK,
				ExpiresAt:       expiresAt,
			},
		},
		{
			Kind: settlement.OperationTransferQuantityFromItemStackToItemStackEscrow,
			TransferQuantityFromItemStackToItemStackEscrow: &settlement.TransferQuantityFromItemStackToItemStackEscrow{
				SourceItemStackID: input.ItemStack.ItemStackID,
				ItemStackEscrowID: itemStackEscrowID,
				TradeInstanceID:   tradeInstanceID,
				Quantity:          input.Quantity,
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
