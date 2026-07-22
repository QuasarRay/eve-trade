package settlementworker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sync/atomic"
	"time"

	"encore.dev/cron"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
)

var activeService atomic.Pointer[Service]

const (
	settlementOutboxBatchSize       = 50
	settlementOutboxMaxBatches      = 20
	settlementOutboxLeaseSeconds    = 2 * 60
	settlementOutboxDispatchTimeout = 30 * time.Second
)

var _ = cron.NewJob("dispatch-settlement-outbox", cron.JobConfig{
	Title:    "Dispatch durable settlement work",
	Every:    cron.Minute,
	Endpoint: DispatchSettlementOutbox,
})

type OutboxDispatchResponse struct {
	Claimed   int `json:"claimed"`
	Delivered int `json:"delivered"`
	Released  int `json:"released"`
}

//encore:api private method=POST path=/settlementworker/outbox/dispatch
func DispatchSettlementOutbox(ctx context.Context) (*OutboxDispatchResponse, error) {
	service := activeService.Load()
	if service == nil {
		return nil, fmt.Errorf("settlement worker service is not initialized")
	}
	return service.DispatchSettlementOutbox(ctx)
}

func (s *Service) startOutboxDispatcher(interval time.Duration) {
	if interval <= 0 {
		interval = time.Second
	}
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		runOutboxDispatchLoop(context.Background(), ticker.C, s.dispatchPendingOutbox)
	}()
}

func (s *Service) dispatchPendingOutbox(parent context.Context) {
	ctx, cancel := context.WithTimeout(parent, settlementOutboxDispatchTimeout)
	defer cancel()
	response, err := s.DispatchSettlementOutbox(ctx)
	if err != nil {
		slog.Error("settlement outbox dispatch failed", "error", err)
		return
	}
	if response.Claimed > 0 {
		slog.Info("settlement outbox dispatched", "claimed", response.Claimed, "delivered", response.Delivered, "released", response.Released)
	}
}

func runOutboxDispatchLoop(ctx context.Context, ticks <-chan time.Time, dispatch func(context.Context)) {
	for {
		dispatch(ctx)
		select {
		case <-ctx.Done():
			return
		case _, ok := <-ticks:
			if !ok {
				return
			}
		}
	}
}

func (s *Service) DispatchSettlementOutbox(ctx context.Context) (*OutboxDispatchResponse, error) {
	if s.outbox == nil || s.work == nil {
		return nil, fmt.Errorf("settlement outbox store and work publisher are required")
	}
	response := new(OutboxDispatchResponse)
	var dispatchErrors []error
	for batch := 0; batch < settlementOutboxMaxBatches; batch++ {
		claimed, err := s.outbox.ClaimSettlementOutbox(ctx, &tradesettlementv1.ClaimSettlementOutboxRequest{
			WorkerId:     s.workerIdentity(),
			Limit:        settlementOutboxBatchSize,
			LeaseSeconds: settlementOutboxLeaseSeconds,
		})
		if err != nil {
			return response, fmt.Errorf("claim settlement outbox: %w", err)
		}
		if len(claimed.GetDeliveries()) == 0 {
			break
		}
		response.Claimed += len(claimed.GetDeliveries())
		for _, delivery := range claimed.GetDeliveries() {
			released, err := s.dispatchOutboxDelivery(ctx, delivery)
			if err != nil {
				dispatchErrors = append(dispatchErrors, err)
				if released {
					response.Released++
				}
				continue
			}
			response.Delivered++
		}
	}
	if len(dispatchErrors) > 0 {
		return response, errors.Join(dispatchErrors...)
	}
	return response, nil
}

func (s *Service) dispatchOutboxDelivery(ctx context.Context, delivery *tradesettlementv1.SettlementOutboxDelivery) (bool, error) {
	if delivery == nil || delivery.GetOperationId() == "" || delivery.GetLeaseGeneration() == 0 {
		return false, fmt.Errorf("claimed settlement outbox delivery is incomplete")
	}
	var work settlement.Work
	if err := json.Unmarshal(delivery.GetWorkPayloadJson(), &work); err != nil {
		return true, s.releaseOutboxDelivery(ctx, delivery, fmt.Errorf("decode durable settlement work: %w", err))
	}
	if work.OperationID != delivery.GetOperationId() {
		return true, s.releaseOutboxDelivery(ctx, delivery, fmt.Errorf(
			"durable settlement work operation_id %q does not match outbox operation_id %q",
			work.OperationID,
			delivery.GetOperationId(),
		))
	}
	messageID, err := s.work.Publish(ctx, &work)
	if err != nil {
		return true, s.releaseOutboxDelivery(ctx, delivery, fmt.Errorf("publish durable settlement work: %w", err))
	}
	_, err = s.outbox.CompleteSettlementOutbox(ctx, &tradesettlementv1.CompleteSettlementOutboxRequest{
		OperationId:     delivery.GetOperationId(),
		WorkerId:        s.workerIdentity(),
		LeaseGeneration: delivery.GetLeaseGeneration(),
		MessageId:       messageID,
	})
	if err != nil {
		return false, fmt.Errorf("mark settlement outbox delivered after broker message %s: %w", messageID, err)
	}
	return false, nil
}

func (s *Service) releaseOutboxDelivery(ctx context.Context, delivery *tradesettlementv1.SettlementOutboxDelivery, cause error) error {
	description := cause.Error()
	if len(description) > 2048 {
		description = description[:2048]
	}
	releaseCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 10*time.Second)
	defer cancel()
	_, releaseErr := s.outbox.ReleaseSettlementOutbox(releaseCtx, &tradesettlementv1.ReleaseSettlementOutboxRequest{
		OperationId:      delivery.GetOperationId(),
		WorkerId:         s.workerIdentity(),
		LeaseGeneration:  delivery.GetLeaseGeneration(),
		ErrorDescription: description,
	})
	if releaseErr != nil {
		return errors.Join(cause, fmt.Errorf("release settlement outbox delivery: %w", releaseErr))
	}
	return cause
}
