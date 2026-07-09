package gametrade

import (
	"crypto/sha256"
	"fmt"

	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
)

const (
	CreatedByService = "market"

	TradeKindSell = "SELL"

	TradeStateOpen      = "OPEN"
	TradeStateCancelled = "CANCELLED"
	TradeStateCompleted = "COMPLETED"

	TradeStateChangeIssued    = "ISSUED"
	TradeStateChangeCancelled = "CANCELLED_BY_ISSUER"
	TradeStateChangeAccepted  = "ACCEPTED_BY_BUYER"
)

type ItemStackRow struct {
	ItemStackID string
	OwnerID     int64
	ItemTypeID  int64
	StationID   int64
	Quantity    int64
}

type SettlementPlan struct {
	IdempotencyKey         string
	RequestFingerprint     string
	ExternalRequestID      string
	CausedByCapsuleerID    int64
	Operations             []settlement.Operation
	TradeInstanceID        string
	ItemStackEscrowID      string
	WalletEscrowID         string
	DestinationItemStackID string
}

func SettleTradeInstance(plan SettlementPlan) (*settlement.Work, error) {
	if plan.IdempotencyKey == "" {
		return nil, fmt.Errorf("idempotency key is required")
	}
	if len(plan.Operations) == 0 {
		return nil, fmt.Errorf("settlement plan must contain at least one operation")
	}

	return &settlement.Work{
		IdempotencyKey:      plan.IdempotencyKey,
		ExternalRequestID:   plan.ExternalRequestID,
		CausedByCapsuleerID: plan.CausedByCapsuleerID,
		Operations:          plan.Operations,
		CreatedByService:    CreatedByService,
		RequestID:           "",
	}, nil
}

func deterministicID(idempotencyKey string, purpose string) (string, error) {
	if idempotencyKey == "" {
		return "", fmt.Errorf("idempotency_key is required")
	}
	sum := sha256.Sum256([]byte("eve-trade:" + idempotencyKey + ":" + purpose))
	sum[6] = (sum[6] & 0x0f) | 0x40
	sum[8] = (sum[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", sum[0:4], sum[4:6], sum[6:8], sum[8:10], sum[10:16]), nil
}
