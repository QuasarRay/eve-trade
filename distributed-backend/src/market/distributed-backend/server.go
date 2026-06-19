package distributedbackend

import (
	"net/http"
	"time"

	marketv1connect "github.com/astral/eve-trade/market/distributed-backend/gen/market/v1/marketv1connect"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
)

func NewHTTPServer(config Config, handler *MarketHandler) *http.Server {
	mux := http.NewServeMux()
	path, serviceHandler := marketv1connect.NewMarketServiceHandler(handler)
	mux.Handle(path, serviceHandler)

	return &http.Server{
		Addr:              config.HTTPAddr,
		Handler:           h2c.NewHandler(mux, &http2.Server{}),
		ReadHeaderTimeout: 5 * time.Second,
	}
}
