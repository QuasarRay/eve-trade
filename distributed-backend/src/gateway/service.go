package gateway

import (
	"context"
	"log/slog"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/market"
)

//encore:service
type Service struct {
	server      *QuilkinUDPServer
	marketReady func(context.Context) error
}

func initService() (*Service, error) {
	cfg, err := LoadConfig()
	if err != nil {
		return nil, err
	}
	if !cfg.QuilkinUDPEnabled {
		return &Service{marketReady: marketReadiness}, nil
	}
	server := NewQuilkinUDPServer(cfg, EncoreMarketClient{})
	go func() {
		if err := server.ListenAndServe(context.Background()); err != nil {
			slog.Error("quilkin udp listener stopped", "error", err)
		}
	}()
	return &Service{server: server, marketReady: marketReadiness}, nil
}

type HealthResponse struct {
	Status string `json:"status"`
}

//encore:api public method=GET path=/gateway/healthz
func (s *Service) GatewayHealth(ctx context.Context) (*HealthResponse, error) {
	if s.server != nil && s.server.Failed() {
		return nil, errs.B().Code(errs.Unavailable).Msg("quilkin UDP listener failed").Err()
	}
	return &HealthResponse{Status: "ok"}, nil
}

//encore:api public method=GET path=/gateway/readyz
func (s *Service) GatewayReady(ctx context.Context) (*HealthResponse, error) {
	if s.server == nil {
		return &HealthResponse{Status: "ready"}, nil
	}
	if !s.server.Ready() {
		return nil, errs.B().Code(errs.Unavailable).Msg("quilkin UDP listener is not ready").Err()
	}
	if s.marketReady != nil {
		if err := s.marketReady(ctx); err != nil {
			return nil, errs.WrapCode(err, errs.Unavailable, "market is not ready")
		}
	}
	return &HealthResponse{Status: "ready"}, nil
}

func marketReadiness(ctx context.Context) error {
	_, err := market.MarketReady(ctx)
	return err
}
