package market

import (
	"context"
	"fmt"
	"sync"
	"time"

	"encore.dev/beta/errs"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"github.com/andeya/gust/option"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type SubmitTradeGuiInteractionRequest struct {
	RawPayload []byte `json:"raw_payload"`
}

type SubmitTradeGuiInteractionResponse struct {
	InteractionID               string    `json:"interaction_id"`
	OperationID                 string    `json:"operation_id"`
	QueuedAt                    time.Time `json:"queued_at"`
	Status                      string    `json:"status"`
	SettlementBatchID           string    `json:"settlement_batch_id,omitempty"`
	TradeInstanceID             string    `json:"trade_instance_id,omitempty"`
	ItemStackEscrowID           string    `json:"item_stack_escrow_id,omitempty"`
	WalletEscrowID              string    `json:"wallet_escrow_id,omitempty"`
	BuyerDestinationItemStackID string    `json:"buyer_destination_item_stack_id,omitempty"`
}

type HealthResponse struct {
	Status string `json:"status"`
}

type SettlementOperationResponse struct {
	OperationID        string    `json:"operation_id"`
	IdempotencyKey     string    `json:"idempotency_key"`
	Status             string    `json:"status"`
	QueuedAt           time.Time `json:"queued_at"`
	UpdatedAt          time.Time `json:"updated_at"`
	SettlementBatchID  string    `json:"settlement_batch_id,omitempty"`
	FailureCode        string    `json:"failure_code,omitempty"`
	FailureDescription string    `json:"failure_description,omitempty"`
}

var defaultState struct {
	mu           sync.Mutex
	initializing chan struct{}
	handler      *MarketHandler
	err          error
}

var loadMarketConfig = LoadConfig

var openDefaultTradeRepository = func(ctx context.Context, cfg Config) (TradeRepository, error) {
	return openPostgresRepositoryWithRetry(ctx, cfg)
}

var newDefaultSettlementPublisher = func(cfg Config) (SettlementPublisher, error) {
	publisher, err := NewSettlementPublisher(cfg.TradeSettlementTarget, cfg.TradeSettlementTimeout)
	return publisher, err
}

//encore:api private
func SubmitTradeGuiInteraction(ctx context.Context, req *SubmitTradeGuiInteractionRequest) (*SubmitTradeGuiInteractionResponse, error) {
	handler, err := defaultMarketHandler(ctx)
	if err != nil {
		return nil, errs.WrapCode(err, errs.Unavailable, "market dependencies unavailable")
	}
	return handler.SubmitTradeGuiInteraction(ctx, req)
}

//encore:api public method=GET path=/market/healthz
func MarketHealth(ctx context.Context) (*HealthResponse, error) {
	return &HealthResponse{Status: "ok"}, nil
}

//encore:api public method=GET path=/market/readyz
func MarketReady(ctx context.Context) (*HealthResponse, error) {
	handler, err := defaultMarketHandler(ctx)
	if err != nil {
		return nil, errs.WrapCode(err, errs.Unavailable, "market dependencies unavailable")
	}
	if pinger, ok := handler.trades.(interface{ Ping(context.Context) error }); ok {
		if err := pinger.Ping(ctx); err != nil {
			return nil, errs.WrapCode(err, errs.Unavailable, "market database unavailable")
		}
	}
	return &HealthResponse{Status: "ready"}, nil
}

//encore:api public method=GET path=/market/operations/:operation_id
func GetSettlementOperation(ctx context.Context, operation_id string) (*SettlementOperationResponse, error) {
	handler, err := defaultMarketHandler(ctx)
	if err != nil {
		return nil, errs.WrapCode(err, errs.Unavailable, "market dependencies unavailable")
	}
	reader, ok := handler.settlement.(OperationStatusReader)
	if !ok {
		return nil, errs.B().Code(errs.Unavailable).Msg("settlement lifecycle unavailable").Err()
	}
	operation, err := reader.GetSettlementOperation(ctx, operation_id)
	if err != nil {
		return nil, settlementOperationAPIError(err)
	}
	return settlementOperationResponse(operation), nil
}

func settlementOperationResponse(operation *tradesettlementv1.SettlementOperationStatus) *SettlementOperationResponse {
	response := &SettlementOperationResponse{
		OperationID:        operation.GetOperationId(),
		IdempotencyKey:     operation.GetIdempotencyKey(),
		Status:             settlementOperationStatus(operation.GetState()),
		SettlementBatchID:  operation.GetSettlementBatchId(),
		FailureCode:        operation.GetFailureCode(),
		FailureDescription: operation.GetFailureDescription(),
	}
	option.PtrOpt(operation.GetQueuedAt()).Inspect(func(value *timestamppb.Timestamp) {
		response.QueuedAt = value.AsTime().UTC()
	})
	option.PtrOpt(operation.GetUpdatedAt()).Inspect(func(value *timestamppb.Timestamp) {
		response.UpdatedAt = value.AsTime().UTC()
	})
	return response
}

func settlementOperationAPIError(err error) error {
	switch status.Code(err) {
	case codes.InvalidArgument:
		return errs.WrapCode(err, errs.InvalidArgument, "invalid settlement operation query")
	case codes.NotFound:
		return errs.WrapCode(err, errs.NotFound, "settlement operation not found")
	case codes.DeadlineExceeded, codes.Unavailable:
		return errs.WrapCode(err, errs.Unavailable, "settlement lifecycle unavailable")
	default:
		return errs.WrapCode(err, errs.Internal, "load settlement operation")
	}
}

func settlementOperationStatus(state tradesettlementv1.SettlementOperationState) string {
	switch state {
	case tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_QUEUED:
		return "queued"
	case tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_PROCESSING:
		return "processing"
	case tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_SUCCEEDED:
		return "succeeded"
	case tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_FAILED:
		return "failed"
	case tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_CANCELLED:
		return "cancelled"
	case tradesettlementv1.SettlementOperationState_SETTLEMENT_OPERATION_STATE_EXPIRED:
		return "expired"
	default:
		return "unknown"
	}
}

func defaultMarketHandler(ctx context.Context) (*MarketHandler, error) {
	for {
		defaultState.mu.Lock()
		if defaultState.handler != nil {
			handler := defaultState.handler
			defaultState.mu.Unlock()
			return handler, nil
		}
		if initializing := defaultState.initializing; initializing != nil {
			defaultState.mu.Unlock()
			select {
			case <-initializing:
				continue
			case <-ctx.Done():
				return nil, fmt.Errorf("initialize market dependencies: %w", ctx.Err())
			}
		}
		initializing := make(chan struct{})
		defaultState.initializing = initializing
		defaultState.mu.Unlock()

		handler, err := initializeDefaultMarketHandler(ctx)

		defaultState.mu.Lock()
		if err == nil {
			defaultState.handler = handler
			defaultState.err = nil
		} else {
			defaultState.err = err
		}
		defaultState.initializing = nil
		close(initializing)
		defaultState.mu.Unlock()

		return handler, err
	}
}

func initializeDefaultMarketHandler(ctx context.Context) (*MarketHandler, error) {
	cfg := loadMarketConfig()
	repo, err := openDefaultTradeRepository(ctx, cfg)
	if err != nil {
		return nil, err
	}
	publisher, err := newDefaultSettlementPublisher(cfg)
	if err != nil {
		if closer, ok := repo.(interface{ Close() }); ok {
			closer.Close()
		}
		return nil, err
	}
	return NewMarketHandler(publisher, repo), nil
}

func openPostgresRepositoryWithRetry(ctx context.Context, cfg Config) (*PostgresTradeRepository, error) {
	deadline := time.Now().Add(cfg.StartupDependencyTimeout)
	var lastErr error
	for {
		repo, err := NewPostgresTradeRepository(ctx, cfg.DatabaseURL)
		if err == nil {
			return repo, nil
		}
		lastErr = err
		if !time.Now().Before(deadline) {
			return nil, fmt.Errorf("connect market database: %w", lastErr)
		}
		select {
		case <-ctx.Done():
			return nil, fmt.Errorf("connect market database: %w", ctx.Err())
		case <-time.After(cfg.StartupRetryInterval):
		}
	}
}
