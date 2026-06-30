package rabbitmqsettlement

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"connectrpc.com/connect"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	amqp "github.com/rabbitmq/amqp091-go"
	"google.golang.org/protobuf/proto"
)

type integrationExecutor struct {
	calls        atomic.Int32
	drainStarted chan struct{}
	drainRelease chan struct{}
}

func (e *integrationExecutor) Ping(context.Context) error { return nil }

func (e *integrationExecutor) ExecuteSettlementBatch(_ context.Context, request *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	e.calls.Add(1)
	if request.GetRequestId() == "shutdown-drain" {
		select {
		case e.drainStarted <- struct{}{}:
		default:
		}
		<-e.drainRelease
	}
	if request.GetRequestId() == "reject" {
		return nil, connect.NewError(connect.CodeFailedPrecondition, errors.New("deliberate rejection"))
	}
	return &tradesettlementv1.ExecuteSettlementBatchResponse{
		SettlementBatchId: "batch-" + request.GetRequestId(),
		BatchState:        "COMPLETED",
	}, nil
}

func TestRabbitMQWorkerAndClientReliabilityContract(t *testing.T) {
	url := os.Getenv("RABBITMQ_TEST_URL")
	if url == "" {
		if os.Getenv("EVE_TRADE_REQUIRE_LIVE_TESTS") == "true" {
			t.Fatal("RABBITMQ_TEST_URL is required by the live-test gate")
		}
		t.Skip("RABBITMQ_TEST_URL is required for live broker reliability tests")
	}
	waitForRabbitMQTestBroker(t, url)
	suffix, err := randomID()
	if err != nil {
		t.Fatal(err)
	}
	config := Config{
		URL:                  url,
		Exchange:             "test.settlement." + suffix,
		CommandQueue:         "test.settlement.commands." + suffix,
		RoutingKey:           "execute." + suffix,
		DeadLetterExchange:   "test.settlement.dlx." + suffix,
		DeadLetterQueue:      "test.settlement.dead." + suffix,
		DeadLetterRoutingKey: "dead." + suffix,
		RequestTimeout:       3 * time.Second,
		PublishTimeout:       2 * time.Second,
		PrefetchCount:        2,
	}

	workerCtx, cancelWorker := context.WithCancel(context.Background())
	clientCtx, cancelClient := context.WithCancel(context.Background())
	defer cancelClient()
	executor := &integrationExecutor{drainStarted: make(chan struct{}, 1), drainRelease: make(chan struct{})}
	ready := make(chan bool, 2)
	workerDone := make(chan error, 1)
	go func() {
		workerDone <- RunSettlementWorker(workerCtx, config, executor, func(value bool) { ready <- value })
	}()
	select {
	case value := <-ready:
		if !value {
			t.Fatal("worker reported not ready during startup")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("worker did not become ready")
	}

	auditConnection, auditChannel, auditDeliveries := observeBrokerCommands(t, config)
	defer auditConnection.Close()
	defer auditChannel.Close()

	client, err := NewRPCClient(clientCtx, config)
	if err != nil {
		t.Fatalf("create RPC client: %v", err)
	}
	defer client.Close()

	response, err := client.ExecuteSettlementBatch(clientCtx, &tradesettlementv1.ExecuteSettlementBatchRequest{
		RequestId: "success", IdempotencyKey: "success-key",
	})
	if err != nil {
		t.Fatalf("successful brokered call failed: %v", err)
	}
	if response.GetSettlementBatchId() != "batch-success" || response.GetBatchState() != "COMPLETED" {
		t.Fatalf("unexpected brokered response: %v", response)
	}
	assertCommandMetadata(t, auditDeliveries, "success", "success-key")

	_, err = client.ExecuteSettlementBatch(clientCtx, &tradesettlementv1.ExecuteSettlementBatchRequest{
		RequestId: "reject", IdempotencyKey: "reject-key",
	})
	if connect.CodeOf(err) != connect.CodeFailedPrecondition || err.Error() == "" {
		t.Fatalf("brokered business rejection = %v", err)
	}
	if executor.calls.Load() != 2 {
		t.Fatalf("executor calls = %d, want 2", executor.calls.Load())
	}

	assertMalformedCommandGetsErrorReplyAndIsDeadLettered(t, clientCtx, config)
	assertUnavailableReplyTargetIsAcknowledged(t, clientCtx, config, executor)

	drainResult := make(chan error, 1)
	go func() {
		response, err := client.ExecuteSettlementBatch(clientCtx, &tradesettlementv1.ExecuteSettlementBatchRequest{
			RequestId: "shutdown-drain", IdempotencyKey: "shutdown-drain-key",
		})
		if err == nil && (response.GetSettlementBatchId() != "batch-shutdown-drain" || response.GetBatchState() != "COMPLETED") {
			err = fmt.Errorf("unexpected drained response: %v", response)
		}
		drainResult <- err
	}()
	select {
	case <-executor.drainStarted:
	case <-time.After(5 * time.Second):
		t.Fatal("queued shutdown request did not reach executor")
	}
	cancelWorker()
	close(executor.drainRelease)
	if err := <-drainResult; err != nil {
		t.Fatalf("admitted work did not finish during graceful shutdown: %v", err)
	}
	select {
	case err := <-workerDone:
		if err != nil {
			t.Fatalf("worker shutdown returned error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("worker did not drain and stop after cancellation")
	}
}

func observeBrokerCommands(t *testing.T, config Config) (*amqp.Connection, *amqp.Channel, <-chan amqp.Delivery) {
	t.Helper()
	connection, err := amqp.Dial(config.URL)
	if err != nil {
		t.Fatal(err)
	}
	channel, err := connection.Channel()
	if err != nil {
		connection.Close()
		t.Fatal(err)
	}
	queue, err := channel.QueueDeclare("", false, true, true, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := channel.QueueBind(queue.Name, config.RoutingKey, config.Exchange, false, nil); err != nil {
		t.Fatal(err)
	}
	deliveries, err := channel.Consume(queue.Name, "", false, true, false, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	return connection, channel, deliveries
}

func assertCommandMetadata(t *testing.T, deliveries <-chan amqp.Delivery, requestID string, idempotencyKey string) {
	t.Helper()
	select {
	case delivery := <-deliveries:
		if delivery.MessageId != requestID || delivery.CorrelationId == "" || delivery.ReplyTo == "" {
			t.Fatalf("command identifiers were not propagated: %+v", delivery)
		}
		if delivery.ContentType != requestContentType || delivery.Type != requestType || delivery.DeliveryMode != amqp.Persistent {
			t.Fatalf("command transport metadata = type %q content %q mode %d", delivery.Type, delivery.ContentType, delivery.DeliveryMode)
		}
		if delivery.Headers["request_id"] != requestID || delivery.Headers["idempotency_key"] != idempotencyKey {
			t.Fatalf("command headers = %#v", delivery.Headers)
		}
		if err := delivery.Ack(false); err != nil {
			t.Fatalf("ack audit command: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("broker did not route the metadata audit copy; publisher confirm/route contract failed")
	}
}

func assertUnavailableReplyTargetIsAcknowledged(t *testing.T, ctx context.Context, config Config, executor *integrationExecutor) {
	t.Helper()
	connection, err := amqp.Dial(config.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	channel, err := connection.Channel()
	if err != nil {
		t.Fatal(err)
	}
	defer channel.Close()
	body, err := proto.Marshal(&tradesettlementv1.ExecuteSettlementBatchRequest{RequestId: "lost-reply", IdempotencyKey: "lost-reply-key"})
	if err != nil {
		t.Fatal(err)
	}
	before := executor.calls.Load()
	if err := channel.PublishWithContext(ctx, config.Exchange, config.RoutingKey, true, false, amqp.Publishing{
		CorrelationId: "lost-reply", MessageId: "lost-reply", ReplyTo: "queue-that-does-not-exist", Body: body,
	}); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		queue, err := channel.QueueInspect(config.CommandQueue)
		if err == nil && queue.Messages == 0 && executor.calls.Load() == before+1 {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("unroutable reply command was not acknowledged: queue=%+v calls=%d err=%v", queue, executor.calls.Load(), err)
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func waitForRabbitMQTestBroker(t *testing.T, url string) {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	for {
		connection, err := amqp.Dial(url)
		if err == nil {
			_ = connection.Close()
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("RabbitMQ did not become ready: %v", err)
		}
		time.Sleep(250 * time.Millisecond)
	}
}

func assertMalformedCommandGetsErrorReplyAndIsDeadLettered(t *testing.T, ctx context.Context, config Config) {
	t.Helper()
	connection, err := amqp.Dial(config.URL)
	if err != nil {
		t.Fatalf("connect for malformed command: %v", err)
	}
	defer connection.Close()
	channel, err := connection.Channel()
	if err != nil {
		t.Fatal(err)
	}
	defer channel.Close()
	reply, err := channel.QueueDeclare("", false, true, true, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	deliveries, err := channel.Consume(reply.Name, "", true, true, false, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	correlation := fmt.Sprintf("malformed-%d", time.Now().UnixNano())
	if err := channel.PublishWithContext(ctx, config.Exchange, config.RoutingKey, true, false, amqp.Publishing{
		CorrelationId: correlation,
		MessageId:     correlation,
		ReplyTo:       reply.Name,
		Body:          []byte("not-protobuf"),
	}); err != nil {
		t.Fatalf("publish malformed command: %v", err)
	}
	select {
	case delivery := <-deliveries:
		if delivery.CorrelationId != correlation {
			t.Fatalf("reply correlation = %q", delivery.CorrelationId)
		}
		_, err := decodeReply(delivery.Body)
		if connect.CodeOf(err) != connect.CodeInvalidArgument {
			t.Fatalf("malformed command reply = %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("malformed command did not receive an error reply")
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		delivery, ok, err := channel.Get(config.DeadLetterQueue, true)
		if err != nil {
			t.Fatalf("inspect malformed-command dead letter: %v", err)
		}
		if ok {
			if delivery.CorrelationId != correlation || string(delivery.Body) != "not-protobuf" {
				t.Fatalf("dead letter = correlation %q body %q", delivery.CorrelationId, delivery.Body)
			}
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("malformed command was acknowledged instead of dead-lettered")
		}
		time.Sleep(50 * time.Millisecond)
	}
}
