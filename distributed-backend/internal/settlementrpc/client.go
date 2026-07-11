package settlementrpc

import (
	"context"
	"fmt"

	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const (
	queueMethod  = "/eve.trade_settlement.v1.TradeSettlementService/QueueSettlementOperation"
	getMethod    = "/eve.trade_settlement.v1.TradeSettlementService/GetSettlementOperation"
	updateMethod = "/eve.trade_settlement.v1.TradeSettlementService/UpdateSettlementOperation"
)

type Client struct {
	conn grpc.ClientConnInterface
}

func New(target string) (*Client, error) {
	conn, err := grpc.NewClient(target, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, fmt.Errorf("create trade settlement gRPC client: %w", err)
	}
	return &Client{conn: conn}, nil
}

func NewWithConn(conn grpc.ClientConnInterface) *Client {
	return &Client{conn: conn}
}

func (c *Client) QueueSettlementOperation(ctx context.Context, request *tradesettlementv1.QueueSettlementOperationRequest) (*tradesettlementv1.QueueSettlementOperationResponse, error) {
	response := new(tradesettlementv1.QueueSettlementOperationResponse)
	if err := c.conn.Invoke(ctx, queueMethod, request, response); err != nil {
		return nil, err
	}
	return response, nil
}

func (c *Client) GetSettlementOperation(ctx context.Context, request *tradesettlementv1.GetSettlementOperationRequest) (*tradesettlementv1.GetSettlementOperationResponse, error) {
	response := new(tradesettlementv1.GetSettlementOperationResponse)
	if err := c.conn.Invoke(ctx, getMethod, request, response); err != nil {
		return nil, err
	}
	return response, nil
}

func (c *Client) UpdateSettlementOperation(ctx context.Context, request *tradesettlementv1.UpdateSettlementOperationRequest) (*tradesettlementv1.UpdateSettlementOperationResponse, error) {
	response := new(tradesettlementv1.UpdateSettlementOperationResponse)
	if err := c.conn.Invoke(ctx, updateMethod, request, response); err != nil {
		return nil, err
	}
	return response, nil
}
