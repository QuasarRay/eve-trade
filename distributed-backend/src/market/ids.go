package market

import (
	"crypto/sha256"
	"encoding/hex"

	evetradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/v1"
)

func stableID(prefix string, values ...string) string {
	hash := sha256.New()
	writeStableText(hash, prefix)
	for _, value := range values {
		writeStableText(hash, value)
	}
	sum := hash.Sum(nil)
	return prefix + "-" + hex.EncodeToString(sum[:16])
}

func writeStableText(hash interface{ Write([]byte) (int, error) }, value string) {
	_, _ = hash.Write([]byte{byte(len(value) >> 24), byte(len(value) >> 16), byte(len(value) >> 8), byte(len(value))})
	_, _ = hash.Write([]byte(value))
}

func interactionStableSeed(interaction *evetradev1.ProjectTradeInteraction) []string {
	return []string{
		interaction.GetInteractionId().GetValue(),
		interaction.GetSourceActivityId().GetValue(),
		interaction.GetCorrelationId().GetValue(),
		interaction.GetTraceId().GetValue(),
	}
}

func tradeInstanceIDForInteraction(interaction *evetradev1.ProjectTradeInteraction) *evetradev1.TradeInstanceId {
	if interaction.GetVisibleTradeInstanceId().GetValue() != "" {
		return interaction.GetVisibleTradeInstanceId()
	}

	return &evetradev1.TradeInstanceId{
		Value: stableID("trade-instance", interactionStableSeed(interaction)...),
	}
}

func transactionIDForInteraction(function transactionFunction, interaction *evetradev1.ProjectTradeInteraction) *evetradev1.TradeInstanceTransactionId {
	seed := append([]string{string(function)}, interactionStableSeed(interaction)...)
	return &evetradev1.TradeInstanceTransactionId{
		Value: stableID("trade-tx", seed...),
	}
}

func idempotencyKeyForInteraction(function transactionFunction, interaction *evetradev1.ProjectTradeInteraction) *evetradev1.IdempotencyKey {
	seed := append([]string{string(function)}, interactionStableSeed(interaction)...)
	return &evetradev1.IdempotencyKey{
		Value: stableID("market-idem", seed...),
	}
}
