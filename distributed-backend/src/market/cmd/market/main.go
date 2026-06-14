package main

import (
	"context"
	"log"
	"log/slog"
	"net/http"
	"os"

	"connectrpc.com/connect"
	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/market/v1/marketv1connect"
	market "github.com/QuasarRay/eve-trade/distributed-backend/src/market"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/internal/observability"
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

	settlementClient := market.NewSettlementClient(
		settlementURL,
		connect.WithInterceptors(clientInterceptor),
	)

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