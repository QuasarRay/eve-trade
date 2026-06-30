package rabbitmqsettlement

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
)

type fakeSettlementExecutor struct{}

func (fakeSettlementExecutor) ExecuteSettlementBatch(context.Context, *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	return &tradesettlementv1.ExecuteSettlementBatchResponse{}, nil
}

func (fakeSettlementExecutor) Ping(context.Context) error { return nil }

type fakeReadinessExecutor struct {
	fakeSettlementExecutor
	failuresBeforeReady int
	calls               int
	err                 error
}

func (e *fakeReadinessExecutor) Ping(context.Context) error {
	e.calls++
	if e.calls <= e.failuresBeforeReady {
		if e.err != nil {
			return e.err
		}
		return errors.New("not ready")
	}
	return nil
}

type alwaysFailingReadinessExecutor struct {
	fakeSettlementExecutor
	calls int
	err   error
}

func (e *alwaysFailingReadinessExecutor) Ping(context.Context) error {
	e.calls++
	return e.err
}

func TestWaitForExecutorReadyRetriesUntilPingSucceeds(t *testing.T) {
	executor := &fakeReadinessExecutor{failuresBeforeReady: 2}

	err := waitForExecutorReady(context.Background(), executor, time.Second, time.Millisecond)
	if err != nil {
		t.Fatalf("waitForExecutorReady returned error: %v", err)
	}
	if executor.calls != 3 {
		t.Fatalf("Ping calls = %d, want 3", executor.calls)
	}
}

func TestWaitForExecutorReadyReturnsLastPingErrorOnTimeout(t *testing.T) {
	executor := &alwaysFailingReadinessExecutor{err: errors.New("connection refused")}

	err := waitForExecutorReady(context.Background(), executor, 5*time.Millisecond, time.Millisecond)
	if err == nil {
		t.Fatal("waitForExecutorReady returned nil, want error")
	}
	if !strings.Contains(err.Error(), "connection refused") {
		t.Fatalf("error = %v, want last ping error", err)
	}
	if executor.calls == 0 {
		t.Fatal("Ping was not called")
	}
}

func TestWaitForExecutorReadyRejectsNilDependency(t *testing.T) {
	err := waitForExecutorReady(context.Background(), nil, time.Second, time.Millisecond)
	if err == nil || err.Error() != "settlement executor is required" {
		t.Fatalf("nil executor error = %v, want exact required-dependency error", err)
	}
}
