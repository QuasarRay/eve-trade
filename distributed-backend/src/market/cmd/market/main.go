package main

import (
	"context"
	"log"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"time"

	"connectrpc.com/connect"
	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/market/v1/marketv1connect"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/internal/observability"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/internal/rabbitmq"
	market "github.com/QuasarRay/eve-trade/distributed-backend/src/market"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
)

func main() {
	ctx := context.Background()

	shutdownTelemetry := observability.Init(ctx)
	defer func() {
		if err := shutdownTelemetry(ctx); err != nil {
			slog.Error("failed to shutdown telemetry", "error", err)
		}
	}()

	settlementURL := getenv("SETTLEMENT_URL", "http://localhost:9092")
	listenAddr := getenv("MARKET_ADDR", ":8081")

	serverInterceptor := observability.NewInternalServerInterceptor()
	clientInterceptor := observability.NewClientInterceptor()

	settlementClient, closeSettlement := newSettlementClient(settlementURL, clientInterceptor)
	defer func() {
		if err := closeSettlement(); err != nil {
			slog.Error("failed to close settlement client", "error", err)
		}
	}()

	path, handler := marketv1connect.NewMarketTradeServiceHandler(
		market.NewService(settlementClient),
		connect.WithInterceptors(serverInterceptor),
	)

	mux := http.NewServeMux()
	mux.Handle(path, handler)

	server := &http.Server{
		Addr:    listenAddr,
		Handler: h2c.NewHandler(mux, &http2.Server{}),
	}

	log.Printf("market listening on %s", listenAddr)

	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("market server failed", "error", err)

		if shutdownErr := shutdownTelemetry(ctx); shutdownErr != nil {
			slog.Error("failed to shutdown telemetry", "error", shutdownErr)
		}

		os.Exit(1)
	}
}

func getenv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}

	return fallback
}

func newSettlementClient(settlementURL string, clientInterceptor connect.Interceptor) (market.Settlement, func() error) {
	transport := strings.ToLower(getenv("SETTLEMENT_TRANSPORT", "grpc"))
	switch transport {
	case "rabbitmq", "amqp":
		client, err := rabbitmq.NewSettlementClient(rabbitmq.SettlementConfig{
			URL:            getenv("RABBITMQ_URL", rabbitmq.DefaultSettlementURL),
			Exchange:       getenv("RABBITMQ_SETTLEMENT_EXCHANGE", rabbitmq.DefaultSettlementExchange),
			CommandQueue:   getenv("RABBITMQ_SETTLEMENT_COMMAND_QUEUE", rabbitmq.DefaultSettlementCommandQueue),
			RoutingKey:     getenv("RABBITMQ_SETTLEMENT_ROUTING_KEY", rabbitmq.DefaultSettlementRoutingKey),
			RequestTimeout: getenvDuration("RABBITMQ_SETTLEMENT_REQUEST_TIMEOUT", 30*time.Second),
		})
		if err != nil {
			slog.Error("failed to initialize RabbitMQ settlement client", "error", err)
			os.Exit(1)
		}
		return client, client.Close
	default:
		return market.NewSettlementClient(settlementURL, connect.WithInterceptors(clientInterceptor)), func() error { return nil }
	}
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
