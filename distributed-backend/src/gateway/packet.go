package gateway

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"log/slog"
	"net"
	"time"

	"github.com/QuasarRay/eve-trade/distributed-backend/src/market"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

func (s *QuilkinUDPServer) handlePacket(parent context.Context, conn net.PacketConn, remote net.Addr, packet []byte) {
	ctx, receiveSpan := udpTracer.Start(parent, "gateway.receive_ui_activity", trace.WithAttributes(
		attribute.Int("network.packet.size", len(packet)),
		attribute.String("network.transport", "udp"),
	))
	defer receiveSpan.End()

	if rejection := validateInboundPacket(packet); rejection != nil {
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
	if s.handleReplay(parent, conn, remote, packet, interactionID, fingerprint, receiveSpan) {
		return
	}

	receiveSpan.SetAttributes(attribute.String("interaction_id", interactionID), attribute.String("validation.result", "accepted"))
	ctx, cancel := context.WithTimeout(ctx, s.timeout)
	defer cancel()

	body, elapsed, callErr := s.forwardToMarket(ctx, interactionID, rawPayload)
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

	s.replay().complete(interactionID, fingerprint, body)
	if writeErr := s.writeResponse(conn, remote, interactionID, body); writeErr != nil {
		recordUDPPacket(parent, "write_failed", len(packet))
		return
	}

	slog.Info("udp downstream call succeeded", "remote", remoteKey(remote), "interaction_id", interactionID, "duration_ms", elapsed.Milliseconds())
	recordUDPPacket(parent, "success", len(packet))
}

func (s *QuilkinUDPServer) handleReplay(parent context.Context, conn net.PacketConn, remote net.Addr, packet []byte, interactionID string, fingerprint [sha256.Size]byte, span trace.Span) bool {
	replayState, cachedResponse := s.replay().begin(interactionID, fingerprint)
	switch replayState {
	case replayCached:
		span.SetAttributes(attribute.String("interaction_id", interactionID), attribute.String("validation.result", "cached"))
		slog.Info("udp retry served from response cache", "remote", remoteKey(remote), "interaction_id", interactionID)
		recordUDPPacket(parent, "cached", len(packet))
		if writeErr := s.writeResponse(conn, remote, interactionID, cachedResponse); writeErr != nil {
			recordUDPPacket(parent, "write_failed", len(packet))
		}
		return true
	case replayInFlight:
		span.SetAttributes(attribute.String("interaction_id", interactionID), attribute.String("validation.result", "retry_later"))
		slog.Info("udp retry is already in progress", "remote", remoteKey(remote), "interaction_id", interactionID)
		recordUDPPacket(parent, "request_in_progress", len(packet))
		s.writeError(conn, remote, interactionID, "request_in_progress", "request is still in progress")
		return true
	case replayConflict:
		span.SetAttributes(attribute.String("interaction_id", interactionID), attribute.String("validation.result", "rejected"), attribute.String("rejection.reason", "replay_payload_mismatch"))
		slog.Warn("udp replay payload mismatch rejected", "remote", remoteKey(remote), "interaction_id", interactionID)
		recordUDPPacket(parent, "replay", len(packet))
		s.writeError(conn, remote, interactionID, "replay", "replay rejected: interaction_id was already used with a different payload")
		return true
	default:
		return false
	}
}

func (s *QuilkinUDPServer) forwardToMarket(ctx context.Context, interactionID string, rawPayload []byte) ([]byte, time.Duration, error) {
	start := time.Now()
	forwardCtx, forwardSpan := udpTracer.Start(ctx, "gateway.forward_to_market", trace.WithAttributes(attribute.String("interaction_id", interactionID)))
	response, callErr := s.market.SubmitTradeGuiInteraction(forwardCtx, &market.SubmitTradeGuiInteractionRequest{
		RawPayload: rawPayload,
	})
	if callErr != nil {
		forwardSpan.RecordError(callErr)
		forwardSpan.SetAttributes(attribute.String("error.kind", stableDownstreamCode(callErr)))
		forwardSpan.End()
		return nil, time.Since(start), callErr
	}
	forwardSpan.End()

	if response == nil {
		slog.Error("udp downstream returned nil response", "request_interaction_id", interactionID)
		return nil, time.Since(start), &downstreamFailure{code: "downstream_unavailable", message: "downstream unavailable"}
	}
	if err := validateMarketResponseIdentity(interactionID, response.InteractionID); err != nil {
		slog.Error("udp downstream response interaction mismatch", "request_interaction_id", interactionID, "response_interaction_id", response.InteractionID)
		return nil, time.Since(start), err
	}

	body, marshalErr := json.Marshal(response)
	if marshalErr != nil {
		slog.Error("udp response marshal failed", "interaction_id", interactionID, "error", marshalErr)
		return nil, time.Since(start), downstreamInternalError("internal error")
	}
	return body, time.Since(start), nil
}
