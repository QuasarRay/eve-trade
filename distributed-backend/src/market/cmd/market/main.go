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
	repository, err := distributedbackend.NewPostgresTradeRepository(ctx, config.DatabaseURL)
	if err != nil {
		slog.Error("market repository initialization failed", "error", err)
		os.Exit(1)
	}
	defer repository.Close()

	settlement := distributedbackend.NewConnectSettlementExecutor(
		config.TradeSettlementURL,
		config.SettlementRequestTimeout,
		connect.WithInterceptors(observability.NewClientInterceptor()),
	)
	handler := distributedbackend.NewMarketHandler(settlement, repository)
	server := distributedbackend.NewHTTPServer(
		config,
		handler,
		connect.WithInterceptors(observability.NewInternalServerInterceptor()),
	)

	errs := make(chan error, 1)
	go func() {
		slog.Info("market service listening", "addr", config.HTTPAddr, "trade_settlement_url", config.TradeSettlementURL)
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
