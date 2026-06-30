package main

import (
	"net/http"
	"sync/atomic"
	"time"
)

type HealthStatus struct {
	ready atomic.Bool
}

func (s *HealthStatus) SetReady(ready bool) {
	s.ready.Store(ready)
}

func (s *HealthStatus) Ready() bool {
	return s.ready.Load()
}

func NewHealthServer(addr string, status *HealthStatus) *http.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", healthHandler)
	mux.HandleFunc("/readyz", readyHandler(status))

	return &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       5 * time.Second,
		WriteTimeout:      5 * time.Second,
		IdleTimeout:       30 * time.Second,
	}
}

func healthHandler(response http.ResponseWriter, request *http.Request) {
	writePlainStatus(response, request, http.StatusOK, "ok\n")
}

func readyHandler(status *HealthStatus) http.HandlerFunc {
	return func(response http.ResponseWriter, request *http.Request) {
		if status != nil && status.Ready() {
			writePlainStatus(response, request, http.StatusOK, "ready\n")
			return
		}
		writePlainStatus(response, request, http.StatusServiceUnavailable, "not ready\n")
	}
}

func writePlainStatus(response http.ResponseWriter, request *http.Request, statusCode int, body string) {
	if request.Method != http.MethodGet && request.Method != http.MethodHead {
		response.Header().Set("Allow", http.MethodGet+", "+http.MethodHead)
		http.Error(response, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	response.Header().Set("Content-Type", "text/plain; charset=utf-8")
	response.WriteHeader(statusCode)
	if request.Method == http.MethodGet {
		_, _ = response.Write([]byte(body))
	}
}
