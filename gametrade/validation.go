package gametrade

import (
	"buf.build/go/protovalidate"
	tradev1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade/v1"
)

func validateIssueTradeInstanceInput(input IssueTradeInstanceInput) error {
	return protovalidate.Validate(&tradev1.IssueTradeInstanceInput{
		IdempotencyKey:      input.IdempotencyKey,
		ExternalRequestId:   input.ExternalRequestID,
		IssuedByCapsuleerId: input.IssuedByCapsuleerID,
		ItemStack: &tradev1.ItemStackSnapshot{
			ItemStackId: input.ItemStack.ItemStackID,
			OwnerId:     input.ItemStack.OwnerID,
			ItemTypeId:  input.ItemStack.ItemTypeID,
			StationId:   input.ItemStack.StationID,
			Quantity:    input.ItemStack.Quantity,
		},
		Quantity:          input.Quantity,
		UnitPriceIsk:      input.UnitPriceISK,
		ExpiresAt:         input.ExpiresAt,
		TradeInstanceId:   input.TradeInstanceID,
		ItemStackEscrowId: input.ItemStackEscrowID,
	})
}

func validateAcceptTradeInstanceInput(input AcceptTradeInstanceInput) error {
	return protovalidate.Validate(&tradev1.AcceptTradeInstanceInput{
		IdempotencyKey:                  input.IdempotencyKey,
		ExternalRequestId:               input.ExternalRequestID,
		TradeInstanceId:                 input.TradeInstanceID,
		BuyerCapsuleerId:                input.BuyerCapsuleerID,
		SellerCapsuleerId:               input.SellerCapsuleerID,
		ItemTypeId:                      input.ItemTypeID,
		StationId:                       input.StationID,
		QuantityRequested:               input.QuantityRequested,
		IskAmountPaid:                   input.ISKAmountPaid,
		BuyerWalletId:                   input.BuyerWalletID,
		SellerWalletId:                  input.SellerWalletID,
		ItemStackEscrowId:               input.ItemStackEscrowID,
		BuyerDestinationItemStackId:     input.BuyerDestinationItemStackID,
		CreateBuyerDestinationItemStack: input.CreateBuyerDestinationItemStack,
		WalletEscrowId:                  input.WalletEscrowID,
		CompleteTrade:                   input.CompleteTrade,
	})
}

func validateCancelTradeInstanceInput(input CancelTradeInstanceInput) error {
	return protovalidate.Validate(&tradev1.CancelTradeInstanceInput{
		IdempotencyKey:         input.IdempotencyKey,
		ExternalRequestId:      input.ExternalRequestID,
		TradeInstanceId:        input.TradeInstanceID,
		CancelledByCapsuleerId: input.CancelledByCapsuleerID,
		ItemStackEscrowId:      input.ItemStackEscrowID,
		ReturnItemStackId:      input.ReturnItemStackID,
		ReturnQuantity:         input.ReturnQuantity,
		WalletEscrowId:         input.WalletEscrowID,
		ReturnWalletId:         input.ReturnWalletID,
		ReturnIskAmount:        input.ReturnISKAmount,
	})
}
