package main

import (
	"log"
	"net/http"
	"os"

	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/gateway/v1/gatewayv1connect"
	gateway "github.com/QuasarRay/eve-trade/distributed-backend/src/api-gateway"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
)

func main() {
	marketURL := getenv("MARKET_URL", "http://localhost:8081")
	listenAddr := getenv("API_GATEWAY_ADDR", ":8080")

	path, handler := gatewayv1connect.NewApiGatewayTradeServiceHandler(
		gateway.NewService(gateway.NewMarketClient(marketURL)),
	)

	mux := http.NewServeMux()
	mux.Handle(path, handler)

	log.Printf("api-gateway listening on %s", listenAddr)
	log.Fatal((&http.Server{
		Addr:    listenAddr,
		Handler: h2c.NewHandler(mux, &http2.Server{}),
	}).ListenAndServe())
}

func getenv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}

	return fallback
}
