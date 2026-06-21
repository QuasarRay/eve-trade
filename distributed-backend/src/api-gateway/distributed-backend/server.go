package distributedbackend

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"connectrpc.com/connect"
	api_gatewayv1connect "github.com/QuasarRay/eve-trade/proto/gen/eve/api_gateway/v1/api_gatewayv1connect"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
)

type ReadinessCheck func(context.Context) error

func NewHTTPServer(config Config, handler *GatewayHandler, readiness ReadinessCheck, handlerOptions ...connect.HandlerOption) *http.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", healthHandler)
	mux.HandleFunc("/readyz", readyHandler(readiness))

	path, serviceHandler := api_gatewayv1connect.NewGameTradeGatewayServiceHandler(handler, handlerOptions...)
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

func readyHandler(check ReadinessCheck) http.HandlerFunc {
	return func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet && request.Method != http.MethodHead {
			response.Header().Set("Allow", http.MethodGet+", "+http.MethodHead)
			http.Error(response, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		if check == nil {
			writePlainStatus(response, request, http.StatusOK, "ready\n")
			return
		}

		ctx, cancel := context.WithTimeout(request.Context(), 2*time.Second)
		defer cancel()
		if err := check(ctx); err != nil {
			writePlainStatus(response, request, http.StatusServiceUnavailable, fmt.Sprintf("not ready: %v\n", err))
			return
		}
		writePlainStatus(response, request, http.StatusOK, "ready\n")
	}
}

func healthHandler(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet && request.Method != http.MethodHead {
		response.Header().Set("Allow", http.MethodGet+", "+http.MethodHead)
		http.Error(response, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	writePlainStatus(response, request, http.StatusOK, "ok\n")
}

func writePlainStatus(response http.ResponseWriter, request *http.Request, status int, body string) {
	response.Header().Set("Content-Type", "text/plain; charset=utf-8")
	response.WriteHeader(status)
	if request.Method == http.MethodGet {
		_, _ = response.Write([]byte(body))
	}
}
