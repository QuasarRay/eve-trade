package market

import (
	evetradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/v1"
)

func buildTransactionMetadata(draft *tradeLifecycleDecisionDraft) (*evetradev1.TradeInstanceTransactionMetadata, error) {
	common := &evetradev1.TradeInstanceTransactionCommonMetadata{
		TransactionId:  transactionIDForInteraction(draft.requiredFunction, draft.interaction),
		IdempotencyKey: idempotencyKeyForInteraction(draft.requiredFunction, draft.interaction),
		CorrelationId:  draft.interaction.GetCorrelationId(),
		TraceId:        draft.interaction.GetTraceId(),
	}
	rows := rowIdentitiesForInteraction(draft.interaction)
	terms := termsForInteraction(draft.interaction)

	switch draft.requiredFunction {
	case transactionFunctionIssueTradeInstance:
		return &evetradev1.TradeInstanceTransactionMetadata{
			RequestedTransactionFunction: &evetradev1.TradeInstanceTransactionMetadata_IssueTradeInstance{
				IssueTradeInstance: &evetradev1.IssueTradeInstanceMetadata{
					Common:            common,
					RowIdentities:     rows,
					SourceInteraction: draft.interaction,
					Terms:             terms,
				},
			},
		}, nil
	case transactionFunctionSettleTradeInstance:
		return &evetradev1.TradeInstanceTransactionMetadata{
			RequestedTransactionFunction: &evetradev1.TradeInstanceTransactionMetadata_SettleTradeInstance{
				SettleTradeInstance: &evetradev1.SettleTradeInstanceMetadata{
					Common:            common,
					RowIdentities:     rows,
					SourceInteraction: draft.interaction,
					Terms:             terms,
				},
			},
		}, nil
	case transactionFunctionCancelTradeInstance:
		return &evetradev1.TradeInstanceTransactionMetadata{
			RequestedTransactionFunction: &evetradev1.TradeInstanceTransactionMetadata_CancelTradeInstance{
				CancelTradeInstance: &evetradev1.CancelTradeInstanceMetadata{
					Common:            common,
					RowIdentities:     rows,
					SourceInteraction: draft.interaction,
				},
			},
		}, nil
	default:
		return nil, ErrInvalidInteractionKind
	}
}

func rowIdentitiesForInteraction(interaction *evetradev1.ProjectTradeInteraction) *evetradev1.TradeInstanceRowIdentities {
	rows := &evetradev1.TradeInstanceRowIdentities{
		TradeInstanceId:   tradeInstanceIDForInteraction(interaction),
		IssuerCapsuleerId: interaction.GetCapsuleerId(),
		TradeHubId:        interaction.GetVisibleTradeHubId(),
	}

	for _, selected := range interaction.GetSelectedItems() {
		if selected.GetItemStackId().GetValue() != "" {
			rows.ItemStackIds = append(rows.ItemStackIds, selected.GetItemStackId())
		}
		rows.ItemInstanceIds = append(rows.ItemInstanceIds, selected.GetItemInstanceIds()...)
	}

	return rows
}

func termsForInteraction(interaction *evetradev1.ProjectTradeInteraction) *evetradev1.TradeInstanceTerms {
	terms := &evetradev1.TradeInstanceTerms{
		TradeTypeName: &evetradev1.TradeTypeName{Value: tradeTypeNameForWindow(interaction.GetTradeWindow())},
	}

	for _, selected := range interaction.GetSelectedItems() {
		line := &evetradev1.TradeAssetLine{
			ItemTypeId: selected.GetItemTypeId(),
			Quantity:   selected.GetQuantity(),
		}
		if selected.GetItemStackId().GetValue() != "" {
			line.ItemStackIds = append(line.ItemStackIds, selected.GetItemStackId())
		}
		line.ItemInstanceIds = append(line.ItemInstanceIds, selected.GetItemInstanceIds()...)
		terms.OfferedAssets = append(terms.OfferedAssets, line)
	}

	if interaction.GetTypedValues().GetTotalPrice().GetValue() > 0 {
		terms.RequestedAssets = append(terms.RequestedAssets, &evetradev1.TradeAssetLine{
			IskAmount: interaction.GetTypedValues().GetTotalPrice(),
		})
	}

	return terms
}
