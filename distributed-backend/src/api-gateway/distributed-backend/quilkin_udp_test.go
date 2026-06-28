package distributedbackend

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net"
	"sync"
	"testing"
	"time"

	"connectrpc.com/connect"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
)

type recordingMarketClient struct {
	mu       sync.Mutex
	requests []*marketv1.SubmitTradeGuiInteractionRequest
	err      error
}

func (c *recordingMarketClient) SubmitTradeGuiInteraction(_ context.Context, request *marketv1.SubmitTradeGuiInteractionRequest) (*marketv1.SubmitTradeGuiInteractionResponse, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.requests = append(c.requests, request)
	if c.err != nil {
		return nil, c.err
	}
	return &marketv1.SubmitTradeGuiInteractionResponse{
		InteractionId:     "interaction-1",
		Status:            "accepted",
		SettlementBatchId: "settlement-batch",
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

func (c *transientMarketClient) SubmitTradeGuiInteraction(_ context.Context, _ *marketv1.SubmitTradeGuiInteractionRequest) (*marketv1.SubmitTradeGuiInteractionResponse, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.calls++
	if c.calls == 1 {
		return nil, connect.NewError(connect.CodeUnavailable, errors.New("simulated transient outage"))
	}
	return &marketv1.SubmitTradeGuiInteractionResponse{
		InteractionId:     "interaction-1",
		Status:            "accepted",
		SettlementBatchId: "settlement-batch",
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
	var body map[string]any
	if err := json.Unmarshal(c.writes[len(c.writes)-1], &body); err != nil {
		t.Fatalf("decode UDP response: %v", err)
	}
	return body
}

func TestQuilkinUDPServerForwardsOnlyRawPayloadToMarket(t *testing.T) {
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}
	remote := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}
	rawPayload := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"interaction-1","ui":{"window":"regional_market","action":"market_place_sell_order"},"input":{"idempotency_key":"issue-1"}}`)

	server.handlePacket(context.Background(), conn, remote, signedUDPPacket(t, rawPayload, "edge-secret", "primary"))

	if market.count() != 1 {
		t.Fatalf("market calls = %d, want 1", market.count())
	}
	if got := string(market.requests[0].RawPayload); got != string(rawPayload) {
		t.Fatalf("market raw payload = %s, want %s", got, rawPayload)
	}
	response := conn.lastJSON(t)
	if response["status"] != "accepted" {
		t.Fatalf("response status = %v, want accepted", response["status"])
	}
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
	rawPayload := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"interaction-1"}`)
	packet := signedUDPPacket(t, rawPayload, "wrong-secret", "primary")

	server.handlePacket(context.Background(), conn, &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}, packet)

	if market.count() != 0 {
		t.Fatalf("market calls = %d, want 0", market.count())
	}
	if code := conn.lastJSON(t)["code"]; code != "invalid_signature" {
		t.Fatalf("error code = %v, want invalid_signature", code)
	}
}

func TestQuilkinUDPServerReturnsCachedResponseWithoutSecondMarketCall(t *testing.T) {
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}
	remote := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}
	rawPayload := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"interaction-1"}`)
	packet := signedUDPPacket(t, rawPayload, "edge-secret", "primary")

	server.handlePacket(context.Background(), conn, remote, packet)
	server.handlePacket(context.Background(), conn, remote, packet)

	if market.count() != 1 {
		t.Fatalf("market calls = %d, want 1", market.count())
	}
	if status := conn.lastJSON(t)["status"]; status != "accepted" {
		t.Fatalf("cached response status = %v, want accepted", status)
	}
}

func TestQuilkinUDPServerRejectsInteractionIDReusedWithDifferentPayload(t *testing.T) {
	market := &recordingMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}
	remote := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}
	first := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"interaction-1","input":{"quantity":1}}`)
	conflict := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"interaction-1","input":{"quantity":2}}`)

	server.handlePacket(context.Background(), conn, remote, signedUDPPacket(t, first, "edge-secret", "primary"))
	server.handlePacket(context.Background(), conn, remote, signedUDPPacket(t, conflict, "edge-secret", "primary"))

	if market.count() != 1 {
		t.Fatalf("market calls = %d, want 1", market.count())
	}
	if code := conn.lastJSON(t)["code"]; code != "replay" {
		t.Fatalf("error code = %v, want replay", code)
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
	rawPayload := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"interaction-1"}`)
	packet := signedUDPPacket(t, rawPayload, "edge-secret", "primary")

	server.handlePacket(context.Background(), conn, remote, packet)
	server.handlePacket(context.Background(), conn, remote, packet)

	if market.count() != 1 {
		t.Fatalf("market calls = %d, want 1", market.count())
	}
	if status := conn.lastJSON(t)["status"]; status != "accepted" {
		t.Fatalf("retried response status = %v, want accepted", status)
	}
}

func TestQuilkinUDPServerAllowsSafeRetryAfterTransientDownstreamFailure(t *testing.T) {
	market := &transientMarketClient{}
	server := testUDPServer(market)
	conn := &capturePacketConn{}
	remote := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}
	rawPayload := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"interaction-1"}`)
	packet := signedUDPPacket(t, rawPayload, "edge-secret", "primary")

	server.handlePacket(context.Background(), conn, remote, packet)
	if code := conn.lastJSON(t)["code"]; code != "downstream_unavailable" {
		t.Fatalf("first response code = %v, want downstream_unavailable", code)
	}

	server.handlePacket(context.Background(), conn, remote, packet)

	if market.count() != 2 {
		t.Fatalf("market calls = %d, want 2", market.count())
	}
	if status := conn.lastJSON(t)["status"]; status != "accepted" {
		t.Fatalf("retried response status = %v, want accepted", status)
	}
}

func TestQuilkinUDPServerRateLimitsRemoteAddress(t *testing.T) {
	server := testUDPServer(&recordingMarketClient{})
	server.rateLimiter = newRemoteRateLimiter(1, 1)
	remote := &net.UDPAddr{IP: net.ParseIP("203.0.113.10"), Port: 40000}

	if !server.allowRemote(remote) {
		t.Fatalf("first packet should be allowed")
	}
	if server.allowRemote(remote) {
		t.Fatalf("second packet should be rate limited")
	}
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
		market:       market,
		rateLimiter:  newRemoteRateLimiter(100, 100),
		replayCache:  newInteractionReplayCache(time.Minute),
	}
}

func signedUDPPacket(t *testing.T, rawPayload []byte, secret string, keyID string) []byte {
	t.Helper()
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
		t.Fatalf("marshal packet: %v", err)
	}
	return packet
}
