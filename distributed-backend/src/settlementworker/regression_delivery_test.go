package settlementworker

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"encore.dev/pubsub"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/testkit"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"github.com/onsi/gomega"
)

type rawSettlementMessageHandler interface {
	HandleSettlementMessage(context.Context, []byte) error
}

type deadLetterHandler interface {
	HandleSettlementDeadLetter(context.Context, *settlement.Work, error) error
}

type leaseTickDrivenWorker interface {
	HandleSettlementWorkWithLeaseTicks(context.Context, *settlement.Work, <-chan time.Time) error
}

type observedLeaseToken struct {
	owner      string
	generation uint64
	expiresAt  time.Time
}

func leaseTokenFromUpdate(request *tradesettlementv1.UpdateSettlementOperationRequest) (observedLeaseToken, error) {
	expires := request.GetLeaseExpiresAt()
	if request.GetLeaseOwner() == "" || request.GetLeaseGeneration() == 0 || expires == nil {
		return observedLeaseToken{}, errors.New("processing update is missing lease owner, generation, or expiry fields")
	}
	if !expires.IsValid() {
		return observedLeaseToken{}, errors.New("processing update has an invalid lease expiry")
	}
	return observedLeaseToken{
		owner:      request.GetLeaseOwner(),
		generation: request.GetLeaseGeneration(),
		expiresAt:  expires.AsTime(),
	}, nil
}

func expectDurableInvalidFailure(t *testing.T, executor *recordingExecutor, results *recordingResultPublisher) {
	t.Helper()
	g := testkit.Expect(t)
	g.Expect(executor.currentOperation().GetState()).To(gomega.Equal(tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED), "invalid work was not made terminal")
	g.Expect(executor.currentOperation().GetFailureCode()).NotTo(gomega.BeEmpty(), "invalid work has no durable failure code")
	g.Expect(results.results).To(gomega.HaveLen(1), "invalid work did not publish one terminal result")
}

func TestCanonicalWorkerDeliveryRegressions(t *testing.T) {
	t.Run("test_malformed_work_message_is_rejected_before_operation_enters_processing", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &recordingExecutor{}
		service := &Service{executor: executor, results: &recordingResultPublisher{}}
		handler, ok := any(service).(rawSettlementMessageHandler)
		g.Expect(ok).To(gomega.BeTrue(), "worker exposes no raw-message validation boundary")
		if ok {
			g.Expect(handler.HandleSettlementMessage(context.Background(), []byte(`{"operation_id":"11111111-1111-4111-8111-111111111111"`))).To(gomega.Succeed())
			g.Expect(executor.currentOperation().GetState()).NotTo(gomega.Equal(tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING))
		}
	})

	t.Run("test_unsupported_work_message_schema_marks_operation_failed", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &recordingExecutor{}
		results := &recordingResultPublisher{}
		service := &Service{executor: executor, results: results}
		handler, ok := any(service).(rawSettlementMessageHandler)
		g.Expect(ok).To(gomega.BeTrue(), "worker exposes no raw-message schema validation boundary")
		if !ok {
			t.FailNow()
		}
		encoded, err := json.Marshal(validSettlementWork())
		g.Expect(err).NotTo(gomega.HaveOccurred())
		var document map[string]any
		g.Expect(json.Unmarshal(encoded, &document)).To(gomega.Succeed())
		document["schema_version"] = "settlement-work.v999"
		encoded, err = json.Marshal(document)
		g.Expect(err).NotTo(gomega.HaveOccurred())
		g.Expect(handler.HandleSettlementMessage(context.Background(), encoded)).To(gomega.Succeed(), "permanently unsupported schema should be terminalized and acknowledged")
		expectDurableInvalidFailure(t, executor, results)
	})

	t.Run("test_invalid_work_message_enum_marks_operation_failed", func(t *testing.T) {
		executor := &recordingExecutor{}
		results := &recordingResultPublisher{}
		service := &Service{executor: executor, results: results}
		work := validSettlementWork()
		work.Intent = "UNRECOGNIZED"
		_ = service.HandleSettlementWork(context.Background(), work)
		expectDurableInvalidFailure(t, executor, results)
	})

	t.Run("test_missing_work_message_identifier_marks_operation_failed", func(t *testing.T) {
		executor := &recordingExecutor{}
		results := &recordingResultPublisher{}
		service := &Service{executor: executor, results: results}
		work := validSettlementWork()
		work.IdempotencyKey = ""
		_ = service.HandleSettlementWork(context.Background(), work)
		expectDurableInvalidFailure(t, executor, results)
	})

	t.Run("test_invalid_work_message_operations_mark_operation_failed", func(t *testing.T) {
		executor := &recordingExecutor{}
		results := &recordingResultPublisher{}
		service := &Service{executor: executor, results: results}
		work := validSettlementWork()
		work.Operations = []settlement.Operation{{Kind: settlement.OperationCreateNewTradeInstanceRow}}
		_ = service.HandleSettlementWork(context.Background(), work)
		expectDurableInvalidFailure(t, executor, results)
	})

	t.Run("test_permanently_invalid_message_publishes_failed_result", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &recordingExecutor{}
		results := &recordingResultPublisher{}
		service := &Service{executor: executor, results: results}
		work := validSettlementWork()
		work.Operations = []settlement.Operation{{Kind: "not-supported"}}
		g.Expect(service.HandleSettlementWork(context.Background(), work)).To(gomega.Succeed(), "permanent validation failure should be terminalized and acknowledged")
		expectDurableInvalidFailure(t, executor, results)
	})

	t.Run("test_permanently_invalid_message_is_not_retried_forever", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &recordingExecutor{}
		results := &recordingResultPublisher{}
		service := &Service{executor: executor, results: results}
		work := validSettlementWork()
		work.Operations = []settlement.Operation{{Kind: "not-supported"}}
		g.Expect(service.HandleSettlementWork(context.Background(), work)).To(gomega.Succeed())
		g.Expect(service.HandleSettlementWork(context.Background(), work)).To(gomega.Succeed())
		g.Expect(executor.updates).To(gomega.HaveLen(2), "duplicate invalid delivery repeated nonterminal processing")
		g.Expect(results.results).To(gomega.HaveLen(1))
	})

	t.Run("test_processing_operation_has_expiring_worker_lease", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &recordingExecutor{}
		startedAt := time.Now()
		g.Expect((&Service{executor: executor, results: &recordingResultPublisher{}}).HandleSettlementWork(context.Background(), validSettlementWork())).To(gomega.Succeed())
		g.Expect(executor.updates).NotTo(gomega.BeEmpty(), "worker emitted no durable PROCESSING transition")
		if len(executor.updates) == 0 {
			t.FailNow()
		}
		token, err := leaseTokenFromUpdate(executor.updates[0])
		g.Expect(err).NotTo(gomega.HaveOccurred())
		if err == nil {
			g.Expect(token.owner).NotTo(gomega.BeEmpty(), "PROCESSING lease has no owner")
			g.Expect(token.generation).To(gomega.BeNumerically(">", 0), "PROCESSING lease generation is not positive")
			g.Expect(token.expiresAt).To(gomega.BeTemporally(">", startedAt), "PROCESSING lease was already expired when persisted")
		}
	})

	t.Run("test_processing_operation_lease_is_renewed_during_execution", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := newRenewalObservingExecutor()
		service := &Service{executor: executor, results: &recordingResultPublisher{}}
		worker, ok := any(service).(leaseTickDrivenWorker)
		g.Expect(ok).To(gomega.BeTrue(), "worker lease renewal has no deterministic clock boundary")
		if !ok {
			t.FailNow()
		}
		clock := testkit.NewManualClock(time.Unix(1_700_000_000, 0))
		done := make(chan error, 1)
		go func() {
			done <- worker.HandleSettlementWorkWithLeaseTicks(context.Background(), validSettlementWork(), clock.After(time.Minute))
		}()
		g.Eventually(executor.executing).WithTimeout(time.Second).Should(gomega.Receive(), "settlement execution never reached the deterministic renewal barrier")
		clock.Advance(time.Minute)
		g.Eventually(done).WithTimeout(time.Second).Should(gomega.Receive(gomega.Succeed()), "settlement did not finish after its lease-renewal tick")
		tokens := executor.leaseTokens()
		g.Expect(len(tokens)).To(gomega.BeNumerically(">=", 2), "long settlement completed without an observed lease renewal")
		if len(tokens) >= 2 {
			g.Expect(tokens[1].owner).To(gomega.Equal(tokens[0].owner))
			g.Expect(tokens[1].generation).To(gomega.BeNumerically(">", tokens[0].generation))
			g.Expect(tokens[1].expiresAt).To(gomega.BeTemporally(">", tokens[0].expiresAt))
		}
	})

	t.Run("test_stale_processing_operation_is_recovered_after_worker_crash", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &recordingExecutor{operation: &tradesettlementv1.SettlementOperationStatus{
			OperationId: "11111111-1111-4111-8111-111111111111",
			State:       tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING,
		}}
		results := &recordingResultPublisher{}
		service := &Service{executor: executor, results: results}
		g.Expect(service.HandleSettlementWork(context.Background(), validSettlementWork())).To(gomega.Succeed())
		g.Expect(executor.requests).To(gomega.HaveLen(1))
		g.Expect(executor.currentOperation().GetState()).To(gomega.Equal(tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_SUCCEEDED))
	})

	t.Run("test_stale_processing_operation_is_not_left_processing_forever", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &recordingExecutor{operation: &tradesettlementv1.SettlementOperationStatus{
			OperationId: "11111111-1111-4111-8111-111111111111",
			State:       tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING,
		}}
		service := &Service{executor: executor, results: &recordingResultPublisher{}}
		g.Expect(service.HandleSettlementWork(context.Background(), validSettlementWork())).To(gomega.Succeed())
		g.Expect(executor.currentOperation().GetState()).NotTo(gomega.Equal(tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING))
	})

	t.Run("test_only_current_lease_owner_can_complete_operation", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &leaseStealingExecutor{}
		results := &recordingResultPublisher{}
		err := (&Service{executor: executor, results: results}).HandleSettlementWork(context.Background(), validSettlementWork())
		g.Expect(executor.processingHadToken).To(gomega.BeTrue(), "worker entered execution without a durable lease token")
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("stale lease")))
		g.Expect(executor.terminalAccepted).To(gomega.BeFalse(), "stale worker completed an operation owned by a newer lease generation")
		g.Expect(results.results).To(gomega.BeEmpty(), "stale worker published a terminal result")
	})

	t.Run("test_duplicate_worker_delivery_does_not_execute_settlement_concurrently", func(t *testing.T) {
		assertDuplicateDeliveryIsSerialized(t)
	})

	t.Run("test_dead_lettered_message_marks_operation_terminal", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &recordingExecutor{}
		service := &Service{executor: executor, results: &recordingResultPublisher{}}
		handler, ok := any(service).(deadLetterHandler)
		g.Expect(ok).To(gomega.BeTrue(), "worker exposes no dead-letter terminalization boundary")
		if ok {
			g.Expect(handler.HandleSettlementDeadLetter(context.Background(), validSettlementWork(), errors.New("retry budget exhausted"))).To(gomega.Succeed())
			g.Expect(executor.currentOperation().GetState()).To(gomega.Equal(tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED))
		}
	})

	t.Run("test_dead_lettered_message_preserves_failure_reason", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &recordingExecutor{}
		service := &Service{executor: executor, results: &recordingResultPublisher{}}
		handler, ok := any(service).(deadLetterHandler)
		g.Expect(ok).To(gomega.BeTrue(), "worker exposes no dead-letter failure-reason boundary")
		if ok {
			g.Expect(handler.HandleSettlementDeadLetter(context.Background(), validSettlementWork(), errors.New("retry budget exhausted"))).To(gomega.Succeed())
			g.Expect(executor.currentOperation().GetFailureDescription()).To(gomega.ContainSubstring("retry budget exhausted"))
		}
	})
}

type renewalObservingExecutor struct {
	mu        sync.Mutex
	operation *tradesettlementv1.SettlementOperationStatus
	tokens    []observedLeaseToken
	renewed   chan struct{}
	executing chan struct{}
	once      sync.Once
	startOnce sync.Once
}

func newRenewalObservingExecutor() *renewalObservingExecutor {
	return &renewalObservingExecutor{
		operation: &tradesettlementv1.SettlementOperationStatus{
			OperationId: "11111111-1111-4111-8111-111111111111",
			State:       tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_QUEUED,
		},
		renewed:   make(chan struct{}),
		executing: make(chan struct{}, 1),
	}
}

func (executor *renewalObservingExecutor) GetSettlementOperation(context.Context, string) (*tradesettlementv1.SettlementOperationStatus, error) {
	executor.mu.Lock()
	defer executor.mu.Unlock()
	return executor.operation, nil
}

func (executor *renewalObservingExecutor) UpdateSettlementOperation(_ context.Context, request *tradesettlementv1.UpdateSettlementOperationRequest) (*tradesettlementv1.SettlementOperationStatus, error) {
	executor.mu.Lock()
	defer executor.mu.Unlock()
	if request.GetState() == tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING {
		token, err := leaseTokenFromUpdate(request)
		if err != nil {
			return nil, err
		}
		executor.tokens = append(executor.tokens, token)
		if len(executor.tokens) >= 2 {
			executor.once.Do(func() { close(executor.renewed) })
		}
	}
	executor.operation.State = request.GetState()
	executor.operation.SettlementBatchId = request.GetSettlementBatchId()
	executor.operation.ResultPublished = executor.operation.ResultPublished || request.GetResultPublished()
	return executor.operation, nil
}

func (executor *renewalObservingExecutor) ExecuteSettlementBatch(ctx context.Context, _ *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	executor.startOnce.Do(func() { executor.executing <- struct{}{} })
	select {
	case <-executor.renewed:
		return &tradesettlementv1.ExecuteSettlementBatchResponse{SettlementBatchId: "renewed-batch"}, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (*renewalObservingExecutor) Ping(context.Context) error { return nil }

func (executor *renewalObservingExecutor) leaseTokens() []observedLeaseToken {
	executor.mu.Lock()
	defer executor.mu.Unlock()
	return append([]observedLeaseToken(nil), executor.tokens...)
}

type leaseStealingExecutor struct {
	mu                 sync.Mutex
	operation          *tradesettlementv1.SettlementOperationStatus
	current            observedLeaseToken
	processingHadToken bool
	terminalAccepted   bool
}

func (executor *leaseStealingExecutor) GetSettlementOperation(context.Context, string) (*tradesettlementv1.SettlementOperationStatus, error) {
	executor.mu.Lock()
	defer executor.mu.Unlock()
	if executor.operation == nil {
		executor.operation = &tradesettlementv1.SettlementOperationStatus{
			OperationId: "11111111-1111-4111-8111-111111111111",
			State:       tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_QUEUED,
		}
	}
	return executor.operation, nil
}

func (executor *leaseStealingExecutor) UpdateSettlementOperation(_ context.Context, request *tradesettlementv1.UpdateSettlementOperationRequest) (*tradesettlementv1.SettlementOperationStatus, error) {
	executor.mu.Lock()
	defer executor.mu.Unlock()
	token, err := leaseTokenFromUpdate(request)
	if err != nil {
		return nil, err
	}
	if request.GetState() == tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING {
		executor.processingHadToken = true
		executor.current = token
		executor.operation.State = request.GetState()
		return executor.operation, nil
	}
	if token.owner != executor.current.owner || token.generation != executor.current.generation {
		return nil, errors.New("stale lease generation cannot complete operation")
	}
	executor.terminalAccepted = true
	executor.operation.State = request.GetState()
	return executor.operation, nil
}

func (executor *leaseStealingExecutor) ExecuteSettlementBatch(context.Context, *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	executor.mu.Lock()
	executor.current.owner = "replacement-worker"
	executor.current.generation++
	executor.mu.Unlock()
	return &tradesettlementv1.ExecuteSettlementBatchResponse{SettlementBatchId: "stale-batch"}, nil
}

func (*leaseStealingExecutor) Ping(context.Context) error { return nil }

type concurrentExecutor struct {
	mu        sync.Mutex
	operation *tradesettlementv1.SettlementOperationStatus
	started   chan struct{}
	release   chan struct{}
	getCalls  chan struct{}
	active    atomic.Int32
	maximum   atomic.Int32
	executed  atomic.Int32
}

func newConcurrentExecutor() *concurrentExecutor {
	return &concurrentExecutor{
		operation: &tradesettlementv1.SettlementOperationStatus{
			OperationId: "11111111-1111-4111-8111-111111111111",
			State:       tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_QUEUED,
		},
		started:  make(chan struct{}, 2),
		release:  make(chan struct{}),
		getCalls: make(chan struct{}, 2),
	}
}

func (executor *concurrentExecutor) GetSettlementOperation(context.Context, string) (*tradesettlementv1.SettlementOperationStatus, error) {
	executor.getCalls <- struct{}{}
	executor.mu.Lock()
	defer executor.mu.Unlock()
	return executor.operation, nil
}

func (executor *concurrentExecutor) UpdateSettlementOperation(_ context.Context, request *tradesettlementv1.UpdateSettlementOperationRequest) (*tradesettlementv1.SettlementOperationStatus, error) {
	executor.mu.Lock()
	defer executor.mu.Unlock()
	executor.operation.State = request.GetState()
	executor.operation.SettlementBatchId = request.GetSettlementBatchId()
	executor.operation.ResultPublished = executor.operation.ResultPublished || request.GetResultPublished()
	if request.GetLeaseOwner() != "" {
		executor.operation.LeaseOwner = request.GetLeaseOwner()
		executor.operation.LeaseGeneration = request.GetLeaseGeneration()
		executor.operation.LeaseExpiresAt = request.GetLeaseExpiresAt()
	}
	return executor.operation, nil
}

func (executor *concurrentExecutor) ExecuteSettlementBatch(context.Context, *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	executor.executed.Add(1)
	active := executor.active.Add(1)
	for active > executor.maximum.Load() && !executor.maximum.CompareAndSwap(executor.maximum.Load(), active) {
	}
	executor.started <- struct{}{}
	<-executor.release
	executor.active.Add(-1)
	return &tradesettlementv1.ExecuteSettlementBatchResponse{SettlementBatchId: "batch"}, nil
}

func (*concurrentExecutor) Ping(context.Context) error { return nil }

type synchronizedPublisher struct {
	mu      sync.Mutex
	results []*settlement.Result
}

func (publisher *synchronizedPublisher) Publish(_ context.Context, result *settlement.Result) (string, error) {
	publisher.mu.Lock()
	defer publisher.mu.Unlock()
	publisher.results = append(publisher.results, result)
	return "result", nil
}

func (*synchronizedPublisher) Meta() pubsub.TopicMeta { return pubsub.TopicMeta{} }

func assertDuplicateDeliveryIsSerialized(t *testing.T) {
	t.Helper()
	g := testkit.Expect(t)
	executor := newConcurrentExecutor()
	service := &Service{executor: executor, results: &synchronizedPublisher{}}
	done := make(chan error, 2)
	go func() { done <- service.HandleSettlementWork(context.Background(), validSettlementWork()) }()
	g.Eventually(executor.getCalls).WithTimeout(time.Second).Should(gomega.Receive())
	g.Eventually(executor.started).WithTimeout(time.Second).Should(gomega.Receive())
	go func() { done <- service.HandleSettlementWork(context.Background(), validSettlementWork()) }()
	g.Eventually(executor.getCalls).WithTimeout(time.Second).Should(gomega.Receive())
	duplicateDone := false
	duplicateExecuted := false
	decisionDeadline := time.NewTimer(250 * time.Millisecond)
	select {
	case <-executor.started:
		duplicateExecuted = true
	case <-done:
		duplicateDone = true
	case <-decisionDeadline.C:
		t.Fatal("duplicate delivery neither exited nor entered the executor after loading the active operation")
	}
	if !decisionDeadline.Stop() {
		select {
		case <-decisionDeadline.C:
		default:
		}
	}
	g.Expect(duplicateExecuted).To(gomega.BeFalse(), "duplicate delivery entered the Rust settlement boundary while the current lease was active")
	g.Expect(executor.executed.Load()).To(gomega.Equal(int32(1)))
	close(executor.release)
	g.Eventually(done).WithTimeout(time.Second).Should(gomega.Receive())
	if !duplicateDone {
		g.Eventually(done).WithTimeout(time.Second).Should(gomega.Receive())
	}
	g.Expect(executor.maximum.Load()).To(gomega.Equal(int32(1)), "maximum concurrent execution for one operation exceeded one")
}
