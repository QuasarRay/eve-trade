package settlementworker

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"encore.dev/beta/errs"
	"encore.dev/pubsub"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
)

const (
	settlementSubscriptionConcurrency = 8
	settlementSubscriptionMaxRetries  = 12
)

//encore:service
type Service struct {
	executor SettlementExecutor
	results  pubsub.Publisher[*settlement.Result]
}

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
	MaxConcurrency: settlementSubscriptionConcurrency,
	AckDeadline:    30 * time.Second,
	RetryPolicy: &pubsub.RetryPolicy{
		MinBackoff: 2 * time.Second,
		MaxBackoff: 2 * time.Minute,
		MaxRetries: settlementSubscriptionMaxRetries,
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
	request, err := toProtoRequest(work)
	if err != nil {
		slog.Warn("settlement work rejected", "error", err)
		return err
	}
	response, err := s.executor.ExecuteSettlementBatch(ctx, request)
	if err != nil {
		slog.Error("settlement work failed", "idempotency_key", work.IdempotencyKey, "error", err)
		return err
	}
	if _, err := s.results.Publish(ctx, &settlement.Result{
		IdempotencyKey:    work.IdempotencyKey,
		RequestID:         work.RequestID,
		SettlementBatchID: response.SettlementBatchId,
		BatchState:        response.BatchState,
		IdempotentReplay:  response.IdempotentReplay,
	}); err != nil {
		slog.Error("settlement result publish failed", "idempotency_key", work.IdempotencyKey, "settlement_batch_id", response.SettlementBatchId, "error", err)
		return err
	}
	slog.Info("settlement work completed", "idempotency_key", work.IdempotencyKey, "settlement_batch_id", response.SettlementBatchId, "idempotent_replay", response.IdempotentReplay)
	return nil
}
