package market

import (
	"time"

	"google.golang.org/protobuf/types/known/timestamppb"
)

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
	OperationID       string
	QueuedAt          time.Time
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
	OperationID                 string
	QueuedAt                    time.Time
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
	OperationID       string
	QueuedAt          time.Time
	SettlementBatchID string
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
