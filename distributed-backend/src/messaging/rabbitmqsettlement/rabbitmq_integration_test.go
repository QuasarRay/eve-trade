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
	calls atomic.Int32
}

func (e *integrationExecutor) Ping(context.Context) error { return nil }

func (e *integrationExecutor) ExecuteSettlementBatch(_ context.Context, request *tradesettlementv1.ExecuteSettlementBatchRequest) (*tradesettlementv1.ExecuteSettlementBatchResponse, error) {
	e.calls.Add(1)
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

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	executor := &integrationExecutor{}
	ready := make(chan bool, 2)
	workerDone := make(chan error, 1)
	go func() {
		workerDone <- RunSettlementWorker(ctx, config, executor, func(value bool) { ready <- value })
	}()
	select {
	case value := <-ready:
		if !value {
			t.Fatal("worker reported not ready during startup")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("worker did not become ready")
	}

	client, err := NewRPCClient(ctx, config)
	if err != nil {
		t.Fatalf("create RPC client: %v", err)
	}
	defer client.Close()

	response, err := client.ExecuteSettlementBatch(ctx, &tradesettlementv1.ExecuteSettlementBatchRequest{
		RequestId: "success", IdempotencyKey: "success-key",
	})
	if err != nil {
		t.Fatalf("successful brokered call failed: %v", err)
	}
	if response.GetSettlementBatchId() != "batch-success" || response.GetBatchState() != "COMPLETED" {
		t.Fatalf("unexpected brokered response: %v", response)
	}

	_, err = client.ExecuteSettlementBatch(ctx, &tradesettlementv1.ExecuteSettlementBatchRequest{
		RequestId: "reject", IdempotencyKey: "reject-key",
	})
	if connect.CodeOf(err) != connect.CodeFailedPrecondition || err.Error() == "" {
		t.Fatalf("brokered business rejection = %v", err)
	}
	if executor.calls.Load() != 2 {
		t.Fatalf("executor calls = %d, want 2", executor.calls.Load())
	}

	assertMalformedCommandGetsErrorReply(t, ctx, config)
	assertUnavailableReplyTargetIsAcknowledged(t, ctx, config, executor)

	cancel()
	select {
	case err := <-workerDone:
		if err != nil {
			t.Fatalf("worker shutdown returned error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("worker did not drain and stop after cancellation")
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

func assertMalformedCommandGetsErrorReply(t *testing.T, ctx context.Context, config Config) {
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
}
