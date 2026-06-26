package distributedbackend

import (
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"time"

	"connectrpc.com/connect"
	marketv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1"
	marketv1connect "github.com/QuasarRay/eve-trade/proto/gen/eve/market/v1/marketv1connect"
	"golang.org/x/net/http2"
)

type MarketClient interface {
	SubmitTradeGuiInteraction(context.Context, *marketv1.SubmitTradeGuiInteractionRequest) (*marketv1.SubmitTradeGuiInteractionResponse, error)
}

type ConnectMarketClient struct {
	client     marketv1connect.MarketServiceClient
	httpClient *http.Client
	baseURL    string
	timeout    time.Duration
}

func NewConnectMarketClient(baseURL string, timeout time.Duration, options ...connect.ClientOption) *ConnectMarketClient {
	httpClient := h2cClient()
	clientOptions := append([]connect.ClientOption{connect.WithGRPC()}, options...)
	return &ConnectMarketClient{
		client:     marketv1connect.NewMarketServiceClient(httpClient, baseURL, clientOptions...),
		httpClient: httpClient,
		baseURL:    baseURL,
		timeout:    timeout,
	}
}

func (c *ConnectMarketClient) SubmitTradeGuiInteraction(ctx context.Context, request *marketv1.SubmitTradeGuiInteractionRequest) (*marketv1.SubmitTradeGuiInteractionResponse, error) {
	ctx, cancel := c.callContext(ctx)
	defer cancel()

	response, err := c.client.SubmitTradeGuiInteraction(ctx, connect.NewRequest(request))
	if err != nil {
		return nil, downstreamUnavailable("market", err)
	}
	return response.Msg, nil
}

func (c *ConnectMarketClient) CheckReady(ctx context.Context) error {
	ctx, cancel := c.callContext(ctx)
	defer cancel()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(c.baseURL, "/")+"/readyz", nil)
	if err != nil {
		return fmt.Errorf("build market readiness request: %w", err)
	}
	response, err := c.httpClient.Do(request)
	if err != nil {
		return fmt.Errorf("market readiness request: %w", err)
	}
	defer func(Body io.ReadCloser) {
		err := Body.Close()
		if err != nil {

		}
	}(response.Body)
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("market readiness returned %s", response.Status)
	}
	return nil
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
