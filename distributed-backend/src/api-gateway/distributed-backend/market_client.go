package distributedbackend

import (
	"context"
	"crypto/tls"
	"net"
	"net/http"
	"time"

	"connectrpc.com/connect"
	marketv1 "github.com/astral/eve-trade/market/distributed-backend/gen/market/v1"
	marketv1connect "github.com/astral/eve-trade/market/distributed-backend/gen/market/v1/marketv1connect"
	"golang.org/x/net/http2"
)

type MarketClient interface {
	IssueTradeInstance(context.Context, *marketv1.IssueTradeInstanceRequest) (*marketv1.IssueTradeInstanceResponse, error)
	AcceptTradeInstance(context.Context, *marketv1.AcceptTradeInstanceRequest) (*marketv1.AcceptTradeInstanceResponse, error)
	CancelTradeInstance(context.Context, *marketv1.CancelTradeInstanceRequest) (*marketv1.CancelTradeInstanceResponse, error)
}

type ConnectMarketClient struct {
	client  marketv1connect.MarketServiceClient
	timeout time.Duration
}

func NewConnectMarketClient(baseURL string, timeout time.Duration) *ConnectMarketClient {
	return &ConnectMarketClient{
		client:  marketv1connect.NewMarketServiceClient(h2cClient(), baseURL, connect.WithGRPC()),
		timeout: timeout,
	}
}

func (c *ConnectMarketClient) IssueTradeInstance(ctx context.Context, request *marketv1.IssueTradeInstanceRequest) (*marketv1.IssueTradeInstanceResponse, error) {
	ctx, cancel := c.callContext(ctx)
	defer cancel()

	response, err := c.client.IssueTradeInstance(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, downstreamUnavailable("market", err)
	}
	return response.Msg, nil
}

func (c *ConnectMarketClient) AcceptTradeInstance(ctx context.Context, request *marketv1.AcceptTradeInstanceRequest) (*marketv1.AcceptTradeInstanceResponse, error) {
	ctx, cancel := c.callContext(ctx)
	defer cancel()

	response, err := c.client.AcceptTradeInstance(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, downstreamUnavailable("market", err)
	}
	return response.Msg, nil
}

func (c *ConnectMarketClient) CancelTradeInstance(ctx context.Context, request *marketv1.CancelTradeInstanceRequest) (*marketv1.CancelTradeInstanceResponse, error) {
	ctx, cancel := c.callContext(ctx)
	defer cancel()

	response, err := c.client.CancelTradeInstance(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, downstreamUnavailable("market", err)
	}
	return response.Msg, nil
}

func (c *ConnectMarketClient) callContext(parent context.Context) (context.Context, context.CancelFunc) {
	if c.timeout <= 0 {
		return context.WithCancel(parent)
	}
	return context.WithTimeout(parent, c.timeout)
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
