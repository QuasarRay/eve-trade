package main

import (
	"context"
	"log"
	"log/slog"
	"net/http"
	"os"

	"connectrpc.com/connect"
	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/gateway/v1/gatewayv1connect"
	gateway "github.com/QuasarRay/eve-trade/distributed-backend/src/api-gateway"
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

	marketURL := getenv("MARKET_URL", "http://localhost:8081")
	listenAddr := getenv("API_GATEWAY_ADDR", ":8080")

	serverInterceptor := observability.NewExternalServerInterceptor()
	clientInterceptor := observability.NewClientInterceptor()

	marketClient := gateway.NewMarketClient(
		marketURL,
		connect.WithInterceptors(clientInterceptor),
	)

	path, handler := gatewayv1connect.NewGameTradeGatewayServiceHandler(
		gateway.NewService(marketClient),
		connect.WithInterceptors(serverInterceptor),
	)

	mux := http.NewServeMux()
	mux.Handle(path, handler)

	server := &http.Server{
		Addr:    listenAddr,
		Handler: h2c.NewHandler(mux, &http2.Server{}),
	}

	log.Printf("api-gateway listening on %s", listenAddr)

	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("api-gateway server failed", "error", err)

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