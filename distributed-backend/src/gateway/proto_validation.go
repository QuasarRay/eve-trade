package gateway

import (
	"errors"
	"fmt"
	"strings"

	"buf.build/go/protovalidate"
	api_gatewayv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/api_gateway/v1"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

func validateGatewayProto(message proto.Message) error {
	return protovalidate.Validate(message)
}

func validateListenerConfig(maxPacket int, workers int, queueDepth int) error {
	if err := validateGatewayProto(&api_gatewayv1.QuilkinUdpServerConfig{
		MaxPacketBytes: int64(maxPacket),
		WorkerCount:    int64(workers),
		QueueDepth:     int64(queueDepth),
	}); err != nil {
		return fmt.Errorf("invalid UDP server config: %w", err)
	}
	return nil
}

func validateInboundPacket(packet []byte) *packetRejection {
	if err := validateGatewayProto(&api_gatewayv1.UdpInboundPacket{Packet: packet}); err != nil {
		return reject("empty_packet", "empty packet")
	}
	return nil
}

func validateEdgeEnvelope(envelope edgeEnvelope) *packetRejection {
	view := &api_gatewayv1.UdpEdgeEnvelope{
		SchemaVersion: strings.TrimSpace(envelope.SchemaVersion),
		PayloadJson:   strings.TrimSpace(string(envelope.Payload)),
	}
	if envelope.Auth != nil {
		view.Auth = &api_gatewayv1.UdpEdgeAuth{
			Algorithm: strings.TrimSpace(envelope.Auth.Algorithm),
			KeyId:     strings.TrimSpace(envelope.Auth.KeyID),
			Signature: strings.TrimSpace(envelope.Auth.Signature),
		}
	}
	if err := validateGatewayProto(view); err != nil {
		return edgeEnvelopeRejection(err)
	}
	return nil
}

func validateAuthenticatedActor(rawPayload []byte, authenticatedCapsuleerID int64) *packetRejection {
	var interaction marketv1.TradeGuiInteraction
	if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(rawPayload, &interaction); err != nil {
		return reject("malformed_packet", "malformed packet")
	}
	actor := &api_gatewayv1.AuthenticatedTradeGuiActor{
		AuthenticatedCapsuleerId: authenticatedCapsuleerID,
		Action:                   strings.TrimSpace(interaction.GetUi().GetAction()),
	}
	if input := interaction.GetInput(); input != nil {
		actor.IssuedByCapsuleerId = input.GetIssuedByCapsuleerId()
		actor.BuyerCapsuleerId = input.GetBuyerCapsuleerId()
		actor.CancelledByCapsuleerId = input.GetCancelledByCapsuleerId()
		actor.SellerCapsuleerId = input.GetSellerCapsuleerId()
		actor.AcceptedByCapsuleerId = input.GetAcceptedByCapsuleerId()
		actor.DelegatedCapsuleerId = input.GetDelegatedCapsuleerId()
		if itemStack := input.GetItemStack(); itemStack != nil {
			actor.ItemStackOwnerId = itemStack.GetOwnerId()
		}
	}
	if err := validateGatewayProto(actor); err != nil {
		return actorRejection(actor, err)
	}
	return nil
}

func validateMarketResponseIdentity(requestInteractionID string, responseInteractionID string) error {
	if err := validateGatewayProto(&api_gatewayv1.MarketResponseIdentity{
		RequestInteractionId:  requestInteractionID,
		ResponseInteractionId: responseInteractionID,
	}); err != nil {
		return downstreamInternalError("downstream response identity mismatch")
	}
	return nil
}

func edgeEnvelopeRejection(err error) *packetRejection {
	for _, violation := range validationViolations(err) {
		field := protovalidate.FieldPathString(violation.Proto.GetField())
		ruleID := violation.Proto.GetRuleId()
		switch {
		case ruleID == "eve.string.edge_schema_version":
			return reject("unsupported_envelope", "unsupported packet envelope")
		case field == "payload_json":
			return reject("empty_payload", "empty payload")
		case field == "auth":
			return reject("missing_signature", "missing signature")
		case ruleID == "eve.string.hmac_sha256_algorithm":
			return reject("unsupported_signature", "unsupported signature")
		case strings.HasPrefix(field, "auth."):
			return reject("invalid_signature", "invalid signature")
		}
	}
	return reject("malformed_packet", "malformed packet")
}

func actorRejection(actor *api_gatewayv1.AuthenticatedTradeGuiActor, err error) *packetRejection {
	for _, violation := range validationViolations(err) {
		switch violation.Proto.GetRuleId() {
		case "eve.string.trade_gui_action":
			return reject("unsupported_action", "unsupported trade GUI action")
		case "gateway.actor.issue_matches_principal":
			if actor.GetIssuedByCapsuleerId() <= 0 {
				return reject("missing_principal", "capsuleer identity is required")
			}
			return reject("principal_mismatch", "authenticated capsuleer does not match request actor")
		case "gateway.actor.accept_matches_principal":
			if actor.GetBuyerCapsuleerId() <= 0 {
				return reject("missing_principal", "capsuleer identity is required")
			}
			return reject("principal_mismatch", "authenticated capsuleer does not match request actor")
		case "gateway.actor.cancel_matches_principal":
			if actor.GetCancelledByCapsuleerId() <= 0 {
				return reject("missing_principal", "capsuleer identity is required")
			}
			return reject("principal_mismatch", "authenticated capsuleer does not match request actor")
		case "gateway.actor.item_stack_owner_matches_principal", "gateway.actor.all_known_actor_claims_match_principal":
			return reject("principal_mismatch", "authenticated capsuleer does not match actor field")
		}
	}
	return reject("malformed_packet", "malformed packet")
}

func validationViolations(err error) []*protovalidate.Violation {
	var validationError *protovalidate.ValidationError
	if errors.As(err, &validationError) {
		return validationError.Violations
	}
	return nil
}
