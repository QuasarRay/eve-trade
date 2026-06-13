package main

import (
	"log"
	"net/http"
	"os"

	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/market/v1/marketv1connect"
	market "github.com/QuasarRay/eve-trade/distributed-backend/src/market"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
)

// main wires the market service process together.
// It reads runtime configuration from environment variables, creates the
// settlement client, registers the generated MarketService handler, and starts
// an HTTP server because connect-go exposes gRPC-compatible handlers over net/http.
// This exists so the market binary remains a thin composition layer instead of
// hiding domain behavior in startup code.
func main() {
	settlementURL := getenv("SETTLEMENT_URL", "http://localhost:9092")
	listenAddr := getenv("MARKET_ADDR", ":8081")

	path, handler := marketv1connect.NewMarketInteractionIngressServiceHandler(
		market.NewService(market.NewSettlementClient(settlementURL)),
	)

	mux := http.NewServeMux()
	mux.Handle(path, handler)

	log.Printf("market listening on %s", listenAddr)
	log.Fatal((&http.Server{
		Addr:    listenAddr,
		Handler: h2c.NewHandler(mux, &http2.Server{}),
	}).ListenAndServe())
}

// getenv reads one environment variable with a deterministic fallback.
// It checks the process environment first, returns the configured value when it
// exists, and otherwise returns the fallback supplied by the caller.
// This exists so local development and container deployment can use the same
// binary without hard-coding addresses into the market implementation.
func getenv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}

	return fallback
}
