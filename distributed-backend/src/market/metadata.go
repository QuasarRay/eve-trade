package market

import (
	"time"

	commonv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/common/v1"
	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/market/v1"
	operationv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/operation/v1"
	settlementv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/settlement/v1"
)

const marketServiceName = "market"

func buildTradeDecision(draft *tradeLifecycleDecisionDraft) (*marketv1.TradeDecision, error) {
	if draft == nil || draft.interaction == nil {
		return nil, ErrMissingInteraction
	}

	metadata := operationMetadataForInteraction(draft.requiredOperation, draft.interaction)
	decision := &marketv1.TradeDecision{
		SourceInteractionId:   draft.interaction.GetInteractionId(),
		CorrelationId:         draft.interaction.GetCorrelationId(),
		RequiredOperationKind: draft.requiredOperation,
		SourceInteraction:     draft.interaction,
	}

	switch draft.requiredOperation {
	case operationv1.TradeOperationKind_TRADE_OPERATION_KIND_ISSUE_TRADE_INSTANCE:
		decision.RequiredOperation = &marketv1.TradeDecision_IssueTradeInstance{
			IssueTradeInstance: issueTradeInstanceCommand(draft.interaction, metadata),
		}
	case operationv1.TradeOperationKind_TRADE_OPERATION_KIND_SETTLE_TRADE_INSTANCE:
		decision.RequiredOperation = &marketv1.TradeDecision_SettleTradeInstance{
			SettleTradeInstance: settleTradeInstanceCommand(draft.interaction, metadata),
		}
	case operationv1.TradeOperationKind_TRADE_OPERATION_KIND_CANCEL_TRADE_INSTANCE:
		decision.RequiredOperation = &marketv1.TradeDecision_CancelTradeInstance{
			CancelTradeInstance: cancelTradeInstanceCommand(draft.interaction, metadata),
		}
	default:
		return nil, ErrInvalidInteractionKind
	}

	return decision, nil
}

func operationMetadataForInteraction(kind operationv1.TradeOperationKind, interaction *marketv1.ProjectTradeInteraction) *commonv1.OperationMetadata {
	requestedAt := interaction.GetOccurredAtUnixMillis()
	if requestedAt <= 0 {
		requestedAt = time.Now().UnixMilli()
	}

	return &commonv1.OperationMetadata{
		OperationId:           operationIDForInteraction(kind, interaction),
		RequestId:             requestIDForInteraction(kind, interaction),
		IdempotencyKey:        idempotencyKeyForInteraction(kind, interaction),
		CorrelationId:         interaction.GetCorrelationId(),
		TraceId:               interaction.GetTraceId(),
		SourceSystem:          &commonv1.SourceSystem{Value: "eve-trade.market"},
		ExternalOperationId:   &commonv1.ExternalOperationId{Value: interaction.GetSourceActivityId().GetValue()},
		CausedByCapsuleerId:   interaction.GetCapsuleerId(),
		CreatedByService:      &commonv1.CreatedByService{Value: marketServiceName},
		RequestedAtUnixMillis: requestedAt,
	}
}

func issueTradeInstanceCommand(interaction *marketv1.ProjectTradeInteraction, metadata *commonv1.OperationMetadata) *operationv1.IssueTradeInstanceCommand {
	context := interaction.GetVisibleTradeContext()
	selected := interaction.GetSelectedItems()[0]
	sourceStackID := selected.GetItemStackId()
	if sourceStackID.GetValue() == "" {
		sourceStackID = context.GetSourceItemStackId()
	}

	return &operationv1.IssueTradeInstanceCommand{
		Metadata: metadata,
		RowIds: &operationv1.IssueTradeInstanceRowIds{
			TradeInstanceId:   tradeInstanceIDForInteraction(interaction),
			IssuerId:          interaction.GetCapsuleerId(),
			IssuerWalletId:    context.GetWalletId(),
			ItemTypeId:        selected.GetItemTypeId(),
			StationId:         context.GetStationId(),
			RegionId:          context.GetRegionId(),
			SourceItemStackId: sourceStackID,
			ItemStackEscrowId: itemStackEscrowIDForInteraction(interaction),
			WalletEscrowId:    walletEscrowIDForInteraction(interaction),
		},
		Terms: &operationv1.IssueTradeInstanceTerms{
			TotalQuantity:       selected.GetQuantity(),
			UnitPriceIsk:        unitPriceForInteraction(interaction),
			ExpiresAtUnixMillis: issueExpiresAtUnixMillis(interaction),
		},
	}
}

func settleTradeInstanceCommand(interaction *marketv1.ProjectTradeInteraction, metadata *commonv1.OperationMetadata) *operationv1.SettleTradeInstanceCommand {
	context := interaction.GetVisibleTradeContext()
	quantity := quantityForInteraction(interaction)
	unitPrice := unitPriceForInteraction(interaction)
	totalPrice := totalPriceForInteraction(interaction)

	accepted := &operationv1.AcceptTradeInstanceCommand{
		Metadata: metadata,
		RowIds: &operationv1.AcceptTradeInstanceRowIds{
			TradeInstanceId:        context.GetTradeInstanceId(),
			BuyerCapsuleerId:       interaction.GetCapsuleerId(),
			BuyerWalletId:          context.GetWalletId(),
			DestinationItemStackId: context.GetDestinationItemStackId(),
		},
		Terms: &operationv1.AcceptTradeInstanceTerms{
			Quantity:              quantity,
			ExpectedUnitPriceIsk:  unitPrice,
			ExpectedTotalPriceIsk: totalPrice,
			AcceptedAtUnixMillis:  requestedAtUnixMillis(interaction),
		},
	}

	return &operationv1.SettleTradeInstanceCommand{
		Metadata: metadata,
		RowIds: &operationv1.SettleTradeInstanceRowIds{
			TradeInstanceId:         context.GetTradeInstanceId(),
			SourceItemStackEscrowId: itemStackEscrowIDForInteraction(interaction),
			TradeTransactionId:      tradeTransactionIDForInteraction(interaction),
			SettlementId:            settlementIDForInteraction(interaction),
			SellerCapsuleerId:       interaction.GetCapsuleerId(),
			SellerWalletId:          context.GetWalletId(),
			BuyerCapsuleerId:        interaction.GetCapsuleerId(),
			BuyerWalletId:           context.GetWalletId(),
			DestinationItemStackId:  context.GetDestinationItemStackId(),
		},
		Terms: &operationv1.SettleTradeInstanceTerms{
			Quantity:              quantity,
			UnitPriceIsk:          unitPrice,
			TotalPriceIsk:         totalPrice,
			RequestedAtUnixMillis: requestedAtUnixMillis(interaction),
		},
		AcceptedTrade: accepted,
	}
}

func cancelTradeInstanceCommand(interaction *marketv1.ProjectTradeInteraction, metadata *commonv1.OperationMetadata) *operationv1.CancelTradeInstanceCommand {
	return &operationv1.CancelTradeInstanceCommand{
		Metadata: metadata,
		RowIds: &operationv1.CancelTradeInstanceRowIds{
			TradeInstanceId:       interaction.GetVisibleTradeContext().GetTradeInstanceId(),
			RequestingCapsuleerId: interaction.GetCapsuleerId(),
		},
		Reason: "player cancelled visible trade",
	}
}

func settlementCommandFromDecision(decision *marketv1.TradeDecision) (*settlementv1.TradeSettlementCommand, error) {
	if decision == nil {
		return nil, ErrMissingSettlementCommand
	}

	switch {
	case decision.GetIssueTradeInstance() != nil:
		command := decision.GetIssueTradeInstance()
		return &settlementv1.TradeSettlementCommand{
			Metadata:      command.GetMetadata(),
			OperationKind: operationv1.TradeOperationKind_TRADE_OPERATION_KIND_ISSUE_TRADE_INSTANCE,
			Command: &settlementv1.TradeSettlementCommand_IssueTradeInstance{
				IssueTradeInstance: command,
			},
		}, nil
	case decision.GetSettleTradeInstance() != nil:
		command := decision.GetSettleTradeInstance()
		return &settlementv1.TradeSettlementCommand{
			Metadata:      command.GetMetadata(),
			OperationKind: operationv1.TradeOperationKind_TRADE_OPERATION_KIND_SETTLE_TRADE_INSTANCE,
			Command: &settlementv1.TradeSettlementCommand_SettleTradeInstance{
				SettleTradeInstance: command,
			},
		}, nil
	case decision.GetCancelTradeInstance() != nil:
		command := decision.GetCancelTradeInstance()
		return &settlementv1.TradeSettlementCommand{
			Metadata:      command.GetMetadata(),
			OperationKind: operationv1.TradeOperationKind_TRADE_OPERATION_KIND_CANCEL_TRADE_INSTANCE,
			Command: &settlementv1.TradeSettlementCommand_CancelTradeInstance{
				CancelTradeInstance: command,
			},
		}, nil
	default:
		return nil, ErrMissingSettlementCommand
	}
}

func quantityForInteraction(interaction *marketv1.ProjectTradeInteraction) *commonv1.ItemQuantity {
	if interaction.GetTypedValues().GetQuantity().GetUnits() > 0 {
		return interaction.GetTypedValues().GetQuantity()
	}
	if len(interaction.GetSelectedItems()) > 0 && interaction.GetSelectedItems()[0].GetQuantity().GetUnits() > 0 {
		return interaction.GetSelectedItems()[0].GetQuantity()
	}
	return &commonv1.ItemQuantity{Units: 1}
}

func unitPriceForInteraction(interaction *marketv1.ProjectTradeInteraction) *commonv1.IskAmount {
	if interaction.GetTypedValues().GetUnitPriceIsk() != nil {
		return interaction.GetTypedValues().GetUnitPriceIsk()
	}
	return &commonv1.IskAmount{}
}

func totalPriceForInteraction(interaction *marketv1.ProjectTradeInteraction) *commonv1.IskAmount {
	if interaction.GetTypedValues().GetTotalPriceIsk() != nil {
		return interaction.GetTypedValues().GetTotalPriceIsk()
	}
	return &commonv1.IskAmount{MinorUnits: quantityForInteraction(interaction).GetUnits() * unitPriceForInteraction(interaction).GetMinorUnits()}
}

func issueExpiresAtUnixMillis(interaction *marketv1.ProjectTradeInteraction) int64 {
	requestedAt := requestedAtUnixMillis(interaction)
	return requestedAt + int64(time.Hour/time.Millisecond)
}

func requestedAtUnixMillis(interaction *marketv1.ProjectTradeInteraction) int64 {
	if interaction.GetOccurredAtUnixMillis() > 0 {
		return interaction.GetOccurredAtUnixMillis()
	}

	return time.Now().UnixMilli()
}
