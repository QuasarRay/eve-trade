package market

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"net/http"
	"time"

	"connectrpc.com/connect"
	settlementv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/settlement/v1"
	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/settlement/v1/settlementv1connect"
	"golang.org/x/net/http2"
)

type Settlement interface {
	SendTradeSettlementCommand(context.Context, *settlementv1.TradeSettlementCommand) (*settlementv1.TradeSettlementResult, error)
}

type SettlementClient struct {
	client settlementv1connect.TradeSettlementServiceClient
}

func NewSettlementClient(url string, opts ...connect.ClientOption) SettlementClient {
	options := append([]connect.ClientOption{connect.WithGRPC()}, opts...)

	return SettlementClient{
		client: settlementv1connect.NewTradeSettlementServiceClient(
			newH2CGRPCClient(),
			url,
			options...,
		),
	}
}

func newH2CGRPCClient() *http.Client {
	dialer := &net.Dialer{
		Timeout:   10 * time.Second,
		KeepAlive: 30 * time.Second,
	}

	return &http.Client{
		Transport: &http2.Transport{
			AllowHTTP: true,
			DialTLSContext: func(ctx context.Context, network string, addr string, _ *tls.Config) (net.Conn, error) {
				return dialer.DialContext(ctx, network, addr)
			},
		},
	}
}

func (s SettlementClient) SendTradeSettlementCommand(ctx context.Context, command *settlementv1.TradeSettlementCommand) (*settlementv1.TradeSettlementResult, error) {
	stream := s.client.StreamTradeSettlementCommands(ctx)
	if err := stream.Send(&settlementv1.StreamTradeSettlementCommandsRequest{Command: command}); err != nil {
		return nil, err
	}
	if err := stream.CloseRequest(); err != nil {
		return nil, err
	}

	response, err := stream.Receive()
	if err != nil {
		return nil, err
	}
	if err := stream.CloseResponse(); err != nil && !errors.Is(err, io.EOF) {
		return nil, err
	}

	return response.GetResult(), nil
}
