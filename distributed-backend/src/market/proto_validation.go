package market

import (
	"fmt"
	"strings"

	"buf.build/go/protovalidate"
	"encore.dev/beta/errs"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

func validateMarketProto(message proto.Message) error {
	if err := protovalidate.Validate(message); err != nil {
		return apiError(errs.InvalidArgument, err)
	}
	return nil
}

func validateSubmitTradeGuiInteractionRequest(request *SubmitTradeGuiInteractionRequest) error {
	if request == nil {
		return apiError(errs.InvalidArgument, fmt.Errorf("request is required"))
	}
	return validateMarketProto(&marketv1.SubmitTradeGuiInteractionRequest{
		RawPayload: request.RawPayload,
	})
}

func decodeTradeGUIInteraction(rawPayload []byte) (*marketv1.TradeGuiInteraction, error) {
	var interaction marketv1.TradeGuiInteraction
	if err := (protojson.UnmarshalOptions{DiscardUnknown: false}).Unmarshal(rawPayload, &interaction); err != nil {
		return nil, apiError(errs.InvalidArgument, fmt.Errorf("decode trade GUI packet: %w", err))
	}
	normalizeTradeGUIInteraction(&interaction)
	if err := validateMarketProto(&interaction); err != nil {
		return nil, err
	}
	return &interaction, nil
}

func normalizeTradeGUIInteraction(interaction *marketv1.TradeGuiInteraction) {
	interaction.SchemaVersion = strings.TrimSpace(interaction.GetSchemaVersion())
	interaction.InteractionId = strings.TrimSpace(interaction.GetInteractionId())
	if interaction.Ui != nil {
		interaction.Ui.Window = strings.TrimSpace(interaction.Ui.GetWindow())
		interaction.Ui.Button = strings.TrimSpace(interaction.Ui.GetButton())
		interaction.Ui.Action = strings.TrimSpace(interaction.Ui.GetAction())
		interaction.Ui.ControlId = strings.TrimSpace(interaction.Ui.GetControlId())
	}
	if interaction.Input != nil {
		input := interaction.Input
		input.IdempotencyKey = strings.TrimSpace(input.GetIdempotencyKey())
		input.ExternalRequestId = strings.TrimSpace(input.GetExternalRequestId())
		input.TradeInstanceId = strings.TrimSpace(input.GetTradeInstanceId())
		input.BuyerWalletId = strings.TrimSpace(input.GetBuyerWalletId())
		input.BuyerDestinationItemStackId = strings.TrimSpace(input.GetBuyerDestinationItemStackId())
		if input.ItemStack != nil {
			input.ItemStack.ItemStackId = strings.TrimSpace(input.ItemStack.GetItemStackId())
		}
	}
}

func normalizeTradeGUIInput(interaction *marketv1.TradeGuiInteraction) tradeGUIInput {
	input := tradeGUIInput{}
	if interaction.GetInput() != nil {
		input = tradeGUIInputFromProto(interaction.GetInput())
	}
	if input.IdempotencyKey == "" {
		input.IdempotencyKey = interaction.GetInteractionId()
	}
	if input.ExternalRequestID == "" {
		input.ExternalRequestID = interaction.GetInteractionId()
	}
	return input
}

func tradeGUIInputFromProto(input *marketv1.TradeGuiInput) tradeGUIInput {
	return tradeGUIInput{
		IdempotencyKey:              input.GetIdempotencyKey(),
		ExternalRequestID:           input.GetExternalRequestId(),
		IssuedByCapsuleerID:         input.GetIssuedByCapsuleerId(),
		CancelledByCapsuleerID:      input.GetCancelledByCapsuleerId(),
		TradeInstanceID:             input.GetTradeInstanceId(),
		BuyerCapsuleerID:            input.GetBuyerCapsuleerId(),
		Quantity:                    input.GetQuantity(),
		QuantityRequested:           quantityRequestedFromProto(input),
		UnitPriceISK:                input.GetUnitPriceIsk(),
		BuyerWalletID:               input.GetBuyerWalletId(),
		BuyerDestinationItemStackID: input.GetBuyerDestinationItemStackId(),
		ItemStack:                   itemStackInputFromProto(input.GetItemStack()),
		ExpiresAt:                   input.GetExpiresAt(),
	}
}

func itemStackInputFromProto(itemStack *marketv1.ItemStackRow) *tradeGUIItemStackInput {
	if itemStack == nil {
		return nil
	}
	return &tradeGUIItemStackInput{
		ItemStackID: itemStack.GetItemStackId(),
		OwnerID:     itemStack.GetOwnerId(),
		ItemTypeID:  itemStack.GetItemTypeId(),
		StationID:   itemStack.GetStationId(),
		Quantity:    itemStack.GetQuantity(),
	}
}

func quantityRequestedFromProto(input *marketv1.TradeGuiInput) int64 {
	if input.GetQuantityRequested() > 0 {
		return input.GetQuantityRequested()
	}
	return input.GetQuantity()
}

func validateIssueTradeInstanceRequest(message issueTradeInstanceRequest) error {
	return validateMarketProto(&marketv1.IssueTradeInstanceRequest{
		IdempotencyKey:      message.IdempotencyKey,
		ExternalRequestId:   message.ExternalRequestID,
		IssuedByCapsuleerId: message.IssuedByCapsuleerID,
		ItemStack:           protoItemStackRow(message.ItemStack),
		Quantity:            message.Quantity,
		UnitPriceIsk:        message.UnitPriceISK,
		ExpiresAt:           message.ExpiresAt,
	})
}

func validateAcceptTradeInstanceRequest(message acceptTradeInstanceRequest) error {
	return validateMarketProto(&marketv1.AcceptTradeInstanceRequest{
		IdempotencyKey:              message.IdempotencyKey,
		ExternalRequestId:           message.ExternalRequestID,
		TradeInstanceId:             message.TradeInstanceID,
		BuyerCapsuleerId:            message.BuyerCapsuleerID,
		QuantityRequested:           message.QuantityRequested,
		BuyerWalletId:               message.BuyerWalletID,
		BuyerDestinationItemStackId: message.BuyerDestinationItemStackID,
	})
}

func validateCancelTradeInstanceRequest(message cancelTradeInstanceRequest) error {
	return validateMarketProto(&marketv1.CancelTradeInstanceRequest{
		IdempotencyKey:         message.IdempotencyKey,
		ExternalRequestId:      message.ExternalRequestID,
		TradeInstanceId:        message.TradeInstanceID,
		CancelledByCapsuleerId: message.CancelledByCapsuleerID,
	})
}

func protoItemStackRow(itemStack *tradeGUIItemStackInput) *marketv1.ItemStackRow {
	if itemStack == nil {
		return nil
	}
	return &marketv1.ItemStackRow{
		ItemStackId: itemStack.ItemStackID,
		OwnerId:     itemStack.OwnerID,
		ItemTypeId:  itemStack.ItemTypeID,
		StationId:   itemStack.StationID,
		Quantity:    itemStack.Quantity,
	}
}
