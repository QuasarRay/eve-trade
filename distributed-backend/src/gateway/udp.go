package gateway

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

type QuilkinUDPServer struct {
	addr          string
	maxPacket     int
	timeout       time.Duration
	workers       int
	queueDepth    int
	authRequired  bool
	hmacSecret    []byte
	hmacKeyID     string
	principals    map[string]UDPPrincipalCredential
	market        MarketClient
	listenFunc    func(network string, address string) (net.PacketConn, error)
	rateLimiter   *remoteRateLimiter
	sourceLimiter *remoteRateLimiter
	replayCache   *interactionReplayCache
	ready         atomic.Bool
	failed        atomic.Bool
}

type udpPacketJob struct {
	remote net.Addr
	packet []byte
}

func NewQuilkinUDPServer(config Config, market MarketClient) *QuilkinUDPServer {
	return &QuilkinUDPServer{
		addr:          config.QuilkinUDPAddr,
		maxPacket:     config.QuilkinMaxPacket,
		timeout:       config.DownstreamTimeout,
		workers:       config.QuilkinWorkers,
		queueDepth:    config.QuilkinQueueDepth,
		authRequired:  config.UDPAuthRequired,
		hmacSecret:    []byte(config.UDPHMACSecret),
		hmacKeyID:     config.UDPHMACKeyID,
		principals:    config.UDPPrincipalKeys,
		market:        market,
		listenFunc:    net.ListenPacket,
		rateLimiter:   newBoundedRemoteRateLimiter(config.UDPRatePerSecond, config.UDPRateBurst, config.UDPLimiterMaxIDs, config.UDPLimiterIdleTTL),
		sourceLimiter: newBoundedRemoteRateLimiter(config.UDPSourceRate, config.UDPSourceBurst, config.UDPLimiterMaxIDs, config.UDPLimiterIdleTTL),
		replayCache:   newInteractionReplayCache(config.UDPReplayTTL, config.UDPReplayMaxIDs),
	}
}

func (s *QuilkinUDPServer) ListenAndServe(ctx context.Context) (serveErr error) {
	s.ready.Store(false)
	s.failed.Store(false)
	defer func() {
		if serveErr != nil && ctx.Err() == nil {
			s.failed.Store(true)
		}
	}()
	if err := validateListenerConfig(s.maxPacket, s.workers, s.queueDepth); err != nil {
		return err
	}

	conn, err := s.listenFunc("udp", s.addr)
	if err != nil {
		return fmt.Errorf("listen for Quilkin UDP packets on %s: %w", s.addr, err)
	}
	s.ready.Store(true)
	defer func() {
		s.ready.Store(false)
		if closeErr := conn.Close(); closeErr != nil {
			slog.Warn("quilkin udp close failed", "error", closeErr)
		}
	}()

	go func() {
		<-ctx.Done()
		_ = conn.Close()
	}()

	jobs := make(chan udpPacketJob, s.queueDepth)
	var workers sync.WaitGroup
	for i := 0; i < s.workers; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			s.worker(ctx, conn, jobs)
		}()
	}
	defer func() {
		close(jobs)
		workers.Wait()
	}()

	buffer := make([]byte, s.maxPacket+1)
	for {
		n, remote, err := conn.ReadFrom(buffer)
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			if errors.Is(err, net.ErrClosed) {
				return fmt.Errorf("read Quilkin UDP packet: listener closed")
			}
			return fmt.Errorf("read Quilkin UDP packet: %w", err)
		}

		recordUDPPacket(ctx, "received", n)

		if n > s.maxPacket {
			slog.Warn("udp packet rejected", "reason", "packet_too_large", "remote", remoteKey(remote), "bytes", n, "max_packet_bytes", s.maxPacket)
			recordUDPPacket(ctx, "packet_too_large", n)
			s.writeError(conn, remote, bestEffortInteractionID(buffer[:n]), "packet_too_large", "packet too large")
			continue
		}
		packet := append([]byte(nil), buffer[:n]...)
		select {
		case jobs <- udpPacketJob{remote: remote, packet: packet}:
		default:
			slog.Warn("udp packet queue full", "remote", remoteKey(remote), "queue_depth", s.queueDepth)
			recordUDPPacket(ctx, "queue_full", n)
			s.writeError(conn, remote, bestEffortInteractionID(packet), "queue_full", "temporarily overloaded")
		}
	}
}

func (s *QuilkinUDPServer) Ready() bool {
	return s.ready.Load()
}

func (s *QuilkinUDPServer) Failed() bool {
	return s.failed.Load()
}

func (s *QuilkinUDPServer) worker(ctx context.Context, conn net.PacketConn, jobs <-chan udpPacketJob) {
	for job := range jobs {
		// Once the listener admits a packet, shutdown drains it under the normal
		// per-request timeout instead of abandoning it because the listener context
		// was cancelled.
		s.handlePacket(context.WithoutCancel(ctx), conn, job.remote, job.packet)
	}
}
