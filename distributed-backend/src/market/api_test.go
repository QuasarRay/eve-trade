package market

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func resetDefaultMarketStateForTest(t *testing.T) {
	t.Helper()
	defaultState.mu.Lock()
	defaultState.initializing = nil
	defaultState.handler = nil
	defaultState.err = nil
	defaultState.mu.Unlock()

	oldLoadConfig := loadMarketConfig
	oldOpenRepository := openDefaultTradeRepository
	oldNewPublisher := newDefaultSettlementPublisher
	loadMarketConfig = func() Config {
		return Config{
			DatabaseURL:              "postgres://market-readonly@example.invalid/eve_trade",
			StartupDependencyTimeout: time.Millisecond,
			StartupRetryInterval:     time.Millisecond,
		}
	}
	newDefaultSettlementPublisher = func() SettlementPublisher {
		return fakeSettlementExecutor{}
	}
	t.Cleanup(func() {
		defaultState.mu.Lock()
		defaultState.initializing = nil
		defaultState.handler = nil
		defaultState.err = nil
		defaultState.mu.Unlock()
		loadMarketConfig = oldLoadConfig
		openDefaultTradeRepository = oldOpenRepository
		newDefaultSettlementPublisher = oldNewPublisher
	})
}

func TestDefaultMarketHandlerRetriesAfterInitializationFailure(t *testing.T) {
	resetDefaultMarketStateForTest(t)
	var attempts atomic.Int32
	openDefaultTradeRepository = func(context.Context, Config) (TradeRepository, error) {
		if attempts.Add(1) == 1 {
			return nil, errors.New("temporary database outage")
		}
		return fakeTradeRepository{}, nil
	}

	if _, err := defaultMarketHandler(context.Background()); err == nil {
		t.Fatal("first initialization unexpectedly succeeded")
	}
	handler, err := defaultMarketHandler(context.Background())
	if err != nil {
		t.Fatalf("second initialization returned error: %v", err)
	}
	if handler == nil {
		t.Fatal("second initialization returned nil handler")
	}
	if attempts.Load() != 2 {
		t.Fatalf("initialization attempts = %d, want 2", attempts.Load())
	}
}

func TestDefaultMarketHandlerCoordinatesConcurrentInitialization(t *testing.T) {
	resetDefaultMarketStateForTest(t)
	var attempts atomic.Int32
	started := make(chan struct{})
	release := make(chan struct{})
	openDefaultTradeRepository = func(context.Context, Config) (TradeRepository, error) {
		attempts.Add(1)
		close(started)
		<-release
		return fakeTradeRepository{}, nil
	}

	const callers = 8
	var wg sync.WaitGroup
	errs := make(chan error, callers)
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := defaultMarketHandler(context.Background())
			errs <- err
		}()
	}

	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("initializer did not start")
	}
	close(release)
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatalf("concurrent caller returned error: %v", err)
		}
	}
	if attempts.Load() != 1 {
		t.Fatalf("initialization attempts = %d, want 1", attempts.Load())
	}
}

func TestDefaultMarketHandlerReusesSuccessfulInitialization(t *testing.T) {
	resetDefaultMarketStateForTest(t)
	var attempts atomic.Int32
	openDefaultTradeRepository = func(context.Context, Config) (TradeRepository, error) {
		attempts.Add(1)
		return fakeTradeRepository{}, nil
	}

	first, err := defaultMarketHandler(context.Background())
	if err != nil {
		t.Fatalf("first initialization returned error: %v", err)
	}
	second, err := defaultMarketHandler(context.Background())
	if err != nil {
		t.Fatalf("second initialization returned error: %v", err)
	}
	if first != second {
		t.Fatal("successful initialization was not reused")
	}
	if attempts.Load() != 1 {
		t.Fatalf("initialization attempts = %d, want 1", attempts.Load())
	}
}

func TestDefaultMarketHandlerCancelledFirstAttemptDoesNotPoisonState(t *testing.T) {
	resetDefaultMarketStateForTest(t)
	var attempts atomic.Int32
	openDefaultTradeRepository = func(ctx context.Context, _ Config) (TradeRepository, error) {
		if attempts.Add(1) == 1 {
			<-ctx.Done()
			return nil, ctx.Err()
		}
		return fakeTradeRepository{}, nil
	}

	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := defaultMarketHandler(cancelled); err == nil {
		t.Fatal("cancelled initialization unexpectedly succeeded")
	}
	if _, err := defaultMarketHandler(context.Background()); err != nil {
		t.Fatalf("retry after cancellation returned error: %v", err)
	}
	if attempts.Load() != 2 {
		t.Fatalf("initialization attempts = %d, want 2", attempts.Load())
	}
}
