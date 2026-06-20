package rabbitmqsettlement

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	amqp "github.com/rabbitmq/amqp091-go"
	"google.golang.org/protobuf/proto"
)

type SettlementExecutor interface {
	ExecuteSettlementBatch(context.Context, *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error)
}

func RunSettlementWorker(ctx context.Context, config Config, executor SettlementExecutor) error {
	config = config.WithDefaults()

	connection, err := amqp.DialConfig(config.URL, amqp.Config{
		Heartbeat: 30 * time.Second,
		Locale:    "en_US",
	})
	if err != nil {
		return fmt.Errorf("connect to rabbitmq: %w", err)
	}
	defer connection.Close()

	channel, err := connection.Channel()
	if err != nil {
		return fmt.Errorf("open rabbitmq channel: %w", err)
	}
	defer channel.Close()

	if err := setupTopology(channel, config); err != nil {
		return err
	}
	if err := channel.Qos(config.PrefetchCount, 0, false); err != nil {
		return fmt.Errorf("configure settlement worker prefetch: %w", err)
	}
	confirms, err := enablePublisherConfirms(channel)
	if err != nil {
		return err
	}

	deliveries, err := channel.ConsumeWithContext(
		ctx,
		config.CommandQueue,
		"",
		false,
		false,
		false,
		false,
		nil,
	)
	if err != nil {
		return fmt.Errorf("consume settlement command queue: %w", err)
	}

	connectionClosed := connection.NotifyClose(make(chan *amqp.Error, 1))
	var publishMu sync.Mutex

	slog.Info("settlement worker consuming rabbitmq commands", "queue", config.CommandQueue, "prefetch", config.PrefetchCount)
	for {
		select {
		case <-ctx.Done():
			return nil
		case rabbitErr, ok := <-connectionClosed:
			if !ok || rabbitErr == nil {
				return errors.New("rabbitmq settlement worker connection closed")
			}
			return fmt.Errorf("rabbitmq settlement worker connection closed: %w", rabbitErr)
		case delivery, ok := <-deliveries:
			if !ok {
				return errors.New("rabbitmq settlement command consumer closed")
			}
			if err := handleDelivery(ctx, config, executor, channel, confirms, &publishMu, delivery); err != nil {
				return err
			}
		}
	}
}

func handleDelivery(
	ctx context.Context,
	config Config,
	executor SettlementExecutor,
	channel *amqp.Channel,
	confirms <-chan amqp.Confirmation,
	publishMu *sync.Mutex,
	delivery amqp.Delivery,
) error {
	if delivery.ReplyTo == "" || delivery.CorrelationId == "" {
		slog.Warn("rejecting settlement command without reply target", "message_id", delivery.MessageId)
		return delivery.Nack(false, false)
	}

	var request tradesettlementv1.ExecuteSettlementBatchRequest
	if err := proto.Unmarshal(delivery.Body, &request); err != nil {
		slog.Warn("rejecting malformed settlement command", "message_id", delivery.MessageId, "error", err)
		if publishErr := publishErrorReply(ctx, config, channel, confirms, publishMu, delivery, "INVALID_ARGUMENT", err); publishErr != nil {
			return errors.Join(publishErr, delivery.Nack(false, true))
		}
		return delivery.Ack(false)
	}

	callCtx, cancel := context.WithTimeout(ctx, config.RequestTimeout)
	defer cancel()

	response, err := executor.ExecuteSettlementBatch(callCtx, &request)
	if err != nil {
		slog.Warn("settlement command failed", "idempotency_key", request.GetIdempotencyKey(), "error", err)
		if publishErr := publishErrorReply(ctx, config, channel, confirms, publishMu, delivery, "SETTLEMENT_FAILED", err); publishErr != nil {
			return errors.Join(publishErr, delivery.Nack(false, true))
		}
		return delivery.Ack(false)
	}

	responseBody, err := proto.Marshal(response)
	if err != nil {
		return errors.Join(fmt.Errorf("marshal settlement response: %w", err), delivery.Nack(false, true))
	}
	replyBody, err := json.Marshal(settlementReply{Success: true, Response: responseBody})
	if err != nil {
		return errors.Join(fmt.Errorf("marshal settlement reply envelope: %w", err), delivery.Nack(false, true))
	}
	if err := publishReply(ctx, config, channel, confirms, publishMu, delivery, replyBody); err != nil {
		return errors.Join(err, delivery.Nack(false, true))
	}

	return delivery.Ack(false)
}

func publishErrorReply(
	ctx context.Context,
	config Config,
	channel *amqp.Channel,
	confirms <-chan amqp.Confirmation,
	publishMu *sync.Mutex,
	delivery amqp.Delivery,
	code string,
	err error,
) error {
	replyBody, marshalErr := json.Marshal(settlementReply{
		Success: false,
		Code:    code,
		Error:   err.Error(),
	})
	if marshalErr != nil {
		return fmt.Errorf("marshal settlement error reply: %w", marshalErr)
	}
	return publishReply(ctx, config, channel, confirms, publishMu, delivery, replyBody)
}

func publishReply(
	ctx context.Context,
	config Config,
	channel *amqp.Channel,
	confirms <-chan amqp.Confirmation,
	publishMu *sync.Mutex,
	delivery amqp.Delivery,
	body []byte,
) error {
	publishCtx, cancel := context.WithTimeout(ctx, config.PublishTimeout)
	defer cancel()

	publishMu.Lock()
	err := publishConfirmed(
		publishCtx,
		channel,
		confirms,
		"",
		delivery.ReplyTo,
		true,
		amqp.Publishing{
			ContentType:   replyContentType,
			Type:          replyType,
			DeliveryMode:  amqp.Transient,
			MessageId:     delivery.MessageId,
			CorrelationId: delivery.CorrelationId,
			Timestamp:     time.Now().UTC(),
			Body:          body,
		},
	)
	publishMu.Unlock()
	if err != nil {
		return fmt.Errorf("publish settlement reply: %w", err)
	}
	return nil
}

func NormalizeTransport(value string) string {
	return strings.ToLower(strings.TrimSpace(value))
}
