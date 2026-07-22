package settlementworker

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/QuasarRay/eve-trade/distributed-backend/internal/testkit"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"github.com/onsi/gomega"
)

type ackExtendingWorker interface {
	HandleSettlementWorkWithAckExtender(context.Context, *settlement.Work, <-chan time.Time, func(context.Context, string) error) error
}

type subscriptionAwareReadiness interface {
	SettlementWorkerReadyWithSubscriptionCheck(context.Context, func(context.Context) error) (*HealthResponse, error)
}

type initializationAwareStartup interface {
	SettlementWorkerStartupForState(context.Context, func() bool) (*HealthResponse, error)
}

type dependencyExecutor struct {
	operationErr error
	pingErr      error
}

func (executor *dependencyExecutor) ExecuteSettlementBatch(context.Context, *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	return &tradesettlementv1.ExecuteSettlementBatchResponse{SettlementBatchId: "batch"}, nil
}

func (executor *dependencyExecutor) GetSettlementOperation(context.Context, string) (*tradesettlementv1.SettlementOperationStatus, error) {
	if executor.operationErr != nil {
		return nil, executor.operationErr
	}
	return &tradesettlementv1.SettlementOperationStatus{OperationId: "11111111-1111-4111-8111-111111111111"}, nil
}

func (executor *dependencyExecutor) UpdateSettlementOperation(context.Context, *tradesettlementv1.UpdateSettlementOperationRequest) (*tradesettlementv1.SettlementOperationStatus, error) {
	return &tradesettlementv1.SettlementOperationStatus{}, nil
}

func (executor *dependencyExecutor) Ping(context.Context) error { return executor.pingErr }

func (executor *dependencyExecutor) OperationStoreReady(context.Context) error {
	return executor.operationErr
}

func TestCanonicalWorkerLifecycleRegressions(t *testing.T) {
	t.Run("test_worker_ack_deadline_exceeds_maximum_execution_timeout", func(t *testing.T) {
		g := testkit.Expect(t)
		t.Setenv("SETTLEMENT_WORKER_REQUEST_TIMEOUT", "10s")
		config := LoadConfig()
		g.Expect(settlementWorkerAckDeadline).To(gomega.BeNumerically(">", config.RequestTimeout), "ack deadline must exceed the maximum Rust execution timeout")
	})

	t.Run("test_worker_extends_ack_deadline_during_long_execution", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := newConcurrentExecutor()
		service := &Service{executor: executor, results: &synchronizedPublisher{}}
		extender, ok := any(service).(ackExtendingWorker)
		g.Expect(ok).To(gomega.BeTrue(), "long-running settlement has no acknowledgement extension mechanism")
		if !ok {
			t.FailNow()
		}
		clock := testkit.NewManualClock(time.Unix(1_700_000_000, 0))
		extended := make(chan string, 1)
		done := make(chan error, 1)
		go func() {
			done <- extender.HandleSettlementWorkWithAckExtender(
				context.Background(),
				validSettlementWork(),
				clock.After(time.Minute),
				func(_ context.Context, operationID string) error {
					extended <- operationID
					return nil
				},
			)
		}()
		g.Eventually(executor.started).WithTimeout(time.Second).Should(gomega.Receive(), "settlement execution never reached the ack-extension barrier")
		clock.Advance(time.Minute)
		g.Eventually(extended).WithTimeout(time.Second).Should(gomega.Receive(gomega.Equal(validSettlementWork().OperationID)), "ack deadline was not extended while settlement remained active")
		select {
		case err := <-done:
			t.Fatalf("settlement finished before the blocked executor was released: %v", err)
		default:
		}
		close(executor.release)
		g.Eventually(done).WithTimeout(time.Second).Should(gomega.Receive(gomega.Succeed()))
	})

	t.Run("test_long_running_settlement_is_not_redelivered_while_lease_is_active", func(t *testing.T) {
		assertDuplicateDeliveryIsSerialized(t)
	})

	t.Run("test_redelivered_message_exits_when_another_worker_owns_valid_lease", func(t *testing.T) {
		assertDuplicateDeliveryIsSerialized(t)
	})

	t.Run("test_duplicate_delivery_does_not_publish_duplicate_terminal_result", func(t *testing.T) {
		g := testkit.Expect(t)
		executor := &recordingExecutor{}
		results := &recordingResultPublisher{}
		service := &Service{executor: executor, results: results}
		g.Expect(service.HandleSettlementWork(context.Background(), validSettlementWork())).To(gomega.Succeed())
		g.Expect(service.HandleSettlementWork(context.Background(), validSettlementWork())).To(gomega.Succeed())
		g.Expect(results.results).To(gomega.HaveLen(1))
		g.Expect(executor.requests).To(gomega.HaveLen(1))
	})

	t.Run("test_worker_startup_rejects_invalid_ack_deadline_timeout_relationship", func(t *testing.T) {
		g := testkit.Expect(t)
		t.Setenv("SETTLEMENT_WORKER_REQUEST_TIMEOUT", "31s")
		service, err := initService()
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("ack deadline")), "startup returned service=%v for a request timeout exceeding the ack deadline", service)
	})

	t.Run("test_settlement_worker_readiness_fails_when_operation_store_is_unavailable", func(t *testing.T) {
		g := testkit.Expect(t)
		service := &Service{executor: &dependencyExecutor{operationErr: errors.New("operation store unavailable")}, results: &recordingResultPublisher{}}
		_, err := service.SettlementWorkerReady(context.Background())
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("operation store")))
	})

	t.Run("test_settlement_worker_readiness_fails_when_executor_is_unavailable", func(t *testing.T) {
		g := testkit.Expect(t)
		service := &Service{executor: &dependencyExecutor{pingErr: errors.New("executor unavailable")}, results: &recordingResultPublisher{}}
		_, err := service.SettlementWorkerReady(context.Background())
		g.Expect(err).To(gomega.HaveOccurred())
	})

	t.Run("test_settlement_worker_readiness_fails_when_result_publisher_is_unavailable", func(t *testing.T) {
		g := testkit.Expect(t)
		service := &Service{executor: &dependencyExecutor{}, results: &recordingResultPublisher{err: errors.New("result publisher unavailable")}}
		_, err := service.SettlementWorkerReady(context.Background())
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("result publisher")))
	})

	t.Run("test_settlement_worker_readiness_fails_when_subscription_is_unavailable", func(t *testing.T) {
		g := testkit.Expect(t)
		service := &Service{executor: &dependencyExecutor{}, results: &recordingResultPublisher{}}
		dependency, ok := any(service).(subscriptionAwareReadiness)
		g.Expect(ok).To(gomega.BeTrue(), "worker readiness has no injectable subscription-health boundary")
		if ok {
			probeErr := errors.New("subscription unavailable")
			response, err := dependency.SettlementWorkerReadyWithSubscriptionCheck(context.Background(), func(context.Context) error {
				return probeErr
			})
			g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("subscription unavailable")), "subscription failure was not propagated by readiness")
			g.Expect(response).To(gomega.BeNil(), "readiness returned a successful response while the subscription was unavailable")
		}
	})

	t.Run("test_settlement_worker_readiness_passes_only_when_all_required_dependencies_are_available", func(t *testing.T) {
		g := testkit.Expect(t)
		service := &Service{executor: &dependencyExecutor{}, results: &recordingResultPublisher{}}
		response, err := service.SettlementWorkerReady(context.Background())
		g.Expect(err).NotTo(gomega.HaveOccurred())
		g.Expect(response.Status).To(gomega.Equal("ready"))
	})

	t.Run("test_settlement_worker_liveness_does_not_depend_on_external_dependency_health", func(t *testing.T) {
		g := testkit.Expect(t)
		service := &Service{executor: &dependencyExecutor{pingErr: errors.New("executor unavailable")}, results: &recordingResultPublisher{}}
		response, err := service.SettlementWorkerHealth(context.Background())
		g.Expect(err).NotTo(gomega.HaveOccurred())
		g.Expect(response.Status).To(gomega.Equal("ok"))
	})

	t.Run("test_settlement_worker_startup_probe_waits_for_initialization", func(t *testing.T) {
		g := testkit.Expect(t)
		service := &Service{executor: &dependencyExecutor{}, results: &recordingResultPublisher{}}
		probe, ok := any(service).(initializationAwareStartup)
		g.Expect(ok).To(gomega.BeTrue(), "worker exposes no startup probe distinct from liveness and readiness")
		if ok {
			initialized := false
			response, err := probe.SettlementWorkerStartupForState(context.Background(), func() bool { return initialized })
			g.Expect(err).To(gomega.HaveOccurred(), "startup passed before service initialization")
			g.Expect(response).To(gomega.BeNil())
			initialized = true
			response, err = probe.SettlementWorkerStartupForState(context.Background(), func() bool { return initialized })
			g.Expect(err).NotTo(gomega.HaveOccurred())
			g.Expect(response.Status).To(gomega.Equal("started"))
		}
	})
}
