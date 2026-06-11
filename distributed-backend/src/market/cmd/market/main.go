package main

import (
	"log"
	"net/http"
	"os"

	"connectrpc.com/connect"
	"connectrpc.com/validate"
	"github.com/QuasarRay/eve-trade/distributed-backend/gen/go/market/v1/marketv1connect"
	"github.com/QuasarRay/eve-trade/distributed-backend/market"
)

func main() {
	settlementURL := getenv("SETTLEMENT_URL", "http://localhost:8082")
	listenAddr := getenv("MARKET_ADDR", ":8081")

	path, handler := marketv1connect.NewMarketServiceHandler(
		market.NewService(market.NewBook(), market.NewSettlementClient(settlementURL)),
		connect.WithInterceptors(validate.NewInterceptor()),
	)

	mux := http.NewServeMux()
	mux.Handle(path, handler)
	log.Printf("market listening on %s", listenAddr)
	log.Fatal(http.ListenAndServe(listenAddr, mux))
}

func getenv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
