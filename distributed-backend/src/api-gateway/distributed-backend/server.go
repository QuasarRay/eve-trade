package distributedbackend

import (
	"net/http"
	"time"

	"connectrpc.com/connect"
	api_gatewayv1connect "github.com/astral/eve-trade/proto/gen/eve/api_gateway/v1/api_gatewayv1connect"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
)

func NewHTTPServer(config Config, handler *GatewayHandler, handlerOptions ...connect.HandlerOption) *http.Server {
	mux := http.NewServeMux()
	path, serviceHandler := api_gatewayv1connect.NewGameTradeGatewayServiceHandler(handler, handlerOptions...)
	mux.Handle(path, serviceHandler)

	return &http.Server{
		Addr:              config.HTTPAddr,
		Handler:           h2c.NewHandler(mux, &http2.Server{}),
		ReadHeaderTimeout: 5 * time.Second,
	}
}
