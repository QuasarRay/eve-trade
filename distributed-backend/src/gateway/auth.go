package gateway

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"strings"
)

const (
	edgeEnvelopeSchema         = "eve-trade-edge.v2"
	edgeResponseEnvelopeSchema = "eve-trade-edge-response.v2"
	hmacSHA256Algorithm        = "hmac-sha256"
	envelopeSigningDomain      = "eve-trade.udp-envelope.hmac-sha256.v1"
)

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
	if _, err := decodeStrictJSON(packet); err != nil {
		return nil, "", 0, reject("malformed_packet", "malformed packet")
	}
	decoder := json.NewDecoder(bytes.NewReader(packet))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&envelope); err != nil {
		return nil, "", 0, reject("malformed_packet", "malformed packet")
	}
	if rejection := validateEdgeEnvelope(envelope); rejection != nil {
		return nil, "", 0, rejection
	}
	canonicalPayload, err := canonicalJSON(envelope.Payload)
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
	signingBytes, err := envelopeSigningBytes(
		envelope.SchemaVersion,
		envelope.Auth.Algorithm,
		envelope.Auth.KeyID,
		canonicalPayload,
	)
	if err != nil {
		return nil, "", 0, reject("malformed_packet", "malformed packet")
	}
	_, _ = mac.Write(signingBytes)
	if !hmac.Equal(signature, mac.Sum(nil)) {
		return nil, "", 0, reject("invalid_signature", "invalid signature")
	}
	interactionID, err := extractInteractionID(canonicalPayload)
	if err != nil {
		return nil, "", 0, reject("malformed_packet", "malformed packet")
	}
	if rejection := validateAuthenticatedActor(canonicalPayload, credential.CapsuleerID); rejection != nil {
		return nil, "", 0, rejection
	}
	return canonicalPayload, interactionID, credential.CapsuleerID, nil
}

func extractInteractionID(rawPayload []byte) (string, error) {
	if _, err := decodeStrictJSON(rawPayload); err != nil {
		return "", err
	}
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
