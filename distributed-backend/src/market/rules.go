package market

import (
	"errors"
	"fmt"
	"time"

	tradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/trade/v1"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const (
	defaultPageSize = uint32(50)
	maxPageSize     = uint32(200)
	maxInt64        = int64(1<<63 - 1)
)

var (
	ErrMissingContext      = errors.New("request context is required")
	ErrMissingRequestID    = errors.New("request context.request_id is required")
	ErrMissingIdempotency  = errors.New("request context.idempotency_key is required for commands")
	ErrInvalidOrderSide    = errors.New("trade order side is invalid")
	ErrInvalidItemKind     = errors.New("trade item kind is invalid")
	ErrInvalidOrderState   = errors.New("trade order is not outstanding")
	ErrInvalidQuantity     = errors.New("quantity must be greater than zero")
	ErrInvalidUnitPrice    = errors.New("unit price must be greater than zero")
	ErrMissingExpiry       = errors.New("expires_at is required")
	ErrExpiryNotInFuture   = errors.New("expires_at must be in the future")
	ErrOrderNotExpired     = errors.New("order has not expired yet")
	ErrQuantityTooLarge    = errors.New("requested fill quantity exceeds remaining order quantity")
	ErrTotalPriceOverflow  = errors.New("total price overflows int64 minor units")
	ErrOnlyOwnerCanCancel  = errors.New("only the order owner can cancel the order")
	ErrMissingBuyerWallet  = errors.New("buyer_wallet_id is required for this fill")
	ErrMissingSellerWallet = errors.New("seller_wallet_id is required for this fill")
	ErrMissingSourceStack  = errors.New("source_item_stack_id is required for stackable fills")
	ErrMissingSourceItem   = errors.New("source_item_instance_id is required for singleton fills")
	ErrMissingOfferedStack = errors.New("sell order is missing its offered_item_stack_id")
	ErrMissingOfferedItem  = errors.New("sell order is missing its offered_item_instance_id")
)

type valueID interface {
	GetValue() string
}

// normalizeContext marks the current service as the creator of downstream
// settlement commands. It mutates the request context in-place only when fields
// are empty, preserving values already supplied by upstream services. It exists
// so settlement/audit logs can distinguish commands created by market from raw
// game-server or gateway requests.
func normalizeContext(context *tradev1.RequestContext) *tradev1.RequestContext {
	if context == nil {
		return nil
	}

	if context.SourceSystem == "" {
		context.SourceSystem = "market"
	}
	if context.CreatedByService == "" {
		context.CreatedByService = "market"
	}

	return context
}

// validateReadContext checks the minimum metadata required for read-style RPCs.
// It requires a context and request_id, but does not require idempotency because
// reads do not create durable state changes. It exists so all market read APIs
// produce traceable requests without pretending that reads are write commands.
func validateReadContext(context *tradev1.RequestContext) error {
	if context == nil {
		return ErrMissingContext
	}
	if context.GetRequestId().GetValue() == "" {
		return ErrMissingRequestID
	}

	return nil
}

// validateCommandContext checks metadata required for durable commands.
// It reuses validateReadContext and then also requires an idempotency key so
// retries can be recognized by settlement. It exists because create, fill,
// cancel, expire, and claim operations can change durable trade state.
func validateCommandContext(context *tradev1.RequestContext) error {
	if err := validateReadContext(context); err != nil {
		return err
	}
	if context.GetIdempotencyKey().GetValue() == "" {
		return ErrMissingIdempotency
	}

	return nil
}

// requireID validates protobuf wrapper IDs without caring about the concrete ID
// type. It calls the generated GetValue method and rejects empty strings. It
// exists so the rules layer can validate CapsuleerId, WalletId, ItemTypeId, and
// other domain IDs with one small helper instead of repeating boilerplate.
func requireID(name string, id valueID) error {
	if id == nil || id.GetValue() == "" {
		return fmt.Errorf("%s is required", name)
	}

	return nil
}

// sameID compares two protobuf wrapper IDs by their serialized value. It treats
// nil IDs as empty values and returns true only when both concrete values match.
// It exists for ownership checks where market must verify the requester is the
// same capsuleer that owns the current durable order.
func sameID(left valueID, right valueID) bool {
	if left == nil || right == nil {
		return false
	}

	return left.GetValue() == right.GetValue()
}

// validatePositiveQuantity ensures an amount of items is meaningful. It checks
// the Quantity wrapper and rejects nil or zero units. It exists because market
// should never ask settlement to create or fill a zero-size order.
func validatePositiveQuantity(quantity *tradev1.Quantity) error {
	if quantity == nil || quantity.GetUnits() == 0 {
		return ErrInvalidQuantity
	}

	return nil
}

// validatePositiveIsk ensures a price can move real ISK value. It checks the
// fixed-scale IskAmount wrapper and rejects nil, zero, or negative minor units.
// It exists because market prices must use deterministic integer money instead
// of floats or invalid negative values.
func validatePositiveIsk(amount *tradev1.IskAmount) error {
	if amount == nil || amount.GetMinorUnits() <= 0 {
		return ErrInvalidUnitPrice
	}

	return nil
}

// validateFutureExpiry checks that a newly created order has a usable expiry.
// It requires a timestamp and ensures it is after the market service clock. It
// exists so market does not open already-expired orders in settlement.
func validateFutureExpiry(expiresAt *timestamppb.Timestamp, now time.Time) error {
	if expiresAt == nil {
		return ErrMissingExpiry
	}
	if !expiresAt.AsTime().After(now) {
		return ErrExpiryNotInFuture
	}

	return nil
}

// validateExpired checks whether an order is eligible for explicit expiration.
// It requires an existing expiry timestamp and ensures the timestamp is not in
// the future relative to the market service clock. It exists because market owns
// the decision that an order should be expired, while settlement owns the durable
// state transition.
func validateExpired(expiresAt *timestamppb.Timestamp, now time.Time) error {
	if expiresAt == nil {
		return ErrMissingExpiry
	}
	if expiresAt.AsTime().After(now) {
		return ErrOrderNotExpired
	}

	return nil
}

// validateTradeItemKind checks that the item path is one settlement understands.
// It accepts only stackable and singleton item kinds and rejects unspecified
// values. It exists because settlement must receive exactly one ownership path:
// stack quantity movement or singleton item movement.
func validateTradeItemKind(itemKind tradev1.TradeItemKind) error {
	switch itemKind {
	case tradev1.TradeItemKind_TRADE_ITEM_KIND_STACKABLE,
		tradev1.TradeItemKind_TRADE_ITEM_KIND_SINGLETON:
		return nil
	default:
		return ErrInvalidItemKind
	}
}

// validateOutstanding checks that the durable order is fillable or closable from
// the normal market path. It compares the current state to the canonical
// outstanding lifecycle state. It exists because market should not ask
// settlement to fill, cancel, or expire an order that is already completed,
// failed, cancelled, or expired.
func validateOutstanding(order *tradev1.TradeOrderView) error {
	if order == nil || order.GetState() != tradev1.TransactionState_outstanding {
		return ErrInvalidOrderState
	}

	return nil
}

// normalizePageSize constrains list calls to a predictable server-side range.
// It assigns a default when the client sends zero and caps oversized requests to
// maxPageSize. It exists to prevent accidental unbounded list calls while still
// keeping pagination simple for the current MVP.
func normalizePageSize(pageSize uint32) uint32 {
	if pageSize == 0 {
		return defaultPageSize
	}
	if pageSize > maxPageSize {
		return maxPageSize
	}

	return pageSize
}

// totalPrice multiplies unit ISK price by item quantity with overflow checking.
// It converts the unsigned quantity only after proving it fits in int64, then
// verifies multiplication will not exceed the fixed-scale money representation.
// It exists because settlement requires total_price_isk and market must not send
// a wrapped or truncated money amount.
func totalPrice(unitPrice *tradev1.IskAmount, quantity *tradev1.Quantity) (*tradev1.IskAmount, error) {
	if err := validatePositiveIsk(unitPrice); err != nil {
		return nil, err
	}
	if err := validatePositiveQuantity(quantity); err != nil {
		return nil, err
	}
	if quantity.GetUnits() > uint64(maxInt64) {
		return nil, ErrTotalPriceOverflow
	}

	units := int64(quantity.GetUnits())
	minorUnits := unitPrice.GetMinorUnits()
	if units != 0 && minorUnits > maxInt64/units {
		return nil, ErrTotalPriceOverflow
	}

	return &tradev1.IskAmount{MinorUnits: minorUnits * units}, nil
}
