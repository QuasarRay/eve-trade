package gateway

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/market"
)

type recordingMarketClient struct {
	mu       sync.Mutex
	requests []*market.SubmitTradeGuiInteractionRequest
	err      error
}

func (c *recordingMarketClient) SubmitTradeGuiInteraction(_ context.Context, request *market.SubmitTradeGuiInteractionRequest) (*market.SubmitTradeGuiInteractionResponse, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.requests = append(c.requests, request)
	if c.err != nil {
		return nil, c.err
	}
	interactionID, err := extractInteractionID(request.RawPayload)
	if err != nil {
		return nil, err
	}
	return &market.SubmitTradeGuiInteractionResponse{
		InteractionID:     interactionID,
		Status:            "queued",
		SettlementBatchID: "settlement-batch",
	}, nil
}

func (c *recordingMarketClient) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.requests)
}

type transientMarketClient struct {
	mu    sync.Mutex
	calls int
}

type blockingMarketClient struct {
	started chan struct{}
	release chan struct{}
	calls   atomic.Int32
}

func (c *blockingMarketClient) SubmitTradeGuiInteraction(ctx context.Context, _ *market.SubmitTradeGuiInteractionRequest) (*market.SubmitTradeGuiInteractionResponse, error) {
	c.calls.Add(1)
	select {
	case c.started <- struct{}{}:
	default:
	}
	select {
	case <-c.release:
		return &market.SubmitTradeGuiInteractionResponse{InteractionID: "interaction-1", Status: "queued"}, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (c *transientMarketClient) SubmitTradeGuiInteraction(_ context.Context, _ *market.SubmitTradeGuiInteractionRequest) (*market.SubmitTradeGuiInteractionResponse, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.calls++
	if c.calls == 1 {
		return nil, errs.B().Code(errs.Unavailable).Cause(errors.New("simulated transient outage")).Msg("simulated transient outage").Err()
	}
	return &market.SubmitTradeGuiInteractionResponse{
		InteractionID:     "interaction-1",
		Status:            "queued",
		SettlementBatchID: "settlement-batch",
	}, nil
}

func (c *transientMarketClient) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.calls
}

type capturePacketConn struct {
	mu     sync.Mutex
	writes [][]byte
}

func (c *capturePacketConn) ReadFrom([]byte) (int, net.Addr, error) {
	return 0, nil, net.ErrClosed
}

func (c *capturePacketConn) WriteTo(payload []byte, _ net.Addr) (int, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.writes = append(c.writes, append([]byte(nil), payload...))
	return len(payload), nil
}

func (c *capturePacketConn) Close() error {
	return nil
}

func (c *capturePacketConn) LocalAddr() net.Addr {
	return &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 26000}
}

func (c *capturePacketConn) SetDeadline(time.Time) error {
	return nil
}

func (c *capturePacketConn) SetReadDeadline(time.Time) error {
	return nil
}

func (c *capturePacketConn) SetWriteDeadline(time.Time) error {
	return nil
}

func (c *capturePacketConn) lastJSON(t *testing.T) map[string]any {
	t.Helper()
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.writes) == 0 {
		t.Fatalf("no UDP response was written")
	}
	responseBytes := c.writes[len(c.writes)-1]
	var envelope edgeEnvelope
	if err := json.Unmarshal(responseBytes, &envelope); err != nil {
		t.Fatalf("decode UDP response: %v", err)
	}
	if envelope.SchemaVersion == edgeResponseEnvelopeSchema {
		if envelope.Auth == nil {
			t.Fatal("signed UDP response has no auth block")
		}
		canonical, err := canonicalJSON(envelope.Payload)
		if err != nil {
			t.Fatalf("canonicalize UDP response: %v", err)
		}
		mac := hmac.New(sha256.New, []byte("edge-secret"))
		signingBytes, err := responseSigningBytes(envelope.SchemaVersion, envelope.Auth.KeyID, canonical)
		if err != nil {
			t.Fatalf("build UDP response signing bytes: %v", err)
		}
		_, _ = mac.Write(signingBytes)
		want := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
		if envelope.Auth.Signature != want {
			t.Fatalf("UDP response signature = %q, want %q", envelope.Auth.Signature, want)
		}
		responseBytes = envelope.Payload
	}
	var body map[string]any
	if err := json.Unmarshal(responseBytes, &body); err != nil {
		t.Fatalf("decode UDP response payload: %v", err)
	}
	return body
}

func (c *capturePacketConn) lastEnvelope(t *testing.T) edgeEnvelope {
	t.Helper()
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.writes) == 0 {
		t.Fatal("no UDP response was written")
	}
	var envelope edgeEnvelope
	if err := json.Unmarshal(c.writes[len(c.writes)-1], &envelope); err != nil {
		t.Fatalf("decode UDP response envelope: %v", err)
	}
	return envelope
}

func TestQuilkinUDPServerForwardsOnlyRawPayloadToMarket(t *testing.T) {
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}
	remote := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}
	rawPayload := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"interaction-1","ui":{"window":"regional_market","action":"market_place_sell_order"},"input":{"idempotency_key":"issue-1","issued_by_capsuleer_id":1001}}`)

	server.handlePacket(context.Background(), conn, remote, signedUDPPacket(t, rawPayload, "edge-secret", "primary"))

	if market.count() != 1 {
		t.Fatalf("market calls = %d, want 1", market.count())
	}
	if got := string(market.requests[0].RawPayload); got != string(rawPayload) {
		t.Fatalf("market raw payload = %s, want %s", got, rawPayload)
	}
	response := conn.lastJSON(t)
	if response["status"] != "queued" {
		t.Fatalf("response status = %v, want queued", response["status"])
	}
	envelope := conn.lastEnvelope(t)
	if envelope.SchemaVersion != edgeResponseEnvelopeSchema || envelope.Auth == nil {
		t.Fatalf("response was not authenticated: %+v", envelope)
	}
}

func TestQuilkinUDPServerAcceptsVersionedProtocolGoldenPacket(t *testing.T) {
	rawPayload, err := os.ReadFile(filepath.Join("..", "distributed-backend", "protocol", "fixtures", "sell-order.packet.json"))
	if err != nil {
		t.Fatalf("read protocol golden packet: %v", err)
	}
	canonical, err := canonicalJSON(rawPayload)
	if err != nil {
		t.Fatalf("canonicalize golden packet: %v", err)
	}
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}

	server.handlePacket(context.Background(), conn, &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}, signedUDPPacket(t, canonical, "edge-secret", "primary"))

	if market.count() != 1 || conn.lastJSON(t)["status"] != "queued" {
		t.Fatalf("golden packet was not accepted; market calls=%d response=%v", market.count(), conn.lastJSON(t))
	}
}

func TestQuilkinUDPServerListenAndServeProcessesRealSocketAndShutsDown(t *testing.T) {
	listener, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen UDP: %v", err)
	}
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	server.listenFunc = func(network string, address string) (net.PacketConn, error) {
		if network != "udp" {
			t.Fatalf("listen network = %q, want udp", network)
		}
		return listener, nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- server.ListenAndServe(ctx) }()

	client, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		cancel()
		t.Fatalf("listen client UDP: %v", err)
	}
	defer client.Close()
	packet := signedUDPPacket(t, authenticatedTestPayload("socket-1", 1), "edge-secret", "primary")
	if _, err := client.WriteTo(packet, listener.LocalAddr()); err != nil {
		cancel()
		t.Fatalf("write UDP packet: %v", err)
	}
	response := readSignedUDPResponse(t, client)
	if response["status"] != "queued" || market.count() != 1 {
		t.Fatalf("socket response = %v; market calls = %d", response, market.count())
	}

	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("ListenAndServe returned error during shutdown: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("ListenAndServe did not shut down after context cancellation")
	}
}

func TestQuilkinUDPServerListenAndServeRejectsOversizedDatagram(t *testing.T) {
	listener, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen UDP: %v", err)
	}
	server := testUDPServer(&recordingMarketClient{})
	server.maxPacket = 32
	server.listenFunc = func(string, string) (net.PacketConn, error) { return listener, nil }
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- server.ListenAndServe(ctx) }()
	client, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		cancel()
		t.Fatalf("listen client UDP: %v", err)
	}
	defer client.Close()
	if _, err := client.WriteTo(make([]byte, 33), listener.LocalAddr()); err != nil {
		cancel()
		t.Fatalf("write oversized packet: %v", err)
	}
	if response := readSignedUDPResponse(t, client); response["code"] != "packet_too_large" {
		t.Fatalf("oversized response = %v", response)
	}
	cancel()
	if err := <-done; err != nil {
		t.Fatalf("ListenAndServe returned error during shutdown: %v", err)
	}
}

func TestQuilkinUDPServerListenAndServeRejectsQueueOverflow(t *testing.T) {
	listener, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	market := &blockingMarketClient{started: make(chan struct{}, 1), release: make(chan struct{})}
	server := testUDPServer(market)
	server.queueDepth = 1
	server.workers = 1
	server.listenFunc = func(string, string) (net.PacketConn, error) { return listener, nil }
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- server.ListenAndServe(ctx) }()
	client, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	defer client.Close()

	write := func(id string) {
		t.Helper()
		packet := signedUDPPacket(t, authenticatedTestPayload(id, 1), "edge-secret", "primary")
		if _, err := client.WriteTo(packet, listener.LocalAddr()); err != nil {
			t.Fatalf("write %s: %v", id, err)
		}
	}
	write("queue-1")
	select {
	case <-market.started:
	case <-time.After(time.Second):
		t.Fatal("first queued packet did not reach worker")
	}
	write("queue-2")
	write("queue-3")
	if response := readSignedUDPResponse(t, client); response["code"] != "queue_full" {
		t.Fatalf("overflow response = %v", response)
	}
	close(market.release)
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("server shutdown: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("server did not drain queued work during shutdown")
	}
	if market.calls.Load() != 2 {
		t.Fatalf("admitted downstream calls after shutdown = %d, want exactly 2", market.calls.Load())
	}
}

func readSignedUDPResponse(t *testing.T, conn net.PacketConn) map[string]any {
	t.Helper()
	if err := conn.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatalf("set UDP deadline: %v", err)
	}
	buffer := make([]byte, 65535)
	n, _, err := conn.ReadFrom(buffer)
	if err != nil {
		t.Fatalf("read UDP response: %v", err)
	}
	var envelope edgeEnvelope
	if err := json.Unmarshal(buffer[:n], &envelope); err != nil {
		t.Fatalf("decode response envelope: %v", err)
	}
	if envelope.Auth == nil {
		t.Fatal("response envelope has no authentication metadata")
	}
	canonical, err := canonicalJSON(envelope.Payload)
	if err != nil {
		t.Fatalf("canonicalize response: %v", err)
	}
	mac := hmac.New(sha256.New, []byte("edge-secret"))
	signingBytes, err := responseSigningBytes(envelope.SchemaVersion, envelope.Auth.KeyID, canonical)
	if err != nil {
		t.Fatalf("build response signing bytes: %v", err)
	}
	_, _ = mac.Write(signingBytes)
	want := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	if envelope.Auth.Signature != want {
		t.Fatalf("response is not correctly signed: %+v", envelope.Auth)
	}
	var response map[string]any
	if err := json.Unmarshal(envelope.Payload, &response); err != nil {
		t.Fatalf("decode response payload: %v", err)
	}
	return response
}

func TestQuilkinUDPServerRejectsMissingSignature(t *testing.T) {
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}
	payload := json.RawMessage(`{"schema_version":"eve-trade-gui.v1","interaction_id":"interaction-1"}`)
	packet, err := json.Marshal(map[string]any{
		"schema_version": edgeEnvelopeSchema,
		"payload":        payload,
	})
	if err != nil {
		t.Fatalf("marshal packet: %v", err)
	}

	server.handlePacket(context.Background(), conn, &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}, packet)

	if market.count() != 0 {
		t.Fatalf("market calls = %d, want 0", market.count())
	}
	if code := conn.lastJSON(t)["code"]; code != "missing_signature" {
		t.Fatalf("error code = %v, want missing_signature", code)
	}
}

func TestQuilkinUDPServerRejectsInvalidSignature(t *testing.T) {
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}
	rawPayload := authenticatedTestPayload("interaction-1", 1)
	packet := signedUDPPacket(t, rawPayload, "wrong-secret", "primary")

	server.handlePacket(context.Background(), conn, &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}, packet)

	if market.count() != 0 {
		t.Fatalf("market calls = %d, want 0", market.count())
	}
	if code := conn.lastJSON(t)["code"]; code != "invalid_signature" {
		t.Fatalf("error code = %v, want invalid_signature", code)
	}
}

func TestQuilkinUDPServerRejectsUnknownKeyIDAndKnownKeyWithWrongHMAC(t *testing.T) {
	tests := []struct {
		name   string
		keyID  string
		secret string
	}{
		{name: "unknown key ID", keyID: "unknown", secret: "edge-secret"},
		{name: "known key wrong HMAC", keyID: "primary", secret: "wrong-secret"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			market := &recordingMarketClient{}
			server := testUDPServer(market)
			conn := &capturePacketConn{}
			server.handlePacket(
				context.Background(),
				conn,
				&net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000},
				signedUDPPacket(t, authenticatedTestPayload("interaction-1", 1), test.secret, test.keyID),
			)
			if market.count() != 0 {
				t.Fatalf("market calls = %d, want 0", market.count())
			}
			response := conn.lastJSON(t)
			if response["code"] != "invalid_signature" || response["interaction_id"] != "interaction-1" {
				t.Fatalf("response = %#v, want interaction-bound invalid_signature", response)
			}
		})
	}
}

func TestQuilkinUDPServerBindsEveryActionAndActorLikeFieldToAuthenticatedPrincipal(t *testing.T) {
	actions := []struct {
		name       string
		action     string
		actorField string
	}{
		{name: "issue market", action: "market_place_sell_order", actorField: "issued_by_capsuleer_id"},
		{name: "issue contract", action: "contract_create_item_exchange", actorField: "issued_by_capsuleer_id"},
		{name: "issue direct", action: "direct_trade_offer", actorField: "issued_by_capsuleer_id"},
		{name: "buy market", action: "market_buy_from_sell_order", actorField: "buyer_capsuleer_id"},
		{name: "accept contract", action: "contract_accept_item_exchange", actorField: "buyer_capsuleer_id"},
		{name: "accept direct", action: "direct_trade_accept", actorField: "buyer_capsuleer_id"},
		{name: "cancel market", action: "market_cancel_order", actorField: "cancelled_by_capsuleer_id"},
		{name: "cancel contract", action: "contract_cancel_item_exchange", actorField: "cancelled_by_capsuleer_id"},
		{name: "cancel direct", action: "direct_trade_cancel", actorField: "cancelled_by_capsuleer_id"},
	}
	principals := []struct {
		keyID string
		id    int64
		key   string
	}{
		{keyID: "seller", id: 1001, key: "seller-secret"},
		{keyID: "buyer", id: 2002, key: "buyer-secret"},
		{keyID: "other", id: 3003, key: "other-secret"},
	}
	for _, action := range actions {
		for _, principal := range principals {
			t.Run(action.name+"/"+principal.keyID, func(t *testing.T) {
				server := testUDPServer(&recordingMarketClient{})
				server.principals = map[string]UDPPrincipalCredential{
					principal.keyID: {CapsuleerID: principal.id, Secret: principal.key},
				}
				for _, hostileID := range []int64{1001, 2002, 3003} {
					if hostileID == principal.id {
						continue
					}
					hostileFields := map[string]map[string]any{
						"issue actor":    {"issued_by_capsuleer_id": hostileID},
						"buyer actor":    {"buyer_capsuleer_id": hostileID},
						"seller actor":   {"seller_capsuleer_id": hostileID},
						"cancel actor":   {"cancelled_by_capsuleer_id": hostileID},
						"accepted actor": {"accepted_by_capsuleer_id": hostileID},
						"nested owner":   {"item_stack": map[string]any{"owner_id": hostileID}},
						"unknown actor":  {"delegated_capsuleer_id": hostileID},
					}
					for fieldName, hostile := range hostileFields {
						input := map[string]any{action.actorField: principal.id}
						for name, value := range hostile {
							input[name] = value
						}
						payload := actorTestPayload(t, "actor-binding", action.action, input)
						_, _, _, rejection := server.authenticatedPayload(signedUDPPacket(t, payload, principal.key, principal.keyID))
						if rejection == nil || rejection.Code != "principal_mismatch" {
							t.Fatalf("principal %d action %s accepted hostile %s=%d: %#v", principal.id, action.action, fieldName, hostileID, rejection)
						}
					}
				}
			})
		}
	}
}

func TestQuilkinUDPServerAcceptsActorAliasesOnlyWhenTheyMatchPrincipal(t *testing.T) {
	server := testUDPServer(&recordingMarketClient{})
	payload := actorTestPayload(t, "all-bound", "market_place_sell_order", map[string]any{
		"issued_by_capsuleer_id":   1001,
		"seller_capsuleer_id":      1001,
		"accepted_by_capsuleer_id": 1001,
		"item_stack":               map[string]any{"owner_id": 1001},
	})
	_, interactionID, principalID, rejection := server.authenticatedPayload(signedUDPPacket(t, payload, "edge-secret", "primary"))
	if rejection != nil || interactionID != "all-bound" || principalID != 1001 {
		t.Fatalf("fully bound actor payload rejected: interaction=%q principal=%d rejection=%v", interactionID, principalID, rejection)
	}
}

func TestQuilkinUDPServerRejectsActorClaimThatDoesNotMatchAuthenticatedPrincipal(t *testing.T) {
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	server.principals = map[string]UDPPrincipalCredential{
		"attacker": {CapsuleerID: 2002, Secret: "attacker-secret"},
	}
	conn := &capturePacketConn{}
	packet := signedUDPPacket(t, authenticatedTestPayload("impersonation-1", 1), "attacker-secret", "attacker")

	server.handlePacket(context.Background(), conn, &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}, packet)

	if market.count() != 0 {
		t.Fatalf("market calls = %d, want 0", market.count())
	}
	if code := conn.lastJSON(t)["code"]; code != "principal_mismatch" {
		t.Fatalf("error code = %v, want principal_mismatch", code)
	}
}

func TestQuilkinUDPServerReturnsCachedResponseWithoutSecondMarketCall(t *testing.T) {
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}
	remote := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}
	rawPayload := authenticatedTestPayload("interaction-1", 1)
	packet := signedUDPPacket(t, rawPayload, "edge-secret", "primary")

	server.handlePacket(context.Background(), conn, remote, packet)
	server.handlePacket(context.Background(), conn, remote, packet)

	if market.count() != 1 {
		t.Fatalf("market calls = %d, want 1", market.count())
	}
	if status := conn.lastJSON(t)["status"]; status != "queued" {
		t.Fatalf("cached response status = %v, want queued", status)
	}
}

func TestQuilkinUDPServerConcurrentDuplicateHasOneDownstreamCall(t *testing.T) {
	market := &blockingMarketClient{started: make(chan struct{}, 1), release: make(chan struct{})}
	server := testUDPServer(market)
	firstConn := &capturePacketConn{}
	secondConn := &capturePacketConn{}
	packet := signedUDPPacket(t, authenticatedTestPayload("interaction-1", 1), "edge-secret", "primary")
	firstDone := make(chan struct{})
	go func() {
		defer close(firstDone)
		server.handlePacket(context.Background(), firstConn, &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}, packet)
	}()
	select {
	case <-market.started:
	case <-time.After(time.Second):
		t.Fatal("first request did not reach downstream")
	}

	server.handlePacket(context.Background(), secondConn, &net.UDPAddr{IP: net.ParseIP("203.0.113.11"), Port: 40001}, packet)
	if code := secondConn.lastJSON(t)["code"]; code != "request_in_progress" {
		t.Fatalf("concurrent duplicate response code = %v", code)
	}
	close(market.release)
	select {
	case <-firstDone:
	case <-time.After(time.Second):
		t.Fatal("first request did not finish")
	}
	if market.calls.Load() != 1 || firstConn.lastJSON(t)["status"] != "queued" {
		t.Fatalf("downstream calls=%d first response=%v", market.calls.Load(), firstConn.lastJSON(t))
	}
}

func TestInteractionReplayCacheExpiresAndDoesNotReturnStaleResponse(t *testing.T) {
	now := time.Unix(100, 0)
	cache := newInteractionReplayCache(time.Second)
	cache.now = func() time.Time { return now }
	fingerprint := sha256.Sum256([]byte("payload"))
	if state, _ := cache.begin("interaction", fingerprint); state != replayNew {
		t.Fatalf("first state = %v", state)
	}
	cache.complete("interaction", fingerprint, []byte(`{"status":"queued"}`))
	if state, _ := cache.begin("interaction", fingerprint); state != replayCached {
		t.Fatalf("cached state = %v", state)
	}
	now = now.Add(2 * time.Second)
	if state, response := cache.begin("interaction", fingerprint); state != replayNew || response != nil {
		t.Fatalf("expired state=%v response=%q", state, response)
	}
}

func TestQuilkinUDPServerRejectsInteractionIDReusedWithDifferentPayload(t *testing.T) {
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}
	remote := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}
	first := authenticatedTestPayload("interaction-1", 1)
	conflict := authenticatedTestPayload("interaction-1", 2)

	server.handlePacket(context.Background(), conn, remote, signedUDPPacket(t, first, "edge-secret", "primary"))
	server.handlePacket(context.Background(), conn, remote, signedUDPPacket(t, conflict, "edge-secret", "primary"))

	if market.count() != 1 {
		t.Fatalf("market calls = %d, want 1", market.count())
	}
	response := conn.lastJSON(t)
	if code := response["code"]; code != "replay" {
		t.Fatalf("error code = %v, want replay", code)
	}
	if message, _ := response["message"].(string); !strings.Contains(message, "replay") {
		t.Fatalf("error message = %q, want replay diagnostic", message)
	}
}

type failFirstWritePacketConn struct {
	capturePacketConn
	failed bool
}

func (c *failFirstWritePacketConn) WriteTo(payload []byte, remote net.Addr) (int, error) {
	if !c.failed {
		c.failed = true
		return 0, errors.New("simulated response loss")
	}
	return c.capturePacketConn.WriteTo(payload, remote)
}

func TestQuilkinUDPServerRetriesLostResponseWithoutRepeatingMarketCall(t *testing.T) {
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	conn := &failFirstWritePacketConn{}
	remote := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}
	rawPayload := authenticatedTestPayload("interaction-1", 1)
	packet := signedUDPPacket(t, rawPayload, "edge-secret", "primary")

	server.handlePacket(context.Background(), conn, remote, packet)
	server.handlePacket(context.Background(), conn, remote, packet)

	if market.count() != 1 {
		t.Fatalf("market calls = %d, want 1", market.count())
	}
	if status := conn.lastJSON(t)["status"]; status != "queued" {
		t.Fatalf("retried response status = %v, want queued", status)
	}
}

func TestQuilkinUDPServerAllowsSafeRetryAfterTransientDownstreamFailure(t *testing.T) {
	market := &transientMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}
	remote := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}
	rawPayload := authenticatedTestPayload("interaction-1", 1)
	packet := signedUDPPacket(t, rawPayload, "edge-secret", "primary")

	server.handlePacket(context.Background(), conn, remote, packet)
	if code := conn.lastJSON(t)["code"]; code != "downstream_unavailable" {
		t.Fatalf("first response code = %v, want downstream_unavailable", code)
	}

	server.handlePacket(context.Background(), conn, remote, packet)

	if market.count() != 2 {
		t.Fatalf("market calls = %d, want 2", market.count())
	}
	if status := conn.lastJSON(t)["status"]; status != "queued" {
		t.Fatalf("retried response status = %v, want queued", status)
	}
}

func TestQuilkinUDPServerRateLimitsAuthenticatedPrincipalAcrossProxyAddresses(t *testing.T) {
	server := testUDPServer(&recordingMarketClient{})
	server.rateLimiter = newRemoteRateLimiter(1, 1)
	firstProxy := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}
	secondProxy := &net.UDPAddr{IP: net.ParseIP("203.0.113.11"), Port: 40001}

	if !server.allowPrincipal(1001, firstProxy) {
		t.Fatalf("first packet should be allowed")
	}
	if server.allowPrincipal(1001, secondProxy) {
		t.Fatalf("second packet should be rate limited")
	}
	if !server.allowPrincipal(2002, firstProxy) {
		t.Fatalf("different authenticated principal should have an independent bucket")
	}
}

func FuzzAuthenticatedPayloadNeverAcceptsAnUnboundPrincipal(f *testing.F) {
	server := testUDPServer(&recordingMarketClient{})
	validPayload := authenticatedTestPayload("fuzz-seed", 1)
	f.Add(signedUDPPacketForFuzz(validPayload, "edge-secret", "primary"))
	f.Add([]byte(`{"schema_version":"eve-trade-edge.v1"}`))
	f.Add([]byte{})
	f.Fuzz(func(t *testing.T, packet []byte) {
		raw, interactionID, principalID, rejection := server.authenticatedPayload(packet)
		if rejection == nil {
			if len(raw) == 0 || interactionID == "" || principalID != 1001 {
				t.Fatalf("accepted packet without complete authenticated binding: raw=%q interaction=%q principal=%d", raw, interactionID, principalID)
			}
			if rejection := validateAuthenticatedActor(raw, principalID); rejection != nil {
				t.Fatalf("accepted packet failed proto actor binding validation: %v", rejection)
			}
		}
	})
}

func actorTestPayload(t *testing.T, interactionID string, action string, input map[string]any) []byte {
	t.Helper()
	payload, err := json.Marshal(map[string]any{
		"schema_version": "eve-trade-gui.v1",
		"interaction_id": interactionID,
		"ui":             map[string]any{"action": action},
		"input":          input,
	})
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func testUDPServer(market MarketClient) *QuilkinUDPServer {
	return &QuilkinUDPServer{
		maxPacket:    8192,
		timeout:      time.Second,
		workers:      1,
		queueDepth:   1,
		authRequired: true,
		hmacSecret:   []byte("edge-secret"),
		hmacKeyID:    "primary",
		principals: map[string]UDPPrincipalCredential{
			"primary": {CapsuleerID: 1001, Secret: "edge-secret"},
		},
		market:      market,
		rateLimiter: newRemoteRateLimiter(100, 100),
		replayCache: newInteractionReplayCache(time.Minute),
	}
}

func authenticatedTestPayload(interactionID string, quantity int) []byte {
	return fmt.Appendf(nil, `{"schema_version":"eve-trade-gui.v1","interaction_id":%q,"ui":{"action":"market_place_sell_order"},"input":{"issued_by_capsuleer_id":1001,"quantity":%d}}`, interactionID, quantity)
}

func signedUDPPacket(t *testing.T, rawPayload []byte, secret string, keyID string) []byte {
	t.Helper()
	return signedUDPPacketForFuzz(rawPayload, secret, keyID)
}

func signedUDPPacketForFuzz(rawPayload []byte, secret string, keyID string) []byte {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write(rawPayload)
	signature := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	packet, err := json.Marshal(map[string]any{
		"schema_version": edgeEnvelopeSchema,
		"payload":        json.RawMessage(rawPayload),
		"auth": map[string]string{
			"algorithm": "hmac-sha256",
			"key_id":    keyID,
			"signature": signature,
		},
	})
	if err != nil {
		panic(err)
	}
	return packet
}
