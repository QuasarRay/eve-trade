package gateway

import (
	"context"
	"fmt"
	"log/slog"
	"sync"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/market"
)

//encore:service
type Service struct {
	server      *QuilkinUDPServer
	marketReady func(context.Context) error
	lifecycleMu sync.Mutex
	lifecycle   listenerLifecycle
}

type listenerLifecycle interface {
	Serve(context.Context, func(context.Context) error) error
	Shutdown(context.Context) error
	Errors() <-chan error
}

type udpListenerLifecycle struct {
	mu          sync.Mutex
	cancelServe context.CancelFunc
	serveDone   chan struct{}
	serveErr    error
	serving     bool
	listenerErr chan error
}

//lint:ignore U1000 Encore invokes this initializer through generated service wiring.
func initService() (*Service, error) {
	cfg, err := LoadConfig()
	if err != nil {
		return nil, err
	}
	if !cfg.QuilkinUDPEnabled {
		return &Service{marketReady: marketReadiness}, nil
	}
	server := NewQuilkinUDPServer(cfg, EncoreMarketClient{})
	service := &Service{server: server, marketReady: marketReadiness}
	go func() {
		if err := service.Serve(context.Background()); err != nil {
			slog.Error("quilkin udp listener stopped", "error", err)
		}
	}()
	return service, nil
}

func (s *Service) Serve(parent context.Context) error {
	if s.server == nil {
		return nil
	}
	return s.listenerLifecycle().Serve(parent, s.server.ListenAndServe)
}

func (l *udpListenerLifecycle) Serve(parent context.Context, serve func(context.Context) error) error {
	l.mu.Lock()
	if l.serving {
		l.mu.Unlock()
		return fmt.Errorf("gateway UDP listener is already serving")
	}
	serveCtx, cancel := context.WithCancel(parent)
	l.cancelServe = cancel
	l.serveDone = make(chan struct{})
	l.serveErr = nil
	l.serving = true
	done := l.serveDone
	listenerErr := l.listenerErr
	l.mu.Unlock()

	err := serve(serveCtx)
	cancel()
	if err != nil {
		select {
		case listenerErr <- err:
		default:
		}
	}
	l.mu.Lock()
	l.serveErr = err
	l.serving = false
	l.cancelServe = nil
	close(done)
	l.mu.Unlock()
	return err
}

func (s *Service) Shutdown(ctx context.Context) error {
	return s.listenerLifecycle().Shutdown(ctx)
}

func (l *udpListenerLifecycle) Shutdown(ctx context.Context) error {
	l.mu.Lock()
	if !l.serving {
		err := l.serveErr
		l.mu.Unlock()
		return err
	}
	cancel := l.cancelServe
	done := l.serveDone
	l.mu.Unlock()

	cancel()
	select {
	case <-done:
		l.mu.Lock()
		err := l.serveErr
		l.mu.Unlock()
		return err
	case <-ctx.Done():
		return fmt.Errorf("shut down gateway UDP listener: %w", ctx.Err())
	}
}

func (s *Service) ListenerErrors() <-chan error {
	return s.listenerLifecycle().Errors()
}

func (l *udpListenerLifecycle) Errors() <-chan error {
	return l.listenerErr
}

func (s *Service) listenerLifecycle() listenerLifecycle {
	s.lifecycleMu.Lock()
	defer s.lifecycleMu.Unlock()
	if s.lifecycle == nil {
		s.lifecycle = &udpListenerLifecycle{listenerErr: make(chan error, 1)}
	}
	return s.lifecycle
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

//lint:ignore U1000 Referenced by the Service constructed through Encore-generated initialization.
func marketReadiness(ctx context.Context) error {
	_, err := market.MarketReady(ctx)
	return err
}
