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

	distributedbackend "github.com/astral/eve-trade/market/distributed-backend"
)

func main() {
	config := distributedbackend.LoadConfig()
	settlement := distributedbackend.NewConnectSettlementExecutor(config.TradeSettlementURL, config.SettlementRequestTimeout)
	handler := distributedbackend.NewMarketHandler(settlement)
	server := distributedbackend.NewHTTPServer(config, handler)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

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
