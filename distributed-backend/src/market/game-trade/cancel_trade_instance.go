package gametrade

import (
	"fmt"

	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
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
	if err := validateRequired("idempotency_key", input.IdempotencyKey); err != nil {
		return SettlementPlan{}, err
	}
	if err := validateRequired("trade_instance_id", input.TradeInstanceID); err != nil {
		return SettlementPlan{}, err
	}
	if input.CancelledByCapsuleerID <= 0 {
		return SettlementPlan{}, fmt.Errorf("cancelled_by_capsuleer_id is required")
	}

	ops := make([]*tradesettlementv1.SettlementOperation, 0, 3)
	if input.ItemStackEscrowID != "" || input.ReturnItemStackID != "" || input.ReturnQuantity != 0 {
		for name, value := range map[string]string{
			"item_stack_escrow_id": input.ItemStackEscrowID,
			"return_item_stack_id": input.ReturnItemStackID,
		} {
			if err := validateRequired(name, value); err != nil {
				return SettlementPlan{}, err
			}
		}
		if err := validatePositive("return_quantity", input.ReturnQuantity); err != nil {
			return SettlementPlan{}, err
		}
		ops = append(ops, &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner{
				TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner: &tradesettlementv1.TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner{
					ItemStackEscrowId:      input.ItemStackEscrowID,
					DestinationItemStackId: input.ReturnItemStackID,
					Quantity:               input.ReturnQuantity,
				},
			},
		})
	}

	if input.WalletEscrowID != "" || input.ReturnWalletID != "" || input.ReturnISKAmount != 0 {
		for name, value := range map[string]string{
			"wallet_escrow_id": input.WalletEscrowID,
			"return_wallet_id": input.ReturnWalletID,
		} {
			if err := validateRequired(name, value); err != nil {
				return SettlementPlan{}, err
			}
		}
		if err := validatePositive("return_isk_amount", input.ReturnISKAmount); err != nil {
			return SettlementPlan{}, err
		}
		ops = append(ops, &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner{
				TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner: &tradesettlementv1.TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner{
					WalletEscrowId:      input.WalletEscrowID,
					DestinationWalletId: input.ReturnWalletID,
					IskAmount:           input.ReturnISKAmount,
				},
			},
		})
	}

	ops = append(ops, &tradesettlementv1.SettlementOperation{
		Operation: &tradesettlementv1.SettlementOperation_ModifyTradeInstanceState{
			ModifyTradeInstanceState: &tradesettlementv1.ModifyTradeInstanceState{
				TradeInstanceId:      input.TradeInstanceID,
				ToTradeState:         TradeStateCancelled,
				TradeStateChangeKind: TradeStateChangeCancelled,
				ChangedByService:     CreatedByService,
			},
		},
	})

	return SettlementPlan{
		IdempotencyKey:      input.IdempotencyKey,
		ExternalRequestID:   input.ExternalRequestID,
		CausedByCapsuleerID: input.CancelledByCapsuleerID,
		Operations:          ops,
		TradeInstanceID:     input.TradeInstanceID,
		ItemStackEscrowID:   input.ItemStackEscrowID,
		WalletEscrowID:      input.WalletEscrowID,
	}, nil
}
