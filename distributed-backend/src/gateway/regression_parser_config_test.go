package gateway

import (
	"bytes"
	"fmt"
	"strings"
	"testing"

	"github.com/QuasarRay/eve-trade/distributed-backend/internal/testkit"
	"github.com/onsi/gomega"
)

func expectAuthenticatedRejection(t *testing.T, packet []byte, code string) {
	t.Helper()
	g := testkit.Expect(t)
	_, _, _, rejection := testUDPServer(&recordingMarketClient{}).authenticatedPayload(packet)
	excerpt := packet
	if len(excerpt) > 512 {
		excerpt = excerpt[:512]
	}
	g.Expect(rejection).NotTo(
		gomega.BeNil(),
		"ambiguous raw packet was accepted: length=%d prefix=%q",
		len(packet),
		excerpt,
	)
	if rejection != nil {
		g.Expect(rejection.Code).To(gomega.Equal(code))
	}
}

func opaqueSignedEnvelope(payload []byte) []byte {
	return fmt.Appendf(nil, `{"schema_version":%q,"payload":%s,"auth":{"algorithm":%q,"key_id":"primary","signature":"AA"}}`, edgeEnvelopeSchema, payload, hmacSHA256Algorithm)
}

func validGatewayConfigEnvironment(t *testing.T) {
	t.Helper()
	t.Setenv("API_GATEWAY_QUILKIN_UDP_ENABLED", "true")
	t.Setenv("API_GATEWAY_UDP_AUTH_REQUIRED", "true")
	t.Setenv("API_GATEWAY_UDP_HMAC_SECRET", "response-secret")
	t.Setenv("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON", `{"seller":{"capsuleer_id":1001,"secret":"seller-secret"}}`)
}

func TestCanonicalJSONRegressions(t *testing.T) {
	validPayload := authenticatedTestPayload("json-regression", 1)
	validPacket := signedUDPPacket(t, validPayload, "edge-secret", "primary")

	t.Run("test_authenticated_envelope_rejects_trailing_json_value", func(t *testing.T) {
		expectAuthenticatedRejection(t, append(append([]byte(nil), validPacket...), []byte(` {}`)...), "malformed_packet")
	})

	t.Run("test_authenticated_envelope_rejects_trailing_non_whitespace_bytes", func(t *testing.T) {
		expectAuthenticatedRejection(t, append(append([]byte(nil), validPacket...), []byte(` garbage`)...), "malformed_packet")
	})

	t.Run("test_interaction_id_parser_rejects_trailing_json_value", func(t *testing.T) {
		g := testkit.Expect(t)
		_, err := extractInteractionID(append(append([]byte(nil), validPayload...), []byte(` {"interaction_id":"other"}`)...))
		g.Expect(err).To(gomega.HaveOccurred(), "interaction parser accepted a second JSON value")
	})

	t.Run("test_authenticated_envelope_rejects_duplicate_object_keys", func(t *testing.T) {
		payload := []byte(`{"schema_version":"eve-trade-gui.v1","interaction_id":"duplicate","interaction_id":"duplicate","ui":{"action":"market_place_sell_order","action":"market_place_sell_order"},"input":{"issued_by_capsuleer_id":1001,"issued_by_capsuleer_id":1001,"quantity":1}}`)
		expectAuthenticatedRejection(t, opaqueSignedEnvelope(payload), "malformed_packet")
	})

	t.Run("test_authenticated_envelope_rejects_unknown_top_level_fields", func(t *testing.T) {
		packet := bytes.TrimSuffix(append([]byte(nil), validPacket...), []byte("}"))
		packet = append(packet, []byte(`,"unexpected":true}`)...)
		expectAuthenticatedRejection(t, packet, "malformed_packet")
	})

	t.Run("test_authenticated_envelope_rejects_excessive_json_nesting", func(t *testing.T) {
		nested := strings.Repeat(`{"value":`, 128) + `0` + strings.Repeat(`}`, 128)
		payload := []byte(fmt.Sprintf(`{"schema_version":"eve-trade-gui.v1","interaction_id":"deep","ui":{"action":"market_place_sell_order"},"input":{"issued_by_capsuleer_id":1001,"quantity":1,"nested":%s}}`, nested))
		expectAuthenticatedRejection(t, opaqueSignedEnvelope(payload), "malformed_packet")
	})

	t.Run("test_authenticated_envelope_rejects_oversized_strings_before_canonicalization", func(t *testing.T) {
		payload := authenticatedTestPayload(strings.Repeat("x", 128*1024), 1)
		expectAuthenticatedRejection(t, opaqueSignedEnvelope(payload), "malformed_packet")
	})

	t.Run("test_authenticated_envelope_accepts_exactly_one_complete_json_value", func(t *testing.T) {
		g := testkit.Expect(t)
		payload, interactionID, principalID, rejection := testUDPServer(&recordingMarketClient{}).authenticatedPayload(append(validPacket, '\n', '\t'))
		g.Expect(rejection).To(gomega.BeNil())
		g.Expect(payload).NotTo(gomega.BeEmpty())
		g.Expect(interactionID).To(gomega.Equal("json-regression"))
		g.Expect(principalID).To(gomega.Equal(int64(1001)))
	})
}

func TestCanonicalConfigurationRegressions(t *testing.T) {
	t.Run("test_configuration_rejects_worker_count_below_minimum", func(t *testing.T) {
		g := testkit.Expect(t)
		validGatewayConfigEnvironment(t)
		t.Setenv("API_GATEWAY_QUILKIN_WORKERS", "0")
		_, err := LoadConfig()
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("API_GATEWAY_QUILKIN_WORKERS")))
	})

	t.Run("test_configuration_rejects_worker_count_above_maximum", func(t *testing.T) {
		g := testkit.Expect(t)
		validGatewayConfigEnvironment(t)
		t.Setenv("API_GATEWAY_QUILKIN_WORKERS", "1000000")
		_, err := LoadConfig()
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("API_GATEWAY_QUILKIN_WORKERS")), "worker count exceeds the documented CPU budget")
	})

	t.Run("test_configuration_rejects_queue_depth_below_minimum", func(t *testing.T) {
		g := testkit.Expect(t)
		validGatewayConfigEnvironment(t)
		t.Setenv("API_GATEWAY_QUILKIN_QUEUE_DEPTH", "0")
		_, err := LoadConfig()
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("API_GATEWAY_QUILKIN_QUEUE_DEPTH")))
	})

	t.Run("test_configuration_rejects_queue_depth_above_memory_budget", func(t *testing.T) {
		g := testkit.Expect(t)
		validGatewayConfigEnvironment(t)
		t.Setenv("API_GATEWAY_QUILKIN_QUEUE_DEPTH", "100000000")
		_, err := LoadConfig()
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("API_GATEWAY_QUILKIN_QUEUE_DEPTH")), "queue allocation exceeds the documented memory budget")
	})

	t.Run("test_configuration_rejects_udp_packet_size_above_protocol_maximum", func(t *testing.T) {
		g := testkit.Expect(t)
		validGatewayConfigEnvironment(t)
		t.Setenv("API_GATEWAY_QUILKIN_MAX_PACKET_BYTES", "65508")
		_, err := LoadConfig()
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("API_GATEWAY_QUILKIN_MAX_PACKET_BYTES")), "UDP payload exceeds the protocol maximum")
	})

	t.Run("test_configuration_rejects_replay_capacity_above_memory_budget", func(t *testing.T) {
		g := testkit.Expect(t)
		validGatewayConfigEnvironment(t)
		t.Setenv("API_GATEWAY_UDP_REPLAY_MAX_ENTRIES", "100000000")
		_, err := LoadConfig()
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("API_GATEWAY_UDP_REPLAY_MAX_ENTRIES")))
	})

	t.Run("test_configuration_rejects_rate_limiter_identity_capacity_above_memory_budget", func(t *testing.T) {
		g := testkit.Expect(t)
		validGatewayConfigEnvironment(t)
		t.Setenv("API_GATEWAY_UDP_LIMITER_MAX_IDENTITIES", "100000000")
		_, err := LoadConfig()
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("API_GATEWAY_UDP_LIMITER_MAX_IDENTITIES")))
	})

	t.Run("test_configuration_rejects_malformed_resource_limit_values", func(t *testing.T) {
		g := testkit.Expect(t)
		validGatewayConfigEnvironment(t)
		t.Setenv("API_GATEWAY_QUILKIN_QUEUE_DEPTH", "not-an-integer")
		_, err := LoadConfig()
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("API_GATEWAY_QUILKIN_QUEUE_DEPTH")))
	})

	t.Run("test_configuration_does_not_silently_coerce_invalid_production_values", func(t *testing.T) {
		g := testkit.Expect(t)
		validGatewayConfigEnvironment(t)
		t.Setenv("API_GATEWAY_QUILKIN_UDP_ADDR", "   ")
		_, err := LoadConfig()
		g.Expect(err).To(gomega.MatchError(gomega.ContainSubstring("API_GATEWAY_QUILKIN_UDP_ADDR")), "blank production listener address was silently replaced by a default")
	})
}
