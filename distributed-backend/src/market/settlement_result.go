package market

import (
	"context"
	"fmt"

	"encore.dev/pubsub"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
)

var _ = pubsub.NewSubscription(settlement.ResultTopic, "market-settlement-result-projection", pubsub.SubscriptionConfig[*settlement.Result]{
	Handler:        HandleSettlementResult,
	MaxConcurrency: 8,
	AckDeadline:    30000000000,
	RetryPolicy: &pubsub.RetryPolicy{
		MinBackoff: 2000000000,
		MaxBackoff: 120000000000,
		MaxRetries: 12,
	},
})

func HandleSettlementResult(ctx context.Context, result *settlement.Result) error {
	if result == nil || result.OperationID == "" {
		return fmt.Errorf("settlement result operation_id is required")
	}
	handler, err := defaultMarketHandler(ctx)
	if err != nil {
		return err
	}
	reader, ok := handler.settlement.(OperationStatusReader)
	if !ok {
		return fmt.Errorf("settlement lifecycle reader is unavailable")
	}
	operation, err := reader.GetSettlementOperation(ctx, result.OperationID)
	if err != nil {
		return err
	}
	return validateSettlementResult(result, operation)
}

func validateSettlementResult(result *settlement.Result, operation *tradesettlementv1.SettlementOperationStatus) error {
	if result == nil || operation == nil {
		return fmt.Errorf("settlement result and durable operation are required")
	}
	status := settlementOperationStatus(operation.GetState())
	if status != "succeeded" && status != "failed" && status != "cancelled" && status != "expired" {
		return fmt.Errorf("settlement result %s references non-terminal operation state %s", result.OperationID, status)
	}
	if result.SettlementBatchID != operation.GetSettlementBatchId() || result.FailureCode != operation.GetFailureCode() {
		return fmt.Errorf("settlement result %s does not match durable operation status", result.OperationID)
	}
	return nil
}
