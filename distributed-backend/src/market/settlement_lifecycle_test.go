package market

import (
	"reflect"
	"testing"
	"time"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/timestamppb"
)

func TestSettlementOperationResponseIsDeterministic(t *testing.T) {
	queuedAt := timestamppb.New(time.Date(2026, 7, 10, 1, 2, 3, 0, time.UTC))
	updatedAt := timestamppb.New(time.Date(2026, 7, 10, 1, 3, 4, 0, time.UTC))
	operation := &tradesettlementv1.SettlementOperationStatus{
		OperationId:        "11111111-1111-4111-8111-111111111111",
		IdempotencyKey:     "issue-1",
		State:              tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED,
		QueuedAt:           queuedAt,
		UpdatedAt:          updatedAt,
		FailureCode:        codes.PermissionDenied.String(),
		FailureDescription: "unauthorized actor",
	}

	first := settlementOperationResponse(operation)
	second := settlementOperationResponse(operation)

	if !reflect.DeepEqual(first, second) {
		t.Fatalf("operation response changed between identical queries: first=%+v second=%+v", first, second)
	}
	if first.Status != "failed" || !first.QueuedAt.Equal(queuedAt.AsTime()) || !first.UpdatedAt.Equal(updatedAt.AsTime()) {
		t.Fatalf("unexpected operation response: %+v", first)
	}
}

func TestSettlementOperationAPIErrorsPreserveClientMeaning(t *testing.T) {
	tests := []struct {
		grpcCode codes.Code
		want     errs.ErrCode
	}{
		{grpcCode: codes.InvalidArgument, want: errs.InvalidArgument},
		{grpcCode: codes.NotFound, want: errs.NotFound},
		{grpcCode: codes.DeadlineExceeded, want: errs.Unavailable},
		{grpcCode: codes.Unavailable, want: errs.Unavailable},
		{grpcCode: codes.Internal, want: errs.Internal},
	}
	for _, test := range tests {
		t.Run(test.grpcCode.String(), func(t *testing.T) {
			err := settlementOperationAPIError(status.Error(test.grpcCode, "failure"))
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
	operation.FailureCode = codes.PermissionDenied.String()
	if err := validateSettlementResult(&settlement.Result{OperationID: operation.OperationId}, operation); err == nil {
		t.Fatal("mismatched durable failure accepted")
	}
}
