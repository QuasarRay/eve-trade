package distributedbackend

import (
	"net/http"
	"time"

	"connectrpc.com/connect"
	marketv1connect "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1/marketv1connect"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
)

func NewHTTPServer(config Config, handler *MarketHandler, handlerOptions ...connect.HandlerOption) *http.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", healthHandler)
	mux.HandleFunc("/readyz", healthHandler)

	path, serviceHandler := marketv1connect.NewMarketServiceHandler(handler, handlerOptions...)
	mux.Handle(path, serviceHandler)

	return &http.Server{
		Addr:              config.HTTPAddr,
		Handler:           h2c.NewHandler(mux, &http2.Server{}),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
}

func healthHandler(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet && request.Method != http.MethodHead {
		response.Header().Set("Allow", http.MethodGet+", "+http.MethodHead)
		http.Error(response, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	response.Header().Set("Content-Type", "text/plain; charset=utf-8")
	response.WriteHeader(http.StatusOK)
	if request.Method == http.MethodGet {
		_, _ = response.Write([]byte("ok\n"))
	}
}
