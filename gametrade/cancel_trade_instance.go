package gametrade

import (
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
)

type CancelTradeInstanceInput struct {
	IdempotencyKey         string
	ExternalRequestID      string
	TradeInstanceID        string
	CancelledByCapsuleerID int64
	ItemStackEscrowID      string
	ReturnItemStackID      string
	ReturnQuantity         int64
	WalletEscrowID         string
	ReturnWalletID         string
	ReturnISKAmount        int64
}

func CancelTradeInstance(input CancelTradeInstanceInput) (SettlementPlan, error) {
	if err := validateCancelTradeInstanceInput(input); err != nil {
		return SettlementPlan{}, err
	}

	ops := make([]settlement.Operation, 0, 3)
	if input.ItemStackEscrowID != "" {
		ops = append(ops, settlement.Operation{
			Kind: settlement.OperationTransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner,
			TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner: &settlement.TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner{
				ItemStackEscrowID:      input.ItemStackEscrowID,
				DestinationItemStackID: input.ReturnItemStackID,
				Quantity:               input.ReturnQuantity,
			},
		})
	}

	if input.WalletEscrowID != "" {
		ops = append(ops, settlement.Operation{
			Kind: settlement.OperationTransferISKAmountFromWalletEscrowToWalletWithPreviousOwner,
			TransferISKAmountFromWalletEscrowToWalletWithPreviousOwner: &settlement.TransferISKAmountFromWalletEscrowToWalletWithPreviousOwner{
				WalletEscrowID:      input.WalletEscrowID,
				DestinationWalletID: input.ReturnWalletID,
				ISKAmount:           input.ReturnISKAmount,
			},
		})
	}

	ops = append(ops, settlement.Operation{
		Kind: settlement.OperationModifyTradeInstanceState,
		ModifyTradeInstanceState: &settlement.ModifyTradeInstanceState{
			TradeInstanceID:      input.TradeInstanceID,
			ToTradeState:         TradeStateCancelled,
			TradeStateChangeKind: TradeStateChangeCancelled,
			ChangedByService:     CreatedByService,
		},
	})

	return SettlementPlan{
		Intent:              settlement.IntentCancel,
		IdempotencyKey:      input.IdempotencyKey,
		ExternalRequestID:   input.ExternalRequestID,
		CausedByCapsuleerID: input.CancelledByCapsuleerID,
		Operations:          ops,
		TradeInstanceID:     input.TradeInstanceID,
		ItemStackEscrowID:   input.ItemStackEscrowID,
		WalletEscrowID:      input.WalletEscrowID,
	}, nil
}
