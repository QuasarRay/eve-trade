package settlementrpc

import (
	"context"
	"fmt"
	"time"

	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/timestamppb"
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

func Timestamp(value time.Time) *timestamppb.Timestamp {
	return timestamppb.New(value)
}

func Time(value *timestamppb.Timestamp) time.Time {
	if value == nil {
		return time.Time{}
	}
	return value.AsTime().UTC()
}

type ErrorClass uint8

const (
	ErrorUnknown ErrorClass = iota
	ErrorInvalidArgument
	ErrorNotFound
	ErrorDeadlineExceeded
	ErrorUnavailable
	ErrorPermissionDenied
	ErrorInternal
)

func ErrorCodeString(err error) string {
	return status.Code(err).String()
}

func ClassifyError(err error) ErrorClass {
	switch status.Code(err) {
	case codes.InvalidArgument:
		return ErrorInvalidArgument
	case codes.NotFound:
		return ErrorNotFound
	case codes.DeadlineExceeded:
		return ErrorDeadlineExceeded
	case codes.Unavailable:
		return ErrorUnavailable
	case codes.PermissionDenied:
		return ErrorPermissionDenied
	case codes.Internal:
		return ErrorInternal
	default:
		return ErrorUnknown
	}
}

func NewError(class ErrorClass, message string) error {
	return status.Error(grpcCode(class), message)
}

func ErrorClassName(class ErrorClass) string {
	return grpcCode(class).String()
}

func grpcCode(class ErrorClass) codes.Code {
	switch class {
	case ErrorInvalidArgument:
		return codes.InvalidArgument
	case ErrorNotFound:
		return codes.NotFound
	case ErrorDeadlineExceeded:
		return codes.DeadlineExceeded
	case ErrorUnavailable:
		return codes.Unavailable
	case ErrorPermissionDenied:
		return codes.PermissionDenied
	case ErrorInternal:
		return codes.Internal
	default:
		return codes.Unknown
	}
}

func IsPermanentError(err error) bool {
	switch status.Code(err) {
	case codes.InvalidArgument, codes.PermissionDenied, codes.FailedPrecondition, codes.NotFound:
		return true
	default:
		return false
	}
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
