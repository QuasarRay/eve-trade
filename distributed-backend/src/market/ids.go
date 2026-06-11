package market

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"time"

	tradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/trade/v1"
)

// newPrefixedID creates opaque service-generated IDs for settlement commands.
// It uses 128 bits of cryptographic randomness and prefixes the encoded value
// with the domain category so logs remain human-scannable without relying on
// global counters. It exists because AcceptFillOrder creates a transaction and
// settlement record, but the market proto request does not provide those IDs.
func newPrefixedID(prefix string) string {
	var randomBytes [16]byte
	if _, err := rand.Read(randomBytes[:]); err != nil {
		return fmt.Sprintf("%s-%d", prefix, time.Now().UnixNano())
	}

	return prefix + "-" + hex.EncodeToString(randomBytes[:])
}

// newTradeTransactionID adapts a generated opaque string into the protobuf
// TradeTransactionId wrapper. It delegates the randomness to newPrefixedID and
// only performs the transport-shape wrapping here. It exists so service logic
// can ask for a domain ID without repeating protobuf construction everywhere.
func newTradeTransactionID() *tradev1.TradeTransactionId {
	return &tradev1.TradeTransactionId{Value: newPrefixedID("trade-tx")}
}

// newSettlementID adapts a generated opaque string into the protobuf
// SettlementId wrapper. It uses the same generator as transaction IDs but a
// different prefix so operation traces clearly distinguish the settlement row
// from the trade transaction row. It exists because settlement requires a stable
// settlement identifier for idempotent writes.
func newSettlementID() *tradev1.SettlementId {
	return &tradev1.SettlementId{Value: newPrefixedID("settlement")}
}
