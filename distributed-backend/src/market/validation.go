package market

import (
	"context"
	"errors"
	"fmt"
	"os"
	"time"

	"encore.dev/beta/errs"
	"github.com/QuasarRay/eve-trade/gametrade"
)

type directTestAPIError struct {
	code  errs.ErrCode
	cause error
}

func (e *directTestAPIError) Error() string {
	if e.cause == nil {
		return e.code.String()
	}
	return e.cause.Error()
}

func (e *directTestAPIError) Unwrap() error { return e.cause }

func apiError(code errs.ErrCode, err error) error {
	if os.Getenv("ENCORERUNTIME_NOPANIC") != "" {
		return &directTestAPIError{code: code, cause: err}
	}
	if err == nil {
		return errs.B().Code(code).Err()
	}
	return errs.B().Code(code).Cause(err).Msg(err.Error()).Err()
}

func apiErrorCode(err error) errs.ErrCode {
	var direct *directTestAPIError
	if errors.As(err, &direct) {
		return direct.code
	}
	return errs.Code(err)
}

func (h *MarketHandler) loadAcceptableTrade(ctx context.Context, tradeInstanceID string, requestedQuantity int64) (TradeSnapshot, error) {
	trade, err := h.trades.LoadTrade(ctx, tradeInstanceID)
	if err != nil {
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, err)
	}
	switch trade.TradeState {
	case gametrade.TradeStateOpen:
	case gametrade.TradeStateCancelled:
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, fmt.Errorf("trade is cancelled"))
	case gametrade.TradeStateCompleted:
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, fmt.Errorf("trade is completed"))
	default:
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, fmt.Errorf("trade is not open"))
	}
	if trade.EscrowReleased {
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, fmt.Errorf("item_stack_escrow %s is already released", trade.ItemStackEscrowID))
	}
	if trade.ExpiresAtValid && !trade.ExpiresAt.After(time.Now()) {
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, fmt.Errorf("trade is expired"))
	}
	if requestedQuantity > trade.EscrowQuantity {
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, fmt.Errorf("item_stack_escrow %s has %d, requested %d", trade.ItemStackEscrowID, trade.EscrowQuantity, requestedQuantity))
	}
	return trade, nil
}

func (h *MarketHandler) loadCancellableTrade(ctx context.Context, tradeInstanceID string, cancelledByCapsuleerID int64) (TradeSnapshot, error) {
	trade, err := h.trades.LoadTrade(ctx, tradeInstanceID)
	if err != nil {
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, err)
	}
	if cancelledByCapsuleerID != trade.IssuerID {
		return TradeSnapshot{}, apiError(errs.PermissionDenied, fmt.Errorf("only the trade issuer can cancel this trade"))
	}
	switch trade.TradeState {
	case gametrade.TradeStateOpen:
	case gametrade.TradeStateCancelled:
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, fmt.Errorf("trade is cancelled"))
	case gametrade.TradeStateCompleted:
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, fmt.Errorf("trade is completed"))
	default:
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, fmt.Errorf("trade is not open"))
	}
	if trade.EscrowReleased || trade.EscrowQuantity <= 0 {
		return TradeSnapshot{}, apiError(errs.FailedPrecondition, fmt.Errorf("item_stack_escrow %s is already released", trade.ItemStackEscrowID))
	}
	return trade, nil
}

func checkedISKAmount(quantity int64, unitPriceISK int64) (int64, error) {
	if unitPriceISK < 0 {
		return 0, fmt.Errorf("unit_price_isk must be non-negative")
	}
	const maxInt64 = int64(^uint64(0) >> 1)
	if unitPriceISK != 0 && quantity > maxInt64/unitPriceISK {
		return 0, fmt.Errorf("trade price overflows int64")
	}
	return quantity * unitPriceISK, nil
}
