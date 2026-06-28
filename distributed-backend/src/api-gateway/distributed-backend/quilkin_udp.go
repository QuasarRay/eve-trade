package distributedbackend

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"strings"
	"sync"
	"time"

	"connectrpc.com/connect"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/trace"
	"google.golang.org/protobuf/encoding/protojson"
)

const edgeEnvelopeSchema = "eve-trade-edge.v1"

var (
	udpMeter           = otel.Meter("github.com/QuasarRay/eve-trade/api-gateway/udp")
	udpTracer          = otel.Tracer("github.com/QuasarRay/eve-trade/api-gateway/udp")
	udpPacketCounter   metric.Int64Counter
	udpPacketBytes     metric.Int64Histogram
	udpDownstreamCalls metric.Float64Histogram
)

func init() {
	var err error
	udpPacketCounter, err = udpMeter.Int64Counter("eve_trade_api_gateway_udp_packets_total")
	if err != nil {
		slog.Warn("create udp packet counter failed", "error", err)
	}
	udpPacketBytes, err = udpMeter.Int64Histogram("eve_trade_api_gateway_udp_packet_bytes")
	if err != nil {
		slog.Warn("create udp packet size histogram failed", "error", err)
	}
	udpDownstreamCalls, err = udpMeter.Float64Histogram("eve_trade_api_gateway_udp_downstream_seconds")
	if err != nil {
		slog.Warn("create udp downstream histogram failed", "error", err)
	}
}

type QuilkinUDPServer struct {
	addr         string
	maxPacket    int
	timeout      time.Duration
	workers      int
	queueDepth   int
	authRequired bool
	hmacSecret   []byte
	hmacKeyID    string
	market       MarketClient
	listenFunc   func(network string, address string) (net.PacketConn, error)
	rateLimiter  *remoteRateLimiter
	replayCache  *interactionReplayCache
}

type udpPacketJob struct {
	remote net.Addr
	packet []byte
}

type edgeEnvelope struct {
	SchemaVersion string          `json:"schema_version"`
	Payload       json.RawMessage `json:"payload"`
	Auth          *edgeAuth       `json:"auth"`
}

type edgeAuth struct {
	Algorithm string `json:"algorithm"`
	KeyID     string `json:"key_id"`
	Signature string `json:"signature"`
}

type guiPacketHeader struct {
	SchemaVersion string `json:"schema_version"`
	InteractionID string `json:"interaction_id"`
}

func NewQuilkinUDPServer(config Config, market MarketClient) *QuilkinUDPServer {
	return &QuilkinUDPServer{
		addr:         config.QuilkinUDPAddr,
		maxPacket:    config.QuilkinMaxPacket,
		timeout:      config.DownstreamTimeout,
		workers:      config.QuilkinWorkers,
		queueDepth:   config.QuilkinQueueDepth,
		authRequired: config.UDPAuthRequired,
		hmacSecret:   []byte(config.UDPHMACSecret),
		hmacKeyID:    config.UDPHMACKeyID,
		market:       market,
		listenFunc:   net.ListenPacket,
		rateLimiter:  newRemoteRateLimiter(config.UDPRatePerSecond, config.UDPRateBurst),
		replayCache:  newInteractionReplayCache(config.UDPReplayTTL),
	}
}

func (s *QuilkinUDPServer) ListenAndServe(ctx context.Context) error {
	if s.maxPacket <= 0 {
		return fmt.Errorf("max UDP packet size must be positive")
	}
	if s.workers <= 0 {
		return fmt.Errorf("UDP worker count must be positive")
	}
	if s.queueDepth <= 0 {
		return fmt.Errorf("UDP queue depth must be positive")
	}

	conn, err := s.listenFunc("udp", s.addr)
	if err != nil {
		return fmt.Errorf("listen for Quilkin UDP packets on %s: %w", s.addr, err)
	}
	defer func() {
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
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) {
				return nil
			}
			return fmt.Errorf("read Quilkin UDP packet: %w", err)
		}

		slog.Info("udp packet received", "remote", remoteKey(remote), "bytes", n)
		recordUDPPacket(ctx, "received", n)

		if n > s.maxPacket {
			slog.Warn("udp packet rejected", "reason", "packet_too_large", "remote", remoteKey(remote), "bytes", n, "max_packet_bytes", s.maxPacket)
			recordUDPPacket(ctx, "packet_too_large", n)
			s.writeError(conn, remote, "packet_too_large", "packet too large")
			continue
		}
		if !s.allowRemote(remote) {
			slog.Warn("udp packet rate limited", "remote", remoteKey(remote))
			recordUDPPacket(ctx, "rate_limited", n)
			s.writeError(conn, remote, "rate_limited", "rate limited")
			continue
		}

		packet := append([]byte(nil), buffer[:n]...)
		select {
		case jobs <- udpPacketJob{remote: remote, packet: packet}:
		default:
			slog.Warn("udp packet queue full", "remote", remoteKey(remote), "queue_depth", s.queueDepth)
			recordUDPPacket(ctx, "queue_full", n)
			s.writeError(conn, remote, "queue_full", "temporarily overloaded")
		}
	}
}

func (s *QuilkinUDPServer) worker(ctx context.Context, conn net.PacketConn, jobs <-chan udpPacketJob) {
	for {
		select {
		case <-ctx.Done():
			return
		case job, ok := <-jobs:
			if !ok {
				return
			}
			s.handlePacket(ctx, conn, job.remote, job.packet)
		}
	}
}

func (s *QuilkinUDPServer) handlePacket(parent context.Context, conn net.PacketConn, remote net.Addr, packet []byte) {
	ctx, receiveSpan := udpTracer.Start(parent, "gateway.receive_ui_activity", trace.WithAttributes(
		attribute.Int("network.packet.size", len(packet)),
		attribute.String("network.transport", "udp"),
	))
	defer receiveSpan.End()

	if len(packet) == 0 {
		receiveSpan.SetAttributes(attribute.String("validation.result", "rejected"), attribute.String("rejection.reason", "empty_packet"))
		slog.Warn("udp packet rejected", "reason", "empty_packet", "remote", remoteKey(remote))
		recordUDPPacket(parent, "empty_packet", 0)
		s.writeError(conn, remote, "empty_packet", "empty packet")
		return
	}

	rawPayload, interactionID, err := s.authenticatedPayload(packet)
	if err != nil {
		receiveSpan.RecordError(err)
		receiveSpan.SetAttributes(attribute.String("validation.result", "rejected"), attribute.String("rejection.reason", err.Code))
		slog.Warn("udp packet rejected", "reason", err.Code, "remote", remoteKey(remote))
		recordUDPPacket(parent, err.Code, len(packet))
		s.writeError(conn, remote, err.Code, err.ClientMessage)
		return
	}
	if interactionID == "" {
		receiveSpan.SetAttributes(attribute.String("validation.result", "rejected"), attribute.String("rejection.reason", "missing_interaction_id"))
		slog.Warn("udp packet rejected", "reason", "missing_interaction_id", "remote", remoteKey(remote))
		recordUDPPacket(parent, "missing_interaction_id", len(packet))
		s.writeError(conn, remote, "missing_interaction_id", "missing interaction_id")
		return
	}

	fingerprint := sha256.Sum256(rawPayload)
	replayState, cachedResponse := s.replay().begin(interactionID, fingerprint)
	switch replayState {
	case replayCached:
		receiveSpan.SetAttributes(attribute.String("interaction_id", interactionID), attribute.String("validation.result", "cached"))
		slog.Info("udp retry served from response cache", "remote", remoteKey(remote), "interaction_id", interactionID)
		recordUDPPacket(parent, "cached", len(packet))
		if writeErr := s.writeResponse(conn, remote, interactionID, cachedResponse); writeErr != nil {
			recordUDPPacket(parent, "write_failed", len(packet))
		}
		return
	case replayInFlight:
		receiveSpan.SetAttributes(attribute.String("interaction_id", interactionID), attribute.String("validation.result", "retry_later"))
		slog.Info("udp retry is already in progress", "remote", remoteKey(remote), "interaction_id", interactionID)
		recordUDPPacket(parent, "request_in_progress", len(packet))
		s.writeError(conn, remote, "request_in_progress", "request is still in progress")
		return
	case replayConflict:
		receiveSpan.SetAttributes(attribute.String("interaction_id", interactionID), attribute.String("validation.result", "rejected"), attribute.String("rejection.reason", "replay_payload_mismatch"))
		slog.Warn("udp replay payload mismatch rejected", "remote", remoteKey(remote), "interaction_id", interactionID)
		recordUDPPacket(parent, "replay", len(packet))
		s.writeError(conn, remote, "replay", "interaction_id was already used with a different payload")
		return
	}

	receiveSpan.SetAttributes(attribute.String("interaction_id", interactionID), attribute.String("validation.result", "accepted"))
	ctx, cancel := context.WithTimeout(ctx, s.timeout)
	defer cancel()

	start := time.Now()
	forwardCtx, forwardSpan := udpTracer.Start(ctx, "gateway.forward_to_market", trace.WithAttributes(attribute.String("interaction_id", interactionID)))
	response, callErr := s.market.SubmitTradeGuiInteraction(forwardCtx, &marketv1.SubmitTradeGuiInteractionRequest{
		RawPayload: rawPayload,
	})
	if callErr != nil {
		forwardSpan.RecordError(callErr)
		forwardSpan.SetAttributes(attribute.String("error.kind", stableDownstreamCode(callErr)))
	}
	forwardSpan.End()
	elapsed := time.Since(start)
	recordUDPDownstream(ctx, elapsed, callErr)
	if callErr != nil {
		code := stableDownstreamCode(callErr)
		slog.Warn("udp downstream call failed", "remote", remoteKey(remote), "interaction_id", interactionID, "code", code, "duration_ms", elapsed.Milliseconds())
		recordUDPPacket(parent, code, len(packet))
		if isRetryableDownstreamCode(code) {
			s.replay().release(interactionID, fingerprint)
			s.writeError(conn, remote, code, stableDownstreamMessage(callErr))
		} else {
			s.writeCachedError(conn, remote, interactionID, fingerprint, code, stableDownstreamMessage(callErr))
		}
		return
	}

	body, marshalErr := protojson.MarshalOptions{UseProtoNames: true}.Marshal(response)
	if marshalErr != nil {
		slog.Error("udp response marshal failed", "interaction_id", interactionID, "error", marshalErr)
		recordUDPPacket(parent, "internal", len(packet))
		s.writeCachedError(conn, remote, interactionID, fingerprint, "internal", "internal error")
		return
	}

	s.replay().complete(interactionID, fingerprint, body)
	if writeErr := s.writeResponse(conn, remote, interactionID, body); writeErr != nil {
		recordUDPPacket(parent, "write_failed", len(packet))
		return
	}

	slog.Info("udp downstream call succeeded", "remote", remoteKey(remote), "interaction_id", interactionID, "duration_ms", elapsed.Milliseconds())
	recordUDPPacket(parent, "success", len(packet))
}

type packetRejection struct {
	Code          string
	ClientMessage string
}

func (e *packetRejection) Error() string {
	return e.Code
}

func reject(code string, message string) *packetRejection {
	return &packetRejection{Code: code, ClientMessage: message}
}

func (s *QuilkinUDPServer) authenticatedPayload(packet []byte) ([]byte, string, *packetRejection) {
	if !s.authRequired && len(s.hmacSecret) == 0 {
		interactionID, err := extractInteractionID(packet)
		if err != nil {
			return nil, "", reject("malformed_packet", "malformed packet")
		}
		return packet, interactionID, nil
	}
	if len(s.hmacSecret) == 0 {
		return nil, "", reject("auth_not_configured", "authentication unavailable")
	}

	var envelope edgeEnvelope
	decoder := json.NewDecoder(bytes.NewReader(packet))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&envelope); err != nil {
		return nil, "", reject("malformed_packet", "malformed packet")
	}
	if envelope.SchemaVersion != edgeEnvelopeSchema {
		return nil, "", reject("unsupported_envelope", "unsupported packet envelope")
	}
	if len(envelope.Payload) == 0 {
		return nil, "", reject("empty_payload", "empty payload")
	}
	if envelope.Auth == nil {
		return nil, "", reject("missing_signature", "missing signature")
	}
	if envelope.Auth.Algorithm != "hmac-sha256" {
		return nil, "", reject("unsupported_signature", "unsupported signature")
	}
	if s.hmacKeyID != "" && envelope.Auth.KeyID != s.hmacKeyID {
		return nil, "", reject("invalid_signature", "invalid signature")
	}

	rawPayload, err := compactJSON(envelope.Payload)
	if err != nil {
		return nil, "", reject("malformed_packet", "malformed packet")
	}
	signature, err := base64.RawURLEncoding.DecodeString(envelope.Auth.Signature)
	if err != nil {
		return nil, "", reject("invalid_signature", "invalid signature")
	}
	mac := hmac.New(sha256.New, s.hmacSecret)
	_, _ = mac.Write(rawPayload)
	if !hmac.Equal(signature, mac.Sum(nil)) {
		return nil, "", reject("invalid_signature", "invalid signature")
	}
	interactionID, err := extractInteractionID(rawPayload)
	if err != nil {
		return nil, "", reject("malformed_packet", "malformed packet")
	}
	return rawPayload, interactionID, nil
}

func compactJSON(body []byte) ([]byte, error) {
	var buffer bytes.Buffer
	if err := json.Compact(&buffer, body); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}

func extractInteractionID(rawPayload []byte) (string, error) {
	var header guiPacketHeader
	decoder := json.NewDecoder(bytes.NewReader(rawPayload))
	if err := decoder.Decode(&header); err != nil {
		return "", err
	}
	if header.SchemaVersion != "eve-trade-gui.v1" {
		return "", nil
	}
	return strings.TrimSpace(header.InteractionID), nil
}

func (s *QuilkinUDPServer) writeError(conn net.PacketConn, remote net.Addr, code string, message string) {
	body, _ := json.Marshal(map[string]string{
		"code":    code,
		"message": message,
	})
	_ = s.writeResponse(conn, remote, "", body)
}

func (s *QuilkinUDPServer) writeCachedError(conn net.PacketConn, remote net.Addr, interactionID string, fingerprint [sha256.Size]byte, code string, message string) {
	body, _ := json.Marshal(map[string]string{
		"code":    code,
		"message": message,
	})
	s.replay().complete(interactionID, fingerprint, body)
	_ = s.writeResponse(conn, remote, interactionID, body)
}

func (s *QuilkinUDPServer) writeResponse(conn net.PacketConn, remote net.Addr, interactionID string, body []byte) error {
	if _, writeErr := conn.WriteTo(body, remote); writeErr != nil {
		slog.Warn("udp response write failed", "remote", remoteKey(remote), "interaction_id", interactionID, "error", writeErr)
		return writeErr
	}
	return nil
}

func (s *QuilkinUDPServer) allowRemote(remote net.Addr) bool {
	if s.rateLimiter == nil {
		return true
	}
	return s.rateLimiter.allow(remoteKey(remote))
}

func (s *QuilkinUDPServer) replay() *interactionReplayCache {
	if s.replayCache == nil {
		s.replayCache = newInteractionReplayCache(10 * time.Minute)
	}
	return s.replayCache
}

func stableDownstreamCode(err error) string {
	switch connect.CodeOf(err) {
	case connect.CodeDeadlineExceeded:
		return "downstream_timeout"
	case connect.CodeInvalidArgument:
		return "invalid_argument"
	case connect.CodeFailedPrecondition:
		return "failed_precondition"
	case connect.CodePermissionDenied:
		return "permission_denied"
	case connect.CodeAborted:
		return "aborted"
	case connect.CodeUnavailable:
		return "downstream_unavailable"
	default:
		return "downstream_error"
	}
}

func stableDownstreamMessage(err error) string {
	switch connect.CodeOf(err) {
	case connect.CodeDeadlineExceeded:
		return "downstream timeout"
	case connect.CodeInvalidArgument, connect.CodeFailedPrecondition, connect.CodePermissionDenied, connect.CodeAborted:
		return sanitizeClientMessage(err.Error())
	case connect.CodeUnavailable:
		return "downstream unavailable"
	default:
		return "downstream error"
	}
}

func isRetryableDownstreamCode(code string) bool {
	return code == "downstream_timeout" || code == "downstream_unavailable"
}

func sanitizeClientMessage(message string) string {
	message = strings.ReplaceAll(message, "\n", " ")
	message = strings.ReplaceAll(message, "\r", " ")
	message = strings.TrimSpace(message)
	if len(message) > 240 {
		return message[:240]
	}
	return message
}

func remoteKey(remote net.Addr) string {
	if remote == nil {
		return ""
	}
	host, _, err := net.SplitHostPort(remote.String())
	if err == nil {
		return host
	}
	return remote.String()
}

func recordUDPPacket(ctx context.Context, outcome string, bytes int) {
	if udpPacketCounter != nil {
		udpPacketCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("outcome", outcome)))
	}
	if udpPacketBytes != nil && bytes >= 0 {
		udpPacketBytes.Record(ctx, int64(bytes), metric.WithAttributes(attribute.String("outcome", outcome)))
	}
}

func recordUDPDownstream(ctx context.Context, elapsed time.Duration, err error) {
	if udpDownstreamCalls == nil {
		return
	}
	outcome := "success"
	if err != nil {
		outcome = stableDownstreamCode(err)
	}
	udpDownstreamCalls.Record(ctx, elapsed.Seconds(), metric.WithAttributes(attribute.String("outcome", outcome)))
}

type remoteRateLimiter struct {
	mu      sync.Mutex
	rate    float64
	burst   float64
	buckets map[string]*tokenBucket
	now     func() time.Time
}

type tokenBucket struct {
	tokens  float64
	updated time.Time
}

func newRemoteRateLimiter(ratePerSecond float64, burst int) *remoteRateLimiter {
	if ratePerSecond <= 0 || burst <= 0 {
		return nil
	}
	return &remoteRateLimiter{
		rate:    ratePerSecond,
		burst:   float64(burst),
		buckets: make(map[string]*tokenBucket),
		now:     time.Now,
	}
}

func (l *remoteRateLimiter) allow(key string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()

	now := l.now()
	bucket := l.buckets[key]
	if bucket == nil {
		l.buckets[key] = &tokenBucket{tokens: l.burst - 1, updated: now}
		return true
	}
	elapsed := now.Sub(bucket.updated).Seconds()
	bucket.updated = now
	bucket.tokens = minFloat(l.burst, bucket.tokens+elapsed*l.rate)
	if bucket.tokens < 1 {
		return false
	}
	bucket.tokens--
	return true
}

func minFloat(a float64, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

type interactionReplayCache struct {
	mu    sync.Mutex
	ttl   time.Duration
	seen  map[string]interactionReplayEntry
	now   func() time.Time
	sweep time.Time
}

type replayDisposition uint8

const (
	replayNew replayDisposition = iota
	replayInFlight
	replayCached
	replayConflict
)

type interactionReplayEntry struct {
	fingerprint [sha256.Size]byte
	response    []byte
	expiresAt   time.Time
}

func newInteractionReplayCache(ttl time.Duration) *interactionReplayCache {
	if ttl <= 0 {
		ttl = 10 * time.Minute
	}
	return &interactionReplayCache{
		ttl:  ttl,
		seen: make(map[string]interactionReplayEntry),
		now:  time.Now,
	}
}

func (c *interactionReplayCache) begin(interactionID string, fingerprint [sha256.Size]byte) (replayDisposition, []byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	now := c.now()
	if now.After(c.sweep) {
		for id, entry := range c.seen {
			if !entry.expiresAt.After(now) {
				delete(c.seen, id)
			}
		}
		c.sweep = now.Add(c.ttl / 2)
	}
	if entry, ok := c.seen[interactionID]; ok && entry.expiresAt.After(now) {
		if entry.fingerprint != fingerprint {
			return replayConflict, nil
		}
		if entry.response == nil {
			return replayInFlight, nil
		}
		return replayCached, append([]byte(nil), entry.response...)
	}
	c.seen[interactionID] = interactionReplayEntry{
		fingerprint: fingerprint,
		expiresAt:   now.Add(c.ttl),
	}
	return replayNew, nil
}

func (c *interactionReplayCache) complete(interactionID string, fingerprint [sha256.Size]byte, response []byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	entry, ok := c.seen[interactionID]
	if !ok || entry.fingerprint != fingerprint {
		return
	}
	entry.response = append([]byte(nil), response...)
	entry.expiresAt = c.now().Add(c.ttl)
	c.seen[interactionID] = entry
}

func (c *interactionReplayCache) release(interactionID string, fingerprint [sha256.Size]byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	entry, ok := c.seen[interactionID]
	if ok && entry.fingerprint == fingerprint {
		delete(c.seen, interactionID)
	}
}
