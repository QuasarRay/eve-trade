package distributedbackend

import (
	"net/http"
	"time"

	"connectrpc.com/connect"
	apigatewayv1connect "github.com/astral/eve-trade/api-gateway/distributed-backend/gen/api_gateway/v1/apigatewayv1connect"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
)

func NewHTTPServer(config Config, handler *GatewayHandler, handlerOptions ...connect.HandlerOption) *http.Server {
	mux := http.NewServeMux()
	path, serviceHandler := apigatewayv1connect.NewGameTradeGatewayServiceHandler(handler, handlerOptions...)
	mux.Handle(path, serviceHandler)

	return &http.Server{
		Addr:              config.HTTPAddr,
		Handler:           h2c.NewHandler(mux, &http2.Server{}),
		ReadHeaderTimeout: 5 * time.Second,
	}
}
