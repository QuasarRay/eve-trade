package distributedbackend

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"connectrpc.com/connect"
	gametrade "github.com/QuasarRay/eve-trade/market/game-trade"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
	marketv1connect "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1/marketv1connect"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
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

type tradeGUIItemStackInput struct {
	ItemStackID string `json:"item_stack_id"`
	OwnerID     int64  `json:"owner_id"`
	ItemTypeID  int64  `json:"item_type_id"`
	StationID   int64  `json:"station_id"`
	Quantity    int64  `json:"quantity"`
}

type issueTradeInstanceRequest struct {
	IdempotencyKey      string
	ExternalRequestID   string
	IssuedByCapsuleerID int64
	ItemStack           *tradeGUIItemStackInput
	Quantity            int64
	UnitPriceISK        int64
	ExpiresAt           *timestamppb.Timestamp
}

type issueTradeInstanceResult struct {
	TradeInstanceID   string
	ItemStackEscrowID string
	SettlementBatchID string
}

type acceptTradeInstanceRequest struct {
	IdempotencyKey              string
	ExternalRequestID           string
	TradeInstanceID             string
	BuyerCapsuleerID            int64
	QuantityRequested           int64
	BuyerWalletID               string
	BuyerDestinationItemStackID string
}

type acceptTradeInstanceResult struct {
	WalletEscrowID              string
	BuyerDestinationItemStackID string
	SettlementBatchID           string
}

type cancelTradeInstanceRequest struct {
	IdempotencyKey         string
	ExternalRequestID      string
	TradeInstanceID        string
	CancelledByCapsuleerID int64
}

type cancelTradeInstanceResult struct {
	SettlementBatchID string
}

func (h *MarketHandler) issueTradeInstance(ctx context.Context, message issueTradeInstanceRequest) (*issueTradeInstanceResult, error) {
	if message.ItemStack == nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("item_stack is required"))
	}
	if response, ok, err := h.replayIssueTradeInstance(ctx, message); ok || err != nil {
		return response, err
	}
	if message.ItemStack.ItemStackID == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("item_stack_id is required"))
	}
	itemStack, err := h.trades.LoadItemStack(ctx, message.ItemStack.ItemStackID)
	if err != nil {
		return nil, connect.NewError(connect.CodeFailedPrecondition, err)
	}
	if itemStack.OwnerID != message.IssuedByCapsuleerID {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("item stack owner must match issued_by_capsuleer_id"))
	}
	if itemStack.StackState != "ACTIVE" {
		return nil, connect.NewError(connect.CodeFailedPrecondition, fmt.Errorf("item_stack is not ACTIVE"))
	}
	if message.ItemStack.OwnerID != 0 && message.ItemStack.OwnerID != itemStack.OwnerID {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("item_stack owner_id does not match canonical item stack"))
	}
	if message.ItemStack.ItemTypeID != 0 && message.ItemStack.ItemTypeID != itemStack.ItemTypeID {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("item_stack item_type_id does not match canonical item stack"))
	}
	if message.ItemStack.StationID != 0 && message.ItemStack.StationID != itemStack.StationID {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("item_stack station_id does not match canonical item stack"))
	}
	if message.ItemStack.Quantity != 0 && message.ItemStack.Quantity != itemStack.Quantity {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("item_stack quantity does not match canonical item stack"))
	}

	plan, err := gametrade.IssueTradeInstance(gametrade.IssueTradeInstanceInput{
		IdempotencyKey:      message.IdempotencyKey,
		ExternalRequestID:   message.ExternalRequestID,
		IssuedByCapsuleerID: message.IssuedByCapsuleerID,
		ItemStack: gametrade.ItemStackRow{
			ItemStackID: itemStack.ItemStackID,
			OwnerID:     itemStack.OwnerID,
			ItemTypeID:  itemStack.ItemTypeID,
			StationID:   itemStack.StationID,
			Quantity:    itemStack.Quantity,
		},
		Quantity:     message.Quantity,
		UnitPriceISK: message.UnitPriceISK,
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

	return &issueTradeInstanceResult{
		TradeInstanceID:   plan.TradeInstanceID,
		ItemStackEscrowID: plan.ItemStackEscrowID,
		SettlementBatchID: settlementResponse.SettlementBatchId,
	}, nil
}

func (h *MarketHandler) acceptTradeInstance(ctx context.Context, message acceptTradeInstanceRequest) (*acceptTradeInstanceResult, error) {
	if response, ok, err := h.replayAcceptTradeInstance(ctx, message); ok || err != nil {
		return response, err
	}
	trade, err := h.loadAcceptableTrade(ctx, message.TradeInstanceID, message.QuantityRequested)
	if err != nil {
		return nil, err
	}
	if message.BuyerCapsuleerID == trade.IssuerID {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("buyer and seller must differ"))
	}
	buyerWallet, err := h.trades.LoadWallet(ctx, message.BuyerWalletID)
	if err != nil {
		return nil, connect.NewError(connect.CodeFailedPrecondition, err)
	}
	if buyerWallet.CapsuleerID != message.BuyerCapsuleerID {
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
	destinationItemStackID := message.BuyerDestinationItemStackID
	createDestinationItemStack := destinationItemStackID == ""
	if destinationItemStackID != "" {
		destination, err := h.trades.LoadItemStack(ctx, destinationItemStackID)
		if err != nil {
			return nil, connect.NewError(connect.CodeFailedPrecondition, err)
		}
		if destination.OwnerID != message.BuyerCapsuleerID {
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
		ExternalRequestID:               message.ExternalRequestID,
		TradeInstanceID:                 message.TradeInstanceID,
		BuyerCapsuleerID:                message.BuyerCapsuleerID,
		SellerCapsuleerID:               trade.IssuerID,
		ItemTypeID:                      trade.ItemTypeID,
		StationID:                       trade.StationID,
		QuantityRequested:               message.QuantityRequested,
		ISKAmountPaid:                   iskAmountPaid,
		BuyerWalletID:                   message.BuyerWalletID,
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

	return &acceptTradeInstanceResult{
		WalletEscrowID:              plan.WalletEscrowID,
		BuyerDestinationItemStackID: plan.DestinationItemStackID,
		SettlementBatchID:           settlementResponse.SettlementBatchId,
	}, nil
}

func (h *MarketHandler) cancelTradeInstance(ctx context.Context, message cancelTradeInstanceRequest) (*cancelTradeInstanceResult, error) {
	if response, ok, err := h.replayCancelTradeInstance(ctx, message); ok || err != nil {
		return response, err
	}
	trade, err := h.loadCancellableTrade(ctx, message.TradeInstanceID, message.CancelledByCapsuleerID)
	if err != nil {
		return nil, err
	}
	plan, err := gametrade.CancelTradeInstance(gametrade.CancelTradeInstanceInput{
		IdempotencyKey:         message.IdempotencyKey,
		ExternalRequestID:      message.ExternalRequestID,
		TradeInstanceID:        message.TradeInstanceID,
		CancelledByCapsuleerID: message.CancelledByCapsuleerID,
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

	return &cancelTradeInstanceResult{
		SettlementBatchID: settlementResponse.SettlementBatchId,
	}, nil
}

type tradeGUIInteraction struct {
	SchemaVersion string `json:"schema_version"`
	InteractionID string `json:"interaction_id"`
	UI            struct {
		Window string `json:"window"`
		Button string `json:"button"`
		Action string `json:"action"`
	} `json:"ui"`
	Input tradeGUIInput `json:"input"`
}

type tradeGUIInput struct {
	IdempotencyKey              string                  `json:"idempotency_key"`
	ExternalRequestID           string                  `json:"external_request_id"`
	IssuedByCapsuleerID         int64                   `json:"issued_by_capsuleer_id"`
	CancelledByCapsuleerID      int64                   `json:"cancelled_by_capsuleer_id"`
	TradeInstanceID             string                  `json:"trade_instance_id"`
	BuyerCapsuleerID            int64                   `json:"buyer_capsuleer_id"`
	Quantity                    int64                   `json:"quantity"`
	QuantityRequested           int64                   `json:"quantity_requested"`
	UnitPriceISK                int64                   `json:"unit_price_isk"`
	BuyerWalletID               string                  `json:"buyer_wallet_id"`
	BuyerDestinationItemStackID string                  `json:"buyer_destination_item_stack_id"`
	ItemStack                   *tradeGUIItemStackInput `json:"item_stack"`
	ExpiresAt                   *timestamppb.Timestamp  `json:"-"`
}

func (h *MarketHandler) SubmitTradeGuiInteraction(ctx context.Context, request *connect.Request[marketv1.SubmitTradeGuiInteractionRequest]) (*connect.Response[marketv1.SubmitTradeGuiInteractionResponse], error) {
	message := request.Msg
	if len(message.RawPayload) == 0 {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("raw_payload is required"))
	}

	var interaction tradeGUIInteraction
	if err := json.Unmarshal(message.RawPayload, &interaction); err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("decode trade GUI packet: %w", err))
	}
	if interaction.SchemaVersion != "eve-trade-gui.v1" {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("unsupported trade GUI schema_version %q", interaction.SchemaVersion))
	}
	interactionID := strings.TrimSpace(interaction.InteractionID)
	if interactionID == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("interaction_id is required"))
	}
	input := interaction.Input
	if strings.TrimSpace(input.IdempotencyKey) == "" {
		input.IdempotencyKey = interactionID
	}
	if strings.TrimSpace(input.ExternalRequestID) == "" {
		input.ExternalRequestID = interactionID
	}

	action := strings.TrimSpace(interaction.UI.Action)
	switch action {
	case "market_place_sell_order", "contract_create_item_exchange", "direct_trade_offer":
		response, err := h.issueTradeInstance(ctx, issueTradeInstanceRequest{
			IdempotencyKey:      input.IdempotencyKey,
			ExternalRequestID:   input.ExternalRequestID,
			IssuedByCapsuleerID: input.IssuedByCapsuleerID,
			ItemStack:           input.ItemStack,
			Quantity:            input.Quantity,
			UnitPriceISK:        input.UnitPriceISK,
			ExpiresAt:           input.ExpiresAt,
		})
		if err != nil {
			return nil, err
		}
		return connect.NewResponse(&marketv1.SubmitTradeGuiInteractionResponse{
			InteractionId:     interactionID,
			Status:            "accepted",
			SettlementBatchId: response.SettlementBatchID,
			TradeInstanceId:   response.TradeInstanceID,
			ItemStackEscrowId: response.ItemStackEscrowID,
		}), nil
	case "market_buy_from_sell_order", "contract_accept_item_exchange", "direct_trade_accept":
		quantityRequested, quantityPresent, err := readGUIInputInt64(message.RawPayload, "quantity_requested")
		if err != nil {
			return nil, connect.NewError(connect.CodeInvalidArgument, err)
		}

		if !quantityPresent {
			quantityRequested, quantityPresent, err = readGUIInputInt64(message.RawPayload, "quantity")
			if err != nil {
				return nil, connect.NewError(connect.CodeInvalidArgument, err)
			}
		}

		if !quantityPresent {
			return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("quantity_requested is required"))
		}
		response, err := h.acceptTradeInstance(ctx, acceptTradeInstanceRequest{
			IdempotencyKey:              input.IdempotencyKey,
			ExternalRequestID:           input.ExternalRequestID,
			TradeInstanceID:             input.TradeInstanceID,
			BuyerCapsuleerID:            input.BuyerCapsuleerID,
			QuantityRequested:           quantityRequested,
			BuyerWalletID:               input.BuyerWalletID,
			BuyerDestinationItemStackID: input.BuyerDestinationItemStackID,
		})
		if err != nil {
			return nil, err
		}
		return connect.NewResponse(&marketv1.SubmitTradeGuiInteractionResponse{
			InteractionId:               interactionID,
			Status:                      "accepted",
			SettlementBatchId:           response.SettlementBatchID,
			WalletEscrowId:              response.WalletEscrowID,
			BuyerDestinationItemStackId: response.BuyerDestinationItemStackID,
		}), nil
	case "market_cancel_order", "contract_cancel_item_exchange", "direct_trade_cancel":
		response, err := h.cancelTradeInstance(ctx, cancelTradeInstanceRequest{
			IdempotencyKey:         input.IdempotencyKey,
			ExternalRequestID:      input.ExternalRequestID,
			TradeInstanceID:        input.TradeInstanceID,
			CancelledByCapsuleerID: input.CancelledByCapsuleerID,
		})
		if err != nil {
			return nil, err
		}
		return connect.NewResponse(&marketv1.SubmitTradeGuiInteractionResponse{
			InteractionId:     interactionID,
			Status:            "accepted",
			SettlementBatchId: response.SettlementBatchID,
		}), nil
	default:
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("unsupported trade GUI action %q", action))
	}
}

func readGUIInputInt64(rawPayload []byte, field string) (int64, bool, error) {
	var packet struct {
		Input map[string]json.RawMessage `json:"input"`
	}

	if err := json.Unmarshal(rawPayload, &packet); err != nil {
		return 0, false, fmt.Errorf("decode trade GUI packet for %s: %w", field, err)
	}

	if packet.Input == nil {
		return 0, false, nil
	}

	rawValue, exists := packet.Input[field]
	if !exists {
		return 0, false, nil
	}

	var value int64
	if err := json.Unmarshal(rawValue, &value); err != nil {
		return 0, true, fmt.Errorf("%s must be an integer", field)
	}

	return value, true, nil
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
