package settlementworker

import (
	"context"
	"errors"
	"testing"

	"encore.dev/pubsub"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type recordingExecutor struct {
	requests  []*tradesettlementv1.ExecuteSettlementBatchRequest
	updates   []*tradesettlementv1.UpdateSettlementOperationRequest
	operation *tradesettlementv1.SettlementOperationStatus
	err       error
	updateErr error
}

func (e *recordingExecutor) currentOperation() *tradesettlementv1.SettlementOperationStatus {
	if e.operation == nil {
		e.operation = &tradesettlementv1.SettlementOperationStatus{
			OperationId: "11111111-1111-4111-8111-111111111111",
			State:       tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_QUEUED,
		}
	}
	return e.operation
}

func (e *recordingExecutor) GetSettlementOperation(context.Context, string) (*tradesettlementv1.SettlementOperationStatus, error) {
	return e.currentOperation(), nil
}

func (e *recordingExecutor) UpdateSettlementOperation(_ context.Context, request *tradesettlementv1.UpdateSettlementOperationRequest) (*tradesettlementv1.SettlementOperationStatus, error) {
	e.updates = append(e.updates, request)
	if request.ResultPublished && e.updateErr != nil {
		return nil, e.updateErr
	}
	operation := e.currentOperation()
	operation.State = request.State
	if request.SettlementBatchId != "" {
		operation.SettlementBatchId = request.SettlementBatchId
	}
	operation.FailureCode = request.FailureCode
	operation.FailureDescription = request.FailureDescription
	operation.ResultPublished = operation.ResultPublished || request.ResultPublished
	return operation, nil
}

func (e *recordingExecutor) ExecuteSettlementBatch(ctx context.Context, request *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	e.requests = append(e.requests, request)
	if e.err != nil {
		return nil, e.err
	}
	return &tradesettlementv1.ExecuteSettlementBatchResponse{
		SettlementBatchId: "settlement-batch",
		IdempotencyKey:    request.IdempotencyKey,
		BatchState:        "COMPLETED",
	}, nil
}

func (e *recordingExecutor) Ping(context.Context) error {
	return nil
}

type recordingResultPublisher struct {
	results []*settlement.Result
	err     error
}

func (p *recordingResultPublisher) Publish(ctx context.Context, result *settlement.Result) (string, error) {
	if p.err != nil {
		return "", p.err
	}
	p.results = append(p.results, result)
	return "result-message", nil
}

func validSettlementWork() *settlement.Work {
	return &settlement.Work{
		OperationID:         "11111111-1111-4111-8111-111111111111",
		Intent:              settlement.IntentIssue,
		IdempotencyKey:      "issue-1",
		RequestFingerprint:  "market.issue_trade_instance.sha256:fingerprint",
		ExternalRequestID:   "external-1",
		CausedByCapsuleerID: 1001,
		CreatedByService:    settlement.CreatedByMarket,
		Operations: []settlement.Operation{
			{
				Kind: settlement.OperationCreateNewTradeInstanceRow,
				CreateNewTradeInstanceRow: &settlement.CreateNewTradeInstanceRow{
					TradeInstanceID: "22222222-2222-4222-8222-222222222222",
					TradeKind:       "SELL",
					TradeState:      "OPEN",
					IssuerID:        1001,
					ItemTypeID:      34,
					StationID:       60003760,
					TotalQuantity:   4,
					UnitPriceISK:    25,
				},
			},
		},
	}
}

func (p *recordingResultPublisher) Meta() pubsub.TopicMeta {
	return pubsub.TopicMeta{}
}

func TestHandleSettlementWorkCallsRustBoundaryAndPublishesResult(t *testing.T) {
	executor := &recordingExecutor{}
	results := &recordingResultPublisher{}
	service := &Service{executor: executor, results: results}

	err := service.HandleSettlementWork(context.Background(), validSettlementWork())
	if err != nil {
		t.Fatalf("HandleSettlementWork returned error: %v", err)
	}
	if len(executor.requests) != 1 {
		t.Fatalf("executor requests = %d, want 1", len(executor.requests))
	}
	request := executor.requests[0]
	if request.IdempotencyKey != "issue-1" || request.CausedByCapsuleerId == nil || *request.CausedByCapsuleerId != 1001 {
		t.Fatalf("request idempotency/actor not preserved: %+v", request)
	}
	if request.RequestFingerprint != "" {
		t.Fatalf("executor request reused Market fingerprint %q", request.RequestFingerprint)
	}
	if len(request.Operations) != 1 {
		t.Fatalf("operations = %d, want 1", len(request.Operations))
	}
	if len(results.results) != 1 || results.results[0].SettlementBatchID != "settlement-batch" {
		t.Fatalf("published results = %+v, want settlement-batch", results.results)
	}
	if len(executor.updates) != 3 || !executor.currentOperation().ResultPublished {
		t.Fatalf("operation updates = %+v, want processing, succeeded, published", executor.updates)
	}
}

func TestHandleSettlementWorkRetriesOnlyResultDeliveryAfterSettlementCommit(t *testing.T) {
	executor := &recordingExecutor{}
	results := &recordingResultPublisher{err: errors.New("result topic unavailable")}
	service := &Service{executor: executor, results: results}

	if err := service.HandleSettlementWork(context.Background(), validSettlementWork()); err == nil {
		t.Fatal("result publication failure was acknowledged")
	}
	if len(executor.requests) != 1 {
		t.Fatalf("settlement executions = %d, want 1", len(executor.requests))
	}
	results.err = nil
	if err := service.HandleSettlementWork(context.Background(), validSettlementWork()); err != nil {
		t.Fatalf("delivery retry returned error: %v", err)
	}
	if len(executor.requests) != 1 {
		t.Fatalf("settlement re-executed after commit: calls = %d", len(executor.requests))
	}
	if len(results.results) != 1 || !executor.currentOperation().ResultPublished {
		t.Fatalf("result delivery did not complete: results=%+v operation=%+v", results.results, executor.currentOperation())
	}
}

func TestHandleSettlementWorkPublishesPermanentFailure(t *testing.T) {
	executor := &recordingExecutor{err: status.Error(codes.PermissionDenied, "unauthorized actor")}
	results := &recordingResultPublisher{}
	service := &Service{executor: executor, results: results}

	if err := service.HandleSettlementWork(context.Background(), validSettlementWork()); err != nil {
		t.Fatalf("permanent failure was not projected: %v", err)
	}
	if executor.currentOperation().GetState() != tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED {
		t.Fatalf("operation state = %s, want FAILED", executor.currentOperation().GetState())
	}
	if len(results.results) != 1 || results.results[0].FailureCode != codes.PermissionDenied.String() {
		t.Fatalf("failure result = %+v", results.results)
	}
}

func TestHandleSettlementWorkRecoversCommittedUnpublishedOperationWithoutExecution(t *testing.T) {
	executor := &recordingExecutor{operation: &tradesettlementv1.SettlementOperationStatus{
		OperationId:       "11111111-1111-4111-8111-111111111111",
		State:             tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_SUCCEEDED,
		SettlementBatchId: "22222222-2222-4222-8222-222222222222",
	}}
	results := &recordingResultPublisher{}
	service := &Service{executor: executor, results: results}

	if err := service.HandleSettlementWork(context.Background(), validSettlementWork()); err != nil {
		t.Fatalf("committed operation recovery returned error: %v", err)
	}
	if len(executor.requests) != 0 {
		t.Fatalf("committed settlement was re-executed: calls = %d", len(executor.requests))
	}
	if len(results.results) != 1 || !executor.currentOperation().ResultPublished {
		t.Fatalf("pending result was not published: results=%+v operation=%+v", results.results, executor.currentOperation())
	}
}

func TestHandleSettlementWorkAcknowledgesDuplicateAfterResultPublished(t *testing.T) {
	executor := &recordingExecutor{operation: &tradesettlementv1.SettlementOperationStatus{
		OperationId:       "11111111-1111-4111-8111-111111111111",
		State:             tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_SUCCEEDED,
		SettlementBatchId: "22222222-2222-4222-8222-222222222222",
		ResultPublished:   true,
	}}
	results := &recordingResultPublisher{}
	service := &Service{executor: executor, results: results}

	if err := service.HandleSettlementWork(context.Background(), validSettlementWork()); err != nil {
		t.Fatalf("duplicate work returned error: %v", err)
	}
	if len(executor.requests) != 0 || len(results.results) != 0 || len(executor.updates) != 0 {
		t.Fatalf("published terminal work performed side effects: executor=%d results=%d updates=%d", len(executor.requests), len(results.results), len(executor.updates))
	}
}

func TestHandleSettlementWorkRecoversCrashAfterResultPublication(t *testing.T) {
	executor := &recordingExecutor{
		operation: &tradesettlementv1.SettlementOperationStatus{
			OperationId:       "11111111-1111-4111-8111-111111111111",
			State:             tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_SUCCEEDED,
			SettlementBatchId: "22222222-2222-4222-8222-222222222222",
		},
		updateErr: errors.New("status update unavailable"),
	}
	results := &recordingResultPublisher{}
	service := &Service{executor: executor, results: results}

	if err := service.HandleSettlementWork(context.Background(), validSettlementWork()); err == nil {
		t.Fatal("result publication without durable published marker was acknowledged")
	}
	executor.updateErr = nil
	if err := service.HandleSettlementWork(context.Background(), validSettlementWork()); err != nil {
		t.Fatalf("result recovery retry returned error: %v", err)
	}
	if len(executor.requests) != 0 {
		t.Fatalf("settlement executed during result recovery: calls = %d", len(executor.requests))
	}
	if len(results.results) != 2 || !executor.currentOperation().ResultPublished {
		t.Fatalf("at-least-once result recovery did not converge: results=%d operation=%+v", len(results.results), executor.currentOperation())
	}
}

func TestHandleSettlementWorkRejectsInvalidOperationForRetry(t *testing.T) {
	service := &Service{executor: &recordingExecutor{}, results: &recordingResultPublisher{}}
	err := service.HandleSettlementWork(context.Background(), &settlement.Work{
		OperationID:      "11111111-1111-4111-8111-111111111111",
		Intent:           settlement.IntentIssue,
		IdempotencyKey:   "bad-work",
		CreatedByService: settlement.CreatedByMarket,
		Operations: []settlement.Operation{
			{Kind: settlement.OperationCreateNewTradeInstanceRow},
		},
	})
	if err == nil {
		t.Fatal("expected invalid work to return an error")
	}
}
