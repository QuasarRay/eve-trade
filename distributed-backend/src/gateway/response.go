package gateway

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"strings"

	"encore.dev/beta/errs"
)

const maxUDPResponseBytes = 1200

func (s *QuilkinUDPServer) writeError(conn net.PacketConn, remote net.Addr, interactionID string, code string, message string) {
	body, _ := json.Marshal(map[string]string{
		"interaction_id": interactionID,
		"status":         errorResponseStatus(code),
		"code":           code,
		"message":        message,
	})
	_ = s.writeResponse(conn, remote, interactionID, body)
}

func (s *QuilkinUDPServer) writeCachedError(conn net.PacketConn, remote net.Addr, principalID int64, interactionID string, fingerprint [sha256.Size]byte, code string, message string) {
	body, _ := json.Marshal(map[string]string{
		"interaction_id": interactionID,
		"status":         errorResponseStatus(code),
		"code":           code,
		"message":        message,
	})
	s.replay().completeForPrincipal(principalID, interactionID, fingerprint, body)
	_ = s.writeResponse(conn, remote, interactionID, body)
}

func errorResponseStatus(code string) string {
	if code == "request_in_progress" || code == "downstream_timeout" || code == "downstream_unavailable" || code == "queue_full" || code == "rate_limited" || code == "gateway_capacity" {
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
		signingBytes, err := envelopeSigningBytes(edgeResponseEnvelopeSchema, hmacSHA256Algorithm, s.hmacKeyID, canonicalBody)
		if err != nil {
			return fmt.Errorf("bind UDP response authentication metadata: %w", err)
		}
		_, _ = mac.Write(signingBytes)
		responseBody, err = json.Marshal(edgeEnvelope{
			SchemaVersion: edgeResponseEnvelopeSchema,
			Payload:       json.RawMessage(canonicalBody),
			Auth: &edgeAuth{
				Algorithm: hmacSHA256Algorithm,
				KeyID:     s.hmacKeyID,
				Signature: base64.RawURLEncoding.EncodeToString(mac.Sum(nil)),
			},
		})
		if err != nil {
			return fmt.Errorf("encode signed UDP response: %w", err)
		}
	}
	if len(responseBody) > maxUDPResponseBytes {
		return fmt.Errorf("UDP response exceeds %d byte limit", maxUDPResponseBytes)
	}
	if _, writeErr := conn.WriteTo(responseBody, remote); writeErr != nil {
		slog.Warn("udp response write failed", "remote", remoteKey(remote), "interaction_id", interactionID, "error", writeErr)
		return writeErr
	}
	return nil
}

func responseSigningBytes(schemaVersion string, keyID string, canonicalBody []byte) ([]byte, error) {
	return envelopeSigningBytes(schemaVersion, hmacSHA256Algorithm, keyID, canonicalBody)
}

func envelopeSigningBytes(schemaVersion string, algorithm string, keyID string, canonicalBody []byte) ([]byte, error) {
	payload, err := decodeStrictJSON(canonicalBody)
	if err != nil {
		return nil, err
	}
	return marshalCanonicalJSON(map[string]any{
		"algorithm":      algorithm,
		"domain":         envelopeSigningDomain,
		"key_id":         keyID,
		"payload":        payload,
		"schema_version": schemaVersion,
	})
}

func canonicalJSON(body []byte) ([]byte, error) {
	value, err := decodeStrictJSON(body)
	if err != nil {
		return nil, err
	}
	return marshalCanonicalJSON(value)
}

func marshalCanonicalJSON(value any) ([]byte, error) {
	var output bytes.Buffer
	encoder := json.NewEncoder(&output)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(output.Bytes(), []byte("\n")), nil
}

func stableDownstreamCode(err error) string {
	var failure *downstreamFailure
	if errors.As(err, &failure) {
		return failure.code
	}
	return stableEncoreCode(errs.Code(err))
}

func stableEncoreCode(code errs.ErrCode) string {
	switch code {
	case errs.DeadlineExceeded:
		return "downstream_timeout"
	case errs.InvalidArgument:
		return "invalid_argument"
	case errs.FailedPrecondition:
		return "failed_precondition"
	case errs.PermissionDenied:
		return "permission_denied"
	case errs.Aborted:
		return "replay"
	case errs.Unavailable:
		return "downstream_unavailable"
	default:
		return "downstream_error"
	}
}

func stableDownstreamMessage(err error) string {
	var failure *downstreamFailure
	if errors.As(err, &failure) {
		return failure.message
	}
	return stableEncoreMessage(errs.Code(err), err.Error())
}

func stableEncoreMessage(code errs.ErrCode, message string) string {
	switch code {
	case errs.DeadlineExceeded:
		return "downstream timeout"
	case errs.InvalidArgument, errs.FailedPrecondition, errs.PermissionDenied:
		return sanitizeClientMessage(message)
	case errs.Aborted:
		return sanitizeClientMessage("replay rejected: " + message)
	case errs.Unavailable:
		return "downstream unavailable"
	default:
		return "downstream error"
	}
}

func downstreamInternalError(message string) error {
	return &downstreamFailure{code: "internal", message: message}
}

type downstreamFailure struct {
	code    string
	message string
}

func (e *downstreamFailure) Error() string {
	return e.message
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
