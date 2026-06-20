package distributedbackend

import (
	"net/http"
	"time"

	"connectrpc.com/connect"
	marketv1connect "github.com/astral/eve-trade/proto/gen/eve/market/v1/marketv1connect"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
)

func NewHTTPServer(config Config, handler *MarketHandler, handlerOptions ...connect.HandlerOption) *http.Server {
	mux := http.NewServeMux()
	path, serviceHandler := marketv1connect.NewMarketServiceHandler(handler, handlerOptions...)
	mux.Handle(path, serviceHandler)

	return &http.Server{
		Addr:              config.HTTPAddr,
		Handler:           h2c.NewHandler(mux, &http2.Server{}),
		ReadHeaderTimeout: 5 * time.Second,
	}
}
