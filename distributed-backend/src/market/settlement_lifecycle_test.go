package market

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"testing"
	"time"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/settlementrpc"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
)

type recordingSettlementLifecycle struct {
	updateContextErr error
	update           *tradesettlementv1.UpdateSettlementOperationRequest
	queued           *tradesettlementv1.QueueSettlementOperationRequest
}

func (lifecycle *recordingSettlementLifecycle) QueueSettlementOperation(_ context.Context, request *tradesettlementv1.QueueSettlementOperationRequest) (*tradesettlementv1.QueueSettlementOperationResponse, error) {
	lifecycle.queued = request
	return &tradesettlementv1.QueueSettlementOperationResponse{
		Operation: &tradesettlementv1.SettlementOperationStatus{
			OperationId: "11111111-1111-4111-8111-111111111111",
			QueuedAt:    settlementrpc.Timestamp(time.Now()),
		},
	}, nil
}

func (*recordingSettlementLifecycle) GetSettlementOperation(context.Context, *tradesettlementv1.GetSettlementOperationRequest) (*tradesettlementv1.GetSettlementOperationResponse, error) {
	return nil, errors.New("unexpected get")
}

func (lifecycle *recordingSettlementLifecycle) UpdateSettlementOperation(ctx context.Context, request *tradesettlementv1.UpdateSettlementOperationRequest) (*tradesettlementv1.UpdateSettlementOperationResponse, error) {
	lifecycle.updateContextErr = ctx.Err()
	lifecycle.update = request
	return &tradesettlementv1.UpdateSettlementOperationResponse{}, nil
}

func TestSettlementPublicationIsDurablyQueuedBeforeBrokerDelivery(t *testing.T) {
	lifecycle := new(recordingSettlementLifecycle)
	publisher := PubSubSettlementPublisher{
		lifecycle: lifecycle,
		timeout:   time.Second,
	}

	publication, err := publisher.PublishSettlementWork(context.Background(), &settlement.Work{
		IdempotencyKey:      "issue-publish-failure",
		RequestFingerprint:  "market-request-fingerprint.v1:test",
		Intent:              settlement.IntentIssue,
		CausedByCapsuleerID: 1001,
		CreatedByService:    settlement.CreatedByMarket,
		Operations: []settlement.Operation{
			{Kind: settlement.OperationCreateNewTradeInstanceRow},
		},
	})

	if err != nil {
		t.Fatalf("durable queue failed: %v", err)
	}
	if publication.MessageID != "outbox:"+publication.OperationID {
		t.Fatalf("publication does not identify durable outbox record: %+v", publication)
	}
	if lifecycle.update != nil {
		t.Fatalf("durably queued operation was marked terminal before delivery: %+v", lifecycle.update)
	}
	if lifecycle.queued == nil || len(lifecycle.queued.GetWorkPayloadJson()) == 0 {
		t.Fatal("queue request omitted durable work payload")
	}
	var payload settlement.Work
	if err := json.Unmarshal(lifecycle.queued.GetWorkPayloadJson(), &payload); err != nil {
		t.Fatalf("queue payload is invalid JSON: %v", err)
	}
	if payload.IdempotencyKey != "issue-publish-failure" || payload.OperationID != "" {
		t.Fatalf("unexpected pre-queue payload: %+v", payload)
	}
}

func TestSettlementOperationResponseIsDeterministic(t *testing.T) {
	queuedAt := settlementrpc.Timestamp(time.Date(2026, 7, 10, 1, 2, 3, 0, time.UTC))
	updatedAt := settlementrpc.Timestamp(time.Date(2026, 7, 10, 1, 3, 4, 0, time.UTC))
	operation := &tradesettlementv1.SettlementOperationStatus{
		OperationId:        "11111111-1111-4111-8111-111111111111",
		IdempotencyKey:     "issue-1",
		State:              tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED,
		QueuedAt:           queuedAt,
		UpdatedAt:          updatedAt,
		FailureCode:        settlementrpc.ErrorClassName(settlementrpc.ErrorPermissionDenied),
		FailureDescription: "unauthorized actor",
	}

	first := settlementOperationResponse(operation)
	second := settlementOperationResponse(operation)

	if !reflect.DeepEqual(first, second) {
		t.Fatalf("operation response changed between identical queries: first=%+v second=%+v", first, second)
	}
	if first.Status != "failed" || !first.QueuedAt.Equal(settlementrpc.Time(queuedAt)) || !first.UpdatedAt.Equal(settlementrpc.Time(updatedAt)) {
		t.Fatalf("unexpected operation response: %+v", first)
	}
}

func TestSettlementOperationAPIErrorsPreserveClientMeaning(t *testing.T) {
	tests := []struct {
		errorClass settlementrpc.ErrorClass
		want       errs.ErrCode
	}{
		{errorClass: settlementrpc.ErrorInvalidArgument, want: errs.InvalidArgument},
		{errorClass: settlementrpc.ErrorNotFound, want: errs.NotFound},
		{errorClass: settlementrpc.ErrorDeadlineExceeded, want: errs.Unavailable},
		{errorClass: settlementrpc.ErrorUnavailable, want: errs.Unavailable},
		{errorClass: settlementrpc.ErrorInternal, want: errs.Internal},
	}
	for _, test := range tests {
		t.Run(settlementrpc.ErrorClassName(test.errorClass), func(t *testing.T) {
			err := settlementOperationAPIError(settlementrpc.NewError(test.errorClass, "failure"))
			if got := apiErrorCode(err); got != test.want {
				t.Fatalf("error code = %v, want %v: %v", got, test.want, err)
			}
		})
	}
}

func TestDuplicateSettlementResultProjectionIsHarmless(t *testing.T) {
	operation := &tradesettlementv1.SettlementOperationStatus{
		OperationId:       "11111111-1111-4111-8111-111111111111",
		State:             tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_SUCCEEDED,
		SettlementBatchId: "22222222-2222-4222-8222-222222222222",
	}
	result := &settlement.Result{
		OperationID:       operation.OperationId,
		SettlementBatchID: operation.SettlementBatchId,
	}

	for attempt := 1; attempt <= 2; attempt++ {
		if err := validateSettlementResult(result, operation); err != nil {
			t.Fatalf("duplicate projection attempt %d failed: %v", attempt, err)
		}
	}
}

func TestSettlementResultMustMatchDurableTerminalState(t *testing.T) {
	operation := &tradesettlementv1.SettlementOperationStatus{
		OperationId: "11111111-1111-4111-8111-111111111111",
		State:       tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING,
	}
	if err := validateSettlementResult(&settlement.Result{OperationID: operation.OperationId}, operation); err == nil {
		t.Fatal("non-terminal durable operation accepted a result")
	}
	operation.State = tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED
	operation.FailureCode = settlementrpc.ErrorClassName(settlementrpc.ErrorPermissionDenied)
	if err := validateSettlementResult(&settlement.Result{OperationID: operation.OperationId}, operation); err == nil {
		t.Fatal("mismatched durable failure accepted")
	}
}
