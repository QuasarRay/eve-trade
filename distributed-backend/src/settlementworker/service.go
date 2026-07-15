package settlementworker

import (
	"context"
	"fmt"
	"log/slog"

	"encore.dev/beta/errs"
	"encore.dev/pubsub"
	fpArray "github.com/IBM/fp-go/v2/array"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/settlementrpc"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
)

//encore:service
type Service struct {
	executor SettlementExecutor
	results  pubsub.Publisher[*settlement.Result]
}

//lint:ignore U1000 Encore invokes this initializer through generated service wiring.
func initService() (*Service, error) {
	cfg := LoadConfig()
	executor, err := NewGRPCSettlementExecutor(cfg.TradeSettlementTarget, cfg.RequestTimeout)
	if err != nil {
		return nil, fmt.Errorf("create trade-settlement grpc client: %w", err)
	}
	return &Service{
		executor: executor,
		results:  pubsub.TopicRef[pubsub.Publisher[*settlement.Result]](settlement.ResultTopic),
	}, nil
}

var _ = pubsub.NewSubscription(settlement.WorkTopic, "trade-settlement-executor", pubsub.SubscriptionConfig[*settlement.Work]{
	Handler:        pubsub.MethodHandler((*Service).HandleSettlementWork),
	MaxConcurrency: 8,
	AckDeadline:    30000000000,
	RetryPolicy: &pubsub.RetryPolicy{
		MinBackoff: 2000000000,
		MaxBackoff: 120000000000,
		MaxRetries: 12,
	},
})

type HealthResponse struct {
	Status string `json:"status"`
}

//encore:api public method=GET path=/settlementworker/healthz
func (s *Service) SettlementWorkerHealth(ctx context.Context) (*HealthResponse, error) {
	return &HealthResponse{Status: "ok"}, nil
}

//encore:api public method=GET path=/settlementworker/readyz
func (s *Service) SettlementWorkerReady(ctx context.Context) (*HealthResponse, error) {
	if err := s.executor.Ping(ctx); err != nil {
		return nil, errs.WrapCode(err, errs.Unavailable, "trade-settlement unavailable")
	}
	return &HealthResponse{Status: "ready"}, nil
}

func (s *Service) HandleSettlementWork(ctx context.Context, work *settlement.Work) error {
	if work == nil || work.OperationID == "" {
		return fmt.Errorf("settlement work operation_id is required")
	}
	operation, err := s.executor.GetSettlementOperation(ctx, work.OperationID)
	if err != nil {
		return fmt.Errorf("load settlement operation %s: %w", work.OperationID, err)
	}
	if isTerminalOperation(operation.GetState()) {
		if operation.GetResultPublished() {
			return nil
		}
		return s.publishDurableResult(ctx, work, operation)
	}
	if _, err := s.executor.UpdateSettlementOperation(ctx, &tradesettlementv1.UpdateSettlementOperationRequest{
		OperationId: work.OperationID,
		State:       tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING,
	}); err != nil {
		return fmt.Errorf("mark settlement operation processing: %w", err)
	}
	request, err := toProtoRequest(work)
	if err != nil {
		slog.Warn("settlement work rejected", "error", err)
		return err
	}
	response, err := s.executor.ExecuteSettlementBatch(ctx, request)
	if err != nil {
		slog.Error("settlement work failed", "idempotency_key", work.IdempotencyKey, "error", err)
		if !isPermanentSettlementFailure(err) {
			return err
		}
		operation, updateErr := s.executor.UpdateSettlementOperation(ctx, &tradesettlementv1.UpdateSettlementOperationRequest{
			OperationId:        work.OperationID,
			State:              tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED,
			FailureCode:        settlementrpc.ErrorCodeString(err),
			FailureDescription: err.Error(),
		})
		if updateErr != nil {
			return fmt.Errorf("persist permanent settlement failure: %w", updateErr)
		}
		return s.publishDurableResult(ctx, work, operation)
	}
	operation, err = s.executor.UpdateSettlementOperation(ctx, &tradesettlementv1.UpdateSettlementOperationRequest{
		OperationId:       work.OperationID,
		State:             tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_SUCCEEDED,
		SettlementBatchId: response.SettlementBatchId,
	})
	if err != nil {
		return fmt.Errorf("persist successful settlement result: %w", err)
	}
	if err := s.publishDurableResult(ctx, work, operation); err != nil {
		return err
	}
	slog.Info("settlement work completed", "idempotency_key", work.IdempotencyKey, "settlement_batch_id", response.SettlementBatchId, "idempotent_replay", response.IdempotentReplay)
	return nil
}

func (s *Service) publishDurableResult(ctx context.Context, work *settlement.Work, operation *tradesettlementv1.SettlementOperationStatus) error {
	result := &settlement.Result{
		OperationID:        work.OperationID,
		IdempotencyKey:     work.IdempotencyKey,
		RequestID:          work.RequestID,
		SettlementBatchID:  operation.GetSettlementBatchId(),
		BatchState:         operation.GetState().String(),
		FailureCode:        operation.GetFailureCode(),
		FailureDescription: operation.GetFailureDescription(),
	}
	if _, err := s.results.Publish(ctx, result); err != nil {
		slog.Error("settlement result publish failed", "operation_id", work.OperationID, "error", err)
		return err
	}
	_, err := s.executor.UpdateSettlementOperation(ctx, &tradesettlementv1.UpdateSettlementOperationRequest{
		OperationId:        work.OperationID,
		State:              operation.GetState(),
		SettlementBatchId:  operation.GetSettlementBatchId(),
		FailureCode:        operation.GetFailureCode(),
		FailureDescription: operation.GetFailureDescription(),
		ResultPublished:    true,
	})
	if err != nil {
		return fmt.Errorf("mark settlement result published: %w", err)
	}
	return nil
}

var isTerminalOperation = containsValue(fpArray.From(
	tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_SUCCEEDED,
	tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED,
	tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_CANCELLED,
	tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_EXPIRED,
))

func isPermanentSettlementFailure(err error) bool {
	return settlementrpc.IsPermanentError(err)
}

func containsValue[T comparable](values []T) func(T) bool {
	return func(target T) bool {
		return fpArray.Reduce(func(found bool, candidate T) bool {
			return found || candidate == target
		}, false)(values)
	}
}
