//go:build legacy_rabbitmq

package rabbitmq

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"sync"
	"time"

	commonv1 "github.com/astral/eve-trade/distributed-backend/proto/gen/eve_trade/common/v1"
	settlementv1 "github.com/astral/eve-trade/distributed-backend/proto/gen/eve_trade/settlement/v1"
	amqp "github.com/rabbitmq/amqp091-go"
	"google.golang.org/protobuf/proto"
)

const (
	DefaultSettlementURL          = "amqp://guest:guest@localhost:5672/"
	DefaultSettlementExchange     = "eve_trade.settlement"
	DefaultSettlementCommandQueue = "eve_trade.settlement.commands"
	DefaultSettlementRoutingKey   = "settlement.command"

	settlementCommandContentType = "application/protobuf"
	settlementCommandType        = "eve_trade.settlement.v1.TradeSettlementCommand"
	settlementResultType         = "eve_trade.settlement.v1.TradeSettlementResult"
)

type SettlementConfig struct {
	URL            string
	Exchange       string
	CommandQueue   string
	RoutingKey     string
	RequestTimeout time.Duration
	PrefetchCount  int
}

type SettlementBackend interface {
	SendTradeSettlementCommand(context.Context, *settlementv1.TradeSettlementCommand) (*settlementv1.TradeSettlementResult, error)
}

type SettlementClient struct {
	conn       *amqp.Connection
	publishCh  *amqp.Channel
	consumeCh  *amqp.Channel
	exchange   string
	routingKey string
	replyQueue string
	timeout    time.Duration

	publishMu sync.Mutex
	pendingMu sync.Mutex
	pending   map[string]chan amqp.Delivery
	done      chan struct{}
	closeOnce sync.Once
}

func NewSettlementClient(config SettlementConfig) (*SettlementClient, error) {
	config = config.withDefaults()

	conn, err := amqp.Dial(config.URL)
	if err != nil {
		return nil, fmt.Errorf("connect to RabbitMQ: %w", err)
	}

	publishCh, err := conn.Channel()
	if err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("open RabbitMQ publish channel: %w", err)
	}
	if err := declareSettlementTopology(publishCh, config); err != nil {
		_ = publishCh.Close()
		_ = conn.Close()
		return nil, err
	}

	consumeCh, err := conn.Channel()
	if err != nil {
		_ = publishCh.Close()
		_ = conn.Close()
		return nil, fmt.Errorf("open RabbitMQ reply channel: %w", err)
	}
	replyQueue, err := consumeCh.QueueDeclare("", false, true, true, false, nil)
	if err != nil {
		_ = consumeCh.Close()
		_ = publishCh.Close()
		_ = conn.Close()
		return nil, fmt.Errorf("declare RabbitMQ settlement reply queue: %w", err)
	}
	replies, err := consumeCh.Consume(replyQueue.Name, "", true, true, false, false, nil)
	if err != nil {
		_ = consumeCh.Close()
		_ = publishCh.Close()
		_ = conn.Close()
		return nil, fmt.Errorf("consume RabbitMQ settlement replies: %w", err)
	}

	client := &SettlementClient{
		conn:       conn,
		publishCh:  publishCh,
		consumeCh:  consumeCh,
		exchange:   config.Exchange,
		routingKey: config.RoutingKey,
		replyQueue: replyQueue.Name,
		timeout:    config.RequestTimeout,
		pending:    make(map[string]chan amqp.Delivery),
		done:       make(chan struct{}),
	}
	go client.dispatchReplies(replies)

	return client, nil
}

func (c *SettlementClient) SendTradeSettlementCommand(ctx context.Context, command *settlementv1.TradeSettlementCommand) (*settlementv1.TradeSettlementResult, error) {
	if command == nil {
		return nil, errors.New("settlement command is required")
	}
	if c.timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, c.timeout)
		defer cancel()
	}

	body, err := proto.Marshal(command)
	if err != nil {
		return nil, fmt.Errorf("marshal settlement command: %w", err)
	}

	correlationID := replyCorrelationID(command)
	reply := make(chan amqp.Delivery, 1)
	c.pendingMu.Lock()
	c.pending[correlationID] = reply
	c.pendingMu.Unlock()
	defer c.removePending(correlationID)

	c.publishMu.Lock()
	err = c.publishCh.PublishWithContext(ctx, c.exchange, c.routingKey, false, false, amqp.Publishing{
		ContentType:   settlementCommandContentType,
		Type:          settlementCommandType,
		DeliveryMode:  amqp.Persistent,
		MessageId:     messageID(command),
		CorrelationId: correlationID,
		ReplyTo:       c.replyQueue,
		Timestamp:     time.Now(),
		Body:          body,
	})
	c.publishMu.Unlock()
	if err != nil {
		return nil, fmt.Errorf("publish settlement command: %w", err)
	}

	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case delivery := <-reply:
		var result settlementv1.TradeSettlementResult
		if err := proto.Unmarshal(delivery.Body, &result); err != nil {
			return nil, fmt.Errorf("unmarshal settlement result: %w", err)
		}
		return &result, nil
	}
}

func (c *SettlementClient) Close() error {
	var err error
	c.closeOnce.Do(func() {
		close(c.done)
		err = errors.Join(
			c.consumeCh.Close(),
			c.publishCh.Close(),
			c.conn.Close(),
		)
	})
	return err
}

func (c *SettlementClient) dispatchReplies(replies <-chan amqp.Delivery) {
	for {
		select {
		case <-c.done:
			return
		case delivery, ok := <-replies:
			if !ok {
				return
			}
			c.pendingMu.Lock()
			reply := c.pending[delivery.CorrelationId]
			c.pendingMu.Unlock()
			if reply == nil {
				continue
			}
			select {
			case reply <- delivery:
			default:
			}
		}
	}
}

func (c *SettlementClient) removePending(correlationID string) {
	c.pendingMu.Lock()
	delete(c.pending, correlationID)
	c.pendingMu.Unlock()
}

func RunSettlementWorker(ctx context.Context, config SettlementConfig, backend SettlementBackend) error {
	if backend == nil {
		return errors.New("settlement backend is required")
	}
	config = config.withDefaults()

	conn, err := amqp.Dial(config.URL)
	if err != nil {
		return fmt.Errorf("connect to RabbitMQ: %w", err)
	}
	defer conn.Close()

	ch, err := conn.Channel()
	if err != nil {
		return fmt.Errorf("open RabbitMQ settlement worker channel: %w", err)
	}
	defer ch.Close()

	if err := declareSettlementTopology(ch, config); err != nil {
		return err
	}
	if err := ch.Qos(config.PrefetchCount, 0, false); err != nil {
		return fmt.Errorf("configure RabbitMQ settlement worker prefetch: %w", err)
	}

	deliveries, err := ch.Consume(config.CommandQueue, "", false, false, false, false, nil)
	if err != nil {
		return fmt.Errorf("consume RabbitMQ settlement commands: %w", err)
	}

	go func() {
		<-ctx.Done()
		_ = conn.Close()
	}()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case delivery, ok := <-deliveries:
			if !ok {
				if ctx.Err() != nil {
					return ctx.Err()
				}
				return errors.New("RabbitMQ settlement command stream closed")
			}
			if err := handleSettlementDelivery(ctx, ch, backend, delivery); err != nil {
				return err
			}
		}
	}
}

func handleSettlementDelivery(ctx context.Context, ch *amqp.Channel, backend SettlementBackend, delivery amqp.Delivery) error {
	var command settlementv1.TradeSettlementCommand
	if err := proto.Unmarshal(delivery.Body, &command); err != nil {
		_ = delivery.Reject(false)
		return nil
	}

	result, err := backend.SendTradeSettlementCommand(ctx, &command)
	if err != nil {
		result = settlementTransportErrorResult(&command, err)
	}
	if result == nil {
		result = settlementTransportErrorResult(&command, errors.New("settlement returned nil result"))
	}

	if delivery.ReplyTo != "" {
		body, err := proto.Marshal(result)
		if err != nil {
			_ = delivery.Nack(false, true)
			return nil
		}
		if err := ch.PublishWithContext(ctx, "", delivery.ReplyTo, false, false, amqp.Publishing{
			ContentType:   settlementCommandContentType,
			Type:          settlementResultType,
			DeliveryMode:  amqp.Transient,
			MessageId:     messageID(&command) + ".result",
			CorrelationId: delivery.CorrelationId,
			Timestamp:     time.Now(),
			Body:          body,
		}); err != nil {
			_ = delivery.Nack(false, true)
			return nil
		}
	}

	return delivery.Ack(false)
}

func declareSettlementTopology(ch *amqp.Channel, config SettlementConfig) error {
	if err := ch.ExchangeDeclare(config.Exchange, amqp.ExchangeDirect, true, false, false, false, nil); err != nil {
		return fmt.Errorf("declare RabbitMQ settlement exchange: %w", err)
	}
	if _, err := ch.QueueDeclare(config.CommandQueue, true, false, false, false, nil); err != nil {
		return fmt.Errorf("declare RabbitMQ settlement command queue: %w", err)
	}
	if err := ch.QueueBind(config.CommandQueue, config.RoutingKey, config.Exchange, false, nil); err != nil {
		return fmt.Errorf("bind RabbitMQ settlement command queue: %w", err)
	}
	return nil
}

func settlementTransportErrorResult(command *settlementv1.TradeSettlementCommand, err error) *settlementv1.TradeSettlementResult {
	return &settlementv1.TradeSettlementResult{
		Metadata:      command.GetMetadata(),
		OperationKind: command.GetOperationKind(),
		AttemptStatus: settlementv1.TransactionAttemptStatus_TRANSACTION_ATTEMPT_STATUS_RESULT_UNKNOWN,
		Result: &settlementv1.TradeSettlementResult_ResultUnknown{
			ResultUnknown: &settlementv1.TradeSettlementResultUnknown{
				Error: &commonv1.ErrorDetail{
					Code:    commonv1.ErrorCode_ERROR_CODE_UNAVAILABLE,
					Message: err.Error(),
				},
			},
		},
	}
}

func (c SettlementConfig) withDefaults() SettlementConfig {
	if c.URL == "" {
		c.URL = DefaultSettlementURL
	}
	if c.Exchange == "" {
		c.Exchange = DefaultSettlementExchange
	}
	if c.CommandQueue == "" {
		c.CommandQueue = DefaultSettlementCommandQueue
	}
	if c.RoutingKey == "" {
		c.RoutingKey = DefaultSettlementRoutingKey
	}
	if c.RequestTimeout == 0 {
		c.RequestTimeout = 30 * time.Second
	}
	if c.PrefetchCount == 0 {
		c.PrefetchCount = 8
	}
	return c
}

func replyCorrelationID(command *settlementv1.TradeSettlementCommand) string {
	if value := command.GetMetadata().GetRequestId().GetValue(); value != "" {
		return value
	}
	if value := command.GetMetadata().GetOperationId().GetValue(); value != "" {
		return value
	}
	if value := command.GetMetadata().GetCorrelationId().GetValue(); value != "" {
		return value
	}
	return randomHex(16)
}

func messageID(command *settlementv1.TradeSettlementCommand) string {
	if value := command.GetMetadata().GetOperationId().GetValue(); value != "" {
		return value
	}
	if value := command.GetMetadata().GetRequestId().GetValue(); value != "" {
		return value
	}
	return replyCorrelationID(command)
}

func randomHex(size int) string {
	bytes := make([]byte, size)
	if _, err := rand.Read(bytes); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(bytes)
}
