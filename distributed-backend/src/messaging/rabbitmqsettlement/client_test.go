package rabbitmqsettlement

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"connectrpc.com/connect"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
	amqp "github.com/rabbitmq/amqp091-go"
	"google.golang.org/protobuf/proto"
)

func TestDecodeReplySuccessAndFailureContracts(t *testing.T) {
	want := &tradesettlementv1.ExecuteSettlementBatchResponse{SettlementBatchId: "batch-1", BatchState: "COMPLETED"}
	protobuf, err := proto.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	success, _ := json.Marshal(settlementReply{Success: true, Response: protobuf})
	got, err := decodeReply(success)
	if err != nil {
		t.Fatal(err)
	}
	if !proto.Equal(got, want) {
		t.Fatalf("decoded response = %v, want %v", got, want)
	}

	failure, _ := json.Marshal(settlementReply{Success: false, Code: "failed_precondition", Error: "insufficient funds"})
	_, err = decodeReply(failure)
	if connect.CodeOf(err) != connect.CodeFailedPrecondition || err.Error() == "" {
		t.Fatalf("coded failure = %v", err)
	}

	uncoded, _ := json.Marshal(settlementReply{Success: false, Error: "plain failure"})
	if _, err = decodeReply(uncoded); err == nil || err.Error() != "plain failure" {
		t.Fatalf("uncoded failure = %v", err)
	}
	if _, err = decodeReply([]byte("not-json")); err == nil {
		t.Fatal("malformed JSON reply was accepted")
	}
	badProto, _ := json.Marshal(settlementReply{Success: true, Response: []byte("not-protobuf")})
	if _, err = decodeReply(badProto); err == nil {
		t.Fatal("malformed protobuf response was accepted")
	}
}

func TestPendingReplyDispatchAndFailureAreExact(t *testing.T) {
	client := &RPCClient{pending: make(map[string]chan pendingResult)}
	first := make(chan pendingResult, 1)
	second := make(chan pendingResult, 1)
	client.pending["first"] = first
	client.pending["second"] = second

	client.dispatchReply(amqp.Delivery{CorrelationId: "first", Body: []byte("reply")})
	result := <-first
	if string(result.delivery.Body) != "reply" {
		t.Fatalf("reply body = %q", result.delivery.Body)
	}
	if _, exists := client.pending["first"]; exists {
		t.Fatal("delivered correlation remained pending")
	}

	client.dispatchReply(amqp.Delivery{})
	client.dispatchReply(amqp.Delivery{CorrelationId: "unknown"})
	client.failPending(errors.New("connection lost"))
	if result := <-second; result.err == nil || result.err.Error() != "connection lost" {
		t.Fatalf("pending failure = %v", result.err)
	}
	if len(client.pending) != 0 {
		t.Fatalf("pending calls remain: %d", len(client.pending))
	}
}

func TestRequestMetadataHelpersUseStableValues(t *testing.T) {
	if got := expirationMilliseconds(1500 * time.Millisecond); got != "1500" {
		t.Fatalf("expiration = %q", got)
	}
	if got := expirationMilliseconds(0); got != "10000" {
		t.Fatalf("default expiration = %q", got)
	}
	request := &tradesettlementv1.ExecuteSettlementBatchRequest{
		IdempotencyKey: "key", RequestId: "request", ExternalRequestId: "external", CreatedByService: "market",
	}
	headers := requestHeaders(request)
	if len(headers) != 4 || headers["idempotency_key"] != "key" || headers["request_id"] != "request" || headers["external_request_id"] != "external" || headers["created_by_service"] != "market" {
		t.Fatalf("headers = %#v", headers)
	}
	if len(requestHeaders(&tradesettlementv1.ExecuteSettlementBatchRequest{})) != 0 {
		t.Fatal("empty request emitted headers")
	}
	first, err := randomID()
	if err != nil || len(first) != 32 {
		t.Fatalf("random ID = %q, err=%v", first, err)
	}
	second, _ := randomID()
	if first == second {
		t.Fatal("random IDs collided")
	}
}

func TestWaitForPublishConfirmationHandlesAckNackClosureAndCancellation(t *testing.T) {
	ack := make(chan amqp.Confirmation, 1)
	ack <- amqp.Confirmation{Ack: true}
	if err := waitForPublishConfirmation(context.Background(), ack); err != nil {
		t.Fatalf("ACK failed: %v", err)
	}

	nack := make(chan amqp.Confirmation, 1)
	nack <- amqp.Confirmation{Ack: false}
	if err := waitForPublishConfirmation(context.Background(), nack); !errors.Is(err, errPublishNotConfirmed) {
		t.Fatalf("NACK error = %v", err)
	}

	closed := make(chan amqp.Confirmation)
	close(closed)
	if err := waitForPublishConfirmation(context.Background(), closed); err == nil {
		t.Fatal("closed confirmation channel succeeded")
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := waitForPublishConfirmation(ctx, make(chan amqp.Confirmation)); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancel error = %v", err)
	}
}

func TestSamePublishedMessageRequiresEveryAvailableIdentifier(t *testing.T) {
	message := amqp.Publishing{CorrelationId: "correlation", MessageId: "message"}
	if !samePublishedMessage(amqp.Return{CorrelationId: "correlation", MessageId: "message"}, message) {
		t.Fatal("matching return rejected")
	}
	if samePublishedMessage(amqp.Return{CorrelationId: "other", MessageId: "message"}, message) {
		t.Fatal("wrong correlation accepted")
	}
	if samePublishedMessage(amqp.Return{CorrelationId: "correlation", MessageId: "other"}, message) {
		t.Fatal("wrong message ID accepted")
	}
	if !samePublishedMessage(amqp.Return{}, amqp.Publishing{}) {
		t.Fatal("identifier-free message did not match")
	}
}
