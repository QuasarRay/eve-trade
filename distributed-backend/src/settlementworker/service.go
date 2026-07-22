package settlementworker

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"strings"
	"sync"
	"time"

	"encore.dev/pubsub"
	fpArray "github.com/IBM/fp-go/v2/array"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/settlementrpc"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"google.golang.org/protobuf/types/known/timestamppb"
)

//encore:service
type Service struct {
	executor SettlementExecutor
	results  pubsub.Publisher[*settlement.Result]
	outbox   SettlementOutboxStore
	work     pubsub.Publisher[*settlement.Work]

	stateMu  sync.Mutex
	workerID string
}

type operationLease struct {
	owner      string
	generation uint64
	expiresAt  time.Time
}

const (
	settlementWorkerAckDeadline = 30 * time.Second
	settlementWorkerMinBackoff  = 2 * time.Second
	settlementWorkerMaxBackoff  = 2 * time.Minute
	settlementWorkerLease       = 2 * settlementWorkerAckDeadline
	settlementWorkerLeaseRenew  = settlementWorkerLease / 3
)

//lint:ignore U1000 Encore invokes this initializer through generated service wiring.
func initService() (*Service, error) {
	cfg := LoadConfig()
	if cfg.RequestTimeout >= settlementWorkerAckDeadline {
		return nil, fmt.Errorf("settlement worker ack deadline %s must exceed request timeout %s", settlementWorkerAckDeadline, cfg.RequestTimeout)
	}
	executor, err := NewGRPCSettlementExecutor(cfg.TradeSettlementTarget, cfg.RequestTimeout)
	if err != nil {
		return nil, fmt.Errorf("create trade-settlement grpc client: %w", err)
	}
	service := &Service{
		executor: executor,
		results:  pubsub.TopicRef[pubsub.Publisher[*settlement.Result]](settlement.ResultTopic),
		outbox:   executor,
		work:     pubsub.TopicRef[pubsub.Publisher[*settlement.Work]](settlement.WorkTopic),
	}
	activeService.Store(service)
	service.startOutboxDispatcher(cfg.OutboxDispatchInterval)
	return service, nil
}

var _ = pubsub.NewSubscription(settlement.WorkTopic, "trade-settlement-executor", pubsub.SubscriptionConfig[*settlement.Work]{
	Handler:        pubsub.MethodHandler((*Service).HandleSettlementWork),
	MaxConcurrency: 8,
	AckDeadline:    30 * time.Second,
	RetryPolicy: &pubsub.RetryPolicy{
		MinBackoff: 2 * time.Second,
		MaxBackoff: 2 * time.Minute,
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
	if s.executor == nil {
		return nil, fmt.Errorf("settlement operation store is unavailable")
	}
	if checker, ok := s.executor.(interface{ OperationStoreReady(context.Context) error }); ok {
		if err := checker.OperationStoreReady(ctx); err != nil {
			return nil, fmt.Errorf("settlement operation store unavailable: %w", err)
		}
	}
	if err := s.executor.Ping(ctx); err != nil {
		return nil, fmt.Errorf("trade-settlement executor unavailable: %w", err)
	}
	if s.results == nil {
		return nil, fmt.Errorf("settlement result publisher is unavailable")
	}
	if checker, ok := s.results.(interface{ Ready(context.Context) error }); ok {
		if err := checker.Ready(ctx); err != nil {
			return nil, fmt.Errorf("settlement result publisher unavailable: %w", err)
		}
	}
	return &HealthResponse{Status: "ready"}, nil
}

func (s *Service) HandleSettlementWork(ctx context.Context, work *settlement.Work) error {
	ticker := time.NewTicker(settlementWorkerLeaseRenew)
	defer ticker.Stop()
	return s.handleSettlementWork(ctx, work, ticker.C, nil, nil)
}

func (s *Service) HandleSettlementWorkWithLeaseTicks(ctx context.Context, work *settlement.Work, leaseTicks <-chan time.Time) error {
	return s.handleSettlementWork(ctx, work, leaseTicks, nil, nil)
}

func (s *Service) HandleSettlementWorkWithAckExtender(
	ctx context.Context,
	work *settlement.Work,
	ackTicks <-chan time.Time,
	extend func(context.Context, string) error,
) error {
	ticker := time.NewTicker(settlementWorkerLeaseRenew)
	defer ticker.Stop()
	return s.handleSettlementWork(ctx, work, ticker.C, ackTicks, extend)
}

func (s *Service) HandleSettlementMessage(ctx context.Context, raw []byte) error {
	var document map[string]json.RawMessage
	if err := json.Unmarshal(raw, &document); err != nil {
		slog.Warn("malformed settlement work discarded", "error", err)
		return nil
	}
	var work settlement.Work
	if err := json.Unmarshal(raw, &work); err != nil {
		return nil
	}
	if encodedVersion, ok := document["schema_version"]; ok {
		var version string
		if err := json.Unmarshal(encodedVersion, &version); err != nil || (version != "" && version != "settlement-work.v1") {
			return s.terminalizeInvalidWork(ctx, &work, fmt.Errorf("unsupported settlement work schema %q", version))
		}
		delete(document, "schema_version")
		raw, _ = json.Marshal(document)
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&work); err != nil {
		return s.terminalizeInvalidWork(ctx, &work, fmt.Errorf("decode settlement work: %w", err))
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return s.terminalizeInvalidWork(ctx, &work, fmt.Errorf("settlement work contains trailing data"))
	}
	return s.HandleSettlementWork(ctx, &work)
}

func (s *Service) HandleSettlementDeadLetter(ctx context.Context, work *settlement.Work, cause error) error {
	if cause == nil {
		cause = fmt.Errorf("settlement work retry budget exhausted")
	}
	return s.terminalizeWork(ctx, work, "DEAD_LETTERED", cause.Error())
}

func (s *Service) SettlementWorkerReadyWithSubscriptionCheck(ctx context.Context, check func(context.Context) error) (*HealthResponse, error) {
	if check == nil {
		return nil, fmt.Errorf("settlement subscription health check is unavailable")
	}
	if err := check(ctx); err != nil {
		return nil, fmt.Errorf("settlement subscription unavailable: %w", err)
	}
	return s.SettlementWorkerReady(ctx)
}

func (s *Service) SettlementWorkerStartupForState(_ context.Context, initialized func() bool) (*HealthResponse, error) {
	if initialized == nil || !initialized() {
		return nil, fmt.Errorf("settlement worker initialization is incomplete")
	}
	return &HealthResponse{Status: "started"}, nil
}

func (s *Service) handleSettlementWork(
	ctx context.Context,
	work *settlement.Work,
	leaseTicks <-chan time.Time,
	ackTicks <-chan time.Time,
	extendAck func(context.Context, string) error,
) error {
	if work == nil || work.OperationID == "" {
		return fmt.Errorf("settlement work operation_id is required")
	}
	request, err := toProtoRequest(work)
	if err != nil {
		slog.Warn("settlement work rejected", "operation_id", work.OperationID, "error", err)
		return s.terminalizeInvalidWork(ctx, work, err)
	}
	operation, err := s.executor.GetSettlementOperation(ctx, work.OperationID)
	if err != nil {
		return fmt.Errorf("load settlement operation %s: %w", work.OperationID, err)
	}
	if isTerminalOperation(operation.GetState()) {
		if operation.GetResultPublished() {
			return nil
		}
		return s.publishDurableResult(ctx, work, operation, leaseFromOperation(operation))
	}
	lease, acquired, err := s.acquireLease(ctx, operation)
	if err != nil {
		return err
	}
	if !acquired {
		return nil
	}
	response, lease, err := s.executeWithRenewal(ctx, work.OperationID, request, lease, leaseTicks, ackTicks, extendAck)
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
			LeaseOwner:         lease.owner,
			LeaseGeneration:    lease.generation,
			LeaseExpiresAt:     timestamppb.New(lease.expiresAt),
		})
		if updateErr != nil {
			return fmt.Errorf("persist permanent settlement failure: %w", updateErr)
		}
		return s.publishDurableResult(ctx, work, operation, lease)
	}
	operation, err = s.executor.UpdateSettlementOperation(ctx, &tradesettlementv1.UpdateSettlementOperationRequest{
		OperationId:       work.OperationID,
		State:             tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_SUCCEEDED,
		SettlementBatchId: response.SettlementBatchId,
		LeaseOwner:        lease.owner,
		LeaseGeneration:   lease.generation,
		LeaseExpiresAt:    timestamppb.New(lease.expiresAt),
	})
	if err != nil {
		return fmt.Errorf("persist successful settlement result: %w", err)
	}
	if err := s.publishDurableResult(ctx, work, operation, lease); err != nil {
		return err
	}
	slog.Info("settlement work completed", "idempotency_key", work.IdempotencyKey, "settlement_batch_id", response.SettlementBatchId, "idempotent_replay", response.IdempotentReplay)
	return nil
}

func (s *Service) executeWithRenewal(
	ctx context.Context,
	operationID string,
	request *tradesettlementv1.ExecuteSettlementBatchRequest,
	lease *operationLease,
	leaseTicks <-chan time.Time,
	ackTicks <-chan time.Time,
	extendAck func(context.Context, string) error,
) (*tradesettlementv1.ExecuteSettlementBatchResponse, *operationLease, error) {
	type outcome struct {
		response *tradesettlementv1.ExecuteSettlementBatchResponse
		err      error
	}
	executionCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	done := make(chan outcome, 1)
	go func() {
		response, err := s.executor.ExecuteSettlementBatch(executionCtx, request)
		done <- outcome{response: response, err: err}
	}()
	for {
		select {
		case result := <-done:
			return result.response, lease, result.err
		case _, ok := <-leaseTicks:
			if !ok {
				leaseTicks = nil
				continue
			}
			renewed, err := s.renewLease(ctx, operationID, lease)
			if err != nil {
				cancel()
				return nil, lease, fmt.Errorf("renew settlement operation lease: %w", err)
			}
			lease = renewed
		case _, ok := <-ackTicks:
			if !ok {
				ackTicks = nil
				continue
			}
			if extendAck == nil {
				cancel()
				return nil, lease, fmt.Errorf("ack deadline extension callback is required")
			}
			if err := extendAck(ctx, operationID); err != nil {
				cancel()
				return nil, lease, fmt.Errorf("extend settlement work ack deadline: %w", err)
			}
		case <-ctx.Done():
			return nil, lease, ctx.Err()
		}
	}
}

func (s *Service) acquireLease(ctx context.Context, operation *tradesettlementv1.SettlementOperationStatus) (*operationLease, bool, error) {
	now := time.Now().UTC()
	existing := leaseFromOperation(operation)
	if operation.GetState() == tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING && existing != nil && existing.expiresAt.After(now) {
		return nil, false, nil
	}
	generation := operation.GetLeaseGeneration() + 1
	if generation == 0 {
		return nil, false, fmt.Errorf("settlement lease generation overflow")
	}
	lease := &operationLease{owner: s.workerIdentity(), generation: generation, expiresAt: now.Add(settlementWorkerLease)}
	_, err := s.executor.UpdateSettlementOperation(ctx, processingUpdate(operation.GetOperationId(), lease))
	if err == nil {
		return lease, true, nil
	}
	current, loadErr := s.executor.GetSettlementOperation(ctx, operation.GetOperationId())
	if loadErr == nil {
		currentLease := leaseFromOperation(current)
		if current.GetState() == tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING && currentLease != nil && currentLease.expiresAt.After(now) {
			return nil, false, nil
		}
	}
	return nil, false, fmt.Errorf("mark settlement operation processing: %w", err)
}

func (s *Service) renewLease(ctx context.Context, operationID string, current *operationLease) (*operationLease, error) {
	if current == nil || current.generation == ^uint64(0) {
		return nil, fmt.Errorf("settlement lease generation overflow")
	}
	renewed := &operationLease{
		owner:      current.owner,
		generation: current.generation + 1,
		expiresAt:  time.Now().UTC().Add(settlementWorkerLease),
	}
	if _, err := s.executor.UpdateSettlementOperation(ctx, processingUpdate(operationID, renewed)); err != nil {
		return nil, err
	}
	return renewed, nil
}

func processingUpdate(operationID string, lease *operationLease) *tradesettlementv1.UpdateSettlementOperationRequest {
	return &tradesettlementv1.UpdateSettlementOperationRequest{
		OperationId:     operationID,
		State:           tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING,
		LeaseOwner:      lease.owner,
		LeaseGeneration: lease.generation,
		LeaseExpiresAt:  timestamppb.New(lease.expiresAt),
	}
}

func leaseFromOperation(operation *tradesettlementv1.SettlementOperationStatus) *operationLease {
	if operation == nil || operation.GetLeaseOwner() == "" || operation.GetLeaseGeneration() == 0 || operation.GetLeaseExpiresAt() == nil || !operation.GetLeaseExpiresAt().IsValid() {
		return nil
	}
	return &operationLease{
		owner:      operation.GetLeaseOwner(),
		generation: operation.GetLeaseGeneration(),
		expiresAt:  operation.GetLeaseExpiresAt().AsTime(),
	}
}

func (s *Service) terminalizeInvalidWork(ctx context.Context, work *settlement.Work, cause error) error {
	return s.terminalizeWork(ctx, work, "INVALID_WORK_MESSAGE", cause.Error())
}

func (s *Service) terminalizeWork(ctx context.Context, work *settlement.Work, code, description string) error {
	if work == nil || work.OperationID == "" {
		return nil
	}
	operation, err := s.executor.GetSettlementOperation(ctx, work.OperationID)
	if err != nil {
		return fmt.Errorf("load settlement operation %s: %w", work.OperationID, err)
	}
	if isTerminalOperation(operation.GetState()) {
		if operation.GetResultPublished() {
			return nil
		}
		return s.publishDurableResult(ctx, work, operation, leaseFromOperation(operation))
	}
	operation, err = s.executor.UpdateSettlementOperation(ctx, &tradesettlementv1.UpdateSettlementOperationRequest{
		OperationId:        work.OperationID,
		State:              tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED,
		FailureCode:        code,
		FailureDescription: description,
	})
	if err != nil {
		return fmt.Errorf("persist invalid settlement work failure: %w", err)
	}
	return s.publishDurableResult(ctx, work, operation, nil)
}

func (s *Service) publishDurableResult(ctx context.Context, work *settlement.Work, operation *tradesettlementv1.SettlementOperationStatus, lease *operationLease) error {
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
	update := &tradesettlementv1.UpdateSettlementOperationRequest{
		OperationId:        work.OperationID,
		State:              operation.GetState(),
		SettlementBatchId:  operation.GetSettlementBatchId(),
		FailureCode:        operation.GetFailureCode(),
		FailureDescription: operation.GetFailureDescription(),
		ResultPublished:    true,
	}
	if lease != nil {
		update.LeaseOwner = lease.owner
		update.LeaseGeneration = lease.generation
		update.LeaseExpiresAt = timestamppb.New(lease.expiresAt)
	}
	_, err := s.executor.UpdateSettlementOperation(ctx, update)
	if err != nil {
		return fmt.Errorf("mark settlement result published: %w", err)
	}
	return nil
}

func (s *Service) workerIdentity() string {
	s.stateMu.Lock()
	defer s.stateMu.Unlock()
	if s.workerID != "" {
		return s.workerID
	}
	var random [16]byte
	if _, err := rand.Read(random[:]); err != nil {
		s.workerID = fmt.Sprintf("settlement-worker-%d", time.Now().UnixNano())
	} else {
		s.workerID = "settlement-worker-" + hex.EncodeToString(random[:])
	}
	return s.workerID
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
