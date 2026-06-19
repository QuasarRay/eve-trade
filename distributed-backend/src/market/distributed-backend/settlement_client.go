package distributedbackend

import (
	"context"
	"crypto/tls"
	"net"
	"net/http"
	"time"

	"connectrpc.com/connect"
	tradesettlementv1 "github.com/astral/eve-trade/market/distributed-backend/gen/trade_settlement/v1"
	tradesettlementv1connect "github.com/astral/eve-trade/market/distributed-backend/gen/trade_settlement/v1/tradesettlementv1connect"
	"golang.org/x/net/http2"
)

type SettlementExecutor interface {
	ExecuteSettlementBatch(context.Context, *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error)
}

type ConnectSettlementExecutor struct {
	client  tradesettlementv1connect.TradeSettlementServiceClient
	timeout time.Duration
}

func NewConnectSettlementExecutor(baseURL string, timeout time.Duration) *ConnectSettlementExecutor {
	return &ConnectSettlementExecutor{
		client: tradesettlementv1connect.NewTradeSettlementServiceClient(
			h2cClient(),
			baseURL,
			connect.WithGRPC(),
		),
		timeout: timeout,
	}
}

func (e *ConnectSettlementExecutor) ExecuteSettlementBatch(ctx context.Context, request *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	ctx, cancel := e.callContext(ctx)
	defer cancel()

	response, err := e.client.ExecuteSettlementBatch(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, downstreamUnavailable("trade-settlement", err)
	}
	return response.Msg, nil
}

func (e *ConnectSettlementExecutor) callContext(parent context.Context) (context.Context, context.CancelFunc) {
	if e.timeout <= 0 {
		return context.WithCancel(parent)
	}
	return context.WithTimeout(parent, e.timeout)
}

func h2cClient() *http.Client {
	return &http.Client{
		Transport: &http2.Transport{
			AllowHTTP: true,
			DialTLSContext: func(ctx context.Context, network string, addr string, _ *tls.Config) (net.Conn, error) {
				var dialer net.Dialer
				return dialer.DialContext(ctx, network, addr)
			},
		},
	}
}
