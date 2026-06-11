package market

import (
	"context"
	"errors"
	"time"

	"connectrpc.com/connect"
	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/market/v1"
	settlementv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/settlement/v1"
	tradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/trade/v1"
	"google.golang.org/protobuf/types/known/timestamppb"
)

// Service implements the current market/v1 MarketService proto contract.
// It stores a settlement dependency and a clock, validates market-level intent,
// and delegates durable writes/reads to settlement. It exists so market owns game
// market meaning while settlement remains the only durable trade-state authority.
type Service struct {
	settlement Settlement
	now        func() time.Time
}

// NewService constructs a market service with production defaults.
// It accepts the settlement dependency explicitly and uses time.Now as the clock
// so tests can replace time by assigning Service.now. It exists to keep startup
// wiring outside the actual RPC implementation.
func NewService(settlement Settlement) *Service {
	return &Service{
		settlement: settlement,
		now:        time.Now,
	}
}

// CreateSellOrder validates seller intent and opens a durable sell order.
// It checks command metadata, validates seller/item/location/quantity/price/
// expiry fields, builds settlement TradeOrderTerms with SELL_ORDER side, and
// forwards OpenTradeOrder to settlement. It exists because market should decide
// whether the requested sell order is meaningful before settlement reserves the
// seller's item.
func (s *Service) CreateSellOrder(ctx context.Context, req *connect.Request[marketv1.CreateSellOrderRequest]) (*connect.Response[marketv1.CreateOrderResponse], error) {
	message := req.Msg
	requestContext := normalizeContext(message.GetContext())

	if err := validateCommandContext(requestContext); err != nil {
		return nil, invalid(err)
	}
	if err := validateCreateSellOrder(message, s.now()); err != nil {
		return nil, invalid(err)
	}

	result, err := s.settlement.OpenTradeOrder(ctx, &settlementv1.OpenTradeOrderRequest{
		Context: requestContext,
		Terms: &settlementv1.TradeOrderTerms{
			OrderSide:             tradev1.TradeOrderSide_TRADE_ORDER_SIDE_SELL_ORDER,
			ItemKind:              message.GetItemKind(),
			OwnerCapsuleerId:      message.GetSellerCapsuleerId(),
			OwnerWalletId:         message.GetSellerWalletId(),
			ItemTypeId:            message.GetItemTypeId(),
			OfferedItemStackId:    message.GetOfferedItemStackId(),
			OfferedItemInstanceId: message.GetOfferedItemInstanceId(),
			StationId:             message.GetStationId(),
			RegionId:              message.GetRegionId(),
			TotalQuantity:         message.GetQuantity(),
			UnitPriceIsk:          message.GetUnitPriceIsk(),
			ExpiresAt:             message.GetExpiresAt(),
		},
	})
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.CreateOrderResponse{
		TradeOrder:       result.GetTradeOrder(),
		IdempotentReplay: result.GetIdempotentReplay(),
		Failure:          result.GetFailure(),
	}), nil
}

// CreateBuyOrder validates buyer intent and opens a durable buy order.
// It checks command metadata, validates buyer/item/location/quantity/price/
// expiry fields, builds settlement TradeOrderTerms with BUY_ORDER side, and
// forwards OpenTradeOrder to settlement. It exists because market should decide
// whether the requested buy order is meaningful before settlement reserves the
// buyer's ISK.
func (s *Service) CreateBuyOrder(ctx context.Context, req *connect.Request[marketv1.CreateBuyOrderRequest]) (*connect.Response[marketv1.CreateOrderResponse], error) {
	message := req.Msg
	requestContext := normalizeContext(message.GetContext())

	if err := validateCommandContext(requestContext); err != nil {
		return nil, invalid(err)
	}
	if err := validateCreateBuyOrder(message, s.now()); err != nil {
		return nil, invalid(err)
	}

	result, err := s.settlement.OpenTradeOrder(ctx, &settlementv1.OpenTradeOrderRequest{
		Context: requestContext,
		Terms: &settlementv1.TradeOrderTerms{
			OrderSide:        tradev1.TradeOrderSide_TRADE_ORDER_SIDE_BUY_ORDER,
			ItemKind:         message.GetItemKind(),
			OwnerCapsuleerId: message.GetBuyerCapsuleerId(),
			OwnerWalletId:    message.GetBuyerWalletId(),
			ItemTypeId:       message.GetItemTypeId(),
			StationId:        message.GetStationId(),
			RegionId:         message.GetRegionId(),
			TotalQuantity:    message.GetQuantity(),
			UnitPriceIsk:     message.GetUnitPriceIsk(),
			ExpiresAt:        message.GetExpiresAt(),
		},
	})
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.CreateOrderResponse{
		TradeOrder:       result.GetTradeOrder(),
		IdempotentReplay: result.GetIdempotentReplay(),
		Failure:          result.GetFailure(),
	}), nil
}

// GetOrder returns the current durable order view.
// It validates read metadata and order ID, then forwards GetTradeOrder to
// settlement. It exists so market does not serve stale in-memory order state.
func (s *Service) GetOrder(ctx context.Context, req *connect.Request[marketv1.GetOrderRequest]) (*connect.Response[marketv1.GetOrderResponse], error) {
	message := req.Msg
	requestContext := normalizeContext(message.GetContext())

	if err := validateReadContext(requestContext); err != nil {
		return nil, invalid(err)
	}
	if err := requireID("trade_order_id", message.GetTradeOrderId()); err != nil {
		return nil, invalid(err)
	}

	result, err := s.settlement.GetTradeOrder(ctx, &settlementv1.GetTradeOrderRequest{
		Context:      requestContext,
		TradeOrderId: message.GetTradeOrderId(),
	})
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.GetOrderResponse{
		TradeOrder: result.GetTradeOrder(),
	}), nil
}

// ListOutstandingOrders returns outstanding orders from settlement.
// It validates read metadata, normalizes page_size, copies the market filter
// fields into settlement's equivalent list request, and returns settlement's
// durable projection. It exists so order discovery follows the same source of
// truth as order creation and settlement.
func (s *Service) ListOutstandingOrders(ctx context.Context, req *connect.Request[marketv1.ListOutstandingOrdersRequest]) (*connect.Response[marketv1.ListOutstandingOrdersResponse], error) {
	message := req.Msg
	requestContext := normalizeContext(message.GetContext())

	if err := validateReadContext(requestContext); err != nil {
		return nil, invalid(err)
	}

	result, err := s.settlement.ListOutstandingTradeOrders(ctx, &settlementv1.ListOutstandingTradeOrdersRequest{
		Context:    requestContext,
		RegionId:   message.GetRegionId(),
		StationId:  message.GetStationId(),
		ItemTypeId: message.GetItemTypeId(),
		OrderSide:  message.GetOrderSide(),
		PageSize:   normalizePageSize(message.GetPageSize()),
		PageToken:  message.GetPageToken(),
	})
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.ListOutstandingOrdersResponse{
		TradeOrders:   result.GetTradeOrders(),
		NextPageToken: result.GetNextPageToken(),
	}), nil
}

// AcceptFillOrder accepts a fill against the current durable order state.
// It validates command metadata, loads the order from settlement, checks the
// order is outstanding and the requested quantity fits, derives buyer/seller
// roles from order side, builds SettlementRequest, and forwards RequestSettlement.
// It exists so market owns fill intent and role interpretation while settlement
// owns the atomic ISK/item movement.
func (s *Service) AcceptFillOrder(ctx context.Context, req *connect.Request[marketv1.AcceptFillOrderRequest]) (*connect.Response[marketv1.AcceptFillOrderResponse], error) {
	message := req.Msg
	requestContext := normalizeContext(message.GetContext())

	if err := validateCommandContext(requestContext); err != nil {
		return nil, invalid(err)
	}
	if err := validateAcceptFillOrderRequest(message); err != nil {
		return nil, invalid(err)
	}

	orderResult, err := s.settlement.GetTradeOrder(ctx, &settlementv1.GetTradeOrderRequest{
		Context:      requestContext,
		TradeOrderId: message.GetTradeOrderId(),
	})
	if err != nil {
		return nil, err
	}

	order := orderResult.GetTradeOrder()
	if err := validateFillAgainstOrder(message, order); err != nil {
		return nil, invalid(err)
	}

	settlementRequest, err := s.buildSettlementRequest(requestContext, message, order)
	if err != nil {
		return nil, invalid(err)
	}

	settlementResult, err := s.settlement.RequestSettlement(ctx, settlementRequest)
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.AcceptFillOrderResponse{
		TradeOrder:       settlementResult.GetTradeOrder(),
		TradeTransaction: settlementResult.GetTradeTransaction(),
		Settlement:       settlementResult.GetSettlement(),
		IdempotentReplay: settlementResult.GetIdempotentReplay(),
		Failure:          settlementResult.GetFailure(),
	}), nil
}

// CancelOrder validates owner cancellation intent and closes the order.
// It loads the durable order, checks that the requester is the owner, then asks
// settlement to transition the order to cancelled from outstanding. It exists so
// market enforces the game rule that only the order owner may cancel, while
// settlement performs the durable transition and reservation release.
func (s *Service) CancelOrder(ctx context.Context, req *connect.Request[marketv1.CancelOrderRequest]) (*connect.Response[marketv1.CancelOrderResponse], error) {
	message := req.Msg
	requestContext := normalizeContext(message.GetContext())

	if err := validateCommandContext(requestContext); err != nil {
		return nil, invalid(err)
	}
	if err := requireID("trade_order_id", message.GetTradeOrderId()); err != nil {
		return nil, invalid(err)
	}
	if err := requireID("requesting_capsuleer_id", message.GetRequestingCapsuleerId()); err != nil {
		return nil, invalid(err)
	}

	orderResult, err := s.settlement.GetTradeOrder(ctx, &settlementv1.GetTradeOrderRequest{
		Context:      requestContext,
		TradeOrderId: message.GetTradeOrderId(),
	})
	if err != nil {
		return nil, err
	}

	order := orderResult.GetTradeOrder()
	if err := validateOutstanding(order); err != nil {
		return nil, invalid(err)
	}
	if !sameID(order.GetOwnerCapsuleerId(), message.GetRequestingCapsuleerId()) {
		return nil, permissionDenied(ErrOnlyOwnerCanCancel)
	}

	closeResult, err := s.settlement.CloseTradeOrder(ctx, &settlementv1.CloseTradeOrderRequest{
		Context:              requestContext,
		TradeOrderId:         message.GetTradeOrderId(),
		RequestedChange:      tradev1.TradeStateChange_SET_TO_CANCELLED,
		ExpectedCurrentState: tradev1.TransactionState_outstanding,
		Reason:               message.GetReason(),
	})
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.CancelOrderResponse{
		TradeOrder:       closeResult.GetTradeOrder(),
		IdempotentReplay: closeResult.GetIdempotentReplay(),
		Failure:          closeResult.GetFailure(),
	}), nil
}

// ExpireOrder validates that the durable order has reached its expiry time.
// It loads the order, checks it is outstanding and expires_at is not in the
// future, then asks settlement to transition it to expired. It exists because
// market owns the policy decision that time has run out, while settlement owns
// the durable state change and reservation release.
func (s *Service) ExpireOrder(ctx context.Context, req *connect.Request[marketv1.ExpireOrderRequest]) (*connect.Response[marketv1.ExpireOrderResponse], error) {
	message := req.Msg
	requestContext := normalizeContext(message.GetContext())

	if err := validateCommandContext(requestContext); err != nil {
		return nil, invalid(err)
	}
	if err := requireID("trade_order_id", message.GetTradeOrderId()); err != nil {
		return nil, invalid(err)
	}

	orderResult, err := s.settlement.GetTradeOrder(ctx, &settlementv1.GetTradeOrderRequest{
		Context:      requestContext,
		TradeOrderId: message.GetTradeOrderId(),
	})
	if err != nil {
		return nil, err
	}

	order := orderResult.GetTradeOrder()
	if err := validateOutstanding(order); err != nil {
		return nil, invalid(err)
	}
	if err := validateExpired(order.GetExpiresAt(), s.now()); err != nil {
		return nil, invalid(err)
	}

	closeResult, err := s.settlement.CloseTradeOrder(ctx, &settlementv1.CloseTradeOrderRequest{
		Context:              requestContext,
		TradeOrderId:         message.GetTradeOrderId(),
		RequestedChange:      tradev1.TradeStateChange_SET_TO_EXPIRED,
		ExpectedCurrentState: tradev1.TransactionState_outstanding,
		Reason:               "order expiry time reached",
	})
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.ExpireOrderResponse{
		TradeOrder:       closeResult.GetTradeOrder(),
		IdempotentReplay: closeResult.GetIdempotentReplay(),
		Failure:          closeResult.GetFailure(),
	}), nil
}

// GetTransactionState returns settlement-owned transaction state.
// It validates read metadata and transaction ID, then forwards the request to
// settlement's read API. It exists so market can expose transaction progress
// without knowing settlement's table layout.
func (s *Service) GetTransactionState(ctx context.Context, req *connect.Request[marketv1.GetTransactionStateRequest]) (*connect.Response[marketv1.GetTransactionStateResponse], error) {
	message := req.Msg
	requestContext := normalizeContext(message.GetContext())

	if err := validateReadContext(requestContext); err != nil {
		return nil, invalid(err)
	}
	if err := requireID("trade_transaction_id", message.GetTradeTransactionId()); err != nil {
		return nil, invalid(err)
	}

	result, err := s.settlement.GetTransactionState(ctx, &settlementv1.GetTransactionStateRequest{
		Context:            requestContext,
		TradeTransactionId: message.GetTradeTransactionId(),
	})
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.GetTransactionStateResponse{
		TradeTransaction: result.GetTradeTransaction(),
		Settlement:       result.GetSettlement(),
	}), nil
}

// ClaimResult forwards a validated claim command to settlement.
// It checks command metadata, transaction ID, and claiming capsuleer ID, then
// maps the settlement claim response into market's response shape. It exists so
// claim durability and idempotency remain settlement responsibilities.
func (s *Service) ClaimResult(ctx context.Context, req *connect.Request[marketv1.ClaimResultRequest]) (*connect.Response[marketv1.ClaimResultResponse], error) {
	message := req.Msg
	requestContext := normalizeContext(message.GetContext())

	if err := validateCommandContext(requestContext); err != nil {
		return nil, invalid(err)
	}
	if err := requireID("trade_transaction_id", message.GetTradeTransactionId()); err != nil {
		return nil, invalid(err)
	}
	if err := requireID("claiming_capsuleer_id", message.GetClaimingCapsuleerId()); err != nil {
		return nil, invalid(err)
	}

	result, err := s.settlement.ClaimResult(ctx, &settlementv1.ClaimResultRequest{
		Context:             requestContext,
		TradeTransactionId:  message.GetTradeTransactionId(),
		ClaimingCapsuleerId: message.GetClaimingCapsuleerId(),
	})
	if err != nil {
		return nil, err
	}

	return connect.NewResponse(&marketv1.ClaimResultResponse{
		Claim:            result.GetClaim(),
		TradeTransaction: result.GetTradeTransaction(),
		IdempotentReplay: result.GetIdempotentReplay(),
		Failure:          result.GetFailure(),
	}), nil
}

// validateCreateSellOrder validates fields unique to sell-order creation.
// It checks seller identity, wallet, item, location, amount, price, future
// expiry, item kind, and the required offered item path for the selected kind.
// It exists so settlement receives clean sell-order terms instead of repairing
// invalid market intent.
func validateCreateSellOrder(message *marketv1.CreateSellOrderRequest, now time.Time) error {
	if err := requireID("seller_capsuleer_id", message.GetSellerCapsuleerId()); err != nil {
		return err
	}
	if err := requireID("seller_wallet_id", message.GetSellerWalletId()); err != nil {
		return err
	}
	if err := validateCreateOrderCommon(message.GetItemTypeId(), message.GetStationId(), message.GetRegionId(), message.GetQuantity(), message.GetUnitPriceIsk(), message.GetExpiresAt(), now); err != nil {
		return err
	}
	if err := validateTradeItemKind(message.GetItemKind()); err != nil {
		return err
	}

	switch message.GetItemKind() {
	case tradev1.TradeItemKind_TRADE_ITEM_KIND_STACKABLE:
		return requireID("offered_item_stack_id", message.GetOfferedItemStackId())
	case tradev1.TradeItemKind_TRADE_ITEM_KIND_SINGLETON:
		return requireID("offered_item_instance_id", message.GetOfferedItemInstanceId())
	default:
		return ErrInvalidItemKind
	}
}

// validateCreateBuyOrder validates fields unique to buy-order creation.
// It checks buyer identity, wallet, item, location, amount, price, future expiry,
// and item kind. It exists so settlement receives clean buy-order terms and can
// reserve the buyer's ISK without guessing game-market intent.
func validateCreateBuyOrder(message *marketv1.CreateBuyOrderRequest, now time.Time) error {
	if err := requireID("buyer_capsuleer_id", message.GetBuyerCapsuleerId()); err != nil {
		return err
	}
	if err := requireID("buyer_wallet_id", message.GetBuyerWalletId()); err != nil {
		return err
	}
	if err := validateCreateOrderCommon(message.GetItemTypeId(), message.GetStationId(), message.GetRegionId(), message.GetQuantity(), message.GetUnitPriceIsk(), message.GetExpiresAt(), now); err != nil {
		return err
	}

	return validateTradeItemKind(message.GetItemKind())
}

// validateCreateOrderCommon validates creation fields shared by buy and sell
// orders. It checks item/location IDs, quantity, unit price, and future expiry.
// It exists to keep shared market rules identical between CreateBuyOrder and
// CreateSellOrder.
func validateCreateOrderCommon(itemTypeID *tradev1.ItemTypeId, stationID *tradev1.StationId, regionID *tradev1.RegionId, quantity *tradev1.Quantity, unitPrice *tradev1.IskAmount, expiresAt *timestamppb.Timestamp, now time.Time) error {
	if err := requireID("item_type_id", itemTypeID); err != nil {
		return err
	}
	if err := requireID("station_id", stationID); err != nil {
		return err
	}
	if err := requireID("region_id", regionID); err != nil {
		return err
	}
	if err := validatePositiveQuantity(quantity); err != nil {
		return err
	}
	if err := validatePositiveIsk(unitPrice); err != nil {
		return err
	}

	return validateFutureExpiry(expiresAt, now)
}

// validateAcceptFillOrderRequest validates the request fields that do not need
// the current order. It checks command IDs, accepting capsuleer, item kind, and
// quantity. It exists so obvious malformed fill requests fail before market does
// any settlement read.
func validateAcceptFillOrderRequest(message *marketv1.AcceptFillOrderRequest) error {
	if err := requireID("trade_order_id", message.GetTradeOrderId()); err != nil {
		return err
	}
	if err := requireID("accepting_capsuleer_id", message.GetAcceptingCapsuleerId()); err != nil {
		return err
	}
	if err := validateTradeItemKind(message.GetItemKind()); err != nil {
		return err
	}

	return validatePositiveQuantity(message.GetQuantity())
}

// validateFillAgainstOrder validates fill intent against the current durable
// order view. It checks the order is outstanding, quantity does not exceed the
// remaining quantity, order side is known, and required buyer/seller/source item
// fields exist for the selected side and item kind. It exists because market must
// interpret current order meaning before creating a settlement command.
func validateFillAgainstOrder(message *marketv1.AcceptFillOrderRequest, order *tradev1.TradeOrderView) error {
	if err := validateOutstanding(order); err != nil {
		return err
	}
	if message.GetQuantity().GetUnits() > order.GetRemainingQuantity().GetUnits() {
		return ErrQuantityTooLarge
	}

	switch order.GetOrderSide() {
	case tradev1.TradeOrderSide_TRADE_ORDER_SIDE_SELL_ORDER:
		if err := requireID("buyer_wallet_id", message.GetBuyerWalletId()); err != nil {
			return ErrMissingBuyerWallet
		}
	case tradev1.TradeOrderSide_TRADE_ORDER_SIDE_BUY_ORDER:
		if err := requireID("seller_wallet_id", message.GetSellerWalletId()); err != nil {
			return ErrMissingSellerWallet
		}
	default:
		return ErrInvalidOrderSide
	}

	sourceStackID, sourceItemID, err := sourceItemForFill(message, order)
	if err != nil {
		return err
	}
	if message.GetItemKind() == tradev1.TradeItemKind_TRADE_ITEM_KIND_STACKABLE {
		return requireID("source_item_stack_id", sourceStackID)
	}

	return requireID("source_item_instance_id", sourceItemID)
}

// buildSettlementRequest transforms a valid market fill into settlement terms.
// It derives buyer/seller roles from order side, derives the source item path,
// computes total ISK price, generates transaction/settlement IDs, and sets the
// expected order state to outstanding. It exists as the single place where market
// translates fill intent into the correctness-critical settlement command.
func (s *Service) buildSettlementRequest(context *tradev1.RequestContext, message *marketv1.AcceptFillOrderRequest, order *tradev1.TradeOrderView) (*settlementv1.SettlementRequest, error) {
	buyerCapsuleerID, buyerWalletID, sellerCapsuleerID, sellerWalletID, err := partiesForFill(message, order)
	if err != nil {
		return nil, err
	}

	sourceStackID, sourceItemID, err := sourceItemForFill(message, order)
	if err != nil {
		return nil, err
	}

	price, err := totalPrice(order.GetUnitPriceIsk(), message.GetQuantity())
	if err != nil {
		return nil, err
	}

	return &settlementv1.SettlementRequest{
		Context:                   context,
		TradeOrderId:              message.GetTradeOrderId(),
		TradeTransactionId:        newTradeTransactionID(),
		SettlementId:              newSettlementID(),
		ItemKind:                  message.GetItemKind(),
		BuyerCapsuleerId:          buyerCapsuleerID,
		BuyerWalletId:             buyerWalletID,
		SellerCapsuleerId:         sellerCapsuleerID,
		SellerWalletId:            sellerWalletID,
		ItemTypeId:                order.GetItemTypeId(),
		SourceItemStackId:         sourceStackID,
		DestinationItemStackId:    message.GetDestinationItemStackId(),
		SourceItemInstanceId:      sourceItemID,
		DestinationItemInstanceId: message.GetDestinationItemInstanceId(),
		Quantity:                  message.GetQuantity(),
		UnitPriceIsk:              order.GetUnitPriceIsk(),
		TotalPriceIsk:             price,
		ExpectedTradeOrderState:   tradev1.TransactionState_outstanding,
	}, nil
}

// partiesForFill derives buyer and seller identities from order side.
// For sell orders, the order owner is the seller and the accepting capsuleer is
// the buyer; for buy orders, the owner is the buyer and the accepting capsuleer
// is the seller. It exists so settlement receives explicit buyer/seller terms
// without having to reinterpret market-side order direction.
func partiesForFill(message *marketv1.AcceptFillOrderRequest, order *tradev1.TradeOrderView) (*tradev1.CapsuleerId, *tradev1.WalletId, *tradev1.CapsuleerId, *tradev1.WalletId, error) {
	switch order.GetOrderSide() {
	case tradev1.TradeOrderSide_TRADE_ORDER_SIDE_SELL_ORDER:
		return message.GetAcceptingCapsuleerId(), message.GetBuyerWalletId(), order.GetOwnerCapsuleerId(), order.GetOwnerWalletId(), nil
	case tradev1.TradeOrderSide_TRADE_ORDER_SIDE_BUY_ORDER:
		return order.GetOwnerCapsuleerId(), order.GetOwnerWalletId(), message.GetAcceptingCapsuleerId(), message.GetSellerWalletId(), nil
	default:
		return nil, nil, nil, nil, ErrInvalidOrderSide
	}
}

// sourceItemForFill chooses the item source that settlement should move.
// For sell orders, the source item is the item already offered by the order;
// for buy orders, the accepting seller must provide the source item in the fill
// request. It exists to avoid trusting the filler to redefine the item reserved
// by an existing sell order.
func sourceItemForFill(message *marketv1.AcceptFillOrderRequest, order *tradev1.TradeOrderView) (*tradev1.ItemStackId, *tradev1.ItemInstanceId, error) {
	if order.GetOrderSide() == tradev1.TradeOrderSide_TRADE_ORDER_SIDE_SELL_ORDER {
		switch message.GetItemKind() {
		case tradev1.TradeItemKind_TRADE_ITEM_KIND_STACKABLE:
			if order.GetOfferedItemStackId().GetValue() == "" {
				return nil, nil, ErrMissingOfferedStack
			}
			return order.GetOfferedItemStackId(), message.GetSourceItemInstanceId(), nil
		case tradev1.TradeItemKind_TRADE_ITEM_KIND_SINGLETON:
			if order.GetOfferedItemInstanceId().GetValue() == "" {
				return nil, nil, ErrMissingOfferedItem
			}
			return message.GetSourceItemStackId(), order.GetOfferedItemInstanceId(), nil
		default:
			return nil, nil, ErrInvalidItemKind
		}
	}

	switch message.GetItemKind() {
	case tradev1.TradeItemKind_TRADE_ITEM_KIND_STACKABLE:
		if message.GetSourceItemStackId().GetValue() == "" {
			return nil, nil, ErrMissingSourceStack
		}
		return message.GetSourceItemStackId(), message.GetSourceItemInstanceId(), nil
	case tradev1.TradeItemKind_TRADE_ITEM_KIND_SINGLETON:
		if message.GetSourceItemInstanceId().GetValue() == "" {
			return nil, nil, ErrMissingSourceItem
		}
		return message.GetSourceItemStackId(), message.GetSourceItemInstanceId(), nil
	default:
		return nil, nil, ErrInvalidItemKind
	}
}

// invalid converts rule failures into connect invalid-argument errors.
// It wraps the original error with connect.CodeInvalidArgument and returns it as
// a transport error. It exists so clients receive a clear distinction between
// malformed market intent and settlement/internal failures.
func invalid(err error) error {
	return connect.NewError(connect.CodeInvalidArgument, err)
}

// permissionDenied converts authorization failures into connect permission
// errors. It wraps the original error with connect.CodePermissionDenied. It
// exists so ownership violations are not confused with malformed fields or
// settlement failures.
func permissionDenied(err error) error {
	return connect.NewError(connect.CodePermissionDenied, err)
}

// isNotFound reports whether an error is a connect not-found response.
// It unwraps connect errors and compares the code to CodeNotFound. It exists for
// callers that need to preserve settlement not-found semantics without matching
// strings.
func isNotFound(err error) bool {
	var connectErr *connect.Error
	if errors.As(err, &connectErr) {
		return connectErr.Code() == connect.CodeNotFound
	}

	return false
}
