package market

import (
	"context"
	"fmt"
	"sync"
	"time"

	"encore.dev/beta/errs"
)

type SubmitTradeGuiInteractionRequest struct {
	RawPayload []byte `json:"raw_payload"`
}

type SubmitTradeGuiInteractionResponse struct {
	InteractionID               string `json:"interaction_id"`
	Status                      string `json:"status"`
	SettlementBatchID           string `json:"settlement_batch_id,omitempty"`
	TradeInstanceID             string `json:"trade_instance_id,omitempty"`
	ItemStackEscrowID           string `json:"item_stack_escrow_id,omitempty"`
	WalletEscrowID              string `json:"wallet_escrow_id,omitempty"`
	BuyerDestinationItemStackID string `json:"buyer_destination_item_stack_id,omitempty"`
}

type HealthResponse struct {
	Status string `json:"status"`
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

var newDefaultSettlementPublisher = func() SettlementPublisher {
	return NewSettlementPublisher()
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
	return NewMarketHandler(newDefaultSettlementPublisher(), repo), nil
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
