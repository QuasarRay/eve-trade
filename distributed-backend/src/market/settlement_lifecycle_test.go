package market

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"encore.dev/beta/errs"
	"encore.dev/pubsub"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/settlementrpc"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
)

type failingSettlementTopic struct {
	cancel context.CancelFunc
}

func (topic failingSettlementTopic) Publish(context.Context, *settlement.Work) (string, error) {
	topic.cancel()
	return "", errors.New("broker unavailable")
}

func (failingSettlementTopic) Meta() pubsub.TopicMeta { return pubsub.TopicMeta{} }

type recordingSettlementLifecycle struct {
	updateContextErr error
	update           *tradesettlementv1.UpdateSettlementOperationRequest
}

func (lifecycle *recordingSettlementLifecycle) QueueSettlementOperation(context.Context, *tradesettlementv1.QueueSettlementOperationRequest) (*tradesettlementv1.QueueSettlementOperationResponse, error) {
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

func TestSettlementPublicationFailureBecomesDurableTerminalFailure(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	lifecycle := new(recordingSettlementLifecycle)
	publisher := PubSubSettlementPublisher{
		topic:     failingSettlementTopic{cancel: cancel},
		lifecycle: lifecycle,
		timeout:   time.Second,
	}

	_, err := publisher.PublishSettlementWork(ctx, &settlement.Work{
		IdempotencyKey:      "issue-publish-failure",
		RequestFingerprint:  "market-request-fingerprint.v1:test",
		Intent:              settlement.IntentIssue,
		CausedByCapsuleerID: 1001,
	})

	if err == nil || err.Error() != "publish settlement work: broker unavailable" {
		t.Fatalf("unexpected publication error: %v", err)
	}
	if lifecycle.updateContextErr != nil {
		t.Fatalf("terminal update inherited cancelled publication context: %v", lifecycle.updateContextErr)
	}
	if lifecycle.update == nil || lifecycle.update.GetState() != tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED {
		t.Fatalf("publication failure did not mark operation failed: %+v", lifecycle.update)
	}
	if lifecycle.update.GetFailureCode() != "WORK_PUBLICATION_FAILED" {
		t.Fatalf("failure code = %q", lifecycle.update.GetFailureCode())
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
			if got := errs.Code(err); got != test.want {
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
