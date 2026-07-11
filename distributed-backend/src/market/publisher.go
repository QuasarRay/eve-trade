package market

import (
	"context"
	"fmt"
	"time"

	"encore.dev/pubsub"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/settlementrpc"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
)

type SettlementPublication struct {
	MessageID   string
	OperationID string
	QueuedAt    time.Time
}

type SettlementPublisher interface {
	PublishSettlementWork(ctx context.Context, work *settlement.Work) (*SettlementPublication, error)
}

type OperationStatusReader interface {
	GetSettlementOperation(ctx context.Context, operationID string) (*tradesettlementv1.SettlementOperationStatus, error)
}

type PubSubSettlementPublisher struct {
	topic     pubsub.Publisher[*settlement.Work]
	lifecycle *settlementrpc.Client
	timeout   time.Duration
}

func NewSettlementPublisher(target string, timeout time.Duration) (PubSubSettlementPublisher, error) {
	client, err := settlementrpc.New(target)
	if err != nil {
		return PubSubSettlementPublisher{}, fmt.Errorf("create settlement lifecycle client: %w", err)
	}
	return PubSubSettlementPublisher{
		topic:     pubsub.TopicRef[pubsub.Publisher[*settlement.Work]](settlement.WorkTopic),
		lifecycle: client,
		timeout:   timeout,
	}, nil
}

func (p PubSubSettlementPublisher) PublishSettlementWork(ctx context.Context, work *settlement.Work) (*SettlementPublication, error) {
	if work == nil {
		return nil, fmt.Errorf("settlement work is required")
	}
	callCtx, cancel := p.callContext(ctx)
	defer cancel()
	queued, err := p.lifecycle.QueueSettlementOperation(callCtx, &tradesettlementv1.QueueSettlementOperationRequest{
		IdempotencyKey:      work.IdempotencyKey,
		RequestFingerprint:  work.RequestFingerprint,
		Intent:              settlementIntent(work.Intent),
		CausedByCapsuleerId: work.CausedByCapsuleerID,
		ExternalRequestId:   work.ExternalRequestID,
	})
	if err != nil {
		return nil, fmt.Errorf("queue durable settlement operation: %w", err)
	}
	operation := queued.GetOperation()
	if operation == nil || operation.GetOperationId() == "" || operation.GetQueuedAt() == nil {
		return nil, fmt.Errorf("queue durable settlement operation returned incomplete status")
	}
	work.OperationID = operation.GetOperationId()
	work.QueuedAt = operation.GetQueuedAt().AsTime().UTC()
	work.RequestID = work.OperationID
	messageID, err := p.topic.Publish(ctx, work)
	if err != nil {
		return nil, err
	}
	return &SettlementPublication{
		MessageID:   messageID,
		OperationID: work.OperationID,
		QueuedAt:    work.QueuedAt,
	}, nil
}

func (p PubSubSettlementPublisher) GetSettlementOperation(ctx context.Context, operationID string) (*tradesettlementv1.SettlementOperationStatus, error) {
	callCtx, cancel := p.callContext(ctx)
	defer cancel()
	response, err := p.lifecycle.GetSettlementOperation(callCtx, &tradesettlementv1.GetSettlementOperationRequest{OperationId: operationID})
	if err != nil {
		return nil, err
	}
	if response.GetOperation() == nil {
		return nil, fmt.Errorf("settlement lifecycle returned no operation")
	}
	return response.GetOperation(), nil
}

func (p PubSubSettlementPublisher) callContext(parent context.Context) (context.Context, context.CancelFunc) {
	if p.timeout <= 0 {
		return context.WithCancel(parent)
	}
	return context.WithTimeout(parent, p.timeout)
}

func settlementIntent(intent string) tradesettlementv1.SettlementIntent {
	switch intent {
	case settlement.IntentIssue:
		return tradesettlementv1.SettlementIntent_SETTLEMENT_INTENT_ISSUE
	case settlement.IntentAccept:
		return tradesettlementv1.SettlementIntent_SETTLEMENT_INTENT_ACCEPT
	case settlement.IntentCancel:
		return tradesettlementv1.SettlementIntent_SETTLEMENT_INTENT_CANCEL
	default:
		return tradesettlementv1.SettlementIntent_SETTLEMENT_INTENT_UNSPECIFIED
	}
}
