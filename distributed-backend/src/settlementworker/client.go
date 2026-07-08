package settlementworker

import (
	"context"
	"time"

	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
)

const executeSettlementBatchMethod = "/eve.trade_settlement.v1.TradeSettlementService/ExecuteSettlementBatch"

type SettlementExecutor interface {
	ExecuteSettlementBatch(ctx context.Context, request *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error)
	Ping(ctx context.Context) error
}

type GRPCSettlementExecutor struct {
	client  grpc.ClientConnInterface
	timeout time.Duration
}

func NewGRPCSettlementExecutor(target string, timeout time.Duration) (*GRPCSettlementExecutor, error) {
	conn, err := grpc.NewClient(target, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &GRPCSettlementExecutor{
		client:  conn,
		timeout: timeout,
	}, nil
}

func (e *GRPCSettlementExecutor) ExecuteSettlementBatch(ctx context.Context, request *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	ctx, cancel := e.callContext(ctx)
	defer cancel()
	response := new(tradesettlementv1.ExecuteSettlementBatchResponse)
	if err := e.client.Invoke(ctx, executeSettlementBatchMethod, request, response); err != nil {
		return nil, err
	}
	return response, nil
}

func (e *GRPCSettlementExecutor) Ping(ctx context.Context) error {
	ctx, cancel := e.callContext(ctx)
	defer cancel()

	response := new(tradesettlementv1.ExecuteSettlementBatchResponse)
	err := e.client.Invoke(ctx, executeSettlementBatchMethod, &tradesettlementv1.ExecuteSettlementBatchRequest{
		IdempotencyKey:    "settlementworker-readiness",
		ExternalRequestId: "settlementworker-readiness",
		CreatedByService:  "settlementworker",
	}, response)
	if err == nil || status.Code(err) == codes.InvalidArgument {
		return nil
	}
	return err
}

func (e *GRPCSettlementExecutor) callContext(parent context.Context) (context.Context, context.CancelFunc) {
	if e.timeout <= 0 {
		return context.WithCancel(parent)
	}
	return context.WithTimeout(parent, e.timeout)
}
