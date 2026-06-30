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

const (
	edgeEnvelopeSchema         = "eve-trade-edge.v1"
	edgeResponseEnvelopeSchema = "eve-trade-edge-response.v1"
)

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
	principals   map[string]UDPPrincipalCredential
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
		principals:   config.UDPPrincipalKeys,
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

func (s *QuilkinUDPServer) worker(ctx context.Context, conn net.PacketConn, jobs <-chan udpPacketJob) {
	for job := range jobs {
		// Once the listener admits a packet, shutdown drains it under the normal
		// per-request timeout instead of abandoning it because the listener context
		// was cancelled.
		s.handlePacket(context.WithoutCancel(ctx), conn, job.remote, job.packet)
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
		s.writeError(conn, remote, "", "empty_packet", "empty packet")
		return
	}

	interactionID := bestEffortInteractionID(packet)
	rawPayload, authenticatedInteractionID, principalID, err := s.authenticatedPayload(packet)
	if authenticatedInteractionID != "" {
		interactionID = authenticatedInteractionID
	}
	if err != nil {
		receiveSpan.RecordError(err)
		receiveSpan.SetAttributes(attribute.String("validation.result", "rejected"), attribute.String("rejection.reason", err.Code))
		slog.Warn("udp packet rejected", "reason", err.Code, "remote", remoteKey(remote))
		recordUDPPacket(parent, err.Code, len(packet))
		s.writeError(conn, remote, interactionID, err.Code, err.ClientMessage)
		return
	}
	if !s.allowPrincipal(principalID, remote) {
		receiveSpan.SetAttributes(attribute.String("validation.result", "rejected"), attribute.String("rejection.reason", "rate_limited"))
		slog.Warn("udp packet rate limited", "principal_capsuleer_id", principalID)
		recordUDPPacket(parent, "rate_limited", len(packet))
		s.writeError(conn, remote, interactionID, "rate_limited", "rate limited")
		return
	}
	if interactionID == "" {
		receiveSpan.SetAttributes(attribute.String("validation.result", "rejected"), attribute.String("rejection.reason", "missing_interaction_id"))
		slog.Warn("udp packet rejected", "reason", "missing_interaction_id", "remote", remoteKey(remote))
		recordUDPPacket(parent, "missing_interaction_id", len(packet))
		s.writeError(conn, remote, interactionID, "missing_interaction_id", "missing interaction_id")
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
		s.writeError(conn, remote, interactionID, "request_in_progress", "request is still in progress")
		return
	case replayConflict:
		receiveSpan.SetAttributes(attribute.String("interaction_id", interactionID), attribute.String("validation.result", "rejected"), attribute.String("rejection.reason", "replay_payload_mismatch"))
		slog.Warn("udp replay payload mismatch rejected", "remote", remoteKey(remote), "interaction_id", interactionID)
		recordUDPPacket(parent, "replay", len(packet))
		s.writeError(conn, remote, interactionID, "replay", "replay rejected: interaction_id was already used with a different payload")
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
			s.writeError(conn, remote, interactionID, code, stableDownstreamMessage(callErr))
		} else {
			s.writeCachedError(conn, remote, interactionID, fingerprint, code, stableDownstreamMessage(callErr))
		}
		return
	}
	if strings.TrimSpace(response.GetInteractionId()) != interactionID {
		slog.Error("udp downstream response interaction mismatch", "request_interaction_id", interactionID, "response_interaction_id", response.GetInteractionId())
		recordUDPPacket(parent, "internal", len(packet))
		s.writeCachedError(conn, remote, interactionID, fingerprint, "internal", "downstream response identity mismatch")
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

func (s *QuilkinUDPServer) authenticatedPayload(packet []byte) ([]byte, string, int64, *packetRejection) {
	if !s.authRequired && len(s.hmacSecret) == 0 {
		interactionID, err := extractInteractionID(packet)
		if err != nil {
			return nil, "", 0, reject("malformed_packet", "malformed packet")
		}
		return packet, interactionID, 0, nil
	}
	if len(s.hmacSecret) == 0 {
		return nil, "", 0, reject("auth_not_configured", "authentication unavailable")
	}

	var envelope edgeEnvelope
	decoder := json.NewDecoder(bytes.NewReader(packet))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&envelope); err != nil {
		return nil, "", 0, reject("malformed_packet", "malformed packet")
	}
	if envelope.SchemaVersion != edgeEnvelopeSchema {
		return nil, "", 0, reject("unsupported_envelope", "unsupported packet envelope")
	}
	if len(envelope.Payload) == 0 {
		return nil, "", 0, reject("empty_payload", "empty payload")
	}
	if envelope.Auth == nil {
		return nil, "", 0, reject("missing_signature", "missing signature")
	}
	if envelope.Auth.Algorithm != "hmac-sha256" {
		return nil, "", 0, reject("unsupported_signature", "unsupported signature")
	}
	rawPayload, err := compactJSON(envelope.Payload)
	if err != nil {
		return nil, "", 0, reject("malformed_packet", "malformed packet")
	}
	signature, err := base64.RawURLEncoding.DecodeString(envelope.Auth.Signature)
	if err != nil {
		return nil, "", 0, reject("invalid_signature", "invalid signature")
	}
	credential, exists := s.principals[envelope.Auth.KeyID]
	if !exists || credential.CapsuleerID <= 0 || strings.TrimSpace(credential.Secret) == "" {
		return nil, "", 0, reject("invalid_signature", "invalid signature")
	}
	mac := hmac.New(sha256.New, []byte(credential.Secret))
	_, _ = mac.Write(rawPayload)
	if !hmac.Equal(signature, mac.Sum(nil)) {
		return nil, "", 0, reject("invalid_signature", "invalid signature")
	}
	interactionID, err := extractInteractionID(rawPayload)
	if err != nil {
		return nil, "", 0, reject("malformed_packet", "malformed packet")
	}
	if err := validatePrincipalActor(rawPayload, credential.CapsuleerID); err != nil {
		return nil, "", 0, err
	}
	return rawPayload, interactionID, credential.CapsuleerID, nil
}

func validatePrincipalActor(rawPayload []byte, authenticatedCapsuleerID int64) *packetRejection {
	var packet struct {
		UI struct {
			Action string `json:"action"`
		} `json:"ui"`
		Input map[string]json.RawMessage `json:"input"`
	}
	if err := json.Unmarshal(rawPayload, &packet); err != nil {
		return reject("malformed_packet", "malformed packet")
	}
	requiredField := ""
	switch strings.TrimSpace(packet.UI.Action) {
	case "market_place_sell_order", "contract_create_item_exchange", "direct_trade_offer":
		requiredField = "issued_by_capsuleer_id"
	case "market_buy_from_sell_order", "contract_accept_item_exchange", "direct_trade_accept":
		requiredField = "buyer_capsuleer_id"
	case "market_cancel_order", "contract_cancel_item_exchange", "direct_trade_cancel":
		requiredField = "cancelled_by_capsuleer_id"
	default:
		return reject("unsupported_action", "unsupported trade GUI action")
	}
	claimed, present, err := integerJSONField(packet.Input[requiredField])
	if err != nil {
		return reject("malformed_packet", "malformed actor field")
	}
	if !present || claimed <= 0 {
		return reject("missing_principal", "capsuleer identity is required")
	}
	if claimed != authenticatedCapsuleerID {
		return reject("principal_mismatch", "authenticated capsuleer does not match request actor")
	}
	actorClaims := make(map[string][]int64)
	collectActorClaims(packet.Input, "input", actorClaims)
	for field, values := range actorClaims {
		for _, value := range values {
			if value <= 0 || value != authenticatedCapsuleerID {
				return reject("principal_mismatch", fmt.Sprintf("authenticated capsuleer does not match actor field %s", field))
			}
		}
	}
	return nil
}

func integerJSONField(raw json.RawMessage) (int64, bool, error) {
	if len(raw) == 0 {
		return 0, false, nil
	}
	var value int64
	if err := json.Unmarshal(raw, &value); err != nil {
		return 0, true, err
	}
	return value, true, nil
}

func collectActorClaims(value any, path string, claims map[string][]int64) {
	switch typed := value.(type) {
	case map[string]json.RawMessage:
		for key, raw := range typed {
			var child any
			decoder := json.NewDecoder(bytes.NewReader(raw))
			decoder.UseNumber()
			if err := decoder.Decode(&child); err != nil {
				continue
			}
			fieldPath := path + "." + key
			if key == "owner_id" || strings.HasSuffix(key, "_capsuleer_id") {
				actorID := int64(0)
				if number, ok := child.(json.Number); ok {
					actorID, _ = number.Int64()
				}
				claims[fieldPath] = append(claims[fieldPath], actorID)
			}
			collectActorClaims(child, fieldPath, claims)
		}
	case map[string]any:
		for key, child := range typed {
			fieldPath := path + "." + key
			if key == "owner_id" || strings.HasSuffix(key, "_capsuleer_id") {
				actorID := int64(0)
				if number, ok := child.(json.Number); ok {
					actorID, _ = number.Int64()
				}
				claims[fieldPath] = append(claims[fieldPath], actorID)
			}
			collectActorClaims(child, fieldPath, claims)
		}
	case []any:
		for index, child := range typed {
			collectActorClaims(child, fmt.Sprintf("%s[%d]", path, index), claims)
		}
	}
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

func bestEffortInteractionID(packet []byte) string {
	var envelope edgeEnvelope
	if err := json.Unmarshal(packet, &envelope); err == nil && len(envelope.Payload) > 0 {
		if interactionID, err := extractInteractionID(envelope.Payload); err == nil {
			return interactionID
		}
	}
	if interactionID, err := extractInteractionID(packet); err == nil {
		return interactionID
	}
	return ""
}

func (s *QuilkinUDPServer) writeError(conn net.PacketConn, remote net.Addr, interactionID string, code string, message string) {
	body, _ := json.Marshal(map[string]string{
		"interaction_id": interactionID,
		"status":         errorResponseStatus(code),
		"code":           code,
		"message":        message,
	})
	_ = s.writeResponse(conn, remote, interactionID, body)
}

func (s *QuilkinUDPServer) writeCachedError(conn net.PacketConn, remote net.Addr, interactionID string, fingerprint [sha256.Size]byte, code string, message string) {
	body, _ := json.Marshal(map[string]string{
		"interaction_id": interactionID,
		"status":         errorResponseStatus(code),
		"code":           code,
		"message":        message,
	})
	s.replay().complete(interactionID, fingerprint, body)
	_ = s.writeResponse(conn, remote, interactionID, body)
}

func errorResponseStatus(code string) string {
	if code == "request_in_progress" || code == "downstream_timeout" || code == "downstream_unavailable" || code == "queue_full" || code == "rate_limited" {
		return "retryable"
	}
	return "rejected"
}

func (s *QuilkinUDPServer) writeResponse(conn net.PacketConn, remote net.Addr, interactionID string, body []byte) error {
	responseBody := body
	if len(s.hmacSecret) > 0 {
		canonicalBody, err := canonicalJSON(body)
		if err != nil {
			return fmt.Errorf("canonicalize UDP response: %w", err)
		}
		mac := hmac.New(sha256.New, s.hmacSecret)
		signingBytes, err := responseSigningBytes(edgeResponseEnvelopeSchema, s.hmacKeyID, canonicalBody)
		if err != nil {
			return fmt.Errorf("bind UDP response authentication metadata: %w", err)
		}
		_, _ = mac.Write(signingBytes)
		responseBody, err = json.Marshal(edgeEnvelope{
			SchemaVersion: edgeResponseEnvelopeSchema,
			Payload:       json.RawMessage(canonicalBody),
			Auth: &edgeAuth{
				Algorithm: "hmac-sha256",
				KeyID:     s.hmacKeyID,
				Signature: base64.RawURLEncoding.EncodeToString(mac.Sum(nil)),
			},
		})
		if err != nil {
			return fmt.Errorf("encode signed UDP response: %w", err)
		}
	}
	if _, writeErr := conn.WriteTo(responseBody, remote); writeErr != nil {
		slog.Warn("udp response write failed", "remote", remoteKey(remote), "interaction_id", interactionID, "error", writeErr)
		return writeErr
	}
	return nil
}

func responseSigningBytes(schemaVersion string, keyID string, canonicalBody []byte) ([]byte, error) {
	var payload any
	decoder := json.NewDecoder(bytes.NewReader(canonicalBody))
	decoder.UseNumber()
	if err := decoder.Decode(&payload); err != nil {
		return nil, err
	}
	return json.Marshal(map[string]any{
		"algorithm":      "hmac-sha256",
		"key_id":         keyID,
		"payload":        payload,
		"schema_version": schemaVersion,
	})
}

func canonicalJSON(body []byte) ([]byte, error) {
	var value any
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	return json.Marshal(value)
}

func (s *QuilkinUDPServer) allowPrincipal(principalID int64, remote net.Addr) bool {
	if s.rateLimiter == nil {
		return true
	}
	key := remoteKey(remote)
	if principalID > 0 {
		key = fmt.Sprintf("capsuleer:%d", principalID)
	}
	return s.rateLimiter.allow(key)
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
