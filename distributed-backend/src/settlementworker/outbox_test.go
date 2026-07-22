package settlementworker

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"encore.dev/pubsub"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
)

type recordingOutboxStore struct {
	deliveries  []*tradesettlementv1.SettlementOutboxDelivery
	claimCalls  int
	complete    []*tradesettlementv1.CompleteSettlementOutboxRequest
	release     []*tradesettlementv1.ReleaseSettlementOutboxRequest
	completeErr error
}

func (store *recordingOutboxStore) ClaimSettlementOutbox(context.Context, *tradesettlementv1.ClaimSettlementOutboxRequest) (*tradesettlementv1.ClaimSettlementOutboxResponse, error) {
	store.claimCalls++
	if store.claimCalls > 1 {
		return &tradesettlementv1.ClaimSettlementOutboxResponse{}, nil
	}
	return &tradesettlementv1.ClaimSettlementOutboxResponse{Deliveries: store.deliveries}, nil
}

func (store *recordingOutboxStore) CompleteSettlementOutbox(_ context.Context, request *tradesettlementv1.CompleteSettlementOutboxRequest) (*tradesettlementv1.CompleteSettlementOutboxResponse, error) {
	store.complete = append(store.complete, request)
	if store.completeErr != nil {
		return nil, store.completeErr
	}
	return &tradesettlementv1.CompleteSettlementOutboxResponse{}, nil
}

func (store *recordingOutboxStore) ReleaseSettlementOutbox(_ context.Context, request *tradesettlementv1.ReleaseSettlementOutboxRequest) (*tradesettlementv1.ReleaseSettlementOutboxResponse, error) {
	store.release = append(store.release, request)
	return &tradesettlementv1.ReleaseSettlementOutboxResponse{}, nil
}

type recordingWorkPublisher struct {
	work []*settlement.Work
	err  error
}

func (publisher *recordingWorkPublisher) Publish(_ context.Context, work *settlement.Work) (string, error) {
	if publisher.err != nil {
		return "", publisher.err
	}
	publisher.work = append(publisher.work, work)
	return "broker-message", nil
}

func (*recordingWorkPublisher) Meta() pubsub.TopicMeta { return pubsub.TopicMeta{} }

func outboxDelivery(t *testing.T) *tradesettlementv1.SettlementOutboxDelivery {
	t.Helper()
	work := validSettlementWork()
	payload, err := json.Marshal(work)
	if err != nil {
		t.Fatalf("marshal settlement work: %v", err)
	}
	return &tradesettlementv1.SettlementOutboxDelivery{
		OperationId:     work.OperationID,
		WorkPayloadJson: payload,
		AttemptCount:    1,
		LeaseGeneration: 7,
	}
}

func TestDispatchSettlementOutboxPublishesAndCompletesLease(t *testing.T) {
	delivery := outboxDelivery(t)
	store := &recordingOutboxStore{deliveries: []*tradesettlementv1.SettlementOutboxDelivery{delivery}}
	publisher := new(recordingWorkPublisher)
	service := &Service{outbox: store, work: publisher}

	response, err := service.DispatchSettlementOutbox(context.Background())
	if err != nil {
		t.Fatalf("dispatch outbox: %v", err)
	}
	if response.Claimed != 1 || response.Delivered != 1 || response.Released != 0 {
		t.Fatalf("unexpected dispatch response: %+v", response)
	}
	if len(publisher.work) != 1 || publisher.work[0].OperationID != delivery.OperationId {
		t.Fatalf("published work = %+v", publisher.work)
	}
	if len(store.complete) != 1 || store.complete[0].LeaseGeneration != delivery.LeaseGeneration || store.complete[0].MessageId != "broker-message" {
		t.Fatalf("completion request = %+v", store.complete)
	}
}

func TestDispatchSettlementOutboxReleasesBrokerFailure(t *testing.T) {
	delivery := outboxDelivery(t)
	store := &recordingOutboxStore{deliveries: []*tradesettlementv1.SettlementOutboxDelivery{delivery}}
	service := &Service{
		outbox: store,
		work:   &recordingWorkPublisher{err: errors.New("broker unavailable")},
	}

	response, err := service.DispatchSettlementOutbox(context.Background())
	if err == nil {
		t.Fatal("broker failure was swallowed")
	}
	if response.Released != 1 || len(store.release) != 1 || len(store.complete) != 0 {
		t.Fatalf("failed delivery state: response=%+v releases=%+v completions=%+v", response, store.release, store.complete)
	}
}

func TestDispatchSettlementOutboxDoesNotReleaseAfterSuccessfulPublish(t *testing.T) {
	delivery := outboxDelivery(t)
	store := &recordingOutboxStore{
		deliveries:  []*tradesettlementv1.SettlementOutboxDelivery{delivery},
		completeErr: errors.New("completion response lost"),
	}
	service := &Service{outbox: store, work: new(recordingWorkPublisher)}

	response, err := service.DispatchSettlementOutbox(context.Background())
	if err == nil {
		t.Fatal("durable completion failure was swallowed")
	}
	if response.Released != 0 || len(store.release) != 0 {
		t.Fatalf("published delivery was released for immediate duplicate: response=%+v releases=%+v", response, store.release)
	}
}

func TestOutboxDispatchLoopRunsWithoutEncoreCron(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	ticks := make(chan time.Time)
	calls := make(chan struct{}, 2)
	done := make(chan struct{})
	go func() {
		defer close(done)
		runOutboxDispatchLoop(ctx, ticks, func(context.Context) {
			calls <- struct{}{}
		})
	}()

	select {
	case <-calls:
	case <-time.After(time.Second):
		t.Fatal("outbox dispatcher did not run immediately at startup")
	}
	ticks <- time.Now()
	select {
	case <-calls:
	case <-time.After(time.Second):
		t.Fatal("outbox dispatcher did not run after a scheduled tick")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("outbox dispatcher did not stop after cancellation")
	}
}
