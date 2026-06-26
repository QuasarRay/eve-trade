package distributedbackend

import (
	"context"
	"fmt"
	"net/http"
	"time"
)

type ReadinessCheck func(context.Context) error

func NewHTTPServer(config Config, readiness ReadinessCheck) *http.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", healthHandler)
	mux.HandleFunc("/readyz", readyHandler(readiness))

	return &http.Server{
		Addr:              config.HTTPAddr,
		Handler:           mux,
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
