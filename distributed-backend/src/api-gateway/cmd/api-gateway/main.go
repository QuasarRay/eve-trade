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
	distributedbackend "github.com/QuasarRay/eve-trade/api-gateway/distributed-backend"
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
			slog.Error("api-gateway telemetry shutdown failed", "error", err)
		}
	}()

	config := distributedbackend.LoadConfig()
	market := distributedbackend.NewConnectMarketClient(
		config.MarketURL,
		config.DownstreamTimeout,
		connect.WithInterceptors(observability.NewClientInterceptor()),
	)
	handler := distributedbackend.NewGatewayHandler(market)
	server := distributedbackend.NewHTTPServer(
		config,
		handler,
		market.CheckReady,
		connect.WithInterceptors(observability.NewExternalServerInterceptor()),
	)

	errs := make(chan error, 1)
	go func() {
		slog.Info("api-gateway listening", "addr", config.HTTPAddr, "market_url", config.MarketURL)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errs <- err
		}
	}()

	select {
	case <-ctx.Done():
	case err := <-errs:
		slog.Error("api-gateway failed", "error", err)
		os.Exit(1)
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		slog.Error("api-gateway shutdown failed", "error", err)
		os.Exit(1)
	}
}
