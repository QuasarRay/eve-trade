package market

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"time"

	"github.com/QuasarRay/eve-trade/gametrade"
	"google.golang.org/protobuf/types/known/timestamppb"

	"encore.dev/beta/errs"
)

func (h *MarketHandler) replayIssueTradeInstance(ctx context.Context, message issueTradeInstanceRequest) (*issueTradeInstanceResult, bool, error) {
	replay, err := h.loadReplay(ctx, message.IdempotencyKey)
	if err != nil || replay == nil {
		return nil, false, err
	}
	if ok, err := replayRequestFingerprintMatches(replay, "issue_trade_instance", message); err != nil || !ok {
		return nil, false, errOrConflict(err, message.IdempotencyKey)
	}
	createTrade := replayPayload(replay, "create_new_trade_instance_row")
	itemEscrow := replayPayload(replay, "transfer_quantity_from_item_stack_to_item_stack_escrow")
	if createTrade == nil || itemEscrow == nil {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	if replay.ExternalRequestID != message.ExternalRequestID ||
		int64Field(createTrade, "issuer_id") != message.IssuedByCapsuleerID ||
		int64Field(createTrade, "total_quantity") != message.Quantity ||
		int64Field(createTrade, "unit_price_isk") != message.UnitPriceISK ||
		stringField(itemEscrow, "source_item_stack_id") != message.ItemStack.ItemStackID ||
		!timestampFieldMatches(createTrade, "expires_at", message.ExpiresAt) {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	if message.ItemStack.ItemTypeID != 0 && int64Field(createTrade, "item_type_id") != message.ItemStack.ItemTypeID {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	if message.ItemStack.StationID != 0 && int64Field(createTrade, "station_id") != message.ItemStack.StationID {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	return &issueTradeInstanceResult{
		TradeInstanceID:   stringField(createTrade, "trade_instance_id"),
		ItemStackEscrowID: stringField(itemEscrow, "item_stack_escrow_id"),
		SettlementBatchID: replay.SettlementBatchID,
	}, true, nil
}

func (h *MarketHandler) replayAcceptTradeInstance(ctx context.Context, message acceptTradeInstanceRequest) (*acceptTradeInstanceResult, bool, error) {
	replay, err := h.loadReplay(ctx, message.IdempotencyKey)
	if err != nil || replay == nil {
		return nil, false, err
	}
	if ok, err := replayRequestFingerprintMatches(replay, "accept_trade_instance", message); err != nil || !ok {
		return nil, false, errOrConflict(err, message.IdempotencyKey)
	}
	walletEscrow := replayPayload(replay, "transfer_isk_amount_from_wallet_to_wallet_escrow")
	itemTransfer := replayPayload(replay, "transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner")
	createdDestination := replayPayload(replay, "create_new_empty_item_stack") != nil
	if walletEscrow == nil || itemTransfer == nil {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	if replay.ExternalRequestID != message.ExternalRequestID ||
		replay.CausedByCapsuleerID != message.BuyerCapsuleerID ||
		stringField(walletEscrow, "source_wallet_id") != message.BuyerWalletID ||
		stringField(walletEscrow, "trade_instance_id") != message.TradeInstanceID ||
		int64Field(itemTransfer, "quantity") != message.QuantityRequested {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	destinationItemStackID := stringField(itemTransfer, "destination_item_stack_id")
	switch {
	case message.BuyerDestinationItemStackID == "" && !createdDestination:
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	case message.BuyerDestinationItemStackID != "" && createdDestination:
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	case message.BuyerDestinationItemStackID != "" && message.BuyerDestinationItemStackID != destinationItemStackID:
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	return &acceptTradeInstanceResult{
		WalletEscrowID:              stringField(walletEscrow, "wallet_escrow_id"),
		BuyerDestinationItemStackID: destinationItemStackID,
		SettlementBatchID:           replay.SettlementBatchID,
	}, true, nil
}

func (h *MarketHandler) replayCancelTradeInstance(ctx context.Context, message cancelTradeInstanceRequest) (*cancelTradeInstanceResult, bool, error) {
	replay, err := h.loadReplay(ctx, message.IdempotencyKey)
	if err != nil || replay == nil {
		return nil, false, err
	}
	if ok, err := replayRequestFingerprintMatches(replay, "cancel_trade_instance", message); err != nil || !ok {
		return nil, false, errOrConflict(err, message.IdempotencyKey)
	}
	stateChange := replayPayload(replay, "modify_trade_instance_state")
	if stateChange == nil {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	if replay.ExternalRequestID != message.ExternalRequestID ||
		replay.CausedByCapsuleerID != message.CancelledByCapsuleerID ||
		stringField(stateChange, "trade_instance_id") != message.TradeInstanceID {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	return &cancelTradeInstanceResult{
		SettlementBatchID: replay.SettlementBatchID,
	}, true, nil
}

func (h *MarketHandler) loadReplay(ctx context.Context, idempotencyKey string) (*IdempotencyReplay, error) {
	if idempotencyKey == "" {
		return nil, nil
	}
	replay, err := h.trades.LoadCompletedIdempotencyReplay(ctx, idempotencyKey)
	if err != nil {
		return nil, apiError(errs.Unavailable, err)
	}
	return replay, nil
}

func replayPayload(replay *IdempotencyReplay, kind string) map[string]AnyJSON {
	for _, step := range replay.Steps {
		if step.StepKind != kind {
			continue
		}
		payload, _ := step.Payload["payload"].(map[string]AnyJSON)
		return payload
	}
	return nil
}

func idempotencyConflict(idempotencyKey string) error {
	return apiError(errs.Aborted, fmt.Errorf("idempotency_key %s was already used with a different request fingerprint", idempotencyKey))
}

func attachRequestFingerprint(plan *gametrade.SettlementPlan, requestKind string, message any) error {
	fingerprint, err := marketRequestFingerprint(requestKind, message)
	if err != nil {
		return err
	}
	plan.RequestFingerprint = fingerprint
	return nil
}

func replayRequestFingerprintMatches(replay *IdempotencyReplay, requestKind string, message any) (bool, error) {
	fingerprint, err := marketRequestFingerprint(requestKind, message)
	if err != nil {
		return false, err
	}
	return replay.RequestFingerprint == fingerprint, nil
}

func marketRequestFingerprint(requestKind string, message any) (string, error) {
	body, err := json.Marshal(message)
	if err != nil {
		return "", fmt.Errorf("marshal market request fingerprint: %w", err)
	}
	sum := sha256.Sum256(append([]byte(requestKind+":"), body...))
	return "market." + requestKind + ".sha256:" + hex.EncodeToString(sum[:]), nil
}

func errOrConflict(err error, idempotencyKey string) error {
	if err != nil {
		return apiError(errs.InvalidArgument, err)
	}
	return idempotencyConflict(idempotencyKey)
}

func stringField(payload map[string]AnyJSON, name string) string {
	value, _ := payload[name].(string)
	return value
}

func int64Field(payload map[string]AnyJSON, name string) int64 {
	switch value := payload[name].(type) {
	case float64:
		return int64(value)
	case int64:
		return value
	case int:
		return int64(value)
	default:
		return 0
	}
}

func timestampFieldMatches(payload map[string]AnyJSON, name string, timestamp *timestamppb.Timestamp) bool {
	value, exists := payload[name]
	if timestamp == nil {
		return !exists || value == nil || value == ""
	}
	if value == nil {
		return false
	}
	expected := timestamp.AsTime().UTC()
	if text, ok := value.(string); ok {
		actual, err := time.Parse(time.RFC3339Nano, text)
		return err == nil && actual.UTC().Equal(expected)
	}
	return false
}
