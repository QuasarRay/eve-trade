package main

import (
	"net/http"
	"time"
)

func NewHealthServer(addr string) *http.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", healthHandler)
	mux.HandleFunc("/readyz", healthHandler)

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
