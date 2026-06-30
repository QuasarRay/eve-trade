package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthAndReadinessEndpointsAreStrict(t *testing.T) {
	status := &HealthStatus{}
	server := NewHealthServer(":0", status)

	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	response := httptest.NewRecorder()
	server.Handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK || response.Body.String() != "ok\n" {
		t.Fatalf("health response = %d %q", response.Code, response.Body.String())
	}

	request = httptest.NewRequest(http.MethodGet, "/readyz", nil)
	response = httptest.NewRecorder()
	server.Handler.ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("not-ready status = %d", response.Code)
	}

	status.SetReady(true)
	response = httptest.NewRecorder()
	server.Handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK || !status.Ready() {
		t.Fatalf("ready response = %d", response.Code)
	}

	request = httptest.NewRequest(http.MethodPost, "/healthz", nil)
	response = httptest.NewRecorder()
	server.Handler.ServeHTTP(response, request)
	if response.Code != http.StatusMethodNotAllowed || response.Header().Get("Allow") != "GET, HEAD" {
		t.Fatalf("method response = %d, Allow=%q", response.Code, response.Header().Get("Allow"))
	}
}

func TestNilHealthStatusIsNotReady(t *testing.T) {
	response := httptest.NewRecorder()
	readyHandler(nil).ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("nil status reported ready: %d", response.Code)
	}
}
