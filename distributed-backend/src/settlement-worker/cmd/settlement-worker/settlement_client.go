package main

import (
	"context"
	"crypto/tls"
	"net"
	"net/http"
	"time"

	"connectrpc.com/connect"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	trade_settlementv1connect "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1/trade_settlementv1connect"
	"golang.org/x/net/http2"
)

type ConnectSettlementExecutor struct {
	client  trade_settlementv1connect.TradeSettlementServiceClient
	timeout time.Duration
}

func NewConnectSettlementExecutor(baseURL string, timeout time.Duration, options ...connect.ClientOption) *ConnectSettlementExecutor {
	clientOptions := append([]connect.ClientOption{connect.WithGRPC()}, options...)
	return &ConnectSettlementExecutor{
		client: trade_settlementv1connect.NewTradeSettlementServiceClient(
			h2cClient(),
			baseURL,
			clientOptions...,
		),
		timeout: timeout,
	}
}

func (e *ConnectSettlementExecutor) ExecuteSettlementBatch(ctx context.Context, request *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	ctx, cancel := e.callContext(ctx)
	defer cancel()

	response, err := e.client.ExecuteSettlementBatch(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, err
	}
	return response.Msg, nil
}

func (e *ConnectSettlementExecutor) Ping(ctx context.Context) error {
	ctx, cancel := e.callContext(ctx)
	defer cancel()

	_, err := e.client.ExecuteSettlementBatch(ctx, connect.NewRequest(&tradesettlementv1.ExecuteSettlementBatchRequest{
		IdempotencyKey:    "settlement-worker-readiness",
		ExternalRequestId: "settlement-worker-readiness",
		CreatedByService:  "settlement-worker",
	}))
	if err == nil || connect.CodeOf(err) == connect.CodeInvalidArgument {
		return nil
	}
	return err
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
