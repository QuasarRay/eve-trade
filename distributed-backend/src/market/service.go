package market

import (
	"context"
	"errors"
	"time"

	"connectrpc.com/connect"
	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/gen/go/market/v1"
	settlementv1 "github.com/QuasarRay/eve-trade/distributed-backend/gen/go/settlement/v1"
	tradev1 "github.com/QuasarRay/eve-trade/distributed-backend/gen/go/trade/v1"
	gamemarket "github.com/QuasarRay/eve-trade/game-trade/market"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type Service struct {
	book       *Book
	settlement Settlement
	now        func() time.Time
}

func NewService(book *Book, settlement Settlement) *Service {
	return &Service{book: book, settlement: settlement, now: time.Now}
}

func (s *Service) PlaceBuyOrder(ctx context.Context, req *connect.Request[marketv1.PlaceBuyOrderRequest]) (*connect.Response[marketv1.OrderResponse], error) {
	m := req.Msg
	order, err := gamemarket.PlaceBuy(m.RequestId, m.PlayerId, m.WalletId, m.ItemTypeId, m.StationId, m.RegionId, m.Quantity, m.UnitPriceMinor, m.DurationDays, s.now())
	if err != nil {
		return nil, invalid(err)
	}
	opened, err := s.settlement.OpenOrder(ctx, m.RequestId, toProto(order))
	if err != nil {
		return nil, err
	}
	return connect.NewResponse(&marketv1.OrderResponse{Order: s.book.Save(opened)}), nil
}

func (s *Service) PlaceSellOrder(ctx context.Context, req *connect.Request[marketv1.PlaceSellOrderRequest]) (*connect.Response[marketv1.OrderResponse], error) {
	m := req.Msg
	order, err := gamemarket.PlaceSell(m.RequestId, m.PlayerId, m.WalletId, m.ItemTypeId, m.ItemStackId, m.StationId, m.RegionId, m.Quantity, m.UnitPriceMinor, m.DurationDays, s.now())
	if err != nil {
		return nil, invalid(err)
	}
	opened, err := s.settlement.OpenOrder(ctx, m.RequestId, toProto(order))
	if err != nil {
		return nil, err
	}
	return connect.NewResponse(&marketv1.OrderResponse{Order: s.book.Save(opened)}), nil
}

func (s *Service) GetOrder(_ context.Context, req *connect.Request[marketv1.GetOrderRequest]) (*connect.Response[marketv1.OrderResponse], error) {
	order, err := s.book.Get(req.Msg.OrderId)
	if err != nil {
		return nil, notFound(err)
	}
	return connect.NewResponse(&marketv1.OrderResponse{Order: order}), nil
}

func (s *Service) ListOrders(_ context.Context, req *connect.Request[marketv1.ListOrdersRequest]) (*connect.Response[marketv1.ListOrdersResponse], error) {
	m := req.Msg
	return connect.NewResponse(&marketv1.ListOrdersResponse{Orders: s.book.List(m.RegionId, m.ItemTypeId, m.Side)}), nil
}

func (s *Service) FillOrder(ctx context.Context, req *connect.Request[marketv1.FillOrderRequest]) (*connect.Response[marketv1.FillOrderResponse], error) {
	m := req.Msg
	order, err := s.book.Get(m.OrderId)
	if err != nil {
		return nil, notFound(err)
	}
	if m.Quantity > order.RemainingQuantity {
		return nil, invalid(gamemarket.ErrQuantityTooLarge)
	}
	if order.Side == tradev1.OrderSide_ORDER_SIDE_BUY && m.ItemStackId == "" {
		return nil, invalid(errors.New("seller item stack is required when filling a buy order"))
	}
	settlement, err := s.settlement.SettleFill(ctx, &settlementv1.SettleFillRequest{RequestId: m.RequestId, OrderId: m.OrderId, BuyerId: m.PlayerId, BuyerWalletId: m.WalletId, SellerItemStackId: sellerStack(order, m.ItemStackId), Quantity: m.Quantity, UnitPriceMinor: order.UnitPriceMinor})
	if err != nil {
		return nil, err
	}
	order.RemainingQuantity -= m.Quantity
	if order.RemainingQuantity == 0 {
		order.State = tradev1.TradeState_TRADE_STATE_COMPLETED
	}
	return connect.NewResponse(&marketv1.FillOrderResponse{Order: s.book.Save(order), Settlement: settlement}), nil
}

func (s *Service) CancelOrder(ctx context.Context, req *connect.Request[marketv1.CancelOrderRequest]) (*connect.Response[marketv1.OrderResponse], error) {
	old, err := s.book.Get(req.Msg.OrderId)
	if err != nil {
		return nil, notFound(err)
	}
	if old.OwnerId != req.Msg.PlayerId {
		return nil, connect.NewError(connect.CodePermissionDenied, errors.New("only the order owner can cancel the order"))
	}
	order, err := s.settlement.CancelOrder(ctx, req.Msg.RequestId, req.Msg.OrderId)
	if err != nil {
		return nil, err
	}
	return connect.NewResponse(&marketv1.OrderResponse{Order: s.book.Save(order)}), nil
}

func toProto(o gamemarket.Order) *tradev1.MarketOrder {
	side := tradev1.OrderSide_ORDER_SIDE_BUY
	if o.Side == gamemarket.Sell {
		side = tradev1.OrderSide_ORDER_SIDE_SELL
	}
	return &tradev1.MarketOrder{OrderId: o.ID, Side: side, OwnerId: o.OwnerID, WalletId: o.WalletID, ItemTypeId: o.ItemType, ItemStackId: o.ItemStack, StationId: o.Station, RegionId: o.Region, TotalQuantity: o.Quantity, RemainingQuantity: o.Remaining, UnitPriceMinor: o.UnitPrice, State: tradev1.TradeState_TRADE_STATE_OUTSTANDING, CreatedAt: timestamppb.New(o.CreatedAt), ExpiresAt: timestamppb.New(o.ExpiresAt)}
}

func sellerStack(order *tradev1.MarketOrder, provided string) string {
	if order.Side == tradev1.OrderSide_ORDER_SIDE_SELL {
		return order.ItemStackId
	}
	return provided
}

func invalid(err error) error  { return connect.NewError(connect.CodeInvalidArgument, err) }
func notFound(err error) error { return connect.NewError(connect.CodeNotFound, err) }
