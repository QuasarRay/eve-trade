package market

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"

	commonv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/common/v1"
	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/market/v1"
	operationv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/operation/v1"
)

func stableID(prefix string, values ...string) string {
	hash := stableHash(prefix, values...)
	return prefix + "-" + hex.EncodeToString(hash[:16])
}

func stableUUID(prefix string, values ...string) string {
	hash := stableHash(prefix, values...)
	hash[6] = (hash[6] & 0x0f) | 0x40
	hash[8] = (hash[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", hash[0:4], hash[4:6], hash[6:8], hash[8:10], hash[10:16])
}

func stableHash(prefix string, values ...string) [32]byte {
	hash := sha256.New()
	writeStableText(hash, prefix)
	for _, value := range values {
		writeStableText(hash, value)
	}
	var out [32]byte
	copy(out[:], hash.Sum(nil))
	return out
}

func writeStableText(hash interface{ Write([]byte) (int, error) }, value string) {
	_, _ = hash.Write([]byte{byte(len(value) >> 24), byte(len(value) >> 16), byte(len(value) >> 8), byte(len(value))})
	_, _ = hash.Write([]byte(value))
}

func interactionStableSeed(interaction *marketv1.ProjectTradeInteraction) []string {
	return []string{
		interaction.GetInteractionId().GetValue(),
		interaction.GetSourceActivityId().GetValue(),
		interaction.GetCorrelationId().GetValue(),
		interaction.GetTraceId().GetValue(),
	}
}

func tradeInstanceIDForInteraction(interaction *marketv1.ProjectTradeInteraction) *commonv1.TradeInstanceId {
	if interaction.GetVisibleTradeContext().GetTradeInstanceId().GetValue() != "" {
		return interaction.GetVisibleTradeContext().GetTradeInstanceId()
	}

	return &commonv1.TradeInstanceId{
		Value: stableUUID("trade-instance", interactionStableSeed(interaction)...),
	}
}

func operationIDForInteraction(kind operationv1.TradeOperationKind, interaction *marketv1.ProjectTradeInteraction) *commonv1.OperationId {
	seed := append([]string{kind.String()}, interactionStableSeed(interaction)...)
	return &commonv1.OperationId{Value: stableUUID("operation", seed...)}
}

func requestIDForInteraction(kind operationv1.TradeOperationKind, interaction *marketv1.ProjectTradeInteraction) *commonv1.RequestId {
	seed := append([]string{kind.String()}, interactionStableSeed(interaction)...)
	return &commonv1.RequestId{Value: stableUUID("request", seed...)}
}

func idempotencyKeyForInteraction(kind operationv1.TradeOperationKind, interaction *marketv1.ProjectTradeInteraction) *commonv1.IdempotencyKey {
	seed := append([]string{kind.String()}, interactionStableSeed(interaction)...)
	return &commonv1.IdempotencyKey{Value: stableID("market-idem", seed...)}
}

func itemStackEscrowIDForInteraction(interaction *marketv1.ProjectTradeInteraction) *commonv1.ItemStackEscrowId {
	return &commonv1.ItemStackEscrowId{
		Value: stableUUID(
			"item-stack-escrow",
			tradeInstanceIDForInteraction(interaction).GetValue(),
			interaction.GetVisibleTradeContext().GetSourceItemStackId().GetValue(),
		),
	}
}

func walletEscrowIDForInteraction(interaction *marketv1.ProjectTradeInteraction) *commonv1.WalletEscrowId {
	return &commonv1.WalletEscrowId{
		Value: stableUUID("wallet-escrow", tradeInstanceIDForInteraction(interaction).GetValue()),
	}
}

func tradeTransactionIDForInteraction(interaction *marketv1.ProjectTradeInteraction) *commonv1.TradeTransactionId {
	return &commonv1.TradeTransactionId{
		Value: stableUUID("trade-transaction", interactionStableSeed(interaction)...),
	}
}

func settlementIDForInteraction(interaction *marketv1.ProjectTradeInteraction) *commonv1.SettlementId {
	return &commonv1.SettlementId{
		Value: stableUUID("settlement", interactionStableSeed(interaction)...),
	}
}
