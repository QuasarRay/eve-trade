package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"connectrpc.com/connect"
	distributedbackend "github.com/QuasarRay/eve-trade/market/distributed-backend"
	"github.com/QuasarRay/eve-trade/messaging/rabbitmqsettlement"
	"github.com/QuasarRay/eve-trade/observability"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	shutdownTelemetry := observability.Init(ctx)
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := shutdownTelemetry(shutdownCtx); err != nil {
			slog.Error("market telemetry shutdown failed", "error", err)
		}
	}()

	config := distributedbackend.LoadConfig()
	repository, err := retryStartupDependency(ctx, config, "postgres", func(ctx context.Context) (*distributedbackend.PostgresTradeRepository, error) {
		return distributedbackend.NewPostgresTradeRepository(ctx, config.DatabaseURL)
	})
	if err != nil {
		slog.Error("market repository initialization failed", "error", err)
		os.Exit(1)
	}
	defer repository.Close()

	settlementWithClose, err := retryStartupDependency(ctx, config, "settlement_transport", func(ctx context.Context) (settlementExecutorWithClose, error) {
		settlement, closeSettlement, err := newSettlementExecutor(ctx, config)
		return settlementExecutorWithClose{executor: settlement, close: closeSettlement}, err
	})
	if err != nil {
		slog.Error("market settlement transport initialization failed", "transport", config.SettlementTransport, "error", err)
		os.Exit(1)
	}
	settlement := settlementWithClose.executor
	defer settlementWithClose.close()

	handler := distributedbackend.NewMarketHandler(settlement, repository)
	readiness := func(ctx context.Context) error {
		if err := repository.Ping(ctx); err != nil {
			return err
		}
		if checker, ok := settlement.(interface {
			Ping(context.Context) error
		}); ok {
			return checker.Ping(ctx)
		}
		return nil
	}
	server := distributedbackend.NewHTTPServer(
		config,
		handler,
		readiness,
		connect.WithInterceptors(observability.NewInternalServerInterceptor()),
	)

	errs := make(chan error, 1)
	go func() {
		slog.Info("market service listening", "addr", config.HTTPAddr, "settlement_transport", config.SettlementTransport, "trade_settlement_url", config.TradeSettlementURL)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errs <- err
		}
	}()

	select {
	case <-ctx.Done():
	case err := <-errs:
		slog.Error("market service failed", "error", err)
		os.Exit(1)
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		slog.Error("market service shutdown failed", "error", err)
		os.Exit(1)
	}
}

func newSettlementExecutor(ctx context.Context, config distributedbackend.Config) (distributedbackend.SettlementExecutor, func(), error) {
	switch rabbitmqsettlement.NormalizeTransport(config.SettlementTransport) {
	case "", "connect", "grpc", "direct":
		return distributedbackend.NewConnectSettlementExecutor(
			config.TradeSettlementURL,
			config.SettlementRequestTimeout,
			connect.WithInterceptors(observability.NewClientInterceptor()),
		), func() {}, nil
	case "rabbitmq", "amqp":
		client, err := rabbitmqsettlement.NewRPCClient(ctx, config.RabbitMQ)
		if err != nil {
			return nil, func() {}, err
		}
		return client, func() {
			if err := client.Close(); err != nil {
				slog.Warn("rabbitmq settlement client shutdown failed", "error", err)
			}
		}, nil
	default:
		return nil, func() {}, errors.New("SETTLEMENT_TRANSPORT must be connect or rabbitmq")
	}
}

type settlementExecutorWithClose struct {
	executor distributedbackend.SettlementExecutor
	close    func()
}

func retryStartupDependency[T any](
	ctx context.Context,
	config distributedbackend.Config,
	name string,
	connect func(context.Context) (T, error),
) (T, error) {
	var zero T
	deadline := time.Now().Add(config.StartupDependencyTimeout)

	var lastErr error
	for attempt := 1; ; attempt++ {
		value, err := connect(ctx)
		if err == nil {
			if attempt > 1 {
				slog.Info("market startup dependency ready", "dependency", name, "attempt", attempt)
			}
			return value, nil
		}
		lastErr = err
		slog.Warn("market startup dependency not ready", "dependency", name, "attempt", attempt, "error", err)

		timer := time.NewTimer(config.StartupRetryInterval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return zero, errors.Join(lastErr, ctx.Err())
		case <-timer.C:
		}
		if !time.Now().Before(deadline) {
			return zero, errors.Join(lastErr, context.DeadlineExceeded)
		}
	}
}
