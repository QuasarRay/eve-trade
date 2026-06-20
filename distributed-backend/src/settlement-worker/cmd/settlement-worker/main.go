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
			slog.Error("settlement-worker telemetry shutdown failed", "error", err)
		}
	}()

	config := LoadConfig()
	executor := NewConnectSettlementExecutor(
		config.TradeSettlementURL,
		config.SettlementRequestTimeout,
		connect.WithInterceptors(observability.NewClientInterceptor()),
	)
	healthServer := NewHealthServer(config.HealthHTTPAddr)

	errs := make(chan error, 2)
	go func() {
		slog.Info("settlement-worker health server listening", "addr", config.HealthHTTPAddr)
		if err := healthServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errs <- err
		}
	}()
	go func() {
		errs <- rabbitmqsettlement.RunSettlementWorker(ctx, config.RabbitMQ, executor)
	}()

	slog.Info("settlement-worker started", "trade_settlement_url", config.TradeSettlementURL, "rabbitmq_exchange", config.RabbitMQ.Exchange, "rabbitmq_queue", config.RabbitMQ.CommandQueue)

	select {
	case <-ctx.Done():
	case err := <-errs:
		if err != nil && !errors.Is(err, context.Canceled) {
			slog.Error("settlement-worker failed", "error", err)
			stop()
			shutdownHealth(healthServer)
			os.Exit(1)
		}
	}

	shutdownHealth(healthServer)
}

func shutdownHealth(server *http.Server) {
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		slog.Warn("settlement-worker health server shutdown failed", "error", err)
	}
}
