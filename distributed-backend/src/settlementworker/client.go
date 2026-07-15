package settlementworker

import (
	"context"
	"fmt"
	"time"

	"github.com/QuasarRay/eve-trade/distributed-backend/internal/settlementrpc"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	healthv1 "google.golang.org/grpc/health/grpc_health_v1"
)

const executeSettlementBatchMethod = "/eve.trade_settlement.v1.TradeSettlementService/ExecuteSettlementBatch"

type SettlementExecutor interface {
	ExecuteSettlementBatch(ctx context.Context, request *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error)
	GetSettlementOperation(ctx context.Context, operationID string) (*tradesettlementv1.SettlementOperationStatus, error)
	UpdateSettlementOperation(ctx context.Context, request *tradesettlementv1.UpdateSettlementOperationRequest) (*tradesettlementv1.SettlementOperationStatus, error)
	Ping(ctx context.Context) error
}

type healthChecker interface {
	Check(ctx context.Context, request *healthv1.HealthCheckRequest, options ...grpc.CallOption) (*healthv1.HealthCheckResponse, error)
}

type GRPCSettlementExecutor struct {
	client    grpc.ClientConnInterface
	health    healthChecker
	lifecycle *settlementrpc.Client
	timeout   time.Duration
}

func NewGRPCSettlementExecutor(target string, timeout time.Duration) (*GRPCSettlementExecutor, error) {
	conn, err := grpc.NewClient(target, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &GRPCSettlementExecutor{
		client:    conn,
		health:    healthv1.NewHealthClient(conn),
		lifecycle: settlementrpc.NewWithConn(conn),
		timeout:   timeout,
	}, nil
}

func (e *GRPCSettlementExecutor) GetSettlementOperation(ctx context.Context, operationID string) (*tradesettlementv1.SettlementOperationStatus, error) {
	ctx, cancel := e.callContext(ctx)
	defer cancel()
	response, err := e.lifecycle.GetSettlementOperation(ctx, &tradesettlementv1.GetSettlementOperationRequest{OperationId: operationID})
	if err != nil {
		return nil, err
	}
	if response.GetOperation() == nil {
		return nil, fmt.Errorf("trade settlement returned no operation status")
	}
	return response.GetOperation(), nil
}

func (e *GRPCSettlementExecutor) UpdateSettlementOperation(ctx context.Context, request *tradesettlementv1.UpdateSettlementOperationRequest) (*tradesettlementv1.SettlementOperationStatus, error) {
	ctx, cancel := e.callContext(ctx)
	defer cancel()
	response, err := e.lifecycle.UpdateSettlementOperation(ctx, request)
	if err != nil {
		return nil, err
	}
	if response.GetOperation() == nil {
		return nil, fmt.Errorf("trade settlement returned no operation status")
	}
	return response.GetOperation(), nil
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

	response, err := e.health.Check(ctx, &healthv1.HealthCheckRequest{Service: "readiness"})
	if err != nil {
		return err
	}
	if response.GetStatus() != healthv1.HealthCheckResponse_SERVING {
		return fmt.Errorf("trade settlement readiness status is %s", response.GetStatus())
	}
	return nil
}

func (e *GRPCSettlementExecutor) callContext(parent context.Context) (context.Context, context.CancelFunc) {
	if e.timeout <= 0 {
		return context.WithCancel(parent)
	}
	return context.WithTimeout(parent, e.timeout)
}
