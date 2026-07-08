package gateway

import (
	"context"
	"log/slog"
)

//encore:service
type Service struct {
	server *QuilkinUDPServer
}

func initService() (*Service, error) {
	cfg, err := LoadConfig()
	if err != nil {
		return nil, err
	}
	if !cfg.QuilkinUDPEnabled {
		return &Service{}, nil
	}
	server := NewQuilkinUDPServer(cfg, EncoreMarketClient{})
	go func() {
		if err := server.ListenAndServe(context.Background()); err != nil {
			slog.Error("quilkin udp listener stopped", "error", err)
		}
	}()
	return &Service{server: server}, nil
}

type HealthResponse struct {
	Status string `json:"status"`
}

//encore:api public method=GET path=/gateway/healthz
func (s *Service) GatewayHealth(ctx context.Context) (*HealthResponse, error) {
	return &HealthResponse{Status: "ok"}, nil
}

//encore:api public method=GET path=/gateway/readyz
func (s *Service) GatewayReady(ctx context.Context) (*HealthResponse, error) {
	if s.server == nil {
		return &HealthResponse{Status: "ready"}, nil
	}
	return &HealthResponse{Status: "ready"}, nil
}
