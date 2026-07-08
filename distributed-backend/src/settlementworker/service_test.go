package settlementworker

import (
	"context"
	"testing"

	"encore.dev/pubsub"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
)

type recordingExecutor struct {
	requests []*tradesettlementv1.ExecuteSettlementBatchRequest
	err      error
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
}

func (p *recordingResultPublisher) Publish(ctx context.Context, result *settlement.Result) (string, error) {
	p.results = append(p.results, result)
	return "result-message", nil
}

func (p *recordingResultPublisher) Meta() pubsub.TopicMeta {
	return pubsub.TopicMeta{}
}

func TestHandleSettlementWorkCallsRustBoundaryAndPublishesResult(t *testing.T) {
	executor := &recordingExecutor{}
	results := &recordingResultPublisher{}
	service := &Service{executor: executor, results: results}

	err := service.HandleSettlementWork(context.Background(), &settlement.Work{
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
	})
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
	if len(request.Operations) != 1 {
		t.Fatalf("operations = %d, want 1", len(request.Operations))
	}
	if len(results.results) != 1 || results.results[0].SettlementBatchID != "settlement-batch" {
		t.Fatalf("published results = %+v, want settlement-batch", results.results)
	}
}

func TestHandleSettlementWorkRejectsInvalidOperationForRetry(t *testing.T) {
	service := &Service{executor: &recordingExecutor{}, results: &recordingResultPublisher{}}
	err := service.HandleSettlementWork(context.Background(), &settlement.Work{
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
