package market

import (
	"context"
	"net/http"

	"connectrpc.com/connect"
	settlementv1 "github.com/QuasarRay/eve-trade/distributed-backend/gen/go/settlement/v1"
	"github.com/QuasarRay/eve-trade/distributed-backend/gen/go/settlement/v1/settlementv1connect"
	tradev1 "github.com/QuasarRay/eve-trade/distributed-backend/gen/go/trade/v1"
)

type Settlement interface {
	OpenOrder(context.Context, string, *tradev1.MarketOrder) (*tradev1.MarketOrder, error)
	CancelOrder(context.Context, string, string) (*tradev1.MarketOrder, error)
	SettleFill(context.Context, *settlementv1.SettleFillRequest) (*tradev1.SettlementResult, error)
}

type SettlementClient struct {
	client settlementv1connect.TradeSettlementServiceClient
}

func NewSettlementClient(url string) SettlementClient {
	return SettlementClient{client: settlementv1connect.NewTradeSettlementServiceClient(http.DefaultClient, url, connect.WithGRPC())}
}

func (s SettlementClient) OpenOrder(ctx context.Context, requestID string, order *tradev1.MarketOrder) (*tradev1.MarketOrder, error) {
	res, err := s.client.OpenMarketOrder(ctx, connect.NewRequest(&settlementv1.OpenMarketOrderRequest{RequestId: requestID, Order: order}))
	if err != nil {
		return nil, err
	}
	return res.Msg.Order, nil
}

func (s SettlementClient) CancelOrder(ctx context.Context, requestID, orderID string) (*tradev1.MarketOrder, error) {
	res, err := s.client.CancelMarketOrder(ctx, connect.NewRequest(&settlementv1.CancelMarketOrderRequest{RequestId: requestID, OrderId: orderID}))
	if err != nil {
		return nil, err
	}
	return res.Msg.Order, nil
}

func (s SettlementClient) SettleFill(ctx context.Context, fill *settlementv1.SettleFillRequest) (*tradev1.SettlementResult, error) {
	res, err := s.client.SettleFill(ctx, connect.NewRequest(fill))
	if err != nil {
		return nil, err
	}
	return res.Msg.Settlement, nil
}
