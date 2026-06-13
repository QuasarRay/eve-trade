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
	settlementv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/settlement/v1"
	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/settlement/v1/settlementv1connect"
	"golang.org/x/net/http2"
)

type Settlement interface {
	SendTradeInstanceTransaction(context.Context, *settlementv1.TradeInstanceTransactionRequest) (*settlementv1.TradeInstanceTransactionResponse, error)
}

type SettlementClient struct {
	client settlementv1connect.TradeSettlementServiceClient
}

func NewSettlementClient(url string) SettlementClient {
	return SettlementClient{
		client: settlementv1connect.NewTradeSettlementServiceClient(
			newH2CGRPCClient(),
			url,
			connect.WithGRPC(),
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

func (s SettlementClient) SendTradeInstanceTransaction(ctx context.Context, request *settlementv1.TradeInstanceTransactionRequest) (*settlementv1.TradeInstanceTransactionResponse, error) {
	stream := s.client.StreamTradeInstanceTransactions(ctx)
	if err := stream.Send(request); err != nil {
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

	return response, nil
}
