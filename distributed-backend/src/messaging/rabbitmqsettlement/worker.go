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
	Ping(context.Context) error
}

func RunSettlementWorker(ctx context.Context, config Config, executor SettlementExecutor, reportReady func(bool)) error {
	config = config.WithDefaults()

	backoff := time.Second
	for {
		err := runSettlementWorkerSession(ctx, config, executor, reportReady)
		if ctx.Err() != nil {
			return nil
		}
		slog.Warn("settlement worker rabbitmq session ended; reconnecting", "error", err, "backoff", backoff)
		timer := time.NewTimer(backoff)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil
		case <-timer.C:
		}
		if backoff < 30*time.Second {
			backoff *= 2
		}
	}
}

func runSettlementWorkerSession(ctx context.Context, config Config, executor SettlementExecutor, reportReady func(bool)) error {
	connection, err := amqp.DialConfig(config.URL, amqp.Config{
		Heartbeat: 30 * time.Second,
		Locale:    "en_US",
	})
	if err != nil {
		return fmt.Errorf("connect to rabbitmq: %w", err)
	}
	defer func(connection *amqp.Connection) {
		err := connection.Close()
		if err != nil {

		}
	}(connection)

	channel, err := connection.Channel()
	if err != nil {
		return fmt.Errorf("open rabbitmq channel: %w", err)
	}
	defer func(channel *amqp.Channel) {
		err := channel.Close()
		if err != nil {

		}
	}(channel)

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

	// Do not bind the broker consumer lifecycle directly to ctx. The worker loop
	// stops admitting deliveries when ctx is canceled, then workers.Wait drains
	// already-admitted work before the deferred channel close cancels consumption.
	// ConsumeWithContext would send basic.cancel concurrently with those workers'
	// reply publishes and acknowledgements on this same channel.
	deliveries, err := channel.Consume(
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
	var channelMu sync.Mutex
	workerSlots := make(chan struct{}, config.PrefetchCount)
	workerErrs := make(chan error, 1)
	var workers sync.WaitGroup
	defer workers.Wait()

	if err := waitForExecutorReady(ctx, executor, config.RequestTimeout, 500*time.Millisecond); err != nil {
		return err
	}

	slog.Info("settlement worker consuming rabbitmq commands", "queue", config.CommandQueue, "prefetch", config.PrefetchCount)
	if reportReady != nil {
		reportReady(true)
		defer reportReady(false)
	}
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
			select {
			case workerSlots <- struct{}{}:
			case err := <-workerErrs:
				return err
			case <-ctx.Done():
				return nil
			}
			workers.Add(1)
			go func(delivery amqp.Delivery) {
				defer workers.Done()
				defer func() { <-workerSlots }()
				if err := handleDelivery(context.WithoutCancel(ctx), config, executor, channel, confirms, &channelMu, delivery); err != nil {
					select {
					case workerErrs <- err:
					default:
					}
				}
			}(delivery)
		case err := <-workerErrs:
			return err
		}
	}
}

func waitForExecutorReady(ctx context.Context, executor SettlementExecutor, timeout time.Duration, retryInterval time.Duration) error {
	if executor == nil {
		return errors.New("settlement executor is required")
	}
	if timeout <= 0 {
		timeout = DefaultRequestTimeout
	}
	if retryInterval <= 0 {
		retryInterval = time.Second
	}

	readyCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	var lastErr error
	for {
		if err := executor.Ping(readyCtx); err == nil {
			return nil
		} else {
			lastErr = err
		}

		timer := time.NewTimer(retryInterval)
		select {
		case <-readyCtx.Done():
			timer.Stop()
			if lastErr != nil {
				return fmt.Errorf("settlement executor not ready: %w", lastErr)
			}
			return readyCtx.Err()
		case <-timer.C:
		}
	}
}

func handleDelivery(
	ctx context.Context,
	config Config,
	executor SettlementExecutor,
	channel *amqp.Channel,
	confirms publisherConfirmations,
	channelMu *sync.Mutex,
	delivery amqp.Delivery,
) error {
	if delivery.ReplyTo == "" || delivery.CorrelationId == "" {
		slog.Warn("rejecting settlement command without reply target", "message_id", delivery.MessageId)
		return nackDelivery(channelMu, delivery, false)
	}

	var request tradesettlementv1.ExecuteSettlementBatchRequest
	if err := proto.Unmarshal(delivery.Body, &request); err != nil {
		slog.Warn("rejecting malformed settlement command", "message_id", delivery.MessageId, "error", err)
		if publishErr := publishErrorReply(ctx, config, channel, confirms, channelMu, delivery, "INVALID_ARGUMENT", err); publishErr != nil {
			if isPublishReturned(publishErr) {
				return ackUnavailableReplyTarget(channelMu, delivery, publishErr)
			}
			return errors.Join(publishErr, nackDelivery(channelMu, delivery, true))
		}
		// Malformed commands are terminal and are dead-lettered for inspection even
		// when the caller receives a precise INVALID_ARGUMENT reply.
		return nackDelivery(channelMu, delivery, false)
	}

	callCtx, cancel := context.WithTimeout(ctx, config.RequestTimeout)
	defer cancel()

	response, err := executor.ExecuteSettlementBatch(callCtx, &request)
	if err != nil {
		slog.Warn("settlement command failed", "idempotency_key", request.GetIdempotencyKey(), "error", err)
		if publishErr := publishErrorReply(ctx, config, channel, confirms, channelMu, delivery, connectCodeName(err), err); publishErr != nil {
			if isPublishReturned(publishErr) {
				return ackUnavailableReplyTarget(channelMu, delivery, publishErr)
			}
			return errors.Join(publishErr, nackDelivery(channelMu, delivery, true))
		}
		return ackDelivery(channelMu, delivery)
	}

	responseBody, err := proto.Marshal(response)
	if err != nil {
		return errors.Join(fmt.Errorf("marshal settlement response: %w", err), nackDelivery(channelMu, delivery, true))
	}
	replyBody, err := json.Marshal(settlementReply{Success: true, Response: responseBody})
	if err != nil {
		return errors.Join(fmt.Errorf("marshal settlement reply envelope: %w", err), nackDelivery(channelMu, delivery, true))
	}
	if err := publishReply(ctx, config, channel, confirms, channelMu, delivery, replyBody); err != nil {
		if isPublishReturned(err) {
			return ackUnavailableReplyTarget(channelMu, delivery, err)
		}
		return errors.Join(err, nackDelivery(channelMu, delivery, true))
	}

	return ackDelivery(channelMu, delivery)
}

func ackUnavailableReplyTarget(channelMu *sync.Mutex, delivery amqp.Delivery, err error) error {
	slog.Warn(
		"acknowledging settlement command because reply target is unavailable",
		"message_id",
		delivery.MessageId,
		"correlation_id",
		delivery.CorrelationId,
		"reply_to",
		delivery.ReplyTo,
		"error",
		err,
	)
	return ackDelivery(channelMu, delivery)
}

func ackDelivery(channelMu *sync.Mutex, delivery amqp.Delivery) error {
	channelMu.Lock()
	defer channelMu.Unlock()
	return delivery.Ack(false)
}

func nackDelivery(channelMu *sync.Mutex, delivery amqp.Delivery, requeue bool) error {
	channelMu.Lock()
	defer channelMu.Unlock()
	return delivery.Nack(false, requeue)
}

func publishErrorReply(
	ctx context.Context,
	config Config,
	channel *amqp.Channel,
	confirms publisherConfirmations,
	channelMu *sync.Mutex,
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
	return publishReply(ctx, config, channel, confirms, channelMu, delivery, replyBody)
}

func publishReply(
	ctx context.Context,
	config Config,
	channel *amqp.Channel,
	confirms publisherConfirmations,
	channelMu *sync.Mutex,
	delivery amqp.Delivery,
	body []byte,
) error {
	publishCtx, cancel := context.WithTimeout(ctx, config.PublishTimeout)
	defer cancel()

	channelMu.Lock()
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
	channelMu.Unlock()
	if err != nil {
		return fmt.Errorf("publish settlement reply: %w", err)
	}
	return nil
}

func NormalizeTransport(value string) string {
	return strings.ToLower(strings.TrimSpace(value))
}
