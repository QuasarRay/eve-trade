package gametrade

import (
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
)

type AcceptTradeInstanceInput struct {
	IdempotencyKey                  string
	ExternalRequestID               string
	TradeInstanceID                 string
	BuyerCapsuleerID                int64
	SellerCapsuleerID               int64
	ItemTypeID                      int64
	StationID                       int64
	QuantityRequested               int64
	ISKAmountPaid                   int64
	BuyerWalletID                   string
	SellerWalletID                  string
	ItemStackEscrowID               string
	BuyerDestinationItemStackID     string
	CreateBuyerDestinationItemStack bool
	WalletEscrowID                  string
	CompleteTrade                   bool
}

func AcceptTradeInstance(input AcceptTradeInstanceInput) (SettlementPlan, error) {
	if err := validateAcceptTradeInstanceInput(input); err != nil {
		return SettlementPlan{}, err
	}

	destinationItemStackID := input.BuyerDestinationItemStackID
	if destinationItemStackID == "" {
		var err error
		destinationItemStackID, err = deterministicID(input.IdempotencyKey, "buyer-destination-item-stack")
		if err != nil {
			return SettlementPlan{}, err
		}
	}

	walletEscrowID := input.WalletEscrowID
	if walletEscrowID == "" {
		var err error
		walletEscrowID, err = deterministicID(input.IdempotencyKey, "wallet-escrow")
		if err != nil {
			return SettlementPlan{}, err
		}
	}
	ops := make([]settlement.Operation, 0, 5)
	if input.CreateBuyerDestinationItemStack || input.BuyerDestinationItemStackID == "" {
		ops = append(ops, settlement.Operation{
			Kind: settlement.OperationCreateNewEmptyItemStack,
			CreateNewEmptyItemStack: &settlement.CreateNewEmptyItemStack{
				ItemStackID: destinationItemStackID,
				OwnerID:     input.BuyerCapsuleerID,
				ItemTypeID:  input.ItemTypeID,
				StationID:   input.StationID,
			},
		})
	}

	ops = append(ops,
		settlement.Operation{
			Kind: settlement.OperationTransferISKAmountFromWalletToWalletEscrow,
			TransferISKAmountFromWalletToWalletEscrow: &settlement.TransferISKAmountFromWalletToWalletEscrow{
				SourceWalletID:  input.BuyerWalletID,
				WalletEscrowID:  walletEscrowID,
				TradeInstanceID: input.TradeInstanceID,
				ISKAmount:       input.ISKAmountPaid,
			},
		},
		settlement.Operation{
			Kind: settlement.OperationTransferQuantityFromItemStackEscrowToItemStackWithNewOwner,
			TransferQuantityFromItemStackEscrowToItemStackWithNewOwner: &settlement.TransferQuantityFromItemStackEscrowToItemStackWithNewOwner{
				ItemStackEscrowID:      input.ItemStackEscrowID,
				DestinationItemStackID: destinationItemStackID,
				Quantity:               input.QuantityRequested,
			},
		},
		settlement.Operation{
			Kind: settlement.OperationTransferISKAmountFromWalletEscrowToWalletWithNewOwner,
			TransferISKAmountFromWalletEscrowToWalletWithNewOwner: &settlement.TransferISKAmountFromWalletEscrowToWalletWithNewOwner{
				WalletEscrowID:      walletEscrowID,
				DestinationWalletID: input.SellerWalletID,
				ISKAmount:           input.ISKAmountPaid,
			},
		},
	)
	if input.CompleteTrade {
		ops = append(ops, settlement.Operation{
			Kind: settlement.OperationModifyTradeInstanceState,
			ModifyTradeInstanceState: &settlement.ModifyTradeInstanceState{
				TradeInstanceID:      input.TradeInstanceID,
				ToTradeState:         TradeStateCompleted,
				TradeStateChangeKind: TradeStateChangeAccepted,
				ChangedByService:     CreatedByService,
			},
		})
	}

	return SettlementPlan{
		Intent:                 settlement.IntentAccept,
		IdempotencyKey:         input.IdempotencyKey,
		ExternalRequestID:      input.ExternalRequestID,
		CausedByCapsuleerID:    input.BuyerCapsuleerID,
		Operations:             ops,
		TradeInstanceID:        input.TradeInstanceID,
		ItemStackEscrowID:      input.ItemStackEscrowID,
		WalletEscrowID:         walletEscrowID,
		DestinationItemStackID: destinationItemStackID,
	}, nil
}
