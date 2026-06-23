package distributedbackend

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"time"

	"connectrpc.com/connect"
	gametrade "github.com/QuasarRay/eve-trade/market/game-trade"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
	marketv1connect "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1/marketv1connect"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"
)

var _ marketv1connect.MarketServiceHandler = (*MarketHandler)(nil)

type MarketHandler struct {
	settlement SettlementExecutor
	trades     TradeRepository
}

func NewMarketHandler(settlement SettlementExecutor, trades TradeRepository) *MarketHandler {
	return &MarketHandler{settlement: settlement, trades: trades}
}

func (h *MarketHandler) IssueTradeInstance(ctx context.Context, request *connect.Request[marketv1.IssueTradeInstanceRequest]) (*connect.Response[marketv1.IssueTradeInstanceResponse], error) {
	message := request.Msg
	if message.ItemStack == nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("item_stack is required"))
	}
	if response, ok, err := h.replayIssueTradeInstance(ctx, message); ok || err != nil {
		return response, err
	}
	if message.ItemStack.ItemStackId == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("item_stack_id is required"))
	}
	itemStack, err := h.trades.LoadItemStack(ctx, message.ItemStack.ItemStackId)
	if err != nil {
		return nil, connect.NewError(connect.CodeFailedPrecondition, err)
	}
	if itemStack.OwnerID != message.IssuedByCapsuleerId {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("item stack owner must match issued_by_capsuleer_id"))
	}
	if itemStack.StackState != "ACTIVE" {
		return nil, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("item_stack is not ACTIVE"))
	}
	if message.ItemStack.OwnerId != 0 && message.ItemStack.OwnerId != itemStack.OwnerID {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("item_stack owner_id does not match canonical item stack"))
	}
	if message.ItemStack.ItemTypeId != 0 && message.ItemStack.ItemTypeId != itemStack.ItemTypeID {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("item_stack item_type_id does not match canonical item stack"))
	}
	if message.ItemStack.StationId != 0 && message.ItemStack.StationId != itemStack.StationID {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("item_stack station_id does not match canonical item stack"))
	}
	if message.ItemStack.Quantity != 0 && message.ItemStack.Quantity != itemStack.Quantity {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("item_stack quantity does not match canonical item stack"))
	}

	plan, err := gametrade.IssueTradeInstance(gametrade.IssueTradeInstanceInput{
		IdempotencyKey:      message.IdempotencyKey,
		ExternalRequestID:   message.ExternalRequestId,
		IssuedByCapsuleerID: message.IssuedByCapsuleerId,
		ItemStack: gametrade.ItemStackRow{
			ItemStackID: itemStack.ItemStackID,
			OwnerID:     itemStack.OwnerID,
			ItemTypeID:  itemStack.ItemTypeID,
			StationID:   itemStack.StationID,
			Quantity:    itemStack.Quantity,
		},
		Quantity:     message.Quantity,
		UnitPriceISK: message.UnitPriceIsk,
		ExpiresAt:    message.ExpiresAt,
	})
	if err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}
	if err := attachRequestFingerprint(&plan, "issue_trade_instance", message); err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}

	settlementResponse, err := h.executePlan(ctx, plan)
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.IssueTradeInstanceResponse{
		TradeInstanceId:   plan.TradeInstanceID,
		ItemStackEscrowId: plan.ItemStackEscrowID,
		SettlementBatchId: settlementResponse.SettlementBatchId,
	}), nil
}

func (h *MarketHandler) AcceptTradeInstance(ctx context.Context, request *connect.Request[marketv1.AcceptTradeInstanceRequest]) (*connect.Response[marketv1.AcceptTradeInstanceResponse], error) {
	message := request.Msg
	if response, ok, err := h.replayAcceptTradeInstance(ctx, message); ok || err != nil {
		return response, err
	}
	trade, err := h.loadAcceptableTrade(ctx, message.TradeInstanceId, message.QuantityRequested)
	if err != nil {
		return nil, err
	}
	buyerWallet, err := h.trades.LoadWallet(ctx, message.BuyerWalletId)
	if err != nil {
		return nil, connect.NewError(connect.CodeFailedPrecondition, err)
	}
	if buyerWallet.CapsuleerID != message.BuyerCapsuleerId {
		return nil, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("buyer_wallet_id is not owned by buyer_capsuleer_id"))
	}
	if buyerWallet.WalletState != "ACTIVE" {
		return nil, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("buyer wallet is not ACTIVE"))
	}
	sellerWallet, err := h.trades.LoadPrimaryWallet(ctx, trade.IssuerID)
	if err != nil {
		return nil, connect.NewError(connect.CodeFailedPrecondition, err)
	}
	if sellerWallet.WalletState != "ACTIVE" {
		return nil, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("seller wallet is not ACTIVE"))
	}
	destinationItemStackID := message.BuyerDestinationItemStackId
	createDestinationItemStack := destinationItemStackID == ""
	if destinationItemStackID != "" {
		destination, err := h.trades.LoadItemStack(ctx, destinationItemStackID)
		if err != nil {
			return nil, connect.NewError(connect.CodeFailedPrecondition, err)
		}
		if destination.OwnerID != message.BuyerCapsuleerId {
			return nil, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("buyer_destination_item_stack_id is not owned by buyer_capsuleer_id"))
		}
		if destination.StackState != "ACTIVE" {
			return nil, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("buyer destination item stack is not ACTIVE"))
		}
		if destination.ItemTypeID != trade.ItemTypeID || destination.StationID != trade.StationID {
			return nil, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("buyer destination item stack must match trade item type and station"))
		}
	}
	iskAmountPaid, err := checkedISKAmount(message.QuantityRequested, trade.UnitPriceISK)
	if err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}

	plan, err := gametrade.AcceptTradeInstance(gametrade.AcceptTradeInstanceInput{
		IdempotencyKey:                  message.IdempotencyKey,
		ExternalRequestID:               message.ExternalRequestId,
		TradeInstanceID:                 message.TradeInstanceId,
		BuyerCapsuleerID:                message.BuyerCapsuleerId,
		SellerCapsuleerID:               trade.IssuerID,
		ItemTypeID:                      trade.ItemTypeID,
		StationID:                       trade.StationID,
		QuantityRequested:               message.QuantityRequested,
		ISKAmountPaid:                   iskAmountPaid,
		BuyerWalletID:                   message.BuyerWalletId,
		SellerWalletID:                  sellerWallet.WalletID,
		ItemStackEscrowID:               trade.ItemStackEscrowID,
		BuyerDestinationItemStackID:     destinationItemStackID,
		CreateBuyerDestinationItemStack: createDestinationItemStack,
		CompleteTrade:                   message.QuantityRequested == trade.EscrowQuantity,
	})
	if err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}
	if err := attachRequestFingerprint(&plan, "accept_trade_instance", message); err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}

	settlementResponse, err := h.executePlan(ctx, plan)
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.AcceptTradeInstanceResponse{
		WalletEscrowId:              plan.WalletEscrowID,
		BuyerDestinationItemStackId: plan.DestinationItemStackID,
		SettlementBatchId:           settlementResponse.SettlementBatchId,
	}), nil
}

func (h *MarketHandler) CancelTradeInstance(ctx context.Context, request *connect.Request[marketv1.CancelTradeInstanceRequest]) (*connect.Response[marketv1.CancelTradeInstanceResponse], error) {
	message := request.Msg
	if response, ok, err := h.replayCancelTradeInstance(ctx, message); ok || err != nil {
		return response, err
	}
	trade, err := h.loadCancellableTrade(ctx, message.TradeInstanceId, message.CancelledByCapsuleerId)
	if err != nil {
		return nil, err
	}
	plan, err := gametrade.CancelTradeInstance(gametrade.CancelTradeInstanceInput{
		IdempotencyKey:         message.IdempotencyKey,
		ExternalRequestID:      message.ExternalRequestId,
		TradeInstanceID:        message.TradeInstanceId,
		CancelledByCapsuleerID: message.CancelledByCapsuleerId,
		ItemStackEscrowID:      trade.ItemStackEscrowID,
		ReturnItemStackID:      trade.SourceItemStackID,
		ReturnQuantity:         trade.EscrowQuantity,
	})
	if err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}
	if err := attachRequestFingerprint(&plan, "cancel_trade_instance", message); err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}

	settlementResponse, err := h.executePlan(ctx, plan)
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.CancelTradeInstanceResponse{
		SettlementBatchId: settlementResponse.SettlementBatchId,
	}), nil
}

func (h *MarketHandler) executePlan(ctx context.Context, plan gametrade.SettlementPlan) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	settlementRequest, err := gametrade.SettleTradeInstance(plan)
	if err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}
	response, err := h.settlement.ExecuteSettlementBatch(ctx, settlementRequest)
	if err != nil {
		return nil, downstreamUnavailable("trade-settlement", err)
	}
	return response, nil
}

func (h *MarketHandler) replayIssueTradeInstance(ctx context.Context, message *marketv1.IssueTradeInstanceRequest) (*connect.Response[marketv1.IssueTradeInstanceResponse], bool, error) {
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
	if replay.ExternalRequestID != message.ExternalRequestId ||
		int64Field(createTrade, "issuer_id") != message.IssuedByCapsuleerId ||
		int64Field(createTrade, "total_quantity") != message.Quantity ||
		int64Field(createTrade, "unit_price_isk") != message.UnitPriceIsk ||
		stringField(itemEscrow, "source_item_stack_id") != message.ItemStack.ItemStackId ||
		!timestampFieldMatches(createTrade, "expires_at", message.ExpiresAt) {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	if message.ItemStack.ItemTypeId != 0 && int64Field(createTrade, "item_type_id") != message.ItemStack.ItemTypeId {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	if message.ItemStack.StationId != 0 && int64Field(createTrade, "station_id") != message.ItemStack.StationId {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	return connect.NewResponse(&marketv1.IssueTradeInstanceResponse{
		TradeInstanceId:   stringField(createTrade, "trade_instance_id"),
		ItemStackEscrowId: stringField(itemEscrow, "item_stack_escrow_id"),
		SettlementBatchId: replay.SettlementBatchID,
	}), true, nil
}

func (h *MarketHandler) replayAcceptTradeInstance(ctx context.Context, message *marketv1.AcceptTradeInstanceRequest) (*connect.Response[marketv1.AcceptTradeInstanceResponse], bool, error) {
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
	if replay.ExternalRequestID != message.ExternalRequestId ||
		replay.CausedByCapsuleerID != message.BuyerCapsuleerId ||
		stringField(walletEscrow, "source_wallet_id") != message.BuyerWalletId ||
		stringField(walletEscrow, "trade_instance_id") != message.TradeInstanceId ||
		int64Field(itemTransfer, "quantity") != message.QuantityRequested {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	destinationItemStackID := stringField(itemTransfer, "destination_item_stack_id")
	switch {
	case message.BuyerDestinationItemStackId == "" && !createdDestination:
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	case message.BuyerDestinationItemStackId != "" && createdDestination:
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	case message.BuyerDestinationItemStackId != "" && message.BuyerDestinationItemStackId != destinationItemStackID:
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	return connect.NewResponse(&marketv1.AcceptTradeInstanceResponse{
		WalletEscrowId:              stringField(walletEscrow, "wallet_escrow_id"),
		BuyerDestinationItemStackId: destinationItemStackID,
		SettlementBatchId:           replay.SettlementBatchID,
	}), true, nil
}

func (h *MarketHandler) replayCancelTradeInstance(ctx context.Context, message *marketv1.CancelTradeInstanceRequest) (*connect.Response[marketv1.CancelTradeInstanceResponse], bool, error) {
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
	if replay.ExternalRequestID != message.ExternalRequestId ||
		replay.CausedByCapsuleerID != message.CancelledByCapsuleerId ||
		stringField(stateChange, "trade_instance_id") != message.TradeInstanceId {
		return nil, false, idempotencyConflict(message.IdempotencyKey)
	}
	return connect.NewResponse(&marketv1.CancelTradeInstanceResponse{
		SettlementBatchId: replay.SettlementBatchID,
	}), true, nil
}

func (h *MarketHandler) loadReplay(ctx context.Context, idempotencyKey string) (*IdempotencyReplay, error) {
	if idempotencyKey == "" {
		return nil, nil
	}
	replay, err := h.trades.LoadCompletedIdempotencyReplay(ctx, idempotencyKey)
	if err != nil {
		return nil, connect.NewError(connect.CodeUnavailable, err)
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
	return connect.NewError(connect.CodeAborted, fmt.Errorf("idempotency_key %s was already used with a different request fingerprint", idempotencyKey))
}

func attachRequestFingerprint(plan *gametrade.SettlementPlan, requestKind string, message proto.Message) error {
	fingerprint, err := marketRequestFingerprint(requestKind, message)
	if err != nil {
		return err
	}
	plan.RequestFingerprint = fingerprint
	return nil
}

func replayRequestFingerprintMatches(replay *IdempotencyReplay, requestKind string, message proto.Message) (bool, error) {
	if !strings.HasPrefix(replay.RequestFingerprint, "market.") {
		return true, nil
	}
	fingerprint, err := marketRequestFingerprint(requestKind, message)
	if err != nil {
		return false, err
	}
	return replay.RequestFingerprint == fingerprint, nil
}

func marketRequestFingerprint(requestKind string, message proto.Message) (string, error) {
	body, err := proto.MarshalOptions{Deterministic: true}.Marshal(message)
	if err != nil {
		return "", fmt.Errorf("marshal market request fingerprint: %w", err)
	}
	sum := sha256.Sum256(append([]byte(requestKind+":"), body...))
	return "market." + requestKind + ".sha256:" + hex.EncodeToString(sum[:]), nil
}

func errOrConflict(err error, idempotencyKey string) error {
	if err != nil {
		return connect.NewError(connect.CodeInvalidArgument, err)
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

func (h *MarketHandler) loadAcceptableTrade(ctx context.Context, tradeInstanceID string, requestedQuantity int64) (TradeSnapshot, error) {
	if tradeInstanceID == "" {
		return TradeSnapshot{}, connect.NewError(connect.CodeInvalidArgument, errors.New("trade_instance_id is required"))
	}
	trade, err := h.trades.LoadTrade(ctx, tradeInstanceID)
	if err != nil {
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, err)
	}
	switch trade.TradeState {
	case gametrade.TradeStateOpen:
	case gametrade.TradeStateCancelled:
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("trade is cancelled"))
	case gametrade.TradeStateCompleted:
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("trade is completed"))
	default:
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("trade is not open"))
	}
	if trade.EscrowReleased {
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("item_stack_escrow %s is already released", trade.ItemStackEscrowID))
	}
	if trade.ExpiresAtValid && !trade.ExpiresAt.After(time.Now()) {
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("trade is expired"))
	}
	if requestedQuantity > trade.EscrowQuantity {
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("item_stack_escrow %s has %d, requested %d", trade.ItemStackEscrowID, trade.EscrowQuantity, requestedQuantity))
	}
	return trade, nil
}

func (h *MarketHandler) loadCancellableTrade(ctx context.Context, tradeInstanceID string, cancelledByCapsuleerID int64) (TradeSnapshot, error) {
	if tradeInstanceID == "" {
		return TradeSnapshot{}, connect.NewError(connect.CodeInvalidArgument, errors.New("trade_instance_id is required"))
	}
	trade, err := h.trades.LoadTrade(ctx, tradeInstanceID)
	if err != nil {
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, err)
	}
	if cancelledByCapsuleerID != trade.IssuerID {
		return TradeSnapshot{}, connect.NewError(connect.CodePermissionDenied, fmt.Errorf("only the trade issuer can cancel this trade"))
	}
	switch trade.TradeState {
	case gametrade.TradeStateOpen:
	case gametrade.TradeStateCancelled:
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("trade is cancelled"))
	case gametrade.TradeStateCompleted:
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("trade is completed"))
	default:
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("trade is not open"))
	}
	if trade.EscrowReleased || trade.EscrowQuantity <= 0 {
		return TradeSnapshot{}, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("item_stack_escrow %s is already released", trade.ItemStackEscrowID))
	}
	return trade, nil
}

func checkedISKAmount(quantity int64, unitPriceISK int64) (int64, error) {
	if quantity <= 0 {
		return 0, fmt.Errorf("quantity_requested must be greater than zero")
	}
	if unitPriceISK < 0 {
		return 0, fmt.Errorf("unit_price_isk must be non-negative")
	}
	const maxInt64 = int64(^uint64(0) >> 1)
	if unitPriceISK != 0 && quantity > maxInt64/unitPriceISK {
		return 0, fmt.Errorf("trade price overflows int64")
	}
	return quantity * unitPriceISK, nil
}
