//go:build legacy_rabbitmq

package main

import (
	"context"
	"errors"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"connectrpc.com/connect"
	distributedbackend "github.com/QuasarRay/eve-trade/market/distributed-backend"
	"github.com/QuasarRay/eve-trade/observability"
	"github.com/QuasarRay/eve-trade/settlement-worker/internal/rabbitmq"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	shutdownTelemetry := observability.Init(ctx)
	defer func() {
		if err := shutdownTelemetry(ctx); err != nil {
			slog.Error("failed to shutdown telemetry", "error", err)
		}
	}()

	settlementClient := distributedbackend.NewConnectSettlementExecutor(
		getenv("SETTLEMENT_URL", "http://localhost:9092"),
		getenvDuration("SETTLEMENT_REQUEST_TIMEOUT", 30*time.Second),
		connect.WithInterceptors(observability.NewClientInterceptor()),
	)

	config := rabbitmq.SettlementConfig{
		URL:            getenv("RABBITMQ_URL", rabbitmq.DefaultSettlementURL),
		Exchange:       getenv("RABBITMQ_SETTLEMENT_EXCHANGE", rabbitmq.DefaultSettlementExchange),
		CommandQueue:   getenv("RABBITMQ_SETTLEMENT_COMMAND_QUEUE", rabbitmq.DefaultSettlementCommandQueue),
		RoutingKey:     getenv("RABBITMQ_SETTLEMENT_ROUTING_KEY", rabbitmq.DefaultSettlementRoutingKey),
		RequestTimeout: getenvDuration("RABBITMQ_SETTLEMENT_REQUEST_TIMEOUT", 30*time.Second),
		PrefetchCount:  getenvInt("RABBITMQ_SETTLEMENT_PREFETCH", 8),
	}

	slog.Info("settlement worker consuming RabbitMQ commands", "queue", config.CommandQueue)
	if err := rabbitmq.RunSettlementWorker(ctx, config, settlementClient); err != nil && !errors.Is(err, context.Canceled) {
		slog.Error("settlement worker failed", "error", err)
		os.Exit(1)
	}
}

func getenv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}

	return fallback
}

func getenvDuration(key string, fallback time.Duration) time.Duration {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	duration, err := time.ParseDuration(value)
	if err != nil {
		slog.Warn("invalid duration value, using fallback", "key", key, "value", value, "fallback", fallback)
		return fallback
	}
	return duration
}

func getenvInt(key string, fallback int) int {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		slog.Warn("invalid integer value, using fallback", "key", key, "value", value, "fallback", fallback)
		return fallback
	}
	return parsed
}
