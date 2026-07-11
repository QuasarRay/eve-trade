package settlement

import (
	"time"

	"encore.dev/pubsub"
)

const CreatedByMarket = "market"

const (
	IntentIssue  = "ISSUE"
	IntentAccept = "ACCEPT"
	IntentCancel = "CANCEL"
)

const (
	OperationCreateNewTradeInstanceRow                                       = "create_new_trade_instance_row"
	OperationModifyTradeInstanceState                                        = "modify_trade_instance_state"
	OperationCreateNewEmptyItemStack                                         = "create_new_empty_item_stack"
	OperationTransferQuantityFromItemStackToItemStackEscrow                  = "transfer_quantity_from_item_stack_to_item_stack_escrow"
	OperationTransferQuantityFromItemStackEscrowToItemStackWithNewOwner      = "transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner"
	OperationTransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner = "transfer_quantity_from_item_stack_escrow_to_item_stack_with_previous_owner"
	OperationMergeItemStacksWithIdenticalItemTypeAndIdenticalOwner           = "merge_item_stacks_with_identical_item_type_and_identical_owner"
	OperationCreateNewEmptyWalletEscrow                                      = "create_new_empty_wallet_escrow"
	OperationTransferISKAmountFromWalletToWalletEscrow                       = "transfer_isk_amount_from_wallet_to_wallet_escrow"
	OperationTransferISKAmountFromWalletEscrowToWalletWithNewOwner           = "transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner"
	OperationTransferISKAmountFromWalletEscrowToWalletWithPreviousOwner      = "transfer_isk_amount_from_wallet_escrow_to_wallet_with_previous_owner"
)

var WorkTopic = pubsub.NewTopic[*Work]("settlement-work", pubsub.TopicConfig{
	DeliveryGuarantee: pubsub.AtLeastOnce,
	OrderingAttribute: "idempotency-key",
})

var ResultTopic = pubsub.NewTopic[*Result]("settlement-results", pubsub.TopicConfig{
	DeliveryGuarantee: pubsub.AtLeastOnce,
	OrderingAttribute: "idempotency-key",
})

type Work struct {
	OperationID         string      `json:"operation_id"`
	QueuedAt            time.Time   `json:"queued_at"`
	Intent              string      `json:"intent"`
	IdempotencyKey      string      `json:"idempotency_key" pubsub-attr:"idempotency-key"`
	RequestFingerprint  string      `json:"request_fingerprint"`
	ExternalRequestID   string      `json:"external_request_id,omitempty"`
	CausedByCapsuleerID int64       `json:"caused_by_capsuleer_id,omitempty"`
	CreatedByService    string      `json:"created_by_service"`
	RequestID           string      `json:"request_id,omitempty"`
	Operations          []Operation `json:"operations"`
}

type Result struct {
	OperationID        string `json:"operation_id"`
	IdempotencyKey     string `json:"idempotency_key" pubsub-attr:"idempotency-key"`
	RequestID          string `json:"request_id,omitempty"`
	SettlementBatchID  string `json:"settlement_batch_id,omitempty"`
	BatchState         string `json:"batch_state,omitempty"`
	IdempotentReplay   bool   `json:"idempotent_replay"`
	FailureCode        string `json:"failure_code,omitempty"`
	FailureDescription string `json:"failure_description,omitempty"`
}

type Operation struct {
	Kind                                                            string                                                           `json:"kind"`
	CreateNewTradeInstanceRow                                       *CreateNewTradeInstanceRow                                       `json:"create_new_trade_instance_row,omitempty"`
	ModifyTradeInstanceState                                        *ModifyTradeInstanceState                                        `json:"modify_trade_instance_state,omitempty"`
	CreateNewEmptyItemStack                                         *CreateNewEmptyItemStack                                         `json:"create_new_empty_item_stack,omitempty"`
	TransferQuantityFromItemStackToItemStackEscrow                  *TransferQuantityFromItemStackToItemStackEscrow                  `json:"transfer_quantity_from_item_stack_to_item_stack_escrow,omitempty"`
	TransferQuantityFromItemStackEscrowToItemStackWithNewOwner      *TransferQuantityFromItemStackEscrowToItemStackWithNewOwner      `json:"transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner,omitempty"`
	TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner *TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner `json:"transfer_quantity_from_item_stack_escrow_to_item_stack_with_previous_owner,omitempty"`
	MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner           *MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner           `json:"merge_item_stacks_with_identical_item_type_and_identical_owner,omitempty"`
	CreateNewEmptyWalletEscrow                                      *CreateNewEmptyWalletEscrow                                      `json:"create_new_empty_wallet_escrow,omitempty"`
	TransferISKAmountFromWalletToWalletEscrow                       *TransferISKAmountFromWalletToWalletEscrow                       `json:"transfer_isk_amount_from_wallet_to_wallet_escrow,omitempty"`
	TransferISKAmountFromWalletEscrowToWalletWithNewOwner           *TransferISKAmountFromWalletEscrowToWalletWithNewOwner           `json:"transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner,omitempty"`
	TransferISKAmountFromWalletEscrowToWalletWithPreviousOwner      *TransferISKAmountFromWalletEscrowToWalletWithPreviousOwner      `json:"transfer_isk_amount_from_wallet_escrow_to_wallet_with_previous_owner,omitempty"`
}

type CreateNewTradeInstanceRow struct {
	TradeInstanceID string     `json:"trade_instance_id"`
	TradeKind       string     `json:"trade_kind"`
	TradeState      string     `json:"trade_state"`
	IssuerID        int64      `json:"issuer_id"`
	ItemTypeID      int64      `json:"item_type_id"`
	StationID       int64      `json:"station_id"`
	TotalQuantity   int64      `json:"total_quantity"`
	UnitPriceISK    int64      `json:"unit_price_isk"`
	ExpiresAt       *time.Time `json:"expires_at,omitempty"`
}

type ModifyTradeInstanceState struct {
	TradeInstanceID      string `json:"trade_instance_id"`
	ToTradeState         string `json:"to_trade_state"`
	TradeStateChangeKind string `json:"trade_state_change_kind"`
	ChangedByService     string `json:"changed_by_service"`
}

type CreateNewEmptyItemStack struct {
	ItemStackID string `json:"item_stack_id"`
	OwnerID     int64  `json:"owner_id"`
	ItemTypeID  int64  `json:"item_type_id"`
	StationID   int64  `json:"station_id"`
}

type TransferQuantityFromItemStackToItemStackEscrow struct {
	SourceItemStackID string `json:"source_item_stack_id"`
	ItemStackEscrowID string `json:"item_stack_escrow_id"`
	TradeInstanceID   string `json:"trade_instance_id"`
	Quantity          int64  `json:"quantity"`
}

type TransferQuantityFromItemStackEscrowToItemStackWithNewOwner struct {
	ItemStackEscrowID      string `json:"item_stack_escrow_id"`
	DestinationItemStackID string `json:"destination_item_stack_id"`
	Quantity               int64  `json:"quantity"`
}

type TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner struct {
	ItemStackEscrowID      string `json:"item_stack_escrow_id"`
	DestinationItemStackID string `json:"destination_item_stack_id"`
	Quantity               int64  `json:"quantity"`
}

type MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner struct {
	SourceItemStackID      string `json:"source_item_stack_id"`
	DestinationItemStackID string `json:"destination_item_stack_id"`
}

type CreateNewEmptyWalletEscrow struct {
	WalletEscrowID  string `json:"wallet_escrow_id"`
	TradeInstanceID string `json:"trade_instance_id"`
	OwnerID         int64  `json:"owner_id"`
	SourceWalletID  string `json:"source_wallet_id"`
}

type TransferISKAmountFromWalletToWalletEscrow struct {
	SourceWalletID  string `json:"source_wallet_id"`
	WalletEscrowID  string `json:"wallet_escrow_id"`
	TradeInstanceID string `json:"trade_instance_id"`
	ISKAmount       int64  `json:"isk_amount"`
}

type TransferISKAmountFromWalletEscrowToWalletWithNewOwner struct {
	WalletEscrowID      string `json:"wallet_escrow_id"`
	DestinationWalletID string `json:"destination_wallet_id"`
	ISKAmount           int64  `json:"isk_amount"`
}

type TransferISKAmountFromWalletEscrowToWalletWithPreviousOwner struct {
	WalletEscrowID      string `json:"wallet_escrow_id"`
	DestinationWalletID string `json:"destination_wallet_id"`
	ISKAmount           int64  `json:"isk_amount"`
}
