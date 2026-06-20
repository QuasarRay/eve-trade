package gametrade

import (
	"fmt"

	tradesettlementv1 "github.com/astral/eve-trade/proto/gen/eve/trade_settlement/v1"
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
	if err := validateRequired("idempotency_key", input.IdempotencyKey); err != nil {
		return SettlementPlan{}, err
	}
	for name, value := range map[string]string{
		"trade_instance_id":    input.TradeInstanceID,
		"buyer_wallet_id":      input.BuyerWalletID,
		"seller_wallet_id":     input.SellerWalletID,
		"item_stack_escrow_id": input.ItemStackEscrowID,
	} {
		if err := validateRequired(name, value); err != nil {
			return SettlementPlan{}, err
		}
	}
	if input.BuyerCapsuleerID <= 0 || input.SellerCapsuleerID <= 0 {
		return SettlementPlan{}, fmt.Errorf("buyer_capsuleer_id and seller_capsuleer_id are required")
	}
	if input.BuyerCapsuleerID == input.SellerCapsuleerID {
		return SettlementPlan{}, fmt.Errorf("buyer and seller must differ")
	}
	if err := validatePositive("quantity_requested", input.QuantityRequested); err != nil {
		return SettlementPlan{}, err
	}
	if err := validatePositive("isk_amount_paid", input.ISKAmountPaid); err != nil {
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
	ops := make([]*tradesettlementv1.SettlementOperation, 0, 5)
	if input.CreateBuyerDestinationItemStack || input.BuyerDestinationItemStackID == "" {
		ops = append(ops, &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_CreateNewEmptyItemStack{
				CreateNewEmptyItemStack: &tradesettlementv1.CreateNewEmptyItemStack{
					ItemStackId: destinationItemStackID,
					OwnerId:     input.BuyerCapsuleerID,
					ItemTypeId:  input.ItemTypeID,
					StationId:   input.StationID,
				},
			},
		})
	}

	ops = append(ops,
		&tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferIskAmountFromWalletToWalletEscrow{
				TransferIskAmountFromWalletToWalletEscrow: &tradesettlementv1.TransferIskAmountFromWalletToWalletEscrow{
					SourceWalletId:  input.BuyerWalletID,
					WalletEscrowId:  walletEscrowID,
					TradeInstanceId: input.TradeInstanceID,
					IskAmount:       input.ISKAmountPaid,
				},
			},
		},
		&tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferQuantityFromItemStackEscrowToItemStackWithNewOwner{
				TransferQuantityFromItemStackEscrowToItemStackWithNewOwner: &tradesettlementv1.TransferQuantityFromItemStackEscrowToItemStackWithNewOwner{
					ItemStackEscrowId:      input.ItemStackEscrowID,
					DestinationItemStackId: destinationItemStackID,
					Quantity:               input.QuantityRequested,
				},
			},
		},
		&tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferIskAmountFromWalletEscrowToWalletWithNewOwner{
				TransferIskAmountFromWalletEscrowToWalletWithNewOwner: &tradesettlementv1.TransferIskAmountFromWalletEscrowToWalletWithNewOwner{
					WalletEscrowId:      walletEscrowID,
					DestinationWalletId: input.SellerWalletID,
					IskAmount:           input.ISKAmountPaid,
				},
			},
		},
	)
	if input.CompleteTrade {
		ops = append(ops, &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_ModifyTradeInstanceState{
				ModifyTradeInstanceState: &tradesettlementv1.ModifyTradeInstanceState{
					TradeInstanceId:      input.TradeInstanceID,
					ToTradeState:         TradeStateCompleted,
					TradeStateChangeKind: TradeStateChangeAccepted,
					ChangedByService:     CreatedByService,
				},
			},
		})
	}

	return SettlementPlan{
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
