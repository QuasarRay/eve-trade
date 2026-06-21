package rabbitmqsettlement

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"connectrpc.com/connect"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	amqp "github.com/rabbitmq/amqp091-go"
	"google.golang.org/protobuf/proto"
)

const (
	requestContentType = "application/x-protobuf"
	replyContentType   = "application/json"
	requestType        = "eve.trade_settlement.v1.ExecuteSettlementBatchRequest"
	replyType          = "eve.trade_settlement.v1.ExecuteSettlementBatchReply"
)

type RPCClient struct {
	config          Config
	connection      *amqp.Connection
	publishChannel  *amqp.Channel
	publishConfirms publisherConfirmations
	replyQueue      string
	replies         <-chan amqp.Delivery

	publishMu sync.Mutex
	pendingMu sync.Mutex
	pending   map[string]chan pendingResult
	done      chan struct{}
	closeOnce sync.Once
}

type pendingResult struct {
	delivery amqp.Delivery
	err      error
}

func NewRPCClient(ctx context.Context, config Config) (*RPCClient, error) {
	config = config.WithDefaults()

	connection, err := amqp.DialConfig(config.URL, amqp.Config{
		Heartbeat: 30 * time.Second,
		Locale:    "en_US",
	})
	if err != nil {
		return nil, fmt.Errorf("connect to rabbitmq: %w", err)
	}

	channel, err := connection.Channel()
	if err != nil {
		_ = connection.Close()
		return nil, fmt.Errorf("open rabbitmq channel: %w", err)
	}
	if err := setupTopology(channel, config); err != nil {
		_ = channel.Close()
		_ = connection.Close()
		return nil, err
	}
	confirms, err := enablePublisherConfirms(channel)
	if err != nil {
		_ = channel.Close()
		_ = connection.Close()
		return nil, err
	}

	replyQueue, err := channel.QueueDeclare(
		"",
		false,
		true,
		true,
		false,
		nil,
	)
	if err != nil {
		_ = channel.Close()
		_ = connection.Close()
		return nil, fmt.Errorf("declare rabbitmq settlement reply queue: %w", err)
	}
	replies, err := channel.ConsumeWithContext(
		ctx,
		replyQueue.Name,
		"",
		true,
		true,
		false,
		false,
		nil,
	)
	if err != nil {
		_ = channel.Close()
		_ = connection.Close()
		return nil, fmt.Errorf("consume rabbitmq settlement replies: %w", err)
	}

	client := &RPCClient{
		config:          config,
		connection:      connection,
		publishChannel:  channel,
		publishConfirms: confirms,
		replyQueue:      replyQueue.Name,
		replies:         replies,
		pending:         make(map[string]chan pendingResult),
		done:            make(chan struct{}),
	}
	go client.consumeReplies()
	return client, nil
}

func (c *RPCClient) ExecuteSettlementBatch(ctx context.Context, request *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	callCtx, cancel := context.WithTimeout(ctx, c.config.RequestTimeout)
	defer cancel()

	body, err := proto.Marshal(request)
	if err != nil {
		return nil, fmt.Errorf("marshal settlement request: %w", err)
	}

	correlationID, err := randomID()
	if err != nil {
		return nil, fmt.Errorf("create rabbitmq correlation id: %w", err)
	}
	result := make(chan pendingResult, 1)
	c.pendingMu.Lock()
	c.pending[correlationID] = result
	c.pendingMu.Unlock()
	defer c.removePending(correlationID)

	publishCtx, publishCancel := context.WithTimeout(callCtx, c.config.PublishTimeout)
	defer publishCancel()

	messageID := request.GetRequestId()
	if messageID == "" {
		messageID = request.GetIdempotencyKey()
	}
	if messageID == "" {
		messageID = correlationID
	}

	c.publishMu.Lock()
	err = publishConfirmed(
		publishCtx,
		c.publishChannel,
		c.publishConfirms,
		c.config.Exchange,
		c.config.RoutingKey,
		true,
		amqp.Publishing{
			ContentType:   requestContentType,
			Type:          requestType,
			DeliveryMode:  amqp.Persistent,
			MessageId:     messageID,
			CorrelationId: correlationID,
			ReplyTo:       c.replyQueue,
			Timestamp:     time.Now().UTC(),
			Expiration:    expirationMilliseconds(c.config.RequestTimeout),
			Headers:       requestHeaders(request),
			Body:          body,
		},
	)
	c.publishMu.Unlock()
	if err != nil {
		return nil, fmt.Errorf("publish settlement request: %w", err)
	}

	select {
	case reply := <-result:
		if reply.err != nil {
			return nil, reply.err
		}
		return decodeReply(reply.delivery.Body)
	case <-callCtx.Done():
		return nil, fmt.Errorf("wait for settlement reply: %w", callCtx.Err())
	}
}

func (c *RPCClient) Close() error {
	var err error
	c.closeOnce.Do(func() {
		close(c.done)
		err = errors.Join(c.publishChannel.Close(), c.connection.Close())
		c.failPending(errors.New("rabbitmq settlement client closed"))
	})
	return err
}

func (c *RPCClient) consumeReplies() {
	connectionClosed := c.connection.NotifyClose(make(chan *amqp.Error, 1))
	for {
		select {
		case delivery, ok := <-c.replies:
			if !ok {
				c.failPending(errors.New("rabbitmq settlement reply consumer closed"))
				return
			}
			c.dispatchReply(delivery)
		case rabbitErr, ok := <-connectionClosed:
			if !ok || rabbitErr == nil {
				c.failPending(errors.New("rabbitmq settlement connection closed"))
				return
			}
			c.failPending(fmt.Errorf("rabbitmq settlement connection closed: %w", rabbitErr))
			return
		case <-c.done:
			return
		}
	}
}

func (c *RPCClient) dispatchReply(delivery amqp.Delivery) {
	if delivery.CorrelationId == "" {
		return
	}

	c.pendingMu.Lock()
	result, ok := c.pending[delivery.CorrelationId]
	if ok {
		delete(c.pending, delivery.CorrelationId)
	}
	c.pendingMu.Unlock()

	if ok {
		result <- pendingResult{delivery: delivery}
	}
}

func (c *RPCClient) removePending(correlationID string) {
	c.pendingMu.Lock()
	delete(c.pending, correlationID)
	c.pendingMu.Unlock()
}

func (c *RPCClient) failPending(err error) {
	c.pendingMu.Lock()
	pending := c.pending
	c.pending = make(map[string]chan pendingResult)
	c.pendingMu.Unlock()

	for _, result := range pending {
		result <- pendingResult{err: err}
	}
}

func decodeReply(body []byte) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	var reply settlementReply
	if err := json.Unmarshal(body, &reply); err != nil {
		return nil, fmt.Errorf("decode settlement reply: %w", err)
	}
	if !reply.Success {
		if reply.Code != "" {
			return nil, connect.NewError(connectCodeFromName(reply.Code), errors.New(reply.Error))
		}
		return nil, errors.New(reply.Error)
	}

	var response tradesettlementv1.ExecuteSettlementBatchResponse
	if err := proto.Unmarshal(reply.Response, &response); err != nil {
		return nil, fmt.Errorf("decode settlement response: %w", err)
	}
	return &response, nil
}

func expirationMilliseconds(duration time.Duration) string {
	milliseconds := duration.Milliseconds()
	if milliseconds <= 0 {
		milliseconds = DefaultRequestTimeout.Milliseconds()
	}
	return fmt.Sprintf("%d", milliseconds)
}

func requestHeaders(request *tradesettlementv1.ExecuteSettlementBatchRequest) amqp.Table {
	headers := amqp.Table{}
	if request.GetIdempotencyKey() != "" {
		headers["idempotency_key"] = request.GetIdempotencyKey()
	}
	if request.GetRequestId() != "" {
		headers["request_id"] = request.GetRequestId()
	}
	if request.GetExternalRequestId() != "" {
		headers["external_request_id"] = request.GetExternalRequestId()
	}
	if request.GetCreatedByService() != "" {
		headers["created_by_service"] = request.GetCreatedByService()
	}
	return headers
}

func randomID() (string, error) {
	var bytes [16]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(bytes[:]), nil
}
